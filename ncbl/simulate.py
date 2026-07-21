"""Monte-Carlo engine + prediction reports.

Replays the rest of the season many times. Each rival's future results are
bootstrapped from their own history (plus a small 'breakout' chance for upsets).
The target player's future results are set by a chosen strategy so you can read
off 'what do I need to do'.
"""
from __future__ import annotations
import random

from . import points as P
from . import standings as S


# ---- event-value menu for the target player's future events (per cap) ----
def event_menu(cfg, cap):
    """Return {outcome: points} for a given field size, using the points table."""
    return {
        "win":  P.score_event(cap, "1st", _gs_for(cap, "win"), cfg),
        "2nd":  P.score_event(cap, "2nd", _gs_for(cap, "2nd"), cfg),
        "top4": P.score_event(cap, "4th", _gs_for(cap, "top4"), cfg),
        "top8": P.score_event(cap, "5th-8th", _gs_for(cap, "top8"), cfg),
        "miss": P.score_event(cap, "9th-16th", _gs_for(cap, "miss"), cfg),
    }


def _gs_for(cap, outcome):
    # rough Swiss-win counts by how deep you went, scaled to field size
    base = {"win": 5, "2nd": 4, "top4": 4, "top8": 3, "miss": 2}[outcome]
    if cap >= 49:
        base += 1
    if cap >= 65:
        base += 1
    return base


def _future_counts(league, cfg, exclude=None):
    """How many more events each rival plays.

    If a known schedule is given, everyone eligible can attend up to len(schedule)
    (capped by their remaining slots). Otherwise gap-fill from attendance rate.
    """
    mc = cfg["monte_carlo"]
    sched = cfg["schedule"]
    n_total = len(league.tournaments)
    known = sched.get("known_events") or []
    out = {}
    for p in league.by_player:
        if p == exclude:
            continue
        slots = max(0, cfg["of_first"] - league.n_events(p))
        inactive = league.last_event[p] < n_total - mc["inactive_gap"]
        if inactive:
            out[p] = 0
        elif known:
            out[p] = min(slots, len(known))
        else:
            rate = league.n_events(p) / max(1, n_total)
            out[p] = min(slots, max(0, round(rate * sched["remaining_events"])))
    return out


def _strong_pool(league):
    allpts = [pt for evs in league.by_player.values() for _, pt in evs]
    allpts.sort(reverse=True)
    return allpts[: max(1, len(allpts) // 4)]


class Simulator:
    def __init__(self, league, cfg):
        self.league = league
        self.cfg = cfg
        self.mc = cfg["monte_carlo"]
        self.strong = _strong_pool(league)
        self.base = {p: [pt for _, pt in evs] for p, evs in league.by_player.items()}

    def _draw(self, player, rng):
        if rng.random() < self.mc["breakout_prob"]:
            return rng.choice(self.strong)
        return rng.choice(self.base[player])

    def run(self, player, player_events, target_rank, open_spots=0, invited=None):
        """Return dict of probabilities for a given target-player event list."""
        rng = random.Random(self.mc["seed"])
        fut = _future_counts(self.league, self.cfg, exclude=player)
        others = [p for p in self.base if p != player]
        invited = set(x.lower().replace(" ", "") for x in (invited or []))
        top_hits = 0
        stage_hits = 0
        ranks = []
        my_score = P.season_score(self.base[player] + list(player_events), self.cfg)
        for _ in range(self.mc["trials"]):
            better = 0
            better_noninv = 0
            for p in others:
                evs = self.base[p] + [self._draw(p, rng) for _ in range(fut[p])]
                sc = P.season_score(evs, self.cfg)
                if sc > my_score:
                    better += 1
                    if p.replace(" ", "") not in invited:
                        better_noninv += 1
            r = 1 + better
            ranks.append(r)
            if r <= target_rank:
                top_hits += 1
            if open_spots and (1 + better_noninv) <= open_spots:
                stage_hits += 1
        ranks.sort()
        return {
            "score": round(my_score, 3),
            "p_top": top_hits / self.mc["trials"],
            "p_stage": stage_hits / self.mc["trials"] if open_spots else None,
            "median_rank": ranks[len(ranks) // 2],
        }


def strategy_events(cfg, wins=0, top4=0, top8=0, cap=None):
    """Build an event list from a mix of outcomes for the target player's remaining slots."""
    cap = cap or cfg["schedule"]["default_cap"]
    m = event_menu(cfg, cap)
    return [m["win"]] * wins + [m["top4"]] * top4 + [m["top8"]] * top8


def predict_report(league, cfg, player, target_rank=None, remaining=None):
    """The 'what do I need to do' table across common strategies."""
    target_rank = target_rank or cfg["target_rank"]
    remaining = remaining if remaining is not None else max(0, cfg["of_first"] - league.n_events(player))
    sim = Simulator(league, cfg)
    invited = _invited_set(cfg)
    open_spots = cfg.get("open_spots", 0)
    lines = []
    plans = _plans_for(remaining)
    for label, (w, t4, t8) in plans:
        evs = strategy_events(cfg, wins=w, top4=t4, top8=t8)
        res = sim.run(player, evs, target_rank, open_spots, invited)
        lines.append((label, res))
    return {
        "player": league.name(player),
        "current_rank": S.rank_of(league, player),
        "current_score": round(league.score(player), 3),
        "n_events": league.n_events(player),
        "slots_left": remaining,
        "target_rank": target_rank,
        "cutoff": round(S.cutoff(league, target_rank), 3),
        "lines": lines,
    }


def _plans_for(remaining):
    """Common strategies scaled to how many slots remain."""
    plans = [("do nothing", (0, 0, 0))]
    for w in range(1, remaining + 1):
        rest = remaining - w
        plans.append((f"{w} win(s)" + (f" + {rest} top-8" if rest else ""), (w, 0, rest)))
    if remaining >= 2:
        plans.insert(1, (f"{remaining} top-4s", (0, remaining, 0)))
    return plans


def _invited_set(cfg):
    inv = set(x.lower().replace(" ", "") for x in cfg.get("invited", []))
    inv |= set(x.lower().replace(" ", "") for x in cfg.get("wildcards", []))
    return inv


def threats(league, cfg, player, window=6, top=8):
    """Who overtook the player recently, and who can still catch them."""
    n = len(league.tournaments)
    t0 = max(1, n - window)
    r0 = S.ranks(league, t0)
    r1 = S.ranks(league, n)
    me0, me1 = r0.get(player, 999), r1.get(player, 999)
    overtook, live = [], []
    for p in league.by_player:
        if p == player:
            continue
        a, b = r0.get(p, 999), r1.get(p, 999)
        if a > me0 and b < me1:
            overtook.append((p, a, b, league.score(p)))
        elif b > me1:
            slots = cfg["of_first"] - league.n_events(p)
            if slots >= 4 and league.score(p) >= league.score(player) - 2:
                live.append((p, b, slots, league.score(p)))
    overtook.sort(key=lambda z: z[2])
    live.sort(key=lambda z: -z[3])
    return {"overtook": overtook[:top], "live": live[:top]}
