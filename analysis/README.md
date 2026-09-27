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

## PSA 9 catch-up in hype windows (2026-09-27)

Sid's follow-up: the catch-up happens in hyped categories, when buyers get
priced out of the 10s, and the market-wide average buries it. `psa9_hype.py`
re-cuts the lag study four ways: by card type (Gold Star, LV.X, Neo Shining,
e-card Crystal, HGSS Prime/LEGEND, EX-era ex, WOTC 1st Edition, modern V /
ex / alt art, promos), era, language and character (top 50 by alt market
cap); only in **hype windows**, where the group as a whole moved (the median
of all its cards' PSA 10 month-on-month bucket returns ≥ +30%, ≥ 5 cards,
measured on every card with PSA 10 sales); by PSA 10 price tier (< $1K,
$1K–$10K, ≥ $10K); out to 6 and 12 months. Rows are every PSA 9 universe card
(the lag study's 7,532) at every month start; a hype in month M flags the
anchor T = first day of M+1, and everything after T is outcome. Horizon
windows that end after the data end are blank, so 12m covers signals up to
2025-09.

```bash
analysis/.venv/bin/python analysis/test_psa9_hype.py
analysis/.venv/bin/python analysis/psa9_hype.py --cache-dir <dir>   # ~2 min; out/psa9_hype{,_hype20,_hype15}.md
```

**Sid's +30% bar is almost never met, and mostly this year.** Hype months at
+30%: 13 card-type months (10 in 2026), 3 era months (all 2026), 15
character months across 11 of the top 50 (9 in 2026). Six- and twelve-month
outcomes do not exist yet for most of them. The same tables are therefore
run at +20% (29 / 9 / 69 group-months) and **+15% (55 / 20 / 180
group-months, 34 distinct months, 2021–2026)**, the only threshold with
enough history to answer the 6–12 month question.

**How it is measured, and why the first cut misled.** Counting cards,
pooled hype rows show large, "significant" effects at every threshold, and
the priced-out state (card's PSA 10 up ≥ 30% in the hype month, its PSA 9 <
+10%) shows the PSA 9 / PSA 10 gap closing by 20–30%. Neither survives the
two controls that matter. (1) The gap closes by the same ~25% in that state
*without* hype: it is the bucket-noise reversal from the lag study, a
noise-high PSA 10 bucket falling back (−17% at every horizon). (2) One month
can hold hundreds of cards, and the hype months cluster in the 2025–26
vintage run. The table to trust (section 5 of each report) takes **one
observation per hype month** and measures each card against **unhyped cards
of the same era and PSA 10 price tier anchored the same month**:

| +15% hype, matched excess (mean, t across months [months]) | 1m | 3m | 6m | 12m |
|---|---:|---:|---:|---:|
| PSA 9, any hype | +3.7 (2.4) [33] | +2.0 (1.4) [31] | **+6.8 (3.6) [28]** | **+11.0 (4.8) [24]** |
| PSA 10, same rows | −5.1 (−1.9) | −0.3 (−0.1) | −0.3 (−0.1) | +2.4 (0.9) |
| PSA 9, character hype | +3.3 (1.9) [30] | +2.4 (1.4) [28] | +7.7 (3.8) [25] | +8.5 (4.1) [21] |
| PSA 9, card-type hype | +3.0 (1.9) [20] | +3.7 (1.9) [18] | +1.6 (0.6) [15] | +5.8 (0.8) [14] |
| PSA 9, hype, PSA 10 < $1K | +4.2 (2.0) | +1.9 (1.2) | +6.4 (3.7) [27] | +9.4 (4.1) [23] |
| PSA 9, hype, PSA 10 $1K–$10K | +2.5 (1.3) | +4.3 (1.8) | +4.4 (1.1) [23] | +13.1 (1.9) [20] |
| PSA 9, hype, PSA 10 ≥ $10K | +1.7 (0.5) | +4.8 (1.5) | +13.3 (3.4) [9] | +6.9 (0.8) [7] |
| PSA 10, hype, PSA 10 ≥ $10K | −3.8 | −9.3 | −13.1 (−1.3) | −11.4 (−0.5) |

By year of the hype month, PSA 9 at 6m / 12m: 2021 +13.9 / +12.4, 2022
+7.4 / +10.1, 2023 +4.6 / +12.8, 2024 +10.6 / +9.6, 2025 +3.5 / +9.1 (2026:
too recent). Signals from 2021–2024 only: +8.7% at 6m (t 3.5), +11.5% at
12m (t 4.4), 19 months, 79% of them up. The same direction at +20% (+6.2% /
+8.0%, t 1.6, 22 / 18 months) and +30% (+23% / +12%, t 1.9 / 1.1, 11 / 8
months), with too few months to be sure at either.

**The priced-out state, measured properly.** Matched to unhyped cards in the
*same* state (own PSA 10 up ≥ 30% that month, own PSA 9 < +10%), era and
tier, so the state's own bucket noise cancels: hyped priced-out PSA 9s
+7.1% at 3m (t 2.1, 27 months), **+18.8% at 6m (t 2.8, 25) and +16.1% at 12m
(t 3.0, 22)**, medians +10.2% / +11.4%; their PSA 10s +4.2% / +1.2% (t < 1).
Hyped cards *not* in that state: PSA 9 +2.9% at 6m (t 1.2), +9.5% at 12m
(t 3.8). The state is 4% of hype rows (1,365 of 34,425).

**Trade** (buy the PSA 9 at the first sale in (T, T+21], sell at the first
sale ≥ 180 / 365 days later, 13% fee), +15% hype: median net +0.7% at 180d
(hit 51%) and +3.5% at 365d vs −7.6% and −3.4% for PSA 9s bought outside
hype windows; against same-month unhyped cards of the same era and tier,
+4.8 points at 180d (t 2.1, ahead in 69% of months) and +6.1 at 365d (t
1.9, 59%).

**Verdict.**

- **Sid is right that a PSA 9 catch-up exists and that the market-wide
  average and the 90-day window buried it, but it is slow and modest.** After
  a month in which a character (or card type) rose ≥ 15% as a whole, its
  PSA 9s beat unhyped PSA 9s of the same era and price tier by ~7% over 6
  months and ~11% over 12, while the PSA 10s beside them do not (−0.3%,
  +2.4%). Nothing at 3 months (+2.0%, t 1.4), which is why the lag study did
  not see it. Positive for signals from every year 2021–2025.
- **It is a character effect.** Character hype carries it (t ≈ 4); card-type
  hype does not (t < 1 at 6–12m). No single card type or character has
  enough hype months (all ≤ 8) to be judged on its own; Pikachu (8 months),
  Mew and Giratina lean positive at 6–12m, Gold Star and LV.X are flat, HGSS
  Prime/LEGEND jumps at once rather than catching up (4 months).
- **"Priced out" holds at the card level, not as a price tier.** The
  catch-up concentrates where the card's own PSA 10 jumped in the hype month
  and its PSA 9 had not: +19% at 6m and +16% at 12m over unhyped cards in
  that same state (t ≈ 3; mean well above median, so a few big winners
  carry part of it), against +3% / +10% for the rest of the hype rows. At
  1–3 months the gap closing in that state is bucket noise (it closes as
  much without hype). By tier, the effect is clearest under $1K, where most
  of the data is; at ≥ $10K the PSA 9 rises (+13% at 6m, 9 months) but the
  gap there closes as much by the PSA 10 giving back (−13%), and there are
  too few top-tier months to separate the two.
- **At Sid's +30% there is not enough history yet.** Almost every +30% group
  month is in 2026; re-run in 2027 (`--hype 0.30`).
- **What it could ship as.** Not a buy signal with an expected return: the
  median trade roughly breaks even after the 13% fee in absolute terms, and
  +5 points against the alternative is a relative edge, t ≈ 2. At most a
  description on the PSA 9: "this character ran ≥ 15% last month; PSA 9s of
  hyped characters have outperformed by ~7% over the next 6 months (2021–25,
  79% of such months); the PSA 10s have not", with the stronger variant when
  the card's own PSA 10 jumped and its PSA 9 did not (~+19% / 6 months,
  skewed). Worth a forward test: log these flags nightly and check them in
  six months before anything claims a return.

## Sid's buy rule (2026-09-27)

Sid's reply to the hype study, in his words: he buys PSA 9s of blue chip or
top-50 Pokémon "usually when the card's PSA 10 has gained 100%+ over the past
month and the PSA 9 hasn't moved yet"; the catch-up "mainly applies to the
top-moving categories" (LV.X, Gold Star, Prime/LEGEND moving 70–100%+ as a
category in a month); and the theory "is really about the intersection: hot
category plus top-50 Pokémon (or blue chip), not every card in the set".
`psa9_buyrule.py` runs that rule on psa9_hype.py's rows (same cleaning,
buckets, universe of 7,532 cards, matched control, one-observation-per-month
t-stats, 13%-fee trade). Nothing re-derived.

```bash
analysis/.venv/bin/python analysis/test_psa9_buyrule.py
analysis/.venv/bin/python analysis/psa9_buyrule.py --cache-dir <dir with clean_sales.pkl>   # ~40 s first run (rows cached), ~12 s after -> out/psa9_buyrule.md
analysis/.venv/bin/python analysis/psa9_buyrule.py --top-cap-col alt_market_cap_usd          # psa9_hype's all-years top-50 as the primary list
```

**Definitions used (looked up, not guessed).** *Top-50*: PokeSniper's
Characters tab in its default "≤ 2013" view takes its 50 members from the
alt-side market cap of cards dated ≤ 2013 (`PokeSniper.jsx` rosterRanking /
SEED_TOP50, snapshot 2026-09-22), so the primary list is the top 50 of
`latest/characters.csv` by **`alt_market_cap_le2013_usd`**; today that
reproduces SEED_TOP50 except Eevee in / Arceus out. psa9_hype.py's
TOP_CHARACTERS uses the all-years column `alt_market_cap_usd`; the two lists
share 39 names (≤ 2013 only: Machamp, Kabutops, Typhlosion, Houndoom,
Arcanine, Deoxys, Ampharos, Nidoking, Ninetales, Ditto, Slowking; all-years
only: Latios, Giratina, Mimikyu, Zekrom, Sylveon, Reshiram, Gardevoir,
Arceus, Greninja, Dialga, **Palkia**), and the all-years list is run as a
robustness row. *Blue chip*: `BLUE_CHIP_TOP = 10`, the first 10 of that top
50 by the app's own combined cap = Σ PSA 10 price × PSA 10 pop over its
matched cards (year ≤ 2013, English; `mapToCardShape.js` totalMarketValue).
Replicated as Σ `clean_last_sale_price × pop_at_grade` over English ≤ 2013
cards in `latest/cards.csv`: **Charizard, Mewtwo, Gengar, Pikachu, Umbreon,
Mew, Gyarados, Lugia, Dragonite, Rayquaza** (the alt-side ≤ 2013 cap alone
swaps Dragonite for Torchic). Blue chip ⊂ top-50, so "blue chip OR top-50" is
the top-50. Both lists are today's, applied to the whole history (there is no
roster history): a mild look-ahead in the rule's favour. Membership = the
card's alt.xyz subject names the character (whole word). *The rule*: the
card's own PSA 10 monthly-bucket return in signal month M ≥ +100% (also
+50%), its PSA 9 bucket return in M **< +10%, observed** (a sale in both M
and M−1; "flat or no PSA 9 sale" is counted separately), anchor T = first
day of M+1, entry and outcomes strictly after T. *Controls*: `_mx` =
psa9_hype's matched unhyped card (no +15% card-type / era / character
window) of the same era, PSA 10 tier and month; `_sx` = unhyped
**non-top-50** card in the **same own-card state**, era, tier and month,
which nets out the bucket-noise reversal that follows any "PSA 10 bucket up,
PSA 9 bucket flat" month and isolates what the top-50 filter adds.

**Ask 1: the rule as stated.** Signals ((card, month) pairs; 66 signal
months 2021-02..2026-09, ~7 a month at 100%):

| rule | signals | cards | < $1K | $1K–$10K | ≥ $10K | 2021 / 22 / 23 / 24 / 25 / 26 |
|---|---:|---:|---:|---:|---:|---|
| PSA 10 up ≥ 100%, PSA 9 flat, top-50 | 494 | 409 | 367 | 98 | 14 | 45 / 73 / 95 / 58 / 87 / 136 |
| … blue chip only | 223 | 180 | 152 | 54 | 12 | 21 / 24 / 45 / 27 / 34 / 72 |
| … same state, NOT top-50 (either list) | 349 | 309 | 307 | 31 | 4 | 23 / 53 / 58 / 53 / 68 / 94 |
| … top-50, PSA 9 flat OR no PSA 9 return | 1,663 | 1,182 | 1,356 | 245 | 21 | 130 / 248 / 287 / 285 / 320 / 393 |
| PSA 10 up ≥ 50%, PSA 9 flat, top-50 | 2,050 | 1,332 | 1,603 | 370 | 38 | 137 / 283 / 348 / 280 / 476 / 526 |
| … same state, NOT top-50 | 1,505 | 927 | 1,337 | 127 | 11 | 97 / 229 / 281 / 225 / 294 / 379 |

PSA 9 after the signal, one observation per signal month (mean / median
across months, % of months up, (t), [months]):

| sample | control | 3m | 6m | 12m |
|---|---|---|---|---|
| top-50, up ≥ 100% (494) | `_mx` unhyped, same era/tier | +10.2 / +7.6, 71% (4.8) [63] | +12.5 / +9.9, 70% (4.8) [60] | +14.8 / +10.9, 74% (4.6) [54] |
| same state, NOT top-50 (349) | `_mx` | +7.3 / +6.5, 62% (2.1) [58] | +7.5 / +8.0, 71% (2.5) [56] | +11.3 / +10.2, 67% (3.1) [49] |
| **top-50, up ≥ 100%** | **`_sx` same state, non-top-50** | **+2.3 / +7.3, 58% (0.4) [45]** | **+8.7 / +7.0, 52% (1.8) [42]** | **+2.7 / +11.0, 61% (0.5) [36]** |
| blue chip, up ≥ 100% (223) | `_sx` | −0.4 / +0.3 (−0.1) [29] | +19.0 / +9.8, 64% (1.9) [22] | +0.3 / −5.8 (0.0) [19] |
| top-50, up ≥ 50% (2,050) | `_mx` | +8.7 / +6.5, 83% (7.9) [63] | +10.1 / +11.3, 87% (8.1) [60] | +10.3 / +8.8, 87% (7.3) [54] |
| top-50, up ≥ 50% | `_sx` | +4.1 / −0.3, 50% (1.6) [62] | +4.7 / +4.4, 63% (2.4) [59] | +1.3 / +0.6, 53% (0.5) [53] |
| PSA 10 of the top-50 ≥ 100% rows | `_mx` | −36.6 / −42.0, 5% (−5.6) | −43.3 / −45.7, 7% (−11.8) | −40.8 / −43.7, 6% (−10.9) |
| gap (PSA 9 − own PSA 10), top-50 ≥ 100% | `_sx` | −2.7 / 0.0 (−0.4) | +1.1 / 0.0 (0.3) | −5.4 / 0.0 (−2.0) |

By PSA 10 tier, top-50 up ≥ 100%, `_mx` then `_sx`: **< $1K** (367)
+11.2 / +12.0 / +16.2 (t 4.5 / 4.2 / 4.1) then +1.2 / +8.1 / +1.9 (t 0.2 /
1.5 / 0.3); **$1K–$10K** (98, months 2021: 6, 22: 7, 23: 7, 24: 1, 25: 9,
26: 7) +9.8 / +19.7 / +7.8 (t 2.7 / 3.9 / 1.7) then +19.7 / +14.1 / +10.5
over 8 / 9 / 5 months (t 2.0 / 0.8 / 0.4); **≥ $10K: 14 signals in 10
months (2021: 4, 2023: 2, 2025: 4, 2026: 2)**, `_mx` +3.0 / +7.0 / +7.7
(medians 0.0 / −0.4 / +3.1, t ≤ 1.1), and no same-state control exists
(4 non-top-50 rows in five years). At 50% the ≥ $10K tier has 38 signals,
`_mx` +3.6 / +9.5 / +7.2 (t 0.8 / 1.8 / 1.0).

Trade (buy the first PSA 9 sale in (T, T+21], sell the first sale ≥ 90 /
180 / 365 days after entry, 13% fee, closed windows only):

| sample | hold | trades (/yr) | entry vs pre-T PSA 9 median | hit | mean | median net | vs matched unhyped, pts (t) | months ahead |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| top-50, up ≥ 100% | 90d | 302 (50) | +10.5% | 41% | +5.5% | **−11.6%** | +3.2 (0.9) | 46% |
| top-50, up ≥ 100% | 180d | 255 (43) | +9.8% | 34% | −1.4% | **−16.5%** | +0.7 (0.2) | 45% |
| top-50, up ≥ 100% | 365d | 211 (42) | +9.1% | 34% | +4.9% | −15.9% | +0.7 (0.1) | 40% |
| blue chip, up ≥ 100% | 180d | 116 (19) | +13.2% | 38% | +4.8% | −13.5% | +11.8 (1.7) | 55% |
| top-50, ≥ 100%, PSA 10 ≥ $10K | 90 / 180 / 365d | 10 / 9 / 6 | +4.5% | 50 / 56 / 33% | +20 / +24 / +2% | +3.5 / +1.5 / −16.5% | +25 / +16 / +25 (≤ 1.1) | 75 / 38 / 60% |
| same state, NOT top-50 | 180d | 169 (28) | +6.2% | 48% | +13.8% | −1.1% | +11.2 (1.7) | 56% |
| top-50, up ≥ 50% | 90d | 1,259 (210) | +8.7% | 45% | +7.0% | −5.6% | +2.5 (2.3) | 54% |
| top-50, up ≥ 50% | 180d | 1,110 (185) | +6.7% | 45% | +11.8% | −6.0% | +3.7 (2.2) | 62% |
| top-50, up ≥ 50% | 365d | 897 (179) | +5.4% | 47% | +28.9% | −4.6% | +0.9 (0.4) | 44% |
| universe (every card, every month) | 180d | 170,374 | +0.0% | 44% | +7.8% | −7.3% | — | — |

By year of T, top-50 up ≥ 100%, 180-day hold, median net: 2021 −27.4%
(40 trades, hit 13%), 2022 −28.7% (50), 2023 −23.1% (63), 2024 −18.8%
(37), 2025 **+14.3%** (58, hit 59%), 2026 +29.3% (7). At 365 days: −39.2,
−19.2, −21.3, −4.9, +50.1 (2025, 29 trades). At 50%, 180d: −28.0, −22.9,
−13.1, +8.1, +17.5, +52.7 (25); the median trade minus the same-month
matched unhyped trade by year: +3.5, −0.5, +3.4, +1.4, −0.2, +4.5 pts.

**Ask 2: inside hot card-type months, top-50 vs the rest.** Card-type hype
≥ 15%: 60 group-months over 22 distinct months (2021: 2, 2022: 2, 2023: 4,
2024: 1, 2025: 6, 2026: 6). Matched `_mx`, one observation per month:

| hot card-type rows | rows | 3m | 6m | 12m |
|---|---:|---|---|---|
| top-50 (app) | 7,688 | +6.9 / +4.3, 78% (2.8) [18] | +6.1 / +1.2, 60% (1.4) [15] | +7.8 / +1.6, 62% (1.0) [13] |
| blue chip | 3,426 | +5.3 / +3.2, 75% (2.2) [16] | +7.4 / +1.5, 58% (1.1) [12] | +19.7 / +11.6, 70% (2.0) [10] |
| NOT top-50 (either list) | 5,956 | −1.5 / −1.3, 36% (−0.4) [11] | −8.7 / −11.3, 12% (−1.4) [8] | +1.2 / +1.7, 50% (0.1) [8] |
| top-50 minus NOT top-50, paired by month | | +7.6 / +6.1, 73% (1.5) [11] | +20.5 / +13.2, 88% (1.8) [8] | +6.4 / +13.6, 57% (0.5) [7] |
| **Sid's intersection**: top-50 + own PSA 10 up ≥ 50% + PSA 9 flat | 172 | +30.6 / +18.2, 100% (3.1) [10] | +21.3 / +27.0, 86% (4.5) [7] | +9.4 / +26.2, 80% (0.5) [5] |
| NOT top-50 + same own state | 137 | +2.0 / −9.1, 43% (0.2) [7] | +5.3 / −5.4, 40% (0.3) [5] | −23.0 / −25.2, 25% (−1.4) [4] |
| intersection minus non-top-50 same state, paired | | +27.1 / +29.8, 71% (2.0) [7] | +12.6 / +19.7, 80% (0.8) [5] | +11.9 / +9.7 (0.3) [3] |
| PSA 10 of the intersection rows | 172 | −14.8 / −11.1 (−2.2) [9] | −30.8 / −39.6 (−2.6) [8] | −15.6 / −32.2 (−0.9) [6] |

The intersection's months are 2023: 3, 2025: 5, 2026: 6. Trade in hot
card-type months, 180d: top-50 median net −0.6% (1,346 trades, hit 49%,
+2.5 pts vs matched, t 0.6); non-top-50 −3.6% (301, −8.0 pts, t −1.4);
top-50 with PSA 10 ≥ $10K +39.4% on 16 trades in 5 months. At ≥ 30%: 14
group-months in 8 distinct months, 3 of them with 6-month data (top-50
+20.2%, t 2.2, 3 months): not enough.

**Ask 3: how often does a category move +50 / +70 / +100% in a month?**
Over 700 (card type, month) pairs measured (12 card types, 2021-01 to
2026-09, ≥ 5 cards with a PSA 10 bucket return): **+50% three times, +70%
never, +100% never.** The three: LV.X 2026-03 (+61%, 53 cards), EX-era ex
2026-03 (+50%, 131 cards), e-card Crystal 2026-09 (+57%, 10 cards, partial
month, data through 09-26). The next largest ever: Neo Shining 2026-03 +45%,
e-card Crystal 2026-03 +45%, HGSS Prime/LEGEND 2023-01 +44%, Gold Star
2026-09 +42%, HGSS Prime/LEGEND 2026-01 +41%, LV.X 2026-04 +37%. Outcomes
for the two full-month +50% hits (T = 2026-04-01): LV.X PSA 9 +52% raw at
3 months (46 universe rows), PSA 10 +57%, the whole LV.X group's PSA 10
+70% three months on (43 cards); EX-era ex PSA 9 +41%, PSA 10 +40%, group
+42%. No 6- or 12-month window has closed, and no matched control exists
for either (their whole era was in a hype window), so there is no excess to
report. Sid's "70–100%+ category month" has not happened in this data; his
examples are card-level doublings inside a category that moved 30–60%.

**Sid's three cards today** (data through 2026-09-26; the rule's own input
is the September-vs-August bucket):

| | Legends Awakened Mewtwo LV.X #144 | Great Encounters Darkrai LV.X #104 | Platinum Palkia G LV.X #125 |
|---|---|---|---|
| asset_id | f08c4b31-540f-45af-8d58-142812685d1b | 44f089b2-c866-4362-95e6-f549b4b76322 | 0026cc01-6ac2-4e2e-b7b4-e025f3f5d079 |
| top-50 (app ≤ 2013 list) / all-years / blue chip | yes / yes / **yes** (Mewtwo #2) | yes (#45) / yes / no | **no (#51, the app's buffer)** / yes (#49) / no |
| in the PSA 9 universe (was in the backtest) | no (too few PSA 10 months) | yes | yes |
| PSA 10 sales | $264,000 on 09-20; before it $36,000 (05-09), $15,000 (01-25), $10,101 (2025-07) | $23,000 on 07-30; before it $11,600 (06-28), $10,500, $7,501; none since | $48,000, $72,000, $39,995 on 09-13; $24,000 on 08-30; $10,100 on 04-18 |
| PSA 10 bucket Sep vs Aug (rule input) | undefined (no August sale); last sale +1,660% vs the median of the 3 before it, +633% vs May | undefined (no Aug or Sep sale); last sale +119% vs the 3 before it | **+100%** ($24,000 → $48,000 median) |
| PSA 9 last 30d vs prior 30d (bucket) | $2,300 (4 sales) vs $2,300: **0% (+5%)** | $1,350 (7) vs $1,025: **+32% (+29%)** | $904 (4) vs $625: **+45% (+45%)** |
| recent PSA 9 sales | $2,300 09-16, $9,999 09-12, $2,300 09-01, $1,890 08-30, $2,070 08-23 | $2,000 09-21, $1,636 09-18, $1,200 09-17, $1,200 09-07, $1,350 09-05 | $1,000 09-22, $900 09-18, $875 09-04, $909 09-03, $625 08-15 |
| tier | ≥ $10K (PSA 9 / PSA 10 ≈ 0.9%) | no PSA 10 sale in 30 days (last known ≥ $10K; ratio ≈ 6%) | ≥ $10K (ratio ≈ 2%) |
| meets the rule today (100% / 50%) | no: no PSA 10 bucket move (one sale since May) | no: no PSA 10 bucket move, and the PSA 9 has already moved | no: Palkia is not on the app's top-50, and the PSA 9 has already moved |

**Verdict.**

- **Ask 1: the rule finds PSA 9s that do go up, but most of the rise is not
  the rule's.** After a month in which a top-50 card's PSA 10 doubled and its
  PSA 9 sat still, the PSA 9 beats unhyped cards of the same era and price
  tier by +10 / +13 / +15% at 3 / 6 / 12 months (t ≈ 4.8, 70–74% of 63
  months up, every year 2021–2026). But non-top-50 cards in the same state
  gain +7 / +8 / +11% too, and the PSA 10 gives back ~40% of its jump in
  every cut (t −6 to −12): what the rule mostly detects is a noisy-high PSA
  10 bucket about to revert, with the PSA 9 catching the residual. Measured
  against same-state non-top-50 cards, the top-50 filter itself is worth
  **+2 / +9 / +3 points (t 0.4 / 1.8 / 0.5)** at 100% and +4 / +5 / +1 (t
  1.6 / 2.4 / 0.5) at 50%: a 6-month bump that is not there at 3 or 12. The
  catch-up gap net of the state is zero. **As a trade it has lost money at
  the median after the 13% fee in every year 2021–2024 (−19 to −29% at 180
  days) and made money in 2025–26 like every PSA 9;** hit rate 34–41%, +0.7
  to +3 points over the same-month unhyped alternative (t < 1). One reason
  the trade is so much worse than the window excess: the first PSA 9 you can
  buy after the signal is already ~10% above the signal month's PSA 9 median
  (vs 0% for a random card), so a third of the "not yet moved" is gone at
  entry. **At ≥ $10K, where Sid cares most, there are 14 signals in five
  years, 10 trades and no control group: not enough history to say
  anything**, and the 50% version (38 signals) reads +4 / +10 / +7% with
  t ≤ 1.8.
- **Ask 2: yes, the top-50 filter is what separates the hot-category rows,
  but on 8–11 paired months, mostly 2025–26.** In months when a card type
  moved ≥ 15%, its top-50 cards' PSA 9s beat matched unhyped cards by +7 /
  +6 / +8% while its non-top-50 cards did −2 / −9 / +1%; paired by month the
  gap is +8 / +21 / +6 points (t 1.5 / 1.8 / 0.5). Sid's full intersection
  (hot type + top-50 + the card's own PSA 10 up ≥ 50% with its PSA 9 flat)
  is the best cell in the study, +31 / +21% at 3 / 6 months (t 3.1 / 4.5),
  against +2 / +5% for non-top-50 cards in the same state, and its PSA 10s
  fall −15 / −31%: the intersection is where the 9 does the closing rather
  than the 10. It rests on 172 rows in 10 months (2023: 3, 2025: 5, 2026: 6),
  so it is consistent with his theory but is not yet evidence for a return.
  As a trade in hot months the top-50 rows are flat at 180 days (median
  −0.6%, +2.5 points vs matched) and the non-top-50 rows lose (−3.6%, −8
  points): the filter's practical value is avoiding the rest of the set, not
  a positive edge.
- **Ask 3: a +70% or +100% category month has never happened in this
  data, and +50% happened for the first time in March 2026** (LV.X +61%,
  EX-era ex +50%; e-card Crystal is on +57% for a partial September). Both
  March hits are up another +40–50% in both grades three months later with
  the whole group's PSA 10 up +42–70%, but two episodes, no closed 6-month
  window and no control (the era itself was hyped) is a list, not a result.
  Re-run in 2027 (`--hype`-style thresholds are the `CAT_COUNT` constant).
- **The three cards.** None meets the rule as he stated it today. Mewtwo
  LV.X is the only one whose PSA 9 is flat (0% on 4 sales), but its PSA 10
  has traded once since May, so the "100% in a month" is one $264K sale
  against a $36K sale four months earlier; the rule cannot compute a monthly
  move and the card was not even in the backtest universe. Darkrai's PSA 10
  has not sold since July and its PSA 9 is already +32% month on month;
  Palkia's PSA 10 did double September-on-August ($24K → $48K median of
  three sales) but its PSA 9 is already +45%, and Palkia is #51 on the app's
  own list (it qualifies only under the all-years cap). What he bought is
  "PSA 10 up a lot recently, PSA 9 up 30–45% but the ratio still 1–6%",
  which is the ≥ $10K tier this study cannot judge.
- **What the app should do with it.** Same as the hype study: nothing that
  claims a return. A description on the PSA 9 row is defensible ("PSA 10 up
  ≥ 100% last month, PSA 9 < +10%; top-50 PSA 9s in this state have beaten
  unhyped peers by ~10% over 3–12 months since 2021, but the median trade
  lost money after fees every year until 2025 and the PSA 10 usually gives
  back ~40%"). Log the flag nightly with the tier and the hot-category
  intersection, and re-read this in six months when the 2026 signals have
  outcomes. Full tables in `out/psa9_buyrule.md`.

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
