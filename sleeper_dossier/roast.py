"""
roast.py — Claude API commentary layer (degrades gracefully without a key).

Three kinds of generated text:
  write_roasts()       — one short line per award, grounded in that team's
                          record/rank/luck so it reads as specific, not generic.
  write_league_recap()  — one short narrative paragraph synthesizing the whole
                          period (standings + every award) — the connective
                          tissue a plain stat dump can't produce on its own.
  write_waiver_take()   — one short line reacting to the period's waiver
                          pickups, FAAB spend, and trades.

If ANTHROPIC_API_KEY isn't set, or the anthropic package isn't installed,
all three functions return empty ({} / "") and the report falls back to its
plain stat lines — no error, no missing report.
"""

from __future__ import annotations
import os

try:
    import anthropic
except ImportError:
    anthropic = None

from . import render as RND

MODEL = "claude-sonnet-4-6"
ROAST_MAX_TOKENS = 80
RECAP_MAX_TOKENS = 220


def _client():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not anthropic:
        return None
    return anthropic.Anthropic(api_key=key)


def _ordinal(n):
    if n is None:
        return ""
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _rank_of(season, rid):
    for i, r in enumerate(RND.standings_rows(season), 1):
        if r["rid"] == rid:
            return i
    return None


def _team_context(season, rid, kind, season_stats=None, month_stats=None) -> str:
    """Period-scoped facts only — season totals would drown out monthly framing."""
    bits = []

    if kind == "season":
        team = season.teams.get(rid)
        if team:
            wl = f"{team.wins}-{team.losses}" + (f"-{team.ties}" if team.ties else "")
            bits.append(f"season record {wl}")
            bits.append(f"{team.points_for:.0f} points for on the season")
        rank = _rank_of(season, rid)
        if rank:
            bits.append(f"finished {rank}{_ordinal(rank)} in the final standings")
        ss = (season_stats or {}).get(rid)
        if ss:
            tag = "luckier" if ss.luck_index > 0 else ("unluckier" if ss.luck_index < 0 else "exactly as lucky")
            bits.append(f"{tag} than their record alone suggests (luck index {ss.luck_index:+.1f})")
    else:
        ms = (month_stats or {}).get(rid)
        if ms:
            bits.append(f"{ms.h2h_w}-{ms.h2h_l} this month")
            bits.append(f"{ms.total_points:.0f} points this month")
            if ms.climb > 0:
                bits.append(f"climbed {ms.climb} spot(s) this month (now {ms.rank_end})")
            elif ms.climb < 0:
                bits.append(f"dropped {abs(ms.climb)} spot(s) this month (now {ms.rank_end})")
            bits.append(f"{ms.avg_efficiency:.0f}% lineup efficiency this month")

    return "; ".join(bits)


def _award_prompt(season, award, kind, period, season_stats, month_stats):
    team = season.team_name(award.winner_rid)
    scope = f"{season.season} season-end" if kind == "season" else (period or "this month")
    context = _team_context(season, award.winner_rid, kind, season_stats, month_stats)
    context_line = f" Other context on {team}, from {scope} only: {context}." if context else ""
    stance = "roast them" if award.hall == "shame" else "hype them up"
    return (
        f'Write one punchy, conversational sentence (max 25 words, no hashtags, no emoji) '
        f'for a fantasy football {scope} report. Team "{team}" just won the '
        f'"{award.title}" award ({award.flavour}): {award.headline}.{context_line} '
        f'This is a Hall of {award.hall} award, so {stance}. '
        f'Stay scoped to {scope} only — do not reference full-season totals unless this is the season-end report. '
        f'Reference at least one specific number from the headline or context.'
    )


def write_roasts(season, awards, kind: str = "monthly", period: str | None = None,
                 season_stats=None, month_stats=None) -> dict:
    client = _client()
    if not client or not awards:
        return {}

    roasts = {}
    for award in awards:
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=ROAST_MAX_TOKENS,
                messages=[{"role": "user",
                          "content": _award_prompt(season, award, kind, period, season_stats, month_stats)}],
            )
            text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
            if text:
                roasts[award.title] = text
        except Exception as e:
            print(f"  [roast skipped for {award.title}: {e}]")
    return roasts


def _season_standings_block(season) -> str:
    lines = []
    for i, r in enumerate(RND.standings_rows(season), 1):
        wl = f"{r['w']}-{r['l']}" + (f"-{r['ties']}" if r['ties'] else "")
        lines.append(f"{i}. {r['team']} ({wl}, {r['pf']:.0f} PF)")
    return "\n".join(lines)


def _month_standings_block(season, month_stats) -> str:
    ranked = sorted(month_stats.values(), key=lambda m: m.pts_above_avg, reverse=True)
    lines = []
    for m in ranked:
        move = f"{m.rank_start}->{m.rank_end}" if m.rank_start else f"->{m.rank_end}"
        lines.append(f"{season.team_name(m.roster_id)}: {m.h2h_w}-{m.h2h_l} this month, "
                     f"{m.total_points:.0f} pts, {m.pts_above_avg:+.1f} vs monthly avg, rank {move}")
    return "\n".join(lines)


def _awards_block(season, awards) -> str:
    return "\n".join(
        f'- "{a.title}" ({a.hall}): {season.team_name(a.winner_rid)} - {a.headline}'
        for a in awards
    )


def write_league_recap(season, awards, kind: str = "monthly", period: str | None = None,
                       season_stats=None, month_stats=None) -> str:
    """One short narrative paragraph synthesizing the whole period, written once
    (not per-award) — the only place worth an extra API call per report."""
    client = _client()
    if not client:
        return ""

    if kind == "season":
        scope = f"{season.season} season"
        standings_block = _season_standings_block(season)
        standings_label = "Final season standings"
    else:
        scope = period or "this month"
        standings_block = _month_standings_block(season, month_stats or {})
        standings_label = "This month's results only (not season-to-date)"

    prompt = (
        f'You are writing the opening paragraph of a fantasy football {scope} report for the '
        f'league "{season.name}". Write 3-4 sentences (max 110 words) of punchy, conversational '
        f'commentary on the storylines: the title race, the strongest/weakest teams, any notably '
        f'lucky or unlucky results. Reference at least three specific team names and numbers. '
        f'Stay scoped to {scope} only — do not mention full-season win-loss records or season point '
        f'totals unless this is the season-end report. '
        f'No headers, no markdown, no emoji — plain prose only.\n\n'
        f'{standings_label}:\n{standings_block}\n\n'
        f'Awards given out this period:\n{_awards_block(season, awards)}'
    )
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=RECAP_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    except Exception as e:
        print(f"  [league recap skipped: {e}]")
        return ""


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
    once — same one-extra-call-per-report budget as the league recap."""
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
            model=MODEL,
            max_tokens=ROAST_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    except Exception as e:
        print(f"  [waiver take skipped: {e}]")
        return ""
