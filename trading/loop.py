"""
Trading Loop - Main orchestration for the trading system.

Wires together all processors, engines, and data sources into a cohesive system.
"""

import asyncio
import logging
from typing import Optional, Any
from dataclasses import dataclass, field
from datetime import datetime

from core.event import Event, EventType
from core.bus import EventBus, RedisEventBus, BusConfig
from core.state import DistributedState, StateConfig
from core.processor import ProcessorConfig

from processors.base_processor import BaseProcessor
from processors.feature_processor import FeatureProcessor
from processors.alpha_processor import AlphaProcessor
from processors.edge_estimation import EdgeEstimationEngine
from processors.edge_truth import EdgeTruthEngine
from processors.positive_expectation import PositiveExpectationEngine
from processors.regime_processor import RegimeProcessor
from processors.reality_gap import RealityGapMonitor
from processors.portfolio_processor import PortfolioProcessor
from processors.risk_processor import RiskProcessor
from processors.execution_processor import ExecutionProcessor
from processors.learning_processor import LearningProcessor

from engines.positivity import PositivityEngine
from engines.capital_efficiency import CapitalEfficiencyEngine
from engines.execution_quality import ExecutionQualityEngine
from engines.drawdown_analyzer import DrawdownAnalyzer
from engines.strategy_survival import StrategySurvivalEngine
from engines.trade_scarcity import TradeScarcityEngine

from validation.data_validator import DataValidator
from validation.position_reconciler import PositionReconciler
from validation.time_sync import TimeSynchronizer

from observability.logger import get_logger
from observability.metrics import TradingMetrics, MetricConfig
from observability.tracer import get_tracer

logger = get_logger(__name__)


@dataclass
class TradingConfig:
    """Configuration for the trading loop."""
    execution_mode: str = "SIM"
    symbols: list[str] = field(default_factory=lambda: ['BTCUSDT', 'ETHUSDT'])
    initial_capital: float = 100000.0
    max_leverage: float = 3.0
    max_drawdown: float = 0.15
    redis_url: str = "redis://localhost:6379"
    postgres_url: str = "postgresql://postgres:postgres@localhost:5432/trading"
    metrics_port: int = 9090
    health_port: int = 8080


class TradingLoop:
    """
    Main trading system orchestration.
    
    Responsibilities:
    - Initialize all components
    - Wire processors together
    - Manage lifecycle
    - Handle errors gracefully
    """
    
    def __init__(
        self,
        config: Optional[TradingConfig] = None,
    ):
        self.config = config or TradingConfig()
        
        self._bus: Optional[EventBus] = None
        self._state: Optional[DistributedState] = None
        self._metrics: Optional[TradingMetrics] = None
        self._tracer = get_tracer("trading_loop")
        
        self._processors: dict[str, BaseProcessor] = {}
        self._engines: dict[str, Any] = {}
        self._validators: dict[str, Any] = {}
        
        self._running = False
        self._shutdown_event = asyncio.Event()
    
    async def initialize(self) -> None:
        """Initialize all components."""
        logger.info("Initializing trading loop", config=self.config)
        
        await self._init_bus()
        await self._init_state()
        await self._init_metrics()
        await self._init_processors()
        await self._init_engines()
        await self._init_validators()
        
        logger.info("Trading loop initialized successfully")
    
    async def _init_bus(self) -> None:
        """Initialize event bus."""
        bus_config = BusConfig(
            redis_url=self.config.redis_url,
            postgres_url=self.config.postgres_url,
            enable_persistence=True,
        )
        
        self._bus = RedisEventBus(bus_config)
        await self._bus.initialize()
        
        logger.info("Event bus initialized")
    
    async def _init_state(self) -> None:
        """Initialize distributed state."""
        state_config = StateConfig(
            redis_url=self.config.redis_url,
            postgres_url=self.config.postgres_url,
        )
        
        self._state = DistributedState(state_config)
        await self._state.initialize()
        
        logger.info("Distributed state initialized")
    
    async def _init_metrics(self) -> None:
        """Initialize metrics collection."""
        metric_config = MetricConfig(
            namespace="trading",
            subsystem="system",
        )
        
        self._metrics = TradingMetrics(metric_config)
        
        logger.info("Metrics initialized")
    
    async def _init_processors(self) -> None:
        """Initialize all processors."""
        processor_configs = {
            'feature': ProcessorConfig(name='feature', priority=1, queue_size=1000),
            'alpha': ProcessorConfig(name='alpha', priority=2, queue_size=500),
            'edge_estimation': ProcessorConfig(name='edge_estimation', priority=3, queue_size=500),
            'edge_truth': ProcessorConfig(name='edge_truth', priority=3, queue_size=500),
            'positive_expectation': ProcessorConfig(name='positive_expectation', priority=4, queue_size=500),
            'regime': ProcessorConfig(name='regime', priority=2, queue_size=500),
            'reality_gap': ProcessorConfig(name='reality_gap', priority=3, queue_size=500),
            'portfolio': ProcessorConfig(name='portfolio', priority=5, queue_size=500),
            'risk': ProcessorConfig(name='risk', priority=1, queue_size=500),
            'execution': ProcessorConfig(name='execution', priority=1, queue_size=500),
            'learning': ProcessorConfig(name='learning', priority=6, queue_size=100),
        }
        
        self._processors['feature'] = FeatureProcessor(
            bus=self._bus,
            state=self._state,
            config=processor_configs['feature'],
        )
        
        self._processors['alpha'] = AlphaProcessor(
            bus=self._bus,
            state=self._state,
            config=processor_configs['alpha'],
        )
        
        self._processors['edge_estimation'] = EdgeEstimationEngine(
            bus=self._bus,
            state=self._state,
            config=processor_configs['edge_estimation'],
        )
        
        self._processors['edge_truth'] = EdgeTruthEngine(
            bus=self._bus,
            state=self._state,
            config=processor_configs['edge_truth'],
        )
        
        self._processors['positive_expectation'] = PositiveExpectationEngine(
            bus=self._bus,
            state=self._state,
            config=processor_configs['positive_expectation'],
        )
        
        self._processors['regime'] = RegimeProcessor(
            bus=self._bus,
            state=self._state,
            config=processor_configs['regime'],
        )
        
        self._processors['reality_gap'] = RealityGapMonitor(
            bus=self._bus,
            state=self._state,
            config=processor_configs['reality_gap'],
        )
        
        self._processors['portfolio'] = PortfolioProcessor(
            bus=self._bus,
            state=self._state,
            config=processor_configs['portfolio'],
            initial_capital=self.config.initial_capital,
        )
        
        self._processors['risk'] = RiskProcessor(
            bus=self._bus,
            state=self._state,
            config=processor_configs['risk'],
            max_leverage=self.config.max_leverage,
            max_drawdown=self.config.max_drawdown,
        )
        
        self._processors['execution'] = ExecutionProcessor(
            bus=self._bus,
            state=self._state,
            config=processor_configs['execution'],
            execution_mode=self.config.execution_mode,
        )
        
        self._processors['learning'] = LearningProcessor(
            bus=self._bus,
            state=self._state,
            config=processor_configs['learning'],
        )
        
        for name, processor in self._processors.items():
            await processor.start()
        
        logger.info(f"Initialized {len(self._processors)} processors")
    
    async def _init_engines(self) -> None:
        """Initialize all control engines."""
        self._engines['positivity'] = PositivityEngine(
            bus=self._bus,
            state=self._state,
        )
        
        self._engines['capital_efficiency'] = CapitalEfficiencyEngine(
            bus=self._bus,
            state=self._state,
            initial_capital=self.config.initial_capital,
        )
        
        self._engines['execution_quality'] = ExecutionQualityEngine(
            bus=self._bus,
            state=self._state,
        )
        
        self._engines['drawdown'] = DrawdownAnalyzer(
            bus=self._bus,
            state=self._state,
            initial_capital=self.config.initial_capital,
        )
        
        self._engines['survival'] = StrategySurvivalEngine(
            bus=self._bus,
            state=self._state,
        )
        
        self._engines['scarcity'] = TradeScarcityEngine(
            bus=self._bus,
            state=self._state,
        )
        
        logger.info(f"Initialized {len(self._engines)} control engines")
    
    async def _init_validators(self) -> None:
        """Initialize all validators."""
        self._validators['data'] = DataValidator(
            bus=self._bus,
            state=self._state,
        )
        
        self._validators['position'] = PositionReconciler(
            bus=self._bus,
            state=self._state,
        )
        
        self._validators['time'] = TimeSynchronizer(
            bus=self._bus,
            state=self._state,
        )
        
        logger.info(f"Initialized {len(self._validators)} validators")
    
    async def start(self) -> None:
        """Start the trading loop."""
        if self._running:
            logger.warning("Trading loop already running")
            return
        
        logger.info("Starting trading loop")
        
        self._running = True
        self._shutdown_event.clear()
        
        asyncio.create_task(self._event_consumer())
        asyncio.create_task(self._health_monitor())
        
        logger.info("Trading loop started")
    
    async def stop(self) -> None:
        """Stop the trading loop."""
        if not self._running:
            return
        
        logger.info("Stopping trading loop")
        
        self._running = False
        
        for name, processor in self._processors.items():
            try:
                await processor.stop()
            except Exception as e:
                logger.error(f"Error stopping processor {name}: {e}")
        
        if self._bus:
            await self._bus.shutdown()
        
        if self._state:
            await self._state.shutdown()
        
        self._shutdown_event.set()
        
        logger.info("Trading loop stopped")
    
    async def _event_consumer(self) -> None:
        """Consume events from the bus and route to processors."""
        while self._running:
            try:
                event = await self._bus.consume(timeout=1.0)
                
                if event is None:
                    continue
                
                await self._route_event(event)
                
            except Exception as e:
                logger.error(f"Error in event consumer: {e}")
                await asyncio.sleep(0.1)
    
    async def _route_event(self, event: Event) -> None:
        """Route event to appropriate processors."""
        trace_id = self._tracer.start_trace(event.trace_id)
        
        try:
            with self._tracer.start_span(f"route_{event.type.value}") as span:
                span.add_tag('event_type', event.type.value)
                span.add_tag('symbol', event.symbol or 'N/A')
                
                self._metrics.record_event(event.type.value)
                
                for name, processor in self._processors.items():
                    if event.type in processor.event_types():
                        try:
                            result = await processor.process(event)
                            
                            if result:
                                if isinstance(result, list):
                                    for e in result:
                                        await self._bus.publish(e)
                                else:
                                    await self._bus.publish(result)
                                    
                        except Exception as e:
                            logger.error(f"Processor {name} error: {e}")
                            self._metrics.record_error(name, type(e).__name__)
                            
        finally:
            self._tracer.end_trace()
    
    async def _health_monitor(self) -> None:
        """Monitor system health."""
        while self._running:
            try:
                for name, processor in self._processors.items():
                    healthy = await processor.health_check()
                    self._metrics.set_health_status(name, healthy)
                
                await asyncio.sleep(10)
                
            except Exception as e:
                logger.error(f"Error in health monitor: {e}")
                await asyncio.sleep(5)
    
    async def submit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        order_type: str = "LIMIT",
    ) -> Optional[str]:
        """Submit a new order through the system."""
        from core.event import Event, EventType
        
        event = Event.create(
            event_type=EventType.ORDER_SUBMITTED,
            symbol=symbol,
            payload={
                'order_id': f"{symbol}_{datetime.now().timestamp()}",
                'symbol': symbol,
                'side': side,
                'quantity': quantity,
                'price': price,
                'type': order_type,
            },
            source="trading_loop_api",
        )
        
        await self._bus.publish(event)
        
        return event.payload['order_id']
    
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an existing order."""
        event = Event.create(
            event_type=EventType.ORDER_CANCELED,
            symbol=symbol,
            payload={
                'order_id': order_id,
                'symbol': symbol,
            },
            source="trading_loop_api",
        )
        
        await self._bus.publish(event)
        return True
    
    async def get_portfolio_status(self) -> dict:
        """Get current portfolio status."""
        if not self._state:
            return {}
        
        portfolio = await self._state.get("portfolio:global")
        
        if portfolio and portfolio.value:
            return portfolio.value
        
        return {
            'total_value': self.config.initial_capital,
            'cash': self.config.initial_capital,
            'unrealized_pnl': 0,
            'realized_pnl': 0,
            'drawdown_pct': 0,
            'leverage': 1.0,
        }
    
    async def get_system_status(self) -> dict:
        """Get overall system status."""
        processor_status = {}
        for name, processor in self._processors.items():
            processor_status[name] = {
                'healthy': await processor.health_check(),
                'queue_depth': processor.get_queue_depth() if hasattr(processor, 'get_queue_depth') else 0,
            }
        
        return {
            'running': self._running,
            'execution_mode': self.config.execution_mode,
            'processors': processor_status,
            'engines': list(self._engines.keys()),
            'validators': list(self._validators.keys()),
        }


async def create_trading_loop(
    config: Optional[TradingConfig] = None,
) -> TradingLoop:
    """Create and initialize a trading loop."""
    loop = TradingLoop(config)
    await loop.initialize()
    return loop
