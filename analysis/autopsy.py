#!/usr/bin/env python3
"""Pump autopsy: what was observable before the cards that went on to win.

    analysis/.venv/bin/python analysis/autopsy.py                 # -> analysis/out/autopsy.md, autopsy_signals.csv, autopsy_top.csv, autopsy_top_sales.md
    analysis/.venv/bin/python analysis/autopsy.py --horizon 90
    analysis/.venv/bin/python analysis/autopsy.py --vintage       # year <= 2013 only, tag _vintage

Step 1 of handoff/2026-10-21-buy-model-strategy.md §5. Reads the point-in-time
panel (analysis/out/panel.csv, built by panel.py: one row per (asset, month
start), features from sales on or before the date, targets strictly after it)
and asks, for every card-month that went on to WIN -- net return >= WIN_NET
under the money target at the horizon (entry = median of the sales in the 3
weeks after the anchor grossed up for tax / shipping / premium, exit = 40th
percentile of the sales in the exit window net of fees, illiquid exits marked
at the last sale and haircut) -- which candidate signals were ON at the anchor,
and for every signal, how often the rows it flagged went on to win.

  coverage    share of rows with the signal on
  recall      share of winning rows that had the signal on at the anchor
  precision   share of rows with the signal on that went on to win
  base        share of all rows that won (same year for the by-year table)
  lift        precision / base
  t (months)  over months with >= MIN_ON flagged rows: mean of
              (precision_m - base_m) / sd * sqrt(months). The regime cancels
              inside a month, so this is the cross-sectional evidence.
  net | on    median and mean net return of the flagged rows

Only anchors whose exit window closed before the panel's data end count
(DATA_END - horizon - 30 days), so a 2026 anchor marked at its last sale does
not pass as an outcome. Monthly anchors overlap (one card can win at several
consecutive anchors), so counts are card-months, not independent trades.

The panel's own features are point-in-time by construction (test_panel.py).
Added here: Japanese heat for the character and set momentum (aggregates of
point-in-time features over OTHER rows at the same date), Wikipedia pageview
surprise (9 characters cached from 2024-01, so 2025+ anchors only), and two
that are NOT point-in-time and are labelled so: today's PSA 10 pop
(scarcity_gap) and today's character rank (latest/characters.csv).

Top pumps: the largest gross moves among closed 2025+ anchors, one row per
asset, with the observables, the co-movement of set-mates and character-mates
at the same anchor (outcome-side, for cause tagging only, never a feature) and
a sales-path digest from history/sales/ for the hand-read.
"""
import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "analysis" / "out"
WIN_NET = 0.30
MIN_ON = 20
DATA_END = pd.Timestamp("2026-09-18")   # panel.csv was built 2026-09-19 from the store as of then
TOP_K = 50
OTHER_FRANCHISES = r"lorcana|one piece|magic the gathering|magic: the gathering|yu-gi-oh|yugioh|dragon ball|digimon|weiss schwarz|flesh and blood|star wars|marvel|garbage pail|naruto|my hero academia|union arena|riftbound|final fantasy|disney|gundam|cardfight|metazoo|sorcery|kryptik|basketball|football|baseball|hockey|soccer|wrestling|ufc|f1 |formula 1|nba|nfl|mlb"
TOP_START = pd.Timestamp("2025-01-01")


def load_pageviews():
    out = {}
    for f in sorted((OUT / "pageviews").glob("*.json")):
        items = json.load(open(f))["items"]
        s = pd.Series({pd.Timestamp(i["timestamp"][:8]): float(i["views"]) for i in items}).sort_index()
        out[f.stem] = s
    return out


def word_match_map(subjects, names):
    """subject -> first name (longest first) that appears as a whole word in it."""
    names = sorted((n for n in names if isinstance(n, str) and n), key=len, reverse=True)
    pats = [(n, n.lower(), re.compile(r"(?<![a-z])" + re.escape(n.lower()) + r"(?![a-z])")) for n in names]
    out = {}
    for s in subjects:
        sl = str(s).lower()
        for n, nl, pat in pats:
            if nl in sl and pat.search(sl):
                out[s] = n
                break
    return out


def add_features(p, chars, pv):
    # Japanese heat: mean mom30 over the character's Japanese rows at t (other assets by construction)
    jp = p[p.lang == "japanese"].groupby(["date", "subject"]).mom30.agg(["mean", "count"])
    jp.columns = ["char_jp_mom30", "char_jp_n"]
    p = p.join(jp, on=["date", "subject"])
    p.loc[p.char_jp_n.fillna(0) < 2, "char_jp_mom30"] = np.nan
    # set momentum: mean mom30 over the OTHER English rows of the same set at t
    en = p.lang == "english"
    g = p.loc[en].groupby(["date", "set"]).mom30
    s_sum = g.transform("sum")
    n = g.transform("count")
    own = p.loc[en, "mom30"]
    others = (n - own.notna().astype(int))
    p["set_n"] = np.nan
    p["set_mom30"] = np.nan
    p.loc[en, "set_n"] = others
    p.loc[en, "set_mom30"] = ((s_sum - own.fillna(0)) / others).where(others >= 2)
    # set-level lagged signals over the OTHER English rows of the set at t:
    #   set_mom90      mean 90 d momentum of the set-mates
    #   set_lead_mom90 mean 90 d momentum of the set's top-price-quartile mates (the chase cards)
    #   sibling_ran    the largest 180 d momentum among the set-mates
    g90 = p.loc[en].groupby(["date", "set"]).mom90
    s90, n90 = g90.transform("sum"), g90.transform("count")
    own90 = p.loc[en, "mom90"]
    oth90 = n90 - own90.notna().astype(int)
    p["set_mom90"] = np.nan
    p.loc[en, "set_mom90"] = ((s90 - own90.fillna(0)) / oth90).where(oth90 >= 2)
    pe = p.loc[en, ["date", "set", "ref", "mom90", "mom180"]].copy()
    pe["q"] = pe.groupby(["date", "set"]).ref.rank(pct=True)
    lead = pe[pe.q >= 0.75].groupby(["date", "set"]).agg(set_lead_mom90=("mom90", "mean"), set_lead_n=("mom90", "count"), set_lead_mom180=("mom180", "mean"), set_lead_n180=("mom180", "count"))
    p = p.join(lead, on=["date", "set"])
    p.loc[p.set_lead_n.fillna(0) < 2, "set_lead_mom90"] = np.nan
    p.loc[p.set_lead_n180.fillna(0) < 2, "set_lead_mom180"] = np.nan
    gm = pe.groupby(["date", "set"]).mom180
    mx, n180 = gm.transform("max"), gm.transform("count")
    # the max over OTHER rows: when own is the max, fall back to the second largest
    sec = pe.groupby(["date", "set"]).mom180.transform(lambda x: x.nlargest(2).iloc[-1] if x.notna().sum() >= 2 else np.nan)
    sib = mx.where(pe.mom180.fillna(-np.inf) < mx, sec)
    p["sibling_ran"] = np.nan
    p.loc[pe.index, "sibling_ran"] = sib.where(n180 >= 3)
    # within-set price tier at t (sets with >= 8 English rows): the long tail vs the chase cards
    pq = pe.groupby(["date", "set"]).ref.rank(pct=True)
    nset = pe.groupby(["date", "set"]).ref.transform("count")
    p["set_price_q"] = np.nan
    p.loc[pe.index, "set_price_q"] = pq.where(nset >= 8)
    # era momentum: mean 90 d momentum of the OTHER English rows of the same era at t
    ge = p.loc[en].groupby(["date", "era"]).mom90
    se, ne = ge.transform("sum"), ge.transform("count")
    p["era_mom90"] = np.nan
    p.loc[en, "era_mom90"] = ((se - own90.fillna(0)) / (ne - own90.notna().astype(int))).where(ne >= 20)
    # regime nowcast: share of English liquid rows with mom30 > +5% at t (the universe median is 0 in most months)
    br = p.loc[en & (p.ref >= 150)].groupby("date").mom30.agg(lambda x: (x > 0.05).mean()).rename("breadth")
    p = p.join(br, on="date")
    # today's character rank (NOT point-in-time)
    names = chars.character.dropna().unique()
    m = word_match_map(p.subject.dropna().unique(), names)
    rank = dict(zip(chars.character, chars.rank_le2013))
    p["char_name"] = p.subject.map(m)
    p["char_rank"] = p.char_name.map(rank)
    # Wikipedia pageview surprise: log mean views in the 28 d to t vs the 365 d before that
    vals = np.full(len(p), np.nan)
    subj = p.subject.fillna("").str.lower()
    for name, s in pv.items():
        pat = re.compile(r"(?<![a-z])" + re.escape(name.lower()) + r"(?![a-z])")
        rows = subj.str.contains(pat)
        if not rows.any():
            continue
        for d in p.loc[rows, "date"].unique():
            d = pd.Timestamp(d)
            recent = s[(s.index > d - pd.Timedelta(days=28)) & (s.index <= d)]
            base = s[(s.index > d - pd.Timedelta(days=393)) & (s.index <= d - pd.Timedelta(days=28))]
            if len(recent) >= 20 and len(base) >= 300:
                idx = np.flatnonzero(rows.values & (p.date.values == np.datetime64(d)))
                vals[idx] = np.log(recent.mean()) - np.log(base.mean())
    p["pv_surprise"] = vals
    return p


def within_month_pct(u, col):
    return u.groupby("date")[col].rank(pct=True)


def build_signals(u):
    S = []

    def add(name, mask, kind, desc):
        S.append((name, mask.fillna(False).astype(bool), kind, desc))

    add("mom30_up20", u.mom30 >= 0.2, "buy", "own 30 d momentum >= +20%")
    add("mom90_up30", u.mom90 >= 0.3, "buy", "own 90 d momentum >= +30%")
    add("mom30_down20", u.mom30 <= -0.2, "buy", "own 30 d momentum <= -20% (contrarian)")
    add("thin_spike", (u.mom30 >= 0.5) & (u.vol30 <= 2), "buy", "up >= 50% in 30 d on <= 2 sales")
    add("busy_spike", (u.mom30 >= 0.5) & (u.vol30 >= 3), "avoid", "up >= 50% in 30 d on >= 3 sales")
    add("vol_surge2", u.vol_surge >= 2, "avoid", "last month's sales >= 2x the year's monthly average")
    add("quiet", (u.vol30 <= 1) & (u.vol90 >= 3), "buy", "<= 1 sale in 30 d after >= 3 in 90 d (quiet, not stale)")
    add("stale30", u.days_since_sale >= 30, "buy", "no sale in 30 d (Sleeper-like)")
    add("char_mom30_15", u.char_mom30 >= 0.15, "buy", "other cards of the character (any language) up >= 15% in 30 d")
    add("char_jp_mom30_15", u.char_jp_mom30 >= 0.15, "buy", "the character's Japanese cards (>= 2) up >= 15% in 30 d")
    add("set_mom30_20", u.set_mom30 >= 0.2, "buy", "other English cards of the set (>= 2) up >= 20% in 30 d")
    add("set_mom90_20", u.set_mom90 >= 0.2, "buy", "other English cards of the set (>= 2) up >= 20% in 90 d")
    add("set_lead_mom90_30", u.set_lead_mom90 >= 0.3, "buy", "the set's top-price-quartile cards (>= 2) up >= 30% in 90 d (chase cards ran)")
    add("sibling_ran_100", u.sibling_ran >= 1.0, "buy", "a set-mate (>= 3 in the set) doubled over 180 d")
    add("era_mom90_20", u.era_mom90 >= 0.2, "buy", "other English cards of the era up >= 20% in 90 d")
    add("set_cheap_q", u.set_price_q <= 0.25, "buy", "bottom price quartile of its set (sets with >= 8 cards): the long tail")
    add("set_chase_q", u.set_price_q >= 0.75, "context", "top price quartile of its set: the chase cards")
    add("tail_after_lead", (u.set_price_q <= 0.25) & (u.set_lead_mom90 >= 0.3), "buy", "long tail of a set whose chase cards ran >= 30% in 90 d (the catch-up idea)")
    add("tail_after_lead50", (u.set_price_q <= 0.25) & (u.set_lead_mom90 >= 0.5), "buy", "long tail of a set whose chase cards ran >= 50% in 90 d")
    add("tail_after_lead180", (u.set_price_q <= 0.25) & (u.set_lead_mom180 >= 0.5), "buy", "long tail of a set whose chase cards ran >= 50% in 180 d")
    add("tail_no_lead", (u.set_price_q <= 0.25) & (u.set_lead_mom90 < 0.1), "buy", "long tail of a set whose chase cards did NOT run (< 10% in 90 d): the control")
    add("chase_after_lead", (u.set_price_q >= 0.75) & (u.set_lead_mom90 >= 0.3), "buy", "a chase card of a set whose chase cards ran >= 30% in 90 d (momentum on the leaders)")
    add("mid_after_lead", (u.set_price_q > 0.25) & (u.set_price_q < 0.75) & (u.set_lead_mom90 >= 0.3), "buy", "middle of a set whose chase cards ran >= 30% in 90 d")
    add("band_low", u.band_z <= -1, "avoid", ">= 1 sd below the card's own 1 y band")
    add("band_high", u.band_z >= 1, "buy", ">= 1 sd above the card's own 1 y band")
    add("peer_cheap_dec", within_month_pct(u, "peer_resid") <= 0.10, "buy", "cheapest decile vs set-mates after the character effect")
    add("peer_rich_dec", within_month_pct(u, "peer_resid") >= 0.90, "avoid", "dearest decile vs set-mates after the character effect")
    add("new_set", u.age_years <= 1, "avoid", "release-year card")
    add("pv_surprise_30", u.pv_surprise >= 0.30, "buy", "Wikipedia views last 28 d >= 35% above the prior year (9 characters, 2025+)")
    add("top50_char", u.char_rank <= 50, "context", "character in TODAY's top-50 (<= 2013 cap; not point-in-time)")
    add("top75_char", u.char_rank <= 75, "context", "character in TODAY's top-75 (not point-in-time)")
    add("scarcity_top_dec", within_month_pct(u, "scarcity_gap") >= 0.90, "context", "scarcer than its price rank says (TODAY's pop; not point-in-time)")
    add("breadth_35", u.breadth >= 0.35, "context", ">= 35% of English liquid cards up > 5% in 30 d (regime nowcast)")
    add("vintage_era", u.era.isin(["wotc", "vintage"]), "context", "card year <= 2013")
    add("price_1k", u.ref >= 1000, "context", "reference price >= $1,000")
    return S


def month_t(u, on, win):
    d = []
    for _, grp in u.groupby("date"):
        o = on.loc[grp.index]
        if o.sum() >= MIN_ON:
            d.append(win.loc[grp.index][o].mean() - win.loc[grp.index].mean())
    d = np.array(d)
    if len(d) < 3 or d.std(ddof=1) == 0:
        return np.nan, len(d), np.nan
    return d.mean() / d.std(ddof=1) * np.sqrt(len(d)), len(d), (d > 0).mean()


def signal_tables(u, S, win, net):
    years = sorted(u.yr.unique())
    pooled, by_year = [], []
    base_all = win.mean()
    for name, on, kind, desc in S:
        t, nm, pos = month_t(u, on, win)
        pooled.append({
            "signal": name, "kind": kind, "coverage": on.mean(), "recall": (on & win).sum() / max(win.sum(), 1),
            "precision": win[on].mean() if on.any() else np.nan, "base": base_all,
            "lift": (win[on].mean() / base_all) if on.any() else np.nan,
            "t_months": t, "months": nm, "months_pos": pos,
            "net_median_on": net[on].median(), "net_mean_on": net[on].mean(), "n_on": int(on.sum()), "desc": desc,
        })
        for y in years:
            yy = u.yr == y
            o = on & yy
            w = win & yy
            by_year.append({
                "signal": name, "year": y, "n_on": int(o.sum()), "coverage": o.sum() / yy.sum(),
                "recall": (o & w).sum() / max(w.sum(), 1), "precision": win[o].mean() if o.any() else np.nan,
                "base": win[yy].mean(), "lift": (win[o].mean() / win[yy].mean()) if o.any() and win[yy].mean() > 0 else np.nan,
                "net_median_on": net[o].median() if o.any() else np.nan,
            })
    return pd.DataFrame(pooled), pd.DataFrame(by_year)


def tier_grid(u, win, net, lead_col, lead_thr):
    """Within-set price tier x whether the set's chase cards ran: pooled and by year, plus the
    month-paired t of (tail win rate - chase win rate) inside sets whose chase cards ran."""
    tier = pd.Series(np.where(u.set_price_q <= 0.25, "tail", np.where(u.set_price_q >= 0.75, "chase", "mid")), index=u.index)
    tier = tier.where(u.set_price_q.notna())
    ran = pd.Series(np.where(u[lead_col] >= lead_thr, "chase ran", np.where(u[lead_col] < 0.10, "chase flat", "between")), index=u.index).where(u[lead_col].notna())
    rows = []
    for (r, t_), idx in u.groupby([ran, tier]).groups.items():
        idx = pd.Index(idx)
        rec = {"chase cards": r, "tier": t_, "n": len(idx), "win": win.loc[idx].mean(), "lift": win.loc[idx].mean() / win.mean(), "net_median": net.loc[idx].median()}
        for y in sorted(u.yr.unique()):
            ii = idx[u.loc[idx, "yr"] == y]
            rec[f"lift_{y}"] = (win.loc[ii].mean() / win[u.yr == y].mean()) if len(ii) >= MIN_ON and win[u.yr == y].mean() > 0 else np.nan
        rows.append(rec)
    grid = pd.DataFrame(rows).sort_values(["chase cards", "tier"])
    d = []
    for _, g in u.groupby("date"):
        rr = ran.loc[g.index] == "chase ran"
        tl = rr & (tier.loc[g.index] == "tail")
        ch = rr & (tier.loc[g.index] == "chase")
        if tl.sum() >= MIN_ON and ch.sum() >= MIN_ON:
            d.append(win.loc[g.index][tl].mean() - win.loc[g.index][ch].mean())
    d = np.array(d)
    t = d.mean() / d.std(ddof=1) * np.sqrt(len(d)) if len(d) >= 3 and d.std(ddof=1) > 0 else np.nan
    return grid, t, len(d), (d > 0).mean() if len(d) else np.nan


def fmt_pct(x):
    return "" if pd.isna(x) else f"{100 * x:.1f}%"


def fmt(x, d=2):
    return "" if pd.isna(x) else f"{x:.{d}f}"


def md_table(df, cols, fmts):
    head = "| " + " | ".join(str(c) for c in cols) + " |\n|" + "|".join("---" for _ in cols) + "|\n"
    body = ""
    for _, r in df.iterrows():
        body += "| " + " | ".join(fmts[c](r[c]) if c in fmts else str(r[c]) for c in cols) + " |\n"
    return head + body


def sales_digest(top, h):
    """Monthly PSA 10 sales path from the store around each anchor, for the hand-read."""
    lo = (top.date.min() - pd.Timedelta(days=200)).to_period("M")
    hi = min((top.date.max() + pd.Timedelta(days=h + 60)).to_period("M"), DATA_END.to_period("M"))
    ids = set(top.asset_id)
    frames = []
    for per in pd.period_range(lo, hi, freq="M"):
        f = ROOT / "history" / "sales" / f"{per}.csv"
        if not f.exists():
            continue
        s = pd.read_csv(f, usecols=["asset_id", "date", "price", "grade", "source", "sale_type", "url", "skipped_reason"], low_memory=False)
        s = s[s.asset_id.isin(ids) & (s.grade == 10.0) & s.skipped_reason.isna()]
        frames.append(s)
    sales = pd.concat(frames)
    sales["date"] = pd.to_datetime(sales.date)
    out = []
    for _, r in top.iterrows():
        s = sales[(sales.asset_id == r.asset_id)].sort_values("date")
        win = s[(s.date >= r.date - pd.Timedelta(days=200)) & (s.date <= r.date + pd.Timedelta(days=h + 60))]
        out.append(f"\n### #{int(r['rank'])} {r.card_name} (anchor {r.date.date()}, ref ${r.ref:,.0f}, gross {100 * r.gross:+.0f}%, net {100 * r.net:+.0f}%, exit {r.exit_flag}, auto-tag: {r.auto_tag})\n")
        m = win.groupby(win.date.dt.to_period("M")).price.agg(["count", "median", "min", "max"])
        out.append("month | n | median | min | max\n---|---|---|---|---\n" + "\n".join(f"{i} | {int(x['count'])} | {x['median']:,.0f} | {x['min']:,.0f} | {x['max']:,.0f}" for i, x in m.iterrows()) + "\n")
        after = win[win.date > r.date].nlargest(3, "price")
        out.append("highest sales after the anchor: " + "; ".join(f"${x.price:,.0f} {x.date.date()} {x.source} {x.sale_type} {x.url}" for _, x in after.iterrows()) + "\n")
    return "".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=180, choices=[90, 180])
    ap.add_argument("--min-price", type=float, default=150)
    ap.add_argument("--vintage", action="store_true", help="year <= 2013 only")
    ap.add_argument("--no-sales", action="store_true", help="skip the sales-path digest")
    ap.add_argument("--panel", default="panel.csv", help="panel file under analysis/out/ (panel_loose.csv = every card with a sale in 180 d)")
    ap.add_argument("--data-end", default=None, help="last sale date the panel was built from (default: DATA_END for panel.csv, else the panel's last anchor + 90 d)")
    ap.add_argument("--tag", default="", help="extra suffix for the output files")
    a = ap.parse_args()
    global DATA_END
    h = a.horizon
    tag = f"_{h}" + ("_vintage" if a.vintage else "") + (f"_p{int(a.min_price)}" if a.min_price != 150 else "") + ("_loose" if a.panel != "panel.csv" else "") + a.tag

    p = pd.read_csv(OUT / a.panel, low_memory=False)
    p["date"] = pd.to_datetime(p.date)
    if a.data_end:
        DATA_END = pd.Timestamp(a.data_end)
    elif a.panel != "panel.csv":
        DATA_END = p.date.max() + pd.Timedelta(days=90)   # panel.py's last anchor = data end - max(HORIZONS)
    print(f"panel {a.panel}: {len(p):,} rows, anchors {p.date.min().date()} .. {p.date.max().date()}, data end taken as {DATA_END.date()}")
    chars = pd.read_csv(ROOT / "latest" / "characters.csv", usecols=["character", "rank_le2013", "rank_all_years"])
    pv = load_pageviews()
    p = add_features(p, chars, pv)

    assets = pd.read_csv(ROOT / "history" / "assets.csv", usecols=["asset_id", "card_name"])
    other = assets.card_name.str.contains(OTHER_FRANCHISES, case=False, na=False, regex=True)
    n_before = len(p)
    p = p[~p.asset_id.isin(set(assets.loc[other, "asset_id"]))]
    print(f"dropped {n_before - len(p):,} panel rows of other franchises ({other.sum():,} assets): " + ", ".join(assets.loc[other, "card_name"].str.split().str[1:3].str.join(" ").value_counts().head(8).index))
    rest = assets[~other & ~assets.card_name.str.contains("pok[eé]mon", case=False, na=False, regex=True)]
    print(f"kept {len(rest):,} assets whose name has neither 'Pokemon' nor a listed franchise, e.g. " + "; ".join(rest.card_name.head(6)))
    cutoff = DATA_END - pd.Timedelta(days=h + 30)
    u = p[(p.lang == "english") & (p.ref >= a.min_price) & (p.date <= cutoff)].copy()
    if a.vintage:
        u = u[u.year <= 2013]
    u["net"] = u[f"mny{h}"]
    u["gross"] = u[f"gross{h}"]
    u["exit_flag"] = u[f"exit_flag{h}"]
    u = u[u.net.notna()].copy()
    u["yr"] = u.date.dt.year
    win = u.net >= WIN_NET
    u["win"] = win
    net = u.net

    S = build_signals(u)
    pooled, by_year = signal_tables(u, S, win, net)
    pooled.to_csv(OUT / f"autopsy_signals{tag}.csv", index=False)
    by_year.to_csv(OUT / f"autopsy_signals_by_year{tag}.csv", index=False)

    # no-precursor view: how many BUY signals were on for each win
    buy = [on for name, on, kind, _ in S if kind == "buy" and name not in ("mom30_down20", "stale30")]
    n_buy_on = sum(on.astype(int) for on in buy)
    u["n_buy_on"] = n_buy_on
    prec = u[win].groupby("yr").n_buy_on.agg(wins="size", none=lambda s: (s == 0).mean(), one=lambda s: (s == 1).mean(), two_plus=lambda s: (s >= 2).mean())
    prec_all = u.groupby("yr").n_buy_on.agg(rows="size", none_all=lambda s: (s == 0).mean(), two_plus_all=lambda s: (s >= 2).mean())
    prec = prec.join(prec_all)

    # top pumps, one per asset, closed 2025+ anchors
    t = u[u.date >= TOP_START].sort_values("gross", ascending=False).drop_duplicates("asset_id").head(TOP_K).copy()
    t = t.merge(assets, on="asset_id", how="left")
    t["rank"] = range(1, len(t) + 1)
    sw = u.groupby(["date", "set"]).win.agg(["sum", "count"])
    cw = u.groupby(["date", "subject"]).win.agg(["sum", "count"])
    t = t.join(sw.rename(columns={"sum": "set_wins", "count": "set_rows"}), on=["date", "set"])
    t = t.join(cw.rename(columns={"sum": "char_wins", "count": "char_rows"}), on=["date", "subject"])
    t["set_win_share"] = ((t.set_wins - 1) / (t.set_rows - 1)).where(t.set_rows >= 4)
    t["char_win_share"] = ((t.char_wins - 1) / (t.char_rows - 1)).where(t.char_rows >= 4)

    def auto_tag(r):
        tags = []
        if r.set_win_share >= 0.5:
            tags.append("set run")
        if r.char_win_share >= 0.5:
            tags.append("character run")
        if r[f"n_exit{h}"] <= 1 or r.exit_flag != "ok":
            tags.append("thin exit")
        if r.mom30 >= 0.5:
            tags.append("already spiking")
        return ", ".join(tags) if tags else "idiosyncratic"

    t["auto_tag"] = t.apply(auto_tag, axis=1)
    liq = u[(u.date >= TOP_START) & (u.exit_flag == "ok") & (u[f"n_exit{h}"] >= 3) & (u.vol90 >= 3)]
    tl = liq.sort_values("gross", ascending=False).drop_duplicates("asset_id").head(30).copy()
    tl = tl.merge(assets, on="asset_id", how="left")
    tl["rank"] = range(1, len(tl) + 1)
    tl = tl.join(sw.rename(columns={"sum": "set_wins", "count": "set_rows"}), on=["date", "set"])
    tl = tl.join(cw.rename(columns={"sum": "char_wins", "count": "char_rows"}), on=["date", "subject"])
    tl["set_win_share"] = ((tl.set_wins - 1) / (tl.set_rows - 1)).where(tl.set_rows >= 4)
    tl["char_win_share"] = ((tl.char_wins - 1) / (tl.char_rows - 1)).where(tl.char_rows >= 4)
    tl["auto_tag"] = tl.apply(auto_tag, axis=1)
    keep = ["rank", "asset_id", "card_name", "subject", "set", "year", "date", "ref", "entry_med", f"exit{h}", "exit_flag", f"n_entry", f"n_exit{h}", "gross", "net",
            "mom30", "mom90", "vol30", "vol90", "vol_surge", "days_since_sale", "band_z", "peer_resid", "char_mom30", "char_jp_mom30", "char_jp_n", "set_mom30", "set_n",
            "pv_surprise", "age_years", "pop_today", "char_rank", "mkt_mom30", "set_win_share", "char_win_share", "n_buy_on", "auto_tag"]
    keep += ["set_mom90", "set_lead_mom90", "set_lead_mom180", "sibling_ran", "era_mom90", "breadth", "set_price_q"]
    t[keep].to_csv(OUT / f"autopsy_top{tag}.csv", index=False)
    tl[keep].to_csv(OUT / f"autopsy_top_liquid{tag}.csv", index=False)

    # ---- report
    years = sorted(u.yr.unique())
    base = u.groupby("yr").agg(rows=("win", "size"), wins=("win", "sum"), base=("win", "mean"), net_median=("net", "median"),
                               illiquid=("exit_flag", lambda s: (s != "ok").mean()))
    L = [f"# Pump autopsy — horizon {h} d, English cards ≥ ${a.min_price:,.0f}{', year ≤ 2013' if a.vintage else ''}\n\n",
         f"Panel `analysis/out/{a.panel}` (sales through {DATA_END.date()}; universe {'every card with a sale in 180 d' if a.panel != 'panel.csv' else '>= 3 sales in 180 d and >= 1 in 60 d'}); anchors ≤ {cutoff.date()} so every {h}-day exit window closed. "
         f"Win = net ≥ +{100 * WIN_NET:.0f}% under the money target. {len(u):,} card-months, {u.asset_id.nunique():,} assets, {int(win.sum()):,} wins.\n\n",
         "## Base rates by anchor year\n\n",
         md_table(base.reset_index(), ["yr", "rows", "wins", "base", "net_median", "illiquid"],
                  {"base": fmt_pct, "net_median": fmt_pct, "illiquid": fmt_pct, "rows": lambda x: f"{int(x):,}", "wins": lambda x: f"{int(x):,}"}),
         "\n## Signals, pooled (sorted by lift within kind)\n\n`t_months` is the month-paired t of precision minus base; `months_pos` the share of those months where the flagged rows beat the base.\n\n"]
    pooled_s = pooled.sort_values(["kind", "lift"], ascending=[True, False])
    L.append(md_table(pooled_s, ["signal", "kind", "coverage", "recall", "precision", "base", "lift", "t_months", "months", "months_pos", "net_median_on", "net_mean_on", "desc"],
                      {"coverage": fmt_pct, "recall": fmt_pct, "precision": fmt_pct, "base": fmt_pct, "lift": lambda x: fmt(x, 2), "t_months": lambda x: fmt(x, 1),
                       "months_pos": fmt_pct, "net_median_on": fmt_pct, "net_mean_on": fmt_pct}))
    L.append("\n## Lift by year (precision / that year's base; blank = fewer than 20 flagged rows)\n\n")
    piv = by_year.pivot(index="signal", columns="year", values="lift")
    n_on = by_year.pivot(index="signal", columns="year", values="n_on")
    piv = piv.where(n_on >= MIN_ON)
    piv = piv.loc[pooled_s.signal]
    L.append(md_table(piv.reset_index(), ["signal"] + list(years),
                      {y: (lambda x: fmt(x, 2)) for y in years}))
    L.append("\n## Recall by year (share of that year's wins with the signal on)\n\n")
    rec = by_year.pivot(index="signal", columns="year", values="recall").loc[pooled_s.signal]
    L.append(md_table(rec.reset_index(), ["signal"] + list(years), {y: fmt_pct for y in years}))
    L.append("\n## Wins with no precursor\n\n`none` = share of that year's wins with none of the buy-kind signals on at the anchor (contrarian and stale excluded); `none_all` the same share over all rows.\n\n")
    L.append(md_table(prec.reset_index(), ["yr", "wins", "none", "one", "two_plus", "rows", "none_all", "two_plus_all"],
                      {"none": fmt_pct, "one": fmt_pct, "two_plus": fmt_pct, "none_all": fmt_pct, "two_plus_all": fmt_pct, "wins": lambda x: f"{int(x):,}", "rows": lambda x: f"{int(x):,}"}))
    L.append("\n## The long tail after the chase cards (within-set price tier x whether the set's top quartile ran)\n\n")
    for lead_col, thr in (("set_lead_mom90", 0.3), ("set_lead_mom90", 0.5), ("set_lead_mom180", 0.5)):
        grid, t_tc, n_tc, pos_tc = tier_grid(u, win, net, lead_col, thr)
        yrs = [c for c in grid.columns if str(c).startswith("lift_")]
        L.append(f"**{lead_col} ≥ {thr:.0%}** (sets with ≥ 8 English cards priced at the anchor; `lift` vs the pooled base, by-year lifts blank under {MIN_ON} rows). "
                 f"Tail minus chase inside sets whose chase cards ran, month-paired: t {fmt(t_tc, 1)} over {n_tc} months, tail ahead in {fmt_pct(pos_tc)}.\n\n")
        L.append(md_table(grid, ["chase cards", "tier", "n", "win", "lift", "net_median"] + yrs,
                          {"n": lambda x: f"{int(x):,}", "win": fmt_pct, "lift": lambda x: fmt(x, 2), "net_median": fmt_pct, **{y: (lambda x: fmt(x, 2)) for y in yrs}}))
        L.append("\n")
    L.append(f"\n## Top {TOP_K} pumps, anchors from {TOP_START.date()} (one row per asset, by gross {h} d move)\n\n`set_win` / `char_win` = share of the other set-mates / character-mates in the universe that also won at the same anchor (outcome-side). `n_buy` = buy-kind signals on at the anchor.\n\n")
    tt = t.copy()
    tt["card"] = tt.card_name.str.replace("Pokemon ", "", regex=False).str.slice(0, 60)
    L.append(md_table(tt, ["rank", "card", "date", "ref", "gross", "net", "exit_flag", "mom30", "vol30", "vol_surge", "char_mom30", "char_jp_mom30", "set_mom30", "set_lead_mom90", "sibling_ran", "days_since_sale", "age_years", "char_rank", "set_win_share", "char_win_share", "n_buy_on", "auto_tag"],
                      {"date": lambda x: str(x.date()), "ref": lambda x: f"${x:,.0f}", "gross": fmt_pct, "net": fmt_pct, "mom30": fmt_pct, "char_mom30": fmt_pct, "char_jp_mom30": fmt_pct, "set_mom30": fmt_pct, "set_lead_mom90": fmt_pct, "sibling_ran": fmt_pct,
                       "set_win_share": fmt_pct, "char_win_share": fmt_pct, "vol_surge": lambda x: fmt(x, 1), "days_since_sale": lambda x: fmt(x, 0), "age_years": lambda x: fmt(x, 0), "char_rank": lambda x: fmt(x, 0), "vol30": lambda x: fmt(x, 0)}))
    tags = t.auto_tag.str.split(", ").explode().value_counts()
    L.append("\nAuto-tag counts: " + ", ".join(f"{k} {v}" for k, v in tags.items()) + "\n")
    L.append(f"\n## Top 30 LIQUID pumps, anchors from {TOP_START.date()} (exit sold on >= 3 sales, >= 3 sales in the 90 d before the anchor)\n\n")
    tt = tl.copy()
    tt["card"] = tt.card_name.str.replace("Pokemon ", "", regex=False).str.slice(0, 60)
    L.append(md_table(tt, ["rank", "card", "date", "ref", "gross", "net", "mom30", "mom90", "vol90", "vol_surge", "char_mom30", "char_jp_mom30", "set_mom90", "set_lead_mom90", "sibling_ran", "era_mom90", "days_since_sale", "age_years", "set_win_share", "char_win_share", "n_buy_on", "auto_tag"],
                      {"date": lambda x: str(x.date()), "ref": lambda x: f"${x:,.0f}", "gross": fmt_pct, "net": fmt_pct, "mom30": fmt_pct, "mom90": fmt_pct, "char_mom30": fmt_pct, "char_jp_mom30": fmt_pct, "set_mom90": fmt_pct, "set_lead_mom90": fmt_pct, "sibling_ran": fmt_pct, "era_mom90": fmt_pct,
                       "set_win_share": fmt_pct, "char_win_share": fmt_pct, "vol_surge": lambda x: fmt(x, 1), "days_since_sale": lambda x: fmt(x, 0), "age_years": lambda x: fmt(x, 0), "vol90": lambda x: fmt(x, 0)}))
    tags = tl.auto_tag.str.split(", ").explode().value_counts()
    L.append("\nAuto-tag counts (liquid): " + ", ".join(f"{k} {v}" for k, v in tags.items()) + "\n")
    (OUT / f"autopsy{tag}.md").write_text("".join(L))
    print("".join(L[:6]))
    print(f"wrote {OUT / f'autopsy{tag}.md'}")

    if not a.no_sales:
        d = sales_digest(t, h)
        (OUT / f"autopsy_top_sales{tag}.md").write_text(f"# Sales paths of the top {TOP_K} pumps (PSA 10, store rows not skipped), horizon {h} d\n" + d)
        print(f"wrote {OUT / f'autopsy_top_sales{tag}.md'}")


if __name__ == "__main__":
    main()
