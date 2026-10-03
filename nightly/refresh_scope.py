#!/usr/bin/env python3
"""Scope of a listings refresh: every card latest/cards.csv shows with a running auction,
one list per grade, in the scraper's input format plus the .json sidecar that lets it skip
the per-card info request (2026-10-03).

    python3 nightly/refresh_scope.py latest/cards.csv <out_dir> [limit]

Writes <out_dir>/refresh_psa10.txt (+.json) for cards with live_auction_count > 0 and
<out_dir>/refresh_psa9.txt (+.json) for psa9_live_auction_count > 0. Why auctions only:
an auction ends at a known time or is pulled early, and alt.xyz drops it within hours, so
re-checking these cards between nightlies is what keeps the "live" pills honest; a Buy
It Now has no end and alt.xyz re-serves it anyway (its age is lowest_bin_first_seen)."""
import csv
import json
import sys
from pathlib import Path

GRADES = {"10": "live_auction_count", "9": "psa9_live_auction_count"}


def main():
    cards_path, out = Path(sys.argv[1]), Path(sys.argv[2])
    limit = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else 0
    out.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(open(cards_path, newline="", encoding="utf-8")))
    for g, col in GRADES.items():
        picked = [r for r in rows if (r.get(col) or "0") not in ("", "0")]
        picked.sort(key=lambda r: r.get("next_auction_end" if g == "10" else "psa9_next_auction_end") or "~")   # soonest end first
        if limit:
            picked = picked[:limit]
        docs = {}
        with open(out / f"refresh_psa{g}.txt", "w") as f:
            f.write(f"# listings refresh scope: cards with a running PSA {g} auction in {cards_path}, soonest end first\n")
            for r in picked:
                f.write(f"asset:{r['asset_id']}   # {r['card_name']}\n")
                docs[r["asset_id"]] = {"id": r["asset_id"], "name": r["card_name"], "year": r["year"], "subject": r["subject"],
                                       "category": "POKEMON_CARDS", "brand": r["set"], "variety": r["variety"],
                                       "cardNumber": r["card_number"], "pop": r.get("index_total_pop") or None,
                                       "externalTransactionCount": r.get("index_transaction_count") or None}
        with open(out / f"refresh_psa{g}.json", "w") as f:
            json.dump(docs, f)
        print(f"PSA {g}: {len(picked)} card(s) with a running auction -> {out / f'refresh_psa{g}.txt'}")


if __name__ == "__main__":
    main()
