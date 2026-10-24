#!/usr/bin/env python3
"""Calibration candidates on top of the v0.1 cell model (2026-10-23), walk-forward through analysis/buy_eval.py.

    analysis/.venv/bin/python analysis/cand_calib.py      # -> analysis/out/buy_eval/preds_calib_*.csv.gz

The replay of the monthly re-fit found the cell model's order right and its scale stretched:
listed trades scored 0.6-0.7 at 180 d won 45.5%, scored 0.2-0.3 won 36.8%. A calibrator maps the
model's p to the win rate it has actually delivered. Walk-forward and leak-free: at cutoff D the
calibrator is fitted on the baseline's OWN out-of-sample predictions of the earlier folds' test
trades (every one of them closed by D, so its label was known at D), then applied to fold D's test
trades. Under MIN_HISTORY trades of history the raw p is kept.

  calib_shrink   p' = r + lam (p - r), r = the regime's own rate for the trade, lam by least squares
                 on the history (clipped to [0, 1.5]): how far the cells + cuts should move off the market
  calib_platt    logistic regression of the label on logit(p)
  calib_platt2   logistic regression on logit(p) and logit(r): the cell model and the market, reweighted
  calib_iso      isotonic regression of the label on p (monotone, piecewise flat)
  calib_shrink12 calib_shrink on the last 12 history months only (adapts faster, noisier)

--live (the honest protocol, buy_eval.live_folds): on live_baseline / live_regime, one fold per anchor
month A, and the history is only the anchors whose labels were known at A (anchor <= A - (h + 30) d),
-> preds_live_calib_*. Without --live the history is every earlier fold (the re-fit check's timing).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buy_eval as E  # noqa: E402
import buy_list_rates as rates_mod  # noqa: E402

MIN_HISTORY = 3000
EPS = 1e-4


def logit(p):
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def fit_apply(kind, hist, test):
    """calibrated p for test from history rows (p, r, win)."""
    p, r, y = hist.p.values, hist.r.values, hist.win.values.astype(float)
    if kind.startswith("calib_shrink"):
        d = p - r
        lam = float(np.clip(((y - r) * d).sum() / (d * d).sum(), 0, 1.5)) if (d * d).sum() > 0 else 1.0
        return np.clip(test.r.values + lam * (test.p.values - test.r.values), EPS, 1 - EPS), lam
    if kind == "calib_platt":
        m = LogisticRegression(C=1e4).fit(logit(p)[:, None], y)
        return m.predict_proba(logit(test.p.values)[:, None])[:, 1], float(m.coef_[0][0])
    if kind == "calib_platt2":
        m = LogisticRegression(C=1e4).fit(np.c_[logit(p), logit(r)], y)
        return m.predict_proba(np.c_[logit(test.p.values), logit(test.r.values)])[:, 1], float(m.coef_[0][0])
    if kind == "calib_iso":
        m = IsotonicRegression(out_of_bounds="clip", y_min=EPS, y_max=1 - EPS).fit(p, y)
        return m.predict(test.p.values), np.nan
    raise ValueError(kind)


def main():
    live = "--live" in sys.argv[1:]
    pre = "live_" if live else ""
    u, data_end = E.load()
    base, reg = E.load_preds(pre + "baseline"), E.load_preds(pre + "regime")
    kinds = ["calib_shrink", "calib_platt", "calib_platt2", "calib_iso", "calib_shrink12"]
    out = {k: [] for k in kinds}
    for h in E.HORIZONS:
        lab = rates_mod.closed(u, h, data_end)[["trade_id", "date", "win"]].set_index("trade_id")
        b = base[base.horizon == h].merge(reg[reg.horizon == h][["trade_id", "p"]].rename(columns={"p": "r"}), on="trade_id", how="left")
        b["r"] = b.r.fillna(b.p)
        b = b.join(lab, on="trade_id", how="inner")
        b["cutoff"] = pd.to_datetime(b.cutoff)
        for d in sorted(b.cutoff.unique()):
            test = b[b.cutoff == d]
            # earlier folds' test trades: all closed by d; live: only those closed by the anchor d itself
            hist_all = b[b.date <= pd.Timestamp(d) - pd.Timedelta(days=h + 30)] if live else b[b.cutoff < d]
            for k in kinds:
                hist = hist_all
                if k == "calib_shrink12":
                    hist = hist_all[hist_all.date >= hist_all.date.max() - pd.DateOffset(months=11)] if len(hist_all) else hist_all
                if len(hist) < MIN_HISTORY or hist.win.nunique() < 2:
                    p2, par = test.p.values, np.nan
                else:
                    p2, par = fit_apply("calib_shrink" if k == "calib_shrink12" else k, hist, test)
                out[k].append(pd.DataFrame({"horizon": h, "cutoff": d, "trade_id": test.trade_id.values, "p": p2}))
                if k in ("calib_shrink", "calib_platt2") and h == 180 and pd.Timestamp(d).month == 1:
                    print(f"{k} {h} d cutoff {pd.Timestamp(d).date()}: history {len(hist):,} trades, parameter {par:.3f}", flush=True)
    for k, parts in out.items():
        path = E.save_preds(pre + k, pd.concat(parts, ignore_index=True))
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
