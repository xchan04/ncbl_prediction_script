"""Parts database: markdown parsing, combo lookup, and the part-issue vs skill-gap diagnosis."""
import textwrap

from ncbl import parts_db as P

PARTS_MD = textwrap.dedent("""\
    ---
    title: "Beyblade X Complete Parts Reference"
    snapshot_date: "2026-08-02"
    ---

    ## Released and announced Blade assemblies

    | Blade assembly | System | Spin | Intended role | Detailed description and competitive read |
    |---|---|---:|---|---|
    | Wizard Rod | UX | Right | Stamina/Defense | Large outward-weighted circular Blade with exceptional same-spin stamina. |
    | Cobalt Dragoon | UX | Left | Attack/Balance | The first major left-spin X Blade, combining smash with opposite-spin equalization. |
    | Wizard Arrow | BX | Right | Stamina | Smooth circular profile; largely outclassed for open-format stamina. |
    | Shark Scale | UX | Right | Attack/Stamina | Heavy low-profile contact. Self-KO risk and contact wear are real. |

    ## Ratchets

    | Ratchet | Status | Detailed description |
    |---|---|---|
    | 1-60 | Released | Asymmetric one-lobe profile; may add wobble or scrape. Standard low height. |
    | 9-60 | Released | Nine-sided, almost circular; among the safest for burst resistance. Standard low height. |
    | 4-80 | Released | Four-sided profile; commonly more burst-exposed. Tall height; sharply raises burst and tipping risk. |

    ## Bits

    | Code | Bit | Intended role | Detailed description and tradeoffs |
    |---|---|---|---|
    | H | Hexa | Defense/Balance | Hexagonal tip provides high burst resistance, though pure stamina Bits can outspin it. |
    | E | Elevate | Balance/Stamina | Tall wide tip with exceptional opposite-spin life-after-death. Copies vary; wear matters. |
    | LR | Low Rush | Attack | Lower Rush variant; top meta attack Bit but can self-KO and wear noticeably. |
    """)

META_MD = textwrap.dedent("""\
    ---
    title: "Beyblade X Current Meta and Counter Guide"
    snapshot_date: "2026-08-02"
    ---

    ## Established meta core

    | Archetype | Representative combo | Why it is meta | Main weaknesses | Confidence |
    |---|---|---|---|---|
    | Stamina/defense | **Wizard Rod 1-60 Low Orb** | Excellent same-spin stamina. | Left-spin equalizers. | High |

    ### Countering Wizard Rod

    **Best broad answer: Meteor Dragoon 9-60 Elevate.**

    Left spin turns late-game contact into equalization.

    ### Countering Clock Mirage

    **Use controlled attack, not more passive stamina.**

    ## New-release watchlist

    - **Dran Strike:** already has meaningful winning-combo evidence.

    ## Combo-selection decision tree

    1. **Field is mostly Wizard Rod/passive stamina:** lead Meteor Dragoon Elevate.

    ## Confidence-tagged claims for machine training

    | Claim | Confidence | Basis |
    |---|---|---|
    | Wizard Rod remains a central stamina/defense Blade. | High | Tournament representation. |
    """)


def _db(tmp_path):
    p = tmp_path / "parts.md"; p.write_text(PARTS_MD)
    m = tmp_path / "meta.md"; m.write_text(META_MD)
    return P.build(str(p), str(m))


def test_parses_blades_ratchets_bits(tmp_path):
    db = _db(tmp_path)
    assert db["meta"]["counts"] == {"blades": 4, "ratchets": 3, "bits": 3}
    wr = db["blades"]["wizard rod"]
    assert wr["spin"] == "right" and "stamina" in wr["roles"] and "defense" in wr["roles"]
    assert db["blades"]["cobalt dragoon"]["spin"] == "left"      # the key strategic axis


def test_ratchet_geometry_and_flags(tmp_path):
    db = _db(tmp_path)
    assert db["ratchets"]["9-60"]["sides"] == 9
    assert db["ratchets"]["9-60"]["height_mm"] == 6.0
    assert db["ratchets"]["9-60"]["burst_safe"] is True
    assert db["ratchets"]["4-80"]["burst_exposed"] is True
    assert db["ratchets"]["1-60"]["scrape_risk"] is True


def test_bit_flags(tmp_path):
    db = _db(tmp_path)
    assert db["bits"]["hexa"]["burst_resistant"] is True
    assert db["bits"]["low rush"]["self_ko_risk"] is True
    assert db["bits"]["elevate"]["copy_variance"] is True


def test_split_and_describe_combo(tmp_path):
    db = _db(tmp_path)
    assert P.split_combo("Wizard Rod 1-60 Hexa") == ("Wizard Rod", "1-60", "Hexa")
    assert P.split_combo("Clock Mirage 4-55 Under Needle")[2] == "Under Needle"   # multi-word bit
    d = P.describe_combo(db, "Wizard Rod 1-60 Hexa")
    assert d["spin"] == "right" and all(d["known"].values())
    unknown = P.describe_combo(db, "Nonexistent Blade 9-60 Hexa")
    assert unknown["known"]["blade"] is False and unknown["known"]["bit"] is True   # degrades


def test_meta_guide_counters_and_claims(tmp_path):
    db = _db(tmp_path)
    fm = db["field_meta"]
    assert fm["counters"]["wizard rod"]["best_answer"] == "Meteor Dragoon 9-60 Elevate"
    assert fm["counters"]["clock mirage"]["best_answer"].startswith("Use controlled attack")
    assert fm["claims"] and fm["claims"][0]["confidence"] == "High"
    assert fm["watchlist"] and fm["decision_tree"]


def test_counter_for_matches_on_blade(tmp_path):
    db = _db(tmp_path)
    c = P.counter_for(db, "Wizard Rod 1-60 Hexa")      # full combo -> blade-level counter
    assert c["best_answer"] == "Meteor Dragoon 9-60 Elevate"
    assert P.counter_for(db, "Totally Unknown 1-60 Hexa") is None


# ---------------- the headline feature ----------------
def test_diagnose_skill_gap(tmp_path):
    """Peers do much better on the identical combo -> the pilot, not the part."""
    db = _db(tmp_path)
    d = P.diagnose(db, "Wizard Rod 1-60 Hexa", you_win_pct=45.0, field_avg=59.3,
                   best_peer_win=84.6, battles=46, n_peers=35)
    assert d["verdict"] == "skill_gap"
    assert any("BELOW the field" in r for r in d["reasons"])


def test_diagnose_at_par_is_not_a_part_issue(tmp_path):
    """Being a few points off the field is noise — it must NOT be called a part issue."""
    db = _db(tmp_path)
    d = P.diagnose(db, "Wizard Rod 1-60 Hexa", you_win_pct=54.4, field_avg=59.3,
                   best_peer_win=84.6, battles=46, n_peers=35)
    assert d["verdict"] == "at_par"


def test_diagnose_fine_when_above_field(tmp_path):
    db = _db(tmp_path)
    d = P.diagnose(db, "Shark Scale 9-60 Low Rush", you_win_pct=68.3, field_avg=53.3,
                   best_peer_win=66.7, battles=63, n_peers=3)
    assert d["verdict"] == "fine"


def test_diagnose_insufficient_data(tmp_path):
    db = _db(tmp_path)
    d = P.diagnose(db, "Wizard Rod 1-60 Hexa", you_win_pct=40.0, field_avg=59.3, battles=4, n_peers=3)
    assert d["verdict"] == "insufficient_data"


def test_diagnose_outclassed_part_is_flagged_even_when_piloting_is_fine(tmp_path):
    """Piloting at par + catalog says outclassed -> tell them hardware caps the ceiling."""
    db = _db(tmp_path)
    d = P.diagnose(db, "Wizard Arrow 9-60 Hexa", you_win_pct=55.0, field_avg=54.0,
                   best_peer_win=60.0, battles=30, n_peers=5)
    assert d["outclassed"] is True
    assert any("outclassed" in r for r in d["reasons"])


def test_deck_check_flags_all_same_spin(tmp_path):
    db = _db(tmp_path)
    d = P.deck_check(db, ["Wizard Rod 1-60 Hexa", "Shark Scale 9-60 Low Rush", "Wizard Arrow 9-60 Hexa"])
    assert d["spins"] == ["right", "right", "right"]
    assert any("opposite-spin" in i for i in d["issues"])


def test_deck_check_passes_mixed_spin(tmp_path):
    db = _db(tmp_path)
    d = P.deck_check(db, ["Shark Scale 9-60 Low Rush", "Cobalt Dragoon 1-60 Elevate", "Wizard Rod 9-60 Hexa"])
    assert "left" in d["spins"] and "right" in d["spins"]
    assert not any("opposite-spin" in i for i in d["issues"])
