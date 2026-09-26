#!/usr/bin/env python3
"""Checks for the scraper's --skip-unchanged-sales and the sales_fetched column (2026-09-26).

    python3 history/test_skip_unchanged.py

Plain asserts, no pytest (it isn't installed here). The network functions are
monkeypatched: nothing here talks to alt.xyz."""
import contextlib
import csv
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "history"))
import alt_scraper                                # noqa: E402
import ingest                                     # noqa: E402
import metrics                                    # noqa: E402

# Seven cards, one per situation. The ids must look like UUIDs for extract_id.
A = "00000000-0000-0000-0000-00000000000a"   # index count present and unchanged      -> carried, nothing fetched
B = "00000000-0000-0000-0000-00000000000b"   # index count changed                     -> fetched
C = "00000000-0000-0000-0000-00000000000c"   # dormant (pop10 = 0, no sales), has PSA 9s -> PSA 10 carried, PSA 9 fetched
D = "00000000-0000-0000-0000-00000000000d"   # dormant, pop table has no PSA rows        -> carried, no sale request at all
E = "00000000-0000-0000-0000-00000000000e"   # not in the previous daily                -> fetched
F = "00000000-0000-0000-0000-00000000000f"   # pop10 = 0 but sales on record             -> fetched (not dormant)
G = "00000000-0000-0000-0000-000000000010"   # index count blank on both sides, active   -> fetched (blank is unknown)
IDS = [A, B, C, D, E, F, G]


def psa(grade, count):
    return {"gradingCompany": "PSA", "gradeNumber": grade, "count": count}


POPS = {
    A: [psa("10.0", 5), psa("9.0", 3)],
    B: [psa("10.0", 5), psa("9.0", 3)],
    C: [psa("10.0", 0), psa("9.0", 4)],
    D: [],                                    # no PSA rows at all: pops unknown
    E: [psa("10.0", 2), psa("9.0", 0)],
    F: [psa("10.0", 0), psa("9.0", 0)],
    G: [psa("10.0", 9), psa("9.0", 1)],
}


def tx(n, grade, price, date, skipped=None):
    return {"id": f"tx{n}", "date": date, "auctionHouse": "eBay", "auctionType": "AUCTION", "price": price,
            "attributes": {"gradeNumber": grade, "gradingCompany": "PSA", "url": f"https://www.ebay.com/itm/{n}", "autograph": None},
            "subjectToChange": False, "consolidatedSkippedReason": skipped, "label": None}


SALES = {
    (A, "10.0"): [tx(1, "10.0", "999", "2026-09-25")],           # must never be asked for
    (A, "9.0"): [tx(2, "9.0", "500", "2026-09-25")],
    (B, "10.0"): [tx(3, "10.0", "80", "2026-09-24"), tx(4, "10.0", "70", "2026-09-20"), tx(5, "10.0", "1", "2026-09-21", "RELISTED")],
    (B, "9.0"): [tx(6, "9.0", "40", "2026-09-22")],
    (C, "10.0"): [tx(7, "10.0", "123", "2026-09-25")],           # must never be asked for
    (C, "9.0"): [tx(8, "9.0", "30", "2026-09-24")],
    (D, "10.0"): [tx(9, "10.0", "5", "2026-09-25")],             # must never be asked for
    (D, "9.0"): [tx(10, "9.0", "4", "2026-09-25")],              # must never be asked for
    (E, "10.0"): [tx(11, "10.0", "200", "2026-09-23")],
    (F, "10.0"): [tx(12, "10.0", "15", "2026-09-19")],
    (G, "10.0"): [tx(13, "10.0", "60", "2026-09-25"), tx(14, "10.0", "55", "2026-09-01")],
    (G, "9.0"): [tx(15, "9.0", "20", "2026-09-25")],
}

DOCS = {i: {"id": i, "name": f"card {i[-1]}", "year": 1999, "subject": "Pikachu", "category": "POKEMON_CARDS",
            "brand": "Base Set", "variety": "", "cardNumber": i[-1], "pop": 100,
            "externalTransactionCount": {A: 10, B: 11}.get(i)} for i in IDS}

PREV_SUMMARY = {"num_sales": "7", "last_sale_price": "123.0", "last_sale_date": "2026-09-01", "last_sale_source": "eBay",
                "avg_last_3_sales": "120.5", "highest_sale": "200.0", "lowest_sale": "50.0"}
PREV_ROWS = [
    dict(PREV_SUMMARY, asset_id=A, index_transaction_count="10", pop_at_grade="5", scraped_at="2026-09-25T13:00:00"),
    dict(PREV_SUMMARY, asset_id=B, index_transaction_count="10", pop_at_grade="5", scraped_at="2026-09-25T13:00:00"),
    {"asset_id": C, "num_sales": "0", "pop_at_grade": "0", "pop_at_grade_9": "4", "scraped_at": "2026-09-25T13:00:00"},
    {"asset_id": D, "num_sales": "0", "pop_at_grade": "", "scraped_at": "2026-09-25T13:00:00"},
    dict(PREV_SUMMARY, asset_id=F, num_sales="3", pop_at_grade="0", scraped_at="2026-09-25T13:00:00"),
    dict(PREV_SUMMARY, asset_id=G, num_sales="2", pop_at_grade="9", scraped_at="2026-09-25T13:00:00"),
]

calls = []


def fake_pops(aid):
    calls.append(("pops", aid))
    return POPS[aid]


def fake_sales(aid, company, grade):
    calls.append(("sales", aid, grade))
    return SALES.get((aid, grade), [])


def fake_live(aid, company, grade):
    calls.append(("live", aid))
    return []


alt_scraper.fetch_pops, alt_scraper.fetch_sales, alt_scraper.fetch_live_listings = fake_pops, fake_sales, fake_live


def _write(path, cols, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in cols} for r in rows)


def _read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def run_scraper(out, extra):
    """One scraper run over IDS into `out` (asset: lines + sidecar, so no lookup request)."""
    out.mkdir(parents=True, exist_ok=True)
    lst = out / "cards.txt"
    lst.write_text("".join(f"asset:{i}\n" for i in IDS))
    with open(out / "cards.json", "w") as f:
        json.dump(DOCS, f)
    calls.clear()
    sys.argv = ["alt_scraper.py", "--delay", "0", "--max-sales", "0", "--keep-empty", "--also-grade", "9",
                "--out", str(out), *extra, str(lst)]
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        alt_scraper.main()
    cards = {r["asset_id"]: r for r in _read(out / "cards.csv")}
    sales = _read(out / "sales.csv")
    return cards, sales, log.getvalue()


def sales_calls(aid):
    return sorted(c[2] for c in calls if c[0] == "sales" and c[1] == aid)


def test_skip_unchanged_on():
    with tempfile.TemporaryDirectory() as d:
        prev = Path(d) / "prev.csv"
        _write(prev, ingest.DAILY_COLS, PREV_ROWS)
        cards, sales, log = run_scraper(Path(d) / "run", ["--skip-unchanged-sales", str(prev)])
        assert sorted(cards) == sorted(IDS), sorted(cards)
        # every card still gets its pops and live listings
        for i in IDS:
            assert ("pops", i) in calls and ("live", i) in calls, i
        # A: index count unchanged -> summary copied, nothing fetched, no sale rows, extra grades not claimed
        assert cards[A]["sales_fetched"] == "0"
        assert {k: cards[A][k] for k in PREV_SUMMARY} == PREV_SUMMARY, cards[A]
        assert sales_calls(A) == [], sales_calls(A)
        assert cards[A]["extra_grades"] == "", cards[A]["extra_grades"]
        assert cards[A]["pop_at_grade"] == "5" and cards[A]["scraped_at"] != "2026-09-25T13:00:00", "pops and scraped_at are fresh"
        # B: count changed -> fetched at both grades, summary from the fetch (2 clean sales, the RELISTED one excluded)
        assert cards[B]["sales_fetched"] == "1" and sales_calls(B) == ["10.0", "9.0"], sales_calls(B)
        assert cards[B]["num_sales"] == "2" and cards[B]["last_sale_price"] == "80.0" and cards[B]["extra_grades"] == "9.0", cards[B]
        # C: dormant at PSA 10, but PSA 9 copies exist -> PSA 10 carried, PSA 9 fetched
        assert cards[C]["sales_fetched"] == "0" and cards[C]["num_sales"] == "0" and cards[C]["last_sale_price"] == ""
        assert sales_calls(C) == ["9.0"], sales_calls(C)
        assert cards[C]["extra_grades"] == "9.0"
        # D: dormant with an unknown pop table -> nothing asked
        assert cards[D]["sales_fetched"] == "0" and sales_calls(D) == [] and cards[D]["extra_grades"] == ""
        assert cards[D]["pop_at_grade"] == "" and cards[D]["num_sales"] == "0"
        # E: unknown to the previous run -> fetched (PSA 9 skipped by the old known-zero rule, still claimed)
        assert cards[E]["sales_fetched"] == "1" and sales_calls(E) == ["10.0"] and cards[E]["num_sales"] == "1"
        assert cards[E]["extra_grades"] == "9.0"
        # F: pop 0 but sales on record -> not dormant, fetched
        assert cards[F]["sales_fetched"] == "1" and "10.0" in sales_calls(F) and cards[F]["num_sales"] == "1"
        # G: blank counter on both sides is unknown, not unchanged -> fetched
        assert cards[G]["sales_fetched"] == "1" and sorted(sales_calls(G)) == ["10.0", "9.0"] and cards[G]["num_sales"] == "2"
        # sales.csv: no rows for the carried cards' skipped grades
        by = {}
        for s in sales:
            by.setdefault(s["asset_id"], set()).add(s["grade"])
        assert A not in by and D not in by, by
        assert by[C] == {"9.0"} and by[B] == {"10.0", "9.0"} and by[G] == {"10.0", "9.0"}, by
        # the run log counts the carried cards by reason
        assert "3 card(s) kept the sale summary" in log and "1 index count unchanged, 2 dormant" in log, log
        assert "4 card(s) had their sales fetched" in log, log
        print("skip-unchanged on ok:", {i[-1]: (cards[i]["sales_fetched"], sales_calls(i)) for i in IDS})


def test_skip_unchanged_off():
    with tempfile.TemporaryDirectory() as d:
        cards, sales, log = run_scraper(Path(d) / "run", [])
        for i in IDS:
            assert cards[i]["sales_fetched"] == "1", (i, cards[i])
            assert "10.0" in sales_calls(i), (i, sales_calls(i))       # every card's PSA 10 sales fetched, as before
            assert cards[i]["extra_grades"] == "9.0", cards[i]         # --also-grade claimed for every row, as before
        # PSA 9 fetched unless the pop table shows a known zero (the pre-existing rule)
        assert "9.0" in sales_calls(D) and "9.0" not in sales_calls(E) and "9.0" not in sales_calls(F)
        assert cards[A]["num_sales"] == "1" and cards[A]["last_sale_price"] == "999.0"
        assert "skip-unchanged" not in log
        assert set(cards[A]) == set(alt_scraper.CARD_COLS + alt_scraper.LISTING_COLS)
        print("skip-unchanged off ok: every card fetched")


def test_ingest_and_metrics_carry_the_summary():
    with tempfile.TemporaryDirectory() as d:
        store, run = Path(d) / "store", Path(d) / "run"
        (store / "daily").mkdir(parents=True)
        prev = Path(d) / "prev.csv"
        _write(prev, ingest.DAILY_COLS, PREV_ROWS)
        run_scraper(run, ["--skip-unchanged-sales", str(prev)])
        # a daily file from before the column existed, same day, with an older row for A and a row only it has
        old_cols = [c for c in ingest.DAILY_COLS if c != "sales_fetched"]
        Z = "00000000-0000-0000-0000-00000000001a"
        _write(store / "daily" / "2026-09-26.csv", old_cols, [
            {"asset_id": A, "num_sales": "1", "scraped_at": "2000-01-01T01:00:00"},
            {"asset_id": Z, "num_sales": "4", "last_sale_price": "9.0", "scraped_at": "2000-01-01T01:00:00"},
        ])
        with contextlib.redirect_stdout(io.StringIO()):
            out = ingest.ingest(run, "2026-09-26", store)
        daily = {r["asset_id"]: r for r in _read(store / "daily" / "2026-09-26.csv")}
        assert "sales_fetched" in daily[A], daily[A].keys()
        assert daily[A]["sales_fetched"] == "0" and daily[A]["num_sales"] == "7", "newest scrape wins, carried summary kept"
        assert daily[B]["sales_fetched"] == "1" and daily[Z]["sales_fetched"] == "" and daily[Z]["num_sales"] == "4", "old rows read as fetched"
        assert out["sales_added"] == 10, out         # B 3+1, C 1 (PSA 9), E 1, F 1, G 2+1; nothing for A or D
        # metrics builds from the store: A's literal columns are the carried summary, and sales_fetched is not a feed column
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            metrics.build(store, Path(d) / "latest")
        feed = {r["asset_id"]: r for r in _read(Path(d) / "latest" / "cards.csv")}
        assert feed[A]["last_sale_price"] == "123.0" and feed[A]["num_sales"] == "7", feed[A]
        assert "sales_fetched" not in feed[A]
        assert feed[A]["sales_total"] == "0", "no store sales for A: the derived columns come from history/sales only"
        assert feed[B]["sales_total"] == "2" and feed[B]["clean_last_sale_price"] == "80", feed[B]
        print("ingest/metrics ok:", out)


if __name__ == "__main__":
    test_skip_unchanged_on()
    test_skip_unchanged_off()
    test_ingest_and_metrics_carry_the_summary()
    print("all skip-unchanged checks passed")
