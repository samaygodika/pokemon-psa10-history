#!/usr/bin/env python3
"""Checks for history/ebay_verify.py (2026-10-06): which listings are picked, how eBay's
answers are read, what apply drops, and that ingest/metrics honour the verdicts.

    python3 history/test_ebay_verify.py

Plain asserts, no pytest. urlopen is faked: nothing here talks to eBay."""
import csv
import io
import json
import os
import sys
import tempfile
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "history"))
import ebay_verify as ev   # noqa: E402
import ingest              # noqa: E402
import metrics             # noqa: E402

NOW = datetime(2026, 10, 7, 8, 0, tzinfo=timezone.utc)


def _write(path, cols, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in cols} for r in rows)


def row(aid, url, kind="BUY_IT_NOW", price="100", first_seen="2026-10-01", grade="10.0", source="eBay"):
    return {"asset_id": aid, "grading_company": "PSA", "grade": grade, "listing_type": kind, "source": source,
            "buy_it_now_price": price if kind != "AUCTION" else "", "end_date": "2026-10-09T00:00:00+00:00" if kind == "AUCTION" else "",
            "url": url, "checked_at": "2026-10-06T14:00:00+00:00", "first_seen": first_seen}


class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


class FakeEbay:
    """item id -> dict (200 body) | int (HTTP error status; 404 carries errorId 11003)."""

    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def __call__(self, req, timeout=None):
        if req.full_url.startswith(ev.TOKEN_URL):
            return Resp(json.dumps({"access_token": "t", "expires_in": 7200}).encode())
        iid = req.full_url.split("legacy_item_id=")[1]
        self.asked.append(iid)
        a = self.answers.get(iid, 500)
        if isinstance(a, int):
            body = json.dumps({"errors": [{"errorId": ev.NOT_FOUND}]}) if a == 404 else "{}"
            raise urllib.error.HTTPError(req.full_url, a, "err", {}, io.BytesIO(body.encode()))
        return Resp(json.dumps(a).encode())


def test_item_id():
    assert ev.item_id("https://www.ebay.com/itm/286456815956") == "286456815956"
    assert ev.item_id("https://www.ebay.com/itm/some-title/286456815956?hash=x") == "286456815956"
    assert ev.item_id("https://www.ebay.co.uk/itm/123456789012") == "123456789012"
    assert ev.item_id("https://www.fanaticscollect.com/x/123456789012") is None
    assert ev.item_id("") is None and ev.item_id(None) is None
    print("item_id ok")


def test_pick_order():
    years = {"V1": "1999", "V2": "2003", "M1": "2021"}
    rows = [row("V1", "https://www.ebay.com/itm/100000001", first_seen="2026-10-03"),
            row("V2", "https://www.ebay.com/itm/100000002", first_seen="2026-09-25"),
            row("M1", "https://www.ebay.com/itm/100000003", first_seen="2026-09-25"),
            row("V1", "https://www.ebay.com/itm/100000004", kind="AUCTION"),            # auctions never
            row("V2", "https://www.fanaticscollect.com/lot/9", first_seen="2026-09-25"),  # not eBay
            row("V2", "https://www.ebay.com/itm/100000005", first_seen="2026-09-26"),   # checked live 2 days ago
            row("V2", "https://www.ebay.com/itm/100000006", first_seen="2026-09-26"),   # checked live 9 days ago
            row("V2", "https://www.ebay.com/itm/100000007", first_seen="2026-09-26"),   # already dead
            row("M1", "https://www.ebay.com/itm/100000002", grade="9.0")]               # same listing twice -> once
    checks = {"100000005": {"status": "live", "checked_at": "2026-10-05T08:00:00+00:00"},
              "100000006": {"status": "live", "checked_at": "2026-09-28T08:00:00+00:00"},
              "100000007": {"status": "gone", "checked_at": "2026-09-20T08:00:00+00:00"}}
    order = ev.pick(rows, checks, years, 100, NOW)
    # tier 0: vintage never checked, oldest first_seen first; tier 1: vintage re-check; tier 2: modern
    assert order == ["100000002", "100000001", "100000006", "100000003"], order
    assert ev.pick(rows, checks, years, 2, NOW) == ["100000002", "100000001"], "the budget cuts from the end"
    print("pick ok")


def test_check_and_apply():
    with tempfile.TemporaryDirectory() as d:
        store, out = Path(d) / "store", Path(d) / "out"
        store.mkdir()
        _write(store / "assets.csv", ["asset_id", "year"], [{"asset_id": a, "year": "2003"} for a in "ABCDE"])
        _write(store / "live_listings.csv", ingest.LIVE_COLS, [
            row("A", "https://www.ebay.com/itm/200000001"),                      # live
            row("B", "https://www.ebay.com/itm/200000002"),                      # ended (past itemEndDate)
            row("C", "https://www.ebay.com/itm/200000003"),                      # gone (404 11003)
            row("D", "https://www.ebay.com/itm/200000004"),                      # 500: no answer
            row("E", "https://www.ebay.com/itm/200000005", kind="AUCTION"),      # not asked, never dropped
        ])
        fake = FakeEbay({"200000001": {"itemId": "v1|200000001|0", "price": {"value": "95.00"}},
                         "200000002": {"itemId": "v1|200000002|0", "itemEndDate": "2026-10-01T00:00:00.000Z", "price": {"value": "80.00"}},
                         "200000003": 404, "200000004": 500})
        os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"] = "id", "secret"
        try:
            got = ev.check(out, budget=10, workers=2, store=store, urlopen=fake, now=NOW)
        finally:
            del os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"]
        assert sorted(fake.asked) == ["200000001", "200000002", "200000003", "200000004"], fake.asked
        assert got == {"asked": 4, "answered": 3, "live": 1, "ended": 1, "gone": 1, "rate_limited": False}, got
        assert not (store / "ebay_checks.csv").exists(), "check alone never touches history/"
        ev.apply(out, store)
        checks = ev.load_checks(store)
        assert {i: c["status"] for i, c in checks.items()} == {"200000001": "live", "200000002": "ended", "200000003": "gone"}
        assert checks["200000002"]["first_dead_at"] and not checks["200000001"]["first_dead_at"]
        urls = {r["url"] for r in ingest.read_csv(store / "live_listings.csv")}
        assert urls == {"https://www.ebay.com/itm/200000001", "https://www.ebay.com/itm/200000004",
                        "https://www.ebay.com/itm/200000005"}, urls
        before = (store / "ebay_checks.csv").read_text()
        ev.apply(out, store)
        assert (store / "ebay_checks.csv").read_text() == before, "apply is idempotent"
        # a later "live" answer never resurrects an ended listing
        _write(out / "ebay_results.csv", ev.RESULT_COLS, [{"item_id": "200000002", "status": "live", "checked_at": "2026-10-08T08:00:00+00:00"}])
        ev.apply(out, store)
        assert ev.load_checks(store)["200000002"]["status"] == "ended"
        # the next run picks nothing it has a fresh or final answer for
        assert ev.pick(ingest.read_csv(store / "live_listings.csv"), ev.load_checks(store), {"D": "2003"}, 10, NOW) == ["200000004"]
        print("check/apply ok")


def test_rate_limit_stops():
    with tempfile.TemporaryDirectory() as d:
        store, out = Path(d) / "store", Path(d) / "out"
        store.mkdir()
        _write(store / "live_listings.csv", ingest.LIVE_COLS, [row("A", f"https://www.ebay.com/itm/30000000{i}") for i in range(5)])
        fake = FakeEbay({f"30000000{i}": 429 for i in range(5)})
        os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"] = "id", "secret"
        try:
            got = ev.check(out, budget=10, workers=1, store=store, urlopen=fake, now=NOW)
        finally:
            del os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"]
        assert got["rate_limited"] and got["answered"] == 0 and len(fake.asked) == 1, (got, fake.asked)
        print("rate limit ok")


def test_no_keys_is_a_no_op():
    with tempfile.TemporaryDirectory() as d:
        os.environ["EBAY_KEYS_FILE"] = str(Path(d) / "none")
        for k in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET"):
            os.environ.pop(k, None)
        try:
            assert ev.check(Path(d) / "out", store=Path(d)) == {"asked": 0}
        finally:
            del os.environ["EBAY_KEYS_FILE"]
        print("no keys ok")


def test_ingest_skips_dead_and_metrics_column():
    with tempfile.TemporaryDirectory() as d:
        store, run = Path(d) / "store", Path(d) / "run"
        store.mkdir(); run.mkdir()
        _write(store / "ebay_checks.csv", ev.CHECK_COLS, [
            {"item_id": "400000001", "status": "gone", "checked_at": "2026-10-06T08:00:00+00:00"},
            {"item_id": "400000002", "status": "live", "checked_at": "2026-10-06T08:00:00+00:00"}])
        _write(store / "live_listings.csv", ingest.LIVE_COLS, [])
        now = "2026-10-07T14:00:00+00:00"
        # alt.xyz re-serves the dead $50 listing; the $70 live one must be the kept BIN
        _write(run / "listings.csv", ingest.LIVE_COLS[:-1], [
            dict(row("A", "https://www.ebay.com/itm/400000001", price="50"), checked_at=now),
            dict(row("A", "https://www.ebay.com/itm/400000002", price="70"), checked_at=now)])
        out = ingest.ingest_live(run, [{"asset_id": "A", "listings_checked_at": now}], store, "2026-10-07")
        kept = ingest.read_csv(store / "live_listings.csv")
        assert [r["url"] for r in kept] == ["https://www.ebay.com/itm/400000002"], kept
        assert out["bins_ebay_dead_skipped"] == 1
        ebay_live = metrics.load_ebay_live(store)
        assert metrics.ebay_verified_at({"lowest_bin_url": kept[0]["url"]}, ebay_live) == {"lowest_bin_verified_at": "2026-10-06T08:00:00+00:00"}
        assert metrics.ebay_verified_at({"psa9_lowest_bin_url": "https://www.fanaticscollect.com/x"}, ebay_live, "psa9_") == {"psa9_lowest_bin_verified_at": ""}
        assert metrics.ebay_verified_at({"lowest_bin_url": ""}, ebay_live) == {"lowest_bin_verified_at": ""}
        assert metrics.EBAY_COLS == ["lowest_bin_verified_at", "psa9_lowest_bin_verified_at"]
        print("ingest skip + metrics column ok")


if __name__ == "__main__":
    test_item_id()
    test_pick_order()
    test_check_and_apply()
    test_rate_limit_stops()
    test_no_keys_is_a_no_op()
    test_ingest_skips_dead_and_metrics_column()
    print("all ebay-verify checks passed")
