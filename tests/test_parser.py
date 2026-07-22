"""Parser smoke test — only runs if pdfplumber and a sample report PDF are present.
Sample PDFs are NOT committed (see .gitignore); this is skipped in CI."""
import glob
import os
import pytest

pytest.importorskip("pdfplumber")

_SAMPLES = sorted(glob.glob(os.path.expanduser("~/Downloads/Espiiii*.pdf")) +
                  glob.glob(os.path.expanduser("~/Downloads/espiii*.pdf")))


@pytest.mark.skipif(not _SAMPLES, reason="no sample NCBLAST PDF available")
def test_parse_sample_has_core_sections():
    from ncbl import ncblast_parser as NP
    r = NP.parse(_SAMPLES[0])
    # a real report should yield a player and at least one combo with sane fields
    assert r["combos"], "no combos parsed"
    c = r["combos"][0]
    assert set(("combo", "battles", "win_pct", "ppb")) <= set(c)
    assert 0 <= c["win_pct"] <= 100


def test_matches_both_recap_layouts():
    """Regression: the §06 split layout ('vs Name' on its own line, result on the next)
    must parse, not just the inline 'WIN vs Name ... sets' layout. Both feed one record."""
    from ncbl.ncblast_parser import _matches
    inline = [
        "WIN vs Bobablade 2-0 sets · 7 btl · NET +8",
        "Cobalt Dragoon 9-60 Elevate 2-0 +1.20",
    ]
    split = [
        "vs Bobablade",
        "LOSS 1-2 sets · 14 btl · NET -1",
        "W-L",
        "Clock Mirage 4-55 Under Needle 4-1 -0.42",
        "vs Yagah",
        "WIN 2-1 sets · 11 btl · NET +3",
    ]
    mi = _matches(inline)
    assert len(mi) == 1 and mi[0]["opponent"] == "Bobablade" and mi[0]["result"] == "WIN"
    assert mi[0]["opp_combos"][0]["combo"].startswith("Cobalt Dragoon")

    ms = _matches(split)
    assert [m["opponent"] for m in ms] == ["Bobablade", "Yagah"]
    assert ms[0]["result"] == "LOSS" and ms[0]["sets"] == "1-2" and ms[0]["battles"] == 14
    assert ms[0]["opp_combos"][0]["wl"] == "4-1"       # opponent combo attaches to its match
    assert ms[1]["result"] == "WIN"

