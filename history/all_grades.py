#!/usr/bin/env python3
"""Every PSA grade for the Gold Star category (2026-10-21): latest/all_grades/, step 5b of the nightly.

    python3 history/all_grades.py                 # history/ -> latest/all_grades/{grades.csv,sales.csv,summary.json}
    python3 history/all_grades.py --store DIR --out DIR

Samay and Sid bought a Gold Star and want the whole category tracked at every grade, not only
the PSA 10 / PSA 9 the feed carries for every card. The scraper pulls those cards (nightly/
all_grades_scope.py, --all-grades-for) at PSA 1, 1.5, ... 9, 10 and records their pop at every
grade (history/grade_pops.csv, from cards.csv pops_by_grade); the sales land in the store like
any other. This step reads both and writes:

  latest/all_grades/grades.csv   one row per card and PSA grade (18 grades: 1, 1.5, ..., 8.5, 9, 10),
                                 for every card of the category: flags.ALL_GRADES_TYPES by flags.card_type
                                 over history/assets.csv (the rule nightly/all_grades_scope.py selects the
                                 scrape list with), plus any card that has a history/grade_pops.csv row
                                 (collected once, so it stays visible if the rule changes). The columns
                                 are the feed's own, with the same meaning as the PSA 10 ones in
                                 latest/cards.csv (README) applied to that grade's sales:
      asset_id, card_name, year, set, card_number, subject, variety   identity (history/assets.csv)
      grading_company, grade          PSA, "1.0" .. "10.0"
      pop, pop_date                   the pop at that grade from the newest grade_pops row, and its day.
                                      0 = alt.xyz's pop table lists no copies at the grade (a real zero).
                                      BLANK = alt.xyz's pop table has no PSA rows for the record at all
                                      (60 of 161 Gold Star records on 2026-10-21, foreign-language and
                                      deck-reprint records): the scraper then writes no pops_by_grade,
                                      so there is no grade_pops row. Unknown, never zero.
      pop_30d_ago, pop_chg_30d        the pop from the newest grade_pops row at least 30 days old, and
                                      the difference; blank until there is one
      scraped_date                    = pop_date: the night the card was asked at every grade (the pops
                                      and the sales come from the same scrape), so blank sale columns
                                      with a scraped_date mean "collected, none sold". Blank with the
                                      pop (no PSA rows): the sale columns then show whatever the store
                                      holds for the card at that grade (its ladder grades are fetched on
                                      the weekly full run; the nightly carries such dormant cards)
      sales_total                     clean sales at the grade (after alt.xyz's own flags, the PWCC
                                      mirror rule and the outlier filter, exactly as cards.csv)
      last_sale_price/date/source     the newest settled sale alt.xyz lists at the grade
      last_sale_status                completed / pending / unpaid / relisted (cards.csv's rule)
      last_sale_newer_price/date      the newer not-completed sale behind a status that is not completed
      last_sale_unconfirmed           1 = the newest sale is one the outlier filter holds back
      last_sale_single                1 = held only for want of a second sale (cards.csv's rule)
      clean_last_sale_price           the newest clean sale
      median_last_3                   median of the last 3 clean sales within 180 days (the reference price)
      cap_price, cap_price_<w>_ago    median_last_3, else the newest clean sale of any age; and the same
                                      30 / 60 / 90 / 180 days and 1 year ago
      volume_30d/60d/90d/180d/1y      clean sales in the window
      price_chg_30d/60d/90d/180d/1y_pct   reference price now vs then (held-back high sales count, as
                                      in cards.csv); chg_held_windows names the windows that rest on one
      highest_sale, lowest_sale, sales_first_date   over the clean sales
      outliers_excluded               sales the filter holds back at the grade
  latest/all_grades/sales.csv    every sale alt.xyz lists for those cards at every grade, newest first
                                 within a card and grade: asset_id, grade, date, price, source, sale_type,
                                 status (ok, or alt.xyz's RELISTED / NOT_PAID / PENDING, or REMOVED),
                                 outlier (1 = an ok sale the filter holds back), url. PWCC mirror copies
                                 dropped, like recent_sales/.
  latest/all_grades/summary.json cards, grade rows, sales, pops_date range, generated_at.

The pop history itself stays in history/grade_pops.csv (committed, one row per card and night).
Reuses history/metrics.py's functions so a figure here equals the one cards.csv would show for
the same sales. Standard library only."""
import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import flags  # noqa: E402
import metrics  # noqa: E402
from ingest import GRADE_POPS, POP_GRADES, pop_col  # noqa: E402

LATEST = HERE.parent / "latest" / "all_grades"
GRADES = [g for g in POP_GRADES if g != "0.0"]   # 1.0 .. 10.0: the grades asked for sales (0.0 = Authentic, pops only)
IDENTITY = ["asset_id", "card_name", "year", "set", "card_number", "subject", "variety"]
GRADE_COLS = (IDENTITY + ["grading_company", "grade", "pop", "pop_date", "pop_30d_ago", "pop_chg_30d", "scraped_date", "sales_total",
                          "last_sale_price", "last_sale_date", "last_sale_source", "last_sale_status", "last_sale_newer_price",
                          "last_sale_newer_date", "last_sale_unconfirmed", "last_sale_single", "clean_last_sale_price", "median_last_3",
                          "cap_price"] + [f"cap_price_{w}_ago" for w in metrics.CAP_WINDOWS]
              + [f"volume_{w}" for w in metrics.CAP_WINDOWS] + [f"price_chg_{w}_pct" for w in metrics.CAP_WINDOWS]
              + ["chg_held_windows", "highest_sale", "lowest_sale", "sales_first_date", "outliers_excluded"])
SALE_COLS = ["asset_id", "grade", "date", "price", "source", "sale_type", "status", "outlier", "url"]


def load_grade_pops(store, today):
    """{asset_id: (newest row, row at least 30 days old or None)} from history/grade_pops.csv (PSA rows)."""
    newest, old = {}, {}
    cutoff = (today - timedelta(days=30)).isoformat()
    for r in metrics.read_csv(store / GRADE_POPS) if (store / GRADE_POPS).exists() else []:
        if (r.get("grading_company") or "PSA") != "PSA":
            continue
        aid = r["asset_id"]
        if aid not in newest or r["data_date"] > newest[aid]["data_date"]:
            newest[aid] = r
        if r["data_date"] <= cutoff and (aid not in old or r["data_date"] > old[aid]["data_date"]):
            old[aid] = r
    return {aid: (newest[aid], old.get(aid)) for aid in newest}


def load_sales(store, assets):
    """{grade: clean_grade(...)} over the store for these assets at every grade of GRADES (one pass)."""
    raw = {g: defaultdict(list) for g in GRADES}
    for p in sorted((store / "sales").glob("*.csv")):
        with open(p, newline="", encoding="utf-8") as f:
            for s in csv.DictReader(f):
                if s["asset_id"] not in assets or s.get("grading_company") != "PSA" or s.get("grade") not in raw:
                    continue
                try:
                    price = float(s["price"])
                except (TypeError, ValueError):
                    continue
                if price <= 0:
                    continue
                raw[s["grade"]][s["asset_id"]].append((metrics.d(s["date"]), price, s.get("source") or "", s.get("sale_type") or "",
                                                       s.get("skipped_reason") or "ok", s.get("url") or ""))
    return {g: metrics.clean_grade(g, raw[g]) for g in GRADES}


def category_assets(assets, card_types=flags.ALL_GRADES_TYPES):
    """The asset ids of the category in history/assets.csv (its rows carry the fields card_type reads)."""
    return {aid for aid, a in assets.items() if flags.card_type(a) in card_types}


def unlisted_assets(store, today):
    """The records cards.csv leaves out as no longer indexed by alt.xyz (metrics.DROPPED_AFTER_DAYS):
    seen in some daily file, never in the last that many days (2026-10-22: the Gold Star number rule
    took in the Delta Species Groudon Star's stale "English Holo" twin 6996521d, last seen 09-11,
    which assets.csv still holds). Reads only the daily files' asset_id column (~2 s)."""
    last = {}
    for p in sorted((store / "daily").glob("*.csv")):
        with open(p, newline="", encoding="utf-8") as f:
            r = csv.reader(f)
            i = next(r, ["asset_id"]).index("asset_id")
            for row in r:
                last[row[i]] = p.stem
    return {aid for aid, day in last.items() if (today - metrics.d(day)).days > metrics.DROPPED_AFTER_DAYS}


def grade_row(ident, grade, pops, clean, raw, held, singles, outliers, today):
    """One grades.csv row: the card's identity, its pop at the grade, and the feed's sale columns
    over that grade's sales (metrics.psa9_cols's rules, column names without the prefix). pops =
    (newest grade_pops row, row at least 30 days old or None), or None when the card has no
    grade_pops row (alt.xyz has no PSA pop rows for it): pop, pop_date and scraped_date stay blank."""
    row = {c: "" for c in GRADE_COLS}
    row.update({c: ident.get(c, "") for c in IDENTITY})
    row.update({"grading_company": "PSA", "grade": grade})
    newest_pop, old_pop = pops if pops else (None, None)
    if newest_pop is not None:
        row.update({"pop": newest_pop.get(pop_col(grade), ""), "pop_date": newest_pop["data_date"], "scraped_date": newest_pop["data_date"]})
    if old_pop is not None:
        then, now = metrics.to_int(old_pop.get(pop_col(grade))), metrics.to_int(newest_pop.get(pop_col(grade)))
        row["pop_30d_ago"] = metrics.fmt(then)
        row["pop_chg_30d"] = metrics.fmt(now - then) if (then is not None and now is not None) else ""
    last = clean[-1] if clean else None
    ok = [t for t in raw if t[4] == "ok"]
    newest = max(ok) if ok else None
    if newest:
        row["last_sale_price"], row["last_sale_date"], row["last_sale_source"] = metrics.fmt(newest[1]), newest[0].isoformat(), newest[2]
    row["clean_last_sale_price"] = metrics.fmt(last[1]) if last else ""
    row["last_sale_unconfirmed"] = 1 if (newest and last and (newest[0], newest[1]) != (last[0], last[1]) and newest[0] >= last[0]) else 0
    row["last_sale_single"] = metrics.single_flag(newest, last, singles)
    row.update(metrics.newer_cols(raw))
    row["median_last_3"] = metrics.fmt(metrics.ref_price(clean, today))
    counted = sorted(clean + held) if held else clean
    held_windows = set()
    for label, n in metrics.CAP_WINDOWS.items():
        start = today - timedelta(days=n)
        row[f"volume_{label}"] = metrics.count_in(clean, start, today)
        chg, was_held = metrics.change_since(clean, counted, start, today)
        row[f"price_chg_{label}_pct"] = metrics.fmt(chg)
        if was_held:
            held_windows.add(label)
        row[f"cap_price_{label}_ago"] = metrics.fmt(metrics.cap_price(clean, start))
    row["chg_held_windows"] = " ".join(w for w in metrics.CAP_WINDOWS if w in held_windows)
    row["cap_price"] = metrics.fmt(metrics.cap_price(clean, today))
    row["sales_total"] = len(clean)
    if clean:
        prices = [p for _, p, _, *_ in clean]
        row["highest_sale"], row["lowest_sale"] = metrics.fmt(max(prices)), metrics.fmt(min(prices))
        row["sales_first_date"] = clean[0][0].isoformat()
    row["outliers_excluded"] = outliers
    return row


def build(store=HERE, out=LATEST, today=None):
    assets = {a["asset_id"]: a for a in metrics.read_csv(store / "assets.csv")} if (store / "assets.csv").exists() else {}
    if today is None:
        days = sorted(p.stem for p in (store / "daily").glob("*.csv"))
        today = metrics.d(days[-1]) if days else datetime.now(timezone.utc).date()
    pops = load_grade_pops(store, today)
    if not pops:
        print(f"all_grades: no {GRADE_POPS} in {store} yet (no run has asked a card at every grade), nothing written")
        return None
    # The category by the scope's own rule over every asset the feed knows, plus whatever has a pops row,
    # less the records alt.xyz no longer indexes (cards.csv's rule, 2026-10-22)
    cards = (category_assets(assets) | set(pops)) - unlisted_assets(store, today)
    by_grade = load_sales(store, cards)
    rows, sale_rows = [], []
    n_sales = 0
    for aid in sorted(cards):
        ident = assets.get(aid, {"asset_id": aid})
        for g in GRADES:
            clean_by, _, outliers, raw_by, held_high, single_high = by_grade[g]
            clean, raw = clean_by.get(aid, []), raw_by.get(aid, [])
            rows.append(grade_row(ident, g, pops.get(aid), clean, raw, held_high.get(aid, []), single_high.get(aid, set()),
                                  outliers.get(aid, 0), today))
            clean_keys = {(t[0], t[1]) for t in clean}
            for sd, price, src, stype, status, url in sorted(raw, reverse=True):
                outlier = 1 if (status == "ok" and (sd, price) not in clean_keys) else 0
                sale_rows.append([aid, g, sd.isoformat(), metrics.fmt(price), src, stype, status, outlier, url])
                n_sales += 1
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "grades.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=GRADE_COLS)
        w.writeheader()
        w.writerows(rows)
    with open(out / "sales.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(SALE_COLS)
        w.writerows(sale_rows)
    dates = sorted(p[0]["data_date"] for p in pops.values())
    summary = {"data_date": today.isoformat(), "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "cards": len(cards), "cards_with_pops": len(pops), "grades": GRADES, "grade_rows": len(rows), "sales": n_sales,
               "clean_sales": sum(r["sales_total"] for r in rows), "pops_date_oldest": dates[0], "pops_date_newest": dates[-1],
               "rows_with_a_sale": sum(1 for r in rows if r["sales_total"]), "rows_with_pop": sum(1 for r in rows if metrics.to_int(r["pop"]))}
    with open(out / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
        f.write("\n")
    print(f"all_grades: {len(cards)} card(s) ({len(pops)} with a pop table) x {len(GRADES)} grades -> {out}/grades.csv ({len(rows)} rows), "
          f"sales.csv ({n_sales} rows, {summary['clean_sales']} clean); pops as of {dates[0]}..{dates[-1]}")
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default=str(HERE), help="history store directory (default: this folder)")
    ap.add_argument("--out", default=str(LATEST), help="output directory (default: latest/all_grades)")
    args = ap.parse_args()
    build(Path(args.store), Path(args.out))


if __name__ == "__main__":
    main()
