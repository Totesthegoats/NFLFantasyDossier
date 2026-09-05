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
                kind="monthly", month_stats=None, recap="", waiver_take=""):
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


def _weekly_chart_specs(season, week_stats: dict) -> list:
    """Weekly charts: this-week luck scatter (all-play% vs points scored,
    colored by whether the score beat the week's median — a single week's
    actual W/L is binary and the season chart's diagonal-line "fair" framing
    doesn't translate to one week, so median is the more useful colour
    signal here), points-vs-week-average bar, this week's efficiency bar —
    the weekly-scoped analogues of the season/monthly charts."""
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
    scatter = [s for s in specs if s["kind"] == "scatter"]
    bar = [s for s in specs if s["kind"] == "bar"]
    # Defang any "</script>" a malicious team name could smuggle into the JSON payload.
    data_json = json.dumps({"scatterCharts": scatter, "barCharts": bar}).replace("</", "<\\/")
    return f"""
  <h2>Charts</h2>
  <div class="charts-grid">{''.join(boxes)}</div>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2"></script>
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


def render_html(season, awards, roasts, period_label, season_stats=None,
                kind="monthly", month_stats=None, recap="", waiver_take="", tier="normal"):
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

  {_charts_html(_season_chart_specs(season, season_stats, month_stats, upto_week))}

  <div class="screen-only">{power_rank_html}{luck_html}{median_html}</div>
  {draft_value_html}
  {waiver_html}
</div></body></html>"""


def render_weekly_html(season, awards, roasts, period_label, week, rivalry_matchups=None, recap="",
                       tier="normal") -> str:
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
    charts_html = _charts_html(_weekly_chart_specs(season, week_stats))

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
  {charts_html}
  {rivalry_html}
</div></body></html>"""
