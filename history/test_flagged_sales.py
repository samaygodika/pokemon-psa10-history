#!/usr/bin/env python3
"""Checks for history/flagged_sales.py (2026-10-28): Sid's match rule, which rows of his file are
kept and which columns, where the file is read from, that a failed fetch keeps the flags on record,
and that metrics.py, all_grades.py and buy_features.py leave a flagged sale out.

    python3 history/test_flagged_sales.py

Plain asserts, no pytest. urlopen is faked: nothing here talks to GitHub."""
import csv
import io
import sys
import tempfile
import urllib.error
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "history"))
import all_grades          # noqa: E402
import buy_features        # noqa: E402
import flagged_sales as fs  # noqa: E402
import ingest              # noqa: E402
import metrics             # noqa: E402

SID_HEADER = "asset_id,grade,date,price,url,reason,note,flagged_at\n"
URL = "https://api.github.com/repos/NovaCast/pokesniper-research-backup/contents/watchlist.csv"


def sale(aid, day, price, url, grade="10.0", reason=""):
    return {"asset_id": aid, "date": day, "price": str(price), "grading_company": "PSA", "grade": grade, "source": "eBay",
            "sale_type": "AUCTION", "url": url, "label": "", "subject_to_change": "", "skipped_reason": reason, "alt_tx_id": ""}


def store_with(sales, flags_csv):
    td = tempfile.TemporaryDirectory()
    store = Path(td.name)
    (store / "sales").mkdir()
    ingest.write_csv_atomic(store / "sales" / "2026-10.csv", ingest.SALE_COLS, sales)
    if flags_csv is not None:
        fs.fetch(store, ("file", str(write_tmp(store, flags_csv)), None))
    return td, store


def write_tmp(store, text):
    p = store / "sid_flagged_sales.csv"
    p.write_text(text)
    return p


def test_grade_and_cents():
    assert fs.grade_key("10") == fs.grade_key("10.0") == "10"
    assert fs.grade_key("8.5") == "8.5" and fs.grade_key("") is None and fs.grade_key("PSA 10") is None
    assert fs.cents("1830") == fs.cents(1830.004) == 183000


def test_clean_rows_keeps_match_columns_only():
    rows = list(csv.DictReader(io.StringIO(
        SID_HEADER
        + 'a1,10,2026-10-20,1830,https://ebay.com/itm/1,shill or unpaid,"seller, again",2026-10-28T21:00:00Z\n'
        + "a1,10,2026-10-20,1830.00,https://ebay.com/itm/1,lot,dup,2026-10-28T22:00:00Z\n"   # same flag twice: one row
        + "a2,9,2026-10-21T00:00:00Z,99.999,,wrong card,,\n"
        + ",10,2026-10-20,5,,other,,\n"          # no asset
        + "a3,PSA,2026-10-20,5,,other,,\n"       # no grade
        + "a3,10,20/10/2026,5,,other,,\n"        # no date
        + "a3,10,2026-10-20,0,,other,,\n")))     # no price
    out, bad = fs.clean_rows(rows)
    assert bad == 4
    assert out == [{"asset_id": "a1", "grade": "10", "date": "2026-10-20", "price": "1830.00", "url": "https://ebay.com/itm/1",
                    "flagged_at": "2026-10-28T22:00:00Z"},
                   {"asset_id": "a2", "grade": "9", "date": "2026-10-21", "price": "100.00", "url": "", "flagged_at": ""}]


def test_match_is_sids_rule():
    with tempfile.TemporaryDirectory() as td:
        store = Path(td)
        fs.write(store, [{"asset_id": "a1", "grade": "10", "date": "2026-10-20", "price": "1830.00", "url": "https://ebay.com/itm/1", "flagged_at": ""},
                         {"asset_id": "a1", "grade": "9", "date": "2026-10-20", "price": "50.00", "url": "", "flagged_at": ""}])
        ix = fs.load(store)
    assert fs.n_flags(ix) == 2
    assert fs.match(ix, "a1", "10.0", "2026-10-20T18:00:00Z", 1830.0, "https://ebay.com/itm/1")
    assert fs.match(ix, "a1", "10.0", date(2026, 10, 20), 1830.001, "")                  # our side has no URL: matches
    assert not fs.match(ix, "a1", "10.0", "2026-10-20", 1830.0, "https://ebay.com/itm/2")  # both have one, different
    assert not fs.match(ix, "a1", "10.0", "2026-10-21", 1830.0, "https://ebay.com/itm/1")  # another day
    assert not fs.match(ix, "a1", "10.0", "2026-10-20", 1831.0, "https://ebay.com/itm/1")  # another price
    assert not fs.match(ix, "a1", "9.0", "2026-10-20", 1830.0, "https://ebay.com/itm/1")   # another grade
    assert fs.match(ix, "a1", "9.0", "2026-10-20", 50, "https://anything")                # the flag has no URL: matches
    assert not fs.match(ix, "a2", "10.0", "2026-10-20", 1830.0, "")


def test_source():
    assert fs.sibling_url(URL) == URL.replace("watchlist.csv", "flagged_sales.csv")
    assert fs.sibling_url(URL + "?ref=main") == URL.replace("watchlist.csv", "flagged_sales.csv") + "?ref=main"
    assert fs.sibling_url("https://server/api/watch") is None
    assert fs.source(env={}) is None
    assert fs.source(env={"WATCHLIST_URL": URL, "WATCHLIST_TOKEN": "t"}) == ("url", URL.replace("watchlist.csv", "flagged_sales.csv"), "t")
    assert fs.source(env={"WATCHLIST_URL": URL, "WATCHLIST_TOKEN": "t", "FLAGGED_SALES_URL": "https://x/f.csv", "FLAGGED_SALES_TOKEN": "u"}) == ("url", "https://x/f.csv", "u")
    assert fs.source(env={"FLAGGED_SALES_FILE": "f.csv", "WATCHLIST_URL": URL}) == ("file", "f.csv", None)
    assert fs.source("g.csv", env={"FLAGGED_SALES_FILE": "f.csv"}) == ("file", "g.csv", None)


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_counts_and_keeps_on_failure():
    one = SID_HEADER + "a1,10,2026-10-20,1830,https://ebay.com/itm/1,lot,secret note,2026-10-28T21:00:00Z\n"
    two = one + "a2,10,2026-10-22,400,,wrong card,,2026-10-28T23:00:00Z\n"
    seen = {}

    def ok(text):
        def urlopen(req, timeout=None):
            seen["auth"], seen["accept"] = req.get_header("Authorization"), req.get_header("Accept")
            return Resp(text.encode())
        return urlopen

    def http(code):
        def urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
        return urlopen

    with tempfile.TemporaryDirectory() as td:
        store, src = Path(td), ("url", URL.replace("watchlist.csv", "flagged_sales.csv"), "tok")
        r = fs.fetch(store, src, ok(one))
        assert r["ok"] and (r["rows"], r["added"], r["removed"], r["bad"]) == (1, 1, 0, 0), r
        assert seen == {"auth": "Bearer tok", "accept": "application/vnd.github.raw+json"}
        text = (store / fs.FILE).read_text()
        assert text.splitlines()[0] == ",".join(fs.COLS) and "secret note" not in text and "lot" not in text
        r = fs.fetch(store, src, ok(two))
        assert (r["rows"], r["added"], r["removed"]) == (2, 1, 0) and r["note"] == "flagged sales 2 (1 new, 0 unflagged)"
        for bad_fetch in (http(404), http(500), ok("<html>rate limited</html>\n"), ok("")):
            r = fs.fetch(store, src, bad_fetch)
            assert not r["ok"] and r["rows"] == 2 and "the 2 on record kept" in r["note"], r
            assert len(fs.read_file(store)) == 2
        assert "HTTP 404" in fs.fetch(store, src, http(404))["note"]
        assert not fs.fetch(store, None)["ok"] and len(fs.read_file(store)) == 2
        r = fs.fetch(store, src, ok(SID_HEADER))   # every flag lifted in the app
        assert r["ok"] and (r["rows"], r["removed"]) == (0, 2) and fs.load(store) == {}


def test_metrics_marks_flagged_and_skips_it():
    sales = [sale("a1", "2026-10-01", 1000, "https://ebay.com/itm/0"),
             sale("a1", "2026-10-10", 1100, "https://ebay.com/itm/1"),
             sale("a1", "2026-10-20", 5000, "https://ebay.com/itm/2"),   # the flagged one, newest
             sale("a2", "2026-10-20", 5000, "https://ebay.com/itm/3")]   # same day and price, another card
    td, store = store_with(sales, SID_HEADER + "a1,10,2026-10-20,5000,https://ebay.com/itm/2,shill or unpaid,,\n")
    with td:
        by_asset, n, _, raw, _, _ = metrics.load_sales(store)["10.0"]
        assert [t[1] for t in by_asset["a1"]] == [1000, 1100] and n == 3
        assert [t[4] for t in sorted(raw["a1"])] == ["ok", "ok", "FLAGGED"]
        assert metrics.newest_listed(raw["a1"]) == ("completed", None)   # looks past it, like REMOVED
        assert [t[1] for t in by_asset["a2"]] == [5000]
        recent = Path(store, "recent")
        metrics.write_recent(recent, raw, by_asset)
        rows = list(csv.DictReader(open(recent / "a1.csv")))
        assert [(r["date"], r["status"], r["outlier"]) for r in rows if r["asset_id"] == "a1"][0] == ("2026-10-20", "FLAGGED", "0")


def test_all_grades_and_buy_features_skip_it():
    sales = [sale("a1", f"2026-10-0{i}", 100 + i, f"https://ebay.com/itm/{i}") for i in range(1, 6)]
    sales += [sale("a1", "2026-10-03", 300, "https://ebay.com/itm/9", grade="8.5")]
    flags = SID_HEADER + "a1,10,2026-10-05,105,https://ebay.com/itm/5,lot,,\na1,8.5,2026-10-03,300,,wrong grade,,\n"
    td, store = store_with(sales, flags)
    with td:
        g = all_grades.load_sales(store, {"a1"})
        assert [t[1] for t in g["10.0"][0]["a1"]] == [101, 102, 103, 104]
        assert "a1" not in g["8.5"][0] and [t[4] for t in g["8.5"][3]["a1"]] == ["FLAGGED"]
        bf = buy_features.load_sales(store)[10.0]["a1"]
        assert bf[1] == [101, 102, 103, 104]
    td, store = store_with(sales, None)   # no file: nothing changes
    with td:
        assert buy_features.load_sales(store)[10.0]["a1"][1] == [101, 102, 103, 104, 105]
        assert [t[1] for t in metrics.load_sales(store)["10.0"][0]["a1"]] == [101, 102, 103, 104, 105]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} test(s) passed")
