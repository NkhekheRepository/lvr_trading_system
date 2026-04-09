"""
Execution Processor - Handles order execution with adaptive strategies.

Processes RISK_EVALUATED events and produces order events
(SUBMITTED, PARTIAL, FILLED, CANCELED, REJECTED).

Features:
- Adaptive slippage based on market conditions
- Order splitting (TWAP/VWAP)
- Queue position estimation
- Order book depth awareness
- Execution mode: SIM, PAPER, LIVE
"""

import logging
import random
import asyncio
from typing import Optional, NamedTuple
from dataclasses import asdict
from datetime import datetime
from collections import deque
import uuid

from core.event import Event, EventType, OrderPayload
from core.processor import ProcessorConfig
from core.bus import EventBus
from core.state import DistributedState
from processors.base_processor import BaseProcessor

logger = logging.getLogger(__name__)


class MarketCondition(NamedTuple):
    volatility: float
    spread_bps: float
    depth_factor: float
    queue_position: int
    urgency: float


class AdaptiveExecution:
    """
    Adaptive execution strategy.
    
    Adjusts slippage, order size, and execution timing based on:
    - Order book depth
    - Queue position
    - Volatility regime
    - Liquidity conditions
    """
    
    QUEUE_POSITION_WEIGHT = 0.3
    DEPTH_WEIGHT = 0.3
    VOLATILITY_WEIGHT = 0.4
    
    def __init__(self):
        self._slippage_history = deque(maxlen=100)
        self._latency_history = deque(maxlen=100)
    
    def calculate_adaptive_slippage(
        self,
        base_slippage_bps: float,
        market: MarketCondition
    ) -> float:
        """
        Calculate adaptive slippage based on market conditions.
        
        Higher slippage when:
        - Queue position is deeper
        - Order book depth is low
        - Volatility is high
        """
        queue_factor = 1 + (market.queue_position / 100) * self.QUEUE_POSITION_WEIGHT
        depth_factor = max(1.0, 1 + (1 - market.depth_factor) * self.DEPTH_WEIGHT)
        vol_factor = 1 + market.volatility * self.VOLATILITY_WEIGHT
        
        adaptive_slippage = base_slippage_bps * queue_factor * depth_factor * vol_factor
        
        return min(adaptive_slippage, base_slippage_bps * 3)
    
    def estimate_queue_position(
        self,
        symbol: str,
        side: str,
        size: float
    ) -> int:
        """
        Estimate queue position based on order book state.
        
        Returns estimated position in queue (lower = better).
        """
        return max(1, int(size / 0.1) + 1)
    
    def get_order_book_depth(self, symbol: str, side: str) -> float:
        """
        Get order book depth factor (0-1, higher = more liquid).
        
        In production, this would query real market data.
        """
        return random.uniform(0.5, 1.0)


class OrderSlicer:
    """
    Order slicing for large orders.
    
    Implements TWAP and VWAP splitting strategies.
    """
    
    def __init__(
        self,
        num_slices: int = 4,
        execution_window_seconds: int = 60,
    ):
        self.num_slices = max(1, num_slices)
        self.execution_window = max(10, execution_window_seconds)
    
    def create_twap_slices(
        self,
        total_quantity: float,
        interval_seconds: Optional[int] = None
    ) -> list[tuple[float, int]]:
        """
        Create TWAP order slices.
        
        Returns list of (quantity, delay_seconds) tuples.
        """
        if interval_seconds is None:
            interval_seconds = self.execution_window // self.num_slices
        
        slice_size = total_quantity / self.num_slices
        
        slices = []
        cumulative_delay = 0
        for _ in range(self.num_slices):
            slices.append((slice_size, cumulative_delay))
            cumulative_delay += interval_seconds
        
        return slices
    
    def create_vwap_slices(
        self,
        total_quantity: float,
        volume_profile: list[float]
    ) -> list[tuple[float, float]]:
        """
        Create VWAP order slices based on volume profile.
        
        Returns list of (quantity, target_volume_fraction) tuples.
        """
        if not volume_profile:
            return [(total_quantity, 1.0)]
        
        total_volume = sum(volume_profile)
        if total_volume == 0:
            return [(total_quantity, 1.0)]
        
        slices = []
        for vol_fraction in volume_profile:
            qty = total_quantity * (vol_fraction / total_volume)
            slices.append((qty, vol_fraction / total_volume))
        
        return slices


class ExecutionProcessor(BaseProcessor):
    """
    Handles order execution with adaptive strategies.
    
    Execution modes:
    - SIM: Simulated fills with adaptive slippage
    - PAPER: Paper trading with real-time simulation
    - LIVE: Real order placement via vnpy
    
    Features:
    - Adaptive slippage
    - Order splitting (TWAP/VWAP)
    - Queue position estimation
    - Order book depth awareness
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
        enable_order_splitting: bool = True,
        num_slices: int = 4,
    ):
        super().__init__(bus, state, config)
        self.execution_mode = execution_mode
        self.base_slippage_bps = slippage_bps
        self.enable_order_splitting = enable_order_splitting
        self.num_slices = num_slices
        
        self._pending_orders: dict[str, dict] = {}
        self._fill_latencies: list[float] = []
        self._adaptive = AdaptiveExecution()
        self._slicer = OrderSlicer(num_slices=num_slices)
    
    def event_types(self) -> list[EventType]:
        return [EventType.RISK_EVALUATED]
    
    async def process_event(self, event: Event) -> Optional[Event | list[Event]]:
        if not await self._validate(event):
            return None
            
        symbol = event.symbol
        payload = event.payload
        
        if not payload.get('approved', False):
            return None
        
        regime = await self._get_regime(symbol)
        position_size = payload.get('position_size_pct', 0.1)
        quantity = await self._calculate_order_quantity(
            symbol, position_size, regime
        )
        
        price = await self._get_current_price(symbol)
        if not price:
            return None
        
        side = self._determine_side(symbol)
        
        market = await self._get_market_condition(symbol, side, quantity)
        
        adaptive_slippage = self._adaptive.calculate_adaptive_slippage(
            self.base_slippage_bps,
            market
        )
        
        if self.enable_order_splitting and quantity > self._get_min_slice_size():
            return await self._execute_split_order(
                symbol, quantity, price, side, adaptive_slippage, event
            )
        else:
            return await self._execute_single_order(
                symbol, quantity, price, side, adaptive_slippage, event
            )
    
    async def _execute_single_order(
        self,
        symbol: str,
        quantity: float,
        price: float,
        side: str,
        slippage_bps: float,
        event: Event,
    ) -> list[Event]:
        """Execute a single order."""
        order_id = await self._generate_order_id(symbol)
        
        order = {
            'order_id': order_id,
            'symbol': symbol,
            'quantity': quantity,
            'price': price,
            'side': side,
            'status': 'PENDING',
            'filled_quantity': 0,
            'avg_fill_price': 0,
            'slippage_bps': slippage_bps,
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
            return await self._simulate_execution(submit_event, order, slippage_bps)
        else:
            return submit_event
    
    async def _execute_split_order(
        self,
        symbol: str,
        total_quantity: float,
        price: float,
        side: str,
        slippage_bps: float,
        event: Event,
    ) -> list[Event]:
        """Execute split order using TWAP strategy."""
        slices = self._slicer.create_twap_slices(total_quantity)
        
        all_events = []
        parent_order_id = await self._generate_order_id(symbol)
        
        for i, (slice_qty, delay) in enumerate(slices):
            await asyncio.sleep(delay)
            
            order_id = f"{parent_order_id}_slice_{i+1}"
            
            order = {
                'order_id': order_id,
                'parent_order_id': parent_order_id,
                'symbol': symbol,
                'quantity': slice_qty,
                'price': price,
                'side': side,
                'status': 'PENDING',
                'filled_quantity': 0,
                'avg_fill_price': 0,
                'slippage_bps': slippage_bps * (1 + i * 0.1),
                'latency_ms': 0,
                'created_at': event.timestamp,
                'trace_id': event.trace_id,
                'slice_index': i + 1,
                'total_slices': len(slices),
            }
            
            self._pending_orders[order_id] = order
            
            submit_event = await self._create_order_event(
                EventType.ORDER_SUBMITTED,
                order,
                event.trace_id
            )
            
            if self.execution_mode == "SIM":
                slice_events = await self._simulate_execution(
                    submit_event, order, slippage_bps * (1 + i * 0.1)
                )
                all_events.extend(slice_events)
            else:
                all_events.append(submit_event)
        
        return all_events
    
    def _get_min_slice_size(self) -> float:
        """Minimum order size to trigger splitting (1% of typical position)."""
        return 0.01
    
    async def _get_market_condition(
        self,
        symbol: str,
        side: str,
        size: float
    ) -> MarketCondition:
        """Get current market condition for adaptive execution."""
        volatility = await self._get_volatility(symbol)
        spread_bps = await self._get_spread(symbol)
        depth_factor = self._adaptive.get_order_book_depth(symbol, side)
        queue_position = self._adaptive.estimate_queue_position(symbol, side, size)
        urgency = await self._get_execution_urgency(symbol)
        
        return MarketCondition(
            volatility=volatility,
            spread_bps=spread_bps,
            depth_factor=depth_factor,
            queue_position=queue_position,
            urgency=urgency,
        )
    
    async def _get_volatility(self, symbol: str) -> float:
        """Get current volatility factor."""
        features = await self._get_features(symbol)
        return features.get('volatility', 0.01) if features else 0.01
    
    async def _get_spread(self, symbol: str) -> float:
        """Get current spread in bps."""
        features = await self._get_features(symbol)
        return features.get('spread_bps', 1.0) if features else 1.0
    
    async def _get_execution_urgency(self, symbol: str) -> float:
        """Get execution urgency (0-1, higher = more urgent)."""
        alpha = await self._get_alpha_state(symbol)
        if alpha:
            return alpha.get('urgency', 0.5)
        return 0.5
    
    async def _get_features(self, symbol: str) -> Optional[dict]:
        """Get features for symbol."""
        if not self.state:
            return None
        features = await self.state.get(f"features:{symbol}")
        return features.value if features else None
    
    async def _get_alpha_state(self, symbol: str) -> Optional[dict]:
        """Get alpha state for symbol."""
        if not self.state:
            return None
        alpha = await self.state.get(f"alpha:{symbol}")
        return alpha.value if alpha else None
    
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
        order: dict,
        slippage_bps: float
    ) -> list[Event]:
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
        
        slippage = random.gauss(slippage_bps, slippage_bps / 2) / 10000
        
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
        
        self._adaptive._slippage_history.append(abs(slippage) * 10000)
        
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
