"""
roast.py — Claude API commentary layer (degrades gracefully without a key).

One shared engine, used by weekly, monthly, and season reports alike:

  generate_commentary()  — ONE round of generation per report (at most two
                            API calls total, not one per award): the 2-3
                            highest-severity "headline" awards go to
                            MODEL_HEADLINE along with a storyline ranking
                            and the report's opening recap paragraph; every
                            other award goes to MODEL_FILLER. Both calls
                            share ROAST_STYLE_GUIDE so there's no visible
                            seam between a headline roast and a filler one.
  write_waiver_take()     — one short line reacting to the period's waiver
                            pickups, FAAB spend, and trades. Kept separate
                            from generate_commentary() since it's optional,
                            conditional content (only fires when there's
                            waiver/trade activity to react to).

If ANTHROPIC_API_KEY isn't set, or the anthropic package isn't installed,
both return their empty shape ({"roasts": {}, "recap": "", "storylines": []}
/ "") and the report falls back to its plain stat lines — no error, no
missing report, exactly like before this module was rewritten.
"""

from __future__ import annotations
import json
import os
import re
import statistics

try:
    import anthropic
except ImportError:
    anthropic = None

from . import render as RND

# Configurable at a glance: which models do headline vs. filler work, and
# where the line between them sits.
MODEL_HEADLINE = "claude-sonnet-5"
MODEL_FILLER = "claude-haiku-4-5"
SEVERITY_THRESHOLD = 70.0
MAX_HEADLINE_COUNT = 3
MIN_HEADLINE_COUNT = 2

HEADLINE_MAX_TOKENS = 1000
FILLER_MAX_TOKENS = 700
ROAST_MAX_TOKENS = 80
WAIVER_MAX_TOKENS = 80

ROAST_STYLE_GUIDE = """VOICE GUIDE for fantasy football roast/hype commentary:
- Conversational and punchy, like a sharp group-chat reply — not a sports-anchor cliche.
- Every line must reference at least one specific number from the data (a score, a margin,
  a percentage, a record) — vague hype/roasting without a number is forbidden.
- Hall of Fame awards: hype the winner up, genuinely impressed, a little awed.
- Hall of Shame awards: roast the winner, but stay playful — mean enough to sting, not
  genuinely cruel. The team should be able to laugh at it.
- No hashtags, no emoji, no markdown formatting, no exclamation-point spam.
- Higher severity = sharper, more vivid material; lower severity = lighter, quicker jabs —
  match the intensity of the line to how extreme the underlying number actually is.
- Max 25 words per roast line.

EXAMPLES (for tone only — do not reuse these numbers or teams):
- Hall of Fame, high severity: "185 points and a 9-0 all-play week — Put the Kittle on didn't
  just win Week 15, they made the other nine teams look like a bye week."
- Hall of Fame, low severity: "A tidy 97% lineup efficiency from Oh my Goedert — nothing flashy,
  just a manager who set the right lineup."
- Hall of Shame, high severity: "Jackin Goff dropped 167.4 points and STILL lost by 0.5 — that's
  not bad luck, that's a crime scene."
- Hall of Shame, low severity: "Washington Redheads left 72.3 points on the bench, including
  Courtland Sutton's quiet 24.3 — a rough week to trust the wrong gut feeling."
"""


def _client():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not anthropic:
        return None
    return anthropic.Anthropic(api_key=key)


def _scope_for(kind, period, season):
    if kind == "season":
        return f"{season.season} season-end"
    if kind == "weekly":
        return period or "this week"
    return period or "this month"


def _award_dicts(season, awards) -> list:
    return [{
        "title": a.title, "hall": a.hall, "team": season.team_name(a.winner_rid),
        "headline": a.headline, "severity": a.severity,
        "podium": [f"{season.team_name(rid)}: {val}" for rid, val in a.podium],
    } for a in awards]


def _weekly_teams_block(season, week_stats: dict) -> list:
    scores = [v["score"] for v in week_stats.values()]
    median = statistics.median(scores) if scores else 0.0
    out = []
    for rid, ws in week_stats.items():
        out.append({
            "team": season.team_name(rid),
            "score": ws["score"],
            "all_play": f"{ws.get('all_play_w', 0)}-{ws.get('all_play_l', 0)}",
            "efficiency_pct": ws.get("efficiency"),
            "power_rank": ws.get("power_rank"),
            "vs_median": round(ws["score"] - median, 1),
        })
    return sorted(out, key=lambda t: t["score"], reverse=True)


def _monthly_teams_block(season, month_stats: dict, season_stats) -> list:
    out = []
    for rid, m in month_stats.items():
        ss = (season_stats or {}).get(rid)
        out.append({
            "team": season.team_name(rid),
            "record_this_month": f"{m.h2h_w}-{m.h2h_l}" + (f"-{m.h2h_t}" if m.h2h_t else ""),
            "points_this_month": m.total_points,
            "vs_monthly_avg": m.pts_above_avg,
            "rank_move": f"{m.rank_start}->{m.rank_end}" if m.rank_start else f"->{m.rank_end}",
            "efficiency_pct": m.avg_efficiency,
            "season_luck_index": ss.luck_index if ss else None,
        })
    return sorted(out, key=lambda t: t["vs_monthly_avg"], reverse=True)


def _season_teams_block(season, season_stats: dict) -> list:
    out = []
    for rid, t in season.teams.items():
        ss = (season_stats or {}).get(rid)
        out.append({
            "team": season.team_name(rid),
            "record": f"{t.wins}-{t.losses}" + (f"-{t.ties}" if t.ties else ""),
            "points_for": t.points_for,
            "luck_index": ss.luck_index if ss else None,
            "avg_efficiency_pct": ss.avg_efficiency if ss else None,
        })
    return sorted(out, key=lambda t: t["points_for"], reverse=True)


def _rivalry_block(rivalry_matchups) -> list:
    if not rivalry_matchups:
        return []
    return [{
        "matchup": f"{m['name_a']} vs {m['name_b']}",
        "score": f"{m['pa']:.1f}-{m['pb']:.1f}",
        "history": m["line"],
    } for m in rivalry_matchups]


def _build_data_block(season, kind, scope, season_stats, month_stats, week_stats, rivalry_matchups) -> dict:
    block = {"league": season.name, "scope": scope}
    if kind == "weekly":
        block["teams"] = _weekly_teams_block(season, week_stats or {})
        block["rivalries"] = _rivalry_block(rivalry_matchups)
    elif kind == "season":
        block["teams"] = _season_teams_block(season, season_stats or {})
    else:
        block["teams"] = _monthly_teams_block(season, month_stats or {}, season_stats)
    return block


def _parse_json(text: str) -> dict:
    """Strip a markdown fence if the model wrapped its JSON in one, then parse."""
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}


def _call_headline(client, scope, league, data_block, headline_award_dicts) -> dict:
    payload = {
        "scope": scope, "teams": data_block["teams"],
        "rivalries": data_block.get("rivalries", []),
        "awards": headline_award_dicts,
    }
    titles = [a["title"] for a in payload["awards"]]
    prompt = (
        f"{ROAST_STYLE_GUIDE}\n\n"
        f'You are writing the HEADLINE commentary for a fantasy football report, scope: {scope}, '
        f'league "{league}". Below is the full period data as JSON — every team\'s numbers, every '
        f"award given out this period (with a 0-100 severity score — higher means more extreme/"
        f"screenshot-worthy), and rivalry history for this period's matchups if present.\n\n"
        f"{json.dumps(payload, indent=2)}\n\n"
        f"These are this period's {len(titles)} highest-severity awards: {', '.join(titles)}. "
        f"Write your sharpest material for these — they're the ones people will screenshot.\n\n"
        f"Respond with ONLY a JSON object (no markdown fences, no extra commentary) of this exact "
        f"shape:\n"
        f'{{\n'
        f'  "roasts": {{"<award title>": "<one line, max 25 words>", ...}} — one entry for EACH '
        f"of the {len(titles)} awards listed above,\n"
        f'  "storylines": ["<award title>", ...] — those same award titles, re-ranked by how much '
        f"each deserves to be THE headline of this report. Decide this ranking FIRST, before writing "
        f"the recap below, since the recap must open with it.\n"
        f'  "recap": "<2-3 short punchy beats, not one dense paragraph, max 100 words total. Open by '
        f"naming the #1-ranked storyline above and its key number — don't bury it in a scene-setter. "
        f"Then weave in 1-2 more specific numbers from the rest of the period's data. Match the same "
        f"conversational, screenshot-able voice as the style guide above, not a dry stats recap — "
        f'vary sentence length, short sentences are good.>"\n'
        f"}}\n"
        f"Stay scoped to {scope} only — do not reference data outside this period unless this is "
        f"the season-end report."
    )
    try:
        resp = client.messages.create(
            model=MODEL_HEADLINE, max_tokens=HEADLINE_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        return _parse_json(text)
    except Exception as e:
        print(f"  [headline roast call skipped: {e}]")
        return {}


def _call_filler(client, scope, league, data_block, filler_award_dicts, storylines) -> dict:
    payload = {"scope": scope, "teams": data_block["teams"], "awards": filler_award_dicts}
    titles = [a["title"] for a in payload["awards"]]
    headline_note = (f" This period's headline storylines (already written elsewhere — don't "
                     f"repeat their jokes or numbers): {', '.join(storylines)}." if storylines else "")
    prompt = (
        f"{ROAST_STYLE_GUIDE}\n\n"
        f'You are writing routine/filler commentary for a fantasy football report, scope: {scope}, '
        f'league "{league}".{headline_note}\n\n'
        f"{json.dumps(payload, indent=2)}\n\n"
        f"Respond with ONLY a JSON object (no markdown fences, no extra commentary) of this exact "
        f'shape: {{"roasts": {{"<award title>": "<one line, max 25 words>", ...}}}} — one entry '
        f"for EACH of the {len(titles)} awards above: {', '.join(titles)}.\n"
        f"Stay scoped to {scope} only."
    )
    try:
        resp = client.messages.create(
            model=MODEL_FILLER, max_tokens=FILLER_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        return _parse_json(text)
    except Exception as e:
        print(f"  [filler roast call skipped: {e}]")
        return {}


def generate_commentary(season, awards, kind: str = "monthly", period: str | None = None,
                        season_stats=None, month_stats=None, week_stats=None,
                        week=None, rivalry_matchups=None) -> dict:
    """One round of roast generation for a whole report — at most two API
    calls total (headline + filler), replacing the old one-call-per-award
    approach. Returns {"roasts": {title: text}, "recap": str,
    "storylines": [title, ...]}; degrades to the empty shape with no API
    key, no anthropic package, or no awards."""
    client = _client()
    if not client or not awards:
        return {"roasts": {}, "recap": "", "storylines": []}

    scope = _scope_for(kind, period, season)
    ranked = sorted(awards, key=lambda a: a.severity, reverse=True)
    headline = [a for a in ranked if a.severity >= SEVERITY_THRESHOLD][:MAX_HEADLINE_COUNT]
    if len(headline) < min(MIN_HEADLINE_COUNT, len(ranked)):
        headline = ranked[:MIN_HEADLINE_COUNT]
    headline_titles = {a.title for a in headline}
    filler = [a for a in ranked if a.title not in headline_titles]

    data_block = _build_data_block(season, kind, scope, season_stats, month_stats,
                                   week_stats, rivalry_matchups)

    roasts, recap, storylines = {}, "", []

    if headline:
        headline_payload = _award_dicts(season, headline)
        result = _call_headline(client, scope, season.name, data_block, headline_payload)
        roasts.update(result.get("roasts") or {})
        recap = result.get("recap") or ""
        storylines = result.get("storylines") or []

    if filler:
        filler_payload = _award_dicts(season, filler)
        result = _call_filler(client, scope, season.name, data_block, filler_payload, storylines)
        roasts.update(result.get("roasts") or {})

    return {"roasts": roasts, "recap": recap, "storylines": storylines}


def _waiver_block(season, best, worst, faab_totals, trades) -> str:
    lines = []
    if best:
        spend = f"${best.faab}" if best.faab else "a free pickup"
        lines.append(f"Best pickup: {season.team_name(best.roster_id)} added {best.player_name} "
                     f"({spend}) -> {best.points_since:.1f} pts since.")
    if worst:
        lines.append(f"Worst FAAB spend: {season.team_name(worst.roster_id)} paid ${worst.faab} "
                     f"for {worst.player_name} -> only {worst.points_since:.1f} pts since.")
    if faab_totals:
        top = sorted(faab_totals.items(), key=lambda kv: kv[1], reverse=True)[:3]
        lines.append("Top FAAB spend: " + ", ".join(f"{season.team_name(rid)} ${amt}" for rid, amt in top) + ".")
    for t in trades:
        lines.append(f"Trade (week {t.week}): {RND._format_trade(season, t)}.")
    return "\n".join(lines)


def write_waiver_take(season, kind: str = "monthly", period: str | None = None,
                      best=None, worst=None, faab_totals=None, trades=None) -> str:
    """One short line reacting to the period's waiver/trade activity, written
    once — kept as its own call since it's optional, conditional content,
    separate from the headline/filler roast split in generate_commentary()."""
    client = _client()
    block = _waiver_block(season, best, worst, faab_totals or {}, trades or [])
    if not client or not block:
        return ""

    scope = f"{season.season} season" if kind == "season" else (period or "this month")
    prompt = (
        f'Write 1-2 punchy, conversational sentences (max 40 words total, no hashtags, no emoji) '
        f'reacting to the waiver wire and trade activity in a fantasy football {scope} report for '
        f'the league "{season.name}". Reference at least one specific team name and number. '
        f'Stay scoped to {scope} only — do not mention full-season totals unless this is the '
        f'season-end report.\n\n{block}'
    )
    try:
        resp = client.messages.create(
            model=MODEL_HEADLINE,
            max_tokens=WAIVER_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    except Exception as e:
        print(f"  [waiver take skipped: {e}]")
        return ""
