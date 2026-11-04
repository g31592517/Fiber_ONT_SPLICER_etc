"""Topology graph (schematic) and geographic map data. Both are computed from the same link model."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import topology
from app.core.enums import EndpointType
from app.db import get_db
from app.models import (
    Cable,
    DistributionPoint,
    Fat,
    FiberLink,
    InfraPort,
    Olt,
    OltPort,
    Ont,
    Service,
    Site,
    Splitter,
    Subscriber,
)
from app.security.auth import require

router = APIRouter(prefix="/topology", tags=["topology"])
READ = Depends(require("network.read"))


def _node_key(db: Session, ep_type: str, ep_id: int, cache: dict) -> str | None:
    k = (ep_type, ep_id)
    if k in cache:
        return cache[k]
    if ep_type == EndpointType.OLT_PORT:
        key = f"pon:{ep_id}"
    elif ep_type == EndpointType.ONT:
        key = f"ont:{ep_id}"
    else:
        p = db.get(InfraPort, ep_id)
        key = f"{p.owner_type}:{p.owner_id}" if p else None
    cache[k] = key
    return key


def build_graph(db: Session, *, site_id=None, olt_id=None, pon_port_id=None, fat_id=None, splitter_id=None, status=None,
                q=None) -> dict:
    links = list(db.scalars(select(FiberLink).where(FiberLink.status != "decommissioned")))
    cache: dict = {}
    edges: dict[tuple, dict] = {}
    for l in links:
        a = _node_key(db, l.a_type, l.a_id, cache)
        b = _node_key(db, l.b_type, l.b_id, cache)
        if not a or not b or a == b:
            continue
        e = edges.setdefault((a, b), {"id": f"{a}->{b}", "source": a, "target": b, "links": [], "kind": "physical"})
        e["links"].append({"id": l.id, "verification": l.verification, "status": l.status, "link_type": l.link_type,
                           "cable": l.cable.name if l.cable else None, "core": l.core.number if l.core else None})
    # logical association edges for ONTs that have no recorded physical path
    onts = list(db.scalars(select(Ont).where(Ont.registration_status == "registered")))
    physically_connected = {e[1] for e in edges if e[1].startswith("ont:")}
    for o in onts:
        if o.pon_port_id and f"ont:{o.id}" not in physically_connected:
            edges[(f"pon:{o.pon_port_id}", f"ont:{o.id}")] = {"id": f"pon:{o.pon_port_id}->ont:{o.id}", "source": f"pon:{o.pon_port_id}",
                                                              "target": f"ont:{o.id}", "links": [], "kind": "logical"}
    # OLT -> PON containment edges
    ports = list(db.scalars(select(OltPort).where(OltPort.port_type == "pon")))
    used_ports = {e[0] for e in edges if e[0].startswith("pon:")}
    for p in ports:
        if f"pon:{p.id}" in used_ports:
            edges[(f"olt:{p.olt_id}", f"pon:{p.id}")] = {"id": f"olt:{p.olt_id}->pon:{p.id}", "source": f"olt:{p.olt_id}",
                                                       "target": f"pon:{p.id}", "links": [], "kind": "contains"}
    for e in edges.values():
        vs = {l["verification"] for l in e["links"]}
        e["verification"] = "verified" if vs == {"verified"} else ("planned" if "planned" in vs else "unverified") if vs else None
        e["faulty"] = any(l["status"] == "faulty" for l in e["links"])

    # nodes
    nodes: dict[str, dict] = {}
    services = {s.ont_id: s for s in db.scalars(select(Service).where(Service.ont_id.is_not(None)).order_by(Service.id))}
    for o in db.scalars(select(Olt)):
        nodes[f"olt:{o.id}"] = {"id": f"olt:{o.id}", "type": "olt", "label": o.name, "status": o.connection_status,
                                "site_id": o.site_id, "olt_id": o.id, "ref_id": o.id, "simulated": o.is_simulated}
    for p in ports:
        nodes[f"pon:{p.id}"] = {"id": f"pon:{p.id}", "type": "pon_port", "label": f"PON {p.fsp}", "status": p.oper_status,
                                "olt_id": p.olt_id, "site_id": p.olt.site_id if p.olt else None, "ref_id": p.id}
    for s in db.scalars(select(Splitter)):
        nodes[f"splitter:{s.id}"] = {"id": f"splitter:{s.id}", "type": "splitter", "label": f"{s.name} ({s.splitter_type.name})",
                                     "status": s.status, "site_id": s.site_id, "ref_id": s.id, "fat_id": s.fat_id}
    for f in db.scalars(select(Fat)):
        cap = topology.capacity_summary(db, "fat", f.id)
        nodes[f"fat:{f.id}"] = {"id": f"fat:{f.id}", "type": "fat", "label": f.name, "status": f.status, "site_id": f.site_id,
                                "ref_id": f.id, "capacity": cap}
    for d in db.scalars(select(DistributionPoint)):
        nodes[f"dpoint:{d.id}"] = {"id": f"dpoint:{d.id}", "type": d.point_type, "label": d.name, "status": d.status,
                                   "site_id": d.site_id, "ref_id": d.id}
    for o in onts:
        svc = services.get(o.id)
        nodes[f"ont:{o.id}"] = {"id": f"ont:{o.id}", "type": "ont", "label": o.serial_number, "status": o.status,
                                "optical_status": o.optical_status, "rx_power_dbm": o.rx_power_dbm, "olt_id": o.olt_id,
                                "site_id": o.site_id, "ref_id": o.id,
                                "subscriber": svc.subscriber.display_name if svc else None,
                                "customer_ref": svc.subscriber.customer_ref if svc else None}

    edge_list = list(edges.values())
    keep: set[str] | None = None

    def downstream(start: set[str]) -> set[str]:
        out, stack = set(start), list(start)
        children: dict[str, list[str]] = {}
        for e in edge_list:
            children.setdefault(e["source"], []).append(e["target"])
        while stack:
            n = stack.pop()
            for c in children.get(n, []):
                if c not in out:
                    out.add(c)
                    stack.append(c)
        return out

    def upstream(start: set[str]) -> set[str]:
        out, stack = set(start), list(start)
        parents: dict[str, list[str]] = {}
        for e in edge_list:
            parents.setdefault(e["target"], []).append(e["source"])
        while stack:
            n = stack.pop()
            for c in parents.get(n, []):
                if c not in out:
                    out.add(c)
                    stack.append(c)
        return out

    def restrict(s: set[str]):
        nonlocal keep
        keep = s if keep is None else keep & s

    if site_id:
        olts = {k for k, n in nodes.items() if n["type"] == "olt" and n.get("site_id") == site_id}
        restrict(downstream(olts) | {k for k, n in nodes.items() if n.get("site_id") == site_id})
    if olt_id:
        restrict(downstream({f"olt:{olt_id}"}))
    if pon_port_id:
        restrict(downstream({f"pon:{pon_port_id}"}) | upstream({f"pon:{pon_port_id}"}))
    for key in ([f"fat:{fat_id}"] if fat_id else []) + ([f"splitter:{splitter_id}"] if splitter_id else []):
        restrict(downstream({key}) | upstream({key}))
    if q:
        ql = q.lower()
        hits = {k for k, n in nodes.items() if ql in str(n.get("label", "")).lower() or ql in str(n.get("subscriber") or "").lower()
                or ql in str(n.get("customer_ref") or "").lower()}
        restrict(upstream(hits) | downstream(hits))
    if status:
        wanted = set(status.split(","))
        hits = {k for k, n in nodes.items() if n["type"] == "ont" and n["status"] in wanted}
        restrict(upstream(hits))
    if keep is not None:
        nodes = {k: v for k, v in nodes.items() if k in keep}
        edge_list = [e for e in edge_list if e["source"] in keep and e["target"] in keep]
    return {"nodes": list(nodes.values()), "edges": edge_list,
            "legend": {"physical": "Recorded fiber connection", "logical": "ONT registered on PON port; physical path not recorded",
                       "contains": "Port belongs to OLT"}}


@router.get("/graph")
def graph(site_id: int | None = None, olt_id: int | None = None, pon_port_id: int | None = None, fat_id: int | None = None,
          splitter_id: int | None = None, status: str | None = None, q: str | None = Query(None, max_length=64),
          db: Session = Depends(get_db), _=READ):
    return build_graph(db, site_id=site_id, olt_id=olt_id, pon_port_id=pon_port_id, fat_id=fat_id, splitter_id=splitter_id,
                       status=status, q=q)


@router.get("/map")
def map_data(site_id: int | None = None, db: Session = Depends(get_db), _=READ):
    s = get_settings()
    feats: list[dict] = []
    coords: dict[str, tuple[float, float]] = {}

    def add(kind, obj, label, status, extra=None):
        if obj.latitude is None or obj.longitude is None:
            return
        if site_id and getattr(obj, "site_id", site_id) not in (site_id, None) and kind != "site":
            return
        key = f"{kind}:{obj.id}"
        coords[key] = (obj.latitude, obj.longitude)
        feats.append({"id": key, "kind": kind, "ref_id": obj.id, "label": label, "status": status, "lat": obj.latitude,
                      "lng": obj.longitude, **(extra or {})})

    for x in db.scalars(select(Site)):
        add("site", x, x.name, None)
    for x in db.scalars(select(Olt)):
        add("olt", x, x.name, x.connection_status)
    for x in db.scalars(select(Splitter)):
        add("splitter", x, f"{x.name} ({x.splitter_type.name})", x.status)
    for x in db.scalars(select(Fat)):
        cap = topology.capacity_summary(db, "fat", x.id)
        add("fat", x, x.name, x.status, {"utilization_percent": cap["utilization_percent"], "available": cap["available"]})
    for x in db.scalars(select(DistributionPoint)):
        add("dpoint", x, x.name, x.status, {"point_type": x.point_type})
    for svc in db.scalars(select(Service).where(Service.ont_id.is_not(None))):
        sub = svc.subscriber
        if sub.latitude is not None and svc.ont:
            key = f"ont:{svc.ont_id}"
            coords[key] = (sub.latitude, sub.longitude)
            feats.append({"id": key, "kind": "ont", "ref_id": svc.ont_id, "label": f"{svc.ont.serial_number} — {sub.display_name}",
                          "status": svc.ont.status, "lat": sub.latitude, "lng": sub.longitude})
    lines = []
    for c in db.scalars(select(Cable).where(Cable.route.is_not(None))):
        lines.append({"id": f"cable:{c.id}", "kind": "cable", "label": c.name, "cable_type": c.cable_type, "status": c.status,
                      "path": c.route, "estimated": False})
    cache: dict = {}
    for l in db.scalars(select(FiberLink).where(FiberLink.status != "decommissioned", or_(FiberLink.cable_id.is_(None),
                                                                                          FiberLink.cable_id.is_not(None)))):
        a, b = _node_key(db, l.a_type, l.a_id, cache), _node_key(db, l.b_type, l.b_id, cache)
        if a and a.startswith("pon:"):
            p = db.get(OltPort, l.a_id)
            a = f"olt:{p.olt_id}" if p else a
        if a in coords and b in coords and a != b:
            cable = db.get(Cable, l.cable_id) if l.cable_id else None
            if cable and cable.route:
                continue
            lines.append({"id": f"link:{l.id}", "kind": "link", "label": l.link_type, "status": l.status,
                          "verification": l.verification, "path": [list(coords[a]), list(coords[b])], "estimated": True})
    return {"tile_url": s.map_tile_url or None, "attribution": s.map_attribution, "features": feats, "lines": lines,
            "note": "Straight lines between elements are schematic (estimated); only cables with a recorded route show real paths."}


@router.get("/search")
def search(q: str = Query(min_length=2, max_length=64), db: Session = Depends(get_db), _=READ):
    like = f"%{q}%"
    out = []
    for o in db.scalars(select(Olt).where(or_(Olt.name.ilike(like), Olt.host.ilike(like))).limit(10)):
        out.append({"type": "olt", "id": o.id, "label": o.name, "sub": o.host})
    for o in db.scalars(select(Ont).where(Ont.serial_number.ilike(like)).limit(10)):
        out.append({"type": "ont", "id": o.id, "label": o.serial_number, "sub": o.status})
    for f in db.scalars(select(Fat).where(or_(Fat.name.ilike(like), Fat.code.ilike(like))).limit(10)):
        out.append({"type": "fat", "id": f.id, "label": f.name, "sub": f.address})
    for s in db.scalars(select(Splitter).where(Splitter.name.ilike(like)).limit(10)):
        out.append({"type": "splitter", "id": s.id, "label": s.name, "sub": s.splitter_type.name})
    for c in db.scalars(select(Cable).where(Cable.name.ilike(like)).limit(10)):
        out.append({"type": "cable", "id": c.id, "label": c.name, "sub": c.cable_type})
    for s in db.scalars(select(Subscriber).where(or_(Subscriber.display_name.ilike(like), Subscriber.customer_ref.ilike(like))).limit(10)):
        out.append({"type": "subscriber", "id": s.id, "label": s.display_name, "sub": s.customer_ref})
    return out
