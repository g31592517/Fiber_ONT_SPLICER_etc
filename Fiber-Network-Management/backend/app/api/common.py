"""Shared API helpers: serialization (never exposes encrypted columns), pagination, job responses."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import Query
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.models import Job


def ser(obj, exclude: set[str] | None = None, **extra) -> dict[str, Any]:
    """Serialize a model's columns. Columns ending in ``_enc`` (encrypted secrets) are replaced by
    ``has_<name>`` booleans and never returned."""
    if obj is None:
        return None  # type: ignore[return-value]
    out: dict[str, Any] = {}
    for col in inspect(obj).mapper.column_attrs:
        k = col.key
        if exclude and k in exclude:
            continue
        v = getattr(obj, k)
        if k.endswith("_enc"):
            out[f"has_{k[:-4]}"] = bool(v)
            continue
        if isinstance(v, (datetime, date)):
            v = v.isoformat()
        out[k] = v
    out.update(extra)
    return out


class Page:
    def __init__(self, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        self.limit = limit
        self.offset = offset


def paginate(db: Session, stmt, page: Page, mapper=ser) -> dict:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    items = db.scalars(stmt.limit(page.limit).offset(page.offset)).all()
    return {"total": total, "limit": page.limit, "offset": page.offset, "items": [mapper(i) for i in items]}


def job_ref(job: Job) -> dict:
    return {"job_id": job.id, "job_type": job.job_type, "status": job.status, "poll_url": f"/api/v1/jobs/{job.id}"}
