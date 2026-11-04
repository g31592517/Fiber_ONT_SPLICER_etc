"""Adapter registry: maps adapter keys to implementations, and lists planned targets."""
from __future__ import annotations

from app.adapters.base import Capability, ConnectionConfig, VendorAdapter
from app.adapters.huawei.adapter import HuaweiGponAdapter
from app.adapters.simulator.adapter import SimulatorAdapter
from app.adapters.snmp.adapter import SnmpStandardAdapter
from app.config import get_settings
from app.core.errors import NotFound, ValidationFailed


def _planned(key: str, name: str, vendor: str, models: list[str], protocols: list[str], note: str) -> type[VendorAdapter]:
    return type(f"Planned_{key}", (VendorAdapter,), {
        "key": key, "display_name": name, "vendor": vendor, "supported_models": models, "protocols": protocols,
        "description": note, "planned": True, "CAPABILITIES": {},
    })


PLANNED = [
    _planned("zte_cli", "ZTE ZXA10 C300/C600", "ZTE", ["C300", "C320", "C600"], ["ssh", "telnet", "snmp"],
             "Architectural target. Not implemented: requires ZTE CLI/MIB documentation and test equipment."),
    _planned("fiberhome_tl1", "FiberHome AN5516 / AN6000", "FiberHome", ["AN5516-01", "AN5516-06", "AN6000"], ["tl1", "snmp"],
             "Architectural target. FiberHome OLTs are commonly managed through the UNM2000 TL1 northbound interface."),
    _planned("vsol_cli", "V-SOL GPON/EPON OLT", "V-SOL", ["V1600G", "V1600D"], ["ssh", "telnet", "snmp"],
             "Architectural target. Not implemented."),
    _planned("nokia_isam", "Nokia ISAM / Lightspan", "Nokia", ["7360 ISAM FX", "Lightspan MF"], ["netconf", "tl1", "snmp"],
             "Architectural target. Not implemented."),
    _planned("calix_smx", "Calix SMx (management platform)", "Calix", ["E7", "E9", "AXOS systems via SMx"], ["rest"],
             "Architectural target. Integrates with the Calix SMx northbound API rather than individual OLTs."),
    _planned("adtran_mosaic", "Adtran Mosaic Cloud Platform", "Adtran", ["SDX series via Mosaic CP"], ["rest"],
             "Architectural target. Integrates with Mosaic CP northbound APIs rather than individual OLTs."),
]

_ACTIVE: dict[str, type[VendorAdapter]] = {a.key: a for a in (SimulatorAdapter, HuaweiGponAdapter, SnmpStandardAdapter)}
_ALL: dict[str, type[VendorAdapter]] = {**_ACTIVE, **{p.key: p for p in PLANNED}}


def adapter_class(key: str) -> type[VendorAdapter]:
    cls = _ALL.get(key)
    if cls is None:
        raise NotFound(f"Unknown adapter '{key}'")
    return cls


def selectable_adapters() -> list[type[VendorAdapter]]:
    s = get_settings()
    return [a for a in _ACTIVE.values() if a.key != "simulator" or s.simulator_enabled]


def all_adapters() -> list[type[VendorAdapter]]:
    return list(_ALL.values())


def validate_adapter_key(key: str) -> type[VendorAdapter]:
    cls = adapter_class(key)
    if cls.planned:
        raise ValidationFailed(f"Adapter '{key}' is a planned integration and is not implemented yet")
    if key == "simulator" and not get_settings().simulator_enabled:
        raise ValidationFailed("The simulator is disabled in this deployment")
    return cls


def write_allowed(cls: type[VendorAdapter], cap: Capability) -> tuple[bool, str]:
    if not cls.supports(cap):
        return False, f"{cls.display_name} does not support {cap.value}"
    if cls.experimental and not get_settings().allow_unverified_adapter_writes:
        return False, (f"{cls.display_name} is experimental/unverified; write operations are disabled. "
                       "Set FNMS_ALLOW_UNVERIFIED_ADAPTER_WRITES=true after validating it against your equipment.")
    return True, ""


def build_adapter(olt) -> VendorAdapter:
    """Instantiate the adapter for an OLT with decrypted credentials. Worker-side only."""
    from app.security.crypto import decrypt_secret

    cls = validate_adapter_key(olt.adapter)
    conn = ConnectionConfig(
        host=olt.host, port=olt.management_port, protocol=olt.management_protocol, username=olt.username,
        password=decrypt_secret(olt.password_enc), enable_password=decrypt_secret(olt.enable_password_enc),
        api_token=decrypt_secret(olt.api_token_enc), snmp_version=olt.snmp_version, snmp_port=olt.snmp_port or 161,
        snmp_community=decrypt_secret(olt.snmp_community_enc), snmp_v3_username=olt.snmp_v3_username,
        snmp_v3_auth_protocol=olt.snmp_v3_auth_protocol, snmp_v3_auth_key=decrypt_secret(olt.snmp_v3_auth_key_enc),
        snmp_v3_priv_protocol=olt.snmp_v3_priv_protocol, snmp_v3_priv_key=decrypt_secret(olt.snmp_v3_priv_key_enc),
        model=olt.model, extra=dict(olt.extra or {}),
    )
    return cls(conn)
