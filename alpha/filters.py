"""
Signal Filters - Turnover and Stability Validation

This module implements signal filtering rules:
1. Turnover Filter: Reject signals with high turnover (prevents overtrading)
2. Stability Filter: Reject signals with high variance (reduces noise)

FILTER PHILOSOPHY:
- Conservative: Better to miss opportunities than take bad trades
- Layered: Multiple independent filters provide defense in depth
- Adaptive: Filter thresholds can adjust to market conditions

Author: LVR Trading System
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class FilterResult:
    """Result of signal filtering."""
    passed: bool
    reason: Optional[str] = None
    score: float = 0.0
    threshold: float = 0.0
    
    def __bool__(self) -> bool:
        return self.passed


class TurnoverFilter:
    """
    Filters signals based on portfolio turnover.
    
    TURNOVER DEFINITION:
        turnover = Σ|Δposition| / portfolio_value
        
    High turnover implies:
    - Excessive trading costs
    - Signal instability
    - Overfitting to noise
    
    THRESHOLD STRATEGY:
        - Low volatility markets: Higher turnover OK
        - High volatility markets: Stricter turnover limits
        
    Example:
        >>> filter = TurnoverFilter(max_turnover=0.5)
        >>> 
        >>> # Record position changes
        >>> filter.record_trade(symbol="BTCUSDT", size=1.0, price=50000)
        >>> filter.record_trade(symbol="BTCUSDT", size=-0.5, price=50100)
        >>> 
        >>> result = filter.check()
        >>> print(f"Pass: {result.passed}, Turnover: {result.score:.2%}")
    """
    
    def __init__(
        self,
        max_turnover: float = 0.5,
        window_seconds: int = 60,
        cooldown_seconds: int = 5,
    ):
        """
        Initialize turnover filter.
        
        Args:
            max_turnover: Maximum allowed turnover (0.5 = 50%)
            window_seconds: Time window for turnover calculation
            cooldown_seconds: Minimum time between trades per symbol
        """
        self.max_turnover = max_turnover
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        
        self._trades: deque = deque()
        self._symbol_last_trade: dict[str, float] = {}
        self._portfolio_value: float = 100000.0
        
    def set_portfolio_value(self, value: float) -> None:
        """Update portfolio value for turnover calculation."""
        self._portfolio_value = max(value, 1.0)
    
    def record_trade(
        self,
        symbol: str,
        size: float,
        price: float,
        timestamp: Optional[float] = None
    ) -> bool:
        """
        Record a trade for turnover tracking.
        
        Args:
            symbol: Trading symbol
            size: Trade size (signed)
            price: Trade price
            timestamp: Trade timestamp (default: now)
            
        Returns:
            True if trade recorded, False if in cooldown
        """
        now = timestamp or datetime.now().timestamp()
        
        if symbol in self._symbol_last_trade:
            if now - self._symbol_last_trade[symbol] < self.cooldown_seconds:
                return False
        
        self._trades.append({
            "symbol": symbol,
            "size": size,
            "value": abs(size * price),
            "timestamp": now,
        })
        self._symbol_last_trade[symbol] = now
        
        self._cleanup_old_trades(now)
        return True
    
    def _cleanup_old_trades(self, now: float) -> None:
        """Remove trades outside the window."""
        cutoff = now - self.window_seconds
        while self._trades and self._trades[0]["timestamp"] < cutoff:
            self._trades.popleft()
    
    def check(self, proposed_size: float = 0, proposed_price: float = 0) -> FilterResult:
        """
        Check if proposed trade passes turnover filter.
        
        Args:
            proposed_size: Size of proposed trade
            proposed_price: Price of proposed trade
            
        Returns:
            FilterResult with pass/fail and details
        """
        self._cleanup_old_trades(datetime.now().timestamp())
        
        current_turnover = self._calculate_turnover()
        proposed_turnover = abs(proposed_size * proposed_price) / self._portfolio_value
        
        total_turnover = current_turnover + proposed_turnover
        
        if total_turnover > self.max_turnover:
            return FilterResult(
                passed=False,
                reason=f"Turnover {total_turnover:.2%} exceeds max {self.max_turnover:.2%}",
                score=total_turnover,
                threshold=self.max_turnover
            )
        
        return FilterResult(
            passed=True,
            score=total_turnover,
            threshold=self.max_turnover
        )
    
    def _calculate_turnover(self) -> float:
        """Calculate current turnover in the window."""
        if self._portfolio_value <= 0:
            return 0.0
            
        total_value = sum(t["value"] for t in self._trades)
        return total_value / self._portfolio_value
    
    def reset(self) -> None:
        """Reset all tracking state."""
        self._trades.clear()
        self._symbol_last_trade.clear()
    
    def get_stats(self) -> dict:
        """Get turnover statistics."""
        return {
            "current_turnover": self._calculate_turnover(),
            "trade_count": len(self._trades),
            "window_seconds": self.window_seconds,
            "max_turnover": self.max_turnover,
        }


class StabilityFilter:
    """
    Filters signals based on historical stability.
    
    STABILITY METRICS:
    1. Signal variance: High variance = unstable signal
    2. Prediction error: Large errors = unreliable predictions
    3. Regime persistence: Rapid changes = unstable environment
    
    THRESHOLD STRATEGY:
        - Use rolling window for adaptive thresholds
        - Consider market regime when setting limits
        - Require minimum confidence for stability
        
    Example:
        >>> filter = StabilityFilter(min_stable_trades=10, max_variance=0.5)
        >>> 
        >>> # Record prediction outcomes
        >>> filter.record_outcome(symbol="BTCUSDT", predicted=0.01, actual=0.008)
        >>> filter.record_outcome(symbol="BTCUSDT", predicted=0.01, actual=0.012)
        >>> 
        >>> result = filter.check(symbol="BTCUSDT")
        >>> print(f"Stable: {result.passed}, Variance: {result.score:.4f}")
    """
    
    def __init__(
        self,
        min_stable_trades: int = 10,
        max_variance: float = 0.5,
        variance_window: int = 20,
        prediction_decay: float = 0.95,
    ):
        """
        Initialize stability filter.
        
        Args:
            min_stable_trades: Minimum trades before variance calculation valid
            max_variance: Maximum allowed variance (0.5 = 50% CV)
            variance_window: Number of trades to consider
            prediction_decay: Decay factor for recency weighting
        """
        self.min_stable_trades = min_stable_trades
        self.max_variance = max_variance
        self.variance_window = variance_window
        self.prediction_decay = prediction_decay
        
        self._predictions: dict[str, deque] = {}
        self._actuals: dict[str, deque] = {}
        self._last_update: dict[str, float] = {}
    
    def record_outcome(
        self,
        symbol: str,
        predicted: float,
        actual: float,
        timestamp: Optional[float] = None
    ) -> None:
        """
        Record prediction outcome for stability tracking.
        
        Args:
            symbol: Trading symbol
            predicted: Predicted value
            actual: Actual realized value
            timestamp: Event timestamp
        """
        if symbol not in self._predictions:
            self._predictions[symbol] = deque(maxlen=self.variance_window)
            self._actuals[symbol] = deque(maxlen=self.variance_window)
        
        self._predictions[symbol].append(predicted)
        self._actuals[symbol].append(actual)
        self._last_update[symbol] = timestamp or datetime.now().timestamp()
    
    def check(self, symbol: str) -> FilterResult:
        """
        Check if symbol signal is stable.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            FilterResult with stability assessment
        """
        if symbol not in self._predictions:
            return FilterResult(
                passed=False,
                reason="No history available",
                score=0.0,
                threshold=self.max_variance
            )
        
        n = len(self._predictions[symbol])
        
        if n < self.min_stable_trades:
            return FilterResult(
                passed=False,
                reason=f"Insufficient trades: {n}/{self.min_stable_trades}",
                score=n / self.min_stable_trades,
                threshold=1.0
            )
        
        variance = self._calculate_variance(symbol)
        
        if variance > self.max_variance:
            return FilterResult(
                passed=False,
                reason=f"Variance {variance:.3f} exceeds max {self.max_variance:.3f}",
                score=variance,
                threshold=self.max_variance
            )
        
        return FilterResult(
            passed=True,
            score=variance,
            threshold=self.max_variance
        )
    
    def _calculate_variance(self, symbol: str) -> float:
        """
        Calculate coefficient of variation for predictions.
        
        CV = std(actual) / |mean(actual)|
        
        Lower CV = More stable predictions
        """
        predictions = np.array(list(self._predictions[symbol]))
        actuals = np.array(list(self._actuals[symbol]))
        
        errors = actuals - predictions
        mean_error = np.mean(errors)
        std_error = np.std(errors)
        
        if abs(mean_error) < 1e-10:
            return std_error
        
        cv = std_error / abs(mean_error)
        return cv
    
    def get_stability_score(self, symbol: str) -> float:
        """
        Get stability score (0-1, higher = more stable).
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Stability score, 0 if no data
        """
        if symbol not in self._predictions:
            return 0.0
            
        variance = self._calculate_variance(symbol)
        
        score = 1.0 - min(variance / self.max_variance, 1.0)
        return max(0.0, score)
    
    def reset(self, symbol: Optional[str] = None) -> None:
        """Reset stability tracking."""
        if symbol:
            if symbol in self._predictions:
                self._predictions[symbol].clear()
                self._actuals[symbol].clear()
        else:
            self._predictions.clear()
            self._actuals.clear()


@dataclass
class SignalFilters:
    """
    Combined signal filters for validation.
    
    Filters are applied in order:
    1. Turnover → Cost constraints
    2. Stability → Quality constraints
    3. Confidence → Reliability constraints
    
    Example:
        >>> filters = SignalFilters()
        >>> 
        >>> # Check all filters
        >>> result = filters.validate(
        ...     symbol="BTCUSDT",
        ...     size=1.0,
        ...     price=50000,
        ...     confidence=0.8
        ... )
        >>> 
        >>> if result.all_passed:
        ...     execute_trade()
        >>> else:
        ...     log(f"Failed: {result.failed_reasons}")
    """
    
    turnover_filter: TurnoverFilter
    stability_filter: StabilityFilter
    
    min_confidence: float = 0.5
    max_age_seconds: float = 60.0
    
    def __init__(
        self,
        turnover_config: Optional[dict] = None,
        stability_config: Optional[dict] = None,
        min_confidence: float = 0.5,
        max_age_seconds: float = 60.0,
    ):
        turnover_config = turnover_config or {}
        stability_config = stability_config or {}
        
        self.turnover_filter = TurnoverFilter(**turnover_config)
        self.stability_filter = StabilityFilter(**stability_config)
        self.min_confidence = min_confidence
        self.max_age_seconds = max_age_seconds
    
    def validate(
        self,
        symbol: str,
        size: float,
        price: float,
        confidence: float,
        signal_timestamp: Optional[float] = None,
    ) -> ValidationResult:
        """
        Validate signal against all filters.
        
        Args:
            symbol: Trading symbol
            size: Proposed trade size
            price: Current price
            confidence: Signal confidence (0-1)
            signal_timestamp: When signal was generated
            
        Returns:
            ValidationResult with all filter outcomes
        """
        results = []
        all_passed = True
        failed_reasons = []
        
        turnover_result = self.turnover_filter.check(size, price)
        results.append(("turnover", turnover_result))
        if not turnover_result.passed:
            all_passed = False
            failed_reasons.append(turnover_result.reason)
        
        stability_result = self.stability_filter.check(symbol)
        results.append(("stability", stability_result))
        if not stability_result.passed:
            all_passed = False
            failed_reasons.append(stability_result.reason)
        
        confidence_result = self._check_confidence(confidence)
        results.append(("confidence", confidence_result))
        if not confidence_result.passed:
            all_passed = False
            failed_reasons.append(confidence_result.reason)
        
        age_result = self._check_age(signal_timestamp)
        results.append(("age", age_result))
        if not age_result.passed:
            all_passed = False
            failed_reasons.append(age_result.reason)
        
        return ValidationResult(
            all_passed=all_passed,
            results=results,
            failed_reasons=failed_reasons,
        )
    
    def _check_confidence(self, confidence: float) -> FilterResult:
        """Check signal confidence threshold."""
        if confidence < self.min_confidence:
            return FilterResult(
                passed=False,
                reason=f"Confidence {confidence:.2%} below min {self.min_confidence:.2%}",
                score=confidence,
                threshold=self.min_confidence
            )
        return FilterResult(passed=True, score=confidence, threshold=self.min_confidence)
    
    def _check_age(self, timestamp: Optional[float]) -> FilterResult:
        """Check if signal is fresh enough."""
        if timestamp is None:
            return FilterResult(passed=True, score=1.0, threshold=0.0)
        
        age = datetime.now().timestamp() - timestamp
        
        if age > self.max_age_seconds:
            return FilterResult(
                passed=False,
                reason=f"Signal age {age:.0f}s exceeds max {self.max_age_seconds:.0f}s",
                score=1.0 - age / self.max_age_seconds,
                threshold=0.0
            )
        
        return FilterResult(passed=True, score=1.0 - age / self.max_age_seconds, threshold=0.0)


@dataclass
class ValidationResult:
    """Combined validation result."""
    all_passed: bool
    results: list[tuple[str, FilterResult]]
    failed_reasons: list[str]
    
    def __bool__(self) -> bool:
        return self.all_passed
