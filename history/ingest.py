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
                                  asset, newest scraped_at wins. sales_fetched
                                  (2026-09-26) is 1 when that run asked alt.xyz
                                  for the card's sales and 0 when the scraper's
                                  --skip-unchanged-sales carried the sale summary
                                  over from the previous daily row; files from
                                  before the column read as fetched (blank).
    history/sales/<YYYY-MM>.csv   every PSA 10 sale ever seen (plus PSA 9 sales for
                                  cards scraped with --also-grade 9; the grade
                                  column tells them apart), bucketed by sale
                                  month, keyed by alt.xyz's transaction id
                                  (alt_tx_id, recorded from 2026-09-26; older
                                  rows are matched by URL, or URL+date+price for
                                  a listing that sold several times, and gain
                                  their id). A sale the run lists again takes
                                  the run's values: alt.xyz later re-dates or
                                  re-prices ~1% of sales and flags or settles
                                  0.4% (RELISTED / NOT_PAID / PENDING), and the
                                  clean price columns depend on those fields. A
                                  sale re-dated into another month moves there;
                                  one alt.xyz no longer lists under a URL it
                                  still lists is dropped. Only
                                  month files with a change are rewritten
                                  (merge_sales, 2026-09-26; before that the
                                  store was append-only and never saw a flip).
    history/last_run.json         the newest nightly's account of itself (the run's
                                  run_stats.json, written by alt_scraper.py and
                                  nightly/run_stats.py: cards asked / ok / failed /
                                  carried, listings checks asked / failed, the Alt and
                                  Fanatics pulls, the gate verdict, start / end UTC),
                                  copied here by ingest and published by metrics.py as
                                  latest/summary.json `last_run` (2026-10-17)
    history/live_listings.csv     what is for sale RIGHT NOW, not history: every
                                  live auction plus the cheapest Buy It Now
                                  listing per asset and grade (PSA 10 always; PSA 9
                                  too once the scraper runs with --also-listings),
                                  from the run's listings.csv. An asset's rows at a
                                  grade are replaced whenever a run checked it at
                                  that grade (listings_checked_at set for PSA 10,
                                  psa9_listings_checked_at for PSA 9), so a card
                                  that sold out drops to zero rows; a run that
                                  checked a card at one grade only leaves its rows
                                  at the other grade alone, and assets a run
                                  didn't check keep their older rows (a failed
                                  listings request leaves the check time blank,
                                  so the last good snapshot stands).
                                  end_date and checked_at are UTC, "+00:00" (utc_iso;
                                  alt.xyz gives CardHobby ends in US Eastern and the
                                  scraper converts them since 2026-10-17, ingest
                                  normalises what it writes, and auction ends are
                                  compared as instants).
                                  Auctions that ended before the run's newest check
                                  are pruned; a Buy It Now not re-seen for
                                  BIN_MAX_AGE_DAYS is dropped. Note alt.xyz keeps
                                  ended BINs in its own feed for months and re-serves
                                  them every check, so this only catches BINs alt
                                  has itself dropped. Other BIN listings stay in the
                                  run folder only, to keep the daily git diff small.
                                  first_seen = the run day a listing was first
                                  recorded here (2026-10-03), kept while alt.xyz
                                  keeps showing it: the only clue to a BIN that has
                                  been "live" for months (a Kyogre Gold Star BIN
                                  returned as live on 2026-09-25 had ended June 16).
                                  Only the cheapest BIN per asset+grade is stored,
                                  so a BIN that stops being the cheapest and later
                                  is again starts its age over.
                                  A listings refresh between nightlies
                                  (--listings-only, 2026-10-03) replaces rows the
                                  same way and moves the refreshed cards' check
                                  times forward in their newest daily row.
                                  Rows with source "Alt" (2026-10-06) are alt.xyz's
                                  OWN auctions and fixed-price listings, which the
                                  per-card answer above never includes. They come
                                  from one bulk pull of every card at once
                                  (alt_scraper.py --alt-listings ->
                                  alt_listings.csv; the nightly and every listings
                                  refresh) and are replaced all together by the
                                  next complete pull: every running Alt auction,
                                  plus the cheapest Alt fixed-price listing per
                                  asset and grade. The per-card replacement leaves
                                  them alone, and a run without a pull keeps the
                                  last one (auction end / BIN age rules still apply).
                                  A nightly folds <run>/alt_listings.csv when it is
                                  there; a refresh passes --alt-listings FILE.
    history/ebay_checks.csv       what eBay said about each eBay Buy It Now it was
                                  asked about (ebay_verify.py, 2026-10-06): live /
                                  ended / gone and when. Dead listings are dropped
                                  from live_listings.csv and skipped here when
                                  alt.xyz re-serves them, so a card's next cheapest
                                  BIN takes their place.
                                  Rows with via "fanatics" (2026-10-15) come straight from
                                  Fanatics Collect's own Buy Now marketplace index
                                  (alt_scraper.py --fanatics-listings -> fanatics_listings.csv,
                                  with Fanatics' permission; the nightly), matched to the
                                  feed's cards by fanatics_match.py: the cheapest Fanatics
                                  Buy Now per asset and grade, replaced all together by the
                                  next complete pull (ingest_fanatics). That pull is every
                                  live Pokemon PSA 9 / PSA 10 Buy Now on Fanatics, so it
                                  also settles alt.xyz's mirrored Fanatics Buy Now rows: one
                                  whose listing is not in it (Sold, withdrawn, or not the
                                  Pokemon card alt.xyz says) is dropped; one that is live but
                                  the matcher could not place keeps alt.xyz's card with
                                  Fanatics' current price and time; one the matcher placed
                                  is replaced by the direct row (on the matched card).
    history/fanatics_checks.csv   what Fanatics Collect's index said about each Fanatics
                                  Buy Now listing alt.xyz served us (ingest_fanatics,
                                  2026-10-15): gone (Sold or no longer listed as a Pokemon
                                  PSA 9/10 Buy Now), live again, or crossed (the 236 ids of
                                  2026-10-10 whose page is a sports card: alt.xyz's mirror
                                  had attached another item's listing to a Pokemon card).
                                  gone/crossed ids are skipped when alt.xyz re-serves them,
                                  like eBay's dead ones; a pull that finds one live again
                                  flips it back.

Standard library only, like the scraper.
"""
import argparse
import csv
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
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
              "pop_at_grade_9", "extra_grades", "listings_checked_at", "psa9_listings_checked_at", "sales_fetched"]
SALE_COLS = ["asset_id", "date", "price", "grading_company", "grade", "source", "sale_type", "url",
             "label", "subject_to_change", "skipped_reason", "alt_tx_id"]
LIVE_COLS = ["asset_id", "grading_company", "grade", "listing_type", "source", "current_bid", "bid_count", "end_date",
             "buy_it_now_price", "url", "checked_at", "first_seen", "via"]
# first_seen (2026-10-03) = the run day a listing (asset, grade, url) was first recorded here,
# carried across runs while alt.xyz keeps showing it; blank = unknown (older rows were
# backfilled from this file's git history, analysis/backfill_listing_first_seen.py)
# via (2026-10-15) = where the row came from: blank = alt.xyz's per-card answer (or its own
# listings, source "Alt"); "fanatics" = Fanatics Collect's own marketplace index (ingest_fanatics)
VIA_FANATICS = "fanatics"
CHECK_COLS = ["listing_id", "status", "asset_id", "fanatics_title", "fanatics_price", "checked_at"]   # fanatics_checks.csv
DEAD_FANATICS = {"gone", "crossed"}
BIN_MAX_AGE_DAYS = 7   # a Buy It Now row survives this long without alt.xyz showing it again
# grade -> the cards.csv column with the UTC time of the run's believable live-listings check at that
# grade (blank = not asked, or the request failed); live rows are replaced per (asset, grade)
LIVE_CHECK_COLS = {"10.0": "listings_checked_at", "9.0": "psa9_listings_checked_at"}
ALT_SOURCE = "Alt"   # source of the rows from alt.xyz's own auctions / marketplace (ingest_alt)

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

    # --- sales/<month>.csv: keyed by alt_tx_id (URL rules for older rows); see merge_sales ---------
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
    # Dropping a stored sale ("alt.xyz no longer lists it") is only sound when the run
    # listed EVERY sale of the card (--max-sales 0, as the nightly does). A run cut short by
    # --max-sales N shows fewer rows than num_sales for some card: then nothing is dropped.
    n_rows = defaultdict(int)
    for rows in incoming.values():
        for s in rows:
            n_rows[(s["asset_id"], s.get("grade"))] += 1
    truncated = [r["asset_id"] for r in cards
                 if r.get("sales_fetched", "1") != "0" and to_int(r.get("num_sales")) is not None
                 and n_rows[(r["asset_id"], r.get("grade"))] < to_int(r["num_sales"])]
    if truncated:
        print(f"  sales: {len(truncated)} card(s) have fewer sale rows than num_sales (a --max-sales run?): "
              "no stored sale is dropped this time")
    stats = merge_sales(sales_dir, incoming, allow_drop=not truncated)
    print(f"  sales: {n_in} rows in run, {stats['sales_added']} new, {stats['sales_updated']} changed in place, "
          f"{stats['sales_moved']} moved to another month, {stats['sales_dropped']} dropped (no longer listed by alt.xyz), "
          f"{stats['sales_month_files_rewritten']} month files rewritten")

    live = ingest_live(run_dir, cards, store, day)
    if (run_dir / "alt_listings.csv").exists():
        live.update(ingest_alt(run_dir / "alt_listings.csv", store, day))
    if (run_dir / "fanatics_listings.csv").exists():
        live.update(ingest_fanatics(run_dir / "fanatics_listings.csv", store, day))
    live["last_run"] = copy_run_stats(run_dir, store)
    return {"assets": len(assets), "new_assets": new_assets, "daily_rows": len(daily), **stats, **live}


def copy_run_stats(run_dir, store):
    """The run's run_stats.json (alt_scraper.py / nightly/run_stats.py) -> history/last_run.json, for
    metrics.py to publish as latest/summary.json `last_run`. Returns whether there was one."""
    src = Path(run_dir) / "run_stats.json"
    if not src.exists():
        print("  last_run.json: run has no run_stats.json, left as is")
        return False
    dst = store / "last_run.json"
    fd, tmp = tempfile.mkstemp(prefix=dst.name + ".", dir=store)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(src.read_text(encoding="utf-8"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, dst)
    print(f"  last_run.json: the run's account copied from {src}")
    return True


def merge_sales(sales_dir, incoming, allow_drop=True):
    """Fold {month: [sale rows]} into sales/<month>.csv (see the module doc).

    Two facts drive this. (1) alt.xyz keeps editing sales after it first lists them: about
    1% of rows later get a different date or price (a re-attributed lot), 0.4% are flagged
    RELISTED / NOT_PAID or go from PENDING to settled, and metrics.py builds the clean price
    and change columns from exactly those fields. (2) A URL is not one sale: an eBay
    multi-quantity Buy It Now listing keeps its item id while it sells the same card again
    and again, sometimes for a year (54k of the 3.9M URLs in the 2026-09-26 run carry more
    than one sale, one of them 150). Until 2026-09-26 the store kept one row per URL per
    month and never changed it, so repeat sales were dropped and edits never arrived.

    The key is alt.xyz's own id for the sale record (alt_tx_id, recorded by the scraper
    from 2026-09-26). Rows stored before that have none, so for a stored row r whose URL
    the run lists (grade and company agreeing; a URL under another grade is a mislabeled
    twin and is left alone, as before):
      - r has an id -> the run row with that id is the same sale: replaced if anything
        changed, dropped here if the run now dates it into another month (it is added
        there), dropped if the run no longer has that id at all;
      - r has no id and the URL carries ONE sale in the run and ONE in the store -> the
        same sale, handled the same way (and it gains its id);
      - r has no id otherwise (a repeat-sale listing) -> matched to the run row with the
        same date and price in this month, which gives it its id; none -> dropped: alt.xyz
        no longer lists a sale like it under that URL (a re-dated twin, or a sale it
        removed).
    Run rows nothing matched are added. With allow_drop=False (ingest passes it when a run
    shows fewer sale rows than num_sales for some card, i.e. --max-sales cut it short) the
    two "dropped" cases keep the row instead. Stored rows for URLs the run does not list at all
    are never touched (the card was not fetched, or the sale is gone from alt.xyz along
    with its URL). Every month file is read twice (URL counts, then the merge) but only
    changed ones are rewritten."""
    def fine_key(s):
        return (s.get("date") or "", s.get("price") or "")

    def same_grade(a, b):
        return (a.get("grade"), a.get("grading_company")) == (b.get("grade"), b.get("grading_company"))

    inc_rows = defaultdict(list)                      # URL -> the run's rows for it, any month
    by_id = {}                                        # alt_tx_id -> run row
    for rows in incoming.values():
        for s in rows:
            inc_rows[sale_key(s)].append(s)
            if s.get("alt_tx_id"):
                by_id.setdefault(s["alt_tx_id"], s)
    months = sorted(set(incoming) | {p.stem for p in sales_dir.glob("*.csv")})
    store_count = defaultdict(int)                    # URL -> rows in the whole store, for URLs the run lists
    for month in months:
        for r in read_csv(sales_dir / f"{month}.csv"):
            if sale_key(r) in inc_rows:
                store_count[sale_key(r)] += 1

    added = updated = moved = dropped = rewritten = 0
    for month in months:
        path = sales_dir / f"{month}.csv"
        existing = read_csv(path)
        this_month = incoming.get(month, [])
        consumed = set()                              # id() of run rows matched to a stored row in this month
        kept, changed = [], False

        def take(r, s):
            """r is the stored row for run row s (same month): keep the run's version if anything differs."""
            nonlocal updated, changed
            consumed.add(id(s))
            if any((r.get(c) or "") != (s.get(c) or "") for c in SALE_COLS):
                kept.append(s)
                updated += 1
                changed = True
            else:
                kept.append(r)

        for r in existing:
            k = sale_key(r)
            runs = [s for s in inc_rows.get(k, []) if same_grade(s, r)]
            if k not in inc_rows:
                kept.append(r)                        # URL not in this run: card not fetched, or sale and URL gone
                continue
            if not runs:
                kept.append(r)                        # the same URL only under another grade: the stored row stands
                continue
            if r.get("alt_tx_id"):
                s = by_id.get(r["alt_tx_id"])
                if s is None or not same_grade(s, r):
                    if allow_drop:
                        dropped += 1                  # alt.xyz no longer has this sale record
                        changed = True
                    else:
                        kept.append(r)
                elif (s.get("date") or "")[:7] != month:
                    moved += 1                        # re-dated into another month; added there
                    changed = True
                else:
                    take(r, s)
                continue
            if len(runs) == 1 and store_count[k] == 1:
                s = runs[0]                           # one sale here and there: the same sale, now with its id
                if (s.get("date") or "")[:7] != month:
                    moved += 1
                    changed = True
                else:
                    take(r, s)
                continue
            s = next((s for s in runs if id(s) not in consumed and (s.get("date") or "")[:7] == month
                      and fine_key(s) == fine_key(r)), None)
            if s is None:
                if allow_drop:
                    dropped += 1                      # no sale like it under that URL any more: a twin or removed
                    changed = True
                else:
                    kept.append(r)
            else:
                take(r, s)
        twin_urls = {sale_key(r) for r in existing if sale_key(r) in inc_rows
                     and not any(same_grade(s, r) for s in inc_rows[sale_key(r)])}
        # The same sale twice in one run (same id, or same URL + date + price): one row.
        def run_key(s):
            return ("id", s["alt_tx_id"]) if s.get("alt_tx_id") else ("fk", sale_key(s), fine_key(s))
        seen = {run_key(s) for s in this_month if id(s) in consumed}
        fresh = []
        for s in this_month:
            if id(s) in consumed or sale_key(s) in twin_urls or run_key(s) in seen:
                continue
            seen.add(run_key(s))
            fresh.append(s)
        if fresh or changed:
            merged = kept + fresh
            merged.sort(key=lambda s: (s["date"], s["asset_id"], s["url"], s["price"], s.get("alt_tx_id") or ""))
            write_csv_atomic(path, SALE_COLS, merged)
            added += len(fresh)
            rewritten += 1
    return {"sales_added": added, "sales_updated": updated, "sales_moved": moved, "sales_dropped": dropped,
            "sales_month_files_rewritten": rewritten}


def to_int(s):
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def to_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def norm_grade(s):
    """'10' -> '10.0', so a listing row's grade matches LIVE_CHECK_COLS; anything else as is."""
    try:
        return f"{float(s):.1f}"
    except (TypeError, ValueError):
        return s or ""


FANATICS_RX = re.compile(r"fanaticscollect\.com/buy-now/([0-9a-f-]{36})")


def utc_iso(s):
    """An ISO-8601 time as the store writes it: UTC, "+00:00", whole seconds. alt.xyz gives
    CardHobby auction ends in US Eastern ("2026-10-21T08:37:55-04:00"; 834 of 22,218 auction
    rows on 2026-10-17) and everything else in UTC, and the pipeline compared the strings, so
    those auctions were pruned up to 4 h early and sorted wrong. A naive time is taken as UTC;
    blank stays blank; a string that is not a time comes back unchanged."""
    if not s:
        return ""
    try:
        t = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return s
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def when(s):
    """The instant an ISO-8601 string names, for comparing times that may carry different
    offsets (None for blank or unparseable; a naive time is UTC)."""
    if not s:
        return None
    try:
        t = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def live_sorted(rows):
    """live_listings.csv rows ready to write: every end_date in the store's UTC form (utc_iso),
    in the file's order."""
    for r in rows:
        r["end_date"] = utc_iso(r.get("end_date") or "")
    rows.sort(key=lambda r: (r["asset_id"], r["grade"], r["listing_type"], r["end_date"] or "", r["url"] or ""))
    return rows


def normalise_live(store):
    """One pass over history/live_listings.csv rewriting every end_date as UTC (utc_iso); the
    one-off for the rows stored before 2026-10-17. Every ingest does the same for what it
    writes, so this is only needed once. Returns how many cells changed."""
    live_path = Path(store) / "live_listings.csv"
    rows = read_csv(live_path)
    changed = sum(1 for r in rows if utc_iso(r.get("end_date") or "") != (r.get("end_date") or ""))
    write_csv_atomic(live_path, LIVE_COLS, live_sorted(rows))
    print(f"  live_listings.csv: {changed} end_date cell(s) rewritten as UTC in {len(rows)} rows")
    return changed


def fanatics_id(url):
    """The Fanatics Collect Buy Now listing id in a URL, or None for any other listing."""
    m = FANATICS_RX.search(url or "")
    return m.group(1) if m else None


def dead_fanatics_ids(store):
    """Fanatics Buy Now listing ids Fanatics' own index says are gone, or that alt.xyz attached to the
    wrong card (history/fanatics_checks.csv, see the module doc): skipped when alt.xyz re-serves them."""
    return {r["listing_id"] for r in read_csv(store / "fanatics_checks.csv") if r.get("status") in DEAD_FANATICS}


def ingest_live(run_dir, cards, store, day=""):
    """Fold the run's listings.csv into history/live_listings.csv (see the module doc).
    `day` is the run day written as first_seen on listings not recorded before."""
    listings_path = run_dir / "listings.csv"
    if not listings_path.exists():          # a run from before 2026-09-24 has no listings
        print("  live_listings.csv: run has no listings.csv, left as is")
        return {"live_assets_checked": 0}
    # (asset, grade) -> when this run checked that grade's listings. A run that checked a card
    # at PSA 10 only (no --also-listings, zero PSA 9 copies, or the PSA 9 request failed) must
    # leave the card's PSA 9 rows alone, and vice versa.
    checked = {(r["asset_id"], g): r[col] for r in cards for g, col in LIVE_CHECK_COLS.items() if r.get(col)}
    incoming = defaultdict(list)
    for r in read_csv(listings_path):
        key = (r["asset_id"], norm_grade(r["grade"]))
        if key in checked:
            incoming[key].append(r)

    live_path = store / "live_listings.csv"
    stored = read_csv(live_path)
    # When each listing we already hold was first recorded, so a re-seen listing keeps its age.
    seen = {(r["asset_id"], norm_grade(r["grade"]), r["url"]): r.get("first_seen", "") for r in stored}
    # Alt's own listings and the direct Fanatics rows never come in a per-card answer: they are
    # replaced by ingest_alt / ingest_fanatics only.
    kept = [r for r in stored if r["source"] == ALT_SOURCE or r.get("via") == VIA_FANATICS
            or (r["asset_id"], norm_grade(r["grade"])) not in checked]
    # Fanatics Buy Nows Fanatics' own index says are gone, or that alt.xyz pairs with the wrong card
    # (history/fanatics_checks.csv): out of what we hold, and skipped below when re-served. A re-served
    # listing we already hold straight from Fanatics (via "fanatics") is skipped too: that row has
    # Fanatics' own price and card.
    dead = dead_fanatics_ids(store)
    direct = {fanatics_id(r["url"]) for r in stored if r.get("via") == VIA_FANATICS} - {None}
    kept, skipped_dead = [r for r in kept if fanatics_id(r["url"]) not in dead], sum(1 for r in kept if fanatics_id(r["url"]) in dead)
    # eBay listings eBay itself says have ended (history/ebay_checks.csv, ebay_verify.py): alt.xyz
    # keeps re-serving them, so they are skipped here and the next cheapest BIN can be the one kept.
    from ebay_verify import dead_urls, item_id   # noqa: E402  (ebay_verify imports this module)
    dead_ebay = dead_urls(store)
    fresh, skipped_ebay, skipped_direct = [], 0, 0
    for rows in incoming.values():
        fresh += [r for r in rows if r["listing_type"] == "AUCTION"]
        bins = [r for r in rows if r["listing_type"] != "AUCTION" and to_float(r["buy_it_now_price"])]
        live_bins = [r for r in bins if item_id(r["url"]) not in dead_ebay]
        skipped_ebay += len(bins) - len(live_bins)
        live_bins, n = [r for r in live_bins if fanatics_id(r["url"]) not in dead], len(live_bins)
        skipped_dead += n - len(live_bins)
        live_bins, n = [r for r in live_bins if fanatics_id(r["url"]) not in direct], len(live_bins)
        skipped_direct += n - len(live_bins)
        if live_bins:
            fresh.append(min(live_bins, key=lambda r: to_float(r["buy_it_now_price"])))
    for r in fresh:
        r["first_seen"] = seen.get((r["asset_id"], norm_grade(r["grade"]), r["url"])) or day
        r["via"] = ""
    # An auction whose end is before the newest check anywhere in this run has certainly
    # ended; the rows of assets this run didn't check are where such leftovers live. Compared
    # as instants, not strings: CardHobby's ends arrive with a -04:00 offset (utc_iso).
    horizon = max(checked.values(), default="")
    horizon_t = when(horizon)
    rows = [r for r in kept + fresh
            if not (r["listing_type"] == "AUCTION" and horizon_t and when(r["end_date"]) and when(r["end_date"]) < horizon_t)]
    pruned = len(kept) + len(fresh) - len(rows)
    # A Buy It Now has no end date, and alt.xyz keeps one in its live feed long after it
    # sold or was pulled (a Kyogre Gold Star BIN returned as live on 2026-09-25 had ended
    # on June 16) — and re-serves it on every check, so this rule cannot catch that case.
    # It does bound how long a BIN survives for a card whose listings request failed or
    # that dropped out of the scope: not re-seen within BIN_MAX_AGE_DAYS of this run's
    # newest check, it goes.
    aged = 0
    if horizon:
        cutoff = (datetime.fromisoformat(horizon) - timedelta(days=BIN_MAX_AGE_DAYS)).isoformat()
        before = len(rows)
        rows = [r for r in rows if r["listing_type"] == "AUCTION" or not r["checked_at"] or r["checked_at"] >= cutoff]
        aged = before - len(rows)
    rows = live_sorted(rows)
    write_csv_atomic(live_path, LIVE_COLS, rows)
    n_auc = sum(1 for r in rows if r["listing_type"] == "AUCTION")
    n_assets = len({aid for aid, _ in checked})
    by_grade = {g: sum(1 for _, gg in checked if gg == g) for g in LIVE_CHECK_COLS}
    at = ", ".join(f"{n} at PSA {g}" for g, n in by_grade.items() if n)
    print(f"  live_listings.csv: {n_assets} assets checked this run{f' ({at})' if at else ''}, {len(rows)} rows "
          f"({n_auc} auctions, {len(rows) - n_auc} cheapest-BIN), {pruned} ended auctions pruned, "
          f"{aged} BIN(s) unseen for {BIN_MAX_AGE_DAYS}+ days dropped, {skipped_ebay} BIN(s) eBay says have ended skipped, "
          f"{skipped_dead} Fanatics BIN(s) gone or crossed dropped, {skipped_direct} re-served Fanatics BIN(s) already held direct")
    return {"live_assets_checked": n_assets, "live_checks": by_grade, "live_rows": len(rows), "bins_aged_out": aged,
            "bins_ebay_dead_skipped": skipped_ebay, "bins_fanatics_dead": skipped_dead, "bins_fanatics_direct_dup": skipped_direct}


def ingest_alt(path, store, day=""):
    """Replace every source "Alt" row of history/live_listings.csv with this complete pull of
    alt.xyz's own listings (alt_scraper.py --alt-listings; the file is only written when the
    pull read the whole index). Keeps every running auction and the cheapest fixed-price
    listing per asset and grade, like ingest_live; first_seen carries over by (asset, grade, url)."""
    live_path = store / "live_listings.csv"
    stored = read_csv(live_path)
    seen = {(r["asset_id"], norm_grade(r["grade"]), r["url"]): r.get("first_seen", "")
            for r in stored if r["source"] == ALT_SOURCE}
    other = [r for r in stored if r["source"] != ALT_SOURCE]
    incoming = read_csv(Path(path))
    fresh = [r for r in incoming if r["listing_type"] == "AUCTION"]
    cheapest = {}
    for r in incoming:
        if r["listing_type"] != "AUCTION" and to_float(r["buy_it_now_price"]):
            key = (r["asset_id"], norm_grade(r["grade"]))
            if key not in cheapest or to_float(r["buy_it_now_price"]) < to_float(cheapest[key]["buy_it_now_price"]):
                cheapest[key] = r
    fresh += cheapest.values()
    for r in fresh:
        r["grade"] = norm_grade(r["grade"])
        r["first_seen"] = seen.get((r["asset_id"], r["grade"], r["url"])) or day
    gone = len(seen) - sum(1 for r in fresh if (r["asset_id"], r["grade"], r["url"]) in seen)
    rows = other + [{c: r.get(c, "") for c in LIVE_COLS} for r in fresh]
    rows = live_sorted(rows)
    write_csv_atomic(live_path, LIVE_COLS, rows)
    n_auc = sum(1 for r in fresh if r["listing_type"] == "AUCTION")
    print(f"  live_listings.csv: alt.xyz's own listings replaced: {n_auc} auctions + {len(fresh) - n_auc} cheapest "
          f"fixed-price on {len({r['asset_id'] for r in fresh})} assets ({len(incoming)} in the pull; "
          f"{len(seen) - gone} kept their first_seen, {gone} earlier Alt row(s) gone)")
    return {"alt_rows": len(fresh), "alt_auctions": n_auc}


def ingest_fanatics(path, store, day=""):
    """Fold a complete pull of Fanatics Collect's Buy Now marketplace (alt_scraper.py --fanatics-listings
    -> fanatics_listings.csv: every live Pokemon PSA 9 / PSA 10 listing, matched or not; the file is only
    written when the pull was complete) into history/live_listings.csv, replacing every Fanatics Buy Now
    row we hold (see the module doc):
      - matched listings: the cheapest per asset and grade becomes a row with via "fanatics" (first_seen
        carried by asset, grade, url); alt.xyz's mirrored row for the same listing goes, whatever card
        alt.xyz had put it on;
      - a mirrored row whose listing is live but unmatched keeps alt.xyz's card, with Fanatics' price and
        check time;
      - a mirrored or direct row whose listing is not in the pull (Sold, withdrawn, or not a Pokemon
        PSA 9/10 Buy Now any more) is dropped and recorded as gone in fanatics_checks.csv, so ingest_live
        skips it when alt.xyz serves it again; an id recorded gone/crossed that is live again is flipped."""
    live_path = store / "live_listings.csv"
    stored = read_csv(live_path)
    pull = read_csv(Path(path))
    live = {fanatics_id(r["url"]): r for r in pull if fanatics_id(r["url"])}
    checked_at = max((r.get("checked_at") or "" for r in pull), default="") or day
    grades = {norm_grade(r["grade"]) for r in pull}      # a pull of one grade leaves the other grade's rows alone
    old = [r for r in stored if fanatics_id(r["url"]) and norm_grade(r["grade"]) in grades]
    other = [r for r in stored if not (fanatics_id(r["url"]) and norm_grade(r["grade"]) in grades)]
    seen = {(r["asset_id"], norm_grade(r["grade"]), r["url"]): r.get("first_seen", "") for r in old}
    gone, mirror, reattributed, unmatched_direct = [], [], 0, 0
    for r in old:
        p = live.get(fanatics_id(r["url"]))
        if p is None:
            gone.append(r)
        elif p["asset_id"]:                       # placed by the matcher: the direct row below stands for it
            if p["asset_id"] != r["asset_id"] or norm_grade(p["grade"]) != norm_grade(r["grade"]):
                reattributed += 1
        elif r.get("via") == VIA_FANATICS:        # placed last time, not this time (the card list changed)
            unmatched_direct += 1
        else:                                     # alt.xyz's card, Fanatics' price and time
            mirror.append(dict(r, buy_it_now_price=p["buy_it_now_price"], checked_at=p["checked_at"] or r["checked_at"]))
    cheapest = {}
    for p in pull:
        if p.get("asset_id") and to_float(p.get("buy_it_now_price")):
            key = (p["asset_id"], norm_grade(p["grade"]))
            if key not in cheapest or to_float(p["buy_it_now_price"]) < to_float(cheapest[key]["buy_it_now_price"]):
                cheapest[key] = p
    direct = []
    for p in cheapest.values():
        row = {c: p.get(c, "") for c in LIVE_COLS}
        row["grade"], row["via"] = norm_grade(p["grade"]), VIA_FANATICS
        row["first_seen"] = seen.get((row["asset_id"], row["grade"], row["url"])) or day
        direct.append(row)
    rows = other + mirror + direct
    rows = live_sorted(rows)
    write_csv_atomic(live_path, LIVE_COLS, rows)
    # verdicts: gone ids (new or re-confirmed), and dead ids that are live again
    checks_path = store / "fanatics_checks.csv"
    checks = {r["listing_id"]: r for r in read_csv(checks_path)}
    for r in gone:
        fid = fanatics_id(r["url"])
        prev = checks.get(fid, {})
        checks[fid] = {"listing_id": fid, "status": "gone", "asset_id": r["asset_id"],
                       "fanatics_title": prev.get("fanatics_title", ""), "fanatics_price": r["buy_it_now_price"],
                       "checked_at": checked_at}
    relisted = 0
    for fid, c in checks.items():
        if fid in live and c["status"] != "live":
            relisted += 1
            c.update(status="live", fanatics_title=live[fid].get("title", ""), fanatics_price=live[fid]["buy_it_now_price"],
                     checked_at=checked_at)
    write_csv_atomic(checks_path, CHECK_COLS, sorted(checks.values(), key=lambda c: c["listing_id"]))
    n_matched = sum(1 for p in pull if p.get("asset_id"))
    print(f"  live_listings.csv: Fanatics Collect's Buy Nows replaced: {len(pull)} live Pokemon PSA 9/10 listings in the pull, "
          f"{n_matched} matched -> {len(direct)} cheapest direct row(s) on {len({r['asset_id'] for r in direct})} cards; "
          f"of {len(old)} Fanatics Buy Now row(s) held before: {len(gone)} gone (Sold / withdrawn / not Pokemon PSA 9-10) dropped, "
          f"{len(old) - len(gone) - len(mirror) - unmatched_direct} replaced by a direct row ({reattributed} on another card or grade), "
          f"{len(mirror)} unmatched kept on alt.xyz's card with Fanatics' price, {unmatched_direct} direct row(s) no longer matched dropped; "
          f"{relisted} dead id(s) live again")
    return {"fanatics_pull": len(pull), "fanatics_matched": n_matched, "fanatics_rows": len(direct), "fanatics_gone": len(gone),
            "fanatics_mirror_kept": len(mirror), "fanatics_reattributed": reattributed, "fanatics_relisted": relisted}


def ingest_listings_only(run_dir, day, store=HERE):
    """A listings refresh (alt_scraper.py --listings-only): fold the run's listings into
    history/live_listings.csv exactly as ingest_live does, and move the check time of each
    refreshed card forward in its newest daily row (listings_checked_at / psa9_listings_
    checked_at), which is where metrics.py reads the check time from. Nothing else in
    the daily files changes: no row is added or replaced, pops and sales stay as the
    night left them. So a daily row's check time can be later than its scraped_at."""
    run_dir = Path(run_dir)
    cards = read_csv(run_dir / "cards.csv")
    if not cards:
        sys.exit(f"no cards.csv in {run_dir}")
    live = ingest_live(run_dir, cards, store, day)
    newer = {}   # (asset_id, check column) -> this run's check time
    for r in cards:
        for col in LIVE_CHECK_COLS.values():
            if r.get(col):
                newer[(r["asset_id"], col)] = r[col]
    pending = {aid for aid, _ in newer}
    bumped = 0
    for path in sorted((store / "daily").glob("*.csv"), reverse=True):   # newest first
        if not pending:
            break
        rows = read_csv(path)
        hit = False
        for r in rows:
            aid = r["asset_id"]
            if aid not in pending:
                continue
            pending.discard(aid)
            for col in LIVE_CHECK_COLS.values():
                t = newer.get((aid, col))
                if t and t > (r.get(col) or ""):
                    r[col] = t
                    hit = True
                    bumped += 1
        if hit:
            write_csv_atomic(path, DAILY_COLS, rows)
    print(f"  listings refresh: {len(cards)} card(s) in run, {bumped} check time(s) moved forward in daily/, "
          f"{len(pending)} card(s) not in any daily file (ignored)")
    return {"refresh_cards": len(cards), "refresh_bumped": bumped, **live}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", nargs="?", help="folder holding cards.csv and sales.csv from one scrape")
    ap.add_argument("--date", help="run date YYYY-MM-DD (default: taken from the folder name)")
    ap.add_argument("--store", default=str(HERE), help="history store directory (default: this folder)")
    ap.add_argument("--listings-only", action="store_true",
                    help="the run is a listings refresh (alt_scraper.py --listings-only): update live_listings.csv "
                         "and the check times only, no daily rows, no sales")
    ap.add_argument("--alt-listings", metavar="FILE",
                    help="an alt_listings.csv (alt_scraper.py --alt-listings): replace alt.xyz's own listings in "
                         "live_listings.csv with it. On its own (no run_dir) that is all it does; needs --date")
    ap.add_argument("--fanatics-listings", metavar="FILE",
                    help="a fanatics_listings.csv (alt_scraper.py --fanatics-listings): replace the Fanatics Collect Buy Now "
                         "rows in live_listings.csv with it and record gone listings. On its own (no run_dir) that is all "
                         "it does; needs --date")
    ap.add_argument("--normalise-live", action="store_true",
                    help="rewrite every end_date in live_listings.csv as UTC (+00:00) and exit: the one-off for rows "
                         "stored before 2026-10-17, when CardHobby ends still carried -04:00")
    args = ap.parse_args()
    if args.normalise_live:
        normalise_live(Path(args.store))
        return
    if not args.run_dir:
        if not ((args.alt_listings or args.fanatics_listings) and args.date):
            ap.error("give a run_dir, or --alt-listings / --fanatics-listings FILE with --date")
        if args.alt_listings:
            ingest_alt(args.alt_listings, Path(args.store), args.date)
        if args.fanatics_listings:
            ingest_fanatics(args.fanatics_listings, Path(args.store), args.date)
        return
    day = args.date
    if not day:
        m = DATE_RX.search(Path(args.run_dir).resolve().name)
        if not m:
            sys.exit("folder name has no YYYY-MM-DD date; pass --date")
        day = m.group(0)
    if args.listings_only:
        ingest_listings_only(args.run_dir, day, Path(args.store))
    else:
        ingest(args.run_dir, day, Path(args.store))
    if args.alt_listings:
        ingest_alt(args.alt_listings, Path(args.store), day)
    if args.fanatics_listings:
        ingest_fanatics(args.fanatics_listings, Path(args.store), day)


if __name__ == "__main__":
    main()
