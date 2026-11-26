#!/usr/bin/env python3
"""Checks for history/alt_bids.py (2026-11-26): the Alt auction ledger, the bid fetch into the store,
pairing an auction with its sale, and latest/alt_auctions.csv + latest/alt_bidders/.

    python3 history/test_alt_bids.py

Plain asserts, no pytest (it isn't installed here)."""
import csv
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "history"))
import alt_bids                                   # noqa: E402
import ingest                                     # noqa: E402
import store as store_mod                         # noqa: E402

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
A, B = "aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002"
KEY1, KEY2, KEY3 = "1" * 32, "2" * 32, "3" * 32


def _write(path, cols, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in cols} for r in rows)


def _read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _alt_row(lid, asset=A, grade="10.0", end="2026-11-20T02:02:00+00:00", kind="AUCTION", source="Alt"):
    return {"asset_id": asset, "grading_company": "PSA", "grade": grade, "listing_type": kind, "source": source,
            "current_bid": "100", "bid_count": "3", "end_date": end, "buy_it_now_price": "" if kind == "AUCTION" else "50",
            "url": f"https://alt.xyz/itm/{lid}", "alt_listing_id": lid, "checked_at": "2026-11-19T10:00:00+00:00"}


def _event(amount, at, key, auto=False, handle="AB***"):
    return {"bidAmount": f"{amount}.000000", "isAutomatic": auto, "occurredAt": at, "bidder": {"key": key, "handle": handle}}


def _feed(events, ends="2026-11-20T08:38:46+00:00"):
    return {"bidCount": len(events), "auctionEndsAt": ends, "currentPrice": events[0]["bidAmount"] if events else "1.000000",
            "events": events, "error": None}


def test_record_keeps_first_seen_and_ignores_non_auctions():
    with tempfile.TemporaryDirectory() as d:
        store = Path(d)
        n = alt_bids.record([_alt_row("L1"), _alt_row("L2", kind="BUY_IT_NOW"), _alt_row("L3", source="eBay")], store, "2026-11-18")
        assert n == 1, n
        # seen again later, extended: end moves, first_seen stays; a new one is added
        n = alt_bids.record([_alt_row("L1", end="2026-11-20T03:04:20+00:00"), _alt_row("L4", grade="9")], store, "2026-11-19")
        assert n == 1, n
        rows = {r["alt_listing_id"]: r for r in _read(store / alt_bids.LEDGER)}
        assert set(rows) == {"L1", "L4"}, rows
        assert rows["L1"]["first_seen"] == "2026-11-18" and rows["L1"]["end_date"] == "2026-11-20T03:04:20+00:00", rows["L1"]
        assert rows["L4"]["grade"] == "9.0" and rows["L4"]["first_seen"] == "2026-11-19", rows["L4"]


def test_ingest_alt_records_the_ledger():
    with tempfile.TemporaryDirectory() as d:
        store = Path(d)
        _write(store / "alt_listings.csv", list(_alt_row("x")), [_alt_row("L1"), _alt_row("L2", kind="BUY_IT_NOW")])
        ingest.ingest_alt(store / "alt_listings.csv", store, "2026-11-18")
        assert [r["alt_listing_id"] for r in _read(store / alt_bids.LEDGER)] == ["L1"]


def test_parse_feed_outcomes():
    row = {"alt_listing_id": "L1", "asset_id": A, "grade": "10.0", "end_date": "2026-11-20T02:02:00+00:00"}
    ev = [_event(120, "2026-11-20T08:30:00.1+00:00", KEY1), _event(110, "2026-11-20T08:29:00+00:00", KEY2, auto=True),
          _event(100, "2026-11-19T10:00:00+00:00", KEY1)]
    c, bids = alt_bids.parse_feed(row, _feed(ev), NOW)
    assert c["status"] == "ok" and c["final_bid"] == "120" and c["bid_count"] == "3" and c["bidders"] == "2", c
    assert c["ended_at"] == "2026-11-20T08:38:46+00:00", c
    assert [b["amount"] for b in bids] == ["120", "110", "100"] and bids[1]["is_automatic"] == "1", bids
    # extended past now: not recorded, asked again
    assert alt_bids.parse_feed(row, _feed(ev, ends="2026-11-22T00:00:00+00:00"), NOW) == (None, [])
    # a list shorter than bidCount: not recorded
    short = dict(_feed(ev), bidCount=5)
    assert alt_bids.parse_feed(row, short, NOW) == (None, [])
    # no bids: ok, blank final bid
    c, bids = alt_bids.parse_feed(row, _feed([]), NOW)
    assert c["status"] == "ok" and c["bid_count"] == "0" and c["final_bid"] == "" and bids == [], c
    # unknown listing: not_found; any other error: retried
    nf = {"error": {"__typename": "InvalidInputError", "message": "Listing L1 not found"}}
    assert alt_bids.parse_feed(row, nf, NOW)[0]["status"] == "not_found"
    assert alt_bids.parse_feed(row, {"error": {"__typename": "DbError", "message": "timeout"}}, NOW) == (None, [])


def test_fetch_budget_order_failures_and_healing():
    with tempfile.TemporaryDirectory() as d:
        store = Path(d)
        _write(store / alt_bids.LEDGER, alt_bids.LEDGER_COLS, [
            {"alt_listing_id": "OLD", "asset_id": A, "grade": "10.0", "end_date": "2026-11-13T02:02:00+00:00"},
            {"alt_listing_id": "NEW", "asset_id": A, "grade": "10.0", "end_date": "2026-11-20T02:02:00+00:00"},
            {"alt_listing_id": "FAIL", "asset_id": B, "grade": "9.0", "end_date": "2026-11-20T01:00:00+00:00"},
            {"alt_listing_id": "SOON", "asset_id": B, "grade": "9.0", "end_date": "2026-11-21T11:00:00+00:00"},  # < 2 h ago
            {"alt_listing_id": "LIVE", "asset_id": B, "grade": "9.0", "end_date": "2026-11-27T02:02:00+00:00"},
        ])
        asked = []

        def ask(lid):
            asked.append(lid)
            if lid == "FAIL":
                raise RuntimeError("network")
            return _feed([_event(50, "2026-11-19T00:00:00+00:00", KEY1)],
                         ends="2026-11-13T02:05:00+00:00" if lid == "OLD" else "2026-11-20T02:02:00+00:00")

        st = alt_bids.fetch(store, budget=2, workers=1, now=NOW, ask=ask)
        assert asked == ["NEW", "FAIL"], asked            # newest end first, budget 2
        assert st["alt_bids_checked"] == 1 and st["alt_bids_failed"] == 1 and st["alt_bids_left"] == 1, st
        asked.clear()
        st = alt_bids.fetch(store, budget=10, workers=1, now=NOW, ask=ask)
        assert asked == ["FAIL", "OLD"], asked            # NEW is done; FAIL asked again
        checks = alt_bids.load_checks(store)
        assert set(checks) == {"NEW", "OLD"}, checks
        assert (store / "alt_bids" / "auctions-2026-11.csv").exists() and (store / "alt_bids" / "bids-2026-11.csv").exists()
        # the bids of a checked auction went missing from the store: asked again
        bp = store / "alt_bids" / "bids-2026-11.csv"
        _write(bp, alt_bids.BID_COLS, [r for r in _read(bp) if r["alt_listing_id"] != "OLD"])
        asked.clear()
        alt_bids.fetch(store, budget=10, workers=1, now=NOW, ask=ask)
        assert asked == ["FAIL", "OLD"], asked            # FAIL still failing; OLD's bids asked again (NEW's are there)
        assert len([r for r in _read(bp) if r["alt_listing_id"] == "OLD"]) == 1


def _sale(asset, date, price, source="Alt", stype="AUCTION", grade="10.0", tx="t"):
    return {"asset_id": asset, "date": date, "price": price, "grading_company": "PSA", "grade": grade, "source": source,
            "sale_type": stype, "url": f"https://alt.xyz/exchange/select-listing?ids={tx}", "alt_tx_id": tx}


def test_match_sales_premium_window_and_one_to_one():
    checks = [
        {"alt_listing_id": "L1", "asset_id": A, "grade": "10.0", "ended_at": "2026-11-20T08:38:46+00:00", "status": "ok", "final_bid": "792"},
        {"alt_listing_id": "L2", "asset_id": A, "grade": "10.0", "ended_at": "2026-11-20T08:38:46+00:00", "status": "ok", "final_bid": "792"},
        {"alt_listing_id": "L3", "asset_id": A, "grade": "10.0", "ended_at": "2026-11-20T08:38:46+00:00", "status": "ok", "final_bid": "792"},
        {"alt_listing_id": "L4", "asset_id": B, "grade": "9.0", "ended_at": "2026-11-20T08:38:46+00:00", "status": "ok", "final_bid": "5000"},
    ]
    sales = {(A, "10.0"): [_sale(A, "2026-11-19", "950.40", tx="s1"), _sale(A, "2026-11-20", "950.40", tx="s2"),
                           _sale(A, "2026-11-12", "950.40", tx="old"), _sale(A, "2026-11-19", "792.00", tx="nopremium")],
             (B, "9.0"): [_sale(B, "2026-11-19", "6000.00", grade="9.0", tx="s4")]}
    m = alt_bids.match_sales(checks, sales)
    assert m["L1"]["alt_tx_id"] == "s1" and m["L2"]["alt_tx_id"] == "s2", m   # one sale each, in id order
    assert "L3" not in m, m                                                    # the third has no sale left
    assert m["L4"]["alt_tx_id"] == "s4", m


def test_publish_files():
    with tempfile.TemporaryDirectory() as d:
        store, latest = Path(d) / "h", Path(d) / "latest"
        _write(store / alt_bids.LEDGER, alt_bids.LEDGER_COLS, [
            {"alt_listing_id": "L1", "asset_id": A, "grade": "10.0", "end_date": "2026-11-20T02:02:00+00:00"},
            {"alt_listing_id": "L2", "asset_id": B, "grade": "9.0", "end_date": "2026-11-20T02:02:00+00:00"},
            {"alt_listing_id": "L3", "asset_id": B, "grade": "9.0", "end_date": "2026-11-20T02:02:00+00:00"}])
        feeds = {"L1": _feed([_event(792, "2026-11-20T08:38:00+00:00", KEY2), _event(780, "2026-11-20T08:37:00+00:00", KEY1, auto=True),
                              _event(700, "2026-11-20T08:00:00+00:00", KEY2), _event(10, "2026-11-14T00:00:00+00:00", KEY3)]),
                 "L2": _feed([_event(25, "2026-11-15T00:00:00+00:00", KEY1)]),
                 "L3": _feed([])}
        alt_bids.fetch(store, budget=10, workers=1, now=NOW, ask=lambda lid: feeds[lid])
        _write(store / "sales" / "2026-11.csv", ingest.SALE_COLS, [_sale(A, "2026-11-19", "950.40", tx="s1"),
                                                                   _sale(A, "2026-11-19", "950.40", source="eBay", tx="e1")])
        (latest / "alt_bidders").mkdir(parents=True)
        (latest / "alt_bidders" / "zz.csv").write_text("stale\n")
        st = alt_bids.publish(store, latest)
        assert st == {"alt_auctions_published": 2, "alt_auctions_matched": 1, "alt_bidder_rows": 4}, st
        auc = {r["alt_listing_id"]: r for r in _read(latest / "alt_auctions.csv")}
        assert set(auc) == {"L1", "L2"}, auc                                      # L3 had no bids
        a = auc["L1"]
        assert (a["final_bid"], a["price_with_premium"], a["bid_count"], a["bidders"], a["auto_bids"]) == ("792", "950.4", "4", "3", "1"), a
        assert a["winner"] == KEY2[:12] and a["winner_bids"] == "2" and a["runner_up"] == KEY1[:12] and a["runner_up_max_bid"] == "780", a
        assert a["sale_date"] == "2026-11-19" and a["sale_price"] == "950.4" and a["sale_url"].endswith("ids=s1"), a
        assert auc["L2"]["sale_url"] == "" and auc["L2"]["runner_up"] == "", auc["L2"]
        assert sorted(p.name for p in (latest / "alt_bidders").glob("*.csv")) == ["aa.csv", "bb.csv"]   # stale shard gone
        rows = _read(latest / "alt_bidders" / "aa.csv")
        assert [(r["bidder"], r["max_bid"], r["bids"], r["auto_bids"]) for r in rows] == \
            [(KEY2[:12], "792", "2", "0"), (KEY1[:12], "780", "1", "1"), (KEY3[:12], "10", "1", "0")], rows
        assert rows[0]["first_bid_at"] == "2026-11-20T08:00:00+00:00" and rows[0]["last_bid_at"] == "2026-11-20T08:38:00+00:00", rows[0]
        # publishing again is byte-identical (no timestamps of its own)
        before = (latest / "alt_auctions.csv").read_bytes()
        alt_bids.publish(store, latest)
        assert (latest / "alt_auctions.csv").read_bytes() == before


def test_winner_on_a_tie_is_the_standing_proxy():
    # 884e5fd9 (2026-11-06): KEY1 bid 14 with a proxy above it, KEY2 bid 15 by hand, KEY1's proxy matched 15
    # at the same instant -> KEY1 won, though KEY2's first bid came earlier
    c = {"alt_listing_id": "L", "asset_id": A, "grade": "9.0"}
    b = lambda at, amt, key, auto: {"alt_listing_id": "L", "occurred_at": at, "amount": amt,
                                    "is_automatic": "1" if auto else "0", "bidder_key": key, "bidder_handle": "X***"}
    bids = [b("2026-11-05T14:23:32.98+00:00", "9", KEY2, False), b("2026-11-06T05:58:42.94+00:00", "14", KEY1, False),
            b("2026-11-06T06:30:31.45+00:00", "15", KEY2, False), b("2026-11-06T06:30:31.45+00:00", "15", KEY1, True)]
    rows = alt_bids.bidder_rows(c, bids)
    assert [r["bidder"] for r in rows] == [KEY1[:12], KEY2[:12]], rows
    rows = alt_bids.bidder_rows(c, list(reversed(bids)))   # feed order doesn't matter
    assert rows[0]["bidder"] == KEY1[:12], rows


def test_store_carries_alt_bids():
    assert "alt_bids" in store_mod.STORE_DIRS
    with tempfile.TemporaryDirectory() as d:
        h = Path(d) / "history"
        for sub, name in (("sales", "2026-11.csv"), ("daily", "2026-11-20.csv"), ("alt_bids", "bids-2026-11.csv")):
            (h / sub).mkdir(parents=True)
            (h / sub / name).write_text("a\n1\n")
        tar = Path(d) / "g.tar.gz"
        counts = store_mod.make_tarball(h, tar)
        assert counts == {"sales": 1, "daily": 1, "alt_bids": 1}, counts
        before = store_mod.content_hash(h)
        (h / "alt_bids" / "bids-2026-11.csv").write_text("changed\n")
        store_mod.extract_tarball(tar, h)
        assert store_mod.content_hash(h) == before
        # an older generation without alt_bids/ extracts to an empty folder
        old = Path(d) / "old.tar.gz"
        import tarfile
        with tarfile.open(old, "w:gz") as t:
            t.add(h / "sales" / "2026-11.csv", arcname="sales/2026-11.csv")
        store_mod.extract_tarball(old, h)
        assert (h / "alt_bids").is_dir() and not list((h / "alt_bids").iterdir())


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
