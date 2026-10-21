#!/usr/bin/env python3
"""Integrity fields for latest/ (2026-10-17): the sha256, size and row count of every file
PokeSniper's server downloads, written into latest/summary.json under `files`, plus a
per-shard manifest for latest/series/, latest/recent_sales/ and latest/recent_sales_psa9/
(latest/clean_sales/manifest.json has had one since 2026-09-29; same shape).

    python3 history/manifest.py                 # latest/
    python3 history/manifest.py --latest DIR

Why: the server syncs by fetching summary.json and, when generated_at moved, cards.csv and
the other files by raw GitHub URL (samayRemote.js). raw.githubusercontent.com caches each URL
for about 5 minutes on its own clock, so an hourly check can pair a new summary.json with the
previous run's cards.csv, or one old shard, and believe it is current until the next nightly;
and a shard fetch that fails is cached by the app as "no data" for a day. With a hash per file
a reader can tell a stale, truncated or missing download from a current one and fetch again.

What is written
  summary.json["files"]   {path: {"sha256", "bytes", "rows"}} for cards.csv, characters.csv,
                          card_catalog.csv, tcgplayer_sets.csv, tcgplayer_ids.csv and, when the
                          watchlist sweep has produced it, watchlist_listings.csv; rows = data rows
                          (the header not counted, a quoted newline inside a field not a row).
                          Plus {path: {"sha256", "bytes", "entries"}} for the four manifests
                          (series/, recent_sales/, recent_sales_psa9/, clean_sales/), so a reader
                          can check a manifest download too. A file that does not exist is left
                          out. Every other summary.json key (generated_at, data_date, the counts)
                          is kept exactly as metrics.py wrote it.
  <dir>/manifest.json     {"columns": [...], "shards": {"00": {"sha256", "bytes", "rows"}, ...}}
                          for series/, recent_sales/ and recent_sales_psa9/, like clean_sales/'s
                          {"months": ...}. No timestamp: identical input gives identical bytes,
                          so a reader can keep its shards and refetch only the ones whose sha256
                          moved.

Runs LAST in every path that commits latest/ (nightly/run_nightly.sh, commit_results.sh's
re-derive loop, run_listings_refresh.sh, run_ebay_verify.sh): history/coverage.py and
history/tcgplayer_ids.py rewrite characters.csv and card_catalog.csv after metrics.py has
written summary.json, so hashing inside metrics.py would describe files about to change.
metrics.py alone therefore leaves `files` out, and a reader treats an absent `files` as
"not checked", never as "corrupt". Standard library only, like the rest of the feed.
"""
import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LATEST = HERE.parent / "latest"

# the CSVs the server downloads whole, in the order they appear in summary.json["files"]
FILES = ["cards.csv", "characters.csv", "card_catalog.csv", "tcgplayer_sets.csv", "tcgplayer_ids.csv",
         "watchlist_listings.csv", "all_grades/grades.csv", "all_grades/sales.csv"]   # the last two: history/all_grades.py (2026-10-21)
SHARD_DIRS = ["series", "recent_sales", "recent_sales_psa9"]      # one manifest.json each, written here
MANIFESTS = [f"{d}/manifest.json" for d in SHARD_DIRS] + ["clean_sales/manifest.json"]


def csv_rows(data):
    """Data rows in a CSV's bytes: what a CSV parser yields after the header (a quoted newline
    inside a field is not a row), so it matches what a reader counts after parsing."""
    text = data.decode("utf-8")
    n = sum(1 for _ in csv.reader(text.splitlines(keepends=True)))
    return max(n - 1, 0)


def file_entry(path):
    """{"sha256", "bytes", "rows"} of one CSV file."""
    data = Path(path).read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "rows": csv_rows(data)}


def manifest_entry(path, count):
    data = Path(path).read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "entries": count}


def write_shard_manifest(shard_dir):
    """<shard_dir>/manifest.json over its <xx>.csv files; returns the dict written. Columns come
    from the shards' own header line (every shard of a directory has the same)."""
    shard_dir = Path(shard_dir)
    shards, columns = {}, []
    for p in sorted(shard_dir.glob("*.csv")):
        data = p.read_bytes()
        if not columns:
            columns = data.decode("utf-8").split("\n", 1)[0].rstrip("\r").split(",")
        shards[p.stem] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "rows": csv_rows(data)}
    manifest = {"columns": columns, "shards": shards}
    with open(shard_dir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")
    return manifest


def build(latest=LATEST):
    """Write the shard manifests, then summary.json["files"]. Returns the files dict."""
    latest = Path(latest)
    summary_path = latest / "summary.json"
    if not summary_path.exists():
        sys.exit(f"{summary_path} missing: run history/metrics.py first")
    counts = {}
    for d in SHARD_DIRS:
        if (latest / d).is_dir():
            counts[f"{d}/manifest.json"] = len(write_shard_manifest(latest / d)["shards"])
    clean = latest / "clean_sales" / "manifest.json"
    if clean.exists():
        counts["clean_sales/manifest.json"] = len(json.loads(clean.read_text()).get("months", {}))
    files = {}
    for name in FILES:
        if (latest / name).exists():
            files[name] = file_entry(latest / name)
    for name in MANIFESTS:
        if name in counts:
            files[name] = manifest_entry(latest / name, counts[name])
    summary = json.loads(summary_path.read_text())
    summary["files"] = files
    tmp = summary_path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(summary, f, indent=2)
    tmp.replace(summary_path)
    return files


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--latest", type=Path, default=LATEST)
    a = ap.parse_args()
    files = build(a.latest)
    for name, e in files.items():
        print(f"  {name:<32} {e['sha256'][:12]}…  {e['bytes']:>12,} bytes  {e.get('rows', e.get('entries')):>9,} {'rows' if 'rows' in e else 'entries'}")
    print(f"manifest: {len(files)} file(s) hashed into {a.latest / 'summary.json'}")
