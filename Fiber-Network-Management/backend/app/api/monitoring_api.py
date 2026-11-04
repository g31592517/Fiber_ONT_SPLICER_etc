"""Dashboard, alarms, incidents, thresholds and optical monitoring."""
from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.admin import get_setting
from app.api.common import Page, paginate, ser
from app.core import alarms as alarm_svc
from app.core import impact as impact_svc
from app.core import topology
from app.core.audit import Actor, audit
from app.core.enums import OPEN_ALARM_STATES, IncidentStatus
from app.core.errors import ValidationFailed, get_or_404
from app.db import get_db, utcnow
from app.models import Alarm, Fat, Incident, Olt, OltPort, Ont, OntDiscovery, OpticalReading, OpticalThreshold
from app.security.auth import require

router = APIRouter(tags=["monitoring"])
MREAD = Depends(require("monitoring.read"))


@router.get("/dashboard")
def dashboard(site_id: int | None = None, olt_id: int | None = None, db: Session = Depends(get_db), _=MREAD):
    olt_q = select(Olt)
    if site_id:
        olt_q = olt_q.where(Olt.site_id == site_id)
    if olt_id:
        olt_q = olt_q.where(Olt.id == olt_id)
    olts = list(db.scalars(olt_q))
    olt_ids = [o.id for o in olts]
    ont_status = dict(db.execute(select(Ont.status, func.count(Ont.id)).where(Ont.olt_id.in_(olt_ids),
                                 Ont.registration_status == "registered").group_by(Ont.status)).all()) if olt_ids else {}
    optical = dict(db.execute(select(Ont.optical_status, func.count(Ont.id)).where(Ont.olt_id.in_(olt_ids),
                              Ont.registration_status == "registered").group_by(Ont.optical_status)).all()) if olt_ids else {}
    fats = list(db.scalars(select(Fat).where(Fat.site_id == site_id) if site_id else select(Fat)))
    caps = [topology.capacity_summary(db, "fat", f.id) for f in fats]
    total_ports = sum(c["total"] for c in caps)
    warn = float(get_setting(db, "monitoring.fat_capacity_warning_percent") or 80)
    alarm_q = select(Alarm.severity, func.count(Alarm.id)).where(Alarm.state.in_(list(OPEN_ALARM_STATES)))
    if site_id or olt_id:
        alarm_q = alarm_q.where(Alarm.olt_id.in_(olt_ids))
    alarms = dict(db.execute(alarm_q.group_by(Alarm.severity)).all())
    inc_q = select(Incident).where(Incident.status != IncidentStatus.RESOLVED).order_by(Incident.started_at.desc())
    if olt_id:
        inc_q = inc_q.where(Incident.olt_id == olt_id)
    incidents = list(db.scalars(inc_q))
    pon = list(db.scalars(select(OltPort).where(OltPort.port_type == "pon", OltPort.olt_id.in_(olt_ids)))) if olt_ids else []
    return {
        "generated_at": utcnow().isoformat(),
        "olts": {"total": len(olts), "online": sum(1 for o in olts if o.connection_status == "online"),
                 "degraded": sum(1 for o in olts if o.connection_status == "degraded"),
                 "unreachable": sum(1 for o in olts if o.connection_status == "unreachable"),
                 "unknown": sum(1 for o in olts if o.connection_status == "unknown"),
                 "simulated": sum(1 for o in olts if o.is_simulated)},
        "onts": {"registered": sum(ont_status.values()), "online": ont_status.get("online", 0),
                 "offline": ont_status.get("offline", 0), "los": ont_status.get("los", 0),
                 "dying_gasp": ont_status.get("dying_gasp", 0), "disabled": ont_status.get("disabled", 0),
                 "unreachable": ont_status.get("unreachable", 0), "unknown": ont_status.get("unknown", 0),
                 "registered_inactive": ont_status.get("registered_inactive", 0), "faulty": ont_status.get("faulty", 0),
                 "discovered_unregistered": db.scalar(select(func.count(OntDiscovery.id)).where(
                     OntDiscovery.state == "unregistered", *( [OntDiscovery.olt_id.in_(olt_ids)] if (site_id or olt_id) else []))),
                 "optical_warning": optical.get("warning", 0), "optical_critical": optical.get("critical", 0)},
        "pon_ports": {"total": len(pon), "up": sum(1 for p in pon if p.oper_status == "up"),
                      "fault": sum(1 for p in pon if p.oper_status in ("fault", "down"))},
        "fats": {"total": len(fats), "ports_total": total_ports, "ports_occupied": sum(c["occupied"] for c in caps),
                 "ports_available": sum(c["available"] for c in caps), "ports_reserved": sum(c["reserved"] for c in caps),
                 "ports_faulty": sum(c["faulty"] for c in caps),
                 "utilization_percent": round(100 * sum(c["occupied"] for c in caps) / total_ports, 1) if total_ports else 0,
                 "near_full": sum(1 for c in caps if c["utilization_percent"] >= warn)},
        "alarms": {"active": sum(alarms.values()), **{k: alarms.get(k, 0) for k in ("critical", "major", "minor", "warning", "info")}},
        "incidents": {"active": len(incidents), "items": [incident_brief(i) for i in incidents[:10]]},
        "olt_list": [{"id": o.id, "name": o.name, "status": o.connection_status, "simulated": o.is_simulated,
                      "last_contact_at": o.last_contact_at.isoformat() if o.last_contact_at else None} for o in olts],
    }


# ================================================================== alarms
@router.get("/alarms")
def list_alarms(page: Page = Depends(), state: str | None = "open", severity: str | None = None, category: str | None = None,
                olt_id: int | None = None, site_id: int | None = None, source_type: str | None = None, source_id: int | None = None,
                origin: str | None = None, q: str | None = Query(None, max_length=100), db: Session = Depends(get_db), _=MREAD):
    stmt = select(Alarm).order_by(Alarm.last_occurred_at.desc())
    if state == "open":
        stmt = stmt.where(Alarm.state.in_(list(OPEN_ALARM_STATES)))
    elif state and state != "all":
        stmt = stmt.where(Alarm.state.in_(state.split(",")))
    for col, val in ((Alarm.olt_id, olt_id), (Alarm.site_id, site_id), (Alarm.source_type, source_type),
                     (Alarm.source_id, source_id), (Alarm.origin, origin), (Alarm.category, category)):
        if val is not None:
            stmt = stmt.where(col == val)
    if severity:
        stmt = stmt.where(Alarm.severity.in_(severity.split(",")))
    if q:
        stmt = stmt.where(Alarm.description.ilike(f"%{q}%") | Alarm.source_label.ilike(f"%{q}%"))
    return paginate(db, stmt, page, mapper=lambda a: ser(a, exclude={"vendor_raw"}))


@router.get("/alarms/{alarm_id}")
def get_alarm(alarm_id: int, db: Session = Depends(get_db), _=MREAD):
    a = get_or_404(db, Alarm, alarm_id, "Alarm")
    return ser(a, events=[ser(e) for e in a.events], olt_name=db.get(Olt, a.olt_id).name if a.olt_id else None)


class AlarmActionIn(BaseModel):
    action: Literal["acknowledge", "investigate", "resolve", "reopen", "note"]
    note: str = Field("", max_length=2000)


@router.post("/alarms/{alarm_id}/actions")
def alarm_action(alarm_id: int, body: AlarmActionIn, actor: Actor = Depends(require("alarm.manage")), db: Session = Depends(get_db)):
    a = get_or_404(db, Alarm, alarm_id, "Alarm")
    alarm_svc.transition(db, a, body.action, actor.username, body.note)
    audit(db, actor, f"alarm.{body.action}", "alarm", a.id, f"Alarm {a.id} {body.action}", {"note": body.note})
    db.commit()
    return get_alarm(alarm_id, db)


class BulkAckIn(BaseModel):
    alarm_ids: list[int] = Field(min_length=1, max_length=500)
    note: str = ""


@router.post("/alarms/acknowledge")
def bulk_ack(body: BulkAckIn, actor: Actor = Depends(require("alarm.manage")), db: Session = Depends(get_db)):
    done = []
    for aid in body.alarm_ids:
        a = db.get(Alarm, aid)
        if a and a.state in ("new", "reopened"):
            alarm_svc.transition(db, a, "acknowledge", actor.username, body.note)
            done.append(aid)
    audit(db, actor, "alarm.acknowledge_bulk", "alarm", None, f"{len(done)} alarms acknowledged", {"ids": done})
    db.commit()
    return {"acknowledged": done}


# ================================================================== incidents
def incident_brief(i: Incident) -> dict:
    return ser(i, exclude={"impact"})


@router.get("/incidents")
def list_incidents(page: Page = Depends(), status: str | None = "active", olt_id: int | None = None, db: Session = Depends(get_db), _=MREAD):
    stmt = select(Incident).order_by(Incident.started_at.desc())
    if status == "active":
        stmt = stmt.where(Incident.status != IncidentStatus.RESOLVED)
    elif status and status != "all":
        stmt = stmt.where(Incident.status == status)
    if olt_id:
        stmt = stmt.where(Incident.olt_id == olt_id)
    return paginate(db, stmt, page, mapper=incident_brief)


@router.get("/incidents/{iid}")
def get_incident(iid: int, live: bool = False, db: Session = Depends(get_db), _=MREAD):
    i = get_or_404(db, Incident, iid, "Incident")
    out = ser(i)
    out["alarms"] = [ser(a, exclude={"vendor_raw"}) for a in db.scalars(select(Alarm).where(Alarm.incident_id == i.id))]
    if live and i.status != IncidentStatus.RESOLVED:
        out["impact"] = impact_svc.analyze(db, i.source_type, i.source_id)
    return out


class IncidentIn(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    source_type: Literal["olt", "olt_port", "board", "splitter", "fat", "dpoint", "cable", "link"]
    source_id: int
    suspected_cause: str = ""
    severity: Literal["critical", "major", "minor", "warning"] = "major"


@router.post("/incidents", status_code=201)
def create_incident(body: IncidentIn, actor: Actor = Depends(require("incident.manage")), db: Session = Depends(get_db)):
    analysis = impact_svc.analyze(db, body.source_type, body.source_id)
    i = Incident(title=body.title, source_type=body.source_type, source_id=body.source_id, source_label=body.title,
                 suspected_cause=body.suspected_cause, severity=body.severity, detection="manual", impact=analysis,
                 affected_onts=analysis["summary"]["onts"], confirmed_down_onts=analysis["summary"]["confirmed_down"],
                 affected_subscribers=analysis["summary"]["subscribers"], notes=f"Opened by {actor.username}")
    db.add(i)
    db.flush()
    audit(db, actor, "incident.create", "incident", i.id, body.title)
    db.commit()
    return get_incident(i.id, db=db)


class IncidentUpdate(BaseModel):
    status: Literal["open", "investigating", "resolved"] | None = None
    note: str = Field("", max_length=2000)
    suspected_cause: str | None = None


@router.patch("/incidents/{iid}")
def update_incident(iid: int, body: IncidentUpdate, actor: Actor = Depends(require("incident.manage")), db: Session = Depends(get_db)):
    i = get_or_404(db, Incident, iid, "Incident")
    if body.status:
        if i.status == IncidentStatus.RESOLVED and body.status != IncidentStatus.RESOLVED:
            i.resolved_at = None
            i.resolved_by = None
        i.status = body.status
        if body.status == IncidentStatus.RESOLVED:
            i.resolved_at, i.resolved_by = utcnow(), actor.username
    if body.suspected_cause is not None:
        i.suspected_cause = body.suspected_cause
    if body.note:
        i.notes = (i.notes + "\n" if i.notes else "") + f"[{utcnow().isoformat(timespec='seconds')}] {actor.username}: {body.note}"
    if not body.status and not body.note and body.suspected_cause is None:
        raise ValidationFailed("Nothing to update")
    audit(db, actor, "incident.update", "incident", i.id, f"Incident {i.id} updated", body.model_dump(exclude_unset=True))
    db.commit()
    return get_incident(iid, db=db)


# ================================================================== optical
@router.get("/optical/overview")
def optical_overview(olt_id: int | None = None, limit: int = Query(25, le=200), db: Session = Depends(get_db), _=MREAD):
    """Weakest ONTs now, and ONTs whose receive level declined the most over the last 7 days."""
    q = select(Ont).where(Ont.registration_status == "registered", Ont.rx_power_dbm.is_not(None))
    if olt_id:
        q = q.where(Ont.olt_id == olt_id)
    onts = list(db.scalars(q))
    weakest = sorted(onts, key=lambda o: o.rx_power_dbm)[:limit]
    since = utcnow() - timedelta(days=7)
    declining = []
    for o in onts:
        first = db.scalar(select(OpticalReading.rx_power_dbm).where(OpticalReading.ont_id == o.id, OpticalReading.ts >= since,
                                                                    OpticalReading.rx_power_dbm.is_not(None)).order_by(OpticalReading.ts).limit(1))
        if first is not None and o.rx_power_dbm is not None:
            delta = round(o.rx_power_dbm - first, 2)
            if delta <= -1.0:
                declining.append({"id": o.id, "serial_number": o.serial_number, "rx_now": o.rx_power_dbm, "rx_7d_ago": first,
                                  "delta_db": delta, "optical_status": o.optical_status, "olt": o.olt.name if o.olt else None,
                                  "pon_port": o.pon_port.fsp if o.pon_port else None})
    declining.sort(key=lambda x: x["delta_db"])
    brief = lambda o: {"id": o.id, "serial_number": o.serial_number, "rx_power_dbm": o.rx_power_dbm,  # noqa: E731
                       "olt_rx_power_dbm": o.olt_rx_power_dbm, "optical_status": o.optical_status, "status": o.status,
                       "olt": o.olt.name if o.olt else None, "pon_port": o.pon_port.fsp if o.pon_port else None,
                       "simulated": o.is_simulated}
    dist = {"ok": 0, "warning": 0, "critical": 0, "no_reading": 0}
    for o in db.scalars(select(Ont).where(Ont.registration_status == "registered", *([Ont.olt_id == olt_id] if olt_id else []))):
        dist[o.optical_status or "no_reading"] = dist.get(o.optical_status or "no_reading", 0) + 1
    return {"weakest": [brief(o) for o in weakest], "declining": declining[:limit], "distribution": dist}


class ThresholdIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    vendor: str | None = None
    model: str | None = None
    rx_warning_low: float = Field(ge=-50, le=10)
    rx_critical_low: float = Field(ge=-50, le=10)
    rx_warning_high: float | None = Field(None, ge=-50, le=10)
    rx_critical_high: float | None = Field(None, ge=-50, le=10)
    olt_rx_warning_low: float | None = Field(None, ge=-50, le=10)
    olt_rx_critical_low: float | None = Field(None, ge=-50, le=10)
    notes: str = ""


def _check_threshold(b: ThresholdIn):
    if b.rx_critical_low >= b.rx_warning_low:
        raise ValidationFailed("rx_critical_low must be lower than rx_warning_low")
    if b.rx_warning_high is not None and b.rx_critical_high is not None and b.rx_critical_high <= b.rx_warning_high:
        raise ValidationFailed("rx_critical_high must be higher than rx_warning_high")


@router.get("/optical-thresholds")
def list_thresholds(db: Session = Depends(get_db), _=MREAD):
    return [ser(t) for t in db.scalars(select(OpticalThreshold).order_by(OpticalThreshold.id))]


@router.post("/optical-thresholds", status_code=201)
def create_threshold(body: ThresholdIn, actor: Actor = Depends(require("monitoring.configure")), db: Session = Depends(get_db)):
    _check_threshold(body)
    t = OpticalThreshold(**body.model_dump())
    db.add(t)
    db.flush()
    audit(db, actor, "threshold.create", "optical_threshold", t.id, f"Threshold {t.name}", body.model_dump())
    db.commit()
    return ser(t)


@router.put("/optical-thresholds/{tid}")
def update_threshold(tid: int, body: ThresholdIn, actor: Actor = Depends(require("monitoring.configure")), db: Session = Depends(get_db)):
    _check_threshold(body)
    t = get_or_404(db, OpticalThreshold, tid)
    for k, v in body.model_dump().items():
        setattr(t, k, v)
    audit(db, actor, "threshold.update", "optical_threshold", t.id, f"Threshold {t.name} updated", body.model_dump())
    db.commit()
    return ser(t)


@router.delete("/optical-thresholds/{tid}", status_code=204)
def delete_threshold(tid: int, actor: Actor = Depends(require("monitoring.configure")), db: Session = Depends(get_db)):
    t = get_or_404(db, OpticalThreshold, tid)
    db.delete(t)
    audit(db, actor, "threshold.delete", "optical_threshold", tid, f"Threshold {t.name} deleted")
    db.commit()
