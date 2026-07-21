# What you get for what you provide

The pipeline scales with your inputs. This maps **each input** to the **outputs it unlocks**, so
you know what a given player's report can and can't contain. Nothing is hardcoded — every number
comes from the inputs below; missing inputs simply mean the matching sections are skipped.

---

## Inputs, from minimum to rich

| # | Input | Where it goes | What it powers |
|---|-------|---------------|----------------|
| 1 | **Username** | `--player` | Who every report is about |
| 2 | **League spreadsheet** (`.xlsx` or the Data-Entry/Rankings `.csv`s) | `--input` | Standings, predictions, threats, ranking report, videos |
| 3 | **Season length / windows** | `config.seasons` | Seasonal vs lifetime scoping; how much runway remains |
| 4 | **Tournament schedule** (accurate *or* estimated) | `config.schedule` | Prediction accuracy (see below) |
| 5 | **NCBLAST report PDFs** | `--reports` | Coaching: weaknesses, meta, matchup swaps, **next-deck recommendation**, rivals, matchup visual |
| 6 | **Challonge links + free API key** | `--from-sheet` / `--slugs` + `--api-key` | Head-to-head ("who keeps beating you"), coverage for tournaments **without** a report |

**Minimum to get something useful:** username + spreadsheet.
**Recommended baseline (per your ask):** username + spreadsheet + season length + tournament schedule
(accurate if you have it, estimated otherwise) → solid standings, predictions, and threats.
**Everything beyond that (reports, Challonge) makes the report progressively more comprehensive.**

---

## Capability matrix — inputs → outputs

| Output (command) | Needs username | Needs spreadsheet | Needs schedule | Needs report PDFs | Needs Challonge |
|---|:--:|:--:|:--:|:--:|:--:|
| **Standings** (`standings`) | – | ✅ | – | – | – |
| **Prediction "what do I need"** (`predict`) | ✅ | ✅ | ✅* | – | – |
| **Threats / rivals for the goal** (`threats`) | ✅ | ✅ | ✅* | – | ➕ (annotates H2H) |
| **Ranking report** txt/json/html (`report`) | ✅ | ✅ | ✅* | – | ➕ (rival H2H) |
| **Videos** (`video`, `all`) | ✅ | ✅ | ✅* | – | – |
| **Coaching + next-deck rec** (`coach`) | ✅ | – | – | ✅ | – |
| **Head-to-head** (`challonge`) | ✅ | ➕ (harvest links) | – | – | ✅ |

`✅` required · `➕` optional, adds depth · `–` not used · `✅*` = works without it (falls back to an
**estimated** remaining-events count), but is more accurate with a known schedule.

---

## The schedule input (accurate vs estimated)
Predictions/threats simulate the rest of the season, so they need to know how many events remain.
- **Accurate:** list upcoming events in `config.schedule.known_events` → `[{"name": "...", "cap": 32}, …]`.
  Each rival can attend up to that many; the target player's future events use those field caps.
- **Estimated:** if you don't know the schedule, set `config.schedule.remaining_events` (a count) and
  the pipeline **gap-fills** each rival's future events from their historical attendance rate.
Either way you get a prediction; the known schedule just tightens it.

## Season length
Set `config.seasons` windows (name → `["start","end"]`). This drives:
- **Which tab** the ranking side reads (`data_entry_sheet`/`rankings_sheet`) — points are per-season.
- **`--season` scoping** for coaching and head-to-head (seasonal vs **lifetime** = all).

---

## How comprehensiveness grows (the incentive)

| You provide… | You get… |
|---|---|
| Spreadsheet + season + schedule | Standings, "what do I need for Top-N", threats, ranking report, videos |
| …+ **1 report PDF** | Basic coaching (Bronze confidence): combo win rates, one event's matchups, a tentative next-deck |
| …+ **several report PDFs** | Gold-confidence coaching: confirmed weaknesses, finish vulnerabilities, meta you keep facing, a **legal part-unique next-deck recommendation**, cross-event trends, rivals |
| …+ **Challonge (API key)** | Head-to-head "nemeses", coverage for report-less tournaments, rival records folded into the ranking report |
| …+ **reports/brackets across seasons** | True **lifetime** view alongside the **seasonal** one |

**Rule of thumb:** more reports/brackets = larger samples = findings graduate *tentative → likely →
confirmed*, more matchups fill in, and cross-season views unlock. The pipeline always works with
whatever subset you have and degrades gracefully when something's missing.

## What no amount of data changes
- **Points ranking is inherently per-season** (best-6-of-first-10 is a seasonal rule) — there is no
  "lifetime standings" number, though lifetime coaching and head-to-head do exist.
- **Combo/finish/battle detail lives only in the NCBLAST reports** — the spreadsheet and Challonge
  can't substitute for them (spreadsheet = points, Challonge = scores/names, neither has combos).
