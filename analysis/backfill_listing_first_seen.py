#!/usr/bin/env python3
"""One-off (2026-10-03): fill first_seen in history/live_listings.csv from the file's own
git history, so listings recorded before the column existed get the run day they first
appeared (earliest possible 2026-09-25, the first nightly with listings).

    python3 analysis/backfill_listing_first_seen.py          # rewrites history/live_listings.csv
    python3 analysis/backfill_listing_first_seen.py --check  # report only

Walks every commit that touched the file, oldest first; the run day is the date in the
commit subject ("nightly 2026-09-25: ...", "weekly full 2026-09-27: ..."). A listing is
(asset_id, grade, url). Rows already carrying a first_seen keep it."""
import csv
import io
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "history"))
import ingest   # noqa: E402

FILE = "history/live_listings.csv"
DATE_RX = re.compile(r"\d{4}-\d{2}-\d{2}")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def main():
    check = "--check" in sys.argv
    commits = [ln.split("|", 1) for ln in git("log", "--reverse", "--format=%H|%s", "--", FILE).splitlines()]
    first = {}
    for sha, subject in commits:
        m = DATE_RX.search(subject)
        if not m:
            print(f"skip {sha[:8]} (no date in subject: {subject!r})")
            continue
        day = m.group(0)
        n_new = 0
        for r in csv.DictReader(io.StringIO(git("show", f"{sha}:{FILE}"))):
            key = (r["asset_id"], ingest.norm_grade(r["grade"]), r["url"])
            if key not in first:
                first[key] = day
                n_new += 1
        print(f"{sha[:8]} {day}: {n_new} listings first seen")
    rows = ingest.read_csv(ROOT / FILE)
    filled = kept = missing = 0
    for r in rows:
        if r.get("first_seen"):
            kept += 1
            continue
        day = first.get((r["asset_id"], ingest.norm_grade(r["grade"]), r["url"]))
        if day:
            r["first_seen"] = day
            filled += 1
        else:
            r["first_seen"] = ""
            missing += 1
    print(f"{len(rows)} rows: {filled} filled, {kept} already set, {missing} not found in history")
    if check:
        return
    ingest.write_csv_atomic(ROOT / FILE, ingest.LIVE_COLS, rows)
    print(f"wrote {FILE}")


if __name__ == "__main__":
    main()
