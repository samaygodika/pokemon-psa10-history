"""Leak tests for panel.py: features must ignore the future, targets must
ignore the past.   analysis/.venv/bin/python -m pytest analysis -q
(or just run this file)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import panel  # noqa: E402


def make_sales(rows):
    df = pd.DataFrame(rows, columns=["asset_id", "date", "price", "source", "sale_type"])
    df["date"] = pd.to_datetime(df.date)
    df["is_bin"] = (df.sale_type == "BUY_IT_NOW").astype(float)
    df["is_ebay"] = (df.source == "eBay").astype(float)
    return df.sort_values(["asset_id", "date"]).reset_index(drop=True)


ASSETS = pd.DataFrame({"asset_id": ["a", "b"], "subject": ["Charizard", "Charizard"], "year": [1999, 1999], "set": ["Base", "Base"]})


def test_future_sales_do_not_touch_features():
    # 5 steady $100 sales before t, then a planted $1,000,000 sale the day after t
    base = [("a", f"2024-0{m}-15", 100.0, "eBay", "AUCTION") for m in range(1, 6)]
    planted = [("a", "2024-06-02", 1_000_000.0, "eBay", "AUCTION"), ("a", "2024-06-20", 1_000_000.0, "eBay", "AUCTION")]
    p = panel.build_panel(make_sales(base + planted), ASSETS, [pd.Timestamp("2024-06-01")])
    row = p.iloc[0]
    assert row.ref == 100.0, row.ref
    assert row.vol30 == 1 and row.n_sales_to_date == 5
    assert abs(row.mom30) < 1e-9  # 100 vs 100
    # and the target DOES see it
    assert row.fwd60 > 1000, row.fwd60


def test_target_ignores_past_and_needs_future_sales():
    base = [("a", f"2024-0{m}-15", 100.0, "eBay", "AUCTION") for m in range(1, 6)]
    p = panel.build_panel(make_sales(base), ASSETS, [pd.Timestamp("2024-06-01")])
    assert np.isnan(p.iloc[0].fwd60)  # no sales after t -> no target, not a fake 0%
    one_after = base + [("a", "2024-07-01", 150.0, "eBay", "AUCTION")]
    p = panel.build_panel(make_sales(one_after), ASSETS, [pd.Timestamp("2024-06-01")])
    assert np.isnan(p.iloc[0].fwd60)  # MIN_FWD_SALES = 2
    two_after = one_after + [("a", "2024-07-10", 150.0, "eBay", "AUCTION")]
    p = panel.build_panel(make_sales(two_after), ASSETS, [pd.Timestamp("2024-06-01")])
    assert abs(p.iloc[0].fwd60 - 0.5) < 1e-9


def test_momentum_uses_only_sales_before_each_window_end():
    # $100 through March, $200 in May: at t=June 1, mom30 compares ref(t)=200 with ref(May 2)=...
    rows = [("a", "2024-01-10", 100.0, "eBay", "AUCTION"), ("a", "2024-02-10", 100.0, "eBay", "AUCTION"),
            ("a", "2024-03-10", 100.0, "eBay", "AUCTION"), ("a", "2024-05-20", 200.0, "eBay", "BUY_IT_NOW"),
            ("a", "2024-05-25", 200.0, "eBay", "BUY_IT_NOW")]
    p = panel.build_panel(make_sales(rows), ASSETS, [pd.Timestamp("2024-06-01")])
    row = p.iloc[0]
    assert row.ref == 200.0                     # median of last 3 = [100,200,200]
    assert abs(row.mom30 - 1.0) < 1e-9          # ref(May 2) = median([100,100,100]) = 100
    assert abs(row.bin_share90 - 2 / 3) < 1e-9  # Mar 10 (auction) + two May BINs are within 90d of Jun 1


def test_exec_target_uses_first_sale_after_t_and_the_60_90_window():
    base = [("a", f"2024-0{m}-15", 100.0, "eBay", "AUCTION") for m in range(1, 6)]
    after = [("a", "2024-06-05", 110.0, "eBay", "AUCTION"),   # entry: first sale after t
             ("a", "2024-06-25", 500.0, "eBay", "AUCTION"),   # inside (t, t+60]: must NOT count as exit
             ("a", "2024-08-05", 220.0, "eBay", "AUCTION"),   # exit window (t+60, t+90]
             ("a", "2024-08-20", 200.0, "eBay", "AUCTION")]
    p = panel.build_panel(make_sales(base + after), ASSETS, [pd.Timestamp("2024-06-01")])
    row = p.iloc[0]
    assert row.entry_price == 110.0
    assert abs(row.fwd_exec - (210.0 / 110.0 - 1)) < 1e-9
    # no sale within 14 days of t -> not buyable -> no target
    late = base + [("a", "2024-06-20", 110.0, "eBay", "AUCTION"), ("a", "2024-08-05", 220.0, "eBay", "AUCTION"), ("a", "2024-08-20", 200.0, "eBay", "AUCTION")]
    p = panel.build_panel(make_sales(late), ASSETS, [pd.Timestamp("2024-06-01")])
    assert np.isnan(p.iloc[0].fwd_exec)


def test_universe_requires_liquidity():
    rows = [("a", "2024-01-10", 100.0, "eBay", "AUCTION"), ("a", "2024-02-10", 100.0, "eBay", "AUCTION")]
    p = panel.build_panel(make_sales(rows), ASSETS, [pd.Timestamp("2024-06-01")])
    assert len(p) == 0  # 2 sales in 180d < MIN_SALES_180


def test_character_momentum_is_leave_one_out():
    rows = []
    for aid, price_may in (("a", 200.0), ("b", 100.0)):
        rows += [(aid, f"2024-0{m}-10", 100.0, "eBay", "AUCTION") for m in range(1, 4)]
        rows += [(aid, "2024-05-20", price_may, "eBay", "AUCTION"), (aid, "2024-05-25", price_may, "eBay", "AUCTION")]
    p = panel.build_panel(make_sales(rows), ASSETS, [pd.Timestamp("2024-06-01")]).set_index("asset_id")
    assert abs(p.loc["a"].char_mom30 - p.loc["b"].mom30) < 1e-9
    assert abs(p.loc["b"].char_mom30 - p.loc["a"].mom30) < 1e-9


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
