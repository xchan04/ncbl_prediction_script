"""Default configuration + loader. Override any field via a JSON config file.

Everything the pipeline needs to reproduce the analysis lives here:
the points table, the best-N-of-M rule, Monte-Carlo params, the Stage-2
invite lists, the region map, and the video theme.
"""
from __future__ import annotations
import json
import copy

DEFAULTS = {
    # --- scoring rule ---
    "best_of": 6,
    "of_first": 10,
    "gs_win_points": 0.33,
    # Only rank players listed in the rankings tab (registered players).
    # If True and a rankings tab is loaded, unregistered/guest competitors are
    # excluded from standings, ranks, predictions, and videos.
    "ranked_only": True,
    # (lo, hi, key) inclusive field-size tiers
    "cap_tiers": [
        [8, 16, "8-16"], [17, 24, "17-24"], [25, 32, "25-32"],
        [33, 48, "33-48"], [49, 64, "49-64"], [65, 128, "65-128"],
    ],
    "placement_points": {
        "8-16":   {"1st": 1.01,  "2nd": 0.67,  "3rd": 0.66,  "4th": 0.33,  "5th-8th": 0.0,   "9th-16th": 0.0},
        "17-24":  {"1st": 1.505, "2nd": 1.165, "3rd": 1.155, "4th": 0.835, "5th-8th": 0.495, "9th-16th": 0.0},
        "25-32":  {"1st": 1.67,  "2nd": 1.33,  "3rd": 1.32,  "4th": 0.99,  "5th-8th": 0.66,  "9th-16th": 0.0},
        "33-48":  {"1st": 1.835, "2nd": 1.495, "3rd": 1.485, "4th": 1.155, "5th-8th": 0.835, "9th-16th": 0.66},
        "49-64":  {"1st": 2.0,   "2nd": 1.66,  "3rd": 1.65,  "4th": 1.32,  "5th-8th": 0.99,  "9th-16th": 0.66},
        "65-128": {"1st": 2.33,  "2nd": 1.99,  "3rd": 1.98,  "4th": 1.65,  "5th-8th": 1.32,  "9th-16th": 0.99},
    },

    # --- where the data lives (column layout of the Data Entry tab, 1-indexed) ---
    "data_entry_sheet": "2026 Season 6 Data Entry",
    "rankings_sheet": "2026 Season 6 Solo Rankings",
    "columns": {"ref": 2, "tournament": 3, "date": 4, "cap": 5,
                "player": 6, "placement": 7, "gs_wins": 8, "points": 10},
    "data_entry_header_rows": 3,   # rows to skip before results start
    "rankings_header_rows": 2,
    "rankings_cols": {"rank": 2, "player": 3, "points": 4},

    # --- Monte Carlo ---
    "monte_carlo": {
        "trials": 6000,
        "breakout_prob": 0.15,      # chance a player draws an above-history "upset" result
        "inactive_gap": 6,          # a player not seen in the last N events is treated as done
        "seed": 7,
    },

    # --- future schedule handling ---
    # If you KNOW the schedule, list events: [{"name": "...", "cap": 32}, ...]
    # If unknown, the pipeline gap-fills each rival's future count from their
    # historical attendance rate over `remaining_events` weekends.
    "schedule": {
        "known_events": [],           # e.g. [{"name":"July 19 Cup","cap":32},{"name":"Aug 1 Major","cap":64}]
        "remaining_events": 12,       # est. league events left (used only when schedule unknown)
        "default_cap": 32,            # cap assumed for the target player's future events
    },

    # --- Stage-2 / invitational open-spot analysis (optional) ---
    "invited": [],      # confirmed invitees (any casing)
    "wildcards": [],    # named wildcard picks
    "open_spots": 0,    # number of open top-scorer spots (0 disables this analysis)

    # --- region map (for the setting video) ---
    "regions": {
        # name: [lat, lon, reachable(bool)]
        "Marin": [38.05, -122.75, False], "Sonoma": [38.53, -122.94, False],
        "Napa": [38.51, -122.33, False], "Solano": [38.31, -121.94, False],
        "Yolo": [38.73, -121.90, False], "Sacramento": [38.55, -121.30, False],
        "Contra Costa": [37.92, -121.95, False], "San Joaquin": [37.93, -121.27, False],
        "San Francisco": [37.77, -122.45, True], "San Mateo": [37.44, -122.33, True],
        "Alameda": [37.72, -121.98, True], "Santa Clara": [37.30, -121.85, True],
    },
    "home": {"name": "HOLLISTER", "lat": 36.85, "lon": -121.40},
    "reach_limit_lat": 37.87,   # "Berkeley line"

    # --- theme ---
    "theme": {
        "bg": "#000000", "fg": "#e6edf3", "player": "#ff8c1a", "player_edge": "#ffb454",
        "rival": "#2f7d3f", "rival_name": "#57e26b", "muted": "#6b7280",
        "cutoff": "#ff5555", "amber": "#ffd24a", "dim": "#1c1c1c",
    },
    "target_rank": 10,   # the rank you're chasing
}


def load_config(path=None):
    cfg = copy.deepcopy(DEFAULTS)
    if path:
        with open(path) as fh:
            user = json.load(fh)
        _deep_update(cfg, user)
    return cfg


def _deep_update(base, override):
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
    return base
