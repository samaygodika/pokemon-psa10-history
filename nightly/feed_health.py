#!/usr/bin/env python3
"""Daily feed health check (2026-11-17): is the published feed fresh, intact and whole?

The nightly and the other jobs already go red when they crash. This catches what they can't see:
  - the nightly didn't run at all, or ran green without publishing (guard skips, a disabled
    schedule), so latest/ quietly goes stale;
  - a published file that doesn't match summary.json's sha256 / bytes / rows (Sid's server
    refuses such a file and keeps the old one, also quietly);
  - a nightly that published much less than the night before (cards or sales counts fell).

    python3 nightly/feed_health.py --root latest [--prev OLD_SUMMARY.json ...] [--report out.md]

--prev takes earlier summary.json copies (the workflow passes the last few nightly commits'); the
newest one whose data_date is before the current one is the baseline. Exit 1 when there's a
PROBLEM (the workflow goes red and opens an issue), 0 otherwise; WARNINGs are printed and go in
the report but don't fail. Thresholds were set from the 10-21..11-17 nightlies: assets never fell,
sales counts fell at most 0.08% (REMOVED marks), live auction counts swing 3.4k-6.7k with timing.
"""
import argparse
import csv
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Counts in summary.json that must not fall by more than DROP_FAIL_PCT night over night.
FAIL_IF_DROP = ["assets", "psa10_sales", "psa9_sales", "series_rows", "recent_sales_rows",
                "psa9_recent_sales_rows"]
DROP_FAIL_PCT = 1.0
# Counts that move with listings; only a big fall is worth a look.
WARN_IF_DROP = {"rows_with_bin_listing": 25.0, "rows_with_psa9_bin_listing": 25.0,
                "rows_listings_checked": 5.0}
SHARD_KEYS = ("shards", "months")   # manifest.json: series/recent_sales* use shards, clean_sales months


def parse_ts(s):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def csv_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return max(sum(1 for _ in csv.reader(f)) - 1, 0)


def check_file(path, expect, label):
    """Problems for one file against its {sha256, bytes, rows} entry."""
    if not path.is_file():
        return [f"{label}: listed in the feed but missing"]
    out = []
    size = path.stat().st_size
    if "bytes" in expect and size != expect["bytes"]:
        out.append(f"{label}: {size} bytes, the feed says {expect['bytes']}")
    if expect.get("sha256") and sha256_of(path) != expect["sha256"]:
        out.append(f"{label}: sha256 doesn't match the feed (Sid's server would refuse it)")
    if "rows" in expect and path.suffix == ".csv":
        n = csv_rows(path)
        if n != expect["rows"]:
            out.append(f"{label}: {n} rows, the feed says {expect['rows']}")
    return out


def check_integrity(root, summary):
    """Every file summary.json lists, and every shard each listed manifest lists."""
    problems = []
    files = summary.get("files")
    if not files:
        return ["summary.json has no files block"], 0
    checked = 0
    for name, expect in files.items():
        path = root / name
        problems += check_file(path, expect, name)
        checked += 1
        if name.endswith("manifest.json") and path.is_file():
            manifest = json.loads(path.read_text())
            entries = next((manifest[k] for k in SHARD_KEYS if k in manifest), None)
            if entries is None:
                problems.append(f"{name}: no shards/months list")
                continue
            if "entries" in expect and len(entries) != expect["entries"]:
                problems.append(f"{name}: {len(entries)} entries, the feed says {expect['entries']}")
            for key, e in entries.items():
                problems += check_file(path.parent / f"{key}.csv", e, f"{path.parent.name}/{key}.csv")
                checked += 1
    return problems, checked


def check_freshness(summary, now, max_nightly_age_h, max_feed_age_h):
    problems, warnings = [], []
    last = summary.get("last_run") or {}
    finished = parse_ts(last.get("finished_at"))
    if finished is None:
        problems.append("summary.json has no last_run.finished_at: can't tell when the nightly last published")
    else:
        age = (now - finished).total_seconds() / 3600
        if age > max_nightly_age_h:
            problems.append(f"the last nightly finished {age:.0f} h ago ({last.get('finished_at')}, data_date "
                            f"{summary.get('data_date')}); limit {max_nightly_age_h:g} h. Tonight's nightly "
                            "didn't publish: check the nightly scrape workflow's runs")
    gen = parse_ts(summary.get("generated_at"))
    if gen is not None:
        age = (now - gen).total_seconds() / 3600
        if age > max_feed_age_h:
            warnings.append(f"nothing has updated the feed for {age:.0f} h (generated_at {summary.get('generated_at')}); "
                            "the listings refresh runs 3x a day, so check its runs")
    return problems, warnings


def check_last_run(summary):
    warnings = []
    last = summary.get("last_run") or {}
    gate = last.get("gate") or {}
    if gate and not gate.get("ok", True):
        warnings.append(f"the last nightly's quality gate said no: {'; '.join(gate.get('reasons') or [])}")
    scrape = last.get("scrape") or {}
    if scrape.get("scope"):
        failed = scrape.get("failed", 0) / scrape["scope"] * 100
        if failed > 5:
            warnings.append(f"{failed:.1f}% of the last nightly's scrape failed ({scrape.get('failed')} of {scrape['scope']})")
    fan = last.get("fanatics") or {}
    if fan.get("failed_nights", 0) >= 2:
        warnings.append(f"the Fanatics pull has failed {fan['failed_nights']} nights running (last ok {fan.get('last_ok')}); "
                        "the feed is showing old Fanatics listings")
    return warnings


def pick_baseline(summary, prevs):
    """The newest earlier summary with an older data_date, or None."""
    cur = summary.get("data_date") or ""
    older = [p for p in prevs if (p.get("data_date") or "") < cur]
    return max(older, key=lambda p: p.get("data_date") or "", default=None)


def check_vs_prev(summary, prev):
    problems, warnings = [], []
    if prev is None:
        return problems, ["no earlier nightly summary to compare counts with"]

    def drop(key):
        old, new = prev.get(key), summary.get(key)
        if not isinstance(old, (int, float)) or not isinstance(new, (int, float)) or old <= 0:
            return None, old, new
        return (old - new) / old * 100, old, new

    vs = f"vs {prev.get('data_date')}"
    for key in FAIL_IF_DROP:
        pct, old, new = drop(key)
        if pct is not None and pct > DROP_FAIL_PCT:
            problems.append(f"{key} fell {pct:.1f}% {vs} ({old} -> {new}); limit {DROP_FAIL_PCT:g}%")
    for key, limit in WARN_IF_DROP.items():
        pct, old, new = drop(key)
        if pct is not None and pct > limit:
            warnings.append(f"{key} fell {pct:.1f}% {vs} ({old} -> {new})")
    cards = (summary.get("files") or {}).get("cards.csv", {}).get("rows")
    if cards is not None and summary.get("assets") is not None and cards != summary["assets"]:
        problems.append(f"cards.csv has {cards} rows but summary.json says {summary['assets']} assets")
    return problems, warnings


def check_side_files(root, summary):
    """../history/grade_pops.csv: Sid reads it next to latest/, and no hash covers it."""
    path = root.parent / "history" / "grade_pops.csv"
    if not path.is_file() or path.stat().st_size == 0:
        return ["history/grade_pops.csv is missing or empty (Sid's server reads it)"], []
    day = summary.get("data_date")
    with open(path, newline="", encoding="utf-8") as f:
        has_day = any(row and row[0] == day for row in csv.reader(f))
    return [], ([] if has_day else [f"history/grade_pops.csv has no rows for {day}"])


def check_raw(url, summary, timeout=30):
    """What raw.githubusercontent.com serves Sid right now. Only warnings: its cache lags a few
    minutes after a push, and a network blip here isn't a feed problem."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "pokemon-psa10-history feed-health"}),
                                    timeout=timeout) as r:
            remote = json.load(r)
    except Exception as e:  # noqa: BLE001
        return [f"couldn't read {url}: {e}"]
    if remote.get("generated_at") != summary.get("generated_at"):
        return [f"{url} serves generated_at {remote.get('generated_at')}, main has {summary.get('generated_at')} "
                "(normal for a few minutes after a push)"]
    return []


def run(root, prevs=(), now=None, max_nightly_age_h=30.0, max_feed_age_h=14.0, raw_url=None):
    """(problems, warnings, facts) for the feed at root (a latest/ dir)."""
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    summary_path = root / "summary.json"
    if not summary_path.is_file():
        return [f"{summary_path} is missing"], [], {}
    summary = json.loads(summary_path.read_text())
    problems, warnings = [], []
    p, w = check_freshness(summary, now, max_nightly_age_h, max_feed_age_h)
    problems += p
    warnings += w
    p, checked = check_integrity(root, summary)
    problems += p
    baseline = pick_baseline(summary, prevs)
    p, w = check_vs_prev(summary, baseline)
    problems += p
    warnings += w
    warnings += check_last_run(summary)
    p, w = check_side_files(root, summary)
    problems += p
    warnings += w
    if raw_url:
        warnings += check_raw(raw_url, summary)
    facts = {
        "data_date": summary.get("data_date"),
        "nightly finished": (summary.get("last_run") or {}).get("finished_at"),
        "generated_at": summary.get("generated_at"),
        "assets": summary.get("assets"),
        "files checked": checked,
        "compared with": baseline.get("data_date") if baseline else None,
    }
    return problems, warnings, facts


def report(problems, warnings, facts):
    head = "Feed health: PROBLEM" if problems else ("Feed health: ok, with warnings" if warnings else "Feed health: ok")
    lines = [f"## {head}", ""]
    lines += [f"- {k}: {v}" for k, v in facts.items()]
    if problems:
        lines += ["", "### Problems"] + [f"- {x}" for x in problems]
    if warnings:
        lines += ["", "### Warnings"] + [f"- {x}" for x in warnings]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default="latest", help="the latest/ directory")
    ap.add_argument("--prev", nargs="*", default=[], help="earlier summary.json copies (baseline = newest older one)")
    ap.add_argument("--max-nightly-age-h", type=float, default=30.0)
    ap.add_argument("--max-feed-age-h", type=float, default=14.0)
    ap.add_argument("--raw-url", default=None, help="also read summary.json from here (what Sid's server sees)")
    ap.add_argument("--now", default=None, help="ISO time to judge freshness at (tests)")
    ap.add_argument("--report", default=None, help="also write the markdown report here")
    a = ap.parse_args(argv)
    prevs = []
    for p in a.prev:
        try:
            prevs.append(json.loads(Path(p).read_text()))
        except (OSError, ValueError) as e:
            print(f"skipping --prev {p}: {e}", file=sys.stderr)
    problems, warnings, facts = run(a.root, prevs, parse_ts(a.now) if a.now else None,
                                    a.max_nightly_age_h, a.max_feed_age_h, a.raw_url)
    text = report(problems, warnings, facts)
    print(text, end="")
    if a.report:
        with open(a.report, "a", encoding="utf-8") as f:
            f.write(text)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
