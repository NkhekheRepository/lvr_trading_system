"""
Execution Processor - Handles order execution.

Processes RISK_EVALUATED events and produces order events
(SUBMITTED, PARTIAL, FILLED, CANCELED, REJECTED).
"""

import logging
from typing import Optional
from dataclasses import asdict
from datetime import datetime
import uuid

from core.event import Event, EventType, OrderPayload
from core.processor import ProcessorConfig
from core.bus import EventBus
from core.state import DistributedState
from processors.base_processor import BaseProcessor

logger = logging.getLogger(__name__)


class ExecutionProcessor(BaseProcessor):
    """
    Handles order execution and fill simulation.
    
    Execution modes:
    - SIM: Simulated fills with realistic slippage
    - PAPER: Paper trading with real-time simulation
    - LIVE: Real order placement via vnpy
    
    Produces order events for lifecycle tracking.
    """
    
    FILL_PROBABILITY = 0.95
    PARTIAL_FILL_PROBABILITY = 0.1
    DEFAULT_SLIPPAGE_BPS = 2.0
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        config: Optional[ProcessorConfig] = None,
        execution_mode: str = "SIM",
        slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    ):
        super().__init__(bus, state, config)
        self.execution_mode = execution_mode
        self.slippage_bps = slippage_bps
        self._pending_orders: dict[str, dict] = {}
        self._fill_latencies: list[float] = []
        
    def event_types(self) -> list[EventType]:
        return [EventType.RISK_EVALUATED]
    
    async def process_event(self, event: Event) -> Optional[Event | list[Event]]:
        if not await self._validate(event):
            return None
            
        symbol = event.symbol
        payload = event.payload
        
        if not payload.get('approved', False):
            return None
        
        order_id = await self._generate_order_id(symbol)
        
        regime = await self._get_regime(symbol)
        position_size = payload.get('position_size_pct', 0.1)
        quantity = await self._calculate_order_quantity(
            symbol, position_size, regime
        )
        
        price = await self._get_current_price(symbol)
        if not price:
            return None
        
        order = {
            'order_id': order_id,
            'symbol': symbol,
            'quantity': quantity,
            'price': price,
            'side': self._determine_side(symbol),
            'status': 'PENDING',
            'filled_quantity': 0,
            'avg_fill_price': 0,
            'slippage_bps': 0,
            'latency_ms': 0,
            'created_at': event.timestamp,
            'trace_id': event.trace_id,
        }
        
        self._pending_orders[order_id] = order
        
        submit_event = await self._create_order_event(
            EventType.ORDER_SUBMITTED,
            order,
            event.trace_id
        )
        
        if self.execution_mode == "SIM":
            return await self._simulate_execution(submit_event, order)
        else:
            return submit_event
    
    async def _generate_order_id(self, symbol: str) -> str:
        return f"{symbol}_{int(datetime.now().timestamp() * 1000)}_{uuid.uuid4().hex[:8]}"
    
    async def _get_regime(self, symbol: str) -> dict:
        if not self.state:
            return {'max_position_scale': 1.0, 'risk_score': 0}
            
        regime = await self.state.get(f"regime:{symbol}")
        return regime.value if regime else {'max_position_scale': 1.0, 'risk_score': 0}
    
    async def _calculate_order_quantity(
        self,
        symbol: str,
        position_size_pct: float,
        regime: dict
    ) -> float:
        portfolio = await self._get_portfolio_state()
        total_value = portfolio.get('total_value', 100000)
        
        position_scale = regime.get('max_position_scale', 1.0)
        
        target_value = total_value * position_size_pct * position_scale
        
        price = await self._get_current_price(symbol)
        if not price:
            return 0
            
        return target_value / price
    
    async def _get_portfolio_state(self) -> dict:
        if not self.state:
            return {'total_value': 100000}
            
        portfolio = await self.state.get("portfolio:global")
        return portfolio.value if portfolio else {'total_value': 100000}
    
    async def _get_current_price(self, symbol: str) -> Optional[float]:
        if not self.state:
            return None
            
        market = await self.state.get(f"market:{symbol}")
        if market and market.value:
            return market.value.get('price')
            
        features = await self.state.get(f"features:{symbol}")
        if features and features.value:
            return features.value.get('price')
            
        return None
    
    def _determine_side(self, symbol: str) -> str:
        if not self.state:
            return "BUY"
            
        alpha = self.state.get_sync(f"alpha:{symbol}")
        if alpha and alpha.value:
            direction = alpha.value.get('direction', 0)
            return "BUY" if direction > 0 else "SELL"
            
        return "BUY"
    
    async def _simulate_execution(
        self,
        submit_event: Event,
        order: dict
    ) -> list[Event]:
        import random
        
        events = [submit_event]
        
        should_fill = random.random() < self.FILL_PROBABILITY
        
        if not should_fill:
            cancel_event = await self._create_order_event(
                EventType.ORDER_CANCELED,
                order,
                submit_event.trace_id,
                rejection_reason="simulation_fill_miss"
            )
            events.append(cancel_event)
            return events
        
        is_partial = random.random() < self.PARTIAL_FILL_PROBABILITY
        
        slippage = random.gauss(self.slippage_bps, self.slippage_bps / 2) / 10000
        
        if order['side'] == 'BUY':
            fill_price = order['price'] * (1 + slippage)
        else:
            fill_price = order['price'] * (1 - slippage)
        
        if is_partial:
            fill_qty = order['quantity'] * random.uniform(0.3, 0.7)
            
            partial_event = await self._create_order_event(
                EventType.ORDER_PARTIAL,
                order,
                submit_event.trace_id,
                filled_quantity=fill_qty,
                avg_fill_price=fill_price,
                slippage_bps=slippage * 10000
            )
            events.append(partial_event)
            
            order['filled_quantity'] = fill_qty
            order['status'] = 'PARTIAL'
            
            second_fill_qty = order['quantity'] - fill_qty
            if second_fill_qty > 0:
                final_event = await self._create_order_event(
                    EventType.ORDER_FILLED,
                    order,
                    submit_event.trace_id,
                    filled_quantity=second_fill_qty,
                    avg_fill_price=fill_price * (1 + slippage / 2),
                    slippage_bps=slippage * 12000
                )
                events.append(final_event)
        else:
            fill_event = await self._create_order_event(
                EventType.ORDER_FILLED,
                order,
                submit_event.trace_id,
                filled_quantity=order['quantity'],
                avg_fill_price=fill_price,
                slippage_bps=slippage * 10000
            )
            events.append(fill_event)
        
        return events
    
    async def _create_order_event(
        self,
        event_type: EventType,
        order: dict,
        trace_id: str,
        **kwargs
    ) -> Event:
        payload = OrderPayload(
            order_id=order['order_id'],
            symbol=order['symbol'],
            side=order['side'],
            quantity=order['quantity'],
            price=order['price'],
            filled_quantity=kwargs.get('filled_quantity', order.get('filled_quantity', 0)),
            avg_fill_price=kwargs.get('avg_fill_price', order.get('avg_fill_price', 0)),
            status=kwargs.get('status', order.get('status', 'NEW')),
            slippage_bps=kwargs.get('slippage_bps', order.get('slippage_bps', 0)),
            latency_ms=kwargs.get('latency_ms', order.get('latency_ms', 0)),
            rejection_reason=kwargs.get('rejection_reason'),
        )
        
        return Event.create(
            event_type=event_type,
            symbol=order['symbol'],
            payload=asdict(payload),
            trace_id=trace_id,
            source=self.config.name if self.config else "execution_processor",
        )
