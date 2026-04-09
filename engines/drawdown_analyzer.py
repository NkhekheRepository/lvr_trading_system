"""
Drawdown Analyzer - Analyzes drawdown patterns and triggers protective actions.

Monitors portfolio drawdown and initiates risk controls when thresholds breached.
"""

import logging
from typing import Optional
from dataclasses import asdict
from datetime import datetime

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
    """
    
    SOFT_LIMIT_PCT = 0.10
    HARD_LIMIT_PCT = 0.20
    DRAWDOWN_RATE_ALERT = 0.05
    
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
        
        await self._update_drawdown_state(drawdown_pct, status)
        
        return status, required_actions
    
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
