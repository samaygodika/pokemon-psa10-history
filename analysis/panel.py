#!/usr/bin/env python3
"""Point-in-time feature/target panel for the trending model.

    analysis/.venv/bin/python analysis/panel.py            # -> analysis/out/panel.csv

One row per (asset, rebalance date). Every feature is computed from PSA 10
sales dated ON OR BEFORE the rebalance date; every target from sales dated
strictly AFTER it. That is the whole point of this file: the backtest is
only honest if nothing here peeks forward, so the split is enforced by
construction (searchsorted on the sale-date array) and checked by
test_panel.py with planted future sales.

Universe at a date t: assets with >= MIN_SALES_180 clean PSA 10 sales in
(t-180d, t] and >= 1 in (t-60d, t]. Illiquid cards have no measurable
"price", so they are neither features nor targets — a model that can't be
evaluated on them can't be trusted on them either.

Reference price ref(t): median of the up-to-3 most recent clean sales in
(t-180d, t] (same definition history/metrics.py uses for the app).

Features (all as of t):
  mom30/90/180/365   ref(t)/ref(t-N) - 1
  accel              mom30 - mom90/3   (is the last month faster than the quarter's pace)
  vol30/90/365       sale counts in the trailing windows
  vol_surge          vol30 / (vol365/12): last month's volume vs the year's monthly average
  days_since_sale    days since the most recent sale
  log_price          log10(ref(t))
  n_sales_to_date    all clean sales up to t (how established the card is)
  dispersion         (max-min)/median of the last 5 sales (how noisy the price is)
  bin_share90        share of the last 90 days' sales that were Buy-It-Now
  ebay_share90       share of the last 90 days' sales on eBay (vs auction houses / Alt)
  mkt_mom30          median mom30 across the whole universe at t (the market)
  rel_mom30          mom30 - mkt_mom30
  char_mom30         mean mom30 across the OTHER liquid cards of the same subject at t
                     (the spillover hypothesis: hype around a character lifts its cards)
  char_n             how many other liquid cards that subject had at t
  age_years          t.year - card year (vintage-ness)

Targets (all strictly after t):
  fwd60, fwd90       ref(t+N)/ref(t) - 1, only when >= MIN_FWD_SALES sales
                     happened in (t, t+N] (otherwise the "future price" would
                     just be the stale present one and the return a fake 0)
  fwd60_ex, fwd90_ex the same minus the universe median at t (excess over market)
  up20_60            1 if fwd60 >= +20%
  fwd_exec           the TRADABLE one: buy at the first sale in (t, t+14],
                     sell at the median of the sales in (t+60, t+90]
                     (>= 2 of them). Uses no sale that any feature used, so
                     noise in ref(t) can't leak into it as fake reversal —
                     the fwd60 target does share ref(t) with the momentum
                     features and overstates mean reversion for that reason.
  fwd_exec_ex        fwd_exec minus the universe median at t
  fwd_exec180(_ex)   same, selling at the median of the sales in (t+150, t+180]

  The MONEY target (what a trader would actually keep), per horizon h in
  MONEY_HORIZONS (30, 90, 180 days):
  entry_med, n_entry  median of the sales in (t, t+ENTRY_WINDOW] (21 days) -
                     you cannot buy at yesterday's price, and a median means
                     one junk row can't be the entry
  entry_ah           share of those entry sales that were at an auction house
                     (buyer's premium applies, see costs.py)
  exit{h}            the 40th percentile of the sales in (t+h, t+h+EXIT_WINDOW]
                     (>= MIN_FWD_SALES of them); if too few, the window is
                     extended once by EXIT_WINDOW days ("extended"); if still
                     too few, the last sale on or before the end of the
                     extended window is used as a MARK ("illiquid") - the
                     trade is not dropped, because dropping it would keep only
                     the cards that found a buyer, and the backtest applies a
                     haircut to marks and reports their share by decile
  exit_flag{h}       "ok" / "extended" / "illiquid" (blank when no entry)
  gross{h}           exit{h} / entry_med - 1 (no costs, no haircut)
  mny{h}(_ex)        net return under costs.CostModel() defaults with a 15%
                     haircut on illiquid marks; the backtest recomputes this
                     from the components for other cost / haircut settings.
                     _ex is minus the median net return of cards in the same
                     PRICE_BINS bucket that month (cost-matched benchmark),
                     falling back to the universe median for thin buckets.
                     gross{h}_ex is the same for the frictionless return: the
                     backtest RANKS on gross{h}_ex (so a model cannot score by
                     predicting the cost curve or its own haircut) and reports
                     PROFIT on mny{h}

Not point-in-time and therefore NOT in this panel: PSA 10 population. The
daily pop snapshots only start 2026-09-11, so any pop-based feature for a
2022 date would be today's pop, i.e. the future. Pop joins the model once
the snapshot series is long enough to backtest on its own.

Caveat carried into every result: sale dates are alt.xyz's sale dates, but
we don't know how many days after a sale alt.xyz first showed it. A
feature "as of t" may include sales that were not visible until t+k. The
daily snapshots will measure that lag going forward; until then assume a
few days and don't trust anything that only works at a horizon shorter
than a month.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "history"))
from metrics import drop_outliers  # noqa: E402  (the app's own outlier filter — same clean sales as latest/cards.csv)
sys.path.insert(0, str(ROOT / "analysis"))
from costs import CostModel  # noqa: E402

OUT = ROOT / "analysis" / "out"
MIN_SALES_180 = 3
MIN_FWD_SALES = 2
MIN_TOTAL_SALES = 8       # skip assets that could never qualify anyway
REBALANCE_START = "2019-01-01"
LOOKBACK = 180
HORIZONS = (60, 90)
MOM_WINDOWS = (30, 90, 180, 365)
ENTRY_DAYS = 14                                      # executable targets: buy at the first sale in (t, t+14] …
EXIT_WINDOWS = {"fwd_exec": (60, 90), "fwd_exec180": (150, 180)}   # … sell at the median of the sales in (t+a, t+b]
MONEY_HORIZONS = (30, 90, 180)
ENTRY_WINDOW = 21          # money target: entry = median of the sales in (t, t+21]
EXIT_WINDOW = 30           # money target: exit = 40th pct of the sales in (t+h, t+h+30], extended once by another 30
EXIT_QUANTILE = 0.40
ILLIQUID_HAIRCUT = 0.15    # default haircut on an illiquid mark (backtest sweeps 0 / 15 / 30%)
PRICE_BINS = [0, 50, 100, 250, 500, 1000, 2500, 10000, np.inf]   # cost-matched benchmark bins for the money target's excess return
MIN_BIN_ROWS = 20
AUCTION_HOUSE_RX = "auction"   # sources containing this (Goldin Auctions, Heritage Auctions, PWCC Weekly/Premier Auctions, Pristine Auction, Lelands Auctions)


def load_clean_sales(store=ROOT / "history"):
    frames = []
    for p in sorted((store / "sales").glob("*.csv")):
        df = pd.read_csv(p, usecols=["asset_id", "date", "price", "grading_company", "grade", "source", "sale_type", "skipped_reason"],
                         dtype={"skipped_reason": "string", "sale_type": "string", "source": "string"})
        frames.append(df)
    s = pd.concat(frames, ignore_index=True)
    s = s[(s.grading_company == "PSA") & (s.grade == 10.0) & s.skipped_reason.isna()]
    s = s[pd.to_numeric(s.price, errors="coerce") > 0]
    s["date"] = pd.to_datetime(s["date"], errors="coerce")
    s = s.dropna(subset=["date"]).sort_values(["asset_id", "date"]).reset_index(drop=True)
    # apply the app's sequential outlier filter per asset (point-in-time
    # except for the regime-change reset, which re-admits a run of earlier
    # sales once the run is long enough — a mild look-back, not look-ahead)
    keep_idx = []
    for aid, g in s.groupby("asset_id", sort=False):
        if len(g) < MIN_TOTAL_SALES:
            continue
        tuples = list(zip(g.date.dt.date, g.price.astype(float), g.source.fillna("")))
        kept, _ = drop_outliers(tuples)
        kept_set = set((d, p) for d, p, _ in kept)
        keep_idx.extend(i for i, (d, p, _) in zip(g.index, tuples) if (d, p) in kept_set)
    s = s.loc[keep_idx].copy()
    s["is_bin"] = (s.sale_type == "BUY_IT_NOW").astype(float)
    s["is_ebay"] = (s.source.fillna("").str.lower() == "ebay").astype(float)
    s["is_ah"] = s.source.fillna("").str.lower().str.contains(AUCTION_HOUSE_RX).astype(float)
    return s.reset_index(drop=True)


def ref_price(prices, lo, hi):
    """median of the up-to-3 most recent of prices[lo:hi]; nan if empty."""
    if hi <= lo:
        return np.nan
    return float(np.median(prices[max(lo, hi - 3):hi]))


def build_panel(sales, assets, rebalance_dates):
    T = np.array(rebalance_dates, dtype="datetime64[D]")
    rows = []
    day = np.timedelta64(1, "D")
    for aid, g in sales.groupby("asset_id", sort=False):
        d = g.date.values.astype("datetime64[D]")
        p = g.price.values.astype(float)
        bin_ = g.is_bin.values
        ebay = g.is_ebay.values
        ah = g.is_ah.values
        n = len(d)
        # index of first sale strictly after each date (== count of sales <= date)
        upto = np.searchsorted(d, T, side="right")
        for k, t in enumerate(T):
            hi = upto[k]
            lo180 = np.searchsorted(d, t - 180 * day, side="right")
            if hi - lo180 < MIN_SALES_180:
                continue
            lo60 = np.searchsorted(d, t - 60 * day, side="right")
            if hi - lo60 < 1:
                continue
            ref = ref_price(p, lo180, hi)
            row = {"asset_id": aid, "date": pd.Timestamp(t), "ref": ref, "log_price": np.log10(ref)}
            for w in MOM_WINDOWS:
                tw = t - w * day
                hi_w = np.searchsorted(d, tw, side="right")
                lo_w = np.searchsorted(d, tw - LOOKBACK * day, side="right")
                rw = ref_price(p, lo_w, hi_w)
                row[f"mom{w}"] = ref / rw - 1 if rw and not np.isnan(rw) else np.nan
            lo30 = np.searchsorted(d, t - 30 * day, side="right")
            lo90 = np.searchsorted(d, t - 90 * day, side="right")
            lo365 = np.searchsorted(d, t - 365 * day, side="right")
            row["vol30"] = hi - lo30
            row["vol90"] = hi - lo90
            row["vol365"] = hi - lo365
            row["vol_surge"] = row["vol30"] / max(row["vol365"] / 12.0, 0.25)
            row["days_since_sale"] = float((t - d[hi - 1]) / day)
            row["n_sales_to_date"] = hi
            last5 = p[max(0, hi - 5):hi]
            row["dispersion"] = (last5.max() - last5.min()) / np.median(last5)
            row["bin_share90"] = bin_[lo90:hi].mean() if hi > lo90 else np.nan
            row["ebay_share90"] = ebay[lo90:hi].mean() if hi > lo90 else np.nan
            row["accel"] = row["mom30"] - row["mom90"] / 3 if not np.isnan(row["mom90"]) else np.nan
            # targets: strictly after t
            for h in HORIZONS:
                th = t + h * day
                hi_h = np.searchsorted(d, th, side="right")
                if hi_h - hi < MIN_FWD_SALES:
                    row[f"fwd{h}"] = np.nan
                    continue
                lo_h = np.searchsorted(d, th - LOOKBACK * day, side="right")
                rh = ref_price(p, lo_h, hi_h)
                row[f"fwd{h}"] = rh / ref - 1
            # executable target: buy at the FIRST sale after t (within
            # ENTRY_DAYS, else you couldn't have bought), sell at the median
            # of the sales in (t+60, t+90]. Shares no sale with any feature,
            # so measurement noise in ref(t) can't manufacture "reversal".
            hi_entry = np.searchsorted(d, t + ENTRY_DAYS * day, side="right")
            entry = p[hi] if hi_entry > hi else np.nan
            row["entry_price"] = entry
            for label, (x_from, x_to) in EXIT_WINDOWS.items():
                lo_exit = np.searchsorted(d, t + x_from * day, side="right")
                hi_exit = np.searchsorted(d, t + x_to * day, side="right")
                if not np.isnan(entry) and hi_exit - lo_exit >= MIN_FWD_SALES:
                    row[label] = float(np.median(p[lo_exit:hi_exit])) / entry - 1
                else:
                    row[label] = np.nan
            # money target: robust entry, conservative exit, no dropped trades
            hi_ew = np.searchsorted(d, t + ENTRY_WINDOW * day, side="right")
            if hi_ew > hi:
                row["entry_med"] = float(np.median(p[hi:hi_ew]))
                row["n_entry"] = hi_ew - hi
                row["entry_ah"] = float(ah[hi:hi_ew].mean())
                for h in MONEY_HORIZONS:
                    lo_x = np.searchsorted(d, t + h * day, side="right")
                    hi_x = np.searchsorted(d, t + (h + EXIT_WINDOW) * day, side="right")
                    flag = "ok"
                    if hi_x - lo_x < MIN_FWD_SALES:
                        hi_x = np.searchsorted(d, t + (h + 2 * EXIT_WINDOW) * day, side="right")
                        flag = "extended"
                    if hi_x - lo_x >= MIN_FWD_SALES:
                        row[f"exit{h}"] = float(np.quantile(p[lo_x:hi_x], EXIT_QUANTILE))
                        row[f"n_exit{h}"] = hi_x - lo_x
                    else:
                        # mark at the last sale on or before the end of the extended window (the entry sale at worst)
                        row[f"exit{h}"] = float(p[hi_x - 1])
                        row[f"n_exit{h}"] = hi_x - lo_x
                        flag = "illiquid"
                    row[f"exit_flag{h}"] = flag
            else:
                row["entry_med"], row["n_entry"], row["entry_ah"] = np.nan, 0, np.nan
                for h in MONEY_HORIZONS:
                    row[f"exit{h}"], row[f"n_exit{h}"], row[f"exit_flag{h}"] = np.nan, 0, ""
            rows.append(row)
    panel = pd.DataFrame(rows)
    if panel.empty:
        return panel
    panel = panel.merge(assets[["asset_id", "subject", "year", "set"]], on="asset_id", how="left")
    panel["age_years"] = panel.date.dt.year - pd.to_numeric(panel.year, errors="coerce")

    # market and character context, per date, computed from the universe at that date
    panel["mkt_mom30"] = panel.groupby("date").mom30.transform("median")
    panel["rel_mom30"] = panel.mom30 - panel.mkt_mom30
    for h in HORIZONS:
        panel[f"mkt_fwd{h}"] = panel.groupby("date")[f"fwd{h}"].transform("median")
        panel[f"fwd{h}_ex"] = panel[f"fwd{h}"] - panel[f"mkt_fwd{h}"]
    for label in EXIT_WINDOWS:
        panel[f"mkt_{label}"] = panel.groupby("date")[label].transform("median")
        panel[f"{label}_ex"] = panel[label] - panel[f"mkt_{label}"]
    add_money_returns(panel)
    panel["up20_60"] = (panel.fwd60 >= 0.20).astype(float).where(panel.fwd60.notna())
    # leave-one-out character momentum: (sum - own) / (n - 1)
    grp = panel.groupby(["date", "subject"]).mom30
    s_sum = grp.transform("sum")
    s_n = grp.transform("count")
    own = panel.mom30.fillna(0)
    panel["char_n"] = s_n - panel.mom30.notna().astype(int)
    panel["char_mom30"] = ((s_sum - own) / panel.char_n).where(panel.char_n >= 1)
    return panel


def add_money_returns(panel, cost_model=None, haircut=ILLIQUID_HAIRCUT):
    """gross{h}, mny{h}, mny{h}_ex from the stored components; the backtest
    calls this again with other cost / haircut settings."""
    cm = cost_model or CostModel()
    ah = panel.entry_ah.fillna(0.0)
    # Excess is measured against cards of the same PRICE BIN that month, not
    # the whole universe: the fixed costs (shipping, order fee) make a $30
    # card's net return worse than a $3,000 card's by construction, and a
    # model with log_price as a feature would otherwise "predict" the cost
    # curve (GBM IC 0.35 with 100% of months positive, seen 2026-09-15).
    panel["price_bin"] = pd.cut(panel.entry_med.fillna(panel.ref), PRICE_BINS, labels=False)
    for h in MONEY_HORIZONS:
        exit_ = panel[f"exit{h}"].where(panel[f"exit_flag{h}"] != "illiquid", panel[f"exit{h}"] * (1.0 - haircut))
        panel[f"gross{h}"] = panel[f"exit{h}"] / panel.entry_med - 1.0
        panel[f"mny{h}"] = pd.Series(cm.net_return(panel.entry_med.values, exit_.values, ah.values), index=panel.index).where(exit_.notna())
        for col in (f"mny{h}", f"gross{h}"):
            grp = panel.groupby(["date", "price_bin"])[col]
            bin_med = grp.transform("median").where(grp.transform("count") >= MIN_BIN_ROWS)
            panel[f"mkt_{col}"] = bin_med.fillna(panel.groupby("date")[col].transform("median"))
            panel[f"{col}_ex"] = panel[col] - panel[f"mkt_{col}"]
    panel["breakeven"] = pd.Series(cm.breakeven_move(panel.entry_med.fillna(panel.ref).values), index=panel.index)
    return panel


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print("loading sales…")
    sales = load_clean_sales()
    assets = pd.read_csv(ROOT / "history" / "assets.csv", usecols=["asset_id", "subject", "year", "set"])
    data_date = sales.date.max()
    last_rebalance = data_date - pd.Timedelta(days=max(HORIZONS))
    dates = pd.date_range(REBALANCE_START, last_rebalance, freq="MS")
    print(f"{len(sales)} clean sales on {sales.asset_id.nunique()} assets (>= {MIN_TOTAL_SALES} sales each), data through {data_date.date()}")
    print(f"{len(dates)} monthly rebalance dates {dates[0].date()} .. {dates[-1].date()}")
    panel = build_panel(sales, assets, dates)
    panel.to_csv(OUT / "panel.csv", index=False)
    liquid = panel.groupby("date").size()
    print(f"panel: {len(panel)} asset-months, {panel.asset_id.nunique()} assets; liquid universe per month min {liquid.min()} median {int(liquid.median())} max {liquid.max()}")
    print(f"rows with fwd60 target: {panel.fwd60.notna().sum()}, fwd90: {panel.fwd90.notna().sum()}")
    for h in MONEY_HORIZONS:
        f = panel[f"exit_flag{h}"].value_counts()
        print(f"money target {h}d: {int(panel[f'mny{h}'].notna().sum())} rows; exits ok {int(f.get('ok', 0))}, extended {int(f.get('extended', 0))}, illiquid marks {int(f.get('illiquid', 0))}")
    print(f"wrote {OUT / 'panel.csv'}")


if __name__ == "__main__":
    main()
