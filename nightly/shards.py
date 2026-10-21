#!/usr/bin/env python3
"""Split a scrape scope into shards for parallel GitHub Actions jobs, and merge the shards'
output back into one run folder (2026-10-17).

    python3 nightly/shards.py split <scope.txt> <N> <out_dir>     # -> <out_dir>/shard-<i>/scope.txt (+ .json sidecar)
    python3 nightly/shards.py merge <out_dir>                      # <out_dir>/shard-*/{cards,sales,listings}.csv -> <out_dir>/

Why: one GitHub Actions job may run 6 h at most, and the weekly full scrape (~67k cards) sat at
332-338 min against a 350 min timeout in October 2026, while the nightly (~34k cards plus PSA 9
sales and listings) took 177-256 min and grew with every step added. The workflows now run the
index listing in one job, the scrape in N shard jobs at once, and the ingest/rebuild/commit in a
last job, so no single job carries the whole night (nightly/run_nightly.sh, NIGHTLY_STAGE).

split deals the scope's cards round-robin (card i -> shard i % N): the scope is sorted by pop,
biggest first, so every shard gets the same mix of busy and quiet cards and keeps "biggest pops
first" within itself (a run cut short still has the interesting rows). Each shard's .json
sidecar holds just its cards' search documents, so the scraper skips the per-card info request
as before. Comment and blank lines of the scope are not carried (the scraper ignores them).

merge concatenates each shard's cards.csv, sales.csv and listings.csv (one header), in shard
order, into the run folder the rest of the pipeline reads. A shard whose cards.csv is missing
(its job died before writing anything) is reported and skipped: the cards it held are simply not
in this run, like cards whose requests failed; with no shard output at all merge exits 1.
"""
import csv
import json
import shutil
import sys
from pathlib import Path

FILES = ("cards.csv", "sales.csv", "listings.csv")


def split(scope, n, out):
    scope, out = Path(scope), Path(out)
    lines = [l for l in scope.read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]
    sidecar_path = scope.with_suffix(".json")
    sidecar = json.load(open(sidecar_path)) if sidecar_path.exists() else {}
    shards = [lines[i::n] for i in range(n)]
    for i, part in enumerate(shards):
        d = out / f"shard-{i}"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "scope.txt", "w") as f:
            f.write(f"# shard {i} of {n}: {len(part)} of {len(lines)} cards in {scope.name} (every {n}th card from the {i + 1}th)\n")
            f.write("\n".join(part) + ("\n" if part else ""))
        ids = [l.split()[0].split(":", 1)[-1] for l in part]
        with open(d / "scope.json", "w") as f:
            json.dump({k: sidecar[k] for k in ids if k in sidecar}, f)
        print(f"shard {i}: {len(part)} cards -> {d / 'scope.txt'}")
    return [len(p) for p in shards]


def ends_with_newline(path):
    """False when the file's last byte is not a newline (a row the writer never finished)."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        if f.tell() == 0:
            return True
        f.seek(-1, 2)
        return f.read(1) == b"\n"


def merge(out):
    out = Path(out)
    dirs = sorted((d for d in out.glob("shard-*") if d.is_dir()), key=lambda d: (len(d.name), d.name))
    present = [d for d in dirs if (d / "cards.csv").is_file()]
    for d in dirs:
        if d not in present:
            print(f"WARNING: {d.name} has no cards.csv (its job wrote nothing); its cards are not in this run", file=sys.stderr)
    if not present:
        sys.exit(f"merge: no shard under {out} has a cards.csv")
    counts, merged_cards = {}, {}
    for name in FILES:
        rows = 0
        with open(out / name, "w", newline="", encoding="utf-8") as w:
            header = None
            for d in present:
                p = d / name
                if not p.is_file():
                    continue
                with open(p, newline="", encoding="utf-8") as r:
                    first = r.readline()
                    if not first:
                        continue
                    if header is None:
                        header = first
                        w.write(first)
                    elif first != header:
                        sys.exit(f"merge: {p} has a different header than the first shard")
                    if ends_with_newline(p):
                        shutil.copyfileobj(r, w)
                        dropped = 0
                    else:
                        # A shard killed mid-write (the job's timeout) leaves a last line without its newline,
                        # and the next shard's first row would be glued onto it (2026-10-21): that line is
                        # dropped (its card is "not in the run", like one whose requests failed; a quoted
                        # field with a newline inside is a complete line, the scraper flushes whole rows).
                        body = r.read()
                        cut = body.rfind("\n") + 1
                        print(f"merge: {p} ends mid-row, dropped its last {len(body) - cut} bytes", file=sys.stderr)
                        w.write(body[:cut])
                        dropped = 1
                if name == "cards.csv":
                    with open(p, newline="", encoding="utf-8") as f:
                        merged_cards[d.name] = max(sum(1 for _ in csv.reader(f)) - 1 - dropped, 0)
        counts[name] = rows
    for name in FILES:
        with open(out / name, newline="", encoding="utf-8") as f:
            counts[name] = max(sum(1 for _ in csv.reader(f)) - 1, 0)
    per_shard = {d.name: merged_cards.get(d.name, 0) for d in present}   # the rows of each shard that made it into the merge
    print(f"merged {len(present)} of {len(dirs)} shard(s) into {out}: " + ", ".join(f"{k} {v} rows" for k, v in counts.items())
          + " (" + ", ".join(f"{k} {v}" for k, v in per_shard.items()) + ")")
    return counts, per_shard, len(dirs) - len(present)


def main():
    if len(sys.argv) >= 5 and sys.argv[1] == "split":
        split(sys.argv[2], int(sys.argv[3]), sys.argv[4])
    elif len(sys.argv) == 3 and sys.argv[1] == "merge":
        merge(sys.argv[2])
    else:
        sys.exit(__doc__.strip().splitlines()[0] + "\n\n" + "\n".join(__doc__.strip().splitlines()[2:4]))


if __name__ == "__main__":
    main()
