"""Domain errors, mapped to HTTP responses centrally in ``app.main``."""
from __future__ import annotations

from typing import Any


class DomainError(Exception):
    status_code = 400
    code = "domain_error"

    def __init__(self, message: str, details: Any = None):
        super().__init__(message)
        self.message = message
        self.details = details


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class Conflict(DomainError):
    status_code = 409
    code = "conflict"


class ValidationFailed(DomainError):
    status_code = 422
    code = "validation_failed"


class PermissionDenied(DomainError):
    status_code = 403
    code = "permission_denied"


class Unsupported(DomainError):
    status_code = 409
    code = "unsupported_operation"


class RateLimited(DomainError):
    status_code = 429
    code = "rate_limited"


def get_or_404(db, model, ident, label: str | None = None):
    obj = db.get(model, ident)
    if obj is None:
        raise NotFound(f"{label or model.__name__} {ident} not found")
    return obj
