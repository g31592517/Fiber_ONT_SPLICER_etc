"""Vendor integrations (adapter catalogue/capabilities/health), simulator control and the
future billing-system integration contract (``/integration/v1``)."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import Capability
from app.adapters.registry import all_adapters
from app.adapters.simulator.device import EVENT_TYPES, apply_event
from app.api.admin import get_setting
from app.api.common import job_ref, ser
from app.config import get_settings
from app.core.audit import Actor, audit
from app.core.errors import NotFound, ValidationFailed, get_or_404
from app.db import get_db
from app.engines import provisioning as prov
from app.jobs.queue import enqueue
from app.models import Job, Olt, ProvisioningJob, Service, ServiceTemplate, SimulatedDevice
from app.security.auth import require

router = APIRouter(tags=["integrations"])


@router.get("/adapters")
def list_adapters(_=Depends(require("network.read"))):
    s = get_settings()
    out = []
    for a in all_adapters():
        out.append({
            "key": a.key, "name": a.display_name, "vendor": a.vendor, "models": a.supported_models, "protocols": a.protocols,
            "description": a.description, "planned": a.planned, "experimental": a.experimental,
            "selectable": not a.planned and (a.key != "simulator" or s.simulator_enabled),
            "writes_enabled": not a.planned and (not a.experimental or s.allow_unverified_adapter_writes),
            "capabilities": {k: {"supported": v.supported, "verification": v.verification, "notes": v.notes}
                             for k, v in a.capabilities().items()},
        })
    return {"capabilities": [c.value for c in Capability], "adapters": out}


@router.get("/integrations/health")
def integration_health(db: Session = Depends(get_db), _=Depends(require("monitoring.read"))):
    rows = []
    for o in db.scalars(select(Olt).order_by(Olt.name)):
        last = db.scalars(select(Job).where(Job.job_type == "olt.poll", Job.dedupe_key == f"poll:{o.id}")
                          .order_by(Job.created_at.desc()).limit(1)).first()
        rows.append({"olt_id": o.id, "olt": o.name, "adapter": o.adapter, "protocol": o.management_protocol, "host": o.host,
                     "connection_status": o.connection_status, "last_contact_at": o.last_contact_at.isoformat() if o.last_contact_at else None,
                     "last_poll_at": o.last_poll_at.isoformat() if o.last_poll_at else None, "last_poll_error": o.last_poll_error,
                     "next_poll_at": o.next_poll_at.isoformat() if o.next_poll_at else None,
                     "poll_interval_seconds": o.poll_interval_seconds, "monitoring_enabled": o.monitoring_enabled,
                     "snmp_version": o.snmp_version, "has_password": bool(o.password_enc),
                     "has_snmp_credentials": bool(o.snmp_community_enc or o.snmp_v3_auth_key_enc),
                     "last_poll_job": ser(last) if last else None, "simulated": o.is_simulated})
    return rows


# ------------------------------------------------------------------ simulator
class SimEventIn(BaseModel):
    event: str
    params: dict[str, Any] = {}
    poll_after: bool = True


@router.get("/simulator/events")
def sim_events(_=Depends(require("network.read"))):
    return [{"event": k, "description": v} for k, v in EVENT_TYPES.items()]


@router.get("/simulator/olts/{olt_id}/state")
def sim_state(olt_id: int, db: Session = Depends(get_db), _=Depends(require("simulator.control"))):
    olt = get_or_404(db, Olt, olt_id, "OLT")
    dev = db.scalar(select(SimulatedDevice).where(SimulatedDevice.host == olt.host))
    if olt.adapter != "simulator" or dev is None:
        raise ValidationFailed("This OLT is not simulated")
    st = dev.state
    return {"reachable": st["reachable"], "temperature": st["temperature"], "fail_next": st.get("fail_next", {}),
            "onts": [{"fsp": f, "ont_index": int(i), "serial": o["serial"], "state": o["state"], "rx_base": o["rx_base"],
                      "admin": o.get("admin")} for f, onts in st["onts"].items() for i, o in onts.items()],
            "autofind": st["autofind"], "pon_ports": st["pon_ports"],
            "boards": [{k: b.get(k) for k in ("slot", "installed", "board_type", "status")} for b in st["boards"]],
            "service_ports": st["service_ports"]}


@router.post("/simulator/olts/{olt_id}/events")
def sim_event(olt_id: int, body: SimEventIn, actor: Actor = Depends(require("simulator.control")), db: Session = Depends(get_db)):
    if not get_settings().simulator_enabled:
        raise ValidationFailed("Simulator disabled")
    olt = get_or_404(db, Olt, olt_id, "OLT")
    dev = db.scalar(select(SimulatedDevice).where(SimulatedDevice.host == olt.host))
    if olt.adapter != "simulator" or dev is None:
        raise ValidationFailed("Simulator events can only target simulated OLTs")
    import copy

    state = copy.deepcopy(dev.state)
    try:
        message = apply_event(state, body.event, body.params)
    except (KeyError, ValueError) as exc:
        raise ValidationFailed(f"Invalid simulator event: {exc}")
    dev.state = state
    audit(db, actor, "simulator.event", "olt", olt.id, f"[SIMULATION] {message}", {"event": body.event, "params": body.params})
    job = enqueue(db, "olt.poll", {"olt_id": olt.id}, created_by=actor.username, dedupe_key=f"poll:{olt.id}") if body.poll_after else None
    db.commit()
    return {"message": message, "simulated": True, "poll_job": job_ref(job) if job else None}


# ------------------------------------------------------------------ future billing integration contract
integration = APIRouter(prefix="/integration/v1", tags=["integration (future billing system)"])


class ExternalActivateIn(BaseModel):
    external_request_id: str = Field(min_length=1, max_length=64, description="Idempotency key supplied by the caller")
    template_id: int | None = None
    external_package_id: str | None = None


class ExternalChangeIn(BaseModel):
    external_request_id: str = Field(min_length=1, max_length=64)
    template_id: int


class ExternalSimpleIn(BaseModel):
    external_request_id: str = Field(min_length=1, max_length=64)
    deregister_ont: bool = False


def _svc(db: Session, external_service_id: str) -> Service:
    s = db.scalar(select(Service).where(Service.external_service_id == external_service_id))
    if s is None:
        raise NotFound(f"No service with external_service_id '{external_service_id}'")
    return s


def _status(db: Session, s: Service) -> dict:
    last = db.get(ProvisioningJob, s.last_job_id) if s.last_job_id else None
    ont = s.ont
    return {"external_service_id": s.external_service_id, "service_ref": s.service_ref, "status": s.status,
            "external_customer_id": s.subscriber.external_customer_id, "external_package_id": s.external_package_id,
            "template": s.template.name if s.template else None,
            "ont": {"serial_number": ont.serial_number, "status": ont.status, "rx_power_dbm": ont.rx_power_dbm} if ont else None,
            "last_job": {"id": last.id, "operation": last.operation, "state": last.state} if last else None}


def _submit(db: Session, actor: Actor, op: str, s: Service, external_request_id: str, **extra) -> dict:
    auto = bool(get_setting(db, "integration.auto_approve"))
    job = prov.create_request(db, actor, {"operation": op, "service_id": s.id, "external_request_id": external_request_id,
                                          "auto_approve": auto, **extra})
    db.commit()
    return {"job_id": job.id, "state": job.state, "auto_approved": auto, "status_url": f"/api/v1/integration/v1/jobs/{job.id}"}


@integration.get("/services/{external_service_id}")
def ext_status(external_service_id: str, db: Session = Depends(get_db), _=Depends(require("integration.api"))):
    return _status(db, _svc(db, external_service_id))


@integration.post("/services/{external_service_id}/activate", status_code=202)
def ext_activate(external_service_id: str, body: ExternalActivateIn, actor: Actor = Depends(require("integration.api")),
                 db: Session = Depends(get_db)):
    s = _svc(db, external_service_id)
    tpl = body.template_id or s.template_id
    if tpl is None:
        raise ValidationFailed("No service template given or associated with the service")
    get_or_404(db, ServiceTemplate, tpl, "Template")
    return _submit(db, actor, "activate_service", s, body.external_request_id, template_id=tpl)


@integration.post("/services/{external_service_id}/change-profile", status_code=202)
def ext_change(external_service_id: str, body: ExternalChangeIn, actor: Actor = Depends(require("integration.api")),
               db: Session = Depends(get_db)):
    s = _svc(db, external_service_id)
    return _submit(db, actor, "modify_service", s, body.external_request_id, template_id=body.template_id)


@integration.post("/services/{external_service_id}/{action}", status_code=202)
def ext_simple(external_service_id: str, action: Literal["suspend", "restore", "disconnect"], body: ExternalSimpleIn,
               actor: Actor = Depends(require("integration.api")), db: Session = Depends(get_db)):
    s = _svc(db, external_service_id)
    op = {"suspend": "suspend_service", "restore": "restore_service", "disconnect": "disconnect_service"}[action]
    extra = {"deregister_ont": body.deregister_ont} if action == "disconnect" else {}
    return _submit(db, actor, op, s, body.external_request_id, **extra)


@integration.get("/jobs/{job_id}")
def ext_job(job_id: str, db: Session = Depends(get_db), _=Depends(require("integration.api"))):
    j = get_or_404(db, ProvisioningJob, job_id, "Job")
    return {"job_id": j.id, "operation": j.operation, "state": j.state, "error": j.error,
            "validation_errors": (j.validation or {}).get("errors", []), "finished_at": j.finished_at.isoformat() if j.finished_at else None}
