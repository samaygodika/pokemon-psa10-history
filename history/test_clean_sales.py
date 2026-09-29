#!/usr/bin/env python3
"""Checks for metrics.write_clean_sales (2026-09-29): latest/clean_sales/<month>.csv
+ manifest.json, the compact history PokeSniper's salesHistory.js reads.

    python3 history/test_clean_sales.py

Plain asserts, no pytest."""
import hashlib
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics  # noqa: E402

A, B = "aa000000-0000-0000-0000-000000000001", "bb000000-0000-0000-0000-000000000002"
BY_GRADE = {
    "10": {A: [(date(2026, 8, 3), 1200.0, "eBay"), (date(2026, 9, 1), 1250.5, "eBay"), (date(2026, 9, 1), 1250.5, "eBay")],
           B: [(date(2021, 1, 31), 99.99, "PWCC")]},
    "9": {A: [(date(2026, 9, 14), 410.0, "eBay"), (date(2026, 9, 28), 399.5, "Goldin")]},
}


def read_back(d):
    """{(asset, grade): [(date, price)]} from the files, the way a reader would parse them."""
    out = {}
    for f in sorted(d.glob("*.csv")):
        y, m = map(int, f.stem.split("-"))
        lines = f.read_text().splitlines()
        assert lines[0] == "asset_id,grade,sales", lines[0]
        for line in lines[1:]:
            aid, grade, sales = line.split(",")
            for s in sales.split(" "):
                dd, price = s.split(":")
                out.setdefault((aid, grade), []).append((date(y, m, int(dd)), float(price)))
    return out


def test_round_trip_and_manifest():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        months, n = metrics.write_clean_sales(d, BY_GRADE)
        assert (months, n) == (3, 6), (months, n)
        got = read_back(d)
        want = {(aid, g): [(s[0], s[1]) for s in lst] for g, by in BY_GRADE.items() for aid, lst in by.items()}
        assert got == want, got                          # every sale back, repeats kept, grades apart
        assert (d / "2026-09.csv").read_text() == (
            "asset_id,grade,sales\n"
            f"{A},10,1:1250.5 1:1250.5\n"
            f"{A},9,14:410 28:399.5\n")
        man = json.loads((d / "manifest.json").read_text())
        assert sorted(man["months"]) == ["2021-01", "2026-08", "2026-09"], man
        for month, m in man["months"].items():
            data = (d / f"{month}.csv").read_bytes()
            assert m["sha256"] == hashlib.sha256(data).hexdigest() and m["bytes"] == len(data), month
        assert man["months"]["2026-09"]["sales"] == 4


def test_deterministic_and_stale_months_removed():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        (d / "2019-05.csv").write_text("asset_id,grade,sales\nold,10,1:1\n")   # a month no sale is in any more
        metrics.write_clean_sales(d, BY_GRADE)
        first = {f.name: f.read_bytes() for f in d.iterdir()}
        metrics.write_clean_sales(d, BY_GRADE)
        second = {f.name: f.read_bytes() for f in d.iterdir()}
        assert first == second                          # same sales -> same bytes, same manifest
        assert "2019-05.csv" not in second, sorted(second)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
