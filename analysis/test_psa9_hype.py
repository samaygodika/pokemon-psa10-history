#!/usr/bin/env python3
"""Direct-call checks for psa9_hype.py (pytest is not in the venv).

    analysis/.venv/bin/python analysis/test_psa9_hype.py

Point-in-time: sales after an anchor never change what the row knows at the
anchor, horizon windows past the data end are blank, a hype month flags the
NEXT month's anchor only. Plus the BH adjustment and the card-type rules.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import psa9_hype as H  # noqa: E402

DAY = H.DAY


def _card(rng, n=120, start="2023-01-01", days=900):
    d = np.sort(np.datetime64(start) + rng.integers(0, days, n).astype("timedelta64[D]"))
    return d, rng.lognormal(5, 0.2, n)


def test_rows_point_in_time_and_censored():
    rng = np.random.default_rng(0)
    d10, p10 = _card(rng)
    d9, p9 = _card(rng)
    anchors = np.array(["2024-01-01", "2024-06-01", "2025-03-01"], dtype="datetime64[D]")
    end = np.datetime64("2025-06-20")
    a = pd.DataFrame(H.card_rows("x", d10, p10, d9, p9, anchors, end))
    # plant wild sales strictly after each anchor: the move windows must not change
    d10b = np.sort(np.concatenate([d10, anchors + 1 * DAY]))
    p10b = np.concatenate([p10, np.full(3, 1e6)])[np.argsort(np.concatenate([d10, anchors + 1 * DAY]), kind="stable")]
    b = pd.DataFrame(H.card_rows("x", d10b, p10b, d9, p9, anchors, end))
    assert np.allclose(a.m10_move, b.m10_move, equal_nan=True)
    assert not np.allclose(a.m10_30, b.m10_30, equal_nan=True)          # ...while the next window does see them
    # windows ending after the data end are blank; 2025-03-01 + 180d > end
    assert np.isnan(a.loc[2, "m9_180"]) and np.isnan(a.loc[2, "m9_365"])
    assert np.isfinite(a.loc[1, "m9_180"])                               # 2024-06-01 + 180d is inside the data
    # a trade whose exit window has not closed is blank, never a partial result
    assert np.isnan(a.loc[2, "net365"]) and not a.loc[2, "closed365"]


def test_trade_entry_strictly_after_anchor():
    d9 = np.array(["2024-01-01", "2024-01-05", "2024-04-10"], dtype="datetime64[D]")
    p9 = np.array([100.0, 200.0, 400.0])
    d10, p10 = d9.copy(), p9.copy()
    r = H.card_rows("x", d10, p10, d9, p9, np.array(["2024-01-01"], dtype="datetime64[D]"), np.datetime64("2026-01-01"))
    assert r["entry"][0] == 200.0                                        # the sale ON the anchor day is not buyable
    assert np.isclose(r["net90"][0], 400 * (1 - H.L.FEE) / 200 - 1)


def test_hype_month_flags_next_anchor_only():
    r10 = pd.DataFrame({"asset_id": list("abcdefab"), "month": pd.PeriodIndex(["2024-03"] * 6 + ["2024-04"] * 2, freq="M"),
                        "r10": [0.5, 0.4, 0.3, 0.35, 0.6, 0.01, 0.9, 0.9]})
    hm = H.hype_months(r10, {"g": set("abcdef")}, hype=0.30, min_cards=5)
    assert bool(hm.set_index("month").loc[pd.Period("2024-03", "M"), "hyped"])
    assert not bool(hm.set_index("month").loc[pd.Period("2024-04", "M"), "hyped"])   # 2 cards < min_cards
    rows = pd.DataFrame({"asset_id": ["a", "a", "a"], "month": pd.PeriodIndex(["2024-03", "2024-04", "2024-05"], freq="M")})
    rows["signal_month"] = rows.month - 1
    out = H.tag_hype(rows, hm, {"g": set("abcdef")}, "hot")
    assert out.hot.tolist() == [False, True, False]                     # anchor 2024-04-01 is the first that may use March


def test_bh():
    p = np.array([0.01, 0.04, 0.03, 0.2, np.nan])
    q = H.bh(p)
    assert np.isnan(q[-1])
    assert np.allclose(q[:4], [0.04, 0.16 / 3, 0.16 / 3, 0.2]), q    # 0.03*4/2 = 0.06 is pulled down to 0.04*4/3


def test_card_types():
    a = pd.DataFrame({"card_name": ["2005 Pokemon Ex Deoxys Holo Rayquaza Gold Star #107", "2008 Pokemon Diamond and Pearl Great Encounters Holo Cresselia Lv X #103",
                                    "2002 Pokemon Neo Destiny 1st Edition Shining Charizard #107", "2003 Pokemon Skyridge Holo Charizard #146",
                                    "2003 Pokemon Skyridge Holo Gengar #H9", "2010 Triumphant English Bottom Half Dialga/Palkia Legend #102",
                                    "2006 Organized Play Series 4 Deoxys Ex #17", "2009 Pokemon Card Game Dpt Arceus Lv.X Deck: Grass & Fire Japanese Sceptile #004",
                                    "2017 Pokemon Shining Legends Japanese Venusaur #3"],
                      "year": [2005, 2008, 2002, 2003, 2003, 2010, 2006, 2009, 2017],
                      "set": ["Ex Deoxys", "Great Encounters", "Neo Destiny", "Skyridge", "Skyridge", "Triumphant", "Organized Play", "Dpt Arceus Lv.X Deck", "Shining Legends"],
                      "card_number": ["107", "103", "107", "146", "H9", "102", "17", "004", "3"],
                      "subject": ["Rayquaza", "Cresselia", "Charizard", "Charizard", "Gengar", "Dialga/Palkia", "Deoxys Ex", "Sceptile", "Venusaur"]})
    got = H.card_types(a).tolist()
    # an H-numbered Skyridge holo is not a Crystal; a "Lv.X Deck" card is not a LV.X; Shining Legends (2017) is not Neo Shining
    want = ["Gold Star", "LV.X", "Neo Shining", "e-card Crystal", np.nan, "HGSS Prime/LEGEND", "EX-era ex", np.nan, np.nan]
    for g, w in zip(got, want):
        assert (pd.isna(g) and pd.isna(w)) or g == w, (got, want)


def test_matched_excess_uses_same_month_era_tier_unhyped_only():
    cols = {f"{k}_{h}": 0.0 for h in H.HORIZONS for k in ("post9", "post10", "gap")}
    cols.update({f"net{h}": 0.0 for h in H.HOLDS})
    df = pd.DataFrame([{**cols, "month": pd.Period("2024-05", "M"), "era": e, "tier": t, "hype_any": hy, "post9_180": v}
                       for e, t, hy, v in [("EX", "< $1K", True, 0.50), ("EX", "< $1K", False, 0.10), ("EX", "< $1K", False, 0.30),
                                           ("WOTC", "< $1K", False, -0.40), ("EX", "$1K-$10K", False, 0.90)]])
    out = H.add_matched_excess(df)
    assert np.isclose(out.loc[0, "post9_180_mx"], 0.50 - 0.20)        # median of the two unhyped EX < $1K rows only
    assert np.isclose(out.loc[3, "post9_180_mx"], 0.0)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
