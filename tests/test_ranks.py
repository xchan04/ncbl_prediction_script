"""Rank consistency: standings, ranks(), rank_of(), predict, and threats must all
agree on where a player sits — the off-by-one / tie regression must never return.
"""
from ncbl import standings as S
from ncbl import simulate as SIM


def test_ranks_match_standings_positions(league):
    rows = S.standings(league)
    rk = S.ranks(league)
    for pos, (p, _) in enumerate(rows, 1):
        assert rk[p] == pos, f"{p}: ranks()={rk[p]} but standings position={pos}"


def test_rank_of_matches_enumeration(league):
    rows = S.standings(league)
    for pos, (p, _) in enumerate(rows, 1):
        assert S.rank_of(league, p) == pos


def test_rank_is_one_indexed_from_one(league):
    rows = S.standings(league)
    assert S.rank_of(league, rows[0][0]) == 1  # leader is #1, not #2


def test_predict_current_rank_matches_standings(league, cfg):
    rep = SIM.predict_report(league, cfg, "espiiii", target_rank=3, remaining=2)
    assert rep["current_rank"] == S.rank_of(league, "espiiii")


def test_threats_ranks_match_standings(league, cfg):
    """Any rank shown by threats() equals that player's true standings position."""
    n = len(league.tournaments)
    final = S.ranks(league, n)
    t = SIM.threats(league, cfg, "espiiii", window=6)
    for p, _a, b, _s in t["overtook"]:
        assert b == final[p], f"threats final rank {b} != standings rank {final[p]} for {p}"


def test_deterministic_tiebreak_is_stable(league):
    """Tied scores (Cee & Dee at 10.0) resolve the same way on every call."""
    a = [p for p, _ in S.standings(league)]
    b = [p for p, _ in S.standings(league)]
    assert a == b
    # tie resolves by name asc: 'cee' before 'dee'
    assert a.index("cee") < a.index("dee")


def test_tie_has_no_gap_or_overlap(league):
    """Two tied players occupy consecutive, distinct ranks."""
    rk = S.ranks(league)
    assert abs(rk["cee"] - rk["dee"]) == 1


def test_ranked_only_excludes_unregistered(cfg):
    """With ranked_only, only players in the rankings tab are ranked."""
    from ncbl.loader import League
    lg = League(cfg)
    for ref, p, pts in [(1, "Reg1", 3.0), (2, "Reg2", 2.0), (3, "GuestX", 5.0)]:
        lg._add_row(ref, "T1", 32, p, "1st", 0, pts)
    # only Reg1/Reg2 are on the rankings tab
    lg._add_rank(1, "Reg1", 3.0)
    lg._add_rank(2, "Reg2", 2.0)
    lg._finalize()
    names = {lg.name(p) for p, _ in S.standings(lg)}
    assert names == {"Reg1", "Reg2"}          # GuestX excluded despite top score
    assert S.rank_of(lg, "reg1") == 1
