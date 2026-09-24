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
latest/               what the server reads: cards.csv, series/<xx>.csv, recent_sales/<xx>.csv, characters.csv, summary.json  (committed)
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
| `clean_last_sale_price/date/source`, `outliers_excluded`, `last_sale_unconfirmed` | the newest sale after holding back junk rows: a sale below 1/4x or above 6x the running median of the card's last 12 accepted sales (past year) is held back unless confirmed — two consecutive high sales confirm a jump, five consecutive low sales a drop. A card with fewer than 4 sales that year is measured against its last 12 sales of any age with a 1/10x–10x band instead (2026-09-21; before that such cards had no filter, which let a PSA 5 sold at $31 and a $122,000 Goldin lot for a signed Steve Jobs document into PSA 10 histories). After a confirmed move only sales from that move onward form the reference, and a sale within 2x of the last accepted one is never held (see `drop_outliers` in `history/metrics.py`); 0.18% of all sales. Before any of that, a PWCC Weekly Auctions lot that alt.xyz records under both fanaticscollect.com and pwccmarketplace.com (same card, date and price) counts once, here and in `recent_sales/` (2026-09-22; the copy used to confirm its own jump, e.g. one $204,000 Legendary Collection Articuno lot read as +1,260%). The change and volume columns are built from these clean sales. The app shows the literal `last_sale_price` and marks it unconfirmed when `last_sale_unconfirmed` = 1 (the newest sale is one the filter is holding back). |
| `price_chg_30d_pct`, `_90d_`, `_1y_` | median of the last 3 clean sales vs the same median as of 30/90/365 days earlier; blank unless both exist and at least one sale happened in the window. |
| `volume_30d`, `_90d`, `_1y` | clean PSA 10 sales in the window. |
| `pop_30d_ago`, `pop_chg_30d`, `mkt_cap_chg_30d_pct` | need 30 days of daily snapshots; blank until the series is that old (first daily file: 2026-09-11). |
| `median_last_3`, `sales_first_date`, `sales_total`, `history_days` | how much history stands behind the row. |
| `pop_at_grade_9` | PSA 9 pop, same blank-vs-0 rule as `pop_at_grade`. Every run. |
| `psa9_scraped_date` | newest nightly that pulled this card's PSA 9 sales (nightly scope only, from 2026-09-23). **Blank = PSA 9 sales not collected**, and then every `psa9_*` sale column below is blank too. |
| `psa9_last_sale_price/date/source`, `psa9_clean_last_sale_price`, `psa9_last_sale_unconfirmed`, `psa9_median_last_3`, `psa9_volume_30d`, `psa9_price_chg_30d_pct`, `psa9_sales_total` | the PSA 10 definitions above applied to PSA 9 sales (same mirror rule and outlier filter). `psa9_sales_total` = 0 means collected and none sold. |
| `psa9_to_psa10_ratio` | `psa9_median_last_3` / `median_last_3`. |
| `listings_checked_at` | UTC time alt.xyz was last asked what is for sale right now at PSA 10 (from 2026-09-25: the nightly scope daily, everything else on the weekly full run). **Blank = never checked**, and then every live column below is blank too (unknown, not "nothing listed"). |
| `live_auction_count`, `next_auction_end`, `next_auction_bid`, `next_auction_bid_count`, `next_auction_source`, `next_auction_url`, `last_auction_end` | the running PSA 10 auctions at that check (eBay, Fanatics Collect, CardHobby as alt.xyz mirrors them; Goldin / Heritage / PWCC weekly lots are not in alt.xyz's feed). `next_*` = the one ending soonest; end times are UTC. **A snapshot, not live:** the app compares the end times with its own clock — while `last_auction_end` is in the future at least one auction may still be running. The bid is the high bid at the check, or the opening price while the bid count is 0; it is not a price for the card (bids jump in the final minutes). |
| `lowest_bin_price`, `lowest_bin_source`, `lowest_bin_url` | the cheapest PSA 10 Buy It Now listing at that check (PokeSniper's `lowestListingPrice`). It can sell or be pulled between checks; nothing marks that. |

`latest/series/<first two hex of asset_id>.csv` holds the weekly PSA 10 sale
series per asset (`week_start, n_sales, median_price, low, high`) for charts;
the server's `GET /api/samay-data/series?ids=…` reads one shard per lookup.
`latest/recent_sales/<xx>.csv` holds each card's last 10 PSA 10 sales, newest
first, including sales alt.xyz flags (`status` = ok / RELISTED / NOT_PAID /
PENDING …) and sales the filter is holding back (`outlier` = 1), with the sale
URL — `GET /api/samay-data/recent-sales?ids=…` for the app's price dropdown.
`latest/recent_sales_psa9/<xx>.csv` is the same for PSA 9 sales (same columns).

`latest/characters.csv` (from `history/coverage.py`) has one row per name in
`nightly/subjects.txt`: English rows in the feed, how many have a PSA 10 pop /
a real zero / an unknown pop / a price, and the alt-side market cap
(Σ `pop_at_grade` × `last_sale_price` over every row, all years and ≤ 2013),
computed with no matcher so characters can be ranked against each other
fairly. PokeSniper's `scripts/lib/altSide.js` uses the same definition.

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
```

Laptop fallback: `nightly/com.samaygodika.altscrape.plist` runs the same script at
02:00 via launchd (a closed lid still sleeps; launchd resumes on wake). The
2026-09-12 run took 33 h that way, which is why the GitHub Actions cron is the
primary schedule.

Env knobs: `NIGHTLY_SCOPE` (top60 = the subjects lists | full), `NIGHTLY_WORKERS` (4), `NIGHTLY_DELAY`
(0.25 s per worker), `NIGHTLY_LIMIT`, `NIGHTLY_DATE`, `NIGHTLY_SKIP_HISTORY=1`.

## Running the scraper by itself

```bash
python3 alt_scraper.py cards.txt                      # one URL / asset id / "search: ..." per line
python3 alt_scraper.py --find "charizard base set holo"
python3 alt_scraper.py --list charizard               # every card whose subject has the word -> charizard_cards.txt
python3 alt_scraper.py --list '*' --out pokemon       # the whole index (~65k)
python3 alt_scraper.py --workers 4 --delay 0 --max-sales 0 --out pokemon pokemon/all_pokemon_cards.txt
```

Writes `cards.csv` (one row per card) and `sales.csv` (every PSA 10 sale, with the
sale's own eBay/Goldin/Alt link; `--max-sales N` keeps the newest N). Rows are
written as each card finishes; `--resume` skips cards already in `cards.csv`.
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
`https://alt-platform-server.production.internal.onlyalt.com/graphql/<OperationName>`,
called with no login for public data. Operations used: `ExternalListing` /
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
