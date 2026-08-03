"""Parse the Beyblade X reference/meta markdown into a structured parts database.

Two source documents (see the dataset manifest):
  * beyblade_x_all_parts_reference_*.md  — stable reference: Blades, Ratchets, Bits
  * beyblade_x_current_meta_and_counters_*.md — time-sensitive: archetypes, counters,
    confidence-tagged claims

The result is a `parts_db.json` the pipeline can cross-reference against match data. Its
headline use: separate a **part issue** from a **skill gap**. When a combo underperforms we
can now ask *two* questions instead of one —

  1. Do peers on the SAME combo do better?   -> skill gap (the part works, you don't)
  2. Does the reference say the part is weak
     or badly matched into what you faced?   -> part issue (change the hardware)

Everything is keyed on normalized names so it joins to the NCBLAST combo strings.
"""
from __future__ import annotations
import json
import os
import re

# ---------------------------------------------------------------- helpers
def _norm(s):
    """Normalize a part/blade name for joining (lowercase, collapse whitespace)."""
    return re.sub(r"\s+", " ", str(s or "").strip()).lower()


def _rows(md, header):
    """Yield the cell-lists of the markdown table that follows `header`.

    Tables are '| a | b |' rows; the separator row ('|---|') and the header row are skipped.
    Stops at the next '## ' section.
    """
    sec = md.split(header, 1)
    if len(sec) < 2:
        return
    body = sec[1].split("\n## ", 1)[0]
    seen_header = False
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if re.match(r"^\|[\s:\-|]+\|$", line):        # |---|---| separator
            seen_header = True
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not seen_header:                            # this is the header row itself
            continue
        if cells:
            yield cells


# role keywords -> canonical tags. A part can carry several.
_ROLE_MAP = {
    "attack": "attack", "stamina": "stamina", "defense": "defense",
    "balance": "balance", "gimmick": "gimmick",
}


def _roles(text):
    """'Stamina/Defense' -> ['stamina', 'defense'] (order preserved, deduped)."""
    out = []
    for tok in re.split(r"[/,]", str(text or "")):
        t = _ROLE_MAP.get(tok.strip().lower())
        if t and t not in out:
            out.append(t)
    return out


# ---------------------------------------------------------------- parts reference
def parse_parts_reference(md):
    """-> {'blades': {...}, 'ratchets': {...}, 'bits': {...}} keyed by normalized name."""
    blades, ratchets, bits = {}, {}, {}

    # | Blade assembly | System | Spin | Intended role | Detailed description ... |
    for c in _rows(md, "## Released and announced Blade assemblies"):
        if len(c) < 5:
            continue
        name = c[0]
        if not name or name.lower().startswith("blade"):
            continue
        blades[_norm(name)] = {
            "name": name, "system": c[1], "spin": c[2].lower(),
            "roles": _roles(c[3]), "read": c[4],
        }

    # | Ratchet | Status | Detailed description |
    for c in _rows(md, "## Ratchets"):
        if len(c) < 3:
            continue
        name = c[0]
        if not name or name.lower().startswith("ratchet"):
            continue
        desc = c[2]
        m = re.match(r"^(\d+|M)-(\d+)$", name)
        ratchets[_norm(name)] = {
            "name": name, "status": c[1], "desc": desc,
            "sides": (None if not m or m.group(1) == "M" else int(m.group(1))),
            "height_mm": (None if not m else int(m.group(2)) / 10.0),
            # flags the deck-composition checks use
            "burst_exposed": bool(re.search(r"burst[- ]exposed|raises burst|burst.{0,20}risk|"
                                            r"high (?:contact/)?burst", desc, re.I)),
            "burst_safe": bool(re.search(r"burst resistance|burst[- ]safe|safest", desc, re.I)),
            "scrape_risk": bool(re.search(r"scrap", desc, re.I)),
        }

    # | Code | Bit | Intended role | Detailed description and tradeoffs |
    for c in _rows(md, "## Bits"):
        if len(c) < 4:
            continue
        code, name = c[0], c[1]
        if not name or name.lower() == "bit":
            continue
        desc = c[3]
        bits[_norm(name)] = {
            "name": name, "code": code, "roles": _roles(c[2]), "desc": desc,
            "self_ko_risk": bool(re.search(r"self-KO", desc, re.I)),
            "burst_resistant": bool(re.search(r"burst resistance", desc, re.I)),
            "wear_sensitive": bool(re.search(r"\bwear\b", desc, re.I)),
            "copy_variance": bool(re.search(r"copy-to-copy|copies|copy ", desc, re.I)),
        }
    return {"blades": blades, "ratchets": ratchets, "bits": bits}


# ---------------------------------------------------------------- meta / counters
def parse_meta_guide(md):
    """-> archetypes, counter sections, confidence-tagged claims, watchlist, decision tree."""
    out = {"archetypes": [], "counters": {}, "claims": [], "watchlist": [], "decision_tree": []}

    # | Archetype | Representative combo | Why it is meta | Main weaknesses | Confidence |
    for c in _rows(md, "## Established meta core"):
        if len(c) < 5 or c[0].lower().startswith("archetype"):
            continue
        out["archetypes"].append({
            "archetype": c[0], "combo": re.sub(r"\*\*", "", c[1]).strip(),
            "why": c[2], "weaknesses": c[3], "confidence": c[4],
        })

    # | Claim | Confidence | Basis |
    for c in _rows(md, "## Confidence-tagged claims for machine training"):
        if len(c) < 3 or c[0].lower().startswith("claim"):
            continue
        out["claims"].append({"claim": c[0], "confidence": c[1], "basis": c[2]})

    # "### Countering X" prose blocks -> {target: {best, text}}
    for block in md.split("\n### ")[1:]:
        title, _, body = block.partition("\n")
        if not title.lower().startswith("countering"):
            continue
        target = title.replace("Countering", "").strip()
        body = body.split("\n## ")[0]
        best = None
        # "**Best broad answer: X.**" / "**Safest established concept: X.**" /
        # "**Use controlled attack, not more passive stamina.**"
        for pat in (r"\*\*Best broad answer:\s*([^*]+?)\.?\*\*",
                    r"\*\*Safest established concept:\s*([^*]+?)\.?\*\*",
                    r"\*\*(Use [^*]+?)\.?\*\*"):
            m = re.search(pat, body)
            if m:
                best = m.group(1).strip()
                break
        out["counters"][_norm(target)] = {
            "target": target, "best_answer": best,
            "text": re.sub(r"\n{2,}", "\n", body).strip(),
        }

    # watchlist bullets: "- **Part:** reason"
    wl = md.split("## New-release watchlist", 1)
    if len(wl) > 1:
        for line in wl[1].split("\n## ", 1)[0].splitlines():
            m = re.match(r"^-\s+\*\*(.+?):?\*\*\s*(.*)$", line.strip())
            if m:
                out["watchlist"].append({"part": m.group(1).strip(), "note": m.group(2).strip()})

    # decision tree: "1. **Field is ...:** advice"
    dt = md.split("## Combo-selection decision tree", 1)
    if len(dt) > 1:
        for line in dt[1].split("\n## ", 1)[0].splitlines():
            m = re.match(r"^\d+\.\s+\*\*(.+?):?\*\*\s*(.*)$", line.strip())
            if m:
                out["decision_tree"].append({"if_field": m.group(1).strip(), "then": m.group(2).strip()})
    return out


# ---------------------------------------------------------------- front matter
def _front_matter(md):
    if not md.startswith("---"):
        return {}
    fm = md.split("---", 2)
    if len(fm) < 3:
        return {}
    out = {}
    for line in fm[1].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip('"')
    return out


# ---------------------------------------------------------------- build
def build(parts_md_path, meta_md_path=None):
    """Read the markdown file(s) and return the full parts database dict."""
    with open(parts_md_path, encoding="utf-8") as fh:
        parts_md = fh.read()
    db = parse_parts_reference(parts_md)
    db["meta"] = {"parts_snapshot": _front_matter(parts_md).get("snapshot_date")}
    if meta_md_path and os.path.exists(meta_md_path):
        with open(meta_md_path, encoding="utf-8") as fh:
            meta_md = fh.read()
        db["field_meta"] = parse_meta_guide(meta_md)
        db["meta"]["meta_snapshot"] = _front_matter(meta_md).get("snapshot_date")
    db["meta"]["counts"] = {k: len(db[k]) for k in ("blades", "ratchets", "bits")}
    return db


def write(db, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(db, fh, indent=2, ensure_ascii=False)
    return path


# ---------------------------------------------------------------- combo lookup
def split_combo(combo):
    """'Wizard Rod 1-60 Hexa' -> ('Wizard Rod', '1-60', 'Hexa'). Bit may be multi-word."""
    m = re.match(r"^(.*?)\s+(\d+-\d+|M-\d+|None)\s+(.*)$", str(combo).strip())
    if not m:
        return (str(combo).strip(), None, None)
    return (m.group(1).strip(), m.group(2), m.group(3).strip() or None)


def describe_combo(db, combo):
    """Join a combo string to the reference. Unknown parts come back as None, never raise."""
    blade, ratchet, bit = split_combo(combo)
    b = db.get("blades", {}).get(_norm(blade))
    r = db.get("ratchets", {}).get(_norm(ratchet)) if ratchet else None
    t = db.get("bits", {}).get(_norm(bit)) if bit else None
    roles = []
    for part in (b, t):
        for role in (part or {}).get("roles", []):
            if role not in roles:
                roles.append(role)
    return {
        "combo": combo, "blade": blade, "ratchet": ratchet, "bit": bit,
        "spin": (b or {}).get("spin"), "roles": roles,
        "blade_ref": b, "ratchet_ref": r, "bit_ref": t,
        "known": {"blade": b is not None, "ratchet": r is not None, "bit": t is not None},
    }


def counter_for(db, combo_or_blade):
    """Reference counter advice for a combo/blade, matched on the blade name."""
    blade = split_combo(combo_or_blade)[0]
    counters = (db.get("field_meta") or {}).get("counters", {})
    key = _norm(blade)
    if key in counters:
        return counters[key]
    for k, v in counters.items():                     # 'wizard rod' matches 'countering wizard rod'
        if key in k or k in key:
            return v
    return None


# ---------------------------------------------------------------- part issue vs skill gap
# Thresholds for the diagnosis. Peer gaps are in win% points.
SKILL_GAP_PTS = 8.0        # you trail the field by this much on the SAME combo -> skill signal
PART_OK_PTS = 3.0          # you're within this of the field -> the part is not the problem
MIN_BATTLES = 12           # below this, call it insufficient evidence rather than guess


def diagnose(db, combo, you_win_pct=None, field_avg=None, best_peer_win=None,
             battles=0, n_peers=0):
    """Is a combo underperforming because of the PART or the PILOT?

    The two questions are independent and we answer them separately:

      * peer evidence  — do others do better with the identical combo? If yes, the hardware
        demonstrably works, so the gap is execution.
      * reference evidence — does the catalog flag this part as weak//superseded/risky?

    Returns a dict with `verdict` in {skill_gap, part_issue, both, fine, insufficient_data}
    plus the reasons behind it, so the report can show its working.
    """
    info = describe_combo(db, combo)
    reasons, part_flags = [], []

    b, t, r = info["blade_ref"], info["bit_ref"], info["ratchet_ref"]
    # --- reference-side signals (is the hardware itself a liability?) ---
    for ref, label in ((b, "blade"), (t, "bit")):
        if not ref:
            continue
        read = ref.get("read") or ref.get("desc") or ""
        if re.search(r"outclassed|largely outclassed|generally outclassed", read, re.I):
            part_flags.append(f"{label} {ref['name']}: reference calls it outclassed")
        if re.search(r"self-KO", read, re.I):
            part_flags.append(f"{label} {ref['name']}: known self-KO risk")
        if re.search(r"copy-to-copy|copies|wear", read, re.I):
            part_flags.append(f"{label} {ref['name']}: copy/wear variance materially affects results")
    if r and r.get("burst_exposed"):
        part_flags.append(f"ratchet {r['name']}: burst-exposed height")
    if r and r.get("scrape_risk"):
        part_flags.append(f"ratchet {r['name']}: scrape risk")

    # --- peer-side signal (does the same combo work for other people?) ---
    peer_gap = None
    if you_win_pct is not None and field_avg is not None:
        peer_gap = round(you_win_pct - field_avg, 1)
    best_gap = None
    if you_win_pct is not None and best_peer_win is not None:
        best_gap = round(you_win_pct - best_peer_win, 1)

    if battles < MIN_BATTLES:
        verdict = "insufficient_data"
        reasons.append(f"only {battles} battles — not enough to separate part from pilot")
    elif peer_gap is None:
        # No peer ran this combo, so we cannot test the pilot. Only the catalog can speak,
        # and only if it flags something structural.
        verdict = "part_issue" if part_flags else "insufficient_data"
        reasons.append("no peer comparison available (nobody else ran this combo) — "
                       "cannot separate part from pilot on evidence")
    elif peer_gap <= -SKILL_GAP_PTS:
        verdict = "skill_gap"
        reasons.append(f"you are {abs(peer_gap):.1f} pts BELOW the field on the identical combo "
                       f"({n_peers} peers) — the hardware works for others")
        if best_peer_win is not None:
            reasons.append(f"best peer reaches {best_peer_win}% vs your {you_win_pct}%")
    elif peer_gap >= PART_OK_PTS:
        verdict = "fine"
        reasons.append(f"you are {peer_gap:+.1f} pts ABOVE the field on this combo — you pilot it well")
    else:
        # Within noise of the field: you're piloting it as well as everyone else does.
        # That is NOT evidence of a part problem — the part is performing to spec.
        verdict = "at_par"
        reasons.append(f"you are {peer_gap:+.1f} pts vs the field ({n_peers} peers) — you get the "
                       f"same result as everyone else, so neither pilot nor part stands out")

    # A combo can be piloted at/above par and STILL be the wrong hardware for the field.
    # 'Outclassed' is the only flag strong enough to change the verdict — advisory notes like
    # scrape/wear/self-KO apply to most competitive parts and would otherwise fire constantly.
    outclassed = [f for f in part_flags if "outclassed" in f]
    if verdict == "skill_gap" and outclassed:
        verdict = "both"
        reasons.append("the reference ALSO calls this hardware outclassed — "
                       "expect a limited ceiling even after practice")
    elif outclassed and verdict in ("fine", "at_par"):
        reasons.append("note: piloting is fine, but the catalog calls this hardware outclassed — "
                       "an upgrade raises the ceiling more than practice will")

    return {
        "combo": combo, "verdict": verdict, "reasons": reasons, "part_flags": part_flags,
        "outclassed": bool(outclassed),
        "peer_gap": peer_gap, "best_peer_gap": best_gap, "battles": battles, "n_peers": n_peers,
        "spin": info["spin"], "roles": info["roles"],
    }


def deck_check(db, combos):
    """Structural gaps in a 3-combo deck the win/loss data alone cannot see."""
    infos = [describe_combo(db, c) for c in combos]
    spins = [i["spin"] for i in infos if i["spin"]]
    roles = {r for i in infos for r in i["roles"]}
    issues = []
    if spins and len(set(spins)) == 1:
        issues.append(f"all {len(spins)} combos are {spins[0]}-spin — no opposite-spin equalizer "
                      f"against same-spin stamina walls")
    if "attack" not in roles:
        issues.append("no attack role in the deck — nothing to punish passive stamina builds")
    if "stamina" not in roles:
        issues.append("no stamina role — vulnerable to spin-finish losses")
    if "defense" not in roles:
        issues.append("no defense role — little insurance against heavy attack")
    burst = [i for i in infos if (i.get("ratchet_ref") or {}).get("burst_exposed")]
    if burst:
        issues.append("burst-exposed ratchets: " + ", ".join(i["combo"] for i in burst))
    return {"spins": spins, "roles": sorted(roles), "issues": issues,
            "combos": [{"combo": i["combo"], "spin": i["spin"], "roles": i["roles"]} for i in infos]}
