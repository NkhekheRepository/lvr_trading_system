"""
Simulated execution engine for backtesting.

This module provides a realistic simulation of exchange execution for
backtesting purposes. The SimulatedExecutionEngine models order fills
based on order book state, applies configurable slippage, and supports
partial fills to give accurate backtest results.

Key Features:
    - Deterministic fills based on order book state
    - Configurable slippage model (volume-proportional)
    - Simulated network latency
    - Partial fill support based on available liquidity
    - Maker/taker fee modeling

Slippage Model:
    slippage_per_unit = α × (Q / D) × (spread / 2)
    
    Where:
    - α = slippage_alpha parameter
    - Q = order quantity
    - D = total depth (top 5 levels)
    - spread = bid-ask spread
    
    This model produces larger slippage for larger orders relative
    to available liquidity, which is realistic for real markets.

Usage:
    >>> engine = SimulatedExecutionEngine(slippage_alpha=0.5, latency_ms=50)
    >>> await engine.connect()
    >>> engine.set_order_book(order_book_snapshot)
    >>> result = await engine.submit_order(order_request)
"""

import asyncio
import logging
import time
from typing import Optional

from app.schemas import (
    ExecutionMode, ExecutionResult, FillEvent, Order, OrderBookSnapshot,
    OrderRequest, OrderStatus, Position, RejectEvent, Side
)

from execution.base import ExecutionEngine

logger = logging.getLogger(__name__)

EPS = 1e-10


class SimulatedExecutionEngine(ExecutionEngine):
    """
    Simulated execution engine for backtesting.
    
    Provides realistic fill simulation based on order book state without
    requiring actual exchange connectivity. Designed for strategy
    backtesting with accurate cost and slippage modeling.
    
    Features:
        - Deterministic fills based on order book state
        - Configurable slippage model
        - Simulated latency
        - Partial fill support
        - Position tracking
    
    Attributes:
        slippage_alpha: Controls slippage magnitude (0=perfect, higher=more slippage).
        latency_ms: Simulated network latency in milliseconds.
        maker_fee: Fee rate for maker orders.
        taker_fee: Fee rate for taker orders.
        zero_slippage: If True, disable slippage (for comparison testing).
    
    Example:
        >>> from app.schemas import OrderRequest, Side, OrderType
        >>> engine = SimulatedExecutionEngine(
        ...     slippage_alpha=0.5,
        ...     latency_ms=100,
        ...     taker_fee=0.0004
        ... )
        >>> await engine.connect()
        >>> engine.set_order_book(book)
        >>> 
        >>> order = OrderRequest(
        ...     order_id="test_001",
        ...     symbol="BTCUSDT",
        ...     side=Side.BUY,
        ...     quantity=0.1,
        ...     order_type=OrderType.MARKET
        ... )
        >>> result = await engine.submit_order(order)
    """

    def __init__(
        self,
        slippage_alpha: float = 0.5,
        latency_ms: int = 100,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0004,
        zero_slippage: bool = False
    ):
        """
        Initialize simulated execution engine.
        
        Args:
            slippage_alpha: Slippage coefficient. Higher values produce more
                slippage. Default 0.5.
            latency_ms: Simulated latency in milliseconds. Default 100.
            maker_fee: Maker fee rate. Default 0.02% (0.0002).
            taker_fee: Taker fee rate. Default 0.04% (0.0004).
            zero_slippage: If True, disable slippage modeling.
        """
        super().__init__()
        self._mode = ExecutionMode.SIM
        self.slippage_alpha = slippage_alpha
        self.latency_ms = latency_ms
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.zero_slippage = zero_slippage

        self._positions: dict[str, Position] = {}
        self._current_book: Optional[OrderBookSnapshot] = None
        self._connected = False

    @property
    def mode(self) -> ExecutionMode:
        """Get execution mode (SIM)."""
        return self._mode

    async def connect(self) -> None:
        """
        Simulate connection to execution backend.
        
        Performs a small delay to simulate connection overhead.
        """
        logger.info("Connecting to simulated execution engine")
        await asyncio.sleep(0.1)
        self._connected = True
        logger.info("Simulated execution engine connected")

    async def disconnect(self) -> None:
        """
        Simulate disconnection from execution backend.
        """
        logger.info("Disconnecting simulated execution engine")
        self._connected = False

    async def health_check(self) -> bool:
        """
        Check engine health.
        
        Returns:
            True if connected, False otherwise.
        """
        return self._connected

    def set_order_book(self, book: OrderBookSnapshot) -> None:
        """
        Set current order book for fill simulation.
        
        The order book is used to determine fill prices and available
        liquidity for order execution.
        
        Args:
            book: OrderBookSnapshot with bids and asks.
        """
        self._current_book = book

    async def submit_order(self, order: OrderRequest) -> ExecutionResult:
        """
        Submit order with simulated execution.
        
        Simulates the full order lifecycle:
        1. Validate connection
        2. Sleep for latency
        3. Check order book liquidity
        4. Calculate fill price with slippage
        5. Update positions
        6. Return execution result
        
        Args:
            order: OrderRequest to execute.
        
        Returns:
            ExecutionResult with fill details or rejection reason.
        """
        if not self._connected:
            return self._reject(
                order,
                "Engine not connected",
                "NOT_CONNECTED"
            )

        start_time = time.time()
        order_record = Order(
            order_id=order.order_id,
            trace_id=order.trace_id,
            timestamp=order.timestamp,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            quantity=order.quantity,
            price=order.price,
            status=OrderStatus.SUBMITTED
        )

        await asyncio.sleep(self.latency_ms / 1000.0)

        book = self._current_book
        if book is None:
            book = self._create_default_book(order.symbol, order.side)

        fill_result = self._simulate_fill(order, book)

        if fill_result["filled_qty"] == 0:
            order_record.status = OrderStatus.REJECTED
            return ExecutionResult(
                success=False,
                order_id=order.order_id,
                trace_id=order.trace_id,
                symbol=order.symbol,
                status=OrderStatus.REJECTED,
                reject_event=RejectEvent(
                    trace_id=order.trace_id,
                    order_id=order.order_id,
                    symbol=order.symbol,
                    reason="No liquidity"
                ),
                latency_ms=(time.time() - start_time) * 1000
            )

        order_record.status = OrderStatus.FILLED if fill_result["filled_qty"] == order.quantity else OrderStatus.PARTIAL
        order_record.filled_quantity = fill_result["filled_qty"]
        order_record.avg_fill_price = fill_result["avg_price"]

        self._update_position(order, fill_result)

        fill_event = FillEvent(
            trace_id=order.trace_id,
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=fill_result["filled_qty"],
            price=fill_result["avg_price"],
            fee=fill_result["fee"],
            slippage=fill_result["slippage"],
            latency_ms=fill_result["latency"]
        )

        self._emit_fill(fill_event)

        return ExecutionResult(
            success=True,
            order_id=order.order_id,
            trace_id=order.trace_id,
            symbol=order.symbol,
            status=order_record.status,
            filled_quantity=fill_result["filled_qty"],
            avg_fill_price=fill_result["avg_price"],
            total_cost=fill_result["total_cost"],
            slippage=fill_result["slippage"],
            fee=fill_result["fee"],
            fill_events=[fill_event],
            latency_ms=(time.time() - start_time) * 1000
        )

    def _create_default_book(self, symbol: str, side: Side) -> OrderBookSnapshot:
        """
        Create default order book when none available.
        
        Generates a synthetic order book with 20 levels on each side
        for symbols without external data.
        
        Args:
            symbol: Trading symbol to determine price levels.
            side: Order side for default book generation.
        
        Returns:
            OrderBookSnapshot with synthetic bid/ask levels.
        """
        mid = 50000.0 if "BTC" in symbol else 1800.0
        spread = mid * 0.0001

        bids = [(mid - spread - i * 0.1, 1.0) for i in range(20)]
        asks = [(mid + i * 0.1, 1.0) for i in range(20)]

        return OrderBookSnapshot(
            timestamp=int(time.time() * 1000),
            symbol=symbol,
            bids=bids,
            asks=asks
        )

    def _simulate_fill(
        self,
        order: OrderRequest,
        book: OrderBookSnapshot
    ) -> dict:
        """
        Simulate fill based on order book state.
        
        Calculates fill price, quantity, slippage, and fees based on
        the order book snapshot and engine parameters.
        
        Mathematical Model:
            depth = Σ(size_i) for i in top 5 levels
            
            fill_ratio = min(Q / depth, 1.0)
            
            if not zero_slippage:
                slippage = α × (Q / depth) × (spread / 2)
            else:
                slippage = 0
            
            fill_price = best_price + slippage (for BUY)
                        = best_price - slippage (for SELL)
            
            fee = filled_qty × fill_price × taker_fee
        
        Args:
            order: Order being filled.
            book: Order book snapshot for price/liquidity.
        
        Returns:
            Dictionary with filled_qty, avg_price, slippage, fee,
            total_cost, and latency.
        """
        if order.side == Side.BUY:
            levels = book.asks
            best_price = book.best_ask
        else:
            levels = book.bids
            best_price = book.best_bid

        if not levels:
            return {"filled_qty": 0, "avg_price": 0, "slippage": 0, "fee": 0, "total_cost": 0, "latency": 0}

        total_depth = sum(size for _, size in levels[:5])
        fill_ratio = min(order.quantity / (total_depth + EPS), 1.0)

        slippage_per_unit = 0.0
        if not self.zero_slippage and total_depth > 0:
            slippage_per_unit = self.slippage_alpha * (order.quantity / total_depth) * (book.spread / 2 + 1)

        avg_price = best_price + slippage_per_unit if order.side == Side.BUY else best_price - slippage_per_unit

        filled_qty = order.quantity * fill_ratio
        slippage_total = slippage_per_unit * filled_qty
        fee = filled_qty * avg_price * self.taker_fee
        total_cost = slippage_total + fee

        return {
            "filled_qty": filled_qty,
            "avg_price": avg_price,
            "slippage": slippage_total,
            "fee": fee,
            "total_cost": total_cost,
            "latency": self.latency_ms
        }

    def _update_position(
        self,
        order: OrderRequest,
        fill_result: dict
    ) -> None:
        """
        Update position after fill.
        
        Handles position management for both buy and sell fills,
        including position averaging, partial closes, and full reverses.
        
        Position Update Logic:
            BUY order:
                - Long existing: Average up position
                - Short existing: Reduce or reverse to long
            
            SELL order:
                - Short existing: Average down position
                - Long existing: Reduce or reverse to short
        
        Args:
            order: Original order request.
            fill_result: Fill result from _simulate_fill.
        """
        if order.symbol not in self._positions:
            self._positions[order.symbol] = Position(symbol=order.symbol)

        pos = self._positions[order.symbol]
        qty = fill_result["filled_qty"]
        price = fill_result["avg_price"]

        if order.side == Side.BUY:
            if pos.quantity >= 0:
                new_qty = pos.quantity + qty
                pos.entry_price = (pos.entry_price * pos.quantity + price * qty) / new_qty if new_qty > 0 else 0
                pos.quantity = new_qty
            else:
                if qty <= abs(pos.quantity):
                    pos.quantity += qty
                else:
                    pos.quantity += qty
                    pos.entry_price = price
        else:
            if pos.quantity <= 0:
                new_qty = pos.quantity - qty
                pos.entry_price = (pos.entry_price * abs(pos.quantity) + price * qty) / abs(new_qty) if new_qty != 0 else 0
                pos.quantity = new_qty
            else:
                if qty <= pos.quantity:
                    pos.quantity -= qty
                else:
                    pos.quantity -= qty
                    pos.entry_price = price

        pos.last_update = int(time.time() * 1000)

    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel order (simulated).
        
        In simulation, orders are filled immediately so cancellation
        always succeeds (order was already filled or doesn't exist).
        
        Args:
            order_id: Order ID to cancel.
        
        Returns:
            Always True in simulation.
        """
        logger.info(f"Cancel requested for order {order_id}")
        return True

    async def get_position(self, symbol: str) -> Position:
        """
        Get position for symbol.
        
        Args:
            symbol: Trading symbol.
        
        Returns:
            Position object for the symbol (creates empty if none).
        """
        if symbol not in self._positions:
            self._positions[symbol] = Position(symbol=symbol)
        return self._positions[symbol]

    async def get_open_orders(self, symbol: Optional[str] = None) -> list[Order]:
        """
        Get open orders (simulated - always empty).
        
        In simulation, orders are filled immediately upon submission
        so there are never any open orders.
        
        Args:
            symbol: Ignored in simulation.
        
        Returns:
            Empty list (simulated orders are always filled immediately).
        """
        return []

    def _reject(
        self,
        order: OrderRequest,
        reason: str,
        error_code: str
    ) -> ExecutionResult:
        """
        Create rejection result.
        
        Args:
            order: Order that was rejected.
            reason: Human-readable rejection reason.
            error_code: Machine-readable error code.
        
        Returns:
            ExecutionResult with reject_event set.
        """
        reject = RejectEvent(
            trace_id=order.trace_id,
            order_id=order.order_id,
            symbol=order.symbol,
            reason=reason,
            error_code=error_code
        )
        self._emit_reject(reject)
        return ExecutionResult(
            success=False,
            order_id=order.order_id,
            trace_id=order.trace_id,
            symbol=order.symbol,
            status=OrderStatus.REJECTED,
            reject_event=reject
        )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel order (simulated)."""
        logger.info(f"Cancel requested for order {order_id}")
        return True

    async def get_position(self, symbol: str) -> Position:
        """Get position for symbol."""
        if symbol not in self._positions:
            self._positions[symbol] = Position(symbol=symbol)
        return self._positions[symbol]

    async def get_open_orders(self, symbol: Optional[str] = None) -> list[Order]:
        """Get open orders (simulated - always empty)."""
        return []

    def _reject(
        self,
        order: OrderRequest,
        reason: str,
        error_code: str
    ) -> ExecutionResult:
        """Create rejection result."""
        reject = RejectEvent(
            trace_id=order.trace_id,
            order_id=order.order_id,
            symbol=order.symbol,
            reason=reason,
            error_code=error_code
        )
        self._emit_reject(reject)
        return ExecutionResult(
            success=False,
            order_id=order.order_id,
            trace_id=order.trace_id,
            symbol=order.symbol,
            status=OrderStatus.REJECTED,
            reject_event=reject
        )
