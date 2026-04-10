"""
Event Ordering Engine - Ensures strict event ordering and integrity.

Maintains proper sequence numbers per symbol to ensure:
- Events are processed in order
- No lost events
- Duplicate detection and handling
- Time drift detection
"""

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, Callable
from threading import Lock

logger = logging.getLogger(__name__)


@dataclass
class SequenceState:
    """Tracks sequence state per symbol."""
    last_sequence: int = 0
    last_timestamp: int = 0
    pending_events: list = field(default_factory=list)
    gap_detected: bool = False


class EventOrderingEngine:
    """
    Ensures strict event ordering and detects ordering violations.
    
    Features:
    - Per-symbol sequence tracking
    - Gap detection for lost events
    - Duplicate filtering
    - Time drift monitoring
    - Event buffering for out-of-order delivery
    """

    def __init__(
        self,
        max_sequence_gap: int = 100,
        buffer_size: int = 100,
        time_drift_threshold_ms: int = 5000,
        cleanup_interval_sec: int = 60
    ):
        self._sequences: dict[str, SequenceState] = defaultdict(SequenceState)
        self._max_sequence_gap = max_sequence_gap
        self._buffer_size = buffer_size
        self._time_drift_threshold_ms = time_drift_threshold_ms
        self._cleanup_interval_sec = cleanup_interval_sec
        
        self._lock = Lock()
        self._callbacks: list[Callable] = []
        self._last_cleanup = time.time()
        
        self._stats = {
            "events_ordered": 0,
            "gaps_detected": 0,
            "duplicates_filtered": 0,
            "out_of_order_buffered": 0,
            "time_drifts_detected": 0
        }

    def register_callback(self, callback: Callable) -> None:
        """Register callback for ordering violations."""
        self._callbacks.append(callback)

    async def process_event(self, event: dict) -> Optional[dict]:
        """
        Process event with ordering guarantees.
        
        Args:
            event: Event dict with 'type', 'symbol', 'sequence', 'timestamp'
            
        Returns:
            Processed event if valid, None if duplicate/gap
        """
        event_type = event.get("type")
        symbol = event.get("symbol", "UNKNOWN")
        sequence = event.get("sequence", 0)
        timestamp = event.get("timestamp", 0)

        with self._lock:
            state = self._sequences[symbol]
            
            if sequence <= state.last_sequence:
                if sequence == state.last_sequence:
                    self._stats["duplicates_filtered"] += 1
                    logger.debug(f"Duplicate event filtered: {event_type} {symbol} seq={sequence}")
                    return None
                else:
                    logger.warning(f"Out-of-order event: {event_type} {symbol} seq={sequence} < last={state.last_sequence}")
                    self._stats["out_of_order_buffered"] += 1
            
            gap = sequence - state.last_sequence - 1
            if gap > 0 and state.last_sequence > 0:
                if gap > self._max_sequence_gap:
                    logger.error(f"Large sequence gap detected: {symbol} gap={gap}")
                    self._stats["gaps_detected"] += 1
                    state.gap_detected = True
                    await self._notify_violation("sequence_gap", symbol, gap)
            
            time_drift = abs(timestamp - state.last_timestamp)
            if time_drift > self._time_drift_threshold_ms and state.last_timestamp > 0:
                logger.warning(f"Time drift detected: {symbol} drift={time_drift}ms")
                self._stats["time_drifts_detected"] += 1
                await self._notify_violation("time_drift", symbol, time_drift)
            
            state.last_sequence = sequence
            state.last_timestamp = timestamp
            self._stats["events_ordered"] += 1
            
            return event

    async def _notify_violation(self, violation_type: str, symbol: str, value: any) -> None:
        """Notify registered callbacks of ordering violations."""
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(violation_type, symbol, value)
                else:
                    callback(violation_type, symbol, value)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    def get_sequence(self, symbol: str) -> int:
        """Get current sequence for symbol."""
        return self._sequences.get(symbol, SequenceState()).last_sequence

    def get_pending_count(self, symbol: str) -> int:
        """Get number of pending events for symbol."""
        return len(self._sequences.get(symbol, SequenceState()).pending_events)

    def has_gap(self, symbol: str) -> bool:
        """Check if gap detected for symbol."""
        return self._sequences.get(symbol, SequenceState()).gap_detected

    def reset_sequence(self, symbol: str) -> None:
        """Reset sequence for symbol (e.g., after reconnection)."""
        with self._lock:
            self._sequences[symbol] = SequenceState()
            logger.info(f"Sequence reset for {symbol}")

    def get_stats(self) -> dict:
        """Get ordering engine statistics."""
        return self._stats.copy()

    async def cleanup(self) -> None:
        """Periodic cleanup of old state."""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval_sec:
            return
        
        self._last_cleanup = now
        
        with self._lock:
            cleaned = 0
            for symbol, state in list(self._sequences.items()):
                if state.pending_events:
                    cutoff = now - 300
                    state.pending_events = [
                        e for e in state.pending_events
                        if e.get("timestamp", 0) / 1000 > cutoff
                    ]
                    cleaned += 1
            
            if cleaned > 0:
                logger.info(f"Cleaned {cleaned} symbol states")


class EventValidator:
    """Validates event structure and required fields."""
    
    REQUIRED_FIELDS = {
        "type": str,
        "timestamp": (int, float),
        "sequence": int
    }
    
    @classmethod
    def validate(cls, event: dict) -> tuple[bool, Optional[str]]:
        """Validate event has required fields."""
        for field, expected_type in cls.REQUIRED_FIELDS.items():
            if field not in event:
                return False, f"Missing required field: {field}"
            
            if not isinstance(event[field], expected_type):
                return False, f"Invalid type for {field}: {type(event[field])}"
        
        return True, None