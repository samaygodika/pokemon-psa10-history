#!/usr/bin/env python3
"""Checks for the live-listings health gate and snapshot keeping (2026-09-25).

    python3 history/test_live.py

Plain asserts, no pytest (it isn't installed here)."""
import csv
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "history"))
from alt_scraper import ListingsHealth            # noqa: E402
import ingest                                     # noqa: E402
import metrics                                    # noqa: E402


def test_health_gate():
    h = ListingsHealth()
    # First MIN_SEEN answers: no verdict yet, empties trusted even if all empty.
    for _ in range(h.MIN_SEEN - 1):
        assert h.record([], "t") is True
    assert h.untrusted == 0 and h.healthy
    # A healthy stretch: 31% with listings, empties are real zeros.
    h = ListingsHealth()
    for i in range(400):
        ok = h.record([{"x": 1}] if i % 3 == 0 else [], "t")
        assert ok is True
    assert h.healthy and h.untrusted == 0 and not h.flips
    # Endpoint goes quiet: once the rolling rate is < 10%, empties are untrusted...
    for i in range(400):
        h.record([], f"2026-09-25T14:{i % 60:02d}:00+00:00")
    assert not h.healthy and h.flips and h.flips[-1][2] is False
    assert h.untrusted > 0
    # ...but a non-empty answer is always trusted, and does not by itself flip the gate.
    assert h.record([{"x": 1}], "t") is True and not h.healthy
    # Recovery: enough listings again -> healthy, empties trusted again.
    for i in range(300):
        h.record([{"x": 1}] if i % 2 == 0 else [], "t")
    assert h.healthy and h.flips[-1][2] is True
    before = h.untrusted
    assert h.record([], "t") is True and h.untrusted == before
    assert "unchecked" in h.summary()
    print("health gate ok:", h.summary())


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
    test_health_gate()
    test_ingest_live_keeps_snapshots_and_ages_bins()
    print("all live-listing checks passed")
