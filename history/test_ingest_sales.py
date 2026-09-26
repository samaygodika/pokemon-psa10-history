#!/usr/bin/env python3
"""Checks for history/ingest.py merge_sales (2026-09-26): alt.xyz's transaction id is the
key, URL rules cover rows stored before the id existed, edits arrive, repeat sales on one
eBay listing are kept, twins alt.xyz no longer lists are dropped, untouched months are not
rewritten.

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
                         "sales_month_files_rewritten": 3}, stats
        # A second identical run changes nothing and rewrites nothing.
        sep_m = os.stat(sd / "2026-09.csv").st_mtime
        time.sleep(0.02)
        stats2 = ingest.merge_sales(sd, incoming)
        assert stats2 == {"sales_added": 0, "sales_updated": 0, "sales_moved": 0, "sales_dropped": 0,
                          "sales_month_files_rewritten": 0}, stats2
        assert os.stat(sd / "2026-09.csv").st_mtime == sep_m
        # With allow_drop=False (a run cut short by --max-sales) the same store keeps every unmatched row.
        write(sd / "2026-07.csv", [row(url="u/multi", date="2026-07-04", price="90")])
        st3 = ingest.merge_sales(sd, incoming, allow_drop=False)
        assert st3["sales_dropped"] == 0 and rows_for(ingest.read_csv(sd / "2026-07.csv"), "u/multi") == [("2026-07-04", "90", "", "")], st3
        print("merge_sales ok:", stats)


if __name__ == "__main__":
    test_merge_sales()
    print("all sales-merge checks passed")
