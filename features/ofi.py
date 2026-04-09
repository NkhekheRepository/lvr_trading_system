"""
OFI Accumulator - Order Flow Imbalance with acceleration detection.

OFI measures the net order flow at each price level, capturing
the directional pressure from order book dynamics.
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional
from collections import deque
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class OFIFeatures:
    ofi_raw: float
    ofi_normalized: float
    ofi_acceleration: float
    ofi_momentum: float
    bid_pressure: float
    ask_pressure: float
    imbalance_ratio: float
    depth_change_bid: float
    depth_change_ask: float
    timestamp: datetime


class OFIAccumulator:
    """
    Accumulates and analyzes Order Flow Imbalance (OFI).
    
    Features computed:
    - Raw OFI: Net change in depth-weighted orders
    - Normalized OFI: OFI relative to total depth
    - OFI Acceleration: Rate of change in OFI
    - OFI Momentum: EMA of OFI
    - Bid/Ask Pressure: Relative aggression
    - Depth Changes: Volume dynamics at each side
    """
    
    def __init__(
        self,
        symbol: str,
        window_size: int = 100,
        ema_span: int = 20,
        normalization_window: int = 50,
    ):
        self.symbol = symbol
        
        self.window_size = window_size
        self.ema_span = ema_span
        self.normalization_window = normalization_window
        
        self.bid_depth_history: deque[float] = deque(maxlen=window_size)
        self.ask_depth_history: deque[float] = deque(maxlen=window_size)
        
        self.bid_levels: deque[list[tuple[float, float]]] = deque(maxlen=10)
        self.ask_levels: deque[list[tuple[float, float]]] = deque(maxlen=10)
        
        self.ofi_history: deque[float] = deque(maxlen=window_size)
        self.timestamp_history: deque[datetime] = deque(maxlen=window_size)
        
        self.ema_ofi: Optional[float] = None
        self.alpha = 2.0 / (ema_span + 1)
        
        self.baseline_ofi_std: Optional[float] = None
        
    def update(
        self,
        bid_levels: list[tuple[float, float]],
        ask_levels: list[tuple[float, float]],
        timestamp: Optional[datetime] = None,
    ) -> OFIFeatures:
        """
        Update with new order book state.
        
        Args:
            bid_levels: List of (price, volume) tuples for bids
            ask_levels: List of (price, volume) tuples for asks
            
        Returns:
            OFIFeatures with computed metrics
        """
        timestamp = timestamp or datetime.now()
        
        bid_depth = sum(v for _, v in bid_levels)
        ask_depth = sum(v for _, v in ask_levels)
        
        self.bid_depth_history.append(bid_depth)
        self.ask_depth_history.append(ask_depth)
        
        self.bid_levels.append(bid_levels)
        self.ask_levels.append(ask_levels)
        
        ofi_raw = self._compute_ofi(bid_levels, ask_levels)
        
        ofi_normalized = self._normalize_ofi(ofi_raw)
        
        ofi_acceleration = self._compute_acceleration(ofi_raw)
        
        ofi_momentum = self._compute_momentum(ofi_raw)
        
        bid_pressure, ask_pressure = self._compute_pressure()
        
        imbalance_ratio = self._compute_imbalance_ratio()
        
        depth_change_bid = self._compute_depth_change(bid_depth, is_bid=True)
        depth_change_ask = self._compute_depth_change(ask_depth, is_bid=False)
        
        self.ofi_history.append(ofi_raw)
        self.timestamp_history.append(timestamp)
        
        if self.baseline_ofi_std is None and len(self.ofi_history) >= self.normalization_window:
            self.baseline_ofi_std = np.std(list(self.ofi_history))
            
        return OFIFeatures(
            ofi_raw=ofi_raw,
            ofi_normalized=ofi_normalized,
            ofi_acceleration=ofi_acceleration,
            ofi_momentum=ofi_momentum,
            bid_pressure=bid_pressure,
            ask_pressure=ask_pressure,
            imbalance_ratio=imbalance_ratio,
            depth_change_bid=depth_change_bid,
            depth_change_ask=depth_change_ask,
            timestamp=timestamp,
        )
        
    def _compute_ofi(
        self,
        bid_levels: list[tuple[float, float]],
        ask_levels: list[tuple[float, float]],
    ) -> float:
        """Compute depth-weighted OFI."""
        if len(self.bid_levels) == 0 or len(self.ask_levels) == 0:
            return 0.0
            
        prev_bids = self.bid_levels[-1]
        prev_asks = self.ask_levels[-1]
        
        bid_ofi = self._level_ofi(bid_levels, prev_bids)
        ask_ofi = self._level_ofi(ask_levels, prev_asks)
        
        return bid_ofi - ask_ofi
        
    def _level_ofi(
        self,
        current: list[tuple[float, float]],
        previous: list[tuple[float, float]],
    ) -> float:
        """Compute OFI at each price level."""
        current_dict = {round(p, 2): v for p, v in current}
        previous_dict = {round(p, 2): v for p, v in previous}
        
        all_prices = set(current_dict.keys()) | set(previous_dict.keys())
        
        ofi = 0.0
        for price in all_prices:
            curr_vol = current_dict.get(price, 0)
            prev_vol = previous_dict.get(price, 0)
            ofi += (curr_vol - prev_vol)
            
        return ofi
        
    def _normalize_ofi(self, ofi_raw: float) -> float:
        """Normalize OFI by recent volatility."""
        if len(self.ofi_history) < 10:
            return 0.0
            
        recent_std = np.std(list(self.ofi_history))
        
        if recent_std < 1e-9:
            return 0.0
            
        return ofi_raw / recent_std
        
    def _compute_acceleration(self, ofi_raw: float) -> float:
        """Compute acceleration in OFI changes."""
        if len(self.ofi_history) < 3:
            return 0.0
            
        recent = list(self.ofi_history)[-3:]
        ofi_changes = np.diff(recent)
        
        if len(ofi_changes) < 2:
            return 0.0
            
        acceleration = np.mean(np.diff(ofi_changes))
        
        if self.baseline_ofi_std and self.baseline_ofi_std > 0:
            return acceleration / self.baseline_ofi_std
            
        return acceleration
        
    def _compute_momentum(self, ofi_raw: float) -> float:
        """Compute EMA momentum of OFI."""
        if self.ema_ofi is None:
            self.ema_ofi = ofi_raw
        else:
            self.ema_ofi = self.alpha * ofi_raw + (1 - self.alpha) * self.ema_ofi
            
        return self.ema_ofi
        
    def _compute_pressure(self) -> tuple[float, float]:
        """Compute relative bid/ask pressure."""
        if len(self.bid_depth_history) < 2:
            return 0.5, 0.5
            
        bid_change = self.bid_depth_history[-1] / max(self.bid_depth_history[-2], 1)
        ask_change = self.ask_depth_history[-1] / max(self.ask_depth_history[-2], 1)
        
        bid_pressure = bid_change / (bid_change + ask_change + 1e-9)
        ask_pressure = ask_change / (bid_change + ask_change + 1e-9)
        
        return bid_pressure, ask_pressure
        
    def _compute_imbalance_ratio(self) -> float:
        """Compute order book imbalance ratio."""
        if len(self.bid_depth_history) < 2:
            return 0.0
            
        total_bid = sum(list(self.bid_depth_history)[-5:])
        total_ask = sum(list(self.ask_depth_history)[-5:])
        
        total = total_bid + total_ask
        
        if total < 1e-9:
            return 0.0
            
        return (total_bid - total_ask) / total
        
    def _compute_depth_change(self, current_depth: float, is_bid: bool) -> float:
        """Compute percentage change in depth."""
        history = self.bid_depth_history if is_bid else self.ask_depth_history
        
        if len(history) < 2:
            return 0.0
            
        prev_depth = history[-2]
        
        if prev_depth < 1e-9:
            return 0.0
            
        return (current_depth - prev_depth) / prev_depth
        
    def get_trend_signal(self) -> int:
        """
        Get directional signal from OFI trend.
        
        Returns: 1 (bullish), -1 (bearish), 0 (neutral)
        """
        if len(self.ofi_history) < 10:
            return 0
            
        recent = list(self.ofi_history)[-10:]
        
        trend = np.polyfit(range(len(recent)), recent, 1)[0]
        
        if trend > 0.1:
            return 1
        elif trend < -0.1:
            return -1
        else:
            return 0
            
    def get_acceleration_signal(self) -> int:
        """
        Get signal from OFI acceleration.
        
        Returns: 1 (accelerating up), -1 (accelerating down), 0 (stable)
        """
        if len(self.ofi_history) < 5:
            return 0
            
        recent = list(self.ofi_history)[-5:]
        
        acc = np.mean(np.diff(recent, 2))
        
        if acc > 0.5:
            return 1
        elif acc < -0.5:
            return -1
        else:
            return 0
