"""
Sample data generator for testing and backtesting.
"""

import numpy as np
from typing import Optional

from app.schemas import OrderBookSnapshot, Side, TradeTick


class SampleDataGenerator:
    """Generates realistic sample market data."""

    def __init__(
        self,
        base_price: float = 50000.0,
        volatility: float = 0.001,
        tick_size: float = 0.1,
        lot_size: float = 0.001
    ):
        self.base_price = base_price
        self.volatility = volatility
        self.tick_size = tick_size
        self.lot_size = lot_size
        self._last_price = base_price

    def generate_tick(self, timestamp: int, symbol: str) -> TradeTick:
        """Generate a single realistic tick."""
        returns = np.random.normal(0, self.volatility)
        price = self._last_price * (1 + returns)
        price = round(price / self.tick_size) * self.tick_size

        size = np.random.exponential(self.lot_size * 10) * self.lot_size
        size = round(size / self.lot_size) * self.lot_size
        size = max(self.lot_size, size)

        side = Side.BUY if returns > 0 else Side.SELL

        self._last_price = price

        return TradeTick(
            timestamp=timestamp,
            symbol=symbol,
            price=price,
            size=size,
            side=side
        )

    def generate_ticks(
        self,
        n_ticks: int,
        symbol: str,
        start_timestamp: int,
        interval_ms: int = 100
    ) -> list[TradeTick]:
        """Generate sequence of ticks."""
        ticks = []
        for i in range(n_ticks):
            ts = start_timestamp + i * interval_ms
            tick = self.generate_tick(ts, symbol)
            ticks.append(tick)
        return ticks

    def generate_order_book(
        self,
        timestamp: int,
        symbol: str,
        levels: int = 20
    ) -> OrderBookSnapshot:
        """Generate order book snapshot."""
        mid = self._last_price
        spread_pct = 0.0001

        best_bid = mid * (1 - spread_pct)
        best_ask = mid * (1 + spread_pct)

        bids = []
        asks = []

        for i in range(levels):
            depth = np.random.uniform(0.01, 5.0)
            depth_bid = np.random.uniform(0.5, 2.0) * depth
            depth_ask = np.random.uniform(0.5, 2.0) * depth

            bids.append((round(best_bid - i * self.tick_size, 1), round(depth_bid, 4)))
            asks.append((round(best_ask + i * self.tick_size, 1), round(depth_ask, 4)))

        return OrderBookSnapshot(
            timestamp=timestamp,
            symbol=symbol,
            bids=bids,
            asks=asks
        )

    def generate_correlated_ticks(
        self,
        n_ticks: int,
        symbols: list[str],
        correlation: float = 0.7,
        start_timestamp: int = 1609459200000,
        interval_ms: int = 100
    ) -> dict[str, list[TradeTick]]:
        """Generate correlated ticks for multiple symbols."""
        if not symbols:
            return {}

        generators = {
            symbol: SampleDataGenerator(
                base_price=self.base_price if symbol == symbols[0] else self.base_price * 0.02,
                volatility=self.volatility
            )
            for symbol in symbols
        }

        common_shock = np.random.normal(0, 1, n_ticks)

        result = {}
        for symbol in symbols:
            ticks = []
            specific_shock = np.random.normal(0, 1 - correlation, n_ticks)
            
            for i in range(n_ticks):
                combined = correlation * common_shock[i] + (1 - correlation) * specific_shock[i]
                returns = combined * self.volatility
                
                price = generators[symbol]._last_price * (1 + returns)
                price = round(price / self.tick_size) * self.tick_size
                
                size = np.random.exponential(1.0) * self.lot_size
                size = max(self.lot_size, round(size / self.lot_size) * self.lot_size)
                
                side = Side.BUY if returns > 0 else Side.SELL
                
                tick = TradeTick(
                    timestamp=start_timestamp + i * interval_ms,
                    symbol=symbol,
                    price=price,
                    size=size,
                    side=side
                )
                ticks.append(tick)
                generators[symbol]._last_price = price
            
            result[symbol] = ticks

        return result


def generate_test_dataset(
    n_ticks: int = 10000,
    symbols: list[str] = None,
    start_ts: int = 1609459200000
) -> dict[str, list[TradeTick]]:
    """Generate complete test dataset."""
    if symbols is None:
        symbols = ["BTCUSDT", "ETHUSDT"]

    gen = SampleDataGenerator(base_price=50000.0, volatility=0.0005)
    data = {}

    for symbol in symbols:
        if symbol == "ETHUSDT":
            gen_eth = SampleDataGenerator(base_price=1800.0, volatility=0.0006)
            ticks = gen_eth.generate_ticks(n_ticks, symbol, start_ts)
        else:
            ticks = gen.generate_ticks(n_ticks, symbol, start_ts)
        data[symbol] = ticks

    return data
