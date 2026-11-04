"""Physical topology engine: link validation, path tracing and downstream impact.

Connectivity model (see DATA_MODEL.md):
    endpoints  = olt_port | infra_port | ont
    links      = directed a (upstream) -> b (downstream)
    traversal  = splitter "in" port fans out to all of the splitter's "out" ports;
                 "through" ports (FAT/ODF/closure) pass straight through.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.enums import EndpointType, InfraOwner, PortDirection, PortStatus
from app.core.errors import NotFound, ValidationFailed
from app.models import DistributionPoint, Fat, FiberCore, FiberLink, InfraPort, Olt, OltPort, Ont, Splitter

INACTIVE_LINK = ("decommissioned",)


# ------------------------------------------------------------------------------- endpoints
@dataclass(frozen=True)
class Endpoint:
    type: str
    id: int

    def __str__(self):
        return f"{self.type}:{self.id}"


def _owner_obj(db: Session, owner_type: str, owner_id: int):
    model = {InfraOwner.SPLITTER: Splitter, InfraOwner.FAT: Fat, InfraOwner.DPOINT: DistributionPoint}.get(owner_type)
    return db.get(model, owner_id) if model else None


def describe(db: Session, ep: Endpoint) -> dict:
    """Human-readable description of an endpoint, including its owning element."""
    if ep.type == EndpointType.OLT_PORT:
        p = db.get(OltPort, ep.id)
        if not p:
            return {"type": ep.type, "id": ep.id, "label": f"missing olt_port {ep.id}"}
        return {"type": ep.type, "id": ep.id, "label": f"{p.olt.name} PON {p.fsp}", "element_type": "olt",
                "element_id": p.olt_id, "element_label": p.olt.name, "status": p.oper_status}
    if ep.type == EndpointType.ONT:
        o = db.get(Ont, ep.id)
        if not o:
            return {"type": ep.type, "id": ep.id, "label": f"missing ont {ep.id}"}
        return {"type": ep.type, "id": ep.id, "label": f"ONT {o.serial_number}", "element_type": "ont",
                "element_id": o.id, "element_label": o.serial_number, "status": o.status}
    p = db.get(InfraPort, ep.id)
    if not p:
        return {"type": ep.type, "id": ep.id, "label": f"missing infra_port {ep.id}"}
    owner = _owner_obj(db, p.owner_type, p.owner_id)
    oname = owner.name if owner else f"{p.owner_type} {p.owner_id}"
    plabel = {"in": "IN", "out": "OUT"}.get(p.direction, "P")
    return {"type": ep.type, "id": ep.id, "label": f"{oname} {plabel}{p.number}", "element_type": p.owner_type,
            "element_id": p.owner_id, "element_label": oname, "port_number": p.number, "direction": p.direction,
            "status": getattr(owner, "status", None)}


def _require_endpoint(db: Session, ep: Endpoint):
    model = {EndpointType.OLT_PORT: OltPort, EndpointType.INFRA_PORT: InfraPort, EndpointType.ONT: Ont}.get(ep.type)
    if model is None:
        raise ValidationFailed(f"Unknown endpoint type '{ep.type}'")
    obj = db.get(model, ep.id)
    if obj is None:
        raise NotFound(f"{ep.type} {ep.id} not found")
    return obj


def active_links(db: Session):
    return select(FiberLink).where(FiberLink.status.notin_(INACTIVE_LINK))


def link_from(db: Session, ep: Endpoint, exclude_id: int | None = None) -> list[FiberLink]:
    q = active_links(db).where(FiberLink.a_type == ep.type, FiberLink.a_id == ep.id)
    if exclude_id:
        q = q.where(FiberLink.id != exclude_id)
    return list(db.scalars(q))


def link_into(db: Session, ep: Endpoint, exclude_id: int | None = None) -> FiberLink | None:
    q = active_links(db).where(FiberLink.b_type == ep.type, FiberLink.b_id == ep.id)
    if exclude_id:
        q = q.where(FiberLink.id != exclude_id)
    return db.scalars(q).first()


# ------------------------------------------------------------------------------- validation
def validate_link(db: Session, a: Endpoint, b: Endpoint, *, cable_id: int | None = None, core_id: int | None = None,
                  link_id: int | None = None, allow_reserved: bool = False) -> None:
    errors: list[str] = []
    a_obj = _require_endpoint(db, a)
    b_obj = _require_endpoint(db, b)
    if a == b:
        raise ValidationFailed("A link cannot connect an endpoint to itself")

    # direction rules
    if a.type == EndpointType.ONT:
        errors.append("An ONT is a terminal device and cannot feed downstream infrastructure")
    if a.type == EndpointType.OLT_PORT and a_obj.port_type != "pon":
        errors.append("Only PON ports can feed the optical distribution network")
    if a.type == EndpointType.INFRA_PORT and a_obj.direction == PortDirection.IN:
        errors.append("A splitter input port cannot be the upstream end of a link")
    if b.type == EndpointType.OLT_PORT:
        errors.append("An OLT PON port cannot be fed from downstream infrastructure")
    if b.type == EndpointType.INFRA_PORT and b_obj.direction == PortDirection.OUT:
        errors.append("A splitter output port cannot be the downstream end of a link")

    # port status rules
    for ep, obj in ((a, a_obj), (b, b_obj)):
        if ep.type == EndpointType.INFRA_PORT:
            if obj.status == PortStatus.FAULTY:
                errors.append(f"{describe(db, ep)['label']} is marked faulty")
            if obj.status == PortStatus.RESERVED and not allow_reserved:
                errors.append(f"{describe(db, ep)['label']} is reserved ({obj.reserved_for or 'no reason given'}); "
                              "confirm use of a reserved port explicitly")

    # occupancy: one downstream link per upstream port, one upstream feed per downstream port
    if not errors:
        if link_from(db, a, exclude_id=link_id):
            errors.append(f"{describe(db, a)['label']} already feeds another live connection")
        if link_into(db, b, exclude_id=link_id):
            errors.append(f"{describe(db, b)['label']} is already fed by another live connection")
        if b.type == EndpointType.ONT and a.type == EndpointType.OLT_PORT:
            errors.append("An ONT cannot be connected directly to an OLT port without passive distribution in between")

    # cable / core
    if core_id is not None:
        core = db.get(FiberCore, core_id)
        if core is None:
            errors.append(f"Fiber core {core_id} not found")
        else:
            if cable_id is not None and core.cable_id != cable_id:
                errors.append("Fiber core does not belong to the selected cable")
            if core.status == "damaged":
                errors.append(f"Fiber core {core.number} is marked damaged")
            used = db.scalars(active_links(db).where(FiberLink.core_id == core_id, FiberLink.id != (link_id or 0))).first()
            if used:
                errors.append(f"Fiber core {core.number} is already used by link {used.id}")

    # cycles: b's element must not be upstream of a
    if not errors:
        upstream_elements = {(h["element_type"], h["element_id"]) for h in trace_upstream(db, a)["hops"] if h.get("element_type")}
        bd = describe(db, b)
        if (bd.get("element_type"), bd.get("element_id")) in upstream_elements:
            errors.append("This connection would create a loop in the topology")

    if errors:
        raise ValidationFailed("Invalid connection", {"errors": errors})


# ------------------------------------------------------------------------------- traversal
def _emitting_ports(db: Session, ep: Endpoint) -> list[Endpoint]:
    """Given an endpoint that light has reached, which endpoints carry it further downstream?"""
    if ep.type == EndpointType.OLT_PORT:
        return [ep]
    if ep.type == EndpointType.ONT:
        return []
    p = db.get(InfraPort, ep.id)
    if p is None:
        return []
    if p.direction == PortDirection.IN:
        outs = db.scalars(select(InfraPort.id).where(InfraPort.owner_type == p.owner_type, InfraPort.owner_id == p.owner_id,
                                                     InfraPort.direction == PortDirection.OUT).order_by(InfraPort.number))
        return [Endpoint(EndpointType.INFRA_PORT, i) for i in outs]
    return [ep]


def walk_downstream(db: Session, starts: Iterable[Endpoint], max_nodes: int = 20000) -> dict:
    """Breadth-first walk. Returns endpoints reached, links traversed and ONT ids reached."""
    seen: set[Endpoint] = set()
    links: list[FiberLink] = []
    onts: list[int] = []
    queue = list(starts)
    while queue and len(seen) < max_nodes:
        ep = queue.pop(0)
        if ep in seen:
            continue
        seen.add(ep)
        if ep.type == EndpointType.ONT:
            onts.append(ep.id)
            continue
        for emit in _emitting_ports(db, ep):
            if emit != ep:
                seen.add(emit)
            for link in link_from(db, emit):
                links.append(link)
                queue.append(Endpoint(link.b_type, link.b_id))
    return {"endpoints": seen, "links": links, "ont_ids": onts}


def element_start_endpoints(db: Session, element_type: str, element_id: int) -> list[Endpoint]:
    if element_type == "olt":
        ids = db.scalars(select(OltPort.id).where(OltPort.olt_id == element_id, OltPort.port_type == "pon"))
        return [Endpoint(EndpointType.OLT_PORT, i) for i in ids]
    if element_type == "olt_port":
        return [Endpoint(EndpointType.OLT_PORT, element_id)]
    if element_type in (InfraOwner.SPLITTER, InfraOwner.FAT, InfraOwner.DPOINT):
        ids = db.scalars(select(InfraPort.id).where(InfraPort.owner_type == element_type, InfraPort.owner_id == element_id,
                                                    InfraPort.direction != PortDirection.OUT))
        return [Endpoint(EndpointType.INFRA_PORT, i) for i in ids]
    if element_type == "infra_port":
        return [Endpoint(EndpointType.INFRA_PORT, element_id)]
    if element_type == "link":
        link = db.get(FiberLink, element_id)
        return [Endpoint(link.b_type, link.b_id)] if link else []
    if element_type == "cable":
        links = db.scalars(active_links(db).where(FiberLink.cable_id == element_id))
        return [Endpoint(l.b_type, l.b_id) for l in links]
    if element_type == "ont":
        return [Endpoint(EndpointType.ONT, element_id)]
    raise ValidationFailed(f"Unsupported element type '{element_type}'")


def trace_upstream(db: Session, start: Endpoint, max_hops: int = 200) -> dict:
    """Trace from an endpoint towards the OLT. Returns ordered hops (start first) and links."""
    hops = [describe(db, start)]
    links_out = []
    ep = start
    verified = True
    reached_olt = start.type == EndpointType.OLT_PORT
    for _ in range(max_hops):
        if ep.type == EndpointType.INFRA_PORT:
            p = db.get(InfraPort, ep.id)
            if p is not None and p.direction == PortDirection.OUT:
                # jump through the splitter to its input
                in_ports = list(db.scalars(select(InfraPort).where(
                    InfraPort.owner_type == p.owner_type, InfraPort.owner_id == p.owner_id,
                    InfraPort.direction == PortDirection.IN).order_by(InfraPort.number)))
                fed = next((ip for ip in in_ports if link_into(db, Endpoint(EndpointType.INFRA_PORT, ip.id))), None)
                if fed is None:
                    break
                ep = Endpoint(EndpointType.INFRA_PORT, fed.id)
                hops.append(describe(db, ep))
        link = link_into(db, ep)
        if link is None:
            break
        links_out.append(link_summary(db, link))
        verified = verified and link.verification == "verified"
        ep = Endpoint(link.a_type, link.a_id)
        hops.append(describe(db, ep))
        if ep.type == EndpointType.OLT_PORT:
            reached_olt = True
            break
    return {"hops": hops, "links": links_out, "complete": reached_olt, "all_verified": verified and reached_olt and bool(links_out)}


def link_summary(db: Session, link: FiberLink) -> dict:
    return {
        "id": link.id, "link_type": link.link_type, "verification": link.verification, "status": link.status,
        "cable": link.cable.name if link.cable else None, "cable_id": link.cable_id,
        "core": link.core.number if link.core else None, "length_m": link.length_m, "loss_db": link.loss_db,
        "a": describe(db, Endpoint(link.a_type, link.a_id)), "b": describe(db, Endpoint(link.b_type, link.b_id)),
    }


def trace_ont(db: Session, ont: Ont) -> dict:
    """Full path for an ONT: physical trace plus logical (registration) association."""
    phys = trace_upstream(db, Endpoint(EndpointType.ONT, ont.id))
    logical = None
    if ont.pon_port_id:
        logical = {"olt_id": ont.olt_id, "olt": ont.olt.name if ont.olt else None, "pon_port_id": ont.pon_port_id,
                   "pon_port": ont.pon_port.fsp if ont.pon_port else None, "ont_index": ont.ont_index}
    warnings = []
    phys_port = next((h for h in phys["hops"] if h["type"] == EndpointType.OLT_PORT), None)
    if phys["complete"] and logical and phys_port and phys_port["id"] != ont.pon_port_id:
        warnings.append("Physical trace reaches a different PON port than the ONT's registration — records disagree")
    if not phys["complete"]:
        warnings.append("Physical path is incomplete: not all connections from this ONT to the OLT are recorded")
    elif not phys["all_verified"]:
        warnings.append("Path contains planned or unverified connections")
    return {"physical": phys, "logical": logical, "warnings": warnings}


def fat_for_ont(db: Session, ont_id: int) -> dict | None:
    link = link_into(db, Endpoint(EndpointType.ONT, ont_id))
    if link and link.a_type == EndpointType.INFRA_PORT:
        p = db.get(InfraPort, link.a_id)
        if p and p.owner_type == InfraOwner.FAT:
            fat = db.get(Fat, p.owner_id)
            return {"fat_id": fat.id, "fat_name": fat.name, "fat_port_id": p.id, "fat_port_number": p.number}
    return None


# ------------------------------------------------------------------------------- ports & capacity
def port_effective_status(db: Session, port: InfraPort) -> str:
    if port.status in (PortStatus.FAULTY, PortStatus.RESERVED) and not link_from(db, Endpoint(EndpointType.INFRA_PORT, port.id)):
        return port.status
    if port.direction == PortDirection.IN:
        return PortStatus.OCCUPIED if link_into(db, Endpoint(EndpointType.INFRA_PORT, port.id)) else port.status
    if link_from(db, Endpoint(EndpointType.INFRA_PORT, port.id)):
        return PortStatus.OCCUPIED
    return port.status


def ensure_ports(db: Session, owner_type: str, owner_id: int, direction: str, count: int, start: int = 1) -> None:
    """Create missing ports 1..count; refuse to shrink below an occupied port."""
    existing = {p.number: p for p in db.scalars(select(InfraPort).where(
        InfraPort.owner_type == owner_type, InfraPort.owner_id == owner_id, InfraPort.direction == direction))}
    for n in range(start, start + count):
        if n not in existing:
            db.add(InfraPort(owner_type=owner_type, owner_id=owner_id, direction=direction, number=n))
    for n, p in existing.items():
        if n >= start + count:
            ep = Endpoint(EndpointType.INFRA_PORT, p.id)
            if link_from(db, ep) or link_into(db, ep):
                raise ValidationFailed(f"Cannot reduce capacity: port {n} is in use")
            db.delete(p)


def capacity_summary(db: Session, owner_type: str, owner_id: int, direction: str | None = None) -> dict:
    q = select(InfraPort).where(InfraPort.owner_type == owner_type, InfraPort.owner_id == owner_id)
    if direction:
        q = q.where(InfraPort.direction == direction)
    ports = list(db.scalars(q))
    counts = {s.value: 0 for s in PortStatus}
    for p in ports:
        counts[port_effective_status(db, p)] += 1
    total = len(ports)
    usable = total - counts["faulty"]
    return {"total": total, **counts,
            "utilization_percent": round(100 * counts["occupied"] / total, 1) if total else 0.0,
            "usable": usable}


def delete_guard(db: Session, owner_type: str, owner_id: int) -> None:
    ports = list(db.scalars(select(InfraPort.id).where(InfraPort.owner_type == owner_type, InfraPort.owner_id == owner_id)))
    if not ports:
        return
    used = db.scalars(active_links(db).where(or_(
        (FiberLink.a_type == EndpointType.INFRA_PORT) & FiberLink.a_id.in_(ports),
        (FiberLink.b_type == EndpointType.INFRA_PORT) & FiberLink.b_id.in_(ports)))).first()
    if used:
        raise ValidationFailed("Element has live connections; remove or decommission them first")


def delete_ports(db: Session, owner_type: str, owner_id: int) -> None:
    for p in db.scalars(select(InfraPort).where(InfraPort.owner_type == owner_type, InfraPort.owner_id == owner_id)):
        db.delete(p)


@dataclass
class Impact:
    infrastructure: list[dict] = field(default_factory=list)
    onts: list[dict] = field(default_factory=list)
    subscribers: list[dict] = field(default_factory=list)


def olt_of_port(db: Session, port_id: int) -> Olt | None:
    p = db.get(OltPort, port_id)
    return p.olt if p else None
