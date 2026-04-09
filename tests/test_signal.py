"""
Tests for signal generation and filtering.
"""

import pytest
import numpy as np

from app.schemas import FeatureVector, Side, Signal
from strategy import SignalGenerator, RegimeDetector


class TestSignalGenerator:
    """Test signal generator."""

    @pytest.fixture
    def generator(self):
        return SignalGenerator(
            ofi_threshold=0.7,
            min_confidence=0.3
        )

    @pytest.fixture
    def valid_features(self):
        return FeatureVector(
            timestamp=1609459200000,
            symbol="BTCUSDT",
            I_star=0.5,
            L_star=-0.2,
            S_star=0.3,
            OFI=0.1,
            depth_imbalance=0.0,
            returns=0.001,
            volatility=0.002,
            spread=1.0,
            bid_depth=100.0,
            ask_depth=100.0
        )

    def test_signal_generation(self, generator, valid_features):
        """Should generate valid signal."""
        signal = generator.generate(valid_features)
        assert signal is not None
        assert signal.symbol == "BTCUSDT"
        assert signal.direction in [Side.BUY, Side.SELL]
        assert 0 <= signal.strength <= 1
        assert 0 <= signal.confidence <= 1

    def test_ofi_filter_rejects_extreme(self, generator, valid_features):
        """Should reject extreme OFI."""
        valid_features.OFI = 0.9
        signal = generator.generate(valid_features)
        assert signal is None or "ofi" in str(signal.filters_failed)

    def test_low_confidence_rejected(self, generator, valid_features):
        """Should reject low confidence signals."""
        valid_features.I_star = 0.01
        signal = generator.generate(valid_features)
        if signal:
            assert "low_confidence" in signal.filters_failed or signal is None

    def test_direction_opposite_impulse(self, generator, valid_features):
        """Direction should be opposite of impulse."""
        valid_features.I_star = 0.5
        signal = generator.generate(valid_features)
        assert signal.direction == Side.SELL

        valid_features.I_star = -0.5
        signal = generator.generate(valid_features)
        assert signal.direction == Side.BUY


class TestRegimeDetector:
    """Test regime detection."""

    @pytest.fixture
    def detector(self):
        return RegimeDetector(threshold=2.0)

    def test_normal_regime(self, detector):
        """Normal regime should pass."""
        features = FeatureVector(
            timestamp=1609459200000,
            symbol="BTCUSDT",
            I_star=0.1,
            L_star=0.0,
            S_star=0.0,
            OFI=0.0,
            depth_imbalance=0.0,
            returns=0.0001,
            volatility=0.001
        )

        in_regime, T = detector.check_regime(features)
        assert in_regime is True
        assert T < 2.0

    def test_high_volatility_blocked(self, detector):
        """High volatility regime should be blocked."""
        features = FeatureVector(
            timestamp=1609459200000,
            symbol="BTCUSDT",
            I_star=0.1,
            L_star=0.0,
            S_star=0.0,
            OFI=0.0,
            depth_imbalance=0.0,
            returns=0.01,
            volatility=0.001
        )

        in_regime, T = detector.check_regime(features)
        assert T > 2.0
        assert in_regime is False
