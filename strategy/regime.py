"""
Market regime detection and filtering.

This module implements market regime detection algorithms to identify
adverse trading conditions and block execution accordingly. The regime
detector calculates a normalized return signal T = |r_t| / σ_t and compares
it against a configurable threshold to determine if market conditions are
suitable for trading.

Mathematical Foundation:
    T = |r_t| / σ_t

    Where:
    - r_t = realized return at time t
    - σ_t = realized volatility at time t

    Regime States:
    - T <= threshold: Normal regime (trading allowed)
    - T > threshold: High volatility regime (trading blocked)

The VolatilityRegimeDetector provides an alternative approach using percentile
rank of current volatility relative to a rolling window, which may be more
robust in trending markets.

Example:
    >>> from strategy.regime import RegimeDetector, VolatilityRegimeDetector
    >>> from app.schemas import FeatureVector, Signal
    >>>
    >>> # Initialize detector
    >>> detector = RegimeDetector(threshold=2.0)
    >>>
    >>> # Check regime from features
    >>> features = FeatureVector(...)
    >>> in_regime, T = detector.check_regime(features)
    >>> print(f"Regime check: in_trading_regime={in_regime}, T={T:.3f}")
    Regime check: in_trading_regime=True, T=1.234
    >>>
    >>> # Apply to signal
    >>> signal = Signal(...)
    >>> filtered_signal = detector.apply_to_signal(signal)
"""

import logging
from typing import Optional

from app.schemas import FeatureVector, Signal

logger = logging.getLogger(__name__)

EPS = 1e-10


class RegimeDetector:
    """
    Detects market regimes and blocks trading in adverse conditions.
    
    The regime detector uses a Signal-to-Noise Ratio (SNR) approach where
    the regime statistic T = |r_t| / σ_t measures the strength of the
    current price movement relative to normal volatility levels.
    
    When T exceeds the configured threshold, it indicates the market is in
    a high-volatility or trending regime where mean-reversion strategies
    may underperform. The detector maintains a rolling history of regime
    checks for statistics and logging purposes.
    
    Attributes:
        threshold: T value above which trading is blocked. Default is 2.0,
            meaning returns must exceed 2 standard deviations.
        _regime_history: Rolling history of (timestamp, T, in_regime) tuples.
    
    Mathematical Details:
        The statistic T is computed as:
        
            T_t = |r_t| / σ_t
        
        where:
        - r_t = price_return_t = (P_t - P_{t-1}) / P_{t-1}
        - σ_t = realized_volatility_t computed over rolling window
        
        The threshold comparison is:
        
            trading_allowed = (T_t <= threshold)
    
    Note:
        When volatility σ_t approaches zero, the detector returns in_trading_regime=True
        to avoid division by zero. This may allow trading in illiquid or static markets.
    
    See Also:
        VolatilityRegimeDetector: Alternative detector using percentile ranking.
        strategy.filters: Additional signal filtering mechanisms.
    """

    def __init__(self, threshold: float = 2.0):
        """
        Initialize regime detector.
        
        Args:
            threshold: T threshold for blocking trades. Higher values allow
                trading in more volatile conditions. Default 2.0.
        """
        self.threshold = threshold
        self._regime_history = []

    def check_regime(self, features: FeatureVector) -> tuple[bool, float]:
        """
        Check if market is in trading regime based on feature vector.
        
        Computes the regime statistic T = |returns| / volatility and compares
        against the configured threshold to determine if trading should be
        allowed in the current market conditions.
        
        Args:
            features: FeatureVector containing returns and volatility metrics.
                Required fields: returns, volatility, timestamp, symbol.
        
        Returns:
            Tuple of (in_trading_regime, regime_T) where:
            - in_trading_regime: True if trading is allowed, False if blocked
            - regime_T: The computed regime statistic value
        
        Algorithm:
            1. Handle edge case: if volatility <= EPS, return (True, 0.0)
            2. Compute T = abs(returns) / volatility
            3. Determine regime: T <= threshold means in regime
            4. Update rolling history (max 1000 entries, pruned to 500)
            5. Log warning if regime blocked
        
        Example:
            >>> features = FeatureVector(
            ...     symbol="BTCUSDT",
            ...     timestamp=1700000000000,
            ...     returns=0.0025,
            ...     volatility=0.0015
            ... )
            >>> in_regime, T = detector.check_regime(features)
            >>> print(f"T={T:.2f}, trading_allowed={in_regime}")
            T=1.67, trading_allowed=True
        """
        if features.volatility <= EPS:
            return True, 0.0

        T = abs(features.returns) / features.volatility

        in_regime = T <= self.threshold

        self._regime_history.append((features.timestamp, T, in_regime))
        if len(self._regime_history) > 1000:
            self._regime_history = self._regime_history[-500:]

        if not in_regime:
            logger.info(
                f"Regime blocked for {features.symbol}: T={T:.2f} > {self.threshold}"
            )

        return in_regime, T

    def apply_to_signal(self, signal: Signal) -> Signal:
        """
        Apply regime check to a trading signal.
        
        Convenience method that checks regime from the signal's features
        and updates signal metadata accordingly. If the signal is not
        in trading regime, adds "regime_blocked" to filters_failed.
        
        Args:
            signal: Signal object with optional features attribute.
                If features is None, returns signal unchanged.
        
        Returns:
            Modified signal with regime_T, in_trading_regime set and
            filters_failed updated if blocked.
        
        Side Effects:
            Modifies signal.regime_T, signal.in_trading_regime, and
            potentially appends to signal.filters_failed.
        
        Example:
            >>> signal = Signal(
            ...     symbol="ETHUSDT",
            ...     direction=Direction.LONG,
            ...     confidence=0.8,
            ...     features=features
            ... )
            >>> filtered = detector.apply_to_signal(signal)
            >>> if not filtered.in_trading_regime:
            ...     print("Signal blocked by regime filter")
        """
        if signal.features is None:
            return signal

        in_regime, T = self.check_regime(signal.features)
        signal.regime_T = T
        signal.in_trading_regime = in_regime

        if not in_regime:
            signal.filters_failed.append("regime_blocked")

        return signal

    def get_regime_stats(self) -> dict:
        """
        Get aggregated regime statistics from history.
        
        Computes statistics over the rolling regime history including
        the percentage of time steps blocked and average regime T value.
        
        Returns:
            Dictionary containing:
            - blocked_pct: Fraction of checks that blocked trading (0.0 to 1.0)
            - total_checks: Total number of regime checks performed
            - avg_T: Average regime T value across history
        
        Example:
            >>> stats = detector.get_regime_stats()
            >>> print(f"Blocked: {stats['blocked_pct']:.1%}, Avg T: {stats['avg_T']:.2f}")
            Blocked: 12.3%, Avg T: 1.45
        """
        if not self._regime_history:
            return {"blocked_pct": 0.0, "total_checks": 0}

        blocked = sum(1 for _, _, in_regime in self._regime_history if not in_regime)
        return {
            "blocked_pct": blocked / len(self._regime_history),
            "total_checks": len(self._regime_history),
            "avg_T": sum(t for _, t, _ in self._regime_history) / len(self._regime_history)
        }

    def reset(self) -> None:
        """
        Reset regime detector state.
        
        Clears all regime history. Call this at the start of a new
        backtest or trading session to ensure clean statistics.
        """
        self._regime_history.clear()


class VolatilityRegimeDetector:
    """
    Alternative regime detector based on volatility percentile ranking.
    
    This detector uses a non-parametric approach to identify high-volatility
    regimes by comparing current volatility to its historical percentile rank.
    This approach is more robust to volatility clustering than the SNR-based
    RegimeDetector and works well in trending markets.
    
    Attributes:
        window: Rolling window size for volatility history.
        high_percentile: Percentile threshold for high-volatility classification.
        _volatility_history: Rolling history of volatility observations.
    
    Mathematical Details:
        Volatility Ratio = σ_t / μ(σ_{t-window:t})
        
        High Volatility Regime = (σ_t > P_thresh)
        
        Where P_thresh = percentile(vol_history, high_percentile)
    
    Example:
        >>> detector = VolatilityRegimeDetector(window=100, high_percentile=90)
        >>> detector.update(0.0025)
        >>> is_high = detector.is_high_volatility()
        >>> ratio = detector.get_volatility_ratio()
    """

    def __init__(self, window: int = 100, high_percentile: float = 90):
        self.window = window
        self.high_percentile = high_percentile
        self._volatility_history = []

    def update(self, volatility: float) -> None:
        """
        Update volatility observation.
        
        Adds a new volatility observation to the rolling history.
        The history is automatically pruned to maintain window size.
        
        Args:
            volatility: Current realized volatility estimate.
        """
        self._volatility_history.append(volatility)
        if len(self._volatility_history) > self.window * 2:
            self._volatility_history = self._volatility_history[-self.window:]

    def is_high_volatility(self) -> bool:
        """
        Check if current volatility is elevated relative to history.
        
        Uses percentile ranking to determine if current volatility is
        unusually high compared to the rolling window history.
        
        Returns:
            True if current volatility > high_percentile of history,
            False otherwise. Returns False if insufficient data.
        
        Mathematical Formula:
            threshold = P(vol_history, high_percentile)
            return vol_current > threshold
        """
        if len(self._volatility_history) < self.window:
            return False

        recent = self._volatility_history[-self.window:]
        threshold = np.percentile(recent, self.high_percentile)
        return self._volatility_history[-1] > threshold

    def get_volatility_ratio(self) -> float:
        """
        Get current volatility as ratio to recent average.
        
        Returns the volatility ratio which can be used to scale
        position sizes inversely proportional to volatility.
        
        Returns:
            Ratio of current to average historical volatility.
            Returns 1.0 if insufficient data or average is zero.
        
        Mathematical Formula:
            ratio = σ_t / mean(σ_{t-window:t})
        
        Usage:
            When ratio > 1: Consider reducing position size
            When ratio < 1: Consider normal or increased position size
        """
        if len(self._volatility_history) < self.window:
            return 1.0

        recent = self._volatility_history[-self.window:]
        avg = np.mean(recent)
        if avg <= EPS:
            return 1.0
        return self._volatility_history[-1] / avg


import numpy as np
