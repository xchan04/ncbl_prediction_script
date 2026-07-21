"""Points / scoring engine for the NCBL prediction pipeline.

Formula (verified against the official sheet):
    event_points = placement_points[cap_tier][placement_bucket] + gs_wins * gs_win_points
    season_score = sum of the best N of a player's first M events
"""
from __future__ import annotations
import re


def cap_tier(num_players, cap_tiers):
    """Map a field size (max cap) to its tier key, e.g. 48 -> '33-48'."""
    for lo, hi, key in cap_tiers:
        if lo <= num_players <= hi:
            return key
    # clamp to nearest edge tier
    if num_players < cap_tiers[0][0]:
        return cap_tiers[0][2]
    return cap_tiers[-1][2]


def parse_cap(cap_string):
    """'6-48 Player Cap' -> 48 ; '5-32' -> 32 ; 64 -> 64."""
    if cap_string is None:
        return None
    if isinstance(cap_string, (int, float)):
        return int(cap_string)
    nums = [int(x) for x in re.findall(r"\d+", str(cap_string))]
    return max(nums) if nums else None


def placement_bucket(placement):
    """Normalize any placement label to a bucket key used in the points table."""
    if placement is None:
        return None
    p = str(placement).strip().lower()
    if p in ("1st", "1", "first"):
        return "1st"
    if p in ("2nd", "2", "second"):
        return "2nd"
    if p in ("3rd", "3", "third"):
        return "3rd"
    if p in ("4th", "4", "fourth"):
        return "4th"
    m = re.match(r"(\d+)", p)
    n = int(m.group(1)) if m else None
    if n is None:
        return "5th-8th"
    if n <= 4:
        return ["", "1st", "2nd", "3rd", "4th"][n]
    if n <= 8:
        return "5th-8th"
    return "9th-16th"


def score_event(num_players, placement, gs_wins, cfg):
    """Compute the points a single tournament result is worth."""
    tier = cap_tier(num_players, cfg["cap_tiers"])
    bucket = placement_bucket(placement)
    table = cfg["placement_points"].get(tier, {})
    pts = table.get(bucket, 0.0)
    return round(pts + (gs_wins or 0) * cfg["gs_win_points"], 4)


def best_of(values, n):
    """Sum of the top-n values."""
    return sum(sorted(values, reverse=True)[:n])


def season_score(event_points, cfg):
    """Best `best_of` of the first `of_first` events (chronological order assumed)."""
    first = event_points[: cfg["of_first"]]
    return round(best_of(first, cfg["best_of"]), 4)
