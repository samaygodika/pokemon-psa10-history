#!/usr/bin/env python3
"""Every printing TCGplayer has SKUs for, per product, kept in history/tcgplayer_sku_printings.csv
(2026-10-06) for the catalog's printings column.

Why: TCGCSV's prices files (tcgplayer_ids.py's first source) list a printing only while TCGplayer
has price data for it, so a print run nobody sells raw drops out: Neo Destiny Shining Mewtwo
(89167) shows only "Unlimited Holofoil" there, while TCGplayer sells it as "1st Edition Holofoil"
and "Unlimited Holofoil" (PokemonPriceTracker's printingsAvailable lists both, and PokeSniper's
card ids follow that list). TCGplayer's product page reads its SKUs from

    GET https://mp-search-api.tcgplayer.com/v2/product/<productId>/details   -> skus[].variant

(no key; `variant` uses the same names as TCGCSV's subTypeName). One request per product, so this
keeps a cache: a product is asked once and again only after REFRESH_DAYS. The first pass (~28k
products at ~1 request/second) runs in pieces under --budget-min; the nightly then only asks for
new products. Samay OK'd using this endpoint (2026-10-06).

    python3 history/tcgplayer_skus.py --tcgcsv snapshots/tcgcsv --budget-min 20
    python3 history/tcgplayer_skus.py --tcgcsv DIR --ids 89167,107005     # just these

Columns: product_id, printings (English SKU variants, sorted, '|'-joined; all languages when a
product has no English SKU; blank when it has no SKU at all), status (ok | not_found), fetched_at.

Standard library only.
"""
import argparse
import csv
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "tcgplayer_sku_printings.csv"
DETAILS = "https://mp-search-api.tcgplayer.com/v2/product/{}/details"
USER_AGENT = "pokemon-psa10-history/1.0 (school project; github.com/samaygodika/pokemon-psa10-history)"
COLS = ["product_id", "printings", "status", "fetched_at"]
REFRESH_DAYS = 90      # SKUs rarely change; re-ask a product after this long
SAVE_EVERY = 200


def load(path=CACHE):
    """{product_id (int): row} from the cache; empty when it doesn't exist yet."""
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {int(r["product_id"]): r for r in csv.DictReader(f)}


def save(rows, path=CACHE):
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(sorted(rows.values(), key=lambda r: int(r["product_id"])))
    os.replace(tmp, path)


def printings_of(details):
    """Sorted unique SKU variants, English first (all languages if there is no English SKU)."""
    skus = details.get("skus") or []
    english = {s["variant"] for s in skus if s.get("variant") and s.get("language") == "English"}
    every = {s["variant"] for s in skus if s.get("variant")}
    return "|".join(sorted(english or every))


def fetch_one(pid):
    """(printings, status) for one product. Raises on anything but a clean answer or a 404, so a
    blip is retried next run instead of being cached as 'no printings'."""
    req = urllib.request.Request(DETAILS.format(pid), headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return printings_of(json.loads(r.read())), "ok"
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "", "not_found"
        raise


def products(tcgcsv):
    """The catalog's product ids (singles: a number, or a rarity that isn't Code Card, as
    tcgplayer_ids.load_catalog keeps them), oldest set first: vintage is where the missing
    1st Edition printings are."""
    groups = json.loads((tcgcsv / "groups.json").read_text())["results"]
    # TCGplayer leaves ~19 old sets undated (POP Series, Nintendo Promos...) and TCGCSV stamps them
    # with its build time; they go first with the vintage sets, not last.
    built = (tcgcsv / "last-updated.txt").read_text()[:10] if (tcgcsv / "last-updated.txt").exists() else ""
    undated = lambda g: built and abs((datetime.fromisoformat(g["publishedOn"][:10])
                                       - datetime.fromisoformat(built)).days) <= 1
    groups.sort(key=lambda g: ("" if undated(g) else g.get("publishedOn") or "", g["groupId"]))
    out = []
    for g in groups:
        path = tcgcsv / f"{g['groupId']}.json"
        if not path.exists():
            continue
        for p in json.loads(path.read_text())["results"]:
            ext = {e.get("name"): e.get("value") for e in p.get("extendedData") or []}
            if ext.get("Number") or (ext.get("Rarity") and ext.get("Rarity") != "Code Card"):
                out.append(p["productId"])
    return out


def due(cache, pids, now):
    cutoff = (now - timedelta(days=REFRESH_DAYS)).isoformat(timespec="seconds")
    return [p for p in pids if p not in cache or (cache[p]["fetched_at"] or "") < cutoff]


def run(pids, cache_path=CACHE, budget_min=None, delay=1.0):
    cache = load(cache_path)
    now = datetime.now(timezone.utc)
    todo = due(cache, pids, now)
    print(f"tcgplayer skus: {len(pids)} products, {len(todo)} to ask (cache {len(cache)})", flush=True)
    stop = time.time() + budget_min * 60 if budget_min else None
    done = failed = 0
    for i, pid in enumerate(todo, 1):
        if stop and time.time() > stop:
            print(f"  time budget reached after {done} products; {len(todo) - done - failed} left for the next run")
            break
        try:
            printings, status = fetch_one(pid)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            failed += 1
            print(f"  {pid}: {e}", flush=True)
            if failed >= 20 and failed > done:      # the endpoint is down or refusing: stop, keep what we have
                print("  too many failures, stopping")
                break
            time.sleep(delay * 5)
            continue
        cache[pid] = {"product_id": pid, "printings": printings, "status": status,
                      "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        done += 1
        if done % SAVE_EVERY == 0:
            save(cache, cache_path)
            print(f"  {done}/{len(todo)} asked", flush=True)
        time.sleep(delay)
    save(cache, cache_path)
    print(f"tcgplayer skus: {done} asked, {failed} failed, cache {len(cache)} products -> {cache_path}")
    return {"asked": done, "failed": failed, "cached": len(cache)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tcgcsv", type=Path, help="a TCGCSV download (tcgplayer_ids.py --fetch): which products to ask")
    ap.add_argument("--ids", help="comma-separated product ids instead of --tcgcsv")
    ap.add_argument("--cache", type=Path, default=CACHE)
    ap.add_argument("--budget-min", type=float, help="stop after this many minutes (the rest waits for the next run)")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests (default 1)")
    ap.add_argument("--merge", type=Path, metavar="FILE",
                    help="merge another copy of the cache into --cache (newest fetched_at per product wins) and exit")
    a = ap.parse_args()
    if a.merge:
        cache = load(a.cache)
        for pid, r in load(a.merge).items():
            if pid not in cache or (r["fetched_at"] or "") > (cache[pid]["fetched_at"] or ""):
                cache[pid] = r
        save(cache, a.cache)
        print(f"tcgplayer skus: merged {a.merge} -> {a.cache} ({len(cache)} products)")
        sys.exit(0)
    if not (a.tcgcsv or a.ids):
        ap.error("give --tcgcsv DIR or --ids")
    pids = [int(x) for x in a.ids.split(",")] if a.ids else products(a.tcgcsv)
    run(pids, a.cache, a.budget_min, a.delay)
    sys.exit(0)
