#!/usr/bin/env python3
"""Per-character alt.xyz coverage and alt-side market cap -> latest/characters.csv

One row per name in nightly/subjects.txt (PokeSniper's roster candidates),
computed from latest/cards.csv with no matcher involved, so the numbers are
uniform across characters and can rank them fairly. Replaces the one-off
handoff/2026-09-18-top100-alt-coverage.csv; PokeSniper's
scripts/lib/altSide.js reproduces the same definition.

  character                 name as written in nightly/subjects.txt
  alt_english_rows          English TCG rows whose `subject` contains the name as a
                            whole word (hyphen and space are interchangeable, so
                            "Ho-Oh" also matches alt.xyz's "Ho Oh"; "Mew" never
                            matches "Mewtwo"; multi-Pokemon cards count for each)
  with_psa10_pop            rows with pop_at_grade > 0
  zero_psa10_pop            rows with pop_at_grade = 0 (PSA rows exist, no 10s)
  no_psa_rows               rows with a blank pop (alt.xyz has no PSA rows: unknown)
  with_price                rows with a last_sale_price
  alt_market_cap_usd        sum of pop_at_grade x last_sale_price over every row
                            that has both, all years
  alt_market_cap_le2013_usd the same over rows dated 2013 or earlier (or undated),
                            the cutoff PokeSniper ranks its roster on
  newest_scrape             newest scraped_at date among the rows

The English / non-TCG filter is the one pokesniper-server/src/lib/samayData.js
applies (same regexes), so "English rows" here is what the server can match.
"""
import csv
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
FEED = HERE / "latest" / "cards.csv"
SUBJECTS = HERE / "nightly" / "subjects.txt"
OUT = HERE / "latest" / "characters.csv"
VINTAGE_CUTOFF = 2013

FOREIGN = re.compile(r"\b(french|german|spanish|italian|ita|portuguese|dutch|korean|japanese|jpn|chinese|cantonese|mandarin|indonesian|russian|swedish|norwegian|thai|taiwan(ese)?|hong\s?kong)\b", re.I)
NON_TCG = re.compile(r"sticker|topps|amada|bandai|carddass|merlin|action flipz|burger king|hanafuda|tv animation|movie edition|lenticular|bubble gum|topsun|viz video|pop-ups|\btv\b", re.I)

COLS = ["character", "alt_english_rows", "with_psa10_pop", "zero_psa10_pop", "no_psa_rows", "with_price",
        "alt_market_cap_usd", f"alt_market_cap_le{VINTAGE_CUTOFF}_usd", "newest_scrape"]


def english_tcg(row):
    return not NON_TCG.search(row.get("card_name") or "") and not FOREIGN.search(" ".join(filter(None, (row.get("card_name"), row.get("set"), row.get("variety")))))


def norm(s):
    """lower-case, every run of non-alphanumerics -> one space, padded, so a
    whole-word test is `f" {norm(name)} " in norm(subject)`."""
    return " " + re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip() + " "


def read_names(path):
    return [l.strip() for l in path.read_text().splitlines() if l.strip() and not l.startswith("#")]


def build(feed=FEED, subjects=SUBJECTS, out=OUT):
    names = read_names(subjects)
    rows = [r for r in csv.DictReader(open(feed, newline="", encoding="utf-8")) if english_tcg(r)]
    for r in rows:
        r["_subj"] = norm(r.get("subject"))
    table = []
    for name in names:
        key = norm(name)
        mine = [r for r in rows if key in r["_subj"]]
        cap = cap13 = 0.0
        stat = dict.fromkeys(COLS[1:], 0)
        stat["newest_scrape"] = ""
        for r in mine:
            pop, price = r.get("pop_at_grade", ""), r.get("last_sale_price", "")
            if pop == "":
                stat["no_psa_rows"] += 1
            elif float(pop) == 0:
                stat["zero_psa10_pop"] += 1
            else:
                stat["with_psa10_pop"] += 1
            if price != "":
                stat["with_price"] += 1
            if pop != "" and price != "":
                v = float(pop) * float(price)
                cap += v
                try:
                    year = int(r.get("year") or 0)
                except ValueError:
                    year = 0
                if year <= VINTAGE_CUTOFF:
                    cap13 += v
            stat["newest_scrape"] = max(stat["newest_scrape"], (r.get("scraped_at") or "")[:10])
        stat["alt_english_rows"] = len(mine)
        stat["alt_market_cap_usd"] = round(cap)
        stat[f"alt_market_cap_le{VINTAGE_CUTOFF}_usd"] = round(cap13)
        table.append({"character": name, **stat})
    table.sort(key=lambda t: -t["alt_market_cap_usd"])
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(table)
    print(f"{len(table)} characters -> {out}")
    return table


if __name__ == "__main__":
    t = build()
    for row in t[:10]:
        print(f"  {row['character']:<12} rows {row['alt_english_rows']:>4}  cap ${row['alt_market_cap_usd'] / 1e6:,.0f}M  (<= {VINTAGE_CUTOFF}: ${row[f'alt_market_cap_le{VINTAGE_CUTOFF}_usd'] / 1e6:,.0f}M)")
    sys.exit(0)
