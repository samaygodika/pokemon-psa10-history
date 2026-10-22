#!/usr/bin/env python3
"""Checks for history/buy_list.py (2026-10-21): language and era tags, the buckets, the avoid layer,
the cell fallback, the score arithmetic, the reason order, and the ledger's replace-by-date rule.

    python3 history/test_buy_list.py

Plain asserts, no pytest."""
import csv
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buy_list as bl  # noqa: E402


def card(**kw):
    base = {"asset_id": kw.get("asset_id", "A"), "card_name": "2005 Pokemon Ex Delta Species Holo Mewtwo #12", "set": "Pokemon Ex Delta Species",
            "subject": "Mewtwo", "variety": "", "year": "2005", "grade": "10.0", "median_last_3": "600", "clean_last_sale_date": "2026-10-01",
            "price_chg_30d_pct": "", "price_chg_90d_pct": "", "volume_30d": "1", "volume_90d": "3", "volume_180d": "5", "volume_1y": "8",
            "last_sale_status": "completed", "last_sale_title_check": "", "last_sale_unconfirmed": "0", "lowest_bin_price": "", "lowest_bin_verified_at": "", "lowest_bin_suspect": ""}
    base.update(kw)
    return base


def rates():
    r = {}
    for h in (90, 180):
        r[(h, "regime_era_tier_liq", "warm", "vintage", "500-2k", "2-3")] = {"n": 500, "win_rate": 0.40, "win_net": 0.90, "loss_net": -0.30}
        r[(h, "regime_era_tier", "warm", "vintage", "500-2k", "")] = {"n": 2000, "win_rate": 0.42, "win_net": 0.90, "loss_net": -0.30}
        r[(h, "regime_tier", "warm", "", "500-2k", "")] = {"n": 4000, "win_rate": 0.41, "win_net": 0.90, "loss_net": -0.30}
        r[(h, "regime", "warm", "", "", "")] = {"n": 40000, "win_rate": 0.20, "win_net": 0.80, "loss_net": -0.35}
        r[(h, "regime_era_tier_liq", "warm", "vintage", "500-2k", "8+")] = {"n": 20, "win_rate": 0.90, "win_net": 2.0, "loss_net": -0.1}   # too thin, must fall back
    return r


def test_tags_and_buckets():
    assert bl.language(card()) == "english"
    assert bl.language(card(set="Pokemon Japanese Base Set")) == "japanese"
    assert bl.language(card(card_name="2019 Pokemon Japanese Promo Pikachu")) == "japanese"
    assert bl.era("1999") == "wotc" and bl.era("2005") == "vintage" and bl.era("2016") == "modern" and bl.era("2024") == "current" and bl.era("") == ""
    assert bl.tier(30) == "<50" and bl.tier(150) == "150-500" and bl.tier(499.99) == "150-500" and bl.tier(2000) == "2k+"
    assert bl.liq(0) == "0" and bl.liq(1) == "1" and bl.liq(3) == "2-3" and bl.liq(7) == "4-7" and bl.liq(12) == "8+"
    assert bl.regime(0.10) == "flat" and bl.regime(0.25) == "warm" and bl.regime(0.39) == "warm" and bl.regime(0.40) == "hot"
    lo, hi = bl.wilson(0.4, 500)
    assert 0.36 < lo < 0.4 < hi < 0.44


def test_lookup_falls_back_to_a_cell_with_enough_trades():
    r = rates()
    row, level = bl.lookup(r, 180, "warm", "vintage", "500-2k", "2-3")
    assert level == "regime_era_tier_liq" and row["n"] == 500 and row["win_rate_raw"] == 0.40 and abs(row["win_rate"] - bl.shrunk_rate(500, 0.40, 0.42)) < 1e-12
    row, level = bl.lookup(r, 180, "warm", "modern", "<50", "1")
    assert level == "regime" and row["win_rate"] == 0.20, "the coarsest level has no parent: unshrunk"
    row, level = bl.lookup(r, 180, "warm", "vintage", "500-2k", "8+")
    assert level == "regime_era_tier" and row["n"] == 2000, "a 20-trade cell is skipped for the next level"
    row, level = bl.lookup(r, 180, "warm", "modern", "500-2k", "1")
    assert level == "regime_tier"
    row, level = bl.lookup(r, 180, "warm", "modern", "<50", "1")
    assert level == "regime"
    assert bl.lookup(r, 180, "flat", "wotc", "<50", "0") == (None, "")


def run(cards, zs=None, lifts=None, data_date=date(2026, 10, 21)):
    # a warm market: 30 liquid >= $150 cards, 10 of them up more than 5%
    filler = [card(asset_id=f"F{i}", set=f"Filler {i}", subject=f"S{i}", price_chg_30d_pct="12" if i < 10 else "0", volume_1y="6") for i in range(30)]
    return bl.compute(cards + filler, zs or {}, rates(), lifts or {}, data_date, "2026-10-21T07:00:00+00:00")


def test_score_and_membership():
    listed, ledger, stats = run([card()])
    assert stats["regime"] == "warm" and abs(stats["breadth"] - 10 / 31) < 1e-9, stats
    me = [r for r in listed if r["asset_id"] == "A"]
    assert len(me) == 1, "no avoid flag, p 0.40 >= 0.15, expected net > 0 -> listed"
    r = me[0]
    b = bl.shrunk_rate(500, 0.40, 0.42)   # the 500-trade cell pulled toward its 2,000-trade parent: 0.4057
    assert abs(b - (500 * 0.40 + 200 * 0.42) / 700) < 1e-12
    assert r["cell"] == "warm|vintage|500-2k|2-3" and r["cell_level"] == "regime_era_tier_liq" and r["p_win_180"] == f"{b:.3f}" and r["base_rate_180"] == f"{b:.3f}"
    assert r["exp_net_pct_180"] == f"{100 * (b * 0.9 - (1 - b) * 0.3):.1f}" and r["exp_net_usd_180"] == f"{600 * (b * 0.9 - (1 - b) * 0.3):.0f}"
    assert r["cuts"] == "" and r["avoid_flags"] == "" and r["reason"].startswith("base 41% for vintage $500-2k with 2-3 sales/90 d in a warm market")
    assert r["live_bin_price"] == "" and r["model"] == "v0" and r["data_date"] == "2026-10-21"
    assert r["rank_pct"] and int(r["rank_pct"]) >= 1
    assert float(r["p_low_180"]) < b < float(r["p_high_180"])
    led = [x for x in ledger if x["asset_id"] == "A"]
    assert len(led) == 1 and led[0]["listed"] == "1" and led[0]["rank"] == r["rank"] and led[0]["flags"] == "" and led[0]["days_since"] == "20"


def test_cuts_move_the_odds_and_are_capped():
    assert abs(bl.odds_apply(0.40, 1.5) - 0.5) < 1e-9 and bl.odds_apply(0.64, 2.0) < 0.79, "an odds ratio of 2 on 64% gives 78%, not 90%"
    lifts = {(180, "stale30", "warm"): 1.3, (180, "band_high", "warm"): 2.0, (90, "stale30", "warm"): 1.2}
    listed, _, _ = run([card(clean_last_sale_date="2026-09-01")], zs={"A": 1.4}, lifts=lifts)
    r = [x for x in listed if x["asset_id"] == "A"][0]
    assert r["cuts"] == "stale30 band_high"
    b = bl.shrunk_rate(500, 0.40, 0.42)
    assert r["p_win_180"] == f"{bl.odds_apply(b, 2.6):.3f}", "odds ratios multiply: 1.3 x 2.0 = 2.6 under the cap of 3"
    assert r["p_win_90"] == f"{bl.odds_apply(b, 1.2):.3f}"
    assert float(r["p_low_180"]) < float(r["p_win_180"]) < float(r["p_high_180"])
    assert "no sale in 50 d" in r["reason"] and "+1.4 sd vs own 1-y band" in r["reason"]
    lifts[(180, "stale30", "warm")] = 2.0
    listed, _, _ = run([card(clean_last_sale_date="2026-09-01")], zs={"A": 1.4}, lifts=lifts)
    r = [x for x in listed if x["asset_id"] == "A"][0]
    assert r["p_win_180"] == f"{bl.odds_apply(b, 3.0):.3f}", "the product is capped at 3"


def test_avoid_layer_and_warnings():
    for kw, flag in (({"year": "2026"}, "release_year"), ({"price_chg_30d_pct": "60", "volume_30d": "3"}, "busy_spike"),
                     ({"volume_30d": "4", "volume_1y": "12"}, "vol_surge"), ({"price_chg_30d_pct": "-25"}, "dip"),
                     ({"last_sale_status": "pending"}, "pending_sale"), ({"last_sale_title_check": "mismatch"}, "title_mismatch")):
        listed, ledger, stats = run([card(**kw)])
        assert not [r for r in listed if r["asset_id"] == "A"], flag
        assert stats["excluded"] >= 1
    listed, _, _ = run([card()], zs={"A": -1.2})
    assert not [r for r in listed if r["asset_id"] == "A"], "below_band"
    listed, ledger, _ = run([card(volume_180d="2", last_sale_unconfirmed="1")])
    r = [x for x in listed if x["asset_id"] == "A"][0]
    assert r["avoid_flags"] == "illiquid_exit unconfirmed" and "illiquid exit" in r["reason"] and "unconfirmed" in r["reason"], "warnings stay on the row"
    listed, ledger, stats = run([card(price_chg_30d_pct="60", volume_30d="1")])
    assert [r for r in listed if r["asset_id"] == "A"], "a thin spike (1 sale) is not the busy-spike flag"


def test_set_chase_flat_and_tail_lead_and_category_reason_first():
    # a set of 8: two of the three chase cards up 40% over 90 d, 'A' the cheapest at $600
    rows = [card(asset_id=f"D{i}", median_last_3=str(1000 + 100 * i), price_chg_90d_pct="40" if i >= 5 else "", volume_1y="6") for i in range(7)]
    rows.append(card(asset_id="A", median_last_3="600", volume_180d="5"))   # cheapest of 8 -> bottom quartile; tier 500-2k has a cell
    listed, ledger, stats = run(rows)
    a = [r for r in listed if r["asset_id"] == "A"][0]
    assert a["reason"].startswith("set's chase cards +40% / 90 d"), a["reason"]
    assert a["avoid_flags"] == "", "tail_lead is a ledger flag, not shown as a warning"
    led = [x for x in ledger if x["asset_id"] == "A"][0]
    assert "tail_lead" in led["flags"] and led["set_price_q"] == "0.12" and led["set_lead_chg90"] == "40.0"
    assert stats["tail_lead"] == 2, "A and D0: the bottom quartile of 8 is two cards"
    # the same set with flat chase cards keeps everyone off the list
    rows = [card(asset_id=f"D{i}", median_last_3=str(1000 + 100 * i), price_chg_90d_pct="3" if i >= 5 else "", volume_1y="6") for i in range(7)]
    rows.append(card(asset_id="A", median_last_3="600"))
    listed, _, _ = run(rows)
    assert not [r for r in listed if r["asset_id"] == "A"], "chase_flat"


def test_jp_heat_and_live_bin_in_reason():
    jp = [card(asset_id=f"J{i}", set="Pokemon Japanese Ex Delta", card_name="Pokemon Japanese Mewtwo", price_chg_30d_pct="20") for i in range(2)]
    me = card(lowest_bin_price="510", lowest_bin_verified_at="2026-10-20T07:00:00+00:00", lowest_bin_url="https://www.ebay.com/itm/1")
    listed, _, _ = run([me] + jp, lifts={(180, "jp_heat", "warm"): 1.2})
    r = [x for x in listed if x["asset_id"] == "A"][0]
    assert r["cuts"] == "jp_heat", r["cuts"]
    assert r["reason"].startswith("Mewtwo's other cards +20% / 30 d; Japanese copies +20% / 30 d"), "character leg first, then the Japanese copies: " + r["reason"]
    assert "live eBay Buy It Now $510 (-15% vs level)" in r["reason"], r["reason"]
    assert r["live_bin_price"] == "510.00" and r["live_bin_url"].endswith("/1")


def test_ledger_replaces_the_same_date_only():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ledger.csv"
        rows1 = [{c: "" for c in bl.LEDGER_COLS} | {"data_date": "2026-10-20", "asset_id": "A"}]
        assert "rank_pct" in bl.LEDGER_COLS
        rows2 = [{c: "" for c in bl.LEDGER_COLS} | {"data_date": "2026-10-21", "asset_id": "B"}]
        bl.update_ledger(p, rows1, "2026-10-20")
        bl.update_ledger(p, rows2, "2026-10-21")
        n_old, n_new = bl.update_ledger(p, rows2 + [{c: "" for c in bl.LEDGER_COLS} | {"data_date": "2026-10-21", "asset_id": "C"}], "2026-10-21")
        assert (n_old, n_new) == (1, 2)
        got = list(csv.DictReader(open(p)))
        assert [(r["data_date"], r["asset_id"]) for r in got] == [("2026-10-20", "A"), ("2026-10-21", "B"), ("2026-10-21", "C")]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
