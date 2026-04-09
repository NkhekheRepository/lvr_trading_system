"""
Position sizing based on risk parameters.

This module implements position sizing algorithms that calculate
appropriate position sizes based on risk parameters, current market
conditions, and portfolio state. The goal is to achieve consistent
risk per trade while maximizing capital efficiency.

Core Formula:
    size = risk_amount / (leverage × volatility × price)
    
    risk_amount = capital × base_risk × confidence × multipliers

Risk Adjustments:
    - Confidence multiplier: Higher confidence = larger size
    - Drawdown multiplier: Reduce risk during drawdowns
    - Loss streak multiplier: Reduce risk after consecutive losses
    - Volatility scaling: Inverse volatility weighting

Example:
    >>> from risk.sizing import PositionSizer, AdaptivePositionSizer
    >>> from app.schemas import Portfolio, RiskState, Signal, Direction
    >>>
    >>> sizer = PositionSizer(base_risk_per_trade=0.01, max_leverage=10.0)
    >>> 
    >>> signal = Signal(direction=Direction.LONG, confidence=0.8, ...)
    >>> size = sizer.calculate_size(signal, portfolio, risk_state, current_price=50000)
    >>> print(f"Position size: {size:.4f}")
    Position size: 0.1250
    
    >>> # Calculate stop loss and take profit
    >>> stop = sizer.calculate_stop_loss(entry=50000, signal=signal)
    >>> profit = sizer.calculate_take_profit(entry=50000, signal=signal)
"""

import logging
import numpy as np

from app.schemas import Portfolio, RiskState, Signal

logger = logging.getLogger(__name__)

EPS = 1e-10


class PositionSizer:
    """
    Calculates position size based on risk parameters.
    
    Implements Kelly-inspired position sizing with volatility adjustment.
    The core formula calculates size as a fraction of capital that
    risks a fixed percentage per trade.
    
    Mathematical Foundation:
        Position Size = risk_amount / (leverage × σ × price)
        
        where:
        - risk_amount = C × r × confidence × multipliers
        - C = current capital
        - r = base_risk_per_trade (default 1%)
        - σ = realized volatility
        - multipliers = drawdown_adj × loss_streak_adj
    
    Risk Adjustments Applied:
        1. Consecutive Losses: size *= max(0.5, 1 - losses × 0.1)
        2. Drawdown: size *= max(0.5, 1 - drawdown × 2)
        3. Max position cap: min(size, 20% of capital)
    
    Attributes:
        base_risk_per_trade: Base risk per trade as fraction of capital.
        max_leverage: Maximum leverage allowed.
        min_position: Minimum position size (floor).
    
    Example:
        >>> sizer = PositionSizer(base_risk_per_trade=0.02, max_leverage=5.0)
        >>> size = sizer.calculate_size(signal, portfolio, risk_state, 50000)
    """

    def __init__(
        self,
        base_risk_per_trade: float = 0.01,
        max_leverage: float = 10.0,
        min_position: float = 0.001
    ):
        """
        Initialize position sizer.
        
        Args:
            base_risk_per_trade: Risk per trade as fraction (0.01 = 1%).
            max_leverage: Maximum leverage multiplier.
            min_position: Minimum position size floor.
        """
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
        
        Computes the optimal position size based on:
        - Base risk parameter
        - Signal confidence
        - Current volatility
        - Portfolio risk state (drawdown, consecutive losses)
        
        Args:
            signal: Trading signal with direction and confidence.
            portfolio: Current portfolio state (capital, positions).
            risk_state: Current risk state (consecutive losses, drawdown).
            current_price: Current market price for the symbol.
            volatility: Price volatility (optional, uses signal.features if None).
            
        Returns:
            Position size (quantity) to trade.
        
        Mathematical Formula:
            base_risk = portfolio.current_capital × base_risk_per_trade × confidence
            risk_amount = base_risk × loss_mult × drawdown_mult
            leverage = min(max_leverage, risk_state.current_leverage + 1)
            size = risk_amount / (leverage × volatility × current_price)
            size = max(size, min_position)
            size = min(size, 20% × capital / current_price)
        
        Example:
            >>> size = sizer.calculate_size(
            ...     signal=signal,
            ...     portfolio=portfolio,
            ...     risk_state=risk_state,
            ...     current_price=50000.0,
            ...     volatility=0.0015
            ... )
            >>> print(f"Trade {signal.direction.value}: {size:.4f} contracts")
            Trade long: 0.1250 contracts
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
        """
        Calculate stop loss price.
        
        Uses volatility-based stops where the stop distance is
        proportional to the current volatility.
        
        Args:
            entry_price: Trade entry price.
            signal: Trading signal for direction.
            volatility: Volatility to use (optional).
            atr_multiplier: Multiplier for volatility-based distance.
        
        Returns:
            Stop loss price level.
        
        Mathematical Formula:
            stop_distance = entry_price × volatility × atr_multiplier
            
            LONG: stop = entry - distance
            SHORT: stop = entry + distance
        """
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
        """
        Calculate take profit price based on risk-reward ratio.
        
        Args:
            entry_price: Trade entry price.
            signal: Trading signal for direction.
            risk_reward_ratio: Desired reward-to-risk ratio (default 2.0).
            stop_loss: Stop loss price (computed if None).
        
        Returns:
            Take profit price level.
        
        Mathematical Formula:
            risk = |entry - stop_loss|
            reward = risk × risk_reward_ratio
            
            LONG: tp = entry + reward
            SHORT: tp = entry - reward
        
        Example:
            >>> tp = sizer.calculate_take_profit(
            ...     entry_price=50000,
            ...     signal=signal,
            ...     risk_reward_ratio=2.0,
            ...     stop_loss=49500
            ... )
            >>> print(f"Target: {tp}")
            Target: 51000
        """
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
    
    Extends PositionSizer with adaptive risk multiplier that responds
    to recent trading performance. When recent returns are positive,
    the risk multiplier increases (within bounds); when negative,
    it decreases.
    
    Adaptation Rules:
        - Average 5-trade return < 0: multiplier *= (1 - rate)
        - Average 5-trade return > 1%: multiplier *= (1 + rate × 0.5)
        - Multiplier clamped to [0.3, 1.5]
    
    Attributes:
        adaptation_rate: How fast the multiplier adjusts (default 0.1).
        _current_risk_multiplier: Current risk scaling factor.
        _recent_returns: Rolling history of trade returns.
    
    Example:
        >>> sizer = AdaptivePositionSizer(adaptation_rate=0.1)
        >>> 
        >>> # After several trades
        >>> sizer.record_return(0.02)  # 2% win
        >>> sizer.record_return(-0.01)  # 1% loss
        >>> 
        >>> info = sizer.get_adaptation_info()
        >>> print(f"Multiplier: {info['current_multiplier']:.2f}")
        Multiplier: 0.95
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
        """
        Record trade return for adaptation.
        
        Maintains rolling history and updates risk multiplier based
        on recent performance.
        
        Args:
            return_pct: Trade return as decimal (0.02 = 2% profit).
        
        Side Effects:
            Updates _recent_returns and _current_risk_multiplier.
        
        Adaptation Logic:
            if avg_last_5 < 0: multiplier *= (1 - rate)
            if avg_last_5 > 0.01: multiplier *= (1 + rate × 0.5)
            clamp(multiplier, 0.3, 1.5)
        """
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
        """
        Calculate size with adaptation.
        
        Extends parent calculation with adaptive risk multiplier.
        
        Args:
            See PositionSizer.calculate_size().
        
        Returns:
            Base size × current_risk_multiplier.
        """
        base_size = super().calculate_size(
            signal, portfolio, risk_state, current_price, volatility
        )
        return base_size * self._current_risk_multiplier

    def get_adaptation_info(self) -> dict:
        """
        Get adaptation information.
        
        Returns:
            Dictionary with:
            - current_multiplier: Current risk scaling factor.
            - recent_returns: Last 5 trade returns.
            - avg_recent_return: Average of last 5 returns.
        """
        return {
            "current_multiplier": self._current_risk_multiplier,
            "recent_returns": self._recent_returns[-5:] if self._recent_returns else [],
            "avg_recent_return": np.mean(self._recent_returns[-5:]) if len(self._recent_returns) >= 5 else 0
        }
