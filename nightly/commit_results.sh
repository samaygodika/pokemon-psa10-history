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
# The sales + daily store is not in git (2026-10-17, history/store.py, handoff/2026-10-17-store-
# move-design.md): it goes to the release FIRST, as the next generation, then latest/ and the
# history files that stay in git are committed, so latest/ never claims sales the store does not
# hold. Exit 3 from the store push = the release moved since this run pulled it (a laptop run
# landed meanwhile; the Actions runs serialise on the `scrape` group): the run stops with nothing
# committed, and the raw run is in the 7-day artifact (pull, re-ingest, run this script again).
#
# Reads snapshots/last_run.env (written by run_nightly.sh): OUT and DAY.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
# shellcheck disable=SC1091
source snapshots/last_run.env   # OUT=snapshots/<day>[-full] DAY=<YYYY-MM-DD>
git config user.name "alt.xyz nightly"
git config user.email "nightly@users.noreply.github.com"

# "<prefix> <date>: N assets, M sales; <the run's own counts>" from latest/summary.json (nightly/run_stats.py;
# nightly/already_ran.sh matches the "<prefix> <date>" start, so that part never changes shape)
msg() { $PY nightly/run_stats.py commit-message --prefix "${1:-nightly}"; }

$PY history/store.py push --note "$(msg "${COMMIT_PREFIX:-nightly}")"   # history/sales + daily -> the release (see above)
for attempt in 1 2 3; do
  $PY history/store.py stage   # latest/ + the history files that stay in git (never history/sales, history/daily)
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
  # Keep the SKU printings this run asked TCGplayer for (tcgplayer_skus.py) across the reset:
  # merged into origin's copy, newest answer per product wins (origin may hold a bigger backfill).
  cp history/tcgplayer_sku_printings.csv "$OUT/tcgplayer_sku_printings.csv" 2>/dev/null || true
  git reset -q --hard origin/main
  $PY history/store.py pull --force   # the reset put the checkout's copy of sales/ + daily/ back (while still git-tracked): the pulled generation again
  [ -f "$OUT/tcgplayer_sku_printings.csv" ] && $PY history/tcgplayer_skus.py --merge "$OUT/tcgplayer_sku_printings.csv" || true
  $PY history/ingest.py "$OUT" --date "$DAY"
  $PY history/metrics.py
  $PY history/coverage.py
  $PY history/flags.py            # appends this run's data_date rows; without it a rejected push lost that night's PSA 9 flag rows (until 2026-10-17)
  $PY history/tcgplayer_ids.py --tcgcsv "$OUT/tcgcsv" || true   # the run's catalog; origin's file if it has none
  $PY history/manifest.py   # last: sha256 / rows of every latest/ file into summary.json "files" + shard manifests
done
echo "could not push after 3 attempts" >&2
exit 1
