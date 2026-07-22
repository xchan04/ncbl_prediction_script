# ncbl_prediction_script

Reproduce the whole NorCal Beyblade League (NCBL) ranking analysis for **any player** —
standings, "what do I need to do" probabilities, threat lists, and the full set of
climbing/animation videos — from the downloaded Google Sheet.

No Claude agent required. Point it at the sheet, pass a player name, get the numbers and the videos.

---

## What it does

1. **Ingests** the league sheet (whole workbook `.xlsx`, or per-tab `.csv` exports).
2. **Computes standings** using the real formula: `placement points (by field-size tier) + GS wins × 0.33`, scored as **best 6 of your first 10** events. Verified to match the published tab.
3. **Predicts** with a Monte-Carlo simulation (replays the rest of the season thousands of times) → `P(Top N)`, best/worst outcomes, and (optionally) `P(invitational open-spot)`.
4. **Handles unknown schedules** — if you know the upcoming events, list them; if not, it gap-fills each rival's future count from their attendance rate.
5. **Generates videos** parameterized by player: follow-cam climb, whole-field bump chart, Monte-Carlo mosaic, region map, and a 9:16 vertical hook.

> **What can I get for what I have?** See **[docs/CAPABILITIES.md](docs/CAPABILITIES.md)** — a
> full input→output matrix. Minimum: username + league spreadsheet (+ season length + a schedule,
> accurate or estimated). Add NCBLAST report PDFs and/or Challonge links for progressively richer reports.
>
> **Just want the commands?** See **[docs/QUICKSTART.md](docs/QUICKSTART.md)** — copy-paste, grouped by what you have.

## Install

```bash
git clone git@github-personal:xchan04/ncbl_prediction_script.git
cd ncbl_prediction_script

python3 -m venv .venv                    # create a virtual environment
source .venv/bin/activate                # macOS/Linux
# Windows PowerShell:  .venv\Scripts\Activate.ps1
# Windows cmd.exe:     .venv\Scripts\activate.bat
# (Windows: use `py` instead of `python3`; if PS blocks the script, run once:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned)

pip install -r requirements.txt          # openpyxl, matplotlib, pdfplumber
pip install -r requirements-dev.txt      # optional: pytest
# videos also need ffmpeg on PATH:  brew install ffmpeg   (macOS)

cp config.example.json config.json       # edit season tabs / schedule / invite lists
python -m ncbl --help                    # verify
```
Run commands from the repo root as `python -m ncbl …` (re-activate the venv in new shells).
`.venv/` is gitignored.

**Optional — install as a command.** `pip install -e .` (uses `pyproject.toml`) puts an `ncbl`
executable on your PATH, so you can run it from any directory without `python -m`:
```bash
pip install -e .          # editable install into the venv
ncbl --help
ncbl coach --reports ~/Downloads/ --player espiiii --outdir out/
```

## Get the data

The Google Sheet has no open API here, so download it manually:
**File → Download →** either **Microsoft Excel (.xlsx)** (easiest — one file, all tabs)
or **CSV** of the *Data Entry* and *Solo Rankings* tabs.

## Usage

Run from the repo root (the folder containing the `ncbl/` package):

```bash
# Standings
python -m ncbl standings --input sheet.xlsx --top 20

# What does a player need to do? (uses config.json for schedule / invite lists)
python -m ncbl predict  --input sheet.xlsx --player espiiii --config config.json

# Who overtook them / who can still catch them
python -m ncbl threats  --input sheet.xlsx --player espiiii --window 6

# Packaged report for a player -> .txt + .json + styled .html (black/orange)
python -m ncbl report   --input sheet.xlsx --player espiiii --outdir out/

# COACH: analyze NCBLAST match reports -> weaknesses / what-to-run / matchup swaps
# Accepts a folder or any number of reports, PDF OR JSON. More reports = higher confidence.
python -m ncbl coach    --reports Downloads/ --player espiiii --outdir out/
python -m ncbl coach    --reports rfv.pdf mpp.pdf rdc.pdf --player espiiii --outdir out/
python -m ncbl coach    --reports rfv.json mpp.json --player espiiii --outdir out/    # JSON

# CHALLONGE: head-to-head "who keeps beating you" from brackets (needs a free API key)
# Covers tournaments that never published an NCBL report; caches JSON for offline reruns.
python -m ncbl challonge --player espiiii --from-sheet sheet.xlsx --api-key KEY --outdir out/
python -m ncbl challonge --player espiiii --slugs ncbl-goonday ncbl-SRSv10 --api-key KEY

# One video: follow | overview | montecarlo | map | hook
python -m ncbl video follow --input sheet.xlsx --player espiiii --out out/climb.mp4 --published-end
python -m ncbl video hook   --input sheet.xlsx --out out/hook.mp4 --top-number 14 --drop-number 21

# The whole video package for a player
python -m ncbl all --input sheet.xlsx --player espiiii --outdir out/
```

CSV input: pass the Data-Entry CSV directly, or a **folder** containing both the
Data-Entry and Solo-Rankings CSVs (matched by filename keywords).

## Configuration

Everything tunable lives in `ncbl/config.py` (`DEFAULTS`). Override any field with a
JSON file passed via `--config` — see **`config.example.json`**. Key sections:

| Field | Purpose |
|---|---|
| `data_entry_sheet` / `rankings_sheet` | tab names for the current season |
| `columns` / `rankings_cols` | column layout (1-indexed) if the sheet changes |
| `placement_points`, `cap_tiers`, `gs_win_points` | the scoring table |
| `best_of`, `of_first` | the "best 6 of first 10" rule |
| `ranked_only` | rank only registered players (those on the rankings tab); excludes guests/unregistered |
| `schedule.known_events` | list upcoming events `{name, cap}` if known |
| `schedule.remaining_events` | fallback when schedule is unknown (gap-fill) |
| `invited`, `wildcards`, `open_spots` | invitational open-spot analysis (0 disables) |
| `monte_carlo` | trials, breakout probability, seed |
| `regions`, `home`, `reach_limit_lat` | the setting/map video |
| `target_rank` | the rank you're chasing |

### New season / different league
Change `data_entry_sheet`, `rankings_sheet`, and (if the cap tiers or point values
change) the `placement_points` table. Nothing else is hard-coded.

## Notes / gotchas baked in
- Player names are normalized to lowercase and **case-variant typos are merged**
  (the sheet logs e.g. `deviousSprite` and `DeviousSprite` separately).
- The published rankings tab can have tie/skip rank numbers; use `--published-end`
  on videos to anchor the final frame to the **official** rank instead of a recompute.
- Ranks are 1-indexed with a deterministic tie-break (score desc, then name), so
  `standings`, `predict`, and `threats` always agree.
- Monte-Carlo assumptions (breakout %, gap-fill) are all in config — tune to taste.

## Tests
```bash
pip install -r requirements-dev.txt
python -m pytest -q
```
Covers the scoring formula (points table, best-6-of-10) and rank consistency
(standings / ranks / predict / threats must agree; ties stay stable). CI runs
these on every push (`.github/workflows/tests.yml`).

## Layout
```
ncbl/
  points.py      scoring table + score_event / best-of
  config.py      DEFAULTS + JSON override loader
  loader.py      read xlsx/csv -> normalized League object
  standings.py   standings, ranks, snapshots, cutoff
  simulate.py    Monte-Carlo engine + predict/threats reports
  report.py      package a player report as .txt / .json / .html
  ncblast_parser.py  parse an NCBLAST match-report PDF -> structured dict
  ncblast_json.py    parse an NCBLAST report JSON (schema-agnostic) -> same dict
  coaching.py    aggregate N reports -> weaknesses / meta / swaps (+ txt/json/html)
  challonge.py   head-to-head records from Challonge brackets (cache-first, offline-friendly)
  viz.py         all video/chart generators (+ coaching matchup chart)
  cli.py         argparse CLI  (python -m ncbl ...)
config.example.json
requirements.txt
```

## Coach mode — robust to how much data you have
`coach` ingests **any number** of NCBLAST reports — **PDF or JSON**, a folder or a list — and
gets **more comprehensive the more you feed it** — an explicit incentive to collect reports:
- Every finding is **data-driven** (traceable to the report numbers) — no AI at runtime,
  no external part database.
- Sample sizes accumulate across reports, so findings graduate *tentative → likely →
  confirmed*, and a **confidence tier** (Bronze/Silver/Gold) gates the deeper sections.
- With ≥2 events, **cross-event signal** unlocks (per-combo trends, a widened meta).
- **Degrades gracefully**: works from a single report, tolerates missing/garbled sections
  (each PDF section parses independently), and older/letter-spaced report layouts
  partial-parse rather than failing. More/other players' reports simply widen the meta.

### JSON reports — schema-agnostic parsing
The NCBLAST devs also provide reports as **JSON**. Drop `*.json` files into the same
`--reports` folder (or list them) and they're parsed alongside PDFs — no flag needed.
Because the exact JSON key names and nesting aren't fixed, the JSON parser is
**schema-agnostic**: it finds each field by *meaning*, not by a fixed path —
- **alias matching** on normalized keys (`winRate`, `win_pct`, `winPercent`, `wr` all map to win%),
- **deep tree search** (fields can live at any nesting depth), and
- **shape detection** (a row with a combo string + win%/battles is a combo; a row with two
  combos + W-L is a matchup), plus value coercion (`0.63`, `63.2`, `"63.2%"` → `63.2`).

This means a **new JSON layout usually needs no code change** — it round-trips whether the file
matches our own shape or renames/re-nests everything. It's *best effort*, not a guarantee: a
field it genuinely can't recognize degrades to empty rather than crashing (the coaching engine
then just skips that section). If the devs introduce a brand-new *name* for something, add it to
the relevant alias set at the top of `ncbl/ncblast_json.py` — a one-line change, no logic edits.

The HTML report **embeds the matchup chart inline** (no separate image needed) and includes a
**Rivals — head-to-head** section (your record vs each opponent from the match recaps),
scoped by `--season` (or lifetime). A `_matchups.png` is also written for reuse.

It also produces a **Recommended next-tournament deck** — a data-driven 3-combo pick that leans
on your best-performing combos (win% + PPB + tier), rewards combos with winning records vs the
combos you keep facing, benches your negative-PPB combos, and flags meta combos you have no
answer for. All from the report numbers — no AI, no external part database.

The report also includes four platform sections that get richer as more players plug in:
- **Goal card** — a crisp finish-line summary: form (win%/PPB), win% trajectory across events,
  placements, and up to three concrete next objectives synthesized from your top findings.
- **Launch & positioning** — your B-side vs X-side win%/PPB split; a large, battle-backed gap
  flags a coachable habit (favor the strong side, drill the weak one).
- **Vs the field (community benchmark)** — your win% per matchup against the field's aggregate
  (field excludes your own games), so it only fires once *other* players' reports are pooled;
  single-player runs show an honest "unlocks when others plug in" note.
- **Field benchmark per combo** — for each combo you run, your win% vs the battle-weighted field
  average of everyone who ran it, with your standing (top / middle / bottom third) and best peer.
- **Nemesis dossier** — for each player you're sub-.500 against, the builds they beat you with
  and your record vs each (from match recaps).

## Challonge head-to-head
`challonge` pulls match results from Challonge brackets — no combos, but it answers
"who keeps beating me" and covers tournaments that never published an NCBL report.
Needs a free **Challonge API key** (challonge.com → Developer API); pass `--api-key` or set
`CHALLONGE_API_KEY`. Fetched JSON is **cached** to `--cache`, so reruns work offline.
Harvest tournament ids from the sheet's links with `--from-sheet`, or list them via `--slugs`
(org URLs like `ncbl.challonge.com/goonday` → id `ncbl-goonday`). Output: `<player>_h2h.{txt,json,html}`.

Feed the same cache into the ranking report to annotate rivals with your record:
```bash
python -m ncbl report --input sheet.xlsx --player espiiii --h2h-cache challonge_cache/ --outdir out/
```
Only **genuine contenders** for your target rank are shown (a #82 player who can't reach the
cutoff even sweeping is not highlighted); each carries your head-to-head (e.g. `NiceGuyEddie #18 · H2H 0-3`).

## Seasonal vs lifetime
The spreadsheet spans multiple seasons. Point-standings/predictions are inherently **per-season**
(set `data_entry_sheet`/`rankings_sheet` to the season's tabs — best-6-of-10 is a seasonal rule).
Event-based analyses scope with `--season`:
- `coach --season "2026 Season 6"` limits reports to that season's date window; **omit it for lifetime** (all reports).
- `challonge --season NAME` (and `report --h2h-cache … --season NAME`) filter head-to-head to that window; omit for lifetime.
Season date windows live in config under `seasons`.

## License
Personal project — © xchan04.