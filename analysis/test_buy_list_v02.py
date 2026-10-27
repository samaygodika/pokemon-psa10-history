#!/usr/bin/env python3
"""Checks for analysis/buy_list_v02.py (2026-10-26): the nightly's stdlib design row (history/buy_list.py
logit_inputs) is cand_glm's design matrix, the json the fit exports reproduces sklearn's own predictions
through history/buy_list.py score_v02, the 5-decimal replay and the Platt guard.

    analysis/.venv/bin/python analysis/test_buy_list_v02.py
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buy_list_v02 as V  # noqa: E402  (puts history/ on the path)
import buy_list as BL  # noqa: E402
import cand_glm as G  # noqa: E402

RNG = np.random.default_rng(7)


def synthetic(n=600):
    """trades with every input the logistic reads, blanks included; win leans on breadth and price."""
    eras, tiers, liqs = list(G.ERAS), ["<50", "50-150", "150-500", "500-2k", "2k+"], ["0", "1", "2-3", "4-7", "8+"]
    u = pd.DataFrame({
        "era": RNG.choice(eras, n), "tier": RNG.choice(tiers, n), "liq": RNG.choice(liqs, n), "regime": "warm",
        "stale30": RNG.random(n) < 0.3, "jp_heat": RNG.random(n) < 0.1, "band_high": RNG.random(n) < 0.2, "thin_history": RNG.random(n) < 0.1,
        "log_price": RNG.uniform(1, 4, n), "days_since_sale": np.where(RNG.random(n) < 0.1, np.nan, RNG.uniform(0, 400, n)),
        "mom30": np.where(RNG.random(n) < 0.2, np.nan, RNG.normal(0.05, 0.6, n)), "mom90": np.where(RNG.random(n) < 0.2, np.nan, RNG.normal(0.1, 0.8, n)),
        "band_z": np.where(RNG.random(n) < 0.15, np.nan, RNG.normal(0, 2, n)), "breadth": RNG.choice([0.15, 0.22, 0.31, 0.38, 0.47, 0.55], n),
    })
    u["win"] = RNG.random(n) < 1 / (1 + np.exp(-(-1.5 + 3 * (u.breadth - 0.3) + 0.3 * (u.log_price - 2.5))))
    return u


def feat_of(r):
    return {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()}


def test_design_row_is_cand_glms():
    u = synthetic()
    lo, hi = 0.20, 0.45
    xs, s_names = G.static_design(u)
    xb, b_names = G.breadth_design(u, lo, hi)
    x = np.hstack([xs, xb])
    names = s_names + b_names
    for i, r in u.iterrows():
        row = BL.logit_inputs(names, feat_of(r.to_dict()), lo, hi, G.B_CENTER)
        assert np.allclose(row, x[i], atol=1e-6, rtol=1e-6), [(n, a, b) for n, a, b in zip(names, row, x[i]) if abs(a - b) > 1e-6][:3]


def test_exported_logistic_reproduces_sklearn():
    u = synthetic()
    xs, s_names = G.static_design(u)
    g = V.fit_logit(u, xs, s_names, u)
    lg = {"names": g["names"], "mean": g["sc"].mean_.tolist(), "scale": g["sc"].scale_.tolist(), "coef": g["m"].coef_[0].tolist(),
          "intercept": float(g["m"].intercept_[0]), "breadth_lo": g["lo"], "breadth_hi": g["hi"]}
    model = {"blend_logit_eps": 1e-4, "term_or_clip": [0.5, 2.0], "term_product_clip": [1 / 3, 3.0], "breadth_center": G.B_CENTER,
             "horizons": {"180": {"logit": lg, "psa9": {"b_has9": 0.0, "b_gap": 0.0}, "platt": None}}}
    xb, _ = G.breadth_design(u, g["lo"], g["hi"])
    ref = g["m"].predict_proba(g["sc"].transform(np.hstack([xs, xb])))[:, 1]
    s = {"p": 0.3, "lo": 0.2, "hi": 0.4, "win_net": 1.0, "loss_net": -0.5}
    for i, r in u.iterrows():
        v = BL.score_v02(model, 180, feat_of(r.to_dict()) | {"has9": 0.0, "rev_zc": 0.0}, s)
        assert abs(v["p_logit"] - ref[i]) < 1e-6, (i, v["p_logit"], ref[i])
        z = 0.5 * V.blend_logit(np.array([v["p_logit"]]))[0] + 0.5 * V.blend_logit(np.array([0.3]))[0]
        assert abs(v["p"] - 1 / (1 + math.exp(-z))) < 1e-12, "no Platt pair: the raw blend"


def test_replay_rounding_and_platt_guard():
    assert list(V.r5(np.array([0.123456, 0.5, np.nan]))[:2]) == [0.12346, 0.5] and np.isnan(V.r5(np.array([np.nan]))[0])
    assert V.platt(np.zeros(10), np.r_[np.zeros(5), np.ones(5)]) is None, "under MIN_HISTORY trades: the raw blend"
    z = RNG.normal(0, 1, 5000)
    y = RNG.random(5000) < 1 / (1 + np.exp(-(0.5 * z - 1)))
    a, c = V.platt(z, y)
    assert 0.3 < a < 0.7 and -1.2 < c < -0.8
    assert V.platt(z, np.zeros(5000)) is None, "one class: the raw blend"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
