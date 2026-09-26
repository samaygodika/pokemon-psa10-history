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


if __name__ == "__main__":
    test_ingest_live_keeps_snapshots_and_ages_bins()
    test_ingest_live_replaces_per_grade()
    test_metrics_psa9_live_cols()
    print("all live-listing checks passed")
