"""ONT inventory, discovery, details, history and device operations."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.adapters.base import Capability
from app.adapters.registry import adapter_class
from app.api.common import Page, job_ref, paginate, ser
from app.core import optical, topology
from app.core.audit import Actor, audit
from app.core.enums import EndpointType
from app.core.errors import ValidationFailed, get_or_404
from app.db import get_db, utcnow
from app.engines import operations
from app.jobs.queue import enqueue
from app.models import (
    Alarm,
    FiberLink,
    InfraPort,
    Ont,
    OntDiscovery,
    OntStatusEvent,
    OpticalReading,
    ProvisioningJob,
    Service,
    Subscriber,
)
from app.security.auth import operation_rate_limit, require

router = APIRouter(tags=["onts"])
READ = Depends(require("network.read"))


def ont_brief(db: Session, o: Ont) -> dict:
    fat = topology.fat_for_ont(db, o.id)
    svc = db.scalars(select(Service).where(Service.ont_id == o.id).order_by(Service.id.desc())).first()
    return ser(o, olt_name=o.olt.name if o.olt else None, pon_port=o.pon_port.fsp if o.pon_port else None,
               line_profile=o.line_profile.name or o.line_profile.vendor_ref if o.line_profile else None,
               service_profile=o.service_profile.name or o.service_profile.vendor_ref if o.service_profile else None,
               fat=fat, subscriber=({"id": svc.subscriber_id, "customer_ref": svc.subscriber.customer_ref,
                                     "display_name": svc.subscriber.display_name, "service_ref": svc.service_ref,
                                     "service_status": svc.status} if svc else None))


@router.get("/onts")
def list_onts(page: Page = Depends(), q: str | None = Query(None, max_length=64), status: str | None = None,
              registration: str | None = "registered", olt_id: int | None = None, pon_port_id: int | None = None,
              site_id: int | None = None, fat_id: int | None = None, optical_status: str | None = None,
              sort: Literal["serial", "rx_asc", "status_changed"] = "serial", db: Session = Depends(get_db), _=READ):
    stmt = select(Ont)
    if registration and registration != "all":
        stmt = stmt.where(Ont.registration_status == registration)
    if status:
        stmt = stmt.where(Ont.status.in_(status.split(",")))
    if olt_id:
        stmt = stmt.where(Ont.olt_id == olt_id)
    if pon_port_id:
        stmt = stmt.where(Ont.pon_port_id == pon_port_id)
    if site_id:
        stmt = stmt.where(Ont.site_id == site_id)
    if optical_status:
        stmt = stmt.where(Ont.optical_status.in_(optical_status.split(",")))
    if fat_id:
        ports = list(db.scalars(select(InfraPort.id).where(InfraPort.owner_type == "fat", InfraPort.owner_id == fat_id)))
        ont_ids = list(db.scalars(select(FiberLink.b_id).where(FiberLink.a_type == EndpointType.INFRA_PORT,
                                                                FiberLink.a_id.in_(ports), FiberLink.b_type == EndpointType.ONT,
                                                                FiberLink.status != "decommissioned")))
        stmt = stmt.where(Ont.id.in_(ont_ids))
    if q:
        sub_ids = select(Service.ont_id).join(Subscriber, Service.subscriber_id == Subscriber.id).where(or_(
            Service.service_ref.ilike(f"%{q}%"), Subscriber.display_name.ilike(f"%{q}%"),
            Subscriber.customer_ref.ilike(f"%{q}%")))
        stmt = stmt.where(or_(Ont.serial_number.ilike(f"%{q}%"), Ont.description.ilike(f"%{q}%"),
                              Ont.mac_address.ilike(f"%{q}%"), Ont.id.in_(sub_ids)))
    order = {"serial": Ont.serial_number, "rx_asc": Ont.rx_power_dbm.asc().nulls_last(),
             "status_changed": Ont.status_changed_at.desc().nulls_last()}[sort]
    stmt = stmt.order_by(order)
    return paginate(db, stmt, page, mapper=lambda o: ont_brief(db, o))


@router.get("/onts/{ont_id}")
def get_ont(ont_id: int, db: Session = Depends(get_db), _=READ):
    o = get_or_404(db, Ont, ont_id, "ONT")
    th = optical.threshold_for_ont(db, o)
    _, violations = optical.evaluate(th, o.rx_power_dbm, o.olt_rx_power_dbm)
    services = [ser(s, subscriber=ser(s.subscriber), template=s.template.name if s.template else None)
                for s in db.scalars(select(Service).where(Service.ont_id == o.id))]
    caps = adapter_class(o.olt.adapter).capabilities() if o.olt else {}
    return ont_brief(db, o) | {
        "trace": topology.trace_ont(db, o),
        "services": services,
        "threshold": ser(th) if th else None,
        "optical_violations": violations,
        "open_alarms": [ser(a) for a in db.scalars(select(Alarm).where(Alarm.source_type == "ont", Alarm.source_id == o.id,
                                                                        Alarm.state != "resolved"))],
        "recent_jobs": [{"id": j.id, "operation": j.operation, "state": j.state, "created_at": j.created_at.isoformat()}
                        for j in db.scalars(select(ProvisioningJob).where(ProvisioningJob.ont_id == o.id)
                                            .order_by(ProvisioningJob.created_at.desc()).limit(10))],
        "capabilities": {k: {"supported": v.supported, "verification": v.verification} for k, v in caps.items()
                         if k in ("ont_status", "optical_power", "ont_reboot", "ont_enable_disable", "ont_deregistration",
                                  "service_suspension", "service_restoration")},
        "data_source": "simulated" if o.is_simulated else "device",
    }


class OntPatch(BaseModel):
    description: str | None = Field(None, max_length=255)
    notes: str | None = None
    installation_date: date | None = None
    site_id: int | None = None
    vendor: str | None = None
    model: str | None = None


@router.patch("/onts/{ont_id}")
def patch_ont(ont_id: int, body: OntPatch, actor: Actor = Depends(require("ont.write")), db: Session = Depends(get_db)):
    o = get_or_404(db, Ont, ont_id, "ONT")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(o, k, v)
    audit(db, actor, "ont.update", "ont", o.id, f"ONT {o.serial_number} inventory updated",
          {k: str(v) for k, v in changes.items()})
    db.commit()
    return ont_brief(db, o)


@router.get("/onts/{ont_id}/history")
def ont_history(ont_id: int, hours: int = Query(168, ge=1, le=24 * 365), db: Session = Depends(get_db),
                _=Depends(require("monitoring.read"))):
    get_or_404(db, Ont, ont_id, "ONT")
    since = utcnow() - timedelta(hours=hours)
    readings = db.scalars(select(OpticalReading).where(OpticalReading.ont_id == ont_id, OpticalReading.ts >= since)
                          .order_by(OpticalReading.ts))
    events = db.scalars(select(OntStatusEvent).where(OntStatusEvent.ont_id == ont_id).order_by(OntStatusEvent.ts.desc()).limit(200))
    alarms = db.scalars(select(Alarm).where(Alarm.source_type == "ont", Alarm.source_id == ont_id).order_by(Alarm.id.desc()).limit(100))
    return {"optical": [ser(r) for r in readings], "status_events": [ser(e) for e in events], "alarms": [ser(a) for a in alarms]}


@router.post("/onts/{ont_id}/refresh", status_code=202)
def refresh(ont_id: int, actor: Actor = Depends(require("ont.operate")), db: Session = Depends(get_db)):
    o = get_or_404(db, Ont, ont_id, "ONT")
    if o.olt is None:
        raise ValidationFailed("ONT is not associated with an OLT")
    job = enqueue(db, "ont.refresh", {"ont_id": o.id, "olt_id": o.olt_id}, created_by=actor.username, dedupe_key=f"ont-refresh:{o.id}")
    db.commit()
    return job_ref(job)


class OntOperationIn(BaseModel):
    operation: Literal["reboot", "enable", "disable"]
    confirm: str = Field("", description="Must equal the ONT serial number")


@router.post("/onts/{ont_id}/operations", status_code=202)
def ont_operation(ont_id: int, body: OntOperationIn, actor: Actor = Depends(operation_rate_limit), db: Session = Depends(get_db)):
    perm = {"reboot": "ont.reboot", "enable": "ont.disable", "disable": "ont.disable"}[body.operation]
    require(perm)(actor)
    o = get_or_404(db, Ont, ont_id, "ONT")
    operations.precheck(o, body.operation)
    if body.confirm.strip().upper() != o.serial_number.upper():
        raise ValidationFailed(f"Confirmation required: type the ONT serial number ({o.serial_number})",
                               {"confirmation_required": o.serial_number})
    job = enqueue(db, "ont.operation", {"ont_id": o.id, "olt_id": o.olt_id, "operation": body.operation,
                                        "user_id": actor.user_id, "username": actor.username}, created_by=actor.username)
    audit(db, actor, f"ont.{body.operation}.requested", "ont", o.id, f"{body.operation} requested for {o.serial_number}",
          {"job_id": job.id})
    db.commit()
    return job_ref(job)


# ------------------------------------------------------------------ discovery
@router.get("/discovered-onts")
def list_discovered(state: str = "unregistered", olt_id: int | None = None, q: str | None = None, db: Session = Depends(get_db), _=READ):
    stmt = select(OntDiscovery).order_by(OntDiscovery.last_seen_at.desc())
    if state != "all":
        stmt = stmt.where(OntDiscovery.state == state)
    if olt_id:
        stmt = stmt.where(OntDiscovery.olt_id == olt_id)
    if q:
        stmt = stmt.where(OntDiscovery.serial_number.ilike(f"%{q}%"))
    out = []
    for d in db.scalars(stmt):
        registered = db.scalar(select(Ont).where(Ont.serial_number == d.serial_number))
        out.append(ser(d, olt_name=d.olt.name, pon_port=d.pon_port.fsp if d.pon_port else None,
                       inventory_match=({"ont_id": registered.id, "registration_status": registered.registration_status}
                                        if registered else None)))
    return out


@router.get("/discovered-onts/{discovery_id}")
def get_discovered(discovery_id: int, db: Session = Depends(get_db), _=READ):
    d = get_or_404(db, OntDiscovery, discovery_id, "Discovered ONT")
    from app.models import OltProfile

    return ser(d, olt_name=d.olt.name, pon_port=d.pon_port.fsp if d.pon_port else None, olt_adapter=d.olt.adapter,
               pon_port_status=d.pon_port.oper_status if d.pon_port else None,
               profiles=[ser(p) for p in db.scalars(select(OltProfile).where(OltProfile.olt_id == d.olt_id))],
               registration_supported=adapter_class(d.olt.adapter).supports(Capability.ONT_REGISTRATION))


@router.post("/discovered-onts/{discovery_id}/ignore")
def ignore_discovered(discovery_id: int, actor: Actor = Depends(require("ont.register")), db: Session = Depends(get_db)):
    d = get_or_404(db, OntDiscovery, discovery_id, "Discovered ONT")
    d.state = "ignored" if d.state == "unregistered" else "unregistered" if d.state == "ignored" else d.state
    audit(db, actor, "ont.discovery_ignore", "ont_discovery", d.id, f"Discovery {d.serial_number} -> {d.state}")
    db.commit()
    return ser(d)
