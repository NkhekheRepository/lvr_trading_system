"""
Combined Microstructure Features - Unified feature extraction and registry.
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Optional, Callable
from datetime import datetime
from collections import deque
import logging

from .ofi import OFIAccumulator, OFIFeatures
from .spread import SpreadAnalyzer, SpreadFeatures
from .liquidity_vacuum import LiquidityVacuumDetector, VacuumSignal

logger = logging.getLogger(__name__)


@dataclass
class CombinedFeatures:
    ofi: OFIFeatures
    spread: SpreadFeatures
    vacuum: VacuumSignal
    
    combined_signal: int
    signal_confidence: float
    
    execution_quality: float
    liquidity_score: float
    
    regime_flags: list[str]
    risk_flags: list[str]
    
    timestamp: datetime


class FeatureRegistry:
    """
    Registry for managing feature extractors.
    """
    
    def __init__(self):
        self.extractors: dict[str, Callable] = {}
        self.feature_history: dict[str, deque] = {}
        
    def register(self, name: str, extractor: Callable) -> None:
        """Register a feature extractor."""
        self.extractors[name] = extractor
        self.feature_history[name] = deque(maxlen=1000)
        
    def record(self, name: str, value: any) -> None:
        """Record a feature value."""
        if name in self.feature_history:
            self.feature_history[name].append(value)
            
    def get_history(self, name: str, n: int = 100) -> list:
        """Get recent feature history."""
        if name not in self.feature_history:
            return []
        return list(self.feature_history[name])[-n:]
        
    def get_latest(self, name: str) -> Optional[any]:
        """Get most recent feature value."""
        history = self.get_history(name, 1)
        return history[0] if history else None


class MicrostructureFeatures:
    """
    Unified microstructure feature extractor.
    
    Combines:
    - Order Flow Imbalance (OFI)
    - Spread analysis
    - Liquidity vacuum detection
    
    Produces:
    - Combined trading signals
    - Execution quality scores
    - Risk flags
    """
    
    def __init__(
        self,
        symbol: str,
        ofi_window: int = 100,
        spread_window: int = 100,
        vacuum_threshold: float = 0.3,
    ):
        self.symbol = symbol
        
        self.ofi = OFIAccumulator(symbol, window_size=ofi_window)
        self.spread = SpreadAnalyzer(symbol, window_size=spread_window)
        self.vacuum = LiquidityVacuumDetector(
            symbol,
            window_size=100,
            depth_threshold=vacuum_threshold,
        )
        
        self.registry = FeatureRegistry()
        
        self.signal_history: deque[int] = deque(maxlen=100)
        self.confidence_history: deque[float] = deque(maxlen=100)
        
        self.baseline_execution_quality: Optional[float] = None
        
    def update(
        self,
        bid_levels: list[tuple[float, float]],
        ask_levels: list[tuple[float, float]],
        volume: float = 0.0,
        timestamp: Optional[datetime] = None,
    ) -> CombinedFeatures:
        """
        Update with order book and trade data.
        
        Returns CombinedFeatures with all computed metrics.
        """
        timestamp = timestamp or datetime.now()
        
        bid = bid_levels[0][0] if bid_levels else 0
        ask = ask_levels[0][0] if ask_levels else 0
        
        ofi_features = self.ofi.update(bid_levels, ask_levels, timestamp)
        
        spread_features = self.spread.update(bid, ask, timestamp)
        
        ofi_value = ofi_features.ofi_raw if ofi_features else 0
        
        vacuum_signal = self.vacuum.update(
            bid_levels,
            ask_levels,
            volume=volume,
            ofi=ofi_value,
            timestamp=timestamp,
        )
        
        combined_signal, signal_confidence = self._compute_combined_signal(
            ofi_features,
            spread_features,
            vacuum_signal,
        )
        
        execution_quality = self._compute_execution_quality(
            spread_features,
            vacuum_signal,
        )
        
        liquidity_score = self._compute_liquidity_score(
            bid_levels,
            ask_levels,
            vacuum_signal,
        )
        
        regime_flags = self._detect_regime_flags(
            ofi_features,
            spread_features,
            vacuum_signal,
        )
        
        risk_flags = self._detect_risk_flags(
            spread_features,
            vacuum_signal,
            execution_quality,
        )
        
        self.registry.record('combined_signal', combined_signal)
        self.registry.record('confidence', signal_confidence)
        self.registry.record('execution_quality', execution_quality)
        self.registry.record('liquidity_score', liquidity_score)
        
        self.signal_history.append(combined_signal)
        self.confidence_history.append(signal_confidence)
        
        return CombinedFeatures(
            ofi=ofi_features,
            spread=spread_features,
            vacuum=vacuum_signal,
            combined_signal=combined_signal,
            signal_confidence=signal_confidence,
            execution_quality=execution_quality,
            liquidity_score=liquidity_score,
            regime_flags=regime_flags,
            risk_flags=risk_flags,
            timestamp=timestamp,
        )
        
    def _compute_combined_signal(
        self,
        ofi: OFIFeatures,
        spread: SpreadFeatures,
        vacuum: VacuumSignal,
    ) -> tuple[int, float]:
        """Compute combined directional signal."""
        signals = []
        weights = []
        
        ofi_signal = self.ofi.get_trend_signal()
        ofi_accel = self.ofi.get_acceleration_signal()
        
        if ofi_signal != 0:
            signals.append(ofi_signal)
            weights.append(0.4)
        if ofi_accel != 0:
            signals.append(ofi_accel)
            weights.append(0.2)
            
        vacuum_dir, vacuum_conf = self.vacuum.get_trading_signal()
        if vacuum_dir != 0 and vacuum_conf > 0.5:
            signals.append(vacuum_dir)
            weights.append(vacuum_conf * 0.3)
            
        spread_expansion = self.spread.get_expansion_signal()
        if spread_expansion != 0:
            signals.append(-spread_expansion)
            weights.append(0.1)
            
        if not signals:
            return 0, 0.0
            
        total_weight = sum(weights)
        weights = [w / total_weight for w in weights]
        
        weighted_signal = sum(s * w for s, w in zip(signals, weights))
        
        if weighted_signal > 0.3:
            direction = 1
        elif weighted_signal < -0.3:
            direction = -1
        else:
            direction = 0
            
        confidence = min(abs(weighted_signal), 1.0) * 0.8 + 0.2
        
        if vacuum.imminent and vacuum.confidence > 0.7:
            confidence *= 0.8
            
        return direction, confidence
        
    def _compute_execution_quality(
        self,
        spread: SpreadFeatures,
        vacuum: VacuumSignal,
    ) -> float:
        """Compute execution quality score (0-1)."""
        base_quality = 1.0
        
        if spread.spread_regime == 'crisis':
            base_quality *= 0.3
        elif spread.spread_regime == 'stress':
            base_quality *= 0.5
        elif spread.spread_regime == 'wide':
            base_quality *= 0.7
            
        base_quality *= (1.0 - vacuum.intensity * 0.5)
        
        if spread.spread_normalized > 2:
            base_quality *= 0.6
        elif spread.spread_normalized < -1:
            base_quality *= 1.2
            
        if self.baseline_execution_quality is None:
            self.baseline_execution_quality = base_quality
        else:
            self.baseline_execution_quality = (
                0.95 * self.baseline_execution_quality + 0.05 * base_quality
            )
            
        return max(0.0, min(base_quality, 1.0))
        
    def _compute_liquidity_score(
        self,
        bid_levels: list[tuple[float, float]],
        ask_levels: list[tuple[float, float]],
        vacuum: VacuumSignal,
    ) -> float:
        """Compute liquidity score (0-1)."""
        if not bid_levels or not ask_levels:
            return 0.0
            
        bid_depth = sum(v for _, v in bid_levels[:5])
        ask_depth = sum(v for _, v in ask_levels[:5])
        
        total_depth = bid_depth + ask_depth
        
        depth_score = min(total_depth / 100000, 1.0)
        
        imbalance = abs(bid_depth - ask_depth) / max(total_depth, 1)
        imbalance_score = 1.0 - imbalance
        
        vacuum_penalty = vacuum.intensity * 0.4
        
        liquidity = (
            depth_score * 0.4 +
            imbalance_score * 0.3 +
            (1.0 - vacuum_penalty) * 0.3
        )
        
        return max(0.0, min(liquidity, 1.0))
        
    def _detect_regime_flags(
        self,
        ofi: OFIFeatures,
        spread: SpreadFeatures,
        vacuum: VacuumSignal,
    ) -> list[str]:
        """Detect regime flags."""
        flags = []
        
        if vacuum.imminent:
            flags.append('vacuum_imminent')
            
        if spread.spread_regime in ['crisis', 'stress']:
            flags.append(f'spread_{spread.spread_regime}')
            
        if ofi.ofi_acceleration > 2:
            flags.append('ofi_acceleration_up')
        elif ofi.ofi_acceleration < -2:
            flags.append('ofi_acceleration_down')
            
        if abs(ofi.imbalance_ratio) > 0.7:
            flags.append('heavy_imbalance')
            
        return flags
        
    def _detect_risk_flags(
        self,
        spread: SpreadFeatures,
        vacuum: VacuumSignal,
        execution_quality: float,
    ) -> list[str]:
        """Detect risk flags."""
        flags = []
        
        if execution_quality < 0.4:
            flags.append('low_execution_quality')
            
        if vacuum.confidence > 0.8 and vacuum.imminent:
            flags.append('high_vacuum_risk')
            
        if spread.spread_volatility > 2:
            flags.append('high_spread_volatility')
            
        if self.spread.is_spread_anomaly():
            flags.append('spread_anomaly')
            
        return flags
        
    def get_trade_recommendation(
        self,
        signal_threshold: float = 0.5,
    ) -> tuple[int, float, dict]:
        """
        Get trade recommendation based on features.
        
        Returns: (direction, size_multiplier, metadata)
        """
        if len(self.signal_history) < 5:
            return 0, 0.0, {}
            
        recent_signals = list(self.signal_history)[-5:]
        recent_confidence = list(self.confidence_history)[-5:]
        
        avg_signal = np.mean(recent_signals)
        avg_confidence = np.mean(recent_confidence)
        
        if abs(avg_signal) < 0.3 or avg_confidence < signal_threshold:
            return 0, 0.0, {'reason': 'insufficient_signal'}
            
        direction = 1 if avg_signal > 0 else -1
        
        base_size = avg_confidence * 0.5
        
        quality = self.registry.get_latest('execution_quality')
        if quality is not None:
            base_size *= quality
            
        liquidity = self.registry.get_latest('liquidity_score')
        if liquidity is not None:
            base_size *= liquidity
            
        base_size = max(0.1, min(base_size, 1.0))
        
        metadata = {
            'signal_strength': abs(avg_signal),
            'confidence': avg_confidence,
            'execution_quality': quality,
            'liquidity_score': liquidity,
        }
        
        return direction, base_size, metadata
