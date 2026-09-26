#!/usr/bin/env python3
"""Direct-call checks for psa9_lag.py (pytest is not in the venv).

    analysis/.venv/bin/python analysis/test_psa9_lag.py

Same convention as test_panel.py: plant sales in the future and prove the
point-in-time pieces do not move; plus numerical checks of the estimators.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import psa9_lag as L  # noqa: E402
from panel import ref_price  # noqa: E402

DAY = L.DAY


def test_ols_twoway_matches_lstsq_and_hc0():
    rng = np.random.default_rng(0)
    n = 400
    X = np.column_stack([np.ones(n), rng.normal(size=n)])
    y = X @ np.array([0.5, 1.5]) + rng.normal(size=n)
    beta, se = L.ols_twoway(X, y, np.arange(n), np.arange(n) + n)   # every obs its own cluster in both dims -> HC0
    ref = np.linalg.lstsq(X, y, rcond=None)[0]
    assert np.allclose(beta, ref), (beta, ref)
    e = y - X @ beta
    xtx_inv = np.linalg.inv(X.T @ X)
    hc0 = np.sqrt(np.diag(xtx_inv @ (X * e[:, None]).T @ (X * e[:, None]) @ xtx_inv))
    assert np.allclose(se, hc0), (se, hc0)
    # clustered SE must grow when residuals are shared within a cluster
    cl = np.repeat(np.arange(40), 10)
    y2 = X @ np.array([0.5, 1.5]) + np.repeat(rng.normal(size=40), 10) + 0.1 * rng.normal(size=n)
    _, se_cl = L.ols_twoway(X, y2, cl, np.arange(n))
    _, se_iid = L.ols_twoway(X, y2, np.arange(n), np.arange(n) + n)
    assert se_cl[0] > 2 * se_iid[0], (se_cl, se_iid)


def test_ref_at_matches_panel_ref_price():
    rng = np.random.default_rng(1)
    d = np.sort(np.datetime64("2024-01-01") + rng.integers(0, 600, 60).astype("timedelta64[D]"))
    p = rng.lognormal(5, 0.3, 60)
    for i in range(60):
        lo = np.searchsorted(d, d[i] - L.REF_LOOKBACK * DAY, side="right")
        want = ref_price(p, lo, i + 1)
        got = L.ref_at(d, p, np.array([i]))[0]
        assert np.isclose(got, want), (i, got, want)
    assert np.isnan(L.ref_at(d, p, np.array([-1]))[0])


def _card(rng, n10=80, n9=60, start="2023-01-01"):
    d10 = np.sort(np.datetime64(start) + rng.integers(0, 700, n10).astype("timedelta64[D]"))
    p10 = rng.lognormal(6, 0.15, n10)
    d9 = np.sort(np.datetime64(start) + rng.integers(0, 700, n9).astype("timedelta64[D]"))
    p9 = rng.lognormal(5, 0.15, n9)
    return d10, p10, d9, p9


def _events(d10, p10):
    idx = np.arange(len(d10))
    ref_now = L.ref_at(d10, p10, idx)
    hi_prev = np.searchsorted(d10, d10 - 30 * DAY, side="right")
    ref_prev = L.ref_at(d10, p10, hi_prev - 1)
    return ref_now / ref_prev - 1


def test_event_detection_is_point_in_time():
    rng = np.random.default_rng(2)
    d10, p10, _, _ = _card(rng)
    mom = _events(d10, p10)
    # plant a huge PSA 10 sale after every existing sale: nothing before it may change
    d_plus = np.append(d10, d10[-1] + 5 * DAY)
    p_plus = np.append(p10, p10.max() * 50)
    mom_plus = _events(d_plus, p_plus)
    assert np.allclose(mom, mom_plus[:-1], equal_nan=True)
    # plant a huge sale in the middle: only sales on/after it may change
    k = 40
    d_mid = np.insert(d10, k, d10[k])
    p_mid = np.insert(p10, k, p10.max() * 50)
    mom_mid = _events(d_mid, p_mid)
    assert np.allclose(mom[:k], mom_mid[:k], equal_nan=True)


def test_psa9_entry_is_strictly_after_detection_and_windows_are_grade_separated():
    rng = np.random.default_rng(3)
    d10, p10, d9, p9 = _card(rng)
    anchor = d10[50]
    tr = L.trade_at(d9, p9, anchor)
    if not np.isnan(tr["entry"]):
        assert tr["entry_date"] > anchor
        assert tr["entry_date"] <= anchor + L.ENTRY_DAYS * DAY
    # a PSA 9 sale planted ON the detection date is not a valid entry (must be strictly after)
    d9b = np.sort(np.append(d9, anchor))
    p9b = np.append(p9, 1.0)
    tr_b = L.trade_at(d9b, p9b, anchor)
    assert np.isnan(tr_b["entry"]) or tr_b["entry"] != 1.0
    # a PSA 9 sale planted after the last exit window cannot change entry or the 30/60 exits
    d9c = np.append(d9, anchor + 400 * DAY)
    p9c = np.append(p9, 1e6)
    tr_c = L.trade_at(d9c, p9c, anchor)
    for k in ("entry", "exit30", "exit60"):
        assert (np.isnan(tr[k]) and np.isnan(tr_c[k])) or tr[k] == tr_c[k], (k, tr[k], tr_c[k])
    # PSA 9 windows never see PSA 10 prices
    path = L.path_at(d9, np.log(p9), d10, np.log(p10), anchor)
    path2 = L.path_at(d9, np.log(p9), d10, np.log(p10 * 7.0), anchor)
    for w in L.WINDOWS:
        a, b = path[f"m9_{w}"], path2[f"m9_{w}"]
        assert (np.isnan(a) and np.isnan(b)) or a == b, w
        c, e = path[f"m10_{w}"], path2[f"m10_{w}"]
        assert np.isnan(c) or np.isclose(e - c, np.log(7.0)), w
    # window boundaries: [d-90, d-30) baseline vs [d-30, d] move window are disjoint and cover the sales they should
    lo, hi = L.WINDOWS["pre"], L.WINDOWS["move"]
    n_pre = ((d9 > anchor + lo[0] * DAY) & (d9 <= anchor + lo[1] * DAY)).sum()
    n_move = ((d9 > anchor + hi[0] * DAY) & (d9 <= anchor)).sum()
    assert path["n9_pre"] == n_pre and path["n9_move"] == n_move


def test_lagged_corr_sign_and_shift_direction():
    """A panel where r9_t = r10_{t-1} exactly must show corr ~1 at lag +1 (PSA 10 leads) and ~0 elsewhere."""
    rng = np.random.default_rng(4)
    rows = []
    months = pd.period_range("2021-01", "2026-06", freq="M")
    for aid in range(60):
        x = rng.normal(size=len(months))
        y = np.roll(x, 1) + 0.05 * rng.normal(size=len(months))
        y[0] = np.nan
        for m, r10, r9 in zip(months, x, y):
            rows.append({"asset_id": f"c{aid}", "month": m, "r10": r10, "r9": r9})
    ret = pd.DataFrame(rows)
    cc = L.lagged_corr(ret, range(-2, 3), demean=False).set_index("lag")
    assert cc.loc[1, "corr"] > 0.95, cc
    assert abs(cc.loc[0, "corr"]) < 0.1 and abs(cc.loc[-1, "corr"]) < 0.1, cc
    reg = L.panel_regression(ret, "r9", "r10", x_lags=(1, 2), own_lags=(1,)).set_index("regressor")
    assert reg.loc["r10[t-1]", "coef"] > 0.9 and abs(reg.loc["r10[t-2]", "coef"]) < 0.1, reg


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} checks passed")
