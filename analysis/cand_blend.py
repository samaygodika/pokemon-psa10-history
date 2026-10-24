#!/usr/bin/env python3
"""Blends ("soups") of buy-model candidates (2026-10-23), walk-forward through analysis/buy_eval.py.

    analysis/.venv/bin/python analysis/cand_blend.py NAME MODEL MODEL [MODEL ...] [--weights 0.5,0.25,0.25]

NAME's p = the weighted mean of the models' log-odds on every trade all of them scored (equal
weights by default; pass live_* models with --live), then calibrated walk-forward the way calib_platt is (cand_calib.py: a logistic
of the label on the blend's log-odds, fitted at each cutoff on the blend's own out-of-sample
predictions of the earlier folds, raw blend under MIN_HISTORY trades). Writes
analysis/out/buy_eval/preds_blend_<NAME>.csv.gz and preds_blendraw_<NAME>.csv.gz (before the
calibration step). No weight is fitted on test folds: equal weights, or weights given up front.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buy_eval as E  # noqa: E402
import buy_list_rates as rates_mod  # noqa: E402
from cand_calib import MIN_HISTORY, logit  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("models", nargs="+")
    ap.add_argument("--weights", default=None)
    ap.add_argument("--live", action="store_true", help="live_* inputs; the calibration history = anchors closed by the test anchor")
    a = ap.parse_args()
    w = np.array([float(x) for x in a.weights.split(",")]) if a.weights else np.ones(len(a.models))
    w = w / w.sum()
    u, data_end = E.load()
    preds = [E.load_preds(m).rename(columns={"p": f"p{i}"}) for i, m in enumerate(a.models)]
    m = preds[0]
    for i, p in enumerate(preds[1:], 1):
        m = m.merge(p[["horizon", "trade_id", f"p{i}"]], on=["horizon", "trade_id"])
    m["z"] = sum(w[i] * logit(m[f"p{i}"].values) for i in range(len(a.models)))
    m["raw"] = 1 / (1 + np.exp(-m.z))
    out = []
    for h in E.HORIZONS:
        lab = rates_mod.closed(u, h, data_end)[["trade_id", "win"]].set_index("trade_id")
        b = m[m.horizon == h].join(lab, on="trade_id", how="inner")
        b["cutoff"] = pd.to_datetime(b.cutoff)
        for d in sorted(b.cutoff.unique()):
            test = b[b.cutoff == d]
            hist = b[b.cutoff <= d - pd.Timedelta(days=h + 30)] if a.live else b[b.cutoff < d]   # live: labels known at the anchor
            if len(hist) < MIN_HISTORY or hist.win.nunique() < 2:
                p = test.raw.values
            else:
                lr = LogisticRegression(C=1e4).fit(hist.z.values[:, None], hist.win.values.astype(float))
                p = lr.predict_proba(test.z.values[:, None])[:, 1]
            out.append(pd.DataFrame({"horizon": h, "cutoff": d, "trade_id": test.trade_id.values, "p": p, "raw": test.raw.values}))
    out = pd.concat(out, ignore_index=True)
    pre = "live_" if a.live else ""
    print(E.save_preds(f"{pre}blend_{a.name}", out))
    print(E.save_preds(f"{pre}blendraw_{a.name}", out.drop(columns="p").rename(columns={"raw": "p"})))
    print(f"{a.name}: {len(out):,} predictions from {', '.join(f'{x} x{y:.2f}' for x, y in zip(a.models, w))}")


if __name__ == "__main__":
    main()
