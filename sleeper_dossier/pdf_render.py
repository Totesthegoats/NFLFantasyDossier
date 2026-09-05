"""
pdf_render.py — Paginated, landscape A4 PDF renderer.

Generates a fixed-page HTML document where every section is exactly one
.page div (297mm × 210mm, overflow:hidden). Playwright prints it with
preferCSSPageSize:true so the @page declaration controls physical size.

SEPARATE from render.py (the web/scrollable renderer) so both remain
independently viewable and debuggable:
  render.py  → --html  (one long scrollable page, web/email viewing)
  pdf_render → --pdf   (multi-page magazine format, primary deliverable)

Architecture: a section registry (ordered list of Section descriptors)
drives page assembly. Adding, reordering, or removing a page is a single
edit to the WEEKLY_SECTIONS / MONTHLY_SECTIONS / SEASON_SECTIONS lists —
no template surgery.
"""

from __future__ import annotations
import html as _html
import json
from dataclasses import dataclass
from typing import Callable

from . import stats as S
from . import images as IMG
from . import history as H
from . import waivers as W
from . import decision as DEC
from .render import (
    standings_rows, _relevant_weeks, _luck_tag, _week_results,
    _season_chart_specs, _award_image_html, _img_tag,
)

# ──────────────────────────────────────────────────────────────────────────────
# Page dimensions (CSS units match @page declaration below)
# ──────────────────────────────────────────────────────────────────────────────

# At 96 dpi:  297mm ≈ 1122px,  210mm ≈ 793px
# Page inner content (12mm side padding = 90px; 16mm bottom = 60px; header ~33px;
# subhead ~30px; footer ~18px): ≈ 1030px wide × 640px tall available

# Full-page scatter (season/monthly charts page)
_SCATTER_W = 480
_SCATTER_H = 390

# Full-page bar (season/monthly charts page)
_BAR_W = 410
_BAR_H = 215

# Stacked scatter — full page width below a table (page 5: Luck)
_LUCK_SCATTER_W = 1010
_LUCK_SCATTER_H = 290

# Stacked bar — full page width below a table (page 6: Median)
_MED_BAR_W = 1010
_MED_BAR_H = 270

# Single bar below leaderboard table (page 4 — efficiency only, full width)
_SMALL_BAR_W = 1010
_SMALL_BAR_H = 220

# Full-width multi-line trajectory chart (page 7 — raw scores)
_TRAJ_W = 1010
_TRAJ_H = 470

# Full-width bump/ranking chart (page 5 — rank positions over weeks)
_BUMP_W = 1010
_BUMP_H = 510

# Transactions HQ — FAAB scatter on its own dedicated page (full width)
_FAAB_SCATTER_W = 1010
_FAAB_SCATTER_H = 380

# Transactions HQ — small bar charts (remaining FAAB, bandwagons, activity)
_TXN_BAR_W = 460
_TXN_BAR_H = 240

# Decision Lab charts (2-up grid, each half the content width)
_DEC_CHART_W = 470
_DEC_CHART_H = 260
_BW_BAR_W  = 430  # bandwagon bars (stacked pair, half width each)
_BW_BAR_H  = 110


# ──────────────────────────────────────────────────────────────────────────────
# CSS
# ──────────────────────────────────────────────────────────────────────────────

_PDF_CSS = """
/* ── tokens ──────────────────────────────────────────────────────── */
:root {
  --navy:  #15243b;
  --green: #19c37d;
  --red:   #c0392b;
  --ink:   #0d1626;
  --paper: #f7f9fc;
  --muted: #7a8aa3;
}
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
       background: #e8eaed; }

/* ── physical page ───────────────────────────────────────────────── */
@page { size: 297mm 210mm; margin: 0; }

/* ── page container — the hard boundary ─────────────────────────── */
/* The .page div is exactly 210mm tall in document flow (no content
   influence) so the paginator places physical pages every 210mm.
   All actual content lives in .page-inner which is position:absolute
   and therefore removed from flow — it clips at the page boundary
   visually while the paginator never sees the inner height. */
.page {
  width: 297mm;
  height: 210mm;
  position: relative;
  background: #fff;
  font-size: 12px;
  color: var(--ink);
  margin: 0 auto 12px;         /* gap between pages in browser preview only */
}
.page-inner {
  position: absolute;
  inset: 0;
  padding: 0 12mm 16mm 12mm;
  overflow: hidden;
}
/* Break BEFORE each page except the first — avoids the trailing blank
   page that appears when break-after: page is on the last element. */
.page + .page {
  break-before: page;
  page-break-before: always;
}
/* Strip screen-only chrome from print: the margin-bottom (preview gap)
   and body background would otherwise contribute to layout height and
   produce a trailing blank page in Chromium's print output. */
@media print {
  body { background: #fff; }
  .page { margin: 0 auto; }
}

/* ── cover page (styles go on .page-inner, not .page--cover itself) */
.page--cover .page-inner {
  background: var(--navy);
  color: #fff;
  padding: 18mm 20mm;
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.cover-eyebrow { font-size: 10px; text-transform: uppercase; letter-spacing: .14em;
                 opacity: .6; margin-bottom: 12px; }
.cover-title   { font-size: 56px; font-weight: 900; font-style: italic;
                 letter-spacing: -2px; line-height: 1; margin-bottom: 6px; }
.cover-sub     { font-size: 22px; font-weight: 600; opacity: .85; margin-bottom: 28px; }
.cover-divider { width: 48px; height: 3px; background: var(--green); margin-bottom: 20px; }
.cover-meta    { font-size: 11px; opacity: .65; font-family: ui-monospace, Menlo, monospace;
                 line-height: 1.9; }
.cover-stripe  { position: absolute; right: 0; top: 0; bottom: 0; width: 52mm;
                 background: rgba(255,255,255,.04); pointer-events: none; }
.cover-accent  { position: absolute; right: 20mm; top: 18mm; font-size: 120px;
                 font-weight: 900; opacity: .04; line-height: 1; pointer-events: none; }

/* ── section header band (full-bleed across top) ─────────────────── */
.page-header {
  display: flex;
  align-items: center;
  gap: 10px;
  background: var(--navy);
  color: #fff;
  padding: 7px 14px;
  font-weight: 700;
  font-size: 13px;
  margin: 0 -12mm 8px -12mm;  /* negative margins → full-bleed */
}
.page-header .num { opacity: .45; font-size: 10px; font-variant-numeric: tabular-nums; }

/* ── league / period sub-band ────────────────────────────────────── */
.page-subhead {
  display: flex;
  justify-content: space-between;
  font-size: 9px;
  color: var(--muted);
  margin-bottom: 10px;
  text-transform: uppercase;
  letter-spacing: .06em;
}

/* ── page footer (absolute, inside .page) ────────────────────────── */
.page-footer {
  position: absolute;
  left: 12mm;
  right: 12mm;
  bottom: 5mm;
  display: flex;
  justify-content: space-between;
  font-size: 8px;
  color: var(--muted);
}

/* ── award cards — bigger so they fill the landscape page better ─── */
.cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 11px;
  align-content: start;
}
.award-card { background: #fff; border: 1px solid #e3e8f0; border-radius: 10px; overflow: hidden; }
.award-card .head { padding: 7px 12px; font-weight: 700; font-size: 11px; color: #fff; }
.award-card.fame  .head { background: var(--navy); }
.award-card.shame .head { background: var(--red); }
.award-card .body { padding: 10px 12px; }
.award-card .team { font-size: 15px; font-weight: 800; font-style: italic; }
.award-card .val  { color: #5b6b85; font-size: 10px; margin-top: 3px; }
.award-card .roast { margin-top: 7px; font-size: 10px; line-height: 1.45;
                     border-left: 2px solid var(--green); padding-left: 8px; color: #26344d; }
.award-card .podium { margin-top: 6px; font-size: 9px; color: #7a8aa3; }
.award-card .detail { margin-top: 3px; font-size: 9px; color: #5b6b85; font-style: italic; }
.award-card.unavailable { opacity: 0.45; }
.award-card.unavailable .head { background: #7a8aa3; }
.avatar-row { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
.avatar     { width: 30px; height: 30px; object-fit: cover; flex-shrink: 0; background: #eef2f7; }
.avatar.manager { border-radius: 50%; }
.avatar.player  { border-radius: 5px; }
.avatar.sm      { width: 20px; height: 20px; }
.trade-swap { font-size: 11px; color: #9aa7bd; }

/* ── stats tables ────────────────────────────────────────────────── */
.stats-table { width: 100%; border-collapse: collapse; font-size: 10px; background: #fff; }
.stats-table th { background: var(--navy); color: #fff; padding: 5px 7px;
                  text-align: left; font-size: 9px; text-transform: uppercase; letter-spacing: .04em; }
.stats-table td { padding: 3px 7px; border-bottom: 1px solid #eef2f7; vertical-align: middle; }
.stats-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
.stats-table tr:nth-child(even) { background: #f9fafb; }
.lucky  { color: var(--green); font-weight: 700; }
.robbed { color: var(--red);   font-weight: 700; }
.spark  { font-family: ui-monospace, Menlo, monospace; font-size: 13px; letter-spacing: 1px; }

/* ── chart layout ────────────────────────────────────────────────── */
.charts-row { display: flex; gap: 12px; }
.chart-main { flex: 1.15; }
.charts-side { flex: 1; display: flex; flex-direction: column; gap: 10px; }
.chart-wrap { background: #fff; border: 1px solid #e3e8f0; border-radius: 9px;
              padding: 9px 11px; }
.chart-wrap h4 { margin: 0 0 2px; font-size: 9px; text-transform: uppercase;
                 letter-spacing: .05em; color: #41506b; }
.chart-caption { font-size: 8px; color: #7a8aa3; margin: 0 0 7px; }

/* ── column layouts ──────────────────────────────────────────────── */
.cols-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
.cols-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.cols-w { display: grid; grid-template-columns: 1.4fr 1fr; gap: 14px; }
h3.section-label { margin: 0 0 6px; font-size: 9px; text-transform: uppercase;
                   letter-spacing: .06em; color: #41506b; }

/* ── rivalry cards — 2-col, bigger, with season history ─────────── */
.rivalry-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 11px; }
.rivalry-card { background: #fff; border: 1px solid #e3e8f0;
                border-left: 4px solid var(--navy); border-radius: 8px; padding: 10px 13px; }
.rivalry-card .matchup { font-size: 13px; font-weight: 700; }
.rivalry-card .matchup .winner { color: var(--green); }
.rivalry-card .history { margin-top: 5px; font-size: 10px; color: #5b6b85; }
.rivalry-card .season-games { margin-top: 6px; font-size: 9px; color: #41506b; }
.rivalry-card .season-games .game-row { padding: 2px 0; border-bottom: 1px solid #f3f6fa; }
.rivalry-card .season-games .game-row:last-child { border-bottom: none; }
.season-subhead { font-size: 8px; text-transform: uppercase; letter-spacing: .06em;
                  color: var(--muted); margin: 5px 0 3px; }

/* ── leaderboard page: table full-width, chart below ────────────── */
.lb-chart { margin-top: 12px; }

/* ── stacked table + chart: table on top, chart fills remaining space */
.page-stacked .stacked-chart { margin-top: 12px; }

/* ── trajectory chart (full width, page 7) ──────────────────────── */
.traj-wrap { background: #fff; border: 1px solid #e3e8f0; border-radius: 9px;
             padding: 10px 14px; }
.traj-wrap h4 { margin: 0 0 2px; font-size: 10px; text-transform: uppercase;
                letter-spacing: .05em; color: #41506b; }
.traj-caption { font-size: 8px; color: var(--muted); margin: 0 0 8px; }

/* ── Transactions HQ ─────────────────────────────────────────────── */
/* Ledger table: tighter rows since there can be many pickups */
.txn-table { width: 100%; border-collapse: collapse; font-size: 8.5px; }
.txn-table th { background: var(--navy); color: #fff; padding: 4px 6px;
                text-align: left; font-weight: 600; white-space: nowrap; }
.txn-table td { padding: 3px 6px; border-bottom: 1px solid #f0f4f9;
                vertical-align: middle; }
.txn-table tr:nth-child(even) td { background: #fafbfd; }
.txn-table .hit-yes { color: var(--green); font-weight: 700; }
.txn-table .hit-no  { color: var(--muted); }
.txn-table .faab-val { font-weight: 600; }
/* Bottom 3-col chart band */
.txn-charts { display: grid; grid-template-columns: 2fr 1.4fr 1fr; gap: 10px; margin-top: 10px; }
.txn-charts .chart-wrap { background: #fff; border: 1px solid #e3e8f0; border-radius: 8px; padding: 8px 10px; }
/* Bandwagon twin bars stacked */
.bw-stack { display: flex; flex-direction: column; gap: 8px; }
/* Trade overview table — more compact than the transfers ledger */
.trade-tbl { width: 100%; border-collapse: collapse; font-size: 7.5px; }
.trade-tbl th { background: var(--navy); color: #fff; padding: 3px 6px;
                text-align: left; font-weight: 600; }
.trade-tbl td { padding: 2px 6px; border-bottom: 1px solid #f0f4f9; vertical-align: top; }
.trade-tbl tr:nth-child(even) td { background: #fafbfd; }
.trade-tbl .wk-cell { white-space: nowrap; color: var(--muted); }

/* Trade ledger: wider cards */
.trade-ledger { display: flex; flex-direction: column; gap: 8px; }
.trade-card { background: #fff; border: 1px solid #e3e8f0; border-radius: 8px; padding: 8px 12px; }
.trade-card .trade-wk  { font-size: 8px; color: var(--muted); margin-bottom: 4px; }
.trade-card .trade-sides { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.trade-side { border-left: 3px solid #e3e8f0; padding-left: 8px; }
.trade-side .side-mgr   { font-weight: 700; font-size: 10px; margin-bottom: 2px; }
.trade-side .side-gets  { font-size: 9px; color: #26344d; }
.trade-side .side-picks { font-size: 8.5px; color: #7a8aa3; }
.trade-side .side-pts   { font-size: 9px; margin-top: 3px; }
.side-pts .pts-pos { color: var(--green); font-weight: 700; }
.side-pts .pts-neg { color: var(--red); font-weight: 700; }
/* Grade band below the trade card */
.trade-card .grade-band { margin-top: 6px; display: flex; gap: 12px;
                          font-size: 9px; padding-top: 5px;
                          border-top: 1px solid #f0f4f9; }
.grade-band .grade-winner { color: var(--green); font-weight: 700; }
.grade-band .grade-loser  { color: var(--red); }
/* Trade bottom chart */
.trade-bottom { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 10px; }
/* Empty state */
.empty-state { text-align: center; color: var(--muted); font-size: 12px;
               padding: 40px 20px; }

/* ── notices / callout boxes ─────────────────────────────────────── */
.callout { background: #fff; border-left: 3px solid var(--green); border-radius: 7px;
           padding: 6px 10px; font-size: 9px; margin-bottom: 9px; }
.callout.red { border-color: var(--red); }
"""


# ──────────────────────────────────────────────────────────────────────────────
# Chart JS (PDF variant)
# Differences from render.py's _GENERIC_CHARTS_JS:
#   • animation.duration: 0 + onComplete counter (Playwright waits on these)
#   • responsive: false — responsive canvases collapse in print context
#   • Explicit canvas width/height set before Chart constructor
# ──────────────────────────────────────────────────────────────────────────────

_PDF_CHARTS_JS = f"""
Chart.register(ChartDataLabels);
(function() {{
  const NAVY = '#15243b', GREEN = '#19c37d', RED = '#c0392b', GRAY = '#9aa7bd';
  const shortLabel = (name) => name.length > 20 ? name.slice(0, 19) + '\\u2026' : name;

  /* Per-spec sizes — each spec carries cfg.width / cfg.height so different
     pages can use the same chart kind at different canvas sizes. */
  const DSW = {_SCATTER_W}, DSH = {_SCATTER_H};  /* defaults */
  const DBW = {_BAR_W},     DBH = {_BAR_H};

  window.__chartsExpected = (DOSSIER_DATA.scatterCharts     || []).length
                           + (DOSSIER_DATA.barCharts         || []).length
                           + (DOSSIER_DATA.trajectoryCharts  || []).length
                           + (DOSSIER_DATA.decisionCharts    || []).length;
  window.__chartsRendered = 0;

  (DOSSIER_DATA.scatterCharts || []).forEach(function(cfg) {{
    const canvas = document.getElementById(cfg.id);
    if (!canvas) return;
    canvas.width  = cfg.width  || DSW;
    canvas.height = cfg.height || DSH;
    const points  = cfg.points.slice().sort((a, b) => a.x - b.x);
    const datasets = [];
    if (cfg.withDiagonal) {{
      datasets.push({{ type: 'line',
        data: [{{x: cfg.xMin, y: cfg.yMin}}, {{x: cfg.xMax, y: cfg.yMax}}],
        borderColor: GRAY, borderDash: [6, 4], pointRadius: 0, fill: false, order: 2,
        datalabels: {{ display: false }} }});
    }}
    datasets.push({{ type: 'scatter', data: points, order: 1,
      pointRadius: points.map(function(p) {{ return p.radius || 5; }}),
      backgroundColor: points.map(p => p.good === null ? NAVY : (p.good ? GREEN : RED)) }});
    new Chart(canvas, {{
      type: 'scatter',
      data: {{ datasets: datasets }},
      options: {{
        responsive: false,
        animation: {{ duration: 0, onComplete: function() {{ window.__chartsRendered++; }} }},
        layout: {{ padding: {{ top: 22, bottom: 22, left: cfg.leftPadding || 16, right: 100 }} }},
        plugins: {{
          legend: {{ display: false }},
          datalabels: {{
            /* Pill-style backgrounds stop labels bleeding into each other.
               showLabel:false on a point suppresses its label (used for
               dense historical scatter charts to label only outliers). */
            display: function(ctx) {{
              var v = ctx.dataset.data[ctx.dataIndex];
              return !!(v && v.label && v.showLabel !== false);
            }},
            align: function(ctx) {{
              var p = ctx.dataset.data[ctx.dataIndex];
              if (!p || !p.label) return 'top';
              var allX = ctx.dataset.data.filter(function(d) {{ return d.label; }}).map(function(d) {{ return d.x; }});
              var minX = Math.min.apply(null, allX.concat([0]));
              var maxX = Math.max.apply(null, allX.concat([1]));
              var rng = maxX - minX;
              if (rng > 0 && (p.x - minX) < rng * 0.20) return 'right';
              if (rng > 0 && (p.x - minX) > rng * 0.65) return 'left';
              return 'top';
            }},
            anchor: 'center',
            offset: 5,
            color: NAVY,
            font: {{ size: 8, weight: 600 }},
            backgroundColor: 'rgba(255,255,255,0.88)',
            borderRadius: 3,
            padding: {{ top: 1, bottom: 2, left: 4, right: 4 }},
            formatter: function(v) {{ return v.label ? shortLabel(v.label) : ''; }}
          }},
          tooltip: {{ callbacks: {{ label: (c) => c.raw.tooltip || '' }} }}
        }},
        scales: {{
          x: {{ type: 'linear', title: {{ display: true, text: cfg.xLabel, font: {{size:9}} }},
                min: cfg.xMin, max: cfg.xMax }},
          y: {{ type: 'linear', title: {{ display: true, text: cfg.yLabel, font: {{size:9}} }},
                min: cfg.yMin, max: cfg.yMax }}
        }}
      }}
    }});
  }});

  (DOSSIER_DATA.barCharts || []).forEach(function(cfg) {{
    const canvas = document.getElementById(cfg.id);
    if (!canvas) return;
    canvas.width  = cfg.width  || DBW;
    canvas.height = cfg.height || DBH;
    const sorted = cfg.bars.slice().sort((a, b) => b.value - a.value);
    new Chart(canvas, {{
      type: 'bar',
      data: {{
        labels: sorted.map(b => b.label),
        datasets: [{{ data: sorted.map(b => b.value),
          backgroundColor: cfg.colorBySign ? sorted.map(b => b.value >= 0 ? GREEN : RED) : NAVY }}]
      }},
      options: {{
        indexAxis: 'y',
        responsive: false,
        animation: {{ duration: 0, onComplete: function() {{ window.__chartsRendered++; }} }},
        plugins: {{ legend: {{ display: false }}, datalabels: {{ display: false }} }},
        scales: {{ x: {{ title: {{ display: true, text: cfg.xLabel, font: {{size:9}} }},
                        min: cfg.xMin, max: cfg.xMax }} }}
      }}
    }});
  }});

  /* ── Shared color palette for multi-team charts ─────────────────── */
  const TEAM_COLORS = ['#15243b','#19c37d','#e74c3c','#f39c12','#3498db',
                       '#9b59b6','#27ae60','#e91e63','#00bcd4','#ff5722',
                       '#607d8b','#795548'];
  const TRAJ_COLORS = TEAM_COLORS;   /* alias kept for trajectory charts */

  /* ── Bump / ranking chart: rank positions over weeks ────────────── */
  (DOSSIER_DATA.bumpCharts || []).forEach(function(cfg) {{
    const canvas = document.getElementById(cfg.id);
    if (!canvas) return;
    canvas.width  = cfg.width  || {_BUMP_W};
    canvas.height = cfg.height || {_BUMP_H};

    const datasets = cfg.series.map(function(s, i) {{
      const col = TEAM_COLORS[i % TEAM_COLORS.length];
      return {{
        label: s.label,
        data: s.data,
        borderColor: col,
        backgroundColor: col,
        borderWidth: 2.2,
        pointRadius: 7,
        pointHoverRadius: 9,
        tension: 0.35,
        spanGaps: false,
        /* Per-dataset datalabels: show manager name only at their last known rank */
        datalabels: {{
          display: function(ctx) {{
            var d = ctx.dataset.data;
            var idx = ctx.dataIndex;
            for (var j = d.length - 1; j >= 0; j--) {{
              if (d[j] !== null && d[j] !== undefined) {{ return j === idx; }}
            }}
            return false;
          }},
          anchor: 'end',
          align: 'right',
          offset: 6,
          color: col,
          font: {{ size: 8, weight: 700 }},
          backgroundColor: 'rgba(255,255,255,0.9)',
          borderRadius: 3,
          padding: {{ top: 1, bottom: 1, left: 4, right: 4 }},
          formatter: function(v, ctx) {{ return ctx.dataset.label; }},
        }},
      }};
    }});

    new Chart(canvas, {{
      type: 'line',
      data: {{ labels: cfg.weeks, datasets: datasets }},
      options: {{
        responsive: false,
        animation: {{ duration: 0, onComplete: function() {{ window.__chartsRendered++; }} }},
        layout: {{ padding: {{ top: 14, bottom: 14, left: 10, right: 130 }} }},
        plugins: {{
          legend: {{ display: false }},
          /* Global datalabels off — each dataset configures its own above */
          datalabels: {{ display: false }},
        }},
        scales: {{
          x: {{
            title: {{ display: true, text: 'Week', font: {{size:9}} }},
            grid: {{ color: 'rgba(0,0,0,0.06)' }},
            ticks: {{ font: {{size:9}} }},
          }},
          y: {{
            reverse: true,
            min: 0.5,
            max: cfg.numTeams + 0.5,
            ticks: {{
              stepSize: 1,
              callback: function(v) {{ return Number.isInteger(v) ? '#' + v : ''; }},
              font: {{ size: 9 }},
            }},
            title: {{ display: true, text: 'Rank (1 = best)', font: {{size:9}} }},
            grid: {{ color: 'rgba(0,0,0,0.06)' }},
          }},
        }},
      }},
    }});
  }});

  /* ── Season trajectory: multi-line chart, one dataset per manager ── */
  (DOSSIER_DATA.trajectoryCharts || []).forEach(function(cfg) {{
    const canvas = document.getElementById(cfg.id);
    if (!canvas) return;
    canvas.width  = cfg.width  || {_TRAJ_W};
    canvas.height = cfg.height || {_TRAJ_H};
    window.__chartsExpected;  /* already counted above */
    const datasets = cfg.series.map(function(s, i) {{
      return {{
        label: s.label,
        data: s.data,
        borderColor: TRAJ_COLORS[i % TRAJ_COLORS.length],
        backgroundColor: 'transparent',
        borderWidth: 1.5,
        pointRadius: 2.5,
        tension: 0.2,
        spanGaps: true,
        datalabels: {{ display: false }},
      }};
    }});
    datasets.push({{
      label: 'League Avg',
      data: cfg.avgData,
      borderColor: '#bdc3c7',
      borderWidth: 2.5,
      borderDash: [5, 5],
      pointRadius: 0,
      tension: 0.2,
      spanGaps: true,
      datalabels: {{ display: false }},
    }});
    new Chart(canvas, {{
      type: 'line',
      data: {{ labels: cfg.weeks, datasets: datasets }},
      options: {{
        responsive: false,
        animation: {{ duration: 0, onComplete: function() {{ window.__chartsRendered++; }} }},
        plugins: {{
          legend: {{ display: true, position: 'bottom',
                    labels: {{ font: {{ size: 7 }}, boxWidth: 10, padding: 5 }} }},
          datalabels: {{ display: false }},
          tooltip: {{ callbacks: {{ label: (c) => c.dataset.label + ': ' + (c.parsed.y != null ? c.parsed.y.toFixed(1) : '—') }} }},
        }},
        scales: {{
          x: {{ title: {{ display: true, text: 'Week', font: {{size:8}} }} }},
          y: {{ title: {{ display: true, text: 'Points scored', font: {{size:8}} }},
                min: cfg.yMin, max: cfg.yMax }}
        }}
      }}
    }});
  }});

  /* ── Decision Lab charts ─────────────────────────────────────────────── */
  (DOSSIER_DATA.decisionCharts || []).forEach(function(cfg) {{
    const canvas = document.getElementById(cfg.id);
    if (!canvas) return;
    canvas.width  = cfg.width  || {_DEC_CHART_W};
    canvas.height = cfg.height || {_DEC_CHART_H};

    /* ── Quadrant scatter: X=expected/projected, Y=actual ── */
    if (cfg.type === 'quadrant') {{
      const empty = cfg.emptyMessage;
      if (empty) {{
        const ctx2d = canvas.getContext('2d');
        ctx2d.fillStyle = '#9aa7bd';
        ctx2d.font = '12px sans-serif';
        ctx2d.textAlign = 'center';
        ctx2d.fillText(empty, canvas.width / 2, canvas.height / 2);
        window.__chartsRendered++;
        return;
      }}
      const pts = (cfg.points || []).slice().sort(function(a, b) {{ return a.x - b.x; }});
      const datasets = [];
      /* Diagonal reference line */
      var allV = pts.map(function(p) {{ return p.x; }}).concat(pts.map(function(p) {{ return p.y; }}));
      var diagMin = cfg.xMin != null ? cfg.xMin : (Math.min.apply(null, allV) || 0);
      var diag95  = cfg.xMax != null ? cfg.xMax : (Math.max.apply(null, allV) || 50);
      datasets.push({{ type: 'line',
        data: [{{x: diagMin, y: diagMin}}, {{x: diag95, y: diag95}}],
        borderColor: '#bdc3c7', borderDash: [6, 4], pointRadius: 0, fill: false, order: 2,
        datalabels: {{ display: false }} }});
      datasets.push({{ type: 'scatter', data: pts, order: 1,
        pointRadius: pts.map(function(p) {{ return p.r || 5; }}),
        backgroundColor: pts.map(function(p) {{
          return p.isWinner ? GREEN : (p.isBust ? RED : NAVY);
        }}) }});
      new Chart(canvas, {{
        type: 'scatter',
        data: {{ datasets: datasets }},
        options: {{
          responsive: false,
          animation: {{ duration: 0, onComplete: function() {{ window.__chartsRendered++; }} }},
          layout: {{ padding: {{ top: 20, bottom: 20, left: 16, right: 16 }} }},
          plugins: {{
            legend: {{ display: false }},
            datalabels: {{
              display: function(ctx) {{
                var v = ctx.dataset.data[ctx.dataIndex];
                return !!(v && v.label && v.showLabel);
              }},
              align: function(ctx) {{
                var p = ctx.dataset.data[ctx.dataIndex];
                if (!p || !p.label) return 'top';
                var allX = ctx.dataset.data.filter(function(d) {{ return d.label; }}).map(function(d) {{ return d.x; }});
                var minX = Math.min.apply(null, allX.concat([0]));
                var maxX = Math.max.apply(null, allX.concat([1]));
                var rng = maxX - minX;
                if (rng > 0 && (p.x - minX) < rng * 0.22) return 'right';
                if (rng > 0 && (p.x - minX) > rng * 0.65) return 'left';
                return 'top';
              }},
              anchor: 'center', offset: 5, color: NAVY,
              font: {{ size: 8, weight: 600 }},
              backgroundColor: 'rgba(255,255,255,0.88)',
              borderRadius: 3,
              padding: {{ top: 1, bottom: 2, left: 4, right: 4 }},
              formatter: function(v) {{ return v.label ? shortLabel(v.label) : ''; }}
            }},
            tooltip: {{ callbacks: {{ label: function(c) {{ return c.raw.tooltip || ''; }} }} }}
          }},
          scales: {{
            x: {{ type: 'linear', title: {{ display: true, text: cfg.xLabel || 'X', font: {{size:9}} }},
                  min: cfg.xMin, max: cfg.xMax }},
            y: {{ type: 'linear', title: {{ display: true, text: cfg.yLabel || 'Y', font: {{size:9}} }},
                  min: cfg.yMin, max: cfg.yMax }}
          }}
        }}
      }});
    }}

    /* ── Dumbbell: grouped hbar — projected (gray) and actual (green/red) ── */
    else if (cfg.type === 'dumbbell') {{
      var dumbbells = cfg.dumbbells || [];
      var labels   = dumbbells.map(function(d) {{ return d.name; }});
      var proj     = dumbbells.map(function(d) {{ return d.projected; }});
      var actual   = dumbbells.map(function(d) {{ return d.actual; }});
      new Chart(canvas, {{
        type: 'bar',
        data: {{
          labels: labels,
          datasets: [
            {{ label: 'Projected', data: proj,   backgroundColor: GRAY, barPercentage: 0.45 }},
            {{ label: 'Actual',    data: actual,  barPercentage: 0.45,
               backgroundColor: actual.map(function(v, i) {{
                 return v >= proj[i] ? GREEN : RED;
               }}) }}
          ]
        }},
        options: {{
          indexAxis: 'y',
          responsive: false,
          animation: {{ duration: 0, onComplete: function() {{ window.__chartsRendered++; }} }},
          plugins: {{
            legend: {{ display: true, position: 'bottom',
                      labels: {{ font: {{ size: 8 }}, boxWidth: 10, padding: 6 }} }},
            datalabels: {{
              display: true, anchor: 'end', align: 'right',
              color: NAVY, font: {{ size: 8 }},
              formatter: function(v) {{ return v != null ? v.toFixed(1) : ''; }}
            }}
          }},
          scales: {{
            x: {{ title: {{ display: true, text: cfg.xLabel || 'Fantasy Points', font: {{size:9}} }},
                  min: 0, suggestedMax: cfg.suggestedMax || 50 }},
            y: {{ ticks: {{ font: {{ size: 8 }} }} }}
          }}
        }}
      }});
    }}

    /* ── hbar: horizontal bar chart — colorTopBottom option ── */
    else if (cfg.type === 'hbar') {{
      var bars    = (cfg.bars || []).slice();
      var labels2 = bars.map(function(b) {{ return b.label; }});
      var vals    = bars.map(function(b) {{ return b.value; }});
      var colors2 = cfg.colorTopBottom
        ? vals.map(function(v, i) {{ return i === 0 ? GREEN : (i === vals.length - 1 ? RED : NAVY); }})
        : vals.map(function(v) {{ return v >= 0 ? GREEN : RED; }});
      new Chart(canvas, {{
        type: 'bar',
        data: {{
          labels: labels2,
          datasets: [{{ data: vals, backgroundColor: colors2, barPercentage: 0.7 }}]
        }},
        options: {{
          indexAxis: 'y',
          responsive: false,
          animation: {{ duration: 0, onComplete: function() {{ window.__chartsRendered++; }} }},
          plugins: {{
            legend: {{ display: false }},
            datalabels: {{
              display: true, anchor: 'end', align: 'right',
              color: NAVY, font: {{ size: 8, weight: 600 }},
              formatter: function(v) {{ return v != null ? v.toFixed(1) : ''; }}
            }}
          }},
          scales: {{
            x: {{ title: {{ display: true, text: cfg.xLabel || '', font: {{size:9}} }},
                  min: 0, suggestedMax: 100 }},
            y: {{ ticks: {{ font: {{ size: 8 }} }} }}
          }}
        }}
      }});
    }}

    /* Unknown decision chart type — count it anyway so __chartsExpected is satisfied */
    else {{
      window.__chartsRendered++;
    }}
  }});
}})();
"""


# ──────────────────────────────────────────────────────────────────────────────
# Page shell builder
# ──────────────────────────────────────────────────────────────────────────────

def _wrap_page(inner: str, title: str | None, num: int | None,
               league_name: str, period_label: str, cover: bool = False) -> str:
    if cover:
        return f'<div class="page page--cover"><div class="page-inner">{inner}</div></div>'
    hnum = f'<span class="num">{num}.</span> ' if num is not None else ""
    header = (f'<div class="page-header">{hnum}{_html.escape(title or "")}</div>')
    subhead = (f'<div class="page-subhead">'
               f'<span>{_html.escape(league_name)}</span>'
               f'<span>{_html.escape(period_label)}</span>'
               f'</div>')
    footer = (f'<div class="page-footer">'
              f'<span></span>'
              f'<span>{num}</span>'
              f'</div>')
    return f'<div class="page"><div class="page-inner">{header}{subhead}{inner}{footer}</div></div>'


def _full_doc(pages: list, specs: list, trajectory_specs: list | None = None,
              bump_specs: list | None = None,
              decision_specs: list | None = None) -> str:
    """Assemble all page divs into a complete HTML document, with Chart.js
    scripts appended after all content so canvases exist when JS runs."""
    scatter = [s for s in specs if s["kind"] == "scatter"]
    bar = [s for s in specs if s["kind"] == "bar"]
    data = {"scatterCharts": scatter, "barCharts": bar,
            "trajectoryCharts": trajectory_specs or [],
            "bumpCharts": bump_specs or [],
            "decisionCharts": decision_specs or []}
    data_json = json.dumps(data).replace("</", "<\\/")
    has_charts = specs or trajectory_specs or bump_specs or decision_specs
    chart_block = ""
    if has_charts:
        chart_block = (
            f'<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>'
            f'<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2"></script>'
            f'<script>const DOSSIER_DATA = {data_json}; {_PDF_CHARTS_JS}</script>'
        )
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<style>{_PDF_CSS}</style></head><body>'
            f'{"".join(pages)}'
            f'{chart_block}'
            f'</body></html>')


# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────────

def _esc(s) -> str:
    return _html.escape(str(s))


def _manager_cell(season, rid, show_name: bool = True) -> str:
    team = season.teams.get(rid)
    uri = IMG.to_data_uri(IMG.manager_avatar_url(team))
    sil = IMG.SILHOUETTE_DATA_URI
    img = f'<img class="avatar manager sm" src="{uri}" onerror="this.onerror=null;this.src=\'{sil}\'">'
    name = _esc(season.team_name(rid))
    if show_name:
        return f"<td><span style='display:inline-flex;align-items:center;gap:5px'>{img}<strong>{name}</strong></span></td>"
    return f"<td>{img}</td>"


def _pdf_card(season, a, roasts: dict) -> str:
    """Compact award card for the PDF — same data as render._card but with
    PDF-specific CSS classes and smaller typography."""
    team = _esc(season.team_name(a.winner_rid))
    image_html = _award_image_html(season, a)
    roast = roasts.get(a.title, "")
    roast_html = f'<div class="roast">{_esc(roast)}</div>' if roast else ""
    podium = ""
    if a.podium:
        bits = " · ".join(f"{_esc(season.team_name(r))}: {_esc(v)}" for r, v in a.podium)
        podium = f'<div class="podium">{bits}</div>'
    return (f'<div class="award-card {a.hall}">'
            f'<div class="head">{_esc(a.title)}</div>'
            f'<div class="body">{image_html}'
            f'<div class="team">{team}</div>'
            f'<div class="val">{_esc(a.flavour)} — {_esc(a.headline)}</div>'
            f'{roast_html}{podium}'
            f'</div></div>')


# ──────────────────────────────────────────────────────────────────────────────
# Section renderers (each returns inner content only — no wrapping)
# ──────────────────────────────────────────────────────────────────────────────

def _render_cover(ctx: dict) -> str:
    season = ctx["season"]
    week = ctx.get("week")
    kind = ctx.get("kind", "weekly")
    managers = len(season.teams)
    recap = _esc(ctx.get("recap", ""))

    if kind == "weekly" and week:
        eyebrow = f"The Dossier &nbsp;·&nbsp; {_esc(season.name)}"
        title = "THE WAIVER<br>WIRE TAP"
        sub = f"WEEK {week}"
    else:
        eyebrow = _esc(season.name)
        title = "THE DOSSIER"
        sub = _esc(ctx.get("period_label", ""))

    recap_block = (f'<div style="margin-top:22px;font-size:11px;opacity:.72;line-height:1.75;'
                  f'max-width:230mm;border-left:3px solid rgba(255,255,255,.25);padding-left:12px">'
                  f'{recap}</div>') if recap else ""
    return (f'<div class="cover-accent">WWT</div>'
            f'<div class="cover-stripe"></div>'
            f'<div class="cover-eyebrow">{eyebrow}</div>'
            f'<div class="cover-title">{title}</div>'
            f'<div class="cover-sub">{sub}</div>'
            f'<div class="cover-divider"></div>'
            f'<div class="cover-meta">'
            f'League: {_esc(season.name)}<br>'
            f'Season: {_esc(season.season)} &nbsp;·&nbsp; {managers} managers'
            f'</div>'
            f'{recap_block}')


def _smart_card(season, a, roasts: dict) -> str:
    """Dispatch to the right card renderer depending on award type."""
    if hasattr(a, "is_decision"):   # DecisionAward
        return _decision_pdf_card(season, a, roasts)
    return _pdf_card(season, a, roasts)


def _render_hall_of_fame(ctx: dict) -> str:
    season, awards, roasts = ctx["season"], ctx["awards"], ctx["roasts"]
    # Production fame awards + any decision awards opted into hall-of-fame (is_decision=False)
    all_awards = list(awards) + [
        a for a in (ctx.get("decision_awards") or [])
        if not getattr(a, "is_decision", True) and a.hall == "fame"
    ]
    fame = [a for a in all_awards if a.hall == "fame"]
    cards = "".join(_smart_card(season, a, roasts) for a in fame)
    return f'<div class="cards-grid">{cards}</div>'


def _render_hall_of_shame(ctx: dict) -> str:
    season, awards, roasts = ctx["season"], ctx["awards"], ctx["roasts"]
    # Production shame awards + any decision awards opted into hall-of-shame (is_decision=False)
    all_awards = list(awards) + [
        a for a in (ctx.get("decision_awards") or [])
        if not getattr(a, "is_decision", True) and a.hall == "shame"
    ]
    shame = [a for a in all_awards if a.hall == "shame"]
    cards = "".join(_smart_card(season, a, roasts) for a in shame)
    return f'<div class="cards-grid">{cards}</div>'


def _decision_pdf_card(season, a: "DEC.DecisionAward", roasts: dict) -> str:
    """Compact card for a DecisionAward — similar to _pdf_card but uses
    DecisionAward-specific fields (detail, player_name) and handles the
    unavailable dimmed state."""
    unavailable = a.extra.get("unavailable", False)
    dim_cls     = " unavailable" if unavailable else ""
    if unavailable or a.winner_rid is None:
        team_html = '<span style="color:#9aa7bd;font-style:italic">—</span>'
        image_html = ""
    else:
        team_html  = f'<span class="team">{_esc(season.team_name(a.winner_rid))}</span>'
        image_html = _award_image_html(season, a)
    roast = roasts.get(a.title, "")
    roast_html  = f'<div class="roast">{_esc(roast)}</div>' if roast else ""
    detail_html = f'<div class="detail">{_esc(a.detail)}</div>' if a.detail else ""
    return (f'<div class="award-card {a.hall}{dim_cls}">'
            f'<div class="head">{_esc(a.title)}</div>'
            f'<div class="body">{image_html}'
            f'{team_html}'
            f'<div class="val">{_esc(a.flavour)}</div>'
            f'<div class="val">{_esc(a.headline)}</div>'
            f'{detail_html}{roast_html}'
            f'</div></div>')


def _render_decision_awards(ctx: dict) -> str:
    season    = ctx["season"]
    dec_awards = ctx.get("decision_awards") or []
    roasts    = ctx.get("roasts") or {}
    # Only the analytical awards belong in the Decision Lab section
    lab_awards = [a for a in dec_awards if getattr(a, "is_decision", True)]
    if not lab_awards:
        return '<div class="empty-state">Decision Lab data unavailable for this week.</div>'
    cards = "".join(_decision_pdf_card(season, a, roasts) for a in lab_awards)
    return f'<div class="cards-grid">{cards}</div>'


def _render_decision_charts(ctx: dict) -> str:
    specs = ctx.get("decision_chart_specs") or []
    if not specs:
        return '<div class="empty-state">No Decision Lab chart data available.</div>'
    chart_items = "".join(
        f'<div>{_chart_box(s, _DEC_CHART_W, _DEC_CHART_H)}</div>'
        for s in specs
    )
    return f'<div class="cols-2">{chart_items}</div>'


def _has_decision(ctx): return bool(ctx.get("decision_awards"))


def _chart_box(spec, default_w, default_h) -> str:
    cap = _esc(spec.get("caption", ""))
    cap_html = f'<p class="chart-caption">{cap}</p>' if cap else ""
    w = spec.get("width", default_w)
    h = spec.get("height", default_h)
    return (f'<div class="chart-wrap">'
            f'<h4>{_esc(spec["title"])}</h4>{cap_html}'
            f'<canvas id="{spec["id"]}" width="{w}" height="{h}"></canvas>'
            f'</div>')


# ── Page 4: Weekly Leaderboard + small charts ─────────────────────────────

def _render_leaderboard(ctx: dict) -> str:
    """Big combined leaderboard table + two small charts (efficiency + avg delta)."""
    season = ctx["season"]
    week = ctx["week"]
    wd = season.weeks.get(week, {})
    scores = S.weekly_scores(wd)
    pairs = S.matchup_pairs(wd)
    results = _week_results(pairs)
    ap = S.all_play(wd)
    pr = S.power_rank(season, week) or {}
    pr_ranked = {rid: i for i, (rid, _) in enumerate(
        sorted(pr.items(), key=lambda kv: kv[1]["score"], reverse=True), 1)}

    # Compute cumulative W-L-PF through the report week from matchup data
    # (avoids using season.teams.wins which reflects the final season record
    # including weeks beyond the report week on historic reports)
    cum_w: dict = {}
    cum_l: dict = {}
    cum_pf: dict = {}
    for wk in sorted(season.weeks.keys()):
        if wk > week:
            break
        wd_wk = season.weeks.get(wk, {})
        for ra, pa, rb, pb in S.matchup_pairs(wd_wk):
            if pa > pb:
                cum_w[ra] = cum_w.get(ra, 0) + 1
                cum_l[rb] = cum_l.get(rb, 0) + 1
            elif pb > pa:
                cum_w[rb] = cum_w.get(rb, 0) + 1
                cum_l[ra] = cum_l.get(ra, 0) + 1
        for rid_wk, pts_wk in S.weekly_scores(wd_wk).items():
            cum_pf[rid_wk] = cum_pf.get(rid_wk, 0.0) + pts_wk

    rows = ""
    all_rids = list(scores.keys()) or list(season.teams.keys())
    season_sorted = sorted(
        all_rids,
        key=lambda r: (-cum_w.get(r, 0), cum_l.get(r, 0), -cum_pf.get(r, 0.0)),
    )
    for i, rid in enumerate(season_sorted, 1):
        pts = scores.get(rid, 0.0)
        ap_val = ap.get(rid)
        ap_w, ap_l = (ap_val[0], ap_val[1]) if ap_val else (0, 0)
        ap_rate = ap_w / (ap_w + ap_l) if (ap_w + ap_l) else 0.5
        actual = results.get(rid, "—")
        luck = _luck_tag(actual, ap_rate) if actual != "—" else "—"
        lcls = "lucky" if luck == "lucky" else ("robbed" if luck == "robbed" else "")
        # Season record computed from matchup pairs through the report week
        s_w = cum_w.get(rid, 0)
        s_l = cum_l.get(rid, 0)
        s_rec = f"{s_w}-{s_l}"
        # efficiency from week_stats
        ws = (ctx.get("week_stats") or {}).get(rid, {})
        eff = f"{ws.get('efficiency'):.0f}%" if ws.get("efficiency") is not None else "—"
        pr_pos = pr_ranked.get(rid, "—")
        rows += (f"<tr>"
                 f"<td class='num' style='color:#7a8aa3'>{i}</td>"
                 f"{_manager_cell(season, rid)}"
                 f"<td class='num'><strong>{pts:.1f}</strong></td>"
                 f"<td class='num'>{s_rec}</td>"
                 f"<td class='num'>{actual}</td>"
                 f"<td class='num'>{ap_w}-{ap_l}</td>"
                 f"<td class='num {lcls}'>{luck}</td>"
                 f"<td class='num'>{eff}</td>"
                 f"<td class='num'>{pr_pos}</td>"
                 f"</tr>")

    table = (f'<table class="stats-table">'
             f'<tr><th>#</th><th>Team</th><th>Score</th><th>Season</th><th>H2H</th>'
             f'<th>All-Play</th><th>Luck</th><th>Eff %</th><th>PR</th></tr>'
             f'{rows}</table>')

    # Efficiency bar chart — full width below the leaderboard table.
    # avg_delta intentionally excluded here: it lives on the Median What-If page
    # (page 6), and duplicating the canvas id would make one of them blank.
    eff_spec = ctx.get("efficiency_spec")
    chart_html = ""
    if eff_spec:
        chart_html = f'<div class="lb-chart">{_chart_box(eff_spec, _SMALL_BAR_W, _SMALL_BAR_H)}</div>'

    return f'{table}{chart_html}'


# ── Page 5: Luck Index table + scatter chart ──────────────────────────────

def _render_luck_page(ctx: dict) -> str:
    season = ctx["season"]
    week = ctx["week"]
    wd = season.weeks.get(week, {})
    scores = S.weekly_scores(wd)
    pairs = S.matchup_pairs(wd)
    results = _week_results(pairs)
    ap = S.all_play(wd)

    rows = ""
    if ap:
        for rid in sorted(ap, key=lambda r: scores.get(r, 0), reverse=True):
            w, l, _t = ap[rid]
            actual = results.get(rid, "—")
            rate = w / (w + l) if (w + l) else 0.5
            tag = _luck_tag(actual, rate) if actual != "—" else "—"
            tcls = "lucky" if tag == "lucky" else ("robbed" if tag == "robbed" else "")
            rows += (f"<tr>{_manager_cell(season, rid)}"
                     f"<td class='num'>{scores.get(rid, 0):.1f}</td>"
                     f"<td class='num'>{actual}</td>"
                     f"<td class='num'>{w}-{l}</td>"
                     f"<td class='num {tcls}'>{tag}</td></tr>")

    table_block = (f'<h3 class="section-label">Luck Index</h3>'
                   f'<table class="stats-table"><tr><th>Team</th><th>Score</th>'
                   f'<th>H2H</th><th>All-Play</th><th>Luck</th></tr>{rows}</table>')

    luck_spec = ctx.get("luck_spec")
    chart_block = ""
    if luck_spec:
        chart_block = f'<div class="stacked-chart">{_chart_box(luck_spec, _LUCK_SCATTER_W, _LUCK_SCATTER_H)}</div>'

    return f'<div class="page-stacked">{table_block}{chart_block}</div>'


# ── Page 6: Median What-If table + avg delta chart ────────────────────────

def _render_median_page(ctx: dict) -> str:
    season = ctx["season"]
    week = ctx["week"]
    wd = season.weeks.get(week, {})
    scores = S.weekly_scores(wd)
    median = S.weekly_median(wd)

    rows = ""
    for rid, pts in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
        beat = pts > median
        miss = pts < median
        tag = "beat" if beat else ("missed" if miss else "tied")
        tcls = "lucky" if beat else ("robbed" if miss else "")
        rows += (f"<tr>{_manager_cell(season, rid)}"
                 f"<td class='num'>{pts:.1f}</td>"
                 f"<td class='num'>{median:.1f}</td>"
                 f"<td class='num {tcls}'>{tag}</td></tr>")

    table_block = (f'<h3 class="section-label">Median What-If (median: {median:.1f})</h3>'
                   f'<table class="stats-table"><tr><th>Team</th><th>Score</th>'
                   f'<th>Median</th><th>Result</th></tr>{rows}</table>')

    avg_spec = ctx.get("avg_delta_spec")
    chart_block = ""
    if avg_spec:
        chart_block = f'<div class="stacked-chart">{_chart_box(avg_spec, _MED_BAR_W, _MED_BAR_H)}</div>'

    return f'<div class="page-stacked">{table_block}{chart_block}</div>'


# ── Page 5: Season Rankings bump chart ───────────────────────────────────

def _bump_spec(season, week: int) -> dict:
    """Cumulative season standings rank after each week, weeks 1 → week.
    Rank 1 = best season record (W desc, L asc, PF desc as tiebreaker).
    This matches the leaderboard table ordering."""
    played_weeks = sorted(w for w in season.weeks if w <= week)
    if not played_weeks:
        return {}
    num_teams = len(season.teams)
    rids = sorted(season.teams.keys())

    # Rolling accumulators — updated each week from matchup results
    cum_w:  dict = {rid: 0   for rid in rids}
    cum_l:  dict = {rid: 0   for rid in rids}
    cum_pf: dict = {rid: 0.0 for rid in rids}

    rank_by_week: dict = {rid: [] for rid in rids}

    for wk in played_weeks:
        wd = season.weeks.get(wk, {})
        # Accumulate W/L from this week's matchups
        for ra, pa, rb, pb in S.matchup_pairs(wd):
            if pa > pb:
                cum_w[ra] = cum_w.get(ra, 0) + 1
                cum_l[rb] = cum_l.get(rb, 0) + 1
            elif pb > pa:
                cum_w[rb] = cum_w.get(rb, 0) + 1
                cum_l[ra] = cum_l.get(ra, 0) + 1
        # Accumulate points for (all teams with matchup data)
        for rid, pts in S.weekly_scores(wd).items():
            cum_pf[rid] = cum_pf.get(rid, 0.0) + pts

        # Rank by season record after this week
        standings = sorted(rids, key=lambda r: (-cum_w[r], cum_l[r], -cum_pf[r]))
        rank_map = {rid: i + 1 for i, rid in enumerate(standings)}
        for rid in rids:
            rank_by_week[rid].append(rank_map[rid])

    series = [{"label": season.team_name(rid), "data": rank_by_week[rid]}
              for rid in rids]
    return {
        "id": "bumpChart",
        "kind": "bump",
        "title": "Season Standings Over Time",
        "caption": ("Rank based on cumulative W-L record after each week — same ordering as the leaderboard. "
                    "Rank 1 = best season record. Crossings show when teams swapped positions in the standings."),
        "weeks": played_weeks,
        "series": series,
        "numTeams": num_teams,
        "width": _BUMP_W,
        "height": _BUMP_H,
    }


def _render_bump_chart(ctx: dict) -> str:
    spec = ctx.get("bump_spec")
    if not spec:
        return '<div style="color:#9aa7bd;padding:20px">No ranking data yet.</div>'
    cap = _esc(spec.get("caption", ""))
    cap_html = f'<p class="traj-caption">{cap}</p>' if cap else ""
    return (f'<div class="traj-wrap">'
            f'<h4>{_esc(spec["title"])}</h4>{cap_html}'
            f'<canvas id="{spec["id"]}" width="{spec["width"]}" height="{spec["height"]}"></canvas>'
            f'</div>')


def _has_bump(ctx): return bool(ctx.get("bump_spec"))


# ── Page 7: Season trajectory chart ─────────────────────────────────────

def _trajectory_spec(season, week: int) -> dict:
    """Per-manager weekly scores across all weeks up to `week`."""
    played_weeks = sorted(w for w in season.weeks if w <= week)
    if not played_weeks:
        return {}
    avg_data, all_pts = [], []
    for wk in played_weeks:
        wd = season.weeks.get(wk, {})
        pts = [wt.points for wt in wd.values() if wt.points is not None]
        avg_data.append(round(sum(pts) / len(pts), 1) if pts else 0.0)
        all_pts.extend(pts)
    series = []
    for rid in sorted(season.teams.keys()):
        data = [round(season.weeks[wk][rid].points, 1)
                if wk in season.weeks and rid in season.weeks[wk] else None
                for wk in played_weeks]
        series.append({"label": season.team_name(rid), "data": data})
    y_min = max(0, round((min(all_pts) - 10))) if all_pts else 0
    y_max = round((max(all_pts) + 10)) if all_pts else 200
    return {
        "id": "trajectoryChart",
        "kind": "trajectory",
        "title": "Season Scoring Trajectory",
        "caption": "Each manager's week-by-week score vs the league average — shows who's running hot, cold, or consistent.",
        "weeks": played_weeks,
        "series": series,
        "avgData": avg_data,
        "yMin": y_min,
        "yMax": y_max,
        "width": _TRAJ_W,
        "height": _TRAJ_H,
    }


def _render_trajectory(ctx: dict) -> str:
    traj = ctx.get("trajectory_spec")
    if not traj:
        return '<div style="color:#9aa7bd;padding:20px">No trajectory data.</div>'
    cap = _esc(traj.get("caption", ""))
    cap_html = f'<p class="traj-caption">{cap}</p>' if cap else ""
    return (f'<div class="traj-wrap">'
            f'<h4>{_esc(traj["title"])}</h4>{cap_html}'
            f'<canvas id="{traj["id"]}" width="{traj["width"]}" height="{traj["height"]}"></canvas>'
            f'</div>')


# ── Page 8: Rivalry Watch (bigger cards + season H2H history) ────────────

def _season_h2h_games(season, rid_a, rid_b, before_week: int) -> list:
    """Returns [(week, score_a, score_b)] for all games rid_a vs rid_b before `before_week`."""
    games = []
    for wk in sorted(season.weeks.keys()):
        if wk >= before_week:
            break
        for ra, pa, rb, pb in S.matchup_pairs(season.weeks.get(wk, {})):
            if ra == rid_a and rb == rid_b:
                games.append((wk, pa, pb))
            elif ra == rid_b and rb == rid_a:
                games.append((wk, pb, pa))
    return games


# ── Page: Transactions HQ — Waivers & FAAB (ledger + supporting charts) ──

def _txn_ledger_table(ledger: list, max_rows: int = 26) -> str:
    """Compact waiver ledger table HTML, truncated to `max_rows`."""
    rows_html = ""
    truncated = len(ledger) > max_rows
    for row in ledger[:max_rows]:
        faab_str = f"${row['faab']}" if row["faab"] else "$0"
        drop_str = ", ".join(row["drops"][:2]) if row["drops"] else "—"
        pts_str  = f"{row['points_since']:.1f}"
        hit_cls  = "hit-yes" if row["hit"] else "hit-no"
        hit_str  = "Yes" if row["hit"] else "—"
        rows_html += (f"<tr>"
                      f"<td>Wk {row['week']}</td>"
                      f"<td>{_esc(row['manager'])}</td>"
                      f"<td>{_esc(row['player_name'])}</td>"
                      f"<td>{_esc(drop_str)}</td>"
                      f"<td class='faab-val num'>{faab_str}</td>"
                      f"<td class='num'>{pts_str}</td>"
                      f"<td class='num {hit_cls}'>{hit_str}</td>"
                      f"</tr>")
    trunc = (f'<p style="font-size:8px;color:var(--muted);margin:3px 0 0">'
             f'Showing first {max_rows} of {len(ledger)} transactions.</p>') if truncated else ""
    return (f'<table class="txn-table">'
            f'<tr><th>Wk</th><th>Manager</th><th>Added</th><th>Dropped</th>'
            f'<th>FAAB</th><th>Pts since</th><th>Started?</th></tr>'
            f'{rows_html}</table>{trunc}')


def _faab_callouts(txn_ledger: list) -> str:
    """This week's highlight moves — shown on the Transfers page.
    Biggest FAAB spend + most-added free agent this week.
    pts not shown since same-week pickups haven't had a chance to score."""
    if not txn_ledger:
        return ""

    # Biggest FAAB bid this week
    paid = sorted([r for r in txn_ledger if r["faab"] > 0],
                  key=lambda r: r["faab"], reverse=True)
    # Best free claim: if multiple free adds, pick the one with the drop
    # (dropping a player means the manager valued the pickup highly)
    free = [r for r in txn_ledger if r["faab"] == 0]
    free_notable = sorted(free, key=lambda r: len(r["drops"]), reverse=True)

    def _callout(label: str, row: dict | None, cls: str) -> str:
        if not row:
            return ""
        faab_str = f"${row['faab']}" if row["faab"] else "Free"
        drop_str = (f", dropping {_esc(row['drops'][0])}" if row["drops"] else "")
        return (f'<div class="callout {cls}" style="width:48%;box-sizing:border-box">'
                f'<strong>{label}:</strong> {_esc(row["player_name"])} '
                f'→ {_esc(row["manager"])} ({faab_str}{drop_str})'
                f'</div>')

    left  = _callout("Biggest FAAB bet this week", paid[0] if paid else None, "")
    right = _callout("Top free claim this week",   free_notable[0] if free_notable else None, "")
    if not left and not right:
        return ""
    return (f'<div style="display:flex;gap:12px;margin:10px 0">'
            f'{left}{right}'
            f'</div>')


def _render_waiver_hq(ctx: dict) -> str:
    """Page A — simple 'Transfers In This Week' table + season callouts + FAAB remaining bar."""
    ledger     = ctx.get("txn_ledger") or []

    if not ledger:
        return '<div class="empty-state">No waiver or free-agent activity this week.</div>'

    # Simple table — no pts since column (irrelevant for same-week context)
    rows_html = ""
    for row in ledger:
        faab_str = f"${row['faab']}" if row["faab"] else "Free"
        ttype    = "Waiver" if row["type"] == "waiver" else "FA"
        drop_str = ", ".join(row["drops"][:2]) if row["drops"] else "—"
        rows_html += (f"<tr>"
                      f"<td>{_esc(row['manager'])}</td>"
                      f"<td>{_esc(row['player_name'])}</td>"
                      f"<td>{_esc(drop_str)}</td>"
                      f"<td class='faab-val num'>{faab_str}</td>"
                      f"<td class='num'>{ttype}</td>"
                      f"</tr>")
    table = (f'<table class="txn-table">'
             f'<tr><th>Manager</th><th>Added</th><th>Dropped</th>'
             f'<th>FAAB</th><th>Type</th></tr>'
             f'{rows_html}</table>')

    # This week's highlight moves (biggest spend + best free claim)
    callouts = _faab_callouts(ledger)   # ledger = this week only

    # FAAB remaining bar — placed in the right column of a cols-w layout so it
    # doesn't compete for vertical space with the (potentially long) table.
    rem_spec = ctx.get("faab_remaining_spec")
    if rem_spec:
        right_html = ('<div>'
                      + _chart_box(rem_spec, rem_spec.get("width", _TXN_BAR_W),
                                   rem_spec.get("height", _TXN_BAR_H))
                      + '</div>')
        return f'<div class="cols-w">{table}{callouts}{right_html}</div>'

    return f'{table}{callouts}'


# ── Page: FAAB Efficiency (scatter, full page) ────────────────────────────

def _render_faab_efficiency(ctx: dict) -> str:
    """Page B — season-to-date FAAB efficiency scatter + bandwagon charts.
    Callouts (best/bust) now live on the Transfers page instead."""
    spec = ctx.get("faab_scatter_spec")
    if not spec:
        return '<div class="empty-state">Not enough pickup data yet.</div>'

    scatter_html = ('<div class="chart-wrap" style="background:#fff;border:1px solid #e3e8f0;'
                    'border-radius:9px;padding:10px 14px;margin-bottom:6px">'
                    + _chart_box(spec, _FAAB_SCATTER_W, _FAAB_SCATTER_H)
                    + '</div>')

    # Bandwagon charts — tight gap (callouts no longer push them down)
    added_spec = ctx.get("bw_added_spec")
    drop_spec  = ctx.get("bw_dropped_spec")
    bw_html = ""
    if added_spec or drop_spec:
        boxes = ""
        if added_spec:
            boxes += f'<div class="chart-wrap">{_chart_box(added_spec, _BW_BAR_W, _BW_BAR_H)}</div>'
        if drop_spec:
            boxes += f'<div class="chart-wrap">{_chart_box(drop_spec, _BW_BAR_W, _BW_BAR_H)}</div>'
        bw_html = (f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">'
                   f'{boxes}</div>')

    return f'{scatter_html}{bw_html}'


# ── Page: Transactions HQ — Trades ───────────────────────────────────────

def _trade_grade_str(trade: dict) -> tuple:
    """Returns (grade_html, winner_name_or_none) for a trade record."""
    sides = trade["sides"]
    sorted_sides = sorted(sides, key=lambda s: s["pts_since"], reverse=True)
    if all(s["pts_since"] == 0 for s in sides):
        return ('<span style="color:var(--muted)">No data yet</span>', None)
    winner = sorted_sides[0]["manager"]
    margin = sorted_sides[0]["pts_since"] - sorted_sides[-1]["pts_since"] if len(sorted_sides) > 1 else 0
    grade = (f'<span class="grade-winner">{_esc(winner)} +{margin:.1f}</span>')
    return grade, winner


_TRADES_PER_FIRST_PAGE = 16   # rows on page 1 (leaves room for cards + chart)
_TRADES_PER_CONT_PAGE  = 28   # rows on continuation pages (table only)


def _trade_table_html(trades: list) -> str:
    """Compact table rows for a slice of trades."""
    rows = ""
    for trade in trades:
        sides = trade["sides"]
        if len(sides) < 2:
            continue
        picks_note = " *" if trade.get("has_picks") else ""
        grade_html, _ = _trade_grade_str(trade)

        def _side_cell(side: dict) -> str:
            players = ", ".join(side["received_players"][:2])
            if len(side["received_players"]) > 2:
                players += f" +{len(side['received_players']) - 2} more"
            picks = (" + " + ", ".join(side["received_picks"][:2])
                     if side["received_picks"] else "")
            return (f'{_esc(side["manager"])}'
                    f'<br><span style="color:#5b6b85">{_esc(players or "—")}{_esc(picks)}</span>')

        rows += (f"<tr>"
                 f"<td class='wk-cell'>Wk {trade['week']}{picks_note}</td>"
                 f"<td>{_side_cell(sides[0])}</td>"
                 f"<td>{_side_cell(sides[1])}</td>"
                 f"<td>{grade_html}</td>"
                 f"</tr>")
    return rows


def _render_trade_hq(ctx: dict) -> list:
    """Returns a list of page-body strings (multi-page when trades are many)."""
    week    = ctx.get("week")
    tl      = ctx.get("trade_ledger") or []

    if not tl:
        return ['<div class="empty-state">No trades this season.</div>']

    picks_legend = ('<p style="font-size:8px;color:var(--muted);margin:2px 0 6px">'
                    '* includes draft picks</p>')
    table_header = ('<table class="trade-tbl">'
                    '<tr><th>Wk</th><th>Side A received</th>'
                    '<th>Side B received</th><th>Winning so far</th></tr>')

    # ── Full detail cards for this week ──────────────────────────────
    this_week_trades = [t for t in tl if t["week"] == week]
    cards_html = ""
    for trade in this_week_trades:
        sides = trade["sides"]
        sides_html = ""
        for side in sides:
            players_str = ", ".join(side["received_players"]) or "—"
            picks_str   = ", ".join(side["received_picks"])
            pts = side["pts_since"]
            pts_cls = "pts-pos" if pts > 0 else ("pts-neg" if pts < 0 else "")
            pts_html = (f'<div class="side-pts"><span class="{pts_cls}">{pts:+.1f} pts since trade</span></div>'
                        if pts != 0 else "")
            picks_html = f'<div class="side-picks">+ {_esc(picks_str)}</div>' if picks_str else ""
            sides_html += (f'<div class="trade-side">'
                          f'<div class="side-mgr">{_esc(side["manager"])}</div>'
                          f'<div class="side-gets">{_esc(players_str)}</div>'
                          f'{picks_html}{pts_html}'
                          f'</div>')
        grade_html, _ = _trade_grade_str(trade)
        picks_note = " (includes draft picks)" if trade.get("has_picks") else ""
        cards_html += (f'<div class="trade-card">'
                       f'<div class="trade-wk">Week {trade["week"]}{picks_note} — this week</div>'
                       f'<div class="trade-sides">{sides_html}</div>'
                       f'<div class="grade-band">{grade_html}'
                       f'<span class="grade-loser"> — verdict updates each week</span></div>'
                       f'</div>')

    cards_section = ""
    if this_week_trades:
        cards_section = (f'<h3 class="section-label" style="margin-top:8px">This Week\'s Trades</h3>'
                         f'<div class="trade-ledger">{cards_html}</div>')
    else:
        cards_section = '<p style="font-size:9px;color:var(--muted);margin-top:6px">No trades this week.</p>'

    net_spec = ctx.get("trade_net_spec")
    n_net_bars = len((net_spec or {}).get("bars", [])) if net_spec else 0
    net_chart_h = max(240, n_net_bars * 28 + 40)
    chart_html = ('<div style="margin-top:8px">'
                  + _chart_box(net_spec, _TXN_BAR_W, net_chart_h)
                  + '</div>') if net_spec else ""

    # ── Paginate the table ────────────────────────────────────────────
    pages_out = []
    first_chunk  = tl[:_TRADES_PER_FIRST_PAGE]
    rest_chunks  = [tl[i:i + _TRADES_PER_CONT_PAGE]
                    for i in range(_TRADES_PER_FIRST_PAGE, len(tl), _TRADES_PER_CONT_PAGE)]

    # Page 1: first rows + cards (chart goes on last page to avoid page-fold clipping)
    p1_rows = _trade_table_html(first_chunk)
    cont_note = (f'<p style="font-size:8px;color:var(--muted);margin:2px 0">'
                 f'Continued on next page ({len(tl) - _TRADES_PER_FIRST_PAGE} more trades) →</p>'
                 ) if rest_chunks else ""

    if rest_chunks:
        # Chart goes on the last continuation page to guarantee it's not page-fold-clipped
        pages_out.append(
            f'{table_header}{p1_rows}</table>{picks_legend}{cont_note}{cards_section}'
        )
        for chunk in rest_chunks[:-1]:
            rows = _trade_table_html(chunk)
            pages_out.append(f'{table_header}{rows}</table>{picks_legend}')
        last_rows = _trade_table_html(rest_chunks[-1])
        pages_out.append(f'{table_header}{last_rows}</table>{picks_legend}{chart_html}')
    else:
        # Single page — only ≤16 trades; table + cards + chart all fit
        pages_out.append(
            f'{table_header}{p1_rows}</table>{picks_legend}{cont_note}'
            f'{cards_section}{chart_html}'
        )

    return pages_out


def _prev_season_games(hist_index: dict | None, owner_a: str | None,
                       owner_b: str | None) -> tuple:
    """(season_year_str, [(week, score_a, score_b)]) for the most recent previous
    season where these two owners met. score_a/score_b are from owner_a's perspective.
    Returns (None, []) when there's no prior history."""
    if not hist_index or not owner_a or not owner_b:
        return None, []
    pair = {owner_a, owner_b}
    by_season: dict[str, list] = {}
    for r in (hist_index.get("results") or []):
        if {r["owner_a"], r["owner_b"]} != pair:
            continue
        sa = r["points_a"] if r["owner_a"] == owner_a else r["points_b"]
        sb = r["points_b"] if r["owner_a"] == owner_a else r["points_a"]
        by_season.setdefault(r["season"], []).append((r["week"], sa, sb))
    if not by_season:
        return None, []
    latest = max(by_season.keys())
    return latest, sorted(by_season[latest], key=lambda x: x[0])


def _render_rivalry_v2(ctx: dict) -> str:
    season = ctx["season"]
    week = ctx.get("week")
    rivalry_matchups = ctx.get("rivalry_matchups") or []
    if not rivalry_matchups:
        return '<div style="color:#9aa7bd;padding:20px">No rivalry data available.</div>'

    cards = []
    wd = season.weeks.get(week, {})

    for m in rivalry_matchups:
        name_a, name_b = _esc(m["name_a"]), _esc(m["name_b"])
        if m["pa"] == m["pb"]:
            matchup_line = f'{name_a} {m["pa"]:.1f} – {m["pb"]:.1f} {name_b} (tied)'
        else:
            a_win = m["pa"] > m["pb"]
            a_cls, b_cls = ("winner", "") if a_win else ("", "winner")
            matchup_line = (f'<span class="{a_cls}">{name_a} {m["pa"]:.1f}</span> – '
                           f'<span class="{b_cls}">{m["pb"]:.1f} {name_b}</span>')

        av_a = _img_tag(IMG.to_data_uri(IMG.manager_avatar_url(season.teams.get(m["ra"]))), "avatar manager sm")
        av_b = _img_tag(IMG.to_data_uri(IMG.manager_avatar_url(season.teams.get(m["rb"]))), "avatar manager sm")

        # Previous season's H2H games (from the cached history index)
        team_a_obj = season.teams.get(m["ra"])
        team_b_obj = season.teams.get(m["rb"])
        prev_yr, prev_games = _prev_season_games(
            ctx.get("hist_index"),
            team_a_obj.owner_id if team_a_obj else None,
            team_b_obj.owner_id if team_b_obj else None,
        )
        prev_block = ""
        if prev_yr and prev_games:
            # Show ALL games from that season — no cap — so the season W-L is accurate
            prev_wa = sum(1 for _, sa, sb in prev_games if sa > sb)
            prev_wb = sum(1 for _, sa, sb in prev_games if sb > sa)
            if prev_wa > prev_wb:
                prev_rec = f"{m['name_a']} {prev_wa}-{prev_wb}"
            elif prev_wb > prev_wa:
                prev_rec = f"{m['name_b']} {prev_wb}-{prev_wa}"
            else:
                prev_rec = f"Tied {prev_wa}-{prev_wb}"
            rows_prev = ""
            for wk, sa, sb in prev_games:
                a_cls = "lucky" if sa > sb else ("robbed" if sb > sa else "")
                b_cls = "lucky" if sb > sa else ("robbed" if sa > sb else "")
                rows_prev += (f'<div class="game-row">Wk {wk}: '
                              f'<span class="{a_cls}">{name_a} {sa:.1f}</span> – '
                              f'<span class="{b_cls}">{sb:.1f} {name_b}</span></div>')
            prev_block = (f'<div class="season-games">'
                         f'<div class="season-subhead">{_esc(prev_yr)} Season — {_esc(prev_rec)}</div>'
                         f'{rows_prev}</div>')

        # This season: prior meetings + this week's game.
        # All shown — no cap — so the season W-L in the header can be verified.
        season_games = _season_h2h_games(season, m["ra"], m["rb"], before_week=week or 99)
        cur_wa = sum(1 for _, sa, sb in season_games if sa > sb)
        cur_wb = sum(1 for _, sa, sb in season_games if sb > sa)
        # Add this week
        if m["pa"] > m["pb"]:
            cur_wa += 1
        elif m["pb"] > m["pa"]:
            cur_wb += 1
        if cur_wa > cur_wb:
            cur_rec = f"{m['name_a']} {cur_wa}-{cur_wb}"
        elif cur_wb > cur_wa:
            cur_rec = f"{m['name_b']} {cur_wb}-{cur_wa}"
        else:
            cur_rec = f"Tied {cur_wa}-{cur_wb}"

        this_week_row = (f'<div class="game-row">'
                         f'Wk {week}: {name_a} {m["pa"]:.1f} – {m["pb"]:.1f} {name_b} '
                         f'<span class="{"lucky" if m["pa"] > m["pb"] else "robbed" if m["pb"] > m["pa"] else ""}">&#9664; this week</span>'
                         f'</div>') if week else ""
        prior_rows = ""
        for wk, sa, sb in season_games:  # all prior meetings, chronological
            a_cls = "lucky" if sa > sb else ("robbed" if sb > sa else "")
            b_cls = "lucky" if sb > sa else ("robbed" if sa > sb else "")
            prior_rows += (f'<div class="game-row">Wk {wk}: '
                          f'<span class="{a_cls}">{name_a} {sa:.1f}</span> – '
                          f'<span class="{b_cls}">{sb:.1f} {name_b}</span>'
                          f'</div>')

        season_block = ""
        if prior_rows or this_week_row:
            season_block = (f'<div class="season-games">'
                           f'<div class="season-subhead">This season — {_esc(cur_rec)}</div>'
                           f'{prior_rows}{this_week_row}'
                           f'</div>')

        # Recompute all-time line including this week's result.
        # m["line"] used before_week=week so this week was excluded — the record
        # count was right before this game but is stale now the game is done.
        record = m.get("record") or {}
        oa = team_a_obj.owner_id if team_a_obj else None
        ob = team_b_obj.owner_id if team_b_obj else None
        updated_wins = dict(record.get("wins") or {})
        updated_ties = record.get("ties", 0)
        if m["pa"] > m["pb"] and oa:
            updated_wins[oa] = updated_wins.get(oa, 0) + 1
            this_week_owner = oa
        elif m["pb"] > m["pa"] and ob:
            updated_wins[ob] = updated_wins.get(ob, 0) + 1
            this_week_owner = ob
        else:
            updated_ties += 1
            this_week_owner = None
        # Streak: extend if same winner, reset to 1 if different, break on tie
        old_streak_owner = record.get("streak_owner")
        old_streak_len   = record.get("streak_len", 0)
        if this_week_owner is None:
            new_streak_owner, new_streak_len = None, 0
        elif this_week_owner == old_streak_owner:
            new_streak_owner, new_streak_len = old_streak_owner, old_streak_len + 1
        else:
            new_streak_owner, new_streak_len = this_week_owner, 1
        updated_record = dict(record, wins=updated_wins, ties=updated_ties,
                              games=record.get("games", 0) + 1,
                              streak_owner=new_streak_owner, streak_len=new_streak_len)
        updated_line = H.format_rivalry_line(updated_record, m["name_a"], m["name_b"],
                                             oa or "", ob or "")

        cards.append(f'<div class="rivalry-card">'
                     f'<div class="avatar-row">{av_a}{av_b}</div>'
                     f'<div class="matchup">{matchup_line}</div>'
                     f'<div class="history">{_esc(updated_line)}</div>'
                     f'{prev_block}'
                     f'{season_block}'
                     f'</div>')

    # Unmatched notice
    paired = {rid for m in rivalry_matchups for rid in (m["ra"], m["rb"])}
    unmatched = [_esc(season.team_name(rid)) for rid in sorted(wd.keys()) if rid not in paired]
    note = (f'<p style="font-size:9px;color:#7a8aa3;margin-top:8px">'
            f'No matchup recorded for {", ".join(unmatched)} this week '
            f'(consolation bracket — Sleeper doesn\'t assign matchup IDs to these games).</p>'
            ) if unmatched else ""

    return f'<div class="rivalry-grid">{"".join(cards)}</div>{note}'


# ── Page: Rivalry Watch — All-Time Leaderboard ───────────────────────────

def _render_rivalry_leaderboard(ctx: dict) -> str:
    """Continuation page: all-time H2H dominance table + win% bar chart."""
    season     = ctx["season"]
    week       = ctx.get("week")
    hist_index = ctx.get("hist_index")

    if not hist_index:
        return '<div class="empty-state">No cross-season history cached yet.</div>'

    extra = H.current_season_results(season, before_week=(week + 1) if week else 99)

    rid_to_owner = {rid: t.owner_id for rid, t in season.teams.items() if t.owner_id}
    owner_to_rid = {oid: rid for rid, oid in rid_to_owner.items()}
    owners = list(owner_to_rid)

    dominance: dict = {oa: {"wins": 0, "losses": 0, "games": 0} for oa in owners}
    for i, oa in enumerate(owners):
        for ob in owners[i + 1:]:
            rec = H.head_to_head(hist_index, oa, ob, extra_results=extra)
            if not rec or rec["games"] == 0:
                continue
            wa = rec["wins"].get(oa, 0)
            wb = rec["wins"].get(ob, 0)
            dominance[oa]["wins"]   += wa;  dominance[oa]["losses"] += wb;  dominance[oa]["games"] += rec["games"]
            dominance[ob]["wins"]   += wb;  dominance[ob]["losses"] += wa;  dominance[ob]["games"] += rec["games"]

    dom_rows = sorted(
        [(oa, d) for oa, d in dominance.items() if d["games"] > 0],
        key=lambda x: x[1]["wins"] / x[1]["games"] if x[1]["games"] else 0,
        reverse=True,
    )

    if not dom_rows:
        return '<div class="empty-state">No head-to-head history found for current managers.</div>'

    # ── Dominance table ──────────────────────────────────────────────────
    dom_html = ""
    for rank, (oa, d) in enumerate(dom_rows, 1):
        rid = owner_to_rid.get(oa)
        wp  = d["wins"] / d["games"] * 100 if d["games"] else 0
        dom_html += (f"<tr><td class='num'>{rank}</td>"
                     f"{_manager_cell(season, rid)}"
                     f"<td class='num'>{d['wins']}</td>"
                     f"<td class='num'>{d['losses']}</td>"
                     f"<td class='num'>{d['games']}</td>"
                     f"<td class='num'><strong>{wp:.0f}%</strong></td></tr>")
    dom_table = (f'<h3 class="section-label">All-Time H2H Dominance</h3>'
                 f'<table class="stats-table"><tr><th>#</th><th>Manager</th>'
                 f'<th>W</th><th>L</th><th>Games</th><th>Win%</th></tr>'
                 f'{dom_html}</table>')

    # ── Win% bar chart (pre-built spec passed via ctx) ───────────────────
    spec = ctx.get("alltime_winpct_spec")
    chart_html = ""
    if spec:
        chart_html = ('<div class="chart-wrap" style="background:#fff;border:1px solid #e3e8f0;'
                      'border-radius:9px;padding:12px 16px">'
                      + _chart_box(spec, spec.get("width", 580), spec.get("height", 430))
                      + '</div>')

    return (f'<div style="display:grid;grid-template-columns:1fr 1.4fr;gap:18px;align-items:start">'
            f'<div>{dom_table}</div>'
            f'<div>{chart_html}</div>'
            f'</div>')


def _has_hist(ctx):
    idx = ctx.get("hist_index")
    return bool(idx and (idx.get("results") or idx.get("chain")))


# ── Monthly/Season: generic charts page ──────────────────────────────────

def _render_charts(ctx: dict) -> str:
    """Generic charts page for monthly/season (scatter left, bars right)."""
    specs = ctx.get("chart_specs", [])
    if not specs:
        return '<div style="color:#9aa7bd;padding:20px">No charts for this period.</div>'
    scatter_specs = [s for s in specs if s["kind"] == "scatter"]
    bar_specs = [s for s in specs if s["kind"] == "bar"]
    main_html = "".join(_chart_box(s, _SCATTER_W, _SCATTER_H) for s in scatter_specs)
    side_html = "".join(_chart_box(s, _BAR_W, _BAR_H) for s in bar_specs)
    if scatter_specs and bar_specs:
        return (f'<div class="charts-row">'
                f'<div class="chart-main">{main_html}</div>'
                f'<div class="charts-side">{side_html}</div>'
                f'</div>')
    return main_html + side_html


def _render_standings_monthly(ctx: dict) -> str:
    """Month in Review table + Overall Standings side by side."""
    season = ctx["season"]
    month_stats = ctx.get("month_stats") or {}
    weeks = ctx.get("weeks") or []
    upto_week = max(weeks) if weeks else None

    # Overall standings
    st_rows = ""
    spark_weeks = weeks
    for i, r in enumerate(standings_rows(season, upto_week), 1):
        rid = r["rid"]
        wl = f"{r['w']}-{r['l']}" + (f"-{r['ties']}" if r["ties"] else "")
        from .render import _sparkline
        pts_list = [season.weeks[wk][rid].points for wk in spark_weeks
                    if rid in season.weeks.get(wk, {})]
        spark = _esc(_sparkline(pts_list))
        st_rows += (f"<tr>{_manager_cell(season, rid)}"
                    f"<td class='num'>{wl}</td>"
                    f"<td class='num'>{r['pf']:.0f}</td>"
                    f"<td class='num'>{r['pa']:.0f}</td>"
                    f"<td class='spark'>{spark}</td></tr>")

    standings_col = (f'<div><h3 class="section-label">Standings</h3>'
                     f'<table class="stats-table"><tr><th>Team</th><th>W-L</th>'
                     f'<th>PF</th><th>PA</th><th>Form</th></tr>{st_rows}</table></div>')

    # Month in Review
    month_rows = ""
    if month_stats:
        for m in sorted(month_stats.values(), key=lambda x: x.pts_above_avg, reverse=True):
            rank = f"{m.rank_start}→{m.rank_end}" if m.rank_start else f"→{m.rank_end}"
            aa_cls = "lucky" if m.pts_above_avg > 0 else "robbed"
            month_rows += (f"<tr>{_manager_cell(season, m.roster_id)}"
                           f"<td class='num'>{m.h2h_w}-{m.h2h_l}</td>"
                           f"<td class='num {aa_cls}'>{m.pts_above_avg:+.1f}</td>"
                           f"<td class='num'>{rank}</td>"
                           f"<td class='num'>{m.avg_efficiency:.0f}%</td></tr>")

    month_col = (f'<div><h3 class="section-label">Month in Review</h3>'
                 f'<table class="stats-table"><tr><th>Team</th><th>Record</th>'
                 f'<th>+/- Avg</th><th>Rank</th><th>Eff</th></tr>{month_rows}</table></div>'
                 ) if month_rows else ""

    return (f'<div class="cols-2">{standings_col}{month_col}</div>'
            if month_col else standings_col)


def _render_season_analytics(ctx: dict) -> str:
    """Power Ranking + Luck Index + Median What-If for season/monthly PDFs."""
    season = ctx["season"]
    season_stats = ctx.get("season_stats") or {}
    weeks = ctx.get("weeks") or []
    upto_week = max(weeks) if weeks else None
    kind = ctx.get("kind", "season")

    record_by_rid = {r["rid"]: f"{r['w']}-{r['l']}" + (f"-{r['ties']}" if r['ties'] else "")
                     for r in standings_rows(season, upto_week)}

    # ── Luck Index ──
    luck_rows = ""
    for s in sorted(season_stats.values(), key=lambda x: x.luck_index, reverse=True):
        cls = "lucky" if s.luck_index > 0 else ("robbed" if s.luck_index < 0 else "")
        luck_rows += (f"<tr>{_manager_cell(season, s.roster_id)}"
                      f"<td class='num'>{record_by_rid.get(s.roster_id, '—')}</td>"
                      f"<td class='num {cls}'>{s.luck_index:+.1f}</td>"
                      f"<td class='num'>{s.all_play_w}-{s.all_play_l}</td>"
                      f"<td class='num'>{s.avg_efficiency:.0f}%</td></tr>")

    luck_col = (f'<div><h3 class="section-label">Luck Index — Season to Date</h3>'
                f'<table class="stats-table"><tr><th>Team</th><th>Record</th>'
                f'<th>Luck ±W</th><th>All-Play</th><th>Eff</th></tr>{luck_rows}</table></div>')

    # ── Median What-If ──
    mr = S.median_record(season, upto_week=upto_week) if upto_week else {}
    med_rows = ""
    for rid, c in sorted((mr or {}).items(), key=lambda kv: kv[1]["above"], reverse=True):
        rec = f"{c['above']}-{c['below']}" + (f"-{c['tied']}" if c["tied"] else "")
        med_rows += (f"<tr>{_manager_cell(season, rid)}"
                     f"<td class='num'>{rec}</td></tr>")

    med_col = (f'<div><h3 class="section-label">Median What-If — To Date</h3>'
               f'<table class="stats-table"><tr><th>Team</th>'
               f'<th>vs Median</th></tr>{med_rows}</table></div>') if med_rows else ""

    # ── Power Ranking (season only) ──
    pr_col = ""
    if kind == "season" and weeks:
        pr = S.power_rank(season, max(weeks)) or {}
        std_order = [r["rid"] for r in standings_rows(season, max(weeks))]
        std_rank = {rid: i + 1 for i, rid in enumerate(std_order)}
        pr_rows = ""
        for i, (rid, p) in enumerate(
            sorted(pr.items(), key=lambda kv: kv[1]["score"], reverse=True), 1
        ):
            delta = std_rank.get(rid, i) - i
            dcls = "lucky" if delta > 0 else ("robbed" if delta < 0 else "")
            ds = f"{delta:+d}" if delta else "—"
            pr_rows += (f"<tr>{_manager_cell(season, rid)}"
                        f"<td class='num'>{p['score']:.1f}</td>"
                        f"<td class='num'>{p['all_play_pct']:.0f}%</td>"
                        f"<td class='num {dcls}'>{ds}</td></tr>")
        pr_col = (f'<div><h3 class="section-label">Power Ranking</h3>'
                  f'<table class="stats-table"><tr><th>Team</th><th>Score</th>'
                  f'<th>AP%</th><th>±Std</th></tr>{pr_rows}</table></div>')

    cols = [luck_col]
    if med_col:
        cols.append(med_col)
    if pr_col:
        cols.append(pr_col)

    if len(cols) == 3:
        return f'<div class="cols-3">{"".join(cols)}</div>'
    if len(cols) == 2:
        return f'<div class="cols-2">{"".join(cols)}</div>'
    return cols[0] if cols else ""


# ──────────────────────────────────────────────────────────────────────────────
# Section registry
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Section:
    id: str
    title: str | None
    render: Callable
    cover: bool = False
    # Predicate: if provided, section is omitted when it returns False
    when: Callable | None = None


def _always(_ctx): return True
def _has_week(ctx): return bool(ctx.get("week"))
def _has_month(ctx): return bool(ctx.get("month_stats"))
def _has_rivalry(ctx): return bool(ctx.get("rivalry_matchups"))
def _has_charts(ctx): return bool(ctx.get("chart_specs"))
def _has_luck(ctx): return bool(ctx.get("luck_spec"))
def _has_traj(ctx): return bool(ctx.get("trajectory_spec"))
def _has_txns(ctx): return bool(ctx.get("txn_ledger") or ctx["season"].transactions)


WEEKLY_SECTIONS: list[Section] = [
    Section("cover",              None,                              _render_cover,          cover=True),
    Section("hall-fame",          "Hall of Fame",                   _render_hall_of_fame),
    Section("hall-shame",         "Hall of Shame",                  _render_hall_of_shame),
    Section("decision-awards",    "Decision Lab",                   _render_decision_awards, when=_has_decision),
    Section("decision-charts",    "Decision Lab: Charts",           _render_decision_charts, when=_has_decision),
    Section("leaderboard",        "Week Leaderboard",               _render_leaderboard),
    Section("rankings-bump",      "Season Rankings",                _render_bump_chart,     when=_has_bump),
    Section("luck",               "Luck Index",                     _render_luck_page,      when=_has_luck),
    Section("median",             "Median What-If",                 _render_median_page),
    Section("trajectory",         "Season Trajectory",              _render_trajectory,     when=_has_traj),
    Section("transactions-waivers", "Transfers In This Week",         _render_waiver_hq,       when=_has_txns),
    Section("transactions-faab",   "FAAB Efficiency (Season to Date)", _render_faab_efficiency, when=_has_txns),
    Section("transactions-trades", "Transactions HQ: Trades",      _render_trade_hq),
    Section("rivalry",            "Rivalry Watch",                  _render_rivalry_v2,     when=_has_rivalry),
    Section("rivalry-leaderboard", "Rivalry Watch: All-Time",      _render_rivalry_leaderboard),
]

MONTHLY_SECTIONS: list[Section] = [
    Section("cover",       None,                    _render_cover,            cover=True),
    Section("hall-fame",   "Hall of Fame",           _render_hall_of_fame),
    Section("hall-shame",  "Hall of Shame",          _render_hall_of_shame),
    Section("standings",   "Standings",              _render_standings_monthly),
    Section("charts",      "Charts",                 _render_charts,           when=_has_charts),
    Section("analytics",   "Season Analysis",        _render_season_analytics),
]

SEASON_SECTIONS: list[Section] = [
    Section("cover",       None,                    _render_cover,            cover=True),
    Section("hall-fame",   "Hall of Fame",           _render_hall_of_fame),
    Section("hall-shame",  "Hall of Shame",          _render_hall_of_shame),
    Section("standings",   "Standings",              _render_standings_monthly),
    Section("charts",      "Charts",                 _render_charts,           when=_has_charts),
    Section("analytics",   "Season Analysis",        _render_season_analytics),
]


def _assemble(sections: list[Section], ctx: dict) -> str:
    league_name = ctx["season"].name
    period_label = ctx["period_label"]
    pages = []
    n = 0
    for s in sections:
        if s.when and not s.when(ctx):
            continue
        result = s.render(ctx)
        # Renderers may return a list of page bodies for multi-page sections
        if not isinstance(result, list):
            result = [result]
        for i, inner in enumerate(result):
            title = s.title if i == 0 else (f"{s.title} (cont.)" if s.title else None)
            n_this = None if s.cover else (n := n + 1)
            pages.append(_wrap_page(inner, title, n_this, league_name, period_label, s.cover))
    traj = ctx.get("trajectory_spec")
    bump = ctx.get("bump_spec")
    dec  = ctx.get("decision_chart_specs") or []
    return _full_doc(pages, ctx.get("chart_specs", []),
                     [traj] if traj else [],
                     [bump] if bump else [],
                     dec or None)


# ──────────────────────────────────────────────────────────────────────────────
# Public entry points
# ──────────────────────────────────────────────────────────────────────────────

def render_pdf_weekly_html(season, awards, roasts, period_label, week,
                           rivalry_matchups=None, recap="",
                           decision_lines=None, decision_awards=None) -> str:
    from .render import _scatter_spec, _bar_spec
    import statistics as _st

    week_stats = S.week_report_stats(season, week)
    wd = season.weeks.get(week, {})
    scores = S.weekly_scores(wd)
    avg = _st.mean(scores.values()) if scores else 0.0

    # ── Build individual chart specs for each page ──────────────────
    # Luck scatter (page 5)
    ap = S.all_play(wd)
    luck_points = []
    if ap and scores:
        median = S.weekly_median(wd)
        for rid, (w, l, _t) in ap.items():
            ap_pct = round(w / (w + l) * 100, 1) if (w + l) else 0.0
            pts = scores.get(rid, 0)
            luck_points.append({
                "x": ap_pct, "y": pts, "label": season.team_name(rid),
                "good": pts >= median,
                "tooltip": f"{season.team_name(rid)}: {pts:.1f} pts, {ap_pct:.0f}% all-play",
            })
    luck_spec = None
    if luck_points:
        lo = min(p["y"] for p in luck_points); hi = max(p["y"] for p in luck_points)
        pad = max(5.0, (hi - lo) * 0.1)
        luck_spec = _scatter_spec(
            "luckChart", "Luck: All-Play Win% vs Points Scored", luck_points,
            "All-Play Win % (this week)", "Points scored",
            (0, 100), (lo - pad, hi + pad), with_diagonal=False,
            caption_key="luck_chart_weekly",
        )
        # Full page width, compact height (stacked below table on page 5)
        luck_spec["width"] = _LUCK_SCATTER_W
        luck_spec["height"] = _LUCK_SCATTER_H

    # Avg delta bar — page 6 (Median) ONLY. Not reused on page 4 (leaderboard) so
    # there is no canvas-ID collision and the chart renders correctly.
    avg_bars = [{"label": season.team_name(rid), "value": round(pts - avg, 1)}
                for rid, pts in scores.items()]
    avg_spec = None
    if avg_bars:
        avg_spec = _bar_spec(
            "avgDeltaChart", "Points vs Week Average", avg_bars,
            "Points vs league average", color_by_sign=True,
            caption_key="avg_delta_chart",
        )
        avg_spec["width"] = _MED_BAR_W
        avg_spec["height"] = _MED_BAR_H

    # Efficiency bar — page 4 (Leaderboard), full width below the table.
    # Height computed from bar count so all teams render without truncation.
    eff_bars = [{"label": season.team_name(rid), "value": ws.get("efficiency", 0.0)}
                for rid, ws in week_stats.items() if ws.get("efficiency") is not None]
    eff_spec = None
    if eff_bars:
        eff_spec = _bar_spec(
            "efficiencyChart", "This Week's Lineup Efficiency", eff_bars,
            "Lineup efficiency %", x_range=(0, 100),
            caption_key="efficiency_chart",
        )
        eff_spec["width"]  = _SMALL_BAR_W
        eff_spec["height"] = max(260, len(eff_bars) * 28 + 40)

    # Bump / ranking chart (page 5) and scoring trajectory (page 8)
    bump_spec = _bump_spec(season, week) or None
    traj_spec = _trajectory_spec(season, week)

    # History index for previous-season H2H in rivalry cards — already cached
    # on disk from the first run; this is a fast dict-load, not a network call.
    try:
        hist_index = H.build_index(season.league_id)
    except Exception:
        hist_index = None

    # ── Transactions HQ data ────────────────────────────────────────────
    all_weeks = sorted(season.weeks.keys())

    # This week's simple transfer list (no pts — they haven't played yet)
    txn_ledger = W.transaction_ledger(season, [week], max_week=week)

    # Historical ledger: all season pickups, pts capped at the report week
    hist_ledger = W.transaction_ledger(season, all_weeks, max_week=week)

    # All season trades, pts capped at report week
    trade_ledger_data = W.trade_ledger(season, all_weeks, max_week=week)

    # Bandwagon + activity: season-wide (historical)
    bandwagon  = W.bandwagon_counts(season, all_weeks, top_n=8)
    act_counts = W.activity_counts(season, all_weeks)
    hit_rate   = W.waiver_hit_rate(season, all_weeks)
    faab_rem   = W.faab_remaining(season)

    # FAAB scatter: season-wide (hist_ledger), pts capped at report week.
    # Labels only on the top-5 best (high pts, low faab) and top-5 worst
    # (high faab, low pts) so the chart stays readable with many dots.
    faab_scatter_spec = None
    if hist_ledger:
        with_pts = [r for r in hist_ledger if r["points_since"] > 0]
        steal_rows = sorted(with_pts, key=lambda r: r["points_since"] - r["faab"] * 0.5, reverse=True)
        bust_rows  = sorted([r for r in hist_ledger if r["faab"] > 0],
                            key=lambda r: r["cost_per_point"] if r["points_since"] > 0 else 1e9,
                            reverse=True)
        # Top-5 labelled; the single best and worst also get a larger radius
        top_steal_ids = {r["player_id"] for r in steal_rows[:5]}
        top_bust_ids  = {r["player_id"] for r in bust_rows[:5]}
        best_pid = steal_rows[0]["player_id"] if steal_rows else None
        bust_pid = bust_rows[0]["player_id"] if bust_rows else None
        show_ids = top_steal_ids | top_bust_ids

        zero_idx = 0
        step = 0.18
        faab_pts = []
        for row in hist_ledger:
            x = float(row["faab"])
            y = row["points_since"]
            if x == 0:
                x = round(zero_idx * step, 2)
                zero_idx += 1
            is_highlight = row["player_id"] in (best_pid, bust_pid)
            faab_pts.append({
                "x": x, "y": y,
                "label": row["player_name"],
                "showLabel": row["player_id"] in show_ids,
                "radius": 11 if is_highlight else 5,
                "tooltip": (f"Wk{row['week']} {row['player_name']} ({row['manager']}): "
                            f"${row['faab']} → {y:.1f} pts"),
                "good": row["hit"],
            })
        true_max_x = max(row["faab"] for row in hist_ledger) if hist_ledger else 0
        true_max_y = max(p["y"] for p in faab_pts) if faab_pts else 0
        faab_scatter_spec = _scatter_spec(
            "faabScatterChart", f"FAAB Efficiency — Weeks 1–{week} (pts capped at Wk {week})",
            faab_pts, "FAAB spent ($)", "Points since acquisition",
            (0, true_max_x + max(3, true_max_x * 0.12)),
            (0, true_max_y + max(5, true_max_y * 0.12)),
            with_diagonal=False, caption_key=None,
        )
        faab_scatter_spec["caption"] = (
            f"Every pickup Wks 1–{week}. Pts = cumulative starts on this roster through Wk {week} only. "
            "Large circles = season best value (green) and season biggest bust (red). "
            "Labelled: top 5 of each.")
        faab_scatter_spec["width"]  = _FAAB_SCATTER_W
        faab_scatter_spec["height"] = _FAAB_SCATTER_H

    # FAAB remaining bar
    faab_remaining_spec = None
    if faab_rem:
        rem_bars = [{"label": season.team_name(rid), "value": rem}
                    for rid, rem in sorted(faab_rem.items(), key=lambda kv: kv[1], reverse=True)]
        faab_remaining_spec = _bar_spec(
            "faabRemainingChart", f"FAAB Remaining (cap: ${season.faab_budget})", rem_bars,
            "Remaining FAAB ($)", x_range=(0, season.faab_budget), caption_key=None,
        )
        faab_remaining_spec["caption"] = "Remaining budget for the rest of the season."
        faab_remaining_spec["width"]  = _TXN_BAR_W
        # Height computed from bar count so all managers show without truncation.
        # The waiver page uses a cols-w layout so this height doesn't displace the table.
        faab_remaining_spec["height"] = max(260, len(rem_bars) * 28 + 40)

    # Bandwagon bars
    bw_added_spec = bw_dropped_spec = None
    if bandwagon.get("added"):
        bw_added_spec = _bar_spec(
            "bwAddedChart", "Most Added", [{"label": n, "value": c} for n, c in bandwagon["added"]],
            "Times added", caption_key=None,
        )
        bw_added_spec["width"]  = _BW_BAR_W
        bw_added_spec["height"] = _BW_BAR_H
    if bandwagon.get("dropped"):
        bw_dropped_spec = _bar_spec(
            "bwDroppedChart", "Most Dropped", [{"label": n, "value": c} for n, c in bandwagon["dropped"]],
            "Times dropped", caption_key=None,
        )
        bw_dropped_spec["width"]  = _BW_BAR_W
        bw_dropped_spec["height"] = _BW_BAR_H

    # Trade net value bar — full season, pts capped at report week
    trade_net_spec = None
    tv = W.trade_value_by_team(season, all_weeks, max_week=week)
    if tv:
        tv_bars = [{"label": season.team_name(rid), "value": net} for rid, net in tv.items()]
        trade_net_spec = _bar_spec(
            "tradeNetChart", "Season Trade Net Value", tv_bars,
            "Net pts from trades", color_by_sign=True, caption_key=None,
        )
        trade_net_spec["caption"] = "Season-to-date: points scored by received players minus points charged for given-away players."
        trade_net_spec["width"]  = _TXN_BAR_W
        trade_net_spec["height"] = _TXN_BAR_H

    # All-time win% bar chart for the rivalry leaderboard page
    alltime_winpct_spec = None
    if hist_index:
        try:
            at_extra = H.current_season_results(season, before_week=week + 1)
            rid_to_ow = {rid: t.owner_id for rid, t in season.teams.items() if t.owner_id}
            ow_to_rid = {oid: rid for rid, oid in rid_to_ow.items()}
            ows = list(ow_to_rid)
            at_dom: dict = {oa: {"wins": 0, "games": 0} for oa in ows}
            for i, oa in enumerate(ows):
                for ob in ows[i + 1:]:
                    rec = H.head_to_head(hist_index, oa, ob, extra_results=at_extra)
                    if not rec or rec["games"] == 0:
                        continue
                    at_dom[oa]["wins"]  += rec["wins"].get(oa, 0)
                    at_dom[oa]["games"] += rec["games"]
                    at_dom[ob]["wins"]  += rec["wins"].get(ob, 0)
                    at_dom[ob]["games"] += rec["games"]
            at_bars = sorted(
                [{"label": season.team_name(ow_to_rid[oa]),
                  "value": round(d["wins"] / d["games"] * 100, 1)}
                 for oa, d in at_dom.items() if d["games"] > 0],
                key=lambda b: b["value"], reverse=True,
            )
            if at_bars:
                alltime_winpct_spec = _bar_spec(
                    "alltimeWinPctChart", "All-Time H2H Win %", at_bars,
                    "Win % across all H2H matchups", x_range=(0, 100),
                    caption_key=None,
                )
                alltime_winpct_spec["caption"] = (
                    "Aggregate win % across every head-to-head regular-season matchup "
                    "in the league's history — current and previous seasons combined.")
                alltime_winpct_spec["width"]  = 570
                alltime_winpct_spec["height"] = 430
        except Exception:
            alltime_winpct_spec = None

    # ── Decision Lab ────────────────────────────────────────────────────────
    # decision_lines / decision_awards may be pre-computed by cli.py (so that
    # the roast engine can roast decision awards too). If not provided here,
    # compute them now (no roast commentary in that case).
    dec_awards = decision_awards
    dec_lines  = decision_lines
    dec_chart_specs: list = []
    if dec_awards is None:
        try:
            dec_lines, *_ = DEC.enrich_lineups(season, week)
            dec_awards = DEC.compute_decision_awards(dec_lines, season, week)
        except Exception as _dec_exc:
            print(f"  [pdf_render] Decision Lab skipped: {_dec_exc}")
            dec_awards = []
            dec_lines  = []

    if dec_awards and dec_lines:
        try:
            aw_idx = {a.award_id: a for a in dec_awards}
            # Collect pids of non-regression decision award winners so they're not
            # auto-labelled as outliers on the regression scatter (Bug 1 fix).
            exclude_reg_pids: set[str] = set()
            for _a in dec_awards:
                if _a.award_id in ("sell_high", "buy_low"):
                    continue
                _pid = _a.player_id or _a.extra.get("player_id")
                if _pid:
                    exclude_reg_pids.add(str(_pid))

            _scatter = DEC.build_decision_scatter(dec_lines, aw_idx.get("best_start"))
            _regr    = DEC.build_regression_scatter(dec_lines, aw_idx.get("sell_high"),
                                                    aw_idx.get("buy_low"),
                                                    exclude_label_pids=exclude_reg_pids)
            _db      = DEC.build_dumbbell(aw_idx.get("bench_regret"),
                                          aw_idx.get("best_start"), dec_lines)
            _eff     = DEC.build_efficiency_hbar(season, week)
            dec_chart_specs = [_scatter, _regr, _db, _eff]
            # Print position breakdown diagnostic (QB bias question)
            DEC.position_breakdown(dec_lines, dec_awards)
        except Exception as _chart_exc:
            print(f"  [pdf_render] Decision charts skipped: {_chart_exc}")

    # Collect all new chart specs into the global list
    txn_specs = [s for s in [faab_scatter_spec, faab_remaining_spec,
                              bw_added_spec, bw_dropped_spec, trade_net_spec] if s]
    at_specs  = [alltime_winpct_spec] if alltime_winpct_spec else []
    all_specs = [s for s in [luck_spec, eff_spec, avg_spec] if s] + txn_specs + at_specs

    ctx = {
        "season": season, "awards": awards, "roasts": roasts,
        "period_label": period_label, "week": week, "kind": "weekly",
        "rivalry_matchups": rivalry_matchups, "recap": recap,
        "week_stats": week_stats,
        "chart_specs": all_specs,  # all chart specs → DOSSIER_DATA
        "luck_spec": luck_spec,
        "avg_delta_spec": avg_spec,
        "efficiency_spec": eff_spec,
        "bump_spec":       bump_spec,
        "trajectory_spec": traj_spec or None,
        "hist_index": hist_index,
        # Transactions HQ
        "txn_ledger":        txn_ledger,        # this week only (simple table)
        "hist_ledger":       hist_ledger,        # season-wide, pts capped at week
        "trade_ledger":      trade_ledger_data,
        "waiver_hit_rate":   hit_rate,
        "bandwagon":         bandwagon,
        "activity_counts":   act_counts,
        "faab_remaining":    faab_rem,
        "faab_scatter_spec":   faab_scatter_spec,
        "faab_remaining_spec": faab_remaining_spec,
        "bw_added_spec":       bw_added_spec,
        "bw_dropped_spec":     bw_dropped_spec,
        "trade_net_spec":        trade_net_spec,
        "alltime_winpct_spec":   alltime_winpct_spec,
        # Decision Lab
        "decision_awards":       dec_awards or [],
        "decision_chart_specs":  dec_chart_specs,
    }
    return _assemble(WEEKLY_SECTIONS, ctx)


def render_pdf_html(season, awards, roasts, period_label,
                    season_stats=None, kind="monthly", month_stats=None,
                    recap="", waiver_take="") -> str:
    weeks = _relevant_weeks(season, month_stats)
    upto_week = max(weeks) if month_stats else None
    specs = _season_chart_specs(season, season_stats, month_stats, upto_week)
    ctx = {
        "season": season, "awards": awards, "roasts": roasts,
        "period_label": period_label, "kind": kind,
        "season_stats": season_stats, "month_stats": month_stats,
        "weeks": weeks, "upto_week": upto_week,
        "recap": recap, "chart_specs": specs,
    }
    sections = SEASON_SECTIONS if kind == "season" else MONTHLY_SECTIONS
    return _assemble(sections, ctx)
