"""ONT device operations (reboot, enable, disable) executed in the background."""
from __future__ import annotations

from app.adapters.base import AdapterError, Capability, Fsp
from app.adapters.registry import adapter_class, build_adapter, write_allowed
from app.core.audit import Actor, audit
from app.core.errors import Unsupported, ValidationFailed
from app.jobs.queue import handler
from app.models import Ont

OPS = {"reboot": Capability.ONT_REBOOT, "enable": Capability.ONT_ENABLE_DISABLE, "disable": Capability.ONT_ENABLE_DISABLE}


def precheck(ont: Ont, op: str) -> None:
    if op not in OPS:
        raise ValidationFailed(f"Unknown ONT operation '{op}'")
    if ont.registration_status != "registered" or ont.olt is None or ont.pon_port is None or ont.ont_index is None:
        raise ValidationFailed("ONT is not registered on an OLT")
    ok, why = write_allowed(adapter_class(ont.olt.adapter), OPS[op])
    if not ok:
        raise Unsupported(why)


@handler("ont.operation")
def _h_op(db, payload, job):
    ont = db.get(Ont, payload["ont_id"])
    op = payload["operation"]
    precheck(ont, op)
    fsp = Fsp(ont.pon_port.frame, ont.pon_port.slot, ont.pon_port.port)
    actor = Actor(payload.get("user_id"), payload.get("username", "system"))
    with build_adapter(ont.olt) as adapter:
        try:
            if op == "reboot":
                r = adapter.reboot_ont(fsp, ont.ont_index)
            else:
                r = adapter.set_ont_admin(fsp, ont.ont_index, op == "enable")
        except AdapterError as exc:
            audit(db, actor, f"ont.{op}", "ont", ont.id, f"{op} failed: {exc}", outcome="failure")
            raise
    if op in ("enable", "disable") and r.ok:
        ont.admin_status = "enabled" if op == "enable" else "disabled"
    audit(db, actor, f"ont.{op}", "ont", ont.id, f"{op} {ont.serial_number}: {r.message}", {"commands": r.commands})
    return {"ok": r.ok, "message": r.message, "changed": r.changed, "commands": r.commands}
