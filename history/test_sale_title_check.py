#!/usr/bin/env python3
"""Checks for history/sale_title_check.py (2026-10-20): the verdicts, which sales are picked, how
eBay's answers become rows, that apply keeps the first verdict, and that metrics.py shows it.

    python3 history/test_sale_title_check.py

Plain asserts, no pytest. urlopen is faked: nothing here talks to eBay."""
import contextlib
import csv
import io
import json
import os
import sys
import tempfile
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "history"))
import ebay_verify as ev          # noqa: E402
import ingest                     # noqa: E402
import sale_title_check as stc    # noqa: E402
from watchlist_listings import Card   # noqa: E402

NOW = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)
A, B, C, D = ("0a" + "0" * 34, "0b" + "0" * 34, "1c" + "0" * 34, "1d" + "0" * 34)


def _write(path, cols, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in cols} for r in rows)


def asset(aid, name, subject, number, set_, variety="", year="1999"):
    return {"asset_id": aid, "card_name": name, "year": year, "set": set_, "card_number": number, "subject": subject,
            "variety": variety, "first_seen": "2026-09-01"}


CHARIZARD = asset(A, "1999 Pokemon Base Set 1st Edition Charizard #4", "Charizard", "4/102", "Pokemon Base Set", "1st Edition")
PALKIA = asset(B, "2009 Pokemon Platinum Palkia G Lv.X #125", "Palkia G Lv.X", "125/127", "Pokemon Platinum", year="2009")
RAYQUAZA = asset(C, "2005 Pokemon EX Deoxys Rayquaza Gold Star #107", "Rayquaza", "107/107", "Pokemon EX Deoxys", "Gold Star", year="2005")
UMBREON = asset(D, "2007 Pokemon POP Series 5 Umbreon Gold Star #17", "Umbreon", "17/17", "Pokemon POP Series 5", "Gold Star", year="2007")


def test_judge():
    cz = Card(CHARIZARD)
    j = lambda card, g, t: stc.judge(card, g, t)[0]
    assert j(cz, "10.0", "1999 Pokemon Base Set 1st Edition Charizard 4/102 Holo PSA 10 GEM MINT") == "ok"
    assert j(cz, "10.0", "1999 Pokemon Base Set Charizard 4/102 Holo PSA 10") == "mismatch", "1st Edition missing: the unlimited card"
    assert stc.judge(cz, "10.0", "1999 Pokemon Base Set Charizard 4/102 Holo PSA 10")[1] == "1st edition missing"
    assert j(cz, "10.0", "1999 Pokemon Base Set 1st Edition Charizard 4/102 PSA 9") == "mismatch", "another grade"
    assert j(cz, "10.0", "Pokemon Charizard 1st Edition Shadowless 4/102 PSA 10") == "mismatch", "shadowless"
    assert j(cz, "10.0", "1999 Pokemon 1st Edition Blastoise 2/102 Holo PSA 10") == "mismatch", "another number"
    assert j(cz, "10.0", "Pokemon 1st Edition Base Set Charizard 4/102 PSA 10 Lot of 2") == "mismatch", "a lot"
    assert j(cz, "10.0", "Pokemon Japanese Charizard 1st Edition 4/102 PSA 10") == "mismatch", "another language"
    assert j(cz, "10.0", "1999 Pokemon Base Set 1st Edition Holo PSA 10 Blastoise") == "mismatch", "no word of the subject"
    assert j(cz, "10.0", "2023 Pokemon 151 Charizard ex 1st Edition PSA 10") == "mismatch", "year 24 off"
    assert j(cz, "10.0", "Charizard 1st Edition PSA 10") == "unclear", "no number: could be it"
    assert j(cz, "10.0", "Charizard 1st Edition Holo") == "unclear", "no grade in the title"
    assert j(cz, "10.0", "Charizard 1st Edition 4/102 PSA 10 Base ships 12/25") == "ok", "another fraction beside the right number does not condemn it"
    assert j(cz, "10.0", "Charizard 1st Edition 4/102 PSA 10 Base sold 12/25/2024") == "mismatch", "a year 25 off is refused, date or not (the sweep's rule)"
    pk = Card(PALKIA)
    assert j(pk, "10.0", "Palkia LV.X 125/127 Platinum PSA 10") == "ok", "sellers drop the G; the total pins it"
    assert j(pk, "10.0", "Palkia G Lv.X PSA 10 Platinum Holo") == "unclear", "no number"
    assert j(pk, "9.0", "Palkia G LV X 125/127 PSA 9 MINT") == "ok"
    assert j(pk, "9.0", "Palkia G LV X 125/127 PSA 10 GEM MT") == "mismatch", "PSA 10 sale on the PSA 9 record"
    rq = Card(RAYQUAZA)
    assert j(rq, "10.0", "2005 Pokemon EX Deoxys Rayquaza Gold Star 107/107 PSA 10") == "ok"
    assert j(rq, "10.0", "Pokemon EX Deoxys Rayquaza ex 97/107 Holo PSA 10") == "mismatch", "another card of the set"
    assert j(rq, "10.0", "Rayquaza Gold Star PSA 10 EX Deoxys") == "unclear", "no number, and star-less sellers exist"
    um = Card(UMBREON)
    assert j(um, "10.0", "2021 Celebrations Umbreon Gold Star 17/17 PSA 10") == "mismatch", "the reprint"
    assert j(um, "10.0", "Umbreon Gold Star 17/17 PSA 10 POP Series 5") == "ok"
    assert j(um, "10.0", "Umbreon Gold Star 17/17 PSA 10 Reverse Holo") == "mismatch", "reverse on a card that is not"
    assert j(Card(asset(D, "Pikachu", "Pikachu", "58/102", "Pokemon Base Set")), "10.0", "Pikachu Gold Star 58/102 PSA 10") == "mismatch", "a variant the card is not"
    # a letter-prefixed number in the title is a card number too (a real 2026-10-20 probe: the Skyridge "H" reverse holo sold on the #30 record)
    st = Card(asset(D, "2003 Pokemon Skyridge Starmie #30", "Starmie", "30", "Pokemon Skyridge", year="2003"))
    assert stc.judge(st, "10.0", "Starmie H28/H32 Skyridge Reverse Holo PSA 10") == ("mismatch", "number h28/h32 vs 30")
    tg = Card(asset(D, "2022 Pokemon Lost Origin Pikachu #TG05", "Pikachu", "TG05/TG30", "Pokemon Lost Origin", year="2022"))
    assert j(tg, "10.0", "Pikachu TG05/TG30 Lost Origin Trainer Gallery PSA 10") == "ok"
    assert j(tg, "10.0", "Pikachu TG06/TG30 Lost Origin Trainer Gallery PSA 10") == "mismatch"
    print("judge ok")


def test_candidates():
    with tempfile.TemporaryDirectory() as tmp:
        latest = Path(tmp) / "latest"
        cards_cols = ["asset_id", "card_name", "last_sale_unconfirmed", "psa9_last_sale_unconfirmed"]
        _write(latest / "cards.csv", cards_cols, [
            {"asset_id": A, "last_sale_unconfirmed": "1", "psa9_last_sale_unconfirmed": "0"},
            {"asset_id": B, "last_sale_unconfirmed": "0", "psa9_last_sale_unconfirmed": "1"},
            {"asset_id": C, "last_sale_unconfirmed": "1", "psa9_last_sale_unconfirmed": ""},
            {"asset_id": D, "last_sale_unconfirmed": "1", "psa9_last_sale_unconfirmed": ""},   # newest sale is a Goldin lot: no eBay id
        ])
        rc = ["asset_id", "date", "price", "source", "sale_type", "status", "outlier", "url"]
        _write(latest / "recent_sales" / "0a.csv", rc, [
            {"asset_id": A, "date": "2026-10-19", "price": "500", "status": "RELISTED", "outlier": "0", "url": "https://www.ebay.com/itm/100000000009"},   # flagged: not the sale shown
            {"asset_id": A, "date": "2026-10-18", "price": "9000", "status": "ok", "outlier": "1", "url": "https://www.ebay.com/itm/100000000001"},
            {"asset_id": A, "date": "2026-10-01", "price": "300", "status": "ok", "outlier": "0", "url": "https://www.ebay.com/itm/100000000002"},
        ])
        _write(latest / "recent_sales" / "1c.csv", rc, [
            {"asset_id": C, "date": "2026-10-18", "price": "12000", "status": "ok", "outlier": "1", "url": "https://www.ebay.com/itm/100000000003"},
            {"asset_id": D, "date": "2026-10-19", "price": "700", "status": "ok", "outlier": "1", "url": "https://goldin.co/item/x"},
        ])
        _write(latest / "recent_sales_psa9" / "0b.csv", rc, [
            {"asset_id": B, "date": "2026-10-10", "price": "800", "status": "ok", "outlier": "1", "url": "https://www.ebay.com/itm/100000000004"},
            {"asset_id": A, "date": "2026-10-17", "price": "100", "status": "ok", "outlier": "0", "url": "https://www.ebay.com/itm/100000000005"},   # A's PSA 9 is not held
        ])
        got = stc.candidates(latest, {})
        assert [(t[0], t[1], t[2]) for t in got] == [("100000000003", C, "10.0"), ("100000000001", A, "10.0"), ("100000000004", B, "9.0")], got
        assert [(t[0], t[1]) for t in stc.candidates(latest, {("100000000001", A, "10.0"): {}})] == [("100000000003", C), ("100000000004", B)], "on file: skipped"
        print("candidates ok")


class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


class FakeEbay:
    """item id -> dict (200 body) | int (HTTP status; 404 carries errorId 11003)."""

    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def __call__(self, req, timeout=None):
        if req.full_url.startswith(ev.TOKEN_URL):
            return Resp(json.dumps({"access_token": "t", "expires_in": 7200}).encode())
        iid = req.full_url.split("legacy_item_id=")[1]
        self.asked.append(iid)
        a = self.answers.get(iid, 500)
        if isinstance(a, int):
            body = json.dumps({"errors": [{"errorId": ev.NOT_FOUND}]}) if a == 404 else "{}"
            raise urllib.error.HTTPError(req.full_url, a, "err", {}, io.BytesIO(body.encode()))
        return Resp(json.dumps(a).encode())


def test_check_apply_and_metrics():
    with tempfile.TemporaryDirectory() as tmp:
        store, latest, out = Path(tmp) / "store", Path(tmp) / "latest", Path(tmp) / "out"
        _write(store / "assets.csv", ingest.ASSET_COLS, [CHARIZARD, PALKIA, RAYQUAZA])
        _write(latest / "card_catalog.csv", ["asset_id", "set", "number"], [])
        _write(latest / "cards.csv", ["asset_id", "last_sale_unconfirmed", "psa9_last_sale_unconfirmed"], [
            {"asset_id": A, "last_sale_unconfirmed": "1", "psa9_last_sale_unconfirmed": "0"},
            {"asset_id": B, "last_sale_unconfirmed": "1", "psa9_last_sale_unconfirmed": "0"},
            {"asset_id": C, "last_sale_unconfirmed": "1", "psa9_last_sale_unconfirmed": "0"}])
        rc = ["asset_id", "date", "price", "source", "sale_type", "status", "outlier", "url"]
        _write(latest / "recent_sales" / "0a.csv", rc, [
            {"asset_id": A, "date": "2026-10-18", "price": "3000", "status": "ok", "outlier": "1", "url": "https://www.ebay.com/itm/100000000001"},
            {"asset_id": B, "date": "2026-10-17", "price": "9000", "status": "ok", "outlier": "1", "url": "https://www.ebay.com/itm/100000000002"}])
        _write(latest / "recent_sales" / "1c.csv", rc, [
            {"asset_id": C, "date": "2026-07-01", "price": "40000", "status": "ok", "outlier": "1", "url": "https://www.ebay.com/itm/100000000003"}])
        fake = FakeEbay({
            "100000000001": {"title": "1999 Pokemon Base Set Charizard 4/102 Holo PSA 10", "bidCount": 23, "uniqueBidderCount": 9,
                             "itemEndDate": "2026-10-18T01:00:00.000Z", "price": {"value": "3000.00", "currency": "USD"}},
            "100000000002": {"title": "Palkia LV.X 125/127 Platinum PSA 10", "price": {"value": "9000.00"}},   # a Buy It Now: no bids
            "100000000003": 404,
        })
        os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"] = "id", "secret"
        with contextlib.redirect_stdout(io.StringIO()):
            res = stc.check(out, budget=10, workers=2, store=store, latest=latest, urlopen=fake, now=NOW)
        assert sorted(fake.asked) == ["100000000001", "100000000002", "100000000003"], fake.asked
        assert (res["asked"], res["answered"], res["calls"], res["mismatch"], res["ok"], res["unavailable"]) == (3, 3, 3, 1, 1, 1), res
        rows = {r["item_id"]: r for r in ingest.read_csv(out / stc.RESULTS)}
        assert rows["100000000001"]["status"] == "mismatch" and rows["100000000001"]["reason"] == "1st edition missing"
        assert (rows["100000000001"]["bid_count"], rows["100000000001"]["unique_bidders"], rows["100000000001"]["ebay_price"]) == ("23", "9", "3000.00")
        assert rows["100000000002"]["status"] == "ok" and rows["100000000002"]["bid_count"] == ""
        assert rows["100000000003"]["status"] == "unavailable" and rows["100000000003"]["title"] == ""
        status = json.loads((out / stc.STATUS).read_text())
        assert status["calls"] == 3 and "3 answered (1 ok, 1 mismatch, 0 unclear, 1 unavailable)" in status["note"], status
        # apply, twice: the same file; a verdict on file is kept even if a later run says otherwise
        with contextlib.redirect_stdout(io.StringIO()):
            stc.apply(out, store)
            first = (store / stc.CHECKS).read_bytes()
            stc.apply(out, store)
        assert (store / stc.CHECKS).read_bytes() == first
        checks = stc.load_checks(store)
        assert set(checks) == {("100000000001", A, "10.0"), ("100000000002", B, "10.0"), ("100000000003", C, "10.0")}
        # a second check picks nothing: everything is on file
        with contextlib.redirect_stdout(io.StringIO()):
            res2 = stc.check(out / "2", budget=10, store=store, latest=latest, urlopen=fake, now=NOW)
        assert res2["asked"] == 0 and len(fake.asked) == 3
        # rejudge keeps the verdicts when the rule has not changed
        with contextlib.redirect_stdout(io.StringIO()):
            assert stc.rejudge(store, latest) == 0
        # a 500 and a rate limit record nothing
        fake2 = FakeEbay({"100000000001": 500, "100000000002": 429})
        (store / stc.CHECKS).unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            res3 = stc.check(out / "3", budget=2, workers=1, store=store, latest=latest, urlopen=fake2, now=NOW)
        assert res3["answered"] == 0 and res3["rate_limited"] is True, res3
        # no keys: nothing asked, exit clean
        del os.environ["EBAY_CLIENT_ID"]
        os.environ["EBAY_KEYS_FILE"] = str(Path(tmp) / "nokeys")
        with contextlib.redirect_stdout(io.StringIO()):
            assert stc.check(out / "4", store=store, latest=latest)["asked"] == 0
        print("check/apply ok")


if __name__ == "__main__":
    test_judge()
    test_candidates()
    test_check_apply_and_metrics()
    print("all sale-title-check checks passed")
