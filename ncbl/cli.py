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


def _load(args):
    cfg = load_config(args.config)
    league = League(cfg).load(args.input)
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
    print("standings racer..."); viz.follow(league, cfg, player, j("climb.mp4"), published_end=True)
    print("overview...");        viz.overview(league, cfg, player, j("overview.mp4"))
    print("monte carlo...");     viz.montecarlo(league, cfg, player, j("montecarlo.mp4"))
    print("map...");             viz.regions_map(cfg, j("map.png"))
    cur = S.rank_of(league, player)
    print("hook...");            viz.vertical_hook(cfg, j("hook.mp4"), top_number=cur, drop_number=cur)
    print("\nAll assets written to", od)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ncbl", description="NCBL ranking prediction + video pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--input", required=True, help="Path to the downloaded .xlsx (or a Data-Entry .csv / folder of CSVs)")
        p.add_argument("--config", help="Optional JSON config overriding defaults")

    p = sub.add_parser("standings"); common(p); p.add_argument("--top", type=int); p.set_defaults(func=cmd_standings)
    p = sub.add_parser("predict"); common(p)
    p.add_argument("--player", required=True); p.add_argument("--target", type=int); p.add_argument("--remaining", type=int)
    p.set_defaults(func=cmd_predict)
    p = sub.add_parser("threats"); common(p); p.add_argument("--player", required=True); p.add_argument("--window", type=int, default=6); p.set_defaults(func=cmd_threats)
    p = sub.add_parser("video"); common(p)
    p.add_argument("kind", choices=["follow", "drop", "climb", "overview", "montecarlo", "map", "hook"])
    p.add_argument("--player", default=""); p.add_argument("--out", required=True)
    p.add_argument("--t-from", dest="t_from", type=int); p.add_argument("--t-to", dest="t_to", type=int)
    p.add_argument("--published-end", action="store_true", help="Anchor final frame to the published ranking")
    p.add_argument("--title"); p.add_argument("--top-number", type=int, default=14); p.add_argument("--drop-number", type=int, default=21)
    p.set_defaults(func=cmd_video)
    p = sub.add_parser("all"); common(p); p.add_argument("--player", required=True); p.add_argument("--outdir", default="out"); p.set_defaults(func=cmd_all)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
