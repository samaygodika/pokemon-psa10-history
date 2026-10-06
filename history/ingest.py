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
              "pop_at_grade_9", "extra_grades", "listings_checked_at", "psa9_listings_checked_at", "sales_fetched"]
SALE_COLS = ["asset_id", "date", "price", "grading_company", "grade", "source", "sale_type", "url",
             "label", "subject_to_change", "skipped_reason", "alt_tx_id"]
LIVE_COLS = ["asset_id", "grading_company", "grade", "listing_type", "source", "current_bid", "bid_count", "end_date",
             "buy_it_now_price", "url", "checked_at", "first_seen"]
# first_seen (2026-10-03) = the run day a listing (asset, grade, url) was first recorded here,
# carried across runs while alt.xyz keeps showing it; blank = unknown (older rows were
# backfilled from this file's git history, analysis/backfill_listing_first_seen.py)
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
    return {"assets": len(assets), "new_assets": new_assets, "daily_rows": len(daily), **stats, **live}


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
    # Alt's own listings never come in a per-card answer: they are replaced by ingest_alt only.
    kept = [r for r in stored if r["source"] == ALT_SOURCE or (r["asset_id"], norm_grade(r["grade"])) not in checked]
    fresh = []
    for rows in incoming.values():
        fresh += [r for r in rows if r["listing_type"] == "AUCTION"]
        bins = [r for r in rows if r["listing_type"] != "AUCTION" and to_float(r["buy_it_now_price"])]
        if bins:
            fresh.append(min(bins, key=lambda r: to_float(r["buy_it_now_price"])))
    for r in fresh:
        r["first_seen"] = seen.get((r["asset_id"], norm_grade(r["grade"]), r["url"])) or day
    # An auction whose end is before the newest check anywhere in this run has certainly
    # ended; the rows of assets this run didn't check are where such leftovers live.
    horizon = max(checked.values(), default="")
    rows = [r for r in kept + fresh
            if not (r["listing_type"] == "AUCTION" and r["end_date"] and r["end_date"] < horizon)]   # both UTC ISO, same format
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
    rows.sort(key=lambda r: (r["asset_id"], r["grade"], r["listing_type"], r["end_date"] or "", r["url"] or ""))
    write_csv_atomic(live_path, LIVE_COLS, rows)
    n_auc = sum(1 for r in rows if r["listing_type"] == "AUCTION")
    n_assets = len({aid for aid, _ in checked})
    by_grade = {g: sum(1 for _, gg in checked if gg == g) for g in LIVE_CHECK_COLS}
    at = ", ".join(f"{n} at PSA {g}" for g, n in by_grade.items() if n)
    print(f"  live_listings.csv: {n_assets} assets checked this run{f' ({at})' if at else ''}, {len(rows)} rows "
          f"({n_auc} auctions, {len(rows) - n_auc} cheapest-BIN), {pruned} ended auctions pruned, "
          f"{aged} BIN(s) unseen for {BIN_MAX_AGE_DAYS}+ days dropped")
    return {"live_assets_checked": n_assets, "live_checks": by_grade, "live_rows": len(rows), "bins_aged_out": aged}


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
    rows.sort(key=lambda r: (r["asset_id"], r["grade"], r["listing_type"], r["end_date"] or "", r["url"] or ""))
    write_csv_atomic(live_path, LIVE_COLS, rows)
    n_auc = sum(1 for r in fresh if r["listing_type"] == "AUCTION")
    print(f"  live_listings.csv: alt.xyz's own listings replaced: {n_auc} auctions + {len(fresh) - n_auc} cheapest "
          f"fixed-price on {len({r['asset_id'] for r in fresh})} assets ({len(incoming)} in the pull; "
          f"{len(seen) - gone} kept their first_seen, {gone} earlier Alt row(s) gone)")
    return {"alt_rows": len(fresh), "alt_auctions": n_auc}


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
    args = ap.parse_args()
    if not args.run_dir:
        if not (args.alt_listings and args.date):
            ap.error("give a run_dir, or --alt-listings FILE with --date")
        ingest_alt(args.alt_listings, Path(args.store), args.date)
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


if __name__ == "__main__":
    main()
