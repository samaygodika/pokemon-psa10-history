#!/usr/bin/env python3
"""Checks for history/manifest.py (2026-10-17): summary.json["files"] and the per-shard
manifests of series/, recent_sales/ and recent_sales_psa9/.

    python3 history/test_manifest.py

Plain asserts, no pytest."""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import manifest  # noqa: E402

CARDS = 'input,asset_id,card_name\nasset:a,a,"Palkia G\nLv.X"\nasset:b,b,Umbreon\n'    # a quoted newline is not a row
SERIES_00 = "asset_id,week_start,n_sales,median_price,low,high\n00aa,2026-09-07,2,100,90,110\n00aa,2026-09-14,1,120,120,120\n"
SERIES_01 = "asset_id,week_start,n_sales,median_price,low,high\n01bb,2026-09-07,1,5,5,5\n"
RECENT_00 = "asset_id,date,price,source,sale_type,status,outlier,url\n00aa,2026-09-15,120,eBay,AUCTION,ok,0,https://x/1\n"
SUMMARY = {"data_date": "2026-10-16", "generated_at": "2026-10-17T00:19:45", "assets": 2, "rows_with": {"price_chg_30d_pct": 1}}


def sha(b):
    return hashlib.sha256(b).hexdigest()


def make_latest(d):
    (d / "cards.csv").write_text(CARDS)
    (d / "characters.csv").write_text("character,alt_english_rows\nPalkia,1\n")
    (d / "card_catalog.csv").write_text("asset_id,tcgplayer_id\na,1\nb,2\n,3\n")
    (d / "tcgplayer_sets.csv").write_text("group_id,name\n1,Base Set\n")
    (d / "tcgplayer_ids.csv").write_text("asset_id,tcgplayer_id\na,1\n")
    for sub, files in {"series": {"00": SERIES_00, "01": SERIES_01}, "recent_sales": {"00": RECENT_00},
                       "recent_sales_psa9": {}}.items():
        (d / sub).mkdir()
        for stem, text in files.items():
            (d / sub / f"{stem}.csv").write_text(text)
    (d / "clean_sales").mkdir()
    (d / "clean_sales" / "manifest.json").write_text(json.dumps({"columns": ["asset_id", "grade", "sales"], "sales": "...",
                                                                 "months": {"2026-08": {"sha256": "x", "bytes": 1, "sales": 1},
                                                                            "2026-09": {"sha256": "y", "bytes": 2, "sales": 2}}}))
    (d / "summary.json").write_text(json.dumps(SUMMARY, indent=2))


def test_csv_rows():
    assert manifest.csv_rows(CARDS.encode()) == 2
    assert manifest.csv_rows(b"a,b\n") == 0 and manifest.csv_rows(b"") == 0
    assert manifest.csv_rows(b"a,b\r\n1,2\r\n3,4\r\n") == 2


def test_build():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        make_latest(d)
        files = manifest.build(d)
        summary = json.loads((d / "summary.json").read_text())
        # every other key kept exactly as metrics.py wrote it, `files` appended
        assert {k: v for k, v in summary.items() if k != "files"} == SUMMARY, summary
        assert list(summary) == list(SUMMARY) + ["files"]
        assert summary["files"] == files
        # the CSVs the server downloads: hash of the bytes, size, data rows
        cards = (d / "cards.csv").read_bytes()
        assert files["cards.csv"] == {"sha256": sha(cards), "bytes": len(cards), "rows": 2}, files["cards.csv"]
        assert files["card_catalog.csv"]["rows"] == 3 and files["tcgplayer_sets.csv"]["rows"] == 1
        assert "watchlist_listings.csv" not in files                      # absent file: left out, not an error
        # the shard manifests: one per directory, columns from the shards' header, per-shard sha256 / bytes / rows
        series = json.loads((d / "series" / "manifest.json").read_text())
        assert series["columns"] == ["asset_id", "week_start", "n_sales", "median_price", "low", "high"]
        assert sorted(series["shards"]) == ["00", "01"]
        assert series["shards"]["00"] == {"sha256": sha(SERIES_00.encode()), "bytes": len(SERIES_00), "rows": 2}
        assert series["shards"]["01"]["rows"] == 1
        recent = json.loads((d / "recent_sales" / "manifest.json").read_text())
        assert recent["columns"][:3] == ["asset_id", "date", "price"] and recent["shards"]["00"]["rows"] == 1
        psa9 = json.loads((d / "recent_sales_psa9" / "manifest.json").read_text())
        assert psa9 == {"columns": [], "shards": {}}                      # no shards yet: an empty manifest, not a crash
        # the manifests themselves are listed so a reader can check that download too
        for name, n in (("series/manifest.json", 2), ("recent_sales/manifest.json", 1), ("recent_sales_psa9/manifest.json", 0),
                        ("clean_sales/manifest.json", 2)):
            data = (d / name).read_bytes()
            assert files[name] == {"sha256": sha(data), "bytes": len(data), "entries": n}, (name, files[name])
        assert list(files) == ["cards.csv", "characters.csv", "card_catalog.csv", "tcgplayer_sets.csv", "tcgplayer_ids.csv",
                               "series/manifest.json", "recent_sales/manifest.json", "recent_sales_psa9/manifest.json",
                               "clean_sales/manifest.json"]
        # deterministic: a second run over the same files writes the same bytes
        before = {p: p.read_bytes() for p in d.rglob("manifest.json")}
        files2 = manifest.build(d)
        assert files2 == files and {p: p.read_bytes() for p in d.rglob("manifest.json")} == before
        # the watchlist file appears once the sweep has written it; a changed cards.csv changes its hash
        (d / "watchlist_listings.csv").write_text("asset_id,grade,verified\na,10,1\n")
        (d / "cards.csv").write_text(CARDS + "asset:c,c,Mew\n")
        files3 = manifest.build(d)
        assert files3["watchlist_listings.csv"]["rows"] == 1
        assert files3["cards.csv"]["rows"] == 3 and files3["cards.csv"]["sha256"] != files["cards.csv"]["sha256"]
        assert list(files3)[5] == "watchlist_listings.csv"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} test(s) passed")
