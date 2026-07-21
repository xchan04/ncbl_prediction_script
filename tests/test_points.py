"""Scoring formula: placement points (by cap tier) + GS wins x 0.33, best-6-of-first-10."""
from ncbl import points as P


def test_cap_tier_boundaries(cfg):
    tiers = cfg["cap_tiers"]
    assert P.cap_tier(16, tiers) == "8-16"
    assert P.cap_tier(17, tiers) == "17-24"
    assert P.cap_tier(32, tiers) == "25-32"
    assert P.cap_tier(64, tiers) == "49-64"
    assert P.cap_tier(128, tiers) == "65-128"
    assert P.cap_tier(5, tiers) == "8-16"      # clamp low
    assert P.cap_tier(500, tiers) == "65-128"  # clamp high


def test_parse_cap():
    assert P.parse_cap("6-48 Player Cap") == 48
    assert P.parse_cap("5-32") == 32
    assert P.parse_cap(64) == 64
    assert P.parse_cap(None) is None


def test_placement_bucket():
    assert P.placement_bucket("1st") == "1st"
    assert P.placement_bucket("5th-8th") == "5th-8th"
    assert P.placement_bucket("9th-12th") == "9th-16th"
    assert P.placement_bucket("7") == "5th-8th"
    assert P.placement_bucket("2") == "2nd"


def test_score_event_matches_known_values(cfg):
    # 32-cap win, 5 GS wins  ->  1.67 + 5*0.33 = 3.32  (verified against the sheet)
    assert P.score_event(32, "1st", 5, cfg) == 3.32
    # 64-cap win, 6 GS wins  ->  2.00 + 6*0.33 = 3.98
    assert P.score_event(64, "1st", 6, cfg) == 3.98
    # 32-cap 4th, 4 GS wins  ->  0.99 + 4*0.33 = 2.31
    assert P.score_event(32, "4th", 4, cfg) == 2.31


def test_best_of_and_season_score(cfg):
    # season_score = best 6 of first 10
    events = [3.0, 2.5, 2.0, 2.0, 1.0, 0.99, 0.66, 0.5]  # 8 events
    # best 6: 3.0+2.5+2.0+2.0+1.0+0.99 = 11.49
    assert round(P.season_score(events, cfg), 2) == 11.49


def test_only_first_ten_count(cfg):
    # an 11th event that is huge must be ignored (only first 10 count)
    events = [1.0] * 10 + [99.0]
    assert P.season_score(events, cfg) == 6.0  # best 6 of the first 10 ones (all 1.0)
