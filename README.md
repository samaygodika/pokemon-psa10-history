# pokemon-psa10-history

Daily PSA 10 price and population history for Pokemon cards, scraped from alt.xyz.

Pulls PSA population counts and graded sale prices for Pokemon cards from
[alt.xyz](https://alt.xyz), keeps a day-by-day history, and publishes one folder
(`latest/`) that [PokeSniper](https://github.com/NovaCast/PokeSniper)'s server reads
as its only PSA 10 price/population source. Python 3.8+ standard library only.

```
alt_scraper.py        the scraper (one run = cards.csv + sales.csv + listings.csv)
nightly/              scheduled run: index listing -> scope -> scrape -> ingest -> latest/
history/              the store: assets.csv, live_listings.csv and the check files (committed); daily/<date>.csv and
                      sales/<month>.csv (1.2 GB, rewritten nightly) live on the GitHub release tagged `store`, one
                      gzip tarball per generation, pulled and pushed by history/store.py (2026-10-17)
latest/               what the server reads: cards.csv, series/<xx>.csv, recent_sales/<xx>.csv, recent_sales_psa9/<xx>.csv, clean_sales/<month>.csv, characters.csv, tcgplayer_ids.csv, card_catalog.csv, tcgplayer_sets.csv, watchlist_listings.csv, all_grades/{grades,sales}.csv (the Gold Star category at every PSA grade), summary.json (with the sha256 of each)  (committed)
snapshots/            raw per-run output (gitignored, hundreds of MB)
.github/workflows/    GitHub Actions cron: nightly roster candidates + vintage checklist, weekly full index
```

## The data PokeSniper gets

`latest/cards.csv` is the scraper's `cards.csv` (one row per alt.xyz asset, newest
numbers per asset) plus history-derived columns. A record alt.xyz no longer indexes
(renamed, merged into another record, removed) leaves the file once it has not been seen
for more than 14 days (2026-10-21; every live asset is refreshed within 7 days, so that is
two missed Sundays): 18 rows on the 10-20 feed, e.g. the stale "English Holo" twin of the
2005 Delta Species Groudon Star that made PokeSniper's matcher skip the card. The row comes
back by itself if the record reappears; its sales stay in the store. A row whose
`scraped_at` is between 7 and 14 days old is on its way out unless Sunday's run finds it. Blank always means "the data can't
say", never 0:

| column | meaning |
|---|---|
| `pop_at_grade`, `company_total_pop` | PSA 10 pop and all-PSA-grades pop from the card's population table. **Blank when alt.xyz has no PSA rows for the record** (CGC/BGS only); `0` when PSA rows exist and the 10 count is zero (since 2026-09-18 such cards are in the feed instead of being skipped). Usually such a card has no PSA 10 sales, but not always: 38 rows on 2026-10-20 carry clean PSA 10 sales under a pop of 0 (alt.xyz split or duplicate pop records, e.g. the 1999 Base Set 1st Edition Holo Alakazam: pop 0, PSA 9 pop 510, 8 sales of $8,100-$14,400); the sales are real, the pop is what alt.xyz shows, so a market cap from pop x price is 0 there. |
| `index_total_pop`, `index_transaction_count` | alt.xyz search-index counts. `index_total_pop`: graded copies across every company and grade, present even when the pop columns are blank (67,856 of 67,859 rows). `index_transaction_count`: the index's per-asset transaction counter, which alt.xyz fills for exactly one card (the 2023 Obsidian Flames Charizard ex, 1 row of 67,859 on 2026-10-17), so treat it as blank; it is kept because the skip-unchanged rule would use it if alt.xyz ever filled it. |
| `last_sale_price/date/source` | the newest PSA 10 sale alt.xyz lists for the card that it does not itself flag (RELISTED / NOT_PAID / PENDING, or REMOVED): the sale `last_sale_unconfirmed`, `last_sale_single` and `last_sale_title_check` are about, same as `psa9_last_sale_*`. Until 2026-10-21 these were alt.xyz's own last-sale field as scraped, which lags the card's sale list on a few cards a day (7 of 67,859 on 2026-10-20), so the flags could describe a newer sale than the one shown; a card whose every listed sale is flagged keeps the scraped field. |
| `num_sales`, `avg_last_3_sales`, `highest_sale`, `lowest_sale` | the scraper's own PSA 10 sale summary at scrape time, over every sale alt.xyz returned for the card minus the ones alt.xyz itself flags (RELISTED / NOT_PAID / PENDING): how many, the mean price of the newest 3, the highest and the lowest price ever. Literal alt.xyz figures with no outlier filter and no mirror rule: a mislabeled $15 lot is a card's `lowest_sale`, a PWCC lot listed twice counts twice. For anything shown or ranked, use `sales_total`, `median_last_3`, the `clean_*` columns and `cap_price`, which are built from the clean sales. (Documented 2026-10-17; the columns have been there from the start.) |
| `scraped_at` | when this card's row was fetched from alt.xyz. **UTC with offset (`2026-10-17T02:14:09+00:00`) from the 2026-10-17 nightly on**; rows written before that carry naive local time with no offset (UTC on GitHub Actions, Pacific on the laptop fallback) and sort before any new stamp as text. The listings refresh can move `listings_checked_at` past it (see *Listings refresh*). |
| `alt_url`, `alt_public_url` | `alt_url` is the card's alt.xyz research page (`alt.xyz/research/<asset_id>`: pops and the full sale history; needs a free alt.xyz account since 2026-10-02). `alt_public_url` is the public page of one of the card's live external listings at the check (`alt.xyz/itm/<listing id>/external`, viewable without an account), blank when the card had no live listing then (filled on 19,031 of 67,859 rows on 2026-10-17); a listing page, so it can outlive the listing. |
| `listing_source`, `listing_grade`, `listing_grading_company`, `listing_price`, `listing_url` | **Deprecated, always blank.** The scraper's original single-listing columns, superseded by `listings_checked_at` and the `live_auction_*` / `next_auction_*` / `lowest_bin_*` columns (2026-09-24); blank on every row of the published feed. Kept in the file for now so a parser that indexes by header keeps working; they will be removed in a later contract change announced on PR #1. Do not read them. |
| `clean_last_sale_price/date/source`, `outliers_excluded`, `last_sale_unconfirmed` | the newest sale after holding back junk rows: a sale below 1/4x or above 6x the running median of the card's last 12 accepted sales (past year) is held back unless confirmed — two consecutive high sales confirm a jump, five consecutive low sales a drop, and repeat sales of one listing URL count once towards those (2026-09-28: one $15 eBay Buy It Now that sold ten times had reset Dragon Frontiers Gold Star Charizard's reference from $58k to $15, a +2,619,900% 90-day change). A card with fewer than 4 sales that year is measured against its last 12 sales of any age with a 1/10x–10x band instead (2026-09-21; before that such cards had no filter, which let a PSA 5 sold at $31 and a $122,000 Goldin lot for a signed Steve Jobs document into PSA 10 histories). A card with only 1 or 2 earlier sales is measured against those with the same 1/10x–10x band, and two distinct listings confirm a move either way (2026-09-28; before that it had no filter, and single lots such as a $78,000 PWCC sale of a $490 BW87 Leafeon promo or a $3.72M XY Mudkip became clean prices). After a confirmed move only sales from that move onward form the reference, and a sale within 2x of the last accepted one is never held (see `drop_outliers` in `history/metrics.py`); 0.18% of all sales. Before any of that, a PWCC Weekly Auctions lot that alt.xyz records under both fanaticscollect.com and pwccmarketplace.com (same card, date and price) counts once, here and in `recent_sales/` (2026-09-22; the copy used to confirm its own jump, e.g. one $204,000 Legendary Collection Articuno lot read as +1,260%). The volume columns and every price level are built from these clean sales; the change columns also count the held-back sales on the high side (see `price_chg_30d_pct`). The app shows the literal `last_sale_price` and marks it unconfirmed when `last_sale_unconfirmed` = 1 (the newest sale is one the filter is holding back). Since 2026-10-18 `last_sale_single` (last columns of the file) says whether that held sale is one real-looking sale on a rarely traded card waiting for a second (`1`, shown as ≈) or is held for another reason (`0`, shown as held back). |
| `price_chg_30d_pct`, `_90d_`, `_1y_` | median of the last 3 sales vs the same median as of 30/90/365 days earlier; blank unless both exist and at least one sale happened in the window. The sales are the clean ones **plus the ones the outlier filter still holds back on the HIGH side** (2026-10-01). Over the past year a held-back high newest sale was later shown real 81% of the time (90% on cards up to 2013; eBay 80%, major auction houses 86%) and a held-back low one 17% (4%), so highs count here and lows stay out. Without them the cards that had just moved dropped out of every category's median % (452 cards with a held-back sale in the last 30 days had a blank 30-day change). One held sale moves a card that trades often only part of the way, since the price is a median of 3; on a dormant card it counts in full. Prices and market caps (`median_last_3`, `cap_price*`, `clean_*`, `series/`, `clean_sales/`, `characters.csv`) still wait for a confirming sale. |
| `volume_30d`, `_90d`, `_1y` | clean PSA 10 sales in the window. |
| `pop_30d_ago`, `pop_chg_30d`, `mkt_cap_chg_30d_pct` | need 30 days of daily snapshots; blank until the series is that old (first daily file: 2026-09-11). |
| `median_last_3`, `sales_first_date`, `sales_total`, `history_days` | how much history stands behind the row. |
| `pop_at_grade_9` | PSA 9 pop, same blank-vs-0 rule as `pop_at_grade`. Every run. |
| `psa9_scraped_date` | newest nightly that pulled this card's PSA 9 sales (nightly scope only, from 2026-09-23). **Blank = PSA 9 sales not collected**, and then every `psa9_*` sale column below is blank too. |
| `psa9_last_sale_price/date/source`, `psa9_clean_last_sale_price`, `psa9_last_sale_unconfirmed`, `psa9_median_last_3`, `psa9_volume_30d`, `psa9_price_chg_30d_pct`, `psa9_sales_total` | the PSA 10 definitions above applied to PSA 9 sales (same mirror rule and outlier filter). `psa9_sales_total` = 0 means collected and none sold. Since 2026-10-17 `psa9_price_chg_30d_pct` also counts the held-back high PSA 9 sales, exactly as `price_chg_30d_pct` has since 2026-10-01 (it counted clean sales only before; 272 rows moved on that day's feed, every one flagged `30d` in `psa9_chg_held_windows` below). |
| `psa9_to_psa10_ratio` | `psa9_median_last_3` / `median_last_3`. |
| `listings_checked_at` | UTC time alt.xyz was last asked what is for sale right now at PSA 10 (from 2026-09-25: the nightly scope daily, everything else on the weekly full run). **Blank = never checked**, and then every live column below is blank too (unknown, not "nothing listed"). Whether a card has anything listed tracks its PSA 10 pop closely (pop 1000+: ~99% do; pop 26–100: ~60%; pop 1–2: ~10%; pop 0: ~1%), so a low-pop card with a check time and no listing is a real "nothing listed". (For one day, 2026-09-26, 14,828 of these were blanked by a mistaken "alt.xyz outage" rule in the scraper — the nightly scope is sorted by pop, so the falling listing rate through a run is the cards, not the endpoint. Reverted the same day.) Moved forward by the listings refresh between nightlies for cards with a running auction (2026-10-03). |
| `live_auction_count`, `next_auction_end`, `next_auction_bid`, `next_auction_bid_count`, `next_auction_source`, `next_auction_url`, `last_auction_end` | the running PSA 10 auctions at that check, as alt.xyz mirrors them: eBay (~75%), Fanatics Collect including its weekly lots (the six-figure vintage auctions), CardHobby, Pristine Auction, a few Goldin; no Heritage. Since 2026-10-06 also **alt.xyz's own** weekly auctions and fixed-price marketplace (source `Alt`, link `alt.xyz/itm/<listing id>`; these come from one bulk pull of every card per nightly and per listings refresh, so they show on cards that have a check time). Since 2026-10-15 the Fanatics Collect Buy Nows come from Fanatics' own marketplace too (every live Pokémon PSA 9/10 listing, one bulk pull per nightly, see 'Fanatics Collect Buy Nows, read directly'), so `lowest_bin_*` sees all of them, not alt.xyz's partial mirror. `next_*` = the one ending soonest; end times are UTC (alt.xyz reports CardHobby's in US Eastern, `-04:00`; since 2026-10-17 the scraper converts them and the feed compares ends as instants, so a CardHobby auction is no longer pruned or ordered up to 4 h off). **A snapshot, not live:** the app compares the end times with its own clock — while `last_auction_end` is in the future at least one auction may still be running. The bid is the high bid at the check, or the opening price while the bid count is 0; it is not a price for the card (bids jump in the final minutes). A listing can also be pulled early: a link may lead to eBay's "similar items" page. |
| `lowest_bin_price`, `lowest_bin_source`, `lowest_bin_url` | the cheapest PSA 10 Buy It Now listing at that check (PokeSniper's `lowestListingPrice`). It can sell or be pulled between checks, and alt.xyz itself keeps ended BINs in its feed for months and re-serves them on every check (one returned as live on 2026-09-25 had ended on June 16; the listing record has no date or status field to tell), so treat the link as "was listed at", not "is listed at". Ended *auctions* alt.xyz does drop within hours. A BIN alt.xyz has not shown again for 7 days is dropped (`history/ingest.py`). How long it has been listed: `lowest_bin_first_seen` below. |
| `psa9_listings_checked_at` | the PSA 9 counterpart of `listings_checked_at`: UTC time of the last answer from alt.xyz about what is for sale at PSA 9 (set whenever that request succeeded, even with nothing listed; blank when it failed). **On from the 2026-09-26 nightly** (`NIGHTLY_ALSO_LISTINGS`, default 1; `--also-listings` in the scraper); blank in feeds built before that. Nightly scope only, like `psa9_scraped_date`; cards with zero PSA 9 copies are not asked and stay blank; blank = never checked at PSA 9, so every `psa9_live_*` / `psa9_lowest_bin_*` column below is blank too. |
| `psa9_live_auction_count`, `psa9_next_auction_end`, `psa9_next_auction_bid`, `psa9_next_auction_bid_count`, `psa9_next_auction_source`, `psa9_next_auction_url`, `psa9_last_auction_end`, `psa9_lowest_bin_price`, `psa9_lowest_bin_source`, `psa9_lowest_bin_url` | the ten live columns above for PSA 9 listings: same meanings and caveats (a snapshot at `psa9_listings_checked_at`, not live; ended auctions pruned, a BIN dropped after 7 days unseen). On from the 2026-09-26 nightly. A count of 0 = checked, nothing running. |
| `price_chg_60d_pct`, `price_chg_180d_pct`, `volume_60d`, `volume_180d` | the `price_chg_*` and `volume_*` rules for 60 and 180 days, so a 2-month or 6-month window is measured instead of interpolated (2026-09-28). |
| `cap_price` | what the card counts at in a market cap: `median_last_3`, else the newest clean sale of any age. Never an unconfirmed sale (2026-09-28). |
| `chg_held_windows` | the windows (`30d 60d 90d 180d 1y`, space-separated) whose `price_chg_*` figure rests on a held-back high sale, i.e. differs from the clean-only one; blank for most cards. For an "≈" next to that %. `mkt_cap_chg_30d_pct` follows the 30d one. (2026-10-01). |
| `lowest_bin_first_seen` | the run day the feed first recorded the `lowest_bin_url` listing for this card (2026-10-03; earlier rows backfilled from the store's git history, so never before 2026-09-25). alt.xyz re-serves ended Buy It Nows for months and its record has no date or status, so this is the only age there is: the older, the likelier it has ended. Grey out or hide old ones. Blank = no BIN (or unknown). Only the cheapest BIN per card is stored, so a listing that stops being the cheapest and later is again starts its age over. |
| `next_auction_bid_suspect` | `1` when `next_auction_bid` (high bid, or opening price at 0 bids) is above 6x `median_last_3`, the same band the sale filter uses: a mislabeled lot, or a re-pricing no sale has confirmed yet. Do not show such a bid as the card's price. `0` = judged and not out of line; blank = no auction, or no clean reference to judge by (then nothing is known). |
| `psa9_lowest_bin_first_seen`, `psa9_next_auction_bid_suspect` | the same two for the PSA 9 listing columns, judged against `psa9_median_last_3`. (2026-10-03). |
| `lowest_bin_verified_at`, `psa9_lowest_bin_verified_at` | when eBay itself last confirmed the `lowest_bin_url` listing is still live (UTC; see *eBay verification of Buy It Nows*). Blank = not an eBay listing, or not asked yet; never "dead": listings eBay says have ended are removed, so the BIN columns then show the card's next cheapest live listing, or nothing. (2026-10-06). |
| `lowest_bin_suspect`, `psa9_lowest_bin_suspect` | `1` when the cheapest Buy It Now (`lowest_bin_price`, `psa9_lowest_bin_price`) asks more than 6x the card's `cap_price` at that grade (`median_last_3`, else its newest clean sale of any age), the same band as `next_auction_bid_suspect`: a real listing at an asking price no sale backs (e.g. Arceus LV.X DP53 PSA 9 at $100,000 on Fanatics Collect vs a $359 last clean sale). Do not show such an ask as what the card costs. `0` = judged and not out of line; blank = no BIN, or no clean sale ever. (2026-10-09). |
| `lowest_bin_ref_date`, `psa9_lowest_bin_ref_date` | the date of the clean sale the matching `lowest_bin_suspect` / `psa9_lowest_bin_suspect` flag was judged against: the newest clean sale at that grade on or before the data date (the newest of the three behind `median_last_3`, or the any-age fallback itself; for PSA 10 this equals `clean_last_sale_date`). On a rarely traded card that sale can be old (the Arceus LV.X DP53 PSA 9 flag rests on a 2025-05-01 sale), so show or discount a flag by the age of what it rests on. Filled exactly when the flag is `0` or `1`; blank when it is blank. A date rather than a day count, so it cannot drift between syncs. (2026-10-10) |
| `cap_price_30d_ago`, `cap_price_60d_ago`, `cap_price_90d_ago`, `cap_price_180d_ago`, `cap_price_1y_ago` | `cap_price` as of that many days ago; blank = no clean sale yet by then. A group's market cap change over a window is Σ(`cap_price` × pop) / Σ(`cap_price_N_ago` × pop) − 1 over its cards that have both: a card that didn't sell counts as unchanged, and no card's % is weighted by its own jump. Pop is today's for every window until the daily files are that old. |
| `psa9_price_chg_60d_pct`, `psa9_price_chg_90d_pct`, `psa9_price_chg_180d_pct`, `psa9_price_chg_1y_pct`, `psa9_volume_60d`, `psa9_volume_90d`, `psa9_volume_180d`, `psa9_volume_1y`, `psa9_cap_price`, `psa9_cap_price_30d_ago`, `psa9_cap_price_60d_ago`, `psa9_cap_price_90d_ago`, `psa9_cap_price_180d_ago`, `psa9_cap_price_1y_ago`, `psa9_chg_held_windows` | the PSA 10 window columns (`price_chg_*`, `volume_*`, `cap_price`, `cap_price_*_ago`, `chg_held_windows` above) applied to the PSA 9 sales by the same code, so the PSA 9 view can measure 2-, 3-, 6- and 12-month windows and a PSA 9 market cap (Σ `psa9_cap_price` × `pop_at_grade_9`, and its change by the Σ/Σ rule over `psa9_cap_price_N_ago`) instead of being stuck at one month on the PSA 10 cap. Same rules: the change columns count held-back high PSA 9 sales and `psa9_chg_held_windows` names the windows that rest on one; volume and every price level are clean sales only; `psa9_cap_price` = `psa9_median_last_3`, else the newest clean PSA 9 sale of any age. Blank under the same rule as the other `psa9_*` sale columns (`psa9_scraped_date` blank = PSA 9 sales not collected; a `psa9_volume_*` of 0 then means collected and none sold). (2026-10-17) |
| `last_sale_single`, `psa9_last_sale_single`, `last_sale_status`, `psa9_last_sale_status` | Sid's 10-17 ask: tell a real sale that is only waiting for a second sale (≈) from one that may not have completed (held back), without changing any price. `last_sale_single`: `1` when `last_sale_unconfirmed` is 1 **and** the newest sale is held on the HIGH side with the filter in its stale or thin band at that sale (fewer than 4 clean sales in the year before it, see the filter above): one real-looking sale on a rarely traded card, held only for want of a second; measured over the store's history such sales were later shown real 90% of the time (31% for a held high on a card that trades often, 17% for a held low). `0` = unconfirmed for another reason (a high on a liquid card's band, or a held low). Blank = the newest sale is not held, or no sale. `last_sale_unconfirmed` keeps its meaning (the union of both cases); this column splits it, so an app that reads only the old flag sees nothing change. `last_sale_status`: the state of the **newest sale alt.xyz lists** for the card, any status: `completed` (it is the sale in `last_sale_*`), `pending` (alt.xyz PENDING or PAYMENT_INITIATED: the auction ended, payment not settled), `unpaid` (NOT_PAID), `relisted` (RELISTED). When it is not `completed`, `last_sale_*` is the newest completed sale before it and the newer sale is the first row of `recent_sales/` with its price and date; a not-completed sale dated on the shown sale's day wins the tie. Blank = no sale on record. alt.xyz's flags are the only completion signal there is, and they lag: a settled sale is flagged NOT_PAID a median 8 days after the sale (all within 30) or RELISTED a median 14 days (90% within 31, all within 44), so a `completed` sale over 31 days old has had 93% of the flags it will ever get; 1.6% of settled sales flip at all, 10% of far-out sales on cards that trade often, none of 1,092 major-house auction lots above 6x (the flags are eBay's own states). What no column can say: a paid shill bid (a settled sale everywhere) or a completed sale of a different item. The `psa9_` pair is the same over the PSA 9 sales (blank when PSA 9 sales were not collected). `recent_sales/` does not change: its `outlier` row whose date and price equal `last_sale_*` is the ≈ one when `last_sale_single` is 1. (2026-10-18) |
| `last_sale_newer_price`, `last_sale_newer_date`, `psa9_last_sale_newer_price`, `psa9_last_sale_newer_date` | the newer, not-completed sale behind a `last_sale_status` of `pending` / `unpaid` / `relisted`: its price and date (the dearest when several share the newest day and state), so the row carries the pending sale and the app need not read it off the dropdown. Blank when the status is `completed` or blank. The `psa9_` pair is the same over the PSA 9 sales. Sid's 10-20 ask (2026-10-20). A not-completed sale the outlier filter would hold back on the LOW side had it completed is another item, not the card's newest sale: it is left out, and `last_sale_status` and this pair fall back to the sale before it (2026-10-22, Sid: Base Set Charizard showed an unpaid $169 against its $40,401 last sale; 3 rows that day). A far HIGH unpaid or relisted sale still counts: that is the shill-bid case the status is for. |
| `last_sale_title_check`, `psa9_last_sale_title_check` | Sid's 10-20 ask, "eBay title check on far-out sales": what eBay's own record of the sold item says about the sale in `last_sale_*` (`psa9_last_sale_*`). Whenever a card's newest sale is held back (`last_sale_unconfirmed` = 1) and is an eBay item, `history/sale_title_check.py` asks eBay for the item (the Buy Now verify's call and daily budget, ~400 a day for this) and judges its **title** against the card alt.xyz attached the sale to: `ok` = the title names this card at this grade by the watchlist sweep's strict rule (PSA and exactly this grade, every word of the subject, the number with its printed total, the printing, set evidence, no lot / autograph / other-language / reprint word); `mismatch` = the title contradicts the card (another grade; a number or printed total that is not this card's; 1st Edition / Shadowless in the title for a card that is not one or missing for a card that is; Reverse Holo, Gold Star, Lv.X, Prime or Shiny the card is not; a lot, autograph, jumbo, other language or reprint word; a year over a year off; or no word of the subject at all): alt.xyz attached another item's sale to this card, the mislabeled-lot case; `unclear` = too vague to judge either way (no grade or number in the title); `unavailable` = eBay no longer serves the item (sold items vanish after ~90 days). Blank = not an eBay sale, or not asked yet. A verdict is kept once given (a sold item's title never changes), so it stays on the sale after a second sale confirms it. A flag only: no price, count or other flag moves on it; the sale stays `last_sale_*` as alt.xyz lists it. Not a payment check: eBay never says whether the buyer paid (`last_sale_status` carries alt.xyz's unpaid / relisted flags for that). Verdicts with the title, bid count and unique bidders: `history/sale_title_checks.csv`. The last columns of the file (2026-10-20). |

`latest/series/<first two hex of asset_id>.csv` holds the weekly PSA 10 sale
series per asset (`week_start, n_sales, median_price, low, high`) for charts;
the server's `GET /api/samay-data/series?ids=…` reads one shard per lookup.
`latest/recent_sales/<xx>.csv` holds each card's last 10 PSA 10 sales, newest
first, including sales alt.xyz flags (`status` = ok / RELISTED / NOT_PAID /
PENDING …, or REMOVED when alt.xyz has since dropped the sale from the card outright, see
*Ingest* below; 2026-10-20) and sales the filter is holding back (`outlier` = 1), with the sale
URL — `GET /api/samay-data/recent-sales?ids=…` for the app's price dropdown. The eight columns never change (PokeSniper reads everything after the 7th comma as the URL); when `cards.csv` says `last_sale_single` = 1, the `outlier` = 1 row whose date and price equal `last_sale_*` is that single sale (2026-10-18).
`latest/recent_sales_psa9/<xx>.csv` is the same for PSA 9 sales: the same eight columns
(`asset_id, date, price, source, sale_type, status, outlier, url`), each card's last 10 PSA 9
sales newest first, only for cards whose PSA 9 sales have been collected (`psa9_scraped_date`),
with `outlier` judged by the PSA 9 run of the same filter. Each of the three shard directories
carries a `manifest.json` (2026-10-17) listing every shard with its `sha256`, `bytes` and
`rows`, in the shape of `clean_sales/manifest.json` below (`{"columns": [...], "shards":
{"00": {...}}}`, no timestamp): a reader can check a shard it downloaded, tell a failed or
stale fetch from an empty shard, and refetch only the shards whose hash moved. The manifests'
own hashes are in `summary.json` (`files`).

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

### Every PSA grade for the Gold Star category (2026-10-21)

Samay and Sid bought a Gold Star and want the whole category tracked at every grade, not only
the PSA 10 / PSA 9 the feed carries for every card. Each night (and on the weekly full run) the
scrape asks every **Gold Star** card at PSA 1, 1.5, 2, … 8.5, 9 and 10 and records its pop at
every grade. The category is the feed's own card-type rule (`history/flags.py` `card_type`, the
one `history/psa9_category_moves.csv` uses): a card dated 2008 or earlier named "… Gold Star …"
or "… Star #n" / "… Star Holo", every language — 161 records on 2026-10-20; the 2021
Celebrations / 25th-anniversary reprints are not the category. Since 2026-10-22 also any record
of 2008 or earlier at a Gold Star's own set number (`flags.GOLD_STAR_NUMBERS`, the closed list
of TCGplayer's 27 "… Star" products, e.g. POP Series 5 #16 / #17, Unseen Forces #113–115),
whatever alt.xyz calls it: Sid found POP Series 5 Espeon Star listed as "Pop Series 5 Espeon
#16". European printings keep those numbers; Japanese / Chinese / Korean ones do not and need
the name. That added 6 records (Espeon and five European ones with no PSA pop rows). `nightly/all_grades_scope.py`
picks them out of the index listing (the list travels as `snapshots/<day>/all_grades.txt`; the
few whose subject the species lists miss, e.g. the German "Glurak-Holo" record of the Dragon
Frontiers Charizard, are added to the nightly scope), `alt_scraper.py --all-grades-for` does the
pulls (one sale request per grade the pop table shows copies at, ~2,500 a night; `cards.csv`'s
last column `pops_by_grade`), the sales land in the store like any other, and:

- `history/grade_pops.csv` (committed, one row per card and night: `data_date`, `asset_id`,
  `grading_company`, `pop_0` (alt.xyz's "0.0" row: Authentic / altered), `pop_1`, `pop_1_5`, …
  `pop_9`, `pop_10`, `pop_total`, `scraped_at`) is the pop history at every grade. 0 = alt.xyz's
  pop table lists no copies at that grade; a card is absent on a night its scrape failed.
- `latest/all_grades/grades.csv` (`history/all_grades.py`, step 5b) has **one row per card and
  grade** (18 grades per card) for every card of the category (the same `card_type` rule over
  `history/assets.csv`, plus any card that ever had a `grade_pops.csv` row, less the records alt.xyz
  no longer indexes, by `cards.csv`'s 14-day rule, 2026-10-22): identity (`asset_id`,
  `card_name`, `year`, `set`, `card_number`, `subject`, `variety`), `grading_company`, `grade`
  (`1.0` … `10.0`), `pop`, `pop_date` (0 = alt.xyz's pop table lists no copies at the grade;
  **blank = the pop table has no PSA rows for the record at all**, 60 of the 161 on 2026-10-21,
  foreign-language and deck-reprint records: unknown, never zero), `pop_30d_ago`, `pop_chg_30d`
  (blank until there are 30 days of pops), `scraped_date` (the night the card was asked at every
  grade: a row with a `scraped_date` and no sale columns means collected, none sold; blank
  together with the pop, and the sale columns then show what the store holds for the card at that
  grade, fetched on the weekly full run), and the PSA 10 sale columns of `cards.csv` applied to that grade's sales
  with the same rules (`sales_total`, `last_sale_price/date/source`, `last_sale_status`,
  `last_sale_newer_price/date`, `last_sale_unconfirmed`, `last_sale_single`,
  `clean_last_sale_price`, `median_last_3`, `cap_price` and `cap_price_<w>_ago`, `volume_<w>`,
  `price_chg_<w>_pct` for 30d / 60d / 90d / 180d / 1y, `chg_held_windows`, `highest_sale`,
  `lowest_sale`, `sales_first_date`, `outliers_excluded`). Built with `history/metrics.py`'s own
  functions, so a figure here equals the one `cards.csv` would show for the same sales. Live
  listings are not asked at the lower grades (`cards.csv`'s PSA 10 / PSA 9 listing columns still
  cover those two).
- `latest/all_grades/sales.csv`: **every** sale alt.xyz lists for those cards at every grade,
  newest first within a card and grade: `asset_id`, `grade`, `date`, `price`, `source`,
  `sale_type`, `status` (`ok`, alt.xyz's `RELISTED` / `NOT_PAID` / `PENDING`, or `REMOVED`),
  `outlier` (1 = an `ok` sale the filter holds back), `url`. PWCC mirror copies dropped, like
  `recent_sales/`. The first run collects each grade's full history (alt.xyz returns every sale).
- `latest/all_grades/summary.json`: cards, grade rows, sales, the pops' date range,
  `generated_at`. Both CSVs are hashed into `latest/summary.json` `files`.

`NIGHTLY_ALL_GRADES=0` turns the step off; another category is one line in
`nightly/all_grades_scope.py` (`CARD_TYPES`). A 5-card live check on 2026-10-21: the Dragon
Frontiers Charizard Gold Star alone had 1,374 sales at 16 ladder grades (PSA 7: pop 573, 206
clean sales, last $20,000, +409% over a year), and a German record with no PSA 10 copies still
had 7 sales at PSA 1–8.

`latest/summary.json` is the sync check (PokeSniper's server fetches it hourly and downloads
the rest only when `generated_at` moved) and the run's counts:

| key | meaning |
|---|---|
| `data_date` | the newest daily file the feed was built from (the scrape day); "today" for every window column |
| `generated_at` | when `history/metrics.py` wrote the feed: UTC with offset (`+00:00`) since 2026-10-17, naive local time before (PokeSniper compares it as a string); the only key that moves on a rebuild from the same history |
| `last_run` | (2026-10-17) the newest nightly's account of itself, copied from its `run_stats.json` (cards asked / ok / failed / carried, listings checks failed, the Alt and Fanatics pulls, the gate verdict; see *What a run says about itself*). Absent until the first such nightly has landed |
| `daily_files`, `first_daily` | how many daily snapshots stand behind the pop history, and the first one (2026-09-11) |
| `assets`, `psa10_sales`, `psa9_sales`, `outliers_excluded` | rows in `cards.csv`; PSA 10 and PSA 9 sales in the store after alt.xyz's own flags (before the feed's outlier filter); PSA 10 sales the filter holds back |
| `series_rows`, `recent_sales_rows`, `psa9_recent_sales_rows`, `clean_sales_months`, `clean_sales` | rows in `series/`, `recent_sales/`, `recent_sales_psa9/`; months and sales in `clean_sales/` |
| `rows_*` | how many `cards.csv` rows have a value in the named column (`rows_listings_checked`, `rows_with_live_auction`, `rows_with_bin_listing`, `rows_bin_ebay_verified`, `rows_bin_suspect`, `rows_bin_first_seen_known`, `rows_next_auction_bid_suspect`, their `psa9_` twins, `rows_unconfirmed_last_sale`, `rows_with_psa9_last_sale`); `rows_chg_counts_held_high` and `rows_psa9_chg_counts_held_high` = rows whose `chg_held_windows` / `psa9_chg_held_windows` is set; `rows_single_last_sale` / `rows_psa9_single_last_sale` = rows with `last_sale_single` = 1, and `last_sale_status_counts` / `psa9_last_sale_status_counts` = `{completed, pending, unpaid, relisted}` row counts (2026-10-18) |
| `rows_with` | the same for `price_chg_30d_pct`, `price_chg_90d_pct`, `price_chg_1y_pct`, `mkt_cap_chg_30d_pct`, `psa9_price_chg_30d_pct`, `psa9_price_chg_1y_pct` |
| `files` | (2026-10-17) `{path: {"sha256", "bytes", "rows"}}` for `cards.csv`, `characters.csv`, `card_catalog.csv`, `tcgplayer_sets.csv`, `tcgplayer_ids.csv` and, once the watchlist sweep has produced it, `watchlist_listings.csv` (`rows` = data rows, the header not counted), plus `{"sha256", "bytes", "entries"}` for `series/manifest.json`, `recent_sales/manifest.json`, `recent_sales_psa9/manifest.json` and `clean_sales/manifest.json`. Written by `history/manifest.py`, the last step of every run that commits `latest/`. Why: raw GitHub caches each URL for ~5 minutes on its own clock, so a sync can read a new `summary.json` next to the previous run's `cards.csv` (or one old shard) and believe it is current until the next nightly; check each download's sha256 against this and fetch again on a mismatch. A `summary.json` without `files` (a hand rebuild with `metrics.py` alone) means "not checked", never "corrupt". |

`latest/characters.csv` (from `history/coverage.py`) has one row per name in
`nightly/subjects.txt` and `nightly/vintage_species.txt` (469 species): English
rows in the feed, how many have a PSA 10 pop / a real zero / an unknown pop / a
price, the alt-side market cap (Σ `pop_at_grade` × `cap_price` over every row,
all years and ≤ 2013), and for the ≤ 2013 rows the market cap change over
30d / 60d / 90d / 180d / 1y (`mkt_cap_chg_<w>_pct_le2013`, the Σ/Σ rule above,
with `mkt_cap_chg_<w>_coverage_le2013` = the share of the cap it covers),
computed with no matcher so characters can be ranked against each other
fairly. `rank_le2013` and `rank_all_years` (2026-10-17, the last two columns) are those
rankings written out: 1..469 by `alt_market_cap_le2013_usd` and by `alt_market_cap_usd`,
largest first, ties broken by name, every row ranked (a 0 cap sorts last). `rank_le2013` is
the roster rank the app's Sleeper / Arbitrage / Snipe gates read from a hand-pasted array
recomputed by script; read it from here instead, it is the same figure every night. Until
2026-09-28 the cap used the literal `last_sale_price`, unconfirmed
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
plus every TCGplayer English single no row is matched to (so a TCGplayer product can appear on
several `linked` rows: 6,724 are the Normal and Reverse Holo rows of one product, told apart by
`print_run`, and 1,276 are alt.xyz duplicate records with the same `print_run`, e.g. Expedition
Bulbasaur #94 as `0cd601e4` pop 206 and `edd1e397` pop 8, both Reverse Holo; counted 2026-10-20,
a `duplicate_of` marker is on the list), each with a `status` that says what
it is. The image of a row with a `tcgplayer_id` is
`https://tcgplayer-cdn.tcgplayer.com/product/<tcgplayer_id>_in_800x800.jpg`; no id, no image.

| column | meaning |
|---|---|
| `asset_id` | alt.xyz asset id (`cards.csv`); blank for a TCGplayer-only card |
| `tcgplayer_id` | TCGplayer product id; blank for an alt.xyz-only card |
| `status` | `linked` (the same pair as `tcgplayer_ids.csv`), `alt_only` (a real English card TCGplayer has no product for, or one the matcher won't guess between), `not_english` (alt.xyz files it as English but it isn't an English TCG card: Japanese and Chinese sets and promos, Topps movie cards, playing cards...), `tcgplayer_only` (a TCGplayer single alt.xyz has no row for) |
| `how` | `linked`: the `match` kind; `alt_only`: `no_candidate`, `variant_not_found` (a stamped / Cosmos / staff print TCGplayer doesn't list), `set_not_aligned`, `ambiguous_set`, `ambiguous_variant`; `not_english`: `not_on_tcgplayer` or `not_english_tcg`; `tcgplayer_only`: blank, `candidate_of_alt_row` when an unmatched alt.xyz row might be this card, or `unnumbered` (a single TCGplayer gives no number: My First Battle, V-UNION [Set of 4], unnumbered energies; never matched to an alt.xyz row; from 2026-10-05) |
| `name`, `number` | TCGplayer's for `linked` / `tcgplayer_only`; alt.xyz's subject and number otherwise |
| `set`, `set_source` | TCGplayer's set (`tcgplayer`); for an alt.xyz-only card the TCGplayer set its candidates are all in (`candidates`) or its alt.xyz set voted for (`voted`), else blank |
| `rarity` | TCGplayer's; blank for an alt.xyz-only card (unknown, not common) |
| `year` | alt.xyz's year for a card it has; else the set's year from `tcgplayer_sets.csv`, blank for a set spanning many years |
| `print_run` | `1st Edition`, `Shadowless`, `Reverse Holo` or blank, from alt.xyz's name |
| `printings` | every printing TCGplayer sells the product in, `\|`-separated, as TCGCSV's prices name them (`1st Edition Holofoil\|Unlimited Holofoil`, `Holofoil\|Reverse Holofoil`, `Normal`): the same list as PPT's `printingsAvailable`. Blank: no TCGplayer product, or TCGplayer lists no printing for it. Two sources, unioned: TCGCSV's prices (a printing only while it has price data) and, since 2026-10-08, TCGplayer's own SKU list per product (`history/tcgplayer_sku_printings.csv`, `history/tcgplayer_skus.py`), which also has print runs nobody sells raw (Neo Destiny 1st Edition Shining Mewtwo) and keeps a printing whose price data comes and goes. A product the SKU list hasn't reached yet (the newest sets, filled in over the next nights) has the TCGCSV list only (from 2026-10-05) |
| `card_type` | `Pokemon`, `Trainer` or `Energy`, from TCGplayer's "Card Type" (a Pokémon's energy type, or Supporter / Item / Stadium / Tool / Basic Energy...). Blank: no TCGplayer product, or TCGplayer gives no type that places it (from 2026-10-05) |
| `printings_complete` | `1` when TCGplayer's own SKU list for the product has been read (`history/tcgplayer_sku_printings.csv`, status ok), so `printings` is as full as it gets; `0` when the product has only TCGCSV's priced printings so far (the SKU list not fetched yet, or TCGplayer answered 404 for it), so the list can still grow and a card id keyed on it can still split or un-split; blank for a row with no TCGplayer product. On 2026-10-17 every row with a `tcgplayer_id` was `1` (the cache covers all 37,077); new products start at `0` and turn `1` within a few nights. The last column (2026-10-17) |

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

`latest/watchlist_listings.csv` (2026-10-15) is the **custom scrape for portfolio and
favorites**: every live listing, not only the cheapest, for each card and grade on
PokeSniper's `watchlist.csv` (favorites as PSA 10, holdings at their grade), one row per
listing: `asset_id, grade` (10 or 9, as the watchlist writes it), `source` (`eBay` from eBay's
own search; `Alt`, `Fanatics Collect`, `Goldin`, `CardHobby` from the feed's live listings;
`eBay (alt mirror)` for an eBay listing alt.xyz mirrors that eBay's search did not return),
`kind` (`auction` / `bin`), `price` (high bid or opening price for an auction, the ask for a
bin, USD), `bid_count`, `ends` (auction end, UTC), `url`, `title` (eBay's; blank for the feed's
own rows), `first_seen` (the first day the sweep saw that url), `checked_at` (this sweep, UTC), `verified`
(2026-10-17, the last column: `1` = eBay's own search returned the listing on this sweep, so it
was live at `checked_at`; `0` = a row copied from the feed's live-listings mirror, a snapshot
from that card's last alt.xyz check, which can be hours or days older than `checked_at` and,
for a Buy It Now, may have ended since; show those as "last seen", not "live").
Sorted by card, grade, kind, price. `latest/watchlist_listings.summary.json` next to it says
which card-grades were swept, which were not (budget, rate limit, unknown asset) and which
rows were skipped: a card with no row *and* in `swept` had nothing live; a card in
`not_swept` is unknown. Built by the daily eBay verify run when the watchlist secrets are set
(see *Watchlist sweep* below); absent until then.

### latest/buy_list.csv: the v0 "Claude buy" list (2026-10-21)

Sid's ask (2026-10-21): the app should say what to buy, like the leaderboard, as a pill and a
list ordered by most potential profit, no capital or price filter, with a reason, for PSA 10.
`history/buy_list.py` (step 7b of the nightly) writes one row per qualifying English PSA 10
card. **v0 is base-rate arithmetic, not a forecast model**: the score is the measured share of
trades like this card (same market regime, era, price tier and liquidity, 2021–2026 under the
all-in cost model: eBay fee, tax, shipping, auction premium, 40th-percentile exit) that netted
+30% or more within the horizon, moved by the two or three cuts the research found to hold
inside a month, and turned into an expected net gain with the cell's typical win and loss.
Everything the research found not to work is absent; what it found to lose is the avoid layer.
The list exists so a track record starts now (`history/buy_ledger.csv`, read back after three
and six months) and so the app has a file to build against while the model underneath is
replaced. Expect the live hit rate to come in below the base rates (the research's own rule of
thumb: half the backtest).

| column | meaning |
|---|---|
| `rank` | position by `exp_net_usd_180`, "most potential profit"; `rank_pct` the position by `exp_net_pct_180`. In a hot market most priced cards qualify, so a pill belongs on the top of either ranking (say `rank <= 100` or `rank_pct <= 100`), the app's call |
| `asset_id`, `card_name` | as in `cards.csv` |
| `ref_price`, `ref_basis` | the reference price the row is scored at: `median_last_3` (`med3`) |
| `p_win_180`, `p_low_180`, `p_high_180` | the share of trades in the card's cell that netted +30% or more within 180 days, moved by the cuts, with a 90% band; capped at 0.9. The band (v0.1, 2026-10-22) is the wider of the cell's Wilson interval and its month-clustered one (anchor months as clusters, Student t: one market moves a month's trades together, so a cell's real sample is its months, `cell_months`), moved the same way; under two months, 0–1. Until v0.1 it was Wilson alone and read ±4–5 points on cells that rest on three months |
| `exp_net_pct_180`, `exp_net_usd_180` | expected net gain after all costs: `p_win` × the median net of the cell's winners + (1 − `p_win`) × the mean net of its losers, as a % of `ref_price` and in dollars |
| `base_rate_180` | the cell's win rate before the cuts |
| `p_win_90`, `exp_net_pct_90`, `exp_net_usd_90`, `base_rate_90` | the same at 90 days |
| `cell` | `regime|era|tier|liquidity`: regime = tonight's market breadth (share of English cards ≥ $150 with ≥ 3 sales in the past year whose 30-day change is above +5%: flat < 25%, warm 25–40%, hot ≥ 40%); era wotc ≤ 2003 / vintage ≤ 2013 / modern ≤ 2019 / current; tier < $50, $50–150, $150–500, $500–2k, $2k+; liquidity = clean PSA 10 sales in 90 days 0 / 1 / 2–3 / 4–7 / 8+ |
| `cell_level`, `cell_n` | which table level the rates came from (the finest with ≥ 100 trades: `regime_era_tier_liq`, then without liquidity, then without era, then the regime alone) and its trade count |
| `regime`, `breadth` | tonight's regime and the breadth behind it; the regime is most of the forecast, so the list is short in a flat market |
| `cuts` | space-separated, the adjustments on: `stale30` (no clean sale in 30 days), `jp_heat` (the character's Japanese cards up ≥ 15% in 30 days), `band_high` (≥ 1 sd above the card's own 1-year band of weekly medians, reference ≥ $150), `thin_history` (v0.1: a card dated 2013 or earlier with no price a year back, `price_chg_1y_pct` blank, so how far it ran cannot be measured; measured against its own cell it won less in every regime, odds ratio 0.54–0.79, applied after the cap on the other cuts' product so stacked boosts cannot swallow it; the reason names the last price before the gap) |
| `avoid_flags` | warnings kept on the row, never a filter: `illiquid_exit` (≤ 2 sales in 180 days), `unconfirmed` (the newest sale is held back by the outlier filter), `category_falling` (v0.1: the card's category is Correcting by PokeSniper's own rule, `lib/categoryPhase.js` of 2026-10-19, over `recent_sales/`: its cards with ≥ 3 completed sales in 75 days, the newest within 30, together ≥ 15% below their own highs and ≥ 35% of them 15%+ below, over ≥ 10 such cards; a warning because the rates' months hold almost no correcting category, so nothing measures it). Cards with any of `release_year busy_spike vol_surge below_band dip chase_flat pending_sale title_mismatch` are not listed at all |
| `live_bin_price`, `live_bin_url` | the lowest live eBay Buy It Now when one is eBay-verified and not suspect, else blank |
| `reason` | the sentence for the row, in a fixed order: a correcting category first (v0.1), then the category leg (the set's chase cards' 90-day run; the character's other cards' 30-day change; Japanese copies), card leg (days without a sale, position vs own band, no price a year back with the last price before the gap, the live Buy It Now vs the level), the base rate of the cell in tonight's regime with its trades and months, then the other warnings |
| `model`, `data_date`, `scored_at` | `v0.1` (from 2026-10-22; `v0` before); the data date; the UTC time the row was scored (the same row is in the ledger) |
| `cell_months` | (v0.1, appended) the anchor months behind the 180-day cell: its real sample size. 3 on every hot-market row today |
| `category`, `category_off_peak_pct`, `category_breadth_pct` | (v0.1, appended) the category `category_falling` reads (the card type for Gold Star / LV.X / Neo Shining / e-card Crystal / HGSS Prime/LEGEND / EX-era ex, else the set), how far its cards sit below their own 75-day highs together (%) and the share of them 15%+ below; blank when fewer than 10 of its cards have a reading |

Rates: `history/buy_list_rates.csv` (cells, both horizons, every level) and `history/buy_list_lifts.csv`
(the cuts' lifts and odds ratios by horizon and regime) from `analysis/buy_list_rates.py` over the
loose panel (every card with a sale in the 180 days before a monthly anchor, sales through
2026-10-20). The hot-regime cells at 180 days rest on three closed months (Aug and Oct 2025, Mar
2026); the warm ones on twelve. **Re-fitted monthly (2026-10-23):** an anchor month closes at
horizon h once its exit window has passed (anchor ≤ data end − (h + 30) days), one a month per
horizon; `analysis/buy_list_refit.py` (workflow `buy-list-refit.yml`, the 2nd of each month)
first scores every month that closed since the current tables WITH those tables (the
out-of-sample check, by the nightly's own `score_cell`), then re-fits on every closed month.
`history/buy_list_rates_meta.json` = the tables' data end and closed months per horizon and
regime; `history/buy_list_refit_log.csv` = the check, one row per checked month, horizon, `tables`
(`at_anchor`: the tables of the month's own anchor date, what the list showed then, the honest
check; `current`: the tables in use, fitted on months whose exits overlap the checked month's, so
flattering), view
(`all` trades / `listed`, what the nightly would have listed) and group (`all`, `tier=…`,
`era=…`): trades, scored, mean predicted p, realized win rate (scored, and `win_rate_all` over
every trade), gap in points, Brier, the Brier of the regime's rate alone and the skill against
it, predicted and realized mean net. Every ledger row carries `rates_through` (appended last),
the data end of the tables that scored it. Each cell row carries `n_months`, `month_se` and `month_half90`
(v0.1: the band's month-clustered half-width); the lifts file a `vs` column (`own_cell` for
`thin_history`, measured against its own cells' rate; `rest` for the others, against the rest of
the regime). `history/buy_ledger.csv` keeps, per data date, the top 200 listed
cards by dollars and by percent and the top 200 `tail_lead` cards (bottom price quartile of a set
whose top quartile ran ≥ 30% in 90 days, reference ≥ $150: the one promising cell of the pump
autopsy, logged for a forward test, never scored), with the inputs behind each row; a rerun on
the same data date replaces that date's rows. Blank = unknown, never 0. Research and verdicts:
`analysis/README.md` (pump autopsy, reference price v2).

## Scheduled runs

`nightly/run_nightly.sh` does everything: full index listing (~65k cards, ~25 min),
scope filter, scrape with two retry passes, then `history/ingest.py` and
`history/metrics.py`. On GitHub Actions (since 2026-10-17) the same script runs in three jobs,
because a job may run 6 h at most and one job doing everything took 177-256 min a night and
332-338 min on Sundays: `scope` (index listing + scope, dealt round-robin into `NIGHTLY_SHARDS`
slices by `nightly/shards.py`), one `scrape` job per slice at the same time (`NIGHTLY_STAGE=scrape`,
`NIGHTLY_SHARD=<i>`; the 6 workers are split between them, 3 x 2 nightly and 2 x 3 weekly, so
alt.xyz sees the same request rate as before), and `assemble` (merge the slices' `cards.csv` /
`sales.csv` / `listings.csv`, Alt and Fanatics pulls, ingest, rebuild, commit). The files travel
between jobs as artifacts (`scope`, `shard-<i>`, then the usual `snapshot-<run id>`). Two
schedules, both in `.github/workflows/`:

- **nightly** (cron slots 03:17, 04:17 and 05:17 UTC; the first that fires runs, the others stop
  when `nightly/already_ran.sh` sees its commit, because GitHub fires a cron up to ~9 h late; the
  data is on main by ~07:00-09:00 UTC, before PokeSniper's morning sync; not before 03:00 UTC so
  Alt's Thursday auction block, which closes from 01:00 UTC, 02:00 in winter, is over): the 101 Pokemon in
  `nightly/subjects.txt` at every year (~20k
  cards: PokeSniper's 100 roster candidates plus Bulbasaur) plus the ~400 species in
  `nightly/vintage_species.txt` at 2013 or earlier (~13k cards, PokeSniper's Categories
  checklist), ~33k cards, ~2–3 h. Names match `subject` as whole words, with a hyphen
  matching a hyphen or a space ("Ho-Oh" also finds alt.xyz's "Ho Oh").
- **weekly full** (Sunday): every graded Pokemon card (~65k), so the vintage species'
  modern printings and every other species stay fresh too.
- **buy list refit** (monthly, the 2nd at 15:40 UTC, the 3rd as a retry slot that stops once the
  month's commit is on main): `nightly/run_buy_list_refit.sh` pulls the store, builds the loose
  panel (~3 min, ~4.6 GB), checks the newly closed months against the current rate tables,
  re-fits, and commits only `history/buy_list_rates.csv`, `buy_list_lifts.csv`,
  `buy_list_rates_meta.json` and `buy_list_refit_log.csv` (commit `buy list refit <date>: …`);
  nothing when no month closed. Reports as a 90-day artifact. The nightly reads the new tables
  the next night. Needs pandas / numpy / scipy, pinned in the workflow.

Each run ends with `history/manifest.py` (the sha256 of every `latest/` file into
`summary.json`'s `files`, plus the shard manifests), pushes `history/sales/` + `history/daily/`
to the release as the store's next generation, and commits `latest/` and the rest of `history/`.
Run by hand:

```bash
python3 history/store.py pull                             # history/sales + daily from the release (first, on a fresh checkout; ~430 MB)
nightly/run_nightly.sh                                    # nightly scope, ~2–3 h (pulls the store itself)
NIGHTLY_SCOPE=full nightly/run_nightly.sh                 # everything, ~4 h
NIGHTLY_SHARDS=3 NIGHTLY_SCOPE=full nightly/run_nightly.sh   # the same in 3 slices one after another (the Actions layout on one machine)
NIGHTLY_LIMIT=15 NIGHTLY_DATE=smoke nightly/run_nightly.sh   # 15-card smoke test
python3 history/ingest.py snapshots/2026-09-12            # (re)ingest one run
python3 history/metrics.py                                # rebuild latest/ only
python3 history/coverage.py                               # rebuild latest/characters.csv only
python3 history/flags.py                                  # append tonight's PSA 9 buy-condition flags to history/
python3 history/tcgplayer_ids.py --fetch                  # TCGplayer catalog -> latest/tcgplayer_ids.csv, card_catalog.csv, tcgplayer_sets.csv (~4 min)
python3 history/manifest.py                               # last: summary.json "files" + series/recent_sales*/manifest.json (1 s)
nightly/commit_results.sh                                 # store push + commit + push after a laptop run
python3 history/store.py status                           # which generation the release and this checkout hold
```

### What a run says about itself (2026-10-17)

Every scrape leaves `snapshots/<day>/run_stats.json`, its own account: cards asked / scraped ok /
failed / carried by `--skip-unchanged-sales`, live-listings checks asked / failed at PSA 10 and at
PSA 9 (added up over the retry passes), whether the Alt and Fanatics bulk pulls succeeded and how many
of the Fanatics request budget they used, start and end in UTC (`alt_scraper.py` writes the step
sections and exits 2 when cards failed, which is what the retry passes test now instead of grepping
the log; `nightly/run_stats.py` writes the rest). On Actions each scrape job leaves its shard's
account in `shard-<i>/run_stats.json` and the assemble job adds them up (`nightly/run_stats.py
merge`; a shard whose job died is accounted from its files, its missing cards as failed). Before the
pulls and the ingest, `nightly/run_stats.py gate` judges the whole night: fewer than
`NIGHTLY_MIN_SCRAPED_PCT` (90) % of the scope
scraped, or more than `NIGHTLY_MAX_LISTINGS_FAILED_PCT` (20) % of the listings checks failed at either
grade, and the run stops with exit 1, so nothing is ingested or committed and the snapshot stays as
the workflow's artifact (until then a half-finished night was committed as the day's data with one
log line to show for it). `history/ingest.py` copies the file to `history/last_run.json` and
`history/metrics.py` publishes it in `latest/summary.json` as `last_run` (absent until the first
such nightly), so PokeSniper's status can show what the newest nightly asked and got; the commit
message carries the same counts after the usual `nightly <date>: N assets, M sales`. A workflow step
after the commit, `nightly/run_stats.py fanatics-health`, goes red once the direct Fanatics pull has
failed `NIGHTLY_FANATICS_MAX_FAILED_NIGHTS` (3) nights running, or has never succeeded: the night's
data is committed either way, but the direct Fanatics rows age out of `history/live_listings.csv`
after 7 days without a pull, so this shows first. From the same day `summary.json`'s `generated_at`
and every row's `scraped_at` are UTC with offset (`+00:00`); before that both were naive local time.

`history/psa9_flags.csv` and `history/psa9_category_moves.csv` (from 2026-09-27, step 7 of the
nightly, append-only) log Sid's PSA 9 buy conditions forward so they can be checked 3, 6 and
12 months later: for every card of a top-50 Pokemon (the app's roster rank: alt-side market cap of cards up to
2013, `alt_market_cap_le2013_usd` in `latest/characters.csv`) whose PSA 10 is up 50%+ over 30 days, or whose card type is running
(group median 30-day change 30%+), or that is listed in `nightly/track_assets.txt`, one row a
night with that night's PSA 10 / PSA 9 prices, pops, tier and the flags that held
(`buy_rule_50` / `buy_rule_100` = top-50 card, PSA 10 up 50%+ / 100%+, PSA 9 up less than
10%); plus one row per card type a night with the category's median move. The rule and the
backtests behind it are in `analysis/README.md` ("Sid's buy rule").

**The store** (2026-10-17): `history/sales/*.csv` and `history/daily/*.csv` are not in git any more.
`history/store.py` keeps them as one gzip tarball per generation on this repo's release tagged
`store` (`store.json` there names the current one; the newest three generations and the first of
each month are kept). A run `pull`s the current generation before reading it (the assemble stage
before ingest; a nightly scrape shard too, for the previous daily file its skip-unchanged rule
reads; the laptop path once up front) and `push`es the next one before committing, so `latest/`
is always built from the generation the release holds; a push that finds the release moved
meanwhile (a laptop run) stops the run with nothing committed. `STORE_MODE=local` runs offline
on whatever the folder holds. Why, the alternatives and the numbers:
`handoff/2026-10-17-store-move-design.md`. The `analysis/` scripts read `history/sales/` in
place, so run `python3 history/store.py pull` once on a fresh checkout.

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
`sales_total` on ~3,300 cards) and status flips never arrived. A stored sale of a card
whose complete sale list the run fetched (`sales_fetched` 1, not a `--max-sales` run) that
the run no longer lists at all is kept with `skipped_reason` **REMOVED** (2026-10-20):
alt.xyz dropped the record outright, which until then left the card showing an older
price with "held back" (on 2026-10-16, 18 cards; e.g. a $9,852 Bandai Flareon sale alt.xyz
removed on 10-13 while the store still held it as settled). A REMOVED sale is out of every
number like NOT_PAID, is not a listed sale for `last_sale_status`, appears in `recent_sales/`
with that status, and comes back as alt.xyz's version if a later run lists it again. The run
must list at least one sale at that grade (2026-10-21): an empty list marks nothing, because
the scraper vouches for a grade whose pop table says zero copies without fetching it (379
cards on 2026-10-20, three of them holding 152 clean PSA 9 sales on split records) and an
empty answer for a card that had sales the day before is as often a record change or a
lagging replica as a removal; such a list lifts a REMOVED mark at that grade instead.

Laptop fallback: `nightly/com.samaygodika.altscrape.plist` runs the same script at
02:00 via launchd (a closed lid still sleeps; launchd resumes on wake). The
2026-09-12 run took 33 h that way, which is why the GitHub Actions cron is the
primary schedule.

Env knobs: `NIGHTLY_SCOPE` (top60 = the subjects lists | full), `NIGHTLY_ALSO_GRADE` (9 on the
nightly scope, empty on full), `NIGHTLY_ALL_GRADES` (1: the Gold Star category at every PSA
grade on both scopes, see *Every PSA grade for the Gold Star category*; 0 turns it off),
`NIGHTLY_ALSO_LISTINGS` (PSA 9 live listings too, needs an
also-grade; on by default since 2026-09-26, 0 turns it off), `NIGHTLY_WORKERS` (4; the Actions
workflows use 6 in total, split over the scrape jobs), `NIGHTLY_DELAY` (0.25 s per worker; Actions
uses 0.05), `NIGHTLY_LIMIT`, `NIGHTLY_DATE`, `NIGHTLY_SKIP_HISTORY=1`, `NIGHTLY_SKIP_UNCHANGED=1`,
`NIGHTLY_STAGE` (`all` | `scope` | `scrape` | `assemble`: the Actions jobs above; `all` on a laptop),
`NIGHTLY_SHARDS` (1; slices the scope is dealt into) and `NIGHTLY_SHARD` (the slice a `scrape` stage
takes). All stages of one run must share a `NIGHTLY_DATE`.

`NIGHTLY_SKIP_UNCHANGED=1` (off by default on a laptop, on in the nightly workflow since 2026-10-17,
never on the weekly full run) skips the two sale
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
`--all-grades-for FILE` (2026-10-21) asks the cards listed in FILE at every PSA grade from 1 to 10
(half grades too): their sales at each grade the pop table shows copies at go to `sales.csv`, and
their pop at every grade to `cards.csv`'s last column `pops_by_grade` (`0.0:30 1.0:912 ... 10.0:98`;
blank for every other card). Live listings stay at PSA 10 and the `--also-grade` grades.
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

### Listings refresh between nightlies

Since 2026-10-03 `.github/workflows/listings-refresh.yml` runs `nightly/run_listings_refresh.sh`
three times a day (10:00, 16:00, 22:00 UTC; the nightly covers the night; since 2026-10-17 each slot
has a retry at :40 and `nightly/already_ran.sh` skips a slot when a refresh landed within the last 5 h,
because GitHub keeps one pending run per concurrency group and a refresh queued behind a scrape used to be
cancelled by the next queued run). It re-checks the
live listings of every card `latest/cards.csv` shows with a running auction, at the grade(s)
that have one (`nightly/refresh_scope.py`, ~4k PSA 10 + ~3k PSA 9 cards, one request each),
with `alt_scraper.py --listings-only` (no pops, no sales) and `history/ingest.py --listings-only`
(live_listings.csv rows replaced as usual, the card's check time moved forward in its newest
daily row, nothing else in `history/daily/` touched), then rebuilds `latest/` and commits. So
an auction that ended or was pulled early disappears from the pills within hours instead of a
day, and `listings_checked_at` / `psa9_listings_checked_at` can be later than `scraped_at`. Buy
It Nows are not re-checked here: alt.xyz re-serves ended ones anyway (see `lowest_bin_first_seen`);
eBay itself is asked about them once a day (see *eBay verification of Buy It Nows* below).

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

### eBay verification of Buy It Nows

alt.xyz re-serves ended eBay Buy It Nows for months, with no date or status on the record, so
since 2026-10-06 `.github/workflows/ebay-verify.yml` asks eBay itself once a day (07:15 UTC,
after eBay's quota reset; a 09:15 slot retries only if that run was cancelled while queued):
`nightly/run_ebay_verify.sh` runs `history/ebay_verify.py`, one
Browse API lookup per listing (`getItemByLegacyId`, application token), about 4,800 of the
keyset's 5,000 a day. Cards from 2013 or before first, oldest listings first; a listing eBay
called live is asked again after 7 days. A listing eBay says has ended (a past `itemEndDate`,
or 404 "not found") goes into `history/ebay_checks.csv` for good, is dropped from
`history/live_listings.csv`, and `history/ingest.py` skips it whenever alt.xyz serves it again,
so the card shows its next cheapest live BIN instead. Auctions are left to the listings
refresh. Keys: repository secrets `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` (App ID / Cert ID of
a production keyset; a new keyset stays disabled until the Marketplace Account Deletion
opt-out is done in eBay's developer portal), or `~/.ebay_keys` locally. Without keys the
step does nothing. The check and the apply write `snapshots/ebay/<stamp>/ebay_verify.json` (their
counts and the one-line summary the commit message uses), and the watchlist sweep's summary JSON
carries a `note` line; since 2026-10-17 the shell reads those instead of grepping the tee'd log,
which under `set -e` killed the script before the commit whenever the line was not in the tail window.


### Watchlist sweep (custom scrape for portfolio and favorites, 2026-10-15)

The live columns above come from alt.xyz's mirror of other sites, and the mirror is partial
(2026-10-14: Palkia G LV.X PSA 9 showed one eBay Buy It Now at $4,300 while eBay also had
$3,299 and $4,097 copies; Plasma Storm Articuno EX PSA 10's one eBay listing was never
mirrored). For the ~74 card-grades PokeSniper's users favorite or hold, `history/watchlist_listings.py`
asks eBay's own search once a day and writes `latest/watchlist_listings.csv` (columns under
*The data PokeSniper gets*). Per card and grade: two queries, "<subject> <number> PSA <grade>"
and "<subject> <set> PSA <grade>" (eBay titles follow the PSA label, and each query finds
listings the other misses), each run for fixed-price and for auction listings separately
(an unfiltered search left out 12 of 13 running auctions), so 4 calls a card, ~306 a day,
capped by `WATCHLIST_EBAY_BUDGET` (360); `nightly/run_ebay_verify.sh` runs the sweep first and
gives the verify the rest of `EBAY_BUDGET`. A hit counts only when its title names PSA and
exactly the wanted grade, every word of the card's subject, the number as a token (with the
printed total when given), the printing (1st Edition / Reverse Holo / Shadowless present or
absent as the card has it), some set evidence (number with total, promo prefix, Gold Star /
LV.X / Prime / Shiny, or a set word), no other language, reprint (Celebrations, 25th
anniversary, World Championships deck), lot, autograph, error or sealed product, and no year
more than one off the card's. Anything less is refused, never guessed; what alt.xyz already
mirrors for the card (Alt's own listings, Fanatics Collect, Goldin, CardHobby, and eBay rows
the search did not return) is added from `history/live_listings.csv`. Fanatics Collect is
read through alt.xyz's mirror here; a direct pull plugs in as another source once that runs.

Input: PokeSniper's `watchlist.csv` (`asset_id, grade, kind, profiles`), read from
`WATCHLIST_FILE` (a local path) or `WATCHLIST_URL` with `WATCHLIST_TOKEN` as a bearer token
(a GitHub contents-API URL returns the raw file; nothing else of that repository is read).
**Off in Actions until the repository secrets `WATCHLIST_URL` and `WATCHLIST_TOKEN` exist**
(already wired in `.github/workflows/ebay-verify.yml`): either
`https://api.github.com/repos/<owner>/<repo>/contents/watchlist.csv` plus a classic personal
access token with the `repo` scope (a fine-grained token cannot reach another user's private
repository), or a token-protected URL served by PokeSniper's own server plus its token.
Locally: `WATCHLIST_FILE=watchlist.csv python3 history/watchlist_listings.py` (keys from
`~/.ebay_keys`), or `--log hits.tsv` to see every eBay hit with its accept/refuse reason.

### Fanatics Collect Buy Nows, read directly (2026-10-15)

alt.xyz mirrors only about one Fanatics Collect Buy Now in six for the cards the feed covers, and keeps
serving ones that have sold. Since 2026-10-15 the nightly also reads Fanatics Collect's own Buy Now
marketplace, with their written permission and on the terms asked for in
`handoff/2026-10-11-fanatics-permission-request.md`: the public search the marketplace page itself makes,
Pokémon cards graded PSA 9 or PSA 10 only, once a day, ~45 requests at >= 1 s apart under this
project's User-Agent, keeping only listing id, title, asking price, grade, grader and the link
(`alt_scraper.py --fanatics-listings`, budget `NIGHTLY_FANATICS_BUDGET`; empty = off). Each listing is
matched to a feed card by `history/fanatics_match.py` on year + card number + the words of alt.xyz's
subject, print run and set, refusing anything ambiguous (the verdict and candidates are in the run's
`fanatics_listings.csv`). In `history/live_listings.csv` the matched listings are the cheapest Fanatics Buy
Now per card and grade (`via` = `fanatics`), replaced whole by each pull and counted in `lowest_bin_*` like
any other source (source stays `Fanatics Collect`). The pull also settles alt.xyz's mirrored Fanatics rows: a
listing not in it (Sold, withdrawn, or not the Pokémon card alt.xyz said; the 236 "crossed" ids of
2026-10-10 that pointed at sports cards among them) is dropped and recorded in `history/fanatics_checks.csv`,
and `history/ingest.py` skips it when alt.xyz serves it again; a live listing the matcher could not place
keeps alt.xyz's card with Fanatics' current price. Cards never checked per-card at a grade still show
their direct Fanatics Buy Now (the pull's time is the check time, as for Alt's own listings).

The pull reads one page of 1,000 per slice of grade x language x years and splits a slice at its median
price when the page is full or the index cut the scan short. Since 2026-10-17 the slicing is adaptive:
the facet request that sizes a grade and language has exact year counts when the index scanned everything
(`exhaustive`), so the years are grouped to ~900 real listings per slice (one short page each, no split), a
language that fits a page is read whole with no year filter and small ones of a grade are merged into one
page, an empty one costs nothing, and only a facet answer the engine cut short (asked once more first)
falls back to the old cap on estimates. The first pull under the old rule (2026-10-17) used 71 of its 80
requests (22 slices, 19 retries, 8 split pages); the permission allows ~60-80 a day, so the budget stays
at 80 and `run_stats.json` records requests used vs budget every night, with the pull's success or failure
(see *What a run says about itself*).

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
