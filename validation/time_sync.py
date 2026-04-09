"""
Time Synchronizer - Synchronizes time across system components.

Ensures consistent time tracking and detects time drift.
"""

import logging
from typing import Optional
from dataclasses import asdict
from datetime import datetime, timedelta
import time

from core.event import Event, EventType
from core.bus import EventBus
from core.state import DistributedState

logger = logging.getLogger(__name__)


class TimeSynchronizer:
    """
    Synchronizes time across system components.
    
    Features:
    - Clock drift detection
    - Latency measurement
    - Time source validation
    - Drift correction
    """
    
    MAX_DRIFT_MS = 1000
    DRIFT_WARNING_MS = 500
    SYNC_INTERVAL_MS = 60000
    SAMPLE_SIZE = 10
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
    ):
        self.bus = bus
        self.state = state
        
        self._local_offset: float = 0
        self._offset_samples: list[float] = []
        self._last_sync_time: Optional[int] = None
        self._drift_history: list[dict] = []
        self._is_synchronized: bool = False
        
    async def sync_time(
        self,
        external_time_ms: Optional[int] = None
    ) -> dict:
        """
        Synchronize local time with external time source.
        
        Returns:
            Sync result with offset and confidence
        """
        if external_time_ms is None:
            external_time_ms = int(datetime.now().timestamp() * 1000)
        
        local_time_before = int(time.time() * 1000)
        
        round_trip_ms = 0
        
        local_time_after = int(time.time() * 1000)
        estimated_external_now = external_time_ms + round_trip_ms / 2
        
        offset = estimated_external_now - ((local_time_before + local_time_after) / 2)
        
        self._offset_samples.append(offset)
        if len(self._offset_samples) > self.SAMPLE_SIZE:
            self._offset_samples = self._offset_samples[-self.SAMPLE_SIZE:]
        
        median_offset = self._compute_median_offset()
        
        self._local_offset = median_offset
        
        self._last_sync_time = int(datetime.now().timestamp() * 1000)
        
        drift = self._measure_drift()
        
        sync_result = {
            'offset_ms': median_offset,
            'drift_ms': drift,
            'is_synchronized': abs(drift) < self.MAX_DRIFT_MS,
            'confidence': self._compute_sync_confidence(),
            'timestamp': self._last_sync_time,
        }
        
        await self._update_sync_state(sync_result)
        
        if abs(drift) > self.DRIFT_WARNING_MS:
            await self._emit_drift_alert(drift, sync_result)
        
        return sync_result
    
    def get_corrected_time(self) -> int:
        """
        Get corrected time based on known offset.
        """
        return int(datetime.now().timestamp() * 1000) + self._local_offset
    
    def _measure_drift(self) -> float:
        """
        Measure clock drift since last sync.
        """
        if not self._last_sync_time:
            return 0
        
        elapsed_ms = int(datetime.now().timestamp() * 1000) - self._last_sync_time
        
        expected_offset = self._local_offset
        actual_offset = self._offset_samples[-1] if self._offset_samples else 0
        
        drift = actual_offset - expected_offset
        
        return drift
    
    def _compute_median_offset(self) -> float:
        """Compute median offset from samples."""
        if not self._offset_samples:
            return 0
        
        sorted_samples = sorted(self._offset_samples)
        n = len(sorted_samples)
        
        if n % 2 == 0:
            return (sorted_samples[n // 2 - 1] + sorted_samples[n // 2]) / 2
        else:
            return sorted_samples[n // 2]
    
    def _compute_sync_confidence(self) -> float:
        """Compute confidence in time sync."""
        if len(self._offset_samples) < 3:
            return 0.5
        
        mean = sum(self._offset_samples) / len(self._offset_samples)
        variance = sum((x - mean) ** 2 for x in self._offset_samples) / len(self._offset_samples)
        std = variance ** 0.5
        
        consistency_factor = max(0, 1 - std / 100)
        
        recency_factor = 1.0
        if self._last_sync_time:
            elapsed = int(datetime.now().timestamp() * 1000) - self._last_sync_time
            recency_factor = max(0.5, 1 - elapsed / (self.SYNC_INTERVAL_MS * 10))
        
        return consistency_factor * recency_factor
    
    async def validate_event_timing(
        self,
        event_timestamp: int,
        received_timestamp: int
    ) -> tuple[bool, dict]:
        """
        Validate timing of received event.
        
        Returns:
            (is_valid, timing_details)
        """
        expected_timestamp = self.get_corrected_time()
        
        event_age_ms = expected_timestamp - event_timestamp
        
        processing_latency_ms = received_timestamp - event_timestamp
        
        network_latency_ms = processing_latency_ms - event_age_ms
        
        details = {
            'event_timestamp': event_timestamp,
            'received_timestamp': received_timestamp,
            'expected_timestamp': expected_timestamp,
            'event_age_ms': event_age_ms,
            'processing_latency_ms': processing_latency_ms,
            'network_latency_ms': network_latency_ms,
        }
        
        is_valid = (
            event_age_ms >= 0 and
            event_age_ms < 10000 and
            processing_latency_ms < 5000
        )
        
        return is_valid, details
    
    async def should_resync(self) -> tuple[bool, str]:
        """
        Determine if time resync is needed.
        """
        if not self._last_sync_time:
            return True, "never_synced"
        
        elapsed = int(datetime.now().timestamp() * 1000) - self._last_sync_time
        
        if elapsed > self.SYNC_INTERVAL_MS * 2:
            return True, f"sync_interval_exceeded_{elapsed}ms"
        
        drift = self._measure_drift()
        if abs(drift) > self.DRIFT_WARNING_MS:
            return True, f"drift_detected_{drift}ms"
        
        confidence = self._compute_sync_confidence()
        if confidence < 0.5:
            return True, f"low_confidence_{confidence:.2f}"
        
        return False, "ok"
    
    async def _update_sync_state(self, sync_result: dict) -> None:
        if not self.state:
            return
            
        await self.state.set(
            key="time_sync:global",
            value={
                'offset_ms': sync_result['offset_ms'],
                'drift_ms': sync_result['drift_ms'],
                'is_synchronized': sync_result['is_synchronized'],
                'confidence': sync_result['confidence'],
                'last_sync_time': sync_result['timestamp'],
                'updated_at': int(datetime.now().timestamp() * 1000),
            },
            trace_id="time_synchronizer",
        )
        
        self._drift_history.append({
            'drift_ms': sync_result['drift_ms'],
            'confidence': sync_result['confidence'],
            'timestamp': sync_result['timestamp'],
        })
        
        if len(self._drift_history) > 100:
            self._drift_history = self._drift_history[-100:]
    
    async def _emit_drift_alert(
        self,
        drift_ms: float,
        sync_result: dict
    ) -> None:
        """Emit time drift alert."""
        logger.warning(
            f"Time drift detected: {drift_ms:.1f}ms",
            extra={
                'drift_ms': drift_ms,
                'confidence': sync_result['confidence'],
            }
        )
        
        if self.bus:
            alert_event = Event.create(
                event_type=EventType.TIME_DRIFT_DETECTED,
                payload={
                    'drift_ms': drift_ms,
                    'confidence': sync_result['confidence'],
                    'offset_ms': sync_result['offset_ms'],
                },
                source="time_synchronizer",
            )
            await self.bus.publish(alert_event)
    
    async def get_time_report(self) -> dict:
        """Get comprehensive time sync report."""
        avg_drift = (
            sum(d['drift_ms'] for d in self._drift_history) / len(self._drift_history)
            if self._drift_history else 0
        )
        
        max_drift = (
            max(abs(d['drift_ms']) for d in self._drift_history)
            if self._drift_history else 0
        )
        
        return {
            'is_synchronized': self._is_synchronized,
            'current_offset_ms': self._local_offset,
            'last_sync_time': self._last_sync_time,
            'average_drift_ms': avg_drift,
            'max_drift_ms': max_drift,
            'sync_count': len(self._drift_history),
            'avg_confidence': (
                sum(d['confidence'] for d in self._drift_history) / len(self._drift_history)
                if self._drift_history else 0
            ),
        }
