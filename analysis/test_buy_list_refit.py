#!/usr/bin/env python3
"""Checks for analysis/buy_list_refit.py: which months count as newly closed, the out-of-sample check
scoring a month the tables never saw, the listed view's avoid layer, the log's replace-by-day rule.

    analysis/.venv/bin/python analysis/test_buy_list_refit.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buy_list_refit as rf  # noqa: E402
import buy_list_rates as rates_mod  # noqa: E402

TS = pd.Timestamp


def synthetic():
    """three hot anchor months, 300 wotc $150-500 trades each with one sale in 90 d, winning 40% / 60% / 80%
    at both horizons; a win nets +100%, a loss -50%; no cut on, no avoid flag."""
    rows = []
    for month, rate in ((TS("2025-01-01"), 0.4), (TS("2025-02-01"), 0.6), (TS("2025-03-01"), 0.8)):
        for i in range(300):
            win = i < rate * 300
            rows.append({"asset_id": f"{month:%m}-{i}", "date": month, "breadth": 0.5, "regime": "hot", "era": "wotc", "tier": "150-500", "liq": "1",
                         "mny90": 1.0 if win else -0.5, "mny180": 1.0 if win else -0.5, "stale30": False, "jp_heat": False, "band_high": False,
                         "tail_lead": False, "thin_history": False, "year": 2000, "mom30": 0.1, "vol30": 1, "vol365": 12, "band_z": 0.0,
                         "set_lead_mom90": 0.5})
    return pd.DataFrame(rows)


def test_newly_closed_boundaries():
    d = pd.Series([TS("2026-03-01"), TS("2026-04-01"), TS("2026-05-01")])
    got = rf.newly_closed(d, 180, TS("2026-10-20"), TS("2026-10-28")).tolist()
    assert got == [False, True, False], got            # March closed by 10-20; April closes exactly on 10-28 (04-01 + 210 d)
    assert not rf.newly_closed(d, 180, TS("2026-10-20"), TS("2026-10-20")).any()
    print("ok test_newly_closed_boundaries")


def fitted(u, data_end, tmp):
    cells, lifts, _, _ = rates_mod.fit(u, data_end)
    rates_mod.write_tables(cells, lifts, tmp)
    return rf.load_tables(tmp)


def test_check_scores_the_unseen_month():
    u = synthetic()
    old_end = TS("2025-02-01") + pd.Timedelta(days=210)    # months 1-2 closed at 180 d, all three at 90 d
    new_end = TS("2025-03-01") + pd.Timedelta(days=210)    # month 3 closes at 180 d
    with tempfile.TemporaryDirectory() as tmp:
        rates, lifts = fitted(u, old_end, tmp)
        rows, scored = rf.check(u, rates, lifts, old_end, new_end, "2025-09-27T00:00:00+00:00")
    tops = [r for r in rows if r["group"] == "all"]
    assert {(r["horizon"], r["anchor_month"], r["view"]) for r in tops} == {(180, "2025-03", "all"), (180, "2025-03", "listed")}, tops
    r = next(x for x in tops if x["view"] == "all")
    assert r["n"] == 300 and r["n_scored"] == 300
    assert abs(r["p_mean"] - 0.5) < 1e-3, r              # the tables saw 40% and 60%: the cell (shrunk to an identical parent) says 50%
    assert abs(r["win_rate"] - 0.8) < 1e-9 and abs(r["gap_pts"] - 30) < 0.1
    assert abs(r["brier"] - 0.25) < 1e-3 and abs(r["skill"]) < 1e-3   # the regime alone says the same 50%
    assert abs(r["net_pred"] - 0.25) < 1e-3 and abs(r["net_real"] - 0.7) < 1e-9   # 0.5 x 100% + 0.5 x -50%; 0.8 x 100% + 0.2 x -50%
    assert scored.listed.all()
    print("ok test_check_scores_the_unseen_month")


def test_listed_view_drops_avoid_flags():
    u = synthetic()
    m3 = u.date == TS("2025-03-01")
    u.loc[m3 & (u.index % 3 == 0), "mom30"] = -0.3             # a dip: the nightly would not list these
    u.loc[m3 & (u.index % 3 == 1), "year"] = 2025              # release year
    old_end, new_end = TS("2025-02-01") + pd.Timedelta(days=210), TS("2025-03-01") + pd.Timedelta(days=210)
    with tempfile.TemporaryDirectory() as tmp:
        rates, lifts = fitted(u, old_end, tmp)
        rows, _ = rf.check(u, rates, lifts, old_end, new_end, "x")
    listed = next(x for x in rows if x["view"] == "listed" and x["group"] == "all")
    allv = next(x for x in rows if x["view"] == "all" and x["group"] == "all")
    assert allv["n"] == 300 and listed["n"] == 100, (allv["n"], listed["n"])
    print("ok test_listed_view_drops_avoid_flags")


def test_no_cell_leaves_trades_unscored():
    u = synthetic()
    u.loc[u.date == TS("2025-03-01"), "regime"] = "warm"      # a regime the tables have no cell for
    old_end, new_end = TS("2025-02-01") + pd.Timedelta(days=210), TS("2025-03-01") + pd.Timedelta(days=210)
    with tempfile.TemporaryDirectory() as tmp:
        rates, lifts = fitted(u, old_end, tmp)
        rows, _ = rf.check(u, rates, lifts, old_end, new_end, "x")
    r = next(x for x in rows if x["view"] == "all" and x["group"] == "all")
    assert r["n"] == 300 and r["n_scored"] == 0 and "p_mean" not in r and abs(r["win_rate_all"] - 0.8) < 1e-9, r
    print("ok test_no_cell_leaves_trades_unscored")


def test_log_replaces_the_same_day():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / rf.LOG_NAME
        row = {c: "" for c in rf.LOG_COLS}
        rf.write_log(path, [{**row, "checked_at": "2026-11-01T10:00:00+00:00", "anchor_month": "2026-04"}], "2026-11-01T10:00:00+00:00")
        rf.write_log(path, [{**row, "checked_at": "2026-12-01T10:00:00+00:00", "anchor_month": "2026-05"}], "2026-12-01T10:00:00+00:00")
        n = rf.write_log(path, [{**row, "checked_at": "2026-12-01T11:00:00+00:00", "anchor_month": "2026-05"}], "2026-12-01T11:00:00+00:00")
        log = pd.read_csv(path, dtype=str)
    assert n == 2 and sorted(log.anchor_month) == ["2026-04", "2026-05"], log
    print("ok test_log_replaces_the_same_day")


def test_tables_without_meta_are_the_first_ones():
    with tempfile.TemporaryDirectory() as tmp:
        assert rf.current_data_end(tmp) == TS("2026-10-20")
    print("ok test_tables_without_meta_are_the_first_ones")


if __name__ == "__main__":
    np.seterr(all="ignore")
    test_newly_closed_boundaries()
    test_check_scores_the_unseen_month()
    test_listed_view_drops_avoid_flags()
    test_no_cell_leaves_trades_unscored()
    test_log_replaces_the_same_day()
    test_tables_without_meta_are_the_first_ones()
