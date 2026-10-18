#!/usr/bin/env python3
"""Checks for the held-back pivot (2026-10-18): last_sale_single / last_sale_status and the
PSA 9 pair, the last four columns of cards.csv.

    python3 history/test_single.py

Plain asserts, no pytest. The unit checks drive metrics.drop_outliers, single_flag and
sale_status directly; the last check runs metrics.build over a small synthetic store and
reads the columns back, so the header order and the PSA 9 blank rule are covered too."""
import contextlib
import csv
import io
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ingest   # noqa: E402
import metrics  # noqa: E402

D0 = date(2026, 1, 1)


def day(n):
    return D0 + timedelta(days=n)


def liquid(n=6, price=50000.0, start=0):
    """n accepted sales around `price` within a year, each on its own listing: the normal band."""
    return [(day(start + i * 20), price + i * 100, "eBay", f"https://www.ebay.com/itm/{1000 + i}") for i in range(n)]


def test_held_single_is_the_stale_or_thin_band_subset_of_held_high():
    # stale band: three sales two years ago, then one far-out high -> held, and a single
    old = [(day(-800 + i * 30), 500.0 + i, "eBay", f"s{i}") for i in range(3)]
    high = (day(0), 9000.0, "PWCC", "h")
    hh, hs = [], []
    keep, dropped = metrics.drop_outliers(old + [high], held_high=hh, held_single=hs)
    assert dropped == 1 and hh == [high[:3]] and hs == [high[:3]], (hh, hs)
    # thin band: one sale ever, then a 16x high -> held, and a single
    hh, hs = [], []
    metrics.drop_outliers([(day(-100), 500.0, "eBay", "a"), high], held_high=hh, held_single=hs)
    assert hh == [high[:3]] and hs == [high[:3]], (hh, hs)
    # normal band: six sales in the past year, then a far-out high -> held, NOT a single
    hh, hs = [], []
    big = (day(200), 400000.0, "PWCC", "h")
    metrics.drop_outliers(liquid() + [big], held_high=hh, held_single=hs)
    assert hh == [big[:3]] and hs == [], (hh, hs)
    # a held LOW is in neither list
    hh, hs = [], []
    metrics.drop_outliers(old + [(day(0), 20.0, "eBay", "l")], held_high=hh, held_single=hs)
    assert hh == [] and hs == []
    # a second distinct listing confirms the jump: accepted, nothing held
    hh, hs = [], []
    keep, dropped = metrics.drop_outliers(old + [high, (day(5), 9500.0, "Goldin", "h2")], held_high=hh, held_single=hs)
    assert dropped == 0 and hh == [] and hs == [] and keep[-1][1] == 9500.0, (keep, hh, hs)
    # the plain call is unchanged
    assert metrics.drop_outliers(old + [high]) == (keep[:3], 1) or metrics.drop_outliers(old + [high])[1] == 1


def test_single_flag():
    last = (day(-740), 502.0, "eBay")
    newest = (day(0), 9000.0, "PWCC", "AUCTION", "ok", "u")
    assert metrics.single_flag(newest, last, {(day(0), 9000.0)}) == 1
    assert metrics.single_flag(newest, last, set()) == 0                       # held, but not in the single set
    assert metrics.single_flag(last + ("AUCTION", "ok", "u"), last, set()) == ""   # newest is the clean newest: not held
    assert metrics.single_flag(None, last, set()) == "" and metrics.single_flag(newest, None, set()) == ""


def raw(sd, price, status="ok", src="eBay"):
    return (sd, price, src, "AUCTION", status, f"https://www.ebay.com/itm/{sd.isoformat()}-{price}")


def test_sale_status():
    ok = [raw(day(0), 100.0), raw(day(10), 110.0)]
    assert metrics.sale_status([]) == ""
    assert metrics.sale_status(ok) == "completed"
    assert metrics.sale_status(ok + [raw(day(12), 300.0, "PENDING")]) == "pending"
    assert metrics.sale_status(ok + [raw(day(12), 300.0, "PAYMENT_INITIATED")]) == "pending"
    assert metrics.sale_status(ok + [raw(day(12), 300.0, "NOT_PAID")]) == "unpaid"
    assert metrics.sale_status(ok + [raw(day(12), 300.0, "RELISTED")]) == "relisted"
    # an OLDER flagged sale changes nothing: the newest listed sale is the completed one
    assert metrics.sale_status(ok + [raw(day(5), 300.0, "RELISTED")]) == "completed"
    # same-day tie: the not-completed sale wins; pending before unpaid before relisted
    assert metrics.sale_status(ok + [raw(day(10), 90.0, "RELISTED")]) == "relisted"
    assert metrics.sale_status(ok + [raw(day(10), 90.0, "RELISTED"), raw(day(10), 95.0, "PENDING")]) == "pending"
    # only flagged sales ever: the status still says what happened, the price columns stay blank
    assert metrics.sale_status([raw(day(0), 100.0, "NOT_PAID")]) == "unpaid"
    # a status this feed does not know: unknown, blank
    assert metrics.sale_status([raw(day(0), 100.0, "SOMETHING_NEW")]) == ""
    assert metrics.sale_status([raw(day(0), 100.0, "SOMETHING_NEW"), raw(day(0), 100.0)]) == "completed"


# ---- end to end over a synthetic store ------------------------------------------------------
IDS = {k: f"00000000-0000-0000-0000-00000000000{k}" for k in "abcdefghij"}
TODAY = date(2026, 10, 1)


def _write(path, cols, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in cols} for r in rows)


def sale(aid, sd, price, grade="10.0", status="", src="eBay", url=None, n=[0]):
    n[0] += 1
    return {"asset_id": aid, "date": sd.isoformat(), "price": price, "grading_company": "PSA", "grade": grade, "source": src,
            "sale_type": "AUCTION", "url": url or f"https://www.ebay.com/itm/{n[0]}", "label": "", "subject_to_change": "False",
            "skipped_reason": status, "alt_tx_id": f"tx{n[0]}"}


def test_build_writes_the_four_columns_last():
    a, b, c, d_, e, f, g, h, i, j = (IDS[k] for k in "abcdefghij")
    y = TODAY - timedelta(days=750)
    sales = []
    sales += [sale(a, y + timedelta(days=k * 30), 500.0 + k) for k in range(3)] + [sale(a, TODAY - timedelta(days=3), 9000.0, src="PWCC")]   # stale single
    sales += [sale(b, y, 500.0), sale(b, TODAY - timedelta(days=2), 8000.0)]                                                            # thin single
    sales += [sale(c, TODAY - timedelta(days=200 - k * 20), 50000.0 + k * 100) for k in range(6)] + [sale(c, TODAY - timedelta(days=1), 400000.0)]   # liquid held high
    sales += [sale(d_, TODAY - timedelta(days=200 - k * 20), 50000.0 + k * 100) for k in range(6)] + [sale(d_, TODAY - timedelta(days=1), 100.0)]    # held low
    sales += [sale(e, TODAY - timedelta(days=200 - k * 20), 50000.0 + k * 100) for k in range(6)]                                           # clean newest
    sales += [sale(f, y + timedelta(days=k * 30), 500.0 + k) for k in range(3)] + [sale(f, TODAY - timedelta(days=9), 9000.0), sale(f, TODAY - timedelta(days=4), 9500.0, src="Goldin")]   # confirmed jump
    sales += [sale(g, TODAY - timedelta(days=200 - k * 20), 50000.0 + k * 100) for k in range(6)] + [sale(g, TODAY, 52000.0, status="PENDING")]      # newer pending sale
    sales += [sale(g, TODAY - timedelta(days=30), 400.0, grade="9.0"), sale(g, TODAY - timedelta(days=1), 450.0, grade="9.0", status="RELISTED")]    # PSA 9: newest relisted
    sales += [sale(h, TODAY - timedelta(days=10), 1000.0)]                                                                                  # PSA 9 not collected
    sales += [sale(i, TODAY - timedelta(days=10), 1000.0, status="NOT_PAID")]                                                               # only an unpaid sale
    sales += [sale(j, TODAY - timedelta(days=20), 1000.0), sale(j, TODAY - timedelta(days=1), 1010.0), sale(j, TODAY - timedelta(days=1), 990.0, status="RELISTED")]   # same-day tie
    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp) / "store"
        _write(store / "assets.csv", ["asset_id", "card_name", "year", "set", "card_number", "subject", "variety", "first_seen", "last_seen"],
               [{"asset_id": v, "card_name": f"card {k}", "year": "1999", "first_seen": "2026-09-01", "last_seen": TODAY.isoformat()} for k, v in IDS.items()])
        by_month = {}
        for s in sales:
            by_month.setdefault(s["date"][:7], []).append(s)
        for m, rows in by_month.items():
            _write(store / "sales" / f"{m}.csv", ingest.SALE_COLS, rows)
        daily = [{"asset_id": v, "pop_at_grade": "10", "pop_at_grade_9": "20", "num_sales": "1", "scraped_at": f"{TODAY}T10:00:00+00:00",
                  "extra_grades": "" if v == h else "9.0", "sales_fetched": "1"} for v in IDS.values()]
        _write(store / "daily" / f"{TODAY}.csv", ingest.DAILY_COLS, daily)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            summary = metrics.build(store, Path(tmp) / "latest")
        with open(Path(tmp) / "latest" / "cards.csv", newline="", encoding="utf-8") as fh:
            rd = csv.DictReader(fh)
            header = rd.fieldnames
            feed = {r["asset_id"]: r for r in rd}
    assert header[-4:] == metrics.SINGLE_COLS, header[-6:]
    col = lambda k, c: feed[IDS[k]][c]
    assert (col("a", "last_sale_unconfirmed"), col("a", "last_sale_single"), col("a", "last_sale_status")) == ("1", "1", "completed"), feed[a]
    assert (col("b", "last_sale_unconfirmed"), col("b", "last_sale_single")) == ("1", "1")
    assert (col("c", "last_sale_unconfirmed"), col("c", "last_sale_single")) == ("1", "0"), "liquid held high: held back, not single"
    assert (col("d", "last_sale_unconfirmed"), col("d", "last_sale_single")) == ("1", "0"), "held low: held back, not single"
    assert (col("e", "last_sale_unconfirmed"), col("e", "last_sale_single"), col("e", "last_sale_status")) == ("0", "", "completed")
    assert (col("f", "last_sale_unconfirmed"), col("f", "last_sale_single"), col("f", "clean_last_sale_price")) == ("0", "", "9500"), "second listing confirmed the jump"
    assert col("g", "last_sale_status") == "pending" and col("g", "last_sale_unconfirmed") == "0" and col("g", "clean_last_sale_price") == "50500", feed[g]
    assert col("g", "psa9_last_sale_status") == "relisted" and col("g", "psa9_last_sale_single") == "" and col("g", "psa9_last_sale_price") == "400", feed[g]
    assert col("h", "psa9_last_sale_status") == "" and col("h", "psa9_last_sale_single") == "" and col("h", "psa9_scraped_date") == "", "PSA 9 not collected: blank"
    assert col("h", "last_sale_status") == "completed"
    assert col("i", "last_sale_status") == "unpaid" and col("i", "clean_last_sale_price") == "" and col("i", "last_sale_single") == "", feed[i]
    assert col("j", "last_sale_status") == "relisted" and col("j", "clean_last_sale_price") == "1010", "same-day tie goes to the not-completed sale"
    assert summary["rows_single_last_sale"] == 2 and summary["rows_psa9_single_last_sale"] == 0, summary
    assert summary["last_sale_status_counts"] == {"completed": 7, "pending": 1, "unpaid": 1, "relisted": 1}, summary["last_sale_status_counts"]
    assert summary["psa9_last_sale_status_counts"] == {"completed": 0, "pending": 0, "unpaid": 0, "relisted": 1}, summary["psa9_last_sale_status_counts"]
    print("build ok:", {k: (col(k, "last_sale_single"), col(k, "last_sale_status")) for k in IDS})


if __name__ == "__main__":
    test_held_single_is_the_stale_or_thin_band_subset_of_held_high()
    test_single_flag()
    test_sale_status()
    test_build_writes_the_four_columns_last()
    print("all single/status checks passed")
