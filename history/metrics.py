#!/usr/bin/env python3
"""Build latest/ from the history store: the file PokeSniper's server reads.

    python3 history/metrics.py                 # writes latest/cards.csv, latest/series/<xx>.csv, latest/summary.json

latest/cards.csv has every column the scraper's own cards.csv has (so the
server keeps working unchanged), one row per asset using each asset's newest
daily numbers, plus the derived columns below. Every derived column is blank
when the history can't support it honestly — the server renders blank as "—",
never as 0.

  outlier sales        = alt.xyz already flags RELISTED / NOT_PAID rows (kept
                         out here via skipped_reason). On top of that, a sale
                         below 1/4x or above 6x the running median of the
                         card's last 12 accepted sales (past year) is held
                         back unless confirmed — two consecutive high sales
                         confirm a jump, five consecutive low sales a drop —
                         so a "$2,100 PSA 10 1st Edition Charizard" between
                         $300k+ sales is dropped but a card that really
                         tripled is not (see drop_outliers).
                         Everything below is computed from the clean sales only.
  clean_last_sale_*    = the newest clean sale (price/date/source); the change
                         and volume columns are built from clean sales only.
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
  volume_30d/90d/1y    = number of PSA 10 sales in the window.
  pop_30d_ago          = PSA 10 population from the newest daily file at least
                         30 days old that has this asset; blank until the
                         daily series is that old.
  pop_chg_30d          = pop_at_grade - pop_30d_ago.
  mkt_cap_chg_30d_pct  = (ref_now x pop_now) vs (ref_30d x pop_30d_ago); needs
                         pop history, so blank for the first 30 days.
  median_last_3        = ref(today): the number the change columns are built on.
  sales_first_date, sales_total, history_days: how much history stands behind
                         the row.

"today" is the data date (the newest daily file), not the wall clock, so
rebuilding latest/ from the same history always gives the same file.
"""
import csv
import json
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

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

LOOKBACK_DAYS = 180
OUTLIER_LOW, OUTLIER_HIGH = 0.25, 6.0          # band around the running reference (asymmetric: junk is mostly low)
OUTLIER_REF, OUTLIER_MIN_ACCEPTED = 12, 4       # reference = median of the last 12 accepted sales, needs 4
OUTLIER_RESET_RUN_LOW, OUTLIER_RESET_RUN_HIGH = 5, 2   # consecutive same-side rejections that confirm a real move
OUTLIER_REF_MAX_AGE = timedelta(days=365)      # a reference older than this says nothing about today
OUTLIER_STALE_MIN = 3                          # with fewer than OUTLIER_MIN_ACCEPTED same-year sales, fall back to the last
OUTLIER_STALE_LOW, OUTLIER_STALE_HIGH = 0.1, 10.0   # OUTLIER_REF accepted sales of any age, with this wider band (2026-09-21)
OUTLIER_CONTINUATION = 2.0                     # a sale within this factor of the LAST accepted sale is never an outlier
WINDOWS = {"30d": 30, "90d": 90, "1y": 365}


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


def load_sales(store):
    """{asset_id: [(date, price), ...] sorted ascending}, PSA 10 only, skipped
    sales (alt.xyz's own outlier/bad-data flag) excluded."""
    by_asset = defaultdict(list)
    raw = defaultdict(list)   # every PSA 10 sale incl. flagged ones: (date, price, source, sale_type, status, url)
    n = 0
    for p in sorted((store / "sales").glob("*.csv")):
        with open(p, newline="", encoding="utf-8") as f:
            for s in csv.DictReader(f):
                if s.get("grading_company") != "PSA" or s.get("grade") != "10.0":
                    continue
                try:
                    price = float(s["price"])
                except (TypeError, ValueError):
                    continue
                if price <= 0:
                    continue
                raw[s["asset_id"]].append((d(s["date"]), price, s.get("source") or "", s.get("sale_type") or "", s.get("skipped_reason") or "ok", s.get("url") or ""))
                if s.get("skipped_reason"):
                    continue
                by_asset[s["asset_id"]].append((d(s["date"]), price, s.get("source") or ""))
                n += 1
    outliers = {}
    for aid, v in by_asset.items():
        v.sort()
        clean, dropped = drop_outliers(v)
        by_asset[aid] = clean
        outliers[aid] = dropped
    return by_asset, n, outliers, raw


def drop_outliers(sales):
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
    must not be called an outlier."""
    keep, dropped = [], 0
    accepted = []  # accepted sales (date, price, source), in date order
    run = []       # consecutive rejected sales, same side
    regime = 0     # index into accepted: the first sale of the last confirmed run (0 = no confirmed move yet)
    for sale in sales:
        price = sale[1]
        pool = accepted[regime:]
        recent = [a[1] for a in pool[-OUTLIER_REF:] if sale[0] - a[0] <= OUTLIER_REF_MAX_AGE]
        if len(recent) >= (OUTLIER_MIN_ACCEPTED if regime == 0 else 1):
            ref, lo_b, hi_b = statistics.median(recent), OUTLIER_LOW, OUTLIER_HIGH
        elif len(pool) >= (OUTLIER_STALE_MIN if regime == 0 else 1):
            # stale reference: not enough sales this year, so the last 12 of any age with the wider band
            ref, lo_b, hi_b = statistics.median([a[1] for a in pool[-OUTLIER_REF:]]), OUTLIER_STALE_LOW, OUTLIER_STALE_HIGH
        else:
            ref = None
        if ref is not None and not (accepted and 1 / OUTLIER_CONTINUATION <= price / accepted[-1][1] <= OUTLIER_CONTINUATION):
            low, high = price < lo_b * ref, price > hi_b * ref
            if low or high:
                side = "low" if low else "high"
                if run and run[0][0] != side:
                    run = []
                run.append((side, sale))
                if len(run) >= (OUTLIER_RESET_RUN_LOW if side == "low" else OUTLIER_RESET_RUN_HIGH):
                    # confirmed move (high) or regime change (low): the run is real, and the reference jumps to it
                    regime = len(accepted)
                    for _, r in run:
                        keep.append(r)
                        accepted.append(r)
                    dropped -= len(run) - 1
                    run = []
                else:
                    dropped += 1
                continue
        run = []
        keep.append(sale)
        accepted.append(sale)
    keep.sort()
    return keep, dropped


def ref_price(sales, at):
    """Median of the up-to-3 most recent sales on or before `at`, within LOOKBACK_DAYS."""
    floor = at - timedelta(days=LOOKBACK_DAYS)
    recent = [p for (sd, p, _) in sales if floor < sd <= at]
    if not recent:
        return None
    return statistics.median(recent[-3:])


def count_in(sales, start, end):
    return sum(1 for (sd, _, _) in sales if start < sd <= end)


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


def build(store=HERE, out=LATEST):
    assets = {a["asset_id"]: a for a in read_csv(store / "assets.csv")}
    daily, days = load_daily(store)
    today = d(days[-1])
    sales, n_sales, outliers, raw_sales = load_sales(store)
    print(f"{len(assets)} assets, {len(days)} daily files ({days[0]}..{days[-1]}), {n_sales} PSA 10 sales")

    # Newest numbers per asset, and the first day each asset appears.
    latest_row, first_day = {}, {}
    for day in days:
        for aid, row in daily[day].items():
            latest_row[aid] = (day, row)
            first_day.setdefault(aid, day)
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
        for label, n in WINDOWS.items():
            start = today - timedelta(days=n)
            vol = count_in(s, start, today)
            row[f"volume_{label}"] = vol
            chg = pct(ref_now, ref_price(s, start)) if vol > 0 else None
            row[f"price_chg_{label}_pct"] = fmt(chg)
            if chg is not None:
                filled[f"price_chg_{label}_pct"] += 1

        pop_now = to_int(num.get("pop_at_grade"))
        p30 = pop_30.get(aid)
        row["pop_30d_ago"] = fmt(p30)
        row["pop_chg_30d"] = fmt(pop_now - p30) if (pop_now is not None and p30 is not None) else ""
        mc = None
        if pop_now and p30 and ref_now is not None and row["volume_30d"] > 0:
            ref_30 = ref_price(s, today - timedelta(days=30))
            mc = pct(ref_now * pop_now, ref_30 * p30) if ref_30 else None
        row["mkt_cap_chg_30d_pct"] = fmt(mc)
        if mc is not None:
            filled["mkt_cap_chg_30d_pct"] += 1
        row["sales_first_date"] = s[0][0].isoformat() if s else ""
        row["sales_total"] = len(s)
        row["history_days"] = (today - d(first_day[aid])).days
        rows.append(row)

    out.mkdir(parents=True, exist_ok=True)
    with open(out / "cards.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CARD_COLS + DERIVED_COLS, extrasaction="ignore")
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
    # sales" dropdown. Sharded like series/.
    recent_dir = out / "recent_sales"
    recent_dir.mkdir(exist_ok=True)
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
        "rows_unconfirmed_last_sale": sum(1 for r in rows if r["last_sale_unconfirmed"] == 1),
        "rows_with": {k: filled[k] for k in ("price_chg_30d_pct", "price_chg_90d_pct", "price_chg_1y_pct", "mkt_cap_chg_30d_pct")},
    }
    with open(out / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"wrote {out}/cards.csv ({len(rows)} rows), series/*.csv ({n_series} rows in {len(shards)} shards)")
    print("rows with a value:", summary["rows_with"])
    return summary


if __name__ == "__main__":
    build()
