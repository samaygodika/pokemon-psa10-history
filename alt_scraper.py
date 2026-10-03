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
  python3 alt_scraper.py --also-grade 9 --also-listings cards.txt  # ...and PSA 9 live listings in listings.csv
  python3 alt_scraper.py --company BGS --grade 9.5 cards.txt
  python3 alt_scraper.py --find "charizard base set"   # look up cards by name, print asset IDs
  python3 alt_scraper.py --list charizard              # write EVERY matching card to charizard_cards.txt
  python3 alt_scraper.py --list charizard --min-pop 100   # ...only cards with 100+ graded copies
  python3 alt_scraper.py --skip-unchanged-sales history/daily/2026-09-25.csv cards.txt
                                                   # don't refetch sales that cannot have changed since that run

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
  listings.csv     one row per listing live right now at the chosen grade (eBay / Fanatics
                   Collect / CardHobby via alt.xyz): Buy It Now price, or auction end time,
                   bid count and current bid. With --also-listings, also at each --also-grade
                   (the grade column tells them apart)

Login (optional): alt.xyz's pages ask for a free account since 2026-10-02. To scrape as that
account, put its stytch_session cookie value in ALT_SESSION_TOKEN or ~/.alt_session (README, 'Login').

Only the Python standard library is used.
"""

import argparse
import base64
import csv
import threading
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ENDPOINT = "https://alt-platform-server.production.internal.onlyalt.com/graphql/"
HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
    "allow-read-replica": "true",
    "authorization": "",
    "origin": "https://alt.xyz",
    "referer": "https://alt.xyz/",
    # Say who we are. Until 2026-10-01 this was a copied Chrome user-agent; on 2026-10-02
    # alt.xyz started refusing (HTTP 403 from its load balancer) script requests that claim
    # to be a browser, while the same request identifying itself honestly goes through.
    "user-agent": "pokemon-psa10-history/1.0 (school project; github.com/samaygodika/pokemon-psa10-history)",
}
DELAY_SECONDS = 0.5   # polite pause between requests
RETRIES = 3

# --------------------------------------------------------------------------
# Login (optional). On 2026-10-02 alt.xyz's pages started asking for a free account before
# showing market data ("Sign up for free. Access all of the market data for this asset with
# a free account"). The API itself still answered logged-out requests that day, so the
# token is not needed for the scrape to work; it is here so we can send requests the way a
# signed-in visitor does, with Samay's own account and alt.xyz's permission for the school
# project, and so a later server-side login check does not stop the feed again.
#
# How the site does it (read off its own requests on 2026-10-02): the login provider is
# Stytch. A long-lived session token sits in the `stytch_session` cookie; every API call
# sends `authorization: Bearer <session JWT>`, a JWT that lives 5 minutes. The site mints
# a fresh JWT by POSTing to Stytch's `sessions/authenticate` with
# `Authorization: Basic base64(<site public token>:<session token>)` and an empty body.
# We do exactly the same, nothing more.
#
# Where the session token comes from: the ALT_SESSION_TOKEN environment variable, else
# the first line of ~/.alt_session (chmod 600; in GitHub Actions it is the repo secret
# ALT_SESSION_TOKEN). Never commit it. Each refresh reports the session's expiry so a
# token that is about to lapse is noticed in the log, not as a silent 403.
# --------------------------------------------------------------------------
STYTCH_PUBLIC_TOKEN = os.environ.get("ALT_STYTCH_PUBLIC_TOKEN",
                                     "public-token-live-b46d695e-c943-4d41-809f-fe40b713fdc8")
STYTCH_AUTH_URL = "https://api.stytch.com/sdk/v1/sessions/authenticate"
SESSION_FILE = Path(os.environ.get("ALT_SESSION_FILE", str(Path.home() / ".alt_session")))
JWT_REFRESH_MARGIN = 60       # seconds before the JWT's exp at which we fetch a new one
LOGIN_HELP = ("alt.xyz refused the request (see README, 'Why a 403'). If the site now needs a login, put "
              "the account's stytch_session cookie value in the ALT_SESSION_TOKEN environment variable or in "
              f"{SESSION_FILE} (README, 'Login')")


class _Session:
    """Holds the session token and the short-lived JWT minted from it (thread-safe)."""

    def __init__(self):
        self.token = None          # resolved lazily so --help etc. never touch the file
        self.jwt = None
        self.jwt_exp = 0.0
        self.expires_at = None     # the session's own expiry, as Stytch reports it
        self.lock = threading.Lock()
        self._resolved = False

    def session_token(self):
        if not self._resolved:
            self._resolved = True
            tok = os.environ.get("ALT_SESSION_TOKEN", "").strip()
            if not tok and SESSION_FILE.is_file():
                lines = [ln.strip() for ln in SESSION_FILE.read_text().splitlines() if ln.strip()]
                tok = lines[0] if lines else ""
            self.token = tok or None
        return self.token

    def bearer(self, force=False):
        """The `authorization` header value, refreshing the JWT when it is stale. Empty
        string when no session token is configured (requests then go out logged out)."""
        if not self.session_token():
            return ""
        with self.lock:
            if force or not self.jwt or self.jwt_exp - JWT_REFRESH_MARGIN <= time.time():
                self._refresh()
            return f"Bearer {self.jwt}"

    def _refresh(self):
        basic = base64.b64encode(f"{STYTCH_PUBLIC_TOKEN}:{self.token}".encode()).decode()
        # The site's SDK also tags each call with who is calling (its own name + version);
        # same shape here, minus the browser-only telemetry ids.
        sdk_client = base64.b64encode(json.dumps({
            "event_id": f"event-id-{uuid.uuid4()}",
            "app_session_id": f"app-session-id-{uuid.uuid4()}",
            "client_sent_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "app": {"identifier": "alt.xyz"},
            "sdk": {"identifier": "Stytch.js Javascript SDK", "version": "5.40.0"},
        }).encode()).decode()
        req = urllib.request.Request(STYTCH_AUTH_URL, data=b"{}", method="POST", headers={
            "authorization": f"Basic {basic}",
            "content-type": "application/json",
            "x-sdk-client": sdk_client,
            "x-sdk-parent-host": "https://alt.xyz",
            "origin": HEADERS["origin"],       # Stytch checks the calling site; browsers send these two
            "referer": HEADERS["referer"],
            "user-agent": HEADERS["user-agent"],
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = (json.loads(resp.read()) or {}).get("data") or {}
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            raise RuntimeError(f"alt.xyz login refresh failed: HTTP {e.code} {body}. The session token "
                               f"is probably expired or wrong. {LOGIN_HELP}") from None
        jwt = data.get("session_jwt")
        if not jwt:
            raise RuntimeError(f"alt.xyz login refresh returned no session_jwt: {str(data)[:200]}")
        self.jwt = jwt
        self.jwt_exp = _jwt_exp(jwt) or time.time() + 300
        new_exp = (data.get("session") or {}).get("expires_at")
        if new_exp != self.expires_at:
            self.expires_at = new_exp
            print(f"alt.xyz login OK, session valid until {new_exp}", file=sys.stderr)


def _jwt_exp(jwt):
    """The exp claim of a JWT, or None. No signature check: we only use it to know when to refresh."""
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, ValueError, KeyError, TypeError):
        return None


SESSION = _Session()

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
      id auctionHouse buyItNowPrice
      auctionInfo { endDate numBids highestBid }
      attributes { grade gradingCompany itemDetailUrl }
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
    refreshed = False
    for attempt in range(1, RETRIES + 1):
        headers = dict(HEADERS, **{"idempotency-key": str(uuid.uuid4()), "authorization": SESSION.bearer()})
        req = urllib.request.Request(ENDPOINT + operation, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read())
            time.sleep(DELAY_SECONDS)
            if payload.get("errors"):
                raise RuntimeError(payload["errors"][0].get("message", "unknown GraphQL error"))
            return payload.get("data") or {}
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (401, 403):
                if not SESSION.session_token():
                    raise RuntimeError(f"{operation}: HTTP {e.code} with no login configured. {LOGIN_HELP}") from None
                if not refreshed:              # a stale JWT: mint a new one and go again, once
                    refreshed = True
                    SESSION.bearer(force=True)
                    continue
            time.sleep(2 * attempt)
        except (urllib.error.URLError, TimeoutError) as e:
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


def fetch_live_listings(asset_id, company, grade):
    """Everything listed for sale right now at this company + grade, as alt.xyz mirrors it
    from eBay / Fanatics Collect / CardHobby: Buy It Now listings with their price, and
    running auctions with end time, bid count and current high bid (checked against eBay's
    own page 2026-09-24: same bid, same count, same end minute). Returns None when the
    request fails, so "couldn't check" never reads as "nothing listed". The filter needs a
    grade: company alone (or no filter) returns an empty list."""
    try:
        d = gql("AssetLiveExternalTransactions", Q_LIVE_LISTINGS,
                {"id": asset_id, "transactionsFilter": {"gradingCompany": company, "gradeNumber": grade}})
    except RuntimeError:
        return None
    return (d.get("asset") or {}).get("liveExternalTransactions") or []


def public_page_url(listings):
    """A public alt.xyz listing page for this card. It shows the same population table and
    recent sales as the research page but without logging in. Blank if nothing is listed."""
    return f"https://alt.xyz/itm/{listings[0]['id']}/external" if listings else ""


def sale_url(tx):
    url = (tx.get("attributes") or {}).get("url") or ""
    if tx.get("auctionHouse") == "Alt" and UUID_RE.fullmatch(url):
        return f"https://alt.xyz/exchange/select-listing?ids={url}"
    return url


def normalise_grade(g):
    """'10' -> '10.0', '9.5' -> '9.5' (the API insists on one decimal place)."""
    return f"{float(g):.1f}"


def summarise(asset, pops, sales, company, grade, source_input, listing, public_url="", extra_grades=(), listings_checked_at=""):
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
        # 1 = this run asked alt.xyz for the card's sales; 0 = the seven summary columns above
        # were carried over from the previous run's daily row (--skip-unchanged-sales).
        "sales_fetched": 1,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        # UTC time the live listings were fetched (they go to listings.csv); blank when
        # that request failed, i.e. unknown, not "nothing listed".
        "listings_checked_at": listings_checked_at,
    }
    for g in extra_grades:
        row[also_listings_col(g)] = ""   # set by the caller once --also-listings has asked at that grade
    for k in LISTING_COLS:
        row[k] = listing.get(k)
    return row


CARD_COLS = ["input", "asset_id", "card_name", "alt_url", "alt_public_url", "year", "set", "card_number", "subject", "variety",
             "grading_company", "grade", "pop_at_grade", "company_total_pop", "index_total_pop", "index_transaction_count", "num_sales",
             "last_sale_price", "last_sale_date", "last_sale_source", "avg_last_3_sales",
             "highest_sale", "lowest_sale", "scraped_at", "pop_at_grade_9", "extra_grades", "listings_checked_at",
             "psa9_listings_checked_at", "sales_fetched"]
# The sale-summary columns of a card row: what --skip-unchanged-sales copies from the previous
# daily row when a card's sales are not refetched. Everything else in the row (pops, listing,
# index counts, scraped_at) is fresh either way.
SALE_SUMMARY_COLS = ["num_sales", "last_sale_price", "last_sale_date", "last_sale_source", "avg_last_3_sales",
                     "highest_sale", "lowest_sale"]
LISTING_COLS = ["listing_source", "listing_grade", "listing_grading_company", "listing_price", "listing_url"]
SALE_COLS = ["asset_id", "card_name", "alt_url", "date", "price", "grading_company", "grade", "source",
             "sale_type", "url", "label", "subject_to_change", "skipped_reason", "alt_tx_id"]
LIVE_COLS = ["asset_id", "grading_company", "grade", "listing_type", "source", "current_bid", "bid_count", "end_date",
             "buy_it_now_price", "url", "alt_listing_id", "checked_at"]


def also_listings_col(grade):
    """cards.csv column holding the UTC time of the live-listings check at an --also-grade
    grade (--also-listings): '9.0' -> 'psa9_listings_checked_at'. Only PSA 9's is in CARD_COLS,
    so a check at any other extra grade still fills listings.csv but leaves no time behind."""
    return f"psa{grade.rstrip('0').rstrip('.').replace('.', '_')}_listings_checked_at"


def live_rows(asset, listings, company, grade, checked_at):
    """One listings.csv row per live listing. listing_type is AUCTION when alt.xyz has auction
    info for it (current_bid = the high bid, or the opening price while bid_count is 0),
    otherwise BUY_IT_NOW. end_date is UTC."""
    for lt in listings:
        a = lt.get("attributes") or {}
        auc = lt.get("auctionInfo") or {}
        yield {
            "asset_id": asset["id"],
            "grading_company": a.get("gradingCompany") or company,
            "grade": a.get("grade") or grade,
            "listing_type": "AUCTION" if auc else "BUY_IT_NOW",
            "source": lt.get("auctionHouse"),
            "current_bid": auc.get("highestBid"),
            "bid_count": auc.get("numBids"),
            "end_date": auc.get("endDate"),
            "buy_it_now_price": lt.get("buyItNowPrice"),
            "url": a.get("itemDetailUrl"),
            "alt_listing_id": lt.get("id"),
            "checked_at": checked_at,
        }


class ListingsStats:
    """End-of-run sanity table for the live-listings answers: the share of cards with any
    listing, by PSA 10 pop.

    Lesson from 2026-09-25: the nightly's scope is sorted by pop, biggest first, so the
    share of cards with a listing falls from ~99% at the start of a run to ~0% at the end
    (pop 1000+: 99.7% have one; pop 1-2: 10%; pop 0: 1%). That is the cards, not the
    endpoint. Reading the fall as a silent outage and blanking those checks (5fd79ce)
    turned 14,828 real "checked, nothing listed" answers into "unknown" for a day; reverted
    2026-09-26. A real outage would show up here as a low rate in the high-pop rows, so
    the table is printed every run and nothing is gated on it."""

    BINS = ((0, 0), (1, 5), (6, 25), (26, 100), (101, 1000), (1001, float("inf")))

    def __init__(self):
        self.counts = {b: [0, 0] for b in self.BINS}   # pop bin -> [cards asked, cards with a listing]
        self.unknown_pop = [0, 0]
        self.failed = 0

    def record(self, pop, listings):
        try:
            pop = float(pop)
        except (TypeError, ValueError):
            c = self.unknown_pop
        else:
            c = next(self.counts[b] for b in self.BINS if b[0] <= pop <= b[1])
        c[0] += 1
        c[1] += 1 if listings else 0

    def summary(self):
        asked = sum(c[0] for c in self.counts.values()) + self.unknown_pop[0]
        lines = [f"  live listings: {asked} card(s) asked, {self.failed} request(s) failed (left unchecked); share with any listing by PSA 10 pop:"]
        for (lo, hi), (n, k) in self.counts.items():
            if n:
                lines.append(f"    pop {lo:>4}-{'inf' if hi == float('inf') else int(hi):<5} {n:6} cards  {100 * k / n:5.1f}%")
        if self.unknown_pop[0]:
            lines.append(f"    pop unknown  {self.unknown_pop[0]:6} cards  {100 * self.unknown_pop[1] / self.unknown_pop[0]:5.1f}%")
        return "\n".join(lines)


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
            # alt.xyz's own id for this sale record: the one exact key. A URL is not one
            # (multi-quantity eBay listings sell the same card many times under one item id),
            # and date / price / status get edited later. history/ingest.py matches on it.
            "alt_tx_id": s.get("id"),
        }


# --------------------------------------------------------------------------
# --skip-unchanged-sales: which cards' sale requests can be skipped
# --------------------------------------------------------------------------
def load_prev_daily(path):
    """{asset_id: row} of a history/daily/<date>.csv (or any cards.csv with the same
    columns): the previous run's numbers, for --skip-unchanged-sales."""
    with open(path, newline="", encoding="utf-8") as f:
        return {r["asset_id"]: r for r in csv.DictReader(f)}


def pop_count(pops, company, grade):
    return sum(p["count"] for p in pops if p["gradingCompany"] == company and p["gradeNumber"] == grade)


SKIP_INDEX, SKIP_DORMANT = "index count unchanged", "dormant"


def sales_skip_reason(prev, asset, pops, company, grade):
    """Why this card's sale requests can be skipped this run, or None to fetch them.

    Skipping means: no AssetMarketTransactions request at the main grade, nor at the
    --also-grade grades (except that a dormant card's extra grade is still fetched where
    the pop table shows copies at it, see handle); the row's SALE_SUMMARY_COLS copied from
    `prev`, the previous run's daily row for the asset; sales_fetched written as 0; and
    extra_grades listing only the grades this run asked for or knows to be empty, so
    metrics.py's psa9_scraped_date stays at the last real pull. Pops and live listings are
    fetched as usual. Two reasons, either is enough:

    SKIP_INDEX   the search index's per-asset transaction counter (index_transaction_count,
                 from the --list sidecar) is present in both runs and unchanged. Note that
                 as of 2026-09-25 alt.xyz's index does NOT carry this field: it is blank
                 for 33,760 of the 33,761 nightly-scope cards (one stray record has it),
                 so this clause is dormant until alt.xyz fills it. Blank is unknown, not
                 unchanged: a card with a blank counter is fetched.
    SKIP_DORMANT the previous run recorded no clean sale at this grade (num_sales 0) and
                 today's pop table shows no copies at it, either a real 0 or no rows for
                 the company at all. Measured over the four nightlies 2026-09-22..25
                 (33.7k cards each, new sale rows taken from the store's daily commits):
                 14,530-14,550 such cards per night (43%), 0 of the 10,886 new PSA 10
                 rows belonged to one, and the blank-pop ones (12.8k) produced 0 new PSA 9
                 rows on the normal nights (4 of 1.59M on the PSA 9 backfill night).
                 Cards whose pop says 0 but which have recorded sales (29 in the nightly
                 scope: split records, mislabeled lots) have num_sales > 0 and are fetched.

    The keys that were tried and rejected on the same data (the numbers are in the
    2026-09-26 report): "index_total_pop unchanged" skips 94% of cards but misses 51% of
    new PSA 10 rows; "pop_at_grade unchanged" 97% / 64%. New sales mostly arrive without
    a new graded copy, so population is no sales signal."""
    if not prev:
        return None
    cur = asset.get("index_transaction_count")
    if cur not in (None, "") and str(cur) == (prev.get("index_transaction_count") or ""):
        return SKIP_INDEX
    if pop_count(pops, company, grade) == 0 and (prev.get("num_sales") or "0") == "0":
        return SKIP_DORMANT
    return None


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


# Waits between attempts at one index page. Every run starts with the index listing, so
# one failed page used to end the whole run: the 2026-09-27 nightly died in its first
# minute on an HTTP 300 from alt.xyz that was gone by 14:32 (the weekly run got through).
# Per-card failures don't need this; the scrape step retries failed cards on its own.
INDEX_RETRY_WAITS = (30, 60, 120, 240, 300)   # ~12.5 min in all


def _index_page(params):
    """One page of the search index, with a fresh key whenever the cached one is refused."""
    global _search_cfg
    while True:
        cfg = get_search_config()            # re-checked every page: the key only lives ~7 minutes
        node = cfg["clientConfig"]["nodes"][0]
        base = f"{node['protocol']}://{node['host']}:{node['port']}/collections/{cfg['collectionName']}/documents/search"
        req = urllib.request.Request(base + "?" + urllib.parse.urlencode(params), headers={
            "X-TYPESENSE-API-KEY": cfg["clientConfig"]["apiKey"],
            "user-agent": HEADERS["user-agent"], "origin": "https://alt.xyz"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code != 401:
                raise
            _search_cfg = None               # key expired under us: force a fresh one and retry the page


def list_assets(text, category="POKEMON_CARDS", page_size=250, loose=False, min_pop=0):
    """Every card whose subject (the Pokemon on the card) contains `text`.
    loose=True also accepts cards where only the set/name mentions it (deck-kit filler etc.)."""
    everything = text.strip() in ("*", "all")
    want = text.lower()
    out, seen, page = [], set(), 1
    while True:
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
        for wait in INDEX_RETRY_WAITS + (None,):
            try:
                data = _index_page(params)
                break
            except (RuntimeError, urllib.error.URLError, TimeoutError, ValueError) as e:
                if wait is None:
                    raise
                print(f"  page {page}: {e}; retrying in {wait}s", flush=True)
                time.sleep(wait)
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
    ap.add_argument("--also-listings", action="store_true",
                    help="with --also-grade: also fetch the live listings at each of those grades into listings.csv "
                         "(one more request per card that has copies at the grade) and record the check time in "
                         "psa9_listings_checked_at. Off by default")
    ap.add_argument("--listings-only", action="store_true",
                    help="fetch only the live listings at --grade (no pops, no sales): one request per card, for the "
                         "listings refresh between nightlies (nightly/run_listings_refresh.sh). cards.csv then carries "
                         "the card's identity and the check time in the column for that grade (listings_checked_at "
                         "for PSA 10, psa9_listings_checked_at for PSA 9) and nothing else; ingest it with "
                         "history/ingest.py --listings-only. Not combinable with --also-grade")
    ap.add_argument("--category", default="POKEMON_CARDS", help="search category filter, or ALL. Default POKEMON_CARDS")
    ap.add_argument("--loose", action="store_true", help="with --list: also keep cards that only mention TEXT in the set name")
    ap.add_argument("--min-pop", type=int, default=0, metavar="N",
                    help="with --list: only cards with a total graded population of at least N")
    ap.add_argument("--keep-empty", action="store_true",
                    help="also write cards that have zero copies at the chosen grade (skipped by default)")
    ap.add_argument("--resume", action="store_true", help="append to existing CSVs and skip cards already in cards.csv")
    ap.add_argument("--max-sales", type=int, default=200, metavar="N",
                    help="most recent sales per card kept in sales.csv (0 = all). Default 200. Summary stats always use all sales")
    ap.add_argument("--skip-unchanged-sales", metavar="PREV_DAILY_CSV",
                    help="skip the sale requests of cards whose sales cannot have changed since the run recorded in "
                         "PREV_DAILY_CSV (a history/daily/<date>.csv): their sale summary is copied from that file, "
                         "sales_fetched is written as 0 and no sales.csv rows are written for the grades not asked. Pops "
                         "and live listings are fetched as usual. Which cards qualify: see sales_skip_reason")
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
    if args.listings_only and (also_grades or args.skip_unchanged_sales):
        ap.error("--listings-only fetches one grade's listings and nothing else; drop --also-grade / --skip-unchanged-sales")
    if args.listings_only and grade != "10.0" and also_listings_col(grade) not in CARD_COLS:
        ap.error(f"--listings-only: cards.csv has no check-time column for {company} {grade} (only 10 and 9)")
    DELAY_SECONDS = max(0.0, args.delay)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    inputs, sidecar = read_inputs(args.inputs)
    n = len(inputs)
    print(f"{n} card(s) to fetch, {company} {grade}" + (" live listings only" if args.listings_only else "")
          + "".join(f" + {g} sales{' and listings' if args.also_listings else ''}" for g in also_grades)
          + f", {args.workers} worker(s), {DELAY_SECONDS}s pause"
          + (f", {len(sidecar)} card details preloaded" if sidecar else "") + "\n")
    prev_daily = None
    if args.skip_unchanged_sales:
        prev_daily = load_prev_daily(args.skip_unchanged_sales)
        print(f"--skip-unchanged-sales: {len(prev_daily)} card(s) in {args.skip_unchanged_sales}; sales that cannot have changed since then are not refetched\n")

    seen_assets = already_done(out / "cards.csv") if args.resume else set()
    if seen_assets:
        print(f"resuming: {len(seen_assets)} card(s) already in cards.csv will be skipped\n")
    lock = threading.Lock()
    cards = CsvSink(out / "cards.csv", CARD_COLS + LISTING_COLS, args.resume)
    sales_sink = CsvSink(out / "sales.csv", SALE_COLS, args.resume)
    live_sink = CsvSink(out / "listings.csv", LIVE_COLS, args.resume)
    counts = {"ok": 0, "skipped": 0, "done": 0, "failed": 0}
    carried = {SKIP_INDEX: 0, SKIP_DORMANT: 0}   # cards whose sales were not refetched, by reason

    def handle(i, raw):
        """Fetch one card. Runs in a worker thread; returns (status, message, row, sale rows, listing rows,
        listing rows at the --also-grade grades)."""
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
                        return "done", None, None, [], [], []  # --resume: nothing to fetch
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
                    return "done", header + "\n        (already done, skipped)", None, [], [], []
                seen_assets.add(asset["id"])
            if args.listings_only:
                # The refresh: today's listings at one grade, nothing else. The row keeps the card's
                # identity; pops and sales are blank (unknown), never 0. The check time goes in the
                # column for this grade, so ingest --listings-only replaces the right rows.
                listings = fetch_live_listings(asset["id"], company, grade)
                checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds") if listings is not None else ""
                row = summarise(asset, [], [], company, grade, raw, listing, public_page_url(listings), (), checked_at)
                if grade != "10.0":
                    row["listings_checked_at"] = ""
                    row[also_listings_col(grade)] = checked_at
                lrows = list(live_rows(asset, listings or [], company, grade, checked_at))
                msg = header + f"\n        {company} {grade} listings: " + (f"{len(lrows)}" if listings is not None else "request failed")
                return "ok", msg, row, [], lrows, []
            pops = fetch_pops(asset["id"])
            pop_here = pop_count(pops, company, grade)
            company_rows = any(p["gradingCompany"] == company for p in pops)
            if pop_here == 0 and company_rows and not args.keep_empty:
                return "skipped", header + f"\n        no {company} {grade} copies graded, skipped", None, [], [], []
            # --skip-unchanged-sales: the previous run's row for this card, and whether it
            # settles the sales question without a request (None = fetch as usual)
            prev = prev_daily.get(asset["id"]) if prev_daily else None
            carry = sales_skip_reason(prev, asset, pops, company, grade) if prev else None
            sales = [] if carry else fetch_sales(asset["id"], company, grade)
            listings = fetch_live_listings(asset["id"], company, grade)
            checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds") if listings is not None else ""
            extra_sales, extra_lrows, pulled = [], [], []   # pulled: the extra grades this run can vouch for (fetched, or a known zero)
            also_checked = {}                                # grade -> check time of its --also-listings request
            for g in also_grades:
                copies = pop_count(pops, company, g) > 0
                # a pop table that has this company's rows but no copies at g can't have sales or listings at g
                if company_rows and not copies:
                    pulled.append(g)
                    continue
                if args.also_listings:
                    # Live listings are today's state, not history, so they are fetched even when the
                    # card's sales are carried. Checked whenever the request succeeded (an empty answer
                    # is "nothing listed at this grade"); blank when it failed or the card was skipped above.
                    more = fetch_live_listings(asset["id"], company, g)
                    also_checked[g] = datetime.now(timezone.utc).isoformat(timespec="seconds") if more is not None else ""
                    extra_lrows += list(live_rows(asset, more or [], company, g, also_checked[g]))
                # a carried card's extra grades stay with the previous run too, except that a
                # dormant card (no copies at the main grade) may well have copies, and sales, at g
                if carry and not (carry == SKIP_DORMANT and copies):
                    continue
                pulled.append(g)
                extra_sales += list(sale_rows(asset, fetch_sales(asset["id"], company, g), args.max_sales))
            row = summarise(asset, pops, sales, company, grade, raw, listing, public_page_url(listings), pulled, checked_at)
            for g, checked_g in also_checked.items():
                row[also_listings_col(g)] = checked_g
            if carry:
                for k in SALE_SUMMARY_COLS:
                    row[k] = prev.get(k, "")
                row["sales_fetched"] = 0
                with lock:
                    carried[carry] += 1
            lrows = list(live_rows(asset, listings or [], company, grade, checked_at))
            pop_label = row["pop_at_grade"] if company_rows else f"unknown (no {company} rows in the pop table; index says {row['index_total_pop']} graded across all companies)"
            msg = (header + f"\n        {company} {grade} pop: {pop_label}   sales: {row['num_sales']}"
                   f"   last: ${row['last_sale_price']} on {row['last_sale_date']}"
                   + (f"   (sales not refetched: {carry}; summary carried from the previous run)" if carry else ""))
            return "ok", msg, row, list(sale_rows(asset, sales, args.max_sales)) + extra_sales, lrows, extra_lrows
        except Exception as e:  # keep going on a bad line
            return "failed", f"[{i}/{n}] FAILED {raw}: {e}", None, [], [], []

    stats = ListingsStats()

    def emit(result):
        status, msg, row, srows, lrows, extra_lrows = result
        counts[status] += 1
        if msg:
            print(msg, file=sys.stderr if status == "failed" else sys.stdout, flush=True)
        if row is not None:
            check_col = also_listings_col(grade) if (args.listings_only and grade != "10.0") else "listings_checked_at"
            if row[check_col]:
                stats.record(row["pop_at_grade"], lrows)
            else:
                stats.failed += 1     # the listings request failed: unchecked, not "nothing listed"
        if row is not None:
            cards.write([row])
            sales_sink.write(srows)
            live_sink.write(lrows)
            live_sink.write(extra_lrows)   # the --also-listings rows (PSA 9), written as answered

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
    live_sink.close()
    if counts["ok"]:
        print(stats.summary())
    if prev_daily is not None:
        n_carried = sum(carried.values())
        print(f"  --skip-unchanged-sales: {n_carried} card(s) kept the sale summary of {args.skip_unchanged_sales} "
              f"({carried[SKIP_INDEX]} {SKIP_INDEX}, {carried[SKIP_DORMANT]} {SKIP_DORMANT}); "
              f"{counts['ok'] - n_carried} card(s) had their sales fetched")
    if skipped:
        print(f"  {skipped} card(s) skipped because no {company} {grade} copies exist")
    if failures:
        print(f"  {failures} card(s) failed, rerun with --resume to retry just those")


if __name__ == "__main__":
    main()
