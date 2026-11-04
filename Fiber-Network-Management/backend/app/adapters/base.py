"""Vendor adapter contract.

Every OLT manufacturer / management platform is integrated through a subclass of
:class:`VendorAdapter`. The core (monitoring, provisioning, topology) only ever talks to
this interface and the vendor-neutral data classes below — never to vendor CLI/MIBs.

Rules enforced here:
* An adapter declares its capabilities explicitly, each with a verification level.
* Every operation not implemented raises :class:`UnsupportedOperation` (a structured
  "unsupported" result), never a fake success.
* Adapters are synchronous and are only executed from background workers.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Capability(StrEnum):
    CONNECTION_TEST = "connection_test"
    DEVICE_DISCOVERY = "device_discovery"          # identity + hardware inventory (boards/ports)
    OLT_HEALTH = "olt_health_monitoring"           # reachability, CPU, memory, temperature
    PON_PORT_MONITORING = "pon_port_monitoring"
    ONT_DISCOVERY = "ont_discovery"                # unregistered/autofind ONTs
    ONT_REGISTRATION = "ont_registration"
    ONT_DEREGISTRATION = "ont_deregistration"
    ONT_STATUS = "ont_status"
    OPTICAL_POWER = "optical_power"
    PROFILE_RETRIEVAL = "profile_retrieval"        # line/service/DBA profiles, traffic tables
    VLAN_CONFIGURATION = "vlan_configuration"
    BANDWIDTH_PROFILE = "bandwidth_profile_configuration"
    SERVICE_PORT = "service_port_configuration"
    SERVICE_ACTIVATION = "service_activation"
    SERVICE_SUSPENSION = "service_suspension"
    SERVICE_RESTORATION = "service_restoration"
    ALARM_RETRIEVAL = "alarm_retrieval"
    CONFIG_BACKUP = "configuration_backup"
    ONT_REBOOT = "ont_reboot"
    ONT_ENABLE_DISABLE = "ont_enable_disable"


WRITE_CAPABILITIES = {
    Capability.ONT_REGISTRATION, Capability.ONT_DEREGISTRATION, Capability.VLAN_CONFIGURATION,
    Capability.BANDWIDTH_PROFILE, Capability.SERVICE_PORT, Capability.SERVICE_ACTIVATION,
    Capability.SERVICE_SUSPENSION, Capability.SERVICE_RESTORATION, Capability.ONT_REBOOT,
    Capability.ONT_ENABLE_DISABLE,
}


class Verification(StrEnum):
    HARDWARE_VERIFIED = "hardware_verified"   # exercised against real equipment
    SIMULATED = "simulated"                   # exercised only against the built-in simulator
    FIXTURE_TESTED = "fixture_tested"         # parsers tested against sample outputs, never against hardware
    UNVERIFIED = "unverified"                 # implemented, not verified by any means


@dataclass
class CapabilityInfo:
    supported: bool
    verification: Verification | None = None
    notes: str = ""


# ----------------------------------------------------------------------------- errors
class AdapterError(Exception):
    """An operation was attempted and failed on the device side."""

    def __init__(self, message: str, *, retryable: bool = False, raw: str | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.raw = raw


class AdapterConnectionError(AdapterError):
    """Could not communicate with the device at all (timeout, auth failure, unreachable)."""

    def __init__(self, message: str, raw: str | None = None):
        super().__init__(message, retryable=True, raw=raw)


class UnsupportedOperation(Exception):
    def __init__(self, adapter: str, capability: Capability | str, reason: str = ""):
        self.adapter = adapter
        self.capability = str(capability)
        self.reason = reason or f"Adapter '{adapter}' does not implement '{capability}'"
        super().__init__(self.reason)

    def as_result(self) -> dict:
        return {"status": "unsupported", "adapter": self.adapter, "capability": self.capability, "reason": self.reason}


# ----------------------------------------------------------------------------- DTOs
@dataclass
class ConnectionConfig:
    host: str
    port: int | None = None
    protocol: str = "ssh"
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    enable_password: str | None = field(default=None, repr=False)
    api_token: str | None = field(default=None, repr=False)
    snmp_version: str | None = None
    snmp_port: int = 161
    snmp_community: str | None = field(default=None, repr=False)
    snmp_v3_username: str | None = None
    snmp_v3_auth_protocol: str | None = None
    snmp_v3_auth_key: str | None = field(default=None, repr=False)
    snmp_v3_priv_protocol: str | None = None
    snmp_v3_priv_key: str | None = field(default=None, repr=False)
    timeout: float = 15.0
    model: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Fsp:
    frame: int
    slot: int
    port: int

    def __str__(self) -> str:
        return f"{self.frame}/{self.slot}/{self.port}"

    @classmethod
    def parse(cls, s: str) -> "Fsp":
        f, sl, p = (int(x) for x in s.strip().split("/"))
        return cls(f, sl, p)


@dataclass
class DeviceIdentity:
    vendor: str | None = None
    model: str | None = None
    serial_number: str | None = None
    firmware_version: str | None = None
    hostname: str | None = None
    description: str | None = None
    uptime_seconds: int | None = None


@dataclass
class OltHealth:
    reachable: bool
    latency_ms: float | None = None
    cpu_percent: float | None = None
    memory_percent: float | None = None
    temperature_c: float | None = None
    uptime_seconds: int | None = None
    unsupported_metrics: list[str] = field(default_factory=list)


@dataclass
class BoardInfo:
    frame: int
    slot: int
    installed: bool
    board_type: str | None = None
    role: str = "pon"
    status: str = "unknown"          # normalized BoardStatus
    vendor_status: str | None = None
    port_count: int | None = None
    serial_number: str | None = None
    hardware_version: str | None = None
    software_version: str | None = None


@dataclass
class PortInfo:
    frame: int
    slot: int
    port: int
    port_type: str = "pon"
    technology: str | None = None
    admin_status: str = "enabled"
    oper_status: str = "unknown"     # normalized PortOperStatus
    vendor_status: str | None = None
    tx_power_dbm: float | None = None
    max_onts: int | None = None
    onts_total: int | None = None
    onts_online: int | None = None
    label: str | None = None


@dataclass
class DiscoveredOnt:
    fsp: Fsp
    serial_number: str
    vendor_id: str | None = None
    model: str | None = None
    equipment_id: str | None = None
    hardware_version: str | None = None
    software_version: str | None = None
    loid: str | None = None
    rx_power_dbm: float | None = None
    discovered_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class OntStatusInfo:
    fsp: Fsp
    ont_index: int
    serial_number: str
    state: str                      # normalized OntState
    vendor_state: str | None = None
    admin_status: str = "enabled"
    last_down_cause: str | None = None
    last_up_at: datetime | None = None
    last_down_at: datetime | None = None
    distance_m: int | None = None
    description: str | None = None
    line_profile_ref: str | None = None
    service_profile_ref: str | None = None
    vendor: str | None = None
    model: str | None = None
    equipment_id: str | None = None
    hardware_version: str | None = None
    firmware_version: str | None = None
    mac_address: str | None = None


@dataclass
class OpticalInfo:
    rx_power_dbm: float | None = None
    tx_power_dbm: float | None = None
    olt_rx_power_dbm: float | None = None
    temperature_c: float | None = None
    voltage_v: float | None = None
    bias_current_ma: float | None = None
    unsupported_fields: list[str] = field(default_factory=list)


@dataclass
class AlarmRecord:
    vendor_alarm_id: str
    category: str                    # normalized AlarmCategory
    severity: str                    # normalized Severity
    description: str
    source_type: str = "olt"         # olt | board | olt_port | ont
    fsp: Fsp | None = None
    slot: int | None = None
    ont_index: int | None = None
    serial_number: str | None = None
    vendor_severity: str | None = None
    raised_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProfileInfo:
    kind: str                        # line | service | dba | traffic_table
    ref: str
    name: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class OntRegistration:
    fsp: Fsp
    serial_number: str
    ont_index: int | None = None     # adapter allocates the lowest free index when None
    auth_method: str = "sn"
    loid: str | None = None
    password: str | None = field(default=None, repr=False)
    line_profile_ref: str | None = None
    service_profile_ref: str | None = None
    description: str = ""


@dataclass
class ServicePortSpec:
    fsp: Fsp
    ont_index: int
    vlan: int                        # network-side VLAN (S-VLAN in QinQ)
    gem_port: int | None = None
    user_vlan: int | None = None
    inner_vlan: int | None = None    # C-VLAN for QinQ
    vlan_mode: str = "single"
    traffic_up_ref: str | None = None
    traffic_down_ref: str | None = None
    index: int | None = None         # adapter allocates when None
    description: str = ""


@dataclass
class ServicePortInfo:
    index: int
    fsp: Fsp
    ont_index: int
    vlan: int
    gem_port: int | None = None
    user_vlan: int | None = None
    inner_vlan: int | None = None
    admin_status: str = "enabled"
    oper_status: str | None = None
    traffic_up_ref: str | None = None
    traffic_down_ref: str | None = None


@dataclass
class OpResult:
    """Result of a write operation. ``commands`` lists what was sent (secrets masked)."""

    ok: bool
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    changed: bool = True             # False when the device was already in the desired state


def to_dict(obj) -> dict:
    d = asdict(obj)

    def conv(v):
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        if isinstance(v, list):
            return [conv(x) for x in v]
        return v

    out = conv(d)
    for k, v in list(out.items()):
        if isinstance(v, dict) and set(v) == {"frame", "slot", "port"}:
            out[k] = f"{v['frame']}/{v['slot']}/{v['port']}"
    return out


# ----------------------------------------------------------------------------- adapter base
class VendorAdapter:
    key: str = "base"
    display_name: str = "Base adapter"
    vendor: str = ""
    supported_models: list[str] = []
    protocols: list[str] = []
    description: str = ""
    experimental: bool = False       # True => writes are blocked unless explicitly allowed by configuration
    planned: bool = False            # True => architectural target only; cannot be selected

    #: capability -> CapabilityInfo. Anything absent is unsupported.
    CAPABILITIES: dict[Capability, CapabilityInfo] = {}

    def __init__(self, conn: ConnectionConfig):
        self.conn = conn

    # -- capability helpers
    @classmethod
    def capabilities(cls) -> dict[str, CapabilityInfo]:
        out = {}
        for cap in Capability:
            out[cap.value] = cls.CAPABILITIES.get(cap, CapabilityInfo(False, None, "Not implemented"))
        return out

    @classmethod
    def supports(cls, cap: Capability) -> bool:
        info = cls.CAPABILITIES.get(cap)
        return bool(info and info.supported)

    def _unsupported(self, cap: Capability):
        raise UnsupportedOperation(self.key, cap)

    # -- lifecycle
    def close(self) -> None:  # pragma: no cover - overridden by transport-based adapters
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- connection
    def test_connection(self) -> dict[str, Any]:
        self._unsupported(Capability.CONNECTION_TEST)

    def get_identity(self) -> DeviceIdentity:
        self._unsupported(Capability.DEVICE_DISCOVERY)

    # -- monitoring
    def get_health(self) -> OltHealth:
        self._unsupported(Capability.OLT_HEALTH)

    def get_boards(self) -> list[BoardInfo]:
        self._unsupported(Capability.DEVICE_DISCOVERY)

    def get_ports(self) -> list[PortInfo]:
        self._unsupported(Capability.PON_PORT_MONITORING)

    def get_onts(self) -> list[OntStatusInfo]:
        """All ONTs registered (configured) on the OLT, with their current status."""
        self._unsupported(Capability.ONT_STATUS)

    def get_ont(self, fsp: Fsp, ont_index: int) -> OntStatusInfo | None:
        self._unsupported(Capability.ONT_STATUS)

    def get_optical(self, fsp: Fsp, ont_index: int) -> OpticalInfo:
        self._unsupported(Capability.OPTICAL_POWER)

    def get_alarms(self) -> list[AlarmRecord]:
        self._unsupported(Capability.ALARM_RETRIEVAL)

    def get_profiles(self) -> list[ProfileInfo]:
        self._unsupported(Capability.PROFILE_RETRIEVAL)

    # -- discovery
    def discover_onts(self) -> list[DiscoveredOnt]:
        self._unsupported(Capability.ONT_DISCOVERY)

    # -- provisioning
    def register_ont(self, req: OntRegistration, dry_run: bool = False) -> OpResult:
        self._unsupported(Capability.ONT_REGISTRATION)

    def deregister_ont(self, fsp: Fsp, ont_index: int, dry_run: bool = False) -> OpResult:
        self._unsupported(Capability.ONT_DEREGISTRATION)

    def get_service_ports(self, fsp: Fsp, ont_index: int) -> list[ServicePortInfo]:
        self._unsupported(Capability.SERVICE_PORT)

    def create_service_port(self, spec: ServicePortSpec, dry_run: bool = False) -> OpResult:
        self._unsupported(Capability.SERVICE_PORT)

    def delete_service_port(self, index: int, dry_run: bool = False) -> OpResult:
        self._unsupported(Capability.SERVICE_PORT)

    def set_service_port_admin(self, index: int, enabled: bool, dry_run: bool = False) -> OpResult:
        self._unsupported(Capability.SERVICE_SUSPENSION if not enabled else Capability.SERVICE_RESTORATION)

    # -- administration
    def reboot_ont(self, fsp: Fsp, ont_index: int) -> OpResult:
        self._unsupported(Capability.ONT_REBOOT)

    def set_ont_admin(self, fsp: Fsp, ont_index: int, enabled: bool) -> OpResult:
        self._unsupported(Capability.ONT_ENABLE_DISABLE)

    def backup_config(self) -> OpResult:
        self._unsupported(Capability.CONFIG_BACKUP)
