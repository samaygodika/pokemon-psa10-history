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
    df["is_ah"] = df.source.str.lower().str.contains("auction").astype(float)
    return df.sort_values(["asset_id", "date"]).reset_index(drop=True)


ASSETS = pd.DataFrame({"asset_id": ["a", "b"], "subject": ["Charizard", "Charizard"], "year": [1999, 1999], "set": ["Base", "Base"], "variety": ["Holo", "Holo"]})


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


def test_money_target_entry_is_median_of_21_days_and_exit_is_40th_pct():
    base = [("a", f"2024-0{m}-15", 100.0, "eBay", "AUCTION") for m in range(1, 6)]
    after = [("a", "2024-06-03", 1.0, "eBay", "AUCTION"),          # junk entry row: a median ignores it
             ("a", "2024-06-10", 120.0, "eBay", "AUCTION"),
             ("a", "2024-06-20", 130.0, "Goldin Auctions", "AUCTION"),
             ("a", "2024-06-25", 999.0, "eBay", "AUCTION"),        # after the entry window
             ("a", "2024-07-05", 300.0, "eBay", "AUCTION"),        # exit30 window (t+30, t+60]
             ("a", "2024-07-10", 200.0, "eBay", "AUCTION"),
             ("a", "2024-07-20", 100.0, "eBay", "AUCTION")]
    p = panel.build_panel(make_sales(base + after), ASSETS, [pd.Timestamp("2024-06-01")])
    row = p.iloc[0]
    assert row.entry_med == 120.0 and row.n_entry == 3
    assert abs(row.entry_ah - 1 / 3) < 1e-9
    assert row.exit_flag30 == "ok" and row.n_exit30 == 3
    assert abs(row.exit30 - np.quantile([300.0, 200.0, 100.0], 0.4)) < 1e-9
    assert abs(row.gross30 - (row.exit30 / 120.0 - 1)) < 1e-9
    # net is below gross by the cost model, and the entry premium counts the Goldin share
    assert row.mny30 < row.gross30
    assert 0.0 < row.breakeven < 0.6


def test_money_target_extends_then_marks_illiquid_instead_of_dropping():
    base = [("a", f"2024-0{m}-15", 100.0, "eBay", "AUCTION") for m in range(1, 6)]
    entry = [("a", "2024-06-10", 120.0, "eBay", "AUCTION")]
    # only one sale in (t+30, t+60], a second one in the extension (t+60, t+90]
    p = panel.build_panel(make_sales(base + entry + [("a", "2024-07-10", 150.0, "eBay", "AUCTION"), ("a", "2024-08-20", 170.0, "eBay", "AUCTION")]), ASSETS, [pd.Timestamp("2024-06-01")])
    row = p.iloc[0]
    assert row.exit_flag30 == "extended" and row.n_exit30 == 2
    assert abs(row.exit30 - np.quantile([150.0, 170.0], 0.4)) < 1e-9
    # nothing at all after the entry: illiquid, marked at the last sale (the entry) with the haircut applied in mny
    p = panel.build_panel(make_sales(base + entry), ASSETS, [pd.Timestamp("2024-06-01")])
    row = p.iloc[0]
    assert row.exit_flag30 == "illiquid" and row.exit30 == 120.0 and row.n_exit30 == 0
    assert not np.isnan(row.mny30)
    assert row.mny30 < panel.CostModel().net_return(120.0, 120.0)  # the haircut bites
    # no entry sale at all -> no money target, and no fake 0
    p = panel.build_panel(make_sales(base), ASSETS, [pd.Timestamp("2024-06-01")])
    assert np.isnan(p.iloc[0].mny30) and p.iloc[0].exit_flag30 == ""


def test_money_target_ignores_sales_before_t():
    base = [("a", f"2024-0{m}-15", 100.0, "eBay", "AUCTION") for m in range(1, 6)]
    planted_past = [("a", "2024-05-30", 5.0, "eBay", "AUCTION")]   # a $5 sale two days before t must not be the entry
    after = [("a", "2024-06-10", 120.0, "eBay", "AUCTION"), ("a", "2024-07-05", 130.0, "eBay", "AUCTION"), ("a", "2024-07-10", 130.0, "eBay", "AUCTION")]
    p = panel.build_panel(make_sales(base + planted_past + after), ASSETS, [pd.Timestamp("2024-06-01")])
    assert p.iloc[0].entry_med == 120.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)


def test_band_z_ignores_future_and_needs_a_years_history():
    # 6 sales in the trailing year at 100/100/100/100/100/200: ref = median(100,100,200) = 100 -> below the year mean
    base = [("a", f"2024-0{m}-15", 100.0, "eBay", "AUCTION") for m in range(1, 6)] + [("a", "2024-05-20", 200.0, "eBay", "AUCTION")]
    planted = [("a", "2024-06-02", 1_000_000.0, "eBay", "AUCTION"), ("a", "2024-06-20", 1_000_000.0, "eBay", "AUCTION")]
    p0 = panel.build_panel(make_sales(base), ASSETS, [pd.Timestamp("2024-06-01")])
    p1 = panel.build_panel(make_sales(base + planted), ASSETS, [pd.Timestamp("2024-06-01")])
    assert p0.iloc[0].band_z < 0 and abs(p0.iloc[0].band_z - p1.iloc[0].band_z) < 1e-12
    thin = [("a", f"2024-0{m}-15", 100.0, "eBay", "AUCTION") for m in range(2, 6)]   # 4 sales < BAND_MIN_SALES
    assert np.isnan(panel.build_panel(make_sales(thin), ASSETS, [pd.Timestamp("2024-06-01")]).iloc[0].band_z)


def test_peer_resid_is_cross_sectional_and_thin_groups_are_nan():
    # three Base Set Holo Charizard-subject cards at 100 / 400 / 400: the cheap one sits below its peers
    rows = []
    for aid, price in (("a", 100.0), ("b", 400.0), ("c", 400.0)):
        rows += [(aid, f"2024-0{m}-15", price, "eBay", "AUCTION") for m in range(1, 6)]
    assets = pd.DataFrame({"asset_id": list("abc"), "subject": ["Charizard"] * 3, "year": [1999] * 3, "set": ["Base"] * 3, "variety": ["Holo"] * 3})
    p = panel.build_panel(make_sales(rows), assets, [pd.Timestamp("2024-06-01")]).set_index("asset_id")
    assert p.loc["a", "peer_resid"] < -0.5 and abs(p.loc["b", "peer_resid"]) < 1e-9
    # two cards only -> the (set, finish, language) group is below PEER_MIN_GROUP -> NaN, never a fake 0
    p2 = panel.build_panel(make_sales([r for r in rows if r[0] != "c"]), assets, [pd.Timestamp("2024-06-01")])
    assert p2.peer_resid.isna().all()
    # scarcity_gap only exists when pops are supplied
    assert p.scarcity_gap.isna().all()
