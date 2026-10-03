#!/bin/bash
# Listings refresh between nightlies (2026-10-03): re-check the live listings of every card
# the feed shows with a running auction, at the grade(s) that have one, and rebuild latest/.
# Fault it fixes: an auction that ended or was pulled early stayed "live" in the app until
# the next nightly (up to a day); alt.xyz itself drops those within hours. Runs from
# .github/workflows/listings-refresh.yml three times a day (queued behind a running nightly
# by the shared concurrency group), ~7k requests, ~30-40 min.
#
#   nightly/run_listings_refresh.sh                 # scope from latest/cards.csv -> scrape -> ingest -> metrics -> commit
#   REFRESH_LIMIT=20 NO_COMMIT=1 nightly/run_listings_refresh.sh   # local smoke test: 20 cards per grade, no commit
#
# Steps: 1. nightly/refresh_scope.py -> snapshots/refresh/<stamp>/refresh_psa{10,9}.{txt,json}
#        2. alt_scraper.py --listings-only, once per grade (one request per card, no pops, no sales)
#        3. history/ingest.py --listings-only, once per grade (live_listings.csv + check times in daily/)
#        4. history/metrics.py (latest/cards.csv, series, recent_sales, clean_sales, summary.json;
#           only the listing columns can change) — coverage/flags/tcgplayer are not touched
#        5. commit + push, re-deriving on top of origin/main if a push is rejected (same idea as
#           nightly/commit_results.sh). NO_COMMIT=1 skips this.
# Env: NIGHTLY_WORKERS (4), NIGHTLY_DELAY (0.25), REFRESH_LIMIT (cards per grade, smoke tests), NO_COMMIT.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
STAMP=$(date -u +%Y-%m-%dT%H%MZ)
DAY=${STAMP:0:10}
OUT="snapshots/refresh/$STAMP"
mkdir -p "$OUT"
LOG="$OUT/run.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== listings refresh $STAMP started $(date -u '+%F %T') ==="

scrape_and_ingest() {
  echo "--- scope ---"
  $PY nightly/refresh_scope.py latest/cards.csv "$OUT" "${REFRESH_LIMIT:-}"
  for g in 10 9; do
    LIST="$OUT/refresh_psa$g.txt"
    if [ "$(grep -c '^asset:' "$LIST")" = "0" ]; then echo "PSA $g: nothing to refresh"; continue; fi
    echo "--- scrape PSA $g listings ---"
    rm -rf "$OUT/psa$g"
    $PY alt_scraper.py --listings-only --grade "$g" --workers "${NIGHTLY_WORKERS:-4}" --delay "${NIGHTLY_DELAY:-0.25}" \
        --keep-empty --out "$OUT/psa$g" "$LIST" || true
    if tail -n 5 "$LOG" | grep -q 'card(s) failed'; then
      echo "--- retry (--resume) ---"
      $PY alt_scraper.py --listings-only --grade "$g" --workers "${NIGHTLY_WORKERS:-4}" --delay "${NIGHTLY_DELAY:-0.25}" \
          --keep-empty --out "$OUT/psa$g" --resume "$LIST" || true
    fi
    echo "--- ingest PSA $g ---"
    $PY history/ingest.py "$OUT/psa$g" --date "$DAY" --listings-only
  done
  echo "--- rebuild latest/ ---"
  $PY history/metrics.py
}

scrape_and_ingest
if [ -n "${NO_COMMIT:-}" ]; then echo "=== done (no commit) $(date -u '+%F %T') ==="; exit 0; fi

git config user.name "alt.xyz nightly"
git config user.email "nightly@users.noreply.github.com"
for attempt in 1 2 3; do
  git add history latest
  if git commit -q -m "listings refresh $STAMP: $(grep -c '^asset:' "$OUT/refresh_psa10.txt") PSA 10 + $(grep -c '^asset:' "$OUT/refresh_psa9.txt") PSA 9 cards with a running auction re-checked"; then
    if git push -q origin HEAD:main; then
      echo "pushed on attempt $attempt: $(git log --oneline -1)"
      echo "=== done $(date -u '+%F %T') ==="
      exit 0
    fi
  else
    echo "nothing to commit"; exit 0
  fi
  echo "--- push rejected (attempt $attempt): re-deriving on top of origin/main ---"
  git fetch -q origin
  git reset -q --hard origin/main
  # Re-ingest this run's answers onto the newer store (a nightly may have landed meanwhile);
  # the listings scraped are still this run's, so no re-scrape.
  for g in 10 9; do
    [ -f "$OUT/psa$g/cards.csv" ] && $PY history/ingest.py "$OUT/psa$g" --date "$DAY" --listings-only
  done
  $PY history/metrics.py
done
echo "could not push after 3 attempts" >&2
exit 1
