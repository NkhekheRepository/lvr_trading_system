"""
Base Processor - Abstract base for all stream processors.

Extends core.StreamProcessor with trading-specific functionality.
"""

import logging
from abc import abstractmethod
from typing import Optional, Any

from core.event import Event, EventType
from core.processor import StreamProcessor, ProcessorConfig
from core.bus import EventBus
from core.state import DistributedState

logger = logging.getLogger(__name__)


class BaseProcessor(StreamProcessor):
    """
    Base class for all trading system processors.
    
    Provides:
    - Access to distributed state
    - Symbol-specific processing
    - Common validation logic
    - Error handling
    """
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        config: Optional[ProcessorConfig] = None,
    ):
        super().__init__(bus, config)
        self.state = state
        
    @abstractmethod
    def event_types(self) -> list[EventType]:
        """Return list of EventTypes this processor subscribes to."""
        pass
    
    @abstractmethod
    async def process_event(self, event: Event) -> Optional[Event | list[Event]]:
        """
        Process a single event.
        
        Override this instead of process() for simpler implementation.
        """
        pass
    
    async def process(self, event: Event) -> Optional[Event | list[Event]]:
        """Process event with error handling."""
        try:
            return await self.process_event(event)
        except Exception as e:
            logger.error(
                f"{self.name} error processing {event.event_id}: {e}",
                extra={
                    'trace_id': event.trace_id,
                    'event_type': event.type.value,
                    'symbol': event.symbol,
                }
            )
            self.metrics.record_failure(str(e))
            return None
            
    async def _validate(self, event: Event) -> bool:
        """Validate event before processing."""
        if not event.symbol:
            return False
        if not event.payload:
            return False
        return True
        
    async def get_symbol_state(self, symbol: str) -> Optional[dict]:
        """Get state for a specific symbol."""
        if not self.state:
            return None
        state = await self.state.get(f"symbol:{symbol}")
        return state.value if state else None
        
    async def update_symbol_state(
        self,
        symbol: str,
        update_fn,
        trace_id: Optional[str] = None,
    ) -> None:
        """Update state for a specific symbol."""
        if self.state:
            await self.state.atomic_update(
                key=f"symbol:{symbol}",
                update_fn=update_fn,
                trace_id=trace_id or self.config.name,
            )


class PassthroughProcessor(BaseProcessor):
    """
    Simple processor that passes events through with optional transformation.
    
    Useful for logging, metrics, or simple transformations.
    """
    
    def __init__(
        self,
        bus: EventBus,
        event_types: list[EventType],
        transform_fn=None,
        config: Optional[ProcessorConfig] = None,
    ):
        super().__init__(bus, config=config)
        self._event_types = event_types
        self._transform_fn = transform_fn
        
    def event_types(self) -> list[EventType]:
        return self._event_types
        
    async def process_event(self, event: Event) -> Optional[Event | list[Event]]:
        if self._transform_fn:
            return await self._transform_fn(event)
        return event


class FilterProcessor(BaseProcessor):
    """
    Processor that filters events based on predicates.
    """
    
    def __init__(
        self,
        bus: EventBus,
        event_types: list[EventType],
        filter_fn,
        config: Optional[ProcessorConfig] = None,
    ):
        super().__init__(bus, config=config)
        self._event_types = event_types
        self._filter_fn = filter_fn
        
    def event_types(self) -> list[EventType]:
        return self._event_types
        
    async def process_event(self, event: Event) -> Optional[Event | list[Event]]:
        if await self._filter_fn(event):
            return event
        return None
