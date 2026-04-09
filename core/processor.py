"""
Stream Processor - Base class for all event stream processors.

Each processor:
- Consumes specific events
- Produces new events
- Is stateless (idempotent, restartable)
- Has priority for scheduling
- Implements backpressure handling
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, TypeVar, Generic

from core.event import Event, EventType, EventChannel
from core.bus import EventBus

logger = logging.getLogger(__name__)


@dataclass
class ProcessorConfig:
    """Configuration for stream processor."""
    name: str
    priority: int = 2  # 1=HIGH, 2=MEDIUM, 3=LOW
    queue_limit: int = 10000
    batch_size: int = 1
    batch_timeout: float = 0.1
    max_retries: int = 3
    retry_delay: float = 1.0
    enable_metrics: bool = True


@dataclass
class ProcessorMetrics:
    """Metrics for stream processor monitoring."""
    name: str
    events_processed: int = 0
    events_produced: int = 0
    events_failed: int = 0
    events_dropped: int = 0
    total_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    queue_size: int = 0
    last_processed_at: Optional[datetime] = None
    errors: list = field(default_factory=list)
    
    def record_process(self, latency_ms: float) -> None:
        """Record successful processing."""
        self.events_processed += 1
        self.total_latency_ms += latency_ms
        self.avg_latency_ms = self.total_latency_ms / max(self.events_processed, 1)
        self.last_processed_at = datetime.now()
        
    def record_produce(self, count: int = 1) -> None:
        """Record events produced."""
        self.events_produced += count
        
    def record_failure(self, error: str) -> None:
        """Record processing failure."""
        self.events_failed += 1
        self.errors.append({
            'timestamp': datetime.now().isoformat(),
            'error': error,
        })
        if len(self.errors) > 100:
            self.errors = self.errors[-50:]
            
    def record_drop(self) -> None:
        """Record dropped event (backpressure)."""
        self.events_dropped += 1


T = TypeVar('T', bound=Event)


class StreamProcessor(ABC, Generic[T]):
    """
    Base class for all stream processors.
    
    Processors are:
    - Stateless: Process events independently, no shared state
    - Idempotent: Same input always produces same output
    - Restartable: Can resume from any event offset
    - Ordered: Process events in sequence order
    
    Subclass must implement:
    - event_types(): List of EventTypes this processor subscribes to
    - process(event) -> Optional[Event | list[Event]]
    """
    
    def __init__(
        self,
        bus: EventBus,
        config: Optional[ProcessorConfig] = None,
    ):
        self.bus = bus
        self.config = config or ProcessorConfig(name=self.__class__.__name__)
        self.metrics = ProcessorMetrics(name=self.config.name)
        
        self._queue: deque[Event] = deque(maxlen=self.config.queue_limit)
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._processed_ids: set[str] = set()
        
        self._lock = asyncio.Lock()
        
    @property
    def name(self) -> str:
        """Processor name."""
        return self.config.name
        
    @property
    def priority(self) -> int:
        """Processor priority (1=HIGH, 2=MEDIUM, 3=LOW)."""
        return self.config.priority
        
    @abstractmethod
    def event_types(self) -> list[EventType]:
        """Return list of EventTypes this processor subscribes to."""
        pass
    
    @abstractmethod
    async def process(self, event: T) -> Optional[Event | list[Event]]:
        """
        Process a single event and optionally produce new events.
        
        Args:
            event: The event to process
            
        Returns:
            - Single Event to publish
            - List of Events to publish
            - None if no events to publish
        """
        pass
    
    async def _validate(self, event: Event) -> bool:
        """
        Validate event before processing.
        
        Override for custom validation logic.
        """
        return True
        
    def _should_skip(self, event: Event) -> bool:
        """Check if event should be skipped (duplicate)."""
        return event.event_id in self._processed_ids
        
    def _mark_processed(self, event: Event) -> None:
        """Mark event as processed."""
        self._processed_ids.add(event.event_id)
        if len(self._processed_ids) > 50000:
            self._processed_ids = set(list(self._processed_ids)[-25000:])
            
    async def _on_error(self, event: Event, error: Exception) -> None:
        """Handle processing error."""
        self.metrics.record_failure(str(error))
        logger.error(
            f"Processor {self.name} error processing {event.event_id}: {error}",
            extra={
                'trace_id': event.trace_id,
                'event_type': event.type.value,
                'symbol': event.symbol,
            }
        )
        
    async def start(self) -> None:
        """Start the processor."""
        if self._running:
            return
            
        self._running = True
        
        for event_type in self.event_types():
            channel = self._type_to_channel(event_type)
            await self.bus.subscribe(channel, self._handle_event)
            
        self._task = asyncio.create_task(self._process_loop())
        
        logger.info(f"Processor {self.name} started (priority={self.priority})")
        
    async def stop(self) -> None:
        """Stop the processor."""
        self._running = False
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
                
        logger.info(f"Processor {self.name} stopped")
        
    async def _handle_event(self, event: Event) -> None:
        """Handle incoming event."""
        if self._should_skip(event):
            return
            
        if not await self._validate(event):
            self.metrics.record_drop()
            return
            
        if len(self._queue) >= self.config.queue_limit:
            if self.config.priority > 1:
                dropped = self._queue.popleft()
                self.metrics.record_drop()
                logger.warning(f"Processor {self.name}: Queue full, dropped {dropped.event_id}")
            else:
                await asyncio.sleep(0.001)
                
        self._queue.append(event)
        
    async def _process_loop(self) -> None:
        """Main processing loop."""
        while self._running:
            try:
                if not self._queue:
                    await asyncio.sleep(0.001)
                    continue
                    
                event = self._queue.popleft()
                
                start_time = time.perf_counter()
                
                try:
                    result = await self.process(event)
                    
                    latency_ms = (time.perf_counter() - start_time) * 1000
                    self.metrics.record_process(latency_ms)
                    self._mark_processed(event)
                    
                    if result:
                        if isinstance(result, list):
                            for evt in result:
                                await self._publish(evt)
                                self.metrics.record_produce()
                        else:
                            await self._publish(result)
                            self.metrics.record_produce()
                            
                except Exception as e:
                    await self._on_error(event, e)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Processor {self.name} loop error: {e}")
                await asyncio.sleep(0.1)
                
    async def _publish(self, event: Event) -> None:
        """Publish event to bus."""
        try:
            if not event.trace_id:
                event.trace_id = event.event_id
                
            channel = self._type_to_channel(event.type)
            await self.bus.publish(event, channel)
            
        except Exception as e:
            logger.error(f"Failed to publish event: {e}")
            
    def _type_to_channel(self, event_type: EventType) -> EventChannel:
        """Map event type to channel."""
        if event_type in [
            EventType.MARKET_TICK,
            EventType.ORDERBOOK_UPDATE,
            EventType.DATA_VALIDATED,
        ]:
            return EventChannel.MARKET_DATA
            
        if event_type in [
            EventType.FEATURES_COMPUTED,
            EventType.ALPHA_SIGNAL,
            EventType.EDGE_ESTIMATED,
            EventType.EDGE_TRUTH,
            EventType.POSITIVE_EXPECTATION,
            EventType.TRADE_DECISION,
        ]:
            return EventChannel.SIGNALS
            
        if event_type in [
            EventType.ORDER_SUBMITTED,
            EventType.ORDER_PARTIAL,
            EventType.ORDER_FILLED,
            EventType.ORDER_CANCELED,
            EventType.ORDER_REJECTED,
        ]:
            return EventChannel.ORDERS
            
        if event_type in [
            EventType.RISK_EVALUATED,
            EventType.HALT_REQUEST,
            EventType.KILL_SWITCH_TRIGGERED,
            EventType.REALITY_GAP_ALERT,
            EventType.DRAWDOWN_ALERT,
        ]:
            return EventChannel.RISK_EVENTS
            
        return EventChannel.SYSTEM
        
    def get_metrics(self) -> ProcessorMetrics:
        """Get current metrics."""
        self.metrics.queue_size = len(self._queue)
        return self.metrics
        
    async def drain(self) -> int:
        """Drain queue and return count."""
        count = len(self._queue)
        self._queue.clear()
        return count


class CompositeProcessor(StreamProcessor):
    """
    Processor that combines multiple processors.
    
    Useful for grouping related processors.
    """
    
    def __init__(
        self,
        bus: EventBus,
        processors: list[StreamProcessor],
        config: Optional[ProcessorConfig] = None,
    ):
        config = config or ProcessorConfig(name="composite")
        super().__init__(bus, config)
        self.processors = processors
        
    def event_types(self) -> list[EventType]:
        """Union of all sub-processor event types."""
        types = set()
        for p in self.processors:
            types.update(p.event_types())
        return list(types)
        
    async def process(self, event: Event) -> Optional[Event | list[Event]]:
        """Process through all sub-processors."""
        results = []
        for processor in self.processors:
            try:
                result = await processor.process(event)
                if result:
                    if isinstance(result, list):
                        results.extend(result)
                    else:
                        results.append(result)
            except Exception as e:
                logger.error(f"Sub-processor {processor.name} error: {e}")
        return results if results else None
        
    async def start(self) -> None:
        """Start all sub-processors."""
        for p in self.processors:
            await p.start()
            
    async def stop(self) -> None:
        """Stop all sub-processors."""
        for p in self.processors:
            await p.stop()
