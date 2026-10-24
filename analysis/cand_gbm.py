#!/usr/bin/env python3
"""Gradient-boosted candidates for the buy list's p_win (2026-10-23), walk-forward through analysis/buy_eval.py.

    analysis/.venv/bin/python analysis/cand_gbm.py --live   # ~5 min, 3 threads -> analysis/out/buy_eval/preds_live_gbm_*.csv.gz
    analysis/.venv/bin/python analysis/buy_eval.py evaluate live_baseline live_regime live_gbm_plain live_gbm_mono live_gbm_cal live_gbm_nomkt
    analysis/.venv/bin/python analysis/cand_gbm.py          # the cutoff protocol (E.split) -> preds_gbm_*.csv.gz, vs baseline / regime

Two protocols. --live (the headline): one fold per anchor month A (E.live_folds), train = the trades
closed by A (anchor <= A - (h + 30) d), exactly what a buyer on A knew; test = the trades anchored at
A. Without it, the monthly re-fit's cutoff protocol (E.split): train = closed by the cutoff D, test =
the trades closing in the next month, so a 180 d test trade is scored with labels whose exits
overlap its own by ~5 of 6 months (kind to recency). Both refit at every fold. Diagnostics carry the
protocol in their name (gbm_live_*.csv for --live).

The v0.1 list scores a card by its cell (regime x era x tier x liquidity) moved by four capped cuts.
A boosted tree on the panel's point-in-time features can find interactions the cells cannot (a
price x momentum x age shape, a liquidity effect that differs by era) and use breadth as a number
instead of three buckets. It is only worth it if it beats the cells out of time, so it runs on the
shared harness's folds and refits at EVERY fold (a fit takes seconds), so no fold is predicted by a
stale model. Result (analysis/out/buy_eval/gbm_notes.md): under live timing it does not beat the
cells at 180 d; at 90 d gbm_mono ranks better within a month (AUC +0.02) but its top picks earn no
more. The cutoff protocol's large gains are the overlapping labels.

Features: E.FEATURES (29 point-in-time numbers, breadth among them) + era / tier / liq as
categoricals + the four cut flags (stale30, jp_heat, band_high, thin_history) as 0/1. Never the
labels (mny*, exit_flag*), never anything dated after the anchor. NaN is left to the trees.

Fixed hyperparameters, chosen before any fold was scored (no search against test): sklearn
HistGradientBoostingClassifier, learning_rate 0.05, max_iter 400, max_leaf_nodes 31,
min_samples_leaf 300, l2_regularization 1.0. Early stopping is time-ordered: the LAST 15% of the
training anchor months (at least one) are the validation slice; a model fitted on the earlier 85%
stops after 20 rounds without a better validation log loss, the best round count k is read off
validation_score_, and the model that predicts the test month is refitted on ALL training months
with k rounds (the newest months are the ones closest to the test month in a market that turns).

Variants (fixed up front):
  gbm_plain   the above
  gbm_mono    the same with monotone constraints where earlier research gives the sign (MONO below;
              analysis/README.md "Pump autopsy", "Buy list v0.1"): volume surge down, own-band z and
              band_high up, price up (fee schedule), stale30 up, thin_history down, breadth up,
              the set's chase cards' 90 d run up (chase_flat avoid flag)
  gbm_cal     gbm_plain's test p mapped through isotonic regression fitted on the validation months
              only: the 85% model's predictions there (at round k) against their labels
  gbm_nomkt   diagnostic, not a candidate: gbm_plain without the month-level features (breadth,
              mkt_mom30, and rel_mom30 = mom30 - mkt_mom30; mkt_mom30 turned out to be 0 in every
              month of the panel, so breadth is the only month-level feature). It cannot know the market, so its
              level is wrong by design; its within-month AUC and top-k say how much of the ranking
              comes from the cards rather than the month

Also writes analysis/out/buy_eval/gbm_folds.csv (per fold and horizon: train / validation sizes,
rounds), gbm_importance.csv (final fold: split gain of the full model, permutation importance of the
85% model on its validation months, shuffled across and within months) and gbm_month_check.csv
(is it learning the month: between-month share of the variance of p, how the monthly mean p tracks
the monthly win rate, split thresholds on the month-level features vs training months).
"""
import os

os.environ.setdefault("OMP_NUM_THREADS", "3")     # shared machine: three threads for the trees

import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: E402
from sklearn.isotonic import IsotonicRegression  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buy_eval as E  # noqa: E402
import buy_list_rates as rates_mod  # noqa: E402

PARAMS = dict(learning_rate=0.05, max_iter=400, max_leaf_nodes=31, min_samples_leaf=300, l2_regularization=1.0, random_state=0)
N_ITER_NO_CHANGE = 20
VAL_SHARE = 0.15
CATS = {"era": ["wotc", "vintage", "modern", "current"], "tier": rates_mod.TIER_LABELS, "liq": rates_mod.LIQ_LABELS}
FLAGS = ["stale30", "jp_heat", "band_high", "thin_history"]
COLS = E.FEATURES + list(CATS) + FLAGS
MONTH_LEVEL = ["breadth", "mkt_mom30", "rel_mom30"]
COLS_NOMKT = [c for c in COLS if c not in MONTH_LEVEL]
MONO = {"vol_surge": -1, "band_z": 1, "band_high": 1, "log_price": 1, "ref": 1, "stale30": 1, "thin_history": -1,
        "breadth": 1, "set_lead_mom90": 1}
FITS = {"gbm_plain": (COLS, None), "gbm_mono": (COLS, MONO), "gbm_nomkt": (COLS_NOMKT, None)}
VARIANTS = ["gbm_plain", "gbm_mono", "gbm_cal", "gbm_nomkt"]
EPS = 1e-4


def design(u):
    """the model matrix for every trade (index = u's): numbers as they are, categoricals with fixed levels, flags 0/1."""
    X = u[E.FEATURES].astype(float)
    for c, levels in CATS.items():
        X[c] = pd.Categorical(u[c].astype(object), categories=levels)
    for c in FLAGS:
        X[c] = u[c].astype(float)
    return X[COLS]


def fit_variant(X, train, cols, mono):
    """-> dict(full=model on all training months with k rounds, early=the 85% model, k, val index, p_val at k)."""
    months = np.sort(train.date.unique())
    nv = max(1, int(round(VAL_SHARE * len(months))))
    vmask = (train.date >= months[-nv]).values
    tr_idx, va_idx = train.index[~vmask], train.index[vmask]
    y = train.win.astype(int)
    early = HistGradientBoostingClassifier(**PARAMS, early_stopping=True, n_iter_no_change=N_ITER_NO_CHANGE, scoring="loss",
                                           monotonic_cst=mono)
    early.fit(X.loc[tr_idx, cols], y.loc[tr_idx], X_val=X.loc[va_idx, cols], y_val=y.loc[va_idx])
    k = int(np.argmax(early.validation_score_))      # entry 0 = before the first round
    k = max(k, 1)
    p_val = None
    for i, pv in enumerate(early.staged_predict_proba(X.loc[va_idx, cols]), start=1):
        if i == k:
            p_val = pv[:, 1]
            break
    full = HistGradientBoostingClassifier(**{**PARAMS, "max_iter": k}, early_stopping=False, monotonic_cst=mono)
    full.fit(X.loc[train.index, cols], y)
    return {"full": full, "early": early, "k": k, "rounds_run": int(early.n_iter_), "val_idx": va_idx, "p_val": p_val,
            "val_months": nv, "train_months": len(months), "cols": cols}


def split_gain(model, cols):
    """total split gain and the distinct numeric thresholds per feature over every tree of a fitted HGB (in `cols` order).
    The trees index features after sklearn's preprocessor, which puts the categoricals first."""
    is_cat = np.asarray(model.is_categorical_, bool) if model.is_categorical_ is not None else np.zeros(len(cols), bool)
    order = [i for i in range(len(cols)) if is_cat[i]] + [i for i in range(len(cols)) if not is_cat[i]]   # tree index -> cols index
    gain = np.zeros(len(cols))
    thr = {c: set() for c in cols}
    for it in model._predictors:
        for tree in it:
            nd = tree.nodes
            inner = nd[nd["is_leaf"] == 0]
            for f, g, t, cat in zip(inner["feature_idx"], inner["gain"], inner["num_threshold"], inner["is_categorical"]):
                gain[order[f]] += g
                if not cat:
                    thr[cols[order[f]]].add(float(t))
    return gain, thr


def mean_auc_by_month(y, p, months):
    return np.nanmean([E.auc(y[months == m], p[months == m]) for m in np.unique(months)])


def logloss(y, p):
    p = np.clip(p, EPS, 1 - EPS)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def permutation_importance(model, Xv, yv, months, cols, repeats=3, seed=0):
    """log-loss rise and within-month AUC drop when a feature is shuffled across the validation months, and within each month."""
    rng = np.random.default_rng(seed)
    base_p = model.predict_proba(Xv)[:, 1]
    base_ll, base_auc = logloss(yv, base_p), mean_auc_by_month(yv, base_p, months)
    groups = [np.flatnonzero(months == m) for m in np.unique(months)]
    rows = []
    for c in cols:
        out = {"feature": c, "perm_ll_across": 0.0, "perm_auc_across": 0.0, "perm_ll_within": 0.0, "perm_auc_within": 0.0}
        for _ in range(repeats):
            for mode in ("across", "within"):
                Xp = Xv.copy()
                vals = Xv[c].to_numpy(copy=True)
                if mode == "across":
                    vals = vals[rng.permutation(len(vals))]
                else:
                    for g in groups:
                        vals[g] = vals[g[rng.permutation(len(g))]]
                Xp[c] = pd.Categorical(vals, categories=Xv[c].cat.categories) if isinstance(Xv[c].dtype, pd.CategoricalDtype) else vals
                p = model.predict_proba(Xp)[:, 1]
                out[f"perm_ll_{mode}"] += (logloss(yv, p) - base_ll) / repeats
                out[f"perm_auc_{mode}"] += (base_auc - mean_auc_by_month(yv, p, months)) / repeats
        rows.append(out)
    return pd.DataFrame(rows), base_ll, base_auc


def importance(X, train, fit, h, d):
    cols = fit["cols"]
    gain, thr = split_gain(fit["full"], cols)
    va = fit["val_idx"]
    perm, ll, au = permutation_importance(fit["early"], X.loc[va, cols], train.loc[va, "win"].astype(float).values,
                                          train.loc[va, "date"].values, cols)
    imp = pd.DataFrame({"feature": cols, "gain": gain, "gain_share": gain / gain.sum(), "n_thresholds": [len(thr[c]) for c in cols]})
    imp = imp.merge(perm, on="feature")
    imp.insert(0, "horizon", h)
    imp.insert(1, "cutoff", f"{d.date()}")
    # month memorising: the distinct breadth / mkt_mom30 values in training are one per month; how many month gaps do the splits cut?
    notes = {"horizon": h, "cutoff": f"{d.date()}", "val_logloss": ll, "val_auc_within": au, "train_months": fit["train_months"], "k": fit["k"]}
    for c in ("breadth", "mkt_mom30"):
        if c in cols:
            mv = np.sort(train.groupby("date")[c].first().dropna().unique())
            cuts = np.unique(np.searchsorted(mv, sorted(thr[c]), side="right"))
            notes[f"{c}_month_values"] = len(mv)
            notes[f"{c}_gaps_cut"] = int(((cuts > 0) & (cuts < len(mv))).sum())
    # in-sample: how well the full model's monthly mean p reproduces the training months' win rates
    p_tr = fit["full"].predict_proba(X.loc[train.index, cols])[:, 1]
    g = pd.DataFrame({"m": train.date.values, "p": p_tr, "y": train.win.values.astype(float)}).groupby("m").mean()
    notes["train_month_r2"] = float(1 - ((g.y - g.p) ** 2).sum() / ((g.y - g.y.mean()) ** 2).sum())
    notes["train_month_mae"] = float((g.y - g.p).abs().mean())
    return imp, notes


def month_check(u, data_end, preds, pre=""):
    """per model and horizon over the test months (trades the baseline scored): between-month share of var(p),
    correlation of the monthly mean p with the monthly win rate, mean |won - predicted| by month."""
    base, reg = E.load_preds(pre + "baseline"), E.load_preds(pre + "regime")
    allp = {pre + "baseline": base, pre + "regime": reg, **preds}
    rows = []
    for h in E.HORIZONS:
        lab = rates_mod.closed(u, h, data_end)[["trade_id", "date", "win"]].set_index("trade_id")
        keep = set(base.trade_id[base.horizon == h])
        for name, pr in allp.items():
            m = pr[(pr.horizon == h) & pr.trade_id.isin(keep)].join(lab, on="trade_id", how="inner")
            if m.empty:
                continue
            g = m.groupby("date").agg(p=("p", "mean"), y=("win", "mean"), n=("p", "size"))
            between = (m.groupby("date").p.transform("mean") - m.p.mean()).pow(2).mean() / m.p.var(ddof=0)
            for period, gg in (("all", g), ("2025+", g[g.index >= "2025-01-01"])):
                rows.append({"model": name, "horizon": h, "period": period, "months": len(gg), "between_month_var_share": between,
                             "corr_month_p_win": float(np.corrcoef(gg.p, gg.y)[0, 1]), "mean_gap_won_minus_p": float((gg.y - gg.p).mean()),
                             "mean_abs_gap": float((gg.y - gg.p).abs().mean())})
    return pd.DataFrame(rows)


def folds_of(u, data_end, live):
    """(h, fold date, train, test): the cutoff protocol (E.cutoffs + E.split) or the live one (E.live_folds)."""
    if live:
        for h in E.HORIZONS:
            for a, train, test in E.live_folds(u, h, data_end):
                yield h, a, train, test
    else:
        for d, nxt in E.cutoffs(data_end):
            for h in E.HORIZONS:
                train, test = E.split(u, h, d, nxt)
                yield h, d, train, test


def main():
    live = "--live" in sys.argv[1:]
    pre = "live_" if live else ""
    t0 = time.time()
    u, data_end = E.load()
    X = design(u)
    out = {v: [] for v in VARIANTS}
    folds, last = [], {}
    for h, d, train, test in folds_of(u, data_end, live):
        if test.empty:
            continue
        fits = {name: fit_variant(X, train, cols, mono) for name, (cols, mono) in FITS.items()}
        for name, f in fits.items():
            out[name].append(pd.DataFrame({"horizon": h, "cutoff": d, "trade_id": test.trade_id.values,
                                           "p": f["full"].predict_proba(X.loc[test.index, f["cols"]])[:, 1]}))
        f = fits["gbm_plain"]
        iso = IsotonicRegression(out_of_bounds="clip", y_min=EPS, y_max=1 - EPS).fit(f["p_val"], train.loc[f["val_idx"], "win"].astype(float))
        out["gbm_cal"].append(pd.DataFrame({"horizon": h, "cutoff": d, "trade_id": test.trade_id.values, "p": iso.predict(out["gbm_plain"][-1].p.values)}))
        folds.append({"cutoff": f"{d.date()}", "horizon": h, "train": len(train), "train_win": train.win.mean(), "train_months": f["train_months"],
                      "val_months": f["val_months"], "val": len(f["val_idx"]), "val_win": train.loc[f["val_idx"], "win"].mean(), "test": len(test),
                      **{f"k_{n}": fits[n]["k"] for n in FITS}, **{f"rounds_run_{n}": fits[n]["rounds_run"] for n in FITS}})
        last[h] = (d, train, fits)
        print(f"{h} d fold {d.date()} done ({time.time() - t0:.0f} s)", flush=True)
    for name, parts in out.items():
        path = E.save_preds(pre + name, pd.concat(parts, ignore_index=True))
        print(f"wrote {path} ({sum(len(p) for p in parts):,} predictions)")
    fo = pd.DataFrame(folds)
    fo.to_csv(E.DIR / f"gbm_{pre}folds.csv", index=False, float_format="%.4f")
    print(fo.groupby("horizon")[[c for c in fo.columns if c.startswith("k_")]].describe().T.to_string())

    imps, notes = [], []
    for h, (d, train, fits) in last.items():
        for name in ("gbm_plain", "gbm_mono"):
            imp, nt = importance(X, train, fits[name], h, d)
            imp.insert(0, "model", pre + name)
            nt["model"] = pre + name
            imps.append(imp)
            notes.append(nt)
    imp = pd.concat(imps, ignore_index=True)
    imp.to_csv(E.DIR / f"gbm_{pre}importance.csv", index=False, float_format="%.5f")
    for (name, h), g in imp.groupby(["model", "horizon"]):
        print(f"\n{name} {h} d, final fold {g.cutoff.iloc[0]}: split gain share, permutation (85% model on its validation months)")
        print(g.sort_values("gain_share", ascending=False).head(15)[["feature", "gain_share", "n_thresholds", "perm_ll_across", "perm_ll_within",
                                                                       "perm_auc_across", "perm_auc_within"]].to_string(index=False))
    print("\n" + pd.DataFrame(notes).to_string(index=False))

    if not (E.DIR / f"preds_{pre}baseline.csv.gz").exists():
        print(f"\npreds_{pre}baseline.csv.gz missing: month check skipped")
        return
    preds = {pre + v: pd.concat(out[v], ignore_index=True)[["horizon", "trade_id", "p"]] for v in VARIANTS}
    mc = month_check(u, data_end, preds, pre)
    pd.concat([mc, pd.DataFrame(notes).assign(kind="final_fold")], ignore_index=True).to_csv(E.DIR / f"gbm_{pre}month_check.csv", index=False, float_format="%.4f")
    print("\n" + mc.to_string(index=False))
    print(f"\ndone in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
