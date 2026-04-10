"""
Reality Calibration Engine - Real-time adaptation to execution reality.

Compares expected vs actual execution quality:
- Expected vs actual slippage
- Expected vs actual fill rates
- Expected vs actual edge
- Detects reality gaps and adapts
"""

import logging
import time
from dataclasses import dataclass, field
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ExecutionExpectation:
    """Expected execution parameters."""
    expected_slippage: float = 0.0
    expected_fill_rate: float = 1.0
    expected_latency_ms: float = 100.0
    expected_fee_pct: float = 0.04


@dataclass
class ExecutionReality:
    """Actual execution results."""
    actual_slippage: float = 0.0
    actual_fill_rate: float = 1.0
    actual_latency_ms: float = 0.0
    actual_fee_pct: float = 0.0


@dataclass
class RealityGap:
    """Discrepancy between expectation and reality."""
    slippage_error: float = 0.0
    fill_rate_error: float = 0.0
    latency_error: float = 0.0
    edge_error: float = 0.0
    timestamp: int = 0
    severity: str = "none"


class RealityCalibrationEngine:
    """
    Real-time reality calibration and adaptation.
    
    Features:
    - Compare expected vs actual execution
    - Detect reality gaps
    - Auto-adjust expectations
    - Alert on significant deviations
    
    Example:
        >>> calibrator = RealityCalibrationEngine()
        >>> calibrator.record_expectation(symbol, expected)
        >>> calibrator.record_reality(symbol, actual)
        >>> gap = calibrator.get_gap(symbol)
        >>> if gap.severity != "none":
        ...     calibrator.adjust_expectations(symbol)
    """

    def __init__(
        self,
        slippage_tolerance: float = 2.0,
        fill_rate_tolerance: float = 0.2,
        latency_tolerance: float = 2.0,
        edge_tolerance: float = 0.5,
        window_size: int = 100,
        alert_threshold: str = "medium"
    ):
        self.slippage_tolerance = slippage_tolerance
        self.fill_rate_tolerance = fill_rate_tolerance
        self.latency_tolerance = latency_tolerance
        self.edge_tolerance = edge_tolerance
        self.window_size = window_size
        self.alert_threshold = alert_threshold
        
        self._expectations: dict[str, ExecutionExpectation] = {}
        self._reality_history: dict[str, deque] = {}
        
        self._adaptation_factors: dict[str, dict] = {}
        
        self._stats = {
            "total_calibrations": 0,
            "gaps_detected": 0,
            "adaptations_made": 0,
            "alerts_sent": 0
        }

    def set_expectation(self, symbol: str, expectation: ExecutionExpectation) -> None:
        """Set expected execution parameters for symbol."""
        self._expectations[symbol] = expectation
        
        if symbol not in self._reality_history:
            self._reality_history[symbol] = deque(maxlen=self.window_size)
        
        if symbol not in self._adaptation_factors:
            self._adaptation_factors[symbol] = {
                "slippage_factor": 1.0,
                "fill_rate_factor": 1.0,
                "latency_factor": 1.0,
                "edge_factor": 1.0
            }

    def record_reality(self, symbol: str, reality: ExecutionReality) -> None:
        """Record actual execution result."""
        if symbol not in self._reality_history:
            self._reality_history[symbol] = deque(maxlen=self.window_size)
        
        self._reality_history[symbol].append(reality)
        self._stats["total_calibrations"] += 1

    def get_gap(self, symbol: str) -> Optional[RealityGap]:
        """Calculate reality gap for symbol."""
        if symbol not in self._reality_history:
            return None
        
        history = list(self._reality_history[symbol])
        if not history:
            return None
        
        expectation = self._expectations.get(symbol, ExecutionExpectation())
        
        avg_actual_slippage = sum(r.actual_slippage for r in history) / len(history)
        avg_actual_fill_rate = sum(r.actual_fill_rate for r in history) / len(history)
        avg_actual_latency = sum(r.actual_latency_ms for r in history) / len(history)
        
        slippage_error = (avg_actual_slippage - expectation.expected_slippage) / max(expectation.expected_slippage, 1e-10)
        fill_rate_error = avg_actual_fill_rate - expectation.expected_fill_rate
        latency_error = (avg_actual_latency - expectation.expected_latency_ms) / max(expectation.expected_latency_ms, 1)
        
        gap = RealityGap(
            slippage_error=slippage_error,
            fill_rate_error=fill_rate_error,
            latency_error=latency_error,
            timestamp=int(time.time() * 1000),
            severity=self._calculate_severity(slippage_error, fill_rate_error, latency_error)
        )
        
        if gap.severity != "none":
            self._stats["gaps_detected"] += 1
        
        return gap

    def _calculate_severity(
        self,
        slippage_error: float,
        fill_rate_error: float,
        latency_error: float
    ) -> str:
        """Calculate severity of reality gap."""
        max_error = max(
            abs(slippage_error) / self.slippage_tolerance if self.slippage_tolerance > 0 else 0,
            abs(fill_rate_error) / self.fill_rate_tolerance if self.fill_rate_tolerance > 0 else 0,
            abs(latency_error) / self.latency_tolerance if self.latency_tolerance > 0 else 0
        )
        
        if max_error > 3.0:
            return "critical"
        elif max_error > 2.0:
            return "high"
        elif max_error > 1.0:
            return "medium"
        else:
            return "none"

    def adapt_expectations(self, symbol: str) -> ExecutionExpectation:
        """Adapt expectations based on observed reality."""
        if symbol not in self._reality_history:
            return ExecutionExpectation()
        
        history = list(self._reality_history[symbol])
        if not history:
            return ExecutionExpectation()
        
        factors = self._adaptation_factors.get(symbol, {
            "slippage_factor": 1.0,
            "fill_rate_factor": 1.0,
            "latency_factor": 1.0,
            "edge_factor": 1.0
        })
        
        avg_slippage = sum(r.actual_slippage for r in history) / len(history)
        avg_fill_rate = sum(r.actual_fill_rate for r in history) / len(history)
        avg_latency = sum(r.actual_latency_ms for r in history) / len(history)
        avg_fee = sum(r.actual_fee_pct for r in history) / len(history)
        
        learning_rate = 0.1
        factors["slippage_factor"] = factors["slippage_factor"] * (1 - learning_rate) + (avg_slippage / max(self._expectations.get(symbol, ExecutionExpectation()).expected_slippage, 1e-10)) * learning_rate
        factors["fill_rate_factor"] = factors["fill_rate_factor"] * (1 - learning_rate) + avg_fill_rate * learning_rate
        factors["latency_factor"] = factors["latency_factor"] * (1 - learning_rate) + (avg_latency / max(self._expectations.get(symbol, ExecutionExpectation()).expected_latency_ms, 1)) * learning_rate
        
        self._adaptation_factors[symbol] = factors
        self._stats["adaptations_made"] += 1
        
        new_expectation = ExecutionExpectation(
            expected_slippage=avg_slippage,
            expected_fill_rate=avg_fill_rate,
            expected_latency_ms=avg_latency,
            expected_fee_pct=avg_fee
        )
        
        self._expectations[symbol] = new_expectation
        
        logger.info(f"Adapted expectations for {symbol}: slippage={avg_slippage:.4f}, fill_rate={avg_fill_rate:.2f}")
        
        return new_expectation

    def get_adaptation_factor(self, symbol: str) -> dict:
        """Get current adaptation factors."""
        return self._adaptation_factors.get(symbol, {
            "slippage_factor": 1.0,
            "fill_rate_factor": 1.0,
            "latency_factor": 1.0,
            "edge_factor": 1.0
        })

    def reset_calibration(self, symbol: str) -> None:
        """Reset calibration for symbol."""
        if symbol in self._reality_history:
            self._reality_history[symbol].clear()
        if symbol in self._adaptation_factors:
            self._adaptation_factors[symbol] = {
                "slippage_factor": 1.0,
                "fill_rate_factor": 1.0,
                "latency_factor": 1.0,
                "edge_factor": 1.0
            }
        logger.info(f"Calibration reset for {symbol}")

    def get_stats(self) -> dict:
        """Get calibration statistics."""
        return self._stats.copy()


class AdaptiveThreshold:
    """
    Adaptive thresholds that adjust based on market conditions.
    
    Features:
    - Volatility-adjusted position sizing
    - Dynamic confidence thresholds
    - Market regime-aware limits
    """

    def __init__(
        self,
        base_confidence_threshold: float = 0.3,
        base_edge_threshold: float = 0.0001,
        volatility_window: int = 100,
        adaptation_rate: float = 0.05
    ):
        self.base_confidence_threshold = base_confidence_threshold
        self.base_edge_threshold = base_edge_threshold
        self.volatility_window = volatility_window
        self.adaptation_rate = adaptation_rate
        
        self._volatility_history: deque = deque(maxlen=volatility_window)
        self._current_volatility: float = 1.0

    def update_volatility(self, volatility: float) -> None:
        """Update current volatility measure."""
        self._volatility_history.append(volatility)
        
        if len(self._volatility_history) >= 10:
            self._current_volatility = sum(self._volatility_history) / len(self._volatility_history)

    def get_confidence_threshold(self) -> float:
        """Get adapted confidence threshold."""
        vol_multiplier = 1.0 + (self._current_volatility - 1.0) * 0.5
        return min(self.base_confidence_threshold * vol_multiplier, 0.8)

    def get_edge_threshold(self) -> float:
        """Get adapted edge threshold."""
        vol_multiplier = 1.0 + (self._current_volatility - 1.0) * 0.3
        return self.base_edge_threshold * vol_multiplier

    def get_position_multiplier(self) -> float:
        """Get position size multiplier based on volatility."""
        if self._current_volatility < 0.5:
            return 1.5
        elif self._current_volatility > 2.0:
            return 0.5
        else:
            return 1.0