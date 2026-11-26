#!/usr/bin/env python3
"""Who bid on alt.xyz's own auctions (2026-11-26): every bid of every ended Alt auction, so a
shill filter can count how many different bidders were still bidding above a price (Sid's
"Flag 2", PR #1 issuecomment-7153002693).

    python3 history/alt_bids.py fetch [--budget N] [--workers N]   # bids of ended auctions -> the store
    python3 history/alt_bids.py publish                            # latest/alt_auctions.csv + latest/alt_bidders/
    python3 history/alt_bids.py seed-from-git [--rev origin/main]  # one-off: every Alt auction id in
                                                                   # live_listings.csv's git history -> the ledger

Where the data comes from. alt.xyz's public GraphQL answers `auctionBidFeed(listingId)` for a
running or ENDED auction, logged in or not, with every bid: amount, time, whether alt.xyz placed
it as an automatic (proxy) bid, and the bidder as a fixed anonymous key (32 hex, the same key on
every auction that bidder joins) plus a masked handle ("UR***"). The listing index the site
searches drops an auction once it ends, and the sale alt.xyz later lists for it links to a
different id (exchange/select-listing?ids=..., not the listing), so the listing id has to be
kept while the auction runs: every complete pull of Alt's own listings (alt_scraper.py
--alt-listings: each nightly and each listings refresh) is recorded in the ledger
history/alt_auctions.csv by ingest.ingest_alt -> record(). Ids from 2026-10-06 (the first Alt
pull) to the day this shipped came from live_listings.csv's git history (seed-from-git).

Checked on 2026-11-26 against the store's Alt sales: an Alt auction's sale price is the final
bid plus alt.xyz's 20% buyer's premium (792 -> 950.40, 5,000 -> 6,000), dated the US day the
auction ended (they end 02:02 UTC on Fridays = Thursday evening in the US). That is how publish
pairs an auction with its sale: same card and grade, source Alt, sale type AUCTION, price =
round(final bid x 1.2, 2), sale date within a day of the end. Two auctions of one card ending the
same day at the same bid are paired one to one in id order (their sales are interchangeable).

Files
  history/alt_auctions.csv        (git) the ledger: one row per Alt auction ever seen running.
                                  Rows are only added (end_date moves if alt.xyz extends one).
  history/alt_bids/auctions-<YYYY-MM>.csv  (store) one row per auction whose bids were asked
                                  for, by the month it ended: status ok / not_found, the end
                                  alt.xyz gave, final bid, bid count and bidders.
  history/alt_bids/bids-<YYYY-MM>.csv      (store) one row per bid.
  latest/alt_auctions.csv         one row per checked auction with >= 1 bid, plus the sale it became
  latest/alt_bidders/<xx>.csv     one row per bidder per auction (max bid, bid count, first and
                                  last bid time), sharded by the first two characters of asset_id
                                  like recent_sales/, with a manifest (history/manifest.py).

An auction is asked for once, SETTLE_HOURS after its end (an auction alt.xyz extended past that is
left for the next run: its auctionEndsAt is still ahead). fetch asks the most recently ended first
and stops at --budget requests, so a backlog drains newest first over a few nights. A request that
fails is simply not recorded and is asked again next run. Standard library only."""
import argparse
import csv
import os
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from io import StringIO
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LATEST = ROOT / "latest"

LEDGER = "alt_auctions.csv"
LEDGER_COLS = ["alt_listing_id", "asset_id", "grade", "end_date", "first_seen"]
RAW_DIR = "alt_bids"
CHECK_COLS = ["alt_listing_id", "asset_id", "grade", "ended_at", "checked_at", "status", "final_bid", "bid_count", "bidders"]
BID_COLS = ["alt_listing_id", "occurred_at", "amount", "is_automatic", "bidder_key", "bidder_handle"]   # card + grade: the auctions file
OUT_AUCTION_COLS = ["alt_listing_id", "asset_id", "grade", "ended_at", "final_bid", "price_with_premium", "bid_count",
                    "bidders", "auto_bids", "winner", "winner_bids", "runner_up", "runner_up_max_bid",
                    "sale_date", "sale_price", "sale_url", "checked_at"]
OUT_BIDDER_COLS = ["alt_listing_id", "asset_id", "grade", "bidder", "max_bid", "bids", "auto_bids",
                   "first_bid_at", "last_bid_at"]
OUT_AUCTIONS = "alt_auctions.csv"
OUT_BIDDERS = "alt_bidders"

ALT_SOURCE = "Alt"
PREMIUM = Decimal("1.2")   # alt.xyz's buyer's premium on its own auctions: sale price = final bid x 1.2
SETTLE_HOURS = 2           # ask for an auction's bids this long after its end
DEFAULT_BUDGET = 3000      # requests per run (a weekly batch is ~2,000 auctions at PSA 10 + 9)
SAVE_EVERY = 500           # fetch writes the store after this many answers
BIDDER_KEY_LEN = 12       # hex characters of alt.xyz's 32-hex bidder key published in latest/ (48 bits)

Q_BID_FEED = """
query AuctionBidFeed($id: ID!) {
  auctionBidFeed(listingId: $id) {
    bidCount auctionEndsAt currentPrice
    events { bidAmount isAutomatic occurredAt bidder { key handle } }
    error { __typename ... on BaseApiError { message } }
  }
}
"""


# --- small helpers --------------------------------------------------------------------------------

def read_csv(path):
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv_atomic(path, cols, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def when(s):
    """An ISO time as an aware datetime (UTC when it carries no offset), or None."""
    if not s:
        return None
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def utc_iso(t):
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def num(s):
    """alt.xyz's "6250.000000" -> "6250", "4957.500000" -> "4957.5"; blank stays blank."""
    if s in (None, ""):
        return ""
    d = Decimal(str(s)).normalize()
    return format(d, "f") if d != d.to_integral() else str(int(d))


def norm_grade(g):
    try:
        return f"{float(g):.1f}"
    except (TypeError, ValueError):
        return g or ""


def listing_id(url_or_id):
    """alt.xyz/itm/<id> -> <id>; an id stays an id."""
    return (url_or_id or "").rstrip("/").rsplit("/", 1)[-1]


def month_of(iso):
    return (iso or "")[:7] or "unknown"


# --- the ledger -----------------------------------------------------------------------------------

def record(rows, store=HERE, day=""):
    """Add every auction of a complete Alt pull (alt_listings.csv rows, listing_type AUCTION) to the
    ledger; an auction already there keeps its first_seen and takes the newer end_date. Returns the
    number of auctions new to the ledger."""
    path = Path(store) / LEDGER
    ledger = {r["alt_listing_id"]: r for r in read_csv(path)}
    new = 0
    for r in rows:
        if r.get("listing_type") != "AUCTION" or r.get("source", ALT_SOURCE) != ALT_SOURCE:
            continue
        lid = r.get("alt_listing_id") or listing_id(r.get("url"))
        if not lid:
            continue
        old = ledger.get(lid)
        row = {"alt_listing_id": lid, "asset_id": r["asset_id"], "grade": norm_grade(r["grade"]),
               "end_date": r.get("end_date", ""), "first_seen": (old or {}).get("first_seen") or day}
        if old is None:
            new += 1
        elif old == row:
            continue
        ledger[lid] = row
    write_csv_atomic(path, LEDGER_COLS, sorted(ledger.values(), key=lambda r: (r["end_date"], r["alt_listing_id"])))
    return new


def seed_from_git(store=HERE, rev="origin/main", since="2026-10-05"):
    """One-off backfill: every Alt auction in every committed version of history/live_listings.csv
    (since Alt's own listings were first pulled, 2026-10-06) into the ledger. first_seen = the
    earliest version's first_seen column, else that commit's day."""
    hashes = subprocess.run(["git", "log", "--reverse", "--format=%h %cs", rev, f"--since={since}", "--",
                             "history/live_listings.csv"], capture_output=True, text=True, check=True, cwd=ROOT).stdout.split("\n")
    rows = []
    for line in filter(None, hashes):
        h, day = line.split()
        txt = subprocess.run(["git", "show", f"{h}:history/live_listings.csv"], capture_output=True, text=True,
                             check=True, cwd=ROOT).stdout
        for r in csv.DictReader(StringIO(txt)):
            if r.get("source") == ALT_SOURCE and r.get("listing_type") == "AUCTION":
                r["first_seen"] = r.get("first_seen") or day
                rows.append(r)
    # record() keeps the first first_seen it meets and the newest end_date, as the versions come oldest first
    path = Path(store) / LEDGER
    ledger = {r["alt_listing_id"]: r for r in read_csv(path)}
    for r in rows:
        lid = listing_id(r["url"])
        old = ledger.get(lid)
        ledger[lid] = {"alt_listing_id": lid, "asset_id": r["asset_id"], "grade": norm_grade(r["grade"]),
                       "end_date": r["end_date"], "first_seen": (old or {}).get("first_seen") or r["first_seen"]}
    write_csv_atomic(path, LEDGER_COLS, sorted(ledger.values(), key=lambda r: (r["end_date"], r["alt_listing_id"])))
    print(f"alt_auctions.csv: {len(ledger)} auctions from {len([h for h in hashes if h])} versions of live_listings.csv")
    return len(ledger)


# --- the store's raw bids -------------------------------------------------------------------------

def raw_dir(store=HERE):
    return Path(store) / RAW_DIR


def load_checks(store=HERE):
    out = {}
    for p in sorted(raw_dir(store).glob("auctions-*.csv")):
        for r in read_csv(p):
            out[r["alt_listing_id"]] = r
    return out


def load_bids(store=HERE):
    out = defaultdict(list)
    for p in sorted(raw_dir(store).glob("bids-*.csv")):
        for r in read_csv(p):
            out[r["alt_listing_id"]].append(r)
    return out


def save(store, checks, bids):
    """Merge new check rows + their bids into the month files (a listing's old rows replaced)."""
    by_month = defaultdict(list)
    for c in checks:
        by_month[month_of(c["ended_at"])].append(c)
    d = raw_dir(store)
    for month, cs in by_month.items():
        ids = {c["alt_listing_id"] for c in cs}
        ap, bp = d / f"auctions-{month}.csv", d / f"bids-{month}.csv"
        a_rows = [r for r in read_csv(ap) if r["alt_listing_id"] not in ids] + cs
        b_rows = [r for r in read_csv(bp) if r["alt_listing_id"] not in ids] + [b for c in cs for b in bids.get(c["alt_listing_id"], [])]
        write_csv_atomic(ap, CHECK_COLS, sorted(a_rows, key=lambda r: (r["ended_at"], r["alt_listing_id"])))
        write_csv_atomic(bp, BID_COLS, sorted(b_rows, key=lambda r: (r["alt_listing_id"], r["occurred_at"], r["amount"])))


# --- fetch ----------------------------------------------------------------------------------------

def due(ledger, checks, bids, now, settle_hours=SETTLE_HOURS):
    """Ledger auctions to ask about: ended at least settle_hours ago and never checked, or checked ok
    with bids while the store holds none of them (a lost store file heals itself). Newest end first."""
    cutoff = now - timedelta(hours=settle_hours)
    out = []
    for r in ledger:
        end = when(r["end_date"])
        if not end or end > cutoff:
            continue
        c = checks.get(r["alt_listing_id"])
        if c is None or (c["status"] == "ok" and int(c["bid_count"] or 0) > 0 and r["alt_listing_id"] not in bids):
            out.append(r)
    return sorted(out, key=lambda r: (r["end_date"], r["alt_listing_id"]), reverse=True)


def ask_feed(lid):
    import alt_scraper   # noqa: E402  (repo root on sys.path, see main)
    return alt_scraper.gql("AuctionBidFeed", Q_BID_FEED, {"id": lid}).get("auctionBidFeed") or {}


def parse_feed(row, feed, now):
    """(check row, [bid rows]) for one ledger row and its feed answer; (None, []) when the auction
    has not ended yet (alt.xyz extended it) or the answer is unusable, so it is asked again later."""
    lid = row["alt_listing_id"]
    base = {"alt_listing_id": lid, "asset_id": row["asset_id"], "grade": row["grade"], "checked_at": utc_iso(now)}
    err = feed.get("error")
    if err:
        msg = (err.get("message") or "").lower()
        if "not found" in msg:
            return dict(base, ended_at=row["end_date"], status="not_found", final_bid="", bid_count="", bidders=""), []
        return None, []
    ends = when(feed.get("auctionEndsAt"))
    if ends is None or ends > now:
        return None, []
    events = feed.get("events") or []
    bids = [{"alt_listing_id": lid, "occurred_at": e.get("occurredAt") or "", "amount": num(e.get("bidAmount")),
             "is_automatic": "1" if e.get("isAutomatic") else "0",
             "bidder_key": (e.get("bidder") or {}).get("key") or "", "bidder_handle": (e.get("bidder") or {}).get("handle") or ""}
            for e in events]
    count = feed.get("bidCount")
    if count is not None and int(count) != len(bids):
        return None, []   # a partial list would undercount bidders: ask again next run
    return dict(base, ended_at=utc_iso(ends), status="ok", final_bid=num(feed.get("currentPrice")) if bids else "",
                bid_count=str(len(bids)), bidders=str(len({b["bidder_key"] for b in bids}))), bids


def fetch(store=HERE, budget=DEFAULT_BUDGET, workers=2, now=None, ask=ask_feed):
    now = now or datetime.now(timezone.utc)
    ledger = read_csv(Path(store) / LEDGER)
    checks, bids = load_checks(store), load_bids(store)
    todo = due(ledger, checks, bids, now)
    asked = todo[:budget]
    failed, n_checks, n_not_found, n_rows = 0, 0, 0, 0

    def one(r):
        try:
            return r, ask(r["alt_listing_id"]), None
        except Exception as e:   # noqa: BLE001  (a failed ask is retried next run, never recorded)
            return r, None, e

    # Saved in chunks, so a run cut short (a timeout, a laptop asleep) keeps what it already read.
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for i in range(0, len(asked), SAVE_EVERY):
            results = []
            for r, feed, err in ex.map(one, asked[i:i + SAVE_EVERY]):
                if err is not None or feed is None:
                    failed += 1
                    continue
                results.append(parse_feed(r, feed, now))
            new_checks = [c for c, _ in results if c]
            new_bids = {c["alt_listing_id"]: b for c, b in results if c}
            save(store, new_checks, new_bids)
            n_checks += len(new_checks)
            n_not_found += sum(1 for c in new_checks if c["status"] == "not_found")
            n_rows += sum(len(b) for b in new_bids.values())
    stats = {"alt_bids_due": len(todo), "alt_bids_asked": len(asked), "alt_bids_checked": n_checks,
             "alt_bids_not_found": n_not_found, "alt_bids_not_ended": len(asked) - failed - n_checks,
             "alt_bids_failed": failed, "alt_bids_rows": n_rows, "alt_bids_left": len(todo) - len(asked)}
    print("alt bids: " + ", ".join(f"{k[9:]} {v}" for k, v in stats.items()))
    return stats


# --- publish --------------------------------------------------------------------------------------

def premium_price(final_bid):
    return (Decimal(final_bid) * PREMIUM).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def load_alt_auction_sales(store=HERE, months=()):
    """(asset, grade) -> [sale rows] of Alt auction sales in the store's sales/<month>.csv files."""
    out = defaultdict(list)
    for m in months:
        for r in read_csv(Path(store) / "sales" / f"{m}.csv"):
            if r.get("source") == ALT_SOURCE and r.get("sale_type") == "AUCTION":
                out[(r["asset_id"], norm_grade(r["grade"]))].append(r)
    return out


def match_sales(checks, sales):
    """alt_listing_id -> the sale row it became (see the module doc), one sale per auction."""
    used, out = set(), {}
    for c in sorted(checks, key=lambda c: c["alt_listing_id"]):
        if c["status"] != "ok" or not c["final_bid"]:
            continue
        price, end = premium_price(c["final_bid"]), when(c["ended_at"])
        days = {(end + timedelta(days=k)).date().isoformat() for k in (-1, 0)}
        for s in sales.get((c["asset_id"], c["grade"]), []):
            key = s.get("alt_tx_id") or s.get("url")
            if key in used or s["date"] not in days:
                continue
            try:
                if Decimal(s["price"]) != price:
                    continue
            except ArithmeticError:
                continue
            used.add(key)
            out[c["alt_listing_id"]] = s
            break
    return out


def out_grade(g):
    """The store's "10.0" -> "10", "9.0" -> "9" (latest/live_auctions.csv's form)."""
    return g[:-2] if g.endswith(".0") else g


def bidder_rows(c, bids):
    per = {}
    for b in bids:
        k = b["bidder_key"][:BIDDER_KEY_LEN]
        p = per.setdefault(k, {"alt_listing_id": c["alt_listing_id"], "asset_id": c["asset_id"], "grade": out_grade(c["grade"]),
                               "bidder": k, "max_bid": Decimal(0), "bids": 0, "auto_bids": 0,
                               "first_bid_at": b["occurred_at"], "last_bid_at": b["occurred_at"]})
        p["max_bid"] = max(p["max_bid"], Decimal(b["amount"] or 0))
        p["bids"] += 1
        p["auto_bids"] += b["is_automatic"] == "1"
        p["first_bid_at"] = min(p["first_bid_at"], b["occurred_at"])
        p["last_bid_at"] = max(p["last_bid_at"], b["occurred_at"])
    rows = sorted(per.values(), key=lambda p: (-p["max_bid"], p["first_bid_at"]))
    for p in rows:
        p["max_bid"] = num(p["max_bid"])
        p["first_bid_at"] = utc_iso(when(p["first_bid_at"]))
        p["last_bid_at"] = utc_iso(when(p["last_bid_at"]))
    return rows


def publish(store=HERE, out=LATEST):
    checks, bids = load_checks(store), load_bids(store)
    with_bids = [c for c in checks.values() if c["status"] == "ok" and int(c["bid_count"] or 0) > 0
                 and c["alt_listing_id"] in bids]
    months = set()
    for c in with_bids:
        end = when(c["ended_at"])
        months |= {(end + timedelta(days=k)).strftime("%Y-%m") for k in (-1, 0)}
    sales = match_sales(with_bids, load_alt_auction_sales(store, sorted(months)))
    auctions, shards = [], defaultdict(list)
    for c in with_bids:
        rows = bidder_rows(c, bids[c["alt_listing_id"]])
        shards[c["asset_id"][:2]].extend(rows)
        s = sales.get(c["alt_listing_id"], {})
        auctions.append({
            "alt_listing_id": c["alt_listing_id"], "asset_id": c["asset_id"], "grade": out_grade(c["grade"]),
            "ended_at": c["ended_at"], "final_bid": c["final_bid"], "price_with_premium": num(premium_price(c["final_bid"])),
            "bid_count": c["bid_count"], "bidders": c["bidders"],
            "auto_bids": str(sum(1 for b in bids[c["alt_listing_id"]] if b["is_automatic"] == "1")),
            "winner": rows[0]["bidder"], "winner_bids": str(rows[0]["bids"]),
            "runner_up": rows[1]["bidder"] if len(rows) > 1 else "",
            "runner_up_max_bid": rows[1]["max_bid"] if len(rows) > 1 else "",
            "sale_date": s.get("date", ""), "sale_price": num(s.get("price")) if s else "", "sale_url": s.get("url", ""),
            "checked_at": c["checked_at"]})
    auctions.sort(key=lambda r: (r["asset_id"], r["grade"], r["ended_at"], r["alt_listing_id"]))
    out = Path(out)
    write_csv_atomic(out / OUT_AUCTIONS, OUT_AUCTION_COLS, auctions)
    bdir = out / OUT_BIDDERS
    bdir.mkdir(parents=True, exist_ok=True)
    keep = set()
    for prefix, rows in shards.items():
        rows.sort(key=lambda r: (r["asset_id"], r["grade"], r["alt_listing_id"], -Decimal(r["max_bid"]), r["first_bid_at"]))
        write_csv_atomic(bdir / f"{prefix}.csv", OUT_BIDDER_COLS, rows)
        keep.add(f"{prefix}.csv")
    for p in bdir.glob("*.csv"):
        if p.name not in keep:
            p.unlink()
    matched = sum(1 for a in auctions if a["sale_url"])
    stats = {"alt_auctions_published": len(auctions), "alt_auctions_matched": matched,
             "alt_bidder_rows": sum(len(r) for r in shards.values())}
    print(f"alt bids publish: {len(auctions)} auctions with bids ({matched} paired with their sale), "
          f"{stats['alt_bidder_rows']} bidder rows in {len(shards)} shards")
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["fetch", "publish", "seed-from-git"])
    ap.add_argument("--store", default=str(HERE), help="the history folder (default: this one)")
    ap.add_argument("--latest", default=str(LATEST), help="publish: the latest/ folder")
    ap.add_argument("--budget", type=int, default=int(os.environ.get("ALT_BIDS_BUDGET", DEFAULT_BUDGET)),
                    help=f"fetch: most requests this run (env ALT_BIDS_BUDGET, default {DEFAULT_BUDGET})")
    ap.add_argument("--workers", type=int, default=2, help="fetch: requests at once (default 2)")
    ap.add_argument("--delay", type=float, default=None, help="fetch: pause after each request (alt_scraper's default 0.5 s)")
    ap.add_argument("--rev", default="origin/main", help="seed-from-git: the history to read")
    args = ap.parse_args(argv)
    if args.command == "fetch":
        sys.path.insert(0, str(ROOT))
        import alt_scraper   # noqa: E402
        if args.delay is not None:
            alt_scraper.DELAY_SECONDS = max(0.0, args.delay)
        fetch(Path(args.store), args.budget, args.workers)
    elif args.command == "publish":
        publish(Path(args.store), Path(args.latest))
    else:
        seed_from_git(Path(args.store), args.rev)


if __name__ == "__main__":
    main()
