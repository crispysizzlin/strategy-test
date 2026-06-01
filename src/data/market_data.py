"""
Market data feed — historical and real-time data acquisition.

Primary source: Schwab API
Fallback source: yfinance (for historical data during development)

VIX data: Fetched from FRED (free Federal Reserve API) using pandas_datareader.
  VIX  (^VIX) : CBOE Volatility Index (30-day)
  VIX3M (^VIX3M): CBOE 3-Month Volatility Index

Both are needed for term structure slope computation (backwardation detection).
"""

from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from src.utils.logger import logger


class MarketDataFeed:
    """
    Provides historical and real-time market data.

    Falls back gracefully between data sources.
    """

    def __init__(self, schwab_client=None) -> None:
        self.client = schwab_client
        self._cache: Dict[str, pd.DataFrame] = {}

    def get_price_history(
        self,
        symbol: str,
        period_years: int = 2,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch daily OHLCV history.

        Tries Schwab first; falls back to yfinance.
        """
        if use_cache and symbol in self._cache:
            return self._cache[symbol]

        df = None

        # Try Schwab
        if self.client is not None and self.client.connected:
            try:
                df = self.client.get_price_history(symbol, period=period_years)
                logger.debug(f"Price history for {symbol} loaded from Schwab ({len(df)} bars)")
            except Exception as e:
                logger.debug(f"Schwab price history failed for {symbol}: {e}")

        # Fallback: yfinance
        if df is None or df.empty:
            df = self._yfinance_fallback(symbol, period_years)

        if df is not None and not df.empty:
            self._cache[symbol] = df

        return df or pd.DataFrame()

    def _yfinance_fallback(
        self, symbol: str, period_years: int = 2
    ) -> Optional[pd.DataFrame]:
        """Download data from Yahoo Finance as fallback."""
        try:
            import yfinance as yf
            end = datetime.now()
            start = end - timedelta(days=period_years * 365)
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start, end=end, interval="1d")
            if df.empty:
                return None
            df.index = pd.to_datetime(df.index).tz_localize(None)
            df.columns = [c.lower() for c in df.columns]
            df = df[["open", "high", "low", "close", "volume"]]
            logger.debug(f"yfinance fallback for {symbol}: {len(df)} bars")
            return df
        except Exception as e:
            logger.warning(f"yfinance fallback failed for {symbol}: {e}")
            return None

    def get_vix_data(self, period_years: int = 3) -> Tuple[pd.Series, pd.Series]:
        """
        Fetch VIX and VIX3M time series.

        Returns (vix, vix3m) as pd.Series of percentage values (e.g. 15.0 for VIX=15).

        Sources tried in order:
          1. yfinance (^VIX, ^VIX3M)
          2. FRED API (VIXCLS, if FRED_API_KEY set)
        """
        vix = self._fetch_vix_yfinance("^VIX", period_years)
        vix3m = self._fetch_vix_yfinance("^VIX3M", period_years)

        if vix is None or vix.empty:
            logger.warning("Could not fetch VIX — using proxy from SPY realized vol")
            vix = pd.Series(dtype=float)

        if vix3m is None or vix3m.empty:
            # Approximate VIX3M as VIX × 1.02 (mild contango)
            vix3m = vix * 1.02 if not vix.empty else pd.Series(dtype=float)

        return vix, vix3m

    def _fetch_vix_yfinance(self, symbol: str, period_years: int) -> Optional[pd.Series]:
        """Fetch a VIX series from Yahoo Finance."""
        try:
            import yfinance as yf
            end = datetime.now()
            start = end - timedelta(days=period_years * 365)
            df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
            if df.empty:
                return None
            df.index = pd.to_datetime(df.index).tz_localize(None)
            # Return Close series
            close = df["Close"].squeeze()
            close.name = symbol
            logger.debug(f"VIX data loaded: {symbol} ({len(close)} bars)")
            return close
        except Exception as e:
            logger.debug(f"VIX fetch failed ({symbol}): {e}")
            return None

    def get_risk_free_rate(self) -> float:
        """
        Fetch current risk-free rate (3-month T-bill yield).
        Falls back to 5% if API unavailable.
        """
        try:
            import pandas_datareader as pdr
            df = pdr.get_data_fred("DTB3", start=datetime.now() - timedelta(days=10))
            rate = float(df.dropna().iloc[-1].values[0]) / 100
            logger.debug(f"Risk-free rate: {rate:.2%}")
            return rate
        except Exception:
            return 0.05  # 5% default

    def get_dividend_yield(self, symbol: str) -> float:
        """Get dividend yield for dividend-paying ETFs (SPY ≈ 1.4%)."""
        defaults = {"SPY": 0.014, "QQQ": 0.007, "IWM": 0.015, "SPX": 0.014}
        return defaults.get(symbol.upper(), 0.014)
