"""
Drawdown control and circuit breakers.

Implements a multi-tier drawdown protection system:
  Tier 1 (daily -3%):   Reduce new position size by 50%, close weakest positions
  Tier 2 (weekly -5%):  Stop all new entries, manage only existing positions
  Tier 3 (monthly -8%): Stop all trading, alert human operator
  Tier 4 (-15% total):  Hard stop — all positions closed, system halted

This mirrors institutional risk management where portfolio managers have
hard drawdown limits that trigger mandatory reduction or cessation of trading.

The Kelly criterion naturally reduces position sizes as equity decreases,
but the circuit breakers provide hard stops independent of Kelly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date
from enum import Enum
from typing import Callable, List, Optional

import numpy as np

from src.utils.logger import logger


class CircuitBreakerLevel(str, Enum):
    NORMAL = "normal"
    CAUTION = "caution"          # Tier 1: -3% daily
    RESTRICTED = "restricted"    # Tier 2: -5% weekly
    HALT = "halt"                # Tier 3: -8% monthly or -15% total


@dataclass
class DrawdownState:
    level: CircuitBreakerLevel
    daily_pnl: float
    weekly_pnl: float
    monthly_pnl: float
    total_drawdown: float
    peak_equity: float
    current_equity: float
    triggered_at: Optional[datetime]

    @property
    def allow_new_positions(self) -> bool:
        return self.level in (CircuitBreakerLevel.NORMAL, CircuitBreakerLevel.CAUTION)

    @property
    def size_multiplier(self) -> float:
        """Multiply normal position sizes by this factor."""
        return {
            CircuitBreakerLevel.NORMAL: 1.0,
            CircuitBreakerLevel.CAUTION: 0.50,
            CircuitBreakerLevel.RESTRICTED: 0.0,
            CircuitBreakerLevel.HALT: 0.0,
        }[self.level]


class DrawdownController:
    """
    Monitors portfolio equity and triggers circuit breakers.

    Maintains daily, weekly, and monthly P&L tracking.
    Notifies registered callbacks when breakers trigger.
    """

    def __init__(
        self,
        account_size: float = 20000.0,
        daily_limit_pct: float = 0.03,
        weekly_limit_pct: float = 0.05,
        monthly_limit_pct: float = 0.08,
        max_drawdown_pct: float = 0.15,
        on_circuit_break: Optional[Callable] = None,
    ) -> None:
        self.account_size = account_size
        self.daily_limit = account_size * daily_limit_pct
        self.weekly_limit = account_size * weekly_limit_pct
        self.monthly_limit = account_size * monthly_limit_pct
        self.max_drawdown = account_size * max_drawdown_pct
        self._on_circuit_break = on_circuit_break

        self._peak_equity = account_size
        self._current_equity = account_size

        self._day_start_equity = account_size
        self._week_start_equity = account_size
        self._month_start_equity = account_size

        self._current_level = CircuitBreakerLevel.NORMAL
        self._level_triggered_at: Optional[datetime] = None
        self._equity_history: List[tuple] = []  # (timestamp, equity)

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(self, current_equity: float) -> DrawdownState:
        """
        Update equity and check circuit breakers.

        Call this method after every trade fill or at regular intervals.

        Returns current DrawdownState.
        """
        self._current_equity = current_equity
        self._peak_equity = max(self._peak_equity, current_equity)
        self._equity_history.append((datetime.now(), current_equity))

        daily_pnl = current_equity - self._day_start_equity
        weekly_pnl = current_equity - self._week_start_equity
        monthly_pnl = current_equity - self._month_start_equity
        total_drawdown = (current_equity - self._peak_equity) / self._peak_equity

        # Determine circuit breaker level
        if total_drawdown < -0.15 or abs(monthly_pnl) > self.monthly_limit:
            new_level = CircuitBreakerLevel.HALT
        elif abs(weekly_pnl) > self.weekly_limit:
            new_level = CircuitBreakerLevel.RESTRICTED
        elif abs(daily_pnl) > self.daily_limit:
            new_level = CircuitBreakerLevel.CAUTION
        else:
            new_level = CircuitBreakerLevel.NORMAL

        if new_level != self._current_level:
            self._level_triggered_at = datetime.now()
            logger.warning(
                f"Circuit breaker: {self._current_level.value} → {new_level.value} | "
                f"daily={daily_pnl:+.2f} weekly={weekly_pnl:+.2f} "
                f"total_dd={total_drawdown:.2%} equity={current_equity:.2f}"
            )
            self._current_level = new_level
            if self._on_circuit_break:
                self._on_circuit_break(new_level)

        return DrawdownState(
            level=self._current_level,
            daily_pnl=daily_pnl,
            weekly_pnl=weekly_pnl,
            monthly_pnl=monthly_pnl,
            total_drawdown=total_drawdown,
            peak_equity=self._peak_equity,
            current_equity=current_equity,
            triggered_at=self._level_triggered_at,
        )

    # ------------------------------------------------------------------
    # Day / Week / Month resets
    # ------------------------------------------------------------------

    def reset_daily(self) -> None:
        """Call at market open each day."""
        self._day_start_equity = self._current_equity
        if self._current_level == CircuitBreakerLevel.CAUTION:
            self._current_level = CircuitBreakerLevel.NORMAL
            logger.info("Daily circuit breaker reset")

    def reset_weekly(self) -> None:
        """Call at start of each trading week."""
        self._week_start_equity = self._current_equity
        if self._current_level == CircuitBreakerLevel.RESTRICTED:
            self._current_level = CircuitBreakerLevel.CAUTION
            logger.info("Weekly circuit breaker partially reset to CAUTION")

    def reset_monthly(self) -> None:
        """Call at start of each calendar month."""
        self._month_start_equity = self._current_equity
        if self._current_level == CircuitBreakerLevel.HALT:
            logger.critical(
                "HALT circuit breaker requires manual reset. "
                "Please review portfolio before resuming trading."
            )

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    @property
    def current_state(self) -> DrawdownState:
        daily_pnl = self._current_equity - self._day_start_equity
        weekly_pnl = self._current_equity - self._week_start_equity
        monthly_pnl = self._current_equity - self._month_start_equity
        total_drawdown = (self._current_equity - self._peak_equity) / self._peak_equity
        return DrawdownState(
            level=self._current_level,
            daily_pnl=daily_pnl,
            weekly_pnl=weekly_pnl,
            monthly_pnl=monthly_pnl,
            total_drawdown=total_drawdown,
            peak_equity=self._peak_equity,
            current_equity=self._current_equity,
            triggered_at=self._level_triggered_at,
        )

    def drawdown_adjusted_kelly(self, base_kelly: float) -> float:
        """
        Scale Kelly fraction based on current drawdown depth.

        This is the "drawdown-Kelly" adjustment: as the account drawsdown,
        reduce sizing to preserve capital for recovery.

        Kelly_adjusted = Kelly × max(0, (1 - |DD| / max_DD))²
        This creates a convex reduction near the drawdown limit.
        """
        state = self.current_state
        dd = abs(state.total_drawdown)
        max_dd = 0.15
        scale = max(0, (1 - dd / max_dd)) ** 2
        return base_kelly * scale

    def equity_curve(self) -> list:
        """Return list of (timestamp, equity) tuples."""
        return list(self._equity_history)

    def max_drawdown_experienced(self) -> float:
        """Historical maximum drawdown from equity curve."""
        if not self._equity_history:
            return 0.0
        equities = np.array([e for _, e in self._equity_history])
        peak = np.maximum.accumulate(equities)
        dd = (equities - peak) / peak
        return float(dd.min())
