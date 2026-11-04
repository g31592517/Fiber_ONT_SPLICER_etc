"""Sites, OLTs, OLT hardware (boards/ports), vendor profiles, VLANs and background jobs."""
from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.adapters.base import Capability
from app.adapters.registry import adapter_class, validate_adapter_key
from app.api.common import Page, job_ref, paginate, ser
from app.config import get_settings
from app.core.audit import Actor, audit
from app.core.enums import OntState
from app.core.errors import Conflict, ValidationFailed, get_or_404
from app.db import get_db, utcnow
from app.jobs.queue import enqueue
from app.models import (
    Alarm,
    DeviceMetric,
    Job,
    Olt,
    OltBoard,
    OltPort,
    OltProfile,
    Ont,
    OntDiscovery,
    PortMetric,
    Site,
    Vlan,
)
from app.security.auth import operation_rate_limit, require
from app.security.crypto import encrypt_secret

router = APIRouter(tags=["network"])
READ = Depends(require("network.read"))


# ================================================================== sites
class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code: str | None = Field(None, max_length=32)
    site_type: Literal["pop", "central_office", "cabinet", "street", "other"] = "pop"
    address: str = ""
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    description: str = ""


def site_out(db: Session, s: Site) -> dict:
    return ser(s, olt_count=db.scalar(select(func.count(Olt.id)).where(Olt.site_id == s.id)))


@router.get("/sites")
def list_sites(db: Session = Depends(get_db), _=READ):
    return [site_out(db, s) for s in db.scalars(select(Site).order_by(Site.name))]


@router.post("/sites", status_code=201)
def create_site(body: SiteIn, actor: Actor = Depends(require("infrastructure.write")), db: Session = Depends(get_db)):
    if db.scalar(select(Site).where(Site.name == body.name)):
        raise Conflict("A site with this name exists")
    s = Site(**body.model_dump())
    db.add(s)
    db.flush()
    audit(db, actor, "site.create", "site", s.id, f"Site {s.name} created")
    db.commit()
    return site_out(db, s)


@router.get("/sites/{site_id}")
def get_site(site_id: int, db: Session = Depends(get_db), _=READ):
    return site_out(db, get_or_404(db, Site, site_id))


@router.put("/sites/{site_id}")
def update_site(site_id: int, body: SiteIn, actor: Actor = Depends(require("infrastructure.write")), db: Session = Depends(get_db)):
    s = get_or_404(db, Site, site_id)
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    audit(db, actor, "site.update", "site", s.id, f"Site {s.name} updated")
    db.commit()
    return site_out(db, s)


@router.delete("/sites/{site_id}", status_code=204)
def delete_site(site_id: int, actor: Actor = Depends(require("infrastructure.write")), db: Session = Depends(get_db)):
    s = get_or_404(db, Site, site_id)
    if db.scalar(select(Olt.id).where(Olt.site_id == site_id)):
        raise ValidationFailed("Site still has OLTs")
    db.delete(s)
    audit(db, actor, "site.delete", "site", site_id, f"Site {s.name} deleted")
    db.commit()


# ================================================================== OLTs
class OltIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    site_id: int | None = None
    adapter: str
    manufacturer: str = ""
    model: str = ""
    serial_number: str | None = None
    firmware_version: str | None = None
    host: str = Field(min_length=1, max_length=255)
    management_protocol: Literal["ssh", "telnet", "api", "snmp", "simulator"] = "ssh"
    management_port: int | None = Field(None, ge=1, le=65535)
    rack_location: str = ""
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    slot_count: int | None = Field(None, ge=0, le=64)
    pon_port_count: int | None = Field(None, ge=0, le=1024)
    admin_status: Literal["enabled", "disabled", "maintenance"] = "enabled"
    poll_interval_seconds: int = 300
    monitoring_enabled: bool = True
    snmp_version: Literal["v3", "v2c"] | None = None
    snmp_port: int = Field(161, ge=1, le=65535)
    snmp_v3_username: str | None = None
    snmp_v3_auth_protocol: Literal["SHA", "SHA256", "SHA512", "MD5"] | None = None
    snmp_v3_priv_protocol: Literal["AES", "AES256", "DES"] | None = None
    username: str | None = None
    notes: str = ""
    extra: dict | None = None

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        if any(c in v for c in " /\\;|&$`"):
            raise ValueError("invalid host")
        return v.strip()


class CredentialsIn(BaseModel):
    """Write-only. Omitted fields are unchanged; empty string clears a secret."""

    username: str | None = None
    password: str | None = None
    enable_password: str | None = None
    api_token: str | None = None
    snmp_community: str | None = None
    snmp_v3_auth_key: str | None = None
    snmp_v3_priv_key: str | None = None


def olt_out(db: Session, o: Olt, detail: bool = False) -> dict:
    counts = dict(db.execute(select(Ont.status, func.count(Ont.id)).where(Ont.olt_id == o.id, Ont.registration_status == "registered")
                             .group_by(Ont.status)).all())
    total = sum(counts.values())
    cls = adapter_class(o.adapter)
    out = ser(o, site_name=o.site.name if o.site else None, adapter_name=cls.display_name, adapter_experimental=cls.experimental,
              onts_registered=total, onts_online=counts.get(OntState.ONLINE, 0),
              onts_offline=sum(v for k, v in counts.items() if k in ("offline", "los", "dying_gasp", "faulty")),
              onts_unreachable=counts.get(OntState.UNREACHABLE, 0),
              onts_discovered=db.scalar(select(func.count(OntDiscovery.id)).where(OntDiscovery.olt_id == o.id,
                                                                                   OntDiscovery.state == "unregistered")),
              active_alarms=db.scalar(select(func.count(Alarm.id)).where(Alarm.olt_id == o.id,
                                                                         Alarm.state.notin_(["resolved"]))))
    if snmp := o.snmp_version:
        out["snmp_security"] = "SNMPv3" if snmp == "v3" else "SNMPv2c (community-based, insecure — prefer SNMPv3)"
    if detail:
        out["capabilities"] = {k: {"supported": v.supported, "verification": v.verification, "notes": v.notes}
                               for k, v in cls.capabilities().items()}
        out["status_breakdown"] = counts
    return out


def _validate_interval(v: int) -> int:
    m = get_settings().min_poll_interval_seconds
    if v < m:
        raise ValidationFailed(f"Poll interval must be at least {m} seconds")
    return v


@router.get("/olts")
def list_olts(site_id: int | None = None, status: str | None = None, q: str | None = None, db: Session = Depends(get_db), _=READ):
    stmt = select(Olt).order_by(Olt.name)
    if site_id:
        stmt = stmt.where(Olt.site_id == site_id)
    if status:
        stmt = stmt.where(Olt.connection_status == status)
    if q:
        stmt = stmt.where(or_(Olt.name.ilike(f"%{q}%"), Olt.host.ilike(f"%{q}%"), Olt.model.ilike(f"%{q}%")))
    return [olt_out(db, o) for o in db.scalars(stmt)]


@router.post("/olts", status_code=201)
def create_olt(body: OltIn, actor: Actor = Depends(require("olt.write")), db: Session = Depends(get_db)):
    cls = validate_adapter_key(body.adapter)
    _validate_interval(body.poll_interval_seconds)
    if db.scalar(select(Olt).where(Olt.name == body.name)):
        raise Conflict("An OLT with this name exists")
    if body.site_id:
        get_or_404(db, Site, body.site_id, "Site")
    data = body.model_dump()
    olt = Olt(**data, is_simulated=body.adapter == "simulator")
    if body.adapter == "simulator":
        olt.management_protocol = "simulator"
        olt.manufacturer = olt.manufacturer or "SIMULATED"
    else:
        olt.manufacturer = olt.manufacturer or cls.vendor
    olt.next_poll_at = utcnow()
    db.add(olt)
    db.flush()
    audit(db, actor, "olt.create", "olt", olt.id, f"OLT {olt.name} registered ({body.adapter})", data)
    db.commit()
    return olt_out(db, olt, detail=True)


@router.get("/olts/{olt_id}")
def get_olt(olt_id: int, db: Session = Depends(get_db), _=READ):
    return olt_out(db, get_or_404(db, Olt, olt_id), detail=True)


@router.put("/olts/{olt_id}")
def update_olt(olt_id: int, body: OltIn, actor: Actor = Depends(require("olt.write")), db: Session = Depends(get_db)):
    olt = get_or_404(db, Olt, olt_id)
    validate_adapter_key(body.adapter)
    _validate_interval(body.poll_interval_seconds)
    before = ser(olt)
    for k, v in body.model_dump().items():
        setattr(olt, k, v)
    olt.is_simulated = body.adapter == "simulator"
    after = ser(olt)
    audit(db, actor, "olt.update", "olt", olt.id, f"OLT {olt.name} configuration changed",
          {"changed": {k: {"from": before[k], "to": after[k]} for k in after if before.get(k) != after[k] and k != "updated_at"}})
    db.commit()
    return olt_out(db, olt, detail=True)


@router.put("/olts/{olt_id}/credentials")
def set_credentials(olt_id: int, body: CredentialsIn, actor: Actor = Depends(require("olt.credentials")),
                    db: Session = Depends(get_db)):
    olt = get_or_404(db, Olt, olt_id)
    changed = []
    data = body.model_dump(exclude_unset=True)
    if "username" in data:
        olt.username = data.pop("username") or None
        changed.append("username")
    for field, value in data.items():
        setattr(olt, f"{field}_enc", encrypt_secret(value) if value else None)
        changed.append(field)
    audit(db, actor, "olt.credentials_change", "olt", olt.id, f"Credentials changed: {', '.join(changed) or 'none'}",
          {"fields": changed})
    db.commit()
    return olt_out(db, olt, detail=True)


@router.delete("/olts/{olt_id}", status_code=204)
def delete_olt(olt_id: int, actor: Actor = Depends(require("olt.write")), db: Session = Depends(get_db)):
    olt = get_or_404(db, Olt, olt_id)
    if db.scalar(select(Ont.id).where(Ont.olt_id == olt_id, Ont.registration_status == "registered")):
        raise ValidationFailed("OLT still has registered ONTs; deregister or move them first")
    db.delete(olt)
    audit(db, actor, "olt.delete", "olt", olt_id, f"OLT {olt.name} deleted")
    db.commit()


ACTIONS = {
    "test-connection": ("olt.test_connection", "olt.operate"),
    "poll": ("olt.poll", "olt.operate"),
    "discover": ("olt.discover", "ont.discover"),
    "sync-profiles": ("olt.sync_profiles", "olt.operate"),
    "backup": ("olt.backup", "olt.operate"),
}


@router.post("/olts/{olt_id}/actions/{action}", status_code=202)
def olt_action(olt_id: int, action: Literal["test-connection", "poll", "discover", "sync-profiles", "backup"],
               actor: Actor = Depends(operation_rate_limit), db: Session = Depends(get_db)):
    job_type, perm = ACTIONS[action]
    require(perm)(actor)
    olt = get_or_404(db, Olt, olt_id)
    job = enqueue(db, job_type, {"olt_id": olt.id}, created_by=actor.username, dedupe_key=f"{job_type}:{olt.id}")
    audit(db, actor, f"olt.{action}", "olt", olt.id, f"{action} requested for {olt.name}", {"job_id": job.id})
    db.commit()
    return job_ref(job)


@router.get("/olts/{olt_id}/hardware")
def olt_hardware(olt_id: int, db: Session = Depends(get_db), _=READ):
    olt = get_or_404(db, Olt, olt_id)
    boards = list(db.scalars(select(OltBoard).where(OltBoard.olt_id == olt_id).order_by(OltBoard.frame, OltBoard.slot)))
    ports = list(db.scalars(select(OltPort).where(OltPort.olt_id == olt_id).order_by(OltPort.frame, OltPort.slot, OltPort.port)))
    ont_counts = dict(db.execute(select(Ont.pon_port_id, func.count(Ont.id)).where(Ont.olt_id == olt_id,
                                 Ont.registration_status == "registered").group_by(Ont.pon_port_id)).all())
    frames: dict[int, list] = {}
    for b in boards:
        frames.setdefault(b.frame, []).append(ser(b, ports=[port_out(p, ont_counts.get(p.id, 0)) for p in ports
                                                            if p.board_id == b.id or (p.frame == b.frame and p.slot == b.slot)]))
    loose = [port_out(p, ont_counts.get(p.id, 0)) for p in ports if not any(p.frame == b.frame and p.slot == b.slot for b in boards)]
    return {"olt_id": olt.id, "source_note": "Hardware is shown as last reported by the device (source=device) or entered manually.",
            "frames": [{"frame": f, "boards": bs} for f, bs in sorted(frames.items())], "unassigned_ports": loose,
            "supports_hardware_discovery": adapter_class(olt.adapter).supports(Capability.DEVICE_DISCOVERY)}


def port_out(p: OltPort, registered: int | None = None) -> dict:
    util = round(100 * registered / p.max_onts, 1) if registered is not None and p.max_onts else None
    return ser(p, fsp=p.fsp, olt_name=p.olt.name if p.olt else None, registered_onts=registered, utilization_percent=util)


class BoardIn(BaseModel):
    frame: int = 0
    slot: int = Field(ge=0, le=64)
    installed: bool = True
    board_type: str | None = None
    role: Literal["pon", "uplink", "control", "power", "other"] = "pon"
    status: Literal["normal", "failed", "disabled", "empty", "unknown"] = "unknown"
    port_count: int | None = Field(None, ge=0, le=64)


@router.post("/olts/{olt_id}/boards", status_code=201)
def add_board(olt_id: int, body: BoardIn, actor: Actor = Depends(require("olt.write")), db: Session = Depends(get_db)):
    """Manual hardware entry, for adapters that cannot discover hardware."""
    get_or_404(db, Olt, olt_id)
    if db.scalar(select(OltBoard).where(OltBoard.olt_id == olt_id, OltBoard.frame == body.frame, OltBoard.slot == body.slot)):
        raise Conflict("Slot already defined")
    b = OltBoard(olt_id=olt_id, source="manual", **body.model_dump())
    db.add(b)
    db.flush()
    if body.installed and body.port_count and body.role in ("pon", "uplink"):
        for p in range(body.port_count):
            db.add(OltPort(olt_id=olt_id, board_id=b.id, frame=body.frame, slot=body.slot, port=p,
                           port_type=body.role if body.role == "uplink" else "pon", source="manual"))
    audit(db, actor, "olt.board_add", "olt", olt_id, f"Board slot {body.slot} added manually")
    db.commit()
    return ser(b)


@router.get("/pon-ports")
def list_pon_ports(olt_id: int | None = None, site_id: int | None = None, status: str | None = None,
                   db: Session = Depends(get_db), _=READ):
    stmt = select(OltPort).where(OltPort.port_type == "pon").order_by(OltPort.olt_id, OltPort.frame, OltPort.slot, OltPort.port)
    if olt_id:
        stmt = stmt.where(OltPort.olt_id == olt_id)
    if site_id:
        stmt = stmt.join(Olt).where(Olt.site_id == site_id)
    if status:
        stmt = stmt.where(OltPort.oper_status == status)
    ports = list(db.scalars(stmt))
    counts = dict(db.execute(select(Ont.pon_port_id, func.count(Ont.id)).where(Ont.registration_status == "registered")
                             .group_by(Ont.pon_port_id)).all())
    los = dict(db.execute(select(Ont.pon_port_id, func.count(Ont.id)).where(Ont.status.in_(["los", "offline", "dying_gasp"]))
                          .group_by(Ont.pon_port_id)).all())
    return [port_out(p, counts.get(p.id, 0)) | {"onts_down": los.get(p.id, 0)} for p in ports]


@router.get("/pon-ports/{port_id}")
def get_pon_port(port_id: int, db: Session = Depends(get_db), _=READ):
    p = get_or_404(db, OltPort, port_id, "Port")
    onts = list(db.scalars(select(Ont).where(Ont.pon_port_id == port_id).order_by(Ont.ont_index)))
    from app.api.onts import ont_brief

    return port_out(p, sum(1 for o in onts if o.registration_status == "registered")) | {
        "onts": [ont_brief(db, o) for o in onts],
        "discovered": [ser(d) for d in db.scalars(select(OntDiscovery).where(OntDiscovery.pon_port_id == port_id,
                                                                             OntDiscovery.state == "unregistered"))],
    }


class PortPatch(BaseModel):
    label: str | None = None
    max_onts: int | None = Field(None, ge=1, le=1024)


@router.patch("/pon-ports/{port_id}")
def patch_port(port_id: int, body: PortPatch, actor: Actor = Depends(require("olt.write")), db: Session = Depends(get_db)):
    p = get_or_404(db, OltPort, port_id, "Port")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    audit(db, actor, "olt.port_update", "olt_port", p.id, f"Port {p.fsp} updated", body.model_dump(exclude_unset=True))
    db.commit()
    return port_out(p)


@router.get("/pon-ports/{port_id}/metrics")
def port_metrics(port_id: int, hours: int = Query(24, ge=1, le=24 * 90), db: Session = Depends(get_db), _=Depends(require("monitoring.read"))):
    since = utcnow() - timedelta(hours=hours)
    rows = db.scalars(select(PortMetric).where(PortMetric.port_id == port_id, PortMetric.ts >= since).order_by(PortMetric.ts))
    return [ser(r) for r in rows]


@router.get("/olts/{olt_id}/metrics")
def olt_metrics(olt_id: int, hours: int = Query(24, ge=1, le=24 * 90), db: Session = Depends(get_db),
                _=Depends(require("monitoring.read"))):
    since = utcnow() - timedelta(hours=hours)
    rows = db.scalars(select(DeviceMetric).where(DeviceMetric.olt_id == olt_id, DeviceMetric.ts >= since).order_by(DeviceMetric.ts))
    return [ser(r) for r in rows]


@router.get("/olts/{olt_id}/jobs")
def olt_jobs(olt_id: int, limit: int = Query(20, le=200), db: Session = Depends(get_db), _=READ):
    rows = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(500))
    return [ser(j) for j in rows if j.payload.get("olt_id") == olt_id][:limit]


# ================================================================== vendor profiles & VLANs
class ProfileIn(BaseModel):
    kind: Literal["line", "service", "dba", "traffic_table"]
    vendor_ref: str = Field(min_length=1, max_length=64)
    name: str = ""
    parameters: dict | None = None


@router.get("/olts/{olt_id}/profiles")
def list_profiles(olt_id: int, kind: str | None = None, db: Session = Depends(get_db), _=Depends(require("provisioning.read"))):
    stmt = select(OltProfile).where(OltProfile.olt_id == olt_id).order_by(OltProfile.kind, OltProfile.vendor_ref)
    if kind:
        stmt = stmt.where(OltProfile.kind == kind)
    return [ser(p) for p in db.scalars(stmt)]


@router.post("/olts/{olt_id}/profiles", status_code=201)
def add_profile(olt_id: int, body: ProfileIn, actor: Actor = Depends(require("provisioning.profiles")), db: Session = Depends(get_db)):
    get_or_404(db, Olt, olt_id)
    if db.scalar(select(OltProfile).where(OltProfile.olt_id == olt_id, OltProfile.kind == body.kind,
                                          OltProfile.vendor_ref == body.vendor_ref)):
        raise Conflict("Profile reference already recorded")
    p = OltProfile(olt_id=olt_id, source="manual", **body.model_dump())
    db.add(p)
    audit(db, actor, "profile.create", "olt_profile", None, f"{body.kind} profile {body.vendor_ref} recorded for OLT {olt_id}")
    db.commit()
    return ser(p)


@router.delete("/olt-profiles/{profile_id}", status_code=204)
def delete_profile(profile_id: int, actor: Actor = Depends(require("provisioning.profiles")), db: Session = Depends(get_db)):
    p = get_or_404(db, OltProfile, profile_id)
    db.delete(p)
    audit(db, actor, "profile.delete", "olt_profile", profile_id, f"{p.kind} profile {p.vendor_ref} removed")
    db.commit()


class VlanIn(BaseModel):
    vlan_id: int = Field(ge=1, le=4094)
    name: str = ""
    purpose: Literal["internet", "management", "voip", "iptv", "other"] = "internet"
    olt_id: int | None = None
    site_id: int | None = None
    description: str = ""


@router.get("/vlans")
def list_vlans(olt_id: int | None = None, db: Session = Depends(get_db), _=Depends(require("provisioning.read"))):
    stmt = select(Vlan).order_by(Vlan.vlan_id)
    if olt_id:
        stmt = stmt.where(or_(Vlan.olt_id == olt_id, Vlan.olt_id.is_(None)))
    return [ser(v) for v in db.scalars(stmt)]


@router.post("/vlans", status_code=201)
def create_vlan(body: VlanIn, actor: Actor = Depends(require("provisioning.profiles")), db: Session = Depends(get_db)):
    if db.scalar(select(Vlan).where(Vlan.vlan_id == body.vlan_id, Vlan.olt_id == body.olt_id)):
        raise Conflict("VLAN already defined for this scope")
    v = Vlan(**body.model_dump())
    db.add(v)
    db.flush()
    audit(db, actor, "vlan.create", "vlan", v.id, f"VLAN {v.vlan_id} defined")
    db.commit()
    return ser(v)


@router.delete("/vlans/{vlan_pk}", status_code=204)
def delete_vlan(vlan_pk: int, actor: Actor = Depends(require("provisioning.profiles")), db: Session = Depends(get_db)):
    v = get_or_404(db, Vlan, vlan_pk)
    db.delete(v)
    audit(db, actor, "vlan.delete", "vlan", vlan_pk, f"VLAN {v.vlan_id} removed")
    db.commit()


# ================================================================== jobs
@router.get("/jobs")
def list_jobs(page: Page = Depends(), status: str | None = None, job_type: str | None = None, db: Session = Depends(get_db), _=READ):
    stmt = select(Job).order_by(Job.created_at.desc())
    if status:
        stmt = stmt.where(Job.status == status)
    if job_type:
        stmt = stmt.where(Job.job_type == job_type)
    return paginate(db, stmt, page)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db), _=READ):
    return ser(get_or_404(db, Job, job_id))
