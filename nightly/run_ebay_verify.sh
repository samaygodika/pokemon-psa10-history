#!/bin/bash
# eBay verification of the feed's eBay Buy It Nows (2026-10-06): ask eBay's Browse API whether
# each listing is still live, drop the dead ones, rebuild latest/, commit. Runs daily from
# .github/workflows/ebay-verify.yml just after eBay's quota reset (07:00 UTC); ~4,800 lookups,
# ~20-30 min. See history/ebay_verify.py for which listings and what the answers mean.
#
#   nightly/run_ebay_verify.sh                         # [watchlist sweep ->] check -> apply -> metrics -> commit
#   EBAY_BUDGET=50 NO_COMMIT=1 nightly/run_ebay_verify.sh   # smoke test: 50 lookups, no commit
#
# Env: EBAY_CLIENT_ID / EBAY_CLIENT_SECRET (or ~/.ebay_keys), EBAY_BUDGET (4800), EBAY_WORKERS (4),
# NO_COMMIT. Without keys it does nothing and exits 0.
#
# Watchlist sweep (2026-10-15, history/watchlist_listings.py): runs first when WATCHLIST_FILE or
# WATCHLIST_URL (+ WATCHLIST_TOKEN) is set, writes latest/watchlist_listings.csv (+ .summary.json)
# and hands the verify what is left of EBAY_BUDGET after its own eBay calls (WATCHLIST_EBAY_BUDGET,
# default 360, ~300 used), so the day stays under eBay's 5,000. Unset = no sweep, nothing changes.
# A sweep failure is logged and the verify still runs.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
STAMP=$(date -u +%Y-%m-%dT%H%MZ)
OUT="snapshots/ebay/$STAMP"
mkdir -p "$OUT"
LOG="$OUT/run.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== ebay verify $STAMP started $(date -u '+%F %T') ==="

VERIFY_BUDGET=${EBAY_BUDGET:-4800}
SWEEP_NOTE=""
if [ -n "${WATCHLIST_FILE:-}${WATCHLIST_URL:-}" ]; then
  echo "--- watchlist sweep ---"
  if $PY history/watchlist_listings.py --budget "${WATCHLIST_EBAY_BUDGET:-360}"; then
    cp latest/watchlist_listings.csv latest/watchlist_listings.summary.json "$OUT/"   # survives the re-derive below
    SWEEP_CALLS=$($PY -c 'import json;print(json.load(open("latest/watchlist_listings.summary.json"))["ebay_calls"])')
    VERIFY_BUDGET=$(( VERIFY_BUDGET - SWEEP_CALLS )); [ "$VERIFY_BUDGET" -lt 0 ] && VERIFY_BUDGET=0
    # the sweep's own one-line note (its summary JSON), not a grep of the log: under set -e a missing
    # line killed this script here, before the commit (2026-10-17)
    SWEEP_NOTE="; watchlist $($PY -c 'import json,sys;print(json.load(open(sys.argv[1]))["note"])' latest/watchlist_listings.summary.json)"
    echo "watchlist sweep used $SWEEP_CALLS eBay call(s); verify budget now $VERIFY_BUDGET"
  else
    echo "watchlist sweep FAILED (exit $?), verify runs with the full budget"
  fi
fi

$PY history/ebay_verify.py check --out "$OUT" --budget "$VERIFY_BUDGET" --workers "${EBAY_WORKERS:-4}"
if [ ! -s "$OUT/ebay_results.csv" ] && [ ! -s "$OUT/watchlist_listings.csv" ]; then echo "=== no answers, nothing to apply ==="; exit 0; fi
[ -s "$OUT/ebay_results.csv" ] && $PY history/ebay_verify.py apply "$OUT"
echo "--- rebuild latest/ ---"
$PY history/metrics.py
if [ -n "${NO_COMMIT:-}" ]; then echo "=== done (no commit) $(date -u '+%F %T') ==="; exit 0; fi

git config user.name "alt.xyz nightly"
git config user.email "nightly@users.noreply.github.com"
# the check's one-line summary from its status JSON ($OUT/ebay_verify.json, written by ebay_verify.py check
# and apply), not a grep of the tee'd log (same set -e trap as above)
SUMMARY=$($PY -c 'import json,sys;print(json.load(open(sys.argv[1]))["summary"])' "$OUT/ebay_verify.json")$SWEEP_NOTE
for attempt in 1 2 3; do
  git add history latest
  if git commit -q -m "ebay verify $STAMP: $SUMMARY"; then
    if git push -q origin HEAD:main; then
      echo "pushed on attempt $attempt: $(git log --oneline -1)"
      echo "=== done $(date -u '+%F %T') ==="
      exit 0
    fi
  else
    echo "nothing to commit"; exit 0
  fi
  echo "--- push rejected (attempt $attempt): re-applying on top of origin/main ---"
  git fetch -q origin
  git reset -q --hard origin/main
  # eBay's answers are this run's and still true: re-apply them to the newer store, no new lookups.
  [ -s "$OUT/ebay_results.csv" ] && $PY history/ebay_verify.py apply "$OUT"
  [ -s "$OUT/watchlist_listings.csv" ] && cp "$OUT/watchlist_listings.csv" "$OUT/watchlist_listings.summary.json" latest/
  $PY history/metrics.py
done
echo "could not push after 3 attempts" >&2
exit 1
