#!/usr/bin/env python3
"""Buy list v0.2 (2026-10-24): the bake-off's pick, fitted for the nightly.

    analysis/.venv/bin/python analysis/buy_list_v02.py parity 2026-07-01   # history/buy_features.py == the panel's features at that anchor
    analysis/.venv/bin/python analysis/buy_list_v02.py fit [--panel P]     # -> history/buy_list_v02.json (+ out/buy_eval/preds_live_v02.csv.gz)
    analysis/.venv/bin/python analysis/buy_list_v02.py check               # the fitted walk-forward == the bake-off's live_blend_logit_revgap

The model (analysis/README.md "Buy-model bake-off", blend_logit_revgap), per horizon h in (90, 180):
  p_cells  the v0.1 cells (buy_list.score_cell over the monthly re-fit's tables) moved by the PSA 9 terms:
           odds x clip(exp(b_has9 * has9), 0.5, 2) x clip(exp(b_gap * rev_zc), 0.5, 2), the product clipped to
           [1/3, 3], p capped at 0.9; b_* from a ridge logistic of win on (has9, rev_zc) with the cells'
           in-sample logit as a fixed offset, over the trades closed by the fit date (cand_pills.fit_offset_logit)
  p_logit  an L2 logistic regression (C = 1, standardized) on era / tier / liquidity one-hots, the four cuts,
           log10 price, log1p(days since sale, cap 180), mom30 / mom90 clipped to [-0.5, 1] (+ blank flags),
           band_z clipped to [-3, 3] (+ blank flag), breadth clipped to the training months' range as a
           linear spline (knots 0.25, 0.40) and breadth - 0.25 x tier / x era (cand_glm.glm_logit)
  p        Platt: sigmoid(a z + c), z = (logit p_cells + logit p_logit) / 2 (logits clipped at 1e-4), a and c from
           a logistic of win on z over the blend's own LIVE out-of-sample predictions of every anchor month closed
           by the fit date (buy_eval.live_folds: each month scored by models fitted on what was closed by its
           anchor); raw blend under MIN_HISTORY trades (cand_blend)

`fit` runs that walk-forward (one cell-table fit, one logistic, one offset fit per anchor month from
buy_eval.START, both horizons), fits the final models at the panel's data end and writes every number the
nightly needs to history/buy_list_v02.json: the logistic's feature names, means, scales, coefficients,
intercept and breadth range; b_has9 / b_gap; Platt a / c and the history they rest on. The cell tables
themselves stay history/buy_list_rates.csv / buy_list_lifts.csv (the monthly re-fit writes both, with this).
history/buy_list.py scores v0.2 when the json exists, from history/buy_features.py: the panel's features
recomputed in the standard library at the data date; `parity` is the check that they are the same numbers.
"""
import argparse
import bisect
import json
import math
import statistics
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "history"))
sys.path.insert(0, str(ROOT / "analysis"))
import buy_eval as E  # noqa: E402
import buy_features as BF  # noqa: E402
import buy_list as BL  # noqa: E402
import buy_list_rates as R  # noqa: E402
import cand_glm as G  # noqa: E402  (the logistic's design: static_design / breadth_design)
import cand_pills as P  # noqa: E402  (fit_offset_logit / trade_or / group_quantiles)
from cand_calib import MIN_HISTORY, logit as blend_logit  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402

MODEL = "v0.2"
JSON_NAME = BL.V02_JSON              # next to the rate tables (history/ by default)
CUTS = G.CUTS                        # stale30, jp_heat, band_high, thin_history
TERMS = ["has9", "rev_zc"]           # the PSA 9 terms on the cells (cand_pills pills_revgap)
COMBO = ["regime", "era", "tier", "liq", *CUTS]
PLATT_C = 1e4                        # cand_blend: a near-unpenalized logistic of win on z
PREDS = "live_v02"                   # analysis/out/buy_eval/preds_live_v02.csv.gz: the walk-forward, `check` reads it

BASE_COLS = ["ref", "log_price", "mom30", "mom90", "mom180", "mom365", "vol30", "vol90", "vol365", "vol_surge", "days_since_sale",
             "n_sales_to_date", "dispersion", "bin_share90", "ebay_share90", "accel", "band_z", "age_years", "char_mom30", "char_n"]
PREP_COLS = ["breadth", "char_jp_mom30", "set_price_q", "set_lead_mom90"]
CAT_COLS = ["era", "tier", "liq", "regime"]
BOOL_COLS = ["stale30", "jp_heat", "band_high", "tail_lead", "thin_history", "eligible", "release_year", "busy_spike", "vol_surge_flag",
             "below_band", "dip", "chase_flat"]
AVOID_MAP = {"vol_surge_flag": "vol_surge"}


def stdlib_frame(t, assets_path=None, store=ROOT / "history"):
    t0 = time.time()
    rows = BF.features_at(t, assets=BF.load_assets(path=assets_path) if assets_path else None, store=store)
    df = pd.DataFrame(rows)
    for c in E.AVOID:
        df[c] = df.avoid.map(lambda a, c=c: AVOID_MAP.get(c, c) in a)
    print(f"stdlib: {len(df):,} English cards at {t} ({time.time() - t0:.0f} s)", flush=True)
    return df.set_index("asset_id")


def compare(name, a, b, cols, kind):
    """a = stdlib, b = reference, both indexed by asset_id over the same cards; prints per column, returns the bad count."""
    bad = 0
    for c in cols:
        x, y = a[c], b[c]
        if kind == "num":
            x, y = pd.to_numeric(x, errors="coerce").astype(float), pd.to_numeric(y, errors="coerce").astype(float)
            nan_mis = int((x.isna() != y.isna()).sum())
            both = x.notna() & y.notna()
            d = (x[both] - y[both]).abs()
            rel = (d / y[both].abs().clip(lower=1e-12))
            worst = float(rel.max()) if len(rel) else 0.0
            ok = nan_mis == 0 and worst < 1e-9
            print(f"  {name:6s} {c:16s} nan mismatches {nan_mis:5d}  max rel diff {worst:.2e}  {'ok' if ok else 'DIFF'}")
        else:
            xs = x.map(lambda v: None if (v is None or (isinstance(v, float) and math.isnan(v))) else (bool(v) if kind == "bool" else str(v)))
            ys = y.map(lambda v: None if (v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA) else (bool(v) if kind == "bool" else str(v)))
            mis = int((xs != ys).sum())
            ok = mis == 0
            print(f"  {name:6s} {c:16s} mismatches {mis:5d}  {'ok' if ok else 'DIFF'}" + ("" if ok else f"  e.g. {list(zip(xs[xs != ys].head(3), ys[xs != ys].head(3)))}"))
        bad += not ok
    return bad


def parity(t, assets_path=None, store=ROOT / "history"):
    t = date.fromisoformat(t)
    ts = pd.Timestamp(t)
    s = stdlib_frame(t, assets_path, store)
    u, _ = E.load()
    u = u[u.date == ts].set_index("asset_id")
    meta = json.load(open(E.DIR / "trades.meta.json"))
    panel = pd.concat([c[c.date == f"{t}"] for c in R.read_panel(R.OUT / meta["panel"], usecols=["asset_id", "date"] + BASE_COLS,
                                                                    chunksize=200_000)]).set_index("asset_id")
    pills = pd.read_pickle(E.DIR / "pills_features.pkl")
    pills = pills[pills.trade_id.str.endswith(f"|{t}")].copy()
    pills["asset_id"] = pills.trade_id.str.split("|").str[0]
    pills = pills.set_index("asset_id")
    pills["rev_zc"] = pills.rev_zc.fillna(0.0) * pills.has9.fillna(0.0)
    only_s, only_u = sorted(set(s.index) - set(u.index)), sorted(set(u.index) - set(s.index))
    print(f"universe: stdlib {len(s):,}, cache {len(u):,}, panel rows {len(panel):,}; only stdlib {len(only_s)}, only cache {len(only_u)}"
          + (f"  e.g. {only_s[:3]} / {only_u[:3]}" if only_s or only_u else ""))
    common = sorted(set(s.index) & set(u.index) & set(panel.index))
    bad = len(only_s) + len(only_u)
    bad += compare("panel", s.loc[common], panel.loc[common], BASE_COLS, "num")
    bad += compare("cache", s.loc[common], u.loc[common], PREP_COLS, "num")
    bad += compare("cache", s.loc[common], u.loc[common], CAT_COLS, "cat")
    bad += compare("cache", s.loc[common], u.loc[common], BOOL_COLS, "bool")
    pc = sorted(set(common) & set(pills.index))
    bad += compare("pills", s.loc[pc], pills.loc[pc], ["psa9_ref", "has9", "rev_zc"], "num")
    print(f"PARITY {'PASS' if bad == 0 else 'FAIL'}: {bad} column(s) or universe differences at {t}")
    return bad


# ---------------------------------------------------------------- trades + PSA 9 terms

def psa9_terms(u, store=ROOT / "history"):
    """has9 / rev_zc per trade, in place, by cand_pills.build_features' definitions (history/buy_features.psa9_terms
    is the nightly's): psa9_ref = the median of the up-to-3 newest clean PSA 9 sales in (t-180, t]; r9 = psa9_ref /
    ref; rev_zc = log(r9 / the median r9 of the anchor's trades of the same era and tier, >= 20 of them, else the
    era, else the anchor), clipped to +-1.5, 0 without a PSA 9 reference."""
    t0 = time.time()
    c9 = BF.load_sales(store, grades=(9.0,))[9.0]
    t_ord = u.date.map({d: d.toordinal() for d in u.date.unique()}).values
    ref9 = np.full(len(u), np.nan)
    br = bisect.bisect_right
    for i, (aid, t) in enumerate(zip(u.asset_id.values, t_ord)):
        c = c9.get(aid)
        if c:
            hi, lo = br(c[0], t), br(c[0], t - 180)
            if hi > lo:
                ref9[i] = statistics.median(c[1][max(lo, hi - 3):hi])
    u["psa9_ref"] = ref9
    u["r9"] = u.psa9_ref / u.ref
    u["has9"] = u.r9.notna().astype(float)
    _, med = P.group_quantiles(u, [["date", "era", "tier"], ["date", "era"], ["date"]])
    u["rev_zc"] = np.log(u.r9 / med).clip(-1.5, 1.5).fillna(0.0) * u.has9
    print(f"PSA 9 terms: {int(u.has9.sum()):,} of {len(u):,} trades with a PSA 9 reference ({time.time() - t0:.0f} s)", flush=True)
    return u


def load_trades(panel=None, assets=None, store=ROOT / "history", data_end=None):
    """(trades with the PSA 9 terms, data end, panel name): the panel's English trades (buy_eval.trades), or the
    bake-off's cache (analysis/out/buy_eval/trades.pkl) without a panel. data_end: default the panel's .meta.json."""
    if panel:
        data_end = data_end or R.panel_data_end(R.OUT / panel)
        if data_end is None:
            sys.exit(f"{R.OUT / panel}: no .meta.json next to it")
        u = E.trades(R.OUT / panel, assets).reset_index(drop=True)   # positional index: static_design rows = u.index
    else:
        u, cache_end = E.load()
        data_end = data_end or cache_end
        panel = json.load(open(E.DIR / "trades.meta.json"))["panel"]
    print(f"{len(u):,} English trades from {panel}, sales through {data_end.date()}", flush=True)
    return psa9_terms(u, store), data_end, panel


# ---------------------------------------------------------------- the three parts, fitted at one date

def cell_p(rates, lifts, h, df):
    """buy_list.score_cell's p for every row of df (buy_eval.run_baseline's guard: one of the four eras, a regime, a
    tier), NaN where no cell has MIN_CELL_N trades; one score per distinct (regime, era, tier, liq, cuts)."""
    ok = df.era.astype(str).isin(G.ERAS) & df.regime.map(lambda x: isinstance(x, str)) & df.tier.map(lambda x: isinstance(x, str))
    sub = df.loc[ok, COMBO]
    uniq = sub.drop_duplicates().reset_index(drop=True)
    ps = []
    for r in uniq.itertuples(index=False):
        s = BL.score_cell(rates, lifts, h, r[0], str(r[1]), r[2], r[3], [c for c, v in zip(CUTS, r[4:]) if bool(v)])
        ps.append(s["p"] if s else np.nan)
    uniq["p"] = ps
    out = pd.Series(np.nan, index=df.index)
    out[ok] = sub.merge(uniq, on=COMBO, how="left").p.values
    return out


def fit_terms(rates, lifts, h, train):
    """b_has9 / b_gap (cand_pills.run_preds): a ridge logistic of win on the PSA 9 terms with the cells' in-sample logit
    as a fixed offset, over the trades closed by the fit date; has9 only with >= MIN_FLAG_TRAIN such trades over >= 3
    months (rev_zc is continuous: always)."""
    tr = train[G.scorable(train)].copy()
    tr["p_is"] = cell_p(rates, lifts, h, tr)
    tr = tr.dropna(subset=["p_is"])
    X = tr[TERMS].astype(float).values
    usable = [True if c in P.CONTINUOUS else bool((X[:, j] > 0).sum() >= P.MIN_FLAG_TRAIN and tr.date[X[:, j] > 0].nunique() >= 3)
              for j, c in enumerate(TERMS)]
    betas = np.zeros(len(TERMS))
    idx = [j for j, ok in enumerate(usable) if ok]
    if idx:
        _, b = P.fit_offset_logit(X[:, idx], tr.win.values.astype(float), P.logit(tr.p_is.values))
        betas[idx] = b
    return betas, len(tr)


def fit_logit(u, xs, s_names, train):
    """cand_glm.run_logit's glm_logit at one date: L2 logistic (C = 1) on the standardized design, breadth clipped to
    the training range. -> dict with the scaler, the model and the numbers the nightly needs, or None (one class)."""
    train = train[G.scorable(train)]
    if train.win.nunique() < 2:
        return None
    lo, hi = train.breadth.min(), train.breadth.max()
    xb, b_names = G.breadth_design(train, lo, hi)
    x = np.hstack([xs[train.index.values], xb])
    w = np.ones(len(train))                                                 # glm_logit = no decay: unit weights, as run_logit passes them
    sc = StandardScaler().fit(x, sample_weight=w)
    m = LogisticRegression(C=G.C_L2, max_iter=2000).fit(sc.transform(x), train.win.values, sample_weight=w)
    return {"sc": sc, "m": m, "lo": float(lo), "hi": float(hi), "names": s_names + b_names, "n_train": len(train)}


def fit_at(u, xs, s_names, d, tmp, horizons=E.HORIZONS):
    """every part of v0.2 fitted on the trades closed by d: {"rates", "lifts", h: {"betas", "n_terms", "glm"}}."""
    cells, lifts, _, _ = R.fit(u, d)
    R.write_tables(cells, lifts, tmp)
    out = {"rates": BL.load_rates(Path(tmp) / "buy_list_rates.csv"), "lifts": BL.load_lifts(Path(tmp) / "buy_list_lifts.csv")}
    for h in horizons:
        train = R.closed(u, h, d)
        betas, n_terms = fit_terms(out["rates"], out["lifts"], h, train)
        out[h] = {"betas": betas, "n_terms": n_terms, "glm": fit_logit(u, xs, s_names, train)}
    return out


def r5(x):
    """x as the bake-off's preds files carried it (save_preds float_format %.5f): `check` replays that rounding."""
    return np.array([float(f"{v:.5f}") if not np.isnan(v) else np.nan for v in x])


def predict(fitted, h, xs, test, replay=False):
    """p_base, p_cells, p_logit, z for test's rows (index-aligned; NaN where a part cannot score). replay: each part
    through the 5-decimal files of the bake-off (live_baseline -> live_pills_revgap, live_glm_logit -> the blend)."""
    f = fitted[h]
    p_base = cell_p(fitted["rates"], fitted["lifts"], h, test).values
    pb = r5(p_base) if replay else p_base
    orp = P.trade_or(test[TERMS].astype(float).values, f["betas"], TERMS)
    p_cells = np.minimum(G.odds_apply(pb, orp), BL.P_CAP)
    p_cells[np.isnan(pb)] = np.nan
    p_logit = np.full(len(test), np.nan)
    g = f["glm"]
    s = G.scorable(test).values
    if g is not None and s.any():
        t = test[s]
        xb, _ = G.breadth_design(t, g["lo"], g["hi"])
        p_logit[s] = g["m"].predict_proba(g["sc"].transform(np.hstack([xs[t.index.values], xb])))[:, 1]
    if replay:
        p_cells, p_logit = r5(p_cells), r5(p_logit)
    z = 0.5 * blend_logit(p_logit) + 0.5 * blend_logit(p_cells)            # cand_blend: equal weights, glm first
    z[np.isnan(p_cells) | np.isnan(p_logit)] = np.nan
    return p_base, p_cells, p_logit, z


def platt(hist_z, hist_win):
    """(a, c) of sigmoid(a z + c), or None under MIN_HISTORY trades or one class (then the raw blend)."""
    if len(hist_z) < MIN_HISTORY or len(np.unique(hist_win)) < 2:
        return None
    lr = LogisticRegression(C=PLATT_C).fit(np.asarray(hist_z)[:, None], np.asarray(hist_win, float))
    return float(lr.coef_[0][0]), float(lr.intercept_[0])


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


# ---------------------------------------------------------------- walk-forward + final fit

def walk_forward(u, data_end):
    """buy_eval's LIVE protocol: every anchor month A from START whose trades closed by data_end, scored by the parts
    fitted on what was closed by A, then Platt on the blend's own earlier rows (anchor <= A - (h + 30) d). Two
    versions side by side: full precision (what `fit` calibrates on) and replay (the bake-off's file rounding, `check`)."""
    t0 = time.time()
    xs, s_names = G.static_design(u)
    labels = {h: R.closed(u, h, data_end) for h in E.HORIZONS}
    anchors = {h: {pd.Timestamp(a) for a in labels[h].date.unique() if pd.Timestamp(a) >= E.START} for h in E.HORIZONS}
    parts = []
    with tempfile.TemporaryDirectory() as tmp:
        for a in sorted(set().union(*anchors.values())):
            hs = [h for h in E.HORIZONS if a in anchors[h]]
            fitted = fit_at(u, xs, s_names, a, tmp, hs)
            for h in hs:
                test = labels[h][labels[h].date == a]
                full = predict(fitted, h, xs, test)
                rep = predict(fitted, h, xs, test, replay=True)
                df = pd.DataFrame({"horizon": h, "anchor": a, "trade_id": test.trade_id.values, "win": test.win.values,
                                   "net": test.net.values, "eligible": test.eligible.values,
                                   "p_base": full[0], "p_cells": full[1], "p_logit": full[2], "z": full[3],
                                   "p_cells_r5": rep[1], "p_logit_r5": rep[2], "z_r5": rep[3],
                                   "b_has9": fitted[h]["betas"][0], "b_gap": fitted[h]["betas"][1]})
                parts.append(df[df.z.notna()])
            print(f"walk-forward {a.date()} ({'/'.join(map(str, hs))} d) done ({time.time() - t0:.0f} s)", flush=True)
    wf = pd.concat(parts, ignore_index=True)
    out = []
    for h in E.HORIZONS:
        b = wf[wf.horizon == h].copy()
        b["raw"], b["p"], b["p_r5"] = sigmoid(b.z), np.nan, np.nan
        for a in sorted(b.anchor.unique()):
            test = b.anchor == a
            hist = b.anchor <= a - pd.Timedelta(days=h + 30)
            for zc, pc in (("z", "p"), ("z_r5", "p_r5")):
                ac = platt(b.loc[hist, zc].values, b.loc[hist, "win"].values)
                b.loc[test, pc] = sigmoid(b.loc[test, zc]) if ac is None else sigmoid(ac[0] * b.loc[test, zc] + ac[1])
        out.append(b)
    return pd.concat(out, ignore_index=True)


def summarize(wf):
    """per horizon over the walk-forward: months, trades, mean p vs won, Brier vs the cells alone, AUC, top-50 net vs the cells."""
    rows = {}
    for h, b in wf.groupby("horizon"):
        mt = []
        for a, g in b.groupby("anchor"):
            e = g[g.eligible.astype(bool)]
            top = lambda col: e.nlargest(50, col).net.mean() if len(e) >= 50 else np.nan   # noqa: E731
            mt.append({"n": len(g), "auc": E.auc(g.win.values, g.p.values), "top50": top("p"), "top50_base": top("p_base")})
        mt = pd.DataFrame(mt)
        y = b.win.values.astype(float)
        d = mt.top50 - mt.top50_base
        rows[str(h)] = {"months": int(b.anchor.nunique()), "trades": len(b), "first_anchor": f"{b.anchor.min().date()}",
                        "last_anchor": f"{b.anchor.max().date()}", "won": round(float(y.mean()), 4), "p_mean": round(float(b.p.mean()), 4),
                        "brier": round(float(((b.p - y) ** 2).mean()), 5), "brier_cells": round(float(((b.p_base - y) ** 2).mean()), 5),
                        "auc": round(float(mt.auc.mean()), 4), "d_top50_net_vs_cells": round(float(d.mean()), 4), "d_top50_t": round(float(E.tstat(d)), 2)}
    return rows


def fit(panel=None, assets=None, out_dir=ROOT / "history", store=ROOT / "history", data_end=None, preds_path=None):
    """the walk-forward, then every part at the panel's data end -> out_dir/buy_list_v02.json (+ the walk-forward's
    rows -> preds_path, default analysis/out/buy_eval/preds_live_v02.csv.gz, what `check` reads). -> (json dict, walk-forward)."""
    t0 = time.time()
    preds_path = Path(preds_path or E.DIR / f"preds_{PREDS}.csv.gz")
    u, data_end, panel = load_trades(panel, assets, store, data_end)
    wf = walk_forward(u, data_end)
    xs, s_names = G.static_design(u)
    with tempfile.TemporaryDirectory() as tmp:
        final = fit_at(u, xs, s_names, data_end, tmp)
    model = {"model": MODEL, "data_end": f"{data_end.date()}", "panel": panel,
             "fitted_at": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
             "blend_logit_eps": 1e-4, "offset_logit_eps": 1e-3, "term_or_clip": [P.OR_LO, P.OR_HI], "term_product_clip": [P.PROD_LO, P.PROD_HI],
             "breadth_center": G.B_CENTER, "horizons": {}}
    summary = summarize(wf)
    for h in E.HORIZONS:
        g = final[h]["glm"]
        if g is None:
            sys.exit(f"{h} d: one class in the training trades at {data_end.date()}; no logistic")
        b = wf[wf.horizon == h]
        ac = platt(b.z.values, b.win.values)                                # every walk-forward row is closed by data_end
        model["horizons"][str(h)] = {
            "logit": {"names": g["names"], "mean": g["sc"].mean_.tolist(), "scale": g["sc"].scale_.tolist(),
                      "coef": g["m"].coef_[0].tolist(), "intercept": float(g["m"].intercept_[0]),
                      "breadth_lo": g["lo"], "breadth_hi": g["hi"], "n_train": g["n_train"]},
            "psa9": {"b_has9": float(final[h]["betas"][0]), "b_gap": float(final[h]["betas"][1]), "n_train": final[h]["n_terms"]},
            "platt": None if ac is None else {"a": ac[0], "c": ac[1], "n": len(b), "months": int(b.anchor.nunique()),
                                              "first_anchor": f"{b.anchor.min().date()}", "last_anchor": f"{b.anchor.max().date()}"},
            "walk_forward": summary[str(h)]}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / JSON_NAME).write_text(json.dumps(model, indent=1) + "\n")
    preds_path.parent.mkdir(parents=True, exist_ok=True)
    wf.assign(cutoff=wf.anchor.dt.strftime("%Y-%m-%d")).drop(columns="anchor").to_csv(preds_path, index=False, float_format="%.8f")
    for h in E.HORIZONS:
        m = model["horizons"][str(h)]
        print(f"v0.2 {h} d: platt {m['platt'] and (round(m['platt']['a'], 3), round(m['platt']['c'], 3))}, b_has9 {m['psa9']['b_has9']:+.3f}, "
              f"b_gap {m['psa9']['b_gap']:+.3f}, logistic on {m['logit']['n_train']:,} trades; walk-forward {m['walk_forward']}")
    print(f"wrote {out_dir / JSON_NAME} and {preds_path} ({len(wf):,} rows) in {time.time() - t0:.0f} s")
    return model, wf


def closed_months_md(wf, months):
    """markdown: the walk-forward's rows of the months that just closed, i.e. v0.2 as a buyer had it on each anchor
    (models fitted on what was closed by then: out of sample, the honest check), next to the v0.1 cells."""
    out = ["| horizon | anchor month | trades | v0.2 predicted | v0.1 cells predicted | won | v0.2 AUC | cells AUC |\n|---|---|---|---|---|---|---|---|\n"]
    for h, m in sorted(months):
        g = wf[(wf.horizon == h) & (wf.anchor.dt.strftime("%Y-%m") == m)]
        if g.empty:
            continue
        y = g.win.values
        out.append(f"| {h} d | {m} | {len(g):,} | {100 * g.p.mean():.1f}% | {100 * g.p_base.mean():.1f}% | {100 * y.mean():.1f}% | "
                   f"{E.auc(y, g.p.values):.3f} | {E.auc(y, g.p_base.values):.3f} |\n")
    return "".join(out) if len(out) > 1 else "(no walk-forward rows for the closed months)\n"


def check(assets=None, store=ROOT / "history"):
    """the walk-forward of `fit` (replay columns) against the bake-off's files regenerated on the same cache, part by
    part; and the PSA 9 terms against cand_pills' features. Every gap must be the files' rounding (<= 5e-6)."""
    wf = pd.read_csv(E.DIR / f"preds_{PREDS}.csv.gz", dtype={"trade_id": str})
    bad = 0
    for col, ref in (("p_base", "live_baseline"), ("p_cells_r5", "live_pills_revgap"), ("p_logit_r5", "live_glm_logit"),
                     ("p_r5", "live_blend_logit_revgap")):
        b = E.load_preds(ref)
        m = wf[["horizon", "trade_id", col]].merge(b[["horizon", "trade_id", "p"]], on=["horizon", "trade_id"], how="right")
        d = (m[col] - m.p).abs()
        miss = int(m[col].isna().sum())
        ok = d.max() <= 5.01e-6 and (miss == 0 or ref == "live_glm_logit")      # the logistic scores trades the cells cannot; the blend has only both
        bad += not ok
        print(f"  {col:11s} vs preds_{ref}: {len(b):,} rows, {miss:,} not in the walk-forward, max |diff| {d.max():.2e}  {'ok' if ok else 'DIFF'}")
    u, _, _ = load_trades(None, assets, store)
    f = pd.read_pickle(P.FEATS).set_index("trade_id")
    x = u.set_index("trade_id")[TERMS].join(f[["has9", "rev_zc"]].fillna(0.0), rsuffix="_pills")
    x["rev_zc_pills"] = x.rev_zc_pills * x.has9_pills
    for c in TERMS:
        d = (x[c] - x[f"{c}_pills"]).abs().max()
        bad += not d <= 1e-12
        print(f"  {c:11s} vs cand_pills features: max |diff| {d:.2e}  {'ok' if d <= 1e-12 else 'DIFF'}")
    print(f"CHECK {'PASS' if bad == 0 else 'FAIL'}: the fit's walk-forward {'is' if bad == 0 else 'is NOT'} the bake-off's live_blend_logit_revgap")
    return bad


def score_parity(model_dir, t=None, assets=None, store=ROOT / "history"):
    """the nightly's scorer (history/buy_list.py score_v02 over history/buy_features.py at t, the json + tables as
    load_rates reads them) against the fitted models themselves (fit_at at the json's data end, sklearn, on the
    panel's rows at anchor t, Platt from the json), card by card. t: default the panel's newest anchor."""
    model = json.load(open(Path(model_dir) / JSON_NAME))
    u, data_end, panel = load_trades(None, assets, store, pd.Timestamp(model["data_end"]))
    t = pd.Timestamp(t) if t else u.date.max()
    xs, s_names = G.static_design(u)
    feats = {f["asset_id"]: f for f in BF.features_at(t.date(), assets=BF.load_assets(path=assets) if assets else None, store=store)}
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        fitted = fit_at(u, xs, s_names, data_end, tmp)
        test = u[u.date == t]
        for h in E.HORIZONS:
            p_base, p_cells, p_logit, z = predict(fitted, h, xs, test)
            pl = model["horizons"][str(h)]["platt"]
            p = sigmoid(pl["a"] * z + pl["c"]) if pl else sigmoid(z)
            ref = pd.DataFrame({"p_cells": p_cells, "p_logit": p_logit, "p": p}, index=test.asset_id.values).dropna()
            got = {}
            for aid in ref.index:
                f = feats[aid]
                sc = BL.score_cell(fitted["rates"], fitted["lifts"], h, f["regime"], f["era"], f["tier"], f["liq"], [k for k in BL.V02_CUTS if f[k]])
                got[aid] = BL.score_v02(model, h, f, sc)
            mine = pd.DataFrame({k: [got[a][k] for a in ref.index] for k in ("p_cells", "p_logit", "p")}, index=ref.index)
            for k in ("p_cells", "p_logit", "p"):
                d = (mine[k] - ref[k]).abs().max()
                ok = d < 1e-6
                bad += not ok
                print(f"  {h} d {k:8s}: {len(ref):,} cards at {t.date()}, max |nightly - fitted| {d:.2e}  {'ok' if ok else 'DIFF'}")
    print(f"SCORE PARITY {'PASS' if bad == 0 else 'FAIL'}: history/buy_list.py's v0.2 {'equals' if bad == 0 else 'differs from'} the fitted models at {t.date()}")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["parity", "fit", "check", "score-parity"])
    ap.add_argument("date", nargs="?", default="2026-07-01")
    ap.add_argument("--assets", default=None, help="the assets.csv the panel was built with (alt.xyz renames move set / year / subject)")
    ap.add_argument("--panel", default=None, help="fit: a loose panel under analysis/out/ (default: the bake-off's cache)")
    ap.add_argument("--out", default=str(ROOT / "history"), help="fit: where buy_list_v02.json goes (next to the rate tables)")
    ap.add_argument("--store", default=str(ROOT / "history"), help="the folder holding sales/ (the PSA 9 terms): another copy for a past panel")
    ap.add_argument("--data-end", default=None, help="fit: the data end (default: the panel's .meta.json, or the cache's)")
    a = ap.parse_args()
    if a.cmd == "parity":
        sys.exit(1 if parity(a.date, a.assets, a.store) else 0)
    if a.cmd == "fit":
        fit(a.panel, a.assets, a.out, a.store, pd.Timestamp(a.data_end) if a.data_end else None)
    elif a.cmd == "score-parity":
        sys.exit(1 if score_parity(a.out, a.date if a.date != "2026-07-01" else None, a.assets, a.store) else 0)
    else:
        sys.exit(1 if check(a.assets, a.store) else 0)


if __name__ == "__main__":
    main()
