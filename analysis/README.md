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
analysis/.venv/bin/python analysis/test_costs.py                              # cost model
analysis/.venv/bin/python analysis/backtest.py --target money90 --english --min-price 150   # realizable net return, all-in costs
analysis/.venv/bin/python analysis/backtest.py --target money180 --english --haircut 0.30    # haircut sweep on illiquid marks
```

`panel.py` builds one row per (card, month-start) from 2019 on: features from
sales on or before the date, targets from sales strictly after it. The
tradable target buys at the first sale after the date and sells at the median
of the sales 60–90 (or 150–180) days later, in excess of the universe median
that month. The **money target** (`--target money30|90|180`) is the one
that maps to cash, see below. `backtest.py` scores hand-picked signals (momentum, volume surge,
character spillover) and walk-forward-fitted models (ridge, shallow GBM,
retrained monthly with a 90-day embargo) by monthly Spearman IC, decile
spreads, and top-decile return net of a 13% round-trip fee, with `oracle` and
`shuffled` sanity rows.

## The money target and cost model (2026-09-15)

`costs.py` is the all-in cost model: buyer pays 7.5% sales tax and $5
shipping on entry, plus a 20% buyer's premium on the share of entry sales
that came from an auction house (alt.xyz records those rows at 0.92–0.96x
eBay for the same card-month, i.e. hammer); seller pays eBay's 13.25% final
value fee to $7,500 and 2.35% above, $0.40 per order and $5 shipping. The
50%-off promotion on $1,000+ singles is a flag (`--promo`), off by default.
Break-even move by entry price: 61% under $50, 39% at $50–150, 29% at
$150–500, 25% at $500–2k, 24% above $2k. Unit tests: `test_costs.py`.

`panel.py` stores the components of a realizable trade per card-month and
horizon h in {30, 90, 180} days: entry = median of the sales in (t, t+21]
(you cannot buy at yesterday's price, and a median means one junk row can't
be the entry); exit = 40th percentile of the sales in (t+h, t+h+30],
extended once by 30 days if fewer than two sales, otherwise the trade is
marked at the last known sale and flagged `illiquid` rather than dropped
(dropping it would keep only the cards that found a buyer). `mny{h}` is the
net return under the cost model with a 15% haircut on illiquid marks;
`backtest.py --haircut 0|0.15|0.30` re-derives it. Excess returns are
measured against the median of cards in the same price bin that month, not
the universe: the fixed costs make a $30 card's net return worse than a
$3,000 card's by construction.

The backtest **ranks on the frictionless bin-matched return and reports
profit on the net one**. Ranking on the net return let the walk-forward GBM
reach IC 0.35 with 100% of months positive by predicting the cost curve
from `log_price` and its own haircut from the liquidity features; that is
not a signal, and it fell to 0.08 once ranking used the frictionless
return. The `oracle_net` row is the ceiling with perfect foresight.

**Base rates** (English cards, all-in costs, 15% haircut; the median net
return of buying every eligible card, `--min-price 150`):

| horizon | median net, all years | share of trades net > 0 | share net ≥ +30% | illiquid marks | median net 2025–26 |
|---|---:|---:|---:|---:|---:|
| 30d | −26% | 11% | 3% | 22% | −22% |
| 90d | −24% | 23% | 9% | 24% | −15% |
| 180d | −20% | 33% | 18% | 36% | −5% |

By year the 90-day median net for $150–500 cards runs −41% (2021), −35%,
−33%, −23%, −17% (2025), −14% (2026); at 180 days it was positive only in
2025 (+2% at $150–500, +15% at $500–2k, +22% above $2k). Under $50 the
median trade loses 26–57% at every horizon in every year. These medians are
deliberately conservative (40th-percentile exit; the same rows' median-exit
return is a few points higher) and they are what any card-level signal has
to beat.

**Signals under the money target** (`backtest.py --target money90|money180
--english [--min-price 150]`, top-decile numbers are net):

| signal | 90d, all: IC (t) / top net | 90d, ≥$150: IC (t) / top net | 180d, ≥$150: IC (t) / top net / 2025 / 2026 |
|---|---:|---:|---:|
| char_mom30 | +0.024 (4.6) / −34% | +0.022 (2.1) / −20% | +0.012 (1.2) / −13% |
| vol_surge | −0.027 (−4.5) / −38% | −0.053 (−6.0) / −27% | −0.069 (−9.5) / −22% |
| ridge (walk-forward) | +0.055 (4.3) / −30% | 0.00 (0.1) / −17% | +0.011 (0.9) / −4.5% / +25% / +2% |
| GBM (walk-forward) | +0.077 (5.7) / −28% | +0.030 (1.5) / −17% | +0.014 (0.7) / −5.4% / +23% / +10% |
| universe (buy everything) | — / −32% | — / −20% | — / −13% |
| oracle_net (perfect foresight) | — / +26% | — / +35% | — / +62% |

No signal's top decile makes money at 90 days in any year; the 90% lower
bounds of the top-decile net mean are −18% to −40%. At 180 days above $150
the fitted models' top decile made +23–25% in 2025 and +2–10% in 2026 and
lost 30% a year in 2021–23; their IC above $150 is not distinguishable
from zero. Volume surge stays the most reliable ranking signal and it is
negative. The full tables are in `out/backtest_money*.md`.

## Valuation features (2026-09-19)

Sid's four "undervalued / overvalued" candidates (PR #1, 2026-09-19), built
as point-in-time panel features and run under the money target exactly like
the trending signals. Definitions in `panel.py`:

- `band_z` (Bollinger): log ref(t) vs the mean and std of the card's own
  sales in the trailing year (≥ 5 sales). Signal `band_low = −band_z`.
- `peer_resid` (relative value vs peers): log ref(t) minus a two-way
  fixed-effects fit on that month's liquid universe, one effect per
  (set, finish, language) and one per subject, so "cheap" means cheaper than
  set-mates of the same finish after allowing for the character's tier.
  Signal `peer_cheap = −peer_resid`. NaN when the set/finish group has < 3
  liquid cards.
- `scarcity_gap` (scarcity-to-price): within (era, language), percentile rank
  of −log pop minus percentile rank of price. **Uses today's PSA 10 pop**,
  because pop history only starts 2026-09-11; that is not point-in-time
  (today's pop is an upper bound on pop at t), so it is kept out of the fitted
  models and only reported under `--with-pop`.
- Mean reversion is the momentum test with the sign flipped; nothing new.

Signed so that IC > 0 means "the cheap side outperforms". English cards,
15% haircut on illiquid marks, top-decile figures are net:

| signal | 90d, all: IC (t) / top net / univ | 90d, ≥$150: IC (t) / top net / univ | 180d, ≥$150: IC (t) / top net / univ / LB90 |
|---|---:|---:|---:|
| band_low | +0.017 (2.8) / −34% / −32% | +0.039 (2.8) / −21% / −20% | +0.035 (3.2) / −15% / −12% / −19% |
| peer_cheap | +0.007 (0.9) / −34% / −32% | +0.047 (3.2) / −18% / −20% | **+0.053 (3.9) / −9% / −12% / −14%** |
| scarcity_gap (today's pop) | +0.007 (0.7) / −43% / −32% | — | +0.103 (5.3) / −9% / −12% / −15%, **71% illiquid** |
| char_mom30 (for scale) | +0.026 (4.9) / −33% / −31% | +0.024 (2.3) / −19% / −20% | +0.013 (1.4) / −12% / −12% |
| shuffled | 0.00 / −32% | 0.01 / −21% | 0.00 / −13% |

By year, the only one worth a second look, `peer_cheap` at 180 days ≥ $150
(top-decile mean net / median net / share of illiquid marks):

| year | IC | mean net | median net | illiquid |
|---|---:|---:|---:|---:|
| 2021 | +0.09 | −34% | −40% | 20% |
| 2022 | −0.01 | −33% | −35% | 26% |
| 2023 | +0.03 | −29% | −31% | 31% |
| 2024 | −0.03 | −1% | −9% | 25% |
| 2025 | +0.18 | +41% | +21% | 23% |
| 2026 | +0.09 | +12% | −7% | 70% |

**Reading it.**

- **Below-its-own-band (`band_low`) is a real but tiny effect**: IC positive in
  62–68% of months with t ≈ 3 at every horizon, and its top decile earns the
  universe return, not more. Cards do drift back toward their own year's
  range; the drift is smaller than the costs. Fine as a descriptive label
  ("below its 1-year range"), useless as a buy signal.
- **Cheap-vs-peers (`peer_cheap`) is the vintage-premium trade in disguise.**
  Its top decile is the second-tier WOTC holos (Base Set Machamp and Zapdos,
  Fossil, Skyridge, Team Rocket; median age 22 years, median price ~$340). It
  correlates +0.25 with card age and −0.28 with turnover. It lost 29–34% a
  year in 2021–23, broke even in 2024 and made +41% in 2025 (the year the
  vintage premium ran) with a 2026 top decile that is 70% illiquid marks.
  Same conclusion as the 6-month GBM on 09-15: a regime, not a mispricing
  detector. Median net over all years −14% vs −17.5% for everything else.
- **Scarcity-to-price cannot be tested honestly yet, and what it shows is
  liquidity, not value.** With today's pop it posts the best IC on the page
  (+0.10, t 5.3), but 71% of its top decile never found a buyer inside the
  exit window (marked, then haircut). Low pop and low price together mostly
  means "nobody trades this card". Revisit when the daily pop snapshots
  cover a year, using pop-at-t and with the illiquid share reported next to
  every number.
- **Nothing here changes what ships**: no valuation signal makes money at 90
  days in any year; at 180 days the only positive years are 2025–26 for
  every signal at once, which is the market, not the signal. A
  "fair value" label built from `peer_resid` and `band_z` can be shown as
  what it is, a relative-price description with the 2021–23 base rates next
  to it, never as an expected return.

Runs: `backtest.py --target money90|money180 --english [--min-price 150]
[--with-pop] [--haircut 0.30]`; tables in `out/backtest_money*_en*.md`. Leak
tests for the new features in `test_panel.py` (band_z ignores planted future
sales; peer_resid is cross-sectional and NaN in thin groups).

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
