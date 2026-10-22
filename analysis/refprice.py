#!/usr/bin/env python3
"""Reference price v2: does a venue- and sale-type-adjusted, smoothed reference beat the
median of the last three sales?

    analysis/.venv/bin/python analysis/refprice.py                  # -> analysis/out/refprice.md, refprice_factors.csv (sales cached in out/refprice_sales.pkl)
    analysis/.venv/bin/python analysis/refprice.py --q-daily 0.04   # a faster-moving Kalman level

Step 2 of handoff/2026-10-21-buy-model-strategy.md §5. The app's reference price ref(t)
is the median of the up-to-3 most recent clean PSA 10 sales in (t-180 d, t]
(history/metrics.py, panel.py). Per-sale noise is ~18% in logs and a monthly series of
that reference reverts about half-way the next month. The research (§8a) says: adjust each
sale for its sale type and venue (auctions close lower than posted prices, auction houses
record hammer) and shrink toward the previous value. This script builds the alternatives
point-in-time and measures them two ways:

  reversion   own-lag slope of the monthly log change of each reference series, pooled
              over cards (−0.5 = pure noise around a flat level, 0 = a random walk, i.e.
              no measurement noise); and the share of ≥ +30% monthly moves that give back
              half or more the next month.
  next sale   log error of the reference against the FIRST clean sale strictly after the
              anchor (within NEXT_DAYS): bias, MAE, RMSE, share of misses beyond 30%, by
              price tier, liquidity and year; paired share of rows where the alternative
              is closer than med3. Two targets: the raw next sale with no knowledge of its
              type ("raw"), and the next sale with its own category factor taken out
              ("type-matched": the adjusted reference plus the next sale's factor).

References (from sales dated <= t only; factors f_c for a sale in calendar year Y are
estimated from sales dated before Y, so no factor ever saw the sale it adjusts):
  med3     median of the last <= 3 raw sales in 180 d (the app's reference)
  med5     median of the last <= 5 raw sales in 180 d
  adj3     median of the last <= 3 category-adjusted sales in 180 d
  ewma     exponentially weighted mean of the adjusted log prices in 365 d, half-life HALF_LIFE d
  kalman   local-level filter over every adjusted sale to t: level = random walk with daily
           variance q², observation variance by price tier (the within-7-day noise study:
           sd 0.21 / 0.15 / 0.12 in logs under $100 / $100-500 / above); the estimate at t
           is the filtered level after the last sale
  carried  adj3 carried from the card's last sale to t with the era x language index
           (median month-on-month change of adj3 across cards with >= 2 sales in both
           180-day windows, >= 20 cards, else 0)

Category = sale type on eBay (auction / buy it now / best offer), venue elsewhere (PWCC
weekly or premier auctions, PWCC fixed price, Alt, CardHobby, Goldin / Heritage / Lelands,
other). Factors: within each (asset, calendar month) with sales in >= 2 categories, the
deviation of log price from the asset-month mean regressed on within-group-demeaned
category dummies, by price tier of the asset-month; base = eBay auction.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "history"))
sys.path.insert(0, str(ROOT / "analysis"))
from metrics import drop_outliers  # noqa: E402
from panel import language_tag, ERA_BINS, ERA_LABELS  # noqa: E402

OUT = ROOT / "analysis" / "out"
MIN_TOTAL_SALES = 4
ANCHOR_START, ANCHOR_END = "2021-03-01", "2026-08-01"
FACTOR_YEARS = range(2021, 2027)
NEXT_DAYS = 60
HALF_LIFE = 45.0
TIER_EDGES = [0, 100, 500, 2000, np.inf]
TIER_LABELS = ["<$100", "$100-500", "$500-2k", "$2k+"]
NOISE_SD = {"<$100": 0.21, "$100-500": 0.15, "$500-2k": 0.12, "$2k+": 0.12}
CATS = ["ebay_auction", "ebay_bin", "ebay_offer", "pwcc_auction", "pwcc_fixed", "alt", "cardhobby", "major_auction", "other"]
METHODS = ["med3", "med5", "adj3", "ewma", "kalman", "carried"]
LIQ_EDGES = [0, 1, 3, 7, np.inf]
LIQ_LABELS = ["1 sale/180d", "2-3", "4-7", "8+"]
DAY = np.timedelta64(1, "D")


def category(source, sale_type):
    src = source.fillna("").str.lower()
    st = sale_type.fillna("")
    cat = pd.Series("other", index=source.index)
    cat[src.eq("ebay") & st.eq("AUCTION")] = "ebay_auction"
    cat[src.eq("ebay") & st.eq("BUY_IT_NOW")] = "ebay_bin"
    cat[src.eq("ebay") & st.eq("BEST_OFFER")] = "ebay_offer"
    cat[src.str.contains("pwcc weekly|pwcc premier") | src.eq("pwcc")] = "pwcc_auction"
    cat[src.str.contains("pwcc fixed")] = "pwcc_fixed"
    cat[src.eq("alt")] = "alt"
    cat[src.eq("cardhobby")] = "cardhobby"
    cat[src.str.contains("goldin|heritage|lelands|robert edwards")] = "major_auction"
    return cat


def load_sales(cache):
    if cache.exists():
        return pd.read_pickle(cache)
    frames = []
    for p in sorted((ROOT / "history" / "sales").glob("*.csv")):
        df = pd.read_csv(p, usecols=["asset_id", "date", "price", "grading_company", "grade", "source", "sale_type", "skipped_reason"],
                         dtype={"skipped_reason": "string", "sale_type": "string", "source": "string"})
        frames.append(df)
    s = pd.concat(frames, ignore_index=True)
    s = s[(s.grading_company == "PSA") & (s.grade == 10.0) & s.skipped_reason.isna()]
    s = s[pd.to_numeric(s.price, errors="coerce") > 0]
    s["date"] = pd.to_datetime(s["date"], errors="coerce")
    s = s.dropna(subset=["date"]).sort_values(["asset_id", "date"]).reset_index(drop=True)
    keep_idx = []
    for aid, g in s.groupby("asset_id", sort=False):
        if len(g) < MIN_TOTAL_SALES:
            continue
        tuples = list(zip(g.date.dt.date, g.price.astype(float), g.source.fillna("")))
        kept, _ = drop_outliers(tuples)
        kept_set = set((d, p) for d, p, _ in kept)
        keep_idx.extend(i for i, (d, p, _) in zip(g.index, tuples) if (d, p) in kept_set)
    s = s.loc[keep_idx, ["asset_id", "date", "price", "source", "sale_type"]].copy()
    s["cat"] = category(s.source, s.sale_type)
    s["lp"] = np.log(s.price.astype(float))
    s = s.reset_index(drop=True)
    s.to_pickle(cache)
    return s


def estimate_factors(s):
    """{year: DataFrame tier x cat} from sales before that year (2019 on)."""
    h = s[s.date >= "2019-01-01"].copy()
    h["ym"] = h.date.dt.to_period("M")
    out = {}
    rows = []
    for y in FACTOR_YEARS:
        hy = h[h.date < f"{y}-01-01"].copy()
        g = hy.groupby(["asset_id", "ym"])
        hy["gmean"] = g.lp.transform("mean")
        hy["ncat"] = g.cat.transform("nunique")
        hy = hy[hy.ncat >= 2].copy()
        hy["tier"] = pd.cut(np.exp(hy.gmean), TIER_EDGES, labels=TIER_LABELS)
        tab = pd.DataFrame(index=TIER_LABELS, columns=CATS, dtype=float)
        for tier, ht in hy.groupby("tier", observed=True):
            yv = (ht.lp - ht.gmean).values
            D = pd.get_dummies(ht.cat).reindex(columns=CATS, fill_value=0).astype(float)
            D = D - D.groupby([ht.asset_id.values, ht.ym.values]).transform("mean")   # within-group demeaned dummies
            X = D.values
            use = [i for i, c in enumerate(CATS) if ht.cat.eq(c).sum() >= 30]
            beta = np.zeros(len(CATS))
            if len(use) >= 2:
                b, *_ = np.linalg.lstsq(X[:, use], yv, rcond=None)
                beta[use] = b
            beta = beta - beta[0]                      # base: eBay auction = 0
            for i, c in enumerate(CATS):
                n = int(ht.cat.eq(c).sum())
                tab.loc[tier, c] = beta[i] if (i in use) else np.nan
                rows.append({"year": y, "tier": tier, "cat": c, "factor": beta[i] if i in use else np.nan, "n_sales": n, "n_groups": int(ht[ht.cat == c].groupby(["asset_id", "ym"]).ngroups)})
        out[y] = tab
    return out, pd.DataFrame(rows)


def apply_factors(s, factors):
    s = s.copy()
    s["year"] = s.date.dt.year.clip(lower=min(FACTOR_YEARS), upper=max(FACTOR_YEARS))
    s["tier"] = pd.cut(s.price.astype(float), TIER_EDGES, labels=TIER_LABELS)
    f = np.zeros(len(s))
    for y, tab in factors.items():
        m = (s.year == y).values
        if not m.any():
            continue
        vals = tab.stack(future_stack=True)
        key = pd.MultiIndex.from_arrays([s.tier[m].astype(str), s.cat[m]])
        f[m] = vals.reindex(key).fillna(0.0).values
    s["f"] = f
    s["la"] = s.lp - s.f
    return s


def kalman_levels(d, la, tiers, q_daily):
    """filtered local level after each sale (log), random walk with daily variance q_daily^2."""
    n = len(d)
    x = np.empty(n)
    p = NOISE_SD[tiers[0]] ** 2
    x[0] = la[0]
    for i in range(1, n):
        dt = float((d[i] - d[i - 1]) / DAY)
        p = p + (q_daily ** 2) * dt
        r = NOISE_SD[tiers[i]] ** 2
        k = p / (p + r)
        x[i] = x[i - 1] + k * (la[i] - x[i - 1])
        p = (1.0 - k) * p
    return x


def build_rows(s, anchors, q_daily):
    T = np.array(anchors, dtype="datetime64[D]")
    rows = []
    w_ln2 = np.log(2.0) / HALF_LIFE
    for aid, g in s.groupby("asset_id", sort=False):
        d = g.date.values.astype("datetime64[D]")
        lp = g.lp.values
        la = g.la.values
        cat = g.cat.values
        f = g.f.values
        tiers = g.tier.astype(str).values
        kal = kalman_levels(d, la, tiers, q_daily)
        upto = np.searchsorted(d, T, side="right")
        for k, t in enumerate(T):
            hi = upto[k]
            lo180 = np.searchsorted(d, t - 180 * DAY, side="right")
            n180 = hi - lo180
            if n180 < 1:
                continue
            lo365 = np.searchsorted(d, t - 365 * DAY, side="right")
            ages = (t - d[lo365:hi]) / DAY
            w = np.exp(-w_ln2 * ages.astype(float))
            hi_next = np.searchsorted(d, t + NEXT_DAYS * DAY, side="right")
            has_next = hi_next > hi
            rows.append({
                "asset_id": aid, "date": pd.Timestamp(t), "n180": n180, "days_since": float((t - d[hi - 1]) / DAY),
                "last_sale": pd.Timestamp(d[hi - 1]),
                "med3": float(np.median(lp[max(lo180, hi - 3):hi])),
                "med5": float(np.median(lp[max(lo180, hi - 5):hi])),
                "adj3": float(np.median(la[max(lo180, hi - 3):hi])),
                "ewma": float((w * la[lo365:hi]).sum() / w.sum()),
                "kalman": float(kal[hi - 1]),
                "next_lp": float(lp[hi]) if has_next else np.nan,
                "next_la": float(la[hi]) if has_next else np.nan,
                "next_cat": cat[hi] if has_next else "",
                "next_days": float((d[hi] - t) / DAY) if has_next else np.nan,
            })
    return pd.DataFrame(rows)


def add_carried(rows, assets):
    """era x language index from adj3 month-on-month changes, then carried = adj3 at the last sale + index drift."""
    r = rows.merge(assets, on="asset_id", how="left")
    r["ym"] = r.date.dt.to_period("M")
    r = r.sort_values(["asset_id", "date"])
    prev = r.groupby("asset_id")[["adj3", "ym", "n180"]].shift(1)
    consec = (r.ym - prev.ym).apply(lambda x: getattr(x, "n", np.nan)) == 1
    ok = consec & (r.n180 >= 2) & (prev.n180 >= 2)
    chg = (r.adj3 - prev.adj3).where(ok)
    grp = r.assign(chg=chg).dropna(subset=["chg"]).groupby(["grp", "ym"]).chg.agg(["median", "size"])
    grp["median"] = grp["median"].where(grp["size"] >= 20, 0.0)
    idx = grp["median"].groupby(level=0).cumsum().rename("index")
    r = r.join(idx, on=["grp", "ym"])
    r["index"] = r["index"].fillna(0.0)
    r["last_ym"] = r.last_sale.dt.to_period("M")
    r = r.join(idx.rename("index_last"), on=["grp", "last_ym"])
    r["index_last"] = r["index_last"].fillna(r["index"])
    r["carried"] = r.adj3 + (r["index"] - r["index_last"])
    return r.drop(columns=["ym", "last_ym"])


def reversion(r, m):
    x = r.sort_values(["asset_id", "date"])
    cur = x[m]
    prev = x.groupby("asset_id")[m].shift(1)
    prev_ym = x.groupby("asset_id").date.shift(1)
    consec = ((x.date.dt.year - prev_ym.dt.year) * 12 + (x.date.dt.month - prev_ym.dt.month)) == 1
    d1 = (cur - prev).where(consec)
    d0 = d1.groupby(x.asset_id).shift(1)
    consec2 = consec & consec.groupby(x.asset_id).shift(1).fillna(False).astype(bool)
    pair = pd.DataFrame({"d1": d1, "d0": d0})[consec2].dropna()
    slope = np.polyfit(pair.d0, pair.d1, 1)[0] if len(pair) > 100 else np.nan
    corr = pair.d0.corr(pair.d1)
    sd = d1.dropna().std()
    big = pair[pair.d0 >= np.log(1.3)]
    giveback = (big.d1 <= -0.5 * big.d0).mean() if len(big) else np.nan
    return {"method": m, "pairs": len(pair), "own_lag_slope": slope, "corr": corr, "monthly_sd": sd, "moves_30up": len(big), "giveback_half": giveback, "share_abs_chg_gt30": (d1.abs() >= np.log(1.3)).mean()}


def errors(e):
    return {"n": int(e.notna().sum()), "bias": e.mean(), "MAE": e.abs().mean(), "RMSE": np.sqrt((e ** 2).mean()), "miss30": (e.abs() > np.log(1.3)).mean()}


def fmt(x, d=3):
    return "" if pd.isna(x) else f"{x:.{d}f}"


def md(df, cols, fmts):
    head = "| " + " | ".join(str(c) for c in cols) + " |\n|" + "|".join("---" for _ in cols) + "|\n"
    body = "".join("| " + " | ".join(fmts[c](r[c]) if c in fmts else str(r[c]) for c in cols) + " |\n" for _, r in df.iterrows())
    return head + body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q-daily", type=float, default=0.02, help="Kalman level random-walk sd per day (0.02 ~ 11%% a month)")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    s = load_sales(OUT / "refprice_sales.pkl")
    print(f"{len(s):,} clean PSA 10 sales on {s.asset_id.nunique():,} assets, {s.date.min().date()} .. {s.date.max().date()}")
    print("categories:", s.cat.value_counts().to_dict())
    factors, ftab = estimate_factors(s)
    ftab.to_csv(OUT / f"refprice_factors{a.tag}.csv", index=False)
    s = apply_factors(s, factors)
    assets = pd.read_csv(ROOT / "history" / "assets.csv", usecols=["asset_id", "year", "set", "variety", "card_name"], dtype={"variety": str})
    assets["lang"] = (assets.variety.fillna("") + " " + assets.set.fillna("")).map(language_tag)
    assets["era"] = pd.cut(pd.to_numeric(assets.year, errors="coerce"), ERA_BINS, labels=ERA_LABELS).astype(str)
    assets["grp"] = assets.era + "|" + assets.lang
    other = assets.card_name.str.contains(r"lorcana|one piece|weiss schwarz|magic the gathering|yu-gi-oh|dragon ball|digimon|disney", case=False, regex=True, na=False)
    s = s[~s.asset_id.isin(set(assets.loc[other, "asset_id"]))]
    anchors = pd.date_range(ANCHOR_START, ANCHOR_END, freq="MS")
    print(f"building {len(anchors)} monthly anchors …")
    rows = build_rows(s, anchors, a.q_daily)
    rows = add_carried(rows, assets[["asset_id", "grp", "lang"]])
    rows["tier"] = pd.cut(np.exp(rows.med3), TIER_EDGES, labels=TIER_LABELS)
    rows["liq"] = pd.cut(rows.n180, LIQ_EDGES, labels=LIQ_LABELS)
    rows["yr"] = rows.date.dt.year
    en = rows[rows.lang == "english"].copy()
    print(f"{len(rows):,} card-months, {len(en):,} English; with a next sale within {NEXT_DAYS} d: {en.next_lp.notna().sum():,}")

    L = [f"# Reference price v2 (q_daily {a.q_daily}, half-life {HALF_LIFE:.0f} d, next sale within {NEXT_DAYS} d)\n\n",
         f"{len(s):,} clean PSA 10 sales (assets with ≥ {MIN_TOTAL_SALES}), anchors {anchors[0].date()} .. {anchors[-1].date()}; "
         f"English card-months with a sale in 180 d: {len(en):,} on {en.asset_id.nunique():,} cards; {en.next_lp.notna().sum():,} have a next sale within {NEXT_DAYS} d.\n\n",
         "## Category factors (log, vs eBay auction = 0; estimated from the years before, applied in the year named)\n\n"]
    for y in (2022, 2024, 2026):
        t = factors[y].copy()
        t.index.name = "tier"
        L.append(f"**applied in {y}** (from sales 2019 .. {y - 1})\n\n" + md(t.reset_index(), ["tier"] + CATS, {c: (lambda x: fmt(x, 3)) for c in CATS}) + "\n")
    cnt = ftab[ftab.year == 2026].pivot(index="tier", columns="cat", values="n_sales").reindex(columns=CATS)
    cnt.index.name = "tier"
    L.append("Sales behind the 2026 estimate (asset-months with ≥ 2 categories):\n\n" + md(cnt.reset_index(), ["tier"] + CATS, {c: (lambda x: f"{int(x):,}" if pd.notna(x) else "") for c in CATS}) + "\n")

    L.append("## Reversion of the monthly series (English, consecutive month-starts)\n\n`own_lag_slope`: Δ_t on Δ_{t−1}; −0.5 is pure noise, 0 a random walk. `giveback_half`: of the months a reference rose ≥ 30%, the share that fell back by at least half the next month.\n\n")
    rev = pd.DataFrame([reversion(en, m) for m in METHODS])
    L.append(md(rev, ["method", "pairs", "own_lag_slope", "corr", "monthly_sd", "share_abs_chg_gt30", "moves_30up", "giveback_half"],
                {"pairs": lambda x: f"{int(x):,}", "own_lag_slope": fmt, "corr": fmt, "monthly_sd": fmt, "share_abs_chg_gt30": lambda x: f"{100*x:.1f}%", "moves_30up": lambda x: f"{int(x):,}", "giveback_half": lambda x: f"{100*x:.1f}%"}) + "\n")
    L.append("By liquidity (own-lag slope / giveback share):\n\n")
    rl = []
    for liq, g in en.groupby("liq", observed=True):
        rec = {"liquidity": liq}
        for m in METHODS:
            r_ = reversion(g, m)
            rec[m] = f"{fmt(r_['own_lag_slope'], 2)} / {100 * r_['giveback_half']:.0f}%" if pd.notna(r_["giveback_half"]) else fmt(r_["own_lag_slope"], 2)
        rl.append(rec)
    L.append(md(pd.DataFrame(rl), ["liquidity"] + METHODS, {}) + "\n")

    nx = en[en.next_lp.notna()].copy()
    L.append(f"## Error against the next sale (English, {len(nx):,} card-months with a sale within {NEXT_DAYS} d; logs)\n\n"
             "`raw` = log(next sale) − reference, the next sale's type unknown; `type-matched` = the next sale adjusted by its own category factor minus the reference (for med3 / med5 the raw reference is used in both). `closer than med3` = paired share of rows where |error| is smaller than med3's on the same target.\n\n")
    tabs = []
    for target, col in (("raw", "next_lp"), ("type-matched", "next_la")):
        base = (nx[col] - nx.med3).abs()
        for m in METHODS:
            e = nx[col] - nx[m]
            rec = {"target": target, "method": m, **errors(e), "closer_than_med3": (e.abs() < base).mean() if m != "med3" else np.nan}
            tabs.append(rec)
    L.append(md(pd.DataFrame(tabs), ["target", "method", "n", "bias", "MAE", "RMSE", "miss30", "closer_than_med3"],
                {"n": lambda x: f"{int(x):,}", "bias": fmt, "MAE": fmt, "RMSE": fmt, "miss30": lambda x: f"{100*x:.1f}%", "closer_than_med3": lambda x: "" if pd.isna(x) else f"{100*x:.1f}%"}) + "\n")
    for by in ("tier", "liq", "yr"):
        L.append(f"MAE on the type-matched next sale by {by} (med3 uses the raw reference):\n\n")
        rr = []
        for key, g in nx.groupby(by, observed=True):
            rec = {by: key, "n": len(g)}
            for m in METHODS:
                rec[m] = (g.next_la - g[m]).abs().mean()
            rr.append(rec)
        L.append(md(pd.DataFrame(rr), [by, "n"] + METHODS, {"n": lambda x: f"{int(x):,}", **{m: fmt for m in METHODS}}) + "\n")
    # after a jump: rows where med3 rose >= 30% vs the previous month
    x = en.sort_values(["asset_id", "date"])
    prev = x.groupby("asset_id").med3.shift(1)
    prev_date = x.groupby("asset_id").date.shift(1)
    consec = ((x.date.dt.year - prev_date.dt.year) * 12 + (x.date.dt.month - prev_date.dt.month)) == 1
    jump = x[consec & ((x.med3 - prev) >= np.log(1.3)) & x.next_lp.notna()]
    L.append(f"## After a ≥ 30% jump in med3 ({len(jump):,} card-months with a next sale): what each reference said, and what printed\n\n")
    jr = []
    for m in METHODS:
        e = jump.next_la - jump[m]
        jr.append({"method": m, "ref vs med3 (median, log)": (jump[m] - jump.med3).median(), **errors(e)})
    L.append(md(pd.DataFrame(jr), ["method", "ref vs med3 (median, log)", "n", "bias", "MAE", "RMSE", "miss30"],
                {"ref vs med3 (median, log)": fmt, "n": lambda x: f"{int(x):,}", "bias": fmt, "MAE": fmt, "RMSE": fmt, "miss30": lambda x: f"{100*x:.1f}%"}) + "\n")
    L.append(f"\nShare of the jump that printed: median of (next sale − previous med3) / (med3 − previous med3) = "
             f"{((jump.next_la - prev[jump.index]) / (jump.med3 - prev[jump.index])).median():.2f} (type-matched), "
             f"{((jump.next_lp - prev[jump.index]) / (jump.med3 - prev[jump.index])).median():.2f} (raw).\n")
    (OUT / f"refprice{a.tag}.md").write_text("".join(L))
    en[["asset_id", "date", "n180", "days_since", "tier", "liq"] + METHODS + ["next_lp", "next_la", "next_cat", "next_days"]].to_csv(OUT / f"refprice_rows{a.tag}.csv", index=False)
    print("".join(L[:12]))
    print(f"wrote {OUT / f'refprice{a.tag}.md'}")


if __name__ == "__main__":
    main()
