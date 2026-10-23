#!/usr/bin/env python3
"""The cards the nightly asks for at EVERY PSA grade (2026-10-21): the Gold Star category.

    python3 nightly/all_grades_scope.py <all_pokemon_cards.json> <out_list.txt> [--add-to <scope.txt>]

Reads the index listing's sidecar (alt_scraper.py --list '*') and writes, in the scraper's own
list format plus its .json sidecar, every card whose card type is one of CARD_TYPES by the
feed's own classifier (history/flags.py card_type: the same "Gold Star" the PSA 9 category
log uses, i.e. a card dated 2008 or earlier named "... Gold Star ..." or "... Star #n" /
"... Star Holo", or at a Gold Star's own set number, flags.GOLD_STAR_NUMBERS, since 2026-10-22;
the 2021 Celebrations reprints are not the category). The scraper takes the
file as --all-grades-for and pulls those cards' sales at PSA 1, 1.5, ... 9, 10 and their pop
at every grade (cards.csv pops_by_grade); history/ingest.py keeps the pops in
history/grade_pops.csv and history/all_grades.py builds latest/all_grades/.

--add-to <scope.txt>: cards of the list that the nightly scope misses (a subject the species
lists do not name, e.g. the German "Glurak-Holo" record of the Dragon Frontiers Charizard)
are appended to the scope and its .json sidecar, so they are scraped nightly like the rest.
Run before nightly/shards.py split. Standard library only.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "history"))
import flags  # noqa: E402

CARD_TYPES = flags.ALL_GRADES_TYPES   # ("Gold Star",): the one list, shared with history/all_grades.py


def as_card(doc):
    """An index document as the row shape flags.card_type reads (latest/cards.csv's columns)."""
    return {"card_name": doc.get("name") or "", "set": doc.get("brand") or "", "subject": doc.get("subject") or "",
            "year": doc.get("year") if doc.get("year") is not None else "", "card_number": doc.get("cardNumber") or ""}


def select(index, card_types=CARD_TYPES):
    """The index documents of the wanted card types, biggest pop first (the scraper's usual order)."""
    docs = [d for d in index.values() if flags.card_type(as_card(d)) in card_types]
    docs.sort(key=lambda d: (-(d.get("pop") or 0), d.get("name") or ""))
    return docs


def line(d):
    return f"asset:{d['id']}   # pop {d.get('pop', '?')}  {d.get('name')}\n"


KEEP = ("id", "name", "year", "subject", "category", "brand", "variety", "cardNumber", "pop", "externalTransactionCount")


def write_list(path, docs, card_types=CARD_TYPES):
    path = Path(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {len(docs)} cards asked at every PSA grade: card type {' / '.join(card_types)} (history/flags.py card_type)\n\n")
        for d in docs:
            f.write(line(d))
    with open(path.with_suffix(".json"), "w", encoding="utf-8") as f:
        json.dump({d["id"]: {k: d.get(k) for k in KEEP} for d in docs}, f)


def add_to_scope(scope_path, docs):
    """Append the docs the scope list lacks (and their sidecar entries). Returns how many."""
    scope_path = Path(scope_path)
    have = set()
    for l in scope_path.read_text(encoding="utf-8").splitlines():
        l = l.strip()
        if l.startswith("asset:"):
            have.add(l.split()[0][6:])
    missing = [d for d in docs if d["id"] not in have]
    if missing:
        with open(scope_path, "a", encoding="utf-8") as f:
            f.write(f"# + {len(missing)} card(s) of the every-grade list (nightly/all_grades_scope.py) the subject lists miss\n")
            for d in missing:
                f.write(line(d))
        sc = scope_path.with_suffix(".json")
        sidecar = json.load(open(sc, encoding="utf-8")) if sc.exists() else {}
        for d in missing:
            sidecar[d["id"]] = {k: d.get(k) for k in KEEP}
        with open(sc, "w", encoding="utf-8") as f:
            json.dump(sidecar, f)
    return len(missing)


def main(argv):
    if len(argv) < 3 or (len(argv) > 3 and (len(argv) != 5 or argv[3] != "--add-to")):
        sys.exit(__doc__)
    index_path, out_path = Path(argv[1]), Path(argv[2])
    index = json.load(open(index_path, encoding="utf-8"))
    docs = select(index)
    write_list(out_path, docs)
    print(f"{len(docs)} of {len(index)} indexed cards are {' / '.join(CARD_TYPES)} -> {out_path} (+ {out_path.with_suffix('.json').name})")
    if len(argv) == 5:
        added = add_to_scope(argv[4], docs)
        print(f"{added} of them were not in {argv[4]}: appended")


if __name__ == "__main__":
    main(sys.argv)
