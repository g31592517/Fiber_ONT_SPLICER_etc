"""Vendor-neutral monitoring adapter using standard MIBs (SNMPv2-MIB, IF-MIB) and ICMP.

It deliberately implements *monitoring only*: SNMP standard MIBs expose identity, uptime and
interface state, but not GPON provisioning. Use it for OLTs whose vendor adapter is not yet
available, to get reachability and interface monitoring without claiming ONT capabilities.
"""
from __future__ import annotations

import asyncio
import platform
import re
import shutil
import subprocess

from app.adapters.base import (
    AdapterConnectionError,
    Capability,
    CapabilityInfo,
    DeviceIdentity,
    OltHealth,
    PortInfo,
    VendorAdapter,
    Verification,
)

_U = Verification.UNVERIFIED
SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"
IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
IF_ADMIN = "1.3.6.1.2.1.2.2.1.7"
IF_OPER = "1.3.6.1.2.1.2.2.1.8"

_AUTH = {"MD5": "usmHMACMD5AuthProtocol", "SHA": "usmHMACSHAAuthProtocol", "SHA256": "usmHMAC192SHA256AuthProtocol",
         "SHA512": "usmHMAC384SHA512AuthProtocol"}
_PRIV = {"DES": "usmDESPrivProtocol", "AES": "usmAesCfb128Protocol", "AES256": "usmAesCfb256Protocol"}


def icmp_ping(host: str, timeout: float = 2.0) -> float | None:
    """Return round-trip time in ms, or None if unreachable / ping unavailable."""
    exe = shutil.which("ping")
    if not exe:
        return None
    args = [exe, "-n" if platform.system() == "Windows" else "-c", "1", "-W", str(max(1, int(timeout))), host]
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout + 2).stdout
    except (subprocess.TimeoutExpired, OSError):
        return None
    m = re.search(r"time[=<]([\d.]+)\s*ms", out)
    return float(m.group(1)) if m else None


class SnmpStandardAdapter(VendorAdapter):
    key = "snmp_standard"
    display_name = "Standard SNMP + ICMP (monitoring only)"
    vendor = "Any"
    supported_models = ["Any SNMP-capable OLT"]
    protocols = ["snmp", "icmp"]
    description = ("Monitors reachability (ICMP), identity/uptime (SNMPv2-MIB) and interface state (IF-MIB). "
                   "Does not provide ONT discovery, status or provisioning.")
    CAPABILITIES = {
        Capability.CONNECTION_TEST: CapabilityInfo(True, _U, "ICMP + SNMP sysDescr"),
        Capability.DEVICE_DISCOVERY: CapabilityInfo(True, _U, "sysDescr/sysName/sysUpTime only; no board inventory"),
        Capability.OLT_HEALTH: CapabilityInfo(True, _U, "Reachability, latency and uptime; CPU/memory/temperature are vendor MIBs"),
        Capability.PON_PORT_MONITORING: CapabilityInfo(True, _U, "IF-MIB ifAdminStatus/ifOperStatus; port type not distinguished"),
    }

    # -- SNMP primitives (overridable in tests)
    def _auth(self):
        from pysnmp.hlapi.v3arch import asyncio as h

        c = self.conn
        if c.snmp_version == "v3":
            kwargs = {}
            if c.snmp_v3_auth_key:
                kwargs["authKey"] = c.snmp_v3_auth_key
                kwargs["authProtocol"] = getattr(h, _AUTH.get((c.snmp_v3_auth_protocol or "SHA").upper(), _AUTH["SHA"]))
            if c.snmp_v3_priv_key:
                kwargs["privKey"] = c.snmp_v3_priv_key
                kwargs["privProtocol"] = getattr(h, _PRIV.get((c.snmp_v3_priv_protocol or "AES").upper(), _PRIV["AES"]))
            return h.UsmUserData(c.snmp_v3_username or "", **kwargs)
        if c.snmp_version == "v2c" and c.snmp_community:
            return h.CommunityData(c.snmp_community, mpModel=1)
        raise AdapterConnectionError("SNMP is not configured for this OLT (set SNMPv3 credentials or a v2c community)")

    def _snmp_get(self, oids: list[str]) -> dict[str, str]:
        from pysnmp.hlapi.v3arch import asyncio as h

        async def run():
            engine = h.SnmpEngine()
            target = await h.UdpTransportTarget.create((self.conn.host, self.conn.snmp_port), timeout=self.conn.timeout / 3, retries=1)
            err, status, _idx, binds = await h.get_cmd(engine, self._auth(), target, h.ContextData(),
                                                       *[h.ObjectType(h.ObjectIdentity(o)) for o in oids])
            engine.close_dispatcher()
            if err:
                raise AdapterConnectionError(f"SNMP error: {err}")
            if status:
                raise AdapterConnectionError(f"SNMP error status: {status.prettyPrint()}")
            return {str(n): v.prettyPrint() for n, v in binds}

        return asyncio.run(run())

    def _snmp_walk(self, oid: str) -> dict[str, str]:
        from pysnmp.hlapi.v3arch import asyncio as h

        async def run():
            engine = h.SnmpEngine()
            target = await h.UdpTransportTarget.create((self.conn.host, self.conn.snmp_port), timeout=self.conn.timeout / 3, retries=1)
            out = {}
            async for err, status, _idx, binds in h.walk_cmd(engine, self._auth(), target, h.ContextData(),
                                                            h.ObjectType(h.ObjectIdentity(oid)), lexicographicMode=False):
                if err or status:
                    raise AdapterConnectionError(f"SNMP walk error: {err or status.prettyPrint()}")
                for n, v in binds:
                    out[str(n)] = v.prettyPrint()
            engine.close_dispatcher()
            return out

        return asyncio.run(run())

    # -- contract
    def test_connection(self):
        rtt = icmp_ping(self.conn.host)
        descr = self._snmp_get([SYS_DESCR])
        return {"reachable": True, "latency_ms": rtt, "protocol": f"snmp{self.conn.snmp_version}",
                "message": f"SNMP OK: {next(iter(descr.values()), '')[:120]}"}

    def get_identity(self) -> DeviceIdentity:
        v = self._snmp_get([SYS_DESCR, SYS_NAME, SYS_UPTIME])
        up = v.get(SYS_UPTIME)
        return DeviceIdentity(description=v.get(SYS_DESCR), hostname=v.get(SYS_NAME),
                              uptime_seconds=int(up) // 100 if up and up.isdigit() else None)

    def get_health(self) -> OltHealth:
        rtt = icmp_ping(self.conn.host)
        v = self._snmp_get([SYS_UPTIME])  # raises AdapterConnectionError if SNMP unreachable
        up = v.get(SYS_UPTIME)
        return OltHealth(reachable=True, latency_ms=rtt, uptime_seconds=int(up) // 100 if up and up.isdigit() else None,
                         unsupported_metrics=["cpu_percent", "memory_percent", "temperature_c"])

    def get_ports(self) -> list[PortInfo]:
        descr, admin, oper = self._snmp_walk(IF_DESCR), self._snmp_walk(IF_ADMIN), self._snmp_walk(IF_OPER)
        out = []
        for oid, name in descr.items():
            idx = oid.rsplit(".", 1)[-1]
            a = admin.get(f"{IF_ADMIN}.{idx}")
            o = oper.get(f"{IF_OPER}.{idx}")
            out.append(PortInfo(0, 0, int(idx), "uplink", None, "enabled" if a in ("1", "up") else "disabled",
                                "up" if o in ("1", "up") else "down" if o in ("2", "down") else "unknown", o, label=name))
        return out
