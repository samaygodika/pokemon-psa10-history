#!/usr/bin/env python3
"""Walk-forward backtest of trending signals on the point-in-time panel.

    analysis/.venv/bin/python analysis/backtest.py     # -> analysis/out/backtest.md, backtest_monthly.csv

The question this answers: at each month-start t, if we ranked the liquid
universe by signal S using only information up to t, did the top of the
ranking go on to beat the rest over the next 60 days? Measured with:

  IC         Spearman rank correlation between S(t) and the 60-day forward
             EXCESS return (card minus universe median), per month. Excess,
             because "everything went up in 2021" is not a trending signal.
  t-stat     mean IC / std IC * sqrt(months). Rule of thumb: |t| > 2.5 with
             IC consistent across years before believing a signal.
  top-bottom mean fwd60_ex of the top decile minus the bottom decile.
  top gross  mean raw fwd60 of the top decile, and the same net of a 13%
             round-trip fee (eBay/PWCC seller fee; buyer pays shipping on
             top). Money is only made if net > 0 by a margin.
  hit rate   share of top-decile cards that rose >= 20% in 60 days.

Signals:
  hand-picked (no fitting)   mom30, mom90, rel_mom30, accel, vol_surge,
                             char_mom30, composite (mean rank of mom90 and
                             vol_surge — the spec's "volume rising before
                             price" idea in its simplest form)
  fitted, walk-forward       ridge regression and a shallow gradient-boosted
                             tree on cross-sectionally ranked features,
                             retrained every month on all rows whose
                             60-day target window ended >= EMBARGO days
                             before the test month, so no training target
                             overlaps a test target. Minimum TRAIN_MIN_MONTHS
                             of history before the first prediction.
  sanity                     "oracle" (score = the target itself; IC must be
                             ~1, proves the evaluation plumbing) and
                             "shuffled" (target permuted within each month;
                             IC must be ~0, proves there's no leak in the
                             metric code).

Everything is reported per year as well, because a signal that only worked
in the 2021 bubble is not a signal.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge

OUT = Path(__file__).resolve().parent / "out"
TARGETS = {"fwd60": ("fwd60_ex", "fwd60"), "exec": ("fwd_exec_ex", "fwd_exec"), "exec180": ("fwd_exec180_ex", "fwd_exec180")}
WINSOR = (0.01, 0.99)   # per-month clip of the raw return before averaging: one $1 junk "entry" would otherwise make a decile look like +1900%
TARGET, RAW_TARGET = TARGETS["exec"]
FEE = 0.13
EMBARGO_DAYS = 90
TRAIN_MIN_MONTHS = 18
FEATURES = ["mom30", "mom90", "mom180", "mom365", "accel", "vol30", "vol90", "vol365", "vol_surge",
            "days_since_sale", "log_price", "n_sales_to_date", "dispersion", "bin_share90", "ebay_share90",
            "rel_mom30", "char_mom30", "char_n", "age_years"]
HAND_SIGNALS = ["mom30", "mom90", "rel_mom30", "accel", "vol_surge", "char_mom30"]


def rank_within(df, cols):
    """Cross-sectional percentile rank per date, NaN -> 0.5 (neutral)."""
    r = df.groupby("date")[cols].rank(pct=True)
    return r.fillna(0.5)


def month_metrics(g, score):
    g = g[[score, TARGET, RAW_TARGET]].dropna().copy()
    if len(g) < 30:
        return None
    ic = spearmanr(g[score], g[TARGET]).correlation
    lo, hi = g[RAW_TARGET].quantile(WINSOR)
    g[RAW_TARGET] = g[RAW_TARGET].clip(lo, hi)
    g[TARGET] = g[TARGET].clip(lo - g[TARGET].median(), hi - g[TARGET].median())
    q = pd.qcut(g[score].rank(method="first"), 10, labels=False)
    top, bot = g[q == 9], g[q == 0]
    top_gross = top[RAW_TARGET].mean()
    return {
        "n": len(g), "ic": ic,
        "top_bottom": top[TARGET].mean() - bot[TARGET].mean(),
        "top_gross": top_gross,
        "top_net": (1 + top_gross) * (1 - FEE) - 1,
        "top_hit20": (top[RAW_TARGET] >= 0.20).mean(),
        "univ_gross": g[RAW_TARGET].mean(),
    }


def evaluate(panel, score, label):
    rows = []
    for date, g in panel.groupby("date"):
        m = month_metrics(g, score)
        if m:
            rows.append({"signal": label, "date": date, **m})
    return pd.DataFrame(rows)


def walk_forward(panel, model_factory, label):
    """Retrain each month on embargoed history; return panel with a score column."""
    X = rank_within(panel, FEATURES)
    y = panel[TARGET]
    dates = sorted(panel.date.unique())
    scores = pd.Series(np.nan, index=panel.index)
    for i, t in enumerate(dates):
        if i < TRAIN_MIN_MONTHS:
            continue
        cutoff = t - pd.Timedelta(days=EMBARGO_DAYS)
        train = (panel.date <= cutoff) & y.notna()
        test = panel.date == t
        if train.sum() < 500:
            continue
        model = model_factory()
        model.fit(X[train], y[train])
        scores[test] = model.predict(X[test])
    col = f"score_{label}"
    panel[col] = scores
    return col


def summarize(monthly):
    out = []
    for sig, g in monthly.groupby("signal", sort=False):
        ic = g.ic.dropna()
        out.append({
            "signal": sig, "months": len(g), "mean_IC": ic.mean(), "IC_tstat": ic.mean() / ic.std(ddof=1) * np.sqrt(len(ic)) if len(ic) > 2 else np.nan,
            "IC>0 %": (ic > 0).mean() * 100, "top-bottom": g.top_bottom.mean(), "top gross": g.top_gross.mean(),
            "top net": g.top_net.mean(), "hit>=20%": g.top_hit20.mean(), "universe gross": g.univ_gross.mean(),
        })
    return pd.DataFrame(out)


def by_year(monthly, signals):
    m = monthly[monthly.signal.isin(signals)].copy()
    m["year"] = m.date.dt.year
    t = m.groupby(["signal", "year"]).agg(months=("ic", "size"), mean_IC=("ic", "mean"), top_net=("top_net", "mean")).reset_index()
    return t.pivot(index="year", columns="signal", values=["mean_IC", "top_net"])


def bootstrap_ci(ic, n=2000, seed=0):
    rng = np.random.default_rng(seed)
    ic = ic.dropna().values
    means = [rng.choice(ic, len(ic), replace=True).mean() for _ in range(n)]
    return np.percentile(means, [2.5, 97.5])


def fmt(df, floatfmt="{:.3f}"):
    return df.to_markdown(index=False, floatfmt=".3f") if hasattr(df, "to_markdown") else df.to_string()


def main():
    global TARGET, RAW_TARGET
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=list(TARGETS), default="exec", help="exec = buy at first sale after t, sell at median of (t+60, t+90] (default); exec180 = sell at (t+150, t+180]; fwd60 = reference-price return")
    ap.add_argument("--min-price", type=float, default=0, help="restrict the universe to cards whose reference price at t is at least this (fees and noise matter less on expensive cards)")
    args = ap.parse_args()
    TARGET, RAW_TARGET = TARGETS[args.target]
    panel = pd.read_csv(OUT / "panel.csv", parse_dates=["date"])
    if args.min_price:
        panel = panel[panel.ref >= args.min_price].reset_index(drop=True)
    tag = f"{args.target}" + (f"_min{int(args.min_price)}" if args.min_price else "")
    print(f"panel: {len(panel)} rows, {panel.date.nunique()} months, {panel[TARGET].notna().sum()} with target")

    # hand-picked signals + composite
    r = rank_within(panel, ["mom90", "vol_surge"])
    panel["composite"] = (r.mom90 + r.vol_surge) / 2
    signals = HAND_SIGNALS + ["composite"]

    # fitted, walk-forward
    signals.append(walk_forward(panel, lambda: Ridge(alpha=10.0), "ridge"))
    signals.append(walk_forward(panel, lambda: HistGradientBoostingRegressor(max_depth=3, max_iter=150, learning_rate=0.05, min_samples_leaf=200, l2_regularization=1.0), "gbm"))

    # sanity checks
    panel["oracle"] = panel[TARGET]
    rng = np.random.default_rng(0)
    panel["shuffled"] = panel.groupby("date")[TARGET].transform(lambda s: rng.permutation(s.values))
    signals += ["oracle", "shuffled"]

    monthly = pd.concat([evaluate(panel, s, s.replace("score_", "")) for s in signals], ignore_index=True)
    monthly.to_csv(OUT / f"backtest_monthly_{tag}.csv", index=False)
    summary = summarize(monthly)
    years = by_year(monthly, ["mom30", "mom90", "vol_surge", "composite", "ridge", "gbm"])

    lines = [f"# Trending backtest — target `{args.target}` ({TARGET})" + (f", cards ≥ ${args.min_price:,.0f}" if args.min_price else ""), "",
             f"Panel: {len(panel):,} asset-months over {panel.date.nunique()} months ({panel.date.min().date()} → {panel.date.max().date()}), "
             f"{panel.asset_id.nunique():,} assets; {panel[TARGET].notna().sum():,} rows with a 60-day target.", "",
             {"exec": "Target: buy at the first sale after t (within 14 days), sell at the median of sales 60–90 days out, in excess of the universe median that month.",
              "exec180": "Target: buy at the first sale after t (within 14 days), sell at the median of sales 150–180 days out, in excess of the universe median that month.",
              "fwd60": "Target: 60-day forward return of the reference price, in excess of the universe median that month (shares ref(t) with the momentum features — overstates reversal)."}[args.target]
             + f" Fee for 'net': {FEE:.0%} round trip. Raw returns winsorized at the {WINSOR[0]:.0%}/{WINSOR[1]:.0%} per month before averaging.", "",
             "## Summary (mean over months)", "", summary.to_markdown(index=False, floatfmt=".3f"), "",
             "## By year (mean IC / top-decile net return)", "", years.round(3).to_markdown(), ""]
    for sig in ["mom90", "composite", "ridge", "gbm"]:
        ic = monthly[monthly.signal == sig].ic
        if len(ic.dropna()) > 5:
            lo, hi = bootstrap_ci(ic)
            lines.append(f"- {sig}: mean IC 95% bootstrap CI [{lo:.3f}, {hi:.3f}]")
    lines += ["", "## Reading it", "",
              "- `oracle` must be ~1.0 and `shuffled` ~0.0, or the evaluation itself is broken.",
              "- A signal is worth wiring into the app only if its IC is positive in most years, its t-stat is above ~2.5, and the top-decile **net** return beats the universe by a margin that survives the fee.",
              "- Fitted models are trained only on months whose targets ended before the test month (90-day embargo), retrained monthly.",
              "- Pop-based features are absent on purpose (no point-in-time pop history yet); the sale-visibility lag on alt.xyz is unmeasured, so treat sub-monthly effects with suspicion."]
    (OUT / f"backtest_{tag}.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
