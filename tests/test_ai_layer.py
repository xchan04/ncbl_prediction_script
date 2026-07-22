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
