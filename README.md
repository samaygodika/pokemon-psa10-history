# pokemon-psa10-history

Daily PSA 10 price and population history for Pokemon cards, scraped from alt.xyz.

Pulls PSA population counts and graded sale prices for Pokemon cards from
[alt.xyz](https://alt.xyz), keeps a day-by-day history, and publishes one folder
(`latest/`) that [PokeSniper](https://github.com/NovaCast/PokeSniper)'s server reads
as its only PSA 10 price/population source. Python 3.8+ standard library only.

```
alt_scraper.py        the scraper (one run = cards.csv + sales.csv)
nightly/              scheduled run: index listing -> scope -> scrape -> ingest -> latest/
history/              the store: assets.csv, daily/<date>.csv, sales/<month>.csv  (committed)
latest/               what the server reads: cards.csv, series/<xx>.csv, summary.json  (committed)
snapshots/            raw per-run output (gitignored, hundreds of MB)
.github/workflows/    GitHub Actions cron: nightly top-60, weekly full index
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
| `clean_last_sale_price/date/source`, `outliers_excluded`, `last_sale_unconfirmed` | the newest sale after holding back junk rows: a sale below 1/4x or above 6x the running median of the card's last 12 accepted sales (past year) is held back unless confirmed — two consecutive high sales confirm a jump, five consecutive low sales a drop (see `drop_outliers` in `history/metrics.py`); 0.16% of all sales. The change and volume columns are built from these clean sales. The app shows the literal `last_sale_price` and marks it unconfirmed when `last_sale_unconfirmed` = 1 (the newest sale is one the filter is holding back). |
| `price_chg_30d_pct`, `_90d_`, `_1y_` | median of the last 3 clean sales vs the same median as of 30/90/365 days earlier; blank unless both exist and at least one sale happened in the window. |
| `volume_30d`, `_90d`, `_1y` | clean PSA 10 sales in the window. |
| `pop_30d_ago`, `pop_chg_30d`, `mkt_cap_chg_30d_pct` | need 30 days of daily snapshots; blank until the series is that old (first daily file: 2026-09-11). |
| `median_last_3`, `sales_first_date`, `sales_total`, `history_days` | how much history stands behind the row. |

`latest/series/<first two hex of asset_id>.csv` holds the weekly PSA 10 sale
series per asset (`week_start, n_sales, median_price, low, high`) for charts;
the server's `GET /api/samay-data/series?ids=…` reads one shard per lookup.
`latest/recent_sales/<xx>.csv` holds each card's last 10 PSA 10 sales, newest
first, including sales alt.xyz flags (`status` = ok / RELISTED / NOT_PAID /
PENDING …) and sales the filter is holding back (`outlier` = 1), with the sale
URL — `GET /api/samay-data/recent-sales?ids=…` for the app's price dropdown.

PokeSniper's server downloads `latest/` from this repo on its own (raw GitHub
URLs, checked at boot and every few hours; see its `SAMAY_DATA_URL`), so nothing
has to be configured there. For a local checkout instead, set `SAMAY_DATA_URL=`
(empty) and `SAMAY_DATA_DIR=/path/to/latest`.

## Scheduled runs

`nightly/run_nightly.sh` does everything: full index listing (~65k cards, ~25 min),
scope filter, scrape with two retry passes, then `history/ingest.py` and
`history/metrics.py`. Two schedules, both in `.github/workflows/`:

- **nightly** (07:00 UTC): the 101 Pokemon in `nightly/subjects.txt` at every year (~20k
  cards, the PokeSniper roster plus the next 10) plus the ~400 species in
  `nightly/vintage_species.txt` at 2013 or earlier (~13k cards, PokeSniper's Categories
  checklist), ~31k cards, ~2.5–4.5 h.
- **weekly full** (Sunday): every graded Pokemon card (~65k), so chase-checklist
  species and roster candidates outside the top 60 stay fresh too.

Each run commits `history/` and `latest/`. Run by hand:

```bash
nightly/run_nightly.sh                                    # top-60, ~2 h
NIGHTLY_SCOPE=full nightly/run_nightly.sh                 # everything, ~4 h
NIGHTLY_LIMIT=15 NIGHTLY_DATE=smoke nightly/run_nightly.sh   # 15-card smoke test
python3 history/ingest.py snapshots/2026-09-12            # (re)ingest one run
python3 history/metrics.py                                # rebuild latest/ only
```

Laptop fallback: `nightly/com.samaygodika.altscrape.plist` runs the same script at
02:00 via launchd (a closed lid still sleeps; launchd resumes on wake). The
2026-09-12 run took 33 h that way, which is why the GitHub Actions cron is the
primary schedule.

Env knobs: `NIGHTLY_SCOPE` (top60|full), `NIGHTLY_WORKERS` (4), `NIGHTLY_DELAY`
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
refreshed per page), `AssetLiveExternalTransactions` (live listings, for
`alt_public_url`). Sales alt.xyz flags as RELISTED / NOT_PAID / PENDING are kept in
`sales.csv` with a `skipped_reason` and excluded from every number.

The script waits between requests and retries failed calls three times.
`robots.txt` allows crawling; keep the worker count modest.
