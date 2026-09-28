#!/usr/bin/env python3
"""PSA 9 after a PSA 10 spike, at the level of single sales (2026-09-28)

    analysis/.venv/bin/python analysis/psa9_spike.py --cache-dir <dir with clean_sales.pkl>   # ~3-4 min -> analysis/out/psa9_spike.md
    analysis/.venv/bin/python analysis/psa9_spike.py --cache-dir <dir> --max-prior-age 180 --tag _age180   # prior sales < 6 months old -> out/psa9_spike_age180.md
    analysis/.venv/bin/python analysis/test_psa9_spike.py                                       # direct-call checks

Sid (the app owner), 2026-09-28: "I think the catch-up happens fast, within
1 to 2 weeks of the PSA 10 spike, so monthly buckets would miss it. [...]
Could you run it as a sale-level event study: find each PSA 10 spike sale
(50%+ above the prior 2 sales) for top-50 cards in a hot category, then track
the PSA 9 sales at 7, 14, 30, and 60 days after the spike date? Mainly want
to know if buying within the first few days beats buying later, and how fast
the window closes."

No monthly buckets anywhere in the outcome. Reused from the earlier studies
(nothing re-derived): psa9_lag.py's cleaning (skipped rows out, PWCC mirror
copies and per-(card, grade) outliers removed with history/metrics.py, rows
listed under both grades dropped from both), its PSA 9 universe (cards with
>= 12 months having a sale in each grade since 2021), the 13% sell-side fee
and 60-day exit grace; psa9_hype.py's card-type classifier and hot-category
months (a card type's median PSA 10 month-on-month bucket return >= 15%, also
30%, over >= 5 cards); psa9_buyrule.py's top-50 (the app's roster: top 50 of
latest/characters.csv by alt_market_cap_le2013_usd, whole-word subject
match). Standard errors follow the one-observation-per-period discipline:
events are averaged within calendar month first and the t is across months
(REVIEW_2026-09-15: card-days inside one month share one market).

Definitions
  Spike. A clean PSA 10 sale on date D whose price is >= (1 + thr) x the
    median of the card's PREVIOUS two clean PSA 10 sales (thr = 50%, also
    100%), both dated <= D and the older one within MAX_PRIOR_AGE days of D
    (a "spike" over two sales from years ago is drift, not an event). Spikes
    on one card within COLLAPSE = 14 days of the last kept spike are folded
    into it (the first one wins). Only sales from 2021 on (alt.xyz coverage).
    The spike is known on D; alt.xyz shows eBay sales with a 0-1 day lag
    (REVIEW 1d), so it is actionable from D+1, and every PSA 9 sale used as
    an outcome or an entry is dated strictly after D.
  Hot category. The card's card type (psa9_hype.card_types) is hot in the
    calendar month M that contains D (median PSA 10 bucket return of the
    type's cards in M >= 15% / 30%). This is the definition the earlier
    studies used; it is a mild look-ahead when D is early in M (the month is
    not over), so `hot known at D` = the type was hot in M-1 is shown as the
    strictly point-in-time variant.
  PSA 9 path. Pre-event level = median log PSA 9 price in (D-60, D]. Windows
    (D, D+3], (D+3, D+7], (D+7, D+14], (D+14, D+30], (D+30, D+60]: median
    log PSA 9 price in the window minus the pre level. A window that ends
    after the data end is blank (right-censored). The "window closing" curve
    is each window's median level as a share of the (D+30, D+60] level.
  Ratio. (PSA 9 minus PSA 10 log level in the window) minus (PSA 9 pre level
    minus log of the pre-spike PSA 10 reference = the prior-2 median). At D
    the ratio has dropped by log(1 + spike); what closes afterwards is split
    into the PSA 9 rising and the PSA 10 giving back.
  Controls. (a) Spikes on non-top-50 universe cards and on top-50 cards in
    non-hot months, same definition. (b) Random non-spike dates on the same
    cards: for each spike, N_CTRL PSA 10 sale dates of the same card in the
    same calendar year (any year if none) that are not spike candidates and
    are >= 60 days from every spike candidate, with the same paths and
    trades. (c) For the trade: unhyped top-50 cards bought on the SAME dates,
    unhyped = no 50% spike candidate on that card in (D-60, D] and, for the
    hot-category samples, its card type not hot in M; the control return for
    a date is the median over those cards' trades with the same entry window
    and hold. (d) The matched control: for each event, up to K_MATCH other
    cards of the SAME card type (top-50 cards for the top-50 samples) with no
    50% spike candidate in (D-60, D], anchored on the same D, with the same
    paths and trades; an event's excess is its value minus the median of its
    matched cards. Inside a hot month the whole type is rising, so (d) is the
    control that separates the spike's own effect from the category's month;
    (c) measures the spike plus the category against the rest of the top-50.
  Trade. Buy at the first PSA 9 sale in (D, D+3], or (D+3, D+7], (D+7, D+14],
    (D+14, D+30]; sell at the first PSA 9 sale >= 60 / 90 / 180 days after
    the purchase (within a further 60 days, else "no exit"); net = exit x
    (1 - 13%) / entry - 1. Only signals whose exit window closed before the
    data end count.
  Tier = the pre-spike PSA 10 reference (prior-2 median): < $1K, $1K-$10K,
    >= $10K.

Output: every table printed and written to analysis/out/psa9_spike.md.
Checks: analysis/test_psa9_spike.py.
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import psa9_lag as L  # noqa: E402
import psa9_hype as H  # noqa: E402
import psa9_buyrule as B  # noqa: E402

ROOT = L.ROOT
OUT = ROOT / "analysis" / "out"
DAY = L.DAY
START = L.START
SPIKES = (0.50, 1.00)
HOTS = (0.15, 0.30)
COLLAPSE = 14
PRE_DAYS = 60
MAX_PRIOR_AGE = 365
WINDOWS = ((0, 3), (3, 7), (7, 14), (14, 30), (30, 60))
ENTRY_WINDOWS = ((0, 3), (3, 7), (7, 14), (14, 30))
HOLDS = (60, 90, 180)
EXIT_GRACE = L.EXIT_GRACE
FEE = L.FEE
CTRL_GAP = 60              # a control date / control card must be this far from any spike candidate
N_CTRL = 2                 # random non-spike dates per event
K_MATCH = 5                # matched same-type unspiked cards per event
SEED = 20260928
TIERS = H.TIERS
TIER_LABELS = [t[2] for t in TIERS]
THIN_EVENTS, THIN_MONTHS = 10, 5
EXAMPLES = B.EXAMPLES


def wl(w):
    return f"d{w[0] + 1}-{w[1]}" if w[0] + 1 != w[1] else f"d{w[1]}"


def wkey(w):
    return f"{w[0]}_{w[1]}"


# ---------------------------------------------------------------- spikes

def find_spikes(d10, p10, thr, collapse=COLLAPSE, max_prior_age=MAX_PRIOR_AGE, start=START):
    """Spike sales: p[i] >= (1 + thr) x median(p[i-1], p[i-2]), the older prior sale within
    max_prior_age days, date >= start; spikes within `collapse` days of the last kept spike are
    folded into it. Returns (kept indices, prev2 median per sale, ratio per sale, raw candidate mask)."""
    n = len(p10)
    prev2 = np.full(n, np.nan)
    ratio = np.full(n, np.nan)
    cand = np.zeros(n, bool)
    if n < 3:
        return np.array([], int), prev2, ratio, cand
    prev2[2:] = (p10[1:-1] + p10[:-2]) / 2.0                 # the median of two values
    ratio[:] = p10 / prev2 - 1
    age_ok = np.zeros(n, bool)
    age_ok[2:] = (d10[2:] - d10[:-2]) <= max_prior_age * DAY
    cand = np.isfinite(ratio) & (ratio >= thr) & age_ok & (d10 >= np.datetime64(start.date()))
    kept, last = [], None
    for j in np.flatnonzero(cand):
        if last is not None and d10[j] <= last + collapse * DAY:
            continue
        kept.append(j)
        last = d10[j]
    return np.array(kept, int), prev2, ratio, cand


def _win(d, lp, anchors, lo, hi):
    """(median, count) of lp over (anchor+lo, anchor+hi] per anchor."""
    a = np.searchsorted(d, anchors + lo * DAY, side="right")
    b = np.searchsorted(d, anchors + hi * DAY, side="right")
    med = np.full(len(anchors), np.nan)
    for i in np.flatnonzero(b > a):
        med[i] = np.median(lp[a[i]:b[i]])
    return med, (b - a).astype(int)


def _first_after(d, anchors, lo, hi):
    """Index of the first sale dated in (anchor+lo, anchor+hi], -1 when none."""
    i = np.searchsorted(d, anchors + lo * DAY, side="right")
    ok = i < len(d)
    ok[ok] &= d[i[ok]] <= anchors[ok] + hi * DAY
    return np.where(ok, i, -1)


def event_rows(aid, anchors, d10, p10, d9, p9, data_end, holds=HOLDS, windows=WINDOWS, entry_windows=ENTRY_WINDOWS):
    """PSA 9 / PSA 10 window medians and the PSA 9 trades for each anchor date (dict of arrays).
    Everything after the anchor is right-censored at data_end."""
    lp10, lp9 = np.log(p10), np.log(p9)
    row = {"asset_id": np.repeat(aid, len(anchors)), "date": anchors}
    row["m9_pre"], row["n9_pre"] = _win(d9, lp9, anchors, -PRE_DAYS, 0)
    for lo, hi in windows:
        closed = anchors + hi * DAY <= data_end
        m9, n9 = _win(d9, lp9, anchors, lo, hi)
        m10, n10 = _win(d10, lp10, anchors, lo, hi)
        k = wkey((lo, hi))
        row[f"m9_{k}"] = np.where(closed, m9, np.nan)
        row[f"n9_{k}"] = np.where(closed, n9, 0)
        row[f"m10_{k}"] = np.where(closed, m10, np.nan)
        row[f"n10_{k}"] = np.where(closed, n10, 0)
        row[f"closed_{k}"] = closed
    for lo, hi in entry_windows:
        k = wkey((lo, hi))
        i = _first_after(d9, anchors, lo, hi)
        has = i >= 0
        entry = np.where(has, p9[np.maximum(i, 0)], np.nan)
        row[f"entry_{k}"] = entry
        row[f"elag_{k}"] = np.where(has, (d9[np.maximum(i, 0)] - anchors) / DAY, np.nan)
        for h in holds:
            closed = anchors + (hi + h + EXIT_GRACE) * DAY <= data_end
            net = np.full(len(anchors), np.nan)
            m = has & closed
            if m.any():
                ei = i[m]
                j = np.searchsorted(d9, d9[ei] + h * DAY, side="left")
                ok = j < len(d9)
                jj = np.minimum(j, len(d9) - 1)
                ok &= d9[jj] <= d9[ei] + (h + EXIT_GRACE) * DAY
                v = np.where(ok, p9[jj] * (1 - FEE) / p9[ei] - 1, np.nan)
                net[m] = v
            row[f"net_{k}_{h}"] = net
            row[f"closed_{k}_{h}"] = closed & has          # entered, exit window closed -> a trade (no exit if net is NaN)
    return row


def scan(sales, cards, data_end, thresholds=SPIKES, n_ctrl=N_CTRL, seed=SEED, max_prior_age=MAX_PRIOR_AGE):
    """Spike events at each threshold on every card in `cards`, random non-spike control dates on the
    same cards, and each card's raw 50% candidate dates (for the same-date control's contamination test)."""
    s = sales[sales.asset_id.isin(cards)]
    by = {(aid, g): grp for (aid, g), grp in s.groupby(["asset_id", "grade"], sort=False)}
    rng = np.random.default_rng(seed)
    ev, ctrl, cand_dates = [], [], {}
    for aid in cards:
        g10, g9 = by.get((aid, "10.0")), by.get((aid, "9.0"))
        if g10 is None or g9 is None or len(g10) < 3:
            continue
        d10 = g10.date.values.astype("datetime64[D]")
        p10 = g10.price.values.astype(float)
        d9 = g9.date.values.astype("datetime64[D]")
        p9 = g9.price.values.astype(float)
        found = {}
        for thr in thresholds:
            kept, prev2, ratio, cand = find_spikes(d10, p10, thr, max_prior_age=max_prior_age)
            found[thr] = (kept, prev2, ratio, cand)
            if len(kept):
                r = event_rows(aid, d10[kept], d10, p10, d9, p9, data_end)
                r.update(thr=np.repeat(thr, len(kept)), spike=p10[kept], prev2=prev2[kept], ratio=ratio[kept],
                         prior_age=(d10[kept] - d10[kept - 2]) / DAY, kind=np.repeat("spike", len(kept)))
                ev.append(pd.DataFrame(r))
        kept0, prev2, ratio, cand0 = found[thresholds[0]]
        cand_dates[aid] = d10[cand0]
        if len(kept0) and n_ctrl:
            # control dates: non-candidate sales >= CTRL_GAP days from every candidate, same year as the event when possible
            idx = np.arange(len(d10))
            ok = np.isfinite(ratio) & ~cand0 & (d10 >= np.datetime64(START.date()))
            if cand0.any():
                cd = d10[cand0]
                k = np.searchsorted(cd, d10)
                near = np.zeros(len(d10), bool)
                for off in (-1, 0):
                    kk = np.clip(k + off, 0, len(cd) - 1)
                    near |= np.abs((cd[kk] - d10) / DAY) < CTRL_GAP
                ok &= ~near
            pool = idx[ok]
            if len(pool):
                pool_year = d10[pool].astype("datetime64[Y]")
                picks = []
                for j in kept0:
                    same = pool[pool_year == d10[j].astype("datetime64[Y]")]
                    src = same if len(same) else pool
                    picks.extend(rng.choice(src, size=min(n_ctrl, len(src)), replace=False).tolist())
                picks = np.array(sorted(set(picks)), int)
                r = event_rows(aid, d10[picks], d10, p10, d9, p9, data_end)
                r.update(thr=np.repeat(thresholds[0], len(picks)), spike=p10[picks], prev2=prev2[picks], ratio=ratio[picks],
                         prior_age=(d10[picks] - d10[np.maximum(picks - 2, 0)]) / DAY, kind=np.repeat("control", len(picks)))
                ctrl.append(pd.DataFrame(r))
    ev = pd.concat(ev, ignore_index=True) if ev else pd.DataFrame()
    ctrl = pd.concat(ctrl, ignore_index=True) if ctrl else pd.DataFrame()
    return ev, ctrl, cand_dates


def add_derived(df, ct_of, hot_sets, top_ids):
    """Card type, top-50 flag, hot flags (month of D and M-1), tier, relative paths, ratio path, PSA 10 retention."""
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df.date)
    df["month"] = df.date.dt.to_period("M")
    df["week"] = df.date.dt.to_period("W-SUN")
    df["year"] = df.date.dt.year
    df["card_type"] = df.asset_id.map(ct_of)
    df["top"] = df.asset_id.isin(top_ids)
    for thr, hot in hot_sets.items():
        k = int(round(100 * thr))
        keys = list(zip(df.card_type, df.month))
        df[f"hot{k}"] = [x in hot for x in keys]
        keys_prev = list(zip(df.card_type, df.month - 1))
        df[f"hot{k}_prev"] = [x in hot for x in keys_prev]
    df["tier"] = pd.cut(df.prev2, [t[0] for t in TIERS] + [np.inf], labels=TIER_LABELS, right=False)
    lspike, lprev = np.log(df.spike), np.log(df.prev2)
    ratio_pre = df.m9_pre - lprev
    for w in WINDOWS:
        k = wkey(w)
        df[f"rel9_{k}"] = df[f"m9_{k}"] - df.m9_pre                           # PSA 9 vs its pre-event level
        df[f"rel10_{k}"] = df[f"m10_{k}"] - lspike                            # PSA 10 vs the spike price
        df[f"ret10_{k}"] = (df[f"m10_{k}"] - lprev) / (lspike - lprev)        # 1 = held the spike, 0 = back to the prior level
        df[f"ratio_{k}"] = (df[f"m9_{k}"] - df[f"m10_{k}"]) - ratio_pre       # PSA 9 / PSA 10 log ratio vs pre-spike
    df["gap_open"] = -np.log1p(df.ratio)                                      # the ratio's drop at D
    for w in ENTRY_WINDOWS:
        k = wkey(w)
        df[f"eprem_{k}"] = np.log(df[f"entry_{k}"]) - df.m9_pre               # first buyable PSA 9 vs the pre level
    return df


# ---------------------------------------------------------------- the same-date control (unhyped top-50 cards)

def date_control(sales, ctrl_cards, dates, cand_dates, ct_of, hot_sets, data_end):
    """For every (control card, event date): the PSA 9 trades with each entry window and hold, plus flags
    `recent_spike` (a 50% spike candidate on the card in (D-60, D]) and `hotNN` (its card type hot in D's month).
    Returns a long frame; medians per date are taken by the caller."""
    dates = np.array(sorted(set(np.asarray(dates, dtype="datetime64[D]"))), dtype="datetime64[D]")
    s = sales[sales.asset_id.isin(ctrl_cards) & (sales.grade == "9.0")]
    by = {aid: grp for aid, grp in s.groupby("asset_id", sort=False)}
    months = pd.DatetimeIndex(dates).to_period("M")
    parts = []
    cols = [f"net_{wkey(w)}_{h}" for w in ENTRY_WINDOWS for h in HOLDS]
    for aid in ctrl_cards:
        g9 = by.get(aid)
        if g9 is None:
            continue
        d9 = g9.date.values.astype("datetime64[D]")
        p9 = g9.price.values.astype(float)
        r = {"asset_id": np.repeat(aid, len(dates)), "date": dates}
        for lo, hi in ENTRY_WINDOWS:
            k = wkey((lo, hi))
            i = _first_after(d9, dates, lo, hi)
            has = i >= 0
            r[f"entry_{k}"] = np.where(has, p9[np.maximum(i, 0)], np.nan).astype(np.float32)
            for h in HOLDS:
                closed = dates + (hi + h + EXIT_GRACE) * DAY <= data_end
                net = np.full(len(dates), np.nan, dtype=np.float32)
                m = has & closed
                if m.any():
                    ei = i[m]
                    j = np.searchsorted(d9, d9[ei] + h * DAY, side="left")
                    ok = j < len(d9)
                    jj = np.minimum(j, len(d9) - 1)
                    ok &= d9[jj] <= d9[ei] + (h + EXIT_GRACE) * DAY
                    net[m] = np.where(ok, p9[jj] * (1 - FEE) / p9[ei] - 1, np.nan)
                r[f"net_{k}_{h}"] = net
        cd = np.sort(cand_dates.get(aid, np.array([], dtype="datetime64[D]")))
        if len(cd):
            k = np.searchsorted(cd, dates, side="right") - 1            # the last candidate dated <= D
            recent = (k >= 0) & (cd[np.maximum(k, 0)] > dates - CTRL_GAP * DAY)
        else:
            recent = np.zeros(len(dates), bool)
        r["recent_spike"] = recent
        ct = ct_of.get(aid)
        r["card_type"] = np.repeat(ct if ct is not None else "(none)", len(dates))
        for thr, hot in hot_sets.items():
            r[f"hot{int(round(100 * thr))}"] = np.array([(ct, m) in hot for m in months])
        parts.append(pd.DataFrame(r))
    out = pd.concat(parts, ignore_index=True)
    out["date"] = pd.to_datetime(out.date)
    return out


def control_medians(dc, exclude_hot=None, by_type=False):
    """Median control trade per date (or per date x card type) over unspiked cards (no 50% candidate in (D-60, D];
    and, when exclude_hot is 'hot15'/'hot30', a card type not hot that month)."""
    m = ~dc.recent_spike
    if exclude_hot:
        m &= ~dc[exclude_hot]
    cols = [c for c in dc.columns if c.startswith("net_")]
    keys = ["date", "card_type"] if by_type else ["date"]
    q = dc.loc[m, keys + cols]
    med = q.groupby(keys)[cols].median()
    cnt = q.groupby(keys)[cols].count()
    return med, cnt


def matched_control(sales, events, pool_by_type, cand_dates, data_end, k=K_MATCH, seed=SEED):
    """For each event (card c, date D, card type T): up to k other cards of type T from pool_by_type[T] with no
    50% spike candidate in (D-60, D], anchored on D, with event_rows' paths and trades. Returns a frame with an
    `event_id` column (the event's index) so an event's excess is its value minus the median of its matches."""
    rng = np.random.default_rng(seed)
    s = sales[sales.asset_id.isin(set().union(*pool_by_type.values()))] if pool_by_type else sales.iloc[0:0]
    by = {(aid, g): grp for (aid, g), grp in s.groupby(["asset_id", "grade"], sort=False)}
    per_card = {}
    for eid, (c, D, T) in zip(events.index, zip(events.asset_id, events.date.values.astype("datetime64[D]"), events.card_type.fillna("(none)"))):
        pool = pool_by_type.get(T)
        if pool is None or len(pool) <= 1:
            continue
        order = rng.permutation(len(pool))
        got = 0
        for j in order:
            a = pool[j]
            if a == c:
                continue
            cd = cand_dates.get(a)
            if cd is not None and len(cd):
                i = np.searchsorted(cd, D, side="right") - 1
                if i >= 0 and cd[i] > D - CTRL_GAP * DAY:
                    continue
            per_card.setdefault(a, []).append((D, eid))
            got += 1
            if got >= k:
                break
    parts = []
    for a, lst in per_card.items():
        g10, g9 = by.get((a, "10.0")), by.get((a, "9.0"))
        if g10 is None or g9 is None:
            continue
        lst.sort()
        anchors = np.array([d for d, _ in lst], dtype="datetime64[D]")
        r = event_rows(a, anchors, g10.date.values.astype("datetime64[D]"), g10.price.values.astype(float),
                       g9.date.values.astype("datetime64[D]"), g9.price.values.astype(float), data_end)
        r["event_id"] = np.array([e for _, e in lst])
        parts.append(pd.DataFrame(r))
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True)
    out["date"] = pd.to_datetime(out.date)
    out["month"] = out.date.dt.to_period("M")
    for w in WINDOWS:
        kk = wkey(w)
        out[f"rel9_{kk}"] = out[f"m9_{kk}"] - out.m9_pre
    return out


def matched_excess(df, mc, cols):
    """df[col + '_mx'] = df[col] minus the median over the event's matched cards (NaN when no match has data)."""
    df = df.copy()
    if mc.empty:
        for c in cols:
            df[c + "_mx"] = np.nan
        return df
    med = mc.groupby("event_id")[cols].median()
    for c in cols:
        df[c + "_mx"] = df[c].values - med[c].reindex(df.index).values
    return df


def matched_table(samples, prefix="rel9", windows=WINDOWS):
    """Per-event excess over the matched same-type unspiked cards: median / mean (t across months) [events with a match, months]."""
    out = []
    for label, df in samples:
        r = {"sample": label, "events": len(df)}
        for w in windows:
            c = f"{prefix}_{wkey(w)}_mx"
            r[wl(w)] = cell(df, c) if len(df) and c in df else ""
        out.append(r)
    return pd.DataFrame(out)


def path_by_year(df, windows=((0, 3), (3, 7), (7, 14), (30, 60))):
    out = []
    for y, g in df.groupby("year"):
        r = {"year": y, "events": len(g), "cards": g.asset_id.nunique(), "months": g.month.nunique()}
        for w in windows:
            k = wkey(w)
            v = g[f"rel9_{k}"].dropna()
            x = g[f"rel9_{k}_mx"].dropna() if f"rel9_{k}_mx" in g else pd.Series(dtype=float)
            r[f"{wl(w)}: PSA 9 median % [n]"] = f"{100 * v.median():+.1f} [{len(v)}]" if len(v) else ""
            r[f"{wl(w)}: vs matched, median %"] = f"{100 * x.median():+.1f}" if len(x) else ""
        for (lo, hi), h in (((0, 3), 90), ((7, 14), 90)):
            k = wkey((lo, hi))
            v = g[f"net_{k}_{h}"].dropna()
            r[f"trade {wl((lo, hi))} entry, {h}d: hit / median net % [n]"] = f"{100 * (v > 0).mean():.0f}% / {100 * v.median():+.1f} [{len(v)}]" if len(v) else ""
        out.append(r)
    return pd.DataFrame(out)


# ---------------------------------------------------------------- statistics

def period_t(v, period):
    """One observation per period (mean of v within it); mean, median, share > 0, t, n periods."""
    s = pd.Series(np.asarray(v, float)).groupby(np.asarray(period)).mean().dropna()
    n = len(s)
    if n == 0:
        return np.nan, np.nan, np.nan, np.nan, 0
    t = s.mean() / (s.std(ddof=1) / np.sqrt(n)) if n >= 3 and s.std(ddof=1) > 0 else np.nan
    return s.mean(), s.median(), (s > 0).mean(), t, n


def cell(df, col, scale=100.0, fmt="{:+.1f}"):
    """'median / mean (t) [events, months]' for one column; blank when nothing has data."""
    v = df[col]
    ok = v.notna()
    if ok.sum() == 0:
        return ""
    mean, _, _, t, n = period_t(v[ok].values, df.loc[ok, "month"].astype(str).values)
    s = f"{fmt.format(scale * v[ok].median())} / {fmt.format(scale * mean)}"
    if np.isfinite(t):
        s += f" ({t:.1f})"
    return s + f" [{int(ok.sum())}, {n}]"


def path_table(samples, prefix, scale=100.0, fmt="{:+.1f}", windows=WINDOWS):
    out = []
    for label, df in samples:
        r = {"sample": label, "events": len(df)}
        for w in windows:
            r[wl(w)] = cell(df, f"{prefix}_{wkey(w)}", scale, fmt) if len(df) else ""
        out.append(r)
    return pd.DataFrame(out)


def closing_curve(samples, col="rel9", base=(30, 60), windows=WINDOWS):
    """Each window's median level as a share of the base window's median level (the 'window closing' curve),
    plus the same on the complete cases (events with a PSA 9 sale in every window)."""
    out = []
    for label, df in samples:
        for variant in ("all events with a sale in the window", "complete cases only"):
            q = df
            if variant.startswith("complete"):
                q = df[np.all([df[f"n9_{wkey(w)}"].values > 0 for w in windows], axis=0)]
            if not len(q):
                continue
            lv = {w: np.nanmedian(q[f"{col}_{wkey(w)}"]) if q[f"{col}_{wkey(w)}"].notna().any() else np.nan for w in windows}
            b = lv[base]
            r = {"sample": label, "variant": variant, "events": len(q), f"level {wl(base)} (median)": f"{100 * b:+.1f}%" if np.isfinite(b) else ""}
            for w in windows:
                if w == base:
                    continue
                v = lv[w]
                r[f"{wl(w)}: level"] = f"{100 * v:+.1f}%" if np.isfinite(v) else ""
                r[f"{wl(w)}: share of {wl(base)}"] = (f"{100 * v / b:.0f}%" if np.isfinite(v) and np.isfinite(b) and abs(b) >= 0.01 else "n/a (base < 1%)")
            out.append(r)
    return pd.DataFrame(out)


def excess_vs(samples, ctrl_frames, prefix, windows=WINDOWS):
    """Event median minus control median per window, and the paired-by-month difference (mean, t, months)."""
    out = []
    for (label, df), (_, c) in zip(samples, ctrl_frames):
        r = {"sample": label, "events": len(df), "control rows": len(c)}
        for w in windows:
            k = f"{prefix}_{wkey(w)}"
            if not len(df) or not len(c) or df[k].notna().sum() == 0 or c[k].notna().sum() == 0:
                r[wl(w)] = ""
                continue
            a = df.groupby("month")[k].mean().dropna()
            b = c.groupby("month")[k].mean().dropna()
            d = (a - b).dropna()
            t = d.mean() / (d.std(ddof=1) / np.sqrt(len(d))) if len(d) >= 3 and d.std(ddof=1) > 0 else np.nan
            s = f"{100 * (df[k].median() - c[k].median()):+.1f} med; paired {100 * d.mean():+.1f}"
            if np.isfinite(t):
                s += f" ({t:.1f})"
            r[wl(w)] = s + f" [{len(d)}]"
        out.append(r)
    return pd.DataFrame(out)


def liquidity_table(samples):
    out = []
    for label, df in samples:
        if not len(df):
            continue
        r = {"sample": label, "events": len(df)}
        has = np.zeros(len(df), bool)
        for w in ENTRY_WINDOWS:
            has |= df[f"entry_{wkey(w)}"].notna().values
            r[f"PSA 9 sale by day {w[1]}"] = f"{100 * has.mean():.0f}%"
        for w in ENTRY_WINDOWS:
            e = df[f"eprem_{wkey(w)}"].dropna()
            r[f"first buyable in {wl(w)} vs pre level"] = f"{100 * np.expm1(e.median()):+.1f}% [{len(e)}]" if len(e) else ""
        r["PSA 9 sales in (D-60, D], median"] = f"{df.n9_pre.median():.0f}"
        out.append(r)
    return pd.DataFrame(out)


def _ref(med, rows, col):
    """Control median for each row: by date, or by (date, card type) when `med` is indexed that way."""
    if med is None:
        return np.full(len(rows), np.nan)
    if isinstance(med.index, pd.MultiIndex):
        idx = pd.MultiIndex.from_arrays([rows.date.values, rows.card_type.fillna("(none)").values])
        return med[col].reindex(idx).values
    return med[col].reindex(rows.date.values).values


def trade_table(samples, controls=()):
    """Per sample x entry window x hold: closed signals, entered, no exit, hit, mean, median, entry vs pre, and for each
    control in `controls` = [(label, {sample label -> medians frame})]: mean over months of the monthly mean excess, t,
    share of months ahead."""
    out = []
    for label, df in samples:
        if not len(df):
            continue
        for w in ENTRY_WINDOWS:
            k = wkey(w)
            for h in HOLDS:
                c = df[(df.date + pd.Timedelta(days=w[1] + h + EXIT_GRACE) <= df.attrs.get("data_end", pd.Timestamp.max)).values]
                entered = c[c[f"entry_{k}"].notna()]
                v = entered[f"net_{k}_{h}"]
                got = v.notna()
                r = {"sample": label, "entry": wl(w), "hold": h, "signals (closed)": len(c), "entered %": 100 * len(entered) / len(c) if len(c) else np.nan,
                     "trades": int(got.sum()), "no exit %": 100 * (1 - got.mean()) if len(entered) else np.nan,
                     "hit %": 100 * (v[got] > 0).mean() if got.any() else np.nan,
                     "mean net %": 100 * v[got].mean() if got.any() else np.nan, "median net %": 100 * v[got].median() if got.any() else np.nan,
                     "entry vs pre %": 100 * np.expm1(entered[f"eprem_{k}"].median()) if len(entered) else np.nan,
                     "months": entered.loc[got, "month"].nunique()}
                for clabel, lookup in controls:
                    med = lookup(label) if callable(lookup) else lookup
                    if med is None or not got.any():
                        continue
                    ref = _ref(med, entered.loc[got], f"net_{k}_{h}")
                    ex = v[got].values - ref
                    okx = np.isfinite(ex)
                    mean, _, pos, t, n = period_t(ex[okx], entered.loc[got, "month"].astype(str).values[okx])
                    r[f"vs {clabel}, pts"] = 100 * mean
                    r[f"median vs {clabel}, pts"] = 100 * np.nanmedian(ex) if okx.any() else np.nan
                    r[f"t ({clabel})"] = t
                    r[f"% months ahead ({clabel})"] = 100 * pos
                out.append(r)
    return pd.DataFrame(out)


def trade_by_year(df, h, controls=()):
    out = []
    for w in ENTRY_WINDOWS:
        k = wkey(w)
        c = df[df[f"closed_{k}_{h}"].values]
        for y, g in c.groupby("year"):
            v = g[f"net_{k}_{h}"]
            got = v.notna()
            r = {"entry": wl(w), "year": y, "entered": len(g), "trades": int(got.sum()), "hit %": 100 * (v[got] > 0).mean() if got.any() else np.nan,
                 "mean net %": 100 * v[got].mean() if got.any() else np.nan, "median net %": 100 * v[got].median() if got.any() else np.nan}
            for clabel, med in controls:
                if med is not None and got.any():
                    ex = v[got].values - _ref(med, g.loc[got], f"net_{k}_{h}")
                    r[f"median vs {clabel}, pts"] = 100 * np.nanmedian(ex) if np.isfinite(ex).any() else np.nan
            out.append(r)
    return pd.DataFrame(out)


def paired_entries(samples, early=(0, 3), lates=((3, 7), (7, 14), (14, 30)), holds=HOLDS):
    """Same event, same exit rule: net of buying early minus net of buying later, for events with both entries."""
    out = []
    for label, df in samples:
        if not len(df):
            continue
        for late in lates:
            for h in holds:
                a, b = df[f"net_{wkey(early)}_{h}"], df[f"net_{wkey(late)}_{h}"]
                ok = a.notna() & b.notna()
                if ok.sum() == 0:
                    continue
                d = (a - b)[ok]
                mean, _, pos, t, n = period_t(d.values, df.loc[ok, "month"].astype(str).values)
                ep = np.expm1(df.loc[ok, f"eprem_{wkey(late)}"] - df.loc[ok, f"eprem_{wkey(early)}"])
                out.append({"sample": label, "early": wl(early), "late": wl(late), "hold": h, "events with both": int(ok.sum()),
                            "late entry price vs early, median %": 100 * ep.median(),
                            "early minus late net, median pts": 100 * d.median(), "mean pts": 100 * d.mean(),
                            "% events early better": 100 * (d > 0).mean(), "t (months)": t, "months": n})
    return pd.DataFrame(out)


def counts_table(samples, by):
    out = []
    for label, df in samples:
        r = {"sample": label, "events": len(df), "cards": df.asset_id.nunique() if len(df) else 0,
             "weeks": df.week.nunique() if len(df) else 0, "months": df.month.nunique() if len(df) else 0}
        if len(df):
            for k, g in df.groupby(by, observed=True):
                r[str(k)] = f"{len(g)} ({g.month.nunique()} mo)" + (" thin" if len(g) < THIN_EVENTS or g.month.nunique() < THIN_MONTHS else "")
        out.append(r)
    return pd.DataFrame(out).fillna("")


def retention_table(samples):
    out = []
    for label, df in samples:
        if not len(df):
            continue
        r = {"sample": label, "events": len(df), "spike size, median": f"+{100 * df.ratio.median():.0f}%"}
        for w in WINDOWS:
            k = wkey(w)
            v, rt = df[f"rel10_{k}"], df[f"ret10_{k}"]
            ok = v.notna()
            r[f"{wl(w)}: PSA 10 vs spike / retention [n]"] = (f"{100 * v[ok].median():+.1f}% / {100 * rt[ok].median():.0f}% [{int(ok.sum())}]" if ok.any() else "")
        out.append(r)
    return pd.DataFrame(out)


def ratio_table(samples):
    out = []
    for label, df in samples:
        if not len(df):
            continue
        r = {"sample": label, "events": len(df), "ratio drop at D (median)": f"{-100 * df.gap_open.median():+.1f}%"}
        for w in WINDOWS:
            k = wkey(w)
            v = df[f"ratio_{k}"]
            ok = v.notna()
            if not ok.any():
                r[wl(w)] = ""
                continue
            q = df[ok]
            closed = 1 - q[f"ratio_{k}"].median() / q.gap_open.median()
            r[wl(w)] = (f"ratio {100 * q[f'ratio_{k}'].median():+.1f}% ({100 * closed:.0f}% closed): PSA 9 {100 * q[f'rel9_{k}'].median():+.1f}, "
                        f"PSA 10 {100 * q[f'rel10_{k}'].median():+.1f} [{len(q)}]")
        out.append(r)
    return pd.DataFrame(out)


# ---------------------------------------------------------------- the three cards

def card_events(sales, aid, data_end, ct_of, hot_sets, top_ids, max_prior_age):
    s10 = sales[(sales.asset_id == aid) & (sales.grade == "10.0")].sort_values("date")
    s9 = sales[(sales.asset_id == aid) & (sales.grade == "9.0")].sort_values("date")
    d10, p10 = s10.date.values.astype("datetime64[D]"), s10.price.values.astype(float)
    d9, p9 = s9.date.values.astype("datetime64[D]"), s9.price.values.astype(float)
    lines = [f"**{EXAMPLES.get(aid, aid)}** (`{aid}`): card type {ct_of.get(aid, 'none')}, top-50 (app list): {'yes' if aid in top_ids else 'no'}; "
             f"{len(d10)} clean PSA 10 sales, {len(d9)} clean PSA 9 sales."]
    if len(d10) < 3 or len(d9) == 0:
        lines.append("Too few sales for the event finder.")
        return lines, pd.DataFrame()
    kept, prev2, ratio, cand = find_spikes(d10, p10, SPIKES[0], max_prior_age=max_prior_age)
    kept_any, _, _, cand_any = find_spikes(d10, p10, SPIKES[0], max_prior_age=10 ** 6)
    extra = [j for j in kept_any if j not in set(kept)]
    if not len(kept):
        lines.append(f"No >= 50% spike with both prior sales within {max_prior_age} days.")
    rows = []
    for j in list(kept) + extra:
        D = d10[j]
        r = event_rows(aid, np.array([D]), d10, p10, d9, p9, data_end)
        m = pd.Period(pd.Timestamp(D), "M")
        rec = {"spike date": str(D), "PSA 10 spike": f"${p10[j]:,.0f}", "prior 2 (median)": f"${prev2[j]:,.0f}", "vs prior 2": f"+{100 * ratio[j]:.0f}%",
               "prior-2 age (days)": int((D - d10[j - 2]) / DAY), "age cap": "ok" if j in set(kept) else f"> {max_prior_age}d (shown for reference)",
               "hot 15% / 30% (type, month of D)": " / ".join("yes" if (ct_of.get(aid), m) in hot_sets[t] else "no" for t in HOTS),
               "PSA 9 pre level (D-60, D]": f"${np.exp(r['m9_pre'][0]):,.0f} ({int(r['n9_pre'][0])})" if np.isfinite(r["m9_pre"][0]) else "none"}
        for w in WINDOWS:
            k = wkey(w)
            if not r[f"closed_{k}"][0]:
                rec[f"PSA 9 {wl(w)}"] = "window open"
            elif r[f"n9_{k}"][0] == 0:
                rec[f"PSA 9 {wl(w)}"] = "no sale"
            else:
                rel = r[f"m9_{k}"][0] - r["m9_pre"][0]
                rec[f"PSA 9 {wl(w)}"] = f"${np.exp(r[f'm9_{k}'][0]):,.0f} ({int(r[f'n9_{k}'][0])}) {100 * rel:+.0f}%" if np.isfinite(rel) else f"${np.exp(r[f'm9_{k}'][0]):,.0f} ({int(r[f'n9_{k}'][0])})"
            if r[f"n10_{k}"][0] > 0:
                rec[f"PSA 9 {wl(w)}"] += f"; PSA 10 ${np.exp(r[f'm10_{k}'][0]):,.0f} ({int(r[f'n10_{k}'][0])})"
        after = s9[(s9.date > pd.Timestamp(D)) & (s9.date <= pd.Timestamp(D) + pd.Timedelta(days=60))]
        rec["PSA 9 sales in the 60 days after (first 8)"] = ", ".join(f"${p:,.0f} {d.date()}" for d, p in zip(after.date.head(8), after.price.head(8))) or "none"
        rows.append(rec)
    tbl = pd.DataFrame(rows)
    return lines, tbl


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", type=Path, default=L.DEFAULT_CACHE)
    ap.add_argument("--rebuild", action="store_true", help="ignore the cleaned-sales cache")
    ap.add_argument("--english", action="store_true", help="drop Japanese-language cards")
    ap.add_argument("--min-months", type=int, default=L.MIN_MONTHS)
    ap.add_argument("--max-prior-age", type=int, default=MAX_PRIOR_AGE, help="the older of the two prior PSA 10 sales must be within this many days of D")
    ap.add_argument("--n-ctrl", type=int, default=N_CTRL, help="random non-spike control dates per event on the same card")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--top-cap-col", default=B.DEFAULT_TOP_CAP_COL)
    ap.add_argument("--tag", default="", help="suffix for the output file, e.g. _age180 -> out/psa9_spike_age180.md")
    args = ap.parse_args()
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    sales = L.load_clean_sales(cache_dir=args.cache_dir, rebuild=args.rebuild)
    assets = L.load_assets()
    if args.english:
        sales = sales[~sales.asset_id.map(assets.japanese).fillna(False).astype(bool)]
    assets = assets.join(pd.read_csv(ROOT / "history" / "assets.csv", usecols=["asset_id", "card_number"], dtype=str).set_index("asset_id"))
    seg, _ = H.load_segments(assets)
    ct_of = {aid: g for g, ids in seg["card type"].items() for aid in ids}
    monthly = L.monthly_series(sales)
    universe = pd.Index(L.select_universe(monthly, args.min_months))
    data_end = sales.date.max().to_datetime64().astype("datetime64[D]")
    data_end_ts = pd.Timestamp(data_end)
    top_names = B.top_characters(args.top_cap_col)
    top_ids = B.member_ids(assets, top_names)
    r10 = H.psa10_bucket_returns(sales)
    hot_sets = {}
    hot_desc = {}
    for thr in HOTS:
        hm = H.hype_months(r10, seg["card type"], thr, H.MIN_GROUP_CARDS)
        hot = hm[hm.hyped]
        hot_sets[thr] = set(zip(hot.group, hot.month))
        hot_desc[thr] = ", ".join(f"{g} {m}" for g, m in sorted(zip(hot.group, hot.month.astype(str)), key=lambda x: (x[1], x[0])))
    print(f"{len(sales):,} clean sales through {data_end}; PSA 9 universe {len(universe):,} cards, {len(universe.intersection(top_ids)):,} of them top-50; "
          f"hot card-type months: {len(hot_sets[0.15])} at 15%, {len(hot_sets[0.30])} at 30% ({time.time() - t0:.0f}s)", flush=True)

    # --- events
    t1 = time.time()
    ev, ctrl, cand_dates = scan(sales, universe, data_end, n_ctrl=args.n_ctrl, seed=args.seed, max_prior_age=args.max_prior_age)
    ev = add_derived(ev, ct_of, hot_sets, top_ids)
    ctrl = add_derived(ctrl, ct_of, hot_sets, top_ids)
    for df in (ev, ctrl):
        df.attrs["data_end"] = data_end_ts
    print(f"scanned {len(universe):,} cards in {time.time() - t1:.0f}s: {len(ev):,} spike rows ({(ev.thr == 0.5).sum():,} at 50%, {(ev.thr == 1.0).sum():,} at 100%), "
          f"{len(ctrl):,} random control dates", flush=True)

    def S(thr, top=None, hot=None, hot_prev=None):
        m = (ev.thr == thr).values.copy()
        if top is not None:
            m &= (ev.top == top).values
        if hot is not None:
            k, val = hot
            m &= (ev[f"hot{k}"] == val).values
        if hot_prev is not None:
            k, val = hot_prev
            m &= (ev[f"hot{k}_prev"] == val).values
        q = ev[m].copy()
        q.attrs["data_end"] = data_end_ts
        return q

    def C(df):
        """The random non-spike control dates on the cards of `df`, from the same years."""
        q = ctrl[ctrl.asset_id.isin(df.asset_id.unique()) & ctrl.year.isin(df.year.unique())].copy()
        q.attrs["data_end"] = data_end_ts
        return q

    samples = {}
    for thr in SPIKES:
        p = f"{thr:.0%}"
        samples[f"top-50, hot 15%, spike >= {p}"] = S(thr, True, (15, True))
        samples[f"top-50, hot 30%, spike >= {p}"] = S(thr, True, (30, True))
        samples[f"top-50, hot 15% known at D (M-1), spike >= {p}"] = S(thr, True, hot_prev=(15, True))
        samples[f"top-50, any category, spike >= {p}"] = S(thr, True)
        samples[f"top-50, NOT hot 15%, spike >= {p}"] = S(thr, True, (15, False))
        samples[f"non-top-50, hot 15%, spike >= {p}"] = S(thr, False, (15, True))
        samples[f"non-top-50, any category, spike >= {p}"] = S(thr, False)
    main_keys = [k for k in samples if "50%" in k and "M-1" not in k]
    hundred_keys = [k for k in samples if "100%" in k and "M-1" not in k]
    prim = samples["top-50, hot 15%, spike >= 50%"]
    anyc = samples["top-50, any category, spike >= 50%"]
    ctrl_prim, ctrl_any = C(prim), C(anyc)
    ctrl_samples = [("random non-spike dates, cards of top-50 hot 15%", ctrl_prim), ("random non-spike dates, cards of top-50 any category", ctrl_any)]
    hot_ctrl = ctrl_prim[ctrl_prim.hot15].copy()
    hot_ctrl.attrs["data_end"] = data_end_ts
    ctrl_samples_hot = [("random non-spike dates, cards of top-50 hot 15%, in hot 15% months", hot_ctrl)]

    # matched control: same date, same card type, no 50% spike candidate in (D-60, D]; top-50 pool for top-50 samples
    t2 = time.time()
    uni_top = [a for a in universe if a in top_ids]
    uni_rest = [a for a in universe if a not in top_ids]
    pool_top = {T: np.array([a for a in uni_top if ct_of.get(a, "(none)") == T]) for T in set(ct_of.get(a, "(none)") for a in uni_top)}
    pool_rest = {T: np.array([a for a in uni_rest if ct_of.get(a, "(none)") == T]) for T in set(ct_of.get(a, "(none)") for a in uni_rest)}
    mx_cols = [f"rel9_{wkey(w)}" for w in WINDOWS] + [f"net_{wkey(w)}_{h}" for w in ENTRY_WINDOWS for h in HOLDS]
    matched_keys = [k for k in samples if "NOT hot" not in k]
    mc = {}
    for k in matched_keys:
        pool = pool_top if k.startswith("top-50") else pool_rest
        mc[k] = matched_control(sales, samples[k], pool, cand_dates, data_end)
        samples[k] = matched_excess(samples[k], mc[k], mx_cols)
        samples[k].attrs["data_end"] = data_end_ts
    prim, anyc = samples["top-50, hot 15%, spike >= 50%"], samples["top-50, any category, spike >= 50%"]
    n_match = mc["top-50, hot 15%, spike >= 50%"].groupby("event_id").size()
    print(f"matched controls for {len(matched_keys)} samples in {time.time() - t2:.0f}s; primary sample: {len(n_match)} of {len(prim)} events have a match, "
          f"median {n_match.median():.0f} matched cards", flush=True)

    lines = [f"# PSA 9 after a PSA 10 spike, sale level ({pd.Timestamp.today().date()})", "",
             f"Data through {data_end}. Clean sales: {(sales.grade == '10.0').sum():,} PSA 10, {(sales.grade == '9.0').sum():,} PSA 9. "
             f"PSA 9 universe: {len(universe):,} cards (>= {args.min_months} months with a sale in each grade since 2021), {len(universe.intersection(top_ids)):,} with a top-50 subject "
             f"({args.top_cap_col}: {', '.join(top_names[:10])}, ...). Spike = a clean PSA 10 sale >= 50% (also 100%) above the median of the card's previous two clean PSA 10 sales, "
             f"the older of the two within {args.max_prior_age} days; spikes within {COLLAPSE} days of the last kept spike on the card are folded into it. "
             f"Hot category = the card's type is hot (median PSA 10 bucket return >= 15% / 30%) in the calendar month of D. PSA 9 path = median log PSA 9 price in the window minus the "
             f"pre-event level (median in (D-{PRE_DAYS}, D]); every outcome and entry is dated strictly after D. Cells: median / mean (t across months, one observation per month) [events with data, months]. "
             f"Trade: buy the first PSA 9 sale in the entry window, sell the first PSA 9 sale >= hold days later (within +{EXIT_GRACE}), {FEE:.0%} sell-side fee, closed exit windows only.", "",
             f"Hot card-type months at 15%: {hot_desc[0.15]}.", "", f"At 30%: {hot_desc[0.30]}.", ""]

    # ---------------------------------------------------------------- 1. events
    sec = ["## 1. Events", "", "**Counts by year** (events, cards, distinct weeks and months of D; a cell is `thin` under "
           f"{THIN_EVENTS} events or {THIN_MONTHS} months)", "",
           L.md(counts_table([(k, samples[k]) for k in main_keys + hundred_keys] + ctrl_samples, "year")), "",
           "**Counts by card type** (same samples; cards with no card type cannot be in a hot month)", "",
           L.md(counts_table([(k, samples[k]) for k in main_keys[:1] + main_keys[3:4] + main_keys[5:] + hundred_keys[:1] + hundred_keys[3:4]], "card_type")), "",
           "**Counts by PSA 10 tier at the spike (prior-2 median)**", "",
           L.md(counts_table([(k, samples[k]) for k in main_keys + hundred_keys[:1]], "tier")), ""]
    sz = prim.ratio
    sec += [f"Primary sample (top-50, hot 15%, >= 50%): {len(prim)} events on {prim.asset_id.nunique()} cards, {prim.week.nunique()} weeks, {prim.month.nunique()} months; "
            f"median spike +{100 * sz.median():.0f}% (quartiles +{100 * sz.quantile(0.25):.0f}% / +{100 * sz.quantile(0.75):.0f}%), median prior-2 age {prim.prior_age.median():.0f} days, "
            f"median pre-spike PSA 10 reference ${prim.prev2.median():,.0f}, median PSA 9 pre level ${np.exp(prim.m9_pre.dropna()).median():,.0f}. "
            f"Top-50 any category: {len(anyc)} events, {anyc.asset_id.nunique()} cards, {anyc.month.nunique()} months.", ""]
    print("\n".join(sec), flush=True); lines += sec

    # ---------------------------------------------------------------- 2. PSA 9 path
    ps = [(k, samples[k]) for k in main_keys[:2] + ["top-50, hot 15% known at D (M-1), spike >= 50%"] + main_keys[2:]] + ctrl_samples + ctrl_samples_hot
    mkeys = [k for k in matched_keys if "50%" in k and "100%" not in k]
    sec = ["## 2. The PSA 9 path after the spike", "",
           "**PSA 9 level vs its pre-event level (D-60, D], by window: median / mean % (t across months) [events with a PSA 9 sale in the window, months]**", "",
           L.md(path_table(ps, "rel9")), "",
           "**The control that matters inside a hot month: the same event minus the median of up to 5 other cards of the SAME card type (top-50 pool for top-50 samples), "
           "no 50% spike candidate in (D-60, D], anchored on the same D. Per-event excess: median / mean (t across months) [events with a match having data, months]**", "",
           L.md(matched_table([(k, samples[k]) for k in mkeys])), "",
           "**Window closing: each window's median level as a share of the (D+30, D+60] level**", "",
           L.md(closing_curve([(k, samples[k]) for k in main_keys[:1] + main_keys[2:]] + ctrl_samples + ctrl_samples_hot)), "",
           "**Excess over the random non-spike dates on the same cards (same years; the second row's control is restricted to hot months): event median minus control median; paired by month, mean (t) [months]**", "",
           L.md(excess_vs([(k, samples[k]) for k in ("top-50, hot 15%, spike >= 50%", "top-50, hot 15%, spike >= 50%", "top-50, any category, spike >= 50%")],
                          ctrl_samples[:1] + ctrl_samples_hot + ctrl_samples[1:], "rel9")), "",
           "**By year, top-50 hot 15%, spike >= 50%** (PSA 9 median level per window, its excess over the matched same-type cards, and the 90-day trade)", "",
           L.md(path_by_year(prim)), "",
           "**Same at spike >= 100%**", "", L.md(path_table([(k, samples[k]) for k in hundred_keys], "rel9")), "",
           "**>= 100%, excess over matched same-type unspiked cards**", "", L.md(matched_table([(k, samples[k]) for k in matched_keys if "100%" in k])), "",
           "**PSA 9 / PSA 10 ratio vs pre-spike (ratio at D = minus the spike size); how much of the drop closed by each window, split into the PSA 9 rising and the PSA 10 giving back (medians, %)**", "",
           L.md(ratio_table([(k, samples[k]) for k in main_keys[:1] + main_keys[3:4] + main_keys[5:]] + [(k, samples[k]) for k in hundred_keys[:1]])), ""]
    print("\n".join(sec), flush=True); lines += sec

    # ---------------------------------------------------------------- 3. liquidity
    sec = ["## 3. Liquidity: can a PSA 9 even be bought in the first days?", "",
           "Share of events with at least one PSA 9 sale by day 3 / 7 / 14 / 30 after D, and the first buyable PSA 9 in each entry window vs the pre-event level (median).", "",
           L.md(liquidity_table(ps)), "", "**By PSA 10 tier, top-50 hot 15% and top-50 any category (>= 50%)**", "",
           L.md(liquidity_table([(f"top-50 hot 15%, {t}", prim[prim.tier == t]) for t in TIER_LABELS] + [(f"top-50 any, {t}", anyc[anyc.tier == t]) for t in TIER_LABELS])), ""]
    print("\n".join(sec), flush=True); lines += sec

    # ---------------------------------------------------------------- 4. trades
    t2 = time.time()
    trade_keys = [k for k in main_keys if k.startswith("top-50")] + [k for k in hundred_keys if k.startswith("top-50")] + ["top-50, hot 15% known at D (M-1), spike >= 50%"]
    dates = pd.concat([samples[k].date for k in trade_keys]).unique()
    dc = date_control(sales, uni_top, dates, cand_dates, ct_of, hot_sets, data_end)
    ctrl_med = {"hot15": control_medians(dc, "hot15")[0], "hot30": control_medians(dc, "hot30")[0], "any": control_medians(dc)[0]}
    ctrl_med_type = control_medians(dc, by_type=True)[0]
    n_ctrl_cards = control_medians(dc)[1]
    print(f"same-date control: {len(uni_top)} top-50 universe cards x {len(dates)} event dates in {time.time() - t2:.0f}s; "
          f"median eligible control trades per date (d1-3 entry, 90d hold): {n_ctrl_cards['net_0_3_90'].median():.0f}", flush=True)

    def ckey(label):
        return "hot30" if "hot 30%" in label else ("hot15" if "hot 15%" in label else "any")
    unhyped = ("same-date unhyped top-50", lambda label: ctrl_med[ckey(label)] if label.startswith("top-50") else None)
    same_type = ("same-date same-type unspiked top-50", lambda label: ctrl_med_type if label.startswith("top-50") else None)
    tsamples = [(k, samples[k]) for k in trade_keys] + [("non-top-50, hot 15%, spike >= 50%", samples["non-top-50, hot 15%, spike >= 50%"])] + ctrl_samples + ctrl_samples_hot
    tt = trade_table(tsamples, [unhyped, same_type])
    sec = ["## 4. Buying early vs late", "",
           f"Buy at the first PSA 9 sale in the entry window, sell at the first PSA 9 sale >= hold days after the purchase (within +{EXIT_GRACE}), {FEE:.0%} fee. "
           "`vs same-date unhyped top-50` = the trade minus the median trade (same entry window and hold, same date D) over top-50 universe cards with no 50% spike candidate in (D-60, D] "
           "and (for the hot samples) a card type NOT hot that month, i.e. the spike plus its category against the rest of the top-50; "
           "`vs same-date same-type unspiked top-50` = the same against top-50 cards of the event's own card type (hot too when the event is), i.e. the spike's own contribution. "
           "Both: `vs` = averaged within month, then across months (t, share of months ahead); `median vs` = the median per-event excess, which shows how much of the mean is a few multi-baggers. "
           "The random-date control rows are the same cards on non-spike dates.", "",
           L.md(tt, ".1f"), ""]
    for label, h in (("top-50, hot 15%, spike >= 50%", 90), ("top-50, any category, spike >= 50%", 90), ("top-50, hot 15%, spike >= 50%", 180), ("top-50, any category, spike >= 100%", 90)):
        sec += [f"**By year: {label}, hold {h}d**", "", L.md(trade_by_year(samples[label], h, [("unhyped", ctrl_med[ckey(label)]), ("same type", ctrl_med_type)]), ".1f"), ""]
    sec += ["**Paired: the same event bought early vs later, same exit rule (events with a PSA 9 sale in both entry windows)**", "",
            L.md(paired_entries([(k, samples[k]) for k in ("top-50, hot 15%, spike >= 50%", "top-50, any category, spike >= 50%", "non-top-50, any category, spike >= 50%")]), ".1f"), ""]
    print("\n".join(sec), flush=True); lines += sec

    # ---------------------------------------------------------------- 5. the PSA 10 itself
    sec = ["## 5. The PSA 10 after the spike", "",
           "PSA 10 median in the window vs the spike price (median %), and retention = (window level - prior-2 level) / (spike - prior-2): 100% = held the whole spike, 0% = back to the prior level "
           "(median over events with a PSA 10 sale in the window) [n].", "",
           L.md(retention_table([(k, samples[k]) for k in main_keys] + [(k, samples[k]) for k in hundred_keys[:1] + hundred_keys[3:4]])), ""]
    print("\n".join(sec), flush=True); lines += sec

    # ---------------------------------------------------------------- 6. sample sizes
    thin = []
    for k in main_keys + hundred_keys:
        q = samples[k]
        for y, g in q.groupby("year"):
            if len(g) < THIN_EVENTS or g.month.nunique() < THIN_MONTHS:
                thin.append(f"{k} / {y}: {len(g)} events, {g.month.nunique()} months")
    sec = ["## 6. Sample-size honesty", "",
           f"Cells with fewer than {THIN_EVENTS} events or {THIN_MONTHS} distinct months (from the year table above):", ""] + [f"- {t}" for t in thin] + ["",
           "Hot 30% and every >= 100% top-50 cell are listed above by year and card type; read any cell marked `thin` as a list of episodes, not an estimate.", ""]
    print("\n".join(sec), flush=True); lines += sec

    # ---------------------------------------------------------------- 7. Sid's three cards
    sec = ["## 7. Sid's three cards through the event finder", "",
           f"Every clean PSA 10 sale >= 50% above the median of the previous two, with the {args.max_prior_age}-day prior-age cap (spikes failing the cap are listed for reference). "
           "Windows still open at the data end say so.", ""]
    for aid in EXAMPLES:
        hdr, tbl = card_events(sales, aid, data_end, ct_of, hot_sets, top_ids, args.max_prior_age)
        sec += hdr + [""]
        if len(tbl):
            sec += [tbl.T.reset_index().rename(columns={"index": ""}).to_markdown(index=False), ""]
    print("\n".join(sec), flush=True); lines += sec

    out_md = OUT / f"psa9_spike{args.tag}.md"
    out_md.write_text("\n".join(lines))
    pd.to_pickle({"events": ev, "controls": ctrl, "samples": samples, "matched": mc, "ctrl_med": ctrl_med, "ctrl_med_type": ctrl_med_type},
                 args.cache_dir / f"psa9_spike_events{args.tag}.pkl")
    print(f"\nwrote {out_md} ({time.time() - t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
