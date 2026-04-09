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

from engines.positivity import StagedPositivityGovernor
from engines.capital_efficiency import CapitalEfficiencyEngine
from engines.execution_quality import ExecutionQualityEngine
from engines.drawdown_analyzer import DrawdownAnalyzer
from engines.strategy_survival import StrategySurvivalEngine
from engines.trade_scarcity import TradeRateGovernor

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
        self._engines['positivity'] = StagedPositivityGovernor(
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
        
        self._engines['trade_rate'] = TradeRateGovernor(
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
        """Route event through sequential pipeline with backpressure."""
        trace_id = self._tracer.start_trace(event.trace_id)
        
        try:
            with self._tracer.start_span(f"route_{event.type.value}") as span:
                span.add_tag('event_type', event.type.value)
                span.add_tag('symbol', event.symbol or 'N/A')
                
                self._metrics.record_event(event.type.value)
                
                if self._should_use_sequential_pipeline(event):
                    await self._process_sequential_pipeline(event)
                else:
                    await self._process_parallel(event)
                            
        finally:
            self._tracer.end_trace()
    
    def _should_use_sequential_pipeline(self, event: Event) -> bool:
        """Determine if event should use sequential pipeline."""
        pipeline_types = {
            EventType.EDGE_ESTIMATED,
            EventType.TRADE_DECISION,
            EventType.RISK_EVALUATED,
        }
        return event.type in pipeline_types
    
    async def _process_sequential_pipeline(self, event: Event) -> None:
        """
        Process event through sequential pipeline with explicit steps.
        
        Pipeline:
        1. Validate data integrity
        2. Enrich with market context
        3. Compute alpha
        4. Evaluate edge
        5. Check positivity governor
        6. Evaluate risk
        7. Execute
        8. Record outcome
        """
        steps = [
            ('validate', self._validate_step),
            ('enrich', self._enrich_step),
            ('compute_alpha', self._compute_alpha_step),
            ('evaluate_edge', self._evaluate_edge_step),
            ('check_governors', self._check_governors_step),
            ('evaluate_risk', self._evaluate_risk_step),
            ('execute', self._execute_step),
            ('record', self._record_step),
        ]
        
        ctx = PipelineContext(event)
        
        for step_name, step_fn in steps:
            step_start = asyncio.get_event_loop().time()
            
            try:
                ctx = await step_fn(ctx)
                
                if ctx.blocked:
                    logger.info(f"Pipeline blocked at step {step_name}: {ctx.block_reason}")
                    break
                    
            except PipelineStepFailed as e:
                logger.warning(f"Pipeline step {step_name} failed: {e}")
                ctx.failed_step = step_name
                ctx.error = e
                break
                
            finally:
                step_duration = asyncio.get_event_loop().time() - step_start
                self._metrics.observe_pipeline_step_duration(step_name, step_duration)
    
    async def _validate_step(self, ctx: 'PipelineContext') -> 'PipelineContext':
        """Step 1: Validate data integrity."""
        validator = self._validators.get('data')
        if validator:
            is_valid = await validator.validate(ctx.event)
            if not is_valid:
                raise PipelineStepFailed("Data validation failed")
        return ctx
    
    async def _enrich_step(self, ctx: 'PipelineContext') -> 'PipelineContext':
        """Step 2: Enrich with market context."""
        feature_processor = self._processors.get('feature')
        if feature_processor and ctx.event.type in feature_processor.event_types():
            await feature_processor.process(ctx.event)
        return ctx
    
    async def _compute_alpha_step(self, ctx: 'PipelineContext') -> 'PipelineContext':
        """Step 3: Compute alpha."""
        alpha_processor = self._processors.get('alpha')
        if alpha_processor and ctx.event.type in alpha_processor.event_types():
            result = await alpha_processor.process(ctx.event)
            ctx.alpha_result = result
        return ctx
    
    async def _evaluate_edge_step(self, ctx: 'PipelineContext') -> 'PipelineContext':
        """Step 4: Evaluate edge."""
        edge_processor = self._processors.get('edge_estimation')
        if edge_processor and ctx.event.type in edge_processor.event_types():
            result = await edge_processor.process(ctx.event)
            ctx.edge_result = result
        return ctx
    
    async def _check_governors_step(self, ctx: 'PipelineContext') -> 'PipelineContext':
        """Step 5: Check positivity and rate governors."""
        positivity = self._engines.get('positivity')
        trade_rate = self._engines.get('trade_rate')
        
        if positivity:
            portfolio = await self.get_portfolio_status()
            drawdown = portfolio.get('drawdown_pct', 0)
            system_edge = ctx.edge_result.payload.get('expected_edge', 0) if ctx.edge_result else 0
            
            decision = positivity.evaluate(system_edge, drawdown)
            ctx.positivity_decision = decision
            
            if decision.block_signals:
                ctx.blocked = True
                ctx.block_reason = f"positivity_phase={decision.phase}"
        
        if trade_rate and not ctx.blocked and ctx.event.symbol:
            should_block, reason = await trade_rate.should_block_trade(ctx.event.symbol)
            if should_block:
                ctx.blocked = True
                ctx.block_reason = f"trade_rate={reason}"
        
        return ctx
    
    async def _evaluate_risk_step(self, ctx: 'PipelineContext') -> 'PipelineContext':
        """Step 6: Evaluate risk."""
        risk_processor = self._processors.get('risk')
        if risk_processor and ctx.event.type in risk_processor.event_types():
            result = await risk_processor.process(ctx.event)
            ctx.risk_result = result
            
            if result and not result.payload.get('approved', False):
                ctx.blocked = True
                ctx.block_reason = "risk_rejected"
        
        return ctx
    
    async def _execute_step(self, ctx: 'PipelineContext') -> 'PipelineContext':
        """Step 7: Execute order."""
        if ctx.blocked:
            return ctx
        
        execution_processor = self._processors.get('execution')
        if execution_processor and ctx.event.type in execution_processor.event_types():
            result = await execution_processor.process(ctx.event)
            ctx.execution_result = result
            
            if result:
                if isinstance(result, list):
                    for e in result:
                        await self._bus.publish(e)
                else:
                    await self._bus.publish(result)
        
        return ctx
    
    async def _record_step(self, ctx: 'PipelineContext') -> 'PipelineContext':
        """Step 8: Record outcome."""
        if ctx.execution_result:
            learning_processor = self._processors.get('learning')
            if learning_processor and ctx.event.type in learning_processor.event_types():
                await learning_processor.process(ctx.event)
        
        return ctx
    
    async def _process_parallel(self, event: Event) -> None:
        """Process event through all matching processors in parallel."""
        tasks = []
        
        for name, processor in self._processors.items():
            if event.type in processor.event_types():
                if self._backpressure_check(name, processor):
                    tasks.append(self._process_with_processor(name, processor, event))
        
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Processor error: {result}")
                elif result:
                    if isinstance(result, list):
                        for e in result:
                            await self._bus.publish(e)
                    else:
                        await self._bus.publish(result)
    
    def _backpressure_check(self, name: str, processor: BaseProcessor) -> bool:
        """Check if processor can handle more events."""
        if hasattr(processor, 'get_queue_depth'):
            queue_depth = processor.get_queue_depth()
            max_depth = 500
            
            if queue_depth > max_depth:
                logger.warning(f"Backpressure: {name} queue depth {queue_depth}")
                return queue_depth < max_depth * 2
            
        return True
    
    async def _process_with_processor(
        self,
        name: str,
        processor: BaseProcessor,
        event: Event
    ) -> Optional[Event | list[Event]]:
        """Process event with specific processor."""
        try:
            return await processor.process(event)
        except Exception as e:
            logger.error(f"Processor {name} error: {e}")
            self._metrics.record_error(name, type(e).__name__)
            return None
    
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


class PipelineContext:
    """Context passed through sequential pipeline steps."""
    
    def __init__(self, event: Event):
        self.event = event
        self.blocked = False
        self.block_reason: Optional[str] = None
        self.failed_step: Optional[str] = None
        self.error: Optional[Exception] = None
        
        self.alpha_result: Optional[Event] = None
        self.edge_result: Optional[Event] = None
        self.risk_result: Optional[Event] = None
        self.execution_result: Optional[Event | list[Event]] = None
        self.positivity_decision: Optional[Any] = None
    
    @property
    def is_complete(self) -> bool:
        return self.failed_step is None and not self.blocked


class PipelineStepFailed(Exception):
    """Raised when a pipeline step fails."""
    pass
