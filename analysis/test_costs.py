"""analysis/.venv/bin/python analysis/test_costs.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from costs import CostModel  # noqa: E402


def test_300_dollar_card_needs_about_28_percent():
    cm = CostModel()
    assert abs(cm.entry_cost(300) - 327.5) < 1e-9
    be = cm.breakeven_move(300)
    assert 0.27 < be < 0.29, be
    exit_price = 300 * (1 + be)
    assert abs(cm.net_return(300, exit_price)) < 1e-9


def test_cheap_cards_pay_more_and_expensive_ones_less():
    cm = CostModel()
    assert cm.breakeven_move(50) > 0.38
    assert cm.breakeven_move(2000) < 0.25
    # above the cap the marginal fee is 2.35%, so a $20k card breaks even below +20%
    assert cm.breakeven_move(20000) < 0.20
    assert abs(cm.net_return(20000, 20000 * (1 + cm.breakeven_move(20000)))) < 1e-9


def test_fee_tiers_and_promo():
    cm = CostModel()
    assert abs(cm.fee(1000) - 132.5) < 1e-9
    assert abs(cm.fee(10000) - (7500 * 0.1325 + 2500 * 0.0235)) < 1e-9
    promo = CostModel(promo_1000=True)
    assert abs(promo.fee(1000) - 66.25) < 1e-9
    assert abs(promo.fee(999) - 999 * 0.1325) < 1e-9
    assert promo.breakeven_move(2000) < cm.breakeven_move(2000)


def test_auction_house_premium_on_entry_share():
    cm = CostModel()
    assert abs(cm.entry_cost(100, ah_share=1.0) - (100 * 1.275 + 5)) < 1e-9
    assert abs(cm.entry_cost(100, ah_share=0.5) - (100 * 1.175 + 5)) < 1e-9
    assert cm.net_return(100, 150, ah_share=1.0) < cm.net_return(100, 150, ah_share=0.0)


def test_vectorised():
    cm = CostModel()
    r = cm.net_return(np.array([100.0, 300.0]), np.array([150.0, 384.0]), np.array([0.0, 0.0]))
    assert r.shape == (2,) and r[0] > 0 and abs(r[1]) < 0.01


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
