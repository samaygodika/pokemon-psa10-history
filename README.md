# pokemon-psa10-history

Daily PSA 10 price and population history for Pokemon cards, scraped from alt.xyz.

Pulls PSA population counts and graded sale prices for Pokemon cards from
[alt.xyz](https://alt.xyz), keeps a day-by-day history, and publishes one folder
(`latest/`) that [PokeSniper](https://github.com/NovaCast/PokeSniper)'s server reads
as its only PSA 10 price/population source. Python 3.8+ standard library only.

```
alt_scraper.py        the scraper (one run = cards.csv + sales.csv + listings.csv)
nightly/              scheduled run: index listing -> scope -> scrape -> ingest -> latest/
history/              the store: assets.csv, daily/<date>.csv, sales/<month>.csv, live_listings.csv  (committed)
latest/               what the server reads: cards.csv, series/<xx>.csv, recent_sales/<xx>.csv, clean_sales/<month>.csv, characters.csv, tcgplayer_ids.csv, card_catalog.csv, tcgplayer_sets.csv, summary.json  (committed)
snapshots/            raw per-run output (gitignored, hundreds of MB)
.github/workflows/    GitHub Actions cron: nightly roster candidates + vintage checklist, weekly full index
```

## The data PokeSniper gets

`latest/cards.csv` is the scraper's `cards.csv` (one row per alt.xyz asset, newest
numbers per asset) plus history-derived columns. Blank always means "the data can't
say", never 0:

| column | meaning |
|---|---|
| `pop_at_grade`, `company_total_pop` | PSA 10 pop and all-PSA-grades pop from the card's population table. **Blank when alt.xyz has no PSA rows for the record** (CGC/BGS only); `0` when PSA rows exist and the 10 count is zero (since 2026-09-18 such cards are in the feed, with no sales, instead of being skipped). |
| `index_total_pop`, `index_transaction_count` | alt.xyz search-index counts: graded copies across every company and grade, and recorded transactions. Present even when the pop columns are blank. |
| `last_sale_price/date/source` | alt.xyz's literal newest PSA 10 sale. |
| `clean_last_sale_price/date/source`, `outliers_excluded`, `last_sale_unconfirmed` | the newest sale after holding back junk rows: a sale below 1/4x or above 6x the running median of the card's last 12 accepted sales (past year) is held back unless confirmed — two consecutive high sales confirm a jump, five consecutive low sales a drop, and repeat sales of one listing URL count once towards those (2026-09-28: one $15 eBay Buy It Now that sold ten times had reset Dragon Frontiers Gold Star Charizard's reference from $58k to $15, a +2,619,900% 90-day change). A card with fewer than 4 sales that year is measured against its last 12 sales of any age with a 1/10x–10x band instead (2026-09-21; before that such cards had no filter, which let a PSA 5 sold at $31 and a $122,000 Goldin lot for a signed Steve Jobs document into PSA 10 histories). A card with only 1 or 2 earlier sales is measured against those with the same 1/10x–10x band, and two distinct listings confirm a move either way (2026-09-28; before that it had no filter, and single lots such as a $78,000 PWCC sale of a $490 BW87 Leafeon promo or a $3.72M XY Mudkip became clean prices). After a confirmed move only sales from that move onward form the reference, and a sale within 2x of the last accepted one is never held (see `drop_outliers` in `history/metrics.py`); 0.18% of all sales. Before any of that, a PWCC Weekly Auctions lot that alt.xyz records under both fanaticscollect.com and pwccmarketplace.com (same card, date and price) counts once, here and in `recent_sales/` (2026-09-22; the copy used to confirm its own jump, e.g. one $204,000 Legendary Collection Articuno lot read as +1,260%). The volume columns and every price level are built from these clean sales; the change columns also count the held-back sales on the high side (see `price_chg_30d_pct`). The app shows the literal `last_sale_price` and marks it unconfirmed when `last_sale_unconfirmed` = 1 (the newest sale is one the filter is holding back). |
| `price_chg_30d_pct`, `_90d_`, `_1y_` | median of the last 3 sales vs the same median as of 30/90/365 days earlier; blank unless both exist and at least one sale happened in the window. The sales are the clean ones **plus the ones the outlier filter still holds back on the HIGH side** (2026-10-01). Over the past year a held-back high newest sale was later shown real 81% of the time (90% on cards up to 2013; eBay 80%, major auction houses 86%) and a held-back low one 17% (4%), so highs count here and lows stay out. Without them the cards that had just moved dropped out of every category's median % (452 cards with a held-back sale in the last 30 days had a blank 30-day change). One held sale moves a card that trades often only part of the way, since the price is a median of 3; on a dormant card it counts in full. Prices and market caps (`median_last_3`, `cap_price*`, `clean_*`, `series/`, `clean_sales/`, `characters.csv`) still wait for a confirming sale. |
| `volume_30d`, `_90d`, `_1y` | clean PSA 10 sales in the window. |
| `pop_30d_ago`, `pop_chg_30d`, `mkt_cap_chg_30d_pct` | need 30 days of daily snapshots; blank until the series is that old (first daily file: 2026-09-11). |
| `median_last_3`, `sales_first_date`, `sales_total`, `history_days` | how much history stands behind the row. |
| `pop_at_grade_9` | PSA 9 pop, same blank-vs-0 rule as `pop_at_grade`. Every run. |
| `psa9_scraped_date` | newest nightly that pulled this card's PSA 9 sales (nightly scope only, from 2026-09-23). **Blank = PSA 9 sales not collected**, and then every `psa9_*` sale column below is blank too. |
| `psa9_last_sale_price/date/source`, `psa9_clean_last_sale_price`, `psa9_last_sale_unconfirmed`, `psa9_median_last_3`, `psa9_volume_30d`, `psa9_price_chg_30d_pct`, `psa9_sales_total` | the PSA 10 definitions above applied to PSA 9 sales (same mirror rule and outlier filter). `psa9_sales_total` = 0 means collected and none sold. |
| `psa9_to_psa10_ratio` | `psa9_median_last_3` / `median_last_3`. |
| `listings_checked_at` | UTC time alt.xyz was last asked what is for sale right now at PSA 10 (from 2026-09-25: the nightly scope daily, everything else on the weekly full run). **Blank = never checked**, and then every live column below is blank too (unknown, not "nothing listed"). Whether a card has anything listed tracks its PSA 10 pop closely (pop 1000+: ~99% do; pop 26–100: ~60%; pop 1–2: ~10%; pop 0: ~1%), so a low-pop card with a check time and no listing is a real "nothing listed". (For one day, 2026-09-26, 14,828 of these were blanked by a mistaken "alt.xyz outage" rule in the scraper — the nightly scope is sorted by pop, so the falling listing rate through a run is the cards, not the endpoint. Reverted the same day.) |
| `live_auction_count`, `next_auction_end`, `next_auction_bid`, `next_auction_bid_count`, `next_auction_source`, `next_auction_url`, `last_auction_end` | the running PSA 10 auctions at that check, as alt.xyz mirrors them: eBay (~75%), Fanatics Collect including its weekly lots (the six-figure vintage auctions), CardHobby, Pristine Auction, a few Goldin; no Heritage. `next_*` = the one ending soonest; end times are UTC. **A snapshot, not live:** the app compares the end times with its own clock — while `last_auction_end` is in the future at least one auction may still be running. The bid is the high bid at the check, or the opening price while the bid count is 0; it is not a price for the card (bids jump in the final minutes). A listing can also be pulled early: a link may lead to eBay's "similar items" page. |
| `lowest_bin_price`, `lowest_bin_source`, `lowest_bin_url` | the cheapest PSA 10 Buy It Now listing at that check (PokeSniper's `lowestListingPrice`). It can sell or be pulled between checks, and alt.xyz itself keeps ended BINs in its feed for months and re-serves them on every check (one returned as live on 2026-09-25 had ended on June 16; the listing record has no date or status field to tell), so treat the link as "was listed at", not "is listed at". Ended *auctions* alt.xyz does drop within hours. A BIN alt.xyz has not shown again for 7 days is dropped (`history/ingest.py`). |
| `psa9_listings_checked_at` | the PSA 9 counterpart of `listings_checked_at`: UTC time of the last answer from alt.xyz about what is for sale at PSA 9 (set whenever that request succeeded, even with nothing listed; blank when it failed). **On from the 2026-09-26 nightly** (`NIGHTLY_ALSO_LISTINGS`, default 1; `--also-listings` in the scraper); blank in feeds built before that. Nightly scope only, like `psa9_scraped_date`; cards with zero PSA 9 copies are not asked and stay blank; blank = never checked at PSA 9, so every `psa9_live_*` / `psa9_lowest_bin_*` column below is blank too. |
| `psa9_live_auction_count`, `psa9_next_auction_end`, `psa9_next_auction_bid`, `psa9_next_auction_bid_count`, `psa9_next_auction_source`, `psa9_next_auction_url`, `psa9_last_auction_end`, `psa9_lowest_bin_price`, `psa9_lowest_bin_source`, `psa9_lowest_bin_url` | the ten live columns above for PSA 9 listings: same meanings and caveats (a snapshot at `psa9_listings_checked_at`, not live; ended auctions pruned, a BIN dropped after 7 days unseen). On from the 2026-09-26 nightly. A count of 0 = checked, nothing running. |
| `price_chg_60d_pct`, `price_chg_180d_pct`, `volume_60d`, `volume_180d` | the `price_chg_*` and `volume_*` rules for 60 and 180 days, so a 2-month or 6-month window is measured instead of interpolated (2026-09-28). |
| `cap_price` | what the card counts at in a market cap: `median_last_3`, else the newest clean sale of any age. Never an unconfirmed sale (2026-09-28). |
| `chg_held_windows` | the windows (`30d 60d 90d 180d 1y`, space-separated) whose `price_chg_*` figure rests on a held-back high sale, i.e. differs from the clean-only one; blank for most cards. For an "≈" next to that %. `mkt_cap_chg_30d_pct` follows the 30d one. The last column of the file (2026-10-01). |
| `cap_price_30d_ago`, `cap_price_60d_ago`, `cap_price_90d_ago`, `cap_price_180d_ago`, `cap_price_1y_ago` | `cap_price` as of that many days ago; blank = no clean sale yet by then. A group's market cap change over a window is Σ(`cap_price` × pop) / Σ(`cap_price_N_ago` × pop) − 1 over its cards that have both: a card that didn't sell counts as unchanged, and no card's % is weighted by its own jump. Pop is today's for every window until the daily files are that old. |

`latest/series/<first two hex of asset_id>.csv` holds the weekly PSA 10 sale
series per asset (`week_start, n_sales, median_price, low, high`) for charts;
the server's `GET /api/samay-data/series?ids=…` reads one shard per lookup.
`latest/recent_sales/<xx>.csv` holds each card's last 10 PSA 10 sales, newest
first, including sales alt.xyz flags (`status` = ok / RELISTED / NOT_PAID /
PENDING …) and sales the filter is holding back (`outlier` = 1), with the sale
URL — `GET /api/samay-data/recent-sales?ids=…` for the app's price dropdown.
`latest/recent_sales_psa9/<xx>.csv` is the same for PSA 9 sales (same columns).

`latest/clean_sales/<YYYY-MM>.csv` (2026-09-29) holds every clean PSA 10 and PSA 9
sale of that month — the sales every column above is built from, after the mirror
rule and the outlier filter — one line per card and grade: `asset_id,grade,sales`,
where `sales` is space-separated `day-of-month:price`, ascending (e.g.
`ab12…,9,3:410 3:415 28:399.5`). About 70 MB for all months, against ~950 MB for
the store's `history/sales/`. `latest/clean_sales/manifest.json` lists each month
with the `sha256`, `bytes` and number of `sales` of its file. It has no timestamp,
so it only changes when a month does: a reader keeps its copies, fetches only
the months whose `sha256` changed, and checks each download against it (raw GitHub
caches for ~5 minutes, so a file can briefly lag the manifest). PokeSniper's
`lib/salesHistory.js` (the Arbitrage pill's period-matched PSA 9 baseline) reads
these instead of `history/sales/`. Most months still change on most nights
(2026-09-29: 68 of 92; ~90 cards a night enter or leave the feed with their whole
history), so a reader re-fetches most of the files each time: ~32 MB gzipped
instead of ~300 MB. PSA 9 sales
exist only for the nightly scope (from 2026-09-23). alt.xyz's not-yet-final sales
(`subject_to_change`, a few thousand, all in the current month) are included, as
everywhere else in the feed.

`latest/characters.csv` (from `history/coverage.py`) has one row per name in
`nightly/subjects.txt` and `nightly/vintage_species.txt` (469 species): English
rows in the feed, how many have a PSA 10 pop / a real zero / an unknown pop / a
price, the alt-side market cap (Σ `pop_at_grade` × `cap_price` over every row,
all years and ≤ 2013), and for the ≤ 2013 rows the market cap change over
30d / 60d / 90d / 180d / 1y (`mkt_cap_chg_<w>_pct_le2013`, the Σ/Σ rule above,
with `mkt_cap_chg_<w>_coverage_le2013` = the share of the cap it covers),
computed with no matcher so characters can be ranked against each other
fairly. Until 2026-09-28 the cap used the literal `last_sale_price`, unconfirmed
sales included (560 of them added $320M, 11% of the ≤ 2013 cap; Torchic and
Mudkip sat at #11 and #13 on one held-back Gold Star sale each).

`latest/tcgplayer_ids.csv` (from `history/tcgplayer_ids.py`, from 2026-10-02) gives each
English card its TCGplayer product id and card image, so the app can show a card without
PokemonPriceTracker: PPT's records are TCGplayer's catalog (its `tcgPlayerId`, `setName`,
`cardNumber` and `rarity` are TCGplayer's, and its `imageCdnUrl` is TCGplayer's public image
CDN), and tcgcsv.com publishes that catalog free every day. One row per matched card;
a card with no row has no match, never a guess.

| column | meaning |
|---|---|
| `asset_id` | alt.xyz asset id, as in `cards.csv` |
| `tcgplayer_id` | TCGplayer product id (PPT's `tcgPlayerId`) |
| `image_url` | `https://tcgplayer-cdn.tcgplayer.com/product/<id>_in_800x800.jpg` (PPT's `imageCdnUrl`; `_in_200x200`, `_in_1000x1000` and `_200w` exist too) |
| `tcgplayer_name`, `tcgplayer_set`, `tcgplayer_number`, `tcgplayer_rarity` | as TCGplayer lists them, the same strings PPT returns |
| `match` | how it was found: `unique` (one candidate), `best_set` (the set alt.xyz's set voted for), `named_variant` (alt.xyz's name spells out the bracket: "(Prerelease)", "(Poke Ball Pattern)"), `variant`, `plain_variant` (the plain card of a set's same-number variants) |

On the 2026-10-01 feed: 26,566 of 31,017 English cards (85.6%; 81% of those up to 2013),
95% of their PSA 10 market cap, 99 of the 100 biggest. The rest: TCGplayer has no such single (1,706: odd promos, renamed
cards), not a TCGplayer product at all (1,481: Topps Chrome, playing cards, vending cards,
Japanese-only sets alt.xyz doesn't label Japanese), or the name and number fit more than one
product and nothing tells them apart (1,264). Checked against 28 PPT-id pairs from PokeSniper
(22 same, 5 left out, 1 where alt.xyz's name has since become "[Winner]") and against
PokeSniper's own matcher run over the same records (the same id on 99.1% of the 12,736
cards both match; the docstring has the rules and the differences).

`latest/card_catalog.csv` (same script, from 2026-10-02) is the card list PokeSniper builds
from instead of PokemonPriceTracker: one row per card, every English TCG row of `cards.csv`
plus every TCGplayer English single no row is matched to, each with a `status` that says what
it is. The image of a row with a `tcgplayer_id` is
`https://tcgplayer-cdn.tcgplayer.com/product/<tcgplayer_id>_in_800x800.jpg`; no id, no image.

| column | meaning |
|---|---|
| `asset_id` | alt.xyz asset id (`cards.csv`); blank for a TCGplayer-only card |
| `tcgplayer_id` | TCGplayer product id; blank for an alt.xyz-only card |
| `status` | `linked` (the same pair as `tcgplayer_ids.csv`), `alt_only` (a real English card TCGplayer has no product for, or one the matcher won't guess between), `not_english` (alt.xyz files it as English but it isn't an English TCG card: Japanese and Chinese sets and promos, Topps movie cards, playing cards...), `tcgplayer_only` (a TCGplayer single alt.xyz has no row for) |
| `how` | `linked`: the `match` kind; `alt_only`: `no_candidate`, `variant_not_found` (a stamped / Cosmos / staff print TCGplayer doesn't list), `set_not_aligned`, `ambiguous_set`, `ambiguous_variant`; `not_english`: `not_on_tcgplayer` or `not_english_tcg`; `tcgplayer_only`: blank, or `candidate_of_alt_row` when an unmatched alt.xyz row might be this card |
| `name`, `number` | TCGplayer's for `linked` / `tcgplayer_only`; alt.xyz's subject and number otherwise |
| `set`, `set_source` | TCGplayer's set (`tcgplayer`); for an alt.xyz-only card the TCGplayer set its candidates are all in (`candidates`) or its alt.xyz set voted for (`voted`), else blank |
| `rarity` | TCGplayer's; blank for an alt.xyz-only card (unknown, not common) |
| `year` | alt.xyz's year for a card it has; else the set's year from `tcgplayer_sets.csv`, blank for a set spanning many years |
| `print_run` | `1st Edition`, `Shadowless`, `Reverse Holo` or blank, from alt.xyz's name |

On the 2026-10-01 feed: 40,749 rows: 26,566 linked, 1,784 alt.xyz-only (775 with PSA 10
copies; 928 have no PSA rows on alt.xyz), 2,667 not English (40 of 40 checked by hand were
right), 9,732 TCGplayer-only (978 of them a possible match for an unmatched alt.xyz row). In
a hand check of 60 random alt.xyz-only rows, 3 were still not English TCG cards.

`latest/tcgplayer_sets.csv` dates each TCGplayer set: `group_id`, `name`, `published` (blank:
TCGplayer gives no date), `year`, `year_source` (`published`; `learned`: 80%+ of its 5+ matched
alt.xyz cards carry that year, e.g. POP Series 1-9, EX Trainer Kit 1 2004 and 2 2005;
`manual`: First Partner Pack 2021, Pikachu World Collection 2000; `multi_year`: no single year
fits, e.g. Nintendo Promos 2002-2011, Miscellaneous Cards & Products, Blister Exclusives,
Burger King 2008-2009, Professor Program), `span_from`/`span_to` (years of its matched alt.xyz
cards), `linked_cards`, `note`.

PokeSniper's server downloads `latest/` from this repo on its own (raw GitHub
URLs, checked at boot and every few hours; see its `SAMAY_DATA_URL`), so nothing
has to be configured there. For a local checkout instead, set `SAMAY_DATA_URL=`
(empty) and `SAMAY_DATA_DIR=/path/to/latest`.

## Scheduled runs

`nightly/run_nightly.sh` does everything: full index listing (~65k cards, ~25 min),
scope filter, scrape with two retry passes, then `history/ingest.py` and
`history/metrics.py`. Two schedules, both in `.github/workflows/`:

- **nightly** (07:00 UTC): the 101 Pokemon in `nightly/subjects.txt` at every year (~20k
  cards: PokeSniper's 100 roster candidates plus Bulbasaur) plus the ~400 species in
  `nightly/vintage_species.txt` at 2013 or earlier (~13k cards, PokeSniper's Categories
  checklist), ~33k cards, ~2–3 h. Names match `subject` as whole words, with a hyphen
  matching a hyphen or a space ("Ho-Oh" also finds alt.xyz's "Ho Oh").
- **weekly full** (Sunday): every graded Pokemon card (~65k), so the vintage species'
  modern printings and every other species stay fresh too.

Each run commits `history/` and `latest/`. Run by hand:

```bash
nightly/run_nightly.sh                                    # nightly scope, ~2–3 h
NIGHTLY_SCOPE=full nightly/run_nightly.sh                 # everything, ~4 h
NIGHTLY_LIMIT=15 NIGHTLY_DATE=smoke nightly/run_nightly.sh   # 15-card smoke test
python3 history/ingest.py snapshots/2026-09-12            # (re)ingest one run
python3 history/metrics.py                                # rebuild latest/ only
python3 history/coverage.py                               # rebuild latest/characters.csv only
python3 history/flags.py                                  # append tonight's PSA 9 buy-condition flags to history/
python3 history/tcgplayer_ids.py --fetch                  # TCGplayer catalog -> latest/tcgplayer_ids.csv, card_catalog.csv, tcgplayer_sets.csv (~2 min)
```

`history/psa9_flags.csv` and `history/psa9_category_moves.csv` (from 2026-09-27, step 7 of the
nightly, append-only) log Sid's PSA 9 buy conditions forward so they can be checked 3, 6 and
12 months later: for every card of a top-50 Pokemon (the app's roster rank: alt-side market cap of cards up to
2013, `alt_market_cap_le2013_usd` in `latest/characters.csv`) whose PSA 10 is up 50%+ over 30 days, or whose card type is running
(group median 30-day change 30%+), or that is listed in `nightly/track_assets.txt`, one row a
night with that night's PSA 10 / PSA 9 prices, pops, tier and the flags that held
(`buy_rule_50` / `buy_rule_100` = top-50 card, PSA 10 up 50%+ / 100%+, PSA 9 up less than
10%); plus one row per card type a night with the category's median move. The rule and the
backtests behind it are in `analysis/README.md` ("Sid's buy rule").

Ingest keys sales by alt.xyz's own transaction id (`alt_tx_id` in `history/sales/*.csv`,
recorded from 2026-09-26) and lets a later run overwrite what the store knows about a
sale (`merge_sales` in `history/ingest.py`): alt.xyz keeps editing sales after listing
them (about 1% get a new date or price, 0.4% are flagged RELISTED / NOT_PAID or go from
PENDING to settled), and the clean price and % change columns are built from those
fields. A URL is not one sale: an eBay multi-quantity Buy It Now listing keeps its item
id while it sells the same card again and again (54k of the 3.9M URLs in one nightly
carry more than one sale). Rows stored before the id existed are matched by URL when
the URL carries one sale in both the run and the store, otherwise by URL + date + price,
and gain their id that way; a stored sale alt.xyz no longer lists under a URL it does
list is dropped. Before this the store kept one row per URL per month and never changed
it, so repeat sales were dropped (~69k came back on the first run with the fix, raising
`sales_total` on ~3,300 cards) and status flips never arrived.

Laptop fallback: `nightly/com.samaygodika.altscrape.plist` runs the same script at
02:00 via launchd (a closed lid still sleeps; launchd resumes on wake). The
2026-09-12 run took 33 h that way, which is why the GitHub Actions cron is the
primary schedule.

Env knobs: `NIGHTLY_SCOPE` (top60 = the subjects lists | full), `NIGHTLY_ALSO_GRADE` (9 on the
nightly scope, empty on full), `NIGHTLY_ALSO_LISTINGS` (PSA 9 live listings too, needs an
also-grade; on by default since 2026-09-26, 0 turns it off), `NIGHTLY_WORKERS` (4; the Actions
workflows use 6), `NIGHTLY_DELAY` (0.25 s per worker; Actions uses 0.05), `NIGHTLY_LIMIT`,
`NIGHTLY_DATE`, `NIGHTLY_SKIP_HISTORY=1`, `NIGHTLY_SKIP_UNCHANGED=1`.

`NIGHTLY_SKIP_UNCHANGED=1` (off by default, never on the weekly full run) skips the two sale
requests for cards whose sales cannot have changed since the previous run, and carries that
run's sale summary (`num_sales`, `last_sale_*`, `avg_last_3_sales`, `highest_sale`, `lowest_sale`)
into the new daily row with `sales_fetched` = 0; pops and live listings are still fetched, and
Sunday's full run refetches everything. Which cards qualify is decided by `sales_skip_reason`
in `alt_scraper.py`: today that is the ~14.5k cards (43% of the nightly scope) with no PSA 10
copies in the pop table and no PSA 10 sale on record. alt.xyz's search index carries no per-card
transaction counter (`index_transaction_count` is blank for all but one card), so an
"unchanged count" rule has nothing to work with yet; the code checks the field anyway in case
that changes. Measured on the 2026-09-22..25 nightlies: ~27k of ~134k requests a night (about
38 minutes of a 3 h scrape) and 0 of the 10,886 new PSA 10 sales those nights recorded.

## Running the scraper by itself

```bash
python3 alt_scraper.py cards.txt                      # one URL / asset id / "search: ..." per line
python3 alt_scraper.py --find "charizard base set holo"
python3 alt_scraper.py --list charizard               # every card whose subject has the word -> charizard_cards.txt
python3 alt_scraper.py --list '*' --out pokemon       # the whole index (~65k)
python3 alt_scraper.py --workers 4 --delay 0 --max-sales 0 --out pokemon pokemon/all_pokemon_cards.txt
```

Writes `cards.csv` (one row per card) and `sales.csv` (every PSA 10 sale, with the
sale's own eBay/Goldin/Alt link; `--max-sales N` keeps the newest N), plus
`listings.csv` (what is for sale right now at PSA 10). `--also-grade 9` adds PSA 9
sales; `--also-listings` then adds the PSA 9 live listings too, one more request per
card with any PSA 9s, its check time in `psa9_listings_checked_at` (on by default in the nightly since 2026-09-26).
Rows are written as each card finishes; `--resume` skips cards already in `cards.csv`.
`--list` also writes a `.json` sidecar the scraper uses to skip per-card lookups.
Low-pop cards are the interesting ones, so nothing is filtered by population
unless you pass `--min-pop N`. Cards with zero PSA 10s ever graded are skipped
(`--keep-empty` writes them as zeros); cards whose pop table has no PSA rows at all
are kept with blank pops.

Other options: `--grade 9`, `--company BGS --grade 9.5`, `--out DIR`, `--loose`
(with `--list`, also keep set-name-only matches), `--include-ungraded`.

### cards.txt format

```
https://alt.xyz/itm/7c10295d-9758-4201-9519-7cace176ebc6/external   # a listing page link
asset:3c9149c8-2390-44ee-b53e-e9f6db054e92                            # an asset id from --find / --list
search: 1999 Pokemon Base Set Holo Blastoise #2                       # top search hit is used
```

`#` starts a comment only with a space on both sides, so `#4` is safe.

## How it works

alt.xyz is a React app; every number comes from a GraphQL API at
`https://alt-platform-server.production.internal.onlyalt.com/graphql/<OperationName>`.
Until 2026-10-01 it answered with no login; since 2026-10-02 every market-data operation
needs a logged-in session (see **Login** below). Operations used: `ExternalListing` /
`PubliclyVisibleItem` (page id -> asset id), `AssetCardPops` (population table),
`AssetMarketTransactions` (sales, filtered to PSA `"10.0"`; grades must have one
decimal), `SearchServiceConfig` (short-lived Typesense key for `--find`/`--list`,
refreshed per page), `AssetLiveExternalTransactions` (live listings at one
company + grade — company alone returns nothing — with `buyItNowPrice` or
`auctionInfo { endDate numBids highestBid }`; the source of `listings.csv`,
`alt_public_url` and the live columns). Sales alt.xyz flags as RELISTED / NOT_PAID / PENDING are kept in
`sales.csv` with a `skipped_reason` and excluded from every number.

The script waits between requests and retries failed calls three times.
`robots.txt` allows crawling; keep the worker count modest.

### Why a 403

On 2026-10-02 alt.xyz changed two things at once. Its card pages now ask for a free account
before showing market data ("Sign up for free. Access all of the market data for this asset
with a free account"; a logged-out page makes no API calls). And its load balancer started
answering `403 Forbidden` (`server: awselb/2.0`, body `Forbidden`) to script requests that
claim to be a browser: this scraper had sent a copied Chrome user-agent since day one, and
that was what got refused, not the missing login. Checked the same day: the identical
request with the user-agent `pokemon-psa10-history/1.0 (school project; ...)` and no login
returned 200 for every operation above, and so did Python's default user-agent. The scraper
now identifies itself honestly, and that is the whole fix.

The scraping is done with alt.xyz's permission for Samay's school project (ask them for it
in writing; their terms want written authorization for scripts). The scraper keeps the
same pace as before and sends nothing the site itself does not send.

### Login (optional)

Not needed for the scrape as of 2026-10-02, but supported so requests can go out the way
a signed-in visitor sends them, with Samay's own account, in case alt.xyz later checks the
login on the API as well as on the page. How the site does it (read off its own requests):

- Login provider is Stytch. The browser keeps a long-lived session token in the
  `stytch_session` cookie and sends `authorization: Bearer <session JWT>` on every API
  call; the JWT lives 5 minutes.
- A fresh JWT comes from `POST https://api.stytch.com/sdk/v1/sessions/authenticate`
  with `Authorization: Basic base64(<site public token>:<session token>)`, an empty body
  and the site's `Origin`. The reply also says when the session itself expires (one year
  from login); the scraper prints that once per run (`alt.xyz login OK, session valid
  until ...`).

Setup: sign in to alt.xyz, copy the value of the `stytch_session` cookie (DevTools ->
Application -> Cookies -> alt.xyz), and either

```bash
export ALT_SESSION_TOKEN='<value>'
```

or put it on the first line of `~/.alt_session` (`chmod 600`). `ALT_SESSION_FILE` points
elsewhere. In GitHub Actions it is the repository secret `ALT_SESSION_TOKEN`
(`.github/workflows/*.yml` pass it through). With no token configured, requests go out
logged out, as before. A stale JWT is refreshed once and the request retried; an expired
or wrong session token fails the refresh with Stytch's status. Never commit the token:
this repository is public.
