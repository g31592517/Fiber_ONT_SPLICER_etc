"""Subscribers & services, bandwidth profiles, service templates and provisioning jobs."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.common import Page, paginate, ser
from app.core import topology
from app.core.audit import Actor, audit
from app.core.errors import Conflict, ValidationFailed, get_or_404
from app.db import get_db
from app.engines import provisioning as prov
from app.models import (
    BandwidthProfile,
    FiberLink,
    InfraPort,
    Ont,
    ProvisioningJob,
    Service,
    ServiceTemplate,
    Subscriber,
    TemplateVendorMapping,
)
from app.security.auth import require

router = APIRouter(tags=["services & provisioning"])
NREAD = Depends(require("network.read"))
PREAD = Depends(require("provisioning.read"))


# ================================================================== subscribers
class SubscriberIn(BaseModel):
    customer_ref: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    account_ref: str | None = None
    external_customer_id: str | None = None
    external_account_id: str | None = None
    installation_address: str = ""
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    contact_phone: str | None = Field(None, max_length=32)
    notes: str = ""


def service_out(db: Session, s: Service) -> dict:
    ont = s.ont
    fat = topology.fat_for_ont(db, ont.id) if ont else None
    return ser(s, subscriber=ser(s.subscriber), template=s.template.name if s.template else None,
               ont=({"id": ont.id, "serial_number": ont.serial_number, "status": ont.status, "rx_power_dbm": ont.rx_power_dbm,
                     "olt_id": ont.olt_id, "olt": ont.olt.name if ont.olt else None,
                     "pon_port": ont.pon_port.fsp if ont.pon_port else None, "pon_port_id": ont.pon_port_id} if ont else None),
               fat=fat)


def subscriber_out(db: Session, s: Subscriber) -> dict:
    return ser(s, services=[service_out(db, x) for x in db.scalars(select(Service).where(Service.subscriber_id == s.id))])


@router.get("/subscribers")
def list_subscribers(page: Page = Depends(), q: str | None = Query(None, max_length=64), ont_serial: str | None = None,
                     fat_id: int | None = None, pon_port_id: int | None = None, olt_id: int | None = None,
                     db: Session = Depends(get_db), _=NREAD):
    """Find subscribers by name/reference, ONT serial, FAT or PON port."""
    stmt = select(Subscriber).order_by(Subscriber.display_name)
    if q:
        stmt = stmt.where(or_(Subscriber.display_name.ilike(f"%{q}%"), Subscriber.customer_ref.ilike(f"%{q}%"),
                              Subscriber.account_ref.ilike(f"%{q}%"), Subscriber.external_customer_id == q,
                              Subscriber.installation_address.ilike(f"%{q}%")))
    ont_filter = None
    if ont_serial:
        ont_filter = select(Ont.id).where(Ont.serial_number.ilike(f"%{ont_serial}%"))
    if pon_port_id:
        ont_filter = select(Ont.id).where(Ont.pon_port_id == pon_port_id)
    if olt_id:
        ont_filter = select(Ont.id).where(Ont.olt_id == olt_id)
    if fat_id:
        ports = select(InfraPort.id).where(InfraPort.owner_type == "fat", InfraPort.owner_id == fat_id)
        ont_filter = select(FiberLink.b_id).where(FiberLink.a_type == "infra_port", FiberLink.a_id.in_(ports),
                                                  FiberLink.b_type == "ont", FiberLink.status != "decommissioned")
    if ont_filter is not None:
        stmt = stmt.where(Subscriber.id.in_(select(Service.subscriber_id).where(Service.ont_id.in_(ont_filter))))
    return paginate(db, stmt, page, mapper=lambda s: subscriber_out(db, s))


@router.post("/subscribers", status_code=201)
def create_subscriber(body: SubscriberIn, actor: Actor = Depends(require("subscriber.write")), db: Session = Depends(get_db)):
    if db.scalar(select(Subscriber).where(Subscriber.customer_ref == body.customer_ref)):
        raise Conflict("customer_ref already exists")
    s = Subscriber(**body.model_dump())
    db.add(s)
    db.flush()
    audit(db, actor, "subscriber.create", "subscriber", s.id, f"Subscriber {s.customer_ref} created")
    db.commit()
    return subscriber_out(db, s)


@router.get("/subscribers/{sid}")
def get_subscriber(sid: int, db: Session = Depends(get_db), _=NREAD):
    return subscriber_out(db, get_or_404(db, Subscriber, sid, "Subscriber"))


@router.put("/subscribers/{sid}")
def update_subscriber(sid: int, body: SubscriberIn, actor: Actor = Depends(require("subscriber.write")), db: Session = Depends(get_db)):
    s = get_or_404(db, Subscriber, sid, "Subscriber")
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    audit(db, actor, "subscriber.update", "subscriber", s.id, f"Subscriber {s.customer_ref} updated")
    db.commit()
    return subscriber_out(db, s)


class ServiceIn(BaseModel):
    subscriber_id: int
    service_ref: str = Field(min_length=1, max_length=64)
    external_service_id: str | None = None
    external_package_id: str | None = None
    ont_id: int | None = None
    template_id: int | None = None
    notes: str = ""


class ServicePatch(BaseModel):
    ont_id: int | None = None
    template_id: int | None = None
    external_service_id: str | None = None
    external_package_id: str | None = None
    notes: str | None = None


@router.get("/services")
def list_services(page: Page = Depends(), status: str | None = None, q: str | None = None, db: Session = Depends(get_db), _=NREAD):
    stmt = select(Service).order_by(Service.id.desc())
    if status:
        stmt = stmt.where(Service.status == status)
    if q:
        stmt = stmt.where(or_(Service.service_ref.ilike(f"%{q}%"), Service.external_service_id == q))
    return paginate(db, stmt, page, mapper=lambda s: service_out(db, s))


@router.post("/services", status_code=201)
def create_service(body: ServiceIn, actor: Actor = Depends(require("subscriber.write")), db: Session = Depends(get_db)):
    get_or_404(db, Subscriber, body.subscriber_id, "Subscriber")
    if db.scalar(select(Service).where(Service.service_ref == body.service_ref)):
        raise Conflict("service_ref already exists")
    if body.ont_id:
        get_or_404(db, Ont, body.ont_id, "ONT")
    if body.template_id:
        get_or_404(db, ServiceTemplate, body.template_id, "Template")
    s = Service(**body.model_dump(), status="pending")
    db.add(s)
    db.flush()
    audit(db, actor, "service.create", "service", s.id, f"Service {s.service_ref} created (pending)")
    db.commit()
    return service_out(db, s)


@router.get("/services/{sid}")
def get_service(sid: int, db: Session = Depends(get_db), _=NREAD):
    s = get_or_404(db, Service, sid, "Service")
    jobs = db.scalars(select(ProvisioningJob).where(ProvisioningJob.service_id == sid).order_by(ProvisioningJob.created_at.desc()))
    return service_out(db, s) | {"jobs": [job_summary(j) for j in jobs]}


@router.patch("/services/{sid}")
def patch_service(sid: int, body: ServicePatch, actor: Actor = Depends(require("subscriber.write")), db: Session = Depends(get_db)):
    s = get_or_404(db, Service, sid, "Service")
    data = body.model_dump(exclude_unset=True)
    if s.status in ("active", "suspended") and ("ont_id" in data or "template_id" in data):
        raise ValidationFailed("ONT/template of an active service change only through provisioning (modify_service)")
    for k, v in data.items():
        setattr(s, k, v)
    audit(db, actor, "service.update", "service", s.id, f"Service {s.service_ref} updated", data)
    db.commit()
    return service_out(db, s)


# ================================================================== bandwidth profiles & templates
class BandwidthIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    download_kbps: int = Field(ge=64, le=100_000_000)
    upload_kbps: int = Field(ge=64, le=100_000_000)
    description: str = ""
    is_active: bool = True


@router.get("/bandwidth-profiles")
def list_bw(db: Session = Depends(get_db), _=PREAD):
    return [ser(b) for b in db.scalars(select(BandwidthProfile).order_by(BandwidthProfile.download_kbps))]


@router.post("/bandwidth-profiles", status_code=201)
def create_bw(body: BandwidthIn, actor: Actor = Depends(require("provisioning.profiles")), db: Session = Depends(get_db)):
    if db.scalar(select(BandwidthProfile).where(BandwidthProfile.name == body.name)):
        raise Conflict("Name exists")
    b = BandwidthProfile(**body.model_dump())
    db.add(b)
    db.flush()
    audit(db, actor, "bandwidth_profile.create", "bandwidth_profile", b.id, f"Bandwidth profile {b.name}")
    db.commit()
    return ser(b)


@router.put("/bandwidth-profiles/{bid}")
def update_bw(bid: int, body: BandwidthIn, actor: Actor = Depends(require("provisioning.profiles")), db: Session = Depends(get_db)):
    b = get_or_404(db, BandwidthProfile, bid)
    for k, v in body.model_dump().items():
        setattr(b, k, v)
    audit(db, actor, "bandwidth_profile.update", "bandwidth_profile", b.id, f"Bandwidth profile {b.name} updated")
    db.commit()
    return ser(b)


class MappingIn(BaseModel):
    adapter: str
    olt_model: str = ""
    line_profile_ref: str | None = None
    service_profile_ref: str | None = None
    dba_profile_ref: str | None = None
    traffic_table_up_ref: str | None = None
    traffic_table_down_ref: str | None = None
    parameters: dict | None = None


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    service_type: Literal["internet", "voip", "iptv", "management"] = "internet"
    bandwidth_profile_id: int | None = None
    vlan_mode: Literal["single", "qinq", "transparent"] = "single"
    svlan: int | None = Field(None, ge=1, le=4094)
    cvlan: int | None = Field(None, ge=1, le=4094)
    user_vlan: int | None = Field(None, ge=1, le=4094)
    gem_port: int | None = Field(None, ge=0, le=4095)
    tcont: int | None = Field(None, ge=0, le=255)
    wan_mode: Literal["pppoe", "dhcp", "static", "bridge"] | None = None
    description: str = ""
    is_active: bool = True
    mappings: list[MappingIn] = []


def template_out(t: ServiceTemplate) -> dict:
    return ser(t, bandwidth_profile=ser(t.bandwidth_profile) if t.bandwidth_profile else None,
               mappings=[ser(m) for m in t.mappings])


def _apply_template(db: Session, t: ServiceTemplate, body: TemplateIn) -> None:
    from app.adapters.registry import adapter_class

    if body.vlan_mode == "qinq" and not body.cvlan:
        raise ValidationFailed("QinQ templates require a C-VLAN")
    if body.vlan_mode != "transparent" and not body.svlan:
        raise ValidationFailed("A service VLAN is required")
    for m in body.mappings:
        adapter_class(m.adapter)
    for k, v in body.model_dump(exclude={"mappings"}).items():
        setattr(t, k, v)
    t.mappings = [TemplateVendorMapping(**m.model_dump()) for m in body.mappings]


@router.get("/service-templates")
def list_templates(db: Session = Depends(get_db), _=PREAD):
    return [template_out(t) for t in db.scalars(select(ServiceTemplate).order_by(ServiceTemplate.name))]


@router.post("/service-templates", status_code=201)
def create_template(body: TemplateIn, actor: Actor = Depends(require("provisioning.profiles")), db: Session = Depends(get_db)):
    if db.scalar(select(ServiceTemplate).where(ServiceTemplate.name == body.name)):
        raise Conflict("Template name exists")
    t = ServiceTemplate(name=body.name)
    _apply_template(db, t, body)
    db.add(t)
    db.flush()
    audit(db, actor, "service_template.create", "service_template", t.id, f"Template {t.name} created", body.model_dump())
    db.commit()
    return template_out(t)


@router.get("/service-templates/{tid}")
def get_template(tid: int, db: Session = Depends(get_db), _=PREAD):
    return template_out(get_or_404(db, ServiceTemplate, tid, "Template"))


@router.put("/service-templates/{tid}")
def update_template(tid: int, body: TemplateIn, actor: Actor = Depends(require("provisioning.profiles")), db: Session = Depends(get_db)):
    t = get_or_404(db, ServiceTemplate, tid, "Template")
    _apply_template(db, t, body)
    audit(db, actor, "service_template.update", "service_template", t.id, f"Template {t.name} updated", body.model_dump())
    db.commit()
    return template_out(t)


# ================================================================== provisioning jobs
class ProvisioningIn(BaseModel):
    operation: Literal["register_ont", "activate_service", "modify_service", "suspend_service", "restore_service",
                       "disconnect_service", "deregister_ont"]
    discovery_id: int | None = None
    ont_id: int | None = None
    service_id: int | None = None
    template_id: int | None = None
    ont_index: int | None = Field(None, ge=0, le=255)
    line_profile_ref: str | None = None
    service_profile_ref: str | None = None
    auth_method: Literal["sn", "loid", "password", "sn_password"] = "sn"
    loid: str | None = None
    auth_password: str | None = Field(None, max_length=64)
    description: str | None = Field(None, max_length=64)
    fat_port_id: int | None = None
    use_reserved_port: bool = False
    deregister_ont: bool = False
    force: bool = False
    rollback_on_failure: bool = True
    auto_approve: bool = False
    external_request_id: str | None = Field(None, max_length=64)


def job_summary(j: ProvisioningJob) -> dict:
    return ser(j, exclude={"plan", "verification", "result", "validation", "request"},
               errors=(j.validation or {}).get("errors", []), warnings=(j.validation or {}).get("warnings", []))


def job_detail(db: Session, j: ProvisioningJob) -> dict:
    out = ser(j, steps=[ser(s) for s in j.steps], events=[ser(e) for e in j.events])
    out["request"] = {k: v for k, v in (j.request or {}).items() if not k.endswith("_enc")}
    out["retries"] = [x.id for x in db.scalars(select(ProvisioningJob).where(ProvisioningJob.retry_of == j.id))]
    out["retryable"] = j.state in prov.RETRYABLE_STATES and not out["retries"]
    out["cancellable"] = j.state in prov.CANCELLABLE_STATES
    out["confirmation_token"] = prov._confirmation_token(db, j) if (j.operation in prov.DESTRUCTIVE_OPS or any(s.destructive for s in j.steps)) else None
    return out


@router.get("/provisioning/jobs")
def list_prov_jobs(page: Page = Depends(), state: str | None = None, operation: str | None = None, ont_id: int | None = None,
                   service_id: int | None = None, db: Session = Depends(get_db), _=PREAD):
    stmt = select(ProvisioningJob).order_by(ProvisioningJob.created_at.desc())
    if state:
        stmt = stmt.where(ProvisioningJob.state.in_(state.split(",")))
    if operation:
        stmt = stmt.where(ProvisioningJob.operation == operation)
    if ont_id:
        stmt = stmt.where(ProvisioningJob.ont_id == ont_id)
    if service_id:
        stmt = stmt.where(ProvisioningJob.service_id == service_id)
    return paginate(db, stmt, page, mapper=job_summary)


@router.post("/provisioning/jobs", status_code=201)
def create_prov_job(body: ProvisioningIn, actor: Actor = Depends(require("network.read")), db: Session = Depends(get_db)):
    job = prov.create_request(db, actor, body.model_dump(exclude_none=True))
    db.commit()
    return job_detail(db, job)


@router.get("/provisioning/jobs/{job_id}")
def get_prov_job(job_id: str, db: Session = Depends(get_db), _=PREAD):
    return job_detail(db, get_or_404(db, ProvisioningJob, job_id, "Provisioning job"))


class ApproveIn(BaseModel):
    confirm: str | None = None


@router.post("/provisioning/jobs/{job_id}/approve")
def approve_job(job_id: str, body: ApproveIn, actor: Actor = Depends(require("network.read")), db: Session = Depends(get_db)):
    job = get_or_404(db, ProvisioningJob, job_id, "Provisioning job")
    prov.approve(db, job, actor, body.confirm)
    db.commit()
    return job_detail(db, job)


@router.post("/provisioning/jobs/{job_id}/cancel")
def cancel_job(job_id: str, actor: Actor = Depends(require("provisioning.request")), db: Session = Depends(get_db)):
    job = get_or_404(db, ProvisioningJob, job_id, "Provisioning job")
    prov.cancel(db, job, actor)
    db.commit()
    return job_detail(db, job)


@router.post("/provisioning/jobs/{job_id}/retry", status_code=201)
def retry_job(job_id: str, actor: Actor = Depends(require("provisioning.request")), db: Session = Depends(get_db)):
    job = get_or_404(db, ProvisioningJob, job_id, "Provisioning job")
    new = prov.retry(db, job, actor)
    db.commit()
    return job_detail(db, new)
