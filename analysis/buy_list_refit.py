#!/usr/bin/env python3
"""Monthly re-fit of the buy-list rate tables (2026-10-23), the out-of-sample check first.

    analysis/.venv/bin/python analysis/buy_list_refit.py                 # build today's loose panel, check, re-fit
    analysis/.venv/bin/python analysis/buy_list_refit.py --panel panel_loose_2026-11-01.csv   # a panel already built
    analysis/.venv/bin/python analysis/buy_list_refit.py --check-only    # the check alone; the rate tables stay
    analysis/.venv/bin/python analysis/buy_list_refit.py --replay 2025-01 --panel panel_loose_2026-10-23.csv
                                                                         # what monthly re-fits since then would have said

Why. history/buy_list.py scores every card every night with history/buy_list_rates.csv and
buy_list_lifts.csv, which analysis/buy_list_rates.py estimated once from the loose panel. New
sales re-price the cards nightly, but the rates only learn when an anchor month CLOSES: at
horizon h a monthly anchor t closes once its exit window has passed, t <= data end - (h + 30)
days (buy_list_rates.closed, the rule the tables were fitted with). The hot regime's 180-day
cells rested on three closed months on 2026-10-22 (Aug 2025, Oct 2025, Mar 2026); April-July
2026 were hot too and close one a month (April at 180 d and July at 90 d on 10-28 / 10-29,
then May / August late November, and so on). One closed month adds more to those cells than
all the nightly sales in it.

Order, every run:
 1. Panel. analysis/panel.py with the loose switches (--min-sales-180 1 --min-sales-60 0
    --min-total-sales 4 --start 2021-01-01) -> analysis/out/panel_loose_<date>.csv and its
    .meta.json (data_through). Skipped with --panel.
 2. Check, BEFORE anything is re-fitted. Every anchor month that closed since the current
    tables' data end (history/buy_list_rates_meta.json; 2026-10-20 for the first tables, which
    predate the meta file) is scored with the CURRENT rates and lifts by the nightly's own
    function (buy_list.score_cell: same cell lookup, shrinkage, cuts, caps). Two views:
      all     every English trade of the month the tables could score (what the rates claim)
      listed  the trades the nightly would have listed: no avoid flag the panel can see
              (release_year, busy_spike, vol_surge, below_band, dip, chase_flat; pending_sale
              and title_mismatch need sale checks that start 2026-10), p(180) >= 0.15, expected
              net(180) > 0 (what Sid's list claims)
    Per month, view and group (all, then by tier, then by era): trades, scored, mean predicted
    p, realized win rate (net >= +30% after costs), the gap in points, Brier score, the Brier of
    the regime's rate alone (the market-only forecast) and the skill against it, predicted and
    realized mean net. Pooled calibration by p bucket in the report. Out of sample by
    construction: the tables never saw these months' outcomes.
 3. Re-fit on every closed month (buy_list_rates.fit) -> history/buy_list_rates.csv,
    buy_list_lifts.csv, buy_list_rates_meta.json (data end, months per horizon and regime),
    analysis/out/buy_list_rates.md. Not run when no month closed (the tables would only move
    on late sales and label updates; --force does it anyway) or with --check-only.
 4. Log. history/buy_list_refit_log.csv gets the check's rows (keyed by checked_at, horizon,
    anchor month, view, group; a rerun the same day replaces that day's rows);
    analysis/out/buy_list_refit_<date>.md is the readable report, with the old -> new rates of
    the cells behind today's list.
The nightly picks the new tables up on its next run; its ledger rows carry rates_through.

--replay START walks the same procedure forward on one panel: at every month start D from
START to the panel's data end it fits the tables on the months closed by D and checks the
months that close by the next month start. Mild look-ahead, stated in the report: the panel is
built with today's store, so the outlier filter's re-admissions, sales alt.xyz reported late,
REMOVED marks and the extended exit windows of the newest closed months all use data after D.
Writes analysis/out/buy_list_refit_replay.csv and .md; history/ is not touched.

What the check can NOT say: whether a card will win. It says whether the cell rates held on a
month they did not see, which is the only claim v0 makes.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "history"))
sys.path.insert(0, str(ROOT / "analysis"))
import buy_list  # noqa: E402  (the nightly's scorer: load_rates, load_lifts, score_cell)
import buy_list_rates as rates_mod  # noqa: E402

OUT = rates_mod.OUT
HISTORY = ROOT / "history"
LOG_NAME = "buy_list_refit_log.csv"           # next to the tables (history/ unless --tables)
FIRST_DATA_END = rates_mod.DATA_END          # the tables before the meta file existed: sales through 2026-10-20
PANEL_ARGS = ["--min-sales-180", "1", "--min-sales-60", "0", "--min-total-sales", "4", "--start", "2021-01-01"]
HORIZONS = (90, 180)
P_BUCKETS = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0001]
LOG_COLS = ["checked_at", "rates_through", "data_through", "horizon", "anchor_month", "regime", "view", "group", "n", "n_scored",
            "p_mean", "win_rate", "gap_pts", "brier", "brier_ref", "skill", "net_pred", "net_real", "win_rate_all"]
MIN_GROUP = 30                               # a tier / era group is reported from this many scored trades


def newly_closed(dates, h, old_end, new_end):
    """True for anchors that closed at horizon h after old_end and by new_end (buy_list_rates.closed's rule)."""
    lo = old_end - pd.Timedelta(days=h + 30)
    hi = new_end - pd.Timedelta(days=h + 30)
    return (dates > lo) & (dates <= hi)


def avoid_flags(r):
    """the nightly's avoid layer as far as the panel can see it (module doc)."""
    out = []
    y = pd.to_numeric(r.year, errors="coerce")
    if pd.notna(y) and y >= r.date.year - 1:
        out.append("release_year")
    if pd.notna(r.mom30) and r.mom30 >= 0.5 and r.vol30 >= 3:
        out.append("busy_spike")
    if r.vol30 >= 2 * max(r.vol365 / 12.0, 0.25) and r.vol30 >= 2:
        out.append("vol_surge")
    if pd.notna(r.band_z) and r.band_z <= -1:
        out.append("below_band")
    if pd.notna(r.mom30) and r.mom30 <= -0.2:
        out.append("dip")
    if pd.notna(r.set_lead_mom90) and r.set_lead_mom90 < 0.10:
        out.append("chase_flat")
    return out


def score_trades(w, rates, lifts, h):
    """w (prepared trades) + columns p, enet, p_ref (the regime's own rate), listed, scored at horizon h
    with the given tables, by buy_list.score_cell; unscored rows keep NaN."""
    w = w.copy()
    p, enet, p_ref, listed = [], [], [], []
    for r in w.itertuples(index=False):
        er = str(r.era)
        reg, ti, li = str(r.regime), str(r.tier), str(r.liq)
        if er not in ("wotc", "vintage", "modern", "current") or reg == "nan" or ti == "nan":
            p.append(np.nan), enet.append(np.nan), p_ref.append(np.nan), listed.append(False)
            continue
        cuts = [c for c in ("stale30", "jp_heat", "band_high", "thin_history") if bool(getattr(r, c))]
        s = buy_list.score_cell(rates, lifts, h, reg, er, ti, li, cuts)
        s180 = s if h == buy_list.MAIN_H else buy_list.score_cell(rates, lifts, buy_list.MAIN_H, reg, er, ti, li, cuts)
        ref = rates.get((h, "regime", reg, "", "", ""))
        p.append(s["p"] if s else np.nan)
        enet.append(s["enet"] if s else np.nan)
        p_ref.append(ref["win_rate"] if ref and ref["n"] >= buy_list.MIN_CELL_N else np.nan)
        listed.append(bool(s180) and not avoid_flags(r) and s180["p"] >= buy_list.MIN_P_LIST and s180["enet"] > 0)
    w["p"], w["enet"], w["p_ref"], w["listed"] = p, enet, p_ref, listed
    return w


def summarize(g):
    """one log row's numbers from scored trades g (win, net, p, enet, p_ref)."""
    s = g[g.p.notna()]
    row = {"n": len(g), "n_scored": len(s), "win_rate_all": g.win.mean() if len(g) else np.nan}   # win_rate: the scored trades only
    if len(s):
        b = ((s.p - s.win) ** 2).mean()
        r = s[s.p_ref.notna()]
        bref = ((r.p_ref - r.win) ** 2).mean() if len(r) else np.nan
        row.update(p_mean=s.p.mean(), win_rate=s.win.mean(), gap_pts=100 * (s.win.mean() - s.p.mean()), brier=b, brier_ref=bref,
                   skill=1 - ((r.p - r.win) ** 2).mean() / bref if len(r) and bref > 0 else np.nan,
                   net_pred=s.enet.mean(), net_real=s.net.mean())
    return row


def check(u, rates, lifts, old_end, new_end, checked_at):
    """-> (log rows, scored trades) for every anchor month that closed in (old_end, new_end]."""
    rows, scored = [], []
    for h in HORIZONS:
        w = rates_mod.closed(u, h, new_end)
        w = w[newly_closed(w.date, h, old_end, new_end)]
        if w.empty:
            continue
        w = score_trades(w, rates, lifts, h)
        w["horizon"] = h
        scored.append(w)
        for month, gm in w.groupby("date"):
            base = {"checked_at": checked_at, "rates_through": f"{old_end.date()}", "data_through": f"{new_end.date()}", "horizon": h,
                    "anchor_month": f"{month:%Y-%m}", "regime": str(gm.regime.iloc[0])}
            for view, gv in (("all", gm), ("listed", gm[gm.listed])):
                rows.append({**base, "view": view, "group": "all", **summarize(gv)})
                for key in ("tier", "era"):
                    for val, gg in gv.groupby(key, observed=True):
                        if gg.p.notna().sum() >= MIN_GROUP:
                            rows.append({**base, "view": view, "group": f"{key}={val}", **summarize(gg)})
    return rows, (pd.concat(scored, ignore_index=True) if scored else pd.DataFrame())


def calibration(scored):
    """pooled reliability table: predicted p bucket -> trades, mean p, realized, per horizon and view."""
    out = []
    for h, gh in scored.groupby("horizon"):
        for view, gv in (("all", gh), ("listed", gh[gh.listed])):
            s = gv[gv.p.notna()].copy()
            s["bucket"] = pd.cut(s.p, P_BUCKETS, right=False)
            for b, gb in s.groupby("bucket", observed=True):
                out.append({"horizon": h, "view": view, "bucket": f"{b.left:.1f}-{min(b.right, 1):.1f}", "n": len(gb), "p_mean": gb.p.mean(), "win_rate": gb.win.mean()})
    return pd.DataFrame(out)


def fmt_pct(x, d=1):
    return "" if pd.isna(x) else f"{100 * x:.{d}f}%"


def month_table(rows, view):
    t = [r for r in rows if r["view"] == view and r["group"] == "all"]
    lines = ["| horizon | anchor month | regime | trades | scored | predicted | won (scored) | gap | Brier skill vs regime | net predicted | net realized | won (every trade) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(t, key=lambda r: (r["horizon"], r["anchor_month"])):
        lines.append(f"| {r['horizon']} d | {r['anchor_month']} | {r['regime']} | {r['n']:,} | {r['n_scored']:,} | {fmt_pct(r.get('p_mean'))} | {fmt_pct(r.get('win_rate'))} | "
                     f"{'' if pd.isna(r.get('gap_pts', np.nan)) else format(r['gap_pts'], '+.1f')} | {'' if pd.isna(r.get('skill', np.nan)) else format(100 * r['skill'], '+.1f') + '%'} | "
                     f"{fmt_pct(r.get('net_pred'), 0)} | {fmt_pct(r.get('net_real'), 0)} | {fmt_pct(r.get('win_rate_all'))} |")
    return "\n".join(lines) + "\n"


def calib_table(cal):
    if cal.empty:
        return "(nothing scored)\n"
    lines = ["| horizon | view | predicted p | trades | mean predicted | won |", "|---|---|---|---|---|---|"]
    for r in cal.itertuples():
        lines.append(f"| {r.horizon} d | {r.view} | {r.bucket} | {r.n:,} | {fmt_pct(r.p_mean)} | {fmt_pct(r.win_rate)} |")
    return "\n".join(lines) + "\n"


def cell_moves(old_rates, new_rates, buy_list_csv):
    """old -> new win rate and months of the 180-day cells behind today's list (latest/buy_list.csv's cell column)."""
    if not Path(buy_list_csv).exists():
        return "(no latest/buy_list.csv)\n"
    lst = pd.read_csv(buy_list_csv, usecols=["cell", "cell_level"])
    counts = lst.groupby(["cell", "cell_level"]).size().sort_values(ascending=False)
    lines = ["| cell the nightly used (level) | rows today | trades old -> new | months old -> new | rate old -> new (unshrunk) |", "|---|---|---|---|---|"]
    for (cell, level), k in counts.head(15).items():
        reg, er, ti, li = cell.split("|")
        er, li = (er if "era" in level else ""), (li if "liq" in level else "")
        ti = ti if "tier" in level else ""
        key = (buy_list.MAIN_H, level, reg, er, ti, li)
        cell = "|".join(x for x in (reg, er, ti, li) if x) + f" ({level})"
        o, n = old_rates.get(key), new_rates.get(key)
        if not o and not n:
            continue
        f = lambda x, k2, d=0: "-" if not x or x.get(k2) is None else (f"{x[k2]:.{d}f}" if k2 != "win_rate" else fmt_pct(x[k2]))
        lines.append(f"| {cell} | {k:,} | {f(o, 'n')} -> {f(n, 'n')} | {f(o, 'n_months')} -> {f(n, 'n_months')} | {f(o, 'win_rate')} -> {f(n, 'win_rate')} |")
    return "\n".join(lines) + "\n"


def load_tables(folder):
    return buy_list.load_rates(Path(folder) / "buy_list_rates.csv"), buy_list.load_lifts(Path(folder) / "buy_list_lifts.csv")


def write_log(path, rows, checked_at):
    """history/buy_list_refit_log.csv: this run's rows replace any earlier rows of the same checked_at day."""
    new = pd.DataFrame(rows, columns=LOG_COLS)
    if path.exists():
        old = pd.read_csv(path, dtype=str)
        old = old[old.checked_at.str[:10] != checked_at[:10]]
        new = pd.concat([old, new.astype(object)], ignore_index=True)
    new.to_csv(path, index=False, float_format="%.4f")
    return len(new)


def build_panel(today):
    name = f"panel_loose_{today}.csv"
    cmd = [sys.executable, str(ROOT / "analysis" / "panel.py"), *PANEL_ARGS, "--out", name]
    print("panel:", " ".join(cmd[1:]), flush=True)
    subprocess.run(cmd, check=True)
    return name


def current_data_end(tables):
    """the data end of the tables in `tables` (their meta file), FIRST_DATA_END for tables older than the meta file."""
    meta = Path(tables) / buy_list.RATES_META
    if meta.exists():
        return pd.Timestamp(json.load(open(meta))["data_end"])
    return FIRST_DATA_END


def run_monthly(a):
    today = datetime.now(timezone.utc).date().isoformat()
    checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    panel_name = a.panel or build_panel(today)
    panel_path = OUT / panel_name
    new_end = pd.Timestamp(a.data_end) if a.data_end else rates_mod.panel_data_end(panel_path)
    if new_end is None:
        sys.exit(f"{panel_path}: no .meta.json next to it; pass --data-end")
    tables = Path(a.tables)
    old_end = current_data_end(tables)
    print(f"current tables: sales through {old_end.date()}; panel {panel_name}: sales through {new_end.date()}", flush=True)
    u = rates_mod.prepare(pd.read_csv(panel_path, low_memory=False), pd.read_csv(HISTORY / "assets.csv", usecols=["asset_id", "card_name"]))
    old_rates, old_lifts = load_tables(tables)
    rows, scored = check(u, old_rates, old_lifts, old_end, new_end, checked_at)
    months = sorted({(r["horizon"], r["anchor_month"]) for r in rows})
    md = [f"# Buy-list re-fit {today}\n\nCurrent tables: sales through {old_end.date()}. Panel `{panel_name}`: sales through {new_end.date()}.\n\n",
          "## 1. Check: the newly closed months, scored with the current tables (out of sample)\n\n"]
    if not months:
        md.append("No anchor month closed since the current tables (a month closes at horizon h once anchor <= data end - (h + 30) days).\n\n")
    else:
        md.append("Every trade:\n\n" + month_table(rows, "all") + "\nWhat the nightly would have listed (no visible avoid flag, p(180) >= 0.15, expected net > 0):\n\n"
                  + month_table(rows, "listed") + "\nCalibration, pooled over the checked months:\n\n" + calib_table(calibration(scored)) + "\n")
        print("".join(md[-1:]))
    refit = not a.check_only and (months or a.force)
    if refit:
        cells, lifts, rates_md, meta = rates_mod.fit(u, new_end)
        rates_mod.write_tables(cells, lifts, tables)
        meta.update(panel=panel_name, fitted_at=checked_at)
        (tables / buy_list.RATES_META).write_text(json.dumps(meta, indent=1) + "\n")
        (OUT / ("buy_list_rates.md" if tables == HISTORY else f"buy_list_rates_{today}_scratch.md")).write_text(rates_md)
        new_rates, _ = load_tables(tables)
        md.append(f"## 2. Re-fit: tables now through {new_end.date()}\n\n"
                  + "".join(f"- {h} d: {v['trades']:,} trades over {v['months']} months (closed through {v['closed_through']}); months by regime {v['months_by_regime']}\n" for h, v in meta["horizons"].items())
                  + "\nThe 180-day cells behind today's list (latest/buy_list.csv), old -> new:\n\n" + cell_moves(old_rates, new_rates, ROOT / "latest" / "buy_list.csv") + "\n")
    else:
        md.append("## 2. Re-fit: not run (" + ("--check-only" if a.check_only else "no month closed; --force re-fits anyway") + ")\n\n")
    if rows:
        print(f"refit log: {write_log(tables / LOG_NAME, rows, checked_at)} rows in {tables / LOG_NAME}")
    report = OUT / f"buy_list_refit_{today}.md"
    report.write_text("".join(md))
    print(f"checked {len(months)} closed month(s) at {len(rows)} log row(s); re-fit {'done' if refit else 'not run'}; report {report}")
    # one line for the commit message (nightly/run_buy_list_refit.sh)
    checked = "; ".join(f"{r['horizon']} d {r['anchor_month']} {r['regime']} predicted {fmt_pct(r.get('p_mean'))} won {fmt_pct(r.get('win_rate_all'))}"
                        for r in sorted(rows, key=lambda r: (r["horizon"], r["anchor_month"])) if r["view"] == "all" and r["group"] == "all")
    if refit:
        hz = meta["horizons"]
        print(f"refit summary: tables through {new_end.date()} (hot months 90 d {hz['90']['months_by_regime'].get('hot', 0)}, "
              f"180 d {hz['180']['months_by_regime'].get('hot', 0)}); checked {checked or 'nothing'}")
    else:
        print(f"refit summary: tables stay through {old_end.date()}; " + (f"checked {checked}" if checked else "no month closed"))


def run_replay(a):
    panel_path = OUT / a.panel
    end = pd.Timestamp(a.data_end) if a.data_end else rates_mod.panel_data_end(panel_path)
    if end is None:
        sys.exit(f"{panel_path}: no .meta.json next to it; pass --data-end")
    u = rates_mod.prepare(pd.read_csv(panel_path, low_memory=False), pd.read_csv(HISTORY / "assets.csv", usecols=["asset_id", "card_name"]))
    cutoffs = list(pd.date_range(pd.Timestamp(a.replay + "-01"), end, freq="MS"))
    rows, scored = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for i, d in enumerate(cutoffs):
            nxt = cutoffs[i + 1] if i + 1 < len(cutoffs) else end
            if nxt <= d:
                continue
            cells, lifts, _, _ = rates_mod.fit(u, d)
            rates_mod.write_tables(cells, lifts, tmp)          # through the CSVs, so the nightly's parser reads them
            rates, lifts_d = load_tables(tmp)
            r, s = check(u, rates, lifts_d, d, nxt, f"replay {d.date()}")
            rows += r
            if not s.empty:
                scored.append(s)
            done = [x for x in r if x["view"] == "all" and x["group"] == "all"]
            print(f"tables through {d.date()} -> months closing by {nxt.date()}: " + "; ".join(
                f"{x['horizon']} d {x['anchor_month']} {x['regime']} predicted {fmt_pct(x.get('p_mean'))} won {fmt_pct(x.get('win_rate'))}" for x in done), flush=True)
    pd.DataFrame(rows, columns=LOG_COLS).to_csv(OUT / "buy_list_refit_replay.csv", index=False, float_format="%.4f")
    scored = pd.concat(scored, ignore_index=True) if scored else pd.DataFrame()
    md = [f"# Buy-list re-fit replay: monthly re-fits from {a.replay} to {end.date()}\n\n",
          f"One panel (`{a.panel}`, sales through {end.date()}); at each month start D the tables are fitted on the months closed by D and "
          "score the months that close by the next month start. Look-ahead, mild: the panel uses today's store (outlier re-admissions, late-reported "
          "sales, REMOVED marks, the newest months' extended exit windows).\n\n",
          "## Every trade\n\n", month_table(rows, "all"), "\n## What the nightly would have listed\n\n", month_table(rows, "listed"),
          "\n## Calibration, pooled\n\n", calib_table(calibration(scored)) if not scored.empty else "(nothing scored)\n"]
    summ = []
    for h in HORIZONS:
        for view in ("all", "listed"):
            t = pd.DataFrame([x for x in rows if x["horizon"] == h and x["view"] == view and x["group"] == "all" and x.get("n_scored", 0) >= MIN_GROUP])
            if t.empty:
                continue
            summ.append(f"- {h} d, {view}: {len(t)} months checked; mean |gap| {t.gap_pts.abs().mean():.1f} pts, mean gap {t.gap_pts.mean():+.1f} "
                        f"(won minus predicted), {int((t.gap_pts < -10).sum())} months more than 10 pts below; mean Brier skill vs the regime alone "
                        f"{100 * t.skill.mean():+.1f}%\n")
    md.insert(2, "## Summary\n\n" + "".join(summ) + "\n")
    (OUT / "buy_list_refit_replay.md").write_text("".join(md))
    print("".join(summ))
    print(f"wrote {OUT / 'buy_list_refit_replay.md'} and .csv ({len(rows)} rows)")


def main():
    ap = argparse.ArgumentParser(description="monthly re-fit of the buy-list rate tables, the check first")
    ap.add_argument("--panel", default=None, help="a loose panel under analysis/out/ (default: build panel_loose_<today>.csv)")
    ap.add_argument("--data-end", default=None, help="the panel's data end (default: its .meta.json)")
    ap.add_argument("--check-only", action="store_true", help="check the newly closed months; leave the tables")
    ap.add_argument("--force", action="store_true", help="re-fit even when no month closed")
    ap.add_argument("--tables", default=str(HISTORY), help="folder of the current tables, their meta and the log, read and written (default history/)")
    ap.add_argument("--replay", default=None, metavar="YYYY-MM", help="walk monthly re-fits forward from this month on --panel")
    a = ap.parse_args()
    if a.replay:
        if not a.panel:
            sys.exit("--replay needs --panel")
        run_replay(a)
    else:
        run_monthly(a)


if __name__ == "__main__":
    main()
