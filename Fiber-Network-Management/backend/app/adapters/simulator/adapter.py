"""Simulator adapter — implements the full adapter contract against simulated equipment."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from app.adapters.base import (
    AdapterConnectionError,
    AdapterError,
    AlarmRecord,
    BoardInfo,
    Capability,
    CapabilityInfo,
    DeviceIdentity,
    DiscoveredOnt,
    Fsp,
    OltHealth,
    OntRegistration,
    OntStatusInfo,
    OpResult,
    OpticalInfo,
    PortInfo,
    ProfileInfo,
    ServicePortInfo,
    ServicePortSpec,
    VendorAdapter,
    Verification,
)
from app.adapters.simulator.device import device_state, find_ont, noise

_SIM = CapabilityInfo(True, Verification.SIMULATED, "Simulated equipment")

_VENDOR_STATE = {
    "online": "online",
    "offline": "offline (power-off)",
    "los": "offline (LOS)",
    "dying_gasp": "offline (dying-gasp)",
    "registered_inactive": "offline (never online)",
}


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


class SimulatorAdapter(VendorAdapter):
    key = "simulator"
    display_name = "Built-in GPON simulator"
    vendor = "SIMULATED"
    supported_models = ["SIM-GPON-OLT"]
    protocols = ["simulator"]
    description = ("Simulated GPON OLT used for development, testing and demonstrations. "
                   "All data it produces is labelled as simulated.")
    CAPABILITIES = {cap: _SIM for cap in Capability}

    # ------------------------------------------------------------------ internals
    def _state(self, write: bool = False):
        return device_state(self.conn.host, write=write)

    def _check_reachable(self, st: dict):
        if not st.get("reachable", True):
            raise AdapterConnectionError(f"Simulated OLT {self.conn.host} is not responding (timeout)")
        lat = float(self.conn.extra.get("simulated_latency_ms", 0) or 0)
        if lat:
            time.sleep(lat / 1000)

    @staticmethod
    def _maybe_fail(st: dict, op: str):
        remaining = st.get("fail_next", {}).get(op, 0)
        if remaining > 0:
            st["fail_next"][op] = remaining - 1
            raise AdapterError(f"Simulated device rejected '{op}' (injected failure)", retryable=False)

    @staticmethod
    def _board_failed(st: dict, slot: int) -> bool:
        return any(b["slot"] == slot and b.get("status") == "failed" for b in st["boards"])

    def _effective_state(self, st: dict, fsp: str, ont: dict) -> str:
        if ont.get("admin") == "disabled":
            return "disabled"
        port = st["pon_ports"].get(fsp, {})
        if port.get("fault") or self._board_failed(st, int(fsp.split("/")[1])):
            return "los"
        return ont["state"]

    def _ont_info(self, st: dict, fsp: str, idx: str, ont: dict) -> OntStatusInfo:
        eff = self._effective_state(st, fsp, ont)
        vendor_state = "deactivated" if eff == "disabled" else _VENDOR_STATE.get(eff, eff)
        return OntStatusInfo(
            fsp=Fsp.parse(fsp), ont_index=int(idx), serial_number=ont["serial"], state=eff, vendor_state=vendor_state,
            admin_status=ont.get("admin", "enabled"), last_down_cause=ont.get("last_down_cause"),
            last_up_at=_dt(ont.get("last_up")), last_down_at=_dt(ont.get("last_down")),
            distance_m=ont.get("distance") if eff == "online" else None, description=ont.get("desc"),
            line_profile_ref=ont.get("line_profile"), service_profile_ref=ont.get("service_profile"),
            vendor=ont.get("vendor_id"), model=ont.get("model"), equipment_id=ont.get("equipment_id"),
            hardware_version=ont.get("hw"), firmware_version=ont.get("sw"), mac_address=ont.get("mac"),
        )

    @staticmethod
    def _profile_exists(st: dict, kind: str, ref: str | None) -> bool:
        return ref is None or any(p["ref"] == str(ref) for p in st["profiles"].get(kind, []))

    # ------------------------------------------------------------------ connection
    def test_connection(self):
        with self._state() as st:
            self._check_reachable(st)
            return {"reachable": True, "latency_ms": st.get("latency_ms"), "protocol": "simulator",
                    "message": "Connected to simulated OLT"}

    def get_identity(self) -> DeviceIdentity:
        with self._state() as st:
            self._check_reachable(st)
            i = st["identity"]
            boot = _dt(st.get("boot_time"))
            up = int((datetime.now(timezone.utc) - boot).total_seconds()) if boot else None
            return DeviceIdentity(vendor=i["vendor"], model=i["model"], serial_number=i["serial"],
                                  firmware_version=i["firmware"], hostname=self.conn.host,
                                  description="Simulated GPON OLT", uptime_seconds=up)

    # ------------------------------------------------------------------ monitoring
    def get_health(self) -> OltHealth:
        with self._state(write=True) as st:
            self._check_reachable(st)
            st["tick"] = st.get("tick", 0) + 1
            t = st["tick"]
            boot = _dt(st.get("boot_time"))
            return OltHealth(
                reachable=True,
                latency_ms=round(st.get("latency_ms", 4.0) + abs(noise("lat", t, 1.5)), 2),
                cpu_percent=round(max(1.0, st["cpu"] + noise("cpu", t, 6)), 1),
                memory_percent=round(st["memory"] + noise("mem", t, 1), 1),
                temperature_c=round(st["temperature"] + noise("temp", t, 0.8), 1),
                uptime_seconds=int((datetime.now(timezone.utc) - boot).total_seconds()) if boot else None,
            )

    def get_boards(self) -> list[BoardInfo]:
        with self._state() as st:
            self._check_reachable(st)
            return [BoardInfo(frame=b["frame"], slot=b["slot"], installed=b["installed"], board_type=b.get("board_type"),
                              role=b.get("role", "pon"), status=b.get("status", "unknown"),
                              vendor_status={"normal": "Normal", "failed": "Failed", "empty": "-"}.get(b.get("status"), b.get("status")),
                              port_count=b.get("ports"), serial_number=b.get("serial"), hardware_version=b.get("hw"),
                              software_version=b.get("sw")) for b in st["boards"]]

    def get_ports(self) -> list[PortInfo]:
        with self._state() as st:
            self._check_reachable(st)
            out = []
            for fsp, p in st["pon_ports"].items():
                f = Fsp.parse(fsp)
                onts = st["onts"].get(fsp, {})
                online = sum(1 for o in onts.values() if self._effective_state(st, fsp, o) == "online")
                if p.get("admin") == "disabled":
                    oper, vend = "disabled", "Deactivated"
                elif p.get("fault"):
                    oper, vend = "fault", "Fault (LOS on port)"
                elif self._board_failed(st, f.slot):
                    oper, vend = "down", "Board failed"
                else:
                    oper, vend = "up", "Normal"
                out.append(PortInfo(f.frame, f.slot, f.port, "pon", "GPON", p.get("admin", "enabled"), oper, vend,
                                    p.get("tx_power") if oper == "up" else None, p.get("max_onts"), len(onts), online))
            for fsp, u in st.get("uplinks", {}).items():
                f = Fsp.parse(fsp)
                out.append(PortInfo(f.frame, f.slot, f.port, "uplink", "10GE", u.get("admin", "enabled"),
                                    "up" if u.get("up") else "down", "up" if u.get("up") else "down"))
            return out

    def get_onts(self) -> list[OntStatusInfo]:
        with self._state() as st:
            self._check_reachable(st)
            return [self._ont_info(st, fsp, idx, o) for fsp, onts in st["onts"].items() for idx, o in onts.items()]

    def get_ont(self, fsp: Fsp, ont_index: int) -> OntStatusInfo | None:
        with self._state() as st:
            self._check_reachable(st)
            o = st["onts"].get(str(fsp), {}).get(str(ont_index))
            return self._ont_info(st, str(fsp), str(ont_index), o) if o else None

    def get_optical(self, fsp: Fsp, ont_index: int) -> OpticalInfo:
        with self._state() as st:
            self._check_reachable(st)
            o = st["onts"].get(str(fsp), {}).get(str(ont_index))
            if o is None:
                raise AdapterError(f"ONT {fsp}:{ont_index} does not exist on the OLT")
            if self._effective_state(st, str(fsp), o) != "online":
                # No light / no OMCI: the device reports no optical values. We do NOT invent any.
                return OpticalInfo()
            t, s = st.get("tick", 0), o["serial"]
            return OpticalInfo(
                rx_power_dbm=round(o["rx_base"] + noise(s, t), 2),
                tx_power_dbm=round(2.3 + noise(s + "tx", t, 0.15), 2),
                olt_rx_power_dbm=round(o["rx_base"] - 1.8 + noise(s + "orx", t), 2),
                temperature_c=round(46 + noise(s + "t", t, 3), 1),
                voltage_v=round(3.3 + noise(s + "v", t, 0.04), 3),
                bias_current_ma=round(11.5 + noise(s + "b", t, 1.2), 2),
            )

    def get_alarms(self) -> list[AlarmRecord]:
        with self._state() as st:
            self._check_reachable(st)
            out: list[AlarmRecord] = []
            temp = st.get("temperature", 40)
            if temp >= 65:
                sev = "critical" if temp >= 75 else "major"
                out.append(AlarmRecord("SIM-0x0A310001", "temperature", sev, f"Board temperature too high ({temp} °C)",
                                       vendor_severity=sev.capitalize(), raw={"temperature": temp}))
            for b in st["boards"]:
                if b.get("status") == "failed":
                    out.append(AlarmRecord(f"SIM-0x0A200002:{b['slot']}", "board_failure", "critical",
                                           f"Board {b.get('board_type')} in slot 0/{b['slot']} failed", source_type="board",
                                           slot=b["slot"], vendor_severity="Critical"))
            for fsp, p in st["pon_ports"].items():
                if p.get("fault"):
                    out.append(AlarmRecord(f"SIM-0x2E112005:{fsp}", "pon_port_failure", "critical",
                                           f"PON port {fsp} loss of signal on all ONTs", source_type="olt_port",
                                           fsp=Fsp.parse(fsp), vendor_severity="Critical"))
            for fsp, u in st.get("uplinks", {}).items():
                if not u.get("up"):
                    out.append(AlarmRecord(f"SIM-0x0A100003:{fsp}", "uplink_failure", "critical", f"Uplink {fsp} down",
                                           source_type="olt_port", fsp=Fsp.parse(fsp), vendor_severity="Critical"))
            for fsp, onts in st["onts"].items():
                port_fault = st["pon_ports"].get(fsp, {}).get("fault") or self._board_failed(st, int(fsp.split("/")[1]))
                if port_fault:
                    continue  # equipment reports the port-level alarm instead of per-ONT alarms
                for idx, o in onts.items():
                    if o.get("admin") == "disabled":
                        continue
                    if o["state"] == "los":
                        out.append(AlarmRecord(f"SIM-0x2E2D2001:{fsp}:{idx}", "ont_los", "major",
                                               f"ONT {o['serial']} loss of signal", source_type="ont", fsp=Fsp.parse(fsp),
                                               ont_index=int(idx), serial_number=o["serial"], vendor_severity="Major",
                                               raised_at=_dt(o.get("state_since"))))
                    elif o["state"] == "dying_gasp":
                        out.append(AlarmRecord(f"SIM-0x2E2D2002:{fsp}:{idx}", "ont_dying_gasp", "minor",
                                               f"ONT {o['serial']} dying gasp (power failure at premises)", source_type="ont",
                                               fsp=Fsp.parse(fsp), ont_index=int(idx), serial_number=o["serial"],
                                               vendor_severity="Minor", raised_at=_dt(o.get("state_since"))))
            return out

    def get_profiles(self) -> list[ProfileInfo]:
        with self._state() as st:
            self._check_reachable(st)
            return [ProfileInfo(kind, p["ref"], p["name"], p.get("parameters", {}))
                    for kind, items in st["profiles"].items() for p in items]

    # ------------------------------------------------------------------ discovery
    def discover_onts(self) -> list[DiscoveredOnt]:
        with self._state() as st:
            self._check_reachable(st)
            out = []
            for a in st["autofind"]:
                port = st["pon_ports"].get(a["fsp"], {})
                if port.get("fault"):
                    continue
                out.append(DiscoveredOnt(fsp=Fsp.parse(a["fsp"]), serial_number=a["serial"], vendor_id=a.get("vendor_id"),
                                         model=a.get("equipment_id"), equipment_id=a.get("equipment_id"),
                                         hardware_version=a.get("hw"), software_version=a.get("sw"), loid=a.get("loid"),
                                         rx_power_dbm=a.get("rx"), discovered_at=_dt(a.get("ts")),
                                         raw={k: v for k, v in a.items() if k != "password"}))
            return out

    # ------------------------------------------------------------------ provisioning
    def register_ont(self, req: OntRegistration, dry_run: bool = False) -> OpResult:
        with self._state(write=not dry_run) as st:
            self._check_reachable(st)
            fsp = str(req.fsp)
            if fsp not in st["pon_ports"]:
                raise AdapterError(f"PON port {fsp} does not exist on this OLT")
            existing = find_ont(st, req.serial_number)
            if existing:
                if existing[0] == fsp and (req.ont_index is None or str(req.ont_index) == existing[1]):
                    return OpResult(True, "ONT already registered with this serial on this port", changed=False,
                                    data={"ont_index": int(existing[1])})
                raise AdapterError(f"Serial {req.serial_number} is already registered at {existing[0]} ONT {existing[1]}")
            for kind, ref in (("line", req.line_profile_ref), ("service", req.service_profile_ref)):
                if not self._profile_exists(st, kind, ref):
                    raise AdapterError(f"{kind} profile {ref} does not exist on the OLT")
            onts = st["onts"].setdefault(fsp, {})
            if req.ont_index is not None:
                if str(req.ont_index) in onts:
                    raise AdapterError(f"ONT ID {req.ont_index} is already used on {fsp}")
                idx = req.ont_index
            else:
                idx = next(i for i in range(128) if str(i) not in onts)
            cmd = (f"sim> ont add {fsp} {idx} sn-auth {req.serial_number} line-profile {req.line_profile_ref} "
                   f"service-profile {req.service_profile_ref} desc \"{req.description}\"")
            if dry_run:
                return OpResult(True, "Validation passed (dry run, nothing changed)", {"ont_index": idx}, [cmd], changed=False)
            self._maybe_fail(st, "register_ont")
            af = next((a for a in st["autofind"] if a["serial"].upper() == req.serial_number.upper()), None)
            if af and af["fsp"] != fsp:
                raise AdapterError(f"ONT {req.serial_number} is physically detected on {af['fsp']}, not {fsp}")
            from app.adapters.simulator.device import make_ont

            ont = make_ont(req.serial_number, model=(af or {}).get("equipment_id", "SIM-ONT-HG8"),
                           state="online" if af else "registered_inactive", rx=(af or {}).get("rx", -20.0),
                           line_profile=req.line_profile_ref, service_profile=req.service_profile_ref, desc=req.description)
            if not af:
                ont["last_up"] = None
            onts[str(idx)] = ont
            if af:
                st["autofind"].remove(af)
            return OpResult(True, f"ONT registered as {fsp} ONT {idx}", {"ont_index": idx}, [cmd])

    def deregister_ont(self, fsp: Fsp, ont_index: int, dry_run: bool = False) -> OpResult:
        with self._state(write=not dry_run) as st:
            self._check_reachable(st)
            onts = st["onts"].get(str(fsp), {})
            ont = onts.get(str(ont_index))
            cmd = f"sim> ont delete {fsp} {ont_index}"
            if ont is None:
                return OpResult(True, "ONT not present on OLT (already deregistered)", changed=False, commands=[cmd])
            sps = [i for i, sp in st["service_ports"].items() if sp["fsp"] == str(fsp) and sp["ont"] == ont_index]
            if sps:
                raise AdapterError(f"ONT has service-ports {', '.join(sps)}; remove them first")
            if dry_run:
                return OpResult(True, "Validation passed (dry run)", commands=[cmd], changed=False)
            self._maybe_fail(st, "deregister_ont")
            del onts[str(ont_index)]
            if ont["state"] == "online":  # still physically connected -> shows up as unconfigured again
                st["autofind"].append({"fsp": str(fsp), "serial": ont["serial"], "vendor_id": ont.get("vendor_id"),
                                       "equipment_id": ont.get("equipment_id"), "hw": ont.get("hw"), "sw": ont.get("sw"),
                                       "rx": ont["rx_base"], "ts": datetime.now(timezone.utc).isoformat()})
            return OpResult(True, f"ONT {fsp}:{ont_index} deleted", commands=[cmd])

    def get_service_ports(self, fsp: Fsp, ont_index: int) -> list[ServicePortInfo]:
        with self._state() as st:
            self._check_reachable(st)
            out = []
            for idx, sp in st["service_ports"].items():
                if sp["fsp"] == str(fsp) and sp["ont"] == ont_index:
                    out.append(ServicePortInfo(int(idx), fsp, ont_index, sp["vlan"], sp.get("gem"), sp.get("user_vlan"),
                                               sp.get("inner_vlan"), sp.get("admin", "enabled"),
                                               "up" if sp.get("admin") == "enabled" else "down",
                                               sp.get("tt_up"), sp.get("tt_down")))
            return out

    def create_service_port(self, spec: ServicePortSpec, dry_run: bool = False) -> OpResult:
        with self._state(write=not dry_run) as st:
            self._check_reachable(st)
            fsp = str(spec.fsp)
            ont = st["onts"].get(fsp, {}).get(str(spec.ont_index))
            if ont is None:
                raise AdapterError(f"ONT {fsp}:{spec.ont_index} is not registered on this OLT")
            if not 1 <= spec.vlan <= 4094:
                raise AdapterError("VLAN must be between 1 and 4094")
            line = next((p for p in st["profiles"]["line"] if p["ref"] == ont.get("line_profile")), None)
            gems = (line or {}).get("parameters", {}).get("gem_ports", [1])
            if spec.gem_port is not None and spec.gem_port not in gems:
                raise AdapterError(f"GEM port {spec.gem_port} is not defined in line profile {ont.get('line_profile')}")
            for ref in (spec.traffic_up_ref, spec.traffic_down_ref):
                if not self._profile_exists(st, "traffic_table", ref):
                    raise AdapterError(f"Traffic table {ref} does not exist on the OLT")
            for idx, sp in st["service_ports"].items():
                if (sp["fsp"], sp["ont"], sp.get("gem"), sp["vlan"], sp.get("user_vlan")) == (
                        fsp, spec.ont_index, spec.gem_port, spec.vlan, spec.user_vlan):
                    return OpResult(True, f"Equivalent service-port {idx} already exists", {"index": int(idx)}, changed=False)
            if spec.index is not None and str(spec.index) in st["service_ports"]:
                raise AdapterError(f"Service-port index {spec.index} already in use")
            idx = spec.index if spec.index is not None else next(i for i in range(1, 65536) if str(i) not in st["service_ports"])
            cmd = (f"sim> service-port {idx} vlan {spec.vlan} gpon {fsp} ont {spec.ont_index} gemport {spec.gem_port} "
                   f"user-vlan {spec.user_vlan} inbound {spec.traffic_up_ref} outbound {spec.traffic_down_ref}")
            if dry_run:
                return OpResult(True, "Validation passed (dry run)", {"index": idx}, [cmd], changed=False)
            self._maybe_fail(st, "create_service_port")
            st["service_ports"][str(idx)] = {"fsp": fsp, "ont": spec.ont_index, "vlan": spec.vlan, "gem": spec.gem_port,
                                             "user_vlan": spec.user_vlan, "inner_vlan": spec.inner_vlan, "admin": "enabled",
                                             "tt_up": spec.traffic_up_ref, "tt_down": spec.traffic_down_ref}
            return OpResult(True, f"Service-port {idx} created", {"index": idx}, [cmd])

    def delete_service_port(self, index: int, dry_run: bool = False) -> OpResult:
        with self._state(write=not dry_run) as st:
            self._check_reachable(st)
            cmd = f"sim> undo service-port {index}"
            if str(index) not in st["service_ports"]:
                return OpResult(True, f"Service-port {index} does not exist (already removed)", changed=False, commands=[cmd])
            if dry_run:
                return OpResult(True, "Validation passed (dry run)", commands=[cmd], changed=False)
            self._maybe_fail(st, "delete_service_port")
            del st["service_ports"][str(index)]
            return OpResult(True, f"Service-port {index} deleted", commands=[cmd])

    def set_service_port_admin(self, index: int, enabled: bool, dry_run: bool = False) -> OpResult:
        with self._state(write=not dry_run) as st:
            self._check_reachable(st)
            sp = st["service_ports"].get(str(index))
            if sp is None:
                raise AdapterError(f"Service-port {index} does not exist")
            want = "enabled" if enabled else "disabled"
            cmd = f"sim> service-port {index} adminstatus {'enable' if enabled else 'disable'}"
            if sp.get("admin") == want:
                return OpResult(True, f"Service-port {index} already {want}", changed=False, commands=[cmd])
            if dry_run:
                return OpResult(True, "Validation passed (dry run)", commands=[cmd], changed=False)
            self._maybe_fail(st, "set_service_port_admin")
            sp["admin"] = want
            return OpResult(True, f"Service-port {index} {want}", commands=[cmd])

    # ------------------------------------------------------------------ administration
    def reboot_ont(self, fsp: Fsp, ont_index: int) -> OpResult:
        with self._state(write=True) as st:
            self._check_reachable(st)
            o = st["onts"].get(str(fsp), {}).get(str(ont_index))
            if o is None:
                raise AdapterError("ONT not registered on OLT")
            if self._effective_state(st, str(fsp), o) != "online":
                raise AdapterError("ONT is not online; reboot command cannot be delivered")
            self._maybe_fail(st, "reboot_ont")
            o["last_up"] = datetime.now(timezone.utc).isoformat()
            return OpResult(True, "Reboot command accepted by ONT", commands=[f"sim> ont reset {fsp} {ont_index}"])

    def set_ont_admin(self, fsp: Fsp, ont_index: int, enabled: bool) -> OpResult:
        with self._state(write=True) as st:
            self._check_reachable(st)
            o = st["onts"].get(str(fsp), {}).get(str(ont_index))
            if o is None:
                raise AdapterError("ONT not registered on OLT")
            want = "enabled" if enabled else "disabled"
            cmd = f"sim> ont {'activate' if enabled else 'deactivate'} {fsp} {ont_index}"
            if o.get("admin") == want:
                return OpResult(True, f"ONT already {want}", changed=False, commands=[cmd])
            o["admin"] = want
            return OpResult(True, f"ONT {want}", commands=[cmd])

    def backup_config(self) -> OpResult:
        with self._state() as st:
            self._check_reachable(st)
            lines = [f"# simulated configuration of {st['identity']['model']} {st['identity']['serial']}"]
            for fsp, onts in sorted(st["onts"].items()):
                for idx, o in sorted(onts.items(), key=lambda kv: int(kv[0])):
                    lines.append(f"ont add {fsp} {idx} sn-auth {o['serial']} line-profile {o.get('line_profile')} "
                                 f"service-profile {o.get('service_profile')} desc \"{o.get('desc', '')}\"")
            for idx, sp in sorted(st["service_ports"].items(), key=lambda kv: int(kv[0])):
                lines.append(f"service-port {idx} vlan {sp['vlan']} gpon {sp['fsp']} ont {sp['ont']} gemport {sp.get('gem')} "
                             f"user-vlan {sp.get('user_vlan')} adminstatus {sp.get('admin')}")
            return OpResult(True, "Configuration retrieved", {"config": "\n".join(lines)}, ["sim> display current-configuration"],
                            changed=False)
