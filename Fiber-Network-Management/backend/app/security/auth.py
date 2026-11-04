"""Authentication (JWT bearer tokens backed by server-side sessions), authorization and rate limiting."""
from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import timedelta

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.audit import Actor
from app.core.errors import DomainError, PermissionDenied, RateLimited
from app.db import get_db, utcnow
from app.models import User, UserSession

_bearer = HTTPBearer(auto_error=False)


class Unauthorized(DomainError):
    status_code = 401
    code = "unauthorized"


def issue_token(db: Session, user: User, ip: str | None, user_agent: str | None) -> tuple[str, int]:
    s = get_settings()
    jti = uuid.uuid4().hex
    expires = utcnow() + timedelta(minutes=s.access_token_minutes)
    db.add(UserSession(jti=jti, user_id=user.id, expires_at=expires, ip_address=ip,
                       user_agent=(user_agent or "")[:255]))
    token = jwt.encode({"sub": str(user.id), "jti": jti, "exp": expires, "iat": utcnow()}, s.jwt_secret, algorithm="HS256")
    return token, s.access_token_minutes * 60


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def get_current_user(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
                     db: Session = Depends(get_db)) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise Unauthorized("Authentication required")
    try:
        payload = jwt.decode(creds.credentials, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise Unauthorized("Invalid or expired token")
    sess = db.scalar(select(UserSession).where(UserSession.jti == payload.get("jti")))
    if sess is None or sess.revoked_at is not None or sess.expires_at < utcnow():
        raise Unauthorized("Session expired or revoked")
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise Unauthorized("User disabled")
    request.state.user = user
    request.state.jti = sess.jti
    return user


def get_actor(request: Request, user: User = Depends(get_current_user)) -> Actor:
    return Actor(user.id, user.username, _client_ip(request), set(user.role.permission_names))


def require(*perms: str):
    """Dependency factory: the user must hold *all* listed permissions."""

    def _dep(actor: Actor = Depends(get_actor)) -> Actor:
        missing = [p for p in perms if not actor.has(p)]
        if missing:
            raise PermissionDenied(f"Missing permission: {', '.join(missing)}", {"missing": missing})
        return actor

    return _dep


def require_any(*perms: str):
    def _dep(actor: Actor = Depends(get_actor)) -> Actor:
        if not any(actor.has(p) for p in perms):
            raise PermissionDenied(f"Requires one of: {', '.join(perms)}")
        return actor

    return _dep


class RateLimiter:
    """Simple in-process sliding-window limiter. Adequate for a single API instance;
    put a shared limiter (reverse proxy / Redis) in front for multi-instance deployments."""

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: float = 60.0) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                raise RateLimited("Too many requests, slow down")
            q.append(now)

    def reset(self):
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def operation_rate_limit(actor: Actor = Depends(get_actor)) -> Actor:
    limiter.check(f"op:{actor.username}", get_settings().operation_rate_limit_per_minute)
    return actor
