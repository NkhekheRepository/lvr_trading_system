"""
Reality Gap Monitor - Tracks divergence between expected and realized performance.

Processes EDGE_TRUTH events and produces REALITY_GAP events
with gap metrics and adjustment factors.
"""

import logging
from typing import Optional
from dataclasses import asdict
from datetime import datetime

from core.event import Event, EventType, RealityGapPayload
from core.processor import ProcessorConfig
from core.bus import EventBus
from core.state import DistributedState
from processors.base_processor import BaseProcessor

logger = logging.getLogger(__name__)


class RealityGapMonitor(BaseProcessor):
    """
    Monitors reality gap between expected and realized edge.
    
    Gap analysis:
    - Compares expected PnL vs actual PnL
    - Tracks gap trend (widening/narrowing)
    - Computes adjustment factors for future estimates
    - Triggers alerts when gap exceeds thresholds
    """
    
    GAP_ALERT_THRESHOLD = 0.3
    GAP_CRITICAL_THRESHOLD = 0.5
    MIN_TRADES_FOR_ASSESSMENT = 10
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        config: Optional[ProcessorConfig] = None,
        window_size: int = 50,
    ):
        super().__init__(bus, state, config)
        self.window_size = window_size
        self._gap_history: dict[str, list[float]] = {}
        self._expected_history: dict[str, list[float]] = {}
        self._realized_history: dict[str, list[float]] = {}
        
    def event_types(self) -> list[EventType]:
        return [EventType.EDGE_TRUTH, EventType.POSITION_RECONCILED]
    
    async def process_event(self, event: Event) -> Optional[Event]:
        if not await self._validate(event):
            return None
            
        symbol = event.symbol
        payload = event.payload
        
        if event.type == EventType.EDGE_TRUTH:
            return await self._process_truth(event, symbol, payload)
        elif event.type == EventType.POSITION_RECONCILED:
            return await self._process_reconciliation(event, symbol, payload)
            
        return None
    
    async def _process_truth(
        self,
        event: Event,
        symbol: str,
        payload: dict
    ) -> Optional[Event]:
        expected_edge = payload.get('expected_edge', 0)
        realized_edge = payload.get('realized_edge', 0)
        
        self._expected_history.setdefault(symbol, []).append(expected_edge)
        self._realized_history.setdefault(symbol, []).append(realized_edge)
        
        for hist in [self._expected_history, self._realized_history]:
            if len(hist[symbol]) > self.window_size:
                hist[symbol] = hist[symbol][-self.window_size:]
        
        return None
    
    async def _process_reconciliation(
        self,
        event: Event,
        symbol: str,
        payload: dict
    ) -> Optional[Event]:
        realized_pnl = payload.get('realized_pnl', 0)
        
        expected_list = self._expected_history.get(symbol, [])
        realized_list = self._realized_history.get(symbol, [])
        
        trade_count = len(expected_list)
        
        if trade_count < self.MIN_TRADES_FOR_ASSESSMENT:
            return None
            
        avg_expected = sum(expected_list) / len(expected_list)
        avg_realized = sum(realized_list) / len(realized_list) if realized_list else 0
        
        expected_pnl = avg_expected * trade_count
        actual_pnl = realized_pnl
        
        if expected_pnl == 0:
            gap_pct = 0.0
        else:
            gap_pct = (expected_pnl - actual_pnl) / abs(expected_pnl)
        
        gap_history = self._gap_history.setdefault(symbol, [])
        gap_history.append(gap_pct)
        if len(gap_history) > self.window_size:
            gap_history = gap_history[-self.window_size:]
        
        is_widening = self._check_if_widening(gap_history)
        
        confidence = self._compute_confidence(trade_count)
        
        adjustment_factor = self._compute_adjustment_factor(
            gap_pct, trade_count, is_widening
        )
        
        reality_payload = RealityGapPayload(
            gap_pct=gap_pct,
            expected_pnl=expected_pnl,
            actual_pnl=actual_pnl,
            is_widening=is_widening,
            confidence=confidence,
            adjustment_factor=adjustment_factor,
        )
        
        output_event = Event.create(
            event_type=EventType.REALITY_GAP,
            symbol=symbol,
            payload=asdict(reality_payload),
            trace_id=event.trace_id,
            source=self.config.name if self.config else "reality_gap_monitor",
        )
        
        await self._update_gap_state(symbol, reality_payload)
        
        if abs(gap_pct) > self.GAP_CRITICAL_THRESHOLD:
            await self._emit_alert(symbol, gap_pct, 'CRITICAL')
        elif abs(gap_pct) > self.GAP_ALERT_THRESHOLD:
            await self._emit_alert(symbol, gap_pct, 'WARNING')
        
        return output_event
    
    def _check_if_widening(self, gap_history: list[float]) -> bool:
        if len(gap_history) < 5:
            return False
            
        recent_avg = sum(gap_history[-3:]) / 3
        older_avg = sum(gap_history[-5:-2]) / 3
        
        return abs(recent_avg) > abs(older_avg) * 1.2
    
    def _compute_confidence(self, trade_count: int) -> float:
        return min(1.0, trade_count / self.window_size)
    
    def _compute_adjustment_factor(
        self,
        gap_pct: float,
        trade_count: int,
        is_widening: bool
    ) -> float:
        base_adjustment = 1.0 - gap_pct
        
        count_factor = min(1.0, trade_count / 30)
        
        widening_penalty = 0.9 if is_widening else 1.0
        
        if abs(gap_pct) > self.GAP_CRITICAL_THRESHOLD:
            return 0.5 * widening_penalty
        elif abs(gap_pct) > self.GAP_ALERT_THRESHOLD:
            return base_adjustment * 0.8 * widening_penalty
        else:
            return base_adjustment * count_factor
    
    async def _update_gap_state(
        self,
        symbol: str,
        gap: RealityGapPayload
    ) -> None:
        if not self.state:
            return
            
        state_key = f"reality_gap:{symbol}"
        current = await self.state.get(state_key)
        
        history = current.value.get('history', []) if current and current.value else []
        history.append(gap.gap_pct)
        if len(history) > self.window_size:
            history = history[-self.window_size:]
        
        await self.state.set(
            key=state_key,
            value={
                'current_gap_pct': gap.gap_pct,
                'expected_pnl': gap.expected_pnl,
                'actual_pnl': gap.actual_pnl,
                'is_widening': gap.is_widening,
                'adjustment_factor': gap.adjustment_factor,
                'history': history,
                'updated_at': int(datetime.now().timestamp() * 1000),
            },
            trace_id=self.config.name if self.config else "reality_gap_monitor",
        )
    
    async def _emit_alert(
        self,
        symbol: str,
        gap_pct: float,
        severity: str
    ) -> None:
        logger.warning(
            f"Reality gap {severity} for {symbol}: {gap_pct:.2%}",
            extra={
                'symbol': symbol,
                'gap_pct': gap_pct,
                'severity': severity,
            }
        )
        
        if self.bus:
            alert_event = Event.create(
                event_type=EventType.REALITY_GAP_ALERT,
                symbol=symbol,
                payload={
                    'gap_pct': gap_pct,
                    'severity': severity,
                    'threshold': self.GAP_ALERT_THRESHOLD if severity == 'WARNING' else self.GAP_CRITICAL_THRESHOLD,
                },
                source=self.config.name if self.config else "reality_gap_monitor",
            )
            await self.bus.publish(alert_event)
