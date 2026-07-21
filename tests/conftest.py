"""Shared pytest fixtures: a tiny synthetic league built in-memory.

No dependency on the private Google Sheet — we construct a League by hand so the
tests are deterministic and safe to commit.
"""
import pytest

from ncbl.config import load_config
from ncbl.loader import League


def _add(league, ref, tournament, cap, player, placement, gs, points):
    league._add_row(ref, tournament, cap, player, placement, gs, points)


@pytest.fixture
def cfg():
    c = load_config()
    # keep the Monte-Carlo cheap + deterministic for tests
    c["monte_carlo"]["trials"] = 400
    c["monte_carlo"]["seed"] = 1
    c["schedule"]["known_events"] = []
    c["schedule"]["remaining_events"] = 4
    return c


@pytest.fixture
def league(cfg):
    """A 6-player league with a deliberate score tie (10.0) between 'cee' and 'dee'."""
    lg = League(cfg)
    rows = [
        # ref, tournament, cap, player, placement, gs, points
        (1, "T1", 32, "Aaron",  "1st",     5, 3.0),
        (2, "T1", 32, "Bea",    "2nd",     4, 2.5),
        (3, "T1", 32, "Cee",    "3rd",     4, 2.0),
        (4, "T1", 32, "Dee",    "4th",     4, 2.0),   # ties Cee
        (5, "T1", 32, "Eve",    "5th-8th", 3, 1.0),
        (6, "T2", 32, "Aaron",  "1st",     5, 3.0),
        (7, "T2", 32, "Bea",    "2nd",     4, 2.5),
        (8, "T2", 32, "Cee",    "3rd",     4, 2.0),
        (9, "T2", 32, "Dee",    "4th",     4, 2.0),   # ties Cee again
        (10, "T2", 32, "espiiii", "1st",   5, 3.0),   # target player, joins late
        (11, "T2", 32, "Eve",   "5th-8th", 3, 1.0),
    ]
    for r in rows:
        _add(lg, *r)
    lg._finalize()
    return lg
