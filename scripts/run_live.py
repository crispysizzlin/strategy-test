"""
AVRPE Live Trading Runner
=========================
Main entry point for live options income trading via Charles Schwab.

Usage:
  python scripts/run_live.py --config config/config.yaml

Required environment variables (set in Cursor Secrets or .env):
  SCHWAB_API_KEY       - Schwab developer app key
  SCHWAB_APP_SECRET    - Schwab developer app secret
  SCHWAB_CALLBACK_URL  - OAuth callback URL
  SCHWAB_TOKEN_PATH    - Token storage path
  SCHWAB_ACCOUNT_NUMBER - Full Schwab account number
  FRED_API_KEY         - Free Federal Reserve data (optional)

Trading schedule (New York time):
  09:30 - Market open check
  10:00-11:30 - Primary entry window
  10:30 - Position management
  12:30 - Midday management
  13:00-14:30 - Secondary entry window
  15:00 - Afternoon management
  15:45 - Final management before close

Safety note: This system trades real money. Always monitor positions.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import click
import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

# Ensure workspace root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.broker.schwab_client import SchwabClient
from src.broker.order_manager import OrderManager
from src.broker.streaming import StreamingManager
from src.data.market_data import MarketDataFeed
from src.data.options_chain import OptionsChainProcessor
from src.models.regime.hmm_detector import HMMRegimeDetector
from src.models.regime.hurst import HurstAnalyzer
from src.models.volatility.garch import GARCHModel
from src.models.volatility.realized_vol import RealizedVolatility
from src.models.volatility.vol_surface import VolatilitySurface
from src.strategy.vrp_engine import VRPEngine
from src.strategy.structure_selector import StructureSelector
from src.strategy.portfolio_manager import PortfolioManager
from src.risk.position_sizer import PositionSizer
from src.risk.risk_metrics import RiskMetrics
from src.risk.drawdown_control import DrawdownController
from src.execution.order_flow_analyzer import OrderFlowAnalyzer
from src.execution.execution_engine import ExecutionEngine
from src.utils.logger import logger, setup_logger


class AVRPEEngine:
    """
    Main orchestration engine for the Adaptive Volatility Regime Premium Engine.

    Lifecycle:
      1. Connect to Schwab
      2. Load/fit models (GARCH, HMM)
      3. Start streaming data
      4. Schedule management tasks
      5. Run indefinitely until signal or error
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        self._running = False

        acct_cfg = config.get("account", {})
        self.account_size = float(acct_cfg.get("size", 20000))
        self.symbols = [s["symbol"] for s in config.get("universe", {}).get("primary", [])]

        broker_cfg = config.get("broker", {})
        self.client = SchwabClient(
            api_key=os.environ.get("SCHWAB_API_KEY", broker_cfg.get("api_key", "")),
            app_secret=os.environ.get("SCHWAB_APP_SECRET", broker_cfg.get("app_secret", "")),
            callback_url=os.environ.get("SCHWAB_CALLBACK_URL", broker_cfg.get("callback_url", "")),
            token_path=os.environ.get("SCHWAB_TOKEN_PATH", broker_cfg.get("token_path", "./schwab_token.json")),
            account_number=os.environ.get("SCHWAB_ACCOUNT_NUMBER", broker_cfg.get("account_number", "")),
            paper_mode=broker_cfg.get("paper_mode", False),
        )

        strategy_cfg = config.get("strategy", {})
        risk_cfg = config.get("risk", {})

        self.order_manager = OrderManager()
        self.market_data = MarketDataFeed(self.client)
        self.options_processor = OptionsChainProcessor()
        self.vol_surface = VolatilitySurface(model=config.get("volatility", {}).get("vol_surface", {}).get("model", "ssvi"))
        self.hmm = HMMRegimeDetector(n_states=config.get("regime", {}).get("n_states", 3))
        self.hurst = HurstAnalyzer()
        self.garch = GARCHModel()
        self.rv_calc = RealizedVolatility()

        self.vrp_engine = VRPEngine(self.symbols[0] if self.symbols else "SPX")
        self.selector = StructureSelector()
        self.portfolio_manager = PortfolioManager(account_size=self.account_size)
        self.sizer = PositionSizer(account_size=self.account_size)
        self.risk_metrics = RiskMetrics(account_size=self.account_size)
        self.drawdown_ctrl = DrawdownController(
            account_size=self.account_size,
            on_circuit_break=self._on_circuit_break,
        )
        self.flow_analyzer = OrderFlowAnalyzer()
        self.streaming = StreamingManager(self.client)
        self.execution = ExecutionEngine(self.client, self.order_manager, self.flow_analyzer)
        self._scheduler = BackgroundScheduler(timezone="America/New_York")
        self._hmm_fitted = False
        self._price_cache = {}

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Connect, initialise models, start streaming, and enter main loop."""
        logger.info("=" * 60)
        logger.info("AVRPE starting up")
        logger.info(f"Account size: ${self.account_size:,.0f}")
        logger.info(f"Symbols: {self.symbols}")
        logger.info("=" * 60)

        # Connect to Schwab
        self.client.connect()

        # Load historical data and fit models
        self._initialise_models()

        # Start Level 2 streaming
        equity_syms = [s for s in self.symbols if s != "SPX"]
        self.streaming.start_in_thread(equity_symbols=equity_syms)
        logger.info("Streaming started")

        # Schedule management tasks
        self._setup_schedule()
        self._scheduler.start()

        self._running = True
        logger.info("AVRPE live — entering main loop")

        # Main loop
        try:
            while self._running:
                time.sleep(60)  # Heartbeat every minute
                self._heartbeat()
        except KeyboardInterrupt:
            logger.info("Shutdown requested")
        finally:
            self._shutdown()

    def _initialise_models(self) -> None:
        """Fetch historical data, fit GARCH and HMM models."""
        logger.info("Fetching historical data for model initialisation...")
        primary_symbol = self.symbols[0] if self.symbols else "SPY"

        try:
            price_df = self.client.get_price_history(primary_symbol, period=2)
            if price_df.empty:
                logger.warning("No historical data — using defaults")
                return
            self._price_cache[primary_symbol] = price_df

            # Fit GARCH
            log_ret = price_df["close"].pct_change().dropna()
            self.garch.fit(log_ret)
            logger.info("GARCH model fitted")

            # Fit HMM
            features = HMMRegimeDetector.build_features(price_df)
            if len(features) >= 60:
                self.hmm.fit(features)
                self._hmm_fitted = True
                logger.info("HMM regime detector fitted")

            # Hurst analysis
            hurst_result = self.hurst.analyze(price_df["close"].values)
            logger.info(
                f"Hurst analysis: H={hurst_result['avg_hurst']:.3f} "
                f"market_type={hurst_result['market_type']} "
                f"premium_sell_suitability={hurst_result['premium_sell_suitability']}"
            )
        except Exception as e:
            logger.error(f"Model initialisation error: {e}")

    # ------------------------------------------------------------------
    # Scheduled tasks
    # ------------------------------------------------------------------

    def _setup_schedule(self) -> None:
        """Register all scheduled management jobs."""
        sched_cfg = self.config.get("schedule", {})

        # Daily open
        self._scheduler.add_job(
            self._daily_open, "cron",
            day_of_week="mon-fri", hour=9, minute=31,
            id="daily_open",
        )

        # Entry windows
        self._scheduler.add_job(
            self._entry_scan, "cron",
            day_of_week="mon-fri", hour=10, minute=0,
            id="morning_entry",
        )
        self._scheduler.add_job(
            self._entry_scan, "cron",
            day_of_week="mon-fri", hour=13, minute=0,
            id="afternoon_entry",
        )

        # Management
        for t in ["10:30", "12:30", "15:00", "15:45"]:
            h, m = map(int, t.split(":"))
            self._scheduler.add_job(
                self._manage_positions, "cron",
                day_of_week="mon-fri", hour=h, minute=m,
                id=f"manage_{h:02d}{m:02d}",
            )

        # Model refit (weekly)
        self._scheduler.add_job(
            self._refit_models, "cron",
            day_of_week="mon", hour=7, minute=0,
            id="weekly_refit",
        )

        logger.info("Trading schedule configured")

    def _daily_open(self) -> None:
        """Execute daily open tasks."""
        logger.info(f"--- Daily open: {date.today()} ---")
        self.drawdown_ctrl.reset_daily()
        self.portfolio_manager.reset_daily_pnl()
        self._update_price_cache()

    def _entry_scan(self) -> None:
        """Scan for new position entry opportunities."""
        if not self.execution.is_market_open():
            return

        minutes_to_close = self.execution.minutes_to_close()
        if minutes_to_close < 30:
            logger.debug("Too close to close — skipping entry scan")
            return

        dd_state = self.drawdown_ctrl.current_state
        if not dd_state.allow_new_positions:
            logger.info(f"Circuit breaker active: {dd_state.level} — no new entries")
            return

        for symbol in self.symbols:
            self._evaluate_entry(symbol)

    def _evaluate_entry(self, symbol: str) -> None:
        """Evaluate and potentially enter a new position for one symbol."""
        try:
            primary_symbol = self.symbols[0]
            price_df = self._price_cache.get(primary_symbol)
            if price_df is None:
                return

            spot_quote = self.client.get_quote(symbol)
            spot = float(spot_quote.get("lastPrice", spot_quote.get("last", 0)))
            if spot <= 0:
                return

            # Get options chain
            chain_json = self.client.get_options_chain(symbol, strike_count=15)
            options_df = self.client.parse_options_chain_to_df(chain_json)
            chain_processor = OptionsChainProcessor()
            chain_processor.update(options_df)

            # Extract IV, skew
            iv = chain_processor.atm_iv()
            skew = chain_processor.skew_25d()
            vix_proxy = iv * 100

            # Get regime
            regime = None
            if self._hmm_fitted:
                features = HMMRegimeDetector.build_features(price_df)
                if len(features) > 0:
                    regime = self.hmm.current_regime(features)

            if regime is None:
                from src.models.regime.hmm_detector import RegimeState
                regime = RegimeState(
                    label="normal_vol", state_index=0, confidence=0.5,
                    transition_prob=0.8, regime_vol=0.15, kelly_multiplier=0.35,
                    allow_new_positions=True,
                )

            # VRP signal
            signal = self.vrp_engine.update(
                price_df=price_df,
                implied_vol=iv,
                vix=vix_proxy,
                vix3m=vix_proxy * 1.02,
                skew_25d=skew,
                regime=regime,
            )

            if not signal.is_trade_signal:
                return

            # Structure selection
            structure = self.selector.select(
                symbol=symbol,
                spot=spot,
                sigma=iv,
                signal=signal,
                regime=regime,
                options_df=options_df,
            )

            if structure is None or not structure.is_valid:
                return

            # Risk check
            current_equity = self.client.get_account_balance()
            max_loss = structure.max_loss * 100
            sizing = self.sizer.size_position(
                prob_profit=structure.profit_probability,
                max_loss_per_contract=max_loss,
                credit_per_contract=structure.net_credit * 100,
                signal=signal,
                regime=regime,
                current_equity=current_equity,
                open_positions=self.order_manager.count_open_positions(),
            )

            if not sizing.is_nonzero:
                return

            # Build and submit order
            from src.broker.order_manager import OrderManager as OM
            order = self._build_order(structure, sizing.contracts)
            if order is None:
                return

            result = self.execution.submit_spread_entry(structure, order)

            if result.get("status") in ("FILLED", "PAPER"):
                self.portfolio_manager.add_position(
                    structure, order, sizing.contracts, result.get("fill_price", structure.net_credit)
                )
                logger.info(f"New position entered: {symbol} {structure.describe()}")

        except Exception as e:
            logger.error(f"Entry evaluation error ({symbol}): {e}")

    def _manage_positions(self) -> None:
        """Check existing positions for profit targets, stops, and time stops."""
        to_close = self.portfolio_manager.get_positions_to_close(
            spot=0,  # would need current spot
            sigma=0.15,
        )
        for pos, reason in to_close:
            try:
                target_debit = pos.entry_credit * 0.50 if reason == "profit_target" else pos.entry_credit * 2.0
                result = self.execution.submit_spread_exit(
                    pos.order, target_debit, reason
                )
                if result.get("status") in ("FILLED", "PAPER"):
                    fill_price = result.get("fill_price", target_debit)
                    realised = (pos.entry_credit - fill_price) * 100 * pos.contracts
                    self.portfolio_manager.update_pnl(realised)
                    self.portfolio_manager.remove_position(pos.id)
                    self.sizer.record_outcome(realised, pos.profit_target, pos.stop_loss)

                    equity = self.client.get_account_balance()
                    self.drawdown_ctrl.update(equity)
            except Exception as e:
                logger.error(f"Position management error: {e}")

    def _refit_models(self) -> None:
        """Weekly model refit."""
        logger.info("Weekly model refit starting...")
        self._update_price_cache()
        primary_symbol = self.symbols[0] if self.symbols else "SPY"
        price_df = self._price_cache.get(primary_symbol)
        if price_df is not None and len(price_df) >= 60:
            try:
                log_ret = price_df["close"].pct_change().dropna()
                self.garch.fit(log_ret)
                features = HMMRegimeDetector.build_features(price_df)
                if len(features) >= 60:
                    self.hmm.fit(features)
                    self._hmm_fitted = True
                    logger.info("Models refitted successfully")
            except Exception as e:
                logger.error(f"Model refit error: {e}")

    def _update_price_cache(self) -> None:
        """Refresh price cache."""
        for sym in self.symbols:
            try:
                df = self.client.get_price_history(sym)
                if not df.empty:
                    self._price_cache[sym] = df
            except Exception as e:
                logger.debug(f"Price cache update failed for {sym}: {e}")

    def _heartbeat(self) -> None:
        """Periodic health check."""
        summary = self.portfolio_manager.portfolio_summary(5300, 0.15)
        logger.debug(
            f"Heartbeat | equity={summary['equity']:.2f} "
            f"n_pos={summary['n_positions']} "
            f"daily_pnl={summary['daily_pnl']:.2f}"
        )

    def _build_order(self, structure, contracts: int):
        """Build an Order object from a SelectedStructure."""
        from src.broker.order_manager import Order, Leg, OrderSide
        from src.utils.helpers import option_symbol
        import datetime

        try:
            legs = []
            if structure.put_short_strike:
                sym = option_symbol(structure.underlying, structure.expiry, "put", structure.put_short_strike)
                legs.append(Leg(sym, OrderSide.SELL_TO_OPEN, contracts))
            if structure.put_long_strike:
                sym = option_symbol(structure.underlying, structure.expiry, "put", structure.put_long_strike)
                legs.append(Leg(sym, OrderSide.BUY_TO_OPEN, contracts))
            if structure.call_short_strike:
                sym = option_symbol(structure.underlying, structure.expiry, "call", structure.call_short_strike)
                legs.append(Leg(sym, OrderSide.SELL_TO_OPEN, contracts))
            if structure.call_long_strike:
                sym = option_symbol(structure.underlying, structure.expiry, "call", structure.call_long_strike)
                legs.append(Leg(sym, OrderSide.BUY_TO_OPEN, contracts))

            return Order(
                strategy_name=structure.structure_type,
                legs=legs,
                order_type="NET_CREDIT",
                limit_price=round(structure.net_credit, 2),
            )
        except Exception as e:
            logger.error(f"Order build failed: {e}")
            return None

    def _on_circuit_break(self, level) -> None:
        """Handle circuit breaker trigger."""
        logger.critical(f"CIRCUIT BREAKER TRIGGERED: {level.value}")

    def _shutdown(self) -> None:
        """Graceful shutdown."""
        self._running = False
        self._scheduler.shutdown(wait=False)
        logger.info("AVRPE shutdown complete")


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

@click.command()
@click.option("--config", default="config/config.yaml", help="Config file path")
@click.option("--paper", is_flag=True, help="Override to paper mode (no real orders)")
def main(config: str, paper: bool) -> None:
    """AVRPE - Adaptive Volatility Regime Premium Engine"""
    load_dotenv()

    with open(config) as f:
        cfg = yaml.safe_load(f)

    setup_logger(
        level=cfg.get("logging", {}).get("level", "INFO"),
        log_file=cfg.get("logging", {}).get("file", "./logs/avrpe.log"),
    )

    if paper:
        cfg["broker"]["paper_mode"] = True
        logger.info("Paper mode enabled (overridden by CLI)")

    engine = AVRPEEngine(cfg)

    # Handle SIGINT/SIGTERM
    def handle_signal(signum, frame):
        logger.info("Received shutdown signal")
        engine._running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    engine.start()


if __name__ == "__main__":
    main()
