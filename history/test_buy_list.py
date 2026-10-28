#!/usr/bin/env python3
"""Checks for history/buy_list.py (2026-10-21): language and era tags, the buckets, the avoid layer,
the cell fallback, the score arithmetic, the reason order, and the ledger's replace-by-date rule.

    python3 history/test_buy_list.py

Plain asserts, no pytest."""
import csv
import json
import math
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buy_list as bl  # noqa: E402


def card(**kw):
    base = {"asset_id": kw.get("asset_id", "A"), "card_name": "2005 Pokemon Ex Delta Species Holo Mewtwo #12", "set": "Pokemon Ex Delta Species",
            "subject": "Mewtwo", "variety": "", "year": "2005", "grade": "10.0", "median_last_3": "600", "clean_last_sale_date": "2026-10-01",
            "price_chg_30d_pct": "", "price_chg_90d_pct": "", "price_chg_1y_pct": "50", "volume_30d": "1", "volume_90d": "3", "volume_180d": "5", "volume_1y": "8",
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
    assert r["live_bin_price"] == "" and r["model"] == "v0.1" and r["data_date"] == "2026-10-21"
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


def test_month_band_and_cell_months():
    # v0.1: the band is the wider of Wilson and p +- the month-clustered half-width; under 2 months, 0..1
    lo, hi = bl.wilson(0.6, 235)
    assert bl.band(0.6, 235, None, None) == (lo, hi), "an old rates file: Wilson alone"
    assert bl.band(0.6, 235, 3, 0.38) == (0.6 - 0.38, 0.6 + 0.38), "3 months at +-38 points: far wider than Wilson's +-5"
    assert bl.band(0.8, 235, 3, 0.38)[1] == 1.0, "clipped at 1"
    assert bl.band(0.6, 235, 40, 0.001) == (lo, hi), "never narrower than Wilson"
    assert bl.band(0.6, 235, 1, None) == (0.0, 1.0)
    r = rates()
    for h in (90, 180):
        r[(h, "regime_era_tier_liq", "warm", "vintage", "500-2k", "2-3")].update(n_months=3.0, month_half=0.30)
    listed, ledger, _ = bl.compute([card()] + [card(asset_id=f"F{i}", set=f"Filler {i}", subject=f"S{i}", price_chg_30d_pct="12" if i < 10 else "0", volume_1y="6")
                                               for i in range(30)], {}, r, {}, date(2026, 10, 21), "2026-10-21T07:00:00+00:00")
    a = [x for x in listed if x["asset_id"] == "A"][0]
    b = bl.shrunk_rate(500, 0.40, 0.42)
    assert a["p_low_180"] == f"{b - 0.30:.3f}" and a["p_high_180"] == f"{b + 0.30:.3f}" and a["cell_months"] == "3", a
    assert "n 500 over 3 months)" in a["reason"], a["reason"]
    assert [x for x in ledger if x["asset_id"] == "A"][0]["cell_months"] == "3"


def test_thin_history_cut():
    # a 2013-or-earlier card with no price a year back: the cut, its odds ratio, and the last price before the gap in the reason
    lifts = {(180, "thin_history", "warm"): 0.78, (90, "thin_history", "warm"): 0.8}
    filler = [card(asset_id=f"F{i}", set=f"Filler {i}", subject=f"S{i}", price_chg_30d_pct="12" if i < 10 else "0", volume_1y="6") for i in range(30)]
    listed, ledger, stats = bl.compute([card(price_chg_1y_pct="")] + filler, {}, rates(), lifts, date(2026, 10, 21), "t",
                                       prior={"A": ("2024-12-09", 20.0)})
    a = [x for x in listed if x["asset_id"] == "A"][0]
    b = bl.shrunk_rate(500, 0.40, 0.42)
    assert a["cuts"] == "thin_history" and a["p_win_180"] == f"{bl.odds_apply(b, 0.78):.3f}" and a["p_win_90"] == f"{bl.odds_apply(b, 0.8):.3f}"
    assert "no price a year back to measure its run (last priced $20 the week of 2024-12-09, 30x since)" in a["reason"], a["reason"]
    assert stats["listed_thin_history"] == 1 and [x for x in ledger if x["asset_id"] == "A"][0]["chg1y"] == ""
    # stacked boosts over the cap of 3 do not swallow the penalty: it applies after the cap
    lifts2 = {(180, "stale30", "warm"): 2.0, (180, "band_high", "warm"): 2.0, (180, "thin_history", "warm"): 0.78}
    listed, _, _ = bl.compute([card(price_chg_1y_pct="", clean_last_sale_date="2026-09-01")] + filler, {"A": 1.4}, rates(), lifts2, date(2026, 10, 21), "t")
    a = [x for x in listed if x["asset_id"] == "A"][0]
    assert a["cuts"] == "stale30 band_high thin_history" and a["p_win_180"] == f"{bl.odds_apply(b, 3.0 * 0.78):.3f}", a["p_win_180"]
    # a modern card with no year-back price is new, not thin; a vintage card with a 1-year change is measured
    listed, _, _ = bl.compute([card(price_chg_1y_pct="", year="2016", card_name="2016 Pokemon XY Evolutions Holo Mewtwo #51")] + filler,
                              {}, rates(), lifts, date(2026, 10, 21), "t")
    assert all("thin_history" not in x["cuts"] for x in listed)
    listed, _, _ = bl.compute([card(price_chg_1y_pct="5100")] + filler, {}, rates(), lifts, date(2026, 10, 21), "t")
    assert [x for x in listed if x["asset_id"] == "A"][0]["cuts"] == ""


def test_category_falling_warning():
    # Sid's Correcting rule over recent sales: 10 LV.X cards, 6 of them 30% under their own 75-day high
    today = date(2026, 10, 21)
    lvx = [card(asset_id=f"L{i}", card_name=f"2008 Pokemon Diamond and Pearl Legends Awakened Holo Mewtwo Lv X #{140 + i}", year="2008",
                set="Pokemon Diamond and Pearl Legends Awakened", subject=f"Lx{i}", volume_1y="2") for i in range(10)]   # out of breadth's liquid set
    def sales(peak, now):
        return [(date(2026, 9, 1), peak * 0.8, False), (date(2026, 9, 20), peak, False), (date(2026, 10, 15), now, False)]
    recent = {f"L{i}": sales(1000, 700 if i < 6 else 990) for i in range(10)}
    ph = bl.category_phases(lvx, recent, today)
    assert set(ph) == {"LV.X"} and ph["LV.X"]["n"] == 10 and ph["LV.X"]["correcting"] and abs(ph["LV.X"]["breadth"] - 60) < 1e-9
    assert abs(ph["LV.X"]["off"] - ((6 * 700 + 4 * 990) / 10000 - 1) * 100) < 1e-9
    filler = [card(asset_id=f"F{i}", set=f"Filler {i}", subject=f"S{i}", price_chg_30d_pct="12" if i < 10 else "0", volume_1y="6") for i in range(30)]
    me = card(card_name="2008 Pokemon Diamond and Pearl Legends Awakened Holo Palkia Lv X #150", set="Pokemon Diamond and Pearl Legends Awakened", year="2008")
    listed, _, stats = bl.compute([me] + lvx + filler, {}, rates(), {}, today, "t", recent=recent)
    a = [x for x in listed if x["asset_id"] == "A"][0]
    assert "category_falling" in a["avoid_flags"].split() and a["category"] == "LV.X" and a["category_breadth_pct"] == "60.0", a
    assert a["reason"].startswith("LV.X is correcting: -18% off its 75-day highs, 60% of 10 cards 15%+ below their own peak"), a["reason"]
    assert stats["categories_correcting"] == 1, "a warning on the row, never a filter"
    # 9 cards are too few (Sid's 10, so a thin week cannot flip a category); held-back lows do not count; a stale newest sale drops the card
    assert bl.category_phases(lvx[:9], recent, today) == {}
    assert bl.card_phase_reading([(date(2026, 9, 1), 100, False), (date(2026, 9, 10), 120, False), (date(2026, 10, 1), 5, True)], today) is None
    assert bl.card_phase_reading([(date(2026, 8, 1), 100, False), (date(2026, 8, 10), 120, False), (date(2026, 9, 1), 110, False)], today) is None
    assert bl.category_of(card(card_name="2005 Pokemon Ex Delta Species Holo Mewtwo #12")) == "Pokemon Ex Delta Species", "no special type: the set"


def test_market_cooling_warning():
    listed, ledger, stats = run([card()])
    r = [x for x in listed if x["asset_id"] == "A"][0]
    assert not stats["market_cooling"] and r["avoid_flags"] == "", "10 of 31 up, none down: no warning"
    assert (r["market_up30_pct"], r["market_down30_pct"], r["market_up90_pct"], r["market_down90_pct"]) == ("32.3", "0.0", "0.0", "0.0"), r
    # more of breadth's cards down 5%+ over 30 days than up, in a market still warm (15 of 52 up)
    down = [card(asset_id=f"D{i}", set=f"Down {i}", subject=f"T{i}", price_chg_30d_pct="-8", volume_1y="6") for i in range(16)]
    up = [card(asset_id=f"U{i}", set=f"Up {i}", subject=f"W{i}", price_chg_30d_pct="12", volume_1y="6") for i in range(5)]
    listed, ledger, stats = run([card()] + down + up)
    r = [x for x in listed if x["asset_id"] == "A"][0]
    assert stats["market_cooling"] and stats["regime"] == "warm", stats
    assert r["avoid_flags"] == "market_cooling" and r["market_down30_pct"] == f"{100 * 16 / 52:.1f}" and r["market_up30_pct"] == f"{100 * 15 / 52:.1f}"
    assert r["reason"].startswith("market cooling: 31% of liquid cards down 5%+ over 30 d vs 29% up, 0% vs 0% over 90 d"), r["reason"]
    assert r["p_win_180"] == f"{bl.shrunk_rate(500, 0.40, 0.42):.3f}", "a warning, not a cut: p_win unchanged"
    assert all("market_cooling" in x["avoid_flags"].split() for x in listed), "market-wide: on every listed row"
    led = [x for x in ledger if x["asset_id"] == "A"][0]
    assert "market_cooling" in led["flags"].split() and led["market_down30_pct"] == r["market_down30_pct"]
    # over 90 days only, with breadth's filter: a cheap or thin card's fall does not count
    down90 = [card(asset_id=f"N{i}", set=f"N {i}", subject=f"U{i}", price_chg_90d_pct="-30", volume_1y="6") for i in range(3)]
    cheap = [card(asset_id=f"C{i}", set=f"C {i}", subject=f"V{i}", price_chg_90d_pct="-30", median_last_3="100", volume_1y="6") for i in range(5)]
    listed, _, stats = run([card(price_chg_90d_pct="20")] + down90 + cheap)
    assert stats["market_cooling"] and stats["market"]["down90"] == 3 / 34 and stats["market"]["up90"] == 1 / 34, stats["market"]


def test_market_moves_v02():
    import buy_features as bf
    en = [feat(asset_id="A", mom30=0.10, mom90=-0.20), feat(asset_id="B", mom30=-0.06, mom90=float("nan")), feat(asset_id="C", mom30=None, mom90=0.30),
          feat(asset_id="D", mom30=-0.5, mom90=-0.5, ref=100.0), feat(asset_id="E", mom30=-0.5, mom90=-0.5, vol365=2)]
    m = bf.market_moves(en)
    assert m == {"n": 3, "up30": 1 / 3, "down30": 1 / 3, "up90": 1 / 3, "down90": 1 / 3}, "D under $150 and E under 3 sales a year are not breadth's cards"
    assert bf.market_moves([feat(ref=100.0)]) == {}
    cards = [card(asset_id=a) for a in "ABC"]
    feats = {"A": feat(asset_id="A", mom30=-0.1), "B": feat(asset_id="B", mom30=-0.1), "C": feat(asset_id="C", mom30=0.1, mom90=-0.1)}
    listed, _, stats = bl.compute(cards, {}, rates(), {}, date(2026, 10, 26), "now", model=v02_model(), feats=feats)
    assert stats["market_cooling"] and listed and all(r["avoid_flags"] == "market_cooling" and r["market_down30_pct"] == "66.7" for r in listed), listed


def test_entry_price_and_target():
    listed, ledger, _ = run([card(clean_last_sale_price="550")])
    r = [x for x in listed if x["asset_id"] == "A"][0]
    b = bl.shrunk_rate(500, 0.40, 0.42)
    enet = b * 0.9 - (1 - b) * 0.3            # both horizons share the test cell
    assert r["entry_price"] == "550.00" and r["entry_basis"] == "last_sale"
    assert r["target_price_180"] == f"{550 * (1 + enet):.2f}" == r["target_price_90"], r
    bin_kw = {"lowest_bin_price": "500", "lowest_bin_verified_at": "2026-10-20T07:00:00Z", "lowest_bin_url": "https://www.ebay.com/itm/1"}
    r = [x for x in run([card(clean_last_sale_price="550", **bin_kw)])[0] if x["asset_id"] == "A"][0]
    assert r["entry_price"] == "500.00" and r["entry_basis"] == "live_bin", "a cheaper verified Buy It Now is the entry"
    r = [x for x in run([card(clean_last_sale_price="450", **bin_kw)])[0] if x["asset_id"] == "A"][0]
    assert r["entry_price"] == "450.00" and r["entry_basis"] == "last_sale", "a dearer Buy It Now is not"
    r = [x for x in run([card(clean_last_sale_price="550", lowest_bin_suspect="1", **bin_kw)])[0] if x["asset_id"] == "A"][0]
    assert r["entry_basis"] == "last_sale", "a suspect or unverified Buy It Now never sets the entry"
    r = [x for x in run([card()])[0] if x["asset_id"] == "A"][0]
    assert r["entry_price"] == r["entry_basis"] == r["target_price_180"] == "", "blank = unknown"
    led = [x for x in run([card(clean_last_sale_price="550")])[1] if x["asset_id"] == "A"][0]
    assert led["entry_price"] == "550.00" and led["entry_basis"] == "last_sale"


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


def v02_model(platt=True, data_end="2026-10-20"):
    """a hand-sized v0.2 json: the logistic on breadth + log_price + one cut + one era, PSA 9 odds x1.5 when has9."""
    lg = {"names": ["breadth", "breadth_gt0.40", "log_price", "stale30", "era_vintage", "breadth_x_tier_500-2k"],
          "mean": [0.3, 0.05, 2.5, 0.4, 0.2, 0.01], "scale": [0.1, 0.05, 0.5, 0.5, 0.4, 0.05], "coef": [0.8, -0.3, -0.2, 0.1, 0.05, 0.2],
          "intercept": -1.2, "breadth_lo": 0.10, "breadth_hi": 0.45, "n_train": 1000}
    h = {"logit": lg, "psa9": {"b_has9": math.log(1.5), "b_gap": 0.0, "n_train": 1000},
         "platt": {"a": 0.7, "c": 0.2, "n": 5000, "months": 30} if platt else None}
    return {"model": "v0.2", "data_end": data_end, "blend_logit_eps": 1e-4, "offset_logit_eps": 1e-3, "term_or_clip": [0.5, 2.0],
            "term_product_clip": [1 / 3, 3.0], "breadth_center": 0.25, "horizons": {"90": h, "180": h}}


def feat(**kw):
    """one card's history/buy_features.py row (fractions; blank = None)."""
    f = {"asset_id": "A", "ref": 600.0, "log_price": math.log10(600.0), "mom30": 0.02, "mom90": 0.1, "mom365": 0.5, "vol30": 1, "vol90": 3, "vol180": 5,
         "vol365": 8, "days_since_sale": 25.0, "band_z": 0.3, "breadth": 0.38, "char_jp_mom30": None, "set_price_q": 0.6, "set_lead_mom90": 0.2,
         "char_mom30": 0.05, "char_n": 4, "subject": "Mewtwo", "era": "vintage", "tier": "500-2k", "liq": "2-3", "regime": "warm",
         "stale30": False, "jp_heat": False, "band_high": False, "tail_lead": False, "thin_history": False, "avoid": [], "has9": 0.0, "rev_zc": 0.0}
    f.update(kw)
    return f


def test_v02_loads_only_with_matching_tables():
    with tempfile.TemporaryDirectory() as d:
        m, why = bl.load_v02(d, "2026-10-20")
        assert m is None and why == f"no {bl.V02_JSON}"
        (Path(d) / bl.V02_JSON).write_text(json.dumps(v02_model(data_end="2026-10-22")))
        m, why = bl.load_v02(d, "2026-10-20")
        assert m is None and "2026-10-22" in why, "fitted on other tables: v0.1 scores"
        m, why = bl.load_v02(d, "2026-10-22")
        assert m is not None and why == ""


def test_v02_logit_inputs_follow_the_names():
    names = ["era_vintage", "era_wotc", "tier_500-2k", "liq_2-3", "stale30", "log_price", "log_days_since_sale", "mom30", "mom30_blank",
             "mom90", "band_z", "band_z_blank", "breadth", "breadth_gt0.25", "breadth_gt0.40", "breadth_x_tier_500-2k", "breadth_x_era_wotc"]
    x = dict(zip(names, bl.logit_inputs(names, feat(mom30=None, mom90=2.5, band_z=-4.0, days_since_sale=400.0, breadth=0.60), 0.10, 0.45, 0.25)))
    assert x["era_vintage"] == 1 and x["era_wotc"] == 0 and x["tier_500-2k"] == 1 and x["liq_2-3"] == 1 and x["stale30"] == 0
    assert x["mom30"] == 0 and x["mom30_blank"] == 1 and x["mom90"] == 1.0, "blank -> 0 + flag; clipped at +1"
    assert x["band_z"] == -3.0 and x["band_z_blank"] == 0
    assert abs(x["log_days_since_sale"] - math.log1p(180)) < 1e-12, "days capped at 180"
    assert x["breadth"] == 0.45 and abs(x["breadth_gt0.25"] - 0.20) < 1e-12 and abs(x["breadth_gt0.40"] - 0.05) < 1e-12, "breadth clipped to the training range"
    assert abs(x["breadth_x_tier_500-2k"] - 0.20) < 1e-12 and x["breadth_x_era_wotc"] == 0
    try:
        bl.logit_inputs(["no_such_input"], feat(), 0.1, 0.45, 0.25)
        assert False, "an unknown input must fail loudly"
    except KeyError:
        pass


def test_v02_score_arithmetic():
    m = v02_model()
    s = bl.score_cell(rates(), {}, 180, "warm", "vintage", "500-2k", "2-3", [])
    f = feat(has9=1.0)
    v = bl.score_v02(m, 180, f, s)
    assert abs(v["p_cells"] - bl.odds_apply(s["p"], 1.5)) < 1e-12, "has9: the cells' odds x exp(b_has9)"
    lg = m["horizons"]["180"]["logit"]
    xs = bl.logit_inputs(lg["names"], f, lg["breadth_lo"], lg["breadth_hi"], 0.25)
    eta = lg["intercept"] + sum(c * (x - mu) / sd for c, x, mu, sd in zip(lg["coef"], xs, lg["mean"], lg["scale"]))
    assert abs(v["p_logit"] - 1 / (1 + math.exp(-eta))) < 1e-12
    z = 0.5 * math.log(v["p_logit"] / (1 - v["p_logit"])) + 0.5 * math.log(v["p_cells"] / (1 - v["p_cells"]))
    assert abs(v["p"] - 1 / (1 + math.exp(-(0.7 * z + 0.2)))) < 1e-12, "Platt on the mean of the two logits"
    assert v["lo"] <= v["p"] <= v["hi"] and (v["lo"] - s["lo"]) * (v["p"] - s["p"]) > 0, "the band moves with p"
    assert abs(v["enet"] - (v["p"] * s["win_net"] + (1 - v["p"]) * s["loss_net"])) < 1e-12
    raw = bl.score_v02(v02_model(platt=False), 180, f, s)
    assert abs(raw["p"] - 1 / (1 + math.exp(-z))) < 1e-12, "no Platt pair (thin history): the raw blend"
    assert bl.score_v02(m, 180, feat(has9=0.0), s)["p_cells"] == s["p"], "no PSA 9 reference: the cells as they are"


def test_v02_compute_scores_the_panel_universe():
    m = v02_model()
    cards = [card(asset_id="A"), card(asset_id="B"), card(asset_id="C"), card(asset_id="D", median_last_3="700", sales_total="2")]
    feats = {"A": feat(asset_id="A"), "B": feat(asset_id="B", avoid=["dip"]), "C": feat(asset_id="C", era="nan")}
    listed, ledger, stats = bl.compute(cards, {}, rates(), {}, date(2026, 10, 26), "now", model=m, feats=feats)
    assert sorted(r["asset_id"] for r in listed) == ["A", "D"], "B avoided by the panel's dip flag, C no era, D back as an outside_model row"
    r = [x for x in listed if x["asset_id"] == "A"][0]
    assert r["model"] == "v0.2" and r["p_cells_180"] and r["p_logit_180"] and r["p_cells_90"] and r["regime"] == "warm" and r["breadth"] == "0.380"
    assert "blended and calibrated to" in r["reason"] and "logistic" in r["reason"] and "((" not in r["reason"] and "outside_model" not in r["avoid_flags"]
    assert stats["model"] == "v0.2" and stats["model_universe"] == 2 and stats["outside_universe"] == 1 and stats["listed_outside"] == 1
    led = {x["asset_id"]: x for x in ledger}
    assert set(led) == {"A", "D"} and led["A"]["has9"] == "0" and led["A"]["chg90"] == "10.00", "the panel's changes, in percent"
    # D (Sid 2026-10-28): outside the model's universe, scored by v0.1's cells in tonight's (v0.2) regime -- cards.csv's breadth here is 0, flat
    d = [x for x in listed if x["asset_id"] == "D"][0]
    assert d["avoid_flags"] == "outside_model" and d["model"] == "v0.2" and d["regime"] == "warm" and d["breadth"] == "0.380" and d["cell"] == "warm|vintage|500-2k|2-3"
    assert d["p_win_180"] == f"{bl.shrunk_rate(500, 0.40, 0.42):.3f}" and d["p_cells_180"] == "" and d["p_logit_180"] == "", "the v0.1 score, no v0.2 parts"
    assert d["reason"].startswith("outside the v0.2 model: 2 clean PSA 10 sales on record (it is fitted on cards with 4+), so scored by v0.1's cells and cuts, not calibrated; base 41%"), d["reason"]
    assert "outside_model" in led["D"]["flags"].split() and led["D"].get("has9", "") == "" and led["D"]["chg90"] == "", "no PSA 9 terms; cards.csv has no 90 d change here"
    warm = [card(asset_id=f"W{i}", price_chg_30d_pct="10" if i < 3 else "0") for i in range(10)]    # cards.csv breadth 30%: warm
    l1, _, s1 = bl.compute(warm, {}, rates(), {}, date(2026, 10, 26), "now")
    assert s1["model"] == bl.MODEL and l1 and all(r["model"] == bl.MODEL and "p_cells_180" not in r for r in l1), "no model: v0.1 as before"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
