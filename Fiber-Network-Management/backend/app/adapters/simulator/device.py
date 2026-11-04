"""Simulated GPON OLT equipment.

The state of each simulated OLT lives in the ``simulated_devices`` table as JSON and plays
the role of the *device*. The NMS core never reads it directly: it only sees what the
simulator adapter returns, exactly as it would for real hardware. Fault-injection
"events" mutate this state so administrators can watch the monitoring engine react.
"""
from __future__ import annotations

import copy
import random
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models import SimulatedDevice

SIM_VENDOR = "SIMULATED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_profiles() -> dict[str, list[dict]]:
    return {
        "line": [
            {"ref": "10", "name": "SIM-LINE-INTERNET", "parameters": {"tcont": 1, "dba_profile": "20", "gem_ports": [1]}},
            {"ref": "20", "name": "SIM-LINE-INTERNET-VOIP", "parameters": {"tcont": 1, "dba_profile": "20", "gem_ports": [1, 2]}},
        ],
        "service": [
            {"ref": "10", "name": "SIM-SRV-1ETH", "parameters": {"eth_ports": 1}},
            {"ref": "20", "name": "SIM-SRV-4ETH-WIFI", "parameters": {"eth_ports": 4, "wifi": True}},
        ],
        "dba": [{"ref": "20", "name": "SIM-DBA-TYPE4-1G", "parameters": {"type": 4, "max_kbps": 1024000}}],
        "traffic_table": [
            {"ref": str(10 + i), "name": f"SIM-TT-{m}M", "parameters": {"cir_kbps": m * 1024}}
            for i, m in enumerate([10, 20, 30, 50, 100, 200])
        ],
    }


def new_device_state(*, model: str = "SIM-GPON-OLT", serial: str, firmware: str = "SIM-1.4.2",
                     pon_slots: tuple[int, ...] = (1, 2), ports_per_board: int = 8,
                     empty_slots: tuple[int, ...] = (3,), uplink_slot: int = 9) -> dict[str, Any]:
    boards = []
    pon_ports: dict[str, dict] = {}
    for slot in pon_slots:
        boards.append({"frame": 0, "slot": slot, "installed": True, "board_type": "SIM-GPON-8P",
                       "role": "pon", "status": "normal", "ports": ports_per_board,
                       "serial": f"{serial}-B{slot}", "hw": "VER.A", "sw": firmware})
        for p in range(ports_per_board):
            pon_ports[f"0/{slot}/{p}"] = {"admin": "enabled", "fault": False, "tx_power": round(3.5 + random.random(), 2),
                                          "max_onts": 128}
    for slot in empty_slots:
        boards.append({"frame": 0, "slot": slot, "installed": False, "role": "pon", "status": "empty"})
    boards.append({"frame": 0, "slot": uplink_slot, "installed": True, "board_type": "SIM-UPLINK-4x10G", "role": "uplink",
                   "status": "normal", "ports": 2, "serial": f"{serial}-U", "hw": "VER.B", "sw": firmware})
    boards.append({"frame": 0, "slot": 7, "installed": True, "board_type": "SIM-CONTROL", "role": "control",
                   "status": "normal", "ports": 0, "serial": f"{serial}-C", "hw": "VER.A", "sw": firmware})
    uplinks = {f"0/{uplink_slot}/{p}": {"admin": "enabled", "up": True} for p in range(2)}
    return {
        "identity": {"vendor": SIM_VENDOR, "model": model, "serial": serial, "firmware": firmware},
        "reachable": True,
        "cpu": 18.0,
        "memory": 41.0,
        "temperature": 42.0,
        "boot_time": _now(),
        "boards": boards,
        "pon_ports": pon_ports,
        "uplinks": uplinks,
        "onts": {},              # fsp -> {ont_index(str) -> ont}
        "autofind": [],          # ONTs that are physically connected but not configured
        "service_ports": {},     # index(str) -> service-port
        "profiles": default_profiles(),
        "fail_next": {},         # operation -> remaining forced failures (fault injection)
        "latency_ms": 4.0,
    }


def make_ont(serial: str, *, model: str = "SIM-ONT-HG8", state: str = "online", rx: float = -19.5,
             line_profile: str | None = "10", service_profile: str | None = "10", desc: str = "") -> dict[str, Any]:
    return {
        "serial": serial, "model": model, "equipment_id": model, "vendor_id": "SIMU", "hw": "V1", "sw": "V1R002C00",
        "state": state, "admin": "enabled", "rx_base": rx, "line_profile": line_profile,
        "service_profile": service_profile, "desc": desc, "state_since": _now(),
        "last_up": _now() if state == "online" else None, "last_down": None if state == "online" else _now(),
        "last_down_cause": None if state == "online" else _down_cause(state), "distance": random.randint(300, 6000),
        "mac": "02:5e:" + ":".join(f"{random.randint(0, 255):02x}" for _ in range(4)),
    }


def _down_cause(state: str) -> str | None:
    return {"los": "LOS", "dying_gasp": "dying-gasp", "offline": "power-off"}.get(state)


# ------------------------------------------------------------------------------------ persistence
@contextmanager
def device_state(host: str, write: bool = False):
    """Load (and optionally persist) a simulated device's state in its own transaction."""
    db = SessionLocal()
    try:
        dev = db.scalar(select(SimulatedDevice).where(SimulatedDevice.host == host))
        if dev is None:
            raise LookupError(f"No simulated device at host '{host}'")
        state = copy.deepcopy(dev.state)
        yield state
        if write:
            dev.state = state
            db.commit()
    finally:
        db.close()


def create_device(db, host: str, state: dict) -> SimulatedDevice:
    dev = SimulatedDevice(host=host, state=state)
    db.add(dev)
    return dev


def noise(serial: str, tick: int, amplitude: float = 0.3) -> float:
    return random.Random(f"{serial}:{tick}").uniform(-amplitude, amplitude)


def find_ont(state: dict, serial: str) -> tuple[str, str, dict] | None:
    for fsp, onts in state["onts"].items():
        for idx, ont in onts.items():
            if ont["serial"].upper() == serial.upper():
                return fsp, idx, ont
    return None


# ------------------------------------------------------------------------------------ events
EVENT_TYPES = {
    "ont_state": "Set an ONT's physical state: online | offline | los | dying_gasp",
    "signal_degrade": "Shift an ONT's receive power by delta_db (negative = weaker)",
    "set_rx_power": "Set an ONT's baseline receive power to rx_dbm",
    "pon_port_fault": "Fail (or restore with fault=false) a PON port; all ONTs on it lose signal",
    "fiber_cut": "Put a list of ONT serials into LOS (simulates a feeder/splitter/distribution failure)",
    "olt_unreachable": "Make the OLT unreachable (or reachable again with unreachable=false)",
    "board_fail": "Fail (or restore) a board in a slot",
    "new_ont": "Connect a new, unconfigured ONT to a PON port so it appears in discovery",
    "temperature": "Set the OLT temperature (°C)",
    "fail_next": "Force the next N calls of an operation (e.g. create_service_port) to fail",
    "restore_all": "Clear all injected faults: ONTs online, ports/boards normal, OLT reachable",
}


def apply_event(state: dict, event: str, params: dict) -> str:
    now = _now()

    def set_ont(ont: dict, new: str):
        if ont["state"] == new:
            return
        ont["state"] = new
        ont["state_since"] = now
        if new == "online":
            ont["last_up"] = now
        else:
            ont["last_down"] = now
            ont["last_down_cause"] = _down_cause(new)

    if event == "ont_state":
        hit = find_ont(state, params["serial"])
        if not hit:
            raise ValueError(f"ONT {params['serial']} is not configured on this simulated OLT")
        new = params["state"]
        if new not in ("online", "offline", "los", "dying_gasp"):
            raise ValueError("state must be online | offline | los | dying_gasp")
        set_ont(hit[2], new)
        return f"ONT {params['serial']} -> {new}"
    if event == "signal_degrade":
        hit = find_ont(state, params["serial"])
        if not hit:
            raise ValueError("ONT not found on simulated OLT")
        hit[2]["rx_base"] = round(hit[2]["rx_base"] + float(params.get("delta_db", -3)), 2)
        return f"ONT {params['serial']} rx baseline now {hit[2]['rx_base']} dBm"
    if event == "set_rx_power":
        hit = find_ont(state, params["serial"])
        if not hit:
            raise ValueError("ONT not found on simulated OLT")
        hit[2]["rx_base"] = float(params["rx_dbm"])
        return f"ONT {params['serial']} rx baseline set to {hit[2]['rx_base']} dBm"
    if event == "pon_port_fault":
        fsp = params["fsp"]
        if fsp not in state["pon_ports"]:
            raise ValueError(f"Unknown PON port {fsp}")
        fault = bool(params.get("fault", True))
        state["pon_ports"][fsp]["fault"] = fault
        for ont in state["onts"].get(fsp, {}).values():
            set_ont(ont, "los" if fault else "online")
        return f"PON port {fsp} {'failed' if fault else 'restored'}"
    if event == "fiber_cut":
        serials = params.get("serials") or []
        restore = bool(params.get("restore", False))
        n = 0
        for s in serials:
            hit = find_ont(state, s)
            if hit:
                set_ont(hit[2], "online" if restore else "los")
                n += 1
        return f"{n} ONT(s) {'restored' if restore else 'put into LOS'}"
    if event == "olt_unreachable":
        state["reachable"] = not bool(params.get("unreachable", True))
        return f"OLT reachable={state['reachable']}"
    if event == "board_fail":
        slot = int(params["slot"])
        failed = bool(params.get("failed", True))
        for b in state["boards"]:
            if b["slot"] == slot and b.get("installed"):
                b["status"] = "failed" if failed else "normal"
                for fsp, onts in state["onts"].items():
                    if fsp.split("/")[1] == str(slot):
                        for ont in onts.values():
                            set_ont(ont, "los" if failed else "online")
                return f"Board in slot {slot} {'failed' if failed else 'restored'}"
        raise ValueError(f"No installed board in slot {slot}")
    if event == "new_ont":
        fsp = params["fsp"]
        if fsp not in state["pon_ports"]:
            raise ValueError(f"Unknown PON port {fsp}")
        serial = params.get("serial") or f"SIMU{random.randint(0, 0xFFFFFFFF):08X}"
        if find_ont(state, serial) or any(a["serial"] == serial for a in state["autofind"]):
            raise ValueError(f"ONT {serial} already present")
        state["autofind"].append({"fsp": fsp, "serial": serial, "vendor_id": "SIMU", "equipment_id": params.get("model", "SIM-ONT-HG8"),
                                  "hw": "V1", "sw": "V1R002C00", "loid": params.get("loid"),
                                  "rx": float(params.get("rx_dbm", -20.5)), "ts": now})
        return f"Unconfigured ONT {serial} connected on {fsp}"
    if event == "temperature":
        state["temperature"] = float(params["value"])
        return f"OLT temperature {state['temperature']} °C"
    if event == "fail_next":
        op = params["operation"]
        state["fail_next"][op] = int(params.get("count", 1))
        return f"Next {state['fail_next'][op]} '{op}' call(s) will fail"
    if event == "restore_all":
        state["reachable"] = True
        state["temperature"] = 42.0
        state["fail_next"] = {}
        for p in state["pon_ports"].values():
            p["fault"] = False
        for b in state["boards"]:
            if b.get("installed"):
                b["status"] = "normal"
        for onts in state["onts"].values():
            for ont in onts.values():
                set_ont(ont, "online")
        return "All simulated faults cleared"
    raise ValueError(f"Unknown simulator event '{event}'")
