"""Opponent prediction ("shuffle probability"), self-scouting, and meta-counter.

All data-driven from the match recaps a player provides — no parts database, no AI
at runtime. The more reports (and the more times you've faced someone), the sharper
the read. A hidden full-community mode (see cli --bd) widens opponent prediction to a
player's own reports across the whole field; when absent, everything is computed from
the subject player's own match recaps only.

Readout rules (per opponent, by how repetitive their deck is):
  * locked (>=80% deck repeat) — show each full combo, no percentages (understood 100%).
  * core+flex (25-79%)         — locked combos shown in full; variable slots shown at the
                                 component level: blade (with % unless 100%), then most-used
                                 ratchet and bit with their percentages.
  * unpredictable (<25%)       — tag it and show "??? ??? ???"; the full deck history they've
                                 shown against you is available (txt lists it; html popup).
"""
from __future__ import annotations
import itertools
import re
from collections import Counter, defaultdict

MIN_SCOUT_MATCHES = 2      # face someone this many times before predicting their deck
LOCKED_AT = 80
UNPREDICTABLE_UNDER = 25


def _parts(combo):
    m = re.search(r"[\d?]+-[\d?]+", combo)
    if not m:
        toks = combo.split()
        return (" ".join(toks[:-1]) or combo, None, toks[-1] if len(toks) > 1 else None)
    return (combo[:m.start()].strip(), m.group(0), combo[m.end():].strip() or None)


def _blade(combo):
    return _parts(combo)[0]


def _avg_jaccard(decks):
    """Average pairwise deck overlap across matches (100 = identical deck every time)."""
    sims = []
    for a, b in itertools.combinations(decks, 2):
        u = a | b
        sims.append(len(a & b) / len(u) if u else 0.0)
    return round(100 * sum(sims) / len(sims)) if sims else None


# ---------------- opponent scouting / shuffle prediction ----------------
def _opp_decks(reports, player):
    """opponent player -> list of per-match combo lists (from the subject's recaps)."""
    out = defaultdict(list)
    for r in reports:
        if str(r.get("player", "")).lower() != player:
            continue
        for m in r.get("matches", []):
            combos = [oc["combo"] for oc in m.get("opp_combos", []) if oc.get("combo")]
            if m.get("opponent") and combos:
                out[m["opponent"]].append(combos)
    return out


def _community_decks(community, player):
    """A player's OWN combos across the community pool (the backdoor's extra signal)."""
    decks = defaultdict(Counter)      # player_lc -> Counter(combo -> battles)
    for r in community or []:
        p = str(r.get("player", "")).lower()
        for c in r.get("combos", []):
            if c.get("combo") and c.get("battles"):
                decks[p][c["combo"]] += c["battles"]
    return decks


def _predict_deck(match_lists):
    """Component-level readout of an opponent's likely deck across their matches vs you."""
    n = len(match_lists)
    blade_matches = defaultdict(int)          # blade -> matches present
    blade_combos = defaultdict(Counter)       # blade -> Counter(full combo)
    blade_ratchets = defaultdict(Counter)
    blade_bits = defaultdict(Counter)
    for combos in match_lists:
        seen = set()
        for c in combos:
            bl, rt, bt = _parts(c)
            blade_combos[bl][c] += 1
            if rt:
                blade_ratchets[bl][rt] += 1
            if bt:
                blade_bits[bl][bt] += 1
            seen.add(bl)
        for bl in seen:
            blade_matches[bl] += 1
    picks = []
    for bl, bm in sorted(blade_matches.items(), key=lambda z: (-z[1], -sum(blade_combos[z[0]].values()))):
        blade_pct = round(100 * bm / n)
        combos_for = blade_combos[bl]
        if len(combos_for) == 1:
            picks.append({"kind": "combo", "blade": bl, "combo": next(iter(combos_for)), "blade_pct": blade_pct})
        else:
            rt, rtn = (blade_ratchets[bl].most_common(1)[0] if blade_ratchets[bl] else (None, 0))
            bt, btn = (blade_bits[bl].most_common(1)[0] if blade_bits[bl] else (None, 0))
            rtot = sum(blade_ratchets[bl].values()) or 1
            btot = sum(blade_bits[bl].values()) or 1
            picks.append({"kind": "partial", "blade": bl, "blade_pct": blade_pct,
                          "ratchet": rt, "ratchet_pct": round(100 * rtn / rtot),
                          "bit": bt, "bit_pct": round(100 * btn / btot)})
    return picks


def opponent_scout(reports, player, agg, community=None, min_matches=MIN_SCOUT_MATCHES):
    decks = _opp_decks(reports, player)
    comm = _community_decks(community, player) if community else {}
    rivals = dict(agg.get("opp_players", {}))
    per_pair = agg.get("matchups_pair", {})
    out = []
    for opp, match_lists in sorted(decks.items(), key=lambda z: -len(z[1])):
        n = len(match_lists)
        if n < min_matches:
            continue
        jac = _avg_jaccard([set(c) for c in match_lists])
        j = jac or 0
        label = "locked" if j >= LOCKED_AT else "unpredictable" if j < UNPREDICTABLE_UNDER else "core+flex"
        readout = None if label == "unpredictable" else _predict_deck(match_lists)

        # your best in-deck answer to the combos they keep bringing
        combo_present = Counter(c for combos in match_lists for c in set(combos))
        answers = []
        for combo, _k in combo_present.most_common(4):
            best = None
            for (yc, oc), ov in per_pair.items():
                if oc == combo and ov[0] > ov[1] and (best is None or (ov[0] - ov[1]) > best[1] - best[2]):
                    best = (yc, ov[0], ov[1])
            if best:
                answers.append({"vs": combo, "bring": best[0], "record": f"{best[1]}-{best[2]}"})

        # backdoor: if we hold this opponent's OWN reports, show their actual deck
        source = "your matches"
        comm_deck = comm.get(str(opp).lower())
        if comm_deck:
            source = "your matches + community reports"
            tot = sum(comm_deck.values()) or 1
            readout = [{"kind": "combo", "blade": _blade(c), "combo": c,
                        "blade_pct": round(100 * k / tot)} for c, k in comm_deck.most_common(6)]
            label = "known deck (community)"

        w, l = rivals.get(opp, (0, 0))
        out.append({"opponent": opp, "matches": n, "record": f"{w}-{l}",
                    "predictability": jac, "consistency": label, "source": source,
                    "readout": readout, "answers": answers,
                    "decks_faced": [list(c) for c in match_lists]})
    return out


# ---------------- self scout (how you're read) ----------------
def self_read(agg, top=4):
    by_blade = defaultdict(lambda: {"battles": 0, "combos": Counter()})
    total = 0
    for name, c in agg.get("combos", {}).items():
        b = c.get("battles", 0)
        total += b
        bl = _blade(name)
        by_blade[bl]["battles"] += b
        by_blade[bl]["combos"][name] += b
    total = total or 1
    rows = []
    for bl, d in sorted(by_blade.items(), key=lambda z: -z[1]["battles"])[:top]:
        rows.append({"blade": bl, "battles": d["battles"], "pct": round(100 * d["battles"] / total),
                     "combos": [c for c, _ in d["combos"].most_common(3)]})
    return {"blades": rows, "total_battles": total,
            "note": "Approximated from combo battle-share (report has no launch-order / slot-1 data). "
                    "Supplement with per-match slot tracking to sharpen 'what you open with'."}


# ---------------- meta counter (field snapshot) ----------------
def meta_counter(agg, meta, top_combos=8):
    if not meta:
        return None
    per_pair = agg.get("matchups_pair", {})
    opp_rec = agg.get("matchups_opp", {})
    rows = []
    for item in (meta.get("top3_combos") or [])[:top_combos]:
        combo = item.get("combo")
        rec = opp_rec.get(combo)
        best = None
        for (yc, oc), ov in per_pair.items():
            if oc == combo and ov[0] > ov[1] and (best is None or (ov[0] - ov[1]) > best[1] - best[2]):
                best = (yc, ov[0], ov[1])
        rows.append({"combo": combo, "field_count": item.get("count"),
                     "your_record": (f"{rec[0]}-{rec[1]}" if rec else None),
                     "answer": ({"bring": best[0], "record": f"{best[1]}-{best[2]}"} if best else None)})
    covered = sum(1 for r in rows if r["answer"])
    gaps = [r["combo"] for r in rows if not r["answer"] and not r["your_record"]]
    return {"generated": meta.get("generated"), "entries": meta.get("entries"),
            "top_blades": [b["blade"] for b in (meta.get("blade_meta") or [])[:4]],
            "top_ratchets": [r["ratchet"] for r in (meta.get("ratchet_meta") or [])[:3]],
            "rows": rows, "covered": covered, "of": len(rows), "gaps": gaps[:5],
            "disclaimer": "Assumes no meta shift from the " + str(meta.get("generated") or "snapshot") + " field."}


def build(reports, player, agg, meta=None, community=None):
    return {"scouting": opponent_scout(reports, player, agg, community=community),
            "self_read": self_read(agg),
            "meta_counter": meta_counter(agg, meta),
            "backdoor": bool(community)}


# ---------------- readout formatting ----------------
def _pick_txt(pk):
    if pk["kind"] == "combo":
        return pk["combo"] if pk["blade_pct"] >= 100 else f"{pk['combo']} ({pk['blade_pct']}%)"
    bl = pk["blade"] if pk["blade_pct"] >= 100 else f"{pk['blade']} ({pk['blade_pct']}%)"
    rt = f"{pk['ratchet']} ({pk['ratchet_pct']}%)" if pk.get("ratchet") else "?"
    bt = f"{pk['bit']} ({pk['bit_pct']}%)" if pk.get("bit") else "?"
    return f"{bl} {rt} {bt}"


# ---------------- render: txt ----------------
def to_txt(p):
    L = []
    sc = p.get("scouting") or []
    L.append("\nRIVAL SCOUTING — shuffle prediction" + ("  [community mode]" if p.get("backdoor") else ""))
    for s in sc:
        pr = f"{s['predictability']}%" if s["predictability"] is not None else "n/a"
        L.append(f"  {s['opponent']} — you {s['record']} / {s['matches']} matches · predictability {pr} ({s['consistency']})")
        if s["consistency"] == "unpredictable" or not s["readout"]:
            L.append("     ??? ??? ???")
            for i, deck in enumerate(s["decks_faced"], 1):
                L.append(f"       deck {i}: {' · '.join(deck)}")
        else:
            L.append("     " + "  ·  ".join(_pick_txt(pk) for pk in s["readout"]))
        for a in s["answers"]:
            L.append(f"     answer: bring {a['bring']} vs {a['vs']} (you {a['record']})")
        if s["source"] != "your matches":
            L.append(f"     [source: {s['source']}]")
    if not sc:
        L.append("  (no opponent faced 2+ times with combo data yet — feed more reports)")

    sr = p.get("self_read") or {}
    L.append("\nHOW YOU'RE READ — your tendencies (self-scout)")
    for b in sr.get("blades", []):
        L.append(f"  {b['blade']} — {b['pct']}% of your battles ({b['battles']} btl): {', '.join(b['combos'])}")
    L.append(f"  note: {sr.get('note','')}")

    mc = p.get("meta_counter")
    if mc:
        L.append(f"\nMETA COUNTER — field snapshot ({mc.get('entries')} entries, {mc.get('generated')})")
        L.append(f"  top blades: {', '.join(mc['top_blades'])} · top ratchets: {', '.join(mc['top_ratchets'])}")
        for r in mc["rows"]:
            rec = f"you {r['your_record']}" if r["your_record"] else "no record"
            ans = f" -> bring {r['answer']['bring']} ({r['answer']['record']})" if r["answer"] else ""
            L.append(f"  {r['combo']} (x{r['field_count']}) · {rec}{ans}")
        if mc["gaps"]:
            L.append(f"  UNANSWERED meta: {', '.join(mc['gaps'])}")
        L.append(f"  coverage: you have a winning answer to {mc['covered']}/{mc['of']} top field combos")
        L.append(f"  * {mc['disclaimer']}")
    return "\n".join(L)


# ---------------- render: html ----------------
def _pick_html(pk, e, muted, red):
    if pk["kind"] == "combo":
        txt = e(pk["combo"]) if pk["blade_pct"] >= 100 else f'{e(pk["combo"])} <span style="color:{muted}">{pk["blade_pct"]}%</span>'
        return f'<span class="tag">{txt}</span>'
    bl = e(pk["blade"]) if pk["blade_pct"] >= 100 else f'{e(pk["blade"])} <span style="color:{muted}">{pk["blade_pct"]}%</span>'
    rt = f'{e(pk["ratchet"])} <span style="color:{muted}">{pk["ratchet_pct"]}%</span>' if pk.get("ratchet") else "?"
    bt = f'{e(pk["bit"])} <span style="color:{muted}">{pk["bit_pct"]}%</span>' if pk.get("bit") else "?"
    return f'<span class="tag">{bl} · {rt} · {bt}</span>'


def to_html(p, theme):
    import html as _h
    e = _h.escape
    orange = theme.get("player", "#ff8c1a")
    green, red, muted, border = "#57e26b", theme.get("cutoff", "#ff5555"), theme.get("muted", "#6b7280"), "#241a0e"
    cons_col = {"locked": red, "core+flex": orange, "unpredictable": muted, "known deck (community)": red}

    sc = p.get("scouting") or []
    bd = ' <span class="tag">community mode</span>' if p.get("backdoor") else ""
    cards = ""
    for s in sc:
        pr = f"{s['predictability']}%" if s["predictability"] is not None else "n/a"
        head = (f'<b>{e(s["opponent"])}</b> <span class="sub">— you {e(s["record"])} / {s["matches"]} matches · '
                f'predictability {pr} · <b style="color:{cons_col.get(s["consistency"], muted)}">{e(s["consistency"])}</b></span>')
        decks_popup = ("<details><summary>decks faced</summary>"
                       + "".join(f'<div class="sub">deck {i}: {e(" · ".join(dk))}</div>'
                                 for i, dk in enumerate(s["decks_faced"], 1)) + "</details>")
        if s["consistency"] == "unpredictable" or not s["readout"]:
            body = (f'<div style="margin:6px 0;font-size:18px;letter-spacing:2px;color:{muted}">??? ??? ???</div>'
                    f'{decks_popup}')
        else:
            body = ('<div style="margin:6px 0">' + " ".join(_pick_html(pk, e, muted, red) for pk in s["readout"])
                    + "</div>" + decks_popup)
        answers = "".join(f'<div class="sub">Answer: bring <b>{e(a["bring"])}</b> vs {e(a["vs"])} (you {e(a["record"])})</div>'
                          for a in s["answers"])
        src = f'<div class="sub" style="color:{muted}">source: {e(s["source"])}</div>' if s["source"] != "your matches" else ""
        cards += f'<div class="card">{head}{body}{answers}{src}</div>'
    scouting_html = f'<h2>Rival scouting — shuffle prediction{bd}</h2>' + (cards or '<div class="sub">No opponent faced 2+ times yet.</div>')

    sr = p.get("self_read") or {}
    rows = "".join(f'<tr><td>{e(b["blade"])}</td><td style="text-align:right">{b["pct"]}%</td>'
                   f'<td style="text-align:right;color:{muted}">{b["battles"]}</td>'
                   f'<td style="color:{muted}">{e(", ".join(b["combos"]))}</td></tr>' for b in sr.get("blades", []))
    self_html = (f'<h2>How you\'re read — your tendencies</h2>'
                 f'<table><thead><tr><th>Blade</th><th style="text-align:right">Battle share</th>'
                 f'<th style="text-align:right">Btl</th><th>Your combos</th></tr></thead><tbody>{rows}</tbody></table>'
                 f'<div class="sub">{e(sr.get("note",""))}</div>')

    mc = p.get("meta_counter")
    meta_html = ""
    if mc:
        mrows = "".join(
            f'<tr><td>{e(r["combo"])}</td><td style="text-align:center;color:{muted}">x{r["field_count"]}</td>'
            f'<td style="text-align:center">{e(r["your_record"]) if r["your_record"] else "—"}</td>'
            f'<td style="color:{green}">{("bring " + e(r["answer"]["bring"]) + " (" + e(r["answer"]["record"]) + ")") if r["answer"] else ""}</td></tr>'
            for r in mc["rows"])
        gaps = f'<div class="sub" style="color:{red}">Unanswered meta: {e(", ".join(mc["gaps"]))}</div>' if mc["gaps"] else ""
        meta_html = (f'<h2>Meta counter — field snapshot</h2>'
                     f'<div class="sub">{mc.get("entries")} entries · {e(str(mc.get("generated")))} · '
                     f'top blades {e(", ".join(mc["top_blades"]))} · top ratchets {e(", ".join(mc["top_ratchets"]))}</div>'
                     f'<table><thead><tr><th>Field combo</th><th style="text-align:center">Field</th>'
                     f'<th style="text-align:center">You</th><th>Your answer</th></tr></thead><tbody>{mrows}</tbody></table>'
                     f'{gaps}<div class="nudge">Coverage: winning answer to {mc["covered"]}/{mc["of"]} top field combos</div>'
                     f'<div class="sub">{e(mc["disclaimer"])}</div>')
    return scouting_html + self_html + meta_html
