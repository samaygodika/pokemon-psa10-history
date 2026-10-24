#!/usr/bin/env python3
"""Sid's PSA 10 pills -- Reverse Arb, Sleeper -- and the category phase, replayed point-in-time and
tested as cuts on the v0.1 cell model (2026-10-23), walk-forward through analysis/buy_eval.py.

    analysis/.venv/bin/python analysis/cand_pills.py features   # sales -> analysis/out/buy_eval/pills_features.pkl
    analysis/.venv/bin/python analysis/cand_pills.py lifts      # within-month lift tables (stdout, pills_lifts.csv)
    analysis/.venv/bin/python analysis/cand_pills.py preds      # walk-forward -> preds_pills_*.csv.gz, preds_live_pills_*.csv.gz (+ pills_ors.csv)
    analysis/.venv/bin/python analysis/cand_pills.py all        # the three in order

The pills live in PokeSniper (lib/reverseArb.js, lib/categoryPhase.js, lib/cardPhase.js and
computeSleepers in PokeSniper.jsx, Sid 2026-09-28 .. 10-21). The app evaluates them on today's
sales; here every one is recomputed at each monthly anchor t of the harness's trades from the sales
store (history/sales/*.csv: PSA, grade 9.0 / 10.0, skipped_reason empty, price > 0, the feed's
outlier filter history/metrics.py drop_outliers per asset and grade as analysis/panel.py
load_clean_sales applies it), using sales dated on or before t only. Two mild deviations from
point-in-time, both shared with the panel: drop_outliers is run once over each card's whole history,
so a high sale held back at t but confirmed later counts as clean at t (Sid's rule counts held-back
highs anyway) and a low run confirmed later is re-admitted (rare); and alt.xyz's reporting lag is
unknown. The pills use the clean sales only (Sid's lists also carry the held-back ones, counted only
above the card's clean median), except where Sid's rule says "any sale": those use every store sale.

Features per trade (asset, anchor t):
  category       buy_list.category_of: the card type for Gold Star / LV.X / Neo Shining / e-card
                 Crystal / HGSS Prime/LEGEND / EX-era ex, else the set
  cat_phase      Sid's categoryPhase over every English card of the category (history/assets.csv,
                 other franchises out): per card its last 10 clean PSA 10 sales on or before t, those
                 in the 75 days to t, >= 3 of them and the newest within 30 days; newest / peak (a
                 rolling median of 3 from 8 sales); per category valueOff (value-weighted newest vs
                 peak), breadth (share 15%+ below their peak), run (median peak vs first), lastMove
                 (median newest vs the one before), upShare. Correcting: valueOff <= -15% and breadth
                 >= 35%; Pumping: lastMove >= 5%, upShare >= 55%, valueOff > -15%; Peaking: run >=
                 30% and (valueOff <= -15%, or <= -5% with lastMove <= 0); else Steady. On >= 10
                 cards; 3-9 cards the same rules (approx "few"); under 3 the category's 1-month
                 median PSA 10 change (ref(t) / ref(t-30) - 1 over its cards with a sale in the 30
                 days to t): >= +10% Pumping, <= -10% Correcting, else Steady (approx "month")
  card_correcting  Sid's cardPhase on the card's own last 10 clean PSA 10 sales: newest within 90
                 days of t, 3 before it within 365 days, newest >= 35% below the high of the sales in
                 the 75 days up to the newest
  psa9_ref       median of the last 3 clean PSA 9 sales in (t-180 d, t]; n9_90 / psa9_age the PSA 9
                 sales in (t-90 d, t] and the age of the newest
  r9             psa9_ref / ref (ref = the trade's PSA 10 reference, median of its last 3 sales)
  rev_gap        ref / implied, implied = psa9_ref / r_typ, r_typ = the 75th percentile of r9 (Sid's
                 conservative end) over the anchor's trades of the same era and PSA 10 tier (>= 20
                 of them; else the era, else all). Point-in-time, no pops. rev_z = log(r9 / the
                 group's median r9): the continuous version (> 0 = the PSA 10 cheap vs its PSA 9)
  rev_gap_pop    the same with Sid's pop-ratio buckets (PSA 9 pop / PSA 10 pop, edges 0 1 2 4 8 16)
                 for the group, r_typ re-measured at t (Sid's fixed 2026-10-20 ratios only when a
                 bucket has < 20 trades). *** TODAY's pops (latest/cards.csv): a mild look-ahead, pops
                 only grow and the ratio moves slowly for vintage cards; a third of the cards have
                 no pop and get no flag ***
  rev_arb        rev_gap < 0.5 (Sid's REVERSE_ARB_FLAG_SHARE) and Sid's guards: card year <= 2013,
                 category Pumping or Peaking (approx included), card not Correcting, implied >=
                 $1,000, >= 2 clean PSA 9 sales in 90 days with the newest within 60, no PSA 10
                 sale of any kind in the 14 days to t, the newest PSA 10 sale of any kind below
                 implied, the last clean PSA 10 >= 40% of the median of the card's earlier ones
                 (in its last 10). rev_arb_pop: the same on rev_gap_pop. rev_cheap: the gap alone
  sleeper        category Pumping or Peaking, no PSA 10 sale of any kind in the 30 days to t, the
                 last clean PSA 10 sale (price P, date L) >= $500 and >= 40% of the median of the
                 card's earlier ones, card not Correcting, and the peer estimate 1.5x to 5x P
                 (Sid's SLEEPER_MIN_RATIO / MAX_RATIO). Peer estimate (simple, point-in-time, no
                 pops): P x the median over the category's other English cards with a clean sale in
                 the 30 days to t AND one in the 60 days to L of (median price in (t-30, t] / median
                 price in (L-60, L]), >= 3 such peers: what the category's cards that did sell moved
                 since this one last sold. (Sid's estimate is one pop-adjusted peer, same Pokemon /
                 subset first; not replayed.) The trade universe needs a sale in the 180 days before
                 t, so Sleepers staler than that are not in the test; Sid's 21-day staleness and
                 his category heat score >= 40 are not applied
  cat_pumping / cat_correcting  the category phase as two dummies

Lift tables (lifts): trades closed at the data end, per horizon; each flagged trade is compared
with the rest of its anchor month (one regime per month, so this is inside the regime): pooled
win rate on vs the month-matched rate off, lift and odds ratio; vs LIKE cards (the unflagged
trades of the same month, era and PSA 10 tier, >= 20 of them, else month and tier, else month),
since most pills select on price and staleness, which the cells already price; months with a
flagged trade, and over months with >= MIN_ON_MONTH flagged trades the share in which the flag
beat its month and the t of the month differences; and win - p_base, the realized win rate minus
the baseline's own out-of-sample p (2023+ test trades), flagged vs unflagged: the room a cut on
top of the baseline has.

Walk-forward (preds): at each cutoff D of buy_eval.cutoffs and horizon h, the cell tables are
refitted at D (buy_list_rates.fit, as run_baseline does) and score the training trades (closed by
D) in-sample with buy_list.score_cell; a logistic regression of win on the flags with logit(that
p) as a fixed offset (slope 1, plus a free intercept that is NOT applied, ridge RIDGE on the flag
coefficients) estimates each flag's odds ratio over what the cells already say. Each OR is capped
at [0.5, 2] (a continuous term's per-trade OR likewise), their product at [1/3, 3] (the nightly's
OR_PRODUCT_CAP), and applied on the odds scale to the baseline's own out-of-sample p of the fold's
test trades (preds_baseline), capped at 0.9. A flag with < MIN_FLAG_TRAIN flagged training trades
or < 3 months gets OR 1 (p = the baseline's). Two protocols share the fits (the ORs at D use only
the trades closed by D): the cutoff protocol applies them to preds_baseline's fold D (trades closing
in (D, next], whose anchors are about a month after the newest training anchor: exits overlap, too
kind) -> preds_pills_*; the LIVE protocol (buy_eval.live_folds, the honest one) to
preds_live_baseline's month A = D (trades anchored at D) -> preds_live_pills_*.
Variants, fixed before any test fold was scored:
  pills_revarb      rev_arb (point-in-time ratio, no pops)
  pills_revarb_pop  rev_arb_pop (today's pops: look-ahead, see above)
  pills_revgap      the continuous version: has9 (a PSA 9 reference exists) + has9 x clip(rev_z, +-1.5)
  pills_sleeper     sleeper
  pills_phase       cat_correcting (expected a penalty) + cat_pumping (expected a boost), fitted freely
  pills_all         rev_arb + sleeper + cat_correcting + cat_pumping
Post hoc diagnostics, added after the first evaluation to split pills_revgap (NOT candidates):
  pills_diag_has9   has9 alone (does the card have a PSA 9 reference at all)
  pills_diag_revz   has9 x clip(rev_z, +-1.5) alone (the ratio itself)
The fitted ORs per cutoff go to analysis/out/buy_eval/pills_ors.csv.
"""
import argparse
import math
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buy_eval as E  # noqa: E402
import buy_list_rates as rates_mod  # noqa: E402

ROOT = E.ROOT
sys.path.insert(0, str(ROOT / "history"))
import buy_list  # noqa: E402
from metrics import drop_outliers  # noqa: E402

DIR = E.DIR
FEATS = DIR / "pills_features.pkl"
DAY0 = pd.Timestamp("1970-01-01")

# Sid's constants (PokeSniper lib/categoryPhase.js, lib/cardPhase.js, lib/reverseArb.js, computeSleepers)
PH_LOOKBACK, PH_FRESH, PH_MIN_SALES, PH_MIN_CARDS, PH_SMOOTH, PH_FALLBACK_MIN = 75, 30, 3, 10, 8, 3
PH_CORRECT, PH_BREADTH, PH_PEAK_RUN, PH_PEAK_DROP, PH_STALL, PH_PUMP, PH_PUMP_BREADTH, PH_PUMP_MAX_OFF, PH_MONTH = 15, 35, 30, 15, 5, 5, 55, 15, 10
CARD_LOOKBACK, CARD_PRIOR, CARD_NEWEST_MAX, CARD_PRIOR_WINDOW, CARD_CORRECT = 75, 3, 90, 365, 35
RA_POP_EDGES = [0, 1, 2, 4, 8, 16, np.inf]
RA_RATIOS = [0.378, 0.247, 0.174, 0.119, 0.088, 0.053]
RA_SHARE, RA_PSA9_WINDOW, RA_MIN_PSA9, RA_PSA9_NEWEST, RA_MIN_10_AGE, RA_MAX_YEAR, RA_MIN_IMPLIED, FLOOR_SHARE = 0.5, 90, 2, 60, 14, 2013, 1000, 0.4
SL_STALE, SL_MIN_LAST, SL_MIN_RATIO, SL_MAX_RATIO, SL_PEER_DAYS, SL_THEN_DAYS, SL_MIN_PEERS = 30, 500, 1.5, 5, 30, 60, 3
HOT = ("Pumping", "Peaking")
MIN_GROUP = 20
Q_TYP = 0.75

# walk-forward
OR_LO, OR_HI = 0.5, 2.0
PROD_LO, PROD_HI = 1 / buy_list.OR_PRODUCT_CAP, buy_list.OR_PRODUCT_CAP
RIDGE = 1.0
MIN_FLAG_TRAIN = 30
MIN_ON_MONTH = 5
VARIANTS = {
    "pills_revarb": ["rev_arb"],
    "pills_revarb_pop": ["rev_arb_pop"],
    "pills_revgap": ["has9", "rev_zc"],
    "pills_sleeper": ["sleeper"],
    "pills_phase": ["cat_correcting", "cat_pumping"],
    "pills_all": ["rev_arb", "sleeper", "cat_correcting", "cat_pumping"],
    # post hoc (added after the first evaluation, to split pills_revgap; diagnostics, not candidates)
    "pills_diag_has9": ["has9"],
    "pills_diag_revz": ["rev_zc"],
}
CONTINUOUS = ("rev_zc",)
LIFT_FLAGS = ["rev_cheap", "rev_arb", "rev_cheap_pop", "rev_arb_pop", "rev_z_top", "sleeper", "sleeper_pre", "cat_pumping", "cat_peaking", "cat_correcting", "cat_hot", "card_correcting"]


def days(ts):
    return ((pd.to_datetime(ts) - DAY0) // pd.Timedelta(days=1)).astype(np.int64)


# ---------------------------------------------------------------- sales
def load_sales():
    """{grade: (clean, raw)}: asset_id -> (days since 1970 int32, prices) of its clean sales / of every store sale, in date order."""
    t0 = time.time()
    frames = []
    for p in sorted((ROOT / "history" / "sales").glob("*.csv")):
        df = pd.read_csv(p, usecols=["asset_id", "date", "price", "grading_company", "grade", "source", "skipped_reason"],
                         dtype={"skipped_reason": "string", "source": "string", "grading_company": "string", "asset_id": "string"})
        df = df[(df.grading_company == "PSA") & df.grade.isin([9.0, 10.0]) & df.skipped_reason.isna()]
        df = df[pd.to_numeric(df.price, errors="coerce") > 0]
        frames.append(df[["asset_id", "date", "price", "grade", "source"]])
    s = pd.concat(frames, ignore_index=True)
    del frames
    s["price"] = pd.to_numeric(s.price, errors="coerce").astype(float)
    s["date"] = pd.to_datetime(s["date"], errors="coerce")
    s = s.dropna(subset=["date"])
    s["day"] = days(s.date).astype(np.int32)
    s["asset_id"] = s.asset_id.astype("category")
    s = s.sort_values(["grade", "asset_id", "date"], kind="stable").reset_index(drop=True)
    out = {}
    for g, sg in s.groupby("grade"):
        clean, raw = {}, {}
        for aid, a in sg.groupby("asset_id", sort=False, observed=True):
            d, pr = a.day.values, a.price.values
            raw[aid] = (d, pr)
            tuples = list(zip(a.date.dt.date, pr, a.source.fillna("").astype(str)))
            kept, _ = drop_outliers(tuples)
            ks = set((x, y) for x, y, _ in kept)
            m = np.fromiter(((x, y) in ks for x, y, _ in tuples), bool, len(tuples))
            clean[aid] = (d[m], pr[m])
        out[int(g)] = (clean, raw)
        print(f"  PSA {int(g)}: {len(sg):,} sales, {len(clean):,} cards, {sum(len(v[0]) for v in clean.values()):,} clean ({time.time() - t0:.0f}s)", flush=True)
    return out


def english_cards():
    a = pd.read_csv(ROOT / "history" / "assets.csv", dtype=str, keep_default_na=False)
    rows = {}
    for r in a.to_dict("records"):
        if buy_list.language(r) != "english" or buy_list.OTHER_RX.search(r.get("card_name") or ""):
            continue
        rows[r["asset_id"]] = buy_list.category_of(r)
    return rows


# ---------------------------------------------------------------- category phase
def card_reading(d, p, t):
    """Sid's cardPhaseReading on clean sales (d, p) at day t: (now, peak, lastMove, run) or None."""
    hi = np.searchsorted(d, t, "right")
    lo = max(0, hi - 10)
    dd, pp = d[lo:hi], p[lo:hi]
    m = t - dd <= PH_LOOKBACK
    dd, pp = dd[m], pp[m]
    if len(pp) < PH_MIN_SALES or t - dd[-1] > PH_FRESH:
        return None
    now, peak = pp[-1], pp.max()
    if len(pp) >= PH_SMOOTH:
        roll = [float(np.median(pp[i - 2:i + 1])) for i in range(2, len(pp))]
        now, peak = roll[-1], max(roll)
    return now, peak, pp[-1] / pp[-2] - 1, peak / pp[0] - 1


def ref_at(d, p, t):
    hi = np.searchsorted(d, t, "right")
    lo = np.searchsorted(d, t - 180, "right")
    return float(np.median(p[max(lo, hi - 3):hi])) if hi > lo else np.nan


def category_phases(clean10, cats, anchors):
    """DataFrame (date, category, cat_phase, cat_approx, cat_n, valueOff, breadth, run, lastMove, upShare, monthPct)."""
    reads, m1 = [], []
    for aid, cat in cats.items():
        if aid not in clean10:
            continue
        d, p = clean10[aid]
        if not len(d):
            continue
        newest_idx = np.searchsorted(d, anchors, "right") - 1
        for t, ni in zip(anchors, newest_idx):
            if ni < 0 or t - d[ni] > PH_FRESH:
                continue
            r = card_reading(d, p, t)
            if r:
                reads.append((t, cat, *r))
            r0, r1 = ref_at(d, p, t), ref_at(d, p, t - 30)
            if r0 == r0 and r1 == r1 and r1 > 0:
                m1.append((t, cat, r0 / r1 - 1))
    R = pd.DataFrame(reads, columns=["day", "category", "now", "peak", "lastMove", "run"])
    M = pd.DataFrame(m1, columns=["day", "category", "m1"]).groupby(["day", "category"]).m1.median().rename("monthPct")
    R["is_off"] = (R.now / R.peak - 1 <= -PH_CORRECT / 100).astype(float)
    R["is_up"] = (R.lastMove >= 0).astype(float)
    g = R.groupby(["day", "category"])
    agg = pd.DataFrame({"cat_n": g.size(), "valueOff": 100 * (g.now.sum() / g.peak.sum() - 1), "breadth": 100 * g.is_off.mean(),
                        "run": 100 * g.run.median(), "lastMove": 100 * g.lastMove.median(), "upShare": 100 * g.is_up.mean()})
    idx = pd.MultiIndex.from_product([anchors, sorted(set(cats.values()))], names=["day", "category"])
    out = agg.reindex(idx).join(M, how="left")
    out["cat_n"] = out.cat_n.fillna(0).astype(int)
    full = out.cat_n >= PH_FALLBACK_MIN
    ph = np.full(len(out), "Steady", dtype=object)
    vo, br, ru, lm, up = out.valueOff.values, out.breadth.values, out.run.values, out.lastMove.values, out.upShare.values
    with np.errstate(invalid="ignore"):
        corr = full & (vo <= -PH_CORRECT) & (br >= PH_BREADTH)
        pump = full & ~corr & (lm >= PH_PUMP) & (up >= PH_PUMP_BREADTH) & (vo > -PH_PUMP_MAX_OFF)
        peak = full & ~corr & ~pump & (ru >= PH_PEAK_RUN) & ((vo <= -PH_PEAK_DROP) | ((vo <= -PH_STALL) & (lm <= 0)))
        mp = out.monthPct.values * 100
        fb = ~full
        ph[corr] = "Correcting"
        ph[pump] = "Pumping"
        ph[peak] = "Peaking"
        ph[fb & (mp >= PH_MONTH)] = "Pumping"
        ph[fb & (mp <= -PH_MONTH)] = "Correcting"
    out["cat_phase"] = ph
    out["cat_approx"] = np.where(out.cat_n >= PH_MIN_CARDS, "", np.where(full, "few", "month"))
    return out.reset_index()


def card_correcting(d, p, t):
    """Sid's cardPhase (lib/cardPhase.js) reads Correcting on clean sales at day t."""
    hi = np.searchsorted(d, t, "right")
    dd, pp = d[max(0, hi - 10):hi], p[max(0, hi - 10):hi]
    if not len(dd) or t - dd[-1] > CARD_NEWEST_MAX or len(dd) < CARD_PRIOR + 1:
        return False
    if dd[-1] - dd[-1 - CARD_PRIOR] > CARD_PRIOR_WINDOW:
        return False
    w = dd[-1] - dd <= CARD_LOOKBACK
    return pp[-1] / pp[w].max() - 1 <= -CARD_CORRECT / 100


# ---------------------------------------------------------------- features
def build_features():
    t_all = time.time()
    u, data_end = E.load()
    u = u[["trade_id", "asset_id", "date", "year", "era", "tier", "ref", "days_since_sale"]].copy()
    sales = load_sales()
    clean10, raw10 = sales[10]
    clean9, _ = sales[9]
    cats = english_cards()
    anchors = np.array(sorted(days(pd.Series(u.date.unique()))), dtype=np.int64)
    print(f"category phases over {len(cats):,} English cards, {len(anchors)} anchors ...", flush=True)
    ph = category_phases(clean10, cats, anchors)
    print(f"  done ({time.time() - t_all:.0f}s); phase counts at anchors: {ph.cat_phase.value_counts().to_dict()}", flush=True)

    u["day"] = days(u.date).values
    u["category"] = u.asset_id.map(cats).fillna("")
    rows = []
    for aid, g in u.groupby("asset_id", sort=False):
        c10 = clean10.get(aid, (np.array([], np.int32), np.array([])))
        r10 = raw10.get(aid, (np.array([], np.int32), np.array([])))
        c9 = clean9.get(aid)
        for i, t in zip(g.index, g.day.values):
            d, p = c10
            hi = np.searchsorted(d, t, "right")
            last_p = p[hi - 1] if hi else np.nan
            last_d = d[hi - 1] if hi else -1
            earlier = p[max(0, hi - 10):hi - 1] if hi > 1 else p[:0]
            floor_ok = (not len(earlier)) or last_p >= FLOOR_SHARE * float(np.median(earlier))
            cc = card_correcting(d, p, t)
            rd, rp = r10
            rh = np.searchsorted(rd, t, "right")
            raw_age = t - rd[rh - 1] if rh else 99999
            raw_newest = rp[rh - 1] if rh else np.nan
            if c9 is not None:
                d9, p9 = c9
                h9 = np.searchsorted(d9, t, "right")
                l180 = np.searchsorted(d9, t - 180, "right")
                l90 = np.searchsorted(d9, t - RA_PSA9_WINDOW, "right")
                psa9_ref = float(np.median(p9[max(l180, h9 - 3):h9])) if h9 > l180 else np.nan
                n9_90 = h9 - l90
                psa9_age = t - d9[h9 - 1] if h9 else 99999
            else:
                psa9_ref, n9_90, psa9_age = np.nan, 0, 99999
            rows.append((i, last_p, last_d, floor_ok, cc, raw_age, raw_newest, psa9_ref, n9_90, psa9_age))
    F = pd.DataFrame(rows, columns=["i", "last_p", "last_d", "floor_ok", "card_correcting", "raw_age", "raw_newest", "psa9_ref", "n9_90", "psa9_age"]).set_index("i")
    u = u.join(F)
    u = u.merge(ph[["day", "category", "cat_phase", "cat_approx", "cat_n", "valueOff", "breadth", "lastMove", "upShare", "monthPct"]], on=["day", "category"], how="left")
    u["cat_phase"] = u.cat_phase.fillna("Steady")
    print(f"  per-trade sales features done ({time.time() - t_all:.0f}s)", flush=True)

    # Reverse Arb: the PSA 9 / PSA 10 ratio vs comparable cards at t
    u["r9"] = u.psa9_ref / u.ref
    u["has9"] = u.r9.notna().astype(float)
    pops = pd.read_csv(ROOT / "latest" / "cards.csv", usecols=["asset_id", "pop_at_grade", "pop_at_grade_9"], dtype={"asset_id": str}).drop_duplicates("asset_id").set_index("asset_id")
    u = u.join(pops, on="asset_id")
    pr = u.pop_at_grade_9 / u.pop_at_grade
    pr[~(u.pop_at_grade > 0) | ~(u.pop_at_grade_9 >= 0)] = np.nan
    u["pop_ratio"] = pr
    u["pop_bucket"] = pd.cut(pr, RA_POP_EDGES, right=False, labels=False)
    u["r_typ"], u["r_med"] = group_quantiles(u, [["date", "era", "tier"], ["date", "era"], ["date"]])
    u["r_typ_pop"], _ = group_quantiles(u[u.pop_bucket.notna()], [["date", "pop_bucket"]])
    fixed = u.pop_bucket.map(dict(enumerate(RA_RATIOS)))
    u["r_typ_pop"] = u.r_typ_pop.where(u.r_typ_pop.notna(), fixed.where(u.r9.notna() & u.pop_bucket.notna()))
    for sfx, col in (("", "r_typ"), ("_pop", "r_typ_pop")):
        implied = u.psa9_ref / u[col]
        u[f"implied{sfx}"] = implied
        u[f"rev_gap{sfx}"] = u.ref / implied
        u[f"rev_cheap{sfx}"] = (u[f"rev_gap{sfx}"] < RA_SHARE).fillna(False)
        guards = ((u.year <= RA_MAX_YEAR) & u.cat_phase.isin(HOT) & ~u.card_correcting & (implied >= RA_MIN_IMPLIED)
                  & (u.n9_90 >= RA_MIN_PSA9) & (u.psa9_age <= RA_PSA9_NEWEST) & (u.raw_age >= RA_MIN_10_AGE) & u.floor_ok
                  & (u.raw_newest < implied))
        u[f"rev_arb{sfx}"] = (u[f"rev_cheap{sfx}"] & guards).fillna(False)
    u["rev_z"] = np.log(u.r9 / u.r_med)
    u["rev_zc"] = u.rev_z.clip(-1.5, 1.5).fillna(0.0)
    u["rev_z_top"] = (u.groupby("date").rev_z.rank(pct=True) >= 0.9).fillna(False)   # top decile of the month (lift table only)

    # Sleeper
    pre = u.cat_phase.isin(HOT) & (u.raw_age >= SL_STALE) & (u.last_p >= SL_MIN_LAST) & u.floor_ok & ~u.card_correcting
    u["sleeper_pre"] = pre
    u["sleeper_est"] = np.nan
    peers_by_cat = defaultdict(list)
    for aid, cat in cats.items():
        if aid in clean10 and len(clean10[aid][0]):
            peers_by_cat[cat].append(aid)
    est = {}
    for i, r in u[pre].iterrows():
        t, L = r.day, r.last_d
        moves = []
        for aid in peers_by_cat.get(r.category, []):
            if aid == r.asset_id:
                continue
            d, p = clean10[aid]
            a, b = np.searchsorted(d, t - SL_PEER_DAYS, "right"), np.searchsorted(d, t, "right")
            if b <= a:
                continue
            c, e = np.searchsorted(d, L - SL_THEN_DAYS, "right"), np.searchsorted(d, L, "right")
            if e <= c:
                continue
            moves.append(math.log(float(np.median(p[a:b])) / float(np.median(p[c:e]))))
        if len(moves) >= SL_MIN_PEERS:
            est[i] = r.last_p * math.exp(float(np.median(moves)))
    u.loc[list(est), "sleeper_est"] = pd.Series(est)
    ratio = u.sleeper_est / u.last_p
    u["sleeper_ratio"] = ratio
    u["sleeper"] = (pre & (ratio >= SL_MIN_RATIO) & (ratio <= SL_MAX_RATIO)).fillna(False)
    u["cat_pumping"] = u.cat_phase.eq("Pumping")
    u["cat_peaking"] = u.cat_phase.eq("Peaking")
    u["cat_correcting"] = u.cat_phase.eq("Correcting")
    u["cat_hot"] = u.cat_phase.isin(HOT)
    keep = ["trade_id", "category", "cat_phase", "cat_approx", "cat_n", "valueOff", "breadth", "lastMove", "upShare", "monthPct", "card_correcting",
            "last_p", "floor_ok", "raw_age", "psa9_ref", "n9_90", "psa9_age", "r9", "has9", "r_typ", "r_med", "pop_ratio", "r_typ_pop",
            "implied", "rev_gap", "rev_cheap", "rev_arb", "implied_pop", "rev_gap_pop", "rev_cheap_pop", "rev_arb_pop", "rev_z", "rev_zc", "rev_z_top",
            "sleeper_pre", "sleeper_est", "sleeper_ratio", "sleeper", "cat_pumping", "cat_peaking", "cat_correcting", "cat_hot"]
    for c in ("card_correcting", "floor_ok"):
        u[c] = u[c].fillna(False).astype(bool)
    f = u[keep].reset_index(drop=True)
    f.to_pickle(FEATS)
    print(f"wrote {FEATS} ({len(f):,} trades, {time.time() - t_all:.0f}s)")
    for c in ("rev_cheap", "rev_arb", "rev_cheap_pop", "rev_arb_pop", "sleeper_pre", "sleeper", "cat_pumping", "cat_peaking", "cat_correcting", "card_correcting", "has9"):
        print(f"  {c}: {int(f[c].sum()):,} trades ({f[c].mean():.2%})")
    print("  category phase approx levels:", f.cat_approx.value_counts().to_dict())


def group_quantiles(u, levels):
    """per row: (the Q_TYP quantile, the median) of r9 over the finest group in `levels` with >= MIN_GROUP rows with r9."""
    q = pd.Series(np.nan, index=u.index)
    m = pd.Series(np.nan, index=u.index)
    has = u[u.r9.notna()]
    for keys in levels:
        g = has.groupby(keys, observed=True).r9
        tab = pd.DataFrame({"n": g.size(), "q": g.quantile(Q_TYP), "m": g.median()})
        j = u[keys].join(tab, on=keys)
        ok = (j.n >= MIN_GROUP) & q.isna()
        q[ok] = j.q[ok]
        m[ok] = j.m[ok]
    return q, m


# ---------------------------------------------------------------- lifts
def load_all():
    u, data_end = E.load()
    f = pd.read_pickle(FEATS)
    u = u.merge(f, on="trade_id", how="left", suffixes=("", "_f"))
    for c in LIFT_FLAGS:
        u[c] = u[c].fillna(False).astype(bool)
    return u, data_end


def expected_like(w, on):
    """per flagged trade: the win rate of the UNflagged trades of its month and era x tier (>= MIN_GROUP of them),
    else its month and tier, else its month: what like cards did that month."""
    exp = pd.Series(np.nan, index=w.index[on])
    off = w[~on]
    for keys in (["date", "era", "tier"], ["date", "tier"], ["date"]):
        g = off.groupby(keys, observed=True).win.agg(["mean", "size"])
        j = w.loc[on, keys].join(g, on=keys)
        ok = exp.isna() & (j["size"] >= MIN_GROUP)
        exp[ok] = j["mean"][ok]
    return exp


def lift_rows(w, flag, h, label):
    on = w[flag].values
    if on.sum() == 0:
        return None
    g = w.assign(on=on).groupby(["date", "on"]).win.agg(["sum", "size"]).unstack("on", fill_value=0)
    g.columns = [f"{a}_{b}" for a, b in g.columns]
    for c in ("sum_True", "size_True", "sum_False", "size_False"):
        if c not in g:
            g[c] = 0
    g = g[(g.size_True > 0) & (g.size_False > 0)]
    if g.empty:
        return None
    off_rate = g.sum_False / g.size_False
    n_on, wins_on = g.size_True.sum(), g.sum_True.sum()
    p1 = wins_on / n_on
    p0 = (g.size_True * off_rate).sum() / n_on
    like = expected_like(w, on).mean()
    big = g[g.size_True >= MIN_ON_MONTH]
    diff = big.sum_True / big.size_True - big.sum_False / big.size_False
    hb = w.p_base.notna().values
    gb_on = (w.win[on & hb] - w.p_base[on & hb]).mean() if (on & hb).any() else np.nan
    gb_off = (w.win[~on & hb] - w.p_base[~on & hb]).mean() if (~on & hb).any() else np.nan
    return {"horizon": h, "flag": flag, "period": label, "n_on": int(n_on), "share_on": on.mean(), "win_on": p1, "win_off_month": p0,
            "lift": p1 / p0 if p0 > 0 else np.nan, "odds_ratio": (p1 / (1 - p1)) / (p0 / (1 - p0)) if 0 < p0 < 1 and 0 < p1 < 1 else np.nan,
            "win_like": like, "lift_like": p1 / like if like > 0 else np.nan, "months": len(g), "months_5": len(big),
            "beat_share": (diff > 0).mean() if len(big) else np.nan, "t_months": E.tstat(diff) if len(big) > 2 else np.nan,
            "n_base_on": int((on & hb).sum()), "gap_base_on": gb_on, "gap_base_off": gb_off,
            "net_med_on": w.net[on].median(), "net_med_off": w.net[~on].median()}


def run_lifts():
    u, data_end = load_all()
    base = E.load_preds("baseline")
    rows = []
    for h in E.HORIZONS:
        w = rates_mod.closed(u, h, data_end)
        w = w[w.regime.notna() & w.tier.notna()].copy()
        w = w.join(base[base.horizon == h].set_index("trade_id").p.rename("p_base"), on="trade_id")
        for flag in LIFT_FLAGS:
            for label, ww in [("all", w), ("2025+", w[w.date >= "2025-01-01"])] + [(f"regime {r}", w[w.regime == r]) for r in ("flat", "warm", "hot")]:
                r = lift_rows(ww, flag, h, label)
                if r:
                    rows.append(r)
    L = pd.DataFrame(rows)
    L.to_csv(DIR / "pills_lifts.csv", index=False, float_format="%.4f")
    print(lift_md(L))
    return L


def lift_md(L):
    out = []
    for h in (180, 90):
        out.append(f"\n### {h} d\n\n| flag | period | n on | share | win on | win off (month) | lift | OR | win like cards | lift vs like | months | months >= 5 | beat month | t | win - p_base on / off (n on) | median net on / off |\n"
                   "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        for r in L[L.horizon == h].itertuples():
            gb = "" if pd.isna(r.gap_base_on) else f"{100 * r.gap_base_on:+.1f} / {100 * r.gap_base_off:+.1f} ({r.n_base_on:,})"
            out.append(f"| {r.flag} | {r.period} | {r.n_on:,} | {100 * r.share_on:.2f}% | {100 * r.win_on:.1f}% | {100 * r.win_off_month:.1f}% | {r.lift:.2f} | {r.odds_ratio:.2f} | "
                       f"{100 * r.win_like:.1f}% | {r.lift_like:.2f} | {r.months} | {r.months_5} | {'' if pd.isna(r.beat_share) else f'{100 * r.beat_share:.0f}%'} | "
                       f"{'' if pd.isna(r.t_months) else f'{r.t_months:+.1f}'} | {gb} | {100 * r.net_med_on:+.0f}% / {100 * r.net_med_off:+.0f}% |\n")
    return "".join(out)


# ---------------------------------------------------------------- walk-forward
def fit_offset_logit(X, y, off, ridge=RIDGE, iters=50):
    """logistic regression of y on [1, X] with a fixed offset `off`; ridge on the X coefficients. -> (intercept, betas)."""
    n, k = X.shape
    Z = np.column_stack([np.ones(n), X])
    beta = np.zeros(k + 1)
    pen = np.r_[0.0, np.full(k, ridge)]
    for _ in range(iters):
        eta = np.clip(off + Z @ beta, -30, 30)
        mu = 1 / (1 + np.exp(-eta))
        W = mu * (1 - mu)
        grad = Z.T @ (y - mu) - pen * beta
        H = (Z * W[:, None]).T @ Z + np.diag(pen) + 1e-9 * np.eye(k + 1)
        step = np.linalg.solve(H, grad)
        beta += step
        if np.abs(step).max() < 1e-8:
            break
    return beta[0], beta[1:]


def insample_scores(u, d, tmp):
    """{h: Series trade_id -> in-sample p} of the cell tables refitted at d, for the trades closed by d."""
    cells, lifts, _, _ = rates_mod.fit(u, d)
    rates_mod.write_tables(cells, lifts, tmp)
    rates, lifts_d = buy_list.load_rates(Path(tmp) / "buy_list_rates.csv"), buy_list.load_lifts(Path(tmp) / "buy_list_lifts.csv")
    out = {}
    for h in E.HORIZONS:
        tr = rates_mod.closed(u, h, d)
        tr = tr[tr.era.isin(["wotc", "vintage", "modern", "current"]) & tr.regime.notna() & tr.tier.notna()]
        cutcols = ["stale30", "jp_heat", "band_high", "thin_history"]
        cols = ["regime", "era", "tier", "liq"] + cutcols
        uniq = tr[cols].drop_duplicates().reset_index(drop=True)
        ps = []
        for r in uniq.itertuples(index=False):
            s = buy_list.score_cell(rates, lifts_d, h, r[0], str(r[1]), r[2], r[3], [c for c, v in zip(cutcols, r[4:]) if bool(v)])
            ps.append(s["p"] if s else np.nan)
        uniq["p_is"] = ps
        m = tr[["trade_id", "date", "win"] + cols].merge(uniq, on=cols, how="left")
        out[h] = pd.DataFrame({"trade_id": m.trade_id.values, "p_is": m.p_is.values, "win": m.win.values.astype(float), "date": m.date.values}).dropna(subset=["p_is"])
    return out


def logit(p):
    p = np.clip(p, 1e-3, 1 - 1e-3)
    return np.log(p / (1 - p))


def trade_or(X, betas, cols):
    """per-trade odds ratio: each term's OR capped at [OR_LO, OR_HI], the product at [PROD_LO, PROD_HI]."""
    tot = np.ones(len(X))
    for j, c in enumerate(cols):
        tot *= np.clip(np.exp(betas[j] * X[:, j]), OR_LO, OR_HI)
    return np.clip(tot, PROD_LO, PROD_HI)


def run_preds():
    """both protocols from one fit per (cutoff D, horizon, variant): the ORs are estimated on the trades closed by D
    and applied to (a) the cutoff protocol's test fold at D (preds_baseline, trades closing in (D, next]) and (b) the
    LIVE protocol's test month A = D (preds_live_baseline, trades anchored at D; buy_eval.live_folds), when present."""
    u, data_end = load_all()
    for c in ("has9", "rev_zc"):
        u[c] = u[c].fillna(0.0).astype(float)
    u["rev_zc"] = u.rev_zc * u.has9
    feat = u.set_index("trade_id")
    protos = [("", "baseline")] + ([("live_", "live_baseline")] if (DIR / "preds_live_baseline.csv.gz").exists() else [])
    bases = {}
    for prefix, name in protos:
        b = E.load_preds(name)
        b["cutoff"] = pd.to_datetime(b.cutoff)
        bases[prefix] = b
    plan = sorted(set().union(*[set(b.cutoff.unique()) for b in bases.values()]))
    preds = {(prefix, v): [] for prefix, _ in protos for v in VARIANTS}
    ors = []
    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        for d in plan:
            d = pd.Timestamp(d)
            sc = insample_scores(u, d, tmp)
            for h in E.HORIZONS:
                tests = {prefix: b[(b.horizon == h) & (b.cutoff == d)].join(feat, on="trade_id", rsuffix="_u") for prefix, b in bases.items()}
                tests = {k: v for k, v in tests.items() if not v.empty}
                if not tests:
                    continue
                tr = sc[h].join(feat, on="trade_id", rsuffix="_u")
                off = logit(tr.p_is.values)
                y = tr.win.values
                for v, cols in VARIANTS.items():
                    X = tr[cols].astype(float).values
                    usable = []
                    for j, c in enumerate(cols):
                        if c in CONTINUOUS:
                            usable.append(True)
                            continue
                        on = X[:, j] > 0
                        usable.append(bool(on.sum() >= MIN_FLAG_TRAIN and len(np.unique(tr.date.values[on])) >= 3))
                    betas = np.zeros(len(cols))
                    idx = [j for j, ok in enumerate(usable) if ok]
                    if idx:
                        _, b = fit_offset_logit(X[:, idx], y, off)
                        betas[idx] = b
                    for j, c in enumerate(cols):
                        ors.append({"variant": v, "horizon": h, "cutoff": f"{d.date()}", "term": c, "beta": betas[j], "or_raw": math.exp(betas[j]),
                                    "or_capped": float(np.clip(math.exp(betas[j]), OR_LO, OR_HI)), "n_train": len(tr),
                                    "n_on_train": int((X[:, j] != 0).sum()), "used": bool(usable[j]),
                                    **{f"n_on_test{k or '_cutoff'}": int((te[c].astype(float).values != 0).sum()) for k, te in tests.items()}})
                    for prefix, te in tests.items():
                        orp = trade_or(te[cols].astype(float).values, betas, cols)
                        p = np.array([min(buy_list.odds_apply(pb, o), buy_list.P_CAP) for pb, o in zip(te.p.values, orp)])
                        preds[(prefix, v)].append(pd.DataFrame({"horizon": h, "cutoff": d, "trade_id": te.trade_id.values, "p": p}))
            print(f"pills: cutoff {d.date()} done ({time.time() - t0:.0f}s)", flush=True)
    for (prefix, v), parts in preds.items():
        if parts:
            df = pd.concat(parts, ignore_index=True)
            path = E.save_preds(prefix + v, df)
            print(f"wrote {path} ({len(df):,} predictions)")
    O = pd.DataFrame(ors)
    O.to_csv(DIR / "pills_ors.csv", index=False, float_format="%.4f")
    agg = {"cutoffs": ("cutoff", "size"), "or_median": ("or_raw", "median"), "or_min": ("or_raw", "min"), "or_max": ("or_raw", "max"),
           "or_last": ("or_raw", "last"), "n_on_train_last": ("n_on_train", "last")}
    agg.update({c: (c, "sum") for c in O.columns if c.startswith("n_on_test")})
    s = O[O.used].groupby(["variant", "horizon", "term"]).agg(**agg)
    print(s.to_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["features", "lifts", "preds", "all"])
    a = ap.parse_args()
    if a.cmd in ("features", "all"):
        build_features()
    if a.cmd in ("lifts", "all"):
        run_lifts()
    if a.cmd in ("preds", "all"):
        run_preds()


if __name__ == "__main__":
    main()
