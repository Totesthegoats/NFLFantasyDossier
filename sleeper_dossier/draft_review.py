"""
draft_review.py — Standalone draft-grade report for a single league.

Given a league ID, fetches its current-season draft (data.fetch_draft) and
asks Claude — with the web_search tool enabled — to grade each team's draft
against real ADP/expert consensus, the same way a "instant draft grades"
column would. Degrades gracefully like roast.py: without ANTHROPIC_API_KEY,
you still get the draft board, just no grades/commentary.

Usage:
    python -m sleeper_dossier.draft_review --league YOUR_LEAGUE_ID --html draft.html
"""

from __future__ import annotations
import argparse
import html
import re
import sys
from dataclasses import dataclass

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


def _team_picks_block(draft, roster_id) -> str:
    lines = []
    for p in draft.picks:
        if p.roster_id != roster_id:
            continue
        name = D.player_name(draft.players, p.player_id)
        keeper = " (keeper)" if p.is_keeper else ""
        lines.append(f"Round {p.round}, Pick {p.pick_no}: {name} ({p.position}, {p.nfl_team}){keeper}")
    return "\n".join(lines)


def _full_board_block(draft) -> str:
    lines = []
    for p in draft.picks:
        name = D.player_name(draft.players, p.player_id)
        lines.append(f"{p.pick_no}. {draft.team_name(p.roster_id)}: {name} ({p.position})")
    return "\n".join(lines)


def _grade_prompt(draft, roster_id) -> str:
    team = draft.team_name(roster_id)
    return (
        f'You are grading one team\'s fantasy football draft in the {draft.season} season, '
        f'league "{draft.league_name}" ({draft.rounds}-round draft, {len(draft.teams)} teams).\n\n'
        f'Use web search to check current ADP, expert fantasy rankings, and any relevant '
        f'preseason/rookie outlooks for the players below, then grade "{team}"\'s draft.\n\n'
        f'{team}\'s picks:\n{_team_picks_block(draft, roster_id)}\n\n'
        f'For context, the full draft board:\n{_full_board_block(draft)}\n\n'
        f'Respond in exactly this format:\n'
        f'Grade: <a single letter grade, A+ through F>\n'
        f'<3-4 punchy, conversational sentences (max 90 words) explaining the grade — '
        f'reference at least two specific players, their ADP or expert consensus ranking, '
        f'and where this team reached or got a steal. No headers, no markdown, no emoji.>'
    )


def grade_team(draft, roster_id) -> TeamGrade:
    client = R._client()
    if not client:
        return TeamGrade(roster_id=roster_id, grade=None, commentary="")
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=GRADE_MAX_TOKENS,
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}],
            messages=[{"role": "user", "content": _grade_prompt(draft, roster_id)}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        m = _GRADE_RE.search(text)
        grade = m.group(1).upper() if m else None
        commentary = _GRADE_RE.sub("", text, count=1).strip() if m else text
        return TeamGrade(roster_id=roster_id, grade=grade, commentary=commentary)
    except Exception as e:
        print(f"  [draft grade skipped for {draft.team_name(roster_id)}: {e}]", file=sys.stderr)
        return TeamGrade(roster_id=roster_id, grade=None, commentary="")


def grade_all_teams(draft) -> dict:
    return {rid: grade_team(draft, rid) for rid in draft.teams}


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


def _grade_card(draft, rid, tg: TeamGrade) -> str:
    bucket = _grade_bucket(tg.grade)
    grade_html = html.escape(tg.grade) if tg.grade else "—"
    commentary_html = (f'<div class="roast">{html.escape(tg.commentary)}</div>'
                       if tg.commentary else "")
    picks_html = "".join(
        f'<div class="pickrow"><span>R{p.round}.{p.pick_no} {html.escape(D.player_name(draft.players, p.player_id))}</span>'
        f'<span>{html.escape(p.position)}</span></div>'
        for p in draft.picks if p.roster_id == rid
    )
    return f"""<div class="card {bucket}">
      <div class="head"><span>{html.escape(draft.team_name(rid))}</span><span class="grade-badge">{grade_html}</span></div>
      <div class="body">
        {commentary_html}
        <div style="margin-top:10px">{picks_html}</div>
      </div>
    </div>"""


def render_html(draft, grades: dict) -> str:
    ranked = sorted(draft.teams.keys(),
                    key=lambda rid: _GRADE_ORDER.index(grades[rid].grade)
                    if rid in grades and grades[rid].grade in _GRADE_ORDER else len(_GRADE_ORDER))
    cards = "".join(_grade_card(draft, rid, grades.get(rid, TeamGrade(rid, None, ""))) for rid in ranked)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{RND._CSS}{_EXTRA_CSS}</style></head><body><div class="wrap">
  <div class="cover">
    <h1>{html.escape(draft.league_name)} DRAFT GRADES</h1>
    <div class="sub">{html.escape(draft.season)} Draft — {draft.rounds} rounds, {len(draft.teams)} teams</div>
  </div>
  <div class="bar fame">📋 Draft Report Card</div>
  <div class="grid">{cards}</div>
</div></body></html>"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="Grade every team's draft for a Sleeper league.")
    ap.add_argument("--league", required=True, help="Sleeper league ID")
    ap.add_argument("--html", metavar="PATH", help="Write HTML to PATH")
    args = ap.parse_args(argv)

    print(f"Fetching draft for league {args.league}...", file=sys.stderr)
    draft = D.fetch_draft(args.league)
    print(f"Grading {len(draft.teams)} teams (web search + {MODEL})...", file=sys.stderr)
    grades = grade_all_teams(draft)

    for rid in draft.teams:
        tg = grades[rid]
        print(f"  {draft.team_name(rid):<28} {tg.grade or '—'}")

    html_out = render_html(draft, grades)
    if args.html:
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(html_out)
        print(f"\nHTML written to {args.html}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
