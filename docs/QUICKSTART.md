# Quickstart

Copy-paste commands, grouped by what you have. Run everything from the repo root
(the folder containing the `ncbl/` package). See **[CAPABILITIES.md](CAPABILITIES.md)** for
what each input unlocks.

## 0. Setup (once)
```bash
# clone (personal SSH alias from ACCESS.md; or use your https remote)
git clone git@github-personal:xchan04/ncbl_prediction_script.git
cd ncbl_prediction_script

# create + activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # macOS/Linux
# Windows PowerShell:  .venv\Scripts\Activate.ps1
# Windows cmd.exe:     .venv\Scripts\activate.bat
# (Windows: use `py`/`python` instead of `python3`. If PowerShell blocks the activate
#  script, run once: Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned)

# dependencies (inside the venv)
pip install -r requirements.txt          # openpyxl, matplotlib, pdfplumber
pip install -r requirements-dev.txt      # optional: pytest, to run the tests
# videos also need ffmpeg on PATH:  macOS: brew install ffmpeg

cp config.example.json config.json       # then edit: season tabs, schedule, invite lists
python -m ncbl --help                     # verify
```
Re-activate the venv (`source .venv/bin/activate`) in any new shell before running commands.
`.venv/` is gitignored, so it never gets committed.

**Optional:** `pip install -e .` installs an `ncbl` command on your PATH — then use `ncbl …`
from anywhere instead of `python -m ncbl …` (all examples below work either way).

## 1. You have the league spreadsheet
Download it: Google Sheet → **File → Download → Microsoft Excel (.xlsx)**.
```bash
python -m ncbl standings --input sheet.xlsx --top 20
python -m ncbl predict   --input sheet.xlsx --player espiiii --config config.json
python -m ncbl threats   --input sheet.xlsx --player espiiii
python -m ncbl report    --input sheet.xlsx --player espiiii --config config.json --outdir out/
```
`report` → `out/espiiii_report.txt / .json / .html`.

**Tell it the schedule** (in `config.json`) for sharper predictions:
```jsonc
"schedule": {
  "known_events": [{"name":"July 19 Cup","cap":32},{"name":"Aug 1 Major","cap":64}], // accurate
  "remaining_events": 12,   // used only if known_events is empty (estimated)
  "default_cap": 32
}
```

## 2. You also have NCBLAST report PDFs → coaching + next-deck recommendation
Point `--reports` at a folder or list of PDFs (more reports = higher confidence).
```bash
# lifetime (all reports)
python -m ncbl coach --reports ~/Downloads/ --player espiiii --outdir out/

# one season only
python -m ncbl coach --reports ~/Downloads/ --player espiiii --season "2026 Season 6" --outdir out/
```
→ `espiiii_coach.txt / .json / .html` (weaknesses, meta, matchup swaps, **legal part-unique deck rec**,
rivals, embedded matchup chart) + `_matchups.png`.

## 3. You also have Challonge brackets → head-to-head
Needs a free key (challonge.com → Developer API). First run fetches + caches; later runs are offline.
```bash
export CHALLONGE_API_KEY=your_key
# harvest bracket links straight from the sheet:
python -m ncbl challonge --player espiiii --from-sheet sheet.xlsx --outdir out/
# or list tournament ids (ncbl.challonge.com/goonday -> ncbl-goonday):
python -m ncbl challonge --player espiiii --slugs ncbl-goonday ncbl-SRSv10 --outdir out/
```
Fold that head-to-head into the ranking report's rivals:
```bash
python -m ncbl report --input sheet.xlsx --player espiiii --h2h-cache challonge_cache/ --outdir out/
```

## 4. Videos
```bash
python -m ncbl video follow --input sheet.xlsx --player espiiii --out out/climb.mp4 --published-end
python -m ncbl all          --input sheet.xlsx --player espiiii --outdir out/    # report + all videos
```

## Help
```bash
python -m ncbl --help
python -m ncbl coach --help
```

## New season / new player
- **New player:** change `--player`. If it's not found, the error lists valid names.
- **New season:** set `data_entry_sheet` / `rankings_sheet` in `config.json` to that season's tabs,
  and add the season window under `seasons`. Nothing else is hardcoded.
