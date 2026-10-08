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
    (23266, "SV: Scarlet & Violet Trainer Items", "2023-03-31", [(81, "Ultra Ball - 196/198", "196/198", "Uncommon"),
                                                                (82, "Basic Fire Energy", "002", "Common")]),
    (23330, "My First Battle", "2023-07-01", [(91, "Pikachu", None, "Unconfirmed"), (92, "My First Battle [Pikachu & Bulbasaur]", None, None),
                                              (93, "Code Card - 151 Booster Pack [Code Card]", None, "Code Card")]),
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
# TCGplayer's printings (prices subTypeName) per set and product, unlisted product "Normal";
# Miscellaneous Cards & Products has no prices file. Card Type per product, default "Fire".
PRINTINGS = {604: {1: ["Holofoil"], 2: ["Holofoil"], 3: ["Holofoil"]},
             1663: {11: ["Unlimited Holofoil", "1st Edition Holofoil"], 12: ["Unlimited Holofoil", "1st Edition Holofoil"],
                    13: ["Unlimited Holofoil", "1st Edition Holofoil"]},
             23237: {21: ["Normal", "Reverse Holofoil"]}, 23266: {}, 1374: {}, 1418: {}, 1419: {}, 1815: {}, 22880: {},
             2545: {}}
CARD_TYPES = {81: "Item", 82: "Basic Fire Energy"}
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
    ("jpp", "2015", "Pokemon XY Promo", "", "Pikachu", "107XYP", None),     # Japanese promo numbering
    ("tm1", "2000", "Pokemon Movie", "", "Three Treasures", "52", None),    # Topps movie card, not the TCG
]


def test_build():
    with tempfile.TemporaryDirectory() as d:
        out, cat, sets = build_fixture(Path(d))
        got = {r["asset_id"]: int(r["tcgplayer_id"]) for r in csv.DictReader(open(out))}
        rarity = {r["asset_id"]: r["tcgplayer_rarity"] for r in csv.DictReader(open(out))}
        for a, *_, want in ALT:
            assert got.get(a) == want, f"{a}: want {want}, got {got.get(a)}"
        assert rarity["bs1"] == "Holo Rare"
        assert next(r for r in csv.DictReader(open(out)) if r["asset_id"] == "sd1")["image_url"] == \
            "https://tcgplayer-cdn.tcgplayer.com/product/71_in_800x800.jpg"
    print("build ok")


def test_catalog():
    """card_catalog.csv / tcgplayer_sets.csv (2026-10-02): every English row once, every unmatched
    TCGplayer single once, each with the status the app decides on."""
    with tempfile.TemporaryDirectory() as d:
        out, cat, sets = build_fixture(Path(d))
        rows = list(csv.DictReader(open(cat)))
        by_asset = {r["asset_id"]: r for r in rows if r["asset_id"]}
        assert set(by_asset) == {a for a, *_ in ALT}, "every English row exactly once"
        assert len(by_asset) == sum(1 for r in rows if r["asset_id"])
        for a, *_, want in ALT:
            r = by_asset[a]
            if want:
                assert (r["status"], r["tcgplayer_id"]) == ("linked", str(want)), (a, r)
        assert (by_asset["ch1"]["status"], by_asset["ch1"]["how"]) == ("not_english", "not_on_tcgplayer")
        assert (by_asset["jp1"]["status"], by_asset["jp1"]["how"]) == ("not_english", "not_on_tcgplayer")
        assert (by_asset["jpp"]["status"], by_asset["jpp"]["how"]) == ("not_english", "not_english_tcg")
        assert (by_asset["tm1"]["status"], by_asset["tm1"]["how"]) == ("not_english", "not_english_tcg")
        assert (by_asset["cs1"]["status"], by_asset["cs1"]["how"]) == ("alt_only", "variant_not_found")
        assert (by_asset["mw1"]["status"], by_asset["mw1"]["how"]) == ("alt_only", "no_candidate")
        assert by_asset["mw1"]["rarity"] == "", "an alt.xyz-only card's rarity is unknown, not guessed"
        assert by_asset["fe1"]["print_run"] == "1st Edition" and by_asset["bs1"]["print_run"] == ""
        assert by_asset["bs1"]["year"] == "1999" and by_asset["bs1"]["set"] == "Base Set"
        only = {r["tcgplayer_id"]: r for r in rows if r["status"] == "tcgplayer_only"}
        matched = {str(w) for *_, w in ALT if w}
        assert not set(only) & matched, "a matched product is never also TCGplayer-only"
        assert only["31"]["year"] == "1999", "WoTC Promo: TCGplayer's own release year"
        assert only["43"]["year"] == "", "Miscellaneous: undated, too few cards to learn a year"
        s = {r["name"]: r for r in csv.DictReader(open(sets))}
        assert (s["WoTC Promo"]["year"], s["WoTC Promo"]["year_source"]) == ("1999", "published")
        assert s["Miscellaneous Cards & Products"]["year_source"] == "multi_year"
        assert s["Miscellaneous Cards & Products"]["linked_cards"] == "2"
        # printings + card_type (2026-10-05, Sid): the product's, on linked and TCGplayer-only rows
        assert by_asset["fe1"]["printings"] == "1st Edition Holofoil|Unlimited Holofoil", "sorted, '|'-joined"
        assert by_asset["bs1"]["printings"] == "Holofoil" and by_asset["bs1"]["card_type"] == "Pokemon"
        assert by_asset["sv3"]["printings"] == "Normal|Reverse Holofoil"
        assert (by_asset["mw1"]["printings"], by_asset["mw1"]["card_type"]) == ("", ""), "alt.xyz-only: unknown"
        assert by_asset["am1"]["printings"] == "", "no prices file for the set: blank, not guessed"
        assert only["81"]["card_type"] == "Trainer" and only["82"]["card_type"] == "Energy"
        assert only["81"]["printings"] == "Normal"
        # a single with no number is listed, never matched; sealed product and code cards aren't (2026-10-05)
        assert (only["91"]["how"], only["91"]["number"], only["91"]["name"]) == ("unnumbered", "", "Pikachu")
        assert "92" not in only and "93" not in only, "sealed deck / digital code card"
    print("catalog ok")


def test_card_type():
    def ct(value, name="X", hp=None):
        ed = ([{"name": "Card Type", "value": value}] if value else []) + ([{"name": "HP", "value": hp}] if hp else [])
        return t.card_type({"name": name, "extendedData": ed})
    for v in ("Fire", "Darkness Metal", "Fighting/Darkness", "Lighnting", "Dark", "Colorless Psychic", "Normal"):
        assert ct(v) == "Pokemon", v
    for v in ("Supporter", "Item", "Trainer", "Stadium", "Tool", "Pokémon Tool", "Trainer — Item", "Trainer - Supporter",
              "Technical Machine", "TM", "Rocket's Secret Machine", "Supporter - Item"):
        assert ct(v) == "Trainer", v
    for v in ("Basic Energy", "Special Energy", "Energy", "Basic Lightning Energy", "Special Rainbow Energy"):
        assert ct(v) == "Energy", v
    assert ct(None, "Basic Fire Energy - MEE 002 (Cosmos Holo)") == "Energy", "no type: an Energy by its name"
    assert ct(None, "Mewtwo", hp="130") == "Pokemon", "no type: a card with HP"
    assert ct(None, "Brock's Scouting - 179/159") == "", "no type, no HP: unknown"
    assert ct("Something New") == "", "a type we don't know: unknown, not guessed"
    print("card type ok")


def test_set_years():
    undated = "2026-10-01T20:00:06Z"
    groups = {1: {"name": "POP Series 5", "year": None, "publishedOn": undated},
              2: {"name": "Nintendo Promos", "year": None, "publishedOn": undated},
              2776: {"name": "First Partner Pack", "year": None, "publishedOn": undated},
              3: {"name": "Jungle", "year": 1999, "publishedOn": "1999-06-16"}}
    rows, decided = [], {}
    for i, (gid, year) in enumerate([(1, 2007)] * 5 + [(2, 2002), (2, 2003), (2, 2003), (2, 2005), (2, 2006)]):
        rows.append({"asset_id": f"a{i}", "year": str(year)})
        decided[f"a{i}"] = ({"group": {"groupId": gid}}, "unique")
    s = {r["name"]: r for r in t.set_years(groups, rows, decided)}
    assert (s["POP Series 5"]["year"], s["POP Series 5"]["year_source"]) == (2007, "learned")
    assert (s["Nintendo Promos"]["year"], s["Nintendo Promos"]["year_source"]) == ("", "multi_year")
    assert (s["Nintendo Promos"]["span_from"], s["Nintendo Promos"]["span_to"]) == (2002, 2006)
    assert (s["First Partner Pack"]["year"], s["First Partner Pack"]["year_source"]) == (2021, "manual")
    assert (s["Jungle"]["year"], s["Jungle"]["year_source"]) == (1999, "published")
    print("set years ok")


def test_not_english():
    def row(name, number=""):
        return {"card_name": name, "variety": "", "card_number": number}
    assert t.not_english(row("2000 Pokemon Movie Three Treasures #52", "52")), "Topps movie card"
    assert not t.not_english(row("2000 Pokemon Promo Movie 2000 Ancient Mew")), "the English movie promo"
    assert t.not_english(row("2015 Pokemon XY Pokemon Center Promotion July Regigigas #160XYP", "160XYP"))
    assert t.not_english(row("2012 Pokemon Eevee Collection Collection File Umbreon #188/BW-P", "188/BW-P"))
    assert not t.not_english(row("2016 Pokemon Sun and Moon Black Star Promos Registeel #SM75", "SM75"))
    assert t.not_english(row("2022 Pokemon Sun and Moon Storming Emergence Radiant Mewtwo Gx Ssr #194", "194"))
    assert t.not_english(row("2018 Ultra Pokemon Gx Shiny Parallel Foil Darkrai #68", "68"))
    assert not t.not_english(row("2018 Ultra Pokemon Sun and Moon Prism Dewpider #16", "16")), "English Ultra Prism"
    assert not t.not_english(row("1999 Pokemon Base 1st Edition Thick 3-D Stamp Charizard Holo R #4", "4"))
    assert t.print_run({"card_name": "2005 Pokemon Ex Emerald Reverse Foil Torchic", "variety": "", "set": ""}) == "Reverse Holo"
    assert t.print_run({"card_name": "1999 Pokemon Base Set Shadowless Charizard", "variety": "", "set": ""}) == "Shadowless"
    print("not english ok")


def test_english_tcg():
    """coverage.english_tcg is the catalog's English filter: Japanese stays out however alt.xyz spells it."""
    import coverage
    row = lambda name: {"card_name": name, "set": "", "variety": ""}
    for name in ("2009 Pokemon Charizard Half Deck Japaese 1st Edition Holo Charizard G #001",
                 "2025 Pokemon Mega Symphonia Japaneese Art Rare Shedinja #072",
                 "2016 Pokemon Japanesse Mythical and Legendary Dream Shine Collection 1st Edition Moltres #5",
                 "1998 Pokemon Japanesea Red Green Gift Set Baby Pikachu #25",
                 "1999 Pokemon Japanese Base Set Charizard #6"):
        assert not coverage.english_tcg(row(name)), name
    for name in ("1999 Pokemon Base Set Charizard #4", "2009 Pokemon Platinum Arceus Holo Charizard #1",
                 "2000 Pokemon Neo Genesis 1st Edition Lugia #9"):
        assert coverage.english_tcg(row(name)), name


def build_fixture(d):
    """the made-up TCGplayer catalog + feed above, built into d; -> (ids, catalog, sets) paths"""
    d = Path(d)
    (d / "last-updated.txt").write_text(UPDATED)
    (d / "groups_jp.json").write_text(json.dumps({"results": [{"name": "SV2D: Clay Burst"}, {"name": "Pokemon Jungle"}]}))
    (d / "groups.json").write_text(json.dumps({"results": [{"groupId": g, "name": n, "publishedOn": pub} for g, n, pub, _ in GROUPS]}))
    for g, _, _, prods in GROUPS:
        (d / f"{g}.json").write_text(json.dumps({"results": [
            {"productId": pid, "name": name, "extendedData": [{"name": k, "value": v} for k, v in
                                                              (("Number", num), ("Rarity", rar), ("Card Type", CARD_TYPES.get(pid, "Fire")))
                                                              if v is not None]}
            for pid, name, num, rar in prods]}))
        if g in PRINTINGS:
            (d / f"{g}.prices.json").write_text(json.dumps({"results": [
                {"productId": pid, "subTypeName": sub} for pid, _, _, _ in prods for sub in PRINTINGS[g].get(pid, ["Normal"])]}))
    feed, chars, out = d / "cards.csv", d / "characters.csv", d / "out.csv"
    with open(feed, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["asset_id", "year", "set", "variety", "subject", "card_number", "card_name"])
        for a, year, s, v, subj, num, _ in ALT:
            w.writerow([a, year, s, v, subj, num, " ".join(x for x in (year, s, v, subj, f"#{num}" if num else "") if x)])
    chars.write_text("character\n" + "\n".join(["Charizard", "Blastoise", "Venusaur", "Scyther", "Mew", "Mewtwo", "Nidorina",
                                                "Wartortle", "Ampharos", "Unown", "Unown X"]) + "\n")
    cat, sets = d / "card_catalog.csv", d / "tcgplayer_sets.csv"
    t.build(feed=feed, src=d, out=out, characters=chars, catalog=cat, sets=sets)
    return out, cat, sets


if __name__ == "__main__":
    test_numbers()
    test_names()
    test_qualifiers()
    test_build()
    test_catalog()
    test_card_type()
    test_set_years()
    test_not_english()
    test_english_tcg()
