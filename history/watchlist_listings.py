#!/usr/bin/env python3
"""Every live listing for the cards on Sid's watchlist (2026-10-15): the "custom scrape for
portfolio and favorites".

The feed's live columns come from alt.xyz's mirror of other sites, and that mirror is partial
(2026-10-14: for Palkia G LV.X PSA 9 the feed showed one eBay Buy It Now at $4,300 while eBay
itself also had $3,299 and $4,097 copies; for Plasma Storm Articuno EX PSA 10 the one eBay
listing was never mirrored at all). For the ~74 cards PokeSniper's users favorite or hold that
gap matters, so this asks eBay's own search for them once a day and writes every live listing
at the wanted grade, not only the cheapest, to latest/watchlist_listings.csv.

    python3 history/watchlist_listings.py --watchlist FILE [--out latest/watchlist_listings.csv]
    WATCHLIST_FILE=... python3 history/watchlist_listings.py
    WATCHLIST_URL=... WATCHLIST_TOKEN=... python3 history/watchlist_listings.py

Input: Sid's watchlist.csv (asset_id, grade, kind, profiles), one row per card and grade, from
a local file (WATCHLIST_FILE / --watchlist) or a URL (WATCHLIST_URL, with WATCHLIST_TOKEN sent
as a bearer token; a GitHub contents-API URL gets the raw file). Nothing else of that repo is
read. Rows whose asset is not in history/assets.csv, or whose grade is not 9 or 10, are skipped
and listed in the run summary.

Sources, one function each (`ebay_rows`, `mirror_rows`), concatenated by `sweep`; a direct
Fanatics Collect pull slots in as another function when that permission lands (2026-10-15):
  eBay      Browse API item_summary/search (application token, same keyset as ebay_verify.py),
            up to 200 hits per page. Two queries per card and grade because eBay titles follow
            the PSA label, not TCGplayer's catalog ("Umbreon Gold Star 17", "Palkia G Lv X 125")
            and a set-name query finds listings a number query misses and vice versa (2026-10-15
            trial: Mew EX RC24 105 hits by number, 1 by set; Jolteon Gold Star 9 by number, 23
            by set): "<subject> <number> PSA <grade>" and "<subject> <set> PSA <grade>". Each
            query runs twice, with buyingOptions FIXED_PRICE and AUCTION: a search without the
            filter left out 12 of 13 running auctions alt.xyz had mirrored for these cards
            (2026-10-15; a Palkia G LV.X PSA 9 auction absent from 113 unfiltered hits came
            back at once with the AUCTION filter). So a card costs 4 calls (more when a query
            has over 200 hits), ~306 a day for 72 card-grades (busy queries page past 200 hits); WATCHLIST_EBAY_BUDGET (360)
            caps it and nightly/run_ebay_verify.sh takes what was used off the verify's 4,800.
  alt.xyz   history/live_listings.csv rows for the asset at that grade: Alt's own auctions and
            marketplace, and alt.xyz's mirror of Fanatics Collect / Goldin / CardHobby, plus
            the mirrored eBay listings the search did not return (source "eBay (alt mirror)":
            found by alt.xyz, not re-found by eBay's search, e.g. a title with no "PSA 10").

Which eBay hits count (title_matches): the rule is "right card, right grade, or refuse". A
hit is kept only when its title (1) names PSA and exactly one grade, the wanted one;
(2) contains every word of alt.xyz's subject ("Palkia G Lv X"), with Lv.X / LV X / Level X
and Ho-Oh / Lt. Surge's spelled any way; (3) contains the card number as a token, "#17",
"017/017", "DP 28" included, and the printed total when one is given must be the card's;
(4) has set evidence: the number with its total, a letter-prefixed promo number (DP28, SL5,
H23, RC24), a strong variant word on the card (Gold Star, Lv X, Prime, Shiny), or a word of
the set's name; (5) agrees on the printing: a 1st Edition card needs "1st"/"first", any other
refuses it, same for Reverse Holo and Shadowless; (6) names no other language, reprint set
(Celebrations, Classic Collection, 25th anniversary), lot, autograph, proxy, jumbo or sealed
product, and no year more than a year off the card's (sellers copy the PSA label's year, so a
"2021" Umbreon Gold Star 17/17 is the Celebrations reprint). Anything else is refused: a
listing that is really the card but titled vaguely is left to alt.xyz's mirror, never guessed.
Prices are what eBay reports for EBAY_US (foreign listings converted to USD by eBay).

Output latest/watchlist_listings.csv, one row per live listing, sorted by asset, grade, kind,
price: asset_id, grade (as the watchlist writes it: 10 or 9), source, kind (auction / bin),
price (high bid or opening price for an auction; the ask for a bin), bid_count, ends (auction
end, UTC), url, title (eBay's; the feed's own listings have none), first_seen (the first day
this sweep saw the url; carried over from the previous file), checked_at (this run, UTC). A
card with no row was swept and had nothing live, unless the run summary lists it as not
swept (budget, rate limit, unknown asset): absent = unknown, never "nothing listed".
latest/watchlist_listings.summary.json next to it records calls, cards swept and skipped.

Standard library only, like the rest of the feed."""
import argparse
import csv
import io
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import ebay_verify   # noqa: E402
import ingest        # noqa: E402

SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
PAGE = 200                 # Browse search's maximum page size
MAX_PAGES = 3              # per query; more than 600 hits means the query is too loose anyway
DEFAULT_BUDGET = 360       # eBay calls per run (~306 for 72 card-grades); the runner gives ebay_verify.py the rest of the day's 5,000
KINDS = {"bin": "FIXED_PRICE", "auction": "AUCTION"}   # one search per buying option, see the module doc
DEFAULT_OUT = ROOT / "latest" / "watchlist_listings.csv"
OUT_COLS = ["asset_id", "grade", "source", "kind", "price", "bid_count", "ends", "url", "title", "first_seen", "checked_at"]
GRADES = {"10": "10.0", "9": "9.0"}            # watchlist grade -> live_listings.csv grade
MIRROR_EBAY = "eBay (alt mirror)"

STRONG_VARIANTS = ("gold star", "lv x", "prime", "shiny")
REFUSE_WORDS = {
    "japanese", "japan", "jpn", "korean", "chinese", "german", "french", "italian", "spanish", "portuguese",
    "indonesian", "thai", "dutch", "russian",
    "celebrations", "celebration", "25th", "30th", "anniversary", "anniv", "reprint", "proxy", "custom", "replica", "metal", "plated",
    "championship", "championships", "champ", "champs", "worlds",   # World Championships deck replicas carry the real number
    "lot", "lots", "bundle", "set of", "auto", "autograph", "autographed", "signed", "jumbo", "oversized",
    "sealed", "booster", "box", "deck", "staff", "prerelease", "stamped", "error", "miscut",
    "sample", "topps", "sticker", "coin", "figure", "plush", "poster",
    "bgs", "cgc", "sgc", "beckett",
    "sequential", "seq", "pair", "duo", "trio", "both", "x2", "2x", "x3", "3x",   # two or more cards in one listing
}
REFUSE_PHRASES = ("classic collection", "world championships")
GENERIC_WORDS = {"pokemon", "and", "the", "of", "ex", "series", "english"}
# Block-level and packaging words are no evidence of a set on their own ("Black and White" covers 11 sets);
# a set whose name is only such words (Platinum, HeartGold SoulSilver) falls back to them.
WEAK_SET_WORDS = GENERIC_WORDS | {"black", "white", "diamond", "pearl", "platinum", "heartgold", "soulsilver", "nintendo",
                                  "promo", "promos", "star", "organized", "play", "collection", "collector", "collectors",
                                  "tins", "tin", "window", "fall", "shiny", "xy", "sun", "moon", "sword", "shield",
                                  "sylveon", "primal", "kyogre"}
SET_ALIASES = {   # words sellers use for a set that its names do not contain
    "heartgold and soulsilver": ["hgss"],
    "black and white": ["bw"],
    "black & white": ["bw"],
    "diamond and pearl": ["dp"],
    "radiant collection": ["radiant"],
    "pop series": ["pop"],
    "black star promo": ["promo", "promos", "black star"],
}
GRADE_RX = re.compile(r"\bpsa[\s:-]*(?:gem[\s-]*)?(?:mint|mt|nm[\s-]*mt|nm)?[\s-]*(10|[1-9](?:\.5)?)(?![\d.])")
YEAR_RX = re.compile(r"(?<!\d)(19[89]\d|20[0-3]\d)(?!\d)")
TOKEN_RX = re.compile(r"[a-z0-9]+")


def norm(s):
    """Lower-case ASCII with punctuation turned into spaces; Lv.X / LVX / Level X -> 'lv x',
    1st / first edition -> '1st edition', so titles and alt.xyz's names compare word by word."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("'", "").replace("’", "")
    s = re.sub(r"\b(lv|lvl|level)\s*\.?\s*x\b", "lv x", s)
    s = re.sub(r"\blvx\b", "lv x", s)
    s = re.sub(r"\bgoldstar\b", "gold star", s)
    s = re.sub(r"\b(1st|first)[\s.-]*(edition|ed)\b", "1st edition", s)
    s = re.sub(r"[^a-z0-9/#.]+", " ", s)
    s = re.sub(r"\.(?=\s|$)", " ", s)        # a trailing dot ("Lt.") is punctuation, "9.5" is not
    return re.sub(r"\s+", " ", s).strip()


def words(s):
    return TOKEN_RX.findall(norm(s).replace("/", " ").replace("#", " ").replace(".", " "))


def number_parts(number):
    """'017/017' -> ('17', '17'); 'DP28' -> ('dp28', None); '125' -> ('125', None)."""
    n, _, total = (number or "").partition("/")
    n = norm(n).replace(" ", "")
    n = n.lstrip("0") or "0" if n.isdigit() else n
    total = total.strip().lstrip("0") if total.strip().isdigit() else None
    return n, total


def number_rx(n):
    """Matches the card number as a token: '#17', '017', '17/17', 'DP 28', 'No.17'; not '170', '1st'."""
    m = re.fullmatch(r"([a-z]+)(\d+)", n)
    core = rf"{m.group(1)}\s?0*{m.group(2)}" if m else rf"0*{re.escape(n)}"
    return re.compile(rf"(?<![a-z0-9])(?:#|no\.?\s?)?{core}(?:\s?/\s?0*(\d+))?(?![a-z0-9])")


class Card:
    """What the matcher needs to know about one watchlist card, from history/assets.csv and
    latest/card_catalog.csv (the catalog gives the printed total and TCGplayer's set name)."""

    def __init__(self, asset, catalog=None):
        self.asset_id = asset["asset_id"]
        self.subject = asset.get("subject", "")
        self.set = asset.get("set", "")
        self.variety = asset.get("variety", "")
        self.year = (asset.get("year") or "")[:4]
        self.card_name = asset.get("card_name", "")
        cat = catalog or {}
        self.catalog_set = cat.get("set", "")
        self.number, self.total = number_parts(cat.get("number") or asset.get("card_number", ""))
        if not self.number:
            self.number, self.total = number_parts(asset.get("card_number", ""))
        self.letter_number = bool(re.fullmatch(r"[a-z]+\d+", self.number))
        self.number_rx = number_rx(self.number)
        everything = norm(" ".join([self.card_name, self.subject, self.variety, self.set, self.catalog_set]))
        self.variants = [v for v in STRONG_VARIANTS if re.search(rf"\b{v}\b", everything)]
        self.first_edition = "1st edition" in norm(self.variety)
        self.reverse = "reverse" in norm(self.variety)
        self.shadowless = "shadowless" in norm(self.variety)
        self.set_words = self._set_words()
        variant_words = {w for v in self.variants for w in v.split()}
        self.subject_words = [w for w in words(self.subject) if w != "pokemon" and w not in variant_words]
        # "Palkia G", "Rayquaza C", "Alakazam 4": sellers drop the short tag; it is not needed once the
        # number with its printed total pins the card ("Palkia LV.X 125/127" can only be Palkia G).
        self.short_words = {w for w in self.subject_words if len(w) <= 2 or w.isdigit()}

    def _set_words(self):
        found = set()
        for s in (self.set, self.catalog_set):
            ns = norm(s)
            for phrase, aliases in SET_ALIASES.items():
                if phrase in ns:
                    found.update(aliases)
            ws = [w for w in words(s) if not w.isdigit() and len(w) > 1]
            strong = [w for w in ws if w not in WEAK_SET_WORDS]
            found.update(strong or [w for w in ws if w not in GENERIC_WORDS])
        return sorted(found)

    def short_set(self):
        """The set the way an eBay seller writes it, for the second query: the set's own words
        after alt.xyz's "Pokemon Black and White ..." prefix; a promo set is just "Promo"."""
        s = self.set
        if "promo" in norm(s):
            return "Promo"
        for prefix in ("Pokemon Black & White", "Pokemon Black and White", "Pokemon Diamond and Pearl", "Pokemon Heartgold and Soulsilver",
                       "Pokemon Organized Play", "Pokemon Platinum", "Pokemon Gold Star", "Pokemon XY", "Pokemon"):
            if s.startswith(prefix + " ") and len(s) > len(prefix) + 1:
                s = s[len(prefix) + 1:]
                break
        s = re.sub(r"\b(Shiny|Radiant Collection|English)\b", "", s).strip()
        if "Legendary Treasures" in s and self.letter_number:
            s = "Radiant Collection"
        return s or self.catalog_set

    def queries(self, grade):
        subj = re.sub(r"\s+", " ", self.subject.replace("Lv.X", "Lv X").replace("LV.X", "Lv X")).strip()
        num = self.number.upper() if self.letter_number else self.number
        ed = " 1st Edition" if self.first_edition else ""
        rev = " Reverse" if self.reverse else ""
        q1 = f"{subj} {num}{ed}{rev} PSA {grade}"
        q2 = f"{subj} {self.short_set()}{ed}{rev} PSA {grade}"
        return [q1] if norm(q2) == norm(q1) else [q1, q2]


def has_word(title_words, w):
    """w, its plural, or its singular is one of the title's words ("Surge's" / "Surges" / "Surge")."""
    return w in title_words or w + "s" in title_words or (w.endswith("s") and w[:-1] in title_words)


def title_matches(card, grade, title):
    """(True, "") when the eBay title is this card at this grade, else (False, why)."""
    t = norm(title)
    grades = {g for g in GRADE_RX.findall(t)}
    if grades != {str(grade)}:
        return False, f"grade {sorted(grades) or 'none'}"
    if len(GRADE_RX.findall(t)) > 1 or len(re.findall(r"\bpsa\b", t)) > 1:
        return False, "more than one graded card in the title"
    tw = set(words(t))
    for w in REFUSE_WORDS:
        if (" " in w and w in t) or (" " not in w and w in tw):
            if w not in card.subject_words and w not in card.set_words:
                return False, f"refused word {w!r}"
    for p in REFUSE_PHRASES:
        if p in t and not (p.startswith(("1st", "first")) and card.first_edition):
            return False, f"refused {p!r}"
    for y in YEAR_RX.findall(t):
        if card.year.isdigit() and abs(int(y) - int(card.year)) > 1:
            return False, f"year {y} vs {card.year}"
    m = card.number_rx.search(t)
    if not m:
        return False, f"number {card.number} missing"
    total = m.group(1)
    if total and card.total and total.lstrip("0") != card.total:
        return False, f"total {total} vs {card.total}"
    missing = [w for w in card.subject_words if not has_word(tw, w) and not (total and w in card.short_words)]
    if missing:
        return False, f"subject word {missing[0]!r} missing"
    if card.first_edition != bool(re.search(r"\b1st edition\b", t)):
        return False, "1st edition mismatch"
    if card.reverse != bool(re.search(r"\b(reverse|rev)\b", t)):
        return False, "reverse holo mismatch"
    if card.shadowless != ("shadowless" in tw):
        return False, "shadowless mismatch"
    for v in card.variants:
        if v == "gold star" and "star" in tw:
            continue
        if not re.search(rf"\b{v}\b", t):
            return False, f"variant {v!r} missing"
    set_hit = next((w for w in card.set_words if w in tw or (" " in w and w in t)), None)
    if not (total or card.letter_number or card.variants or set_hit):
        return False, "no set evidence"
    return True, ""


def search(ebay, q, kind, offset=0, urlopen=None):
    """One Browse search call for one buying option: (items, total). Raises RateLimited on
    429; other HTTP errors give ([], None) so the card is marked not swept, not wrongly empty."""
    urlopen = urlopen or urllib.request.urlopen
    params = {"q": q, "limit": PAGE, "offset": offset, "filter": "buyingOptions:{%s}" % KINDS[kind]}
    req = urllib.request.Request(f"{SEARCH_URL}?{urllib.parse.urlencode(params)}", headers={
        "Authorization": f"Bearer {ebay.bearer()}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US", "Accept": "application/json"})
    try:
        with urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise ebay_verify.RateLimited() from None
        return [], None
    except (urllib.error.URLError, TimeoutError, ValueError):
        return [], None
    return d.get("itemSummaries") or [], d.get("total")


def item_row(it, stamp):
    """An eBay item summary as an output row (asset_id / grade / first_seen filled by the caller)."""
    auction = "AUCTION" in (it.get("buyingOptions") or [])
    price = ((it.get("currentBidPrice") if auction else None) or it.get("price") or {}).get("value", "")
    return {"source": "eBay", "kind": "auction" if auction else "bin", "price": price,
            "bid_count": str(it.get("bidCount", "")) if auction else "",
            "ends": (it.get("itemEndDate") or "") if auction else "",
            "url": f"https://www.ebay.com/itm/{it.get('legacyItemId') or ''}", "title": it.get("title", ""),
            "checked_at": stamp}


def summary_path(out):
    """latest/watchlist_listings.csv -> latest/watchlist_listings.summary.json"""
    out = Path(out)
    return out.with_name(out.name[:-4] + ".summary.json" if out.name.endswith(".csv") else out.name + ".summary.json")


class Budget:
    def __init__(self, n):
        self.left, self.used, self.rate_limited = n, 0, False

    def take(self):
        if self.left <= 0 or self.rate_limited:
            return False
        self.left -= 1
        self.used += 1
        return True


def ebay_rows(card, grade, ebay, budget, stamp, urlopen=None, log=None):
    """Every eBay listing of this card at this grade the search can find and the title filter
    accepts. Returns (rows, complete): complete is False when a query could not be run (budget,
    rate limit, HTTP error), so the caller can say the card was not fully swept."""
    seen, rows, complete = set(), [], True
    for q in card.queries(grade):
      for kind in KINDS:
        offset = 0
        for _ in range(MAX_PAGES):
            if not budget.take():
                return rows, False
            try:
                items, total = search(ebay, q, kind, offset, urlopen)
            except ebay_verify.RateLimited:
                budget.rate_limited = True
                return rows, False
            if total is None:
                complete = False
                break
            for it in items:
                iid = it.get("legacyItemId")
                if not iid or iid in seen:
                    continue
                ok, why = title_matches(card, grade, it.get("title", ""))
                if log is not None:
                    log.append((f"{q} [{kind}]", iid, ok, why, it.get("title", "")))
                if ok:
                    seen.add(iid)
                    rows.append(item_row(it, stamp))
            offset += PAGE
            if offset >= (total or 0) or not items:
                break
    return rows, complete


def mirror_rows(asset_id, grade, live_rows, ebay_ids, stamp, now_iso):
    """history/live_listings.csv rows for the asset at this grade: Alt's own listings and the
    mirrored houses, plus mirrored eBay listings the direct search did not return."""
    rows = []
    for r in live_rows:
        if r["asset_id"] != asset_id or r.get("grading_company", "PSA") != "PSA" or r["grade"] != GRADES[grade]:
            continue
        auction = r["listing_type"] == "AUCTION"
        end = ingest.utc_iso(r.get("end_date") or "")   # CardHobby ends may be stored as -04:00; compared as instants
        if auction and ingest.when(end) and ingest.when(end) <= ingest.when(now_iso):
            continue
        source = r["source"]
        if source == "eBay":
            if ebay_verify.item_id(r["url"]) in ebay_ids:
                continue
            source = MIRROR_EBAY
        rows.append({"source": source, "kind": "auction" if auction else "bin",
                     "price": (r.get("current_bid") or "") if auction else (r.get("buy_it_now_price") or ""),
                     "bid_count": (r.get("bid_count") or "") if auction else "", "ends": end if auction else "",
                     "url": r["url"], "title": "", "checked_at": stamp})
    return rows


def read_watchlist(path=None, url=None, token=None):
    if path:
        return ingest.read_csv(Path(path))
    headers = {"User-Agent": "pokemon-psa10-history watchlist sweep"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if "api.github.com" in url:
        headers["Accept"] = "application/vnd.github.raw+json"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
        text = r.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def load_cards(store, latest):
    assets = {r["asset_id"]: r for r in ingest.read_csv(store / "assets.csv")}
    catalog = {r["asset_id"]: r for r in ingest.read_csv(latest / "card_catalog.csv") if r.get("asset_id")}
    return assets, catalog


def sweep(watchlist, out=DEFAULT_OUT, store=HERE, latest=None, budget=DEFAULT_BUDGET, urlopen=None, now=None, keys=None, log=None):
    """Run the whole thing; returns the summary dict (also written next to `out`)."""
    latest = latest or out.parent
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    today = now.date().isoformat()
    assets, catalog = load_cards(store, latest)
    live_rows = ingest.read_csv(store / "live_listings.csv")
    previous = {r["url"]: r for r in ingest.read_csv(Path(out))}
    keys = ebay_verify.load_keys() if keys is None else keys     # {} = run without eBay (alt.xyz rows only)
    ebay = ebay_verify.Ebay(keys, urlopen) if keys else None
    bud = Budget(budget if ebay else 0)
    summary = {"checked_at": stamp, "rows": 0, "ebay_calls": 0, "swept": [], "not_swept": [], "skipped": [], "ebay_keys": bool(keys)}
    out_rows, done = [], set()
    for w in watchlist:
        aid, grade = (w.get("asset_id") or "").strip(), (w.get("grade") or "").strip()
        if grade.endswith(".0"):
            grade = grade[:-2]
        if (aid, grade) in done:
            continue
        done.add((aid, grade))
        if aid not in assets or grade not in GRADES:
            summary["skipped"].append({"asset_id": aid, "grade": grade, "why": "unknown asset" if aid not in assets else "grade not 9 or 10"})
            continue
        card = Card(assets[aid], catalog.get(aid))
        rows, complete = ebay_rows(card, grade, ebay, bud, stamp, urlopen, log) if ebay else ([], False)
        ebay_ids = {ebay_verify.item_id(r["url"]) for r in rows}
        rows += mirror_rows(aid, grade, live_rows, ebay_ids, stamp, stamp)
        for r in rows:
            r.update(asset_id=aid, grade=grade, first_seen=(previous.get(r["url"]) or {}).get("first_seen") or today)
        out_rows += rows
        (summary["swept"] if complete else summary["not_swept"]).append({"asset_id": aid, "grade": grade, "name": card.card_name, "rows": len(rows)})
    out_rows.sort(key=lambda r: (r["asset_id"], r["grade"], r["kind"], ingest.to_float(r["price"]) if r["price"] else float("inf"), r["url"]))
    ingest.write_csv_atomic(Path(out), OUT_COLS, out_rows)
    summary["rows"], summary["ebay_calls"], summary["rate_limited"] = len(out_rows), bud.used, bud.rate_limited
    summary_path(out).write_text(json.dumps(summary, indent=1) + "\n")
    by_source = {}
    for r in out_rows:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
    print(f"watchlist sweep: {len(watchlist)} watchlist row(s), {len(summary['swept'])} swept, {len(summary['not_swept'])} not fully swept, "
          f"{len(summary['skipped'])} skipped; {len(out_rows)} live listing(s) {by_source}; {bud.used} eBay call(s)"
          + (" — stopped on eBay's rate limit" if bud.rate_limited else "") + ("" if keys else " — no eBay keys, alt.xyz rows only"))
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watchlist", default=os.environ.get("WATCHLIST_FILE") or None, help="local watchlist.csv (or WATCHLIST_FILE)")
    ap.add_argument("--url", default=os.environ.get("WATCHLIST_URL") or None, help="watchlist.csv URL (or WATCHLIST_URL; WATCHLIST_TOKEN as bearer)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--budget", type=int, default=int(os.environ.get("WATCHLIST_EBAY_BUDGET", DEFAULT_BUDGET)))
    ap.add_argument("--store", default=str(HERE))
    ap.add_argument("--latest", default=str(ROOT / "latest"))
    ap.add_argument("--log", help="write every eBay hit with its accept/refuse reason to this TSV")
    args = ap.parse_args()
    if not args.watchlist and not args.url:
        print("watchlist sweep: no WATCHLIST_FILE / WATCHLIST_URL, nothing to do")
        return
    wl = read_watchlist(args.watchlist, args.url, os.environ.get("WATCHLIST_TOKEN"))
    log = [] if args.log else None
    sweep(wl, Path(args.out), Path(args.store), Path(args.latest), args.budget, log=log)
    if log is not None:
        with open(args.log, "w") as f:
            for q, iid, ok, why, title in log:
                f.write(f"{'OK' if ok else 'no'}\t{why}\t{iid}\t{q}\t{title}\n")


if __name__ == "__main__":
    main()
