"""
Tick data loader with strict schema validation and missing data handling.
"""

import logging
from pathlib import Path
from typing import Iterator, Optional

import numpy as np
import pandas as pd

from app.schemas import OrderBookSnapshot, Side, TradeTick

logger = logging.getLogger(__name__)


class DataLoader:
    """Loads and validates tick data from various sources."""

    MAX_GAP_TICKS = 5
    REQUIRED_COLUMNS = ["timestamp", "price", "size", "side"]

    def __init__(self, max_gap_ticks: int = MAX_GAP_TICKS):
        self.max_gap_ticks = max_gap_ticks
        self._loaded_symbols: set[str] = set()

    def load_parquet(self, path: str, symbol: str) -> Iterator[TradeTick]:
        """Load tick data from parquet file."""
        df = pd.read_parquet(path)
        return self._df_to_ticks(df, symbol)

    def load_csv(self, path: str, symbol: str) -> Iterator[TradeTick]:
        """Load tick data from CSV file."""
        df = pd.read_csv(path)
        self._validate_columns(df.columns.tolist())
        return self._df_to_ticks(df, symbol)

    def _validate_columns(self, columns: list[str]) -> None:
        """Validate required columns exist."""
        missing = set(self.REQUIRED_COLUMNS) - set(columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

    def _df_to_ticks(self, df: pd.DataFrame, symbol: str) -> Iterator[TradeTick]:
        """Convert DataFrame rows to TradeTick objects with gap handling."""
        df = df.sort_values("timestamp").reset_index(drop=True)

        last_price = None
        last_ts = None

        for idx, row in df.iterrows():
            ts = int(row["timestamp"])
            if ts < 1000000000000:
                ts *= 1000

            price = float(row["price"])
            size = float(row["size"])
            side_str = str(row["side"]).lower()
            side = Side.BUY if side_str in ("buy", "b", "1") else Side.SELL

            if last_price is not None and last_ts is not None:
                gap = (ts - last_ts) / 1000.0
                if gap > self.max_gap_ticks * 0.1:
                    logger.warning(
                        f"Data gap detected: {gap:.2f}s at index {idx}",
                        extra={"gap_ticks_approx": int(gap / 0.1)}
                    )

            tick = TradeTick(
                timestamp=ts,
                symbol=symbol,
                price=price,
                size=size,
                side=side
            )

            if tick.has_nans():
                logger.warning(f"NaN detected in tick at index {idx}, skipping")
                continue

            last_price = price
            last_ts = ts
            self._loaded_symbols.add(symbol)

            yield tick

    def validate_sequence(self, ticks: list[TradeTick]) -> bool:
        """Validate ticks are time-sorted and have no duplicates."""
        if not ticks:
            return True

        for i in range(1, len(ticks)):
            if ticks[i].timestamp < ticks[i-1].timestamp:
                logger.error(f"Ticks not sorted at index {i}")
                return False
            if ticks[i].timestamp == ticks[i-1].timestamp:
                logger.warning(f"Duplicate timestamp at index {i}")
        return True


class OrderBookLoader:
    """Loads order book snapshots."""

    REQUIRED_LEVELS = ["price", "size"]

    def load_snapshot(
        self, 
        bids: list[dict], 
        asks: list[dict], 
        timestamp: int,
        symbol: str
    ) -> OrderBookSnapshot:
        """Create order book snapshot from level data."""
        bid_levels = [(float(b["price"]), float(b["size"])) for b in bids]
        ask_levels = [(float(a["price"]), float(a["size"])) for a in asks]

        bid_levels.sort(key=lambda x: x[0], reverse=True)
        ask_levels.sort(key=lambda x: x[0])

        return OrderBookSnapshot(
            timestamp=timestamp,
            symbol=symbol,
            bids=bid_levels,
            asks=ask_levels
        )

    def from_trades_to_book(
        self,
        trades: list[TradeTick],
        depth_levels: int = 20
    ) -> OrderBookSnapshot:
        """Infer order book from trade sequence (for backtesting)."""
        if not trades:
            return OrderBookSnapshot(
                timestamp=0,
                symbol="",
                bids=[],
                asks=[]
            )

        prices = [t.price for t in trades]
        mid = np.median(prices)
        spread_pct = 0.0001

        best_bid = mid * (1 - spread_pct)
        best_ask = mid * (1 + spread_pct)

        tick_size = prices[-1] * 0.0001

        bids = []
        asks = []
        for i in range(depth_levels):
            depth = np.random.uniform(0.1, 2.0)
            bids.append((best_bid - i * tick_size, depth))
            asks.append((best_ask + i * tick_size, depth))

        return OrderBookSnapshot(
            timestamp=trades[-1].timestamp,
            symbol=trades[-1].symbol,
            bids=bids,
            asks=asks
        )
