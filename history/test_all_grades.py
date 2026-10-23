#!/usr/bin/env python3
"""Checks for the every-grade path (2026-10-21): nightly/all_grades_scope.py picks the Gold Star
category out of the index and widens the scope; alt_scraper.py's ladder, pops_by_grade and list
reader; history/ingest.py keeps the per-grade pops in history/grade_pops.csv; history/all_grades.py
builds latest/all_grades/ from them and the store with the feed's own rules.

    python3 history/test_all_grades.py

Plain asserts, no pytest."""
import csv
import importlib.util
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import all_grades  # noqa: E402
import ingest  # noqa: E402
import alt_scraper  # noqa: E402

spec = importlib.util.spec_from_file_location("all_grades_scope", ROOT / "nightly" / "all_grades_scope.py")
scope_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope_mod)

A, B, C, D, E = ("a" * 36, "b" * 36, "c" * 36, "d" * 36, "e" * 36)
INDEX = {
    A: {"id": A, "name": "2006 Pokemon Ex Dragon Frontiers Holo Charizard Gold Star #100", "year": 2006, "subject": "Charizard",
        "brand": "Pokemon Ex Dragon Frontiers", "cardNumber": "100", "pop": 6564},
    B: {"id": B, "name": "2004 Pokemon Clash of the Blue Sky First Edition Japanese Gold Staryu #21", "year": 2004, "subject": "Gold Staryu",
        "brand": "Pokemon Clash of the Blue Sky First Edition Japanese", "cardNumber": "21", "pop": 3},
    C: {"id": C, "name": "2021 Pokemon Celebrations Classic Collection Pop Series 5 Umbreon Gold Star #17", "year": 2021, "subject": "Umbreon",
        "brand": "Pokemon Celebrations Classic Collection", "cardNumber": "17", "pop": 40000},
    D: {"id": D, "name": "2008 Pokemon Diamond and Pearl Legends Awakened Holo Mewtwo Lv X #144", "year": 2008, "subject": "Mewtwo",
        "brand": "Pokemon Diamond and Pearl Legends Awakened", "cardNumber": "144", "pop": 900},
    E: {"id": E, "name": "2006 Pokemon Ex Dragon Frontiers Gold Star-German Glurak-Holo #100", "year": 2006, "subject": "Glurak-Holo",
        "brand": "Pokemon Ex Dragon Frontiers", "cardNumber": "100", "pop": 12},
}


def test_scope_selection_and_widening():
    docs = scope_mod.select(INDEX)
    assert [d["id"] for d in docs] == [A, E], [d["name"] for d in docs]   # the two Gold Stars, biggest pop first; no Staryu, reprint or LV.X
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        scope_mod.write_list(d / "all_grades.txt", docs)
        assert alt_scraper.read_all_grades([d / "all_grades.txt"]) == {A, E}
        assert set(json.load(open(d / "all_grades.json"))) == {A, E}
        (d / "scope.txt").write_text(f"# scope\n\nasset:{A}   # pop 6564  Charizard\nasset:{D}   # pop 900  Mewtwo\n")
        json.dump({A: INDEX[A], D: INDEX[D]}, open(d / "scope.json", "w"))
        assert scope_mod.add_to_scope(d / "scope.txt", docs) == 1
        lines = [l for l in (d / "scope.txt").read_text().splitlines() if l.startswith("asset:")]
        assert [l.split()[0][6:] for l in lines] == [A, D, E], lines
        assert set(json.load(open(d / "scope.json"))) == {A, D, E}
        assert scope_mod.add_to_scope(d / "scope.txt", docs) == 0, "already there: nothing appended twice"
    print("scope selection ok")


def test_scraper_helpers():
    assert alt_scraper.PSA_GRADES == ["1.0", "1.5", "2.0", "2.5", "3.0", "3.5", "4.0", "4.5", "5.0", "5.5", "6.0", "6.5", "7.0", "7.5",
                                      "8.0", "8.5", "9.0", "10.0"]
    assert alt_scraper.ladder_grades("10.0", ["9.0"]) == alt_scraper.PSA_GRADES[:-2]
    assert alt_scraper.ladder_grades("9.0") == [g for g in alt_scraper.PSA_GRADES if g != "9.0"]
    pops = [{"gradingCompany": "PSA", "gradeNumber": "10.0", "count": 98}, {"gradingCompany": "BGS", "gradeNumber": "9.5", "count": 4},
            {"gradingCompany": "PSA", "gradeNumber": "1.5", "count": 32}, {"gradingCompany": "PSA", "gradeNumber": "0.0", "count": 30}]
    assert alt_scraper.pops_by_grade(pops, "PSA") == "0.0:30 1.5:32 10.0:98"
    assert alt_scraper.pops_by_grade(pops, "SGC") == "", "no rows for the company = unknown, blank"
    row = alt_scraper.summarise({"id": A, "name": "x", "attributes": {}}, pops, [], "PSA", "10.0", "asset:" + A, {}, all_grades=True)
    assert row["pops_by_grade"] == "0.0:30 1.5:32 10.0:98" and row["pop_at_grade"] == 98
    row = alt_scraper.summarise({"id": A, "name": "x", "attributes": {}}, pops, [], "PSA", "10.0", "asset:" + A, {})
    assert row["pops_by_grade"] == "", "only the --all-grades-for cards carry it"
    assert "pops_by_grade" in alt_scraper.CARD_COLS
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "l.txt"
        p.write_text(f"# comment\n\nasset:{A}   # pop 1  name\n{B}\n")
        assert alt_scraper.read_all_grades([p]) == {A, B}
    print("scraper helpers ok")


def card(aid, pops, scraped_at="2026-10-21T05:00:00+00:00", **kw):
    r = {"asset_id": aid, "grading_company": "PSA", "pops_by_grade": pops, "scraped_at": scraped_at}
    r.update(kw)
    return r


def test_ingest_grade_pops():
    with tempfile.TemporaryDirectory() as d:
        store = Path(d)
        assert ingest.ingest_grade_pops([card(A, "")], store, "2026-10-21") == 0 and not (store / "grade_pops.csv").exists()
        n = ingest.ingest_grade_pops([card(A, "0.0:30 1.0:912 8.5:40 9.0:554 10.0:98"), card(B, ""), card(E, "10.0:1")], store, "2026-10-21")
        assert n == 2
        rows = {(r["data_date"], r["asset_id"]): r for r in ingest.read_csv(store / "grade_pops.csv")}
        a = rows[("2026-10-21", A)]
        assert (a["pop_0"], a["pop_1"], a["pop_1_5"], a["pop_8_5"], a["pop_9"], a["pop_10"], a["pop_total"]) == ("30", "912", "0", "40", "554", "98", "1634")
        assert rows[("2026-10-21", E)]["pop_10"] == "1" and rows[("2026-10-21", E)]["pop_1"] == "0"
        # a later scrape of the same day replaces, an earlier one does not; another day is kept
        ingest.ingest_grade_pops([card(A, "10.0:99", scraped_at="2026-10-21T06:00:00+00:00")], store, "2026-10-21")
        ingest.ingest_grade_pops([card(A, "10.0:1", scraped_at="2026-10-21T01:00:00+00:00")], store, "2026-10-21")
        ingest.ingest_grade_pops([card(A, "10.0:100")], store, "2026-10-22")
        rows = {(r["data_date"], r["asset_id"]): r for r in ingest.read_csv(store / "grade_pops.csv")}
        assert rows[("2026-10-21", A)]["pop_10"] == "99" and rows[("2026-10-22", A)]["pop_10"] == "100" and len(rows) == 3
        assert list(rows) == sorted(rows), "sorted by day then asset"
        assert ingest.GRADE_POP_COLS[:4] == ["data_date", "asset_id", "grading_company", "pop_0"] and ingest.GRADE_POP_COLS[-2:] == ["pop_total", "scraped_at"]
    print("ingest grade pops ok")


def sale(aid, grade, day, price, status="", url="", source="eBay"):
    return {"asset_id": aid, "date": day, "price": str(price), "grading_company": "PSA", "grade": grade, "source": source,
            "sale_type": "AUCTION", "url": url or f"https://www.ebay.com/itm/{aid[:2]}{grade}{day}{price}", "label": "",
            "subject_to_change": "False", "skipped_reason": status, "alt_tx_id": ""}


def test_build():
    with tempfile.TemporaryDirectory() as d:
        store, out = Path(d) / "history", Path(d) / "latest"
        (store / "sales").mkdir(parents=True)
        (store / "daily").mkdir()
        def asset(aid):
            d = INDEX[aid]
            return {"asset_id": aid, "card_name": d["name"], "alt_url": "", "year": str(d["year"]), "set": d["brand"], "card_number": d["cardNumber"],
                    "subject": d["subject"], "variety": "", "grading_company": "PSA", "grade": "10.0", "first_seen": "2026-09-11"}
        # A: Gold Star with a pop table; E: Gold Star alt.xyz has no PSA pop rows for (no grade_pops row, 2026-10-21's
        # 60 of 161); D: an LV.X with sales, not the category; B (the Staryu) is not even in assets.csv
        # F: a Gold Star alt.xyz names without "Star" (POP Series 5 Espeon #16), in by its set's number (2026-10-22);
        # G: the same rule's stale twin of the Groudon Star, last listed 09-11: out like cards.csv leaves it out
        F, G = "f0000000-0000-0000-0000-000000000016", "60000000-0000-0000-0000-000000000111"
        extra = [{"asset_id": F, "card_name": "2007 Pokemon Pop Series 5 Espeon #16", "alt_url": "", "year": "2007", "set": "Pokemon Pop Series 5",
                  "card_number": "16", "subject": "Espeon", "variety": "", "grading_company": "PSA", "grade": "10.0", "first_seen": "2026-09-11"},
                 {"asset_id": G, "card_name": "2005 Ex Delta Species English Holo Groudon #111", "alt_url": "", "year": "2005", "set": "Ex Delta Species",
                  "card_number": "111", "subject": "Groudon", "variety": "", "grading_company": "PSA", "grade": "10.0", "first_seen": "2026-09-10"}]
        ingest.write_csv_atomic(store / "assets.csv", ingest.ASSET_COLS, [asset(A), asset(E), asset(D)] + extra)
        (store / "daily" / "2026-09-11.csv").write_text(f"asset_id\n{G}\n{F}\n")
        (store / "daily" / "2026-10-21.csv").write_text(f"asset_id\n{F}\n")
        ingest.ingest_grade_pops([card(A, "0.0:30 1.0:912 7.0:573 9.0:554 10.0:98", scraped_at="2026-09-20T05:00:00+00:00")], store, "2026-09-20")
        ingest.ingest_grade_pops([card(A, "0.0:30 1.0:915 7.0:580 9.0:554 10.0:98")], store, "2026-10-21")
        ingest.write_csv_atomic(store / "sales" / "2026-10.csv", ingest.SALE_COLS, [
            sale(A, "7.0", "2026-10-01", 20000), sale(A, "7.0", "2026-10-05", 21000), sale(A, "7.0", "2026-10-08", 26100, source="Alt"),
            sale(A, "7.0", "2026-10-10", 19500), sale(A, "7.0", "2026-10-18", 20000),
            sale(A, "7.0", "2026-10-19", 500),                 # 1/40th of the reference: held back (low side)
            sale(A, "7.0", "2026-10-20", 22000, status="PENDING"),
            sale(A, "10.0", "2026-10-02", 150000), sale(A, "9.0", "2026-10-03", 60000),
            sale(A, "1.0", "2026-10-04", 3000), sale(B, "7.0", "2026-10-04", 1),   # B is not in assets.csv: not in the output
            sale(E, "7.0", "2026-10-06", 700), sale(E, "4.0", "2026-10-07", 400, status="NOT_PAID"),   # E: no pops, sales from the store
            sale(D, "7.0", "2026-10-06", 70),                                                         # D: LV.X, not the category
        ])
        summary = all_grades.build(store, out, today=date(2026, 10, 21))
        assert (summary["cards"], summary["cards_with_pops"], summary["grade_rows"], summary["sales"], summary["clean_sales"]) == (3, 1, 54, 12, 9), summary
        all_rows = ingest.read_csv(out / "grades.csv")
        assert [r["asset_id"] for r in all_rows] == [A] * 18 + [E] * 18 + [F] * 18, "sorted by asset, 18 grades each; D, B and the unlisted G absent"
        all_rows = [r for r in all_rows if r["asset_id"] != F]
        e = {r["grade"]: r for r in all_rows if r["asset_id"] == E}
        assert (e["7.0"]["card_name"], e["7.0"]["pop"], e["7.0"]["pop_date"], e["7.0"]["scraped_date"], e["7.0"]["pop_30d_ago"]) == (INDEX[E]["name"], "", "", "", ""), e["7.0"]
        assert (e["7.0"]["sales_total"], e["7.0"]["last_sale_price"], e["7.0"]["median_last_3"], e["7.0"]["last_sale_status"]) == ("1", "700", "700", "completed")
        assert (e["4.0"]["sales_total"], e["4.0"]["last_sale_price"], e["4.0"]["last_sale_status"], e["4.0"]["pop"]) == ("0", "", "unpaid", ""), e["4.0"]
        assert (e["2.0"]["sales_total"], e["2.0"]["pop"]) == ("0", ""), "blank pop = unknown, 0 sales = none in the store"
        rows = {r["grade"]: r for r in all_rows if r["asset_id"] == A}
        assert list(rows) == all_grades.GRADES
        r7 = rows["7.0"]
        assert (r7["card_name"], r7["subject"], r7["pop"], r7["pop_date"], r7["pop_30d_ago"], r7["pop_chg_30d"], r7["scraped_date"]) \
            == (INDEX[A]["name"], "Charizard", "580", "2026-10-21", "573", "7", "2026-10-21"), r7
        assert (r7["sales_total"], r7["outliers_excluded"], r7["last_sale_price"], r7["last_sale_date"], r7["clean_last_sale_price"]) \
            == ("5", "1", "500", "2026-10-19", "20000"), r7
        assert (r7["last_sale_unconfirmed"], r7["last_sale_single"], r7["last_sale_status"], r7["last_sale_newer_price"], r7["last_sale_newer_date"]) \
            == ("1", "0", "pending", "22000", "2026-10-20"), r7
        assert (r7["median_last_3"], r7["cap_price"], r7["volume_30d"], r7["volume_1y"], r7["highest_sale"], r7["lowest_sale"], r7["sales_first_date"]) \
            == ("20000", "20000", "5", "5", "26100", "19500", "2026-10-01"), r7
        assert r7["price_chg_30d_pct"] == "" and r7["chg_held_windows"] == "", "no sale before the window: blank, as in cards.csv"
        r1, r2, r10 = rows["1.0"], rows["2.0"], rows["10.0"]
        assert (r1["pop"], r1["sales_total"], r1["last_sale_price"], r1["median_last_3"]) == ("915", "1", "3000", "3000"), r1
        assert (r2["pop"], r2["sales_total"], r2["last_sale_price"], r2["scraped_date"]) == ("0", "0", "", "2026-10-21"), "pop 0 and no sales: still a row"
        assert (r10["pop"], r10["sales_total"], r10["last_sale_price"]) == ("98", "1", "150000")
        sales = list(csv.DictReader(open(out / "sales.csv")))
        assert [s["grade"] for s in sales] == ["1.0"] + ["7.0"] * 7 + ["9.0", "10.0", "4.0", "7.0"]
        assert [(s["asset_id"], s["status"]) for s in sales[-2:]] == [(E, "NOT_PAID"), (E, "ok")]
        sales = sales[:-2]
        s7 = [s for s in sales if s["grade"] == "7.0"]
        assert [s["date"] for s in s7] == sorted((s["date"] for s in s7), reverse=True), "newest first"
        assert [(s["date"], s["status"], s["outlier"]) for s in s7[:3]] == [("2026-10-20", "PENDING", "0"), ("2026-10-19", "ok", "1"), ("2026-10-18", "ok", "0")]
        assert all(s["asset_id"] == A for s in sales)
        assert json.load(open(out / "summary.json"))["pops_date_newest"] == "2026-10-21"
        # no grade_pops.csv at all: nothing written, no error
        (store / "grade_pops.csv").unlink()
        assert all_grades.build(store, Path(d) / "latest2", today=date(2026, 10, 21)) is None and not (Path(d) / "latest2").exists()
    print("build ok")


if __name__ == "__main__":
    test_scope_selection_and_widening()
    test_scraper_helpers()
    test_ingest_grade_pops()
    test_build()
    print("all every-grade checks passed")
