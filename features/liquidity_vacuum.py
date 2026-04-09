"""
Liquidity Vacuum Detector - Detects liquidity withdrawal patterns.

Liquidity vacuums occur when large orders consume available liquidity,
causing price slippage and potential cascade effects.
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional
from collections import deque
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class VacuumSignal:
    intensity: float
    direction: int
    imminent: bool
    confidence: float
    vacuum_type: str
    price_impact_estimate: float
    recovery_time_estimate: float
    timestamp: datetime


class LiquidityVacuumDetector:
    """
    Detects imminent liquidity vacuums.
    
    Signals:
    - Sudden depth reduction
    - One-sided liquidity withdrawal
    - Accelerating order flow
    - Large visible orders consuming depth
    """
    
    def __init__(
        self,
        symbol: str,
        window_size: int = 100,
        depth_threshold: float = 0.3,
        velocity_threshold: float = 0.5,
        imbalance_threshold: float = 0.7,
    ):
        self.symbol = symbol
        
        self.window_size = window_size
        self.depth_threshold = depth_threshold
        self.velocity_threshold = velocity_threshold
        self.imbalance_threshold = imbalance_threshold
        
        self.bid_depth_history: deque[float] = deque(maxlen=window_size)
        self.ask_depth_history: deque[float] = deque(maxlen=window_size)
        
        self.bid_count_history: deque[int] = deque(maxlen=window_size)
        self.ask_count_history: deque[int] = deque(maxlen=window_size)
        
        self.ofi_history: deque[float] = deque(maxlen=window_size)
        
        self.volume_history: deque[float] = deque(maxlen=window_size)
        
        self.baseline_depth: Optional[float] = None
        
        self.alerts: deque[tuple[datetime, str]] = deque(maxlen=50)
        
    def update(
        self,
        bid_levels: list[tuple[float, float]],
        ask_levels: list[tuple[float, float]],
        volume: float = 0.0,
        ofi: float = 0.0,
        timestamp: Optional[datetime] = None,
    ) -> VacuumSignal:
        """
        Update with order book state.
        
        Returns VacuumSignal with detection results.
        """
        timestamp = timestamp or datetime.now()
        
        bid_depth = sum(v for _, v in bid_levels)
        ask_depth = sum(v for _, v in ask_levels)
        
        bid_count = len(bid_levels)
        ask_count = len(ask_levels)
        
        self.bid_depth_history.append(bid_depth)
        self.ask_depth_history.append(ask_depth)
        self.bid_count_history.append(bid_count)
        self.ask_count_history.append(ask_count)
        self.ofi_history.append(ofi)
        self.volume_history.append(volume)
        
        if self.baseline_depth is None and len(self.bid_depth_history) >= 50:
            self.baseline_depth = np.median(list(self.bid_depth_history))
            
        intensity = self._compute_intensity(bid_depth, ask_depth)
        
        direction = self._compute_direction()
        
        imminent = self._check_imminent()
        
        confidence = self._compute_confidence()
        
        vacuum_type = self._classify_vacuum_type(bid_depth, ask_depth, direction)
        
        price_impact = self._estimate_price_impact(intensity, direction)
        
        recovery_time = self._estimate_recovery_time()
        
        if imminent and confidence > 0.6:
            self.alerts.append((timestamp, vacuum_type))
            
        return VacuumSignal(
            intensity=intensity,
            direction=direction,
            imminent=imminent,
            confidence=confidence,
            vacuum_type=vacuum_type,
            price_impact_estimate=price_impact,
            recovery_time_estimate=recovery_time,
            timestamp=timestamp,
        )
        
    def _compute_intensity(self, bid_depth: float, ask_depth: float) -> float:
        """Compute vacuum intensity (0-1)."""
        if self.baseline_depth is None or self.baseline_depth < 1e-9:
            return 0.0
            
        current_depth = (bid_depth + ask_depth) / 2
        
        depth_ratio = current_depth / self.baseline_depth
        
        depth_score = max(0.0, 1.0 - depth_ratio)
        
        if len(self.bid_depth_history) >= 5:
            recent_bids = list(self.bid_depth_history)[-5:]
            recent_asks = list(self.ask_depth_history)[-5:]
            
            bid_velocity = self._compute_velocity(recent_bids)
            ask_velocity = self._compute_velocity(recent_asks)
            
            avg_velocity = (abs(bid_velocity) + abs(ask_velocity)) / 2
            velocity_score = min(avg_velocity / self.velocity_threshold, 1.0)
        else:
            velocity_score = 0.0
            
        intensity = depth_score * 0.6 + velocity_score * 0.4
        
        return min(intensity, 1.0)
        
    def _compute_velocity(self, depth_history: list[float]) -> float:
        """Compute velocity of depth change."""
        if len(depth_history) < 3:
            return 0.0
            
        changes = np.diff(depth_history)
        
        velocity = np.mean(changes)
        
        return velocity / max(abs(depth_history[-1]), 1e-9)
        
    def _compute_direction(self) -> int:
        """
        Determine vacuum direction.
        
        Returns: 1 (bid side vacuum), -1 (ask side vacuum), 0 (balanced)
        """
        if len(self.bid_depth_history) < 5:
            return 0
            
        recent_bids = list(self.bid_depth_history)[-5:]
        recent_asks = list(self.ask_depth_history)[-5:]
        
        bid_change = recent_bids[-1] - recent_bids[0]
        ask_change = recent_asks[-1] - recent_asks[0]
        
        bid_pct = bid_change / max(abs(recent_bids[0]), 1e-9)
        ask_pct = ask_change / max(abs(recent_asks[0]), 1e-9)
        
        if bid_pct < -self.depth_threshold and bid_pct < ask_pct:
            return 1
        elif ask_pct < -self.depth_threshold and ask_pct < bid_pct:
            return -1
        elif len(self.ofi_history) > 0:
            recent_ofi = list(self.ofi_history)[-5:]
            avg_ofi = np.mean(recent_ofi)
            
            if avg_ofi > 0.1:
                return 1
            elif avg_ofi < -0.1:
                return -1
                
        return 0
        
    def _check_imminent(self) -> bool:
        """Check if vacuum is imminent."""
        if len(self.bid_depth_history) < 10:
            return False
            
        recent = list(self.bid_depth_history)[-10:]
        
        depth_ratio = recent[-1] / np.mean(recent)
        
        if depth_ratio > 0.9:
            return False
            
        changes = np.diff(recent)
        
        accelerating_negative = all(c < 0 for c in changes[-3:])
        
        if accelerating_negative and depth_ratio < 0.7:
            return True
            
        if self.baseline_depth and recent[-1] / self.baseline_depth < 0.5:
            return True
            
        return False
        
    def _compute_confidence(self) -> float:
        """Compute confidence in vacuum detection."""
        if len(self.bid_depth_history) < 20:
            return 0.0
            
        intensity = self._compute_intensity(
            self.bid_depth_history[-1],
            self.ask_depth_history[-1]
        )
        
        direction = self._compute_direction()
        
        base_confidence = 0.3
        
        if direction != 0:
            base_confidence += 0.2
            
        base_confidence += intensity * 0.4
        
        if len(self.ofi_history) >= 5:
            recent_ofi = list(self.ofi_history)[-5:]
            ofi_consistency = abs(np.mean(recent_ofi))
            base_confidence += ofi_consistency * 0.2
            
        if self._check_imminent():
            base_confidence += 0.15
            
        return min(base_confidence, 0.95)
        
    def _classify_vacuum_type(
        self,
        bid_depth: float,
        ask_depth: float,
        direction: int,
    ) -> str:
        """Classify type of vacuum."""
        if direction == 1:
            return 'bid_liquidity_withdrawal'
        elif direction == -1:
            return 'ask_liquidity_withdrawal'
        else:
            if bid_depth < self.baseline_depth * 0.5:
                return 'bilateral_stress'
            return 'balanced_scarcity'
            
    def _estimate_price_impact(self, intensity: float, direction: int) -> float:
        """Estimate potential price impact in bps."""
        if direction == 0:
            return 0.0
            
        base_impact = intensity * 50
        
        if len(self.ofi_history) >= 5:
            recent_ofi = list(self.ofi_history)[-5:]
            ofi_magnitude = np.mean(recent_ofi)
            
            base_impact *= (1 + abs(ofi_magnitude))
            
        return base_impact
        
    def _estimate_recovery_time(self) -> float:
        """Estimate time to recover liquidity in seconds."""
        if len(self.bid_depth_history) < 10:
            return 30.0
            
        recent = list(self.bid_depth_history)[-10:]
        
        if recent[-1] >= np.mean(recent):
            return 0.0
            
        avg_deficit = np.mean(recent) - recent[-1]
        
        avg_fill_rate = np.mean(np.diff(recent))
        
        if avg_fill_rate <= 0:
            return 60.0
            
        recovery_time = avg_deficit / abs(avg_fill_rate)
        
        return min(max(recovery_time, 5.0), 120.0)
        
    def get_trading_signal(self) -> tuple[int, float]:
        """
        Get signal for trading based on vacuum detection.
        
        Returns: (direction, confidence)
        """
        if len(self.bid_depth_history) < 10:
            return 0, 0.0
            
        intensity = self._compute_intensity(
            self.bid_depth_history[-1],
            self.ask_depth_history[-1]
        )
        
        direction = self._compute_direction()
        confidence = self._compute_confidence()
        
        if intensity < 0.3 or confidence < 0.5:
            return 0, 0.0
            
        return direction, confidence
        
    def get_recent_alerts(self) -> list[tuple[datetime, str]]:
        """Get recent vacuum alerts."""
        return list(self.alerts)
