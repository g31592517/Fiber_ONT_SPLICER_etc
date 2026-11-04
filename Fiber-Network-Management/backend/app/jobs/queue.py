"""Database-backed job queue.

Chosen over Redis/Celery to keep the product independently installable with a single
database. Claiming uses an atomic conditional UPDATE, which is safe for multiple workers on
PostgreSQL and for a single worker on SQLite.
"""
from __future__ import annotations

import logging
import traceback
import uuid
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.adapters.base import AdapterConnectionError, AdapterError, UnsupportedOperation
from app.config import get_settings
from app.core.enums import JobStatus
from app.db import SessionLocal, utcnow
from app.models import Job
from app.security.crypto import redact

log = logging.getLogger("fnms.jobs")

Handler = Callable[[Session, dict, Job], dict | None]
HANDLERS: dict[str, Handler] = {}


def handler(job_type: str):
    def deco(fn: Handler) -> Handler:
        HANDLERS[job_type] = fn
        return fn

    return deco


def enqueue(db: Session, job_type: str, payload: dict[str, Any], *, created_by: str = "system",
            dedupe_key: str | None = None, max_attempts: int = 1, delay_seconds: float = 0) -> Job:
    if dedupe_key:
        existing = db.scalars(select(Job).where(Job.dedupe_key == dedupe_key,
                                                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))).first()
        if existing:
            return existing
    job = Job(id=str(uuid.uuid4()), job_type=job_type, payload=payload, created_by=created_by, dedupe_key=dedupe_key,
              max_attempts=max_attempts, run_after=utcnow() + timedelta(seconds=delay_seconds))
    db.add(job)
    db.flush()
    return job


def _claim(db: Session, worker_id: str) -> Job | None:
    now = utcnow()
    candidates = db.scalars(select(Job.id).where(Job.status == JobStatus.QUEUED, Job.run_after <= now)
                            .order_by(Job.created_at).limit(5)).all()
    for jid in candidates:
        res = db.execute(update(Job).where(Job.id == jid, Job.status == JobStatus.QUEUED)
                         .values(status=JobStatus.RUNNING, locked_by=worker_id, locked_at=now, started_at=now,
                                 attempts=Job.attempts + 1))
        db.commit()
        if res.rowcount == 1:
            return db.get(Job, jid)
    return None


def reap_stale(db: Session) -> int:
    """Jobs left 'running' by a crashed worker are failed (never blindly re-run)."""
    cutoff = utcnow() - timedelta(seconds=get_settings().job_lock_timeout_seconds)
    stale = list(db.scalars(select(Job).where(Job.status == JobStatus.RUNNING, Job.locked_at < cutoff)))
    for j in stale:
        j.status = JobStatus.FAILED
        j.error = "Worker stopped while job was running (lock timeout). Not retried automatically."
        j.finished_at = utcnow()
    db.commit()
    return len(stale)


def execute(job_id: str) -> Job:
    """Run one claimed job in a fresh session."""
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        fn = HANDLERS.get(job.job_type)
        try:
            if fn is None:
                raise RuntimeError(f"No handler registered for job type '{job.job_type}'")
            result = fn(db, job.payload, job)
            db.commit()
            job = db.get(Job, job_id)
            job.status = JobStatus.SUCCEEDED
            job.result = redact(result) if result else None
        except UnsupportedOperation as exc:
            db.rollback()
            job = db.get(Job, job_id)
            job.status = JobStatus.FAILED
            job.result = exc.as_result()
            job.error = exc.reason
        except Exception as exc:  # noqa: BLE001 - job boundary
            db.rollback()
            job = db.get(Job, job_id)
            retryable = isinstance(exc, AdapterConnectionError) or (isinstance(exc, AdapterError) and exc.retryable)
            job.error = f"{exc.__class__.__name__}: {exc}"
            if retryable and job.attempts < job.max_attempts:
                job.status = JobStatus.QUEUED
                job.run_after = utcnow() + timedelta(seconds=min(300, 15 * 2 ** job.attempts))
                log.warning("job %s (%s) failed, will retry: %s", job.id, job.job_type, exc)
            else:
                job.status = JobStatus.FAILED
                log.error("job %s (%s) failed: %s", job.id, job.job_type, exc)
                if not isinstance(exc, (AdapterError,)):
                    log.debug(traceback.format_exc())
        job.finished_at = utcnow()
        job.locked_by = None
        db.commit()
        return job
    finally:
        db.close()


def run_next(worker_id: str = "inline") -> Job | None:
    db = SessionLocal()
    try:
        job = _claim(db, worker_id)
    finally:
        db.close()
    return execute(job.id) if job else None


def run_pending(max_jobs: int = 200, worker_id: str = "inline") -> list[Job]:
    """Process queued jobs until the queue is empty (used by tests, the seed command and the CLI)."""
    done = []
    for _ in range(max_jobs):
        job = run_next(worker_id)
        if job is None:
            break
        done.append(job)
    return done
