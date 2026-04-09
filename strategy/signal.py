"""
Signal generation engine with filters and regime detection.
"""

import logging
from typing import Optional

from app.schemas import FeatureVector, Side, Signal

logger = logging.getLogger(__name__)


class SignalGenerator:
    """
    Generates trading signals from feature vectors.
    
    Core Formula:
        score = I_star * (-L_star) * S_star * (1 - abs(OFI))
        direction = inverse of impulse (opposite of I_star)
    """

    def __init__(
        self,
        ofi_threshold: float = 0.7,
        min_confidence: float = 0.3,
        signal_decay: float = 0.95
    ):
        self.ofi_threshold = ofi_threshold
        self.min_confidence = min_confidence
        self.signal_decay = signal_decay

        self._last_signal: Optional[Signal] = None
        self._signal_history: list[Signal] = []

    def generate(self, features: FeatureVector) -> Optional[Signal]:
        """Generate signal from features."""
        if features.has_nans():
            logger.warning(f"Invalid features for {features.symbol}, skipping")
            return None

        signal = Signal(
            symbol=features.symbol,
            direction=Side.BUY,
            strength=0.0,
            confidence=0.0,
            features=features
        )

        signal.filters_passed = []
        signal.filters_failed = []

        if not self._check_ofi_filter(features, signal):
            self._record_signal(signal)
            return None

        if not self._check_microstructure_reversal(features, signal):
            self._record_signal(signal)
            return None

        self._compute_scores(features, signal)

        if signal.strength < self.min_confidence:
            signal.filters_failed.append("low_confidence")
            self._record_signal(signal)
            return None

        if signal.expected_edge <= 0:
            signal.filters_failed.append("no_edge")
            self._record_signal(signal)
            return None

        signal.filters_passed.append("ofi_filter")
        signal.filters_passed.append("reversal_confirmed")

        self._record_signal(signal)
        return signal

    def _check_ofi_filter(self, features: FeatureVector, signal: Signal) -> bool:
        """Reject if |OFI| > threshold."""
        if abs(features.OFI) > self.ofi_threshold:
            signal.filters_failed.append("ofi_extreme")
            logger.debug(f"OFI filter failed: {features.OFI}")
            return False
        return True

    def _check_microstructure_reversal(
        self,
        features: FeatureVector,
        signal: Signal
    ) -> bool:
        """Require microstructure reversal confirmation."""
        reversal_score = features.I_star * (1 - abs(features.OFI))

        if reversal_score < 0:
            signal.filters_failed.append("no_reversal")
            return False

        return True

    def _compute_scores(self, features: FeatureVector, signal: Signal) -> None:
        """Compute component scores and final signal."""
        signal.impulse_score = abs(features.I_star)
        signal.liquidity_score = 1 - min(abs(features.L_star) / 3.0, 1.0)
        signal.spread_score = 1 - min(abs(features.S_star) / 3.0, 1.0)
        signal.flow_score = 1 - abs(features.OFI)

        score = (
            features.I_star *
            (-features.L_star) *
            features.S_star *
            signal.flow_score
        )

        signal.direction = Side.SELL if features.I_star > 0 else Side.BUY

        signal.strength = float(min(abs(score), 1.0))

        confidence = (
            signal.impulse_score * 0.4 +
            signal.liquidity_score * 0.2 +
            signal.spread_score * 0.2 +
            signal.flow_score * 0.2
        )
        signal.confidence = float(np.clip(confidence, 0, 1))

        if self._last_signal and self._last_signal.symbol == signal.symbol:
            prev_dir = self._last_signal.direction
            if prev_dir == signal.direction:
                signal.confidence *= self.signal_decay

        signal.expected_edge = signal.strength * signal.confidence

    def _record_signal(self, signal: Signal) -> None:
        """Record signal in history."""
        self._last_signal = signal
        self._signal_history.append(signal)
        if len(self._signal_history) > 1000:
            self._signal_history = self._signal_history[-500:]

    def get_last_signal(self, symbol: Optional[str] = None) -> Optional[Signal]:
        """Get last signal for symbol."""
        if symbol:
            for s in reversed(self._signal_history):
                if s.symbol == symbol:
                    return s
            return None
        return self._last_signal

    def reset(self) -> None:
        """Reset generator state."""
        self._last_signal = None
        self._signal_history.clear()


import numpy as np
