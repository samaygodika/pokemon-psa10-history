#!/usr/bin/env python3
"""Checks for history/ingest.py merge_sales: known sales take the run's values, re-dated
sales move month, mislabeled same-URL twins are left alone, untouched months are not
rewritten (2026-09-26).

    python3 history/test_ingest_sales.py

Plain asserts, no pytest."""
import csv
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
            "subject_to_change": "False", "skipped_reason": ""}
    base.update(kw)
    return base


def write(path, rows):
    ingest.write_csv_atomic(path, ingest.SALE_COLS, rows)


def read(path):
    return {r["url"]: r for r in ingest.read_csv(path)} if path.exists() else {}


def by_month(rows):
    out = {}
    for r in rows:
        out.setdefault(r["date"][:7], []).append(r)
    return out


def test_merge_sales():
    with tempfile.TemporaryDirectory() as d:
        sd = Path(d)
        write(sd / "2026-09.csv", [
            row(url="u/ok-to-relisted"),
            row(url="u/pending-to-ok", skipped_reason="PENDING", subject_to_change="True"),
            row(url="u/unchanged", price="55"),
            row(url="u/moves-to-august", date="2026-09-02"),
            row(url="u/twin", grade="9.0"),
        ])
        write(sd / "2026-07.csv", [row(url="u/july-untouched", date="2026-07-04")])
        july_mtime = os.stat(sd / "2026-07.csv").st_mtime
        time.sleep(0.02)
        incoming = by_month([
            row(url="u/ok-to-relisted", skipped_reason="RELISTED"),                 # flagged later
            row(url="u/pending-to-ok", skipped_reason="", subject_to_change="False"),  # settled
            row(url="u/unchanged", price="55"),                                      # identical
            row(url="u/moves-to-august", date="2026-08-30"),                          # re-dated across months
            row(url="u/twin", grade="10.0"),                                          # same URL, other grade
            row(url="u/brand-new", date="2026-09-20", price="70"),                    # new
            row(url="u/brand-new", date="2026-09-20", price="70"),                    # same URL, date, price twice: one sale
            # a multi-quantity eBay listing: one URL, many sales. Stored: 09-05 @100 and 07-04
            # (July) @90. Run: 09-05 @100 now RELISTED, a new 09-12 @100, and 08-20 @95.
            row(url="u/multi", date="2026-09-05", price="100", skipped_reason="RELISTED"),
            row(url="u/multi", date="2026-09-12", price="100"),
            row(url="u/multi", date="2026-08-20", price="95"),
        ])
        write(sd / "2026-09.csv", ingest.read_csv(sd / "2026-09.csv") + [row(url="u/multi", date="2026-09-05", price="100")])
        write(sd / "2026-07.csv", ingest.read_csv(sd / "2026-07.csv") + [row(url="u/multi", date="2026-07-04", price="90")])
        july_mtime = os.stat(sd / "2026-07.csv").st_mtime
        time.sleep(0.02)
        stats = ingest.merge_sales(sd, incoming)
        sep = ingest.read_csv(sd / "2026-09.csv")
        sep1 = {r["url"]: r for r in sep}
        aug = ingest.read_csv(sd / "2026-08.csv")
        jul = ingest.read_csv(sd / "2026-07.csv")
        assert sep1["u/ok-to-relisted"]["skipped_reason"] == "RELISTED"
        assert sep1["u/pending-to-ok"]["skipped_reason"] == "" and sep1["u/pending-to-ok"]["subject_to_change"] == "False"
        assert sep1["u/unchanged"]["price"] == "55"
        assert "u/moves-to-august" not in sep1 and [r for r in aug if r["url"] == "u/moves-to-august"][0]["date"] == "2026-08-30", "re-dated single sale moved month"
        assert sep1["u/twin"]["grade"] == "9.0", "same URL under another grade is left alone"
        assert sum(1 for r in sep if r["url"] == "u/brand-new") == 1
        multi_sep = sorted((r["date"], r["price"], r["skipped_reason"]) for r in sep if r["url"] == "u/multi")
        assert multi_sep == [("2026-09-05", "100", "RELISTED"), ("2026-09-12", "100", "")], multi_sep
        assert [r for r in aug if r["url"] == "u/multi"][0]["price"] == "95"
        assert [r for r in jul if r["url"] == "u/multi"][0]["date"] == "2026-07-04", "an old repeat sale the run no longer lists stays"
        assert os.stat(sd / "2026-07.csv").st_mtime == july_mtime, "untouched month not rewritten"
        assert len(sep) == 7 and len(aug) == 2 and len(jul) == 2
        assert stats == {"sales_added": 4, "sales_updated": 3, "sales_moved": 1, "sales_month_files_rewritten": 2}, stats
        # A second identical run changes nothing and rewrites nothing.
        sep_m = os.stat(sd / "2026-09.csv").st_mtime
        time.sleep(0.02)
        stats2 = ingest.merge_sales(sd, incoming)
        assert stats2 == {"sales_added": 0, "sales_updated": 0, "sales_moved": 0, "sales_month_files_rewritten": 0}, stats2
        assert os.stat(sd / "2026-09.csv").st_mtime == sep_m
        print("merge_sales ok:", stats)


if __name__ == "__main__":
    test_merge_sales()
    print("all sales-merge checks passed")
