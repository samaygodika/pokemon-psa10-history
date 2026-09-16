#!/usr/bin/env python3
"""Character catch-up study, done properly: 2x2 design with month-clustered t-stats.

    analysis/.venv/bin/python analysis/catchup_study.py            # all cards
    analysis/.venv/bin/python analysis/catchup_study.py --english  # PokeSniper's universe (no Japanese rows)

Reads analysis/out/panel.csv. For every (month, character) it measures
"heat" = share of the character's OTHER liquid cards up >20% over 30 days
(leave-one-out, characters with >= MIN_CHAR cards), then splits every row
into hot/quiet x laggard/leader (laggard = own mom30 < 5%). Laggards are
further split into "stale" (no sale in 30 days, so mom30 is exactly 0 by
construction) and "active".

The comparison that matters is hot-laggard vs QUIET-LEADER, not hot-laggard
vs "all quiet cards": quiet-laggards are the worst group in every cut, so
pooling them into the baseline manufactures a catch-up premium. Significance
is a t-stat on the monthly difference of group means (months are the
independent unit; card-months within a month share the same market), plus
the share of months in which the difference was positive.

Also prints: by year, by market regime, by price tier, by source mix, and a
threshold sensitivity grid (hot 20-50%, up 10-30%, laggard 0-10%).
"""
import argparse
import glob
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "analysis" / "out"
FEE = 0.13
MIN_CHAR = 5
HARD_CAP = 3.0          # +300% in 60-90 days; anything above is a junk entry price, not a return
MIN_MONTH_ROWS = 200


def japanese_ids():
    a = pd.read_csv(ROOT / "history" / "assets.csv", usecols=["asset_id", "card_name", "set", "variety"], dtype=str)
    jp = a.card_name.str.contains("Japanese", case=False, na=False) | a.set.str.contains("Japanese", case=False, na=False) | a.variety.str.contains("Japanese", case=False, na=False)
    return set(a[jp].asset_id)


def heat_loo(q, up, min_n):
    g = q.groupby(["date", "subject"]).mom30
    n, k = g.transform("count"), g.transform(lambda s: (s > up).sum())
    return ((k - (q.mom30 > up).astype(int)) / (n - 1)).where(n - 1 >= min_n)


def study(panel, tgt, hot=0.30, up=0.20, lag=0.05, min_n=MIN_CHAR):
    q = panel[panel[tgt].notna() & panel.mom30.notna()].copy()
    # winsorize on the whole month BEFORE any filtering, with a hard cap: a
    # sparse early month clipped at its own 99th percentile still lets one
    # $1 junk entry (+5000%) through and wrecks every mean it touches
    q["raw_w"] = q.groupby("date")[tgt].transform(lambda s: s.clip(*s.quantile([0.01, 0.99]))).clip(-0.9, HARD_CAP)
    q["ex_w"] = q.raw_w - q.groupby("date").raw_w.transform("median")
    q = q[q.groupby("date").raw_w.transform("size") >= MIN_MONTH_ROWS]
    q["heat"] = heat_loo(q, up, min_n)
    q = q[q.heat.notna()].copy()
    q["net"] = (1 + q.raw_w) * (1 - FEE) - 1
    q["dbl"] = q[tgt] >= 1.0
    q["stale"] = q.vol30 == 0
    hot_, lag_ = q.heat >= hot, q.mom30 < lag
    q["grp"] = np.select([hot_ & lag_ & q.stale, hot_ & lag_ & ~q.stale, hot_ & ~lag_, ~hot_ & lag_, ~hot_ & ~lag_],
                         ["hot-lag-stale", "hot-lag-active", "hot-leader", "quiet-lag", "quiet-leader"], default="other")
    return q


def stage_table(panel, q):
    """Breadth this month vs the same character's breadth at the previous rebalance."""
    cb = panel[panel.mom30.notna()].groupby(["date", "subject"]).mom30.agg(lambda s: (s > 0.2).mean()).rename("cb_prev").reset_index()
    cb["date"] = cb.date + pd.offsets.MonthBegin(1)
    r = q.merge(cb, on=["date", "subject"], how="left")
    r = r[r.cb_prev.notna()].copy()
    hot = r.heat >= 0.3
    r["stage"] = np.select([hot & (r.cb_prev < 0.2), hot & (r.cb_prev >= 0.2) & (r.cb_prev < 0.3), hot & (r.cb_prev >= 0.3)],
                           ["newly hot (prev <20%)", "warming (prev 20-30%)", "stayed hot (prev >=30%)"], default="not hot")
    r["lagg"] = np.where(r.mom30 < 0.05, "laggard", "leader")
    t = r.pivot_table(index="stage", columns="lagg", values=["ex_w", "net"], aggfunc=["mean", "median"]).round(3)
    n = r.pivot_table(index="stage", columns="lagg", values="ex_w", aggfunc="size")
    n.columns = pd.MultiIndex.from_tuples([("n", "", c) for c in n.columns])
    return pd.concat([t, n], axis=1)


def table(q):
    t = q.groupby("grp").agg(n=("ex_w", "size"), mean_ex=("ex_w", "mean"), med_ex=("ex_w", "median"), gross=("raw_w", "mean"),
                             net=("net", "mean"), dbl=("dbl", "mean"), months=("date", "nunique"))
    base = q[q.grp == "quiet-leader"].groupby("date").ex_w.mean()
    ts, pos = {}, {}
    for g, sub in q.groupby("grp"):
        d = (sub.groupby("date").ex_w.mean() - base).dropna()
        ts[g] = d.mean() / d.std(ddof=1) * np.sqrt(len(d)) if len(d) > 2 and d.std(ddof=1) > 0 else np.nan
        pos[g] = (d > 0).mean() if len(d) else np.nan
    t["t_vs_quiet_leader"] = pd.Series(ts)
    t["months_positive"] = pd.Series(pos)
    return t.round(3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--english", action="store_true", help="drop Japanese-language rows (PokeSniper's universe)")
    ap.add_argument("--grid", action="store_true", help="also print the threshold sensitivity grid (slow)")
    ap.add_argument("--stage", action="store_true", help="split hot characters by stage: newly hot / warming / stayed hot")
    args = ap.parse_args()
    pd.set_option("display.width", 250)
    p = pd.read_csv(OUT / "panel.csv", parse_dates=["date"])
    if args.english:
        p = p[~p.asset_id.isin(japanese_ids())]
    p["year"] = p.date.dt.year
    for tgt in ["fwd_exec", "fwd_exec180"]:
        q = study(p, tgt)
        print(f"\n===== {tgt} ({'English only' if args.english else 'all cards'}): hot>=30%, up>20%, laggard mom30<5%, char>={MIN_CHAR} =====")
        print(table(q))
        print("\n-- by year: mean excess / n --")
        print(q.pivot_table(index="year", columns="grp", values="ex_w", aggfunc=["mean", "size"]).round(3))
        br = q.groupby("date").mom30.apply(lambda s: (s > 0.2).mean())
        q["regime"] = pd.cut(q.date.map(br), [0, 0.15, 0.30, 1], labels=["quiet<15%", "mid", "hot>30%"])
        print("\n-- by market regime (universe breadth at t): mean excess --")
        print(q.pivot_table(index="regime", columns="grp", values="ex_w", aggfunc="mean", observed=True).round(3))
        q["tier"] = pd.cut(q.ref, [0, 100, 500, 2000, 1e9], labels=["<100", "100-500", "500-2k", "2k+"])
        print("\n-- by price tier: net return after fee --")
        print(q.pivot_table(index="tier", columns="grp", values="net", aggfunc="mean", observed=True).round(3))
        if args.stage:
            print(f"\n-- {tgt}: stage of character heat (this month vs last month's breadth) x laggard --")
            print(stage_table(p, q))
        if args.grid and tgt == "fwd_exec":
            rows = []
            for hot in (0.2, 0.3, 0.4, 0.5):
                for up in (0.1, 0.2, 0.3):
                    for lag in (0.0, 0.05, 0.10):
                        s = study(p, tgt, hot, up, lag)
                        hl, ql = s[s.grp.str.startswith("hot-lag")], s[s.grp == "quiet-leader"]
                        rows.append(dict(hot=hot, up=up, lag=lag, n_hotlag=len(hl), hotlag_ex=hl.ex_w.mean(), quiet_leader_ex=ql.ex_w.mean(),
                                         diff=hl.ex_w.mean() - ql.ex_w.mean(), hotlag_net=hl.net.mean()))
            print("\n===== sensitivity grid: hot-laggard minus quiet-leader mean excess =====")
            print(pd.DataFrame(rows).round(3).to_string())


if __name__ == "__main__":
    main()
