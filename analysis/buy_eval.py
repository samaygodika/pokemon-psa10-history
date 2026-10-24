#!/usr/bin/env python3
"""One walk-forward harness for every buy-model candidate (2026-10-23), so they are compared on the
same trades, the same folds and the same metrics.

    analysis/.venv/bin/python analysis/buy_eval.py cache --panel panel_loose_2026-10-23.csv
    analysis/.venv/bin/python analysis/buy_eval.py baseline          # v0.1 cell model + the regime alone
    analysis/.venv/bin/python analysis/buy_eval.py evaluate [MODEL ...]   # every preds_*.csv.gz by default

Trades. The loose panel's English trades (buy_list_rates.prepare: other franchises out, regime /
tier / liquidity / cuts tagged), one row per (asset, monthly anchor), trade_id = "<asset_id>|<date>".
Labels per horizon h in (90, 180): win = mny{h} >= +30% net of every cost, net = mny{h} clipped
to [-100%, +300%] (the rates' own definitions). `eligible` = no avoid flag the panel can see
(release_year, busy_spike, vol_surge, below_band, dip, chase_flat), the trades the nightly could
list. Cached to analysis/out/buy_eval/trades.pkl with FEATURES, the tags and the labels only.

FEATURES are point-in-time (panel.py computes them from sales on or before the anchor; leak tests
in test_panel.py). Everything else in the panel is NOT a feature: fwd*, entry*, exit*, n_exit*,
exit_flag*, gross*, mny*, mkt_mny* / mkt_gross* / mkt_fwd* (the month's REALIZED medians),
price_bin / breakeven (from the entry after the anchor), scarcity_gap / pop_today (today's pop).

Folds. Cutoffs D = month starts from START (2023-01-01) to the data end. At D a model may train on
the trades closed by D (anchor <= D - (h + 30) d: their labels were known at D) and predicts the
trades that close by the next cutoff, as the monthly re-fit does. One prediction per trade per
horizon. split(u, h, D, nxt) gives (train, test).

LIVE protocol (live_folds, models named live_*): one fold per anchor month A, train = closed by A. This
is the honest forecast test (what the nightly could have said on A); evaluate() compares live_* models
with live_baseline. The cutoff protocol above is the monthly re-fit's check and flatters recency.

Predictions. save_preds(name, df) with columns horizon, cutoff, trade_id, p (probability of a win)
-> analysis/out/buy_eval/preds_<name>.csv.gz. evaluate() scores every model on the trades the
baseline AND it scored, per test month: Brier, log loss, AUC; Brier skill vs the regime alone and
vs the baseline; top-k by p among eligible trades (k = 25, 50, 100: win rate and mean net, what a
buyer of the model's top picks would have earned after costs); pooled calibration (ECE, by decile).
Summaries over all test months and over 2025+ anchors, and month-paired differences vs the
baseline with t (months as the unit: the trades of one month move together). Writes
analysis/out/buy_eval/report.md and summary.csv.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "history"))
sys.path.insert(0, str(ROOT / "analysis"))
import buy_list  # noqa: E402
import buy_list_rates as rates_mod  # noqa: E402

DIR = rates_mod.OUT / "buy_eval"
CACHE = DIR / "trades.pkl"
START = pd.Timestamp("2023-01-01")
HORIZONS = (90, 180)
TOPK = (25, 50, 100)
FEATURES = ["ref", "log_price", "mom30", "mom90", "mom180", "mom365", "vol30", "vol90", "vol365", "vol_surge", "days_since_sale",
            "n_sales_to_date", "dispersion", "bin_share90", "ebay_share90", "accel", "band_z", "age_years", "peer_resid", "mkt_mom30",
            "rel_mom30", "char_mom30", "char_n", "breadth", "char_jp_mom30", "char_jp_n", "set_price_q", "set_lead_mom90", "set_lead_n"]
TAGS = ["trade_id", "asset_id", "date", "subject", "set", "year", "variety", "era", "regime", "tier", "liq",
        "stale30", "jp_heat", "band_high", "tail_lead", "thin_history"]
LABELS = ["mny90", "mny180", "exit_flag90", "exit_flag180"]
AVOID = ("release_year", "busy_spike", "vol_surge_flag", "below_band", "dip", "chase_flat")
EPS = 1e-4


def avoid_columns(u):
    y = pd.to_numeric(u.year, errors="coerce")
    u["release_year"] = y >= u.date.dt.year - 1
    u["busy_spike"] = (u.mom30 >= 0.5) & (u.vol30 >= 3)
    u["vol_surge_flag"] = (u.vol30 >= 2 * np.maximum(u.vol365 / 12.0, 0.25)) & (u.vol30 >= 2)
    u["below_band"] = u.band_z <= -1
    u["dip"] = u.mom30 <= -0.2
    u["chase_flat"] = u.set_lead_mom90 < 0.10
    u["eligible"] = ~u[list(AVOID)].any(axis=1) & u.era.isin(["wotc", "vintage", "modern", "current"])
    return u


def build_cache(panel_name):
    panel = rates_mod.OUT / panel_name
    data_end = rates_mod.panel_data_end(panel)
    if data_end is None:
        sys.exit(f"{panel}: no .meta.json")
    u = rates_mod.prepare(pd.read_csv(panel, low_memory=False), pd.read_csv(ROOT / "history" / "assets.csv", usecols=["asset_id", "card_name"]))
    u["trade_id"] = u.asset_id + "|" + u.date.dt.strftime("%Y-%m-%d")
    u = avoid_columns(u)
    for c in ("regime", "tier", "liq"):       # plain strings; a missing tag stays NaN so the cell fit drops it as before
        u[c] = u[c].astype(object).where(u[c].notna(), np.nan)
    keep = TAGS + FEATURES + LABELS + list(AVOID) + ["eligible"]
    u = u[keep].reset_index(drop=True)
    DIR.mkdir(parents=True, exist_ok=True)
    u.to_pickle(CACHE)
    (DIR / "trades.meta.json").write_text(json.dumps({"panel": panel_name, "data_end": f"{data_end.date()}", "rows": len(u)}, indent=1) + "\n")
    print(f"cached {len(u):,} English trades ({u.eligible.sum():,} eligible) from {panel_name}, data end {data_end.date()} -> {CACHE}")


def load():
    """(trades, data_end)."""
    meta = json.load(open(DIR / "trades.meta.json"))
    return pd.read_pickle(CACHE), pd.Timestamp(meta["data_end"])


def cutoffs(data_end, start=START):
    """[(D, next cutoff)] for every month start D from start; the last pairs with data_end."""
    ds = list(pd.date_range(start, data_end, freq="MS"))
    return [(d, ds[i + 1] if i + 1 < len(ds) else data_end) for i, d in enumerate(ds) if (ds[i + 1] if i + 1 < len(ds) else data_end) > d]


def split(u, h, d, nxt):
    """(train, test) at horizon h: train = closed by d (labels known at d), test = closing in (d, nxt]; both with win / net."""
    train = rates_mod.closed(u, h, d)
    test = rates_mod.closed(u, h, nxt)
    lo = d - pd.Timedelta(days=h + 30)
    return train, test[test.date > lo]


def live_folds(u, h, data_end, start=START):
    """the LIVE protocol (2026-10-23, after the glm agent's lag finding): for every anchor month A from start whose
    trades have closed by data_end, (A, train, test) with train = the trades closed by A (anchor <= A - (h + 30) d:
    exactly what a buyer on A knew) and test = the trades anchored at A. split() instead trains on everything
    closed by the cutoff, so a 180-day trade of month A is scored with the labels of month A - 1, whose exits
    share five of its six months: right for the monthly re-fit's check, too kind for a live forecast."""
    w = rates_mod.closed(u, h, data_end)
    for a in sorted(w.date.unique()):
        a = pd.Timestamp(a)
        if a >= start:
            yield a, rates_mod.closed(u, h, a), w[w.date == a]


def save_preds(name, df):
    df = df[["horizon", "cutoff", "trade_id", "p"]].copy()
    df["cutoff"] = pd.to_datetime(df.cutoff).dt.strftime("%Y-%m-%d")
    DIR.mkdir(parents=True, exist_ok=True)
    path = DIR / f"preds_{name}.csv.gz"
    df.to_csv(path, index=False, float_format="%.5f")
    return path


def load_preds(name):
    return pd.read_csv(DIR / f"preds_{name}.csv.gz", dtype={"trade_id": str})


def run_baseline(live=False):
    """v0.1: the cell tables fitted at each cutoff (buy_list_rates.fit) scored by buy_list.score_cell; and the regime's rate alone.
    live: one fold per anchor month A, tables fitted on what was closed by A (live_folds) -> preds_live_baseline / preds_live_regime."""
    import tempfile
    u, data_end = load()
    out_b, out_r = [], []
    if live:
        anchors = sorted(set(a for h in HORIZONS for a, _, _ in live_folds(u, h, data_end)))
        plan = [(a, None) for a in anchors]
    else:
        plan = cutoffs(data_end)
    with tempfile.TemporaryDirectory() as tmp:
        for d, nxt in plan:
            cells, lifts, _, _ = rates_mod.fit(u, d)
            rates_mod.write_tables(cells, lifts, tmp)
            rates, lifts_d = buy_list.load_rates(Path(tmp) / "buy_list_rates.csv"), buy_list.load_lifts(Path(tmp) / "buy_list_lifts.csv")
            for h in HORIZONS:
                if live:
                    test = rates_mod.closed(u, h, data_end)
                    test = test[test.date == d]
                else:
                    _, test = split(u, h, d, nxt)
                for r in test.itertuples(index=False):
                    er, reg, ti, li = str(r.era), r.regime, r.tier, r.liq
                    if er not in ("wotc", "vintage", "modern", "current") or not isinstance(reg, str) or not isinstance(ti, str):
                        continue
                    cuts = [c for c in ("stale30", "jp_heat", "band_high", "thin_history") if bool(getattr(r, c))]
                    s = buy_list.score_cell(rates, lifts_d, h, reg, er, ti, li, cuts)
                    if s:
                        out_b.append((h, d, r.trade_id, s["p"]))
                    ref = rates.get((h, "regime", reg, "", "", ""))
                    if ref and ref["n"] >= buy_list.MIN_CELL_N:
                        out_r.append((h, d, r.trade_id, ref["win_rate"]))
            print(f"baseline: cutoff {d.date()} done", flush=True)
    for name, rows in ((("live_" if live else "") + "baseline", out_b), (("live_" if live else "") + "regime", out_r)):
        path = save_preds(name, pd.DataFrame(rows, columns=["horizon", "cutoff", "trade_id", "p"]))
        print(f"wrote {path} ({len(rows):,} predictions)")


def auc(y, s):
    """rank AUC (ties averaged); nan when one class is missing."""
    y = np.asarray(y, bool)
    n1, n0 = y.sum(), (~y).sum()
    if n1 == 0 or n0 == 0:
        return np.nan
    r = pd.Series(s).rank().values
    return (r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def month_metrics(g, ref_p=None, base_p=None):
    y, p = g.win.values.astype(float), np.clip(g.p.values, EPS, 1 - EPS)
    row = {"n": len(g), "win": y.mean(), "p_mean": p.mean(), "brier": ((p - y) ** 2).mean(),
           "logloss": -(y * np.log(p) + (1 - y) * np.log(1 - p)).mean(), "auc": auc(y, p)}
    if ref_p is not None:
        row["brier_regime"] = ((ref_p - y) ** 2).mean()
    if base_p is not None:
        row["brier_base"] = ((base_p - y) ** 2).mean()
    e = g[g.eligible]
    for k in TOPK:
        top = e.nlargest(k, "p") if len(e) >= k else e.iloc[0:0]
        row[f"top{k}_win"] = top.win.mean() if len(top) else np.nan
        row[f"top{k}_net"] = top.net.mean() if len(top) else np.nan
    return row


def tstat(x):
    x = pd.Series(x).dropna()
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 and x.std(ddof=1) > 0 else np.nan


def evaluate(models=None):
    u, data_end = load()
    names = models or sorted(p.name[len("preds_"):-len(".csv.gz")] for p in DIR.glob("preds_*.csv.gz"))
    refs = {"": (load_preds("baseline"), load_preds("regime"))}
    if (DIR / "preds_live_baseline.csv.gz").exists():
        refs["live_"] = (load_preds("live_baseline"), load_preds("live_regime"))
    rows, calib = [], []
    labels = {}
    for h in HORIZONS:
        w = rates_mod.closed(u, h, data_end)[["trade_id", "date", "win", "net", "eligible"]]
        labels[h] = w.set_index("trade_id")
    for name in names:
        pr = load_preds(name)
        base, reg = refs["live_" if name.startswith("live_") else ""]   # live_* models against the live baseline
        for h in HORIZONS:
            lab = labels[h]
            m = pr[pr.horizon == h].merge(base[base.horizon == h][["trade_id", "p"]].rename(columns={"p": "p_base"}), on="trade_id")
            m = m.merge(reg[reg.horizon == h][["trade_id", "p"]].rename(columns={"p": "p_reg"}), on="trade_id", how="left")
            m = m.join(lab, on="trade_id", how="inner")
            if m.empty:
                continue
            for month, g in m.groupby("date"):
                rows.append({"model": name, "horizon": h, "month": f"{month:%Y-%m}",
                             **month_metrics(g, ref_p=g.p_reg.fillna(g.p_base).values, base_p=g.p_base.values)})
            s = m.copy()
            s["bucket"] = pd.cut(s.p, np.linspace(0, 1, 11), include_lowest=True)
            for b, gb in s.groupby("bucket", observed=True):
                calib.append({"model": name, "horizon": h, "bucket": f"{b.left:.1f}-{b.right:.1f}", "n": len(gb), "p_mean": gb.p.mean(), "win": gb.win.mean()})
    mt = pd.DataFrame(rows)
    cal = pd.DataFrame(calib)
    summ = []
    for (name, h), g in mt.groupby(["model", "horizon"]):
        for period, gp in (("all", g), ("2025+", g[g.month >= "2025-01"])):
            if gp.empty:
                continue
            b = mt[(mt.model == ("live_baseline" if name.startswith("live_") else "baseline")) & (mt.horizon == h) & mt.month.isin(gp.month)].set_index("month")
            gq = gp.set_index("month")
            cg = cal[(cal.model == name) & (cal.horizon == h)]
            ece = (cg.n * (cg.p_mean - cg.win).abs()).sum() / cg.n.sum() if len(cg) else np.nan
            row = {"model": name, "horizon": h, "period": period, "months": len(gp), "trades": int(gp.n.sum()),
                   "brier": gp.brier.mean(), "skill_vs_regime": 1 - gp.brier.sum() / gp.brier_regime.sum(),
                   "skill_vs_base": 1 - gp.brier.sum() / gp.brier_base.sum(), "logloss": gp.logloss.mean(), "auc": gp.auc.mean(),
                   "ece_pooled_all": ece}
            d_brier = gq.brier - b.brier
            row["d_brier_t"] = tstat(-d_brier)
            for k in TOPK:
                row[f"top{k}_win"] = gp[f"top{k}_win"].mean()
                row[f"top{k}_net"] = gp[f"top{k}_net"].mean()
                dn = gq[f"top{k}_net"] - b[f"top{k}_net"]
                row[f"d_top{k}_net"] = dn.mean()
                row[f"d_top{k}_net_t"] = tstat(dn)
            summ.append(row)
    summ = pd.DataFrame(summ)
    DIR.mkdir(parents=True, exist_ok=True)
    summ.to_csv(DIR / "summary.csv", index=False, float_format="%.4f")
    mt.to_csv(DIR / "months.csv", index=False, float_format="%.4f")
    cal.to_csv(DIR / "calibration.csv", index=False, float_format="%.4f")
    md = [f"# Buy-model candidates, walk-forward from {START.date()} (data end {data_end.date()})\n\n",
          "Every model on the trades the baseline also scored; months = test anchor months; skill = 1 - Brier / Brier of the reference "
          "over the same trades; top-k = the k eligible trades with the highest p each month (win rate, mean net after costs); "
          "d_* = month-paired difference vs the baseline, t over months.\n\n"]
    for h in (180, 90):
        for period in ("all", "2025+"):
            t = summ[(summ.horizon == h) & (summ.period == period)].sort_values("brier")
            if t.empty:
                continue
            md.append(f"## {h} d, {period} ({int(t.months.max())} months)\n\n| model | Brier | skill vs regime | skill vs baseline | t | AUC | ECE | top-50 win | top-50 net | d top-50 net | t | top-100 net | d top-100 net | t |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
            for r in t.itertuples():
                md.append(f"| {r.model} | {r.brier:.4f} | {100 * r.skill_vs_regime:+.1f}% | {100 * r.skill_vs_base:+.1f}% | {r.d_brier_t:+.1f} | {r.auc:.3f} | {r.ece_pooled_all:.3f} | "
                          f"{100 * r.top50_win:.1f}% | {100 * r.top50_net:+.1f}% | {100 * r.d_top50_net:+.1f} | {r.d_top50_net_t:+.1f} | {100 * r.top100_net:+.1f}% | {100 * r.d_top100_net:+.1f} | {r.d_top100_net_t:+.1f} |\n")
            md.append("\n")
    (DIR / "report.md").write_text("".join(md))
    print("".join(md))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["cache", "baseline", "evaluate"])
    ap.add_argument("models", nargs="*")
    ap.add_argument("--panel", default="panel_loose_2026-10-23.csv")
    ap.add_argument("--live", action="store_true", help="baseline: the live protocol (live_folds) -> preds_live_*")
    a = ap.parse_args()
    if a.cmd == "cache":
        build_cache(a.panel)
    elif a.cmd == "baseline":
        run_baseline(live=a.live)
    else:
        evaluate(a.models or None)


if __name__ == "__main__":
    main()
