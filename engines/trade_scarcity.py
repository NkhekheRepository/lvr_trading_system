"""
Trade Rate Governor - Controls excessive trading.

Instead of monitoring scarcity, this engine blocks excess trades when:
- Trade rate exceeds maximum threshold
- Symbol concentration is too high
- Time-weighted constraints are violated

Features:
- Configurable rate limits per time window
- Symbol concentration limits
- Cooldown periods between trades
- Gradual blocking with warnings
"""

import logging
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque

from core.event import Event, EventType
from core.bus import EventBus
from core.state import DistributedState

logger = logging.getLogger(__name__)


@dataclass
class RateLimit:
    window_seconds: int
    max_trades: int
    symbol_max_concentration: float = 0.3


@dataclass
class TradeRecord:
    symbol: str
    timestamp: int
    outcome: Optional[str] = None


class TradeRateGovernor:
    """
    Controls excessive trading through rate limiting.
    
    Limits:
    - Trades per time window (global)
    - Trades per symbol (concentration)
    - Minimum time between trades (cooldown)
    """
    
    DEFAULT_RATE_LIMITS = [
        RateLimit(window_seconds=60, max_trades=5),      # 5 trades/minute
        RateLimit(window_seconds=300, max_trades=15),    # 15 trades/5 minutes
        RateLimit(window_seconds=3600, max_trades=50),  # 50 trades/hour
    ]
    
    COOLDOWN_SECONDS = 5
    MAX_CONCENTRATION = 0.3
    WARNING_THRESHOLD = 0.8
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        rate_limits: Optional[list[RateLimit]] = None,
    ):
        self.bus = bus
        self.state = state
        self.rate_limits = rate_limits or self.DEFAULT_RATE_LIMITS
        
        self._trade_history: deque[TradeRecord] = deque(maxlen=1000)
        self._last_trade_times: dict[str, int] = {}
        self._blocked_count = 0
        self._total_check_count = 0
        self._symbol_trades_today: dict[str, int] = {}
        self._last_reset_date: Optional[str] = None
    
    async def should_block_trade(
        self,
        symbol: str,
        current_time: Optional[int] = None
    ) -> tuple[bool, str]:
        """
        Check if a trade should be blocked.
        
        Args:
            symbol: Trading symbol
            current_time: Current timestamp in ms (optional)
            
        Returns:
            (should_block, reason)
        """
        self._total_check_count += 1
        
        if current_time is None:
            current_time = int(datetime.now().timestamp() * 1000)
        
        await self._reset_daily_counts_if_needed()
        
        block, reason = self._check_cooldown(symbol, current_time)
        if block:
            return True, reason
        
        block, reason = self._check_rate_limits(current_time)
        if block:
            return True, reason
        
        block, reason = self._check_concentration(symbol)
        if block:
            return True, reason
        
        return False, ""
    
    def _check_cooldown(self, symbol: str, current_time: int) -> tuple[bool, str]:
        """Check if cooldown period is in effect."""
        last_time = self._last_trade_times.get(symbol)
        if last_time is None:
            return False, ""
        
        elapsed = (current_time - last_time) / 1000
        if elapsed < self.COOLDOWN_SECONDS:
            return True, f"cooldown_{elapsed:.1f}s_remaining"
        
        return False, ""
    
    def _check_rate_limits(self, current_time: int) -> tuple[bool, str]:
        """Check if rate limits are exceeded."""
        for limit in self.rate_limits:
            window_start = current_time - limit.window_seconds * 1000
            trades_in_window = sum(
                1 for t in self._trade_history
                if t.timestamp >= window_start
            )
            
            if trades_in_window >= limit.max_trades:
                return True, f"rate_limit_{limit.window_seconds}s_exceeded_{trades_in_window}/{limit.max_trades}"
            
            if trades_in_window >= limit.max_trades * self.WARNING_THRESHOLD:
                logger.info(
                    f"Rate limit warning: {trades_in_window}/{limit.max_trades} in {limit.window_seconds}s window"
                )
        
        return False, ""
    
    def _check_concentration(self, symbol: str) -> tuple[bool, str]:
        """Check if symbol concentration is too high."""
        if not self._symbol_trades_today:
            return False, ""
        
        total_trades = sum(self._symbol_trades_today.values())
        if total_trades == 0:
            return False, ""
        
        symbol_trades = self._symbol_trades_today.get(symbol, 0)
        concentration = symbol_trades / total_trades
        
        if concentration > self.MAX_CONCENTRATION:
            return True, f"concentration_{concentration:.2%}_exceeds_{self.MAX_CONCENTRATION:.2%}"
        
        return False, ""
    
    async def record_trade(
        self,
        symbol: str,
        timestamp: int,
        outcome: Optional[str] = None
    ) -> None:
        """Record a trade for rate tracking."""
        record = TradeRecord(symbol=symbol, timestamp=timestamp, outcome=outcome)
        self._trade_history.append(record)
        
        self._last_trade_times[symbol] = timestamp
        
        if symbol not in self._symbol_trades_today:
            self._symbol_trades_today[symbol] = 0
        self._symbol_trades_today[symbol] += 1
        
        logger.debug(
            f"Trade recorded: {symbol}",
            extra={
                'symbol': symbol,
                'total_today': sum(self._symbol_trades_today.values()),
                'symbol_today': self._symbol_trades_today[symbol],
            }
        )
    
    async def record_blocked(self, symbol: str, reason: str) -> None:
        """Record a blocked trade."""
        self._blocked_count += 1
        
        logger.debug(
            f"Trade blocked: {symbol}",
            extra={'symbol': symbol, 'reason': reason, 'blocked_count': self._blocked_count}
        )
    
    async def _reset_daily_counts_if_needed(self) -> None:
        """Reset daily counters at start of new day."""
        current_date = datetime.now().strftime('%Y-%m-%d')
        
        if self._last_reset_date != current_date:
            self._symbol_trades_today = {}
            self._last_reset_date = current_date
    
    def get_rate_stats(self, window_seconds: int = 3600) -> dict:
        """Get rate statistics for a time window."""
        current_time = int(datetime.now().timestamp() * 1000)
        window_start = current_time - window_seconds * 1000
        
        trades_in_window = [
            t for t in self._trade_history
            if t.timestamp >= window_start
        ]
        
        symbol_counts = {}
        for t in trades_in_window:
            symbol_counts[t.symbol] = symbol_counts.get(t.symbol, 0) + 1
        
        return {
            'window_seconds': window_seconds,
            'trade_count': len(trades_in_window),
            'unique_symbols': len(symbol_counts),
            'top_symbol': max(symbol_counts.items(), key=lambda x: x[1])[0] if symbol_counts else None,
            'symbol_distribution': symbol_counts,
            'block_rate': self._blocked_count / max(1, self._total_check_count),
        }
    
    async def get_governor_report(self) -> dict:
        """Get comprehensive rate governor report."""
        stats_1m = self.get_rate_stats(60)
        stats_5m = self.get_rate_stats(300)
        stats_1h = self.get_rate_stats(3600)
        
        return {
            'blocked_count': self._blocked_count,
            'total_checks': self._total_check_count,
            'block_rate': self._blocked_count / max(1, self._total_check_count),
            'trades_1m': stats_1m['trade_count'],
            'trades_5m': stats_5m['trade_count'],
            'trades_1h': stats_1h['trade_count'],
            'symbol_concentration': self._symbol_trades_today.copy(),
            'cooldown_seconds': self.COOLDOWN_SECONDS,
        }
    
    async def update_state(self) -> None:
        """Persist governor state."""
        if not self.state:
            return
        
        await self.state.set(
            key="governor:trade_rate",
            value={
                'blocked_count': self._blocked_count,
                'total_checks': self._total_check_count,
                'symbol_trades_today': self._symbol_trades_today.copy(),
                'last_trade_times': self._last_trade_times.copy(),
                'updated_at': int(datetime.now().timestamp() * 1000),
            },
            trace_id="trade_rate_governor",
        )


from typing import Optional
