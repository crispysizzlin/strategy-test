"""Avellaneda-Stoikov optimal quoting for cost-aware limit placement.

Transaction cost is the documented killer of premium-selling P&L (Vilkov 2026),
so we do *not* cross wide option spreads with market orders.  Instead we post
limit orders inside the spread using the Avellaneda-Stoikov (2008) reservation
price and optimal half-spread, which trade off fill probability against
adverse selection / inventory risk:

    reservation price:  r = s - q * gamma * sigma^2 * (T - t)
    optimal spread:     delta = gamma * sigma^2 * (T-t) + (2/gamma) * ln(1 + gamma/k)

where ``s`` is the mid, ``q`` the signed inventory, ``gamma`` risk aversion,
``sigma`` short-horizon vol, and ``k`` the order-book liquidity intensity, which
we estimate from the Level 2 book.

Reference
---------
Avellaneda, M. & Stoikov, S. (2008). High-frequency trading in a limit order
book. *Quantitative Finance*, 8(3), 217-224.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Quote:
    reservation_price: float
    half_spread: float
    bid: float
    ask: float


def reservation_quote(mid: float, inventory: float, gamma: float,
                      sigma: float, time_left: float, k: float) -> Quote:
    """Compute reservation price and symmetric optimal quotes.

    ``sigma`` is per-unit-time volatility (same time units as ``time_left``).
    """
    var_term = sigma * sigma * max(time_left, 0.0)
    reservation = mid - inventory * gamma * var_term
    half = 0.5 * (gamma * var_term + (2.0 / gamma) * math.log(1.0 + gamma / max(k, 1e-9)))
    half = max(half, 0.0)
    return Quote(reservation_price=reservation, half_spread=half,
                 bid=reservation - half, ask=reservation + half)


def estimate_book_intensity(book_depth: float, spread: float,
                            base: float = 1.5) -> float:
    """Crude liquidity-intensity ``k`` proxy from Level 2 depth and spread.

    Deeper books / tighter spreads imply higher fill intensity (larger k), so we
    can quote closer to mid.  This is a heuristic mapping, intended to be
    re-calibrated against realized fills.
    """
    if spread <= 0:
        return base * 10.0
    return base * (1.0 + book_depth) / spread


def limit_price_for_credit(mid: float, spread: float, aggressiveness: float = 0.35,
                           is_sell: bool = True, tick: float = 0.01) -> float:
    """Limit price to *collect* premium without crossing the spread.

    ``aggressiveness`` in [0,1]: 0 posts at mid, 1 posts at the touch.  For a sell
    (collecting credit) we start near mid and walk toward the bid only as needed.
    """
    half = 0.5 * spread
    if is_sell:
        price = mid - aggressiveness * half
    else:
        price = mid + aggressiveness * half
    return round(price / tick) * tick
