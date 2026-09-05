#!/usr/bin/env python3
"""
cli.py — Generate a Sleeper dossier (weekly, monthly, or end-of-season).

The product runs on a MONTHLY cadence plus an end-of-season review, with an
optional WEEKLY recap for leagues that want one.

Examples:
    # Latest completed calendar month (default)
    python -m sleeper_dossier.cli --league 123456789

    # A specific month
    python -m sleeper_dossier.cli --league 123456789 --month 2025-10 --html oct.html

    # End-of-season review
    python -m sleeper_dossier.cli --league 123456789 --season --html review.html

    # A specific week's recap (power ranking, luck index, median what-if,
    # rivalry head-to-head per matchup)
    python -m sleeper_dossier.cli --league 123456789 --week 5 --html week5.html

    # Manager-of-the-Month history
    python -m sleeper_dossier.cli --league 123456789 --history

Set ANTHROPIC_API_KEY to enable roast commentary; without it the report
still generates with plain stat lines.
"""

from __future__ import annotations
import argparse
import sys

from . import data as D
from . import awards as A
from . import stats as S
from . import roast as R
from . import render as RND
from . import pdf_render as PRND
from . import calendar_map as CM
from . import monthly as M
from . import waivers as W
from . import history as H
from . import decision as DEC


def _parse_month(s):
    y, m = s.split("-")
    return int(y), int(m)


def _resolve_month(season, month_arg):
    if not season.season.isdigit():
        raise SystemExit("Could not determine season year from league data.")
    season_year = int(season.season)
    played = sorted(season.weeks.keys())
    buckets = CM.group_weeks_by_month(season_year, played)
    if month_arg:
        key = _parse_month(month_arg)
        if key not in buckets:
            avail = ", ".join(CM.month_label(*k) for k in sorted(buckets))
            raise SystemExit(f"No data for {CM.month_label(*key)}. Available: {avail}")
        return key, buckets[key], buckets
    key, weeks = CM.current_month_weeks(season_year, played)
    return key, weeks, buckets


def _print_history(season):
    season_year = int(season.season)
    buckets = CM.group_weeks_by_month(season_year, sorted(season.weeks.keys()))
    print(f"\n  MANAGER OF THE MONTH - {season.name}\n  " + "-" * 40)
    for key in sorted(buckets):
        ms = M.month_stats(season, buckets[key])
        if not ms:
            continue
        winner = max(ms.values(), key=lambda m: m.pts_above_avg)
        print(f"  {CM.month_label(*key):<16} {season.team_name(winner.roster_id):<22}"
              f" + {winner.pts_above_avg:.1f} vs avg")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Generate a Sleeper fantasy dossier (monthly/season).")
    ap.add_argument("--league", required=True, help="Sleeper league ID")
    ap.add_argument("--month", default=None, help="Month as YYYY-MM (default: latest completed)")
    ap.add_argument("--season", action="store_true", help="End-of-season review instead of monthly")
    ap.add_argument("--week", type=int, default=None, help="Specific NFL week number for a weekly recap")
    ap.add_argument("--history", action="store_true", help="Print Manager-of-the-Month history and exit")
    ap.add_argument("--html", metavar="PATH", help="Write HTML to PATH")
    ap.add_argument("--pdf", metavar="PATH", help="Write PDF to PATH (requires: pip install playwright && playwright install chromium)")
    ap.add_argument("--assets", metavar="DIR", help="Screenshot each award card and chart to DIR as individual PNGs (requires playwright)")
    ap.add_argument("--no-roast", action="store_true", help="Skip the Claude commentary layer")
    ap.add_argument("--no-transactions", action="store_true", help="Skip waiver/FAAB fetch (faster)")
    args = ap.parse_args(argv)

    print(f"Fetching league {args.league}...", file=sys.stderr)
    season = D.fetch_season(args.league, fetch_transactions=not args.no_transactions)
    if not season.weeks:
        print("No played weeks found for this league yet.", file=sys.stderr)
        return 1

    if args.history:
        _print_history(season)
        return 0

    if args.season:
        ctx, awards = A.compute_season(season)
        ss = ctx["season_stats"]
        period_label = f"{season.season} Season Review"
        weeks = sorted(season.weeks.keys())
        best = W.best_pickup_period(season, weeks)
        worst = W.worst_faab_period(season, weeks)
        faab_totals = W.faab_spent_by_team(season, weeks)
        trades = W.trades_in(season, weeks)
        commentary = {} if args.no_roast else R.generate_commentary(
            season, awards, kind="season", season_stats=ss)
        roasts = commentary.get("roasts", {})
        recap = commentary.get("recap", "")
        waiver_take = "" if args.no_roast else R.write_waiver_take(
            season, kind="season", best=best, worst=worst, faab_totals=faab_totals, trades=trades)
        text = RND.render_text(season, awards, roasts, period_label=period_label,
                               season_stats=ss, kind="season", recap=recap, waiver_take=waiver_take)
        html_out = RND.render_html(season, awards, roasts, period_label=period_label,
                                   season_stats=ss, kind="season", recap=recap, waiver_take=waiver_take)
    elif args.week:
        week = args.week
        if week not in season.weeks:
            avail = ", ".join(str(w) for w in sorted(season.weeks.keys()))
            raise SystemExit(f"No data for week {week}. Available: {avail}")
        _ctx, awards = A.compute_weekly(season, week)
        week_stats = S.week_report_stats(season, week)
        rivalry_matchups = H.rivalry_matchups_for_week(season, args.league, week)
        period = f"Week {week}"
        label = f"{period} Recap"

        # Decision Lab: compute before roast so decision awards can be roasted
        dec_lines: list = []
        dec_awards: list = []
        try:
            dec_lines, *_ = DEC.enrich_lineups(season, week)
            dec_awards = DEC.compute_decision_awards(dec_lines, season, week)
            DEC.position_breakdown(dec_lines, dec_awards)
        except Exception as _dec_exc:
            print(f"  [cli] Decision Lab skipped: {_dec_exc}", file=sys.stderr)

        # Merge roastable decision awards into the awards list for roast engine
        roastable_dec = [a for a in dec_awards if a.winner_rid is not None
                         and not a.extra.get("unavailable")]
        awards_for_roast = list(awards) + roastable_dec

        commentary = {} if args.no_roast else R.generate_commentary(
            season, awards_for_roast, kind="weekly", period=period, week_stats=week_stats,
            week=week, rivalry_matchups=rivalry_matchups)
        roasts = commentary.get("roasts", {})
        recap = commentary.get("recap", "")
        text = RND.render_weekly_text(season, awards, roasts, period_label=label, week=week,
                                      rivalry_matchups=rivalry_matchups, recap=recap)
        html_out = RND.render_weekly_html(season, awards, roasts, period_label=label, week=week,
                                          rivalry_matchups=rivalry_matchups, recap=recap)
    else:
        (yr, mo), weeks, buckets = _resolve_month(season, args.month)
        if not weeks:
            print("No completed month found yet.", file=sys.stderr)
            return 1
        ms = M.month_stats(season, weeks)
        prev_weeks = CM.previous_month_weeks(buckets, (yr, mo))
        prev_ms = M.month_stats(season, prev_weeks) if prev_weeks else None
        _ctx, awards = A.compute_monthly(season, ms, prev_month_stats=prev_ms)
        ss = S.season_stats(season, upto_week=max(weeks))
        period = CM.month_label(yr, mo)
        label = f"{period} Dossier"
        best = W.best_pickup_period(season, weeks)
        worst = W.worst_faab_period(season, weeks)
        faab_totals = W.faab_spent_by_team(season, weeks)
        trades = W.trades_in(season, weeks)
        commentary = {} if args.no_roast else R.generate_commentary(
            season, awards, kind="monthly", period=period, season_stats=ss, month_stats=ms)
        roasts = commentary.get("roasts", {})
        recap = commentary.get("recap", "")
        waiver_take = "" if args.no_roast else R.write_waiver_take(
            season, kind="monthly", period=period, best=best, worst=worst,
            faab_totals=faab_totals, trades=trades)
        text = RND.render_text(season, awards, roasts, period_label=label,
                               season_stats=ss, kind="monthly", month_stats=ms, recap=recap,
                               waiver_take=waiver_take)
        html_out = RND.render_html(season, awards, roasts, period_label=label,
                                   season_stats=ss, kind="monthly", month_stats=ms, recap=recap,
                                   waiver_take=waiver_take)

    print(text)
    if args.html:
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(html_out)
        print(f"\nHTML written to {args.html}", file=sys.stderr)
    if args.pdf or args.assets:
        if args.week:
            pdf_html = PRND.render_pdf_weekly_html(
                season, awards, roasts, period_label=label, week=week,
                rivalry_matchups=rivalry_matchups, recap=recap,
                decision_lines=dec_lines, decision_awards=dec_awards)
        elif args.season:
            pdf_html = PRND.render_pdf_html(
                season, awards, roasts, period_label=period_label,
                season_stats=ss, kind="season", recap=recap, waiver_take=waiver_take)
        else:
            pdf_html = PRND.render_pdf_html(
                season, awards, roasts, period_label=label,
                season_stats=ss, kind="monthly", month_stats=ms,
                recap=recap, waiver_take=waiver_take)
        if args.pdf:
            from . import pdf as PDF
            print("Generating PDF...", file=sys.stderr)
            PDF.html_to_pdf(pdf_html, args.pdf)
            print(f"PDF written to {args.pdf}", file=sys.stderr)
        if args.assets:
            from . import assets as ASSETS
            print(f"Generating assets to {args.assets}...", file=sys.stderr)
            ASSETS.generate_assets(pdf_html, args.assets)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
