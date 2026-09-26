#!/usr/bin/env python3
"""Does a PSA 9 follow its PSA 10 with a lag of about a month? (2026-09-26)

    analysis/.venv/bin/python analysis/psa9_lag.py             # full run, ~5-8 min first time, ~2 min cached
    analysis/.venv/bin/python analysis/psa9_lag.py --rebuild   # ignore the cleaned-sales cache
    analysis/.venv/bin/python analysis/psa9_lag.py --min-months 18 --min-bucket-sales 2   # stricter liquidity

Sid's proposed "PSA 9 lag" buy signal: when a card's PSA 10 price jumps, buy
the PSA 9 because it will follow a month later. This script is the backtest
that decides whether the effect exists, in both directions (PSA 10 leading
PSA 9 and PSA 9 leading PSA 10). Nothing ships as a signal unless it does.

Data: history/sales/*.csv, PSA grades 10.0 and 9.0, rows with a
skipped_reason (alt.xyz's own RELISTED / NOT_PAID / PENDING flags) excluded,
PWCC mirror copies and outliers removed per (card, grade) with the app's own
rules in history/metrics.py (drop_mirror_copies, drop_outliers). The 604
(card, date, price) tuples that alt.xyz lists under BOTH grades are dropped
from both, so no sale can appear in a PSA 9 and a PSA 10 bucket at once
(the "shared reference" artefact from REVIEW_2026-09-15). PSA 9 sales exist
for the ~33k nightly-scope cards only (backfilled 2026-09-22/23).

Method
  1. Series. Per (card, grade, calendar month): log of the median clean
     price and the sale count. Universe: cards with >= MIN_MONTHS months
     that have a sale in EACH grade over 2021-01..data end (alt.xyz's
     coverage before 2021 is a few hundred sales a year). Returns are
     differences between consecutive months' log medians (missing when
     either month has no sale). Vintage = card year <= 2013.
  2. Lead-lag. corr(r9_t, r10_{t-k}) for k = -3..+3 months (k > 0: PSA 10
     leads), estimated as the slope of standardized OLS so that a two-way
     clustered standard error (by card and by month, Cameron-Gelbach-
     Miller) can be attached; raw and with month fixed effects (each
     series demeaned by its month's cross-sectional mean, so a market-wide
     month cannot masquerade as one grade leading the other). Pooled, by
     era, and as an equal-weight mean of per-card correlations (a handful
     of busy cards cannot carry that one). Same on WEEKLY buckets for the
     busiest cards, lags -8..+8 weeks, to see sub-monthly timing.
     Panel regression: r9_t on r10_{t-1..t-3} and r9_{t-1,t-2} with month
     fixed effects; the reverse; and the contemporaneous spec, because if
     the two grades move in the SAME month there is no lag to trade.
  3. Event study. A PSA 10 "move" at a PSA 10 sale date d: ref10(d) /
     ref10(d-30) - 1 >= EVENT_THRESHOLD, where ref is panel.py's reference
     price (median of the up-to-3 most recent clean sales within 180 days,
     using only sales dated <= d), with >= 3 PSA 10 sales in (d-30, d] and
     at least one in (d-120, d-30] (so the +30% is against a price at most
     four months old). One event per card per EVENT_COOLDOWN days. The
     PSA 9 path is the median log PSA 9 price in the 30-day windows
     [-90,-30) (baseline, before the move), [-30,0] (during the PSA 10
     move), (0,30], (30,60], (60,90] after detection, all relative to the
     baseline; the tradeable part is each post window minus [-30,0].
     Controls: (a) the same card at "flat" dates (|30-day PSA 10 change|
     <= 10%, same liquidity requirement, >= 90 days from any event), and
     (b) the universe: every universe card anchored at every month start,
     whose median path is subtracted to give the excess path.
     Point-in-time: detection uses sales dated <= d only; every PSA 9 entry
     is a sale strictly AFTER d. (The outlier filter's confirmed-run reset
     re-admits a few earlier sales once a run confirms: a mild look-back,
     same caveat as panel.py, not look-ahead into the PSA 9 side.)
  4. Trade. Buy the PSA 9 at the first PSA 9 sale in (d, d+21]; sell at
     the first PSA 9 sale >= 30/60/90 days after the entry (within a further
     60 days, else "no exit", reported); net = exit * (1 - FEE) / entry - 1
     with FEE = 13% sell-side only. Hit rate, mean, median, trades per year,
     2025 and 2026 separately; the same rule at flat dates and on the whole
     universe at month starts is the base rate it has to beat.
  5. Sanity. No shared sales between grades (checked and enforced);
     concentration (top-10 cards' share of events, effect with them
     removed, equal-weight vs pooled); the reverse direction everywhere;
     how often the state "PSA 10 up >= 30% in a month, PSA 9 up < 10%" occurs.

Outputs: tables printed to stdout and written to analysis/out/psa9_lag.md;
cleaned sales cached as a pickle in the scratch dir (--cache-dir).
Checks: analysis/test_psa9_lag.py (planted future sales, estimator checks).

Result (2026-09-26, see README "PSA 9 lag"): the two grades move in the SAME
month (corr 0.074 with month FE) and the one-month cross-correlation is small
and nearly symmetric (0.034 PSA 10 leading, 0.022 PSA 9 leading). After a
>= 30% PSA 10 move the PSA 9 adds +1.1% excess in the next 30 days and
nothing after; the PSA 9 / PSA 10 ratio stays ~10% below its pre-move level
90 days later. The trade's median net return after the 13% fee is negative
in every year but 2026. Not a signal.
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "history"))
from metrics import drop_mirror_copies, drop_outliers  # noqa: E402

OUT = ROOT / "analysis" / "out"
DEFAULT_CACHE = Path("/private/tmp/claude-501/-Users-samaygodika-Documents-Code-scrape-/ed217a88-f0e5-4ab7-90c1-5111d678a5f4/scratchpad/lag")
GRADES = ("10.0", "9.0")
START = pd.Timestamp("2021-01-01")
MIN_MONTHS = 12            # months with >= 1 sale in EACH grade, 2021 on
MIN_BUCKET_SALES = 1       # sales a month needs for its median to count
VINTAGE_MAX_YEAR = 2013
LAGS = range(-3, 4)
WEEKLY_MIN_WEEKS = 100     # weekly cross-correlation: weeks with a sale in each grade
WEEKLY_LAGS = range(-8, 9)
EVENT_THRESHOLDS = (0.30, 0.50)
EVENT_MIN_SALES = 3        # PSA 10 sales in (d-30, d]
EVENT_COOLDOWN = 90        # days before the same card can have another event
FLAT_BAND = 0.10           # |30d change| <= this is a "flat" (control) date
FLAT_SPACING = 180         # days between flat control dates on one card
REF_LOOKBACK = 180         # panel.py: reference = median of <= 3 most recent sales within this many days
REF_FRESH = 120            # the d-30 reference must have a sale in (d-120, d-30]
WINDOWS = {"pre": (-90, -30), "move": (-30, 0), "p30": (0, 30), "p60": (30, 60), "p90": (60, 90)}
ENTRY_DAYS = 21
HOLDS = (30, 60, 90)
EXIT_GRACE = 60
FEE = 0.13
RET_CLIP = np.log(3.0)     # bucket-to-bucket log returns clipped at a x3 move: the outlier filter deliberately
                           # admits two consecutive high sales, so a few mislabeled runs survive it (r up to +10 in logs)
DAY = np.timedelta64(1, "D")


# ---------------------------------------------------------------- data

def load_clean_sales(store=ROOT / "history", cache_dir=DEFAULT_CACHE, rebuild=False):
    """Clean PSA 10 and PSA 9 sales: [asset_id, grade, date, price, source]."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / "clean_sales.pkl"
    if cache.exists() and not rebuild:
        return pd.read_pickle(cache)
    t0 = time.time()
    frames = []
    for p in sorted((store / "sales").glob("*.csv")):
        frames.append(pd.read_csv(p, usecols=["asset_id", "date", "price", "grading_company", "grade", "source", "url", "skipped_reason"],
                                  dtype={"grade": str, "skipped_reason": "string", "source": "string", "url": "string"}))
    s = pd.concat(frames, ignore_index=True)
    s = s[(s.grading_company == "PSA") & s.grade.isin(GRADES) & s.skipped_reason.isna()]
    s["price"] = pd.to_numeric(s.price, errors="coerce")
    s = s[s.price > 0]
    s["date"] = pd.to_datetime(s.date, errors="coerce")
    s = s.dropna(subset=["date"])
    # a sale recorded under both grades cannot be allowed to sit in both series
    both = s.groupby(["asset_id", "date", "price"]).grade.transform("nunique") > 1
    n_both = int(both.sum())
    s = s[~both].sort_values(["asset_id", "grade", "date"]).reset_index(drop=True)
    keep = np.zeros(len(s), dtype=bool)
    n_mirror = n_out = 0
    for (aid, g), grp in s.groupby(["asset_id", "grade"], sort=False):
        rows = list(zip(grp.date.dt.date, grp.price.astype(float), grp.source.fillna(""), grp.url.fillna("")))
        rows2, dropped = drop_mirror_copies(rows)
        n_mirror += dropped
        clean, dropped = drop_outliers([(d, p, src) for d, p, src, _ in rows2])
        n_out += dropped
        kept = set((d, p) for d, p, _ in clean)
        # mirror copies: keep exactly as many rows per (date, price) as survived
        counts = {}
        for d, p, _, _ in rows2:
            counts[(d, p)] = counts.get((d, p), 0) + 1
        for i, (d, p, _, _) in zip(grp.index, rows):
            if (d, p) in kept and counts.get((d, p), 0) > 0:
                keep[i] = True
                counts[(d, p)] -= 1
    s = s[keep][["asset_id", "grade", "date", "price", "source"]].reset_index(drop=True)
    s.attrs.update(n_both=n_both, n_mirror=n_mirror, n_outliers=n_out)
    print(f"cleaned {len(s):,} sales in {time.time() - t0:.0f}s: dropped {n_both} rows listed under both grades, "
          f"{n_mirror} PWCC mirror copies, {n_out} outliers", flush=True)
    pd.to_pickle(s, cache)
    return s


def load_assets():
    a = pd.read_csv(ROOT / "history" / "assets.csv", usecols=["asset_id", "card_name", "year", "set", "subject", "variety"], dtype=str)
    a["year"] = pd.to_numeric(a.year, errors="coerce")
    a["vintage"] = a.year <= VINTAGE_MAX_YEAR
    a["japanese"] = a.card_name.str.contains("Japanese", case=False, na=False) | a.set.str.contains("Japanese", case=False, na=False)
    return a.set_index("asset_id")


def monthly_series(sales, min_bucket_sales=MIN_BUCKET_SALES):
    """One row per (card, month) with m10, n10, m9, n9 (log median price, sale count)."""
    s = sales[sales.date >= START].copy()
    s["month"] = s.date.dt.to_period("M")
    s["lp"] = np.log(s.price)
    agg = s.groupby(["asset_id", "grade", "month"]).lp.agg(["median", "size"]).unstack("grade")
    m = pd.DataFrame({"m10": agg[("median", "10.0")], "n10": agg[("size", "10.0")].fillna(0).astype(int),
                      "m9": agg[("median", "9.0")], "n9": agg[("size", "9.0")].fillna(0).astype(int)})
    m.loc[m.n10 < min_bucket_sales, "m10"] = np.nan
    m.loc[m.n9 < min_bucket_sales, "m9"] = np.nan
    return m.reset_index()


def select_universe(monthly, min_months=MIN_MONTHS):
    ok = monthly.groupby("asset_id").agg(k10=("m10", "count"), k9=("m9", "count"))
    return ok[(ok.k10 >= min_months) & (ok.k9 >= min_months)].index


def monthly_returns(monthly):
    """Consecutive-month log returns per grade, on a complete month grid per card."""
    m = monthly.set_index(["asset_id", "month"]).sort_index()
    full = m.unstack("month").stack("month", future_stack=True).sort_index()   # complete grid within each card's span
    g = full.groupby(level=0)
    n_clip = 0
    for c in ("m10", "m9"):
        r = g[c].diff()   # NaN unless both t and t-1 have a median
        n_clip += int((r.abs() > RET_CLIP).sum())
        full["r" + c[1:]] = r.clip(-RET_CLIP, RET_CLIP)
    out = full.reset_index()
    out.attrs["n_clipped"] = n_clip
    return out


# ---------------------------------------------------------------- statistics

def ols_twoway(X, y, c1, c2):
    """OLS with two-way clustered SEs (clusters c1, c2; their intersection is one obs).
    Returns beta, se."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    xtx_inv = np.linalg.pinv(X.T @ X)
    beta = xtx_inv @ X.T @ y
    e = y - X @ beta
    xe = X * e[:, None]

    def meat(cl):
        S = pd.DataFrame(xe).groupby(np.asarray(cl)).sum().values
        return S.T @ S
    M = meat(c1) + meat(c2) - xe.T @ xe
    V = xtx_inv @ M @ xtx_inv
    return beta, np.sqrt(np.maximum(np.diag(V), 0))


def lagged_corr(ret, lags, demean, min_pairs=1):
    """corr(r9_t, r10_{t-k}) per lag with two-way clustered SE; k > 0 means PSA 10 leads."""
    df = ret[["asset_id", "month", "r10", "r9"]].copy()
    if demean:
        for c in ("r10", "r9"):
            df[c] = df[c] - df.groupby("month")[c].transform("mean")
    g = df.groupby("asset_id")
    rows = []
    for k in lags:
        x = g.r10.shift(k)          # r10 at t-k (grid is complete per card, so shift = calendar lag)
        d = pd.DataFrame({"asset_id": df.asset_id, "month": df.month, "y": df.r9, "x": x}).dropna()
        if len(d) < 50:
            continue
        ys = (d.y - d.y.mean()) / d.y.std()
        xs = (d.x - d.x.mean()) / d.x.std()
        beta, se = ols_twoway(np.column_stack([np.ones(len(d)), xs]), ys, d.asset_id.values, d.month.astype(str).values)
        per_card = d.groupby("asset_id").apply(lambda q: q.x.corr(q.y) if len(q) >= 24 else np.nan, include_groups=False).dropna()
        rows.append({"lag": k, "corr": beta[1], "se": se[1], "t": beta[1] / se[1] if se[1] > 0 else np.nan,
                     "n_pairs": len(d), "n_cards": d.asset_id.nunique(),
                     "eq_weight_corr": per_card.mean(), "eq_weight_cards": len(per_card)})
    return pd.DataFrame(rows)


def panel_regression(ret, y_col, x_col, x_lags=(1, 2, 3), own_lags=(1, 2), contemporaneous=False):
    """y_t on x_{t-k} (k in x_lags), own lags, month FE (within-month demeaning); two-way clustered SE."""
    df = ret[["asset_id", "month", "r10", "r9"]].copy()
    g = df.groupby("asset_id")
    cols = {}
    if contemporaneous:
        cols[f"{x_col}[t]"] = df[x_col]
    for k in x_lags:
        cols[f"{x_col}[t-{k}]"] = g[x_col].shift(k)
    for k in own_lags:
        cols[f"{y_col}[t-{k}]"] = g[y_col].shift(k)
    d = pd.DataFrame({"asset_id": df.asset_id, "month": df.month, "y": df[y_col], **cols}).dropna()
    names = list(cols)
    # month fixed effects: demean everything within month
    dm = d[["y"] + names] - d.groupby("month")[["y"] + names].transform("mean")
    beta, se = ols_twoway(dm[names].values, dm.y.values, d.asset_id.values, d.month.astype(str).values)
    out = pd.DataFrame({"regressor": names, "coef": beta, "se": se, "t": beta / np.where(se > 0, se, np.nan)})
    out.attrs.update(n=len(d), n_cards=d.asset_id.nunique(), n_months=d.month.nunique())
    return out


def weekly_corr(sales, universe, assets, lags=WEEKLY_LAGS, min_weeks=WEEKLY_MIN_WEEKS):
    s = sales[(sales.date >= START) & sales.asset_id.isin(universe)].copy()
    s["week"] = s.date.dt.to_period("W-SUN")
    s["lp"] = np.log(s.price)
    agg = s.groupby(["asset_id", "grade", "week"]).lp.median().unstack("grade")
    agg.columns = ["m" + c.split(".")[0] for c in agg.columns]
    k = agg.groupby(level=0).count()
    busy = k[(k.m10 >= min_weeks) & (k.m9 >= min_weeks)].index
    agg = agg[agg.index.get_level_values(0).isin(busy)]
    full = agg.unstack("week").stack("week", future_stack=True).sort_index()
    g = full.groupby(level=0)
    full["r10"], full["r9"] = g.m10.diff().clip(-RET_CLIP, RET_CLIP), g.m9.diff().clip(-RET_CLIP, RET_CLIP)
    ret = full.reset_index().rename(columns={"week": "month"})   # lagged_corr demeans by the "month" column
    res = lagged_corr(ret, lags, demean=True)
    res.attrs["n_cards"] = len(busy)
    return res


# ---------------------------------------------------------------- events

def ref_at(d, p, idx):
    """panel.py's reference price at sale index idx (median of the <= 3 most recent
    sales within REF_LOOKBACK days ending at idx), vectorized over idx. NaN where idx < 0."""
    idx = np.asarray(idx)
    out = np.full(len(idx), np.nan)
    ok = idx >= 0
    if not ok.any():
        return out
    i = idx[ok]
    lo = np.searchsorted(d, d[i] - REF_LOOKBACK * DAY, side="right")
    k = np.minimum(i + 1 - lo, 3)
    p0 = p[i]
    p1 = np.where(k >= 2, p[np.maximum(i - 1, 0)], np.nan)
    p2 = np.where(k >= 3, p[np.maximum(i - 2, 0)], np.nan)
    stack = np.column_stack([p0, p1, p2])
    out[ok] = np.nanmedian(stack, axis=1)
    return out


def window_median(d, lp, anchor, lo_off, hi_off):
    """median of lp for sales with anchor+lo_off < date <= anchor+hi_off (lo inclusive when lo_off < 0 and hi_off <= 0 handled by caller)."""
    lo = np.searchsorted(d, anchor + lo_off * DAY, side="right")
    hi = np.searchsorted(d, anchor + hi_off * DAY, side="right")
    if hi <= lo:
        return np.nan, 0
    return float(np.median(lp[lo:hi])), int(hi - lo)


def path_at(d9, lp9, d10, lp10, anchor):
    row = {}
    for name, (a, b) in WINDOWS.items():
        row[f"m9_{name}"], row[f"n9_{name}"] = window_median(d9, lp9, anchor, a, b)
        row[f"m10_{name}"], row[f"n10_{name}"] = window_median(d10, lp10, anchor, a, b)
    return row


def trade_at(d9, p9, anchor):
    """Buy at the first PSA 9 sale in (anchor, anchor+ENTRY_DAYS]; sell at the first sale >= entry + h."""
    row = {}
    i = np.searchsorted(d9, anchor, side="right")
    if i >= len(d9) or d9[i] > anchor + ENTRY_DAYS * DAY:
        row["entry"] = np.nan
        return row
    row["entry"], row["entry_date"] = float(p9[i]), d9[i]
    row["entry_lag_days"] = int((d9[i] - anchor) / DAY)
    for h in HOLDS:
        j = np.searchsorted(d9, d9[i] + h * DAY, side="left")
        if j < len(d9) and d9[j] <= d9[i] + (h + EXIT_GRACE) * DAY:
            row[f"exit{h}"] = float(p9[j])
            row[f"net{h}"] = p9[j] * (1 - FEE) / p9[i] - 1
        else:
            row[f"exit{h}"] = np.nan
            row[f"net{h}"] = np.nan
    return row


def scan_events(sales, universe, thresholds=EVENT_THRESHOLDS):
    """Per card: PSA 10 move events at each threshold, flat control dates, and month-start anchors."""
    s = sales[sales.asset_id.isin(universe)]
    by = {(aid, g): grp for (aid, g), grp in s.groupby(["asset_id", "grade"], sort=False)}
    data_end = sales.date.max().to_datetime64()
    months = pd.date_range(START, data_end - pd.Timedelta(days=90), freq="MS").values.astype("datetime64[D]")
    events, flats, grid = [], [], []
    for aid in universe:
        g10, g9 = by.get((aid, "10.0")), by.get((aid, "9.0"))
        if g10 is None or g9 is None:
            continue
        d10 = g10.date.values.astype("datetime64[D]")
        p10 = g10.price.values.astype(float)
        lp10 = np.log(p10)
        d9 = g9.date.values.astype("datetime64[D]")
        p9 = g9.price.values.astype(float)
        lp9 = np.log(p9)
        n = len(d10)
        idx = np.arange(n)
        ref_now = ref_at(d10, p10, idx)
        hi_prev = np.searchsorted(d10, d10 - 30 * DAY, side="right")            # sales <= d-30
        lo_fresh = np.searchsorted(d10, d10 - REF_FRESH * DAY, side="right")     # sales <= d-120
        ref_prev = ref_at(d10, p10, hi_prev - 1)
        fresh = hi_prev - lo_fresh >= 1
        n30 = idx + 1 - hi_prev
        mom = ref_now / ref_prev - 1
        in_range = d10 >= np.datetime64(START.date())
        base = in_range & fresh & (n30 >= EVENT_MIN_SALES) & np.isfinite(mom)
        # PSA 9 own 30-day change at d (has the PSA 9 moved yet?)
        ref9_now = ref_at(d9, p9, np.searchsorted(d9, d10, side="right") - 1)
        ref9_prev = ref_at(d9, p9, np.searchsorted(d9, d10 - 30 * DAY, side="right") - 1)
        mom9 = ref9_now / ref9_prev - 1
        event_days = []
        for thr in thresholds:
            cand = np.flatnonzero(base & (mom >= thr))
            last = None
            for j in cand:
                if last is not None and d10[j] < last + EVENT_COOLDOWN * DAY:
                    continue
                last = d10[j]
                if thr == thresholds[0]:
                    event_days.append(d10[j])
                row = {"asset_id": aid, "thr": thr, "date": d10[j], "mom10": mom[j], "mom9_at_d": mom9[j], "n30": int(n30[j]),
                       "ref10": ref_now[j], "ref9": ref9_now[j]}
                row.update(path_at(d9, lp9, d10, lp10, d10[j]))
                row.update(trade_at(d9, p9, d10[j]))
                events.append(row)
        # flat controls: same card, |mom| <= band, away from any (first-threshold) event
        cand = np.flatnonzero(base & (np.abs(mom) <= FLAT_BAND))
        ev = np.array(event_days, dtype="datetime64[D]") if event_days else np.array([], dtype="datetime64[D]")
        last = None
        for j in cand:
            if last is not None and d10[j] < last + FLAT_SPACING * DAY:
                continue
            if len(ev) and np.min(np.abs((ev - d10[j]) / DAY)) < EVENT_COOLDOWN:
                continue
            last = d10[j]
            row = {"asset_id": aid, "date": d10[j], "mom10": mom[j], "mom9_at_d": mom9[j], "n30": int(n30[j])}
            row.update(path_at(d9, lp9, d10, lp10, d10[j]))
            row.update(trade_at(d9, p9, d10[j]))
            flats.append(row)
        # universe grid: every month start (market control and trade base rate)
        for T in months:
            row = {"asset_id": aid, "date": T}
            row.update(path_at(d9, lp9, d10, lp10, T))
            row.update(trade_at(d9, p9, T))
            grid.append(row)
    return pd.DataFrame(events), pd.DataFrame(flats), pd.DataFrame(grid)


PATH_PREFIXES = ("d9_", "d10_", "post9_", "post10_", "inc9_", "inc10_", "ratio_")


def add_paths(df):
    """Relative paths: each window minus the pre-move baseline; post windows minus the move
    window; month-to-month increments after detection; and the PSA 9 / PSA 10 log ratio
    relative to its pre-move level (does the gap the PSA 10 move opened close again?)."""
    for g in ("9", "10"):
        for name in ("move", "p30", "p60", "p90"):
            df[f"d{g}_{name}"] = df[f"m{g}_{name}"] - df[f"m{g}_pre"]
        for name in ("p30", "p60", "p90"):
            df[f"post{g}_{name}"] = df[f"m{g}_{name}"] - df[f"m{g}_move"]
        df[f"inc{g}_p60"] = df[f"m{g}_p60"] - df[f"m{g}_p30"]
        df[f"inc{g}_p90"] = df[f"m{g}_p90"] - df[f"m{g}_p60"]
    ratio_pre = df.m9_pre - df.m10_pre
    for name in ("move", "p30", "p60", "p90"):
        df[f"ratio_{name}"] = (df[f"m9_{name}"] - df[f"m10_{name}"]) - ratio_pre
    df["month"] = pd.to_datetime(df.date).dt.to_period("M")
    return df


def market_control(grid):
    """Universe median of every relative-path column per month-start (the cross-section that month)."""
    cols = [c for c in grid.columns if c.startswith(PATH_PREFIXES)]
    return grid.groupby("month")[cols].median()


def excess_paths(df, ctrl):
    cols = list(ctrl.columns)
    ex = df[cols].values - ctrl.reindex(df.month).values
    out = df.copy()
    for i, c in enumerate(cols):
        out[c + "_ex"] = ex[:, i]
    return out


def summarize_paths(df, label, cols):
    row = {"sample": label, "n": len(df), "cards": df.asset_id.nunique()}
    for c in cols:
        v = df[c].dropna()
        row[c] = v.mean()
        row[c + "_med"] = v.median()
        if len(v) > 10:
            beta, se = ols_twoway(np.ones((len(v), 1)), v.values, df.loc[v.index, "asset_id"].values, df.loc[v.index, "month"].astype(str).values)
            row[c + "_t"] = beta[0] / se[0] if se[0] > 0 else np.nan
        else:
            row[c + "_t"] = np.nan
    return row


def closed(df, h, data_end):
    """Signals whose exit window for hold h has fully closed by the data end (no right-censoring)."""
    return df[pd.to_datetime(df.date) + pd.Timedelta(days=ENTRY_DAYS + h + EXIT_GRACE) <= data_end]


def trade_table(df, label, data_end, holds=HOLDS):
    rows = []
    for h in holds:
        d = closed(df, h, data_end)
        entered = d[d.entry.notna()]
        years = (pd.to_datetime(d.date).dt.year.nunique()) if len(d) else 1
        v = entered[f"net{h}"]
        got = v.notna()
        rows.append({"sample": label, "hold": h, "signals": len(d), "entered": len(entered), "with exit": int(got.sum()),
                     "no exit %": 100 * (1 - got.mean()) if len(entered) else np.nan,
                     "hit %": 100 * (v[got] > 0).mean() if got.any() else np.nan,
                     "mean net %": 100 * v[got].mean() if got.any() else np.nan,
                     "median net %": 100 * v[got].median() if got.any() else np.nan,
                     "trades/yr": got.sum() / years})
    return pd.DataFrame(rows)


def trade_by_year(df, label, data_end, h=60):
    d = closed(df, h, data_end)
    d = d[d.entry.notna()].copy()
    d["year"] = pd.to_datetime(d.date).dt.year
    out = d.groupby("year").apply(lambda q: pd.Series({"trades": q[f"net{h}"].notna().sum(), "no exit %": 100 * q[f"net{h}"].isna().mean(),
                                                       "hit %": 100 * (q[f"net{h}"] > 0).mean() if q[f"net{h}"].notna().any() else np.nan,
                                                       "mean net %": 100 * q[f"net{h}"].mean(), "median net %": 100 * q[f"net{h}"].median()}), include_groups=False)
    out.insert(0, "sample", label)
    return out.reset_index()


def state_table(ret):
    """Monthly buckets: next-month PSA 9 / PSA 10 excess return conditional on this month's state."""
    b = ret.dropna(subset=["r10", "r9"]).copy()
    g = b.groupby("asset_id")
    b["nxt9"], b["nxt10"] = g.r9.shift(-1), g.r10.shift(-1)
    for c in ("nxt9", "nxt10"):
        b[c + "x"] = b[c] - b.groupby("month")[c].transform("mean")
    up10, flat9 = b.r10 >= np.log(1.30), b.r9 < np.log(1.10)
    rows = []
    for label, m in (("all card-months", np.ones(len(b), bool)), ("PSA 10 up >= 30%", up10), ("PSA 9 up < 10%", flat9),
                     ("PSA 10 up >= 30% and PSA 9 up < 10% (the 'not yet moved' state)", up10 & flat9),
                     ("PSA 10 up >= 30% and PSA 9 up >= 10% (both moved)", up10 & ~flat9),
                     ("PSA 10 up < 30% and PSA 9 up < 10% (plain low PSA 9 bucket)", ~up10 & flat9)):
        x = b[m]
        rows.append({"state this month": label, "card-months": len(x), "share %": 100 * len(x) / len(b),
                     "next-month PSA 9 excess, mean %": 100 * x.nxt9x.mean(), "median %": 100 * x.nxt9x.median(),
                     "next-month PSA 10 excess, mean %": 100 * x.nxt10x.mean()})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- report

def md(df, floatfmt=".3f"):
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_integer_dtype(df[c]) or c in ("lag", "n_pairs", "n_cards", "eq_weight_cards", "n", "cards", "hold", "signals", "entered", "with exit"):
            if df[c].notna().all():
                df[c] = df[c].astype(int).astype(str)
    return df.to_markdown(index=False, floatfmt=floatfmt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="ignore the cleaned-sales cache")
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--min-months", type=int, default=MIN_MONTHS)
    ap.add_argument("--min-bucket-sales", type=int, default=MIN_BUCKET_SALES)
    ap.add_argument("--english", action="store_true", help="drop Japanese-language cards")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    sales = load_clean_sales(cache_dir=args.cache_dir, rebuild=args.rebuild)
    assets = load_assets()
    if args.english:
        sales = sales[~sales.asset_id.map(assets.japanese).fillna(False).astype(bool)]
    print(f"{len(sales):,} clean sales ({(sales.grade == '10.0').sum():,} PSA 10, {(sales.grade == '9.0').sum():,} PSA 9), data through {sales.date.max().date()}")
    lines = [f"# PSA 9 lag study ({pd.Timestamp.today().date()})", "",
             f"Clean sales: {(sales.grade == '10.0').sum():,} PSA 10 on {sales[sales.grade == '10.0'].asset_id.nunique():,} cards, "
             f"{(sales.grade == '9.0').sum():,} PSA 9 on {sales[sales.grade == '9.0'].asset_id.nunique():,} cards, through {sales.date.max().date()}. "
             f"Universe: cards with >= {args.min_months} months having a sale in each grade since {START.date()} (bucket needs >= {args.min_bucket_sales} sale(s))."
             + (" English only." if args.english else ""), ""]

    # 1. series and universe
    monthly = monthly_series(sales, args.min_bucket_sales)
    universe = select_universe(monthly, args.min_months)
    monthly = monthly[monthly.asset_id.isin(universe)]
    ret = monthly_returns(monthly)
    ret["vintage"] = ret.asset_id.map(assets.vintage).fillna(False).astype(bool)
    u = pd.DataFrame({"asset_id": universe})
    u["vintage"] = u.asset_id.map(assets.vintage).fillna(False).astype(bool)
    pairs = ret.dropna(subset=["r10", "r9"])
    uni_tbl = pd.DataFrame([
        {"era": "all", "cards": len(u), "card-months with both medians": int(monthly.dropna(subset=["m10", "m9"]).shape[0]),
         "card-months with both returns": len(pairs), "median PSA9/PSA10 price ratio": float(np.exp((monthly.m9 - monthly.m10).median()))},
        {"era": "vintage (<= 2013)", "cards": int(u.vintage.sum()), "card-months with both medians": int(monthly[monthly.asset_id.isin(u[u.vintage].asset_id)].dropna(subset=["m10", "m9"]).shape[0]),
         "card-months with both returns": int(pairs.vintage.sum()), "median PSA9/PSA10 price ratio": float(np.exp((monthly.m9 - monthly.m10)[monthly.asset_id.isin(u[u.vintage].asset_id)].median()))},
        {"era": "modern (> 2013)", "cards": int((~u.vintage).sum()), "card-months with both medians": int(monthly[monthly.asset_id.isin(u[~u.vintage].asset_id)].dropna(subset=["m10", "m9"]).shape[0]),
         "card-months with both returns": int((~pairs.vintage).sum()), "median PSA9/PSA10 price ratio": float(np.exp((monthly.m9 - monthly.m10)[monthly.asset_id.isin(u[~u.vintage].asset_id)].median()))},
    ])
    clip_line = f"Monthly bucket returns clipped at ±log(3): {ret.attrs.get('n_clipped', 0)} of {int(ret.r10.notna().sum() + ret.r9.notna().sum()):,} returns touched."
    print("\n## 1. Universe\n"); print(md(uni_tbl)); print(clip_line)
    lines += ["## 1. Universe", "", md(uni_tbl), "", clip_line, ""]

    # 2. lead-lag
    print("\n## 2. Cross-correlation corr(r9_t, r10_{t-k}), monthly; k > 0 = PSA 10 leads\n")
    cc_rows = []
    for label, sub in (("all", ret), ("vintage", ret[ret.vintage]), ("modern", ret[~ret.vintage])):
        for demean in (False, True):
            cc = lagged_corr(sub, LAGS, demean)
            cc.insert(0, "month FE", "yes" if demean else "no")
            cc.insert(0, "era", label)
            cc_rows.append(cc)
    cc_all = pd.concat(cc_rows, ignore_index=True)
    show = cc_all[["era", "month FE", "lag", "corr", "se", "t", "n_pairs", "n_cards", "eq_weight_corr", "eq_weight_cards"]]
    print(md(show))
    lines += ["## 2. Lead-lag: corr(r9_t, r10_{t-k}) on monthly log-median returns (k > 0: PSA 10 leads, k < 0: PSA 9 leads)", "",
              "SE two-way clustered by card and month. `eq_weight_corr` = mean of per-card correlations over cards with >= 24 pairs.", "", md(show), ""]

    wk = weekly_corr(sales, universe, assets)
    print(f"\n## 2b. Weekly cross-correlation, month FE (week FE), {wk.attrs.get('n_cards')} cards with >= {WEEKLY_MIN_WEEKS} weeks in each grade\n")
    print(md(wk))
    lines += [f"## 2b. Weekly buckets, week fixed effects, {wk.attrs.get('n_cards')} cards with >= {WEEKLY_MIN_WEEKS} weeks having a sale in each grade (k in weeks, k > 0: PSA 10 leads)", "", md(wk), ""]

    print("\n## 2c. Panel regressions with month fixed effects\n")
    lines += ["## 2c. Panel regressions (month fixed effects, two-way clustered SE)", ""]
    for label, sub in (("all", ret), ("vintage", ret[ret.vintage]), ("modern", ret[~ret.vintage])):
        for title, y, x, contemp in ((f"PSA 9 return on lagged PSA 10 [{label}]", "r9", "r10", False),
                                     (f"PSA 9 return on contemporaneous + lagged PSA 10 [{label}]", "r9", "r10", True),
                                     (f"PSA 10 return on lagged PSA 9 [{label}]", "r10", "r9", False)):
            reg = panel_regression(sub, y, x, contemporaneous=contemp)
            hdr = f"**{title}** — n = {reg.attrs['n']:,} card-months, {reg.attrs['n_cards']:,} cards, {reg.attrs['n_months']} months"
            print(hdr); print(md(reg)); print()
            lines += [hdr, "", md(reg), ""]

    # 3. events
    print("\n## 3. Event study\n")
    t1 = time.time()
    data_end = sales.date.max()
    events, flats, grid = scan_events(sales, universe)
    print(f"scanned events in {time.time() - t1:.0f}s: {len(events)} event rows, {len(flats)} flat controls, {len(grid)} grid anchors")
    for df in (events, flats, grid):
        add_paths(df)
        df["vintage"] = df.asset_id.map(assets.vintage).fillna(False).astype(bool)
    ctrl = market_control(grid)
    events, flats, grid = excess_paths(events, ctrl), excess_paths(flats, ctrl), excess_paths(grid, ctrl)
    base_cols = ["d9_move", "d9_p30", "d9_p60", "d9_p90", "post9_p30", "post9_p60", "post9_p90", "inc9_p60", "inc9_p90",
                 "d10_move", "post10_p30", "post10_p60", "post10_p90", "ratio_move", "ratio_p30", "ratio_p60", "ratio_p90"]
    all_cols = base_cols + [c + "_ex" for c in base_cols]
    rows = []
    for thr in EVENT_THRESHOLDS:
        e = events[events.thr == thr]
        rows.append(summarize_paths(e, f"PSA10 move >= {thr:.0%}", all_cols))
        rows.append(summarize_paths(e[e.vintage], f"  vintage, >= {thr:.0%}", all_cols))
        rows.append(summarize_paths(e[~e.vintage], f"  modern, >= {thr:.0%}", all_cols))
        rows.append(summarize_paths(e[e.mom9_at_d < 0.10], f"  PSA9 not yet up 10%, >= {thr:.0%}", all_cols))
        rows.append(summarize_paths(e[e.mom9_at_d >= 0.10], f"  PSA9 already up 10%, >= {thr:.0%}", all_cols))
    rows.append(summarize_paths(flats, "flat control (same cards, |move| <= 10%)", all_cols))
    rows.append(summarize_paths(flats[flats.mom9_at_d < 0.10], "  flat, PSA9 not yet up 10%", all_cols))
    rows.append(summarize_paths(flats[flats.mom9_at_d >= 0.10], "  flat, PSA9 already up 10%", all_cols))
    rows.append(summarize_paths(grid, "universe at month starts", all_cols))
    ev_tbl = pd.DataFrame(rows)

    def path_view(tbl, cols):
        v = tbl[["sample", "n", "cards"]].copy()
        for c in cols:
            v[c] = tbl[c].map(lambda x: f"{100 * x:+.1f}") + " (" + tbl[c + "_med"].map(lambda x: f"{100 * x:+.1f}") + ", t=" + tbl[c + "_t"].map(lambda x: f"{x:.1f}") + ")"
        return v
    sec = ["## 3. Event study: log price path in 30-day windows around a PSA 10 move (detection date d)", "",
           "Baseline = median of the card's sales in [d-90, d-30). `move` = [d-30, d] (the PSA 10 move itself), `p30/p60/p90` = (d, d+30], (d+30, d+60], (d+60, d+90]. "
           "Cells: mean % (median %, t with two-way clustered SE). `d*` = window minus baseline; `post*` = window minus the `move` window (what a buyer after detection could capture); "
           "`inc*` = window minus the previous window; `ratio_*` = PSA 9 / PSA 10 log ratio minus its baseline level; `_ex` = minus the universe median that month.", "",
           "**PSA 9 path relative to baseline**", "", md(path_view(ev_tbl, ["d9_move", "d9_p30", "d9_p60", "d9_p90"])), "",
           "**PSA 9 drift after detection (window minus move window), excess over the universe**", "",
           md(path_view(ev_tbl, ["post9_p30_ex", "post9_p60_ex", "post9_p90_ex"])), "",
           "**PSA 9 month-to-month increments after detection, excess: is there a second-month catch-up?**", "",
           md(path_view(ev_tbl, ["inc9_p60_ex", "inc9_p90_ex"])), "",
           "**PSA 9 / PSA 10 log ratio relative to baseline (raw): does the gap opened by the PSA 10 move close?**", "",
           md(path_view(ev_tbl, ["ratio_move", "ratio_p30", "ratio_p60", "ratio_p90"])), "",
           "**PSA 10 itself: the move (raw) and what it did afterwards (excess)**", "",
           md(path_view(ev_tbl, ["d10_move", "post10_p30_ex", "post10_p60_ex", "post10_p90_ex"])), ""]
    cov = events[events.thr == EVENT_THRESHOLDS[0]][[f"n9_{w}" for w in WINDOWS]].gt(0).mean().mul(100).round(1)
    sec += ["Share of >= 30% events with >= 1 PSA 9 sale in each window: " + ", ".join(f"{w} {cov[f'n9_{w}']:.0f}%" for w in WINDOWS) + ".", ""]
    e30 = events[events.thr == EVENT_THRESHOLDS[0]].copy()
    e30["year"] = pd.to_datetime(e30.date).dt.year
    by_year = e30.groupby("year").agg(events=("asset_id", "size"), cards=("asset_id", "nunique"),
                                      psa9_post60_mean=("post9_p60", "mean"), psa9_post60_ex_mean=("post9_p60_ex", "mean"), psa9_post60_ex_median=("post9_p60_ex", "median"),
                                      psa9_inc60_ex_mean=("inc9_p60_ex", "mean"), psa10_post60_ex_mean=("post10_p60_ex", "mean"),
                                      share_psa9_not_yet_up10=("mom9_at_d", lambda v: (v < 0.10).mean())).reset_index()
    sec += ["**By year, >= 30% events (60-day post drift; `_ex` = excess over the universe)**", "", md(by_year), ""]
    print("\n".join(sec))
    lines += sec

    # 4. trade
    tt = pd.concat([trade_table(events[events.thr == thr], f"PSA10 move >= {thr:.0%}", data_end) for thr in EVENT_THRESHOLDS]
                   + [trade_table(events[(events.thr == 0.30) & (events.mom9_at_d < 0.10)], "  >= 30%, PSA9 not yet up 10%", data_end),
                      trade_table(events[(events.thr == 0.30) & events.vintage], "  >= 30%, vintage", data_end),
                      trade_table(events[(events.thr == 0.30) & ~events.vintage], "  >= 30%, modern", data_end),
                      trade_table(events[(events.thr == 0.30) & (events.entry >= 100)], "  >= 30%, PSA 9 entry >= $100", data_end),
                      trade_table(flats, "flat control (same cards)", data_end), trade_table(grid, "universe at month starts", data_end),
                      trade_table(grid[grid.entry >= 100], "  universe, PSA 9 entry >= $100", data_end)], ignore_index=True)
    ty = pd.concat([trade_by_year(events[events.thr == 0.30], "PSA10 move >= 30%", data_end), trade_by_year(flats, "flat control", data_end),
                    trade_by_year(grid, "universe", data_end)], ignore_index=True)
    e = events[(events.thr == 0.30) & events.entry.notna()]
    sec = ["## 4. Trade: buy the PSA 9 at the first PSA 9 sale in (d, d+21], sell at the first PSA 9 sale >= h days after entry (within h+60), net of a 13% sell-side fee", "",
           f"Only signals whose exit window closed before the data end ({data_end.date()}) are counted, so 2026 is not right-censored.", "", md(tt, ".1f"), "",
           "**By year, 60-day hold**", "", md(ty, ".1f"), "",
           f"Entry realism (>= 30% events): {100 * events[events.thr == 0.30].entry.notna().mean():.0f}% had a PSA 9 sale within 21 days; median entry lag {e.entry_lag_days.median():.0f} days.", ""]
    print("\n".join(sec))
    lines += sec

    # 5. sanity
    san = ["## 5. Sanity checks", ""]
    san.append(f"- Shared sales: {sales.attrs.get('n_both', 'n/a')} (card, date, price) rows listed under both grades were dropped before anything else; the two grades' medians share no sale.")
    top = e30.asset_id.value_counts()
    top10 = top.head(10)
    rest = e30[~e30.asset_id.isin(top10.index)]
    san.append(f"- Concentration: {len(top)} cards produced {len(e30)} >= 30% events; the top 10 cards account for {100 * top10.sum() / len(e30):.1f}% of events. "
               f"Excess 60-day PSA 9 drift without them: mean {100 * rest.post9_p60_ex.mean():+.1f}%, median {100 * rest.post9_p60_ex.median():+.1f}% (with: {100 * e30.post9_p60_ex.mean():+.1f}% / {100 * e30.post9_p60_ex.median():+.1f}%).")
    pc = e30.groupby("asset_id").post9_p60_ex.mean().dropna()
    san.append(f"- Equal-weight over cards: mean of per-card mean excess 60-day PSA 9 drift {100 * pc.mean():+.1f}% (n = {len(pc)} cards), median {100 * pc.median():+.1f}%; share of cards with a positive mean {100 * (pc > 0).mean():.0f}%.")
    e_state = events[events.thr == 0.30]
    san.append(f"- At detection of a >= 30% PSA 10 move, the PSA 9's own 30-day change was < 10% in {100 * (e_state.mom9_at_d < 0.10).mean():.0f}% of events (no PSA 9 reference: {100 * e_state.mom9_at_d.isna().mean():.0f}%); median PSA 9 change at detection {100 * e_state.mom9_at_d.median():+.1f}%. "
               "But conditioning on a low PSA 9 bucket selects a noisy-low price that reverts on its own: compare the `PSA9 not yet up` rows against the `flat, PSA9 not yet up` row above, and the `d9_*` (vs baseline) columns against `post9_*` (vs the move window) for the same rows.")
    san.append("- State frequency and what follows it (monthly buckets, card-months with both returns; excess = minus the month's cross-sectional mean):")
    san += ["", md(state_table(ret), ".1f"), ""]
    print("\n".join(san))
    lines += san

    (OUT / "psa9_lag.md").write_text("\n".join(lines))
    pd.to_pickle({"events": events, "flats": flats, "grid": grid, "ret": ret, "cc": cc_all, "ev_tbl": ev_tbl}, args.cache_dir / "psa9_lag_results.pkl")
    print(f"\nwrote {OUT / 'psa9_lag.md'} ({time.time() - t0:.0f}s total)")


if __name__ == "__main__":
    main()
