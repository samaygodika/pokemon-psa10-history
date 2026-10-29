#!/usr/bin/env python3
"""Sales Sid flagged as bad in PokeSniper (2026-10-28, PR #1 issuecomment-6481523221): out of every
number the feed derives, like a sale alt.xyz removed (REMOVED, ingest.py).

The app's owner profile can flag a sale in any "last 10 sales" list (wrong card, wrong grade, lot,
shill or unpaid, other). PokeSniper exports the list to Sid's private backup repo as flagged_sales.csv
(asset_id, grade, date, price, url, reason, note, flagged_at), next to watchlist.csv, every 6 hours
when it changes.

    python3 history/flagged_sales.py fetch [--out DIR]
    python3 history/flagged_sales.py fetch --file PATH      # from a local copy

fetch    reads --file / FLAGGED_SALES_FILE, else FLAGGED_SALES_URL (+ FLAGGED_SALES_TOKEN), else the
         watchlist's own address with the file name swapped (WATCHLIST_URL + WATCHLIST_TOKEN: same
         repo, same token; .../contents/watchlist.csv -> .../contents/flagged_sales.csv). Writes
         history/flagged_sales.csv with the columns a match needs plus flagged_at, one row per flag,
         sorted: this repo is public, so Sid's reason and note never leave his repo. A fetch that
         fails (no address, an HTTP error such as 404 before his first export, a body without his
         header) keeps the file on record, so the flags already applied stay applied; a row that does
         not validate is skipped and counted. Unflagging in the app drops the row from his export, so
         the next fetch brings that sale back. With --out, DIR/flagged_sales.json gets the counts and
         a one-line note. nightly/run_ebay_verify.sh runs it first (07:15 UTC, the run that holds the
         watchlist secrets) and commits the file; every other run reads the committed copy.

Readers: metrics.load_sales and all_grades.load_sales give a matched sale the status FLAGGED (in
metrics.NOT_LISTED with REMOVED: never a clean sale, never the newest listed sale; recent_sales still
lists it, status FLAGGED), and buy_features.load_sales (the v0.2 buy list and its monthly fit) skips
it. So % change, cap_price, the weekly series, clean_sales, the last sale and the buy list leave it
out from the next run that rebuilds them.

Match (Sid's own, PokeSniper lib/flaggedSales.js matchIn): same asset, same grade (his "10" is our
"10.0"), same sale day, same price to the cent, and the same URL when both sides have one.

Standard library only, like the rest of the feed."""
import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILE = "flagged_sales.csv"
COLS = ["asset_id", "grade", "date", "price", "url", "flagged_at"]
NEEDED = ("asset_id", "grade", "date", "price")   # a body without these columns is not his file
FLAGGED = "FLAGGED"   # the status metrics.py / all_grades.py give a matched sale
DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def grade_key(g):
    """'10', '10.0' -> '10'; '8.5' -> '8.5'; None when not a number."""
    try:
        return f"{float(g):g}"
    except (TypeError, ValueError):
        return None


def cents(p):
    return round(float(p) * 100)


def clean_rows(rows):
    """Sid's rows -> (ours, n_bad): validated, COLS only, one row per flag, sorted."""
    out, bad = {}, 0
    for r in rows:
        aid = (r.get("asset_id") or "").strip()
        g = grade_key(r.get("grade"))
        day = (r.get("date") or "").strip()[:10]
        try:
            price = float(r.get("price") or "")
        except ValueError:
            price = 0
        if not aid or g is None or not DATE_RX.match(day) or not price > 0:
            bad += 1
            continue
        row = {"asset_id": aid, "grade": g, "date": day, "price": f"{cents(price) / 100:.2f}",
               "url": (r.get("url") or "").strip(), "flagged_at": (r.get("flagged_at") or "").strip()}
        out[(aid, g, day, row["price"], row["url"])] = row
    return [out[k] for k in sorted(out)], bad


def read_file(store=HERE):
    path = Path(store) / FILE
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load(store=HERE):
    """{asset_id: {grade_key: [(day, cents, url), ...]}} from history/flagged_sales.csv; {} without it."""
    index = {}
    for r in read_file(store):
        index.setdefault(r["asset_id"], {}).setdefault(r["grade"], []).append((r["date"], cents(r["price"]), r["url"]))
    return index


def match(index, aid, grade, day, price, url=""):
    """The flag (day, cents, url) this sale matches, or None. day: 'YYYY-MM-DD...' or a date."""
    by_grade = index.get(aid)
    if not by_grade:
        return None
    flags = by_grade.get(grade_key(grade))
    if not flags:
        return None
    day, c = str(day)[:10], cents(price)
    for f in flags:
        if f[0] == day and f[1] == c and (not f[2] or not url or f[2] == url):
            return f
    return None


def n_flags(index):
    return sum(len(v) for g in index.values() for v in g.values())


def sibling_url(url):
    """The watchlist's address with its file name swapped, or None when it does not end in watchlist.csv."""
    base, q, query = url.partition("?")
    head, _, last = base.rpartition("/")
    if last != "watchlist.csv":
        return None
    return f"{head}/{FILE}" + (q + query if q else "")


def source(file=None, env=os.environ):
    """('file', path, None) or ('url', url, token), or None when nothing says where Sid's file is."""
    file = file or env.get("FLAGGED_SALES_FILE")
    if file:
        return ("file", file, None)
    url, token = env.get("FLAGGED_SALES_URL"), env.get("FLAGGED_SALES_TOKEN")
    if not url and env.get("WATCHLIST_URL"):
        url, token = sibling_url(env["WATCHLIST_URL"]), token or env.get("WATCHLIST_TOKEN")
    return ("url", url, token) if url else None


def read_source(src, urlopen=urllib.request.urlopen):
    kind, where, token = src
    if kind == "file":
        return Path(where).read_text(encoding="utf-8")
    headers = {"User-Agent": "pokemon-psa10-history flagged sales"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if "api.github.com" in where:
        headers["Accept"] = "application/vnd.github.raw+json"
    with urlopen(urllib.request.Request(where, headers=headers), timeout=60) as r:
        return r.read().decode("utf-8")


def write(store, rows):
    path = Path(store) / FILE
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def fetch(store=HERE, src=None, urlopen=urllib.request.urlopen):
    """Sid's file -> history/flagged_sales.csv. Returns the summary dict (ok, rows, added, removed, bad, note)."""
    key = lambda r: (r["asset_id"], r["grade"], r["date"], r["price"], r["url"])
    before = {key(r) for r in read_file(store)}
    kept = f"the {len(before)} on record kept"
    if src is None:
        return {"ok": False, "rows": len(before), "note": f"flagged sales: no address (FLAGGED_SALES_URL or WATCHLIST_URL), {kept}"}
    try:
        text = read_source(src, urlopen)
    except urllib.error.HTTPError as e:
        return {"ok": False, "rows": len(before), "note": f"flagged sales: HTTP {e.code}, {kept}"}
    except (OSError, ValueError) as e:
        return {"ok": False, "rows": len(before), "note": f"flagged sales: fetch failed ({type(e).__name__}), {kept}"}
    reader = csv.DictReader(io.StringIO(text))
    if not set(NEEDED) <= set(reader.fieldnames or []):
        return {"ok": False, "rows": len(before), "note": f"flagged sales: not Sid's columns, {kept}"}
    rows, bad = clean_rows(reader)
    write(store, rows)
    after = {key(r) for r in rows}
    out = {"ok": True, "rows": len(rows), "added": len(after - before), "removed": len(before - after), "bad": bad}
    out["note"] = (f"flagged sales {len(rows)} ({out['added']} new, {out['removed']} unflagged"
                   + (f", {bad} bad row(s) skipped" if bad else "") + ")")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="Sid's flagged_sales.csv -> history/flagged_sales.csv")
    f.add_argument("--file", help="a local copy of Sid's file instead of the address in the environment")
    f.add_argument("--out", help="folder for flagged_sales.json (counts + note)")
    f.add_argument("--store", default=str(HERE))
    args = ap.parse_args()
    summary = fetch(Path(args.store), source(args.file))
    print(summary["note"])
    if args.out:
        Path(args.out, "flagged_sales.json").write_text(json.dumps(summary, indent=1) + "\n")


if __name__ == "__main__":
    main()
