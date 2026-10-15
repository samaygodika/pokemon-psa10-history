#!/usr/bin/env python3
"""Checks for the direct Fanatics Collect Buy Now pull (2026-10-15): the matcher's rules
(fanatics_match.py), the index paging and request budget (alt_scraper.fetch_fanatics_listings,
against a fake HTTP layer and the recorded fixtures in history/fixtures/), ingest_fanatics
(replace every Fanatics Buy Now row, drop Sold/gone ones, record verdicts), ingest_live's skips,
and metrics' check-time fallback.

    python3 history/test_fanatics.py

Plain asserts, no pytest (it isn't installed here). No request leaves this machine."""
import csv
import io
import json
import sys
import tempfile
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "history"))
import alt_scraper                                # noqa: E402
import fanatics_match as fm                       # noqa: E402
import ingest                                     # noqa: E402
import metrics                                    # noqa: E402

FIX = ROOT / "history" / "fixtures"


def _write(path, cols, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in cols} for r in rows)


def _asset(aid, name, year, sset, num, subject, variety=""):
    return {"asset_id": aid, "card_name": name, "year": year, "set": sset, "card_number": num, "subject": subject,
            "variety": variety}


ASSETS = [
    _asset("zard", "1999 Pokemon Base Set Holo Charizard #4", "1999", "Pokemon Base Set", "4", "Charizard", "Holo"),
    _asset("zard1", "1999 Pokemon Base Set 1st Edition Holo Charizard #4", "1999", "Pokemon Base Set", "4", "Charizard", "1st Edition Holo"),
    _asset("zardsh", "1999 Pokemon Game Shadowless Holo Charizard #4", "1999", "Pokemon Game Shadowless", "4", "Charizard", "Holo"),
    _asset("zardit", "1999 Pokemon Base Set Italian Holo Charizard #4", "1999", "Pokemon Base Set Italian", "4", "Charizard", "Holo"),
    _asset("jynx", "2005 Pokemon Ex Unseen Forces Jynx #28", "2005", "Pokemon Ex Unseen Forces", "28", "Jynx"),
    _asset("jynxrh", "2005 Pokemon Ex Unseen Forces Reverse Holo Jynx #28", "2005", "Pokemon Ex Unseen Forces", "28", "Jynx", "Reverse Holo"),
    _asset("fc925763", "2009 Pokemon Diamond and Pearl Black Star Promo Arceus Collector's Tin Holo Arceus Lv X #DP53", "2009",
           "Pokemon Diamond and Pearl Black Star Promo Arceus Collector's Tin", "DP53", "Arceus Lv X", "Holo"),
    _asset("arcit", "2007 Pokemon Diamond and Pearl Black Star Promos Italian Arceus Lv.X/(Arceus Collector's Tins) P #DP53", "2007",
           "Pokemon Diamond and Pearl Black Star Promos Italian", "DP53", "Arceus Lv.X/(Arceus Collector's Tins) P"),
    _asset("gengar", "2019 Pokemon Sun and Moon Team Up Gengar/Mimikyu Gx #165", "2019", "Pokemon Sun and Moon Team Up", "165", "Gengar/Mimikyu Gx"),
    _asset("perrin", "2024 Pokemon Twilight Masquerade Special Illustration Rare Perrin #220", "2024", "Pokemon Twilight Masquerade", "220",
           "Perrin", "Special Illustration Rare"),
    _asset("dup1", "2000 Pokemon Neo Genesis Holo Lugia #9", "2000", "Pokemon Neo Genesis", "9", "Lugia", "Holo"),
    _asset("dup2", "2000 Pokemon Neo Genesis 2000-2001 Holo Lugia #9", "2000", "Pokemon Neo Genesis 2000-2001", "9", "Lugia", "Holo"),
    _asset("jpmew", "2023 Pokemon Scarlet and Violet 151 Japanese Mew Ex #151", "2023", "Pokemon Scarlet and Violet 151 Japanese", "151", "Mew Ex"),
    _asset("enmew", "2023 Pokemon Scarlet and Violet 151 Mew Ex #151", "2023", "Pokemon Scarlet and Violet 151", "151", "Mew Ex"),
    _asset("nonum", "1999 Pokemon Base Set Charizard", "1999", "Pokemon Base Set", "", "Charizard"),
]
EN, JP = "Trading Card Games > Pokémon (English)", "Trading Card Games > Pokémon (Japanese)"


def hit(title, year="", num="", cats=(EN,), uuid="u", price=100, grade=9):
    return {"listingUuid": uuid, "title": title, "year": year, "cardNumber": num, "subCategory1": list(cats),
            "currentPrice": price, "grade": grade, "gradingService": "PSA"}


def test_words():
    w = fm.words
    assert w("Arceus Lv.X") == w("Arceus LV. X") == w("Arceus Level X") == {"arceus", "lvx"}, w("Arceus Level X")
    assert w("Gengar & Mimikyu GX") == {"gengar", "mimikyu", "gx"}
    assert w("Collector's Tin") == {"collector", "tin"} and w("Team Rocket’s Moltres ex") == {"team", "rocket", "moltres", "ex"}
    assert w("Reverse Foil Jynx") == {"reverse", "holo", "jynx"} and w("Black Star Promos") == {"black", "star", "promo"}
    assert w("Prismatic Evolutions ETB Eevee") == {"prismatic", "evolutions", "elite", "trainer", "box", "eevee"}
    assert w("1st Ed. Pokémon Pokemon") == {"1st", "edition"} and w("1999 PSA 9 MINT Gem Mt") == {"1999", "9"}
    assert fm.hit_number({"cardNumber": "037"}) == "37" and fm.hit_number({"cardNumber": "", "title": "Cynthia #SV82/SV94 PSA 10"}) == "SV82"
    assert fm.hit_number({"title": "no number here"}) == ""
    print("words ok")


def test_match_rules():
    idx = fm.load_assets(rows=ASSETS)
    m = lambda *a, **k: fm.match(hit(*a, **k), idx)   # noqa: E731
    assert m("1999 Pokemon Base Set Holo Charizard #4 PSA 9 MINT", "1999", "4")[:2] == ("zard", "matched")
    assert m("1999 Pokemon Base Set 1st Edition Holo Charizard #4 PSA 9", "1999", "4")[:2] == ("zard1", "matched")
    assert m("1999 Pokemon Base Set Shadowless Holo Charizard #4 PSA 9", "1999", "4")[:2] == ("zardsh", "matched")
    assert m("1999 Pokemon Base Set Italian Holo Charizard #4 PSA 9", "1999", "4")[:2] == ("zardit", "matched")
    assert m("1999 Pokemon Base Set Charizard #4 PSA 9", "1999", "4")[:2] == ("zard", "matched"), "Holo is optional in the title"
    assert m("1999 Pokemon Base Set Holo Charizard #4 PSA 9", "", "")[:2] == ("zard", "matched"), "year and number read off the title"
    # prints: the title's distinguishing word picks the print; a print word in the card's name must be in the title
    assert m("2005 Pokemon EX Unseen Forces Reverse Holo Jynx #28 PSA 9 MINT", "2005", "28")[:2] == ("jynxrh", "matched")
    assert m("2005 Pokemon EX Unseen Forces Holo Jynx #28 PSA 9 MINT", "2005", "28")[:2] == ("jynx", "matched")
    # the lone survivor wins although alt.xyz's set name says more than the title (Sid's Arceus)
    r = m("2009 Pokemon Diamond & Pearl Black Star Promo Holo Arceus Lv.X #DP53 PSA 9 MINT", "2009", "DP53")
    assert r[:2] == ("fc925763", "matched"), r
    assert m("2007 Pokemon Diamond & Pearl Black Star Promo Holo Arceus Lv.X #DP53 PSA 9", "2007", "DP53")[1] == "no_survivor", "Italian card, English title"
    assert m("2007 Pokemon Italian Diamond & Pearl Promo Arceus Lv.X #DP53 PSA 9", "2007", "DP53")[:2] == ("arcit", "matched")
    # both names of a tag team; a rarity label the title abbreviates
    assert m("2019 Pokemon Sun & Moon Team Up Full Art Gengar & Mimikyu GX #165 PSA 9", "2019", "165")[:2] == ("gengar", "matched")
    assert m("2019 Pokemon Team Up Mimikyu GX #165 PSA 9", "2019", "165")[1] == "no_survivor"
    assert m("2024 Pokemon Scarlet & Violet Twilight Masquerade SIR Perrin #220 PSA 10 GEM MINT", "2024", "220")[:2] == ("perrin", "matched")
    # near-duplicate cards the title cannot tell apart: refused, both named
    r = m("2000 Pokemon Neo Genesis Holo Lugia #9 PSA 9", "2000", "9")
    assert r[0] == "" and r[1] == "ambiguous" and len(r[2]) == 2, r
    assert m("2000 Pokemon Neo Genesis 2000-2001 Holo Lugia #9 PSA 9", "2000", "9")[:2] == ("dup2", "matched"), "the title explains strictly more"
    # language: the title's word, else Fanatics' category
    assert m("2023 Pokemon Japanese Scarlet & Violet 151 Mew ex #151 PSA 10", "2023", "151", cats=(JP,))[:2] == ("jpmew", "matched")
    assert m("2023 Pokemon Scarlet & Violet 151 Mew ex #151 PSA 10", "2023", "151", cats=(EN,))[:2] == ("enmew", "matched")
    assert m("2023 Pokemon Scarlet & Violet 151 Mew ex #151 PSA 10", "2023", "151", cats=(JP,))[:2] == ("jpmew", "matched"), "category says Japanese"
    assert m("2023 Pokemon Scarlet & Violet 151 Mew ex #151 PSA 10", "2023", "151", cats=(EN, JP))[1] == "ambiguous", "two categories, no word: unknown"
    assert m("2023 Pokemon Korean Scarlet & Violet 151 Mew ex #151 PSA 10", "2023", "151")[1] == "no_survivor"
    # nothing to go on
    assert m("1999 Pokemon Base Set Charizard PSA 9", "1999", "")[1] == "no_number"
    assert m("1998 Pokemon Base Set Holo Charizard #4 PSA 9", "1998", "4")[1] == "no_candidate", "exact year only, never a guess"
    print("match rules ok")


def test_fixture_page():
    """The recorded 2009 PSA 9 page (40 hits) against the real card list: Sid's Arceus lands on fc925763."""
    page = json.load(open(FIX / "fanatics_page_2009_psa9.json"))["results"][0]
    idx = fm.load_assets()
    out = fm.match_all(page["hits"], idx)
    arceus = out["fdffd8aa-4411-4309-94fe-c81ba4add3d4"]
    assert arceus[:2] == ("fc925763-f4d6-4455-8312-da0b7bcba966", "matched"), arceus
    matched = sum(1 for v in out.values() if v[1] == "matched")
    assert matched >= 25, f"{matched} of {len(out)} matched"
    assert all(v[1] in ("matched", "no_number", "no_candidate", "no_survivor", "ambiguous") for v in out.values())
    print(f"fixture page ok ({matched}/{len(out)} matched)")


class FakeIndex:
    """Stands in for _fanatics_post: the recorded mint and facet replies, and pages of a synthetic
    pool served the way the price-ascending replica does (price >= floor, 1,000 at a time)."""

    def __init__(self, pool, fail_first_search=False, estimate=None, cut_short=()):
        self.pool, self.calls, self.fail, self.estimate = pool, [], fail_first_search, estimate
        self.cut_short = list(cut_short)   # per matching page request: drop this many hits and say the scan was partial
        self.mint = json.load(open(FIX / "fanatics_search_key.json"))
        # the recorded key ran out 10 minutes after it was minted; a live one lasts the whole pull
        from datetime import datetime, timedelta, timezone
        self.mint["data"]["collectSearchKeyV2"]["validUntil"] = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.facets = json.load(open(FIX / "fanatics_facets.json"))

    def __call__(self, url, body, budget, what):
        budget.spend(what)
        self.calls.append((url.split("?")[0], body))
        if "graphql" in url:
            return self.mint
        req = body["requests"][0]
        if self.fail:
            self.fail = False
            raise urllib.error.HTTPError(url, 403, "key refused", {}, io.BytesIO(b"{}"))
        grade = int(req["facetFilters"][0][0].split(":")[1])
        if req.get("hitsPerPage") == 0:        # the recorded year counts, nbHits sized to the pool (or as told)
            fac = json.loads(json.dumps(self.facets))
            fac["results"][0]["nbHits"] = self.estimate if self.estimate is not None else sum(1 for h in self.pool if h["grade"] == grade)
            return fac
        years = {f.split(":")[1] for f in req["facetFilters"][1]} if len(req["facetFilters"]) > 2 and req["facetFilters"][1][0].startswith("year:") else None
        lo, hi = -1.0, float("inf")
        for f in req.get("numericFilters", []):
            if ">=" in f:
                lo = float(f.split(">=")[1])
            elif "<" in f:
                hi = float(f.split("<")[1])
        assert req["indexName"] == "prod_item_state_v1" and "relevancyStrictness" not in req, req   # the primary index: its sort replicas drop records
        hits = sorted((h for h in self.pool if h["grade"] == grade and (years is None or str(h["year"]) in years)
                       and lo <= h["currentPrice"] < hi), key=lambda h: (h["currentPrice"], h["listingUuid"]))
        assert req["page"] == 0, "one page per slice: later pages of a query are not a stable partition"
        cats = {f.split(":", 1)[1] for f in req["facetFilters"][2 if years is not None else 1]}
        hits = [h for h in hits if set(h["subCategory1"]) & cats]
        page, ex = hits[:req["hitsPerPage"]], True
        if self.cut_short and 0 < len(page) < req["hitsPerPage"]:   # the engine cutting a scan short
            drop = self.cut_short.pop(0)
            if drop:
                page, ex = page[:-drop], False
        # above 1,000 hits nbHits is the index's (inflated) estimate, as seen on 2026-10-15; a cut scan counts what it saw
        return {"results": [{"hits": page, "nbHits": (2 * len(hits) + 7) if len(hits) > 1000 else len(page),
                             "exhaustiveNbHits": ex, "exhaustive": {"nbHits": ex, "typo": True}, "page": 0}]}


def _pool(n10, n9, tie_at=None):
    """Synthetic listings: PSA 10 prices climb one dollar every three listings (ties), PSA 9 one a dollar."""
    pool = []
    for i in range(n10):
        pool.append({"listingUuid": f"g10-{i:05}", "grade": 10, "year": 2025 if i % 2 else 1999, "currentPrice": tie_at if tie_at else 10 + i // 3,
                     "title": f"card {i}", "gradingService": "PSA", "subCategory1": [EN]})
    for i in range(n9):
        pool.append({"listingUuid": f"g9-{i:05}", "grade": 9, "year": 2009, "currentPrice": 5 + i, "title": f"card9 {i}",
                     "gradingService": "PSA", "subCategory1": [EN]})
    return pool


def test_pull_paging_and_budget():
    saved = (alt_scraper._fanatics_post, alt_scraper.FANATICS_SLICE_MAX, alt_scraper._fanatics_key, alt_scraper.FANATICS_DELAY)
    alt_scraper.FANATICS_DELAY = 0
    try:
        alt_scraper.FANATICS_SLICE_MAX = 10 ** 9          # one slice per grade
        alt_scraper._fanatics_key = None
        fake = FakeIndex(_pool(2350, 500), fail_first_search=True)
        alt_scraper._fanatics_post = fake
        hits, stats = alt_scraper.fanatics_pull(80)
        assert len(hits) == 2850 and set(hits) == {h["listingUuid"] for h in fake.pool}, len(hits)
        # 1 mint, 1 refused facet request + 1 re-mint, 6 facet requests (grade x category), then one page per slice:
        # PSA 10 English's 2,350 fill a page -> split at the median -> each upper half fills a page again -> 7 pages;
        # PSA 10 Japanese / Other and PSA 9 Japanese / Other are empty pages; PSA 9 English's 500 come in one
        assert stats["requests"] == 21 and stats["slices"] == 6, stats
        kinds = [("mint" if "graphql" in u else "facet" if b["requests"][0].get("hitsPerPage") == 0 else "page") for u, b in fake.calls]
        assert kinds == ["mint", "facet", "mint"] + ["facet"] * 6 + ["page"] * 12, kinds
        pages = [b["requests"][0] for u, b in fake.calls if b.get("requests") and b["requests"][0].get("hitsPerPage") == 1000]
        assert pages[0]["indexName"] == "prod_item_state_v1" and "numericFilters" not in pages[0] and pages[0]["page"] == 0
        assert pages[0]["facetFilters"][2] == [f"subCategory1:{EN}"], pages[0]["facetFilters"]
        bands = [tuple(p.get("numericFilters", [])) for p in pages[:7]]
        # the median of the 1,000 cheapest splits off a short lower half each time; the upper half fills a page again
        assert bands == [(), ("currentPrice < 176.0",), ("currentPrice >= 176.0",), ("currentPrice >= 176.0", "currentPrice < 342.0"),
                         ("currentPrice >= 342.0",), ("currentPrice >= 342.0", "currentPrice < 508.0"), ("currentPrice >= 508.0",)], bands
        assert set(pages[0]["attributesToRetrieve"]) == set(alt_scraper.FANATICS_FIELDS) and "serial" not in pages[0]["attributesToRetrieve"]
        # a page the engine cut short (exhaustive false) is asked again; cut again -> split at the median price
        alt_scraper._fanatics_key = None
        pool = _pool(0, 500)
        for i, h in enumerate(pool):
            h["subCategory1"] = [JP] if i % 3 else [EN]
        fake = FakeIndex(pool, cut_short=[20])            # PSA 9 English: cut once, whole on the retry
        alt_scraper._fanatics_post = fake
        hits, stats = alt_scraper.fanatics_pull(80)
        assert len(hits) == 500 and set(hits) == {h["listingUuid"] for h in pool} and stats["requests"] == 1 + 6 + 3 + 2 + 1 + 1, stats
        fake = FakeIndex(pool, cut_short=[20, 20])        # cut twice -> two price halves
        alt_scraper._fanatics_post = fake
        alt_scraper._fanatics_key = None
        hits, stats = alt_scraper.fanatics_pull(80)
        assert len(hits) == 500 and set(hits) == {h["listingUuid"] for h in pool} and stats["requests"] == 1 + 6 + 3 + 4 + 1 + 1, stats
        en = [b["requests"][0] for u, b in fake.calls if b.get("requests") and b["requests"][0].get("hitsPerPage") == 1000
              and b["requests"][0]["facetFilters"][0] == ["grade:9"] and b["requests"][0]["facetFilters"][2] == [f"subCategory1:{EN}"]]
        assert [tuple(p.get("numericFilters", [])) for p in en] == [(), (), ("currentPrice < 224.0",), ("currentPrice >= 224.0",)], [p.get("numericFilters") for p in en]
        # slicing: the recorded year counts per grade and category, groups under the cap, biggest years first
        alt_scraper.FANATICS_SLICE_MAX = 1500
        alt_scraper._fanatics_post = FakeIndex([], estimate=47397)
        slices, est = alt_scraper.fanatics_slices(alt_scraper.RequestBudget(10))
        assert est == 6 * 47397 and slices[0][:2] == (10, EN) and all(len(ys) for _, _, ys in slices)
        assert slices[0][2] == ["2025"] and sum(len(ys) for _, _, ys in slices) == 6 * 34, slices
        alt_scraper.FANATICS_SLICE_MAX = 10 ** 9
        # all or nothing: the budget runs out -> FanaticsBudget, nothing returned
        alt_scraper.FANATICS_SLICE_MAX = 10 ** 9
        alt_scraper._fanatics_key = None
        alt_scraper._fanatics_post = FakeIndex(_pool(2350, 500))
        try:
            alt_scraper.fanatics_pull(5)
            raise AssertionError("budget not enforced")
        except alt_scraper.FanaticsBudget:
            pass
        # 1,000+ listings at one price: the split cannot advance -> refused, not silently partial
        alt_scraper._fanatics_post = FakeIndex(_pool(1200, 0, tie_at=49.99))
        try:
            alt_scraper.fanatics_pull(80)
            raise AssertionError("stall not detected")
        except RuntimeError as e:
            assert "cannot get past" in str(e), e
        # rows: fetch_fanatics_listings matches the recorded page against a small card list
        page = json.load(open(FIX / "fanatics_page_2009_psa9.json"))["results"][0]["hits"]
        alt_scraper._fanatics_key = None
        alt_scraper._fanatics_post = FakeIndex(page)
        with tempfile.TemporaryDirectory() as d:
            assets = Path(d) / "assets.csv"
            _write(assets, ingest.ASSET_COLS, ASSETS)
            rows, stats = alt_scraper.fetch_fanatics_listings(80, assets)
        assert len(rows) == 40 and list(rows[0]) == alt_scraper.FANATICS_COLS, rows[0]
        arc = [r for r in rows if r["url"].endswith("fdffd8aa-4411-4309-94fe-c81ba4add3d4")][0]
        assert arc["asset_id"] == "fc925763" and arc["match"] == "matched" and arc["grade"] == "9.0" and arc["buy_it_now_price"] == 100000
        assert arc["source"] == "Fanatics Collect" and arc["listing_type"] == "BUY_IT_NOW" and arc["category"] == "English", arc
        assert rows[0]["match"] == "matched" and rows[-1]["match"] != "matched", "matched rows first"
        assert stats["by_reason"]["matched"] == 1 and stats["listings"] == 40, stats
    finally:
        alt_scraper._fanatics_post, alt_scraper.FANATICS_SLICE_MAX, alt_scraper._fanatics_key, alt_scraper.FANATICS_DELAY = saved
    print("pull paging + budget ok")


def test_ingest_fanatics():
    U = "https://www.fanaticscollect.com/buy-now/"
    ids = {k: f"{d * 8}-0000-4000-8000-000000000000" for k, d in zip("abcdefghx", "123456789")}   # hex only
    with tempfile.TemporaryDirectory() as d:
        store, run = Path(d) / "store", Path(d) / "run"
        store.mkdir(); run.mkdir()
        t0, t1 = "2026-10-14T04:00:00+00:00", "2026-10-15T04:00:00+00:00"
        base = {"grading_company": "PSA", "grade": "10.0", "listing_type": "BUY_IT_NOW", "source": "Fanatics Collect",
                "current_bid": "", "bid_count": "", "end_date": "", "checked_at": t0, "first_seen": "2026-10-01", "via": ""}
        _write(store / "live_listings.csv", ingest.LIVE_COLS, [
            dict(base, asset_id="A", buy_it_now_price="120", url=U + ids["a"]),           # live, matched to A -> direct row
            dict(base, asset_id="B", buy_it_now_price="50", url=U + ids["b"]),            # live, unmatched -> alt's card, new price
            dict(base, asset_id="C", buy_it_now_price="80", url=U + ids["c"]),            # not in the pull -> gone
            dict(base, asset_id="D", buy_it_now_price="60", url=U + ids["d"]),            # live, matched to E -> re-attributed
            dict(base, asset_id="F", buy_it_now_price="70", url=U + ids["f"], via="fanatics", first_seen="2026-10-12"),  # old direct, gone
            dict(base, asset_id="A", source="eBay", buy_it_now_price="500", url="https://www.ebay.com/itm/1"),
            dict(base, asset_id="A", source="Alt", buy_it_now_price="450", url="https://alt.xyz/itm/x"),
            dict(base, asset_id="A", source="Fanatics Collect", listing_type="AUCTION", current_bid="5", bid_count="1",
                 end_date="2026-10-20T00:00:00+00:00", buy_it_now_price="", url="https://www.fanaticscollect.com/weekly/w1"),
        ])
        _write(store / "fanatics_checks.csv", ingest.CHECK_COLS, [
            {"listing_id": ids["x"], "status": "crossed", "fanatics_title": "NBA auto", "fanatics_price": "49.99", "checked_at": "2026-10-10"},
            {"listing_id": ids["b"], "status": "gone", "asset_id": "B", "fanatics_price": "50", "checked_at": "2026-10-13"}])
        pull = run / "fanatics_listings.csv"
        prow = {"grading_company": "PSA", "grade": "10", "listing_type": "BUY_IT_NOW", "source": "Fanatics Collect", "checked_at": t1}
        _write(pull, alt_scraper.FANATICS_COLS, [
            dict(prow, asset_id="A", buy_it_now_price="110", url=U + ids["a"], match="matched", title="A card"),
            dict(prow, asset_id="A", buy_it_now_price="130", url=U + ids["e"], match="matched", title="A card dearer"),
            dict(prow, asset_id="", buy_it_now_price="55", url=U + ids["b"], match="no_survivor", title="B?"),
            dict(prow, asset_id="E", buy_it_now_price="65", url=U + ids["d"], match="matched", title="E card"),
            dict(prow, asset_id="", buy_it_now_price="20", url=U + ids["g"], match="no_candidate", title="unknown"),
            dict(prow, asset_id="A", grade="9", buy_it_now_price="40", url=U + ids["h"], match="matched", title="A at 9"),
        ])
        out = ingest.ingest_fanatics(pull, store, "2026-10-15")
        rows = ingest.read_csv(store / "live_listings.csv")
        got = {(r["asset_id"], r["url"]): r for r in rows}
        assert set(got) == {("A", U + ids["a"]), ("B", U + ids["b"]), ("E", U + ids["d"]), ("A", "https://www.ebay.com/itm/1"),
                            ("A", "https://alt.xyz/itm/x"), ("A", "https://www.fanaticscollect.com/weekly/w1"),
                            ("A", U + ids["h"])}, sorted(got)
        a = got[("A", U + ids["a"])]
        assert a["buy_it_now_price"] == "110" and a["via"] == "fanatics" and a["first_seen"] == "2026-10-01" and a["checked_at"] == t1, a
        b = got[("B", U + ids["b"])]
        assert b["buy_it_now_price"] == "55" and b["via"] == "" and b["checked_at"] == t1 and b["first_seen"] == "2026-10-01", b
        e = got[("E", U + ids["d"])]
        assert e["via"] == "fanatics" and e["first_seen"] == "2026-10-15" and e["grade"] == "10.0", e
        assert got[("A", U + ids["h"])]["grade"] == "9.0"
        assert got[("A", "https://www.fanaticscollect.com/weekly/w1")]["via"] == "", "Fanatics auctions (alt's mirror) untouched"
        assert out["fanatics_gone"] == 2 and out["fanatics_reattributed"] == 1 and out["fanatics_mirror_kept"] == 1 and out["fanatics_relisted"] == 1, out
        assert out["fanatics_rows"] == 3 and out["fanatics_matched"] == 4
        checks = {r["listing_id"]: r for r in ingest.read_csv(store / "fanatics_checks.csv")}
        assert checks[ids["c"]]["status"] == "gone" and checks[ids["c"]]["asset_id"] == "C" and checks[ids["c"]]["checked_at"] == t1
        assert checks[ids["f"]]["status"] == "gone" and checks[ids["b"]]["status"] == "live" and checks[ids["b"]]["fanatics_price"] == "55"
        assert checks[ids["x"]]["status"] == "crossed" and len(checks) == 4, sorted(checks)
        assert ingest.dead_fanatics_ids(store) == {ids["x"], ids["c"], ids["f"]}

        # a per-card check of A: alt.xyz re-serves a (held direct) and c (gone) and a cheaper eBay BIN;
        # the direct row stays, a and c are skipped, eBay's is the per-card cheapest
        now = "2026-10-15T06:00:00+00:00"
        _write(run / "listings.csv", alt_scraper.LIVE_COLS, [
            dict(base, asset_id="A", buy_it_now_price="105", url=U + ids["a"], checked_at=now),
            dict(base, asset_id="A", buy_it_now_price="30", url=U + ids["c"], checked_at=now),
            dict(base, asset_id="A", source="eBay", buy_it_now_price="400", url="https://www.ebay.com/itm/2", checked_at=now),
        ])
        out = ingest.ingest_live(run, [{"asset_id": "A", "listings_checked_at": now}], store, "2026-10-15")
        rows = ingest.read_csv(store / "live_listings.csv")
        a10 = {r["url"]: r for r in rows if r["asset_id"] == "A" and r["grade"] == "10.0"}
        assert set(a10) == {U + ids["a"], "https://www.ebay.com/itm/2", "https://alt.xyz/itm/x"}, sorted(a10)
        assert a10[U + ids["a"]]["buy_it_now_price"] == "110" and a10[U + ids["a"]]["via"] == "fanatics"
        assert out["bins_fanatics_dead"] == 1 and out["bins_fanatics_direct_dup"] == 1, out
        assert all("via" in r for r in rows)

        # metrics: the direct row counts like any source, and gives a never-checked card its check time
        live = metrics.load_live(store)
        cols = metrics.live_cols(now, live[("A", "10.0")])
        assert cols["lowest_bin_price"] == "110" and cols["lowest_bin_source"] == "Fanatics Collect" and cols["lowest_bin_url"] == U + ids["a"], cols
        assert metrics.live_check_time("", live[("E", "10.0")]) == t1
        assert metrics.live_check_time("", live[("B", "10.0")]) == "", "alt's mirrored row alone is no check"
        c9 = metrics.live_cols(metrics.live_check_time("", live[("A", "9.0")]), live[("A", "9.0")], prefix="psa9_")
        assert c9["psa9_lowest_bin_price"] == "40" and c9["psa9_listings_checked_at"] == t1, c9

        # stand-alone CLI form
        import subprocess
        r = subprocess.run([sys.executable, str(ROOT / "history" / "ingest.py"), "--fanatics-listings", str(pull), "--date", "2026-10-16",
                            "--store", str(store)], capture_output=True, text=True)
        assert r.returncode == 0 and "Fanatics Collect's Buy Nows replaced" in r.stdout, r.stdout + r.stderr
    print("ingest fanatics ok")


if __name__ == "__main__":
    test_words()
    test_match_rules()
    test_fixture_page()
    test_pull_paging_and_budget()
    test_ingest_fanatics()
    print("all Fanatics checks passed")
