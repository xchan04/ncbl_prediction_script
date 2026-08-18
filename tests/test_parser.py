"""Parser smoke test — only runs if pdfplumber and a sample report PDF are present.
Sample PDFs are NOT committed (see .gitignore); this is skipped in CI."""
import glob
import os
import pytest

pytest.importorskip("pdfplumber")

from ncbl import ncblast_parser as P    # noqa: E402  (after importorskip)

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



# ---------------- per-combo finish breakdown (§03) ----------------
class _W:
    """Minimal stand-in for a pdfplumber word dict."""
    @staticmethod
    def make(text, x0, top):
        return {"text": text, "x0": x0, "x1": x0 + 10 * len(text), "top": top, "bottom": top + 9}


def _finish_page_words():
    """Three columns at x=52/232/412, entry labels indented to 114/294/474 — the real layout.
    Column 2 deliberately includes 'Own (opp KO)', which used to break the checksum."""
    w, y = [], 100.0
    cols = [
        ("Wizard Rod 1-60 Free Ball", 52, 114,
         [("SCORED", "13")], [("Spin", 8, 40.0), ("Over", 3, 30.0), ("Xtreme", 2, 30.0)],
         [("ALLOWED", "10")], [("Opp Spin", 4, 40.0), ("Opp Over", 3, 30.0), ("Own (self-KO)", 3, 30.0)]),
        ("Aero Pegasus 1-60 Rush", 232, 294,
         [("SCORED", "6")], [("Own (opp KO)", 1, 20.0), ("Spin", 4, 60.0), ("Burst", 1, 20.0)],
         [("ALLOWED", "4")], [("Opp Spin", 4, 100.0)]),
        ("Cobalt Dragoon 5-60 Elevate", 412, 474,
         [("SCORED", "5")], [("Spin", 5, 100.0)],
         [("ALLOWED", "2")], [("Opp Over", 2, 100.0)]),
    ]
    for name, cx, ex, sc_hdr, sc, al_hdr, al in cols:
        yy = y
        w.append(_W.make(name, cx + 4, yy)); yy += 12
        w.append(_W.make("SCORED", cx, yy)); w.append(_W.make(sc_hdr[0][1], cx + 60, yy)); yy += 12
        for lbl, n, pct in sc:
            w.append(_W.make(lbl, ex, yy)); w.append(_W.make(f"{n} · {pct}%", ex + 70, yy)); yy += 12
        w.append(_W.make("ALLOWED", cx, yy)); w.append(_W.make(al_hdr[0][1], cx + 60, yy)); yy += 12
        for lbl, n, pct in al:
            w.append(_W.make(lbl, ex, yy)); w.append(_W.make(f"{n} · {pct}%", ex + 70, yy)); yy += 12
    return w


def test_combo_finishes_splits_three_columns(monkeypatch):
    pages = ["cover", "overview", "combos", "Combo Finish Breakdown\nTOP 3 COMBOS"]
    monkeypatch.setattr(P, "_pages_words", lambda path: [[], [], [], _finish_page_words()])
    out = P._combo_finishes("x.pdf", pages)
    assert set(out) == {"Wizard Rod 1-60 Free Ball", "Aero Pegasus 1-60 Rush",
                        "Cobalt Dragoon 5-60 Elevate"}
    wr = out["Wizard Rod 1-60 Free Ball"]
    assert wr["scored"]["Spin"]["count"] == 8 and wr["scored_total"] == 13
    assert wr["allowed"]["Opp Over"]["count"] == 3 and wr["allowed_total"] == 10


def test_combo_finishes_handles_own_opp_ko(monkeypatch):
    """'Own (opp KO)' is a WIN the opponent gave away; it must count toward SCORED."""
    pages = ["", "", "", "Combo Finish Breakdown"]
    monkeypatch.setattr(P, "_pages_words", lambda path: [[], [], [], _finish_page_words()])
    ap = P._combo_finishes("x.pdf", pages)["Aero Pegasus 1-60 Rush"]
    assert ap["scored"]["Own (opp KO)"]["count"] == 1
    assert ap["gifted"] == 1          # unearned points, tracked separately
    assert ap["self_ko"] == 0


def test_combo_finishes_tracks_self_ko(monkeypatch):
    pages = ["", "", "", "Combo Finish Breakdown"]
    monkeypatch.setattr(P, "_pages_words", lambda path: [[], [], [], _finish_page_words()])
    wr = P._combo_finishes("x.pdf", pages)["Wizard Rod 1-60 Free Ball"]
    assert wr["self_ko"] == 3


def test_combo_finishes_drops_column_failing_checksum(monkeypatch):
    """A column whose entries do not sum to the printed total is dropped, not reported wrong."""
    words = [w for w in _finish_page_words()
             if not (w["x0"] in (474, 544) and "5" in w["text"] and "·" in w["text"])]
    pages = ["", "", "", "Combo Finish Breakdown"]
    monkeypatch.setattr(P, "_pages_words", lambda path: [[], [], [], words])
    out = P._combo_finishes("x.pdf", pages)
    assert "Cobalt Dragoon 5-60 Elevate" not in out      # 0 != SCORED 5
    assert "Wizard Rod 1-60 Free Ball" in out            # unaffected columns survive


def test_combo_finishes_absent_section_returns_empty(monkeypatch):
    """Older report templates have no such section — and page 6 'SCORED' in the Points
    Distribution table must NOT be mistaken for it."""
    pages = ["cover", "overview", "", "Battle Dynamics", "", "COMBO SCORED ALLOW NET\nAero 23 17 +6"]
    monkeypatch.setattr(P, "_pages_words", lambda path: [[] for _ in pages])
    assert P._combo_finishes("x.pdf", pages) == {}
