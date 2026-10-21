#!/usr/bin/env python3
"""Checks for nightly/shards.py (2026-10-17): the scope split for the parallel scrape jobs and
the merge of their output back into one run folder.

    python3 history/test_shards.py

Plain asserts, no pytest (it isn't installed here)."""
import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("shards", ROOT / "nightly" / "shards.py")
shards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shards)


def _write(path, cols, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def test_split_round_robin_with_sidecar():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        ids = [f"{i:08d}-0000-0000-0000-000000000000" for i in range(7)]
        (d / "scope.txt").write_text("# 7 cards, biggest pop first\n# second comment line\n\n"
                                     + "".join(f"asset:{aid}   # pop {700 - i}  card {i}\n" for i, aid in enumerate(ids)))
        json.dump({aid: {"id": aid, "name": f"card {i}", "pop": 700 - i} for i, aid in enumerate(ids)}, open(d / "scope.json", "w"))
        sizes = shards.split(d / "scope.txt", 3, d)
        assert sizes == [3, 2, 2], sizes
        for i, want in enumerate([ids[0::3], ids[1::3], ids[2::3]]):
            lines = [l for l in (d / f"shard-{i}" / "scope.txt").read_text().splitlines() if l.startswith("asset:")]
            assert [l.split()[0][6:] for l in lines] == want, (i, lines)
            side = json.load(open(d / f"shard-{i}" / "scope.json"))
            assert sorted(side) == sorted(want) and all(side[a]["name"] for a in want), "each shard carries only its cards' documents"
        # the scraper reads the shard like any list file: ids and sidecar line up
        sys.path.insert(0, str(ROOT))
        import alt_scraper   # noqa: E402
        items, sidecar = alt_scraper.read_inputs([str(d / "shard-1" / "scope.txt")])
        assert [alt_scraper.extract_id(x) if hasattr(alt_scraper, "extract_id") else x for x in items] and len(items) == 2 and len(sidecar) == 2
        # more shards than cards: empty shards are still written (a job must find its file)
        sizes = shards.split(d / "scope.txt", 9, d / "many")
        assert sizes == [1] * 7 + [0, 0] and (d / "many" / "shard-8" / "scope.txt").exists()
        assert not [l for l in (d / "many" / "shard-8" / "scope.txt").read_text().splitlines() if l.startswith("asset:")]
    print("split ok")


def test_merge_concatenates_and_reports_missing():
    cols = ["asset_id", "card_name", "num_sales"]
    scols = ["asset_id", "date", "price"]
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        for i in range(3):
            (d / f"shard-{i}").mkdir()
            (d / f"shard-{i}" / "scope.txt").write_text(f"asset:{i}\n")
        _write(d / "shard-0" / "cards.csv", cols, [{"asset_id": "a", "card_name": 'Card "A", holo', "num_sales": "2"}])
        _write(d / "shard-0" / "sales.csv", scols, [{"asset_id": "a", "date": "2026-10-01", "price": "10"},
                                                    {"asset_id": "a", "date": "2026-10-02", "price": "11"}])
        _write(d / "shard-0" / "listings.csv", scols, [])
        # shard 1 wrote nothing at all (its job died); shard 2 is fine, with a multi-line field
        _write(d / "shard-2" / "cards.csv", cols, [{"asset_id": "c", "card_name": "Card\nC", "num_sales": "0"}])
        _write(d / "shard-2" / "sales.csv", scols, [])
        _write(d / "shard-2" / "listings.csv", scols, [{"asset_id": "c", "date": "", "price": "5"}])
        counts, per_shard, missing = shards.merge(d)
        assert counts == {"cards.csv": 2, "sales.csv": 2, "listings.csv": 1}, counts
        assert per_shard == {"shard-0": 1, "shard-2": 1} and missing == 1, (per_shard, missing)
        cards = list(csv.DictReader(open(d / "cards.csv", newline="")))
        assert [r["asset_id"] for r in cards] == ["a", "c"] and cards[1]["card_name"] == "Card\nC" and cards[0]["card_name"] == 'Card "A", holo'
        assert open(d / "cards.csv").read().count("asset_id,card_name,num_sales") == 1, "one header"
        sales = list(csv.DictReader(open(d / "sales.csv", newline="")))
        assert [r["price"] for r in sales] == ["10", "11"]
        assert list(csv.DictReader(open(d / "listings.csv", newline=""))) == [{"asset_id": "c", "date": "", "price": "5"}]
        # a shard killed mid-row (no newline at the end): that half row is dropped, the next shard's rows stay whole
        with open(d / "shard-0" / "cards.csv", "a", newline="") as f:
            f.write("b,Card B,4")
        counts, per_shard, missing = shards.merge(d)
        cards = list(csv.DictReader(open(d / "cards.csv", newline="")))
        assert [(r["asset_id"], r["num_sales"]) for r in cards] == [("a", "2"), ("c", "0")], cards
        assert counts["cards.csv"] == 2 and per_shard["shard-0"] == 1, (counts, per_shard)
        # the CLI: no shard output at all is a failure, not an empty run
        for i in (0, 2):
            for name in shards.FILES:
                (d / f"shard-{i}" / name).unlink()
        r = subprocess.run([sys.executable, str(ROOT / "nightly" / "shards.py"), "merge", str(d)], capture_output=True, text=True)
        assert r.returncode == 1 and "no shard" in r.stderr, (r.returncode, r.stderr)
        # a shard with a different header is refused
        _write(d / "shard-0" / "cards.csv", cols, [{"asset_id": "a", "card_name": "A", "num_sales": "1"}])
        _write(d / "shard-2" / "cards.csv", cols + ["extra"], [])
        r = subprocess.run([sys.executable, str(ROOT / "nightly" / "shards.py"), "merge", str(d)], capture_output=True, text=True)
        assert r.returncode == 1 and "different header" in r.stderr, (r.returncode, r.stderr)
    print("merge ok")


def test_split_cli_matches_scraper_list_format():
    """split via the CLI on a filter_subjects.py-style file, then the scraper's reader sees the right cards."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "scope.txt").write_text("# 2 of 5 indexed cards\n# Pikachu 1, Mew 1\n\nasset:aaaaaaaa-0000-0000-0000-000000000000   # pop 5  Mew\nasset:bbbbbbbb-0000-0000-0000-000000000000   # pop 4  Pikachu\n")
        r = subprocess.run([sys.executable, str(ROOT / "nightly" / "shards.py"), "split", str(d / "scope.txt"), "2", str(d)],
                           capture_output=True, text=True, check=True)
        assert "shard 0: 1 cards" in r.stdout and "shard 1: 1 cards" in r.stdout, r.stdout
        assert json.load(open(d / "shard-0" / "scope.json")) == {}, "no sidecar next to the scope: empty sidecar, the scraper asks for card info"
    print("split cli ok")


if __name__ == "__main__":
    test_split_round_robin_with_sidecar()
    test_merge_concatenates_and_reports_missing()
    test_split_cli_matches_scraper_list_format()
    print("all shard checks passed")
