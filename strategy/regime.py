"""
Market regime detection and filtering.
"""

import logging
from typing import Optional

from app.schemas import FeatureVector, Signal

logger = logging.getLogger(__name__)

EPS = 1e-10


class RegimeDetector:
    """
    Detects market regimes and blocks trading in adverse conditions.
    
    Regime T = |returns| / volatility
    If T > threshold: block trading (high volatility regime)
    """

    def __init__(self, threshold: float = 2.0):
        self.threshold = threshold
        self._regime_history = []

    def check_regime(self, features: FeatureVector) -> tuple[bool, float]:
        """
        Check if market is in trading regime.
        
        Returns:
            (in_trading_regime, regime_T)
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
        """Apply regime check to signal."""
        if signal.features is None:
            return signal

        in_regime, T = self.check_regime(signal.features)
        signal.regime_T = T
        signal.in_trading_regime = in_regime

        if not in_regime:
            signal.filters_failed.append("regime_blocked")

        return signal

    def get_regime_stats(self) -> dict:
        """Get regime statistics."""
        if not self._regime_history:
            return {"blocked_pct": 0.0, "total_checks": 0}

        blocked = sum(1 for _, _, in_regime in self._regime_history if not in_regime)
        return {
            "blocked_pct": blocked / len(self._regime_history),
            "total_checks": len(self._regime_history),
            "avg_T": sum(t for _, t, _ in self._regime_history) / len(self._regime_history)
        }

    def reset(self) -> None:
        """Reset regime history."""
        self._regime_history.clear()


class VolatilityRegimeDetector:
    """Alternative regime detector based on volatility percentiles."""

    def __init__(self, window: int = 100, high_percentile: float = 90):
        self.window = window
        self.high_percentile = high_percentile
        self._volatility_history = []

    def update(self, volatility: float) -> None:
        """Update volatility observation."""
        self._volatility_history.append(volatility)
        if len(self._volatility_history) > self.window * 2:
            self._volatility_history = self._volatility_history[-self.window:]

    def is_high_volatility(self) -> bool:
        """Check if current volatility is elevated."""
        if len(self._volatility_history) < self.window:
            return False

        recent = self._volatility_history[-self.window:]
        threshold = np.percentile(recent, self.high_percentile)
        return self._volatility_history[-1] > threshold

    def get_volatility_ratio(self) -> float:
        """Get current volatility as ratio to recent average."""
        if len(self._volatility_history) < self.window:
            return 1.0

        recent = self._volatility_history[-self.window:]
        avg = np.mean(recent)
        if avg <= EPS:
            return 1.0
        return self._volatility_history[-1] / avg


import numpy as np
