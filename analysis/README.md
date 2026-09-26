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

## PSA 9 lag (2026-09-26)

Sid's proposed "PSA 9 lag" buy signal: when a card's PSA 10 price jumps, buy
the PSA 9 because it follows about a month later. `psa9_lag.py` is the
backtest that had to say whether the effect exists before anything ships,
in both directions (PSA 10 leading PSA 9, PSA 9 leading PSA 10). Data: the
3.12M PSA 10 and 1.52M PSA 9 clean sales (PSA 9 backfilled 2026-09-22/23 for
the ~33k nightly-scope cards), skipped rows out, PWCC mirror copies and
outliers removed per (card, grade) with `history/metrics.py`'s own rules,
and the 1,556 (card, date, price) rows alt.xyz lists under *both* grades
dropped from both so the two series never share a sale. Universe: cards with
≥ 12 months having a sale in each grade since 2021: **7,524 cards (4,653
vintage ≤ 2013, 2,871 modern)**, 185k card-months with a median in both
grades, 121k with a return in both. Monthly bucket = log of the median clean
price; returns are consecutive-month differences, clipped at ±log 3 (3,225
of 392k touched; the outlier filter admits confirmed runs, so a few
mislabeled runs survive). All standard errors are two-way clustered by card
and month.

```bash
analysis/.venv/bin/python analysis/test_psa9_lag.py   # planted-future-sale and estimator checks
analysis/.venv/bin/python analysis/psa9_lag.py        # ~40 s to clean and cache the sales, ~70 s per run after -> analysis/out/psa9_lag.md
analysis/.venv/bin/python analysis/psa9_lag.py --min-months 18 --min-bucket-sales 2 --english   # stricter liquidity / app universe
```

**Lead–lag on monthly returns, month fixed effects** (corr(r9ₜ, r10ₜ₋ₖ);
k > 0 = PSA 10 leads, k < 0 = PSA 9 leads; t in brackets):

| era | k = −3 | −2 | −1 | 0 | +1 | +2 | +3 |
|---|---:|---:|---:|---:|---:|---:|---:|
| all (6.7k cards) | 0.002 (0.3) | 0.011 (1.9) | 0.022 (4.0) | **0.074 (10.7)** | 0.034 (4.4) | 0.021 (4.2) | 0.009 (1.8) |
| vintage | 0.003 (0.5) | 0.004 (0.6) | 0.004 (0.7) | 0.044 (5.5) | 0.014 (1.7) | 0.012 (1.8) | 0.007 (1.4) |
| modern | 0.000 (0.0) | 0.016 (2.1) | 0.029 (4.6) | 0.090 (12.9) | 0.040 (4.9) | 0.023 (3.9) | 0.009 (1.2) |

Weekly buckets on the 1,238 busiest cards (≥ 100 weeks with a sale in each
grade), lags −8..+8 weeks: 0.026 (t 6.1) at lag 0, every other lag within
±0.012. Panel regression with month fixed effects and own lags, all cards:
r9ₜ on r10ₜ₋₁ / ₋₂ / ₋₃ = 0.154 (t 11.6) / 0.149 (12.6) / 0.078 (8.9);
the reverse, r10ₜ on r9ₜ₋₁ / ₋₂ / ₋₃ = 0.081 (9.0) / 0.072 (5.6) / 0.034
(2.9). Own-lag coefficients are −0.55 and −0.24 in both grades: a bucket
median is noisy and reverts by half the next month, which is why any
"PSA 9 hasn't moved yet" cut must be read against a control with the same
conditioning (below). Note also that a calendar-month bucket is dated by the
card's own sales, so a jump late in month t lands in that month's PSA 10
bucket and in the *next* month's PSA 9 bucket whenever the PSA 9 happened to
sell earlier: the ±1-month correlations are partly this artefact, which is
why they are nearly symmetric (0.034 vs 0.022) and why the event study
below, whose PSA 9 entry is strictly after detection, is the real test.

**Event study.** A PSA 10 move = the reference price (median of the ≤ 3 most
recent clean PSA 10 sales, sales dated ≤ d only) up ≥ 30% (or ≥ 50%) on its
level 30 days earlier, with ≥ 3 PSA 10 sales in the last 30 days and the
earlier reference at most four months old; one event per card per 90 days.
Windows are 30-day medians of log price: baseline [d−90, d−30), `move`
[d−30, d], then (d, d+30], (d+30, d+60], (d+60, d+90]. "Excess" = minus the
median of every universe card anchored at every month start (the
cross-section that month). 27,624 events at ≥ 30% on 5,775 cards; the top
10 cards are 0.7% of them; 75–89% of events have a PSA 9 sale in every window.

| sample | n | PSA 10 in `move` | PSA 9 in `move` | PSA 9 after detection, excess: +30d / +60d / +90d (t) | median +60d | month-2, month-3 increments | PSA 9/PSA 10 ratio vs baseline: `move` → +90d | PSA 10 after, excess +90d |
|---|---:|---:|---:|---|---:|---|---|---:|
| PSA 10 up ≥ 30% | 27,624 | +19.7% | +4.0% | +1.1 (2.3) / +1.6 (2.4) / +1.4 (1.9) | +0.6% | +0.4 (1.0), +0.5 (1.3) | −13.4% → −12.7% (medians −9.9 → −10.3) | −2.0% (−2.2) |
| vintage | 10,215 | +30.1% | +5.9% | +2.3 (2.5) / +3.9 (2.7) / +4.5 (2.9) | +2.2% | +1.6 (2.0), +1.5 (2.6) | −22.5% → −18.5% | −1.5% (−0.5) |
| modern | 17,409 | +14.0% | +2.9% | +0.4 (0.9) / +0.3 (0.5) / −0.3 (−0.3) | −0.0% | −0.2 (−0.5), −0.1 (−0.2) | −8.4% → −10.0% | −2.2% (−2.6) |
| PSA 10 up ≥ 50% | 16,689 | +26.7% | +6.2% | +2.3 (3.4) / +3.0 (3.3) / +2.6 (2.5) | +2.3% | +0.7 (1.3), +0.0 (0.1) | −17.8% → −15.6% | −2.7% (−2.5) |
| flat control (same cards, PSA 10 within ±10%) | 12,999 | −0.9% | +0.3% | −1.7 (−3.6) / −3.1 (−4.5) / −3.4 (−4.1) | −3.2% | −1.7, −0.1 | +1.0% → +3.3% | −6.5% (−9.0) |
| universe at month starts | 496,584 | +3.2% | +1.7% | +0.1 / +0.2 / +0.2 | 0.0% | +0.2, +0.2 | −1.8% → −5.2% | +0.9% |

By year, ≥ 30% events, excess PSA 9 drift over the 60 days after detection
(mean / median): 2021 −2.4 / −2.0, 2022 −0.1 / −0.1, 2023 +0.5 / 0.0,
2024 +2.0 / +1.6, 2025 +2.4 / +1.8, 2026 +2.8 / +0.5. Vintage's 90-day
excess by year: −2.6, +2.3, +3.1, −0.5, +5.0, **+10.7 (2026)**; modern's:
−3.6, +0.7, +0.3, +2.9, +0.5, −4.7.

**The "PSA 10 up, PSA 9 not yet" state.** At detection the PSA 9's own
30-day change was below +10% in 68% of events (median 0.0%); on monthly
buckets the state "PSA 10 up ≥ 30% and PSA 9 up < 10%" is 7.1% of all
card-months (53% of PSA-10-up months). It is common, and most of what
follows it is noise reversal, not catch-up:

| state this month (monthly buckets) | card-months | next-month PSA 9 excess, mean / median | next-month PSA 10 excess |
|---|---:|---:|---:|
| PSA 10 up ≥ 30% and PSA 9 up < 10% | 8,547 | +6.7% / +5.4% | −9.1% |
| PSA 10 up < 30% and PSA 9 up < 10% (a plain low PSA 9 bucket) | 70,665 | +3.8% / +1.8% | +0.6% |
| PSA 10 up ≥ 30% and PSA 9 up ≥ 10% (both moved) | 7,709 | −4.2% / −2.3% | −6.1% |
| PSA 10 up ≥ 30%, any PSA 9 | 16,256 | +1.5% / +1.9% | −7.7% |

In the event study the same cut ("PSA 9 not yet up 10%") shows +5.7% excess
drift at 60 days measured from the `move` window, but −5.7% *during* the move
window and only +1.4% excess measured from the pre-move baseline; flat-PSA-10
dates with the same PSA 9 condition show +0.8%. Roughly +3 points of the +6.7
is attributable to the PSA 10 move, the rest is the low bucket bouncing.

**Trade** (buy the PSA 9 at the first PSA 9 sale in (d, d+21] — 72% of
events had one, median 4 days after detection — sell at the first PSA 9 sale
≥ h days after entry within a further 60 days, net of a 13% sell-side fee;
only signals whose exit window closed before 2026-09-25 count):

| sample | hold | trades | no exit | hit | mean net | median net | trades / yr |
|---|---:|---:|---:|---:|---:|---:|---:|
| PSA 10 up ≥ 30% | 60d | 17,097 | 4.5% | 44.3% | +4.3% | **−5.9%** | 2,850 |
| PSA 10 up ≥ 30% | 90d | 16,245 | 4.5% | 45.3% | +7.0% | −4.9% | 2,708 |
| PSA 10 up ≥ 50% | 60d | 10,318 | 4.6% | 45.3% | +6.0% | −4.8% | 1,720 |
| ≥ 30%, PSA 9 entry ≥ $100 (16% of events) | 60d | 3,207 | 4.3% | 46.3% | +3.5% | −2.9% | 535 |
| ≥ 30%, PSA 9 entry ≥ $100 | 90d | 2,877 | 4.2% | 50.2% | +9.9% | +0.2% | 480 |
| flat control (same cards) | 60d | 6,766 | 5.8% | 34.0% | −5.5% | −13.3% | 1,128 |
| universe (every card, every month start) | 60d | 187,572 | 11.3% | 38.9% | −0.2% | −10.5% | 31,262 |
| universe, PSA 9 entry ≥ $100 | 60d | 56,260 | 8.0% | 33.6% | −6.5% | −12.4% | 9,377 |

By year at 60 days, event trade vs universe (mean / median net): 2021
−16.8 / −23.2 vs −15.1 / −23.1; 2022 −5.2 / −15.7 vs −7.8 / −16.7; 2023
−3.8 / −13.2 vs −6.3 / −15.2; 2024 +3.2 / −6.6 vs +0.3 / −9.8; 2025
+6.3 / −2.6 vs +5.7 / −4.3; 2026 +21.0 / +8.7 vs +20.2 / +6.9. Matched to the
same month, the median event trade beat the median PSA 9 bought that month by
+1.1 points (−1.0 to +2.5 by year) and 51% of event trades beat their month's
median. The ≥ $100 rows look better only because those events are 2025–26
heavy: against the ≥ $100 universe in the same month the medians by year are
−2.5, −1.3, −3.5, +2.6, +3.0, −2.3. The median PSA 9 entry is **$36**
(quartiles $20–$83), where the cost model's break-even move is 61%.

**The PSA 9 / PSA 10 ratio itself** (median across the universe): 0.37
(2021), 0.35, 0.37, 0.38 (2024), 0.30 (2025), **0.21 (2026)**; vintage 0.35 →
0.17, modern 0.40 → 0.28; per card, the median 2022 → 2026 change in the log
ratio is −57%. The 2025–26 run was a PSA 10 run, and twenty months on the
PSA 9s have not followed it.

**Verdict.**

- **PSA 9 does not follow PSA 10 with a lag of about a month; it moves in the
  same month, by less, and then mostly stays put.** The correlation peaks at
  lag 0 (0.074) and the one-month value (0.034) is small, half artefact, and
  nearly matched by the reverse direction (0.022). After a ≥ 30% PSA 10
  move the PSA 9 has already made ~4% of the PSA 10's ~20% window move by
  detection, adds +1.1% excess in the next 30 days, and then nothing (+0.4%
  and +0.5% a month, t ≈ 1). Two-thirds of the relative move is still there
  90 days later (ratio −10% at the median), and what closes is half the PSA
  10 giving back 2%.
- **Both directions exist and neither is tradable.** PSA 10 → PSA 9 is about
  1.5–2× the reverse (regression 0.154 vs 0.081); both are same-month or
  next-bucket co-movement, not a lead you can buy after.
- **How big, how reliable.** +1.6% excess at 60 days (median +0.6%, t 2.4),
  negative in 2021–22, positive in 2023–26. Modern: zero. Vintage: +4.5%
  over 90 days (t 2.9), but that is −2.6 in 2021, ~+2.5 in 2022–23, −0.5 in
  2024, +5 in 2025 and +10.7 in 2026: the vintage premium again, not a lag.
- **Not tradable after fees.** Median net −5.9% at 60 days and negative in
  every year until 2026, when every PSA 9 made money; the mean (+4.3%) is
  skew from a few multi-baggers; ~1 point better than buying any PSA 9 that
  month, a coin flip against it.
- **What the app should do with PSA 9 prices.** Do not ship a "PSA 9 lag" or
  "PSA 9 catch-up" buy signal or any expected return built on it.
  `psa9_to_psa10_ratio` and its change against the card's own history can be
  shown as a *description* ("the PSA 9 has not repriced with the PSA 10"),
  with the base rate next to it: the gap usually persists. The PSA 9 is a
  usable *confirmation* of a PSA 10 move: when the PSA 9 also moved ≥ 10%,
  the PSA 10 held its gain (+0.3 to +1.2% excess over 30–90 days); when it
  did not, the PSA 10 gave back 2–3% (t ≈ −3). And do not chase the PSA 10
  spike itself: −1.7 to −2.0% excess over the following 30–90 days, −7.7%
  the next bucket-month after a ≥ 30% month (mostly bucket-noise reversal).
  Full tables in `out/psa9_lag.md`.

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
