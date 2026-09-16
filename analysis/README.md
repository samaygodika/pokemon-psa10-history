# analysis — trending model research

Backtests for PokeSniper's trending score (spec Rule 8, steps 1–5), built on
the sale history in `history/`. Nothing here runs in production yet.

```bash
python3 -m venv analysis/.venv && analysis/.venv/bin/pip install -r analysis/requirements.txt
analysis/.venv/bin/python analysis/test_panel.py          # leak tests (planted future sales must not move features)
analysis/.venv/bin/python analysis/panel.py               # point-in-time panel -> analysis/out/panel.csv (~1 min)
analysis/.venv/bin/python analysis/backtest.py --target exec       # 60–90 day tradable target -> analysis/out/backtest_exec.md
analysis/.venv/bin/python analysis/backtest.py --target exec180    # 150–180 day
analysis/.venv/bin/python analysis/backtest.py --target exec --min-price 250
```

`panel.py` builds one row per (card, month-start) from 2019 on: features from
sales on or before the date, targets from sales strictly after it. The
tradable target buys at the first sale after the date and sells at the median
of the sales 60–90 (or 150–180) days later, in excess of the universe median
that month. `backtest.py` scores hand-picked signals (momentum, volume surge,
character spillover) and walk-forward-fitted models (ridge, shallow GBM,
retrained monthly with a 90-day embargo) by monthly Spearman IC, decile
spreads, and top-decile return net of a 13% round-trip fee, with `oracle` and
`shuffled` sanity rows.

## Findings (2026-09-15, seed history: newest 200 sales per card)

- **Price/volume momentum predicts nothing at 2–3 months.** mom30/mom90 IC ≈ 0,
  t < 1. The spec's interim momentum-based formula has no measurable edge.
  An earlier version that measured returns off the same noisy reference price
  the features use showed IC −0.2 to −0.3 "mean reversion"; that was
  measurement noise, not a signal, and disappeared with the tradable target.
- **Volume surge is slightly negative** (IC −0.02, t ≈ −3): a burst of sales
  is more often the end of a move than the start.
- **Character spillover is weakly positive** (char_mom30 IC +0.02, t ≈ 2.3–2.8,
  right sign in ~60–67% of months): a character's other cards rising says a
  little about this card's next months. Supports the app's thesis, but small.
- **At 6 months a fitted model finds a modest cross-sectional edge**: GBM IC
  +0.076 (t = 4.1, positive in 80% of months, every year 2022–26), top decile
  beats bottom decile by ~17 points of excess return. Absolute returns still
  track the market (top-decile net was negative in 2022–23 and positive in
  2024–26), so this is relative strength, not a pump detector.
- Restricting to cards ≥ $250 removes what little 60-day signal there was.
- **What the 6-month model actually leans on** (permutation importance on a
  2025-07 → 2026-06 holdout, IC 0.09): `age_years` first by a wide margin
  (univariate IC +0.26: older cards outperformed), then `bin_share90`
  (−0.04), `char_n` (+0.17: characters with many actively traded cards),
  `vol30` (−0.11: quiet cards beat busy ones). So the edge is mostly a
  *vintage premium* that ran through 2024–26 plus "popular character, low
  turnover", not a momentum pattern. That is the app's own thesis showing up
  in the data, but it is one factor's trend and can reverse; it should be
  shown as relative strength with that caveat, not as a pump signal.

What's missing that could matter more than any of the above: PSA 10
population change (daily snapshots start 2026-09-11; usable for backtests
after a few months) and event/news flags (not collected historically). The
alt.xyz sale-visibility lag is also unmeasured; the daily snapshots will
measure it.

## Movers study (2026-09-15) — superseded

The character catch-up and regime-lead claims first written here were
re-examined the same day and do not hold up; see `REVIEW_2026-09-15.md`
for the corrected numbers, the event traces, and the research plan. In
short: hot-character laggards do about as well as hot-character leaders and
quiet-character leaders (month-clustered t < 1 at 60–90 days); the breadth
gauge's apparent lead is the 2021→2026 trend and turns negative once yearly
means are removed; the sale-visibility lag on alt.xyz is 0–1 day, not weeks;
and the moves that doubled cards in Jan–Mar 2026 trace to dated news
(Illustrator auction, Storm Emeralda leaks, 30th Celebration) that led the
breadth signal by one to ten weeks.

Refreshed on the rebuilt panel (top-60 histories no longer capped at 200
sales): character spillover `char_mom30` IC +0.030 (t 4.6, 73% of months)
at 60–90 days and +0.032 (t 4.9) at 150–180; volume surge IC −0.047 (t −7.0)
and −0.070 (t −9.1); GBM IC +0.048 / +0.102. Scripts: `catchup_study.py`
(2×2 with stale split, sensitivity grid), `char_weekly.py` (weekly character
index and event traces), `data_checks.py` (truncation, lag, regime series),
`second_opinion_checks.py` (noise, venue conventions, new-set decay, robust
doubling base rate with an all-in cost model, Wikipedia pageviews, Japanese
lead). Wikipedia pageview JSON is cached under `analysis/out/pageviews/`.
