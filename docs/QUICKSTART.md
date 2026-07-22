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

## 0. Starting from scratch? Scaffold a workspace
If all you have is your name and the league sheet link, let the tool build the folder structure:
```bash
ncbl setup --username espiiii --ranking-sheet-url "https://docs.google.com/spreadsheets/d/<ID>/edit"
```
This creates `espiiii_ncbl/` with:
```
espiiii_ncbl/
  ncbl.config.json   # your username + sheet link (edit season tab names if needed)
  reports/           # drop NCBLAST .pdf or .json reports here (any tournament)
  meta/              # drop the field Meta Analysis .json here (newest is auto-used)
  out/               # generated reports land here
  RUN.md             # copy-paste commands
```
Then, from inside that folder, `--config ncbl.config.json` supplies your player, sheet, reports,
and meta automatically — you don't need `--player` / `--input` / `--reports` / `--meta`:
```bash
cd espiiii_ncbl
ncbl standings --config ncbl.config.json
ncbl report    --config ncbl.config.json --outdir out
ncbl coach     --config ncbl.config.json --outdir out                        # lifetime
ncbl coach     --config ncbl.config.json --season "2026 Season 6" --outdir out
```

## 1. You have the league spreadsheet
Two ways to point the tool at it:
- **A file** — Google Sheet → **File → Download → Microsoft Excel (.xlsx)**, then use its path.
- **A link** — pass the shareable sheet URL directly (no download). Works with a Google Sheets
  link, a shortened link that redirects to one (tinyurl/bit.ly), or a direct `.xlsx`/`.csv` URL.
  The sheet must be shared **"Anyone with the link → Viewer"** (or published to the web);
  a private sheet that needs a Google login must be downloaded instead.
```bash
# by file
python -m ncbl standings --input sheet.xlsx --top 20
# by link (quote it so the shell doesn't choke on # or &)
python -m ncbl standings --input "https://docs.google.com/spreadsheets/d/<ID>/edit" --top 20
python -m ncbl standings --input "https://tinyurl.com/NCBL2026Rankings" --top 20

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
