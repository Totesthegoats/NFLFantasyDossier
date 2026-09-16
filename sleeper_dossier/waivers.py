"""
waivers.py — FAAB/waiver analysis (best pickup, fumbler, most active).

Built on SeasonData.transactions (populated by data.fetch_season). For each
pickup we track points scored by that player in weeks AFTER the pickup,
across whichever roster currently holds them — that's the "did this move
pay off" number the awards layer wants.
"""

from __future__ import annotations
from dataclasses import dataclass
import statistics

from . import data as D


@dataclass
class WaiverPickup:
    roster_id: int
    player_id: str
    player_name: str
    faab: int
    points_since: float
    cost_per_point: float


def _weekly_points_since(season, roster_id: int, player_id: str, since_week: int,
                         max_week: int | None = None) -> list:
    """Points scored by player_id on roster_id in each week (since_week, max_week]
    where they were rostered (starter or bench), one entry per week, in week
    order. _points_since is just sum(this) — kept separate so std-dev-based
    metrics (Sharpe-style ratios) can see the week-by-week shape rather than
    only the total."""
    out = []
    for wk in sorted(season.weeks.keys()):
        if wk <= since_week:
            continue
        if max_week is not None and wk > max_week:
            continue
        wt = season.weeks[wk].get(roster_id)
        if not wt:
            continue
        if player_id in wt.starters:
            idx = wt.starters.index(player_id)
            out.append(wt.starter_points[idx] or 0.0)
        elif player_id in wt.bench:
            idx = wt.bench.index(player_id)
            out.append(wt.bench_points[idx] or 0.0)
    return out


def _points_since(season, roster_id: int, player_id: str, since_week: int,
                  max_week: int | None = None) -> float:
    """Points scored by player_id on roster_id in weeks (since_week, max_week].
    max_week=None means no upper cap (use all available data)."""
    return round(sum(_weekly_points_since(season, roster_id, player_id, since_week, max_week)), 2)


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


def trade_value_by_team(season, weeks: list, max_week: int | None = None) -> dict:
    """roster_id -> net points gained/lost from trades, capped at max_week."""
    weeks_set = set(weeks)
    net = {}
    for t in season.trades:
        if t.week not in weeks_set:
            continue
        for pid, to_rid in t.adds.items():
            pts = _points_since(season, to_rid, pid, t.week, max_week=max_week)
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


def waiver_roi(season, weeks: list) -> dict:
    """roster_id -> {faab, points, roi, free_adds, free_points, adds}.

    roi is points per FAAB dollar, and only counts pickups that actually
    cost something — free-agent adds would divide by zero and, more to the
    point, "infinite ROI" on a free add isn't a spending decision worth
    grading. Their production is reported alongside as free_points so a
    manager who built a bench off the scrap heap still gets credit.
    """
    spent = faab_spent_by_team(season, weeks)
    out = {}
    for p in period_pickups(season, weeks):
        r = out.setdefault(p.roster_id, {"faab": 0, "points": 0.0, "free_adds": 0,
                                         "free_points": 0.0, "adds": 0})
        r["adds"] += 1
        if p.faab and p.faab > 0:
            r["faab"] += p.faab
            r["points"] += p.points_since
        else:
            r["free_adds"] += 1
            r["free_points"] += p.points_since

    for rid, r in out.items():
        # faab_spent_by_team counts every claim's bid; keep it authoritative
        # for spend so the ROI denominator matches the FAAB table elsewhere.
        r["faab"] = spent.get(rid, r["faab"])
        r["points"] = round(r["points"], 2)
        r["free_points"] = round(r["free_points"], 2)
        r["roi"] = round(r["points"] / r["faab"], 2) if r["faab"] else None
    return out


@dataclass
class TradeOutcome:
    trade: D.Trade
    winner_rid: int
    loser_rid: int
    winner_value: float
    loser_value: float
    margin: float


def _trade_net_values(season, trade, max_week: int | None = None) -> dict:
    """roster_id -> net points from THIS single trade, capped at max_week."""
    net = {}
    for pid, to_rid in trade.adds.items():
        pts = _points_since(season, to_rid, pid, trade.week, max_week=max_week)
        net[to_rid] = net.get(to_rid, 0.0) + pts
        from_rid = trade.drops.get(pid)
        if from_rid is not None:
            net[from_rid] = net.get(from_rid, 0.0) - pts
    return net


def _trade_outcome(season, trade) -> "TradeOutcome | None":
    net = _trade_net_values(season, trade)
    if len(net) < 2:
        return None
    ranked = sorted(net.items(), key=lambda kv: kv[1], reverse=True)
    (winner_rid, winner_value), (loser_rid, loser_value) = ranked[0], ranked[-1]
    return TradeOutcome(
        trade=trade, winner_rid=winner_rid, loser_rid=loser_rid,
        winner_value=round(winner_value, 2), loser_value=round(loser_value, 2),
        margin=round(winner_value - loser_value, 2),
    )


# ──────────────────────────────────────────────────────────────────────────
# Transactions HQ data builders
# ───────────────────────────────────────────────────────────

def _is_hit(season, roster_id: int, player_id: str, since_week: int,
            max_week: int | None = None) -> bool:
    """True if the player was started at least once with positive points
    in weeks (since_week, max_week]. max_week=None means no upper cap."""
    for wk, wd in season.weeks.items():
        if wk <= since_week:
            continue
        if max_week is not None and wk > max_week:
            continue
        wt = wd.get(roster_id)
        if not wt:
            continue
        if player_id in wt.starters:
            idx = wt.starters.index(player_id)
            if (wt.starter_points[idx] or 0) > 0:
                return True
    return False


def transaction_ledger(season, weeks: list, max_week: int | None = None) -> list:
    """One record per waiver/free-agent add in the period, enriched with
    points scored since acquisition and whether the pickup was a 'hit'.
    Includes $0 free-agent claims and stream-and-cuts (both show pts=0).
    max_week caps the points window — pass the report week to avoid
    counting future data.  Order: week ascending, then roster_id."""
    weeks_set = set(weeks)
    rows = []
    for t in season.transactions:
        if t.week not in weeks_set:
            continue
        pts = _points_since(season, t.roster_id, t.player_id, t.week, max_week=max_week)
        hit = _is_hit(season, t.roster_id, t.player_id, t.week, max_week=max_week)
        drop_names = [D.player_name(season.players, dpid) for dpid in t.drops]
        cpp = round(t.faab / pts, 2) if pts > 0 and t.faab else float(t.faab or 0)
        rows.append({
            "week":          t.week,
            "roster_id":     t.roster_id,
            "manager":       season.team_name(t.roster_id),
            "type":          t.type,
            "faab":          t.faab,
            "player_id":     t.player_id,
            "player_name":   D.player_name(season.players, t.player_id),
            "drops":         drop_names,
            "points_since":  pts,
            "hit":           hit,
            "cost_per_point": cpp,
        })
    rows.sort(key=lambda r: (r["week"], r["roster_id"]))
    return rows


def faab_remaining(season) -> dict:
    """roster_id -> FAAB remaining (cap - used, floored at 0).
    Uses roster-level waiver_budget_used (live from Sleeper) against the
    league's configured budget cap."""
    cap = season.faab_budget
    return {rid: max(0, cap - team.waiver_budget_used)
            for rid, team in season.teams.items()}


def waiver_hit_rate(season, weeks: list) -> dict:
    """roster_id -> {"made": int, "hits": int, "rate": float}"""
    weeks_set = set(weeks)
    stats: dict = {}
    for t in season.transactions:
        if t.week not in weeks_set:
            continue
        rid = t.roster_id
        if rid not in stats:
            stats[rid] = {"made": 0, "hits": 0}
        stats[rid]["made"] += 1
        if _is_hit(season, rid, t.player_id, t.week):
            stats[rid]["hits"] += 1
    for rid, s in stats.items():
        s["rate"] = round(s["hits"] / s["made"], 2) if s["made"] else 0.0
    return stats


def bandwagon_counts(season, weeks: list, top_n: int = 8) -> dict:
    """{"added": [(player_name, count)], "dropped": [(player_name, count)]}
    Top-N most-added and most-dropped players in the period."""
    weeks_set = set(weeks)
    add_counts: dict = {}
    drop_counts: dict = {}
    for t in season.transactions:
        if t.week not in weeks_set:
            continue
        add_counts[t.player_id] = add_counts.get(t.player_id, 0) + 1
        for dpid in t.drops:
            drop_counts[dpid] = drop_counts.get(dpid, 0) + 1
    def _top(counts_dict):
        ranked = sorted(counts_dict.items(), key=lambda kv: kv[1], reverse=True)
        return [(D.player_name(season.players, pid), cnt) for pid, cnt in ranked[:top_n]]
    return {"added": _top(add_counts), "dropped": _top(drop_counts)}


def activity_counts(season, weeks: list) -> dict:
    """roster_id -> total transaction actions (waiver adds + trade involvements)."""
    weeks_set = set(weeks)
    counts: dict = {}
    for t in season.transactions:
        if t.week in weeks_set:
            counts[t.roster_id] = counts.get(t.roster_id, 0) + 1
    for t in season.trades:
        if t.week in weeks_set:
            for rid in set(t.roster_ids):
                counts[rid] = counts.get(rid, 0) + 1
    return counts


def _pick_label(pick: dict) -> str:
    season = pick.get("season") or "?"
    rnd = pick.get("round") or "?"
    return f"{season} Rd{rnd}"


def trade_ledger(season, weeks: list, max_week: int | None = None) -> list:
    """One record per trade in the period. Each record has:
      week, sides: [{"roster_id", "manager", "received_players": [...names],
                     "received_picks": [...labels], "pts_since": float}]
    Players and picks are listed from each side's receiving perspective."""
    weeks_set = set(weeks)
    out = []
    for trade in season.trades:
        if trade.week not in weeks_set:
            continue
        # Build per-side dicts
        sides: dict = {}
        for rid in trade.roster_ids:
            sides[rid] = {"roster_id": rid, "manager": season.team_name(rid),
                         "received_players": [], "received_picks": [], "pts_since": 0.0}
        # Players received
        for pid, to_rid in trade.adds.items():
            if to_rid not in sides:
                sides[to_rid] = {"roster_id": to_rid, "manager": season.team_name(to_rid),
                                 "received_players": [], "received_picks": [], "pts_since": 0.0}
            pts = _points_since(season, to_rid, pid, trade.week, max_week=max_week)
            sides[to_rid]["received_players"].append(D.player_name(season.players, pid))
            sides[to_rid]["pts_since"] += pts
        # Draft picks received: pick["roster_id"] is the new owner
        for pick in (trade.draft_picks_raw or []):
            to_rid = pick.get("roster_id")
            if to_rid and to_rid in sides:
                sides[to_rid]["received_picks"].append(_pick_label(pick))
        for s in sides.values():
            s["pts_since"] = round(s["pts_since"], 2)
        out.append({"week": trade.week, "sides": list(sides.values()),
                    "has_picks": trade.draft_picks > 0})
    out.sort(key=lambda r: r["week"])
    return out


def best_trade_period(season, weeks: list):
    """The single most lopsided trade in the period, by points swing between
    its winning and losing side — the per-trade counterpart to
    trade_value_by_team's team-level rollup. Returns None if no trade in the
    period had a positive swing (e.g. too early for either side's players to
    have scored anything yet)."""
    weeks_set = set(weeks)
    outcomes = [o for t in season.trades if t.week in weeks_set
               for o in [_trade_outcome(season, t)] if o and o.margin > 0]
    if not outcomes:
        return None
    return max(outcomes, key=lambda o: o.margin)


# ──────────────────────────────────────────────────────────────────────────
# Risk-adjusted return (Sharpe-style) and points over replacement
# ───────────────────────────────────────────────────────────

@dataclass
class PickupSharpe:
    roster_id: int
    player_id: str
    player_name: str
    position: str | None
    games: int
    mean_ppg: float
    stdev_ppg: float
    sharpe: float | None   # None when stdev is 0 — a single game, or dead-flat scoring


def _pickup_sharpe(season, roster_id: int, player_id: str, since_week: int,
                   max_week: int | None = None) -> "PickupSharpe | None":
    """Risk-adjusted return of one pickup: mean weekly points since add,
    divided by the stdev of those weekly points — a high-mean, low-volatility
    pickup scores higher than an equal-mean boom/bust one. Needs at least 2
    games of data for the stdev to mean anything; returns None otherwise."""
    weekly = _weekly_points_since(season, roster_id, player_id, since_week, max_week)
    if len(weekly) < 2:
        return None
    mean_ppg = statistics.mean(weekly)
    stdev_ppg = statistics.pstdev(weekly)
    pos = (season.players.get(str(player_id)) or {}).get("position")
    return PickupSharpe(
        roster_id=roster_id, player_id=player_id,
        player_name=D.player_name(season.players, player_id),
        position=pos, games=len(weekly),
        mean_ppg=round(mean_ppg, 2), stdev_ppg=round(stdev_ppg, 2),
        sharpe=round(mean_ppg / stdev_ppg, 2) if stdev_ppg else None,
    )


def sharpe_by_position(season, weeks: list, max_week: int | None = None,
                       min_games: int = 2) -> dict:
    """position -> {"avg_sharpe", "n", "best": PickupSharpe} across every
    waiver/FA pickup in the period — which positions are producing reliable
    value off the wire (high, steady weekly points) vs. boom-bust lottery
    tickets. Pickups held fewer than min_games weeks are dropped since a
    single game has no meaningful stdev."""
    weeks_set = set(weeks)
    by_pos: dict = {}
    for t in season.transactions:
        if t.week not in weeks_set:
            continue
        ps = _pickup_sharpe(season, t.roster_id, t.player_id, t.week, max_week=max_week)
        if not ps or ps.sharpe is None or ps.games < min_games:
            continue
        pos = ps.position or "UNK"
        bucket = by_pos.setdefault(pos, {"sharpes": [], "best": None})
        bucket["sharpes"].append(ps.sharpe)
        if bucket["best"] is None or ps.sharpe > bucket["best"].sharpe:
            bucket["best"] = ps
    return {
        pos: {"avg_sharpe": round(statistics.mean(b["sharpes"]), 2),
              "n": len(b["sharpes"]), "best": b["best"]}
        for pos, b in by_pos.items()
    }


@dataclass
class WaiverPOR:
    roster_id: int
    player_id: str
    player_name: str
    position: str | None
    games: int
    points_since: float
    replacement_ppg: float
    por: float   # points_since - replacement_ppg * games
    since_week: int   # the transaction week — sim.py needs this to rebuild a counterfactual score history


def _replacement_ppg_by_position(season, weeks: list, max_week: int | None = None) -> dict:
    """position -> median points a BENCHED player at that position scored,
    across every team-week in the period. Sleeper's matchup payload only
    covers rostered players, so there's no true free-agent pool to measure
    against — a league's own bench depth (the next man up any manager could
    have started instead) is the best available stand-in for 'replacement
    level'."""
    weeks_set = set(weeks)
    pool: dict = {}
    for wk in weeks_set:
        if max_week is not None and wk > max_week:
            continue
        wd = season.weeks.get(wk)
        if not wd:
            continue
        for wt in wd.values():
            for pid, pts in zip(wt.bench, wt.bench_points):
                if not pid:
                    continue
                pos = (season.players.get(str(pid)) or {}).get("position")
                if not pos:
                    continue
                pool.setdefault(pos, []).append(pts or 0.0)
    return {pos: round(statistics.median(vals), 2) for pos, vals in pool.items() if vals}


def points_over_replacement(season, weeks: list, max_week: int | None = None) -> list:
    """Every waiver/FA pickup in the period, ranked by points scored above
    what a bench-level replacement at the same position would have put up
    over the same number of games — so a WR's 40 points and an RB's 40
    points since pickup aren't treated as equally valuable when RB is the
    shallower, higher-replacement-level position that period."""
    replacement = _replacement_ppg_by_position(season, weeks, max_week=max_week)
    weeks_set = set(weeks)
    out = []
    for t in season.transactions:
        if t.week not in weeks_set:
            continue
        weekly = _weekly_points_since(season, t.roster_id, t.player_id, t.week, max_week=max_week)
        if not weekly:
            continue
        pos = (season.players.get(str(t.player_id)) or {}).get("position")
        repl_ppg = replacement.get(pos, 0.0)
        pts = round(sum(weekly), 2)
        out.append(WaiverPOR(
            roster_id=t.roster_id, player_id=t.player_id,
            player_name=D.player_name(season.players, t.player_id),
            position=pos, games=len(weekly), points_since=pts,
            replacement_ppg=repl_ppg, por=round(pts - repl_ppg * len(weekly), 2),
            since_week=t.week,
        ))
    out.sort(key=lambda w: w.por, reverse=True)
    return out


def best_por_period(season, weeks: list, max_week: int | None = None):
    """Single best waiver/FA add of the period by points over replacement."""
    rows = points_over_replacement(season, weeks, max_week=max_week)
    return rows[0] if rows else None
