#!/bin/bash
# Did a run of this kind already land on main? (2026-10-14)
#
#   nightly/already_ran.sh "nightly $(date -u +%F)"       # exit 0 if one of the newest commits on
#                                                         # main starts with that text, else exit 1
#   nightly/already_ran.sh "ebay verify $(date -u +%F)" "$(date -u +%FT07:00:00Z)"
#                                                         # ... and was committed at or after that time
#                                                         # (eBay's quota day starts 07:00 UTC: a manual
#                                                         # run before it must not stop the scheduled one,
#                                                         # which happened 2026-10-16)
#
# GitHub fires a scheduled workflow anywhere from on time to ~9 h late (the 07:00 UTC nightly
# started between 12:00 and 16:15 UTC in October 2026), so the workflows list several cron
# slots and the first slot that fires does the work; the others call this with today's date
# (the run day in the commit message, UTC, same as the snapshot folder) and stop. Reads the
# newest commits through the GitHub API (the checkout is shallow, so git log only sees HEAD,
# which may be a listings-refresh commit). Needs GH_TOKEN (the workflow's github.token) and
# GITHUB_REPOSITORY; outside Actions `gh` uses its own login and the repo defaults to
# samaygodika/pokemon-psa10-history. Any API failure exits 1 ("not run"): a duplicate run costs
# a scrape, a skipped one costs a day.
set -uo pipefail
PREFIX=${1:?commit message prefix, e.g. "nightly 2026-10-14"}
SINCE=${2:-}   # optional ISO-8601 UTC instant; older matching commits don't count
REPO=${GITHUB_REPOSITORY:-samaygodika/pokemon-psa10-history}
BRANCH=${ALREADY_RAN_BRANCH:-main}
json=$(gh api "repos/$REPO/commits?sha=$BRANCH&per_page=30" 2>/dev/null) || { echo "already_ran: API call failed, assuming not run"; exit 1; }
printf '%s' "$json" | python3 -c '
import json, sys
prefix, since = sys.argv[1], sys.argv[2]
commits = json.load(sys.stdin)
for c in commits:
    first = c["commit"]["message"].splitlines()[0]
    if first.startswith(prefix):
        sha, when = c["sha"][:8], c["commit"]["committer"]["date"]
        if since and when < since:          # both ISO-8601 Z strings, so text order is time order
            print(f"already_ran: {sha} {when} is before {since}, does not count ({first[:60]})")
            continue
        print(f"already_ran: yes, {sha} {when} ({first[:80]})")
        sys.exit(0)
print(f"already_ran: no commit starting with {prefix!r}" + (f" since {since}" if since else "") + f" in the last {len(commits)}")
sys.exit(1)
' "$PREFIX" "$SINCE"
