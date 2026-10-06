#!/bin/bash
# eBay verification of the feed's eBay Buy It Nows (2026-10-06): ask eBay's Browse API whether
# each listing is still live, drop the dead ones, rebuild latest/, commit. Runs daily from
# .github/workflows/ebay-verify.yml just after eBay's quota reset (07:00 UTC); ~4,800 lookups,
# ~20-30 min. See history/ebay_verify.py for which listings and what the answers mean.
#
#   nightly/run_ebay_verify.sh                         # check -> apply -> metrics -> commit
#   EBAY_BUDGET=50 NO_COMMIT=1 nightly/run_ebay_verify.sh   # smoke test: 50 lookups, no commit
#
# Env: EBAY_CLIENT_ID / EBAY_CLIENT_SECRET (or ~/.ebay_keys), EBAY_BUDGET (4800), EBAY_WORKERS (4),
# NO_COMMIT. Without keys it does nothing and exits 0.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
STAMP=$(date -u +%Y-%m-%dT%H%MZ)
OUT="snapshots/ebay/$STAMP"
mkdir -p "$OUT"
LOG="$OUT/run.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== ebay verify $STAMP started $(date -u '+%F %T') ==="

$PY history/ebay_verify.py check --out "$OUT" --budget "${EBAY_BUDGET:-4800}" --workers "${EBAY_WORKERS:-4}"
if [ ! -s "$OUT/ebay_results.csv" ]; then echo "=== no answers, nothing to apply ==="; exit 0; fi
$PY history/ebay_verify.py apply "$OUT"
echo "--- rebuild latest/ ---"
$PY history/metrics.py
if [ -n "${NO_COMMIT:-}" ]; then echo "=== done (no commit) $(date -u '+%F %T') ==="; exit 0; fi

git config user.name "alt.xyz nightly"
git config user.email "nightly@users.noreply.github.com"
SUMMARY=$(tail -n 30 "$LOG" | grep -m1 '^ebay verify:' | sed 's/^ebay verify: //')
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
  $PY history/ebay_verify.py apply "$OUT"
  $PY history/metrics.py
done
echo "could not push after 3 attempts" >&2
exit 1
