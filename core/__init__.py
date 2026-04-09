"""
Core infrastructure for event-driven trading system.

Exports:
    Event: Base event class with schema
    EventType: All event type enums
    EventBus: Abstract event bus
    RedisEventBus: Redis-based event bus with PostgreSQL persistence
    InProcessEventBus: In-process event bus for testing
    StreamProcessor: Base class for event processors
    DistributedState: Hot/cold state management
    ReplayEngine: Event replay and state reconstruction
    Supervisor: Process monitoring and lifecycle management
"""

from core.event import (
    Event,
    EventType,
    EventChannel,
    EventPriority,
    create_market_tick_event,
    create_order_event,
    create_halt_event,
)

from core.bus import (
    EventBus,
    RedisEventBus,
    InProcessEventBus,
    BusConfig,
    EventChannel,
)

from core.processor import (
    StreamProcessor,
    CompositeProcessor,
    ProcessorConfig,
    ProcessorMetrics,
)

from core.state import (
    DistributedState,
    PositionState,
    StateValue,
)

from core.replay import (
    ReplayEngine,
    ReplayConfig,
    ReplayResult,
    SimulationEngine,
)

from core.supervisor import (
    Supervisor,
    SupervisorConfig,
    ComponentStatus,
    ComponentHealth,
    HealthEndpoint,
)

__all__ = [
    # Event
    'Event',
    'EventType',
    'EventChannel',
    'EventPriority',
    'create_market_tick_event',
    'create_order_event',
    'create_halt_event',
    
    # Bus
    'EventBus',
    'RedisEventBus',
    'InProcessEventBus',
    'BusConfig',
    
    # Processor
    'StreamProcessor',
    'CompositeProcessor',
    'ProcessorConfig',
    'ProcessorMetrics',
    
    # State
    'DistributedState',
    'PositionState',
    'StateValue',
    
    # Replay
    'ReplayEngine',
    'ReplayConfig',
    'ReplayResult',
    'SimulationEngine',
    
    # Supervisor
    'Supervisor',
    'SupervisorConfig',
    'ComponentStatus',
    'ComponentHealth',
    'HealthEndpoint',
]
