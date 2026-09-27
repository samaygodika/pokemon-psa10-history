#!/usr/bin/env python3
"""Direct-call checks for psa9_buyrule.py (pytest is not in the venv).

    analysis/.venv/bin/python analysis/test_psa9_buyrule.py

The rule flags only signals detectable from sales dated before the anchor; a
PSA 9 that moved in the same month is not flagged; top-50 / blue-chip
membership comes from the cap columns and matches subjects whole-word; the
PSA 10 tier split lands on the $1K / $10K boundaries.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import psa9_buyrule as B  # noqa: E402
import psa9_hype as H  # noqa: E402
import psa9_lag as L  # noqa: E402


def _sales(rows):
    s = pd.DataFrame(rows, columns=["asset_id", "grade", "date", "price"])
    s["date"] = pd.to_datetime(s.date)
    s["source"] = "eBay"
    return s


def _rows_from_sales(sales, universe):
    monthly = L.monthly_series(sales)
    base = H.add_excess(H.build_rows(sales, universe))
    ret = L.monthly_returns(monthly[monthly.asset_id.isin(universe)])[["asset_id", "month", "r10", "r9"]]
    ret = ret.rename(columns={"month": "signal_month", "r10": "own_r10", "r9": "own_r9"})
    return base.merge(ret, on=["asset_id", "signal_month"], how="left")


def test_rule_flags_only_detectable_at_anchor():
    # card x: PSA 10 at $100 through March 2024, $250 in April (up 150%); PSA 9 flat at $40 in March and April.
    rows = []
    for d in pd.date_range("2023-01-05", "2024-03-25", freq="10D"):
        rows += [("x", "10.0", d, 100.0), ("x", "9.0", d, 40.0)]
    for d in pd.date_range("2024-04-03", "2024-04-28", freq="5D"):
        rows += [("x", "10.0", d, 250.0), ("x", "9.0", d, 40.0)]
    for d in pd.date_range("2024-05-02", "2025-06-01", freq="10D"):        # afterwards: PSA 9 doubles (the outcome, never an input)
        rows += [("x", "10.0", d, 250.0), ("x", "9.0", d, 80.0)]
    sales = _sales(rows)
    r = _rows_from_sales(sales, pd.Index(["x"]))
    r["date"] = pd.to_datetime(r.date)
    flagged = r.loc[B.rule_state(r, 1.00), "date"].dt.strftime("%Y-%m-%d").tolist()
    assert flagged == ["2024-05-01"], flagged           # the April jump is usable from May 1 only, not from April 1
    # the same at 50%
    assert r.loc[B.rule_state(r, 0.50), "date"].dt.strftime("%Y-%m-%d").tolist() == ["2024-05-01"]
    # planting the jump one month later moves the flag one month later: nothing after the anchor leaks in
    sales2 = sales.copy()
    sales2.loc[sales2.date.dt.month == 4, "price"] = np.where(sales2.loc[sales2.date.dt.month == 4, "grade"] == "10.0", 100.0, 40.0)
    sales2.loc[(sales2.date >= "2024-05-01") & (sales2.date < "2024-06-01") & (sales2.grade == "9.0"), "price"] = 40.0
    r2 = _rows_from_sales(sales2, pd.Index(["x"]))
    r2["date"] = pd.to_datetime(r2.date)
    assert r2.loc[B.rule_state(r2, 1.00), "date"].dt.strftime("%Y-%m-%d").tolist() == ["2024-06-01"]
    # the outcome the May-1 row sees is the PSA 9 doubling, strictly after T
    row = r[r.date == "2024-05-01"].iloc[0]
    assert np.isclose(row.post9_90, np.log(2), atol=1e-6)
    assert row.entry == 80.0                            # first PSA 9 sale after T


def test_psa9_that_moved_is_not_flagged_and_unobserved_is_optional():
    rows = []
    for d in pd.date_range("2023-01-05", "2024-03-25", freq="10D"):
        rows += [("y", "10.0", d, 100.0), ("y", "9.0", d, 40.0)]
    for d in pd.date_range("2024-04-03", "2024-04-28", freq="5D"):
        rows += [("y", "10.0", d, 250.0), ("y", "9.0", d, 60.0)]       # PSA 9 up 50% in the same month
    for d in pd.date_range("2024-05-02", "2024-12-01", freq="10D"):
        rows += [("y", "10.0", d, 250.0), ("y", "9.0", d, 60.0)]
    # card z: PSA 10 doubles in April, no PSA 9 sale at all in April
    for d in pd.date_range("2023-01-05", "2024-03-25", freq="10D"):
        rows += [("z", "10.0", d, 100.0), ("z", "9.0", d, 40.0)]
    for d in pd.date_range("2024-04-03", "2024-04-28", freq="5D"):
        rows += [("z", "10.0", d, 250.0)]
    for d in pd.date_range("2024-05-02", "2024-12-01", freq="10D"):
        rows += [("z", "10.0", d, 250.0), ("z", "9.0", d, 40.0)]
    r = _rows_from_sales(_sales(rows), pd.Index(["y", "z"]))
    r["date"] = pd.to_datetime(r.date)
    strict = r[B.rule_state(r, 1.00)]
    assert strict.empty, strict[["asset_id", "date"]]                   # y: PSA 9 moved; z: PSA 9 return unobserved
    loose = r[B.rule_state(r, 1.00, flat_observed=False)]
    assert loose[["asset_id"]].values.ravel().tolist() == ["z"] and str(loose.date.iloc[0].date()) == "2024-05-01"


def test_membership_lists(tmp_path=Path("/tmp")):
    chars = pd.DataFrame({"character": ["Charizard", "Mew", "Mewtwo", "Palkia", "Ditto"],
                          "alt_market_cap_usd": [500, 50, 300, 100, 10], "alt_market_cap_le2013_usd": [400, 40, 200, 20, 60]})
    p = Path("/private/tmp/claude-501/-Users-samaygodika-Documents-Code-scrape-/ed217a88-f0e5-4ab7-90c1-5111d678a5f4/scratchpad/buyrule")
    p.mkdir(parents=True, exist_ok=True)
    chars.to_csv(p / "chars.csv", index=False)
    assert B.top_characters("alt_market_cap_usd", top=3, path=p / "chars.csv") == ["Charizard", "Mewtwo", "Palkia"]
    assert B.top_characters("alt_market_cap_le2013_usd", top=3, path=p / "chars.csv") == ["Charizard", "Mewtwo", "Ditto"]
    # blue chip = top of the list by own cap (price x pop, English, <= 2013); a Japanese card and a 2020 card do not count
    cards = pd.DataFrame({"year": ["1999", "2008", "2008", "2020", "2003"], "clean_last_sale_price": ["1000", "5000", "100000", "1e9", "10"],
                          "pop_at_grade": ["10", "10", "10", "1", "1"], "card_name": ["Base Charizard", "LA Mewtwo Lv X", "Japanese Mewtwo", "Modern Palkia", "Mew"],
                          "set": ["Base", "LA", "Japanese", "SWSH", "e"], "variety": ["", "", "", "", ""],
                          "subject": ["Charizard", "Mewtwo Lv X", "Mewtwo", "Palkia", "Mew"]})
    blue, caps = B.blue_chips(["Charizard", "Mewtwo", "Palkia", "Mew"], cards=cards, n=2)
    assert blue == ["Mewtwo", "Charizard"], (blue, caps)            # 50,000 > 10,000; Palkia's 2020 card and the Japanese Mewtwo excluded
    assert caps["Palkia"] == 0 and caps["Mew"] == 10                # "Mew" does not match "Mewtwo" (whole word)
    # member_ids: subject whole-word match, hyphen = space
    assets = pd.DataFrame({"subject": ["Mewtwo Lv X", "Mew", "Ho-Oh", "Meowth"]}, index=["a", "b", "c", "d"])
    assert B.member_ids(assets, ["Mewtwo"]) == {"a"}
    assert B.member_ids(assets, ["Mew"]) == {"b"}
    assert B.member_ids(assets, ["Ho-Oh"]) == {"c"}
    assert B.member_ids(assets, []) == set()


def test_tier_split():
    df = pd.DataFrame({"p10": [999.0, 1000.0, 9999.0, 10000.0, 250000.0]})
    tier = pd.cut(df.p10, [t[0] for t in H.TIERS] + [np.inf], labels=B.TIER_LABELS, right=False).astype(str).tolist()
    assert tier == ["< $1K", "$1K-$10K", "$1K-$10K", ">= $10K", ">= $10K"], tier


def test_paired_by_month():
    rows = pd.DataFrame({"signal_month": pd.PeriodIndex(["2024-01", "2024-01", "2024-02", "2024-02", "2024-03"], freq="M"),
                         "v": [0.30, 0.10, 0.20, 0.00, 0.50]})
    a = np.array([True, False, True, False, True])
    s = B.paired_by_month(rows, a, ~a, "v")
    assert s.startswith("+20.0 / +20.0, 100% up") and s.endswith("[2]"), s     # March has no B row, so 2 paired months


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
