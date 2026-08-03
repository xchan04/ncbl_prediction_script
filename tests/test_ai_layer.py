"""AI analyst layer: prompt/persona, key resolution, markdown rendering, graceful degradation,
and a mocked-SDK happy path (no real Claude call)."""
import types

from ncbl import ai_layer as AI


def _res():
    return {"player": "espiiii", "scope": "lifetime", "events": ["A", "B"],
            "confidence": {"tier": "Gold", "events": 5, "battles": 283},
            "archetype": "The Generalist", "style": {"Aggression": 34},
            "combos": {"Shark Scale 9-60 Free Ball": {"tier": "S", "win_pct": 68.3, "ppb": 0.57, "battles": 63, "trend": "flat"}},
            "loss_finishes": {"Opp Spin": 41.2}, "weaknesses": [{"text": "x", "suggestion": "y", "severity": "high"}],
            "strengths": [{"text": "s"}], "swaps": [], "meta": [], "recommendation": {"deck": [], "bench": [], "note": ""},
            "rivals": [{"player": "Oyapapi", "wins": 1, "losses": 2, "source": "reports+h2h"}],
            "nemeses": [], "launch": {}, "field": [], "prediction": {"scouting": [], "meta_counter": {}}}


def test_system_persona_is_expert_beyblader():
    assert "Beyblade" in AI.SYSTEM and "analyst" in AI.SYSTEM.lower() and "coach" in AI.SYSTEM.lower()


def test_prompt_embeds_player_and_data():
    p = AI.build_prompt(_res())
    assert "espiiii" in p and "Shark Scale 9-60 Free Ball" in p and "Top-10" in p


def test_default_model_is_opus_4_8():
    assert AI.DEFAULT_MODEL == "claude-opus-4-8"


def test_key_resolution_order(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert AI.resolve_key("sk-explicit", {}) == "sk-explicit"                     # explicit wins
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert AI.resolve_key(None, {}) == "sk-env"                                   # env next
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert AI.resolve_key(None, {"anthropic_api_key": "sk-cfg"}) == "sk-cfg"      # config next
    f = tmp_path / "runtime_config.json"
    f.write_text('{"models": {"default": "qwen"}, "claude": {"api_key": "sk-ant-fromjarvis123"}}')
    assert AI.resolve_key(None, {"anthropic_key_file": str(f)}) == "sk-ant-fromjarvis123"  # scanned
    assert AI.resolve_key(None, {}) is None                                       # nothing -> None


def test_md_to_html_basics():
    import html as _h
    out = AI._md_to_html("## Head\n**bold** text\n- one\n- two", _h.escape)
    assert "<h3>Head</h3>" in out and "<b>bold</b>" in out and "<li>one</li>" in out


def test_analyze_degrades_without_key_or_sdk(monkeypatch):
    # No key and (likely) no SDK / no network -> clean (None, reason), never raises
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    notes, err = AI.analyze(_res(), cfg={})
    assert notes is None and isinstance(err, str) and err


def test_analyze_happy_path_with_mocked_client(monkeypatch):
    block = types.SimpleNamespace(type="text", text="Executive read: espiiii is a stamina grinder. Fix the spin gap.")
    resp = types.SimpleNamespace(content=[block], stop_reason="end_turn")

    class _Msgs:
        def create(self, **kw):
            assert kw["model"] == "claude-opus-4-8"
            assert kw["thinking"] == {"type": "adaptive"}
            assert "Beyblade" in kw["system"]
            return resp

    class _Fake:
        messages = _Msgs()

    monkeypatch.setitem(__import__("sys").modules, "anthropic", types.SimpleNamespace(Anthropic=lambda **k: _Fake()))
    monkeypatch.setattr(AI, "_client", lambda key: _Fake())
    notes, err = AI.analyze(_res(), api_key="sk-test")
    assert err is None
    assert "stamina grinder" in notes and "spin gap" in notes.lower()


def _rank():
    """A minimal ranking report (report.build output shape)."""
    return {"player": "espiiii", "current_rank": 12, "current_score": 14.2, "n_events": 7,
            "slots_left": 3, "target_rank": 10, "cutoff": 15.1, "open_spots": 0, "field_size": 40,
            "window": 6,
            "predictions": [{"strategy": "win out", "total": 18.0, "p_top": 0.82, "p_stage": None, "median_rank": 7},
                            {"strategy": "do nothing", "total": 14.2, "p_top": 0.10, "p_stage": None, "median_rank": 13}],
            "threats": {"overtook": [{"player": "Teefoh", "from_rank": 11, "to_rank": 9, "score": 15.5,
                                      "h2h": {"record": "1-2", "wins": 1, "losses": 2, "win_pct": 33.3}}],
                        "live": [{"player": "Oyapapi", "rank": 14, "score": 13.8, "slots_left": 4, "h2h": None}]},
            "standings": [{"rank": 9, "player": "Teefoh", "score": 15.5, "events": 8},
                          {"rank": 12, "player": "espiiii", "score": 14.2, "events": 7}]}


def test_plan_prompt_embeds_ranking_and_rivals():
    p = AI.build_plan_prompt(_rank())
    assert "espiiii" in p and "Teefoh" in p and "BATTLE PLAN" in p
    assert "win out" in p and "1-2" in p            # scenario + head-to-head both reach the model


def test_plan_compact_trims_to_cutoff_neighbourhood():
    c = AI._compact_plan(_rank())
    assert c["target_rank"] == 10 and c["current_rank"] == 12
    assert c["overtook_me"][0]["player"] == "Teefoh" and c["overtook_me"][0]["h2h"] == "1-2"
    assert all(s["rank"] <= 15 for s in c["standings_near_cutoff"])


def test_plan_to_html_and_txt_render():
    html = AI.plan_to_html("## Path\n- win two events", {})
    assert "AI battle plan" in html and "<li>win two events</li>" in html
    assert "battle plan" in AI.plan_to_txt("x").lower()


def test_battleplan_happy_path_with_mocked_client(monkeypatch):
    block = types.SimpleNamespace(type="text", text="Standing read: you are two good events from Top-10.")
    resp = types.SimpleNamespace(content=[block], stop_reason="end_turn")

    class _Msgs:
        def create(self, **kw):
            assert "BATTLE PLAN" in kw["messages"][0]["content"]
            return resp

    class _Fake:
        messages = _Msgs()

    monkeypatch.setattr(AI, "_client", lambda key: _Fake())
    plan, err = AI.battleplan(_rank(), api_key="sk-test")
    assert err is None and "Top-10" in plan


def test_battleplan_degrades_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    plan, err = AI.battleplan(_rank(), cfg={})
    assert plan is None and isinstance(err, str) and err


# ---------------- reconciliation layer ----------------
def test_parse_json_pulls_object_out_of_prose():
    assert AI._parse_json('junk {"a": 1} trailing')["a"] == 1
    assert AI._parse_json("not json at all") is None
    assert AI._parse_json("") is None


def test_parse_json_tolerates_markdown_newlines_and_fences():
    # Claude returns a 'narrative' with REAL newlines (markdown) — strict JSON would reject it,
    # which previously leaked the raw {"narrative": ...} into the report.
    payload = '{\n  "narrative": "## Read\nYou are a grinder.\n- Fix spin.",\n  "top10": "Close." \n}'
    r = AI._parse_json(payload)
    assert r is not None and r["narrative"].startswith("## Read") and "\n" in r["narrative"]
    fenced = "```json\n" + payload + "\n```"
    assert AI._parse_json(fenced)["narrative"].startswith("## Read")


def test_reconcile_prompt_asks_for_conflicts_and_combo_calls():
    assert "conflicts" in AI.TASK_RECONCILE and "combo_calls" in AI.TASK_RECONCILE
    assert "make it agree" in AI.TASK_RECONCILE


def test_reconcile_parses_structured_json(monkeypatch):
    payload = ('{"narrative": "You are a grinder.", '
               '"conflicts": [{"topic": "Wizard Rod", "resolution": "1-60 Hexa is the answer; bench note is the 6-60"}], '
               '"combo_calls": [{"combo": "Shark Scale 9-60 Free Ball", "call": "anchor", "why": "stamina core"}], '
               '"recommendation_note": "Drop the 5-60 Cobalt.", "top10": "Realistic with two clean events."}')
    block = types.SimpleNamespace(type="text", text=payload)
    resp = types.SimpleNamespace(content=[block], stop_reason="end_turn")

    class _Fake:
        messages = types.SimpleNamespace(create=lambda **kw: resp)

    monkeypatch.setattr(AI, "_client", lambda key: _Fake())
    rec, err = AI.reconcile(_res(), api_key="sk-test")
    assert err is None
    assert rec["conflicts"][0]["topic"] == "Wizard Rod"
    assert rec["combo_calls"][0]["call"] == "anchor"
    assert rec["recommendation_note"] == "Drop the 5-60 Cobalt."


def test_reconcile_falls_back_to_narrative_on_bad_json(monkeypatch):
    block = types.SimpleNamespace(type="text", text="Sorry, here is prose with no json object.")
    resp = types.SimpleNamespace(content=[block], stop_reason="end_turn")

    class _Fake:
        messages = types.SimpleNamespace(create=lambda **kw: resp)

    monkeypatch.setattr(AI, "_client", lambda key: _Fake())
    rec, err = AI.reconcile(_res(), api_key="sk-test")
    assert err is None and rec["narrative"].startswith("Sorry")
    assert rec["conflicts"] == [] and rec["combo_calls"] == []


def test_combo_call_map_normalizes_and_renders():
    rec = {"combo_calls": [{"combo": "Shark Scale 9-60 Free Ball", "call": "anchor", "why": "core"}],
           "conflicts": [{"topic": "WR", "resolution": "x"}], "top10": "close"}
    m = AI.combo_call_map(rec)
    assert m["shark scale 9-60 free ball"]["call"] == "anchor"
    html = AI.to_html("notes", {}, rec)
    assert "Conflicts reconciled" in html and "ANCHOR" in html and "Top-10 verdict" in html
    txt = AI.to_txt("notes", rec)
    assert "reconciled by AI" in txt and "ANCHOR" in txt


# ---------------- prompt hardening (spin gap + accuracy guards) ----------------
def _res_grounded():
    r = _res()
    r["grounded"] = {
        "inventory": {"blades": {"Shark Scale": 60}, "ratchets": {}, "bits": {}},
        "landscape": {"spin_mix": {"right": 9, "left": 1}, "role_mix": {"attack": 7},
                      "n_distinct": 36, "combos": []},
        "gaps": [], "meta_alignment": {"off_meta": False, "archetypes": []},
        "diagnoses": [
            {"combo": "A", "verdict": "fine", "peer_gap": 15.0, "battles": 63, "n_peers": 3, "reasons": []},
            {"combo": "B", "verdict": "at_par", "peer_gap": -4.9, "battles": 46, "n_peers": 35, "reasons": []},
            {"combo": "C", "verdict": "insufficient_data", "peer_gap": None, "battles": 8, "n_peers": 0, "reasons": []},
        ],
        "policy": "owned parts only",
    }
    return r


def test_spin_analysis_is_precomputed_for_the_model():
    """The raw spin_mix was already sent and got ignored — the conclusion must be explicit."""
    g = AI._compact(_res_grounded())["grounded"]
    note = g["field_landscape"]["SPIN_ANALYSIS"]
    assert "9 of the 10" in note and "right-spin" in note and "EQUALIZATION" in note


def test_verdict_tally_prevents_miscounting():
    """The model once invented a 'skill_gap' bucket; give it exact counts and members."""
    g = AI._compact(_res_grounded())["grounded"]
    assert g["VERDICT_TALLY"] == {"fine": 1, "at_par": 1, "insufficient_data": 1}
    assert "skill_gap" not in g["VERDICT_TALLY"]
    assert g["VERDICT_MEMBERS"]["fine"] == ["A"]


def test_reconcile_prompt_carries_spin_and_accuracy_rules():
    t = AI.TASK_RECONCILE
    assert "SPIN DIRECTION" in t and "MUST address spin coverage" in t
    assert "ACCURACY RULES" in t
    assert "never promote a low-battle combo" in t.lower() or "never promote a low-battle" in t
    assert "VERDICT_TALLY" in t


def test_battleplan_combat_profile_has_spin_and_sample_size():
    cb = AI._compact_combat(_res_grounded())
    assert cb["spin_analysis"] and "right-spin" in cb["spin_analysis"]
    assert cb["part_vs_skill_tally"]["fine"] == 1
    # battle/event counts must ride along so thin samples cannot be praised blindly
    assert "events" in next(iter(cb["top_combos"].values()))


def test_plan_prompt_has_accuracy_and_spin_guards():
    assert "ACCURACY" in AI.TASK_PLAN and "SPIN" in AI.TASK_PLAN


def test_prompts_guard_heading_mismatch_and_vagueness():
    """A real run produced the heading 'Bench your anchors' over a list of dead weight."""
    for t in (AI.TASK_RECONCILE, AI.TASK_PLAN):
        assert "Bench your anchors" in t          # the exact failure is named so it is not repeated
        assert "ACTIONABILITY" in t
    assert "Name exact combos" in AI.TASK_RECONCILE


def test_combat_profile_carries_rival_answers():
    """Without this the plan can only say 'scout the bracket' instead of naming an answer."""
    r = _res_grounded()
    r["prediction"] = {"scouting": [
        {"opponent": "Oyapapi", "record": "0-2", "losing": True, "predictability": 0,
         "readout": None, "answers": []},
        {"opponent": "Bobablade", "record": "1-2", "losing": True, "predictability": 47,
         "readout": [{"combo": "Silver Wolf 9-60 Orb"}],
         "answers": [{"vs": "Phoenix Wing 1-60 Rush", "bring": "Cobalt Dragoon 9-60 Elevate", "record": "4-0"}]},
        {"opponent": "SHKR", "record": "2-1", "losing": False, "predictability": 100,
         "readout": None, "answers": []},
    ], "meta_counter": {}}
    cb = AI._compact_combat(r)
    opps = [x["opp"] for x in cb["rival_answers"]]
    assert "Oyapapi" in opps and "Bobablade" in opps
    assert "SHKR" not in opps                      # only rivals you are LOSING to
    oya = next(x for x in cb["rival_answers"] if x["opp"] == "Oyapapi")
    assert oya["your_answers"] == []               # honest "no owned answer"
    bob = next(x for x in cb["rival_answers"] if x["opp"] == "Bobablade")
    assert bob["your_answers"][0]["bring"] == "Cobalt Dragoon 9-60 Elevate"


def test_combat_profile_includes_deck_part_conflicts():
    r = _res_grounded()
    r["recommendation"] = {"deck": [{"combo": "Shark Scale 9-60 Free Ball"}],
                           "part_conflicts": [{"combo": "Cobalt Dragoon 9-60 Elevate",
                                               "clash": "Ratchet '9-60'"}]}
    cb = AI._compact_combat(r)
    assert cb["deck_part_conflicts"][0]["clash"] == "Ratchet '9-60'"
