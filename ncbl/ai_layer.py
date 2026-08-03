"""Optional AI analyst layer — calls the Claude API directly with the user's personal key
(the official `anthropic` SDK; no Jarvis, no local model).

It sends the finished coaching report as data and asks an expert Beyblade-X-analyst persona
for a written assessment, appended as an "AI analyst" section. It never alters the
deterministic numbers, and it degrades gracefully (missing SDK, no key, or a network/API
error just skips the section — the base report is unaffected).

API key resolution (first hit wins): explicit --ai-key → ANTHROPIC_API_KEY env → config
'anthropic_api_key' → an sk-ant- key scanned out of config 'anthropic_key_file' (e.g. your
Jarvis runtime_config.json) → the SDK's own resolution (ant profile). Model defaults to
claude-opus-4-8.
"""
from __future__ import annotations
import json
import os
import re

DEFAULT_MODEL = "claude-opus-4-8"

SYSTEM = (
    "You are an elite, world-class competitive Beyblade X analyst and coach. You have deep, "
    "current knowledge of the metagame, blade/ratchet/bit synergies, launch and B/X-side "
    "dynamics, finish types (Xtreme/Over/Spin/Burst), deck-building under the 3v3 "
    "part-uniqueness rule, and Swiss + top-cut tournament strategy. You read a data-driven "
    "coaching report (produced by a no-AI stats pipeline) and give a sharp, specific, honest "
    "assessment — the read a top coach would give, not a restatement of the tables."
)

TASK = (
    "Below is a player's coaching report as JSON. Write an analyst assessment with these parts:\n"
    "1) Executive read (2-3 sentences): who is this player, competitively?\n"
    "2) The single highest-leverage fix, and exactly why.\n"
    "3) 3-4 prioritized, concrete action items — name the combos and opponents.\n"
    "4) Where the raw numbers might MISLEAD — be skeptical (small samples, event ordering, "
    "context the stats miss).\n"
    "5) One-line verdict on their Top-10 chances.\n"
    "Be specific and under ~450 words. Interpret the data; don't just repeat it.\n\nREPORT JSON:\n"
)


# ---------------- compact the report (control tokens + focus) ----------------
def _compact(res):
    def combos(d):
        return {k: {"tier": v.get("tier"), "win": v.get("win_pct"), "ppb": v.get("ppb"),
                    "btl": v.get("battles"), "trend": v.get("trend")}
                for k, v in d.items()}
    pred = res.get("prediction") or {}
    rec = res.get("recommendation") or {}
    scouting = [{"opp": s["opponent"], "record": s["record"], "predictability": s["predictability"],
                 "label": s["pred_label"], "meta": (s.get("meta_style") or {}).get("tag"),
                 "likely": [p.get("combo") for p in (s.get("readout") or [])][:4]}
                for s in pred.get("scouting", [])]
    goal = dict(res.get("goal") or {})
    # win_seq is already date-sorted by goal_card; expose the labeled trajectory explicitly
    # so the model reads form chronologically (latest event = last entry), not in load order.
    return {
        "player": res.get("player"), "scope": res.get("scope"),
        "events": res.get("events"), "confidence": res.get("confidence"),
        "archetype": res.get("archetype"), "style": res.get("style"),
        "goal": goal, "trajectory_by_date": goal.get("trajectory"),
        "combos": combos(res.get("combos", {})),
        "loss_finishes": res.get("loss_finishes"),
        "weaknesses": [{"t": w["text"], "fix": w["suggestion"], "sev": w["severity"]} for w in res.get("weaknesses", [])],
        "strengths": [s["text"] for s in res.get("strengths", [])],
        "swaps": res.get("swaps"), "meta_field": res.get("meta"),
        "recommendation": {"deck": [d["combo"] for d in rec.get("deck", [])],
                           "bench": [b["combo"] for b in rec.get("bench", [])],
                           "note": rec.get("note"),
                           # combos excluded from the deck by the 3v3 no-shared-part rule —
                           # tells the model why the higher-tier combo isn't in the deck.
                           "part_conflicts": rec.get("part_conflicts")},
        "rivals": [{"p": r["player"], "rec": f"{r['wins']}-{r['losses']}", "src": r.get("source")}
                   for r in res.get("rivals", [])],
        "nemeses": res.get("nemeses"), "launch": res.get("launch"),
        "field_benchmark": res.get("field"),
        "rival_scouting": scouting,
        "meta_counter": (pred.get("meta_counter") or {}),
        # Grounded coaching: inventory, landscape, structural gaps, meta alignment, and the
        # part-issue-vs-skill-gap diagnosis. This is what keeps advice actionable.
        "grounded": _compact_grounded(res.get("grounded")),
    }


def _compact_grounded(g):
    """Trim the grounded block for the prompt, keeping the parts that constrain advice."""
    if not g or g.get("error"):
        return None
    land = g.get("landscape") or {}
    diags = g.get("diagnoses") or []

    # Pre-computed spin analysis. The raw spin_mix was already being sent and the model still
    # missed it, so state the conclusion explicitly rather than hoping it infers one.
    spin_mix = land.get("spin_mix") or {}
    inv = g.get("inventory") or {}
    deck = g.get("deck") or []
    deck_spins = [c.get("spin") for c in (land.get("deck_spins") or [])]
    spin_note = None
    if spin_mix:
        dom = max(spin_mix, key=spin_mix.get)
        tot = sum(spin_mix.values()) or 1
        opp = "left" if dom == "right" else "right"
        spin_note = (f"{spin_mix.get(dom, 0)} of the {tot} combos you most often face are {dom}-spin. "
                     f"An opposite-spin ({opp}) blade turns those late-game contacts into EQUALIZATION "
                     f"instead of a same-spin stamina race you are currently losing.")

    # Verdict tally: the model previously invented a 'skill_gap' count that did not exist.
    # Give it the exact counts so it cannot miscount, plus the explicit list per bucket.
    tally, by_verdict = {}, {}
    for d in diags:
        v = d.get("verdict")
        tally[v] = tally.get(v, 0) + 1
        by_verdict.setdefault(v, []).append(d.get("combo"))

    return {
        "owned_parts": inv,
        "field_landscape": {
            "spin_mix": spin_mix, "role_mix": land.get("role_mix"),
            "n_distinct_combos_faced": land.get("n_distinct"),
            "SPIN_ANALYSIS": spin_note,
            "most_common": [{"combo": r["combo"], "seen": r["times_seen"], "record": r["record"],
                             "spin": r["spin"], "roles": r["roles"], "losing": r["losing"]}
                            for r in (land.get("combos") or [])[:8]],
        },
        "deck_gaps": [{"type": x["type"], "severity": x["severity"], "gap": x["text"],
                       "why": x["why"],
                       "fill_tier": (x.get("fill") or {}).get("tier"),
                       "fill_combo": (x.get("fill") or {}).get("combo"),
                       "fill_note": (x.get("fill") or {}).get("note")}
                      for x in (g.get("gaps") or [])],
        "meta_alignment": {k: v for k, v in (g.get("meta_alignment") or {}).items()
                           if k != "archetypes"},
        "meta_archetypes": [{"archetype": a["archetype"], "combo": a["combo"],
                             "weaknesses": a["weaknesses"], "confidence": a["confidence"]}
                            for a in ((g.get("meta_alignment") or {}).get("archetypes") or [])],
        "part_vs_skill": [{"combo": d["combo"], "verdict": d["verdict"], "peer_gap": d["peer_gap"],
                           "battles": d["battles"], "n_peers": d["n_peers"],
                           "reasons": d["reasons"]} for d in diags],
        "VERDICT_TALLY": tally,
        "VERDICT_MEMBERS": by_verdict,
        "policy": g.get("policy"),
    }


def build_prompt(res):
    return TASK + json.dumps(_compact(res), default=str)


# ---------------- reconciliation (make the whole report agree when AI is on) ----------------
# The no-AI report is a set of independent heuristics, so two sections can APPEAR to conflict
# (e.g. a combo praised in "what's working" but benched in a weakness — usually a different
# ratchet/bit build). When AI is on the user wants ONE coherent voice: the model resolves every
# apparent conflict, gives a single final call per combo, and may revise the recommendation note.
TASK_RECONCILE = (
    "Below is a player's coaching report as JSON — produced by independent no-AI heuristics, so "
    "sections can APPEAR to contradict each other (a combo can look praised in one place and "
    "benched in another — often because they are different ratchet/bit builds of the same blade, "
    "or different sample sizes). Your job: read ALL of it and make it agree, AND give a substantial, "
    "stat-driven analysis a top coach would give. Respond with ONLY a JSON object (no prose, no "
    "markdown fences) with this shape:\n"
    "{\n"
    '  "narrative": "markdown, ~500-650 words with ## sub-headings. Go deep on the STATS, citing '
    'actual numbers: (1) Executive read — who is this player competitively (use archetype + the '
    'top style axes). (2) Combo analysis — walk the key combos by win%/PPB/tier/trend, which are '
    'carrying vs dragging and why. (3) Finish & vulnerability — what the winning/losing finish mix '
    "and self-KO say about how they win and lose. (4) Launch & positioning — B-side vs X-side "
    'gaps. (5) The single highest-leverage fix and exactly why. (6) 3-4 prioritized action items '
    'naming combos + opponents. Interpret the numbers; never just restate the table.",\n'
    '  "conflicts": [{"topic": "short label e.g. Wizard Rod", "resolution": "the one true reading — '
    'e.g. \'Wizard Rod 1-60 Hexa is your best answer (5-0); the bench note is about the 6-60 build, '
    'not this one\'"}],\n'
    '  "combo_calls": [{"combo": "<EXACT combo string copied verbatim from the combos keys>", '
    '"call": "anchor|keep|tune|bench", "why": "<=12 words"}],\n'
    '  "recommendation_note": "a revised note for the recommended deck if the pipeline\'s is wrong, '
    'misleading, or conflicts with your calls — else empty string. Respect the 3v3 no-shared-'
    'Blade/Ratchet/Bit rule and part_conflicts.",\n'
    '  "top10": "one-line verdict on their Top-10 chances"\n'
    "}\n"
    "Rules: only reference combos that exist in the data; copy combo strings EXACTLY so they can be "
    "matched; resolve every apparent contradiction you notice; never invent numbers.\n"
    "\n"
    "GROUNDING RULES — these override everything else:\n"
    "* The `grounded` block lists `owned_parts` (every Blade/Ratchet/Bit the player has actually "
    "used, with battle counts). **Never recommend a combo containing a part that is not in "
    "owned_parts.** Telling a player to buy hardware is not coaching.\n"
    "* Prefer combos they have already RUN (they appear in `combos` with battle counts). Second "
    "choice is a combo they can ASSEMBLE from owned_parts — say explicitly that it is untested and "
    "must be practised before an event.\n"
    "* The ONLY exception: if `meta_alignment.off_meta` is true AND no owned part can fill the gap "
    "(`fill_tier` is 'acquire'), you may name new hardware — and you must label it as a purchase "
    "decision with the reason.\n"
    "* Use `field_landscape` (what they actually face), `deck_gaps` (structural holes, each with a "
    "pre-computed `fill` respecting ownership), and `part_vs_skill` to decide WHY something is "
    "underperforming. If the verdict is 'skill_gap' or 'at_par', recommend practice or deployment "
    "changes — NOT a part swap. Only call for a part change when the evidence supports it.\n"
    "* Anchor the plan on their demonstrated strengths (verdict 'fine'), the deck gap, the field "
    "landscape, the documented meta, and their weaknesses — in that order.\n"
    "\n"
    "ACCURACY RULES — violating these makes the report worse than no report:\n"
    "* **Never state a count or category you have not verified.** `VERDICT_TALLY` gives the exact "
    "number of combos per verdict and `VERDICT_MEMBERS` lists which combos are in each bucket. If "
    "the tally shows zero 'skill_gap', do NOT write that skill gaps exist. Cite the real buckets.\n"
    "* **Respect sample size — never promote a low-battle combo to anchor.** A verdict of "
    "'insufficient_data' means exactly that: the evidence cannot support a strong call. A combo "
    "with <20 battles or from a single event may be called 'tune' or 'promising, needs testing', "
    "but must NOT be labelled anchor/carry, and must not be recommended as a core slot for a "
    "must-win event. Say how many battles and how many events back any combo you praise.\n"
    "* **Do not re-grade a tier without saying why the data is thin.** If you think a B-tier "
    "undersells a combo, state the battle count in the same sentence.\n"
    "\n"
    "SPIN DIRECTION — do not skip this:\n"
    "* `field_landscape.SPIN_ANALYSIS` and `spin_mix` describe the spin make-up of the field. "
    "Spin direction is the single biggest strategic axis in Beyblade X: an opposite-spin blade "
    "turns a losing same-spin stamina race into an equalization win.\n"
    "* You MUST address spin coverage explicitly. State the field's spin mix, whether the player's "
    "deck can answer it, and which owned blade provides the opposite spin. If the deck is all one "
    "spin, call it out as a structural gap even when every individual combo looks healthy.\n\n"
    "REPORT JSON:\n"
)


def _parse_json(text):
    """Pull the first {...} object out of a model response; None if it isn't valid JSON.
    Uses strict=False so the markdown 'narrative' (which contains real newlines) parses —
    strict JSON rejects literal control characters inside strings."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):                       # strip ```json ... ``` fences
        s = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", s).strip()
    try:
        i, j = s.index("{"), s.rindex("}")
        obj = json.loads(s[i:j + 1], strict=False)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def reconcile(res, api_key=None, model=DEFAULT_MODEL, cfg=None, max_tokens=12000):
    """Return (reconcile_dict, None) or (None, reason). The dict always has a 'narrative' key.
    If the model doesn't return parseable JSON, the whole reply becomes the narrative (graceful)."""
    text, err = _call(SYSTEM, TASK_RECONCILE + json.dumps(_compact(res), default=str),
                       api_key=api_key, model=model, cfg=cfg, max_tokens=max_tokens)
    if err:
        return None, err
    data = _parse_json(text)
    if not data:
        return {"narrative": text, "conflicts": [], "combo_calls": [], "recommendation_note": "", "top10": ""}, None
    data.setdefault("narrative", "")
    data.setdefault("conflicts", [])
    data.setdefault("combo_calls", [])
    data.setdefault("recommendation_note", "")
    data.setdefault("top10", "")
    return data, None


# ---------------- battle plan (the RANKING/climb report) ----------------
# The coaching AI (above) reads combat data and says how to FIGHT. This one reads the ranking
# situation — standing, cutoff, best/worst-case scenarios, and the rivals who overtook you or can
# still catch you — PLUS a compact combat profile, and says how to CLIMB: a game plan to reach the
# target rank that is grounded in how the player actually performs.
TASK_PLAN = (
    "Below is JSON with a player's SEASON RANKING SITUATION and (if present) their COMBAT PROFILE. "
    "The ranking part: where they stand, the standings and Top-N cutoff, the modeled scenarios "
    "(each row is a strategy and its resulting total / P(Top-N) / median finish — read best-to-worst "
    "case), and the rivals who OVERTOOK them or can STILL catch them (head-to-head where known). "
    "The combat part: archetype, style, best/worst combos, weaknesses, recommended deck and nemeses "
    "— use it so your tips are grounded in how they actually fight, not generic. Write a BATTLE PLAN "
    "to reach the target rank:\n"
    "1) Standing read (2-3 sentences): how close are they, realistically?\n"
    "2) The path to Top-N — exactly what it takes: how many more events, what scores/finishes, "
    "citing the scenario rows by name.\n"
    "3) Best case and worst case — name what each hinges on.\n"
    "4) Rival watch: who can overtake them and who they must hold off; use the head-to-head "
    "records — who to hunt, who to fear.\n"
    "5) 3-4 concrete, prioritized tips to climb — tie them to the COMBAT PROFILE (which combos to "
    "lean on, which leak to fix to beat the players above them), event selection, when to grind "
    "ground-state wins.\n"
    "6) One-line verdict on their Top-N odds.\n"
    "Be specific, cite the numbers, and stay under ~550 words. This is a strategist's game plan, "
    "not a restatement of the tables.\n"
    "\n"
    "ACCURACY: never state a count or category you have not verified against the JSON. Respect "
    "sample size — a combo with few battles or from a single event is 'promising, needs testing', "
    "never a core recommendation for a must-win event; say the battle count when you praise it.\n"
    "SPIN: if `combat_profile.spin_analysis` is present, address spin coverage explicitly — an "
    "opposite-spin blade converts a losing same-spin stamina race into an equalization win.\n\nJSON:\n"
)


def _compact_combat(res):
    """Trim a coach() result to the combat essentials the battle plan needs (token-light)."""
    if not res:
        return None
    rec = res.get("recommendation") or {}
    combos = sorted((res.get("combos") or {}).items(), key=lambda z: -(z[1].get("ppb") or 0))
    g = res.get("grounded") or {}
    land = g.get("landscape") or {}
    spin_mix = land.get("spin_mix") or {}
    spin_note = None
    if spin_mix:
        dom = max(spin_mix, key=spin_mix.get)
        tot = sum(spin_mix.values()) or 1
        opp = "left" if dom == "right" else "right"
        spin_note = (f"{spin_mix.get(dom, 0)} of the {tot} combos they most often face are "
                     f"{dom}-spin; an opposite-spin ({opp}) blade equalizes instead of racing.")
    return {
        "archetype": res.get("archetype"), "style": res.get("style"),
        # battles/events included so the model cannot praise a thin sample as a core piece
        "top_combos": {k: {"win": v.get("win_pct"), "ppb": v.get("ppb"), "btl": v.get("battles"),
                           "events": v.get("events"), "tier": v.get("tier"), "trend": v.get("trend")}
                       for k, v in combos[:6]},
        "weaknesses": [w.get("text") for w in res.get("weaknesses", [])][:5],
        "strengths": [s.get("text") for s in res.get("strengths", [])][:4],
        "loss_finishes": res.get("loss_finishes"),
        "recommended_deck": [x.get("combo") for x in rec.get("deck", [])],
        # why a stronger combo is NOT in the deck — without this the battle plan recommends
        # e.g. Cobalt 9-60 while the coaching report correctly runs 5-60, and they contradict
        "deck_part_conflicts": rec.get("part_conflicts"),
        "nemeses": [{"opp": n.get("player"), "record": n.get("record"),
                     "recent": (n.get("recent") or {}).get("form")} for n in res.get("nemeses", [])],
        "spin_analysis": spin_note,
        "field_spin_mix": spin_mix,
        "part_vs_skill_tally": {v: sum(1 for d in (g.get("diagnoses") or [])
                                       if d.get("verdict") == v)
                                for v in {d.get("verdict") for d in (g.get("diagnoses") or [])}},
    }


def _compact_plan(d):
    """Trim the ranking report (report.build) to the fields the climb plan needs."""
    def _rival(x):
        h = x.get("h2h")
        return {"player": x["player"], "rank": x.get("to_rank") or x.get("rank"),
                "score": x.get("score"), "slots_left": x.get("slots_left"),
                "h2h": (h["record"] if h else None)}
    thr = d.get("threats") or {}
    # standings around the cutoff give the model the neighbourhood it's fighting in
    target = d.get("target_rank") or 10
    near = [s for s in d.get("standings", []) if s["rank"] <= target + 5]
    return {
        "player": d.get("player"), "current_rank": d.get("current_rank"),
        "current_score": d.get("current_score"), "target_rank": target,
        "cutoff": d.get("cutoff"), "slots_left": d.get("slots_left"),
        "events_so_far": d.get("n_events"), "field_size": d.get("field_size"),
        "open_invitational_spots": d.get("open_spots"),
        "scenarios": [{"strategy": p["strategy"], "total": p["total"],
                       "p_top": p["p_top"], "p_spot": p.get("p_stage"),
                       "median_rank": p["median_rank"]} for p in d.get("predictions", [])],
        "overtook_me": [_rival(o) for o in thr.get("overtook", [])],
        "can_still_catch_me": [_rival(t) for t in thr.get("live", [])],
        "standings_near_cutoff": near,
    }


def build_plan_prompt(d, combat=None):
    payload = {"ranking": _compact_plan(d)}
    if combat:
        payload["combat_profile"] = combat
    return TASK_PLAN + json.dumps(payload, default=str)


# ---------------- key resolution ----------------
def _scan_for_key(path):
    """Find the first sk-ant-… string anywhere in a JSON file (e.g. Jarvis runtime_config.json)."""
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            raw = fh.read()
    except Exception:
        return None
    m = re.search(r"sk-ant-[A-Za-z0-9_\-]+", raw)
    return m.group(0) if m else None


def resolve_key(api_key=None, cfg=None):
    cfg = cfg or {}
    if api_key:
        return api_key
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    if cfg.get("anthropic_api_key"):
        return cfg["anthropic_api_key"]
    if cfg.get("anthropic_key_file"):
        k = _scan_for_key(cfg["anthropic_key_file"])
        if k:
            return k
    return None   # let the SDK try its own resolution (ant profile, etc.)


# ---------------- client (indirected for testing) ----------------
def _client(api_key):
    import anthropic
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def analyze(res, api_key=None, model=DEFAULT_MODEL, cfg=None, max_tokens=4000):
    """Return (analyst_text, None) on success or (None, reason) on failure. Never raises."""
    return _call(SYSTEM, build_prompt(res), api_key=api_key, model=model, cfg=cfg, max_tokens=max_tokens)


def battleplan(d, api_key=None, model=DEFAULT_MODEL, cfg=None, max_tokens=12000, combat=None):
    """AI battle plan for the ranking/climb report, optionally grounded in a compact combat
    profile (from a coach() result). (plan_text, None) or (None, reason). Never raises."""
    return _call(SYSTEM, build_plan_prompt(d, combat), api_key=api_key, model=model, cfg=cfg, max_tokens=max_tokens)


def _call(system, prompt, api_key=None, model=DEFAULT_MODEL, cfg=None, max_tokens=4000):
    """Shared Claude call for the analyst layers. Returns (text, None) or (None, reason); never raises."""
    try:
        import anthropic  # noqa: F401
    except Exception:
        return None, ("the 'anthropic' package isn't installed. Run: pip install anthropic  "
                      "(and set ANTHROPIC_API_KEY, or --ai-key).")
    key = resolve_key(api_key, cfg)
    try:
        client = _client(key)
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
        )
        if getattr(resp, "stop_reason", None) == "refusal":
            return None, "Claude declined to analyze this request."
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        return (text, None) if text else (None, "Claude returned an empty response.")
    except Exception as ex:
        name, msg = type(ex).__name__, str(ex)
        if ("Authentication" in name or "auth" in msg.lower() or "api_key" in msg
                or "x-api-key" in msg.lower()):
            return None, ("no valid Claude API key. Create one at platform.claude.com "
                          "(Settings -> API keys, it looks like sk-ant-api...), then set "
                          "ANTHROPIC_API_KEY or pass --ai-key. A little billing credit is needed.")
        if "Connection" in name:
            return None, f"could not reach the Claude API ({ex})."
        return None, f"AI layer error ({name}): {ex}"


# ---------------- rendering ----------------
def _norm_combo(s):
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def combo_call_map(reconcile):
    """{normalized combo -> {call, why}} so the combo table can badge the AI's final call."""
    out = {}
    for c in (reconcile or {}).get("combo_calls", []) or []:
        if c.get("combo"):
            out[_norm_combo(c["combo"])] = {"call": (c.get("call") or "").lower(), "why": c.get("why") or ""}
    return out


CALL_COLORS = {"anchor": "#39ff14", "keep": "#57e26b", "tune": "#ff8c1a", "bench": "#ff5555"}


def to_txt(notes, reconcile=None):
    L = ["\n\nAI BEYBLADE ANALYST", "-" * 52, notes or ""]
    r = reconcile or {}
    if r.get("conflicts"):
        L.append("\nConflicts reconciled by AI:")
        for c in r["conflicts"]:
            L.append(f"  • {c.get('topic', '')}: {c.get('resolution', '')}")
    if r.get("combo_calls"):
        L.append("\nAI's final call per combo:")
        for c in r["combo_calls"]:
            L.append(f"  [{(c.get('call') or '?').upper():6}] {c.get('combo', '')} — {c.get('why', '')}")
    if r.get("recommendation_note"):
        L.append(f"\nAI note on the recommended deck: {r['recommendation_note']}")
    if r.get("top10"):
        L.append(f"\nTop-10 verdict: {r['top10']}")
    return "\n".join(L) + "\n"


def _md_to_html(text, e):
    """Tiny markdown -> html: ## headings, - bullets, **bold**, blank-line paragraphs."""
    out, in_ul = [], False
    for raw in (text or "").split("\n"):
        line = raw.rstrip()
        stripped = line.lstrip()
        if stripped.startswith(("- ", "* ")):
            if not in_ul:
                out.append("<ul>"); in_ul = True
            item = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", e(stripped[2:]))
            out.append(f"<li>{item}</li>")
            continue
        if in_ul:
            out.append("</ul>"); in_ul = False
        b = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", e(line))
        if not stripped:
            out.append("")
        elif stripped.startswith("### "):
            out.append(f"<h4>{e(stripped[4:])}</h4>")
        elif stripped.startswith("## "):
            out.append(f"<h3>{e(stripped[3:])}</h3>")
        elif re.match(r"^\d+[\).]\s", stripped):
            out.append(f'<div class="row">{b}</div>')
        else:
            out.append(f"<p>{b}</p>")
    if in_ul:
        out.append("</ul>")
    return "\n".join(out)


def to_html(notes, theme, reconcile=None):
    import html as _h
    e = _h.escape
    body = _md_to_html(notes, e)
    r = reconcile or {}
    muted = theme.get("muted", "#6b7280")
    extra = ""
    if r.get("conflicts"):
        rows = "".join(f'<div class="row"><b>{e(c.get("topic", ""))}</b> — {e(c.get("resolution", ""))}</div>'
                       for c in r["conflicts"])
        extra += f'<h4>⚖ Conflicts reconciled</h4>{rows}'
    if r.get("combo_calls"):
        chips = "".join(
            f'<span class="tag" style="border-color:{CALL_COLORS.get((c.get("call") or "").lower(), muted)};'
            f'color:{CALL_COLORS.get((c.get("call") or "").lower(), muted)}">'
            f'{e((c.get("call") or "?").upper())} · {e(c.get("combo", ""))}</span> '
            for c in r["combo_calls"])
        extra += f'<h4>Final call per combo</h4><div style="line-height:2">{chips}</div>'
    if r.get("top10"):
        extra += f'<h4>Top-10 verdict</h4><div class="row">{e(r["top10"])}</div>'
    return ('<h2>🧠 AI Beyblade Analyst <span class="pill">AI-enabled</span></h2>'
            f'<div class="ai">{body}{extra}'
            '<div class="sub" style="margin-top:10px">AI Beyblade Analyst — the '
            'raw numbers above are the pipeline\'s and unchanged; when AI is on it reconciles the '
            'wording so the sections agree.</div></div>')



def plan_to_txt(notes):
    return "\n\nAI BATTLE PLAN — road to the top (AI Beyblade Analyst)\n" + "-" * 62 + "\n" + notes + "\n"


def plan_to_html(notes, theme):
    import html as _h
    body = _md_to_html(notes, _h.escape)
    return ('<h2>⚑ AI battle plan <span class="pill">AI Beyblade Analyst</span></h2>'
            f'<div class="ai">{body}'
            '<div class="sub" style="margin-top:10px">AI Beyblade Analyst — the '
            'standings, scenarios and threats above are the pipeline\'s and unchanged; this is a '
            'coach\'s reading of them.</div></div>')
