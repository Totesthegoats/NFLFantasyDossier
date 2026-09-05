"""
history.py — Cross-season rivalry data: who's beaten whom, all-time.

Sleeper re-creates a league each year with a new league_id, chained via
previous_league_id. roster_id resets every season but owner_id (the actual
Sleeper user account) doesn't, so head-to-head is tracked by owner_id pair,
not roster_id.

Past seasons are immutable once complete, so the whole chain is fetched once
and cached to disk forever (no TTL) — walking N seasons' worth of weekly
matchups on every run would be wasteful and slow. The *current* season is
deliberately never written to this cache, since it's still changing week to
week; callers merge in this season's matchups-so-far live via
current_season_results().
"""

from __future__ import annotations
import json
import os

from . import data as D
from . import stats as S

_CACHE_DIR = D._CACHE_DIR


def _cache_path(league_id: str) -> str:
    return os.path.join(_CACHE_DIR, f"history_{league_id}.json")


def _fetch_season_results(league_id: str):
    """One past season: (season_year, previous_league_id, results, owner_names)."""
    league = D._get(f"{D.API}/league/{league_id}")
    season_year = str(league.get("season") or "")
    prev_id = league.get("previous_league_id")

    rosters = D._get(f"{D.API}/league/{league_id}/rosters") or []
    users = D._get(f"{D.API}/league/{league_id}/users") or []
    users_by_id = {u["user_id"]: u for u in users}
    roster_owner = {r["roster_id"]: r.get("owner_id") for r in rosters}

    owner_names = {}
    for r in rosters:
        owner_id = r.get("owner_id")
        if not owner_id:
            continue
        user = users_by_id.get(owner_id, {})
        owner_names[owner_id] = (user.get("metadata") or {}).get("team_name") or user.get("display_name") or owner_id

    results = []
    for wk in range(1, D._LAST_REGULAR_SEASON_WEEK + 1):
        matchups = D._get(f"{D.API}/league/{league_id}/matchups/{wk}")
        if not matchups:
            continue
        by_mid = {}
        for m in matchups:
            by_mid.setdefault(m.get("matchup_id"), []).append((m["roster_id"], m.get("points") or 0.0))
        for lst in by_mid.values():
            if len(lst) != 2:
                continue
            (ra, pa), (rb, pb) = lst
            oa, ob = roster_owner.get(ra), roster_owner.get(rb)
            if not oa or not ob:
                continue
            winner = oa if pa > pb else (ob if pb > pa else None)
            results.append({"season": season_year, "week": wk, "owner_a": oa, "owner_b": ob,
                            "winner": winner, "points_a": pa, "points_b": pb})
    return season_year, prev_id, results, owner_names


def _build_chain_results(start_league_id: str):
    """Walk previous_league_id backward from `start_league_id` (the current
    season's own league_id, NOT included — that one's fetched live)."""
    league = D._get(f"{D.API}/league/{start_league_id}")
    prev_id = league.get("previous_league_id")

    chain, all_results, all_owner_names = [], [], {}
    while prev_id:
        _season_year, next_prev_id, results, owner_names = _fetch_season_results(prev_id)
        chain.append(prev_id)
        all_results.extend(results)
        all_owner_names.update(owner_names)
        prev_id = next_prev_id
    return chain, all_results, all_owner_names


def build_index(league_id: str, force_refresh: bool = False) -> dict:
    """Load the cached cross-season head-to-head index for this league,
    building (and caching) it on first use. Delete the cache file (or pass
    force_refresh=True) to rebuild — e.g. if Sleeper's chain metadata changed."""
    path = _cache_path(league_id)
    if not force_refresh and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    chain, results, owner_names = _build_chain_results(league_id)
    index = {"root_league_id": league_id, "chain": chain, "results": results, "owner_names": owner_names}
    os.makedirs(_CACHE_DIR, exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(index, f)
    except OSError:
        pass
    return index


def current_season_results(season, before_week: int) -> list:
    """Same shape as the cached historical results, built live from the
    current SeasonData for weeks before `before_week` — so this week's
    not-yet-reported game isn't counted in its own rivalry line."""
    results = []
    for wk in sorted(season.weeks.keys()):
        if wk >= before_week:
            break
        wd = season.weeks[wk]
        for ra, pa, rb, pb in S.matchup_pairs(wd):
            ta, tb = season.teams.get(ra), season.teams.get(rb)
            oa, ob = (ta and ta.owner_id), (tb and tb.owner_id)
            if not oa or not ob:
                continue
            winner = oa if pa > pb else (ob if pb > pa else None)
            results.append({"season": season.season, "week": wk, "owner_a": oa, "owner_b": ob,
                            "winner": winner, "points_a": pa, "points_b": pb})
    return results


def head_to_head(index: dict, owner_a: str, owner_b: str, extra_results: list | None = None):
    """Aggregate record between two owners across the cached index plus any
    `extra_results` (typically this season's games so far). None if they've
    never played — callers should render a "first-ever meeting" line."""
    if not owner_a or not owner_b:
        return None
    pair = {owner_a, owner_b}
    all_results = (index.get("results") or []) + (extra_results or [])
    games = [r for r in all_results if {r["owner_a"], r["owner_b"]} == pair]
    if not games:
        return None
    games.sort(key=lambda r: (r["season"], r["week"]))

    wins = {owner_a: 0, owner_b: 0}
    for g in games:
        if g["winner"] in wins:
            wins[g["winner"]] += 1

    streak_owner, streak_len = None, 0
    for g in reversed(games):
        if g["winner"] is None:
            break
        if streak_owner is None:
            streak_owner, streak_len = g["winner"], 1
        elif g["winner"] == streak_owner:
            streak_len += 1
        else:
            break

    return {
        "games": len(games),
        "wins": wins,
        "ties": sum(1 for g in games if g["winner"] is None),
        "streak_owner": streak_owner,
        "streak_len": streak_len,
    }


def rivalry_matchups_for_week(season, league_id: str, week: int) -> list:
    """One dict per matchup in `week`, with this week's score plus the
    all-time head-to-head — render.py uses both so the rivalry line sits
    alongside the actual result instead of floating as a bare sentence.
    Shared by cli.py and batch.py so the build_index/current_season_results/
    head_to_head wiring only lives in one place.

    Returns [] for a week with no head-to-head matchups (e.g. a playoff
    week outside the regular bracket, where Sleeper records scores with no
    matchup_id pairing) — callers should treat that as "nothing to show
    here," not an error.
    """
    idx = build_index(league_id)
    extra = current_season_results(season, before_week=week)
    wd = season.weeks.get(week, {})
    out = []
    for ra, pa, rb, pb in S.matchup_pairs(wd):
        ta, tb = season.teams.get(ra), season.teams.get(rb)
        if not ta or not tb:
            continue
        record = head_to_head(idx, ta.owner_id, tb.owner_id, extra_results=extra)
        line = format_rivalry_line(record, ta.team_name, tb.team_name, ta.owner_id, tb.owner_id)
        out.append({
            "ra": ra, "rb": rb, "pa": pa, "pb": pb,
            "name_a": ta.team_name, "name_b": tb.team_name,
            "record": record, "line": line,
        })
    return out


def format_rivalry_line(record, name_a: str, name_b: str, owner_a: str, owner_b: str) -> str:
    """A streak is what generates trash talk ("won 2 straight") — the
    all-time tally is just supporting context. So a 2+ game streak by
    EITHER side leads the sentence, with the tally demoted to a clause
    after it, even when the team on the hot streak is still trailing
    all-time. Without a meaningful streak, the tally alone is the line."""
    if not record or record["games"] == 0:
        return f"First-ever meeting between {name_a} and {name_b}."

    wa, wb = record["wins"].get(owner_a, 0), record["wins"].get(owner_b, 0)
    ties = record["ties"]
    tally_tie = wa == wb

    if wa > wb:
        leader_name, leader_wins, trail_name, trail_wins, leader_owner = name_a, wa, name_b, wb, owner_a
    else:
        leader_name, leader_wins, trail_name, trail_wins, leader_owner = name_b, wb, name_a, wa, owner_b

    tally = f"{wa}-{wb}" if tally_tie else f"{leader_wins}-{trail_wins}"
    if ties:
        tally += f"-{ties}"

    streak_owner, streak_len = record["streak_owner"], record["streak_len"]
    if streak_owner and streak_len >= 2:
        streaker_name = name_a if streak_owner == owner_a else name_b
        plural = streaker_name.endswith("s")
        have_won = "have won" if plural else "has won"
        leads = "lead" if plural else "leads"
        # "though" only makes sense when the streaker is the team still
        # trailing overall (winning streak *despite* the deficit) — when
        # the streaker IS the leader, or the series is tied, the streak and
        # the tally agree, so it reads as one fact ("and"), not a
        # contradiction, and doesn't need to repeat the streaker's name.
        if tally_tie:
            return f"{streaker_name} {have_won} {streak_len} straight, tying this series at {tally} all-time."
        if streak_owner == leader_owner:
            return f"{streaker_name} {have_won} {streak_len} straight and {leads} {trail_name} {tally} all-time."
        leader_leads = "lead" if leader_name.endswith("s") else "leads"
        return (f"{streaker_name} {have_won} {streak_len} straight in this series, though "
               f"{leader_name} still {leader_leads} {trail_name} {tally} all-time.")

    if tally_tie:
        tie_str = f", {ties} ties" if ties else ""
        return f"{name_a} and {name_b} are tied {wa}-{wb} all-time{tie_str}."

    leads = "lead" if leader_name.endswith("s") else "leads"
    return f"{leader_name} {leads} {trail_name} {tally} all-time."
