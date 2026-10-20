#!/usr/bin/env python3
"""Independent audit of the feed's price-change columns.

Rebuilds price_chg_30d/90d/1y_pct, volume_30d/90d/1y, median_last_3,
clean_last_sale_* and last_sale_unconfirmed for every card from the raw
sale history (history/sales/*.csv), written from the README's definitions
and NOT importing history/metrics.py, then diffs the result against
latest/cards.csv. Any card where the two disagree is printed.

Definitions audited (README, "The data PokeSniper gets"):
  clean sales      PSA 10 sales alt.xyz does not flag (RELISTED/NOT_PAID/PENDING
                   excluded), PWCC lots listed under both fanaticscollect.com and
                   pwccmarketplace.com counted once, after the sequential outlier filter: reference =
                   median of the last 12 accepted sales within one year, needs 4;
                   a sale below 1/4x or above 6x is held back unless confirmed
                   (2 consecutive highs, 5 consecutive lows, where repeat sales
                   of one listing URL count once); with fewer than 4
                   same-year sales but at least 3 ever, the reference is the last
                   12 of any age and the band 1/10x .. 10x; with only 1-2 sales
                   ever, those with the same band, and 2 distinct listings
                   confirm either side. After a confirmed
                   run only sales from that run onward form the reference, and
                   one of them is enough. A sale within 2x of the last accepted
                   sale is never held.
  ref(t)           median of the up-to-3 newest clean sales in (t-180d, t].
  held highs       sales the filter rejected on the HIGH side and never
                   accepted later (2026-10-01).
  price_chg_Nd_pct the same median-of-3 ref over clean sales PLUS held highs:
                   ref'(today) vs ref'(today-N), only when both exist and at
                   least one such sale fell in (today-N, today] (2026-10-01).
  chg_held_windows the windows (30d 60d 90d 180d 1y) whose price_chg differs
                   from the clean-only figure, space-separated.
  volume_Nd        clean sales in (today-N, today].
  price_chg_60d_pct, price_chg_180d_pct, volume_60d, volume_180d
                   the price_chg / volume rules for 60 / 180 days.
  cap_price(t)     ref(t), else the newest clean sale on or before t (any age);
                   cap_price = cap_price(today), cap_price_Nd_ago = cap_price(today-N)
                   for N in 30d/60d/90d/180d/1y (2026-09-28).
  today            the date of the newest history/daily file.
  psa9_*           (2026-10-17) the same definitions over the PSA 9 sales, for
                   psa9_clean_last_sale_price, psa9_last_sale_unconfirmed,
                   psa9_median_last_3, psa9_volume_30d/60d/90d/180d/1y,
                   psa9_price_chg_30d/60d/90d/180d/1y_pct (held highs counted),
                   psa9_cap_price, psa9_cap_price_*_ago and psa9_chg_held_windows;
                   audited on the rows whose psa9_scraped_date is set or that have
                   any PSA 9 sale on record, and expected blank on every other row
                   (PSA 9 sales not collected).
  last_sale_single (2026-10-18) blank when the newest settled sale is not held
                   (last_sale_unconfirmed 0) or there is none; 1 when it was held on the
                   HIGH side while the filter's reference was the stale or thin one (fewer
                   than 4 accepted sales in the year before it); 0 when held otherwise.
  last_sale_status (2026-10-18) the state of the newest sale alt.xyz lists, any status:
                   completed / pending (PENDING, PAYMENT_INITIATED) / unpaid (NOT_PAID) /
                   relisted (RELISTED); a not-completed sale on the newest day wins the tie
                   in that order; blank with no sales (or only unknown statuses).
  psa9_last_sale_single, psa9_last_sale_status  the same over the PSA 9 sales.
  REMOVED          (2026-10-20) a skipped_reason ingest writes when a complete fetch of the card no
                   longer lists the sale: excluded from the clean sales like any flag, and NOT a
                   listed sale, so last_sale_status looks past it.
  last_sale_newer_price / _date  (2026-10-20) when last_sale_status is pending / unpaid / relisted,
                   the price and date of the newest-day sale in that state (the dearest on a tie);
                   blank otherwise. psa9_ pair the same over the PSA 9 sales.
  last_sale_title_check  (2026-10-20) history/sale_title_checks.csv's status for (the eBay item id
                   of the newest settled sale, asset, grade), blank when there is none or the sale
                   is not an eBay item; psa9_ the same over the PSA 9 sales. Read here straight
                   from that file, not through metrics.py.

Usage: analysis/.venv/bin/python analysis/audit_pct_change.py [--show 20] [--feed cards.csv]
"""
import argparse
import re
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
CHG_ONLY = {"60d": 60, "180d": 180}
CAP_AGO = {"30d": 30, "60d": 60, "90d": 90, "180d": 180, "1y": 365}
REF_LOOKBACK = 180
OUT_LOW, OUT_HIGH = 0.25, 6.0
OUT_REF_N, OUT_MIN_ACCEPTED = 12, 4
OUT_RUN_LOW, OUT_RUN_HIGH = 5, 2
OUT_REF_MAX_AGE = 365
OUT_STALE_MIN, OUT_STALE_LOW, OUT_STALE_HIGH = 3, 0.1, 10.0
OUT_CONTINUATION = 2.0
OUT_THIN_RUN = 2
STATUS = {"PENDING": "pending", "PAYMENT_INITIATED": "pending", "NOT_PAID": "unpaid", "RELISTED": "relisted"}   # alt.xyz's flags


MIRRORS = ("fanaticscollect.com", "pwccmarketplace.com")
EBAY_RX = re.compile(r"ebay\.[a-z.]+/itm/(?:[^/?#]+/)?(\d{9,})")   # the eBay legacy item id in a sale URL (2026-10-20)
TITLE_CHECKS = STORE / "sale_title_checks.csv"


def mirror_of(url):
    """Which of the two PWCC mirror hosts a sale URL is on, else None."""
    host = url.split("://", 1)[-1].split("/", 1)[0].lower().removeprefix("www.")
    return host if host in MIRRORS else None


GRADES = ("10.0", "9.0")


def load_sales():
    """grade -> asset_id -> list of (date, price, source, flagged, url) sorted by (date, price,
    source, url), for PSA 10 and PSA 9; flagged = alt.xyz's status flag (RELISTED, NOT_PAID,
    PENDING ...), "" when the sale is settled. A PWCC lot recorded under both mirror hosts (same asset,
    grade, date, price) is one sale: per (asset, date, price) keep the host with more rows
    (fanaticscollect on a tie) and drop the other's."""
    rows = {g: [] for g in GRADES}
    for p in sorted((STORE / "sales").glob("*.csv")):
        with open(p, newline="", encoding="utf-8") as f:
            for s in csv.DictReader(f):
                if s["grading_company"] != "PSA" or s["grade"] not in rows:
                    continue
                try:
                    price = float(s["price"])
                except ValueError:
                    continue
                if price <= 0:
                    continue
                rows[s["grade"]].append((s["asset_id"], date.fromisoformat(s["date"][:10]), price, s["source"] or "",
                                         s["skipped_reason"] or "", mirror_of(s["url"] or ""), s["url"] or ""))
    return {g: _dedupe_mirrors(rows[g]) for g in GRADES}


def _dedupe_mirrors(rows):
    per_host = defaultdict(lambda: {m: 0 for m in MIRRORS})
    for aid, sd, price, _, _, m, _ in rows:
        if m:
            per_host[(aid, sd, price)][m] += 1
    loser = {}
    for k, c in per_host.items():
        fan, pwcc = c["fanaticscollect.com"], c["pwccmarketplace.com"]
        if fan and pwcc:
            loser[k] = "pwccmarketplace.com" if fan >= pwcc else "fanaticscollect.com"
    by = defaultdict(list)
    for aid, sd, price, source, flagged, m, url in rows:
        if m and loser.get((aid, sd, price)) == m:
            continue
        by[aid].append((sd, price, source, flagged, url))
    for v in by.values():
        v.sort(key=lambda t: (t[0], t[1], t[2], t[4]))
    return by


def clean_sales(sales):
    """Sequential outlier filter, re-implemented from the README description.
    Returns (kept, dropped, held_highs, singles): singles = the {(date, price)} of the held
    highs rejected while the reference was the stale or thin one (2026-10-18)."""
    accepted, kept = [], []
    rejected = {}   # position -> (sale, side, band) for every rejection; confirmed runs are removed
    run_side, run, run_pos = None, [], []
    dropped = 0
    regime = 0
    for n, (sd, price, src, flagged, url) in enumerate(sales):
        if flagged:
            continue
        listing = url or ("no-url", n)
        sale = (sd, price, src)
        pool = accepted[regime:]
        window = [a for a in pool[-OUT_REF_N:] if (sd - a[0]).days <= OUT_REF_MAX_AGE]
        ref, band = None, None
        if len(window) >= (OUT_MIN_ACCEPTED if regime == 0 else 1):
            ref, lo_b, hi_b, band = statistics.median(a[1] for a in window), OUT_LOW, OUT_HIGH, "normal"
        elif len(pool) >= (OUT_STALE_MIN if regime == 0 else 1):
            ref, lo_b, hi_b, band = statistics.median(a[1] for a in pool[-OUT_REF_N:]), OUT_STALE_LOW, OUT_STALE_HIGH, "stale"
        elif len(pool) > 0:
            ref, lo_b, hi_b, band = statistics.median(a[1] for a in pool), OUT_STALE_LOW, OUT_STALE_HIGH, "thin"
        few = regime == 0 and 0 < len(pool) < OUT_STALE_MIN
        if ref is not None and not (accepted and 1 / OUT_CONTINUATION <= price / accepted[-1][1] <= OUT_CONTINUATION):
            side = "low" if price < lo_b * ref else "high" if price > hi_b * ref else None
            if side:
                if run_side != side:
                    run_side, run, run_pos = side, [], []
                run.append((sale, listing))
                rejected[n] = (sale, side, band)
                run_pos.append(n)
                need = OUT_THIN_RUN if few else OUT_RUN_LOW if side == "low" else OUT_RUN_HIGH
                if len({lst for _, lst in run}) >= need:
                    regime = len(accepted)
                    kept.extend(r for r, _ in run)
                    accepted.extend(r for r, _ in run)
                    dropped -= len(run) - 1
                    for q in run_pos:
                        del rejected[q]
                    run_side, run, run_pos = None, [], []
                else:
                    dropped += 1
                continue
        run_side, run, run_pos = None, [], []
        kept.append(sale)
        accepted.append(sale)
    kept.sort(key=lambda t: (t[0], t[1], t[2]))
    highs = sorted((sale for sale, side, _ in rejected.values() if side == "high"), key=lambda t: (t[0], t[1], t[2]))
    singles = {(sale[0], sale[1]) for sale, side, band in rejected.values() if side == "high" and band != "normal"}
    return kept, dropped, highs, singles


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


def derive(raw, today, windows):
    """Every audited figure for one card and grade from its raw sales: the README definitions
    over the clean sales (+ held highs for the change columns). Keys are the PSA 10 column
    names; the caller prefixes psa9_ for the PSA 9 set."""
    clean, dropped, highs, singles = clean_sales(raw)
    counted = sorted(clean + highs, key=lambda t: (t[0], t[1], t[2]))
    mine = {}
    last = clean[-1] if clean else None
    mine["clean_last_sale_price"] = last[1] if last else None
    mine["clean_last_sale_date"] = last[0].isoformat() if last else ""
    mine["outliers_excluded"] = dropped
    ok = [(sd, p) for sd, p, _, fl, _ in raw if not fl]
    newest = max(ok) if ok else None
    mine["last_sale_unconfirmed"] = 1 if (newest and last and newest != (last[0], last[1]) and newest[0] >= last[0]) else 0
    mine["last_sale_single"] = None if not mine["last_sale_unconfirmed"] else 1 if newest in singles else 0   # None = blank, like the other numeric cells
    # the newest sale alt.xyz lists, flagged ones included but not REMOVED ones (alt.xyz no longer lists
    # those); a not-completed one wins a same-day tie, and is the "newer" sale (dearest on a tie)
    listed = [t for t in raw if t[3] != "REMOVED"]
    mine["last_sale_newer_price"], mine["last_sale_newer_date"] = None, ""
    if listed:
        top = max(sd for sd, _, _, _, _ in listed)
        states = {fl for sd, _, _, fl, _ in listed if sd == top}
        state = next((s for s in ("PENDING", "PAYMENT_INITIATED", "NOT_PAID", "RELISTED") if s in states), None)
        mine["last_sale_status"] = STATUS[state] if state else ("completed" if "" in states else "")
        if state:
            mine["last_sale_newer_price"] = max(p for sd, p, _, fl, _ in listed if sd == top and fl == state)
            mine["last_sale_newer_date"] = top.isoformat()
    else:
        mine["last_sale_status"] = ""
    # the eBay item ids of the newest settled sale(s): several only when two sales share date and price
    mine["_newest_items"] = {EBAY_RX.search(u).group(1) for sd, p, _, fl, u in raw if not fl and (sd, p) == newest and EBAY_RX.search(u or "")} if newest else set()
    ref_now = ref_price(clean, today)
    mine["median_last_3"] = ref_now
    held_in = []
    for w, n in windows.items():
        start = today - timedelta(days=n)
        mine[f"volume_{w}"] = sum(1 for sd, _, _ in clean if start < sd <= today)
        only_clean = pct(ref_now, ref_price(clean, start)) if mine[f"volume_{w}"] else None
        with_held = (pct(ref_price(counted, today), ref_price(counted, start))
                     if any(start < sd <= today for sd, _, _ in counted) else None)
        mine[f"price_chg_{w}_pct"] = with_held
        if with_held != only_clean:
            held_in.append(n)
    mine["chg_held_windows"] = " ".join(w for w, n in sorted(CAP_AGO.items(), key=lambda kv: kv[1]) if n in held_in)

    def worth(at):
        r = ref_price(clean, at)
        if r is not None:
            return r
        upto = [p for sd, p, _ in clean if sd <= at]
        return upto[-1] if upto else None
    mine["cap_price"] = worth(today)
    for w, n in CAP_AGO.items():
        mine[f"cap_price_{w}_ago"] = worth(today - timedelta(days=n))
    return mine, last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", type=int, default=25, help="max mismatches to print per column")
    ap.add_argument("--feed", type=Path, default=FEED, help="cards.csv to audit (default latest/cards.csv)")
    args = ap.parse_args()

    days = sorted(p.stem for p in (STORE / "daily").glob("*.csv"))
    today = date.fromisoformat(days[-1])
    feed = {r["asset_id"]: r for r in csv.DictReader(open(args.feed, newline="", encoding="utf-8"))}
    sales = load_sales()
    print(f"today = {today} (newest daily file); feed rows = {len(feed)}; assets with PSA 10 sales = {len(sales['10.0'])}, "
          f"with PSA 9 sales = {len(sales['9.0'])}")

    cols = ["clean_last_sale_price", "clean_last_sale_date", "outliers_excluded", "last_sale_unconfirmed", "median_last_3"]
    cols += [f"volume_{w}" for w in WINDOWS] + [f"price_chg_{w}_pct" for w in WINDOWS]
    cols += [f"price_chg_{w}_pct" for w in CHG_ONLY] + [f"volume_{w}" for w in CHG_ONLY] + ["cap_price"] + [f"cap_price_{w}_ago" for w in CAP_AGO]
    cols += ["chg_held_windows", "last_sale_single", "last_sale_status"]          # 2026-10-18
    cols += ["last_sale_newer_price", "last_sale_newer_date", "last_sale_title_check"]          # 2026-10-20
    # the PSA 9 set (2026-10-17): the same figures over the PSA 9 sales, under the psa9_ prefix
    psa9_cols = ["clean_last_sale_price", "last_sale_unconfirmed", "median_last_3"]
    psa9_cols += [f"volume_{w}" for w in CAP_AGO] + [f"price_chg_{w}_pct" for w in CAP_AGO]
    psa9_cols += ["cap_price"] + [f"cap_price_{w}_ago" for w in CAP_AGO] + ["chg_held_windows", "last_sale_single", "last_sale_status"]
    psa9_cols += ["last_sale_newer_price", "last_sale_newer_date", "last_sale_title_check"]
    cols += ["psa9_" + c for c in psa9_cols]
    mism = defaultdict(list)
    checked = defaultdict(int)
    future_dated = []
    sales10, sales9 = sales["10.0"], sales["9.0"]
    text_cols = {"clean_last_sale_date", "chg_held_windows", "psa9_chg_held_windows", "last_sale_status", "psa9_last_sale_status",
                 "last_sale_newer_date", "psa9_last_sale_newer_date", "last_sale_title_check", "psa9_last_sale_title_check"}
    blank_text = {"chg_held_windows", "last_sale_status", "last_sale_newer_date", "last_sale_title_check"}
    title_checks = {}
    if TITLE_CHECKS.exists():
        with open(TITLE_CHECKS, newline="", encoding="utf-8") as f:
            title_checks = {(r["item_id"], r["asset_id"], r["grade"]): r["status"] for r in csv.DictReader(f)}

    def title_verdict(mine, aid, grade):
        """The file's verdict for the newest settled sale; when two sales share the newest date and price,
        any of their verdicts is accepted (metrics.py picks one of them by source / URL order)."""
        found = {title_checks.get((iid, aid, grade), "") for iid in mine.pop("_newest_items", set())}
        return found if found else {""}

    for aid, row in feed.items():
        mine, last = derive(sales10.get(aid, []), today, {**WINDOWS, **CHG_ONLY})
        mine["last_sale_title_check"] = title_verdict(mine, aid, "10.0")
        if last and last[0] > today:
            future_dated.append((aid, last[0].isoformat()))
        raw9 = sales9.get(aid, [])
        if row.get("psa9_scraped_date") or raw9:
            mine9, _ = derive(raw9, today, CAP_AGO)
            mine9["last_sale_title_check"] = title_verdict(mine9, aid, "9.0")
            mine.update({"psa9_" + c: mine9[c] for c in psa9_cols})
        else:
            # PSA 9 sales never collected for this card: every PSA 9 column must be blank
            mine.update({"psa9_" + c: ("" if c in blank_text else None) for c in psa9_cols})

        for c in cols:
            theirs, ours = row.get(c, ""), mine[c]
            checked[c] += 1
            if isinstance(ours, set):
                same = theirs in ours
                ours = "/".join(sorted(ours))
            elif c in text_cols:
                same = theirs == ours
            else:
                t = num(theirs)
                same = (t is None and ours is None) or (t is not None and ours is not None and abs(t - float(ours)) <= 0.011)
            if not same:
                mism[c].append((aid, row["card_name"], theirs, ours))

    print()
    print(f"{'column':<30}{'checked':>9}{'mismatch':>10}")
    for c in cols:
        print(f"{c:<30}{checked[c]:>9}{len(mism[c]):>10}")
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
