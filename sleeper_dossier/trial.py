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
        print(f"  [trial: {e}]")
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


def trial_days_remaining(declared_tier: str | None, signup_date: str | None, today: datetime | None = None) -> int | None:
    """Days left in a free-trial league's full-tier window, or None if the
    league isn't currently on trial (declared tier isn't "free", no/unparseable
    signup_date, or the trial has already ended). Used to remind trial leagues
    every week — via their regular dossier email — how long they have left."""
    declared = normalize_tier(declared_tier)
    if declared != "free" or not signup_date:
        return None
    today = today or datetime.utcnow()
    days = _days_since_signup(signup_date, today)
    if days is None or days >= TRIAL_DAYS:
        return None
    return TRIAL_DAYS - days


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


# How often the "welcome new signups" cron runs (welcome.yml: daily). The
# window below is wider than the cadence (2 days, not 1) so a signup still
# gets welcomed even if one day's run is late or fails to fire.
WELCOME_WINDOW_DAYS = 2


def is_new_signup(signup_date: str | None, today: datetime | None = None) -> bool:
    """True if signup_date falls within the last WELCOME_WINDOW_DAYS days —
    used to spot sheet rows that just appeared, so batch.py's --welcome-new
    mode can send a one-time welcome email. Same stateless approach as
    just_converted_to_free(): no record of who's already been welcomed, just
    "is this signup recent" from date math."""
    if not signup_date:
        return False
    today = today or datetime.utcnow()
    days = _days_since_signup(signup_date, today)
    if days is None:
        return False
    return 0 <= days < WELCOME_WINDOW_DAYS
