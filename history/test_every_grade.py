#!/usr/bin/env python3
"""Checks for every card at every PSA grade (2026-11-25): alt_scraper.py --every-grade (one allGrades
request per card, the every-grade dormant rule), history/ingest.py (pops_by_grade in the daily files,
REMOVED marking at a lower grade) and history/all_grades.py build_every_card (latest/grades/ and
latest/recent_sales_grades/).

    python3 history/test_every_grade.py

Plain asserts, no pytest. The network functions are monkeypatched: nothing here talks to alt.xyz."""
import contextlib
import csv
import io
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "history"))
import alt_scraper                                # noqa: E402
import ingest                                     # noqa: E402
import all_grades                                 # noqa: E402

P = "00000000-0000-0000-0000-0000000000a1"   # PSA 10 / 9 / 8 sales + noise           -> fetched once, PSA rows on the ladder kept
Q = "00000000-0000-0000-0000-0000000000a2"   # PSA rows, every count 0, no sales      -> carried (dormant), known zero everywhere
R = "11000000-0000-0000-0000-0000000000a3"   # no PSA 10 but PSA 8s, no PSA 10 sales  -> fetched (the old rule carried it)
S = "11000000-0000-0000-0000-0000000000a4"   # no PSA rows at all, no sales           -> carried, nothing vouched for
IDS = [P, Q, R, S]


def psa(grade, count, company="PSA"):
    return {"gradingCompany": company, "gradeNumber": grade, "count": count}


POPS = {P: [psa("10.0", 5), psa("9.0", 3), psa("8.0", 7), psa("9.5", 2, "BGS")],
        Q: [psa("10.0", 0), psa("9.0", 0)],
        R: [psa("10.0", 0), psa("8.0", 4), psa("0.0", 1)],
        S: [psa("9.5", 3, "BGS")]}


def tx(n, grade, price, day, company="PSA", skipped=None):
    return {"id": f"tx{n}", "date": day, "auctionHouse": "eBay", "auctionType": "AUCTION", "price": price,
            "attributes": {"gradeNumber": grade, "gradingCompany": company, "url": f"https://www.ebay.com/itm/{n}", "autograph": None},
            "subjectToChange": False, "consolidatedSkippedReason": skipped, "label": None}


ALL = {P: [tx(1, "10.0", "100", "2026-11-20"), tx(2, "10.0", "90", "2026-11-01"), tx(3, "9.0", "40", "2026-11-19"),
           tx(4, "8.0", "20", "2026-11-18"), tx(5, "8.0", "18", "2026-10-30"), tx(6, "8.0", "1", "2026-11-17", skipped="RELISTED"),
           tx(7, "9.5", "70", "2026-11-18", "BGS"), tx(8, None, "5", "2026-11-18", "Raw"),
           tx(9, "0.0", "3", "2026-11-10"), tx(10, None, "4", "2026-11-10")],
       Q: [tx(11, "10.0", "1", "2026-11-20")],        # must never be asked for
       R: [tx(12, "8.0", "30", "2026-11-21"), tx(13, "7.0", "12", "2026-11-02")],
       S: [tx(14, "9.5", "50", "2026-11-21", "BGS")]}   # must never be asked for
DOCS = {i: {"id": i, "name": f"card {i[-2:]}", "year": 1999, "subject": "Pikachu", "category": "POKEMON_CARDS",
            "brand": "Base Set", "variety": "", "cardNumber": i[-2:], "pop": 10} for i in IDS}
PREV = [{"asset_id": P, "num_sales": "2", "pop_at_grade": "5", "scraped_at": "2026-11-20T13:00:00+00:00"},
        {"asset_id": Q, "num_sales": "0", "pop_at_grade": "0", "scraped_at": "2026-11-20T13:00:00+00:00"},
        {"asset_id": R, "num_sales": "0", "pop_at_grade": "0", "scraped_at": "2026-11-20T13:00:00+00:00"},
        {"asset_id": S, "num_sales": "0", "pop_at_grade": "", "scraped_at": "2026-11-20T13:00:00+00:00"}]
calls = []


def fake_pops(aid):
    calls.append(("pops", aid))
    return POPS[aid]


def fake_sales(aid, company, grade):
    raise AssertionError(f"--every-grade must not ask per grade ({aid}, {grade})")


def fake_all(aid):
    calls.append(("all", aid))
    return ALL[aid]


def fake_live(aid, company, grade):
    calls.append(("live", aid, grade))
    return []


alt_scraper.fetch_pops, alt_scraper.fetch_sales = fake_pops, fake_sales
alt_scraper.fetch_sales_all, alt_scraper.fetch_live_listings = fake_all, fake_live


def _write(path, cols, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in cols} for r in rows)


def _read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def run_scraper(out, extra=()):
    out.mkdir(parents=True, exist_ok=True)
    (out / "cards.txt").write_text("".join(f"asset:{i}\n" for i in IDS))
    json.dump(DOCS, open(out / "cards.json", "w"))
    calls.clear()
    sys.argv = ["alt_scraper.py", "--delay", "0", "--max-sales", "0", "--keep-empty", "--also-grade", "9", "--also-listings",
                "--every-grade", "--out", str(out), *extra, str(out / "cards.txt")]
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        assert alt_scraper.main() == 0, log.getvalue()
    return {r["asset_id"]: r for r in _read(out / "cards.csv")}, _read(out / "sales.csv"), log.getvalue()


LADDER_BUT_10 = " ".join(g for g in alt_scraper.PSA_GRADES if g != "10.0")


def test_split_by_grade():
    by, dropped = alt_scraper.split_by_grade(ALL[P], "PSA")
    assert {g: [s["id"] for s in v] for g, v in by.items()} == {"10.0": ["tx1", "tx2"], "9.0": ["tx3"], "8.0": ["tx4", "tx5", "tx6"]}
    assert dropped == 2, "PSA 0.0 (Authentic) and a PSA row without a grade are counted; BGS and raw are not"
    _, dropped = alt_scraper.split_by_grade([tx(90, "x", "5", "2026-11-01"), tx(91, "10", "5", "2026-11-01")], "PSA")
    assert dropped == 1 and alt_scraper.split_by_grade([tx(91, "10", "5", "2026-11-01")], "PSA")[0]["10.0"]
    # a PSA pop row with a blank grade (612a6e67 on 2026-11-25) used to make the card fail
    pops = [psa("10.0", 15056), psa("", 1509), psa("9.0", 19950)]
    assert alt_scraper.pops_by_grade(pops, "PSA") == "9.0:19950 10.0:15056 na:1509"
    row = ingest.grade_pops_row({"asset_id": P, "pops_by_grade": "9.0:19950 10.0:15056 na:1509"}, "2026-11-25")
    assert (row["pop_9"], row["pop_10"], row["pop_total"]) == (19950, 15056, 36515), row
    print("split_by_grade ok")


def test_scraper_every_grade():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        prev = d / "prev.csv"
        _write(prev, ingest.DAILY_COLS, PREV)
        cards, sales, log = run_scraper(d / "run", ["--skip-unchanged-sales", str(prev)])
        alls = [c[1] for c in calls if c[0] == "all"]
        assert sorted(alls) == sorted([P, R]), alls            # Q and S carried; one request each for P and R
        assert sorted((c[1], c[2]) for c in calls if c[0] == "live") == sorted(
            [(P, "10.0"), (P, "9.0"), (Q, "10.0"), (R, "10.0"), (S, "10.0"), (S, "9.0")]), \
            "listings stay at 10, and 9 unless the pop table shows no 9s (S has no PSA rows: unknown, asked)"
        by_id = {s["alt_tx_id"]: s for s in sales}
        assert sorted(by_id) == ["tx1", "tx12", "tx13", "tx2", "tx3", "tx4", "tx5", "tx6"], sorted(by_id)
        assert all(s["grading_company"] == "PSA" for s in sales)
        p = cards[P]
        assert (p["num_sales"], p["last_sale_price"], p["sales_fetched"]) == ("2", "100.0", "1"), p
        assert p["extra_grades"] == LADDER_BUT_10 and p["pops_by_grade"] == "8.0:7 9.0:3 10.0:5"
        q = cards[Q]
        assert q["sales_fetched"] == "0" and q["extra_grades"] == LADDER_BUT_10, "dormant: a known zero at every grade"
        r = cards[R]
        assert r["sales_fetched"] == "1" and r["num_sales"] == "0" and r["extra_grades"] == LADDER_BUT_10 and r["pops_by_grade"] == "0.0:1 8.0:4 10.0:0"
        s = cards[S]
        assert s["sales_fetched"] == "0" and s["extra_grades"] == "" and s["pops_by_grade"] == "", "no PSA rows: unknown, nothing vouched"
        stats = json.load(open(d / "run" / "run_stats.json"))["scrape"]
        assert (stats["every_grade"], stats["all_grades_cards"], stats["all_grades_requests"], stats["every_grade_sales_dropped"]) == (True, 2, 2, 2), stats
        assert "--every-grade: 2 card(s)" in log
    print("scraper --every-grade ok")


def test_old_dormant_rule_unchanged():
    pops = [psa("10.0", 0), psa("8.0", 4)]
    prev = {"num_sales": "0"}
    assert alt_scraper.sales_skip_reason(prev, {}, pops, "PSA", "10.0") == alt_scraper.SKIP_DORMANT
    assert alt_scraper.sales_skip_reason(prev, {}, pops, "PSA", "10.0", every_grade=True) is None
    assert alt_scraper.sales_skip_reason(prev, {}, [psa("10.0", 0)], "PSA", "10.0", every_grade=True) == alt_scraper.SKIP_DORMANT
    assert alt_scraper.sales_skip_reason({"num_sales": "3"}, {}, [], "PSA", "10.0", every_grade=True) is None
    print("dormant rules ok")


def test_ingest_and_build():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        store, latest = d / "history", d / "latest"
        (store / "sales").mkdir(parents=True)
        (store / "daily").mkdir()
        # before any --every-grade run: nothing written
        assert all_grades.build_every_card(store, latest, today=date(2026, 11, 22)) is None and not (latest / "grades").exists()
        run_scraper(d / "run1")
        ingest.ingest(d / "run1", "2026-11-22", store)
        daily = {r["asset_id"]: r for r in _read(store / "daily" / "2026-11-22.csv")}
        assert daily[P]["pops_by_grade"] == "8.0:7 9.0:3 10.0:5" and daily[S]["pops_by_grade"] == ""
        assert not (store / "grade_pops.csv").exists(), "no Gold Star in the run: the committed file is not touched"
        stored = {r["alt_tx_id"]: r for p in (store / "sales").glob("*.csv") for r in _read(p)}
        assert {"tx4", "tx5", "tx6", "tx12", "tx13"} <= set(stored)

        summary = all_grades.build_every_card(store, latest, today=date(2026, 11, 22))
        rows = {(r["asset_id"], r["grade"]): r for p in (latest / "grades").glob("*.csv") for r in _read(p)}
        # P at 8 (pop 7, sales); R at 8 (pop 4, a sale) and 7 (no pop row = 0, but a sale); nothing for Q / S, no 9 / 10
        assert sorted(rows) == sorted([(P, "8.0"), (R, "7.0"), (R, "8.0")]), sorted(rows)
        p8 = rows[(P, "8.0")]
        assert (p8["pop"], p8["pop_date"], p8["sales_total"], p8["last_sale_price"], p8["last_sale_date"]) == ("7", "2026-11-22", "2", "20", "2026-11-18"), p8
        assert rows[(R, "7.0")]["pop"] == "0" and rows[(R, "7.0")]["sales_total"] == "1"
        assert sorted(p.name for p in (latest / "grades").glob("*.csv")) == ["00.csv", "11.csv"], "sharded by asset_id[:2]"
        recent = [r for p in (latest / "recent_sales_grades").glob("*.csv") for r in _read(p)]
        assert sorted((r["asset_id"], r["grade"], r["price"], r["status"]) for r in recent) == sorted(
            [(P, "8.0", "20", "ok"), (P, "8.0", "18", "ok"), (P, "8.0", "1", "RELISTED"), (R, "8.0", "30", "ok"), (R, "7.0", "12", "ok")])
        man = json.load(open(latest / "grades" / "manifest.json"))
        assert set(man["shards"]) == {"00", "11"} and man["columns"] == all_grades.EVERY_COLS
        assert "card_name" not in man["columns"] and {"asset_id", "grade", "pop", "scraped_date", "cap_price"} <= set(man["columns"])
        assert (summary["cards"], summary["grade_rows"], summary["recent_sales_rows"]) == (2, 3, 5), summary

        # the next night alt.xyz no longer lists P's $18 PSA 8 sale: REMOVED at that grade, as at PSA 10 / 9
        ALL[P] = [s for s in ALL[P] if s["id"] != "tx5"]
        run_scraper(d / "run2")
        ingest.ingest(d / "run2", "2026-11-23", store)
        stored = {r["alt_tx_id"]: r for p in (store / "sales").glob("*.csv") for r in _read(p)}
        assert stored["tx5"]["skipped_reason"] == ingest.REMOVED and stored["tx4"]["skipped_reason"] == ""
        all_grades.build_every_card(store, latest, today=date(2026, 11, 23))
        rows = {(r["asset_id"], r["grade"]): r for p in (latest / "grades").glob("*.csv") for r in _read(p)}
        assert rows[(P, "8.0")]["sales_total"] == "1" and rows[(P, "8.0")]["pop_date"] == "2026-11-23"
        # recent_sales_grades keeps the last EVERY_RECENT_N (10) per card and grade: R gets 10 more PSA 7 sales
        ALL[R] = ALL[R] + [tx(70 + i, "7.0", str(10 + i), f"2026-11-{10 + i}") for i in range(10)]
        run_scraper(d / "run3")
        ingest.ingest(d / "run3", "2026-11-24", store)
        all_grades.build_every_card(store, latest, today=date(2026, 11, 24))
        recent = [r for p in (latest / "recent_sales_grades").glob("*.csv") for r in _read(p) if (r["asset_id"], r["grade"]) == (R, "7.0")]
        assert [r["price"] for r in recent] == [str(p) for p in range(19, 9, -1)], recent
        rows = {(r["asset_id"], r["grade"]): r for p in (latest / "grades").glob("*.csv") for r in _read(p)}
        assert rows[(R, "7.0")]["sales_total"] == "11", "the grade row still counts every sale"
    print("ingest + build_every_card ok")


if __name__ == "__main__":
    test_split_by_grade()
    test_scraper_every_grade()
    test_old_dormant_rule_unchanged()
    test_ingest_and_build()
    print("all --every-grade checks passed")
