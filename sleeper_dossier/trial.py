"""
trial.py — Resolve a league's effective tier, honoring a free trial window.

A league declared "free" in the sheet gets bumped to "normal" for its first
TRIAL_DAYS days (measured from the sheet's Date column), then drops back to
free. Declared "normal"/"dynasty" leagues are unaffected — the trial only
ever upgrades, never downgrades, a declared tier.
"""

from __future__ import annotations
from datetime import datetime, timedelta

TRIAL_DAYS = 14

_DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y"]

_TIER_ALIASES = {
    "free": "free",
    "normal": "normal",
    "dynasty": "dynasty",
}


def normalize_tier(raw: str | None) -> str:
    key = (raw or "free").strip().lower()
    if key in _TIER_ALIASES:
        return _TIER_ALIASES[key]
    if "dynasty" in key or "5" in key:
        return "dynasty"
    if "normal" in key or "paid" in key:
        return "normal"
    return "free"


def _parse_date(s: str) -> datetime:
    s = s.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date format: {s!r}")


def effective_tier(declared_tier: str | None, signup_date: str | None, today: datetime | None = None) -> str:
    declared = normalize_tier(declared_tier)
    if declared != "free" or not signup_date:
        return declared
    today = today or datetime.utcnow()
    try:
        signup = _parse_date(signup_date)
    except ValueError as e:
        print(f"  [trial: {e}, treating as free]")
        return declared
    if today - signup < timedelta(days=TRIAL_DAYS):
        return "normal"
    return declared
