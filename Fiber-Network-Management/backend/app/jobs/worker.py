"""Background worker + scheduler.

Run as a separate process:  ``python -m app.jobs.worker``
or embedded in the API process with ``FNMS_EMBEDDED_WORKER=true`` (single-process demo mode).
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time

from sqlalchemy import or_, select

from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.jobs.queue import enqueue, reap_stale, run_next
from app.models import Olt

log = logging.getLogger("fnms.worker")


def load_handlers() -> None:
    # Importing registers @handler functions.
    import app.engines.monitoring  # noqa: F401
    import app.engines.operations  # noqa: F401
    import app.engines.provisioning  # noqa: F401


def schedule_due_polls() -> int:
    """Enqueue a poll for every enabled OLT whose next poll time has passed."""
    db = SessionLocal()
    try:
        now = utcnow()
        due = db.scalars(select(Olt).where(Olt.monitoring_enabled.is_(True), Olt.admin_status == "enabled",
                                           or_(Olt.next_poll_at.is_(None), Olt.next_poll_at <= now)))
        n = 0
        for olt in due:
            enqueue(db, "olt.poll", {"olt_id": olt.id}, created_by="scheduler", dedupe_key=f"poll:{olt.id}", max_attempts=1)
            from datetime import timedelta

            olt.next_poll_at = now + timedelta(seconds=olt.poll_interval_seconds)
            n += 1
        db.commit()
        return n
    finally:
        db.close()


def run_worker(stop: threading.Event | None = None, *, with_scheduler: bool = True) -> None:
    load_handlers()
    s = get_settings()
    stop = stop or threading.Event()
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"
    log.info("worker %s started (scheduler=%s)", worker_id, with_scheduler)
    last_tick = 0.0
    db = SessionLocal()
    try:
        reap_stale(db)
    finally:
        db.close()
    while not stop.is_set():
        try:
            if with_scheduler and time.monotonic() - last_tick >= s.scheduler_tick_seconds:
                last_tick = time.monotonic()
                n = schedule_due_polls()
                if n:
                    log.info("scheduled %d OLT poll(s)", n)
            job = run_next(worker_id)
            if job is None:
                stop.wait(s.worker_poll_seconds)
            else:
                log.info("job %s %s -> %s", job.job_type, job.id[:8], job.status)
        except Exception:  # noqa: BLE001 - keep the worker alive
            log.exception("worker loop error")
            stop.wait(5)


def start_embedded() -> threading.Event:
    stop = threading.Event()
    t = threading.Thread(target=run_worker, args=(stop,), name="fnms-worker", daemon=True)
    t.start()
    return stop


def main() -> None:  # pragma: no cover - process entrypoint
    from app.logging_setup import setup_logging

    setup_logging()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    run_worker(stop)


if __name__ == "__main__":  # pragma: no cover
    main()
