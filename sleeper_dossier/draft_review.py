"""
draft_review.py — Standalone draft-grade report for a single league.

Given a league ID, fetches the season (data.fetch_season — draft picks live
on SeasonData.draft_picks) and asks Claude — with the web_search tool
enabled — to grade each team's draft against real ADP/expert consensus, the
same way an "instant draft grades" column would. Degrades gracefully like
roast.py: without ANTHROPIC_API_KEY, you still get the draft board, just no
grades/commentary/stat-award headlines that need the model.

On top of the letter grades, compute_draft_awards() builds a set of
Hall-of-Fame/Shame-style trivia awards (reusing awards.Award + render._card
so they look identical to the rest of the dossier):
  - Cherry Picker / Recipe for Disaster — biggest steal/reach vs. ADP,
    grounded via the same web-search call as the grades. Deliberately NOT
    based on in-season points: that data doesn't exist yet right after a
    draft, which is when this report is actually run.
  - Bye Week Hell / Diversified Portfolio — most / fewest NFL teams
    concentrated on one roster.
  - Position Zealot / Well-Rounded — most picks on one position / most
    balanced across positions.
  - Rookie Hoarder — most first-year players drafted.

Usage:
    python -m sleeper_dossier.draft_review --league YOUR_LEAGUE_ID --html draft.html
"""

from __future__ import annotations
import argparse
import html
import re
import sys
from collections import Counter
from dataclasses import dataclass

from . import awards as A
from . import data as D
from . import render as RND
from . import roast as R

MODEL = "claude-opus-5"
GRADE_MAX_TOKENS = 2048

_GRADE_RE = re.compile(r"grade\s*:\s*([A-F][+-]?)", re.IGNORECASE)
_GRADE_ORDER = ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F"]


@dataclass
class TeamGrade:
    roster_id: int
    grade: str | None
    commentary: str


def _sorted_picks(season):
    """[(player_id, info), ...] from SeasonData.draft_picks, sorted by pick_no."""
    return sorted(season.draft_picks.items(), key=lambda kv: kv[1]["pick_no"])


def _player_pos(season, pid) -> str:
    return (season.players.get(str(pid)) or {}).get("position") or "?"


def _player_team(season, pid) -> str:
    return (season.players.get(str(pid)) or {}).get("team") or "FA"


def _is_rookie(season, pid) -> bool:
    """years_exp is a live snapshot (like injury_status elsewhere in this
    codebase), not pinned to draft day — it only reads as "was a rookie at
    the time of this draft" when run during the same season the draft
    happened in. Run months/a season later and last year's rookies will
    have already ticked up to years_exp=1."""
    return (season.players.get(str(pid)) or {}).get("years_exp") == 0


# ----------------------------------------------------------------------
# Deterministic draft-stat awards — no LLM involved, computed straight off
# SeasonData.draft_picks (+ this season's box scores, once any are played).
# Reuses awards.Award so these render with the exact same _card() the rest
# of the dossier uses.
# ----------------------------------------------------------------------

def _team_by_name(season, name: str):
    """Best-effort team-name -> roster_id lookup for matching the LLM's free-text
    answer back to a real roster (exact match first, then substring either way)."""
    name = (name or "").strip().lower()
    if not name:
        return None
    for rid in season.teams:
        if season.team_name(rid).strip().lower() == name:
            return rid
    for rid in season.teams:
        tn = season.team_name(rid).strip().lower()
        if tn in name or name in tn:
            return rid
    return None


def _pick_by_player_name(season, name: str):
    """Best-effort player-name -> player_id lookup, scoped to this draft's
    own picks so a common name can't accidentally match the wrong player."""
    name = (name or "").strip().lower()
    if not name:
        return None
    for pid in season.draft_picks:
        if D.player_name(season.players, pid).strip().lower() == name:
            return pid
    return None


def _parse_cherry_line(line: str):
    """'Team Name | Player Name (pick 12) | reason text' -> (team, player, reason), or None."""
    parts = line.split("|")
    if len(parts) < 3:
        return None
    team = parts[0].split(":", 1)[-1].strip()
    player_part = parts[1].strip()
    reason = "|".join(parts[2:]).strip()
    pm = re.match(r"^(.*?)\s*\(pick\s*\d+\)\s*$", player_part, re.IGNORECASE)
    player = pm.group(1).strip() if pm else player_part
    if not team or not player or not reason:
        return None
    return team, player, reason


def _cherry_picker_prompt(season, rounds) -> str:
    return (
        f'You are analyzing the full draft board for a fantasy football {season.season} season, '
        f'league "{season.name}" ({rounds}-round draft, {len(season.teams)} teams).\n\n'
        f'Use web search to check ADP and expert fantasy rankings for these players at the time '
        f'of this draft. Then identify the single biggest STEAL (the player who fell furthest '
        f'past their ADP/expert-consensus slot) and the single biggest REACH (the player picked '
        f'furthest ahead of their ADP/expert-consensus slot) across the whole draft.\n\n'
        f'Full draft board:\n{_full_board_block(season)}\n\n'
        f'Respond with exactly these two lines, nothing else:\n'
        f'Steal: <team name> | <player name> (pick <N>) | <one punchy sentence, max 25 words, '
        f'citing their ADP or expert-consensus rank>\n'
        f'Reach: <team name> | <player name> (pick <N>) | <one punchy sentence, max 25 words, '
        f'citing their ADP or expert-consensus rank>'
    )


def cherry_picker_awards(season, rounds) -> list:
    """Web-search-grounded steal/reach, based on real ADP and expert consensus
    rather than in-season production — unlike a points-based version, this
    works immediately after a draft, before a single game has been played."""
    client = R._client()
    if not client:
        return []
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=GRADE_MAX_TOKENS,
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}],
            messages=[{"role": "user", "content": _cherry_picker_prompt(season, rounds)}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    except Exception as e:
        print(f"  [cherry picker skipped: {e}]", file=sys.stderr)
        return []

    lines = {kind: next((ln for ln in text.splitlines() if ln.strip().lower().startswith(f"{kind}:")), None)
             for kind in ("steal", "reach")}

    out = []
    for kind, title, flavour, hall in (
        ("steal", "Cherry Picker", "Biggest steal vs. ADP/expert consensus", "fame"),
        ("reach", "Recipe for Disaster", "Biggest reach vs. ADP/expert consensus", "shame"),
    ):
        line = lines[kind]
        if not line:
            continue
        parsed = _parse_cherry_line(line)
        if not parsed:
            print(f"  [cherry picker: could not parse {kind} line: {line!r}]", file=sys.stderr)
            continue
        team, player, reason = parsed
        rid = _team_by_name(season, team)
        if rid is None:
            continue
        pid = _pick_by_player_name(season, player)
        out.append(A.Award(
            title=title, flavour=flavour, hall=hall, winner_rid=rid, headline=reason, podium=[],
            image_kind="player" if pid else "manager", player_id=pid,
        ))
    return out


def _diversified_portfolio_award(season):
    """Most different NFL teams represented on one roster — the mirror image
    of Bye Week Hell: bye-week risk spread as thin as it can go."""
    best = None  # (distinct_count, roster_id)
    for rid in season.teams:
        nfl_teams = {_player_team(season, pid) for pid, info in season.draft_picks.items()
                     if info.get("roster_id") == rid and _player_team(season, pid) != "FA"}
        count = len(nfl_teams)
        if best is None or count > best[0]:
            best = (count, rid)
    if not best or best[0] < 2:
        return None
    count, rid = best
    return A.Award(
        title="Diversified Portfolio", flavour="Most different NFL teams on one roster", hall="fame",
        winner_rid=rid, headline=f"{count} different NFL teams drafted — bye weeks spread thin", podium=[],
    )


def _well_rounded_award(season):
    """Most different positions drafted — genuine positional balance, the
    mirror image of Position Zealot."""
    best = None  # (distinct_count, roster_id)
    for rid in season.teams:
        positions = {_player_pos(season, pid) for pid, info in season.draft_picks.items()
                     if info.get("roster_id") == rid and _player_pos(season, pid) != "?"}
        count = len(positions)
        if best is None or count > best[0]:
            best = (count, rid)
    if not best or best[0] < 3:
        return None
    count, rid = best
    return A.Award(
        title="Well-Rounded", flavour="Most different positions drafted", hall="fame",
        winner_rid=rid, headline=f"{count} different positions drafted — genuine positional balance", podium=[],
    )


def _bye_week_hell_award(season):
    """Most drafted players sharing one NFL team on a single roster — the
    team most exposed to one bye week wiping out half a lineup."""
    best = None  # (count, roster_id, nfl_team)
    for rid in season.teams:
        counts = Counter(_player_team(season, pid) for pid, info in season.draft_picks.items()
                         if info.get("roster_id") == rid and _player_team(season, pid) != "FA")
        if not counts:
            continue
        team, count = counts.most_common(1)[0]
        if best is None or count > best[0]:
            best = (count, rid, team)
    if not best or best[0] < 2:
        return None
    count, rid, team = best
    names = [D.player_name(season.players, pid) for pid, info in season.draft_picks.items()
             if info.get("roster_id") == rid and _player_team(season, pid) == team]
    return A.Award(
        title="Bye Week Hell", flavour="Most drafted players sharing one NFL team", hall="shame",
        winner_rid=rid, headline=f"{count} picks from {team}: {', '.join(names)}", podium=[],
    )


def _position_zealot_award(season):
    """Most picks spent on a single position — a type, apparently."""
    best = None  # (count, roster_id, position)
    for rid in season.teams:
        counts = Counter(_player_pos(season, pid) for pid, info in season.draft_picks.items()
                         if info.get("roster_id") == rid)
        if not counts:
            continue
        pos, count = counts.most_common(1)[0]
        if best is None or count > best[0]:
            best = (count, rid, pos)
    if not best or best[0] < 3:
        return None
    count, rid, pos = best
    return A.Award(
        title="Position Zealot", flavour="Most picks spent on one position", hall="fame",
        winner_rid=rid, headline=f"{count} {pos}s drafted — clearly a type", podium=[],
    )


def _rookie_hoarder_award(season):
    """Most rookies (years_exp == 0) drafted onto one roster."""
    best = None  # (count, roster_id)
    for rid in season.teams:
        count = sum(1 for pid, info in season.draft_picks.items()
                    if info.get("roster_id") == rid and _is_rookie(season, pid))
        if best is None or count > best[0]:
            best = (count, rid)
    if not best or best[0] < 2:
        return None
    count, rid = best
    return A.Award(
        title="Rookie Hoarder", flavour="Most rookies drafted", hall="fame",
        winner_rid=rid, headline=f"{count} first-year players drafted", podium=[],
    )


def compute_draft_awards(season, rounds) -> list:
    """All draft-stat awards for this draft — the ADP-grounded Cherry Picker/
    Recipe for Disaster pair (one extra web-search-enabled Claude call) plus
    everything computable straight off the draft board. Any that don't apply
    (e.g. no two picks share an NFL team) are silently omitted, not forced."""
    awards = []
    awards.extend(cherry_picker_awards(season, rounds))
    for fn in (_position_zealot_award, _rookie_hoarder_award, _bye_week_hell_award,
              _diversified_portfolio_award, _well_rounded_award):
        a = fn(season)
        if a:
            awards.append(a)
    return awards


def _team_picks_block(season, roster_id) -> str:
    lines = []
    for pid, info in _sorted_picks(season):
        if info.get("roster_id") != roster_id:
            continue
        name = D.player_name(season.players, pid)
        keeper = " (keeper)" if info.get("is_keeper") else ""
        lines.append(f"Round {info.get('round')}, Pick {info['pick_no']}: "
                     f"{name} ({_player_pos(season, pid)}, {_player_team(season, pid)}){keeper}")
    return "\n".join(lines)


def _full_board_block(season) -> str:
    lines = []
    for pid, info in _sorted_picks(season):
        name = D.player_name(season.players, pid)
        lines.append(f"{info['pick_no']}. {season.team_name(info.get('roster_id'))}: "
                     f"{name} ({_player_pos(season, pid)})")
    return "\n".join(lines)


def _grade_prompt(season, roster_id, rounds) -> str:
    team = season.team_name(roster_id)
    return (
        f'You are grading one team\'s fantasy football draft in the {season.season} season, '
        f'league "{season.name}" ({rounds}-round draft, {len(season.teams)} teams).\n\n'
        f'Use web search to check current ADP, expert fantasy rankings, and any relevant '
        f'preseason/rookie outlooks for the players below, then grade "{team}"\'s draft.\n\n'
        f'{team}\'s picks:\n{_team_picks_block(season, roster_id)}\n\n'
        f'For context, the full draft board:\n{_full_board_block(season)}\n\n'
        f'Respond in exactly this format:\n'
        f'Grade: <a single letter grade, A+ through F>\n'
        f'<3-4 punchy, conversational sentences (max 90 words) explaining the grade — '
        f'reference at least two specific players, their ADP or expert consensus ranking, '
        f'and where this team reached or got a steal. No headers, no markdown, no emoji.>'
    )


def grade_team(season, roster_id, rounds) -> TeamGrade:
    client = R._client()
    if not client:
        return TeamGrade(roster_id=roster_id, grade=None, commentary="")
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=GRADE_MAX_TOKENS,
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}],
            messages=[{"role": "user", "content": _grade_prompt(season, roster_id, rounds)}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        m = _GRADE_RE.search(text)
        grade = m.group(1).upper() if m else None
        commentary = _GRADE_RE.sub("", text, count=1).strip() if m else text
        return TeamGrade(roster_id=roster_id, grade=grade, commentary=commentary)
    except Exception as e:
        print(f"  [draft grade skipped for {season.team_name(roster_id)}: {e}]", file=sys.stderr)
        return TeamGrade(roster_id=roster_id, grade=None, commentary="")


def grade_all_teams(season, rounds) -> dict:
    return {rid: grade_team(season, rid, rounds) for rid in season.teams}


def _grade_bucket(grade: str | None) -> str:
    if not grade:
        return "mid"
    if grade.startswith(("A", "B")):
        return "fame"
    if grade.startswith(("D", "F")):
        return "shame"
    return "mid"


_EXTRA_CSS = """
.card.mid .head{background:#5b6b85;}
.grade-badge{font-size:20px;font-weight:800;}
.pickrow{display:flex;justify-content:space-between;font-size:13px;padding:3px 0;
        border-bottom:1px solid #f0f2f6;color:#41506b;}
.pickrow:last-child{border-bottom:none;}
"""


def _grade_card(season, rid, tg: TeamGrade) -> str:
    bucket = _grade_bucket(tg.grade)
    grade_html = html.escape(tg.grade) if tg.grade else "—"
    commentary_html = (f'<div class="roast">{html.escape(tg.commentary)}</div>'
                       if tg.commentary else "")
    picks_html = "".join(
        f'<div class="pickrow"><span>R{info.get("round")}.{info["pick_no"]} '
        f'{html.escape(D.player_name(season.players, pid))}</span>'
        f'<span>{html.escape(_player_pos(season, pid))}</span></div>'
        for pid, info in _sorted_picks(season) if info.get("roster_id") == rid
    )
    return f"""<div class="card {bucket}">
      <div class="head"><span>{html.escape(season.team_name(rid))}</span><span class="grade-badge">{grade_html}</span></div>
      <div class="body">
        {commentary_html}
        <div style="margin-top:10px">{picks_html}</div>
      </div>
    </div>"""


def render_html(season, grades: dict, rounds: int, draft_awards: list | None = None) -> str:
    ranked = sorted(season.teams.keys(),
                    key=lambda rid: _GRADE_ORDER.index(grades[rid].grade)
                    if rid in grades and grades[rid].grade in _GRADE_ORDER else len(_GRADE_ORDER))
    cards = "".join(_grade_card(season, rid, grades.get(rid, TeamGrade(rid, None, ""))) for rid in ranked)

    stats_html = ""
    if draft_awards:
        stat_cards = "".join(RND._card(season, a, {}) for a in draft_awards)
        stats_html = f"""
  <div class="bar fame">📊 Draft Stats &amp; Trivia</div>
  <div class="grid">{stat_cards}</div>"""

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{RND._CSS}{_EXTRA_CSS}</style></head><body><div class="wrap">
  <div class="cover">
    <h1>{html.escape(season.name)} DRAFT GRADES</h1>
    <div class="sub">{html.escape(season.season)} Draft — {rounds} rounds, {len(season.teams)} teams</div>
  </div>
  <div class="bar fame">📋 Draft Report Card</div>
  <div class="grid">{cards}</div>
  {stats_html}
</div></body></html>"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="Grade every team's draft for a Sleeper league.")
    ap.add_argument("--league", required=True, help="Sleeper league ID")
    ap.add_argument("--html", metavar="PATH", help="Write HTML to PATH")
    args = ap.parse_args(argv)

    print(f"Fetching league {args.league}...", file=sys.stderr)
    season = D.fetch_season(args.league, fetch_transactions=False)
    if not season.draft_picks:
        print("This league has no recorded draft.", file=sys.stderr)
        return 1
    rounds = max((info.get("round") or 0) for info in season.draft_picks.values())

    print(f"Grading {len(season.teams)} teams (web search + {MODEL})...", file=sys.stderr)
    grades = grade_all_teams(season, rounds)

    for rid in season.teams:
        tg = grades[rid]
        print(f"  {season.team_name(rid):<28} {tg.grade or '—'}")

    draft_awards = compute_draft_awards(season, rounds)
    if draft_awards:
        print("\nDraft stats & trivia:", file=sys.stderr)
        for a in draft_awards:
            print(f"  {a.title:<20} {season.team_name(a.winner_rid):<28} {a.headline}", file=sys.stderr)

    html_out = render_html(season, grades, rounds, draft_awards)
    if args.html:
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(html_out)
        print(f"\nHTML written to {args.html}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
