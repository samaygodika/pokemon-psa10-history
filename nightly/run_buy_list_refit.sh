#!/bin/bash
# Monthly re-fit of the buy-list rate tables (2026-10-23): analysis/buy_list_refit.py builds today's
# loose panel, scores every anchor month that closed since the current tables with those tables
# (the out-of-sample check), re-fits on every closed month, and this commits the four files it
# writes in history/ (+ history/buy_list_v02.json, 2026-10-26). No other run writes them, so a rejected push is re-applied on top of
# origin/main as is. The nightly's step 7b reads the new tables on its next run.
#
#   nightly/run_buy_list_refit.sh                       # what .github/workflows/buy-list-refit.yml runs
#   STORE_MODE=local NO_COMMIT=1 nightly/run_buy_list_refit.sh   # here: the local store, no commit
#
# Needs pandas / numpy / scipy / scikit-learn (v0.2's fit, 2026-10-26; analysis/requirements.txt); PY picks the interpreter.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-python3}
STAMP=$(date -u +%F)
FILES=(history/buy_list_rates.csv history/buy_list_lifts.csv history/buy_list_rates_meta.json history/buy_list_refit_log.csv history/buy_list_v02.json)
LOGFILE=analysis/out/buy_list_refit_$STAMP.log
mkdir -p analysis/out

$PY history/store.py pull   # history/sales from the release (STORE_MODE=local skips it): panel.py reads them
$PY analysis/buy_list_refit.py 2>&1 | tee "$LOGFILE"
SUMMARY=$(grep '^refit summary: ' "$LOGFILE" | tail -1 | sed 's/^refit summary: //')

changed=()
for f in "${FILES[@]}"; do
  [ -e "$f" ] || continue
  if ! git diff --quiet -- "$f" 2>/dev/null || [ -z "$(git ls-files -- "$f")" ]; then changed+=("$f"); fi
done
if [ ${#changed[@]} -eq 0 ]; then
  echo "buy list refit: nothing to commit ($SUMMARY)"
  exit 0
fi
if [ -n "${NO_COMMIT:-}" ]; then
  echo "buy list refit: NO_COMMIT set, leaving ${changed[*]} uncommitted"
  exit 0
fi

git config user.name "alt.xyz nightly"
git config user.email "nightly@users.noreply.github.com"
keep=$(mktemp -d)
cp "${changed[@]}" "$keep/"
for attempt in 1 2 3; do
  git add -- "${changed[@]}"
  if git commit -q -m "buy list refit $STAMP: $SUMMARY"; then
    if git push -q origin HEAD:main; then
      echo "pushed on attempt $attempt: $(git log --oneline -1)"
      exit 0
    fi
  fi
  echo "--- push rejected (attempt $attempt): re-applying the re-fit files on top of origin/main ---"
  git fetch -q origin
  git reset -q --hard origin/main
  for f in "${changed[@]}"; do cp "$keep/$(basename "$f")" "$f"; done
done
echo "could not push after 3 attempts" >&2
exit 1
