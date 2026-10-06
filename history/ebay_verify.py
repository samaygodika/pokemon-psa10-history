#!/usr/bin/env python3
"""Ask eBay whether the feed's eBay Buy It Now listings are still live (2026-10-06).

alt.xyz re-serves ended eBay Buy It Nows for months and its listing record has no date
or status (a Kyogre Gold Star BIN shown as live on 2026-09-25 had ended on June 16), so
only eBay can say a listing is gone. This uses eBay's Browse API with an application
token (client-credentials grant, basic scope): one getItemByLegacyId lookup per listing.
The batch lookup (getItems) is for eBay partners only and refuses this keyset, so the
budget is the default 5,000 Browse calls a day (resets 07:00 UTC).

    python3 history/ebay_verify.py check --out DIR [--budget N] [--workers N]
    python3 history/ebay_verify.py apply DIR
    python3 history/ebay_verify.py check --out DIR --apply     # both, as the workflow runs it

check  picks up to --budget eBay BIN listings from history/live_listings.csv, asks eBay
       about each and writes DIR/ebay_results.csv. Nothing in history/ changes, so a run
       whose commit is rejected can re-apply the same answers on a newer store.
apply  folds DIR/ebay_results.csv into history/ebay_checks.csv and drops every BIN row
       of history/live_listings.csv whose listing eBay says has ended. ingest.py reads
       ebay_checks.csv too, so alt.xyz re-serving a dead listing never brings it back,
       and the card's next cheapest live BIN can take its place at the next check.

Which listings, within the budget (eBay item ids are never reused, so "ended" is final):
  1. never checked, on a card from 2013 or before (PokeSniper's default view), oldest
     first_seen first (the oldest are the likeliest to be dead);
  2. checked live more than RECHECK_DAYS ago, same cards, oldest check first;
  3. and 4. the same two for every other card.
Only Buy It Nows: an auction has an end time, the feed prunes ended ones, and the
listings refresh re-checks every card with a running auction three times a day.

Statuses in ebay_checks.csv:
  live   eBay returned the item and no itemEndDate before now
  ended  eBay returned the item with an itemEndDate before now (a pulled or sold listing
         eBay still serves, e.g. the Mewtwo Gold Star auction pulled 2026-09-25)
  gone   HTTP 404 with errorId 11003 "legacy item Id was not found" (a listing that ended
         long ago, e.g. that Kyogre BIN)
Anything else (rate limit, a multi-variation listing, a network error) records nothing:
the listing is simply asked again on a later day. HTTP 429 stops the run.

Keys: EBAY_CLIENT_ID / EBAY_CLIENT_SECRET (App ID / Cert ID of a production keyset) from
the environment, else ~/.ebay_keys (KEY=value lines). Without them `check` prints a line
and exits 0, so the workflow never fails over a missing key.

Standard library only, like the scraper."""
import argparse
import base64
import csv
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import ingest   # noqa: E402

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/get_item_by_legacy_id"
SCOPE = "https://api.ebay.com/oauth/api_scope"
DEFAULT_BUDGET = 4800          # of 5,000 a day: leaves room for a manual lookup
RECHECK_DAYS = 7               # a listing eBay called live is asked again after this
VINTAGE_MAX_YEAR = 2013
NOT_FOUND = 11003              # Browse API errorId: "The specified legacy item Id was not found."
DEAD = ("ended", "gone")
CHECK_COLS = ["item_id", "status", "checked_at", "end_date", "price", "first_dead_at"]
RESULT_COLS = ["item_id", "status", "checked_at", "end_date", "price"]
ITEM_RX = re.compile(r"ebay\.[a-z.]+/itm/(?:[^/?#]+/)?(\d{9,})")


def item_id(url):
    """The eBay legacy item id in a listing URL, or None for any other site."""
    m = ITEM_RX.search(url or "")
    return m.group(1) if m else None


def load_checks(store):
    path = store / "ebay_checks.csv"
    return {r["item_id"]: r for r in ingest.read_csv(path)} if path.exists() else {}


def dead_urls(store):
    """item ids eBay says have ended: ingest.py skips listings with these."""
    return {i for i, r in load_checks(store).items() if r["status"] in DEAD}


def load_keys():
    keys = {k: os.environ.get(k, "").strip() for k in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET")}
    f = Path(os.environ.get("EBAY_KEYS_FILE", str(Path.home() / ".ebay_keys")))
    if not all(keys.values()) and f.is_file():
        for ln in f.read_text().splitlines():
            k, sep, v = ln.partition("=")
            if sep and k.strip() in keys and not keys[k.strip()]:
                keys[k.strip()] = v.strip().strip("'\"")
    return keys if all(keys.values()) else None


def card_years(store):
    """asset_id -> year, from history/assets.csv."""
    path = store / "assets.csv"
    return {r["asset_id"]: r.get("year", "") for r in ingest.read_csv(path)} if path.exists() else {}


def pick(live_rows, checks, years, budget, now):
    """The eBay item ids to ask about this run, in priority order (see the module doc)."""
    due = (now - timedelta(days=RECHECK_DAYS)).isoformat()
    cand = {}
    for r in live_rows:
        if r["listing_type"] == "AUCTION":
            continue
        iid = item_id(r["url"])
        if not iid or iid in cand:
            continue
        c = checks.get(iid)
        if c and (c["status"] in DEAD or c["checked_at"] >= due):
            continue
        y = (years.get(r["asset_id"]) or "")[:4]
        vintage = y.isdigit() and int(y) <= VINTAGE_MAX_YEAR
        tier = (0 if vintage else 2) + (1 if c else 0)
        cand[iid] = (tier, c["checked_at"] if c else (r.get("first_seen") or "~"), iid)
    return [iid for iid, _ in sorted(cand.items(), key=lambda kv: kv[1])][:budget]


class Ebay:
    """Application token + item lookups. Thread-safe; the token lives 2 h, refreshed early."""

    def __init__(self, keys, urlopen=None):
        self.keys, self.lock = keys, threading.Lock()
        self.urlopen = urlopen or urllib.request.urlopen
        self.token, self.exp = None, 0.0

    def bearer(self):
        with self.lock:
            if not self.token or self.exp - 300 <= time.time():
                basic = base64.b64encode(f"{self.keys['EBAY_CLIENT_ID']}:{self.keys['EBAY_CLIENT_SECRET']}".encode()).decode()
                req = urllib.request.Request(TOKEN_URL, method="POST",
                                             data=urllib.parse.urlencode({"grant_type": "client_credentials", "scope": SCOPE}).encode(),
                                             headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
                try:
                    with self.urlopen(req, timeout=30) as r:
                        d = json.loads(r.read())
                except urllib.error.HTTPError as e:
                    raise RuntimeError(f"eBay token request failed: HTTP {e.code} {e.read().decode(errors='replace')[:200]} "
                                       "(a new keyset stays disabled until the Marketplace Account Deletion opt-out is done)") from None
                self.token, self.exp = d["access_token"], time.time() + float(d.get("expires_in", 7200))
            return self.token

    def lookup(self, iid, now):
        """(status, end_date, price) for one listing; status None = no answer this time.
        Raises RateLimited on HTTP 429."""
        req = urllib.request.Request(f"{ITEM_URL}?{urllib.parse.urlencode({'legacy_item_id': iid})}", headers={
            "Authorization": f"Bearer {self.bearer()}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US", "Accept": "application/json"})
        try:
            with self.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise RateLimited() from None
            if e.code == 404:
                try:
                    errs = json.loads(e.read()).get("errors") or []
                except ValueError:
                    errs = []
                if any(err.get("errorId") == NOT_FOUND for err in errs):
                    return "gone", "", ""
            return None, "", ""
        except (urllib.error.URLError, TimeoutError, ValueError):
            return None, "", ""
        end = d.get("itemEndDate") or ""
        price = (d.get("price") or {}).get("value") or (d.get("currentBidPrice") or {}).get("value") or ""
        ended = bool(end) and datetime.fromisoformat(end.replace("Z", "+00:00")) <= now
        return ("ended" if ended else "live"), end, price


class RateLimited(Exception):
    pass


def check(out, budget=DEFAULT_BUDGET, workers=4, store=HERE, urlopen=None, now=None):
    """Ask eBay about up to `budget` listings; write <out>/ebay_results.csv. Returns counts."""
    keys = load_keys()
    if not keys:
        print("ebay verify: no EBAY_CLIENT_ID / EBAY_CLIENT_SECRET (env or ~/.ebay_keys), nothing checked")
        return {"asked": 0}
    now = now or datetime.now(timezone.utc)
    live_rows = ingest.read_csv(store / "live_listings.csv")
    todo = pick(live_rows, load_checks(store), card_years(store), budget, now)
    ebay = Ebay(keys, urlopen)
    ebay.bearer()                        # fail fast on a bad keyset, before any lookup
    results, stop = [], threading.Event()
    stamp = now.isoformat(timespec="seconds")

    def one(iid):
        if stop.is_set():
            return None
        try:
            status, end, price = ebay.lookup(iid, now)
        except RateLimited:
            stop.set()
            return None
        return {"item_id": iid, "status": status, "checked_at": stamp, "end_date": end, "price": price} if status else None

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for res in ex.map(one, todo):
            if res:
                results.append(res)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    ingest.write_csv_atomic(out / "ebay_results.csv", RESULT_COLS, sorted(results, key=lambda r: r["item_id"]))
    counts = {s: sum(1 for r in results if r["status"] == s) for s in ("live", "ended", "gone")}
    print(f"ebay verify: {len(todo)} listing(s) picked, {len(results)} answered ({counts['live']} live, "
          f"{counts['ended']} ended, {counts['gone']} gone), {len(todo) - len(results)} no answer"
          + (" — stopped on eBay's rate limit" if stop.is_set() else ""))
    return {"asked": len(todo), "answered": len(results), **counts, "rate_limited": stop.is_set()}


def apply(out, store=HERE):
    """Fold <out>/ebay_results.csv into history/ebay_checks.csv; drop dead BIN rows from
    history/live_listings.csv. Idempotent: applying the same results twice changes nothing."""
    path = Path(out) / "ebay_results.csv"
    results = ingest.read_csv(path) if path.exists() else []
    checks = load_checks(store)
    for r in results:
        prev = checks.get(r["item_id"])
        if prev and prev["status"] in DEAD:
            continue                              # ended is final; keep the first verdict
        if prev and prev["checked_at"] > r["checked_at"]:
            continue                              # a newer answer is already on file
        row = {c: r.get(c, "") for c in CHECK_COLS}
        row["first_dead_at"] = r["checked_at"] if r["status"] in DEAD else ""
        checks[r["item_id"]] = row
    ingest.write_csv_atomic(store / "ebay_checks.csv", CHECK_COLS, sorted(checks.values(), key=lambda r: r["item_id"]))
    dead = {i for i, r in checks.items() if r["status"] in DEAD}
    live_path = store / "live_listings.csv"
    rows = ingest.read_csv(live_path)
    kept = [r for r in rows if r["listing_type"] == "AUCTION" or item_id(r["url"]) not in dead]
    ingest.write_csv_atomic(live_path, ingest.LIVE_COLS, kept)
    print(f"ebay verify apply: {len(results)} answer(s) folded into ebay_checks.csv ({len(checks)} listings on file, "
          f"{len(dead)} ended); {len(rows) - len(kept)} dead Buy It Now row(s) dropped from live_listings.csv")
    return {"dropped": len(rows) - len(kept), "on_file": len(checks), "dead": len(dead)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="ask eBay about the next listings in line")
    c.add_argument("--out", required=True, help="folder for ebay_results.csv")
    c.add_argument("--budget", type=int, default=int(os.environ.get("EBAY_BUDGET", DEFAULT_BUDGET)))
    c.add_argument("--workers", type=int, default=4)
    c.add_argument("--apply", action="store_true", help="also apply the results to history/")
    c.add_argument("--store", default=str(HERE))
    a = sub.add_parser("apply", help="fold a check's results into history/")
    a.add_argument("out")
    a.add_argument("--store", default=str(HERE))
    args = ap.parse_args()
    if args.cmd == "check":
        check(args.out, args.budget, args.workers, Path(args.store))
        if args.apply:
            apply(args.out, Path(args.store))
    else:
        apply(args.out, Path(args.store))


if __name__ == "__main__":
    main()
