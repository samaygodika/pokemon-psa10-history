#!/usr/bin/env python3
"""Filter a full alt.xyz index listing (from `alt_scraper.py --list '*'`) down
to the subjects in subjects.txt, writing a list file + sidecar the scraper
accepts as-is. Whole-word match on `subject`, case-insensitive — identical to
pokesniper-server's samayData.js, so what gets scraped is exactly what the
server can use.

usage: filter_subjects.py <all_pokemon_cards.json> <subjects.txt> <out_list.txt> [limit] [extra_subjects.txt] [extra_max_year]

The optional extra list (nightly/vintage_species.txt) adds species whose cards
are only wanted up to a year cap — PokeSniper's Categories tab needs every
notable pre-2014 card of ~400 more species refreshed nightly, but not their
modern printings, which the weekly full run covers.
"""
import json, re, sys
from pathlib import Path

index_path, subjects_path, out_path = map(Path, sys.argv[1:4])
limit = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] else 0
extra_path = Path(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] else None
extra_max_year = int(sys.argv[6]) if len(sys.argv) > 6 else 2013


def read_list(path):
    return [l.strip() for l in path.read_text().splitlines() if l.strip() and not l.startswith("#")]


subjects = read_list(subjects_path)
rx = re.compile(r"\b(" + "|".join(re.escape(s) for s in subjects) + r")\b", re.I)
index = json.load(open(index_path))
docs = [d for d in index.values() if rx.search(d.get("subject") or "")]
n_extra = 0
if extra_path:
    extra = read_list(extra_path)
    rx_extra = re.compile(r"\b(" + "|".join(re.escape(s) for s in extra) + r")\b", re.I)
    seen = {d["id"] for d in docs}
    for d in index.values():
        if d["id"] in seen or not rx_extra.search(d.get("subject") or ""):
            continue
        try:
            if int(d.get("year") or 0) > extra_max_year:
                continue
        except ValueError:
            continue
        docs.append(d)
        n_extra += 1
docs.sort(key=lambda d: (-(d.get("pop") or 0), d.get("name") or ""))  # biggest pops first: the interesting rows land early if a run is cut short
if limit:
    docs = docs[:limit]

per_subject = {s: sum(1 for d in docs if re.search(r"\b" + re.escape(s) + r"\b", d.get("subject") or "", re.I)) for s in subjects}
with open(out_path, "w") as f:
    f.write(f"# {len(docs)} of {len(index)} indexed cards: subject matches one of {len(subjects)} names in {subjects_path.name}" + (f", plus {n_extra} cards dated <= {extra_max_year} of {len(extra)} species in {extra_path.name}" if extra_path else "") + "\n")
    f.write("# " + ", ".join(f"{s} {n}" for s, n in per_subject.items()) + "\n\n")
    for d in docs:
        f.write(f"asset:{d['id']}   # pop {d.get('pop', '?')}  {d.get('name')}\n")
keep = ("id", "name", "year", "subject", "category", "brand", "variety", "cardNumber", "pop", "externalTransactionCount")
with open(out_path.with_suffix(".json"), "w") as f:
    json.dump({d["id"]: {k: d.get(k) for k in keep} for d in docs}, f)
zero = [s for s, n in per_subject.items() if n == 0]
print(f"{len(docs)} of {len(index)} cards match {len(subjects)} subjects" + (f" + {n_extra} pre-{extra_max_year + 1} cards of {len(extra)} extra species" if extra_path else "") + f" -> {out_path}" + (f"  (limit {limit})" if limit else ""))
if zero:
    print("WARNING: no index entries for:", ", ".join(zero))
