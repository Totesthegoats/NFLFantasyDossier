"""
insights.py — extra monthly-report sections, as pure data builders.

  award_leaderboard     season-to-date Hall of Fame / Hall of Shame tally
  playoff_movers        playoff odds at the start vs the end of the period
  trade_regrades        last month's trades re-judged with this month's results
  next_month_preview    next month's fixtures with Elo win probabilities
  points_by_position    where each team's starting points came from

Every builder is capped at `upto_week` so a retrospective report never sees
results its readers hadn't, and returns an empty value when there is
nothing honest to show, so the renderer can simply skip the section.
"""

from __future__ import annotations

import random

from . import analytics as AN
from . import awards as A
from . import calendar_map as CM
from . import monthly as M
from . import stats as S
from . import waivers as W

# Positions shown as their own column. Individual defensive players are
# grouped as IDP; anything left over is "Other". Columns with no points in a
# league are dropped by the renderer, so non-IDP leagues never see IDP.
POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF", "IDP"]
_IDP = {"DL", "DE", "DT", "LB", "ILB", "OLB", "DB", "CB", "S", "SS", "FS"}


def _season_year(season) -> int | None:
    return int(season.season) if str(season.season).isdigit() else None


def _month_buckets(season, upto_week: int) -> dict:
    """{(year, month): [weeks]} for played weeks up to upto_week."""
    year = _season_year(season)
    if year is None:
        return {}
    played = [w for w in sorted(season.weeks) if w <= upto_week]
    return CM.group_weeks_by_month(year, played)


# ──────────────────────────────────────────────────────────────────────────
# Fame / Shame leaderboard
# ──────────────────────────────────────────────────────────────────────────

def award_leaderboard(season, upto_week: int, current_weeks: list | None = None,
                      current_awards=None) -> dict:
    """{"teams": {rid: {fame, shame, net, titles}}, "months": n}.

    Re-runs the monthly award engine for every month up to upto_week. The
    current report's own awards are passed in and used for its month, so the
    tally always agrees with the Hall of Fame/Shame pages printed beside it.
    """
    buckets = _month_buckets(season, upto_week)
    if not buckets:
        return {}
    current_key = None
    if current_weeks:
        cw = set(current_weeks)
        current_key = next((k for k, v in buckets.items() if set(v) & cw), None)

    tally = {rid: {"fame": 0, "shame": 0, "titles": {}} for rid in season.teams}
    prev_ms = None
    for key in sorted(buckets):
        weeks = buckets[key]
        ms = M.month_stats(season, weeks)
        if key == current_key and current_awards is not None:
            month_awards = current_awards
        else:
            _ctx, month_awards = A.compute_monthly(season, ms, prev_month_stats=prev_ms)
        for a in month_awards:
            rid = getattr(a, "winner_rid", None)
            if rid not in tally:
                continue
            tally[rid][a.hall] = tally[rid].get(a.hall, 0) + 1
            if a.hall == "shame":
                tally[rid]["titles"][a.title] = tally[rid]["titles"].get(a.title, 0) + 1
        prev_ms = ms

    for t in tally.values():
        t["net"] = t["fame"] - t["shame"]
        titles = t.pop("titles")
        t["signature"] = max(titles, key=titles.get) if titles else None
        t["signature_count"] = titles.get(t["signature"], 0) if titles else 0
    return {"teams": tally, "months": len(buckets)}


# ──────────────────────────────────────────────────────────────────────────
# Playoff odds movers
# ──────────────────────────────────────────────────────────────────────────

def playoff_movers(season, weeks: list, upto_week: int, end_odds: dict | None = None,
                   n_sims: int = 2000) -> dict:
    """rid -> {start, end, change} (probabilities 0-1).

    Only reported once the END of the period has enough weeks behind it for
    odds to mean something (sim.MIN_WEEKS_FOR_ODDS), and never after the
    regular season. The start-of-period baseline before any games is simply
    an equal share of the playoff spots.
    """
    from . import data as D
    from . import sim as SIM

    if not weeks or not season.playoff_teams:
        return {}
    last_regular = season.playoff_week_start - 1
    if upto_week >= last_regular:
        return {}
    if len([w for w in season.weeks if w <= upto_week]) < SIM.MIN_WEEKS_FOR_ODDS:
        return {}

    start_upto = min(weeks) - 1
    n = len(season.teams)
    schedule = D.remaining_schedule(season, max(start_upto, 0), last_regular)
    if start_upto <= 0:
        start = {rid: season.playoff_teams / n for rid in season.teams}
    else:
        start = SIM.simulate_playoff_odds(season, start_upto, schedule, season.playoff_teams,
                                          n_sims=n_sims, rng=random.Random(start_upto))
    if not end_odds:
        end_sched = {wk: v for wk, v in schedule.items() if wk > upto_week}
        end_odds = SIM.simulate_playoff_odds(season, upto_week, end_sched, season.playoff_teams,
                                             n_sims=n_sims, rng=random.Random(upto_week))
    return {rid: {"start": start.get(rid, 0.0), "end": end_odds.get(rid, 0.0),
                  "change": end_odds.get(rid, 0.0) - start.get(rid, 0.0)}
            for rid in season.teams}


# ──────────────────────────────────────────────────────────────────────────
# Trade re-grades
# ──────────────────────────────────────────────────────────────────────────

def _winner(sides: list):
    if len(sides) < 2:
        return None
    ranked = sorted(sides, key=lambda s: s["pts_since"], reverse=True)
    if ranked[0]["pts_since"] == ranked[1]["pts_since"]:
        return None
    return ranked[0]["roster_id"]


def trade_regrades(season, weeks: list, upto_week: int) -> list:
    """Last month's trades, graded at the end of last month and again now.

    Each item: {week, sides: [{roster_id, received_players, received_picks,
    pts_then, pts_now}], winner_then, winner_now, flipped, has_picks}.
    """
    buckets = _month_buckets(season, upto_week)
    keys = sorted(buckets)
    cur = next((k for k in keys if set(buckets[k]) & set(weeks or [])), None)
    if cur is None or keys.index(cur) == 0:
        return []
    prev_weeks = buckets[keys[keys.index(cur) - 1]]
    then = W.trade_ledger(season, prev_weeks, max_week=max(prev_weeks))
    now = W.trade_ledger(season, prev_weeks, max_week=upto_week)
    out = []
    for t_then, t_now in zip(then, now):
        if len(t_now["sides"]) < 2:
            continue
        pts_then = {s["roster_id"]: s["pts_since"] for s in t_then["sides"]}
        sides = [{**s, "pts_then": pts_then.get(s["roster_id"], 0.0), "pts_now": s["pts_since"]}
                 for s in t_now["sides"]]
        w_then, w_now = _winner(t_then["sides"]), _winner(t_now["sides"])
        out.append({"week": t_now["week"], "sides": sides, "has_picks": t_now["has_picks"],
                    "winner_then": w_then, "winner_now": w_now,
                    "flipped": bool(w_then and w_now and w_then != w_now)})
    return out


# ──────────────────────────────────────────────────────────────────────────
# Next month preview
# ──────────────────────────────────────────────────────────────────────────

def next_month_preview(season, weeks: list, upto_week: int) -> dict:
    """{"label": "October 2026", "weeks": [{week, games: [{a, b, p_a}]}]}.

    Next month = the calendar month after the report's, regular season only.
    Win probability is the Elo expectation from ratings as of upto_week.
    """
    from . import data as D

    year = _season_year(season)
    if year is None or not weeks:
        return {}
    last_regular = season.playoff_week_start - 1
    cur = CM.week_to_month(year, max(weeks))
    nxt = (cur[0] + (cur[1] // 12), cur[1] % 12 + 1)
    next_weeks = [w for w in range(upto_week + 1, last_regular + 1)
                  if CM.week_to_month(year, w) == nxt]
    if not next_weeks:
        return {}

    traj = AN.elo_trajectory(season, upto_week=upto_week)
    elo = {rid: t["end"] for rid, t in traj.items()}
    schedule = D.remaining_schedule(season, upto_week, max(next_weeks))

    out_weeks = []
    for wk in next_weeks:
        by_match: dict = {}
        for rid, mid in (schedule.get(wk) or {}).items():
            if mid is not None:
                by_match.setdefault(mid, []).append(rid)
        games = []
        for pair in by_match.values():
            if len(pair) != 2:
                continue
            a, b = sorted(pair, key=lambda r: elo.get(r, AN.ELO_START), reverse=True)
            p_a = AN._elo_expected(elo.get(a, AN.ELO_START), elo.get(b, AN.ELO_START))
            games.append({"a": a, "b": b, "p_a": p_a})
        games.sort(key=lambda g: abs(g["p_a"] - 0.5))   # closest games first
        if games:
            out_weeks.append({"week": wk, "games": games})
    if not out_weeks:
        return {}
    return {"label": CM.month_label(*nxt), "weeks": out_weeks}


# ──────────────────────────────────────────────────────────────────────────
# Points by position
# ──────────────────────────────────────────────────────────────────────────

def points_by_position(season, weeks: list) -> dict:
    """rid -> {"by_pos": {pos: pts}, "total", "top_player", "top_pts", "top_share"}.

    Starters only: bench points did not count, so they are not "where the
    points came from".
    """
    out = {}
    player_pts: dict = {}
    for wk in weeks:
        wd = season.weeks.get(wk)
        if not wd:
            continue
        for rid, wt in wd.items():
            rec = out.setdefault(rid, {"by_pos": {p: 0.0 for p in POSITIONS + ["Other"]},
                                       "total": 0.0})
            for pid, pts in zip(wt.starters, wt.starter_points or []):
                if not pid or pid == "0":
                    continue
                pos = (season.players.get(str(pid)) or {}).get("position") or "Other"
                pos = "IDP" if pos in _IDP else (pos if pos in POSITIONS else "Other")
                rec["by_pos"][pos] += pts
                rec["total"] += pts
                player_pts.setdefault(rid, {})
                player_pts[rid][pid] = player_pts[rid].get(pid, 0.0) + pts
    for rid, rec in out.items():
        pp = player_pts.get(rid) or {}
        if pp:
            top = max(pp, key=pp.get)
            rec["top_player"] = S.D.player_name(season.players, top)
            rec["top_pts"] = pp[top]
            rec["top_share"] = pp[top] / rec["total"] if rec["total"] > 0 else 0.0
        else:
            rec["top_player"], rec["top_pts"], rec["top_share"] = None, 0.0, 0.0
    return out
