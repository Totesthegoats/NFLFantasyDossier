"""
awards.py — The Hall of Fame / Hall of Shame engine.

The centrepiece of the product. Each award is one small function that
consumes a context dict and returns an Award (or None). Adding a new
roast = add a function + register it. The named title is the magic;
the stat behind it is trivial.

Two registries:
  WEEKLY_AWARDS  — run each week on a single week's data
  SEASON_AWARDS  — run once at season end on accumulated data
"""

from __future__ import annotations
from dataclasses import dataclass, field

from . import stats as S
from . import waivers as W


@dataclass
class Award:
    title: str
    flavour: str
    hall: str                       # "fame" | "shame"
    winner_rid: int | None
    headline: str                   # formatted value for the winner
    podium: list = field(default_factory=list)   # [(rid, value_str), ...]


# ----------------------------------------------------------------------
# WEEKLY AWARDS
# ----------------------------------------------------------------------

def a_top_of_pile(ctx):
    r = sorted(ctx["scores"].items(), key=lambda x: x[1], reverse=True)
    return Award("Top of the Pile", "Highest score this week", "fame",
                 r[0][0], f"{r[0][1]:.1f} pts",
                 [(rid, f"{p:.1f} pts") for rid, p in r[1:3]])


def a_bottom_feeder(ctx):
    r = sorted(ctx["scores"].items(), key=lambda x: x[1])
    return Award("Bottom Feeders", "Lowest score this week", "shame",
                 r[0][0], f"{r[0][1]:.1f} pts",
                 [(rid, f"{p:.1f} pts") for rid, p in r[1:3]])


def a_sharpshooter(ctx):
    """Highest single STARTING player score this week."""
    best = None
    for rid, wt in ctx["week_data"].items():
        for pid, pts in zip(wt.starters, wt.starter_points or []):
            if best is None or pts > best[2]:
                best = (rid, pid, pts)
    if not best:
        return None
    rid, pid, pts = best
    name = S.D.player_name(ctx["season"].players, pid)
    return Award("The Sharpshooter", "Highest-scoring starter", "fame",
                 rid, f"{name} — {pts:.1f} pts", [])


def a_highway_robbery(ctx):
    pairs = ctx["pairs"]
    if not pairs:
        return None
    win = max(pairs, key=lambda p: abs(p[1] - p[3]))
    ra, pa, rb, pb = win
    winner = ra if pa > pb else rb
    return Award("Highway Robbery", "Biggest blowout win", "fame",
                 winner, f"won by {abs(pa-pb):.1f} ({max(pa,pb):.1f}–{min(pa,pb):.1f})", [])


def a_nail_biter(ctx):
    pairs = ctx["pairs"]
    if not pairs:
        return None
    close = min(pairs, key=lambda p: abs(p[1] - p[3]))
    ra, pa, rb, pb = close
    winner = ra if pa > pb else rb
    return Award("Nail-Biter of the Week", "Closest matchup", "fame",
                 winner, f"won by just {abs(pa-pb):.1f}", [])


def a_coin_flip_king(ctx):
    """Won the matchup despite a bottom-3 score in the league that week."""
    pairs, scores = ctx["pairs"], ctx["scores"]
    if len(scores) < 3:
        return None
    ranked = sorted(scores.items(), key=lambda kv: kv[1])
    bottom3 = {rid for rid, _ in ranked[:3]}
    rank_of = {rid: i for i, (rid, _) in enumerate(ranked, 1)}
    cand = []
    for ra, pa, rb, pb in pairs:
        winner = ra if pa > pb else rb
        if winner in bottom3:
            cand.append((winner, rank_of[winner], max(pa, pb)))
    if not cand:
        return None
    rid, rank, pts = min(cand, key=lambda x: x[1])
    return Award("Coin-Flip King", "Won despite a bottom-3 score", "fame",
                 rid, f"won with {pts:.1f} ({rank}{_ordinal_suffix(rank)}-lowest score this week)", [])


def a_robbed(ctx):
    """Lost the matchup despite a top-3 score in the league that week."""
    pairs, scores = ctx["pairs"], ctx["scores"]
    if len(scores) < 3:
        return None
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top3 = {rid for rid, _ in ranked[:3]}
    rank_of = {rid: i for i, (rid, _) in enumerate(ranked, 1)}
    cand = []
    for ra, pa, rb, pb in pairs:
        loser = ra if pa < pb else rb
        lp = pa if loser == ra else pb
        if loser in top3:
            cand.append((loser, rank_of[loser], lp))
    if not cand:
        return None
    rid, rank, pts = min(cand, key=lambda x: x[1])
    return Award("Robbed", "Lost despite a top-3 score", "shame",
                 rid, f"lost with {pts:.1f} ({rank}{_ordinal_suffix(rank)}-highest score this week)", [])


def a_sleepwalker(ctx):
    """Started the exact same lineup as last week — the 'forgot to set it'
    award. Sleeper's injury_status is a live snapshot, not historical, so
    this checks lineup-vs-last-week instead of player health, which is the
    reliable signal for a retrospective report."""
    season, week, wd, eff = ctx["season"], ctx["week"], ctx["week_data"], ctx["efficiency"]
    prev_wd = season.weeks.get(week - 1)
    if not prev_wd:
        return None
    candidates = [rid for rid, wt in wd.items()
                  if wt.starters and rid in prev_wd and set(wt.starters) == set(prev_wd[rid].starters)]
    if not candidates:
        return None
    rid = max(candidates, key=lambda r: eff[r].bench_points if r in eff else 0)
    e = eff.get(rid)
    extra = f" — {e.bench_points:.1f} pts benched as a result" if e and e.bench_points > 0 else ""
    return Award("Sleepwalker", "Started the exact same lineup as last week", "shame",
                 rid, f"unchanged lineup{extra}", [])


def a_bench_warmer(ctx):
    eff = ctx["efficiency"]
    r = sorted(eff.values(), key=lambda e: e.bench_points, reverse=True)
    top = r[0]
    return Award("Bench Warmer of the Week", "Most points left on the bench", "shame",
                 top.roster_id,
                 f"{top.bench_points:.1f} benched (worst: {top.best_benched_player} {top.best_benched_points:.1f})",
                 [(e.roster_id, f"{e.bench_points:.1f} benched") for e in r[1:3]])


def a_bumbling_boss(ctx):
    """Lowest lineup efficiency — the weekly villain."""
    eff = ctx["efficiency"]
    r = sorted(eff.values(), key=lambda e: e.efficiency)
    top = r[0]
    if top.efficiency >= 99.9:
        return None
    return Award("Bumbling Boss", "Worst lineup management (actual vs optimal)", "shame",
                 top.roster_id,
                 f"{top.efficiency:.0f}% efficient ({top.actual:.1f} of possible {top.optimal:.1f})",
                 [(e.roster_id, f"{e.efficiency:.0f}%") for e in r[1:3]])


def a_perfect_manager(ctx):
    """Highest efficiency — set (near) their best possible lineup."""
    eff = ctx["efficiency"]
    r = sorted(eff.values(), key=lambda e: e.efficiency, reverse=True)
    top = r[0]
    return Award("The Perfect Manager", "Best lineup management this week", "fame",
                 top.roster_id, f"{top.efficiency:.0f}% efficient", [])


def a_faab_fumbler(ctx):
    fm = ctx.get("faab_fumbler")
    if not fm:
        return None
    if fm.points_since <= 0:
        val = f"spent ${fm.faab} on {fm.player_name} for {fm.points_since:.0f} pts"
    else:
        val = f"${fm.faab} on {fm.player_name} = ${fm.cost_per_point}/pt"
    return Award("Waiver Disaster", "Worst waiver-wire spend", "shame",
                 fm.roster_id, val, [])


def a_wheeler_dealer(ctx):
    bp = ctx.get("best_pickup")
    if not bp or bp.points_since <= 0:
        return None
    spend = f"${bp.faab}" if bp.faab else "free"
    return Award("Wheeler Dealer", "Shrewdest waiver pickup", "fame",
                 bp.roster_id,
                 f"{bp.player_name} ({spend}) → {bp.points_since:.1f} pts since", [])


WEEKLY_AWARDS = [
    a_top_of_pile, a_sharpshooter, a_highway_robbery, a_perfect_manager,
    a_wheeler_dealer, a_coin_flip_king, a_nail_biter,
    a_bottom_feeder, a_bench_warmer, a_bumbling_boss, a_robbed,
    a_faab_fumbler, a_sleepwalker,
]


# ----------------------------------------------------------------------
# SEASON AWARDS
# ----------------------------------------------------------------------

def s_coach_of_year(ctx):
    ss = ctx["season_stats"]
    r = sorted(ss.values(), key=lambda s: s.avg_efficiency, reverse=True)
    top = r[0]
    return Award("Coach of the Year", "Best average lineup efficiency", "fame",
                 top.roster_id, f"{top.avg_efficiency:.0f}% avg efficiency",
                 [(s.roster_id, f"{s.avg_efficiency:.0f}%") for s in r[1:3]])


def s_bumbler_of_year(ctx):
    ss = ctx["season_stats"]
    r = sorted(ss.values(), key=lambda s: s.avg_efficiency)
    top = r[0]
    return Award("Lord of the Dullards", "Worst average lineup efficiency", "shame",
                 top.roster_id, f"{top.avg_efficiency:.0f}% avg efficiency",
                 [(s.roster_id, f"{s.avg_efficiency:.0f}%") for s in r[1:3]])


def s_iron_manager(ctx):
    ss = ctx["season_stats"]
    r = sorted(ss.values(), key=lambda s: s.stdev)
    top = r[0]
    return Award("Old Reliable", "Most consistent scorer (lowest variance)", "fame",
                 top.roster_id, f"±{top.stdev:.1f} pts week to week", [])


def s_boom_bust(ctx):
    ss = ctx["season_stats"]
    r = sorted(ss.values(), key=lambda s: s.stdev, reverse=True)
    top = r[0]
    return Award("The Heart-Attack Merchant", "Most volatile scorer", "shame",
                 top.roster_id, f"±{top.stdev:.1f} pts swings (high {top.high:.0f}, low {top.low:.0f})", [])


def s_luckiest(ctx):
    ss = ctx["season_stats"]
    r = sorted(ss.values(), key=lambda s: s.luck_index, reverse=True)
    top = r[0]
    if top.luck_index <= 0:
        return None
    return Award("Horseshoe Up Somewhere", "Most wins above what scores deserved", "fame",
                 top.roster_id, f"+{top.luck_index:.1f} wins vs expected", [])


def s_unluckiest(ctx):
    ss = ctx["season_stats"]
    r = sorted(ss.values(), key=lambda s: s.luck_index)
    top = r[0]
    if top.luck_index >= 0:
        return None
    return Award("Cursed", "Most wins BELOW what scores deserved", "shame",
                 top.roster_id, f"{top.luck_index:.1f} wins vs expected", [])


def s_bench_hoarder(ctx):
    ss = ctx["season_stats"]
    r = sorted(ss.values(), key=lambda s: s.season_bench_points, reverse=True)
    top = r[0]
    return Award("Strength in Depth (or Stupidity)", "Most points benched all season", "shame",
                 top.roster_id, f"{top.season_bench_points:.0f} pts on the pine", [])


def s_highest_high(ctx):
    ss = ctx["season_stats"]
    r = sorted(ss.values(), key=lambda s: s.high, reverse=True)
    top = r[0]
    return Award("Best Week of the Season", "Highest single-week score", "fame",
                 top.roster_id, f"{top.high:.1f} pts", [])


def s_lowest_low(ctx):
    ss = ctx["season_stats"]
    r = sorted(ss.values(), key=lambda s: s.low)
    top = r[0]
    return Award("Worst Week of the Season", "Lowest single-week score", "shame",
                 top.roster_id, f"{top.low:.1f} pts", [])


def s_wheeler_dealer(ctx):
    """Most trades made this season — pure activity, not whether they paid off."""
    counts = ctx["trade_counts"]
    if not counts:
        return None
    r = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    rid, n = r[0]
    if n <= 0:
        return None
    return Award("Wheeler-Dealer", "Most trades made this season", "fame",
                 rid, f"{n} trade{'s' if n != 1 else ''} made",
                 [(rid2, f"{n2} trade{'s' if n2 != 1 else ''}") for rid2, n2 in r[1:3]])


def s_the_mark(ctx):
    """Opposite of Wheeler-Dealer: lost the most net value across this season's trades."""
    values = ctx["trade_values"]
    if not values:
        return None
    r = sorted(values.items(), key=lambda kv: kv[1])
    rid, v = r[0]
    if v >= 0:
        return None
    return Award("The Mark", "Lost the most value in trades this season", "shame",
                 rid, f"{v:+.1f} pts net from trades", [])


def s_waiver_warrior(ctx):
    points = ctx["waiver_points"]
    if not points:
        return None
    r = sorted(points.items(), key=lambda kv: kv[1], reverse=True)
    rid, pts = r[0]
    if pts <= 0:
        return None
    return Award("Waiver Warrior", "Most points added via the waiver wire this season", "fame",
                 rid, f"{pts:.1f} pts from waiver pickups",
                 [(rid2, f"{p2:.1f} pts") for rid2, p2 in r[1:3]])


def s_waiver_washout(ctx):
    """Opposite of Waiver Warrior: least production from this season's waiver adds."""
    points = ctx["waiver_points"]
    if not points:
        return None
    r = sorted(points.items(), key=lambda kv: kv[1])
    rid, pts = r[0]
    return Award("Waiver Washout", "Least production from waiver-wire adds this season", "shame",
                 rid, f"{pts:.1f} pts from waiver pickups", [])


def s_faabulous(ctx):
    totals = ctx["faab_totals"]
    if not totals:
        return None
    r = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    rid, amt = r[0]
    if amt <= 0:
        return None
    return Award("FAAB-ulous", "Most FAAB spent this season", "fame",
                 rid, f"${amt} spent on waivers",
                 [(rid2, f"${a2}") for rid2, a2 in r[1:3]])


SEASON_AWARDS = [
    s_coach_of_year, s_iron_manager, s_luckiest, s_highest_high,
    s_wheeler_dealer, s_waiver_warrior, s_faabulous,
    s_bumbler_of_year, s_boom_bust, s_unluckiest, s_bench_hoarder, s_lowest_low,
    s_the_mark, s_waiver_washout,
]


# ----------------------------------------------------------------------
# Runners
# ----------------------------------------------------------------------

def compute_weekly(season, week):
    wd = season.weeks[week]
    ctx = {
        "season": season,
        "week": week,
        "week_data": wd,
        "scores": S.weekly_scores(wd),
        "pairs": S.matchup_pairs(wd),
        "all_play": S.all_play(wd),
        "efficiency": S.lineup_efficiency(season, wd),
        "faab_fumbler": W.faab_fumbler(season, week),
        "best_pickup": W.best_pickup(season, week),
    }
    results = []
    for fn in WEEKLY_AWARDS:
        try:
            a = fn(ctx)
            if a:
                results.append(a)
        except Exception as e:
            print(f"  [weekly award {fn.__name__} skipped: {e}]")
    return ctx, results


def compute_season(season):
    weeks = sorted(season.weeks.keys())
    ctx = {
        "season": season,
        "season_stats": S.season_stats(season),
        "trade_counts": W.trade_count_by_team(season, weeks),
        "trade_values": W.trade_value_by_team(season, weeks),
        "waiver_points": W.waiver_points_by_team(season, weeks),
        "faab_totals": W.faab_spent_by_team(season, weeks),
    }
    results = []
    for fn in SEASON_AWARDS:
        try:
            a = fn(ctx)
            if a:
                results.append(a)
        except Exception as e:
            print(f"  [season award {fn.__name__} skipped: {e}]")
    return ctx, results


# ----------------------------------------------------------------------
# MONTHLY AWARDS  (operate on month_stats: roster_id -> MonthTeam)
# ----------------------------------------------------------------------

def m_manager_of_month(ctx):
    """Headline award: most points above league average for the month."""
    ms = ctx["month_stats"]
    r = sorted(ms.values(), key=lambda m: m.pts_above_avg, reverse=True)
    top = r[0]
    return Award("Manager of the Month", "Most points above league average", "fame",
                 top.roster_id,
                 f"+{top.pts_above_avg:.1f} vs avg ({top.total_points:.0f} pts, {top.h2h_w}-{top.h2h_l})",
                 [(m.roster_id, f"+{m.pts_above_avg:.1f}") for m in r[1:3]])


def m_wooden_spoon(ctx):
    ms = ctx["month_stats"]
    r = sorted(ms.values(), key=lambda m: m.pts_above_avg)
    top = r[0]
    return Award("Dud of the Month", "Furthest below league average", "shame",
                 top.roster_id,
                 f"{top.pts_above_avg:.1f} vs avg ({top.total_points:.0f} pts, {top.h2h_w}-{top.h2h_l})",
                 [(m.roster_id, f"{m.pts_above_avg:.1f}") for m in r[1:3]])


def m_biggest_climber(ctx):
    ms = ctx["month_stats"]
    r = sorted(ms.values(), key=lambda m: m.climb, reverse=True)
    top = r[0]
    if top.climb <= 0:
        return None
    return Award("Up and to the Right", "Biggest standings climb this month", "fame",
                 top.roster_id,
                 f"+{top.climb} places (now {top.rank_end}{_ordinal_suffix(top.rank_end)})", [])


def m_biggest_faller(ctx):
    ms = ctx["month_stats"]
    r = sorted(ms.values(), key=lambda m: m.climb)
    top = r[0]
    if top.climb >= 0:
        return None
    return Award("Freefall", "Biggest standings drop this month", "shame",
                 top.roster_id,
                 f"{top.climb} places (now {top.rank_end}{_ordinal_suffix(top.rank_end)})", [])


def m_month_efficiency(ctx):
    ms = ctx["month_stats"]
    r = sorted(ms.values(), key=lambda m: m.avg_efficiency, reverse=True)
    top = r[0]
    return Award("Tactician of the Month", "Best average lineup efficiency", "fame",
                 top.roster_id, f"{top.avg_efficiency:.0f}% efficient", [])


def m_month_bumbler(ctx):
    ms = ctx["month_stats"]
    r = sorted(ms.values(), key=lambda m: m.avg_efficiency)
    top = r[0]
    if top.avg_efficiency >= 99.9:
        return None
    return Award("Asleep at the Wheel", "Worst average lineup efficiency", "shame",
                 top.roster_id,
                 f"{top.avg_efficiency:.0f}% efficient ({top.bench_points:.0f} pts benched)", [])


def m_hot_hand(ctx):
    """Best all-play record across the month (true form, schedule-blind)."""
    ms = ctx["month_stats"]
    r = sorted(ms.values(), key=lambda m: (m.all_play_w / max(1, m.all_play_w + m.all_play_l)),
               reverse=True)
    top = r[0]
    return Award("On a Heater", "Best all-play form this month", "fame",
                 top.roster_id, f"{top.all_play_w}-{top.all_play_l} all-play", [])


def m_month_high(ctx):
    ms = ctx["month_stats"]
    r = sorted(ms.values(), key=lambda m: m.best_week, reverse=True)
    top = r[0]
    return Award("Peak of the Month", "Highest single week this month", "fame",
                 top.roster_id, f"{top.best_week:.1f} pts", [])


def _ordinal_suffix(n):
    if n is None:
        return ""
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def m_wheeler_dealer(ctx):
    """Most trades made this month — pure activity, not whether they paid off."""
    counts = ctx["trade_counts"]
    if not counts:
        return None
    r = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    rid, n = r[0]
    if n <= 0:
        return None
    return Award("Wheeler-Dealer", "Most trades made this month", "fame",
                 rid, f"{n} trade{'s' if n != 1 else ''} made",
                 [(rid2, f"{n2} trade{'s' if n2 != 1 else ''}") for rid2, n2 in r[1:3]])


def m_the_mark(ctx):
    """Opposite of Wheeler-Dealer: lost the most net value in this month's trades."""
    values = ctx["trade_values"]
    if not values:
        return None
    r = sorted(values.items(), key=lambda kv: kv[1])
    rid, v = r[0]
    if v >= 0:
        return None
    return Award("The Mark", "Lost the most value in trades this month", "shame",
                 rid, f"{v:+.1f} pts net from trades", [])


def m_waiver_warrior(ctx):
    points = ctx["waiver_points"]
    if not points:
        return None
    r = sorted(points.items(), key=lambda kv: kv[1], reverse=True)
    rid, pts = r[0]
    if pts <= 0:
        return None
    return Award("Waiver Warrior", "Most points added via the waiver wire this month", "fame",
                 rid, f"{pts:.1f} pts from waiver pickups",
                 [(rid2, f"{p2:.1f} pts") for rid2, p2 in r[1:3]])


def m_waiver_washout(ctx):
    """Opposite of Waiver Warrior: least production from this month's waiver adds."""
    points = ctx["waiver_points"]
    if not points:
        return None
    r = sorted(points.items(), key=lambda kv: kv[1])
    rid, pts = r[0]
    return Award("Waiver Washout", "Least production from waiver-wire adds this month", "shame",
                 rid, f"{pts:.1f} pts from waiver pickups", [])


def m_faabulous(ctx):
    totals = ctx["faab_totals"]
    if not totals:
        return None
    r = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    rid, amt = r[0]
    if amt <= 0:
        return None
    return Award("FAAB-ulous", "Most FAAB spent this month", "fame",
                 rid, f"${amt} spent on waivers",
                 [(rid2, f"${a2}") for rid2, a2 in r[1:3]])


def m_best_below_500(ctx):
    """Rewards a different skill than the top-line award: staying respectable
    on a team that's still losing more than it wins this month."""
    ms = ctx["month_stats"]
    cands = [m for m in ms.values() if m.h2h_l > m.h2h_w]
    if not cands:
        return None
    top = max(cands, key=lambda m: (m.h2h_w - m.h2h_l, m.pts_above_avg))
    return Award("Best Manager Below .500", "Best record among under-.500 teams this month", "fame",
                 top.roster_id, f"{top.h2h_w}-{top.h2h_l} ({top.pts_above_avg:+.1f} vs avg)", [])


def m_lineup_wizard(ctx):
    """Highest single-week efficiency this month — distinct from the
    season-long average-efficiency award, so a one-week peak counts too."""
    season, weeks = ctx["season"], ctx["weeks"]
    best = None
    for wk in weeks:
        wd = season.weeks.get(wk)
        if not wd:
            continue
        for rid, e in S.lineup_efficiency(season, wd).items():
            if best is None or e.efficiency > best[2]:
                best = (rid, wk, e.efficiency)
    if not best:
        return None
    rid, wk, pct = best
    return Award("Lineup Wizard", "Highest single-week efficiency this month", "fame",
                 rid, f"{pct:.0f}% efficient in week {wk}", [])


def m_bench_hero(ctx):
    ms = ctx["month_stats"]
    r = sorted(ms.values(), key=lambda m: m.bench_points)
    top = r[0]
    return Award("Bench Hero Avoided", "Fewest points left on the bench this month", "fame",
                 top.roster_id, f"only {top.bench_points:.0f} pts benched",
                 [(m.roster_id, f"{m.bench_points:.0f} pts") for m in r[1:3]])


def m_closest_survivor(ctx):
    season, weeks = ctx["season"], ctx["weeks"]
    game = S.closest_game(season, weeks)
    if not game:
        return None
    margin, wk, ra, pa, rb, pb = game
    winner = ra if pa > pb else rb
    return Award("Closest-Call Survivor", "Won the tightest matchup this month", "fame",
                 winner, f"won by just {margin:.1f} in week {wk}", [])


def m_most_improved(ctx):
    """Biggest per-week scoring jump vs the team's own prior month (uses
    avg, not total, so months with different week counts compare fairly)."""
    ms = ctx["month_stats"]
    prev = ctx.get("prev_month_stats") or {}
    if not prev:
        return None
    deltas = []
    for rid, m in ms.items():
        p = prev.get(rid)
        if p:
            deltas.append((rid, m.avg_points - p.avg_points, m, p))
    if not deltas:
        return None
    deltas.sort(key=lambda x: x[1], reverse=True)
    rid, delta, m, p = deltas[0]
    if delta <= 0:
        return None
    return Award("Most Improved Scoring", "Biggest points-per-week jump vs last month", "fame",
                 rid, f"+{delta:.1f} pts/wk vs last month ({p.avg_points:.1f} -> {m.avg_points:.1f})", [])


def m_giant_killer(ctx):
    """Beat this month's highest-scoring team in head-to-head play —
    None if that team went unbeaten this month (no giant was killed)."""
    season, weeks, ms = ctx["season"], ctx["weeks"], ctx["month_stats"]
    if not ms:
        return None
    giant_rid = max(ms.values(), key=lambda m: m.total_points).roster_id
    for wk in weeks:
        wd = season.weeks.get(wk)
        if not wd:
            continue
        for ra, pa, rb, pb in S.matchup_pairs(wd):
            if giant_rid not in (ra, rb):
                continue
            winner = ra if pa > pb else rb
            if winner != giant_rid:
                giant_pts = pa if giant_rid == ra else pb
                return Award("Giant Killer", "Beat this month's highest-scoring team", "fame",
                             winner,
                             f"upset {season.team_name(giant_rid)} ({giant_pts:.1f} pts) in week {wk}", [])
    return None


MONTHLY_AWARDS = [
    m_manager_of_month, m_biggest_climber, m_month_efficiency,
    m_hot_hand, m_month_high, m_wheeler_dealer, m_waiver_warrior, m_faabulous,
    m_best_below_500, m_lineup_wizard, m_bench_hero, m_closest_survivor,
    m_most_improved, m_giant_killer,
    m_wooden_spoon, m_biggest_faller, m_month_bumbler, m_the_mark, m_waiver_washout,
]


def compute_monthly(season, month_stats, prev_month_stats=None):
    weeks = next(iter(month_stats.values())).weeks if month_stats else []
    ctx = {
        "season": season,
        "month_stats": month_stats,
        "weeks": weeks,
        "prev_month_stats": prev_month_stats,
        "trade_counts": W.trade_count_by_team(season, weeks),
        "trade_values": W.trade_value_by_team(season, weeks),
        "waiver_points": W.waiver_points_by_team(season, weeks),
        "faab_totals": W.faab_spent_by_team(season, weeks),
    }
    results = []
    for fn in MONTHLY_AWARDS:
        try:
            a = fn(ctx)
            if a:
                results.append(a)
        except Exception as e:
            print(f"  [monthly award {fn.__name__} skipped: {e}]")
    return ctx, results
