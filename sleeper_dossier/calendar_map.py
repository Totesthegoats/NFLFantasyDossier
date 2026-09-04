"""
calendar_map.py — Map NFL weeks to real calendar dates and months.

A "month" in this product means a real calendar month (Sept, Oct, ...),
which is what players intuitively understand. The NFL regular season is
a sequence of weeks, each anchored to a Thursday kickoff. We derive each
week's date from the season's Week 1 Thursday, then bucket weeks into the
calendar month their Sunday (the bulk of fantasy scoring) falls in.

Why Sunday and not Thursday: the vast majority of points score on Sunday,
so a week that kicks off Thu Sept 30 but plays Sun Oct 3 belongs to October.
"""

from __future__ import annotations
from datetime import date, timedelta

# Historical NFL Week 1 Thursday kickoff dates. Extend each year.
# (Used only if the API doesn't give us a start date; see week1_thursday().)
KNOWN_WEEK1_THURSDAY = {
    2021: date(2021, 9, 9),
    2022: date(2022, 9, 8),
    2023: date(2023, 9, 7),
    2024: date(2024, 9, 5),
    2025: date(2025, 9, 4),
    2026: date(2026, 9, 10),   # update when the 2026 schedule is confirmed
}

MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


def week1_thursday(season_year: int) -> date:
    """Best-known Week 1 Thursday for a season; falls back to a heuristic."""
    if season_year in KNOWN_WEEK1_THURSDAY:
        return KNOWN_WEEK1_THURSDAY[season_year]
    # Heuristic: NFL opens the Thursday after US Labor Day (first Mon of Sept).
    d = date(season_year, 9, 1)
    while d.weekday() != 0:          # find first Monday (Labor Day)
        d += timedelta(days=1)
    return d + timedelta(days=3)     # Thursday of that week


def week_sunday(season_year: int, week: int) -> date:
    """The Sunday on which most of `week` is played."""
    thu = week1_thursday(season_year)
    week_thu = thu + timedelta(weeks=week - 1)
    return week_thu + timedelta(days=3)   # Thu -> Sun


def week_to_month(season_year: int, week: int) -> tuple[int, int]:
    """Return (calendar_year, calendar_month) for a given NFL week."""
    s = week_sunday(season_year, week)
    return (s.year, s.month)


def group_weeks_by_month(season_year: int, weeks: list[int]) -> dict:
    """
    Bucket played week numbers into calendar months.
    Returns {(year, month): [week, ...]} ordered by date.
    """
    buckets: dict[tuple[int, int], list[int]] = {}
    for w in sorted(weeks):
        key = week_to_month(season_year, w)
        buckets.setdefault(key, []).append(w)
    return buckets


def month_label(year: int, month: int) -> str:
    return f"{MONTH_NAMES[month]} {year}"


def previous_month_weeks(buckets: dict, key: tuple) -> list[int] | None:
    """Weeks for the calendar month immediately before `key`, or None if
    `key` is the first month with data (no prior month to compare against)."""
    keys = sorted(buckets.keys())
    idx = keys.index(key)
    if idx == 0:
        return None
    return buckets[keys[idx - 1]]


def current_month_weeks(season_year: int, played_weeks: list[int]) -> tuple:
    """
    The most recent calendar month present in the played weeks, plus its weeks.
    Useful for "generate this month's report" with no explicit month given.
    Returns ((year, month), [weeks]) or (None, []).
    """
    buckets = group_weeks_by_month(season_year, played_weeks)
    if not buckets:
        return None, []
    key = sorted(buckets.keys())[-1]
    return key, buckets[key]
