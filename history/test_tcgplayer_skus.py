#!/usr/bin/env python3
"""Checks for tcgplayer_skus.py (2026-10-06): SKU variants -> printings, the cache's refresh
rule, and the catalog's printings = TCGCSV's priced printings + the SKU ones.

    python3 history/test_tcgplayer_skus.py

Plain asserts, no network."""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tcgplayer_ids    # noqa: E402
import tcgplayer_skus   # noqa: E402


def test_printings_of():
    sku = lambda v, lang="English": {"sku": 1, "condition": "Near Mint", "variant": v, "language": lang}
    # Neo Destiny Shining Mewtwo (89167): 10 SKUs, two printings, the 1st Edition one unpriced
    d = {"skus": [sku("Unlimited Holofoil"), sku("1st Edition Holofoil"), sku("Unlimited Holofoil")]}
    assert tcgplayer_skus.printings_of(d) == "1st Edition Holofoil|Unlimited Holofoil"
    assert tcgplayer_skus.printings_of({"skus": [sku("Holofoil", "Japanese")]}) == "Holofoil"   # no English SKU
    assert tcgplayer_skus.printings_of({"skus": [sku("Normal"), sku("Holofoil", "German")]}) == "Normal"
    assert tcgplayer_skus.printings_of({"skus": []}) == "" and tcgplayer_skus.printings_of({}) == ""


def test_due():
    now = datetime(2026, 10, 6, tzinfo=timezone.utc)
    cache = {1: {"fetched_at": "2026-10-01T00:00:00+00:00"}, 2: {"fetched_at": "2026-06-01T00:00:00+00:00"}}
    assert tcgplayer_skus.due(cache, [1, 2, 3], now) == [2, 3]   # 2 is older than REFRESH_DAYS, 3 never asked


def test_catalog_unions_sku_printings():
    with tempfile.TemporaryDirectory() as d:
        src, cache = Path(d) / "tcgcsv", Path(d) / "skus.csv"
        src.mkdir()
        (src / "last-updated.txt").write_text("2026-10-06T00:00:00+0000")
        (src / "groups.json").write_text(json.dumps({"results": [
            {"groupId": 1396, "name": "Neo Destiny", "publishedOn": "2002-02-28T00:00:00"}]}))
        (src / "groups_jp.json").write_text(json.dumps({"results": []}))
        ext = lambda n, r: [{"name": "Number", "value": n}, {"name": "Rarity", "value": r}]
        (src / "1396.json").write_text(json.dumps({"results": [
            {"productId": 89167, "name": "Shining Mewtwo", "extendedData": ext("109/105", "Shining Holo Rare")},
            {"productId": 89999, "name": "Dark Gengar", "extendedData": ext("6/105", "Holo Rare")}]}))
        (src / "1396.prices.json").write_text(json.dumps({"results": [
            {"productId": 89167, "subTypeName": "Unlimited Holofoil"},
            {"productId": 89999, "subTypeName": "Unlimited Holofoil"}]}))
        tcgplayer_skus.save({89167: {"product_id": 89167, "printings": "1st Edition Holofoil|Unlimited Holofoil",
                                     "status": "ok", "fetched_at": "2026-10-06T00:00:00+00:00"}}, cache)
        assert tcgplayer_skus.products(src) == [89167, 89999]
        old = tcgplayer_ids.SKU_CACHE
        tcgplayer_ids.SKU_CACHE = cache
        try:
            _, (by_number, *_), _ = tcgplayer_ids.load_catalog(src)
        finally:
            tcgplayer_ids.SKU_CACHE = old
        got = {p["productId"]: p["printings"] for ps in by_number.values() for p in ps}
        assert got == {89167: "1st Edition Holofoil|Unlimited Holofoil",   # SKU list adds the unpriced run
                       89999: "Unlimited Holofoil"}, got                   # not in the cache yet: TCGCSV only


if __name__ == "__main__":
    test_printings_of()
    test_due()
    test_catalog_unions_sku_printings()
    print("all tcgplayer sku checks passed")
