"""Schema-agnostic JSON report parser: renamed keys, nesting, and canonical passthrough."""
import json

from ncbl import ncblast_json as J
from ncbl import coaching as C


def _write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return str(p)


# A report whose keys / nesting differ from ours on purpose — nothing here matches
# the PDF parser's field names, so it exercises the alias + shape heuristics.
RENAMED = {
    "user": "espiiii",
    "tournamentName": "Renamed Open",
    "playedOn": "June 28, 2026",
    "overall": {"won": 12, "lost": 8, "winRate": 0.60, "pointsPerBattle": 0.35,
                "place": "5th", "gamesPlayed": 20},
    "loadouts": [
        {"build": "Cobalt Dragoon 9-60 Elevate", "gamesPlayed": 17, "winPercent": 76.0, "ppbAvg": 0.7, "grade": "S"},
        {"build": "Shark Scale 7-70 Low Rush", "gamesPlayed": 6, "winPercent": 0, "ppbAvg": -2.5, "grade": "D"},
    ],
    "matchupMatrix": [
        {"self": "Cobalt Dragoon 9-60 Elevate", "vsCombo": "Silver Wolf 9-60 Orb", "won": 3, "lost": 0},
        {"self": "Aero 1-60 Rush", "vsCombo": "Silver Wolf 9-60 Orb", "won": 1, "lost": 4},
    ],
    "peerCompare": [
        {"combo": "Cobalt Dragoon 9-60 Elevate", "field": [
            {"handle": "YOU", "winRate": 60.0, "gamesPlayed": 17},
            {"handle": "rivalA", "winRate": 76.0, "gamesPlayed": 12},
        ]},
    ],
    "finishBreakdown": {"scored": {"Xtreme": {"count": 5}},
                        "allowed": {"Opp Xtreme": {"count": 6, "percent": 42.0}}},
    "styleFingerprint": {"efficiency": 84, "aggression": 30},
    "battleDynamics": {"B": {"winRate": 63.2, "gamesPlayed": 38, "ppbAvg": 0.18},
                       "X": {"winRate": 55.0, "gamesPlayed": 20, "ppbAvg": 0.30}},
    "matchHistory": [
        {"outcome": "LOSS", "opponentName": "Bongo", "setScore": "0-2",
         "combosFaced": [{"build": "Wizard Rod 3-70 Attack", "winLoss": "2-0", "ppb": 1.0}]},
    ],
}


def test_renamed_json_maps_core_fields(tmp_path):
    rep = J.parse(_write(tmp_path, "r.json", RENAMED))
    assert rep["player"] == "espiiii"
    assert rep["event"] == "Renamed Open"
    assert rep["totals"]["win_pct"] == 60.0          # 0.60 fraction -> percent
    assert rep["totals"]["placement"] == "5th"
    cobalt = next(c for c in rep["combos"] if "Cobalt" in c["combo"])
    assert cobalt["battles"] == 17 and cobalt["win_pct"] == 76.0 and cobalt["tier"] == "S"
    shark = next(c for c in rep["combos"] if "Shark" in c["combo"])
    assert shark["ppb"] == -2.5


def test_renamed_json_maps_collections(tmp_path):
    rep = J.parse(_write(tmp_path, "r.json", RENAMED))
    mu = next(m for m in rep["matchups"] if "Silver Wolf" in m["opp_combo"] and "Cobalt" in m["your_combo"])
    assert (mu["wins"], mu["losses"]) == (3, 0)
    assert rep["finishes"]["loss"]["Opp Xtreme"]["count"] == 6
    assert rep["style"]["Efficiency"] == 84
    assert rep["dynamics"]["side"]["B"] == {"win_pct": 63.2, "battles": 38, "ppb": 0.18}
    assert rep["dynamics"]["side"]["X"]["win_pct"] == 55.0
    peer_you = next(p for p in rep["peers"] if p["player"] == "YOU")
    assert peer_you["win_pct"] == 60.0
    match = rep["matches"][0]
    assert match["result"] == "LOSS" and match["opponent"] == "Bongo"
    assert match["opp_combos"][0]["combo"] == "Wizard Rod 3-70 Attack"
    assert match["opp_combos"][0]["wl"] == "2-0"


def test_renamed_json_flows_through_coaching(tmp_path):
    C.load_reports  # sanity
    reps = C.load_reports([_write(tmp_path, "r.json", RENAMED)])
    assert reps and reps[0]["combos"]
    res = C.coach(reps, "espiiii")
    # launch gap (63.2 vs 55.0) surfaces as a positioning weakness
    assert res["launch"]["gap"] == 8.2
    assert any(w["type"] == "launch" for w in res["weaknesses"])
    # the -2.5 combo is benched in the recommendation
    assert any("Shark" in b["combo"] for b in res["recommendation"]["bench"])
    # field benchmark picks up the peer row
    assert any("Cobalt" in f["combo"] for f in res["field"])


def test_canonical_shape_passthrough(tmp_path):
    # JSON that already matches our own parsed shape must round-trip cleanly
    canonical = {
        "player": "espiiii", "event": "Canon Cup", "date": "July 1, 2026",
        "totals": {"wins": 10, "losses": 5, "win_pct": 66.7, "ppb": 0.4, "placement": "3rd", "total_battles": 15},
        "combos": [{"combo": "Aero 1-60 Rush", "battles": 15, "win_pct": 66.7, "ppb": 0.4, "tier": "A"}],
        "matchups": [{"your_combo": "Aero 1-60 Rush", "opp_combo": "Phoenix 1-60 Rush",
                      "faced": 4, "wins": 3, "losses": 1, "win_pct": 75.0, "ppb": 0.5, "net": 2}],
        "peers": [{"combo": "Aero 1-60 Rush", "player": "YOU", "win_pct": 66.7, "ppb": 0.4, "battles": 15}],
        "finishes": {"win": {}, "loss": {"Opp Over": {"count": 3, "total_pts": 9, "pct": 60.0}}},
        "style": {"Efficiency": 80}, "archetype": "The Strategist",
        "dynamics": {"side": {"B": {"win_pct": 70.0, "battles": 8, "ppb": 0.5},
                              "X": {"win_pct": 62.0, "battles": 7, "ppb": 0.3}}, "points_dist": {}},
        "matches": [{"result": "WIN", "opponent": "Kai", "sets": "2-0", "battles": 3, "net": 2,
                     "opp_combos": [{"combo": "Phoenix 1-60 Rush", "wl": "0-2", "match_ppb": 0.1}]}],
    }
    rep = J.parse(_write(tmp_path, "c.json", canonical))
    assert rep["player"] == "espiiii" and rep["event"] == "Canon Cup"
    assert rep["combos"][0]["combo"] == "Aero 1-60 Rush" and rep["combos"][0]["tier"] == "A"
    assert rep["matchups"][0]["opp_combo"] == "Phoenix 1-60 Rush"
    assert rep["finishes"]["loss"]["Opp Over"]["count"] == 3
    assert rep["dynamics"]["side"]["B"]["win_pct"] == 70.0
    assert rep["matches"][0]["opponent"] == "Kai"


def test_missing_and_garbage_degrade_gracefully(tmp_path):
    # a nearly empty / unrecognizable file yields empty sections, never an exception
    rep = J.parse(_write(tmp_path, "empty.json", {"hello": "world", "nested": {"nothing": [1, 2, 3]}}))
    assert rep["combos"] == [] and rep["matchups"] == [] and rep["matches"] == []
    assert rep["finishes"] == {"win": {}, "loss": {}}
    assert rep["dynamics"]["side"] == {}
