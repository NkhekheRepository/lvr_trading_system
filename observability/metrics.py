"""
Metrics - Prometheus metrics for the trading system.

Provides metrics collection and exposure for monitoring.
"""

import time
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime
from prometheus_client import Counter, Histogram, Gauge, Summary, CollectorRegistry, REGISTRY

from observability.logger import get_logger

logger = get_logger(__name__)


@dataclass
class MetricConfig:
    """Configuration for metrics collection."""
    namespace: str = "trading"
    subsystem: str = "system"
    buckets: tuple = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class TradingMetrics:
    """
    Prometheus metrics for trading system.
    
    Metrics:
    - Counters: Events, trades, errors
    - Histograms: Latency, prices, volumes
    - Gauges: Positions, balances, health
    - Summaries: Request durations
    """
    
    def __init__(
        self,
        config: Optional[MetricConfig] = None,
        registry: Optional[CollectorRegistry] = None,
    ):
        self.config = config or MetricConfig()
        self.registry = registry or REGISTRY
        
        self._setup_counters()
        self._setup_histograms()
        self._setup_gauges()
        self._setup_summaries()
    
    def _setup_counters(self) -> None:
        ns = self.config.namespace
        ss = self.config.subsystem
        
        self.events_total = Counter(
            'events_total',
            'Total events processed',
            ['event_type'],
            registry=self.registry,
        )
        
        self.trades_total = Counter(
            'trades_total',
            'Total trades executed',
            ['symbol', 'side', 'status'],
            registry=self.registry,
        )
        
        self.errors_total = Counter(
            'errors_total',
            'Total errors',
            ['component', 'error_type'],
            registry=self.registry,
        )
        
        self.signals_total = Counter(
            'signals_total',
            'Total signals generated',
            ['symbol', 'direction'],
            registry=self.registry,
        )
        
        self.rejections_total = Counter(
            'rejections_total',
            'Total trade rejections',
            ['symbol', 'reason'],
            registry=self.registry,
        )
    
    def _setup_histograms(self) -> None:
        ns = self.config.namespace
        ss = self.config.subsystem
        buckets = self.config.buckets
        
        self.processing_latency = Histogram(
            'processing_latency_seconds',
            'Event processing latency',
            ['processor', 'event_type'],
            buckets=buckets,
            registry=self.registry,
        )
        
        self.trade_latency = Histogram(
            'trade_latency_seconds',
            'Trade execution latency',
            ['symbol', 'execution_mode'],
            buckets=buckets,
            registry=self.registry,
        )
        
        self.order_latency = Histogram(
            'order_latency_seconds',
            'Order lifecycle latency',
            ['symbol', 'stage'],
            buckets=buckets,
            registry=self.registry,
        )
        
        self.slippage_bps = Histogram(
            'slippage_bps',
            'Slippage in basis points',
            ['symbol', 'side'],
            buckets=(0.5, 1, 2, 5, 10, 20, 50),
            registry=self.registry,
        )
        
        self.edge_estimate = Histogram(
            'edge_estimate',
            'Estimated edge',
            ['symbol'],
            buckets=(-0.01, -0.005, -0.001, 0, 0.001, 0.005, 0.01),
            registry=self.registry,
        )
    
    def _setup_gauges(self) -> None:
        ns = self.config.namespace
        ss = self.config.subsystem
        
        self.portfolio_value = Gauge(
            'portfolio_value_dollars',
            'Current portfolio value',
            registry=self.registry,
        )
        
        self.position_size = Gauge(
            'position_size',
            'Current position size',
            ['symbol'],
            registry=self.registry,
        )
        
        self.leverage = Gauge(
            'leverage_ratio',
            'Current leverage ratio',
            registry=self.registry,
        )
        
        self.drawdown_pct = Gauge(
            'drawdown_pct',
            'Current drawdown percentage',
            registry=self.registry,
        )
        
        self.daily_pnl = Gauge(
            'daily_pnl_dollars',
            'Daily profit/loss',
            registry=self.registry,
        )
        
        self.queue_depth = Gauge(
            'queue_depth',
            'Processor queue depth',
            ['processor'],
            registry=self.registry,
        )
        
        self.health_status = Gauge(
            'health_status',
            'Component health (1=healthy, 0=unhealthy)',
            ['component'],
            registry=self.registry,
        )
    
    def _setup_summaries(self) -> None:
        ns = self.config.namespace
        ss = self.config.subsystem
        
        self.event_processing_time = Summary(
            'event_processing_seconds',
            'Event processing time',
            ['processor'],
            registry=self.registry,
        )
    
    def record_event(self, event_type: str) -> None:
        """Record an event."""
        self.events_total.labels(event_type=event_type).inc()
    
    def record_trade(
        self,
        symbol: str,
        side: str,
        status: str = 'filled'
    ) -> None:
        """Record a trade."""
        self.trades_total.labels(
            symbol=symbol,
            side=side,
            status=status
        ).inc()
    
    def record_error(
        self,
        component: str,
        error_type: str
    ) -> None:
        """Record an error."""
        self.errors_total.labels(
            component=component,
            error_type=error_type
        ).inc()
    
    def record_signal(
        self,
        symbol: str,
        direction: int
    ) -> None:
        """Record a signal."""
        direction_str = 'long' if direction > 0 else ('short' if direction < 0 else 'neutral')
        self.signals_total.labels(
            symbol=symbol,
            direction=direction_str
        ).inc()
    
    def record_rejection(
        self,
        symbol: str,
        reason: str
    ) -> None:
        """Record a trade rejection."""
        self.rejections_total.labels(
            symbol=symbol,
            reason=reason
        ).inc()
    
    def observe_latency(
        self,
        processor: str,
        event_type: str,
        duration_seconds: float
    ) -> None:
        """Observe processing latency."""
        self.processing_latency.labels(
            processor=processor,
            event_type=event_type
        ).observe(duration_seconds)
    
    def observe_trade_latency(
        self,
        symbol: str,
        execution_mode: str,
        duration_seconds: float
    ) -> None:
        """Observe trade latency."""
        self.trade_latency.labels(
            symbol=symbol,
            execution_mode=execution_mode
        ).observe(duration_seconds)
    
    def observe_slippage(
        self,
        symbol: str,
        side: str,
        slippage: float
    ) -> None:
        """Observe slippage in bps."""
        self.slippage_bps.labels(
            symbol=symbol,
            side=side
        ).observe(slippage)
    
    def set_portfolio_value(self, value: float) -> None:
        """Set current portfolio value."""
        self.portfolio_value.set(value)
    
    def set_position_size(self, symbol: str, size: float) -> None:
        """Set current position size."""
        self.position_size.labels(symbol=symbol).set(size)
    
    def set_leverage(self, leverage: float) -> None:
        """Set current leverage."""
        self.leverage.set(leverage)
    
    def set_drawdown(self, drawdown_pct: float) -> None:
        """Set current drawdown."""
        self.drawdown_pct.set(drawdown_pct)
    
    def set_daily_pnl(self, pnl: float) -> None:
        """Set daily P&L."""
        self.daily_pnl.set(pnl)
    
    def set_queue_depth(self, processor: str, depth: int) -> None:
        """Set processor queue depth."""
        self.queue_depth.labels(processor=processor).set(depth)
    
    def set_health_status(self, component: str, healthy: bool) -> None:
        """Set component health status."""
        self.health_status.labels(component=component).set(1 if healthy else 0)


class LatencyTracker:
    """Context manager for tracking latency."""
    
    def __init__(
        self,
        metrics: TradingMetrics,
        processor: str,
        event_type: str,
    ):
        self.metrics = metrics
        self.processor = processor
        self.event_type = event_type
        self.start_time: Optional[float] = None
    
    def __enter__(self) -> 'LatencyTracker':
        self.start_time = time.perf_counter()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.start_time is not None:
            duration = time.perf_counter() - self.start_time
            self.metrics.observe_latency(
                self.processor,
                self.event_type,
                duration
            )
