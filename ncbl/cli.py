"""Command-line interface for the NCBL prediction pipeline.

Examples
--------
Standings:
    python -m ncbl standings --input sheet.xlsx

Prediction report for a player (what they need to do):
    python -m ncbl predict --input sheet.xlsx --player espiiii --config config.json

Who overtook them / who can still catch them:
    python -m ncbl threats --input sheet.xlsx --player espiiii

Generate one video (follow | overview | spikers | montecarlo | map | hook):
    python -m ncbl video follow --input sheet.xlsx --player espiiii --out out/climb.mp4

Generate the whole video package:
    python -m ncbl all --input sheet.xlsx --player espiiii --outdir out/
"""
from __future__ import annotations
import argparse
import json
import os
import re
from collections import defaultdict

from .config import load_config
from .loader import League
from . import standings as S
from . import simulate as SIM
from . import viz
from . import report as R
from . import coaching as CO
from . import challonge as CH
from . import sheet_source as _SS


def _load(args):
    cfg = load_config(args.config)
    src = getattr(args, "input", None) or cfg.get("ranking_sheet_url")
    if not src:
        raise SystemExit("error: no league sheet given. Pass --input (a .xlsx / .csv / folder / sheet URL), "
                         "or set 'ranking_sheet_url' in --config (see 'ncbl setup').")
    if not _SS.is_url(src) and not os.path.exists(src):
        raise SystemExit(f"error: input not found: {src}\n"
                         "Pass the league sheet file, a folder of CSVs, or a shareable sheet link (URL).")
    try:
        league = League(cfg).load(src)
    except RuntimeError as e:                    # sheet-URL fetch problems come through here
        raise SystemExit(f"error: {e}")
    if not league.by_player:
        raise SystemExit("error: no results found in the input. Check that it's the league sheet and that "
                         "'data_entry_sheet'/'columns' in your config match its layout.")
    return cfg, league


def _resolve_player(args, cfg):
    """--player, falling back to the config's player (set by `ncbl setup`)."""
    name = getattr(args, "player", None) or cfg.get("player")
    if not name:
        raise SystemExit("error: no player given. Pass --player NAME or set 'player' in --config "
                         "(see 'ncbl setup').")
    return name


def _pkey(league, name):
    """Resolve a player argument (any casing) to the internal lowercase key."""
    lc = name.strip().lower()
    if lc in league.by_player:
        return lc
    for p in league.by_player:
        if p.replace(" ", "") == lc.replace(" ", ""):
            return p
    raise SystemExit(f"Player '{name}' not found. Try one of: "
                     + ", ".join(sorted(league.name(p) for p in list(league.by_player)[:20])) + " ...")


def cmd_standings(args):
    cfg, league = _load(args)
    rows = S.standings(league)
    n = args.top or 25
    print(f"{'#':>3}  {'Player':20}{'Pts':>8}  events")
    for i, (p, s) in enumerate(rows[:n], 1):
        print(f"{i:>3}  {league.name(p):20}{s:8.3f}  {league.n_events(p)}")


def cmd_predict(args):
    cfg, league = _load(args)
    player = _pkey(league, _resolve_player(args, cfg))
    rep = SIM.predict_report(league, cfg, player, target_rank=args.target, remaining=args.remaining)
    print(f"\n{rep['player']}: #{rep['current_rank']}  {rep['current_score']} pts  "
          f"({rep['n_events']} events, {rep['slots_left']} slots left)")
    print(f"Target: Top {rep['target_rank']}   cutoff now = {rep['cutoff']}\n")
    stage = cfg.get("open_spots", 0)
    hdr = f"  {'strategy':22}{'total':>8}{'P(Top%d)':>10}" % rep['target_rank']
    if stage: hdr += f"{'P(spot)':>10}"
    hdr += f"{'median':>9}"
    print(hdr)
    for label, res in rep["lines"]:
        line = f"  {label:22}{res['score']:8.2f}{res['p_top']*100:9.1f}%"
        if stage: line += f"{(res['p_stage'] or 0)*100:9.1f}%"
        line += f"   #{res['median_rank']}"
        print(line)


def cmd_threats(args):
    cfg, league = _load(args)
    player = _pkey(league, _resolve_player(args, cfg))
    t = SIM.threats(league, cfg, player, window=args.window)
    print(f"\n=== Overtook {league.name(player)} (last {args.window} events) ===")
    for p, a, b, s in t["overtook"]:
        print(f"  {league.name(p):18} #{a} -> #{b}   {s:.2f}")
    print(f"\n=== Live threats (below, still have slots) ===")
    for p, b, slots, s in t["live"]:
        print(f"  {league.name(p):18} #{b}  {s:.2f}  ({slots} slots left)")


def cmd_report(args):
    cfg, league = _load(args)
    player = _pkey(league, _resolve_player(args, cfg))
    h2h = None
    if args.h2h_cache and os.path.isdir(args.h2h_cache):
        seasons = cfg.get("seasons") or {}
        tours = CH.load_cache(args.h2h_cache, season=args.season, seasons_cfg=seasons)
        if tours:
            h2h = CH.head_to_head(tours, league.name(player))
    data = R.build(league, cfg, player, target_rank=args.target,
                   remaining=args.remaining, window=args.window, top=args.top or 25, h2h=h2h)
    os.makedirs(args.outdir, exist_ok=True)
    base = os.path.join(args.outdir, args.name or _slug(data["player"]) + "_report")
    paths = R.write_all(data, cfg, base)
    print(R.to_txt(data))
    if h2h:
        print(f"[head-to-head from Challonge cache: {args.h2h_cache}"
              + (f" · season {args.season}" if args.season else " · lifetime") + "]")
    print("written:", ", ".join(os.path.basename(p) for p in paths), "->", args.outdir)


def _slug(name):
    return "".join(c if c.isalnum() else "_" for c in name).strip("_").lower()


def _find_meta(meta_dir):
    """Newest field meta-analysis JSON in meta_dir (by filename), or None."""
    if not meta_dir or not os.path.isdir(meta_dir):
        return None
    import glob
    js = sorted(glob.glob(os.path.join(meta_dir, "*.json")))
    return js[-1] if js else None


# ---------------- setup / scaffold ----------------
def cmd_setup(args):
    root = args.dir or f"{_slug(args.username)}_ncbl"
    folders = {
        "reports": "Drop every NCBLAST tournament report here — .pdf OR .json, any tournament, any season.\n"
                   "More reports = higher-confidence coaching. Filenames don't matter.",
        "meta": "Drop the field Meta Analysis export here as .json (e.g. ncbl_meta_2026-07-01.json).\n"
                "The newest file is used automatically for meta-counter picks.",
        "out": "Generated reports land here (espiiii_coach.html / .txt / .json / _matchups.png).",
    }
    made = []
    for name, blurb in folders.items():
        d = os.path.join(root, name)
        os.makedirs(d, exist_ok=True)
        readme = os.path.join(d, "README.txt")
        if not os.path.exists(readme):
            with open(readme, "w") as fh:
                fh.write(blurb + "\n")
        made.append(d)

    cfg_path = os.path.join(root, "ncbl.config.json")
    if os.path.exists(cfg_path) and not args.force:
        print(f"(kept existing {cfg_path} — pass --force to overwrite)")
    else:
        scaffold = {
            "player": args.username,
            "ranking_sheet_url": args.ranking_sheet_url or "PASTE_YOUR_GOOGLE_SHEET_LINK_HERE",
            "reports_dir": "reports",
            "meta_dir": "meta",
            "data_entry_sheet": "2026 Season 6 Data Entry",
            "rankings_sheet": "2026 Season 6 Solo Rankings",
            "target_rank": 10,
        }
        with open(cfg_path, "w") as fh:
            json.dump(scaffold, fh, indent=2)

    links_path = os.path.join(root, "challonge_links.txt")
    if not os.path.exists(links_path) or args.force:
        with open(links_path, "w") as fh:
            fh.write(_LINKS_TXT.format(user=args.username))

    run_md = os.path.join(root, "RUN.md")
    if not os.path.exists(run_md) or args.force:
        with open(run_md, "w") as fh:
            fh.write(_RUN_MD.format(user=args.username))

    # runner scripts (edit-the-vars-or-just-double-click), pre-wired to this workspace
    pipeline_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _write_runners(root, pipeline_dir, force=args.force)

    print(f"Workspace ready: {root}/")
    print("  ncbl.config.json   ← your username + sheet link (edit the season tab names if needed)")
    print("  reports/           ← drop .pdf / .json tournament reports here")
    print("  meta/              ← drop the field Meta Analysis .json here")
    print("  out/               ← reports are written here")
    print("  RUN.md             ← copy-paste commands")
    print("  install + run_lifetime + run_season + run_all + run_full  (.sh mac/linux, .bat Windows)")
    if not args.ranking_sheet_url:
        print("\nNext: paste your Google Sheet link into ncbl.config.json (ranking_sheet_url),")
        print("      shared 'Anyone with the link -> Viewer'.")
    print(f"\nThen, from inside {root}/:")
    print("  ./install.sh          (Windows: install.bat)   # one-time")
    print("  ./run_lifetime.sh     (Windows: run_lifetime.bat)")


def _write_runners(root, pipeline_dir, force=False):
    """Drop install + lifetime/season/both runner scripts into the workspace."""
    files = {
        "install.sh": (_SH_INSTALL.format(pipeline=pipeline_dir), True),
        "run_lifetime.sh": (_SH_RUN.format(title="LIFETIME coaching report", season_line="",
                                           coach='ncbl coach --config "$CONFIG" --outdir "$OUT"',
                                           out="out/lifetime"), True),
        "run_season.sh": (_SH_RUN.format(title="SEASON coaching report", season_line='SEASON="2026 Season 6"',
                                         coach='ncbl coach --config "$CONFIG" --season "$SEASON" --outdir "$OUT"',
                                         out="out/season"), True),
        "run_all.sh": (_SH_ALL, True),
        "run_full.sh": (_SH_FULL, True),
        "install.bat": (_BAT_INSTALL.format(pipeline=pipeline_dir), False),
        "run_lifetime.bat": (_BAT_RUN.format(title="LIFETIME coaching report", season_set="",
                                             coach="ncbl coach --config %CONFIG% --outdir %OUT%",
                                             out="out\\lifetime"), False),
        "run_season.bat": (_BAT_RUN.format(title="SEASON coaching report", season_set='set "SEASON=2026 Season 6"',
                                           coach='ncbl coach --config %CONFIG% --season "%SEASON%" --outdir %OUT%',
                                           out="out\\season"), False),
        "run_all.bat": (_BAT_ALL, False),
        "run_full.bat": (_BAT_FULL, False),
    }
    for name, (content, executable) in files.items():
        path = os.path.join(root, name)
        if os.path.exists(path) and not force:
            continue
        with open(path, "w", newline="\n") as fh:
            fh.write(content)
        if executable:
            os.chmod(path, 0o755)


_RUN_MD = """\
# {user} — NCBL pipeline workspace

Everything here is driven by **ncbl.config.json** (your username + Google Sheet link), so most
commands don't need `--player` or `--input`. Run them from inside this folder.

## One-time
Install the pipeline once (from the cloned `ncbl_prediction_script` repo):
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\\Scripts\\Activate.ps1
pip install -e /path/to/ncbl_prediction_script
```
Then edit **ncbl.config.json**:
- `ranking_sheet_url` → your league Google Sheet link (shared "Anyone with the link → Viewer").
- `data_entry_sheet` / `rankings_sheet` → the tab names for your current season.

## Put your data in
- `reports/` — every NCBLAST report you have (`.pdf` or `.json`), any tournament/season.
- `meta/`    — the field Meta Analysis export as `.json` (newest is picked automatically).

## Run
```bash
# Ranking side (reads the sheet link from config)
python -m ncbl standings --config ncbl.config.json
python -m ncbl report    --config ncbl.config.json --outdir out
python -m ncbl predict   --config ncbl.config.json
python -m ncbl threats   --config ncbl.config.json

# Coaching side (reads reports/ + meta/ from config)
python -m ncbl coach     --config ncbl.config.json --outdir out                       # lifetime
python -m ncbl coach     --config ncbl.config.json --season "2026 Season 6" --outdir out
```
Outputs land in `out/`. The more reports you add, the more comprehensive the coaching gets.
"""

_LINKS_TXT = """\
# {user} — Challonge bracket links (one per line, one per tournament)
# Paste the full URL for every tournament you played — even ones with no NCBLAST report.
# Example:
#   https://ncbl.challonge.com/goonday
#   https://challonge.com/abcd1234
# Lines starting with # are ignored. These fill your head-to-head record (who beat you,
# who you beat) for tournaments that never published a report. Challonge has no combo/deck
# data — decks are still inferred from your reports + history.

"""


# ---- runner script templates (generated into the workspace by `ncbl setup`) ----
_SH_INSTALL = """\
#!/usr/bin/env bash
# One-time install. Run:  ./install.sh
# Edit PIPELINE if you moved the ncbl_prediction_script repo.
set -e
cd "$(dirname "$0")"
PIPELINE="{pipeline}"

python3 -m venv .venv
. .venv/bin/activate
pip install -e "$PIPELINE"
echo
echo "Installed. Next:"
echo "  1) edit ncbl.config.json -> paste your Google Sheet link (ranking_sheet_url)"
echo "  2) drop reports into reports/  and the meta export into meta/"
echo "  3) ./run_lifetime.sh   (or ./run_season.sh / ./run_all.sh)"
"""

_SH_RUN = """\
#!/usr/bin/env bash
# {title}. Run:  ./THIS_FILE.sh
set -e
cd "$(dirname "$0")"
# ---- edit if you like ----
CONFIG="ncbl.config.json"
OUT="{out}"
{season_line}
# --------------------------
[ -d .venv ] && . .venv/bin/activate
{coach}
echo "Done -> $OUT"
"""

_SH_ALL = """\
#!/usr/bin/env bash
# Lifetime + season coaching, plus the ranking report/standings (needs the sheet link). Run: ./run_all.sh
set -e
cd "$(dirname "$0")"
CONFIG="ncbl.config.json"
SEASON="2026 Season 6"
[ -d .venv ] && . .venv/bin/activate
ncbl coach  --config "$CONFIG" --outdir out/lifetime
ncbl coach  --config "$CONFIG" --season "$SEASON" --outdir out/season
ncbl report --config "$CONFIG" --outdir out/lifetime || echo "(ranking report skipped — set ranking_sheet_url in $CONFIG)"
ncbl standings --config "$CONFIG" > out/standings.txt 2>/dev/null || true
echo "Done -> out/lifetime, out/season"
"""

_SH_FULL = """\
#!/usr/bin/env bash
# FULL run: Top-10 grind (ranking) + coaching + Challonge head-to-head. Run: ./run_full.sh
# Coaching confidence uses the sheet's TRUE event count (even events with no report),
# and Challonge fills your head-to-head for tournaments that never published a report.
set -e
cd "$(dirname "$0")"
CONFIG="ncbl.config.json"
SEASON="2026 Season 6"
LINKS="challonge_links.txt"        # paste your bracket links in this file (one per tournament)
CHALLONGE_API_KEY=""               # free key: challonge.com -> Developer API (needed once to fetch; then cached)
# -----------------------------------------------------------------------------------------
[ -d .venv ] && . .venv/bin/activate
mkdir -p out

# 1) fetch + cache Challonge brackets from your links (skips if no key and already cached)
if [ -s "$LINKS" ] && [ -n "$CHALLONGE_API_KEY" ]; then
  ncbl challonge --config "$CONFIG" --links "$LINKS" --api-key "$CHALLONGE_API_KEY" --cache challonge_cache --outdir out || echo "(challonge fetch skipped)"
fi

# 2) Ranking side — your Top-10 grind (reads the sheet from config: true event count)
ncbl standings --config "$CONFIG" > out/standings.txt || true
ncbl predict   --config "$CONFIG" || true
ncbl report    --config "$CONFIG" --h2h-cache challonge_cache --outdir out/lifetime 2>/dev/null \\
  || ncbl report --config "$CONFIG" --outdir out/lifetime || echo "(ranking report skipped)"

# 3) Coaching side — confidence + rivals reflect the sheet AND Challonge (report-less tournaments)
ncbl coach --config "$CONFIG" --links "$LINKS" --h2h-cache challonge_cache --api-key "$CHALLONGE_API_KEY" --outdir out/lifetime
ncbl coach --config "$CONFIG" --links "$LINKS" --h2h-cache challonge_cache --api-key "$CHALLONGE_API_KEY" --season "$SEASON" --outdir out/season
echo "Done -> out/lifetime, out/season, out/standings.txt"
"""

_BAT_INSTALL = """\
@echo off
REM One-time install. Double-click or run: install.bat
REM Edit PIPELINE if you moved the ncbl_prediction_script repo.
cd /d "%~dp0"
set "PIPELINE={pipeline}"

python -m venv .venv
call .venv\\Scripts\\activate.bat
pip install -e "%PIPELINE%"
echo.
echo Installed. Next:
echo   1) edit ncbl.config.json -^> paste your Google Sheet link (ranking_sheet_url)
echo   2) drop reports into reports\\  and the meta export into meta\\
echo   3) run_lifetime.bat   (or run_season.bat / run_all.bat)
pause
"""

_BAT_RUN = """\
@echo off
REM {title}. Double-click or run this .bat
cd /d "%~dp0"
set "CONFIG=ncbl.config.json"
set "OUT={out}"
{season_set}
if exist .venv\\Scripts\\activate.bat call .venv\\Scripts\\activate.bat
{coach}
echo Done -^> %OUT%
pause
"""

_BAT_ALL = """\
@echo off
REM Lifetime + season coaching, plus ranking report/standings. Double-click or run: run_all.bat
cd /d "%~dp0"
set "CONFIG=ncbl.config.json"
set "SEASON=2026 Season 6"
if exist .venv\\Scripts\\activate.bat call .venv\\Scripts\\activate.bat
ncbl coach  --config %CONFIG% --outdir out\\lifetime
ncbl coach  --config %CONFIG% --season "%SEASON%" --outdir out\\season
ncbl report --config %CONFIG% --outdir out\\lifetime
ncbl standings --config %CONFIG%
echo Done -^> out\\lifetime, out\\season
pause
"""

_BAT_FULL = """\
@echo off
REM FULL run: Top-10 grind (ranking) + coaching + Challonge head-to-head. Double-click or run: run_full.bat
cd /d "%~dp0"
set "CONFIG=ncbl.config.json"
set "SEASON=2026 Season 6"
set "LINKS=challonge_links.txt"
REM free key: challonge.com -> Developer API (needed once to fetch; then cached)
set "CHALLONGE_API_KEY="
if exist .venv\\Scripts\\activate.bat call .venv\\Scripts\\activate.bat
if not exist out mkdir out
if not "%CHALLONGE_API_KEY%"=="" ncbl challonge --config %CONFIG% --links "%LINKS%" --api-key "%CHALLONGE_API_KEY%" --cache challonge_cache --outdir out
ncbl standings --config %CONFIG% > out\\standings.txt
ncbl predict   --config %CONFIG%
ncbl report    --config %CONFIG% --h2h-cache challonge_cache --outdir out\\lifetime
ncbl coach     --config %CONFIG% --links "%LINKS%" --h2h-cache challonge_cache --api-key "%CHALLONGE_API_KEY%" --outdir out\\lifetime
ncbl coach     --config %CONFIG% --links "%LINKS%" --h2h-cache challonge_cache --api-key "%CHALLONGE_API_KEY%" --season "%SEASON%" --outdir out\\season
echo Done -^> out\\lifetime, out\\season, out\\standings.txt
pause
"""



def cmd_coach(args):
    cfg = load_config(args.config)
    report_paths = args.reports or [cfg.get("reports_dir") or "reports"]
    reports = CO.load_reports(report_paths)
    if not reports:
        raise SystemExit(f"error: no NCBLAST reports found in {report_paths}.\n"
                         "Drop report .pdf/.json files there, or pass --reports <folder|files>.")
    scope = "lifetime"
    if args.season:
        reports = CO.filter_by_season(reports, args.season, cfg.get("seasons") or {})
        scope = args.season
        if not reports:
            raise SystemExit(f"error: no reports fall within season '{args.season}'. "
                             "Check config 'seasons' windows or omit --season for lifetime.")
    players = sorted({str(r["player"]).lower() for r in reports if r.get("player")})
    player = getattr(args, "player", None) or cfg.get("player")
    if not player:
        raise SystemExit("error: no player given. Pass --player NAME (or set 'player' in --config). "
                         "Reports contain: " + ", ".join(players))
    meta_path = getattr(args, "meta", None) or _find_meta(cfg.get("meta_dir"))
    meta_report = None
    if meta_path:
        try:
            with open(meta_path, encoding="utf-8") as fh:
                meta_report = json.load(fh)
        except Exception as ex:
            print("(meta report skipped:", ex, ")")
    community = None
    if getattr(args, "bd", None):
        community = CO.load_reports(args.bd)
    # true events attended (from the league sheet) drives the confidence tier, even for
    # tournaments that never published a report.
    events_attended = None
    sheet = getattr(args, "input", None) or cfg.get("ranking_sheet_url")
    if sheet and str(sheet) != "PASTE_YOUR_GOOGLE_SHEET_LINK_HERE":
        try:
            league = League(cfg).load(sheet)
            events_attended = league.n_events(_pkey(league, player))
        except (SystemExit, Exception) as ex:
            print("(event count from sheet skipped:", ex, ")")

    # Challonge head-to-head from a links file / cache: fills rivals for tournaments with no
    # report, and counts them toward events attended. No decks (Challonge has no combos).
    h2h_extra = None
    slugs = CH.slugs_from_file(args.links) if getattr(args, "links", None) else []
    cache_dir = getattr(args, "h2h_cache", None) or "challonge_cache"
    api_key = getattr(args, "api_key", None) or os.environ.get("CHALLONGE_API_KEY")
    tournaments = []
    for s in slugs:
        try:
            tournaments.append(CH.parse_tournament(CH.fetch(s, api_key=api_key, cache_dir=cache_dir)))
        except Exception as ex:
            print(f"(challonge {s} skipped: {ex})")
    if not slugs and getattr(args, "h2h_cache", None):
        tournaments = CH.load_cache(cache_dir, season=args.season, seasons_cfg=cfg.get("seasons"))
    if tournaments:
        report_events = {re.sub(r"\s+", "", str(r.get("event", "")).lower()) for r in reports}
        extra = [t for t in tournaments if re.sub(r"\s+", "", str(t["name"]).lower()) not in report_events]
        h2h_extra = CH.head_to_head(extra, player)
        all_events = report_events | {re.sub(r"\s+", "", str(t["name"]).lower()) for t in tournaments}
        events_attended = max(events_attended or 0, len(all_events))
        merged = ", ".join(t["name"] for t in extra) or "none"
        print(f"[challonge: {len(tournaments)} bracket(s); {len(extra)} without a matching report "
              f"-> merged into rivals: {merged}]")
        print("  (if a bracket you DO have a report for is listed above, its name didn't match the "
              "report's event title — tell me and I'll map it so it isn't double-counted.)")

    # manual head-to-head file — for brackets the API can't reach (not your tournaments)
    manual = CH.load_h2h_file(args.h2h_file) if getattr(args, "h2h_file", None) else []
    if manual or h2h_extra:
        combined = defaultdict(lambda: [0, 0])
        for h in (h2h_extra or []):
            combined[h["opponent"]][0] += h["wins"]; combined[h["opponent"]][1] += h["losses"]
        for h in manual:
            combined[h["opponent"]][0] += h["wins"]; combined[h["opponent"]][1] += h["losses"]
        h2h_extra = [{"opponent": o, "wins": w, "losses": l} for o, (w, l) in combined.items()]
        if manual:
            print(f"[manual head-to-head: {len(manual)} opponent record(s) merged from {args.h2h_file}]")

    res = CO.coach(reports, player, scope=scope, meta_report=meta_report, community=community,
                   events_attended=events_attended, h2h_extra=h2h_extra)
    os.makedirs(args.outdir, exist_ok=True)
    base = os.path.join(args.outdir, _slug(res["player"]) + "_coach")
    img = base + "_matchups.png"
    try:
        viz.matchup_chart(res, cfg, img)
    except Exception as ex:
        print("(visual skipped:", ex, ")"); img = None
    paths = CO.write_all(res, cfg, base, image_path=img)   # HTML embeds the chart inline
    if img:
        paths.append(img)
    print(CO.coach_txt(res))
    print(f"[{len(reports)} report(s) · scope: {scope} · confidence {res['confidence']['tier']}]")
    print("written:", ", ".join(os.path.basename(p) for p in paths), "->", args.outdir)


def cmd_challonge(args):
    cfg = load_config(args.config)
    slugs = list(args.slugs or [])
    if getattr(args, "links", None):
        slugs += [s for s in CH.slugs_from_file(args.links) if s not in slugs]
    if args.from_sheet:
        slugs += [s for s in CH.slugs_from_sheet(args.from_sheet, cfg) if s not in slugs]
    if not slugs:
        raise SystemExit("error: give --slugs ncbl-goonday ..., --links links.txt, or --from-sheet sheet.xlsx.")
    api_key = args.api_key or os.environ.get("CHALLONGE_API_KEY")
    seasons = cfg.get("seasons") or {}
    tournaments, missed = [], []
    for s in slugs:
        try:
            data = CH.fetch(s, api_key, args.cache)
            if args.season and not CH._in_season(CH._tournament_date(data), args.season, seasons):
                continue
            tournaments.append(CH.parse_tournament(data))
        except Exception as ex:
            missed.append(f"{s}: {ex}")
    if not tournaments:
        raise SystemExit("error: no tournaments loaded.\n  " + "\n  ".join(missed))
    if missed:
        print("(skipped:", "; ".join(m.split(':')[0] for m in missed), ")")
    a = CH.analyze(tournaments, _resolve_player(args, cfg))
    os.makedirs(args.outdir, exist_ok=True)
    base = os.path.join(args.outdir, _slug(_resolve_player(args, cfg)) + "_h2h")
    paths = CH.write_all(a, cfg, base)
    print(CH.to_txt(a))
    print(f"[{len(tournaments)} tournament(s) · cache: {args.cache}]")
    print("written:", ", ".join(os.path.basename(p) for p in paths), "->", args.outdir)


def cmd_video(args):
    cfg, league = _load(args)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    kind = args.kind
    if kind in ("follow", "drop", "climb"):
        viz.follow(league, cfg, _pkey(league, _resolve_player(args, cfg)), args.out,
                   t_from=args.t_from, t_to=args.t_to,
                   published_end=args.published_end,
                   title=args.title)
    elif kind == "overview":
        viz.overview(league, cfg, _pkey(league, _resolve_player(args, cfg)), args.out, t_from=args.t_from, t_to=args.t_to)
    elif kind == "montecarlo":
        viz.montecarlo(league, cfg, _pkey(league, _resolve_player(args, cfg)), args.out)
    elif kind == "map":
        viz.regions_map(cfg, args.out)
    elif kind == "hook":
        viz.vertical_hook(cfg, args.out, top_number=args.top_number, drop_number=args.drop_number)
    else:
        raise SystemExit(f"Unknown video kind: {kind}")
    print("saved", args.out)


def cmd_all(args):
    cfg, league = _load(args)
    player = _pkey(league, _resolve_player(args, cfg))
    od = args.outdir; os.makedirs(od, exist_ok=True)
    j = lambda f: os.path.join(od, f)
    print("report (txt/json/html)...")
    data = R.build(league, cfg, player, target_rank=args.target, remaining=args.remaining)
    R.write_all(data, cfg, j("report"))
    print("standings racer..."); viz.follow(league, cfg, player, j("climb.mp4"), published_end=True)
    print("overview...");        viz.overview(league, cfg, player, j("overview.mp4"))
    print("monte carlo...");     viz.montecarlo(league, cfg, player, j("montecarlo.mp4"))
    print("map...");             viz.regions_map(cfg, j("map.png"))
    cur = S.rank_of(league, player)
    print("hook...");            viz.vertical_hook(cfg, j("hook.mp4"), top_number=cur, drop_number=cur)
    print("\nAll assets written to", od)


_EPILOG = """\
examples:
  # starting from scratch — scaffold a workspace (folders + config), then just use --config:
  python -m ncbl setup --username espiiii --ranking-sheet-url "https://docs.google.com/spreadsheets/d/<ID>/edit"
  #   -> creates espiiii_ncbl/ with reports/, meta/, out/, ncbl.config.json, RUN.md
  #   drop reports into reports/ and the meta export into meta/, then:
  python -m ncbl coach --config espiiii_ncbl/ncbl.config.json --outdir espiiii_ncbl/out

  # get the data either way:
  #   A) open the Google Sheet -> File -> Download -> Excel (.xlsx), or
  #   B) just pass the shareable sheet link ('Anyone with the link -> Viewer')

  python -m ncbl standings --input sheet.xlsx --top 20
  python -m ncbl standings --input "https://docs.google.com/spreadsheets/d/<ID>/edit" --top 20
  python -m ncbl predict   --input sheet.xlsx --player espiiii --config config.json
  python -m ncbl report    --input sheet.xlsx --player espiiii --outdir out/
  python -m ncbl threats   --input sheet.xlsx --player espiiii
  python -m ncbl video follow --input sheet.xlsx --player espiiii --out out/climb.mp4
  python -m ncbl all       --input sheet.xlsx --player espiiii --outdir out/
  python -m ncbl coach     --reports Downloads/ --player espiiii --outdir out/
  python -m ncbl challonge --player espiiii --from-sheet sheet.xlsx --api-key KEY --outdir out/

--input accepts: the whole workbook .xlsx, a Data-Entry .csv, a folder of CSVs, OR a
shareable sheet URL (Google Sheets link, a shortened link that redirects to one, or a
direct http(s) link to an .xlsx/.csv). Link-based input needs the sheet shared to
"Anyone with the link -> Viewer" (or published to web); private sheets must be downloaded.
Copy config.example.json -> config.json to set the season tabs, schedule, and invite lists.
"""


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="ncbl", description="NCBL ranking prediction + video pipeline",
        epilog=_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", metavar="{setup,standings,predict,report,coach,challonge,threats,video,all}")

    def common(p):
        p.add_argument("--input", metavar="PATH",
                       help="league sheet: .xlsx/.csv/folder or a sheet URL (default: config 'ranking_sheet_url')")
        p.add_argument("--config", metavar="FILE", help="JSON config overriding ncbl/config.py defaults")

    p = sub.add_parser("setup", help="scaffold a workspace (folders + config) for a player from scratch")
    p.add_argument("--username", required=True, help="player name (any casing)")
    p.add_argument("--ranking-sheet-url", dest="ranking_sheet_url", metavar="URL",
                   help="Google Sheet link (or file path) for the league rankings")
    p.add_argument("--dir", metavar="PATH", help="workspace folder to create (default: <username>_ncbl)")
    p.add_argument("--force", action="store_true", help="overwrite an existing config / RUN.md")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("standings", help="print the current standings")
    common(p); p.add_argument("--top", type=int, metavar="N", help="show top N (default 25)")
    p.set_defaults(func=cmd_standings)

    p = sub.add_parser("predict", help="probabilities of reaching a target rank for a player")
    common(p)
    p.add_argument("--player", help="player name (any casing; default: config 'player')")
    p.add_argument("--target", type=int, metavar="RANK", help="target rank (default from config)")
    p.add_argument("--remaining", type=int, metavar="N", help="events the player will still play (default: open slots)")
    p.set_defaults(func=cmd_predict)

    p = sub.add_parser("report", help="write a .txt + .json + .html report for a player")
    common(p)
    p.add_argument("--player", help="player name (any casing; default: config 'player')")
    p.add_argument("--outdir", default="out", metavar="DIR", help="output folder (default: out/)")
    p.add_argument("--name", metavar="BASE", help="output basename (default: <player>_report)")
    p.add_argument("--target", type=int, metavar="RANK")
    p.add_argument("--remaining", type=int, metavar="N")
    p.add_argument("--window", type=int, default=6, metavar="N", help="recent-events window for threats (default 6)")
    p.add_argument("--top", type=int, metavar="N", help="standings rows to include (default 25)")
    p.add_argument("--h2h-cache", dest="h2h_cache", metavar="DIR",
                   help="Challonge cache dir; annotates rivals with your head-to-head record")
    p.add_argument("--season", metavar="NAME", help="scope head-to-head to a season window (default: lifetime)")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("threats", help="who overtook the player / who can still catch them")
    common(p); p.add_argument("--player"); p.add_argument("--window", type=int, default=6, metavar="N")
    p.set_defaults(func=cmd_threats)

    p = sub.add_parser("video", help="render one visualization")
    common(p)
    p.add_argument("kind", choices=["follow", "drop", "climb", "overview", "montecarlo", "map", "hook"],
                   help="which visualization to render")
    p.add_argument("--player", default="", help="player to feature (not needed for map)")
    p.add_argument("--out", required=True, metavar="PATH", help="output file (.mp4, or .png for map)")
    p.add_argument("--t-from", dest="t_from", type=int); p.add_argument("--t-to", dest="t_to", type=int)
    p.add_argument("--published-end", action="store_true", help="anchor final frame to the published ranking")
    p.add_argument("--title"); p.add_argument("--top-number", type=int, default=14); p.add_argument("--drop-number", type=int, default=21)
    p.set_defaults(func=cmd_video)

    p = sub.add_parser("all", help="render every report + video for a player into one folder")
    common(p); p.add_argument("--player"); p.add_argument("--outdir", default="out", metavar="DIR")
    p.add_argument("--target", type=int, metavar="RANK"); p.add_argument("--remaining", type=int, metavar="N")
    p.set_defaults(func=cmd_all)

    p = sub.add_parser("coach", help="analyze NCBLAST match reports (PDF or JSON) -> coaching report + visual")
    p.add_argument("--reports", nargs="+", metavar="PATH",
                   help="a folder of reports, or .pdf/.json paths (default: config 'reports_dir')")
    p.add_argument("--player", help="player to coach (any casing); reports list their player")
    p.add_argument("--config", metavar="FILE", help="optional JSON config (theme, etc.)")
    p.add_argument("--outdir", default="out", metavar="DIR", help="output folder (default: out/)")
    p.add_argument("--season", metavar="NAME", help="scope to a season window (default: lifetime = all reports)")
    p.add_argument("--meta", metavar="FILE", help="optional field meta-analysis JSON -> meta-counter picks")
    p.add_argument("--input", metavar="PATH",
                   help="league sheet (.xlsx/.csv/folder/URL) -> confidence reflects true events attended "
                        "(default: config 'ranking_sheet_url')")
    p.add_argument("--links", metavar="FILE",
                   help="txt/md/json file of Challonge links (one per tournament) -> merges head-to-head, "
                        "even for tournaments with no report")
    p.add_argument("--h2h-file", dest="h2h_file", metavar="FILE",
                   help="manual head-to-head (json/txt of 'Opponent W-L') for brackets the API can't reach")
    p.add_argument("--h2h-cache", dest="h2h_cache", metavar="DIR",
                   help="Challonge cache dir (offline reuse of fetched brackets)")
    p.add_argument("--api-key", dest="api_key", metavar="KEY", help="Challonge API key (or set CHALLONGE_API_KEY)")
    p.add_argument("--bd", metavar="PATH", help=argparse.SUPPRESS)   # hidden: full-community prediction pool
    p.set_defaults(func=cmd_coach)

    p = sub.add_parser("challonge", help="head-to-head records from Challonge brackets (needs a free API key)")
    p.add_argument("--player", help="player to build head-to-head for (default: config 'player')")
    p.add_argument("--slugs", nargs="+", metavar="ID", help="Challonge tournament ids, e.g. ncbl-goonday")
    p.add_argument("--links", metavar="FILE", help="txt/md/json file of Challonge links (one per tournament)")
    p.add_argument("--from-sheet", dest="from_sheet", metavar="PATH", help="harvest Challonge links from a Data-Entry sheet")
    p.add_argument("--api-key", dest="api_key", metavar="KEY", help="Challonge API key (or set CHALLONGE_API_KEY)")
    p.add_argument("--cache", default="challonge_cache", metavar="DIR", help="JSON cache dir (enables offline reruns)")
    p.add_argument("--season", metavar="NAME", help="scope to a season window (default: lifetime)")
    p.add_argument("--config", metavar="FILE"); p.add_argument("--outdir", default="out", metavar="DIR")
    p.set_defaults(func=cmd_challonge)

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        raise SystemExit(0)
    args.func(args)


if __name__ == "__main__":
    main()
