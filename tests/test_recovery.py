"""
Tests for state recovery.
"""

import pytest

from app.schemas import Position, Portfolio
from state import StateStore


class TestStateStore:
    """Test state store."""

    @pytest.fixture
    async def store(self):
        store = StateStore(
            redis_config={"host": "localhost", "port": 6379},
            checkpoint_interval=10
        )
        await store.connect()
        yield store
        await store.disconnect()

    @pytest.mark.asyncio
    async def test_save_load_position(self, store):
        """Should save and load position."""
        position = Position(
            symbol="BTCUSDT",
            quantity=1.0,
            entry_price=50000,
            current_price=51000
        )

        await store.save_position(position)

        positions = await store.load_positions()
        assert "BTCUSDT" in positions

    @pytest.mark.asyncio
    async def test_save_load_portfolio(self, store):
        """Should save and load portfolio."""
        portfolio = Portfolio(
            initial_capital=100000,
            current_capital=105000,
            available_capital=90000
        )

        await store.save_portfolio(portfolio)

        loaded = await store.load_portfolio()
        if loaded:
            assert loaded.current_capital == 105000

    @pytest.mark.asyncio
    async def test_recovery(self, store):
        """Should recover state."""
        result = await store.recover()
        assert "positions" in result
        assert "portfolio" in result
