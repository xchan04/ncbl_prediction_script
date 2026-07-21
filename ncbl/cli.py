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
import os

from .config import load_config
from .loader import League
from . import standings as S
from . import simulate as SIM
from . import viz
from . import report as R
from . import coaching as CO
from . import challonge as CH


def _load(args):
    if not getattr(args, "input", None):
        raise SystemExit("error: --input is required (path to the downloaded .xlsx, a Data-Entry .csv, "
                         "or a folder of CSVs).\nRun 'python -m ncbl --help' for examples.")
    if not os.path.exists(args.input):
        raise SystemExit(f"error: input not found: {args.input}\n"
                         "Download the league sheet (File -> Download) and pass its path with --input.")
    cfg = load_config(args.config)
    league = League(cfg).load(args.input)
    if not league.by_player:
        raise SystemExit("error: no results found in the input. Check that it's the league sheet and that "
                         "'data_entry_sheet'/'columns' in your config match its layout.")
    return cfg, league


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
    player = _pkey(league, args.player)
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
    player = _pkey(league, args.player)
    t = SIM.threats(league, cfg, player, window=args.window)
    print(f"\n=== Overtook {league.name(player)} (last {args.window} events) ===")
    for p, a, b, s in t["overtook"]:
        print(f"  {league.name(p):18} #{a} -> #{b}   {s:.2f}")
    print(f"\n=== Live threats (below, still have slots) ===")
    for p, b, slots, s in t["live"]:
        print(f"  {league.name(p):18} #{b}  {s:.2f}  ({slots} slots left)")


def cmd_report(args):
    cfg, league = _load(args)
    player = _pkey(league, args.player)
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


def cmd_coach(args):
    cfg = load_config(args.config)
    reports = CO.load_reports(args.reports)
    if not reports:
        raise SystemExit("error: no NCBLAST report PDFs could be read from --reports.\n"
                         "Pass a folder of report .pdf files or one/more file paths.")
    scope = "lifetime"
    if args.season:
        reports = CO.filter_by_season(reports, args.season, cfg.get("seasons") or {})
        scope = args.season
        if not reports:
            raise SystemExit(f"error: no reports fall within season '{args.season}'. "
                             "Check config 'seasons' windows or omit --season for lifetime.")
    players = sorted({str(r["player"]).lower() for r in reports if r.get("player")})
    if not args.player:
        raise SystemExit("error: --player is required. Reports contain: " + ", ".join(players))
    res = CO.coach(reports, args.player)
    os.makedirs(args.outdir, exist_ok=True)
    base = os.path.join(args.outdir, _slug(res["player"]) + "_coach")
    paths = CO.write_all(res, cfg, base)
    try:
        viz.matchup_chart(res, cfg, base + "_matchups.png")
        paths.append(base + "_matchups.png")
    except Exception as ex:
        print("(visual skipped:", ex, ")")
    print(CO.coach_txt(res))
    print(f"[{len(reports)} report(s) · scope: {scope} · confidence {res['confidence']['tier']}]")
    print("written:", ", ".join(os.path.basename(p) for p in paths), "->", args.outdir)


def cmd_challonge(args):
    cfg = load_config(args.config)
    slugs = list(args.slugs or [])
    if args.from_sheet:
        slugs += [s for s in CH.slugs_from_sheet(args.from_sheet, cfg) if s not in slugs]
    if not slugs:
        raise SystemExit("error: give --slugs ncbl-goonday ... or --from-sheet sheet.xlsx to harvest links.")
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
    a = CH.analyze(tournaments, args.player)
    os.makedirs(args.outdir, exist_ok=True)
    base = os.path.join(args.outdir, _slug(args.player) + "_h2h")
    paths = CH.write_all(a, cfg, base)
    print(CH.to_txt(a))
    print(f"[{len(tournaments)} tournament(s) · cache: {args.cache}]")
    print("written:", ", ".join(os.path.basename(p) for p in paths), "->", args.outdir)


def cmd_video(args):
    cfg, league = _load(args)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    kind = args.kind
    if kind in ("follow", "drop", "climb"):
        viz.follow(league, cfg, _pkey(league, args.player), args.out,
                   t_from=args.t_from, t_to=args.t_to,
                   published_end=args.published_end,
                   title=args.title)
    elif kind == "overview":
        viz.overview(league, cfg, _pkey(league, args.player), args.out, t_from=args.t_from, t_to=args.t_to)
    elif kind == "montecarlo":
        viz.montecarlo(league, cfg, _pkey(league, args.player), args.out)
    elif kind == "map":
        viz.regions_map(cfg, args.out)
    elif kind == "hook":
        viz.vertical_hook(cfg, args.out, top_number=args.top_number, drop_number=args.drop_number)
    else:
        raise SystemExit(f"Unknown video kind: {kind}")
    print("saved", args.out)


def cmd_all(args):
    cfg, league = _load(args)
    player = _pkey(league, args.player)
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
  # 1) get the data: open the Google Sheet -> File -> Download -> Excel (.xlsx)
  # 2) run any command below, pointing --input at that file

  python -m ncbl standings --input sheet.xlsx --top 20
  python -m ncbl predict   --input sheet.xlsx --player espiiii --config config.json
  python -m ncbl report    --input sheet.xlsx --player espiiii --outdir out/
  python -m ncbl threats   --input sheet.xlsx --player espiiii
  python -m ncbl video follow --input sheet.xlsx --player espiiii --out out/climb.mp4
  python -m ncbl all       --input sheet.xlsx --player espiiii --outdir out/
  python -m ncbl coach     --reports Downloads/ --player espiiii --outdir out/
  python -m ncbl challonge --player espiiii --from-sheet sheet.xlsx --api-key KEY --outdir out/

--input accepts: the whole workbook .xlsx, a Data-Entry .csv, or a folder of the CSVs.
Copy config.example.json -> config.json to set the season tabs, schedule, and invite lists.
"""


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="ncbl", description="NCBL ranking prediction + video pipeline",
        epilog=_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", metavar="{standings,predict,report,coach,challonge,threats,video,all}")

    def common(p):
        p.add_argument("--input", required=True, metavar="PATH",
                       help="league sheet: .xlsx workbook, a Data-Entry .csv, or a folder of CSVs")
        p.add_argument("--config", metavar="FILE", help="JSON config overriding ncbl/config.py defaults")

    p = sub.add_parser("standings", help="print the current standings")
    common(p); p.add_argument("--top", type=int, metavar="N", help="show top N (default 25)")
    p.set_defaults(func=cmd_standings)

    p = sub.add_parser("predict", help="probabilities of reaching a target rank for a player")
    common(p)
    p.add_argument("--player", required=True, help="player name (any casing)")
    p.add_argument("--target", type=int, metavar="RANK", help="target rank (default from config)")
    p.add_argument("--remaining", type=int, metavar="N", help="events the player will still play (default: open slots)")
    p.set_defaults(func=cmd_predict)

    p = sub.add_parser("report", help="write a .txt + .json + .html report for a player")
    common(p)
    p.add_argument("--player", required=True, help="player name (any casing)")
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
    common(p); p.add_argument("--player", required=True); p.add_argument("--window", type=int, default=6, metavar="N")
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
    common(p); p.add_argument("--player", required=True); p.add_argument("--outdir", default="out", metavar="DIR")
    p.add_argument("--target", type=int, metavar="RANK"); p.add_argument("--remaining", type=int, metavar="N")
    p.set_defaults(func=cmd_all)

    p = sub.add_parser("coach", help="analyze NCBLAST match-report PDFs -> coaching report + visual")
    p.add_argument("--reports", required=True, nargs="+", metavar="PATH",
                   help="a folder of NCBLAST report PDFs, or one/more PDF paths (more = higher confidence)")
    p.add_argument("--player", help="player to coach (any casing); reports list their player")
    p.add_argument("--config", metavar="FILE", help="optional JSON config (theme, etc.)")
    p.add_argument("--outdir", default="out", metavar="DIR", help="output folder (default: out/)")
    p.add_argument("--season", metavar="NAME", help="scope to a season window (default: lifetime = all reports)")
    p.set_defaults(func=cmd_coach)

    p = sub.add_parser("challonge", help="head-to-head records from Challonge brackets (needs a free API key)")
    p.add_argument("--player", required=True, help="player to build head-to-head for")
    p.add_argument("--slugs", nargs="+", metavar="ID", help="Challonge tournament ids, e.g. ncbl-goonday")
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
