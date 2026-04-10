"""
Tests for risk engine.
"""

import pytest

from app.schemas import OrderRequest, OrderType, Portfolio, RiskState, Side, Signal
from risk import RiskEngine, RiskLimits, PositionSizer


class TestRiskEngine:
    """Test risk engine."""

    @pytest.fixture
    def engine(self):
        return RiskEngine(RiskLimits(
            max_leverage=10,
            max_drawdown_pct=0.10,
            max_daily_loss_pct=0.03
        ))

    @pytest.fixture
    def portfolio(self):
        return Portfolio(
            initial_capital=100000,
            current_capital=100000,
            available_capital=100000
        )

    def test_normal_order_approved(self, engine, portfolio):
        """Normal order should be approved."""
        order = OrderRequest(
            trace_id="test",
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=0.1,
            price=50000
        )

        risk_state = RiskState()
        result = engine.check_order(order, None, portfolio, risk_state)

        assert result.approved is True

    def test_high_leverage_rejected(self, engine, portfolio):
        """High leverage should be rejected."""
        portfolio.current_capital = 10000

        order = OrderRequest(
            trace_id="test",
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=2.0,
            price=50000
        )

        risk_state = RiskState(current_leverage=15)
        result = engine.check_order(order, None, portfolio, risk_state)

        assert result.approved is False


class TestPositionSizer:
    """Test position sizing."""

    def test_size_calculation(self):
        """Should calculate reasonable size."""
        sizer = PositionSizer(
            base_risk_per_trade=0.01,
            max_leverage=10
        )

        from app.schemas import Signal, FeatureVector

        portfolio = Portfolio(
            initial_capital=100000,
            current_capital=100000,
            available_capital=100000
        )

        signal = Signal(
            symbol="BTCUSDT",
            direction=Side.BUY,
            strength=0.7,
            confidence=0.6
        )

        risk_state = RiskState()

        size = sizer.calculate_size(
            signal=signal,
            portfolio=portfolio,
            risk_state=risk_state,
            current_price=50000,
            volatility=0.002
        )

        assert size > 0
        assert size < 10

    def test_stop_loss_calculation(self):
        """Should calculate stop loss."""
        sizer = PositionSizer()

        signal = Signal(
            symbol="BTCUSDT",
            direction=Side.BUY,
            strength=0.5,
            confidence=0.5
        )

        stop = sizer.calculate_stop_loss(50000, signal, volatility=0.002)
        assert 0 < stop < 50000

        signal.direction = Side.SELL
        stop = sizer.calculate_stop_loss(50000, signal, volatility=0.002)
        assert stop > 50000
