#!/usr/bin/env python3
"""Market-level candidates on top of the v0.1 cell model (2026-10-23), walk-forward through analysis/buy_eval.py.

    analysis/.venv/bin/python analysis/cand_market.py       # -> analysis/out/buy_eval/preds_market_*.csv.gz, market_months.csv
    analysis/.venv/bin/python analysis/buy_eval.py evaluate baseline regime market_nowcast market_nowcast4 \
        market_persist market_cont market_nowcast_lag market_persist_lag
    analysis/.venv/bin/python analysis/cand_market.py --live   # on preds_live_baseline -> preds_live_market_{persist,cont}, market_live_months.csv

The baseline's largest error is the level of a whole anchor month, not the order inside it: late-2024
"flat" months scored 3-5% at 180 d and won 12-19%, Dec 2025 (breadth 0.25, a pause) 6.8% and won
38.5%, mid-2025 warm months 4-7 points too high. Every variant here keeps the baseline's per-trade p
and moves the whole month on the log-odds scale by one delta, p' = sigmoid(logit(p) + delta), so the
order inside a month (AUC, top-k) is the baseline's and only the level changes.

Month features (fixed before any outcome was looked at), from the cross-section of English trades AT the
anchor (features only, point-in-time), over the breadth universe (reference >= $150, >= 3 sales in the
past year):
  breadth     share with a 30-day change > +5% (the nightly's; equals the cache's column)
  d_breadth3  breadth minus breadth three anchors earlier
  mom90_tm    10%-trimmed mean of the 90-day change (the median the brief named is exactly 0 in most
              2021-24 months: a median-of-3 reference rarely moves in a month, so it cannot rank them)
  band_hi     share >= 1 sd above the card's own 1-year band (of those with a band)
  stale       share with no sale in 30 days
  pulse       mean of vol30 / (vol365 / 12) (the median is discrete: vol30 is a small integer)
Months enter a fit from FIT_FROM (2021-10, the first anchor with a full year of sales behind vol365:
the pulse reads 12 / 10 / 5.5 / 3.2 in 2021-03..06 and settles at 1.0-1.6 from 2021-10) with >= MIN_MONTH_N
labelled trades. Month target = empirical logit of the month's win rate, (wins + 0.5) / (n + 1), over
its trades in the four eras with a tier and a regime (the baseline's universe before cell lookup).

Variants (all fixed up front, all reported):
  market_nowcast      ridge on standardized [breadth, mom90_tm] (lambda RIDGE_LAMBDA on the sum of squares
                      over months, intercept free), fitted at each cutoff D on the months closed by D
                      (anchor <= D - (h + 30) d), months as equal units; the prediction is clipped to
                      [min, max] of the training targets +- 1 logit (no wild extrapolation). delta solves
                      mean(p') = nowcast over the test month's trades.
  market_nowcast4     the same on [breadth, mom90_tm, band_hi, pulse].
  market_persist      no model: delta = SHRINK x the mean log-odds residual, logit(realized) - logit(mean
                      baseline p), of the last 3 baseline test months closed by D (its own out-of-sample
                      predictions; fewer when fewer exist, delta 0 with none).
  market_cont         breadth continuous instead of three buckets: at D the regime-level rates (trade-pooled
                      win rate of the closed trades per bucket, as buy_list_rates.fit's "regime" level) are
                      placed at the trade-weighted mean breadth of their bucket; the rate at the test month's
                      breadth is interpolated linearly in log-odds between those points (constant beyond the
                      outermost), and delta = logit(interpolated) - logit(the month's own bucket rate).
  market_nowcast_lag  market_nowcast fitted only on the months closed by the TEST ANCHOR A (anchor <= A -
  market_persist_lag  (h + 30) d), and persist on the baseline months closed by A: the information production
                      has when it buys at A. The harness's test anchor sits ONE month after its last training
                      anchor (D = 2025-09-01 at 180 d trains through 2025-02 and tests 2025-03), so "closed by D"
                      is 1 month stale relative to the trade, while the nightly's trailing information is ~7
                      months stale at 180 d (~4 at 90 d). The _lag twins measure what survives that.

Leak guards: every fit asserts its months are <= its cut; features use anchors <= A only; nothing reads a
label of a trade not closed at the cut. Writes analysis/out/buy_eval/market_months.csv (one row per horizon
and test month: features, realized, baseline mean, each variant's mean, months per fit, coefficients).

Timing check (added AFTER the variants above were scored, because both _lag twins lost: a reference, not a
candidate). market_base_lag = the baseline exactly as buy_eval.run_baseline builds it (rates_mod.fit +
buy_list.score_cell) but with the tables fitted at the test anchor A itself (closed = anchor <= A - (h + 30) d,
what the nightly holds when it buys at A) instead of at the harness cutoff; trades whose cell does not exist
yet stay unscored, as on the list. market_persist_blag = market_persist_lag's correction on top of it, from its
own months closed by A. Printed: each against market_base_lag on the trades both scored. ~2 min, 2.4 GB peak.

Result (2026-10-23, data end 2026-10-22; out/buy_eval/market_notes.md). Brier skill vs the baseline, 180 d all /
2025+ / 90 d all: nowcast -5.1% / -8.2% / -2.9%, nowcast4 -4.7% / -8.1% / -1.9%, persist +1.9% (t +2.7) / +1.4% /
+0.1%, cont +0.2% / -0.1% / +0.1%, nowcast_lag -9.1% / -13.0% / -4.6%, persist_lag -3.3% / -5.5% / -1.3%. AUC and
top-k unchanged by construction. The anchor's market features do not lead the next months' level (the fit learns
nothing from 2021-24, then overshoots early 2025 and reads the 2025-12 pause as cold before the 2026 run); persist
wins only on information production never has. Timing check: the baseline at production timing loses 5.5% Brier
at 180 d (t -2.6; AUC 0.661 -> 0.626, top-50 net -6.5 points), and there persist recovers +3.2% (t +2.3) at 180 d,
-1.1% at 90 d.

Live protocol (--live, buy_eval.live_folds; live_baseline == market_base_lag on every common trade): persist and
cont on top of live_baseline with only what was closed by A. live_market_persist: 180 d Brier +3.4% (t +2.3), 2025+
+4.8% (t +2.5), ECE 0.081 -> 0.060; 90 d -1.1% (t -0.9), 2025+ -1.5%. live_market_cont -0.1% / -0.1% / -0.2% / -0.3%
(nearly inert: one closed bucket most months). Ranking unchanged by construction.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import optimize, stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buy_eval as E  # noqa: E402
import buy_list_rates as rates_mod  # noqa: E402

FIT_FROM = pd.Timestamp("2021-10-01")
MIN_MONTH_N = 500
RIDGE_LAMBDA = 3.0
CLIP_LOGIT = 1.0
SHRINK = 0.5
PERSIST_K = 3
FEATS2 = ["breadth", "mom90_tm"]
FEATS4 = ["breadth", "mom90_tm", "band_hi", "pulse"]
FEATS_ALL = ["breadth", "d_breadth3", "mom90_tm", "band_hi", "stale", "pulse"]
ERAS = ("wotc", "vintage", "modern", "current")
EPS = 1e-4
VARIANTS = ["market_nowcast", "market_nowcast4", "market_persist", "market_cont", "market_nowcast_lag", "market_persist_lag"]


def logit(p):
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.asarray(x, float)))


def month_features(u):
    """one row per anchor date: FEATS_ALL from the English cross-section at that anchor (features only)."""
    liq = u.loc[(u.ref >= 150) & (u.vol365 >= 3), ["date", "mom30", "mom90", "band_z", "days_since_sale", "vol30", "vol365"]].copy()
    liq["pulse"] = liq.vol30 / (liq.vol365 / 12.0)
    g = liq.groupby("date")
    f = pd.DataFrame({
        "n_liq": g.size(),
        "breadth": g.mom30.agg(lambda s: (s > 0.05).mean()),
        "mom90_tm": g.mom90.agg(lambda s: stats.trim_mean(s.dropna(), 0.1) if s.notna().sum() >= 20 else np.nan),
        "band_hi": g.band_z.agg(lambda s: (s.dropna() > 1).mean() if s.notna().any() else np.nan),
        "stale": g.days_since_sale.agg(lambda s: (s >= 30).mean()),
        "pulse": g.pulse.mean(),
    }).sort_index()
    f["d_breadth3"] = f.breadth - f.breadth.shift(3)       # rows are consecutive month starts (checked in main)
    return f


def ridge_nowcast(train, xa, feats):
    """(prediction on the logit scale, coefficients) from training months (feats + y) for one test row xa."""
    x = train[feats].values
    mu, sd = x.mean(0), x.std(0, ddof=0)
    sd[sd == 0] = 1.0
    z = (x - mu) / sd
    y = train.y.values
    ym = y.mean()
    beta = np.linalg.solve(z.T @ z + RIDGE_LAMBDA * np.eye(len(feats)), z.T @ (y - ym))
    pred = ym + ((np.asarray(xa, float) - mu) / sd) @ beta
    return float(np.clip(pred, y.min() - CLIP_LOGIT, y.max() + CLIP_LOGIT)), beta


def solve_delta(lp, target):
    """delta with mean(sigmoid(lp + delta)) == target."""
    target = float(np.clip(target, EPS, 1 - EPS))
    return optimize.brentq(lambda d: sigmoid(lp + d).mean() - target, -15, 15)


def timing_check(u, data_end, base):
    """market_base_lag + market_persist_blag (module doc), and their paired comparison."""
    import tempfile
    sys.path.insert(0, str(E.ROOT / "history"))
    import buy_list  # noqa: E402
    ut = u.set_index("trade_id")
    bdate = pd.to_datetime(base.trade_id.str.split("|").str[1])
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for a in sorted(bdate.unique()):
            cells, lifts, _, _ = rates_mod.fit(u, a)       # closed by the anchor: what the nightly holds at A
            rates_mod.write_tables(cells, lifts, tmp)
            rates, lifts_d = buy_list.load_rates(Path(tmp) / "buy_list_rates.csv"), buy_list.load_lifts(Path(tmp) / "buy_list_lifts.csv")
            for h in E.HORIZONS:
                bt = base[(base.horizon == h) & (bdate == a)]
                for tid, d in zip(bt.trade_id.values, bt.cutoff.values):
                    r = ut.loc[tid]
                    cuts = [c for c in ("stale30", "jp_heat", "band_high", "thin_history") if bool(r[c])]
                    s = buy_list.score_cell(rates, lifts_d, h, r.regime, str(r.era), r.tier, r.liq, cuts)
                    if s:
                        rows.append((h, d, tid, s["p"], a))
            print(f"timing check: anchor {a:%Y-%m} done", flush=True)
    bl = pd.DataFrame(rows, columns=["horizon", "cutoff", "trade_id", "p", "date"])
    outp = []
    for h in E.HORIZONS:
        lab = rates_mod.closed(u, h, data_end).set_index("trade_id")[["win"]]
        g = bl[bl.horizon == h].join(lab, on="trade_id", how="inner")
        bm = g.groupby("date").agg(p_mean=("p", "mean"), rate=("win", "mean"))
        bm["resid"] = logit(bm.rate) - logit(bm.p_mean)
        for a, gt in g.groupby("date"):
            hist = bm[bm.index <= a - pd.Timedelta(days=h + 30)].tail(PERSIST_K)
            dv = SHRINK * hist.resid.mean() if len(hist) else 0.0
            outp.append(pd.DataFrame({"horizon": h, "cutoff": gt.cutoff.values, "trade_id": gt.trade_id.values,
                                      "p": np.clip(sigmoid(logit(gt.p.values) + dv), EPS, 1 - EPS)}))
    pb = pd.concat(outp, ignore_index=True)
    E.save_preds("market_base_lag", bl)
    E.save_preds("market_persist_blag", pb)
    print(f"wrote preds_market_base_lag ({len(bl):,}) and preds_market_persist_blag ({len(pb):,})")
    # paired: persist_blag vs base_lag; base_lag vs the harness baseline; on the trades all three scored
    hb = base.rename(columns={"p": "p_h"})[["horizon", "trade_id", "p_h"]]
    for h in (180, 90):
        lab = rates_mod.closed(u, h, data_end).set_index("trade_id")[["date", "win"]]
        m = bl[bl.horizon == h][["trade_id", "p"]].merge(pb[pb.horizon == h][["trade_id", "p"]].rename(columns={"p": "p_pb"}), on="trade_id")
        m = m.merge(hb[hb.horizon == h], on="trade_id").join(lab, on="trade_id", how="inner")
        y = m.win.astype(float)
        for c in ("p", "p_pb", "p_h"):
            m[f"b_{c}"] = (m[c] - y) ** 2
        mm = m.groupby("date")[["b_p", "b_p_pb", "b_p_h"]].mean()
        for period, q in (("all", mm), ("2025+", mm[mm.index >= "2025-01-01"])):
            print(f"{h} d {period} ({len(q)} months, {int(m.date.isin(q.index).sum()):,} trades): Brier harness baseline {q.b_p_h.mean():.4f}, "
                  f"base_lag {q.b_p.mean():.4f} (skill vs harness {100 * (1 - q.b_p.sum() / q.b_p_h.sum()):+.1f}%, t {E.tstat(q.b_p_h - q.b_p):+.1f}), "
                  f"persist_blag {q.b_p_pb.mean():.4f} (skill vs base_lag {100 * (1 - q.b_p_pb.sum() / q.b_p.sum()):+.1f}%, t {E.tstat(q.b_p - q.b_p_pb):+.1f})")


def live(u, data_end):
    """LIVE protocol (buy_eval.live_folds: one fold per anchor A, train = closed by A) on top of live_baseline:
    live_market_persist (SHRINK x mean residual of live_baseline's last PERSIST_K months closed by A) and
    live_market_cont (market_cont with the regime-level rates of the trades closed by A)."""
    lb = E.load_preds("live_baseline")
    lb["cutoff"] = pd.to_datetime(lb.cutoff)
    out = {"live_market_persist": [], "live_market_cont": []}
    table = []
    for h in E.HORIZONS:
        lag = pd.Timedelta(days=h + 30)
        lab_all = rates_mod.closed(u, h, data_end)
        b = lb[lb.horizon == h].join(lab_all.set_index("trade_id")[["date", "win", "breadth", "regime"]], on="trade_id", how="inner")
        bm = b.groupby("date").agg(p_mean=("p", "mean"), rate=("win", "mean"))
        bm["resid"] = logit(bm.rate) - logit(bm.p_mean)
        reg_tr = lab_all[lab_all.regime.notna()][["date", "regime", "breadth", "win"]]
        for a, gt in b.groupby("date"):
            cut = a - lag
            hist = bm[bm.index <= cut].tail(PERSIST_K)
            assert (hist.index <= cut).all()
            d_p = SHRINK * hist.resid.mean() if len(hist) else 0.0
            rt = reg_tr[reg_tr.date <= cut]
            pts = rt.groupby("regime").agg(c=("breadth", "mean"), r=("win", "mean"), n=("win", "size"))
            pts = pts[pts.n >= 100].sort_values("c")
            br, reg_a = float(gt.breadth.iloc[0]), str(gt.regime.iloc[0])
            d_c = float(np.interp(br, pts.c.values, logit(pts.r.values)) - logit(pts.loc[reg_a, "r"])) if reg_a in pts.index else 0.0
            lp = logit(gt.p.values)
            row = {"horizon": h, "month": f"{a:%Y-%m}", "regime": reg_a, "breadth": br, "trades": len(gt), "realized": gt.win.mean(),
                   "p_live_baseline": gt.p.mean(), "persist_months": len(hist)}
            for name, dv in (("live_market_persist", d_p), ("live_market_cont", d_c)):
                p2 = np.clip(sigmoid(lp + dv), EPS, 1 - EPS)
                out[name].append(pd.DataFrame({"horizon": h, "cutoff": gt.cutoff.values, "trade_id": gt.trade_id.values, "p": p2}))
                row[f"delta_{name}"], row[f"p_{name}"] = dv, p2.mean()
            table.append(row)
    for name, parts in out.items():
        path = E.save_preds(name, pd.concat(parts, ignore_index=True))
        print(f"wrote {path} ({sum(len(x) for x in parts):,} predictions)")
    t = pd.DataFrame(table)
    t.to_csv(E.DIR / "market_live_months.csv", index=False, float_format="%.4f")
    for h in (180, 90):
        for period, g in (("all", t[t.horizon == h]), ("2025+", t[(t.horizon == h) & (t.month >= "2025-01")])):
            print(f"{h} d {period} ({len(g)} months): " + "; ".join(
                f"{c[2:]} gap {100 * (g.realized - g[c]).mean():+.1f} abs {100 * (g.realized - g[c]).abs().mean():.1f}"
                for c in ("p_live_baseline", "p_live_market_persist", "p_live_market_cont")))
    # consistency: live_baseline should equal this module's market_base_lag where both scored (same construction)
    if (E.DIR / "preds_market_base_lag.csv.gz").exists():
        m = lb.merge(E.load_preds("market_base_lag"), on=["horizon", "trade_id"])
        print(f"live_baseline vs market_base_lag: {len(m):,} common predictions, max |diff| {(m.p_x - m.p_y).abs().max():.5f}")


def main():
    if "--live" in sys.argv:
        u, data_end = E.load()
        live(u, data_end)
        return
    u, data_end = E.load()
    feat = month_features(u)
    assert (feat.index.to_series().diff().dropna().dt.days.between(28, 31)).all(), "anchors are not consecutive months"
    base = E.load_preds("baseline")
    base["cutoff"] = pd.to_datetime(base.cutoff)
    out = {v: [] for v in VARIANTS}
    table = []
    for h in E.HORIZONS:
        lag = pd.Timedelta(days=h + 30)
        lab_all = rates_mod.closed(u, h, data_end)
        # month targets over the baseline's universe (before cell lookup); a month enters a fit only once closed at the cut
        uni = lab_all[lab_all.era.astype(str).isin(ERAS) & lab_all.tier.notna() & lab_all.regime.notna()]
        mon = uni.groupby("date").win.agg(wins="sum", n="size")
        mon["rate"] = mon.wins / mon.n
        mon["y"] = np.log((mon.wins + 0.5) / (mon.n - mon.wins + 0.5))
        mon = mon.join(feat)
        fit_ok = (mon.index >= FIT_FROM) & (mon.n >= MIN_MONTH_N) & mon[FEATS4].notna().all(axis=1).values
        months = mon[fit_ok]
        # regime-level rates and their breadth anchors come from every labelled trade with a regime (fit()'s "regime" level)
        reg_tr = lab_all[lab_all.regime.notna()][["date", "regime", "breadth", "win"]]
        # the baseline's own out-of-sample predictions, with labels: test trades and month residuals
        b = base[base.horizon == h].join(lab_all.set_index("trade_id")[["date", "win"]], on="trade_id", how="inner")
        bmon = b.groupby("date").agg(p_mean=("p", "mean"), rate=("win", "mean"), n=("p", "size"))
        bmon["resid"] = logit(bmon.rate) - logit(bmon.p_mean)
        test_dates = set(b.date.unique())
        for d, nxt in E.cutoffs(data_end):
            cut_d = d - lag
            anchors = [a for a in mon.index if cut_d < a <= nxt - lag]
            for a in anchors:
                xa = feat.loc[a]
                row = {"horizon": h, "cutoff": f"{d.date()}", "month": f"{a:%Y-%m}", **{k: xa[k] for k in FEATS_ALL},
                       "regime": str(uni.loc[uni.date == a, "regime"].iloc[0]) if (uni.date == a).any() else "",
                       "realized_all": mon.loc[a, "rate"], "n_all": int(mon.loc[a, "n"])}
                bt = b[(b.cutoff == d) & (b.date == a)]
                lp = logit(bt.p.values)
                row.update(n_scored=len(bt), realized=bt.win.mean() if len(bt) else np.nan, p_baseline=bt.p.mean() if len(bt) else np.nan)
                deltas = {}
                for name, cut in (("", cut_d), ("_lag", a - lag)):
                    tr = months[months.index <= cut]
                    assert (tr.index <= cut).all()
                    # nowcasts
                    for vname, feats in (("market_nowcast", FEATS2), ("market_nowcast4", FEATS4)):
                        if name and vname == "market_nowcast4":
                            continue
                        key = vname + name
                        if len(tr) >= len(feats) + 3:
                            q, beta = ridge_nowcast(tr, xa[feats].values, feats)
                            row[f"nowcast{key[len('market_nowcast'):]}"] = sigmoid(q)
                            row[f"months{key[len('market_nowcast'):]}"] = len(tr)
                            row[f"beta{key[len('market_nowcast'):]}"] = " ".join(f"{x:+.2f}" for x in beta)
                            if len(bt):
                                deltas[key] = solve_delta(lp, sigmoid(q))
                        else:
                            row[f"months{key[len('market_nowcast'):]}"] = len(tr)
                            deltas[key] = 0.0
                    # persist: the last PERSIST_K baseline test months closed at the cut
                    hist = bmon[bmon.index <= cut].tail(PERSIST_K)
                    deltas["market_persist" + name] = SHRINK * hist.resid.mean() if len(hist) else 0.0
                    row[f"persist_months{name}"] = len(hist)
                # cont: regime-level rates closed by D, interpolated in log-odds on breadth
                rt = reg_tr[reg_tr.date <= cut_d]
                assert (rt.date <= cut_d).all()
                pts = rt.groupby("regime", observed=True).agg(c=("breadth", "mean"), r=("win", "mean"), n=("win", "size"))
                pts = pts[pts.n >= 100].sort_values("c")
                reg_a = row["regime"]
                if len(pts) and reg_a in pts.index:
                    li = np.interp(xa["breadth"], pts.c.values, logit(pts.r.values))
                    deltas["market_cont"] = float(li - logit(pts.loc[reg_a, "r"]))
                    row["cont_rate"] = sigmoid(li)
                    row["bucket_rate"] = pts.loc[reg_a, "r"]
                else:
                    deltas["market_cont"] = 0.0
                for v in VARIANTS:
                    dv = deltas.get(v, 0.0)
                    row[f"delta_{v}"] = dv
                    if len(bt):
                        p2 = np.clip(sigmoid(lp + dv), EPS, 1 - EPS)
                        row[f"p_{v}"] = p2.mean()
                        out[v].append(pd.DataFrame({"horizon": h, "cutoff": d, "trade_id": bt.trade_id.values, "p": p2}))
                row["scored"] = a in test_dates
                table.append(row)
        print(f"{h} d: {sum(1 for r in table if r['horizon'] == h)} test months, nowcast fits on "
              f"{min(r['months'] for r in table if r['horizon'] == h)}-{max(r['months'] for r in table if r['horizon'] == h)} months "
              f"(lag twin {min(r['months_lag'] for r in table if r['horizon'] == h)}-{max(r['months_lag'] for r in table if r['horizon'] == h)})", flush=True)
    for v, parts in out.items():
        path = E.save_preds(v, pd.concat(parts, ignore_index=True))
        print(f"wrote {path} ({sum(len(x) for x in parts):,} predictions)")
    t = pd.DataFrame(table)
    t.to_csv(E.DIR / "market_months.csv", index=False, float_format="%.4f")
    print(f"wrote {E.DIR / 'market_months.csv'}")
    # month-level level errors (the thing this family targets), on the months the baseline scored
    s = t[t.scored]
    for h in (180, 90):
        for period, g in (("all", s[s.horizon == h]), ("2025+", s[(s.horizon == h) & (s.month >= "2025-01")])):
            cols = ["p_baseline"] + [f"p_{v}" for v in VARIANTS]
            parts = [f"{c[2:]} gap {100 * (g.realized - g[c]).mean():+.1f} abs {100 * (g.realized - g[c]).abs().mean():.1f}" for c in cols]
            print(f"{h} d {period} ({len(g)} months): " + "; ".join(parts))
    if "--no-timing" not in sys.argv:
        timing_check(u, data_end, base)


if __name__ == "__main__":
    main()
