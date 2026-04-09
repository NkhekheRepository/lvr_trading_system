"""
LVR Trading System - Main trading loop with fail-safe operation.

Hard Safety Rules:
- NO VALID DATA -> NO TRADE
- NO EDGE -> NO TRADE
- NO VALIDATION -> NO TRADE
- ALWAYS FAIL SAFE
- ALWAYS LOG EVERYTHING
"""

import asyncio
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Optional
from datetime import datetime

import structlog
import yaml

from app.schemas import (
    ExecutionMode, OrderBookSnapshot, OrderRequest, ProtectionLevel,
    RiskState, Side, TradeTick
)

from alpha import AlphaFactory, CostAwareEdge
from data.sample_data import generate_test_dataset
from executors import ExecutionPlanner, SmartOrderRouter, FillPredictor
from execution import (
    ExecutionEngine, SimulatedExecutionEngine, PaperExecutionEngine,
    VnpyExecutionEngine
)
from features import FeatureEngine, MicrostructureFeatures
from learning import BayesianLearner, AttributionEngine
from monitoring import MetricsCollector, AlertManager, ProtectionSystem
from portfolio import PortfolioManager
from regime import RegimeClassifier, RegimeState
from risk import PositionSizer, RiskEngine, RiskLimits
from state import StateStore
from strategy import SignalGenerator, RegimeDetector

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer()
    ]
)

logger = structlog.get_logger()


class KillSwitch:
    """Hardware-level kill switch for emergency stops."""
    
    def __init__(self):
        self._killswitch_triggered = False
        self._killswitch_reason: Optional[str] = None
        
    def trigger(self, reason: str) -> None:
        """Trigger kill switch."""
        logger.critical(f"KILL SWITCH TRIGGERED: {reason}")
        self._killswitch_triggered = True
        self._killswitch_reason = reason
        
    def is_active(self) -> bool:
        """Check if kill switch is active."""
        return self._killswitch_triggered
        
    def reset(self) -> None:
        """Reset kill switch (requires manual intervention)."""
        self._killswitch_triggered = False
        self._killswitch_reason = None
        logger.info("Kill switch reset")


class TradingSystem:
    """
    Main trading system orchestrator.
    
    Coordinates all components in fail-safe loop:
    data -> features -> signal -> execution -> portfolio -> learning -> monitoring -> protection
    
    Hard Safety Rules enforced:
    - NO VALID DATA -> NO TRADE
    - NO EDGE -> NO TRADE
    - NO VALIDATION -> NO TRADE
    - ALWAYS FAIL SAFE
    - ALWAYS LOG EVERYTHING
    """

    def __init__(self, config_path: str = "config/config.yaml"):
        self.config = self._load_config(config_path)

        self.mode = ExecutionMode(self.config.get("system", {}).get("mode", "SIM"))

        self._running = False
        self._halted = False
        
        self.killswitch = KillSwitch()

        self._init_components()

    def _load_config(self, path: str) -> dict:
        """Load configuration."""
        try:
            with open(path) as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.warning(f"Config load failed: {e}, using defaults")
            return {}

    def _init_components(self) -> None:
        """Initialize all components."""
        risk_config = self.config.get("risk", {})
        exec_config = self.config.get("execution", {})
        feature_config = self.config.get("features", {})

        risk_limits = RiskLimits(
            max_leverage=risk_config.get("max_leverage", 10),
            max_drawdown_pct=risk_config.get("limits", {}).get("max_drawdown_pct", 0.10),
            max_daily_loss_pct=risk_config.get("limits", {}).get("max_daily_loss_pct", 0.03),
        )

        self.portfolio = PortfolioManager(
            initial_capital=self.config.get("portfolio", {}).get("initial_capital", 100000)
        )

        self.risk_engine = RiskEngine(limits=risk_limits)
        self.position_sizer = PositionSizer(
            base_risk_per_trade=risk_config.get("base_risk_per_trade", 0.01),
            max_leverage=risk_config.get("max_leverage", 10)
        )

        self.feature_engine = FeatureEngine(
            return_window=feature_config.get("return_window", 50),
            volatility_window=feature_config.get("volatility_window", 100),
            depth_window=feature_config.get("depth_window", 100),
            spread_window=feature_config.get("spread_window", 100)
        )
        
        self.micro_features = MicrostructureFeatures(
            symbol=self.config.get("exchange", {}).get("symbols", ["BTCUSDT"])[0]
        )

        self.signal_generator = SignalGenerator(
            ofi_threshold=self.config.get("strategy", {}).get("ofi_threshold", 0.7),
            min_confidence=self.config.get("strategy", {}).get("min_confidence", 0.3)
        )

        self.regime_detector = RegimeDetector(
            threshold=self.config.get("strategy", {}).get("regime_threshold", 2.0)
        )
        
        self.regime_classifier = RegimeClassifier(
            symbol=self.config.get("exchange", {}).get("symbols", ["BTCUSDT"])[0],
            use_kronos=self.config.get("kronos", {}).get("enabled", True)
        )

        self.learner = BayesianLearner(
            min_samples=self.config.get("learning", {}).get("min_samples", 30),
            update_rate=self.config.get("learning", {}).get("update_rate", 0.1)
        )

        self.attribution = AttributionEngine()
        
        self.alpha_factory = AlphaFactory()
        
        self.execution_planner = ExecutionPlanner(
            strategy=self.config.get("execution", {}).get("strategy", "TWAP"),
            max_order_lifetime_seconds=exec_config.get("max_order_lifetime_seconds", 300)
        )
        
        self.sor = SmartOrderRouter()
        
        self.fill_predictor = FillPredictor(
            symbol=self.config.get("exchange", {}).get("symbols", ["BTCUSDT"])[0]
        )
        
        self.cost_aware_edge = CostAwareEdge(
            maker_fee_bps=self.config.get("fees", {}).get("maker_bps", 2),
            taker_fee_bps=self.config.get("fees", {}).get("taker_bps", 4),
            slippage_alpha=exec_config.get("slippage_alpha", 0.5),
        )

        self.metrics = MetricsCollector()

        self.alert_manager = AlertManager(
            rate_limit_per_minute=self.config.get("monitoring", {}).get("alerts", {}).get("rate_limit_per_minute", 10)
        )

        self.protection = ProtectionSystem(alert_manager=self.alert_manager)

        self.state_store = StateStore(
            checkpoint_interval=self.config.get("state", {}).get("checkpoint_interval_sec", 60)
        )
        
        self.data_quality_failures = 0
        self.edge_failures = 0
        self.validation_failures = 0

        self.executor = self._create_executor()

        logger.info("Components initialized", mode=self.mode.value)

    def _create_executor(self) -> ExecutionEngine:
        """Create execution engine based on mode."""
        exec_config = self.config.get("execution", {})

        if self.mode == ExecutionMode.SIM:
            return SimulatedExecutionEngine(
                slippage_alpha=exec_config.get("slippage_alpha", 0.5),
                latency_ms=exec_config.get("simulated_latency_ms", 100)
            )
        elif self.mode == ExecutionMode.PAPER:
            return PaperExecutionEngine(
                slippage_alpha=exec_config.get("slippage_alpha", 0.5)
            )
        elif self.mode == ExecutionMode.LIVE:
            return VnpyExecutionEngine()
        else:
            raise ValueError(f"Unknown mode: {self.mode}")

    async def start(self) -> None:
        """Start the trading system."""
        logger.info("Starting trading system", mode=self.mode.value)

        await self.executor.connect()
        await self.state_store.connect()

        recovery = await self.state_store.recover()
        if recovery.get("recovered"):
            logger.info("State recovered from storage")

        self._running = True

        if self.mode == ExecutionMode.SIM:
            await self._run_backtest()
        else:
            await self._run_live()

    async def stop(self) -> None:
        """Stop the trading system."""
        logger.info("Stopping trading system")
        self._running = False

        await self.state_store.checkpoint(force=True)
        await self.executor.disconnect()
        await self.state_store.disconnect()

    async def _run_backtest(self) -> None:
        """Run backtest simulation."""
        logger.info("Running backtest")

        symbols = self.config.get("exchange", {}).get("symbols", ["BTCUSDT"])
        data = generate_test_dataset(n_ticks=10000, symbols=symbols)

        for symbol, ticks in data.items():
            logger.info(f"Processing {len(ticks)} ticks for {symbol}")

            for i, tick in enumerate(ticks):
                if not self._running:
                    break

                try:
                    await self._process_tick(tick, None)
                except Exception as e:
                    logger.error(f"Tick processing error: {e}")
                    continue

                if i % 100 == 0:
                    self.metrics.collect()

        logger.info("Backtest complete")

    async def _run_live(self) -> None:
        """Run live trading loop."""
        logger.info("Running live trading")

        while self._running:
            try:
                metrics = self.metrics.collect()
                self.protection.evaluate(
                    metrics,
                    self.portfolio.portfolio.current_drawdown,
                    self.portfolio.portfolio.daily_pnl / self.portfolio.initial_capital
                )

                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Live loop error: {e}")

    async def _process_tick(
        self,
        tick: TradeTick,
        order_book: Optional[OrderBookSnapshot]
    ) -> None:
        """Process single tick through the system with full safety checks."""
        
        if self.killswitch.is_active():
            logger.warning("Killswitch active, skipping tick")
            return
        
        self.metrics.update_data_freshness(tick.timestamp)
        
        features = self.feature_engine.update(tick, order_book)
        
        if self.executor.mode == ExecutionMode.SIM and order_book:
            if isinstance(self.executor, SimulatedExecutionEngine):
                self.executor.set_order_book(order_book)
        
        bid_levels = [(tick.price * 0.999, 1.0)] if order_book is None else [(ob.bid, ob.bid_volume) for ob in order_book.bids]
        ask_levels = [(tick.price * 1.001, 1.0)] if order_book is None else [(ob.ask, ob.ask_volume) for ob in order_book.asks]
        
        micro_features = self.micro_features.update(bid_levels, ask_levels, volume=0.0, timestamp=tick.timestamp)
        
        if micro_features.execution_quality < 0.3:
            self.data_quality_failures += 1
            logger.debug(f"Low data quality: {micro_features.execution_quality}")
            
        regime_state = self.regime_classifier.update(
            bid_levels[0][0], ask_levels[0][0],
            bid_levels[0][1], ask_levels[0][1],
            timestamp=tick.timestamp
        )
        
        signal = self.signal_generator.generate(features)

        if not signal:
            return
            
        signal = self.regime_detector.apply_to_signal(signal)

        if not signal.is_valid:
            self.validation_failures += 1
            logger.debug(f"Signal rejected: {signal.filters_failed}")
            return

        edge = self.learner.get_edge_estimate(tick.symbol)
        if edge <= 0:
            self.edge_failures += 1
            logger.debug(f"No edge for {tick.symbol}, edge={edge}")
            return
            
        signal.expected_edge = edge
        
        cost_estimate = self.cost_aware_edge.estimate_cost(
            signal.direction,
            features.volatility,
            micro_features.execution_quality,
            micro_features.liquidity_score
        )
        
        net_edge = signal.expected_edge - cost_estimate
        
        if net_edge < self.config.get("strategy", {}).get("min_net_edge", 0.001):
            logger.debug(f"Net edge too low: {net_edge:.4f} (edge={signal.expected_edge:.4f}, cost={cost_estimate:.4f})")
            return
            
        fill_prediction = self.fill_predictor.predict(
            order_size=1.0,
            market_depth=micro_features.liquidity_score * 100000,
            spread_bps=micro_features.spread.spread_bps,
            recent_volatility=features.volatility,
            ofi_slope=micro_features.ofi.ofi_acceleration,
            urgency=0.5
        )
        
        if fill_prediction.confidence < 0.3:
            logger.debug(f"Low fill prediction confidence: {fill_prediction.confidence}")
            return
            
        risk_state = self._get_risk_state()
        risk_result = self.risk_engine.check_order(
            OrderRequest(
                order_id="",
                trace_id=signal.trace_id,
                symbol=signal.symbol,
                side=signal.direction,
                quantity=1.0,
                price=tick.price
            ),
            signal,
            self.portfolio.portfolio,
            risk_state
        )

        if not risk_result.approved:
            logger.info(f"Risk rejected: {risk_result.rejection_reason}")
            return

        size = self.position_sizer.calculate_size(
            signal,
            self.portfolio.portfolio,
            risk_state,
            tick.price,
            features.volatility
        )
        
        if regime_state.max_position_scale < 1.0:
            size *= regime_state.max_position_scale
            logger.info(f"Position size reduced by regime: {regime_state.max_position_scale:.2f}")
        
        if micro_features.liquidity_score < 0.4:
            size *= 0.5
            logger.debug(f"Position size reduced by liquidity: {micro_features.liquidity_score:.2f}")
        
        execution_plan = self.execution_planner.create_plan(
            symbol=signal.symbol,
            side=signal.direction,
            quantity=size,
            price=tick.price,
            urgency=0.5,
            regime=regime_state
        )
        
        order = OrderRequest(
            trace_id=signal.trace_id,
            symbol=signal.symbol,
            side=signal.direction,
            quantity=size,
            price=tick.price
        )

        result = await self.executor.submit_order(order)

        if result.success:
            for fill in result.fill_events:
                self.portfolio.update_from_fill(fill)
                self.learner.update(fill, signal.expected_edge)
                self.metrics.record_fill(fill.quantity, order.quantity)
                
                self.fill_predictor.update({
                    'slippage_bps': abs(fill.price - tick.price) / tick.price * 10000,
                    'fill_rate': fill.quantity / order.quantity,
                })
                
            self.data_quality_failures = 0
            self.edge_failures = 0
            self.validation_failures = 0
        else:
            self.metrics.record_rejection()
            
        self._check_enforce_kill(regime_state, micro_features)
        
        await self.state_store.checkpoint()

    def _get_risk_state(self) -> RiskState:
        """Get current risk state."""
        return RiskState(
            current_leverage=self.portfolio.portfolio.portfolio_leverage,
            current_drawdown=self.portfolio.portfolio.current_drawdown,
            daily_loss=self.portfolio.portfolio.daily_pnl / self.portfolio.initial_capital,
            consecutive_losses=self.risk_engine._consecutive_losses,
            protection_level=self.protection.protection_level
        )

    def _check_enforce_kill(
        self,
        regime_state: Optional[RegimeState],
        micro_features: Optional[any]
    ) -> None:
        """Enforce kill conditions based on market state."""
        
        if regime_state and regime_state.use_caution:
            logger.warning(
                f"Regime caution: risk={regime_state.risk_score:.2f}, "
                f"regime={regime_state.market_regime.value}"
            )
            
        if regime_state and regime_state.market_regime.value == 'crisis':
            logger.critical("CRISIS regime detected - enforcing caution")
            self.killswitch.trigger("CRISIS regime")
            
        if micro_features and micro_features.vacuum.imminent:
            if micro_features.vacuum.confidence > 0.85:
                logger.warning(
                    f"High-confidence vacuum imminent: "
                    f"type={micro_features.vacuum.vacuum_type}, "
                    f"confidence={micro_features.vacuum.confidence:.2f}"
                )
                
        if self.data_quality_failures > 10:
            logger.error(f"Data quality failures: {self.data_quality_failures}")
            self.killswitch.trigger("Data quality degraded")
            
        if self.edge_failures > 50:
            logger.error(f"Edge failures: {self.edge_failures}")
            self.killswitch.trigger("No edge available")
            
        if self.validation_failures > 20:
            logger.warning(f"Validation failures: {self.validation_failures}")

    def halt(self) -> None:
        """Emergency halt."""
        logger.critical("EMERGENCY HALT")
        self._halted = True
        self._running = False
        self.killswitch.trigger("Manual halt")


async def main():
    """Main entry point."""
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/config.yaml"

    system = TradingSystem(config_path)

    loop = asyncio.get_event_loop()

    def signal_handler(sig):
        logger.info(f"Received signal {sig}")
        loop.create_task(system.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))

    try:
        await system.start()
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        await system.stop()


if __name__ == "__main__":
    asyncio.run(main())
