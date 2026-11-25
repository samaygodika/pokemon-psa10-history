#!/usr/bin/env python3
"""Checks for analysis/audit_pct_change.py (2026-11-24): the audit reads Sid's flagged sales
(history/flagged_sales.csv) itself, so it agrees with the feed on a flagged card and fails a feed
that ignored the flag.

Found on 2026-11-24: the Power Keepers Vaporeon Gold Star (3d7ec3d3) PSA 10 sale of 2026-09-13
($21,775, eBay) is flagged by Sid; metrics.py leaves it out (FLAGGED, 2026-10-28) but the audit
counted it, so volume_90d / 180d / 1y were one higher in the audit than in the feed.

    python3 analysis/test_audit_pct_change.py

Plain asserts, no pytest. Builds a tiny store in a temp dir and runs the real metrics.build on it."""
import contextlib
import csv
import io
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "history"))
sys.path.insert(0, str(ROOT / "analysis"))
import audit_pct_change as audit   # noqa: E402
import flagged_sales               # noqa: E402
import ingest                      # noqa: E402
import metrics                     # noqa: E402

TODAY = date(2026, 11, 24)
A = "aa000000-0000-0000-0000-000000000001"   # the Vaporeon-like card: a flagged sale in the 90 d window
B = "bb000000-0000-0000-0000-000000000002"   # same day and price as A's flagged sale, another card: not flagged
C = "cc000000-0000-0000-0000-000000000003"   # the newest sale is flagged: last_sale_status looks past it
D = "dd000000-0000-0000-0000-000000000004"   # a flag with another eBay item id: not this sale
FLAGS_HEADER = "asset_id,grade,date,price,url,flagged_at\n"


def _write(path, cols, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in cols} for r in rows)


def sale(aid, ago, price, item, status=""):
    sd = TODAY - timedelta(days=ago)
    return {"asset_id": aid, "date": sd.isoformat(), "price": f"{price:.2f}", "grading_company": "PSA", "grade": "10.0",
            "source": "eBay", "sale_type": "AUCTION", "url": f"https://www.ebay.com/itm/{item}", "label": "",
            "subject_to_change": "False", "skipped_reason": status, "alt_tx_id": f"tx{item}"}


def day(ago):
    return (TODAY - timedelta(days=ago)).isoformat()


def make_store(root):
    sales = []
    for aid, base in ((A, 50000), (B, 50000), (C, 50000), (D, 50000)):
        sales += [sale(aid, 300 - k * 40, base + k * 1000, f"{aid[:2]}{k}0000000") for k in range(6)]   # 6 clean, 300..100 days ago
    sales += [sale(A, 72, 21775, "178465997592")]            # flagged (Sid's URL carries ?nordt=true)
    sales += [sale(B, 72, 21775, "222222222222")]            # same day / price, other card
    sales += [sale(C, 2, 60000, "333333333333")]             # flagged, newest
    sales += [sale(D, 50, 52000, "444444444444")]            # flag names another item id
    by_month = {}
    for s in sales:
        by_month.setdefault(s["date"][:7], []).append(s)
    for m, rows in by_month.items():
        _write(root / "sales" / f"{m}.csv", ingest.SALE_COLS, rows)
    ids = (A, B, C, D)
    _write(root / "assets.csv", ["asset_id", "card_name", "year", "set", "card_number", "subject", "variety", "first_seen", "last_seen"],
           [{"asset_id": v, "card_name": f"card {v[:2]}", "year": "2007", "first_seen": "2026-09-01", "last_seen": TODAY.isoformat()} for v in ids])
    _write(root / "daily" / f"{TODAY}.csv", ingest.DAILY_COLS,
           [{"asset_id": v, "pop_at_grade": "10", "num_sales": "7", "scraped_at": f"{TODAY}T10:00:00+00:00", "sales_fetched": "1"} for v in ids])
    (root / "flagged_sales.csv").write_text(
        FLAGS_HEADER
        + f"{A},10,{day(72)},21775.00,https://www.ebay.com/itm/178465997592?nordt=true,2026-10-29T18:08:11Z\n"
        + f"{C},10,{day(2)},60000,,2026-11-23T00:00:00Z\n"
        + f"{D},10,{day(50)},52000.00,https://www.ebay.com/itm/999999999999,2026-11-23T00:00:00Z\n")


def build(root, out, ignore_flags=False):
    load = flagged_sales.load
    if ignore_flags:
        flagged_sales.load = lambda store=None: {}
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            metrics.build(root, out)
    finally:
        flagged_sales.load = load
    return {r["asset_id"]: r for r in csv.DictReader(open(out / "cards.csv", newline="", encoding="utf-8"))}


def run_audit(root, feed):
    old = audit.STORE, audit.TITLE_CHECKS, sys.argv
    audit.STORE, audit.TITLE_CHECKS = root, root / "sale_title_checks.csv"
    sys.argv = ["audit_pct_change.py", "--feed", str(feed)]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = audit.main()
    finally:
        audit.STORE, audit.TITLE_CHECKS, sys.argv = old
    return code, buf.getvalue()


def test_match_rule():
    flags = audit.load_flags(io_path(FLAGS_HEADER + "x,10.0,2026-09-13,21775,https://www.ebay.com/itm/178465997592?nordt=true,\n"))
    assert audit.is_flagged(flags, "x", "10.0", "2026-09-13", 21775.0, "https://www.ebay.com/itm/178465997592")   # nordt / grade 10 vs 10.0
    assert audit.is_flagged(flags, "x", "10", "2026-09-13T00:00:00", 21775.004, "")                              # no URL on our side
    assert not audit.is_flagged(flags, "x", "10.0", "2026-09-13", 21775.0, "https://www.ebay.com/itm/178465997593")   # another item
    assert not audit.is_flagged(flags, "x", "10.0", "2026-09-13", 21775.01, "")   # a cent off
    assert not audit.is_flagged(flags, "x", "9.0", "2026-09-13", 21775.0, "")     # another grade
    assert not audit.is_flagged(flags, "y", "10.0", "2026-09-13", 21775.0, "")    # another card
    assert audit.load_flags(Path("/nonexistent/flagged_sales.csv")) == {}


def io_path(text):
    td = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
    td.write(text)
    td.close()
    return Path(td.name)


def test_audit_agrees_with_feed_on_flagged_sales():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "store"
        make_store(root)
        feed = build(root, Path(tmp) / "latest")
        assert (feed[A]["volume_90d"], feed[A]["volume_1y"]) == ("0", "6")   # the flagged one is the only 90 d sale: left out
        assert (feed[B]["volume_90d"], feed[B]["volume_1y"]) == ("1", "7")   # B's twin sale counts
        assert feed[C]["clean_last_sale_date"] == day(100) and feed[C]["last_sale_status"] == "completed"
        assert feed[D]["volume_90d"] == "1"                                   # a flag on another item id leaves D's sale in
        code, out = run_audit(root, Path(tmp) / "latest" / "cards.csv")
        assert code == 0 and "PASS: 0 mismatching cells" in out, out


def test_audit_fails_a_feed_that_ignored_the_flag():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "store"
        make_store(root)
        feed = build(root, Path(tmp) / "latest", ignore_flags=True)
        assert feed[A]["volume_90d"] == "1" and feed[C]["clean_last_sale_date"] == day(2)
        code, out = run_audit(root, Path(tmp) / "latest" / "cards.csv")
        assert code == 1
        assert "--- volume_90d" in out and "--- volume_1y" in out and "--- clean_last_sale_date" in out, out


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} test(s) passed")
