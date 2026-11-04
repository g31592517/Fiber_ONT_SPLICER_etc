"""Audit logging. Details are always redacted before being persisted."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog
from app.security.crypto import redact

log = logging.getLogger("fnms.audit")


class Actor:
    """Who performed an action: a user, or the system (worker/scheduler)."""

    def __init__(self, user_id: int | None = None, username: str = "system", ip: str | None = None,
                 permissions: set[str] | None = None):
        self.user_id = user_id
        self.username = username
        self.ip = ip
        self.permissions = permissions or set()

    def has(self, perm: str) -> bool:
        return perm in self.permissions

    @classmethod
    def system(cls, name: str = "system") -> "Actor":
        from app.security.permissions import PERMISSIONS

        return cls(None, name, None, set(PERMISSIONS))


def audit(db: Session, actor: Actor | None, action: str, entity_type: str | None = None,
          entity_id: Any = None, summary: str = "", details: dict | None = None, outcome: str = "success") -> AuditLog:
    actor = actor or Actor.system()
    entry = AuditLog(
        user_id=actor.user_id,
        username=actor.username,
        action=action,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        summary=summary,
        details=redact(details) if details else None,
        outcome=outcome,
        ip_address=actor.ip,
    )
    db.add(entry)
    log.info("audit action=%s entity=%s:%s user=%s outcome=%s", action, entity_type, entity_id, actor.username, outcome)
    return entry
