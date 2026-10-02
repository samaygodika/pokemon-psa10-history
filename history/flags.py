#!/usr/bin/env python3
"""Forward log of Sid's PSA 9 buy conditions (2026-09-27), step 7 of the nightly.

    python3 history/flags.py            # reads latest/cards.csv + latest/characters.csv, appends to history/

Sid's rule (PokeSniper PR #1, 2026-09-27): buy the PSA 9 of a blue-chip or top-50 Pokemon
card whose PSA 10 gained 100%+ (or 50%+) over the past month while its PSA 9 has not moved,
ideally while its card type (LV.X, Gold Star, Prime/LEGEND ...) is running as a category.
The backtests (analysis/psa9_hype.py, analysis/psa9_buyrule.py) can only look back; this
logs the conditions as the feed sees them each night so the PSA 9s can be checked 3, 6 and
12 months later (analysis/psa9_flags_outcomes.py, once there is anything to check).

Two append-only, committed files, one row per (data_date, key), never rewritten:

    history/psa9_flags.csv            one row per card-night that matters: the card's
                                      subject is in the top 50 by alt-side market cap of
                                      cards up to 2013 (latest/characters.csv, the app's roster rank) and its PSA 10 is up 50%+
                                      over 30 days, OR its card type is "hot" (group median
                                      30-day change >= 30%) and the subject is top-50, OR
                                      the card is in nightly/track_assets.txt (Sid's own
                                      buys). Prices and pops as of that night, and the
                                      flags that held, space-separated:
                                        top50 blue_chip (top 10) psa10_up_50 psa10_up_100
                                        psa9_flat (PSA 9 30-day change < +10%, and known)
                                        buy_rule_50 buy_rule_100 (top50 & up & psa9_flat)
                                        cat_hot_15/30/50/70/100 (the card type's median)
                                        tracked
    history/psa9_category_moves.csv   one row per (data_date, card type): cards with a
                                      30-day PSA 10 change, its median, and the share of
                                      cards up 50%+ / 100%+ -- the "category running"
                                      history Sid asked for.

"30-day change" is the feed's price_chg_30d_pct / psa9_price_chg_30d_pct (median of the
last 3 clean sales vs 30 days earlier), i.e. what the app shows, not the monthly buckets
the backtests use. From the 2026-10-01 log on, the PSA 10 one also counts sales the
outlier filter holds back on the high side (metrics.py, chg_held_windows); rows logged
before that don't. Card types are the analysis/psa9_hype.py classifier, ported to plain
regexes so the nightly stays standard-library only. Standard library only.
"""
import csv
import re
import statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LATEST = ROOT / "latest"
FLAGS_PATH = HERE / "psa9_flags.csv"
MOVES_PATH = HERE / "psa9_category_moves.csv"
TRACK_PATH = ROOT / "nightly" / "track_assets.txt"

TOP_N, BLUE_CHIP_N = 50, 10           # Sid's app: Blue Chip = top 10 Pokemon by market cap
UP_LEVELS = (50, 100)
CAT_LEVELS = (15, 30, 50, 70, 100)
PSA9_FLAT_MAX = 10.0                  # PSA 9 30-day change below this = "hasn't moved yet"
MIN_GROUP_CARDS = 5
TIERS = ((0, 1_000, "< $1K"), (1_000, 10_000, "$1K-$10K"), (10_000, float("inf"), ">= $10K"))

FLAG_COLS = ["data_date", "asset_id", "card_name", "subject", "card_type", "tier", "psa10_price", "psa10_last_sale",
             "psa10_chg_30d_pct", "psa9_price", "psa9_last_sale", "psa9_chg_30d_pct", "pop10", "pop9",
             "cat_median_chg_30d_pct", "flags"]
MOVE_COLS = ["data_date", "card_type", "cards_with_chg", "median_chg_30d_pct", "share_up_50", "share_up_100",
             "top50_cards_with_chg", "top50_median_chg_30d_pct"]


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def card_number(s):
    m = re.search(r"\d+", s or "")
    return int(m.group()) if m else None


def card_type(r):
    """analysis/psa9_hype.py's card_types(), one card at a time, same rules and order."""
    name, st, subj = r.get("card_name") or "", r.get("set") or "", r.get("subject") or ""
    yr = f(r.get("year"))
    num = card_number(r.get("card_number"))
    ex_card = re.search(r"\bex\b", subj, re.I) is not None
    if yr is None:
        yr = 0
    if yr <= 2008 and re.search(r"Gold Star|\bStar\b(?= #| Holo)", name):
        return "Gold Star"
    if 2006 <= yr <= 2010 and re.search(r"\bLv\.?\s?X\b|\bLV\.X\b", name, re.I) \
            and not re.search(r"Lv\.?\s?X (?:Deck|Collection)", name, re.I):
        return "LV.X"
    if yr <= 2003 and re.search(r"\bShining\b", name):
        return "Neo Shining"
    if num is not None and (("Aquapolis" in st and 148 <= num <= 150) or ("Skyridge" in st and 145 <= num <= 150)) \
            and "#H" not in name:
        return "e-card Crystal"
    if 2010 <= yr <= 2011 and (re.search(r"\bPrime\b", name)
                               or (re.search(r"\bLegend\b(?!s|ary)", name)
                                   and not re.search(r"Legend (?:Promo|Perfect)|Promo Legend|Legend \w+ Constructed|Japanese Legend", name))):
        return "HGSS Prime/LEGEND"
    if 2003 <= yr <= 2007 and ex_card:
        return "EX-era ex"
    if yr <= 2003 and "1st Edition" in name:
        return "WOTC 1st Edition"
    if yr <= 2002:
        return "WOTC other"
    if yr >= 2020 and re.search(r"\b(?:V|VMAX|VSTAR)\b(?= #| Holo|/|$)", name):
        return "Modern V/VMAX/VSTAR"
    if yr >= 2023 and ex_card:
        return "Modern ex (2023+)"
    if yr >= 2014 and re.search(r"Illustration|Alternate Art|Alt Art|Full Art|Secret|Rainbow|Character Rare|Trainer Gallery", name, re.I):
        return "Modern alt art / illustration"
    if re.search(r"\bPromo", name) or re.search(r"\bPromo", st):
        return "Promo"
    return ""


def tier_of(price):
    if price is None:
        return ""
    return next(label for lo, hi, label in TIERS if lo <= price < hi)


def read_csv(path):
    if not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


CAP_COL = "alt_market_cap_le2013_usd"   # the app's roster ranks Pokemon by the cap of their cards up to 2013


def top_characters(chars_path=LATEST / "characters.csv", cap_col=CAP_COL):
    """The app's top-50 list (PokeSniper rosterRanking: alt-side cap of cards <= 2013, so
    Palkia sits at #51) and its Blue Chip list, the first BLUE_CHIP_N of them. The app ranks
    blue chips by its own matched cap; by the alt-side cap the top 10 differ in one name."""
    rows = sorted(read_csv(chars_path), key=lambda r: -(f(r.get(cap_col)) or 0))
    names = [r["character"] for r in rows if r.get("character")]
    return names[:TOP_N], names[:BLUE_CHIP_N]


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def subject_matches(subject, names):
    """Whole-word match of a character name in the card's subject ('Mewtwo Lv.X' -> Mewtwo),
    hyphen or space alike (Ho-Oh / Ho Oh), the way nightly/filter_subjects.py scopes cards."""
    sub = f" {norm(subject)} "
    return next((n for n in names if f" {norm(n)} " in sub), None)


def tracked_assets(path=TRACK_PATH):
    ids = set()
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                ids.add(line)
    return ids


def compute(cards, top50, blue, tracked, data_date):
    """(flag rows, category rows) for one night's latest/cards.csv."""
    by_type = defaultdict(list)
    info = {}
    for r in cards:
        ct = card_type(r)
        chg = f(r.get("price_chg_30d_pct"))
        who = subject_matches(r.get("subject"), top50)
        info[r["asset_id"]] = (ct, chg, who)
        if ct and chg is not None:
            by_type[ct].append((chg, who is not None))
    moves, cat_median = [], {}
    for ct in sorted(by_type):
        vals = [c for c, _ in by_type[ct]]
        top_vals = [c for c, t in by_type[ct] if t]
        med = statistics.median(vals) if len(vals) >= MIN_GROUP_CARDS else None
        cat_median[ct] = med
        moves.append({
            "data_date": data_date, "card_type": ct, "cards_with_chg": len(vals),
            "median_chg_30d_pct": "" if med is None else f"{med:.1f}",
            "share_up_50": f"{sum(1 for c in vals if c >= 50) / len(vals):.3f}",
            "share_up_100": f"{sum(1 for c in vals if c >= 100) / len(vals):.3f}",
            "top50_cards_with_chg": len(top_vals),
            "top50_median_chg_30d_pct": f"{statistics.median(top_vals):.1f}" if len(top_vals) >= MIN_GROUP_CARDS else "",
        })
    flags_out = []
    for r in cards:
        aid = r["asset_id"]
        ct, chg10, who = info[aid]
        chg9 = f(r.get("psa9_price_chg_30d_pct"))
        med = cat_median.get(ct)
        flags = []
        if who:
            flags.append("top50")
            if subject_matches(r.get("subject"), blue):
                flags.append("blue_chip")
        for lvl in UP_LEVELS:
            if chg10 is not None and chg10 >= lvl:
                flags.append(f"psa10_up_{lvl}")
        flat = chg9 is not None and chg9 < PSA9_FLAT_MAX
        if flat:
            flags.append("psa9_flat")
        for lvl in UP_LEVELS:
            if who and flat and f"psa10_up_{lvl}" in flags:
                flags.append(f"buy_rule_{lvl}")
        for lvl in CAT_LEVELS:
            if med is not None and med >= lvl:
                flags.append(f"cat_hot_{lvl}")
        if aid in tracked:
            flags.append("tracked")
        keep = ("psa10_up_50" in flags and who) or ("cat_hot_30" in flags and who) or aid in tracked
        if not keep:
            continue
        p10 = f(r.get("median_last_3"))
        flags_out.append({
            "data_date": data_date, "asset_id": aid, "card_name": r.get("card_name", ""), "subject": r.get("subject", ""),
            "card_type": ct, "tier": tier_of(p10),
            "psa10_price": r.get("median_last_3", ""), "psa10_last_sale": r.get("last_sale_price", ""),
            "psa10_chg_30d_pct": r.get("price_chg_30d_pct", ""),
            "psa9_price": r.get("psa9_median_last_3", ""), "psa9_last_sale": r.get("psa9_last_sale_price", ""),
            "psa9_chg_30d_pct": r.get("psa9_price_chg_30d_pct", ""),
            "pop10": r.get("pop_at_grade", ""), "pop9": r.get("pop_at_grade_9", ""),
            "cat_median_chg_30d_pct": "" if med is None else f"{med:.1f}",
            "flags": " ".join(flags),
        })
    return flags_out, moves


def append_rows(path, cols, rows, key):
    """Append rows whose key is not in the file yet. Returns how many were added."""
    existing = {key(r) for r in read_csv(path)}
    new = [r for r in rows if key(r) not in existing]
    if not new:
        return 0
    write_header = not Path(path).exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        if write_header:
            w.writeheader()
        w.writerows(new)
    return len(new)


def main():
    import json
    cards = read_csv(LATEST / "cards.csv")
    data_date = json.load(open(LATEST / "summary.json"))["data_date"]
    top50, blue = top_characters()
    tracked = tracked_assets()
    flags_out, moves = compute(cards, top50, blue, tracked, data_date)
    n_f = append_rows(FLAGS_PATH, FLAG_COLS, flags_out, lambda r: (r["data_date"], r["asset_id"]))
    n_m = append_rows(MOVES_PATH, MOVE_COLS, moves, lambda r: (r["data_date"], r["card_type"]))
    n_rule = {lvl: sum(1 for r in flags_out if f"buy_rule_{lvl}" in r["flags"].split()) for lvl in UP_LEVELS}
    hot = [(m["card_type"], m["median_chg_30d_pct"]) for m in moves if m["median_chg_30d_pct"] and float(m["median_chg_30d_pct"]) >= 30]
    print(f"psa9 flags {data_date}: {len(flags_out)} card rows ({n_f} new), buy_rule_50 {n_rule[50]}, buy_rule_100 {n_rule[100]}, "
          f"{len(tracked)} tracked; {len(moves)} category rows ({n_m} new)" + (f"; hot categories (median >= 30%): {hot}" if hot else ""))


if __name__ == "__main__":
    main()
