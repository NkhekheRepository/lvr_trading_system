"""
Paper trading execution engine using real-time market data simulation.
"""

import asyncio
import logging
import time
import random
from typing import Optional

from app.schemas import (
    ExecutionMode, ExecutionResult, FillEvent, Order, OrderBookSnapshot,
    OrderRequest, OrderStatus, Position, RejectEvent, Side
)

from execution.base import ExecutionEngine

logger = logging.getLogger(__name__)

EPS = 1e-10


class PaperExecutionEngine(ExecutionEngine):
    """
    Paper trading engine using real market data.
    
    Features:
    - Real-time market data ingestion
    - Realistic slippage based on order book depth
    - Latency simulation with jitter
    - Partial fills based on available liquidity
    """

    def __init__(
        self,
        slippage_alpha: float = 0.5,
        slippage_multiplier: float = 1.0,
        latency_base_ms: int = 50,
        latency_jitter_ms: int = 50,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0004
    ):
        super().__init__()
        self._mode = ExecutionMode.PAPER
        self.slippage_alpha = slippage_alpha
        self.slippage_multiplier = slippage_multiplier
        self.latency_base_ms = latency_base_ms
        self.latency_jitter_ms = latency_jitter_ms
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee

        self._positions: dict[str, Position] = {}
        self._current_books: dict[str, OrderBookSnapshot] = {}
        self._connected = False
        self._running = False

    @property
    def mode(self) -> ExecutionMode:
        return self._mode

    async def connect(self) -> None:
        """Connect to paper trading."""
        logger.info("Connecting to paper trading engine")
        await asyncio.sleep(0.1)
        self._connected = True
        self._running = True
        logger.info("Paper trading engine connected")

    async def disconnect(self) -> None:
        """Disconnect from paper trading."""
        logger.info("Disconnecting paper trading engine")
        self._running = False
        self._connected = False

    async def health_check(self) -> bool:
        return self._connected and self._running

    def update_order_book(self, book: OrderBookSnapshot) -> None:
        """Update order book snapshot."""
        self._current_books[book.symbol] = book

    async def submit_order(self, order: OrderRequest) -> ExecutionResult:
        """Submit paper trade."""
        if not self._connected:
            return self._reject(order, "Engine not connected", "NOT_CONNECTED")

        start_time = time.time()

        book = self._current_books.get(order.symbol)
        if book is None:
            return self._reject(order, "No market data available", "NO_DATA")

        latency = self.latency_base_ms + random.randint(0, self.latency_jitter_ms)
        await asyncio.sleep(latency / 1000.0)

        fill_result = self._simulate_paper_fill(order, book)

        if fill_result["filled_qty"] == 0:
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
                    reason="Insufficient liquidity"
                ),
                latency_ms=(time.time() - start_time) * 1000
            )

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
            latency_ms=latency
        )

        self._emit_fill(fill_event)

        return ExecutionResult(
            success=True,
            order_id=order.order_id,
            trace_id=order.trace_id,
            symbol=order.symbol,
            status=OrderStatus.FILLED if fill_result["filled_qty"] == order.quantity else OrderStatus.PARTIAL,
            filled_quantity=fill_result["filled_qty"],
            avg_fill_price=fill_result["avg_price"],
            total_cost=fill_result["total_cost"],
            slippage=fill_result["slippage"],
            fee=fill_result["fee"],
            fill_events=[fill_event],
            latency_ms=(time.time() - start_time) * 1000
        )

    def _simulate_paper_fill(
        self,
        order: OrderRequest,
        book: OrderBookSnapshot
    ) -> dict:
        """Simulate realistic paper fill."""
        if order.side == Side.BUY:
            levels = book.asks
            best_price = book.best_ask
        else:
            levels = book.bids
            best_price = book.best_bid

        total_depth = sum(size for _, size in levels[:10])
        if total_depth < EPS:
            return self._empty_fill_result()

        queue_position = random.randint(1, max(1, int(order.quantity / 0.01)))
        fill_probability = 1.0 / (queue_position + 1)

        if random.random() > fill_probability:
            partial_qty = order.quantity * fill_probability * random.uniform(0.5, 1.0)
        else:
            partial_qty = order.quantity

        fill_ratio = min(partial_qty / (total_depth + EPS), 1.0)
        filled_qty = order.quantity * fill_ratio

        slippage_per_unit = 0.0
        if total_depth > 0:
            slippage_per_unit = (
                self.slippage_alpha *
                self.slippage_multiplier *
                (order.quantity / total_depth) *
                (book.spread / 2 + 1)
            )

        avg_price = best_price + slippage_per_unit if order.side == Side.BUY else best_price - slippage_per_unit

        slippage_total = slippage_per_unit * filled_qty
        fee = filled_qty * avg_price * self.taker_fee
        total_cost = slippage_total + fee

        return {
            "filled_qty": filled_qty,
            "avg_price": avg_price,
            "slippage": slippage_total,
            "fee": fee,
            "total_cost": total_cost
        }

    def _empty_fill_result(self) -> dict:
        return {
            "filled_qty": 0,
            "avg_price": 0,
            "slippage": 0,
            "fee": 0,
            "total_cost": 0
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
                if new_qty > 0:
                    pos.entry_price = (pos.entry_price * pos.quantity + price * qty) / new_qty
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
                if new_qty != 0:
                    pos.entry_price = (abs(pos.entry_price * pos.quantity) + price * qty) / abs(new_qty)
                pos.quantity = new_qty
            else:
                if qty <= pos.quantity:
                    pos.quantity -= qty
                else:
                    pos.quantity -= qty
                    pos.entry_price = price

        pos.last_update = int(time.time() * 1000)

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel order."""
        logger.info(f"Paper cancel requested for {order_id}")
        return True

    async def get_position(self, symbol: str) -> Position:
        """Get position."""
        if symbol not in self._positions:
            self._positions[symbol] = Position(symbol=symbol)
        return self._positions[symbol]

    async def get_open_orders(self, symbol: Optional[str] = None) -> list[Order]:
        """Get open orders."""
        return []

    def _reject(
        self,
        order: OrderRequest,
        reason: str,
        error_code: str
    ) -> ExecutionResult:
        """Create rejection."""
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
