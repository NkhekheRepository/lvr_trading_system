"""
Simulated execution engine for backtesting.
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
    
    Features:
    - Deterministic fills based on order book state
    - Configurable slippage model
    - Simulated latency
    - Partial fill support
    """

    def __init__(
        self,
        slippage_alpha: float = 0.5,
        latency_ms: int = 100,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0004,
        zero_slippage: bool = False
    ):
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
        return self._mode

    async def connect(self) -> None:
        """Simulate connection."""
        logger.info("Connecting to simulated execution engine")
        await asyncio.sleep(0.1)
        self._connected = True
        logger.info("Simulated execution engine connected")

    async def disconnect(self) -> None:
        """Simulate disconnection."""
        logger.info("Disconnecting simulated execution engine")
        self._connected = False

    async def health_check(self) -> bool:
        return self._connected

    def set_order_book(self, book: OrderBookSnapshot) -> None:
        """Set current order book for fill simulation."""
        self._current_book = book

    async def submit_order(self, order: OrderRequest) -> ExecutionResult:
        """Submit order with simulated execution."""
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
        """Create default order book when none available."""
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
        """Simulate fill based on order book state."""
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
        """Update position after fill."""
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
