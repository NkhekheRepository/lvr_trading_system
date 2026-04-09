"""
Signal filters for quality control.
"""

import logging
from typing import Callable, Optional

from app.schemas import FeatureVector, Signal

logger = logging.getLogger(__name__)


class SignalFilter:
    """Base class for signal filters."""

    def __init__(self, name: str):
        self.name = name

    def apply(self, signal: Signal) -> tuple[bool, str]:
        """Apply filter, returns (passed, reason)."""
        raise NotImplementedError


class OFIFilter(SignalFilter):
    """Filter signals based on Order Flow Imbalance."""

    def __init__(self, threshold: float = 0.7):
        super().__init__("OFI")
        self.threshold = threshold

    def apply(self, signal: Signal) -> tuple[bool, str]:
        if signal.features is None:
            return True, "no_features"

        ofi = signal.features.OFI
        if abs(ofi) > self.threshold:
            return False, f"OFI={ofi:.3f} > {self.threshold}"
        return True, "passed"


class SpreadFilter(SignalFilter):
    """Filter signals based on bid-ask spread."""

    def __init__(self, max_zscore: float = 3.0):
        super().__init__("Spread")
        self.max_zscore = max_zscore

    def apply(self, signal: Signal) -> tuple[bool, str]:
        if signal.features is None:
            return True, "no_features"

        s_star = signal.features.S_star
        if abs(s_star) > self.max_zscore:
            return False, f"S_star={s_star:.3f} > {self.max_zscore}"
        return True, "passed"


class DepthFilter(SignalFilter):
    """Filter signals based on order book depth."""

    def __init__(self, min_depth: float = 0.1):
        super().__init__("Depth")
        self.min_depth = min_depth

    def apply(self, signal: Signal) -> tuple[bool, str]:
        if signal.features is None:
            return True, "no_features"

        depth = min(
            signal.features.bid_depth,
            signal.features.ask_depth
        )
        if depth < self.min_depth:
            return False, f"depth={depth:.3f} < {self.min_depth}"
        return True, "passed"


class ConfidenceFilter(SignalFilter):
    """Filter signals based on confidence threshold."""

    def __init__(self, min_confidence: float = 0.3):
        super().__init__("Confidence")
        self.min_confidence = min_confidence

    def apply(self, signal: Signal) -> tuple[bool, str]:
        if signal.confidence < self.min_confidence:
            return False, f"confidence={signal.confidence:.3f} < {self.min_confidence}"
        return True, "passed"


class EdgeFilter(SignalFilter):
    """Filter signals based on expected edge."""

    def __init__(self, min_edge: float = 0.001):
        super().__init__("Edge")
        self.min_edge = min_edge

    def apply(self, signal: Signal) -> tuple[bool, str]:
        if signal.expected_edge < self.min_edge:
            return False, f"edge={signal.expected_edge:.5f} < {self.min_edge}"
        return True, "passed"


class FilterChain:
    """Chain multiple filters together."""

    def __init__(self):
        self.filters: list[SignalFilter] = []

    def add(self, filter_: SignalFilter) -> "FilterChain":
        self.filters.append(filter_)
        return self

    def apply(self, signal: Signal) -> Signal:
        """Apply all filters to signal."""
        for filter_ in self.filters:
            passed, reason = filter_.apply(signal)
            if passed:
                signal.filters_passed.append(f"{filter_.name}:{reason}")
            else:
                signal.filters_failed.append(f"{filter_.name}:{reason}")
                logger.debug(f"Filter {filter_.name} failed: {reason}")

        return signal

    def is_valid(self, signal: Signal) -> bool:
        """Check if signal passed all filters."""
        return len(signal.filters_failed) == 0


def create_default_filter_chain(
    ofi_threshold: float = 0.7,
    min_confidence: float = 0.3,
    min_edge: float = 0.001
) -> FilterChain:
    """Create default filter chain."""
    return (
        FilterChain()
        .add(OFIFilter(ofi_threshold))
        .add(SpreadFilter())
        .add(DepthFilter())
        .add(ConfidenceFilter(min_confidence))
        .add(EdgeFilter(min_edge))
    )
