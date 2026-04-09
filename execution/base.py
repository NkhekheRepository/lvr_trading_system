"""
Execution Abstraction Layer - Abstract interface for all execution engines.
"""

from abc import ABC, abstractmethod
from typing import Callable, Optional

from app.schemas import (
    ExecutionMode, ExecutionResult, FillEvent, Order, OrderBookSnapshot,
    OrderRequest, OrderStatus, Position, RejectEvent
)


class ExecutionEngine(ABC):
    """
    Abstract execution engine interface.
    
    All execution engines (SIM, PAPER, LIVE) MUST implement this interface.
    This ensures full decoupling from any specific execution backend.
    """

    @property
    @abstractmethod
    def mode(self) -> ExecutionMode:
        """Get execution mode."""
        pass

    @abstractmethod
    async def submit_order(self, order: OrderRequest) -> ExecutionResult:
        """
        Submit an order.
        
        Args:
            order: Order request with symbol, side, quantity, type, etc.
            
        Returns:
            ExecutionResult with fill events or rejection
        """
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an order.
        
        Args:
            order_id: Order ID to cancel
            
        Returns:
            True if cancelled, False otherwise
        """
        pass

    @abstractmethod
    async def get_position(self, symbol: str) -> Position:
        """Get current position for symbol."""
        pass

    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> list[Order]:
        """Get all open orders, optionally filtered by symbol."""
        pass

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to execution backend."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from execution backend."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if execution engine is healthy."""
        pass

    def on_fill(self, callback: Callable[[FillEvent], None]) -> None:
        """Register fill callback."""
        self._fill_callbacks.append(callback)

    def on_reject(self, callback: Callable[[RejectEvent], None]) -> None:
        """Register reject callback."""
        self._reject_callbacks.append(callback)

    def on_order_update(self, callback: Callable[[Order], None]) -> None:
        """Register order update callback."""
        self._order_update_callbacks.append(callback)

    def _emit_fill(self, fill: FillEvent) -> None:
        """Emit fill event to callbacks."""
        for callback in self._fill_callbacks:
            try:
                callback(fill)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Fill callback error: {e}")

    def _emit_reject(self, reject: RejectEvent) -> None:
        """Emit reject event to callbacks."""
        for callback in self._reject_callbacks:
            try:
                callback(reject)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Reject callback error: {e}")

    def _emit_order_update(self, order: Order) -> None:
        """Emit order update to callbacks."""
        for callback in self._order_update_callbacks:
            try:
                callback(order)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Order update callback error: {e}")

    def __init__(self):
        self._fill_callbacks: list[Callable[[FillEvent], None]] = []
        self._reject_callbacks: list[Callable[[RejectEvent], None]] = []
        self._order_update_callbacks: list[Callable[[Order], None]] = []


class OrderManager:
    """Manages order state and lifecycle."""

    def __init__(self):
        self._orders: dict[str, Order] = {}
        self._orders_by_symbol: dict[str, list[str]] = {}

    def add_order(self, order: Order) -> None:
        """Add new order."""
        self._orders[order.order_id] = order
        if order.symbol not in self._orders_by_symbol:
            self._orders_by_symbol[order.symbol] = []
        self._orders_by_symbol[order.symbol].append(order.order_id)

    def update_order(self, order: Order) -> None:
        """Update existing order."""
        self._orders[order.order_id] = order

    def get_order(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)

    def get_orders_for_symbol(self, symbol: str) -> list[Order]:
        order_ids = self._orders_by_symbol.get(symbol, [])
        return [self._orders[oid] for oid in order_ids if oid in self._orders]

    def get_open_orders(self, symbol: Optional[str] = None) -> list[Order]:
        if symbol:
            return [
                o for o in self.get_orders_for_symbol(symbol)
                if o.status in (OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIAL)
            ]
        return [
            o for o in self._orders.values()
            if o.status in (OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIAL)
        ]

    def remove_order(self, order_id: str) -> None:
        if order_id in self._orders:
            order = self._orders[order_id]
            self._orders_by_symbol[order.symbol].remove(order_id)
            del self._orders[order_id]
