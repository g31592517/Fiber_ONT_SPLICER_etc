"""Huawei SmartAX (MA5600T / MA5800) GPON CLI: command builders and output parsers.

VERIFICATION STATUS: UNVERIFIED.
The command syntax and output layouts below follow the commonly published Huawei SmartAX
CLI conventions. They have NOT been checked against the official Huawei command reference
(the documentation hosts were unreachable from the development environment) and have NOT
been exercised against hardware. The unit tests use synthetic fixtures. Command syntax and
output columns differ between product releases (e.g. V800R0xx vs. MA5800 V100R0xx);
verify every builder and parser against the target firmware before enabling writes.

This module is pure (no I/O) so it can be tested in isolation.
"""
from __future__ import annotations

import re
from datetime import datetime

from app.adapters.base import AlarmRecord, BoardInfo, DiscoveredOnt, Fsp, OntStatusInfo, OpticalInfo, ServicePortInfo

_SEP = re.compile(r"^\s*-{10,}\s*$")
_KV = re.compile(r"^\s*([^:]+?)\s*:\s*(.*?)\s*$")


# ----------------------------------------------------------------------------- command builders
def cmd_interface(fsp: Fsp) -> str:
    return f"interface gpon {fsp.frame}/{fsp.slot}"


def cmd_ont_add(fsp: Fsp, ont_index: int | None, serial: str, line_ref: str, srv_ref: str, desc: str,
                auth: str = "sn", loid: str | None = None, password: str | None = None) -> str:
    idx = f" {ont_index}" if ont_index is not None else ""
    if auth == "loid" and loid:
        auth_part = f"loid-auth {quote(loid)} always-on"
    elif auth == "password" and password:
        auth_part = f"password-auth {quote(password)} always-on"
    else:
        auth_part = f"sn-auth {serial}"
    return (f"ont add {fsp.port}{idx} {auth_part} omci ont-lineprofile-id {int(line_ref)} "
            f"ont-srvprofile-id {int(srv_ref)} desc {quote(desc or serial)}")


def cmd_ont_delete(fsp: Fsp, ont_index: int) -> str:
    return f"ont delete {fsp.port} {ont_index}"


def cmd_ont_reset(fsp: Fsp, ont_index: int) -> str:
    return f"ont reset {fsp.port} {ont_index}"


def cmd_ont_admin(fsp: Fsp, ont_index: int, enabled: bool) -> str:
    return f"ont {'activate' if enabled else 'deactivate'} {fsp.port} {ont_index}"


def cmd_service_port(index: int | None, vlan: int, fsp: Fsp, ont_index: int, gem: int, user_vlan: int | None,
                     tt_up: str | None, tt_down: str | None) -> str:
    idx = f"{index} " if index is not None else ""
    uv = f"multi-service user-vlan {user_vlan} tag-transform translate" if user_vlan else "multi-service user-vlan untagged"
    tt = ""
    if tt_up and tt_down:
        tt = f" inbound traffic-table index {int(tt_up)} outbound traffic-table index {int(tt_down)}"
    return f"service-port {idx}vlan {vlan} gpon {fsp} ont {ont_index} gemport {gem} {uv}{tt}"


def cmd_service_port_admin(index: int, enabled: bool) -> str:
    return f"service-port {index} adminstatus {'enable' if enabled else 'disable'}"


def quote(s: str) -> str:
    s = re.sub(r'["\r\n]', "", s)[:64]
    return f'"{s}"'


SERIAL_RE = re.compile(r"^[0-9A-Fa-f]{16}$|^[A-Za-z]{4}[0-9A-Fa-f]{8}$")


def normalize_serial(serial: str) -> str:
    """Accept 16-hex or VENDOR+8hex forms; return the 16-hex form used by the CLI."""
    s = serial.strip()
    if re.fullmatch(r"[A-Za-z]{4}[0-9A-Fa-f]{8}", s):
        return s[:4].encode().hex().upper() + s[4:].upper()
    if re.fullmatch(r"[0-9A-Fa-f]{16}", s):
        return s.upper()
    raise ValueError(f"Invalid GPON serial number '{serial}'")


# ----------------------------------------------------------------------------- parsers
def _blocks(text: str) -> list[dict[str, str]]:
    blocks, cur = [], {}
    for line in text.splitlines():
        if _SEP.match(line):
            if cur:
                blocks.append(cur)
                cur = {}
            continue
        m = _KV.match(line)
        if m and not line.strip().startswith("<") and m.group(1).strip():
            cur[m.group(1).strip()] = m.group(2).strip()
    if cur:
        blocks.append(cur)
    return blocks


def _float(v: str | None) -> float | None:
    if v is None:
        return None
    m = re.search(r"-?\d+(\.\d+)?", v)
    return float(m.group(0)) if m else None


def _dt(v: str | None) -> datetime | None:
    if not v or v == "-":
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(v.replace("+", "+").strip(), fmt)
        except ValueError:
            continue
    return None


def parse_autofind(text: str) -> list[DiscoveredOnt]:
    out = []
    for b in _blocks(text):
        if "F/S/P" not in b or "Ont SN" not in b:
            continue
        sn = b["Ont SN"].split()[0]
        out.append(DiscoveredOnt(
            fsp=Fsp.parse(b["F/S/P"].replace(" ", "")), serial_number=sn, vendor_id=b.get("VendorID"),
            model=b.get("Ont EquipmentID"), equipment_id=b.get("Ont EquipmentID"), hardware_version=b.get("Ont Version"),
            software_version=b.get("Ont SoftwareVersion"), loid=b.get("Loid") or None,
            discovered_at=_dt(b.get("Ont autofind time")),
            raw={k: v for k, v in b.items() if k.lower() not in ("password", "checkcode")},
        ))
    return out


_ONT_ROW = re.compile(
    r"^\s*(\d+)/\s*(\d+)/\s*(\d+)\s+(\d+)\s+([0-9A-Fa-f]{16})\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", re.M)


def parse_ont_info_all(text: str) -> list[OntStatusInfo]:
    """Parses the summary table of ``display ont info <f> <s> <p> all``."""
    out = []
    for m in _ONT_ROW.finditer(text):
        f, s, p, idx, sn, control, run, config, match = m.groups()
        out.append(OntStatusInfo(fsp=Fsp(int(f), int(s), int(p)), ont_index=int(idx), serial_number=sn.upper(),
                                 state=normalize_ont_state(run, control, None, config),
                                 vendor_state=f"run={run} control={control} config={config} match={match}",
                                 admin_status="disabled" if control.lower() == "deactivated" else "enabled"))
    return out


def parse_ont_detail(text: str, fsp: Fsp, ont_index: int) -> OntStatusInfo | None:
    b = {}
    for blk in _blocks(text):
        b.update(blk)
    if "Run state" not in b:
        return None
    run, control, cause = b.get("Run state"), b.get("Control flag"), b.get("Last down cause")
    return OntStatusInfo(
        fsp=fsp, ont_index=ont_index, serial_number=(b.get("SN", "").split() or [""])[0],
        state=normalize_ont_state(run, control, cause, b.get("Config state")),
        vendor_state=f"run={run} control={control} config={b.get('Config state')}", last_down_cause=cause,
        last_up_at=_dt(b.get("Last up time")), last_down_at=_dt(b.get("Last down time")),
        distance_m=int(_float(b.get("ONT distance(m)")) or 0) or None, description=b.get("Description"),
        line_profile_ref=b.get("Line profile ID"), service_profile_ref=b.get("Service profile ID"),
        admin_status="disabled" if (control or "").lower() == "deactivated" else "enabled",
    )


def normalize_ont_state(run: str | None, control: str | None, down_cause: str | None, config: str | None) -> str:
    run_l, ctl = (run or "").lower(), (control or "").lower()
    cause = (down_cause or "").lower()
    if ctl == "deactivated":
        return "disabled"
    if run_l == "online":
        return "faulty" if (config or "").lower() == "failed" else "online"
    if run_l == "offline":
        if "los" in cause or "lofi" in cause:
            return "los"
        if "dying" in cause:
            return "dying_gasp"
        if cause in ("", "-"):
            return "registered_inactive"
        return "offline"
    return "unknown"


def parse_optical(text: str) -> OpticalInfo:
    b = {}
    for blk in _blocks(text):
        b.update(blk)

    def g(*keys):
        for k in keys:
            if k in b and b[k] not in ("-", ""):
                return _float(b[k])
        return None

    info = OpticalInfo(
        rx_power_dbm=g("Rx optical power(dBm)"), tx_power_dbm=g("Tx optical power(dBm)"),
        olt_rx_power_dbm=g("OLT Rx ONT optical power(dBm)"), temperature_c=g("Temperature(C)"),
        voltage_v=g("Voltage(V)"), bias_current_ma=g("Laser bias current(mA)"),
    )
    info.unsupported_fields = [k for k, v in vars(info).items() if v is None and k != "unsupported_fields"]
    return info


_SP_ROW = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+(\S+)\s+gpon\s+(\d+)/\s*(\d+)\s*/\s*(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\w+)",
    re.M | re.I)


def parse_service_ports(text: str) -> list[ServicePortInfo]:
    out = []
    for m in _SP_ROW.finditer(text):
        idx, vlan, _attr, f, s, p, ont, gem, _ftype, fpara, rx, tx, state = m.groups()
        out.append(ServicePortInfo(int(idx), Fsp(int(f), int(s), int(p)), int(ont), int(vlan), int(gem),
                                   int(fpara) if fpara.isdigit() else None, None, "enabled" if state.lower() == "up" else "disabled",
                                   state.lower(), None if rx == "-" else rx, None if tx == "-" else tx))
    return out


_BOARD_ROW = re.compile(r"^\s*(\d+)\s+(\S+)?\s+(Normal|Failed|Auto_find|Config|Prohibited|Standby_failed|Active_normal|Standby_normal)\b(.*)$",
                        re.M | re.I)


def parse_boards(text: str, frame: int = 0) -> list[BoardInfo]:
    out = []
    seen = set()
    for m in _BOARD_ROW.finditer(text):
        slot, name, status, _rest = m.groups()
        st = status.lower()
        norm = "normal" if "normal" in st else "failed" if "failed" in st else "disabled" if st == "prohibited" else "unknown"
        role = "pon" if name and re.search(r"GP|XG|PON", name, re.I) else "uplink" if name and re.search(r"GI|X2|XC|ETH|GE", name, re.I) else \
            "control" if name and re.search(r"SC|MPL|MCU|CTRL", name, re.I) else "other"
        out.append(BoardInfo(frame, int(slot), True, name, role, norm, status))
        seen.add(int(slot))
    for line in text.splitlines():  # empty slots: "  3                              "
        mm = re.match(r"^\s*(\d+)\s*$", line)
        if mm and int(mm.group(1)) not in seen:
            out.append(BoardInfo(frame, int(mm.group(1)), False, None, "other", "empty", None))
    return out


def parse_version(text: str) -> dict[str, str]:
    d = {}
    for line in text.splitlines():
        m = _KV.match(line)
        if m:
            d[m.group(1).strip().upper()] = m.group(2).strip()
    return d


_ALARM_HEAD = re.compile(r"ALARM\s+(\d+)\s+(\S+)\s+(CRITICAL|MAJOR|MINOR|WARNING|CLEARED)?\s*(\S+)?\s*(0x[0-9A-Fa-f]+)?", re.I)


def parse_active_alarms(text: str) -> list[AlarmRecord]:
    """Best-effort parser for active alarm output. Unrecognized alarms are kept as category ``other``."""
    out = []
    for block in re.split(r"\n\s*-{10,}\s*\n", text):
        m = _ALARM_HEAD.search(block)
        if not m:
            continue
        seq, _type, sev, _cls, code = m.groups()
        desc_m = re.search(r"Description\s*:\s*(.+)", block) or re.search(r"\n\s*([A-Za-z].{10,})", block)
        desc = desc_m.group(1).strip() if desc_m else block.strip()[:200]
        out.append(AlarmRecord(vendor_alarm_id=code or seq, category=categorize_alarm(desc),
                               severity=(sev or "warning").lower(), description=desc, vendor_severity=sev,
                               raw={"text": block.strip()[:2000]}))
    return out


def categorize_alarm(text: str) -> str:
    t = text.lower()
    rules = [
        ("dying", "ont_dying_gasp"), ("loss of signal", "ont_los"), ("los", "ont_los"),
        ("temperature", "temperature"), ("power", "power_failure"), ("board", "board_failure"),
        ("uplink", "uplink_failure"), ("optical power", "low_optical_signal"), ("register", "ont_registration_failure"),
    ]
    for needle, cat in rules:
        if needle in t:
            return cat
    return "other"
