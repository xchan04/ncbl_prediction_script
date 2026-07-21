"""Build a unified prediction report and render it to .txt, .json, and .html.

    from ncbl import report
    data = report.build(league, cfg, "espiiii")
    report.write_all(data, cfg, "out/report")   # -> report.txt / .json / .html
"""
from __future__ import annotations
import html
import json

from . import standings as S
from . import simulate as SIM


def build(league, cfg, player_lc, target_rank=None, remaining=None, window=6, top=25):
    """Assemble the full JSON-serializable report for one player."""
    rep = SIM.predict_report(league, cfg, player_lc, target_rank=target_rank, remaining=remaining)
    thr = SIM.threats(league, cfg, player_lc, window=window)
    rows = S.standings(league, include=player_lc)
    stage = int(cfg.get("open_spots", 0) or 0)
    return {
        "player": rep["player"],
        "current_rank": rep["current_rank"],
        "current_score": rep["current_score"],
        "n_events": rep["n_events"],
        "slots_left": rep["slots_left"],
        "target_rank": rep["target_rank"],
        "cutoff": rep["cutoff"],
        "open_spots": stage,
        "field_size": len(rows),
        "window": window,
        "predictions": [
            {"strategy": label, "total": r["score"], "p_top": r["p_top"],
             "p_stage": r["p_stage"], "median_rank": r["median_rank"]}
            for label, r in rep["lines"]
        ],
        "threats": {
            "overtook": [{"player": league.name(p), "from_rank": a, "to_rank": b, "score": round(s, 3)}
                         for p, a, b, s in thr["overtook"]],
            "live": [{"player": league.name(p), "rank": b, "score": round(s, 3), "slots_left": sl}
                     for p, b, sl, s in thr["live"]],
        },
        "standings": [{"rank": i, "player": league.name(p), "score": round(s, 3),
                       "events": league.n_events(_key(league, p))}
                      for i, (p, s) in enumerate(rows[:top], 1)],
    }


def _key(league, name):
    return name if name in league.by_player else name.lower()


# ---------------------------------------------------------------- TXT
def to_txt(d):
    L = []
    L.append(f"{d['player']}: #{d['current_rank']}  {d['current_score']} pts "
             f"({d['n_events']} events, {d['slots_left']} slots left)")
    L.append(f"Target: Top {d['target_rank']}   cutoff now = {d['cutoff']}   "
             f"field = {d['field_size']} ranked players")
    L.append("")
    stage = d["open_spots"]
    hdr = f"  {'strategy':24}{'total':>8}{'P(Top%d)':>10}" % d["target_rank"]
    if stage:
        hdr += f"{'P(spot)':>10}"
    hdr += f"{'median':>9}"
    L.append(hdr)
    for r in d["predictions"]:
        line = f"  {r['strategy']:24}{r['total']:8.2f}{r['p_top']*100:9.1f}%"
        if stage:
            line += f"{(r['p_stage'] or 0)*100:9.1f}%"
        line += f"   #{r['median_rank']}"
        L.append(line)
    L.append("")
    L.append(f"=== Overtook {d['player']} (last {d['window']} events) ===")
    for o in d["threats"]["overtook"]:
        L.append(f"  {o['player']:18} #{o['from_rank']} -> #{o['to_rank']}   {o['score']:.2f}")
    L.append("")
    L.append("=== Live threats (below, still have slots) ===")
    for t in d["threats"]["live"]:
        L.append(f"  {t['player']:18} #{t['rank']}  {t['score']:.2f}  ({t['slots_left']} slots left)")
    L.append("")
    L.append(f"=== Standings (top {len(d['standings'])}) ===")
    for s in d["standings"]:
        mark = "  <= " + d["player"] if s["player"] == d["player"] else ""
        L.append(f"  {s['rank']:>3}  {s['player']:20}{s['score']:8.3f}  {s['events']} ev{mark}")
    return "\n".join(L) + "\n"


def to_json(d):
    return json.dumps(d, indent=2)


# ---------------------------------------------------------------- HTML
def to_html(d, cfg):
    th = cfg.get("theme", {})
    bg = th.get("bg", "#000000"); fg = th.get("fg", "#e6edf3")
    orange = th.get("player", "#ff8c1a"); green = "#57e26b"; red = th.get("cutoff", "#ff5555")
    muted = th.get("muted", "#6b7280"); panel = "#0d0d0d"; border = "#241a0e"
    e = html.escape
    stage = d["open_spots"]

    def pct_color(p):
        return green if p >= 0.66 else (orange if p >= 0.33 else red)

    pred_rows = ""
    for r in d["predictions"]:
        spot = f'<td style="text-align:right;color:{pct_color(r["p_stage"] or 0)}">{(r["p_stage"] or 0)*100:.1f}%</td>' if stage else ""
        pred_rows += (
            f'<tr><td>{e(r["strategy"])}</td>'
            f'<td style="text-align:right">{r["total"]:.2f}</td>'
            f'<td style="text-align:right;color:{pct_color(r["p_top"])};font-weight:700">{r["p_top"]*100:.1f}%</td>'
            f'{spot}<td style="text-align:right;color:{muted}">#{r["median_rank"]}</td></tr>'
        )
    spot_h = f'<th style="text-align:right">P(spot)</th>' if stage else ""

    over_rows = "".join(
        f'<tr><td>{e(o["player"])}</td>'
        f'<td style="text-align:right;color:{muted}">#{o["from_rank"]}</td>'
        f'<td style="text-align:center;color:{green}">&#8594; #{o["to_rank"]}</td>'
        f'<td style="text-align:right">{o["score"]:.2f}</td></tr>'
        for o in d["threats"]["overtook"]) or f'<tr><td colspan="4" style="color:{muted}">none</td></tr>'

    live_rows = "".join(
        f'<tr><td>{e(t["player"])}</td>'
        f'<td style="text-align:right;color:{muted}">#{t["rank"]}</td>'
        f'<td style="text-align:right">{t["score"]:.2f}</td>'
        f'<td style="text-align:right;color:{orange}">{t["slots_left"]} left</td></tr>'
        for t in d["threats"]["live"]) or f'<tr><td colspan="4" style="color:{muted}">none</td></tr>'

    stand_rows = ""
    for s in d["standings"]:
        me = s["player"] == d["player"]
        rowstyle = f'background:{orange};color:#000;font-weight:700' if me else ""
        cut = f'border-bottom:2px dashed {red}' if s["rank"] == d["target_rank"] else ""
        stand_rows += (
            f'<tr style="{rowstyle};{cut}"><td style="text-align:right">{s["rank"]}</td>'
            f'<td>{e(s["player"])}</td><td style="text-align:right">{s["score"]:.3f}</td>'
            f'<td style="text-align:right">{s["events"]}</td></tr>'
        )

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{e(d['player'])} — NCBL report</title>
<style>
  body{{background:{bg};color:{fg};font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;padding:32px}}
  .wrap{{max-width:820px;margin:0 auto}}
  h1{{color:{orange};font-size:34px;margin:0 0 4px}}
  h2{{color:{orange};font-size:18px;border-bottom:1px solid {border};padding-bottom:6px;margin:34px 0 12px}}
  .sub{{color:{muted};font-size:15px;margin-bottom:6px}}
  .card{{background:{panel};border:1px solid {border};border-radius:12px;padding:18px 20px}}
  .big{{font-size:20px}} .big b{{color:{orange}}}
  table{{width:100%;border-collapse:collapse;font-size:14px}}
  th,td{{padding:7px 10px;border-bottom:1px solid {border}}}
  th{{color:{muted};text-align:left;font-weight:600}}
  .cols{{display:flex;gap:24px;flex-wrap:wrap}} .cols>div{{flex:1;min-width:300px}}
  .foot{{color:{muted};font-size:12px;margin-top:30px}}
</style></head><body><div class="wrap">
  <h1>{e(d['player'])}</h1>
  <div class="card big">Currently <b>#{d['current_rank']}</b> &nbsp;·&nbsp; <b>{d['current_score']}</b> pts
    &nbsp;·&nbsp; {d['n_events']} events, <b>{d['slots_left']}</b> slots left<br>
    <span class="sub">Target: Top {d['target_rank']} &nbsp;·&nbsp; cutoff now = {d['cutoff']}
    &nbsp;·&nbsp; field = {d['field_size']} ranked players{f" &nbsp;·&nbsp; {stage} open invitational spots" if stage else ""}</span>
  </div>

  <h2>What it takes</h2>
  <table><thead><tr><th>Strategy</th><th style="text-align:right">Total</th>
    <th style="text-align:right">P(Top {d['target_rank']})</th>{spot_h}
    <th style="text-align:right">Median</th></tr></thead><tbody>{pred_rows}</tbody></table>

  <h2>Threats</h2>
  <div class="cols">
    <div><div class="sub">Overtook me (last {d['window']} events)</div>
      <table><tbody>{over_rows}</tbody></table></div>
    <div><div class="sub">Can still catch me</div>
      <table><tbody>{live_rows}</tbody></table></div>
  </div>

  <h2>Standings — top {len(d['standings'])}</h2>
  <table><thead><tr><th style="text-align:right">#</th><th>Player</th>
    <th style="text-align:right">Pts</th><th style="text-align:right">Events</th></tr></thead>
    <tbody>{stand_rows}</tbody></table>
  <div class="foot">NCBL prediction pipeline · best {cfg['best_of']} of first {cfg['of_first']} ·
    Monte-Carlo {cfg['monte_carlo']['trials']:,} trials · orange row = you · dashed line = Top-{d['target_rank']} cutoff</div>
</div></body></html>"""


def write_all(d, cfg, basepath):
    """Write basepath.txt / .json / .html and return the list of paths."""
    paths = []
    for ext, text in (("txt", to_txt(d)), ("json", to_json(d)), ("html", to_html(d, cfg))):
        p = f"{basepath}.{ext}"
        with open(p, "w") as fh:
            fh.write(text)
        paths.append(p)
    return paths
