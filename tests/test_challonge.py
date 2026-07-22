"""Challonge head-to-head: slug parsing, match parsing, and H2H aggregation.
Uses an in-memory mock of the Challonge API JSON (no network)."""
from ncbl import challonge as CH


def test_slug_from_url():
    assert CH.slug_from_url("https://ncbl.challonge.com/goonday") == "ncbl-goonday"
    assert CH.slug_from_url("ncbl.challonge.com/SRSv10/standings") == "ncbl-SRSv10"
    assert CH.slug_from_url("https://challonge.com/abcd1234") == "abcd1234"


def test_slugs_from_file_txt_md_json(tmp_path):
    txt = tmp_path / "links.txt"
    txt.write_text("# my brackets\nhttps://ncbl.challonge.com/goonday\n\nncbl.challonge.com/rfv/standings\nabcd1234\n")
    assert CH.slugs_from_file(str(txt)) == ["ncbl-goonday", "ncbl-rfv", "abcd1234"]

    md = tmp_path / "links.md"
    md.write_text("- Event 1: [bracket](https://ncbl.challonge.com/goonday)\n- Event 2: https://challonge.com/xy12\n")
    assert CH.slugs_from_file(str(md)) == ["ncbl-goonday", "xy12"]

    js = tmp_path / "links.json"
    js.write_text('{"links": ["https://ncbl.challonge.com/goonday", "wxyz9"]}')
    assert CH.slugs_from_file(str(js)) == ["ncbl-goonday", "wxyz9"]



def _mock():
    # Challonge API v1 shape
    return {"tournament": {"name": "GoonDay",
            "participants": [{"participant": {"id": 1, "name": "espiiii"}},
                             {"participant": {"id": 2, "name": "bongo"}},
                             {"participant": {"id": 3, "name": "Teefoh"}}],
            "matches": [
                {"match": {"player1_id": 1, "player2_id": 2, "winner_id": 2, "scores_csv": "1-2"}},
                {"match": {"player1_id": 2, "player2_id": 1, "winner_id": 2, "scores_csv": "2-0"}},
                {"match": {"player1_id": 1, "player2_id": 3, "winner_id": 1, "scores_csv": "2-1"}},
                {"match": {"player1_id": 3, "player2_id": 1, "winner_id": 1, "scores_csv": "2-0"}},
                {"match": {"player1_id": 2, "player2_id": 3, "winner_id": 2}},  # not espiiii
            ]}}


def test_parse_and_head_to_head():
    t = CH.parse_tournament(_mock())
    assert t["name"] == "GoonDay"
    assert len(t["matches"]) == 5
    a = CH.analyze([t], "espiiii")
    h = {x["opponent"]: x for x in a["h2h"]}
    assert h["bongo"]["wins"] == 0 and h["bongo"]["losses"] == 2      # bongo beats espiiii twice
    assert h["Teefoh"]["wins"] == 2 and h["Teefoh"]["losses"] == 0
    assert any(n["opponent"] == "bongo" for n in a["nemeses"])         # nemesis surfaced
    assert any(o["opponent"] == "Teefoh" for o in a["owned"])


def test_name_normalization_matches_casing():
    t = CH.parse_tournament(_mock())
    a = CH.analyze([t], "ESPIIII")   # different casing still resolves
    assert a["h2h"] and sum(x["played"] for x in a["h2h"]) == 4


def test_renders_three_formats(tmp_path):
    from ncbl.config import load_config
    a = CH.analyze([CH.parse_tournament(_mock())], "espiiii")
    paths = CH.write_all(a, load_config(), str(tmp_path / "h2h"))
    assert sorted(p.rsplit(".", 1)[1] for p in paths) == ["html", "json", "txt"]
