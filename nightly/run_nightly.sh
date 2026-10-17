#!/bin/bash
# Scheduled alt.xyz scrape -> history store -> latest/ (what PokeSniper reads).
#
#   nightly/run_nightly.sh              # PokeSniper's roster candidates (nightly/subjects.txt) at every year, plus the
#                                        # vintage-checklist species (nightly/vintage_species.txt) at <= 2013: ~33k cards
#   NIGHTLY_SCOPE=full nightly/run_nightly.sh   # every graded Pokemon card (~65k), for the weekly run
#
# Steps:
#   1. index listing  -> snapshots/<date>/all_pokemon_cards.{txt,json}   (~260 requests, ~25 min)
#   2. scope filter   -> snapshots/<date>/scope.txt (+ .json sidecar)
#                        (+ shard-<i>/scope.{txt,json} when NIGHTLY_SHARDS > 1, nightly/shards.py)
#   3. scrape         -> snapshots/<date>/cards.csv, sales.csv, listings.csv, run.log, run_stats.json
#                        (retries failed cards twice: the scraper exits 2 when cards failed)
#                        (sharded: each shard into shard-<i>/, merged back into the run folder by nightly/shards.py;
#                        the shards' run_stats.json by nightly/run_stats.py merge)
#   3b. gate          -> nightly/run_stats.py gate: STOP (no pulls, no ingest, no commit) when fewer than
#                        NIGHTLY_MIN_SCRAPED_PCT (90) % of the scope came back or more than
#                        NIGHTLY_MAX_LISTINGS_FAILED_PCT (20) % of the live-listings checks failed (2026-10-17)
#   3c. Alt + Fanatics -> alt_listings.csv, fanatics_listings.csv (bulk pulls; a failed pull keeps the last good rows)
#   4. ingest         -> history/assets.csv, history/daily/<date>.csv, history/sales/<month>.csv, history/live_listings.csv,
#                        history/last_run.json (the run's run_stats.json: what was asked, what came back, what failed)
#   5. metrics        -> latest/cards.csv, latest/series/*.csv, latest/recent_sales{,_psa9}/*.csv, latest/summary.json
#   6. coverage       -> latest/characters.csv (per-character alt-side market cap and coverage)
#   7. flags          -> history/psa9_flags.csv, history/psa9_category_moves.csv (Sid's PSA 9 buy conditions, logged forward)
#   8. tcgplayer ids  -> latest/tcgplayer_ids.csv (each English card's TCGplayer product id + image URL, from
#                        tcgcsv.com's free copy of TCGplayer's catalog; PokeSniper's images without PPT),
#                        latest/card_catalog.csv (PokeSniper's card list) and latest/tcgplayer_sets.csv
#
# Stages (NIGHTLY_STAGE, 2026-10-17): on a laptop the default `all` runs every step in this one process, as
# always. GitHub Actions runs the same script three times in separate jobs so that no single job carries
# the whole night (a job may run 6 h at most; the weekly full run sat at 332-338 min of a 350 min timeout
# in October 2026): `scope` does steps 1-2 and deals the scope into NIGHTLY_SHARDS shards, `scrape` with
# NIGHTLY_SHARD=<i> does step 3 for one shard (N such jobs run at once, each with a share of the workers:
# alt.xyz sees the same total request rate as one 6-worker job), and `assemble` merges the shards' files
# into the run folder and does steps 4-8 (the artifacts carry the files between jobs). All stages share
# snapshots/<DAY>; pass the same NIGHTLY_DATE to every job. NIGHTLY_SHARDS > 1 with the default stage
# scrapes the shards one after another on one machine and merges them, so the laptop can mirror the
# Actions layout.
#
# Only history/ and latest/ are committed; snapshots/ is raw and gitignored.
# Every dated snapshot is kept locally on purpose (cheap, and lets a bad ingest
# be redone), prune old ones by hand if disk gets tight.
#
# Env knobs: NIGHTLY_FANATICS_BUDGET (max requests for the direct Fanatics Collect Buy Now pull; empty =
# off), NIGHTLY_MIN_SCRAPED_PCT (90) and NIGHTLY_MAX_LISTINGS_FAILED_PCT (20) (the gate, see
# nightly/run_stats.py), NIGHTLY_SCOPE (top60 = the subjects lists | full), NIGHTLY_ALSO_GRADE (9 on the nightly scope,
# empty on full; "" turns it off), NIGHTLY_ALSO_LISTINGS (1 = also fetch the live listings at the
# --also-grade grade, i.e. PSA 9 listings; default empty = off, and never on the full run, which
# has no --also-grade), NIGHTLY_WORKERS (4), NIGHTLY_DELAY
# (0.25 s per worker between requests), NIGHTLY_LIMIT (scrape only the first N
# cards, for smoke tests), NIGHTLY_DATE (override the folder/day name),
# NIGHTLY_SKIP_HISTORY=1 (stop after the scrape), NIGHTLY_STAGE (all | scope | scrape | assemble, above),
# NIGHTLY_SHARDS (1; how many slices the scope is dealt into), NIGHTLY_SHARD (which slice a `scrape`
# stage takes, 0-based), NIGHTLY_SKIP_UNCHANGED=1
# (default off, ignored on the full scope: don't refetch the sales of cards whose
# sales cannot have changed since the previous run — the newest history/daily/*.csv
# dated before DAY — and carry that run's sale summary instead; the scraper's
# --skip-unchanged-sales, rules and measurements in sales_skip_reason there. The
# 2026-09-22..25 nightlies: ~14.5k of 33.7k cards qualify, ~27k of ~134k requests,
# ~38 min of a 3 h scrape, 0 of 10,886 new PSA 10 sales missed. Sunday's full run
# refetches everything, so nothing is carried for more than a week. On in the
# nightly workflow since 2026-10-17).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
DAY=${NIGHTLY_DATE:-$(date +%F)}
SCOPE=${NIGHTLY_SCOPE:-top60}
STAGE=${NIGHTLY_STAGE:-all}
SHARDS=${NIGHTLY_SHARDS:-1}
SHARD=${NIGHTLY_SHARD:-}
case "$STAGE" in all|scope|scrape|assemble) ;; *) echo "NIGHTLY_STAGE must be all, scope, scrape or assemble (got '$STAGE')" >&2; exit 2;; esac
if [ "$STAGE" = "scrape" ] && [ "$SHARDS" -gt 1 ] && [ -z "$SHARD" ]; then echo "NIGHTLY_STAGE=scrape with $SHARDS shards needs NIGHTLY_SHARD=<0..$((SHARDS - 1))>" >&2; exit 2; fi
OUT="snapshots/$DAY"
if [ "$SCOPE" = "full" ]; then OUT="snapshots/$DAY-full"; fi
mkdir -p "$OUT"
printf 'OUT=%s\nDAY=%s\n' "$OUT" "$DAY" > snapshots/last_run.env   # for nightly/commit_results.sh
# A shard job logs into its own folder (uploaded with its files); everything else into the run's log.
if [ "$STAGE" = "scrape" ] && [ "$SHARDS" -gt 1 ]; then mkdir -p "$OUT/shard-$SHARD"; LOG="$OUT/shard-$SHARD/run.log"; else LOG="$OUT/run.log"; fi
exec > >(tee -a "$LOG") 2>&1
echo "=== $SCOPE scrape $DAY${STAGE:+ (stage $STAGE${SHARD:+, shard $SHARD of $SHARDS})} started $(date '+%F %T') ==="

# The list the (unsharded) scrape reads, from the scope stage's output; a function because the
# `scrape` stage runs in another process than the `scope` stage.
scope_list() {
  if [ "$SHARDS" -gt 1 ]; then echo "$OUT/shard-$SHARD/scope.txt"
  elif [ "$SCOPE" = "full" ] && [ -z "${NIGHTLY_LIMIT:-}" ]; then echo "$OUT/all_pokemon_cards.txt"
  else echo "$OUT/scope.txt"; fi
}
scrape_out() { if [ "$SHARDS" -gt 1 ]; then echo "$OUT/shard-$SHARD"; else echo "$OUT"; fi; }

stage_scope() {
  # -> $OUT/run_stats.json: the run's account of itself (day, scope, started_at here; the scrape and the pulls
  # add their sections; it travels to the other stages in the `scope` artifact)
  $PY nightly/run_stats.py start "$OUT" --day "$DAY" --scope "$SCOPE"
  echo "--- 1/8 index listing ---"
  $PY alt_scraper.py --list '*' --out "$OUT"

  echo "--- 2/8 scope ($SCOPE) ---"
  if [ "$SCOPE" = "full" ]; then
    # The index listing already wrote all_pokemon_cards.txt/.json in the format the scraper takes.
    if [ -n "${NIGHTLY_LIMIT:-}" ]; then
      { grep '^asset:' "$OUT/all_pokemon_cards.txt" | head -n "$NIGHTLY_LIMIT"; } > "$OUT/scope.txt"   # exactly N cards (the file's blank line used to count)
      cp "$OUT/all_pokemon_cards.json" "$OUT/scope.json"
    fi
  else
    # roster candidates (nightly/subjects.txt) at every year, plus the vintage-checklist species at <= 2013
    $PY nightly/filter_subjects.py "$OUT/all_pokemon_cards.json" nightly/subjects.txt "$OUT/scope.txt" "${NIGHTLY_LIMIT:-0}" nightly/vintage_species.txt 2013
  fi
  if [ "$SHARDS" -gt 1 ]; then
    echo "--- 2b/8 deal the scope into $SHARDS shards ---"
    local LIST; LIST=$(SHARDS=1 scope_list)
    $PY nightly/shards.py split "$LIST" "$SHARDS" "$OUT"
  fi
  # Job outputs for the Actions workflow: the later jobs take the day (so every job uses this run
  # folder) and the shard list for their matrix.
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    { echo "day=$DAY"; echo "out=$OUT"; echo "shards=$($PY -c "import json; print(json.dumps(list(range($SHARDS))))")"; } >> "$GITHUB_OUTPUT"
  fi
}

stage_scrape() {   # $1 = list file, $2 = folder for cards.csv / sales.csv / listings.csv
  local LIST="$1" SOUT="$2"
  echo "--- 3/8 scrape${SHARD:+ (shard $SHARD of $SHARDS)} ---"
  [ -f "$LIST" ] || { echo "no scope list at $LIST: run the scope stage first (on Actions: its artifact was not downloaded)" >&2; exit 1; }
  mkdir -p "$SOUT"
  # caffeinate (macOS only) keeps the machine from idle-sleeping mid-run; a
  # closed lid still sleeps, launchd resumes the job on wake. No-op elsewhere.
  local CAFF=""; command -v caffeinate >/dev/null && CAFF="caffeinate -i"
  # --max-sales 0: write every sale alt.xyz returns (one request per card either
  # way), so the history store gets a card's full PSA 10 sale record, not just
  # the newest 200 — the 90-day/1-year change columns need the older sales.
  # --keep-empty: cards whose PSA pop table exists but shows zero PSA 10s are
  # written with pop_at_grade = 0 (a real zero) instead of being skipped, so
  # PokeSniper's Categories tab can say "PSA 10 pop: 0" (Sid, 2026-09-17).
  # Blank pop still means "alt.xyz has no PSA rows at all" — a different fact.
  # --also-grade 9 (nightly scope only): PSA 9 sales too, one more request per
  # card that has any PSA 9s (~+0.7 sale rows per PSA 10 row), for the PSA 9 feed
  # columns and the "PSA 9s lag a PSA 10 pump" research (2026-09-22). The weekly
  # full run leaves it off: 65k cards is already ~5 h of a 6 h job limit.
  # --also-listings (only ever with --also-grade, so never on the full run): PSA 9 live listings
  # too, one more request per card that has any PSA 9s, for the psa9_listings_checked_at /
  # psa9_live_* / psa9_lowest_bin_* feed columns. On by default since 2026-09-26 (Sid asked for the 9s' buy /
  # auction data; ~+33k requests a night); NIGHTLY_ALSO_LISTINGS=0 turns it off. Was: off until set to 1 (in the
  # workflow env, or change the default below); those feed columns stay blank until then.
  local ALSO_GRADE ALSO_LISTINGS
  if [ "$SCOPE" = "full" ]; then ALSO_GRADE="${NIGHTLY_ALSO_GRADE-}"; else ALSO_GRADE="${NIGHTLY_ALSO_GRADE-9}"; fi
  ALSO_LISTINGS="${NIGHTLY_ALSO_LISTINGS-1}"
  local SCRAPE=($PY alt_scraper.py --workers "${NIGHTLY_WORKERS:-4}" --delay "${NIGHTLY_DELAY:-0.25}" --max-sales 0 --keep-empty --out "$SOUT")
  if [ -n "$ALSO_GRADE" ]; then SCRAPE+=(--also-grade "$ALSO_GRADE"); fi
  if [ -n "$ALSO_GRADE" ] && [ "$ALSO_LISTINGS" = "1" ]; then SCRAPE+=(--also-listings); fi
  # NIGHTLY_SKIP_UNCHANGED=1: pass the previous run's daily file (newest history/daily/*.csv
  # dated before DAY; the names are YYYY-MM-DD so lexical order is date order). Never on the
  # full scope, which is what refetches every carried card once a week.
  if [ -n "${NIGHTLY_SKIP_UNCHANGED:-}" ] && [ "$SCOPE" != "full" ]; then
    local PREV_DAILY="" f
    for f in history/daily/*.csv; do
      [ -e "$f" ] || continue
      if [ "$(basename "$f" .csv)" \< "$DAY" ]; then PREV_DAILY="$f"; fi
    done
    if [ -n "$PREV_DAILY" ]; then
      echo "skip-unchanged sales: previous run = $PREV_DAILY"
      SCRAPE+=(--skip-unchanged-sales "$PREV_DAILY")
    else
      echo "skip-unchanged sales: no history/daily file dated before $DAY, every card's sales are fetched"
    fi
  fi
  SCRAPE+=("$LIST")
  # The scraper exits 2 when some cards failed (network blips) and 0 when every card came back; anything
  # else is a crash. Retry passes: --resume skips everything already in cards.csv, so only the missing cards
  # are fetched again. Up to 2 passes, on the exit status (until 2026-10-17 this grepped the tee'd log for
  # "card(s) failed", which also hid a crash). What came back is judged by the gate in the assemble stage,
  # not here; the scraper's account of this shard is $SOUT/run_stats.json.
  local rc=0 pass
  $CAFF "${SCRAPE[@]}" || rc=$?
  for pass in 1 2; do
    [ "$rc" -ne 0 ] || break
    echo "--- retry pass $pass (--resume; the scraper exited $rc) ---"
    rc=0; $CAFF "${SCRAPE[@]}" --resume || rc=$?
  done
  local ROWS; ROWS=$(($(wc -l < "$SOUT/cards.csv") - 1))
  echo "=== scrape done $(date '+%F %T'): $ROWS rows in $SOUT/cards.csv (scraper exit $rc) ==="
}

stage_assemble() {
  if [ "$SHARDS" -gt 1 ]; then
    echo "--- 3b/8 merge the shards' cards.csv / sales.csv / listings.csv ---"
    $PY nightly/shards.py merge "$OUT"
  fi
  # The shards' accounts (shard-<i>/run_stats.json) added up into the run's; a shard whose job died is
  # accounted from its files (its missing cards count as failed). No shard folders = nothing to do.
  $PY nightly/run_stats.py merge "$OUT"

  if [ -n "${NIGHTLY_SKIP_HISTORY:-}" ]; then echo "=== NIGHTLY_SKIP_HISTORY set: stopping after the scrape ==="; exit 0; fi

  echo "--- 3c/8 gate: is this a day's data? ---"
  # nightly/run_stats.py gate reads $OUT/run_stats.json (the assembled totals) and exits 1, and with set -e so
  # does this stage (no pulls, no ingest, no commit; the snapshot stays as the workflow artifact), when fewer
  # than NIGHTLY_MIN_SCRAPED_PCT (90) % of the scope came back or more than NIGHTLY_MAX_LISTINGS_FAILED_PCT
  # (20) % of the live-listings checks failed at either grade. Before 2026-10-17 such a night was committed as
  # the day's data with one log line to show for it. The verdict is in run_stats.json either way.
  $PY nightly/run_stats.py gate "$OUT"

  echo "--- alt.xyz's own auctions + marketplace (one bulk pull, all cards) ---"
  # -> $OUT/alt_listings.csv, which ingest folds in (and commit_results.sh's re-derive again). A failed
  # pull writes no file, so the last good Alt rows stand; the run goes on.
  $PY alt_scraper.py --alt-listings --out "$OUT" || echo "alt listings: pull failed, kept the previous Alt rows"

  # Fanatics Collect's Buy Now marketplace, read directly with their permission (2026-10-15): every live
  # Pokemon PSA 9 / PSA 10 listing in ~45 search requests, matched to the feed's cards, -> $OUT/
  # fanatics_listings.csv, which ingest folds in (replacing every Fanatics Buy Now row, dropping Sold / gone
  # ones). Only when NIGHTLY_FANATICS_BUDGET (max requests; the permission said ~60-80 a day) is set; a
  # failed or over-budget pull writes no file, so the last good rows stand and the run goes on. The pull
  # records ok / failed and requests used vs budget in run_stats.json; after the commit the workflow's
  # "Fanatics pull health" step (nightly/run_stats.py fanatics-health) goes red once the pull has failed
  # NIGHTLY_FANATICS_MAX_FAILED_NIGHTS (3) nights running, before the direct rows age out (7 days).
  if [ -n "${NIGHTLY_FANATICS_BUDGET:-}" ]; then
    echo "--- Fanatics Collect's Buy Now marketplace (one bulk pull, all cards) ---"
    $PY alt_scraper.py --fanatics-listings --budget "$NIGHTLY_FANATICS_BUDGET" --out "$OUT" \
      || echo "fanatics listings: pull failed, kept the previous Fanatics rows"
  fi
  $PY nightly/run_stats.py finish "$OUT"   # finished_at + the Fanatics pull's health -> run_stats.json

  echo "--- 4/8 ingest into history/ ---"
  $PY history/ingest.py "$OUT" --date "$DAY"

  echo "--- 5/8 rebuild latest/ ---"
  $PY history/metrics.py

  echo "--- 6/8 per-character coverage ---"
  $PY history/coverage.py

  echo "--- 7/8 PSA 9 buy-condition flags ---"
  $PY history/flags.py

  echo "--- 8/8 TCGplayer ids + card images ---"
  # tcgcsv.com (TCGplayer's catalog, free, ~220 requests) into the snapshot, matched to latest/cards.csv.
  # If it's unreachable, yesterday's three files stay: the run doesn't fail over images.
  # --skus-budget-min: TCGplayer's SKU printings for products new to history/tcgplayer_sku_printings.csv
  # (the catalog's printings column; one request per product, so capped at 15 minutes a night).
  if ! $PY history/tcgplayer_ids.py --fetch --tcgcsv "$OUT/tcgcsv" --skus-budget-min "${NIGHTLY_SKUS_BUDGET_MIN:-15}"; then
    echo "tcgplayer ids: catalog download or match failed, kept the previous tcgplayer_ids / card_catalog / tcgplayer_sets"
  fi
  echo "=== all done $(date '+%F %T') ==="
}

case "$STAGE" in
  scope)    stage_scope ;;
  scrape)   stage_scrape "$(scope_list)" "$(scrape_out)" ;;
  assemble) stage_assemble ;;
  all)
    stage_scope
    if [ "$SHARDS" -gt 1 ]; then
      for SHARD in $(seq 0 $((SHARDS - 1))); do stage_scrape "$(scope_list)" "$(scrape_out)"; done
      SHARD=""
    else
      stage_scrape "$(scope_list)" "$(scrape_out)"
    fi
    stage_assemble ;;
esac
