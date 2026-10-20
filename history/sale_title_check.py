#!/usr/bin/env python3
"""eBay title check of held-back eBay sales (2026-10-20; Sid's "Samay Work" list: "eBay title
check on far-out sales (catches mislabeled listings)").

When a card's newest sale is one the outlier filter holds back (last_sale_unconfirmed = 1 in
latest/cards.csv, psa9_last_sale_unconfirmed for PSA 9) and it is an eBay item, ask eBay's Browse
API for the sold item's own record (getItemByLegacyId: the call, keyset and daily budget of
ebay_verify.py) and judge its TITLE against the card record alt.xyz attached the sale to. alt.xyz
pairs eBay sales to its card records by text: on the 1st Edition Base Set Charizard it lists more
$3k-$26k "PSA 10" sales of other Charizards than real $300k+ ones, and the 2026-09-29 lot-page
check found 27% of held-back major-house lots were another item. eBay serves a sold item for
roughly 90 days, with title, bidCount, uniqueBidderCount, itemEndDate and the final price; it
never says whether the buyer paid (alt.xyz's NOT_PAID / RELISTED flags are that signal:
last_sale_status in metrics.py).

    python3 history/sale_title_check.py check --out DIR [--budget N] [--workers N] [--apply]
    python3 history/sale_title_check.py apply DIR
    python3 history/sale_title_check.py rejudge      # judge() again over the titles on file, no eBay calls

check    picks up to --budget (item, card, grade) triples not yet on file, newest sale first (eBay
         forgets sold items, so the fresh ones are the ones it can still answer), asks eBay and
         writes DIR/title_results.csv + DIR/title_check.json (counts, calls used and a one-line
         note; nightly/run_ebay_verify.sh runs this before the Buy It Now verify and takes the
         calls off that verify's budget). Nothing in history/ changes.
apply    folds DIR/title_results.csv into history/sale_title_checks.csv. Idempotent; a verdict on
         file is kept (a sold item's title never changes). metrics.py reads that file into
         cards.csv last_sale_title_check / psa9_last_sale_title_check for the newest sale.
rejudge  after a change to judge(): every stored title is judged again (unavailable rows stay).

Verdicts (judge):
  ok           the title names this card at this grade by the watchlist sweep's strict rule
               (watchlist_listings.title_matches: PSA and exactly this grade, every word of the
               subject, the card number as a token with the right printed total, the card's
               printing, set evidence, no lot / autograph / other language / reprint word).
  mismatch     the title contradicts the card: another PSA grade; a card number with a total that is
               not this card's; 1st Edition / Shadowless in the title for a card that is not one,
               or missing for a card that is (sellers always write them: they multiply the price);
               Reverse Holo, Gold Star, Lv.X, Prime or Shiny in the title for a card that is not;
               a lot, autograph, jumbo, other language, reprint or other refuse word; a year over a
               year off the card's; or no word of the card's subject at all. The sale is another
               item's.
  unclear      neither: too vague to judge (no grade or number in the title, a subject word
               missing, no set evidence). Left to the reader, never guessed either way.
  unavailable  eBay no longer serves the item (HTTP 404, errorId 11003): the usual answer for a sale
               over ~90 days old.
No answer (another HTTP error, a timeout, eBay's rate limit) records nothing: asked again next run.
A verdict is a flag. No price, count or other flag in the feed moves on it.

Standard library only, like the rest of the feed."""
import argparse
import csv
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import ebay_verify   # noqa: E402
import ingest        # noqa: E402
from watchlist_listings import (Card, GRADE_RX, REFUSE_PHRASES, REFUSE_WORDS, STRONG_VARIANTS, YEAR_RX,   # noqa: E402
                                has_word, load_cards, norm, title_matches, words)

CHECKS = "sale_title_checks.csv"       # history/: every verdict, keyed by (item_id, asset_id, grade)
RESULTS = "title_results.csv"          # <out>/: one check's answers
STATUS = "title_check.json"            # <out>/: the check's and apply's counts, what nightly/run_ebay_verify.sh reads
CHECK_COLS = ["item_id", "asset_id", "grade", "status", "reason", "title", "bid_count", "unique_bidders", "ebay_price",
              "end_date", "checked_at"]
STATUSES = ("ok", "mismatch", "unclear", "unavailable")
DEFAULT_BUDGET = 400                   # eBay calls per run: ~1,300 held eBay sales to ask about once (2026-10-20), then a few dozen a day
# grade -> (the cards.csv flag that says the newest sale at that grade is held, the shard dir with the sale's URL)
HELD_COLS = {"10.0": ("last_sale_unconfirmed", "recent_sales"), "9.0": ("psa9_last_sale_unconfirmed", "recent_sales_psa9")}
FRACTION_RX = re.compile(r"(?<![a-z0-9])#?([a-z]{0,2}\d{1,3})\s?/\s?([a-z]{0,2}\d{1,3})(?![a-z0-9/])")   # "4/102", "#17/17", "H28/H32"; not a date's "12/25/2024"


def short_grade(grade):
    """'10.0' -> '10', the form GRADE_RX finds in a title."""
    return grade[:-2] if grade.endswith(".0") else grade


def judge(card, grade, title):
    """(status, reason) for a sold item's eBay title against the card record alt.xyz attached the
    sale to: ok / mismatch / unclear, as the module doc defines them."""
    g = short_grade(grade)
    t = norm(title)
    for v in STRONG_VARIANTS:                 # a Gold Star / Lv.X / Prime / Shiny the card is not: title_matches does not look for extras
        if v not in card.variants and re.search(rf"\b{v}\b", t):
            return "mismatch", f"{v!r} in the title"
    okay, why = title_matches(card, g, title)
    if okay:
        return "ok", ""
    grades = set(GRADE_RX.findall(t))
    if grades and grades != {g}:
        return "mismatch", f"grade {'/'.join(sorted(grades))} in the title"
    tw = set(words(t))
    for w in sorted(REFUSE_WORDS):
        if ((" " in w and w in t) or (" " not in w and w in tw)) and w not in card.subject_words and w not in card.set_words:
            return "mismatch", f"{w!r} in the title"
    for p in REFUSE_PHRASES:
        if p in t:
            return "mismatch", f"{p!r} in the title"
    for y in YEAR_RX.findall(t):
        if card.year.isdigit() and abs(int(y) - int(card.year)) > 1:
            return "mismatch", f"year {y} vs {card.year}"
    fracs = FRACTION_RX.findall(t)
    if fracs and not any((n.lstrip("0") or "0") == card.number and (not card.total or not tot.isdigit() or tot.lstrip("0") == card.total) for n, tot in fracs):
        n, tot = fracs[0]
        return "mismatch", f"number {n}/{tot} vs {card.number}" + (f"/{card.total}" if card.total else "")
    has_1st = bool(re.search(r"\b1st edition\b", t))
    if has_1st != card.first_edition:
        return "mismatch", "1st edition " + ("in the title" if has_1st else "missing")
    if ("shadowless" in tw) != card.shadowless:
        return "mismatch", "shadowless " + ("in the title" if "shadowless" in tw else "missing")
    if re.search(r"\b(reverse|rev)\b", t) and not card.reverse:
        return "mismatch", "reverse holo in the title"
    subject = [w for w in card.subject_words if w not in card.short_words]
    if subject and not any(has_word(tw, w) for w in subject):
        return "mismatch", "no word of the subject"
    return "unclear", why


def load_checks(store):
    path = Path(store) / CHECKS
    return {(r["item_id"], r["asset_id"], r["grade"]): r for r in ingest.read_csv(path)} if path.exists() else {}


def write_checks(store, checks):
    ingest.write_csv_atomic(Path(store) / CHECKS, CHECK_COLS,
                            sorted(checks.values(), key=lambda r: (r["asset_id"], r["grade"], r["item_id"])))


def candidates(latest, checks):
    """(item_id, asset_id, grade, date, price) for every card whose newest sale at a grade is held back
    (cards.csv) and is an eBay item not yet on file; newest sale first, then the dearer one."""
    held = {g: set() for g in HELD_COLS}
    with open(Path(latest) / "cards.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for g, (col, _) in HELD_COLS.items():
                if r.get(col) == "1":
                    held[g].add(r["asset_id"])
    out = []
    for g, (_, shard_dir) in HELD_COLS.items():
        for sh in sorted({aid[:2] for aid in held[g]}):
            p = Path(latest) / shard_dir / f"{sh}.csv"
            if not p.exists():
                continue
            seen = set()
            for r in ingest.read_csv(p):              # newest first per asset: the first status-ok row is the sale in last_sale_*
                aid = r["asset_id"]
                if aid not in held[g] or aid in seen or r["status"] != "ok":
                    continue
                seen.add(aid)
                iid = ebay_verify.item_id(r["url"])
                if iid and (iid, aid, g) not in checks:
                    out.append((iid, aid, g, r["date"], ingest.to_float(r["price"]) or 0.0))
    out.sort(key=lambda t: (t[3], t[4]), reverse=True)
    return out


def verdict_row(d, iid, aid, grade, card, stamp):
    """One results row from eBay's answer (a dict, or "gone"); None when there is nothing to record."""
    row = {c: "" for c in CHECK_COLS}
    row.update(item_id=iid, asset_id=aid, grade=grade, checked_at=stamp)
    if d == "gone":
        row["status"] = "unavailable"
        return row
    title = (d or {}).get("title") or ""
    if not title or card is None:
        return None
    status, reason = judge(card, grade, title)
    row.update(status=status, reason=reason, title=title, bid_count=d.get("bidCount", ""),
               unique_bidders=d.get("uniqueBidderCount", ""), ebay_price=(d.get("price") or {}).get("value", ""),
               end_date=d.get("itemEndDate", ""))
    return row


def check(out, budget=DEFAULT_BUDGET, workers=4, store=HERE, latest=None, urlopen=None, now=None):
    """Ask eBay about up to `budget` held-back eBay sales; write <out>/title_results.csv and
    <out>/title_check.json. Returns the counts."""
    latest = Path(latest) if latest else ROOT / "latest"
    keys = ebay_verify.load_keys()
    if not keys:
        print("sale title check: no EBAY_CLIENT_ID / EBAY_CLIENT_SECRET (env or ~/.ebay_keys), nothing checked")
        ebay_verify.write_status(out, STATUS, asked=0, answered=0, calls=0, keys=False, note="no eBay keys, nothing checked")
        return {"asked": 0, "answered": 0, "calls": 0}
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    todo = candidates(latest, load_checks(store))[:max(0, budget)]
    assets, catalog = load_cards(Path(store), latest)
    cards = {}
    for _, aid, _, _, _ in todo:
        if aid in assets and aid not in cards:
            cards[aid] = Card(assets[aid], catalog.get(aid))
    ebay = ebay_verify.Ebay(keys, urlopen)
    if todo:
        ebay.bearer()                                 # fail fast on a bad keyset, before any lookup
    results, stop, lock, calls = [], threading.Event(), threading.Lock(), [0]

    def one(t):
        iid, aid, g, _, _ = t
        if stop.is_set():
            return None
        with lock:
            calls[0] += 1
        try:
            d = ebay.fetch(iid)
        except ebay_verify.RateLimited:
            stop.set()
            return None
        return verdict_row(d, iid, aid, g, cards.get(aid), stamp) if d is not None else None

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for res in ex.map(one, todo):
            if res:
                results.append(res)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    ingest.write_csv_atomic(out / RESULTS, CHECK_COLS, sorted(results, key=lambda r: (r["asset_id"], r["grade"], r["item_id"])))
    counts = {s: sum(1 for r in results if r["status"] == s) for s in STATUSES}
    note = (f"{len(todo)} held eBay sale(s) asked, {len(results)} answered ({counts['ok']} ok, {counts['mismatch']} mismatch, "
            f"{counts['unclear']} unclear, {counts['unavailable']} unavailable)" + (", stopped on eBay's rate limit" if stop.is_set() else ""))
    print("sale title check: " + note + f"; {calls[0]} eBay call(s)")
    ebay_verify.write_status(out, STATUS, asked=len(todo), answered=len(results), calls=calls[0], **counts,
                             rate_limited=stop.is_set(), keys=True, checked_at=stamp, note=note)
    return {"asked": len(todo), "answered": len(results), "calls": calls[0], **counts, "rate_limited": stop.is_set()}


def apply(out, store=HERE):
    """Fold <out>/title_results.csv into history/sale_title_checks.csv. Idempotent; a verdict on file stays."""
    path = Path(out) / RESULTS
    results = ingest.read_csv(path) if path.exists() else []
    checks = load_checks(store)
    new = 0
    for r in results:
        k = (r["item_id"], r["asset_id"], r["grade"])
        if k not in checks:
            checks[k] = {c: r.get(c, "") for c in CHECK_COLS}
            new += 1
    write_checks(store, checks)
    counts = {s: sum(1 for r in checks.values() if r["status"] == s) for s in STATUSES}
    print(f"sale title check apply: {len(results)} answer(s), {new} new on file; {len(checks)} on file {counts}")
    ebay_verify.write_status(out, STATUS, applied=len(results), new=new, on_file=len(checks))
    return {"applied": len(results), "new": new, "on_file": len(checks)}


def rejudge(store=HERE, latest=None):
    """judge() again over every stored title (after a change to the rule); unavailable rows stay."""
    latest = Path(latest) if latest else ROOT / "latest"
    checks = load_checks(store)
    assets, catalog = load_cards(Path(store), latest)
    changed = 0
    for r in checks.values():
        if not r["title"] or r["asset_id"] not in assets:
            continue
        status, reason = judge(Card(assets[r["asset_id"]], catalog.get(r["asset_id"])), r["grade"], r["title"])
        if (status, reason) != (r["status"], r["reason"]):
            r["status"], r["reason"] = status, reason
            changed += 1
    write_checks(store, checks)
    print(f"sale title check rejudge: {changed} of {len(checks)} verdict(s) changed")
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="ask eBay about the next held-back eBay sales")
    c.add_argument("--out", required=True, help="folder for title_results.csv")
    c.add_argument("--budget", type=int, default=int(os.environ.get("TITLE_CHECK_BUDGET", DEFAULT_BUDGET)))
    c.add_argument("--workers", type=int, default=4)
    c.add_argument("--apply", action="store_true", help="also apply the results to history/")
    c.add_argument("--store", default=str(HERE))
    c.add_argument("--latest", default=str(ROOT / "latest"))
    a = sub.add_parser("apply", help="fold a check's results into history/")
    a.add_argument("out")
    a.add_argument("--store", default=str(HERE))
    r = sub.add_parser("rejudge", help="judge the stored titles again")
    r.add_argument("--store", default=str(HERE))
    r.add_argument("--latest", default=str(ROOT / "latest"))
    args = ap.parse_args()
    if args.cmd == "check":
        check(args.out, args.budget, args.workers, Path(args.store), Path(args.latest))
        if args.apply:
            apply(args.out, Path(args.store))
    elif args.cmd == "apply":
        apply(args.out, Path(args.store))
    else:
        rejudge(Path(args.store), Path(args.latest))


if __name__ == "__main__":
    main()
