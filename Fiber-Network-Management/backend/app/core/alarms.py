"""Alarm lifecycle: raise / repeat / clear / acknowledge / investigate / resolve / reopen.

Alarms are never deleted. When a condition disappears from the equipment the alarm is
resolved by the system with ``cleared_on_device_at`` set, preserving its history.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import OPEN_ALARM_STATES, AlarmState
from app.core.errors import ValidationFailed
from app.db import utcnow
from app.models import Alarm, AlarmEvent

REOPEN_WINDOW = timedelta(hours=1)


def open_alarm(db: Session, key: str) -> Alarm | None:
    return db.scalars(select(Alarm).where(Alarm.alarm_key == key, Alarm.state.in_(list(OPEN_ALARM_STATES)))
                      .order_by(Alarm.id.desc())).first()


def raise_alarm(db: Session, *, key: str, origin: str, source_type: str, source_id: int | None, source_label: str,
                category: str, severity: str, description: str, olt_id: int | None = None, site_id: int | None = None,
                vendor_alarm_id: str | None = None, vendor_severity: str | None = None, vendor_raw: dict | None = None,
                occurred_at=None, is_simulated: bool = False) -> tuple[Alarm, str]:
    """Upsert an active alarm. Returns (alarm, action) where action is raised|repeated|reopened."""
    now = utcnow()
    occurred = occurred_at or now
    alarm = open_alarm(db, key)
    if alarm:
        alarm.last_occurred_at = now
        alarm.occurrence_count += 1
        if alarm.severity != severity:
            db.add(AlarmEvent(alarm=alarm, action="severity_changed", note=f"{alarm.severity} -> {severity}"))
            alarm.severity = severity
        alarm.description = description
        return alarm, "repeated"
    recent = db.scalars(select(Alarm).where(Alarm.alarm_key == key, Alarm.state == AlarmState.RESOLVED,
                                            Alarm.resolved_at >= now - REOPEN_WINDOW).order_by(Alarm.id.desc())).first()
    if recent:
        recent.state = AlarmState.REOPENED
        recent.last_occurred_at = now
        recent.occurrence_count += 1
        recent.resolved_at = None
        recent.resolved_by = None
        recent.cleared_on_device_at = None
        recent.acknowledged = False
        recent.severity = severity
        recent.description = description
        db.add(AlarmEvent(alarm=recent, action="reopened", note="Condition re-occurred within the reopen window"))
        return recent, "reopened"
    alarm = Alarm(alarm_key=key, origin=origin, source_type=source_type, source_id=source_id, source_label=source_label,
                  category=category, severity=severity, description=description, olt_id=olt_id, site_id=site_id,
                  vendor_alarm_id=vendor_alarm_id, vendor_severity=vendor_severity, vendor_raw=vendor_raw,
                  first_occurred_at=occurred, last_occurred_at=now, is_simulated=is_simulated)
    db.add(alarm)
    db.add(AlarmEvent(alarm=alarm, action="raised", note=description))
    return alarm, "raised"


def clear_alarm(db: Session, key: str, note: str = "Condition cleared on device") -> Alarm | None:
    alarm = open_alarm(db, key)
    if alarm is None:
        return None
    now = utcnow()
    alarm.cleared_on_device_at = now
    alarm.state = AlarmState.RESOLVED
    alarm.resolved_at = now
    alarm.resolved_by = "system"
    alarm.resolution_note = note
    db.add(AlarmEvent(alarm=alarm, action="cleared", note=note))
    return alarm


TRANSITIONS = {
    "acknowledge": ({AlarmState.NEW, AlarmState.REOPENED}, AlarmState.ACKNOWLEDGED),
    "investigate": ({AlarmState.NEW, AlarmState.REOPENED, AlarmState.ACKNOWLEDGED}, AlarmState.INVESTIGATING),
    "resolve": (set(OPEN_ALARM_STATES), AlarmState.RESOLVED),
    "reopen": ({AlarmState.RESOLVED}, AlarmState.REOPENED),
}


def transition(db: Session, alarm: Alarm, action: str, actor: str, note: str = "") -> Alarm:
    if action == "note":
        if not note.strip():
            raise ValidationFailed("Note text is required")
        db.add(AlarmEvent(alarm=alarm, action="note", actor=actor, note=note))
        return alarm
    if action not in TRANSITIONS:
        raise ValidationFailed(f"Unknown alarm action '{action}'")
    allowed, target = TRANSITIONS[action]
    if alarm.state not in allowed:
        raise ValidationFailed(f"Cannot {action} an alarm in state '{alarm.state}'")
    now = utcnow()
    if action in ("acknowledge", "investigate") and not alarm.acknowledged:
        alarm.acknowledged = True
        alarm.acknowledged_by = actor
        alarm.acknowledged_at = now
    if action == "resolve":
        alarm.resolved_at = now
        alarm.resolved_by = actor
        alarm.resolution_note = note or None
    if action == "reopen":
        alarm.resolved_at = None
        alarm.resolved_by = None
        alarm.acknowledged = False
    alarm.state = target
    db.add(AlarmEvent(alarm=alarm, action=action if action != "resolve" else "resolved", actor=actor, note=note))
    return alarm
