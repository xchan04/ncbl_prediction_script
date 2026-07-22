"""Challonge ingestion: head-to-head records from tournament brackets.

Challonge brackets have match results (who beat whom, scores) but no beyblade
combos. They're useful for two things the NCBLAST reports/rankings miss:
  1) opponents who keep beating you (head-to-head nemeses), and
  2) coverage for tournaments that never published an NCBLAST report.

The public Challonge REST API v1 needs a free API key
(challonge.com -> Developer API). Org-subdomain URLs like
`ncbl.challonge.com/goonday` map to the API id `ncbl-goonday`.

Network is optional: fetched JSON is cached to disk, so after one pull the
head-to-head analysis runs fully offline.
"""
from __future__ import annotations
import json
import os
import re
import urllib.parse
import urllib.request
from collections import defaultdict

API = "https://api.challonge.com/v1/tournaments/{id}.json"


# ---------------- slugs ----------------
def slug_from_url(url):
    """`https://ncbl.challonge.com/goonday/standings` -> `ncbl-goonday`.
    A bare `challonge.com/abcd` (no org) -> `abcd`."""
    m = re.search(r"(?:https?://)?(?:([a-z0-9-]+)\.)?challonge\.com/([A-Za-z0-9_]+)", url)
    if not m:
        return None
    org, slug = m.group(1), m.group(2)
    if org and org not in ("www", "challonge"):
        return f"{org}-{slug}"
    return slug


def slugs_from_sheet(path, cfg):
    """Harvest Challonge tournament slugs from the Data-Entry sheet's links."""
    from .loader import League
    lg = League(cfg).load(path)
    slugs = []
    for t in lg.tournaments:
        if "challonge.com" in str(t):
            s = slug_from_url(t)
            if s and s not in slugs:
                slugs.append(s)
    return slugs


def slugs_from_file(path):
    """Read Challonge links/slugs from a manual list file — one per tournament.

    Accepts .txt / .md (one URL or slug per line, or URLs embedded in markdown; '#' lines
    are comments) or .json (a list, or {"links": [...]} / {"slugs": [...]}). Returns unique
    slugs in order. This is the fallback when links can't be harvested from reports/sheet."""
    slugs = []

    def add(tok):
        tok = str(tok).strip().strip("<>()[]")
        if not tok:
            return
        s = slug_from_url(tok) or tok
        if s and s not in slugs:
            slugs.append(s)

    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    if os.path.splitext(path)[1].lower() == ".json":
        data = json.loads(raw)
        items = data if isinstance(data, list) else (data.get("links") or data.get("slugs") or [])
        for it in items:
            add(it)
    else:
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls = re.findall(r"(?:https?://)?[\w-]*\.?challonge\.com/[A-Za-z0-9_]+", line)
            if urls:
                for u in urls:
                    add(u)
            else:
                add(line.split()[0])   # a bare slug per line
    return slugs


# ---------------- fetch (cache-first) ----------------
def fetch(slug, api_key=None, cache_dir="challonge_cache", timeout=20):
    """Return the raw tournament JSON dict. Uses the on-disk cache if present;
    otherwise hits the API (needs api_key) and caches the result."""
    os.makedirs(cache_dir, exist_ok=True)
    cache = os.path.join(cache_dir, f"{slug}.json")
    if os.path.exists(cache):
        with open(cache) as fh:
            return json.load(fh)
    if not api_key:
        raise RuntimeError(f"{slug}: not cached and no API key given. Set --api-key / "
                           "CHALLONGE_API_KEY to fetch (then it caches for offline use).")
    q = urllib.parse.urlencode({"include_participants": 1, "include_matches": 1, "api_key": api_key})
    url = API.format(id=urllib.parse.quote(slug)) + "?" + q
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = json.load(r)
    with open(cache, "w") as fh:
        json.dump(data, fh)
    return data


# ---------------- parse ----------------
def parse_tournament(data):
    """Challonge JSON -> {name, participants{id:name}, matches[(p1,p2,winner,scores)]}."""
    t = data.get("tournament", data)
    parts = {}
    for p in t.get("participants", []):
        pp = p.get("participant", p)
        parts[pp["id"]] = pp.get("name") or pp.get("display_name") or str(pp["id"])
    matches = []
    for m in t.get("matches", []):
        mm = m.get("match", m)
        p1, p2, win = mm.get("player1_id"), mm.get("player2_id"), mm.get("winner_id")
        if p1 in parts and p2 in parts and win in (p1, p2):
            matches.append((parts[p1], parts[p2], parts[win], mm.get("scores_csv", "")))
    return {"name": t.get("name") or t.get("id"), "participants": parts, "matches": matches}


def _norm(name):
    return re.sub(r"\s+", "", str(name)).lower()


def load_cache(cache_dir, season=None, seasons_cfg=None):
    """Load all cached tournament JSON in a dir -> list of parse_tournament dicts.
    Optionally keep only tournaments inside a named season's date window."""
    import glob
    out = []
    for f in sorted(glob.glob(os.path.join(cache_dir, "*.json"))):
        try:
            with open(f) as fh:
                t = parse_tournament(json.load(fh))
        except Exception:
            continue
        if season and seasons_cfg and not _in_season(_tournament_date(json.load(open(f))), season, seasons_cfg):
            continue
        out.append(t)
    return out


def _tournament_date(data):
    t = data.get("tournament", data)
    return (t.get("started_at") or t.get("created_at") or "")[:10]


def _in_season(date_str, season, seasons_cfg):
    win = seasons_cfg.get(season)
    if not win or not date_str:
        return True
    return win[0] <= date_str <= win[1]


# ---------------- head-to-head ----------------
def head_to_head(tournaments, player):
    """Aggregate a player's record vs each opponent across tournaments.
    `tournaments` = list of parse_tournament() dicts. Returns sorted nemeses first."""
    pn = _norm(player)
    rec = defaultdict(lambda: {"wins": 0, "losses": 0, "events": set()})
    for t in tournaments:
        for p1, p2, win, _ in t["matches"]:
            names = {_norm(p1): p1, _norm(p2): p2}
            if pn not in names:
                continue
            opp_disp = p2 if _norm(p1) == pn else p1
            r = rec[opp_disp]
            r["events"].add(t["name"])
            if _norm(win) == pn:
                r["wins"] += 1
            else:
                r["losses"] += 1
    out = []
    for opp, r in rec.items():
        out.append({"opponent": opp, "wins": r["wins"], "losses": r["losses"],
                    "played": r["wins"] + r["losses"], "events": len(r["events"]),
                    "win_pct": round(100 * r["wins"] / max(1, r["wins"] + r["losses"]), 1)})
    out.sort(key=lambda z: (z["wins"] - z["losses"], -z["played"]))   # nemeses first
    return out


def analyze(tournaments, player):
    h2h = head_to_head(tournaments, player)
    nemeses = [h for h in h2h if h["losses"] > h["wins"] and h["played"] >= 2]
    owned = [h for h in h2h if h["wins"] > h["losses"] and h["played"] >= 2]
    total_events = len({t["name"] for t in tournaments})
    return {"player": player, "events": total_events, "h2h": h2h,
            "nemeses": nemeses, "owned": owned[::-1][:10]}


# ---------------- rendering ----------------
def to_txt(a):
    L = [f"{a['player']} — Challonge head-to-head ({a['events']} tournaments)", ""]
    L.append("NEMESES (lose to more than you beat)")
    for h in a["nemeses"]:
        L.append(f"  {h['opponent']:20} {h['wins']}-{h['losses']}  ({h['win_pct']}%, {h['events']} events)")
    if not a["nemeses"]:
        L.append("  (none — no recurring losing head-to-heads)")
    L.append("\nYOU OWN")
    for h in a["owned"]:
        L.append(f"  {h['opponent']:20} {h['wins']}-{h['losses']}  ({h['win_pct']}%)")
    return "\n".join(L) + "\n"


def to_json(a):
    return json.dumps(a, indent=2)


def to_html(a, cfg):
    import html
    th = cfg.get("theme", {})
    bg, fg, orange = th.get("bg", "#000"), th.get("fg", "#e6edf3"), th.get("player", "#ff8c1a")
    green, red, muted, border = "#57e26b", th.get("cutoff", "#ff5555"), th.get("muted", "#6b7280"), "#241a0e"
    e = html.escape

    def rows(items):
        return "".join(
            f'<tr><td>{e(h["opponent"])}</td>'
            f'<td style="text-align:right;color:{green if h["wins"]>=h["losses"] else red}">{h["wins"]}-{h["losses"]}</td>'
            f'<td style="text-align:right">{h["win_pct"]}%</td>'
            f'<td style="text-align:right;color:{muted}">{h["events"]}</td></tr>' for h in items) or \
            f'<tr><td colspan="4" style="color:{muted}">none</td></tr>'
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{e(a['player'])} — H2H</title>
<style>body{{background:{bg};color:{fg};font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;padding:32px}}
 .wrap{{max-width:720px;margin:0 auto}} h1{{color:{orange}}} h2{{color:{orange};border-bottom:1px solid {border};padding-bottom:6px;margin-top:28px}}
 table{{width:100%;border-collapse:collapse;font-size:15px}} th,td{{padding:7px 10px;border-bottom:1px solid {border}}} th{{color:{muted};text-align:left}}</style>
</head><body><div class="wrap"><h1>{e(a['player'])}</h1>
 <div style="color:{muted}">Challonge head-to-head · {a['events']} tournaments</div>
 <h2>Nemeses</h2><table><thead><tr><th>Opponent</th><th style="text-align:right">Record</th><th style="text-align:right">Win%</th><th style="text-align:right">Events</th></tr></thead><tbody>{rows(a['nemeses'])}</tbody></table>
 <h2>You own</h2><table><tbody>{rows(a['owned'])}</tbody></table></div></body></html>"""


def write_all(a, cfg, basepath):
    paths = []
    for ext, text in (("txt", to_txt(a)), ("json", to_json(a)), ("html", to_html(a, cfg))):
        p = f"{basepath}.{ext}"
        with open(p, "w") as fh:
            fh.write(text)
        paths.append(p)
    return paths
