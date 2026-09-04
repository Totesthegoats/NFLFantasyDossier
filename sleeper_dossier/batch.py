#!/usr/bin/env python3
"""
batch.py — Run the dossier for many leagues at once (the Model B engine).

Monthly cadence: generate each paying league's monthly HTML report,
optionally emailing it. Serving league #50 costs the same as league #1 —
this is where the season-pass economics pay off.

leagues.csv format:
    league_id,league_label,email
    123456789,Sunday Lads,commish@example.com
    987654321,Office League,boss@example.com

Usage:
    # Latest completed month for every league
    python -m sleeper_dossier.batch --csv leagues.csv --outdir reports

    # A specific month, and email them (sends real mail)
    python -m sleeper_dossier.batch --csv leagues.csv --month 2025-10 --email

    # End-of-season reviews for everyone
    python -m sleeper_dossier.batch --csv leagues.csv --season
"""

from __future__ import annotations
import argparse
import csv
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage

from . import data as D
from . import awards as A
from . import stats as S
from . import roast as R
from . import render as RND
from . import calendar_map as CM
from . import monthly as M
from . import waivers as W


def generate_one(league_id, month_arg=None, do_season=False, do_roast=True):
    """Returns (season, html, period_label) or (season, None, None)."""
    season = D.fetch_season(league_id)
    if not season.weeks:
        return None, None, None

    if do_season:
        ctx, awards = A.compute_season(season)
        ss = ctx["season_stats"]
        label = f"{season.season} Season Review"
        weeks = sorted(season.weeks.keys())
        best = W.best_pickup_period(season, weeks)
        worst = W.worst_faab_period(season, weeks)
        faab_totals = W.faab_spent_by_team(season, weeks)
        trades = W.trades_in(season, weeks)
        roasts = R.write_roasts(season, awards, kind="season", season_stats=ss) if do_roast else {}
        recap = R.write_league_recap(season, awards, kind="season", season_stats=ss) if do_roast else ""
        waiver_take = R.write_waiver_take(season, kind="season", best=best, worst=worst,
                                          faab_totals=faab_totals, trades=trades) if do_roast else ""
        html_out = RND.render_html(season, awards, roasts, period_label=label,
                                   season_stats=ss, kind="season", recap=recap, waiver_take=waiver_take)
        return season, html_out, label

    season_year = int(season.season) if season.season.isdigit() else None
    if season_year is None:
        return season, None, None
    buckets = CM.group_weeks_by_month(season_year, sorted(season.weeks.keys()))
    if month_arg:
        y, m = month_arg.split("-")
        key = (int(y), int(m))
        weeks = buckets.get(key)
    else:
        key, weeks = CM.current_month_weeks(season_year, sorted(season.weeks.keys()))
    if not weeks:
        return season, None, None

    ms = M.month_stats(season, weeks)
    prev_weeks = CM.previous_month_weeks(buckets, key)
    prev_ms = M.month_stats(season, prev_weeks) if prev_weeks else None
    _ctx, awards = A.compute_monthly(season, ms, prev_month_stats=prev_ms)
    ss = S.season_stats(season, upto_week=max(weeks))
    period = CM.month_label(*key)
    label = f"{period} Dossier"
    best = W.best_pickup_period(season, weeks)
    worst = W.worst_faab_period(season, weeks)
    faab_totals = W.faab_spent_by_team(season, weeks)
    trades = W.trades_in(season, weeks)
    roasts = R.write_roasts(season, awards, kind="monthly", period=period,
                            season_stats=ss, month_stats=ms) if do_roast else {}
    recap = R.write_league_recap(season, awards, kind="monthly", period=period,
                                 season_stats=ss, month_stats=ms) if do_roast else ""
    waiver_take = R.write_waiver_take(season, kind="monthly", period=period, best=best, worst=worst,
                                      faab_totals=faab_totals, trades=trades) if do_roast else ""
    html_out = RND.render_html(season, awards, roasts, period_label=label,
                               season_stats=ss, kind="monthly", month_stats=ms, recap=recap,
                               waiver_take=waiver_take)
    return season, html_out, label


def send_email(to_addr, subject, html_body):
    """
    SMTP via env vars: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM.
    Sends real mail on your behalf — test against your own address first.
    """
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", 587))
    user = os.environ["SMTP_USER"]
    pw = os.environ["SMTP_PASS"]
    sender = os.environ.get("SMTP_FROM", user)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_addr
    msg.set_content("Your dossier is attached as HTML. Open it in a browser.")
    msg.add_alternative(html_body, subtype="html")

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port) as s:
        s.starttls(context=ctx)
        s.login(user, pw)
        s.send_message(msg)
    return True


def _slug(label):
    return label.lower().replace(" ", "_").replace("/", "-")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Batch-generate Sleeper dossiers (monthly/season).")
    ap.add_argument("--csv", required=True, help="leagues.csv (league_id,label,email)")
    ap.add_argument("--month", default=None, help="Month YYYY-MM (default: latest completed)")
    ap.add_argument("--season", action="store_true", help="End-of-season reviews")
    ap.add_argument("--outdir", default="reports")
    ap.add_argument("--email", action="store_true",
                    help="Email each report (requires SMTP_* env vars). Sends real mail.")
    ap.add_argument("--no-roast", action="store_true")
    args = ap.parse_args(argv)

    os.makedirs(args.outdir, exist_ok=True)
    with open(args.csv) as f:
        leagues = list(csv.DictReader(f))

    ok, failed = 0, 0
    for row in leagues:
        lid = row["league_id"].strip()
        label = (row.get("league_label") or lid).strip()
        try:
            season, html_out, period = generate_one(
                lid, month_arg=args.month, do_season=args.season,
                do_roast=not args.no_roast)
            if not html_out:
                print(f"  ! {label}: no data for period", file=sys.stderr)
                failed += 1
                continue
            path = os.path.join(args.outdir, f"{lid}_{_slug(period)}.html")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html_out)
            print(f"  ok {label}: {path}")
            ok += 1
            if args.email and row.get("email"):
                send_email(row["email"].strip(),
                           f"{season.name} - {period}", html_out)
                print(f"     emailed {row['email'].strip()}")
        except Exception as e:
            print(f"  x {label}: {e}", file=sys.stderr)
            failed += 1

    print(f"\nDone: {ok} generated, {failed} failed.", file=sys.stderr)
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
