#!/usr/bin/env python3
"""Rate tables for the v0 buy list (history/buy_list.py), from the loose panel.

    analysis/.venv/bin/python analysis/buy_list_rates.py      # -> history/buy_list_rates.csv, history/buy_list_lifts.csv, out/buy_list_rates.md

The v0 list (handoff/2026-10-21-buy-model-strategy.md §12) scores a PSA 10 card as the
base rate of its cell, adjusted by a few cuts the autopsy found to hold inside a month,
and turns that into an expected net gain with the cell's typical win and loss sizes.
This script estimates those numbers once from analysis/out/panel_loose.csv (panel.py
--min-sales-180 1 --min-sales-60 0 --min-total-sales 4: every card with a sale in the
180 days before a monthly anchor, sales through 2026-10-20) under the money target
(entry = median of the sales in the 3 weeks after the anchor grossed up for costs, exit =
40th percentile of the sales in the exit window net of fees, illiquid exits marked and
haircut 15%), English cards, other franchises out, anchors whose exit window closed.

Cells: regime x era x price tier x liquidity, per horizon (90 and 180 days):
  regime     market breadth at the anchor = share of English cards priced >= $150 with
             >= 3 sales in the past year whose 30-day change exceeds +5%:
             flat < 0.25, warm 0.25-0.40, hot >= 0.40 (2021-24 sat at 0.15-0.30, 2025-26 at 0.37-0.62)
  era        wotc <= 2003, vintage <= 2013, modern <= 2019, current
  tier       reference price < $50, $50-150, $150-500, $500-2k, $2k+
  liquidity  sales in the past 90 days: 0 (a sale in 180 d only), 1, 2-3, 4-7, 8+
win = net return >= +30% at the horizon. Each row carries n, the win rate, the mean and
median net return of the winners and of the losers (winsorized at +300% / -100%), and
the median over all. The nightly uses the winners' MEDIAN and the losers' mean: the
winners' mean is carried by 2026's multi-baggers.
Coarser fallbacks (without liquidity, then without era, then regime only) are written
too; the nightly script takes the finest level with n >= MIN_N.

Lifts: for the cuts the nightly script applies, the ratio of the win rate with the cut on
to the win rate with it off, and the same as an odds ratio (what the nightly combines,
capped at OR_CAP so a 64% base cannot be pushed to 90%), inside the same regime bucket,
per horizon, with the n and the share of months (>= 20 flagged rows) in which the flagged
rows beat the month's base.
  stale30    no sale in the 30 days before the anchor
  jp_heat    the character's Japanese cards (>= 2) up >= 15% over 30 days (90 d only)
  band_high  the card >= 1 sd above its own 1-year band, reference >= $150
  tail_lead  bottom price quartile of its set (set-wide, >= 8 cards) with the set's top
             quartile up >= 30% in 90 d, reference >= $150 (logged, not scored)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "analysis" / "out"
DATA_END = pd.Timestamp("2026-10-20")
MIN_N = 100
TIER_EDGES = [0, 50, 150, 500, 2000, np.inf]
TIER_LABELS = ["<50", "50-150", "150-500", "500-2k", "2k+"]
LIQ_EDGES = [-1, 0, 1, 3, 7, np.inf]
LIQ_LABELS = ["0", "1", "2-3", "4-7", "8+"]
REGIME_EDGES = [-1, 0.25, 0.40, 2]
REGIME_LABELS = ["flat", "warm", "hot"]
OTHER = r"lorcana|one piece|weiss schwarz|magic the gathering|yu-gi-oh|dragon ball|digimon|disney"
LIFT_CAP = 1.5
OR_CAP = 2.0      # the nightly combines cuts on the odds scale: odds x odds_ratio_capped per cut


def main():
    p = pd.read_csv(OUT / "panel_loose.csv", low_memory=False)
    p["date"] = pd.to_datetime(p.date)
    assets = pd.read_csv(ROOT / "history" / "assets.csv", usecols=["asset_id", "card_name"])
    other = set(assets.loc[assets.card_name.str.contains(OTHER, case=False, regex=True, na=False), "asset_id"])
    p = p[~p.asset_id.isin(other)]
    en = p.lang == "english"
    # breadth per anchor, the nightly definition
    liquid = en & (p.ref >= 150) & (p.vol365 >= 3)
    breadth = p[liquid].groupby("date").mom30.agg(lambda s: (s > 0.05).mean()).rename("breadth")
    p = p.join(breadth, on="date")
    # Japanese heat per (date, subject)
    jp = p[p.lang == "japanese"].groupby(["date", "subject"]).mom30.agg(["mean", "count"])
    jp.columns = ["char_jp_mom30", "char_jp_n"]
    p = p.join(jp, on=["date", "subject"])
    p.loc[p.char_jp_n.fillna(0) < 2, "char_jp_mom30"] = np.nan
    # set-wide price quartile and the chase cards' 90 d run (English rows of the set at t)
    pe = p.loc[en, ["date", "set", "ref", "mom90"]].copy()
    pe["q"] = pe.groupby(["date", "set"]).ref.rank(pct=True)
    nset = pe.groupby(["date", "set"]).ref.transform("count")
    pe.loc[nset < 8, "q"] = np.nan
    lead = pe[pe.q >= 0.75].groupby(["date", "set"]).mom90.agg(["mean", "count"])
    lead.columns = ["set_lead_mom90", "set_lead_n"]
    p["set_price_q"] = np.nan
    p.loc[pe.index, "set_price_q"] = pe.q
    p = p.join(lead, on=["date", "set"])
    p.loc[p.set_lead_n.fillna(0) < 2, "set_lead_mom90"] = np.nan

    u = p[en].copy()
    u["tier"] = pd.cut(u.ref, TIER_EDGES, labels=TIER_LABELS)
    u["liq"] = pd.cut(u.vol90, LIQ_EDGES, labels=LIQ_LABELS)
    u["regime"] = pd.cut(u.breadth, REGIME_EDGES, labels=REGIME_LABELS)
    u["stale30"] = u.days_since_sale >= 30
    u["jp_heat"] = u.char_jp_mom30 >= 0.15
    u["band_high"] = (u.band_z >= 1) & (u.ref >= 150)
    u["tail_lead"] = (u.set_price_q <= 0.25) & (u.set_lead_mom90 >= 0.3) & (u.ref >= 150)

    cells, lifts, md = [], [], ["# v0 buy-list rate tables (from panel_loose.csv, sales through 2026-10-20)\n\n"]
    reg_hist = u.drop_duplicates("date")[["date", "breadth", "regime"]].sort_values("date")
    md.append("Breadth by anchor month (the regime nowcast):\n\n" + ", ".join(f"{d.date():%Y-%m} {b:.2f} {r}" for d, b, r in reg_hist.itertuples(index=False)) + "\n\n")
    for h in (90, 180):
        cut = DATA_END - pd.Timedelta(days=h + 30)
        w = u[(u.date <= cut) & u[f"mny{h}"].notna()].copy()
        net = w[f"mny{h}"].clip(-1.0, 3.0)
        w["win"] = w[f"mny{h}"] >= 0.30
        w["net"] = net
        md.append(f"## horizon {h} d: {len(w):,} trades, {w.win.mean():.1%} win, median net {w.net.median():+.1%}\n\n")
        for level, keys in (("regime_era_tier_liq", ["regime", "era", "tier", "liq"]), ("regime_era_tier", ["regime", "era", "tier"]), ("regime_tier", ["regime", "tier"]), ("regime", ["regime"])):
            g = w.groupby(keys, observed=True)
            tab = g.agg(n=("win", "size"), win_rate=("win", "mean"), net_median=("net", "median"))
            tab["win_net_mean"] = g.apply(lambda x: x.net[x.win].mean() if x.win.any() else np.nan, include_groups=False)
            tab["win_net_median"] = g.apply(lambda x: x.net[x.win].median() if x.win.any() else np.nan, include_groups=False)
            tab["loss_net_mean"] = g.apply(lambda x: x.net[~x.win].mean() if (~x.win).any() else np.nan, include_groups=False)
            tab["loss_net_median"] = g.apply(lambda x: x.net[~x.win].median() if (~x.win).any() else np.nan, include_groups=False)
            tab = tab.reset_index()
            for k in ["regime", "era", "tier", "liq"]:
                if k not in tab.columns:
                    tab[k] = ""
            tab["horizon"] = h
            tab["level"] = level
            cells.append(tab[["horizon", "level", "regime", "era", "tier", "liq", "n", "win_rate", "win_net_mean", "win_net_median", "loss_net_mean", "loss_net_median", "net_median"]])
        rt = w.groupby(["regime", "tier"], observed=True).agg(n=("win", "size"), win=("win", "mean"), net_med=("net", "median")).reset_index()
        md.append("Win rate by regime x tier:\n\n| regime | tier | n | win | median net |\n|---|---|---|---|---|\n" + "".join(f"| {r.regime} | {r.tier} | {r.n:,} | {r.win:.1%} | {r.net_med:+.1%} |\n" for r in rt.itertuples()) + "\n")
        for cutname in ("stale30", "jp_heat", "band_high", "tail_lead"):
            for reg, g in w.groupby("regime", observed=True):
                on = g[cutname].fillna(False).astype(bool)
                if on.sum() < 50 or (~on).sum() < 50:
                    continue
                p1, p0 = g.win[on].mean(), g.win[~on].mean()
                lift = p1 / p0 if p0 > 0 else np.nan
                odds_ratio = (p1 / (1 - p1)) / (p0 / (1 - p0)) if 0 < p0 < 1 and 0 < p1 < 1 else np.nan
                months, ahead = 0, 0
                for _, gm in g.groupby("date"):
                    om = gm[cutname].fillna(False).astype(bool)
                    if om.sum() >= 20 and (~om).sum() >= 20:
                        months += 1
                        ahead += gm.win[om].mean() > gm.win[~om].mean()
                lifts.append({"horizon": h, "cut": cutname, "regime": reg, "n_on": int(on.sum()), "win_on": g.win[on].mean(), "win_off": g.win[~on].mean(),
                              "lift": lift, "lift_capped": min(lift, LIFT_CAP) if pd.notna(lift) else np.nan,
                              "odds_ratio": odds_ratio, "odds_ratio_capped": min(odds_ratio, OR_CAP) if pd.notna(odds_ratio) else np.nan,
                              "months": months, "months_ahead": ahead / months if months else np.nan})
        lt = pd.DataFrame([x for x in lifts if x["horizon"] == h])
        md.append("Lifts by cut and regime:\n\n| cut | regime | n on | win on | win off | lift | odds ratio | months | ahead |\n|---|---|---|---|---|---|---|---|---|\n" + "".join(f"| {r.cut} | {r.regime} | {r.n_on:,} | {r.win_on:.1%} | {r.win_off:.1%} | {r.lift:.2f} | {r.odds_ratio:.2f} | {r.months} | {r.months_ahead:.0%} |\n" for r in lt.itertuples()) + "\n")
    cells = pd.concat(cells, ignore_index=True)
    cells.to_csv(ROOT / "history" / "buy_list_rates.csv", index=False, float_format="%.4f")
    pd.DataFrame(lifts).to_csv(ROOT / "history" / "buy_list_lifts.csv", index=False, float_format="%.4f")
    (OUT / "buy_list_rates.md").write_text("".join(md))
    print("".join(md))
    print(f"wrote {len(cells)} cell rows, {len(lifts)} lift rows")


if __name__ == "__main__":
    main()
