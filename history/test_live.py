#!/usr/bin/env python3
"""Checks for live-listings snapshot keeping and Buy It Now aging in ingest/metrics
(2026-09-25), and for the PSA 9 listings behind --also-listings: replacement per
(asset, grade), the psa9_* live columns.

    python3 history/test_live.py

Plain asserts, no pytest (it isn't installed here)."""
import csv
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "history"))
import ingest                                     # noqa: E402
import metrics                                    # noqa: E402


def _write(path, cols, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in cols} for r in rows)


def test_ingest_live_keeps_snapshots_and_ages_bins():
    with tempfile.TemporaryDirectory() as d:
        store, run = Path(d) / "store", Path(d) / "run"
        store.mkdir(); run.mkdir()
        now = "2026-09-25T14:00:00+00:00"
        old = "2026-09-15T10:00:00+00:00"    # 10 days before: BIN aged out
        recent = "2026-09-21T10:00:00+00:00" # 4 days before: BIN kept
        base = {"grading_company": "PSA", "grade": "10.0", "source": "eBay", "current_bid": "", "bid_count": "",
                "end_date": "", "buy_it_now_price": "", "url": ""}
        _write(store / "live_listings.csv", ingest.LIVE_COLS, [
            # A: untrusted answer this run (listings_checked_at blank) -> rows kept, BIN recent
            dict(base, asset_id="A", listing_type="AUCTION", end_date="2026-09-30T00:00:00+00:00", url="a1", checked_at=recent),
            dict(base, asset_id="A", listing_type="BUY_IT_NOW", buy_it_now_price="100", url="a2", checked_at=recent),
            # B: not in this run at all; its BIN is 10 days old -> dropped; its auction ended -> pruned
            dict(base, asset_id="B", listing_type="BUY_IT_NOW", buy_it_now_price="50", url="b1", checked_at=old),
            dict(base, asset_id="B", listing_type="AUCTION", end_date="2026-09-20T00:00:00+00:00", url="b2", checked_at=old),
            # C: checked this run with a trusted empty answer -> rows replaced by nothing
            dict(base, asset_id="C", listing_type="BUY_IT_NOW", buy_it_now_price="70", url="c1", checked_at=recent),
        ])
        cards = [{"asset_id": "A", "listings_checked_at": ""},
                 {"asset_id": "C", "listings_checked_at": now},
                 {"asset_id": "D", "listings_checked_at": now}]
        _write(run / "listings.csv", ingest.LIVE_COLS, [
            dict(base, asset_id="D", listing_type="BUY_IT_NOW", buy_it_now_price="30", url="d1", checked_at=now),
            dict(base, asset_id="D", listing_type="BUY_IT_NOW", buy_it_now_price="20", url="d2", checked_at=now),
        ])
        out = ingest.ingest_live(run, cards, store)
        rows = ingest.read_csv(store / "live_listings.csv")
        by = {}
        for r in rows:
            by.setdefault(r["asset_id"], []).append(r)
        assert sorted(by) == ["A", "D"], sorted(by)
        assert {r["url"] for r in by["A"]} == {"a1", "a2"}, "A keeps its last believable snapshot"
        assert [r["url"] for r in by["D"]] == ["d2"], "only the cheapest BIN is stored"
        assert out["bins_aged_out"] == 1
        # metrics: A's columns come from the kept snapshot with the OLD checked_at
        cols = metrics.live_cols(recent, by["A"])
        assert cols["listings_checked_at"] == recent and cols["live_auction_count"] == 1
        assert cols["lowest_bin_price"] == "100"
        assert metrics.live_cols("", [])["listings_checked_at"] == ""
        print("ingest/metrics ok:", out)


def _urls_by_key(store):
    by = {}
    for r in ingest.read_csv(store / "live_listings.csv"):
        by.setdefault((r["asset_id"], r["grade"]), []).append(r["url"])
    return by


def test_ingest_live_replaces_per_grade():
    with tempfile.TemporaryDirectory() as d:
        store, run = Path(d) / "store", Path(d) / "run"
        store.mkdir(); run.mkdir()
        now = "2026-09-25T14:00:00+00:00"
        recent = "2026-09-24T10:00:00+00:00"
        base = {"grading_company": "PSA", "source": "eBay", "current_bid": "", "bid_count": "",
                "end_date": "", "buy_it_now_price": "", "url": "", "checked_at": recent}
        _write(store / "live_listings.csv", ingest.LIVE_COLS, [
            dict(base, asset_id="A", grade="10.0", listing_type="BUY_IT_NOW", buy_it_now_price="100", url="a10"),
            dict(base, asset_id="A", grade="9.0", listing_type="BUY_IT_NOW", buy_it_now_price="40", url="a9"),
            dict(base, asset_id="A", grade="9.0", listing_type="AUCTION", end_date="2026-09-30T00:00:00+00:00", url="a9auc"),
            dict(base, asset_id="B", grade="10.0", listing_type="BUY_IT_NOW", buy_it_now_price="200", url="b10"),
            dict(base, asset_id="B", grade="9.0", listing_type="BUY_IT_NOW", buy_it_now_price="80", url="b9"),
        ])
        # Run 1: A checked at PSA 10 only (a cheaper BIN now); B not in the run at all.
        cards = [{"asset_id": "A", "listings_checked_at": now, "psa9_listings_checked_at": ""}]
        _write(run / "listings.csv", ingest.LIVE_COLS, [
            dict(base, asset_id="A", grade="10.0", listing_type="BUY_IT_NOW", buy_it_now_price="90", url="a10new", checked_at=now),
        ])
        out = ingest.ingest_live(run, cards, store)
        by = _urls_by_key(store)
        assert by[("A", "10.0")] == ["a10new"], by
        assert sorted(by[("A", "9.0")]) == ["a9", "a9auc"], "a PSA 10-only check leaves the PSA 9 rows alone"
        assert by[("B", "10.0")] == ["b10"] and by[("B", "9.0")] == ["b9"]
        assert out["live_checks"] == {"10.0": 1, "9.0": 0} and out["live_assets_checked"] == 1, out
        # Run 2: A checked at PSA 9 with an empty answer (nothing listed); its PSA 10 request failed (blank).
        cards = [{"asset_id": "A", "listings_checked_at": "", "psa9_listings_checked_at": now}]
        _write(run / "listings.csv", ingest.LIVE_COLS, [])
        out = ingest.ingest_live(run, cards, store)
        by = _urls_by_key(store)
        assert ("A", "9.0") not in by, "an empty PSA 9 answer clears only the PSA 9 rows"
        assert by[("A", "10.0")] == ["a10new"], "the PSA 10 snapshot stands"
        assert by[("B", "9.0")] == ["b9"], "other assets' PSA 9 rows untouched"
        assert out["live_checks"] == {"10.0": 0, "9.0": 1}
        # Run 3: a listing row whose grade alt.xyz wrote as '9' still lands on the PSA 9 key.
        cards = [{"asset_id": "B", "listings_checked_at": "", "psa9_listings_checked_at": now}]
        _write(run / "listings.csv", ingest.LIVE_COLS, [
            dict(base, asset_id="B", grade="9", listing_type="BUY_IT_NOW", buy_it_now_price="70", url="b9new", checked_at=now),
        ])
        ingest.ingest_live(run, cards, store)
        by = _urls_by_key(store)
        assert [u for (aid, g), us in by.items() if aid == "B" and g != "10.0" for u in us] == ["b9new"], by
        assert by[("B", "10.0")] == ["b10"]
        print("per-grade ingest ok")


def test_metrics_psa9_live_cols():
    now = "2026-09-25T14:00:00+00:00"
    rows9 = [
        {"listing_type": "AUCTION", "end_date": "2026-09-27T00:00:00+00:00", "current_bid": "12.5", "bid_count": "3",
         "source": "eBay", "url": "u1", "buy_it_now_price": ""},
        {"listing_type": "AUCTION", "end_date": "2026-09-26T00:00:00+00:00", "current_bid": "5", "bid_count": "0",
         "source": "Fanatics Collect", "url": "u2", "buy_it_now_price": ""},
        {"listing_type": "BUY_IT_NOW", "end_date": "", "current_bid": "", "bid_count": "", "source": "eBay", "url": "u3",
         "buy_it_now_price": "45.0"},
    ]
    assert metrics.PSA9_LIVE_COLS == ["psa9_listings_checked_at", "psa9_live_auction_count", "psa9_next_auction_end",
                                      "psa9_next_auction_bid", "psa9_next_auction_bid_count", "psa9_next_auction_source",
                                      "psa9_next_auction_url", "psa9_last_auction_end", "psa9_lowest_bin_price",
                                      "psa9_lowest_bin_source", "psa9_lowest_bin_url"]
    cols = metrics.live_cols(now, rows9, prefix="psa9_")
    assert list(cols) == metrics.PSA9_LIVE_COLS, list(cols)
    assert cols["psa9_listings_checked_at"] == now and cols["psa9_live_auction_count"] == 2
    assert cols["psa9_next_auction_end"] == "2026-09-26T00:00:00+00:00" and cols["psa9_next_auction_bid"] == "5"
    assert cols["psa9_next_auction_bid_count"] == "0" and cols["psa9_next_auction_source"] == "Fanatics Collect"
    assert cols["psa9_next_auction_url"] == "u2" and cols["psa9_last_auction_end"] == "2026-09-27T00:00:00+00:00"
    assert cols["psa9_lowest_bin_price"] == "45" and cols["psa9_lowest_bin_source"] == "eBay" and cols["psa9_lowest_bin_url"] == "u3"
    # Never checked at PSA 9: every psa9 column blank (unknown), whatever rows exist.
    blank = metrics.live_cols("", rows9, prefix="psa9_")
    assert list(blank) == metrics.PSA9_LIVE_COLS and not any(blank.values())
    # Checked, nothing running: count 0, everything else blank.
    empty = metrics.live_cols(now, [], prefix="psa9_")
    assert empty["psa9_live_auction_count"] == 0 and empty["psa9_lowest_bin_price"] == "" and empty["psa9_next_auction_end"] == ""
    # The PSA 10 set is untouched by the prefix parameter.
    assert list(metrics.live_cols(now, [])) == metrics.LIVE_COLS
    print("psa9 live_cols ok")


def test_ingest_live_first_seen():
    """A listing keeps the day it was first recorded while alt.xyz keeps showing it; a new
    one gets the run day; a row from before the column existed stays blank until backfilled."""
    with tempfile.TemporaryDirectory() as d:
        store, run = Path(d) / "store", Path(d) / "run"
        store.mkdir(); run.mkdir()
        now = "2026-10-03T14:00:00+00:00"
        base = {"grading_company": "PSA", "grade": "10.0", "source": "eBay", "current_bid": "", "bid_count": "",
                "end_date": "", "buy_it_now_price": "", "url": "", "checked_at": "2026-10-02T14:00:00+00:00"}
        _write(store / "live_listings.csv", ingest.LIVE_COLS, [
            dict(base, asset_id="A", listing_type="BUY_IT_NOW", buy_it_now_price="100", url="a-bin", first_seen="2026-09-25"),
            dict(base, asset_id="A", listing_type="AUCTION", end_date="2026-10-09T00:00:00+00:00", url="a-auc", first_seen="2026-10-01"),
            dict(base, asset_id="B", listing_type="BUY_IT_NOW", buy_it_now_price="50", url="b-bin", first_seen=""),   # pre-column row
        ])
        cards = [{"asset_id": "A", "listings_checked_at": now}]
        _write(run / "listings.csv", ingest.LIVE_COLS[:-1], [          # the scraper's file has no first_seen
            dict(base, asset_id="A", listing_type="BUY_IT_NOW", buy_it_now_price="90", url="a-bin", checked_at=now),  # same listing, price edited
            dict(base, asset_id="A", listing_type="AUCTION", end_date="2026-10-09T00:00:00+00:00", url="a-auc", checked_at=now),
            dict(base, asset_id="A", listing_type="AUCTION", end_date="2026-10-10T00:00:00+00:00", url="a-auc2", checked_at=now),
        ])
        ingest.ingest_live(run, cards, store, day="2026-10-03")
        fs = {r["url"]: r["first_seen"] for r in ingest.read_csv(store / "live_listings.csv")}
        assert fs == {"a-bin": "2026-09-25", "a-auc": "2026-10-01", "a-auc2": "2026-10-03", "b-bin": ""}, fs
        # Run 2: a cheaper BIN replaces a-bin as the stored one; it is new, so it gets today.
        _write(run / "listings.csv", ingest.LIVE_COLS[:-1], [
            dict(base, asset_id="A", listing_type="BUY_IT_NOW", buy_it_now_price="90", url="a-bin", checked_at=now),
            dict(base, asset_id="A", listing_type="BUY_IT_NOW", buy_it_now_price="80", url="a-bin-cheaper", checked_at=now),
        ])
        ingest.ingest_live(run, cards, store, day="2026-10-04")
        fs = {r["url"]: r["first_seen"] for r in ingest.read_csv(store / "live_listings.csv")}
        assert fs == {"a-bin-cheaper": "2026-10-04", "b-bin": ""}, fs
        assert "first_seen" in ingest.LIVE_COLS and ingest.LIVE_COLS[-1] == "first_seen"
        print("first_seen ok")


def test_metrics_listing_age_cols():
    now = "2026-10-03T14:00:00+00:00"
    rows = [
        {"listing_type": "AUCTION", "end_date": "2026-10-05T00:00:00+00:00", "current_bid": "700", "bid_count": "0",
         "source": "eBay", "url": "u1", "buy_it_now_price": "", "first_seen": "2026-10-02"},
        {"listing_type": "AUCTION", "end_date": "2026-10-09T00:00:00+00:00", "current_bid": "5", "bid_count": "2",
         "source": "eBay", "url": "u2", "buy_it_now_price": "", "first_seen": "2026-10-03"},
        {"listing_type": "BUY_IT_NOW", "end_date": "", "current_bid": "", "bid_count": "", "source": "eBay", "url": "u3",
         "buy_it_now_price": "120", "first_seen": "2026-09-26"},
        {"listing_type": "BUY_IT_NOW", "end_date": "", "current_bid": "", "bid_count": "", "source": "eBay", "url": "u4",
         "buy_it_now_price": "95", "first_seen": "2026-10-01"},
    ]
    assert metrics.LISTING_AGE_COLS == ["lowest_bin_first_seen", "next_auction_bid_suspect",
                                        "psa9_lowest_bin_first_seen", "psa9_next_auction_bid_suspect"]
    # ref 100: the next auction (soonest end, u1) carries a $700 opening price = 7x -> suspect
    c = metrics.listing_age_cols(now, rows, 100.0)
    assert c == {"lowest_bin_first_seen": "2026-10-01", "next_auction_bid_suspect": 1}, c   # u4 is the cheapest BIN
    # exactly at the band is not above it
    assert metrics.listing_age_cols(now, rows, 700 / metrics.SUSPECT_BID_MULT)["next_auction_bid_suspect"] == 0
    # no clean reference -> blank (unknown), the BIN age still known
    c = metrics.listing_age_cols(now, rows, None)
    assert c == {"lowest_bin_first_seen": "2026-10-01", "next_auction_bid_suspect": ""}, c
    # never checked -> both blank, whatever the rows
    assert not any(metrics.listing_age_cols("", rows, 100.0).values())
    # checked, nothing running and no BIN -> both blank
    assert not any(metrics.listing_age_cols(now, [], 100.0).values())
    # a BIN row from before the column existed -> blank age
    c = metrics.listing_age_cols(now, [dict(rows[3], first_seen="")], 100.0)
    assert c["lowest_bin_first_seen"] == "" and c["next_auction_bid_suspect"] == ""
    # PSA 9 prefix
    c = metrics.listing_age_cols(now, rows, 100.0, prefix="psa9_")
    assert list(c) == ["psa9_lowest_bin_first_seen", "psa9_next_auction_bid_suspect"] and c["psa9_next_auction_bid_suspect"] == 1
    # live_cols itself is unchanged by the new rows' extra key
    assert list(metrics.live_cols(now, rows)) == metrics.LIVE_COLS
    print("listing age / suspect bid ok")


def test_ingest_listings_only():
    """A listings refresh replaces the checked grade's rows and moves the check time forward
    in the card's newest daily row; no daily row is added or replaced, sales untouched."""
    with tempfile.TemporaryDirectory() as d:
        store, run = Path(d) / "store", Path(d) / "run"
        (store / "daily").mkdir(parents=True); run.mkdir()
        night = "2026-10-02T14:00:00+00:00"
        refresh = "2026-10-03T02:10:00+00:00"
        daily_cols = ingest.DAILY_COLS
        _write(store / "daily" / "2026-10-01.csv", daily_cols, [
            {"asset_id": "A", "scraped_at": "2026-10-01T13:00:00+00:00", "pop_at_grade": "5", "listings_checked_at": "2026-10-01T13:00:00+00:00"},
            {"asset_id": "C", "scraped_at": "2026-10-01T13:00:00+00:00", "pop_at_grade": "9", "listings_checked_at": "2026-10-01T13:00:00+00:00"},
        ])
        _write(store / "daily" / "2026-10-02.csv", daily_cols, [
            {"asset_id": "A", "scraped_at": night, "pop_at_grade": "5", "num_sales": "3", "listings_checked_at": night, "psa9_listings_checked_at": night},
            {"asset_id": "B", "scraped_at": night, "pop_at_grade": "1", "listings_checked_at": night, "psa9_listings_checked_at": ""},
        ])
        base = {"grading_company": "PSA", "source": "eBay", "current_bid": "", "bid_count": "", "end_date": "",
                "buy_it_now_price": "", "url": "", "checked_at": night, "first_seen": "2026-10-02"}
        _write(store / "live_listings.csv", ingest.LIVE_COLS, [
            dict(base, asset_id="A", grade="10.0", listing_type="AUCTION", end_date="2026-10-03T01:00:00+00:00", url="a-ended"),
            dict(base, asset_id="A", grade="10.0", listing_type="AUCTION", end_date="2026-10-05T01:00:00+00:00", url="a-live"),
            dict(base, asset_id="A", grade="9.0", listing_type="BUY_IT_NOW", buy_it_now_price="40", url="a9"),
            dict(base, asset_id="B", grade="10.0", listing_type="AUCTION", end_date="2026-10-04T01:00:00+00:00", url="b-pulled"),
            dict(base, asset_id="C", grade="10.0", listing_type="BUY_IT_NOW", buy_it_now_price="70", url="c-bin"),
        ])
        # The refresh run (PSA 10): A still has a-live (a-ended is gone), B's auction was pulled.
        _write(run / "cards.csv", ["asset_id", "card_name", "grade", "listings_checked_at", "psa9_listings_checked_at"], [
            {"asset_id": "A", "card_name": "A", "grade": "10.0", "listings_checked_at": refresh},
            {"asset_id": "B", "card_name": "B", "grade": "10.0", "listings_checked_at": refresh},
            {"asset_id": "Z", "card_name": "Z", "grade": "10.0", "listings_checked_at": refresh},   # not in any daily file
        ])
        _write(run / "listings.csv", ingest.LIVE_COLS[:-1], [
            dict(base, asset_id="A", grade="10.0", listing_type="AUCTION", end_date="2026-10-05T01:00:00+00:00", url="a-live", checked_at=refresh),
        ])
        out = ingest.ingest_listings_only(run, "2026-10-03", store)
        by = _urls_by_key(store)
        assert by == {("A", "10.0"): ["a-live"], ("A", "9.0"): ["a9"], ("C", "10.0"): ["c-bin"]}, by
        fs = {r["url"]: r["first_seen"] for r in ingest.read_csv(store / "live_listings.csv")}
        assert fs["a-live"] == "2026-10-02", "a re-seen auction keeps its first_seen"
        d2 = {r["asset_id"]: r for r in ingest.read_csv(store / "daily" / "2026-10-02.csv")}
        assert sorted(d2) == ["A", "B"], "no daily row added or removed"
        assert d2["A"]["listings_checked_at"] == refresh and d2["A"]["psa9_listings_checked_at"] == night, d2["A"]
        assert d2["A"]["num_sales"] == "3" and d2["A"]["scraped_at"] == night, "the night's numbers stand"
        assert d2["B"]["listings_checked_at"] == refresh and d2["B"]["psa9_listings_checked_at"] == ""
        d1 = {r["asset_id"]: r for r in ingest.read_csv(store / "daily" / "2026-10-01.csv")}
        assert d1["A"]["listings_checked_at"] == "2026-10-01T13:00:00+00:00", "only the newest row of a card moves"
        assert d1["C"]["listings_checked_at"] == "2026-10-01T13:00:00+00:00", "an unrefreshed card is untouched"
        assert out["refresh_bumped"] == 2 and out["live_checks"] == {"10.0": 3, "9.0": 0}, out
        assert not (store / "daily" / "2026-10-03.csv").exists(), "a refresh never creates a daily file"
        # metrics reads the moved check time: the newest daily row wins
        daily, days = metrics.load_daily(store)
        assert days == ["2026-10-01", "2026-10-02"]
        print("listings-only ingest ok")


def test_scraper_listings_only():
    """alt_scraper.py --listings-only: one listings request per card, no pops or sales; the
    check time lands in the column for the grade asked."""
    import alt_scraper as s   # noqa: E402
    calls = []
    s.fetch_live_listings = lambda aid, company, grade: calls.append(("listings", aid, grade)) or [
        {"id": "L1", "auctionHouse": "eBay", "buyItNowPrice": None,
         "auctionInfo": {"endDate": "2026-10-05T01:00:00+00:00", "numBids": 3, "highestBid": 120},
         "attributes": {"grade": grade, "gradingCompany": company, "itemDetailUrl": "https://www.ebay.com/itm/1"}}]
    s.fetch_pops = lambda aid: calls.append(("pops", aid)) or []
    s.fetch_sales = lambda aid, company, grade: calls.append(("sales", aid, grade)) or []
    s.DELAY_SECONDS = 0
    with tempfile.TemporaryDirectory() as d:
        lst = Path(d) / "refresh_psa9.txt"
        lst.write_text("asset:00000000-0000-0000-0000-00000000000a   # card A\n")
        import json
        (Path(d) / "refresh_psa9.json").write_text(json.dumps({"00000000-0000-0000-0000-00000000000a": {
            "id": "00000000-0000-0000-0000-00000000000a", "name": "card A", "year": "2003", "subject": "Mew",
            "category": "POKEMON_CARDS", "brand": "Set", "variety": "Holo", "cardNumber": "1"}}))
        sys.argv = ["alt_scraper.py", "--listings-only", "--grade", "9", "--keep-empty", "--out", d, str(lst)]
        s.main()
        rows = list(csv.DictReader(open(Path(d) / "cards.csv")))
        assert [c[0] for c in calls] == ["listings"], calls
        assert calls[0][2] == "9.0"
        assert len(rows) == 1 and rows[0]["card_name"] == "card A" and rows[0]["grade"] == "9.0", rows
        assert rows[0]["listings_checked_at"] == "" and rows[0]["psa9_listings_checked_at"], rows[0]
        assert rows[0]["pop_at_grade"] == "" and rows[0]["num_sales"] in ("", "0"), "pops/sales not fetched"
        lrows = list(csv.DictReader(open(Path(d) / "listings.csv")))
        assert len(lrows) == 1 and lrows[0]["grade"] == "9.0" and lrows[0]["listing_type"] == "AUCTION", lrows
        assert not list(csv.DictReader(open(Path(d) / "sales.csv")))
        print("scraper listings-only ok")


if __name__ == "__main__":
    test_ingest_live_keeps_snapshots_and_ages_bins()
    test_ingest_live_replaces_per_grade()
    test_metrics_psa9_live_cols()
    test_ingest_live_first_seen()
    test_metrics_listing_age_cols()
    test_ingest_listings_only()
    test_scraper_listings_only()
    print("all live-listing checks passed")
