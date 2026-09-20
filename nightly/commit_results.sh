#!/bin/bash
# Commit history/ and latest/ after a run, in a way that survives another
# run's commit landing on main in the meantime.
#
# The generated files (latest/*, history/daily/<date>.csv, history/sales/*)
# are rebuilt from scratch by every run, so a `git pull --rebase` of two runs'
# commits always conflicts (2026-09-20: the weekly full run lost 5 h of
# scraping that way when its checkout missed the nightly's push by seconds).
# Instead of merging, this script re-derives: reset to origin/main, ingest the
# run's snapshot again on top of it (ingest is idempotent: sales are deduped
# by URL, a day's file merges every run of that day), rebuild latest/, commit,
# push. Up to 3 attempts.
#
# Reads snapshots/last_run.env (written by run_nightly.sh): OUT and DAY.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
# shellcheck disable=SC1091
source snapshots/last_run.env   # OUT=snapshots/<day>[-full] DAY=<YYYY-MM-DD>
git config user.name "alt.xyz nightly"
git config user.email "nightly@users.noreply.github.com"

msg() { $PY -c 'import json;s=json.load(open("latest/summary.json"));print("'"${1:-nightly}"'",s["data_date"]+":",s["assets"],"assets,",s["psa10_sales"],"sales")'; }

for attempt in 1 2 3; do
  git add history latest
  if git commit -q -m "$(msg "${COMMIT_PREFIX:-nightly}")"; then
    if git push -q origin HEAD:main; then
      echo "pushed on attempt $attempt: $(git log --oneline -1)"
      exit 0
    fi
  else
    echo "nothing to commit"
    exit 0
  fi
  echo "--- push rejected (attempt $attempt): re-deriving on top of origin/main ---"
  git fetch -q origin
  git reset -q --hard origin/main
  $PY history/ingest.py "$OUT" --date "$DAY"
  $PY history/metrics.py
  $PY history/coverage.py
done
echo "could not push after 3 attempts" >&2
exit 1
