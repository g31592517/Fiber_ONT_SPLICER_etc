from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import ser
from app.config import get_settings
from app.core.audit import Actor, audit
from app.core.errors import ValidationFailed
from app.db import get_db, utcnow
from app.models import User, UserSession
from app.security.auth import Unauthorized, get_actor, get_current_user, issue_token, limiter
from app.security.crypto import hash_password, validate_password_strength, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=256)


def user_out(u: User) -> dict:
    return ser(u, exclude={"password_hash", "failed_logins"}, role=u.role.name, permissions=u.role.permission_names)


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else None
    limiter.check(f"login:{ip}", get_settings().login_rate_limit_per_minute)
    s = get_settings()
    user = db.scalar(select(User).where(User.username == body.username))
    now = utcnow()
    if user and user.locked_until and user.locked_until > now:
        audit(db, Actor(user.id, user.username, ip), "auth.login", "user", user.id, "Login refused: account locked", outcome="denied")
        db.commit()
        raise Unauthorized("Account temporarily locked after repeated failures")
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        if user:
            user.failed_logins += 1
            if user.failed_logins >= s.max_failed_logins:
                user.locked_until = now + timedelta(minutes=s.lockout_minutes)
                user.failed_logins = 0
            audit(db, Actor(user.id, user.username, ip), "auth.login", "user", user.id, "Login failed", outcome="failure")
        else:
            audit(db, Actor(None, body.username[:64], ip), "auth.login", None, None, "Login failed (unknown user)", outcome="failure")
        db.commit()
        raise Unauthorized("Invalid username or password")
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    token, ttl = issue_token(db, user, ip, request.headers.get("user-agent"))
    audit(db, Actor(user.id, user.username, ip), "auth.login", "user", user.id, "Login succeeded")
    db.commit()
    return {"access_token": token, "token_type": "bearer", "expires_in": ttl, "user": user_out(user)}


@router.post("/logout")
def logout(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sess = db.scalar(select(UserSession).where(UserSession.jti == request.state.jti))
    if sess:
        sess.revoked_at = utcnow()
    db.commit()
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return user_out(user)


@router.post("/change-password")
def change_password(body: PasswordChangeIn, user: User = Depends(get_current_user), actor: Actor = Depends(get_actor),
                    db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise ValidationFailed("Current password is incorrect")
    problems = validate_password_strength(body.new_password)
    if problems:
        raise ValidationFailed("Weak password: " + "; ".join(problems))
    user.password_hash = hash_password(body.new_password)
    user.password_changed_at = utcnow()
    audit(db, actor, "user.password_change", "user", user.id, "Password changed")
    db.commit()
    return {"ok": True}
