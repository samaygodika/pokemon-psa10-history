# Runbook: keeping the feed running

What runs, how to tell it's healthy, and what to do when it isn't. Written 2026-11-17 for whoever
is on call (Samay, a helper, or a Claude session). The README explains how everything works;
this page only covers operating it. This repository is public, so no secret values belong here
or anywhere else in it.

## What runs, and when (all UTC, all GitHub Actions)

| Workflow | Schedule | What it does | Normal result |
|---|---|---|---|
| nightly scrape | 03:17, retry slots 04:17 and 05:17 | Scrapes ~35k cards from alt.xyz, pulls Alt and Fanatics listings, rebuilds `latest/` and commits it | Commit `nightly <date>: …`. GitHub starts it hours late, so it usually lands between 12:00 and 15:00 |
| listings refresh | 10:00, 16:00, 22:00 (+:40 retry) | Re-checks cards with a running auction | Commit `listings refresh …` |
| eBay verify | 07:15 (09:15 retry) | Asks eBay whether each Buy It Now is still live, sweeps Sid's watchlist and flagged sales | Commit `ebay verify …` |
| weekly scrape | Sunday 09:00 (13:00 retry) | Every graded Pokémon card (~70k) | Commit, ~3.5–4 h |
| buy list refit | 2nd of the month 15:40 (3rd retry) | Re-fits the buy-list rate tables | Commit only when a month closed |
| **feed health** | 22:30 | Checks the published feed itself (below) | Green run, no commit |

The nightly, weekly and listings refresh share one queue (`concurrency: scrape`), so a late
nightly delays the others. That's expected. A retry slot that finds the day's commit already on
`main` stops on its own (`nightly/already_ran.sh`).

Who reads the output: PokeSniper's server polls
`https://raw.githubusercontent.com/samaygodika/pokemon-psa10-history/main/latest/summary.json`
hourly. When `generated_at` changes it downloads the files, checks each against the sha256 in
`summary.json`, and keeps its old copy of any file that doesn't match. It also reads
`history/grade_pops.csv`. Anything that changes file names, columns or paths under `latest/`
needs a note to Sid on PokeSniper PR #1 first.

## How to tell it's healthy

1. **The feed health run is green** (Actions → "feed health"). It runs `nightly/feed_health.py`,
   which fails when:
   - the last nightly finished more than 30 h ago, so a night was missed;
   - any file under `latest/`, including every shard, doesn't match `summary.json` on bytes,
     sha256 or row count;
   - the card count or a sales count fell more than 1% since the previous nightly;
   - `history/grade_pops.csv` is missing.

   It only warns, without failing, on slower trouble: no feed update for 14 h, the quality gate
   refused a nightly, Fanatics failing 2+ nights in a row, a big fall in Buy It Now rows.
2. **When it fails** it opens one issue titled "Feed health check failed" with the report (or
   comments on the open one), and GitHub emails whoever last edited its cron line. The next green
   run closes the issue. To be told too, watch this repo's issues.
3. Run it yourself any time, from a checkout of `main`:

   ```bash
   python3 nightly/feed_health.py --root latest
   ```

   Or start it on GitHub: Actions → feed health → Run workflow.

## When something breaks

Start with the failed run's log (Actions → the run → the red step). Most failures fix themselves
on the next slot. Act when the same job fails twice in a row, or feed health goes red.

| Symptom | Likely cause | What to do |
|---|---|---|
| Nightly red at the scrape, HTTP 403 from alt.xyz | alt.xyz refusing scripted requests (happened 2026-10-02) | See README "Why a 403". Check the user-agent still identifies the project honestly. Try one card by hand (`python3 alt_scraper.py --find "charizard"`). If it's the login, renew the token (below) |
| Nightly red at "3c/8 gate", no commit | Too many cards failed to scrape (gate: ≥90% scraped, ≤20% listing checks failed). The gate stops the run so a bad night never replaces a good feed | Usually alt.xyz being slow. Re-run the nightly (Actions → Run workflow). If it repeats, look at which requests fail in `run.log` |
| Feed health: "the last nightly finished … h ago" | The nightly never ran, was cancelled in the queue, or failed | Check the nightly's runs for the day. If none ran, start one by hand. GitHub turns off cron schedules in repos with no activity for 60 days, but the daily commits prevent that |
| Feed health: a file "doesn't match the feed" | A job changed a file after `history/manifest.py` wrote the hashes, or a bad merge in a push retry | Re-run `python3 history/manifest.py` on a checkout of `main` and commit, or re-run the last job. Sid's server keeps the old copy until it matches, so nothing is lost, only stale |
| Feed health: a count "fell" | A partial scrape got through, or a real removal | Compare that nightly's `summary.json` with the night before (`last_run` block). If it's a partial scrape, re-run the nightly |
| Fanatics warning, 2+ nights | The pull used up its 80-request budget on retries | The feed keeps the last good Fanatics rows, so prices are only stale. See README "Fanatics Collect Buy Nows" |
| eBay verify red with 401/403 | eBay keyset revoked or changed | Renew the keyset on developer.ebay.com and update `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` |
| eBay verify: watchlist "0 swept" | Sid's watchlist link or token stopped working | Ask Sid. The token is his. Check the expiry date of the GitHub token behind `WATCHLIST_TOKEN` |
| Push rejected / "non-fast-forward" in a log | Two jobs committed at once | The scripts rebase and retry by themselves. Only act if the job ends red |
| Store errors (`history/store.py`) | The `store` release or its assets were edited by hand | `python3 history/store.py status` shows which generation the release holds |

## Secrets and things that expire

Repository secrets (Settings → Secrets and variables → Actions). Only the repo owner can see or
set them. Nobody else needs the values.

| Secret | Used by | Expires / renew |
|---|---|---|
| `ALT_SESSION_TOKEN` | nightly, weekly, listings refresh | alt.xyz login session, valid until 2027-10-02. Optional today: the scrape works logged out. Renew per README "Login" |
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | eBay verify | eBay production keyset. Doesn't expire, but eBay can revoke it. 5,000 lookups a day, resetting at 07:00 UTC |
| `WATCHLIST_URL`, `WATCHLIST_TOKEN` | eBay verify | Sid's watchlist file and his GitHub token. Classic tokens can carry an expiry date, so ask Sid |
| `FLAGGED_SALES_URL`, `FLAGGED_SALES_TOKEN` | eBay verify | Optional overrides. By default flagged sales use the watchlist pair |

Also outside this repo: alt.xyz's permission to scrape (get it in writing), and the `store`
release, which holds the full sales history (`history/sales`, `history/daily`). Don't delete
that release or its assets.

## Before changing anything

- Run the tests: `for t in history/test_*.py; do python3 "$t" || echo "FAILED $t"; done`.
- Anything Sid's server reads (paths, columns, `summary.json` keys): tell Sid on PokeSniper PR #1
  before it ships.
- Never filter or rank cards out by population. Blank means unknown, 0 means a real zero.
