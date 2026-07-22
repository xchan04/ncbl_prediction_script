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
from . import prediction as PRED

MIN_COMBO_BATTLES = 5      # a combo needs this many battles to be judged
MIN_MATCHUP_FACED = 3      # aggregated encounters before a matchup finding counts
MIN_SWAP_FACED = 2         # encounters for a combo to be a recommended answer
MIN_SIDE_BATTLES = 6       # battles on a side before its win% is trusted
SIDE_GAP = 8.0             # win% gap between sides that flags a positioning weakness
MIN_COMMUNITY_BTL = 4      # community battles vs a combo before its field win% is trusted
COMMUNITY_GAP = 15.0       # win% the field beats you by before a matchup is flagged as winnable


# ---------------- loading ----------------
def _parse_any(path):
    """Dispatch by extension: NCBLAST reports may arrive as PDF or (schema-agnostic) JSON."""
    if str(path).lower().endswith(".json"):
        from . import ncblast_json as NJ
        return NJ.parse(path)
    return NP.parse(path)


def load_reports(paths):
    """Accept a file, a list of files, or a folder; parse each PDF/JSON; dedupe by (player,event)."""
    files = []
    if isinstance(paths, str):
        paths = [paths]
    for p in paths:
        if os.path.isdir(p):
            files += sorted(glob.glob(os.path.join(p, "*.pdf")))
            files += sorted(glob.glob(os.path.join(p, "*.json")))
        else:
            files.append(p)
    reports, seen = [], set()
    for f in files:
        try:
            r = _parse_any(f)
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

    # B-side / X-side split, battle-weighted across events
    side_acc = {"B": [0, 0.0, 0.0], "X": [0, 0.0, 0.0]}   # [battles, win*btl, ppb*btl]
    for r in mine:
        for k, v in r.get("dynamics", {}).get("side", {}).items():
            if k in side_acc and v.get("battles"):
                side_acc[k][0] += v["battles"]
                side_acc[k][1] += v["win_pct"] * v["battles"]
                side_acc[k][2] += v["ppb"] * v["battles"]
    side = {}
    for k, (b, w, p) in side_acc.items():
        if b:
            side[k] = {"battles": b, "win_pct": round(w / b, 1), "ppb": round(p / b, 3)}

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
        "peer_gap": peer_gap, "style": style_avg, "side": side,
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


# ---------------- community benchmark (any players) ----------------
def community_benchmark(reports):
    """Pool every player's recurring matchups into a field-wide benchmark.

    Each report's matchup rows are that player's own combo vs an opponent combo,
    so summing across all reports gives the community's aggregate record. The
    benchmark gets sharper as more players' reports are fed (n_players grows).
    Returns per-opponent-combo and per-(your_combo, opponent_combo) field win%s.
    """
    by_opp = defaultdict(lambda: [0, 0, set()])           # opp -> [w, l, {players}]
    by_pair = defaultdict(lambda: [0, 0, set()])          # (your,opp) -> [w, l, {players}]
    for r in reports:
        p = str(r.get("player", "")).lower()
        for m in r["matchups"]:
            by_opp[m["opp_combo"]][0] += m["wins"]
            by_opp[m["opp_combo"]][1] += m["losses"]
            by_opp[m["opp_combo"]][2].add(p)
            key = (m["your_combo"], m["opp_combo"])
            by_pair[key][0] += m["wins"]
            by_pair[key][1] += m["losses"]
            by_pair[key][2].add(p)
    players = {str(r.get("player", "")).lower() for r in reports if r.get("player")}

    def fin(rec):
        w, l, ps = rec
        n = w + l
        return {"wins": w, "losses": l, "battles": n, "n_players": len(ps),
                "win_pct": round(100 * w / n, 1) if n else 0.0}
    return {"by_opp": {k: fin(v) for k, v in by_opp.items()},
            "by_pair": {k: fin(v) for k, v in by_pair.items()},
            "n_players": len(players)}


def _pair_leaders(comm, opp, exclude_combo=None, top=2):
    """The your-combos the field uses most successfully vs `opp` (field win% desc)."""
    rows = [(yc, s) for (yc, oc), s in comm["by_pair"].items()
            if oc == opp and s["battles"] >= MIN_COMMUNITY_BTL and yc != exclude_combo]
    rows.sort(key=lambda z: (-z[1]["win_pct"], -z[1]["battles"]))
    return rows[:top]


# ---------------- goal card + nemesis dossier ----------------
def goal_card(reports, player, agg, weaknesses, rec, benchmarks):
    """A crisp finish-line card: current form, trajectory, and the next concrete objectives.

    Everything is derived from the parsed reports — win% trend across events, the
    top fixable leaks, the recommended engine, and the most winnable field matchup.
    """
    mine = [r for r in reports if str(r.get("player", "")).lower() == player]
    seq = [r["totals"].get("win_pct") for r in mine if r.get("totals", {}).get("win_pct") is not None]
    placements = [r["totals"].get("placement") for r in mine if r.get("totals", {}).get("placement")]
    overall = _wmerge([(c["win_pct"], c["ppb"], c["battles"]) for c in agg["combos"].values()]) if agg["combos"] else {"win_pct": 0, "ppb": 0, "battles": 0}
    trend = None
    if len(seq) >= 2:
        d = seq[-1] - seq[0]
        trend = "improving" if d > 3 else "declining" if d < -3 else "steady"

    objectives = []
    hi = [w for w in weaknesses if w.get("severity") == "high"]
    for w in hi[:2]:
        objectives.append(w["suggestion"])
    if benchmarks:
        b = benchmarks[0]
        objectives.append(f"Convert the {b['opp']} matchup (you {b['you_pct']}% vs field {b['field_pct']}%).")
    if rec.get("deck"):
        objectives.append("Run the recommended deck: " + ", ".join(x["combo"] for x in rec["deck"]) + ".")
    if not objectives:
        objectives.append("Keep feeding reports — more data sharpens every recommendation.")
    return {"win_pct": overall["win_pct"], "ppb": overall["ppb"], "battles": overall["battles"],
            "events": len(mine), "placements": placements, "win_seq": seq, "trend": trend,
            "objectives": objectives[:3]}


def nemesis_dossier(reports, player, rivals):
    """For each nemesis (a player you're sub-.500 against over >=2 sets), the combos they
    beat you with and your record vs each — a focused scouting card built from match recaps."""
    mine = [r for r in reports if str(r.get("player", "")).lower() == player]
    by_player = defaultdict(lambda: defaultdict(lambda: [0, 0]))   # opp_player -> combo -> [your_w, your_l]
    for r in mine:
        for mt in r["matches"]:
            for oc in mt["opp_combos"]:
                w, l = _wl(oc["wl"])                # opponent's W-L vs you -> invert
                by_player[mt["opponent"]][oc["combo"]][0] += l
                by_player[mt["opponent"]][oc["combo"]][1] += w
    dossier = []
    for rv in rivals:
        if not (rv["losses"] > rv["wins"] and rv["played"] >= 2):
            continue
        combos = by_player.get(rv["player"], {})
        rows = sorted(({"combo": c, "record": f"{w}-{l}", "wins": w, "losses": l, "btl": w + l}
                       for c, (w, l) in combos.items()), key=lambda z: (z["losses"] - z["wins"], z["btl"]), reverse=True)
        dossier.append({"player": rv["player"], "record": f"{rv['wins']}-{rv['losses']}",
                        "win_pct": rv["win_pct"], "played": rv["played"], "combos": rows[:4]})
    return dossier


# ---------------- field benchmark per combo ----------------
def field_benchmark(reports, player, agg):
    """For every combo you run, your win% vs the field of everyone who ran the same combo.

    Uses the NCBLAST peer-comparison rows (your combo vs each peer on that combo). Reports
    your standing (top / middle / bottom third) so you can see which combos you over- or
    under-pilot relative to the field — the on-ramp for cross-player combo comparison.
    """
    mine = [r for r in reports if str(r.get("player", "")).lower() == player]
    peer_rows = defaultdict(list)
    for r in mine:
        for p in r["peers"]:
            if p.get("combo") and p.get("player"):
                peer_rows[p["combo"]].append(p)
    out = []
    for combo, rows in peer_rows.items():
        peers = [x for x in rows if x["player"] != "YOU"]
        you = agg["combos"].get(combo)
        if not peers or not you or you["battles"] < MIN_COMBO_BATTLES:
            continue
        fb = sum(x["battles"] for x in peers) or 1
        field_avg = round(sum(x["win_pct"] * x["battles"] for x in peers) / fb, 1)
        best = max(peers, key=lambda x: x["win_pct"])
        beat = sum(1 for x in peers if you["win_pct"] > x["win_pct"])
        pct_rank = round(100 * beat / len(peers))
        standing = "top-third" if pct_rank >= 66 else "bottom-third" if pct_rank <= 33 else "middle"
        out.append({"combo": combo, "you": you["win_pct"], "field_avg": field_avg,
                    "gap": round(you["win_pct"] - field_avg, 1), "best_peer": best["player"],
                    "best_win": best["win_pct"], "n_peers": len(peers), "battles": you["battles"],
                    "pct_rank": pct_rank, "standing": standing})
    out.sort(key=lambda z: z["gap"])          # worst-vs-field first
    return out


# ---------------- confidence ----------------
def confidence(agg, events_attended=None):
    """Confidence tier. Event count is the true events ATTENDED (from the league sheet)
    when available, else the number of reports on hand. Combat-detail sections still key
    off report coverage, so we track both."""
    report_events = agg["n_events"]
    e = max(events_attended or 0, report_events)
    b = agg["total_battles"]
    if e >= 4 or b >= 150:
        tier = "Gold"
    elif e >= 2 or b >= 60:
        tier = "Silver"
    else:
        tier = "Bronze"
    unlocked = {"cross_event_trends": report_events >= 2, "widened_meta": report_events >= 2}
    return {"tier": tier, "events": e, "report_events": report_events, "battles": b,
            "attended": events_attended, "missing_reports": max(0, e - report_events),
            "unlocked": unlocked}


def _conf(n, hi, mid):
    return "confirmed" if n >= hi else ("likely" if n >= mid else "tentative")


# ---------------- analysis ----------------
def coach(reports, player, scope="lifetime", meta_report=None, community=None, events_attended=None):
    player = _resolve(reports, player)
    agg = aggregate(reports, player)
    meta = build_meta(reports)
    comm = community_benchmark(reports)
    conf = confidence(agg, events_attended=events_attended)
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

    # launch / positioning: B-side vs X-side split
    launch = _launch_finding(agg.get("side") or {}, weaknesses, strengths)

    # community matchup benchmark: your win% vs the field's win% per opponent combo.
    # The field excludes your own games, so it only fires once OTHER players' reports
    # are in the pool — that is the incentive to grow the platform.
    benchmarks = []
    for opp, (pw, pl) in agg["matchups_opp"].items():
        played = pw + pl
        if played < MIN_MATCHUP_FACED:
            continue
        cs = comm["by_opp"].get(opp)
        if not cs:
            continue
        fw, fl = cs["wins"] - pw, cs["losses"] - pl      # field = everyone but you
        fn = fw + fl
        if fn < MIN_COMMUNITY_BTL:
            continue
        you_pct = round(100 * pw / played, 1)
        field_pct = round(100 * fw / fn, 1)
        gap = round(field_pct - you_pct, 1)
        if gap >= COMMUNITY_GAP and you_pct < 50:
            leaders = _pair_leaders(comm, opp)
            tip = ""
            if leaders:
                yc, s = leaders[0]
                tip = f" The field wins most with {yc} ({s['win_pct']}% over {s['battles']} btl)."
            benchmarks.append({
                "opp": opp, "record": f"{pw}-{pl}", "you_pct": you_pct, "field_pct": field_pct,
                "gap": gap, "field_btl": fn, "n_players": cs["n_players"],
                "text": f"vs {opp}: you win {you_pct}% but the field wins {field_pct}% ({fn} field btl)",
                "suggestion": f"This matchup is winnable — study how the field solves {opp}.{tip}"})
    benchmarks.sort(key=lambda z: -z["gap"])

    # meta: most common field combos you must beat
    for opp, freq in list(meta.items())[:6]:
        rec = agg["matchups_opp"].get(opp)
        note = f" — you are {rec[0]}-{rec[1]} vs it" if rec else " — no data on your record"
        meta_notes.append({"combo": opp, "seen": freq, "text": f"{opp} (seen {freq}x){note}"})

    recommendation = recommend(agg, meta)
    goal = goal_card(reports, player, agg, weaknesses, recommendation, benchmarks)
    nemeses = nemesis_dossier(reports, player, rivals)
    field = field_benchmark(reports, player, agg)
    prediction = PRED.build(reports, player, agg, meta=meta_report, community=community)

    return {"player": agg["player"], "scope": scope, "events": agg["events"], "n_events": agg["n_events"],
            "confidence": conf, "style": agg["style"], "archetype": (agg["archetypes"] or [None])[0],
            "combos": agg["combos"], "loss_finishes": agg["loss_finishes"],
            "weaknesses": weaknesses, "strengths": strengths, "swaps": swaps, "meta": meta_notes,
            "rivals": rivals, "recommendation": recommendation,
            "launch": launch, "side": agg.get("side") or {},
            "benchmarks": benchmarks, "community": {"n_players": comm["n_players"]},
            "goal": goal, "nemeses": nemeses, "field": field, "prediction": prediction,
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


# ---------------- launch / positioning (B-side vs X-side) ----------------
def _launch_finding(side, weaknesses, strengths):
    """Compare B-side vs X-side win% and flag a positioning weakness/strength.

    NCBLAST records which side of the launcher each battle started from. A large,
    battle-backed gap between the two sides is a coachable habit — favor the strong
    side and drill the weak one. Returns a summary dict (or None) and appends any
    finding to the shared weaknesses/strengths lists.
    """
    b, x = side.get("B"), side.get("X")
    if not b or not x:
        return None
    summary = {"B": b, "X": x, "gap": round(b["win_pct"] - x["win_pct"], 1)}
    if b["battles"] < MIN_SIDE_BATTLES or x["battles"] < MIN_SIDE_BATTLES:
        summary["verdict"] = "not enough side-split battles to judge yet"
        return summary
    gap = b["win_pct"] - x["win_pct"]
    strong, weak = ("B", "X") if gap >= 0 else ("X", "B")
    hi, lo = side[strong], side[weak]
    if abs(gap) >= SIDE_GAP:
        conf = "confirmed" if min(b["battles"], x["battles"]) >= 12 else "likely"
        summary["verdict"] = f"favor {strong}-side (+{abs(gap):.1f}% win)"
        weaknesses.append({
            "type": "launch", "severity": "med", "confidence": conf,
            "text": (f"You win {hi['win_pct']}% from {strong}-side but only "
                     f"{lo['win_pct']}% from {weak}-side ({abs(gap):.1f}% gap, "
                     f"{hi['battles']}/{lo['battles']} btl)"),
            "suggestion": (f"Default to {strong}-side launches when you have the choice, "
                           f"and drill {weak}-side starts — it is your weaker opening.")})
        strengths.append({
            "type": "launch", "confidence": conf,
            "text": f"Strong {strong}-side launches: {hi['win_pct']}% win, {hi['ppb']:+} PPB ({hi['battles']} btl)",
            "suggestion": f"Steer toward {strong}-side openings — it is a real edge."})
    else:
        summary["verdict"] = "balanced across both sides"
    return summary


# ---------------- next-tournament recommendation (data-driven) ----------------
TIER_BONUS = {"S": 0.30, "A": 0.15, "B": 0.0, "C": -0.10, "D": -0.30, None: 0.0}


def combo_parts(combo):
    """'Shark Scale 9-60 Free Ball' -> ('Shark Scale', '9-60', 'Free Ball').
    Ratchet is the digits token; a garbled '?-??' ratchet is treated as unknown."""
    import re
    m = re.search(r"[\d?]+-[\d?]+", combo)
    if not m:
        return (combo.strip(), None, None)
    blade = combo[:m.start()].strip(" ·")
    ratchet = m.group(0)
    bit = combo[m.end():].strip(" ·")
    return (blade, ratchet, bit or None)


def recommend(agg, meta, deck_size=3, top_meta=6):
    """Recommend a legal next-tournament deck: proven performance + meta coverage, with the
    3v3 constraint that no Blade/Ratchet/Bit repeats across the deck. Purely data-driven."""
    combos = agg["combos"]
    per_pair = agg["matchups_pair"]
    meta_combos = list(meta)[:top_meta]
    scored = []
    for name, c in combos.items():
        if c["battles"] < MIN_COMBO_BATTLES:
            continue
        covered = [opp for opp in meta_combos
                   if (wl := per_pair.get((name, opp))) and wl[0] > wl[1] and (wl[0] + wl[1]) >= 2]
        score = c["win_pct"] / 100.0 + c["ppb"] + TIER_BONUS.get(c["tier"], 0.0) + 0.1 * len(covered)
        reasons = []
        if c["tier"] in ("S", "A"):
            reasons.append(f"tier {c['tier']}")
        reasons.append(f"{c['win_pct']}% / {c['ppb']:+} PPB over {c['battles']} btl")
        if covered:
            reasons.append(f"beats {len(covered)} of your common meta combos")
        if c.get("trend") == "up":
            reasons.append("trending up")
        scored.append({"combo": name, "score": round(score, 3), "win_pct": c["win_pct"],
                       "ppb": c["ppb"], "tier": c["tier"], "battles": c["battles"],
                       "covered": covered, "trend": c.get("trend"),
                       "confidence": _conf(c["battles"], 15, MIN_COMBO_BATTLES),
                       "reason": ", ".join(reasons)})
    scored.sort(key=lambda z: -z["score"])

    # build a LEGAL deck: greedily take best scorers with no repeated Blade/Ratchet/Bit
    deck, part_conflicts = [], []
    used = {"blade": set(), "ratchet": set(), "bit": set()}
    for s in scored:
        blade, ratchet, bit = combo_parts(s["combo"])
        rkey = ratchet if (ratchet and "?" not in ratchet) else None   # unknown ratchet can't conflict
        clash = None
        if blade in used["blade"]:
            clash = f"Blade '{blade}'"
        elif rkey and rkey in used["ratchet"]:
            clash = f"Ratchet '{rkey}'"
        elif bit and bit in used["bit"]:
            clash = f"Bit '{bit}'"
        if clash:
            part_conflicts.append({"combo": s["combo"], "clash": clash})
            continue
        deck.append(s)
        used["blade"].add(blade)
        if rkey:
            used["ratchet"].add(rkey)
        if bit:
            used["bit"].add(bit)
        if len(deck) >= deck_size:
            break

    bench = [{"combo": s["combo"], "why": f"{s['win_pct']}% / {s['ppb']:+} PPB"} for s in scored
             if s["ppb"] < 0 or s["win_pct"] < 45]
    gaps = []
    for opp in meta_combos:
        rec = agg["matchups_opp"].get(opp)
        if rec and rec[1] > rec[0]:
            answer = any((per_pair.get((n, opp), [0, 0])[0] > per_pair.get((n, opp), [0, 0])[1])
                         for n in combos)
            if not answer:
                gaps.append({"opp": opp, "record": f"{rec[0]}-{rec[1]}"})
    note = None
    if agg["loss_finishes"]:
        t, p = next(iter(agg["loss_finishes"].items()))
        note = f"Your #1 loss condition is {t} ({p}%) — weight the deck toward combos that historically hold up against it."
    return {"deck": deck, "bench": bench, "gaps": gaps, "note": note,
            "part_conflicts": part_conflicts, "meta_combos": meta_combos}


# ---------------- rendering ----------------
def _events_str(c):
    if c.get("missing_reports"):
        return f"{c['events']} events ({c['report_events']} with reports)"
    return f"{c['events']} event(s)"


def _coverage_note(c):
    if c.get("missing_reports"):
        n = c["missing_reports"]
        return (f"{n} attended event(s) have no NCBLAST report — combat detail (combos, matchups, "
                f"finishes) covers {c['report_events']} of {c['events']}. Add those reports to sharpen it.")
    return f"feed more reports: {_next_tier(c).replace('**','')}"


def _next_tier(conf):
    if conf["tier"] == "Bronze":
        return "reach 2 events (or 60 battles) for **Silver** — unlocks cross-event trends"
    if conf["tier"] == "Silver":
        return "reach 4 events (or 150 battles) for **Gold** — highest-confidence findings"
    return "you're at the highest confidence tier"


def coach_txt(d):
    c = d["confidence"]
    L = [f"{d['player']} — coaching report  [scope: {d.get('scope','lifetime')}]",
         f"{_events_str(c)} · {c['battles']} battles · confidence: {c['tier']}",
         f"archetype: {d.get('archetype')}  style: {d.get('style')}",
         f"({_coverage_note(c)})", ""]
    g = d.get("goal") or {}
    if g:
        form = f"form: {g.get('win_pct')}% win, {g.get('ppb'):+} PPB over {g.get('battles')} btl"
        if g.get("trend"):
            form += f" · trend {g['trend']}"
        if g.get("placements"):
            form += f" · placements {', '.join(str(p) for p in g['placements'])}"
        L.append("GOAL CARD")
        L.append(f"  {form}")
        for i, o in enumerate(g.get("objectives", []), 1):
            L.append(f"  {i}. {o}")
        L.append("")
    rec = d.get("recommendation") or {}
    L.append("RECOMMENDED NEXT-TOURNAMENT DECK")
    for i, x in enumerate(rec.get("deck", []), 1):
        L.append(f"  {i}. {x['combo']}  ({x['reason']}) [{x['confidence']}]")
    if not rec.get("deck"):
        L.append("  (not enough battles yet — feed more reports)")
    if rec.get("part_conflicts"):
        L.append("  (part rule: no shared Blade/Ratchet/Bit across the 3 — skipped:")
        for pc in rec["part_conflicts"][:4]:
            L.append(f"     {pc['combo']} — reuses {pc['clash']}")
        L.append("  )")
    if rec.get("bench"):
        L.append("  bench: " + ", ".join(f"{b['combo']} ({b['why']})" for b in rec["bench"]))
    if rec.get("gaps"):
        L.append("  unanswered meta: " + ", ".join(f"{g['opp']} ({g['record']})" for g in rec["gaps"]))
    if rec.get("note"):
        L.append(f"  note: {rec['note']}")
    L.append("")
    L.append("STRENGTHS")
    for s in d["strengths"]:
        L.append(f"  + {s['text']}")
    lf = d.get("launch")
    if lf and lf.get("B") and lf.get("X"):
        b, x = lf["B"], lf["X"]
        L.append("\nLAUNCH & POSITIONING")
        L.append(f"  B-side: {b['win_pct']}% win, {b['ppb']:+} PPB ({b['battles']} btl)")
        L.append(f"  X-side: {x['win_pct']}% win, {x['ppb']:+} PPB ({x['battles']} btl)")
        L.append(f"  -> {lf.get('verdict')}")
    L.append("\nWEAKNESSES")
    for w in d["weaknesses"]:
        L.append(f"  - [{w['severity']}/{w['confidence']}] {w['text']}")
        L.append(f"      -> {w['suggestion']}")
    L.append("\nMATCHUP SWAPS")
    for s in d["swaps"]:
        L.append(f"  vs {s['opp']} (you {s['record']}) -> {s['suggestion']}")
    if not d["swaps"]:
        L.append("  (none — no clean in-deck answer found for your losing matchups)")
    L.append("\nVS THE FIELD — matchups the community solves better than you")
    for b in d.get("benchmarks", []):
        L.append(f"  vs {b['opp']} (you {b['record']}): you {b['you_pct']}% vs field {b['field_pct']}% [+{b['gap']}%, {b['field_btl']} field btl]")
        L.append(f"      -> {b['suggestion']}")
    if not d.get("benchmarks"):
        np = (d.get("community") or {}).get("n_players", 0)
        if np <= 1:
            L.append("  (community benchmark unlocks once other players' reports are added — right now the pool is only you)")
        else:
            L.append("  (no matchup where the field clearly outperforms you)")
    L.append(f"\nRIVALS — your head-to-head ({d.get('scope','lifetime')})")
    for r in d.get("rivals", []):
        tag = "  <-- nemesis" if r["losses"] > r["wins"] and r["played"] >= 2 else ""
        L.append(f"  {r['player']:20} {r['wins']}-{r['losses']}  ({r['win_pct']}%, {r['played']} sets){tag}")
    if not d.get("rivals"):
        L.append("  (no match-recap data in these reports)")
    L.append("\nNEMESIS DOSSIER — who beats you and with what")
    for n in d.get("nemeses", []):
        L.append(f"  {n['player']} (you {n['record']}, {n['win_pct']}% over {n['played']} sets)")
        for cb in n["combos"]:
            L.append(f"     {cb['combo']:32} you {cb['record']}")
    if not d.get("nemeses"):
        L.append("  (no nemesis yet — you're even-or-better vs everyone with a sample)")
    L.append("\nMETA — field you keep facing")
    for m in d["meta"]:
        L.append(f"  * {m['text']}")
    L.append("\nFIELD BENCHMARK — your combos vs everyone who runs them")
    for f in d.get("field", []):
        L.append(f"  {f['combo']:32} you {f['you']}% vs field {f['field_avg']}% "
                 f"[{f['gap']:+}%, {f['standing']}, best {f['best_peer']} {f['best_win']}%]")
    if not d.get("field"):
        L.append("  (no shared-combo peer data in these reports)")
    if d.get("prediction"):
        L.append(PRED.to_txt(d["prediction"]))
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

    # community benchmark — you vs the field per matchup
    def bench_row(b):
        return (f'<div class="row"><span class="dot" style="background:{red}"></span>'
                f'<b>vs {e(b["opp"])}</b> — you {e(b["record"])} '
                f'<span style="color:{red}">{b["you_pct"]}%</span> vs field '
                f'<span style="color:{green}">{b["field_pct"]}%</span>'
                f'<span class="tag">+{b["gap"]}%, {b["field_btl"]} btl</span>'
                f'<div class="sug">{e(b["suggestion"])}</div></div>')
    if d.get("benchmarks"):
        benchmarks_html = '<h2>Vs the field — matchups the community solves better</h2>' + "".join(bench_row(b) for b in d["benchmarks"])
    else:
        np = (d.get("community") or {}).get("n_players", 0)
        msg = ("Community benchmark unlocks once other players' reports are added — the pool is only you right now."
               if np <= 1 else "No matchup where the field clearly outperforms you.")
        benchmarks_html = f'<h2>Vs the field</h2><div class="sub">{e(msg)}</div>'

    meta = block("Meta — field you keep facing", d["meta"],
                 lambda m: f'<div class="row"><span class="dot" style="background:{muted}"></span>{e(m["text"])}</div>')

    # launch / positioning (B-side vs X-side)
    lf = d.get("launch") or {}
    launch_html = ""
    if lf.get("B") and lf.get("X"):
        b, x = lf["B"], lf["X"]
        best = "B" if b["win_pct"] >= x["win_pct"] else "X"
        def side_row(label, s, hot):
            col = green if hot else fg
            return (f'<tr><td style="color:{col}">{label}-side</td>'
                    f'<td style="text-align:right;color:{col}">{s["win_pct"]}%</td>'
                    f'<td style="text-align:right">{s["ppb"]:+}</td>'
                    f'<td style="text-align:right;color:{muted}">{s["battles"]}</td></tr>')
        launch_html = (
            f'<h2>Launch &amp; positioning</h2>'
            f'<table><thead><tr><th>Side</th><th style="text-align:right">Win%</th>'
            f'<th style="text-align:right">PPB</th><th style="text-align:right">Btl</th></tr></thead>'
            f'<tbody>{side_row("B", b, best=="B")}{side_row("X", x, best=="X")}</tbody></table>'
            f'<div class="nudge">{e(str(lf.get("verdict") or ""))}</div>')

    # goal card — crisp finish-line summary
    g = d.get("goal") or {}
    goal_html = ""
    if g:
        chips = [f'{g.get("win_pct")}% win', f'{g.get("ppb"):+} PPB', f'{g.get("battles")} btl']
        if g.get("trend"):
            chips.append(f'trend {g["trend"]}')
        if g.get("placements"):
            chips.append("placements " + ", ".join(str(p) for p in g["placements"]))
        obj = "".join(f'<div class="row"><span class="dot" style="background:{orange}"></span>{e(o)}</div>'
                      for o in g.get("objectives", []))
        goal_html = (f'<h2>Goal card</h2><div class="card"><span class="sub">'
                     + " · ".join(e(c) for c in chips) + f'</span>{obj}</div>')

    # recommended next-tournament deck
    rec = d.get("recommendation") or {}
    deck_rows = "".join(
        f'<div class="row"><span class="dot" style="background:{green}"></span>'
        f'<b>{i}. {e(x["combo"])}</b><span class="tag">{x["confidence"]}</span>'
        f'<div class="sug">{e(x["reason"])}</div></div>'
        for i, x in enumerate(rec.get("deck", []), 1)) or f'<div class="sub">not enough battles yet</div>'
    rec_extra = ""
    if rec.get("part_conflicts"):
        rec_extra += (f'<div class="sub">Part rule (no shared Blade/Ratchet/Bit) skipped: '
                      + ", ".join(f'{e(pc["combo"])} (reuses {e(pc["clash"])})' for pc in rec["part_conflicts"][:4]) + '</div>')
    if rec.get("bench"):
        rec_extra += f'<div class="sub">Bench: ' + ", ".join(e(b["combo"]) for b in rec["bench"]) + '</div>'
    if rec.get("gaps"):
        rec_extra += f'<div class="sub" style="color:{red}">Unanswered meta: ' + ", ".join(f'{e(g["opp"])} ({g["record"]})' for g in rec["gaps"]) + '</div>'
    if rec.get("note"):
        rec_extra += f'<div class="nudge">{e(rec["note"])}</div>'
    recommendation = f'<h2>Recommended next-tournament deck</h2>{deck_rows}{rec_extra}'

    # rivals (head-to-head vs players), nemeses highlighted
    rival_rows = "".join(
        f'<tr><td>{e(r["player"])}</td>'
        f'<td style="text-align:right;color:{green if r["wins"]>=r["losses"] else red}">{r["wins"]}-{r["losses"]}</td>'
        f'<td style="text-align:right">{r["win_pct"]}%</td>'
        f'<td style="text-align:right;color:{muted}">{r["played"]}</td></tr>'
        for r in d.get("rivals", [])) or f'<tr><td colspan="4" style="color:{muted}">no match-recap data</td></tr>'

    # nemesis dossier — who beats you and with which combos
    def nem_card(n):
        rows = "".join(f'<tr><td>{e(cb["combo"])}</td>'
                       f'<td style="text-align:right;color:{red}">{e(cb["record"])}</td></tr>'
                       for cb in n["combos"]) or f'<tr><td colspan="2" style="color:{muted}">combos not itemized</td></tr>'
        return (f'<div class="card"><b style="color:{red}">{e(n["player"])}</b> '
                f'<span class="sub">— you {e(n["record"])} ({n["win_pct"]}% over {n["played"]} sets)</span>'
                f'<table style="margin-top:8px"><thead><tr><th>Their build</th>'
                f'<th style="text-align:right">Your record</th></tr></thead><tbody>{rows}</tbody></table></div>')
    nemeses_html = ("<h2>Nemesis dossier</h2>" + "".join(nem_card(n) for n in d["nemeses"])
                    if d.get("nemeses") else
                    f'<h2>Nemesis dossier</h2><div class="sub">No nemesis yet — you\'re even-or-better vs everyone with a sample.</div>')

    # field benchmark per combo — you vs everyone who runs the same combo
    stand_col = {"top-third": green, "middle": orange, "bottom-third": red}
    field_rows = "".join(
        f'<tr><td>{e(f["combo"])}</td>'
        f'<td style="text-align:right">{f["you"]}%</td>'
        f'<td style="text-align:right;color:{muted}">{f["field_avg"]}%</td>'
        f'<td style="text-align:right;color:{green if f["gap"]>=0 else red}">{f["gap"]:+}%</td>'
        f'<td style="text-align:center;color:{stand_col.get(f["standing"], muted)}">{e(f["standing"])}</td>'
        f'<td style="text-align:right;color:{muted}">{e(f["best_peer"])} {f["best_win"]}%</td></tr>'
        for f in d.get("field", []))
    if field_rows:
        field_html = ('<h2>Field benchmark — your combos vs the field</h2>'
                      '<table><thead><tr><th>Combo</th><th style="text-align:right">You</th>'
                      '<th style="text-align:right">Field avg</th><th style="text-align:right">Gap</th>'
                      '<th style="text-align:center">Standing</th><th style="text-align:right">Best peer</th>'
                      f'</tr></thead><tbody>{field_rows}</tbody></table>')
    else:
        field_html = '<h2>Field benchmark</h2><div class="sub">No shared-combo peer data in these reports.</div>'

    prediction_html = PRED.to_html(d["prediction"], th) if d.get("prediction") else ""

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
 summary{{color:{orange};cursor:pointer;font-size:12px;margin-top:6px}} details{{margin-top:4px}}
</style></head><body><div class="wrap">
 <h1>{e(d['player'])} <span class="pill">{e(scope)}</span></h1>
 <div class="card"><span class="big">{_events_str(c)} · {c['battles']} battles · confidence
   <b style="color:{orange}">{c['tier']}</b></span><br>
   <span class="sub">archetype: {e(str(d.get('archetype')))} · style {e(str(d.get('style')))}</span>
   <div class="nudge">▲ {e(_coverage_note(c))}</div></div>
 {goal_html}
 {recommendation}
 {strengths}{weaknesses}{swaps}
 {benchmarks_html}
 {launch_html}
 <h2>Rivals — head-to-head ({e(scope)})</h2>
 <table><thead><tr><th>Opponent</th><th style="text-align:right">Record</th><th style="text-align:right">Win%</th><th style="text-align:right">Sets</th></tr></thead>
   <tbody>{rival_rows}</tbody></table>
 {nemeses_html}
 {prediction_html}
 {meta}
 {field_html}
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
