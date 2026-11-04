from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import user_out
from app.api.common import Page, paginate, ser
from app.core.audit import Actor, audit
from app.core.errors import Conflict, ValidationFailed, get_or_404
from app.db import get_db, utcnow
from app.models import AppSetting, AuditLog, Role, RolePermission, User, UserSession
from app.security.auth import require
from app.security.crypto import hash_password, validate_password_strength
from app.security.permissions import PERMISSIONS

router = APIRouter(tags=["administration"])


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    email: str | None = None
    full_name: str = ""
    password: str = Field(min_length=10)
    role_id: int
    is_active: bool = True


class UserPatch(BaseModel):
    email: str | None = None
    full_name: str | None = None
    role_id: int | None = None
    is_active: bool | None = None
    password: str | None = Field(None, min_length=10)


class RoleIn(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    description: str = ""
    permissions: list[str] = []


DEFAULT_SETTINGS: dict[str, tuple[Any, str]] = {
    "terminology.fat": ("FAT", "Preferred name for fiber access terminals (FAT / FDB / FDT / Fiber Distribution Terminal)"),
    "monitoring.fat_capacity_warning_percent": (80, "FAT utilization above which a capacity warning is shown"),
    "monitoring.pon_capacity_warning_percent": (80, "PON port ONT utilization above which a warning is shown"),
    "integration.auto_approve": (False, "Integration API requests are executed without manual approval"),
}


@router.get("/permissions")
def list_permissions(_: Actor = Depends(require("roles.manage"))):
    return [{"name": k, "description": v} for k, v in PERMISSIONS.items()]


@router.get("/roles")
def list_roles(_: Actor = Depends(require("users.manage")), db: Session = Depends(get_db)):
    return [ser(r, permissions=r.permission_names) for r in db.scalars(select(Role).order_by(Role.id))]


@router.post("/roles", status_code=201)
def create_role(body: RoleIn, actor: Actor = Depends(require("roles.manage")), db: Session = Depends(get_db)):
    bad = [p for p in body.permissions if p not in PERMISSIONS]
    if bad:
        raise ValidationFailed(f"Unknown permissions: {bad}")
    if db.scalar(select(Role).where(Role.name == body.name)):
        raise Conflict("Role name already exists")
    role = Role(name=body.name, description=body.description, permissions=[RolePermission(permission=p) for p in set(body.permissions)])
    db.add(role)
    db.flush()
    audit(db, actor, "role.create", "role", role.id, f"Role {role.name} created", {"permissions": sorted(body.permissions)})
    db.commit()
    return ser(role, permissions=role.permission_names)


@router.put("/roles/{role_id}")
def update_role(role_id: int, body: RoleIn, actor: Actor = Depends(require("roles.manage")), db: Session = Depends(get_db)):
    role = get_or_404(db, Role, role_id)
    if role.builtin and role.name == "Super Administrator" and set(body.permissions) != set(PERMISSIONS):
        raise ValidationFailed("The Super Administrator role must keep every permission")
    bad = [p for p in body.permissions if p not in PERMISSIONS]
    if bad:
        raise ValidationFailed(f"Unknown permissions: {bad}")
    before = role.permission_names
    role.description = body.description
    if not role.builtin:
        role.name = body.name
    role.permissions = [RolePermission(permission=p) for p in sorted(set(body.permissions))]
    audit(db, actor, "role.update", "role", role.id, f"Role {role.name} permissions changed",
          {"before": before, "after": sorted(set(body.permissions))})
    db.commit()
    db.refresh(role)
    return ser(role, permissions=role.permission_names)


@router.delete("/roles/{role_id}", status_code=204)
def delete_role(role_id: int, actor: Actor = Depends(require("roles.manage")), db: Session = Depends(get_db)):
    role = get_or_404(db, Role, role_id)
    if role.builtin:
        raise ValidationFailed("Built-in roles cannot be deleted")
    if db.scalar(select(User.id).where(User.role_id == role.id)):
        raise ValidationFailed("Role is assigned to users")
    db.delete(role)
    audit(db, actor, "role.delete", "role", role_id, f"Role {role.name} deleted")
    db.commit()


@router.get("/users")
def list_users(_: Actor = Depends(require("users.manage")), db: Session = Depends(get_db)):
    return [user_out(u) for u in db.scalars(select(User).order_by(User.username))]


@router.post("/users", status_code=201)
def create_user(body: UserIn, actor: Actor = Depends(require("users.manage")), db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.username == body.username)):
        raise Conflict("Username already exists")
    get_or_404(db, Role, body.role_id)
    problems = validate_password_strength(body.password)
    if problems:
        raise ValidationFailed("Weak password: " + "; ".join(problems))
    u = User(username=body.username, email=body.email, full_name=body.full_name, password_hash=hash_password(body.password),
             role_id=body.role_id, is_active=body.is_active, password_changed_at=utcnow())
    db.add(u)
    db.flush()
    audit(db, actor, "user.create", "user", u.id, f"User {u.username} created", {"role_id": body.role_id})
    db.commit()
    db.refresh(u)
    return user_out(u)


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserPatch, actor: Actor = Depends(require("users.manage")), db: Session = Depends(get_db)):
    u = get_or_404(db, User, user_id)
    changes = body.model_dump(exclude_unset=True)
    if "password" in changes:
        problems = validate_password_strength(changes["password"])
        if problems:
            raise ValidationFailed("Weak password: " + "; ".join(problems))
        u.password_hash = hash_password(changes.pop("password"))
        u.password_changed_at = utcnow()
        changes["password"] = "changed"
    if "role_id" in changes:
        get_or_404(db, Role, changes["role_id"])
        if u.id == actor.user_id:
            raise ValidationFailed("You cannot change your own role")
    for k, v in changes.items():
        if k != "password":
            setattr(u, k, v)
    if changes.get("is_active") is False or "password" in changes or "role_id" in changes:
        for s in db.scalars(select(UserSession).where(UserSession.user_id == u.id, UserSession.revoked_at.is_(None))):
            s.revoked_at = utcnow()
    audit(db, actor, "user.update" if "role_id" not in changes else "user.role_change", "user", u.id,
          f"User {u.username} updated", changes)
    db.commit()
    db.refresh(u)
    return user_out(u)


@router.get("/audit-logs")
def audit_logs(page: Page = Depends(), action: str | None = None, entity_type: str | None = None, entity_id: str | None = None,
               username: str | None = None, outcome: str | None = None, q: str | None = Query(None, max_length=100),
               _: Actor = Depends(require("audit.read")), db: Session = Depends(get_db)):
    stmt = select(AuditLog).order_by(AuditLog.id.desc())
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if username:
        stmt = stmt.where(AuditLog.username == username)
    if outcome:
        stmt = stmt.where(AuditLog.outcome == outcome)
    if q:
        stmt = stmt.where(AuditLog.summary.ilike(f"%{q}%"))
    return paginate(db, stmt, page)


def get_setting(db: Session, key: str):
    row = db.get(AppSetting, key)
    return row.value if row else DEFAULT_SETTINGS.get(key, (None, ""))[0]


@router.get("/settings")
def list_settings(db: Session = Depends(get_db), _: Actor = Depends(require("network.read"))):
    from app.config import get_settings

    s = get_settings()
    out = {k: {"value": get_setting(db, k), "description": d} for k, (_, d) in DEFAULT_SETTINGS.items()}
    out["map.tile_url"] = {"value": s.map_tile_url, "description": "Map tile URL (environment: FNMS_MAP_TILE_URL)", "readonly": True}
    out["map.attribution"] = {"value": s.map_attribution, "description": "Map attribution", "readonly": True}
    out["simulator.enabled"] = {"value": s.simulator_enabled, "description": "Simulator enabled (environment)", "readonly": True}
    out["adapters.allow_unverified_writes"] = {"value": s.allow_unverified_adapter_writes,
                                               "description": "Write operations on unverified adapters (environment)", "readonly": True}
    return out


class SettingIn(BaseModel):
    value: Any


@router.put("/settings/{key}")
def put_setting(key: str, body: SettingIn, actor: Actor = Depends(require("settings.manage")), db: Session = Depends(get_db)):
    if key not in DEFAULT_SETTINGS:
        raise ValidationFailed(f"Unknown or read-only setting '{key}'")
    row = db.get(AppSetting, key) or AppSetting(key=key, description=DEFAULT_SETTINGS[key][1])
    before = row.value
    row.value = body.value
    db.add(row)
    audit(db, actor, "settings.update", "setting", key, f"Setting {key} changed", {"before": before, "after": body.value})
    db.commit()
    return {"key": key, "value": row.value}
