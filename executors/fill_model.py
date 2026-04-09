"""
Fill Model - Predictive slippage modeling for execution quality estimation.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class FillPrediction:
    expected_slippage_bps: float
    confidence: float
    fill_rate: float
    expected_cost_bps: float
    latency_ms: float


class FillPredictor:
    """
    Predictive fill model using microstructure features.
    Combines time-series patterns with real-time market microstructure.
    """
    
    def __init__(
        self,
        symbol: str,
        lookback_bars: int = 1000,
        liquidity_weight: float = 0.4,
        volatility_weight: float = 0.3,
        momentum_weight: float = 0.3,
    ):
        self.symbol = symbol
        self.lookback_bars = lookback_bars
        self.liquidity_weight = liquidity_weight
        self.volatility_weight = volatility_weight
        self.momentum_weight = momentum_weight
        
        self.history: list[dict] = []
        self.baseline_slippage: dict[str, float] = {}
        
    def update(self, fill_data: dict) -> None:
        """Update model with actual fill data."""
        self.history.append({
            'timestamp': datetime.now(),
            **fill_data
        })
        
        if len(self.history) > self.lookback_bars:
            self.history.pop(0)
            
        self._recompute_baseline()
        
    def _recompute_baseline(self) -> None:
        """Recompute baseline slippage estimates from history."""
        if len(self.history) < 50:
            return
            
        df = pd.DataFrame(self.history)
        
        self.baseline_slippage = {
            'mean': df['slippage_bps'].mean(),
            'std': df['slippage_bps'].std(),
            'p25': df['slippage_bps'].quantile(0.25),
            'p75': df['slippage_bps'].quantile(0.75),
            'fill_rate': df['fill_rate'].mean(),
        }
        
    def predict(
        self,
        order_size: float,
        market_depth: float,
        spread_bps: float,
        recent_volatility: float,
        ofi_slope: float,
        urgency: float = 0.5,
    ) -> FillPrediction:
        """
        Predict fill metrics for an order.
        
        Args:
            order_size: Order size in base currency
            market_depth: Available liquidity at top of book
            spread_bps: Current bid-ask spread in basis points
            recent_volatility: Recent realized volatility
            ofi_slope: Order flow imbalance slope
            urgency: Execution urgency 0-1 (1 = aggressive)
            
        Returns:
            FillPrediction with expected slippage, confidence, etc.
        """
        size_ratio = order_size / max(market_depth, 1e-9)
        
        size_impact = self._size_impact_model(size_ratio, urgency)
        
        spread_component = spread_bps / 2
        
        vol_component = self._volatility_adjustment(recent_volatility)
        
        ofi_component = self._ofi_adjustment(ofi_slope)
        
        base_slippage = (
            self.liquidity_weight * size_impact +
            self.volatility_weight * vol_component +
            self.momentum_weight * ofi_component
        )
        
        expected_slippage = base_slippage + spread_component
        
        confidence = self._compute_confidence(size_ratio, urgency)
        
        fill_rate = self._estimate_fill_rate(size_ratio, market_depth)
        
        latency_ms = self._estimate_latency(size_ratio, urgency)
        
        expected_cost = expected_slippage + spread_component
        
        return FillPrediction(
            expected_slippage_bps=expected_slippage,
            confidence=confidence,
            fill_rate=fill_rate,
            expected_cost_bps=expected_cost,
            latency_ms=latency_ms,
        )
        
    def _size_impact_model(self, size_ratio: float, urgency: float) -> float:
        """Model slippage from order size relative to depth."""
        if size_ratio < 0.01:
            return 0.0
            
        impact_base = 0.5 * size_ratio ** 1.5
        
        urgency_multiplier = 1.0 + 0.5 * urgency
        
        return impact_base * urgency_multiplier
        
    def _volatility_adjustment(self, volatility: float) -> float:
        """Adjust for current volatility regime."""
        vol_normalized = volatility / max(self.baseline_slippage.get('std', 1e-6), 1e-6)
        
        if vol_normalized > 2.0:
            return 3.0
        elif vol_normalized > 1.5:
            return 1.5
        elif vol_normalized < 0.5:
            return 0.5
        return 1.0
        
    def _ofi_adjustment(self, ofi_slope: float) -> float:
        """Adjust for order flow imbalance."""
        if ofi_slope > 2.0:
            return 2.0
        elif ofi_slope < -2.0:
            return -1.0
        return 0.0
        
    def _compute_confidence(self, size_ratio: float, urgency: float) -> float:
        """Compute prediction confidence based on data quality."""
        base_confidence = min(len(self.history) / 200, 1.0)
        
        if size_ratio > 0.5:
            base_confidence *= 0.7
        elif size_ratio > 0.2:
            base_confidence *= 0.85
            
        if len(self.baseline_slippage) == 0:
            return 0.5
            
        return base_confidence
        
    def _estimate_fill_rate(self, size_ratio: float, market_depth: float) -> float:
        """Estimate probability of full fill."""
        if market_depth <= 0:
            return 0.0
            
        depth_ratio = size_ratio / (size_ratio + market_depth)
        
        if depth_ratio < 0.1:
            return 0.99
        elif depth_ratio < 0.3:
            return 0.95
        elif depth_ratio < 0.5:
            return 0.85
        else:
            return 0.70
            
    def _estimate_latency(self, size_ratio: float, urgency: float) -> float:
        """Estimate execution latency in milliseconds."""
        base_latency = 50
        
        if urgency > 0.8:
            return base_latency
        elif urgency > 0.5:
            return base_latency * 2
        else:
            return base_latency * 4 * (1 + size_ratio)


class AdaptiveFillModel(FillPredictor):
    """
    Adaptive fill model with online learning.
    Updates parameters based on realized execution quality.
    """
    
    def __init__(
        self,
        symbol: str,
        learning_rate: float = 0.01,
        **kwargs
    ):
        super().__init__(symbol, **kwargs)
        self.learning_rate = learning_rate
        
        self.weights = np.array([
            kwargs.get('liquidity_weight', 0.4),
            kwargs.get('volatility_weight', 0.3),
            kwargs.get('momentum_weight', 0.3),
        ])
        
    def update_weights(self, predicted: float, actual: float) -> None:
        """Update model weights using prediction error."""
        error = actual - predicted
        
        gradient = np.array([
            self._compute_gradient_component(0, predicted, actual),
            self._compute_gradient_component(1, predicted, actual),
            self._compute_gradient_component(2, predicted, actual),
        ])
        
        self.weights += self.learning_rate * error * gradient
        
        self.weights = np.clip(self.weights, 0.1, 0.9)
        self.weights /= self.weights.sum()
        
    def _compute_gradient_component(
        self, 
        idx: int, 
        predicted: float, 
        actual: float
    ) -> np.ndarray:
        """Compute gradient for a weight component."""
        gradients = np.zeros(3)
        gradients[idx] = 1.0
        return gradients
