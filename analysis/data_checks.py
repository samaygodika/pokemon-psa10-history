#!/usr/bin/env python3
"""Data-quality checks the research depends on.

    analysis/.venv/bin/python analysis/data_checks.py

1. Seed truncation: assets whose raw sale count is exactly 200 were capped by
   the seed scrape (--max-sales 200) and never backfilled; they are all
   outside the nightly top-60 scope until the weekly full run (which uses
   --max-sales 0) lands.
2. Sale-visibility lag: for assets far below the cap in both runs, sales in
   the 2026-09-12/13 full snapshot that were absent from the 2026-09-11 seed
   commit, by sale date. The share of day-D sales still missing on 09-11 is
   the lag. (Assets AT the cap must be excluded: comparing a capped run with
   an uncapped one shows the cap, not a lag.)
3. Regime series: monthly universe breadth, doubling rate and forward
   universe median, raw and year-demeaned.
"""
import glob
import io
import re
import subprocess
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SEED_COMMIT = "c5c3f75"
SNAPSHOT = ROOT / "snapshots" / "2026-09-12"


def truncation():
    raw = pd.concat([pd.read_csv(p, usecols=["asset_id"]) for p in glob.glob(str(ROOT / "history/sales/*.csv"))]).asset_id.value_counts()
    top60 = [l.strip() for l in open(ROOT / "nightly/subjects.txt") if l.strip() and not l.startswith("#")]
    subj = pd.read_csv(ROOT / "history/assets.csv", usecols=["asset_id", "subject"]).set_index("asset_id").subject.fillna("")
    pat = re.compile(r"\b(" + "|".join(map(re.escape, top60)) + r")\b")
    capped = raw[raw == 200].index
    in_top = subj.reindex(capped).fillna("").map(lambda s: bool(pat.search(s)))
    print(f"assets with exactly 200 raw sales (seed cap): {len(capped)}; of which in top-60 scope: {int(in_top.sum())}; assets >200: {(raw > 200).sum()} (all top-60, backfilled by the nightly)")


def lag():
    if not SNAPSHOT.exists():
        print("no snapshots/2026-09-12 on this machine; skipping lag check")
        return
    months = ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
    seed = pd.concat([pd.read_csv(io.StringIO(subprocess.run(["git", "show", f"{SEED_COMMIT}:history/sales/{m}.csv"], capture_output=True, text=True, cwd=ROOT).stdout), dtype=str) for m in months])
    snap = pd.read_csv(SNAPSHOT / "sales.csv", dtype=str)
    seed, snap = (df[(df.grading_company == "PSA") & (df.grade == "10.0")] for df in (seed, snap))
    n_seed, n_snap = seed.groupby("asset_id").size(), snap.groupby("asset_id").size()
    ok = set(n_seed[n_seed <= 150].index) & set(n_snap[n_snap <= 150].index)
    key = lambda df: df.url.fillna("") + "|" + df.asset_id + "|" + df.date + "|" + df.price
    snap = snap[snap.asset_id.isin(ok) & (snap.date >= "2026-08-15")]
    new = snap[~key(snap).isin(set(key(seed[seed.asset_id.isin(ok)])))]
    tot, late = snap.groupby("date").size(), new.groupby("date").size()
    t = pd.DataFrame({"sales_on_0912": tot, "absent_on_0911": late.reindex(tot.index, fill_value=0)})
    t["share_absent"] = (t.absent_on_0911 / t.sales_on_0912).round(3)
    print(f"\nsale-visibility lag ({len(ok)} assets far below the 200 cap in both runs):")
    print(t.loc[:"2026-09-11"])


def regime():
    p = pd.read_csv(ROOT / "analysis/out/panel.csv", parse_dates=["date"], usecols=["date", "mom30", "fwd_exec"])
    m = p.groupby("date").agg(breadth=("mom30", lambda s: (s > 0.2).mean()), fwd_med=("fwd_exec", "median"),
                              dbl=("fwd_exec", lambda s: (s.dropna() >= 1).mean()), n_tgt=("fwd_exec", "count"))
    m = m[(m.index >= "2021-03-01") & (m.n_tgt >= 200)]
    yr = m.index.year
    dev = lambda s: s - s.groupby(yr).transform("mean")
    print(f"\nregime series, {len(m)} months: dbl autocorr(1) {m.dbl.autocorr(1):.2f}; corr breadth->dbl {m.breadth.corr(m.dbl):.2f}, breadth->fwd_med {m.breadth.corr(m.fwd_med):.2f}")
    print(f"same, year-demeaned targets: breadth->dbl {m.breadth.corr(dev(m.dbl)):.2f}, breadth->fwd_med {m.breadth.corr(dev(m.fwd_med)):.2f}")
    print(m.tail(14).round(3))


if __name__ == "__main__":
    truncation()
    lag()
    regime()
