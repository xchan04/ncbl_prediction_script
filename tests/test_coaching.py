"""Coaching engine: aggregation, confidence scaling, and finding generation."""
from ncbl import coaching as C


def _rep(event, combos, matchups=(), matches=(), loss=None, style=None, peers=(), dynamics=None):
    return {
        "player": "espiiii", "event": event, "date": None, "source": event,
        "totals": {}, "archetype": "The Strategist",
        "combos": [{"combo": c[0], "win_pct": c[1], "ppb": c[2], "battles": c[3], "tier": c[4]} for c in combos],
        "finishes": {"win": {}, "loss": loss or {}},
        "matchups": [{"your_combo": m[0], "opp_combo": m[1], "faced": m[2], "wins": m[3],
                      "losses": m[4], "win_pct": 0, "ppb": 0, "net": 0} for m in matchups],
        "matches": list(matches), "peers": list(peers), "style": style or {},
        "dynamics": dynamics or {"side": {}, "points_dist": {}},
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


def test_confidence_uses_events_attended_from_sheet():
    agg = C.aggregate(_reports(), "espiiii")               # 2 reports
    c = C.confidence(agg, events_attended=7)               # but 7 attended per the sheet
    assert c["events"] == 7 and c["report_events"] == 2 and c["missing_reports"] == 5
    assert c["tier"] == "Gold"                             # 7 events -> Gold regardless of report count
    res = C.coach(_reports(), "espiiii", events_attended=7)
    txt = C.coach_txt(res)
    assert "7 events (2 with reports)" in txt
    assert "no NCBLAST report" in txt                      # coverage note fires


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


def test_recommendation_picks_best_and_benches_bad():
    res = C.coach(_reports(), "espiiii")
    rec = res["recommendation"]
    deck = [x["combo"] for x in rec["deck"]]
    assert "Cobalt 9-60 Elevate" in deck                 # S/A engine is recommended
    assert any(b["combo"] == "Shark 7-70 Low Rush" for b in rec["bench"])  # -2.5 combo benched
    assert deck and deck[0] not in [b["combo"] for b in rec["bench"]]      # top pick isn't a benched combo


def test_side_split_aggregates_battle_weighted():
    reps = [_rep("A", combos=[("Cobalt 9-60 Elevate", 70, 0.5, 20, "S")],
                 dynamics={"side": {"B": {"win_pct": 60.0, "battles": 10, "ppb": 0.2},
                                    "X": {"win_pct": 50.0, "battles": 10, "ppb": 0.1}}, "points_dist": {}}),
            _rep("B", combos=[("Cobalt 9-60 Elevate", 70, 0.5, 20, "S")],
                 dynamics={"side": {"B": {"win_pct": 80.0, "battles": 30, "ppb": 0.4},
                                    "X": {"win_pct": 50.0, "battles": 10, "ppb": 0.1}}, "points_dist": {}})]
    agg = C.aggregate(reps, "espiiii")
    assert agg["side"]["B"]["battles"] == 40
    # battle-weighted: (60*10 + 80*30)/40 = 75.0
    assert agg["side"]["B"]["win_pct"] == 75.0
    assert agg["side"]["X"]["win_pct"] == 50.0


def test_launch_gap_flags_positioning_weakness():
    reps = [_rep("A", combos=[("Cobalt 9-60 Elevate", 70, 0.5, 20, "S")],
                 dynamics={"side": {"B": {"win_pct": 72.0, "battles": 20, "ppb": 0.4},
                                    "X": {"win_pct": 48.0, "battles": 15, "ppb": 0.0}}, "points_dist": {}})]
    res = C.coach(reps, "espiiii")
    assert res["launch"]["gap"] == 24.0
    assert any(w["type"] == "launch" and "X-side" in w["text"] for w in res["weaknesses"])
    assert any(s["type"] == "launch" for s in res["strengths"])
    assert "LAUNCH & POSITIONING" in C.coach_txt(res)


def test_launch_balanced_no_weakness():
    reps = [_rep("A", combos=[("Cobalt 9-60 Elevate", 70, 0.5, 20, "S")],
                 dynamics={"side": {"B": {"win_pct": 62.0, "battles": 20, "ppb": 0.3},
                                    "X": {"win_pct": 60.0, "battles": 20, "ppb": 0.3}}, "points_dist": {}})]
    res = C.coach(reps, "espiiii")
    assert res["launch"]["verdict"] == "balanced across both sides"
    assert not any(w["type"] == "launch" for w in res["weaknesses"])


def test_community_benchmark_flags_field_outperformance():
    # espiiii is 1-4 vs Wizard Rod; two other players go a combined 8-2 vs it -> field solves it
    reps = [_rep("A", combos=[("Aero 1-60 Rush", 40, -0.1, 10, "B")],
                 matchups=[("Aero 1-60 Rush", "Wizard Rod 3-70 Attack", 5, 1, 4)])]
    other1 = _rep("A", combos=[("Cobalt 9-60 Elevate", 80, 1.0, 12, "S")],
                  matchups=[("Cobalt 9-60 Elevate", "Wizard Rod 3-70 Attack", 5, 4, 1)])
    other1["player"] = "rivalA"
    other2 = _rep("B", combos=[("Cobalt 9-60 Elevate", 80, 1.0, 12, "S")],
                  matchups=[("Cobalt 9-60 Elevate", "Wizard Rod 3-70 Attack", 5, 4, 1)])
    other2["player"] = "rivalB"
    res = C.coach(reps + [other1, other2], "espiiii")
    b = next((x for x in res["benchmarks"] if "Wizard Rod" in x["opp"]), None)
    assert b is not None
    assert b["you_pct"] == 20.0          # 1-4
    assert b["field_pct"] == 80.0        # 8-2 across the two rivals
    assert "Cobalt 9-60 Elevate" in b["suggestion"]   # field wins most with Cobalt


def test_community_benchmark_empty_with_only_you():
    reps = [_rep("A", combos=[("Aero 1-60 Rush", 40, -0.1, 10, "B")],
                 matchups=[("Aero 1-60 Rush", "Wizard Rod 3-70 Attack", 5, 1, 4)])]
    res = C.coach(reps, "espiiii")
    assert res["benchmarks"] == []                     # no other players -> no field
    assert res["community"]["n_players"] == 1
    assert "community benchmark unlocks" in C.coach_txt(res).lower()


def test_field_benchmark_ranks_you_vs_peers():
    peers = [
        {"combo": "Aero 1-60 Rush", "player": "YOU", "win_pct": 40.0, "ppb": 0.0, "battles": 10},
        {"combo": "Aero 1-60 Rush", "player": "rivalA", "win_pct": 70.0, "ppb": 0.6, "battles": 12},
        {"combo": "Aero 1-60 Rush", "player": "rivalB", "win_pct": 65.0, "ppb": 0.5, "battles": 8},
    ]
    reps = [_rep("A", combos=[("Aero 1-60 Rush", 40, 0.0, 10, "B")], peers=peers)]
    res = C.coach(reps, "espiiii")
    f = next((x for x in res["field"] if "Aero" in x["combo"]), None)
    assert f is not None
    assert f["you"] == 40.0
    assert f["gap"] < 0                                # below field average
    assert f["standing"] == "bottom-third"             # you beat 0 of 2 peers
    assert f["best_peer"] == "rivalA"
    assert "FIELD BENCHMARK" in C.coach_txt(res)


def test_goal_card_summarizes_form_and_objectives():
    reps = _reports()
    reps[0]["totals"] = {"win_pct": 55.0, "placement": "5th"}
    reps[1]["totals"] = {"win_pct": 68.0, "placement": "3rd"}
    res = C.coach(reps, "espiiii")
    g = res["goal"]
    assert g["trend"] == "improving"                  # 55 -> 68
    assert g["placements"] == ["5th", "3rd"]
    assert g["objectives"]                             # at least one concrete objective
    assert "GOAL CARD" in C.coach_txt(res)


def test_nemesis_dossier_lists_beating_combos():
    match = {"result": "LOSS", "opponent": "Bongo", "sets": "0-2", "battles": 3, "net": -2,
             "opp_combos": [{"combo": "Wizard Rod 3-70 Attack", "wl": "2-0", "match_ppb": 1.0}]}
    match2 = {"result": "LOSS", "opponent": "Bongo", "sets": "0-2", "battles": 3, "net": -2,
              "opp_combos": [{"combo": "Wizard Rod 3-70 Attack", "wl": "2-1", "match_ppb": 0.8}]}
    reps = [_rep("A", combos=[("Aero 1-60 Rush", 50, 0.1, 10, "B")], matches=[match, match2])]
    res = C.coach(reps, "espiiii")
    nem = next((n for n in res["nemeses"] if n["player"] == "Bongo"), None)
    assert nem is not None                             # 0-2 sets -> nemesis
    assert any("Wizard Rod" in cb["combo"] for cb in nem["combos"])
    assert "NEMESIS DOSSIER" in C.coach_txt(res)


def test_combo_parts_split():
    assert C.combo_parts("Shark Scale 9-60 Free Ball") == ("Shark Scale", "9-60", "Free Ball")
    assert C.combo_parts("Cobalt Dragoon 9-60 Elevate") == ("Cobalt Dragoon", "9-60", "Elevate")


def test_recommended_deck_has_no_shared_parts():
    # two strong combos share the Blade 'Dranzer' and one shares a Bit -> deck must not reuse parts
    reps = [_rep("E",
                 combos=[("Dranzer 3-60 Attack", 80, 1.0, 12, "S"),
                         ("Dranzer 9-60 Attack", 78, 0.9, 12, "S"),   # shares Blade + Bit with #1
                         ("Wizard Rod 3-70 Attack", 70, 0.6, 10, "A"),  # shares Ratchet 3-60? no; shares Bit Attack
                         ("Phoenix 1-80 Guard", 68, 0.5, 10, "A")])]
    rec = C.coach(reps, "espiiii")["recommendation"]
    deck = [x["combo"] for x in rec["deck"]]
    blades = [C.combo_parts(cbo)[0] for cbo in deck]
    ratchets = [C.combo_parts(cbo)[1] for cbo in deck]
    bits = [C.combo_parts(cbo)[2] for cbo in deck]
    assert len(blades) == len(set(blades))       # no repeated blade
    assert len(ratchets) == len(set(ratchets))   # no repeated ratchet
    assert len(bits) == len(set(bits))           # no repeated bit
    assert rec["part_conflicts"]                 # the clashing combos were flagged
