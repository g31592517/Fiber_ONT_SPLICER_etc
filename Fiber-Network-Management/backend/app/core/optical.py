"""Optical threshold resolution and evaluation."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Ont, OpticalThreshold


def resolve_threshold(db: Session, vendor: str | None, model: str | None) -> OpticalThreshold | None:
    """Most specific threshold wins: vendor+model > model > vendor > default (both empty)."""
    rows = list(db.scalars(select(OpticalThreshold)))
    best, best_score = None, -1
    for t in rows:
        if t.model and (not model or t.model.lower() != model.lower()):
            continue
        if t.vendor and (not vendor or t.vendor.lower() != vendor.lower()):
            continue
        score = (2 if t.model else 0) + (1 if t.vendor else 0)
        if score > best_score:
            best, best_score = t, score
    return best


def evaluate(t: OpticalThreshold | None, rx: float | None, olt_rx: float | None) -> tuple[str | None, list[dict]]:
    """Return (overall status, list of violations). None status = no reading or no threshold configured."""
    if t is None or (rx is None and olt_rx is None):
        return None, []
    v: list[dict] = []
    if rx is not None:
        if rx <= t.rx_critical_low:
            v.append({"metric": "rx_power_dbm", "level": "critical", "direction": "low", "value": rx, "limit": t.rx_critical_low})
        elif rx <= t.rx_warning_low:
            v.append({"metric": "rx_power_dbm", "level": "warning", "direction": "low", "value": rx, "limit": t.rx_warning_low})
        elif t.rx_critical_high is not None and rx >= t.rx_critical_high:
            v.append({"metric": "rx_power_dbm", "level": "critical", "direction": "high", "value": rx, "limit": t.rx_critical_high})
        elif t.rx_warning_high is not None and rx >= t.rx_warning_high:
            v.append({"metric": "rx_power_dbm", "level": "warning", "direction": "high", "value": rx, "limit": t.rx_warning_high})
    if olt_rx is not None and t.olt_rx_critical_low is not None:
        if olt_rx <= t.olt_rx_critical_low:
            v.append({"metric": "olt_rx_power_dbm", "level": "critical", "direction": "low", "value": olt_rx, "limit": t.olt_rx_critical_low})
        elif t.olt_rx_warning_low is not None and olt_rx <= t.olt_rx_warning_low:
            v.append({"metric": "olt_rx_power_dbm", "level": "warning", "direction": "low", "value": olt_rx, "limit": t.olt_rx_warning_low})
    if any(x["level"] == "critical" for x in v):
        return "critical", v
    if v:
        return "warning", v
    return "ok", v


def threshold_for_ont(db: Session, ont: Ont) -> OpticalThreshold | None:
    return resolve_threshold(db, ont.vendor, ont.model)
