#!/usr/bin/env python3
"""Checks for history/coverage.py: the per-character rows over a made-up feed, and the
rank_le2013 / rank_all_years columns (2026-10-17).

    python3 history/test_coverage.py

Plain asserts, no pytest."""
import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coverage  # noqa: E402

FEED_COLS = ["asset_id", "card_name", "set", "variety", "subject", "year", "pop_at_grade", "cap_price", "scraped_at",
             "cap_price_30d_ago", "cap_price_60d_ago", "cap_price_90d_ago", "cap_price_180d_ago", "cap_price_1y_ago"]


def row(aid, subject, year, pop, price, name=None, set_="Pokemon Base Set", ago=""):
    return {"asset_id": aid, "card_name": name or f"{year} Pokemon {subject}", "set": set_, "variety": "", "subject": subject,
            "year": year, "pop_at_grade": pop, "cap_price": price, "scraped_at": "2026-10-16T06:50:05",
            **{f"cap_price_{w}_ago": ago for w in ("30d", "60d", "90d", "180d", "1y")}}


FEED = [
    row("a1", "Charizard", "1999", "100", "1000"),            # cap 100,000 (<= 2013)
    row("a2", "Charizard", "2023", "1000", "500"),            # cap 500,000 (modern only)
    row("a3", "Pikachu", "2000", "300", "400"),               # cap 120,000 (<= 2013)
    row("a4", "Mew", "2001", "60", "2000", ago="1000"),       # cap 120,000 (<= 2013): ties Pikachu on the <= 2013 cap
    row("a5", "Mewtwo", "2002", "5", "20"),                   # cap 100 (<= 2013)
    row("a6", "Gengar", "2010", "", "50"),                    # blank pop: unknown, no cap
    row("a7", "Gengar", "2011", "0", ""),                     # real zero, no price
    row("a8", "Pikachu", "2000", "9", "100", name="2000 Pokemon Japanese Pikachu", set_="Pokemon Japanese Neo"),   # not English: out
]


def test_ranks_and_order():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        feed, subjects, out = d / "cards.csv", d / "subjects.txt", d / "characters.csv"
        with open(feed, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FEED_COLS)
            w.writeheader()
            w.writerows(FEED)
        subjects.write_text("Charizard\nPikachu\nMew\nMewtwo\nGengar\nSnorlax\n")
        table = coverage.build(feed=feed, subjects=subjects, out=out, extra=None)
        got = list(csv.DictReader(open(out)))
        cols = list(got[0].keys())
        assert cols[-2:] == ["rank_le2013", "rank_all_years"], cols      # the new columns, at the end
        assert cols[:9] == ["character", "alt_english_rows", "with_psa10_pop", "zero_psa10_pop", "no_psa_rows", "with_price",
                            "alt_market_cap_usd", "alt_market_cap_le2013_usd", "newest_scrape"]
        by = {r["character"]: r for r in got}
        # the counts and caps as before
        assert (by["Charizard"]["alt_english_rows"], by["Charizard"]["alt_market_cap_usd"], by["Charizard"]["alt_market_cap_le2013_usd"]) == ("2", "600000", "100000")
        assert (by["Gengar"]["no_psa_rows"], by["Gengar"]["zero_psa10_pop"], by["Gengar"]["with_price"], by["Gengar"]["alt_market_cap_usd"]) == ("1", "1", "1", "0")
        assert by["Pikachu"]["alt_english_rows"] == "1"                   # the Japanese row is out
        assert by["Mew"]["mkt_cap_chg_30d_pct_le2013"] == "100.0" and by["Mew"]["mkt_cap_chg_30d_coverage_le2013"] == "1.0"
        # file order unchanged: by the all-years cap, largest first
        assert [r["character"] for r in got] == ["Charizard", "Pikachu", "Mew", "Mewtwo", "Gengar", "Snorlax"]
        # rank_all_years follows that cap; rank_le2013 the <= 2013 cap, ties by name (Mew before Pikachu at 120,000),
        # and a 0 cap still gets a rank (last, by name)
        assert [r["rank_all_years"] for r in got] == ["1", "3", "2", "4", "5", "6"]   # Mew before Pikachu on the 120,000 tie
        assert {r["character"]: r["rank_le2013"] for r in got} == {"Mew": "1", "Pikachu": "2", "Charizard": "3", "Mewtwo": "4",
                                                                   "Gengar": "5", "Snorlax": "6"}
        assert sorted(int(r["rank_le2013"]) for r in got) == list(range(1, 7))
        assert table[0]["character"] == "Charizard" and table[0]["rank_le2013"] == 3


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} test(s) passed")
