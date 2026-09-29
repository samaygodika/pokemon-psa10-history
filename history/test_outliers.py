#!/usr/bin/env python3
"""Checks for metrics.drop_outliers (2026-09-28): a confirming run counts distinct
listings, so repeat sales of one eBay Buy It Now can't reset a card's reference.

    python3 history/test_outliers.py

Plain asserts, no pytest."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics  # noqa: E402

D0 = date(2026, 1, 1)


def day(n):
    return D0 + timedelta(days=n)


def base(n=6, price=50000.0):
    """n accepted sales around `price`, each on its own listing."""
    return [(day(i), price + i * 100, "eBay", f"https://www.ebay.com/itm/{1000 + i}") for i in range(n)]


def test_repeat_listing_cannot_confirm_a_drop():
    # the Dragon Frontiers Charizard case: one $15 listing sells again and again, plus one other $12 listing
    junk = [(day(10 + i), 15.0, "eBay", "https://www.ebay.com/itm/176128041757") for i in range(8)]
    junk.append((day(30), 12.0, "eBay", "https://www.ebay.com/itm/297914733112"))
    keep, dropped = metrics.drop_outliers(sorted(base() + junk))
    assert [p for _, p, _ in keep if p < 100] == [], keep
    assert dropped == 9, dropped
    assert all(len(s) == 3 for s in keep)          # callers get (date, price, source)


def test_distinct_listings_still_confirm_a_drop():
    lows = [(day(10 + i), 15.0, "eBay", f"https://www.ebay.com/itm/{2000 + i}") for i in range(5)]
    keep, dropped = metrics.drop_outliers(sorted(base() + lows))
    assert sum(1 for _, p, _ in keep if p == 15.0) == 5, keep
    assert dropped == 0, dropped


def test_repeat_listing_counts_once_inside_a_longer_run():
    # 4 distinct low listings, one of them repeated: not enough; a 5th distinct one confirms the whole run
    lows = [(day(10), 15.0, "eBay", "u1"), (day(11), 15.0, "eBay", "u1"), (day(12), 15.0, "eBay", "u2"),
            (day(13), 15.0, "eBay", "u3"), (day(14), 15.0, "eBay", "u4")]
    keep, _ = metrics.drop_outliers(sorted(base() + lows))
    assert not any(p == 15.0 for _, p, _ in keep)
    keep, dropped = metrics.drop_outliers(sorted(base() + lows + [(day(15), 15.0, "eBay", "u5")]))
    assert sum(1 for _, p, _ in keep if p == 15.0) == 6 and dropped == 0, (keep, dropped)


def test_high_side_needs_two_listings():
    same = [(day(10), 400000.0, "eBay", "u9"), (day(11), 400000.0, "eBay", "u9")]
    keep, dropped = metrics.drop_outliers(sorted(base() + same))
    assert not any(p == 400000.0 for _, p, _ in keep) and dropped == 2
    two = [(day(10), 400000.0, "eBay", "u9"), (day(11), 400000.0, "Goldin", "u10")]
    keep, dropped = metrics.drop_outliers(sorted(base() + two))
    assert sum(1 for _, p, _ in keep if p == 400000.0) == 2 and dropped == 0


def test_blank_or_missing_url_counts_as_its_own_listing():
    lows = [(day(10 + i), 15.0, "eBay", "") for i in range(5)]
    keep, _ = metrics.drop_outliers(sorted(base() + lows))
    assert sum(1 for _, p, _ in keep if p == 15.0) == 5
    # (date, price, source) tuples, as analysis/panel.py and psa9_lag.py pass them: every sale its own listing
    three = [s[:3] for s in sorted(base() + lows)]
    keep, dropped = metrics.drop_outliers(three)
    assert sum(1 for _, p, _ in keep if p == 15.0) == 5 and dropped == 0


def test_thin_card_holds_a_10x_jump_until_a_second_listing():
    # the BW87 Leafeon promo: two sales around $600, then one $78,000 lot
    two = [(day(0), 750.65, "eBay", "a"), (day(100), 490.0, "eBay", "b")]
    keep, dropped = metrics.drop_outliers(two + [(day(900), 78000.0, "PWCC", "c")])
    assert [p for _, p, _ in keep] == [750.65, 490.0] and dropped == 1, keep
    keep, dropped = metrics.drop_outliers(two + [(day(900), 78000.0, "PWCC", "c"), (day(910), 80000.0, "Goldin", "d")])
    assert [p for _, p, _ in keep][-2:] == [78000.0, 80000.0] and dropped == 0, keep
    # within 10x of a thin reference: accepted as before
    keep, _ = metrics.drop_outliers(two + [(day(900), 5000.0, "eBay", "c")])
    assert keep[-1][1] == 5000.0


def test_thin_card_whose_first_sale_was_junk_recovers_on_two_listings():
    junk_first = [(day(0), 78000.0, "PWCC", "a"), (day(30), 500.0, "eBay", "b"), (day(40), 480.0, "eBay", "c")]
    keep, dropped = metrics.drop_outliers(junk_first)
    assert [p for _, p, _ in keep] == [78000.0, 500.0, 480.0] and dropped == 0, keep   # confirmed on 2 listings, not 5


if __name__ == "__main__":
    test_repeat_listing_cannot_confirm_a_drop()
    test_distinct_listings_still_confirm_a_drop()
    test_repeat_listing_counts_once_inside_a_longer_run()
    test_high_side_needs_two_listings()
    test_blank_or_missing_url_counts_as_its_own_listing()
    test_thin_card_holds_a_10x_jump_until_a_second_listing()
    test_thin_card_whose_first_sale_was_junk_recovers_on_two_listings()
    print("all outlier checks passed")
