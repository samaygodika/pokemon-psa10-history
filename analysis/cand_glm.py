#!/usr/bin/env python3
"""The "glm" candidates (2026-10-23): smoother, faster-adapting versions of the v0.1 base rates,
walk-forward through analysis/buy_eval.py.

    analysis/.venv/bin/python analysis/cand_glm.py check     # the cell machinery with no decay must reproduce preds_baseline
    analysis/.venv/bin/python analysis/cand_glm.py run       # -> analysis/out/buy_eval/preds_glm_*.csv.gz
    analysis/.venv/bin/python analysis/cand_glm.py run glm_logit       # one variant
    analysis/.venv/bin/python analysis/cand_glm.py run --live   # the harness's LIVE protocol -> preds_live_glm_*.csv.gz
    analysis/.venv/bin/python analysis/buy_eval.py evaluate live_baseline live_regime live_glm_decay live_glm_logit live_glm_logit_decay
    analysis/.venv/bin/python analysis/cand_glm.py live      # the first lag diagnostic (anchors from 2022-07 / 2022-10)
    analysis/.venv/bin/python analysis/buy_eval.py evaluate baseline regime glm_decay glm_logit glm_logit_decay

Why. v0.1's cell tables count every closed month the same, so they lag a market turn (late-2024 flat
months won 12-19% at 180 d where the tables said 3-5%), and the three regime buckets cut breadth into
steps. Two fixes, chosen up front, nothing tuned on the test folds:

  glm_decay        v0.1 as it is (history/buy_list.py score_cell over analysis/buy_list_rates.py fit), but every
                   training trade weighted 0.5 ** (age / HALF_LIFE_MONTHS), age = months between its anchor and
                   the newest closed anchor at the cutoff (the newest month weighs 1, a year older 0.5). Weighted
                   cell rate, n = the weighted count (the finest cell with >= MIN_CELL_N of it and a win and a loss
                   in it; the parent = the next coarser cell with >= MIN_CELL_N), shrunk toward the parent with
                   SHRINK_K, the cuts' odds ratios estimated with the same weights inside the regime (>= 50 weighted
                   trades on and off; thin_history against its own cells' weighted rate), each capped at OR_CAP,
                   the product at OR_PRODUCT_CAP, thin_history applied after that cap, p capped at P_CAP.
                   With the weights set to 1 it reproduces preds_baseline (`check`).
  glm_logit        L2 logistic regression (C = 1, standardized inputs, trade-weighted like the cells) refitted at
                   every cutoff on the trades closed by it: breadth as a linear spline with knots at the regime
                   edges 0.25 and 0.40 (so it bends where the buckets cut, and extrapolates on a straight line),
                   one-hot era / tier / liquidity, the four cuts, log10 price, log(1 + days since sale, cap 180),
                   mom30 and mom90 clipped to [-0.5, +1.0] (blank -> 0 + a blank flag), band_z clipped to [-3, 3]
                   (blank -> 0 + a flag), and breadth x tier, breadth x era. Breadth is clipped to the range of the
                   training months before it enters: one value per month and only 2-3 years of flat months
                   before 2025, so a slope must not be carried past the highest month it has seen
                   (cand_calib's calib_platt2 broke on exactly that, 2025-02).
  glm_logit_decay  glm_logit with the glm_decay weights (sample_weight; the scaler uses them too).

The design matrix uses FEATURES and tags only (buy_eval.py: point-in-time at the anchor); the labels
mny90 / mny180 / exit_flag* never enter. One prediction per test trade per fold per horizon, every fold.
No inner validation: nothing is chosen, every constant above was fixed before the first run.

`live` (diagnostic, not a harness model). In the harness a trade anchored in month A is scored by a model
trained through anchor A - 1 month (closed by the cutoff after A + h + 30 d); a buyer ON A has only the
trades anchored by A - h - 30 d. Neighbouring anchors share most of their exit window (5 of 6 months at
180 d), so the harness flatters anything that leans on the newest months. `live` refits every model per
test anchor month on R.closed(u, h, anchor) and compares it with the no-decay cells at the same lag
(= v0.1 as the nightly would have run it then); prints the summary, writes analysis/out/buy_eval/glm_live_lag.csv.
The harness then adopted the same protocol as buy_eval.live_folds (anchors from 2023-01, models named live_*,
scored against live_baseline): `run --live` goes through it. Predictions are the raw model p, uncalibrated.
Threads capped at 2 (shared machine).
"""
import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "2")

import argparse  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buy_eval as E  # noqa: E402  (puts history/ on the path too)
import buy_list as BL  # noqa: E402
import buy_list_rates as R  # noqa: E402

HALF_LIFE_MONTHS = 12
MONTH_DAYS = 365.25 / 12
MIN_CELL_N = BL.MIN_CELL_N          # 100
SHRINK_K = BL.SHRINK_K              # 200
OR_CAP = R.OR_CAP                   # 2.0 per cut
OR_PRODUCT_CAP = BL.OR_PRODUCT_CAP  # 3.0
P_CAP = BL.P_CAP                    # 0.90
AFTER_CAP_CUTS = BL.AFTER_CAP_CUTS  # thin_history
OWN_CELL_CUTS = R.OWN_CELL_CUTS     # thin_history
MIN_CUT_N = 50                      # buy_list_rates.fit: >= 50 on and off inside the regime
CUTS = ("stale30", "jp_heat", "band_high", "thin_history")
ERAS = ("wotc", "vintage", "modern", "current")
LEVELS = [("regime_era_tier_liq", ["regime", "era", "tier", "liq"]), ("regime_era_tier", ["regime", "era", "tier"]),
          ("regime_tier", ["regime", "tier"]), ("regime", ["regime"])]
KNOTS = (0.25, 0.40)                # breadth spline knots = the regime edges
B_CENTER = 0.25                     # breadth is centered here in the interactions
C_L2 = 1.0
VARIANTS = ("glm_decay", "glm_logit", "glm_logit_decay")


def decay_weights(dates, half_life=HALF_LIFE_MONTHS):
    """0.5 ** (months behind the newest anchor / half_life); all ones when half_life is None."""
    if half_life is None:
        return np.ones(len(dates))
    age = (dates.max() - dates).dt.days.values / MONTH_DAYS
    return 0.5 ** (age / half_life)


def scorable(df):
    """the trades score_cell can score in run_baseline: one of the four eras, a regime and a tier."""
    return df.era.isin(ERAS) & df.regime.notna() & df.tier.notna()


# ---------------------------------------------------------------- glm_decay: weighted v0.1 cells

def cell_fit(train, w):
    """-> (tables {level: DataFrame(wn, rate, ok, scorable) indexed by its keys}, lifts {(cut, regime): capped OR})."""
    t = train[["regime", "era", "tier", "liq", *CUTS, "win"]].copy()
    t["w"] = w
    t["ww"] = w * t.win.values
    tabs = {}
    for level, keys in LEVELS:
        g = t.groupby(keys, observed=True).agg(wn=("w", "sum"), wwin=("ww", "sum"), nwin=("win", "sum"), n=("win", "size"))
        g["rate"] = g.wwin / g.wn
        g["ok"] = g.wn >= MIN_CELL_N - 1e-9
        g["scorable"] = g.ok & (g.nwin > 0) & (g.nwin < g.n)      # score_cell needs a winners' and a losers' net
        tabs[level] = g[["wn", "rate", "ok", "scorable"]]
    t["cell_rate"] = t.join(tabs[LEVELS[0][0]].rate.rename("cell_rate"), on=LEVELS[0][1]).cell_rate
    lifts = {}
    for cut in CUTS:
        own = cut in OWN_CELL_CUTS
        for reg, g in t.groupby("regime", observed=True):
            on = g[cut].fillna(False).astype(bool).values
            w_on, w_off = g.w.values[on].sum(), g.w.values[~on].sum()
            if w_on < MIN_CUT_N or w_off < MIN_CUT_N:
                continue
            p1 = g.ww.values[on].sum() / w_on
            if own:
                cr, wc = g.cell_rate.values[on], g.w.values[on]
                keep = ~np.isnan(cr)
                p0 = (cr[keep] * wc[keep]).sum() / wc[keep].sum() if keep.any() else np.nan
            else:
                p0 = g.ww.values[~on].sum() / w_off
            if 0 < p0 < 1 and 0 < p1 < 1:
                lifts[(cut, reg)] = min((p1 / (1 - p1)) / (p0 / (1 - p0)), OR_CAP)
    return tabs, lifts


def odds_apply(p, orp):
    p = np.asarray(p, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        o = p / (1 - p) * orp
        out = o / (1 + o)
    return np.where(p <= 0, 0.0, np.where(p >= 1, 1.0, out))


def cell_score(tabs, lifts, test):
    """score_cell for every test row, vectorized; NaN where no level has a scorable cell."""
    n = len(test)
    rate, wn = np.full((4, n), np.nan), np.full((4, n), np.nan)
    ok, sc = np.zeros((4, n), bool), np.zeros((4, n), bool)
    for i, (level, keys) in enumerate(LEVELS):
        m = test[keys].join(tabs[level], on=keys)
        rate[i], wn[i] = m.rate.values, m.wn.values
        ok[i], sc[i] = m.ok.fillna(False).values.astype(bool), m.scorable.fillna(False).values.astype(bool)
    has = sc.any(axis=0)
    first = np.where(has, sc.argmax(axis=0), -1)
    base = np.full(n, np.nan)
    for i in range(4):
        sel = first == i
        if not sel.any():
            continue
        r, k = rate[i, sel], wn[i, sel]
        parent = np.full(sel.sum(), np.nan)
        for j in range(i + 1, 4):
            take = np.isnan(parent) & ok[j, sel]
            parent[take] = rate[j, sel][take]
        base[sel] = np.where(np.isnan(parent), r, (k * r + SHRINK_K * parent) / (k + SHRINK_K))
    reg = test.regime.values
    orp = np.ones(n)
    for cut in CUTS:
        if cut in AFTER_CAP_CUTS:
            continue
        f = np.array([lifts.get((cut, r), 1.0) for r in reg])
        orp *= np.where(test[cut].values.astype(bool), f, 1.0)
    orp = np.minimum(orp, OR_PRODUCT_CAP)
    for cut in AFTER_CAP_CUTS:
        f = np.array([lifts.get((cut, r), 1.0) for r in reg])
        orp *= np.where(test[cut].values.astype(bool), f, 1.0)
    p = np.minimum(odds_apply(np.nan_to_num(base, nan=0.5), orp), P_CAP)
    return np.where(has, p, np.nan)


def harness_folds(u, data_end):
    """(cutoff, h, train, test) of buy_eval: train = closed by D, test = closing in (D, next]."""
    for d, nxt in E.cutoffs(data_end):
        for h in E.HORIZONS:
            train, test = E.split(u, h, d, nxt)
            yield d, h, train, test


def live_folds(u, data_end):
    """(anchor, h, train, test) at the information a buyer has ON the anchor: train = closed by the anchor
    (anchor - h - 30 d), test = that anchor month's trades, for the anchor months the harness tests."""
    for h in E.HORIZONS:
        lab = R.closed(u, h, data_end)
        first = E.START - pd.Timedelta(days=h + 30)
        for a in sorted(lab.date[lab.date > first].unique()):
            a = pd.Timestamp(a)
            yield a, h, R.closed(u, h, a), lab[lab.date == a]


def harness_live_folds(u, data_end):
    """(anchor, h, train, test) of buy_eval.live_folds, the harness's LIVE protocol (anchors from 2023-01)."""
    for h in E.HORIZONS:
        for a, train, test in E.live_folds(u, h, data_end):
            yield a, h, train, test


def run_cells(u, data_end, half_life, folds=harness_folds):
    rows = []
    for d, h, train, test in folds(u, data_end):
        test = test[scorable(test)]
        if test.empty or train.empty:
            continue
        tabs, lifts = cell_fit(train, decay_weights(train.date, half_life))
        p = cell_score(tabs, lifts, test)
        keep = ~np.isnan(p)
        rows.append(pd.DataFrame({"horizon": h, "cutoff": d, "trade_id": test.trade_id.values[keep], "p": p[keep]}))
    return pd.concat(rows, ignore_index=True)


def check(u, data_end):
    """no decay -> must match preds_baseline (the rate tables are written at 4 decimals, so ~1e-4)."""
    mine = run_cells(u, data_end, None)
    base = E.load_preds("baseline")
    m = base.merge(mine, on=["horizon", "trade_id"], how="outer", suffixes=("_base", "_mine"), indicator=True)
    print(m._merge.value_counts().to_string())
    both = m[m._merge == "both"]
    d = (both.p_base - both.p_mine).abs()
    print(f"cutoff equal: {(pd.to_datetime(both.cutoff_base) == pd.to_datetime(both.cutoff_mine)).mean():.4f}")
    print(f"|p_base - p_mine|: max {d.max():.6f}, 99.9% {d.quantile(0.999):.6f}, share > 1e-3 {(d > 1e-3).mean():.5f}")


# ---------------------------------------------------------------- glm_logit

def static_design(u):
    """the columns that do not depend on the fold (float32), and their names."""
    cols = {}
    for e in ERAS:
        cols[f"era_{e}"] = (u.era == e).values
    for t in R.TIER_LABELS:
        cols[f"tier_{t}"] = (u.tier == t).values
    for li in R.LIQ_LABELS:
        cols[f"liq_{li}"] = (u.liq == li).values
    for c in CUTS:
        cols[c] = u[c].fillna(False).values.astype(bool)
    cols["log_price"] = u.log_price.values
    cols["log_days_since_sale"] = np.log1p(u.days_since_sale.clip(0, 180).fillna(180).values)
    for m in ("mom30", "mom90"):
        cols[m] = u[m].clip(-0.5, 1.0).fillna(0).values
        cols[f"{m}_blank"] = u[m].isna().values
    cols["band_z"] = u.band_z.clip(-3, 3).fillna(0).values
    cols["band_z_blank"] = u.band_z.isna().values
    names = list(cols)
    return np.column_stack([np.asarray(cols[k], np.float64) for k in names]).astype(np.float32), names


def breadth_design(u_rows, lo, hi):
    b = u_rows.breadth.clip(lo, hi).values
    cols = {"breadth": b}
    for k in KNOTS:
        cols[f"breadth_gt{k:.2f}"] = np.maximum(b - k, 0)
    bc = b - B_CENTER
    for t in R.TIER_LABELS:
        cols[f"breadth_x_tier_{t}"] = bc * (u_rows.tier == t).values
    for e in ERAS:
        cols[f"breadth_x_era_{e}"] = bc * (u_rows.era == e).values
    names = list(cols)
    return np.column_stack([cols[k] for k in names]).astype(np.float32), names


def run_logit(u, data_end, half_life, name, folds=harness_folds):
    xs, s_names = static_design(u)
    rows, notes = [], []
    t0 = time.time()
    for d, h, train, test in folds(u, data_end):
        train, test = train[scorable(train)], test[scorable(test)]
        if test.empty or train.win.nunique() < 2:
            continue
        lo, hi = train.breadth.min(), train.breadth.max()
        xb_tr, b_names = breadth_design(train, lo, hi)
        xb_te, _ = breadth_design(test, lo, hi)
        x_tr = np.hstack([xs[train.index.values], xb_tr])
        x_te = np.hstack([xs[test.index.values], xb_te])
        w = decay_weights(train.date, half_life)
        sc = StandardScaler().fit(x_tr, sample_weight=w)
        m = LogisticRegression(C=C_L2, max_iter=2000).fit(sc.transform(x_tr), train.win.values, sample_weight=w)
        p = m.predict_proba(sc.transform(x_te))[:, 1]
        rows.append(pd.DataFrame({"horizon": h, "cutoff": d, "trade_id": test.trade_id.values, "p": p}))
        coef = dict(zip(s_names + b_names, m.coef_[0] / sc.scale_))     # per raw unit
        notes.append({"model": name, "horizon": h, "cutoff": d, "n_train": len(train), "w_sum": w.sum(), "n_test": len(test),
                      "breadth_lo": lo, "breadth_hi": hi, "test_breadth_max": test.breadth.max(), "n_iter": int(m.n_iter_[0]),
                      "p_test_mean": p.mean(), **{f"b_{k}": v for k, v in coef.items()}})
    print(f"{name}: {len(notes)} fits ({time.time() - t0:.0f} s)", flush=True)
    return pd.concat(rows, ignore_index=True), pd.DataFrame(notes)


def live(u, data_end):
    """every variant and the no-decay cells (= v0.1 at that lag) at the buyer's information lag; prints the summary.
    Nothing is written to the shared preds: these are not the harness's folds."""
    preds = {"v01_live": run_cells(u, data_end, None, live_folds), "glm_decay": run_cells(u, data_end, HALF_LIFE_MONTHS, live_folds),
             "glm_logit": run_logit(u, data_end, None, "glm_logit", live_folds)[0],
             "glm_logit_decay": run_logit(u, data_end, HALF_LIFE_MONTHS, "glm_logit_decay", live_folds)[0]}
    out = []
    for h in E.HORIZONS:
        lab = R.closed(u, h, data_end)[["trade_id", "date", "win", "net", "eligible"]].set_index("trade_id")
        ref = preds["v01_live"][preds["v01_live"].horizon == h][["trade_id", "p"]].rename(columns={"p": "p_ref"})
        for name, pr in preds.items():
            m = pr[pr.horizon == h][["trade_id", "p"]].merge(ref, on="trade_id").join(lab, on="trade_id", how="inner")
            mt = pd.DataFrame([{"month": mo, **E.month_metrics(g, base_p=g.p_ref.values),
                                **{f"ref_top{k}": (g[g.eligible].nlargest(k, "p_ref").net.mean() if g.eligible.sum() >= k else np.nan) for k in E.TOPK}}
                               for mo, g in m.groupby("date")])
            for period, g in (("all", mt), ("2025+", mt[mt.month >= "2025-01-01"])):
                r = {"model": name, "horizon": h, "period": period, "months": len(g), "brier": g.brier.mean(),
                     "skill_vs_v01_live": 1 - g.brier.sum() / g.brier_base.sum(), "t": E.tstat(g.brier_base - g.brier), "auc": g.auc.mean()}
                for k in (50, 100):
                    dn = g[f"top{k}_net"] - g[f"ref_top{k}"]
                    r.update({f"top{k}_net": g[f"top{k}_net"].mean(), f"d_top{k}": dn.mean(), f"d_top{k}_t": E.tstat(dn)})
                out.append(r)
    s = pd.DataFrame(out)
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(s.round(4).to_string(index=False))
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "run", "live"])
    ap.add_argument("models", nargs="*", default=list(VARIANTS))
    ap.add_argument("--live", action="store_true", help="run: the harness's LIVE protocol (buy_eval.live_folds) -> preds_live_glm_*")
    a = ap.parse_args()
    u, data_end = E.load()
    if a.cmd == "check":
        check(u, data_end)
        return
    if a.cmd == "live":
        live(u, data_end).to_csv(E.DIR / "glm_live_lag.csv", index=False, float_format="%.4f")
        return
    folds, prefix = (harness_live_folds, "live_") if a.live else (harness_folds, "")
    for name in a.models:
        t0 = time.time()
        if name == "glm_decay":
            preds = run_cells(u, data_end, HALF_LIFE_MONTHS, folds)
        elif name in ("glm_logit", "glm_logit_decay"):
            preds, notes = run_logit(u, data_end, HALF_LIFE_MONTHS if name.endswith("_decay") else None, prefix + name, folds)
            notes.to_csv(E.DIR / f"{prefix}{name}_fits.csv", index=False, float_format="%.5g")
        else:
            sys.exit(f"unknown model {name}")
        path = E.save_preds(prefix + name, preds)
        print(f"wrote {path} ({len(preds):,} predictions, {preds.groupby('horizon').cutoff.nunique().to_dict()} cutoffs per horizon, "
              f"{time.time() - t0:.0f} s)", flush=True)


if __name__ == "__main__":
    main()
