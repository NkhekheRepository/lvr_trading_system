"""
Portfolio Processor - Tracks portfolio state and positions.

Processes order and reconciliation events to maintain portfolio view.
Produces PORTFOLIO_UPDATED events with complete portfolio state.
"""

import logging
from typing import Optional
from dataclasses import asdict
from datetime import datetime

from core.event import Event, EventType, PortfolioPayload
from core.processor import ProcessorConfig
from core.bus import EventBus
from core.state import DistributedState
from processors.base_processor import BaseProcessor

logger = logging.getLogger(__name__)


class PortfolioProcessor(BaseProcessor):
    """
    Maintains portfolio state and position tracking.
    
    Portfolio metrics:
    - Total value (cash + positions)
    - Unrealized/realized PnL
    - Drawdown tracking
    - Daily P&L
    - Position details per symbol
    """
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        config: Optional[ProcessorConfig] = None,
        initial_capital: float = 100000.0,
    ):
        super().__init__(bus, state, config)
        self.initial_capital = initial_capital
        self._positions: dict[str, dict] = {}
        self._daily_pnl = 0.0
        self._peak_value = initial_capital
        self._last_reconciliation: dict[str, int] = {}
        
    def event_types(self) -> list[EventType]:
        return [
            EventType.ORDER_FILLED,
            EventType.ORDER_CANCELED,
            EventType.POSITION_RECONCILED,
        ]
    
    async def process_event(self, event: Event) -> Optional[Event]:
        payload = event.payload
        
        if event.type == EventType.ORDER_FILLED:
            await self._process_fill(event)
        elif event.type == EventType.ORDER_CANCELED:
            await self._process_cancel(event)
        elif event.type == EventType.POSITION_RECONCILED:
            await self._process_reconciliation(event)
        
        return await self._create_portfolio_update(event)
    
    async def _process_fill(self, event: Event) -> None:
        symbol = event.symbol
        payload = event.payload
        
        side = payload.get('side', '')
        quantity = payload.get('filled_quantity', 0)
        price = payload.get('avg_fill_price', 0)
        
        if quantity <= 0:
            return
            
        position = self._positions.get(symbol, {
            'quantity': 0,
            'avg_entry': 0,
            'side': 'NONE',
        })
        
        if side == 'BUY':
            if position['side'] == 'LONG':
                total_qty = position['quantity'] + quantity
                position['avg_entry'] = (
                    (position['avg_entry'] * position['quantity'] + price * quantity) / total_qty
                )
                position['quantity'] = total_qty
            else:
                position['quantity'] = quantity
                position['avg_entry'] = price
                position['side'] = 'LONG'
        elif side == 'SELL':
            if position['side'] == 'SHORT':
                total_qty = position['quantity'] + quantity
                position['avg_entry'] = (
                    (position['avg_entry'] * position['quantity'] + price * quantity) / total_qty
                )
                position['quantity'] = total_qty
            else:
                position['quantity'] = quantity
                position['avg_entry'] = price
                position['side'] = 'SHORT'
        
        self._positions[symbol] = position
        self._last_reconciliation[symbol] = event.timestamp
    
    async def _process_cancel(self, event: Event) -> None:
        symbol = event.symbol
        payload = event.payload
        
        order_id = payload.get('order_id', '')
        remaining_qty = payload.get('quantity', 0) - payload.get('filled_quantity', 0)
        
        if remaining_qty <= 0:
            return
            
        logger.info(f"Order {order_id} canceled, {remaining_qty} not filled")
    
    async def _process_reconciliation(self, event: Event) -> None:
        symbol = event.symbol
        payload = event.payload
        
        position_qty = payload.get('position_qty', 0)
        avg_price = payload.get('avg_price', 0)
        unrealized_pnl = payload.get('unrealized_pnl', 0)
        realized_pnl = payload.get('realized_pnl', 0)
        
        self._positions[symbol] = {
            'quantity': position_qty,
            'avg_entry': avg_price,
            'unrealized_pnl': unrealized_pnl,
            'realized_pnl': realized_pnl,
            'side': 'LONG' if position_qty > 0 else ('SHORT' if position_qty < 0 else 'NONE'),
        }
        
        self._daily_pnl += realized_pnl
        self._last_reconciliation[symbol] = event.timestamp
        
        await self._update_position_state(symbol, self._positions[symbol])
    
    async def _create_portfolio_update(self, event: Event) -> Optional[Event]:
        total_value = await self._calculate_total_value()
        cash = self.initial_capital + self._calculate_total_pnl()
        unrealized = self._calculate_unrealized_pnl()
        realized = self._daily_pnl
        
        current_value = cash + unrealized
        if current_value > self._peak_value:
            self._peak_value = current_value
            
        drawdown_pct = (self._peak_value - current_value) / self._peak_value if self._peak_value > 0 else 0
        
        leverage = self._calculate_leverage()
        
        portfolio_payload = PortfolioPayload(
            total_value=current_value,
            cash=cash,
            unrealized_pnl=unrealized,
            realized_pnl=realized,
            drawdown_pct=drawdown_pct,
            daily_pnl=self._daily_pnl,
            positions=self._positions.copy(),
            leverage=leverage,
        )
        
        output_event = Event.create(
            event_type=EventType.PORTFOLIO_UPDATED,
            payload=asdict(portfolio_payload),
            trace_id=event.trace_id,
            source=self.config.name if self.config else "portfolio_processor",
        )
        
        await self._update_portfolio_state(portfolio_payload)
        
        return output_event
    
    async def _calculate_total_value(self) -> float:
        unrealized = self._calculate_unrealized_pnl()
        return self.initial_capital + self._calculate_total_pnl() + unrealized
    
    def _calculate_total_pnl(self) -> float:
        return sum(p.get('realized_pnl', 0) for p in self._positions.values())
    
    def _calculate_unrealized_pnl(self) -> float:
        unrealized = 0.0
        for symbol, pos in self._positions.items():
            if pos.get('quantity', 0) != 0:
                unrealized += pos.get('unrealized_pnl', 0)
        return unrealized
    
    def _calculate_leverage(self) -> float:
        total_exposure = sum(
            abs(pos.get('quantity', 0) * pos.get('avg_entry', 0))
            for pos in self._positions.values()
        )
        total_value = self.initial_capital + self._calculate_total_pnl()
        
        return total_exposure / total_value if total_value > 0 else 1.0
    
    async def _update_position_state(self, symbol: str, position: dict) -> None:
        if not self.state:
            return
            
        state_key = f"position:{symbol}"
        await self.state.set(
            key=state_key,
            value=position,
            trace_id=self.config.name if self.config else "portfolio_processor",
        )
    
    async def _update_portfolio_state(self, portfolio: PortfolioPayload) -> None:
        if not self.state:
            return
            
        state_key = "portfolio:global"
        await self.state.set(
            key=state_key,
            value={
                'total_value': portfolio.total_value,
                'cash': portfolio.cash,
                'unrealized_pnl': portfolio.unrealized_pnl,
                'realized_pnl': portfolio.realized_pnl,
                'drawdown_pct': portfolio.drawdown_pct,
                'daily_pnl': portfolio.daily_pnl,
                'leverage': portfolio.leverage,
                'updated_at': int(datetime.now().timestamp() * 1000),
            },
            trace_id=self.config.name if self.config else "portfolio_processor",
        )
