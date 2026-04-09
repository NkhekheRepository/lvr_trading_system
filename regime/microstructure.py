"""
Market Microstructure Detector - Analyze order book dynamics and trade patterns.
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional
from collections import deque
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class MicrostructureState:
    bid_depth: float
    ask_depth: float
    spread_bps: float
    ofi_slope: float
   TradeIntensity: float
    short_term_vol: float
    liquidity_regime: str
    pressure_index: float
    resilience: float
    timestamp: datetime


class MicrostructureDetector:
    """
    Real-time market microstructure analysis.
    
    Detects:
    - Order flow imbalance
    - Liquidity regimes (abundant/normal/scarce)
    - Trade intensity patterns
    - Market pressure
    - Liquidity resilience
    """
    
    def __init__(
        self,
        symbol: str,
        window_size: int = 100,
        ofi_decay: float = 0.95,
        resilience_window: int = 50,
    ):
        self.symbol = symbol
        
        self.bid_depth_history = deque(maxlen=window_size)
        self.ask_depth_history = deque(maxlen=window_size)
        self.spread_history = deque(maxlen=window_size)
        self.ofi_history = deque(maxlen=window_size)
        self.volume_history = deque(maxlen=window_size)
        
        self.trade_sizes = deque(maxlen=window_size)
        self.trade_intervals = deque(maxlen=window_size)
        self.last_trade_time: Optional[datetime] = None
        
        self.ofi_decay = ofi_decay
        self.resilience_window = resilience_window
        
        self.baseline_depth: Optional[float] = None
        self.baseline_spread: Optional[float] = None
        
    def update(
        self,
        bid_price: float,
        ask_price: float,
        bid_volume: float,
        ask_volume: float,
        trade_price: Optional[float] = None,
        trade_volume: Optional[float] = None,
        timestamp: Optional[datetime] = None,
    ) -> MicrostructureState:
        """
        Update with new market data.
        
        Returns current microstructure state.
        """
        timestamp = timestamp or datetime.now()
        
        spread = (ask_price - bid_price) / bid_price * 10000
        self.spread_history.append(spread)
        
        bid_depth = bid_price * bid_volume
        ask_depth = ask_price * ask_volume
        self.bid_depth_history.append(bid_depth)
        self.ask_depth_history.append(ask_depth)
        
        ofi = self._compute_ofi(bid_depth, ask_depth)
        self.ofi_history.append(ofi)
        
        ofi_slope = self._compute_ofi_slope()
        
        if self.baseline_depth is None and len(self.bid_depth_history) >= 20:
            self.baseline_depth = np.median(list(self.bid_depth_history))
            self.baseline_spread = np.median(list(self.spread_history))
            
        if trade_volume is not None and trade_price is not None:
            self.trade_sizes.append(trade_volume)
            
            if self.last_trade_time is not None:
                interval = (timestamp - self.last_trade_time).total_seconds()
                self.trade_intervals.append(interval)
                
            self.last_trade_time = timestamp
            
        trade_intensity = self._compute_trade_intensity()
        short_term_vol = self._compute_short_term_volatility()
        
        liquidity_regime = self._classify_liquidity_regime()
        
        pressure_index = self._compute_pressure_index()
        
        resilience = self._compute_resilience()
        
        return MicrostructureState(
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            spread_bps=spread,
            ofi_slope=ofi_slope,
            trade_intensity=trade_intensity,
            short_term_vol=short_term_vol,
            liquidity_regime=liquidity_regime,
            pressure_index=pressure_index,
            resilience=resilience,
            timestamp=timestamp,
        )
        
    def _compute_ofi(self, bid_depth: float, ask_depth: float) -> float:
        """Compute Order Flow Imbalance."""
        if not self.bid_depth_history:
            return 0.0
            
        prev_bid = self.bid_depth_history[-1]
        prev_ask = self.ask_depth_history[-1] if self.ask_depth_history else 0
        
        bid_change = bid_depth - prev_bid
        ask_change = ask_depth - prev_ask
        
        ofi = (bid_change - ask_change) / max(bid_depth + ask_depth, 1)
        
        return ofi * self.ofi_decay
        
    def _compute_ofi_slope(self) -> float:
        """Compute trend in order flow imbalance."""
        if len(self.ofi_history) < 5:
            return 0.0
            
        ofi_values = list(self.ofi_history)[-5:]
        
        trend = np.polyfit(range(len(ofi_values)), ofi_values, 1)[0]
        
        return trend * 100
        
    def _compute_trade_intensity(self) -> float:
        """Compute current trade intensity (trades per second)."""
        if len(self.trade_intervals) < 2:
            return 1.0
            
        avg_interval = np.mean(list(self.trade_intervals))
        
        return 1.0 / max(avg_interval, 0.001)
        
    def _compute_short_term_volatility(self) -> float:
        """Compute short-term volatility from trade-to-trade returns."""
        if len(self.volume_history) < 3:
            return 0.0
            
        volumes = np.array(list(self.volume_history))
        returns = np.diff(volumes) / volumes[:-1]
        
        return np.std(returns) if len(returns) > 0 else 0.0
        
    def _classify_liquidity_regime(self) -> str:
        """Classify current liquidity regime."""
        if len(self.bid_depth_history) < 10:
            return 'unknown'
            
        current_depth = np.mean(list(self.bid_depth_history))
        
        if self.baseline_depth is None:
            return 'normal'
            
        depth_ratio = current_depth / self.baseline_depth
        
        if depth_ratio > 1.5:
            return 'abundant'
        elif depth_ratio < 0.5:
            return 'scarce'
        else:
            return 'normal'
            
    def _compute_pressure_index(self) -> float:
        """Compute market pressure index (-1 to 1)."""
        if len(self.spread_history) < 5:
            return 0.0
            
        current_spread = self.spread_history[-1]
        
        if self.baseline_spread is None:
            baseline = np.mean(list(self.spread_history))
        else:
            baseline = self.baseline_spread
            
        spread_ratio = current_spread / max(baseline, 1e-6)
        
        if len(self.ofi_history) < 5:
            ofi_avg = 0.0
        else:
            ofi_avg = np.mean(list(self.ofi_history)[-5:])
            
        pressure = (spread_ratio - 1.0) * 0.5 - ofi_avg
        
        return np.clip(pressure, -1.0, 1.0)
        
    def _compute_resilience(self) -> float:
        """Compute liquidity resilience (0-1)."""
        if len(self.bid_depth_history) < self.resilience_window:
            return 0.5
            
        recent = list(self.bid_depth_history)[-self.resilience_window:]
        
        baseline = np.median(recent)
        
        current = recent[-1]
        
        if baseline == 0:
            return 0.5
            
        resilience = current / baseline
        
        return np.clip(resilience, 0.0, 2.0) / 2.0
        
    def get_execution_quality_score(self) -> float:
        """
        Get overall execution quality score (0-1).
        
        Factors:
        - Spread relative to baseline
        - Depth relative to baseline
        - Resilience
        - Pressure (lower is better when negative)
        """
        if len(self.spread_history) < 5 or self.baseline_spread is None:
            return 0.5
            
        spread_score = min(self.baseline_spread / max(self.spread_history[-1], 1e-6), 2.0) / 2.0
        
        depth_score = 0.5
        if self.baseline_depth is not None and len(self.bid_depth_history) > 0:
            current_depth = np.mean(list(self.bid_depth_history)[-5:])
            depth_score = min(current_depth / max(self.baseline_depth, 1e-6), 2.0) / 2.0
            
        resilience_score = self._compute_resilience()
        
        pressure_score = 1.0 - abs(self._compute_pressure_index())
        
        quality = (
            spread_score * 0.3 +
            depth_score * 0.3 +
            resilience_score * 0.2 +
            pressure_score * 0.2
        )
        
        return np.clip(quality, 0.0, 1.0)
