"""Monitoring engine: polls OLTs through their adapters and records what the equipment reports.

Principles:
* Only data returned by the adapter is stored. Unsupported metrics stay NULL ("unsupported").
* A communication failure with the OLT is recorded as such; ONTs behind it become
  ``unreachable`` (state unknown) — they are NOT marked offline.
* Alarms reported by equipment are ingested with their vendor identifiers. When an adapter
  cannot retrieve alarms, the NMS derives alarms from status changes and marks them origin=nms.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import (
    AdapterConnectionError,
    AdapterError,
    Capability,
    Fsp,
    OntStatusInfo,
    UnsupportedOperation,
    to_dict,
)
from app.adapters.registry import adapter_class, build_adapter
from app.core import alarms as alarm_svc
from app.core import optical
from app.core.enums import ONT_DOWN_STATES, AlarmCategory, ConnectionStatus, OntState, Severity
from app.db import utcnow
from app.engines import correlation
from app.jobs.queue import handler
from app.models import (
    DeviceMetric,
    Olt,
    OltBoard,
    OltPort,
    OltProfile,
    Ont,
    OntDiscovery,
    OntStatusEvent,
    OpticalReading,
    PortMetric,
)

log = logging.getLogger("fnms.monitoring")


# ------------------------------------------------------------------------------ helpers
def _port_map(db: Session, olt: Olt) -> dict[tuple[int, int, int, str], OltPort]:
    return {(p.frame, p.slot, p.port, p.port_type): p for p in db.scalars(select(OltPort).where(OltPort.olt_id == olt.id))}


def _set_ont_status(db: Session, ont: Ont, new: str, vendor: str | None, reason: str | None = None) -> bool:
    now = utcnow()
    changed = ont.status != new
    if changed:
        db.add(OntStatusEvent(ont_id=ont.id, from_status=ont.status, to_status=new, vendor_status=vendor, reason=reason))
        ont.status = new
        ont.status_changed_at = now
        if new == OntState.ONLINE:
            ont.last_online_at = now
        elif new in ONT_DOWN_STATES:
            ont.last_offline_at = now
    if new != OntState.UNREACHABLE:
        ont.vendor_status = vendor
    return changed


def _alarm_key(olt: Olt, *parts) -> str:
    return ":".join(["olt", str(olt.id), *[str(p) for p in parts]])


# ------------------------------------------------------------------------------ connection test
def test_connection(db: Session, olt: Olt) -> dict:
    with build_adapter(olt) as adapter:
        try:
            result = adapter.test_connection()
        except AdapterConnectionError as exc:
            olt.connection_status = ConnectionStatus.UNREACHABLE
            olt.last_poll_error = str(exc)
            return {"ok": False, "error": str(exc)}
        identity = None
        if adapter.supports(Capability.DEVICE_DISCOVERY):
            try:
                identity = to_dict(adapter.get_identity())
                if identity.get("model") and not olt.model:
                    olt.model = identity["model"]
                if identity.get("serial_number"):
                    olt.serial_number = identity["serial_number"]
                if identity.get("firmware_version"):
                    olt.firmware_version = identity["firmware_version"]
            except AdapterError as exc:
                identity = {"error": str(exc)}
        olt.connection_status = ConnectionStatus.ONLINE
        olt.last_contact_at = utcnow()
        olt.last_poll_error = None
        return {"ok": True, **result, "identity": identity}


# ------------------------------------------------------------------------------ poll
def poll_olt(db: Session, olt: Olt) -> dict:
    now = utcnow()
    cls = adapter_class(olt.adapter)
    summary: dict = {"olt_id": olt.id, "adapter": olt.adapter, "steps": {}}
    olt.last_poll_at = now
    olt.next_poll_at = now + timedelta(seconds=olt.poll_interval_seconds)
    source = olt.adapter
    sim = olt.is_simulated

    with build_adapter(olt) as adapter:
        # 1. health / reachability
        try:
            health = adapter.get_health() if cls.supports(Capability.OLT_HEALTH) else None
            if health is None:
                adapter.test_connection()
        except (AdapterConnectionError, AdapterError) as exc:
            return _handle_unreachable(db, olt, str(exc), summary)
        olt.connection_status = ConnectionStatus.ONLINE
        olt.last_contact_at = now
        olt.last_poll_error = None
        alarm_svc.clear_alarm(db, _alarm_key(olt, "unreachable"), "OLT communication restored")
        if health:
            olt.cpu_percent, olt.memory_percent = health.cpu_percent, health.memory_percent
            olt.temperature_c, olt.uptime_seconds = health.temperature_c, health.uptime_seconds
        partial_errors: list[str] = []

        def step(name: str, cap: Capability, fn):
            if not cls.supports(cap):
                summary["steps"][name] = "unsupported"
                return None
            try:
                r = fn()
                summary["steps"][name] = "ok"
                return r
            except UnsupportedOperation:
                summary["steps"][name] = "unsupported"
            except AdapterError as exc:
                summary["steps"][name] = f"error: {exc}"
                partial_errors.append(f"{name}: {exc}")
            return None

        # 2. hardware
        boards = step("boards", Capability.DEVICE_DISCOVERY, adapter.get_boards)
        if boards is not None:
            existing = {(b.frame, b.slot): b for b in db.scalars(select(OltBoard).where(OltBoard.olt_id == olt.id))}
            for b in boards:
                row = existing.get((b.frame, b.slot)) or OltBoard(olt_id=olt.id, frame=b.frame, slot=b.slot)
                row.installed, row.board_type, row.role = b.installed, b.board_type, b.role
                row.status, row.vendor_status, row.port_count = b.status, b.vendor_status, b.port_count
                row.serial_number, row.hardware_version, row.software_version = b.serial_number, b.hardware_version, b.software_version
                row.source, row.last_seen_at = "device", now
                db.add(row)
                key = _alarm_key(olt, "board", b.slot)
                if not cls.supports(Capability.ALARM_RETRIEVAL):
                    if b.status == "failed":
                        alarm_svc.raise_alarm(db, key=key, origin="nms", source_type="board", source_id=None,
                                              source_label=f"{olt.name} slot {b.frame}/{b.slot}", category=AlarmCategory.BOARD_FAILURE,
                                              severity=Severity.CRITICAL, description=f"Board in slot {b.slot} reported failed",
                                              olt_id=olt.id, site_id=olt.site_id, is_simulated=sim)
                    else:
                        alarm_svc.clear_alarm(db, key, "Board reported normal")
            db.flush()
            olt.slot_count = olt.slot_count or len(boards)

        # 3. ports
        ports = step("ports", Capability.PON_PORT_MONITORING, adapter.get_ports)
        port_rows = _port_map(db, olt)
        if ports is not None:
            board_ids = {(b.frame, b.slot): b.id for b in db.scalars(select(OltBoard).where(OltBoard.olt_id == olt.id))}
            for p in ports:
                row = port_rows.get((p.frame, p.slot, p.port, p.port_type))
                if row is None:
                    row = OltPort(olt_id=olt.id, frame=p.frame, slot=p.slot, port=p.port, port_type=p.port_type)
                    db.add(row)
                    port_rows[(p.frame, p.slot, p.port, p.port_type)] = row
                row.board_id = board_ids.get((p.frame, p.slot))
                row.technology, row.admin_status, row.oper_status = p.technology, p.admin_status, p.oper_status
                row.vendor_status, row.tx_power_dbm, row.max_onts = p.vendor_status, p.tx_power_dbm, p.max_onts or row.max_onts
                row.online_onts, row.source, row.last_seen_at = p.onts_online, "device", now
                if p.label:
                    row.label = row.label or p.label
            db.flush()
            for p in ports:
                row = port_rows[(p.frame, p.slot, p.port, p.port_type)]
                db.add(PortMetric(port_id=row.id, ts=now, oper_status=p.oper_status, onts_online=p.onts_online,
                                  onts_total=p.onts_total, tx_power_dbm=p.tx_power_dbm, source=source))
            olt.pon_port_count = sum(1 for p in ports if p.port_type == "pon")

        # 4. ONT status
        statuses = step("ont_status", Capability.ONT_STATUS, adapter.get_onts)
        status_changes = 0
        if statuses is not None:
            status_changes = _sync_onts(db, olt, statuses, port_rows, derive_alarms=not cls.supports(Capability.ALARM_RETRIEVAL))
            summary["onts_reported"] = len(statuses)
            summary["status_changes"] = status_changes

        # 5. optical
        if cls.supports(Capability.OPTICAL_POWER) and statuses is not None:
            n, errs = _collect_optical(db, olt, adapter, source)
            summary["steps"]["optical"] = "ok" if not errs else f"partial ({len(errs)} errors)"
            summary["optical_readings"] = n
            partial_errors.extend(errs[:5])
        else:
            summary["steps"]["optical"] = "unsupported"

        # 6. alarms
        alarms = step("alarms", Capability.ALARM_RETRIEVAL, adapter.get_alarms)
        if alarms is not None:
            summary["alarms_active_on_device"] = len(alarms)
            _ingest_alarms(db, olt, alarms, port_rows)

        # 7. discovery
        discovered = step("discovery", Capability.ONT_DISCOVERY, adapter.discover_onts)
        if discovered is not None:
            summary["discovered_unregistered"] = record_discoveries(db, olt, discovered, port_rows)

    totals = list(db.scalars(select(Ont.status).where(Ont.olt_id == olt.id, Ont.registration_status == "registered")))
    db.add(DeviceMetric(olt_id=olt.id, ts=now, reachable=True, latency_ms=health.latency_ms if health else None,
                        cpu_percent=olt.cpu_percent, memory_percent=olt.memory_percent, temperature_c=olt.temperature_c,
                        onts_total=len(totals), onts_online=sum(1 for s in totals if s == OntState.ONLINE), source=source))
    if partial_errors:
        olt.connection_status = ConnectionStatus.DEGRADED
        olt.last_poll_error = "; ".join(partial_errors)[:2000]
    db.flush()
    summary["incidents"] = correlation.correlate_olt(db, olt)
    return summary


def _handle_unreachable(db: Session, olt: Olt, error: str, summary: dict) -> dict:
    olt.connection_status = ConnectionStatus.UNREACHABLE
    olt.last_poll_error = error
    db.add(DeviceMetric(olt_id=olt.id, reachable=False, source=olt.adapter))
    alarm_svc.raise_alarm(db, key=_alarm_key(olt, "unreachable"), origin="nms", source_type="olt", source_id=olt.id,
                          source_label=olt.name, category=AlarmCategory.OLT_UNREACHABLE, severity=Severity.CRITICAL,
                          description=f"OLT {olt.name} is not responding: {error}", olt_id=olt.id, site_id=olt.site_id,
                          is_simulated=olt.is_simulated)
    n = 0
    for ont in db.scalars(select(Ont).where(Ont.olt_id == olt.id, Ont.registration_status == "registered")):
        if _set_ont_status(db, ont, OntState.UNREACHABLE, ont.vendor_status, "OLT communication failure — ONT state unknown"):
            n += 1
    db.flush()
    summary.update({"reachable": False, "error": error, "onts_marked_unreachable": n,
                    "incidents": correlation.correlate_olt(db, olt)})
    return summary


def _sync_onts(db: Session, olt: Olt, statuses: list[OntStatusInfo], port_rows: dict, derive_alarms: bool) -> int:
    now = utcnow()
    inventory = {o.serial_number.upper(): o for o in db.scalars(select(Ont).where(Ont.olt_id == olt.id))}
    seen: set[int] = set()
    changes = 0
    for s in statuses:
        port = port_rows.get((s.fsp.frame, s.fsp.slot, s.fsp.port, "pon"))
        ont = inventory.get(s.serial_number.upper())
        if ont is None:
            ont = db.scalar(select(Ont).where(Ont.serial_number == s.serial_number.upper()))
        if ont is None:
            # Automatic synchronization: ONT configured on the OLT but unknown to the NMS.
            ont = Ont(serial_number=s.serial_number.upper(), olt_id=olt.id, registration_status="registered",
                      status=OntState.UNKNOWN, notes="Imported automatically from OLT configuration during sync",
                      site_id=olt.site_id, is_simulated=olt.is_simulated)
            db.add(ont)
            db.flush()
        ont.olt_id = olt.id
        ont.pon_port_id = port.id if port else ont.pon_port_id
        ont.ont_index = s.ont_index
        ont.registration_status = "registered"
        ont.admin_status = s.admin_status
        ont.last_down_cause = s.last_down_cause
        ont.distance_m = s.distance_m if s.distance_m is not None else ont.distance_m
        for attr, val in (("vendor", s.vendor), ("model", s.model), ("equipment_id", s.equipment_id),
                          ("hardware_version", s.hardware_version), ("firmware_version", s.firmware_version),
                          ("mac_address", s.mac_address)):
            if val:
                setattr(ont, attr, val)
        if s.line_profile_ref:
            ont.line_profile_id = _profile_id(db, olt, "line", s.line_profile_ref) or ont.line_profile_id
        if s.service_profile_ref:
            ont.service_profile_id = _profile_id(db, olt, "service", s.service_profile_ref) or ont.service_profile_id
        if s.last_up_at and (ont.last_online_at is None or s.last_up_at > ont.last_online_at):
            ont.last_online_at = s.last_up_at
        if s.last_down_at and (ont.last_offline_at is None or s.last_down_at > ont.last_offline_at):
            ont.last_offline_at = s.last_down_at
        ont.last_polled_at = now
        if _set_ont_status(db, ont, s.state, s.vendor_state, s.last_down_cause):
            changes += 1
        if s.state != OntState.ONLINE:
            ont.rx_power_dbm = ont.tx_power_dbm = ont.olt_rx_power_dbm = None
            ont.optical_status = None
        seen.add(ont.id)
        if derive_alarms:
            _derive_ont_alarms(db, olt, ont)
        db.flush()
    # ONTs the NMS believes are registered on this OLT but the device no longer reports.
    for ont in inventory.values():
        if ont.id not in seen and ont.registration_status == "registered":
            if _set_ont_status(db, ont, OntState.UNKNOWN, "not present in OLT configuration", "Missing from OLT"):
                changes += 1
            alarm_svc.raise_alarm(db, key=_alarm_key(olt, "ont-missing", ont.serial_number), origin="nms", source_type="ont",
                                  source_id=ont.id, source_label=f"ONT {ont.serial_number}", category=AlarmCategory.OTHER,
                                  severity=Severity.WARNING,
                                  description=f"ONT {ont.serial_number} is registered in the NMS but not configured on {olt.name}",
                                  olt_id=olt.id, site_id=olt.site_id, is_simulated=olt.is_simulated)
        elif ont.id in seen:
            alarm_svc.clear_alarm(db, _alarm_key(olt, "ont-missing", ont.serial_number), "ONT present on OLT again")
    return changes


def _derive_ont_alarms(db: Session, olt: Olt, ont: Ont) -> None:
    for state, cat, sev in ((OntState.LOS, AlarmCategory.ONT_LOS, Severity.MAJOR),
                            (OntState.DYING_GASP, AlarmCategory.ONT_DYING_GASP, Severity.MINOR)):
        key = _alarm_key(olt, "nms-ont", cat, ont.serial_number)
        if ont.status == state:
            alarm_svc.raise_alarm(db, key=key, origin="nms", source_type="ont", source_id=ont.id,
                                  source_label=f"ONT {ont.serial_number}", category=cat, severity=sev,
                                  description=f"ONT {ont.serial_number} status {state} (derived from status poll)",
                                  olt_id=olt.id, site_id=olt.site_id, is_simulated=olt.is_simulated)
        else:
            alarm_svc.clear_alarm(db, key, f"ONT status is now {ont.status}")


def _profile_id(db: Session, olt: Olt, kind: str, ref: str) -> int | None:
    return db.scalar(select(OltProfile.id).where(OltProfile.olt_id == olt.id, OltProfile.kind == kind,
                                                 OltProfile.vendor_ref == str(ref)))


def record_optical(db: Session, ont: Ont, info, source: str) -> None:
    now = utcnow()
    db.add(OpticalReading(ont_id=ont.id, ts=now, rx_power_dbm=info.rx_power_dbm, tx_power_dbm=info.tx_power_dbm,
                          olt_rx_power_dbm=info.olt_rx_power_dbm, temperature_c=info.temperature_c,
                          voltage_v=info.voltage_v, bias_current_ma=info.bias_current_ma, source=source))
    ont.rx_power_dbm, ont.tx_power_dbm, ont.olt_rx_power_dbm = info.rx_power_dbm, info.tx_power_dbm, info.olt_rx_power_dbm
    ont.temperature_c, ont.voltage_v, ont.bias_current_ma = info.temperature_c, info.voltage_v, info.bias_current_ma
    status, violations = optical.evaluate(optical.threshold_for_ont(db, ont), info.rx_power_dbm, info.olt_rx_power_dbm)
    ont.optical_status = status
    olt = ont.olt
    for direction, cat in (("low", AlarmCategory.LOW_OPTICAL_SIGNAL), ("high", AlarmCategory.HIGH_OPTICAL_SIGNAL)):
        key = f"nms:optical:{direction}:{ont.serial_number}"
        hit = [v for v in violations if v["direction"] == direction]
        if hit:
            worst = "critical" if any(v["level"] == "critical" for v in hit) else "warning"
            sev = Severity.MAJOR if worst == "critical" else Severity.WARNING
            desc = "; ".join(f"{v['metric']} {v['value']} dBm {'≤' if direction == 'low' else '≥'} {v['limit']} ({v['level']})" for v in hit)
            alarm_svc.raise_alarm(db, key=key, origin="nms", source_type="ont", source_id=ont.id,
                                  source_label=f"ONT {ont.serial_number}", category=cat, severity=sev,
                                  description=f"Optical signal {direction}: {desc}", olt_id=ont.olt_id,
                                  site_id=olt.site_id if olt else None, is_simulated=ont.is_simulated)
        else:
            alarm_svc.clear_alarm(db, key, "Optical level back within thresholds" if status else "No optical reading")


def _collect_optical(db: Session, olt: Olt, adapter, source: str) -> tuple[int, list[str]]:
    n, errs = 0, []
    onts = db.scalars(select(Ont).where(Ont.olt_id == olt.id, Ont.registration_status == "registered",
                                        Ont.status == OntState.ONLINE, Ont.ont_index.is_not(None)))
    for ont in onts:
        if not ont.pon_port:
            continue
        try:
            info = adapter.get_optical(Fsp(ont.pon_port.frame, ont.pon_port.slot, ont.pon_port.port), ont.ont_index)
        except AdapterConnectionError:
            raise
        except AdapterError as exc:
            errs.append(f"{ont.serial_number}: {exc}")
            continue
        record_optical(db, ont, info, source)
        n += 1
    return n, errs


def _ingest_alarms(db: Session, olt: Olt, records, port_rows: dict) -> None:
    from app.models import Alarm

    active_keys = set()
    for r in records:
        key = _alarm_key(olt, "dev", r.vendor_alarm_id)
        active_keys.add(key)
        source_type, source_id, label = r.source_type, None, olt.name
        if r.source_type == "ont" and r.serial_number:
            ont = db.scalar(select(Ont).where(Ont.serial_number == r.serial_number.upper()))
            source_id, label = (ont.id if ont else None), f"ONT {r.serial_number}"
        elif r.source_type == "olt_port" and r.fsp:
            p = port_rows.get((r.fsp.frame, r.fsp.slot, r.fsp.port, "pon")) or port_rows.get((r.fsp.frame, r.fsp.slot, r.fsp.port, "uplink"))
            source_id, label = (p.id if p else None), f"{olt.name} port {r.fsp}"
        elif r.source_type == "board":
            b = db.scalar(select(OltBoard).where(OltBoard.olt_id == olt.id, OltBoard.slot == r.slot))
            source_id, label = (b.id if b else None), f"{olt.name} slot {r.slot}"
        else:
            source_id = olt.id
        alarm_svc.raise_alarm(db, key=key, origin="device", source_type=source_type, source_id=source_id, source_label=label,
                              category=r.category, severity=r.severity, description=r.description, olt_id=olt.id,
                              site_id=olt.site_id, vendor_alarm_id=r.vendor_alarm_id, vendor_severity=r.vendor_severity,
                              vendor_raw=r.raw or None, occurred_at=r.raised_at, is_simulated=olt.is_simulated)
    prefix = _alarm_key(olt, "dev", "")
    from app.core.enums import OPEN_ALARM_STATES

    for a in db.scalars(select(Alarm).where(Alarm.olt_id == olt.id, Alarm.origin == "device",
                                            Alarm.state.in_(list(OPEN_ALARM_STATES)))):
        if a.alarm_key.startswith(prefix) and a.alarm_key not in active_keys:
            alarm_svc.clear_alarm(db, a.alarm_key, "Alarm no longer active on device")


def record_discoveries(db: Session, olt: Olt, discovered, port_rows: dict | None = None) -> int:
    """Store autofind results. Discovery is never treated as registration."""
    now = utcnow()
    port_rows = port_rows or _port_map(db, olt)
    seen = set()
    for d in discovered:
        serial = d.serial_number.upper()
        seen.add(serial)
        port = port_rows.get((d.fsp.frame, d.fsp.slot, d.fsp.port, "pon"))
        row = db.scalar(select(OntDiscovery).where(OntDiscovery.olt_id == olt.id, OntDiscovery.serial_number == serial))
        if row is None:
            row = OntDiscovery(olt_id=olt.id, serial_number=serial, first_seen_at=d.discovered_at or now,
                               is_simulated=olt.is_simulated)
            db.add(row)
        row.pon_port_id = port.id if port else None
        row.vendor_id, row.model, row.equipment_id = d.vendor_id, d.model, d.equipment_id
        row.hardware_version, row.software_version, row.loid = d.hardware_version, d.software_version, d.loid
        row.rx_power_dbm, row.vendor_reported, row.last_seen_at = d.rx_power_dbm, d.raw or None, now
        registered = db.scalar(select(Ont).where(Ont.serial_number == serial, Ont.registration_status == "registered"))
        if row.state in ("gone", "registered") or row.state is None:
            row.state = "unregistered"
        if registered:
            # Seen in autofind while the NMS thinks it is registered: report conflict rather than hide it.
            row.state = "unregistered"
            row.vendor_reported = {**(row.vendor_reported or {}), "nms_note": f"Serial is registered in NMS as ONT {registered.id}"}
    for row in db.scalars(select(OntDiscovery).where(OntDiscovery.olt_id == olt.id, OntDiscovery.state == "unregistered")):
        if row.serial_number not in seen:
            row.state = "gone"
    db.flush()
    return len(seen)


def refresh_ont(db: Session, ont: Ont) -> dict:
    olt = ont.olt
    if olt is None or ont.pon_port is None or ont.ont_index is None:
        return {"ok": False, "error": "ONT is not associated with an OLT port/index"}
    cls = adapter_class(olt.adapter)
    fsp = Fsp(ont.pon_port.frame, ont.pon_port.slot, ont.pon_port.port)
    out = {"ok": True}
    with build_adapter(olt) as adapter:
        try:
            if cls.supports(Capability.ONT_STATUS):
                s = adapter.get_ont(fsp, ont.ont_index)
                if s is None:
                    _set_ont_status(db, ont, OntState.UNKNOWN, "not present in OLT configuration", "Missing from OLT")
                    out["status"] = "missing_on_olt"
                else:
                    _set_ont_status(db, ont, s.state, s.vendor_state, s.last_down_cause)
                    ont.last_polled_at = utcnow()
                    out["status"] = s.state
            else:
                out["status"] = "unsupported"
            if cls.supports(Capability.OPTICAL_POWER) and ont.status == OntState.ONLINE:
                info = adapter.get_optical(fsp, ont.ont_index)
                record_optical(db, ont, info, olt.adapter)
                out["optical"] = to_dict(info)
            else:
                out["optical"] = "unsupported" if not cls.supports(Capability.OPTICAL_POWER) else "ont_not_online"
        except AdapterConnectionError as exc:
            _set_ont_status(db, ont, OntState.UNREACHABLE, ont.vendor_status, "OLT communication failure")
            return {"ok": False, "error": str(exc)}
    return out


def sync_profiles(db: Session, olt: Olt) -> dict:
    now = utcnow()
    with build_adapter(olt) as adapter:
        profiles = adapter.get_profiles()
    n = 0
    for p in profiles:
        row = db.scalar(select(OltProfile).where(OltProfile.olt_id == olt.id, OltProfile.kind == p.kind,
                                                 OltProfile.vendor_ref == p.ref))
        if row is None:
            row = OltProfile(olt_id=olt.id, kind=p.kind, vendor_ref=p.ref)
            db.add(row)
        row.name, row.parameters, row.source, row.last_synced_at = p.name, p.parameters, "device", now
        n += 1
    return {"profiles": n}


def discover(db: Session, olt: Olt) -> dict:
    with build_adapter(olt) as adapter:
        found = adapter.discover_onts()
    n = record_discoveries(db, olt, found)
    return {"discovered": n}


# ------------------------------------------------------------------------------ job handlers
def _olt(db: Session, payload: dict) -> Olt:
    olt = db.get(Olt, payload["olt_id"])
    if olt is None:
        raise RuntimeError(f"OLT {payload['olt_id']} no longer exists")
    return olt


@handler("olt.test_connection")
def _h_test(db, payload, job):
    return test_connection(db, _olt(db, payload))


@handler("olt.poll")
def _h_poll(db, payload, job):
    return poll_olt(db, _olt(db, payload))


@handler("olt.discover")
def _h_discover(db, payload, job):
    return discover(db, _olt(db, payload))


@handler("olt.sync_profiles")
def _h_sync(db, payload, job):
    return sync_profiles(db, _olt(db, payload))


@handler("olt.backup")
def _h_backup(db, payload, job):
    olt = _olt(db, payload)
    with build_adapter(olt) as adapter:
        r = adapter.backup_config()
    return {"ok": r.ok, "message": r.message, "config": r.data.get("config"), "taken_at": utcnow().isoformat()}


@handler("ont.refresh")
def _h_refresh(db, payload, job):
    ont = db.get(Ont, payload["ont_id"])
    return refresh_ont(db, ont)
