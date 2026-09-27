#!/usr/bin/env python3
"""Does the PSA 9 catch up inside hype windows? psa9_lag.py segmented (2026-09-27)

    analysis/.venv/bin/python analysis/psa9_hype.py --cache-dir <dir with clean_sales.pkl>
    analysis/.venv/bin/python analysis/psa9_hype.py --hype 0.20     # looser hype threshold

Sid's follow-up to the lag study: the catch-up happens in hyped categories,
when buyers get priced out of the PSA 10s, and the market-wide average buries
it. So the same PSA 9 measurements as psa9_lag.py, cut four ways:

  1. Segment. Card type (Gold Star, LV.X, Neo Shining, e-card Crystal,
     HGSS Prime/LEGEND, EX-era ex, WOTC 1st Edition, modern V/ex/alt art,
     promos), era (by year), language, and character (top 50 by alt-side
     market cap, latest/characters.csv; a card counts for every character
     named in its subject, whole-word match as in history/coverage.py).
  2. Hype windows only. A group (a card type, an era, or a character) is
     "hyped" in calendar month M when the median over its cards of the PSA 10
     month-on-month bucket return (log median price in M minus M-1, both
     months with a clean sale, clipped at +-log 3) is >= log(1 + HYPE), with
     >= MIN_GROUP_CARDS cards having that return. Measured on EVERY card with
     PSA 10 sales, not just the PSA 9 universe, so it is the group as a whole.
     The signal date is T = the first day of M+1: everything used to flag it
     is dated inside M, so nothing after T is used.
  3. PSA 10 price tier at T (median PSA 10 sale in (T-30, T]): < $1K,
     $1K-$10K, >= $10K.
  4. Horizons 1, 2, 3, 6, 12 months: 30-day windows ending T+30/60/90/180/365.

Rows are (card, T) for every PSA 9 universe card (psa9_lag.py's: >= 12 months
with a sale in each grade since 2021) at every month start. For each row:
  post9_h   = median log PSA 9 price in the horizon window minus in (T-30, T]
  post10_h  = the same for the PSA 10
  gap_h     = post9_h - post10_h: > 0 means the PSA 9 / PSA 10 ratio rose
              after T, i.e. the PSA 9 caught up (the direct test of the theory)
  `_ex`     = minus the median over every universe row at the same T, so a
              market-wide month is not read as a segment effect
  trade     = buy the first PSA 9 sale in (T, T+21], sell at the first PSA 9
              sale >= hold days later (within a further 60), 13% sell fee;
              only rows whose exit window closed before the data end
A horizon window that ends after the data end is left blank (right-censored),
so the 12-month column only covers signals up to 2025-09.

The test of "hype matters": within a segment, hype rows vs the same
segment's non-hype rows, both as excess over the month's universe median;
difference and t from OLS on a hype dummy, two-way clustered by card and
month. A segment with few hype months is few independent episodes however
many cards it has, so every table shows `months`, and p-values are
Benjamini-Hochberg adjusted across every segment x horizon tested.

Checks: analysis/test_psa9_hype.py.

Result (2026-09-27, README "PSA 9 catch-up in hype windows"): a +30% group
month is rare and nearly all in 2026, too recent for 6-12 month outcomes. At
+15%, against same-month unhyped cards of the same era and tier, one
observation per hype month: PSA 9 +6.8% at 6m (t 3.6, 28 months), +11.0% at
12m (t 4.8, 24), nothing at 3m, PSA 10 flat; positive for every signal year
2021-2025; carried by character hype; +19% / +16% where the card's own PSA 10
jumped and its PSA 9 had not. A slow, modest relative edge, not a trade that
clears the fee on its own.
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

ROOT = L.ROOT
OUT = ROOT / "analysis" / "out"
HYPE = 0.30
MIN_GROUP_CARDS = 5
TOP_CHARACTERS = 50
HORIZONS = (30, 60, 90, 180, 365)          # days: window (T+h-30, T+h]
HLABEL = {30: "1m", 60: "2m", 90: "3m", 180: "6m", 365: "12m"}
HOLDS = (90, 180, 365)
TIERS = ((0, 1_000, "< $1K"), (1_000, 10_000, "$1K-$10K"), (10_000, np.inf, ">= $10K"))
DAY = L.DAY


# ---------------------------------------------------------------- segments

def _num(s):
    return pd.to_numeric(s.str.extract(r"(\d+)", expand=False), errors="coerce")


def card_types(a):
    """One card type per card (first rule that matches), else NaN. `a`: assets frame
    with card_name, year, set, subject, card_number."""
    name = a.card_name.fillna("")
    st = a["set"].fillna("")
    yr = a.year
    num = _num(a.card_number.fillna(""))
    ex_card = a.subject.fillna("").str.contains(r"\bex\b", case=False, regex=True)   # alt.xyz puts it in the subject: "Lugia Ex"
    rules = [
        ("Gold Star", (yr <= 2008) & name.str.contains(r"Gold Star|\bStar\b(?= #| Holo)", regex=True)),
        ("LV.X", yr.between(2006, 2010) & name.str.contains(r"\bLv\.?\s?X\b|\bLV\.X\b", case=False, regex=True)
         & ~name.str.contains(r"Lv\.?\s?X (?:Deck|Collection)", case=False, regex=True)),
        ("Neo Shining", (yr <= 2003) & name.str.contains(r"\bShining\b", regex=True)),
        ("e-card Crystal", ((st.str.contains("Aquapolis") & num.between(148, 150)) | (st.str.contains("Skyridge") & num.between(145, 150)))
         & ~name.str.contains(r"#H", regex=True)),
        ("HGSS Prime/LEGEND", yr.between(2010, 2011) & (name.str.contains(r"\bPrime\b", regex=True)
                                                       | name.str.contains(r"\bLegend\b(?!s|ary)", regex=True)
                                                       & ~name.str.contains(r"Legend (?:Promo|Perfect)|Promo Legend|Legend \w+ Constructed|Japanese Legend", regex=True))),
        ("EX-era ex", yr.between(2003, 2007) & ex_card),
        ("WOTC 1st Edition", (yr <= 2003) & name.str.contains("1st Edition")),
        ("WOTC other", yr <= 2002),
        ("Modern V/VMAX/VSTAR", (yr >= 2020) & name.str.contains(r"\b(?:V|VMAX|VSTAR)\b(?= #| Holo|/|$)", regex=True)),
        ("Modern ex (2023+)", (yr >= 2023) & ex_card),
        ("Modern alt art / illustration", (yr >= 2014) & name.str.contains(r"Illustration|Alternate Art|Alt Art|Full Art|Secret|Rainbow|Character Rare|Trainer Gallery", case=False, regex=True)),
        ("Promo", name.str.contains(r"\bPromo", regex=True) | st.str.contains(r"\bPromo", regex=True)),
    ]
    out = pd.Series(np.nan, index=a.index, dtype=object)
    for label, m in rules:
        out[out.isna() & m.fillna(False)] = label
    return out


def eras(a):
    yr = a.year
    st = a["set"].fillna("")
    ecard = st.str.contains(r"Aquapolis|Skyridge|Expedition|e-?Card|Split Earth|Wind From the Sea|Mysterious Mountains|Town on No Map", case=False, regex=True)
    exset = st.str.contains(r"\bEx\b", case=False, regex=True)
    conds = [
        ((yr <= 2002) | ((yr == 2003) & ecard), "WOTC / e-card (1999-2003)"),
        ((yr <= 2006) | ((yr == 2007) & exset), "EX era (2003-2007)"),
        (yr <= 2009, "DP / Platinum (2007-2009)"),
        (yr <= 2013, "HGSS / BW (2010-2013)"),
        (yr <= 2019, "XY / SM (2014-2019)"),
        (yr <= 2022, "SWSH (2020-2022)"),
        (yr <= 2026, "SV / Mega (2023-2026)"),
    ]
    out = pd.Series(np.nan, index=a.index, dtype=object)
    for m, label in conds:
        out[out.isna() & m.fillna(False)] = label
    return out


def languages(a):
    text = (a.card_name.fillna("") + " " + a["set"].fillna("") + " " + a.variety.fillna(""))
    jp = text.str.contains(r"\bJapanese\b", case=False, regex=True)
    other = text.str.contains(r"\b(?:French|German|Spanish|Italian|Portuguese|Dutch|Korean|Chinese|Indonesian|Russian|Thai|Polish)\b", case=False, regex=True)
    return pd.Series(np.where(jp, "Japanese", np.where(other, "other foreign", "English")), index=a.index)


def _norm(s):
    return " " + re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip() + " "


def character_members(a, names):
    """{character: set(asset_id)}; whole-word, hyphen = space (history/coverage.py's rule)."""
    subj = a.subject.fillna("").map(_norm)
    return {c: set(a.index[subj.str.contains(re.escape(_norm(c)), regex=True)]) for c in names}


def load_segments(assets, top=TOP_CHARACTERS):
    """{dimension: {group: set(asset_id)}}"""
    a = assets.reset_index().rename(columns={"index": "asset_id"}).set_index("asset_id")
    ct, er, lg = card_types(a), eras(a), languages(a)
    chars = pd.read_csv(ROOT / "latest" / "characters.csv").sort_values("alt_market_cap_usd", ascending=False).character.head(top).tolist()
    seg = {"card type": {g: set(ct.index[ct == g]) for g in ct.dropna().unique()},
           "era": {g: set(er.index[er == g]) for g in er.dropna().unique()},
           "language": {g: set(lg.index[lg == g]) for g in lg.unique()},
           "character": character_members(a, chars)}
    return seg, chars


# ---------------------------------------------------------------- hype months

def psa10_bucket_returns(sales):
    """Every card's PSA 10 month-on-month log return of the monthly median (psa9_lag's buckets)."""
    s = sales[(sales.grade == "10.0") & (sales.date >= L.START)]
    lp = np.log(s.price)
    m = lp.groupby([s.asset_id, s.date.dt.to_period("M")]).median()
    m.index.names = ["asset_id", "month"]
    m = m.reset_index(name="m10")
    prev = m.month - 1
    key = pd.MultiIndex.from_arrays([m.asset_id, m.month])
    look = pd.Series(m.m10.values, index=key)
    m["r10"] = (m.m10.values - look.reindex(pd.MultiIndex.from_arrays([m.asset_id, prev])).values)
    m["r10"] = m.r10.clip(-L.RET_CLIP, L.RET_CLIP)
    return m.dropna(subset=["r10"])[["asset_id", "month", "r10"]]


def hype_months(r10, groups, hype=HYPE, min_cards=MIN_GROUP_CARDS):
    """Per (group, month): cards with a return, median return, hyped flag."""
    rows = []
    for g, members in groups.items():
        sub = r10[r10.asset_id.isin(members)]
        agg = sub.groupby("month").r10.agg(["median", "size"]).reset_index()
        agg.insert(0, "group", g)
        rows.append(agg)
    if not rows:
        return pd.DataFrame(columns=["group", "month", "median", "size", "hyped"])
    out = pd.concat(rows, ignore_index=True).rename(columns={"median": "med_r10", "size": "n_cards"})
    out["hyped"] = (out.n_cards >= min_cards) & (out.med_r10 >= np.log1p(hype))
    return out


# ---------------------------------------------------------------- per-card rows

def _win_medians(d, lp, anchors, lo_off, hi_off):
    """median of lp over (anchor+lo_off, anchor+hi_off] for each anchor; NaN when empty."""
    lo = np.searchsorted(d, anchors + lo_off * DAY, side="right")
    hi = np.searchsorted(d, anchors + hi_off * DAY, side="right")
    out = np.full(len(anchors), np.nan)
    for i in np.flatnonzero(hi > lo):
        out[i] = np.median(lp[lo[i]:hi[i]])
    return out


def card_rows(aid, d10, p10, d9, p9, anchors, data_end):
    """One row per anchor T: PSA 9 / PSA 10 window medians and the PSA 9 trade, right-censored at data_end."""
    lp10, lp9 = np.log(p10), np.log(p9)
    row = {"asset_id": np.repeat(aid, len(anchors)), "date": anchors}
    row["m9_move"] = _win_medians(d9, lp9, anchors, -30, 0)
    row["m10_move"] = _win_medians(d10, lp10, anchors, -30, 0)
    for h in HORIZONS:
        ok = anchors + h * DAY <= data_end
        m9 = _win_medians(d9, lp9, anchors, h - 30, h)
        m10 = _win_medians(d10, lp10, anchors, h - 30, h)
        row[f"m9_{h}"] = np.where(ok, m9, np.nan)
        row[f"m10_{h}"] = np.where(ok, m10, np.nan)
    i = np.searchsorted(d9, anchors, side="right")
    has = (i < len(d9))
    has[has] &= d9[i[has]] <= anchors[has] + L.ENTRY_DAYS * DAY
    entry = np.full(len(anchors), np.nan)
    entry[has] = p9[i[has]]
    row["entry"] = entry
    for h in HOLDS:
        net = np.full(len(anchors), np.nan)
        closed = anchors + (L.ENTRY_DAYS + h + L.EXIT_GRACE) * DAY <= data_end
        for k in np.flatnonzero(has & closed):
            j = np.searchsorted(d9, d9[i[k]] + h * DAY, side="left")
            if j < len(d9) and d9[j] <= d9[i[k]] + (h + L.EXIT_GRACE) * DAY:
                net[k] = p9[j] * (1 - L.FEE) / p9[i[k]] - 1
        row[f"net{h}"] = np.where(closed, net, np.nan)
        row[f"closed{h}"] = closed & has
    return row


def build_rows(sales, universe):
    s = sales[sales.asset_id.isin(universe)]
    by = {(aid, g): grp for (aid, g), grp in s.groupby(["asset_id", "grade"], sort=False)}
    data_end = sales.date.max().to_datetime64().astype("datetime64[D]")
    anchors = pd.date_range(L.START + pd.offsets.MonthBegin(1), data_end, freq="MS").values.astype("datetime64[D]")
    parts = []
    for aid in universe:
        g10, g9 = by.get((aid, "10.0")), by.get((aid, "9.0"))
        if g10 is None or g9 is None:
            continue
        parts.append(pd.DataFrame(card_rows(aid, g10.date.values.astype("datetime64[D]"), g10.price.values.astype(float),
                                            g9.date.values.astype("datetime64[D]"), g9.price.values.astype(float), anchors, data_end)))
    df = pd.concat(parts, ignore_index=True)
    for h in HORIZONS:
        df[f"post9_{h}"] = df[f"m9_{h}"] - df.m9_move
        df[f"post10_{h}"] = df[f"m10_{h}"] - df.m10_move
        df[f"gap_{h}"] = df[f"post9_{h}"] - df[f"post10_{h}"]
    df["month"] = pd.to_datetime(df.date).dt.to_period("M")
    df["signal_month"] = df.month - 1                      # the month whose sales flagged the row
    df["p10"] = np.exp(df.m10_move)
    df["tier"] = pd.cut(df.p10, [t[0] for t in TIERS] + [np.inf], labels=[t[2] for t in TIERS], right=False)
    df.attrs["data_end"] = data_end
    return df


OUTCOMES = [f"{k}_{h}" for h in HORIZONS for k in ("post9", "post10", "gap")] + [f"net{h}" for h in HOLDS]


def add_excess(df):
    """Excess over the universe median of the same anchor month, for every outcome."""
    med = df.groupby("month")[OUTCOMES].transform("median")
    for c in OUTCOMES:
        df[c + "_ex"] = df[c] - med[c]
    return df


def tag_hype(df, hm, members, col):
    """df[col] = True where the card belongs to a group hyped in the signal month (any of its groups)."""
    hot = hm[hm.hyped][["group", "month"]]
    card_group = pd.DataFrame([(aid, g) for g, ids in members.items() for aid in ids], columns=["asset_id", "group"])
    hot_cards = card_group.merge(hot, on="group")[["asset_id", "month"]].drop_duplicates().rename(columns={"month": "signal_month"})
    hot_cards[col] = True
    out = df.merge(hot_cards, on=["asset_id", "signal_month"], how="left")
    out[col] = out[col].fillna(False).astype(bool)
    return out


# ---------------------------------------------------------------- statistics

def mean_t(v, card, month):
    ok = np.isfinite(v)
    if ok.sum() < 10:
        return np.nan, np.nan, int(ok.sum())
    beta, se = L.ols_twoway(np.ones((ok.sum(), 1)), v[ok], card[ok], month[ok])
    return beta[0], beta[0] / se[0] if se[0] > 0 else np.nan, int(ok.sum())


def diff_t(v, is_hype, card, month):
    """hype minus non-hype mean of v, t from OLS on a dummy with two-way clustered SE."""
    ok = np.isfinite(v)
    if ok.sum() < 20 or is_hype[ok].sum() < 5 or (~is_hype[ok]).sum() < 5:
        return np.nan, np.nan
    X = np.column_stack([np.ones(ok.sum()), is_hype[ok].astype(float)])
    beta, se = L.ols_twoway(X, v[ok], card[ok], month[ok])
    return beta[1], beta[1] / se[1] if se[1] > 0 else np.nan


def pval(t):
    from scipy.stats import norm
    return 2 * norm.sf(np.abs(t))


def bh(p):
    """Benjamini-Hochberg adjusted p-values (NaN kept)."""
    p = np.asarray(p, float)
    q = np.full(len(p), np.nan)
    ok = np.isfinite(p)
    if not ok.any():
        return q
    pv = p[ok]
    order = np.argsort(pv)
    ranked = pv[order] * len(pv) / (np.arange(len(pv)) + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(len(pv))
    out[order] = np.minimum(ranked, 1)
    q[ok] = out
    return q


def segment_table(df, dim, members, hm, horizons=HORIZONS):
    """One row per (group, horizon): hype-row PSA 9 / gap / PSA 10 excess, and hype minus non-hype."""
    rows = []
    card = df.asset_id.values
    month = df.month.astype(str).values
    for g, ids in members.items():
        in_g = df.asset_id.isin(ids).values
        if not in_g.any():
            continue
        hot_months = set(hm[(hm.group == g) & hm.hyped].month)
        is_h = in_g & df.signal_month.isin(hot_months).values
        sub = in_g
        for h in horizons:
            r = {"dimension": dim, "group": g, "horizon": HLABEL[h], "cards": int(len(set(card[in_g]))),
                 "hype months": len(hot_months), "hype months with data": int(df.loc[is_h & np.isfinite(df[f"post9_{h}_ex"].values), "signal_month"].nunique()),
                 "hype rows": int((is_h & np.isfinite(df[f"post9_{h}_ex"].values)).sum())}
            for k in ("post9", "gap", "post10"):
                v = df[f"{k}_{h}_ex"].values
                m, t, _ = mean_t(v[is_h], card[is_h], month[is_h])
                r[f"{k} hype"], r[f"{k} hype t"] = m, t
                r[f"{k} hype median"] = np.nanmedian(v[is_h]) if is_h.any() and np.isfinite(v[is_h]).any() else np.nan
                d, dt = diff_t(v[sub], is_h[sub], card[sub], month[sub])
                r[f"{k} diff"], r[f"{k} diff t"] = d, dt
            rows.append(r)
    return pd.DataFrame(rows)


def trade_rows(df, mask, label):
    rows = []
    for h in HOLDS:
        closed = df[f"closed{h}"].values & mask
        v = df.loc[closed, f"net{h}"]
        ex = df.loc[closed, f"net{h}_ex"]
        got = v.notna()
        rows.append({"sample": label, "hold": h, "entered": int(closed.sum()), "no exit %": 100 * (1 - got.mean()) if closed.any() else np.nan,
                     "hit %": 100 * (v[got] > 0).mean() if got.any() else np.nan,
                     "mean net %": 100 * v[got].mean() if got.any() else np.nan, "median net %": 100 * v[got].median() if got.any() else np.nan,
                     "median vs same-month universe median, pts": 100 * ex[got].median() if got.any() else np.nan,
                     "months": df.loc[closed, "month"].nunique()})
    return rows


def add_matched_excess(df, keys=("era", "tier"), hype_col="hype_any", suffix="_mx"):
    """`_mx` = minus the median NON-hype row with the same anchor month and the same `keys`
    (default era x PSA 10 price tier). The hype months cluster in the 2025-26 vintage run and
    in particular eras, so the universe median alone would re-find the vintage premium; this
    compares a hyped card with unhyped cards of its own era and price level, the same month."""
    keys = ["month", *keys]
    idx = pd.MultiIndex.from_frame(df[keys].astype(object))
    base = df[~df[hype_col]]
    for c in [f"{k}_{h}" for h in HORIZONS for k in ("post9", "post10", "gap")] + [f"net{h}" for h in HOLDS]:
        ref = base.groupby(keys, observed=True)[c].median()
        ref.index = pd.MultiIndex.from_frame(ref.index.to_frame(index=False).astype(object))
        df[c + suffix] = df[c].values - ref.reindex(idx).values
    return df


def by_month_stats(df, mask, col):
    """One observation per signal month (mean over that month's hype rows), then the mean,
    median, share > 0 and t across months: the honest n when a few months carry many cards."""
    v = df.loc[mask, [col, "signal_month"]].dropna()
    if v.empty:
        return np.nan, np.nan, np.nan, np.nan, 0
    per = v.groupby("signal_month")[col].mean()
    n = len(per)
    t = per.mean() / (per.std(ddof=1) / np.sqrt(n)) if n >= 3 and per.std(ddof=1) > 0 else np.nan
    return per.mean(), per.median(), (per > 0).mean(), t, n


def episode_view(df, masks, col_fmt, horizons=HORIZONS):
    """rows = samples, columns = horizons; cell = mean / median across months, % months > 0, (t), [months]."""
    out = []
    for label, m in masks:
        r = {"sample": label, "rows": int(m.sum())}
        for h in horizons:
            mean, med, pos, t, n = by_month_stats(df, m, col_fmt.format(h=h))
            r[HLABEL[h]] = "" if n == 0 else (f"{100 * mean:+.1f} / {100 * med:+.1f}, {100 * pos:.0f}% up"
                                               + (f" ({t:.1f})" if np.isfinite(t) else "") + f" [{n}]")
        out.append(r)
    return pd.DataFrame(out)


# ---------------------------------------------------------------- report

def pct(x, t=None, med=None):
    if not np.isfinite(x):
        return ""
    s = f"{100 * x:+.1f}"
    if med is not None and np.isfinite(med):
        s += f" / {100 * med:+.1f}"
    if t is not None and np.isfinite(t):
        s += f" ({t:.1f})"
    return s


def wide(tbl, value, tcol, horizons=HORIZONS, med=None, extra=("cards", "hype months")):
    """group x horizon view of one statistic."""
    out = []
    for (dim, g), q in tbl.groupby(["dimension", "group"], sort=False):
        r = {"group": g}
        for c in extra:
            r[c] = int(q[c].iloc[0])
        for h in horizons:
            x = q[q.horizon == HLABEL[h]]
            if len(x):
                x = x.iloc[0]
                r[HLABEL[h]] = pct(x[value], x[tcol], x[med] if med else None)
                r[HLABEL[h] + " n"] = int(x["hype rows"])
        out.append(r)
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", type=Path, default=L.DEFAULT_CACHE)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--hype", type=float, nargs="+", default=[HYPE, 0.20, 0.15], help="one report per threshold")
    ap.add_argument("--min-group-cards", type=int, default=MIN_GROUP_CARDS)
    ap.add_argument("--min-months", type=int, default=L.MIN_MONTHS)
    args = ap.parse_args()
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    sales = L.load_clean_sales(cache_dir=args.cache_dir, rebuild=args.rebuild)
    assets = L.load_assets()
    assets = assets.join(pd.read_csv(ROOT / "history" / "assets.csv", usecols=["asset_id", "card_number"], dtype=str).set_index("asset_id"))
    seg, chars = load_segments(assets)
    monthly = L.monthly_series(sales)
    universe = L.select_universe(monthly, args.min_months)
    print(f"{len(sales):,} clean sales through {sales.date.max().date()}; PSA 9 universe {len(universe):,} cards", flush=True)

    r10 = psa10_bucket_returns(sales)
    base = build_rows(sales, universe)
    base = add_excess(base)
    print(f"{len(base):,} (card, month-start) rows built ({time.time() - t0:.0f}s)", flush=True)
    ret = L.monthly_returns(monthly[monthly.asset_id.isin(universe)])[["asset_id", "month", "r10", "r9"]]
    ret = ret.rename(columns={"month": "signal_month", "r10": "own_r10", "r9": "own_r9"})
    data_end = base.attrs["data_end"]
    base = base.merge(ret, on=["asset_id", "signal_month"], how="left")
    base["vintage"] = base.asset_id.map(assets.vintage).fillna(False).astype(bool)
    base.attrs["data_end"] = data_end
    for thr in args.hype:
        report(base, r10, seg, chars, universe, thr, args)
    print(f"done ({time.time() - t0:.0f}s total)")


def report(base, r10, seg, chars, universe, hype, args):
    """Tag hype rows at one threshold and write analysis/out/psa9_hype[_hypeNN].md."""
    args.hype = hype
    hms = {dim: hype_months(r10, groups, hype, args.min_group_cards) for dim, groups in seg.items()}
    rows = base
    data_end = base.attrs["data_end"]
    for dim in ("card type", "era", "character"):
        rows = tag_hype(rows, hms[dim], seg[dim], f"hype_{dim.replace(' ', '_')}")
    rows["hype_any"] = rows.hype_card_type | rows.hype_era | rows.hype_character
    era_of = {aid: g for g, ids in seg["era"].items() for aid in ids}
    rows["era"] = rows.asset_id.map(era_of)
    rows = add_matched_excess(rows)

    lines = [f"# PSA 9 catch-up in hype windows ({pd.Timestamp.today().date()})", "",
             f"Data through {pd.Timestamp(data_end).date()}. PSA 9 universe: {len(universe):,} cards (>= {args.min_months} months with a sale in each grade since 2021), "
             f"{len(rows):,} (card, month-start) rows. Hype = a group's median PSA 10 month-on-month return >= +{args.hype:.0%} with >= {args.min_group_cards} cards "
             "having a return, measured on every card with PSA 10 sales; the row's anchor T is the first day of the next month.", "",
             "Cells are **excess over the median universe card anchored the same month**, in %: `post9` = PSA 9 change from (T-30, T] to the "
             "30-day window ending at the horizon; `gap` = post9 minus the PSA 10's own change (> 0: the PSA 9 / PSA 10 ratio rose, i.e. the PSA 9 caught up); "
             "`diff` = hype rows minus the same group's non-hype rows. Format: mean / median (t, two-way clustered by card and month). "
             "`n` = rows with data. 12m only covers signals up to a year before the data end.", ""]

    # --- hype months
    hm_rows = []
    for dim, hm in hms.items():
        for g, q in hm.groupby("group"):
            hot = q[q.hyped].sort_values("month")
            hm_rows.append({"dimension": dim, "group": g, "cards with PSA 10 sales": int(r10[r10.asset_id.isin(seg[dim][g])].asset_id.nunique()),
                            "months measured": int((q.n_cards >= args.min_group_cards).sum()), "hype months": len(hot),
                            "which (median PSA 10 move)": ", ".join(f"{m} {100 * np.expm1(v):+.0f}%" for m, v in zip(hot.month.astype(str), hot.med_r10))[:400]})
    hmt = pd.DataFrame(hm_rows)
    hmt = hmt[(hmt.dimension != "character") | hmt.group.isin(chars)]
    sec = ["## 1. Hype months per group", "", L.md(hmt), ""]
    print("\n".join(sec)); lines += sec

    # --- segment tables
    tables = {dim: segment_table(rows, dim, seg[dim], hms[dim]) for dim in ("card type", "era", "language", "character")}
    # tier: a card attribute, not a group -> hype = any of the card's groups hyped; non-hype = the rest
    tier_rows = []
    card, month = rows.asset_id.values, rows.month.astype(str).values
    for lo, hi, label in TIERS:
        in_t = (rows.tier == label).values
        for h in HORIZONS:
            r = {"dimension": "tier", "group": label, "horizon": HLABEL[h], "cards": int(rows.loc[in_t, "asset_id"].nunique()),
                 "hype months": int(rows.loc[in_t & rows.hype_any.values, "signal_month"].nunique())}
            is_h = in_t & rows.hype_any.values
            r["hype rows"] = int((is_h & np.isfinite(rows[f"post9_{h}_ex"].values)).sum())
            for k in ("post9", "gap", "post10"):
                v = rows[f"{k}_{h}_ex"].values
                m, t, _ = mean_t(v[is_h], card[is_h], month[is_h])
                r[f"{k} hype"], r[f"{k} hype t"] = m, t
                r[f"{k} hype median"] = np.nanmedian(v[is_h]) if np.isfinite(v[is_h]).any() else np.nan
                d, dt = diff_t(v[in_t], is_h[in_t], card[in_t], month[in_t])
                r[f"{k} diff"], r[f"{k} diff t"] = d, dt
            tier_rows.append(r)
    tables["tier"] = pd.DataFrame(tier_rows)
    allt = pd.concat(tables.values(), ignore_index=True)
    # multiple testing over every (group, horizon) with at least 3 hype months of data
    test = allt["hype months with data"].fillna(allt["hype months"]) >= 3 if "hype months with data" in allt else allt["hype months"] >= 3
    for k in ("post9", "gap"):
        p = np.where(test, pval(allt[f"{k} diff t"]), np.nan)
        allt[f"{k} diff q"] = bh(p)
    n_tests = int(np.isfinite(allt["post9 diff q"]).sum())

    def block(dim, title, groups=None):
        q = allt[allt.dimension == dim]
        if groups is not None:
            q = q[q.group.isin(groups)]
        return ["### " + title, "",
                "**PSA 9 excess after T, hype rows only** (mean / median (t))", "", L.md(wide(q, "post9 hype", "post9 hype t", med="post9 hype median")), "",
                "**Catch-up: PSA 9 minus PSA 10 change, hype rows** (> 0 = the PSA 9 closed some of the gap)", "", L.md(wide(q, "gap hype", "gap hype t", med="gap hype median")), "",
                "**Hype minus the same group's non-hype rows: PSA 9 excess (t)**", "", L.md(wide(q, "post9 diff", "post9 diff t")), "",
                "**Hype minus non-hype: catch-up gap (t)**", "", L.md(wide(q, "gap diff", "gap diff t")), ""]

    sec = ["## 2. By segment", ""]
    sec += block("card type", "2a. Card type")
    sec += block("era", "2b. Era")
    sec += block("language", "2c. Language")
    sec += block("tier", "2d. PSA 10 price tier at T (hype = any of the card's type / era / character groups hyped)")
    active = [c for c in chars if hms["character"][(hms["character"].group == c) & hms["character"].hyped].shape[0] > 0]
    sec += block("character", f"2e. Top {TOP_CHARACTERS} characters (the {len(active)} with at least one hype month)", active)
    print("\n".join(sec)); lines += sec

    # --- what survives multiple testing
    sig = allt[(allt["post9 diff q"] < 0.10) | (allt["gap diff q"] < 0.10)]
    sig_view = sig[["dimension", "group", "horizon", "hype months", "hype months with data", "hype rows", "post9 hype", "post9 diff", "post9 diff t", "post9 diff q",
                    "gap hype", "gap diff", "gap diff t", "gap diff q"]].copy()
    for c in ("post9 hype", "post9 diff", "gap hype", "gap diff"):
        sig_view[c] = 100 * sig_view[c]
    raw = allt[test & ((allt["post9 diff t"].abs() >= 2) | (allt["gap diff t"].abs() >= 2))]
    sec = ["## 3. What survives multiple testing", "",
           f"{n_tests} (group, horizon) cells with >= 3 hype months were tested for each outcome. At |t| >= 2 about {0.046 * n_tests:.0f} would pass by chance; "
           f"{len(raw)} cells do on at least one of the two. Benjamini-Hochberg q < 0.10 on either outcome:", "",
           L.md(sig_view, ".2f") if len(sig_view) else "_none_", ""]
    print("\n".join(sec)); lines += sec

    # --- pooled hype, the priced-out state, trade
    card, month = rows.asset_id.values, rows.month.astype(str).values
    def pooled(mask, label):
        r = {"sample": label, "rows": int(mask.sum()), "months": int(rows.loc[mask, "signal_month"].nunique())}
        for h in HORIZONS:
            v = rows[f"gap_{h}_ex"].values
            m, t, n = mean_t(v[mask], card[mask], month[mask])
            r[f"gap {HLABEL[h]}"] = pct(m, t)
            v9 = rows[f"post9_{h}_ex"].values
            m9, t9, _ = mean_t(v9[mask], card[mask], month[mask])
            r[f"PSA 9 {HLABEL[h]}"] = pct(m9, t9)
        return r
    # own-card state in the signal month (bucket returns): did the PSA 10 move and the PSA 9 not?
    opened = ((rows.own_r10 >= np.log(1.30)) & (rows.own_r9 < np.log(1.10))).values
    hyp = rows.hype_any.values
    top = (rows.tier == ">= $10K").values
    pr = [pooled(np.ones(len(rows), bool), "every row (universe)"),
          pooled(hyp, "any hype (card type, era or character)"),
          pooled(~hyp, "no hype"),
          pooled(rows.hype_character.values, "character hype"),
          pooled(rows.hype_card_type.values | rows.hype_era.values, "card type or era hype"),
          pooled(hyp & opened, "hype, and this card's PSA 10 up >= 30% while its PSA 9 < +10% (priced-out state)"),
          pooled(~hyp & opened, "no hype, same card state (control for bucket noise)"),
          pooled(hyp & opened & top, "hype, priced-out state, PSA 10 >= $10K"),
          pooled(~hyp & opened & top, "no hype, priced-out state, PSA 10 >= $10K")]
    sec = ["## 4. Pooled: hype vs not, and the 'priced out' state", "",
           "Excess over the universe the same month, mean % (t). `gap` > 0 = PSA 9 caught up on the PSA 10. The priced-out rows condition on the card's own "
           "monthly buckets, so part of what follows is a low PSA 9 bucket reverting: read them against the no-hype row with the same state.", "",
           L.md(pd.DataFrame(pr)), ""]
    tr = []
    tr += trade_rows(rows, np.ones(len(rows), bool), "universe (every card, every month start)")
    tr += trade_rows(rows, hyp, "any hype")
    tr += trade_rows(rows, hyp & opened, "hype + priced-out state")
    tr += trade_rows(rows, hyp & top, "hype, PSA 10 >= $10K")
    tr += trade_rows(rows, hyp & opened & top, "hype + priced-out + PSA 10 >= $10K")
    sec += ["**Trade: buy the PSA 9 at the first PSA 9 sale in (T, T+21], sell at the first PSA 9 sale >= hold days later (within +60), 13% fee**", "",
            L.md(pd.DataFrame(tr), ".1f"), ""]
    yr = rows.assign(year=pd.to_datetime(rows.date).dt.year)
    by_year = []
    for y, q in yr.groupby("year"):
        hq = q[q.hype_any]
        by_year.append({"year": y, "hype rows": len(hq), "hype months": hq.signal_month.nunique(),
                        **{f"gap {HLABEL[h]} hype": 100 * hq[f"gap_{h}_ex"].mean() for h in HORIZONS},
                        **{f"gap {HLABEL[h]} no hype": 100 * q[~q.hype_any][f"gap_{h}_ex"].mean() for h in (90, 180, 365)}})
    sec += ["**By year of T: catch-up gap, excess, mean %**", "", L.md(pd.DataFrame(by_year), ".1f"), ""]
    print("\n".join(sec)); lines += sec

    # --- episode level: one observation per hype month, cohort-matched
    hyp = rows.hype_any.values
    masks = [("any hype", hyp), ("character hype", rows.hype_character.values),
             ("card type hype", rows.hype_card_type.values), ("era hype", rows.hype_era.values)]
    masks += [(f"any hype, PSA 10 {label}", hyp & (rows.tier == label).values) for _, _, label in TIERS]
    masks += [("any hype, priced-out state", hyp & opened), ("no hype, priced-out state (bucket-noise control)", ~hyp & opened)]
    sec = ["## 5. The test to trust: one observation per hype month, against same-month unhyped cards of the same era and price tier", "",
           "Each hype month's rows are averaged into one number; cells are the mean / median across months, the share of months that were up, "
           "the t across months, and [the number of months]. `_mx` = minus the median unhyped card of the same era and PSA 10 price tier anchored "
           "the same month, so neither the 2025-26 vintage run nor a tier's own drift is read as a hype effect. Sections 2-4 count cards, which a "
           "single month with hundreds of cards can dominate; this section cannot be.", "",
           "**PSA 9 excess**", "", L.md(episode_view(rows, masks, "post9_{h}_mx")), "",
           "**PSA 10 excess (does the PSA 10 keep going too?)**", "", L.md(episode_view(rows, masks, "post10_{h}_mx")), "",
           "**Catch-up `gap` = PSA 9 minus its own PSA 10 (> 0: the PSA 9 / PSA 10 ratio rose)**", "", L.md(episode_view(rows, masks, "gap_{h}_mx")), ""]
    # the priced-out state against unhyped cards in the SAME state, era and tier: nets out the state's own bucket noise
    rows["opened"] = opened
    rows = add_matched_excess(rows, keys=("era", "tier", "opened"), suffix="_sx")
    po = [("any hype, priced-out state", hyp & opened), ("any hype, not priced-out", hyp & ~opened)]
    sec += ["**Priced-out state vs unhyped cards in the same state, era and tier (`_sx`)**", "",
            "PSA 9:", "", L.md(episode_view(rows, po, "post9_{h}_sx")), "", "PSA 10:", "", L.md(episode_view(rows, po, "post10_{h}_sx")), "",
            "Gap:", "", L.md(episode_view(rows, po, "gap_{h}_sx")), ""]
    yr_rows = []
    for name, m in (("any hype", hyp), ("character hype", rows.hype_character.values)):
        q = rows[m]
        for y, qy in q.groupby(q.signal_month.dt.year):
            r = {"sample": name, "year of hype month": y, "months": qy.signal_month.nunique()}
            for h in (90, 180, 365):
                for k, lab in (("post9", "PSA 9"), ("post10", "PSA 10")):
                    per = qy.groupby("signal_month")[f"{k}_{h}_mx"].mean().dropna()
                    r[f"{lab} {HLABEL[h]}"] = f"{100 * per.mean():+.1f} [{len(per)}]" if len(per) else ""
            yr_rows.append(r)
        for h in (180, 365):
            per = rows[m & (rows.signal_month.dt.year <= 2024).values].groupby("signal_month")[f"post9_{h}_mx"].mean().dropna()
            t = per.mean() / (per.std(ddof=1) / np.sqrt(len(per))) if len(per) > 2 else np.nan
            yr_rows.append({"sample": name, "year of hype month": f"2021-2024 only, PSA 9 {HLABEL[h]}",
                            "months": len(per), "PSA 9 6m" if h == 180 else "PSA 9 12m": f"{100 * per.mean():+.1f}, {100 * (per > 0).mean():.0f}% up (t {t:.1f})"})
    sec += ["**By year of the hype month (matched excess, mean over months [months])**", "", L.md(pd.DataFrame(yr_rows).fillna("")), ""]
    tr = []
    for name, m in (("any hype", hyp), ("character hype", rows.hype_character.values), ("card type hype", rows.hype_card_type.values),
                    ("any hype, PSA 10 >= $10K", hyp & top), ("no hype", ~hyp)):
        for h in HOLDS:
            q = rows[m & rows[f"closed{h}"].values & rows[f"net{h}"].notna().values]
            if q.empty:
                continue
            perx = q.groupby("signal_month")[f"net{h}_mx"].median().dropna()
            tx = perx.mean() / (perx.std(ddof=1) / np.sqrt(len(perx))) if len(perx) > 2 and perx.std(ddof=1) > 0 else np.nan
            tr.append({"sample": name, "hold": h, "trades": len(q), "months": q.signal_month.nunique(), "hit %": 100 * (q[f"net{h}"] > 0).mean(),
                       "median net %": 100 * q[f"net{h}"].median(), "mean net %": 100 * q[f"net{h}"].mean(),
                       "vs matched unhyped, pts (month medians)": 100 * perx.mean() if len(perx) else np.nan, "t": tx,
                       "% of months ahead": 100 * (perx > 0).mean() if len(perx) else np.nan})
    sec += ["**Trade: buy the PSA 9 at the first PSA 9 sale in (T, T+21], sell at the first PSA 9 sale >= hold days later (within +60), 13% fee; "
            "`vs matched` = the month's median trade minus the median trade in unhyped cards of the same era and tier that month, averaged over months**", "",
            L.md(pd.DataFrame(tr), ".1f"), ""]
    seg_masks = []
    for dim, col in (("card type", "hype_card_type"), ("character", "hype_character"), ("era", "hype_era")):
        for g in (seg[dim] if dim != "character" else chars):
            hot = set(hms[dim][(hms[dim].group == g) & hms[dim].hyped].month)
            if len(hot) >= 3:
                seg_masks.append((f"{dim}: {g}", rows.asset_id.isin(seg[dim][g]).values & rows.signal_month.isin(hot).values))
    if seg_masks:
        sec += ["**Per group with >= 3 hype months: catch-up `gap`, matched**", "", L.md(episode_view(rows, seg_masks, "gap_{h}_mx")), "",
                "**Per group with >= 3 hype months: PSA 9 excess, matched**", "", L.md(episode_view(rows, seg_masks, "post9_{h}_mx")), ""]
    print("\n".join(sec)); lines += sec

    suffix = "" if args.hype == HYPE else f"_hype{int(round(100 * args.hype))}"
    (OUT / f"psa9_hype{suffix}.md").write_text("\n".join(lines))
    allt.to_csv(OUT / f"psa9_hype_cells{suffix}.csv", index=False)
    rows.to_pickle(args.cache_dir / f"psa9_hype_rows{suffix}.pkl")
    print(f"\nwrote {OUT / f'psa9_hype{suffix}.md'}", flush=True)


if __name__ == "__main__":
    main()
