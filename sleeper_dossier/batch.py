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

Or pull the same list from a Google Sheet (see sheet.py) with columns
Date, email, league_id, Teir — the Teir/Date columns also drive the free
trial (see trial.py): a "free" row gets full-tier output for its first
14 days, then drops to the free-tier report.

Usage:
    # Latest completed month for every league
    python -m sleeper_dossier.batch --csv leagues.csv --outdir reports

    # A specific month, and email them (sends real mail)
    python -m sleeper_dossier.batch --csv leagues.csv --month 2025-10 --email

    # A single week's recap for every league, sourced from a Google Sheet
    python -m sleeper_dossier.batch --sheet SHEET_ID --week latest --email

    # End-of-season reviews for everyone
    python -m sleeper_dossier.batch --csv leagues.csv --season
"""

from __future__ import annotations
import argparse
import csv
import os
import html as html_mod
import re
import smtplib
import ssl
import sys
from email.message import EmailMessage

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
from . import trial as T


def generate_one(league_id, month_arg=None, week_arg=None, do_season=False,
                 do_roast=True, do_pdf=False, tier="normal"):
    """Returns (season, html, pdf_html, period_label, email_html), or that shape
    with Nones when there's nothing to report. pdf_html is None when do_pdf=False.

    email_html is the slim, email-client-safe teaser (see render.render_email_html)
    — NOT the full dossier, which is far too large to send inline."""
    season = D.fetch_season(league_id)
    if not season.weeks:
        return None, None, None, None, None

    if week_arg:
        week = max(season.weeks.keys()) if week_arg == "latest" else int(week_arg)
        if week not in season.weeks:
            return season, None, None, None, None
        _ctx, awards = A.compute_weekly(season, week)
        week_stats = S.week_report_stats(season, week)
        rivalry_matchups = H.rivalry_matchups_for_week(season, league_id, week)
        period = f"Week {week}"
        label = f"{period} Recap"

        # Decision Lab: computed even when do_pdf=False so its highest-severity
        # awards can still inform the roast commentary (matches cli.py).
        dec_lines, dec_awards = [], []
        try:
            dec_lines, *_ = DEC.enrich_lineups(season, week)
            dec_awards = DEC.compute_decision_awards(dec_lines, season, week)
        except Exception as e:
            print(f"  [batch] Decision Lab skipped for {season.name} week {week}: {e}", file=sys.stderr)

        roastable_dec = [a for a in dec_awards if a.winner_rid is not None
                         and not a.extra.get("unavailable")]
        awards_for_roast = list(awards) + roastable_dec

        commentary = R.generate_commentary(
            season, awards_for_roast, kind="weekly", period=period, week_stats=week_stats,
            week=week, rivalry_matchups=rivalry_matchups) if do_roast else {}
        roasts = commentary.get("roasts", {})
        recap = commentary.get("recap", "")
        html_out = RND.render_weekly_html(season, awards, roasts, period_label=label, week=week,
                                          rivalry_matchups=rivalry_matchups, recap=recap, tier=tier)
        pdf_out = PRND.render_pdf_weekly_html(
            season, awards, roasts, period_label=label, week=week,
            rivalry_matchups=rivalry_matchups, recap=recap,
            decision_lines=dec_lines, decision_awards=dec_awards) if do_pdf else None
        email_html = RND.render_email_html(season, awards, roasts, label,
                                           pdf_attached=bool(pdf_out))
        return season, html_out, pdf_out, label, email_html

    if do_season:
        ctx, awards = A.compute_season(season)
        ss = ctx["season_stats"]
        label = f"{season.season} Season Review"
        weeks = sorted(season.weeks.keys())
        best = W.best_pickup_period(season, weeks)
        worst = W.worst_faab_period(season, weeks)
        faab_totals = W.faab_spent_by_team(season, weeks)
        trades = W.trades_in(season, weeks)
        commentary = R.generate_commentary(
            season, awards, kind="season", season_stats=ss) if do_roast else {}
        roasts = commentary.get("roasts", {})
        recap = commentary.get("recap", "")
        waiver_take = R.write_waiver_take(season, kind="season", best=best, worst=worst,
                                          faab_totals=faab_totals, trades=trades) if do_roast else ""
        html_out = RND.render_html(season, awards, roasts, period_label=label,
                                   season_stats=ss, kind="season", recap=recap, waiver_take=waiver_take,
                                   tier=tier)
        pdf_out = PRND.render_pdf_html(
            season, awards, roasts, period_label=label,
            season_stats=ss, kind="season", recap=recap, waiver_take=waiver_take) if do_pdf else None
        email_html = RND.render_email_html(season, awards, roasts, label,
                                           pdf_attached=bool(pdf_out))
        return season, html_out, pdf_out, label, email_html

    season_year = int(season.season) if season.season.isdigit() else None
    if season_year is None:
        return season, None, None, None, None
    buckets = CM.group_weeks_by_month(season_year, sorted(season.weeks.keys()))
    if month_arg:
        y, m = month_arg.split("-")
        key = (int(y), int(m))
        weeks = buckets.get(key)
    else:
        key, weeks = CM.current_month_weeks(season_year, sorted(season.weeks.keys()))
    if not weeks:
        return season, None, None, None, None

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
    commentary = R.generate_commentary(
        season, awards, kind="monthly", period=period,
        season_stats=ss, month_stats=ms) if do_roast else {}
    roasts = commentary.get("roasts", {})
    recap = commentary.get("recap", "")
    waiver_take = R.write_waiver_take(season, kind="monthly", period=period, best=best, worst=worst,
                                      faab_totals=faab_totals, trades=trades) if do_roast else ""
    html_out = RND.render_html(season, awards, roasts, period_label=label,
                               season_stats=ss, kind="monthly", month_stats=ms, recap=recap,
                               waiver_take=waiver_take, tier=tier)
    pdf_out = PRND.render_pdf_html(
        season, awards, roasts, period_label=label,
        season_stats=ss, kind="monthly", month_stats=ms,
        recap=recap, waiver_take=waiver_take) if do_pdf else None
    email_html = RND.render_email_html(season, awards, roasts, label,
                                       pdf_attached=bool(pdf_out))
    return season, html_out, pdf_out, label, email_html


def _send_mail(to_addr, subject, text_body, html_body=None, attachment_path=None):
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
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    if attachment_path:
        with open(attachment_path, "rb") as fh:
            msg.add_attachment(fh.read(), maintype="application", subtype="pdf",
                               filename=os.path.basename(attachment_path))

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port) as s:
        s.starttls(context=ctx)
        s.login(user, pw)
        s.send_message(msg)
    return True


UPGRADE_URL = "https://waiver-wire-tap.com/"


def _banner_html(inner: str, bg: str, border: str, color: str) -> str:
    """Wrap a notice in the same 600px centred column the email body uses, so
    a spliced-in banner lines up with the card instead of spanning the full
    window width."""
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:#eef1f6;"><tr><td align="center" style="padding:20px 12px 0;">'
        f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;max-width:600px;"><tr><td '
        f'style="background:{bg};border:1px solid {border};border-radius:8px;'
        f'padding:12px 16px;font:14px/1.45 -apple-system,BlinkMacSystemFont,\'Segoe UI\','
        f'Helvetica,Arial,sans-serif;color:{color};">{inner}</td></tr></table>'
        f'</td></tr></table>'
    )


def _trial_banner(league_name: str, days_remaining: int) -> tuple[str, str]:
    """Returns (html_snippet, text_line) reminding a trial league how long
    they have left at full tier — shown on every dossier email while on
    trial, not just a one-time notice at the end."""
    weeks = T.TRIAL_DAYS // 7
    if days_remaining <= T.BATCH_CYCLE_DAYS:
        msg = (f'This is the last week of your {weeks}-week free trial for "{league_name}". '
               f"Next week you'll move to the free tier — award cards only, no standings, "
               f"charts, luck leaderboard, or waiver/trade breakdown.")
    else:
        msg = (f'You\'re on the free trial for "{league_name}" — {days_remaining} day(s) left '
               f"before you move to the free tier.")
    keep_it_text = f"Keep full access at {UPGRADE_URL}"
    inner = (f'{msg} <a href="{UPGRADE_URL}" style="color:#6b5300;font-weight:600;">'
             f'Keep full access &rarr;</a>')
    html = _banner_html(inner, "#fff8e1", "#f0c36d", "#6b5300")
    return html, f"{msg} {keep_it_text}"


def _splice_after_body(html_doc: str, snippet: str) -> str:
    """Insert snippet immediately after the opening <body> tag, whatever
    attributes it carries. A plain str.replace("<body>") silently no-ops on
    <body style="..."> — which is exactly what the email template uses — and
    a dropped trial banner is the kind of thing nobody notices for weeks."""
    m = re.search(r"<body\b[^>]*>", html_doc, re.I)
    if not m:
        return snippet + html_doc
    return html_doc[:m.end()] + snippet + html_doc[m.end():]


def send_email(to_addr, subject, html_body, *, attachment_path=None, trial_note=None):
    """Sends the slim HTML teaser inline, with the full dossier attached as PDF.

    trial_note, when given, is (html_snippet, text_line) from _trial_banner():
    the html_snippet is spliced right after the <body> tag of the emailed HTML
    (the HTML/PDF saved to disk are left untouched), and the text_line is appended
    to the plain-text alternative.
    """
    text_body = ("Your full dossier is attached as a PDF.\n"
                 "Open it for standings, the luck index, the Decision Lab, "
                 "waivers and trades.")
    if trial_note:
        banner_html, banner_text = trial_note
        html_body = _splice_after_body(html_body, banner_html)
        text_body = f"{text_body}\n\n{banner_text}"
    return _send_mail(to_addr, subject, text_body, html_body, attachment_path=attachment_path)


def send_trial_ended_email(to_addr, league_name):
    """One-time notice sent the week a free-trial league's trial expires —
    see trial.just_converted_to_free() for how "the week it expires" is
    detected without a state file."""
    weeks = T.TRIAL_DAYS // 7
    subject = f"{league_name} — your free trial has ended"
    body = (
        f'Your {weeks}-week free trial of the full Sleeper Dossier for "{league_name}" has ended. '
        f"You've been moved to the free tier, so you'll keep getting the weekly award cards — "
        f"just without the standings, charts, luck leaderboard, and waiver/trade breakdown that "
        f"came with the trial.\n\n"
        f"Want to keep full access? Upgrade at {UPGRADE_URL}"
    )
    return _send_mail(to_addr, subject, body)


def send_welcome_email(to_addr, league_name, html_report, *, attachment_path=None):
    """First email a league ever gets after showing up in the sheet — sent
    once, within a day or two of signup (see trial.is_new_signup). Leads with
    a clear "you're on the trial" message, then includes the most recently
    completed week's report so day one isn't a cold start."""
    weeks = T.TRIAL_DAYS // 7
    subject = f"Welcome to Sleeper Dossier — {league_name} is on the trial"
    text_body = (
        f'Welcome to Sleeper Dossier! "{league_name}" has just been added to the full '
        f"premium tier for a {weeks}-week free trial — standings, charts, the luck "
        f"leaderboard, waiver/trade breakdown, and roasts, all included. Last week's "
        f"full report is attached to kick things off.\n\n"
        f"Keep full access after the trial at {UPGRADE_URL}"
    )
    banner_html = _banner_html(
        f'Welcome! "{html_mod.escape(league_name)}" has been added to the full premium tier '
        f'for a {weeks}-week free trial — your first full report is attached. '
        f'<a href="{UPGRADE_URL}" style="color:#245c24;font-weight:600;">Keep full access &rarr;</a>',
        "#eaf6ea", "#8fc98f", "#245c24")
    html_body = _splice_after_body(html_report, banner_html)
    return _send_mail(to_addr, subject, text_body, html_body, attachment_path=attachment_path)


_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')


def _dossier_filename(league_name: str, ext: str) -> str:
    """"<League Name>-Dossier.<ext>" — keeps the league's own spacing/casing
    (recipients see this as the email attachment name), just strips
    characters that are illegal in file names on Windows/macOS/Linux."""
    safe = _UNSAFE_FILENAME_RE.sub("", league_name).strip() or "League"
    return f"{safe}-Dossier.{ext}"


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _looks_like_email(addr: str) -> bool:
    """Cheap sanity check, not RFC 5322 validation — just enough to catch
    blanks, typos, and placeholder junk ('N/A', 'none', 'asdf') before an
    SMTP call is even attempted."""
    return bool(_EMAIL_RE.match((addr or "").strip()))


def _dedupe_leagues(rows: list) -> list:
    """Keep the first row per league_id; warn about the rest. A duplicate
    row would otherwise fetch the same league and send its email twice in
    one run."""
    seen: set[str] = set()
    out = []
    for row in rows:
        lid = row["league_id"]
        if lid in seen:
            print(f"  ! duplicate league_id '{lid}' ({row['label'] or lid}) — "
                  f"keeping the first row, skipping this one", file=sys.stderr)
            continue
        seen.add(lid)
        out.append(row)
    return out


_NON_DIGIT_RE = re.compile(r"\D+")


def _clean_league_id(raw) -> str:
    """Strips every non-digit character — Sleeper league IDs are always
    numeric, so this survives common paste artifacts: whitespace, a trailing
    slash, or even a whole league URL (".../leagues/123.../") instead of
    just the ID. Sleeper-specific: a future non-Sleeper source (e.g. Yahoo)
    would need its own cleaning, since those IDs aren't purely numeric."""
    return _NON_DIGIT_RE.sub("", str(raw or ""))


def _load_leagues(args):
    """Returns a list of {league_id, label, email, tier, signup_date},
    deduplicated by league_id (first occurrence wins)."""
    if args.sheet:
        from . import sheet as SH
        raw = SH.load_rows(args.sheet, worksheet=args.worksheet)
        rows = [{
            "league_id": _clean_league_id(r.get("league_id")),
            "label": _clean_league_id(r.get("league_id")),
            "email": str(r.get("email", "")).strip(),
            "tier": str(r.get("Teir", "")).strip(),
            "signup_date": str(r.get("Date", "")).strip(),
        } for r in raw if _clean_league_id(r.get("league_id"))]
    else:
        with open(args.csv) as f:
            raw = list(csv.DictReader(f))
        rows = [{
            "league_id": _clean_league_id(row["league_id"]),
            "label": (row.get("league_label") or "").strip() or _clean_league_id(row["league_id"]),
            "email": (row.get("email") or "").strip(),
            "tier": "normal",
            "signup_date": "",
        } for row in raw]
    return _dedupe_leagues(rows)


def _run_welcome(args, leagues):
    """--welcome-new: send a one-time welcome email (latest completed week's
    report + a "you're on the trial" note) to sheet rows whose signup date
    is recent (trial.is_new_signup). Meant for a separate daily cron from
    the weekly --week latest run in main() below — always uses the latest
    completed week regardless of --month/--season/--week.

    Skipped entirely for --csv sources: _load_leagues() gives CSV rows an
    empty signup_date, so is_new_signup() never matches and nothing would
    ever be welcomed."""
    if not args.sheet:
        print("--welcome-new needs --sheet (CSV rows have no signup date, so "
              "nothing would ever count as new) — skipping", file=sys.stderr)
        return 0
    if not args.email:
        print("--welcome-new does nothing without --email — skipping", file=sys.stderr)
        return 0

    if args.pdf:
        from . import pdf as PDF
    os.makedirs(args.outdir, exist_ok=True)

    ok, failed, invalid, bad_email, nothing_yet = 0, 0, 0, 0, 0
    for row in leagues:
        if not T.is_new_signup(row["signup_date"]):
            continue
        lid = row["league_id"]
        label = row["label"] or lid
        if not _looks_like_email(row["email"]):
            print(f"  ! {label}: '{row['email']}' doesn't look like a valid email — skipping welcome",
                  file=sys.stderr)
            bad_email += 1
            continue
        if not D.validate_league_id(lid):
            print(f"  ! {label}: '{lid}' is not a valid Sleeper league ID — skipping", file=sys.stderr)
            invalid += 1
            continue
        tier = T.effective_tier(row["tier"], row["signup_date"])
        try:
            season, html_out, pdf_out, period, email_html = generate_one(
                lid, week_arg="latest", do_roast=not args.no_roast, do_pdf=args.pdf, tier=tier)
            if not html_out:
                print(f"  - {label}: no played weeks yet — nothing to welcome with", file=sys.stderr)
                nothing_yet += 1
                continue
        except Exception as e:
            print(f"  x {label}: {e}", file=sys.stderr)
            failed += 1
            continue

        pdf_path = None
        if args.pdf and pdf_out:
            pdf_path = os.path.join(args.outdir, _dossier_filename(season.name, "pdf"))
            try:
                PDF.html_to_pdf(pdf_out, pdf_path)
            except Exception as pe:
                print(f"     pdf failed: {pe}", file=sys.stderr)
                pdf_path = None

        try:
            send_welcome_email(row["email"], season.name, email_html, attachment_path=pdf_path)
            print(f"  ok {label}: welcomed {row['email']} ({period})")
            ok += 1
        except Exception as e:
            print(f"     welcome email failed for {row['email']}: {e}", file=sys.stderr)
            bad_email += 1

    print(f"\nWelcome run done: {ok} welcomed, {nothing_yet} with no played weeks yet, "
          f"{failed} failed, {invalid} invalid league ID(s), "
          f"{bad_email} email(s) skipped/failed.", file=sys.stderr)
    return 0 if failed == 0 and invalid == 0 and bad_email == 0 else 2


def main(argv=None):
    ap = argparse.ArgumentParser(description="Batch-generate Sleeper dossiers (weekly/monthly/season).")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="leagues.csv (league_id,label,email)")
    src.add_argument("--sheet", help="Google Sheet ID (columns: Date, email, league_id, Teir)")
    ap.add_argument("--worksheet", default=None,
                    help="Tab name within --sheet to read (default: the sheet's first tab). "
                         "Ignored when using --csv.")
    ap.add_argument("--month", default=None, help="Month YYYY-MM (default: latest completed)")
    ap.add_argument("--season", action="store_true", help="End-of-season reviews")
    ap.add_argument("--week", default=None,
                    help="A single NFL week's recap instead of monthly. An integer, or 'latest' "
                         "for each league's most recently completed week")
    ap.add_argument("--outdir", default="reports")
    ap.add_argument("--pdf", action="store_true",
                    help="Also write a PDF alongside each HTML (requires playwright + chromium).")
    ap.add_argument("--email", action="store_true",
                    help="Email each report (requires SMTP_* env vars). Sends real mail.")
    ap.add_argument("--welcome-new", action="store_true",
                    help="Instead of the normal run, email a one-time welcome (latest "
                         "completed week's report + trial note) to --sheet rows whose "
                         "signup date is recent. Meant for a separate daily cron; requires "
                         "--sheet and --email, and ignores --month/--season/--week.")
    ap.add_argument("--no-roast", action="store_true")
    args = ap.parse_args(argv)

    os.makedirs(args.outdir, exist_ok=True)
    leagues = _load_leagues(args)

    if args.welcome_new:
        return _run_welcome(args, leagues)

    if args.pdf:
        from . import pdf as PDF

    ok, failed, invalid, bad_email, nothing_yet = 0, 0, 0, 0, 0
    for row in leagues:
        lid = row["league_id"]
        label = row["label"] or lid
        if not D.validate_league_id(lid):
            print(f"  ! {label}: '{lid}' is not a valid Sleeper league ID — skipping", file=sys.stderr)
            invalid += 1
            continue
        tier = T.effective_tier(row["tier"], row["signup_date"])
        pdf_path = None
        try:
            season, html_out, pdf_out, period, email_html = generate_one(
                lid, month_arg=args.month, week_arg=args.week, do_season=args.season,
                do_roast=not args.no_roast, do_pdf=args.pdf, tier=tier)
            if not html_out:
                print(f"  - {label}: no played weeks yet — nothing to report", file=sys.stderr)
                nothing_yet += 1
                continue
            path = os.path.join(args.outdir, _dossier_filename(season.name, "html"))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html_out)
            print(f"  ok {label} ({tier}): {path}")
            ok += 1
            if args.pdf and pdf_out:
                pdf_path = path.replace(".html", ".pdf")
                try:
                    PDF.html_to_pdf(pdf_out, pdf_path)
                    print(f"     pdf: {pdf_path}")
                except Exception as pe:
                    print(f"     pdf failed: {pe}", file=sys.stderr)
                    pdf_path = None
        except Exception as e:
            print(f"  x {label}: {e}", file=sys.stderr)
            failed += 1
            continue

        # Email is sent outside the block above on purpose: a send failure
        # here shouldn't retroactively turn an already-successful generation
        # into a "failed" one — it gets its own counter instead.
        if args.email and row["email"]:
            if not _looks_like_email(row["email"]):
                print(f"  ! {label}: '{row['email']}' doesn't look like a valid email — skipping send",
                      file=sys.stderr)
                bad_email += 1
            else:
                try:
                    days_left = T.trial_days_remaining(row["tier"], row["signup_date"])
                    trial_note = _trial_banner(season.name, days_left) if days_left is not None else None
                    send_email(row["email"], f"{season.name} - {period}", email_html,
                              attachment_path=pdf_path, trial_note=trial_note)
                    print(f"     emailed {row['email']}" + (" (+pdf)" if pdf_path else "")
                          + (" (+trial reminder)" if trial_note else ""))
                    if T.just_converted_to_free(row["tier"], row["signup_date"]):
                        send_trial_ended_email(row["email"], season.name)
                        print(f"     trial-ended notice sent to {row['email']}")
                except Exception as e:
                    print(f"     email failed for {row['email']}: {e}", file=sys.stderr)
                    bad_email += 1

    print(f"\nDone: {ok} generated, {nothing_yet} with no played weeks yet, "
          f"{failed} failed, {invalid} invalid league ID(s), "
          f"{bad_email} email(s) skipped/failed.", file=sys.stderr)
    return 0 if failed == 0 and invalid == 0 and bad_email == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
