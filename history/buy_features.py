#!/usr/bin/env python3
"""The buy model's features at one date, in the standard library (2026-10-24, buy list v0.2).

    python3 history/buy_features.py 2026-07-01 --out DIR    # -> DIR/features_2026-07-01.csv (one row per English card)

v0.2 (analysis/buy_list_v02.py fits it monthly) was trained on the loose panel: analysis/panel.py
build_panel (per card, from its clean PSA 10 sales on or before the anchor), analysis/buy_list_rates.py
prepare (breadth, Japanese heat, set quartiles, tier / liquidity / regime, the cuts),
analysis/buy_eval.py avoid_columns (the avoid flags the panel can see) and analysis/cand_pills.py
(the PSA 9 reference and its gap to like cards). The nightly's v0.1 features come from cards.csv and
latest/series instead (band_z from weekly medians, momentum from the change columns): close, not
equal. A model is only as good as the match between the features it was fitted on and the ones it is
fed, so this module recomputes the panel's own definitions, line for line, from history/sales for
any date t; analysis/buy_list_v02.py `parity` checks it against panel.py on a past anchor.

Sales (load_sales): every row of history/sales/*.csv with grading_company PSA, the grade, an empty
skipped_reason, a price > 0 and a date; in file order (month files sorted), stably sorted by date per
card. PSA 10: cards with >= MIN_TOTAL_SALES such sales (the loose panel's --min-total-sales 4), then the
feed's outlier filter history/metrics.py drop_outliers over the card's whole history; a sale is clean
when its (date, price) is among the kept ones. PSA 9: the same filter, no minimum (cand_pills).

Per card at t (card_features; panel.py build_panel): the universe is a clean sale in (t-180, t];
ref = median of the up-to-3 newest of them; mom{30,90,180,365} = ref / ref(t - w) - 1 (ref(t - w) over
(t-w-180, t-w]); vol{30,90,180,365} = sales in the window; vol_surge = vol30 / max(vol365 / 12, 0.25);
days_since_sale; dispersion of the last 5; bin / ebay share of the last 90 days; accel = mom30 -
mom90 / 3; band_z = (log10 ref - the mean of the log10 prices of the past 365 days) / their sd (>= 5
sales; 0 when sd is 0). Tags from history/assets.csv: era (<= 2003 wotc, <= 2013 vintage, <= 2019
modern, else current), language from variety + set (panel.language_tag), age.

Cross-section at t (cross_section; buy_list_rates.prepare): other franchises out; breadth = the share of
English cards with ref >= $150 and >= 3 sales in a year whose mom30 > +5%; char_jp_mom30 = the mean mom30
of the subject's Japanese cards (>= 2); set_price_q = the card's rank by ref inside its set's English
cards (>= 8), set_lead_mom90 = the mean mom90 of the set's top quartile (>= 2); char_mom30 = the mean
mom30 of the subject's other cards (any language); tier / liq / regime with the panel's right-closed
edges; cuts stale30, jp_heat, band_high, thin_history, tail_lead; avoid flags release_year, busy_spike,
vol_surge, below_band, dip, chase_flat; eligible. PSA 9 (cand_pills): psa9_ref = the median of the
up-to-3 newest clean PSA 9 sales in (t-180, t]; has9; rev_zc = log(the card's PSA 9 / PSA 10 ratio over
the median ratio of the date's English cards of the same era and tier, >= 20 of them, else the era,
else all), clipped to +-1.5, 0 without a PSA 9 reference.

Standard library only, like the rest of the feed. Blank = unknown (None), never 0.
"""
import argparse
import bisect
import csv
import math
import re
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

from metrics import drop_outliers
import flagged_sales

HERE = Path(__file__).resolve().parent
MIN_TOTAL_SALES = 4          # the loose panel (panel.py --min-sales-180 1 --min-sales-60 0 --min-total-sales 4)
MIN_SALES_180 = 1
MIN_SALES_60 = 0
LOOKBACK = 180
MOM_WINDOWS = (30, 90, 180, 365)
BAND_MIN_SALES = 5
LANGS = ("japanese", "german", "italian", "french", "spanish", "portuguese", "korean", "chinese", "dutch")
OTHER_RX = re.compile(r"lorcana|one piece|weiss schwarz|magic the gathering|yu-gi-oh|dragon ball|digimon|disney", re.I)   # buy_list_rates.OTHER
ERAS = ("wotc", "vintage", "modern", "current")
TIERS = ((50, "<50"), (150, "50-150"), (500, "150-500"), (2000, "500-2k"), (math.inf, "2k+"))   # right-closed: (0, 50] is "<50"
LIQS = ((0, "0"), (1, "1"), (3, "2-3"), (7, "4-7"), (math.inf, "8+"))
REGIMES = ((0.25, "flat"), (0.40, "warm"), (2.0, "hot"))
SET_MIN, SET_LEAD_MIN, JP_MIN, R9_GROUP_MIN, REV_CLIP = 8, 2, 2, 20, 1.5
NAN = float("nan")


def isnan(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


def load_sales(store=HERE, grades=(10.0, 9.0)):
    """{grade: {asset_id: (day ordinals, prices, is_bin, is_ebay)}} of the clean sales, in date order. A sale Sid
    flagged (history/flagged_sales.csv) is not one (2026-10-28)."""
    raw = {g: defaultdict(list) for g in grades}
    flags = flagged_sales.load(store)
    for p in sorted((Path(store) / "sales").glob("*.csv")):
        with open(p, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("grading_company") != "PSA" or (r.get("skipped_reason") or "") != "":
                    continue
                try:
                    g = float(r["grade"])
                    price = float(r["price"])
                    d = date.fromisoformat(r["date"][:10])
                except (TypeError, ValueError):
                    continue
                if flags and flagged_sales.match(flags, r["asset_id"], r["grade"], d, price, r.get("url") or ""):
                    continue
                if g in raw and price > 0:
                    raw[g][r["asset_id"]].append((d, price, r.get("source") or "", r.get("sale_type") or ""))
    out = {}
    for g, by in raw.items():
        clean = {}
        for aid, rows in by.items():
            if g == 10.0 and len(rows) < MIN_TOTAL_SALES:
                continue
            rows.sort(key=lambda x: x[0])                          # stable: same-day sales keep file order (pandas' lexsort)
            kept, _ = drop_outliers([(d, p, s) for d, p, s, _ in rows])
            ks = {(d, p) for d, p, _ in kept}
            rows = [x for x in rows if (x[0], x[1]) in ks]
            clean[aid] = ([d.toordinal() for d, _, _, _ in rows], [p for _, p, _, _ in rows],
                          [1.0 if st == "BUY_IT_NOW" else 0.0 for _, _, _, st in rows], [1.0 if s.lower() == "ebay" else 0.0 for _, _, s, _ in rows])
        out[g] = clean
    return out


def ref_price(p, lo, hi):
    """median of the up-to-3 newest of p[lo:hi]; NAN if empty (panel.ref_price)."""
    if hi <= lo:
        return NAN
    return statistics.median(p[max(lo, hi - 3):hi])


def mean(xs):
    return sum(xs) / len(xs)


def card_features(d, p, is_bin, is_ebay, t):
    """panel.build_panel's features of one card at day ordinal t, or None outside the universe."""
    br = bisect.bisect_right
    hi = br(d, t)
    lo180 = br(d, t - 180)
    if hi - lo180 < MIN_SALES_180 or hi - br(d, t - 60) < MIN_SALES_60:
        return None
    ref = ref_price(p, lo180, hi)
    f = {"ref": ref, "log_price": math.log10(ref)}
    for w in MOM_WINDOWS:
        tw = t - w
        rw = ref_price(p, br(d, tw - LOOKBACK), br(d, tw))
        f[f"mom{w}"] = ref / rw - 1 if not isnan(rw) and rw else NAN
    lo30, lo90, lo365 = br(d, t - 30), br(d, t - 90), br(d, t - 365)
    f["vol30"], f["vol90"], f["vol180"], f["vol365"] = hi - lo30, hi - lo90, hi - lo180, hi - lo365
    f["vol_surge"] = f["vol30"] / max(f["vol365"] / 12.0, 0.25)
    f["days_since_sale"] = float(t - d[hi - 1])
    f["n_sales_to_date"] = hi
    last5 = p[max(0, hi - 5):hi]
    f["dispersion"] = (max(last5) - min(last5)) / statistics.median(last5)
    f["bin_share90"] = mean(is_bin[lo90:hi]) if hi > lo90 else NAN
    f["ebay_share90"] = mean(is_ebay[lo90:hi]) if hi > lo90 else NAN
    f["accel"] = f["mom30"] - f["mom90"] / 3 if not isnan(f["mom90"]) else NAN
    if hi - lo365 >= BAND_MIN_SALES:
        lp = [math.log10(x) for x in p[lo365:hi]]
        sd = statistics.stdev(lp)
        f["band_z"] = (math.log10(ref) - mean(lp)) / sd if sd > 0 else 0.0
    else:
        f["band_z"] = NAN
    return f


def language_tag(text):
    t = (text or "").lower()
    for lang in LANGS:
        if lang in t:
            return lang
    return "english"


def era_of(year):
    if isnan(year):
        return "nan"
    return "wotc" if year <= 2003 else "vintage" if year <= 2013 else "modern" if year <= 2019 else "current"


def bucket(x, edges):
    """the label of the first right-closed edge >= x (pd.cut's (a, b] bins); None for a missing x."""
    if isnan(x):
        return None
    for hi, label in edges:
        if x <= hi:
            return label
    return None


def load_assets(store=HERE, path=None):
    """{asset_id: row} of history/assets.csv (path: another copy, e.g. the one a past panel was built with)."""
    out = {}
    with open(path or Path(store) / "assets.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["asset_id"]] = r
    return out


def to_year(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return NAN


def cross_section(rows, t_year):
    """buy_list_rates.prepare + buy_eval.avoid_columns over one date's universe (rows: dicts with the card
    features and tags), in place; returns the date's breadth."""
    keep = [r for r in rows if not r["_other"]]
    en = [r for r in keep if r["lang"] == "english"]
    liquid = [r for r in en if r["ref"] >= 150 and r["vol365"] >= 3]
    breadth = mean([1.0 if (not isnan(r["mom30"]) and r["mom30"] > 0.05) else 0.0 for r in liquid]) if liquid else NAN
    jp = defaultdict(list)
    for r in keep:
        if r["lang"] == "japanese" and r["subject"] and not isnan(r["mom30"]):
            jp[r["subject"]].append(r["mom30"])
    subj = defaultdict(list)
    for r in rows:                                               # panel.build_panel: char_mom30 over the whole universe, other franchises too
        if r["subject"]:
            subj[r["subject"]].append(r)
    by_set = defaultdict(list)
    for r in en:
        if r["set"]:
            by_set[r["set"]].append(r)
    q, lead = {}, {}
    for s, rs in by_set.items():
        if len(rs) < SET_MIN:
            continue
        refs = sorted(r["ref"] for r in rs)
        n = len(refs)
        for r in rs:                                             # pandas rank(pct=True): average rank of ties / n
            a, b = bisect.bisect_left(refs, r["ref"]), bisect.bisect_right(refs, r["ref"])
            q[id(r)] = (a + b + 1) / 2 / n
        top = [r["mom90"] for r in rs if q[id(r)] >= 0.75 and not isnan(r["mom90"])]
        if len(top) >= SET_LEAD_MIN:
            lead[s] = mean(top)
    for r in rows:
        sv = [x["mom30"] for x in subj.get(r["subject"], []) if not isnan(x["mom30"])] if r["subject"] else []
        n_other = len(sv) - (0 if isnan(r["mom30"]) else 1)
        r["char_n"] = n_other if r["subject"] else NAN
        r["char_mom30"] = (sum(sv) - (0 if isnan(r["mom30"]) else r["mom30"])) / n_other if r["subject"] and n_other >= 1 else NAN
    for r in keep:
        r["breadth"] = breadth
        js = jp.get(r["subject"], []) if r["subject"] else []
        r["char_jp_mom30"] = mean(js) if len(js) >= JP_MIN else NAN
        r["set_lead_mom90"] = lead.get(r["set"], NAN) if r["set"] else NAN
        r["set_price_q"] = q.get(id(r), NAN)
    for r in en:
        r["tier"], r["liq"], r["regime"] = bucket(r["ref"], TIERS), bucket(r["vol90"], LIQS), bucket(breadth, REGIMES)
        r["stale30"] = r["days_since_sale"] >= 30
        r["jp_heat"] = not isnan(r["char_jp_mom30"]) and r["char_jp_mom30"] >= 0.15
        r["band_high"] = not isnan(r["band_z"]) and r["band_z"] >= 1 and r["ref"] >= 150
        r["tail_lead"] = (not isnan(r["set_price_q"]) and r["set_price_q"] <= 0.25 and not isnan(r["set_lead_mom90"])
                          and r["set_lead_mom90"] >= 0.3 and r["ref"] >= 150)
        r["thin_history"] = isnan(r["mom365"]) and r["era"] in ("wotc", "vintage")
        flags = []
        if not isnan(r["year"]) and r["year"] >= t_year - 1:
            flags.append("release_year")
        if not isnan(r["mom30"]) and r["mom30"] >= 0.5 and r["vol30"] >= 3:
            flags.append("busy_spike")
        if r["vol30"] >= 2 * max(r["vol365"] / 12.0, 0.25) and r["vol30"] >= 2:
            flags.append("vol_surge")
        if not isnan(r["band_z"]) and r["band_z"] <= -1:
            flags.append("below_band")
        if not isnan(r["mom30"]) and r["mom30"] <= -0.2:
            flags.append("dip")
        if not isnan(r["set_lead_mom90"]) and r["set_lead_mom90"] < 0.10:
            flags.append("chase_flat")
        r["avoid"] = flags
        r["eligible"] = not flags and r["era"] in ERAS
    return breadth


def psa9_terms(en, clean9, t):
    """cand_pills' has9 / rev_zc for the date's English cards, in place."""
    br = bisect.bisect_right
    for r in en:
        c9 = clean9.get(r["asset_id"])
        r["psa9_ref"] = NAN
        if c9:
            d9, p9 = c9[0], c9[1]
            r["psa9_ref"] = ref_price(p9, br(d9, t - 180), br(d9, t))
        r["has9"] = 0.0 if isnan(r["psa9_ref"]) else 1.0
        r["r9"] = r["psa9_ref"] / r["ref"] if r["has9"] else NAN
    groups = [defaultdict(list), defaultdict(list), defaultdict(list)]
    for r in en:
        if r["has9"]:
            for i, k in enumerate(((r["era"], r["tier"]), (r["era"],), ())):
                groups[i][k].append(r["r9"])
    med = [{k: statistics.median(v) for k, v in g.items() if len(v) >= R9_GROUP_MIN} for g in groups]
    for r in en:
        m = NAN
        for i, k in enumerate(((r["era"], r["tier"]), (r["era"],), ())):
            if k in med[i]:
                m = med[i][k]
                break
        z = math.log(r["r9"] / m) if r["has9"] and not isnan(m) else NAN
        r["rev_zc"] = 0.0 if isnan(z) else max(-REV_CLIP, min(REV_CLIP, z)) * r["has9"]


def features_at(t, sales=None, assets=None, store=HERE):
    """every English card in the date's universe with the panel's features, tags, cross-section, cuts,
    avoid flags and PSA 9 terms, as dicts. t = a datetime.date."""
    sales = sales if sales is not None else load_sales(store)
    assets = assets if assets is not None else load_assets(store)
    to = t.toordinal()
    rows = []
    for aid, (d, p, b, e) in sales[10.0].items():
        f = card_features(d, p, b, e, to)
        if f is None:
            continue
        a = assets.get(aid, {})
        year = to_year(a.get("year"))
        f.update(asset_id=aid, card_name=a.get("card_name") or "", subject=a.get("subject") or "", set=a.get("set") or "",
                 variety=a.get("variety") or "", year=year, era=era_of(year), age_years=(t.year - year) if not isnan(year) else NAN,
                 lang=language_tag((a.get("variety") or "") + " " + (a.get("set") or "")),
                 _other=bool(OTHER_RX.search(a.get("card_name") or "")))
        rows.append(f)
    cross_section(rows, t.year)
    en = [r for r in rows if not r["_other"] and r["lang"] == "english"]
    psa9_terms(en, sales.get(9.0, {}), to)
    return en


def market_moves(en):
    """the market's direction at the date (2026-10-28, the buy list's market_cooling warning): over the cards
    breadth counts (English, ref >= $150, >= 3 sales in a year; features_at's rows), the shares up more than
    +5% and down more than 5% over 30 and 90 days; a card without a price w days back counts in neither
    share but in n. {up30, down30, up90, down90, n}, or {} when no card qualifies."""
    liquid = [r for r in en if r["ref"] >= 150 and r["vol365"] >= 3]
    if not liquid:
        return {}
    out = {"n": len(liquid)}
    for w in (30, 90):
        out[f"up{w}"] = mean([1.0 if (not isnan(r[f"mom{w}"]) and r[f"mom{w}"] > 0.05) else 0.0 for r in liquid])
        out[f"down{w}"] = mean([1.0 if (not isnan(r[f"mom{w}"]) and r[f"mom{w}"] < -0.05) else 0.0 for r in liquid])
    return out


OUT_COLS = ["asset_id", "ref", "log_price", "mom30", "mom90", "mom180", "mom365", "vol30", "vol90", "vol180", "vol365", "vol_surge",
            "days_since_sale", "n_sales_to_date", "dispersion", "bin_share90", "ebay_share90", "accel", "band_z", "age_years", "era",
            "breadth", "char_jp_mom30", "set_price_q", "set_lead_mom90", "char_mom30", "char_n", "tier", "liq", "regime", "stale30",
            "jp_heat", "band_high", "tail_lead", "thin_history", "eligible", "avoid", "psa9_ref", "has9", "rev_zc"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    t = date.fromisoformat(a.date)
    rows = features_at(t)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"features_{t}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(OUT_COLS)
        for r in rows:
            w.writerow(["" if isnan(r.get(c)) else (" ".join(r[c]) if isinstance(r.get(c), list) else repr(r[c]) if isinstance(r.get(c), float) else r.get(c))
                        for c in OUT_COLS])
    print(f"{len(rows):,} English cards at {t} -> {path}")


if __name__ == "__main__":
    main()
