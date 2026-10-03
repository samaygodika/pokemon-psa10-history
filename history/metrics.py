#!/usr/bin/env python3
"""Build latest/ from the history store: the file PokeSniper's server reads.

    python3 history/metrics.py                 # writes latest/cards.csv, latest/series/<xx>.csv, latest/summary.json
                                               # (reads history/assets.csv, daily/, sales/, live_listings.csv)

latest/cards.csv has every column the scraper's own cards.csv has (so the
server keeps working unchanged), one row per asset using each asset's newest
daily numbers, plus the derived columns below. Every derived column is blank
when the history can't support it honestly — the server renders blank as "—",
never as 0.

  mirror copies        = a PWCC lot listed under both fanaticscollect.com and
                         pwccmarketplace.com (same asset, date, price) counts
                         once, everywhere below including recent_sales/.
  outlier sales        = alt.xyz already flags RELISTED / NOT_PAID rows (kept
                         out here via skipped_reason). On top of that, a sale
                         below 1/4x or above 6x the running median of the
                         card's last 12 accepted sales (past year) is held
                         back unless confirmed — two consecutive high sales
                         confirm a jump, five consecutive low sales a drop,
                         counting repeat sales of one listing URL once —
                         so a "$2,100 PSA 10 1st Edition Charizard" between
                         $300k+ sales is dropped but a card that really
                         tripled is not (see drop_outliers).
                         Everything below is computed from the clean sales only,
                         except that the change columns also count held-back
                         HIGH sales (see price_chg_30d_pct).
  clean_last_sale_*    = the newest clean sale (price/date/source); the volume
                         columns and every price level are built from clean
                         sales only.
                         outliers_excluded says how many rows the filter
                         dropped for the card.
  last_sale_unconfirmed = 1 when the literal newest sale (last_sale_*) is one
                         the filter is holding back, i.e. it differs from the
                         clean newest sale. The app shows the literal newest
                         sale and marks it unconfirmed (Sid, 2026-09-18).
  latest/recent_sales/<xx>.csv = the last RECENT_N sales per asset, newest
                         first, INCLUDING ones alt.xyz flags (status column:
                         ok / RELISTED / NOT_PAID / PENDING …) and ones the
                         outlier filter holds back (outlier = 1), with the
                         sale URL — so a user can eyeball a suspicious price.
  ref price at a date  = median of the up-to-3 most recent clean PSA 10 sales
                         on or before that date, looking back at most 180 days.
  price_chg_30d_pct    = ref(today) vs ref(today-30d), only when both exist AND
                         at least one sale happened in the last 30 days (a
                         window with no sales has no new information, so it is
                         blank rather than a misleading 0%). Same for 90d / 1y.
                         Since 2026-10-01 these refs are taken over the clean
                         sales PLUS the sales the filter still holds back on the
                         HIGH side: measured over the past year, a held-back high
                         newest sale was later shown real 81% of the time (90%
                         on cards up to 2013, eBay 80%, major auction houses
                         86%), a held-back low one 17% (4%). Leaving them out
                         left the cards that moved out of every category's
                         median %: 452 cards with a held-back sale in the last
                         30 days had a blank 30-day change. Held-back LOW sales
                         stay out. Because the ref is a median of 3, one held
                         sale moves a card only part of the way; a second sale
                         at the new level confirms the move and moves it fully.
  chg_held_windows     = the windows (30d 60d 90d 180d 1y, space-separated)
                         whose price_chg differs from the clean-only figure, i.e.
                         rests on a held-back high sale; blank for most cards.
                         mkt_cap_chg_30d_pct follows the 30d one.
  volume_30d/90d/1y    = number of clean PSA 10 sales in the window.
  pop_30d_ago          = PSA 10 population from the newest daily file at least
                         30 days old that has this asset; blank until the
                         daily series is that old.
  pop_chg_30d          = pop_at_grade - pop_30d_ago.
  mkt_cap_chg_30d_pct  = (ref_now x pop_now) vs (ref_30d x pop_30d_ago), refs as
                         in price_chg_30d_pct (held-back highs counted); needs
                         pop history, so blank for the first 30 days.
  median_last_3        = ref(today) over clean sales only: the card's price level
                         (the change columns differ from it only where
                         chg_held_windows is set).
  sales_first_date, sales_total, history_days: how much history stands behind
                         the row.

PSA 9 (2026-09-22; the idea to test: PSA 9s lag a PSA 10 pump by weeks):
  pop_at_grade_9       = PSA 9 population, from every run (same pop response).
  psa9_scraped_date    = newest run that pulled this card's PSA 9 sales
                         (--also-grade 9; the nightly scope only). Blank = PSA 9
                         sales not collected, so every psa9_* sale column is
                         blank too; psa9_sales_total = 0 means none sold.
  psa9_last_sale_*, psa9_clean_last_sale_price, psa9_last_sale_unconfirmed,
  psa9_median_last_3, psa9_volume_30d, psa9_price_chg_30d_pct, psa9_sales_total
                       = the PSA 10 definitions above, applied to PSA 9 sales
                         (same mirror rule, same outlier filter).
  psa9_to_psa10_ratio  = psa9_median_last_3 / median_last_3.
  latest/recent_sales_psa9/<xx>.csv = recent_sales/ for PSA 9, same columns.

Full clean sale history (2026-09-29), for PokeSniper's lib/salesHistory.js
(the Arbitrage pill's period-matched PSA 9 baseline), which until now read the
store's history/sales/ (~950 MB) and re-downloaded most of it every night:
  latest/clean_sales/<YYYY-MM>.csv = every clean PSA 10 and PSA 9 sale of that
                         month (after the mirror rule and the outlier filter,
                         exactly the sales the columns above are built from),
                         one line per card and grade:
                           asset_id,grade,sales    e.g.  ab12...,9,3:410 3:415 28:399.5
                         sales = space-separated day-of-month:price, ascending.
                         ~70 MB for all months (~32 MB gzipped) against ~950 MB
                         of store: only the columns the app reads, clean sales
                         only. Most months still change on most nights (2026-09-29:
                         68 of 92): ~90 cards a night enter or leave the feed with
                         their whole history, so a reader re-fetches most of it
                         each time -- ~32 MB instead of ~300 MB gzipped.
  latest/clean_sales/manifest.json = {"months": {"YYYY-MM": {"sha256", "bytes",
                         "sales"}}} over those files; sha256 of the file's bytes.
                         Deterministic (no timestamp), so it changes only when a
                         month does: a reader keeps its copies and fetches just
                         the months whose sha256 moved, and can check a download
                         against it (raw GitHub caches ~5 minutes, so a file can
                         briefly lag the manifest).

Live listings (2026-09-24), from history/live_listings.csv — what is for sale
right now at PSA 10, as alt.xyz mirrors it: eBay mostly, then Fanatics Collect
(including its weekly lots), CardHobby, Pristine Auction and some Goldin. A
snapshot from the card's last check, NOT live: the app must compare the end
times with its own clock.
  listings_checked_at  = UTC time of the newest successful check of this card's
                         listings (the nightly, or a listings refresh between
                         nightlies for cards with a running auction, 2026-10-03);
                         blank = never checked (or every check failed),
                         so every live column is blank = unknown (not "nothing
                         listed"). The share of cards with anything listed
                         tracks PSA 10 pop (pop 1000+: ~99%; pop 1-2: ~10%;
                         pop 0: ~1%), so most low-pop cards are a real "checked,
                         nothing listed". Auctions are pruned once they end; a
                         Buy It Now is dropped after 7 days without alt.xyz
                         showing it again (ingest.py) — but alt.xyz re-serves
                         ended BINs indefinitely, so a BIN link means "was
                         listed at", not "is for sale".
  live_auction_count   = auctions running at that check.
  next_auction_end, next_auction_bid, next_auction_bid_count,
  next_auction_source, next_auction_url
                       = the auction ending soonest (UTC end time). The bid is
                         the high bid at the check, or the opening price when
                         the bid count is 0 — never a price for the card: bids
                         jump in the last minutes (Sid's spec, Rule 1b).
  last_auction_end     = the latest end among those auctions: while it is in
                         the future at least one auction may still be running.
  lowest_bin_price, lowest_bin_source, lowest_bin_url
                       = the cheapest Buy It Now listing at that check (the
                         spec's lowestListingPrice). A BIN listing can sell or
                         be pulled between checks; nothing marks that.
  psa9_listings_checked_at, psa9_live_auction_count, psa9_next_auction_*,
  psa9_last_auction_end, psa9_lowest_bin_*
                       = the same eleven columns for PSA 9 listings, from the
                         card's PSA 9 rows in live_listings.csv and the
                         psa9_listings_checked_at the scraper writes with
                         --also-listings (set whenever that request succeeded,
                         blank when it failed or the card has no PSA 9 copies).
                         Off until the nightly switches it on
                         (NIGHTLY_ALSO_LISTINGS=1), so all blank until then;
                         once on, blank still = never checked at PSA 9, and a
                         count of 0 = checked, nothing running.

Listing age and suspect bids (2026-10-03), the last four columns of cards.csv
(Sid: "Buy now / Live data is sometimes still inaccurate"). alt.xyz re-serves
ended eBay Buy It Nows for months and its listing record carries no date or
status, so the feed's own first sighting is the only age there is; and a
running auction can carry a bid that is not for this card (a $120k bid on a
$2.6k Dragonite: a mislabeled lot, the same fault as mislabeled sales).
  lowest_bin_first_seen
                       = the run day the feed first recorded the lowest_bin_url
                         listing for this card and grade (history/
                         live_listings.csv first_seen; rows from before
                         2026-10-03 backfilled from the file's git history, so
                         the earliest possible value is 2026-09-25). The longer
                         ago, the more likely the BIN has ended: the app can
                         grey out or hide old ones. Blank = no BIN, or unknown.
  next_auction_bid_suspect
                       = 1 when next_auction_bid (high bid, or opening price at
                         0 bids) is above SUSPECT_BID_MULT x median_last_3, the
                         card's clean reference; 0 = checked against the
                         reference and not out of line; blank = no auction, or
                         no clean reference to judge by. A 1 means "do not show
                         this bid as the card's price", not "fake": a real
                         re-pricing looks the same until a sale confirms it.
  psa9_lowest_bin_first_seen, psa9_next_auction_bid_suspect
                       = the same two for the PSA 9 listing columns, judged
                         against psa9_median_last_3.

Market cap inputs (2026-09-28), the last columns of cards.csv before chg_held_windows:
  price_chg_60d_pct, price_chg_180d_pct, volume_60d, volume_180d
                       = the price_chg / volume rules above for 60 and 180
                         days, so the app's 2m and 6m windows are measured,
                         not interpolated.
  cap_price            = what the card counts at in a market cap today:
                         median_last_3, else the newest clean sale of any age.
                         Never an unconfirmed sale. Blank = no clean sale ever.
  cap_price_30d_ago, _60d_ago, _90d_ago, _180d_ago, _1y_ago
                       = the same price as of that many days before today
                         (blank = no clean sale yet by then). A group's market
                         cap change over a window is
                         sum(cap_price x pop) / sum(cap_price_N_ago x pop) - 1
                         over its cards that have both: a card that didn't
                         sell counts as unchanged, and one card's % can't be
                         weighted by its own jump. Pop is today's for every
                         window until the daily files are that old (pop_30d_ago
                         from 2026-10-11); history/coverage.py does this per
                         character.

"today" is the data date (the newest daily file), not the wall clock, so
rebuilding latest/ from the same history always gives the same file.
"""
import csv
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LATEST = ROOT / "latest"

# Same column order the scraper writes, so latest/cards.csv is a drop-in.
CARD_COLS = ["input", "asset_id", "card_name", "alt_url", "alt_public_url", "year", "set", "card_number", "subject", "variety",
             "grading_company", "grade", "pop_at_grade", "company_total_pop", "index_total_pop", "index_transaction_count", "num_sales",
             "last_sale_price", "last_sale_date", "last_sale_source", "avg_last_3_sales",
             "highest_sale", "lowest_sale", "scraped_at",
             "listing_source", "listing_grade", "listing_grading_company", "listing_price", "listing_url"]
DERIVED_COLS = ["clean_last_sale_price", "clean_last_sale_date", "clean_last_sale_source", "outliers_excluded", "last_sale_unconfirmed",
                "price_chg_30d_pct", "price_chg_90d_pct", "price_chg_1y_pct", "mkt_cap_chg_30d_pct",
                "volume_30d", "volume_90d", "volume_1y", "pop_30d_ago", "pop_chg_30d",
                "median_last_3", "sales_first_date", "sales_total", "history_days"]
SERIES_COLS = ["asset_id", "week_start", "n_sales", "median_price", "low", "high"]
RECENT_COLS = ["asset_id", "date", "price", "source", "sale_type", "status", "outlier", "url"]
RECENT_N = 10
CLEAN_SALES_COLS = ["asset_id", "grade", "sales"]

LOOKBACK_DAYS = 180
OUTLIER_LOW, OUTLIER_HIGH = 0.25, 6.0          # band around the running reference (asymmetric: junk is mostly low)
OUTLIER_REF, OUTLIER_MIN_ACCEPTED = 12, 4       # reference = median of the last 12 accepted sales, needs 4
OUTLIER_RESET_RUN_LOW, OUTLIER_RESET_RUN_HIGH = 5, 2   # consecutive same-side rejections that confirm a real move
OUTLIER_REF_MAX_AGE = timedelta(days=365)      # a reference older than this says nothing about today
OUTLIER_STALE_MIN = 3                          # with fewer than OUTLIER_MIN_ACCEPTED same-year sales, fall back to the last
OUTLIER_STALE_LOW, OUTLIER_STALE_HIGH = 0.1, 10.0   # OUTLIER_REF accepted sales of any age, with this wider band (2026-09-21)
OUTLIER_CONTINUATION = 2.0                     # a sale within this factor of the LAST accepted sale is never an outlier
OUTLIER_THIN_RUN = 2                           # 1-2 accepted sales ever: the stale band, and 2 listings confirm either side (2026-09-28)
WINDOWS = {"30d": 30, "90d": 90, "1y": 365}
EXTRA_WINDOWS = {"60d": 60, "180d": 180}        # measured 2m / 6m changes (2026-09-28), appended at the end of cards.csv
CAP_WINDOWS = {"30d": 30, "60d": 60, "90d": 90, "180d": 180, "1y": 365}
MIRROR_HOSTS = {"fanaticscollect.com", "pwccmarketplace.com"}   # one PWCC lot, two URLs (2026-09-22)
GRADES = ("10.0", "9.0")   # PSA 9 sales exist only for cards scraped with --also-grade 9
PSA9_COLS = ["pop_at_grade_9", "psa9_scraped_date", "psa9_last_sale_price", "psa9_last_sale_date", "psa9_last_sale_source",
             "psa9_clean_last_sale_price", "psa9_last_sale_unconfirmed", "psa9_median_last_3", "psa9_volume_30d",
             "psa9_price_chg_30d_pct", "psa9_sales_total", "psa9_to_psa10_ratio"]
LIVE_COLS = ["listings_checked_at", "live_auction_count", "next_auction_end", "next_auction_bid", "next_auction_bid_count",
             "next_auction_source", "next_auction_url", "last_auction_end", "lowest_bin_price", "lowest_bin_source", "lowest_bin_url"]
PSA9_LIVE_COLS = ["psa9_" + c for c in LIVE_COLS]   # the same eleven for PSA 9 listings
CAP_COLS = ([f"price_chg_{w}_pct" for w in EXTRA_WINDOWS] + [f"volume_{w}" for w in EXTRA_WINDOWS] + ["cap_price"]
            + [f"cap_price_{w}_ago" for w in CAP_WINDOWS])   # 2026-09-28
HELD_COLS = ["chg_held_windows"]   # 2026-10-01
# 2026-10-03, the very end of cards.csv: how long the cheapest BIN has been listed, and
# whether the next auction's bid is out of line with the card's price (see the module doc)
LISTING_AGE_COLS = ["lowest_bin_first_seen", "next_auction_bid_suspect",
                    "psa9_lowest_bin_first_seen", "psa9_next_auction_bid_suspect"]
SUSPECT_BID_MULT = OUTLIER_HIGH   # a bid above this x the clean reference is flagged, same band as a sale
LIVE_CHECK_COLS = {"10.0": "listings_checked_at", "9.0": "psa9_listings_checked_at"}   # grade -> daily column with its check time


def d(s):
    return date.fromisoformat(s[:10])


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_daily(store):
    """{date: {asset_id: row}} for every daily file, plus the sorted date list."""
    files = sorted((store / "daily").glob("*.csv"))
    if not files:
        sys.exit("no history/daily/*.csv yet — run history/ingest.py first")
    daily = {}
    for p in files:
        daily[p.stem] = {r["asset_id"]: r for r in read_csv(p)}
    return daily, sorted(daily)


def url_host(url):
    h = urlparse(url).hostname or ""
    return h[4:] if h.startswith("www.") else h


def drop_mirror_copies(rows):
    """alt.xyz records each PWCC Weekly Auctions lot twice, once under
    fanaticscollect.com and once under pwccmarketplace.com: same asset, date
    and price, different URL, so the URL dedup in ingest.py keeps both. Left
    in, the copy confirms its own original in drop_outliers (Legendary
    Collection Articuno RH: one $204,000 lot read as a confirmed +1,260%).
    Within each (date, price) the two hosts describe the same sales, so keep
    whichever host has more rows (several copies of a card can sell in one
    auction at one price) and drop the other's. rows: (date, price, ..., url)
    tuples with url last. Returns (kept, n_dropped)."""
    groups = defaultdict(lambda: defaultdict(list))
    for i, r in enumerate(rows):
        h = url_host(r[-1])
        if h in MIRROR_HOSTS:
            groups[(r[0], r[1])][h].append(i)
    drop = set()
    for by_host in groups.values():
        if len(by_host) < 2:
            continue
        fan, pwcc = by_host["fanaticscollect.com"], by_host["pwccmarketplace.com"]
        drop.update(pwcc if len(fan) >= len(pwcc) else fan)
    return [r for i, r in enumerate(rows) if i not in drop], len(drop)


def load_sales(store):
    """{grade: (by_asset, n, outliers, raw, held_high)} for each of GRADES, one pass over
    the history. by_asset = {asset_id: [(date, price, source), ...] ascending}
    with skipped sales (alt.xyz's own outlier/bad-data flag), PWCC mirror
    copies and outliers excluded; raw = every sale incl. flagged ones as
    (date, price, source, sale_type, status, url), mirror copies excluded;
    held_high = {asset_id: [(date, price, source), ...]} the sales the outlier
    filter still holds back on the high side (drop_outliers)."""
    raw = {g: defaultdict(list) for g in GRADES}
    for p in sorted((store / "sales").glob("*.csv")):
        with open(p, newline="", encoding="utf-8") as f:
            for s in csv.DictReader(f):
                if s.get("grading_company") != "PSA" or s.get("grade") not in raw:
                    continue
                try:
                    price = float(s["price"])
                except (TypeError, ValueError):
                    continue
                if price <= 0:
                    continue
                raw[s["grade"]][s["asset_id"]].append((d(s["date"]), price, s.get("source") or "", s.get("sale_type") or "", s.get("skipped_reason") or "ok", s.get("url") or ""))
    return {g: clean_grade(g, raw[g]) for g in GRADES}


def clean_grade(grade, raw):
    by_asset = defaultdict(list)
    n = mirrors = 0
    for aid in list(raw):
        raw[aid], dropped = drop_mirror_copies(raw[aid])
        mirrors += dropped
        for dt_, price, source, _, status, url in raw[aid]:
            if status == "ok":
                by_asset[aid].append((dt_, price, source, url))
                n += 1
    print(f"  PSA {grade}: {mirrors} PWCC mirror copies dropped", file=sys.stderr)
    outliers, held_high = {}, {}
    for aid, v in by_asset.items():
        v.sort()
        hh = []
        clean, dropped = drop_outliers(v, held_high=hh)
        by_asset[aid] = clean
        outliers[aid] = dropped
        if hh:
            held_high[aid] = hh
    return by_asset, n, outliers, raw, held_high


def drop_outliers(sales, held_high=None):
    """Sequential outlier filter. Walk the sales in date order keeping a
    reference price = median of the last OUTLIER_REF accepted sales from the
    past OUTLIER_REF_MAX_AGE; once at least OUTLIER_MIN_ACCEPTED sales are
    accepted, a sale outside [OUTLIER_LOW, OUTLIER_HIGH] x the reference is
    held back. Junk never enters the reference, so a cluster of bogus rows
    can't drag it down — which is what broke a plain neighbour-median filter
    on the 1st Edition Base Set Charizard, where alt.xyz lists more $3k–$26k
    mislabeled "PSA 10" eBay sales in 2026 than real $300k–$950k ones.

    The band and the confirmation rule are asymmetric on purpose (revised
    2026-09-16 after Sid found the first version discarding real moves —
    2,658 cards had their newest sale rejected and were shown a price a
    median 157 days stale). Measured on the full history, a rejected sale on
    the HIGH side was later confirmed by another sale within 35% of it 61%
    of the time at 4–6x and 54% at 6–10x: those are mostly real re-pricings
    (hype cycles, a rising 2025–26 market), not junk. Rejected LOW sales
    were confirmed only a third of the time: that side is where the
    mislabeled lots live. So:
      - high side: band 6x, and TWO consecutive high sales confirm the move
        (both are then accepted and the reference jumps to them);
      - low side: band 1/4x, and FIVE consecutive low sales are needed to
        reset (the Charizard's junk came in runs of up to four);
      - the reference only looks back one year, so a thin card whose last
        dozen accepted sales are years old can't anchor a stale price.
    Result on 2026-09-16 data: newest-sale rejections 2,658 -> 144, sales
    dropped 0.67% -> 0.16%, every known junk case still clean, and the
    sales it still rejects are mostly never confirmed.

    Stale-reference fallback (2026-09-21, after Sid found a PSA 5 sold at
    $31.20 sitting in a Meganium Prime's PSA 10 history): the one-year
    reference left every card with fewer than four sales that year with NO
    filter at all, and 3% of all sales (83k) fall in that gap, including 963
    cards whose newest sale is 10x+ their whole history (alt.xyz's Goldin and
    PWCC ingestion attaches unrelated lots: a "Crown Zenith" $122,000 sale
    whose URL is a signed Steve Jobs job application). So when the year has
    fewer than OUTLIER_MIN_ACCEPTED accepted sales but the card has at least
    OUTLIER_STALE_MIN, the reference is the last OUTLIER_REF accepted sales
    of any age with a wider band, 1/10x .. 10x, and the same confirmation
    runs. Measured on that population: low-side sales below 1/10x are later
    confirmed 5% of the time (junk), high-side sales above 10x about 65-70%
    of the time (mostly real re-pricings of long-dormant vintage cards), so
    the high side is a hold-until-confirmed, not a verdict: the literal sale
    still ships as last_sale_price with last_sale_unconfirmed = 1, and the
    clean price / change columns wait for the second sale.

    Once a run is confirmed the reference really does jump to it: only sales
    from the confirmed run onward feed the reference (a regime), and one such
    sale is enough. Without that, the median of the last 12 accepted sales
    stayed anchored to the old level after a confirmed 10x move and held
    back every later sale at the new level in pairs (seen 2026-09-21 on the
    first version of the stale fallback: 700+ cards flagged unconfirmed on a
    newest sale within 1% of the previous one). And a sale within
    OUTLIER_CONTINUATION of the last accepted sale is accepted whatever the
    median says: a slow-moving 12-sale median can sit just under the band's
    edge after one accepted 9x sale, and the next sale at the same level
    must not be called an outlier.

    A run is confirmed by DISTINCT listings, not by sales (2026-09-28). An
    optional fourth element per sale is its listing URL; repeat sales on one
    URL count once towards OUTLIER_RESET_RUN_LOW/HIGH (a blank or missing
    URL counts as its own listing). Since the store keeps every sale of a
    multi-quantity eBay listing (ingest merge_sales, 2026-09-26), one $15
    Buy It Now that sold ten times — something that is not the card — made
    five "consecutive low sales" on its own and reset EX Dragon Frontiers
    Gold Star Charizard's reference from $58k to $15, so its 90-day change
    read +2,619,900%. Measured over the whole store: 28 of 114 confirmed low
    runs and 11 of 951 confirmed high runs rested on fewer distinct listings
    than the run length.

    Thin cards (2026-09-28): a card with only one or two accepted sales used
    to have no filter at all, so any single lot became its clean price
    unchecked (a BW87 Leafeon promo at $490 -> one $78,000 PWCC lot; an XY
    Mudkip at $3.72M). Correction 2026-10-01: the Leafeon lot (72 bids) is
    real by its lot page, not misattributed as first said; the point is that
    one sale can be either. 437 accepted 10x+ moves sat in that gap, 213 of them a card's
    newest clean sale. Now the first one or two sales are the reference with
    the stale band (1/10x .. 10x), and OUTLIER_THIN_RUN distinct listings
    confirm a move on EITHER side: with a one-sale reference the low side
    can't ask for five, or a card whose first sale was junk would sit on it.
    Where later sales exist, 131 of 224 such jumps held and 59 went back:
    the same hold-until-confirmed trade as the stale fallback.
    Returns (date, price, source) tuples.

    held_high: pass a list to also get the sales still held back on the HIGH
    side at the end (above the band, never confirmed), as (date, price,
    source) tuples in date order. The change columns count those (see
    build: a held-back high sale was later shown real 81% of the time over
    the past year, a held-back low one 17%)."""
    keep, dropped = [], 0
    accepted = []  # accepted sales (date, price, source), in date order
    run = []       # consecutive rejected sales, same side: (side, sale, listing, index)
    regime = 0     # index into accepted: the first sale of the last confirmed run (0 = no confirmed move yet)
    held = {}      # index -> (side, sale) for every rejected sale not (yet) confirmed
    for i, sale in enumerate(sales):
        listing = sale[3] if len(sale) > 3 and sale[3] else i
        sale = sale[:3]
        price = sale[1]
        pool = accepted[regime:]
        recent = [a[1] for a in pool[-OUTLIER_REF:] if sale[0] - a[0] <= OUTLIER_REF_MAX_AGE]
        if len(recent) >= (OUTLIER_MIN_ACCEPTED if regime == 0 else 1):
            ref, lo_b, hi_b = statistics.median(recent), OUTLIER_LOW, OUTLIER_HIGH
        elif len(pool) >= (OUTLIER_STALE_MIN if regime == 0 else 1):
            # stale reference: not enough sales this year, so the last 12 of any age with the wider band
            ref, lo_b, hi_b = statistics.median([a[1] for a in pool[-OUTLIER_REF:]]), OUTLIER_STALE_LOW, OUTLIER_STALE_HIGH
        elif pool:
            # thin card: one or two accepted sales ever, so the same wide band around them
            ref, lo_b, hi_b = statistics.median([a[1] for a in pool]), OUTLIER_STALE_LOW, OUTLIER_STALE_HIGH
        else:
            ref = None
        thin = regime == 0 and 0 < len(pool) < OUTLIER_STALE_MIN
        if ref is not None and not (accepted and 1 / OUTLIER_CONTINUATION <= price / accepted[-1][1] <= OUTLIER_CONTINUATION):
            low, high = price < lo_b * ref, price > hi_b * ref
            if low or high:
                side = "low" if low else "high"
                if run and run[0][0] != side:
                    run = []
                run.append((side, sale, listing, i))
                need = OUTLIER_THIN_RUN if thin else OUTLIER_RESET_RUN_LOW if side == "low" else OUTLIER_RESET_RUN_HIGH
                if len({r[2] for r in run}) >= need:
                    # confirmed move (high) or regime change (low): the run is real, and the reference jumps to it
                    regime = len(accepted)
                    for _, r, _, j in run:
                        keep.append(r)
                        accepted.append(r)
                        held.pop(j, None)
                    dropped -= len(run) - 1
                    run = []
                else:
                    dropped += 1
                    held[i] = (side, sale)
                continue
        run = []
        keep.append(sale)
        accepted.append(sale)
    keep.sort()
    if held_high is not None:
        held_high.extend(sorted(s for side, s in held.values() if side == "high"))
    return keep, dropped


def ref_price(sales, at):
    """Median of the up-to-3 most recent sales on or before `at`, within LOOKBACK_DAYS."""
    floor = at - timedelta(days=LOOKBACK_DAYS)
    recent = [p for (sd, p, _) in sales if floor < sd <= at]
    if not recent:
        return None
    return statistics.median(recent[-3:])


def cap_price(sales, at):
    """The price a card counts at in a market cap on `at`: ref_price, else the
    newest clean sale on or before `at` of any age (a card that hasn't sold for
    six months is still worth its last sale). None before its first clean sale."""
    ref = ref_price(sales, at)
    if ref is not None:
        return ref
    before = [p for (sd, p, _) in sales if sd <= at]
    return before[-1] if before else None


def count_in(sales, start, end):
    return sum(1 for (sd, _, _) in sales if start < sd <= end)


def change_since(clean, counted, start, today):
    """(% change since `start`, whether it rests on a held-back high sale) for
    one change column. `counted` = the card's clean sales plus the ones the
    outlier filter still holds back on the high side (the same list object as
    `clean` when there are none). Blank unless a counted sale landed in the
    window. The flag is set when the figure differs from the clean-only one."""
    chg = pct(ref_price(counted, today), ref_price(counted, start)) if count_in(counted, start, today) > 0 else None
    if counted is clean:
        return chg, False
    base = pct(ref_price(clean, today), ref_price(clean, start)) if count_in(clean, start, today) > 0 else None
    return chg, fmt(chg) != fmt(base)


def pct(now, before):
    if now is None or before is None or before <= 0:
        return None
    return round((now / before - 1) * 100, 2)


def fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))
    return str(v)


def to_int(s):
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def psa9_cols(aid, num, scraped_day, s9, raw9, ref10, today):
    """The PSA 9 columns for one card. pop_at_grade_9 comes with every run (it
    is in the same pop response as PSA 10); the sale columns only exist for
    cards some run scraped with --also-grade 9 (psa9_scraped_date), so blank
    there means "not collected", while psa9_sales_total = 0 means none sold."""
    out = {c: "" for c in PSA9_COLS}
    out["pop_at_grade_9"] = num.get("pop_at_grade_9", "")
    if not scraped_day and not raw9:
        return out
    out["psa9_scraped_date"] = scraped_day or ""
    last = s9[-1] if s9 else None
    ok = [t for t in raw9 if t[4] == "ok"]
    newest = max(ok) if ok else None
    if newest:
        out["psa9_last_sale_price"], out["psa9_last_sale_date"], out["psa9_last_sale_source"] = fmt(newest[1]), newest[0].isoformat(), newest[2]
    out["psa9_clean_last_sale_price"] = fmt(last[1]) if last else ""
    out["psa9_last_sale_unconfirmed"] = 1 if (newest and last and (newest[0], newest[1]) != (last[0], last[1]) and newest[0] >= last[0]) else 0
    ref9 = ref_price(s9, today)
    out["psa9_median_last_3"] = fmt(ref9)
    start = today - timedelta(days=30)
    vol = count_in(s9, start, today)
    out["psa9_volume_30d"] = vol
    out["psa9_price_chg_30d_pct"] = fmt(pct(ref9, ref_price(s9, start)) if vol > 0 else None)
    out["psa9_sales_total"] = len(s9)
    out["psa9_to_psa10_ratio"] = f"{ref9 / ref10:.4f}" if (ref9 and ref10) else ""
    return out


def norm_grade(s):
    """'10' -> '10.0', so a listing row's grade matches LIVE_CHECK_COLS; anything else as is."""
    try:
        return f"{float(s):.1f}"
    except (TypeError, ValueError):
        return s or ""


def load_live(store):
    """{(asset_id, grade): [listing rows]} from history/live_listings.csv (absent before
    2026-09-24; PSA 10 rows only until the scraper runs with --also-listings)."""
    path = store / "live_listings.csv"
    out = defaultdict(list)
    if path.exists():
        for r in read_csv(path):
            out[(r["asset_id"], norm_grade(r["grade"]))].append(r)
    return out


def to_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def running_auctions(listings):
    """The auction rows with an end time, soonest first."""
    return sorted((r for r in listings if r["listing_type"] == "AUCTION" and r["end_date"]), key=lambda r: r["end_date"])


def lowest_bin(listings):
    """The cheapest priced Buy It Now row, or None."""
    bins = [r for r in listings if r["listing_type"] != "AUCTION" and to_float(r["buy_it_now_price"])]
    return min(bins, key=lambda r: to_float(r["buy_it_now_price"])) if bins else None


def live_cols(checked_at, listings, prefix=""):
    """The live-listing columns for one card (see the module doc). prefix="psa9_" names the
    PSA 9 set (PSA9_LIVE_COLS): same rules, fed the card's PSA 9 rows and PSA 9 check time."""
    out = {c: "" for c in LIVE_COLS}
    if not checked_at:
        return {prefix + c: v for c, v in out.items()}
    out["listings_checked_at"] = checked_at
    auctions = running_auctions(listings)
    out["live_auction_count"] = len(auctions)
    if auctions:
        nxt = auctions[0]
        out["next_auction_end"] = nxt["end_date"]
        out["next_auction_bid"] = fmt(to_float(nxt["current_bid"]))
        out["next_auction_bid_count"] = nxt["bid_count"]
        out["next_auction_source"] = nxt["source"]
        out["next_auction_url"] = nxt["url"]
        out["last_auction_end"] = auctions[-1]["end_date"]
    low = lowest_bin(listings)
    if low:
        out["lowest_bin_price"] = fmt(to_float(low["buy_it_now_price"]))
        out["lowest_bin_source"] = low["source"]
        out["lowest_bin_url"] = low["url"]
    return {prefix + c: v for c, v in out.items()}


def listing_age_cols(checked_at, listings, ref, prefix=""):
    """lowest_bin_first_seen and next_auction_bid_suspect for one card and grade (see the
    module doc): the first-seen day of the row live_cols picks as the lowest BIN, and the
    next auction's bid against `ref` (the grade's clean reference price, None = unknown)."""
    out = {"lowest_bin_first_seen": "", "next_auction_bid_suspect": ""}
    if checked_at:
        low = lowest_bin(listings)
        if low:
            out["lowest_bin_first_seen"] = low.get("first_seen", "")
        auctions = running_auctions(listings)
        bid = to_float(auctions[0]["current_bid"]) if auctions else None
        if bid is not None and ref:
            out["next_auction_bid_suspect"] = 1 if bid > SUSPECT_BID_MULT * ref else 0
    return {prefix + c: v for c, v in out.items()}


def write_recent(recent_dir, raw_sales, sales):
    recent_dir.mkdir(parents=True, exist_ok=True)
    for old_f in recent_dir.glob("*.csv"):
        old_f.unlink()
    rshards = defaultdict(list)
    n_recent = 0
    for aid, lst in raw_sales.items():
        clean_keys = {(t[0], t[1]) for t in sales.get(aid, [])}
        lst.sort(reverse=True)
        for sd, price, src, stype, status, url in lst[:RECENT_N]:
            outlier = 1 if (status == "ok" and (sd, price) not in clean_keys) else 0
            rshards[aid[:2]].append([aid, sd.isoformat(), fmt(price), src, stype, status, outlier, url])
            n_recent += 1
    for shard, rows_ in rshards.items():
        with open(recent_dir / f"{shard}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(RECENT_COLS)
            w.writerows(rows_)
    return n_recent


def write_clean_sales(clean_dir, by_grade):
    """latest/clean_sales/<YYYY-MM>.csv + manifest.json (see the module doc).
    by_grade = {"10": clean sales by asset, "9": ...}, each list of (date, price, source)
    ascending. Returns (months, sales)."""
    clean_dir.mkdir(parents=True, exist_ok=True)
    months = defaultdict(lambda: defaultdict(list))     # "YYYY-MM" -> (asset_id, grade) -> ["DD:price"]
    for grade, sales in by_grade.items():
        for aid, lst in sales.items():
            for sd, price, _ in lst:
                months[sd.isoformat()[:7]][(aid, grade)].append(f"{sd.day}:{fmt(price)}")
    manifest, n_sales = {}, 0
    for month in sorted(months):
        lines = [",".join(CLEAN_SALES_COLS)]
        n = 0
        for (aid, grade), v in sorted(months[month].items()):
            lines.append(f"{aid},{grade},{' '.join(v)}")
            n += len(v)
        data = ("\n".join(lines) + "\n").encode("utf-8")
        (clean_dir / f"{month}.csv").write_bytes(data)
        manifest[month] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "sales": n}
        n_sales += n
    for old in clean_dir.glob("*.csv"):
        if old.stem not in manifest:
            old.unlink()
    with open(clean_dir / "manifest.json", "w") as f:
        json.dump({"columns": CLEAN_SALES_COLS, "sales": "space-separated day-of-month:price, ascending",
                   "months": manifest}, f, indent=1)
        f.write("\n")
    return len(manifest), n_sales


def build(store=HERE, out=LATEST):
    assets = {a["asset_id"]: a for a in read_csv(store / "assets.csv")}
    daily, days = load_daily(store)
    today = d(days[-1])
    by_grade = load_sales(store)
    sales, n_sales, outliers, raw_sales, held_high = by_grade["10.0"]
    sales9, n_sales9, _, raw_sales9, _ = by_grade["9.0"]
    print(f"{len(assets)} assets, {len(days)} daily files ({days[0]}..{days[-1]}), {n_sales} PSA 10 sales, {n_sales9} PSA 9 sales")

    live = load_live(store)
    # Newest numbers per asset, the first day each asset appears, and per grade the newest
    # successful live-listings check (a run whose check failed, or that never asked at that
    # grade, leaves the column blank).
    latest_row, first_day, checked_at = {}, {}, {g: {} for g in LIVE_CHECK_COLS}
    for day in days:
        for aid, row in daily[day].items():
            latest_row[aid] = (day, row)
            first_day.setdefault(aid, day)
            for g, col in LIVE_CHECK_COLS.items():
                if row.get(col):
                    checked_at[g][aid] = max(checked_at[g].get(aid, ""), row[col])
    # Newest run that also pulled PSA 9 sales, per asset (--also-grade 9).
    psa9_day = {}
    for day in days:
        for aid, row in daily[day].items():
            if "9.0" in (row.get("extra_grades") or "").split():
                psa9_day[aid] = day
    # Newest daily file at least 30 days old, per asset.
    cutoff30 = (today - timedelta(days=30)).isoformat()
    pop_30 = {}
    for day in days:
        if day > cutoff30:
            break
        for aid, row in daily[day].items():
            p = to_int(row.get("pop_at_grade"))
            if p is not None:
                pop_30[aid] = p

    rows = []
    filled = defaultdict(int)
    for aid in sorted(latest_row):
        day, num = latest_row[aid]
        ident = assets.get(aid, {})
        row = {"input": f"asset:{aid}", "asset_id": aid}
        for c in CARD_COLS:
            if c in row:
                continue
            row[c] = ident.get(c, "") if c in ident else num.get(c, "")

        s = sales.get(aid, [])
        last = s[-1] if s else None
        row["clean_last_sale_price"] = fmt(last[1]) if last else ""
        row["clean_last_sale_date"] = last[0].isoformat() if last else ""
        row["clean_last_sale_source"] = last[2] if last else ""
        row["outliers_excluded"] = outliers.get(aid, 0)
        # the literal newest unflagged sale, from the raw list (newest of status ok)
        raw_ok = [t for t in raw_sales.get(aid, []) if t[4] == "ok"]
        newest = max(raw_ok) if raw_ok else None
        row["last_sale_unconfirmed"] = 1 if (newest and last and (newest[0], newest[1]) != (last[0], last[1]) and newest[0] >= last[0]) else 0
        ref_now = ref_price(s, today)
        row["median_last_3"] = fmt(ref_now)
        # The change columns also count the sales the filter holds back on the
        # HIGH side (2026-10-01, Samay: held-back sales were leaving the cards
        # that moved out of the category %). Volume and every price level
        # (median_last_3, cap_price, clean_*) stay on clean sales only.
        hh = held_high.get(aid)
        counted = sorted(s + hh) if hh else s
        held_windows = set()
        for label, n in WINDOWS.items():
            start = today - timedelta(days=n)
            row[f"volume_{label}"] = count_in(s, start, today)
            chg, held = change_since(s, counted, start, today)
            row[f"price_chg_{label}_pct"] = fmt(chg)
            if chg is not None:
                filled[f"price_chg_{label}_pct"] += 1
            if held:
                held_windows.add(label)
        for label, n in EXTRA_WINDOWS.items():
            start = today - timedelta(days=n)
            row[f"volume_{label}"] = count_in(s, start, today)
            chg, held = change_since(s, counted, start, today)
            row[f"price_chg_{label}_pct"] = fmt(chg)
            if held:
                held_windows.add(label)
        row["chg_held_windows"] = " ".join(w for w in CAP_WINDOWS if w in held_windows)
        if held_windows:
            filled["chg_held_windows"] += 1
        row["cap_price"] = fmt(cap_price(s, today))
        for label, n in CAP_WINDOWS.items():
            row[f"cap_price_{label}_ago"] = fmt(cap_price(s, today - timedelta(days=n)))

        pop_now = to_int(num.get("pop_at_grade"))
        p30 = pop_30.get(aid)
        row["pop_30d_ago"] = fmt(p30)
        row["pop_chg_30d"] = fmt(pop_now - p30) if (pop_now is not None and p30 is not None) else ""
        mc = None
        ref_c = ref_price(counted, today)   # = ref_now unless the card has held-back high sales
        if pop_now and p30 and ref_c is not None and count_in(counted, today - timedelta(days=30), today) > 0:
            ref_30 = ref_price(counted, today - timedelta(days=30))
            mc = pct(ref_c * pop_now, ref_30 * p30) if ref_30 else None
        row["mkt_cap_chg_30d_pct"] = fmt(mc)
        if mc is not None:
            filled["mkt_cap_chg_30d_pct"] += 1
        row["sales_first_date"] = s[0][0].isoformat() if s else ""
        row["sales_total"] = len(s)
        row["history_days"] = (today - d(first_day[aid])).days
        row.update(psa9_cols(aid, num, psa9_day.get(aid), sales9.get(aid, []), raw_sales9.get(aid, []), ref_now, today))
        if row["psa9_last_sale_price"]:
            filled["psa9_last_sale_price"] += 1
        row.update(live_cols(checked_at["10.0"].get(aid), live.get((aid, "10.0"), [])))
        row.update(live_cols(checked_at["9.0"].get(aid), live.get((aid, "9.0"), []), prefix="psa9_"))
        row.update(listing_age_cols(checked_at["10.0"].get(aid), live.get((aid, "10.0"), []), ref_now))
        row.update(listing_age_cols(checked_at["9.0"].get(aid), live.get((aid, "9.0"), []),
                                    to_float(row["psa9_median_last_3"]), prefix="psa9_"))
        for c in ("lowest_bin_first_seen", "psa9_lowest_bin_first_seen"):
            if row[c]:
                filled[c] += 1
        for c in ("next_auction_bid_suspect", "psa9_next_auction_bid_suspect"):
            if row[c] == 1:
                filled[c] += 1
        rows.append(row)

    out.mkdir(parents=True, exist_ok=True)
    with open(out / "cards.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CARD_COLS + DERIVED_COLS + PSA9_COLS + LIVE_COLS + PSA9_LIVE_COLS + CAP_COLS + HELD_COLS + LISTING_AGE_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    # Weekly PSA 10 sale series per asset, for real charts. Sharded into 256
    # files by the first two hex digits of the asset id (latest/series/ab.csv)
    # so a server can read one ~200 KB file per lookup instead of loading
    # ~60 MB of series into memory, and so a day's new sales touch only the
    # shards they belong to in git.
    n_series = 0
    series_dir = out / "series"
    series_dir.mkdir(exist_ok=True)
    for old in series_dir.glob("*.csv"):
        old.unlink()
    shards = defaultdict(list)
    for aid in sorted(sales):
        weeks = defaultdict(list)
        for sd, p, _ in sales[aid]:
            weeks[sd - timedelta(days=sd.weekday())].append(p)
        for wk in sorted(weeks):
            ps = weeks[wk]
            shards[aid[:2]].append([aid, wk.isoformat(), len(ps), fmt(statistics.median(ps)), fmt(min(ps)), fmt(max(ps))])
            n_series += 1
    for shard, rows_ in shards.items():
        with open(series_dir / f"{shard}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(SERIES_COLS)
            w.writerows(rows_)

    # Last RECENT_N sales per asset, newest first, flagged rows included with
    # their status, held-back rows marked outlier=1 — for the app's "last 10
    # sales" dropdown. Sharded like series/. PSA 9 gets the same files under
    # recent_sales_psa9/.
    n_recent = write_recent(out / "recent_sales", raw_sales, sales)
    n_recent9 = write_recent(out / "recent_sales_psa9", raw_sales9, sales9)
    # Every clean sale, by month, with a manifest (see the module doc).
    n_clean_months, n_clean = write_clean_sales(out / "clean_sales", {"10": sales, "9": sales9})

    summary = {
        "data_date": today.isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "daily_files": len(days),
        "first_daily": days[0],
        "assets": len(rows),
        "psa10_sales": n_sales,
        "outliers_excluded": sum(outliers.values()),
        "series_rows": n_series,
        "recent_sales_rows": n_recent,
        "psa9_sales": n_sales9,
        "psa9_recent_sales_rows": n_recent9,
        "clean_sales_months": n_clean_months,
        "clean_sales": n_clean,
        "rows_with_psa9_last_sale": filled["psa9_last_sale_price"],
        "rows_unconfirmed_last_sale": sum(1 for r in rows if r["last_sale_unconfirmed"] == 1),
        "rows_chg_counts_held_high": filled["chg_held_windows"],
        "rows_listings_checked": sum(1 for r in rows if r["listings_checked_at"]),
        "rows_with_live_auction": sum(1 for r in rows if r["live_auction_count"]),
        "rows_with_bin_listing": sum(1 for r in rows if r["lowest_bin_price"]),
        "rows_bin_first_seen_known": filled["lowest_bin_first_seen"],          # 2026-10-03
        "rows_next_auction_bid_suspect": filled["next_auction_bid_suspect"],
        "rows_psa9_bin_first_seen_known": filled["psa9_lowest_bin_first_seen"],
        "rows_psa9_next_auction_bid_suspect": filled["psa9_next_auction_bid_suspect"],
        "rows_psa9_listings_checked": sum(1 for r in rows if r["psa9_listings_checked_at"]),
        "rows_with_psa9_live_auction": sum(1 for r in rows if r["psa9_live_auction_count"]),
        "rows_with_psa9_bin_listing": sum(1 for r in rows if r["psa9_lowest_bin_price"]),
        "rows_with": {k: filled[k] for k in ("price_chg_30d_pct", "price_chg_90d_pct", "price_chg_1y_pct", "mkt_cap_chg_30d_pct")},
    }
    with open(out / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"wrote {out}/cards.csv ({len(rows)} rows), series/*.csv ({n_series} rows in {len(shards)} shards)")
    print("rows with a value:", summary["rows_with"])
    return summary


if __name__ == "__main__":
    build()
