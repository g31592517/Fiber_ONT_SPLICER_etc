"""Structured (JSON-lines) logging with secret redaction."""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone

_SECRET_PATTERNS = [
    re.compile(r"(?i)(password|passwd|secret|community|token|authorization|auth_key|priv_key)(\s*[=:]\s*|\"\s*:\s*\")([^\s\",]+)"),
    re.compile(r"(?i)bearer\s+[a-z0-9\-_\.]+"),
]


def scrub(text: str) -> str:
    text = _SECRET_PATTERNS[1].sub("Bearer ***", text)
    return _SECRET_PATTERNS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": scrub(record.getMessage()),
        }
        if record.exc_info:
            payload["exc"] = scrub(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root.handlers = [h]
    root.setLevel(level)
    for noisy in ("paramiko", "pysnmp", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
