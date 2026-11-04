"""Outage impact analysis: which infrastructure, ONTs and subscribers depend on an element.

Evidence levels per ONT:
* confirmed — the ONT's own status (from the equipment) shows it is down;
* inferred  — it depends on the element, but its status does not confirm an outage;
* unknown   — its status cannot be determined (e.g. the OLT is unreachable).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import topology
from app.core.enums import ONT_DOWN_STATES, EndpointType, ImpactEvidence, OntState
from app.models import Ont, OltBoard, OltPort, Service


def ont_evidence(ont: Ont) -> str:
    if ont.status in ONT_DOWN_STATES:
        return ImpactEvidence.CONFIRMED
    if ont.status in (OntState.UNREACHABLE, OntState.UNKNOWN):
        return ImpactEvidence.UNKNOWN
    return ImpactEvidence.INFERRED


def _logical_onts(db: Session, element_type: str, element_id: int) -> set[int]:
    if element_type == "olt":
        return set(db.scalars(select(Ont.id).where(Ont.olt_id == element_id, Ont.registration_status == "registered")))
    if element_type == "olt_port":
        return set(db.scalars(select(Ont.id).where(Ont.pon_port_id == element_id, Ont.registration_status == "registered")))
    if element_type == "board":
        b = db.get(OltBoard, element_id)
        if not b:
            return set()
        ports = db.scalars(select(OltPort.id).where(OltPort.olt_id == b.olt_id, OltPort.slot == b.slot, OltPort.frame == b.frame))
        return set(db.scalars(select(Ont.id).where(Ont.pon_port_id.in_(list(ports)), Ont.registration_status == "registered")))
    return set()


def analyze(db: Session, element_type: str, element_id: int) -> dict:
    physical_ids: set[int] = set()
    infra: dict[tuple, dict] = {}
    if element_type != "board":
        starts = topology.element_start_endpoints(db, element_type, element_id)
        walk = topology.walk_downstream(db, starts)
        physical_ids = set(walk["ont_ids"])
        for ep in walk["endpoints"]:
            if ep.type == EndpointType.INFRA_PORT:
                d = topology.describe(db, ep)
                key = (d.get("element_type"), d.get("element_id"))
                if key != (element_type, element_id):
                    infra.setdefault(key, {"type": key[0], "id": key[1], "label": d.get("element_label"),
                                           "status": d.get("status")})
        cables = {}
        for link in walk["links"]:
            if link.cable_id and link.cable_id not in cables:
                cables[link.cable_id] = {"type": "cable", "id": link.cable_id, "label": link.cable.name if link.cable else "",
                                         "status": link.cable.status if link.cable else None}
        infra.update({("cable", k): v for k, v in cables.items()})
    logical_ids = _logical_onts(db, element_type, element_id)
    all_ids = physical_ids | logical_ids
    onts = []
    subscribers = {}
    counts = {e.value: 0 for e in ImpactEvidence}
    for ont in db.scalars(select(Ont).where(Ont.id.in_(all_ids))) if all_ids else []:
        ev = ont_evidence(ont)
        counts[ev] += 1
        services = list(db.scalars(select(Service).where(Service.ont_id == ont.id)))
        onts.append({
            "id": ont.id, "serial_number": ont.serial_number, "status": ont.status, "vendor_status": ont.vendor_status,
            "evidence": ev, "dependency": "physical+logical" if ont.id in physical_ids and ont.id in logical_ids
            else "physical" if ont.id in physical_ids else "logical",
            "olt": ont.olt.name if ont.olt else None, "pon_port": ont.pon_port.fsp if ont.pon_port else None,
            "services": [s.service_ref for s in services],
        })
        for s in services:
            subscribers[s.subscriber_id] = {"id": s.subscriber_id, "customer_ref": s.subscriber.customer_ref,
                                            "display_name": s.subscriber.display_name, "service_ref": s.service_ref,
                                            "service_status": s.status, "evidence": ev}
    onts.sort(key=lambda o: (o["evidence"] != "confirmed", o["serial_number"]))
    return {
        "element": {"type": element_type, "id": element_id},
        "infrastructure": sorted(infra.values(), key=lambda x: (x["type"], str(x["label"]))),
        "onts": onts,
        "subscribers": list(subscribers.values()),
        "summary": {"onts": len(onts), "confirmed_down": counts["confirmed"], "inferred": counts["inferred"],
                    "unknown": counts["unknown"], "subscribers": len(subscribers),
                    "physical_onts": len(physical_ids), "logical_onts": len(logical_ids)},
    }
