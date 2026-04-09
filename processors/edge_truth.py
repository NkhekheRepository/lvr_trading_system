"""
Edge Truth Engine - Measures realized edge vs expected.

Processes ORDER_FILLED events and produces EDGE_TRUTH events
comparing realized PnL to expected edge.
"""

import logging
from typing import Optional
from dataclasses import asdict
from datetime import datetime

from core.event import Event, EventType, EdgeTruthPayload
from core.processor import ProcessorConfig
from core.bus import EventBus
from core.state import DistributedState
from processors.base_processor import BaseProcessor

logger = logging.getLogger(__name__)


class EdgeTruthEngine(BaseProcessor):
    """
    Tracks realized edge vs expectations.
    
    Metrics computed:
    - Edge truth score (realized / expected)
    - Win rate, avg win, avg loss
    - Statistical significance
    - Confidence adjustment
    """
    
    MIN_TRADES_FOR_SIGNIFICANCE = 20
    SIGNIFICANCE_P_VALUE = 0.05
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        config: Optional[ProcessorConfig] = None,
        window_size: int = 100,
    ):
        super().__init__(bus, state, config)
        self.window_size = window_size
        self._trade_history: dict[str, list[dict]] = {}
        self._expected_edges: dict[str, list[float]] = {}
        self._realized_edges: dict[str, list[float]] = {}
        
    def event_types(self) -> list[EventType]:
        return [EventType.ORDER_FILLED, EventType.POSITION_RECONCILED]
    
    async def process_event(self, event: Event) -> Optional[Event]:
        if not await self._validate(event):
            return None
            
        symbol = event.symbol
        payload = event.payload
        
        if event.type == EventType.ORDER_FILLED:
            return await self._process_filled(event, symbol, payload)
        elif event.type == EventType.POSITION_RECONCILED:
            return await self._process_reconciled(event, symbol, payload)
            
        return None
    
    async def _process_filled(
        self,
        event: Event,
        symbol: str,
        payload: dict
    ) -> Optional[Event]:
        order_id = payload.get('order_id', '')
        side = payload.get('side', '')
        filled_qty = payload.get('filled_quantity', 0)
        fill_price = payload.get('avg_fill_price', 0)
        slippage_bps = payload.get('slippage_bps', 0)
        
        if filled_qty <= 0 or fill_price <= 0:
            return None
            
        entry = {
            'event_id': event.event_id,
            'order_id': order_id,
            'side': side,
            'quantity': filled_qty,
            'entry_price': fill_price,
            'slippage_bps': slippage_bps,
            'timestamp': event.timestamp,
            'trace_id': event.trace_id,
        }
        
        self._trade_history.setdefault(symbol, []).append(entry)
        if len(self._trade_history[symbol]) > self.window_size:
            self._trade_history[symbol] = self._trade_history[symbol][-self.window_size:]
        
        expected_edge = await self._get_expected_edge(symbol)
        if expected_edge:
            self._expected_edges.setdefault(symbol, []).append(expected_edge)
            if len(self._expected_edges[symbol]) > self.window_size:
                self._expected_edges[symbol] = self._expected_edges[symbol][-self.window_size:]
        
        return None
    
    async def _process_reconciled(
        self,
        event: Event,
        symbol: str,
        payload: dict
    ) -> Optional[Event]:
        realized_pnl = payload.get('realized_pnl', 0)
        trade_count = len(self._trade_history.get(symbol, []))
        
        if trade_count < 5:
            return None
            
        trades = self._trade_history.get(symbol, [])
        expected_list = self._expected_edges.get(symbol, [])
        
        wins = []
        losses = []
        realized_edges = []
        
        for i, trade in enumerate(trades):
            if i < len(expected_list):
                expected = expected_list[i]
            else:
                expected = 0
                
            if trade['side'] == 'BUY':
                pnl = -realized_pnl / trade['quantity'] if trade['quantity'] > 0 else 0
            else:
                pnl = realized_pnl / trade['quantity'] if trade['quantity'] > 0 else 0
                
            realized_edges.append(pnl)
            
            if pnl > 0:
                wins.append(pnl)
            elif pnl < 0:
                losses.append(pnl)
        
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 1
        
        realized_edge = sum(realized_edges) / len(realized_edges) if realized_edges else 0
        expected_edge = sum(expected_list) / len(expected_list) if expected_list else 0
        
        edge_truth_score = realized_edge / expected_edge if expected_edge != 0 else 1.0
        
        win_rate = len(wins) / len(realized_edges) if realized_edges else 0
        
        is_significant = self._check_significance(
            realized_edges,
            len(realized_edges)
        )
        
        confidence = self._compute_confidence(
            edge_truth_score,
            len(realized_edges),
            is_significant
        )
        
        truth_payload = EdgeTruthPayload(
            edge_truth_score=edge_truth_score,
            expected_edge=expected_edge,
            realized_edge=realized_edge,
            trade_count=trade_count,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            is_significant=is_significant,
            confidence=confidence,
        )
        
        output_event = Event.create(
            event_type=EventType.EDGE_TRUTH,
            symbol=symbol,
            payload=asdict(truth_payload),
            trace_id=event.trace_id,
            source=self.config.name if self.config else "edge_truth_engine",
        )
        
        await self._update_truth_state(symbol, truth_payload)
        
        return output_event
    
    async def _get_expected_edge(self, symbol: str) -> Optional[float]:
        if not self.state:
            return None
            
        edge_state = await self.state.get(f"edge:{symbol}")
        if edge_state and edge_state.value:
            return edge_state.value.get('expected_edge')
        return None
    
    def _check_significance(
        self,
        edges: list[float],
        n: int
    ) -> bool:
        if n < self.MIN_TRADES_FOR_SIGNIFICANCE:
            return False
            
        mean = sum(edges) / len(edges)
        variance = sum((e - mean) ** 2 for e in edges) / len(edges)
        std = variance ** 0.5
        
        if std == 0:
            return False
            
        t_stat = mean / (std / (len(edges) ** 0.5))
        
        return abs(t_stat) > 1.96
    
    def _compute_confidence(
        self,
        truth_score: float,
        trade_count: int,
        is_significant: bool
    ) -> float:
        count_factor = min(1.0, trade_count / self.MIN_TRADES_FOR_SIGNIFICANCE)
        
        score_factor = min(1.0, abs(truth_score - 1.0) + 0.5)
        
        sig_bonus = 0.2 if is_significant else 0.0
        
        return min(1.0, count_factor * score_factor * 0.8 + sig_bonus)
    
    async def _update_truth_state(
        self,
        symbol: str,
        truth: EdgeTruthPayload
    ) -> None:
        if not self.state:
            return
            
        state_key = f"edge_truth:{symbol}"
        await self.state.set(
            key=state_key,
            value={
                'edge_truth_score': truth.edge_truth_score,
                'realized_edge': truth.realized_edge,
                'win_rate': truth.win_rate,
                'trade_count': truth.trade_count,
                'is_significant': truth.is_significant,
                'updated_at': int(datetime.now().timestamp() * 1000),
            },
            trace_id=self.config.name if self.config else "edge_truth_engine",
        )
