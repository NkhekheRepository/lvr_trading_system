"""
Positivity Engine - Ensures only positive edge trades are executed.

Monitors edge estimates and blocks trades when edge turns negative.
"""

import logging
from typing import Optional
from dataclasses import asdict
from datetime import datetime

from core.event import Event, EventType
from core.bus import EventBus
from core.state import DistributedState

logger = logging.getLogger(__name__)


class PositivityEngine:
    """
    Enforces positive edge requirement before trade execution.
    
    Conditions for trade:
    - Expected edge must be positive
    - Confidence must be above threshold
    - Reality gap adjustment must not invalidate
    """
    
    MIN_EDGE_THRESHOLD = 0.0001
    MIN_CONFIDENCE = 0.5
    MIN_TRADE_PROBABILITY = 0.55
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        min_edge: float = MIN_EDGE_THRESHOLD,
        min_confidence: float = MIN_CONFIDENCE,
    ):
        self.bus = bus
        self.state = state
        self.min_edge = min_edge
        self.min_confidence = min_confidence
        self._positivity_history: dict[str, list[bool]] = {}
        
    async def check_trade_positive(
        self,
        symbol: str,
        edge_estimate: dict,
        reality_gap: Optional[dict] = None
    ) -> tuple[bool, str]:
        """
        Check if trade meets positivity requirements.
        
        Returns:
            (approved, reason)
        """
        expected_edge = edge_estimate.get('expected_edge', 0)
        confidence = edge_estimate.get('confidence', 0)
        
        if expected_edge <= 0:
            self._record_result(symbol, False)
            return False, "negative_or_zero_edge"
        
        if expected_edge < self.min_edge:
            self._record_result(symbol, False)
            return False, f"edge_below_minimum_{expected_edge:.6f}"
        
        if confidence < self.min_confidence:
            self._record_result(symbol, False)
            return False, f"confidence_too_low_{confidence:.2f}"
        
        if reality_gap:
            adjustment = reality_gap.get('adjustment_factor', 1.0)
            adjusted_edge = expected_edge * adjustment
            
            if adjusted_edge <= 0:
                self._record_result(symbol, False)
                return False, f"adjusted_edge_negative_{adjusted_edge:.6f}"
        
        trade_prob = self._estimate_trade_probability(
            expected_edge, confidence, reality_gap
        )
        
        if trade_prob < self.MIN_TRADE_PROBABILITY:
            self._record_result(symbol, False)
            return False, f"trade_probability_too_low_{trade_prob:.2f}"
        
        self._record_result(symbol, True)
        return True, "approved"
    
    def _estimate_trade_probability(
        self,
        edge: float,
        confidence: float,
        reality_gap: Optional[dict]
    ) -> float:
        base_prob = 0.5 + edge * 1000
        
        confidence_boost = confidence * 0.3
        
        gap_adjustment = 1.0
        if reality_gap:
            gap_adjustment = reality_gap.get('adjustment_factor', 1.0)
        
        probability = (base_prob + confidence_boost) * gap_adjustment
        
        return max(0.1, min(0.95, probability))
    
    def _record_result(self, symbol: str, positive: bool) -> None:
        history = self._positivity_history.setdefault(symbol, [])
        history.append(positive)
        if len(history) > 100:
            history = history[-100:]
        self._positivity_history[symbol] = history
    
    async def get_positivity_stats(self, symbol: str) -> dict:
        """Get positivity statistics for a symbol."""
        history = self._positivity_history.get(symbol, [])
        
        if not history:
            return {'count': 0, 'positive_rate': 0.0}
        
        positive_count = sum(1 for x in history if x)
        
        return {
            'count': len(history),
            'positive_count': positive_count,
            'positive_rate': positive_count / len(history),
            'recent_rate': sum(1 for x in history[-20:] if x) / min(20, len(history)),
        }
    
    async def adjust_edge_threshold(
        self,
        symbol: str,
        current_stats: dict
    ) -> float:
        """
        Dynamically adjust edge threshold based on recent performance.
        """
        recent_rate = current_stats.get('recent_rate', 0.5)
        
        if recent_rate > 0.7:
            return self.min_edge * 0.8
        elif recent_rate < 0.4:
            return self.min_edge * 1.5
        else:
            return self.min_edge
    
    async def emit_positivity_alert(
        self,
        symbol: str,
        reason: str,
        edge_estimate: dict
    ) -> None:
        """Emit alert when positivity check fails."""
        logger.warning(
            f"Positivity check failed for {symbol}: {reason}",
            extra={
                'symbol': symbol,
                'reason': reason,
                'edge': edge_estimate.get('expected_edge', 0),
                'confidence': edge_estimate.get('confidence', 0),
            }
        )
        
        if self.bus:
            alert_event = Event.create(
                event_type=EventType.STRATEGY_TERMINATION,
                symbol=symbol,
                payload={
                    'reason': f"positivity_check_failed_{reason}",
                    'edge': edge_estimate.get('expected_edge', 0),
                    'confidence': edge_estimate.get('confidence', 0),
                    'severity': 'WARNING',
                },
                source="positivity_engine",
            )
            await self.bus.publish(alert_event)
