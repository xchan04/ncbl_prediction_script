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
