"""
Time Sync & Rate Limit System.

Features:
- Time synchronization with exchange
- Rate limiting for API calls
- Request queuing and batching
- Adaptive rate limiting
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from collections import deque
from typing import Optional, Callable

logger = logging.getLogger(__name__)


@dataclass
class TimeSyncState:
    """Time synchronization state."""
    local_time: int = 0
    exchange_time: int = 0
    offset_ms: int = 0
    last_sync: int = 0
    sync_count: int = 0
    drift_detected: bool = False


@dataclass
class RateLimitConfig:
    """Rate limit configuration."""
    requests_per_second: int = 10
    requests_per_minute: int = 120
    requests_per_hour: int = 10000
    burst_limit: int = 20


class RateLimiter:
    """
    Token bucket rate limiter.
    
    Features:
    - Multiple time window tracking
    - Burst handling
    - Adaptive limiting
    """

    def __init__(self, config: RateLimitConfig):
        self.config = config
        
        self._tokens: float = config.burst_limit
        self._last_refill: float = time.time()
        
        self._requests_second: deque = deque(maxlen=config.requests_per_second)
        self._requests_minute: deque = deque(maxlen=config.requests_per_minute)
        self._requests_hour: deque = deque(maxlen=config.requests_per_hour)
        
        self._adaptive_mode = False
        self._current_rps = config.requests_per_second

    async def acquire(self, tokens: int = 1) -> bool:
        """Acquire tokens, return True if allowed."""
        now = time.time()
        
        self._refill_tokens(now)
        self._cleanup_timestamps(now)
        
        if self._tokens < tokens:
            return False
        
        self._requests_second.append(now)
        self._requests_minute.append(now)
        self._requests_hour.append(now)
        
        self._tokens -= tokens
        
        return True

    def _refill_tokens(self, now: float) -> None:
        """Refill token bucket."""
        elapsed = now - self._last_refill
        refill_amount = elapsed * self._current_rps
        
        self._tokens = min(self._tokens + refill_amount, self.config.burst_limit)
        self._last_refill = now

    def _cleanup_timestamps(self, now: float) -> None:
        """Remove old timestamps."""
        cutoff_second = now - 1
        cutoff_minute = now - 60
        cutoff_hour = now - 3600
        
        while self._requests_second and self._requests_second[0] < cutoff_second:
            self._requests_second.popleft()
        
        while self._requests_minute and self._requests_minute[0] < cutoff_minute:
            self._requests_minute.popleft()
        
        while self._requests_hour and self._requests_hour[0] < cutoff_hour:
            self._requests_hour.popleft()

    def get_current_usage(self) -> dict:
        """Get current rate usage."""
        now = time.time()
        self._cleanup_timestamps(now)
        
        return {
            "second": len(self._requests_second),
            "minute": len(self._requests_minute),
            "hour": len(self._requests_hour),
            "tokens": self._tokens,
            "adaptive": self._adaptive_mode
        }

    def enable_adaptive(self, enabled: bool) -> None:
        """Enable adaptive rate limiting."""
        self._adaptive_mode = enabled

    def adjust_limits(self, factor: float) -> None:
        """Adjust rate limits by factor."""
        self._current_rps = max(1, int(self._current_rps * factor))
        logger.info(f"Rate limits adjusted: {self._current_rps} req/s")


class TimeSynchronizer:
    """
    Time synchronization with exchange.
    
    Features:
    - NTP-style time sync
    - Drift detection
    - Offset compensation
    """

    def __init__(
        self,
        sync_interval_sec: int = 60,
        max_drift_ms: int = 1000,
        samples_for_sync: int = 5
    ):
        self.sync_interval_sec = sync_interval_sec
        self.max_drift_ms = max_drift_ms
        self.samples_for_sync = samples_for_sync
        
        self._state = TimeSyncState()
        self._sync_samples: list[tuple[int, int, int]] = []
        
        self._callbacks: list[Callable] = []
        
        self._stats = {
            "syncs_attempted": 0,
            "syncs_successful": 0,
            "drifts_detected": 0,
            "corrections_applied": 0
        }

    def register_callback(self, callback: Callable) -> None:
        """Register callback for time sync events."""
        self._callbacks.append(callback)

    async def sync(self, exchange_time_ms: int) -> bool:
        """
        Synchronize time with exchange.
        
        Args:
            exchange_time_ms: Current exchange timestamp in milliseconds
            
        Returns:
            True if sync successful
        """
        self._stats["syncs_attempted"] += 1
        
        local_time_ms = int(time.time() * 1000)
        latency_estimate_ms = 50
        
        corrected_exchange_time = exchange_time_ms + latency_estimate_ms
        
        self._sync_samples.append((local_time_ms, corrected_exchange_time, int(time.time() * 1000)))
        
        if len(self._sync_samples) < self.samples_for_sync:
            return False
        
        if len(self._sync_samples) > self.samples_for_sync * 2:
            self._sync_samples = self._sync_samples[-self.samples_for_sync:]
        
        offsets = []
        for local, exchange, _ in self._sync_samples:
            offsets.append(exchange - local)
        
        median_offset = sorted(offsets)[len(offsets) // 2]
        
        drift = abs(median_offset - self._state.offset_ms)
        
        if drift > self.max_drift_ms:
            self._state.drift_detected = True
            self._stats["drifts_detected"] += 1
            logger.warning(f"Time drift detected: {drift}ms")
            await self._notify_callbacks("drift", drift)
        
        old_offset = self._state.offset_ms
        self._state.offset_ms = median_offset
        self._state.local_time = local_time_ms
        self._state.exchange_time = exchange_time_ms
        self._state.last_sync = int(time.time() * 1000)
        self._state.sync_count += 1
        
        if abs(old_offset - median_offset) > 10:
            self._stats["corrections_applied"] += 1
        
        self._stats["syncs_successful"] += 1
        
        await self._notify_callbacks("sync", self._state.offset_ms)
        
        return True

    def get_exchange_time(self) -> int:
        """Get current time adjusted for offset."""
        return int(time.time() * 1000) + self._state.offset_ms

    def get_local_time(self) -> int:
        """Get local time in milliseconds."""
        return int(time.time() * 1000)

    def get_offset(self) -> int:
        """Get current time offset in ms."""
        return self._state.offset_ms

    def is_healthy(self) -> bool:
        """Check if time sync is healthy."""
        if self._state.sync_count < 3:
            return True
        
        if self._state.drift_detected:
            return False
        
        time_since_sync = (int(time.time() * 1000) - self._state.last_sync) / 1000
        return time_since_sync < self.sync_interval_sec * 2

    async def _notify_callbacks(self, event: str, data: any) -> None:
        """Notify registered callbacks."""
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event, data)
                else:
                    callback(event, data)
            except Exception as e:
                logger.error(f"Time sync callback error: {e}")

    def get_stats(self) -> dict:
        """Get time sync statistics."""
        return {
            **self._stats,
            "current_offset_ms": self._state.offset_ms,
            "sync_count": self._state.sync_count,
            "drift_detected": self._state.drift_detected
        }


class RequestQueue:
    """
    Request queue with rate limiting and prioritization.
    
    Features:
    - Priority-based ordering
    - Automatic retry
    - Request batching
    """

    def __init__(
        self,
        rate_limiter: RateLimiter,
        max_queue_size: int = 100,
        max_retries: int = 3,
        retry_delay_sec: float = 1.0
    ):
        self.rate_limiter = rate_limiter
        self.max_queue_size = max_queue_size
        self.max_retries = max_retries
        self.retry_delay_sec = retry_delay_sec
        
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=max_queue_size)
        self._running = False
        
        self._stats = {
            "requests_queued": 0,
            "requests_sent": 0,
            "requests_failed": 0,
            "retries": 0,
            "batches": 0
        }

    async def enqueue(
        self,
        priority: int,
        request: Callable,
        *args,
        **kwargs
    ) -> any:
        """Enqueue request with priority."""
        self._queue.put((priority, time.time(), request, args, kwargs))
        self._stats["requests_queued"] += 1

    async def start(self) -> None:
        """Start processing queue."""
        self._running = True
        
        while self._running:
            try:
                priority, timestamp, request, args, kwargs = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=1.0
                )
                
                tokens_needed = 1
                for _ in range(self.max_retries):
                    if await self.rate_limiter.acquire(tokens_needed):
                        try:
                            if asyncio.iscoroutinefunction(request):
                                result = await request(*args, **kwargs)
                            else:
                                result = request(*args, **kwargs)
                            
                            self._stats["requests_sent"] += 1
                            break
                            
                        except Exception as e:
                            logger.error(f"Request failed: {e}")
                            self._stats["requests_failed"] += 1
                            
                            await asyncio.sleep(self.retry_delay_sec)
                            self._stats["retries"] += 1
                    else:
                        await asyncio.sleep(0.1)
                        
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Queue processing error: {e}")

    def stop(self) -> None:
        """Stop processing queue."""
        self._running = False

    def get_stats(self) -> dict:
        """Get queue statistics."""
        return {
            **self._stats,
            "queue_size": self._queue.qsize()
        }


class AdaptiveRateController:
    """
    Adaptive rate controller that adjusts based on response codes.
    
    Features:
    - 429 detection and backoff
    - Success rate monitoring
    - Automatic recovery
    """

    def __init__(
        self,
        rate_limiter: RateLimiter,
        backoff_factor: float = 0.5,
        recovery_factor: float = 1.2
    ):
        self.rate_limiter = rate_limiter
        self.backoff_factor = backoff_factor
        self.recovery_factor = recovery_factor
        
        self._consecutive_errors = 0
        self._consecutive_429s = 0
        self._last_rate = rate_limiter.config.requests_per_second
        
        self._stats = {
            "rate_adjustments": 0,
            "backoffs": 0,
            "recoveries": 0,
            "rate_limit_hits": 0
        }

    def on_response(self, status_code: int) -> None:
        """Process response and adjust if needed."""
        if status_code == 429:
            self._consecutive_429s += 1
            self._consecutive_errors += 1
            self._stats["rate_limit_hits"] += 1
            
            if self._consecutive_429s >= 2:
                self._apply_backoff()
                
        elif status_code >= 400:
            self._consecutive_errors += 1
            
            if self._consecutive_errors >= 3:
                self._apply_backoff()
                
        else:
            self._consecutive_errors = 0
            self._consecutive_429s = 0
            
            if self._last_rate < self.rate_limiter.config.requests_per_second:
                self._attempt_recovery()

    def _apply_backoff(self) -> None:
        """Apply rate limit backoff."""
        new_rate = max(1, int(self._last_rate * self.backoff_factor))
        self.rate_limiter.adjust_limits(self.backoff_factor)
        self._last_rate = new_rate
        
        self._stats["backoffs"] += 1
        self._stats["rate_adjustments"] += 1
        
        logger.warning(f"Rate limit backoff applied: {new_rate} req/s")

    def _attempt_recovery(self) -> None:
        """Attempt to recover rate limit."""
        new_rate = min(
            self.rate_limiter.config.requests_per_second,
            int(self._last_rate * self.recovery_factor)
        )
        self.rate_limiter._current_rps = new_rate
        self._last_rate = new_rate
        
        self._stats["recoveries"] += 1
        self._stats["rate_adjustments"] += 1
        
        logger.info(f"Rate limit recovery: {new_rate} req/s")

    def reset(self) -> None:
        """Reset controller state."""
        self._consecutive_errors = 0
        self._consecutive_429s = 0

    def get_stats(self) -> dict:
        """Get controller statistics."""
        return self._stats.copy()