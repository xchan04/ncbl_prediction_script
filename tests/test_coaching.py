"""Coaching engine: aggregation, confidence scaling, and finding generation."""
from ncbl import coaching as C


def _rep(event, combos, matchups=(), matches=(), loss=None, style=None, peers=()):
    return {
        "player": "espiiii", "event": event, "date": None, "source": event,
        "totals": {}, "archetype": "The Strategist",
        "combos": [{"combo": c[0], "win_pct": c[1], "ppb": c[2], "battles": c[3], "tier": c[4]} for c in combos],
        "finishes": {"win": {}, "loss": loss or {}},
        "matchups": [{"your_combo": m[0], "opp_combo": m[1], "faced": m[2], "wins": m[3],
                      "losses": m[4], "win_pct": 0, "ppb": 0, "net": 0} for m in matchups],
        "matches": list(matches), "peers": list(peers), "style": style or {},
    }


def _reports():
    r1 = _rep("Event A",
              combos=[("Cobalt 9-60 Elevate", 76.0, 0.7, 17, "S"),
                      ("Shark 7-70 Low Rush", 0.0, -2.5, 6, "D")],
              matchups=[("Cobalt 9-60 Elevate", "Phoenix 1-60 Rush", 4, 4, 0),
                        ("Aero 1-60 Rush", "Silver Wolf 9-60 Orb", 5, 1, 4)],
              loss={"Opp Xtreme": {"count": 6, "total_pts": 18, "pct": 42.0}},
              style={"Aggression": 30, "Efficiency": 84})
    r2 = _rep("Event B",
              combos=[("Cobalt 9-60 Elevate", 60.0, 0.2, 15, "A"),
                      ("Aero 1-60 Rush", 66.0, 0.5, 12, "A")],
              matchups=[("Cobalt 9-60 Elevate", "Silver Wolf 9-60 Orb", 4, 3, 1)],
              loss={"Opp Xtreme": {"count": 4, "total_pts": 12, "pct": 40.0}},
              style={"Aggression": 36, "Efficiency": 80})
    return [r1, r2]


def test_aggregate_battle_weights_combo():
    agg = C.aggregate(_reports(), "espiiii")
    cob = agg["combos"]["Cobalt 9-60 Elevate"]
    assert cob["battles"] == 32                      # 17 + 15
    assert cob["tier"] == "S"                         # best tier across events
    assert 60 < cob["win_pct"] < 76                   # weighted between the two events


def test_confidence_scales_with_reports():
    one = C.confidence(C.aggregate(_reports()[:1], "espiiii"))
    two = C.confidence(C.aggregate(_reports(), "espiiii"))
    assert one["unlocked"]["cross_event_trends"] is False
    assert two["unlocked"]["cross_event_trends"] is True   # >=2 events unlocks trends


def test_bad_combo_flagged_as_weakness():
    res = C.coach(_reports(), "espiiii")
    assert any("Shark 7-70 Low Rush" in w["text"] for w in res["weaknesses"])


def test_finish_vulnerability_surfaces():
    res = C.coach(_reports(), "espiiii")
    assert any(w["type"] == "finish" and "Xtreme" in w["text"] for w in res["weaknesses"])


def test_losing_matchup_gets_a_swap():
    res = C.coach(_reports(), "espiiii")
    # espiiii is 4-4 vs Silver Wolf overall but Cobalt is 3-0 vs it -> should suggest Cobalt
    swap = next((s for s in res["swaps"] if "Silver Wolf" in s["opp"]), None)
    assert swap and "Cobalt 9-60 Elevate" in swap["suggestion"]


def test_renders_all_three_formats(tmp_path):
    from ncbl.config import load_config
    res = C.coach(_reports(), "espiiii")
    paths = C.write_all(res, load_config(), str(tmp_path / "coach"))
    assert sorted(p.rsplit(".", 1)[1] for p in paths) == ["html", "json", "txt"]
    assert "espiiii" in C.coach_html(res, load_config())
