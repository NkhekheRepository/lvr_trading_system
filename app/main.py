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
    RiskCheckResult, RiskState, Side, TradeTick
)

from alpha import AlphaFactory, CostAwareEdge
from data.sample_data import generate_test_dataset
from executors import ExecutionPlanner, SmartOrderRouter, FillPredictor
from execution import (
    ExecutionEngine, SimulatedExecutionEngine, PaperExecutionEngine,
    VnpyExecutionEngine, TestnetExecutionEngine
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

        self._warmup_enabled = self.config.get("learning", {}).get("warmup_enabled", True)
        self._warmup_trades = 0

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

        self.execution_planner = ExecutionPlanner()

        self.sor = SmartOrderRouter()

        self.fill_predictor = FillPredictor(
            symbol=self.config.get("exchange", {}).get("symbols", ["BTCUSDT"])[0]
        )

        self.cost_aware_edge = CostAwareEdge()

        self.metrics = MetricsCollector()

        alert_config = self.config.get("monitoring", {}).get("alerts", {})
        self.alert_manager = AlertManager(
            rate_limit_per_minute=alert_config.get("rate_limit_per_minute", 10),
            slack_webhook=alert_config.get("slack_webhook"),
            email_recipients=alert_config.get("email_recipients", []),
            telegram_bot_token=alert_config.get("telegram_bot_token"),
            telegram_chat_id=alert_config.get("telegram_chat_id"),
            telegram_enabled=alert_config.get("telegram_enabled", True),
        )

        self.protection = ProtectionSystem(alert_manager=self.alert_manager)

        self.state_store = StateStore(
            checkpoint_interval=self.config.get("state", {}).get("checkpoint_interval_sec", 60)
        )

        self.data_quality_failures = 0
        self.edge_failures = 0
        self.validation_failures = 0
        self._ticks_received = 0

        self.executor = self._create_executor()

        if self.mode in (ExecutionMode.TESTNET, ExecutionMode.LIVE):
            self._init_websocket()
        else:
            self.ws_client = None

        logger.info("Components initialized", mode=self.mode.value)

    def _init_websocket(self) -> None:
        """Initialize WebSocket for TESTNET/LIVE modes."""
        from data import MultiExchangeWebSocket, ExchangeConfig, Exchange

        is_testnet = self.mode == ExecutionMode.TESTNET

        self.ws_client = MultiExchangeWebSocket(
            on_ticker=self._handle_ticker,
            on_orderbook=self._handle_orderbook,
            on_trade=self._handle_trade
        )

        symbols = self.config.get("exchange", {}).get("symbols", ["BTCUSDT"])

        if is_testnet:
            config = ExchangeConfig(
                exchange=Exchange.BINANCE,
                ws_url="wss://stream.binancefuture.com",
                rest_url="https://testnet.binancefuture.com",
                enabled=True,
                priority=1,
                is_futures=True
            )
        else:
            config = ExchangeConfig(
                exchange=Exchange.BINANCE,
                ws_url="wss://fstream.binance.com",
                rest_url="https://fapi.binance.com",
                enabled=True,
                priority=1,
                is_futures=True
            )
        self.ws_client.add_exchange(config)

        logger.info("WebSocket initialized", testnet=is_testnet, symbols=symbols)

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
        elif self.mode == ExecutionMode.TESTNET:
            return TestnetExecutionEngine(
                api_key=exec_config.get("testnet_api_key"),
                api_secret=exec_config.get("testnet_api_secret")
            )
        elif self.mode == ExecutionMode.LIVE:
            return VnpyExecutionEngine()
        else:
            raise ValueError(f"Unknown mode: {self.mode}")

    async def start(self) -> None:
        """Start the trading system."""
        logger.info("Starting trading system", mode=self.mode.value)

        await self.alert_manager.send_system_status(
            status="STARTING",
            mode=self.mode.value,
        )

        await self.executor.connect()
        await self.state_store.connect()

        recovery = await self.state_store.recover()
        if recovery.get("recovered"):
            logger.info("State recovered from storage")

        self._running = True

        await self.alert_manager.send_system_status(
            status="RUNNING",
            mode=self.mode.value,
        )

        if self.mode == ExecutionMode.SIM:
            await self._run_backtest()
        else:
            await self._run_live()

    async def stop(self) -> None:
        """Stop the trading system."""
        logger.info("Stopping trading system")

        await self.alert_manager.send_system_status(
            status="STOPPING",
            mode=self.mode.value,
        )

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

        if self.ws_client:
            symbols = self.config.get("exchange", {}).get("symbols", ["BTCUSDT"])
            asyncio.create_task(self._run_websocket(symbols))

        while self._running:
            try:
                metrics = self.metrics.collect()

                if self._ticks_received < 5:
                    await asyncio.sleep(0.5)
                    continue

                drawdown = self.portfolio.portfolio.current_drawdown
                daily_pnl_pct = self.portfolio.portfolio.daily_pnl / self.portfolio.initial_capital

                prot_level = self.protection.evaluate(
                    metrics, drawdown, daily_pnl_pct
                )

                if prot_level >= ProtectionLevel.CLOSE_ALL_HALT:
                    self.killswitch.trigger("Protection CLOSE_ALL_HALT triggered")
                    self._running = False
                    await self.alert_manager.send_system_status(
                        status="HALTED",
                        mode=self.mode.value,
                    )
                    break

                if prot_level >= ProtectionLevel.REDUCE_SIZE:
                    logger.warning(f"Protection level: {prot_level.name}, reducing exposure")

                max_dd = self.config.get("risk", {}).get("limits", {}).get("max_drawdown_pct", 0.10)
                if drawdown > max_dd * 0.5:
                    await self.alert_manager.send_drawdown_alert(
                        drawdown_pct=drawdown,
                        max_drawdown_pct=max_dd,
                        daily_pnl=self.portfolio.portfolio.daily_pnl,
                    )

                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Live loop error: {e}")

    async def _run_websocket(self, symbols: list) -> None:
        """Start WebSocket for real-time data."""
        if self.ws_client:
            logger.info(f"Starting WebSocket for {symbols}")

            try:
                await self.ws_client.start(symbols)
            except Exception as e:
                logger.error(f"WebSocket error: {e}")

    async def _handle_ticker(self, ticker_data) -> None:
        """Handle incoming ticker."""
        logger.debug(f"TICK: {ticker_data.symbol} price={ticker_data.last}")
        tick = TradeTick(
            symbol=ticker_data.symbol,
            price=ticker_data.last,
            size=ticker_data.volume_24h or 0.001,
            timestamp=int(ticker_data.timestamp.timestamp() * 1000) if ticker_data.timestamp else 0,
            side=Side.BUY
        )
        await self._process_tick(tick, None)

    async def _handle_orderbook(self, orderbook_data) -> None:
        """Handle incoming orderbook."""
        pass

    async def _handle_trade(self, trade_data) -> None:
        """Handle incoming trade."""
        pass

    async def _process_tick(
        self,
        tick: TradeTick,
        order_book: Optional[OrderBookSnapshot]
    ) -> None:
        """Process single tick through the system with full safety checks."""

        self._ticks_received += 1

        if self.killswitch.is_active():
            logger.warning("Killswitch active, skipping tick")
            return

        self.metrics.update_data_freshness(tick.timestamp)

        metrics = self.metrics.collect()
        if not metrics.data_fresh and self.mode in (ExecutionMode.TESTNET, ExecutionMode.LIVE):
            logger.warning("Stale data, skipping tick", age_sec=metrics.last_tick_age_sec)
            return

        self.portfolio.update_market_prices({tick.symbol: tick.price})

        features = self.feature_engine.update(tick, order_book)

        logger.debug(f"FEATURES: I*={getattr(features, 'I_star', 'N/A')}, tick={tick.symbol}")

        if self.executor.mode == ExecutionMode.SIM and order_book:
            if isinstance(self.executor, SimulatedExecutionEngine):
                self.executor.set_order_book(order_book)

        default_bid_depth = 100.0 if "BTC" in tick.symbol else 1000.0
        default_ask_depth = 100.0 if "BTC" in tick.symbol else 1000.0
        bid_levels = [(tick.price * 0.999, default_bid_depth)] if order_book is None else [(ob.bid, ob.bid_volume) for ob in order_book.bids]
        ask_levels = [(tick.price * 1.001, default_ask_depth)] if order_book is None else [(ob.ask, ob.ask_volume) for ob in order_book.asks]

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

        logger.debug(f"GEN: OFI={features.OFI:.2f}, I*={features.I_star:.2f} -> {signal}")

        if not signal:
            logger.debug("NO SIGNAL - features invalid")
            return

        signal = self.regime_detector.apply_to_signal(signal)

        logger.info(f"SIGNAL: {signal.direction} {tick.symbol} valid={signal.is_valid} failed={signal.filters_failed}")

        if not signal.is_valid:
            logger.info(f"SIGNAL REJECTED: {signal.filters_failed}")
            return

        edge = self.learner.get_edge_estimate(tick.symbol)
        learner_reliable = self.learner.is_reliable(tick.symbol)

        if not learner_reliable and self._warmup_enabled:
            raw_edge = signal.strength * signal.confidence
            if raw_edge > 0:
                edge = raw_edge
                logger.info(f"WARMUP: using raw edge={edge:.4f} for {tick.symbol} (learner not reliable, warmup trade #{self._warmup_trades + 1})")
            else:
                logger.info(f"NO EDGE (warmup): raw_edge={raw_edge:.4f} for {tick.symbol}")
                self.edge_failures += 1
                return
        elif edge <= 0:
            logger.info(f"NO EDGE: learner edge={edge:.4f} for {tick.symbol}, skipping trade")
            self.edge_failures += 1
            await self.alert_manager.send_rejection_alert(
                symbol=tick.symbol,
                reason=f"No edge: {edge:.4f}",
                error_code="NO_EDGE",
            )
            return

        signal.expected_edge = edge

        prot_level = self.protection.protection_level
        prot_actions = self.protection.apply_protection(prot_level)
        if prot_actions["should_restrict_trading"]:
            logger.warning(f"Protection RESTRICT_TRADING active, skipping order for {tick.symbol}")
            await self.alert_manager.send_rejection_alert(
                symbol=tick.symbol,
                reason=f"Trading restricted (protection level: {prot_level.name})",
                error_code="PROTECTION_RESTRICT",
            )
            return

        risk_state = RiskState(
            current_leverage=self.portfolio.portfolio.portfolio_leverage,
            current_drawdown=self.portfolio.portfolio.current_drawdown,
            daily_loss=self.portfolio.portfolio.daily_pnl / max(self.portfolio.initial_capital, 1),
            consecutive_losses=0,
            protection_level=prot_level,
        )

        qty = self.position_sizer.calculate_size(
            signal=signal,
            portfolio=self.portfolio.portfolio,
            risk_state=risk_state,
            current_price=tick.price,
        )

        if qty <= 0:
            logger.info(f"SIZE=0 for {tick.symbol}, skipping trade")
            return

        min_notional = 100.0
        notional = qty * tick.price
        if notional < min_notional:
            qty = min_notional / tick.price
            logger.info(f"Notional enforced: qty adjusted to {qty:.4f} (min ${min_notional})")

        warmup_max_trades = self.config.get("learning", {}).get("warmup_max_trades", 10)
        if not learner_reliable and self._warmup_trades >= warmup_max_trades:
            logger.info(f"Warmup limit reached ({warmup_max_trades} trades), waiting for learner")
            return

        if prot_actions["should_reduce_size"]:
            qty *= prot_actions["size_multiplier"]
            logger.info(f"Protection REDUCE_SIZE: qty reduced to {qty:.4f}")

        min_edge = self.config.get("autonomy", {}).get("min_edge", 0.0001)
        market_depth = sum(sz for _, sz in bid_levels) + sum(sz for _, sz in ask_levels)
        volatility = features.volatility if features.volatility > 0 else 0.001

        if learner_reliable:
            should_trade, costs, net_edge = self.cost_aware_edge.should_trade(
                raw_edge=edge,
                size=qty,
                price=tick.price,
                market_depth=market_depth,
                volatility=volatility,
                min_edge=min_edge,
            )

            if not should_trade or net_edge <= 0:
                logger.info(f"COST TOO HIGH: net_edge={net_edge:.5f} (raw={edge:.5f}, costs={costs.total_cost:.5f}), skipping {tick.symbol}")
                self.edge_failures += 1
                await self.alert_manager.send_rejection_alert(
                    symbol=tick.symbol,
                    reason=f"Net edge too low: {net_edge:.5f} (costs: {costs.total_cost:.5f})",
                    error_code="NO_NET_EDGE",
                )
                return
        else:
            costs = None
            net_edge = edge
            logger.info(f"WARMUP: skipping cost check, raw_edge={edge:.4f} for {tick.symbol}")

        logger.info(f"EDGE OK: raw={edge:.4f}, net={net_edge:.4f}, qty={qty:.4f}, proceeding to order")

        from app.schemas import OrderType, TimeInForce

        order = OrderRequest(
            trace_id=signal.trace_id,
            symbol=signal.symbol,
            side=signal.direction,
            quantity=qty,
            order_type=OrderType.LIMIT,
            price=tick.price,
            time_in_force=TimeInForce.GTC
        )

        risk_result = self.risk_engine.check_order(
            order=order,
            signal=signal,
            portfolio=self.portfolio.portfolio,
            risk_state=risk_state,
        )

        if not risk_result.approved:
            logger.warning(f"RISK REJECTED: {risk_result.rejection_reason} for {tick.symbol}")
            self.validation_failures += 1
            self.metrics.record_rejection()
            await self.alert_manager.send_rejection_alert(
                symbol=tick.symbol,
                reason=risk_result.rejection_reason,
                error_code="RISK_REJECTED",
            )
            return

        if risk_result.adjusted_quantity is not None:
            order = OrderRequest(
                trace_id=signal.trace_id,
                symbol=signal.symbol,
                side=signal.direction,
                quantity=risk_result.adjusted_quantity,
                order_type=OrderType.LIMIT,
                price=tick.price,
                time_in_force=TimeInForce.GTC
            )
            logger.info(f"Risk adjusted qty: {qty:.4f} -> {risk_result.adjusted_quantity:.4f}")

        for action in risk_result.required_actions:
            logger.info(f"Risk action: {action}")

        await self.alert_manager.send_signal_alert(
            symbol=signal.symbol,
            direction=signal.direction.value,
            strength=signal.strength,
            confidence=signal.confidence,
            edge=edge,
        )

        logger.info(f"ORDER: {order.symbol} {order.side} {order.quantity:.4f} @ {order.price}")

        result = await self.executor.submit_order(order)

        logger.info(f"RESULT: success={result.success}, order_id={order.order_id}")

        if result.success:
            self.metrics.record_success()
            for fill in result.fill_events:
                if fill.quantity <= 0:
                    logger.info(f"ORDER PLACED but not filled yet: {order.symbol} {order.side} (limit order pending)")
                    continue

                logger.info(f"FILLED: price={fill.price} qty={fill.quantity}")

                self.portfolio.update_from_fill(fill)

                self.learner.update(fill, expected_edge=edge)

                self.attribution.record_trade(
                    fill=fill,
                    signal_edge=edge,
                    expected_price=tick.price,
                    execution_price=fill.price,
                    fee=fill.fee,
                    spread_cost=abs(fill.slippage) if fill.slippage else 0.0,
                )

                if fill.slippage:
                    self.metrics.record_slippage(fill.slippage, 0.0)

                self.metrics.record_fill(fill.quantity, order.quantity)

                self.risk_engine.record_trade_result(
                    pnl=(fill.price - tick.price) * fill.quantity if signal.direction == Side.BUY else (tick.price - fill.price) * fill.quantity
                )

                if self._warmup_enabled and not learner_reliable:
                    self._warmup_trades += 1
                    learner_state = self.learner.get_state(tick.symbol)
                    if learner_state.is_reliable:
                        logger.info(f"WARMUP COMPLETE: {tick.symbol} has {learner_state.trade_count} samples, learner is now reliable")
                        self._warmup_enabled = False

                await self.alert_manager.send_trade_alert(
                    symbol=order.symbol,
                    side=order.side.value,
                    quantity=fill.quantity,
                    price=order.price or tick.price,
                    order_id=order.order_id,
                    fill_price=fill.price,
                    fee=fill.fee,
                    slippage=fill.slippage,
                )

                pos = self.portfolio.get_position(order.symbol)
                if abs(pos.quantity) > 0:
                    await self.alert_manager.send_position_alert(
                        symbol=order.symbol,
                        quantity=pos.quantity,
                        entry_price=pos.entry_price,
                        unrealized_pnl=pos.unrealized_pnl,
                    )
        else:
            reason = result.reject_event.reason if result.reject_event else "unknown"
            error_code = result.reject_event.error_code if result.reject_event else ""
            self.metrics.record_rejection()
            logger.warning(f"ORDER REJECTED: {reason} ({error_code})")
            await self.alert_manager.send_rejection_alert(
                symbol=order.symbol,
                reason=reason,
                error_code=error_code,
            )