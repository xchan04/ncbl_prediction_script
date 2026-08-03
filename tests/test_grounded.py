"""Grounded coaching: advice must stay inside the parts the player owns."""
import json, os, textwrap
from ncbl import grounded as G, parts_db as PDB

PARTS_MD = textwrap.dedent("""\
    ---
    snapshot_date: "2026-08-02"
    ---
    ## Released and announced Blade assemblies

    | Blade assembly | System | Spin | Intended role | Detailed description and competitive read |
    |---|---|---:|---|---|
    | Wizard Rod | UX | Right | Stamina/Defense | Big circular stamina Blade. |
    | Cobalt Dragoon | UX | Left | Attack/Balance | Left-spin equalizer. |
    | Shark Scale | UX | Right | Attack/Stamina | Heavy low attack. |
    | Meteor Dragoon | UX | Left | Stamina/Defense | The best modern left-spin choice. |

    ## Ratchets

    | Ratchet | Status | Detailed description |
    |---|---|---|
    | 1-60 | Released | Asymmetric one-lobe. Standard low height. |
    | 9-60 | Released | Nine-sided; burst resistance. Standard low height. |

    ## Bits

    | Code | Bit | Intended role | Detailed description and tradeoffs |
    |---|---|---|---|
    | H | Hexa | Defense/Balance | High burst resistance. |
    | FB | Free Ball | Stamina/Defense | Free-rotating ball. |
    | E | Elevate | Balance/Stamina | Opposite-spin life-after-death. |
    """)

META_MD = textwrap.dedent("""\
    ---
    snapshot_date: "2026-08-02"
    ---
    ## Established meta core

    | Archetype | Representative combo | Why it is meta | Main weaknesses | Confidence |
    |---|---|---|---|---|
    | Stamina/defense | **Wizard Rod 1-60 Low Orb** | Stamina. | Left-spin. | High |
    | Left-spin equalizer | **Meteor Dragoon 9-60 Elevate** | Equalization. | Same-spin left. | High |
    """)


def _db(tmp_path):
    p = tmp_path / "p.md"; p.write_text(PARTS_MD)
    m = tmp_path / "m.md"; m.write_text(META_MD)
    return PDB.build(str(p), str(m))


def _res(combos, deck=None, scouting=None, matchups=None):
    return {
        "player": "me", "combos": combos,
        "recommendation": {"deck": [{"combo": c} for c in (deck or list(combos)[:3])]},
        "prediction": {"scouting": scouting or []},
        "matchups_opp": matchups or {}, "field": [],
    }


ALL_RIGHT = {
    "Wizard Rod 1-60 Hexa": {"win_pct": 55.0, "ppb": 0.1, "battles": 40, "tier": "B"},
    "Shark Scale 9-60 Free Ball": {"win_pct": 68.0, "ppb": 0.5, "battles": 60, "tier": "S"},
}


def test_inventory_is_inferred_from_combos_run():
    inv = G.infer_inventory(_res(ALL_RIGHT))
    assert inv["blades"] == {"Wizard Rod": 40, "Shark Scale": 60}
    assert inv["ratchets"] == {"1-60": 40, "9-60": 60}
    assert set(inv["bits"]) == {"Hexa", "Free Ball"}


def test_owns_and_has_run():
    inv = G.infer_inventory(_res(ALL_RIGHT))
    assert G.has_run(inv, "Wizard Rod 1-60 Hexa") is True
    # never run, but every part is owned -> buildable territory
    assert G.has_run(inv, "Wizard Rod 9-60 Free Ball") is False
    assert G.owns(inv, "Wizard Rod 9-60 Free Ball") is True
    assert G.owns(inv, "Meteor Dragoon 9-60 Elevate") is False       # blade not owned


def test_spin_gap_filled_by_owned_proven_combo(tmp_path):
    """Player owns a left-spin combo -> the fill must be PROVEN, never an acquisition."""
    db = _db(tmp_path)
    combos = dict(ALL_RIGHT)
    combos["Cobalt Dragoon 1-60 Elevate"] = {"win_pct": 60.0, "ppb": 0.3, "battles": 25, "tier": "A"}
    res = _res(combos, deck=list(ALL_RIGHT))
    gaps = G.deck_gaps(res, db)
    spin = [g for g in gaps["gaps"] if g["type"] == "spin"]
    assert spin, "all-right-spin deck should raise a spin gap"
    assert spin[0]["fill"]["tier"] == "proven"
    assert spin[0]["fill"]["combo"] == "Cobalt Dragoon 1-60 Elevate"


def test_spin_gap_becomes_acquire_when_no_left_spin_owned(tmp_path):
    """No left-spin Blade anywhere in history -> honestly labelled a purchase."""
    db = _db(tmp_path)
    res = _res(ALL_RIGHT)
    gaps = G.deck_gaps(res, db)
    spin = [g for g in gaps["gaps"] if g["type"] == "spin"][0]
    assert spin["fill"]["tier"] == "acquire"
    assert "purchase" in spin["fill"]["note"]


def test_buildable_uses_only_owned_parts(tmp_path):
    db = _db(tmp_path)
    res = _res(ALL_RIGHT)
    inv = G.infer_inventory(res)
    b = G._buildable(res, db, inv)
    assert b is not None and b["tier"] == "buildable"
    assert G.owns(inv, b["combo"]) is True          # the key invariant
    assert G.has_run(inv, b["combo"]) is False      # and it is genuinely new
    assert "No purchase needed" in b["note"]


def test_landscape_counts_what_you_face(tmp_path):
    db = _db(tmp_path)
    scouting = [{"opponent": "X", "decks_faced": [
        {"combos": ["Wizard Rod 1-60 Hexa", "Shark Scale 9-60 Free Ball"], "times": 2}]}]
    res = _res(ALL_RIGHT, scouting=scouting,
               matchups={"Wizard Rod 1-60 Hexa": [1, 5]})
    land = G.landscape(res, db)
    assert land["spin_mix"] == {"right": 2}
    wr = [c for c in land["combos"] if c["combo"] == "Wizard Rod 1-60 Hexa"][0]
    assert wr["losing"] is True and wr["times_seen"] == 2


def test_meta_alignment_flags_off_meta(tmp_path):
    db = _db(tmp_path)
    on = G.meta_alignment(_res(ALL_RIGHT), db)          # Wizard Rod IS meta core
    assert on["off_meta"] is False and "wizard rod" in on["overlap"]


def test_build_attaches_policy_and_sections(tmp_path):
    db = _db(tmp_path)
    g = G.build(_res(ALL_RIGHT), db)
    assert "owned" in g["policy"]
    for k in ("inventory", "landscape", "gaps", "meta_alignment", "diagnoses"):
        assert k in g
