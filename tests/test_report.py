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
