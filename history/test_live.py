#!/usr/bin/env python3
"""Checks for live-listings snapshot keeping and Buy It Now aging in ingest/metrics (2026-09-25).

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


if __name__ == "__main__":
    test_ingest_live_keeps_snapshots_and_ages_bins()
    print("all live-listing checks passed")
