#!/usr/bin/env python3
"""
cli.py — Generate a Sleeper dossier (monthly or end-of-season).

The product runs on a MONTHLY cadence plus an end-of-season review.

Examples:
    # Latest completed calendar month (default)
    python -m sleeper_dossier.cli --league 123456789

    # A specific month
    python -m sleeper_dossier.cli --league 123456789 --month 2025-10 --html oct.html

    # A single week's recap, as a PDF
    python -m sleeper_dossier.cli --league 123456789 --week 5 --pdf week5.pdf

    # End-of-season review
    python -m sleeper_dossier.cli --league 123456789 --season --html review.html

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
from . import calendar_map as CM
from . import monthly as M
from . import waivers as W
from . import pdf as P


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
              f" +{winner.pts_above_avg:.1f} vs avg")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Generate a Sleeper fantasy dossier (monthly/season).")
    ap.add_argument("--league", required=True, help="Sleeper league ID")
    ap.add_argument("--month", default=None, help="Month as YYYY-MM (default: latest completed)")
    ap.add_argument("--week", type=int, default=None, help="A single NFL week's recap instead of monthly")
    ap.add_argument("--season", action="store_true", help="End-of-season review instead of monthly")
    ap.add_argument("--history", action="store_true", help="Print Manager-of-the-Month history and exit")
    ap.add_argument("--html", metavar="PATH", help="Write HTML to PATH")
    ap.add_argument("--pdf", metavar="PATH",
                    help="Write PDF to PATH (requires: pip install playwright && playwright install chromium)")
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

    if args.week:
        week = args.week
        if week not in season.weeks:
            avail = ", ".join(str(w) for w in sorted(season.weeks))
            raise SystemExit(f"No data for week {week}. Available: {avail}")
        ctx, awards = A.compute_weekly(season, week)
        ss = S.season_stats(season, upto_week=week)
        period_label = f"Week {week}"
        best = W.best_pickup_period(season, [week])
        worst = W.worst_faab_period(season, [week])
        faab_totals = W.faab_spent_by_team(season, [week])
        trades = W.trades_in(season, [week])
        roasts = {} if args.no_roast else R.write_roasts(season, awards, kind="week", period=period_label,
                                                         season_stats=ss)
        recap = "" if args.no_roast else R.write_league_recap(season, awards, kind="week", period=period_label,
                                                              season_stats=ss)
        waiver_take = "" if args.no_roast else R.write_waiver_take(
            season, kind="week", period=period_label, best=best, worst=worst,
            faab_totals=faab_totals, trades=trades)
        text = RND.render_text(season, awards, roasts, period_label=period_label,
                               season_stats=ss, kind="week", recap=recap, waiver_take=waiver_take,
                               weeks=[week])
        html_out = RND.render_html(season, awards, roasts, period_label=period_label,
                                   season_stats=ss, kind="week", recap=recap, waiver_take=waiver_take,
                                   weeks=[week])
    elif args.season:
        ctx, awards = A.compute_season(season)
        ss = ctx["season_stats"]
        period_label = f"{season.season} Season Review"
        weeks = sorted(season.weeks.keys())
        best = W.best_pickup_period(season, weeks)
        worst = W.worst_faab_period(season, weeks)
        faab_totals = W.faab_spent_by_team(season, weeks)
        trades = W.trades_in(season, weeks)
        roasts = {} if args.no_roast else R.write_roasts(season, awards, kind="season", season_stats=ss)
        recap = "" if args.no_roast else R.write_league_recap(season, awards, kind="season", season_stats=ss)
        waiver_take = "" if args.no_roast else R.write_waiver_take(
            season, kind="season", best=best, worst=worst, faab_totals=faab_totals, trades=trades)
        text = RND.render_text(season, awards, roasts, period_label=period_label,
                               season_stats=ss, kind="season", recap=recap, waiver_take=waiver_take)
        html_out = RND.render_html(season, awards, roasts, period_label=period_label,
                                   season_stats=ss, kind="season", recap=recap, waiver_take=waiver_take)
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
        roasts = {} if args.no_roast else R.write_roasts(season, awards, kind="monthly", period=period,
                                                         season_stats=ss, month_stats=ms)
        recap = "" if args.no_roast else R.write_league_recap(season, awards, kind="monthly", period=period,
                                                              season_stats=ss, month_stats=ms)
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
    if args.pdf:
        P.html_to_pdf(html_out, args.pdf)
        print(f"PDF written to {args.pdf}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
