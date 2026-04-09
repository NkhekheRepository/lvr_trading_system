"""
Positive Expectation Engine - Validates positive edge before trade.

Processes EDGE_ESTIMATED events and produces POSITIVE_EXPECTATION events
with trade decisions (ACCEPT/REJECT) and detailed reasoning.
"""

import logging
from typing import Optional
from dataclasses import asdict

from core.event import Event, EventType, TradeDecisionPayload
from core.processor import ProcessorConfig
from core.bus import EventBus
from core.state import DistributedState
from processors.base_processor import BaseProcessor

logger = logging.getLogger(__name__)


class PositiveExpectationEngine(BaseProcessor):
    """
    Validates that edge is positive after all costs.
    
    Decision criteria:
    - Net edge must be positive
    - Edge must exceed minimum threshold
    - Cost/edge ratio must be favorable
    - Realized edge must track expected
    """
    
    MIN_EDGE_THRESHOLD = 0.0001
    MAX_COST_EDGE_RATIO = 10.0
    MIN_PAYOFF_RATIO = 1.0
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        config: Optional[ProcessorConfig] = None,
        min_edge_threshold: float = MIN_EDGE_THRESHOLD,
        reality_gap_limit: float = 0.5,
    ):
        super().__init__(bus, state, config)
        self.min_edge_threshold = min_edge_threshold
        self.reality_gap_limit = reality_gap_limit
        
    def event_types(self) -> list[EventType]:
        return [EventType.EDGE_ESTIMATED, EventType.EDGE_TRUTH]
    
    async def process_event(self, event: Event) -> Optional[Event]:
        if not await self._validate(event):
            return None
            
        symbol = event.symbol
        
        if event.type == EventType.EDGE_ESTIMATED:
            return await self._process_edge_estimated(event, symbol)
        elif event.type == EventType.EDGE_TRUTH:
            return await self._process_edge_truth(event, symbol)
            
        return None
    
    async def _process_edge_estimated(
        self,
        event: Event,
        symbol: str
    ) -> Optional[Event]:
        payload = event.payload
        
        expected_edge = payload.get('expected_edge', 0)
        expected_return = payload.get('expected_return', 0)
        total_cost_bps = payload.get('total_cost_bps', 0)
        confidence = payload.get('confidence', 0)
        confidence_interval = payload.get('confidence_interval', (0, 0))
        
        if confidence < 0.3:
            decision = "REJECT"
            rejection_reason = "confidence_too_low"
        elif expected_edge < self.min_edge_threshold:
            decision = "REJECT"
            rejection_reason = "edge_below_threshold"
        elif expected_return <= 0:
            decision = "REJECT"
            rejection_reason = "negative_expected_return"
        else:
            payoff_ratio = self._compute_payoff_ratio(expected_return, total_cost_bps)
            cost_edge_ratio = total_cost_bps / (expected_edge * 10000) if expected_edge > 0 else float('inf')
            
            if payoff_ratio < self.MIN_PAYOFF_RATIO:
                decision = "REJECT"
                rejection_reason = "poor_payoff_ratio"
            elif cost_edge_ratio > self.MAX_COST_EDGE_RATIO:
                decision = "REJECT"
                rejection_reason = "cost_edge_ratio_too_high"
            else:
                reality_gap = await self._check_reality_gap(symbol)
                if reality_gap and reality_gap < (1 - self.reality_gap_limit):
                    decision = "REJECT"
                    rejection_reason = f"reality_gap_too_wide_{reality_gap:.2f}"
                else:
                    decision = "ACCEPT"
                    rejection_reason = None
        
        is_significant = confidence >= 0.6 and expected_edge >= self.min_edge_threshold * 2
        
        total_cost = total_cost_bps / 10000
        payoff_ratio = self._compute_payoff_ratio(expected_return, total_cost_bps)
        cost_edge_ratio = total_cost_bps / (expected_edge * 10000) if expected_edge > 0 else float('inf')
        
        decision_payload = TradeDecisionPayload(
            decision=decision,
            expected_edge=expected_edge,
            total_cost=total_cost,
            payoff_ratio=payoff_ratio,
            cost_edge_ratio=cost_edge_ratio,
            is_significant=is_significant,
            rejection_reason=rejection_reason,
        )
        
        output_event = Event.create(
            event_type=EventType.TRADE_DECISION,
            symbol=symbol,
            payload=asdict(decision_payload),
            trace_id=event.trace_id,
            source=self.config.name if self.config else "positive_expectation_engine",
        )
        
        await self._update_decision_state(symbol, decision_payload)
        
        return output_event
    
    async def _process_edge_truth(
        self,
        event: Event,
        symbol: str
    ) -> Optional[Event]:
        payload = event.payload
        edge_truth_score = payload.get('edge_truth_score', 1.0)
        
        await self._update_reality_gap_state(symbol, edge_truth_score)
        
        return None
    
    def _compute_payoff_ratio(
        self,
        expected_return: float,
        total_cost_bps: float
    ) -> float:
        if total_cost_bps == 0:
            return float('inf')
            
        expected_win = expected_return * 10000
        return expected_win / total_cost_bps if total_cost_bps > 0 else float('inf')
    
    async def _check_reality_gap(self, symbol: str) -> Optional[float]:
        if not self.state:
            return None
            
        truth_state = await self.state.get(f"edge_truth:{symbol}")
        if truth_state and truth_state.value:
            return truth_state.value.get('edge_truth_score')
        return None
    
    async def _update_reality_gap_state(
        self,
        symbol: str,
        edge_truth_score: float
    ) -> None:
        if not self.state:
            return
            
        state_key = f"reality_gap:{symbol}"
        current = await self.state.get(state_key)
        
        history = current.value.get('history', []) if current and current.value else []
        history.append(edge_truth_score)
        if len(history) > 20:
            history = history[-20:]
            
        avg_score = sum(history) / len(history) if history else 1.0
        
        await self.state.set(
            key=state_key,
            value={
                'current': edge_truth_score,
                'average': avg_score,
                'history': history,
            },
            trace_id=self.config.name if self.config else "positive_expectation_engine",
        )
    
    async def _update_decision_state(
        self,
        symbol: str,
        decision: TradeDecisionPayload
    ) -> None:
        if not self.state:
            return
            
        state_key = f"decision:{symbol}"
        await self.state.set(
            key=state_key,
            value={
                'decision': decision.decision,
                'expected_edge': decision.expected_edge,
                'is_significant': decision.is_significant,
                'rejection_reason': decision.rejection_reason,
            },
            trace_id=self.config.name if self.config else "positive_expectation_engine",
        )
