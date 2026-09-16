"""All-in cost model for a PSA 10 round trip, so "net" means what the trader keeps.

    from costs import CostModel
    cm = CostModel()                      # eBay, US buyer, defaults below
    cm.entry_cost(300)                    # 327.5: price + 7.5% tax + $5 shipping
    cm.exit_net(384)                      # what a $384 sale leaves after fee, order fee, shipping
    cm.net_return(300, 384)               # ~0.0 -> $300 needs ~+28% to break even
    cm.breakeven_move(300)                # 0.28

Defaults (checked Sept 2026):
  eBay final value fee on trading cards: 13.25% of the sale up to $7,500 per
  item, 2.35% on the part above, plus $0.40 per order ($0.30 under $10).
  A standing promotion halves the 13.25% on singles sold for $1,000+; it is
  OFF by default because it has to be opted into per listing and can end.
  Buyer side: sales tax (7.5% as a US average) and shipping each way ($5).
  Auction houses (Goldin, Heritage, PWCC/Fanatics auctions) add a buyer's
  premium; alt.xyz records those rows at or below eBay prices for the same
  card-month (0.92-0.96x), i.e. as hammer, so the premium is added here on
  the share of entry sales that came from an auction house.

Everything is a parameter so the backtest can sweep it (+/- 5 points).
"""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CostModel:
    tax: float = 0.075            # buyer's sales tax on entry
    ship_in: float = 5.0          # shipping paid by the buyer on entry
    ship_out: float = 5.0         # shipping paid by the seller on exit
    fee_rate: float = 0.1325      # eBay final value fee up to fee_cap
    fee_cap: float = 7500.0
    fee_above: float = 0.0235     # fee on the part above fee_cap
    order_fee: float = 0.40
    ah_premium: float = 0.20      # buyer's premium when the entry sale was at an auction house
    promo_1000: bool = False      # 50% off fee_rate on singles >= $1,000

    def entry_cost(self, price, ah_share=0.0):
        """Cash out of pocket to own the card at `price` (hammer / sale price)."""
        price, ah_share = np.asarray(price, dtype=float), np.asarray(ah_share, dtype=float)
        return price * (1.0 + self.tax + self.ah_premium * ah_share) + self.ship_in

    def fee(self, price):
        price = np.asarray(price, dtype=float)
        rate = self.fee_rate
        if self.promo_1000:
            rate = np.where(price >= 1000.0, self.fee_rate / 2.0, self.fee_rate)
        below = np.minimum(price, self.fee_cap)
        above = np.maximum(price - self.fee_cap, 0.0)
        return below * rate + above * self.fee_above

    def exit_net(self, price):
        """Cash the seller keeps from a sale at `price`."""
        price = np.asarray(price, dtype=float)
        return price - self.fee(price) - self.order_fee - self.ship_out

    def net_return(self, entry_price, exit_price, ah_share=0.0):
        return self.exit_net(exit_price) / self.entry_cost(entry_price, ah_share) - 1.0

    def breakeven_move(self, price, ah_share=0.0):
        """Fractional rise in the sale price needed to get the entry cash back."""
        price = np.asarray(price, dtype=float)
        need = self.entry_cost(price, ah_share) + self.order_fee + self.ship_out
        rate = self.fee_rate / 2.0 if self.promo_1000 else self.fee_rate
        # below the cap the fee is linear; above it the extra dollars are taxed at fee_above
        exit_below = need / (1.0 - rate)
        if self.promo_1000:
            # the promo only applies if the exit itself is >= $1,000; resolve the boundary honestly
            plain = need / (1.0 - self.fee_rate)
            exit_below = np.where(plain >= 1000.0, need / (1.0 - self.fee_rate / 2.0), plain)
        exit_above = self.fee_cap + (need - self.fee_cap * (1.0 - rate)) / (1.0 - self.fee_above)
        exit_price = np.where(exit_below <= self.fee_cap, exit_below, exit_above)
        return exit_price / price - 1.0
