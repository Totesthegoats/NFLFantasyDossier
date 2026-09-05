# Sleeper Dossier

**Weekly, monthly,** and end-of-season fantasy reports for **Sleeper** NFL leagues — a
done-for-you "dossier" of named awards (Hall of Fame / Hall of Shame), standings, an
all-play luck leaderboard, and optimal-lineup efficiency, with optional AI-written roast
commentary.

The data engine exists for free on GitHub; the moat here is the **entertainment layer** —
the named awards, the roast voice, and the head-to-head/efficiency stats that FPL tools
structurally cannot produce.

## Install

```bash
pip install requests anthropic      # anthropic only needed for the roast layer
```

No Sleeper API key is required — Sleeper's API is public and read-only.

## Quick start

```bash
# Monthly report (latest completed calendar month) to the terminal
python -m sleeper_dossier.cli --league YOUR_LEAGUE_ID

# A specific month, written as a shareable HTML page
python -m sleeper_dossier.cli --league YOUR_LEAGUE_ID --month 2025-10 --html oct.html

# A single week's recap — that week's awards, standings-to-date, and luck leaderboard
python -m sleeper_dossier.cli --league YOUR_LEAGUE_ID --week 5 --html week5.html

# End-of-season review
python -m sleeper_dossier.cli --league YOUR_LEAGUE_ID --season --html review.html

# Generate a PDF (requires: pip install playwright && playwright install chromium)
python -m sleeper_dossier.cli --league YOUR_LEAGUE_ID --week 5 --pdf week5.pdf

# Manager-of-the-Month history so far
python -m sleeper_dossier.cli --league YOUR_LEAGUE_ID --history
```

Months are **real calendar months** (Sept, Oct, ...). Each NFL week is mapped to
the calendar month its Sunday falls in (see `calendar_map.py`; update
`KNOWN_WEEK1_THURSDAY` each new season).

Your league ID is the number in the league URL: `sleeper.com/leagues/THIS_NUMBER/...`

### Enable the roast layer
Set an API key; without it the report still generates with plain stat lines.
```bash
env ANTHROPIC_API_KEY=sk-ant-...
python -m sleeper_dossier.cli --league YOUR_LEAGUE_ID --week 5 --html week5.html
```

## Batch mode (the season-pass engine)

Serve every paying league in one run. Marginal cost per extra league is ~zero — this is
where the season-pass model's economics work.

`leagues.csv`:
```csv
league_id,league_label,email
123456789,Sunday Lads,commish@example.com
987654321,Office League,boss@example.com
```

```bash
# Latest completed month for every league
python -m sleeper_dossier.batch --csv leagues.csv --outdir reports

# A specific month, emailed (sends real mail — see SMTP env vars below)
python -m sleeper_dossier.batch --csv leagues.csv --month 2025-10 --email

# A specific week's recap for every league
python -m sleeper_dossier.batch --csv leagues.csv --week 5

# End-of-season reviews for everyone
python -m sleeper_dossier.batch --csv leagues.csv --season

# Also write a paginated PDF (Decision Lab charts included) alongside each HTML
python -m sleeper_dossier.batch --csv leagues.csv --week 5 --pdf
```

Email requires: `SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM`.

### Sourcing the league list from a Google Sheet

`--sheet SHEET_ID` reads the same list from a Google Sheet instead of a CSV
(`sheet.py`), with columns `Date, email, league_id, Teir`:

```bash
python -m sleeper_dossier.batch --sheet 1QZ-vewj...Y2k --week latest --email
```

Setup (one-time):
1. Google Cloud Console → create a project (if you don't have one) → **IAM & Admin
   → Service Accounts** → create a service account → **Keys** → Add key → JSON.
   Download the key file.
2. Open the sheet's Share dialog and add the service account's email
   (`...@...iam.gserviceaccount.com`, from the JSON key) as **Viewer**.
3. Set `GOOGLE_SHEETS_CREDENTIALS=/path/to/key.json` before running.

`--week latest` resolves to each league's most recently completed week — this
is what the scheduled automation uses (see below).

Every row's `league_id` is checked against Sleeper (`data.validate_league_id`)
before it's used — a typo'd, made-up, or no-longer-existing ID is skipped with
a clear `not a valid Sleeper league ID` message instead of a raw HTTP error
buried in a stack trace, and counted separately in the run summary. Bad
`email` values aren't checked — a row with a working league_id and a broken
email still generates its report, it just won't get sent.

### Free trial (`Teir` column)

A row with `Teir` = `free` gets the **full-tier report** for its first 14
days after `Date` (any of `YYYY-MM-DD`, `DD/MM/YYYY`, `MM/DD/YYYY`), then
drops to the free-tier report (award cards only). `normal`/`dynasty` rows are
unaffected — the trial only ever upgrades a declared tier, never downgrades
it. See `trial.py`.

The tier also controls `render_html`'s `tier=` argument directly if you're
calling it outside batch mode: `"free"` renders award cards + recap only;
`"normal"`/`"dynasty"` render the full report (standings, charts, luck
leaderboard, waiver/trades). Dynasty-specific features don't exist yet, so
`"dynasty"` currently renders identically to `"normal"`.

### Running it on a schedule

`.github/workflows/dossier.yml` runs the batch weekly (Tuesdays, after MNF)
via GitHub Actions. It needs these repo secrets (Settings → Secrets and
variables → Actions):

- `SHEET_ID` — the Google Sheet ID (the long string in its URL)
- `GOOGLE_SHEETS_CREDENTIALS_JSON` — the full contents of the service account JSON key
- `ANTHROPIC_API_KEY` — omit to send reports without the roast layer
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`

Trigger a run manually from the Actions tab (`workflow_dispatch`) to test
before waiting for the schedule.

## Draft review (standalone)

A separate one-off tool, unrelated to the weekly/monthly/season dossier:
given a league ID, grades every team's most recent draft. Not batched, not
scheduled — run it manually whenever a league's draft finishes.

```bash
python -m sleeper_dossier.draft_review --league YOUR_LEAGUE_ID --html draft.html
```

Requires `ANTHROPIC_API_KEY` — without it you still get the draft board, just
no grades. Per team, it calls Claude (`claude-opus-5`) with the **web search**
tool enabled so grades are grounded in real, current ADP and expert rankings
rather than the model's training-data guesses, then parses a `Grade: B+`-style
line plus a short roast-style paragraph out of the response (`draft_review.py`).
One API call (with up to 5 searches) per team, so a 12-team league costs 12 calls.

## Architecture

```
sleeper_dossier/
  data.py         Sleeper API access + caching -> SeasonData
  calendar_map.py NFL week -> real calendar month mapping
  stats.py        all-play, luck index, season accumulation, OPTIMAL-LINEUP EFFICIENCY,
                  power ranking, weekly median, closest/blowout game
  monthly.py      aggregate a set of weeks into monthly per-team stats
  waivers.py      FAAB/waiver analysis (best pickup, fumbler, most active)
  history.py      cross-season rivalry data (previous_league_id chain, cached to disk —
                  past seasons never change, so the chain is only ever walked once)
  awards.py       Hall of Fame/Shame engine (MONTHLY_AWARDS, SEASON_AWARDS, WEEKLY_AWARDS)
  decision.py     Decision Lab: projection-vs-actual player awards (nflverse, falls back
                  to Sleeper's own pre-game projections if nfl_data_py/nflverse is unavailable)
  roast.py        Claude API commentary layer (generate_commentary — degrades gracefully
                  without a key)
  images.py       avatar/headshot URL construction + local disk cache -> base64 data URIs
  render.py       text + HTML renderers (render_html/render_text for monthly/season,
                  render_weekly_html/render_weekly_text for a single week)
  pdf_render.py   paginated, print-ready HTML for pdf.py (landscape A4, one page per section)
  pdf.py          HTML -> PDF via headless Chromium (Playwright)
  assets.py       screenshot every award card/chart/table in a report to individual PNGs
  cli.py          single-league entry point
  batch.py        many-league runner + email delivery
  sheet.py        Google Sheet ingestion for batch mode (service account)
  trial.py        free-trial tier resolution (Date + Teir columns -> effective tier)
  draft_review.py standalone: web-search-grounded draft grades (not part of the dossier)
```

Data flows one way: `data → stats/waivers/history/decision → awards → roast → render (→ pdf_render → pdf)`.

### Award card images

HTML award cards embed a small image where there's a natural subject — never on
league-wide stat awards with no individual subject. `awards.Award` carries
`image_kind` ("manager" | "player" | "trade" | "none"), `player_id`, and `loser_rid`;
`render._award_image_html` reads these and `images.py` does the actual fetching:

- **Player headshot** (rounded square): awards that name a specific player — The
  Sharpshooter, Wheeler Dealer/Pickup of the Month/Season, Waiver Disaster, Bench
  Warmer of the Week, and the Draft Value table's steals/busts.
- **Manager avatar** (circle): the default for everything else — awards about a
  manager's overall performance, luck, or lineup decisions, plus a small avatar per
  team on each Rivalry Watch card.
- **Both managers' avatars + a swap glyph**: Best Trade of the Month/Season.

New awards default to `image_kind="manager"` (every award has a `winner_rid`, so this
is almost always right) — only override it when an award's real subject is a
specific player or a trade.

Images are fetched once and cached to disk (`.cache/images/`, keyed by a hash of the
URL) as raw bytes, then re-served as base64 data URIs embedded directly in the HTML —
not as `<img src="https://...">` links. Two reasons: the PDF step is real headless
Chromium (Playwright), which *would* fetch remote images on its own, but with no
cache across renders and a hard dependency on the rendering machine having live
network access at PDF time; and batch.py emails this same HTML directly, where inline
data URIs display immediately instead of waiting on a "show images" click. A bundled
local SVG silhouette (no network, no external default) is the fallback both when an
avatar/headshot fetch fails (cached via an empty `.fail` marker so a permanently
404-ing ID isn't re-fetched every run) and, defensively, via each `<img>`'s `onerror`.
Sleeper's CDN sometimes mislabels the `Content-Type` header (a `.jpg` URL serving PNG
bytes under `Content-Type: image/jpeg`) — `images._sniff_ext` reads the real magic
bytes rather than trusting the header or the URL extension.

## Adding a new award

An award is one function that takes a context dict and returns an `Award` (or `None`
to skip). Register it in `WEEKLY_AWARDS` or `SEASON_AWARDS`. Example:

```python
def a_kicker_hero(ctx):
    # highest-scoring kicker started this week
    ...
    return Award("The Boot", "Best kicker performance", "fame", rid, headline, [])

WEEKLY_AWARDS.append(a_kicker_hero)
```

The named title is the product; the stat is trivial. That's the whole design.

New awards get a flat default **roast severity** (50/100) automatically, which routes
them to the cheaper filler-tier model — fine to leave as-is. If an award has a clean
numeric "how extreme is this" signal and a sensible historical comparison pool, add a
bespoke entry to `awards._SEVERITY_FNS` (keyed by award title) so it can earn its way
into the high-severity "headline" tier — see `_severity_bench_warmer`/`_severity_match_margin`
for examples.

## What's computed

**Weekly:** Top of the Pile, Sharpshooter, Highway Robbery, Perfect Manager, Wheeler Dealer
(best pickup), Coin-Flip King (won despite a bottom-3 score), Nail-Biter, Bottom Feeders,
Bench Warmer, Bumbling Boss, Robbed (lost despite a top-3 score), Waiver Disaster (worst
FAAB spend), Sleepwalker (started the exact same lineup as last week).

Plus, beyond the awards: a **power ranking** distinct from the standings (60% all-play
win% through that week + 25% recent-form percentile, last 3 weeks + 15% recent lineup
efficiency — see `stats.power_rank` for the exact formula and the "vs Standings" column
that's meant to spark debate), a **luck index** (each team's all-play record alongside
their actual result), a **median what-if** (who beat the league median that week),
closest-matchup/biggest-blowout callouts, Chart.js visuals scoped to just this week (luck
scatter, points-vs-week-average, lineup efficiency), and a **rivalry line per matchup**
using Sleeper's multi-season history (`history.py`) — all-time head-to-head by manager,
not roster, since `roster_id` resets every season but the Sleeper account doesn't. A 2+
game win streak by either team leads the line (that's what drives trash talk — "won 2
straight"), with the all-time tally demoted to a supporting clause; falls back to
"First-ever meeting between X and Y" for new leagues/managers. The weekly report stays
deliberately this-week-only — season-cumulative numbers live in the monthly/season
reports instead, so the weekly report stays short and snackable.

**Monthly:** Manager of the Month (most points above league average), Tactician of the
Month, On a Heater (best all-play form), Up and to the Right (biggest climb), Peak of the
Month, Wheeler-Dealer (most trades), Waiver Warrior (most points added via waiver),
FAAB-ulous (most FAAB spent), **Pickup of the Month** (single best waiver/FA add, points
scored since — distinct from Waiver Warrior's volume-across-all-pickups framing),
**Best Trade of the Month** (the month's most lopsided single trade by points swing
between the two sides — distinct from The Mark's team-level net-across-all-trades
framing), Best Manager Below .500, Lineup Wizard (best single-week efficiency), Bench
Hero Avoided, Closest-Call Survivor, Most Improved Scoring (vs the team's own prior
month), Giant Killer (beat the month's highest scorer) — and the mirrored Hall of Shame:
Dud of the Month, Freefall, Asleep at the Wheel, The Mark, Waiver Washout.

**Season:** Coach of the Year, Old Reliable (consistency), Horseshoe Up Somewhere (luckiest),
Best/Worst Week, Wheeler-Dealer, Waiver Warrior, FAAB-ulous, **Pickup of the Season**,
**Best Trade of the Season** (same framing as their monthly counterparts, season-wide),
Lord of the Dullards, Heart-Attack Merchant (volatility), Cursed (unluckiest), The Mark,
Waiver Washout, bench hoarder.

Plus standings, a **Luck Index — Season to Date** (actual record alongside all-play
record; luck = actual wins − all-play-deserved wins), a **Median What-If — To Date**
(weeks beating the league's weekly median, cumulative through this period — in monthly
reports that's "through end of this month," not the literal season; season reports also
get the full **Power Ranking**, season-only since a single month's sample is too
schedule-thin for the formula to mean much), a season-only **Draft Value** table —
biggest steals and busts by production vs. draft slot (`stats.draft_value_board`; needs
`SeasonData.draft_picks`, fetched from Sleeper's draft endpoints — empty for leagues with
no recorded draft, and keeper-league "rounds" aren't a real ADP signal so treat that
league type's results loosely) — and (in the HTML report) Chart.js visualizations of
all-play vs actual win%, monthly +/- vs average, and season efficiency per team.

## Notes / known limits

- **Optimal lineup** uses a greedy slot-filler (most-constrained slots first). Correct for
  standard slot sets incl. FLEX/SUPER_FLEX; verify exotic IDP leagues.
- **points-since-added** for waiver pickups scans subsequent played weeks; early-season
  pickups will show small totals until more weeks are played.
- **Sleepwalker** deliberately checks "same starters as last week," not Sleeper's
  `injury_status` field — that field is a live, current-day snapshot, not historical
  per-week status, so it can't reliably tell you who was hurt in a past week.
- **Rivalry history** is cached to `.cache/history_<league_id>.json` forever (past seasons
  are immutable). Delete that file to force a rebuild if Sleeper's chain metadata changes.
- Monthly/weekly reports generated **retrospectively** (after the season has moved past
  that period) correctly freeze standings/season-stats at that period's end — see
  `stats.standings_through`/`stats.season_stats(upto_week=...)` — rather than leaking
  later weeks' results in.
- **Roast generation is one round per report, not one call per award.** Each award gets a
  0-100 roast severity score (`awards._attach_severity`); the 2-3 highest-severity awards
  go to `roast.MODEL_HEADLINE` (Sonnet) along with a storyline ranking and the report's
  recap paragraph, and everything else goes to `roast.MODEL_FILLER` (Haiku) in one more
  call — at most 2 API calls per report instead of one per award. Both calls share
  `roast.ROAST_STYLE_GUIDE` so there's no visible seam between a headline roast and a
  filler one. The models and severity threshold are constants at the top of `roast.py`.
  The recap paragraph is explicitly instructed to open with the #1-ranked storyline (not
  bury it in a scene-setter) and to match the style guide's punchy, conversational voice
  rather than reading like a dry stats summary.
- **Draft Value needs `SeasonData.draft_picks`** — fetched once per `fetch_season()` call
  from `GET /league/{id}` (for `draft_id`) + `GET /draft/{draft_id}/picks`. If a league has
  no `draft_id` (or Sleeper has no picks recorded yet), this is just an empty dict and the
  Draft Value table silently doesn't render — not an error.
- **Trade-value math (per-trade and team-level) doesn't account for draft-pick
  compensation** — Sleeper's trade payload only reports a *count* of picks involved, not
  which player was later drafted with them, so a trade with picks attached is scored only
  on the players actually exchanged. `Best Trade of the Month/Season`'s description notes
  this explicitly when picks were part of the deal.
