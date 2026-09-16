"""
render.py — Turn computed data + awards (+ roasts) into a report.

Two renderers:
  render_text()  — clean terminal/markdown-ish output (MVP, email body)
  render_html()  — styled, screenshot-friendly dossier page (the shareable asset)

Both consume the same inputs so you can offer text now and HTML later.
"""

from __future__ import annotations
import html
import json
import statistics
import textwrap

from . import stats as S
from . import waivers as W
from . import manager as MG
from . import images as IMG


# ----------------------------------------------------------------------
# Caption copy — the ONE place to edit explainer wording for tables and
# charts (not award cards — those are already explained by their title +
# stat line + roast, and a generic caption there would just be noise).
# Each caption is a single short sentence, styled as small muted text
# subordinate to its section's header — see the .caption CSS rule.
# ----------------------------------------------------------------------

CAPTIONS = {
    "power_ranking":
        "Blends weekly scoring, all-play record, recent form, and lineup efficiency — "
        "not the same as the standings, which only count wins and losses.",
    "luck_index_weekly":
        "Your record if you'd played every other team this week. Strips out schedule "
        "luck to show who actually performed.",
    "luck_index_to_date":
        "All-play record across the whole season so far — the closest thing to a "
        "\"true\" standings, ignoring who you happened to be scheduled against.",
    "median_whatif_weekly":
        "Did you beat the league's median score this week? A win here means you'd "
        "have won against half the league.",
    "median_whatif_to_date":
        "How many weeks you've beaten the league median — a schedule-proof measure "
        "of consistency.",
    "rivalry_watch":
        "Head-to-head history between this week's opponents, including current streaks.",
    "pythagorean":
        "Expected wins from your own points for and against. Ahead of it means you've "
        "converted your scoring into more wins than it should have bought; behind it "
        "means the scoreboard owes you.",
    "consistency":
        "How much your weekly score swings, as a % of your average — so it's fair "
        "between high and low scorers. Low = reliable floor, high = boom-or-bust.",
    "optimal_record":
        "The record everyone would have if BOTH sides started their best possible "
        "lineup every week. The gap is what your bench decisions actually cost — or "
        "saved — you.",
    "clutch":
        "Record in games decided by under 5 points. Small samples, big arguments.",
    "waiver_roi":
        "Fantasy points per FAAB dollar, counting only pickups you actually paid for. "
        "Free-agent adds are listed separately since you can't overpay for free.",
    "decision_value_chart":
        "Points left on the bench, added up week by week. Flat is perfect; every "
        "step down is a start/sit call that cost you. The slope matters more than "
        "the number — a steady bleed is a habit, one cliff is a bad Sunday.",
    "schedule_swap_chart":
        "Every team's scores replayed against every other team's schedule. Read across "
        "your row to see the record you'd have had with their fixtures. The dark "
        "diagonal is what actually happened.",
    "timing_chart":
        "For each pickup: points per game in the 3 weeks before you added them vs. the "
        "3 weeks after. Below the line means you bought production that had already "
        "happened; above it means you got there first.",
    "fingerprint_chart":
        "Five-axis manager profile, scaled against your own league — so every axis "
        "always has a 0 and a 100. It describes how someone plays, not how well.",
    "draft_slope_chart":
        "Where a player was drafted vs. where they finished in scoring. Bars to the "
        "right beat their draft slot; bars to the left were reaches.",
    "luck_chart_season":
        "Points scored vs. luck — top-right got good results and earned them; "
        "bottom-right scored well but got unlucky.",
    "luck_chart_weekly":
        "Each dot is one team this week — further right means more of the league "
        "would have lost to that score; green means above this week's median, red "
        "means below.",
    "month_delta_chart":
        "Each team's points above or below the league average this month — a quick "
        "read on who's running hot or cold.",
    "avg_delta_chart":
        "Each team's score compared to the week's average — a quick read on who ran "
        "hot or cold this week.",
    "efficiency_chart":
        "How close each manager came to their optimal lineup — low bars mean points "
        "left on the bench.",
    "draft_value":
        "Production vs. draft slot — a late pick who outscored their slot is a steal, "
        "an early pick who didn't is a bust. Scoped to this league's own draft; keeper "
        "picks aren't a real ADP signal.",
}


def _caption_html(key: str) -> str:
    text = CAPTIONS.get(key, "")
    return f'<p class="caption">{html.escape(text)}</p>' if text else ""


def _caption_lines(key: str, width: int = 70) -> list:
    text = CAPTIONS.get(key, "")
    return [f"  {ln}" for ln in textwrap.wrap(text, width=width)] if text else []


# ----------------------------------------------------------------------
# Shared: standings, sparklines, closest race
# ----------------------------------------------------------------------

def standings_rows(season, upto_week=None):
    """upto_week=None uses season.teams' live totals (correct for an
    in-progress or just-finished season). Pass a week number to freeze
    standings at that point — what a retrospective monthly report needs,
    so it doesn't leak later weeks' results into an earlier month's table."""
    rows = []
    if upto_week is None:
        for rid, t in season.teams.items():
            rows.append({
                "rid": rid, "team": t.team_name, "manager": t.manager,
                "w": t.wins, "l": t.losses, "ties": t.ties,
                "pf": t.points_for, "pa": t.points_against,
            })
    else:
        cum = S.standings_through(season, upto_week)
        for rid, t in season.teams.items():
            c = cum.get(rid, {"wins": 0, "losses": 0, "ties": 0, "pf": 0.0, "pa": 0.0})
            rows.append({
                "rid": rid, "team": t.team_name, "manager": t.manager,
                "w": c["wins"], "l": c["losses"], "ties": c["ties"],
                "pf": round(c["pf"], 2), "pa": round(c["pa"], 2),
            })
    rows.sort(key=lambda r: (r["w"], r["pf"]), reverse=True)
    return rows


_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list) -> str:
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = hi - lo
    if span == 0:
        return _SPARK_CHARS[4] * len(values)
    return "".join(_SPARK_CHARS[min(7, int((v - lo) / span * 7))] for v in values)


def _relevant_weeks(season, month_stats) -> list:
    if month_stats:
        any_team = next(iter(month_stats.values()), None)
        return any_team.weeks if any_team else []
    return sorted(season.weeks.keys())


def _format_trade(season, trade) -> str:
    parts = []
    for pid, to_rid in trade.adds.items():
        from_rid = trade.drops.get(pid)
        name = S.D.player_name(season.players, pid)
        if from_rid is not None:
            parts.append(f"{season.team_name(from_rid)} -> {season.team_name(to_rid)}: {name}")
        else:
            parts.append(f"{season.team_name(to_rid)} added {name}")
    if trade.draft_picks:
        parts.append(f"+{trade.draft_picks} draft pick(s) involved")
    return "; ".join(parts) if parts else "details unavailable"


def _closest_game(season, weeks: list):
    return S.closest_game(season, weeks)


# ----------------------------------------------------------------------
# TEXT
# ----------------------------------------------------------------------

def render_text(season, awards, roasts, period_label, season_stats=None,
                kind="monthly", month_stats=None, recap="", waiver_take="",
                playoff_odds=None, pickup_odds_swing=None):
    L = []
    header = f"{season.name} — {period_label}"
    L.append("=" * 64)
    L.append(f"  {header}")
    L.append("=" * 64)

    if recap:
        L.append("")
        L.extend(f"  {ln}" for ln in textwrap.wrap(recap, width=70))

    fame = [a for a in awards if a.hall == "fame"]
    shame = [a for a in awards if a.hall == "shame"]

    def block(title, items):
        L.append("\n" + "-" * 64)
        L.append(f"  {title}")
        L.append("-" * 64)
        for a in items:
            team = season.team_name(a.winner_rid)
            L.append(f'\n  "{a.title}" — {a.flavour}')
            L.append(f"    {team}: {a.headline}")
            line = roasts.get(a.title)
            if line:
                L.append(f"    “{line}”")
            for rid, val in a.podium:
                L.append(f"      · {season.team_name(rid)}: {val}")

    block("HALL OF FAME", fame)
    block("HALL OF SHAME", shame)

    # Monthly recap: who climbed / fell this month — this IS the month-scoped standing
    if month_stats:
        L.append("\n" + "-" * 64)
        L.append("  THE MONTH IN REVIEW  (this month's standing, not season-to-date)")
        L.append("-" * 64)
        L.append(f"  {'Team':<24}{'Rec':<8}{'+/- Avg':>9}{'Rank':>9}{'Eff':>7}")
        ranked = sorted(month_stats.values(), key=lambda m: m.pts_above_avg, reverse=True)
        for m in ranked:
            wl = f"{m.h2h_w}-{m.h2h_l}"
            rank = f"{m.rank_start}->{m.rank_end}" if m.rank_start else f"-> {m.rank_end}"
            L.append(f"  {season.team_name(m.roster_id)[:23]:<24}{wl:<8}"
                     f"{m.pts_above_avg:>+9.1f}{rank:>9}{m.avg_efficiency:>6.0f}%")

    # Standings: frozen at this period's last played week, not today's live
    # totals — a retrospective monthly report shouldn't show later weeks' results.
    weeks = _relevant_weeks(season, month_stats)
    upto_week = max(weeks) if month_stats else None
    standings_label = "OVERALL STANDINGS  (final season)" if kind == "season" \
        else "OVERALL STANDINGS  (through end of this month)"
    L.append("\n" + "-" * 64)
    L.append(f"  {standings_label}")
    L.append("-" * 64)
    L.append(f"  {'#':<3}{'Team':<24}{'W-L':<8}{'PF':>8}{'PA':>8}")
    for i, r in enumerate(standings_rows(season, upto_week), 1):
        wl = f"{r['w']}-{r['l']}" + (f"-{r['ties']}" if r['ties'] else "")
        L.append(f"  {i:<3}{r['team'][:23]:<24}{wl:<8}{r['pf']:>8.1f}{r['pa']:>8.1f}")

    # Monte Carlo playoff odds — Monte Carlo projection of the rest of the
    # regular season, bootstrapped from each team's own scoring so far.
    if playoff_odds:
        L.append("\n" + "-" * 64)
        L.append("  PLAYOFF ODDS  (Monte Carlo, rest of regular season)")
        L.append("-" * 64)
        ranked_odds = sorted(playoff_odds.items(), key=lambda kv: kv[1], reverse=True)
        for rid, odds in ranked_odds:
            L.append(f"  {season.team_name(rid)[:23]:<24}{odds * 100:>6.1f}%")

    # Team form (weekly score sparkline across the period)
    if weeks:
        L.append("\n" + "-" * 64)
        L.append("  TEAM FORM  (weekly scores, low -> high)")
        L.append("-" * 64)
        for r in standings_rows(season, upto_week):
            rid = r["rid"]
            scores = [season.weeks[wk][rid].points for wk in weeks if rid in season.weeks.get(wk, {})]
            L.append(f"  {r['team'][:23]:<24}{_sparkline(scores)}")

        cg = _closest_game(season, weeks)
        if cg:
            margin, wk, ra, pa, rb, pb = cg
            winner, loser = (ra, rb) if pa > pb else (rb, ra)
            hi, lo = max(pa, pb), min(pa, pb)
            L.append("\n" + "-" * 64)
            L.append("  CLOSEST RACE")
            L.append("-" * 64)
            L.append(f"  Week {wk}: {season.team_name(winner)} beat {season.team_name(loser)}"
                     f" by just {margin:.1f} ({hi:.1f}-{lo:.1f})")

    # Waiver wire & trades across the period
    if weeks:
        best = W.best_pickup_period(season, weeks)
        worst = W.worst_faab_period(season, weeks)
        faab_totals = W.faab_spent_by_team(season, weeks)
        trades = W.trades_in(season, weeks)
        if best or worst or faab_totals or trades or waiver_take:
            L.append("\n" + "-" * 64)
            L.append("  WAIVER WIRE & TRADES")
            L.append("-" * 64)
            if best:
                spend = f"${best.faab}" if best.faab else "free"
                L.append(f"  Best pickup: {season.team_name(best.roster_id)} added {best.player_name}"
                         f" ({spend}) -> {best.points_since:.1f} pts since")
            if worst:
                L.append(f"  Worst FAAB spend: {season.team_name(worst.roster_id)} paid ${worst.faab}"
                         f" for {worst.player_name} -> {worst.points_since:.1f} pts since")
            if faab_totals:
                top = sorted(faab_totals.items(), key=lambda kv: kv[1], reverse=True)[:3]
                spend_str = ", ".join(f"{season.team_name(rid)} ${amt}" for rid, amt in top)
                L.append(f"  Top FAAB spend: {spend_str}")
            por = W.best_por_period(season, weeks)
            if por:
                swing = f", {pickup_odds_swing:+.1f}% playoff odds" if pickup_odds_swing is not None else ""
                L.append(f"  Best value pickup (pts over replacement): {season.team_name(por.roster_id)}"
                         f" added {por.player_name} ({por.position}) -> +{por.por:.1f} pts over a"
                         f" replacement-level {por.position} over {por.games} games{swing}")
            sharpe = W.sharpe_by_position(season, weeks)
            if sharpe:
                top_pos, s = max(sharpe.items(), key=lambda kv: kv[1]["avg_sharpe"])
                L.append(f"  Most reliable position off the wire: {top_pos}"
                         f" (avg Sharpe {s['avg_sharpe']:.2f} across {s['n']} pickups,"
                         f" best: {s['best'].player_name} at {s['best'].sharpe:.2f})")
            if trades:
                L.append(f"\n  Trades ({len(trades)}):")
                for t in trades:
                    L.append(f"    Week {t.week}: {_format_trade(season, t)}")
            if waiver_take:
                L.append(f'\n  "{waiver_take}"')

    # Luck index — explicitly labeled "season to date" since this is the
    # only place season-long luck lives now that the weekly report stays
    # this-week-only: a reader should know at a glance this isn't scoped
    # to just this month. Actual record alongside all-play, plus the
    # luck_index summary number (actual - deserved wins).
    if season_stats:
        record_by_rid = {r["rid"]: f"{r['w']}-{r['l']}" + (f"-{r['ties']}" if r['ties'] else "")
                         for r in standings_rows(season, upto_week)}
        L.append("\n" + "-" * 64)
        L.append("  LUCK INDEX — SEASON TO DATE  (actual record vs all-play record; "
                 "luck = actual - deserved wins)")
        L.append("-" * 64)
        L.extend(_caption_lines("luck_index_to_date"))
        ranked = sorted(season_stats.values(), key=lambda s: s.luck_index, reverse=True)
        for s in ranked:
            tag = "lucky" if s.luck_index > 0 else ("robbed" if s.luck_index < 0 else "fair")
            L.append(f"  {season.team_name(s.roster_id)[:23]:<24}"
                     f"{record_by_rid.get(s.roster_id, '—'):<8}"
                     f"{s.luck_index:+6.1f}  ({tag}; all-play {s.all_play_w}-{s.all_play_l}, "
                     f"{s.avg_efficiency:.0f}% eff)")

    # Season-wide power ranking, distinct from the standings above (same
    # formula as the weekly report, run through the season's last week).
    # Season-only, as before — not extended to monthly.
    if kind == "season" and weeks:
        last_week = max(weeks)
        pr = S.power_rank(season, last_week)
        if pr:
            L.append("\n" + "-" * 64)
            L.append("  SEASON POWER RANKING")
            L.append("-" * 64)
            L.extend(_caption_lines("power_ranking"))
            L.append(f"  {'#':<3}{'Team':<24}{'Score':>7}{'AllPlay':>9}{'Form':>7}{'Eff':>6}{'vsStd':>7}")
            std_order = [r["rid"] for r in standings_rows(season, last_week)]
            std_rank = {rid: i + 1 for i, rid in enumerate(std_order)}
            ranked = sorted(pr.items(), key=lambda kv: kv[1]["score"], reverse=True)
            for i, (rid, p) in enumerate(ranked, 1):
                delta = std_rank.get(rid, i) - i
                delta_str = f"{delta:+d}" if delta else "-"
                L.append(f"  {i:<3}{season.team_name(rid)[:23]:<24}{p['score']:>7.1f}"
                         f"{p['all_play_pct']:>8.0f}%{p['form_pct']:>6.0f}%{p['efficiency_pct']:>5.0f}%{delta_str:>7}")

    # Draft value: production vs. draft slot, season-only (wants a full
    # season of scoring to mean anything). No-op if this league has no
    # recorded draft.
    if kind == "season":
        board = S.draft_value_board(season)
        if board["values"] or board["busts"]:
            L.append("\n" + "-" * 64)
            L.append("  DRAFT VALUE: BIGGEST STEALS & BUSTS")
            L.append("-" * 64)
            L.extend(_caption_lines("draft_value"))
            for r in board["values"]:
                L.append(f"  STEAL  {r['player_name'][:20]:<21}pick {r['pick_no']:>3}"
                         f"  {r['points']:>6.1f} pts  ({season.team_name(r['roster_id'])[:18]})")
            for r in board["busts"]:
                L.append(f"  BUST   {r['player_name'][:20]:<21}pick {r['pick_no']:>3}"
                         f"  {r['points']:>6.1f} pts  ({season.team_name(r['roster_id'])[:18]})")

    # Median what-if to date: weeks beating the league's weekly median
    # through this period's last week — schedule-blind like all-play, but
    # benchmarked against the middle of the pack each week. Monthly's
    # last_week is that month's last week (so this reads "season to date
    # through end of this month"); season's is the season's last week.
    if kind in ("season", "monthly") and weeks:
        last_week = max(weeks)
        mr = S.median_record(season, upto_week=last_week)
        if mr:
            label = "MEDIAN WHAT-IF TO DATE  (weeks beating the league median each week, season to date)"
            L.append("\n" + "-" * 64)
            L.append(f"  {label}")
            L.append("-" * 64)
            L.extend(_caption_lines("median_whatif_to_date"))
            ranked_mr = sorted(mr.items(), key=lambda kv: kv[1]["above"], reverse=True)
            for rid, c in ranked_mr:
                record = f"{c['above']}-{c['below']}" + (f"-{c['tied']}" if c["tied"] else "")
                L.append(f"  {season.team_name(rid)[:23]:<24}{record}")

    L.append("\n" + "=" * 64)
    return "\n".join(L)


def _luck_tag(actual_result: str, all_play_rate: float) -> str:
    if actual_result == "W" and all_play_rate < 0.5:
        return "lucky"
    if actual_result == "L" and all_play_rate > 0.5:
        return "robbed"
    return "fair"


def _week_results(pairs) -> dict:
    """roster_id -> 'W'/'L'/'T' for one week's matchup_pairs()."""
    out = {}
    for ra, pa, rb, pb in pairs:
        if pa > pb:
            out[ra], out[rb] = "W", "L"
        elif pb > pa:
            out[ra], out[rb] = "L", "W"
        else:
            out[ra], out[rb] = "T", "T"
    return out


def render_weekly_text(season, awards, roasts, period_label, week, rivalry_matchups=None, recap="") -> str:
    L = []
    header = f"{season.name} — {period_label}"
    L.append("=" * 64)
    L.append(f"  {header}")
    L.append("=" * 64)

    if recap:
        L.append("")
        L.extend(f"  {ln}" for ln in textwrap.wrap(recap, width=70))

    fame = [a for a in awards if a.hall == "fame"]
    shame = [a for a in awards if a.hall == "shame"]

    def block(title, items):
        L.append("\n" + "-" * 64)
        L.append(f"  {title}")
        L.append("-" * 64)
        for a in items:
            team = season.team_name(a.winner_rid)
            L.append(f'\n  "{a.title}" — {a.flavour}')
            L.append(f"    {team}: {a.headline}")
            line = roasts.get(a.title)
            if line:
                L.append(f"    “{line}”")
            for rid, val in a.podium:
                L.append(f"      · {season.team_name(rid)}: {val}")

    block("HALL OF FAME", fame)
    block("HALL OF SHAME", shame)

    wd = season.weeks.get(week, {})
    scores = S.weekly_scores(wd)
    pairs = S.matchup_pairs(wd)

    if not pairs:
        L.append("\n" + "-" * 64)
        L.append("  NOTE: no head-to-head matchups recorded for this week")
        L.append("-" * 64)
        L.append("  (likely outside the regular-season bracket). Power ranking and median")
        L.append("  what-if below are still accurate; matchup-dependent awards, the luck")
        L.append("  index's actual result, this week's extremes, and rivalry watch are not.")

    # Power ranking — deliberately distinct from the standings (see
    # stats.power_rank's docstring for the formula). "vs Std" is the part
    # meant to spark debate.
    pr = S.power_rank(season, week)
    if pr:
        L.append("\n" + "-" * 64)
        L.append("  POWER RANKING")
        L.append("-" * 64)
        L.extend(_caption_lines("power_ranking"))
        L.append(f"  {'#':<3}{'Team':<24}{'Score':>7}{'AllPlay':>9}{'Form':>7}{'Eff':>6}{'vsStd':>7}")
        std_order = [r["rid"] for r in standings_rows(season, week)]
        std_rank = {rid: i + 1 for i, rid in enumerate(std_order)}
        ranked = sorted(pr.items(), key=lambda kv: kv[1]["score"], reverse=True)
        for i, (rid, p) in enumerate(ranked, 1):
            delta = std_rank.get(rid, i) - i
            delta_str = f"{delta:+d}" if delta else "-"
            L.append(f"  {i:<3}{season.team_name(rid)[:23]:<24}{p['score']:>7.1f}"
                     f"{p['all_play_pct']:>8.0f}%{p['form_pct']:>6.0f}%{p['efficiency_pct']:>5.0f}%{delta_str:>7}")

    # Luck index: this week's all-play record alongside the actual result.
    ap = S.all_play(wd)
    if ap:
        results = _week_results(pairs)
        L.append("\n" + "-" * 64)
        L.append("  LUCK INDEX  (all-play record if you'd played everyone this week)")
        L.append("-" * 64)
        L.extend(_caption_lines("luck_index_weekly"))
        for rid in sorted(ap, key=lambda r: scores.get(r, 0), reverse=True):
            w, l, _t = ap[rid]
            actual = results.get(rid, "—")
            rate = w / (w + l) if (w + l) else 0.5
            tag = _luck_tag(actual, rate)
            L.append(f"  {season.team_name(rid)[:23]:<24}{scores.get(rid, 0):>7.1f} pts"
                     f"   actual: {actual}   all-play: {w}-{l}  ({tag})")

    # Median what-if
    median = S.weekly_median(wd)
    if scores:
        L.append("\n" + "-" * 64)
        L.append(f"  MEDIAN WHAT-IF  (league median this week: {median:.1f})")
        L.append("-" * 64)
        L.extend(_caption_lines("median_whatif_weekly"))
        for rid, pts in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
            tag = "beat the median" if pts > median else ("missed the median" if pts < median else "tied the median")
            L.append(f"  {season.team_name(rid)[:23]:<24}{pts:>7.1f}  ({tag})")

    # This week's extremes
    cg = S.closest_game(season, [week])
    bg = S.blowout_game(season, [week])
    if cg or bg:
        L.append("\n" + "-" * 64)
        L.append("  THIS WEEK'S EXTREMES")
        L.append("-" * 64)
        if cg:
            margin, wk, ra, pa, rb, pb = cg
            winner, loser = (ra, rb) if pa > pb else (rb, ra)
            hi, lo = max(pa, pb), min(pa, pb)
            L.append(f"  Closest: {season.team_name(winner)} beat {season.team_name(loser)}"
                     f" by just {margin:.1f} ({hi:.1f}-{lo:.1f})")
        if bg:
            margin, wk, ra, pa, rb, pb = bg
            winner, loser = (ra, rb) if pa > pb else (rb, ra)
            hi, lo = max(pa, pb), min(pa, pb)
            L.append(f"  Blowout: {season.team_name(winner)} beat {season.team_name(loser)}"
                     f" by {margin:.1f} ({hi:.1f}-{lo:.1f})")

    if rivalry_matchups:
        L.append("\n" + "-" * 64)
        L.append("  RIVALRY WATCH")
        L.append("-" * 64)
        L.extend(_caption_lines("rivalry_watch"))
        for m in rivalry_matchups:
            if m["pa"] == m["pb"]:
                L.append(f"\n  {m['name_a']} and {m['name_b']} tied at {m['pa']:.1f}")
            else:
                winner, loser = (m["name_a"], m["name_b"]) if m["pa"] > m["pb"] else (m["name_b"], m["name_a"])
                hi, lo = max(m["pa"], m["pb"]), min(m["pa"], m["pb"])
                L.append(f"\n  {winner} ({hi:.1f}) beat {loser} ({lo:.1f})")
            L.append(f"    {m['line']}")

    # Detect teams that have scores but no recorded matchup pairing — common
    # during playoff weeks where Sleeper doesn't assign matchup_ids to
    # consolation-bracket games. Scores still count for power ranking and
    # luck index; the H2H pairing just can't be determined from the API data.
    paired_rids = {rid for m in (rivalry_matchups or []) for rid in (m["ra"], m["rb"])}
    unmatched = [season.team_name(rid) for rid in sorted(wd.keys()) if rid not in paired_rids]
    if unmatched:
        L.append("\n" + "-" * 64)
        L.append("  NOTE: no matchup recorded for " + ", ".join(unmatched))
        L.append("-" * 64)
        L.append("  (Sleeper doesn't assign matchup IDs to consolation/non-bracket games;")
        L.append("   scores count for ranking and luck index but H2H pairing is unavailable.)")

    L.append("\n" + "=" * 64)
    return "\n".join(L)


# ----------------------------------------------------------------------
# HTML (screenshot-friendly)
# ----------------------------------------------------------------------

_CSS = """
:root{--navy:#15243b;--green:#19c37d;--red:#c0392b;--ink:#0d1626;--paper:#f7f9fc;}
*{box-sizing:border-box;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;}
body{margin:0;background:var(--paper);color:var(--ink);}
.wrap{max-width:1100px;margin:0 auto;padding:28px;}
.cover{background:var(--navy);color:#fff;border-radius:18px;padding:38px 40px;margin-bottom:24px;}
.cover h1{margin:0;font-size:40px;font-style:italic;letter-spacing:-1px;}
.cover .sub{opacity:.8;margin-top:6px;font-size:18px;font-weight:600;}
.cover .meta{margin-top:18px;font-size:14px;opacity:.85;line-height:1.7;font-family:ui-monospace,Menlo,monospace;}
.recap{background:#fff;border:1px solid #e3e8f0;border-radius:14px;padding:18px 22px;
       margin-bottom:20px;font-size:15px;line-height:1.6;color:#26344d;}
.closest{background:#fff;border-left:4px solid var(--green);border-radius:10px;
        padding:12px 18px;margin:10px 0 20px;font-size:14px;}
.take{font-style:italic;color:#26344d;border-left:3px solid var(--green);padding-left:10px;margin:10px 0;}
.bar{display:flex;align-items:center;gap:10px;color:#fff;padding:12px 18px;border-radius:12px;font-weight:700;font-size:18px;margin:26px 0 14px;}
.bar.fame{background:var(--navy);} .bar.shame{background:var(--red);}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:14px;}
.card{background:#fff;border:1px solid #e3e8f0;border-radius:14px;overflow:hidden;}
.card .head{padding:10px 14px;font-weight:700;font-size:13px;color:#fff;display:flex;justify-content:space-between;}
.card.fame .head{background:var(--navy);} .card.shame .head{background:var(--red);}
.card .body{padding:14px;}
.card .team{font-size:21px;font-weight:800;font-style:italic;}
.card .val{color:#5b6b85;font-size:13px;margin-top:2px;}
.avatar-row{display:flex;align-items:center;gap:8px;margin-bottom:4px;}
.avatar{width:34px;height:34px;object-fit:cover;flex-shrink:0;background:#eef2f7;}
.avatar.manager{border-radius:50%;}
.avatar.player{border-radius:8px;}
.avatar.sm{width:22px;height:22px;}
.trade-swap{font-size:15px;color:#9aa7bd;flex-shrink:0;}
.card .roast{margin-top:10px;font-size:14px;line-height:1.5;border-left:3px solid var(--green);padding-left:10px;color:#26344d;}
.podium{margin-top:10px;font-size:12px;color:#7a8aa3;}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;margin-top:8px;font-size:14px;}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid #eef2f7;}
th{background:var(--navy);color:#fff;font-size:12px;text-transform:uppercase;letter-spacing:.04em;}
td.num{text-align:right;font-variant-numeric:tabular-nums;}
td.spark{font-family:ui-monospace,Menlo,monospace;font-size:16px;letter-spacing:1px;white-space:nowrap;}
.lucky{color:var(--green);font-weight:700;} .robbed{color:var(--red);font-weight:700;}
h2{margin:30px 0 6px;font-size:15px;text-transform:uppercase;letter-spacing:.06em;color:#41506b;}
p.caption{font-size:12px;color:#7a8aa3;margin:0 0 10px;}
p{font-size:14px;line-height:1.5;margin:6px 0;}
ul{margin:4px 0 12px;padding-left:20px;font-size:14px;line-height:1.6;}
.charts-grid{display:flex;flex-direction:column;gap:20px;margin:14px 0 24px;}
.chart-box{background:#fff;border:1px solid #e3e8f0;border-radius:14px;padding:14px 16px;
          height:420px;position:relative;}
.chart-box h3{margin:0 0 4px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:#41506b;}
.chart-box p.caption{margin:0 0 8px;}
.chart-box .canvas-wrap{position:relative;height:330px;}
.chart-box.tall{height:520px;}
.chart-box.tall .canvas-wrap{height:430px;}
.notice{background:#fff;border-left:4px solid #9aa7bd;border-radius:10px;
       padding:12px 18px;margin:10px 0 20px;font-size:13px;color:#5b6b85;}
.rivalry-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;margin:10px 0 20px;}
.rivalry-card{background:#fff;border:1px solid #e3e8f0;border-left:4px solid var(--navy);
             border-radius:10px;padding:12px 16px;}
.rivalry-card .matchup{font-size:15px;font-weight:700;}
.rivalry-card .matchup .winner{color:var(--green);}
.rivalry-card .history{margin-top:6px;font-size:13px;color:#5b6b85;}
@media screen{.print-only{display:none;}}
@media print{
  body{-webkit-print-color-adjust:exact;print-color-adjust:exact;}
  .wrap{max-width:100%;padding:16px 20px;}
  .page-start{break-before:page;page-break-before:always;}
  .card{break-inside:avoid;page-break-inside:avoid;}
  .chart-box{break-inside:avoid;page-break-inside:avoid;}
  .rivalry-card{break-inside:avoid;page-break-inside:avoid;}
  .grid{grid-template-columns:repeat(auto-fill,minmax(240px,1fr));}
  .screen-only{display:none;}
  .print-only{display:block;}
  .pdf-table{width:100%;border-collapse:collapse;background:#fff;font-size:11px;margin-top:8px;}
  .pdf-table th{background:var(--navy);color:#fff;padding:6px 8px;text-align:left;
                font-size:10px;text-transform:uppercase;letter-spacing:.04em;}
  .pdf-table td{padding:5px 8px;border-bottom:1px solid #eef2f7;vertical-align:middle;}
  .pdf-table td.num{text-align:right;font-variant-numeric:tabular-nums;}
  .pdf-table .rank-col{font-weight:700;color:#41506b;text-align:center;}
  .pdf-table tr:nth-child(even){background:#f9fafb;}
  .pdf-section-head{font-size:11px;font-weight:700;text-transform:uppercase;
                    letter-spacing:.06em;color:#41506b;margin:16px 0 4px;}
}
"""


# ----------------------------------------------------------------------
# Charts (Chart.js, generic spec-driven engine shared by weekly and
# monthly/season — one JS template reads a list of chart specs rather
# than hardcoding named charts, so the same engine serves both reports
# without duplicating the whole JS block per report kind.
# ----------------------------------------------------------------------

_GENERIC_CHARTS_JS = """
Chart.register(ChartDataLabels);
(function() {
  const NAVY = '#15243b', GREEN = '#19c37d', RED = '#c0392b', GRAY = '#9aa7bd';
  const PALETTE = ['#15243b','#19c37d','#c0392b','#2e86c1','#e67e22','#8e44ad',
                   '#16a085','#d4ac0d','#5d6d7e','#cb4335','#1abc9c','#7d3c98'];
  const shortLabel = (name) => name.length > 16 ? name.slice(0, 15) + '…' : name;

  (DOSSIER_DATA.scatterCharts || []).forEach(function(cfg) {
    // Spread label rows: sort by x so neighbours alternate top/bottom
    // instead of stacking straight up when teams cluster on screen.
    const points = cfg.points.slice().sort((a, b) => a.x - b.x);
    const datasets = [];
    if (cfg.withDiagonal) {
      datasets.push({ type: 'line', data: [{x: cfg.xMin, y: cfg.yMin}, {x: cfg.xMax, y: cfg.yMax}],
        borderColor: GRAY, borderDash: [6, 4], pointRadius: 0, fill: false, order: 2,
        datalabels: { display: false } });
    }
    datasets.push({ type: 'scatter', data: points, order: 1, pointRadius: 6,
      backgroundColor: points.map(p => p.good === null ? NAVY : (p.good ? GREEN : RED)) });
    new Chart(document.getElementById(cfg.id), {
      type: 'scatter',
      data: { datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        layout: { padding: { top: 24, bottom: 24 } },
        plugins: {
          legend: { display: false },
          datalabels: {
            align: (ctx) => ctx.dataIndex % 2 === 0 ? 'top' : 'bottom',
            offset: 8, color: NAVY, font: { size: 11, weight: 600 },
            formatter: (v) => v.label ? shortLabel(v.label) : ''
          },
          tooltip: { callbacks: { label: (c) => c.raw.tooltip || '' } }
        },
        scales: {
          x: { type: 'linear', title: { display: true, text: cfg.xLabel }, min: cfg.xMin, max: cfg.xMax },
          y: { type: 'linear', title: { display: true, text: cfg.yLabel }, min: cfg.yMin, max: cfg.yMax }
        }
      }
    });
  });

  (DOSSIER_DATA.barCharts || []).forEach(function(cfg) {
    const sorted = cfg.bars.slice().sort((a, b) => b.value - a.value);
    new Chart(document.getElementById(cfg.id), {
      type: 'bar',
      data: {
        labels: sorted.map(b => b.label),
        datasets: [{ data: sorted.map(b => b.value),
          backgroundColor: cfg.colorBySign ? sorted.map(b => b.value >= 0 ? GREEN : RED) : NAVY }]
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, datalabels: { display: false } },
        scales: { x: { title: { display: true, text: cfg.xLabel }, min: cfg.xMin, max: cfg.xMax } }
      }
    });
  });

  (DOSSIER_DATA.lineCharts || []).forEach(function(cfg) {
    // One line per manager. Deliberately no datalabels: twelve labelled
    // series is unreadable, so the legend carries identity instead.
    new Chart(document.getElementById(cfg.id), {
      type: 'line',
      data: {
        labels: cfg.labels,
        datasets: cfg.series.map(function(s, i) {
          return { label: s.label, data: s.data, borderColor: PALETTE[i % PALETTE.length],
                   backgroundColor: PALETTE[i % PALETTE.length], borderWidth: s.emphasis ? 3 : 1.5,
                   pointRadius: 0, pointHoverRadius: 4, tension: 0.25, fill: false };
        })
      },
      options: {
        responsive: true, maintainAspectRatio: false, interaction: { mode: 'nearest', intersect: false },
        plugins: {
          legend: { display: true, position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } },
          datalabels: { display: false },
          tooltip: { callbacks: { label: (c) => c.dataset.label + ': ' + c.parsed.y.toFixed(1) } }
        },
        scales: {
          x: { title: { display: true, text: cfg.xLabel } },
          y: { title: { display: true, text: cfg.yLabel } }
        }
      }
    });
  });

  (DOSSIER_DATA.matrixCharts || []).forEach(function(cfg) {
    // Heatmap via the matrix controller. Colour runs red (few wins) through
    // to green (many), scaled to the grid's own min/max so the contrast is
    // always usable regardless of how many weeks have been played.
    const vals = cfg.cells.map(c => c.v);
    const lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    const span = (hi - lo) || 1;
    new Chart(document.getElementById(cfg.id), {
      type: 'matrix',
      data: {
        datasets: [{
          data: cfg.cells,
          backgroundColor: function(ctx) {
            const c = ctx.dataset.data[ctx.dataIndex];
            if (!c) return '#fff';
            const t = (c.v - lo) / span;
            if (c.x === c.y) return 'rgba(21,36,59,0.92)';
            return 'rgba(' + Math.round(192 - 167 * t) + ',' + Math.round(57 + 138 * t) + ',' +
                   Math.round(43 + 82 * t) + ',0.82)';
          },
          borderColor: '#fff', borderWidth: 1,
          width: (ctx) => (ctx.chart.chartArea || {}).width / cfg.xLabels.length - 2,
          height: (ctx) => (ctx.chart.chartArea || {}).height / cfg.yLabels.length - 2
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          datalabels: {
            display: true, color: '#fff', font: { size: 9, weight: 700 },
            formatter: (v) => v.v
          },
          tooltip: { callbacks: { title: () => '', label: (c) => c.raw.tooltip || '' } }
        },
        scales: {
          x: { type: 'category', labels: cfg.xLabels, offset: true,
               title: { display: true, text: cfg.xLabel },
               ticks: { font: { size: 9 }, maxRotation: 90, minRotation: 60 }, grid: { display: false } },
          y: { type: 'category', labels: cfg.yLabels, offset: true, reverse: true,
               title: { display: true, text: cfg.yLabel },
               ticks: { font: { size: 9 } }, grid: { display: false } }
        }
      }
    });
  });

  (DOSSIER_DATA.radarCharts || []).forEach(function(cfg) {
    new Chart(document.getElementById(cfg.id), {
      type: 'radar',
      data: {
        labels: cfg.axes,
        datasets: [{ label: cfg.label, data: cfg.values,
          borderColor: NAVY, backgroundColor: 'rgba(21,36,59,0.18)',
          borderWidth: 2, pointRadius: 3, pointBackgroundColor: NAVY }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, datalabels: { display: false },
                   tooltip: { callbacks: { label: (c) => c.label + ': ' + c.parsed.r } } },
        scales: { r: { min: 0, max: 100, ticks: { display: false, stepSize: 25 },
                       pointLabels: { font: { size: 9 } } } }
      }
    });
  });
})();
"""


def _scatter_spec(chart_id, title, points, x_label, y_label, x_range, y_range,
                  with_diagonal=True, tall=False, caption_key=None) -> dict:
    return {
        "id": chart_id, "title": title, "tall": tall, "kind": "scatter",
        "xLabel": x_label, "yLabel": y_label,
        "xMin": x_range[0], "xMax": x_range[1], "yMin": y_range[0], "yMax": y_range[1],
        "withDiagonal": with_diagonal, "points": points,
        "caption": CAPTIONS.get(caption_key, ""),
    }


def _bar_spec(chart_id, title, bars, x_label, x_range=None, color_by_sign=False, tall=False,
              caption_key=None) -> dict:
    xr = x_range or (None, None)
    return {
        "id": chart_id, "title": title, "tall": tall, "kind": "bar",
        "xLabel": x_label, "xMin": xr[0], "xMax": xr[1],
        "colorBySign": color_by_sign, "bars": bars,
        "caption": CAPTIONS.get(caption_key, ""),
    }



def _line_spec(chart_id, title, labels, series, x_label, y_label,
               tall=False, caption_key=None) -> dict:
    """series: [{"label": str, "data": [float,...], "emphasis": bool}]."""
    return {
        "id": chart_id, "title": title, "tall": tall, "kind": "line",
        "labels": labels, "series": series,
        "xLabel": x_label, "yLabel": y_label,
        "caption": CAPTIONS.get(caption_key, ""),
    }


def _matrix_spec(chart_id, title, cells, x_labels, y_labels, x_label, y_label,
                 tall=False, caption_key=None) -> dict:
    """cells: [{"x": str, "y": str, "v": int, "tooltip": str}] — x/y are the
    category labels themselves, which is what the matrix controller indexes on."""
    return {
        "id": chart_id, "title": title, "tall": tall, "kind": "matrix",
        "cells": cells, "xLabels": x_labels, "yLabels": y_labels,
        "xLabel": x_label, "yLabel": y_label,
        "caption": CAPTIONS.get(caption_key, ""),
    }


def _radar_spec(chart_id, title, axes, values, label, caption_key=None) -> dict:
    return {
        "id": chart_id, "title": title, "tall": False, "kind": "radar",
        "axes": axes, "values": values, "label": label,
        "caption": CAPTIONS.get(caption_key, ""),
    }


def _manager_chart_specs(season, weeks: list, upto_week=None) -> list:
    """Monthly manager charts: schedule-swap heatmap, transaction-timing
    scatter, and a fingerprint radar for the month's standout manager.

    These are monthly rather than weekly on purpose — the matrix barely
    moves week to week once a season is underway, and transaction timing
    needs a few weeks of after-data before a pickup can be scored at all.
    """
    specs = []
    if not weeks:
        return specs
    last = upto_week if upto_week is not None else max(weeks)

    matrix = MG.schedule_swap_matrix(season, upto_week=last)
    if matrix and len(matrix) > 1:
        labels = [_short_name(season.team_name(rid)) for rid in sorted(matrix)]
        cells = []
        for rid in sorted(matrix):
            for sched_rid in sorted(matrix[rid]):
                v = matrix[rid][sched_rid]
                cells.append({
                    "y": _short_name(season.team_name(rid)),
                    "x": _short_name(season.team_name(sched_rid)),
                    "v": v["wins"],
                    "tooltip": (f"{season.team_name(rid)} on {season.team_name(sched_rid)}'s "
                                f"schedule: {v['wins']}-{v['losses']}"
                                + (f"-{v['ties']}" if v["ties"] else "")),
                })
        specs.append(_matrix_spec("scheduleSwapChart", "Schedule Swap: Wins Under Every Slate",
                                  cells, labels, labels,
                                  "…running this manager's schedule", "This team…",
                                  tall=True, caption_key="schedule_swap_chart"))

    timing = MG.transaction_timing(season, weeks, max_week=last)
    if timing:
        pts = []
        for t in timing:
            swing = t["after_ppg"] - t["before_ppg"]
            pts.append({
                "x": t["before_ppg"], "y": t["after_ppg"],
                "label": t["player_name"], "good": swing > 0,
                "tooltip": (f"{t['player_name']} — {season.team_name(t['roster_id'])} "
                            f"wk{t['week']}: {t['before_ppg']:.1f} before, "
                            f"{t['after_ppg']:.1f} after"),
            })
        hi = max([max(p["x"], p["y"]) for p in pts] + [1.0])
        cap = round(hi * 1.1, 1)
        specs.append(_scatter_spec("timingChart", "Pickup Timing: Before vs After",
                                   pts, "Pts/game in 3 weeks before add",
                                   "Pts/game in 3 weeks after add",
                                   (0, cap), (0, cap), with_diagonal=True,
                                   tall=True, caption_key="timing_chart"))

    fp = MG.manager_fingerprint(season, weeks, upto_week=last)
    if fp:
        cdv = MG.cumulative_decision_value(season, upto_week=last)
        if cdv:
            star = max(cdv.items(), key=lambda kv: kv[1]["total"])[0]
            if star in fp:
                specs.append(_radar_spec(
                    "fingerprintChart",
                    f"Manager Fingerprint: {season.team_name(star)}",
                    MG.FINGERPRINT_AXES,
                    [fp[star][a] for a in MG.FINGERPRINT_AXES],
                    season.team_name(star), caption_key="fingerprint_chart"))
    return specs


def _draft_chart_specs(season) -> list:
    """Season-review chart: draft slot vs. finishing scoring rank.

    Season-scoped by nature — draft capital is fixed in August and only
    becomes judgeable once there is a full season to judge it against.
    """
    rows = MG.draft_slope(season, top_n=12)
    if not rows:
        return []
    bars = [{"label": f"{r['player_name']} (#{r['pick_no']})", "value": r["slope"]}
            for r in rows]
    return [_bar_spec("draftSlopeChart", "Draft Slot vs Finish: Steals & Reaches",
                      bars, "Places gained vs draft slot", color_by_sign=True,
                      tall=True, caption_key="draft_slope_chart")]


def _short_name(name: str, width: int = 14) -> str:
    """Axis labels on a 12x12 grid have almost no room; trim rather than let
    Chart.js overlap them."""
    return name if len(name) <= width else name[: width - 1] + "\u2026"


def _season_chart_specs(season, season_stats, month_stats, upto_week=None) -> list:
    """Season/monthly charts: season-long luck scatter, optional monthly
    +/- bar (only when month_stats is present), season efficiency bar."""
    if not season_stats:
        return []
    points, eff_bars = [], []
    for r in standings_rows(season, upto_week):
        rid = r["rid"]
        ss = season_stats.get(rid)
        games = r["w"] + r["l"] + r["ties"]
        season_win_pct = round(r["w"] / games * 100, 1) if games else 0.0
        if not ss:
            continue
        ap_games = ss.all_play_w + ss.all_play_l
        if ap_games:
            ap_pct = round(ss.all_play_w / ap_games * 100, 1)
            points.append({
                "x": ap_pct, "y": season_win_pct, "label": r["team"],
                "good": season_win_pct >= ap_pct,
                "tooltip": f"{r['team']}: {season_win_pct:.0f}% actual vs {ap_pct:.0f}% all-play",
            })
        eff_bars.append({"label": r["team"], "value": ss.avg_efficiency})

    specs = []
    if points:
        specs.append(_scatter_spec("luckChart", "Luck: All-Play Win% vs Actual Win%", points,
                                   "All-Play Win % (season)", "Actual Win % (season)",
                                   (0, 100), (0, 100), tall=True, caption_key="luck_chart_season"))
    if month_stats:
        month_bars = [{"label": season.team_name(rid), "value": m.pts_above_avg}
                      for rid, m in month_stats.items()]
        if month_bars:
            specs.append(_bar_spec("monthDeltaChart", "Monthly +/- vs Average", month_bars,
                                   "Points vs league average", color_by_sign=True,
                                   caption_key="month_delta_chart"))
    if eff_bars:
        specs.append(_bar_spec("efficiencyChart", "Season Lineup Efficiency", eff_bars,
                               "Lineup efficiency %", x_range=(0, 100),
                               caption_key="efficiency_chart"))
    return specs


def _weekly_chart_specs(season, week_stats: dict, week: int | None = None) -> list:
    """Weekly charts: this-week luck scatter (all-play% vs points scored,
    colored by whether the score beat the week's median — a single week's
    actual W/L is binary and the season chart's diagonal-line "fair" framing
    doesn't translate to one week, so median is the more useful colour
    signal here), points-vs-week-average bar, this week's efficiency bar —
    the weekly-scoped analogues of the season/monthly charts, plus the
    cumulative decision-value line — the one chart the weekly cadence
    actually builds rather than merely recomputes."""
    if not week_stats:
        return []
    scores = {rid: ws["score"] for rid, ws in week_stats.items()}
    avg = statistics.mean(scores.values()) if scores else 0.0
    median = statistics.median(scores.values()) if scores else 0.0
    points, eff_bars, avg_bars = [], [], []
    for rid, ws in week_stats.items():
        team = season.team_name(rid)
        w, l = ws.get("all_play_w", 0), ws.get("all_play_l", 0)
        if w + l:
            ap_pct = round(w / (w + l) * 100, 1)
            points.append({
                "x": ap_pct, "y": ws["score"], "label": team,
                "good": ws["score"] >= median,
                "tooltip": f"{team}: {ws['score']:.1f} pts, {ap_pct:.0f}% all-play this week",
            })
        if ws.get("efficiency") is not None:
            eff_bars.append({"label": team, "value": ws["efficiency"]})
        avg_bars.append({"label": team, "value": round(ws["score"] - avg, 1)})

    specs = []
    if points:
        score_lo, score_hi = min(p["y"] for p in points), max(p["y"] for p in points)
        pad = max(5.0, (score_hi - score_lo) * 0.1)
        specs.append(_scatter_spec("luckChart", "Luck: All-Play Win% vs Points Scored", points,
                                   "All-Play Win % (this week)", "Points scored",
                                   (0, 100), (score_lo - pad, score_hi + pad),
                                   with_diagonal=False, tall=True, caption_key="luck_chart_weekly"))
    if avg_bars:
        specs.append(_bar_spec("avgDeltaChart", "Points vs Week Average", avg_bars,
                               "Points vs league average", color_by_sign=True,
                               caption_key="avg_delta_chart"))
    if eff_bars:
        specs.append(_bar_spec("efficiencyChart", "This Week's Lineup Efficiency", eff_bars,
                               "Lineup efficiency %", x_range=(0, 100),
                               caption_key="efficiency_chart"))

    if week is not None:
        cdv = MG.cumulative_decision_value(season, upto_week=week)
        if cdv:
            weeks_axis = sorted({w for v in cdv.values() for (w, _d, _c) in v["series"]})
            # Emphasise the two extremes so a twelve-line chart still has a
            # readable story rather than being a ball of spaghetti.
            ranked = sorted(cdv.items(), key=lambda kv: kv[1]["total"])
            emphasised = {ranked[0][0], ranked[-1][0]} if len(ranked) > 1 else set()
            series = []
            for rid, v in sorted(cdv.items(), key=lambda kv: kv[1]["total"]):
                by_week = {w: c for (w, _d, c) in v["series"]}
                series.append({
                    "label": season.team_name(rid),
                    "data": [by_week.get(w) for w in weeks_axis],
                    "emphasis": rid in emphasised,
                })
            specs.append(_line_spec("decisionValueChart",
                                    "Cumulative Cost of Start/Sit Decisions",
                                    [f"Wk {w}" for w in weeks_axis], series,
                                    "Week", "Cumulative points left on bench",
                                    tall=True, caption_key="decision_value_chart"))
    return specs


def _charts_html(specs: list) -> str:
    if not specs:
        return ""
    boxes = []
    for spec in specs:
        cls = "chart-box tall" if spec.get("tall") else "chart-box"
        caption = spec.get("caption", "")
        caption_html = f'<p class="caption">{html.escape(caption)}</p>' if caption else ""
        boxes.append(f'<div class="{cls}"><h3>{html.escape(spec["title"])}</h3>{caption_html}'
                     f'<div class="canvas-wrap"><canvas id="{spec["id"]}"></canvas></div></div>')
    by_kind = {k: [s for s in specs if s["kind"] == k]
               for k in ("scatter", "bar", "line", "matrix", "radar")}
    # Defang any "</script>" a malicious team name could smuggle into the JSON payload.
    data_json = json.dumps({
        "scatterCharts": by_kind["scatter"], "barCharts": by_kind["bar"],
        "lineCharts": by_kind["line"], "matrixCharts": by_kind["matrix"],
        "radarCharts": by_kind["radar"],
    }).replace("</", "<\\/")
    # The matrix controller is a separate plugin; only pay for it when a
    # matrix chart is actually on the page.
    matrix_js = ('<script src="https://cdn.jsdelivr.net/npm/chartjs-chart-matrix@2"></script>'
                 if by_kind["matrix"] else "")
    return f"""
  <h2>Charts</h2>
  <div class="charts-grid">{''.join(boxes)}</div>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2"></script>
  {matrix_js}
  <script>
    const DOSSIER_DATA = {data_json};
    {_GENERIC_CHARTS_JS}
  </script>
"""


def _pdf_manager_cell(season, rid) -> str:
    """Small circular avatar + team name in one <td> for the PDF analytics table."""
    team = season.teams.get(rid)
    uri = IMG.to_data_uri(IMG.manager_avatar_url(team))
    img = f'<img class="avatar manager sm" src="{uri}" onerror="this.onerror=null;this.src=\'{IMG.SILHOUETTE_DATA_URI}\'">'
    name = html.escape(season.team_name(rid))
    return f"<td><span style='display:inline-flex;align-items:center;gap:6px'>{img}<strong>{name}</strong></span></td>"


def _pdf_weekly_table(season, week, week_stats: dict, pairs: list, pr: dict | None) -> str:
    """Dense print-only summary table for weekly PDFs — all teams in one row
    with score, power rank, all-play, luck, and efficiency side by side.
    Mirrors the FPL-style leaderboard layout."""
    if not week_stats:
        return ""
    results = _week_results(pairs)
    pr_ranked = {rid: i for i, (rid, _) in enumerate(
        sorted((pr or {}).items(), key=lambda kv: kv[1]["score"], reverse=True), 1)}
    ap_data = S.all_play(season.weeks.get(week, {}))
    rows = ""
    for i, (rid, ws) in enumerate(sorted(week_stats.items(),
                                          key=lambda kv: kv[1]["score"], reverse=True), 1):
        ap = ap_data.get(rid)
        ap_w, ap_l = (ap[0], ap[1]) if ap else (0, 0)
        ap_rate = ap_w / (ap_w + ap_l) if (ap_w + ap_l) else 0.5
        actual = results.get(rid, "—")
        luck = _luck_tag(actual, ap_rate) if actual != "—" else "—"
        luck_cls = "lucky" if luck == "lucky" else ("robbed" if luck == "robbed" else "")
        eff = ws.get("efficiency")
        eff_str = f"{eff:.0f}%" if eff is not None else "—"
        pr_pos = pr_ranked.get(rid, "—")
        rows += (f"<tr>"
                f"<td class='rank-col'>{i}</td>"
                f"{_pdf_manager_cell(season, rid)}"
                f"<td class='num'><strong>{ws['score']:.1f}</strong></td>"
                f"<td class='num'>{actual}</td>"
                f"<td class='num'>{ap_w}-{ap_l}</td>"
                f"<td class='num {luck_cls}'>{luck}</td>"
                f"<td class='num'>{eff_str}</td>"
                f"<td class='num'>{pr_pos}</td>"
                f"</tr>")
    return f"""<div class="print-only">
      <p class="pdf-section-head">This Week's Standings</p>
      <table class="pdf-table">
        <tr><th>#</th><th>Team</th><th>Score</th><th>Result</th>
            <th>All-Play</th><th>Luck</th><th>Efficiency</th><th>PR</th></tr>
        {rows}
      </table></div>"""


def _pdf_season_table(season, season_stats: dict | None, month_stats: dict | None,
                      kind: str, weeks: list, upto_week: int | None) -> str:
    """Dense print-only summary table for monthly/season PDFs."""
    rows_data = standings_rows(season, upto_week)
    pr_data = None
    if kind == "season" and weeks:
        pr_data = S.power_rank(season, max(weeks))
    pr_ranked = {rid: i for i, (rid, _) in enumerate(
        sorted((pr_data or {}).items(), key=lambda kv: kv[1]["score"], reverse=True), 1)}

    rows = ""
    for i, r in enumerate(rows_data, 1):
        rid = r["rid"]
        wl = f"{r['w']}-{r['l']}" + (f"-{r['ties']}" if r['ties'] else "")
        ss = (season_stats or {}).get(rid)
        ms = (month_stats or {}).get(rid)
        ap = f"{ss.all_play_w}-{ss.all_play_l}" if ss else "—"
        luck_val = f"{ss.luck_index:+.1f}" if ss else "—"
        luck_cls = ("lucky" if ss and ss.luck_index > 0 else
                    "robbed" if ss and ss.luck_index < 0 else "")
        eff = f"{ss.avg_efficiency:.0f}%" if ss else "—"
        pr_pos = pr_ranked.get(rid, "—") if pr_data else "—"
        pts_above = f"{ms.pts_above_avg:+.1f}" if ms else "—"
        pts_above_cls = "lucky" if ms and ms.pts_above_avg > 0 else (
                         "robbed" if ms and ms.pts_above_avg < 0 else "")
        rows += (f"<tr>"
                f"<td class='rank-col'>{i}</td>"
                f"{_pdf_manager_cell(season, rid)}"
                f"<td class='num'>{wl}</td>"
                f"<td class='num'>{r['pf']:.0f}</td>"
                f"<td class='num {pts_above_cls}'>{pts_above}</td>"
                f"<td class='num'>{ap}</td>"
                f"<td class='num {luck_cls}'>{luck_val}</td>"
                f"<td class='num'>{eff}</td>"
                f"<td class='num'>{pr_pos}</td>"
                f"</tr>")

    pts_col = "This Month +/-" if kind == "monthly" else "vs Avg"
    pr_col = "PR" if kind == "season" else "—"
    return f"""<div class="print-only">
      <p class="pdf-section-head">{"Season" if kind == "season" else "Month"} Standings</p>
      <table class="pdf-table">
        <tr><th>#</th><th>Team</th><th>Record</th><th>PF</th>
            <th>{pts_col}</th><th>All-Play</th><th>Luck</th><th>Efficiency</th>
            <th>{pr_col}</th></tr>
        {rows}
      </table></div>"""


def _img_tag(data_uri: str, css_class: str) -> str:
    """onerror swaps to the bundled local silhouette — mostly defensive,
    since data URIs we resolved ourselves rarely fail to decode, but it's
    the documented safety net for any image that does."""
    return (f'<img class="{css_class}" src="{data_uri}" '
           f'onerror="this.onerror=null;this.src=\'{IMG.SILHOUETTE_DATA_URI}\'">')


def _award_image_html(season, a) -> str:
    """Player awards get a headshot; trade awards get both managers'
    avatars with a swap glyph between them; everything else (the vast
    majority — awards about a manager's overall performance, luck, lineup
    decisions) gets that manager's avatar. image_kind == "none" (or a
    malformed trade missing its loser) renders nothing — no fallback image
    forced onto an award with no natural subject."""
    if a.image_kind == "player":
        uri = IMG.to_data_uri(IMG.player_headshot_url(a.player_id))
        return f'<div class="avatar-row">{_img_tag(uri, "avatar player")}</div>'
    if a.image_kind == "trade" and a.loser_rid is not None:
        winner_uri = IMG.to_data_uri(IMG.manager_avatar_url(season.teams.get(a.winner_rid)))
        loser_uri = IMG.to_data_uri(IMG.manager_avatar_url(season.teams.get(a.loser_rid)))
        return (f'<div class="avatar-row">{_img_tag(winner_uri, "avatar manager")}'
               f'<span class="trade-swap">⇄</span>{_img_tag(loser_uri, "avatar manager")}</div>')
    if a.image_kind == "manager" and a.winner_rid is not None:
        uri = IMG.to_data_uri(IMG.manager_avatar_url(season.teams.get(a.winner_rid)))
        return f'<div class="avatar-row">{_img_tag(uri, "avatar manager")}</div>'
    return ""


def _card(season, a, roasts):
    team = html.escape(season.team_name(a.winner_rid))
    val = html.escape(a.headline)
    roast = roasts.get(a.title, "")
    roast_html = f'<div class="roast">{html.escape(roast)}</div>' if roast else ""
    podium = ""
    if a.podium:
        bits = " · ".join(f"{html.escape(season.team_name(r))}: {html.escape(v)}"
                          for r, v in a.podium)
        podium = f'<div class="podium">{bits}</div>'
    image_html = _award_image_html(season, a)
    return f"""<div class="card {a.hall}">
      <div class="head"><span>{html.escape(a.title)}</span></div>
      <div class="body">
        {image_html}<div class="team">{team}</div>
        <div class="val">{html.escape(a.flavour)} — {val}</div>
        {roast_html}{podium}
      </div></div>"""


def _deep_dive_html(season, weeks: list, upto_week=None) -> str:
    """The Deep Dive section: Pythagorean expectation, scoring volatility,
    optimal-lineup record, close-game record, and waiver ROI.

    Each is a season-to-date measure, so it's built from `upto_week` (the
    report's cutoff) rather than live team totals — a retrospective monthly
    report must not leak later weeks' results.
    """
    last_week = upto_week if upto_week is not None else max(weeks)

    py = S.pythagorean(season, upto_week=last_week)
    cons = S.consistency(season, upto_week=last_week)
    opt = S.optimal_record(season, upto_week=last_week)
    strk = S.streaks(season, upto_week=last_week)
    clutch = S.clutch_record(season, upto_week=last_week)
    if not py:
        return ""

    rows = ""
    for rid, p in sorted(py.items(), key=lambda kv: kv[1]["delta"], reverse=True):
        c = cons.get(rid, {})
        o = opt.get(rid, {})
        st = strk.get(rid, {})
        cl = clutch.get(rid, {})
        d_cls = "lucky" if p["delta"] > 0 else ("robbed" if p["delta"] < 0 else "")
        o_delta = o.get("delta", 0)
        o_cls = "robbed" if o_delta > 0 else ("lucky" if o_delta < 0 else "")
        opt_cell = (f"{o.get('wins', 0)}-{o.get('losses', 0)}"
                    f" <span class='{o_cls}'>({o_delta:+d})</span>") if o else "—"
        streak_cell = (f"{st['current']}{st['current_kind']}"
                       if st.get("current") else "—")
        clutch_cell = f"{cl['wins']}-{cl['losses']}" if cl else "—"
        rows += (f"<tr><td>{html.escape(season.team_name(rid))}</td>"
                 f"<td class='num'>{p['expected_wins']:.1f}</td>"
                 f"<td class='num {d_cls}'>{p['delta']:+.1f}</td>"
                 f"<td class='num'>{opt_cell}</td>"
                 f"<td class='num'>{c.get('cv', 0):.0f}%</td>"
                 f"<td class='num'>{c.get('floor', 0):.0f}–{c.get('ceiling', 0):.0f}</td>"
                 f"<td class='num'>{clutch_cell}</td>"
                 f"<td class='num'>{streak_cell}</td></tr>")

    table = f"""<table><tr><th>Team</th><th>Exp W</th><th>vs Exp</th><th>Optimal</th>
      <th>Swing</th><th>Floor–Ceiling</th><th>Close</th><th>Streak</th></tr>{rows}</table>"""

    bits = []
    biggest = max(opt.items(), key=lambda kv: kv[1]["delta"], default=None)
    if biggest and biggest[1]["delta"] > 0:
        rid, o = biggest
        bits.append(f"<p><strong>Most wins left on the bench:</strong> "
                    f"{html.escape(season.team_name(rid))} — {o['actual_wins']}-{o['actual_losses']} "
                    f"actual, but {o['wins']}-{o['losses']} if they'd started their best lineup "
                    f"every week ({o['delta']:+d}).</p>")
    if cons:
        swingiest = max(cons.items(), key=lambda kv: kv[1]["cv"])
        steadiest = min(cons.items(), key=lambda kv: kv[1]["cv"])
        bits.append(f"<p><strong>Boom or bust:</strong> "
                    f"{html.escape(season.team_name(swingiest[0]))} "
                    f"({swingiest[1]['cv']:.0f}% swing, {swingiest[1]['floor']:.0f}–"
                    f"{swingiest[1]['ceiling']:.0f}) &nbsp;·&nbsp; "
                    f"<strong>Metronome:</strong> "
                    f"{html.escape(season.team_name(steadiest[0]))} "
                    f"({steadiest[1]['cv']:.0f}%).</p>")
    longest = max(strk.items(), key=lambda kv: kv[1]["longest_win"], default=None)
    if longest and longest[1]["longest_win"] >= 3:
        bits.append(f"<p><strong>Longest win streak:</strong> "
                    f"{html.escape(season.team_name(longest[0]))} — "
                    f"{longest[1]['longest_win']} straight.</p>")

    roi_html = ""
    roi = W.waiver_roi(season, weeks)
    paid = {rid: v for rid, v in roi.items() if v["roi"] is not None}
    if paid:
        roi_rows = ""
        for rid, v in sorted(paid.items(), key=lambda kv: kv[1]["roi"], reverse=True):
            free = (f"{v['free_adds']} free (+{v['free_points']:.0f})"
                    if v["free_adds"] else "—")
            roi_rows += (f"<tr><td>{html.escape(season.team_name(rid))}</td>"
                         f"<td class='num'>${v['faab']}</td>"
                         f"<td class='num'>{v['points']:.1f}</td>"
                         f"<td class='num'>{v['roi']:.2f}</td>"
                         f"<td class='num'>{free}</td></tr>")
        roi_html = f"""<h2>Waiver ROI — Points per FAAB Dollar</h2>
          {_caption_html("waiver_roi")}
          <table><tr><th>Team</th><th>Spent</th><th>Points</th><th>Pts/$</th>
          <th>Free adds</th></tr>{roi_rows}</table>"""

    return f"""<h2>Deep Dive</h2>
      {_caption_html("pythagorean")}
      {table}
      {''.join(bits)}
      {roi_html}"""


def render_html(season, awards, roasts, period_label, season_stats=None,
                kind="monthly", month_stats=None, recap="", waiver_take="", tier="normal",
                playoff_odds=None, pickup_odds_swing=None):
    fame = [a for a in awards if a.hall == "fame"]
    shame = [a for a in awards if a.hall == "shame"]
    title = period_label
    full_report = tier != "free"

    recap_html = f'<div class="recap">{html.escape(recap)}</div>' if recap else ""

    fame_cards = "".join(_card(season, a, roasts) for a in fame)
    shame_cards = "".join(_card(season, a, roasts) for a in shame)

    if not full_report:
        return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{_CSS}</style></head><body><div class="wrap">
  <div class="cover">
    <h1>{html.escape(season.name)} THE DOSSIER</h1>
    <div class="sub">{html.escape(title)}</div>
    <div class="meta">League: {html.escape(season.name)}<br>
      Season: {html.escape(season.season)} &nbsp; Managers: {len(season.teams)}</div>
  </div>

  {recap_html}

  <div class="bar fame page-start">🏆 Hall of Fame</div>
  <div class="grid">{fame_cards}</div>

  <div class="bar shame page-start">💀 Hall of Shame</div>
  <div class="grid">{shame_cards}</div>
</div></body></html>"""

    # Standings table (with weekly-form sparkline column). Frozen at this
    # period's last played week for monthly reports, not today's live totals.
    weeks = _relevant_weeks(season, month_stats)
    upto_week = max(weeks) if month_stats else None
    standings_heading = "Overall Standings &amp; Form (final season)" if kind == "season" \
        else "Overall Standings &amp; Form (through end of this month)"
    st_rows = ""
    for i, r in enumerate(standings_rows(season, upto_week), 1):
        wl = f"{r['w']}-{r['l']}" + (f"-{r['ties']}" if r['ties'] else "")
        scores = [season.weeks[wk][r["rid"]].points for wk in weeks if r["rid"] in season.weeks.get(wk, {})]
        spark = html.escape(_sparkline(scores))
        st_rows += (f"<tr><td>{i}</td><td>{html.escape(r['team'])}</td>"
                    f"<td>{wl}</td><td class='num'>{r['pf']:.1f}</td>"
                    f"<td class='num'>{r['pa']:.1f}</td><td class='spark'>{spark}</td></tr>")

    closest_html = ""
    cg = _closest_game(season, weeks)
    if cg:
        margin, wk, ra, pa, rb, pb = cg
        winner, loser = (ra, rb) if pa > pb else (rb, ra)
        hi, lo = max(pa, pb), min(pa, pb)
        closest_html = (f'<div class="closest"><strong>Closest race —</strong> '
                        f'Week {wk}: {html.escape(season.team_name(winner))} beat '
                        f'{html.escape(season.team_name(loser))} by just {margin:.1f} '
                        f'({hi:.1f}–{lo:.1f})</div>')

    playoff_html = ""
    if playoff_odds:
        rows = "".join(f"<tr><td>{html.escape(season.team_name(rid))}</td>"
                       f"<td class='num'>{odds * 100:.1f}%</td></tr>"
                       for rid, odds in sorted(playoff_odds.items(), key=lambda kv: kv[1], reverse=True))
        playoff_html = (f"<h2>Playoff Odds (Monte Carlo, rest of regular season)</h2>"
                        f"<table><tr><th>Team</th><th>Odds</th></tr>{rows}</table>")

    waiver_html = ""
    if weeks:
        best = W.best_pickup_period(season, weeks)
        worst = W.worst_faab_period(season, weeks)
        faab_totals = W.faab_spent_by_team(season, weeks)
        trades = W.trades_in(season, weeks)
        if best or worst or faab_totals or trades or waiver_take:
            bits = []
            if best:
                spend = f"${best.faab}" if best.faab else "free"
                bits.append(f'<p><strong>Best pickup:</strong> {html.escape(season.team_name(best.roster_id))} '
                           f'added {html.escape(best.player_name)} ({spend}) → {best.points_since:.1f} pts since</p>')
            if worst:
                bits.append(f'<p><strong>Worst FAAB spend:</strong> {html.escape(season.team_name(worst.roster_id))} '
                           f'paid ${worst.faab} for {html.escape(worst.player_name)} → {worst.points_since:.1f} pts since</p>')
            if faab_totals:
                top = sorted(faab_totals.items(), key=lambda kv: kv[1], reverse=True)[:3]
                spend_str = ", ".join(f"{html.escape(season.team_name(rid))} (${amt})" for rid, amt in top)
                bits.append(f"<p><strong>Top FAAB spend:</strong> {spend_str}</p>")
            por = W.best_por_period(season, weeks)
            if por:
                swing = f", {pickup_odds_swing:+.1f}% playoff odds" if pickup_odds_swing is not None else ""
                bits.append(f'<p><strong>Best value pickup (pts over replacement):</strong> '
                           f'{html.escape(season.team_name(por.roster_id))} added '
                           f'{html.escape(por.player_name)} ({html.escape(por.position or "?")}) '
                           f'&rarr; +{por.por:.1f} pts over a replacement-level {html.escape(por.position or "?")} '
                           f'over {por.games} games{html.escape(swing)}</p>')
            sharpe = W.sharpe_by_position(season, weeks)
            if sharpe:
                top_pos, s = max(sharpe.items(), key=lambda kv: kv[1]["avg_sharpe"])
                bits.append(f'<p><strong>Most reliable position off the wire:</strong> '
                           f'{html.escape(top_pos)} (avg Sharpe {s["avg_sharpe"]:.2f} across {s["n"]} pickups; '
                           f'best: {html.escape(s["best"].player_name)} at {s["best"].sharpe:.2f})</p>')
            if trades:
                trade_rows = "".join(f"<li>Week {t.week}: {html.escape(_format_trade(season, t))}</li>" for t in trades)
                bits.append(f"<p><strong>Trades ({len(trades)}):</strong></p><ul>{trade_rows}</ul>")
            if waiver_take:
                bits.append(f'<p class="take">{html.escape(waiver_take)}</p>')
            waiver_html = f"<h2>Waiver Wire &amp; Trades</h2>{''.join(bits)}"

    luck_html = ""
    if season_stats:
        record_by_rid = {r["rid"]: f"{r['w']}-{r['l']}" + (f"-{r['ties']}" if r['ties'] else "")
                         for r in standings_rows(season, upto_week)}
        rows = ""
        for s in sorted(season_stats.values(), key=lambda x: x.luck_index, reverse=True):
            cls = "lucky" if s.luck_index > 0 else ("robbed" if s.luck_index < 0 else "")
            rows += (f"<tr><td>{html.escape(season.team_name(s.roster_id))}</td>"
                     f"<td class='num'>{record_by_rid.get(s.roster_id, '—')}</td>"
                     f"<td class='num {cls}'>{s.luck_index:+.1f}</td>"
                     f"<td class='num'>{s.all_play_w}-{s.all_play_l}</td>"
                     f"<td class='num'>{s.avg_efficiency:.0f}%</td></tr>")
        luck_html = f"""<h2>Luck Index — Season to Date (actual record vs all-play record; luck = actual - deserved wins)</h2>
          {_caption_html("luck_index_to_date")}
          <table><tr><th>Team</th><th>Actual</th><th>Luck (W vs deserved)</th>
          <th>All-Play</th><th>Efficiency</th></tr>{rows}</table>"""

    power_rank_html = ""
    median_html = ""
    if kind == "season" and weeks:
        last_week = max(weeks)
        pr = S.power_rank(season, last_week)
        if pr:
            std_order = [r["rid"] for r in standings_rows(season, last_week)]
            std_rank = {rid: i + 1 for i, rid in enumerate(std_order)}
            ranked = sorted(pr.items(), key=lambda kv: kv[1]["score"], reverse=True)
            rows = ""
            for i, (rid, p) in enumerate(ranked, 1):
                delta = std_rank.get(rid, i) - i
                delta_cls = "lucky" if delta > 0 else ("robbed" if delta < 0 else "")
                delta_str = f"{delta:+d}" if delta else "—"
                rows += (f"<tr><td>{i}</td><td>{html.escape(season.team_name(rid))}</td>"
                         f"<td class='num'>{p['score']:.1f}</td>"
                         f"<td class='num'>{p['all_play_pct']:.0f}%</td>"
                         f"<td class='num'>{p['form_pct']:.0f}%</td>"
                         f"<td class='num'>{p['efficiency_pct']:.0f}%</td>"
                         f"<td class='num {delta_cls}'>{delta_str}</td></tr>")
            power_rank_html = f"""<h2>Season Power Ranking</h2>
              {_caption_html("power_ranking")}
              <table><tr><th>#</th><th>Team</th><th>Score</th><th>All-Play</th>
              <th>Form</th><th>Efficiency</th><th>vs Standings</th></tr>{rows}</table>"""

    draft_value_html = ""
    if kind == "season":
        board = S.draft_value_board(season)
        if board["values"] or board["busts"]:
            def _player_cell(r):
                avatar = _img_tag(IMG.to_data_uri(IMG.player_headshot_url(r["player_id"])), "avatar player sm")
                return (f"<td><span style='display:inline-flex;align-items:center;gap:8px'>"
                       f"{avatar}{html.escape(r['player_name'])}</span></td>")

            rows = ""
            for r in board["values"]:
                rows += (f"<tr><td class='lucky'>Steal</td>{_player_cell(r)}"
                         f"<td class='num'>{r['pick_no']}</td><td class='num'>{r['points']:.1f}</td>"
                         f"<td>{html.escape(season.team_name(r['roster_id']))}</td></tr>")
            for r in board["busts"]:
                rows += (f"<tr><td class='robbed'>Bust</td>{_player_cell(r)}"
                         f"<td class='num'>{r['pick_no']}</td><td class='num'>{r['points']:.1f}</td>"
                         f"<td>{html.escape(season.team_name(r['roster_id']))}</td></tr>")
            draft_value_html = f"""<h2>Draft Value: Biggest Steals &amp; Busts</h2>
              {_caption_html("draft_value")}
              <table><tr><th></th><th>Player</th><th>Pick</th><th>Points</th><th>Team</th></tr>{rows}</table>"""

    if weeks:
        last_week = max(weeks)
        mr = S.median_record(season, upto_week=last_week)
        if mr:
            rows = ""
            for rid, c in sorted(mr.items(), key=lambda kv: kv[1]["above"], reverse=True):
                record = f"{c['above']}-{c['below']}" + (f"-{c['tied']}" if c["tied"] else "")
                rows += (f"<tr><td>{html.escape(season.team_name(rid))}</td>"
                         f"<td class='num'>{record}</td></tr>")
            median_html = f"""<h2>Median What-If To Date (weeks beating the league median each week, season to date)</h2>
              {_caption_html("median_whatif_to_date")}
              <table><tr><th>Team</th><th>Record vs Median</th></tr>{rows}</table>"""

    deep_dive_html = ""
    if weeks:
        deep_dive_html = _deep_dive_html(season, weeks, upto_week)

    chart_specs = _season_chart_specs(season, season_stats, month_stats, upto_week)
    chart_specs += _manager_chart_specs(season, weeks, upto_week)
    if kind == "season":
        chart_specs += _draft_chart_specs(season)
    charts_html = _charts_html(chart_specs)

    month_html = ""
    if month_stats:
        rows = ""
        for m in sorted(month_stats.values(), key=lambda x: x.pts_above_avg, reverse=True):
            rank = f"{m.rank_start} → {m.rank_end}" if m.rank_start else f"→ {m.rank_end}"
            rank_cls = "lucky" if m.climb > 0 else ("robbed" if m.climb < 0 else "")
            aa_cls = "lucky" if m.pts_above_avg > 0 else "robbed"
            rows += (f"<tr><td>{html.escape(season.team_name(m.roster_id))}</td>"
                     f"<td class='num'>{m.h2h_w}-{m.h2h_l}</td>"
                     f"<td class='num {aa_cls}'>{m.pts_above_avg:+.1f}</td>"
                     f"<td class='num {rank_cls}'>{rank}</td>"
                     f"<td class='num'>{m.avg_efficiency:.0f}%</td></tr>")
        month_html = f"""<h2>The Month in Review (this month's standing, not season-to-date)</h2>
          <table><tr><th>Team</th><th>Record</th><th>+/- vs Avg</th>
          <th>Rank (start → end)</th><th>Efficiency</th></tr>{rows}</table>"""

    pdf_table = _pdf_season_table(season, season_stats, month_stats, kind, weeks, upto_week)

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{_CSS}</style></head><body><div class="wrap">
  <div class="cover">
    <h1>{html.escape(season.name)} THE DOSSIER</h1>
    <div class="sub">{html.escape(title)}</div>
    <div class="meta">League: {html.escape(season.name)}<br>
      Season: {html.escape(season.season)} &nbsp; Managers: {len(season.teams)}</div>
  </div>

  {recap_html}

  <div class="bar fame page-start">🏆 Hall of Fame</div>
  <div class="grid">{fame_cards}</div>

  <div class="bar shame page-start">💀 Hall of Shame</div>
  <div class="grid">{shame_cards}</div>

  <div class="page-start"></div>

  {month_html}
  {closest_html}

  {pdf_table}

  <h2 class="screen-only">{standings_heading}</h2>
  <table class="screen-only"><tr><th>#</th><th>Team</th><th>W-L</th><th>PF</th><th>PA</th><th>Form</th></tr>{st_rows}</table>

  {charts_html}

  <div class="screen-only">{power_rank_html}{luck_html}{median_html}</div>
  {deep_dive_html}
  {draft_value_html}
  {playoff_html}
  {waiver_html}
</div></body></html>"""


def render_weekly_html(season, awards, roasts, period_label, week, rivalry_matchups=None, recap="",
                       tier="normal", playoff_odds=None, pickup_odds_swing=None) -> str:
    fame = [a for a in awards if a.hall == "fame"]
    shame = [a for a in awards if a.hall == "shame"]

    recap_html = f'<div class="recap">{html.escape(recap)}</div>' if recap else ""
    fame_cards = "".join(_card(season, a, roasts) for a in fame)
    shame_cards = "".join(_card(season, a, roasts) for a in shame)

    if tier == "free":
        return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{_CSS}</style></head><body><div class="wrap">
  <div class="cover">
    <h1>{html.escape(season.name)} THE DOSSIER</h1>
    <div class="sub">{html.escape(period_label)}</div>
    <div class="meta">League: {html.escape(season.name)}<br>
      Season: {html.escape(season.season)} &nbsp; Managers: {len(season.teams)}</div>
  </div>

  {recap_html}

  <div class="bar fame page-start">🏆 Hall of Fame</div>
  <div class="grid">{fame_cards}</div>

  <div class="bar shame page-start">💀 Hall of Shame</div>
  <div class="grid">{shame_cards}</div>
</div></body></html>"""

    wd = season.weeks.get(week, {})
    scores = S.weekly_scores(wd)
    pairs = S.matchup_pairs(wd)
    week_stats = S.week_report_stats(season, week)
    charts_html = _charts_html(_weekly_chart_specs(season, week_stats, week=week))

    # Season-to-date context in a single-week report. Playoff odds belong here
    # more than anywhere: they move most in the week that moved them, and
    # "your odds fell 14 points on Sunday" is a weekly sentence.
    weekly_playoff_html = ""
    if playoff_odds:
        rows = "".join(f"<tr><td>{html.escape(season.team_name(rid))}</td>"
                       f"<td class='num'>{odds * 100:.1f}%</td></tr>"
                       for rid, odds in sorted(playoff_odds.items(),
                                               key=lambda kv: kv[1], reverse=True))
        swing_note = ""
        if pickup_odds_swing is not None:
            swing_note = (f"<p>This week's best waiver add was worth "
                          f"<strong>{pickup_odds_swing:+.1f}%</strong> of playoff odds "
                          f"to the team that made it.</p>")
        weekly_playoff_html = (f"<h2>Playoff Odds (Monte Carlo, rest of regular season)</h2>"
                               f"<table><tr><th>Team</th><th>Odds</th></tr>{rows}</table>"
                               f"{swing_note}")

    weekly_deep_dive_html = _deep_dive_html(season, list(range(1, week + 1)), week)

    no_matchup_html = "" if pairs else (
        '<div class="notice">No head-to-head matchups recorded for this week (likely outside '
        'the regular-season bracket). Power ranking and median what-if below are still accurate; '
        "matchup-dependent awards, the luck index's actual result, this week's extremes, and "
        'rivalry watch are not.</div>')

    power_html = ""
    pr = S.power_rank(season, week)
    if pr:
        std_order = [r["rid"] for r in standings_rows(season, week)]
        std_rank = {rid: i + 1 for i, rid in enumerate(std_order)}
        ranked = sorted(pr.items(), key=lambda kv: kv[1]["score"], reverse=True)
        rows = ""
        for i, (rid, p) in enumerate(ranked, 1):
            delta = std_rank.get(rid, i) - i
            delta_cls = "lucky" if delta > 0 else ("robbed" if delta < 0 else "")
            delta_str = f"{delta:+d}" if delta else "—"
            rows += (f"<tr><td>{i}</td><td>{html.escape(season.team_name(rid))}</td>"
                     f"<td class='num'>{p['score']:.1f}</td>"
                     f"<td class='num'>{p['all_play_pct']:.0f}%</td>"
                     f"<td class='num'>{p['form_pct']:.0f}%</td>"
                     f"<td class='num'>{p['efficiency_pct']:.0f}%</td>"
                     f"<td class='num {delta_cls}'>{delta_str}</td></tr>")
        power_html = f"""<h2>Power Ranking</h2>
          {_caption_html("power_ranking")}
          <table><tr><th>#</th><th>Team</th><th>Score</th><th>All-Play</th>
          <th>Form</th><th>Efficiency</th><th>vs Standings</th></tr>{rows}</table>"""

    luck_index_html = ""
    ap = S.all_play(wd)
    if ap:
        results = _week_results(pairs)
        rows = ""
        for rid in sorted(ap, key=lambda r: scores.get(r, 0), reverse=True):
            w, l, _t = ap[rid]
            actual = results.get(rid, "—")
            rate = w / (w + l) if (w + l) else 0.5
            tag = _luck_tag(actual, rate)
            cls = "lucky" if tag == "lucky" else ("robbed" if tag == "robbed" else "")
            rows += (f"<tr><td>{html.escape(season.team_name(rid))}</td>"
                     f"<td class='num'>{scores.get(rid, 0):.1f}</td>"
                     f"<td class='num'>{actual}</td>"
                     f"<td class='num'>{w}-{l}</td>"
                     f"<td class='num {cls}'>{tag}</td></tr>")
        luck_index_html = f"""<h2>Luck Index (all-play record if you'd played everyone this week)</h2>
          {_caption_html("luck_index_weekly")}
          <table><tr><th>Team</th><th>Points</th><th>Actual</th><th>All-Play</th><th>Luck</th></tr>{rows}</table>"""

    median_html = ""
    median = S.weekly_median(wd)
    if scores:
        rows = ""
        for rid, pts in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
            tag = "beat" if pts > median else ("missed" if pts < median else "tied")
            cls = "lucky" if tag == "beat" else ("robbed" if tag == "missed" else "")
            rows += (f"<tr><td>{html.escape(season.team_name(rid))}</td>"
                     f"<td class='num'>{pts:.1f}</td>"
                     f"<td class='num {cls}'>{tag} median</td></tr>")
        median_html = f"""<h2>Median What-If (league median: {median:.1f})</h2>
          {_caption_html("median_whatif_weekly")}
          <table><tr><th>Team</th><th>Points</th><th>Result</th></tr>{rows}</table>"""

    extremes_html = ""
    cg = S.closest_game(season, [week])
    bg = S.blowout_game(season, [week])
    if cg:
        margin, wk, ra, pa, rb, pb = cg
        winner, loser = (ra, rb) if pa > pb else (rb, ra)
        hi, lo = max(pa, pb), min(pa, pb)
        extremes_html += (f'<div class="closest"><strong>Closest —</strong> '
                          f'{html.escape(season.team_name(winner))} beat {html.escape(season.team_name(loser))} '
                          f'by just {margin:.1f} ({hi:.1f}–{lo:.1f})</div>')
    if bg:
        margin, wk, ra, pa, rb, pb = bg
        winner, loser = (ra, rb) if pa > pb else (rb, ra)
        hi, lo = max(pa, pb), min(pa, pb)
        extremes_html += (f'<div class="closest"><strong>Blowout —</strong> '
                          f'{html.escape(season.team_name(winner))} beat {html.escape(season.team_name(loser))} '
                          f'by {margin:.1f} ({hi:.1f}–{lo:.1f})</div>')

    rivalry_html = ""
    if rivalry_matchups:
        cards = []
        for m in rivalry_matchups:
            name_a, name_b = html.escape(m["name_a"]), html.escape(m["name_b"])
            if m["pa"] == m["pb"]:
                matchup_line = f'{name_a} {m["pa"]:.1f} – {m["pb"]:.1f} {name_b} (tied)'
            else:
                a_win = m["pa"] > m["pb"]
                a_cls, b_cls = ("winner", "") if a_win else ("", "winner")
                matchup_line = (f'<span class="{a_cls}">{name_a} {m["pa"]:.1f}</span> – '
                               f'<span class="{b_cls}">{m["pb"]:.1f} {name_b}</span>')
            avatar_a = _img_tag(IMG.to_data_uri(IMG.manager_avatar_url(season.teams.get(m["ra"]))), "avatar manager sm")
            avatar_b = _img_tag(IMG.to_data_uri(IMG.manager_avatar_url(season.teams.get(m["rb"]))), "avatar manager sm")
            cards.append(f"""<div class="rivalry-card">
              <div class="avatar-row">{avatar_a}{avatar_b}</div>
              <div class="matchup">{matchup_line}</div>
              <div class="history">{html.escape(m['line'])}</div>
            </div>""")
        paired_rids = {rid for m in rivalry_matchups for rid in (m["ra"], m["rb"])}
        unmatched = [html.escape(season.team_name(rid))
                     for rid in sorted(wd.keys()) if rid not in paired_rids]
        unmatched_note = ""
        if unmatched:
            names = ", ".join(unmatched)
            unmatched_note = (f'<div class="notice">'
                              f'<strong>No matchup recorded for {names}.</strong> '
                              f'Sleeper doesn\'t assign matchup IDs to consolation/non-bracket '
                              f'games; scores still count for power ranking and luck index but '
                              f'the head-to-head pairing can\'t be determined from the API data.'
                              f'</div>')
        rivalry_html = (f'<h2>Rivalry Watch</h2>{_caption_html("rivalry_watch")}'
                        f'<div class="rivalry-grid">{"".join(cards)}</div>{unmatched_note}')

    pdf_table = _pdf_weekly_table(season, week, week_stats, pairs, pr)

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{_CSS}</style></head><body><div class="wrap">
  <div class="cover">
    <h1>{html.escape(season.name)} THE DOSSIER</h1>
    <div class="sub">{html.escape(period_label)}</div>
    <div class="meta">League: {html.escape(season.name)}<br>
      Season: {html.escape(season.season)} &nbsp; Managers: {len(season.teams)}</div>
  </div>

  {recap_html}

  <div class="bar fame page-start">🏆 Hall of Fame</div>
  <div class="grid">{fame_cards}</div>

  <div class="bar shame page-start">💀 Hall of Shame</div>
  <div class="grid">{shame_cards}</div>

  <div class="page-start"></div>

  {no_matchup_html}
  {extremes_html}

  {pdf_table}
  <div class="screen-only">{power_html}{luck_index_html}{median_html}</div>
  {weekly_deep_dive_html}
  {weekly_playoff_html}
  {charts_html}
  {rivalry_html}
</div></body></html>"""


# ──────────────────────────────────────────────────────────────────────────────
# Slim HTML email
# ──────────────────────────────────────────────────────────────────────────────
#
# The full dossier is 900KB once every avatar is inlined as a base64 data URI,
# which is ~9x Gmail's ~102KB clipping threshold — most of it ends up hidden
# behind "View entire message". It also leans on <canvas> + Chart.js and on
# flex/grid, none of which survive an email client.
#
# So the email is deliberately NOT the dossier: it's a teaser — a couple of
# standout awards and the branding — with the real thing attached as a PDF.
# Everything here is table-based with inline styles, and avatars are plain
# sleepercdn.com URLs rather than data URIs, which is what keeps it small.

EMAIL_BRAND = "The Waiver Wire Tap"
_EMAIL_NAVY = "#15243b"
_EMAIL_GREEN = "#19c37d"
_EMAIL_RED = "#c0392b"
_EMAIL_MUTED = "#5b6b85"


def _email_award_image(season, a) -> str:
    """Remote CDN URL for an award's subject — never a data URI. Returns ""
    when there's no natural subject, so the caller can omit the cell."""
    if a.image_kind == "player" and a.player_id:
        return IMG.player_headshot_url(a.player_id)
    if a.winner_rid is not None:
        return IMG.manager_avatar_url(season.teams.get(a.winner_rid))
    return ""


def _email_hero(season, a, roasts, label: str) -> str:
    """One standout award, as an email-safe table."""
    accent = _EMAIL_GREEN if a.hall == "fame" else _EMAIL_RED
    team = html.escape(season.team_name(a.winner_rid))
    img = _email_award_image(season, a)
    img_cell = ""
    if img:
        img_cell = (
            f'<td width="56" valign="top" style="padding:0 14px 0 0;">'
            f'<img src="{html.escape(img, quote=True)}" width="48" height="48" alt="" '
            f'style="display:block;width:48px;height:48px;border-radius:50%;'
            f'border:2px solid {accent};background:#cdd5e3;"></td>'
        )
    roast = roasts.get(a.title, "")
    roast_html = ""
    if roast:
        roast_html = (
            f'<div style="margin:8px 0 0;padding:8px 12px;background:#f7f9fc;'
            f'border-left:3px solid {accent};font-size:13px;line-height:1.5;'
            f'color:{_EMAIL_NAVY};">{html.escape(roast)}</div>'
        )
    return f"""
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
             style="margin:0 0 14px;border:1px solid #e3e8f0;border-radius:8px;
                    border-collapse:separate;background:#ffffff;">
        <tr><td style="padding:14px 16px;">
          <div style="font-size:11px;letter-spacing:.08em;text-transform:uppercase;
                      color:{accent};font-weight:700;margin:0 0 10px;">{html.escape(label)}</div>
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
            <tr>{img_cell}<td valign="top">
              <div style="font-size:17px;font-weight:700;color:{_EMAIL_NAVY};
                          line-height:1.25;">{team}</div>
              <div style="font-size:13px;color:{_EMAIL_MUTED};margin:3px 0 0;
                          line-height:1.45;">{html.escape(a.flavour)} — {html.escape(a.headline)}</div>
            </td></tr>
          </table>
          {roast_html}
        </td></tr>
      </table>"""


def _pick_email_awards(awards: list) -> list:
    """The one or two awards worth putting in front of someone before they
    open the PDF: the week's standout manager, plus a contrasting lowlight.
    Falls back to whatever's available so a league missing either still gets
    a sensible email."""
    by_title = {a.title: a for a in awards if a.winner_rid is not None}
    picked = []
    top = by_title.get("Top of the Pile") or next(
        (a for a in awards if a.hall == "fame" and a.winner_rid is not None), None)
    if top:
        picked.append((top, "Manager of the Week"))
    bottom = by_title.get("Bottom Feeders") or next(
        (a for a in awards if a.hall == "shame" and a.winner_rid is not None), None)
    if bottom and bottom is not top:
        picked.append((bottom, bottom.title))
    return picked


def render_email_html(season, awards, roasts, period_label: str,
                      *, pdf_attached: bool = True) -> str:
    """Compact, email-client-safe teaser. The full report rides along as the
    PDF attachment — this is what someone reads in the preview pane."""
    league = html.escape(season.name)
    period = html.escape(period_label)
    heroes = "".join(_email_hero(season, a, roasts, label)
                     for a, label in _pick_email_awards(awards))
    if pdf_attached:
        cta = (f'<p style="margin:0 0 6px;font-size:15px;color:{_EMAIL_NAVY};font-weight:600;">'
              f'Your full {period} is attached as a PDF.</p>'
              f'<p style="margin:0;font-size:13px;color:{_EMAIL_MUTED};line-height:1.5;">'
              f'Standings, luck index, the Decision Lab, waivers and trades — the lot.</p>')
    else:
        cta = (f'<p style="margin:0;font-size:13px;color:{_EMAIL_MUTED};line-height:1.5;">'
              f'The full breakdown is on its way.</p>')
    return f"""<html><body style="margin:0;padding:0;background:#eef1f6;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:#eef1f6;padding:20px 12px;">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
             style="width:100%;max-width:600px;background:#ffffff;border-radius:12px;
                    overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',
                    Helvetica,Arial,sans-serif;">
        <tr><td style="background:{_EMAIL_NAVY};padding:22px 24px;">
          <div style="font-size:11px;letter-spacing:.14em;text-transform:uppercase;
                      color:{_EMAIL_GREEN};font-weight:700;">{html.escape(EMAIL_BRAND)}</div>
          <div style="font-size:24px;font-weight:800;color:#ffffff;margin:6px 0 0;
                      line-height:1.15;">{period}</div>
          <div style="font-size:13px;color:#a9b6cc;margin:4px 0 0;">{league}</div>
        </td></tr>
        <tr><td style="padding:22px 24px 6px;">
          <p style="margin:0 0 16px;font-size:15px;line-height:1.5;color:{_EMAIL_NAVY};">
            Here's your weekly waiver wire report — the headlines first.</p>
          {heroes}
        </td></tr>
        <tr><td style="padding:6px 24px 24px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
                 style="background:#f7f9fc;border-radius:8px;border-collapse:separate;">
            <tr><td style="padding:16px 18px;">{cta}</td></tr>
          </table>
        </td></tr>
        <tr><td style="padding:0 24px 24px;">
          <div style="border-top:1px solid #e3e8f0;padding-top:14px;font-size:12px;
                      color:{_EMAIL_MUTED};line-height:1.5;">
            {html.escape(EMAIL_BRAND)} · {league}<br>
            Delivered every week of the season.</div>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""
