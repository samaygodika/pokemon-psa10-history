#!/usr/bin/env python3
"""Weekly per-character breadth index and event study.

    analysis/.venv/bin/python analysis/char_weekly.py          # build analysis/out/char_weekly.csv (~3 min)
    analysis/.venv/bin/python analysis/char_weekly.py --events # print breadth / forward return around known events

Per character and week (Mondays from 2024-01): n liquid cards (>=3 clean
sales in 180d, >=1 in 60d), breadth (share with 30-day ref-price move >20%),
median 30-day move, and the median tradable forward return (buy first sale
within 14d, sell median of sales 60-90d out). Same definitions as panel.py,
just weekly and per character, so an announcement can be lined up against
the week breadth first crossed 30%.

The event list is hand-checked (dates verified against press releases and
PokeBeach/Bulbapedia in Sept 2026); extend it as the Rule 12 calendar grows.
"""
import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analysis"))
from panel import load_clean_sales, ref_price  # noqa: E402

OUT = ROOT / "analysis" / "out"
CHARS = ["Pikachu", "Charizard", "Rayquaza", "Umbreon", "Mewtwo", "Mew", "Lucario", "Gardevoir", "Eevee", "Espeon", "Sylveon",
         "Vaporeon", "Jolteon", "Flareon", "Glaceon", "Leafeon", "Dragonite", "Venusaur", "Blastoise", "Gengar", "Lugia", "Snorlax",
         "Greninja", "Salamence", "Zygarde", "Absol", "Kangaskhan", "Darkrai", "Raikou", "Suicune", "Entei", "Gyarados", "Latias", "Latios"]
EVENTS = [  # (character, date, what) — the date is the first PUBLIC information, not the product release
    ("Eevee", "2024-11-01", "Prismatic Evolutions announced (Eeveelutions as Tera ex); released 2025-01-17"),
    ("Umbreon", "2024-11-01", "Prismatic Evolutions announced; released 2025-01-17"),
    ("Lucario", "2025-02-27", "Pokemon Day 2025: Mega Lucario ex revealed, Mega Evolution series; set released 2025-09-26"),
    ("Gardevoir", "2025-02-27", "Pokemon Day 2025: Mega Gardevoir ex revealed"),
    ("Rayquaza", "2025-09-29", "Storm Emeralda / Emerald Surge leak (Mega Rayquaza rumour)"),
    ("Charizard", "2025-12-01", "1st Ed Base PSA 10 $550k at Heritage (Dec 2025); Phantasmal Flames Mega Charizard X (Nov 2025)"),
    ("Pikachu", "2026-01-05", "Goldin opens Logan Paul Illustrator auction (announced 01-02); sold $16.5M on 02-16"),
    ("Dragonite", "2026-01-30", "Ascended Heroes released (Mega Dragonite ex)"),
    ("Pikachu", "2026-02-16", "Illustrator sells for $16.5M"),
    ("Mewtwo", "2026-06-01", "30th Celebration full reveal: Mewtwo ex / Mew ex (set announced 02-27, released 09-16)"),
    ("Mew", "2026-06-01", "30th Celebration full reveal"),
    ("Umbreon", "2026-06-01", "30th Celebration full reveal: Umbreon ex"),
    ("Greninja", "2026-06-01", "30th Celebration full reveal: Greninja ex"),
    ("Salamence", "2026-06-01", "30th Celebration full reveal: Salamence ex"),
    ("Rayquaza", "2026-06-18", "Storm Emeralda official (Mega Rayquaza ex); JP 07-31, EN Delta Reign 11-06"),
    ("Darkrai", "2026-08-30", "ex-star series reveal"),
    ("Raikou", "2026-08-30", "ex-star series reveal"),
    ("Suicune", "2026-08-30", "ex-star series reveal"),
    ("Entei", "2026-08-30", "ex-star series reveal"),
]


def build():
    sales = load_clean_sales()
    a = pd.read_csv(ROOT / "history" / "assets.csv", usecols=["asset_id", "subject", "year"], dtype=str)
    sales = sales.merge(a, on="asset_id", how="left")
    weeks = pd.date_range("2024-01-01", sales.date.max(), freq="W-MON")
    T = weeks.values.astype("datetime64[D]")
    day = np.timedelta64(1, "D")
    rows = []
    for ch in CHARS:
        pat = re.compile(r"\b" + re.escape(ch) + r"\b")
        sub = sales[sales.subject.fillna("").map(lambda s: bool(pat.search(s)))]
        for aid, g in sub.groupby("asset_id"):
            d = g.date.values.astype("datetime64[D]")
            p = g.price.values.astype(float)
            upto = np.searchsorted(d, T, side="right")
            for k, t in enumerate(T):
                hi = upto[k]
                lo180 = np.searchsorted(d, t - 180 * day, side="right")
                lo60 = np.searchsorted(d, t - 60 * day, side="right")
                if hi - lo180 < 3 or hi - lo60 < 1:
                    continue
                ref = ref_price(p, lo180, hi)
                hi30 = np.searchsorted(d, t - 30 * day, side="right")
                r30 = ref_price(p, np.searchsorted(d, t - 210 * day, side="right"), hi30)
                hi_e = np.searchsorted(d, t + 14 * day, side="right")
                entry = p[hi] if hi_e > hi else np.nan
                lo_x = np.searchsorted(d, t + 60 * day, side="right")
                hi_x = np.searchsorted(d, t + 90 * day, side="right")
                fwd = float(np.median(p[lo_x:hi_x])) / entry - 1 if (not np.isnan(entry) and hi_x - lo_x >= 2) else np.nan
                rows.append((ch, pd.Timestamp(t), aid, ref, ref / r30 - 1 if r30 and not np.isnan(r30) else np.nan, hi - hi30, fwd))
    w = pd.DataFrame(rows, columns=["char", "week", "asset_id", "ref", "mom30", "vol30", "fwd_exec"])
    agg = w.groupby(["char", "week"]).agg(n=("mom30", "size"), breadth=("mom30", lambda s: (s > 0.2).mean()), med_mom30=("mom30", "median"),
                                          fwd_med=("fwd_exec", "median"), n_fwd=("fwd_exec", "count")).reset_index()
    OUT.mkdir(exist_ok=True)
    agg.to_csv(OUT / "char_weekly.csv", index=False)
    print(f"wrote {OUT / 'char_weekly.csv'}: {len(agg)} character-weeks")


def events():
    w = pd.read_csv(OUT / "char_weekly.csv", parse_dates=["week"])
    pd.set_option("display.width", 220)
    for ch, dt, desc in EVENTS:
        dt = pd.Timestamp(dt)
        g = w[w.char == ch].set_index("week").sort_index()
        win = g[(g.index >= dt - pd.Timedelta(weeks=8)) & (g.index <= dt + pd.Timedelta(weeks=16))]
        if win.empty:
            print(f"\n### {ch} {dt.date()} — no data")
            continue
        cross = win[win.breadth >= 0.30]
        first = cross.index[0] if len(cross) else None
        print(f"\n### {ch} — {desc}\n  event {dt.date()}; breadth first >=30% at "
              f"{first.date() if first is not None else 'never in window'}{f' ({(first - dt).days:+d} days vs event)' if first is not None else ''}")
        sel = win.iloc[::2][["n", "breadth", "med_mom30", "fwd_med", "n_fwd"]].copy()
        sel.index = [f"{d.date()} ({(d - dt).days:+d}d)" for d in sel.index]
        print(sel.round(3).to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", action="store_true")
    if ap.parse_args().events:
        events()
    else:
        build()
