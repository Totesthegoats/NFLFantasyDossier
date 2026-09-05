"""
decision.py — Decision Lab: player-level decision analysis for the weekly dossier.

Provides:
  • PlayerLine: per-player data snapshot (actual vs projected vs expected)
  • DecisionAward: production-compatible award compatible with roast engine
  • enrich_lineups(): builds PlayerLine list from Sleeper data + nflverse/Sleeper enrichment
  • 6 award functions + compute_decision_awards() orchestrator
  • Chart data builders for decision scatter, regression scatter, dumbbell, efficiency bar
  • position_breakdown(): surfaces QB bias question in regression watch winners

Data sources:
  projected_pts: Sleeper trailing 4-week average (always available)
  expected_pts priority:
    1. nflverse opportunity model — used when same-year nflverse data is accessible
    2. Sleeper weekly pre-game projections — fallback when nflverse 404s (e.g. 2025)
    3. 0.0 — player excluded from regression awards/scatter
"""

from __future__ import annotations
from dataclasses import dataclass, field

try:
    import nfl_data_py as _nfl
    _HAS_NFL = True
except ImportError:
    _HAS_NFL = False

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

from . import stats as S

_MIN_SNAP_SHARE = 0.25
_COIN_FLIP_WINDOW = 2.5
_COIN_FLIP_MIN_SWING = 5.0
SKILL_POSITIONS = {"QB", "RB", "WR", "TE"}
FLEX_POSITIONS  = {"RB", "WR", "TE"}


# ──────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class PlayerLine:
    """One player's complete data snapshot for the target week."""
    roster_id: int
    manager: str
    player_id: str
    player_name: str
    position: str | None
    nfl_id: str | None
    actual_pts: float
    projected_pts: float
    expected_pts: float
    started: bool
    snap_eligible: bool = True


@dataclass
class DecisionAward:
    """Production-compatible award for decision lab results.

    Implements the same interface as awards.Award so it can be passed to
    roast.generate_commentary() and _pdf_card()-variants without adaptation.
    Extra fields (award_id, detail, player_name, extra, is_decision) are
    decision-lab-specific and ignored by the production rendering path.
    """
    award_id: str
    title: str
    flavour: str
    hall: str               # "fame" | "shame"
    winner_rid: int | None
    headline: str
    detail: str = ""
    podium: list = field(default_factory=list)
    severity: float = 60.0
    image_kind: str = "manager"
    player_id: str | None = None
    player_name: str | None = None
    loser_rid: int | None = None
    extra: dict = field(default_factory=dict)
    is_decision: bool = True   # signals hall-of-fame/shame page renderers to skip


# ──────────────────────────────────────────────────────────────────────────────
# nflverse data: ID crosswalk, snap filter, weekly stats
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_sleeper_id(v) -> str:
    s = str(v)
    return s[:-2] if s.endswith(".0") else s


_id_map_cache: dict | None = None
_pfr_to_gsis_cache: dict | None = None
_snap_filter_cache: dict = {}
_weekly_cache: dict = {}
_sleeper_week_proj_cache: dict = {}


def _load_id_map() -> dict[str, str]:
    global _id_map_cache
    if _id_map_cache is not None:
        return _id_map_cache
    if not _HAS_NFL:
        _id_map_cache = {}
        return _id_map_cache
    try:
        ids = _nfl.import_ids()
        if "sleeper_id" not in ids.columns or "gsis_id" not in ids.columns:
            _id_map_cache = {}
            return _id_map_cache
        valid = ids[ids["sleeper_id"].notna() & ids["gsis_id"].notna()]
        _id_map_cache = {
            _normalize_sleeper_id(row["sleeper_id"]): str(row["gsis_id"])
            for _, row in valid.iterrows()
        }
    except Exception as exc:
        print(f"  [decision] import_ids() failed: {exc}")
        _id_map_cache = {}
    return _id_map_cache


def _build_pfr_to_gsis() -> dict[str, str]:
    global _pfr_to_gsis_cache
    if _pfr_to_gsis_cache is not None:
        return _pfr_to_gsis_cache
    if not _HAS_NFL:
        _pfr_to_gsis_cache = {}
        return _pfr_to_gsis_cache
    try:
        ids = _nfl.import_ids()
        pfr_col = next((c for c in ("pfr_id", "pfr") if c in ids.columns), None)
        if pfr_col and "gsis_id" in ids.columns:
            valid = ids[ids[pfr_col].notna() & ids["gsis_id"].notna()]
            _pfr_to_gsis_cache = {
                str(row[pfr_col]): str(row["gsis_id"]) for _, row in valid.iterrows()
            }
        else:
            _pfr_to_gsis_cache = {}
    except Exception as exc:
        print(f"  [decision] pfr->gsis map failed: {exc}")
        _pfr_to_gsis_cache = {}
    return _pfr_to_gsis_cache


def _load_snap_filter(season_year: int, week: int) -> frozenset[str]:
    cache_key = (season_year, week)
    if cache_key in _snap_filter_cache:
        return _snap_filter_cache[cache_key]
    result: frozenset[str] = frozenset()
    if not _HAS_NFL:
        _snap_filter_cache[cache_key] = result
        return result
    try:
        snap_df = _nfl.import_snap_counts(years=[season_year])
    except Exception as exc:
        print(f"  [decision] import_snap_counts({season_year}) failed: {exc}")
        _snap_filter_cache[cache_key] = result
        return result
    if snap_df is None or (hasattr(snap_df, "empty") and snap_df.empty):
        _snap_filter_cache[cache_key] = result
        return result
    season_col = next((c for c in ("season", "year") if c in snap_df.columns), None)
    week_col   = "week" if "week" in snap_df.columns else None
    if season_col and week_col:
        wk_snap = snap_df[(snap_df[season_col] == season_year) & (snap_df[week_col] == week)]
    elif week_col:
        wk_snap = snap_df[snap_df[week_col] == week]
    else:
        wk_snap = snap_df
    if wk_snap.empty or "offense_pct" not in wk_snap.columns:
        _snap_filter_cache[cache_key] = result
        return result
    eligible = wk_snap[wk_snap["offense_pct"] >= _MIN_SNAP_SHARE]
    if "gsis_id" in eligible.columns:
        result = frozenset(str(v) for v in eligible["gsis_id"].dropna())
    else:
        pid_col = next(
            (c for c in ("pfr_player_id", "pfr_id", "player_id") if c in eligible.columns),
            None,
        )
        if pid_col:
            pfr_map = _build_pfr_to_gsis()
            if pfr_map:
                result = frozenset(
                    pfr_map[str(v)] for v in eligible[pid_col].dropna() if str(v) in pfr_map
                )
            else:
                result = frozenset(str(v) for v in eligible[pid_col].dropna())
    dropped = len(wk_snap) - len(eligible)
    print(f"  [decision snap] week {week}: {len(eligible)} >= "
          f"{_MIN_SNAP_SHARE:.0%} snap share; {dropped} dropped")
    _snap_filter_cache[cache_key] = result
    return result


def _load_season_weekly(season_year: int):
    if not _HAS_NFL:
        return None
    if season_year in _weekly_cache:
        return _weekly_cache[season_year]
    cols = [
        "player_id", "player_display_name", "position",
        "season", "week", "season_type",
        "fantasy_points", "fantasy_points_ppr",
        "targets", "carries", "receptions",
        "attempts", "rushing_yards",
        "passing_tds", "rushing_tds", "receiving_tds",
    ]
    for year in (season_year, season_year - 1):
        try:
            df = _nfl.import_weekly_data(years=[year], columns=cols)
            if df is None or (hasattr(df, "empty") and df.empty):
                continue
            if "season_type" in df.columns:
                df = df[df["season_type"] == "REG"].copy()
            if year != season_year:
                print(f"  [decision] {season_year} unavailable; using {year} data for rates")
            _weekly_cache[season_year] = df
            return df
        except Exception as exc:
            print(f"  [decision] import_weekly_data({year}) failed: {exc}")
    _weekly_cache[season_year] = None
    return None


def _load_sleeper_week_projections(season_year: int, week: int) -> dict[str, float]:
    """Sleeper player_id -> pre-game PPR projection for the given week.

    Calls Sleeper's public projections endpoint. Keyed by Sleeper player_id —
    no crosswalk needed. Returns {} on any network failure.
    """
    cache_key = (season_year, week)
    if cache_key in _sleeper_week_proj_cache:
        return _sleeper_week_proj_cache[cache_key]
    if not _HAS_REQUESTS:
        _sleeper_week_proj_cache[cache_key] = {}
        return {}
    url = f"https://api.sleeper.app/v1/projections/nfl/regular/{season_year}/{week}"
    try:
        resp = _requests.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json() or {}
        result: dict[str, float] = {}
        for pid, stats in data.items():
            if isinstance(stats, dict):
                pts = stats.get("pts_ppr")
                if pts is not None:
                    result[str(pid)] = round(float(pts), 2)
        print(f"  [decision] Sleeper projections ({season_year} wk {week}): {len(result)} players")
        _sleeper_week_proj_cache[cache_key] = result
        return result
    except Exception as exc:
        print(f"  [decision] Sleeper projections failed: {exc}")
        _sleeper_week_proj_cache[cache_key] = {}
        return {}


def _compute_projections(df, week: int, window: int = 4) -> dict[str, float]:
    if df is None or df.empty:
        return {}
    prior = df[(df["week"] < week) & (df["fantasy_points_ppr"].notna())]
    if prior.empty:
        return {}
    max_prior_week = int(prior["week"].max())
    cutoff = max(1, max_prior_week - window + 1)
    windowed = prior[prior["week"] >= cutoff]
    agg = windowed.groupby("player_id")["fantasy_points_ppr"].mean()
    return {pid: round(float(v), 2) for pid, v in agg.items()}


def _compute_expected(df, week: int) -> dict[str, float]:
    if df is None or df.empty:
        return {}
    wk_df = df[df["week"] == week].copy()
    if wk_df.empty:
        return {}

    def _safe_rate(num_col, den_col, pos_filter=None) -> float:
        sub = wk_df if pos_filter is None else wk_df[wk_df["position"].isin(pos_filter)]
        num = sub[num_col].fillna(0).sum()
        den = sub[den_col].fillna(0).sum()
        return float(num / den) if den > 0 else 0.0

    tgt_rate_wr_te = _safe_rate("fantasy_points_ppr", "targets", {"WR", "TE"})
    tgt_rate_rb    = _safe_rate("fantasy_points_ppr", "targets", {"RB"})
    carry_rate_rb  = _safe_rate("fantasy_points_ppr", "carries", {"RB"})
    att_rate_qb    = _safe_rate("fantasy_points_ppr", "attempts", {"QB"})

    expected: dict[str, float] = {}
    for _, row in wk_df.iterrows():
        pid = row.get("player_id")
        pos = str(row.get("position") or "")
        if not pid:
            continue
        t  = float(row.get("targets", 0) or 0)
        c  = float(row.get("carries", 0) or 0)
        at = float(row.get("attempts", 0) or 0)
        ry = float(row.get("rushing_yards", 0) or 0)
        if pos in ("WR", "TE"):
            exp = t * tgt_rate_wr_te
        elif pos == "RB":
            exp = c * carry_rate_rb + t * tgt_rate_rb
        elif pos == "QB":
            rush_bonus = ry * 0.1 + float(row.get("rushing_tds", 0) or 0) * 6
            exp = at * att_rate_qb + rush_bonus
        else:
            continue
        expected[str(pid)] = round(exp, 2)
    return expected


def _sleeper_trailing_projections(season, week: int, window: int = 4) -> dict[str, float]:
    """Trailing-average PPR from THIS season's Sleeper score history.
    Used as projected_pts source; always available since it reads Sleeper data directly."""
    history: dict[str, list[tuple[int, float]]] = {}
    for wk in sorted(season.weeks.keys()):
        if wk >= week:
            break
        wd = season.weeks[wk]
        for wt in wd.values():
            for pid, pts in zip(wt.starters, wt.starter_points or []):
                history.setdefault(pid, []).append((wk, float(pts or 0)))
            for pid, pts in zip(wt.bench, wt.bench_points or []):
                history.setdefault(pid, []).append((wk, float(pts or 0)))
    out: dict[str, float] = {}
    for pid, entries in history.items():
        entries.sort(key=lambda x: x[0])
        recent = [p for _, p in entries[-window:]]
        out[pid] = round(sum(recent) / len(recent), 2) if recent else 0.0
    return out


def _player_name(players: dict, pid: str) -> str:
    p = players.get(str(pid))
    if not p:
        return str(pid)
    name = p.get("full_name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip()
    return name or str(pid)


def _player_pos(players: dict, pid: str) -> str | None:
    p = players.get(str(pid))
    return (p or {}).get("position")


# ──────────────────────────────────────────────────────────────────────────────
# Main enrichment entry point
# ──────────────────────────────────────────────────────────────────────────────

def enrich_lineups(season, week: int) -> tuple[list[PlayerLine], float, float, list[str]]:
    """Build per-player data snapshot for the target week.

    Returns (lines, skill_match_rate, dst_match_rate, unmatched_names).

    expected_pts source priority:
      1. nflverse opportunity model (same season year) — targets/carries × rate
      2. Sleeper weekly pre-game projection — cross-season fallback (no crosswalk needed)
      3. 0.0 — genuinely unknown; player excluded from regression awards/scatter

    projected_pts: Sleeper-native trailing 4-week average (always available).

    Snap filter: import_snap_counts() loaded regardless of same_year — it works
    even when import_weekly_data() returns 404 (different nflverse artifact).
    """
    try:
        season_year = int(season.season)
    except (ValueError, TypeError):
        season_year = 2024

    id_map = _load_id_map()
    df     = _load_season_weekly(season_year)
    same_year = (df is not None and hasattr(df, "empty") and not df.empty and
                 "season" in df.columns and
                 int(df["season"].mode().iloc[0]) == season_year)

    if same_year:
        nfl_proj_map = _compute_projections(df, week)
        exp_map      = _compute_expected(df, week)
        sleeper_week_proj: dict[str, float] = {}
    else:
        nfl_proj_map = {}
        exp_map      = {}
        if df is not None:
            print("  [decision] Cross-season fallback: fetching Sleeper weekly projections.")
        sleeper_week_proj = _load_sleeper_week_projections(season_year, week)
        if not sleeper_week_proj:
            print("  [decision] Sleeper projections unavailable — "
                  "regression scatter will show placeholder.")

    sleeper_proj_map = _sleeper_trailing_projections(season, week)
    snap_eligible_set = _load_snap_filter(season_year, week)

    wd = season.weeks.get(week, {})
    if not wd:
        return [], 0.0, 0.0, []

    lines: list[PlayerLine] = []
    skill_joined = skill_total = dst_joined = dst_total = 0
    unmatched: list[str] = []

    for rid, wt in wd.items():
        manager = season.team_name(rid)
        player_pts: list[tuple[str, float, bool]] = []
        for pid, pts in zip(wt.starters, wt.starter_points or []):
            player_pts.append((pid, float(pts or 0), True))
        for pid, pts in zip(wt.bench, wt.bench_points or []):
            player_pts.append((pid, float(pts or 0), False))

        for pid, actual, started in player_pts:
            pname = _player_name(season.players, pid)
            pos   = _player_pos(season.players, pid)
            is_dst = (pos == "DEF")

            if is_dst:
                dst_total  += 1
                gsis        = f"DST_{pid}"
                dst_joined += 1
                snap_elig   = True
            else:
                skill_total += 1
                gsis = id_map.get(str(pid))
                if gsis:
                    skill_joined += 1
                else:
                    unmatched.append(pname)
                snap_elig = (gsis in snap_eligible_set) if (gsis and snap_eligible_set) else True

            proj = (nfl_proj_map.get(gsis, 0.0)
                    if (gsis and nfl_proj_map and not is_dst) else 0.0)
            if proj <= 0.0:
                proj = sleeper_proj_map.get(pid, 0.0)

            if not is_dst:
                exp = (exp_map.get(gsis) if (same_year and gsis)
                       else sleeper_week_proj.get(str(pid)))
            else:
                exp = None
            if exp is None:
                exp = 0.0

            lines.append(PlayerLine(
                roster_id=rid, manager=manager, player_id=pid,
                player_name=pname, position=pos, nfl_id=gsis,
                actual_pts=actual, projected_pts=proj, expected_pts=exp,
                started=started, snap_eligible=snap_elig,
            ))

    skill_rate = round(skill_joined / skill_total, 3) if skill_total else 0.0
    dst_rate   = round(dst_joined   / dst_total,   3) if dst_total   else 0.0
    skill_lines = [p for p in lines if p.position in SKILL_POSITIONS]
    exp_hits = sum(1 for p in skill_lines if p.expected_pts > 0)
    exp_miss = sum(1 for p in skill_lines if p.expected_pts == 0.0)
    source = "opportunity model" if same_year else "Sleeper pre-game projection"
    print(f"  [decision] expected_pts ({source}): {exp_hits} hits, {exp_miss} misses")
    return lines, skill_rate, dst_rate, unmatched


# ──────────────────────────────────────────────────────────────────────────────
# Award helpers
# ──────────────────────────────────────────────────────────────────────────────

def _by_manager(lines: list[PlayerLine]) -> dict[int, list[PlayerLine]]:
    out: dict = {}
    for p in lines:
        out.setdefault(p.roster_id, []).append(p)
    return out


def _starters(lines): return [p for p in lines if p.started]
def _bench(lines):    return [p for p in lines if not p.started]


# ──────────────────────────────────────────────────────────────────────────────
# Award 1: Best Starting Decision
# ──────────────────────────────────────────────────────────────────────────────

def _award_best_start(by_manager: dict) -> DecisionAward | None:
    """Manager who started a low-projected player who smashed expectations."""
    best = None  # (score, rid, player, contrarian)
    for rid, lines in by_manager.items():
        starters = _starters(lines)
        bench    = _bench(lines)
        bench_median_by_pos: dict[str, float] = {}
        for pos in set(p.position for p in bench if p.position):
            vals = [p.projected_pts for p in bench if p.position == pos and p.projected_pts > 0]
            if vals:
                bench_median_by_pos[pos] = sorted(vals)[len(vals) // 2]
        for p in starters:
            if p.actual_pts <= 0 or p.projected_pts < 0:
                continue
            d = p.actual_pts - p.projected_pts
            if d <= 0:
                continue
            contrarian = bool(
                bench_median_by_pos.get(p.position) and
                p.projected_pts < bench_median_by_pos[p.position]
            )
            score = d * (1.5 if contrarian else 1.0)
            if best is None or score > best[0]:
                best = (score, rid, p, contrarian)
    if not best:
        return None
    _, rid, p, contrarian = best
    contrast = " (projected below bench avg at position)" if contrarian else ""
    return DecisionAward(
        award_id="best_start",
        title="The Gut-Call God",
        flavour="Best starting decision of the week",
        hall="fame",
        winner_rid=rid,
        headline=f"{p.player_name}: {p.actual_pts:.1f} pts (projected {p.projected_pts:.1f})",
        detail=f"+{p.actual_pts - p.projected_pts:.1f} vs projection{contrast}",
        severity=min(100.0, 40.0 + abs(p.actual_pts - p.projected_pts) * 2),
        image_kind="player",
        player_id=p.player_id,
        player_name=p.player_name,
        is_decision=False,   # appears in Hall of Fame, not Decision Lab
        extra={"player_id": p.player_id, "actual": p.actual_pts,
               "projected": p.projected_pts, "contrarian": contrarian,
               "position": p.position},
    )


# ──────────────────────────────────────────────────────────────────────────────
# Award 2: Bench Regret
# ──────────────────────────────────────────────────────────────────────────────

def _award_bench_regret(by_manager: dict) -> DecisionAward | None:
    """Manager who left the most points on the bench vs their worst starter."""
    worst = None  # (points_left, rid, bench_player, started_player)
    for rid, lines in by_manager.items():
        starters = _starters(lines)
        bench    = _bench(lines)
        if not bench:
            continue
        top_bench = max(bench, key=lambda p: p.actual_pts)
        if top_bench.actual_pts <= 0:
            continue
        pos = top_bench.position
        same_pos = [s for s in starters if s.position == pos]
        if not same_pos and pos in FLEX_POSITIONS:
            same_pos = [s for s in starters if s.position in FLEX_POSITIONS]
        if not same_pos:
            same_pos = starters
        worst_started = min(same_pos, key=lambda p: p.actual_pts)
        left = top_bench.actual_pts - worst_started.actual_pts
        if left <= 0:
            continue
        if worst is None or left > worst[0]:
            worst = (left, rid, top_bench, worst_started)
    if not worst:
        return None
    left, rid, b, s = worst
    return DecisionAward(
        award_id="bench_regret",
        title="The Bench Regret Trophy",
        flavour="Most points left rotting on the pine",
        hall="shame",
        winner_rid=rid,
        headline=f"{b.player_name} benched — {b.actual_pts:.1f} pts untouched",
        detail=f"{left:+.1f} vs started {s.player_name} ({s.actual_pts:.1f} pts)",
        severity=min(100.0, 30.0 + left * 2),
        image_kind="player",
        player_id=b.player_id,
        player_name=b.player_name,
        is_decision=False,   # appears in Hall of Shame, not Decision Lab
        extra={
            "bench_player": b.player_name, "bench_player_id": b.player_id,
            "bench_actual": b.actual_pts, "bench_projected": b.projected_pts,
            "started_player": s.player_name, "started_player_id": s.player_id,
            "started_actual": s.actual_pts, "started_projected": s.projected_pts,
            "points_left": left,
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# Award 3: Chalk Merchant
# ──────────────────────────────────────────────────────────────────────────────

def _award_chalk_merchant(by_manager: dict) -> DecisionAward | None:
    """Manager whose lineup most closely matched the consensus projection-optimal."""
    best = None  # (score, rid, actual_proj, opt_proj)
    for rid, lines in by_manager.items():
        starters = _starters(lines)
        n = len(starters)
        if n == 0:
            continue
        available = [p for p in lines if p.projected_pts > 0]
        top_proj  = sorted(available, key=lambda p: p.projected_pts, reverse=True)[:n]
        opt = sum(p.projected_pts for p in top_proj)
        if opt <= 0:
            continue
        act = sum(p.projected_pts for p in starters)
        score = act / opt
        if best is None or score > best[0]:
            best = (score, rid, act, opt)
    if not best:
        return None
    score, rid, act_proj, opt_proj = best
    return DecisionAward(
        award_id="chalk_merchant",
        title="The Chalk Merchant",
        flavour="Lineup closest to consensus projection-optimal",
        hall="fame",
        winner_rid=rid,
        headline=f"{score * 100:.0f}% chalk ({act_proj:.1f} vs optimal {opt_proj:.1f} proj pts)",
        detail=f"Deviated just {opt_proj - act_proj:.1f} projected pts from the 'correct' lineup",
        severity=min(100.0, 50.0 + score * 30),
        image_kind="manager",
        extra={"chalk_score": round(score, 3), "actual_proj": act_proj, "optimal_proj": opt_proj},
    )


# ──────────────────────────────────────────────────────────────────────────────
# Award 4: Coin-Flip Curse
# ──────────────────────────────────────────────────────────────────────────────

def _award_coin_flip_curse(by_manager: dict) -> DecisionAward | None:
    """Manager who lost the closest, highest-stakes start/sit coin-flip."""
    worst = None  # (swing, rid, started, benched, proj_diff)
    for rid, lines in by_manager.items():
        starters = _starters(lines)
        bench    = _bench(lines)
        for s in starters:
            if s.position not in SKILL_POSITIONS:
                continue
            for b in bench:
                if b.position != s.position and not (
                    b.position in FLEX_POSITIONS and s.position in FLEX_POSITIONS
                ):
                    continue
                if s.projected_pts <= 0 or b.projected_pts <= 0:
                    continue
                proj_diff = abs(s.projected_pts - b.projected_pts)
                if proj_diff > _COIN_FLIP_WINDOW:
                    continue
                swing = b.actual_pts - s.actual_pts
                if swing < _COIN_FLIP_MIN_SWING:
                    continue
                if worst is None or swing > worst[0]:
                    worst = (swing, rid, s, b, proj_diff)
    if not worst:
        return None
    swing, rid, started, benched, proj_diff = worst
    return DecisionAward(
        award_id="coin_flip",
        title="The Coin-Flip Curse",
        flavour="Closest start/sit decision that went badly wrong",
        hall="shame",
        winner_rid=rid,
        headline=(f"Started {started.player_name} ({started.actual_pts:.1f} pts) "
                  f"over {benched.player_name} ({benched.actual_pts:.1f} pts)"),
        detail=(f"Projections within {proj_diff:.1f} pts — genuine toss-up. "
                f"Left {swing:.1f} pts on the table."),
        severity=min(100.0, 50.0 + swing * 1.5),
        image_kind="player",
        player_id=benched.player_id,
        player_name=benched.player_name,
        is_decision=False,   # appears in Hall of Shame, not Decision Lab
        extra={
            "started_player": started.player_name, "started_player_id": started.player_id,
            "started_actual": started.actual_pts, "started_projected": started.projected_pts,
            "bench_player": benched.player_name, "bench_player_id": benched.player_id,
            "bench_actual": benched.actual_pts, "bench_projected": benched.projected_pts,
            "proj_diff": proj_diff, "swing": swing,
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# Award 5: Regression Watch (Smoke & Mirrors + The Grinder)
# ──────────────────────────────────────────────────────────────────────────────

def _award_regression_watch(
    lines: list[PlayerLine],
    exclude_pids: set[str] | None = None,
) -> tuple[DecisionAward | None, DecisionAward | None]:
    """
    Sell High (Smoke & Mirrors): player with largest positive actual-vs-expected gap.
    Buy Low (The Grinder): player with largest negative actual-vs-expected gap.

    Returns placeholder awards (extra["unavailable"]=True) when expected data is absent
    so the cards are never silently missing from the report.
    """
    candidates = [p for p in lines if p.position in SKILL_POSITIONS and p.expected_pts > 0]
    skill = [p for p in candidates if p.actual_pts > 0 and p.snap_eligible]
    dropped = len(candidates) - len(skill)
    if dropped:
        print(f"  [decision regression] {dropped} filtered (0 actual pts or low snap share)")

    if not skill:
        _sell = DecisionAward(
            award_id="sell_high", title="Smoke & Mirrors",
            flavour="Sell High — regression model unavailable",
            hall="fame", winner_rid=None,
            headline="Expected-pts data unavailable for this week",
            detail="Regression model requires current-season usage or projection data.",
            severity=0.0, extra={"unavailable": True},
        )
        _buy = DecisionAward(
            award_id="buy_low", title="The Grinder",
            flavour="Buy Low — regression model unavailable",
            hall="shame", winner_rid=None,
            headline="Expected-pts data unavailable for this week",
            detail="Regression model requires current-season usage or projection data.",
            severity=0.0, extra={"unavailable": True},
        )
        return _sell, _buy

    if exclude_pids:
        deduped = [p for p in skill if p.player_id not in exclude_pids]
        if deduped:
            n_excl = len(skill) - len(deduped)
            if n_excl:
                print(f"  [decision regression] {n_excl} skipped (claimed by decision award)")
            skill = deduped

    sell_high = max(skill, key=lambda p: p.actual_pts - p.expected_pts)
    buy_low   = min(skill, key=lambda p: p.actual_pts - p.expected_pts)
    sh_delta  = sell_high.actual_pts - sell_high.expected_pts
    bl_delta  = buy_low.actual_pts   - buy_low.expected_pts

    sell = None
    if sh_delta > 1.0:
        sell = DecisionAward(
            award_id="sell_high",
            title="Smoke & Mirrors",
            flavour="Sell High — over-performing vs opportunity",
            hall="fame",
            winner_rid=sell_high.roster_id,
            headline=(f"{sell_high.player_name}: {sell_high.actual_pts:.1f} pts "
                      f"(expected {sell_high.expected_pts:.1f})"),
            detail=f"+{sh_delta:.1f} above expected — expect regression",
            severity=min(100.0, 40.0 + sh_delta * 2),
            image_kind="player",
            player_id=sell_high.player_id,
            player_name=sell_high.player_name,
            extra={
                "player_id": sell_high.player_id, "actual": sell_high.actual_pts,
                "expected": sell_high.expected_pts, "delta": sh_delta,
                "position": sell_high.position, "manager": sell_high.manager,
            },
        )

    buy = None
    if bl_delta < -1.0:
        buy = DecisionAward(
            award_id="buy_low",
            title="The Grinder",
            flavour="Buy Low — grinding opportunity, not converting",
            hall="shame",
            winner_rid=buy_low.roster_id,
            headline=(f"{buy_low.player_name}: {buy_low.actual_pts:.1f} pts "
                      f"(expected {buy_low.expected_pts:.1f})"),
            detail=f"{bl_delta:.1f} below expected — due positive regression",
            severity=min(100.0, 40.0 + abs(bl_delta) * 2),
            image_kind="player",
            player_id=buy_low.player_id,
            player_name=buy_low.player_name,
            extra={
                "player_id": buy_low.player_id, "actual": buy_low.actual_pts,
                "expected": buy_low.expected_pts, "delta": bl_delta,
                "position": buy_low.position, "manager": buy_low.manager,
            },
        )

    return sell, buy


# ──────────────────────────────────────────────────────────────────────────────
# Award 6: Galaxy Brain (Optimal Lineup Efficiency)
# Left It On The Field is retired — Bumbling Boss (awards.py) covers the shame side.
# ──────────────────────────────────────────────────────────────────────────────

def _award_lineup_efficiency(season, week: int) -> DecisionAward | None:
    wd = season.weeks.get(week, {})
    if not wd:
        return None
    eff = S.lineup_efficiency(season, wd)
    if not eff:
        return None
    ranked = sorted(eff.values(), key=lambda e: e.efficiency, reverse=True)
    top = ranked[0]
    return DecisionAward(
        award_id="galaxy_brain",
        title="Galaxy Brain",
        flavour="Highest lineup efficiency this week",
        hall="fame",
        winner_rid=top.roster_id,
        headline=f"{top.efficiency:.0f}% efficient ({top.actual:.1f} of {top.optimal:.1f} possible)",
        detail=f"Only {top.bench_points:.1f} pts left on the bench",
        severity=min(100.0, 40.0 + top.efficiency * 0.4),
        image_kind="manager",
        is_decision=False,   # appears in Hall of Fame, not Decision Lab
        extra={
            "efficiencies": [
                {"manager": season.team_name(e.roster_id), "efficiency": round(e.efficiency, 1),
                 "actual": e.actual, "optimal": e.optimal}
                for e in ranked
            ],
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────────────────────────

def compute_decision_awards(lines: list[PlayerLine], season, week: int) -> list[DecisionAward]:
    """Compute all 8 decision awards (6 blocks = 8 cards).

    Decision awards claim their headline players first; regression awards
    skip already-claimed players to avoid one player sweeping both categories.
    """
    by_mgr = _by_manager(lines)
    results: list[DecisionAward] = []
    claimed_pids: set[str] = set()

    def _try(fn, *args, **kwargs):
        try:
            r = fn(*args, **kwargs)
            to_add = list(r) if isinstance(r, tuple) else [r]
            for a in to_add:
                if a is None:
                    continue
                results.append(a)
                pid = a.extra.get("player_id") or a.player_id
                if pid:
                    claimed_pids.add(str(pid))
                for key in ("bench_player_id", "started_player_id"):
                    p2 = a.extra.get(key)
                    if p2:
                        claimed_pids.add(str(p2))
        except Exception as exc:
            print(f"  [decision award {fn.__name__} skipped: {exc}]")

    _try(_award_best_start, by_mgr)
    _try(_award_bench_regret, by_mgr)
    _try(_award_chalk_merchant, by_mgr)
    _try(_award_coin_flip_curse, by_mgr)
    _try(_award_regression_watch, lines, exclude_pids=claimed_pids)
    _try(_award_lineup_efficiency, season, week)
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Chart data builders
# ──────────────────────────────────────────────────────────────────────────────

def _apply_label_cap(pts: list[dict], is_key, delta_key,
                     max_extra: int = 2,
                     exclude_pids: set[str] | None = None) -> None:
    """Set showLabel=True on key (award) points + up to max_extra outliers.

    exclude_pids: player_ids to never label as extras (e.g. decision-award winners
    that are already highlighted on a different chart).
    """
    labeled: set[int] = set()
    for i, pt in enumerate(pts):
        if is_key(pt):
            pt["showLabel"] = True
            labeled.add(i)
    unlabeled = sorted(
        ((i, pt) for i, pt in enumerate(pts)
         if i not in labeled
         and (not exclude_pids or pt.get("_pid") not in exclude_pids)),
        key=lambda x: delta_key(x[1]), reverse=True,
    )
    for _, pt in unlabeled[:max_extra]:
        pt["showLabel"] = True


def _scatter_bounds(pts: list[dict]) -> tuple[float, float]:
    if not pts:
        return 0.0, 50.0
    all_v = [p["x"] for p in pts] + [p["y"] for p in pts]
    pad   = max(5.0, (max(all_v) - min(all_v)) * 0.12)
    return round(min(all_v) - pad, 1), round(max(all_v) + pad, 1)


def build_decision_scatter(lines: list[PlayerLine], winner_award) -> dict:
    """Chart 1: X=projected, Y=actual for all starters. Gut-Call God highlighted green."""
    winner_pid = (winner_award.extra.get("player_id") if winner_award else None)
    pts = []
    for p in lines:
        if not p.started or p.projected_pts <= 0:
            continue
        is_winner = (p.player_id == winner_pid)
        pts.append({
            "x": round(p.projected_pts, 1), "y": round(p.actual_pts, 1),
            "label": p.player_name, "showLabel": False,
            "isWinner": is_winner, "isBust": False,
            "r": 9 if is_winner else 5,
            "tooltip": (f"{p.player_name} ({p.manager}): "
                        f"proj {p.projected_pts:.1f} → actual {p.actual_pts:.1f}"),
        })
    _apply_label_cap(pts, is_key=lambda pt: pt["isWinner"],
                     delta_key=lambda pt: abs(pt["y"] - pt["x"]))
    lo, hi = _scatter_bounds(pts)
    return {
        "id": "decisionScatter", "type": "quadrant", "kind": "decision",
        "title": "Decision Scatter: Projected vs Actual (Starters)",
        "caption": ("Top-left = gut-call smash. Bottom-right = chalk flopped. "
                    "Green = Gut-Call God winner."),
        "xLabel": "Projected PPR pts", "yLabel": "Actual PPR pts",
        "xMin": lo, "xMax": hi, "yMin": lo, "yMax": hi,
        "points": pts, "width": 470, "height": 260,
    }


def build_regression_scatter(lines: list[PlayerLine], sell_award, buy_award,
                             exclude_label_pids: set[str] | None = None) -> dict:
    """Chart 2: X=expected, Y=actual for snap-eligible rostered players.

    exclude_label_pids: player_ids already featured on another award (e.g. Gut-Call God)
    that should NOT be auto-labelled as outliers on this chart to avoid confusion.
    """
    unavailable = (
        (sell_award is not None and sell_award.extra.get("unavailable")) or
        (buy_award  is not None and buy_award.extra.get("unavailable"))
    )
    sell_pid = (sell_award.extra.get("player_id") if sell_award else None)
    buy_pid  = (buy_award.extra.get("player_id")  if buy_award  else None)
    pts = []
    if not unavailable:
        for p in lines:
            if p.expected_pts <= 0 or p.actual_pts <= 0 or not p.snap_eligible:
                continue
            is_sell = (p.player_id == sell_pid)
            is_buy  = (p.player_id == buy_pid)
            pts.append({
                "x": round(p.expected_pts, 1), "y": round(p.actual_pts, 1),
                "label": p.player_name, "showLabel": False,
                "isWinner": is_sell, "isBust": is_buy,
                "r": 9 if (is_sell or is_buy) else 5,
                "_pid": p.player_id,   # used by _apply_label_cap exclude filter; not sent to JS
                "tooltip": (f"{p.player_name} ({p.manager}): "
                            f"expected {p.expected_pts:.1f} → actual {p.actual_pts:.1f}"),
            })
        _apply_label_cap(pts, is_key=lambda pt: pt["isWinner"] or pt["isBust"],
                         delta_key=lambda pt: abs(pt["y"] - pt["x"]),
                         max_extra=1,    # regression winners (2) + 1 extreme = 3 labels max
                         exclude_pids=exclude_label_pids)
        # Strip internal field before serialising to JSON
        for pt in pts:
            pt.pop("_pid", None)
    lo, hi = _scatter_bounds(pts)
    return {
        "id": "regressionScatter", "type": "quadrant", "kind": "decision",
        "title": "Regression Watch: Expected vs Actual (All Rostered)",
        "caption": ("Top-left = unlucky grinders (buy low). "
                    "Bottom-right = over-performing (sell high). "
                    "Green = Sell High, Red = Buy Low winner."),
        "xLabel": "Expected PPR pts", "yLabel": "Actual PPR pts",
        "xMin": lo, "xMax": hi, "yMin": lo, "yMax": hi,
        "points": pts,
        "emptyMessage": "Expected-pts data unavailable for this week" if unavailable else None,
        "width": 470, "height": 260,
    }


def build_dumbbell(bench_regret_award, best_start_award, lines: list[PlayerLine]) -> dict:
    """Chart 3: projected vs actual grouped bars for key award players."""
    dumbbells = []
    used: set = set()

    def _add(label, proj, actual):
        if label in used or proj <= 0:
            return
        used.add(label)
        dumbbells.append({"name": label, "projected": round(proj, 1), "actual": round(actual, 1)})

    if best_start_award and not best_start_award.extra.get("unavailable"):
        e = best_start_award.extra
        _add(f"* {best_start_award.player_name}", e.get("projected", 0), e.get("actual", 0))

    if bench_regret_award and not bench_regret_award.extra.get("unavailable"):
        e = bench_regret_award.extra
        _add(f"✗ {e.get('bench_player', 'Benched')}", e.get("bench_projected", 0),
             e.get("bench_actual", 0))
        _add(f"  {e.get('started_player', 'Started')}", e.get("started_projected", 0),
             e.get("started_actual", 0))

    all_vals = [d["projected"] for d in dumbbells] + [d["actual"] for d in dumbbells]
    sug_max  = round(max(all_vals) * 1.22, 1) if all_vals else 50.0
    n = len(dumbbells)
    return {
        "id": "dumbbellChart", "type": "dumbbell", "kind": "decision",
        "title": "Projected vs Actual — Key Award Players",
        "caption": ("* Best Start winner. ✗ Benched player (Bench Regret). "
                    "Gray = projection, colored = actual outcome."),
        "xLabel": "Fantasy Points",
        "suggestedMax": sug_max,
        "dumbbells": dumbbells,
        "width": 470, "height": max(160, 70 * n + 60) if n else 200,
    }


def build_efficiency_hbar(season, week: int) -> dict:
    """Chart 4: lineup efficiency % per manager (highest to lowest)."""
    wd     = season.weeks.get(week, {})
    eff    = S.lineup_efficiency(season, wd) if wd else {}
    ranked = sorted(eff.values(), key=lambda e: e.efficiency, reverse=True)
    bars   = [{"label": season.team_name(e.roster_id), "value": round(e.efficiency, 1)}
              for e in ranked]
    n = len(bars)
    return {
        "id": "efficiencyBar", "type": "hbar", "kind": "decision",
        "title": "Lineup Efficiency Ranking",
        "caption": ("% of optimal score achieved. "
                    "Green = Galaxy Brain (top), Red = lowest efficiency (bottom)."),
        "xLabel": "Lineup Efficiency %",
        "bars": bars,
        "colorTopBottom": True,
        "width": 470, "height": max(180, 30 * n + 60),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Reporting
# ──────────────────────────────────────────────────────────────────────────────

def position_breakdown(lines: list[PlayerLine], awards: list[DecisionAward]) -> dict:
    """Report position breakdown of regression watch winners — surfaces QB bias risk."""
    candidates = [
        p for p in lines
        if p.position in SKILL_POSITIONS and p.expected_pts > 0
        and p.actual_pts > 0 and p.snap_eligible
    ]
    pos_counts: dict[str, int] = {}
    for p in candidates:
        pos_counts[p.position or "?"] = pos_counts.get(p.position or "?", 0) + 1

    aw_idx = {a.award_id: a for a in awards}
    sell   = aw_idx.get("sell_high")
    buy    = aw_idx.get("buy_low")
    sell_pos = sell.extra.get("position") if sell and not sell.extra.get("unavailable") else None
    buy_pos  = buy.extra.get("position")  if buy  and not buy.extra.get("unavailable")  else None

    total      = sum(pos_counts.values())
    sell_share = pos_counts.get(sell_pos, 0) / total if (sell_pos and total) else 0.0

    print(f"  [decision pos breakdown] candidates by pos: {pos_counts}")
    print(f"  [decision pos breakdown] Sell High: {sell_pos} ({sell_share:.0%} of candidates)")
    print(f"  [decision pos breakdown] Buy Low:   {buy_pos}")

    return {
        "sell_high_pos": sell_pos,
        "buy_low_pos":   buy_pos,
        "sell_high_pos_share": round(sell_share, 3),
        "regression_winner_positions": pos_counts,
    }
