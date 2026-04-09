"""
Drawdown Analyzer - Analyzes drawdown patterns and triggers protective actions.

Monitors portfolio drawdown and initiates risk controls when thresholds breached.
"""

import logging
import numpy as np
from typing import Optional
from dataclasses import asdict
from datetime import datetime
from collections import deque

from core.event import Event, EventType
from core.bus import EventBus
from core.state import DistributedState

logger = logging.getLogger(__name__)


class DrawdownAnalyzer:
    """
    Analyzes drawdown patterns and triggers protective actions.
    
    Drawdown metrics:
    - Current drawdown
    - Peak-to-trough
    - Drawdown duration
    - Recovery time
    - Accelerating drawdown detection
    - Spiky loss detection
    """
    
    SOFT_LIMIT_PCT = 0.10
    HARD_LIMIT_PCT = 0.20
    DRAWDOWN_RATE_ALERT = 0.05
    ACCELERATION_WINDOW = 5
    SPIKE_ZSCORE_THRESHOLD = 3.0
    SPIKE_WINDOW = 10
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        initial_capital: float = 100000.0,
    ):
        self.bus = bus
        self.state = state
        self.initial_capital = initial_capital
        
        self._peak_value = initial_capital
        self._trough_value = initial_capital
        self._in_drawdown = False
        self._drawdown_start: Optional[datetime] = None
        self._drawdown_history: list[dict] = []
        self._consecutive_losses: int = 0
        
        self._drawdown_rates = deque(maxlen=self.ACCELERATION_WINDOW * 2)
        self._period_returns = deque(maxlen=self.SPIKE_WINDOW * 2)
        self._acceleration_detected = False
        self._spike_detected = False
        
    async def update_drawdown(
        self,
        current_value: float,
        daily_pnl: float
    ) -> tuple[str, list[str]]:
        """
        Update drawdown state and determine actions.
        
        Returns:
            (status, required_actions)
        """
        if current_value > self._peak_value:
            self._peak_value = current_value
            if self._in_drawdown:
                duration = self._calculate_drawdown_duration()
                await self._record_drawdown_end(duration)
            self._in_drawdown = False
            self._consecutive_losses = 0
        
        current_drawdown = (self._peak_value - current_value) / self._peak_value
        drawdown_pct = current_drawdown
        
        if daily_pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0
        
        if current_drawdown > 0.01:
            if not self._in_drawdown:
                self._in_drawdown = True
                self._drawdown_start = datetime.now()
                self._trough_value = current_value
            else:
                if current_value < self._trough_value:
                    self._trough_value = current_value
        else:
            if self._in_drawdown:
                duration = self._calculate_drawdown_duration()
                await self._record_drawdown_end(duration)
            self._in_drawdown = False
        
        required_actions = []
        status = "ok"
        
        if drawdown_pct >= self.HARD_LIMIT_PCT:
            status = "HARD_LIMIT"
            required_actions = [
                "halt_all_trading",
                "close_all_positions",
                "manual_review_required",
            ]
            await self._emit_drawdown_alert(drawdown_pct, status)
        elif drawdown_pct >= self.SOFT_LIMIT_PCT:
            status = "SOFT_LIMIT"
            required_actions = [
                "reduce_position_sizes",
                "increase_cash_buffer",
                "pause_new_entries",
            ]
            await self._emit_drawdown_alert(drawdown_pct, status)
        
        drawdown_rate = self._calculate_drawdown_rate(current_value, daily_pnl)
        if abs(drawdown_rate) > self.DRAWDOWN_RATE_ALERT:
            required_actions.append("monitor_drawdown_rate")
        
        if self._consecutive_losses >= 5:
            required_actions.append("review_strategy")
        
        self._update_rate_history(drawdown_rate)
        
        if self._detect_accelerating_drawdown():
            if not self._acceleration_detected:
                self._acceleration_detected = True
                required_actions.append("acceleration_detected")
                logger.warning(
                    f"Accelerating drawdown detected: rate={drawdown_rate:.4f}"
                )
            required_actions.append("increase_reserves")
        
        if self._detect_spiky_losses():
            if not self._spike_detected:
                self._spike_detected = True
                required_actions.append("spiky_losses_detected")
                logger.warning("Spiky loss pattern detected")
            required_actions.append("volatility_exit")
        
        if drawdown_pct < self.SOFT_LIMIT_PCT:
            self._acceleration_detected = False
            self._spike_detected = False
        
        await self._update_drawdown_state(drawdown_pct, status)
        
        return status, required_actions
    
    def _update_rate_history(self, rate: float) -> None:
        """Update drawdown rate history for acceleration detection."""
        self._drawdown_rates.append(rate)
    
    def _detect_accelerating_drawdown(self) -> bool:
        """
        Detect if drawdown rate is increasing over multiple periods.
        
        Compares recent average rate to historical average.
        Returns True if recent rate is significantly worse (>20% faster).
        """
        if len(self._drawdown_rates) < self.ACCELERATION_WINDOW:
            return False
        
        rates = list(self._drawdown_rates)
        recent_avg = np.mean(rates[-2:]) if len(rates) >= 2 else rates[-1]
        historical_avg = np.mean(rates[:-2]) if len(rates) > 2 else np.mean(rates)
        
        if abs(historical_avg) < 1e-10:
            return False
        
        acceleration_ratio = recent_avg / historical_avg
        
        return acceleration_ratio < 0.8
    
    def _detect_spiky_losses(self) -> bool:
        """
        Detect abnormal loss spikes using z-score.
        
        Returns True if any return is > 3 standard deviations from mean.
        """
        if len(self._period_returns) < self.SPIKE_WINDOW:
            return False
        
        returns = list(self._period_returns)
        mean = np.mean(returns)
        std = np.std(returns)
        
        if std < 1e-10:
            return False
        
        z_scores = [(r - mean) / std for r in returns]
        
        return any(z < -self.SPIKE_ZSCORE_THRESHOLD for z in z_scores)
    
    def record_return(self, period_return: float) -> None:
        """Record a period return for spike detection."""
        self._period_returns.append(period_return)
    
    def _calculate_drawdown_duration(self) -> int:
        """Calculate duration of current drawdown in seconds."""
        if not self._drawdown_start:
            return 0
        return int((datetime.now() - self._drawdown_start).total_seconds())
    
    def _calculate_drawdown_rate(
        self,
        current_value: float,
        daily_pnl: float
    ) -> float:
        """Calculate rate of drawdown change."""
        if self._peak_value == 0:
            return 0
        
        drawdown_change = daily_pnl / self._peak_value
        
        return drawdown_change
    
    async def _record_drawdown_end(self, duration_seconds: int) -> None:
        """Record completed drawdown event."""
        max_drawdown = (self._peak_value - self._trough_value) / self._peak_value
        
        drawdown_event = {
            'peak_value': self._peak_value,
            'trough_value': self._trough_value,
            'max_drawdown_pct': max_drawdown,
            'duration_seconds': duration_seconds,
            'timestamp': int(datetime.now().timestamp() * 1000),
        }
        
        self._drawdown_history.append(drawdown_event)
        if len(self._drawdown_history) > 50:
            self._drawdown_history = self._drawdown_history[-50:]
        
        logger.info(
            f"Drawdown ended: {max_drawdown:.2%} over {duration_seconds}s",
            extra=drawdown_event
        )
    
    async def _emit_drawdown_alert(
        self,
        drawdown_pct: float,
        status: str
    ) -> None:
        """Emit drawdown alert."""
        logger.warning(
            f"Drawdown {status}: {drawdown_pct:.2%}",
            extra={
                'drawdown_pct': drawdown_pct,
                'status': status,
                'peak_value': self._peak_value,
                'current_value': self._peak_value * (1 - drawdown_pct),
            }
        )
        
        if self.bus:
            alert_event = Event.create(
                event_type=EventType.DRAWDOWN_ALERT,
                payload={
                    'drawdown_pct': drawdown_pct,
                    'status': status,
                    'consecutive_losses': self._consecutive_losses,
                    'in_drawdown': self._in_drawdown,
                    'peak_value': self._peak_value,
                },
                source="drawdown_analyzer",
            )
            await self.bus.publish(alert_event)
    
    async def _update_drawdown_state(
        self,
        drawdown_pct: float,
        status: str
    ) -> None:
        if not self.state:
            return
            
        await self.state.set(
            key="drawdown:global",
            value={
                'current_drawdown_pct': drawdown_pct,
                'peak_value': self._peak_value,
                'status': status,
                'in_drawdown': self._in_drawdown,
                'consecutive_losses': self._consecutive_losses,
                'drawdown_count': len(self._drawdown_history),
                'updated_at': int(datetime.now().timestamp() * 1000),
            },
            trace_id="drawdown_analyzer",
        )
    
    async def get_drawdown_report(self) -> dict:
        """Get comprehensive drawdown report."""
        if not self._drawdown_history:
            return {
                'current_drawdown_pct': 0,
                'peak_value': self._peak_value,
                'in_drawdown': False,
                'historical_drawdowns': [],
                'avg_drawdown': 0,
                'max_drawdown': 0,
            }
        
        max_dds = [d['max_drawdown_pct'] for d in self._drawdown_history]
        avg_dd = sum(max_dds) / len(max_dds) if max_dds else 0
        max_dd = max(max_dds) if max_dds else 0
        
        return {
            'current_drawdown_pct': (self._peak_value - self._trough_value) / self._peak_value if self._peak_value > 0 else 0,
            'peak_value': self._peak_value,
            'trough_value': self._trough_value,
            'in_drawdown': self._in_drawdown,
            'consecutive_losses': self._consecutive_losses,
            'historical_drawdowns': len(self._drawdown_history),
            'avg_drawdown': avg_dd,
            'max_drawdown': max_dd,
        }
    
    async def should_increase_reserves(self) -> tuple[bool, float]:
        """Determine if cash reserves should be increased."""
        if self._consecutive_losses >= 3:
            return True, 0.2
        elif self._consecutive_losses >= 2:
            return True, 0.1
        return False, 0.0
