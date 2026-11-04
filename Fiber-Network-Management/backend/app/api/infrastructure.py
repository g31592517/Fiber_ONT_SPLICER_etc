"""Passive infrastructure: splitters, FATs, distribution points, cables, fiber cores and links;
plus path tracing and impact analysis."""
from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.admin import get_setting
from app.api.common import ser
from app.core import impact as impact_svc
from app.core import topology
from app.core.audit import Actor, audit
from app.core.enums import EndpointType, InfraOwner, PortDirection
from app.core.errors import Conflict, NotFound, ValidationFailed, get_or_404
from app.db import get_db, utcnow
from app.engines.correlation import upsert_incident
from app.models import Cable, DistributionPoint, Fat, FiberCore, FiberLink, InfraPort, Ont, Splitter, SplitterType

from app.security.auth import require

router = APIRouter(tags=["infrastructure"])
READ = Depends(require("network.read"))

WRITE = require("infrastructure.write")
MAINT = require("infrastructure.maintenance")
INFRA_STATUS = Literal["planned", "active", "faulty", "maintenance", "decommissioned"]
FIBER_COLORS = ["blue", "orange", "green", "brown", "slate", "white", "red", "black", "yellow", "violet", "rose", "aqua"]


def _port_out(db: Session, p: InfraPort) -> dict:
    ep = topology.Endpoint(EndpointType.INFRA_PORT, p.id)
    down = topology.link_from(db, ep)
    up = topology.link_into(db, ep)
    ont = None
    if down and down[0].b_type == EndpointType.ONT:
        o = db.get(Ont, down[0].b_id)
        if o:
            from app.models import Service

            svc = db.scalars(select(Service).where(Service.ont_id == o.id).order_by(Service.id.desc())).first()
            ont = {"id": o.id, "serial_number": o.serial_number, "status": o.status,
                   "subscriber": svc.subscriber.display_name if svc else None, "customer_ref": svc.subscriber.customer_ref if svc else None,
                   "service_ref": svc.service_ref if svc else None}
    return ser(p, effective_status=topology.port_effective_status(db, p),
               upstream=topology.link_summary(db, up) if up else None,
               downstream=[topology.link_summary(db, l) for l in down], ont=ont)


def _ports(db: Session, owner_type: str, owner_id: int) -> list[dict]:
    rows = db.scalars(select(InfraPort).where(InfraPort.owner_type == owner_type, InfraPort.owner_id == owner_id)
                      .order_by(InfraPort.direction, InfraPort.number))
    return [_port_out(db, p) for p in rows]


# ================================================================== splitter types
class SplitterTypeIn(BaseModel):
    name: str = Field(min_length=3, max_length=32, pattern=r"^\d+:\d+$")
    input_ports: int = Field(1, ge=1, le=4)
    output_ports: int = Field(ge=2, le=128)
    nominal_loss_db: float | None = Field(None, ge=0, le=30)
    splitter_kind: Literal["plc", "fbt"] = "plc"


@router.get("/splitter-types")
def list_splitter_types(db: Session = Depends(get_db), _=READ):
    return [ser(t) for t in db.scalars(select(SplitterType).order_by(SplitterType.output_ports))]


@router.post("/splitter-types", status_code=201)
def create_splitter_type(body: SplitterTypeIn, actor: Actor = Depends(WRITE), db: Session = Depends(get_db)):
    if db.scalar(select(SplitterType).where(SplitterType.name == body.name)):
        raise Conflict("Splitter type exists")
    t = SplitterType(**body.model_dump())
    db.add(t)
    audit(db, actor, "splitter_type.create", "splitter_type", None, f"Splitter type {body.name} added")
    db.commit()
    return ser(t)


# ================================================================== splitters
class SplitterIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    splitter_type_id: int
    site_id: int | None = None
    fat_id: int | None = None
    dpoint_id: int | None = None
    location: str = ""
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    status: INFRA_STATUS = "active"
    estimated_loss_db: float | None = Field(None, ge=0, le=40)
    installation_date: date | None = None
    notes: str = ""


def splitter_out(db: Session, s: Splitter, detail: bool = False) -> dict:
    out = ser(s, ratio=s.splitter_type.name, fat_name=db.get(Fat, s.fat_id).name if s.fat_id else None,
              capacity=topology.capacity_summary(db, InfraOwner.SPLITTER, s.id, PortDirection.OUT))
    if detail:
        out["ports"] = _ports(db, InfraOwner.SPLITTER, s.id)
        ins = [p for p in out["ports"] if p["direction"] == "in"]
        out["upstream_trace"] = topology.trace_upstream(db, topology.Endpoint(EndpointType.INFRA_PORT, ins[0]["id"])) if ins else None
        out["impact"] = impact_svc.analyze(db, InfraOwner.SPLITTER, s.id)["summary"]
    return out


@router.get("/splitters")
def list_splitters(site_id: int | None = None, fat_id: int | None = None, q: str | None = None, db: Session = Depends(get_db), _=READ):
    stmt = select(Splitter).order_by(Splitter.name)
    if site_id:
        stmt = stmt.where(Splitter.site_id == site_id)
    if fat_id:
        stmt = stmt.where(Splitter.fat_id == fat_id)
    if q:
        stmt = stmt.where(Splitter.name.ilike(f"%{q}%"))
    return [splitter_out(db, s) for s in db.scalars(stmt)]


@router.post("/splitters", status_code=201)
def create_splitter(body: SplitterIn, actor: Actor = Depends(WRITE), db: Session = Depends(get_db)):
    t = get_or_404(db, SplitterType, body.splitter_type_id, "Splitter type")
    if db.scalar(select(Splitter).where(Splitter.name == body.name)):
        raise Conflict("Splitter name exists")
    s = Splitter(**body.model_dump())
    if s.estimated_loss_db is None:
        s.estimated_loss_db = t.nominal_loss_db
    db.add(s)
    db.flush()
    topology.ensure_ports(db, InfraOwner.SPLITTER, s.id, PortDirection.IN, t.input_ports)
    topology.ensure_ports(db, InfraOwner.SPLITTER, s.id, PortDirection.OUT, t.output_ports)
    audit(db, actor, "splitter.create", "splitter", s.id, f"Splitter {s.name} ({t.name}) created")
    db.commit()
    return splitter_out(db, s, True)


@router.get("/splitters/{sid}")
def get_splitter(sid: int, db: Session = Depends(get_db), _=READ):
    return splitter_out(db, get_or_404(db, Splitter, sid, "Splitter"), True)


@router.put("/splitters/{sid}")
def update_splitter(sid: int, body: SplitterIn, actor: Actor = Depends(WRITE), db: Session = Depends(get_db)):
    s = get_or_404(db, Splitter, sid, "Splitter")
    if body.splitter_type_id != s.splitter_type_id:
        t = get_or_404(db, SplitterType, body.splitter_type_id)
        topology.ensure_ports(db, InfraOwner.SPLITTER, s.id, PortDirection.IN, t.input_ports)
        topology.ensure_ports(db, InfraOwner.SPLITTER, s.id, PortDirection.OUT, t.output_ports)
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    audit(db, actor, "splitter.update", "splitter", s.id, f"Splitter {s.name} updated")
    db.commit()
    return splitter_out(db, s, True)


@router.delete("/splitters/{sid}", status_code=204)
def delete_splitter(sid: int, actor: Actor = Depends(WRITE), db: Session = Depends(get_db)):
    s = get_or_404(db, Splitter, sid, "Splitter")
    topology.delete_guard(db, InfraOwner.SPLITTER, sid)
    topology.delete_ports(db, InfraOwner.SPLITTER, sid)
    db.delete(s)
    audit(db, actor, "splitter.delete", "splitter", sid, f"Splitter {s.name} deleted")
    db.commit()


# ================================================================== FATs
class FatIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code: str | None = Field(None, max_length=64)
    fat_type: Literal["pole", "wall", "pedestal", "underground", "indoor", "other"] = "pole"
    site_id: int | None = None
    address: str = ""
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    description: str = ""
    manufacturer: str | None = None
    installation_date: date | None = None
    capacity: int = Field(ge=1, le=256)
    status: INFRA_STATUS = "active"
    photos: list[dict] | None = None
    notes: str = ""


def fat_out(db: Session, f: Fat, detail: bool = False) -> dict:
    cap = topology.capacity_summary(db, InfraOwner.FAT, f.id)
    warn = float(get_setting(db, "monitoring.fat_capacity_warning_percent") or 80)
    out = ser(f, capacity_summary=cap, capacity_warning=cap["utilization_percent"] >= warn)
    if detail:
        ports = _ports(db, InfraOwner.FAT, f.id)
        out["ports"] = ports
        out["splitters"] = [splitter_out(db, s) for s in db.scalars(select(Splitter).where(Splitter.fat_id == f.id))]
        fed = next((p for p in ports if p.get("upstream")), None)
        path = topology.trace_upstream(db, topology.Endpoint(EndpointType.INFRA_PORT, fed["id"])) if fed else None
        out["network_path"] = path
        olt_hop = next((h for h in (path or {}).get("hops", []) if h["type"] == EndpointType.OLT_PORT), None)
        out["served_by"] = olt_hop
        out["impact"] = impact_svc.analyze(db, InfraOwner.FAT, f.id)["summary"]
    return out


@router.get("/fats")
def list_fats(site_id: int | None = None, q: str | None = None, status: str | None = None, near_full: bool = False,
              db: Session = Depends(get_db), _=READ):
    stmt = select(Fat).order_by(Fat.name)
    if site_id:
        stmt = stmt.where(Fat.site_id == site_id)
    if status:
        stmt = stmt.where(Fat.status == status)
    if q:
        stmt = stmt.where(or_(Fat.name.ilike(f"%{q}%"), Fat.code.ilike(f"%{q}%"), Fat.address.ilike(f"%{q}%")))
    rows = [fat_out(db, f) for f in db.scalars(stmt)]
    if near_full:
        rows = [r for r in rows if r["capacity_warning"]]
    return rows


@router.post("/fats", status_code=201)
def create_fat(body: FatIn, actor: Actor = Depends(WRITE), db: Session = Depends(get_db)):
    if db.scalar(select(Fat).where(Fat.name == body.name)):
        raise Conflict("FAT name exists")
    f = Fat(**body.model_dump())
    db.add(f)
    db.flush()
    topology.ensure_ports(db, InfraOwner.FAT, f.id, PortDirection.THROUGH, body.capacity)
    audit(db, actor, "fat.create", "fat", f.id, f"FAT {f.name} created with {body.capacity} ports")
    db.commit()
    return fat_out(db, f, True)


@router.get("/fats/{fid}")
def get_fat(fid: int, db: Session = Depends(get_db), _=READ):
    return fat_out(db, get_or_404(db, Fat, fid, "FAT"), True)


@router.put("/fats/{fid}")
def update_fat(fid: int, body: FatIn, actor: Actor = Depends(WRITE), db: Session = Depends(get_db)):
    f = get_or_404(db, Fat, fid, "FAT")
    topology.ensure_ports(db, InfraOwner.FAT, f.id, PortDirection.THROUGH, body.capacity)
    for k, v in body.model_dump().items():
        setattr(f, k, v)
    audit(db, actor, "fat.update", "fat", f.id, f"FAT {f.name} updated")
    db.commit()
    return fat_out(db, f, True)


@router.delete("/fats/{fid}", status_code=204)
def delete_fat(fid: int, actor: Actor = Depends(WRITE), db: Session = Depends(get_db)):
    f = get_or_404(db, Fat, fid, "FAT")
    topology.delete_guard(db, InfraOwner.FAT, fid)
    if db.scalar(select(Splitter.id).where(Splitter.fat_id == fid)):
        raise ValidationFailed("FAT houses splitters; move or delete them first")
    topology.delete_ports(db, InfraOwner.FAT, fid)
    db.delete(f)
    audit(db, actor, "fat.delete", "fat", fid, f"FAT {f.name} deleted")
    db.commit()


class PortPatch(BaseModel):
    status: Literal["available", "reserved", "faulty"] | None = None
    label: str | None = None
    reserved_for: str | None = None
    reserved_until: date | None = None
    drop_cable_id: int | None = None
    notes: str | None = None


@router.patch("/infra-ports/{port_id}")
def patch_infra_port(port_id: int, body: PortPatch, actor: Actor = Depends(MAINT), db: Session = Depends(get_db)):
    p = get_or_404(db, InfraPort, port_id, "Port")
    data = body.model_dump(exclude_unset=True)
    if data.get("status") in ("reserved", "faulty") and topology.port_effective_status(db, p) == "occupied":
        if data["status"] == "reserved":
            raise ValidationFailed("Port is occupied by a live connection and cannot be reserved")
    if data.get("status") == "reserved" and not (data.get("reserved_for") or p.reserved_for):
        raise ValidationFailed("reserved_for is required when reserving a port")
    for k, v in data.items():
        setattr(p, k, v)
    if data.get("status") == "available":
        p.reserved_for = None
        p.reserved_until = None
    audit(db, actor, "infra_port.update", "infra_port", p.id, f"{p.owner_type} {p.owner_id} port {p.number} updated",
          {k: str(v) for k, v in data.items()})
    db.commit()
    return _port_out(db, p)


# ================================================================== distribution points (ODF/ODB/closure/...)
class DPointIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    point_type: Literal["odf", "odb", "closure", "splice_point", "termination"]
    site_id: int | None = None
    olt_id: int | None = None
    address: str = ""
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    port_count: int = Field(0, ge=0, le=1152)
    status: INFRA_STATUS = "active"
    notes: str = ""


def dpoint_out(db: Session, d: DistributionPoint, detail: bool = False) -> dict:
    out = ser(d, capacity=topology.capacity_summary(db, InfraOwner.DPOINT, d.id))
    if detail:
        out["ports"] = _ports(db, InfraOwner.DPOINT, d.id)
        out["impact"] = impact_svc.analyze(db, InfraOwner.DPOINT, d.id)["summary"]
    return out


@router.get("/distribution-points")
def list_dpoints(point_type: str | None = None, site_id: int | None = None, db: Session = Depends(get_db), _=READ):
    stmt = select(DistributionPoint).order_by(DistributionPoint.name)
    if point_type:
        stmt = stmt.where(DistributionPoint.point_type == point_type)
    if site_id:
        stmt = stmt.where(DistributionPoint.site_id == site_id)
    return [dpoint_out(db, d) for d in db.scalars(stmt)]


@router.post("/distribution-points", status_code=201)
def create_dpoint(body: DPointIn, actor: Actor = Depends(WRITE), db: Session = Depends(get_db)):
    if db.scalar(select(DistributionPoint).where(DistributionPoint.name == body.name)):
        raise Conflict("Name exists")
    d = DistributionPoint(**body.model_dump())
    db.add(d)
    db.flush()
    topology.ensure_ports(db, InfraOwner.DPOINT, d.id, PortDirection.THROUGH, body.port_count)
    audit(db, actor, "dpoint.create", "dpoint", d.id, f"{body.point_type.upper()} {d.name} created")
    db.commit()
    return dpoint_out(db, d, True)


@router.get("/distribution-points/{did}")
def get_dpoint(did: int, db: Session = Depends(get_db), _=READ):
    return dpoint_out(db, get_or_404(db, DistributionPoint, did, "Distribution point"), True)


@router.put("/distribution-points/{did}")
def update_dpoint(did: int, body: DPointIn, actor: Actor = Depends(WRITE), db: Session = Depends(get_db)):
    d = get_or_404(db, DistributionPoint, did, "Distribution point")
    topology.ensure_ports(db, InfraOwner.DPOINT, d.id, PortDirection.THROUGH, body.port_count)
    for k, v in body.model_dump().items():
        setattr(d, k, v)
    audit(db, actor, "dpoint.update", "dpoint", d.id, f"{d.name} updated")
    db.commit()
    return dpoint_out(db, d, True)


@router.delete("/distribution-points/{did}", status_code=204)
def delete_dpoint(did: int, actor: Actor = Depends(WRITE), db: Session = Depends(get_db)):
    d = get_or_404(db, DistributionPoint, did, "Distribution point")
    topology.delete_guard(db, InfraOwner.DPOINT, did)
    topology.delete_ports(db, InfraOwner.DPOINT, did)
    db.delete(d)
    audit(db, actor, "dpoint.delete", "dpoint", did, f"{d.name} deleted")
    db.commit()


# ================================================================== cables & cores
class CableIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    cable_type: Literal["feeder", "distribution", "drop", "backbone", "patch"]
    fiber_count: int = Field(ge=1, le=864)
    parent_cable_id: int | None = None
    site_id: int | None = None
    from_description: str = ""
    to_description: str = ""
    length_m: float | None = Field(None, ge=0)
    route: list[list[float]] | None = None
    manufacturer: str | None = None
    installation_date: date | None = None
    status: INFRA_STATUS = "active"
    notes: str = ""


def cable_out(db: Session, c: Cable, detail: bool = False) -> dict:
    cores = list(db.scalars(select(FiberCore).where(FiberCore.cable_id == c.id).order_by(FiberCore.number)))
    used_by = {l.core_id: l for l in db.scalars(topology.active_links(db).where(FiberLink.cable_id == c.id))}
    counts = {"free": 0, "used": 0, "reserved": 0, "damaged": 0}
    core_rows = []
    for core in cores:
        st = "used" if core.id in used_by else core.status
        counts[st] = counts.get(st, 0) + 1
        if detail:
            core_rows.append(ser(core, effective_status=st,
                                 link=topology.link_summary(db, used_by[core.id]) if core.id in used_by else None))
    out = ser(c, core_usage=counts, utilization_percent=round(100 * counts["used"] / len(cores), 1) if cores else 0)
    if detail:
        out["cores"] = core_rows
        out["segments"] = [ser(s) for s in db.scalars(select(Cable).where(Cable.parent_cable_id == c.id))]
        out["impact"] = impact_svc.analyze(db, "cable", c.id)["summary"]
    return out


def _sync_cores(db: Session, cable: Cable) -> None:
    existing = {c.number: c for c in db.scalars(select(FiberCore).where(FiberCore.cable_id == cable.id))}
    for n in range(1, cable.fiber_count + 1):
        if n not in existing:
            db.add(FiberCore(cable_id=cable.id, number=n, color=FIBER_COLORS[(n - 1) % 12], tube=(n - 1) // 12 + 1))
    for n, core in existing.items():
        if n > cable.fiber_count:
            if db.scalar(topology.active_links(db).where(FiberLink.core_id == core.id)):
                raise ValidationFailed(f"Cannot reduce fiber count: core {n} is in use")
            db.delete(core)


@router.get("/cables")
def list_cables(cable_type: str | None = None, site_id: int | None = None, q: str | None = None, db: Session = Depends(get_db), _=READ):
    stmt = select(Cable).order_by(Cable.name)
    if cable_type:
        stmt = stmt.where(Cable.cable_type == cable_type)
    if site_id:
        stmt = stmt.where(Cable.site_id == site_id)
    if q:
        stmt = stmt.where(Cable.name.ilike(f"%{q}%"))
    return [cable_out(db, c) for c in db.scalars(stmt)]


@router.post("/cables", status_code=201)
def create_cable(body: CableIn, actor: Actor = Depends(WRITE), db: Session = Depends(get_db)):
    if db.scalar(select(Cable).where(Cable.name == body.name)):
        raise Conflict("Cable name exists")
    c = Cable(**body.model_dump())
    db.add(c)
    db.flush()
    _sync_cores(db, c)
    audit(db, actor, "cable.create", "cable", c.id, f"{body.cable_type} cable {c.name} ({body.fiber_count}F) created")
    db.commit()
    return cable_out(db, c, True)


@router.get("/cables/{cid}")
def get_cable(cid: int, db: Session = Depends(get_db), _=READ):
    return cable_out(db, get_or_404(db, Cable, cid, "Cable"), True)


@router.put("/cables/{cid}")
def update_cable(cid: int, body: CableIn, actor: Actor = Depends(WRITE), db: Session = Depends(get_db)):
    c = get_or_404(db, Cable, cid, "Cable")
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    _sync_cores(db, c)
    audit(db, actor, "cable.update", "cable", c.id, f"Cable {c.name} updated")
    db.commit()
    return cable_out(db, c, True)


class CorePatch(BaseModel):
    status: Literal["free", "reserved", "damaged"] | None = None
    notes: str | None = None


@router.patch("/fiber-cores/{core_id}")
def patch_core(core_id: int, body: CorePatch, actor: Actor = Depends(MAINT), db: Session = Depends(get_db)):
    core = get_or_404(db, FiberCore, core_id, "Fiber core")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(core, k, v)
    audit(db, actor, "fiber_core.update", "fiber_core", core.id, f"Core {core.number} of cable {core.cable_id} updated",
          body.model_dump(exclude_unset=True))
    db.commit()
    return ser(core)


# ================================================================== links
class LinkIn(BaseModel):
    a_type: Literal["olt_port", "infra_port"]
    a_id: int
    b_type: Literal["infra_port", "ont"]
    b_id: int
    link_type: Literal["feeder", "distribution", "drop", "patch", "splice"] = "distribution"
    cable_id: int | None = None
    core_id: int | None = None
    verification: Literal["verified", "unverified", "planned"] = "unverified"
    length_m: float | None = Field(None, ge=0)
    loss_db: float | None = Field(None, ge=0, le=50)
    notes: str = ""
    allow_reserved: bool = False


class LinkPatch(BaseModel):
    verification: Literal["verified", "unverified", "planned"] | None = None
    status: Literal["active", "faulty", "planned", "decommissioned"] | None = None
    length_m: float | None = None
    loss_db: float | None = None
    notes: str | None = None


@router.get("/links")
def list_links(endpoint_type: str | None = None, endpoint_id: int | None = None, cable_id: int | None = None,
               verification: str | None = None, include_decommissioned: bool = False, db: Session = Depends(get_db), _=READ):
    stmt = select(FiberLink).order_by(FiberLink.id)
    if not include_decommissioned:
        stmt = stmt.where(FiberLink.status != "decommissioned")
    if endpoint_type and endpoint_id:
        stmt = stmt.where(or_((FiberLink.a_type == endpoint_type) & (FiberLink.a_id == endpoint_id),
                              (FiberLink.b_type == endpoint_type) & (FiberLink.b_id == endpoint_id)))
    if cable_id:
        stmt = stmt.where(FiberLink.cable_id == cable_id)
    if verification:
        stmt = stmt.where(FiberLink.verification == verification)
    return [topology.link_summary(db, l) for l in db.scalars(stmt)]


@router.post("/links/validate")
def validate_link(body: LinkIn, db: Session = Depends(get_db), _=READ):
    try:
        topology.validate_link(db, topology.Endpoint(body.a_type, body.a_id), topology.Endpoint(body.b_type, body.b_id),
                               cable_id=body.cable_id, core_id=body.core_id, allow_reserved=body.allow_reserved)
        return {"valid": True, "errors": []}
    except (ValidationFailed, NotFound) as exc:
        return {"valid": False, "errors": (exc.details or {}).get("errors", [exc.message]) if isinstance(exc.details, dict) else [exc.message]}


@router.post("/links", status_code=201)
def create_link(body: LinkIn, actor: Actor = Depends(WRITE), db: Session = Depends(get_db)):
    a, b = topology.Endpoint(body.a_type, body.a_id), topology.Endpoint(body.b_type, body.b_id)
    topology.validate_link(db, a, b, cable_id=body.cable_id, core_id=body.core_id, allow_reserved=body.allow_reserved)
    data = body.model_dump(exclude={"allow_reserved"})
    link = FiberLink(**data)
    if body.verification == "verified":
        link.verified_at, link.verified_by = utcnow(), actor.username
    if body.verification == "planned":
        link.status = "planned"
    db.add(link)
    if body.core_id:
        db.get(FiberCore, body.core_id).status = "used"
    if b.type == EndpointType.ONT and a.type == EndpointType.INFRA_PORT:
        port = db.get(InfraPort, a.id)
        port.connected_since = port.connected_since or utcnow().date()
        if port.status == "reserved":
            port.status, port.reserved_for = "available", None
    db.flush()
    audit(db, actor, "link.create", "link", link.id,
          f"Connected {topology.describe(db, a)['label']} -> {topology.describe(db, b)['label']} ({body.verification})")
    db.commit()
    return topology.link_summary(db, link)


@router.patch("/links/{link_id}")
def patch_link(link_id: int, body: LinkPatch, actor: Actor = Depends(MAINT), db: Session = Depends(get_db)):
    link = get_or_404(db, FiberLink, link_id, "Link")
    data = body.model_dump(exclude_unset=True)
    if data.get("status") in ("active", "faulty") and link.status == "decommissioned":
        topology.validate_link(db, topology.Endpoint(link.a_type, link.a_id), topology.Endpoint(link.b_type, link.b_id),
                               cable_id=link.cable_id, core_id=link.core_id, link_id=link.id, allow_reserved=True)
    for k, v in data.items():
        setattr(link, k, v)
    if data.get("verification") == "verified":
        link.verified_at, link.verified_by = utcnow(), actor.username
    if data.get("status") == "decommissioned" and link.core_id:
        db.get(FiberCore, link.core_id).status = "free"
    audit(db, actor, "link.update", "link", link.id, f"Link {link.id} updated", data)
    db.commit()
    return topology.link_summary(db, link)


@router.delete("/links/{link_id}", status_code=204)
def delete_link(link_id: int, actor: Actor = Depends(WRITE), db: Session = Depends(get_db)):
    link = get_or_404(db, FiberLink, link_id, "Link")
    summary = topology.link_summary(db, link)
    if link.core_id:
        core = db.get(FiberCore, link.core_id)
        if core and core.status == "used":
            core.status = "free"
    db.delete(link)
    audit(db, actor, "link.delete", "link", link_id, f"Removed {summary['a']['label']} -> {summary['b']['label']}")
    db.commit()


# ================================================================== trace & impact
@router.get("/trace/ont/{ont_id}")
def trace_ont(ont_id: int, db: Session = Depends(get_db), _=READ):
    return topology.trace_ont(db, get_or_404(db, Ont, ont_id, "ONT"))


@router.get("/trace/endpoint")
def trace_endpoint(type: Literal["olt_port", "infra_port", "ont"], id: int, db: Session = Depends(get_db), _=READ):
    return topology.trace_upstream(db, topology.Endpoint(type, id))


ELEMENT = Literal["olt", "olt_port", "board", "splitter", "fat", "dpoint", "cable", "link", "infra_port"]


@router.get("/impact/{element_type}/{element_id}")
def impact(element_type: ELEMENT, element_id: int, db: Session = Depends(get_db), _=READ):
    return impact_svc.analyze(db, element_type, element_id)


class FaultIn(BaseModel):
    element_type: Literal["splitter", "fat", "dpoint", "cable", "link"]
    element_id: int
    faulty: bool = True
    note: str = Field("", max_length=1000)
    create_incident: bool = True


@router.post("/infrastructure/fault")
def mark_fault(body: FaultIn, actor: Actor = Depends(MAINT), db: Session = Depends(get_db)):
    """Mark passive infrastructure faulty (or restore it). Optionally opens an incident with impact analysis.
    Downstream subscribers are reported with evidence levels — they are NOT marked disconnected."""
    model = {"splitter": Splitter, "fat": Fat, "dpoint": DistributionPoint, "cable": Cable, "link": FiberLink}[body.element_type]
    obj = get_or_404(db, model, body.element_id)
    obj.status = "faulty" if body.faulty else "active"
    label = getattr(obj, "name", None) or f"link {obj.id}"
    inc = None
    if body.faulty and body.create_incident:
        inc = upsert_incident(db, source_type=body.element_type, source_id=obj.id, label=label,
                              title=f"Infrastructure fault: {label}", cause=body.note or "Marked faulty by operator",
                              severity="major", olt_id=None, site_id=getattr(obj, "site_id", None), analysis_type=body.element_type,
                              analysis_id=obj.id, simulated=bool(getattr(obj, "is_simulated", False)), detection="manual")
        inc.notes = f"Opened by {actor.username}: {body.note}"
    audit(db, actor, "infrastructure.fault" if body.faulty else "infrastructure.restore", body.element_type, obj.id,
          f"{label} marked {'faulty' if body.faulty else 'active'}", {"note": body.note})
    db.commit()
    return {"element": {"type": body.element_type, "id": obj.id, "status": obj.status},
            "incident_id": inc.id if inc else None, "impact": impact_svc.analyze(db, body.element_type, obj.id)}
