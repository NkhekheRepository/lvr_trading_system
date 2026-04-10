"""
Tests for execution engine.
"""

import pytest
import asyncio

from app.schemas import OrderRequest, OrderType, Side
from execution import SimulatedExecutionEngine, FillModel, CostModel


class TestSimulatedExecution:
    """Test simulated execution."""

    @pytest.fixture
    def engine(self):
        engine = SimulatedExecutionEngine(
            slippage_alpha=0.5,
            latency_ms=50,
            zero_slippage=False
        )
        return engine

    @pytest.mark.asyncio
    async def test_basic_order(self, engine):
        """Should execute basic order."""
        await engine.connect()

        order = OrderRequest(
            trace_id="test-123",
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
            price=50000
        )

        result = await engine.submit_order(order)

        assert result.success is True
        assert result.filled_quantity > 0
        assert result.avg_fill_price > 0
        assert result.slippage >= 0
        assert result.fee >= 0

        await engine.disconnect()

    @pytest.mark.asyncio
    async def test_slippage_bounds(self, engine):
        """Slippage should be within expected bounds."""
        await engine.connect()

        slippage_total = 0
        n_orders = 10

        for i in range(n_orders):
            order = OrderRequest(
                trace_id=f"test-{i}",
                symbol="BTCUSDT",
                side=Side.BUY if i % 2 == 0 else Side.SELL,
                order_type=OrderType.MARKET,
                quantity=0.1,
                price=50000 + i
            )
            result = await engine.submit_order(order)
            slippage_total += result.slippage

        avg_slippage = slippage_total / n_orders
        assert avg_slippage >= 0
        assert avg_slippage < 100

        await engine.disconnect()


class TestFillModel:
    """Test fill probability model."""

    def test_fill_probability_bounds(self):
        """Fill probability should be between 0 and 1."""
        model = FillModel(base_flow_rate=0.5)

        for queue in range(10):
            for size in [0.01, 0.1, 1.0, 10.0]:
                prob = model.compute_fill_probability(
                    queue_ahead=queue,
                    order_size=size,
                    market_depth=10.0
                )
                assert 0 <= prob <= 1


class TestCostModel:
    """Test cost model."""

    def test_total_cost_calculation(self):
        """Should calculate all cost components."""
        model = CostModel(
            maker_fee=0.0002,
            taker_fee=0.0004,
            slippage_alpha=0.5
        )

        costs = model.calculate_total_cost(
            quantity=1.0,
            price=50000,
            side="buy",
            spread=1.0,
            market_depth=100.0,
            latency_ms=100
        )

        assert costs["spread_cost"] > 0
        assert costs["slippage_cost"] >= 0
        assert costs["fee_cost"] > 0
        assert costs["total_cost"] > 0
        assert costs["total_cost"] == (
            costs["spread_cost"] + costs["slippage_cost"] +
            costs["fee_cost"] + costs["latency_cost"]
        )

    def test_cost_in_basis_points(self):
        """Cost should be reasonable in bps."""
        model = CostModel()

        costs = model.calculate_total_cost(
            quantity=1.0,
            price=50000,
            side="buy",
            spread=1.0,
            market_depth=100.0
        )

        assert costs["total_cost_bps"] < 50
