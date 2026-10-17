#!/usr/bin/env python3
"""Checks for history/watchlist_listings.py (2026-10-15): the queries built for a card, which
eBay titles the matcher accepts and which it refuses (and why), how the eBay and alt.xyz
sources combine, and the file the sweep writes.

    python3 history/test_watchlist_listings.py

Plain asserts, no pytest. urlopen is faked: nothing here talks to eBay."""
import csv
import io
import json
import sys
import tempfile
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "history"))
import ingest                   # noqa: E402
import watchlist_listings as wl  # noqa: E402

NOW = datetime(2026, 10, 15, 20, 0, tzinfo=timezone.utc)
KEYS = {"EBAY_CLIENT_ID": "id", "EBAY_CLIENT_SECRET": "secret"}


def asset(aid, name, set_, subject, number, variety, year):
    return {"asset_id": aid, "card_name": name, "set": set_, "subject": subject, "card_number": number, "variety": variety,
            "year": year, "grading_company": "PSA", "grade": "10.0", "alt_url": "", "first_seen": "2026-09-11", "last_seen": "2026-10-15"}


PALKIA = asset("a1", "2009 Pokemon Platinum Holo Palkia G Lv X #125", "Pokemon Platinum", "Palkia G Lv X", "125", "Holo", "2009")
PALKIA_CAT = {"asset_id": "a1", "name": "Palkia G Lv.X", "set": "Platinum", "number": "125/127"}
UMBREON = asset("a2", "2007 Pokemon Organized Play Pop Series 5 Umbreon Gold Star #17", "Pokemon Organized Play Pop Series 5", "Umbreon Gold Star", "17", "", "2007")
UMBREON_CAT = {"asset_id": "a2", "name": "Umbreon Star", "set": "POP Series 5", "number": "017/017"}
RAICHU = asset("a3", "2000 Pokemon Gym Challenge 1st Edition Holo Lt. Surge's Raichu #11", "Pokemon Gym Challenge", "Lt. Surge's Raichu", "11", "1st Edition Holo", "2000")
RAICHU_CAT = {"asset_id": "a3", "name": "Lt. Surge's Raichu", "set": "Gym Challenge", "number": "011/132"}
CHAR_REV = asset("a4", "2013 Pokemon Black and White Legendary Treasures Reverse Holo Charizard #19", "Pokemon Black and White Legendary Treasures", "Charizard", "19", "Reverse Holo", "2013")
CHAR_HOLO = asset("a5", "2013 Pokemon Black and White Legendary Treasures Holo Charizard #19", "Pokemon Black and White Legendary Treasures", "Charizard", "19", "Holo", "2013")
CHAR_CAT = {"name": "Charizard", "set": "Legendary Treasures", "number": "19/113"}
MEWTWO = asset("a6", "2008 Pokemon Diamond and Pearl Black Star Promo Holo Mewtwo Lv X #DP28", "Pokemon Diamond and Pearl Black Star Promo", "Mewtwo Lv X", "DP28", "Holo", "2008")
MEWTWO_CAT = {"asset_id": "a6", "name": "Mewtwo LV.X - DP28", "set": "Diamond and Pearl Promos", "number": "DP28"}
DEOXYS = asset("a7", "2013 Pokemon Black and White Plasma Freeze Holo Deoxys Ex #53", "Pokemon Black and White Plasma Freeze", "Deoxys Ex", "53", "Holo", "2013")
DEOXYS_CAT = {"asset_id": "a7", "name": "Deoxys EX (Team Plasma)", "set": "Plasma Freeze", "number": "53/116"}


def test_card_and_queries():
    c = wl.Card(PALKIA, PALKIA_CAT)
    assert c.number == "125" and c.total == "127" and c.variants == ["lv x"] and not c.letter_number
    assert c.subject_words == ["palkia", "g"] and c.short_words == {"g"}
    assert c.queries("9") == ["Palkia G Lv X 125 PSA 9", "Palkia G Lv X Platinum PSA 9"]
    u = wl.Card(UMBREON, UMBREON_CAT)
    assert u.number == "17" and u.total == "17" and u.variants == ["gold star"] and u.subject_words == ["umbreon"]
    assert "pop" in u.set_words
    assert u.queries("10") == ["Umbreon Gold Star 17 PSA 10", "Umbreon Gold Star Pop Series 5 PSA 10"]
    r = wl.Card(RAICHU, RAICHU_CAT)
    assert r.first_edition and r.queries("10") == ["Lt. Surge's Raichu 11 1st Edition PSA 10", "Lt. Surge's Raichu Gym Challenge 1st Edition PSA 10"]
    m = wl.Card(MEWTWO, MEWTWO_CAT)
    assert m.number == "dp28" and m.letter_number and m.queries("10") == ["Mewtwo Lv X DP28 PSA 10", "Mewtwo Lv X Promo PSA 10"]
    cr = wl.Card(CHAR_REV, dict(CHAR_CAT, asset_id="a4"))
    assert cr.reverse and cr.queries("10")[0] == "Charizard 19 Reverse PSA 10"
    # no catalog row: the number comes from assets.csv, no total
    c2 = wl.Card(PALKIA)
    assert c2.number == "125" and c2.total is None


def test_title_matches_accepts():
    p = wl.Card(PALKIA, PALKIA_CAT)
    for t in ["PALKIA G LV. X 2009 POKEMON PLATINUM #125/127 HOLO RARE PSA 9 Q0895",
              "Pokemon Platinum Palkia G Lv X Holo Psa 9 Mint 125/127 2009",
              "2009 POKEMON PLATINUM #125 PALKIA G LV.X HOLO PSA 9 MINT",
              "2009 Pokemon Platinum Palkia LV.X 125/127 PSA 9, VERY CLEAN",     # no "G": the total pins the card
              "Palkia G Level X 125/127 PSA GEM MINT 9"]:
        assert wl.title_matches(p, "9", t) == (True, ""), t
    u = wl.Card(UMBREON, UMBREON_CAT)
    assert wl.title_matches(u, "10", "2007 Pokémon Gold Star Umbreon Pop Series 5 #17 - PSA 10 - GEM MINT")[0]
    assert wl.title_matches(u, "10", "Umbreon Star 017/017 POP 5 PSA 10")[0]
    r = wl.Card(RAICHU, RAICHU_CAT)
    assert wl.title_matches(r, "10", "2000 POKEMON GYM CHALLENGE HOLO-1ST EDITION #11 LT. SURGE'S RAICHU PSA 10")[0]
    assert wl.title_matches(r, "10", "PSA 10 1st Edition Lt Surges Raichu 11/132 Gym Challenge Pokemon 2000")[0]
    m = wl.Card(MEWTWO, MEWTWO_CAT)
    assert wl.title_matches(m, "9", "mewtwo lv x psa 9 Black Star Promo #DP28")[0]
    assert wl.title_matches(m, "9", "Pokemon Mewtwo LV.X Holo Black Star Promo DP 28 PSA 9 Mint")[0]
    ch = wl.Card(CHAR_HOLO, dict(CHAR_CAT, asset_id="a5"))
    assert wl.title_matches(ch, "10", "2013 Pokemon Legendary Treasures Charizard Holo Black And White #19 PSA 10")[0]
    cr = wl.Card(CHAR_REV, dict(CHAR_CAT, asset_id="a4"))
    assert wl.title_matches(cr, "10", "2013 Pokemon Legendary Treasures Charizard Reverse Holo #19 PSA 10")[0]


def test_title_matches_refuses():
    p = wl.Card(PALKIA, PALKIA_CAT)
    u = wl.Card(UMBREON, UMBREON_CAT)
    r = wl.Card(RAICHU, RAICHU_CAT)
    ch = wl.Card(CHAR_HOLO, dict(CHAR_CAT, asset_id="a5"))
    cr = wl.Card(CHAR_REV, dict(CHAR_CAT, asset_id="a4"))
    d = wl.Card(DEOXYS, DEOXYS_CAT)
    cases = [
        (p, "9", "2009 POKEMON PLATINUM #125 PALKIA G LV.X-HOLO PSA 8", "grade"),
        (p, "9", "Palkia G Lv.X 125/127 Platinum Holo", "grade none"),
        (p, "9", "Palkia G LV.X 125 PSA 9 Dialga G LV.X 122 PSA 9 Sequential", "more than one"),
        (p, "9", "2008 POKEMON GREAT ENCOUNTERS #106 PALKIA LV.X-HOLO PSA 9", "number 125"),
        (p, "9", "Palkia G LV.X 033/096 PSA 9 Japanese Pokemon", "japanese"),
        (p, "9", "Palkia LV.X 125 PSA 9 Platinum", "subject word 'g'"),      # no total, no G: could be another Palkia LV.X
        (p, "9", "Palkia G LV.X 125/150 PSA 9", "total 150"),
        (u, "10", "Pokémon TCG Umbreon Gold Star 17/17 Celebrations Classic Collection Holo PSA 10", "celebrations"),
        (u, "10", "PSA 10 Umbreon Gold Star From Celebration 17/17.  Free Shipping", "celebration"),
        (u, "10", "2021 Umbreon Gold Star 17/17 Holo PSA 10", "year 2021"),
        (u, "10", "POP 3 Umbreon GoldStar PSA 10 AUTO 10 Megan Hollingshead 17/17", "auto"),
        (u, "10", "Umbreon 17/17 PSA 10", "variant 'gold star'"),
        (r, "10", "2000 Pokemon Gym Challenge Unlimited Lt. Surge's Raichu #11 PSA 10", "1st edition"),
        (ch, "10", "2013 Pokemon Legendary Treasures Charizard Reverse Holo #19 PSA 10", "reverse"),
        (cr, "10", "2013 Pokemon Legendary Treasures Charizard Holo #19 PSA 10", "reverse"),
        (ch, "10", "Charizard 19 PSA 10", "no set evidence"),
        (ch, "10", "Charizard Legendary Treasures PSA 10", "number 19"),
        (ch, "10", "Charizard 19/113 PSA 10 & Blastoise 20/113 PSA 10 Lot", "more than one"),
        (d, "10", "2014 POKEMON PROMO DEOXYS EX #53 PSA 10 GEM MINT WORLD CHAMP PLASMA POWER", "champ"),
        (d, "10", "Deoxys EX 53/116 Plasma Freeze PSA 10 BGS 9.5 cross", "bgs"),
        (d, "10", "Incomplete Holo ERROR! Deoxys EX Plasma Freeze #53 PSA 10", "error"),
    ]
    for card, grade, title, why in cases:
        ok, reason = wl.title_matches(card, grade, title)
        assert not ok and why in reason, (title, reason)


class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


class FakeEbay:
    """(query, buying option, offset) -> list of item summaries, or int HTTP status. Unlisted = []."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, req, timeout=None):
        if req.full_url.startswith(wl.ebay_verify.TOKEN_URL):
            return Resp(json.dumps({"access_token": "t", "expires_in": 7200}).encode())
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
        kind = "auction" if "AUCTION" in qs["filter"][0] else "bin"
        key = (qs["q"][0], kind, int(qs["offset"][0]))
        self.calls.append(key)
        a = self.answers.get(key, [])
        if isinstance(a, int):
            raise urllib.error.HTTPError(req.full_url, a, "err", {}, io.BytesIO(b"{}"))
        items, total = a if isinstance(a, tuple) else (a, len(a))
        return Resp(json.dumps({"itemSummaries": items, "total": total}).encode())


def item(iid, title, price="100.00", auction=False, bids=None, end=None):
    d = {"legacyItemId": iid, "title": title, "price": {"value": price, "currency": "USD"},
         "buyingOptions": ["AUCTION"] if auction else ["FIXED_PRICE", "BEST_OFFER"]}
    if auction:
        d["currentBidPrice"] = {"value": price, "currency": "USD"}
        d["bidCount"] = bids or 0
        d["itemEndDate"] = end or "2026-10-20T03:59:01.000Z"
    return d


def test_ebay_rows_sources_and_pages():
    p = wl.Card(PALKIA, PALKIA_CAT)
    good = item("100000000001", "2009 POKEMON PLATINUM #125 PALKIA G LV.X HOLO PSA 9")
    bad = item("100000000002", "2009 POKEMON PLATINUM #125 PALKIA G LV.X HOLO PSA 8")
    auc = item("100000000003", "Palkia G Lv.X 125/127 Platinum PSA 9", "1025.00", auction=True, bids=10)
    fake = FakeEbay({
        ("Palkia G Lv X 125 PSA 9", "bin", 0): ([good, bad], 201),      # 201 hits: a second page
        ("Palkia G Lv X 125 PSA 9", "bin", 200): [item("100000000004", "Palkia G LV.X 125/127 PSA 9 Mint")],
        ("Palkia G Lv X 125 PSA 9", "auction", 0): [auc],
        ("Palkia G Lv X Platinum PSA 9", "bin", 0): [good],             # same listing again: once in the output
        ("Palkia G Lv X Platinum PSA 9", "auction", 0): [auc],
    })
    ebay = wl.ebay_verify.Ebay(KEYS, fake)
    bud = wl.Budget(10)
    rows, complete = wl.ebay_rows(p, "9", ebay, bud, "2026-10-15T20:00:00+00:00", fake)
    assert complete and bud.used == 5 and len(fake.calls) == 5
    assert [(r["url"][-12:], r["kind"], r["price"]) for r in rows] == [("100000000001", "bin", "100.00"), ("100000000004", "bin", "100.00"), ("100000000003", "auction", "1025.00")]
    assert rows[2]["bid_count"] == "10" and rows[2]["ends"] == "2026-10-20T03:59:01.000Z" and rows[0]["ends"] == "" and rows[0]["bid_count"] == ""
    assert rows[0]["title"] == good["title"] and rows[0]["source"] == "eBay"
    # budget runs out after 2 calls: what was found is kept, the card is reported as not fully swept
    fake.calls.clear()
    rows, complete = wl.ebay_rows(p, "9", wl.ebay_verify.Ebay(KEYS, fake), wl.Budget(2), "s", fake)
    assert not complete and len(fake.calls) == 2 and [r["url"][-12:] for r in rows] == ["100000000001", "100000000004"]
    # a 429 stops everything; an HTTP 500 only marks the card incomplete
    fake429 = FakeEbay({("Palkia G Lv X 125 PSA 9", "bin", 0): 429})
    bud = wl.Budget(10)
    rows, complete = wl.ebay_rows(p, "9", wl.ebay_verify.Ebay(KEYS, fake429), bud, "s", fake429)
    assert not complete and bud.rate_limited and rows == []
    fake500 = FakeEbay({("Palkia G Lv X 125 PSA 9", "bin", 0): 500, ("Palkia G Lv X 125 PSA 9", "auction", 0): [auc]})
    rows, complete = wl.ebay_rows(p, "9", wl.ebay_verify.Ebay(KEYS, fake500), wl.Budget(10), "s", fake500)
    assert not complete and [r["url"][-12:] for r in rows] == ["100000000003"]


def live(aid, grade, kind, source, url, price="500", end="", bids=""):
    return {"asset_id": aid, "grading_company": "PSA", "grade": grade, "listing_type": kind, "source": source,
            "current_bid": price if kind == "AUCTION" else "", "bid_count": bids, "end_date": end,
            "buy_it_now_price": price if kind != "AUCTION" else "", "url": url, "checked_at": "2026-10-15T16:00:00+00:00", "first_seen": "2026-10-07"}


def test_mirror_rows():
    now = "2026-10-15T20:00:00+00:00"
    rows = [
        live("a1", "9.0", "AUCTION", "Alt", "https://alt.xyz/itm/x1", "1024", "2026-10-30T01:02:00+00:00", "0"),
        live("a1", "9.0", "BUY_IT_NOW", "Fanatics Collect", "https://www.fanaticscollect.com/buy-now/f1", "9999"),
        live("a1", "9.0", "AUCTION", "Goldin", "https://goldin.co/item/g1", "100", "2026-10-15T19:00:00+00:00", "3"),   # ended: dropped
        live("a1", "9.0", "BUY_IT_NOW", "eBay", "https://www.ebay.com/itm/100000000001", "100"),            # found by the search: not repeated
        live("a1", "9.0", "AUCTION", "eBay", "https://www.ebay.com/itm/200000000002", "1025", "2026-10-20T03:59:01+00:00", "10"),   # not found: mirror row
        live("a1", "10.0", "BUY_IT_NOW", "Alt", "https://alt.xyz/itm/x2", "5000"),                   # other grade
        live("a9", "9.0", "BUY_IT_NOW", "Alt", "https://alt.xyz/itm/x3", "5000"),                    # other card
    ]
    out = wl.mirror_rows("a1", "9", rows, {"100000000001"}, "stamp", now)
    assert [(r["source"], r["kind"], r["price"], r["bid_count"], r["ends"]) for r in out] == [
        ("Alt", "auction", "1024", "0", "2026-10-30T01:02:00+00:00"),
        ("Fanatics Collect", "bin", "9999", "", ""),
        (wl.MIRROR_EBAY, "auction", "1025", "10", "2026-10-20T03:59:01+00:00")]
    assert all(r["title"] == "" and r["checked_at"] == "stamp" for r in out)


def test_mirror_rows_offsets():
    """A CardHobby auction stored with its -04:00 end: still running at 20:00Z when it ends
    17:00 Eastern (21:00Z), gone when it ended 15:00 Eastern (19:00Z); `ends` is published as UTC."""
    now = "2026-10-15T20:00:00+00:00"
    rows = [
        live("a1", "9.0", "AUCTION", "CardHobby", "https://www.cardhobby.com/1", "40", "2026-10-15T17:00:00-04:00", "2"),
        live("a1", "9.0", "AUCTION", "CardHobby", "https://www.cardhobby.com/2", "30", "2026-10-15T15:00:00-04:00", "1"),
    ]
    out = wl.mirror_rows("a1", "9", rows, set(), "stamp", now)
    assert [(r["url"][-1], r["ends"]) for r in out] == [("1", "2026-10-15T21:00:00+00:00")], out


def _write(path, cols, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in cols} for r in rows)


def test_sweep_end_to_end():
    with tempfile.TemporaryDirectory() as td:
        store, latest = Path(td) / "history", Path(td) / "latest"
        store.mkdir(), latest.mkdir()
        _write(store / "assets.csv", ingest.ASSET_COLS, [PALKIA, UMBREON])
        _write(latest / "card_catalog.csv", ["asset_id", "name", "set", "number"], [PALKIA_CAT, UMBREON_CAT])
        _write(store / "live_listings.csv", ingest.LIVE_COLS, [
            live("a1", "9.0", "AUCTION", "Alt", "https://alt.xyz/itm/x1", "1024", "2026-10-30T01:02:00+00:00", "0"),
            live("a2", "10.0", "AUCTION", "Goldin", "https://goldin.co/item/g1", "128100", "2026-10-26T02:00:00+00:00", "23")])
        out = latest / "watchlist_listings.csv"
        _write(out, wl.OUT_COLS, [{"asset_id": "a1", "grade": "9", "source": "eBay", "kind": "bin", "price": "4300",
                                   "url": "https://www.ebay.com/itm/100000000001", "first_seen": "2026-10-01", "checked_at": "old"}])
        good = item("100000000001", "2009 Pokemon Platinum Palkia LV.X 125/127 PSA 9, VERY CLEAN", "4300.00")
        fake = FakeEbay({("Palkia G Lv X 125 PSA 9", "bin", 0): [good, item("100000000002", "Palkia G LV.X 125/127 PSA 9", "3999.99")],
                         ("Umbreon Gold Star 17 PSA 10", "bin", 0): [item("100000000003", "Umbreon Gold Star 17/17 Celebrations PSA 10", "400.00"),
                                                                      item("100000000004", "2007 Pokemon Umbreon Gold Star Pop Series 5 #17 PSA 10", "3000000.00")]})
        watch = [{"asset_id": "a1", "grade": "9", "kind": "holding", "profiles": "2"},
                 {"asset_id": "a2", "grade": "10", "kind": "favorite", "profiles": "1"},
                 {"asset_id": "a2", "grade": "10", "kind": "holding", "profiles": "1"},     # same card and grade: swept once
                 {"asset_id": "zz", "grade": "10", "kind": "favorite", "profiles": "1"},    # not in assets.csv
                 {"asset_id": "a1", "grade": "8", "kind": "holding", "profiles": "1"}]      # not a grade the feed tracks
        summary = wl.sweep(watch, out, store, latest, budget=100, urlopen=fake, now=NOW, keys=KEYS)
        rows = list(csv.DictReader(open(out)))
        assert [tuple(r[c] for c in ("asset_id", "grade", "source", "kind", "price", "url")) for r in rows] == [
            ("a1", "9", "Alt", "auction", "1024", "https://alt.xyz/itm/x1"),
            ("a1", "9", "eBay", "bin", "3999.99", "https://www.ebay.com/itm/100000000002"),
            ("a1", "9", "eBay", "bin", "4300.00", "https://www.ebay.com/itm/100000000001"),
            ("a2", "10", "Goldin", "auction", "128100", "https://goldin.co/item/g1"),
            ("a2", "10", "eBay", "bin", "3000000.00", "https://www.ebay.com/itm/100000000004")]
        assert list(rows[0].keys()) == wl.OUT_COLS
        by_url = {r["url"]: r for r in rows}
        assert by_url["https://www.ebay.com/itm/100000000001"]["first_seen"] == "2026-10-01"      # carried over
        assert by_url["https://www.ebay.com/itm/100000000002"]["first_seen"] == "2026-10-15"      # new today
        assert all(r["checked_at"] == "2026-10-15T20:00:00+00:00" for r in rows)
        assert summary["rows"] == 5 and summary["ebay_calls"] == 8 and len(summary["swept"]) == 2 and summary["not_swept"] == []
        assert [s["why"] for s in summary["skipped"]] == ["unknown asset", "grade not 9 or 10"]
        assert json.loads(wl.summary_path(out).read_text())["ebay_calls"] == 8
        assert wl.summary_path(out).name == "watchlist_listings.summary.json"
        # no eBay keys: alt.xyz rows only, every card reported as not fully swept
        summary = wl.sweep(watch, out, store, latest, budget=100, urlopen=fake, now=NOW, keys={})
        assert summary["ebay_calls"] == 0 and len(summary["not_swept"]) == 2 and summary["rows"] == 2 and not summary["ebay_keys"]


def test_read_watchlist_file():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "watchlist.csv"
        p.write_text("asset_id,grade,kind,profiles\na1,9,holding,2\n")
        assert wl.read_watchlist(str(p)) == [{"asset_id": "a1", "grade": "9", "kind": "holding", "profiles": "2"}]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} test(s) passed")
