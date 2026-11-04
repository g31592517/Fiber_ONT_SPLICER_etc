"""Provisioning engine.

Lifecycle:  draft -> validation_pending -> (validation_failed | awaiting_approval) -> queued -> running
            -> succeeded | failed | partially_succeeded | rolled_back      (cancelled from draft/awaiting/queued)

* Validation has two phases: local checks (database: capacity, ports, profiles, permissions) and
  a device-side dry run through the adapter where the adapter supports it. Both run in the worker.
* A plan is an ordered list of vendor-neutral steps. Each step is executed through the adapter.
* Steps are idempotent: adapters report ``changed=False`` when the device is already in the
  desired state, so a retry never blindly repeats a configuration command.
* On failure, completed steps with a declared compensation are undone in reverse order when
  ``rollback_on_failure`` is set. If communication is lost mid-step the outcome is unknown: the
  job fails without automatic compensation and asks for manual verification.
* After execution the result is read back from the device (verification). A command being
  accepted is never treated as proof of successful provisioning.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.base import (
    AdapterConnectionError,
    AdapterError,
    Capability,
    Fsp,
    OntRegistration,
    ServicePortSpec,
    UnsupportedOperation,
    to_dict,
)
from app.adapters.registry import adapter_class, build_adapter, write_allowed
from app.config import get_settings
from app.core import topology
from app.core.audit import Actor, audit
from app.core.enums import (
    EndpointType,
    OntState,
    PortStatus,
    ProvisioningOperation as Op,
    ProvisioningState as PS,
    ServiceStatus,
    StepState,
)
from app.core.errors import NotFound, PermissionDenied, ValidationFailed
from app.db import utcnow
from app.jobs.queue import enqueue, handler
from app.models import (
    FiberLink,
    InfraPort,
    Olt,
    OltPort,
    OltProfile,
    Ont,
    OntDiscovery,
    ProvisioningEvent,
    ProvisioningJob,
    ProvisioningStep,
    Service,
    ServiceTemplate,
    TemplateVendorMapping,
)
from app.security.crypto import decrypt_secret, encrypt_secret, redact

log = logging.getLogger("fnms.provisioning")

DESTRUCTIVE_OPS = {Op.DISCONNECT_SERVICE: "provisioning.disconnect", Op.DEREGISTER_ONT: "ont.deregister"}
APPROVE_PERMS = {
    Op.REGISTER_ONT: "ont.register", Op.ACTIVATE_SERVICE: "provisioning.approve", Op.MODIFY_SERVICE: "provisioning.approve",
    Op.SUSPEND_SERVICE: "provisioning.suspend", Op.RESTORE_SERVICE: "provisioning.suspend",
    Op.DISCONNECT_SERVICE: "provisioning.disconnect", Op.DEREGISTER_ONT: "ont.deregister",
}
REQUIRED_CAPS = {
    Op.REGISTER_ONT: [Capability.ONT_REGISTRATION],
    Op.ACTIVATE_SERVICE: [Capability.SERVICE_PORT, Capability.SERVICE_ACTIVATION],
    Op.MODIFY_SERVICE: [Capability.SERVICE_PORT],
    Op.SUSPEND_SERVICE: [Capability.SERVICE_SUSPENSION],
    Op.RESTORE_SERVICE: [Capability.SERVICE_RESTORATION],
    Op.DISCONNECT_SERVICE: [Capability.SERVICE_PORT],
    Op.DEREGISTER_ONT: [Capability.ONT_DEREGISTRATION],
}
RETRYABLE_STATES = {PS.FAILED, PS.PARTIALLY_SUCCEEDED, PS.ROLLED_BACK, PS.VALIDATION_FAILED}
CANCELLABLE_STATES = {PS.DRAFT, PS.VALIDATION_PENDING, PS.VALIDATION_FAILED, PS.AWAITING_APPROVAL, PS.QUEUED}


# ============================================================================ state helpers
def _transition(db: Session, job: ProvisioningJob, to: str, actor: str = "system", message: str = "") -> None:
    db.add(ProvisioningEvent(job_id=job.id, from_state=job.state, to_state=to, actor=actor, message=message))
    job.state = to


def _event(db: Session, job: ProvisioningJob, message: str, actor: str = "system") -> None:
    db.add(ProvisioningEvent(job_id=job.id, actor=actor, message=message))


# ============================================================================ request creation
def create_request(db: Session, actor: Actor, data: dict[str, Any]) -> ProvisioningJob:
    op = data.get("operation")
    if op not in {o.value for o in Op}:
        raise ValidationFailed(f"Unknown operation '{op}'")
    if not actor.has("provisioning.request") and not actor.has("ont.register"):
        raise PermissionDenied("Missing permission: provisioning.request")
    if data.get("external_request_id"):
        existing = db.scalar(select(ProvisioningJob).where(ProvisioningJob.external_request_id == data["external_request_id"]))
        if existing:
            return existing  # idempotent for integrations
    req = {k: v for k, v in data.items() if k not in ("auth_password", "auto_approve")}
    if data.get("auth_password"):
        req["auth_password_enc"] = encrypt_secret(data["auth_password"])
    job = ProvisioningJob(id=str(uuid.uuid4()), operation=op, state=PS.DRAFT, request=req,
                          rollback_on_failure=bool(data.get("rollback_on_failure", True)), requested_by=actor.username,
                          external_request_id=data.get("external_request_id"))
    _resolve_targets(db, job)
    db.add(job)
    db.flush()
    _event(db, job, f"Request created: {op}", actor.username)
    submit_for_validation(db, job, actor.username)
    if data.get("auto_approve"):
        job.request = {**job.request, "auto_approve_by": actor.username}
        if not actor.has(APPROVE_PERMS[Op(op)]):
            raise PermissionDenied(f"auto_approve requires permission {APPROVE_PERMS[Op(op)]}")
    audit(db, actor, "provisioning.request", "provisioning_job", job.id, f"{op} requested",
          {"request": redact(job.request)})
    return job


def _resolve_targets(db: Session, job: ProvisioningJob) -> None:
    r = job.request
    if r.get("service_id"):
        svc = db.get(Service, r["service_id"])
        if svc is None:
            raise NotFound(f"Service {r['service_id']} not found")
        job.service_id = svc.id
        job.ont_id = svc.ont_id
        job.template_id = r.get("template_id") or svc.template_id
    if r.get("ont_id"):
        job.ont_id = r["ont_id"]
    if r.get("discovery_id"):
        d = db.get(OntDiscovery, r["discovery_id"])
        if d is None:
            raise NotFound(f"Discovered ONT {r['discovery_id']} not found")
        job.discovery_id = d.id
        job.olt_id = d.olt_id
    if r.get("template_id"):
        job.template_id = r["template_id"]
    if job.ont_id and not job.olt_id:
        ont = db.get(Ont, job.ont_id)
        if ont is None:
            raise NotFound(f"ONT {job.ont_id} not found")
        job.olt_id = ont.olt_id
    if job.olt_id:
        olt = db.get(Olt, job.olt_id)
        job.is_simulated = bool(olt and olt.is_simulated)


def submit_for_validation(db: Session, job: ProvisioningJob, actor: str = "system") -> None:
    _transition(db, job, PS.VALIDATION_PENDING, actor, "Queued for validation")
    enqueue(db, "provisioning.validate", {"job_id": job.id}, created_by=actor, dedupe_key=f"prov-validate:{job.id}")


# ============================================================================ validation + planning
class _Checks:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, m):
        self.errors.append(m)

    def warn(self, m):
        self.warnings.append(m)


def _mapping(db: Session, template: ServiceTemplate, olt: Olt) -> TemplateVendorMapping | None:
    rows = [m for m in template.mappings if m.adapter == olt.adapter]
    exact = [m for m in rows if m.olt_model and olt.model and m.olt_model.lower() == olt.model.lower()]
    generic = [m for m in rows if not m.olt_model]
    return (exact or generic or [None])[0]


def _profile_known(db: Session, olt: Olt, kind: str, ref: str | None) -> bool:
    if ref is None:
        return True
    return db.scalar(select(OltProfile.id).where(OltProfile.olt_id == olt.id, OltProfile.kind == kind,
                                                 OltProfile.vendor_ref == str(ref))) is not None


def _fsp(port: OltPort) -> str:
    return f"{port.frame}/{port.slot}/{port.port}"


def build_plan(db: Session, job: ProvisioningJob, c: _Checks) -> list[dict]:
    """Local validation + plan generation. Pure database work."""
    r = job.request
    op = Op(job.operation)
    olt = db.get(Olt, job.olt_id) if job.olt_id else None
    if olt is None:
        c.error("No OLT could be determined for this request")
        return []
    cls = adapter_class(olt.adapter)
    if olt.admin_status != "enabled":
        c.error(f"OLT {olt.name} is administratively {olt.admin_status}")
    if olt.connection_status == "unreachable":
        c.error(f"OLT {olt.name} is currently unreachable")
    elif olt.connection_status == "unknown":
        c.warn(f"OLT {olt.name} has not been polled yet; connectivity unconfirmed")
    for cap in REQUIRED_CAPS[op]:
        ok, why = write_allowed(cls, cap)
        if not ok:
            c.error(why)

    steps: list[dict] = []
    ont = db.get(Ont, job.ont_id) if job.ont_id else None
    svc = db.get(Service, job.service_id) if job.service_id else None
    template = db.get(ServiceTemplate, job.template_id) if job.template_id else None
    mapping = _mapping(db, template, olt) if template else None

    # ---------------------------------------------------------------- registration part
    needs_registration = op == Op.REGISTER_ONT or (op == Op.ACTIVATE_SERVICE and (ont is None or ont.registration_status != "registered"))
    reg_ctx: dict[str, Any] = {}
    if needs_registration:
        d = db.get(OntDiscovery, job.discovery_id) if job.discovery_id else None
        if d is None:
            c.error("Registration requires a discovered ONT (discovery_id)")
            return []
        if d.state != "unregistered":
            c.error(f"Discovered ONT {d.serial_number} is in state '{d.state}', not 'unregistered'")
        if db.scalar(select(Ont).where(Ont.serial_number == d.serial_number, Ont.registration_status == "registered")):
            c.error(f"Serial {d.serial_number} is already registered")
        port = d.pon_port
        if port is None:
            c.error("The PON port of the discovered ONT is not known in the inventory; poll the OLT first")
            return []
        if port.oper_status in ("fault", "down", "disabled"):
            c.error(f"PON port {port.fsp} is {port.oper_status}")
        registered_on_port = db.scalar(select(func.count(Ont.id)).where(Ont.pon_port_id == port.id,
                                                                         Ont.registration_status == "registered")) or 0
        if port.max_onts and registered_on_port >= port.max_onts:
            c.error(f"PON port {port.fsp} is at capacity ({registered_on_port}/{port.max_onts} ONTs)")
        line_ref = r.get("line_profile_ref") or (mapping.line_profile_ref if mapping else None)
        srv_ref = r.get("service_profile_ref") or (mapping.service_profile_ref if mapping else None)
        if line_ref is None or srv_ref is None:
            c.error("Line and service profiles are required (directly or through the service template's vendor mapping)")
        for kind, ref in (("line", line_ref), ("service", srv_ref)):
            if ref is not None and not _profile_known(db, olt, kind, ref):
                c.warn(f"{kind} profile {ref} is not in the synchronized profile list for {olt.name} (sync profiles to confirm)")
        idx = r.get("ont_index")
        if idx is not None and db.scalar(select(Ont.id).where(Ont.pon_port_id == port.id, Ont.ont_index == idx,
                                                              Ont.registration_status == "registered")):
            c.error(f"ONT ID {idx} is already used on {port.fsp}")
        if r.get("fat_port_id"):
            fp = db.get(InfraPort, r["fat_port_id"])
            if fp is None or fp.owner_type != "fat":
                c.error("fat_port_id does not reference a FAT port")
            else:
                st = topology.port_effective_status(db, fp)
                if st == PortStatus.OCCUPIED:
                    c.error(f"FAT port {fp.number} is already occupied")
                elif st == PortStatus.FAULTY:
                    c.error(f"FAT port {fp.number} is marked faulty")
                elif st == PortStatus.RESERVED and not r.get("use_reserved_port"):
                    c.error(f"FAT port {fp.number} is reserved ({fp.reserved_for}); set use_reserved_port to confirm")
        reg_ctx = {"fsp": _fsp(port), "serial": d.serial_number, "ont_index": idx, "line_profile_ref": line_ref,
                   "service_profile_ref": srv_ref, "auth_method": r.get("auth_method", "sn"), "loid": r.get("loid") or d.loid,
                   "description": (r.get("description") or d.serial_number)[:64]}
        steps.append({"action": "register_ont", "description": f"Register ONT {d.serial_number} on {olt.name} {port.fsp}",
                      "params": reg_ctx, "destructive": False,
                      "compensation": {"action": "deregister_ont", "params": {"fsp": reg_ctx["fsp"], "ont_index": "$ont_index"}}})
        steps.append({"action": "verify_ont", "description": "Read back ONT registration from the OLT",
                      "params": {"fsp": reg_ctx["fsp"], "ont_index": "$ont_index", "serial": d.serial_number}})
    elif ont is not None:
        if ont.olt_id != olt.id or ont.pon_port is None or ont.ont_index is None:
            c.error("ONT is not fully associated with an OLT port and ONT ID")
        else:
            reg_ctx = {"fsp": ont.pon_port.fsp, "ont_index": ont.ont_index, "serial": ont.serial_number}

    # ---------------------------------------------------------------- service part
    def sp_steps(tpl: ServiceTemplate, mp: TemplateVendorMapping | None, label: str) -> list[dict]:
        out = []
        if not tpl.is_active:
            c.error(f"Service template '{tpl.name}' is inactive")
        if mp is None:
            c.error(f"Template '{tpl.name}' has no mapping for adapter '{olt.adapter}'"
                    f"{' / model ' + olt.model if olt.model else ''}")
            return out
        vlan = tpl.svlan
        if vlan is None or not 1 <= vlan <= 4094:
            c.error(f"Template '{tpl.name}' has no valid service VLAN")
            return out
        from app.models import Vlan

        if not db.scalar(select(Vlan.id).where(Vlan.vlan_id == vlan, (Vlan.olt_id == olt.id) | (Vlan.olt_id.is_(None)))):
            c.warn(f"VLAN {vlan} is not defined in the VLAN register for {olt.name}")
        for ref in (mp.traffic_table_up_ref, mp.traffic_table_down_ref):
            if ref and not _profile_known(db, olt, "traffic_table", ref):
                c.warn(f"Traffic table {ref} is not in the synchronized profile list for {olt.name}")
        params = {"fsp": reg_ctx.get("fsp"), "ont_index": "$ont_index", "vlan": vlan, "gem_port": tpl.gem_port,
                  "user_vlan": tpl.user_vlan, "inner_vlan": tpl.cvlan, "vlan_mode": tpl.vlan_mode,
                  "traffic_up_ref": mp.traffic_table_up_ref, "traffic_down_ref": mp.traffic_table_down_ref}
        out.append({"action": "create_service_port", "description": f"{label}: service-port VLAN {vlan} GEM {tpl.gem_port}",
                    "params": params, "destructive": False,
                    "compensation": {"action": "delete_service_port", "params": {"index": "$last_service_port"}}})
        return out

    current_ports = list((svc.network_config or {}).get("service_ports", [])) if svc else []

    if op in (Op.ACTIVATE_SERVICE, Op.MODIFY_SERVICE, Op.SUSPEND_SERVICE, Op.RESTORE_SERVICE, Op.DISCONNECT_SERVICE):
        if svc is None:
            c.error("A service reference (service_id) is required")
            return steps
        if not reg_ctx and op != Op.ACTIVATE_SERVICE:
            c.error("The service's ONT is not registered on an OLT")
            return steps

    if op == Op.ACTIVATE_SERVICE:
        if svc.status == ServiceStatus.ACTIVE and current_ports:
            c.error("Service is already active; use modify_service to change it")
        if template is None:
            c.error("A service template is required")
        else:
            if ont and ont.status not in (OntState.ONLINE, OntState.REGISTERED_INACTIVE) and not needs_registration:
                c.warn(f"ONT is currently {ont.status}; configuration can be applied but service cannot be verified end-to-end")
            steps += sp_steps(template, mapping, "Activate")
            steps.append({"action": "verify_service", "description": "Read back service-ports and ONT status",
                          "params": {"fsp": reg_ctx.get("fsp"), "ont_index": "$ont_index", "expect_admin": "enabled"}})
    elif op == Op.MODIFY_SERVICE:
        if template is None:
            c.error("New template_id is required")
        elif svc.status != ServiceStatus.ACTIVE:
            c.error(f"Only active services can be modified (service is {svc.status})")
        else:
            old_tpl = svc.template
            old_map = _mapping(db, old_tpl, olt) if old_tpl else None
            for sp in current_ports:
                steps.append({"action": "delete_service_port", "description": f"Remove current service-port {sp['index']}",
                              "params": {"index": sp["index"]}, "destructive": True,
                              "compensation": {"action": "create_service_port", "params": {
                                  **{k: sp.get(k) for k in ("vlan", "gem_port", "user_vlan", "inner_vlan")},
                                  "fsp": reg_ctx["fsp"], "ont_index": reg_ctx["ont_index"], "index": sp["index"],
                                  "traffic_up_ref": old_map.traffic_table_up_ref if old_map else None,
                                  "traffic_down_ref": old_map.traffic_table_down_ref if old_map else None}}})
            steps += sp_steps(template, mapping, "Modify")
            steps.append({"action": "verify_service", "description": "Read back service-ports",
                          "params": {"fsp": reg_ctx["fsp"], "ont_index": "$ont_index", "expect_admin": "enabled"}})
            c.warn("Changing the service profile removes and recreates the service-port; expect a brief interruption")
    elif op in (Op.SUSPEND_SERVICE, Op.RESTORE_SERVICE):
        enable = op == Op.RESTORE_SERVICE
        want = ServiceStatus.SUSPENDED if enable else ServiceStatus.ACTIVE
        if svc.status != want:
            c.error(f"Service must be {want} to {op.value.split('_')[0]} (currently {svc.status})")
        if not current_ports:
            c.error("Service has no recorded network service-ports")
        for sp in current_ports:
            steps.append({"action": "set_service_port_admin",
                          "description": f"{'Enable' if enable else 'Disable'} service-port {sp['index']}",
                          "params": {"index": sp["index"], "enabled": enable},
                          "compensation": {"action": "set_service_port_admin", "params": {"index": sp["index"], "enabled": not enable}}})
        steps.append({"action": "verify_service", "description": "Read back service-port admin state",
                      "params": {"fsp": reg_ctx.get("fsp"), "ont_index": "$ont_index",
                                 "expect_admin": "enabled" if enable else "disabled"}})
    elif op == Op.DISCONNECT_SERVICE:
        if svc.status == ServiceStatus.DISCONNECTED:
            c.error("Service is already disconnected")
        for sp in current_ports:
            steps.append({"action": "delete_service_port", "description": f"Delete service-port {sp['index']}",
                          "params": {"index": sp["index"]}, "destructive": True})
        if r.get("deregister_ont"):
            steps.append({"action": "deregister_ont", "description": f"Deregister ONT {reg_ctx.get('serial')}",
                          "params": {"fsp": reg_ctx.get("fsp"), "ont_index": reg_ctx.get("ont_index")}, "destructive": True})
        steps.append({"action": "verify_service", "description": "Confirm service-ports removed",
                      "params": {"fsp": reg_ctx.get("fsp"), "ont_index": reg_ctx.get("ont_index"), "expect_absent": True}})
    elif op == Op.DEREGISTER_ONT:
        if ont is None or ont.registration_status != "registered":
            c.error("ONT is not registered")
        else:
            active = list(db.scalars(select(Service).where(Service.ont_id == ont.id,
                                                           Service.status.in_([ServiceStatus.ACTIVE, ServiceStatus.SUSPENDED]))))
            if active and not r.get("force"):
                c.error(f"ONT has {len(active)} active/suspended service(s); disconnect them first")
            steps.append({"action": "deregister_ont", "description": f"Deregister ONT {ont.serial_number}",
                          "params": {"fsp": reg_ctx.get("fsp"), "ont_index": reg_ctx.get("ont_index")}, "destructive": True})
            steps.append({"action": "verify_absent", "description": "Confirm ONT no longer configured on OLT",
                          "params": {"fsp": reg_ctx.get("fsp"), "ont_index": reg_ctx.get("ont_index")}})

    # substitute known ont_index where registration is not part of the plan
    if reg_ctx.get("ont_index") is not None and not needs_registration:
        for s in steps:
            for container in (s.get("params", {}), (s.get("compensation") or {}).get("params", {})):
                if container.get("ont_index") == "$ont_index":
                    container["ont_index"] = reg_ctx["ont_index"]
    return [{"seq": i + 1, **s} for i, s in enumerate(steps)]


def validate(db: Session, job: ProvisioningJob) -> None:
    c = _Checks()
    plan = build_plan(db, job, c)
    dry_runs: list[dict] = []
    if not c.errors and plan:
        olt = db.get(Olt, job.olt_id)
        try:
            dry_runs = _dry_run(db, job, olt, plan)
            for d in dry_runs:
                if d.get("status") == "error":
                    c.error(f"Device dry run rejected step {d['seq']} ({d['action']}): {d['message']}")
        except AdapterConnectionError as exc:
            c.error(f"Could not reach OLT for dry run: {exc}")
    job.plan = plan
    job.validation = {"ok": not c.errors, "errors": c.errors, "warnings": c.warnings, "dry_run": dry_runs,
                      "validated_at": utcnow().isoformat()}
    db.query(ProvisioningStep).filter(ProvisioningStep.job_id == job.id).delete()
    for s in plan:
        db.add(ProvisioningStep(job_id=job.id, seq=s["seq"], action=s["action"], description=s.get("description", ""),
                                params=redact(s.get("params")), destructive=bool(s.get("destructive")),
                                compensation=s.get("compensation")))
    if c.errors:
        _transition(db, job, PS.VALIDATION_FAILED, message="; ".join(c.errors)[:1000])
        return
    _transition(db, job, PS.AWAITING_APPROVAL, message=f"Plan with {len(plan)} step(s) ready for review")
    approver = job.request.get("auto_approve_by")
    if approver:
        _approve(db, job, approver, auto=True)


def _dry_run(db: Session, job: ProvisioningJob, olt: Olt, plan: list[dict]) -> list[dict]:
    """Ask the device to validate the first write step(s). Only steps whose inputs are already known are dry-run."""
    out = []
    with build_adapter(olt) as adapter:
        for s in plan:
            p = s["params"]
            if any(v == "$ont_index" or (isinstance(v, str) and v.startswith("$")) for v in p.values()):
                out.append({"seq": s["seq"], "action": s["action"], "status": "deferred",
                            "message": "Depends on an earlier step's result; validated at execution"})
                continue
            try:
                if s["action"] == "register_ont":
                    r = adapter.register_ont(_registration(job, p), dry_run=True)
                elif s["action"] == "create_service_port":
                    r = adapter.create_service_port(_sp_spec(p), dry_run=True)
                elif s["action"] == "delete_service_port":
                    r = adapter.delete_service_port(int(p["index"]), dry_run=True)
                elif s["action"] == "set_service_port_admin":
                    r = adapter.set_service_port_admin(int(p["index"]), bool(p["enabled"]), dry_run=True)
                elif s["action"] == "deregister_ont":
                    r = adapter.deregister_ont(Fsp.parse(p["fsp"]), int(p["ont_index"]), dry_run=True)
                else:
                    continue
                out.append({"seq": s["seq"], "action": s["action"], "status": "ok", "message": r.message, "commands": r.commands})
            except UnsupportedOperation as exc:
                out.append({"seq": s["seq"], "action": s["action"], "status": "unsupported", "message": exc.reason})
            except AdapterConnectionError:
                raise
            except AdapterError as exc:
                out.append({"seq": s["seq"], "action": s["action"], "status": "error", "message": str(exc)})
    return out


# ============================================================================ approval / cancel / retry
def approve(db: Session, job: ProvisioningJob, actor: Actor, confirm: str | None = None) -> ProvisioningJob:
    op = Op(job.operation)
    perm = APPROVE_PERMS[op]
    if not actor.has(perm):
        raise PermissionDenied(f"Approving {op.value} requires permission {perm}")
    if job.state != PS.AWAITING_APPROVAL:
        raise ValidationFailed(f"Job is {job.state}; only jobs awaiting approval can be approved")
    if op in DESTRUCTIVE_OPS or any(s.destructive for s in job.steps):
        expected = _confirmation_token(db, job)
        if (confirm or "").strip().upper() != expected.upper():
            raise ValidationFailed(f"Destructive operation: type '{expected}' to confirm", {"confirmation_required": expected})
    _approve(db, job, actor.username)
    audit(db, actor, "provisioning.approve", "provisioning_job", job.id, f"Approved {op.value}",
          {"plan": [s.action for s in job.steps]})
    return job


def _confirmation_token(db: Session, job: ProvisioningJob) -> str:
    if job.ont_id:
        ont = db.get(Ont, job.ont_id)
        if ont:
            return ont.serial_number
    if job.service_id:
        svc = db.get(Service, job.service_id)
        if svc:
            return svc.service_ref
    return job.id[:8]


def _approve(db: Session, job: ProvisioningJob, approver: str, auto: bool = False) -> None:
    job.approved_by = approver
    job.approved_at = utcnow()
    _transition(db, job, PS.QUEUED, approver, "Auto-approved" if auto else "Approved")
    enqueue(db, "provisioning.execute", {"job_id": job.id}, created_by=approver, dedupe_key=f"prov-exec:{job.id}")


def cancel(db: Session, job: ProvisioningJob, actor: Actor) -> ProvisioningJob:
    if job.state not in CANCELLABLE_STATES:
        raise ValidationFailed(f"Job in state {job.state} cannot be cancelled")
    _transition(db, job, PS.CANCELLED, actor.username, "Cancelled")
    job.finished_at = utcnow()
    audit(db, actor, "provisioning.cancel", "provisioning_job", job.id, "Cancelled")
    return job


def retry(db: Session, job: ProvisioningJob, actor: Actor) -> ProvisioningJob:
    if job.state not in RETRYABLE_STATES:
        raise ValidationFailed(f"Only failed/partial/rolled-back jobs can be retried (job is {job.state})")
    if db.scalar(select(ProvisioningJob.id).where(ProvisioningJob.retry_of == job.id,
                                                  ProvisioningJob.state.notin_([PS.CANCELLED, PS.VALIDATION_FAILED]))):
        raise ValidationFailed("This job has already been retried")
    new = ProvisioningJob(id=str(uuid.uuid4()), operation=job.operation, state=PS.DRAFT, request=dict(job.request),
                          olt_id=job.olt_id, ont_id=job.ont_id, discovery_id=job.discovery_id, service_id=job.service_id,
                          template_id=job.template_id, rollback_on_failure=job.rollback_on_failure,
                          attempt=job.attempt + 1, retry_of=job.id, requested_by=actor.username, is_simulated=job.is_simulated)
    new.request.pop("auto_approve_by", None)
    db.add(new)
    db.flush()
    _event(db, new, f"Retry of job {job.id}. Steps are idempotent: already-applied configuration is detected and skipped.",
           actor.username)
    submit_for_validation(db, new, actor.username)
    audit(db, actor, "provisioning.retry", "provisioning_job", new.id, f"Retry of {job.id}")
    return new


# ============================================================================ execution
def _registration(job: ProvisioningJob, p: dict) -> OntRegistration:
    return OntRegistration(fsp=Fsp.parse(p["fsp"]), serial_number=p["serial"], ont_index=p.get("ont_index"),
                           auth_method=p.get("auth_method", "sn"), loid=p.get("loid"),
                           password=decrypt_secret(job.request.get("auth_password_enc")),
                           line_profile_ref=p.get("line_profile_ref"), service_profile_ref=p.get("service_profile_ref"),
                           description=p.get("description", ""))


def _sp_spec(p: dict) -> ServicePortSpec:
    return ServicePortSpec(fsp=Fsp.parse(p["fsp"]), ont_index=int(p["ont_index"]), vlan=int(p["vlan"]),
                           gem_port=p.get("gem_port"), user_vlan=p.get("user_vlan"), inner_vlan=p.get("inner_vlan"),
                           vlan_mode=p.get("vlan_mode", "single"), traffic_up_ref=p.get("traffic_up_ref"),
                           traffic_down_ref=p.get("traffic_down_ref"), index=p.get("index"))


def _resolve(params: dict, ctx: dict) -> dict:
    out = {}
    for k, v in (params or {}).items():
        if isinstance(v, str) and v.startswith("$"):
            if v[1:] not in ctx:
                raise AdapterError(f"Step input '{v}' is not available (earlier step did not produce it)")
            out[k] = ctx[v[1:]]
        else:
            out[k] = v
    return out


def _run_action(adapter, job: ProvisioningJob, action: str, p: dict, ctx: dict) -> dict:
    if action == "register_ont":
        r = adapter.register_ont(_registration(job, p))
        ctx["ont_index"] = r.data.get("ont_index")
        return {"ok": r.ok, "changed": r.changed, "message": r.message, "commands": r.commands, "data": r.data}
    if action == "create_service_port":
        r = adapter.create_service_port(_sp_spec(p))
        idx = r.data.get("index")
        ctx["last_service_port"] = idx
        ctx.setdefault("created_service_ports", []).append({"index": idx, **{k: p.get(k) for k in (
            "vlan", "gem_port", "user_vlan", "inner_vlan", "traffic_up_ref", "traffic_down_ref")}})
        return {"ok": r.ok, "changed": r.changed, "message": r.message, "commands": r.commands, "data": r.data}
    if action == "delete_service_port":
        r = adapter.delete_service_port(int(p["index"]))
        ctx.setdefault("deleted_service_ports", []).append(int(p["index"]))
        return {"ok": r.ok, "changed": r.changed, "message": r.message, "commands": r.commands}
    if action == "set_service_port_admin":
        r = adapter.set_service_port_admin(int(p["index"]), bool(p["enabled"]))
        return {"ok": r.ok, "changed": r.changed, "message": r.message, "commands": r.commands}
    if action == "deregister_ont":
        r = adapter.deregister_ont(Fsp.parse(p["fsp"]), int(p["ont_index"]))
        return {"ok": r.ok, "changed": r.changed, "message": r.message, "commands": r.commands}
    if action == "verify_ont":
        s = adapter.get_ont(Fsp.parse(p["fsp"]), int(p["ont_index"]))
        if s is None or s.serial_number.upper() != str(p["serial"]).upper():
            raise AdapterError("Verification failed: ONT not found at the expected position after registration")
        ctx["ont_state"] = s.state
        ctx["ont_vendor_state"] = s.vendor_state
        return {"ok": True, "changed": False, "message": f"ONT present, state {s.state}", "data": to_dict(s)}
    if action == "verify_service":
        sps = adapter.get_service_ports(Fsp.parse(p["fsp"]), int(p["ont_index"]))
        found = {sp.index: sp for sp in sps}
        if p.get("expect_absent"):
            leftover = [i for i in ctx.get("deleted_service_ports", []) if i in found]
            if leftover:
                raise AdapterError(f"Verification failed: service-ports {leftover} still present")
            return {"ok": True, "changed": False, "message": "Service-ports removed"}
        expected = [sp["index"] for sp in ctx.get("created_service_ports", [])] or ctx.get("existing_service_ports", [])
        missing = [i for i in expected if i not in found]
        wrong = [i for i in expected if i in found and found[i].admin_status != p.get("expect_admin", "enabled")]
        if missing or wrong:
            raise AdapterError(f"Verification failed: missing={missing} wrong_admin_state={wrong}")
        ont = adapter.get_ont(Fsp.parse(p["fsp"]), int(p["ont_index"])) if adapter.supports(Capability.ONT_STATUS) else None
        ctx["ont_state"] = ont.state if ont else None
        return {"ok": True, "changed": False, "message": f"{len(expected)} service-port(s) verified; ONT state {ctx['ont_state']}",
                "data": {"service_ports": [to_dict(found[i]) for i in expected]}}
    if action == "verify_absent":
        s = adapter.get_ont(Fsp.parse(p["fsp"]), int(p["ont_index"]))
        if s is not None:
            raise AdapterError("Verification failed: ONT is still configured on the OLT")
        return {"ok": True, "changed": False, "message": "ONT no longer configured"}
    raise AdapterError(f"Unknown plan action '{action}'")


def execute(db: Session, job: ProvisioningJob) -> dict:
    if job.state != PS.QUEUED:
        return {"skipped": f"job is {job.state}"}
    olt = db.get(Olt, job.olt_id)
    _transition(db, job, PS.RUNNING, message="Execution started")
    job.started_at = utcnow()
    db.commit()
    svc = db.get(Service, job.service_id) if job.service_id else None
    ctx: dict[str, Any] = {"existing_service_ports": [sp["index"] for sp in ((svc.network_config or {}).get("service_ports", []) if svc else [])]}
    plan = {s["seq"]: s for s in (job.plan or [])}
    done: list[ProvisioningStep] = []
    failure: str | None = None
    comm_lost = False
    with build_adapter(olt) as adapter:
        for step in sorted(job.steps, key=lambda s: s.seq):
            raw = plan.get(step.seq, {})
            step.state, step.started_at, step.attempts = StepState.RUNNING, utcnow(), step.attempts + 1
            db.commit()
            try:
                params = _resolve(raw.get("params", {}), ctx)
                out = _run_action(adapter, job, step.action, params, ctx)
                step.output = redact(out)
                step.state = StepState.SUCCEEDED if out.get("changed", True) else StepState.SKIPPED
                if step.state == StepState.SKIPPED:
                    _event(db, job, f"Step {step.seq} ({step.action}) skipped: device already in desired state")
                if raw.get("compensation"):
                    step.compensation = {"action": raw["compensation"]["action"],
                                         "params": _resolve_soft(raw["compensation"]["params"], ctx)}
                done.append(step)
            except UnsupportedOperation as exc:
                step.state, step.error, failure = StepState.FAILED, exc.reason, exc.reason
            except AdapterConnectionError as exc:
                step.state, comm_lost = StepState.FAILED, True
                step.error = f"Communication lost: outcome of this step is UNKNOWN. {exc}"
                failure = step.error
            except AdapterError as exc:
                step.state, step.error, failure = StepState.FAILED, str(exc), str(exc)
            step.finished_at = utcnow()
            db.commit()
            if failure:
                break

        if failure is None:
            job.verification = {"ok": True, "ont_state": ctx.get("ont_state"), "checked_at": utcnow().isoformat()}
            _finalize_success(db, job, ctx)
            _transition(db, job, PS.SUCCEEDED, message="All steps succeeded and the result was verified on the device")
        else:
            job.error = failure
            changed_steps = [s for s in done if s.state == StepState.SUCCEEDED]
            if comm_lost:
                _transition(db, job, PS.FAILED, message="Communication with the OLT was lost during execution. "
                            "Manual verification required; no automatic compensation was attempted.")
            elif changed_steps and job.rollback_on_failure:
                ok = _compensate(db, job, adapter, changed_steps)
                _transition(db, job, PS.ROLLED_BACK if ok else PS.FAILED,
                            message="Completed steps were compensated" if ok else "Compensation incomplete — manual action required")
            elif changed_steps:
                _finalize_partial(db, job, ctx)
                _transition(db, job, PS.PARTIALLY_SUCCEEDED, message="Some steps were applied; rollback disabled")
            else:
                _transition(db, job, PS.FAILED, message=failure[:500])
    job.finished_at = utcnow()
    job.result = redact({k: v for k, v in ctx.items()})
    if svc:
        svc.last_job_id = job.id
        if job.state == PS.FAILED and job.operation == Op.ACTIVATE_SERVICE and svc.status == ServiceStatus.PENDING:
            svc.status = ServiceStatus.FAILED
    audit(db, Actor(None, job.approved_by or "system"), f"provisioning.{job.operation}", "provisioning_job", job.id,
          f"{job.operation} {job.state}", {"error": job.error}, outcome="success" if job.state == PS.SUCCEEDED else "failure")
    return {"state": job.state, "error": job.error}


def _resolve_soft(params: dict, ctx: dict) -> dict:
    return {k: (ctx.get(v[1:], v) if isinstance(v, str) and v.startswith("$") else v) for k, v in (params or {}).items()}


def _compensate(db: Session, job: ProvisioningJob, adapter, steps: list[ProvisioningStep]) -> bool:
    ok = True
    for s in reversed(steps):
        if not s.compensation:
            ok = False
            _event(db, job, f"Step {s.seq} ({s.action}) has no compensation; manual action may be required")
            continue
        try:
            comp = s.compensation
            p = comp["params"]
            if comp["action"] == "deregister_ont":
                adapter.deregister_ont(Fsp.parse(p["fsp"]), int(p["ont_index"]))
            elif comp["action"] == "delete_service_port":
                adapter.delete_service_port(int(p["index"]))
            elif comp["action"] == "create_service_port":
                adapter.create_service_port(_sp_spec(p))
            elif comp["action"] == "set_service_port_admin":
                adapter.set_service_port_admin(int(p["index"]), bool(p["enabled"]))
            s.state = StepState.COMPENSATED
            _event(db, job, f"Compensated step {s.seq} ({s.action}) with {comp['action']}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            s.state = StepState.COMPENSATION_FAILED
            s.error = f"Compensation failed: {exc}"
    db.commit()
    return ok


def _finalize_partial(db: Session, job: ProvisioningJob, ctx: dict) -> None:
    if ctx.get("ont_index") is not None and job.operation in (Op.REGISTER_ONT, Op.ACTIVATE_SERVICE) and job.discovery_id:
        _record_registration(db, job, ctx)


def _record_registration(db: Session, job: ProvisioningJob, ctx: dict) -> Ont:
    d = db.get(OntDiscovery, job.discovery_id)
    olt = db.get(Olt, job.olt_id)
    reg = next((s for s in (job.plan or []) if s["action"] == "register_ont"), {}).get("params", {})
    ont = db.scalar(select(Ont).where(Ont.serial_number == d.serial_number))
    if ont is None:
        ont = Ont(serial_number=d.serial_number, is_simulated=job.is_simulated)
        db.add(ont)
    ont.olt_id, ont.pon_port_id, ont.ont_index = olt.id, d.pon_port_id, ctx["ont_index"]
    ont.vendor, ont.model, ont.equipment_id = d.vendor_id, d.model, d.equipment_id
    ont.hardware_version, ont.firmware_version, ont.loid = d.hardware_version, d.software_version, d.loid
    ont.auth_method = reg.get("auth_method", "sn")
    ont.auth_password_enc = job.request.get("auth_password_enc")
    ont.description = reg.get("description", "")
    ont.registration_status = "registered"
    ont.site_id = olt.site_id
    ont.installation_date = ont.installation_date or utcnow().date()
    ont.line_profile_id = db.scalar(select(OltProfile.id).where(OltProfile.olt_id == olt.id, OltProfile.kind == "line",
                                                                OltProfile.vendor_ref == str(reg.get("line_profile_ref"))))
    ont.service_profile_id = db.scalar(select(OltProfile.id).where(OltProfile.olt_id == olt.id, OltProfile.kind == "service",
                                                                   OltProfile.vendor_ref == str(reg.get("service_profile_ref"))))
    new_state = ctx.get("ont_state") or OntState.UNKNOWN
    from app.engines.monitoring import _set_ont_status

    db.flush()
    _set_ont_status(db, ont, new_state, ctx.get("ont_vendor_state"), "Registered via provisioning job")
    d.state = "registered"
    d.registered_ont_id = ont.id
    job.ont_id = ont.id
    fat_port_id = job.request.get("fat_port_id")
    if fat_port_id:
        a = topology.Endpoint(EndpointType.INFRA_PORT, fat_port_id)
        b = topology.Endpoint(EndpointType.ONT, ont.id)
        try:
            topology.validate_link(db, a, b, allow_reserved=bool(job.request.get("use_reserved_port")))
            db.add(FiberLink(a_type=a.type, a_id=a.id, b_type=b.type, b_id=b.id, link_type="drop",
                             verification="unverified", notes=f"Created by provisioning job {job.id}",
                             is_simulated=job.is_simulated))
            port = db.get(InfraPort, fat_port_id)
            port.connected_since = utcnow().date()
            if port.status == PortStatus.RESERVED:
                port.status = PortStatus.AVAILABLE
                port.reserved_for = None
        except ValidationFailed as exc:
            _event(db, job, f"ONT registered but FAT port assignment failed: {exc.details or exc.message}")
    if job.service_id:
        svc = db.get(Service, job.service_id)
        svc.ont_id = ont.id
    return ont


def _finalize_success(db: Session, job: ProvisioningJob, ctx: dict) -> None:
    op = Op(job.operation)
    svc = db.get(Service, job.service_id) if job.service_id else None
    if job.discovery_id and ctx.get("ont_index") is not None:
        _record_registration(db, job, ctx)
    if op == Op.ACTIVATE_SERVICE and svc:
        svc.status = ServiceStatus.ACTIVE
        svc.activation_date = svc.activation_date or utcnow().date()
        svc.template_id = job.template_id
        svc.network_config = {"service_ports": ctx.get("created_service_ports", []), "olt_id": job.olt_id,
                              "applied_by_job": job.id}
    elif op == Op.MODIFY_SERVICE and svc:
        svc.template_id = job.template_id
        svc.network_config = {"service_ports": ctx.get("created_service_ports", []), "olt_id": job.olt_id,
                              "applied_by_job": job.id}
    elif op == Op.SUSPEND_SERVICE and svc:
        svc.status = ServiceStatus.SUSPENDED
        svc.suspended_at = utcnow()
    elif op == Op.RESTORE_SERVICE and svc:
        svc.status = ServiceStatus.ACTIVE
        svc.suspended_at = None
    elif op == Op.DISCONNECT_SERVICE and svc:
        svc.status = ServiceStatus.DISCONNECTED
        svc.network_config = {"service_ports": [], "disconnected_by_job": job.id}
        if job.request.get("deregister_ont") and svc.ont:
            _mark_deregistered(db, svc.ont)
    elif op == Op.DEREGISTER_ONT and job.ont_id:
        _mark_deregistered(db, db.get(Ont, job.ont_id))


def _mark_deregistered(db: Session, ont: Ont) -> None:
    from app.engines.monitoring import _set_ont_status

    ont.registration_status = "deregistered"
    ont.ont_index = None
    _set_ont_status(db, ont, OntState.UNKNOWN, "deregistered", "Deregistered via provisioning job")
    for d in db.scalars(select(OntDiscovery).where(OntDiscovery.serial_number == ont.serial_number)):
        if d.state == "registered":
            d.state = "gone"


# ============================================================================ job handlers
@handler("provisioning.validate")
def _h_validate(db, payload, job):
    pj = db.get(ProvisioningJob, payload["job_id"])
    if pj is None or pj.state != PS.VALIDATION_PENDING:
        return {"skipped": True}
    validate(db, pj)
    return {"state": pj.state, "errors": (pj.validation or {}).get("errors")}


@handler("provisioning.execute")
def _h_execute(db, payload, job):
    pj = db.get(ProvisioningJob, payload["job_id"])
    return execute(db, pj)


def queue_requires_worker() -> bool:
    return not get_settings().embedded_worker
