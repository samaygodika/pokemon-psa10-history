#!/usr/bin/env python3
"""Checks for the run's account of itself (2026-10-17): alt_scraper.py writes OUT/run_stats.json and exits 2 when
cards failed, the counts add up across --resume passes, scraped_at is UTC with offset, nightly/run_stats.py
judges the run (the gate, the Fanatics pull's health, the commit message), history/ingest.py copies the file
into the store and history/metrics.py publishes it as summary.json `last_run` with a UTC generated_at.

    python3 history/test_run_stats.py

Plain asserts, no pytest (it isn't installed here). The network functions are monkeypatched: nothing here
talks to alt.xyz."""
import contextlib
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "history"))
sys.path.insert(0, str(ROOT / "nightly"))
import alt_scraper                                # noqa: E402
import ingest                                     # noqa: E402
import metrics                                    # noqa: E402
import run_stats                                  # noqa: E402

IDS = [f"00000000-0000-0000-0000-0000000000{i:02x}" for i in range(1, 11)]   # ten cards
FAIL_POPS = {IDS[3], IDS[7]}       # the pop request of these two fails on the first pass only
FAIL_LISTINGS = {IDS[0], IDS[1]}   # the PSA 10 listings request of these two always fails
FAIL_LISTINGS9 = {IDS[2]}          # the PSA 9 listings request of this one always fails
DOCS = {i: {"id": i, "name": f"card {n}", "year": 1999, "subject": "Pikachu", "category": "POKEMON_CARDS", "brand": "Base Set",
            "variety": "", "cardNumber": str(n), "pop": 100} for n, i in enumerate(IDS, 1)}
failed_once = set()


def fake_pops(aid):
    if aid in FAIL_POPS and aid not in failed_once:
        failed_once.add(aid)
        raise RuntimeError("HTTP 502 (pretend)")
    return [{"gradingCompany": "PSA", "gradeNumber": "10.0", "count": 5}, {"gradingCompany": "PSA", "gradeNumber": "9.0", "count": 3}]


def fake_sales(aid, company, grade):
    return [{"id": f"tx-{aid[-2:]}-{grade}", "date": "2026-10-01", "auctionHouse": "eBay", "auctionType": "AUCTION", "price": "50",
             "attributes": {"gradeNumber": grade, "gradingCompany": "PSA", "url": f"https://www.ebay.com/itm/{aid[-2:]}{grade[0]}"},
             "subjectToChange": False, "consolidatedSkippedReason": None, "label": None}]


def fake_live(aid, company, grade):
    if (grade == "10.0" and aid in FAIL_LISTINGS) or (grade == "9.0" and aid in FAIL_LISTINGS9):
        return None      # the request failed: unchecked, not "nothing listed"
    return []


alt_scraper.fetch_pops, alt_scraper.fetch_sales, alt_scraper.fetch_live_listings = fake_pops, fake_sales, fake_live


def run_scraper(out, extra=()):
    out.mkdir(parents=True, exist_ok=True)
    lst = out / "cards.txt"
    lst.write_text("".join(f"asset:{i}\n" for i in IDS))
    (out / "cards.json").write_text(json.dumps(DOCS))
    sys.argv = ["alt_scraper.py", "--delay", "0", "--max-sales", "0", "--keep-empty", "--also-grade", "9", "--also-listings",
                "--out", str(out), *extra, str(lst)]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return alt_scraper.main()


def _read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def test_scraper_writes_run_stats_and_exits_2():
    failed_once.clear()
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "run"
        rc = run_scraper(out)
        st = json.load(open(out / "run_stats.json"))["scrape"]
        assert rc == 2, "some cards failed -> exit 2"
        assert st["scope"] == 10 and st["ok"] == 8 and st["failed"] == 2 and st["skipped"] == 0 and st["passes"] == 1, st
        # listings: asked for every card that got a row (8), two of those requests failed; PSA 9 likewise, one failed
        assert st["listings_asked"] == 8 and st["listings_failed"] == 2, st
        assert st["psa9_listings_asked"] == 8 and st["psa9_listings_failed"] == 1, st
        assert st["company"] == "PSA" and st["grade"] == "10.0" and st["also_grades"] == ["9.0"] and st["listings_only"] is False, st
        assert st["started_at"].endswith("+00:00") and st["finished_at"].endswith("+00:00") and st["finished_at"] >= st["started_at"]
        rows = _read(out / "cards.csv")
        assert len(rows) == 8 and all(r["scraped_at"].endswith("+00:00") and len(r["scraped_at"]) == 25 for r in rows), rows[0]["scraped_at"]
        assert sum(1 for r in rows if not r["listings_checked_at"]) == 2 and sum(1 for r in rows if not r["psa9_listings_checked_at"]) == 1
        # the retry pass (--resume): only the two missing cards are fetched; the counts add up, failed is this pass's
        rc = run_scraper(out, ["--resume"])
        st = json.load(open(out / "run_stats.json"))["scrape"]
        assert rc == 0 and st["ok"] == 10 and st["failed"] == 0 and st["passes"] == 2 and st["scope"] == 10, st
        assert st["listings_asked"] == 10 and st["listings_failed"] == 2 and st["psa9_listings_asked"] == 10 and st["psa9_listings_failed"] == 1, st
        assert len(_read(out / "cards.csv")) == 10
        # a listings-only run (the refresh) accounts for its one request per card
        failed_once.clear()
        out2 = Path(d) / "refresh"
        out2.mkdir()
        lst = out2 / "refresh_psa10.txt"
        lst.write_text("".join(f"asset:{i}\n" for i in IDS[:4]))
        (out2 / "refresh_psa10.json").write_text(json.dumps({i: DOCS[i] for i in IDS[:4]}))
        sys.argv = ["alt_scraper.py", "--listings-only", "--grade", "10", "--keep-empty", "--out", str(out2), str(lst)]
        with contextlib.redirect_stdout(io.StringIO()):
            rc = alt_scraper.main()
        st = json.load(open(out2 / "run_stats.json"))["scrape"]
        assert rc == 0 and st["ok"] == 4 and st["listings_asked"] == 4 and st["listings_failed"] == 2 and st["listings_only"] is True, st
    print("scraper run_stats + exit code ok")


def _stats(scope=100, ok=100, failed=0, la=100, lf=0, l9a=60, l9f=0, **extra):
    s = {"day": "2026-10-17", "scope": "top60", "started_at": "2026-10-17T01:20:00+00:00",
         "scrape": {"scope": scope, "ok": ok, "failed": failed, "skipped": 0, "carried": 7, "passes": 1,
                    "listings_asked": la, "listings_failed": lf, "psa9_listings_asked": l9a, "psa9_listings_failed": l9f}}
    s.update(extra)
    return s


def test_merge_shards():
    """The assemble stage adds the scrape jobs' accounts up; a shard that died is accounted from its files."""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        with contextlib.redirect_stdout(io.StringIO()):
            run_stats.start(out, "2026-10-17", "top60")
            assert run_stats.merge_shards(out) is None, "no shard folders: nothing to do"
            for i in range(3):
                (out / f"shard-{i}").mkdir()
            s0 = _stats(scope=50, ok=48, failed=2, la=48, lf=3, l9a=30, l9f=1)["scrape"]
            s0.update(company="PSA", grade="10.0", also_grades=["9.0"], listings_only=False, passes=2,
                      started_at="2026-10-17T01:30:00+00:00", finished_at="2026-10-17T04:10:00+00:00")
            run_stats.save(out / "shard-0", {"scrape": s0})
            # shard 1 died mid-way: two rows in cards.csv (one listings check failed), five cards in its scope, no account
            (out / "shard-1" / "scope.txt").write_text("# shard 1 of 3: 5 cards\n" + "".join(f"asset:{i}\n" for i in range(5)))
            ingest.write_csv_atomic(out / "shard-1" / "cards.csv", ["asset_id", "listings_checked_at"],
                                    [{"asset_id": "0", "listings_checked_at": "2026-10-17T01:40:00+00:00"}, {"asset_id": "1", "listings_checked_at": ""}])
            # shard 2 never ran at all (its job never started): scope only
            (out / "shard-2" / "scope.txt").write_text("".join(f"asset:{i}\n" for i in range(3)))
            merged = run_stats.merge_shards(out)
        assert merged["scope"] == 58 and merged["ok"] == 50 and merged["failed"] == 2 + 3 + 3, merged
        assert merged["listings_asked"] == 50 and merged["listings_failed"] == 4 and merged["psa9_listings_asked"] == 30 and merged["psa9_listings_failed"] == 1, merged
        assert merged["company"] == "PSA" and merged["also_grades"] == ["9.0"] and merged["passes"] == 2, merged
        assert merged["started_at"] == "2026-10-17T01:30:00+00:00" and merged["finished_at"] == "2026-10-17T04:10:00+00:00"
        assert set(merged["shards"]) == {"shard-0", "shard-1", "shard-2"} and merged["shards"]["shard-1"]["unfinished"] is True
        assert merged["shards"]["shard-2"] == dict(scope=3, ok=0, failed=3, skipped=0, carried=0, passes=0, listings_asked=0, listings_failed=0,
                                                   psa9_listings_asked=0, psa9_listings_failed=0, all_grades_cards=0, all_grades_requests=0, unfinished=True), merged["shards"]["shard-2"]
        st = run_stats.load(out)
        assert st["day"] == "2026-10-17" and st["scrape"] == merged, "the run-level fields from `start` are kept"
        with contextlib.redirect_stdout(io.StringIO()):
            assert run_stats.gate(out) is False, "50 of 58 = 86%: the gate judges the assembled totals"
            assert run_stats.load(out)["gate"]["scraped_pct"] == 86.2
        # the CLI form
        r = subprocess.run([sys.executable, str(ROOT / "nightly" / "run_stats.py"), "merge", str(out)], capture_output=True, text=True)
        assert r.returncode == 0 and "3 shard(s) -> scraped 50/58" in r.stdout and "2 shard(s) unfinished" in r.stdout, r.stdout + r.stderr
    print("merge shards ok")


def test_gate():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        for name in ("NIGHTLY_MIN_SCRAPED_PCT", "NIGHTLY_MAX_LISTINGS_FAILED_PCT", "NIGHTLY_FANATICS_MAX_FAILED_NIGHTS"):
            os.environ.pop(name, None)
        with contextlib.redirect_stdout(io.StringIO()):
            run_stats.start(out, "2026-10-17", "top60")
            st = run_stats.load(out)
            assert st["day"] == "2026-10-17" and st["scope"] == "top60" and st["started_at"].endswith("+00:00"), st
            # defaults: 90% scraped, 20% listings failed
            run_stats.save(out, _stats()); assert run_stats.gate(out) is True
            assert run_stats.load(out)["gate"] == dict(ok=True, reasons=[], min_scraped_pct=90.0, max_listings_failed_pct=20.0, scraped_pct=100.0,
                                                       listings_failed_pct=0.0, psa9_listings_failed_pct=0.0, judged_at=run_stats.load(out)["gate"]["judged_at"])
            run_stats.save(out, _stats(ok=90)); assert run_stats.gate(out) is True, "90% is enough"
            run_stats.save(out, _stats(ok=89, failed=11)); assert run_stats.gate(out) is False
            assert "scraped ok 89/100 = 89.0% of the scope, under 90%" in run_stats.load(out)["gate"]["reasons"], run_stats.load(out)["gate"]
            run_stats.save(out, _stats(lf=20)); assert run_stats.gate(out) is True, "20% failed is still tolerated"
            run_stats.save(out, _stats(lf=21)); assert run_stats.gate(out) is False
            run_stats.save(out, _stats(l9f=13)); assert run_stats.gate(out) is False, "PSA 9 checks judged on their own (13/60)"
            run_stats.save(out, _stats(la=0, lf=0)); assert run_stats.gate(out) is True, "no listings asked (a run without checks) is not a failure"
            run_stats.save(out, _stats(scope=0, ok=0)); assert run_stats.gate(out) is False
            run_stats.save(out, {"day": "2026-10-17"}); assert run_stats.gate(out) is False, "no scrape section = the scraper never finished"
            # the knobs: arguments beat the environment beat the defaults
            run_stats.save(out, _stats(ok=85, lf=25)); assert run_stats.gate(out, 80, 30) is True
            os.environ["NIGHTLY_MIN_SCRAPED_PCT"], os.environ["NIGHTLY_MAX_LISTINGS_FAILED_PCT"] = "80", "30"
            try:
                assert run_stats.gate(out) is True and run_stats.load(out)["gate"]["min_scraped_pct"] == 80.0
            finally:
                del os.environ["NIGHTLY_MIN_SCRAPED_PCT"], os.environ["NIGHTLY_MAX_LISTINGS_FAILED_PCT"]
            assert run_stats.gate(out) is False
        # the CLI form, exit status
        run_stats.save(out, _stats())
        r = subprocess.run([sys.executable, str(ROOT / "nightly" / "run_stats.py"), "gate", str(out)], capture_output=True, text=True)
        assert r.returncode == 0 and "PASS" in r.stdout, r.stdout + r.stderr
        run_stats.save(out, _stats(ok=50, failed=50))
        r = subprocess.run([sys.executable, str(ROOT / "nightly" / "run_stats.py"), "gate", str(out)], capture_output=True, text=True)
        assert r.returncode == 1 and "FAIL" in r.stdout and "NOT committed" in r.stdout, r.stdout + r.stderr
    print("gate ok")


def test_finish_and_fanatics_health():
    with tempfile.TemporaryDirectory() as d:
        out, store = Path(d) / "run", Path(d) / "store"
        out.mkdir(); store.mkdir()
        U = "https://www.fanaticscollect.com/buy-now/"
        ingest.write_csv_atomic(store / "live_listings.csv", ingest.LIVE_COLS, [
            {"asset_id": "A", "grade": "10.0", "listing_type": "BUY_IT_NOW", "source": "Fanatics Collect", "url": U + "a", "via": "fanatics",
             "checked_at": "2026-10-14T02:10:00+00:00"},
            {"asset_id": "B", "grade": "10.0", "listing_type": "BUY_IT_NOW", "source": "Fanatics Collect", "url": U + "b", "via": "",
             "checked_at": "2026-10-16T02:10:00+00:00"},                 # alt.xyz's mirror row: not a direct pull
            {"asset_id": "C", "grade": "9.0", "listing_type": "BUY_IT_NOW", "source": "Fanatics Collect", "url": U + "c", "via": "fanatics",
             "checked_at": "2026-10-15T02:10:00+00:00"},
        ])
        assert run_stats.last_fanatics_pull(store) == "2026-10-15"
        with contextlib.redirect_stdout(io.StringIO()):
            # no Fanatics step in the run: finished_at only, health passes
            run_stats.save(out, _stats()); st = run_stats.finish(out, store)
            assert st["finished_at"].endswith("+00:00") and "fanatics" not in st and run_stats.fanatics_health(out) is True
            # tonight's pull ok
            run_stats.save(out, _stats(fanatics={"ok": True, "requests": 41, "budget": 80, "listings": 19000}))
            st = run_stats.finish(out, store)
            assert st["fanatics"]["last_ok"] == "2026-10-17" and st["fanatics"]["failed_nights"] == 0 and run_stats.fanatics_health(out) is True
            # failed tonight, last success 2 nights ago: under the default limit of 3
            fail = {"ok": False, "requests": 80, "budget": 80, "error": "FanaticsBudget: request budget of 80 used up before ..."}
            run_stats.save(out, _stats(fanatics=fail)); st = run_stats.finish(out, store)
            assert st["fanatics"]["last_ok"] == "2026-10-15" and st["fanatics"]["failed_nights"] == 2, st["fanatics"]
            assert run_stats.fanatics_health(out) is True and run_stats.fanatics_health(out, 2) is False
            os.environ["NIGHTLY_FANATICS_MAX_FAILED_NIGHTS"] = "1"
            try:
                assert run_stats.fanatics_health(out) is False
            finally:
                del os.environ["NIGHTLY_FANATICS_MAX_FAILED_NIGHTS"]
            # no successful pull on record at all (no direct rows): red at once
            ingest.write_csv_atomic(store / "live_listings.csv", ingest.LIVE_COLS, [])
            run_stats.save(out, _stats(fanatics=fail)); st = run_stats.finish(out, store)
            assert st["fanatics"]["last_ok"] == "" and st["fanatics"]["failed_nights"] is None and run_stats.fanatics_health(out) is False
        # CLI: a failed health check exits 1 and says why
        r = subprocess.run([sys.executable, str(ROOT / "nightly" / "run_stats.py"), "fanatics-health", str(out)], capture_output=True, text=True)
        assert r.returncode == 1 and "no successful direct pull is on record" in r.stdout, r.stdout + r.stderr
    print("finish + fanatics health ok")


def test_commit_message():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "summary.json"
        base = {"data_date": "2026-10-17", "assets": 67859, "psa10_sales": 3266473}
        p.write_text(json.dumps(base))
        assert run_stats.commit_message(p) == "nightly 2026-10-17: 67859 assets, 3266473 sales"
        assert run_stats.commit_message(p, "weekly full").startswith("weekly full 2026-10-17: 67859 assets")
        lr = _stats(scope=33700, ok=33512, failed=3, la=33512, lf=140, l9a=20000, l9f=9,
                    alt={"ok": True, "listings": 2100}, fanatics={"ok": True, "requests": 41, "budget": 80, "listings": 19529},
                    gate={"ok": True})
        p.write_text(json.dumps(dict(base, last_run=lr)))
        msg = run_stats.commit_message(p)
        assert msg == ("nightly 2026-10-17: 67859 assets, 3266473 sales; scrape 33512/33700 ok, 3 failed, 7 carried, listings 140/33512 failed, "
                       "PSA 9 listings 9/20000 failed, Alt 2100 listings, Fanatics 19529 listings in 41/80 requests"), msg
        assert msg.startswith("nightly 2026-10-17:"), "nightly/already_ran.sh matches on this start"
        lr["alt"], lr["fanatics"] = {"ok": False, "error": "x"}, {"ok": False, "requests": 80, "budget": 80, "error": "y"}
        p.write_text(json.dumps(dict(base, last_run=lr)))
        assert run_stats.commit_message(p).endswith("Alt pull FAILED, Fanatics pull FAILED (80/80 requests)"), run_stats.commit_message(p)
    print("commit message ok")


def test_ingest_copies_and_metrics_publishes():
    failed_once.clear()
    with tempfile.TemporaryDirectory() as d:
        store, run = Path(d) / "store", Path(d) / "run"
        (store / "daily").mkdir(parents=True)
        run_scraper(run)
        with contextlib.redirect_stdout(io.StringIO()):
            run_stats.start(run, "2026-10-17", "top60")
            run_stats.gate(run)
            run_stats.finish(run, store)
            out = ingest.ingest(run, "2026-10-17", store)
        assert out["last_run"] is True and (store / "last_run.json").exists()
        copied = json.load(open(store / "last_run.json"))
        assert copied == json.load(open(run / "run_stats.json")) and copied["scrape"]["ok"] == 8 and copied["gate"]["ok"] is False
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            summary = metrics.build(store, Path(d) / "latest")
        published = json.load(open(Path(d) / "latest" / "summary.json"))
        assert published["last_run"] == copied and summary["last_run"]["scrape"]["failed"] == 2
        assert published["generated_at"].endswith("+00:00") and len(published["generated_at"]) == 25, published["generated_at"]
        assert list(published)[-1] == "last_run", "appended after the existing keys"
        # a run without the file leaves the store's copy alone (and a store without one publishes no last_run)
        (store / "last_run.json").unlink()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            summary = metrics.build(store, Path(d) / "latest")
        assert "last_run" not in summary
        # the daily merge still picks the newest scraped_at when an older naive stamp sits next to a UTC one
        daily = {r["asset_id"]: r for r in _read(store / "daily" / "2026-10-17.csv")}
        assert all(v["scraped_at"].endswith("+00:00") for v in daily.values())
        assert "2026-10-16T06:50:05" < daily[IDS[0]]["scraped_at"], "a naive stamp from before the switch sorts older"
    print("ingest copy + metrics last_run ok")


if __name__ == "__main__":
    test_scraper_writes_run_stats_and_exits_2()
    test_merge_shards()
    test_gate()
    test_finish_and_fanatics_health()
    test_commit_message()
    test_ingest_copies_and_metrics_publishes()
    print("all run_stats checks passed")
