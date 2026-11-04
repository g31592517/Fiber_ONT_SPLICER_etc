"""Subscribers/services, service profiles & templates, provisioning, alarms, incidents, jobs, simulator."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, TimestampMixin, utcnow
from app.models.network import Ont


# --------------------------------------------------------------------------- subscribers
class Subscriber(Base, TimestampMixin):
    """Lightweight subscriber reference. The billing system remains authoritative."""

    __tablename__ = "subscribers"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_ref: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    account_ref: Mapped[str | None] = mapped_column(String(64))
    external_customer_id: Mapped[str | None] = mapped_column(String(64), index=True)
    external_account_id: Mapped[str | None] = mapped_column(String(64))
    installation_address: Mapped[str] = mapped_column(Text, default="")
    latitude: Mapped[float | None]
    longitude: Mapped[float | None]
    contact_phone: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str] = mapped_column(Text, default="")
    is_simulated: Mapped[bool] = mapped_column(default=False)


class BandwidthProfile(Base, TimestampMixin):
    """Vendor-neutral speed definition (e.g. 50/20 Mbps)."""

    __tablename__ = "bandwidth_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    download_kbps: Mapped[int]
    upload_kbps: Mapped[int]
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(default=True)


class ServiceTemplate(Base, TimestampMixin):
    """Vendor-neutral description of the configuration needed to activate a service."""

    __tablename__ = "service_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    service_type: Mapped[str] = mapped_column(String(16), default="internet")  # internet | voip | iptv | management
    bandwidth_profile_id: Mapped[int | None] = mapped_column(ForeignKey("bandwidth_profiles.id"))
    vlan_mode: Mapped[str] = mapped_column(String(16), default="single")  # single | qinq | transparent
    svlan: Mapped[int | None]          # service / outer VLAN (or the only VLAN in single mode)
    cvlan: Mapped[int | None]          # customer / inner VLAN (QinQ)
    user_vlan: Mapped[int | None]      # VLAN on the ONT UNI side
    gem_port: Mapped[int | None]
    tcont: Mapped[int | None]
    wan_mode: Mapped[str | None] = mapped_column(String(16))  # pppoe | dhcp | static | bridge
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(default=True)
    bandwidth_profile: Mapped[BandwidthProfile | None] = relationship(lazy="selectin")
    mappings: Mapped[list["TemplateVendorMapping"]] = relationship(
        back_populates="template", cascade="all, delete-orphan", lazy="selectin"
    )


class TemplateVendorMapping(Base, TimestampMixin):
    """How a template translates onto a specific adapter / OLT model.

    The keys inside ``parameters`` are interpreted only by the adapter, never by the core.
    For example, a Huawei mapping holds line/service profile IDs and traffic-table indexes.
    """

    __tablename__ = "template_vendor_mappings"
    __table_args__ = (UniqueConstraint("template_id", "adapter", "olt_model"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("service_templates.id", ondelete="CASCADE"))
    adapter: Mapped[str] = mapped_column(String(64))
    olt_model: Mapped[str] = mapped_column(String(64), default="")  # "" = any model for that adapter
    line_profile_ref: Mapped[str | None] = mapped_column(String(64))
    service_profile_ref: Mapped[str | None] = mapped_column(String(64))
    dba_profile_ref: Mapped[str | None] = mapped_column(String(64))
    traffic_table_up_ref: Mapped[str | None] = mapped_column(String(64))
    traffic_table_down_ref: Mapped[str | None] = mapped_column(String(64))
    parameters: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    template: Mapped[ServiceTemplate] = relationship(back_populates="mappings")


class Service(Base, TimestampMixin):
    __tablename__ = "services"
    id: Mapped[int] = mapped_column(primary_key=True)
    subscriber_id: Mapped[int] = mapped_column(ForeignKey("subscribers.id", ondelete="CASCADE"), index=True)
    service_ref: Mapped[str] = mapped_column(String(64), unique=True)
    external_service_id: Mapped[str | None] = mapped_column(String(64), index=True)
    external_package_id: Mapped[str | None] = mapped_column(String(64))
    ont_id: Mapped[int | None] = mapped_column(ForeignKey("onts.id", ondelete="SET NULL"), index=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("service_templates.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    activation_date: Mapped[date | None]
    suspended_at: Mapped[datetime | None]
    # Network-side identifiers created on the equipment (e.g. service-port indexes), as reported by the adapter.
    network_config: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    last_job_id: Mapped[str | None] = mapped_column(String(36))
    notes: Mapped[str] = mapped_column(Text, default="")
    is_simulated: Mapped[bool] = mapped_column(default=False)
    subscriber: Mapped[Subscriber] = relationship(lazy="selectin")
    ont: Mapped[Ont | None] = relationship(lazy="selectin")
    template: Mapped[ServiceTemplate | None] = relationship(lazy="selectin")


# --------------------------------------------------------------------------- provisioning
class ProvisioningJob(Base, TimestampMixin):
    __tablename__ = "provisioning_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID
    operation: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    olt_id: Mapped[int | None] = mapped_column(ForeignKey("olts.id", ondelete="SET NULL"))
    ont_id: Mapped[int | None] = mapped_column(ForeignKey("onts.id", ondelete="SET NULL"))
    discovery_id: Mapped[int | None] = mapped_column(ForeignKey("ont_discoveries.id", ondelete="SET NULL"))
    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id", ondelete="SET NULL"))
    template_id: Mapped[int | None] = mapped_column(ForeignKey("service_templates.id", ondelete="SET NULL"))
    request: Mapped[dict[str, Any]] = mapped_column(JSON)          # validated input (no secrets)
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # {ok, errors[], warnings[]}
    plan: Mapped[list[Any] | None] = mapped_column(JSON)            # ordered list of abstract steps
    verification: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    rollback_on_failure: Mapped[bool] = mapped_column(default=True)
    attempt: Mapped[int] = mapped_column(default=0)
    retry_of: Mapped[str | None] = mapped_column(String(36))
    requested_by: Mapped[str] = mapped_column(String(64), default="system")
    approved_by: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    external_request_id: Mapped[str | None] = mapped_column(String(64), index=True)  # idempotency key from callers
    is_simulated: Mapped[bool] = mapped_column(default=False)
    steps: Mapped[list["ProvisioningStep"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="ProvisioningStep.seq", lazy="selectin"
    )
    events: Mapped[list["ProvisioningEvent"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="ProvisioningEvent.id", lazy="selectin"
    )


class ProvisioningStep(Base):
    __tablename__ = "provisioning_steps"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("provisioning_jobs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int]
    action: Mapped[str] = mapped_column(String(48))
    description: Mapped[str] = mapped_column(String(255), default="")
    params: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    destructive: Mapped[bool] = mapped_column(default=False)
    state: Mapped[str] = mapped_column(String(24), default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    compensation: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # {action, params} to undo, if supported
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    job: Mapped[ProvisioningJob] = relationship(back_populates="steps")


class ProvisioningEvent(Base):
    __tablename__ = "provisioning_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("provisioning_jobs.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    from_state: Mapped[str | None] = mapped_column(String(24))
    to_state: Mapped[str | None] = mapped_column(String(24))
    actor: Mapped[str] = mapped_column(String(64), default="system")
    message: Mapped[str] = mapped_column(Text, default="")
    job: Mapped[ProvisioningJob] = relationship(back_populates="events")


# --------------------------------------------------------------------------- alarms & incidents
class Alarm(Base, TimestampMixin):
    __tablename__ = "alarms"
    __table_args__ = (Index("ix_alarms_key_state", "alarm_key", "state"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    alarm_key: Mapped[str] = mapped_column(String(255))  # dedupe key: origin + source + category (+ vendor id)
    origin: Mapped[str] = mapped_column(String(16))      # device (vendor-reported) | nms (derived by this system) | manual
    source_type: Mapped[str] = mapped_column(String(16))  # olt | board | olt_port | ont | splitter | fat | dpoint | link
    source_id: Mapped[int | None]
    source_label: Mapped[str] = mapped_column(String(255), default="")
    olt_id: Mapped[int | None] = mapped_column(ForeignKey("olts.id", ondelete="SET NULL"), index=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    category: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    vendor_alarm_id: Mapped[str | None] = mapped_column(String(64))
    vendor_severity: Mapped[str | None] = mapped_column(String(32))
    vendor_raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    first_occurred_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_occurred_at: Mapped[datetime] = mapped_column(default=utcnow)
    occurrence_count: Mapped[int] = mapped_column(default=1)
    state: Mapped[str] = mapped_column(String(16), default="new", index=True)
    acknowledged: Mapped[bool] = mapped_column(default=False)
    acknowledged_by: Mapped[str | None] = mapped_column(String(64))
    acknowledged_at: Mapped[datetime | None]
    cleared_on_device_at: Mapped[datetime | None]
    resolved_at: Mapped[datetime | None]
    resolved_by: Mapped[str | None] = mapped_column(String(64))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id", ondelete="SET NULL"))
    is_simulated: Mapped[bool] = mapped_column(default=False)
    events: Mapped[list["AlarmEvent"]] = relationship(
        back_populates="alarm", cascade="all, delete-orphan", order_by="AlarmEvent.id", lazy="selectin"
    )


class AlarmEvent(Base):
    __tablename__ = "alarm_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    alarm_id: Mapped[int] = mapped_column(ForeignKey("alarms.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    action: Mapped[str] = mapped_column(String(32))  # raised | repeated | acknowledged | investigating | cleared | resolved | reopened | note
    actor: Mapped[str] = mapped_column(String(64), default="system")
    note: Mapped[str] = mapped_column(Text, default="")
    alarm: Mapped[Alarm] = relationship(back_populates="events")


class Incident(Base, TimestampMixin):
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    severity: Mapped[str] = mapped_column(String(16), default="major")
    source_type: Mapped[str] = mapped_column(String(16))   # element that is (suspected to be) the cause
    source_id: Mapped[int | None]
    source_label: Mapped[str] = mapped_column(String(255), default="")
    suspected_cause: Mapped[str] = mapped_column(Text, default="")
    detection: Mapped[str] = mapped_column(String(16), default="automatic")  # automatic | manual
    olt_id: Mapped[int | None] = mapped_column(ForeignKey("olts.id", ondelete="SET NULL"))
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    impact: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # snapshot from impact analysis
    affected_onts: Mapped[int] = mapped_column(default=0)
    confirmed_down_onts: Mapped[int] = mapped_column(default=0)
    affected_subscribers: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    resolved_at: Mapped[datetime | None]
    resolved_by: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str] = mapped_column(Text, default="")
    is_simulated: Mapped[bool] = mapped_column(default=False)


# --------------------------------------------------------------------------- background jobs
class Job(Base):
    """Generic DB-backed background job queue (monitoring polls, discovery, provisioning runs…)."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_run_after", "status", "run_after"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    dedupe_key: Mapped[str | None] = mapped_column(String(128), index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=1)
    run_after: Mapped[datetime] = mapped_column(default=utcnow)
    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64), default="system")


# --------------------------------------------------------------------------- simulator
class SimulatedDevice(Base, TimestampMixin):
    """State of a simulated OLT. This plays the role of the *equipment*: the NMS only learns
    about it through the simulator adapter, exactly as it would with a real device."""

    __tablename__ = "simulated_devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    host: Mapped[str] = mapped_column(String(255), unique=True)  # matches Olt.host for adapter=simulator
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    tick: Mapped[int] = mapped_column(default=0)
