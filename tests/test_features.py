"""
Tests for feature engine stability and correctness.
"""

import pytest
import numpy as np

from app.schemas import OrderBookSnapshot, Side, TradeTick
from features import FeatureEngine


class TestFeatureEngine:
    """Test feature engine."""

    @pytest.fixture
    def engine(self):
        return FeatureEngine(
            return_window=50,
            volatility_window=100,
            depth_window=100,
            spread_window=100
        )

    @pytest.fixture
    def sample_order_book(self):
        return OrderBookSnapshot(
            timestamp=1609459200000,
            symbol="BTCUSDT",
            bids=[(50000.0, 1.0), (49999.0, 2.0), (49998.0, 3.0)],
            asks=[(50001.0, 1.0), (50002.0, 2.0), (50003.0, 3.0)]
        )

    def test_no_nans_on_basic_tick(self, engine, sample_order_book):
        """Features should never contain NaN."""
        for i in range(200):
            tick = TradeTick(
                timestamp=1609459200000 + i * 100,
                symbol="BTCUSDT",
                price=50000 + np.random.randn() * 10,
                size=0.1,
                side=Side.BUY if i % 2 == 0 else Side.SELL
            )
            features = engine.update(tick, sample_order_book)
            assert not features.has_nans(), f"NaN at tick {i}"

    def test_no_nans_on_extreme_values(self, engine):
        """Features should handle extreme values."""
        for price in [0.0001, 0.01, 100, 10000, 1000000]:
            tick = TradeTick(
                timestamp=1609459200000,
                symbol="BTCUSDT",
                price=price,
                size=0.001,
                side=Side.BUY
            )
            features = engine.update(tick, None)
            assert not features.has_nans()

    def test_deterministic_output(self, engine):
        """Same input should produce same output."""
        tick = TradeTick(
            timestamp=1609459200000,
            symbol="BTCUSDT",
            price=50000.0,
            size=1.0,
            side=Side.BUY
        )

        features1 = engine.update(tick, None)
        engine.reset("BTCUSDT")
        features2 = engine.update(tick, None)

        assert features1.I_star == features2.I_star

    def test_zscore_bounds(self, engine, sample_order_book):
        """Z-scores should be reasonably bounded."""
        for i in range(150):
            tick = TradeTick(
                timestamp=1609459200000 + i * 100,
                symbol="BTCUSDT",
                price=50000 + np.random.randn() * 100,
                size=0.1,
                side=Side.BUY
            )
            features = engine.update(tick, sample_order_book)

            assert -10 <= features.I_star <= 10
            assert -10 <= features.L_star <= 10
            assert -10 <= features.S_star <= 10

    def test_ofi_bounds(self, engine, sample_order_book):
        """OFI should be between -1 and 1."""
        for i in range(100):
            tick = TradeTick(
                timestamp=1609459200000 + i * 100,
                symbol="BTCUSDT",
                price=50000 + np.random.randn() * 10,
                size=1.0,
                side=Side.BUY
            )
            features = engine.update(tick, sample_order_book)
            assert -1 <= features.OFI <= 1

    def test_depth_imbalance_bounds(self, engine, sample_order_book):
        """Depth imbalance should be between -1 and 1."""
        for i in range(100):
            tick = TradeTick(
                timestamp=1609459200000 + i * 100,
                symbol="BTCUSDT",
                price=50000,
                size=1.0,
                side=Side.BUY
            )
            features = engine.update(tick, sample_order_book)
            assert -1 <= features.depth_imbalance <= 1
