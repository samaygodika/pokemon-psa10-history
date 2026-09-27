#!/usr/bin/env python3
"""Checks for history/flags.py (2026-09-27): the card-type port, subject matching, the buy-rule
flags, the category median, and append-once behaviour.

    python3 history/test_flags.py

Plain asserts, no pytest."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import flags  # noqa: E402


def card(**kw):
    base = {"asset_id": kw.get("asset_id", "A"), "card_name": "", "set": "", "subject": "", "year": "2008", "card_number": "1",
            "price_chg_30d_pct": "", "psa9_price_chg_30d_pct": "", "median_last_3": "", "last_sale_price": "",
            "psa9_median_last_3": "", "psa9_last_sale_price": "", "pop_at_grade": "", "pop_at_grade_9": ""}
    base.update(kw)
    return base


def test_card_type_port():
    assert flags.card_type(card(card_name="2008 Pokemon Diamond and Pearl Legends Awakened Holo Mewtwo Lv X #144", year="2008")) == "LV.X"
    assert flags.card_type(card(card_name="2009 Pokemon Platinum Holo Palkia G Lv X #125", year="2009")) == "LV.X"
    assert flags.card_type(card(card_name="2008 Pokemon Lv.X Collection Box", year="2008")) == ""
    assert flags.card_type(card(card_name="2005 Pokemon Ex Deoxys Holo Rayquaza Gold Star #107", year="2005")) == "Gold Star"
    assert flags.card_type(card(card_name="2011 Pokemon Call of Legends Holo Lugia Legend #113", year="2011")) == "HGSS Prime/LEGEND"
    assert flags.card_type(card(card_name="2011 Pokemon HGSS Triumphant Holo Celebi Prime #92", year="2011")) == "HGSS Prime/LEGEND"
    assert flags.card_type(card(card_name="2002 Pokemon Neo Destiny Holo Shining Charizard #107", year="2002")) == "Neo Shining"
    assert flags.card_type(card(card_name="2003 Pokemon Skyridge Holo Charizard #146", set="Pokemon Skyridge", card_number="146", year="2003")) == "e-card Crystal"
    assert flags.card_type(card(card_name="2003 Pokemon Skyridge Holo Gengar #H9", set="Pokemon Skyridge", card_number="H9", year="2003")) == ""
    assert flags.card_type(card(card_name="1999 Pokemon Base Set 1st Edition Holo Charizard #4", year="1999")) == "WOTC 1st Edition"
    assert flags.card_type(card(card_name="1999 Pokemon Base Set Holo Charizard #4", year="1999")) == "WOTC other"
    assert flags.card_type(card(card_name="2004 Pokemon Ex Team Rocket Returns Holo Rocket's Mewtwo Ex #99", subject="Rocket's Mewtwo Ex", year="2004")) == "EX-era ex"
    assert flags.card_type(card(card_name="2023 Pokemon Scarlet and Violet Mew Ex #151", subject="Mew Ex", year="2023")) == "Modern ex (2023+)"
    assert flags.card_type(card(card_name="2021 Pokemon Sword and Shield Evolving Skies Umbreon VMAX #215", year="2021")) == "Modern V/VMAX/VSTAR"
    assert flags.card_type(card(card_name="2016 Pokemon XY Evolutions Charizard Full Art #12", year="2016")) == "Modern alt art / illustration"
    assert flags.card_type(card(card_name="2019 Pokemon Sun and Moon Black Star Promo Mewtwo #SM77", year="2019")) == "Promo"
    print("card types ok")


def test_subject_matching():
    names = ["Mewtwo", "Ho-Oh", "Mr. Mime", "Palkia"]
    assert flags.subject_matches("Mewtwo Lv X", names) == "Mewtwo"
    assert flags.subject_matches("Palkia G Lv X", names) == "Palkia"
    assert flags.subject_matches("Ho Oh", names) == "Ho-Oh"
    assert flags.subject_matches("Mr Mime", names) == "Mr. Mime"
    assert flags.subject_matches("Mew", names) is None, "Mew is not Mewtwo"
    print("subject matching ok")


def test_flags_and_category():
    top50, blue = ["Mewtwo", "Palkia", "Pikachu"], ["Mewtwo"]
    lvx = "Lv X #1"
    cards = [
        # six LV.X cards so the category has a median: five up a lot, one flat
        card(asset_id="m", card_name=f"2008 Pokemon Legends Awakened Holo Mewtwo {lvx}", subject="Mewtwo Lv X", price_chg_30d_pct="316.7",
             psa9_price_chg_30d_pct="0", median_last_3="150000", psa9_median_last_3="2300"),
        card(asset_id="p", card_name=f"2009 Pokemon Platinum Holo Palkia G {lvx}", subject="Palkia G Lv X", price_chg_30d_pct="461.4",
             psa9_price_chg_30d_pct="44", median_last_3="48000", psa9_median_last_3="900"),
        card(asset_id="d", card_name=f"2008 Pokemon Great Encounters Holo Darkrai {lvx}", subject="Darkrai Lv X", price_chg_30d_pct="80",
             psa9_price_chg_30d_pct="5", median_last_3="11600"),
        card(asset_id="g", card_name=f"2008 Pokemon Stormfront Holo Gliscor {lvx}", subject="Gliscor Lv X", price_chg_30d_pct="60", psa9_price_chg_30d_pct="", median_last_3="500"),
        card(asset_id="h", card_name=f"2008 Pokemon Stormfront Holo Heatran {lvx}", subject="Heatran Lv X", price_chg_30d_pct="55", median_last_3="400"),
        card(asset_id="i", card_name=f"2008 Pokemon Stormfront Holo Infernape {lvx}", subject="Infernape Lv X", price_chg_30d_pct="2", median_last_3="300"),
        # a top-50 card of another type, PSA 10 up 120% but PSA 9 up too: not the rule
        card(asset_id="k", card_name="2019 Pokemon Sun and Moon Black Star Promo Pikachu #SM1", subject="Pikachu", year="2019",
             price_chg_30d_pct="120", psa9_price_chg_30d_pct="40", median_last_3="200"),
        # a top-50 card whose PSA 9 change is unknown: up, but not "flat" (unknown is not flat)
        card(asset_id="u", card_name="1999 Pokemon Base Set Holo Pikachu #58", subject="Pikachu", year="1999", price_chg_30d_pct="70", median_last_3="50"),
    ]
    rows, moves = flags.compute(cards, top50, blue, tracked={"d"}, data_date="2026-09-27")
    by = {r["asset_id"]: r for r in rows}
    m = set(by["m"]["flags"].split())
    assert {"top50", "blue_chip", "psa10_up_50", "psa10_up_100", "psa9_flat", "buy_rule_50", "buy_rule_100", "cat_hot_15", "cat_hot_30", "cat_hot_50"} <= m, m
    assert "cat_hot_70" in m and "cat_hot_100" not in m, "LV.X median of (2, 55, 60, 80, 316.7, 461.4) is 70"
    p = set(by["p"]["flags"].split())
    assert "psa9_flat" not in p and "buy_rule_50" not in p and "top50" in p and "blue_chip" not in p
    d = set(by["d"]["flags"].split())
    assert d == {"psa10_up_50", "psa9_flat", "cat_hot_15", "cat_hot_30", "cat_hot_50", "cat_hot_70", "tracked"}, d   # Darkrai not top-50 here: no buy_rule
    assert "g" not in by and "h" not in by and "i" not in by, "non-top-50, untracked cards are not logged"
    k = set(by["k"]["flags"].split())
    assert "psa10_up_100" in k and "psa9_flat" not in k and "buy_rule_50" not in k
    u = set(by["u"]["flags"].split())
    assert "psa9_flat" not in u and "buy_rule_50" not in u
    assert by["m"]["tier"] == ">= $10K" and by["p"]["tier"] == ">= $10K" and by["k"]["tier"] == "< $1K"
    lvx_move = next(mv for mv in moves if mv["card_type"] == "LV.X")
    assert lvx_move["cards_with_chg"] == 6 and lvx_move["median_chg_30d_pct"] == "70.0" and lvx_move["share_up_50"] == "0.833"
    assert lvx_move["top50_median_chg_30d_pct"] == "", "only 2 top-50 LV.X cards: below MIN_GROUP_CARDS"
    print("flags ok:", sorted(by))
    return rows, moves


def test_append_once(rows, moves):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "f.csv"
        key = lambda r: (r["data_date"], r["asset_id"])
        assert flags.append_rows(p, flags.FLAG_COLS, rows, key) == len(rows)
        assert flags.append_rows(p, flags.FLAG_COLS, rows, key) == 0
        rows2 = [dict(r, data_date="2026-09-28") for r in rows]
        assert flags.append_rows(p, flags.FLAG_COLS, rows2, key) == len(rows)
        assert len(flags.read_csv(p)) == 2 * len(rows)
    print("append-once ok")


if __name__ == "__main__":
    test_card_type_port()
    test_subject_matching()
    r, m = test_flags_and_category()
    test_append_once(r, m)
    print("all flag checks passed")
