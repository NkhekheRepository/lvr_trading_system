"""
Position sizing based on risk parameters.
"""

import logging
import numpy as np

from app.schemas import Portfolio, RiskState, Signal

logger = logging.getLogger(__name__)

EPS = 1e-10


class PositionSizer:
    """
    Calculates position size based on risk parameters.
    
    Formula: size = risk / (leverage * volatility)
    """

    def __init__(
        self,
        base_risk_per_trade: float = 0.01,
        max_leverage: float = 10.0,
        min_position: float = 0.001
    ):
        self.base_risk_per_trade = base_risk_per_trade
        self.max_leverage = max_leverage
        self.min_position = min_position

    def calculate_size(
        self,
        signal: Signal,
        portfolio: Portfolio,
        risk_state: RiskState,
        current_price: float,
        volatility: float = None
    ) -> float:
        """
        Calculate position size for signal.
        
        Args:
            signal: Trading signal with direction and confidence
            portfolio: Current portfolio state
            risk_state: Current risk state
            current_price: Current market price
            volatility: Price volatility (optional)
            
        Returns:
            Position size (quantity)
        """
        if volatility is None or volatility <= EPS:
            volatility = signal.features.volatility if signal.features else 0.001

        risk_amount = portfolio.current_capital * self.base_risk_per_trade * signal.confidence

        if risk_state.consecutive_losses > 0:
            loss_multiplier = max(0.5, 1 - risk_state.consecutive_losses * 0.1)
            risk_amount *= loss_multiplier

        if risk_state.current_drawdown > 0.05:
            drawdown_multiplier = max(0.5, 1 - risk_state.current_drawdown * 2)
            risk_amount *= drawdown_multiplier

        leverage = min(self.max_leverage, risk_state.current_leverage + 1)

        if volatility > EPS:
            size = risk_amount / (leverage * volatility * current_price)
        else:
            size = risk_amount / current_price

        size = max(size, self.min_position)

        max_position_value = portfolio.current_capital * 0.2
        max_size = max_position_value / current_price
        size = min(size, max_size)

        logger.debug(
            f"Size calc: risk={risk_amount:.2f}, vol={volatility:.4f}, "
            f"leverage={leverage:.1f}, size={size:.4f}"
        )

        return size

    def calculate_stop_loss(
        self,
        entry_price: float,
        signal: Signal,
        volatility: float = None,
        atr_multiplier: float = 2.0
    ) -> float:
        """Calculate stop loss price."""
        if volatility is None:
            volatility = signal.features.volatility if signal.features else 0.001

        if signal.direction.value == "buy":
            stop_distance = entry_price * volatility * atr_multiplier
            return entry_price - stop_distance
        else:
            stop_distance = entry_price * volatility * atr_multiplier
            return entry_price + stop_distance

    def calculate_take_profit(
        self,
        entry_price: float,
        signal: Signal,
        risk_reward_ratio: float = 2.0,
        stop_loss: float = None
    ) -> float:
        """Calculate take profit price."""
        if stop_loss is None:
            stop_loss = self.calculate_stop_loss(entry_price, signal)

        risk = abs(entry_price - stop_loss)
        reward = risk * risk_reward_ratio

        if signal.direction.value == "buy":
            return entry_price + reward
        else:
            return entry_price - reward


class AdaptivePositionSizer(PositionSizer):
    """
    Position sizer that adapts based on recent performance.
    """

    def __init__(
        self,
        base_risk_per_trade: float = 0.01,
        max_leverage: float = 10.0,
        min_position: float = 0.001,
        adaptation_rate: float = 0.1
    ):
        super().__init__(base_risk_per_trade, max_leverage, min_position)
        self.adaptation_rate = adaptation_rate
        self._recent_returns = []
        self._current_risk_multiplier = 1.0

    def record_return(self, return_pct: float) -> None:
        """Record trade return for adaptation."""
        self._recent_returns.append(return_pct)
        if len(self._recent_returns) > 20:
            self._recent_returns = self._recent_returns[-20:]

        if len(self._recent_returns) >= 5:
            avg_return = np.mean(self._recent_returns[-5:])
            if avg_return < 0:
                self._current_risk_multiplier *= (1 - self.adaptation_rate)
            elif avg_return > 0.01:
                self._current_risk_multiplier *= (1 + self.adaptation_rate * 0.5)

            self._current_risk_multiplier = np.clip(self._current_risk_multiplier, 0.3, 1.5)

    def calculate_size(
        self,
        signal: Signal,
        portfolio: Portfolio,
        risk_state: RiskState,
        current_price: float,
        volatility: float = None
    ) -> float:
        """Calculate size with adaptation."""
        base_size = super().calculate_size(
            signal, portfolio, risk_state, current_price, volatility
        )
        return base_size * self._current_risk_multiplier

    def get_adaptation_info(self) -> dict:
        """Get adaptation information."""
        return {
            "current_multiplier": self._current_risk_multiplier,
            "recent_returns": self._recent_returns[-5:] if self._recent_returns else [],
            "avg_recent_return": np.mean(self._recent_returns[-5:]) if len(self._recent_returns) >= 5 else 0
        }
