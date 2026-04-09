"""
System monitoring and metrics collection.
"""

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

from app.schemas import MetricsSnapshot, ProtectionLevel

logger = logging.getLogger(__name__)

EPS = 1e-10


@dataclass
class MetricWindow:
    """Rolling window for metrics."""
    values: deque = None
    max_size: int = 100

    def __post_init__(self):
        self.values = deque(maxlen=self.max_size)

    def add(self, value: float) -> None:
        self.values.append(value)

    def mean(self) -> float:
        if not self.values:
            return 0.0
        return np.mean(self.values)

    def std(self) -> float:
        if len(self.values) < 2:
            return 0.0
        return np.std(self.values)

    def last(self) -> Optional[float]:
        return self.values[-1] if self.values else None


class MetricsCollector:
    """
    Collects and tracks system metrics.
    """

    def __init__(self, window_size: int = 100):
        self.window_size = window_size

        self.fill_rate = MetricWindow(max_size=window_size)
        self.slippage = MetricWindow(max_size=window_size)
        self.edge_error = MetricWindow(max_size=window_size)
        self.latency = MetricWindow(max_size=window_size)

        self._orders_submitted = 0
        self._orders_filled = 0
        self._orders_rejected = 0
        self._consecutive_failures = 0

        self._last_tick_time = 0
        self._start_time = time.time()

    def record_fill(self, filled_qty: float, requested_qty: float) -> None:
        """Record fill event."""
        self._orders_submitted += 1
        self._orders_filled += 1

        if requested_qty > EPS:
            rate = filled_qty / requested_qty
        else:
            rate = 1.0

        self.fill_rate.add(rate)

    def record_rejection(self) -> None:
        """Record order rejection."""
        self._orders_submitted += 1
        self._orders_rejected += 1
        self._consecutive_failures += 1

    def record_success(self) -> None:
        """Record successful cycle."""
        self._consecutive_failures = 0

    def record_slippage(self, actual_slippage: float, expected_slippage: float) -> None:
        """Record slippage."""
        slippage_error = actual_slippage - expected_slippage
        self.slippage.add(slippage_error)

    def record_edge(
        self,
        expected_edge: float,
        realized_edge: float
    ) -> None:
        """Record edge error."""
        error = realized_edge - expected_edge
        self.edge_error.add(error)

    def record_latency(self, latency_ms: float) -> None:
        """Record order latency."""
        self.latency.add(latency_ms)

    def update_data_freshness(self, tick_timestamp: int) -> None:
        """Update last tick time."""
        self._last_tick_time = tick_timestamp

    def collect(self) -> MetricsSnapshot:
        """Collect current metrics snapshot."""
        now = int(time.time() * 1000)

        last_tick_age = (now - self._last_tick_time) / 1000.0 if self._last_tick_time > 0 else 999

        total_orders = self._orders_submitted
        if total_orders > 0:
            fill_rate = self._orders_filled / total_orders
            rejection_rate = self._orders_rejected / total_orders
        else:
            fill_rate = 1.0
            rejection_rate = 0.0

        return MetricsSnapshot(
            timestamp=now,
            fill_rate=fill_rate,
            avg_slippage=self.slippage.mean(),
            slippage_error=self.slippage.mean(),
            rejection_rate=rejection_rate,
            edge_error=self.edge_error.mean(),
            signal_accuracy=self._calculate_signal_accuracy(),
            drawdown=0.0,
            daily_pnl=0.0,
            order_latency_ms=self.latency.mean(),
            data_latency_ms=last_tick_age * 1000,
            last_tick_age_sec=last_tick_age,
            data_fresh=last_tick_age < 10,
            protection_level=ProtectionLevel.NONE,
            consecutive_failures=self._consecutive_failures
        )

    def _calculate_signal_accuracy(self) -> float:
        """Calculate signal accuracy."""
        if len(self.edge_error.values) < 10:
            return 0.5

        correct_signals = sum(
            1 for e in list(self.edge_error.values)[-20:]
            if e > 0
        )
        return correct_signals / min(len(self.edge_error.values), 20)

    def get_summary(self) -> dict:
        """Get metrics summary."""
        return {
            "fill_rate": self.fill_rate.mean(),
            "avg_slippage": self.slippage.mean(),
            "slippage_std": self.slippage.std(),
            "edge_error": self.edge_error.mean(),
            "avg_latency_ms": self.latency.mean(),
            "total_orders": self._orders_submitted,
            "total_fills": self._orders_filled,
            "rejections": self._orders_rejected,
            "consecutive_failures": self._consecutive_failures
        }

    def reset(self) -> None:
        """Reset all metrics."""
        self.fill_rate = MetricWindow(max_size=self.window_size)
        self.slippage = MetricWindow(max_size=self.window_size)
        self.edge_error = MetricWindow(max_size=self.window_size)
        self.latency = MetricWindow(max_size=self.window_size)

        self._orders_submitted = 0
        self._orders_filled = 0
        self._orders_rejected = 0
        self._consecutive_failures = 0
