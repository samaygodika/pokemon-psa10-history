#!/usr/bin/env python3
"""Checks for nightly/feed_health.py (2026-11-17): a tiny fake feed passes, and each way a feed can
go bad (stale nightly, a file that doesn't match summary.json, a missing shard, counts that fell,
missing grade_pops) is a PROBLEM; softer things are only warnings.

    python3 history/test_feed_health.py

Plain asserts, no pytest."""
import copy
import hashlib
import importlib.util
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("feed_health", ROOT / "nightly" / "feed_health.py")
fh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fh)

NOW = datetime(2026, 11, 17, 22, 30, tzinfo=timezone.utc)


def entry(path, rows=None):
    b = path.read_bytes()
    e = {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)}
    if rows is not None:
        e["rows"] = rows
    return e


def build(base):
    """A fake repo: base/latest (cards.csv, series/ with 2 shards + manifest) + base/history/grade_pops.csv."""
    latest = base / "latest"
    (latest / "series").mkdir(parents=True)
    (base / "history").mkdir()
    (latest / "cards.csv").write_text("asset_id,name\na,One\nb,Two\nc,\"Three, quoted\"\n")
    (latest / "series" / "00.csv").write_text("asset_id,week_start\na,2026-11-09\n")
    (latest / "series" / "01.csv").write_text("asset_id,week_start\nb,2026-11-09\nc,2026-11-09\n")
    manifest = {"columns": ["asset_id", "week_start"],
                "shards": {"00": entry(latest / "series" / "00.csv", 1), "01": entry(latest / "series" / "01.csv", 2)}}
    (latest / "series" / "manifest.json").write_text(json.dumps(manifest))
    (base / "history" / "grade_pops.csv").write_text("data_date,asset_id\n2026-11-17,a\n")
    summary = {
        "data_date": "2026-11-17", "generated_at": "2026-11-17T17:08:46+00:00",
        "assets": 3, "psa10_sales": 1000, "psa9_sales": 500, "series_rows": 3, "recent_sales_rows": 30,
        "psa9_recent_sales_rows": 20, "rows_with_bin_listing": 100, "rows_listings_checked": 3,
        "files": {"cards.csv": entry(latest / "cards.csv", 3),
                  "series/manifest.json": {**entry(latest / "series" / "manifest.json"), "entries": 2}},
        "last_run": {"day": "2026-11-17", "finished_at": "2026-11-17T15:03:27+00:00",
                     "gate": {"ok": True, "reasons": []},
                     "scrape": {"scope": 3, "ok": 3, "failed": 0},
                     "fanatics": {"ok": True, "failed_nights": 0, "last_ok": "2026-11-17"}},
    }
    (latest / "summary.json").write_text(json.dumps(summary))
    prev = copy.deepcopy(summary)
    prev.update(data_date="2026-11-16", assets=3, psa10_sales=990, psa9_sales=499)
    return latest, summary, prev


def run_case(mutate=None, prevs=None, now=NOW):
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        latest, summary, prev = build(base)
        if mutate:
            mutate(base, latest, summary, prev)
            (latest / "summary.json").write_text(json.dumps(summary))
        return fh.run(latest, [prev] if prevs is None else prevs, now)


def has(items, text):
    return any(text in x for x in items)


# 1. A healthy feed: no problems, no warnings, every listed file checked (cards + manifest + 2 shards).
p, w, facts = run_case()
assert p == [] and w == [], (p, w)
assert facts["files checked"] == 4 and facts["compared with"] == "2026-11-16", facts

# 2. Stale: judged 40 h after the nightly finished.
p, w, _ = run_case(now=datetime(2026, 11, 19, 7, 0, tzinfo=timezone.utc))
assert has(p, "the last nightly finished"), p
assert has(w, "nothing has updated the feed"), w

# 3. A shard rewritten after its manifest was made (sha + bytes + rows all off).
p, _, _ = run_case(lambda b, l, s, pr: (l / "series" / "01.csv").write_text("asset_id,week_start\nb,2026-11-09\n"))
assert has(p, "series/01.csv: sha256") and has(p, "series/01.csv: 1 rows"), p

# 4. A shard the manifest lists is gone.
p, _, _ = run_case(lambda b, l, s, pr: (l / "series" / "00.csv").unlink())
assert has(p, "series/00.csv: listed in the feed but missing"), p

# 5. cards.csv swapped without updating summary.json.
p, _, _ = run_case(lambda b, l, s, pr: (l / "cards.csv").write_text("asset_id,name\na,One\n"))
assert has(p, "cards.csv: sha256") and has(p, "cards.csv: 1 rows"), p

# 6. Sales fell 5% night over night -> problem; within 1% -> fine.
def big_drop(b, l, s, pr):
    s["psa10_sales"] = 940
p, _, _ = run_case(big_drop)
assert has(p, "psa10_sales fell"), p
def small_drop(b, l, s, pr):
    s["psa10_sales"] = 985   # 0.5% under prev's 990
p, _, _ = run_case(small_drop)
assert p == [], p

# 7. BIN rows fell 40%: only a warning.
def bin_drop(b, l, s, pr):
    s["rows_with_bin_listing"] = 60
p, w, _ = run_case(bin_drop)
assert p == [] and has(w, "rows_with_bin_listing fell"), (p, w)

# 8. cards.csv rows disagree with assets.
def assets_off(b, l, s, pr):
    s["assets"] = 4
p, _, _ = run_case(assets_off)
assert has(p, "cards.csv has 3 rows but summary.json says 4 assets"), p

# 9. grade_pops.csv missing -> problem; present but without today's rows -> warning.
p, _, _ = run_case(lambda b, l, s, pr: shutil.rmtree(b / "history"))
assert has(p, "grade_pops.csv is missing"), p
p, w, _ = run_case(lambda b, l, s, pr: (b / "history" / "grade_pops.csv").write_text("data_date,asset_id\n2026-11-16,a\n"))
assert p == [] and has(w, "no rows for 2026-11-17"), (p, w)

# 10. Baseline choice: same-day and future summaries are skipped; no baseline at all is a warning.
_, s0, _ = build(Path(tempfile.mkdtemp()))
older = {**s0, "data_date": "2026-11-15"}
newer = {**s0, "data_date": "2026-11-16"}
same = {**s0, "data_date": "2026-11-17"}
assert fh.pick_baseline(s0, [older, same, newer])["data_date"] == "2026-11-16"
assert fh.pick_baseline(s0, [same]) is None
p, w, _ = run_case(prevs=[])
assert p == [] and has(w, "no earlier nightly summary"), (p, w)

# 11. Last-run warnings: gate said no, Fanatics failing 2 nights, 10% scrape failed.
def bad_run(b, l, s, pr):
    s["last_run"]["gate"] = {"ok": False, "reasons": ["scraped 80% < 90%"]}
    s["last_run"]["fanatics"] = {"ok": False, "failed_nights": 2, "last_ok": "2026-11-15"}
    s["last_run"]["scrape"] = {"scope": 10, "ok": 9, "failed": 1}
p, w, _ = run_case(bad_run)
assert p == [] and has(w, "quality gate said no") and has(w, "Fanatics pull has failed 2") and has(w, "10.0% of the last"), (p, w)

# 12. No files block / no summary at all.
def no_files(b, l, s, pr):
    del s["files"]
p, _, _ = run_case(no_files)
assert has(p, "no files block"), p
with tempfile.TemporaryDirectory() as d:
    p, _, _ = fh.run(Path(d), [], NOW)
    assert has(p, "summary.json is missing"), p

# 13. main(): exit code and report file.
with tempfile.TemporaryDirectory() as d:
    latest, _, _ = build(Path(d))
    rep = Path(d) / "report.md"
    assert fh.main(["--root", str(latest), "--now", "2026-11-17T22:30:00+00:00", "--report", str(rep)]) == 0
    assert "Feed health: ok" in rep.read_text()
    assert fh.main(["--root", str(latest), "--now", "2026-11-20T00:00:00+00:00"]) == 1

print("test_feed_health: all checks passed")
