# Sleeper Dossier

**Monthly** and end-of-season fantasy reports for **Sleeper** NFL leagues — a done-for-you
"dossier" of named awards (Hall of Fame / Hall of Shame), standings, an all-play luck
leaderboard, and optimal-lineup efficiency, with optional AI-written roast commentary.

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

# End-of-season review
python -m sleeper_dossier.cli --league YOUR_LEAGUE_ID --season --html review.html

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
export ANTHROPIC_API_KEY=sk-ant-...
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

# End-of-season reviews for everyone
python -m sleeper_dossier.batch --csv leagues.csv --season
```

Email requires: `SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM`.

## Architecture

```
sleeper_dossier/
  data.py         Sleeper API access + caching -> SeasonData
  calendar_map.py NFL week -> real calendar month mapping
  stats.py        all-play, luck index, season accumulation, OPTIMAL-LINEUP EFFICIENCY
  monthly.py      aggregate a set of weeks into monthly per-team stats
  waivers.py      FAAB/waiver analysis (best pickup, fumbler, most active)
  awards.py       Hall of Fame/Shame engine (MONTHLY_AWARDS, SEASON_AWARDS, WEEKLY_AWARDS)
  roast.py        Claude API commentary layer (degrades gracefully without a key)
  render.py       text + HTML renderers (HTML is the screenshot-friendly asset)
  cli.py          single-league entry point
  batch.py        many-league runner + email delivery
```

Data flows one way: `data → stats/waivers → awards → roast → render`.

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

## What's computed

**Monthly:** Manager of the Month (most points above league average), Tactician of the
Month, On a Heater (best all-play form), Up and to the Right (biggest climb), Peak of the
Month, Dud of the Month, Freefall, Asleep at the Wheel (worst efficiency).

**Weekly** awards are retained in `WEEKLY_AWARDS` if you ever want a weekly variant:
Top of the Pile, Sharpshooter, Highway Robbery, Perfect Manager, Wheeler Dealer,
Lucky Sod, Nail-Biter, Bottom Feeders, Bench Warmer, Bumbling Boss, Hard Done By, FAAB Fumbler.

**Season:** Coach of the Year, Old Reliable (consistency), Horseshoe Up Somewhere (luckiest),
Best/Worst Week, Lord of the Dullards, Heart-Attack Merchant (volatility), Cursed (unluckiest),
bench hoarder.

Plus standings and the **(Un)Lucky Leaderboard** (actual wins − all-play-deserved wins).

## Notes / known limits

- **Optimal lineup** uses a greedy slot-filler (most-constrained slots first). Correct for
  standard slot sets incl. FLEX/SUPER_FLEX; verify exotic IDP leagues.
- **points-since-added** for waiver pickups scans subsequent played weeks; early-season
  pickups will show small totals until more weeks are played.
- The roast layer model string is set in `roast.py` (`MODEL`). Use a cheaper model for
  large batch runs if cost matters.
```
