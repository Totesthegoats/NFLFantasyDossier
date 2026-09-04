"""
waivers.py — FAAB/waiver analysis (best pickup, fumbler, most active).

Built on SeasonData.transactions (populated by data.fetch_season). For each
pickup we track points scored by that player in weeks AFTER the pickup,
across whichever roster currently holds them — that's the "did this move
pay off" number the awards layer wants.
"""

from __future__ import annotations
from dataclasses import dataclass

from . import data as D


@dataclass
class WaiverPickup:
    roster_id: int
    player_id: str
    player_name: str
    faab: int
    points_since: float
    cost_per_point: float


def _points_since(season, roster_id: int, player_id: str, since_week: int) -> float:
    total = 0.0
    for wk, wd in season.weeks.items():
        if wk <= since_week:
            continue
        wt = wd.get(roster_id)
        if not wt:
            continue
        if player_id in wt.starters:
            idx = wt.starters.index(player_id)
            total += wt.starter_points[idx] or 0.0
        elif player_id in wt.bench:
            idx = wt.bench.index(player_id)
            total += wt.bench_points[idx] or 0.0
    return round(total, 2)


def _week_pickups(season, week: int) -> list:
    out = []
    for t in season.transactions:
        if t.week != week:
            continue
        pts = _points_since(season, t.roster_id, t.player_id, week)
        cost_per_point = round(t.faab / pts, 2) if pts > 0 and t.faab else float(t.faab or 0)
        out.append(WaiverPickup(
            roster_id=t.roster_id,
            player_id=t.player_id,
            player_name=D.player_name(season.players, t.player_id),
            faab=t.faab,
            points_since=pts,
            cost_per_point=cost_per_point,
        ))
    return out


def best_pickup(season, week: int):
    """Best waiver/FA add of the week: most points scored since, FAAB-adjusted."""
    cands = [p for p in _week_pickups(season, week) if p.points_since > 0]
    if not cands:
        return None
    return max(cands, key=lambda p: p.points_since - (p.faab or 0) * 0.1)


def faab_fumbler(season, week: int):
    """Worst FAAB spend of the week — high cost-per-point, or spend for nothing."""
    cands = [p for p in _week_pickups(season, week) if p.faab and p.faab > 0]
    if not cands:
        return None
    return max(cands, key=lambda p: p.cost_per_point if p.points_since > 0 else float("inf"))


# ----------------------------------------------------------------------
# Period aggregation (month/season report summaries, not per-week awards)
# ----------------------------------------------------------------------

def period_pickups(season, weeks: list) -> list:
    weeks_set = set(weeks)
    return [p for wk in sorted(weeks_set) for p in _week_pickups(season, wk)]


def best_pickup_period(season, weeks: list):
    """Best waiver/FA add across the period: most points scored since, FAAB-adjusted."""
    cands = [p for p in period_pickups(season, weeks) if p.points_since > 0]
    if not cands:
        return None
    return max(cands, key=lambda p: p.points_since - (p.faab or 0) * 0.1)


def worst_faab_period(season, weeks: list):
    """Worst FAAB spend across the period — high cost-per-point, or spend for nothing."""
    cands = [p for p in period_pickups(season, weeks) if p.faab and p.faab > 0]
    if not cands:
        return None
    return max(cands, key=lambda p: p.cost_per_point if p.points_since > 0 else float("inf"))


def faab_spent_by_team(season, weeks: list) -> dict:
    """roster_id -> total FAAB spent across the period."""
    weeks_set = set(weeks)
    totals = {}
    for t in season.transactions:
        if t.week in weeks_set and t.faab:
            totals[t.roster_id] = totals.get(t.roster_id, 0) + t.faab
    return totals


def trades_in(season, weeks: list) -> list:
    weeks_set = set(weeks)
    return [t for t in season.trades if t.week in weeks_set]


def trade_count_by_team(season, weeks: list) -> dict:
    """roster_id -> number of trades they were part of."""
    weeks_set = set(weeks)
    counts = {}
    for t in season.trades:
        if t.week not in weeks_set:
            continue
        for rid in set(t.roster_ids):
            counts[rid] = counts.get(rid, 0) + 1
    return counts


def trade_value_by_team(season, weeks: list) -> dict:
    """roster_id -> net points gained/lost from trades: points scored by
    acquired players since the trade, minus the same points charged against
    whoever gave them up (zero-sum per trade, like the deal itself)."""
    weeks_set = set(weeks)
    net = {}
    for t in season.trades:
        if t.week not in weeks_set:
            continue
        for pid, to_rid in t.adds.items():
            pts = _points_since(season, to_rid, pid, t.week)
            net[to_rid] = net.get(to_rid, 0.0) + pts
            from_rid = t.drops.get(pid)
            if from_rid is not None:
                net[from_rid] = net.get(from_rid, 0.0) - pts
    return {rid: round(v, 2) for rid, v in net.items()}


def waiver_points_by_team(season, weeks: list) -> dict:
    """roster_id -> total points scored since pickup, summed across every
    waiver/free-agent add in the period (volume, vs best_pickup_period's
    single best move)."""
    totals = {}
    for p in period_pickups(season, weeks):
        totals[p.roster_id] = totals.get(p.roster_id, 0.0) + p.points_since
    return {rid: round(v, 2) for rid, v in totals.items()}
