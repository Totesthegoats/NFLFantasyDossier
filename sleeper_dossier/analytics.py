"""
analytics.py — the monthly dossier's data-science layer.

Everything here answers a question the existing tables can't. stats.py measures
what happened (points, records, all-play); this module estimates what it *means*
— how much of a record is talent, how much of an average is signal, and how good
the opposition actually was.

Five pieces, each independently useful and each deliberately pure-stdlib
(numpy arrives only as a pandas dependency, and pandas is optional in this
package — see decision.py's guarded import — so nothing load-bearing may
assume it):

  massey_ratings      opponent-adjusted points-per-game rating (ridge least squares)
  elo_trajectory      week-by-week Elo with a margin-of-victory multiplier
  shrunk_scoring      empirical-Bayes regression of each team's PPG to the mean
  form_signal         is week-to-week form predictive in this league, or noise?
  luck_decomposition  a record split into talent, schedule, and lineup calls

Every function takes `upto_week` and never reads `season.teams`' live win/loss
totals, so a retrospective monthly report can't leak results from weeks its
readers hadn't seen yet. The ones that are genuinely month-scoped rather than
season-to-date also take `weeks`.
"""

from __future__ import annotations
import math
import statistics

from . import stats as S
from . import manager as MG


# ──────────────────────────────────────────────────────────────────────────
# Opponent-adjusted rating (Massey least squares)
# ──────────────────────────────────────────────────────────────────────────
#
# In fantasy football your *score* is independent of your opponent — there is
# no defense to adjust for. Your *margin* is not: it is your score minus
# whatever they happened to put up. So the thing worth adjusting for opponent
# strength is margin, which is exactly what a Massey rating does.
#
# Massey solves, in the least-squares sense, the overdetermined system
#     r_i - r_j = margin_ij
# over every game played. The normal equations are (D - A) r = m, where D is
# diag(games), A is the games-played adjacency, and m each team's total margin.
# (D - A) is singular (ratings are only identified up to a constant), so a
# ridge term λ both regularizes it and pins the solution — with λ > 0 a team
# with a thin schedule is pulled toward 0 (league average) rather than being
# handed an extreme rating off two games.
#
# Solved by Gauss-Seidel rather than a matrix inverse: a 12x12 system converges
# in well under a hundred sweeps, and it keeps this module dependency-free.

MASSEY_RIDGE = 1.0
_MASSEY_MAX_SWEEPS = 500
_MASSEY_TOLERANCE = 1e-7


def massey_ratings(season, upto_week: int | None = None,
                   ridge: float = MASSEY_RIDGE) -> dict:
    """roster_id -> {rating, sos, raw_margin, games, avg_opp_points}.

    `rating` is in points per game, centred so the league averages 0: +8 means
    "eight points a game better than the average team in this league, after
    adjusting for who they played".

    The three columns are readable together because they satisfy the Massey
    identity, raw_margin = rating - sos:
      raw_margin  average scoreboard margin (what happened)
      sos         average rating of the opponents faced (who they played)
      rating      what's left once the schedule is accounted for
    So a team with a +2 margin against +6 opposition rates above a team with
    the same margin against -6 opposition. The ridge term makes the identity
    approximate rather than exact — it shrinks every rating slightly toward
    zero — which is the intended trade and worth stating wherever it's shown.
    """
    weeks = [w for w in sorted(season.weeks) if upto_week is None or w <= upto_week]

    games: dict = {}
    margin_sum: dict = {}
    opponents: dict = {}          # rid -> {opponent_rid: times played}
    opp_points: dict = {}

    for wk in weeks:
        for ra, pa, rb, pb in S.matchup_pairs(season.weeks[wk]):
            for me, mine, them, theirs in ((ra, pa, rb, pb), (rb, pb, ra, pa)):
                games[me] = games.get(me, 0) + 1
                margin_sum[me] = margin_sum.get(me, 0.0) + (mine - theirs)
                opponents.setdefault(me, {})
                opponents[me][them] = opponents[me].get(them, 0) + 1
                opp_points.setdefault(me, []).append(theirs)

    played = [rid for rid, n in games.items() if n]
    if not played:
        return {}

    ratings = {rid: 0.0 for rid in played}
    for _ in range(_MASSEY_MAX_SWEEPS):
        shift = 0.0
        for rid in played:
            neighbour_sum = sum(n * ratings.get(o, 0.0)
                                for o, n in opponents[rid].items())
            updated = (margin_sum[rid] + neighbour_sum) / (games[rid] + ridge)
            shift = max(shift, abs(updated - ratings[rid]))
            ratings[rid] = updated
        if shift < _MASSEY_TOLERANCE:
            break

    # Centre on the league so a rating reads as "vs an average team here".
    centre = statistics.mean(ratings.values())
    ratings = {rid: v - centre for rid, v in ratings.items()}

    out = {}
    for rid in played:
        n = games[rid]
        sos = sum(cnt * ratings.get(o, 0.0)
                  for o, cnt in opponents[rid].items()) / n
        out[rid] = {
            "rating": round(ratings[rid], 2),
            "sos": round(sos, 2),
            "raw_margin": round(margin_sum[rid] / n, 2),
            "games": n,
            "avg_opp_points": round(statistics.mean(opp_points[rid]), 1),
        }
    return out


# ──────────────────────────────────────────────────────────────────────────
# Elo
# ──────────────────────────────────────────────────────────────────────────
#
# Massey is a season-long snapshot; Elo is a path. It answers a different
# question — not "how good are they" but "when did that change" — which is
# what makes it the right monthly chart: four weeks of Elo movement is the
# month's story as a single line per team.
#
# The margin-of-victory multiplier is the FiveThirtyEight NFL form: the log of
# the margin (so a 40-point win counts for more than a 4-point one, but not ten
# times more) damped by the pre-game rating gap (so a favourite thrashing an
# underdog moves less than an upset of the same margin). Without it, a
# one-point escape and a blowout are identical to the model, which throws away
# most of what a fantasy scoreboard is telling you.

ELO_START = 1500.0
ELO_K = 24.0
_ELO_SCALE = 400.0


def _elo_expected(rating: float, opp_rating: float) -> float:
    return 1.0 / (1.0 + 10 ** ((opp_rating - rating) / _ELO_SCALE))


def _elo_mov_multiplier(margin: float, winner_edge: float) -> float:
    """FiveThirtyEight's margin multiplier. `winner_edge` is the winner's
    pre-game rating minus the loser's — positive when the favourite won."""
    return math.log(abs(margin) + 1.0) * (2.2 / (winner_edge * 0.001 + 2.2))


def elo_trajectory(season, upto_week: int | None = None,
                   k: float = ELO_K, start: float = ELO_START) -> dict:
    """roster_id -> {series, before, start, end, peak, trough, delta}.

    `series` is [(week, rating_after_that_week), ...] and `before` is
    {week: rating_entering_that_week} — the second is what a month-scoped
    report needs, since "where did they stand walking into October" is a
    rating that no week's result has been applied to yet.

    Ties nudge both sides toward each other by construction (actual = 0.5), so
    they're handled by the same update rather than skipped.
    """
    weeks = [w for w in sorted(season.weeks) if upto_week is None or w <= upto_week]
    ratings = {rid: start for rid in season.teams}
    series: dict = {rid: [] for rid in season.teams}
    before: dict = {rid: {} for rid in season.teams}

    for wk in weeks:
        pairs = S.matchup_pairs(season.weeks[wk])
        for rid in ratings:
            before.setdefault(rid, {})[wk] = round(ratings[rid], 1)
        for ra, pa, rb, pb in pairs:
            ea = _elo_expected(ratings.get(ra, start), ratings.get(rb, start))
            if pa > pb:
                actual_a, winner_edge = 1.0, ratings.get(ra, start) - ratings.get(rb, start)
            elif pb > pa:
                actual_a, winner_edge = 0.0, ratings.get(rb, start) - ratings.get(ra, start)
            else:
                actual_a, winner_edge = 0.5, 0.0
            mult = (_elo_mov_multiplier(pa - pb, winner_edge)
                    if actual_a != 0.5 else 1.0)
            shift = k * mult * (actual_a - ea)
            ratings[ra] = ratings.get(ra, start) + shift
            ratings[rb] = ratings.get(rb, start) - shift
        # Only teams with a result this week get a point on the line; a team
        # Sleeper recorded no matchup for would otherwise draw a flat segment
        # that looks like a deliberate hold rather than missing data.
        scored = {rid for ra, _pa, rb, _pb in pairs for rid in (ra, rb)}
        for rid in scored:
            series.setdefault(rid, []).append((wk, round(ratings[rid], 1)))

    out = {}
    for rid, pts in series.items():
        if not pts:
            continue
        values = [v for _w, v in pts]
        out[rid] = {
            "series": pts,
            "before": before.get(rid, {}),
            "start": start,
            "end": values[-1],
            "peak": max(values),
            "trough": min(values),
            "delta": round(values[-1] - start, 1),
        }
    return out


def elo_month_change(traj: dict, weeks: list) -> dict:
    """roster_id -> {entering, leaving, change} across `weeks` only.

    The season-long Elo tells you who is good; this tells you whose month it
    was, which is the monthly report's actual question. Elo is well suited to
    it because the scale is the same in every window — 30 points of movement
    means the same thing in October as in December.
    """
    if not traj or not weeks:
        return {}
    first, last = min(weeks), max(weeks)
    out = {}
    for rid, t in traj.items():
        entering = t["before"].get(first)
        after = [v for w, v in t["series"] if w <= last]
        if entering is None or not after:
            continue
        out[rid] = {
            "entering": entering,
            "leaving": after[-1],
            "change": round(after[-1] - entering, 1),
        }
    return out


# ──────────────────────────────────────────────────────────────────────────
# Empirical-Bayes shrinkage
# ──────────────────────────────────────────────────────────────────────────
#
# A team averaging 128 a week through four weeks is not a 128-point team. Some
# of that 128 is how good they are and some is which four weeks they got, and
# with a sample this small the second part is large. Shrinkage estimates the
# split from the data itself rather than by assumption.
#
# The model is the standard two-level one: team means are drawn from a league
# distribution with variance tau^2, and each observed week is that team's true
# level plus noise of variance sigma^2. Then the best estimate of a team's true
# level is a weighted average of their own mean and the league mean, weighted
# by how much information each carries:
#
#     B_i = (sigma^2 / n_i) / (sigma^2 / n_i + tau^2)
#     shrunk_i = B_i * mu + (1 - B_i) * mean_i
#
# B is the fraction of the way back to the league mean. It falls as n grows
# (more of your own evidence) and as tau^2 grows (the league is genuinely
# spread out, so extreme means are more believable). This is the James-Stein
# estimator, and the point of it is that it beats the raw means at predicting
# next month — which is what makes it worth printing next to them.

_MIN_WEEKS_SHRINKAGE = 2


def shrunk_scoring(season, upto_week: int | None = None) -> dict:
    """{"teams": {rid: {...}}, "league": {...}} — regressed scoring estimates.

    Per team: `ppg` (raw), `shrunk` (regressed), `shrinkage` (B, as a %),
    `ci_low`/`ci_high` (95% credible interval on the true level), `delta`
    (shrunk - raw: how much the estimate disagrees with the average), and
    `rank`/`shrunk_rank` so a table can show who the regression moves.

    League-level: `mu` (grand mean), `sigma` (within-team weekly SD — the
    week-to-week noise), `tau` (between-team SD — the real spread of talent),
    and `reliability` = tau^2 / (tau^2 + sigma^2), the share of a single
    week's variance that is signal rather than noise.

    Returns {} below two weeks per team: with one observation there is no
    within-team variance to estimate sigma from.
    """
    per_team: dict = {}
    for wk in sorted(season.weeks):
        if upto_week is not None and wk > upto_week:
            break
        for rid, wt in season.weeks[wk].items():
            per_team.setdefault(rid, []).append(wt.points)

    usable = {rid: v for rid, v in per_team.items() if len(v) >= _MIN_WEEKS_SHRINKAGE}
    if len(usable) < 2:
        return {}

    means = {rid: statistics.mean(v) for rid, v in usable.items()}
    mu = statistics.mean(means.values())

    # Pooled within-team variance: the weekly noise, estimated from how much
    # each team bounces around its OWN mean rather than around the league's.
    ss, df = 0.0, 0
    for rid, v in usable.items():
        m = means[rid]
        ss += sum((x - m) ** 2 for x in v)
        df += len(v) - 1
    sigma_sq = ss / df if df else 0.0

    # Between-team variance, by method of moments: the observed spread of team
    # means is inflated by sampling noise, so subtract the expected inflation.
    # Clamped at 0 — a negative estimate means "this league's spread is
    # indistinguishable from noise", which is 0 talent variance, not negative.
    n_bar = statistics.mean(len(v) for v in usable.values())
    observed_var = statistics.variance(means.values()) if len(means) > 1 else 0.0
    tau_sq = max(0.0, observed_var - sigma_sq / n_bar)

    teams = {}
    for rid, v in usable.items():
        n = len(v)
        se_sq = sigma_sq / n
        b = se_sq / (se_sq + tau_sq) if (se_sq + tau_sq) else 1.0
        shrunk = b * mu + (1 - b) * means[rid]
        post_sd = math.sqrt((1 - b) * se_sq) if se_sq else 0.0
        teams[rid] = {
            "ppg": round(means[rid], 1),
            "shrunk": round(shrunk, 1),
            "shrinkage": round(b * 100, 1),
            "sd": round(post_sd, 1),
            "ci_low": round(shrunk - 1.96 * post_sd, 1),
            "ci_high": round(shrunk + 1.96 * post_sd, 1),
            "delta": round(shrunk - means[rid], 1),
            "weeks": n,
        }

    for key, target in (("ppg", "rank"), ("shrunk", "shrunk_rank")):
        order = sorted(teams, key=lambda r: teams[r][key], reverse=True)
        for i, rid in enumerate(order, 1):
            teams[rid][target] = i
    for rid, t in teams.items():
        t["rank_move"] = t["rank"] - t["shrunk_rank"]

    sigma = math.sqrt(sigma_sq)
    tau = math.sqrt(tau_sq)
    return {
        "teams": teams,
        "league": {
            "mu": round(mu, 1),
            "sigma": round(sigma, 1),
            "tau": round(tau, 1),
            "reliability": round(tau_sq / (tau_sq + sigma_sq) * 100, 1) if (tau_sq + sigma_sq) else 0.0,
            "weeks": round(n_bar, 1),
        },
    }


# ──────────────────────────────────────────────────────────────────────────
# Is form real?
# ──────────────────────────────────────────────────────────────────────────
#
# Every fantasy league talks about who is hot. This tests it. Two independent
# measures, because they can disagree and the disagreement is informative:
#
#   lag-1 autocorrelation  does a good week predict a good NEXT week?
#   split-half reliability do odd weeks and even weeks agree about who is good?
#
# Autocorrelation is about momentum (ordering matters); split-half is about
# talent (ordering doesn't). A league can have real talent spread and zero
# momentum — which is the usual finding, and the useful one to print next to a
# power ranking that weights recent form at 25%.
#
# Both are computed on within-team-centred residuals, so a team that is simply
# good every week doesn't masquerade as a team on a heater.

_MIN_PAIRS_AUTOCORR = 8


def form_signal(season, upto_week: int | None = None) -> dict:
    """{"autocorr", "pairs", "threshold", "momentum", "split_half",
        "split_half_n", "reliability_verdict"} or {} when too thin.

    `threshold` is the usual 2/sqrt(n) rule of thumb for an autocorrelation
    being distinguishable from zero. `momentum` is the plain-English verdict
    the report prints; the number is the evidence for it.
    """
    per_team: dict = {}
    for wk in sorted(season.weeks):
        if upto_week is not None and wk > upto_week:
            break
        for rid, wt in season.weeks[wk].items():
            per_team.setdefault(rid, []).append(wt.points)

    series = {rid: v for rid, v in per_team.items() if len(v) >= 3}
    if not series:
        return {}

    num = den = 0.0
    pairs = 0
    for v in series.values():
        m = statistics.mean(v)
        resid = [x - m for x in v]
        num += sum(resid[i] * resid[i + 1] for i in range(len(resid) - 1))
        den += sum(x * x for x in resid)
        pairs += len(resid) - 1
    autocorr = (num / den) if den else 0.0

    threshold = 2 / math.sqrt(pairs) if pairs else 1.0
    if pairs < _MIN_PAIRS_AUTOCORR:
        momentum = "not enough games yet to tell"
    elif autocorr > threshold:
        momentum = "form carries over — hot teams stay hot"
    elif autocorr < -threshold:
        momentum = "scores bounce back — a big week tends to be followed by a small one"
    else:
        momentum = "no detectable momentum — last week says nothing about next week"

    # Split-half: odd weeks vs even weeks, per team, correlated across teams.
    halves = []
    for rid, v in series.items():
        odd = v[0::2]
        even = v[1::2]
        if odd and even:
            halves.append((statistics.mean(odd), statistics.mean(even)))
    split_half = None
    verdict = "not enough games yet to tell"
    if len(halves) >= 4:
        xs = [a for a, _b in halves]
        ys = [b for _a, b in halves]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        cov = sum((a - mx) * (b - my) for a, b in halves)
        vx = sum((a - mx) ** 2 for a in xs)
        vy = sum((b - my) ** 2 for b in ys)
        if vx > 0 and vy > 0:
            split_half = cov / math.sqrt(vx * vy)
            if split_half > 0.5:
                verdict = "scoring is mostly skill — the same teams score well in both halves"
            elif split_half > 0.2:
                verdict = "scoring is part skill, part luck"
            else:
                verdict = "scoring looks close to random — this league is a coin-flip league"

    return {
        "autocorr": round(autocorr, 3),
        "pairs": pairs,
        "threshold": round(threshold, 3),
        "momentum": momentum,
        "split_half": round(split_half, 3) if split_half is not None else None,
        "split_half_n": len(halves),
        "reliability_verdict": verdict,
    }


# ──────────────────────────────────────────────────────────────────────────
# Where the wins came from
# ──────────────────────────────────────────────────────────────────────────
#
# Three separate measures in this package already each explain part of a
# record, and a reader has no way to weigh them against each other:
#   all-play         what your scoring deserved, schedule-blind
#   schedule swap    what your fixtures were worth
#   optimal record   what your start/sit calls were worth
#
# Putting all three on one row, in the same unit (wins), turns three arguments
# into one accounting. The components are estimated independently and are not
# guaranteed to add up, so the remainder is reported rather than hidden — an
# honest `unexplained` column is worth more than three columns silently
# rescaled to force a total.

def luck_decomposition(season, weeks: list | None = None,
                       upto_week: int | None = None) -> dict:
    """roster_id -> {actual, talent, schedule, lineup, unexplained, games,
    avg_opp_points}.

    All five numbers are in wins.
      talent       all-play win rate x games — what the scoring earned
      schedule     wins under their own slate minus the average across all
                   slates (positive = the fixtures flattered them)
      lineup       actual wins minus wins-if-both-sides-were-perfect
                   (positive = their start/sit calls beat the field's)
      unexplained  actual - (talent + schedule + lineup)

    Pass `weeks` for a month-scoped decomposition (the monthly report's
    version) or `upto_week` alone for season-to-date. When both are given,
    `weeks` scopes the record and `upto_week` caps everything else.
    """
    scope = sorted(weeks) if weeks else [w for w in sorted(season.weeks)
                                         if upto_week is None or w <= upto_week]
    scope = [w for w in scope if w in season.weeks]
    if not scope:
        return {}
    cap = upto_week if upto_week is not None else max(scope)

    actual: dict = {}
    games: dict = {}
    ap_w: dict = {}
    ap_l: dict = {}
    opp_points: dict = {}
    opt_wins: dict = {}

    for wk in scope:
        wd = season.weeks[wk]
        ap = S.all_play(wd)
        eff = S.lineup_efficiency(season, wd)
        for rid, (w, l, _t) in ap.items():
            ap_w[rid] = ap_w.get(rid, 0) + w
            ap_l[rid] = ap_l.get(rid, 0) + l
        for ra, pa, rb, pb in S.matchup_pairs(wd):
            for me, mine, them, theirs in ((ra, pa, rb, pb), (rb, pb, ra, pa)):
                games[me] = games.get(me, 0) + 1
                opp_points.setdefault(me, []).append(theirs)
                actual.setdefault(me, 0.0)
                if mine > theirs:
                    actual[me] += 1.0
                elif mine == theirs:
                    actual[me] += 0.5
            oa = eff[ra].optimal if ra in eff else pa
            ob = eff[rb].optimal if rb in eff else pb
            opt_wins.setdefault(ra, 0.0)
            opt_wins.setdefault(rb, 0.0)
            if oa > ob:
                opt_wins[ra] += 1.0
            elif ob > oa:
                opt_wins[rb] += 1.0
            else:
                opt_wins[ra] += 0.5
                opt_wins[rb] += 0.5

    swap = MG.schedule_swap_matrix(season, upto_week=cap, weeks=scope)

    out = {}
    for rid, n in games.items():
        if not n:
            continue
        ap_games = ap_w.get(rid, 0) + ap_l.get(rid, 0)
        rate = ap_w.get(rid, 0) / ap_games if ap_games else 0.5
        talent = rate * n

        row = swap.get(rid) or {}
        wins_by_slate = [v["wins"] + 0.5 * v["ties"] for v in row.values()]
        schedule = ((row.get(rid, {}).get("wins", 0)
                     + 0.5 * row.get(rid, {}).get("ties", 0))
                    - statistics.mean(wins_by_slate)) if wins_by_slate else 0.0

        lineup = actual.get(rid, 0.0) - opt_wins.get(rid, 0.0)

        a = actual.get(rid, 0.0)
        out[rid] = {
            "actual": round(a, 1),
            "talent": round(talent, 2),
            "schedule": round(schedule, 2),
            "lineup": round(lineup, 2),
            "unexplained": round(a - talent - schedule - lineup, 2),
            "games": n,
            "avg_opp_points": round(statistics.mean(opp_points[rid]), 1) if opp_points.get(rid) else 0.0,
        }
    return out
