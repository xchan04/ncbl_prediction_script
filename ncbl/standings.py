"""Standings + rank-over-time, computed from a loaded League."""
from __future__ import annotations


def standings(league, upto=None):
    """Return [(lc_name, score)] sorted best-first, computed through tournament `upto`."""
    upto = upto if upto is not None else len(league.tournaments)
    rows = [(p, league.points_through(p, upto)) for p in league.by_player]
    rows = [(p, s) for p, s in rows if s > 0]
    # deterministic: score desc, then name asc so ties resolve identically everywhere
    rows.sort(key=lambda z: (-z[1], z[0]))
    return rows


def ranks(league, upto=None):
    """lc_name -> rank (1=best) at tournament `upto`."""
    return {p: i for i, (p, _) in enumerate(standings(league, upto), 1)}


def rank_of(league, player, upto=None):
    return ranks(league, upto).get(player)


def snapshots(league, t_from=1, t_to=None):
    """List of {lc_name: score} dicts, one per tournament index in [t_from, t_to]."""
    t_to = t_to if t_to is not None else len(league.tournaments)
    out = []
    for t in range(t_from, t_to + 1):
        d = {p: league.points_through(p, t) for p in league.by_player}
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
