"""
monthly.py — Aggregate a set of weeks into monthly per-team stats.

A month is just a list of week numbers (from calendar_map). This computes,
for each team across those weeks: total/avg points, points above league
average (the MotM metric), H2H record in the month, all-play record,
average efficiency, total bench points, and rank movement across the month.
"""

from __future__ import annotations
from dataclasses import dataclass
import statistics

from . import stats as S


@dataclass
class MonthTeam:
    roster_id: int
    weeks: list
    total_points: float
    avg_points: float
    pts_above_avg: float        # total points minus league-average total (MotM metric)
    h2h_w: int
    h2h_l: int
    h2h_t: int
    all_play_w: int
    all_play_l: int
    avg_efficiency: float
    bench_points: float
    best_week: float
    worst_week: float
    rank_start: int | None      # standings rank entering the month
    rank_end: int | None        # standings rank leaving the month
    climb: int                  # positive = moved up


def _cumulative_rank_after(season, upto_week: int) -> dict:
    """
    Standings rank for each team using only weeks 1..upto_week.
    Rank by (H2H wins, then points-for). Returns roster_id -> rank (1=best).
    """
    wins = {rid: 0 for rid in season.teams}
    pf = {rid: 0.0 for rid in season.teams}
    for wk in sorted(season.weeks.keys()):
        if wk > upto_week:
            break
        wd = season.weeks[wk]
        for rid, wt in wd.items():
            pf[rid] += wt.points
        for ra, pa, rb, pb in S.matchup_pairs(wd):
            if pa > pb:
                wins[ra] += 1
            elif pb > pa:
                wins[rb] += 1
    order = sorted(season.teams.keys(),
                   key=lambda r: (wins[r], pf[r]), reverse=True)
    return {rid: i + 1 for i, rid in enumerate(order)}


def month_stats(season, month_weeks: list[int]) -> dict:
    """roster_id -> MonthTeam for the given set of weeks."""
    month_weeks = [w for w in month_weeks if w in season.weeks]
    if not month_weeks:
        return {}

    first_wk, last_wk = min(month_weeks), max(month_weeks)
    rank_before = _cumulative_rank_after(season, first_wk - 1) if first_wk > 1 else None
    rank_after = _cumulative_rank_after(season, last_wk)

    acc = {rid: {"scores": [], "h2h_w": 0, "h2h_l": 0, "h2h_t": 0,
                 "ap_w": 0, "ap_l": 0, "eff": [], "bench": 0.0}
           for rid in season.teams}

    for wk in month_weeks:
        wd = season.weeks[wk]
        ap = S.all_play(wd)
        eff = S.lineup_efficiency(season, wd)
        for ra, pa, rb, pb in S.matchup_pairs(wd):
            if pa > pb:
                acc[ra]["h2h_w"] += 1; acc[rb]["h2h_l"] += 1
            elif pb > pa:
                acc[rb]["h2h_w"] += 1; acc[ra]["h2h_l"] += 1
            else:
                acc[ra]["h2h_t"] += 1; acc[rb]["h2h_t"] += 1
        for rid, wt in wd.items():
            acc[rid]["scores"].append(wt.points)
            w, l, _ = ap.get(rid, (0, 0, 0))
            acc[rid]["ap_w"] += w; acc[rid]["ap_l"] += l
            e = eff.get(rid)
            if e:
                acc[rid]["eff"].append(e.efficiency)
                acc[rid]["bench"] += e.bench_points

    # league-average total points this month (for points-above-average)
    totals = [sum(a["scores"]) for a in acc.values() if a["scores"]]
    league_avg_total = statistics.mean(totals) if totals else 0.0

    out = {}
    for rid, a in acc.items():
        scores = a["scores"]
        if not scores:
            continue
        total = round(sum(scores), 2)
        out[rid] = MonthTeam(
            roster_id=rid,
            weeks=month_weeks,
            total_points=total,
            avg_points=round(statistics.mean(scores), 2),
            pts_above_avg=round(total - league_avg_total, 2),
            h2h_w=a["h2h_w"], h2h_l=a["h2h_l"], h2h_t=a["h2h_t"],
            all_play_w=a["ap_w"], all_play_l=a["ap_l"],
            avg_efficiency=round(statistics.mean(a["eff"]), 1) if a["eff"] else 100.0,
            bench_points=round(a["bench"], 2),
            best_week=max(scores),
            worst_week=min(scores),
            rank_start=(rank_before or {}).get(rid),
            rank_end=rank_after.get(rid),
            climb=((rank_before or {}).get(rid, rank_after.get(rid)) - rank_after.get(rid))
                  if rank_before else 0,
        )
    return out


# ──────────────────────────────────────────────────────────────────────────
# Month-scoped narrative: who won the month, who moved, what changed
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class MonthVerdict:
    """The month's headline result — the one sentence a reader wants first."""
    roster_id: int
    record: str
    total_points: float
    pts_above_avg: float
    avg_efficiency: float
    climb: int
    runner_up_rid: int | None
    margin: float          # points-above-average clear of the runner-up


def manager_of_the_month(ms: dict) -> "MonthVerdict | None":
    """Best manager over the month: most points above the league average.

    This deliberately matches awards.m_manager_of_month exactly. That award
    is the canonical Manager of the Month and appears on its own card in the
    same report — ranking by any other rule here (wins first, say) produces a
    different winner and the report contradicts itself on its own headline.
    Any change to the definition belongs in both places or neither.

    Points above average rather than raw points because month lengths differ,
    and rather than record because a 3-2 built on the month's two biggest
    scores is a better month than a 4-1 of narrow escapes.
    """
    if not ms:
        return None
    ranked = sorted(ms.values(), key=lambda m: m.pts_above_avg, reverse=True)
    top = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    return MonthVerdict(
        roster_id=top.roster_id,
        record=f"{top.h2h_w}-{top.h2h_l}" + (f"-{top.h2h_t}" if top.h2h_t else ""),
        total_points=top.total_points,
        pts_above_avg=top.pts_above_avg,
        avg_efficiency=top.avg_efficiency,
        climb=top.climb,
        runner_up_rid=runner.roster_id if runner else None,
        margin=round(top.pts_above_avg - runner.pts_above_avg, 2) if runner else 0.0,
    )


def movers(ms: dict, min_climb: int = 1) -> dict:
    """{"risers": [MonthTeam...], "fallers": [...]} by standings places moved
    across the month, biggest move first.

    Uses `climb` (rank entering the month minus rank leaving it), so this is
    explicitly about the month rather than the season — a team can be
    mid-table all year and still have had the month of their life.
    """
    if not ms:
        return {"risers": [], "fallers": []}
    moved = [m for m in ms.values() if m.climb is not None]
    risers = sorted([m for m in moved if m.climb >= min_climb],
                    key=lambda m: m.climb, reverse=True)
    fallers = sorted([m for m in moved if m.climb <= -min_climb],
                     key=lambda m: m.climb)
    return {"risers": risers, "fallers": fallers}


def month_over_month(ms: dict, prev_ms: dict | None) -> dict:
    """roster_id -> {points_delta, efficiency_delta, wins_delta} against the
    previous month, for the teams present in both.

    This is the "what changed since you last heard from us" layer. Without
    it every monthly issue reads as a standalone snapshot, and a reader has
    no way to tell an improving team from a coasting one.
    """
    if not ms or not prev_ms:
        return {}
    out = {}
    for rid, m in ms.items():
        p = prev_ms.get(rid)
        if not p:
            continue
        out[rid] = {
            "points_delta": round(m.total_points - p.total_points, 2),
            "avg_points_delta": round(m.avg_points - p.avg_points, 2),
            "efficiency_delta": round(m.avg_efficiency - p.avg_efficiency, 1),
            "wins_delta": m.h2h_w - p.h2h_w,
        }
    return out


def month_shape(ms: dict) -> dict:
    """League-wide facts about the month itself: scoring level, spread, and
    how settled the table was. Used for the section lead-in, so a reader
    knows what kind of month they're reading about before any team names
    appear."""
    if not ms:
        return {}
    totals = [m.total_points for m in ms.values()]
    effs = [m.avg_efficiency for m in ms.values()]
    climbs = [abs(m.climb) for m in ms.values()]
    weeks = max((len(m.weeks) for m in ms.values()), default=0)
    return {
        "weeks": weeks,
        "avg_total": round(statistics.mean(totals), 1),
        "high_total": round(max(totals), 1),
        "low_total": round(min(totals), 1),
        "spread": round(max(totals) - min(totals), 1),
        "avg_efficiency": round(statistics.mean(effs), 1) if effs else 0.0,
        "total_places_moved": sum(climbs),
        "settled": sum(climbs) <= len(ms),   # roughly: fewer than one place moved per team
    }
