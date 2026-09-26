#!/usr/bin/env python3
"""Canary for alt.xyz's live-listings endpoint, which fails silently (returns [] for every
card for hours; see ListingsHealth in alt_scraper.py).

    python3 nightly/listings_canary.py                 # probe every 30 min until snapshots/canary/STOP exists
    python3 nightly/listings_canary.py --once          # one probe
    CANARY_INTERVAL_MIN=10 python3 nightly/listings_canary.py

Every probe asks for the PSA 10 and PSA 9 live listings of each card in
nightly/canary_assets.txt (cards that had a bid-carrying PSA 10 auction ending after
2026-09-27 12:00 UTC in the 09-25 feed, so a healthy endpoint should answer for them) and
appends one row per (card, grade) to snapshots/canary/listings_canary.csv, plus a
summary line to stdout. The point is the timeline: when the answers come back (a cache
cycle?), whether they ever come back from this machine while GitHub Actions gets them
(per-IP throttling?), and whether PSA 9 listings exist at all."""
import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import alt_scraper as s  # noqa: E402

OUT = ROOT / "snapshots" / "canary"
LOG = OUT / "listings_canary.csv"
COLS = ["probed_at", "asset_id", "grade", "status", "n_listings", "n_auctions", "n_bin",
        "first_auction_end", "first_auction_bids", "first_auction_bid"]


def read_assets():
    ids = []
    for line in (ROOT / "nightly" / "canary_assets.txt").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            ids.append(line)
    return ids


def probe(ids):
    OUT.mkdir(parents=True, exist_ok=True)
    new = not LOG.exists()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tot = {"10": [0, 0, 0], "9": [0, 0, 0]}   # answered-with-listings, empty, failed
    with open(LOG, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        for aid in ids:
            for g in ("10", "9"):
                ls = s.fetch_live_listings(aid, "PSA", s.normalise_grade(g))   # "10.0": the API answers [] for "10"
                row = {"probed_at": now, "asset_id": aid, "grade": g}
                if ls is None:
                    row["status"] = "failed"; tot[g][2] += 1
                else:
                    aucs = [lt for lt in ls if lt.get("auctionInfo")]
                    row.update(status="ok", n_listings=len(ls), n_auctions=len(aucs), n_bin=len(ls) - len(aucs))
                    if aucs:
                        a = min(aucs, key=lambda lt: lt["auctionInfo"].get("endDate") or "")["auctionInfo"]
                        row.update(first_auction_end=a.get("endDate"), first_auction_bids=a.get("numBids"), first_auction_bid=a.get("highestBid"))
                    tot[g][0 if ls else 1] += 1
                w.writerow(row)
    print(f"{now}  PSA10: {tot['10'][0]} with listings / {tot['10'][1]} empty / {tot['10'][2]} failed"
          f"   PSA9: {tot['9'][0]} with listings / {tot['9'][1]} empty / {tot['9'][2]} failed   (of {len(ids)} cards)", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    ids = read_assets()
    interval = 60 * float(os.environ.get("CANARY_INTERVAL_MIN", "30"))
    while True:
        probe(ids)
        if args.once or (OUT / "STOP").exists():
            return
        time.sleep(interval)


if __name__ == "__main__":
    main()
