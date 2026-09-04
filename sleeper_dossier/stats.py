"""
stats.py — all-play, luck index, season accumulation, optimal-lineup efficiency.

All-play: how a team's score that week would have fared against every other
team in the league, not just its actual opponent — the schedule-blind measure
of "true" form that the luck index is built on.

Optimal-lineup efficiency: a greedy slot-filler. Slots are filled
most-constrained-first (fewest eligible players first) so FLEX-type slots
don't greedily claim a player a rigid slot also needed.
"""

from __future__ import annotations
from dataclasses import dataclass
import statistics

from . import data as D

FLEX_ELIGIBLE = {
    "FLEX": {"RB", "WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "REC_FLEX": {"WR", "TE"},
    "IDP_FLEX": {"DL", "LB", "DB"},
}

_NON_STARTING_SLOTS = {"BN", "IR", "TAXI"}


@dataclass
class Efficiency:
    roster_id: int
    actual: float
    optimal: float
    efficiency: float
    bench_points: float
    best_benched_player: str
    best_benched_points: float


@dataclass
class SeasonTeamStats:
    roster_id: int
    avg_efficiency: float
    stdev: float
    high: float
    low: float
    luck_index: float
    all_play_w: int
    all_play_l: int
    season_bench_points: float


def weekly_scores(week_data: dict) -> dict:
    return {rid: wt.points for rid, wt in week_data.items()}


def weekly_median(week_data: dict) -> float:
    scores = list(weekly_scores(week_data).values())
    return statistics.median(scores) if scores else 0.0


def matchup_pairs(week_data: dict) -> list:
    """[(roster_a, points_a, roster_b, points_b), ...] for each real matchup."""
    by_mid = {}
    for rid, wt in week_data.items():
        by_mid.setdefault(wt.matchup_id, []).append((rid, wt.points))
    pairs = []
    for lst in by_mid.values():
        if len(lst) == 2:
            (ra, pa), (rb, pb) = lst
            pairs.append((ra, pa, rb, pb))
    return pairs


def all_play(week_data: dict) -> dict:
    """roster_id -> (wins, losses, ties) vs every other team that week."""
    items = list(week_data.items())
    result = {rid: [0, 0, 0] for rid, _ in items}
    for ra, wa in items:
        for rb, wb in items:
            if ra == rb:
                continue
            if wa.points > wb.points:
                result[ra][0] += 1
            elif wa.points < wb.points:
                result[ra][1] += 1
            else:
                result[ra][2] += 1
    return {rid: tuple(v) for rid, v in result.items()}


def _slot_constraint_count(slot: str, pos_map: dict, candidate_ids) -> int:
    eligible = FLEX_ELIGIBLE.get(slot, {slot})
    return sum(1 for pid in candidate_ids if pos_map.get(pid) in eligible)


def lineup_efficiency(season, week_data: dict) -> dict:
    slots = [s for s in season.roster_positions if s not in _NON_STARTING_SLOTS]
    out = {}
    for rid, wt in week_data.items():
        all_ids = [p for p in (list(wt.starters) + list(wt.bench)) if p]
        pts_map = {}
        for pid, pts in zip(wt.starters, wt.starter_points):
            if pid:
                pts_map[pid] = pts or 0.0
        for pid, pts in zip(wt.bench, wt.bench_points):
            if pid:
                pts_map[pid] = pts or 0.0

        pos_map = {pid: (season.players.get(str(pid)) or {}).get("position") for pid in all_ids}
        actual = round(sum(p or 0.0 for p in wt.starter_points), 2)

        remaining = set(all_ids)
        slot_order = sorted(slots, key=lambda s: _slot_constraint_count(s, pos_map, remaining))
        optimal = 0.0
        for slot in slot_order:
            eligible = FLEX_ELIGIBLE.get(slot, {slot})
            candidates = [pid for pid in remaining if pos_map.get(pid) in eligible]
            if not candidates:
                continue
            best = max(candidates, key=lambda pid: pts_map.get(pid, 0.0))
            optimal += pts_map.get(best, 0.0)
            remaining.discard(best)

        bench_ids = [p for p in wt.bench if p]
        bench_pts = [pts_map.get(pid, 0.0) for pid in bench_ids]
        bench_points = round(sum(bench_pts), 2)
        if bench_ids:
            bi = max(range(len(bench_ids)), key=lambda i: bench_pts[i])
            best_benched_player = D.player_name(season.players, bench_ids[bi])
            best_benched_points = bench_pts[bi]
        else:
            best_benched_player = "—"
            best_benched_points = 0.0

        efficiency = round(actual / optimal * 100, 1) if optimal else 100.0
        out[rid] = Efficiency(
            roster_id=rid,
            actual=actual,
            optimal=round(optimal, 2),
            efficiency=efficiency,
            bench_points=bench_points,
            best_benched_player=best_benched_player,
            best_benched_points=round(best_benched_points, 2),
        )
    return out


def closest_game(season, weeks: list):
    """(margin, week, roster_a, points_a, roster_b, points_b) for the tightest
    matchup across the given weeks, or None."""
    best = None
    for wk in weeks:
        wd = season.weeks.get(wk)
        if not wd:
            continue
        for ra, pa, rb, pb in matchup_pairs(wd):
            margin = abs(pa - pb)
            if best is None or margin < best[0]:
                best = (margin, wk, ra, pa, rb, pb)
    return best


def blowout_game(season, weeks: list):
    """(margin, week, roster_a, points_a, roster_b, points_b) for the biggest
    margin across the given weeks, or None. Mirrors closest_game()."""
    best = None
    for wk in weeks:
        wd = season.weeks.get(wk)
        if not wd:
            continue
        for ra, pa, rb, pb in matchup_pairs(wd):
            margin = abs(pa - pb)
            if best is None or margin > best[0]:
                best = (margin, wk, ra, pa, rb, pb)
    return best


def standings_through(season, upto_week: int | None = None) -> dict:
    """roster_id -> {wins, losses, ties, pf, pa} using only weeks <= upto_week
    (all played weeks if None). A retrospective monthly report needs this —
    season.teams' wins/losses/points_for are live, as-of-today totals, which
    leak later weeks' results into a report about an earlier month."""
    acc = {rid: {"wins": 0, "losses": 0, "ties": 0, "pf": 0.0, "pa": 0.0} for rid in season.teams}
    for wk in sorted(season.weeks.keys()):
        if upto_week is not None and wk > upto_week:
            break
        wd = season.weeks[wk]
        for ra, pa, rb, pb in matchup_pairs(wd):
            acc[ra]["pf"] += pa; acc[ra]["pa"] += pb
            acc[rb]["pf"] += pb; acc[rb]["pa"] += pa
            if pa > pb:
                acc[ra]["wins"] += 1; acc[rb]["losses"] += 1
            elif pb > pa:
                acc[rb]["wins"] += 1; acc[ra]["losses"] += 1
            else:
                acc[ra]["ties"] += 1; acc[rb]["ties"] += 1
    return acc


def season_stats(season, upto_week: int | None = None) -> dict:
    acc = {rid: {"scores": [], "eff": [], "ap_w": 0, "ap_l": 0, "bench": 0.0, "wins": 0, "losses": 0}
           for rid in season.teams}
    for wk in sorted(season.weeks.keys()):
        if upto_week is not None and wk > upto_week:
            break
        wd = season.weeks[wk]
        ap = all_play(wd)
        eff = lineup_efficiency(season, wd)
        for ra, pa, rb, pb in matchup_pairs(wd):
            if pa > pb:
                acc[ra]["wins"] += 1
                acc[rb]["losses"] += 1
            elif pb > pa:
                acc[rb]["wins"] += 1
                acc[ra]["losses"] += 1
        for rid, wt in wd.items():
            a = acc.setdefault(rid, {"scores": [], "eff": [], "ap_w": 0, "ap_l": 0,
                                      "bench": 0.0, "wins": 0, "losses": 0})
            a["scores"].append(wt.points)
            w, l, _ = ap.get(rid, (0, 0, 0))
            a["ap_w"] += w
            a["ap_l"] += l
            e = eff.get(rid)
            if e:
                a["eff"].append(e.efficiency)
                a["bench"] += e.bench_points

    out = {}
    for rid, a in acc.items():
        scores = a["scores"]
        if not scores:
            continue
        games = a["wins"] + a["losses"]
        ap_games = a["ap_w"] + a["ap_l"]
        deserved_win_rate = a["ap_w"] / ap_games if ap_games else 0.5
        luck_index = round(a["wins"] - deserved_win_rate * games, 2)
        out[rid] = SeasonTeamStats(
            roster_id=rid,
            avg_efficiency=round(statistics.mean(a["eff"]), 1) if a["eff"] else 100.0,
            stdev=round(statistics.pstdev(scores), 2) if len(scores) > 1 else 0.0,
            high=max(scores),
            low=min(scores),
            luck_index=luck_index,
            all_play_w=a["ap_w"],
            all_play_l=a["ap_l"],
            season_bench_points=round(a["bench"], 2),
        )
    return out


def power_rank(season, upto_week: int, form_window: int = 3) -> dict:
    """roster_id -> {score, all_play_pct, form_pct, efficiency_pct}.

    A power ranking distinct from the standings, so it can disagree with
    raw W-L and spark debate. Formula:
        score = 0.60 * all-play win% (cumulative through this week)
              + 0.25 * recent-form percentile (avg score, last `form_window`
                       played weeks, ranked against the rest of the league)
              + 0.15 * recent lineup efficiency (same window)
    All-play dominates the weight because it's the "deserved" component —
    schedule-independent, already used elsewhere as the luck-index basis.
    Recent form is the part most likely to diverge from the standings (a
    team can be winning on an easy schedule while cooling off, or losing
    while red-hot) — that's the intentional source of debate. Efficiency
    gets a small weight to reward roster management, not just scoring.
    """
    played = [wk for wk in sorted(season.weeks.keys()) if wk <= upto_week]
    if not played:
        return {}

    ap_acc = {rid: [0, 0] for rid in season.teams}
    for wk in played:
        for rid, (w, l, _t) in all_play(season.weeks[wk]).items():
            ap_acc.setdefault(rid, [0, 0])
            ap_acc[rid][0] += w
            ap_acc[rid][1] += l
    all_play_pct = {rid: (w / (w + l) * 100 if (w + l) else 50.0) for rid, (w, l) in ap_acc.items()}

    recent_weeks = played[-form_window:]
    recent_scores = {rid: [] for rid in season.teams}
    recent_eff = {rid: [] for rid in season.teams}
    for wk in recent_weeks:
        wd = season.weeks[wk]
        eff = lineup_efficiency(season, wd)
        for rid, wt in wd.items():
            recent_scores.setdefault(rid, []).append(wt.points)
        for rid, e in eff.items():
            recent_eff.setdefault(rid, []).append(e.efficiency)
    avg_recent = {rid: (statistics.mean(v) if v else 0.0) for rid, v in recent_scores.items()}
    avg_eff = {rid: (statistics.mean(v) if v else 100.0) for rid, v in recent_eff.items()}

    # Percentile-rank recent form against the league (0-100) rather than a
    # raw ratio, so it's on the same scale as the percentages above and
    # robust to whatever this league's scoring environment looks like.
    ranked = sorted(avg_recent.items(), key=lambda kv: kv[1])
    n = len(ranked)
    form_pct = {rid: (i / (n - 1) * 100 if n > 1 else 50.0) for i, (rid, _v) in enumerate(ranked)}

    out = {}
    for rid in season.teams:
        score = (0.60 * all_play_pct.get(rid, 50.0)
                 + 0.25 * form_pct.get(rid, 50.0)
                 + 0.15 * avg_eff.get(rid, 100.0))
        out[rid] = {
            "score": round(score, 1),
            "all_play_pct": round(all_play_pct.get(rid, 50.0), 1),
            "form_pct": round(form_pct.get(rid, 50.0), 1),
            "efficiency_pct": round(avg_eff.get(rid, 100.0), 1),
        }
    return out
