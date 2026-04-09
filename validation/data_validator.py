"""
Data Validator - Validates market data quality.

Ensures data meets quality thresholds before use in trading decisions.
"""

import logging
from typing import Optional
from dataclasses import asdict
from datetime import datetime, timedelta

from core.bus import EventBus
from core.state import DistributedState

logger = logging.getLogger(__name__)


class DataValidator:
    """
    Validates market data quality.
    
    Validation checks:
    - Data freshness
    - Price sanity
    - Volume sanity
    - Spread sanity
    - Completeness
    """
    
    MAX_DATA_AGE_MS = 5000
    MIN_PRICE = 0.0001
    MAX_SPREAD_BPS = 100
    MIN_VOLUME_24H = 1000
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
    ):
        self.bus = bus
        self.state = state
        self._validation_history: dict[str, list[dict]] = {}
        
    async def validate_tick(
        self,
        symbol: str,
        tick: dict
    ) -> tuple[bool, list[str]]:
        """
        Validate a market tick.
        
        Returns:
            (is_valid, failure_reasons)
        """
        failures = []
        
        timestamp = tick.get('timestamp', 0)
        if not self._check_freshness(timestamp):
            failures.append('stale_data')
        
        price = tick.get('price', 0)
        if not self._check_price_sanity(price):
            failures.append(f'invalid_price_{price}')
        
        bid = tick.get('bid', 0)
        ask = tick.get('ask', 0)
        if not self._check_spread_sanity(bid, ask):
            failures.append('spread_too_wide')
        
        volume = tick.get('volume', 0)
        if not self._check_volume_sanity(volume):
            failures.append(f'low_volume_{volume}')
        
        is_valid = len(failures) == 0
        
        await self._record_validation(symbol, is_valid, tick)
        
        if not is_valid:
            logger.warning(
                f"Data validation failed for {symbol}: {failures}",
                extra={'tick': tick, 'failures': failures}
            )
        
        return is_valid, failures
    
    async def validate_orderbook(
        self,
        symbol: str,
        orderbook: dict
    ) -> tuple[bool, list[str]]:
        """
        Validate order book data.
        
        Returns:
            (is_valid, failure_reasons)
        """
        failures = []
        
        bids = orderbook.get('bids', [])
        asks = orderbook.get('asks', [])
        
        if not bids or not asks:
            failures.append('empty_orderbook')
        
        if len(bids) < 5:
            failures.append('insufficient_bid_depth')
        
        if len(asks) < 5:
            failures.append('insufficient_ask_depth')
        
        best_bid = bids[0][0] if bids else 0
        best_ask = asks[0][0] if asks else 0
        
        if best_bid >= best_ask:
            failures.append('crossed_orderbook')
        
        spread_bps = (
            (best_ask - best_bid) / best_bid * 10000
            if best_bid > 0 else float('inf')
        )
        if spread_bps > self.MAX_SPREAD_BPS:
            failures.append(f'spread_too_wide_{spread_bps:.1f}bps')
        
        timestamp = orderbook.get('timestamp', 0)
        if not self._check_freshness(timestamp):
            failures.append('stale_orderbook')
        
        is_valid = len(failures) == 0
        
        await self._record_validation(symbol, is_valid, orderbook)
        
        return is_valid, failures
    
    async def validate_trade(
        self,
        symbol: str,
        trade: dict
    ) -> tuple[bool, list[str]]:
        """
        Validate a trade.
        
        Returns:
            (is_valid, failure_reasons)
        """
        failures = []
        
        trade_id = trade.get('trade_id', '')
        if not trade_id:
            failures.append('missing_trade_id')
        
        price = trade.get('price', 0)
        if not self._check_price_sanity(price):
            failures.append(f'invalid_trade_price_{price}')
        
        quantity = trade.get('quantity', 0)
        if quantity <= 0:
            failures.append(f'invalid_quantity_{quantity}')
        
        side = trade.get('side', '')
        if side not in ('BUY', 'SELL'):
            failures.append(f'invalid_side_{side}')
        
        is_valid = len(failures) == 0
        
        return is_valid, failures
    
    def _check_freshness(self, timestamp: int) -> bool:
        """Check if data is fresh enough."""
        if timestamp == 0:
            return False
        
        current_time = int(datetime.now().timestamp() * 1000)
        age_ms = current_time - timestamp
        
        return age_ms <= self.MAX_DATA_AGE_MS
    
    def _check_price_sanity(self, price: float) -> bool:
        """Check if price is sane."""
        if price <= 0:
            return False
        if price < self.MIN_PRICE:
            return False
        if price > 1000000:
            return False
        return True
    
    def _check_spread_sanity(self, bid: float, ask: float) -> bool:
        """Check if spread is sane."""
        if bid <= 0 or ask <= 0:
            return False
        if bid >= ask:
            return False
        
        spread_bps = (ask - bid) / bid * 10000
        return spread_bps <= self.MAX_SPREAD_BPS
    
    def _check_volume_sanity(self, volume: float) -> bool:
        """Check if volume is sane."""
        return volume >= 0
    
    async def _record_validation(
        self,
        symbol: str,
        is_valid: bool,
        data: dict
    ) -> None:
        """Record validation result."""
        history = self._validation_history.setdefault(symbol, [])
        
        record = {
            'is_valid': is_valid,
            'timestamp': int(datetime.now().timestamp() * 1000),
            'data_age_ms': (
                int(datetime.now().timestamp() * 1000) - data.get('timestamp', 0)
            ),
        }
        
        history.append(record)
        if len(history) > 1000:
            history = history[-1000:]
        
        self._validation_history[symbol] = history
    
    async def get_validation_stats(self, symbol: str) -> dict:
        """Get validation statistics for a symbol."""
        history = self._validation_history.get(symbol, [])
        
        if not history:
            return {
                'total_checks': 0,
                'pass_rate': 1.0,
                'avg_data_age_ms': 0,
            }
        
        valid_count = sum(1 for r in history if r['is_valid'])
        total_count = len(history)
        
        return {
            'total_checks': total_count,
            'valid_checks': valid_count,
            'pass_rate': valid_count / total_count,
            'avg_data_age_ms': (
                sum(r['data_age_ms'] for r in history) / total_count
            ),
            'recent_failures': sum(
                1 for r in history[-50:] if not r['is_valid']
            ),
        }
    
    async def should_halt_processing(self, symbol: str) -> tuple[bool, str]:
        """
        Determine if processing should halt due to data quality.
        """
        stats = await self.get_validation_stats(symbol)
        
        if stats['total_checks'] < 10:
            return False, "insufficient_data"
        
        if stats['pass_rate'] < 0.7:
            return True, f"low_pass_rate_{stats['pass_rate']:.2%}"
        
        if stats['avg_data_age_ms'] > self.MAX_DATA_AGE_MS * 0.8:
            return True, f"high_latency_{stats['avg_data_age_ms']:.0f}ms"
        
        return False, "ok"
