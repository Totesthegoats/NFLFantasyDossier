#!/usr/bin/env python3
"""
sleeper_diagnose.py — Diagnose why a Sleeper league week query returns no data.

Usage:
    python sleeper_diagnose.py <league_id> [week]

Reuses sleeper_dossier's existing HTTP client so there's one code path for
talking to Sleeper. Every section is wrapped so a single failing call doesn't
crash the rest of the report.
"""

from __future__ import annotations
import sys
import textwrap

# Reuse the package's API base URL and HTTP helper — no auth needed.
from sleeper_dossier.data import API, _get

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _safe_get(url: str, label: str):
    """Call _get, returning None and printing a message on any failure."""
    try:
        return _get(url)
    except Exception as e:
        print(f"  ✗ {label}: {e}")
        return None


def _section(title: str):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _subsection(title: str):
    print()
    print(f"  ── {title}")
    print()


def _wrap(text: str, indent: int = 4) -> str:
    prefix = " " * indent
    return textwrap.fill(text, width=70, initial_indent=prefix,
                         subsequent_indent=prefix)


FORMAT_NAMES = {0: "Redraft", 1: "Keeper", 2: "Dynasty"}
STATUS_LABEL = {
    "pre_draft": "[PENDING]", "drafting": "[DRAFTING]", "in_season": "[ACTIVE]",
    "complete": "[COMPLETE]", "postponed": "[POSTPONED]",
}

# ──────────────────────────────────────────────────────────────────────────────
# Check 1: Global NFL state
# ──────────────────────────────────────────────────────────────────────────────

def check_nfl_state() -> dict | None:
    _section("1. GLOBAL NFL STATE")
    state = _safe_get(f"{API}/state/nfl", "GET /state/nfl")
    if not state:
        return None
    print(f"    Season          : {state.get('season')}")
    print(f"    Season type     : {state.get('season_type')}")
    print(f"    Current week    : {state.get('week')}")
    print(f"    Leg (scored)    : {state.get('leg')}")
    print(f"    Display week    : {state.get('display_week')}")
    print(f"    League season   : {state.get('league_season')}")
    print(f"    League create   : {state.get('league_create_season')}")
    return state


# ──────────────────────────────────────────────────────────────────────────────
# Check 2: League object
# ──────────────────────────────────────────────────────────────────────────────

def check_league(league_id: str, nfl_state: dict | None) -> dict | None:
    _section("2. LEAGUE OBJECT")
    league = _safe_get(f"{API}/league/{league_id}", f"GET /league/{league_id}")
    if not league:
        print(_wrap("Could not load league. Check the league_id — it may be "
                    "wrong, deleted, or private.", indent=4))
        return None

    settings = league.get("settings") or {}
    fmt_code  = settings.get("type", 0)
    taxi      = settings.get("taxi_slots", 0)
    status    = league.get("status", "unknown")

    fmt_label = FORMAT_NAMES.get(fmt_code, f"unknown ({fmt_code})")
    if fmt_code == 2 or taxi:
        fmt_label += " (DYNASTY)"

    print(f"    Name            : {league.get('name')}")
    print(f"    Status          : {STATUS_LABEL.get(status, '[?]')} {status}")
    print(f"    League season   : {league.get('season')}")
    print(f"    Format          : {fmt_label}")
    print(f"    Taxi slots      : {taxi}")
    print(f"    Total rosters   : {league.get('total_rosters')}")
    print(f"    Settings.leg    : {settings.get('leg')}")
    print(f"    Start week      : {settings.get('start_week')}")
    print(f"    Last scored leg : {settings.get('last_scored_leg')}")
    print(f"    Previous league : {league.get('previous_league_id') or '(none — first season)'}")

    if nfl_state and league.get("season") != nfl_state.get("league_season"):
        print()
        print(_wrap(
            f"⚠️  SEASON MISMATCH: this league is for the {league.get('season')} "
            f"season but the current Sleeper league season is "
            f"{nfl_state.get('league_season')}. You are querying an old season's "
            f"league_id. Dynasty/keeper leagues get a NEW league_id every season "
            f"— the current one will not be this ID.", indent=4))

    return league


# ──────────────────────────────────────────────────────────────────────────────
# Check 3: Status interpretation
# ──────────────────────────────────────────────────────────────────────────────

def interpret_status(league: dict, nfl_state: dict | None):
    _section("3. STATUS INTERPRETATION")
    status   = league.get("status", "unknown")
    settings = league.get("settings") or {}
    last_leg = settings.get("last_scored_leg", 0)

    if status in ("pre_draft", "drafting"):
        print(_wrap(
            "🔴 ROOT CAUSE (most likely): The league has NOT started play yet "
            "this season. Matchup endpoints return an empty array for any week "
            "when the status is pre_draft or drafting. This is the single most "
            "common cause of empty matchup data on dynasty leagues — the season "
            "rolled over, a new league_id was created, but the draft hasn't "
            "happened yet so no weeks have any game data.", indent=4))

    elif status == "complete":
        print(_wrap(
            f"⚪ The season is complete. Weeks beyond last_scored_leg "
            f"({last_leg}) will return an empty array. Weeks ≤ {last_leg} "
            f"should have data.", indent=4))

    elif status == "in_season":
        cur_week = (nfl_state or {}).get("leg", 0)
        season_type = (nfl_state or {}).get("season_type", "")
        if cur_week == 0 or season_type == "off":
            print(_wrap(
                "ROOT CAUSE: The Sleeper league status is in_season, but the "
                "NFL is currently in the OFFSEASON (global leg = 0, season_type = "
                f"'{season_type}'). The league's matchup schedule exists but no "
                "real games have been played yet — all weeks return entries with "
                "zero points. Data will appear once the 2026 NFL season kicks off "
                "and games are scored.", indent=4))
        else:
            print(_wrap(
                f"League is in season. Weeks 1-{cur_week} should have matchup "
                f"data. Weeks beyond {cur_week} return an empty array — they "
                f"haven't been played yet.", indent=4))

    else:
        print(_wrap(f"❓ Unknown status '{status}'. Cannot determine expected "
                    f"matchup availability.", indent=4))


# ──────────────────────────────────────────────────────────────────────────────
# Check 4: Matchup probe
# ──────────────────────────────────────────────────────────────────────────────

def probe_matchups(league_id: str, probe_weeks: list[int]):
    _section("4. MATCHUP PROBE")
    print(f"    Probing weeks: {probe_weeks}")

    for wk in probe_weeks:
        matchups = _safe_get(f"{API}/league/{league_id}/matchups/{wk}",
                             f"GET matchups/{wk}")
        if matchups is None:
            print(f"    Week {wk:>2}: request failed")
            continue

        if not matchups:
            print(f"    Week {wk:>2}: EMPTY ARRAY — no data (draft not started, "
                  f"or week beyond current season)")
            continue

        roster_count = len(matchups)
        with_points  = sum(1 for m in matchups if (m.get("points") or 0) > 0)
        pts_sample   = [round(m.get("points") or 0, 1) for m in matchups[:4]]
        pts_str      = ", ".join(str(p) for p in pts_sample)
        if len(matchups) > 4:
            pts_str += ", …"

        status_tag = "✓ has points" if with_points else "⚠ all-zero points"
        print(f"    Week {wk:>2}: {roster_count} entries, "
              f"{with_points}/{roster_count} with points  [{pts_str}]  {status_tag}")


# ──────────────────────────────────────────────────────────────────────────────
# Check 5: Roster probe
# ──────────────────────────────────────────────────────────────────────────────

def probe_rosters(league_id: str):
    _section("5. ROSTER PROBE")
    rosters = _safe_get(f"{API}/league/{league_id}/rosters", "GET /rosters")
    if not rosters:
        print(_wrap("Could not load rosters. The league_id may be wrong.", indent=4))
        return

    print(f"    Rosters returned: {len(rosters)}")
    sample = rosters[0]
    s = sample.get("settings") or {}
    print(f"    Sample roster #{sample.get('roster_id')}:")
    print(f"      Wins   : {s.get('wins', '—')}")
    print(f"      Losses : {s.get('losses', '—')}")
    print(f"      PF     : {(s.get('fpts') or 0) + (s.get('fpts_decimal', 0) or 0) / 100:.2f}")
    print()
    if any((s.get("wins", 0) + s.get("losses", 0)) > 0 for r in rosters
           for s in [(r.get("settings") or {})]):
        print(_wrap("✓ Rosters have win/loss records — at least some weeks have "
                    "been played and scored this season.", indent=4))
    else:
        print(_wrap("⚠ All rosters show 0 wins and 0 losses — no weeks have been "
                    "scored yet this season. This confirms pre-season status.", indent=4))


# ──────────────────────────────────────────────────────────────────────────────
# Check 6: Summary
# ──────────────────────────────────────────────────────────────────────────────

def summarise(league: dict | None, nfl_state: dict | None,
              probe_weeks: list[int], league_id: str):
    _section("6. SUMMARY — MOST LIKELY CAUSES")
    causes = []

    if league is None:
        causes.append("Wrong or deleted league_id — the league could not be loaded.")
        for i, c in enumerate(causes, 1):
            print(f"    {i}. {c}")
        return

    status   = league.get("status", "unknown")
    settings = league.get("settings") or {}
    fmt_code = settings.get("type", 0)
    taxi     = settings.get("taxi_slots", 0)
    league_season = league.get("season")
    nfl_season    = (nfl_state or {}).get("league_season")

    nfl_offseason = (nfl_state or {}).get("season_type") == "off" or \
                    (nfl_state or {}).get("leg", 1) == 0

    if nfl_offseason and status == "in_season":
        causes.append(
            "NFL is in the offseason (season_type=off, leg=0). The league has "
            "a valid schedule but no games have been played yet — all matchup "
            "weeks return entries with zero points. Data will appear once the "
            f"{(nfl_state or {}).get('league_season', 'new')} season kicks off.")
    elif status in ("pre_draft", "drafting"):
        causes.append(
            f"League status is '{status}' — play has not started this season. "
            f"All matchup weeks will return empty arrays until after the draft.")
    if league_season and nfl_season and league_season != nfl_season:
        causes.append(
            f"Season mismatch: this league_id belongs to {league_season} but the "
            f"current season is {nfl_season}. You need the {nfl_season} league_id "
            f"(a NEW id created when the dynasty league rolled over for this season).")
    if nfl_state:
        cur_leg = nfl_state.get("leg", 0)
        if probe_weeks and max(probe_weeks) > cur_leg:
            causes.append(
            f"One or more probed weeks ({max(probe_weeks)}) exceed the current "
            f"NFL week ({cur_leg}). Those weeks haven't happened yet.")
    if fmt_code == 2 or taxi:
        causes.append(
            "This is a dynasty/keeper league. Roster data carries over each year "
            "but matchups only exist from the moment the season starts. Check "
            "previous_league_id to find past seasons, or the active league's "
            "new id to find this season's live data.")

    if not causes:
        causes.append(
            "No obvious cause found. The league is in_season and week data "
            "should exist — check whether you're probing a valid week number "
            "between start_week and the current NFL week.")

    for i, c in enumerate(causes, 1):
        print(_wrap(f"{i}. {c}", indent=4))
        print()


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        print("Usage: python sleeper_diagnose.py <league_id> [week]")
        sys.exit(1)

    league_id  = sys.argv[1].strip()
    probe_week = int(sys.argv[2]) if len(sys.argv) > 2 else None

    print()
    print("#" * 70)
    print(f"  SLEEPER LEAGUE DIAGNOSTIC")
    print(f"  League ID : {league_id}")
    if probe_week:
        print(f"  Probing   : week {probe_week}")
    print("#" * 70)

    nfl_state  = check_nfl_state()
    league     = check_league(league_id, nfl_state)

    if league:
        interpret_status(league, nfl_state)

    # Build the probe-week list
    if probe_week:
        probe_weeks = [probe_week]
    else:
        cur_leg = (nfl_state or {}).get("leg", 0)
        # Spread across the season: early, mid, late, current
        candidates = [1, 5, 10, 14, cur_leg]
        seen: set[int] = set()
        probe_weeks = [w for w in candidates if w > 0 and not (w in seen or seen.add(w))]

    probe_matchups(league_id, probe_weeks)
    probe_rosters(league_id)
    summarise(league, nfl_state, probe_weeks, league_id)

    print()


if __name__ == "__main__":
    main()
