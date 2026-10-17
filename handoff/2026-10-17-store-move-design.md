# Moving the history store out of the public repo (2026-10-17)

Review item 2 (`handoff/2026-10-17-improvement-review.md`): the remote is 3.3 GB and grows ~1 GB a
month; every Actions run checks out a 1.45 GB tree. This note picks where the store goes and how the
runs use it. Implementation: `history/store.py`, the four `nightly/run_*.sh` / `commit_results.sh`
scripts and the workflows; staging plan at the end.

## What a commit actually costs (measured 2026-10-17)

A fresh clone of the last 30 commits (2026-10-11..16, delta search redone, window 10), incremental
pack per commit = what a push sends and roughly what GitHub keeps after its repack:

| run kind | commits | each |
|---|---|---|
| nightly | 5 | 43 MiB |
| weekly full | 1 | 46 MiB |
| listings refresh | 14 | 0.6 MiB |
| eBay verify | 4 | 0.1 MiB |

So the refreshes and verifies re-storing `latest/cards.csv` cost almost nothing: git deltas them
fine (same rows, a few columns change). The nightly and weekly are the growth, ~1.5 GB a month at
these sizes (GitHub measures ~1 GB). Inside a nightly the big items are the 59-67 rewritten
`history/sales/*.csv` month files, `history/assets.csv` (every scraped row changes because
`last_seen` is bumped: 34,351 of 67,859 lines in the 10-15 -> 10-16 diff) and the rewritten
`latest/clean_sales/` months; `history/daily/` adds one 6 MB file a day and the refreshes rewrite the
newest one (5,958 lines changed per refresh for the check times).

One more finding worth knowing: a forced full repack of that 30-commit clone (`git repack -adf
--window=20`) stores the five days of older `history/sales` versions in 0.4 MiB and the whole window
in 56 MiB of history (cards.csv 29, live_listings 10, series + recent_sales 11), i.e. the deltas
exist; it is the deltas computed at push time that are poor, and GitHub's repacks reuse them. So the
3.3 GB is largely push-pack quality, a local `git gc --aggressive` would shrink the 12 GB laptop
`.git` a lot, and the remote would keep growing by whatever the nightly pushes until GitHub repacks
from scratch, which it does not promise. Moving the store out removes the pushes themselves and the
1.2 GB every Actions run checks out; after the move the public repo still grows by `latest/`
(cards.csv first; the review's `latest/live.csv` split is the next lever, a contract change for Sid).

The store itself: `history/sales/` 969 MB (93 files), `history/daily/` 186 MB (34 files); together
1.2 GB, gzip level 1 = 427 MB in 9 s on the laptop, extracts in 2 s. Everything else in `history/`
is 32 MB (`assets.csv` 17.8, `live_listings.csv` 7.6, `psa9_flags.csv` 3.1, `ebay_checks.csv` 2.1,
`tcgplayer_sku_printings.csv` 1.5, two small check files). `latest/` is 244 MB.

## The three options

**A. A separate private "store" repo, history squashed monthly.** The runs check it out next to the
feed (a PAT secret, since `github.token` cannot reach another repo), commit the store there, and once
a month someone force-pushes a squashed history. Pros: git semantics, the re-derive loop already
exists, the last month of store history stays browsable. Cons: it grows at the same ~1.5 GB a month
until squashed, and a squash does not shrink GitHub's copy until their GC runs (unreachable objects
linger, sometimes needing a support ticket); a second checkout per run (the store is 1.2 GB raw, so
a depth-1 clone still moves ~400 MB); a PAT that expires; a second repo to keep private; the monthly
chore is exactly the kind of thing that stops happening.

**B. Release assets on this repo.** One gzip tarball per generation as an asset of a release tagged
`store`, plus a 300-byte `store.json` pointer naming the current generation. The run downloads the
current tarball, ingests, uploads the next generation, moves the pointer. Release assets do not count
toward repository size (the 5 GB guidance is about the git data), each asset may be up to 2 GB (ours
is 427 MB, growing ~3-5 % a month), downloads are free and fast from GitHub's CDN, and the workflow's
own `github.token` (`contents: write`) can upload and delete assets, so no new secret. Cons: no
diff/blame of the store (we keep generations instead, see below); `gh release upload --clobber` is
delete-then-upload, so the pointer, not the tarball, is the only thing ever clobbered; two writers
need a check (below).

**C. Keep `latest/`, `history/daily/`, `history/live_listings.csv` and the check files in the public
repo, move only `history/sales/`.** This is a question of *what* moves, not where; A or B still has
to hold `sales/`. Keeping `daily/` in git costs a 6 MB file a day (2 MiB packed) plus the refresh
rewrites of the newest file, and the checked-out tree grows 180 MB a month forever, which is the
other half of the problem (every Actions run checks the tree out). Keeping `live_listings.csv`
(7.6 MB, ~5 rewrites a day, 0.6 MiB packed each) is cheap and its git history is the one record of
past listing states, which the first_seen backfill used once.

**Pick: B, with `history/sales/` and `history/daily/` in the store; everything else stays in git.**
No new secret, no second repo, no monthly chore, no size ceiling to watch, and ingest, metrics and
every analysis script keep reading the same paths because the tarball is extracted into `history/`
exactly where the files are today. `assets.csv` stays in git and stops churning (last_seen below).

## How it works (`history/store.py`)

- `pull`: read `store.json` from the release, download the generation it names (cached in
  `snapshots/store/`, sha256 checked), extract into `history/sales/` and `history/daily/` (the
  previous contents are replaced wholesale, so a month file that vanished upstream vanishes here),
  write `snapshots/store/state.json` (generation, content hash). A pull that is already at the
  current generation and whose files still hash the same is a no-op (~3 s to hash 1.2 GB). A pull
  refuses to overwrite a local store that changed since it was pulled (an ingest not pushed) unless
  `--force`; the scripts pass `--force` only in their recovery paths.
- `push`: hash the local store; if it equals the pointer's content hash, nothing to do (the eBay
  verify never changes the store, a re-derive after a rejected git push neither). Else re-read the
  pointer: if its generation is not the one pulled, exit 3 ("store moved"); else tar + gzip to
  `store-<UTC stamp>-g<N+1>.tar.gz`, upload it under that new name (never clobbered), re-read the
  pointer once more (exit 3 and delete the upload if it moved meanwhile), then clobber
  `store.json`. Then prune: keep the newest 3 generations plus the first generation of every month
  (a monthly archive, ~430 MB each on the release, free). The release is created on first use.
- Recovery: an upload that dies mid-way leaves an orphan `store-*.tar.gz` (pruned next time) and
  the pointer still names the last good generation; a pointer clobber that dies leaves the newest
  complete `store-*-g<N>.tar.gz`, which `pull` falls back to (assets GitHub reports as uploaded,
  highest generation). A run that dies between pull and push leaves the release untouched. If the
  store push fails the scripts stop before the git commit, so `latest/` never claims sales the store
  does not hold; the raw run is still in the 7-day artifact and can be re-ingested by hand after a
  `pull`.
- Where the runs call it (one line each, so the sharding and run-stats branches rebase over it):
  `run_listings_refresh.sh` and `run_ebay_verify.sh` `pull` right after their "started" line;
  `run_nightly.sh` pulls once up front on the laptop path (`NIGHTLY_STAGE=all`, not on a
  `NIGHTLY_SKIP_HISTORY` smoke run), in a nightly `scrape` shard inside the skip-unchanged rule
  (the previous daily file it reads is in the store; the weekly's full scope never takes that
  branch, so its shards read nothing), and in `assemble` right before ingest. The `scope` stage
  never touches the store: it reads nothing from it and its artifact is all of `snapshots/`
  (where the pulled tarball is cached), which the shard jobs download. Each commit path (`commit_results.sh`,
  the refresh, the verify) calls `push` on one line before its commit loop, replaces
  `git add history latest` with `store.py stage` (never stages `sales/` and `daily/`), and after the
  `git reset --hard origin/main` of a rejected push adds one `pull --force` (the reset puts the
  checkout's copy back while those files are still git-tracked).
- Concurrency: the nightly, weekly and listings refresh share the `scrape` concurrency group and
  the verify has its own but never writes the store, so on Actions only one run writes it at a
  time; the generation check guards against a laptop run landing meanwhile.
  Store before git, so a rejected git push can never leave `latest/` ahead of the store; the
  re-derive after a rejected push re-ingests onto the pushed generation (idempotent, the store
  hashes the same, nothing more to push). A store push that finds the release moved (exit 3) stops
  the run before anything is committed: the raw run is in the 7-day artifact and the recovery is
  the one PROGRESS.md section 4 already describes (pull, ingest, metrics, commit script), done by
  hand, because only a laptop run can cause it. Kept deliberately this small so the two branches
  that restructure the same runner scripts (sharding, run stats) rebase over it.
- Laptop runs: `python3 history/store.py pull` before `history/ingest.py` or any `analysis/` script
  on a fresh checkout (`gh` is logged in as samaygodika); `nightly/run_nightly.sh` pulls by itself
  and `nightly/commit_results.sh` pushes. `STORE_MODE=local` turns both off for offline or scratch
  work; a local-mode run can only commit while the store is still git-tracked (it refuses after the
  migration, so `latest/` cannot be published from a store nobody else can see). `STORE_BACKEND=dir:<path>`
  swaps the release for a folder laid out the same way (tests, an offline mirror).
- `init`: the first generation from a checkout that holds the git-tracked store (refuses if the
  release already has one). `status`: pointer vs local state, assets held.
- Store history: nobody needs git history of `sales/` or `daily/` for the pipeline. The two uses so
  far: `analysis/backfill_listing_first_seen.py` read `live_listings.csv`'s git history once (that
  file stays in git, and `first_seen` is a column now), and `analysis/data_checks.py` reads the
  seed commit's `history/sales/*.csv` with `git show` (the public repo's history is not rewritten,
  so that keeps working). After the move the record is the generations: three recent + one a month.
- For Sid: nothing. `latest/` is built by the same `metrics.py` from the same files; its column
  contract (README) does not change; his server keeps reading `latest/` by raw URL. The repo stops
  growing by the store, and every Actions checkout is ~300 MB instead of 1.45 GB once the migration
  commit lands.

## `last_seen` in `assets.csv`

Nothing reads it: not `metrics.py` (cards.csv has no first_seen / last_seen column), not
`coverage.py`, `flags.py`, the matchers, PokeSniper, or any `analysis/` script (they read identity
columns only). It equals the newest `history/daily/` file that holds the asset, which `metrics.py`
already computes (`latest_row`). So the column is dropped from `ASSET_COLS` and the per-run bump
goes; `assets.csv` then changes only when a card is new or alt.xyz corrects an identity field.
`first_seen` stays. Safe for `metrics.py` by construction; verified cell-for-cell (section below).

## Staging (nothing on main changes without Samay's OK)

1. **Commit 1 (this branch): the code.** `history/store.py`, the one-line `pull`s above, the push
   + the exclusion of `history/sales` and `history/daily` from `git add` in the three commit paths,
   `STORE_MODE` + `GH_TOKEN` on the steps that touch the store in the four workflows, the README,
   tests, the `last_seen` change. Default `STORE_MODE=release`. While the release has no generation
   yet, `pull` keeps the checkout's git-tracked store and says so, so a run before `init` behaves
   as today except that it no longer commits `sales/` and `daily/` (they stay as the checkout had
   them) -- which is why step 2 comes before the first run.
2. **Samay: `python3 history/store.py init`** from a checkout at origin/main (creates the release
   `store`, uploads generation 1 from the git-tracked store, ~430 MB). Then merge commit 1.
3. **First run, side by side.** The next nightly pulls generation 1 (the same bytes main holds),
   ingests, pushes generation 2, commits `latest/` + the small history files. Check: download that
   run's snapshot artifact, ingest it by hand on a checkout of the previous main (git-tracked store),
   rebuild, and diff `latest/` against the commit: byte-identical except `generated_at`. The same
   check is done in this branch's verification with a local dir backend (below). `store.py status`
   shows the generation and the pointer.
4. **Commit 2 (separate, after step 3 is clean): untrack.** `.gitignore` gains `history/sales/` and
   `history/daily/`; `git rm -r --cached history/sales history/daily` (the files stay on disk, the
   tree drops 1.15 GB; nothing is rewritten in history). Merging it is the only "delete from
   history/ on main" in the plan and needs Samay's explicit OK. Until it lands, the tracked copies
   on main simply go stale.
5. Optional later: `git gc --prune=now` on the laptop (405 MB of `tmp_pack_*` garbage, 12 GB
   `.git`); nothing about the public repo's history is rewritten by this plan.

## Verification done on this branch

See the commit message and `PROGRESS.md`: baseline rebuild byte-identical; after the `last_seen`
change `latest/` byte-identical (only `assets.csv` lost its column); a dir-backend rehearsal of
init -> pull into a wiped `history/` -> ingest of a real snapshot -> metrics -> push -> pull, with
`latest/` byte-identical to the same snapshot ingested on the git-tracked store, and the pushed
generation's files byte-identical to the ingested ones; the "store moved" exit 3 path; all
`history/test_*.py`; `analysis/audit_pct_change.py` PASS; Sid's parser loads `latest/`.
The `gh` write path (`release create` / `upload` / `delete-asset`) is exercised against the
documented flags only; `init` on Samay's laptop is its first real call.
