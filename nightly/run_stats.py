#!/usr/bin/env python3
"""The nightly run's account of itself: snapshots/<day>/run_stats.json (2026-10-17).

alt_scraper.py writes one section per step as it goes: "scrape" (cards asked / ok / failed / carried
and the live-listings checks asked / failed at PSA 10 and PSA 9, over every --resume pass), "alt" and
"fanatics" (the bulk pulls: ok / failed, the Fanatics requests used vs budget). This script adds the
run-level fields and judges the run, so a partial or failed scrape is never committed as the day's
data with nothing that says so (until then run_nightly.sh retried on a log grep and `|| true`d the
scraper, and summary.json had no per-run counts):

    nightly/run_stats.py start OUT --day DAY --scope SCOPE   # day, scope, started_at (the scope stage)
    nightly/run_stats.py merge OUT           # the assemble stage, after nightly/shards.py merge: the shards'
                                             # accounts (OUT/shard-<i>/run_stats.json, one per scrape job)
                                             # added up into OUT's "scrape" section, each kept under
                                             # "shards". A shard whose scraper never finished is accounted
                                             # from its files: scope.txt's cards, cards.csv's rows ok, the
                                             # rest failed. No shard folders = nothing to do.
    nightly/run_stats.py gate OUT            # after the scrape: exit 1 (so run_nightly.sh stops before the
                                             # pulls, the ingest and the commit) when scraped ok is under
                                             # NIGHTLY_MIN_SCRAPED_PCT (90) % of the scope, or the listings
                                             # checks failed over NIGHTLY_MAX_LISTINGS_FAILED_PCT (20) % of
                                             # those asked at either grade. The verdict is written either way.
    nightly/run_stats.py finish OUT [--store history]   # finished_at + the Fanatics pull's health: nights
                                             # since the last successful direct pull (from the newest direct
                                             # row in history/live_listings.csv)
    nightly/run_stats.py fanatics-health [OUT]   # after the commit (its own workflow step, so the night's
                                             # data lands either way): exit 1 when the direct Fanatics pull
                                             # failed tonight and has not succeeded for
                                             # NIGHTLY_FANATICS_MAX_FAILED_NIGHTS (3) nights, or never. The
                                             # direct rows age out of live_listings.csv after 7 days without
                                             # a pull, so this shows red first. OUT defaults to the run in
                                             # snapshots/last_run.env.
    nightly/run_stats.py commit-message [--prefix nightly] [--summary latest/summary.json]
                                             # "<prefix> <date>: N assets, M sales; <the run's counts>";
                                             # nightly/already_ran.sh matches commits on "<prefix> <date>"

history/ingest.py copies the file to history/last_run.json and history/metrics.py publishes it as
latest/summary.json `last_run`. Standard library only; plain functions so history/test_run_stats.py
can call them."""
import argparse
import csv
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATS = "run_stats.json"
DEFAULTS = {"NIGHTLY_MIN_SCRAPED_PCT": 90.0, "NIGHTLY_MAX_LISTINGS_FAILED_PCT": 20.0, "NIGHTLY_FANATICS_MAX_FAILED_NIGHTS": 3}


def knob(name, value=None):
    """A threshold: the argument when given, else the environment, else the default."""
    if value is not None:
        return value
    raw = os.environ.get(name)
    return type(DEFAULTS[name])(raw) if raw not in (None, "") else DEFAULTS[name]


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(out):
    path = Path(out) / STATS
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(out, stats):
    path = Path(out) / STATS
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(STATS + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=1)
        f.write("\n")
    tmp.replace(path)


def start(out, day, scope):
    stats = load(out)
    stats.update({"day": day, "scope": scope, "started_at": utc_now()})
    save(out, stats)
    print(f"run_stats: {scope} run {day} started {stats['started_at']} -> {Path(out) / STATS}")
    return stats


SUM_KEYS = ("scope", "ok", "failed", "skipped", "carried", "listings_asked", "listings_failed",
            "psa9_listings_asked", "psa9_listings_failed", "all_grades_cards", "all_grades_requests")


def scope_size(path):
    """Cards in a scope.txt (asset lines; comments and blanks are not cards)."""
    if not Path(path).exists():
        return 0
    return sum(1 for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip() and not l.lstrip().startswith("#"))


def account_from_files(d):
    """A shard whose scraper never wrote its account: what its files say. Every row's PSA 10 listings
    were asked (a blank check time is a failed request); PSA 9's cannot be told apart from "no copies"."""
    rows = []
    if (d / "cards.csv").exists():
        with open(d / "cards.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    scope = scope_size(d / "scope.txt")
    return {"scope": scope, "ok": len(rows), "failed": max(scope - len(rows), 0), "skipped": 0, "carried": 0, "passes": 0,
            "listings_asked": len(rows), "listings_failed": sum(1 for r in rows if not r.get("listings_checked_at")),
            "psa9_listings_asked": 0, "psa9_listings_failed": 0,
            "all_grades_cards": sum(1 for r in rows if r.get("pops_by_grade")), "all_grades_requests": 0, "unfinished": True}


def merge_shards(out):
    """OUT/shard-*/run_stats.json "scrape" sections added up into OUT/run_stats.json's "scrape" (the
    per-shard accounts stay under "shards"). Returns the merged section, or None without shard folders."""
    out = Path(out)
    dirs = sorted((d for d in out.glob("shard-*") if d.is_dir()), key=lambda d: (len(d.name), d.name))
    if not dirs:
        print(f"run_stats merge: no shard folders under {out}, the run's own account stands")
        return None
    shards, total = {}, {k: 0 for k in SUM_KEYS}
    for d in dirs:
        s = load(d).get("scrape")
        if s is None:
            s = account_from_files(d)
            print(f"run_stats merge: {d.name} left no account (its scraper never finished): {s['ok']} of {s['scope']} cards in its files")
        shards[d.name] = s
        for k in SUM_KEYS:
            total[k] += int(s.get(k) or 0)
    first = next((s for s in shards.values() if not s.get("unfinished")), next(iter(shards.values())))
    merged = dict(total)
    merged.update({k: first.get(k) for k in ("company", "grade", "also_grades", "listings_only") if k in first})
    merged["passes"] = max(int(s.get("passes") or 0) for s in shards.values())
    starts = [s["started_at"] for s in shards.values() if s.get("started_at")]
    ends = [s["finished_at"] for s in shards.values() if s.get("finished_at")]
    if starts:
        merged["started_at"] = min(starts)
    if ends:
        merged["finished_at"] = max(ends)
    merged["shards"] = shards
    stats = load(out)
    stats["scrape"] = merged
    save(out, stats)
    unfinished = sum(1 for s in shards.values() if s.get("unfinished"))
    print(f"run_stats merge: {len(shards)} shard(s) -> scraped {merged['ok']}/{merged['scope']}, {merged['failed']} failed, "
          f"listings checks failed {merged['listings_failed']}/{merged['listings_asked']}, PSA 9 "
          f"{merged['psa9_listings_failed']}/{merged['psa9_listings_asked']}" + (f"; {unfinished} shard(s) unfinished" if unfinished else ""))
    return merged


def judge(stats, min_scraped_pct, max_listings_failed_pct):
    """(ok, reasons, numbers) for the scrape section: enough of the scope back, few enough listings checks
    failed. The listings rule runs per grade: PSA 10's checks (every card) and PSA 9's (--also-listings,
    cards with PSA 9 copies) are separate requests and either can fail on its own."""
    s = stats.get("scrape")
    if not s:
        return False, ["no scrape section in run_stats.json (the scraper never finished a pass)"], {}
    scope, ok = int(s.get("scope") or 0), int(s.get("ok") or 0)
    pct = round(100.0 * ok / scope, 1) if scope else 0.0
    numbers, reasons = {"scraped_pct": pct}, []
    if not scope:
        reasons.append("the scope is empty")
    elif pct < min_scraped_pct:
        reasons.append(f"scraped ok {ok}/{scope} = {pct}% of the scope, under {min_scraped_pct:g}%")
    for label, key in (("listings", "listings"), ("PSA 9 listings", "psa9_listings")):
        asked, failed = int(s.get(f"{key}_asked") or 0), int(s.get(f"{key}_failed") or 0)
        fpct = round(100.0 * failed / asked, 1) if asked else 0.0
        numbers[f"{key}_failed_pct"] = fpct
        if asked and fpct > max_listings_failed_pct:
            reasons.append(f"{label} checks failed {failed}/{asked} = {fpct}% of those asked, over {max_listings_failed_pct:g}%")
    return not reasons, reasons, numbers


def gate(out, min_scraped_pct=None, max_listings_failed_pct=None):
    """Judge the scrape and record the verdict in run_stats.json; False = do not commit this run."""
    min_pct, max_pct = knob("NIGHTLY_MIN_SCRAPED_PCT", min_scraped_pct), knob("NIGHTLY_MAX_LISTINGS_FAILED_PCT", max_listings_failed_pct)
    stats = load(out)
    ok, reasons, numbers = judge(stats, min_pct, max_pct)
    stats["gate"] = {"ok": ok, "reasons": reasons, "min_scraped_pct": min_pct, "max_listings_failed_pct": max_pct,
                     **numbers, "judged_at": utc_now()}
    save(out, stats)
    s = stats.get("scrape") or {}
    print(f"run_stats gate: {'PASS' if ok else 'FAIL'} - scraped {s.get('ok', 0)}/{s.get('scope', 0)} ({numbers.get('scraped_pct', 0)}%), "
          f"listings checks failed {s.get('listings_failed', 0)}/{s.get('listings_asked', 0)}, "
          f"PSA 9 {s.get('psa9_listings_failed', 0)}/{s.get('psa9_listings_asked', 0)}; "
          f"limits {min_pct:g}% scraped, {max_pct:g}% listings failed"
          + ("" if ok else "\n  " + "\n  ".join(reasons) + "\n  this run is NOT committed; the snapshot is the artifact"))
    return ok


def last_fanatics_pull(store):
    """The day (YYYY-MM-DD) of the newest direct Fanatics row's check in history/live_listings.csv, or ""
    when there is none (never pulled, or every direct row aged out)."""
    path = Path(store) / "live_listings.csv"
    newest = ""
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("via") == "fanatics" and (r.get("checked_at") or "") > newest:
                    newest = r["checked_at"]
    return newest[:10]


def finish(out, store=ROOT / "history"):
    """finished_at, and the Fanatics pull's health when the run had one: last_ok (day of the last successful
    direct pull) and failed_nights (0 when tonight's pull succeeded; None when none ever did)."""
    stats = load(out)
    stats["finished_at"] = utc_now()
    fan = stats.get("fanatics")
    if fan is not None:
        day = stats.get("day") or stats["finished_at"][:10]
        if fan.get("ok"):
            fan.update({"last_ok": day, "failed_nights": 0})
        else:
            last_ok = last_fanatics_pull(store)
            fan["last_ok"] = last_ok
            fan["failed_nights"] = max(0, (date.fromisoformat(day) - date.fromisoformat(last_ok)).days) if last_ok else None
    save(out, stats)
    print(f"run_stats: finished {stats['finished_at']}" + (f"; fanatics pull {'ok' if fan.get('ok') else 'FAILED'}, "
                                                           f"last success {fan.get('last_ok') or 'never'}" if fan is not None else ""))
    return stats


def fanatics_health(out, max_failed_nights=None):
    """False when the direct Fanatics pull failed tonight and has had no success for max_failed_nights
    nights (or ever); the post-commit workflow step turns that into a red run."""
    limit = knob("NIGHTLY_FANATICS_MAX_FAILED_NIGHTS", max_failed_nights)
    fan = load(out).get("fanatics")
    if fan is None:
        print("fanatics health: no direct pull in this run (NIGHTLY_FANATICS_BUDGET unset)")
        return True
    used = f"{fan.get('requests', '?')}/{fan.get('budget', '?')} requests"
    if fan.get("ok"):
        print(f"fanatics health: ok, {fan.get('listings', '?')} listings in {used}")
        return True
    nights, why = fan.get("failed_nights"), fan.get("error") or "no error recorded"
    if nights is None:
        print(f"fanatics health: FAIL - tonight's pull failed ({why}; {used}) and no successful direct pull is on record; "
              f"the feed shows no direct Fanatics Buy Nows")
        return False
    if nights >= limit:
        print(f"fanatics health: FAIL - tonight's pull failed ({why}; {used}); no success since {fan.get('last_ok')}, "
              f"{nights} night(s), limit {limit}; the direct rows age out of live_listings.csv after 7 days")
        return False
    print(f"fanatics health: tonight's pull failed ({why}; {used}); last success {fan.get('last_ok')}, {nights} night(s) ago, "
          f"under the limit of {limit}")
    return True


def commit_message(summary_path=ROOT / "latest" / "summary.json", prefix="nightly"):
    """The commit title: "<prefix> <data_date>: N assets, M sales" (nightly/already_ran.sh matches on
    "<prefix> <date>") plus the run's own counts after a semicolon when summary.json carries last_run."""
    with open(summary_path, encoding="utf-8") as f:
        s = json.load(f)
    msg = f"{prefix} {s['data_date']}: {s['assets']} assets, {s['psa10_sales']} sales"
    lr, parts = s.get("last_run") or {}, []
    sc = lr.get("scrape")
    if sc:
        part = f"scrape {sc.get('ok', 0)}/{sc.get('scope', 0)} ok"
        if sc.get("failed"):
            part += f", {sc['failed']} failed"
        if sc.get("carried"):
            part += f", {sc['carried']} carried"
        parts.append(part)
        for label, key in (("listings", "listings"), ("PSA 9 listings", "psa9_listings")):
            if sc.get(f"{key}_asked"):
                parts.append(f"{label} {sc.get(f'{key}_failed', 0)}/{sc[f'{key}_asked']} failed")
    alt = lr.get("alt")
    if alt:
        parts.append(f"Alt {alt.get('listings', 0)} listings" if alt.get("ok") else "Alt pull FAILED")
    fan = lr.get("fanatics")
    if fan:
        used = f"{fan.get('requests', '?')}/{fan.get('budget', '?')} requests"
        parts.append(f"Fanatics {fan.get('listings', 0)} listings in {used}" if fan.get("ok") else f"Fanatics pull FAILED ({used})")
    gate_ = lr.get("gate")
    if gate_ and not gate_.get("ok"):
        parts.append("gate FAILED")   # cannot happen from run_nightly.sh (it stops); a hand re-ingest of such a run would say so
    return msg + ("; " + ", ".join(parts) if parts else "")


def last_run_dir():
    """OUT from snapshots/last_run.env (written by run_nightly.sh)."""
    env = ROOT / "snapshots" / "last_run.env"
    for line in env.read_text().splitlines() if env.exists() else []:
        if line.startswith("OUT="):
            return ROOT / line[4:].strip()
    sys.exit("run_stats: no snapshots/last_run.env; give the run folder")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("out"); s.add_argument("--day", required=True); s.add_argument("--scope", default="top60")
    m = sub.add_parser("merge"); m.add_argument("out")
    g = sub.add_parser("gate"); g.add_argument("out")
    g.add_argument("--min-scraped-pct", type=float, help="default $NIGHTLY_MIN_SCRAPED_PCT or 90")
    g.add_argument("--max-listings-failed-pct", type=float, help="default $NIGHTLY_MAX_LISTINGS_FAILED_PCT or 20")
    f = sub.add_parser("finish"); f.add_argument("out"); f.add_argument("--store", default=str(ROOT / "history"))
    h = sub.add_parser("fanatics-health"); h.add_argument("out", nargs="?")
    h.add_argument("--max-failed-nights", type=int, help="default $NIGHTLY_FANATICS_MAX_FAILED_NIGHTS or 3")
    c = sub.add_parser("commit-message"); c.add_argument("--prefix", default="nightly")
    c.add_argument("--summary", default=str(ROOT / "latest" / "summary.json"))
    args = ap.parse_args()
    if args.cmd == "start":
        start(args.out, args.day, args.scope)
    elif args.cmd == "merge":
        merge_shards(args.out)
    elif args.cmd == "gate":
        sys.exit(0 if gate(args.out, args.min_scraped_pct, args.max_listings_failed_pct) else 1)
    elif args.cmd == "finish":
        finish(args.out, Path(args.store))
    elif args.cmd == "fanatics-health":
        sys.exit(0 if fanatics_health(args.out or last_run_dir(), args.max_failed_nights) else 1)
    else:
        print(commit_message(args.summary, args.prefix))


if __name__ == "__main__":
    main()
