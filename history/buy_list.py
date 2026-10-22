#!/usr/bin/env python3
"""v0 buy list -- the "Claude buy" list (2026-10-21), step 7b of the nightly.

    python3 history/buy_list.py                       # latest/cards.csv + latest/series/ + history/buy_list_{rates,lifts}.csv
                                                      #   -> latest/buy_list.csv, and this data_date's rows into history/buy_ledger.csv
    python3 history/buy_list.py --out DIR --no-ledger # a dry run somewhere else

Sid's spec (PokeSniper PR #1 list, 2026-10-21): the app should say what to buy, like the
leaderboard -- a pill and a list ordered by most potential profit, no capital or price
filter, a reason on every row (the category reason first when it drives the pick), PSA 10.
handoff/2026-10-21-buy-model-strategy.md §12 has the contract; this is the v0 behind it.

WHAT V0 IS, HONESTLY. Not a forecasting model. The score is the measured base rate of the
card's cell -- the share of trades like it that netted +30% or more within the horizon
after every cost (eBay fee, tax, shipping, auction premium; entry = median of the sales in
the three weeks after the anchor, exit = 40th percentile of the sales in the exit window,
illiquid exits marked and haircut), over 2021-2026 (analysis/buy_list_rates.py from the
loose panel, history/buy_list_rates.csv) -- adjusted by the two or three cuts the pump
autopsy found to hold inside a month (history/buy_list_lifts.csv, capped), and turned into
an expected net gain with the cell's typical win and loss. Everything the research found
NOT to work (momentum, volume, category momentum as a buy signal) is absent; what it found
to lose is the avoid layer below. The list exists so that a track record starts now
(history/buy_ledger.csv, read back in three and six months) and so Sid has a file to build
the pill against while the model under it is replaced.

The cell = regime x era x price tier x liquidity:
  regime      market breadth tonight: the share of English cards priced >= $150 with >= 3
              sales in the past year whose 30-day change is above +5%; flat < 25%, warm
              25-40%, hot >= 40% (2021-24 sat at 15-25%, the 2025-26 run at 30-53%). The
              regime is most of the forecast, so the list is short in a flat market.
  era         card year <= 2003 wotc, <= 2013 vintage, <= 2019 modern, else current
  tier        reference price (median of the last 3 clean sales): < $50, $50-150, $150-500, $500-2k, $2k+
  liquidity   clean PSA 10 sales in the last 90 days: 0, 1, 2-3, 4-7, 8+
The finest cell with >= MIN_CELL_N trades is used (fallbacks: without liquidity, then
without era, then the regime alone); `cell_level` says which. Its win rate is shrunk toward
the next coarser cell's with the weight of SHRINK_K trades (the hierarchical-shrinkage
baseline the research recommends; a 235-trade cell at 64% whose parent sits at 45% is scored
at 55%), and `base_rate_*` is that shrunk rate.

Cuts (odds ratios from history/buy_list_lifts.csv, per horizon and regime, each capped
at 2, the product at 3, applied on the odds scale so a 64% base cannot be pushed to 90%):
  stale30     no clean sale in the last 30 days (but one within 180)
  jp_heat     the character's Japanese cards (>= 2 with a value) up >= 15% over 30 days
  band_high   the card >= 1 sd above its own 1-year band (log weekly medians, >= 5 weeks),
              reference >= $150
p_win = the base moved by the odds ratios (cap 0.9); the 90% band is the cell's Wilson
interval moved the same way. exp_net_pct = p_win x the MEDIAN net of the cell's winners +
(1 - p_win) x the mean net of its losers (the winners' mean is carried by 2026's
multi-baggers); exp_net_usd = that x the reference price. Listed = no avoid flag,
p_win(180) >= MIN_P_LIST and exp_net_pct(180) > 0; `rank` sorts by exp_net_usd(180), "most
potential profit", `rank_pct` by exp_net_pct(180). In a hot market most priced cards
qualify, so the pill belongs on the top of either ranking (say rank <= 100 or rank_pct
<= 100), which is Sid's call; the file carries every qualifying card.

Avoid layer (any one keeps the card off the list; all measured to lose in every year):
  release_year   card year within a year of tonight's
  busy_spike     up >= 50% in 30 days on >= 3 sales
  vol_surge      last 30 days' sales >= 2x the year's monthly average
  below_band     >= 1 sd below its own 1-year band (the falling knife)
  dip            down >= 20% in 30 days
  chase_flat     the set's top price quartile (>= 8 English cards in the set, >= 2 with a
                 90-day change) averages < +10% over 90 days
  pending_sale   newest sale pending / unpaid / relisted
  title_mismatch the newest sale's eBay title doesn't match the card
Warnings (kept on the row, never a filter): illiquid_exit (<= 2 sales in 180 days),
unconfirmed (the newest sale is held back by the outlier filter). One more flag goes to
the ledger only, never to the list: tail_lead (bottom price quartile of its set while the
set's top quartile ran >= 30% in 90 days, reference >= $150: the autopsy's one promising
cell, logged for the forward test, not scored).

Reason: category leg (set's chase cards' 90-day run; the character's other cards' 30-day
change; Japanese copies), then the card leg (days without a sale, position vs own band, a
live eBay Buy It Now vs the level when one is verified), then the base rate of the cell in
tonight's regime, then warnings. Standard library only. Blank = unknown, never 0.

Outputs
  latest/buy_list.csv        the listed cards, one row each (columns: COLS below), rebuilt nightly
  history/buy_ledger.csv     the top LEDGER_TOP listed cards by $ and by %, plus the top
                             LEDGER_TOP tail_lead cards by $, with the inputs, keyed
                             (data_date, asset_id); a rerun on the same data_date replaces
                             that date's rows. The outcome reader comes later.
"""
import argparse
import csv
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LATEST = ROOT / "latest"
HISTORY = ROOT / "history"
MODEL = "v0"
HORIZONS = (90, 180)
MAIN_H = 180
MIN_CELL_N = 100
SHRINK_K = 200              # a cell's win rate is shrunk toward its parent cell's with the weight of SHRINK_K trades
OR_PRODUCT_CAP = 3.0        # the cuts' odds ratios multiply on the odds scale, product capped here
P_CAP = 0.90
LEDGER_TOP = 200            # ledger: the top LEDGER_TOP by $ and by %, plus the top LEDGER_TOP tail_lead rows by $
MIN_P_LIST = 0.15
STALE_DAYS = 30
MAX_AGE_DAYS = 180
BAND_MIN_WEEKS = 5
TIERS = [(50, "<50"), (150, "50-150"), (500, "150-500"), (2000, "500-2k"), (float("inf"), "2k+")]
LIQ = [(0, "0"), (1, "1"), (3, "2-3"), (7, "4-7"), (float("inf"), "8+")]
REGIMES = [(0.25, "flat"), (0.40, "warm"), (float("inf"), "hot")]
LEVELS = ["regime_era_tier_liq", "regime_era_tier", "regime_tier", "regime"]
LANGS = ("japanese", "german", "italian", "french", "spanish", "portuguese", "korean", "chinese", "dutch")
OTHER_RX = re.compile(r"lorcana|one piece|weiss schwarz|magic the gathering|yu-gi-oh|dragon ball|digimon|disney", re.I)
EXCLUDE = ("release_year", "busy_spike", "vol_surge", "below_band", "dip", "chase_flat", "pending_sale", "title_mismatch")
WARN = ("illiquid_exit", "unconfirmed")      # shown on the row; tail_lead goes to the ledger only
COLS = ["rank", "rank_pct", "asset_id", "card_name", "ref_price", "ref_basis", "p_win_180", "p_low_180", "p_high_180", "exp_net_pct_180", "exp_net_usd_180",
        "base_rate_180", "p_win_90", "exp_net_pct_90", "exp_net_usd_90", "base_rate_90", "cell", "cell_level", "cell_n", "regime", "breadth",
        "cuts", "avoid_flags", "live_bin_price", "live_bin_url", "reason", "model", "data_date", "scored_at"]
LEDGER_COLS = ["data_date", "scored_at", "model", "asset_id", "listed", "rank", "rank_pct", "ref_price", "p_win_180", "exp_net_pct_180", "exp_net_usd_180", "base_rate_180",
               "p_win_90", "exp_net_pct_90", "base_rate_90", "cell", "cell_level", "cell_n", "regime", "breadth", "cuts", "flags",
               "chg30", "chg90", "vol30", "vol90", "vol180", "days_since", "band_z", "set_lead_chg90", "set_price_q", "jp_chg30", "char_chg30"]


def fnum(s):
    try:
        return float(s) if s not in ("", None) else None
    except ValueError:
        return None


def fint(s):
    v = fnum(s)
    return int(v) if v is not None else 0


def language(card):
    t = " ".join((card.get("variety") or "", card.get("set") or "", card.get("card_name") or "")).lower()
    for lang in LANGS:
        if lang in t:
            return lang
    return "english"


def era(year):
    y = fnum(year)
    if y is None:
        return ""
    return "wotc" if y <= 2003 else "vintage" if y <= 2013 else "modern" if y <= 2019 else "current"


def bucket(x, edges):
    for hi, label in edges:
        if x < hi if hi != edges[-1][0] else True:
            return label
    return edges[-1][1]


def tier(ref):
    for hi, label in TIERS:
        if ref < hi:
            return label
    return TIERS[-1][1]


def liq(vol90):
    for hi, label in LIQ:
        if vol90 <= hi:
            return label
    return LIQ[-1][1]


def regime(breadth):
    for hi, label in REGIMES:
        if breadth < hi:
            return label
    return REGIMES[-1][1]


def odds_apply(p, odds_ratio):
    """p moved by an odds ratio: odds = p / (1 - p), times the ratio, back to a probability."""
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    o = p / (1 - p) * odds_ratio
    return o / (1 + o)


def wilson(p, n, z=1.645):
    if n <= 0:
        return (None, None)
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_rates(path):
    rates = {}
    for r in read_csv(path):
        rates[(int(r["horizon"]), r["level"], r["regime"], r["era"], r["tier"], r["liq"])] = {
            "n": int(float(r["n"])), "win_rate": fnum(r["win_rate"]), "win_net": fnum(r.get("win_net_median")) if fnum(r.get("win_net_median")) is not None else fnum(r["win_net_mean"]),
            "loss_net": fnum(r["loss_net_mean"])}
    return rates


def load_lifts(path):
    lifts = {}
    for r in read_csv(path):
        v = fnum(r.get("odds_ratio_capped"))
        if v is not None:
            lifts[(int(r["horizon"]), r["cut"], r["regime"])] = v
    return lifts


def shrunk_rate(n, rate, parent_rate, k=SHRINK_K):
    """the cell's win rate pulled toward its parent's with the weight of k trades (n / (n + k) on the cell)."""
    if parent_rate is None:
        return rate
    return (n * rate + k * parent_rate) / (n + k)


def lookup(rates, h, reg, er, ti, li):
    """finest cell with >= MIN_CELL_N trades, its win rate shrunk toward the next coarser cell's;
    (row with win_rate_raw and win_rate, level) or (None, '')."""
    keys = [("regime_era_tier_liq", reg, er, ti, li), ("regime_era_tier", reg, er, ti, ""), ("regime_tier", reg, "", ti, ""), ("regime", reg, "", "", "")]
    for i, (level, a, b, c, d) in enumerate(keys):
        row = rates.get((h, level, a, b, c, d))
        if row and row["n"] >= MIN_CELL_N and row["win_net"] is not None and row["loss_net"] is not None:
            parent = None
            for level2, a2, b2, c2, d2 in keys[i + 1:]:
                p2 = rates.get((h, level2, a2, b2, c2, d2))
                if p2 and p2["n"] >= MIN_CELL_N:
                    parent = p2["win_rate"]
                    break
            out = dict(row)
            out["win_rate_raw"] = row["win_rate"]
            out["win_rate"] = shrunk_rate(row["n"], row["win_rate"], parent)
            return out, level
    return None, ""


def band_zs(series_dir, data_date, refs):
    """asset -> z of log(ref) vs the card's log weekly medians over the past 365 days (>= BAND_MIN_WEEKS weeks)."""
    since = (data_date - timedelta(days=365)).isoformat()
    logs = defaultdict(list)
    for p in sorted(Path(series_dir).glob("*.csv")):
        with open(p, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                aid = r["asset_id"]
                if aid in refs and r["week_start"] > since and r["week_start"] <= data_date.isoformat():
                    m = fnum(r["median_price"])
                    if m and m > 0:
                        logs[aid].append(math.log(m))
    out = {}
    for aid, xs in logs.items():
        if len(xs) >= BAND_MIN_WEEKS:
            mean = sum(xs) / len(xs)
            var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
            sd = math.sqrt(var)
            out[aid] = (math.log(refs[aid]) - mean) / sd if sd > 0 else 0.0
    return out


def pct(x, d=0):
    return "" if x is None else f"{x:+.{d}f}%"


def compute(cards, zs, rates, lifts, data_date, now_iso):
    """-> (listed rows sorted by exp_net_usd_180, ledger rows, stats)."""
    today = data_date
    stats = {"english_psa10": 0, "with_price": 0, "scored": 0, "excluded": 0, "listed": 0, "tail_lead": 0}
    # pass 1: the universe and the group statistics
    univ = []
    for c in cards:
        if fnum(c.get("grade")) != 10.0 or OTHER_RX.search(c.get("card_name") or ""):
            continue
        lang = language(c)
        ref = fnum(c.get("median_last_3"))
        last = c.get("clean_last_sale_date") or ""
        c["_lang"], c["_ref"] = lang, ref
        if lang == "english":
            stats["english_psa10"] += 1
        if ref is None or ref <= 0 or not last:
            continue
        try:
            c["_days"] = (today - datetime.strptime(last[:10], "%Y-%m-%d").date()).days
        except ValueError:
            continue
        if c["_days"] > MAX_AGE_DAYS or c["_days"] < 0:
            continue
        c["_chg30"], c["_chg90"] = fnum(c.get("price_chg_30d_pct")), fnum(c.get("price_chg_90d_pct"))
        c["_vol30"], c["_vol90"], c["_vol180"], c["_vol1y"] = fint(c.get("volume_30d")), fint(c.get("volume_90d")), fint(c.get("volume_180d")), fint(c.get("volume_1y"))
        univ.append(c)
    en = [c for c in univ if c["_lang"] == "english"]
    stats["with_price"] = len(en)
    # breadth: English, ref >= 150, >= 3 sales in the past year
    liquid = [c for c in en if c["_ref"] >= 150 and c["_vol1y"] >= 3]
    breadth = (sum(1 for c in liquid if c["_chg30"] is not None and c["_chg30"] > 5) / len(liquid)) if liquid else 0.0
    reg = regime(breadth)
    # set-wide price quartile and the chase cards' run (English rows with a price)
    by_set = defaultdict(list)
    for c in en:
        by_set[c.get("set") or ""].append(c)
    set_q, set_lead = {}, {}
    for s, rows in by_set.items():
        if len(rows) < 8:
            continue
        rows.sort(key=lambda c: c["_ref"])
        n = len(rows)
        for i, c in enumerate(rows):
            set_q[c["asset_id"]] = (i + 1) / n
        chase = [c["_chg90"] for c in rows[int(math.ceil(0.75 * n)) - 1:] if c["_chg90"] is not None]
        if len(chase) >= 2:
            set_lead[s] = sum(chase) / len(chase)
    # character heat: Japanese copies, and the other cards of the subject (any language)
    jp, allc = defaultdict(list), defaultdict(list)
    for c in univ:
        if c["_chg30"] is None:
            continue
        if c["_lang"] == "japanese":
            jp[c.get("subject") or ""].append(c["_chg30"])
        allc[c.get("subject") or ""].append((c["asset_id"], c["_chg30"]))
    # pass 2: score
    listed, ledger = [], []
    for c in en:
        aid, ref = c["asset_id"], c["_ref"]
        er, ti, li = era(c.get("year")), tier(ref), liq(c["_vol90"])
        if not er:
            continue
        z = zs.get(aid)
        subj = c.get("subject") or ""
        jp_vals = jp.get(subj, [])
        jp_chg = sum(jp_vals) / len(jp_vals) if len(jp_vals) >= 2 else None
        others = [v for a, v in allc.get(subj, []) if a != aid]
        char_chg = sum(others) / len(others) if len(others) >= 2 else None
        lead = set_lead.get(c.get("set") or "")
        q = set_q.get(aid)
        flags = []
        y = fnum(c.get("year"))
        if y is not None and y >= today.year - 1:
            flags.append("release_year")
        if c["_chg30"] is not None and c["_chg30"] >= 50 and c["_vol30"] >= 3:
            flags.append("busy_spike")
        if c["_vol30"] >= 2 * max(c["_vol1y"] / 12.0, 0.25) and c["_vol30"] >= 2:
            flags.append("vol_surge")
        if z is not None and z <= -1:
            flags.append("below_band")
        if c["_chg30"] is not None and c["_chg30"] <= -20:
            flags.append("dip")
        if lead is not None and lead < 10:
            flags.append("chase_flat")
        if (c.get("last_sale_status") or "") in ("pending", "unpaid", "relisted"):
            flags.append("pending_sale")
        if (c.get("last_sale_title_check") or "") == "mismatch":
            flags.append("title_mismatch")
        if c["_vol180"] <= 2:
            flags.append("illiquid_exit")
        if (c.get("last_sale_unconfirmed") or "") == "1":
            flags.append("unconfirmed")
        if q is not None and q <= 0.25 and lead is not None and lead >= 30 and ref >= 150:
            flags.append("tail_lead")
        cuts = []
        if c["_days"] >= STALE_DAYS:
            cuts.append("stale30")
        if jp_chg is not None and jp_chg >= 15:
            cuts.append("jp_heat")
        if z is not None and z >= 1 and ref >= 150:
            cuts.append("band_high")
        score = {}
        for h in HORIZONS:
            row, level = lookup(rates, h, reg, er, ti, li)
            if row is None:
                score[h] = None
                continue
            orp = 1.0
            for cut in cuts:
                orp *= lifts.get((h, cut, reg), 1.0)
            orp = min(orp, OR_PRODUCT_CAP)
            p = min(odds_apply(row["win_rate"], orp), P_CAP)
            lo, hi = wilson(row["win_rate"], row["n"])
            lo, hi = min(odds_apply(lo, orp), P_CAP), min(odds_apply(hi, orp), P_CAP)
            enet = p * row["win_net"] + (1 - p) * row["loss_net"]
            score[h] = {"p": p, "lo": lo, "hi": hi, "base": row["win_rate"], "n": row["n"], "level": level, "enet": enet, "eusd": enet * ref}
        if score.get(MAIN_H) is None:
            continue
        stats["scored"] += 1
        s180, s90 = score[MAIN_H], score.get(90)
        excluded = [f for f in flags if f in EXCLUDE]
        warn = [f for f in flags if f in WARN]
        is_listed = not excluded and s180["p"] >= MIN_P_LIST and s180["enet"] > 0
        if excluded:
            stats["excluded"] += 1
        if "tail_lead" in flags:
            stats["tail_lead"] += 1
        bin_price = fnum(c.get("lowest_bin_price"))
        bin_ok = bin_price is not None and (c.get("lowest_bin_verified_at") or "") != "" and (c.get("lowest_bin_suspect") or "") != "1"
        if is_listed:
            stats["listed"] += 1
            parts = []
            if lead is not None and lead >= 30:
                parts.append(f"set's chase cards {pct(lead)} / 90 d")
            if char_chg is not None and char_chg >= 15:
                parts.append(f"{subj}'s other cards {pct(char_chg)} / 30 d")
            if "jp_heat" in cuts:
                parts.append(f"Japanese copies {pct(jp_chg)} / 30 d")
            if "stale30" in cuts:
                parts.append(f"no sale in {c['_days']} d")
            if z is not None and abs(z) >= 1:
                parts.append(f"{z:+.1f} sd vs own 1-y band")
            if bin_ok:
                parts.append(f"live eBay Buy It Now ${bin_price:,.0f} ({pct(100 * (bin_price / ref - 1))} vs level)")
            parts.append(f"base {100 * s180['base']:.0f}% for {er} ${ti} with {li} sales/90 d in a {reg} market (breadth {100 * breadth:.0f}%, n {s180['n']:,})")
            parts.extend(w.replace("_", " ") for w in warn)
            listed.append({
                "asset_id": aid, "card_name": c.get("card_name") or "", "ref_price": f"{ref:.2f}", "ref_basis": "med3",
                "p_win_180": f"{s180['p']:.3f}", "p_low_180": f"{s180['lo']:.3f}", "p_high_180": f"{s180['hi']:.3f}",
                "exp_net_pct_180": f"{100 * s180['enet']:.1f}", "exp_net_usd_180": f"{s180['eusd']:.0f}", "base_rate_180": f"{s180['base']:.3f}",
                "p_win_90": f"{s90['p']:.3f}" if s90 else "", "exp_net_pct_90": f"{100 * s90['enet']:.1f}" if s90 else "",
                "exp_net_usd_90": f"{s90['eusd']:.0f}" if s90 else "", "base_rate_90": f"{s90['base']:.3f}" if s90 else "",
                "cell": f"{reg}|{er}|{ti}|{li}", "cell_level": s180["level"], "cell_n": str(s180["n"]), "regime": reg, "breadth": f"{breadth:.3f}",
                "cuts": " ".join(cuts), "avoid_flags": " ".join(warn), "live_bin_price": f"{bin_price:.2f}" if bin_ok else "",
                "live_bin_url": (c.get("lowest_bin_url") or "") if bin_ok else "", "reason": "; ".join(parts), "model": MODEL,
                "data_date": today.isoformat(), "scored_at": now_iso, "_eusd": s180["eusd"],
            })
        if is_listed or "tail_lead" in flags:
            ledger.append({
                "data_date": today.isoformat(), "scored_at": now_iso, "model": MODEL, "asset_id": aid, "listed": "1" if is_listed else "0", "rank": "",
                "ref_price": f"{ref:.2f}", "p_win_180": f"{s180['p']:.3f}", "exp_net_pct_180": f"{100 * s180['enet']:.1f}", "exp_net_usd_180": f"{s180['eusd']:.0f}",
                "base_rate_180": f"{s180['base']:.3f}", "p_win_90": f"{s90['p']:.3f}" if s90 else "", "exp_net_pct_90": f"{100 * s90['enet']:.1f}" if s90 else "",
                "base_rate_90": f"{s90['base']:.3f}" if s90 else "", "cell": f"{reg}|{er}|{ti}|{li}", "cell_level": s180["level"], "cell_n": str(s180["n"]),
                "regime": reg, "breadth": f"{breadth:.3f}", "cuts": " ".join(cuts), "flags": " ".join(flags),
                "chg30": "" if c["_chg30"] is None else f"{c['_chg30']:.2f}", "chg90": "" if c["_chg90"] is None else f"{c['_chg90']:.2f}",
                "vol30": str(c["_vol30"]), "vol90": str(c["_vol90"]), "vol180": str(c["_vol180"]), "days_since": str(c["_days"]),
                "band_z": "" if z is None else f"{z:.2f}", "set_lead_chg90": "" if lead is None else f"{lead:.1f}", "set_price_q": "" if q is None else f"{q:.2f}",
                "jp_chg30": "" if jp_chg is None else f"{jp_chg:.1f}", "char_chg30": "" if char_chg is None else f"{char_chg:.1f}", "_aid": aid,
            })
    listed.sort(key=lambda r: (-float(r["exp_net_pct_180"]), -r["_eusd"]))
    rank_pct = {r["asset_id"]: str(i) for i, r in enumerate(listed, 1)}
    listed.sort(key=lambda r: -r["_eusd"])
    rank = {}
    for i, r in enumerate(listed, 1):
        r["rank"], r["rank_pct"] = str(i), rank_pct[r["asset_id"]]
        rank[r["asset_id"]] = str(i)
    for r in ledger:
        r["rank"], r["rank_pct"] = rank.get(r["_aid"], ""), rank_pct.get(r["_aid"], "")
    keep = set()
    keep.update(r["asset_id"] for r in listed[:LEDGER_TOP])
    keep.update(r["asset_id"] for r in sorted(listed, key=lambda r: int(r["rank_pct"]))[:LEDGER_TOP])
    tails = sorted((r for r in ledger if "tail_lead" in r["flags"].split()), key=lambda r: -float(r["exp_net_usd_180"]))
    keep.update(r["_aid"] for r in tails[:LEDGER_TOP])
    ledger = [r for r in ledger if r["_aid"] in keep]
    stats["breadth"], stats["regime"], stats["ledger_rows"] = breadth, reg, len(ledger)
    return listed, ledger, stats


def write_rows(path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def update_ledger(path, rows, data_date):
    """replace this data_date's rows, keep every other date, append-only otherwise."""
    old = [r for r in read_csv(path) if r.get("data_date") != data_date] if Path(path).exists() else []
    write_rows(path, LEDGER_COLS, old + rows)
    return len(old), len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--latest", default=str(LATEST))
    ap.add_argument("--rates", default=str(HISTORY), help="folder holding buy_list_rates.csv and buy_list_lifts.csv")
    ap.add_argument("--out", default=None, help="write buy_list.csv here instead of --latest")
    ap.add_argument("--ledger", default=str(HISTORY / "buy_ledger.csv"))
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--data-date", default=None, help="override latest/summary.json data_date")
    a = ap.parse_args()
    latest = Path(a.latest)
    data_date = a.data_date or json.load(open(latest / "summary.json"))["data_date"]
    data_date = datetime.strptime(data_date, "%Y-%m-%d").date()
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    cards = read_csv(latest / "cards.csv")
    rates = load_rates(Path(a.rates) / "buy_list_rates.csv")
    lifts = load_lifts(Path(a.rates) / "buy_list_lifts.csv")
    refs = {c["asset_id"]: fnum(c.get("median_last_3")) for c in cards if fnum(c.get("median_last_3")) and language(c) == "english" and fnum(c.get("grade")) == 10.0}
    zs = band_zs(latest / "series", data_date, refs)
    listed, ledger, stats = compute(cards, zs, rates, lifts, data_date, now_iso)
    out_dir = Path(a.out) if a.out else latest
    out_dir.mkdir(parents=True, exist_ok=True)
    write_rows(out_dir / "buy_list.csv", COLS, listed)
    msg = (f"buy list {data_date}: breadth {100 * stats['breadth']:.1f}% ({stats['regime']}); {stats['english_psa10']:,} English PSA 10 cards, "
           f"{stats['with_price']:,} priced within {MAX_AGE_DAYS} d, {stats['scored']:,} scored, {stats['excluded']:,} excluded by the avoid layer, "
           f"{stats['listed']:,} listed, {stats['tail_lead']:,} tail_lead rows -> {out_dir / 'buy_list.csv'}; ledger rows {stats['ledger_rows']}")
    if not a.no_ledger:
        n_old, n_new = update_ledger(a.ledger, ledger, data_date.isoformat())
        msg += f"; ledger {a.ledger}: {n_new} rows for {data_date} ({n_old} other rows kept)"
    print(msg)


if __name__ == "__main__":
    main()
