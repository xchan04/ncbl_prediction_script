"""Best-effort, schema-agnostic parser for NCBLAST report JSON.

The NCBLAST devs may hand us JSON whose exact key names and nesting we don't
know ahead of time and which can change without notice. Rather than hard-code a
schema, this module extracts the same fields the PDF parser produces by:

  * matching keys against alias sets            (name-agnostic, within reason)
  * deep-searching the whole tree               (location-agnostic)
  * validating candidate collections by shape   (shape-agnostic)
  * coercing messy values                        ("63.2%" / 0.632 / 63.2 -> 63.2)

falling back to shape-only detection when the names are unfamiliar. It never
raises on unexpected structure — a field it can't find degrades to empty, just
like the PDF parser, so `coaching` consumes JSON and PDF reports identically.
No AI, and no code change is required when the layout shifts (a genuinely new
key name is the only thing that needs an alias added).

This is *best effort*, not a guarantee: JSON that renames everything to opaque
tokens or nests in a wholly unexpected way may parse partially. The design goal
is maximum resilience and graceful degradation, never a crash.
"""
from __future__ import annotations
import json
import re

FINISH_TYPES = ("Xtreme", "Over", "Spin", "Burst")

# ---- alias sets (compared against normalized keys: lowercased, alnum only) ----
PLAYER      = {"player", "playername", "username", "handle", "gamertag", "user", "competitor", "name"}
EVENT       = {"event", "eventname", "tournament", "tournamentname", "title", "eventtitle"}
DATE        = {"date", "eventdate", "playedon", "datetime", "day", "when"}
ARCHETYPE   = {"archetype", "playertype", "profile"}

WINPCT      = {"winpct", "winpercent", "winpercentage", "winrate", "wr", "winratio", "wpct"}
PPB         = {"ppb", "pointsperbattle", "pointperbattle", "avgppb", "netppb", "avgpoints", "ppbavg"}
BATTLES     = {"battles", "btl", "games", "totalbattles", "numbattles", "battlecount", "gamesplayed"}
WINS        = {"wins", "win", "won", "w"}
LOSSES      = {"losses", "loss", "lost", "l"}
PLACEMENT   = {"placement", "place", "finish", "rank", "standing", "position"}
FACED       = {"faced", "encounters", "seen", "times", "played", "count", "meetings"}
NET         = {"net", "netpoints", "pointdiff", "differential", "plusminus", "diff"}
TIER        = {"tier", "grade", "fieldtier", "rating"}

COMBO_NAME  = {"combo", "build", "deck", "comboname", "buildname", "loadout", "setup"}
BLADE       = {"blade", "lockchip", "mainblade"}
RATCHET     = {"ratchet", "disk", "disc", "track", "assistblade"}
BIT         = {"bit", "driver", "tip", "performancetip", "bitchip"}

YOURCOMBO   = {"yourcombo", "mycombo", "combo", "build", "self", "selfcombo"}
OPPCOMBO    = {"oppcombo", "opponentcombo", "vscombo", "enemycombo", "oppbuild", "opponentbuild", "against"}
OPPONENT    = {"opponent", "opp", "vs", "against", "enemy", "rival", "opponentname"}
RESULT      = {"result", "outcome", "wl", "winloss"}
SETS        = {"sets", "setscore", "setrecord", "setresult"}
WL_KEYS     = {"wl", "record", "winloss", "score", "setscore"}
OPPCOMBOS   = {"oppcombos", "opponentcombos", "combos", "builds", "oppbuilds", "combosfaced", "decksfaced"}
PEERROWS    = {"peers", "players", "rows", "comparison", "competitors", "others", "field"}

TOTALS      = {"totals", "total", "overview", "summary", "record", "stats", "overall"}
COMBOS      = {"combos", "combosused", "builds", "decks", "mycombos", "combolist", "loadouts"}
MATCHUPS    = {"matchups", "recurringmatchups", "matchup", "combomatchups", "matchupmatrix"}
PEERS       = {"peers", "peercomparison", "peercompare", "sharedcombos", "peerstats"}
MATCHES     = {"matches", "matchrecap", "recap", "matchhistory", "results", "gamelog"}
FINISHES    = {"finishes", "finish", "finishbreakdown", "finishtypes", "finishstats"}
STYLE       = {"style", "stylefingerprint", "fingerprint", "styleaxes", "playstyle"}
DYNAMICS    = {"dynamics", "battledynamics", "sidedynamics"}
STYLE_AXES  = {"axis", "name", "stat", "label", "metric"}
STYLE_VALS  = {"value", "val", "score", "rating", "pct", "percentile", "percentrank"}


# ---------------- primitives ----------------
def _nk(k):
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


def _walk(node, path=()):
    """Yield (path, key, value) for every dict entry anywhere in the tree."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield path, str(k), v
            yield from _walk(v, path + (str(k),))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, path + (i,))


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?", v.replace(",", ""))
        if m:
            return float(m.group())
    return None


def _int(v):
    n = _num(v)
    return int(round(n)) if n is not None else None


def _flt(v):
    n = _num(v)
    return round(n, 3) if n is not None else 0.0


def _pct(v):
    """Normalize a win-rate to a 0-100 percentage. Accepts 0.632, 63.2, '63.2%'."""
    n = _num(v)
    if n is None:
        return None
    if 0 <= n <= 1 and not (isinstance(v, str) and "%" in v):
        return round(n * 100, 1)     # looks like a fraction
    return round(n, 1)


def _tier(v):
    if v is None:
        return None
    s = str(v).strip().upper()
    if s in ("S", "A", "B", "C", "D"):
        return s
    return s[0] if s and s[0] in "SABCD" else None


def _title(s):
    return re.sub(r"\s+", " ", str(s).replace("_", " ")).strip().title()


def _clean_combo(s):
    return re.sub(r"\s+", " ", str(s).replace("·", " ").replace("�", "?")).strip()


# ---------------- key lookups ----------------
def _row_get(d, aliases, fuzzy=True):
    """First value in dict `d` whose normalized key matches (exact first, then substring)."""
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        if _nk(k) in aliases:
            return v
    if fuzzy:
        for k, v in d.items():
            nk = _nk(k)
            if any(a in nk for a in aliases):
                return v
    return None


def _str_field(d, aliases, fuzzy=True):
    v = _row_get(d, aliases, fuzzy)
    if isinstance(v, str) and v.strip():
        return v.strip()
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    return None


def _find_scalar(root, aliases, fuzzy=True):
    """Shallowest scalar in the tree whose key matches (exact matches beat fuzzy)."""
    best, best_rank = None, (9, 9999)
    for path, k, v in _walk(root):
        if isinstance(v, (dict, list)):
            continue
        nk = _nk(k)
        if nk in aliases:
            rank = (0, len(path))
        elif fuzzy and any(a in nk for a in aliases):
            rank = (1, len(path))
        else:
            continue
        if rank < best_rank:
            best, best_rank = v, rank
    return best


def _find_node(root, aliases, kind):
    """Best matching node of `kind` ('list' of dicts, or 'dict'). Exact key beats fuzzy, shallow beats deep."""
    best, best_rank = None, (9, 9999)
    for path, k, v in _walk(root):
        if kind == "list":
            ok = isinstance(v, list) and v and all(isinstance(x, dict) for x in v)
        else:
            ok = isinstance(v, dict) and v
        if not ok:
            continue
        nk = _nk(k)
        if nk in aliases:
            rank = (0, len(path))
        elif any(a in nk for a in aliases):
            rank = (1, len(path))
        else:
            continue
        if rank < best_rank:
            best, best_rank = v, rank
    return best


def _all_lists_of_dicts(root):
    for path, k, v in _walk(root):
        if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            yield k, v


def _looks_like_combo(s):
    return isinstance(s, str) and re.search(r"[\d?]+-[\d?]+", s) is not None


def _combo_from_row(d):
    """Extract a combo string from a row by name, by parts, or by ratchet-pattern scan."""
    v = _row_get(d, COMBO_NAME, fuzzy=False)
    if isinstance(v, str) and v.strip():
        return _clean_combo(v)
    blade = _str_field(d, BLADE, fuzzy=False)
    ratchet = _str_field(d, RATCHET, fuzzy=False)
    bit = _str_field(d, BIT, fuzzy=False)
    if blade and ratchet:
        return _clean_combo(" ".join(x for x in (blade, ratchet, bit) if x))
    for _, vv in d.items():
        if _looks_like_combo(vv):
            return _clean_combo(vv)
    # last resort: a fuzzy combo/name key
    v = _row_get(d, COMBO_NAME, fuzzy=True)
    return _clean_combo(v) if isinstance(v, str) and v.strip() else None


# ---------------- section builders ----------------
def _you(name, player):
    if not name:
        return "YOU"
    if _nk(name) in {"you", "self", "me"} or (player and _nk(name) == _nk(player)):
        return "YOU"
    return name


def _combos(root, player):
    node = _find_node(root, COMBOS, "list")
    if not node:
        # shape fallback: rows with a combo string + a win% or battles, no opponent combo
        node = []
        for _, lst in _all_lists_of_dicts(root):
            hits = [r for r in lst if _combo_from_row(r) and not _row_get(r, OPPCOMBO, fuzzy=False)
                    and (_row_get(r, WINPCT) is not None or _row_get(r, BATTLES, fuzzy=False) is not None)
                    and not _row_get(r, PEERROWS, fuzzy=False)]
            if len(hits) > len(node):
                node = hits
    out, seen = [], set()
    for i, r in enumerate(node, 1):
        combo = _combo_from_row(r)
        if not combo or combo in seen:
            continue
        seen.add(combo)
        out.append({"idx": i, "combo": combo,
                    "battles": _int(_row_get(r, BATTLES, fuzzy=False)) or 0,
                    "win_pct": _pct(_row_get(r, WINPCT)) or 0.0,
                    "ppb": _flt(_row_get(r, PPB)),
                    "tier": _tier(_row_get(r, TIER))})
    return out


def _matchups(root):
    node = _find_node(root, MATCHUPS, "list")
    rows = []
    if node:
        for r in node:
            rows.extend(_matchup_row(r, r))
    if not rows:
        # nested form: each combo row carries a list of opponents it faced
        for _, lst in _all_lists_of_dicts(root):
            for r in lst:
                sub = _row_get(r, {"vs", "against", "opponents", "matchups", "faced"}, fuzzy=True)
                if isinstance(sub, list) and sub and all(isinstance(x, dict) for x in sub):
                    for s in sub:
                        rows.extend(_matchup_row(s, r))
    return rows


def _matchup_row(r, parent):
    opp = _str_field(r, OPPCOMBO, fuzzy=False) or (_combo_from_row(r) if _row_get(r, YOURCOMBO, fuzzy=False) is None else None)
    if not opp or not _looks_like_combo(opp):
        # maybe the opp combo is just the row's combo and 'your' lives on the parent
        opp = _str_field(r, OPPCOMBO, fuzzy=True)
    if not opp:
        return []
    your = _str_field(r, YOURCOMBO, fuzzy=False) or _str_field(parent, COMBO_NAME, fuzzy=False) or _combo_from_row(parent) or ""
    wins = _int(_row_get(r, WINS, fuzzy=False))
    losses = _int(_row_get(r, LOSSES, fuzzy=False))
    faced = _int(_row_get(r, FACED, fuzzy=False))
    if faced is None:
        faced = (wins or 0) + (losses or 0)
    return [{"your_combo": _clean_combo(your), "opp_combo": _clean_combo(opp), "faced": faced or 0,
             "wins": wins or 0, "losses": losses or 0,
             "win_pct": _pct(_row_get(r, WINPCT)) or 0.0, "ppb": _flt(_row_get(r, PPB)),
             "net": _int(_row_get(r, NET, fuzzy=False)) or 0}]


def _peers(root, player):
    node = _find_node(root, PEERS, "list")
    out = []
    if not node:
        return out
    for r in node:
        combo = _combo_from_row(r)
        nested = _row_get(r, PEERROWS, fuzzy=True)
        if isinstance(nested, list) and all(isinstance(x, dict) for x in nested):
            for pr in nested:
                out.append(_peer_row(pr, combo, player))
        else:
            out.append(_peer_row(r, combo, player))
    return [x for x in out if x["combo"]]


def _peer_row(r, combo, player):
    return {"combo": combo, "player": _you(_str_field(r, PLAYER, fuzzy=False), player),
            "win_pct": _pct(_row_get(r, WINPCT)) or 0.0, "ppb": _flt(_row_get(r, PPB)),
            "battles": _int(_row_get(r, BATTLES, fuzzy=False)) or 0}


def _norm_result(v, net):
    if isinstance(v, str) and v.strip():
        s = v.strip().upper()
        if s.startswith("W"):
            return "WIN"
        if s.startswith("L"):
            return "LOSS"
    if net is not None:
        return "WIN" if net >= 0 else "LOSS"
    return "WIN"


def _matches(root):
    node = _find_node(root, MATCHES, "list")
    out = []
    if not node:
        return out
    for r in node:
        opp = _str_field(r, OPPONENT, fuzzy=False)
        oc_node = _row_get(r, OPPCOMBOS, fuzzy=True)
        opp_combos = []
        if isinstance(oc_node, list):
            for oc in oc_node:
                if not isinstance(oc, dict):
                    continue
                combo = _combo_from_row(oc)
                if not combo:
                    continue
                wl = _str_field(oc, WL_KEYS, fuzzy=False)
                w = _int(_row_get(oc, WINS, fuzzy=False))
                l = _int(_row_get(oc, LOSSES, fuzzy=False))
                if not (isinstance(wl, str) and re.search(r"\d+\s*[-–]\s*\d+", wl)):
                    wl = f"{w or 0}-{l or 0}"
                opp_combos.append({"combo": combo, "wl": wl, "match_ppb": _flt(_row_get(oc, PPB))})
        if not opp and not opp_combos:
            continue
        net = _int(_row_get(r, NET, fuzzy=False))
        out.append({"result": _norm_result(_row_get(r, RESULT, fuzzy=False), net),
                    "opponent": opp or "?", "sets": _str_field(r, SETS, fuzzy=False) or "",
                    "battles": _int(_row_get(r, BATTLES, fuzzy=False)) or 0,
                    "net": net or 0, "opp_combos": opp_combos})
    return out


def _finishes(root):
    node = _find_node(root, FINISHES, "dict")
    win, loss = {}, {}

    def add(bucket, name, row):
        cnt = _int(_row_get(row, {"count", "n", "num", "total"}, fuzzy=False)) if isinstance(row, dict) else _int(row)
        pts = _int(_row_get(row, {"points", "pts", "totalpoints"}, fuzzy=False)) if isinstance(row, dict) else None
        pct = _pct(_row_get(row, {"pct", "percent", "share"})) if isinstance(row, dict) else None
        bucket[name] = {"count": cnt or 0, "total_pts": pts or 0, "pct": pct or 0.0}

    def is_loss(name):
        n = _nk(name)
        return n.startswith("opp") or "self" in n or "ko" in n or "against" in n or "allowed" in n

    if isinstance(node, dict):
        winnode = _row_get(node, {"win", "wins", "scored", "winning"}, fuzzy=True)
        lossnode = _row_get(node, {"loss", "losses", "allowed", "against", "losing"}, fuzzy=True)
        for label, sub, bucket in (("win", winnode, win), ("loss", lossnode, loss)):
            if isinstance(sub, dict):
                for k, v in sub.items():
                    add(bucket, _title(k) if label == "win" else _loss_name(k), v)
            elif isinstance(sub, list):
                for r in sub:
                    nm = _str_field(r, {"type", "finish", "name", "kind"}, fuzzy=True) or "?"
                    add(bucket, _title(nm) if label == "win" else _loss_name(nm), r)
    # also try a flat list of finish rows tagged with a win/loss category
    if not win and not loss:
        lst = _find_node(root, FINISHES, "list")
        if isinstance(lst, list):
            for r in lst:
                nm = _str_field(r, {"type", "finish", "name", "kind"}, fuzzy=True)
                if not nm:
                    continue
                (loss if is_loss(nm) else win)[_loss_name(nm) if is_loss(nm) else _title(nm)] = {
                    "count": _int(_row_get(r, {"count", "n", "num"}, fuzzy=False)) or 0,
                    "total_pts": _int(_row_get(r, {"points", "pts"}, fuzzy=False)) or 0,
                    "pct": _pct(_row_get(r, {"pct", "percent"})) or 0.0}
    return {"win": win, "loss": loss}


def _loss_name(k):
    n = _nk(k)
    if "self" in n or ("own" in n and "ko" in n):
        return "Own (self-KO)"
    base = re.sub(r"(?i)^opp(onent)?", "", str(k)).strip()
    for t in FINISH_TYPES:
        if t.lower() in n:
            return "Opp " + t
    return "Opp " + _title(base or k)


def _style(root):
    node = _find_node(root, STYLE, "dict")
    out = {}
    if isinstance(node, dict):
        for k, v in node.items():
            n = _int(v)
            if n is not None:
                out[_title(k)] = n
    if not out:
        lst = _find_node(root, STYLE, "list")
        if isinstance(lst, list):
            for r in lst:
                nm = _str_field(r, STYLE_AXES, fuzzy=True)
                val = _int(_row_get(r, STYLE_VALS, fuzzy=True))
                if nm and val is not None:
                    out[_title(nm)] = val
    return out


def _get_nk(d, nk):
    for k, v in d.items():
        if _nk(k) == nk:
            return v
    return None


def _side_stat(v):
    if isinstance(v, dict):
        wp = _pct(_row_get(v, WINPCT))
        return {"win_pct": wp if wp is not None else 0.0,
                "battles": _int(_row_get(v, BATTLES, fuzzy=False)) or 0,
                "ppb": _flt(_row_get(v, PPB))}
    wp = _pct(v)
    return {"win_pct": wp, "battles": 0, "ppb": 0.0} if wp is not None else None


def _dynamics(root):
    d = {"side": {}, "points_dist": {}}
    # dict form: some object holds both a B/b/bside and X/x/xside entry
    for _, k, v in _walk(root):
        if not isinstance(v, dict):
            continue
        keys = {_nk(x) for x in v}
        bkey = next((kk for kk in ("b", "bside", "left") if kk in keys), None)
        xkey = next((kk for kk in ("x", "xside", "right") if kk in keys), None)
        if bkey and xkey:
            b = _side_stat(_get_nk(v, bkey))
            x = _side_stat(_get_nk(v, xkey))
            if b and x:
                d["side"] = {"B": b, "X": x}
                return d
    # list form: [{side:'B', ...}, {side:'X', ...}]
    for _, lst in _all_lists_of_dicts(root):
        sides = {}
        for r in lst:
            sv = _str_field(r, {"side", "launchside", "sidename", "orientation"}, fuzzy=True)
            if sv and sv[0].upper() in ("B", "X"):
                sides[sv[0].upper()] = _side_stat(r)
        if "B" in sides and "X" in sides:
            d["side"] = {"B": sides["B"], "X": sides["X"]}
            return d
    return d


def _totals(root, player):
    node = _find_node(root, TOTALS, "dict") or root
    t = {}
    w = _int(_find_scalar(node, WINS, fuzzy=False))
    l = _int(_find_scalar(node, LOSSES, fuzzy=False))
    if w is not None:
        t["wins"] = w
    if l is not None:
        t["losses"] = l
    wp = _pct(_find_scalar(node, WINPCT))
    if wp is not None:
        t["win_pct"] = wp
    ppb = _find_scalar(node, PPB)
    if ppb is not None:
        t["ppb"] = _flt(ppb)
    place = _find_scalar(node, PLACEMENT, fuzzy=False)
    if place is not None:
        t["placement"] = str(place).strip()
    tb = _int(_find_scalar(node, BATTLES, fuzzy=False))
    if tb is not None:
        t["total_battles"] = tb
    return t


# ---------------- entry point ----------------
def _unwrap(data):
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        return data[0]
    return data


def parse(path):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    root = _unwrap(data)

    player = _find_scalar(root, PLAYER, fuzzy=False) or _find_scalar(root, PLAYER)
    player = str(player).strip() if player is not None else None

    rep = {
        "source": path,
        "player": player,
        "event": (lambda v: str(v).strip() if v is not None else None)(_find_scalar(root, EVENT)),
        "date": (lambda v: str(v).strip() if v is not None else None)(_find_scalar(root, DATE)),
        "totals": _totals(root, player),
        "combos": _combos(root, player),
        "finishes": _finishes(root),
        "matchups": _matchups(root),
        "peers": _peers(root, player),
        "style": _style(root),
        "archetype": (lambda v: str(v).strip() if v is not None else None)(_find_scalar(root, ARCHETYPE, fuzzy=False)),
        "matches": _matches(root),
        "dynamics": _dynamics(root),
    }
    rep["tiers"] = {c["combo"]: c["tier"] for c in rep["combos"] if c.get("tier")}
    for c in rep["combos"]:
        if c.get("tier") is None:
            c["tier"] = rep["tiers"].get(c["combo"])
    return rep
