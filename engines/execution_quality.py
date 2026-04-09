"""
Execution Quality Engine - Monitors and reports on execution quality.

Tracks slippage, fill rates, latency, and execution degradation.
"""

import logging
from typing import Optional
from dataclasses import asdict, field
from datetime import datetime, timedelta
from collections import deque

from core.event import Event, EventType, ExecutionQualityPayload
from core.bus import EventBus
from core.state import DistributedState

logger = logging.getLogger(__name__)


class ExecutionQualityEngine:
    """
    Monitors execution quality metrics.
    
    Metrics tracked:
    - Slippage (bps)
    - Fill rate
    - Latency (p50, p99)
    - Degradation detection
    """
    
    DEGRADATION_THRESHOLD = 0.7
    SLIPPAGE_ALERT_BPS = 5.0
    LATENCY_ALERT_MS = 500
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        window_size: int = 100,
    ):
        self.bus = bus
        self.state = state
        self.window_size = window_size
        self._slippage_history: deque = deque(maxlen=window_size)
        self._latency_history: deque = deque(maxlen=window_size)
        self._fill_attempts: int = 0
        self._fill_successes: int = 0
        self._last_quality_score: float = 1.0
        self._degradation_count: int = 0
        
    async def record_fill(
        self,
        order_id: str,
        symbol: str,
        expected_price: float,
        actual_price: float,
        latency_ms: float,
        fill_time_ms: float
    ) -> None:
        """Record a fill for quality tracking."""
        slippage_bps = (
            abs(actual_price - expected_price) / expected_price * 10000
            if expected_price > 0 else 0
        )
        
        self._slippage_history.append(slippage_bps)
        self._latency_history.append(latency_ms + fill_time_ms)
        
        self._fill_attempts += 1
        self._fill_successes += 1
        
        if slippage_bps > self.SLIPPAGE_ALERT_BPS:
            await self._emit_quality_alert(
                symbol,
                'slippage',
                slippage_bps,
                f"High slippage: {slippage_bps:.1f} bps"
            )
        
        if latency_ms > self.LATENCY_ALERT_MS:
            await self._emit_quality_alert(
                symbol,
                'latency',
                latency_ms,
                f"High latency: {latency_ms:.0f}ms"
            )
    
    async def record_miss(
        self,
        order_id: str,
        symbol: str,
        reason: str
    ) -> None:
        """Record a missed fill."""
        self._fill_attempts += 1
        
        logger.info(f"Fill miss for {symbol}: {reason}")
    
    async def calculate_quality_score(self) -> ExecutionQualityPayload:
        """Calculate current execution quality metrics."""
        if not self._slippage_history:
            return ExecutionQualityPayload(
                quality_score=1.0,
                avg_slippage_bps=0,
                fill_rate=1.0,
                latency_p50_ms=0,
                latency_p99_ms=0,
                is_degrading=False,
            )
        
        avg_slippage = sum(self._slippage_history) / len(self._slippage_history)
        fill_rate = (
            self._fill_successes / self._fill_attempts
            if self._fill_attempts > 0 else 1.0
        )
        
        sorted_latencies = sorted(self._slippage_history)
        p50_idx = int(len(sorted_latencies) * 0.5)
        p99_idx = int(len(sorted_latencies) * 0.99)
        
        latency_p50 = sorted_latencies[p50_idx] if sorted_latencies else 0
        latency_p99 = sorted_latencies[p99_idx] if len(sorted_latencies) > p99_idx else latency_p50
        
        slippage_score = max(0, 1 - avg_slippage / 20)
        fill_score = fill_rate
        latency_score = max(0, 1 - latency_p99 / 1000)
        
        quality_score = (
            slippage_score * 0.4 +
            fill_score * 0.3 +
            latency_score * 0.3
        )
        
        is_degrading = self._detect_degradation(quality_score)
        
        payload = ExecutionQualityPayload(
            quality_score=quality_score,
            avg_slippage_bps=avg_slippage,
            fill_rate=fill_rate,
            latency_p50_ms=latency_p50,
            latency_p99_ms=latency_p99,
            is_degrading=is_degrading,
        )
        
        self._last_quality_score = quality_score
        
        if is_degrading:
            self._degradation_count += 1
        else:
            self._degradation_count = 0
        
        await self._update_quality_state(payload)
        
        return payload
    
    def _detect_degradation(self, current_score: float) -> bool:
        """Detect if execution quality is degrading."""
        if self._last_quality_score == 0:
            self._last_quality_score = current_score
            return False
        
        score_change = (current_score - self._last_quality_score) / self._last_quality_score
        
        if score_change < -0.2:
            return True
        
        if len(self._slippage_history) >= 20:
            recent_avg = sum(list(self._slippage_history)[-10:]) / 10
            older_avg = sum(list(self._slippage_history)[-20:-10]) / 10
            
            if recent_avg > older_avg * 1.5:
                return True
        
        return False
    
    async def _emit_quality_alert(
        self,
        symbol: str,
        alert_type: str,
        value: float,
        message: str
    ) -> None:
        """Emit execution quality alert."""
        logger.warning(
            f"Execution quality alert for {symbol}: {message}",
            extra={
                'symbol': symbol,
                'type': alert_type,
                'value': value,
            }
        )
        
        if self.bus:
            alert_event = Event.create(
                event_type=EventType.EXECUTION_QUALITY,
                symbol=symbol,
                payload={
                    'alert_type': alert_type,
                    'value': value,
                    'message': message,
                    'quality_score': self._last_quality_score,
                },
                source="execution_quality_engine",
            )
            await self.bus.publish(alert_event)
    
    async def _update_quality_state(
        self,
        payload: ExecutionQualityPayload
    ) -> None:
        if not self.state:
            return
            
        await self.state.set(
            key="execution_quality:global",
            value={
                'quality_score': payload.quality_score,
                'avg_slippage_bps': payload.avg_slippage_bps,
                'fill_rate': payload.fill_rate,
                'latency_p50_ms': payload.latency_p50_ms,
                'latency_p99_ms': payload.latency_p99_ms,
                'is_degrading': payload.is_degrading,
                'degradation_count': self._degradation_count,
                'updated_at': int(datetime.now().timestamp() * 1000),
            },
            trace_id="execution_quality_engine",
        )
    
    async def should_throttle(self) -> tuple[bool, str]:
        """Determine if we should throttle trading due to quality issues."""
        if self._degradation_count >= 3:
            return True, "multiple_degradation_events"
        
        if self._last_quality_score < self.DEGRADATION_THRESHOLD:
            return True, f"quality_score_low_{self._last_quality_score:.2f}"
        
        return False, "ok"
