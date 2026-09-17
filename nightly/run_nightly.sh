#!/bin/bash
# Scheduled alt.xyz scrape -> history store -> latest/ (what PokeSniper reads).
#
#   nightly/run_nightly.sh              # top-60 subjects (nightly/subjects.txt) at every year, plus the
#                                        # vintage-checklist species (nightly/vintage_species.txt) at <= 2013: ~31k cards
#   NIGHTLY_SCOPE=full nightly/run_nightly.sh   # every graded Pokemon card (~65k), for the weekly run
#
# Steps:
#   1. index listing  -> snapshots/<date>/all_pokemon_cards.{txt,json}   (~260 requests, ~25 min)
#   2. scope filter   -> snapshots/<date>/scope.txt (+ .json sidecar)
#   3. scrape         -> snapshots/<date>/cards.csv, sales.csv, run.log   (retries failed cards twice)
#   4. ingest         -> history/assets.csv, history/daily/<date>.csv, history/sales/<month>.csv
#   5. metrics        -> latest/cards.csv, latest/series/*.csv, latest/summary.json
#
# Only history/ and latest/ are committed; snapshots/ is raw and gitignored.
# Every dated snapshot is kept locally on purpose (cheap, and lets a bad ingest
# be redone), prune old ones by hand if disk gets tight.
#
# Env knobs: NIGHTLY_SCOPE (top60|full), NIGHTLY_WORKERS (4), NIGHTLY_DELAY
# (0.25 s per worker between requests), NIGHTLY_LIMIT (scrape only the first N
# cards, for smoke tests), NIGHTLY_DATE (override the folder/day name),
# NIGHTLY_SKIP_HISTORY=1 (stop after the scrape).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
DAY=${NIGHTLY_DATE:-$(date +%F)}
SCOPE=${NIGHTLY_SCOPE:-top60}
OUT="snapshots/$DAY"
if [ "$SCOPE" = "full" ]; then OUT="snapshots/$DAY-full"; fi
mkdir -p "$OUT"
LOG="$OUT/run.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== $SCOPE scrape $DAY started $(date '+%F %T') ==="

echo "--- 1/5 index listing ---"
$PY alt_scraper.py --list '*' --out "$OUT"

echo "--- 2/5 scope ($SCOPE) ---"
if [ "$SCOPE" = "full" ]; then
  # The index listing already wrote all_pokemon_cards.txt/.json in the format the scraper takes.
  SCOPE_LIST="$OUT/all_pokemon_cards.txt"
  if [ -n "${NIGHTLY_LIMIT:-}" ]; then
    { grep -v '^#' "$OUT/all_pokemon_cards.txt" | head -n "$NIGHTLY_LIMIT"; } > "$OUT/scope.txt"
    cp "$OUT/all_pokemon_cards.json" "$OUT/scope.json"
    SCOPE_LIST="$OUT/scope.txt"
  fi
else
  # top-60 subjects at every year, plus the vintage-checklist species (nightly/vintage_species.txt) at <= 2013
  $PY nightly/filter_subjects.py "$OUT/all_pokemon_cards.json" nightly/subjects.txt "$OUT/scope.txt" "${NIGHTLY_LIMIT:-0}" nightly/vintage_species.txt 2013
  SCOPE_LIST="$OUT/scope.txt"
fi

echo "--- 3/5 scrape ---"
# caffeinate (macOS only) keeps the machine from idle-sleeping mid-run; a
# closed lid still sleeps, launchd resumes the job on wake. No-op elsewhere.
CAFF=""; command -v caffeinate >/dev/null && CAFF="caffeinate -i"
# --max-sales 0: write every sale alt.xyz returns (one request per card either
# way), so the history store gets a card's full PSA 10 sale record, not just
# the newest 200 — the 90-day/1-year change columns need the older sales.
# --keep-empty: cards whose PSA pop table exists but shows zero PSA 10s are
# written with pop_at_grade = 0 (a real zero) instead of being skipped, so
# PokeSniper's Categories tab can say "PSA 10 pop: 0" (Sid, 2026-09-17).
# Blank pop still means "alt.xyz has no PSA rows at all" — a different fact.
SCRAPE=($PY alt_scraper.py --workers "${NIGHTLY_WORKERS:-4}" --delay "${NIGHTLY_DELAY:-0.25}" --max-sales 0 --keep-empty --out "$OUT" "$SCOPE_LIST")
$CAFF "${SCRAPE[@]}" || true
# Retry pass: --resume skips everything already in cards.csv, so only the
# cards that failed (network blips) get fetched again. Up to 2 passes.
for pass in 1 2; do
  if tail -n 5 "$LOG" | grep -q 'card(s) failed'; then
    echo "--- retry pass $pass (--resume) ---"
    $CAFF "${SCRAPE[@]}" --resume || true
  fi
done
ROWS=$(($(wc -l < "$OUT/cards.csv") - 1))
echo "=== scrape done $(date '+%F %T'): $ROWS rows in $OUT/cards.csv ==="

if [ -n "${NIGHTLY_SKIP_HISTORY:-}" ]; then exit 0; fi

echo "--- 4/5 ingest into history/ ---"
$PY history/ingest.py "$OUT" --date "$DAY"

echo "--- 5/5 rebuild latest/ ---"
$PY history/metrics.py
echo "=== all done $(date '+%F %T') ==="
