"""
data.py — Sleeper API access + caching -> SeasonData.

Sleeper's API is public, read-only, and unauthenticated. We pull league info,
rosters, users, weekly matchups, and (optionally) transactions, and flatten
them into the SeasonData shape every other module consumes.

The full /players/nfl dump is large and changes rarely, so it's cached to
disk for a day.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import json
import os
import time

import requests

API = "https://api.sleeper.app/v1"
_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
_PLAYERS_CACHE = os.path.join(_CACHE_DIR, "players_nfl.json")
_PLAYERS_TTL = 24 * 3600
_LAST_REGULAR_SEASON_WEEK = 18


@dataclass
class WeekTeam:
    roster_id: int
    matchup_id: int | None
    points: float
    starters: list
    starter_points: list
    bench: list
    bench_points: list


@dataclass
class Team:
    roster_id: int
    owner_id: str | None
    team_name: str
    manager: str
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float
    avatar: str | None = None            # Sleeper avatar ID (hash), or a full URL in rare cases
    avatar_full_url: str | None = None   # set when the user uploaded a custom avatar
    waiver_budget_used: int = 0          # cumulative FAAB spent this season (from roster settings)


@dataclass
class Transaction:
    week: int
    roster_id: int
    type: str                          # "waiver" | "free_agent"
    faab: int
    player_id: str
    drops: list = field(default_factory=list)   # player_ids dropped in the same claim


@dataclass
class Trade:
    week: int
    roster_ids: list
    adds: dict          # player_id -> roster_id that received the player
    drops: dict         # player_id -> roster_id that gave up the player
    draft_picks: int = 0
    draft_picks_raw: list = field(default_factory=list)   # full pick dicts from Sleeper


@dataclass
class SeasonData:
    league_id: str
    name: str
    season: str
    roster_positions: list
    teams: dict                # roster_id -> Team
    weeks: dict                # week -> {roster_id: WeekTeam}
    players: dict               # player_id -> player dict
    transactions: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    draft_picks: dict = field(default_factory=dict)   # player_id -> {"pick_no", "round", "roster_id", "is_keeper"}
    faab_budget: int = 100                            # league-wide starting FAAB cap

    def team_name(self, roster_id):
        t = self.teams.get(roster_id)
        return t.team_name if t else f"Team {roster_id}"


def player_name(players: dict, pid) -> str:
    if pid is None:
        return "Empty"
    p = players.get(str(pid))
    if not p:
        return str(pid)
    name = p.get("full_name")
    if not name:
        name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    return name or str(pid)


def _get(url: str):
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    return resp.json()


def validate_league_id(league_id: str) -> bool:
    """Cheap existence check (just /league/{id} — no rosters/weeks/transactions).
    False for a made-up, malformed, or no-longer-existing league ID; re-raises
    on anything that isn't a definitive 404 (network errors, etc.) since those
    aren't evidence the ID is bad."""
    league_id = (league_id or "").strip()
    if not league_id.isdigit():
        return False
    try:
        league = _get(f"{API}/league/{league_id}")
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return False
        raise
    return bool(league) and str(league.get("league_id")) == league_id


def _load_players() -> dict:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    if os.path.exists(_PLAYERS_CACHE) and time.time() - os.path.getmtime(_PLAYERS_CACHE) < _PLAYERS_TTL:
        with open(_PLAYERS_CACHE, encoding="utf-8") as f:
            return json.load(f)
    players = _get(f"{API}/players/nfl") or {}
    try:
        with open(_PLAYERS_CACHE, "w", encoding="utf-8") as f:
            json.dump(players, f)
    except OSError:
        pass
    return players


def _money(whole, decimal) -> float:
    return round((whole or 0) + (decimal or 0) / 100, 2)


def _build_teams(rosters: list, users: list) -> dict:
    users_by_id = {u["user_id"]: u for u in users}
    teams = {}
    for r in rosters:
        rid = r["roster_id"]
        owner_id = r.get("owner_id")
        user = users_by_id.get(owner_id, {})
        team_name = (user.get("metadata") or {}).get("team_name") or user.get("display_name") or f"Team {rid}"
        settings = r.get("settings") or {}
        teams[rid] = Team(
            roster_id=rid,
            owner_id=owner_id,
            team_name=team_name,
            manager=user.get("display_name") or "Unknown",
            wins=settings.get("wins", 0),
            losses=settings.get("losses", 0),
            ties=settings.get("ties", 0),
            points_for=_money(settings.get("fpts"), settings.get("fpts_decimal")),
            points_against=_money(settings.get("fpts_against"), settings.get("fpts_against_decimal")),
            avatar=user.get("avatar"),
            avatar_full_url=(user.get("metadata") or {}).get("avatar"),
            waiver_budget_used=settings.get("waiver_budget_used", 0),
        )
    return teams


def _fetch_week(league_id: str, week: int) -> dict:
    matchups = _get(f"{API}/league/{league_id}/matchups/{week}")
    if not matchups:
        return {}
    wd = {}
    for m in matchups:
        rid = m["roster_id"]
        starters = m.get("starters") or []
        players_points = m.get("players_points") or {}
        starter_points = [players_points.get(pid, 0.0) or 0.0 for pid in starters]
        all_players = m.get("players") or []
        bench = [p for p in all_players if p not in starters]
        bench_points = [players_points.get(pid, 0.0) or 0.0 for pid in bench]
        points = m.get("points")
        if points is None:
            points = round(sum(starter_points), 2)
        wd[rid] = WeekTeam(
            roster_id=rid,
            matchup_id=m.get("matchup_id"),
            points=points,
            starters=starters,
            starter_points=starter_points,
            bench=bench,
            bench_points=bench_points,
        )
    return wd


def _fetch_week_transactions(league_id: str, week: int):
    """Returns (waiver/free-agent Transactions, Trades) for the week."""
    txns = _get(f"{API}/league/{league_id}/transactions/{week}") or []
    waivers, trades = [], []
    for t in txns:
        if t.get("status") != "complete":
            continue
        ttype = t.get("type")
        if ttype in ("waiver", "free_agent"):
            adds = t.get("adds") or {}
            drops_map = t.get("drops") or {}
            faab = (t.get("settings") or {}).get("waiver_bid", 0) or 0
            for pid, rid in adds.items():
                # drops_for_this: players this roster dropped in the same claim
                drops_for_this = [dpid for dpid, drid in drops_map.items() if drid == rid]
                waivers.append(Transaction(
                    week=week, roster_id=rid, type=ttype, faab=faab,
                    player_id=pid, drops=drops_for_this,
                ))
        elif ttype == "trade":
            raw_picks = t.get("draft_picks") or []
            trades.append(Trade(
                week=week,
                roster_ids=t.get("roster_ids") or [],
                adds=t.get("adds") or {},
                drops=t.get("drops") or {},
                draft_picks=len(raw_picks),
                draft_picks_raw=raw_picks,
            ))
    return waivers, trades


def _fetch_draft_picks(draft_id: str) -> dict:
    """player_id -> draft slot info for this league's draft. Best-effort:
    leagues with no recorded draft (or a draft_id Sleeper hasn't populated
    picks for yet) just get an empty dict, not an error."""
    picks = _get(f"{API}/draft/{draft_id}/picks") or []
    out = {}
    for p in picks:
        pid = p.get("player_id")
        if not pid:
            continue
        out[pid] = {
            "pick_no": p.get("pick_no"),
            "round": p.get("round"),
            "roster_id": p.get("roster_id"),
            "is_keeper": p.get("is_keeper"),
        }
    return out


def fetch_season(league_id: str, fetch_transactions: bool = True) -> SeasonData:
    league = _get(f"{API}/league/{league_id}")
    rosters = _get(f"{API}/league/{league_id}/rosters") or []
    users = _get(f"{API}/league/{league_id}/users") or []
    players = _load_players()
    teams = _build_teams(rosters, users)

    weeks = {}
    for wk in range(1, _LAST_REGULAR_SEASON_WEEK + 1):
        wd = _fetch_week(league_id, wk)
        if wd:
            weeks[wk] = wd

    transactions, trades = [], []
    if fetch_transactions:
        for wk in weeks:
            w, t = _fetch_week_transactions(league_id, wk)
            transactions.extend(w)
            trades.extend(t)

    draft_id = league.get("draft_id")
    try:
        draft_picks = _fetch_draft_picks(draft_id) if draft_id else {}
    except requests.RequestException:
        draft_picks = {}

    faab_budget = int((league.get("settings") or {}).get("waiver_budget", 100) or 100)

    return SeasonData(
        league_id=league_id,
        name=league.get("name") or "League",
        season=str(league.get("season") or ""),
        roster_positions=league.get("roster_positions") or [],
        teams=teams,
        weeks=weeks,
        players=players,
        transactions=transactions,
        trades=trades,
        draft_picks=draft_picks,
        faab_budget=faab_budget,
    )
