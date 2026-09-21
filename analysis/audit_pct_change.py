#!/usr/bin/env python3
"""Independent audit of the feed's price-change columns.

Rebuilds price_chg_30d/90d/1y_pct, volume_30d/90d/1y, median_last_3,
clean_last_sale_* and last_sale_unconfirmed for every card from the raw
sale history (history/sales/*.csv), written from the README's definitions
and NOT importing history/metrics.py, then diffs the result against
latest/cards.csv. Any card where the two disagree is printed.

Definitions audited (README, "The data PokeSniper gets"):
  clean sales      PSA 10 sales alt.xyz does not flag (RELISTED/NOT_PAID/PENDING
                   excluded), after the sequential outlier filter: reference =
                   median of the last 12 accepted sales within one year, needs 4;
                   a sale below 1/4x or above 6x is held back unless confirmed
                   (2 consecutive highs, 5 consecutive lows); with fewer than 4
                   same-year sales but at least 3 ever, the reference is the last
                   12 of any age and the band 1/10x .. 10x. After a confirmed
                   run only sales from that run onward form the reference, and
                   one of them is enough. A sale within 2x of the last accepted
                   sale is never held.
  ref(t)           median of the up-to-3 newest clean sales in (t-180d, t].
  price_chg_Nd_pct ref(today) vs ref(today-N), only when both exist and at
                   least one clean sale fell in (today-N, today].
  volume_Nd        clean sales in (today-N, today].
  today            the date of the newest history/daily file.

Usage: analysis/.venv/bin/python analysis/audit_pct_change.py [--show 20]
"""
import argparse
import csv
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "history"
FEED = ROOT / "latest" / "cards.csv"

WINDOWS = {"30d": 30, "90d": 90, "1y": 365}
REF_LOOKBACK = 180
OUT_LOW, OUT_HIGH = 0.25, 6.0
OUT_REF_N, OUT_MIN_ACCEPTED = 12, 4
OUT_RUN_LOW, OUT_RUN_HIGH = 5, 2
OUT_REF_MAX_AGE = 365
OUT_STALE_MIN, OUT_STALE_LOW, OUT_STALE_HIGH = 3, 0.1, 10.0
OUT_CONTINUATION = 2.0


def load_sales():
    """asset_id -> list of (date, price, source, flagged) sorted by (date, price, source)."""
    by = defaultdict(list)
    for p in sorted((STORE / "sales").glob("*.csv")):
        with open(p, newline="", encoding="utf-8") as f:
            for s in csv.DictReader(f):
                if s["grading_company"] != "PSA" or s["grade"] != "10.0":
                    continue
                try:
                    price = float(s["price"])
                except ValueError:
                    continue
                if price <= 0:
                    continue
                by[s["asset_id"]].append((date.fromisoformat(s["date"][:10]), price, s["source"] or "", bool(s["skipped_reason"])))
    for v in by.values():
        v.sort(key=lambda t: (t[0], t[1], t[2]))
    return by


def clean_sales(sales):
    """Sequential outlier filter, re-implemented from the README description."""
    accepted, kept = [], []
    run_side, run = None, []
    dropped = 0
    regime = 0
    for sd, price, src, flagged in sales:
        if flagged:
            continue
        sale = (sd, price, src)
        pool = accepted[regime:]
        window = [a for a in pool[-OUT_REF_N:] if (sd - a[0]).days <= OUT_REF_MAX_AGE]
        ref = None
        if len(window) >= (OUT_MIN_ACCEPTED if regime == 0 else 1):
            ref, lo_b, hi_b = statistics.median(a[1] for a in window), OUT_LOW, OUT_HIGH
        elif len(pool) >= (OUT_STALE_MIN if regime == 0 else 1):
            ref, lo_b, hi_b = statistics.median(a[1] for a in pool[-OUT_REF_N:]), OUT_STALE_LOW, OUT_STALE_HIGH
        if ref is not None and not (accepted and 1 / OUT_CONTINUATION <= price / accepted[-1][1] <= OUT_CONTINUATION):
            side = "low" if price < lo_b * ref else "high" if price > hi_b * ref else None
            if side:
                if run_side != side:
                    run_side, run = side, []
                run.append(sale)
                need = OUT_RUN_LOW if side == "low" else OUT_RUN_HIGH
                if len(run) >= need:
                    regime = len(accepted)
                    kept.extend(run)
                    accepted.extend(run)
                    dropped -= len(run) - 1
                    run_side, run = None, []
                else:
                    dropped += 1
                continue
        run_side, run = None, []
        kept.append(sale)
        accepted.append(sale)
    kept.sort(key=lambda t: (t[0], t[1], t[2]))
    return kept, dropped


def ref_price(clean, at):
    lo = at - timedelta(days=REF_LOOKBACK)
    recent = [p for sd, p, _ in clean if lo < sd <= at]
    return statistics.median(recent[-3:]) if recent else None


def pct(now, before):
    if now is None or before is None or before <= 0:
        return None
    return round((now / before - 1) * 100, 2)


def num(s):
    return None if s in ("", None) else float(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", type=int, default=25, help="max mismatches to print per column")
    args = ap.parse_args()

    days = sorted(p.stem for p in (STORE / "daily").glob("*.csv"))
    today = date.fromisoformat(days[-1])
    feed = {r["asset_id"]: r for r in csv.DictReader(open(FEED, newline="", encoding="utf-8"))}
    sales = load_sales()
    print(f"today = {today} (newest daily file); feed rows = {len(feed)}; assets with PSA 10 sales = {len(sales)}")

    cols = ["clean_last_sale_price", "clean_last_sale_date", "outliers_excluded", "last_sale_unconfirmed", "median_last_3"]
    cols += [f"volume_{w}" for w in WINDOWS] + [f"price_chg_{w}_pct" for w in WINDOWS]
    mism = defaultdict(list)
    checked = defaultdict(int)
    future_dated = []

    for aid, row in feed.items():
        raw = sales.get(aid, [])
        clean, dropped = clean_sales(raw)
        mine = {}
        last = clean[-1] if clean else None
        mine["clean_last_sale_price"] = last[1] if last else None
        mine["clean_last_sale_date"] = last[0].isoformat() if last else ""
        mine["outliers_excluded"] = dropped
        ok = [(sd, p) for sd, p, _, fl in raw if not fl]
        newest = max(ok) if ok else None
        mine["last_sale_unconfirmed"] = 1 if (newest and last and newest != (last[0], last[1]) and newest[0] >= last[0]) else 0
        ref_now = ref_price(clean, today)
        mine["median_last_3"] = ref_now
        for w, n in WINDOWS.items():
            start = today - timedelta(days=n)
            vol = sum(1 for sd, _, _ in clean if start < sd <= today)
            mine[f"volume_{w}"] = vol
            mine[f"price_chg_{w}_pct"] = pct(ref_now, ref_price(clean, start)) if vol > 0 else None
        if last and last[0] > today:
            future_dated.append((aid, last[0].isoformat()))

        for c in cols:
            theirs, ours = row.get(c, ""), mine[c]
            checked[c] += 1
            if c == "clean_last_sale_date":
                same = theirs == ours
            else:
                t = num(theirs)
                same = (t is None and ours is None) or (t is not None and ours is not None and abs(t - float(ours)) <= 0.011)
            if not same:
                mism[c].append((aid, row["card_name"], theirs, ours))

    print()
    print(f"{'column':<24}{'checked':>9}{'mismatch':>10}")
    for c in cols:
        print(f"{c:<24}{checked[c]:>9}{len(mism[c]):>10}")
    if future_dated:
        print(f"\n{len(future_dated)} cards whose newest clean sale is dated after today ({today}); e.g. {future_dated[:5]}")
    for c in cols:
        if mism[c]:
            print(f"\n--- {c}: first {min(args.show, len(mism[c]))} of {len(mism[c])}")
            for aid, name, theirs, ours in mism[c][: args.show]:
                print(f"  {aid[:8]}  {name[:60]:<60} feed={theirs!r:>12} audit={ours!r}")
    total = sum(len(v) for v in mism.values())
    print(f"\n{'PASS' if total == 0 else 'FAIL'}: {total} mismatching cells")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
