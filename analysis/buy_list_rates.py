#!/usr/bin/env python3
"""Rate tables for the v0 buy list (history/buy_list.py), from the loose panel.

    analysis/.venv/bin/python analysis/buy_list_rates.py      # -> history/buy_list_rates.csv, history/buy_list_lifts.csv, out/buy_list_rates.md
    analysis/.venv/bin/python analysis/buy_list_rates.py --panel panel_loose_2026-11-02.csv   # data end from the panel's .meta.json
                                                              #   (+ history/buy_list_rates_meta.json: data end, months per horizon/regime)

The monthly re-fit (analysis/buy_list_refit.py, 2026-10-23) calls prepare() / fit() / write_tables()
directly: fit(prepare(panel), data_end) is a pure function of the panel and the data end.
DATA_END below is only the first panel's (panel_loose.csv has no .meta.json).

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

Months (2026-10-22, v0.1): the trades of one anchor month are not independent draws -- one
market moves them together -- so each cell also carries n_months (distinct anchor months),
month_se (the cluster-robust standard error of the win rate, months as clusters: k/(k-1) x
sum over months of (wins - rate x n)^2 / N^2) and month_half90 (Student t at k-1 degrees
of freedom x month_se: the 90% half-width the nightly's band uses; blank under 2 months).
Measured that day: the hot vintage $2k+ cell's 242 trades are 3 months at 25% / 63% / 80%,
the hot wotc $2k+ cell's 428 are 3 at 67% / 59% / 46%; a Wilson band on n said +-4 points.

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
  thin_history  (2026-10-22, v0.1, Sid: TRR Mudkip Star ranked #1 at ~$1M after one $20,400
             sale in 2024-12 and nothing between) a card dated 2013 or earlier with no price a
             year back (mom365 blank: no sale in the 180 days before the year-ago date), so how
             far it ran cannot be measured. Its rows are compared with their OWN cells' win
             rate (vs = own_cell), not with the rest of the regime: the cut is applied on top of
             the cell base, and thin cards sit in thin cells that already score lower. At 180 d
             it lost to its cells in every regime (flat -2.8, warm -5.5, hot -8.0 points).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

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
OWN_CELL_CUTS = ("thin_history",)   # measured against the flagged trades' own cells (win_off = their cells' mean rate)


def month_band(w, keys):
    """n_months, month_se, month_half90 per cell of `keys`: anchor months as clusters (module doc)."""
    mm = w.groupby(keys + ["date"], observed=True).win.agg(wins="sum", n="size").reset_index()

    def one(x):
        k, big_n = len(x), x.n.sum()
        if k < 2:
            return pd.Series({"n_months": k, "month_se": np.nan, "month_half90": np.nan})
        rate = x.wins.sum() / big_n
        se = np.sqrt(k / (k - 1) * ((x.wins - rate * x.n) ** 2).sum() / big_n ** 2)
        return pd.Series({"n_months": k, "month_se": se, "month_half90": stats.t.ppf(0.95, k - 1) * se})
    return mm.groupby(keys, observed=True)[["wins", "n"]].apply(one).reset_index()


def prepare(p, assets):
    """the panel's English trades with every tag the cells and cuts need (regime, tier, liq, the cuts),
    other franchises out. Point-in-time per anchor, so one prepared panel serves any data end (fit)."""
    p = p.copy()
    p["date"] = pd.to_datetime(p.date)
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
    u["thin_history"] = u.mom365.isna() & u.era.isin(["wotc", "vintage"])
    return u


def closed(u, h, data_end):
    """the trades at horizon h whose exit window had passed by data_end (anchor <= data_end - (h + 30) d), with win and net."""
    cut = data_end - pd.Timedelta(days=h + 30)
    w = u[(u.date <= cut) & u[f"mny{h}"].notna()].copy()
    w["win"] = w[f"mny{h}"] >= 0.30
    w["net"] = w[f"mny{h}"].clip(-1.0, 3.0)
    return w


def fit(u, data_end):
    """-> (cell rows, lift rows, the markdown report, meta) from the trades closed by data_end."""
    cells, lifts, md = [], [], [f"# v0 buy-list rate tables (from the loose panel, sales through {data_end.date()})\n\n"]
    meta = {"data_end": f"{data_end.date()}", "horizons": {}}
    reg_hist = u.drop_duplicates("date")[["date", "breadth", "regime"]].sort_values("date")
    md.append("Breadth by anchor month (the regime nowcast):\n\n" + ", ".join(f"{d.date():%Y-%m} {b:.2f} {r}" for d, b, r in reg_hist.itertuples(index=False)) + "\n\n")
    for h in (90, 180):
        w = closed(u, h, data_end)
        if w.empty:                       # nothing closed yet at this horizon (an early anchor date): no cells, no lifts
            meta["horizons"][str(h)] = {"trades": 0, "months": 0, "closed_through": "", "months_by_regime": {}}
            continue
        months = w.drop_duplicates("date")[["date", "regime"]].sort_values("date")
        meta["horizons"][str(h)] = {"trades": int(len(w)), "months": int(len(months)),
                                    "closed_through": f"{months.date.max().date()}" if len(months) else "",
                                    "months_by_regime": {str(r): int(n) for r, n in months.regime.value_counts().sort_index().items()}}
        md.append(f"## horizon {h} d: {len(w):,} trades, {w.win.mean():.1%} win, median net {w.net.median():+.1%}\n\n")
        for level, keys in (("regime_era_tier_liq", ["regime", "era", "tier", "liq"]), ("regime_era_tier", ["regime", "era", "tier"]), ("regime_tier", ["regime", "tier"]), ("regime", ["regime"])):
            g = w.groupby(keys, observed=True)
            tab = g.agg(n=("win", "size"), win_rate=("win", "mean"), net_median=("net", "median"))
            tab["win_net_mean"] = g.apply(lambda x: x.net[x.win].mean() if x.win.any() else np.nan, include_groups=False)
            tab["win_net_median"] = g.apply(lambda x: x.net[x.win].median() if x.win.any() else np.nan, include_groups=False)
            tab["loss_net_mean"] = g.apply(lambda x: x.net[~x.win].mean() if (~x.win).any() else np.nan, include_groups=False)
            tab["loss_net_median"] = g.apply(lambda x: x.net[~x.win].median() if (~x.win).any() else np.nan, include_groups=False)
            tab = tab.reset_index().merge(month_band(w, keys), on=keys, how="left")
            for k in ["regime", "era", "tier", "liq"]:
                if k not in tab.columns:
                    tab[k] = ""
            tab["horizon"] = h
            tab["level"] = level
            cells.append(tab[["horizon", "level", "regime", "era", "tier", "liq", "n", "win_rate", "win_net_mean", "win_net_median", "loss_net_mean", "loss_net_median", "net_median",
                              "n_months", "month_se", "month_half90"]])
        rt = w.groupby(["regime", "tier"], observed=True).agg(n=("win", "size"), win=("win", "mean"), net_med=("net", "median")).reset_index()
        md.append("Win rate by regime x tier:\n\n| regime | tier | n | win | median net |\n|---|---|---|---|---|\n" + "".join(f"| {r.regime} | {r.tier} | {r.n:,} | {r.win:.1%} | {r.net_med:+.1%} |\n" for r in rt.itertuples()) + "\n")
        # the win rate of each trade's finest cell, for the cuts measured against their own cells (OWN_CELL_CUTS)
        w["cell_rate"] = w.groupby(["regime", "era", "tier", "liq"], observed=True).win.transform("mean")
        for cutname in ("stale30", "jp_heat", "band_high", "tail_lead", "thin_history"):
            own = cutname in OWN_CELL_CUTS
            for reg, g in w.groupby("regime", observed=True):
                on = g[cutname].fillna(False).astype(bool)
                if on.sum() < 50 or (~on).sum() < 50:
                    continue
                p1, p0 = g.win[on].mean(), (g.cell_rate[on].mean() if own else g.win[~on].mean())
                lift = p1 / p0 if p0 > 0 else np.nan
                odds_ratio = (p1 / (1 - p1)) / (p0 / (1 - p0)) if 0 < p0 < 1 and 0 < p1 < 1 else np.nan
                months, ahead = 0, 0
                for _, gm in g.groupby("date"):
                    om = gm[cutname].fillna(False).astype(bool)
                    if om.sum() >= 20 and (~om).sum() >= 20:
                        months += 1
                        ahead += gm.win[om].mean() > (gm.cell_rate[om].mean() if own else gm.win[~om].mean())
                lifts.append({"horizon": h, "cut": cutname, "regime": reg, "n_on": int(on.sum()), "win_on": g.win[on].mean(), "win_off": p0,
                              "lift": lift, "lift_capped": min(lift, LIFT_CAP) if pd.notna(lift) else np.nan,
                              "odds_ratio": odds_ratio, "odds_ratio_capped": min(odds_ratio, OR_CAP) if pd.notna(odds_ratio) else np.nan,
                              "months": months, "months_ahead": ahead / months if months else np.nan, "vs": "own_cell" if own else "rest"})
        lt = pd.DataFrame([x for x in lifts if x["horizon"] == h])
        md.append("Lifts by cut and regime:\n\n| cut | regime | n on | win on | win off | lift | odds ratio | months | ahead |\n|---|---|---|---|---|---|---|---|---|\n" + "".join(f"| {r.cut} | {r.regime} | {r.n_on:,} | {r.win_on:.1%} | {r.win_off:.1%} | {r.lift:.2f} | {r.odds_ratio:.2f} | {r.months} | {r.months_ahead:.0%} |\n" for r in lt.itertuples()) + "\n")
    cell_cols = ["horizon", "level", "regime", "era", "tier", "liq", "n", "win_rate", "win_net_mean", "win_net_median", "loss_net_mean",
                 "loss_net_median", "net_median", "n_months", "month_se", "month_half90"]
    lift_cols = ["horizon", "cut", "regime", "n_on", "win_on", "win_off", "lift", "lift_capped", "odds_ratio", "odds_ratio_capped", "months", "months_ahead", "vs"]
    return (pd.concat(cells, ignore_index=True) if cells else pd.DataFrame(columns=cell_cols)), pd.DataFrame(lifts, columns=lift_cols), "".join(md), meta


def write_tables(cells, lifts, folder):
    """the two CSVs the nightly reads (history/buy_list.py load_rates / load_lifts)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    cells.to_csv(folder / "buy_list_rates.csv", index=False, float_format="%.4f")
    lifts.to_csv(folder / "buy_list_lifts.csv", index=False, float_format="%.4f")


def panel_data_end(panel_path):
    """the panel's data end from the sidecar analysis/panel.py writes (<panel>.meta.json); None without one."""
    meta = Path(str(panel_path) + ".meta.json")
    return pd.Timestamp(json.load(open(meta))["data_through"]) if meta.exists() else None


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="panel_loose.csv", help="loose panel under analysis/out/")
    ap.add_argument("--data-end", default=None, help="default: the panel's .meta.json data_through, else 2026-10-20 (the first panel)")
    a = ap.parse_args()
    panel_path = OUT / a.panel
    data_end = pd.Timestamp(a.data_end) if a.data_end else (panel_data_end(panel_path) or DATA_END)
    p = pd.read_csv(panel_path, low_memory=False)
    assets = pd.read_csv(ROOT / "history" / "assets.csv", usecols=["asset_id", "card_name"])
    cells, lifts, md, meta = fit(prepare(p, assets), data_end)
    write_tables(cells, lifts, ROOT / "history")
    meta.update(panel=a.panel, fitted_at=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"))
    (ROOT / "history" / "buy_list_rates_meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    (OUT / "buy_list_rates.md").write_text(md)
    print(md)
    print(f"wrote {len(cells)} cell rows, {len(lifts)} lift rows; rates through {data_end.date()}")


if __name__ == "__main__":
    main()
