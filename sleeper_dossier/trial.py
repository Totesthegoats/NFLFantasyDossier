"""
trial.py — Resolve a league's effective tier, honoring a free trial window.

A league declared "free" in the sheet gets bumped to "normal" for its first
TRIAL_DAYS days (measured from the sheet's Date column), then drops back to
free. Declared "normal"/"dynasty" leagues are unaffected — the trial only
ever upgrades, never downgrades, a declared tier.

Both effective_tier() and just_converted_to_free() are computed fresh from
signup_date on every call — no state file. That's deliberate: batch.py runs
on ephemeral GitHub Actions runners with nothing persisted between weekly
runs, so "have we already emailed this league about its trial ending" can't
be tracked with a local cache. Instead, just_converted_to_free() answers
"did the trial end within roughly the last batch cycle" purely from date
math, which survives a missed/late run without re-firing every week after.
"""

from __future__ import annotations
from datetime import datetime

TRIAL_DAYS = 28  # 4 weeks

# How often batch.py actually runs (dossier.yml: weekly, Tuesdays) — the
# window just_converted_to_free() uses to catch "the trial ended since
# roughly the last run" without needing to remember when that run was.
BATCH_CYCLE_DAYS = 7

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


def _days_since_signup(signup_date: str, today: datetime) -> int | None:
    try:
        signup = _parse_date(signup_date)
    except ValueError as e:
        print(f"  [trial: {e}, treating as free]")
        return None
    return (today - signup).days


def effective_tier(declared_tier: str | None, signup_date: str | None, today: datetime | None = None) -> str:
    declared = normalize_tier(declared_tier)
    if declared != "free" or not signup_date:
        return declared
    today = today or datetime.utcnow()
    days = _days_since_signup(signup_date, today)
    if days is None:
        return declared
    if days < TRIAL_DAYS:
        return "normal"
    return declared


def just_converted_to_free(declared_tier: str | None, signup_date: str | None, today: datetime | None = None) -> bool:
    """True only in the run(s) shortly after a free-trial league's trial
    expires — TRIAL_DAYS <= days_since_signup < TRIAL_DAYS + BATCH_CYCLE_DAYS.
    Lets batch.py send a one-time "you've moved to the free tier" notice
    without a state file (see module docstring)."""
    declared = normalize_tier(declared_tier)
    if declared != "free" or not signup_date:
        return False
    today = today or datetime.utcnow()
    days = _days_since_signup(signup_date, today)
    if days is None:
        return False
    return TRIAL_DAYS <= days < TRIAL_DAYS + BATCH_CYCLE_DAYS
