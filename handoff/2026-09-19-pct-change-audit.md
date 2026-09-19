# Audit of the 30 / 90 / 365-day % change (2026-09-19)

Sid asked (PR #1, 2026-09-19) for an independent end-to-end check of the
percent-change numbers behind the dashboard. This is that check: the feed
side rebuilt from scratch and diffed cell by cell, then the server and app
code that carries the number to the screen, read line by line.

## 1. The feed: every cell reproduces

`analysis/audit_pct_change.py` re-implements the README's definitions from
the raw sale history (`history/sales/*.csv`, 2.76M PSA 10 sales) without
importing `history/metrics.py`, and diffs its result against
`latest/cards.csv` (the 2026-09-18 data, 52,649 rows). It rebuilds the
outlier filter, the reference price, the three change columns, the three
volume columns, the clean last sale, the outlier count and the unconfirmed
flag.

| column | cells checked | mismatches |
|---|---:|---:|
| price_chg_30d_pct | 52,649 | 0 |
| price_chg_90d_pct | 52,649 | 0 |
| price_chg_1y_pct | 52,649 | 0 |
| volume_30d / 90d / 1y | 52,649 each | 0 |
| median_last_3 | 52,649 | 0 |
| clean_last_sale_price / date | 52,649 | 0 |
| outliers_excluded | 52,649 | 0 |
| last_sale_unconfirmed | 52,649 | 0 |

Run it any time: `analysis/.venv/bin/python analysis/audit_pct_change.py`
(11 s, exits non-zero on any mismatch).

What the number is, exactly:

- `ref(t)` = median of the up-to-3 newest **clean** PSA 10 sales dated in
  `(t − 180 d, t]`. Clean = not flagged by alt.xyz (RELISTED / NOT_PAID /
  PENDING) and not held back by the outlier filter.
- `price_chg_Nd_pct` = `ref(today) / ref(today − N) − 1`, only when both
  exist **and** at least one clean sale fell inside the window. Otherwise
  blank. `today` is the date of the newest daily snapshot.
- `volume_Nd` = clean sales in `(today − N, today]`.

## 2. What the number is not, and where the app treats it as something else

None of these are arithmetic errors. They are places where the app combines
this figure with a differently-defined one, or fills a blank. Sid's call on
each; the fix is a line or two in every case.

**2a. Two price bases are now live.** Since 2026-09-18 the displayed
`psa10Price` is the literal newest unflagged sale
(`mapToCardShape.js:149`), while the % change is median-of-3 clean sales
vs the same median N days earlier. On 2026-09-18 data, for the 25,755 cards
with a reference price, the literal newest sale differs from the median by
more than 20% on 6,039 of them (23%) and by more than 50% on 1,898. So
"price × (1 + change)" does not recover any earlier price.
`correctForListing` (`PokeSniper.jsx:2021`) does exactly that division to
get `priceAMonthAgo`; it should divide `median_last_3` (on
`card.samay.medianLast3`), not `psa10Price`.

**2b. A missing change is scored as zero.** `valuationOf`
(`PokeSniper.jsx:2031`) uses `c.psa10PriceChangePct ?? 0`. On 2026-09-18
data 61% of priced cards have no 30-day figure (no clean sale in the last
30 days), 45% have no 90-day figure and 46% no 1-year figure. Every one of
those is currently scored as "flat" in the Undervalued / Overvalued label.
Under the never-guess rule they should get no label, or the label should
say it rests on scarcity alone.

**2c. 1d and 1w are the 30-day figure divided down.** `cardWindowChange`
and `cardWindowPriceChange` (`PokeSniper.jsx:1689`, `:2012`) return
`30d change × 1/30` and `× 7/30`. The UI marks them "≈", which is honest,
but a ranking on 1d or 1w is a ranking on 30d. There is no sub-30-day
measurement in the feed and, with one clean sale a month being the norm,
there cannot be a meaningful one for most cards.

**2d. The 30-day basis will change on 2026-10-11, for some cards only.**
`mkt_cap_chg_30d_pct` needs a daily pop snapshot at least 30 days old. The
first daily file is 2026-09-11 (the full-index run), so from 2026-10-11
cards present in that file get a price × pop figure while cards first seen
later stay on price alone. `mapToCardShape.js:170` picks
`mktCapChg30dPct ?? priceChg30dPct`, so the "1m" column will mix the two
bases inside one ranking until every card has 30 days of history.
`card.samay.changeBasis` says which one a card is on; a rollup should
either group by it or pin everything to price change until the pop history
is uniform.

**2e. Thin cards move on one sale.** Of the 13,172 cards with a 30-day
figure, 5,030 (38%) have exactly one clean sale in the window, and on 7,279
(55%) the two medians share at least one sale. That is by design (a median
of three is what stops one junk row from being a +2,000% badge), but the
consequence is that a card's 30-day change is often "one new sale against
its last two", so a single odd sale moves the number by up to a third of
its size. The `volume_30d` column next to it says how much stands behind
it; showing it beside the % is the honest fix.

**2f. Same-day ties.** Sales on one day are ordered by price, so when a
card's newest day has more than three sales the "last 3" are its three
highest that day. It affects 1,454 of the 13,172 cards with a 30-day figure
and moves the median by a small amount only on high-volume modern cards.
Noted for completeness; not worth changing.

## 3. What did not need fixing

- The server passes every column through unchanged (`samayData.js:420`
  `toNum`, blank → `undefined`); nothing is recomputed on that side.
- 2m and 6m are log-interpolated between measured points, which is the
  right interpolation for compounding changes, and they refuse when either
  neighbour is blank.
- The rollups weight by market value and count only cards that have a
  figure for the window, and they say `n / total`.
- The unconfirmed flag, the clean price, and the change columns all come
  from the same clean-sale list, so a held-back sale can never appear in
  one and not the other.
