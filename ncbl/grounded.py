"""Grounded coaching: advice constrained to what the player actually owns.

The rule this module enforces: **never tell a player to run hardware they have not shown
they own.** A recommendation is only useful if they can act on it this weekend.

Three tiers of advice, in strict preference order:

  1. PROVEN     — a combo they have battle data on. Strongest evidence, zero acquisition cost.
  2. BUILDABLE  — a combo they have NEVER run, assembled only from Blades/Ratchets/Bits that
                  appear somewhere in their own history. They own the parts; it is a re-mix,
                  not a purchase. Confidence comes from the component track record.
  3. ACQUIRE    — hardware they do not own. Gated: only surfaced when the deck has a
                  structural hole the owned pool genuinely cannot fill (e.g. no left-spin
                  Blade at all, or nothing in an entire role). Always labelled as a purchase.

Everything else the report says — landscape, meta, gaps, strengths — is framed against that
inventory so the coaching stays actionable.
"""
from __future__ import annotations
import re
from collections import Counter, defaultdict

from . import parts_db as PDB


# ---------------------------------------------------------------- inventory
def infer_inventory(res):
    """Parts the player demonstrably owns, inferred from every combo they have run.

    Returns blades/ratchets/bits -> {part: battles}, so we can weight by experience:
    a Blade seen over 60 battles is better evidence of ownership *and* familiarity than one
    seen twice.
    """
    blades, ratchets, bits = Counter(), Counter(), Counter()
    for combo, v in (res.get("combos") or {}).items():
        b, r, t = PDB.split_combo(combo)
        n = int(v.get("battles") or 0)
        if b:
            blades[b] += n
        if r:
            ratchets[r] += n
        if t:
            bits[t] += n
    return {"blades": dict(blades), "ratchets": dict(ratchets), "bits": dict(bits),
            "combos_run": {c: (v.get("battles") or 0) for c, v in (res.get("combos") or {}).items()}}


def owns(inv, combo):
    """True when every component of `combo` appears in the player's inventory."""
    b, r, t = PDB.split_combo(combo)
    return (b in inv["blades"]) and (r in inv["ratchets"]) and (t in inv["bits"])


def has_run(inv, combo):
    return combo in inv["combos_run"]


# ---------------------------------------------------------------- landscape
def landscape(res, db=None, top=10):
    """What the player is actually facing: opponent combos by frequency, with their record
    and — where the parts DB is loaded — the spin/role make-up of the field."""
    seen = Counter()
    for s in ((res.get("prediction") or {}).get("scouting") or []):
        for d in s.get("decks_faced", []):
            for c in d.get("combos", []):
                seen[c] += d.get("times", 1)
    mu = res.get("matchups_opp") or {}
    rows = []
    for combo, n in seen.most_common(top):
        rec = mu.get(combo)
        w, l = (rec[0], rec[1]) if rec else (0, 0)
        info = PDB.describe_combo(db, combo) if db else {"spin": None, "roles": []}
        rows.append({
            "combo": combo, "times_seen": n,
            "record": f"{w}-{l}" if rec else None,
            "win_pct": (round(100 * w / (w + l)) if rec and (w + l) else None),
            "spin": info.get("spin"), "roles": info.get("roles") or [],
            "losing": bool(rec and l > w),
        })
    spins = Counter(r["spin"] for r in rows if r["spin"])
    roles = Counter(role for r in rows for role in r["roles"])
    return {"combos": rows, "spin_mix": dict(spins), "role_mix": dict(roles),
            "n_distinct": len(seen)}


# ---------------------------------------------------------------- deck gaps
def deck_gaps(res, db, deck=None):
    """Structural holes in the player's deck, judged against the field they actually face.

    Each gap carries a `fill` that respects the ownership rule: a proven or buildable option
    when one exists, otherwise an explicit acquisition flag.
    """
    rec = res.get("recommendation") or {}
    deck = deck or [d["combo"] for d in rec.get("deck", [])]
    inv = infer_inventory(res)
    check = PDB.deck_check(db, deck) if db else {"spins": [], "roles": [], "issues": []}
    land = landscape(res, db)
    gaps = []

    # --- spin coverage: only a real gap if the field actually punishes it ---
    spins = [s for s in check.get("spins") or [] if s]
    if spins and len(set(spins)) == 1:
        only = spins[0]
        opposite = "left" if only == "right" else "right"
        # can they fill it from owned parts?
        owned_opp = _owned_by_spin(res, db, inv, opposite)
        field_same = land["spin_mix"].get(only, 0)
        g = {"type": "spin", "severity": "high" if field_same >= 3 else "med",
             "text": f"Every combo in the deck is {only}-spin — no opposite-spin equalizer.",
             "why": (f"{field_same} of the {len(land['combos'])} most-common combos you face are "
                     f"also {only}-spin, so those turn into pure same-spin stamina races."),
             "fill": owned_opp}
        gaps.append(g)

    # --- role coverage, judged against what the field brings ---
    have = set(check.get("roles") or [])
    for role, label in (("attack", "attack"), ("stamina", "stamina"), ("defense", "defense")):
        if role in have:
            continue
        field_n = land["role_mix"].get(role, 0)
        gaps.append({
            "type": f"role:{role}", "severity": "high" if field_n >= 4 else "med",
            "text": f"No {label} role in the deck.",
            "why": f"{field_n} of the combos you commonly face carry a {label} role.",
            "fill": _owned_by_role(res, db, inv, role),
        })

    # --- unanswered matchups: opponents you lose to with nothing that beats them ---
    for row in land["combos"]:
        if not row["losing"]:
            continue
        ans = _owned_answer(res, row["combo"])
        gaps.append({
            "type": "matchup", "severity": "high" if row["times_seen"] >= 2 else "med",
            "text": f"Losing to {row['combo']} ({row['record']}).",
            "why": f"seen {row['times_seen']}x in your matches.",
            "fill": ans,
        })
    return {"deck": deck, "spins": spins, "roles": sorted(have), "gaps": gaps,
            "inventory": inv, "landscape": land}


def _combo_stats(res, combo):
    v = (res.get("combos") or {}).get(combo) or {}
    return {"win_pct": v.get("win_pct"), "ppb": v.get("ppb"),
            "battles": v.get("battles"), "tier": v.get("tier"), "trend": v.get("trend")}


def _owned_by_spin(res, db, inv, spin):
    """Best PROVEN combo of the requested spin; else a BUILDABLE one; else acquire."""
    if not db:
        return None
    proven = []
    for combo, v in (res.get("combos") or {}).items():
        if (PDB.describe_combo(db, combo).get("spin") == spin) and (v.get("battles") or 0) >= 8:
            proven.append((combo, v))
    if proven:
        proven.sort(key=lambda z: -(z[1].get("ppb") or 0))
        c, v = proven[0]
        return {"tier": "proven", "combo": c, "stats": _combo_stats(res, c),
                "note": f"you already run this — {v.get('win_pct')}% / {v.get('ppb'):+} over "
                        f"{v.get('battles')} battles"}
    build = _buildable(res, db, inv, spin=spin)
    if build:
        return build
    return {"tier": "acquire", "combo": None,
            "note": f"you own no {spin}-spin Blade — this gap cannot be filled from your "
                    f"current parts, so it is a purchase decision."}


def _owned_by_role(res, db, inv, role):
    if not db:
        return None
    proven = []
    for combo, v in (res.get("combos") or {}).items():
        if role in (PDB.describe_combo(db, combo).get("roles") or []) and (v.get("battles") or 0) >= 8:
            proven.append((combo, v))
    if proven:
        proven.sort(key=lambda z: -(z[1].get("ppb") or 0))
        c, v = proven[0]
        return {"tier": "proven", "combo": c, "stats": _combo_stats(res, c),
                "note": f"you already run this — {v.get('win_pct')}% / {v.get('ppb'):+} over "
                        f"{v.get('battles')} battles"}
    build = _buildable(res, db, inv, role=role)
    if build:
        return build
    return {"tier": "acquire", "combo": None,
            "note": f"nothing in your parts pool fills the {role} role — this is a purchase decision."}


def _owned_answer(res, opp_combo):
    """Your best PROVEN answer to an opponent combo, from your own pair records."""
    best = None
    for s in ((res.get("prediction") or {}).get("scouting") or []):
        for a in s.get("answers", []):
            if a.get("vs") == opp_combo:
                best = a
                break
    if best:
        return {"tier": "proven", "combo": best["bring"],
                "note": f"you are {best['record']} bringing {best['bring']} into this matchup"}
    return {"tier": "none", "combo": None,
            "note": "no combo in your history has a winning record here — this is a real hole, "
                    "not a part you are missing"}


def _buildable(res, db, inv, spin=None, role=None, min_part_battles=8):
    """A combo the player has NEVER run but CAN assemble from parts they own.

    Only proposed when every component has real battle history, so the suggestion inherits
    evidence from the parts rather than being a blind guess.
    """
    if not db:
        return None
    blades = [b for b, n in inv["blades"].items() if n >= min_part_battles]
    ratchets = [r for r, n in inv["ratchets"].items() if n >= min_part_battles]
    bits = [t for t, n in inv["bits"].items() if n >= min_part_battles]
    cands = []
    for b in blades:
        binfo = db.get("blades", {}).get(PDB._norm(b))
        if not binfo:
            continue
        if spin and binfo.get("spin") != spin:
            continue
        for t in bits:
            tinfo = db.get("bits", {}).get(PDB._norm(t))
            if not tinfo:
                continue
            roles = list(dict.fromkeys((binfo.get("roles") or []) + (tinfo.get("roles") or [])))
            if role and role not in roles:
                continue
            for r in ratchets:
                combo = f"{b} {r} {t}"
                if has_run(inv, combo):
                    continue
                # score by how much experience backs each component
                score = inv["blades"][b] + inv["ratchets"][r] + inv["bits"][t]
                cands.append((score, combo, b, r, t, roles))
    if not cands:
        return None
    cands.sort(key=lambda z: -z[0])
    _, combo, b, r, t, roles = cands[0]
    return {"tier": "buildable", "combo": combo, "roles": roles,
            "note": (f"you have never run this exact combo, but you own every part: "
                     f"{b} ({inv['blades'][b]} btl), {r} ({inv['ratchets'][r]} btl), "
                     f"{t} ({inv['bits'][t]} btl). No purchase needed — just assemble and test.")}


# ---------------------------------------------------------------- meta alignment
def meta_alignment(res, db):
    """How far the player's deck sits from the documented competitive meta.

    This is the ONLY gate that may recommend hardware they do not own: if their build is
    genuinely off-meta AND the owned pool cannot fix it, an acquisition is justified.
    """
    if not db or not db.get("field_meta"):
        return None
    rec = res.get("recommendation") or {}
    deck = [d["combo"] for d in rec.get("deck", [])]
    inv = infer_inventory(res)
    arche = (db["field_meta"].get("archetypes") or [])
    meta_blades = {PDB.split_combo(a["combo"])[0].lower() for a in arche}
    mine = {PDB.split_combo(c)[0].lower() for c in deck}
    overlap = mine & meta_blades
    owned_meta = {b for b in inv["blades"] if b.lower() in meta_blades}
    off_meta = len(overlap) == 0
    return {
        "deck_blades": sorted(mine), "meta_blades": sorted(meta_blades),
        "overlap": sorted(overlap), "owned_meta_blades": sorted(owned_meta),
        "off_meta": off_meta,
        "verdict": ("Your deck shares no Blade with the documented meta core — the one case "
                    "where new hardware is worth recommending."
                    if off_meta else
                    f"Your deck overlaps the meta core on {len(overlap)} Blade(s) "
                    f"({', '.join(sorted(overlap))}) — no acquisition needed."),
        "archetypes": arche,
    }


# ---------------------------------------------------------------- assembly
def build(res, db):
    """The grounded-coaching block attached to a coaching result."""
    if not db:
        return None
    inv = infer_inventory(res)
    land = landscape(res, db)
    gaps = deck_gaps(res, db)
    align = meta_alignment(res, db)

    # diagnose each combo: part issue vs skill gap, using peer data where present
    field = {f["combo"]: f for f in (res.get("field") or [])}
    diagnoses = []
    for combo, v in sorted((res.get("combos") or {}).items(), key=lambda z: -(z[1].get("battles") or 0)):
        f = field.get(combo)
        diagnoses.append(PDB.diagnose(
            db, combo,
            you_win_pct=(f or {}).get("you"), field_avg=(f or {}).get("field_avg"),
            best_peer_win=(f or {}).get("best_win"), battles=v.get("battles") or 0,
            n_peers=(f or {}).get("n_peers") or 0))

    strengths = [d for d in diagnoses if d["verdict"] == "fine"]
    skills = [d for d in diagnoses if d["verdict"] in ("skill_gap", "both")]
    return {
        "inventory": {"blades": inv["blades"], "ratchets": inv["ratchets"], "bits": inv["bits"],
                      "n_combos_run": len(inv["combos_run"])},
        "landscape": land, "gaps": gaps["gaps"], "deck": gaps["deck"],
        "meta_alignment": align, "diagnoses": diagnoses,
        "proven_strengths": strengths, "skill_gaps": skills,
        "policy": ("Recommendations are restricted to combos the player has run (proven) or can "
                   "assemble from parts they already own (buildable). New hardware is proposed "
                   "only when the deck is off-meta and the owned pool cannot fill the gap."),
    }
