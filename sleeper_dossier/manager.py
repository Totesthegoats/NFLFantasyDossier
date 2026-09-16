"""
manager.py — manager-centric metrics.

Everything else in the package grades *players* (who scored, who regressed).
These grade the person making the decisions, using only data already fetched
from Sleeper — no external source, so none of it can 404 mid-season.

Each function is season-to-date and takes `upto_week`, so a retrospective
report never leaks results from weeks its readers hadn't seen yet.
"""

from __future__ import annotations
import statistics

from . import stats as S
from . import waivers as W


# ──────────────────────────────────────────────────────────────────────────
# Cumulative decision value  (weekly report)
# ──────────────────────────────────────────────────────────────────────────

def cumulative_decision_value(season, upto_week: int | None = None) -> dict:
    """roster_id -> {"series": [(week, weekly, cumulative), ...], "total", "best_week", "worst_week"}.

    `weekly` is points left on the bench that week: actual - optimal, so it is
    always <= 0 and 0.0 means a perfect lineup. Cumulating it turns the weekly
    efficiency number — which is noisy and forgotten by Tuesday — into a
    season-long running cost, where the *shape* carries the story: a manager
    bleeding two points a week is a different problem from one who lost
    thirty in a single week and has been clean since.

    Points, not efficiency percentage, on purpose: percentages aren't
    additive, and a 90% week for a 150-point team costs far more than a 90%
    week for an 80-point team.
    """
    weeks = sorted(w for w in season.weeks if upto_week is None or w <= upto_week)
    running: dict = {}
    out: dict = {}
    for wk in weeks:
        eff = S.lineup_efficiency(season, season.weeks[wk])
        for rid, e in eff.items():
            delta = round(e.actual - e.optimal, 2)
            running[rid] = round(running.get(rid, 0.0) + delta, 2)
            out.setdefault(rid, []).append((wk, delta, running[rid]))

    result = {}
    for rid, series in out.items():
        if not series:
            continue
        best = max(series, key=lambda s: s[1])
        worst = min(series, key=lambda s: s[1])
        result[rid] = {
            "series": series,
            "total": series[-1][2],
            "best_week": {"week": best[0], "delta": best[1]},
            "worst_week": {"week": worst[0], "delta": worst[1]},
        }
    return result


# ──────────────────────────────────────────────────────────────────────────
# Schedule swap  (monthly report)
# ──────────────────────────────────────────────────────────────────────────

def schedule_swap_matrix(season, upto_week: int | None = None) -> dict:
    """{roster_id: {schedule_owner_rid: wins}} — every team's record replayed
    against every other team's slate.

    Each team keeps its own weekly scores and inherits someone else's
    opponents: for week w, team A is credited a win if A's week-w score beats
    the score of whoever B actually played that week. The diagonal
    (A running A's own schedule) reproduces A's real record, which is the
    built-in correctness check.

    A team scheduled against itself in some week is skipped for that week
    rather than auto-counted, so a bye-like artefact can't inflate a row.
    """
    weeks = sorted(w for w in season.weeks if upto_week is None or w <= upto_week)

    # week -> {rid: opponent_rid} and week -> {rid: points}
    opponents: dict = {}
    points: dict = {}
    for wk in weeks:
        wd = season.weeks[wk]
        points[wk] = S.weekly_scores(wd)
        opp = {}
        for ra, _pa, rb, _pb in S.matchup_pairs(wd):
            opp[ra] = rb
            opp[rb] = ra
        opponents[wk] = opp

    rids = sorted(season.teams)
    matrix = {rid: {} for rid in rids}
    for rid in rids:
        for sched_rid in rids:
            wins = losses = ties = 0
            for wk in weeks:
                mine = points[wk].get(rid)
                opp_rid = opponents[wk].get(sched_rid)
                if mine is None or opp_rid is None or opp_rid == rid:
                    continue
                theirs = points[wk].get(opp_rid)
                if theirs is None:
                    continue
                if mine > theirs:
                    wins += 1
                elif mine < theirs:
                    losses += 1
                else:
                    ties += 1
            matrix[rid][sched_rid] = {"wins": wins, "losses": losses, "ties": ties}
    return matrix


def schedule_luck(season, upto_week: int | None = None) -> dict:
    """roster_id -> {actual, best, worst, average, delta} wins across all
    twelve possible schedules. `delta` = actual - average: positive means the
    schedule flattered them, negative means it robbed them. This is the
    number worth printing next to the matrix — the grid is the evidence, the
    delta is the verdict.
    """
    matrix = schedule_swap_matrix(season, upto_week)
    out = {}
    for rid, row in matrix.items():
        wins = [v["wins"] for v in row.values()]
        if not wins:
            continue
        actual = row.get(rid, {}).get("wins", 0)
        avg = statistics.mean(wins)
        out[rid] = {
            "actual": actual,
            "best": max(wins),
            "worst": min(wins),
            "average": round(avg, 2),
            "delta": round(actual - avg, 2),
        }
    return out


# ──────────────────────────────────────────────────────────────────────────
# Transaction timing  (monthly report)
# ──────────────────────────────────────────────────────────────────────────

def transaction_timing(season, weeks: list, window: int = 3,
                       max_week: int | None = None) -> list:
    """One entry per pickup: what the player averaged in the `window` weeks
    before the add vs. the `window` weeks after.

    Separates buying a breakout from chasing a box score. High-before /
    low-after is the classic mistake — paying for production that already
    happened. Low-before / high-after is the manager seeing it coming.

    Pickups with no after-data yet are dropped, which is why this is a
    monthly rather than weekly measure: in a single-week report almost every
    add is too recent to have been scored.
    """
    cap = max_week if max_week is not None else max(season.weeks or [0])
    weeks_set = set(weeks)
    out = []
    for t in season.transactions:
        if t.week not in weeks_set:
            continue
        before = W._weekly_points_since(season, t.roster_id, t.player_id,
                                        max(0, t.week - window - 1), max_week=t.week)
        after = W._weekly_points_since(season, t.roster_id, t.player_id,
                                       t.week, max_week=min(cap, t.week + window))
        if not after:
            continue
        # Before the add the player was on someone else's roster (or nobody's),
        # so _weekly_points_since only sees the weeks this manager held them —
        # usually empty. Fall back to the league-wide record of that player.
        if not before:
            before = _league_wide_points(season, t.player_id,
                                         max(0, t.week - window), t.week)
        out.append({
            "roster_id": t.roster_id,
            "player_id": t.player_id,
            "player_name": _player_name(season, t.player_id),
            "week": t.week,
            "faab": t.faab,
            "before_ppg": round(statistics.mean(before), 2) if before else 0.0,
            "after_ppg": round(statistics.mean(after), 2),
            "games_after": len(after),
        })
    return out


def _league_wide_points(season, player_id: str, from_week: int, to_week: int) -> list:
    """Weekly points for a player on whichever roster held them, for weeks
    (from_week, to_week]. Needed because a pickup's pre-add production
    belongs to a different team's box score."""
    out = []
    for wk in sorted(season.weeks):
        if wk <= from_week or wk > to_week:
            continue
        for wt in season.weeks[wk].values():
            if player_id in wt.starters:
                out.append(wt.starter_points[wt.starters.index(player_id)] or 0.0)
                break
            if player_id in wt.bench:
                out.append(wt.bench_points[wt.bench.index(player_id)] or 0.0)
                break
    return out


def _player_name(season, player_id: str) -> str:
    from . import data as D
    return D.player_name(season.players, player_id)


def timing_summary(season, weeks: list, window: int = 3,
                   max_week: int | None = None) -> dict:
    """roster_id -> {n, avg_swing, chased, anticipated} over their pickups.
    avg_swing is mean(after - before): positive means their adds improved
    after arriving."""
    rows = transaction_timing(season, weeks, window=window, max_week=max_week)
    acc: dict = {}
    for r in rows:
        a = acc.setdefault(r["roster_id"], {"swings": [], "chased": 0, "anticipated": 0})
        swing = r["after_ppg"] - r["before_ppg"]
        a["swings"].append(swing)
        if swing < -2.0:
            a["chased"] += 1
        elif swing > 2.0:
            a["anticipated"] += 1
    return {
        rid: {"n": len(v["swings"]), "avg_swing": round(statistics.mean(v["swings"]), 2),
              "chased": v["chased"], "anticipated": v["anticipated"]}
        for rid, v in acc.items() if v["swings"]
    }


# ──────────────────────────────────────────────────────────────────────────
# Manager fingerprint  (monthly report)
# ──────────────────────────────────────────────────────────────────────────

FINGERPRINT_AXES = ["Lineup IQ", "Waiver Aggression", "Trade Activity",
                    "Roster Churn", "Consistency"]


def _rescale(values: dict, invert: bool = False) -> dict:
    """Map raw values onto 0-100 by min-max across the league.

    Deliberately league-relative rather than absolute: "70% lineup
    efficiency" means nothing to a reader, but "most efficient manager in
    your league" does, and the radar is a comparison object. The cost is that
    every league always has a 0 and a 100 on every axis — worth stating
    wherever this is displayed.
    """
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi == lo:
        return {rid: 50.0 for rid in values}
    out = {}
    for rid, v in values.items():
        pct = (v - lo) / (hi - lo) * 100
        out[rid] = round(100 - pct if invert else pct, 1)
    return out


def manager_fingerprint(season, weeks: list, upto_week: int | None = None) -> dict:
    """roster_id -> {axis_name: 0-100} across FINGERPRINT_AXES.

    A five-axis identity per manager. Not a ranking — a shape. Two managers
    with the same record can look completely different here, which is the
    point: it describes *how* someone plays, not how well.
    """
    last = upto_week if upto_week is not None else max(season.weeks or [0])

    ss = S.season_stats(season, upto_week=last)
    eff = {rid: s.avg_efficiency for rid, s in ss.items()}

    faab = W.faab_spent_by_team(season, weeks)
    trades = W.trade_count_by_team(season, weeks)
    adds = W.activity_counts(season, weeks)
    cons = S.consistency(season, upto_week=last)

    rids = list(season.teams)
    raw_eff = {rid: eff.get(rid, 0.0) for rid in rids}
    raw_faab = {rid: float(faab.get(rid, 0)) for rid in rids}
    raw_trade = {rid: float(trades.get(rid, 0)) for rid in rids}
    raw_churn = {rid: float(adds.get(rid, 0)) for rid in rids}
    raw_cv = {rid: cons.get(rid, {}).get("cv", 0.0) for rid in rids}

    s_eff = _rescale(raw_eff)
    s_faab = _rescale(raw_faab)
    s_trade = _rescale(raw_trade)
    s_churn = _rescale(raw_churn)
    s_cons = _rescale(raw_cv, invert=True)   # low volatility = high consistency

    return {
        rid: {
            "Lineup IQ": s_eff.get(rid, 50.0),
            "Waiver Aggression": s_faab.get(rid, 50.0),
            "Trade Activity": s_trade.get(rid, 50.0),
            "Roster Churn": s_churn.get(rid, 50.0),
            "Consistency": s_cons.get(rid, 50.0),
        }
        for rid in rids
    }


# ──────────────────────────────────────────────────────────────────────────
# Draft capital efficiency  (season / draft review)
# ──────────────────────────────────────────────────────────────────────────

def draft_slope(season, top_n: int = 24) -> list:
    """Draft slot vs. finishing points rank, one entry per drafted player.

    `slope` = draft_rank - points_rank, so a positive slope is a pick that
    outran its cost. Returns the most extreme `top_n` in both directions —
    plotting all ~200 picks is unreadable, and the middle of the board is
    where nothing interesting happened by definition.

    Keeper leagues distort this: a keeper's pick number encodes last year's
    cost rather than this year's market, so `is_keeper` is carried through
    for callers that want to mark or drop them.
    """
    board = S.draft_value_board(season, top_n=top_n)
    rows = board.get("values", []) + board.get("busts", [])
    out = []
    for r in rows:
        out.append({
            "player_id": r["player_id"],
            "player_name": r["player_name"],
            "roster_id": r.get("roster_id"),
            "pick_no": r["pick_no"],
            "round": r.get("round"),
            "is_keeper": r.get("is_keeper"),
            "points": r["points"],
            "draft_rank": r["draft_rank"],
            "points_rank": r["points_rank"],
            "slope": r["value_delta"],
        })
    out.sort(key=lambda r: r["slope"], reverse=True)
    return out


def draft_capital_by_team(season) -> dict:
    """roster_id -> {picks, total_slope, avg_slope}: whose draft board as a
    whole beat its slot. Averaged as well as totalled because a manager with
    more scoring picks on roster would otherwise win on volume alone."""
    if not season.draft_picks:
        return {}
    rows = draft_slope(season, top_n=10_000)
    acc: dict = {}
    for r in rows:
        rid = r.get("roster_id")
        if rid is None:
            continue
        a = acc.setdefault(rid, {"picks": 0, "total_slope": 0})
        a["picks"] += 1
        a["total_slope"] += r["slope"]
    for rid, a in acc.items():
        a["avg_slope"] = round(a["total_slope"] / a["picks"], 1) if a["picks"] else 0.0
    return acc
