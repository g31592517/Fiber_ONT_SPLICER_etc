"""Active network equipment: sites, OLTs and their hardware, ONTs, discovery, metrics."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, TimestampMixin, utcnow


class Site(Base, TimestampMixin):
    __tablename__ = "sites"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    code: Mapped[str | None] = mapped_column(String(32), unique=True)
    site_type: Mapped[str] = mapped_column(String(32), default="pop")  # pop | central_office | cabinet | street | other
    address: Mapped[str] = mapped_column(Text, default="")
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str] = mapped_column(Text, default="")
    is_simulated: Mapped[bool] = mapped_column(default=False)


class Olt(Base, TimestampMixin):
    __tablename__ = "olts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"), index=True)
    adapter: Mapped[str] = mapped_column(String(64))  # adapter key in the vendor registry
    manufacturer: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    serial_number: Mapped[str | None] = mapped_column(String(128))
    firmware_version: Mapped[str | None] = mapped_column(String(128))
    host: Mapped[str] = mapped_column(String(255))
    management_protocol: Mapped[str] = mapped_column(String(16), default="ssh")  # ssh | telnet | api | snmp | simulator
    management_port: Mapped[int | None]
    rack_location: Mapped[str] = mapped_column(String(128), default="")
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    slot_count: Mapped[int | None]
    pon_port_count: Mapped[int | None]
    admin_status: Mapped[str] = mapped_column(String(16), default="enabled")
    connection_status: Mapped[str] = mapped_column(String(16), default="unknown")
    last_contact_at: Mapped[datetime | None]           # last successful communication
    last_poll_at: Mapped[datetime | None]
    last_poll_error: Mapped[str | None] = mapped_column(Text)
    next_poll_at: Mapped[datetime | None]
    poll_interval_seconds: Mapped[int] = mapped_column(default=300)
    monitoring_enabled: Mapped[bool] = mapped_column(default=True)
    # Credentials — encrypted at rest, never returned by the API.
    username: Mapped[str | None] = mapped_column(String(128))
    password_enc: Mapped[str | None] = mapped_column(Text)
    enable_password_enc: Mapped[str | None] = mapped_column(Text)
    api_token_enc: Mapped[str | None] = mapped_column(Text)
    snmp_version: Mapped[str | None] = mapped_column(String(8))  # v3 | v2c | None
    snmp_port: Mapped[int] = mapped_column(default=161)
    snmp_community_enc: Mapped[str | None] = mapped_column(Text)
    snmp_v3_username: Mapped[str | None] = mapped_column(String(128))
    snmp_v3_auth_protocol: Mapped[str | None] = mapped_column(String(16))   # SHA | SHA256 | MD5
    snmp_v3_auth_key_enc: Mapped[str | None] = mapped_column(Text)
    snmp_v3_priv_protocol: Mapped[str | None] = mapped_column(String(16))   # AES | AES256 | DES
    snmp_v3_priv_key_enc: Mapped[str | None] = mapped_column(Text)
    # Latest device-reported health (None = unsupported / not retrieved)
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    memory_percent: Mapped[float | None] = mapped_column(Float)
    temperature_c: Mapped[float | None] = mapped_column(Float)
    uptime_seconds: Mapped[int | None]
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # adapter-specific, non-secret settings
    notes: Mapped[str] = mapped_column(Text, default="")
    is_simulated: Mapped[bool] = mapped_column(default=False)

    site: Mapped[Site | None] = relationship(lazy="selectin")
    boards: Mapped[list["OltBoard"]] = relationship(
        back_populates="olt", cascade="all, delete-orphan", order_by="OltBoard.slot"
    )


class OltBoard(Base, TimestampMixin):
    """Slot + board/card. ``installed=False`` represents an empty slot."""

    __tablename__ = "olt_boards"
    __table_args__ = (UniqueConstraint("olt_id", "frame", "slot"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    olt_id: Mapped[int] = mapped_column(ForeignKey("olts.id", ondelete="CASCADE"), index=True)
    frame: Mapped[int] = mapped_column(default=0)  # chassis / shelf
    slot: Mapped[int]
    installed: Mapped[bool] = mapped_column(default=True)
    board_type: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), default="pon")  # pon | uplink | control | power | other
    status: Mapped[str] = mapped_column(String(16), default="unknown")
    vendor_status: Mapped[str | None] = mapped_column(String(64))
    serial_number: Mapped[str | None] = mapped_column(String(128))
    hardware_version: Mapped[str | None] = mapped_column(String(64))
    software_version: Mapped[str | None] = mapped_column(String(64))
    port_count: Mapped[int | None]
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual | device
    last_seen_at: Mapped[datetime | None]
    olt: Mapped[Olt] = relationship(back_populates="boards")


class OltPort(Base, TimestampMixin):
    """PON and uplink ports. Addressed as frame/slot/port."""

    __tablename__ = "olt_ports"
    __table_args__ = (UniqueConstraint("olt_id", "frame", "slot", "port", "port_type"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    olt_id: Mapped[int] = mapped_column(ForeignKey("olts.id", ondelete="CASCADE"), index=True)
    board_id: Mapped[int | None] = mapped_column(ForeignKey("olt_boards.id", ondelete="SET NULL"))
    port_type: Mapped[str] = mapped_column(String(16), default="pon")  # pon | uplink
    technology: Mapped[str | None] = mapped_column(String(16))  # GPON | XGS-PON | EPON | 10GE ...
    frame: Mapped[int] = mapped_column(default=0)
    slot: Mapped[int]
    port: Mapped[int]
    label: Mapped[str] = mapped_column(String(128), default="")
    admin_status: Mapped[str] = mapped_column(String(16), default="enabled")
    oper_status: Mapped[str] = mapped_column(String(16), default="unknown")
    vendor_status: Mapped[str | None] = mapped_column(String(64))
    max_onts: Mapped[int | None]
    tx_power_dbm: Mapped[float | None] = mapped_column(Float)
    online_onts: Mapped[int | None]   # last poll
    source: Mapped[str] = mapped_column(String(16), default="manual")
    last_seen_at: Mapped[datetime | None]
    olt: Mapped[Olt] = relationship(lazy="selectin")

    @property
    def fsp(self) -> str:
        return f"{self.frame}/{self.slot}/{self.port}"


class OltProfile(Base, TimestampMixin):
    """Vendor-side profiles (line, service, DBA, traffic table) available on an OLT.

    These are references to objects that exist *on the equipment*. ``source=device`` rows
    were retrieved through an adapter; ``source=manual`` rows were entered by an administrator.
    """

    __tablename__ = "olt_profiles"
    __table_args__ = (UniqueConstraint("olt_id", "kind", "vendor_ref"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    olt_id: Mapped[int] = mapped_column(ForeignKey("olts.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # line | service | dba | traffic_table
    vendor_ref: Mapped[str] = mapped_column(String(64))  # e.g. profile ID / index on the device
    name: Mapped[str] = mapped_column(String(128), default="")
    parameters: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(16), default="manual")
    last_synced_at: Mapped[datetime | None]


class Vlan(Base, TimestampMixin):
    __tablename__ = "vlans"
    __table_args__ = (UniqueConstraint("olt_id", "vlan_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    vlan_id: Mapped[int]
    name: Mapped[str] = mapped_column(String(128), default="")
    purpose: Mapped[str] = mapped_column(String(32), default="internet")  # internet | management | voip | iptv | other
    olt_id: Mapped[int | None] = mapped_column(ForeignKey("olts.id", ondelete="CASCADE"))
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    description: Mapped[str] = mapped_column(Text, default="")


class Ont(Base, TimestampMixin):
    __tablename__ = "onts"
    __table_args__ = (
        UniqueConstraint("serial_number"),
        Index("ix_onts_olt_port", "olt_id", "pon_port_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    serial_number: Mapped[str] = mapped_column(String(64))
    olt_id: Mapped[int | None] = mapped_column(ForeignKey("olts.id", ondelete="SET NULL"))
    pon_port_id: Mapped[int | None] = mapped_column(ForeignKey("olt_ports.id", ondelete="SET NULL"))
    ont_index: Mapped[int | None]  # ONU/ONT ID on the PON port
    vendor: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(64))
    equipment_id: Mapped[str | None] = mapped_column(String(64))
    hardware_version: Mapped[str | None] = mapped_column(String(64))
    firmware_version: Mapped[str | None] = mapped_column(String(64))
    mac_address: Mapped[str | None] = mapped_column(String(32))
    auth_method: Mapped[str] = mapped_column(String(16), default="sn")  # sn | loid | password | sn_password
    loid: Mapped[str | None] = mapped_column(String(64))
    auth_password_enc: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(String(255), default="")
    line_profile_id: Mapped[int | None] = mapped_column(ForeignKey("olt_profiles.id", ondelete="SET NULL"))
    service_profile_id: Mapped[int | None] = mapped_column(ForeignKey("olt_profiles.id", ondelete="SET NULL"))
    registration_status: Mapped[str] = mapped_column(String(24), default="unregistered")
    # registration_status: unregistered | registered | deregistered
    admin_status: Mapped[str] = mapped_column(String(16), default="enabled")
    status: Mapped[str] = mapped_column(String(24), default="unknown")       # normalized OntState
    vendor_status: Mapped[str | None] = mapped_column(String(128))           # raw vendor state
    last_down_cause: Mapped[str | None] = mapped_column(String(128))
    status_changed_at: Mapped[datetime | None]
    last_online_at: Mapped[datetime | None]
    last_offline_at: Mapped[datetime | None]
    last_polled_at: Mapped[datetime | None]
    # latest optical snapshot (history in optical_readings)
    rx_power_dbm: Mapped[float | None] = mapped_column(Float)
    tx_power_dbm: Mapped[float | None] = mapped_column(Float)
    olt_rx_power_dbm: Mapped[float | None] = mapped_column(Float)
    temperature_c: Mapped[float | None] = mapped_column(Float)
    voltage_v: Mapped[float | None] = mapped_column(Float)
    bias_current_ma: Mapped[float | None] = mapped_column(Float)
    optical_status: Mapped[str | None] = mapped_column(String(16))  # ok | warning | critical | None
    distance_m: Mapped[int | None]
    installation_date: Mapped[date | None]
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    vendor_config: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # gem/tcont/etc. as reported or configured
    notes: Mapped[str] = mapped_column(Text, default="")
    is_simulated: Mapped[bool] = mapped_column(default=False)

    olt: Mapped[Olt | None] = relationship(lazy="selectin")
    pon_port: Mapped[OltPort | None] = relationship(lazy="selectin")
    line_profile: Mapped[OltProfile | None] = relationship(foreign_keys=[line_profile_id], lazy="selectin")
    service_profile: Mapped[OltProfile | None] = relationship(foreign_keys=[service_profile_id], lazy="selectin")


class OntDiscovery(Base, TimestampMixin):
    """An unregistered ONT reported by an OLT (autofind). Discovery is NOT registration."""

    __tablename__ = "ont_discoveries"
    __table_args__ = (UniqueConstraint("olt_id", "serial_number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    olt_id: Mapped[int] = mapped_column(ForeignKey("olts.id", ondelete="CASCADE"), index=True)
    pon_port_id: Mapped[int | None] = mapped_column(ForeignKey("olt_ports.id", ondelete="SET NULL"))
    serial_number: Mapped[str] = mapped_column(String(64))
    vendor_id: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))
    equipment_id: Mapped[str | None] = mapped_column(String(64))
    hardware_version: Mapped[str | None] = mapped_column(String(64))
    software_version: Mapped[str | None] = mapped_column(String(64))
    loid: Mapped[str | None] = mapped_column(String(64))
    rx_power_dbm: Mapped[float | None] = mapped_column(Float)
    vendor_reported: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # raw fields (secrets removed)
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    state: Mapped[str] = mapped_column(String(16), default="unregistered")  # unregistered | registered | ignored | gone
    registered_ont_id: Mapped[int | None] = mapped_column(ForeignKey("onts.id", ondelete="SET NULL"))
    is_simulated: Mapped[bool] = mapped_column(default=False)
    olt: Mapped[Olt] = relationship(lazy="selectin")
    pon_port: Mapped[OltPort | None] = relationship(lazy="selectin")


class OntStatusEvent(Base):
    __tablename__ = "ont_status_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ont_id: Mapped[int] = mapped_column(ForeignKey("onts.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24))
    vendor_status: Mapped[str | None] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(String(255))


class OpticalReading(Base):
    __tablename__ = "optical_readings"
    __table_args__ = (Index("ix_optical_ont_ts", "ont_id", "ts"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    ont_id: Mapped[int] = mapped_column(ForeignKey("onts.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    rx_power_dbm: Mapped[float | None] = mapped_column(Float)
    tx_power_dbm: Mapped[float | None] = mapped_column(Float)
    olt_rx_power_dbm: Mapped[float | None] = mapped_column(Float)
    temperature_c: Mapped[float | None] = mapped_column(Float)
    voltage_v: Mapped[float | None] = mapped_column(Float)
    bias_current_ma: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32))  # adapter key, e.g. "simulator"


class DeviceMetric(Base):
    """OLT-level health samples."""

    __tablename__ = "device_metrics"
    __table_args__ = (Index("ix_device_metrics_olt_ts", "olt_id", "ts"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    olt_id: Mapped[int] = mapped_column(ForeignKey("olts.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    reachable: Mapped[bool]
    latency_ms: Mapped[float | None] = mapped_column(Float)
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    memory_percent: Mapped[float | None] = mapped_column(Float)
    temperature_c: Mapped[float | None] = mapped_column(Float)
    onts_total: Mapped[int | None]
    onts_online: Mapped[int | None]
    source: Mapped[str] = mapped_column(String(32))


class PortMetric(Base):
    __tablename__ = "port_metrics"
    __table_args__ = (Index("ix_port_metrics_port_ts", "port_id", "ts"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    port_id: Mapped[int] = mapped_column(ForeignKey("olt_ports.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    oper_status: Mapped[str] = mapped_column(String(16))
    onts_online: Mapped[int | None]
    onts_total: Mapped[int | None]
    tx_power_dbm: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32))


class OpticalThreshold(Base, TimestampMixin):
    """Configurable optical thresholds. Most specific match wins: model > vendor > default."""

    __tablename__ = "optical_thresholds"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    vendor: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(64))
    rx_warning_low: Mapped[float] = mapped_column(Float)
    rx_critical_low: Mapped[float] = mapped_column(Float)
    rx_warning_high: Mapped[float | None] = mapped_column(Float)
    rx_critical_high: Mapped[float | None] = mapped_column(Float)
    olt_rx_warning_low: Mapped[float | None] = mapped_column(Float)
    olt_rx_critical_low: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str] = mapped_column(Text, default="")
