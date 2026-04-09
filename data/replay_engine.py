"""
Tick-by-tick backtest replay engine with synchronized execution simulation.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from app.schemas import OrderBookSnapshot, Side, TradeTick

logger = logging.getLogger(__name__)


@dataclass
class ReplayState:
    """Current replay state."""
    tick_index: int = 0
    total_ticks: int = 0
    start_time: Optional[int] = None
    current_tick: Optional[TradeTick] = None
    paused: bool = False
    speed_multiplier: float = 1.0


class ReplayEngine:
    """
    Tick-by-tick replay engine for backtesting.
    
    Synchronizes trade data with order book snapshots and execution simulation.
    """

    def __init__(
        self,
        ticks: list[TradeTick],
        order_books: Optional[list[OrderBookSnapshot]] = None,
        speed_multiplier: float = 1.0
    ):
        self.ticks = ticks
        self.order_books = order_books or []
        self.state = ReplayState(total_ticks=len(ticks))
        self.speed_multiplier = speed_multiplier

        self._tick_callbacks: list[Callable[[TradeTick], None]] = []
        self._book_callbacks: list[Callable[[OrderBookSnapshot], None]] = []
        self._cycle_callbacks: list[Callable[[int, TradeTick], None]] = []

    def on_tick(self, callback: Callable[[TradeTick], None]) -> None:
        """Register tick callback."""
        self._tick_callbacks.append(callback)

    def on_order_book(self, callback: Callable[[OrderBookSnapshot], None]) -> None:
        """Register order book callback."""
        self._book_callbacks.append(callback)

    def on_cycle(self, callback: Callable[[int, TradeTick], None]) -> None:
        """Register cycle callback (called after each tick processing)."""
        self._cycle_callbacks.append(callback)

    async def run(self) -> None:
        """Run replay loop."""
        logger.info(f"Starting replay with {len(self.ticks)} ticks")
        self.state.start_time = int(time.time() * 1000)

        try:
            for i, tick in enumerate(self.ticks):
                if self.state.paused:
                    await self._wait_for_unpause()

                self.state.tick_index = i
                self.state.current_tick = tick

                for callback in self._tick_callbacks:
                    callback(tick)

                book = self._get_order_book_at_tick(tick.timestamp)
                if book:
                    for callback in self._book_callbacks:
                        callback(book)

                for callback in self._cycle_callbacks:
                    callback(i, tick)

                await self._throttle(tick)

        except Exception as e:
            logger.error(f"Replay error at tick {self.state.tick_index}: {e}")
            raise

    def _get_order_book_at_tick(self, timestamp: int) -> Optional[OrderBookSnapshot]:
        """Get nearest order book snapshot for timestamp."""
        if not self.order_books:
            return None

        for book in self.order_books:
            if book.timestamp <= timestamp:
                return book

        return self.order_books[-1] if self.order_books else None

    async def _throttle(self, tick: TradeTick) -> None:
        """Throttle replay speed."""
        if self.speed_multiplier <= 0:
            return

        base_interval = 0.001
        target_interval = base_interval / self.speed_multiplier

        if self.state.tick_index < len(self.ticks) - 1:
            next_tick = self.ticks[self.state.tick_index + 1]
            actual_interval = (next_tick.timestamp - tick.timestamp) / 1000.0
            wait_time = min(actual_interval, target_interval)
            if wait_time > 0:
                await asyncio.sleep(wait_time)

    async def _wait_for_unpause(self) -> None:
        """Wait for replay to be unpaused."""
        while self.state.paused:
            await asyncio.sleep(0.1)

    def pause(self) -> None:
        """Pause replay."""
        self.state.paused = True
        logger.info("Replay paused")

    def resume(self) -> None:
        """Resume replay."""
        self.state.paused = False
        logger.info("Replay resumed")

    def seek(self, tick_index: int) -> None:
        """Seek to specific tick index."""
        if 0 <= tick_index < len(self.ticks):
            self.state.tick_index = tick_index
            self.state.current_tick = self.ticks[tick_index]
            logger.info(f"Seeked to tick {tick_index}")

    def get_progress(self) -> dict:
        """Get replay progress."""
        return {
            "current": self.state.tick_index,
            "total": self.state.total_ticks,
            "pct": (self.state.tick_index / self.state.total_ticks * 100) 
                   if self.state.total_ticks > 0 else 0,
            "paused": self.state.paused
        }


class SyncReplayEngine:
    """Synchronous version of ReplayEngine for backtesting."""

    def __init__(
        self,
        ticks: list[TradeTick],
        order_books: Optional[list[OrderBookSnapshot]] = None
    ):
        self.ticks = ticks
        self.order_books = order_books or []
        self.state = ReplayState(total_ticks=len(ticks))

        self._tick_callbacks: list[Callable[[TradeTick], None]] = []
        self._book_callbacks: list[Callable[[OrderBookSnapshot], None]] = []
        self._cycle_callbacks: list[Callable[[int, TradeTick], None]] = []

    def on_tick(self, callback: Callable[[TradeTick], None]) -> None:
        self._tick_callbacks.append(callback)

    def on_order_book(self, callback: Callable[[OrderBookSnapshot], None]) -> None:
        self._book_callbacks.append(callback)

    def on_cycle(self, callback: Callable[[int, TradeTick], None]) -> None:
        self._cycle_callbacks.append(callback)

    def run(self) -> None:
        """Run synchronous replay."""
        logger.info(f"Starting sync replay with {len(self.ticks)} ticks")

        for i, tick in enumerate(self.ticks):
            self.state.tick_index = i
            self.state.current_tick = tick

            for callback in self._tick_callbacks:
                callback(tick)

            book = self._get_order_book_at_tick(tick.timestamp)
            if book:
                for callback in self._book_callbacks:
                    callback(book)

            for callback in self._cycle_callbacks:
                callback(i, tick)

    def _get_order_book_at_tick(self, timestamp: int) -> Optional[OrderBookSnapshot]:
        if not self.order_books:
            return None
        for book in self.order_books:
            if book.timestamp <= timestamp:
                return book
        return self.order_books[-1] if self.order_books else None
