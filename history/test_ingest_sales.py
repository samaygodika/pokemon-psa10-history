#!/usr/bin/env python3
"""Checks for history/ingest.py merge_sales (2026-09-26): alt.xyz's transaction id is the
key, URL rules cover rows stored before the id existed, edits arrive, repeat sales on one
eBay listing are kept, twins alt.xyz no longer lists are dropped, untouched months are not
rewritten; and (2026-10-20) a sale a fully fetched card no longer lists at all is marked
REMOVED, comes back when listed again, is left alone for cards the run did not fetch, and (2026-10-21)
is never marked from an empty list, which lifts a mark there instead.

    python3 history/test_ingest_sales.py

Plain asserts, no pytest."""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ingest  # noqa: E402


def row(**kw):
    base = {"asset_id": "A", "date": "2026-09-10", "price": "100", "grading_company": "PSA", "grade": "10.0",
            "source": "eBay", "sale_type": "AUCTION", "url": "https://www.ebay.com/itm/1", "label": "",
            "subject_to_change": "False", "skipped_reason": "", "alt_tx_id": ""}
    base.update(kw)
    return base


def write(path, rows):
    ingest.write_csv_atomic(path, ingest.SALE_COLS, rows)


def by_month(rows):
    out = {}
    for r in rows:
        out.setdefault(r["date"][:7], []).append(r)
    return out


def rows_for(rows, url):
    return sorted(((r["date"], r["price"], r["skipped_reason"], r["alt_tx_id"]) for r in rows if r["url"] == url))


def test_merge_sales():
    with tempfile.TemporaryDirectory() as d:
        sd = Path(d)
        # The store as it was before ids existed (no alt_tx_id), plus two rows with ids.
        write(sd / "2026-09.csv", [
            row(url="u/ok-to-relisted"),                                             # single sale, will be flagged
            row(url="u/pending-to-ok", skipped_reason="PENDING", subject_to_change="True"),
            row(url="u/unchanged", price="55"),
            row(url="u/moves-to-august", date="2026-09-02"),                          # single sale, re-dated
            row(url="u/twin-grade", grade="9.0"),                                     # same URL under another grade
            row(url="u/multi", date="2026-09-05", price="100"),                       # repeat-sale listing
            row(url="u/redate-twin", date="2026-09-03", price="80"),                 # old copy of a re-dated sale
            row(url="u/redate-twin", date="2026-09-20", price="80"),                 # new copy of the same sale
            row(url="u/has-id", date="2026-09-08", price="10", alt_tx_id="id-8"),     # id row, gets re-priced
            row(url="u/has-id-gone", date="2026-09-09", price="10", alt_tx_id="id-gone"),  # id alt dropped
            row(url="u/not-in-run", date="2026-09-25", price="5"),                    # card not fetched this run
        ])
        write(sd / "2026-07.csv", [row(url="u/multi", date="2026-07-04", price="90")])   # repeat sale alt.xyz no longer lists
        write(sd / "2026-06.csv", [row(url="u/june-untouched", date="2026-06-04")])
        june_mtime = os.stat(sd / "2026-06.csv").st_mtime
        time.sleep(0.02)
        incoming = by_month([
            row(url="u/ok-to-relisted", skipped_reason="RELISTED", alt_tx_id="id-1"),
            row(url="u/pending-to-ok", skipped_reason="", subject_to_change="False", alt_tx_id="id-2"),
            row(url="u/unchanged", price="55"),                                        # no id, identical -> untouched
            row(url="u/moves-to-august", date="2026-08-30", alt_tx_id="id-3"),
            row(url="u/twin-grade", grade="10.0", alt_tx_id="id-4"),                   # other-grade twin: ignored
            row(url="u/multi", date="2026-09-05", price="100", skipped_reason="RELISTED", alt_tx_id="id-5a"),
            row(url="u/multi", date="2026-09-12", price="100", alt_tx_id="id-5b"),
            row(url="u/multi", date="2026-08-20", price="95", alt_tx_id="id-5c"),
            row(url="u/redate-twin", date="2026-09-20", price="80", alt_tx_id="id-6"),  # only the new date now
            row(url="u/has-id", date="2026-09-08", price="12", alt_tx_id="id-8"),       # same id, new price
            row(url="u/has-id-gone", date="2026-09-09", price="10", alt_tx_id="id-new"), # alt replaced the record
            row(url="u/brand-new", date="2026-09-20", price="70", alt_tx_id="id-7"),
            row(url="u/brand-new", date="2026-09-20", price="70", alt_tx_id="id-7"),    # listed twice: one sale
        ])
        stats = ingest.merge_sales(sd, incoming)
        sep = ingest.read_csv(sd / "2026-09.csv")
        aug = ingest.read_csv(sd / "2026-08.csv")
        jul = ingest.read_csv(sd / "2026-07.csv")
        assert rows_for(sep, "u/ok-to-relisted") == [("2026-09-10", "100", "RELISTED", "id-1")]
        assert rows_for(sep, "u/pending-to-ok") == [("2026-09-10", "100", "", "id-2")]
        assert rows_for(sep, "u/unchanged") == [("2026-09-10", "55", "", "")]
        assert rows_for(sep, "u/moves-to-august") == [] and rows_for(aug, "u/moves-to-august") == [("2026-08-30", "100", "", "id-3")]
        assert rows_for(sep, "u/twin-grade") == [("2026-09-10", "100", "", "")], "same URL under another grade is left alone"
        assert rows_for(sep, "u/multi") == [("2026-09-05", "100", "RELISTED", "id-5a"), ("2026-09-12", "100", "", "id-5b")]
        assert rows_for(aug, "u/multi") == [("2026-08-20", "95", "", "id-5c")]
        assert rows_for(jul, "u/multi") == [], "a repeat sale alt.xyz no longer lists under a URL it does list is dropped"
        assert rows_for(sep, "u/redate-twin") == [("2026-09-20", "80", "", "id-6")], "the stale copy of a re-dated sale is dropped"
        assert rows_for(sep, "u/has-id") == [("2026-09-08", "12", "", "id-8")], "matched by id despite the new price"
        assert rows_for(sep, "u/has-id-gone") == [("2026-09-09", "10", "", "id-new")], "old record dropped, new one added"
        assert rows_for(sep, "u/not-in-run") == [("2026-09-25", "5", "", "")]
        assert rows_for(sep, "u/brand-new") == [("2026-09-20", "70", "", "id-7")]
        assert os.stat(sd / "2026-06.csv").st_mtime == june_mtime, "untouched month not rewritten"
        assert stats == {"sales_added": 5, "sales_updated": 5, "sales_moved": 1, "sales_dropped": 3,
                         "sales_removed": 0, "sales_unmarked": 0, "sales_month_files_rewritten": 3}, stats
        # A second identical run changes nothing and rewrites nothing.
        sep_m = os.stat(sd / "2026-09.csv").st_mtime
        time.sleep(0.02)
        stats2 = ingest.merge_sales(sd, incoming)
        assert stats2 == {"sales_added": 0, "sales_updated": 0, "sales_moved": 0, "sales_dropped": 0,
                          "sales_removed": 0, "sales_unmarked": 0, "sales_month_files_rewritten": 0}, stats2
        assert os.stat(sd / "2026-09.csv").st_mtime == sep_m
        # With allow_drop=False (a run cut short by --max-sales) the same store keeps every unmatched row.
        write(sd / "2026-07.csv", [row(url="u/multi", date="2026-07-04", price="90")])
        st3 = ingest.merge_sales(sd, incoming, allow_drop=False)
        assert st3["sales_dropped"] == 0 and rows_for(ingest.read_csv(sd / "2026-07.csv"), "u/multi") == [("2026-07-04", "90", "", "")], st3
        print("merge_sales ok:", stats)


def test_removed():
    """fetched = the (asset, company, grade) lists the run holds in full: their stored sales the run no
    longer lists anywhere become REMOVED; everyone else's stay; a later run that lists the sale again
    restores alt.xyz's version; marking is idempotent and only rewrites months with a change."""
    F = {("A", "PSA", "10.0"), ("B", "PSA", "10.0")}
    with tempfile.TemporaryDirectory() as d:
        sd = Path(d)
        write(sd / "2026-09.csv", [
            row(url="u/a-still", alt_tx_id="id-a1"),                                     # A: listed again, unchanged
            row(url="u/a-gone", date="2026-09-20", price="9852", alt_tx_id="id-a2"),      # A: gone from the run -> REMOVED
            row(url="u/a-other-grade", date="2026-09-21", price="300", alt_tx_id="id-a3"), # A@10: run lists the URL at PSA 9 only -> REMOVED
            row(asset_id="A", grade="9.0", url="u/a9-gone", date="2026-09-22", alt_tx_id="id-a4"),   # A@9 not fetched: stays
            row(asset_id="C", url="u/c-gone", date="2026-09-23", alt_tx_id="id-c1"),      # C not fetched: stays
            row(asset_id="B", url="u/b-gone", date="2026-09-24", skipped_reason="REMOVED", alt_tx_id="id-b1"),   # already marked: no rewrite
        ])
        write(sd / "2026-08.csv", [row(asset_id="B", url="u/b-aug", date="2026-08-10", alt_tx_id="id-b2")])        # B: gone -> REMOVED, August rewritten
        write(sd / "2026-07.csv", [row(asset_id="C", url="u/c-jul", date="2026-07-10", alt_tx_id="id-c2")])        # untouched
        july_m = os.stat(sd / "2026-07.csv").st_mtime
        time.sleep(0.02)
        incoming = by_month([
            row(url="u/a-still", alt_tx_id="id-a1"),
            row(asset_id="A", grade="9.0", url="u/a-other-grade", date="2026-09-21", price="300", alt_tx_id="id-a3x"),
        ])
        stats = ingest.merge_sales(sd, incoming, fetched=F)
        sep = ingest.read_csv(sd / "2026-09.csv")
        assert rows_for(sep, "u/a-still") == [("2026-09-10", "100", "", "id-a1")]
        assert rows_for(sep, "u/a-gone") == [("2026-09-20", "9852", "REMOVED", "id-a2")], rows_for(sep, "u/a-gone")
        assert rows_for(sep, "u/a-other-grade") == [("2026-09-21", "300", "REMOVED", "id-a3")], "twin at PSA 9 only: the PSA 10 row is no longer listed"
        assert rows_for(sep, "u/a9-gone") == [("2026-09-22", "100", "", "id-a4")], "PSA 9 list of A not fetched: untouched"
        assert rows_for(sep, "u/c-gone") == [("2026-09-23", "100", "", "id-c1")], "card C not fetched: untouched"
        assert rows_for(ingest.read_csv(sd / "2026-08.csv"), "u/b-aug") == [("2026-08-10", "100", "REMOVED", "id-b2")]
        assert os.stat(sd / "2026-07.csv").st_mtime == july_m
        assert stats == {"sales_added": 0, "sales_updated": 0, "sales_moved": 0, "sales_dropped": 0,
                         "sales_removed": 3, "sales_unmarked": 0, "sales_month_files_rewritten": 2}, stats
        # the same run again: nothing to mark, nothing rewritten
        sep_m = os.stat(sd / "2026-09.csv").st_mtime
        time.sleep(0.02)
        assert ingest.merge_sales(sd, incoming, fetched=F)["sales_month_files_rewritten"] == 0
        assert os.stat(sd / "2026-09.csv").st_mtime == sep_m
        # alt.xyz lists the sale again: the run's (settled) version replaces the REMOVED row
        back = by_month(incoming["2026-09"] + [row(url="u/a-gone", date="2026-09-20", price="9852", alt_tx_id="id-a2")])
        st = ingest.merge_sales(sd, back, fetched=F)
        assert rows_for(ingest.read_csv(sd / "2026-09.csv"), "u/a-gone") == [("2026-09-20", "9852", "", "id-a2")], "restored"
        assert st["sales_updated"] == 1 and st["sales_removed"] == 0, st
        # no fetched set (a --max-sales run, or an old caller): nothing is ever marked
        write(sd / "2026-06.csv", [row(url="u/a-june", date="2026-06-01", alt_tx_id="id-a6")])
        assert ingest.merge_sales(sd, incoming)["sales_removed"] == 0
        assert rows_for(ingest.read_csv(sd / "2026-06.csv"), "u/a-june") == [("2026-06-01", "100", "", "id-a6")]
        print("removed ok:", stats)


def test_ingest_fetched_set():
    """ingest() derives the fetched set from the run's cards.csv: sales_fetched 0 (carried) and the
    grades the run did not ask are out; a grade the run vouches for but lists no sale at marks nothing
    and lifts a REMOVED mark there instead (2026-10-21: the scraper vouches for a known-zero PSA 9
    without fetching it, and an empty answer is not a removal); a --max-sales run marks nothing."""
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as d:
        store = Path(d) / "store"
        (store / "sales").mkdir(parents=True)
        (store / "daily").mkdir()
        ingest.write_csv_atomic(store / "assets.csv", ingest.ASSET_COLS, [])
        write(store / "sales" / "2026-09.csv", [
            row(asset_id="A", url="u/a", alt_tx_id="id-a"),                                  # A fetched, run lists another PSA 10 sale -> REMOVED
            row(asset_id="A", grade="9.0", url="u/a9", alt_tx_id="id-a9"),                  # A's PSA 9 also fetched and listed -> REMOVED
            row(asset_id="B", url="u/b", alt_tx_id="id-b"),                                  # B carried -> stays
            row(asset_id="C", url="u/c", alt_tx_id="id-c"),                                  # C fetched, empty answer -> stays
            row(asset_id="C", url="u/c-old", date="2026-09-02", skipped_reason="REMOVED", alt_tx_id="id-c0"),   # ... and a mark there is lifted
            row(asset_id="C", grade="9.0", url="u/c9", alt_tx_id="id-c9"),                  # C's PSA 9 not asked -> stays
            row(asset_id="D", url="u/d", skipped_reason="REMOVED", alt_tx_id="id-d"),       # D not in the run: its mark stays
            row(asset_id="E", grade="9.0", url="u/e9", date="2026-09-03", alt_tx_id="id-e9"),   # E: PSA 9 known zero (in extra_grades, never fetched) -> stays
            row(asset_id="E", grade="9.0", url="u/e9-old", date="2026-09-04", skipped_reason="REMOVED", alt_tx_id="id-e9o"),  # ... and its mark is lifted
        ])
        run = Path(d) / "run"
        run.mkdir()
        cols = ["asset_id", "grading_company", "grade", "num_sales", "scraped_at", "extra_grades", "sales_fetched"] + \
               [c for c in ingest.IDENTITY_COLS + ingest.DAILY_COLS if c not in ("asset_id",)]
        cols = list(dict.fromkeys(cols))
        cards = [{"asset_id": "A", "grading_company": "PSA", "grade": "10.0", "num_sales": "1", "scraped_at": "2026-10-20T01:00:00+00:00", "extra_grades": "9.0", "sales_fetched": "1"},
                 {"asset_id": "B", "grading_company": "PSA", "grade": "10.0", "num_sales": "1", "scraped_at": "2026-10-20T01:00:00+00:00", "extra_grades": "", "sales_fetched": "0"},
                 {"asset_id": "C", "grading_company": "PSA", "grade": "10.0", "num_sales": "0", "scraped_at": "2026-10-20T01:00:00+00:00", "extra_grades": "", "sales_fetched": "1"},
                 {"asset_id": "E", "grading_company": "PSA", "grade": "10.0", "num_sales": "1", "scraped_at": "2026-10-20T01:00:00+00:00", "extra_grades": "9.0", "sales_fetched": "1"}]
        ingest.write_csv_atomic(run / "cards.csv", cols, cards)
        ingest.write_csv_atomic(run / "sales.csv", ingest.SALE_COLS, [
            row(asset_id="A", url="u/a-new", date="2026-09-15", alt_tx_id="id-a-new"),
            row(asset_id="A", grade="9.0", url="u/a9-new", date="2026-09-16", alt_tx_id="id-a9-new"),
            row(asset_id="E", url="u/e", date="2026-09-17", alt_tx_id="id-e"),
        ])
        ingest.write_csv_atomic(run / "listings.csv", ingest.LIVE_COLS, [])
        with contextlib.redirect_stdout(io.StringIO()):
            st = ingest.ingest(run, "2026-10-20", store)
        got = {(r["asset_id"], r["grade"], r["url"]): r["skipped_reason"] for r in ingest.read_csv(store / "sales" / "2026-09.csv")}
        want = {("A", "10.0", "u/a"): "REMOVED", ("A", "9.0", "u/a9"): "REMOVED", ("A", "10.0", "u/a-new"): "", ("A", "9.0", "u/a9-new"): "",
                ("B", "10.0", "u/b"): "", ("C", "10.0", "u/c"): "", ("C", "10.0", "u/c-old"): "", ("C", "9.0", "u/c9"): "",
                ("D", "10.0", "u/d"): "REMOVED", ("E", "9.0", "u/e9"): "", ("E", "9.0", "u/e9-old"): "", ("E", "10.0", "u/e"): ""}
        assert got == want, {k: (got.get(k), want.get(k)) for k in set(got) | set(want) if got.get(k) != want.get(k)}
        assert st["sales_removed"] == 2 and st["sales_unmarked"] == 2, st
        # a run cut short by --max-sales (fewer rows than num_sales for some card) marks and lifts nothing
        cards[2]["num_sales"] = "5"
        ingest.write_csv_atomic(run / "cards.csv", cols, cards)
        write(store / "sales" / "2026-09.csv", [row(asset_id="A", url="u/a", alt_tx_id="id-a"),
                                                row(asset_id="C", url="u/c-old", skipped_reason="REMOVED", alt_tx_id="id-c0")])
        with contextlib.redirect_stdout(io.StringIO()):
            st = ingest.ingest(run, "2026-10-20", store)
        sep = {r["url"]: r["skipped_reason"] for r in ingest.read_csv(store / "sales" / "2026-09.csv")}
        assert st["sales_removed"] == 0 and st["sales_unmarked"] == 0 and sep["u/a"] == "" and sep["u/c-old"] == "REMOVED", (st, sep)
        print("ingest fetched set ok")

if __name__ == "__main__":
    test_merge_sales()
    test_removed()
    test_ingest_fetched_set()
    print("all sales-merge checks passed")
