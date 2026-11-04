"""Permission catalogue and built-in roles (section 13)."""
from __future__ import annotations

PERMISSIONS: dict[str, str] = {
    # read
    "network.read": "View sites, OLTs, ONTs, infrastructure, topology and services",
    "monitoring.read": "View monitoring data, optical readings, alarms and incidents",
    "provisioning.read": "View service profiles, templates and provisioning jobs",
    "audit.read": "View the audit log",
    # infrastructure & inventory
    "infrastructure.write": "Create/edit sites, FATs, splitters, cables, links and distribution points",
    "infrastructure.maintenance": "Update maintenance fields: port status, link verification, notes, faults",
    "olt.write": "Create/edit OLTs and their hardware records",
    "olt.credentials": "Set or change OLT management credentials",
    "olt.operate": "Run OLT operations: test connection, poll now, sync, backup",
    # ONT
    "ont.write": "Edit ONT inventory records and subscriber associations",
    "ont.discover": "Trigger ONT discovery on OLTs",
    "ont.register": "Register discovered ONTs on an OLT",
    "ont.operate": "Refresh ONT, retrieve optical metrics",
    "ont.reboot": "Reboot ONTs",
    "ont.disable": "Enable/disable ONTs",
    "ont.deregister": "Deregister (delete) ONTs from an OLT — destructive",
    # services & provisioning
    "subscriber.write": "Create/edit subscriber and service references",
    "provisioning.profiles": "Manage bandwidth profiles, service templates, VLANs and vendor profiles",
    "provisioning.request": "Create and validate provisioning requests",
    "provisioning.approve": "Approve and execute provisioning plans",
    "provisioning.suspend": "Suspend and restore services",
    "provisioning.disconnect": "Disconnect services — destructive",
    # monitoring
    "alarm.manage": "Acknowledge, investigate, resolve and annotate alarms",
    "incident.manage": "Create, update and resolve incidents",
    "monitoring.configure": "Configure thresholds and polling intervals",
    # admin
    "simulator.control": "Inject simulated network events",
    "users.manage": "Manage users",
    "roles.manage": "Manage roles and permissions",
    "settings.manage": "Change application settings",
    "integration.api": "Use the integration API (for future billing-system integration)",
}

_ALL = set(PERMISSIONS)
_READ = {"network.read", "monitoring.read", "provisioning.read"}

BUILTIN_ROLES: dict[str, tuple[str, set[str]]] = {
    "Super Administrator": ("Complete control over the system.", _ALL),
    "Network Administrator": (
        "Device and infrastructure management, provisioning and monitoring.",
        _ALL - {"users.manage", "roles.manage", "settings.manage"},
    ),
    "Network Technician": (
        "Read network information, view alarms, update maintenance records, run explicitly permitted operations.",
        _READ | {"infrastructure.maintenance", "ont.operate", "ont.discover", "alarm.manage"},
    ),
    "Monitoring Operator": (
        "View device status, optical information, alarms and incidents. No provisioning or destructive operations.",
        _READ | {"alarm.manage"},
    ),
    "Read-only Auditor": ("Read access to records and history, including the audit log.", _READ | {"audit.read"}),
}
