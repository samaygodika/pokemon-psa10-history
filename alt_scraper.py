#!/usr/bin/env python3
"""
alt_scraper.py - pull PSA population counts and recent graded sale prices for
Pokemon cards from alt.xyz, using the same GraphQL API the website itself calls.

Usage
-----
  python3 alt_scraper.py cards.txt                 # one alt.xyz URL, ID, or search per line
  python3 alt_scraper.py https://alt.xyz/itm/<id>/external ...
  python3 alt_scraper.py --grade 9 cards.txt       # PSA 9 instead of PSA 10
  python3 alt_scraper.py --also-grade 9 cards.txt  # PSA 10 rows, plus PSA 9 sales in sales.csv
  python3 alt_scraper.py --company BGS --grade 9.5 cards.txt
  python3 alt_scraper.py --find "charizard base set"   # look up cards by name, print asset IDs
  python3 alt_scraper.py --list charizard              # write EVERY matching card to charizard_cards.txt
  python3 alt_scraper.py --list charizard --min-pop 100   # ...only cards with 100+ graded copies

Cards with zero copies at the chosen grade (e.g. no PSA 10 exists) are skipped, so every row
in cards.csv is a card you can actually buy in that grade. --keep-empty writes them anyway.

Accepted inputs (one per line, blank lines and # comments ignored):
  https://alt.xyz/itm/<uuid>/external     external (eBay etc.) listing page
  https://alt.xyz/itm/<uuid>              Alt item page
  <uuid>                                  listing id, item id, or asset id
  asset:<uuid>                            asset id (skips the id lookup, faster)
  search: 1999 Pokemon Base Set Charizard #4   top search hit is used (check the log!)

Outputs (written next to this script unless --out is given):
  cards.csv        one row per card: population + price summary
  sales.csv        one row per recorded sale at the chosen grade (and at any --also-grade)

Only the Python standard library is used.
"""

import argparse
import csv
import threading
from concurrent.futures import ThreadPoolExecutor
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

ENDPOINT = "https://alt-platform-server.production.internal.onlyalt.com/graphql/"
HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
    "allow-read-replica": "true",
    "authorization": "",
    "origin": "https://alt.xyz",
    "referer": "https://alt.xyz/",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
}
DELAY_SECONDS = 0.5   # polite pause between requests
RETRIES = 3

# --------------------------------------------------------------------------
# GraphQL documents (copied from the alt.xyz frontend bundle)
# --------------------------------------------------------------------------
FRAG_ASSET = """
fragment AssetBase on Asset {
  id name year subject category brand variety
  attributes { cardNumber printRun }
}
"""

Q_EXTERNAL_LISTING = """
query ExternalListing($id: ID!) {
  liveExternalTransaction(id: $id) {
    id
    asset { ...AssetBase }
    attributes { grade gradingCompany itemDetailUrl qualifier autograph }
    auctionHouse
    auctionInfo { endDate numBids highestBid }
    buyItNowPrice
  }
}
""" + FRAG_ASSET

Q_PUBLIC_ITEM = """
query PubliclyVisibleItem($id: ID!) {
  publiclyVisibleItem(id: $id) {
    id
    asset { ...AssetBase }
    attributes { gradeNumber gradingCompany certNumber }
  }
}
""" + FRAG_ASSET

Q_ASSET = """
query AssetInfo($id: ID!) { asset(id: $id) { ...AssetBase } }
""" + FRAG_ASSET

Q_POPS = """
query AssetCardPops($id: ID!) {
  asset(id: $id) { id cardPops { gradingCompany gradeNumber count } }
}
"""

Q_TRANSACTIONS = """
query AssetMarketTransactions($id: ID!, $marketTransactionFilter: MarketTransactionFilter!) {
  asset(id: $id) {
    id
    marketTransactions(marketTransactionFilter: $marketTransactionFilter) {
      id date auctionHouse auctionType price
      attributes { gradeNumber gradingCompany url autograph }
      subjectToChange consolidatedSkippedReason label
    }
  }
}
"""

Q_LIVE_LISTINGS = """
query AssetLiveExternalTransactions($id: ID!, $transactionsFilter: TransactionsFilter!) {
  asset(id: $id) {
    id
    liveExternalTransactions(transactionsFilter: $transactionsFilter) {
      id auctionHouse attributes { grade gradingCompany itemDetailUrl }
    }
  }
}
"""

Q_SEARCH_CONFIG = """
query SearchServiceConfig {
  serviceConfig { search { assetSearch {
    clientConfig { nodes { host port protocol } apiKey }
    collectionName expiresAt
  } } }
}
"""

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------
def gql(operation, query, variables):
    """POST one GraphQL operation. Returns the `data` dict, raises on errors."""
    body = json.dumps({"operationName": operation, "variables": variables, "query": query}).encode()
    last_err = None
    for attempt in range(1, RETRIES + 1):
        headers = dict(HEADERS, **{"idempotency-key": str(uuid.uuid4())})
        req = urllib.request.Request(ENDPOINT + operation, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read())
            time.sleep(DELAY_SECONDS)
            if payload.get("errors"):
                raise RuntimeError(payload["errors"][0].get("message", "unknown GraphQL error"))
            return payload.get("data") or {}
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            time.sleep(2 * attempt)
    raise RuntimeError(f"{operation} failed after {RETRIES} attempts: {last_err}")


# --------------------------------------------------------------------------
# Resolving whatever the user gave us into an asset (the card itself)
# --------------------------------------------------------------------------
def extract_id(text):
    m = UUID_RE.search(text)
    if not m:
        raise ValueError(f"no UUID found in {text!r}")
    return m.group(0).lower()


def resolve_asset(any_id):
    """Try listing id -> item id -> asset id. Returns (asset dict, listing info dict)."""
    listing = {}
    try:
        d = gql("ExternalListing", Q_EXTERNAL_LISTING, {"id": any_id})
        lt = d.get("liveExternalTransaction")
        if lt and lt.get("asset"):
            listing = {
                "listing_source": lt.get("auctionHouse"),
                "listing_grade": (lt.get("attributes") or {}).get("grade"),
                "listing_grading_company": (lt.get("attributes") or {}).get("gradingCompany"),
                "listing_price": lt.get("buyItNowPrice")
                or ((lt.get("auctionInfo") or {}).get("highestBid")),
                "listing_url": (lt.get("attributes") or {}).get("itemDetailUrl"),
            }
            return lt["asset"], listing
    except RuntimeError:
        pass

    try:
        d = gql("PubliclyVisibleItem", Q_PUBLIC_ITEM, {"id": any_id})
        it = d.get("publiclyVisibleItem")
        if it and it.get("asset"):
            attrs = it.get("attributes") or {}
            listing = {
                "listing_source": "Alt",
                "listing_grade": attrs.get("gradeNumber"),
                "listing_grading_company": attrs.get("gradingCompany"),
                "listing_price": None,
                "listing_url": f"https://alt.xyz/itm/{any_id}",
            }
            return it["asset"], listing
    except RuntimeError:
        pass

    d = gql("AssetInfo", Q_ASSET, {"id": any_id})
    if d.get("asset"):
        return d["asset"], listing
    raise RuntimeError(f"{any_id} is not a listing, item, or asset id that alt.xyz recognises")


# --------------------------------------------------------------------------
# Data pulls
# --------------------------------------------------------------------------
def fetch_pops(asset_id):
    d = gql("AssetCardPops", Q_POPS, {"id": asset_id})
    return (d.get("asset") or {}).get("cardPops") or []


def fetch_sales(asset_id, company, grade):
    flt = {"allGrades": False, "gradingCompany": company, "gradeNumber": grade, "showSkipped": True}
    d = gql("AssetMarketTransactions", Q_TRANSACTIONS, {"id": asset_id, "marketTransactionFilter": flt})
    return (d.get("asset") or {}).get("marketTransactions") or []


def research_url(asset_id):
    """alt.xyz's own page for the card (pops + full sales history). Needs a free account to view."""
    return f"https://alt.xyz/research/{asset_id}"


def fetch_public_page(asset_id, company, grade):
    """A public alt.xyz listing page for this card. It shows the same population table and
    recent sales as the research page but without logging in. Blank if nothing is listed."""
    for flt in ({"gradingCompany": company, "gradeNumber": grade}, {"gradingCompany": company}):
        try:
            d = gql("AssetLiveExternalTransactions", Q_LIVE_LISTINGS, {"id": asset_id, "transactionsFilter": flt})
        except RuntimeError:
            continue
        listings = (d.get("asset") or {}).get("liveExternalTransactions") or []
        if listings:
            return f"https://alt.xyz/itm/{listings[0]['id']}/external"
    return ""


def sale_url(tx):
    url = (tx.get("attributes") or {}).get("url") or ""
    if tx.get("auctionHouse") == "Alt" and UUID_RE.fullmatch(url):
        return f"https://alt.xyz/exchange/select-listing?ids={url}"
    return url


def normalise_grade(g):
    """'10' -> '10.0', '9.5' -> '9.5' (the API insists on one decimal place)."""
    return f"{float(g):.1f}"


def summarise(asset, pops, sales, company, grade, source_input, listing, public_url="", extra_grades=()):
    pop_by = {(p["gradingCompany"], p["gradeNumber"]): p["count"] for p in pops}
    company_total = sum(c for (co, _), c in pop_by.items() if co == company)
    # No rows at all for the chosen company means "alt.xyz has no <company> data for this
    # record" — not the same as a real 0 — so the two pop fields are written blank in that
    # case, and a reader can tell the two apart. (Found 2026-09-12: ~1,600 of the 6,400
    # English cards for the top-60 characters have CGC/BGS rows but no PSA rows at all,
    # e.g. the SWSH061 Shining Fates Pikachu V with 14,005 graded copies in the index.)
    pops_known = any(p["gradingCompany"] == company for p in pops)

    # Exclude sales Alt itself flags as skipped (outliers, bad data) from the stats.
    clean = [s for s in sales if s.get("price") and not s.get("consolidatedSkippedReason")]
    clean.sort(key=lambda s: s["date"], reverse=True)
    prices = [float(s["price"]) for s in clean]
    last = clean[0] if clean else None
    last3 = prices[:3]

    attrs = asset.get("attributes") or {}
    row = {
        "input": source_input,
        "asset_id": asset["id"],
        "card_name": asset.get("name"),
        "alt_url": research_url(asset["id"]),
        "alt_public_url": public_url,
        "year": asset.get("year"),
        "set": asset.get("brand"),
        "card_number": attrs.get("cardNumber"),
        "subject": asset.get("subject"),
        "variety": asset.get("variety"),
        "grading_company": company,
        "grade": grade,
        "pop_at_grade": pop_by.get((company, grade), 0) if pops_known else None,
        # PSA 9 pop rides along for free (same cardPops response); PSA 9 sales only
        # when --also-grade 9 asked for them, and extra_grades says so.
        "pop_at_grade_9": pop_by.get((company, "9.0"), 0) if pops_known else None,
        "extra_grades": " ".join(extra_grades),
        "company_total_pop": company_total if pops_known else None,
        "index_total_pop": asset.get("index_total_pop"),
        "index_transaction_count": asset.get("index_transaction_count"),
        "num_sales": len(clean),
        "last_sale_price": float(last["price"]) if last else None,
        "last_sale_date": last["date"] if last else None,
        "last_sale_source": last["auctionHouse"] if last else None,
        "avg_last_3_sales": round(sum(last3) / len(last3), 2) if last3 else None,
        "highest_sale": max(prices) if prices else None,
        "lowest_sale": min(prices) if prices else None,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
    }
    for k in LISTING_COLS:
        row[k] = listing.get(k)
    return row


CARD_COLS = ["input", "asset_id", "card_name", "alt_url", "alt_public_url", "year", "set", "card_number", "subject", "variety",
             "grading_company", "grade", "pop_at_grade", "company_total_pop", "index_total_pop", "index_transaction_count", "num_sales",
             "last_sale_price", "last_sale_date", "last_sale_source", "avg_last_3_sales",
             "highest_sale", "lowest_sale", "scraped_at", "pop_at_grade_9", "extra_grades"]
LISTING_COLS = ["listing_source", "listing_grade", "listing_grading_company", "listing_price", "listing_url"]
SALE_COLS = ["asset_id", "card_name", "alt_url", "date", "price", "grading_company", "grade", "source",
             "sale_type", "url", "label", "subject_to_change", "skipped_reason"]


def sale_rows(asset, sales, max_sales=None):
    sales = sorted(sales, key=lambda s: s.get("date") or "", reverse=True)
    if max_sales:
        sales = sales[:max_sales]
    for s in sales:
        a = s.get("attributes") or {}
        yield {
            "asset_id": asset["id"],
            "card_name": asset.get("name"),
            "alt_url": research_url(asset["id"]),
            "date": s.get("date"),
            "price": s.get("price"),
            "grading_company": a.get("gradingCompany"),
            "grade": a.get("gradeNumber"),
            "source": s.get("auctionHouse"),
            "sale_type": s.get("auctionType"),
            "url": sale_url(s),
            "label": s.get("label"),
            "subject_to_change": s.get("subjectToChange"),
            "skipped_reason": s.get("consolidatedSkippedReason"),
        }


# --------------------------------------------------------------------------
# Card search (alt.xyz hands out a short-lived, search-only key to its
# Typesense index; this is exactly what the site's search box does)
# --------------------------------------------------------------------------
_search_cfg = None


def get_search_config():
    global _search_cfg
    if _search_cfg and _search_cfg.get("expiresAt", 0) - 60 > time.time():
        return _search_cfg
    d = gql("SearchServiceConfig", Q_SEARCH_CONFIG, {})
    _search_cfg = d["serviceConfig"]["search"]["assetSearch"]
    return _search_cfg


def search_assets(text, limit=10, category="POKEMON_CARDS"):
    """Return matching asset documents (id, name, year, brand, cardNumber, pop, ...)."""
    cfg = get_search_config()
    node = cfg["clientConfig"]["nodes"][0]
    params = {"q": text, "query_by": "name,subject,brand,cardNumber", "per_page": limit}
    if category and category.upper() != "ALL":
        params["filter_by"] = f"category:={category.upper()}"
    url = (f"{node['protocol']}://{node['host']}:{node['port']}/collections/"
           f"{cfg['collectionName']}/documents/search?" + urllib.parse.urlencode(params))
    req = urllib.request.Request(url, headers={
        "X-TYPESENSE-API-KEY": cfg["clientConfig"]["apiKey"],
        "user-agent": HEADERS["user-agent"], "origin": "https://alt.xyz"})
    with urllib.request.urlopen(req, timeout=30) as r:
        hits = json.loads(r.read()).get("hits", [])
    time.sleep(DELAY_SECONDS)
    return [h["document"] for h in hits]


def list_assets(text, category="POKEMON_CARDS", page_size=250, loose=False, min_pop=0):
    """Every card whose subject (the Pokemon on the card) contains `text`.
    loose=True also accepts cards where only the set/name mentions it (deck-kit filler etc.)."""
    everything = text.strip() in ("*", "all")
    want = text.lower()
    out, seen, page = [], set(), 1
    while True:
        cfg = get_search_config()            # re-checked every page: the key only lives ~7 minutes
        node = cfg["clientConfig"]["nodes"][0]
        base = f"{node['protocol']}://{node['host']}:{node['port']}/collections/{cfg['collectionName']}/documents/search"
        params = {"q": "*" if everything else text, "query_by": "name,subject", "per_page": page_size,
                  "page": page, "num_typos": 0, "prefix": "false", "drop_tokens_threshold": 0,
                  "exhaustive_search": "true", "sort_by": "pop:desc"}
        filters = []
        if category and category.upper() != "ALL":
            filters.append(f"category:={category.upper()}")
        if min_pop:
            filters.append(f"pop:>={int(min_pop)}")
        if filters:
            params["filter_by"] = " && ".join(filters)
        req = urllib.request.Request(base + "?" + urllib.parse.urlencode(params), headers={
            "X-TYPESENSE-API-KEY": cfg["clientConfig"]["apiKey"],
            "user-agent": HEADERS["user-agent"], "origin": "https://alt.xyz"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 401:                # key expired under us: force a fresh one and retry the page
                global _search_cfg
                _search_cfg = None
                continue
            raise
        hits = data.get("hits", [])
        for h in hits:
            d = h["document"]
            if d["id"] in seen:
                continue
            hit = everything or want in (d.get("subject") or "").lower()
            if loose:
                hit = hit or want in (d.get("name") or "").lower()
            if hit:
                seen.add(d["id"]); out.append(d)
        if page == 1 or page % 10 == 0 or len(hits) < page_size:
            print(f"  page {page}: {len(out)} cards so far of {data.get('found')} in the index", flush=True)
        if len(hits) < page_size or page * page_size >= data.get("found", 0):
            break
        page += 1
        time.sleep(0.2)
    return out


def sidecar_path(list_path):
    return Path(list_path).with_suffix(".json")


def write_sidecar(list_path, docs):
    """Save the search documents next to the list so the scraper can skip the per-card info request."""
    keep = ("id", "name", "year", "subject", "category", "brand", "variety", "cardNumber", "pop", "externalTransactionCount")
    with open(sidecar_path(list_path), "w") as f:
        json.dump({d["id"]: {k: d.get(k) for k in keep} for d in docs}, f)


def asset_from_search_doc(doc):
    return {
        "id": doc["id"], "name": doc.get("name"), "year": doc.get("year"),
        "subject": doc.get("subject"), "category": doc.get("category"),
        "brand": doc.get("brand"), "variety": doc.get("variety"),
        "attributes": {"cardNumber": doc.get("cardNumber"), "printRun": None},
        # From the search index, not the per-card API: total graded copies across every
        # company and grade, and total recorded transactions. Kept because some records
        # have a large index count but an EMPTY per-card pop table (see summarise).
        "index_total_pop": doc.get("pop"), "index_transaction_count": doc.get("externalTransactionCount"),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
COMMENT_RE = re.compile(r"(^|\s)#(\s|$)")   # '#' at line start or surrounded by spaces


def strip_comment(line):
    """Drop '# comments' but keep card numbers like 'Charizard #4'."""
    m = COMMENT_RE.search(line)
    return (line[:m.start()] if m else line).strip()


def read_inputs(args):
    """Returns (items, sidecar) where sidecar maps asset id -> search document, if a
    <listfile>.json written by --list sits next to the list file."""
    items, sidecar = [], {}
    for a in args:
        p = Path(a)
        if p.is_file():
            for line in p.read_text().splitlines():
                line = strip_comment(line)
                if line:
                    items.append(line)
            sc = sidecar_path(p)
            if sc.exists():
                try:
                    sidecar.update(json.load(open(sc)))
                except (OSError, ValueError):
                    pass
        else:
            items.append(a)
    return items, sidecar


class CsvSink:
    """Append rows to a CSV as they arrive (so a crash mid-run keeps everything so far)."""

    def __init__(self, path, columns, resume):
        self.path, self.columns, self.count = Path(path), columns, 0
        exists = self.path.exists() and self.path.stat().st_size > 0
        self.f = open(self.path, "a" if (resume and exists) else "w", newline="")
        self.w = csv.DictWriter(self.f, fieldnames=columns, extrasaction="ignore")
        if not (resume and exists):
            self.w.writeheader()

    def write(self, rows):
        for r in rows:
            self.w.writerow(r); self.count += 1
        self.f.flush()

    def close(self):
        self.f.close()
        print(f"  {self.count} rows written this run -> {self.path}")


def already_done(path):
    """asset_ids present in an existing cards.csv (used by --resume)."""
    try:
        with open(path, newline="") as f:
            return {r["asset_id"] for r in csv.DictReader(f)}
    except (FileNotFoundError, KeyError):
        return set()


def main():
    global DELAY_SECONDS
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="*", help="alt.xyz URLs, IDs, 'search: ...' terms, or text files containing them")
    ap.add_argument("--find", metavar="TEXT", help="search for cards by name, print matches, and exit")
    ap.add_argument("--list", metavar="TEXT", help="write every card matching TEXT to <TEXT>_cards.txt and exit. "
                    "TEXT '*' means every card in the category")
    ap.add_argument("--include-ungraded", action="store_true",
                    help="with --list: also include cards no grading company has ever graded (they cannot have a PSA 10)")
    ap.add_argument("--workers", type=int, default=1, metavar="N", help="cards fetched in parallel. Default 1")
    ap.add_argument("--delay", type=float, default=DELAY_SECONDS, metavar="SEC",
                    help=f"pause after each request, per worker. Default {DELAY_SECONDS}")
    ap.add_argument("--company", default="PSA", help="grading company (PSA, BGS, CGC, SGC). Default PSA")
    ap.add_argument("--grade", default="10", help="grade to pull sales for. Default 10")
    ap.add_argument("--also-grade", action="append", default=[], metavar="G",
                    help="also pull sales at this grade (same company) into sales.csv, e.g. --also-grade 9. "
                         "One extra request per card; the card row's stats stay on --grade. Repeatable")
    ap.add_argument("--category", default="POKEMON_CARDS", help="search category filter, or ALL. Default POKEMON_CARDS")
    ap.add_argument("--loose", action="store_true", help="with --list: also keep cards that only mention TEXT in the set name")
    ap.add_argument("--min-pop", type=int, default=0, metavar="N",
                    help="with --list: only cards with a total graded population of at least N")
    ap.add_argument("--keep-empty", action="store_true",
                    help="also write cards that have zero copies at the chosen grade (skipped by default)")
    ap.add_argument("--resume", action="store_true", help="append to existing CSVs and skip cards already in cards.csv")
    ap.add_argument("--max-sales", type=int, default=200, metavar="N",
                    help="most recent sales per card kept in sales.csv (0 = all). Default 200. Summary stats always use all sales")
    ap.add_argument("--out", default=str(Path(__file__).parent), help="output directory")
    args = ap.parse_args()

    if args.find:
        hits = search_assets(args.find, limit=15, category=args.category)
        if not hits:
            print("no matches"); return
        print(f"{'asset id':42}  {'pop':>6}  name")
        for d in hits:
            print(f"asset:{d['id']:36}  {str(d.get('pop', '?')):>6}  {d.get('name')}")
        print("\nPaste an 'asset:...' line into cards.txt to scrape it.")
        return
    if args.list:
        min_pop = args.min_pop if (args.min_pop or args.include_ungraded) else 1   # 1 = graded by someone, not a rarity cutoff
        docs = list_assets(args.list, category=args.category, loose=args.loose, min_pop=min_pop)
        everything = args.list.strip() in ("*", "all")
        safe = "all_" + re.sub(r"_cards$", "", args.category.lower()) if everything else (re.sub(r"[^a-z0-9]+", "_", args.list.lower()).strip("_") or "list")
        Path(args.out).mkdir(parents=True, exist_ok=True)
        path = Path(args.out) / f"{safe}_cards.txt"
        with open(path, "w") as f:
            f.write(f"# every {args.category} card {'' if everything else 'matching ' + repr(args.list) + ' '}on alt.xyz, {datetime.now():%Y-%m-%d}"
                    + (", graded by at least one company" if min_pop == 1 else f", total graded pop >= {min_pop}" if min_pop else ", including ungraded") + "\n")
            f.write("# pop = total graded population across all companies, per the search index\n\n")
            for d in docs:
                f.write(f"asset:{d['id']}   # pop {d.get('pop', '?')}  {d.get('name')}\n")
        write_sidecar(path, docs)
        print(f"\n{len(docs)} cards -> {path}  (+ {sidecar_path(path).name} with card details)"
              f"\nScrape them with:  python3 alt_scraper.py {path.name}")
        return
    if not args.inputs:
        ap.error("give at least one URL / ID / file, or use --find")

    company = args.company.upper()
    grade = normalise_grade(args.grade)
    also_grades = [g for g in dict.fromkeys(normalise_grade(g) for g in args.also_grade) if g != grade]
    DELAY_SECONDS = max(0.0, args.delay)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    inputs, sidecar = read_inputs(args.inputs)
    n = len(inputs)
    print(f"{n} card(s) to fetch, {company} {grade}" + "".join(f" + {g} sales" for g in also_grades) + f", {args.workers} worker(s), {DELAY_SECONDS}s pause"
          + (f", {len(sidecar)} card details preloaded" if sidecar else "") + "\n")

    seen_assets = already_done(out / "cards.csv") if args.resume else set()
    if seen_assets:
        print(f"resuming: {len(seen_assets)} card(s) already in cards.csv will be skipped\n")
    lock = threading.Lock()
    cards = CsvSink(out / "cards.csv", CARD_COLS + LISTING_COLS, args.resume)
    sales_sink = CsvSink(out / "sales.csv", SALE_COLS, args.resume)
    counts = {"ok": 0, "skipped": 0, "done": 0, "failed": 0}

    def handle(i, raw):
        """Fetch one card. Runs in a worker thread; returns (status, message, row, sale rows)."""
        try:
            if raw.lower().startswith("search:"):
                text = raw.split(":", 1)[1].strip()
                hits = search_assets(text, limit=1, category=args.category)
                if not hits:
                    raise RuntimeError(f"no search results for {text!r}")
                asset, listing = asset_from_search_doc(hits[0]), {}
                header = f"[{i}/{n}] search {text!r}\n        -> {asset['name']}"
            elif raw.lower().startswith("asset:"):
                aid = extract_id(raw)
                with lock:
                    if aid in seen_assets:
                        return "done", None, None, []          # --resume: nothing to fetch
                if aid in sidecar:
                    asset, listing = asset_from_search_doc(sidecar[aid]), {}
                else:
                    asset, listing = (gql("AssetInfo", Q_ASSET, {"id": aid}).get("asset")), {}
                    if not asset:
                        raise RuntimeError(f"asset {aid} not found")
                header = f"[{i}/{n}] {asset['name']}"
            else:
                asset, listing = resolve_asset(extract_id(raw))
                header = f"[{i}/{n}] {asset['name']}"
            with lock:
                if asset["id"] in seen_assets:
                    return "done", header + "\n        (already done, skipped)", None, []
                seen_assets.add(asset["id"])
            pops = fetch_pops(asset["id"])
            pop_here = sum(p["count"] for p in pops
                           if p["gradingCompany"] == company and p["gradeNumber"] == grade)
            company_rows = any(p["gradingCompany"] == company for p in pops)
            if pop_here == 0 and company_rows and not args.keep_empty:
                return "skipped", header + f"\n        no {company} {grade} copies graded, skipped", None, []
            sales = fetch_sales(asset["id"], company, grade)
            public_url = fetch_public_page(asset["id"], company, grade)
            row = summarise(asset, pops, sales, company, grade, raw, listing, public_url, also_grades)
            extra_sales = []
            for g in also_grades:
                # a pop table that has this company's rows but no copies at g can't have sales at g
                if company_rows and not any(p["gradingCompany"] == company and p["gradeNumber"] == g and p["count"] for p in pops):
                    continue
                extra_sales += list(sale_rows(asset, fetch_sales(asset["id"], company, g), args.max_sales))
            pop_label = row["pop_at_grade"] if company_rows else f"unknown (no {company} rows in the pop table; index says {row['index_total_pop']} graded across all companies)"
            msg = (header + f"\n        {company} {grade} pop: {pop_label}   sales: {row['num_sales']}"
                   f"   last: ${row['last_sale_price']} on {row['last_sale_date']}")
            return "ok", msg, row, list(sale_rows(asset, sales, args.max_sales)) + extra_sales
        except Exception as e:  # keep going on a bad line
            return "failed", f"[{i}/{n}] FAILED {raw}: {e}", None, []

    def emit(result):
        status, msg, row, srows = result
        counts[status] += 1
        if msg:
            print(msg, file=sys.stderr if status == "failed" else sys.stdout, flush=True)
        if row is not None:
            cards.write([row])
            sales_sink.write(srows)

    if args.workers <= 1:
        for i, raw in enumerate(inputs, 1):
            emit(handle(i, raw))
    else:
        chunk = args.workers * 8
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for start in range(0, n, chunk):
                batch = list(enumerate(inputs[start:start + chunk], start + 1))
                for res in ex.map(lambda t: handle(*t), batch):
                    emit(res)
    skipped, failures = counts["skipped"], counts["failed"]

    print()
    cards.close()
    sales_sink.close()
    if skipped:
        print(f"  {skipped} card(s) skipped because no {company} {grade} copies exist")
    if failures:
        print(f"  {failures} card(s) failed, rerun with --resume to retry just those")


if __name__ == "__main__":
    main()
