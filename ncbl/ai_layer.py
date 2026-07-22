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
    scouting = [{"opp": s["opponent"], "record": s["record"], "predictability": s["predictability"],
                 "label": s["pred_label"], "meta": (s.get("meta_style") or {}).get("tag"),
                 "likely": [p.get("combo") for p in (s.get("readout") or [])][:4]}
                for s in pred.get("scouting", [])]
    return {
        "player": res.get("player"), "scope": res.get("scope"),
        "events": res.get("events"), "confidence": res.get("confidence"),
        "archetype": res.get("archetype"), "style": res.get("style"),
        "goal": res.get("goal"), "combos": combos(res.get("combos", {})),
        "loss_finishes": res.get("loss_finishes"),
        "weaknesses": [{"t": w["text"], "fix": w["suggestion"], "sev": w["severity"]} for w in res.get("weaknesses", [])],
        "strengths": [s["text"] for s in res.get("strengths", [])],
        "swaps": res.get("swaps"), "meta_field": res.get("meta"),
        "recommendation": {"deck": [d["combo"] for d in (res.get("recommendation") or {}).get("deck", [])],
                           "bench": [b["combo"] for b in (res.get("recommendation") or {}).get("bench", [])],
                           "note": (res.get("recommendation") or {}).get("note")},
        "rivals": [{"p": r["player"], "rec": f"{r['wins']}-{r['losses']}", "src": r.get("source")}
                   for r in res.get("rivals", [])],
        "nemeses": res.get("nemeses"), "launch": res.get("launch"),
        "field_benchmark": res.get("field"),
        "rival_scouting": scouting,
        "meta_counter": (pred.get("meta_counter") or {}),
    }


def build_prompt(res):
    return TASK + json.dumps(_compact(res), default=str)


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
            system=SYSTEM,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": build_prompt(res)}],
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
def to_txt(notes):
    return "\n\nAI ANALYST NOTES (via your personal Claude account)\n" + "-" * 52 + "\n" + notes + "\n"


def _md_to_html(text, e):
    """Tiny markdown -> html: ## headings, - bullets, **bold**, blank-line paragraphs."""
    out, in_ul = [], False
    for raw in text.split("\n"):
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


def to_html(notes, theme):
    import html as _h
    body = _md_to_html(notes, _h.escape)
    return ('<h2>⚑ AI analyst notes <span class="pill">via your personal Claude</span></h2>'
            f'<div class="ai">{body}'
            '<div class="sub" style="margin-top:10px">AI commentary layer (Claude Opus 4.8) — the '
            'numbers above are the pipeline\'s and unchanged; this reading may over-read small samples.</div></div>')
