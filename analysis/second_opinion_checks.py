#!/usr/bin/env python3
"""Checks run on 2026-09-15 against the external review pasted into the session
(costs, noise, venue conventions, spike reversal, new-set decay, exit
survivorship, robust doubling base rate, Wikipedia pageviews, Japanese lead).

    analysis/.venv/bin/python analysis/second_opinion_checks.py noise      # same-card sale-pair noise, venue ratios, coverage by venue
    analysis/.venv/bin/python analysis/second_opinion_checks.py signals    # spike reversal, card age, illiquid-exit share (English)
    analysis/.venv/bin/python analysis/second_opinion_checks.py baserate   # doubling with robust entry/exit + all-in cost model
    analysis/.venv/bin/python analysis/second_opinion_checks.py pageviews  # Wikipedia pageviews vs character breadth (fetches from wikimedia.org)
    analysis/.venv/bin/python analysis/second_opinion_checks.py japan      # Japanese-card heat -> English cards, and the reverse

Cost model used for "net": buyer pays 7.5% sales tax + $5 shipping on entry;
seller pays eBay 13.25% to $7,500 then 2.35%, $0.40 order fee, $5 shipping.
The 50%-off promotion on $1,000+ singles is not applied (conservative).
"""
import glob
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "analysis" / "out"
sys.path.insert(0, str(ROOT / "analysis"))
from catchup_study import japanese_ids  # noqa: E402

pd.set_option("display.width", 250)


def load_sales(years=("2023", "2024", "2025", "2026")):
    fr = []
    for p in sorted(glob.glob(str(ROOT / "history/sales/*.csv"))):
        if not Path(p).name[:4] in years:
            continue
        df = pd.read_csv(p, usecols=["asset_id", "date", "price", "grading_company", "grade", "source", "sale_type", "skipped_reason"],
                         dtype={"skipped_reason": "string", "sale_type": "string", "source": "string"})
        fr.append(df[(df.grading_company == "PSA") & (df.grade == 10.0) & df.skipped_reason.isna() & (df.price > 0)])
    s = pd.concat(fr, ignore_index=True)
    s["date"] = pd.to_datetime(s.date)
    return s.sort_values(["asset_id", "date"])


def noise():
    s = load_sales()
    s["lp"] = np.log(s.price)
    g = s.groupby("asset_id")
    s["prev_lp"], s["prev_date"], s["prev_type"] = g.lp.shift(), g.date.shift(), g.sale_type.shift()
    pr = s[(s.date - s.prev_date).dt.days <= 7].copy()
    pr["d"] = pr.lp - pr.prev_lp
    pr = pr[pr.d.abs() < np.log(4)]
    sd = pr.d.std() / np.sqrt(2)
    print(f"consecutive sales of the same card within 7 days: n={len(pr)}; per-sale noise sd {sd:.3f} in logs (~{(np.exp(sd) - 1) * 100:.0f}%)")
    pr["tier"] = pd.cut(np.exp(pr.prev_lp), [0, 100, 500, 2000, 1e9], labels=["<100", "100-500", "500-2k", "2k+"])
    print(pr.groupby("tier", observed=True).d.agg(n="size", noise_sd=lambda x: x.std() / np.sqrt(2)).round(3))
    print("same sale type vs different:")
    print(pr.groupby(pr.sale_type == pr.prev_type).d.agg(n="size", noise_sd=lambda x: x.std() / np.sqrt(2)).round(3))
    s["m"] = s.date.dt.to_period("M")
    src = s.source.fillna("?")
    s["src"] = np.select([src == "eBay", src.str.contains("PWCC"), src.str.contains("Goldin"), src.str.contains("Heritage")], ["eBay", "PWCC", "Goldin", "Heritage"], default=src)
    g = s.groupby(["asset_id", "m", "src"]).price.median().unstack()
    print("\nvenue price vs eBay, same card, same month (median ratio):")
    for v in ["PWCC", "Goldin", "Heritage", "Alt", "CardHobby", "Pristine Auction"]:
        if v in g:
            r = (g[v] / g["eBay"]).dropna()
            r = r[(r > 0.25) & (r < 4)]
            print(f"  {v:18s} n={len(r):6d}  {r.median():.3f}")
    s["h"] = s.date.dt.year.astype(str) + "H" + ((s.date.dt.month > 6).astype(int) + 1).astype(str)
    cov = s.groupby(["h", "src"]).size().unstack(fill_value=0)
    print("\nshare of PSA 10 sales by venue and half-year (%):")
    print((cov.div(cov.sum(1), axis=0) * 100).round(1)[[c for c in ["eBay", "PWCC", "Goldin", "Heritage", "Alt", "CardHobby"] if c in cov]])


def _panel_english():
    p = pd.read_csv(OUT / "panel.csv", parse_dates=["date"])
    p = p[~p.asset_id.isin(japanese_ids())].copy()
    p["y"] = p.date.dt.year
    return p


def _excess(q, tgt):
    q = q[q[tgt].notna()].copy()
    q["raw"] = q.groupby("date")[tgt].transform(lambda s: s.clip(*s.quantile([.01, .99]))).clip(-0.9, 3)
    q["ex"] = q.raw - q.groupby("date").raw.transform("median")
    return q


def _t_vs(q, col, base_label):
    base = q[q[col] == base_label].groupby("date").ex.mean()
    out = {}
    for g, sub in q.groupby(col, observed=True):
        d = (sub.groupby("date").ex.mean() - base).dropna()
        out[g] = d.mean() / d.std(ddof=1) * np.sqrt(len(d)) if len(d) > 2 and d.std(ddof=1) > 0 else np.nan
    return pd.Series(out)


def signals():
    p = _panel_english()
    for tgt in ["fwd_exec", "fwd_exec180"]:
        q = _excess(p, tgt)
        q["spike"] = np.select([(q.mom30 > 0.5) & (q.vol30 <= 2), (q.mom30 > 0.5) & (q.vol30 > 2), (q.mom30 > 0.2) & (q.mom30 <= 0.5)],
                               ["spike>50% thin (<=2 sales)", "spike>50% busy", "up 20-50%"], default="other")
        t = q.groupby("spike").ex.agg(n="size", mean="mean", median="median")
        t["t_vs_other"] = _t_vs(q, "spike", "other")
        print(f"\n== {tgt}: spike reversal (excess return) ==\n{t.round(3)}")
        q["age"] = pd.cut(q.age_years, [-1, 0, 1, 2, 4, 10, 20, 40], labels=["0y", "1y", "2y", "3-4y", "5-10y", "11-20y", "21y+"])
        t = q.groupby("age", observed=True).ex.agg(n="size", mean="mean", median="median")
        t["t_vs_5-10y"] = _t_vs(q, "age", "5-10y")
        print(f"\n== {tgt}: card age at t (new-set decay) ==\n{t.round(3)}")
        print("median excess by year:")
        print(q.pivot_table(index="y", columns="age", values="ex", aggfunc="median", observed=True).round(3))
    e = p[p.entry_price.notna()].copy()
    e["dec"] = (e.groupby("date").mom30.rank(pct=True) * 10).clip(upper=9.999) // 1
    print("\nshare of entered trades with no exit (fewer than 2 sales in the exit window), by mom30 decile (4-5 = stale cards):")
    print(pd.DataFrame({"60-90d": e.groupby("dec").fwd_exec.apply(lambda s: s.isna().mean()), "150-180d": e.groupby("dec").fwd_exec180.apply(lambda s: s.isna().mean())}).round(3).T)


def net_return(entry, exit_):
    cost = entry * 1.075 + 5
    fee = np.where(exit_ > 7500, 7500 * 0.1325 + (exit_ - 7500) * 0.0235, exit_ * 0.1325)
    return (exit_ - fee - 5 - 0.4) / cost - 1


def baserate(start="2025-06-01", end="2026-06-01"):
    from panel import load_clean_sales
    sales = load_clean_sales()
    sales = sales[~sales.asset_id.isin(japanese_ids())]
    T = pd.date_range(start, end, freq="MS").values.astype("datetime64[D]")
    day = np.timedelta64(1, "D")
    rows = []
    for aid, g in sales.groupby("asset_id"):
        d, p = g.date.values.astype("datetime64[D]"), g.price.values.astype(float)
        for t in T:
            hi = np.searchsorted(d, t, side="right")
            if hi - np.searchsorted(d, t - 180 * day, side="right") < 3 or hi - np.searchsorted(d, t - 60 * day, side="right") < 1:
                continue
            e_hi = np.searchsorted(d, t + 21 * day, side="right")
            x_lo, x_hi = np.searchsorted(d, t + 60 * day, side="right"), np.searchsorted(d, t + 90 * day, side="right")
            if e_hi - hi < 1 or x_hi - x_lo < 2:
                continue
            rows.append((t, p[hi], np.median(p[hi:e_hi]), np.median(p[x_lo:x_hi]), np.quantile(p[x_lo:x_hi], 0.4)))
    r = pd.DataFrame(rows, columns=["t", "first", "med_entry", "ex_med", "ex_q40"])
    r["first_sale"] = r.ex_med / r["first"] - 1
    r["robust"] = r.ex_q40 / r.med_entry - 1
    r["net"] = net_return(r.med_entry, r.ex_q40)
    print(f"English cards, monthly rebalances {start}..{end}, {len(r)} card-months with an entry and an exit")
    for col in ["first_sale", "robust", "net"]:
        print(f"  {col:10s} doubled {(r[col] >= 1).mean() * 100:5.2f}%   >= +30% {(r[col] >= 0.3).mean() * 100:5.1f}%   > 0 {(r[col] > 0).mean() * 100:5.1f}%   median {r[col].median() * 100:+.1f}%")
    r["tier"] = pd.cut(r.med_entry, [0, 100, 250, 500, 2000, 1e9], labels=["<100", "100-250", "250-500", "500-2k", "2k+"])
    print(r.groupby("tier", observed=True).net.agg(n="size", median="median", share_positive=lambda s: (s > 0).mean()).round(3))


ARTICLES = {"Pikachu": "Pikachu", "Charizard": "Charizard", "Mewtwo": "Mewtwo", "Lucario": "Lucario", "Mew": "Mew_(Pok%C3%A9mon)",
            "Eevee": "Eevee", "Gardevoir": "Gardevoir", "Greninja": "Greninja", "Dragonite": "Dragonite"}


def pageviews():
    w = pd.read_csv(OUT / "char_weekly.csv", parse_dates=["week"])
    cache = OUT / "pageviews"
    cache.mkdir(exist_ok=True)
    for ch, art in ARTICLES.items():
        f = cache / f"{ch}.json"
        if not f.exists():
            url = f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{art}/daily/20240101/{pd.Timestamp.today():%Y%m%d}"
            req = urllib.request.Request(url, headers={"User-Agent": "pokemon-psa10-research/0.1"})
            f.write_bytes(urllib.request.urlopen(req, timeout=60).read())
        items = json.loads(f.read_text())["items"]
        pv = pd.Series({pd.Timestamp(i["timestamp"][:8]): i["views"] for i in items}).sort_index()
        wk = pv.resample("W-MON", label="right", closed="right").sum()
        g = w[w.char == ch].set_index("week").sort_index().join(wk.rename("pv"), how="inner")
        g["surprise"] = np.log(g.pv) - np.log(g.pv).rolling(26, min_periods=8).median().shift(1)
        lead = {f"breadth t{k:+d}w": round(g.surprise.corr(g.breadth.shift(-k)), 2) for k in (-4, 0, 4, 8)}
        fwd = {f"fwd_med t{k:+d}w": round(g.surprise.corr(g.fwd_med.shift(-k)), 2) for k in (0, 4)}
        print(f"{ch:10s} weeks={len(g):3d} views {int(pv.sum()):>9,}  corr(view surprise, ...): {lead} {fwd}")
    print("Rayquaza, Umbreon, Salamence have no standalone article (redirects); Bulbapedia has no public pageview API.")


def japan():
    p = pd.read_csv(OUT / "panel.csv", parse_dates=["date"])
    p["jp"] = p.asset_id.isin(japanese_ids())
    for tgt in ["fwd_exec", "fwd_exec180"]:
        q = _excess(p[p.mom30.notna()], tgt)
        for src, dst, label in [(True, False, "JP heat -> EN cards"), (False, True, "EN heat -> JP cards")]:
            h = q[q.jp == src].groupby(["date", "subject"]).mom30.agg(n="count", k=lambda s: (s > 0.2).sum())
            h["heat"] = (h.k / h.n).where(h.n >= 5)
            d = q[q.jp == dst].merge(h[["heat"]], left_on=["date", "subject"], right_index=True, how="inner").dropna(subset=["heat"])
            ic = d.groupby("date").apply(lambda g: g.heat.corr(g.ex, method="spearman") if len(g) > 50 else np.nan).dropna()
            print(f"{tgt:12s} {label:22s} n={len(d):6d} months={len(ic):3d}  IC={ic.mean():+.3f} (t={ic.mean() / ic.std() * np.sqrt(len(ic)):.1f}, months>0 {(ic > 0).mean() * 100:.0f}%)")


if __name__ == "__main__":
    {"noise": noise, "signals": signals, "baserate": baserate, "pageviews": pageviews, "japan": japan}[sys.argv[1]]()
