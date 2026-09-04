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
import textwrap

from . import stats as S
from . import waivers as W


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


def _relevant_weeks(season, month_stats, weeks=None) -> list:
    if weeks is not None:
        return weeks
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
                kind="monthly", month_stats=None, recap="", waiver_take="", weeks=None):
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
    weeks = _relevant_weeks(season, month_stats, weeks)
    upto_week = max(weeks) if (month_stats or kind == "week") else None
    if kind == "season":
        standings_label = "OVERALL STANDINGS  (final season)"
    elif kind == "week":
        standings_label = f"OVERALL STANDINGS  (through week {upto_week})"
    else:
        standings_label = "OVERALL STANDINGS  (through end of this month)"
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

    # Unlucky leaderboard (season only or if stats present)
    if season_stats:
        L.append("\n" + "-" * 64)
        L.append("  THE (UN)LUCKY LEADERBOARD  (luck = actual - deserved wins)")
        L.append("-" * 64)
        ranked = sorted(season_stats.values(), key=lambda s: s.luck_index, reverse=True)
        for s in ranked:
            tag = "lucky" if s.luck_index > 0 else ("robbed" if s.luck_index < 0 else "fair")
            L.append(f"  {season.team_name(s.roster_id)[:23]:<24}"
                     f"{s.luck_index:+6.1f}  ({tag}; all-play {s.all_play_w}-{s.all_play_l}, "
                     f"{s.avg_efficiency:.0f}% eff)")

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
.card .roast{margin-top:10px;font-size:14px;line-height:1.5;border-left:3px solid var(--green);padding-left:10px;color:#26344d;}
.podium{margin-top:10px;font-size:12px;color:#7a8aa3;}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;margin-top:8px;font-size:14px;}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid #eef2f7;}
th{background:var(--navy);color:#fff;font-size:12px;text-transform:uppercase;letter-spacing:.04em;}
td.num{text-align:right;font-variant-numeric:tabular-nums;}
td.spark{font-family:ui-monospace,Menlo,monospace;font-size:16px;letter-spacing:1px;white-space:nowrap;}
.lucky{color:var(--green);font-weight:700;} .robbed{color:var(--red);font-weight:700;}
h2{margin:30px 0 6px;font-size:15px;text-transform:uppercase;letter-spacing:.06em;color:#41506b;}
p{font-size:14px;line-height:1.5;margin:6px 0;}
ul{margin:4px 0 12px;padding-left:20px;font-size:14px;line-height:1.6;}
.charts-grid{display:flex;flex-direction:column;gap:20px;margin:14px 0 24px;}
.chart-box{background:#fff;border:1px solid #e3e8f0;border-radius:14px;padding:14px 16px;
          height:380px;position:relative;}
.chart-box h3{margin:0 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:#41506b;}
.chart-box .canvas-wrap{position:relative;height:330px;}
.chart-box.tall{height:480px;}
.chart-box.tall .canvas-wrap{height:430px;}
"""


# ----------------------------------------------------------------------
# Charts (Chart.js, fed by one shared DOSSIER_DATA block per report)
# ----------------------------------------------------------------------

_CHARTS_JS = """
Chart.register(ChartDataLabels);
(function() {
  const teams = DOSSIER_DATA.teams;
  const NAVY = '#15243b', GREEN = '#19c37d', RED = '#c0392b';

  const shortLabel = (name) => name.length > 16 ? name.slice(0, 15) + '…' : name;
  const luckPoints = teams.filter(t => t.allPlayWinPct !== null)
    .map(t => ({x: t.allPlayWinPct, y: t.seasonWinPct, label: t.name}))
    // Spread label rows: sort by x so neighbours alternate top/bottom instead
    // of stacking straight up when several teams cluster together on screen.
    .sort((a, b) => a.x - b.x);
  new Chart(document.getElementById('luckChart'), {
    type: 'scatter',
    data: {
      datasets: [
        { type: 'line', label: 'Fair (even)', data: [{x:0,y:0},{x:100,y:100}],
          borderColor: '#9aa7bd', borderDash: [6,4], pointRadius: 0, fill: false, order: 2,
          datalabels: { display: false } },
        { type: 'scatter', label: 'Teams', data: luckPoints, order: 1, pointRadius: 6,
          backgroundColor: luckPoints.map(p => p.y >= p.x ? GREEN : RED) }
      ]
    },
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
        tooltip: { callbacks: { label: (c) => c.raw.label
          ? (c.raw.label + ': ' + c.raw.y.toFixed(0) + '% actual vs ' + c.raw.x.toFixed(0) + '% all-play')
          : '' } }
      },
      scales: {
        x: { type: 'linear', title: { display: true, text: 'All-Play Win % (season)' }, min: 0, max: 100 },
        y: { type: 'linear', title: { display: true, text: 'Actual Win % (season)' }, min: 0, max: 100 }
      }
    }
  });

  __MONTH_CHART__

  const effSorted = teams.filter(t => t.seasonEfficiency !== null)
    .sort((a, b) => b.seasonEfficiency - a.seasonEfficiency);
  new Chart(document.getElementById('efficiencyChart'), {
    type: 'bar',
    data: {
      labels: effSorted.map(t => t.name),
      datasets: [{ label: 'Season avg efficiency %', data: effSorted.map(t => t.seasonEfficiency),
                   backgroundColor: NAVY }]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false }, datalabels: { display: false } },
      scales: { x: { title: { display: true, text: 'Lineup efficiency %' }, min: 0, max: 100 } }
    }
  });
})();
"""

_MONTH_CHART_JS = """
  const monthSorted = teams.filter(t => t.monthAboveAvg !== null)
    .sort((a, b) => b.monthAboveAvg - a.monthAboveAvg);
  new Chart(document.getElementById('monthDeltaChart'), {
    type: 'bar',
    data: {
      labels: monthSorted.map(t => t.name),
      datasets: [{ label: '+/- vs monthly average', data: monthSorted.map(t => t.monthAboveAvg),
                   backgroundColor: monthSorted.map(t => t.monthAboveAvg >= 0 ? GREEN : RED) }]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false }, datalabels: { display: false } },
      scales: { x: { title: { display: true, text: 'Points vs league average' } } }
    }
  });
"""


def _chart_data(season, season_stats, month_stats, upto_week=None) -> list:
    """One team list every chart reads from — add a stat once here, not
    once per chart."""
    teams = []
    for r in standings_rows(season, upto_week):
        rid = r["rid"]
        ss = (season_stats or {}).get(rid)
        ms = (month_stats or {}).get(rid)
        games = r["w"] + r["l"] + r["ties"]
        ap_games = (ss.all_play_w + ss.all_play_l) if ss else 0
        teams.append({
            "name": r["team"],
            "seasonWinPct": round(r["w"] / games * 100, 1) if games else 0.0,
            "allPlayWinPct": round(ss.all_play_w / ap_games * 100, 1) if ss and ap_games else None,
            "seasonEfficiency": ss.avg_efficiency if ss else None,
            "monthAboveAvg": ms.pts_above_avg if ms else None,
        })
    return teams


def _charts_html(season, season_stats, month_stats, upto_week=None) -> str:
    if not season_stats:
        return ""
    teams = _chart_data(season, season_stats, month_stats, upto_week)
    has_month = any(t["monthAboveAvg"] is not None for t in teams)
    month_box = ('<div class="chart-box"><h3>Monthly +/- vs Average</h3>'
                 '<div class="canvas-wrap"><canvas id="monthDeltaChart"></canvas></div></div>') if has_month else ""
    js = _CHARTS_JS.replace("__MONTH_CHART__", _MONTH_CHART_JS if has_month else "")
    # Defang any "</script>" a malicious team name could smuggle into the JSON payload.
    data_json = json.dumps({"teams": teams}).replace("</", "<\\/")
    return f"""
  <h2>Charts</h2>
  <div class="charts-grid">
    <div class="chart-box tall"><h3>Luck: All-Play Win% vs Actual Win%</h3>
      <div class="canvas-wrap"><canvas id="luckChart"></canvas></div></div>
    {month_box}
    <div class="chart-box"><h3>Season Lineup Efficiency</h3>
      <div class="canvas-wrap"><canvas id="efficiencyChart"></canvas></div></div>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2"></script>
  <script>
    const DOSSIER_DATA = {data_json};
    {js}
  </script>
"""


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
    return f"""<div class="card {a.hall}">
      <div class="head"><span>{html.escape(a.title)}</span></div>
      <div class="body">
        <div class="team">{team}</div>
        <div class="val">{html.escape(a.flavour)} — {val}</div>
        {roast_html}{podium}
      </div></div>"""


def render_html(season, awards, roasts, period_label, season_stats=None,
                kind="monthly", month_stats=None, recap="", waiver_take="",
                weeks=None, tier="normal"):
    fame = [a for a in awards if a.hall == "fame"]
    shame = [a for a in awards if a.hall == "shame"]
    title = period_label
    full_report = tier != "free"

    recap_html = f'<div class="recap">{html.escape(recap)}</div>' if recap else ""

    fame_cards = "".join(_card(season, a, roasts) for a in fame)
    shame_cards = "".join(_card(season, a, roasts) for a in shame)

    # Standings table (with weekly-form sparkline column). Frozen at this
    # period's last played week for monthly reports, not today's live totals.
    weeks = _relevant_weeks(season, month_stats, weeks)
    upto_week = max(weeks) if (month_stats or kind == "week") else None
    if kind == "season":
        standings_heading = "Overall Standings &amp; Form (final season)"
    elif kind == "week":
        standings_heading = f"Overall Standings &amp; Form (through week {upto_week})"
    else:
        standings_heading = "Overall Standings &amp; Form (through end of this month)"
    st_rows = ""
    closest_html = ""
    waiver_html = ""
    luck_html = ""
    month_html = ""
    charts_html = ""

    if full_report:
        for i, r in enumerate(standings_rows(season, upto_week), 1):
            wl = f"{r['w']}-{r['l']}" + (f"-{r['ties']}" if r['ties'] else "")
            scores = [season.weeks[wk][r["rid"]].points for wk in weeks if r["rid"] in season.weeks.get(wk, {})]
            spark = html.escape(_sparkline(scores))
            st_rows += (f"<tr><td>{i}</td><td>{html.escape(r['team'])}</td>"
                        f"<td>{wl}</td><td class='num'>{r['pf']:.1f}</td>"
                        f"<td class='num'>{r['pa']:.1f}</td><td class='spark'>{spark}</td></tr>")

        cg = _closest_game(season, weeks)
        if cg:
            margin, wk, ra, pa, rb, pb = cg
            winner, loser = (ra, rb) if pa > pb else (rb, ra)
            hi, lo = max(pa, pb), min(pa, pb)
            closest_html = (f'<div class="closest"><strong>Closest race —</strong> '
                            f'Week {wk}: {html.escape(season.team_name(winner))} beat '
                            f'{html.escape(season.team_name(loser))} by just {margin:.1f} '
                            f'({hi:.1f}–{lo:.1f})</div>')

        charts_html = _charts_html(season, season_stats, month_stats, upto_week)

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

        if season_stats:
            rows = ""
            for s in sorted(season_stats.values(), key=lambda x: x.luck_index, reverse=True):
                cls = "lucky" if s.luck_index > 0 else ("robbed" if s.luck_index < 0 else "")
                rows += (f"<tr><td>{html.escape(season.team_name(s.roster_id))}</td>"
                         f"<td class='num {cls}'>{s.luck_index:+.1f}</td>"
                         f"<td class='num'>{s.all_play_w}-{s.all_play_l}</td>"
                         f"<td class='num'>{s.avg_efficiency:.0f}%</td></tr>")
            luck_html = f"""<h2>The (Un)Lucky Leaderboard</h2>
              <table><tr><th>Team</th><th>Luck (W vs deserved)</th>
              <th>All-Play</th><th>Efficiency</th></tr>{rows}</table>"""

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

    standings_section = (f"<h2>{standings_heading}</h2>"
                        f"<table><tr><th>#</th><th>Team</th><th>W-L</th><th>PF</th><th>PA</th><th>Form</th></tr>{st_rows}</table>") \
        if full_report else ""

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

  <div class="bar fame">🏆 Hall of Fame</div>
  <div class="grid">{fame_cards}</div>

  <div class="bar shame">💀 Hall of Shame</div>
  <div class="grid">{shame_cards}</div>

  {month_html}

  {closest_html}

  {standings_section}

  {charts_html}

  {luck_html}
  {waiver_html}
</div></body></html>"""
