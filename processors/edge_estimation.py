"""
Edge Estimation Engine - Estimates expected edge from alpha signals.

Processes ALPHA_SIGNAL events and produces EDGE_ESTIMATED events
with detailed cost breakdown and confidence intervals.
"""

import logging
from typing import Optional
from dataclasses import asdict
from datetime import datetime

from core.event import Event, EventType, EdgeEstimationPayload
from core.processor import ProcessorConfig
from core.bus import EventBus
from core.state import DistributedState
from processors.base_processor import BaseProcessor

logger = logging.getLogger(__name__)


class EdgeEstimationEngine(BaseProcessor):
    """
    Estimates edge accounting for all costs.
    
    Cost components:
    - Fees (maker/taker in bps)
    - Slippage (based on order book depth)
    - Latency cost (time decay)
    - Risk penalty (VaR, drawdown)
    """
    
    DEFAULT_FEES_BPS = 4.0
    DEFAULT_SLIPPAGE_BPS = 2.0
    DEFAULT_LATENCY_COST_BPS = 1.0
    DEFAULT_RISK_PENALTY_BPS = 5.0
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        config: Optional[ProcessorConfig] = None,
        fees_bps: float = DEFAULT_FEES_BPS,
        latency_decay_ms: float = 100.0,
    ):
        super().__init__(bus, state, config)
        self.fees_bps = fees_bps
        self.latency_decay_ms = latency_decay_ms
        self._edge_history: dict[str, list[float]] = {}
        self._signal_history: dict[str, list[dict]] = {}
        
    def event_types(self) -> list[EventType]:
        return [EventType.ALPHA_SIGNAL]
    
    async def process_event(self, event: Event) -> Optional[Event]:
        if not await self._validate(event):
            return None
            
        symbol = event.symbol
        payload = event.payload
        
        direction = payload.get('direction', 0)
        strength = payload.get('strength', 0)
        confidence = payload.get('confidence', 0)
        expected_edge = payload.get('expected_edge', 0)
        
        if direction == 0 or confidence < 0.3:
            return None
        
        slippage_bps = self._estimate_slippage(symbol, strength)
        
        latency_cost = self._estimate_latency_cost(event)
        
        risk_penalty = await self._estimate_risk_penalty(symbol)
        
        total_cost_bps = self.fees_bps + slippage_bps + latency_cost + risk_penalty
        
        gross_edge_bps = expected_edge * 10000
        net_edge_bps = gross_edge_bps - total_cost_bps
        
        adjusted_confidence = confidence * self._get_confidence_multiplier(symbol)
        
        expected_return = (net_edge_bps / 10000) * strength * adjusted_confidence
        
        confidence_interval = self._compute_confidence_interval(
            net_edge_bps, adjusted_confidence
        )
        
        estimation_payload = EdgeEstimationPayload(
            expected_edge=expected_return,
            expected_return=expected_return,
            fees_bps=self.fees_bps,
            slippage_bps=slippage_bps,
            latency_cost_bps=latency_cost,
            risk_penalty_bps=risk_penalty,
            total_cost_bps=total_cost_bps,
            confidence=adjusted_confidence,
            confidence_interval=confidence_interval,
        )
        
        output_event = Event.create(
            event_type=EventType.EDGE_ESTIMATED,
            symbol=symbol,
            payload=asdict(estimation_payload),
            trace_id=event.trace_id,
            source=self.config.name if self.config else "edge_estimation_engine",
        )
        
        await self._update_edge_state(symbol, estimation_payload)
        
        return output_event
    
    def _estimate_slippage(self, symbol: str, strength: float) -> float:
        base_slippage = self.DEFAULT_SLIPPAGE_BPS
        return base_slippage * (1 + strength * 0.5)
    
    def _estimate_latency_cost(self, event: Event) -> float:
        latency_ms = event.payload.get('latency_ms', 0)
        decay_factor = latency_ms / self.latency_decay_ms if self.latency_decay_ms > 0 else 0
        return self.DEFAULT_LATENCY_COST_BPS * min(1.0, decay_factor)
    
    async def _estimate_risk_penalty(self, symbol: str) -> float:
        if not self.state:
            return self.DEFAULT_RISK_PENALTY_BPS
            
        risk_state = await self.state.get(f"risk:{symbol}")
        if risk_state and risk_state.value:
            drawdown = risk_state.value.get('drawdown_pct', 0)
            leverage = risk_state.value.get('leverage', 1)
            
            base_penalty = self.DEFAULT_RISK_PENALTY_BPS
            drawdown_multiplier = 1 + drawdown * 5
            leverage_multiplier = 1 + (leverage - 1) * 0.2
            
            return base_penalty * drawdown_multiplier * leverage_multiplier
            
        return self.DEFAULT_RISK_PENALTY_BPS
    
    def _get_confidence_multiplier(self, symbol: str) -> float:
        history = self._edge_history.get(symbol, [])
        if len(history) < 10:
            return 1.0
            
        recent = history[-10:]
        accuracy = sum(1 for e in recent if (e > 0) == (self._signal_history.get(symbol, [{}])[-1].get('direction', 0) > 0)) / len(recent)
        
        return min(1.2, max(0.8, accuracy))
    
    def _compute_confidence_interval(
        self,
        net_edge_bps: float,
        confidence: float
    ) -> tuple[float, float]:
        width = abs(net_edge_bps) * (1 - confidence) * 2
        
        lower = net_edge_bps - width / 2
        upper = net_edge_bps + width / 2
        
        return (lower / 10000, upper / 10000)
    
    async def _update_edge_state(
        self,
        symbol: str,
        estimation: EdgeEstimationPayload
    ) -> None:
        if not self.state:
            return
            
        self._edge_history.setdefault(symbol, []).append(estimation.expected_edge)
        if len(self._edge_history[symbol]) > 100:
            self._edge_history[symbol] = self._edge_history[symbol][-100:]
            
        state_key = f"edge:{symbol}"
        await self.state.set(
            key=state_key,
            value={
                'expected_edge': estimation.expected_edge,
                'expected_return': estimation.expected_return,
                'total_cost_bps': estimation.total_cost_bps,
                'confidence': estimation.confidence,
                'confidence_interval': estimation.confidence_interval,
                'updated_at': int(datetime.now().timestamp() * 1000),
            },
            trace_id=self.config.name if self.config else "edge_estimation_engine",
        )
