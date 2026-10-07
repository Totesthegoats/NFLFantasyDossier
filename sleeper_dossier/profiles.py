"""
profiles.py — per-manager profiles and league analytics for the monthly report.

Gathers analytics.py (Massey, Elo, shrunk scoring, form, luck decomposition)
and manager.py's fingerprint into one dict the PDF renderer can lay out,
so pdf_render only formats and never recomputes.

Everything is capped at `upto_week` for the same reason analytics.py is: a
retrospective monthly report must not leak results from later weeks.
"""

from __future__ import annotations

from . import analytics as AN
from . import manager as MG
from . import stats as S


def _ranks(values: dict, reverse: bool = True) -> dict:
    order = sorted(values, key=lambda r: values[r], reverse=reverse)
    return {rid: i for i, rid in enumerate(order, 1)}


def _identity(fp: dict) -> str:
    """One line describing the shape of a fingerprint: strongest and weakest axis."""
    if not fp:
        return ""
    hi = max(fp, key=fp.get)
    lo = min(fp, key=fp.get)
    if fp[hi] - fp[lo] < 15:
        return "Balanced: no axis stands out from the league"
    return f"High {hi}, low {lo}"


def build(season, weeks: list, upto_week: int | None = None,
          month_stats: dict | None = None, awards=(),
          with_fingerprint: bool = True) -> dict:
    """{"teams": {rid: profile}, "order": [rid, ...], "league": {...}}.

    `weeks` is the report's period (the month, or the whole season for a
    season review). Season-to-date numbers use everything up to `upto_week`;
    month-scoped ones (Elo change, luck split) use `weeks` only.
    """
    played = sorted(w for w in season.weeks if upto_week is None or w <= upto_week)
    if not played:
        return {}
    last = upto_week if upto_week is not None else played[-1]
    period = [w for w in (weeks or played) if w in season.weeks and w <= last]

    massey = AN.massey_ratings(season, upto_week=last)
    traj = AN.elo_trajectory(season, upto_week=last)
    elo_period = AN.elo_month_change(traj, period)
    shrunk = AN.shrunk_scoring(season, upto_week=last)
    form = AN.form_signal(season, upto_week=last)
    luck = AN.luck_decomposition(season, weeks=period, upto_week=last)
    fp = MG.manager_fingerprint(season, period, upto_week=last) if with_fingerprint else {}
    standings = S.standings_through(season, last)

    massey_rank = _ranks({r: v["rating"] for r, v in massey.items()})
    elo_rank = _ranks({r: v["end"] for r, v in traj.items()})

    awards_by_rid: dict = {}
    for a in awards or ():
        if getattr(a, "winner_rid", None) is not None:
            awards_by_rid.setdefault(a.winner_rid, []).append((a.title, a.hall))

    teams = {}
    for rid in season.teams:
        st = standings.get(rid, {})
        ms = (month_stats or {}).get(rid)
        sh = (shrunk.get("teams") or {}).get(rid)
        teams[rid] = {
            "roster_id": rid,
            "record": f"{st.get('wins', 0)}-{st.get('losses', 0)}"
                      + (f"-{st['ties']}" if st.get("ties") else ""),
            "month_record": (f"{ms.h2h_w}-{ms.h2h_l}" + (f"-{ms.h2h_t}" if ms.h2h_t else ""))
                            if ms else None,
            "month_vs_avg": ms.pts_above_avg if ms else None,
            "month_efficiency": ms.avg_efficiency if ms else None,
            "massey": massey.get(rid),
            "massey_rank": massey_rank.get(rid),
            "elo": traj.get(rid),
            "elo_rank": elo_rank.get(rid),
            "elo_period": elo_period.get(rid),
            "shrunk": sh,
            "luck": luck.get(rid),
            "fingerprint": fp.get(rid) or {},
            "identity": _identity(fp.get(rid) or {}),
            "awards": awards_by_rid.get(rid, []),
        }

    # Profiles are read in power order: best team by opponent-adjusted rating first.
    order = sorted(teams, key=lambda r: (massey_rank.get(r) or 99, season.team_name(r)))
    return {
        "teams": teams,
        "order": order,
        "period": period,
        "upto_week": last,
        "league": {
            "shrunk": shrunk.get("league") if shrunk else None,
            "form": form or None,
            "elo_series": {rid: t["series"] for rid, t in traj.items()},
        },
    }
