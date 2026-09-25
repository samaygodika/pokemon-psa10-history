#!/usr/bin/env python3
"""Fold one scrape run (a folder with cards.csv and sales.csv) into the
long-term history store.

    python3 history/ingest.py snapshots/2026-09-12            # day = folder name
    python3 history/ingest.py pokemon --date 2026-09-11       # day given explicitly

Store layout (all plain CSV, all append/merge-friendly so git diffs stay small):

    history/assets.csv            one row per alt.xyz asset: identity columns that
                                  never change (name, set, number, subject, ...),
                                  plus first_seen / last_seen run dates
    history/daily/<date>.csv      the numbers from that day's run(s), one row per
                                  asset (pop, totals, last sale, index counts,
                                  listing). Re-ingesting the same day merges by
                                  asset, newest scraped_at wins.
    history/sales/<YYYY-MM>.csv   every PSA 10 sale ever seen (plus PSA 9 sales for
                                  cards scraped with --also-grade 9; the grade
                                  column tells them apart), bucketed by sale
                                  month, deduplicated by sale URL (or by
                                  asset+date+price+source when there is no URL).
                                  Old months are never rewritten unless a scrape
                                  surfaces an older sale that was missing.
    history/live_listings.csv     what is for sale RIGHT NOW, not history: every
                                  live auction plus the cheapest Buy It Now
                                  listing per asset, from the run's listings.csv.
                                  An asset's rows are replaced whenever a run
                                  checked it (listings_checked_at set), so a card
                                  that sold out drops to zero rows; assets a run
                                  didn't check keep their older rows. That includes
                                  cards alt.xyz answered [] for while it was
                                  answering [] for everyone (the scraper's
                                  ListingsHealth blanks listings_checked_at for
                                  those), so the last believable snapshot stands.
                                  Auctions that ended before the run's newest check
                                  are pruned; a Buy It Now not re-seen for
                                  BIN_MAX_AGE_DAYS is dropped (alt.xyz keeps ended
                                  BINs in its feed for months). Other BIN listings
                                  stay in the run folder only, to keep the daily
                                  git diff small.

Standard library only, like the scraper.
"""
import argparse
import csv
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

IDENTITY_COLS = ["asset_id", "card_name", "alt_url", "year", "set", "card_number", "subject", "variety",
                 "grading_company", "grade"]
ASSET_COLS = IDENTITY_COLS + ["first_seen", "last_seen"]
DAILY_COLS = ["asset_id", "pop_at_grade", "company_total_pop", "index_total_pop", "index_transaction_count",
              "num_sales", "last_sale_price", "last_sale_date", "last_sale_source", "avg_last_3_sales",
              "highest_sale", "lowest_sale", "scraped_at", "alt_public_url",
              "listing_source", "listing_grade", "listing_grading_company", "listing_price", "listing_url",
              "pop_at_grade_9", "extra_grades", "listings_checked_at"]
SALE_COLS = ["asset_id", "date", "price", "grading_company", "grade", "source", "sale_type", "url",
             "label", "subject_to_change", "skipped_reason"]
LIVE_COLS = ["asset_id", "grading_company", "grade", "listing_type", "source", "current_bid", "bid_count", "end_date",
             "buy_it_now_price", "url", "checked_at"]
BIN_MAX_AGE_DAYS = 7   # a Buy It Now row survives this long without alt.xyz showing it again

DATE_RX = re.compile(r"\d{4}-\d{2}-\d{2}")


def read_csv(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv_atomic(path, cols, rows):
    """Write via a temp file in the same directory so a crash never leaves a
    half-written history file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.chmod(tmp, 0o644)  # mkstemp defaults to 0600
    os.replace(tmp, path)


def sale_key(s):
    return s.get("url") or f"{s['asset_id']}|{s.get('date')}|{s.get('price')}|{s.get('source')}"


def ingest(run_dir, day, store=HERE):
    run_dir = Path(run_dir)
    cards = read_csv(run_dir / "cards.csv")
    if not cards:
        sys.exit(f"no cards.csv in {run_dir}")
    print(f"ingesting {run_dir} as {day}: {len(cards)} card rows")

    # --- assets.csv: identity, merged ------------------------------------
    assets_path = store / "assets.csv"
    assets = {a["asset_id"]: a for a in read_csv(assets_path)}
    new_assets = 0
    for r in cards:
        a = assets.get(r["asset_id"])
        if a is None:
            a = {c: r.get(c, "") for c in IDENTITY_COLS}
            a["first_seen"] = day
            assets[r["asset_id"]] = a
            new_assets += 1
        else:
            # identity fields can be corrected on alt.xyz's side; take the newest
            for c in IDENTITY_COLS:
                if r.get(c):
                    a[c] = r[c]
        a["last_seen"] = max(a.get("last_seen") or "", day)
    write_csv_atomic(assets_path, ASSET_COLS, sorted(assets.values(), key=lambda a: a["asset_id"]))
    print(f"  assets.csv: {len(assets)} assets ({new_assets} new)")

    # --- daily/<day>.csv: numbers, merged newest-wins --------------------
    daily_path = store / "daily" / f"{day}.csv"
    daily = {d["asset_id"]: d for d in read_csv(daily_path)}
    replaced = 0
    for r in cards:
        row = {c: r.get(c, "") for c in DAILY_COLS}
        prev = daily.get(r["asset_id"])
        if prev is None or (row["scraped_at"] or "") >= (prev.get("scraped_at") or ""):
            if prev is not None:
                replaced += 1
            daily[r["asset_id"]] = row
    write_csv_atomic(daily_path, DAILY_COLS, sorted(daily.values(), key=lambda d: d["asset_id"]))
    print(f"  daily/{day}.csv: {len(daily)} rows ({replaced} replaced by a newer scrape of the same asset)")

    # --- sales/<month>.csv: dedup by URL, append only what's new ---------
    sales_dir = store / "sales"
    incoming = defaultdict(list)
    n_in = 0
    with open(run_dir / "sales.csv", newline="", encoding="utf-8") as f:
        for s in csv.DictReader(f):
            n_in += 1
            d = s.get("date") or ""
            if not DATE_RX.match(d):
                continue
            incoming[d[:7]].append({c: s.get(c, "") for c in SALE_COLS})
    added = 0
    for month in sorted(incoming):
        path = sales_dir / f"{month}.csv"
        existing = read_csv(path)
        seen = {sale_key(s) for s in existing}
        fresh = []
        for s in incoming[month]:
            k = sale_key(s)
            if k in seen:
                continue
            seen.add(k)
            fresh.append(s)
        if fresh:
            merged = existing + fresh
            merged.sort(key=lambda s: (s["date"], s["asset_id"], s["url"]))
            write_csv_atomic(path, SALE_COLS, merged)
            added += len(fresh)
    print(f"  sales: {n_in} rows in run, {added} new across {len(incoming)} month files")

    live = ingest_live(run_dir, cards, store)
    return {"assets": len(assets), "new_assets": new_assets, "daily_rows": len(daily), "sales_added": added, **live}


def to_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def ingest_live(run_dir, cards, store):
    """Fold the run's listings.csv into history/live_listings.csv (see the module doc)."""
    listings_path = run_dir / "listings.csv"
    if not listings_path.exists():          # a run from before 2026-09-24 has no listings
        print("  live_listings.csv: run has no listings.csv, left as is")
        return {"live_assets_checked": 0}
    checked = {r["asset_id"]: r["listings_checked_at"] for r in cards if r.get("listings_checked_at")}
    incoming = defaultdict(list)
    for r in read_csv(listings_path):
        if r["asset_id"] in checked:
            incoming[r["asset_id"]].append(r)

    live_path = store / "live_listings.csv"
    kept = [r for r in read_csv(live_path) if r["asset_id"] not in checked]
    fresh = []
    for aid, rows in incoming.items():
        fresh += [r for r in rows if r["listing_type"] == "AUCTION"]
        bins = [r for r in rows if r["listing_type"] != "AUCTION" and to_float(r["buy_it_now_price"])]
        if bins:
            fresh.append(min(bins, key=lambda r: to_float(r["buy_it_now_price"])))
    # An auction whose end is before the newest check anywhere in this run has certainly
    # ended; the rows of assets this run didn't check are where such leftovers live.
    horizon = max(checked.values(), default="")
    rows = [r for r in kept + fresh
            if not (r["listing_type"] == "AUCTION" and r["end_date"] and r["end_date"] < horizon)]   # both UTC ISO, same format
    pruned = len(kept) + len(fresh) - len(rows)
    # A Buy It Now has no end date, and alt.xyz keeps one in its live feed long after it
    # sold or was pulled (a Kyogre Gold Star BIN returned as live on 2026-09-25 had ended
    # on June 16). A card the scraper could not trust an answer for keeps its old rows
    # too. So a BIN not re-seen within BIN_MAX_AGE_DAYS of this run's newest check goes.
    aged = 0
    if horizon:
        cutoff = (datetime.fromisoformat(horizon) - timedelta(days=BIN_MAX_AGE_DAYS)).isoformat()
        before = len(rows)
        rows = [r for r in rows if r["listing_type"] == "AUCTION" or not r["checked_at"] or r["checked_at"] >= cutoff]
        aged = before - len(rows)
    rows.sort(key=lambda r: (r["asset_id"], r["listing_type"], r["end_date"] or "", r["url"] or ""))
    write_csv_atomic(live_path, LIVE_COLS, rows)
    n_auc = sum(1 for r in rows if r["listing_type"] == "AUCTION")
    print(f"  live_listings.csv: {len(checked)} assets checked this run, {len(rows)} rows "
          f"({n_auc} auctions, {len(rows) - n_auc} cheapest-BIN), {pruned} ended auctions pruned, "
          f"{aged} BIN(s) unseen for {BIN_MAX_AGE_DAYS}+ days dropped")
    return {"live_assets_checked": len(checked), "live_rows": len(rows), "bins_aged_out": aged}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", help="folder holding cards.csv and sales.csv from one scrape")
    ap.add_argument("--date", help="run date YYYY-MM-DD (default: taken from the folder name)")
    ap.add_argument("--store", default=str(HERE), help="history store directory (default: this folder)")
    args = ap.parse_args()
    day = args.date
    if not day:
        m = DATE_RX.search(Path(args.run_dir).resolve().name)
        if not m:
            sys.exit("folder name has no YYYY-MM-DD date; pass --date")
        day = m.group(0)
    ingest(args.run_dir, day, Path(args.store))


if __name__ == "__main__":
    main()
