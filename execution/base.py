"""
Execution Abstraction Layer - Abstract interface for all execution engines.

This module defines the core interfaces for order execution across all
trading modes (SIM, PAPER, LIVE). The Execution Abstraction Layer (EAL)
ensures complete decoupling from any specific execution backend while
providing consistent behavior regardless of the underlying implementation.

Architecture:
    Strategy → OrderRequest → ExecutionEngine.submit_order()
                                   ↓
                    ┌─────────────┴─────────────┐
                    ↓             ↓             ↓
                  SIM        PAPER           LIVE
                    └─────────────┬─────────────┘
                                  ↓
                        ExecutionResult
                                  ↓
                    ┌─────────────┴─────────────┐
                    ↓             ↓             ↓
                FillEvent    RejectEvent    Order Update

Callback System:
    Components can register callbacks for:
    - on_fill: Triggered on order fills
    - on_reject: Triggered on order rejection
    - on_order_update: Triggered on any order status change

Example:
    >>> from execution.base import ExecutionEngine
    >>> from execution.simulator import SimulatedExecutionEngine
    >>> from app.schemas import OrderRequest, Side, OrderType
    >>>
    >>> engine = SimulatedExecutionEngine()
    >>> await engine.connect()
    >>>
    >>> order = OrderRequest(
    ...     symbol="BTCUSDT",
    ...     side=Side.BUY,
    ...     quantity=0.1,
    ...     order_type=OrderType.MARKET
    ... )
    >>> result = await engine.submit_order(order)
    >>> print(f"Success: {result.success}, Fill: {result.filled_quantity}")
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
    This ensures full decoupling from any specific execution backend while
    maintaining consistent behavior across all trading modes.
    
    Execution Modes:
        SIM: Simulated execution for backtesting with synthetic fills
        PAPER: Paper trading against live market data (simulated fills)
        LIVE: Real execution against exchange with real fills and risk
    
    Safety Requirements:
        LIVE mode requires explicit safety confirmation via LVR_LIVE_CONFIRMED=true
        All modes must implement proper connection lifecycle management
        Health checks should be performed before any trading activity
    
    Implementation Requirements:
        Subclasses MUST implement:
        - mode property: Return execution mode
        - submit_order: Submit and execute orders
        - cancel_order: Cancel pending orders
        - get_position: Query current positions
        - get_open_orders: Query open orders
        - connect: Establish backend connection
        - disconnect: Close backend connection
        - health_check: Verify engine health
    
    Example:
        >>> class MyExecutionEngine(ExecutionEngine):
        ...     @property
        ...     def mode(self) -> ExecutionMode:
        ...         return ExecutionMode.LIVE
        ...
        ...     async def submit_order(self, order: OrderRequest) -> ExecutionResult:
        ...         # Implement actual order submission
        ...         pass
    """

    @property
    @abstractmethod
    def mode(self) -> ExecutionMode:
        """
        Get execution mode.
        
        Returns:
            ExecutionMode enum value indicating current mode (SIM/PAPER/LIVE).
        """
        pass

    @abstractmethod
    async def submit_order(self, order: OrderRequest) -> ExecutionResult:
        """
        Submit an order for execution.
        
        Handles order submission across all order types (market, limit, stop).
        Returns immediately with an ExecutionResult containing fill events
        for SIM/PAPER or acknowledgment for LIVE mode.
        
        Args:
            order: OrderRequest containing:
                - symbol: Trading pair (e.g., "BTCUSDT")
                - side: BUY or SELL
                - quantity: Order quantity
                - order_type: MARKET, LIMIT, STOP, STOP_LIMIT
                - price: Limit price (required for LIMIT orders)
                - stop_price: Stop price (required for STOP orders)
                - order_id: Unique order identifier
                - trace_id: Correlation ID for tracing
        
        Returns:
            ExecutionResult containing:
                - success: Boolean indicating if order was accepted
                - order_id: Original order ID
                - status: Final order status
                - filled_quantity: Number of contracts filled
                - avg_fill_price: Average fill price
                - fill_events: List of individual fill events
                - reject_event: Present if order was rejected
                - latency_ms: Order processing latency
        
        Raises:
            No explicit raises - all errors should be captured in
            ExecutionResult with appropriate reject_event.
        """
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel a pending order.
        
        Attempts to cancel an order that is still pending or partially filled.
        The success of cancellation depends on exchange order state.
        
        Args:
            order_id: Unique identifier of the order to cancel.
        
        Returns:
            True if cancellation was successful or order was already filled/cancelled.
            False if cancellation failed (e.g., order not found, exchange error).
        """
        pass

    @abstractmethod
    async def get_position(self, symbol: str) -> Position:
        """
        Get current position for a symbol.
        
        Returns the current position including quantity, entry price,
        unrealized PnL, and other position metrics.
        
        Args:
            symbol: Trading symbol (e.g., "BTCUSDT").
        
        Returns:
            Position object containing:
                - symbol: Trading symbol
                - quantity: Current position size (+ for long, - for short)
                - entry_price: Average entry price
                - current_price: Current market price
                - unrealized_pnl: Mark-to-market PnL
                - realized_pnl: Closed PnL
                - last_update: Timestamp of last update
        """
        pass

    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> list[Order]:
        """
        Get all open orders, optionally filtered by symbol.
        
        Returns orders that are pending or partially filled.
        
        Args:
            symbol: Optional symbol to filter orders. If None, returns
                all open orders across all symbols.
        
        Returns:
            List of Order objects representing pending orders.
            Returns empty list if no open orders exist.
        """
        pass

    @abstractmethod
    async def connect(self) -> None:
        """
        Establish connection to execution backend.
        
        Should be called before any trading activity. Implementations
        should handle authentication, session setup, and any required
        handshakes with the execution backend.
        
        Raises:
            ConnectionError: If connection fails.
        
        Example:
            >>> engine = MyExecutionEngine()
            >>> await engine.connect()
            >>> print("Connected successfully")
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """
        Disconnect from execution backend.
        
        Should be called during shutdown to clean up resources and
        ensure no pending operations are left active. Implementations
        should cancel any pending orders and close network connections.
        
        Example:
            >>> await engine.disconnect()
            >>> print("Disconnected")
        """
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Check if execution engine is healthy.
        
        Performs internal health checks to verify the engine is
        functioning correctly and can accept orders.
        
        Returns:
            True if engine is healthy and ready to accept orders.
            False if engine has issues (connection lost, rate limited, etc.).
        
        Example:
            >>> if await engine.health_check():
            ...     print("Engine healthy, ready to trade")
            ... else:
            ...     print("Engine unhealthy, reconnecting...")
        """
        pass

    def on_fill(self, callback: Callable[[FillEvent], None]) -> None:
        """
        Register a callback for fill events.
        
        The callback will be invoked whenever an order is fully or
        partially filled. Multiple callbacks can be registered.
        
        Args:
            callback: Callable that accepts a FillEvent parameter.
        
        Example:
            >>> def handle_fill(fill: FillEvent):
            ...     print(f"Filled: {fill.quantity} @ {fill.price}")
            ...
            >>> engine.on_fill(handle_fill)
        """
        self._fill_callbacks.append(callback)

    def on_reject(self, callback: Callable[[RejectEvent], None]) -> None:
        """
        Register a callback for rejection events.
        
        The callback will be invoked whenever an order is rejected.
        
        Args:
            callback: Callable that accepts a RejectEvent parameter.
        
        Example:
            >>> def handle_reject(reject: RejectEvent):
            ...     print(f"Rejected: {reject.reason}")
            ...
            >>> engine.on_reject(handle_reject)
        """
        self._reject_callbacks.append(callback)

    def on_order_update(self, callback: Callable[[Order], None]) -> None:
        """
        Register a callback for order update events.
        
        The callback will be invoked for any order status change.
        
        Args:
            callback: Callable that accepts an Order parameter.
        
        Example:
            >>> def handle_update(order: Order):
            ...     print(f"Order {order.order_id}: {order.status}")
            ...
            >>> engine.on_order_update(handle_update)
        """
        self._order_update_callbacks.append(callback)

    def _emit_fill(self, fill: FillEvent) -> None:
        """
        Emit fill event to registered callbacks.
        
        Internal method called by the engine when fills occur.
        Catches and logs any exceptions from callbacks to prevent
        callback errors from affecting the main execution flow.
        
        Args:
            fill: FillEvent to emit.
        """
        for callback in self._fill_callbacks:
            try:
                callback(fill)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Fill callback error: {e}")

    def _emit_reject(self, reject: RejectEvent) -> None:
        """
        Emit reject event to registered callbacks.
        
        Internal method called by the engine when orders are rejected.
        
        Args:
            reject: RejectEvent to emit.
        """
        for callback in self._reject_callbacks:
            try:
                callback(reject)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Reject callback error: {e}")

    def _emit_order_update(self, order: Order) -> None:
        """
        Emit order update to registered callbacks.
        
        Internal method called by the engine on order status changes.
        
        Args:
            order: Order with updated status.
        """
        for callback in self._order_update_callbacks:
            try:
                callback(order)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Order update callback error: {e}")

    def __init__(self):
        """
        Initialize execution engine with empty callback lists.
        
        Subclasses should call super().__init__() if they override __init__.
        """
        self._fill_callbacks: list[Callable[[FillEvent], None]] = []
        self._reject_callbacks: list[Callable[[RejectEvent], None]] = []
        self._order_update_callbacks: list[Callable[[Order], None]] = []


class OrderManager:
    """
    Manages order state and lifecycle.
    
    Provides centralized order tracking for the execution layer.
    Maintains both a flat index by order_id and a per-symbol index
    for efficient lookups.
    
    Attributes:
        _orders: Dictionary mapping order_id to Order objects.
        _orders_by_symbol: Dictionary mapping symbol to list of order_ids.
    
    Thread Safety:
        This class is NOT thread-safe. Use external locking if accessed
        from multiple threads.
    
    Example:
        >>> manager = OrderManager()
        >>> order = Order(order_id="123", symbol="BTCUSDT", ...)
        >>> manager.add_order(order)
        >>> retrieved = manager.get_order("123")
        >>> open_orders = manager.get_open_orders("BTCUSDT")
    """

    def __init__(self):
        """
        Initialize order manager with empty state.
        """
        self._orders: dict[str, Order] = {}
        self._orders_by_symbol: dict[str, list[str]] = {}

    def add_order(self, order: Order) -> None:
        """
        Add a new order to the manager.
        
        Args:
            order: Order object to add. Must have order_id and symbol set.
        
        Raises:
            KeyError: If order_id already exists (use update_order instead).
        """
        self._orders[order.order_id] = order
        if order.symbol not in self._orders_by_symbol:
            self._orders_by_symbol[order.symbol] = []
        self._orders_by_symbol[order.symbol].append(order.order_id)

    def update_order(self, order: Order) -> None:
        """
        Update an existing order.
        
        Use this for status updates, fill updates, etc.
        
        Args:
            order: Order object with updated fields.
        """
        self._orders[order.order_id] = order

    def get_order(self, order_id: str) -> Optional[Order]:
        """
        Get order by ID.
        
        Args:
            order_id: Unique order identifier.
        
        Returns:
            Order object if found, None otherwise.
        """
        return self._orders.get(order_id)

    def get_orders_for_symbol(self, symbol: str) -> list[Order]:
        """
        Get all orders for a specific symbol.
        
        Args:
            symbol: Trading symbol to filter by.
        
        Returns:
            List of Order objects for the symbol, excluding removed orders.
        """
        order_ids = self._orders_by_symbol.get(symbol, [])
        return [self._orders[oid] for oid in order_ids if oid in self._orders]

    def get_open_orders(self, symbol: Optional[str] = None) -> list[Order]:
        """
        Get all open (pending, submitted, or partial) orders.
        
        Args:
            symbol: Optional symbol to filter by. If None, returns
                open orders across all symbols.
        
        Returns:
            List of open Order objects.
        """
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
        """
        Remove an order from the manager.
        
        Args:
            order_id: Order ID to remove.
        
        Note:
            Does not raise if order_id not found.
        """
        if order_id in self._orders:
            order = self._orders[order_id]
            self._orders_by_symbol[order.symbol].remove(order_id)
            del self._orders[order_id]
