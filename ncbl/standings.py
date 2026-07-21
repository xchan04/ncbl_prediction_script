"""Standings + rank-over-time, computed from a loaded League."""
from __future__ import annotations


def _universe(league, extra=None):
    """Players eligible to be ranked (the roster), optionally including `extra`."""
    base = league.roster if league.roster else set(league.by_player.keys())
    if extra:
        base = base | {extra}
    return base


def standings(league, upto=None, include=None):
    """Return [(lc_name, score)] sorted best-first, computed through tournament `upto`.

    Only players in the roster (registered, if ranked_only) are ranked; `include`
    forces a specific player in even if unregistered (used for the queried player).
    """
    upto = upto if upto is not None else len(league.tournaments)
    universe = _universe(league, include)
    rows = [(p, league.points_through(p, upto)) for p in universe]
    rows = [(p, s) for p, s in rows if s > 0]
    # deterministic: score desc, then name asc so ties resolve identically everywhere
    rows.sort(key=lambda z: (-z[1], z[0]))
    return rows


def ranks(league, upto=None, include=None):
    """lc_name -> rank (1=best) at tournament `upto`."""
    return {p: i for i, (p, _) in enumerate(standings(league, upto, include), 1)}


def rank_of(league, player, upto=None):
    return ranks(league, upto, include=player).get(player)


def snapshots(league, t_from=1, t_to=None, include=None):
    """List of {lc_name: score} dicts, one per tournament index in [t_from, t_to]."""
    t_to = t_to if t_to is not None else len(league.tournaments)
    universe = _universe(league, include)
    out = []
    for t in range(t_from, t_to + 1):
        d = {p: league.points_through(p, t) for p in universe}
        out.append({p: s for p, s in d.items() if s > 0})
    return out


def published_standings(league):
    """Use the published rankings tab as the authoritative order (handles ties/skips)."""
    rows = sorted(league.published_rank.items(), key=lambda z: z[1])
    return rows  # [(lc_name, published_rank)]


def cutoff(league, target_rank, upto=None):
    """Score of the player currently sitting at `target_rank`."""
    s = standings(league, upto)
    return s[target_rank - 1][1] if len(s) >= target_rank else (s[-1][1] if s else 0.0)
