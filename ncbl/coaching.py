"""Aggregate N NCBLAST reports for a player and derive data-driven coaching.

No AI at runtime — every finding is computed from the parsed report numbers and
carries its evidence + a confidence that grows with the amount of data fed in.
"""
from __future__ import annotations
import glob
import html
import json
import os
from collections import defaultdict

from . import ncblast_parser as NP

MIN_COMBO_BATTLES = 5      # a combo needs this many battles to be judged
MIN_MATCHUP_FACED = 3      # aggregated encounters before a matchup finding counts
MIN_SWAP_FACED = 2         # encounters for a combo to be a recommended answer


# ---------------- loading ----------------
def load_reports(paths):
    """Accept a file, a list of files, or a folder; parse each PDF; dedupe by (player,event)."""
    files = []
    if isinstance(paths, str):
        paths = [paths]
    for p in paths:
        if os.path.isdir(p):
            files += sorted(glob.glob(os.path.join(p, "*.pdf")))
        else:
            files.append(p)
    reports, seen = [], set()
    for f in files:
        try:
            r = NP.parse(f)
        except Exception:
            continue
        key = (str(r.get("player")).lower(), str(r.get("event")).lower())
        if key in seen or not r.get("combos"):
            continue
        seen.add(key)
        reports.append(r)
    return reports


def _players(reports):
    return sorted({str(r["player"]).lower() for r in reports if r.get("player")})


def filter_by_season(reports, season, seasons_cfg):
    """Keep only reports whose date falls in the named season's window (lifetime if season is None)."""
    if not season or not seasons_cfg or season not in seasons_cfg:
        return reports
    lo, hi = seasons_cfg[season]
    kept = []
    for r in reports:
        d = _date_iso(r.get("date"))
        if d is None or lo <= d <= hi:
            kept.append(r)
    return kept


def _date_iso(date_str):
    """'June 28, 2026' -> '2026-06-28' (best-effort)."""
    if not date_str:
        return None
    import datetime
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _resolve(reports, name):
    lc = name.strip().lower()
    for r in reports:
        if str(r.get("player", "")).lower() == lc:
            return str(r["player"]).lower()
    return lc


# ---------------- aggregation ----------------
def _wmerge(rows):
    """Battle-weighted combo merge: rows are (win_pct, ppb, battles)."""
    b = sum(x[2] for x in rows) or 1
    return {"battles": sum(x[2] for x in rows),
            "win_pct": round(sum(x[0] * x[2] for x in rows) / b, 1),
            "ppb": round(sum(x[1] * x[2] for x in rows) / b, 3)}


def aggregate(reports, player):
    mine = [r for r in reports if str(r.get("player", "")).lower() == player]
    events = [r.get("event") for r in mine]

    combos = defaultdict(list)          # name -> [(win,ppb,btl)]
    combo_tiers = defaultdict(list)
    combo_trend = defaultdict(list)     # name -> [(event_idx, win_pct)]
    for i, r in enumerate(mine):
        for c in r["combos"]:
            combos[c["combo"]].append((c["win_pct"], c["ppb"], c["battles"]))
            if c.get("tier"):
                combo_tiers[c["combo"]].append(c["tier"])
            combo_trend[c["combo"]].append((i, c["win_pct"]))
    combo_stats = {name: {**_wmerge(rows), "tier": _best_tier(combo_tiers.get(name)),
                          "events": len(rows), "trend": _trend(combo_trend[name])}
                   for name, rows in combos.items()}

    # finishes (loss = vulnerability), summed counts
    loss_counts = defaultdict(int)
    for r in mine:
        for k, v in r["finishes"].get("loss", {}).items():
            loss_counts[k] += v.get("count", 0)
    total_loss = sum(loss_counts.values()) or 1
    loss_dist = {k: round(100 * v / total_loss, 1) for k, v in sorted(loss_counts.items(), key=lambda z: -z[1])}

    # matchups: per (your,opp) from the recurring table (has your-combo, for swaps);
    # per-opp record from the match recap when present (complete, per-battle), else recurring.
    # Using both sources for per-opp would double-count the same battles.
    per_pair = defaultdict(lambda: [0, 0])   # (your,opp) -> [w,l]
    per_opp = defaultdict(lambda: [0, 0])    # opp -> [w,l]
    for r in mine:
        for m in r["matchups"]:
            per_pair[(m["your_combo"], m["opp_combo"])][0] += m["wins"]
            per_pair[(m["your_combo"], m["opp_combo"])][1] += m["losses"]
        if r["matches"]:
            for mt in r["matches"]:
                for oc in mt["opp_combos"]:
                    w, l = _wl(oc["wl"])          # opponent's W-L vs you -> invert for your record
                    per_opp[oc["combo"]][0] += l
                    per_opp[oc["combo"]][1] += w
        else:
            for m in r["matchups"]:
                per_opp[m["opp_combo"]][0] += m["wins"]
                per_opp[m["opp_combo"]][1] += m["losses"]

    # peer gaps: your win% vs best peer on same combo
    peer_gap = {}
    for r in mine:
        by_combo = defaultdict(list)
        for p in r["peers"]:
            if p.get("combo"):
                by_combo[p["combo"]].append(p)
        for combo, rows in by_combo.items():
            you = next((x for x in rows if x["player"] == "YOU"), None)
            peers = [x for x in rows if x["player"] != "YOU"]
            if you and peers:
                best = max(peers, key=lambda x: x["win_pct"])
                peer_gap[combo] = {"you": you["win_pct"], "best_peer": best["player"],
                                   "best_win": best["win_pct"], "gap": round(you["win_pct"] - best["win_pct"], 1)}

    # style averaged
    style = defaultdict(list)
    for r in mine:
        for k, v in r.get("style", {}).items():
            style[k].append(v)
    style_avg = {k: round(sum(v) / len(v)) for k, v in style.items()}

    # opponents (players) record
    opp_players = defaultdict(lambda: [0, 0])
    for r in mine:
        for mt in r["matches"]:
            (opp_players[mt["opponent"]])[0 if mt["result"] == "WIN" else 1] += 1

    total_battles = sum(c["battles"] for c in combo_stats.values())
    return {
        "player": mine[0]["player"] if mine else player,
        "events": events, "n_events": len(mine), "total_battles": total_battles,
        "combos": combo_stats, "loss_finishes": loss_dist,
        "matchups_pair": {k: v for k, v in per_pair.items()},
        "matchups_opp": {k: v for k, v in per_opp.items()},
        "peer_gap": peer_gap, "style": style_avg,
        "opp_players": {k: v for k, v in opp_players.items()},
        "archetypes": [r.get("archetype") for r in mine if r.get("archetype")],
    }


def _wl(s):
    a, b = s.replace("–", "-").split("-")[:2]
    return int(a), int(b)


def _best_tier(tiers):
    if not tiers:
        return None
    order = "SABCD"
    return sorted(tiers, key=lambda t: order.index(t) if t in order else 9)[0]


def _trend(points):
    if len(points) < 2:
        return None
    pts = [w for _, w in sorted(points)]
    d = pts[-1] - pts[0]
    return "up" if d > 5 else "down" if d < -5 else "flat"


# ---------------- meta (any players) ----------------
def build_meta(reports):
    freq = defaultdict(int)
    for r in reports:
        for m in r["matchups"]:
            freq[m["opp_combo"]] += m["faced"]
        for mt in r["matches"]:
            for oc in mt["opp_combos"]:
                freq[oc["combo"]] += 1
    return dict(sorted(freq.items(), key=lambda z: -z[1]))


# ---------------- confidence ----------------
def confidence(agg):
    e, b = agg["n_events"], agg["total_battles"]
    if e >= 4 or b >= 150:
        tier = "Gold"
    elif e >= 2 or b >= 60:
        tier = "Silver"
    else:
        tier = "Bronze"
    unlocked = {"cross_event_trends": e >= 2, "widened_meta": e >= 2}
    return {"tier": tier, "events": e, "battles": b, "unlocked": unlocked}


def _conf(n, hi, mid):
    return "confirmed" if n >= hi else ("likely" if n >= mid else "tentative")


# ---------------- analysis ----------------
def coach(reports, player, scope="lifetime"):
    player = _resolve(reports, player)
    agg = aggregate(reports, player)
    meta = build_meta(reports)
    conf = confidence(agg)
    weaknesses, strengths, swaps, meta_notes = [], [], [], []

    # rivals: your head-to-head vs each opponent PLAYER (from match recaps), nemeses first
    rivals = []
    for opp, (w, l) in agg["opp_players"].items():
        rivals.append({"player": opp, "wins": w, "losses": l, "played": w + l,
                       "win_pct": round(100 * w / max(1, w + l), 1)})
    rivals.sort(key=lambda r: (r["wins"] - r["losses"], -r["played"]))

    # combo strengths / liabilities
    for name, c in sorted(agg["combos"].items(), key=lambda z: z[1]["ppb"]):
        if c["battles"] < MIN_COMBO_BATTLES:
            continue
        if c["ppb"] < 0 or c["win_pct"] < 45:
            weaknesses.append({"type": "combo", "severity": "high" if c["ppb"] <= -1 else "med",
                               "confidence": _conf(c["battles"], 15, MIN_COMBO_BATTLES),
                               "text": f"{name} is underwater ({c['win_pct']}% win, {c['ppb']:+} PPB over {c['battles']} btl)",
                               "suggestion": f"Bench or rebuild {name} — it is costing you points."})
        if (c["tier"] in ("S", "A")) or c["ppb"] >= 0.4:
            strengths.append({"type": "combo", "confidence": _conf(c["battles"], 15, MIN_COMBO_BATTLES),
                              "text": f"{name}: {c['win_pct']}% win, {c['ppb']:+} PPB, tier {c['tier'] or '?'} ({c['battles']} btl)"
                                      + (f" · trend {c['trend']}" if c['trend'] else ""),
                              "suggestion": f"Lean on {name} — it is your engine."})

    # finish vulnerability
    if agg["loss_finishes"]:
        top, pct = next(iter(agg["loss_finishes"].items()))
        weaknesses.append({"type": "finish", "severity": "high" if pct >= 40 else "med",
                           "confidence": "confirmed" if conf["battles"] >= 60 else "likely",
                           "text": f"{pct}% of finishes scored on you are {top}",
                           "suggestion": _finish_advice(top)})
        selfko = agg["loss_finishes"].get("Own (self-KO)", 0)
        if selfko >= 8:
            weaknesses.append({"type": "finish", "severity": "med", "confidence": "likely",
                               "text": f"{selfko}% of your losses are self-KOs",
                               "suggestion": "Tighten launches / bit choice — you are giving away free points."})

    # opponent-combo matchup holes + swaps (cap holes so the report stays focused)
    holes = 0
    for opp, (w, l) in sorted(agg["matchups_opp"].items(), key=lambda z: z[1][1] - z[1][0], reverse=True):
        faced = w + l
        if faced < MIN_MATCHUP_FACED or l <= w:
            continue
        answers = [(yc, ov[0], ov[1]) for (yc, oc), ov in agg["matchups_pair"].items()
                   if oc == opp and (ov[0] + ov[1]) >= MIN_SWAP_FACED and ov[0] > ov[1]]
        answers.sort(key=lambda x: (x[1] - x[2]), reverse=True)
        if answers:
            best = answers[0]
            swaps.append({"opp": opp, "record": f"{w}-{l}", "confidence": _conf(faced, 6, MIN_MATCHUP_FACED),
                          "text": f"You are {w}-{l} vs {opp}",
                          "suggestion": f"Bring {best[0]} into this matchup (it is {best[1]}-{best[2]} vs {opp})."})
        elif holes < 5:
            holes += 1
            weaknesses.append({"type": "matchup", "severity": "high", "confidence": _conf(faced, 6, MIN_MATCHUP_FACED),
                               "text": f"You are {w}-{l} vs {opp} with no winning answer in your deck",
                               "suggestion": f"You need a dedicated counter to {opp}."})

    # peer gaps (where the field gets more from your combo)
    for combo, g in agg["peer_gap"].items():
        if g["gap"] <= -10 and combo in agg["combos"] and agg["combos"][combo]["battles"] >= MIN_COMBO_BATTLES:
            weaknesses.append({"type": "peer", "severity": "med", "confidence": "likely",
                               "text": f"On {combo} you win {g['you']}% but {g['best_peer']} gets {g['best_win']}% ({g['gap']} gap)",
                               "suggestion": f"Study how {g['best_peer']} pilots {combo} — same combo, better results."})

    # style axes
    for axis, val in agg["style"].items():
        if val <= 35:
            weaknesses.append({"type": "style", "severity": "low", "confidence": "likely",
                               "text": f"Low {axis} ({val} pct-rank)",
                               "suggestion": _style_advice(axis)})

    # meta: most common field combos you must beat
    for opp, freq in list(meta.items())[:6]:
        rec = agg["matchups_opp"].get(opp)
        note = f" — you are {rec[0]}-{rec[1]} vs it" if rec else " — no data on your record"
        meta_notes.append({"combo": opp, "seen": freq, "text": f"{opp} (seen {freq}x){note}"})

    return {"player": agg["player"], "scope": scope, "events": agg["events"], "n_events": agg["n_events"],
            "confidence": conf, "style": agg["style"], "archetype": (agg["archetypes"] or [None])[0],
            "combos": agg["combos"], "loss_finishes": agg["loss_finishes"],
            "weaknesses": weaknesses, "strengths": strengths, "swaps": swaps, "meta": meta_notes,
            "rivals": rivals,
            "matchups_opp": {f"{k}": v for k, v in agg["matchups_opp"].items()},
            "opp_players": agg["opp_players"]}


def _finish_advice(t):
    return {"Opp Xtreme": "You leak Xtreme/KO finishes — add a heavier/defensive bit or a stamina answer.",
            "Opp Over": "You get Over-finished — lower your center of gravity / pick a more stable ratchet.",
            "Opp Spin": "You lose spin-finishes — you need more stamina in the deck.",
            "Opp Burst": "You are getting burst — check tightness / go burst-resistant."}.get(t, f"Address {t} losses.")


def _style_advice(axis):
    return {"Aggression": "Your wins are mostly non-attack — a KO combo would add a closing threat.",
            "Adaptability": "Inconsistent across sides/positions — practice your weak side.",
            "Clutch": "You fade when behind — work on come-from-behind lines.",
            "Efficiency": "Low net points per battle — trim negative-PPB combos.",
            "Deck Usage": "Deck leans on one combo — deepen your other two."}.get(axis, f"Improve {axis}.")


# ---------------- rendering ----------------
def _next_tier(conf):
    if conf["tier"] == "Bronze":
        return "reach 2 events (or 60 battles) for **Silver** — unlocks cross-event trends"
    if conf["tier"] == "Silver":
        return "reach 4 events (or 150 battles) for **Gold** — highest-confidence findings"
    return "you're at the highest confidence tier"


def coach_txt(d):
    c = d["confidence"]
    L = [f"{d['player']} — coaching report  [scope: {d.get('scope','lifetime')}]",
         f"{d['n_events']} event(s) · {c['battles']} battles · confidence: {c['tier']}",
         f"archetype: {d.get('archetype')}  style: {d.get('style')}",
         f"(feed more reports: {_next_tier(c).replace('**','')})", ""]
    L.append("STRENGTHS")
    for s in d["strengths"]:
        L.append(f"  + {s['text']}")
    L.append("\nWEAKNESSES")
    for w in d["weaknesses"]:
        L.append(f"  - [{w['severity']}/{w['confidence']}] {w['text']}")
        L.append(f"      -> {w['suggestion']}")
    L.append("\nMATCHUP SWAPS")
    for s in d["swaps"]:
        L.append(f"  vs {s['opp']} (you {s['record']}) -> {s['suggestion']}")
    if not d["swaps"]:
        L.append("  (none — no clean in-deck answer found for your losing matchups)")
    L.append(f"\nRIVALS — your head-to-head ({d.get('scope','lifetime')})")
    for r in d.get("rivals", []):
        tag = "  <-- nemesis" if r["losses"] > r["wins"] and r["played"] >= 2 else ""
        L.append(f"  {r['player']:20} {r['wins']}-{r['losses']}  ({r['win_pct']}%, {r['played']} sets){tag}")
    if not d.get("rivals"):
        L.append("  (no match-recap data in these reports)")
    L.append("\nMETA — field you keep facing")
    for m in d["meta"]:
        L.append(f"  * {m['text']}")
    return "\n".join(L) + "\n"


def coach_json(d):
    return json.dumps(d, indent=2, default=str)


def coach_html(d, cfg, image_path=None):
    th = cfg.get("theme", {})
    bg, fg, orange = th.get("bg", "#000"), th.get("fg", "#e6edf3"), th.get("player", "#ff8c1a")
    green, red, muted, panel, border = "#57e26b", th.get("cutoff", "#ff5555"), th.get("muted", "#6b7280"), "#0d0d0d", "#241a0e"
    e = html.escape
    sev = {"high": red, "med": orange, "low": muted}

    def block(title, items, render):
        return f'<h2>{title}</h2>' + ("".join(render(x) for x in items) if items else f'<div class="sub">none</div>')

    strengths = block("What's working", d["strengths"],
                      lambda s: f'<div class="row"><span class="dot" style="background:{green}"></span>{e(s["text"])}</div>')
    weaknesses = block("Weaknesses", d["weaknesses"], lambda w:
                       f'<div class="row"><span class="dot" style="background:{sev.get(w["severity"],muted)}"></span>'
                       f'<b>{e(w["text"])}</b><span class="tag">{w["confidence"]}</span>'
                       f'<div class="sug">{e(w["suggestion"])}</div></div>')
    swaps = block("Matchup swaps", d["swaps"], lambda s:
                  f'<div class="row"><span class="dot" style="background:{orange}"></span>'
                  f'<b>vs {e(s["opp"])}</b> — you {e(s["record"])}<div class="sug">{e(s["suggestion"])}</div></div>')
    meta = block("Meta — field you keep facing", d["meta"],
                 lambda m: f'<div class="row"><span class="dot" style="background:{muted}"></span>{e(m["text"])}</div>')

    # rivals (head-to-head vs players), nemeses highlighted
    rival_rows = "".join(
        f'<tr><td>{e(r["player"])}</td>'
        f'<td style="text-align:right;color:{green if r["wins"]>=r["losses"] else red}">{r["wins"]}-{r["losses"]}</td>'
        f'<td style="text-align:right">{r["win_pct"]}%</td>'
        f'<td style="text-align:right;color:{muted}">{r["played"]}</td></tr>'
        for r in d.get("rivals", [])) or f'<tr><td colspan="4" style="color:{muted}">no match-recap data</td></tr>'

    combos = "".join(
        f'<tr><td>{e(n)}</td><td style="text-align:center">{e(str(c.get("tier") or "?"))}</td>'
        f'<td style="text-align:right">{c["win_pct"]}%</td><td style="text-align:right">{c["ppb"]:+}</td>'
        f'<td style="text-align:right">{c["battles"]}</td><td style="text-align:center;color:{muted}">{c.get("trend") or ""}</td></tr>'
        for n, c in sorted(d["combos"].items(), key=lambda z: -z[1]["ppb"]))
    c = d["confidence"]
    scope = d.get("scope", "lifetime")

    # embed the matchup visual inline (base64) if a PNG was rendered
    img_html = ""
    if image_path and os.path.exists(image_path):
        import base64
        with open(image_path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
        img_html = (f'<h2>Matchup profile</h2>'
                    f'<img alt="matchup chart" style="width:100%;border:1px solid {border};border-radius:10px" '
                    f'src="data:image/png;base64,{b64}">')

    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{e(d['player'])} — coaching</title>
<style>
 body{{background:{bg};color:{fg};font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;padding:32px}}
 .wrap{{max-width:840px;margin:0 auto}} h1{{color:{orange};font-size:32px;margin:0}}
 h2{{color:{orange};font-size:18px;border-bottom:1px solid {border};padding-bottom:6px;margin:30px 0 12px}}
 .sub{{color:{muted};font-size:14px}} .card{{background:{panel};border:1px solid {border};border-radius:12px;padding:16px 20px;margin-top:10px}}
 .row{{padding:8px 0;border-bottom:1px solid {border};font-size:15px}} .dot{{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:9px}}
 .sug{{color:{muted};font-size:13px;margin:3px 0 0 18px}} .tag{{color:{muted};font-size:11px;border:1px solid {border};border-radius:8px;padding:1px 6px;margin-left:8px}}
 table{{width:100%;border-collapse:collapse;font-size:14px}} th,td{{padding:7px 10px;border-bottom:1px solid {border}}} th{{color:{muted};text-align:left}}
 .nudge{{color:{orange};font-size:13px;margin-top:8px}} .pill{{color:{orange};border:1px solid {orange};border-radius:8px;padding:1px 8px;font-size:12px}}
</style></head><body><div class="wrap">
 <h1>{e(d['player'])} <span class="pill">{e(scope)}</span></h1>
 <div class="card"><span class="big">{d['n_events']} event(s) · {c['battles']} battles · confidence
   <b style="color:{orange}">{c['tier']}</b></span><br>
   <span class="sub">archetype: {e(str(d.get('archetype')))} · style {e(str(d.get('style')))}</span>
   <div class="nudge">▲ Feed more reports: {_next_tier(c).replace('**','')}</div></div>
 {strengths}{weaknesses}{swaps}
 <h2>Rivals — head-to-head ({e(scope)})</h2>
 <table><thead><tr><th>Opponent</th><th style="text-align:right">Record</th><th style="text-align:right">Win%</th><th style="text-align:right">Sets</th></tr></thead>
   <tbody>{rival_rows}</tbody></table>
 {meta}
 {img_html}
 <h2>Your combos (all events)</h2>
 <table><thead><tr><th>Combo</th><th style="text-align:center">Tier</th><th style="text-align:right">Win%</th>
   <th style="text-align:right">PPB</th><th style="text-align:right">Btl</th><th style="text-align:center">Trend</th></tr></thead>
   <tbody>{combos}</tbody></table>
 <div class="sub" style="margin-top:24px">NCBLAST coaching · data-driven, no AI · more reports = more confidence</div>
</div></body></html>"""


def write_all(d, cfg, basepath, image_path=None):
    paths = []
    for ext, text in (("txt", coach_txt(d)), ("json", coach_json(d)),
                      ("html", coach_html(d, cfg, image_path))):
        p = f"{basepath}.{ext}"
        with open(p, "w") as fh:
            fh.write(text)
        paths.append(p)
    return paths
