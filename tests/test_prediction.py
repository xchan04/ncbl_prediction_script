"""Opponent prediction / shuffle readout, self-scout, meta-counter, and the hidden community backdoor."""
from ncbl import coaching as C


def _rep(event, combos, matches, matchups=()):
    return {
        "player": "me", "event": event, "date": None, "source": event, "totals": {}, "archetype": None,
        "combos": [{"combo": c[0], "win_pct": c[1], "ppb": c[2], "battles": c[3], "tier": c[4]} for c in combos],
        "finishes": {"win": {}, "loss": {}},
        "matchups": [{"your_combo": m[0], "opp_combo": m[1], "faced": m[2], "wins": m[3], "losses": m[4],
                      "win_pct": 0, "ppb": 0, "net": 0} for m in matchups],
        "matches": list(matches), "peers": [], "style": {}, "dynamics": {"side": {}, "points_dist": {}},
    }


def _m(result, opp, combos):
    return {"result": result, "opponent": opp, "sets": "2-0" if result == "WIN" else "0-2",
            "battles": 6, "net": 0, "opp_combos": [{"combo": c, "wl": "0-2", "match_ppb": 0.0} for c in combos]}


def _scout(res, opp):
    return next((s for s in res["prediction"]["scouting"] if s["opponent"] == opp), None)


def test_locked_opponent_shows_full_combos_no_pct():
    deck = ["Wizard Rod 1-60 Hexa", "Aero Pegasus 1-70 Rush", "Shark Scale 3-60 Low Rush"]
    reps = [_rep("A", [("Cobalt 9-60 Elevate", 70, 0.5, 10, "S")],
                 [_m("WIN", "Sol", deck)]),
            _rep("B", [("Cobalt 9-60 Elevate", 70, 0.5, 10, "S")],
                 [_m("LOSS", "Sol", deck)])]
    s = _scout(C.coach(reps, "me"), "Sol")
    assert s["predictability"] == 100 and s["pred_label"] == "Locked In"
    assert s["pred_color"] == "#39ff14"                      # neon green box
    assert all(pk["kind"] == "combo" and pk["blade_pct"] == 100 for pk in s["readout"])


def test_partial_blade_shows_ratchet_bit_pcts():
    # Wizard Rod every match but ratchet varies (1-60 vs 3-60) -> component-level readout
    reps = [_rep("A", [("Cobalt 9-60 Elevate", 70, 0.5, 10, "S")],
                 [_m("WIN", "Var", ["Wizard Rod 1-60 Hexa", "Aero 1-60 Rush", "Shark 9-60 Ball"])]),
            _rep("B", [("Cobalt 9-60 Elevate", 70, 0.5, 10, "S")],
                 [_m("LOSS", "Var", ["Wizard Rod 3-60 Hexa", "Aero 1-60 Rush", "Shark 9-60 Ball"])])]
    s = _scout(C.coach(reps, "me"), "Var")
    assert 25 <= s["predictability"] < 100                   # retains most, swaps one ratchet
    wr = next(pk for pk in s["readout"] if pk["blade"] == "Wizard Rod")
    assert wr["kind"] == "partial" and wr["blade_pct"] == 100
    assert wr["bit"] == "Hexa" and wr["bit_pct"] == 100      # bit constant
    assert wr["ratchet_pct"] == 50                            # 1-60 / 3-60 split


def test_unpredictable_opponent_is_tagged_with_deck_history():
    reps = [_rep("A", [("Cobalt 9-60 Elevate", 70, 0.5, 10, "S")],
                 [_m("WIN", "Cha", ["Blade A 1-60 Rush", "Blade B 9-60 Kick", "Blade C 3-60 Orb"])]),
            _rep("B", [("Cobalt 9-60 Elevate", 70, 0.5, 10, "S")],
                 [_m("LOSS", "Cha", ["Blade D 1-70 Flat", "Blade E 5-60 Ball", "Blade F 7-60 Low"])])]
    res = C.coach(reps, "me")
    s = _scout(res, "Cha")
    assert s["pred_label"] == "Wild Card" and s["readout"] is None
    assert s["pred_color"] == "#FF3B3B"                       # red
    assert len(s["decks_faced"]) == 2
    assert "??? ??? ???" in C.coach_txt(res)


def test_meta_player_gets_shift_watch_note():
    deck = ["Shark Scale 9-60 Free Ball", "Aero Pegasus 1-60 Rush", "Wizard Rod 1-60 Hexa"]
    reps = [_rep("A", [("Cobalt 9-60 Elevate", 70, 0.5, 10, "S")], [_m("WIN", "Sol", deck)]),
            _rep("B", [("Cobalt 9-60 Elevate", 70, 0.5, 10, "S")], [_m("LOSS", "Sol", deck)])]
    meta = {"generated": "2026-07-01", "top3_combos": [],
            "blade_meta": [{"blade": "Shark Scale"}, {"blade": "Aero Pegasus"}, {"blade": "Wizard Rod"}]}
    s = _scout(C.coach(reps, "me", meta_report=meta), "Sol")
    assert s["meta_style"]["tag"] == "meta"
    assert any("meta shift" in w for w in s["watch"])


def test_self_read_ranks_your_blades():
    reps = [_rep("A", [("Aero Pegasus 1-60 Rush", 60, 0.2, 20, "A"),
                       ("Shark Scale 9-60 Free Ball", 70, 0.5, 10, "S")], [])]
    sr = C.coach(reps, "me")["prediction"]["self_read"]
    assert sr["blades"][0]["blade"] == "Aero Pegasus"        # most battles
    assert sr["blades"][0]["pct"] == 67                       # 20 of 30


def test_meta_counter_uses_field_snapshot():
    reps = [_rep("A", [("Cobalt 9-60 Elevate", 70, 0.5, 10, "S")],
                 [], matchups=[("Cobalt 9-60 Elevate", "Wizard Rod 1-60 Hexa", 3, 3, 0)])]
    meta = {"generated": "2026-07-01", "entries": 81, "blade_meta": [{"blade": "Shark Scale"}],
            "ratchet_meta": [{"ratchet": "1-60"}],
            "top3_combos": [{"combo": "Wizard Rod 1-60 Hexa", "count": 7}]}
    mc = C.coach(reps, "me", meta_report=meta)["prediction"]["meta_counter"]
    row = mc["rows"][0]
    assert row["combo"] == "Wizard Rod 1-60 Hexa" and row["field_count"] == 7
    assert row["answer"]["bring"] == "Cobalt 9-60 Elevate"    # your 3-0 answer
    assert "no meta shift" in mc["disclaimer"]


def test_backdoor_widens_prediction_with_community():
    deck = ["Wizard Rod 1-60 Hexa", "Aero Pegasus 1-70 Rush"]
    reps = [_rep("A", [("Cobalt 9-60 Elevate", 70, 0.5, 10, "S")], [_m("WIN", "Sol", deck)]),
            _rep("B", [("Cobalt 9-60 Elevate", 70, 0.5, 10, "S")], [_m("LOSS", "Sol", deck)])]
    # Sol's OWN report in the community pool reveals their real deck
    community = [{"player": "Sol", "event": "X", "combos": [
        {"combo": "Wizard Rod 1-60 Hexa", "win_pct": 60, "ppb": 0.2, "battles": 30, "tier": "A"},
        {"combo": "Dranzer 3-60 Attack", "win_pct": 55, "ppb": 0.1, "battles": 20, "tier": "B"}],
        "matches": [], "matchups": [], "peers": [], "finishes": {"win": {}, "loss": {}},
        "style": {}, "dynamics": {"side": {}, "points_dist": {}}}]
    res = C.coach(reps, "me", community=community)
    assert res["prediction"]["backdoor"] is True
    s = _scout(res, "Sol")
    assert "community" in s["source"]
    # community reveals Dranzer, which was never seen in the head-to-head matches
    assert any("Dranzer" in pk["combo"] for pk in s["readout"])
