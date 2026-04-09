"""
Trade Scarcity Engine - Handles low trade frequency situations.

Monitors and addresses situations where trading frequency drops abnormally.
"""

import logging
from typing import Optional
from dataclasses import asdict
from datetime import datetime, timedelta

from core.event import Event, EventType
from core.bus import EventBus
from core.state import DistributedState

logger = logging.getLogger(__name__)


class TradeScarcityEngine:
    """
    Monitors trade scarcity and adjusts system behavior.
    
    Scarcity scenarios:
    - No trades for extended period
    - Signal generation dry spell
    - Edge degradation
    - Market conditions
    """
    
    NO_TRADE_WARNING_HOURS = 2
    NO_TRADE_CRITICAL_HOURS = 8
    MIN_DAILY_TRADES = 3
    SCARCITY_THRESHOLD = 0.5
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        check_interval_seconds: int = 300,
    ):
        self.bus = bus
        self.state = state
        self.check_interval = check_interval_seconds
        
        self._last_trade_time: Optional[int] = None
        self._trade_count_today: int = 0
        self._signal_count_today: int = 0
        self._last_reset_date: Optional[str] = None
        self._scarcity_history: list[dict] = []
        
    async def check_scarcity(
        self,
        current_time: Optional[int] = None
    ) -> tuple[str, list[str]]:
        """
        Check for trade scarcity conditions.
        
        Returns:
            (status, recommendations)
        """
        if current_time is None:
            current_time = int(datetime.now().timestamp() * 1000)
        
        await self._reset_daily_counts_if_needed()
        
        time_since_trade = (
            (current_time - self._last_trade_time) / (1000 * 60 * 60)
            if self._last_trade_time else float('inf')
        )
        
        status = "OK"
        recommendations = []
        
        if time_since_trade >= self.NO_TRADE_CRITICAL_HOURS:
            status = "CRITICAL"
            recommendations = [
                "investigate_market_conditions",
                "check_signal_generation",
                "review_filter_effectiveness",
                "consider_broader_universe",
            ]
        elif time_since_trade >= self.NO_TRADE_WARNING_HOURS:
            status = "WARNING"
            recommendations = [
                "monitor_situation",
                "review_recent_rejections",
            ]
        
        if self._trade_count_today < self.MIN_DAILY_TRADES:
            status = "LOW_ACTIVITY" if status == "OK" else status
            recommendations.append("low_trade_count_review")
        
        scarcity_score = self._calculate_scarcity_score(
            time_since_trade,
            self._trade_count_today,
            self._signal_count_today
        )
        
        if scarcity_score > self.SCARCITY_THRESHOLD:
            status = "SCARCITY_DETECTED"
            recommendations.extend([
                "relax_filters_temporarily",
                "check_market_opportunities",
            ])
        
        await self._record_scarcity_check(status, scarcity_score)
        
        if status != "OK":
            await self._emit_scarcity_alert(status, scarcity_score, recommendations)
        
        return status, recommendations
    
    def _calculate_scarcity_score(
        self,
        hours_since_trade: float,
        trade_count: int,
        signal_count: int
    ) -> float:
        """Calculate scarcity score 0-1 (higher = more scarce)."""
        time_factor = min(1.0, hours_since_trade / 24)
        
        expected_trades = self.MIN_DAILY_TRADES * (hours_since_trade / 24)
        count_factor = 1.0 - min(1.0, trade_count / max(1, expected_trades))
        
        conversion_rate = (
            trade_count / signal_count if signal_count > 0 else 0
        )
        conversion_factor = 1.0 - min(1.0, conversion_rate)
        
        score = time_factor * 0.4 + count_factor * 0.4 + conversion_factor * 0.2
        
        return min(1.0, max(0.0, score))
    
    async def _reset_daily_counts_if_needed(self) -> None:
        """Reset daily counters at start of new day."""
        current_date = datetime.now().strftime('%Y-%m-%d')
        
        if not hasattr(self, '_last_reset_date') or self._last_reset_date != current_date:
            self._trade_count_today = 0
            self._signal_count_today = 0
            self._last_reset_date = current_date
    
    async def record_trade(
        self,
        symbol: str,
        timestamp: int,
        pnl: float = 0
    ) -> None:
        """Record a trade for scarcity tracking."""
        self._last_trade_time = timestamp
        self._trade_count_today += 1
        
        logger.debug(
            f"Trade recorded for scarcity tracking: {symbol}",
            extra={
                'symbol': symbol,
                'pnl': pnl,
                'daily_trade_count': self._trade_count_today,
            }
        )
    
    async def record_signal(
        self,
        symbol: str,
        timestamp: int,
        was_accepted: bool
    ) -> None:
        """Record a signal for scarcity tracking."""
        self._signal_count_today += 1
        
        if not was_accepted:
            logger.debug(
                f"Signal rejected for scarcity tracking: {symbol}",
                extra={'symbol': symbol, 'daily_signal_count': self._signal_count_today}
            )
    
    async def _record_scarcity_check(
        self,
        status: str,
        score: float
    ) -> None:
        """Record scarcity check result."""
        record = {
            'status': status,
            'score': score,
            'trade_count': self._trade_count_today,
            'signal_count': self._signal_count_today,
            'timestamp': int(datetime.now().timestamp() * 1000),
        }
        
        self._scarcity_history.append(record)
        if len(self._scarcity_history) > 100:
            self._scarcity_history = self._scarcity_history[-100:]
        
        await self._update_scarcity_state(status, score)
    
    async def _update_scarcity_state(
        self,
        status: str,
        score: float
    ) -> None:
        if not self.state:
            return
            
        await self.state.set(
            key="scarcity:global",
            value={
                'status': status,
                'score': score,
                'trade_count_today': self._trade_count_today,
                'signal_count_today': self._signal_count_today,
                'last_trade_time': self._last_trade_time,
                'updated_at': int(datetime.now().timestamp() * 1000),
            },
            trace_id="trade_scarcity_engine",
        )
    
    async def _emit_scarcity_alert(
        self,
        status: str,
        score: float,
        recommendations: list[str]
    ) -> None:
        """Emit scarcity alert."""
        logger.warning(
            f"Trade scarcity {status}: score={score:.2f}",
            extra={
                'status': status,
                'score': score,
                'recommendations': recommendations,
                'trade_count_today': self._trade_count_today,
                'signal_count_today': self._signal_count_today,
            }
        )
        
        if self.bus:
            alert_event = Event.create(
                event_type=EventType.RATE_LIMIT_APPLIED,
                payload={
                    'reason': 'trade_scarcity',
                    'status': status,
                    'score': score,
                    'recommendations': recommendations,
                },
                source="trade_scarcity_engine",
            )
            await self.bus.publish(alert_event)
    
    async def get_scarcity_report(self) -> dict:
        """Get comprehensive scarcity report."""
        avg_score = (
            sum(s['score'] for s in self._scarcity_history) / len(self._scarcity_history)
            if self._scarcity_history else 0
        )
        
        return {
            'current_score': self._scarcity_history[-1]['score'] if self._scarcity_history else 0,
            'average_score': avg_score,
            'status': self._scarcity_history[-1]['status'] if self._scarcity_history else 'OK',
            'trade_count_today': self._trade_count_today,
            'signal_count_today': self._signal_count_today,
            'last_trade_time': self._last_trade_time,
            'scarcity_events': len([s for s in self._scarcity_history if s['status'] != 'OK']),
        }
    
    async def should_relax_filters(self) -> tuple[bool, float]:
        """
        Determine if filters should be temporarily relaxed.
        
        Returns:
            (should_relax, relaxation_factor)
        """
        if not self._scarcity_history:
            return False, 0.0
        
        recent = self._scarcity_history[-5:]
        avg_score = sum(s['score'] for s in recent) / len(recent)
        
        if avg_score > 0.7:
            return True, 0.3
        elif avg_score > 0.5:
            return True, 0.15
        elif avg_score > 0.3:
            return True, 0.1
        
        return False, 0.0
