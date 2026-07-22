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
UNPREDICTABLE_UNDER = 25   # below this predictability we show "??? ??? ???"


# Predictability gradient: neon green (certain) -> yellow (retains core) -> red (chaotic).
# The score blends full-combo repeat with blade repeat, so "keeps blades, swaps ratchets/bits"
# lands in the middle rather than looking random.
_PRED_TIERS = [
    (100, "Locked In", "#39ff14"),        # neon green — never changes the deck
    (86, "Dead Read", "#7CFC00"),
    (72, "Readable", "#B4E400"),
    (58, "Leans a Way", "#E4E400"),        # yellow-green
    (46, "Core + Flex", "#FFD400"),        # yellow — 1-2 locked, rest flexes
    (32, "Mixes It Up", "#FFA500"),
    (18, "Shape-Shifter", "#FF6B1A"),
    (0, "Wild Card", "#FF3B3B"),           # red — explores, no read
]


def _pred_tier(pct):
    if pct is None:
        return ("Known deck", "#39ff14")
    for lo, label, color in _PRED_TIERS:
        if pct >= lo:
            return (label, color)
    return ("Wild Card", "#FF3B3B")



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


def _unique_decks(match_lists):
    """Collapse the deck history to DISTINCT decks (order-independent) with how often each ran.
    Two matches with the same combos in a different slot order = one deck, not two."""
    seen, order = {}, []
    for combos in match_lists:
        key = frozenset(combos)
        if key not in seen:
            seen[key] = {"combos": list(dict.fromkeys(combos)), "times": 0}
            order.append(key)
        seen[key]["times"] += 1
    decks = [seen[k] for k in order]
    decks.sort(key=lambda d: -d["times"])
    return decks


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


def _meta_style(combos, meta):
    """Tag an opponent 'meta' / 'anti-meta' / 'mixed' by how much their kit sits in the field meta."""
    if not meta or not combos:
        return None
    top_combos = {c["combo"] for c in (meta.get("top3_combos") or [])}
    top_blades = {b["blade"] for b in (meta.get("blade_meta") or [])[:6]}
    combos = set(combos)
    hits = sum(1 for c in combos if c in top_combos or _blade(c) in top_blades)
    frac = hits / len(combos)
    tag = "meta" if frac >= 0.6 else "anti-meta" if frac <= 0.34 else "mixed"
    return {"tag": tag, "pct": round(100 * frac)}


def _watch(pred, pred_label, meta_style):
    notes = []
    if pred is None:
        notes.append("their actual deck is known (community data)")
    elif pred >= 72:
        notes.append("highly readable — prep a specific counter before you play them")
    elif pred < UNPREDICTABLE_UNDER:
        notes.append("hard to prep — expect anything; play reactively")
    if meta_style and meta_style["tag"] == "meta" and (pred is None or pred >= 58):
        notes.append("meta player: a meta shift may change this deck — re-scout them if the meta moves")
    elif meta_style and meta_style["tag"] == "anti-meta":
        notes.append("anti-meta: runs off-meta builds, less swayed by the field")
    return notes


def opponent_scout(reports, player, agg, community=None, meta=None, min_matches=MIN_SCOUT_MATCHES):
    decks = _opp_decks(reports, player)
    comm = _community_decks(community, player) if community else {}
    rivals = dict(agg.get("opp_players", {}))
    per_pair = agg.get("matchups_pair", {})
    out = []
    for opp, match_lists in sorted(decks.items(), key=lambda z: -len(z[1])):
        n = len(match_lists)
        if n < min_matches:
            continue
        combo_jac = _avg_jaccard([set(c) for c in match_lists]) or 0
        blade_jac = _avg_jaccard([{_blade(c) for c in cl} for cl in match_lists]) or 0
        pred = round(0.6 * combo_jac + 0.4 * blade_jac)      # blended predictability
        readout = None if pred < UNPREDICTABLE_UNDER else _predict_deck(match_lists)
        all_combos = {c for cl in match_lists for c in cl}
        mstyle = _meta_style(all_combos, meta)

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
            pred = None                                       # not a guess anymore — it's known
            mstyle = _meta_style(set(comm_deck), meta) or mstyle

        pred_label, pred_color = _pred_tier(pred)
        w, l = rivals.get(opp, (0, 0))
        out.append({"opponent": opp, "matches": n, "record": f"{w}-{l}",
                    "predictability": pred, "pred_label": pred_label, "pred_color": pred_color,
                    "meta_style": mstyle, "watch": _watch(pred, pred_label, mstyle),
                    "source": source, "readout": readout, "answers": answers,
                    "decks_faced": _unique_decks(match_lists)})
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
    return {"scouting": opponent_scout(reports, player, agg, community=community, meta=meta),
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
        pr = f"{s['predictability']}%" if s["predictability"] is not None else "known"
        mtag = f" · {s['meta_style']['tag']} ({s['meta_style']['pct']}%)" if s.get("meta_style") else ""
        L.append(f"  {s['opponent']} — you {s['record']} / {s['matches']} matches · "
                 f"[{s['pred_label']} {pr}]{mtag}")
        if not s["readout"]:
            L.append("     ??? ??? ???")
            for i, d in enumerate(s["decks_faced"], 1):
                tag = f" (seen {d['times']}x)" if d["times"] > 1 else ""
                L.append(f"       deck {i}{tag}: {' · '.join(d['combos'])}")
        else:
            L.append("     " + "  ·  ".join(_pick_txt(pk) for pk in s["readout"]))
        for a in s["answers"]:
            L.append(f"     answer: bring {a['bring']} vs {a['vs']} (you {a['record']})")
        for wnote in s.get("watch", []):
            L.append(f"     watch: {wnote}")
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

    sc = p.get("scouting") or []
    bd = ' <span class="tag">community mode</span>' if p.get("backdoor") else ""
    cards = ""
    for s in sc:
        pr = f"{s['predictability']}%" if s["predictability"] is not None else "known"
        box = (f'<span style="background:{s["pred_color"]};color:#0a0a0a;border-radius:6px;'
               f'padding:2px 9px;font-weight:700;font-size:12px">{e(s["pred_label"])} · {pr}</span>')
        mtag = ""
        if s.get("meta_style"):
            mc = {"meta": orange, "anti-meta": green, "mixed": muted}.get(s["meta_style"]["tag"], muted)
            mtag = f' <span class="tag" style="border-color:{mc};color:{mc}">{e(s["meta_style"]["tag"])} {s["meta_style"]["pct"]}%</span>'
        head = (f'<b>{e(s["opponent"])}</b> {box}{mtag} '
                f'<span class="sub">— you {e(s["record"])} / {s["matches"]} matches</span>')
        decks = s["decks_faced"]
        popup_rows = "".join(
            f'<div class="sub">deck {i}{(" (seen " + str(d["times"]) + "x)") if d["times"] > 1 else ""}: {e(" · ".join(d["combos"]))}</div>'
            for i, d in enumerate(decks, 1))
        decks_popup = (f'<details><summary>decks faced ({len(decks)} unique)</summary>{popup_rows}</details>')
        if not s["readout"]:
            body = (f'<div style="margin:6px 0;font-size:18px;letter-spacing:2px;color:{red}">??? ??? ???</div>'
                    f'{decks_popup}')
        else:
            body = ('<div style="margin:6px 0">' + " ".join(_pick_html(pk, e, muted, red) for pk in s["readout"])
                    + "</div>" + decks_popup)
        answers = "".join(f'<div class="sub">Answer: bring <b>{e(a["bring"])}</b> vs {e(a["vs"])} (you {e(a["record"])})</div>'
                          for a in s["answers"])
        watch = "".join(f'<div class="nudge">▲ {e(w)}</div>' for w in s.get("watch", []))
        src = f'<div class="sub" style="color:{muted}">source: {e(s["source"])}</div>' if s["source"] != "your matches" else ""
        cards += f'<div class="card">{head}{body}{answers}{watch}{src}</div>'
    legend = ('<div class="sub" style="margin-bottom:8px">How to read: the colored box is '
              '<b>predictability</b> (green = same deck every time → readable, yellow = keeps a core but '
              'flexes, red = unpredictable). <b>meta / anti-meta</b> = how much of their kit is in the current '
              'field meta. Combos with a % = how often they bring it; no % = every match. Click '
              '<i>decks faced</i> for their distinct decks (same combos in a different order count as one).</div>')
    scouting_html = f'<h2>Rival scouting — shuffle prediction{bd}</h2>' + legend + (cards or '<div class="sub">No opponent faced 2+ times yet.</div>')

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
        mlegend = ('<div class="sub" style="margin:4px 0 2px">Columns: <b>Field</b> = top-3 finishes in the '
                   'meta snapshot (how popular the combo is) · <b>You</b> = your battle record vs it ('
                   '"—" = no data yet) · <b>Your answer</b> = the combo in your deck with the best record vs it '
                   '(blank = no winning answer — a gap).</div>')
        meta_html = (f'<h2>Meta counter — field snapshot</h2>'
                     f'<div class="sub">{mc.get("entries")} entries · {e(str(mc.get("generated")))} · '
                     f'top blades {e(", ".join(mc["top_blades"]))} · top ratchets {e(", ".join(mc["top_ratchets"]))}</div>'
                     f'{mlegend}'
                     f'<table><thead><tr><th>Field combo</th><th style="text-align:center">Field</th>'
                     f'<th style="text-align:center">You</th><th>Your answer</th></tr></thead><tbody>{mrows}</tbody></table>'
                     f'{gaps}<div class="nudge">Coverage: winning answer to {mc["covered"]}/{mc["of"]} top field combos</div>'
                     f'<div class="sub">{e(mc["disclaimer"])}</div>')
    return scouting_html + self_html + meta_html
