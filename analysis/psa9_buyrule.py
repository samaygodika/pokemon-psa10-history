#!/usr/bin/env python3
"""Sid's actual PSA 9 buy rule, backtested (2026-09-27)

    analysis/.venv/bin/python analysis/psa9_buyrule.py --cache-dir <dir with clean_sales.pkl>   # ~3 min
    analysis/.venv/bin/python analysis/psa9_buyrule.py --top-cap-col alt_market_cap_usd          # psa9_hype's top-50 instead of the app's

Sid, replying to the hype study (his words): "I mostly buy PSA 9s of blue chip
cards or cards from the top-50 Pokemon, usually when the card's PSA 10 has
gained 100%+ over the past month and the PSA 9 hasn't moved yet. [...] when a
card type like LV.X, Gold Star, or Prime/LEGEND moves 70 to 100%+ as a
category in a month, its PSA 9s catch up to the 10s over the following months.
[...] My theory is really about the intersection: hot category plus top-50
Pokemon (or blue chip), not every card in the set."

Everything here reuses psa9_lag.py (cleaning, monthly buckets, universe, fee,
trade) and psa9_hype.py (per-card month-start rows, hype months, matched
control, one-observation-per-month t-stats). Nothing is re-derived.

Definitions (looked up, not guessed)
  Top-50 Pokemon by market cap. PokeSniper's Characters tab (its default
    "<= 2013" vintage view, PokeSniper.jsx rosterRanking / SEED_TOP50) takes
    its 50 members from the alt-side market cap of cards dated <= 2013:
    latest/characters.csv column `alt_market_cap_le2013_usd`. That is the
    default here (`--top-cap-col`). psa9_hype.py's TOP_CHARACTERS uses the
    all-years column `alt_market_cap_usd`; the two lists share 39 names and
    the all-years list is reported as a robustness row. Today's <= 2013 top
    50 matches the app's SEED_TOP50 except Eevee in / Arceus out (cap drift
    since the 2026-09-22 snapshot). Palkia is #51 on the <= 2013 list (the
    app's buffer, not shown) and #49 on the all-years list.
  Blue chip. PokeSniper.jsx BLUE_CHIP_TOP = 10: the first 10 of that top 50
    ranked by the app's OWN combined market cap = sum over the character's
    matched cards (year <= 2013, English) of PSA 10 price x PSA 10 pop
    (mapToCardShape.js totalMarketValue). Replicated as the top 10 of the
    top-50 list by sum(clean_last_sale_price x pop_at_grade) over English
    cards with year <= 2013 in latest/cards.csv. (The alt-side <= 2013 cap
    alone gives the same 10 except Torchic in place of Dragonite.) Blue chip
    is a subset of the top 50, so "blue chip OR top-50" = the top 50; blue
    chip is also shown on its own. Both lists are today's; membership is
    applied to the whole history (there is no historical roster), a mild
    look-ahead that favours the rule if today's big names were not always big.
  A card is a member when its alt.xyz subject names the character
    (whole-word match, psa9_hype.character_members).
  The card's own PSA 10 move = psa9_lag's monthly bucket return for the
    signal month M: log median PSA 10 price in M minus in M-1 (both months
    need a clean sale; clipped at +-log 3). "Up 100%+" = >= log 2; the 50%
    variant = >= log 1.5. "PSA 9 hasn't moved" = the PSA 9's own bucket
    return in M is < +10% (observed: both months have a PSA 9 sale). The
    anchor is T = first day of M+1, so every input is dated inside M and
    entry / outcomes are strictly after T (psa9_hype's point-in-time rule).
  Tier = median PSA 10 sale in (T-30, T]: < $1K, $1K-$10K, >= $10K.
  Control = psa9_hype's matched control: the median row NOT in any +15%
    hype window (card type, era or character) with the same anchor month,
    era and PSA 10 tier (`_mx`); and the stricter same-state control (`_sx`):
    unhyped cards in the same era, tier, month AND the same own-card state
    (PSA 10 up >= the threshold, PSA 9 < +10%), which cancels the bucket-
    noise reversal that follows any "PSA 10 bucket up, PSA 9 bucket flat" month.
  Trade = buy the first PSA 9 sale in (T, T+21], sell the first PSA 9 sale
    >= 90 / 180 / 365 days after entry (within a further 60), 13% sell fee.

Asks
  1. The rule as stated, at 100% and 50%: PSA 9 / PSA 10 / gap at 3, 6, 12
     months vs the matched controls, by tier, one observation per month; the
     trade by year.
  2. Inside card-type hype months (>= 15%, >= 30%): top-50 / blue-chip cards
     vs non-top-50 cards, same months, matched excess, paired by month.
  3. Card-type category months at +50 / +70 / +100% (group median PSA 10
     bucket return, >= 5 cards): the list and whatever 3 / 6 / 12-month
     outcomes exist.
  Plus: Sid's three example cards today.

Checks: analysis/test_psa9_buyrule.py. Output: analysis/out/psa9_buyrule.md.
"""
import argparse
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import psa9_lag as L  # noqa: E402
import psa9_hype as H  # noqa: E402

ROOT = L.ROOT
OUT = ROOT / "analysis" / "out"
DAY = L.DAY
TOP_N = 50
BLUE_CHIP_TOP = 10
DEFAULT_TOP_CAP_COL = "alt_market_cap_le2013_usd"      # the app's Characters tab (default <= 2013 view)
ALT_TOP_CAP_COL = "alt_market_cap_usd"                 # psa9_hype.py's TOP_CHARACTERS
VINTAGE_CUTOFF = 2013
UP_THRESHOLDS = (1.00, 0.50)         # the card's own PSA 10 bucket return in the signal month
FLAT = 0.10                          # PSA 9 bucket return below this = "hasn't moved yet"
CONTROL_HYPE = 0.15                  # the hype threshold whose windows are excluded from the matched control
CAT_HYPE = (0.15, 0.30)              # ask 2
CAT_COUNT = (0.50, 0.70, 1.00)       # ask 3
REPORT_H = (90, 180, 365)
HOLDS = H.HOLDS
HL = H.HLABEL
TIER_LABELS = [t[2] for t in H.TIERS]
EXAMPLES = {"f08c4b31-540f-45af-8d58-142812685d1b": "2008 Legends Awakened Mewtwo LV.X #144",
            "44f089b2-c866-4362-95e6-f549b4b76322": "2008 Great Encounters Darkrai LV.X #104",
            "0026cc01-6ac2-4e2e-b7b4-e025f3f5d079": "2009 Platinum Palkia G LV.X #125"}
FOREIGN = r"Japanese|Korean|Chinese|French|German|Spanish|Italian|Portuguese|Dutch|Indonesian|Russian|Thai|Polish"


# ---------------------------------------------------------------- definitions

def top_characters(cap_col=DEFAULT_TOP_CAP_COL, top=TOP_N, path=None):
    """The top `top` subjects in latest/characters.csv by `cap_col`, in rank order."""
    ch = pd.read_csv(path or ROOT / "latest" / "characters.csv")
    return ch.sort_values(cap_col, ascending=False).character.head(top).tolist()


def blue_chips(top50, cards=None, n=BLUE_CHIP_TOP, cutoff=VINTAGE_CUTOFF):
    """PokeSniper's Blue Chip list replicated: the first `n` of `top50` by the app's own combined
    market cap, sum(PSA 10 clean last price x PSA 10 pop) over English cards with year <= cutoff.
    Returns (names in rank order, {name: cap})."""
    c = cards if cards is not None else pd.read_csv(ROOT / "latest" / "cards.csv", dtype=str, low_memory=False)
    year = pd.to_numeric(c.year, errors="coerce")
    price = pd.to_numeric(c.clean_last_sale_price, errors="coerce")
    pop = pd.to_numeric(c.pop_at_grade, errors="coerce")
    text = c.card_name.fillna("") + " " + c["set"].fillna("") + " " + c.variety.fillna("")
    keep = (year <= cutoff) & price.notna() & pop.notna() & ~text.str.contains(FOREIGN, case=False, regex=True)
    cap = (price * pop)[keep]
    subj = c.subject.fillna("")[keep].map(H._norm)
    caps = {name: float(cap[subj.str.contains(re.escape(H._norm(name)), regex=True)].sum()) for name in top50}
    ranked = sorted(top50, key=lambda k: -caps[k])
    return ranked[:n], caps


def member_ids(assets, names):
    """Asset ids whose subject names any of `names` (whole word)."""
    a = assets.reset_index().rename(columns={"index": "asset_id"}).set_index("asset_id")
    return set().union(*H.character_members(a, names).values()) if names else set()


# ---------------------------------------------------------------- rows

def build_base(sales, assets, universe, cache_dir):
    """psa9_hype's per-(card, month-start) rows + own bucket returns + era, cached."""
    cache = cache_dir / "psa9_buyrule_base.pkl"
    if cache.exists():
        base = pd.read_pickle(cache)
        base.attrs["data_end"] = sales.date.max().to_datetime64().astype("datetime64[D]")
        return base
    monthly = L.monthly_series(sales)
    base = H.add_excess(H.build_rows(sales, universe))
    data_end = base.attrs["data_end"]
    ret = L.monthly_returns(monthly[monthly.asset_id.isin(universe)])[["asset_id", "month", "r10", "r9"]]
    ret = ret.rename(columns={"month": "signal_month", "r10": "own_r10", "r9": "own_r9"})
    base = base.merge(ret, on=["asset_id", "signal_month"], how="left")
    base.attrs["data_end"] = data_end
    pd.to_pickle(base, cache)
    return base


def rule_state(rows, up, flat=FLAT, flat_observed=True):
    """The card's own state in the signal month: PSA 10 bucket up >= `up`, PSA 9 bucket < +flat.
    flat_observed=False also admits rows with no PSA 9 return (no sale in one of the two months)."""
    up10 = (rows.own_r10 >= np.log1p(up)).values
    r9 = rows.own_r9.values
    flat9 = np.where(np.isfinite(r9), r9 < np.log1p(flat), not flat_observed)
    return up10 & flat9


def tag_hype_windows(rows, r10, seg, hype=CONTROL_HYPE):
    """psa9_hype's hype flags at one threshold: hype_card_type / hype_era / hype_character / hype_any."""
    hms = {dim: H.hype_months(r10, seg[dim], hype, H.MIN_GROUP_CARDS) for dim in ("card type", "era", "character")}
    for dim in hms:
        rows = H.tag_hype(rows, hms[dim], seg[dim], f"hype_{dim.replace(' ', '_')}")
    rows["hype_any"] = rows.hype_card_type | rows.hype_era | rows.hype_character
    return rows, hms


# ---------------------------------------------------------------- statistics / views

def years_of(rows, mask):
    """'2021: 3, 2024: 1' = distinct signal months per year under mask."""
    y = rows.loc[mask, "signal_month"].drop_duplicates().dt.year.value_counts().sort_index()
    return ", ".join(f"{k}: {v}" for k, v in y.items())


def episode(rows, masks, col_fmt, horizons=REPORT_H):
    """psa9_hype.episode_view plus the signal months per year, so a cell's [months] can be placed in time."""
    out = H.episode_view(rows, masks, col_fmt, horizons)
    out.insert(2, "signal months by year", [years_of(rows, m) for _, m in masks])
    return out


def paired_by_month(rows, mask_a, mask_b, col):
    """Per signal month: mean(col | a) - mean(col | b) where both exist; mean, median, % > 0, t, months."""
    a = rows.loc[mask_a, ["signal_month", col]].dropna().groupby("signal_month")[col].mean()
    b = rows.loc[mask_b, ["signal_month", col]].dropna().groupby("signal_month")[col].mean()
    d = (a - b).dropna()
    n = len(d)
    if n == 0:
        return ""
    t = d.mean() / (d.std(ddof=1) / np.sqrt(n)) if n >= 3 and d.std(ddof=1) > 0 else np.nan
    return f"{100 * d.mean():+.1f} / {100 * d.median():+.1f}, {100 * (d > 0).mean():.0f}% up" + (f" ({t:.1f})" if np.isfinite(t) else "") + f" [{n}]"


def paired_view(rows, pairs, col_fmt, horizons=REPORT_H):
    out = []
    for label, ma, mb in pairs:
        r = {"comparison": label, "rows A": int(ma.sum()), "rows B": int(mb.sum())}
        for h in horizons:
            r[HL[h]] = paired_by_month(rows, ma, mb, col_fmt.format(h=h))
        out.append(r)
    return pd.DataFrame(out)


def trade_table(rows, masks):
    tr = []
    for label, m in masks:
        years = pd.to_datetime(rows.date).dt.year
        for h in HOLDS:
            q = rows[m & rows[f"closed{h}"].values]
            got = q[f"net{h}"].notna()
            v = q.loc[got, f"net{h}"]
            perx = q.loc[got].groupby("signal_month")[f"net{h}_mx"].median().dropna()
            tx = perx.mean() / (perx.std(ddof=1) / np.sqrt(len(perx))) if len(perx) > 2 and perx.std(ddof=1) > 0 else np.nan
            ny = years[q.index].nunique() if len(q) else 1
            prem = (np.log(q.entry) - q.m9_move).dropna()          # the first buyable PSA 9 vs the signal month's PSA 9 median
            tr.append({"sample": label, "hold": h, "entered": len(q), "no exit %": 100 * (1 - got.mean()) if len(q) else np.nan,
                       "trades": int(got.sum()), "trades / yr": got.sum() / max(ny, 1), "months": q.loc[got, "signal_month"].nunique(),
                       "entry vs pre-T PSA 9 median, median %": 100 * np.expm1(prem.median()) if len(prem) else np.nan,
                       "hit %": 100 * (v > 0).mean() if len(v) else np.nan, "mean net %": 100 * v.mean() if len(v) else np.nan,
                       "median net %": 100 * v.median() if len(v) else np.nan,
                       "vs matched unhyped, pts": 100 * perx.mean() if len(perx) else np.nan, "t": tx,
                       "% months ahead": 100 * (perx > 0).mean() if len(perx) else np.nan})
    return pd.DataFrame(tr)


def trade_by_year(rows, mask, h):
    q = rows[mask & rows[f"closed{h}"].values].copy()
    q["year"] = pd.to_datetime(q.date).dt.year
    out = []
    for y, g in q.groupby("year"):
        v = g[f"net{h}"].dropna()
        out.append({"year": y, "signals": len(g), "trades": len(v), "hit %": 100 * (v > 0).mean() if len(v) else np.nan,
                    "mean net %": 100 * v.mean() if len(v) else np.nan, "median net %": 100 * v.median() if len(v) else np.nan,
                    "median vs matched unhyped, pts": 100 * g[f"net{h}_mx"].median() if g[f"net{h}_mx"].notna().any() else np.nan})
    return pd.DataFrame(out)


def counts_view(rows, mask, label):
    q = rows[mask]
    r = {"rule": label, "signals": len(q), "cards": q.asset_id.nunique(), "months": q.signal_month.nunique()}
    for lab in TIER_LABELS:
        r[lab] = int((q.tier == lab).sum())
    for y, g in q.groupby(pd.to_datetime(q.date).dt.year):
        r[str(y)] = len(g)
    return r


def group_forward(sales, members, month, ks=(3, 6, 12)):
    """Median over the group's cards (every card with PSA 10 sales) of log median PSA 10 in month+k
    minus in `month`; (value, n cards) per k. NaN when month+k is after the data."""
    s = sales[(sales.grade == "10.0") & sales.asset_id.isin(members)]
    m = np.log(s.price).groupby([s.asset_id, s.date.dt.to_period("M")]).median()
    last = s.date.dt.to_period("M").max()
    out = {}
    base = m.xs(month, level=1) if month in m.index.get_level_values(1) else pd.Series(dtype=float)
    for k in ks:
        tgt = month + k
        if tgt >= last:                 # the target month is incomplete or absent
            out[k] = (np.nan, 0)
            continue
        fwd = m.xs(tgt, level=1) if tgt in m.index.get_level_values(1) else pd.Series(dtype=float)
        d = (fwd - base).dropna()
        out[k] = (float(np.median(d)) if len(d) else np.nan, len(d))
    return out


# ---------------------------------------------------------------- the three cards today

def card_now(sales, aid, data_end, universe, lists):
    s10 = sales[(sales.asset_id == aid) & (sales.grade == "10.0")].sort_values("date")
    s9 = sales[(sales.asset_id == aid) & (sales.grade == "9.0")].sort_values("date")
    end = pd.Timestamp(data_end)

    def win(s, lo, hi):
        q = s[(s.date > end - pd.Timedelta(days=lo)) & (s.date <= end - pd.Timedelta(days=hi))]
        return (float(np.exp(np.log(q.price).median())) if len(q) else np.nan), len(q)

    def bucket(s, per):
        q = s[s.date.dt.to_period("M") == per]
        return float(np.exp(np.log(q.price).median())) if len(q) else np.nan

    this_m = end.to_period("M")
    r = {"card": EXAMPLES.get(aid, aid), "asset_id": aid, "in PSA 9 universe": aid in set(universe)}
    for name, ids in lists.items():
        r[name] = aid in ids
    p10_now, n10_now = win(s10, 30, 0)
    p10_prev, n10_prev = win(s10, 60, 30)
    p9_now, n9_now = win(s9, 30, 0)
    p9_prev, n9_prev = win(s9, 60, 30)
    r["PSA 10 median, last 30d (n)"] = f"${p10_now:,.0f} ({n10_now})" if n10_now else f"none ({n10_now})"
    r["PSA 10 median, prior 30d (n)"] = f"${p10_prev:,.0f} ({n10_prev})" if n10_prev else f"none ({n10_prev})"
    r["PSA 10 30d-vs-prior-30d"] = f"{100 * (p10_now / p10_prev - 1):+.0f}%" if n10_now and n10_prev else "undefined (no sale in one window)"
    b10, b10p = bucket(s10, this_m), bucket(s10, this_m - 1)
    b9, b9p = bucket(s9, this_m), bucket(s9, this_m - 1)
    r[f"PSA 10 bucket {this_m} vs {this_m - 1} (the rule's input)"] = f"{100 * (b10 / b10p - 1):+.0f}%" if np.isfinite(b10) and np.isfinite(b10p) else "undefined (no PSA 10 sale in one of the two months)"
    r["PSA 9 median, last 30d (n)"] = f"${p9_now:,.0f} ({n9_now})" if n9_now else "none"
    r["PSA 9 median, prior 30d (n)"] = f"${p9_prev:,.0f} ({n9_prev})" if n9_prev else "none"
    r["PSA 9 30d-vs-prior-30d"] = f"{100 * (p9_now / p9_prev - 1):+.0f}%" if n9_now and n9_prev else "undefined"
    r[f"PSA 9 bucket {this_m} vs {this_m - 1}"] = f"{100 * (b9 / b9p - 1):+.0f}%" if np.isfinite(b9) and np.isfinite(b9p) else "undefined"
    last10 = s10.iloc[-1] if len(s10) else None
    prev10 = s10.iloc[:-1].tail(3) if len(s10) > 1 else None
    r["last PSA 10 sale"] = f"${last10.price:,.0f} on {last10.date.date()}" if last10 is not None else "none"
    r["vs median of the 3 PSA 10 sales before it"] = (f"{100 * (last10.price / np.exp(np.log(prev10.price).median()) - 1):+.0f}% (those sales: "
                                                       + ", ".join(f"${p:,.0f} {d.date()}" for d, p in zip(prev10.date, prev10.price)) + ")") if prev10 is not None and len(prev10) else "n/a"
    r["tier (PSA 10 median last 30d)"] = str(pd.cut([p10_now], [t[0] for t in H.TIERS] + [np.inf], labels=TIER_LABELS, right=False)[0]) if n10_now else "no PSA 10 sale in 30d"
    r["last 5 PSA 9 sales"] = ", ".join(f"${p:,.0f} {d.date()}" for d, p in zip(s9.date.tail(5), s9.price.tail(5)))
    r["last 5 PSA 10 sales"] = ", ".join(f"${p:,.0f} {d.date()}" for d, p in zip(s10.date.tail(5), s10.price.tail(5)))
    member = any(aid in ids for ids in lists.values())
    up_ok = {thr: (np.isfinite(b10) and np.isfinite(b10p) and np.log(b10 / b10p) >= np.log1p(thr)) for thr in UP_THRESHOLDS}
    flat_ok = np.isfinite(b9) and np.isfinite(b9p) and np.log(b9 / b9p) < np.log1p(FLAT)
    for thr in UP_THRESHOLDS:
        why = []
        if not member:
            why.append("subject not in any top-50 / blue-chip list")
        if not up_ok[thr]:
            why.append(f"PSA 10 bucket move not >= +{thr:.0%} (or undefined)")
        if not flat_ok:
            why.append("PSA 9 bucket not flat (< +10%) or undefined")
        r[f"meets the rule today at +{thr:.0%}"] = "YES" if member and up_ok[thr] and flat_ok else "no: " + "; ".join(why)
    return r


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", type=Path, default=L.DEFAULT_CACHE)
    ap.add_argument("--rebuild", action="store_true", help="ignore the cleaned-sales and rows caches")
    ap.add_argument("--top-cap-col", default=DEFAULT_TOP_CAP_COL, help=f"characters.csv column for the top-50 (default {DEFAULT_TOP_CAP_COL}; psa9_hype uses {ALT_TOP_CAP_COL})")
    ap.add_argument("--min-months", type=int, default=L.MIN_MONTHS)
    args = ap.parse_args()
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    if args.rebuild and (args.cache_dir / "psa9_buyrule_base.pkl").exists():
        (args.cache_dir / "psa9_buyrule_base.pkl").unlink()
    sales = L.load_clean_sales(cache_dir=args.cache_dir, rebuild=args.rebuild)
    assets = L.load_assets()
    assets = assets.join(pd.read_csv(ROOT / "history" / "assets.csv", usecols=["asset_id", "card_number"], dtype=str).set_index("asset_id"))
    seg, chars_all = H.load_segments(assets)                  # psa9_hype's segments; chars_all = all-years top 50
    monthly = L.monthly_series(sales)
    universe = L.select_universe(monthly, args.min_months)
    data_end = sales.date.max()
    print(f"{len(sales):,} clean sales through {data_end.date()}; PSA 9 universe {len(universe):,} cards", flush=True)

    # --- definitions
    top_app = top_characters(args.top_cap_col)
    top_alt = top_characters(ALT_TOP_CAP_COL if args.top_cap_col != ALT_TOP_CAP_COL else DEFAULT_TOP_CAP_COL)
    blue, caps = blue_chips(top_app)
    ids_top = member_ids(assets, top_app)
    ids_alt = member_ids(assets, top_alt)
    ids_blue = member_ids(assets, blue)
    lists = {"top-50 (app list)": ids_top, "top-50 (other cap column)": ids_alt, "blue chip": ids_blue}
    uni = set(universe)
    defs = pd.DataFrame([
        {"list": f"top-50 by {args.top_cap_col}", "names": ", ".join(top_app), "cards (all)": len(ids_top), "cards in PSA 9 universe": len(ids_top & uni)},
        {"list": f"top-50 by {ALT_TOP_CAP_COL if args.top_cap_col != ALT_TOP_CAP_COL else DEFAULT_TOP_CAP_COL} (robustness)", "names": ", ".join(top_alt), "cards (all)": len(ids_alt), "cards in PSA 9 universe": len(ids_alt & uni)},
        {"list": f"blue chip (top {BLUE_CHIP_TOP} of the first list by own cap = sum PSA 10 price x pop, English, <= {VINTAGE_CUTOFF})",
         "names": ", ".join(f"{n} (${caps[n] / 1e6:,.0f}M)" for n in blue), "cards (all)": len(ids_blue), "cards in PSA 9 universe": len(ids_blue & uni)},
    ])
    only_a = [n for n in top_app if n not in top_alt]
    only_b = [n for n in top_alt if n not in top_app]
    lines = [f"# Sid's buy rule ({pd.Timestamp.today().date()})", "",
             f"Data through {data_end.date()}. PSA 9 universe: {len(universe):,} cards (>= {args.min_months} months with a sale in each grade since 2021). "
             f"Rule: the card's subject is a top-50 Pokemon (or blue chip, a subset), its own PSA 10 monthly bucket return in the signal month M is >= +100% "
             f"(also +50%), its PSA 9 bucket return in M is < +{FLAT:.0%} (observed). Anchor T = first day of M+1; outcomes strictly after T. "
             f"`_mx` = minus the median unhyped (no +{CONTROL_HYPE:.0%} card-type / era / character hype window) card of the same era and PSA 10 tier anchored the same month; "
             "`_sx` = minus the median unhyped NON-top-50 card in the same own-card state (PSA 10 up >= threshold, PSA 9 < +10%), era, tier and month, i.e. net of the bucket-noise reversal that follows any such month. "
             f"The most recent month ({data_end.to_period('M')}) is partial (data through {data_end.date()}). "
             "Episode cells: mean / median across signal months, % of months > 0, (t across months), [months]. Blank = fewer than 1 month with data.", "",
             "## 0. Definitions", "", L.md(defs), "",
             f"In the first list only: {', '.join(only_a) or 'none'}. In the second only: {', '.join(only_b) or 'none'}.", ""]
    print("\n".join(lines), flush=True)

    # --- rows
    base = build_base(sales, assets, universe, args.cache_dir)
    print(f"{len(base):,} (card, month-start) rows ({time.time() - t0:.0f}s)", flush=True)
    r10 = H.psa10_bucket_returns(sales)
    rows, hms15 = tag_hype_windows(base, r10, seg, CONTROL_HYPE)
    era_of = {aid: g for g, ids in seg["era"].items() for aid in ids}
    rows["era"] = rows.asset_id.map(era_of)
    rows["top"] = rows.asset_id.isin(ids_top).values
    rows["top_alt"] = rows.asset_id.isin(ids_alt).values
    rows["blue"] = rows.asset_id.isin(ids_blue).values
    rows["any_top"] = rows.top | rows.top_alt
    rows = H.add_matched_excess(rows)                          # _mx: vs unhyped rows, same month x era x tier
    # _sx: vs unhyped NON-top-50 rows in the same own-card state, month, era and tier. Top-50 rows are kept out
    # of this control because an unhyped rule row would otherwise be (often the only) member of its own control.
    rows["ctrl_excl"] = rows.hype_any | rows.any_top
    for thr in UP_THRESHOLDS:
        rows[f"state{int(100 * thr)}"] = rule_state(rows, thr)
        rows = H.add_matched_excess(rows, keys=("era", "tier", f"state{int(100 * thr)}"), hype_col="ctrl_excl", suffix=f"_sx{int(100 * thr)}")
    top_tier = (rows.tier == ">= $10K").values

    # ================================================================ ask 1
    sec = ["## 1. The rule as stated", ""]
    cnt = []
    rule_masks = {}
    for thr in UP_THRESHOLDS:
        st = rows[f"state{int(100 * thr)}"].values
        rule_masks[thr] = {"top-50 (app)": rows.top.values & st, "blue chip": rows.blue.values & st, "top-50 (other column)": rows.top_alt.values & st,
                           "either top-50 list": rows.any_top.values & st, "NOT top-50 (either list), same state": ~rows.any_top.values & st}
        for lab, m in rule_masks[thr].items():
            cnt.append(counts_view(rows, m, f"PSA 10 up >= {thr:.0%}, PSA 9 flat, {lab}"))
        loose = rows.top.values & rule_state(rows, thr, flat_observed=False)
        cnt.append(counts_view(rows, loose, f"PSA 10 up >= {thr:.0%}, PSA 9 flat OR no PSA 9 return, top-50 (app)"))
    cnt = pd.DataFrame(cnt).fillna(0)
    for c in cnt.columns[1:]:
        cnt[c] = cnt[c].astype(int)
    sec += ["**Signals** (rows = (card, month) pairs meeting the rule; by tier and by year of T)", "", L.md(cnt), ""]

    for thr in UP_THRESHOLDS:
        s = int(100 * thr)
        masks = [(f"rule: top-50 (app), up >= {thr:.0%}", rule_masks[thr]["top-50 (app)"]),
                 (f"rule: blue chip only", rule_masks[thr]["blue chip"]),
                 (f"rule: top-50 (other column)", rule_masks[thr]["top-50 (other column)"]),
                 (f"same state, NOT top-50", rule_masks[thr]["NOT top-50 (either list), same state"])]
        masks += [(f"rule: top-50 (app), PSA 10 {lab}", rule_masks[thr]["top-50 (app)"] & (rows.tier == lab).values) for lab in TIER_LABELS]
        masks += [(f"same state, NOT top-50, PSA 10 {lab}", rule_masks[thr]["NOT top-50 (either list), same state"] & (rows.tier == lab).values) for lab in TIER_LABELS]
        sx_masks = [m for m in masks if "NOT top-50" not in m[0]]          # non-top-50 same-state rows ARE the _sx control
        sec += [f"### 1{'a' if thr == 1.0 else 'b'}. PSA 10 up >= {thr:.0%} in the signal month, PSA 9 < +{FLAT:.0%}", "",
                "**PSA 9 vs matched unhyped cards (same month, era, tier) `_mx`**", "", L.md(episode(rows, masks, "post9_{h}_mx")), "",
                f"**PSA 9 vs unhyped NON-top-50 cards in the SAME state, month, era and tier `_sx` (net of bucket-noise reversal; the top-50 filter's own contribution)**", "",
                L.md(episode(rows, sx_masks, f"post9_{{h}}_sx{s}")), "",
                "**PSA 10 of the same rows `_mx` (does the 10 hold its jump?)**", "", L.md(episode(rows, masks, "post10_{h}_mx")), "",
                "**Catch-up gap = PSA 9 minus own PSA 10, `_sx` (> 0: the 9 closed more of the gap than same-state non-top-50 cards did)**", "",
                L.md(episode(rows, sx_masks, f"gap_{{h}}_sx{s}")), "",
                "**Raw PSA 9 change (no control), mean / median across months**", "", L.md(episode(rows, masks, "post9_{h}")), ""]
        pairs = [(f"top-50 (app) minus NOT top-50, same state, `_mx`", rule_masks[thr]["top-50 (app)"], rule_masks[thr]["NOT top-50 (either list), same state"])]
        pairs += [(f"  PSA 10 {lab}", rule_masks[thr]["top-50 (app)"] & (rows.tier == lab).values, rule_masks[thr]["NOT top-50 (either list), same state"] & (rows.tier == lab).values) for lab in TIER_LABELS]
        pairs += [("blue chip minus NOT top-50, same state", rule_masks[thr]["blue chip"], rule_masks[thr]["NOT top-50 (either list), same state"])]
        sec += ["**Is it the top-50 filter? Same state, same month: top-50 rows minus non-top-50 rows (PSA 9 `_mx`, paired by month)**", "",
                L.md(paired_view(rows, pairs, "post9_{h}_mx")), ""]
        tmasks = [(f"rule: top-50 (app), up >= {thr:.0%}", rule_masks[thr]["top-50 (app)"]), ("rule: blue chip", rule_masks[thr]["blue chip"]),
                  (f"rule, PSA 10 >= $10K", rule_masks[thr]["top-50 (app)"] & top_tier), (f"rule, PSA 10 $1K-$10K", rule_masks[thr]["top-50 (app)"] & (rows.tier == "$1K-$10K").values),
                  ("same state, NOT top-50", rule_masks[thr]["NOT top-50 (either list), same state"]), ("universe (every card, every month start)", np.ones(len(rows), bool))]
        sec += ["**Trade: buy the first PSA 9 sale in (T, T+21], sell the first PSA 9 sale >= hold days later (within +60), 13% fee; only closed exit windows**", "",
                L.md(trade_table(rows, tmasks), ".1f"), ""]
        for h in (180, 365):
            sec += [f"**By year of T, rule top-50 (app), hold {h}d**", "", L.md(trade_by_year(rows, rule_masks[thr]["top-50 (app)"], h), ".1f"), ""]
    print("\n".join(sec), flush=True); lines += sec

    # ================================================================ ask 2
    sec = ["## 2. Inside hot card-type months: top-50 vs the rest", "",
           "A card-type group is hot in month M when the median PSA 10 bucket return over its cards (every card with PSA 10 sales) is >= the threshold with >= 5 cards. "
           "Rows are the group's PSA 9 universe cards at T = M+1. Cells are matched excess `_mx` (vs unhyped cards of the same era and tier that month).", ""]
    for thr in CAT_HYPE:
        hm = H.hype_months(r10, seg["card type"], thr, H.MIN_GROUP_CARDS)
        hot = hm[hm.hyped]
        tagged = H.tag_hype(rows[["asset_id", "signal_month"]].copy(), hm, seg["card type"], "hot")
        hot_m = tagged.hot.values
        st50 = rows.state50.values
        masks = [("hot card type, top-50 (app)", hot_m & rows.top.values), ("hot card type, blue chip", hot_m & rows.blue.values),
                 ("hot card type, NOT top-50 (either list)", hot_m & ~rows.any_top.values),
                 ("hot card type, top-50, own PSA 10 up >= 50% & PSA 9 flat", hot_m & rows.top.values & st50),
                 ("hot card type, NOT top-50, own PSA 10 up >= 50% & PSA 9 flat", hot_m & ~rows.any_top.values & st50),
                 ("hot card type, top-50, PSA 10 >= $10K", hot_m & rows.top.values & top_tier),
                 ("hot card type, NOT top-50, PSA 10 >= $10K", hot_m & ~rows.any_top.values & top_tier)]
        pairs = [("top-50 (app) minus NOT top-50", hot_m & rows.top.values, hot_m & ~rows.any_top.values),
                 ("blue chip minus NOT top-50", hot_m & rows.blue.values, hot_m & ~rows.any_top.values),
                 ("top-50 minus NOT top-50, both in own state (up >= 50%, PSA 9 flat)", hot_m & rows.top.values & st50, hot_m & ~rows.any_top.values & st50)]
        pairs += [(f"  top-50 minus NOT top-50, PSA 10 {lab}", hot_m & rows.top.values & (rows.tier == lab).values, hot_m & ~rows.any_top.values & (rows.tier == lab).values) for lab in TIER_LABELS]
        listing = ", ".join(f"{g} {m} ({n} cards, {100 * np.expm1(v):+.0f}%)" for g, m, n, v in zip(hot.group, hot.month.astype(str), hot.n_cards, hot.med_r10))
        sec += [f"### 2{'a' if thr == CAT_HYPE[0] else 'b'}. Card-type hype >= {thr:.0%}: {len(hot)} group-months, {hot.month.nunique()} distinct months", "",
                f"Which: {listing}", "",
                "**PSA 9 `_mx`**", "", L.md(episode(rows, masks, "post9_{h}_mx")), "",
                "**PSA 10 `_mx`**", "", L.md(episode(rows, masks, "post10_{h}_mx")), "",
                "**Gap `_mx`**", "", L.md(episode(rows, masks, "gap_{h}_mx")), "",
                "**Paired by month: top-50 minus non-top-50 in the same hot months (PSA 9 `_mx`)**", "", L.md(paired_view(rows, pairs, "post9_{h}_mx")), "",
                "**Trade in hot card-type months**", "", L.md(trade_table(rows, masks[:3] + masks[5:]), ".1f"), ""]
    print("\n".join(sec), flush=True); lines += sec

    # ================================================================ ask 3
    sec = ["## 3. How often does a card-type category move +50 / +70 / +100% in a month?", "",
           "Group median PSA 10 month-on-month bucket return over every card with PSA 10 sales, >= 5 cards with a return (psa9_hype's hype definition). "
           "Outcomes: the group's PSA 9 universe cards anchored at T = M+1, mean raw change and `_mx` at 3 / 6 / 12 months (n rows); plus `group PSA 10 fwd` = median over ALL "
           "the group's PSA 10 cards of log median price in M+k minus in M (n cards), which needs no PSA 9. Blank = the window is past the data end.", ""]
    m10_all = None
    for thr in CAT_COUNT:
        hm = H.hype_months(r10, seg["card type"], thr, H.MIN_GROUP_CARDS)
        hot = hm[hm.hyped].sort_values(["month", "group"])
        measured = int((hm.n_cards >= H.MIN_GROUP_CARDS).sum())
        out = []
        for _, e in hot.iterrows():
            ids = seg["card type"][e.group]
            m = rows.asset_id.isin(ids).values & (rows.signal_month == e.month).values
            r = {"category": e.group, "month": str(e.month), "cards w/ return": int(e.n_cards), "median PSA 10 move": f"{100 * np.expm1(e.med_r10):+.0f}%",
                 "universe rows": int(m.sum())}
            fwd = group_forward(sales, ids, e.month)
            for h in REPORT_H:
                v9, v10 = rows.loc[m, f"post9_{h}"].dropna(), rows.loc[m, f"post10_{h}"].dropna()
                x9 = rows.loc[m, f"post9_{h}_mx"].dropna()
                mx = f"{100 * x9.mean():+.0f}" if len(x9) else "n/a (no unhyped card of that era to match)"
                r[f"PSA 9 {HL[h]} raw / _mx (n)"] = f"{100 * v9.mean():+.0f} / {mx} ({len(v9)})" if len(v9) else ""
                r[f"PSA 10 {HL[h]} raw (n)"] = f"{100 * v10.mean():+.0f} ({len(v10)})" if len(v10) else ""
            for k in (3, 6, 12):
                v, n = fwd[k]
                r[f"group PSA 10 fwd {k}m (n)"] = f"{100 * np.expm1(v):+.0f}% ({n})" if n else ""
            out.append(r)
        sec += [f"### 3{'abc'[CAT_COUNT.index(thr)]}. >= +{thr:.0%}: {len(hot)} category-months out of {measured} (category, month) pairs measured; "
                f"{hot.month.nunique()} distinct months; years: {', '.join(f'{y}: {n}' for y, n in hot.month.dt.year.value_counts().sort_index().items()) or 'none'}", "",
                L.md(pd.DataFrame(out)) if len(out) else "_none_", ""]
    # the biggest category months there have ever been, for scale
    hm_all = H.hype_months(r10, seg["card type"], 0.0, H.MIN_GROUP_CARDS)
    hm_all = hm_all[hm_all.n_cards >= H.MIN_GROUP_CARDS].sort_values("med_r10", ascending=False).head(12)
    big = pd.DataFrame({"category": hm_all.group.values, "month": hm_all.month.astype(str).values, "cards w/ return": hm_all.n_cards.values,
                        "median PSA 10 move": [f"{100 * np.expm1(v):+.0f}%" for v in hm_all.med_r10]})
    sec += ["**The 12 largest card-type category months on record (same measure)**", "", L.md(big), ""]
    print("\n".join(sec), flush=True); lines += sec

    # ================================================================ the three cards
    now = [card_now(sales, aid, data_end, universe, {"top-50 (app list)": ids_top, "top-50 (other cap column)": ids_alt, "blue chip": ids_blue}) for aid in EXAMPLES]
    nowt = pd.DataFrame(now).T
    nowt.columns = [EXAMPLES[a] for a in EXAMPLES]
    nowt = nowt.drop(index=["card"]).reset_index().rename(columns={"index": ""})
    sec = ["## 4. Sid's three example cards today", "",
           "The rule's own input is the calendar-month bucket (this month vs last month); the 30-day windows and the last-sale-vs-previous-three are shown because "
           "a thin PSA 10 (one sale a quarter) has no bucket return at all.", "", nowt.to_markdown(index=False), ""]
    print("\n".join(sec), flush=True); lines += sec

    (OUT / "psa9_buyrule.md").write_text("\n".join(lines))
    rows.to_pickle(args.cache_dir / "psa9_buyrule_rows.pkl")
    print(f"\nwrote {OUT / 'psa9_buyrule.md'} ({time.time() - t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
