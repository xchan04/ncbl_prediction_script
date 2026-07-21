"""Video/chart generators. Every function is parameterized by the target player
and reads its look from cfg['theme']. Requires matplotlib + ffmpeg.

Generators:
    follow()        - follow-cam bar race that tracks the player (climb OR drop)
    overview()      - whole-field rank-over-time bump chart
    spikers()       - who overtook the player / who still can
    montecarlo()    - mosaic of N mini-simulations
    bestworst()     - best/worst-case outcome bars
    regions_map()   - the league region map (from cfg['regions'])
    vertical_hook() - 9:16 hook card for Shorts
"""
from __future__ import annotations
import random

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import matplotlib.cm as cm

from . import standings as S
from . import points as P


def _writer(fps):
    return animation.FFMpegWriter(fps=fps, bitrate=5200)


def _short(name, n=24):
    return "Online event" if str(name).startswith("http") else str(name)[:n]


# ------------------------------------------------------------------ follow-cam
def follow(league, cfg, player, out, t_from=None, t_to=None, fps=60,
           title=None, rows=15, published_end=False):
    """Bar-chart race that keeps `player` in frame the whole season."""
    th = cfg["theme"]
    t_to = t_to or len(league.tournaments)
    # default: start when the player first appears (before that they aren't ranked)
    if t_from is None:
        firsts = [i for i, _ in league.by_player.get(player, [])]
        t_from = min(firsts) if firsts else 1
    snaps = S.snapshots(league, t_from, t_to)
    tnames = [ _short(league.tournaments[t-1]) for t in range(t_from, t_to+1) ]
    rankmaps = [{p: i+1 for i, (p, _) in enumerate(sorted(d.items(), key=lambda z:-z[1]), 1)} for d in snaps]
    if published_end and league.published_rank:
        snaps[-1] = {p: league.published_points.get(p, 0) for p in league.by_player if league.published_points.get(p, 0) > 0}
        rankmaps[-1] = {p: league.published_rank[p] for p in league.by_player
                        if p in league.published_rank and league.published_points.get(p, 0) > 0}
    BIG = 10**9
    HOLD, TWEEN, END = 22, 40, 130
    frames = [("h", 0)] * HOLD
    for k in range(len(snaps)-1):
        frames += [(k, a/TWEEN) for a in range(1, TWEEN+1)] + [(k+1, 0.0)] * HOLD
    frames += [("end", 0.0)] * END
    maxx = max((max(d.values()) for d in snaps if d), default=10) * 1.12
    fig, ax = plt.subplots(figsize=(13, 8.4)); fig.subplots_adjust(left=0.25, right=0.93, top=0.83, bottom=0.09)

    def state(fr):
        tag, frac = fr
        k = 0 if tag == "h" else (len(snaps)-1 if tag == "end" else tag)
        if tag in ("h", "end"): frac = 0.0
        d0, d1 = snaps[k], snaps[min(k+1, len(snaps)-1)]
        r0, r1 = rankmaps[k], rankmaps[min(k+1, len(snaps)-1)]
        vals, rnk = {}, {}
        for p in set(d0) | set(d1):
            v0, v1 = d0.get(p, 0), d1.get(p, 0); vals[p] = v0 + (v1-v0)*frac
            a, b = r0.get(p, BIG), r1.get(p, BIG); rnk[p] = a + (b-a)*frac
        return vals, rnk, k

    def draw(fr):
        ax.clear(); vals, rnk, k = state(fr); re = rnk.get(player, rows + 6)
        if re <= rows: top, bottom = 1.0, float(rows)
        else:
            top, bottom = re-7, re+7
            w = max(0.0, min(1.0, (rows+1-re)/2.0)); top = (1-w)*top + w*1.0; bottom = (1-w)*bottom + w*rows
        ax.set_ylim(-(bottom+0.6), -(top-0.6)); ax.set_xlim(0, maxx)
        for p, v in vals.items():
            r = rnk[p]
            if r < top-0.45 or r > bottom+0.45: continue
            me = (p == player); y = -r
            ax.barh(y, v, height=0.8, color=th["player"] if me else th["rival"], zorder=2,
                    edgecolor=th["player_edge"] if me else "none", lw=3 if me else 0)
            ax.text(-0.15*maxx/16.5, y, league.name(p), va="center", ha="right", clip_on=False,
                    color=th["player"] if me else th["rival_name"], fontsize=12.5 if me else 10.5,
                    fontweight="bold" if me else "normal")
            ax.text(v+0.01*maxx, y, f"{v:.2f}", va="center", ha="left", color=th["player"] if me else th["fg"],
                    fontsize=10 if me else 9.5, fontweight="bold" if me else "normal")
        tr = cfg["target_rank"]
        ax.axhline(-(tr+0.5), color=th["cutoff"], ls="--", lw=1.5, alpha=0.8)
        ax.text(maxx*0.99, -(tr+0.5), f"TOP {tr}", color=th["cutoff"], fontsize=10, va="center", ha="right", fontweight="bold")
        ax.set_yticks([]); ax.set_xlabel("League Points (best %d of first %d)" % (cfg["best_of"], cfg["of_first"]), color=th["fg"])
        fig.suptitle(title or f"THE {league.name(player)} CLIMB", color=th["player"], fontsize=20, fontweight="bold", x=0.25, ha="left", y=0.955)
        cap = f"FINAL — {league.name(player)} #{int(round(re))}" if fr[0] == "end" else _short(league.tournaments[t_from-1+k])
        ax.set_title(cap, color=th["fg"], fontsize=13, loc="left", pad=10)
        fig.patch.set_facecolor(th["bg"]); ax.set_facecolor(th["bg"])
        for s in ax.spines.values(): s.set_color(th["muted"])
        ax.tick_params(colors=th["fg"]); ax.grid(axis="x", alpha=0.1, color=th["muted"])
        fall = max(0.0, re - cfg["target_rank"] - 4); fs = min(15 + fall * 2.0, 34)
        bc = th["player"] if re <= rows else th["cutoff"]
        arrow = " ▼" if re > rows else ""
        ax.text(0.985, 0.96, f"{league.name(player)}: #{int(round(re))}{arrow}", transform=ax.transAxes,
                ha="right", va="top", color="#000000" if re <= rows else "#ffffff", fontsize=fs, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.4", fc=bc, ec="none"))

    animation.FuncAnimation(fig, draw, frames=frames, interval=1000/fps).save(
        out, writer=_writer(fps), savefig_kwargs={"facecolor": th["bg"]})
    plt.close(); return out


# ------------------------------------------------------------------ overview / spikers
def _bumpbase(league, cfg, t_from, t_to):
    snaps = S.snapshots(league, t_from, t_to)
    rk = [{p: i+1 for i, (p, _) in enumerate(sorted(d.items(), key=lambda z:-z[1]), 1)} for d in snaps]
    players = [p for p in league.by_player if any(p in rk[i] for i in range(len(snaps)))]
    series = {p: [rk[i].get(p) for i in range(len(snaps))] for p in players}
    tnames = [_short(league.tournaments[t-1]) for t in range(t_from, t_to+1)]
    nmax = max((max(m.values()) for m in rk if m), default=1)
    return snaps, rk, players, series, tnames, nmax


def _line_upto(series_p, prog, n):
    px, py = [], []
    for i in range(n):
        if i > prog + 1e-9: break
        if series_p[i] is None: continue
        px.append(i); py.append(series_p[i])
    last = max((i for i in range(n) if series_p[i] is not None and i <= prog), default=None)
    if last is not None and last < n-1 and prog > last:
        nxt = next((j for j in range(last+1, n) if series_p[j] is not None), None)
        if nxt is not None:
            f = min(1.0, (prog-last)/(nxt-last)); px.append(last+(nxt-last)*f)
            py.append(series_p[last]+(series_p[nxt]-series_p[last])*f)
    return px, py


def overview(league, cfg, player, out, t_from=None, t_to=None, fps=60):
    th = cfg["theme"]; t_to = t_to or len(league.tournaments); t_from = t_from or 1
    snaps, rk, players, series, tnames, nmax = _bumpbase(league, cfg, t_from, t_to)
    def firstlast(p):
        seq = [series[p][i] for i in range(len(snaps)) if series[p][i] is not None]; return seq[0], seq[-1]
    G, R = cm.get_cmap("Greens"), cm.get_cmap("Reds")
    pcol = {}
    for p in players:
        if p == player: pcol[p] = th["player"]; continue
        fr, lr = firstlast(p); h = (abs(hash(p)) % 1000)/1000.0
        pcol[p] = G(0.45+0.45*h) if lr <= fr else R(0.40+0.45*h)
    HOLD, PER, END = 22, 40, 130
    frames = [("h", 0)]*HOLD
    for k in range(len(snaps)-1): frames += [(k, a/PER) for a in range(1, PER+1)] + [(k+1, 0.0)]*HOLD
    frames += [("end", 0.0)]*END
    fig, ax = plt.subplots(figsize=(13, 8.8)); fig.subplots_adjust(left=0.09, right=0.94, top=0.86, bottom=0.20)
    def prog_of(fr):
        return 0.0 if fr[0] == "h" else (float(len(snaps)-1) if fr[0] == "end" else fr[0]+fr[1])
    def draw(fr):
        ax.clear(); prog = prog_of(fr); cur = min(int(round(prog)), len(snaps)-1)
        for p in players:
            if p == player: continue
            px, py = _line_upto(series[p], prog, len(snaps))
            if len(px) > 1: ax.plot(px, py, color=pcol[p], alpha=0.45, lw=1.1, zorder=2)
        ax.axhspan(0.5, cfg["target_rank"]+0.5, color="white", alpha=0.06)
        ax.text(0.05, cfg["target_rank"]+0.3, f"TOP {cfg['target_rank']}", color="white", fontsize=11, fontweight="bold", va="bottom", alpha=0.8)
        px, py = _line_upto(series[player], prog, len(snaps))
        if px:
            ax.plot(px, py, color=th["player"], lw=4.5, zorder=10)
            ax.scatter([px[-1]], [py[-1]], color=th["player"], edgecolor="white", s=160, zorder=11)
            ax.text(px[-1]+0.15, py[-1], f"{league.name(player)} #{int(round(py[-1]))}", color=th["player"], fontsize=14, fontweight="bold", va="center", zorder=12)
        ax.set_ylim(nmax+2, 0); ax.set_xlim(-0.3, len(snaps)-1+2.2)
        ax.set_xticks(range(len(snaps))); L = ax.set_xticklabels(tnames, rotation=40, ha="right", fontsize=7); L[cur].set_color(th["player"]); L[cur].set_fontweight("bold")
        ax.set_ylabel("Rank among ranked players", color=th["fg"])
        fig.suptitle(f"THE BIG PICTURE — {league.name(player)} vs the field", color=th["player"], fontsize=19, fontweight="bold", x=0.09, ha="left", y=0.95)
        ax.set_title(f"Now playing: {tnames[cur]}", color=th["fg"], fontsize=13, loc="left", pad=8)
        fig.patch.set_facecolor(th["bg"]); ax.set_facecolor(th["bg"])
        for s in ax.spines.values(): s.set_color(th["muted"])
        ax.tick_params(colors=th["fg"]); ax.grid(alpha=0.08, color=th["muted"])
    animation.FuncAnimation(fig, draw, frames=frames, interval=1000/fps).save(out, writer=_writer(fps), savefig_kwargs={"facecolor": th["bg"]})
    plt.close(); return out


def montecarlo(league, cfg, player, out, rols=8, cols=14, fps=40, dur=7.0, target_rank=None):
    """Mosaic of rows*cols mini-simulations, the player's bar in the theme color."""
    th = cfg["theme"]; rng = random.Random(cfg["monte_carlo"]["seed"])
    from .simulate import event_menu
    mine = sorted([pt for _, pt in league.by_player[player]], reverse=True)
    contenders = sorted([league.score(p) for p in league.by_player], reverse=True)[:45]
    m = event_menu(cfg, cfg["schedule"]["default_cap"])
    slots = max(1, cfg["of_first"] - league.n_events(player))
    NT = rols*cols; BARS = 7
    tiles = []
    for i in range(NT):
        ev = [rng.choices([m["win"], m["top4"], m["top8"], m["miss"]], weights=[.25,.30,.25,.20])[0] for _ in range(slots)]
        et = P.season_score(mine + ev, cfg)
        vals = [rng.choice(contenders)+rng.uniform(-1.2,1.2) for _ in range(BARS-1)] + [et]
        isme = [False]*(BARS-1)+[True]
        order = sorted(range(BARS), key=lambda j:-vals[j]); vals=[vals[j] for j in order]; isme=[isme[j] for j in order]
        r, c = divmod(i, cols); tiles.append((r, c, vals, isme, rng.uniform(0,0.45)))
    N = int(fps*dur); G = cm.get_cmap("summer")
    fig, ax = plt.subplots(figsize=(16,9)); fig.subplots_adjust(left=0.01,right=0.99,top=0.90,bottom=0.02)
    def draw(fi):
        ax.clear(); prog = fi/(N-1); ys=[];ws=[];lefts=[];colu=[];hs=[]
        for (r,c,vals,isme,delay) in tiles:
            grow = max(0.0, min(1.0, (prog-delay)/0.45)); tm = max(vals)
            yb = (rols-1-r)
            for j in range(BARS):
                ys.append(yb+0.90-j*0.115); ws.append((vals[j]/tm)*0.90*grow); lefts.append(c+0.05); hs.append(0.085)
                colu.append(th["player"] if isme[j] else G(0.2+0.5*(BARS-1-j)/BARS))
        ax.barh(ys, ws, left=lefts, height=hs, color=colu)
        ax.set_xlim(0,cols); ax.set_ylim(-0.1,rols+0.1); ax.axis("off")
        fig.patch.set_facecolor(th["bg"]); ax.set_facecolor(th["bg"])
        nsim = int(min(1.0, prog/0.9)*cfg["monte_carlo"]["trials"])
        fig.suptitle(f"MONTE CARLO — simulating the rest of the season, {cfg['monte_carlo']['trials']:,} times", color=th["player"], fontsize=24, fontweight="bold", y=0.965)
        ax.text(cols/2, rols+0.02, f"{nsim:,} / {cfg['monte_carlo']['trials']:,} simulated seasons   ({league.name(player)} = the highlighted bar)", color=th["fg"], fontsize=14, ha="center", va="bottom")
    animation.FuncAnimation(fig, draw, frames=N, interval=1000/fps).save(out, writer=_writer(fps), savefig_kwargs={"facecolor": th["bg"]})
    plt.close(); return out


def regions_map(cfg, out):
    """Static region map (PNG) from cfg['regions'] + home + reach limit."""
    th = cfg["theme"]; GREEN="#57e26b"; RED="#ff5d5d"
    fig, ax = plt.subplots(figsize=(12,11)); fig.subplots_adjust(left=0.03,right=0.97,top=0.90,bottom=0.04)
    ax.set_facecolor(th["bg"]); fig.patch.set_facecolor(th["bg"])
    lim = cfg["reach_limit_lat"]
    ax.axhspan(35, lim, color=GREEN, alpha=0.06); ax.axhspan(lim, 40, color=RED, alpha=0.06)
    ax.axhline(lim, color=th["player"], ls="--", lw=1.6, alpha=0.85)
    ax.text(-123.15, lim+0.05, "reach limit", color=th["player"], fontsize=12.5, fontweight="bold", va="bottom", ha="left")
    for name, (la, lo, reach) in cfg["regions"].items():
        ax.scatter([lo], [la], s=340, color=GREEN if reach else RED, edgecolor="white", lw=0.8, zorder=5)
        ax.text(lo, la-0.06, name, color=th["fg"], fontsize=11, ha="center", va="top")
    h = cfg["home"]; ax.scatter([h["lon"]], [h["lat"]], marker="*", s=900, color=th["player"], edgecolor="white", lw=1.2, zorder=7)
    ax.text(h["lon"], h["lat"]-0.07, h["name"], color=th["player"], fontsize=13, fontweight="bold", ha="center", va="top")
    ax.set_xlim(-123.25, -120.85); ax.set_ylim(36.5, 39.0); ax.set_aspect(1/0.8); ax.axis("off")
    fig.suptitle(f"THE LEAGUE MAP — {len(cfg['regions'])} regions", color=th["player"], fontsize=22, fontweight="bold", y=0.965)
    plt.savefig(out, dpi=130, facecolor=th["bg"]); plt.close(); return out


def vertical_hook(cfg, out, top_number, drop_number, fps=30, dur=6.0,
                  kicker="MY RANKING EXPERIMENT", trials=None):
    """9:16 hook card: climb number -> crash to drop number."""
    th = cfg["theme"]; rng = random.Random(1); N = int(fps*dur)
    trials = trials or cfg["monte_carlo"]["trials"]
    fig, ax = plt.subplots(figsize=(9,16), dpi=120); fig.subplots_adjust(left=0,right=1,top=1,bottom=0)
    def eo(t): t = max(0, min(1, t)); return 1-(1-t)**3
    def draw(fi):
        ax.clear(); ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis("off")
        fig.patch.set_facecolor(th["bg"]); ax.set_facecolor(th["bg"]); p = fi/(N-1)
        ax.text(0.5,0.9,kicker,color=th["fg"],fontsize=26,fontweight="bold",ha="center",va="center",alpha=0.9)
        if p < 0.42:
            e = eo(p/0.2)
            ax.text(0.5,0.60,f"#{top_number}",color=th["player"],fontsize=200,fontweight="bold",ha="center",va="center",alpha=e)
            ax.text(0.5,0.40,"climbing",color="#57e26b",fontsize=36,fontweight="bold",ha="center",va="center",alpha=e)
        elif p < 0.55:
            jx, jy = rng.uniform(-0.02,0.02), rng.uniform(-0.015,0.015)
            ax.text(0.5+jx,0.60+jy,f"#{drop_number}",color=th["cutoff"],fontsize=210,fontweight="bold",ha="center",va="center")
            ax.text(0.5,0.40,"...then it fell apart",color=th["cutoff"],fontsize=36,fontweight="bold",ha="center",va="center")
        else:
            e = eo((p-0.55)/0.2)
            ax.text(0.5,0.60,f"#{drop_number} ▼",color=th["cutoff"],fontsize=150,fontweight="bold",ha="center",va="center")
            ax.text(0.5,0.40,"the whole story",color=th["fg"],fontsize=30,fontweight="bold",ha="center",va="center",alpha=e)
        if p >= 0.72:
            e = eo((p-0.72)/0.2)
            ax.text(0.5,0.18,f"I ran {trials:,} simulations\nto fix it.",color=th["player"],fontsize=32,fontweight="bold",ha="center",va="center",alpha=e)
            ax.text(0.5,0.07,"FULL STORY ↓",color=th["fg"],fontsize=32,fontweight="bold",ha="center",va="center",alpha=e)
    animation.FuncAnimation(fig, draw, frames=N, interval=1000/fps).save(out, writer=_writer(fps), savefig_kwargs={"facecolor": th["bg"]})
    plt.close(); return out
