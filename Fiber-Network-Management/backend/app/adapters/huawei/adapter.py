"""Huawei SmartAX MA5600T/MA5800 GPON adapter over SSH CLI.

EXPERIMENTAL / UNVERIFIED — see ``cli.py``. Write operations are blocked by the core unless
``FNMS_ALLOW_UNVERIFIED_ADAPTER_WRITES=true``. No claim is made that this adapter has been
tested against hardware.
"""
from __future__ import annotations

import re
import socket
import time

from app.adapters.base import (
    AdapterConnectionError,
    AdapterError,
    Capability,
    CapabilityInfo,
    DeviceIdentity,
    Fsp,
    OltHealth,
    OntRegistration,
    OntStatusInfo,
    OpResult,
    OpticalInfo,
    ServicePortInfo,
    ServicePortSpec,
    VendorAdapter,
    Verification,
)
from app.adapters.huawei import cli

_U = Verification.UNVERIFIED
_NOTE = "Implemented from commonly published CLI syntax; not verified against Huawei documentation or hardware."

_PROMPT = re.compile(r"[\w\-\.]+(\([\w\-/]+\))?[#>]\s*$")
_MORE = re.compile(r"-+\s*More\s*\(.*?\)\s*-+|---- More ----")
_CR_PROMPT = re.compile(r"\{\s*<cr>.*\}:\s*$")
_CONFIRM = re.compile(r"\(y/n\)\[n\]:\s*$", re.I)
_ERROR = re.compile(r"(% ?(Unknown command|Parameter error|Failure)|Failure:|Error:)", re.I)


class SshCliSession:
    """Interactive SSH CLI session with Huawei-style paging/prompt handling."""

    def __init__(self, host: str, port: int, username: str, password: str, timeout: float):
        try:
            import paramiko
        except ImportError as exc:  # pragma: no cover
            raise AdapterConnectionError("paramiko is not installed") from exc
        self._client = paramiko.SSHClient()
        # Host keys: production deployments should pin known host keys (extra.known_hosts_file).
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self._client.connect(host, port=port, username=username, password=password, timeout=timeout,
                                 look_for_keys=False, allow_agent=False, banner_timeout=timeout, auth_timeout=timeout)
        except (OSError, socket.timeout) as exc:
            raise AdapterConnectionError(f"SSH connection to {host}:{port} failed: {exc.__class__.__name__}") from exc
        except Exception as exc:  # paramiko.AuthenticationException etc.
            raise AdapterConnectionError(f"SSH login to {host}:{port} failed: {exc.__class__.__name__}") from exc
        self._chan = self._client.invoke_shell(width=512, height=1000)
        self._timeout = timeout
        self._read_until_prompt()
        for c in ("enable", "undo smart", "scroll 512", "config"):
            self.run(c, check=False)

    def _read_until_prompt(self) -> str:
        buf, deadline = "", time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            if self._chan.recv_ready():
                buf += self._chan.recv(65535).decode(errors="replace")
                tail = buf[-200:]
                if _MORE.search(tail):
                    buf = _MORE.sub("", buf)
                    self._chan.send(" ")
                    continue
                if _CR_PROMPT.search(tail):
                    self._chan.send("\n")
                    continue
                if _CONFIRM.search(tail):
                    self._chan.send("y\n")
                    continue
                if _PROMPT.search(tail):
                    return buf
            else:
                time.sleep(0.05)
        raise AdapterConnectionError("Timed out waiting for CLI prompt", raw=buf[-500:])

    def run(self, command: str, check: bool = True) -> str:
        self._chan.send(command + "\n")
        out = self._read_until_prompt()
        if check and _ERROR.search(out):
            raise AdapterError(f"Device rejected command: {command.split()[0]} …", raw=out[-1000:])
        return out

    def close(self):
        try:
            self._client.close()
        except Exception:  # pragma: no cover
            pass


class HuaweiGponAdapter(VendorAdapter):
    key = "huawei_smartax_cli"
    display_name = "Huawei SmartAX GPON (SSH CLI) — experimental"
    vendor = "Huawei"
    supported_models = ["MA5600T", "MA5683T", "MA5800-X2", "MA5800-X7", "MA5800-X17"]
    protocols = ["ssh"]
    description = ("CLI adapter for Huawei SmartAX GPON OLTs. UNVERIFIED: built from commonly published "
                   "command syntax; not validated against official documentation or hardware.")
    experimental = True
    CAPABILITIES = {
        Capability.CONNECTION_TEST: CapabilityInfo(True, _U, "SSH login + prompt detection"),
        Capability.DEVICE_DISCOVERY: CapabilityInfo(True, _U, "display version / display board"),
        Capability.OLT_HEALTH: CapabilityInfo(True, _U, "Reachability and latency only; CPU/memory/temperature not implemented"),
        Capability.ONT_DISCOVERY: CapabilityInfo(True, _U, "display ont autofind all"),
        Capability.ONT_STATUS: CapabilityInfo(True, _U, "display ont info"),
        Capability.OPTICAL_POWER: CapabilityInfo(True, _U, "display ont optical-info"),
        Capability.ONT_REGISTRATION: CapabilityInfo(True, _U, "ont add … sn-auth … omci"),
        Capability.ONT_DEREGISTRATION: CapabilityInfo(True, _U, "ont delete"),
        Capability.SERVICE_PORT: CapabilityInfo(True, _U, "service-port / undo service-port"),
        Capability.SERVICE_ACTIVATION: CapabilityInfo(True, _U, "Registration + service-port creation"),
        Capability.SERVICE_SUSPENSION: CapabilityInfo(True, _U, "service-port adminstatus disable"),
        Capability.SERVICE_RESTORATION: CapabilityInfo(True, _U, "service-port adminstatus enable"),
        Capability.ALARM_RETRIEVAL: CapabilityInfo(True, _U, "Best-effort parser; unknown alarms kept as 'other'"),
        Capability.ONT_REBOOT: CapabilityInfo(True, _U, "ont reset"),
        Capability.ONT_ENABLE_DISABLE: CapabilityInfo(True, _U, "ont activate / deactivate"),
    }

    def __init__(self, conn):
        super().__init__(conn)
        self._session: SshCliSession | None = None

    def _s(self) -> SshCliSession:
        if self._session is None:
            if not self.conn.username or not self.conn.password:
                raise AdapterConnectionError("SSH username/password are not configured for this OLT")
            self._session = SshCliSession(self.conn.host, self.conn.port or 22, self.conn.username, self.conn.password,
                                          self.conn.timeout)
        return self._session

    def close(self):
        if self._session:
            self._session.close()
            self._session = None

    def _in_iface(self, fsp: Fsp, command: str, check: bool = True) -> str:
        s = self._s()
        s.run(cli.cmd_interface(fsp))
        try:
            return s.run(command, check=check)
        finally:
            s.run("quit", check=False)

    # -- connection / identity
    def test_connection(self):
        t0 = time.monotonic()
        self._s()
        return {"reachable": True, "latency_ms": round((time.monotonic() - t0) * 1000, 1), "protocol": "ssh",
                "message": "SSH CLI session established"}

    def get_identity(self) -> DeviceIdentity:
        v = cli.parse_version(self._s().run("display version", check=False))
        return DeviceIdentity(vendor="Huawei", model=v.get("PRODUCT"), firmware_version=v.get("VERSION"),
                              hostname=self.conn.host)

    def get_health(self) -> OltHealth:
        r = self.test_connection()
        return OltHealth(reachable=True, latency_ms=r["latency_ms"],
                         unsupported_metrics=["cpu_percent", "memory_percent", "temperature_c"])

    def get_boards(self):
        return cli.parse_boards(self._s().run("display board 0", check=False))

    # -- ONTs
    def discover_onts(self):
        return cli.parse_autofind(self._s().run("display ont autofind all", check=False))

    def get_onts(self) -> list[OntStatusInfo]:
        out: list[OntStatusInfo] = []
        for b in self.get_boards():
            if b.installed and b.role == "pon" and b.status == "normal":
                text = self._s().run(f"display ont info {b.frame} {b.slot} all", check=False)
                out.extend(cli.parse_ont_info_all(text))
        return out

    def get_ont(self, fsp: Fsp, ont_index: int):
        return cli.parse_ont_detail(self._in_iface(fsp, f"display ont info {fsp.port} {ont_index}", check=False),
                                    fsp, ont_index)

    def get_optical(self, fsp: Fsp, ont_index: int) -> OpticalInfo:
        return cli.parse_optical(self._in_iface(fsp, f"display ont optical-info {fsp.port} {ont_index}", check=False))

    def get_alarms(self):
        return cli.parse_active_alarms(self._s().run("display alarm active all", check=False))

    # -- provisioning
    def register_ont(self, req: OntRegistration, dry_run: bool = False) -> OpResult:
        if not req.line_profile_ref or not req.service_profile_ref:
            raise AdapterError("Huawei registration requires line and service profile IDs")
        serial = cli.normalize_serial(req.serial_number)
        cmd = cli.cmd_ont_add(req.fsp, req.ont_index, serial, req.line_profile_ref, req.service_profile_ref,
                              req.description, req.auth_method, req.loid, req.password)
        shown = cmd if not req.password else cmd.replace(req.password, "***")
        if dry_run:
            return OpResult(True, "Command generated (dry run). The device itself was not queried.", commands=[shown],
                            changed=False)
        out = self._in_iface(req.fsp, cmd)
        m = re.search(r"ONTID\s*:\s*(\d+)", out)
        if not m:
            raise AdapterError("ONT add did not report an ONT ID", raw=out[-1000:])
        return OpResult(True, "ont add accepted", {"ont_index": int(m.group(1))}, [shown])

    def deregister_ont(self, fsp: Fsp, ont_index: int, dry_run: bool = False) -> OpResult:
        cmd = cli.cmd_ont_delete(fsp, ont_index)
        if dry_run:
            return OpResult(True, "Command generated (dry run)", commands=[cmd], changed=False)
        self._in_iface(fsp, cmd)
        return OpResult(True, "ont delete accepted", commands=[cmd])

    def get_service_ports(self, fsp: Fsp, ont_index: int) -> list[ServicePortInfo]:
        return cli.parse_service_ports(self._s().run(f"display service-port port {fsp} ont {ont_index}", check=False))

    def create_service_port(self, spec: ServicePortSpec, dry_run: bool = False) -> OpResult:
        if spec.gem_port is None:
            raise AdapterError("Huawei service-port requires a GEM port")
        cmd = cli.cmd_service_port(spec.index, spec.vlan, spec.fsp, spec.ont_index, spec.gem_port, spec.user_vlan,
                                   spec.traffic_up_ref, spec.traffic_down_ref)
        if dry_run:
            return OpResult(True, "Command generated (dry run)", commands=[cmd], changed=False)
        before = {sp.index for sp in self.get_service_ports(spec.fsp, spec.ont_index)}
        self._s().run(cmd)
        after = self.get_service_ports(spec.fsp, spec.ont_index)
        new = [sp for sp in after if sp.index not in before and sp.vlan == spec.vlan]
        if not new:
            raise AdapterError("service-port command accepted but no new service-port was found on read-back")
        return OpResult(True, "service-port created", {"index": new[0].index}, [cmd])

    def delete_service_port(self, index: int, dry_run: bool = False) -> OpResult:
        cmd = f"undo service-port {index}"
        if dry_run:
            return OpResult(True, "Command generated (dry run)", commands=[cmd], changed=False)
        self._s().run(cmd)
        return OpResult(True, "service-port deleted", commands=[cmd])

    def set_service_port_admin(self, index: int, enabled: bool, dry_run: bool = False) -> OpResult:
        cmd = cli.cmd_service_port_admin(index, enabled)
        if dry_run:
            return OpResult(True, "Command generated (dry run)", commands=[cmd], changed=False)
        self._s().run(cmd)
        return OpResult(True, f"service-port {'enabled' if enabled else 'disabled'}", commands=[cmd])

    def reboot_ont(self, fsp: Fsp, ont_index: int) -> OpResult:
        cmd = cli.cmd_ont_reset(fsp, ont_index)
        self._in_iface(fsp, cmd)
        return OpResult(True, "ont reset accepted", commands=[cmd])

    def set_ont_admin(self, fsp: Fsp, ont_index: int, enabled: bool) -> OpResult:
        cmd = cli.cmd_ont_admin(fsp, ont_index, enabled)
        self._in_iface(fsp, cmd)
        return OpResult(True, "accepted", commands=[cmd])
