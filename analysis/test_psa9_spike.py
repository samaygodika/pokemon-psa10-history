#!/usr/bin/env python3
"""Direct-call checks for psa9_spike.py (pytest is not in the venv).

    analysis/.venv/bin/python analysis/test_psa9_spike.py

Spike detection uses only the two PRIOR sales (planting later sales changes
nothing before them); spikes within 14 days of the last kept spike collapse
into it; PSA 9 windows are assigned as (D, D+3], (D+3, D+7], ...; the pre
level is (D-60, D]; entries are strictly after D and exits >= entry + hold;
windows past the data end are blank; the same-date control excludes cards
with a recent spike and lines up on the event's date and card type; the
matched control draws same-type cards with no recent spike.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import psa9_spike as S  # noqa: E402

D = np.timedelta64(1, "D")


def _dates(*strs):
    return np.array(strs, dtype="datetime64[D]")


def test_spike_uses_only_prior_two_sales():
    d = _dates("2024-01-01", "2024-01-10", "2024-01-20", "2024-02-01", "2024-02-10")
    p = np.array([100.0, 300.0, 290.0, 320.0, 1000.0])
    kept, prev2, ratio, cand = S.find_spikes(d, p, 0.50)
    # sale 2: prior two are 100 and 300 -> median 200; 290 / 200 = +45%: not a spike at 50%
    assert not cand[2], (ratio[2], cand)
    # sale 3: prior two 300, 290 -> 295; 320 is +8%: no. sale 4: prior 290, 320 -> 305; 1000 is +228%: yes
    assert list(kept) == [4], kept
    assert abs(prev2[4] - 305.0) < 1e-9 and abs(ratio[4] - (1000 / 305 - 1)) < 1e-9
    # at 100% the same
    kept2, *_ = S.find_spikes(d, p, 1.00)
    assert list(kept2) == [4]
    # planting a huge LATER sale changes nothing about earlier decisions
    d2 = np.append(d, _dates("2024-03-01"))
    p2 = np.append(p, 50_000.0)
    kept3, _, ratio3, cand3 = S.find_spikes(d2, p2, 0.50)
    assert list(cand3[:5]) == list(cand[:5]) and np.allclose(ratio3[:5], ratio[:5], equal_nan=True)
    assert list(kept3) == [4, 5]
    # the first two sales can never be spikes (no two priors)
    assert not cand[0] and not cand[1] and np.isnan(ratio[0]) and np.isnan(ratio[1])
    # prior-age cap: the older prior sale must be within max_prior_age days
    d4 = _dates("2022-01-01", "2023-12-20", "2024-02-10")
    p4 = np.array([100.0, 100.0, 1000.0])
    assert list(S.find_spikes(d4, p4, 0.5, max_prior_age=365)[0]) == []
    assert list(S.find_spikes(d4, p4, 0.5, max_prior_age=10_000)[0]) == [2]
    # sales before START are never events
    d5 = _dates("2019-01-01", "2019-01-05", "2019-01-09")
    assert list(S.find_spikes(d5, np.array([1.0, 1.0, 10.0]), 0.5, max_prior_age=10_000)[0]) == []


def test_collapse_14_days_first_wins():
    d = _dates("2024-01-01", "2024-01-02", "2024-01-03", "2024-01-13", "2024-01-17", "2024-01-23", "2024-02-20")
    p = np.array([100.0, 100.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0])
    # candidates: index 2 (1000 vs 100) and index 3 (1000 vs median(100, 1000) = 550); 3 is 10 days after 2 -> folded
    kept, _, _, cand = S.find_spikes(d, p, 0.5)
    assert list(np.flatnonzero(cand)) == [2, 3] and list(kept) == [2]
    # now make every later sale a spike over its priors and check the collapsing chain
    p = np.array([100.0, 100.0, 1000.0, 1e4, 1e5, 1e6, 1e7])
    kept, _, _, cand = S.find_spikes(d, p, 0.5)
    assert list(np.flatnonzero(cand)) == [2, 3, 4, 5, 6]
    # 01-03 kept; 01-13 (10 days) folded; 01-17 (14 days from 01-03) folded (<= 14); 01-23 (20 days) kept; 02-20 kept
    assert [str(x) for x in d[kept]] == ["2024-01-03", "2024-01-23", "2024-02-20"], d[kept]


def test_window_assignment_and_pre_level():
    D0 = _dates("2024-03-01")
    d9 = _dates("2023-12-31", "2024-01-01", "2024-03-01", "2024-03-04", "2024-03-05", "2024-03-15", "2024-03-31", "2024-04-30", "2024-05-01")
    p9 = np.array([1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0])
    d10 = _dates("2024-01-15", "2024-02-15", "2024-03-01")
    p10 = np.array([100.0, 100.0, 200.0])
    r = S.event_rows("x", D0, d10, p10, d9, p9, data_end=_dates("2025-01-01")[0])
    # pre level (D-60, D]: 2023-12-31 is exactly D-61 -> out; 2024-01-01 (D-60) -> out (open bound); 03-01 (= D) -> in
    assert r["n9_pre"][0] == 1 and np.isclose(np.exp(r["m9_pre"][0]), 4.0), (r["n9_pre"], r["m9_pre"])
    # (D, D+3]: 03-04 only (D itself excluded); (D+3, D+7]: 03-05; (D+7, D+14]: 03-15 (= D+14, closed bound); (D+14, D+30]: 03-31; (D+30, D+60]: 04-30 (05-01 = D+61 out)
    assert np.isclose(np.exp(r["m9_0_3"][0]), 8.0) and r["n9_0_3"][0] == 1
    assert np.isclose(np.exp(r["m9_3_7"][0]), 16.0) and r["n9_3_7"][0] == 1
    assert np.isclose(np.exp(r["m9_7_14"][0]), 32.0) and r["n9_7_14"][0] == 1
    assert np.isclose(np.exp(r["m9_14_30"][0]), 64.0) and r["n9_14_30"][0] == 1
    assert np.isclose(np.exp(r["m9_30_60"][0]), 128.0) and r["n9_30_60"][0] == 1
    # the PSA 10 windows exclude the spike sale itself (dated D)
    assert r["n10_0_3"][0] == 0


def test_entry_strictly_after_D_and_exit_after_hold():
    D0 = _dates("2024-03-01")
    d9 = _dates("2024-03-01", "2024-03-02", "2024-03-06", "2024-05-30", "2024-05-31", "2024-06-05")
    p9 = np.array([100.0, 50.0, 60.0, 100.0, 100.0, 100.0])
    d10 = _dates("2024-01-01", "2024-02-01", "2024-03-01")
    p10 = np.array([1.0, 1.0, 2.0])
    r = S.event_rows("x", D0, d10, p10, d9, p9, data_end=_dates("2025-01-01")[0])
    # the $100 sale on D is not buyable; the entry in (D, D+3] is the $50 sale on D+1
    assert r["entry_0_3"][0] == 50.0 and r["elag_0_3"][0] == 1
    assert r["entry_3_7"][0] == 60.0 and r["elag_3_7"][0] == 5
    assert np.isnan(r["entry_7_14"][0])
    # 90-day hold from 03-02: the first sale >= 05-31 is the $100 on 05-31 (05-30 is day 89)
    assert np.isclose(r["net_0_3_90"][0], 100 * (1 - S.FEE) / 50 - 1)
    # 60-day hold from 03-02: first sale >= 05-01 is 05-30
    assert np.isclose(r["net_0_3_60"][0], 100 * (1 - S.FEE) / 50 - 1)
    # 180-day hold: no sale within (entry+180, entry+240] -> no exit (NaN) though the window is closed
    assert np.isnan(r["net_0_3_180"][0]) and r["closed_0_3_180"][0]
    # right-censoring: with the data ending before the exit window closes, the trade is NaN and not "closed"
    r2 = S.event_rows("x", D0, d10, p10, d9, p9, data_end=_dates("2024-06-01")[0])
    assert np.isnan(r2["net_0_3_90"][0]) and not r2["closed_0_3_90"][0]
    # (D+30, D+60] ends 04-30 <= 06-01: closed, and empty because no PSA 9 sold in it
    assert r2["closed_30_60"][0] and np.isnan(r2["m9_30_60"][0]) and r2["n9_30_60"][0] == 0
    r3 = S.event_rows("x", D0, d10, p10, d9, p9, data_end=_dates("2024-04-29")[0])
    assert not r3["closed_30_60"][0] and np.isnan(r3["m9_30_60"][0])
    assert r3["closed_14_30"][0]


def test_same_date_control_alignment():
    sales = pd.DataFrame([
        # control card a: PSA 9 sales around two event dates; a 50% spike candidate on 2024-03-01
        ("a", "9.0", "2024-01-05", 10.0), ("a", "9.0", "2024-03-03", 12.0), ("a", "9.0", "2024-06-05", 15.0), ("a", "9.0", "2024-06-08", 15.0), ("a", "9.0", "2024-09-10", 20.0),
        # control card b: no spikes, PSA 9 sales after both dates
        ("b", "9.0", "2024-03-03", 100.0), ("b", "9.0", "2024-06-06", 100.0), ("b", "9.0", "2024-06-07", 100.0), ("b", "9.0", "2024-09-20", 87.0 / (1 - S.FEE)),
    ], columns=["asset_id", "grade", "date", "price"])
    sales["date"] = pd.to_datetime(sales.date)
    dates = _dates("2024-03-02", "2024-06-04")
    cand = {"a": _dates("2024-03-01")}
    ct_of = {"a": "LV.X", "b": "Gold Star"}
    hot = {0.15: {("LV.X", pd.Period("2024-03", "M"))}, 0.30: set()}
    dc = S.date_control(sales, ["a", "b"], dates, cand, ct_of, hot, data_end=_dates("2025-06-01")[0])
    dc = dc.set_index(["asset_id", "date"])
    # a spiked on 03-01, so on 03-02 it is "recent spike"; by 06-04 (95 days later) it is not
    assert dc.loc[("a", pd.Timestamp("2024-03-02")), "recent_spike"] and not dc.loc[("a", pd.Timestamp("2024-06-04")), "recent_spike"]
    assert not dc.loc[("b", pd.Timestamp("2024-03-02")), "recent_spike"]
    # hot flag follows the card's type and the date's month
    assert dc.loc[("a", pd.Timestamp("2024-03-02")), "hot15"] and not dc.loc[("a", pd.Timestamp("2024-06-04")), "hot15"]
    assert not dc.loc[("b", pd.Timestamp("2024-03-02")), "hot15"]
    # the control trade is anchored on the event date: b bought at the first sale in (06-04, 06-07] = 06-06 at 100, sold >= 90 days later at 87/(1-fee) -> net -13%
    assert np.isclose(dc.loc[("b", pd.Timestamp("2024-06-04")), "net_0_3_90"], -0.13, atol=1e-6)
    # a on 06-04: entry 06-05 at 15, exit 09-10 at 20
    assert np.isclose(dc.loc[("a", pd.Timestamp("2024-06-04")), "net_0_3_90"], 20 * (1 - S.FEE) / 15 - 1, atol=1e-6)
    dc = dc.reset_index()
    # medians per date exclude the recently spiked card: on 03-02 only b is eligible
    med, cnt = S.control_medians(dc)
    assert cnt.loc[pd.Timestamp("2024-03-02"), "net_0_3_90"] == 1 and cnt.loc[pd.Timestamp("2024-06-04"), "net_0_3_90"] == 2
    # excluding hot types drops a on 03-02 anyway and keeps both on 06-04
    med_h, cnt_h = S.control_medians(dc, "hot15")
    assert cnt_h.loc[pd.Timestamp("2024-06-04"), "net_0_3_90"] == 2
    # by type: the (date, type) index lines up with an event of that type
    med_t, _ = S.control_medians(dc, by_type=True)
    ev = pd.DataFrame({"date": [pd.Timestamp("2024-06-04")], "card_type": ["Gold Star"]})
    assert np.isclose(S._ref(med_t, ev, "net_0_3_90")[0], -0.13, atol=1e-6)
    ev2 = pd.DataFrame({"date": [pd.Timestamp("2024-06-04")], "card_type": ["Neo Shining"]})
    assert np.isnan(S._ref(med_t, ev2, "net_0_3_90")[0])          # no control of that type -> no excess, not a wrong one


def test_matched_control_same_type_no_recent_spike():
    rows = []
    for aid, base in (("e", 10.0), ("m1", 20.0), ("m2", 30.0), ("m3", 40.0)):
        for d in pd.date_range("2024-01-01", "2024-12-31", freq="15D"):
            rows += [(aid, "10.0", d, base * 10), (aid, "9.0", d, base)]
    sales = pd.DataFrame(rows, columns=["asset_id", "grade", "date", "price"])
    events = pd.DataFrame({"asset_id": ["e"], "date": [pd.Timestamp("2024-06-01")], "card_type": ["LV.X"]}, index=[7])
    pool = {"LV.X": np.array(["e", "m1", "m2", "m3"])}
    cand = {"m2": _dates("2024-05-20")}                     # m2 spiked 12 days before D -> ineligible
    mc = S.matched_control(sales, events, pool, cand, data_end=_dates("2025-06-01")[0], k=5)
    assert set(mc.asset_id) == {"m1", "m3"}, set(mc.asset_id)          # not the event card, not the recently spiked one
    assert (mc.event_id == 7).all() and (mc.date == pd.Timestamp("2024-06-01")).all()
    # a spike 61+ days before D no longer disqualifies
    cand = {"m2": _dates("2024-03-30")}
    mc = S.matched_control(sales, events, pool, cand, data_end=_dates("2025-06-01")[0], k=5)
    assert set(mc.asset_id) == {"m1", "m2", "m3"}
    # excess = event minus the median of its matches, aligned by event_id
    ev = pd.DataFrame({"rel9_0_3": [0.5]}, index=[7])
    mc2 = pd.DataFrame({"event_id": [7, 7, 7], "rel9_0_3": [0.1, 0.2, 0.6]})
    out = S.matched_excess(ev, mc2, ["rel9_0_3"])
    assert np.isclose(out.loc[7, "rel9_0_3_mx"], 0.5 - 0.2)


def test_period_t_one_observation_per_month():
    # 100 events in one month and 2 in another: the t must see 2 periods, not 102 observations
    v = np.r_[np.full(100, 0.5), [0.1, 0.1]]
    per = np.r_[np.repeat("2026-03", 100), ["2026-04", "2026-04"]]
    mean, med, pos, t, n = S.period_t(v, per)
    assert n == 2 and np.isnan(t) and np.isclose(mean, 0.3) and pos == 1.0
    v = np.array([0.1, 0.2, 0.3, -0.1])
    per = np.array(["a", "b", "c", "c"])
    mean, med, pos, t, n = S.period_t(v, per)
    assert n == 3 and np.isclose(mean, (0.1 + 0.2 + 0.1) / 3) and np.isfinite(t)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} checks passed")
