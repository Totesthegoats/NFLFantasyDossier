"""
sim.py — Monte Carlo playoff-odds simulation.

Bootstrap-resamples each team's own weekly score history (no distributional
assumptions) to project the rest of the regular season and estimate each
team's probability of making the playoffs: a simple top-N-by-(wins,
points_for) cutoff, ignoring divisions and reseeding — the same tiebreak
convention monthly.py's cumulative rank uses.

Also isolates a single waiver pickup's effect on its own team's odds by
re-running the same simulation with a counterfactual score history where
that pickup's starter-week contribution is swapped for replacement-level
production at their position (waivers.points_over_replacement).

Not modeled (v1 scope): divisions, reseeding, exact league tiebreaker rules,
cross-team score correlation, and anything past "made the playoffs" — no
bracket/championship simulation.
"""

from __future__ import annotations
import random

from . import stats as S

DEFAULT_N_SIMS = 3000
_MIN_GAMES_FOR_OWN_POOL = 3   # below this, a team's own history is too thin to bootstrap from
MIN_WEEKS_FOR_ODDS = 4        # below this, don't report odds at all (see odds_for_report)


def _team_actual_scores(season, roster_id: int, upto_week: int) -> dict:
    """week -> team's actual total points, for weeks 1..upto_week the team has data."""
    out = {}
    for wk in sorted(season.weeks.keys()):
        if wk > upto_week:
            break
        wt = season.weeks[wk].get(roster_id)
        if wt:
            out[wk] = wt.points
    return out


def _counterfactual_team_scores(season, roster_id: int, player_id: str, since_week: int,
                                upto_week: int, replacement_ppg: float) -> dict:
    """_team_actual_scores, but every week the player started, their
    starter-slot contribution is swapped for replacement_ppg — 'what would
    this team have scored with a replacement-level player in that lineup
    slot instead of this specific waiver pickup.' Bench weeks are left as
    they were: bench points never counted toward the team's actual score,
    so swapping them changes nothing either way."""
    scores = _team_actual_scores(season, roster_id, upto_week)
    for wk in range(since_week + 1, upto_week + 1):
        wt = season.weeks.get(wk, {}).get(roster_id)
        if not wt or player_id not in wt.starters:
            continue
        idx = wt.starters.index(player_id)
        actual_pts = wt.starter_points[idx] or 0.0
        scores[wk] = round(scores[wk] - actual_pts + replacement_ppg, 2)
    return scores


def _counterfactual_standings(season, roster_id: int, counterfactual_scores: dict, upto_week: int) -> dict:
    """stats.standings_through's (wins, losses, ties, pf, pa) computation,
    but replaying every already-played matchup with roster_id's counterfactual
    score instead of its real one. A pickup's whole realized effect lives in
    the past (it's already been rostered by upto_week) — its impact on
    ALREADY-DECIDED games, not just on the bootstrap pool for future ones, is
    what actually moves the standings this counterfactual needs to reflect."""
    acc = {rid: {"wins": 0, "losses": 0, "ties": 0, "pf": 0.0, "pa": 0.0} for rid in season.teams}
    for wk in sorted(season.weeks.keys()):
        if wk > upto_week:
            break
        wd = season.weeks[wk]
        for ra, pa, rb, pb in S.matchup_pairs(wd):
            if ra == roster_id and wk in counterfactual_scores:
                pa = counterfactual_scores[wk]
            if rb == roster_id and wk in counterfactual_scores:
                pb = counterfactual_scores[wk]
            acc[ra]["pf"] += pa; acc[ra]["pa"] += pb
            acc[rb]["pf"] += pb; acc[rb]["pa"] += pa
            if pa > pb:
                acc[ra]["wins"] += 1; acc[rb]["losses"] += 1
            elif pb > pa:
                acc[rb]["wins"] += 1; acc[ra]["losses"] += 1
            else:
                acc[ra]["ties"] += 1; acc[rb]["ties"] += 1
    return acc


def _score_pools(season, upto_week: int, overrides: dict | None = None) -> dict:
    """roster_id -> list of weekly scores to bootstrap-sample from. A team
    with fewer than _MIN_GAMES_FOR_OWN_POOL played weeks borrows the whole
    league's pool instead — too little history of its own for a meaningful
    bootstrap this early in the season."""
    raw = {rid: list(_team_actual_scores(season, rid, upto_week).values()) for rid in season.teams}
    if overrides:
        raw.update(overrides)
    league_pool = [v for weekly in raw.values() for v in weekly]
    return {rid: (weekly if len(weekly) >= _MIN_GAMES_FOR_OWN_POOL else league_pool)
            for rid, weekly in raw.items()}


def simulate_playoff_odds(season, upto_week: int, schedule: dict, playoff_teams: int,
                          n_sims: int = DEFAULT_N_SIMS, rng: "random.Random | None" = None,
                          score_pool_overrides: dict | None = None, base: dict | None = None) -> dict:
    """roster_id -> probability (0-1) of finishing in the top playoff_teams
    by (wins, points_for). `schedule` is {week: {roster_id: matchup_id}} for
    the remaining regular-season weeks (see data.remaining_schedule) — two
    rosters sharing a matchup_id in a week are that week's real matchup.
    Each remaining game is decided by bootstrap-sampling a score for each
    side from its own season-to-date weekly scores; each team's score is
    drawn independently (no cross-team correlation modeled). `base` is the
    (wins, losses, ties, pf, pa)-per-roster starting point for weeks
    1..upto_week — defaults to the real stats.standings_through, but
    pickup_playoff_impact passes a counterfactual one. If schedule has no
    weeks left past upto_week, the season's already decided — returns a
    deterministic 1.0/0.0 off the standings rather than simulating nothing
    n_sims times."""
    base = base if base is not None else S.standings_through(season, upto_week)
    remaining_weeks = sorted(wk for wk in schedule if wk > upto_week)

    if not remaining_weeks:
        ranked = sorted(season.teams.keys(),
                        key=lambda r: (base[r]["wins"], base[r]["pf"]), reverse=True)
        made_now = set(ranked[:playoff_teams])
        return {rid: (1.0 if rid in made_now else 0.0) for rid in season.teams}

    rng = rng or random.Random()
    pools = _score_pools(season, upto_week, overrides=score_pool_overrides)
    made = {rid: 0 for rid in season.teams}

    for _ in range(n_sims):
        wins = {rid: base[rid]["wins"] for rid in season.teams}
        pf = {rid: base[rid]["pf"] for rid in season.teams}
        for wk in remaining_weeks:
            pairs: dict = {}
            for rid, mid in schedule[wk].items():
                pairs.setdefault(mid, []).append(rid)
            for rid_pair in pairs.values():
                if len(rid_pair) != 2:
                    continue
                ra, rb = rid_pair
                sa = rng.choice(pools.get(ra) or [0.0])
                sb = rng.choice(pools.get(rb) or [0.0])
                pf[ra] += sa
                pf[rb] += sb
                if sa > sb:
                    wins[ra] += 1
                elif sb > sa:
                    wins[rb] += 1
        ranked = sorted(season.teams.keys(), key=lambda r: (wins[r], pf[r]), reverse=True)
        for rid in ranked[:playoff_teams]:
            made[rid] += 1

    return {rid: round(made[rid] / n_sims, 3) for rid in season.teams}


def pickup_playoff_impact(season, upto_week: int, schedule: dict, playoff_teams: int,
                          pickup, n_sims: int = DEFAULT_N_SIMS, seed: int | None = None) -> float:
    """Playoff-odds swing (percentage points) attributable to one waiver/FA
    pickup (a waivers.WaiverPOR row): real simulated odds for its roster
    minus the odds from the same simulation with a counterfactual score
    history where the pickup's starter-week contributions are replaced by
    replacement-level production at their position. Both runs share an RNG
    seed (common random numbers) so the reported delta reflects the swapped
    player, not simulation noise from independently-drawn games."""
    seed = seed if seed is not None else random.randrange(2**32)
    real = simulate_playoff_odds(season, upto_week, schedule, playoff_teams,
                                 n_sims=n_sims, rng=random.Random(seed))
    counterfactual_scores = _counterfactual_team_scores(
        season, pickup.roster_id, pickup.player_id, pickup.since_week, upto_week, pickup.replacement_ppg)
    counterfactual_base = _counterfactual_standings(season, pickup.roster_id, counterfactual_scores, upto_week)
    counterfactual = simulate_playoff_odds(
        season, upto_week, schedule, playoff_teams, n_sims=n_sims, rng=random.Random(seed),
        score_pool_overrides={pickup.roster_id: list(counterfactual_scores.values())},
        base=counterfactual_base)
    return round((real[pickup.roster_id] - counterfactual[pickup.roster_id]) * 100, 1)


def odds_for_report(season, upto_week: int, weeks: list):
    """(playoff_odds, pickup_odds_swing) for a report covering `weeks` and
    cut off at `upto_week`.

    Both cli.py and batch.py need exactly this pair, and both the weekly and
    monthly reports now want it — one helper rather than four copies of the
    same five lines drifting apart.

    Returns ({}, None) once the regular season is over: there is no remaining
    schedule to simulate, so every team's odds are already decided and the
    table would just print 100s and 0s.

    Also returns ({}, None) before MIN_WEEKS_FOR_ODDS. Every team is below
    _MIN_GAMES_FOR_OWN_POOL that early, so they all bootstrap from the same
    league-wide pool and the "odds" mostly restate who happens to be 1-0 —
    a precise-looking number with nothing behind it.
    """
    from . import data as D
    from . import waivers as W

    last_regular = season.playoff_week_start - 1
    if upto_week >= last_regular:
        return {}, None
    from .render import _FORCE_APPENDIX
    if (not _FORCE_APPENDIX
            and len([w for w in season.weeks if w <= upto_week]) < MIN_WEEKS_FOR_ODDS):
        return {}, None

    schedule = D.remaining_schedule(season, upto_week, last_regular)
    odds = simulate_playoff_odds(season, upto_week, schedule, season.playoff_teams)
    swing = None
    if weeks:
        por = W.best_por_period(season, weeks)
        if por:
            swing = pickup_playoff_impact(season, upto_week, schedule,
                                          season.playoff_teams, por)
    return odds, swing
