"""
Spread Analyzer - Spread expansion and contraction analysis.
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional
from collections import deque
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class SpreadFeatures:
    spread_bps: float
    spread_normalized: float
    spread_expansion_rate: float
    spread_volatility: float
    mid_price_trend: float
    spread_skew: float
    relative_spread: float
    spread_regime: str
    timestamp: datetime


class SpreadAnalyzer:
    """
    Analyzes bid-ask spread dynamics.
    
    Features:
    - Current spread in bps
    - Normalized spread (relative to historical)
    - Expansion/contraction rate
    - Spread volatility
    - Mid-price trend
    - Spread skew
    - Regime detection
    """
    
    def __init__(
        self,
        symbol: str,
        window_size: int = 100,
        regime_window: int = 50,
    ):
        self.symbol = symbol
        
        self.window_size = window_size
        self.regime_window = regime_window
        
        self.spread_history: deque[float] = deque(maxlen=window_size)
        self.mid_price_history: deque[float] = deque(maxlen=window_size)
        self.bid_history: deque[float] = deque(maxlen=window_size)
        self.ask_history: deque[float] = deque(maxlen=window_size)
        
        self.baseline_spread: Optional[float] = None
        self.baseline_std: Optional[float] = None
        
        self.last_update: Optional[datetime] = None
        
    def update(
        self,
        bid: float,
        ask: float,
        timestamp: Optional[datetime] = None,
    ) -> SpreadFeatures:
        """
        Update with new quote.
        
        Returns SpreadFeatures with computed metrics.
        """
        timestamp = timestamp or datetime.now()
        
        spread = ask - bid
        spread_bps = (spread / bid) * 10000
        
        mid = (bid + ask) / 2
        
        self.spread_history.append(spread_bps)
        self.mid_price_history.append(mid)
        self.bid_history.append(bid)
        self.ask_history.append(ask)
        
        self._update_baseline()
        
        spread_normalized = self._normalize_spread(spread_bps)
        
        expansion_rate = self._compute_expansion_rate()
        
        spread_volatility = self._compute_spread_volatility()
        
        mid_trend = self._compute_mid_trend()
        
        spread_skew = self._compute_spread_skew()
        
        relative_spread = self._compute_relative_spread(bid, ask)
        
        regime = self._classify_regime(spread_bps)
        
        return SpreadFeatures(
            spread_bps=spread_bps,
            spread_normalized=spread_normalized,
            spread_expansion_rate=expansion_rate,
            spread_volatility=spread_volatility,
            mid_price_trend=mid_trend,
            spread_skew=spread_skew,
            relative_spread=relative_spread,
            spread_regime=regime,
            timestamp=timestamp,
        )
        
    def _update_baseline(self) -> None:
        """Update baseline statistics."""
        if len(self.spread_history) >= self.regime_window:
            recent = list(self.spread_history)[-self.regime_window:]
            
            if self.baseline_spread is None:
                self.baseline_spread = np.median(recent)
                self.baseline_std = np.std(recent)
            else:
                self.baseline_spread = 0.95 * self.baseline_spread + 0.05 * np.median(recent)
                self.baseline_std = 0.95 * self.baseline_std + 0.05 * np.std(recent)
                
    def _normalize_spread(self, spread_bps: float) -> float:
        """Normalize spread relative to baseline."""
        if self.baseline_std is None or self.baseline_std < 0.01:
            return 0.0
            
        return (spread_bps - self.baseline_spread) / self.baseline_std
        
    def _compute_expansion_rate(self) -> float:
        """Compute rate of spread expansion."""
        if len(self.spread_history) < 5:
            return 0.0
            
        recent = list(self.spread_history)[-5:]
        
        expansion = recent[-1] - recent[0]
        
        if self.baseline_std and self.baseline_std > 0:
            return expansion / self.baseline_std
            
        return expansion
        
    def _compute_spread_volatility(self) -> float:
        """Compute volatility of spread changes."""
        if len(self.spread_history) < 10:
            return 0.0
            
        recent = list(self.spread_history)[-20:]
        spread_changes = np.diff(recent)
        
        return np.std(spread_changes)
        
    def _compute_mid_trend(self) -> float:
        """Compute trend in mid price."""
        if len(self.mid_price_history) < 5:
            return 0.0
            
        recent = list(self.mid_price_history)[-5:]
        returns = np.diff(recent) / recent[:-1]
        
        return np.sum(returns)
        
    def _compute_spread_skew(self) -> float:
        """Compute spread skew (asymmetric expansion)."""
        if len(self.spread_history) < 10:
            return 0.0
            
        recent = list(self.spread_history)[-10:]
        median = np.median(recent)
        
        above_median = [s for s in recent if s > median]
        below_median = [s for s in recent if s <= median]
        
        if not above_median or not below_median:
            return 0.0
            
        mean_above = np.mean(above_median)
        mean_below = np.mean(below_median)
        
        if median == 0:
            return 0.0
            
        skew = (mean_above - mean_below) / median
        
        return np.clip(skew, -2, 2)
        
    def _compute_relative_spread(self, bid: float, ask: float) -> float:
        """Compute spread relative to price level."""
        if len(self.mid_price_history) < 2:
            return 1.0
            
        avg_price = np.mean(list(self.mid_price_history))
        
        if avg_price < 1e-9:
            return 1.0
            
        current_spread = (ask - bid) / avg_price
        baseline_spread_rel = self.baseline_spread / 10000 if self.baseline_spread else 0
        
        if baseline_spread_rel < 1e-9:
            return 1.0
            
        return current_spread / baseline_spread_rel
        
    def _classify_regime(self, spread_bps: float) -> str:
        """Classify current spread regime."""
        if self.baseline_spread is None:
            return 'unknown'
            
        ratio = spread_bps / self.baseline_spread
        
        if ratio > 3.0:
            return 'crisis'
        elif ratio > 2.0:
            return 'stress'
        elif ratio > 1.5:
            return 'wide'
        elif ratio < 0.5:
            return 'tight'
        else:
            return 'normal'
            
    def get_expansion_signal(self) -> int:
        """
        Get signal from spread expansion.
        
        Returns: 1 (spreads expanding), -1 (spreads contracting), 0 (stable)
        """
        if len(self.spread_history) < 5:
            return 0
            
        rate = self._compute_expansion_rate()
        
        if rate > 0.5:
            return -1
        elif rate < -0.5:
            return 1
        else:
            return 0
            
    def is_spread_anomaly(self) -> bool:
        """Check if current spread is anomalous."""
        if len(self.spread_history) < 20:
            return False
            
        recent = list(self.spread_history)[-20:]
        
        current = self.spread_history[-1]
        
        z_score = (current - np.mean(recent)) / max(np.std(recent), 0.01)
        
        return abs(z_score) > 3.0
        
    def estimate_execution_cost(self, order_size_pct: float) -> float:
        """
        Estimate execution cost based on spread and order size.
        
        Args:
            order_size_pct: Order size as % of typical volume
            
        Returns:
            Estimated cost in bps
        """
        if len(self.spread_history) < 5:
            return 5.0
            
        base_spread = np.mean(list(self.spread_history)[-5:])
        
        size_impact = 0.5 * order_size_pct ** 1.5
        
        total_cost = base_spread / 2 + size_impact
        
        return total_cost
