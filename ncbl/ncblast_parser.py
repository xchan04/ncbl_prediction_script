"""Parse an NCBLAST tournament-report PDF into a structured dict.

The reports are a fixed 7-section template (ncblast.pages.dev). We parse each
section independently and defensively: a section that fails to parse yields an
empty result rather than crashing the whole report.

Requires: pdfplumber.
"""
from __future__ import annotations
import re

FINISH_TYPES = ("Xtreme", "Over", "Spin", "Burst")
DASH = "[-–—]"          # hyphen / en-dash / em-dash
_num = r"[+-]?\d+(?:\.\d+)?"


def _pages_text(path):
    import pdfplumber
    import logging
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    out = []
    with pdfplumber.open(path) as pdf:
        for pg in pdf.pages:
            out.append(pg.extract_text() or "")
    return out


def parse(path):
    pages = _pages_text(path)
    full = "\n".join(pages)
    lines = [ln.rstrip() for ln in full.splitlines()]
    combos = _combos(lines)
    combo_names = [c["combo"] for c in combos]
    rep = {
        "source": path,
        "player": _player(lines),
        "event": _event(lines),
        "date": _date(lines),
        "totals": _totals(lines),
        "combos": combos,
        "tiers": _tiers(full, combo_names),
        "finishes": _finishes(lines),
        "matchups": _matchups(lines, combo_names),
        "peers": _peers(lines, combo_names),
        "style": _style(lines),
        "archetype": _archetype(lines),
        "matches": _matches(lines),
        "dynamics": _dynamics(full),
    }
    # attach tier onto each combo by index/name
    for c in rep["combos"]:
        c["tier"] = rep["tiers"].get(c["combo"])
    return rep


def _collapsed(s):
    return re.sub(r"\s", "", s).upper()


# ---------------- cover / totals ----------------
def _player(lines):
    for i, ln in enumerate(lines):
        if _collapsed(ln) == "PLAYER":           # matches "PLAYER" and letter-spaced "P L A Y E R"
            for j in range(i + 1, min(i + 5, len(lines))):
                s = lines[j].strip()
                if s and "NORCAL" not in s.upper():
                    s = re.sub(r"^[A-Z]{1,2}\s+", "", s)     # strip leading avatar initials
                    if s and s not in ("E", "ES") and not re.fullmatch(r"[A-Z]{1,2}", s):
                        return s
    return None


def _event(lines):
    for i, ln in enumerate(lines):
        if "EVENTDATEFORMAT" in _collapsed(ln) and i + 1 < len(lines):
            m = re.match(r"(.+?)\s+[A-Z][a-z]+ \d{1,2}, \d{4}", lines[i + 1])
            if m:
                return m.group(1).strip()
    return None


def _date(lines):
    for ln in lines:
        m = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", ln)
        if m:
            return m.group(1)
    return None


def _totals(lines):
    t = {}
    for i, ln in enumerate(lines):
        if _collapsed(ln).startswith("RECORDPLACEMENTPPBWIN%") and i + 1 < len(lines):
            m = re.search(rf"(\d+){DASH}(\d+)\s+(\S+)\s+({_num})\s+([\d.]+)%", lines[i + 1])
            if m:
                t["wins"] = int(m.group(1)); t["losses"] = int(m.group(2))
                t["placement"] = m.group(3); t["ppb"] = float(m.group(4)); t["win_pct"] = float(m.group(5))
    for ln in lines:
        m = re.search(r"(\d+)\s+battles", ln)
        if m and "total_battles" not in t:
            t["total_battles"] = int(m.group(1))
    return t


# ---------------- combos used ----------------
def _combos(lines):
    out = []
    rx = re.compile(rf"^(\d{{1,2}})\s+(.+?)\s+(\d+)\s+([\d.]+)%\s+({_num})$")
    for ln in lines:
        m = rx.match(ln.strip())
        if m and _looks_like_combo(m.group(2)):
            out.append({"idx": int(m.group(1)), "combo": _norm(m.group(2)),
                        "battles": int(m.group(3)), "win_pct": float(m.group(4)),
                        "ppb": float(m.group(5))})
    # keep first occurrence per idx (Combos Used table)
    seen = {}
    for c in out:
        seen.setdefault(c["idx"], c)
    return [seen[k] for k in sorted(seen)]


def _looks_like_combo(s):
    # ratchet like "9-60"; some PDFs render the digits as replacement glyphs (font issue)
    return re.search(r"[\d�]+[-–][\d�]+", s) is not None


def _norm(combo):
    return re.sub(r"\s+", " ", combo.replace("�", "?")).strip()


# ---------------- tiers ----------------
def _tiers(full, combo_names):
    """Map combo name -> tier letter using 'COMBO NN X NN btl' order."""
    letters = re.findall(r"COMBO\s+(\d{1,2})\s+([SABCD])\s+\d+\s+btl", full)
    by_idx = {int(i): t for i, t in letters}
    out = {}
    for i, name in enumerate(combo_names, 1):
        if i in by_idx:
            out[name] = by_idx[i]
    return out


# ---------------- finishes ----------------
def _finishes(lines):
    win, loss = {}, {}
    for ln in lines:
        s = ln.strip()
        # winning finishes (NOT preceded by "Opp ") — may share a line with the loss column
        for m in re.finditer(r"(?<!Opp )\b(Xtreme|Over|Spin|Burst)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)%", s):
            win[m.group(1)] = {"count": int(m.group(2)), "total_pts": int(m.group(4)), "pct": float(m.group(5))}
        for m in re.finditer(r"Opp (Xtreme|Over|Spin|Burst)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)%", s):
            loss["Opp " + m.group(1)] = {"count": int(m.group(2)), "total_pts": int(m.group(4)), "pct": float(m.group(5))}
        m = re.search(r"Own \(self-KO\)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)%", s)
        if m:
            loss["Own (self-KO)"] = {"count": int(m.group(1)), "total_pts": int(m.group(3)), "pct": float(m.group(4))}
    return {"win": win, "loss": loss}


# ---------------- recurring matchups ----------------
def _matchups(lines, combo_names):
    out = []
    tail = re.compile(rf"\s+(\d+)\s+(\d+){DASH}(\d+)\s+([\d.]+)%\s+({_num})\s+({_num})$")
    ordered = sorted(combo_names, key=len, reverse=True)
    for ln in lines:
        s = ln.strip()
        m = tail.search(s)
        if not m:
            continue
        prefix = s[: m.start()].strip()
        your = next((c for c in ordered if prefix.startswith(c)), None)
        if not your:
            continue
        opp = _norm(prefix[len(your):])
        if not opp:
            continue
        out.append({"your_combo": your, "opp_combo": opp, "faced": int(m.group(1)),
                    "wins": int(m.group(2)), "losses": int(m.group(3)),
                    "win_pct": float(m.group(4)), "ppb": float(m.group(5)), "net": int(float(m.group(6)))})
    return out


# ---------------- peer comparison ----------------
def _peers(lines, combo_names):
    """Rows of (combo, player, win%, ppb, battles) within the peer-comparison section.
    Combos are listed in deck order; labels wrap across lines, so we advance the
    current combo whenever a line begins the next expected combo."""
    # scope to the peer section
    start = next((i for i, ln in enumerate(lines) if "Peer Comparison" in ln), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(lines))
                if "Player Profile" in lines[i] or "STYLE FINGERPRINT" in lines[i]), len(lines))
    section = lines[start:end]
    out = []
    rx = re.compile(rf"^[··]?\s*(.+?)\s+([\d.]+)%\s+({_num})\s+(\d+)$")
    order = combo_names
    idx, current = -1, None
    for ln in section:
        s = ln.strip()
        for k in range(idx + 1, len(order)):
            first2 = " ".join(order[k].split()[:2])
            if first2 and s.startswith(first2):
                idx, current = k, order[k]
                break
        m = rx.match(s)
        if not m:
            continue
        name = m.group(1).strip()
        for c in order:
            if name.startswith(c):
                name = _norm(name[len(c):]) or "YOU"
                break
        if _looks_like_combo(name) or name in ("", "PLAYER"):
            continue
        out.append({"combo": current, "player": name, "win_pct": float(m.group(2)),
                    "ppb": float(m.group(3)), "battles": int(m.group(4))})
    return out


# ---------------- style / archetype ----------------
def _style(lines):
    out = {}
    for ln in lines:
        m = re.match(r"^(EFFICIENCY|AGGRESSION|CLUTCH|DECK USAGE|ADAPTABILITY)\s*[★★]?\s*(\d{1,3})\b", ln.strip())
        if m:
            out[m.group(1).title().replace(" Usage", " Usage")] = int(m.group(2))
    return out


def _archetype(lines):
    for i, ln in enumerate(lines):
        if ln.strip() == "ARCHETYPE":
            for j in range(i + 1, min(i + 4, len(lines))):
                s = lines[j].strip()
                if s and s not in ("★", "*"):
                    return re.split(r"\s{2,}|High |Clutch and", s)[0].strip() or s
    return None


# ---------------- match recap ----------------
def _matches(lines):
    """Parse the per-match recap. Two report layouts exist:
      * inline  : 'WIN vs Name 2-0 sets · 7 btl · NET +8'  (opponent on the result line)
      * split   : 'vs Name' on its own line, then 'LOSS 1-2 sets · 14 btl · NET -1'
                  (the §06 EVERY MATCH layout). The opponent name precedes the result.
    Both feed the same record; opponent-combo rows that follow attach to the current match.
    """
    out = []
    # inline: result line carries "vs Name"; tolerant of any "· PPB ..." tail after NET
    head_inline = re.compile(rf"^(WIN|LOSS)\s+vs\s+(.+?)\s+(\d+){DASH}(\d+)\s+sets\b[^\n]*?(\d+)\s*btl[^\n]*?NET\s+({_num})", re.I)
    # split: result line has no name (name came from a preceding 'vs Name' line)
    head_split = re.compile(rf"^(WIN|LOSS)\s+(\d+){DASH}(\d+)\s+sets\b[^\n]*?(\d+)\s*btl[^\n]*?NET\s+({_num})", re.I)
    opp_line = re.compile(r"^vs\s+(\S.*)$", re.I)
    row = re.compile(rf"^(.+?)\s+(\d+){DASH}(\d+)\s+({_num})$")
    cur = None
    pending_opp = None
    for ln in lines:
        s = ln.strip()
        h = head_inline.match(s)
        if h:
            cur = {"result": h.group(1).upper(), "opponent": h.group(2).strip(),
                   "sets": f"{h.group(3)}-{h.group(4)}", "battles": int(h.group(5)),
                   "net": int(float(h.group(6))), "opp_combos": []}
            out.append(cur)
            pending_opp = None
            continue
        h = head_split.match(s)
        if h:
            cur = {"result": h.group(1).upper(), "opponent": (pending_opp or "?").strip(),
                   "sets": f"{h.group(2)}-{h.group(3)}", "battles": int(h.group(4)),
                   "net": int(float(h.group(5))), "opp_combos": []}
            out.append(cur)
            pending_opp = None
            continue
        m = row.match(s)
        if m and _looks_like_combo(m.group(1)):
            if cur is not None:
                cur["opp_combos"].append({"combo": _norm(m.group(1)), "wl": f"{m.group(2)}-{m.group(3)}",
                                          "match_ppb": float(m.group(4))})
            continue
        # a bare "vs Name" line names the *next* split-layout result
        o = opp_line.match(s)
        if o and not _looks_like_combo(s):
            pending_opp = o.group(1).strip()
    return out



# ---------------- battle dynamics (side split + points distribution) ----------------
def _dynamics(full):
    d = {"side": {}, "points_dist": {}}
    # overall B-side / X-side split: two decimal percentages, then two "N btl · PPB ±x"
    pct = re.search(r"(\d+\.\d+)%\s+(\d+\.\d+)%", full)
    bp = re.search(rf"(\d+)\s*btl\s*[·.\-]?\s*PPB\s*({_num})\s+(\d+)\s*btl\s*[·.\-]?\s*PPB\s*({_num})", full)
    if pct and bp:
        d["side"] = {
            "B": {"win_pct": float(pct.group(1)), "battles": int(bp.group(1)), "ppb": float(bp.group(2))},
            "X": {"win_pct": float(pct.group(2)), "battles": int(bp.group(3)), "ppb": float(bp.group(4))},
        }
    # per-combo points distribution: lines like "Aero 23 17 +6"
    for m in re.finditer(r"^([A-Z][A-Za-z]+)\s+(\d+)\s+(\d+)\s+([+-]?\d+)$", full, re.M):
        d["points_dist"][m.group(1)] = {"scored": int(m.group(2)), "allowed": int(m.group(3)), "net": int(m.group(4))}
    return d
