#!/usr/bin/env python3
"""Every PSA grade for the Gold Star category (2026-10-21): latest/all_grades/, step 5b of the nightly.

    python3 history/all_grades.py                 # history/ -> latest/all_grades/{grades.csv,sales.csv,summary.json}
                                                  #   + latest/grades/ and latest/recent_sales_grades/ (every card)
    python3 history/all_grades.py --store DIR --out DIR --latest DIR

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
                                 outlier (1 = an ok sale the filter holds back), url (eBay item links
                                 + ?nordt=true, like recent_sales/). PWCC mirror copies dropped, like
                                 recent_sales/.
  latest/all_grades/summary.json cards, grade rows, sales, pops_date range, generated_at.

The pop history itself stays in history/grade_pops.csv (committed, one row per card and night).

EVERY CARD at grades 1 - 8.5 (2026-11-25, Samay: "PSA 1-10 for all pokemon, no longer just 9 and 10"):
the nightly asks every card at every PSA grade in one request (alt_scraper.py --every-grade), so the
store holds every card's sales at every grade and daily/<day>.csv's pops_by_grade its pop at every
grade. build_every_card writes the grades latest/cards.csv does not carry (PSA 10 / PSA 9 stay there,
in recent_sales/ and in recent_sales_psa9/), sharded by the first two hex digits of asset_id like
recent_sales/ (one manifest.json each, history/manifest.py):

  latest/grades/<xx>.csv         grades.csv's columns (above) less the identity ones but asset_id (EVERY_COLS:
                                 latest/cards.csv has them), one row per card and grade 1.0 .. 8.5 that
                                 has copies (pop > 0) or any sale in the store. A grade with neither
                                 (pop a real 0, or unknown, and nothing ever sold) has no row: it would
                                 carry nothing but its pop, which is 0 or blank. Cards: every card with a
                                 pops_by_grade in the last POPS_LOOKBACK_DAYS of daily files (the weekly
                                 full run reaches the cards outside the nightly scope) or a sale at those
                                 grades, less the records alt.xyz no longer indexes. pop / pop_date /
                                 scraped_date = the newest daily row with pops_by_grade; pop_30d_ago from
                                 the newest one at least 30 days old (blank until 30 days of them exist).
  latest/recent_sales_grades/<xx>.csv  the last EVERY_RECENT_N (10) sales per card and grade 1.0 .. 8.5, newest
                                 first, flagged ones included: sales.csv's columns (asset_id, grade, date,
                                 price, source, sale_type, status, outlier, url), as recent_sales/ does for
                                 PSA 10.
  latest/grades/summary.json     cards, grade rows, sales, pops date range, generated_at.

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
import flagged_sales  # noqa: E402
from ingest import GRADE_POPS, POP_GRADES, grade_pops_row, pop_col  # noqa: E402
import manifest  # noqa: E402

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
# Every card (build_every_card, 2026-11-25): the grades cards.csv does not carry, and where they go
EVERY_GRADES = [g for g in GRADES if g not in ("9.0", "10.0")]   # 1.0 .. 8.5
EVERY_DIR, EVERY_SALES_DIR = "grades", "recent_sales_grades"
POPS_LOOKBACK_DAYS = 14   # a card's newest pops_by_grade may be this old (outside the nightly scope: the weekly run)
# Trimmed for size (2026-11-25, Samay: "trim first"; projected over 71.4k cards): no identity columns (card_name,
# year, set, card_number, subject, variety: 23 of grades/' 53 MB, and cards.csv has them by asset_id). The recent
# sales stay at RECENT_N (10, ~75 MB) like recent_sales/: git sends shard changes as deltas (recent_sales/, 37 MB,
# cost 106 KB of the 11-25 nightly's 4.9 MB push), so 5 instead of 10 would save almost nothing per push.
EVERY_COLS = ["asset_id"] + [c for c in GRADE_COLS if c not in IDENTITY]
EVERY_RECENT_N = metrics.RECENT_N


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


def load_sales(store, assets, grades=GRADES):
    """{grade: clean_grade(...)} over the store for these assets (None = every asset) at each of `grades` (one
    pass). A sale Sid flagged (history/flagged_sales.csv) gets the status FLAGGED, as in metrics.load_sales
    (2026-10-28)."""
    raw = {g: defaultdict(list) for g in grades}
    flags = flagged_sales.load(store)
    for p in sorted((store / "sales").glob("*.csv")):
        with open(p, newline="", encoding="utf-8") as f:
            for s in csv.DictReader(f):
                if (assets is not None and s["asset_id"] not in assets) or s.get("grading_company") != "PSA" \
                        or s.get("grade") not in raw:
                    continue
                try:
                    price = float(s["price"])
                except (TypeError, ValueError):
                    continue
                if price <= 0:
                    continue
                status = s.get("skipped_reason") or "ok"
                if flags and flagged_sales.match(flags, s["asset_id"], s["grade"], s["date"], price, s.get("url") or ""):
                    status = flagged_sales.FLAGGED
                raw[s["grade"]][s["asset_id"]].append((metrics.d(s["date"]), price, s.get("source") or "", s.get("sale_type") or "",
                                                       status, s.get("url") or ""))
    return {g: metrics.clean_grade(g, raw[g]) for g in grades}


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
                sale_rows.append([aid, g, sd.isoformat(), metrics.fmt(price), src, stype, status, outlier,
                                  metrics.sale_link(url)])
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


def load_daily_pops(store, today, lookback=POPS_LOOKBACK_DAYS):
    """{asset_id: (newest pops row, row at least 30 days old or None)} from the daily files' pops_by_grade
    (every card since --every-grade, 2026-11-25), in load_grade_pops's shape (ingest.grade_pops_row). The
    newest is looked for in the last `lookback` days, the old one between 30 and 30 + `lookback` days back;
    the files in between are not read."""
    newest, old = {}, {}
    for p in sorted((store / "daily").glob("*.csv"), reverse=True):
        try:
            age = (today - metrics.d(p.stem)).days
        except ValueError:
            continue
        want_new, want_old = 0 <= age <= lookback, 30 <= age <= 30 + lookback
        if age > 30 + lookback:
            break
        if not (want_new or want_old):
            continue
        with open(p, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if not r.get("pops_by_grade"):
                    continue
                aid = r["asset_id"]
                if want_new and aid not in newest:
                    newest[aid] = grade_pops_row({**r, "grading_company": "PSA"}, p.stem)
                if want_old and aid not in old:
                    old[aid] = grade_pops_row({**r, "grading_company": "PSA"}, p.stem)
    return {aid: (newest[aid], old.get(aid)) for aid in newest}


def write_shards(shard_dir, cols, rows_by_shard):
    """<shard_dir>/<xx>.csv, one per shard with rows (old shard files removed first, like
    metrics.write_recent), then <shard_dir>/manifest.json (history/manifest.py)."""
    shard_dir.mkdir(parents=True, exist_ok=True)
    for old_f in shard_dir.glob("*.csv"):
        old_f.unlink()
    for shard, rows in sorted(rows_by_shard.items()):
        with open(shard_dir / f"{shard}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(rows)
    manifest.write_shard_manifest(shard_dir)


def build_every_card(store=HERE, latest=LATEST.parent, today=None):
    """latest/grades/ + latest/recent_sales_grades/ (see the module doc). A no-op (returns None) until a daily
    file carries pops_by_grade, i.e. before the first --every-grade run."""
    assets = {a["asset_id"]: a for a in metrics.read_csv(store / "assets.csv")} if (store / "assets.csv").exists() else {}
    if today is None:
        days = sorted(p.stem for p in (store / "daily").glob("*.csv"))
        today = metrics.d(days[-1]) if days else datetime.now(timezone.utc).date()
    pops = load_daily_pops(store, today)
    # The category's pops from history/grade_pops.csv where it is newer (or the daily files have none): a Gold
    # Star outside the nightly scope then keeps its pop until the weekly run asks it at every grade
    def newer(a, b):
        return b if a is None or (b is not None and b["data_date"] > a["data_date"]) else a
    if pops:
        for aid, (gp_new, gp_old) in load_grade_pops(store, today).items():
            have_new, have_old = pops.get(aid, (None, None))
            pops[aid] = (newer(have_new, gp_new), newer(have_old, gp_old))
    if not pops:
        print(f"every grade: no daily file in the last {POPS_LOOKBACK_DAYS} days carries pops_by_grade yet "
              "(no --every-grade run), latest/grades/ not written")
        return None
    by_grade = load_sales(store, None, EVERY_GRADES)
    with_sales = {aid for g in EVERY_GRADES for aid in by_grade[g][3]}
    cards = (set(pops) | with_sales) - unlisted_assets(store, today)
    rows, recent = defaultdict(list), defaultdict(list)
    n_rows = n_recent = n_clean = 0
    for aid in sorted(cards):
        ident, card_pops = assets.get(aid, {"asset_id": aid}), pops.get(aid)
        newest_pop = card_pops[0] if card_pops else None
        for g in EVERY_GRADES:
            clean_by, _, outliers, raw_by, held_high, single_high = by_grade[g]
            clean, raw = clean_by.get(aid, []), raw_by.get(aid, [])
            if not raw and not (newest_pop and metrics.to_int(newest_pop.get(pop_col(g)))):
                continue   # no copies and nothing ever sold at the grade: no row (module doc)
            row = grade_row(ident, g, card_pops, clean, raw, held_high.get(aid, []), single_high.get(aid, set()),
                            outliers.get(aid, 0), today)
            rows[aid[:2]].append([row[c] for c in EVERY_COLS])
            n_rows += 1
            n_clean += row["sales_total"]
            clean_keys = {(t[0], t[1]) for t in clean}
            for sd, price, src, stype, status, url in sorted(raw, reverse=True)[:EVERY_RECENT_N]:
                outlier = 1 if (status == "ok" and (sd, price) not in clean_keys) else 0
                recent[aid[:2]].append([aid, g, sd.isoformat(), metrics.fmt(price), src, stype, status, outlier,
                                        metrics.sale_link(url)])
                n_recent += 1
    write_shards(latest / EVERY_DIR, EVERY_COLS, rows)
    write_shards(latest / EVERY_SALES_DIR, SALE_COLS, recent)
    dates = sorted(p[0]["data_date"] for p in pops.values())
    summary = {"data_date": today.isoformat(), "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "grades": EVERY_GRADES, "cards": len({aid for shard in rows.values() for aid, *_ in shard}),
               "cards_with_pops": len(pops), "grade_rows": n_rows, "clean_sales": n_clean, "recent_sales_rows": n_recent,
               "sales": sum(len(lst) for g in EVERY_GRADES for lst in by_grade[g][3].values()),
               "pops_date_oldest": dates[0], "pops_date_newest": dates[-1]}
    with open(latest / EVERY_DIR / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
        f.write("\n")
    print(f"every grade: {summary['cards']} card(s) with a row at PSA 1-8.5 ({len(pops)} with a pop table) -> "
          f"{latest / EVERY_DIR}/ ({n_rows} rows, {n_clean} clean sales), {latest / EVERY_SALES_DIR}/ ({n_recent} rows); "
          f"pops as of {dates[0]}..{dates[-1]}")
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default=str(HERE), help="history store directory (default: this folder)")
    ap.add_argument("--out", default=str(LATEST), help="output directory (default: latest/all_grades)")
    ap.add_argument("--latest", default=str(LATEST.parent),
                    help="where latest/grades/ and latest/recent_sales_grades/ go (default: latest)")
    args = ap.parse_args()
    build(Path(args.store), Path(args.out))
    build_every_card(Path(args.store), Path(args.latest))


if __name__ == "__main__":
    main()
