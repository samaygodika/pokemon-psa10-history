# Prompt for a fresh session: trending-signal research for PokeSniper

> **Revised 2026-09-15 (later the same day):** read `analysis/REVIEW_2026-09-15.md`
> first. Findings 4–6 below were re-measured and largely do not hold; the
> sale-visibility lag is 0–1 day; the fee model is incomplete; and the event
> calendar (task 4) is now the priority. The "First tasks" list is superseded
> by the review's section 3.


Paste everything below this line as the first message.

---

I'm Samay. I own the data side of PokeSniper, a Pokémon-card screener my friend Sid builds (React + Express, github.com/NovaCast/PokeSniper, working branch `samay-all-characters`). The goal of this session is research, not app code: find a trending / early-warning signal for PSA 10 Pokémon card prices that survives an honest backtest, because real money will be traded on it. Read this whole brief, then start with the "First tasks" section. Do not modify anything under `history/`, `latest/`, `nightly/` or `.github/` without asking; those are the production feed.

## Where things are

Repo: `/Users/samaygodika/Documents/Code/scrape_` = github.com/samaygodika/pokemon-psa10-history (public). Python 3.14. The research venv is `analysis/.venv` (pandas, numpy, scikit-learn, scipy, tabulate; `analysis/requirements.txt`). The scraper and feed are stdlib-only and must stay that way.

- `history/sales/<YYYY-MM>.csv` — every PSA 10 sale alt.xyz has shown us, deduped by sale URL: ~2.7M rows back to 2017, columns asset_id, date, price, source (eBay / PWCC / Goldin / Alt / Heritage…), sale_type (AUCTION / BUY_IT_NOW / BEST_OFFER), skipped_reason (alt.xyz's own RELISTED / NOT_PAID flags; exclude these).
- `history/daily/<date>.csv` — one row per card per scrape day: PSA 10 pop, all-grade pop, index counts, last sale. Starts 2026-09-11. Nightly top-60-character scrape at 07:00 UTC via GitHub Actions, weekly whole-index run on Sundays. Pop history is therefore only days old; it is NOT point-in-time for any past date.
- `history/assets.csv` — card identity: card_name, year, set, card_number, subject (the Pokémon), variety.
- `latest/cards.csv` — what Sid's server reads (45k cards): clean last sale, 30/90/365-day price change, volumes, plus `latest/series/<xx>.csv` weekly sale series. Built by `history/metrics.py`, whose `drop_outliers` is the canonical junk-sale filter (sequential running-median, 1/4x–4x band, 2-year reference window, 8-run regime reset). Use the same filter in research so results match the app.
- `analysis/panel.py` — point-in-time panel: one row per (card, month-start) from 2019; features from sales ≤ t, targets from sales > t. Leak tests in `analysis/test_panel.py` (run them after any change). Rebuild takes ~1 min: `analysis/.venv/bin/python analysis/panel.py` → `analysis/out/panel.csv` (gitignored).
- `analysis/backtest.py` — walk-forward evaluation: monthly Spearman IC, decile spreads, top-decile return net of a 13% round-trip fee, per-year tables, `oracle` and `shuffled` sanity rows. `--target exec` (buy at first sale after t, sell at median of sales 60–90d out, excess of universe median) is the tradable target; `exec180` for 150–180d; `--min-price`.
- `analysis/README.md` — findings so far. Read it first.

## What has been established (don't re-derive; build on it)

1. Price/volume momentum has no predictive power at 2–3 months (IC ≈ 0). A first pass showed IC −0.25 "mean reversion"; that was measurement noise from using the same noisy reference price in both the signal and the return, and vanished with the tradable target. Any target that shares a sale with a feature is invalid.
2. Volume surge is slightly negative. Character spillover (mean momentum of the character's other cards) is weakly positive, IC +0.02, t ≈ 2.5.
3. A walk-forward GBM has IC +0.08 at 6 months, driven by card age (vintage premium 2024–26), character breadth, and low turnover. Relative strength, not a pump detector; regime-dependent.
4. Doubling within 60–90 days is common (6.8% of tradable card-months since mid-2025) but is a market regime: 12–16% of the universe doubled in Jan–Mar 2026 vs ~2% in quiet months. Month-to-month autocorrelation of the doubling rate is +0.71; market breadth (share of cards up >20% over the prior 30d) predicts next month's doubling rate at +0.50.
5. Doubles cluster by character and month (Feb 2026: 49 Pikachu cards; Jan: 13 Charizard; Feb: 12 Rayquaza Ex, 11 Mew), matching the headliners of the 30th Celebration and Mega Evolution announcements. Before the fact, doublers' own price/volume look like everyone else's.
6. **Character catch-up** is the first signal that is both real and tradable: in a (month, character) where ≥30% of the character's liquid cards are up >20%, the laggards (own 30d move <5%) earned mean +13.4% / median +3.9% excess over 60–90d (8.1% doubled) vs +8.4% / +0.8% (4.5%) for quiet characters; +27.9% / +11.2% at 150–180d; positive every year 2022–2026.

## Ground rules

- Point-in-time or it doesn't count. Features only from information visible at t; targets only from after t; embargo between train and test; report by year; keep the oracle/shuffled rows.
- Tradable target, net of fees. Measure "buy at the next actual sale, sell at actual later sales", never reference-price-to-reference-price.
- Never guess a number; never filter cards out by population (low-pop PSA 10s are the point). Blank means unknown.
- The alt.xyz sale-visibility lag (how many days after a sale it appears) is unmeasured. Distrust anything that only works at horizons under a month. The daily snapshots can measure this lag now: diff consecutive `history/daily` files' `num_sales` against sale dates.
- Prefer few, interpretable features. Sid's UI shows a score; it must be explainable in one line.

## First tasks

1. Reproduce finding 6 from `analysis/out/panel.csv` and harden it: sensitivity to the 30% / 20% / 5% thresholds, minimum character size, price tiers, source mix (eBay-only vs auction houses), and whether it holds when the market regime is quiet. Report by year with the fee applied.
2. Turn it into a daily score spec: character heat (breadth of the character's liquid cards up >20% over 30d) × laggard status × liquidity, computed from `latest/cards.csv` alone, so the feed can publish it nightly (`history/metrics.py` would gain a `trend_score` column and a per-character heat table). Define exactly what "Hot / Rising / Flat / Cooling" should mean in terms of measured base rates.
3. Build a market regime gauge (breadth, doubling rate, median 30d move) and test how much lead it gives.
4. Design the announcement event study: a calendar of official set/product announcements 2021–2026 with announcement date, release date, and featured Pokémon (Bulbapedia set pages are a good source), then test whether featured characters' vintage cards outperform in the 30/60/90 days after the announcement and how that timing compares with when breadth would have flagged them. This is the spec's Rule 12 event pipeline; the Jan–Mar 2026 cluster says it may be the real early signal.
5. Only after 1–4: propose what should ship, with the base rates it was measured at, and what it must never claim.

Write findings into `analysis/README.md` as you go, commit research code under `analysis/`, and leave `analysis/out/` uncommitted.
