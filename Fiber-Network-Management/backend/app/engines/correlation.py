"""Outage correlation: turns device conditions into network incidents with impact analysis.

Automatic rules (per OLT, evaluated after every poll):
  1. OLT unreachable                      -> incident on the OLT (ONT impact evidence = unknown)
  2. PON port fault/down with ONTs        -> incident on the port
  3. Board failed                         -> incident on the board
  4. >= 2 ONTs in LOS on a healthy port   -> incident on the nearest common upstream passive element
                                             (splitter / FAT / closure) if topology allows, else the port.
Incidents are resolved automatically when their triggering condition clears. Impacts are
always split into confirmed / inferred / unknown — nothing is marked down without evidence.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import impact as impact_svc
from app.core import topology
from app.core.enums import OPEN_ALARM_STATES, ConnectionStatus, EndpointType, IncidentStatus, OntState
from app.db import utcnow
from app.models import Alarm, Incident, OltBoard, OltPort, Ont

PASSIVE = {"splitter", "fat", "dpoint"}
CLUSTER_MIN_LOS = 2
CLUSTER_RATIO = 0.5


def _open_incident(db: Session, source_type: str, source_id: int) -> Incident | None:
    return db.scalars(select(Incident).where(Incident.source_type == source_type, Incident.source_id == source_id,
                                             Incident.status != IncidentStatus.RESOLVED)).first()


def _apply_impact(db: Session, inc: Incident, analysis: dict) -> None:
    s = analysis["summary"]
    inc.impact = analysis
    inc.affected_onts = s["onts"]
    inc.confirmed_down_onts = s["confirmed_down"]
    inc.affected_subscribers = s["subscribers"]
    ont_ids = [o["id"] for o in analysis["onts"]]
    if ont_ids:
        for a in db.scalars(select(Alarm).where(Alarm.source_type == "ont", Alarm.source_id.in_(ont_ids),
                                                Alarm.state.in_(list(OPEN_ALARM_STATES)), Alarm.incident_id.is_(None))):
            a.incident_id = inc.id


def upsert_incident(db: Session, *, source_type: str, source_id: int, label: str, title: str, cause: str, severity: str,
                    olt_id: int | None, site_id: int | None, analysis_type: str, analysis_id: int, simulated: bool,
                    detection: str = "automatic") -> Incident:
    inc = _open_incident(db, source_type, source_id)
    if inc is None:
        inc = Incident(title=title, source_type=source_type, source_id=source_id, source_label=label, suspected_cause=cause,
                       severity=severity, olt_id=olt_id, site_id=site_id, detection=detection, is_simulated=simulated)
        db.add(inc)
        db.flush()
    else:
        inc.suspected_cause = cause
    _apply_impact(db, inc, impact_svc.analyze(db, analysis_type, analysis_id))
    for a in db.scalars(select(Alarm).where(Alarm.olt_id == olt_id, Alarm.source_type == source_type,
                                            Alarm.source_id == source_id, Alarm.state.in_(list(OPEN_ALARM_STATES)))):
        a.incident_id = inc.id
    return inc


def _common_upstream(db: Session, onts: list[Ont]) -> tuple[str, int, str] | None:
    paths = []
    for o in onts:
        hops = topology.trace_upstream(db, topology.Endpoint(EndpointType.ONT, o.id))["hops"][1:]
        elems = []
        for h in hops:
            key = (h.get("element_type"), h.get("element_id"), h.get("element_label"))
            if key[0] in PASSIVE and key not in elems:
                elems.append(key)
        if not elems:
            return None
        paths.append(elems)
    first = paths[0]
    for elem in first:  # nearest-to-ONT first
        if all(elem in p for p in paths[1:]):
            return elem
    return None


def correlate_olt(db: Session, olt) -> list[int]:
    touched: set[int] = set()
    sim = olt.is_simulated
    # 1. OLT unreachable
    if olt.connection_status == ConnectionStatus.UNREACHABLE:
        inc = upsert_incident(db, source_type="olt", source_id=olt.id, label=olt.name, title=f"OLT {olt.name} unreachable",
                              cause="Management communication with the OLT failed. ONT states are unknown until it recovers; "
                                    "this does not by itself prove a subscriber outage.",
                              severity="critical", olt_id=olt.id, site_id=olt.site_id, analysis_type="olt",
                              analysis_id=olt.id, simulated=sim)
        touched.add(inc.id)
    else:
        # 2. PON ports
        for p in db.scalars(select(OltPort).where(OltPort.olt_id == olt.id, OltPort.port_type == "pon")):
            has_onts = db.scalar(select(Ont.id).where(Ont.pon_port_id == p.id, Ont.registration_status == "registered").limit(1))
            if p.admin_status == "enabled" and p.oper_status in ("fault", "down") and has_onts:
                inc = upsert_incident(db, source_type="olt_port", source_id=p.id, label=f"{olt.name} PON {p.fsp}",
                                      title=f"PON port {p.fsp} on {olt.name} {p.oper_status}",
                                      cause=f"OLT reports PON port {p.oper_status} ({p.vendor_status or 'no vendor detail'}).",
                                      severity="critical", olt_id=olt.id, site_id=olt.site_id, analysis_type="olt_port",
                                      analysis_id=p.id, simulated=sim)
                touched.add(inc.id)
        # 3. boards
        for b in db.scalars(select(OltBoard).where(OltBoard.olt_id == olt.id, OltBoard.status == "failed")):
            inc = upsert_incident(db, source_type="board", source_id=b.id, label=f"{olt.name} slot {b.frame}/{b.slot}",
                                  title=f"Board failure in {olt.name} slot {b.slot}",
                                  cause=f"OLT reports board {b.board_type or ''} in slot {b.slot} as {b.vendor_status or 'failed'}.",
                                  severity="critical", olt_id=olt.id, site_id=olt.site_id, analysis_type="board",
                                  analysis_id=b.id, simulated=sim)
            touched.add(inc.id)
        # 4. LOS clusters on healthy ports
        for p in db.scalars(select(OltPort).where(OltPort.olt_id == olt.id, OltPort.port_type == "pon",
                                                  OltPort.oper_status == "up")):
            los = list(db.scalars(select(Ont).where(Ont.pon_port_id == p.id, Ont.status == OntState.LOS)))
            if len(los) < CLUSTER_MIN_LOS:
                continue
            elem = _common_upstream(db, los)
            if elem:
                etype, eid, elabel = elem
                downstream = topology.walk_downstream(db, topology.element_start_endpoints(db, etype, eid))["ont_ids"]
                ratio = len(los) / max(1, len(downstream))
                if ratio >= CLUSTER_RATIO:
                    inc = upsert_incident(
                        db, source_type=etype, source_id=eid, label=elabel,
                        title=f"Suspected fiber fault at/above {elabel}",
                        cause=(f"{len(los)} of {len(downstream)} ONTs downstream of {elabel} report LOS at the same time on "
                               f"PON {p.fsp}. Suspected cause: fault at {elabel} or in the fiber feeding it (inferred from topology)."),
                        severity="major", olt_id=olt.id, site_id=olt.site_id, analysis_type=etype, analysis_id=eid, simulated=sim)
                    touched.add(inc.id)
                    continue
            inc = upsert_incident(db, source_type="olt_port", source_id=p.id, label=f"{olt.name} PON {p.fsp}",
                                  title=f"Multiple LOS on PON {p.fsp} ({olt.name})",
                                  cause=f"{len(los)} ONTs on this port report LOS; no single common upstream element identified "
                                        "(topology incomplete or failures dispersed).",
                                  severity="major", olt_id=olt.id, site_id=olt.site_id, analysis_type="olt_port",
                                  analysis_id=p.id, simulated=sim)
            touched.add(inc.id)
    # Resolve automatic incidents on this OLT whose condition no longer holds.
    for inc in db.scalars(select(Incident).where(Incident.olt_id == olt.id, Incident.detection == "automatic",
                                                 Incident.status != IncidentStatus.RESOLVED)):
        if inc.id not in touched:
            inc.status = IncidentStatus.RESOLVED
            inc.resolved_at = utcnow()
            inc.resolved_by = "system"
            inc.notes = (inc.notes + "\n" if inc.notes else "") + f"[{utcnow().isoformat(timespec='seconds')}] Triggering condition cleared."
    db.flush()
    return sorted(touched)
