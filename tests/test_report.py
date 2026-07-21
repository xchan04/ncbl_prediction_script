"""Report packaging: build() -> txt / json / html all render and round-trip."""
import json

from ncbl import report as R


def test_build_has_expected_keys(league, cfg):
    d = R.build(league, cfg, "espiiii", target_rank=3, remaining=2, top=10)
    for k in ("player", "current_rank", "current_score", "predictions", "threats", "standings", "field_size"):
        assert k in d
    assert d["player"] == "espiiii"
    assert isinstance(d["predictions"], list) and d["predictions"]
    assert {"overtook", "live"} <= set(d["threats"])


def test_json_round_trips(league, cfg):
    d = R.build(league, cfg, "espiiii", target_rank=3, remaining=2)
    s = R.to_json(d)
    assert json.loads(s)["player"] == "espiiii"


def test_txt_and_html_render(league, cfg):
    d = R.build(league, cfg, "espiiii", target_rank=3, remaining=2)
    txt = R.to_txt(d)
    html = R.to_html(d, cfg)
    assert "espiiii" in txt and "Target: Top 3" in txt
    assert html.lstrip().startswith("<!doctype html>")
    assert cfg["theme"]["player"] in html          # orange accent present
    assert "espiiii" in html


def test_write_all_creates_three_files(league, cfg, tmp_path):
    d = R.build(league, cfg, "espiiii", target_rank=3, remaining=2)
    paths = R.write_all(d, cfg, str(tmp_path / "rep"))
    exts = sorted(p.rsplit(".", 1)[1] for p in paths)
    assert exts == ["html", "json", "txt"]
    for p in paths:
        assert (tmp_path / p.split("/")[-1]).exists()


def test_h2h_annotation_wires_in(league, cfg):
    h2h = [{"opponent": "Bea", "wins": 1, "losses": 4, "win_pct": 20.0}]
    d = R.build(league, cfg, "espiiii", target_rank=3, remaining=2, h2h=h2h)
    assert d["has_h2h"] is True
    # any rival matching the h2h name carries the record; non-matches stay None
    all_rivals = d["threats"]["overtook"] + d["threats"]["live"]
    for r in all_rivals:
        if r["player"].lower() == "bea":
            assert r["h2h"]["record"] == "1-4"


def test_no_h2h_is_graceful(league, cfg):
    d = R.build(league, cfg, "espiiii", target_rank=3, remaining=2)
    assert d["has_h2h"] is False
    assert all(r["h2h"] is None for r in d["threats"]["live"])
