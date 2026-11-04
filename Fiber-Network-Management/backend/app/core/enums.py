"""Normalized vocabularies used across the core.

Values are stored as plain strings so that the schema stays portable between
PostgreSQL and SQLite and new values can be added without enum migrations.
Vendor-reported raw values are always stored alongside these normalized values.
"""
from __future__ import annotations

from enum import StrEnum


class AdminStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    MAINTENANCE = "maintenance"


class ConnectionStatus(StrEnum):
    UNKNOWN = "unknown"          # never polled
    ONLINE = "online"            # last poll succeeded
    DEGRADED = "degraded"        # reachable, but some data retrieval failed
    UNREACHABLE = "unreachable"  # last poll failed to communicate


class BoardStatus(StrEnum):
    NORMAL = "normal"
    FAILED = "failed"
    DISABLED = "disabled"
    EMPTY = "empty"          # slot with no board installed
    UNKNOWN = "unknown"


class PortOperStatus(StrEnum):
    UP = "up"
    DOWN = "down"
    FAULT = "fault"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


class OntState(StrEnum):
    """Normalized ONT operational state (section 5.4)."""

    ONLINE = "online"
    OFFLINE = "offline"
    DISCOVERED = "discovered"            # discovered but unregistered
    REGISTERED_INACTIVE = "registered_inactive"
    LOS = "los"
    DYING_GASP = "dying_gasp"
    DISABLED = "disabled"
    FAULTY = "faulty"
    UNKNOWN = "unknown"
    UNREACHABLE = "unreachable"          # OLT communication failure: ONT state cannot be known


ONT_DOWN_STATES = {OntState.OFFLINE, OntState.LOS, OntState.DYING_GASP, OntState.FAULTY}


class Severity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    WARNING = "warning"
    INFO = "info"


SEVERITY_RANK = {Severity.CRITICAL: 5, Severity.MAJOR: 4, Severity.MINOR: 3, Severity.WARNING: 2, Severity.INFO: 1}


class AlarmCategory(StrEnum):
    OLT_UNREACHABLE = "olt_unreachable"
    BOARD_FAILURE = "board_failure"
    PON_PORT_FAILURE = "pon_port_failure"
    ONT_LOS = "ont_los"
    ONT_DYING_GASP = "ont_dying_gasp"
    LOW_OPTICAL_SIGNAL = "low_optical_signal"
    HIGH_OPTICAL_SIGNAL = "high_optical_signal"
    POWER_FAILURE = "power_failure"
    TEMPERATURE = "temperature"
    UPLINK_FAILURE = "uplink_failure"
    ONT_REGISTRATION_FAILURE = "ont_registration_failure"
    INFRASTRUCTURE_FAULT = "infrastructure_fault"
    OTHER = "other"


class AlarmState(StrEnum):
    NEW = "new"
    ACKNOWLEDGED = "acknowledged"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    REOPENED = "reopened"


OPEN_ALARM_STATES = {AlarmState.NEW, AlarmState.ACKNOWLEDGED, AlarmState.INVESTIGATING, AlarmState.REOPENED}


class IncidentStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"


class ImpactEvidence(StrEnum):
    CONFIRMED = "confirmed"   # device status confirms the ONT is down
    INFERRED = "inferred"     # topology says it depends on the failed element; status not confirmed down
    UNKNOWN = "unknown"       # status cannot be determined (e.g. OLT unreachable)


class InfraStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    FAULTY = "faulty"
    MAINTENANCE = "maintenance"
    DECOMMISSIONED = "decommissioned"


class PortStatus(StrEnum):
    AVAILABLE = "available"
    OCCUPIED = "occupied"
    RESERVED = "reserved"
    FAULTY = "faulty"


class Verification(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    PLANNED = "planned"


class EndpointType(StrEnum):
    OLT_PORT = "olt_port"
    INFRA_PORT = "infra_port"
    ONT = "ont"


class InfraOwner(StrEnum):
    SPLITTER = "splitter"
    FAT = "fat"
    DPOINT = "dpoint"  # ODF / ODB / closure / splice point / termination point


class PortDirection(StrEnum):
    IN = "in"            # splitter input: fed from upstream only
    OUT = "out"          # splitter output: feeds downstream only
    THROUGH = "through"  # FAT / ODF / closure port: one upstream feed and one downstream link


class CableType(StrEnum):
    FEEDER = "feeder"
    DISTRIBUTION = "distribution"
    DROP = "drop"
    BACKBONE = "backbone"
    PATCH = "patch"


class CoreStatus(StrEnum):
    FREE = "free"
    USED = "used"
    RESERVED = "reserved"
    DAMAGED = "damaged"


class DPointType(StrEnum):
    ODF = "odf"
    ODB = "odb"
    CLOSURE = "closure"
    SPLICE_POINT = "splice_point"
    TERMINATION = "termination"


class ServiceStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


class ProvisioningState(StrEnum):
    DRAFT = "draft"
    VALIDATION_PENDING = "validation_pending"
    VALIDATION_FAILED = "validation_failed"
    AWAITING_APPROVAL = "awaiting_approval"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    ROLLED_BACK = "rolled_back"
    CANCELLED = "cancelled"


class ProvisioningOperation(StrEnum):
    REGISTER_ONT = "register_ont"            # registration only (profiles), no subscriber service
    ACTIVATE_SERVICE = "activate_service"    # register if needed + service ports + verify
    MODIFY_SERVICE = "modify_service"        # change service template/profile
    SUSPEND_SERVICE = "suspend_service"
    RESTORE_SERVICE = "restore_service"
    DISCONNECT_SERVICE = "disconnect_service"
    DEREGISTER_ONT = "deregister_ont"


class StepState(StrEnum):
    PENDING = "pending"
    SKIPPED = "skipped"          # already in desired state (idempotency check)
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    COMPENSATED = "compensated"
    COMPENSATION_FAILED = "compensation_failed"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
