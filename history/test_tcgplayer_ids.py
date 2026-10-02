#!/usr/bin/env python3
"""Checks for history/tcgplayer_ids.py (2026-10-01): number and name normalising, the variant
rules, and one build over a small made-up TCGplayer catalog covering each way a row is matched,
skipped or refused. Every case is a mistake an earlier version made on the real feed.

    python3 history/test_tcgplayer_ids.py

Plain asserts, no pytest, no network."""
import csv
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tcgplayer_ids as t  # noqa: E402

SPECIES = t.base_species(["Mew", "Mewtwo", "Unown", "Unown X", "Pikachu", "Flying Pikachu", "Charizard",
                          "Yveltal", "Camerupt", "Solgaleo", "Lunala", "Entei & Raikou"])


def agree(alt_subject, tcg_product_name, strict=False):
    return t.names_agree(t.alt_name({"subject": alt_subject}), t.tcg_name({"name": tcg_product_name}), SPECIES, strict)


def test_numbers():
    assert t.norm_number("004/102") == "4"
    assert t.norm_number("H01/H32") == "H1"
    assert t.norm_number("SWSH286") == "SWSH286"
    assert t.norm_number("#21a") == "21A"
    assert t.norm_number("TG08/TG30") == "TG8"
    print("numbers ok")


def test_names():
    assert not agree("Mew", "Mewtwo (Movie Promo)"), "Mew is not Mewtwo"
    assert agree("Unown X", "Unown [X]"), "a form on the roster is not a different species"
    assert agree("Pikachuvmax P Jumbo", "Pikachu VMAX - SWSH286")
    assert agree("Lunala/Solgaleo Gx", "Solgaleo & Lunala GX (Secret)"), "tag teams in either order"
    assert agree("Megacameruptex", "M Camerupt EX - XY198")
    assert agree("Mew Gold Star", "Mew Star (Delta Species)")
    assert not agree("Galarian Sirfetch'd", "Galarian Darumaka"), "regional prefix is not the name"
    assert agree("Groudon", "Groudon Star"), "alt drops suffixes; only a bare promo number checks them"
    assert agree("Yveltal Ex", "Yveltal - XY06") and not agree("Yveltal Ex", "Yveltal - XY06", strict=True)
    print("names ok")


def test_qualifiers():
    assert t.qualifier_words({"name": "Gyarados (21)"}) == set(), "a bare number is not a variant"
    assert t.qualifier_words({"name": "Pichu - 28/123 (Prerelease) [Staff]"}) == {"prerelease", "staff"}
    assert t.qualifier_words({"name": "Houndoom (Poke Ball Pattern)"}) == {"poke", "ball"}
    print("qualifiers ok")


UPDATED = "2026-10-01T20:05:57+0000"
GROUPS = [  # groupId, name, publishedOn, products: (productId, name, number, rarity)
    (604, "Base Set", "1999-01-09", [(1, "Charizard", "004/102", "Holo Rare"), (2, "Blastoise", "002/102", "Holo Rare"),
                                     (3, "Venusaur", "015/102", "Holo Rare")]),
    (1663, "Base Set (Shadowless)", "1999-01-09", [(11, "Charizard", "004/102", "Holo Rare"), (12, "Blastoise", "002/102", "Holo Rare"),
                                                   (13, "Venusaur", "015/102", "Holo Rare")]),
    (1374, "Jungle", "1999-06-16", [(15, "Scyther", "010/064", "Holo Rare")]),
    (23237, "SV: Scarlet & Violet 151", "2023-09-22", [(21, "Nidorina", "030/165", "Uncommon"), (22, "Mew ex - 151/165", "151/165", "Double Rare"),
                                                       (23, "Mewtwo", "150/165", "Rare")]),
    (1418, "WoTC Promo", "1999-07-01", [(31, "Mewtwo (Movie Promo)", "3", "Promo")]),
    (2374, "Miscellaneous Cards & Products", "2026-10-01T20:00:06Z", [(41, "Wartortle - 50/112 (Prerelease)", "50/112", "Promo"),
                                                                     (42, "Ancient Mew", "1", "Promo"),
                                                                     (43, "Ampharos - 40/114 (XY Steam Siege)", "40/114", "Rare")]),
    (1419, "EX FireRed & LeafGreen", "2004-09-01", [(51, "Wartortle", "50/112", "Uncommon")]),
    (1815, "XY - Steam Siege", "2016-08-03", [(55, "Ampharos", "40/114", "Rare")]),
    (22880, "Prize Pack Series Cards", "2022-11-30", [(61, "Mew ex - 151/165", "151/165", "Double Rare")]),
    (2545, "SWSH: Sword & Shield Promo Cards", "2019-11-15", [(71, "Special Delivery Charizard - SWSH075", "SWSH075", "Promo")]),
]
ALT = [  # asset_id, year, set, variety, subject, number, expected tcgplayer_id (None = no row)
    ("bs1", "1999", "Pokemon Base Set", "", "Charizard", "4", 1),
    ("bs2", "1999", "Pokemon Base Set", "", "Blastoise", "2", 2),
    ("bs3", "1999", "Pokemon Base Set", "", "Venusaur", "15", 3),
    ("fe1", "1999", "Pokemon Base Set", "1st Edition", "Charizard", "4", 11),   # 1st Edition is TCGplayer's Shadowless set
    ("fe2", "1999", "Pokemon Base Set", "1st Edition", "Blastoise", "2", 12),
    ("fe3", "1999", "Pokemon Base Set", "1st Edition", "Venusaur", "15", 13),
    ("jg1", "1999", "Pokemon Jungle", "", "Scyther", "10", 15),               # "Pokemon Jungle" is also a Japanese set name
    ("sv1", "2023", "Pokemon Scarlet and Violet 151", "", "Mew Ex", "151", 22),  # not the Prize Pack reprint
    ("sv2", "2023", "Pokemon Scarlet and Violet 151", "", "Mewtwo", "150", 23),
    ("sv3", "2023", "Pokemon Scarlet and Violet 151", "", "Nidorina", "30", 21),
    ("pp1", "2023", "Pokemon Scarlet and Violet 151", "Play! Prize Pack", "Mew Ex", "151", 61),
    # Pokedex-numbered products that aren't TCGplayer singles. A year doesn't always give them away
    # (Japanese Clay Burst is 2023, like SV 151), so these keep a fitting year: only the skip
    # rules stop them
    ("ch1", "2023", "Pokemon Chrome", "", "Nidorina", "30", None),
    ("jp1", "2023", "Pokemon Scarlet and Violet Clay Burst", "", "Mewtwo", "150", None),   # Japanese-only set
    ("pr1", "2004", "Pokemon Ex Fire Red and Leaf Green", "Prerelease", "Wartortle", "50", 41),
    ("fr1", "2004", "Pokemon Ex Fire Red and Leaf Green", "", "Wartortle", "50", 51),
    ("st1", "2016", "Pokemon XY Steam Siege", "Holo", "Ampharos", "40", 55),  # "(XY Steam Siege)" is a set, not a variant
    ("cs1", "2010", "Pokemon Heartgold Soulsilver", "Cosmos Holo", "Wartortle", "50", None),  # names a print TCGplayer lacks
    ("mw1", "2007", "Organized Play Series 5", "", "Mew", "3", None),         # only candidate is Mewtwo
    ("am1", "2000", "Pokemon Promo Movie 2000", "", "Ancient Mew", "", 42),   # no number: exact, rare name
    ("sd1", "2022", "Pokemon Black Star Promo", "Holo", "Special Delivery Charizard", "075", 71),   # alt drops "SWSH"
]


def test_build():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "last-updated.txt").write_text(UPDATED)
        (d / "groups_jp.json").write_text(json.dumps({"results": [{"name": "SV2D: Clay Burst"}, {"name": "Pokemon Jungle"}]}))
        (d / "groups.json").write_text(json.dumps({"results": [{"groupId": g, "name": n, "publishedOn": pub} for g, n, pub, _ in GROUPS]}))
        for g, _, _, prods in GROUPS:
            (d / f"{g}.json").write_text(json.dumps({"results": [
                {"productId": pid, "name": name, "extendedData": [{"name": "Number", "value": num}, {"name": "Rarity", "value": rar}]}
                for pid, name, num, rar in prods]}))
        feed, chars, out = d / "cards.csv", d / "characters.csv", d / "out.csv"
        with open(feed, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["asset_id", "year", "set", "variety", "subject", "card_number", "card_name"])
            for a, year, s, v, subj, num, _ in ALT:
                w.writerow([a, year, s, v, subj, num, " ".join(x for x in (year, s, v, subj, f"#{num}" if num else "") if x)])
        chars.write_text("character\n" + "\n".join(["Charizard", "Blastoise", "Venusaur", "Scyther", "Mew", "Mewtwo", "Nidorina",
                                                    "Wartortle", "Ampharos", "Unown", "Unown X"]) + "\n")
        t.build(feed=feed, src=d, out=out, characters=chars)
        got = {r["asset_id"]: int(r["tcgplayer_id"]) for r in csv.DictReader(open(out))}
        rarity = {r["asset_id"]: r["tcgplayer_rarity"] for r in csv.DictReader(open(out))}
        for a, *_, want in ALT:
            assert got.get(a) == want, f"{a}: want {want}, got {got.get(a)}"
        assert rarity["bs1"] == "Holo Rare"
        assert next(r for r in csv.DictReader(open(out)) if r["asset_id"] == "sd1")["image_url"] == \
            "https://tcgplayer-cdn.tcgplayer.com/product/71_in_800x800.jpg"
    print("build ok")


if __name__ == "__main__":
    test_numbers()
    test_names()
    test_qualifiers()
    test_build()
