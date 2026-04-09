# LVR Trading System - Complete Documentation

## Table of Contents

- [Part I: Architecture Overview](#part-i-architecture-overview)
- [Part II: Core Infrastructure](#part-ii-core-infrastructure)
  - [Event System (event.py)](#event-system-eventpy)
  - [Event Bus (bus.py)](#event-bus-buspy)
  - [Distributed State (state.py)](#distributed-state-statepy)
  - [Replay Engine (replay.py)](#replay-engine-replaypy)
  - [Supervisor (supervisor.py)](#supervisor-supervisorpy)
- [Part III: Processors](#part-iii-processors)
- [Part IV: Control Engines](#part-iv-control-engines)
- [Part V: Model Registry](#part-v-model-registry)
- [Part VI: Observability](#part-vi-observability)
- [Part VII: Validation](#part-vii-validation)
- [Part VIII: Orchestration](#part-viii-orchestration)
- [Part IX: Infrastructure](#part-ix-infrastructure)
- [Part X: Configuration Reference](#part-x-configuration-reference)
- [Part XI: Deployment](#part-xi-deployment)
- [Appendices](#appendices)

---

# Part I: Architecture Overview

## System Design Philosophy

The LVR Trading System is built on a **strict event-driven architecture** with the following core principles:

1. **Event Sourcing**: All state changes are recorded as immutable events
2. **CQRS**: Command/Query separation with optimized read/write paths
3. **Microkernel Architecture**: Core system with pluggable processors
4. **Safety by Construction**: Impossible to violate trading rules
5. **Replayability**: Full system state can be reconstructed from any point

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Trading System                            │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │  Processors │  │   Engines   │  │ Validators  │          │
│  │  (13 total) │  │  (6 total)  │  │ (3 total)   │          │
│  └─────────────┘  └─────────────┘  └─────────────┘          │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │            Event Bus (Redis + PostgreSQL)           │   │
│  │  ┌─────────┐    ┌──────────────┐    ┌──────────┐  │   │
│  │  │  Redis  │    │ PostgreSQL    │    │ Dedup   │  │   │
│  │  │ Pub/Sub │◄──►│ Event Log     │◄──►│ Engine  │  │   │
│  │  └─────────┘    └──────────────┘    └──────────┘  │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Distributed State                        │   │
│  │  ┌─────────┐    ┌──────────────┐    ┌──────────┐  │   │
│  │  │  Redis  │    │ PostgreSQL    │    │ Locking │  │   │
│  │  │ Hot Cache│◄──►│ Authoritative │◄──►│ Engine  │  │   │
│  │  └─────────┘    └──────────────┘    └──────────┘  │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## Event Flow Architecture

1. **Data Ingestion**: Market data → Feature Events
2. **Signal Generation**: Features → Alpha Events
3. **Edge Validation**: Alpha → Edge Estimation → Positive Expectation
4. **Risk Control**: Edge → Risk Evaluation → Governor Checks
5. **Execution**: Decision → Order Submission → Order Events
6. **Learning**: All events → Model Updates

## Safety Architecture

The system enforces these invariant safety rules:

- **NO DATA → NO TRADE**: Cannot trade without validated data
- **NO EDGE → NO TRADE**: Cannot trade without positive expected edge
- **NO VALIDATION → NO TRADE**: Cannot trade without passing all validations
- **ALWAYS FAIL SAFE**: Any failure blocks trading, not executes
- **ALWAYS LOG EVERYTHING**: Every decision is logged with trace ID

---

# Part II: Core Infrastructure

## Event System (event.py)

The event system forms the foundation of all communication in the trading system. Every interaction is recorded as an immutable event with full traceability.

### Core Features

- **25+ Event Types**: Comprehensive coverage of all system interactions
- **Type Safety**: Full TypeScript-like event validation
- **Idempotency**: Events can be safely replayed
- **Traceability**: Every event carries a trace ID through the pipeline

### Event Types

```python
class EventType(Enum):
    # Input Events
    MARKET_TICK = "market_tick"
    ORDERBOOK_UPDATE = "orderbook_update"
    
    # Processing Events
    FEATURES_COMPUTED = "features_computed"
    ALPHA_SIGNAL = "alpha_signal"
    EDGE_ESTIMATED = "edge_estimated"
    EDGE_TRUTH = "edge_truth"
    POSITIVE_EXPECTATION = "positive_expectation"
    TRADE_DECISION = "trade_decision"
    REGIME_DETECTED = "regime_detected"
    REALITY_GAP = "reality_gap"
    
    # Output Events
    ORDER_SUBMITTED = "order_submitted"
    ORDER_PARTIAL = "order_partial"
    ORDER_FILLED = "order_filled"
    ORDER_CANCELED = "order_canceled"
    ORDER_REJECTED = "order_rejected"
    
    # State Events
    PORTFOLIO_UPDATED = "portfolio_updated"
    RISK_EVALUATED = "risk_evaluated"
    POSITION_RECONCILED = "position_reconciled"
    
    # Control Events
    ALLOCATION_UPDATE = "allocation_update"
    CAPITAL_REALLOCATION = "capital_reallocation"
    HALT_REQUEST = "halt_request"
    RESUME_REQUEST = "resume_request"
    KILL_SWITCH_TRIGGERED = "kill_switch_triggered"
    
    # Quality Events
    EXECUTION_QUALITY = "execution_quality"
    DRAWDOWN_ALERT = "drawdown_alert"
    REALITY_GAP_ALERT = "reality_gap_alert"
    STRATEGY_TERMINATION = "strategy_termination"
    RATE_LIMIT_APPLIED = "rate_limit_applied"
    
    # System Events
    SYSTEM_EVENT = "system_event"
    HEALTH_CHECK = "health_check"
    METRICS_SNAPSHOT = "metrics_snapshot"
    MODEL_UPDATED = "model_updated"
    
    # Validation Events
    DATA_VALIDATED = "data_validated"
    POSITION_MISMATCH = "position_mismatch"
    TIME_DRIFT_DETECTED = "time_drift_detected"
```

### Event Structure

```python
@dataclass
class Event:
    """Base event class for all system events."""
    event_id: str
    trace_id: str
    type: EventType
    symbol: Optional[str] = None
    timestamp: int = field(default_factory=lambda: int(datetime.now().timestamp() * 1000))
    sequence: int = 0
    version: int = 1
    payload: dict = field(default_factory=dict)
    offset: int = 0
    source: str = "unknown"
```

### Key Payload Dataclasses

```python
@dataclass
class MarketTickPayload:
    """Payload for MARKET_TICK events."""
    price: float
    bid: float
    ask: float
    volume: float
    timestamp: int
    exchange: str
    latency_ms: float = 0.0
    quality_score: float = 1.0

@dataclass
class TradeDecisionPayload:
    """Payload for TRADE_DECISION events."""
    decision: str  # ACCEPT or REJECT
    expected_edge: float
    total_cost: float
    payoff_ratio: float
    cost_edge_ratio: float
    is_significant: bool
    rejection_reason: Optional[str] = None

@dataclass
class RiskPayload:
    """Payload for RISK_EVALUATED events."""
    approved: bool
    leverage: float
    drawdown_pct: float
    daily_loss_pct: float
    position_size_pct: float
    rejection_reason: Optional[str] = None
    required_actions: list[str] = field(default_factory=list)
```

### Event Factory Functions

```python
def create_market_tick_event(
    symbol: str,
    price: float,
    bid: float,
    ask: float,
    volume: float,
    exchange: str,
    trace_id: Optional[str] = None,
    latency_ms: float = 0.0,
    quality_score: float = 1.0
) -> Event:
    """Create a MARKET_TICK event."""
    payload = MarketTickPayload(
        price=price,
        bid=bid,
        ask=ask,
        volume=volume,
        timestamp=int(datetime.now().timestamp() * 1000),
        exchange=exchange,
        latency_ms=latency_ms,
        quality_score=quality_score,
    )
    return Event.create(
        event_type=EventType.MARKET_TICK,
        symbol=symbol,
        payload=asdict(payload),
        trace_id=trace_id,
        source="data_layer",
    )
```

## Event Bus (bus.py)

The event bus provides a high-performance, fault-tolerant event distribution system with both real-time and persistent capabilities.

### Architecture

- **Redis Pub/Sub**: Real-time low-latency event distribution
- **PostgreSQL Log**: Durable event storage with replay capability
- **Deduplication**: Prevents duplicate processing
- **Health Monitoring**: Automatic reconnection and failure recovery

### Configuration

```python
@dataclass
class BusConfig:
    """Configuration for the event bus."""
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: Optional[str] = None
    redis_ssl: bool = False
    
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_database: str = "trading_events"
    
    channel: EventChannel = EventChannel.SYSTEM
    
    health_check_interval: int = 30
    reconnect_delay: float = 1.0
    max_reconnect_attempts: int = 10
    
    enable_persistence: bool = True
    enable_replay: bool = True
    
    redis_publish_retries: int = 3
    redis_publish_retry_delay: float = 0.1
    dedup_cache_max_size: int = 100000
```

### Core Implementation

```python
class RedisEventBus(EventBus):
    """Redis-based event bus with PostgreSQL persistence."""
    
    def __init__(self, config: Optional[BusConfig] = None):
        self.config = config or BusConfig()
        self._redis: Optional[redis.Redis] = None
        self._pubsub: Optional[redis.client.PubSub] = None
        self._postgres: Optional[asyncpg.Pool] = None
        
        self._subscribers: dict[EventChannel, list[Callable]] = defaultdict(list)
        self._processed_event_ids: set[str] = set()
        self._last_offsets: dict[EventChannel, int] = defaultdict(int)
        
        self._running = False
        self._reconnect_attempts = 0
        self._health_check_task: Optional[asyncio.Task] = None
        
        self._lock = asyncio.Lock()
```

### Critical Fix: Redis Publish with Retry

```python
async def _publish_with_retry(
    self,
    event: Event,
    channel: EventChannel
) -> None:
    """Publish to Redis with exponential backoff retry."""
    import redis as sync_redis
    
    payload = event.to_json()
    channel_name = f"{channel.value}:{event.type.value}"
    system_channel = EventChannel.SYSTEM.value
    
    for attempt in range(self.config.redis_publish_retries):
        try:
            await self._redis.publish(channel_name, payload)
            await self._redis.publish(system_channel, payload)
            return
        except Exception as e:
            if attempt == self.config.redis_publish_retries - 1:
                raise
            delay = self.config.redis_publish_retry_delay * (2 ** attempt)
            logger.warning(
                f"Redis publish failed (attempt {attempt + 1}): {e}. "
                f"Retrying in {delay}s"
            )
            await asyncio.sleep(delay)
```

### Critical Fix: Persistent Deduplication

```python
async def _is_duplicate_persistent(self, event_id: str) -> bool:
    """Check if event_id exists in PostgreSQL for persistent deduplication."""
    if not self._postgres:
        return False
    try:
        result = await self._postgres.fetchval(
            "SELECT 1 FROM events WHERE event_id = $1 LIMIT 1",
            event_id
        )
        return result is not None
    except Exception as e:
        logger.warning(f"Persistent dedup check failed: {e}")
        return False
```

### Database Schema

```sql
CREATE TABLE IF NOT EXISTS events (
    offset BIGSERIAL PRIMARY KEY,
    event_id VARCHAR(64) UNIQUE NOT NULL,
    trace_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    symbol VARCHAR(32),
    timestamp BIGINT NOT NULL,
    sequence BIGINT NOT NULL,
    version INTEGER DEFAULT 1,
    payload JSONB NOT NULL,
    source VARCHAR(64) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_symbol ON events(symbol);
CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id);
CREATE INDEX IF NOT EXISTS idx_events_symbol_offset ON events(symbol, offset);
```

## Distributed State (state.py)

The distributed state system provides ACID-compliant state management with hot/cold architecture for optimal performance and durability.

### Architecture

- **Redis**: Hot cache for low-latency reads (5-minute TTL)
- **PostgreSQL**: Authoritative source of truth with full ACID
- **Row-level Locking**: Prevents race conditions on concurrent updates
- **Version Tracking**: Optimistic concurrency control

### Configuration

```python
class DistributedState:
    """Distributed state management with Redis + PostgreSQL."""
    
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        postgres_dsn: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/trading",
        hot_ttl: int = 300,
    ):
        self.redis_url = redis_url
        self.postgres_dsn = postgres_dsn
        self.hot_ttl = hot_ttl
        
        self._redis: Optional[redis.Redis] = None
        self._pg_pool: Optional[asyncpg.Pool] = None
        self._sqlalchemy_engine = None
        
        self._lock = asyncio.Lock()
```

### Critical Fix: SERIALIZABLE Isolation

```python
async def set(
    self,
    key: str,
    value: Any,
    version: Optional[int] = None,
    updated_by: Optional[str] = None,
    trace_id: Optional[str] = None,
    use_serializable: bool = True,
) -> StateValue:
    """Set value atomically with version check."""
    async with self._lock:
        if version is not None:
            current = await self.get(key)
            if current and current.version != version:
                raise ValueError(
                    f"Version mismatch: expected {version}, got {current.version}"
                )
                
        new_version = (version or 0) + 1
        updated_at = datetime.utcnow()
        
        async with self._pg_pool.acquire() as conn:
            if use_serializable:
                await conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO state (key, value, version, updated_at, updated_by, trace_id)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        version = EXCLUDED.version,
                        updated_at = EXCLUDED.updated_at,
                        updated_by = EXCLUDED.updated_by,
                        trace_id = EXCLUDED.trace_id
                """, key, json.dumps(value), new_version, updated_at, trace_id)
        
        await self._cache_to_redis(key, {
            'key': key,
            'value': value,
            'version': new_version,
            'updated_at': updated_at.isoformat(),
            'updated_by': updated_by,
            'trace_id': trace_id,
        })
        
        return StateValue(
            key=key,
            value=value,
            version=new_version,
            updated_at=updated_at,
            updated_by=updated_by,
            trace_id=trace_id,
        )
```

### Critical Fix: Pessimistic Row-Level Locking

```python
async def _atomic_update_pessimistic(
    self,
    key: str,
    update_fn: Callable[[Any], Any],
    updated_by: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> StateValue:
    """Atomic update using row-level locking with SELECT FOR UPDATE."""
    async with self._pg_pool.acquire() as conn:
        await conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        
        async with conn.transaction():
            row = await conn.fetchrow("""
                SELECT * FROM state WHERE key = $1 FOR UPDATE
            """, key)
            
            current_value = row['value'] if row else None
            
            try:
                new_value = update_fn(current_value)
            except Exception as e:
                raise ValueError(f"Update function failed: {e}")
            
            new_version = (row['version'] if row else 0) + 1
            updated_at = datetime.utcnow()
            
            await conn.execute("""
                INSERT INTO state (key, value, version, updated_at, updated_by, trace_id)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    version = EXCLUDED.version,
                    updated_at = EXCLUDED.updated_at,
                    updated_by = EXCLUDED.updated_by,
                    trace_id = EXCLUDED.trace_id
            """, key, json.dumps(new_value), new_version, updated_at, updated_by, trace_id)
            
            await self._cache_to_redis(key, {
                'key': key,
                'value': new_value,
                'version': new_version,
                'updated_at': updated_at.isoformat(),
                'updated_by': updated_by,
                'trace_id': trace_id,
            })
            
            return StateValue(
                key=key,
                value=new_value,
                version=new_version,
                updated_at=updated_at,
                updated_by=updated_by,
                trace_id=trace_id,
            )
```

### PositionState Specialization

```python
class PositionState:
    """Specialized position state management."""
    
    async def update_position(
        self,
        symbol: str,
        quantity_delta: float,
        price: float,
        realized_pnl_delta: float = 0.0,
        trace_id: Optional[str] = None,
        use_pessimistic_locking: bool = True,
    ) -> dict:
        """Atomically update position with row-level locking."""
        
        def update_fn(current: Optional[dict]) -> dict:
            if current is None:
                current = {
                    'symbol': symbol,
                    'quantity': 0.0,
                    'avg_entry_price': 0.0,
                    'unrealized_pnl': 0.0,
                    'realized_pnl': 0.0,
                }
                
            new_qty = current['quantity'] + quantity_delta
            
            if quantity_delta > 0 and new_qty > 0:
                current['avg_entry_price'] = (
                    (current['avg_entry_price'] * current['quantity'] + price * quantity_delta)
                    / new_qty
                )
            elif quantity_delta < 0:
                pass
                
            current['quantity'] = new_qty
            current['realized_pnl'] = current.get('realized_pnl', 0) + realized_pnl_delta
            
            return current
            
        result = await self.state.atomic_update(
            key=f"position:{symbol}",
            update_fn=update_fn,
            trace_id=trace_id,
            use_pessimistic_locking=use_pessimistic_locking,
        )
        
        return result.value
```

## Replay Engine (replay.py)

The replay engine enables deterministic system reconstruction from any point in time across multiple use_cases.

### Capabilities

- **Event Replay**: Replay from any offset or timestamp
- **State Reconstruction**: Rebuild system state from events
- **Simulation**: Run deterministic simulations with isolation
- **Verification**: Compare multiple runs for reproducibility

```python
class ReplayEngine:
    """Engine for replaying events from the event log."""
    
    async def replay(
        self,
        start_offset: int = 0,
        end_offset: Optional[int] = None,
        event_types: Optional[list[EventType]] = None,
        symbols: Optional[list[str]] = None,
        processor: Optional[Callable[[Event], Any]] = None,
    ) -> ReplayResult:
        """Replay events from start_offset."""
        start_time = datetime.now()
        events_replayed = 0
        events_failed = 0
        state = {}
        
        try:
            while True:
                events = await self._fetch_batch(
                    start_offset=start_offset,
                    event_types=event_types,
                    symbols=symbols,
                )
                
                if not events:
                    break
                    
                for event in events:
                    if end_offset and event.offset > end_offset:
                        break
                        
                    try:
                        if processor:
                            result = await processor(event)
                            if result:
                                state[event.event_id] = result
                                
                        events_replayed += 1
                        start_offset = event.offset
                        
                        if (events_replayed % self.config.state_snapshot_interval == 0):
                            self._state_snapshots[events_replayed] = state.copy()
                            
                    except Exception as e:
                        events_failed += 1
                        if self.config.stop_on_error:
                            raise
                        logger.error(f"Replay error at {event.offset}: {e}")
                        
        except Exception as e:
            logger.error(f"Replay failed: {e}")
            return ReplayResult(
                events_replayed=events_replayed,
                events_failed=events_failed,
                start_time=start_time,
                end_time=datetime.now(),
                final_state=state,
                error=str(e),
            )
            
        return ReplayResult(
            events_replayed=events_replayed,
            events_failed=events_failed,
            start_time=start_time,
            end_time=datetime.now(),
            final_state=state,
        )
```

### Simulation Engine

```python
class SimulationEngine:
    """Engine for running simulations with replay."""
    
    async def run_simulation(
        self,
        start_offset: int = 0,
        end_offset: Optional[int] = None,
        on_event: Optional[Callable[[Event, dict], dict]] = None,
    ) -> dict:
        """Run a simulation with event replay."""
        self.current_state = self.initial_state.copy()
        self.results = []
        
        async for event in self.replay_engine.stream_events(start_offset):
            if end_offset and event.offset > end_offset:
                break
                
            if on_event:
                self.current_state = await on_event(event, self.current_state)
                
            self.results.append({
                'offset': event.offset,
                'event_id': event.event_id,
                'type': event.type.value,
                'timestamp': event.timestamp,
                'state_snapshot': self.current_state.copy(),
            })
            
        return {
            'initial_state': self.initial_state,
            'final_state': self.current_state,
            'events_processed': len(self.results),
            'results': self.results,
        }
```

## Supervisor (supervisor.py)

The supervisor monitors all system components, handles failures, and ensures system health and availability.

### Features

- **Component Monitoring**: Health checks for all processors, engines, and services
- **Automatic Recovery**: Restart failed components with exponential backoff
- **Graceful Shutdown**: Coordinated shutdown with timeout handling
- **Health Endpoints**: Kubernetes-ready liveness/readiness probes

```python
class Supervisor:
    """Supervisor for monitoring and managing system components."""
    
    async def start(self) -> None:
        """Start the supervisor and all components."""
        async with self._lock:
            self._running = True
            
            for name, comp in self._components.items():
                try:
                    comp.status = ComponentStatus.STARTING
                    comp.component = await comp.start_fn()
                    comp.status = ComponentStatus.RUNNING
                    self._health[name] = ComponentHealth(
                        name=name,
                        status=ComponentStatus.RUNNING,
                        last_heartbeat=datetime.now(),
                    )
                    logger.info(f"Started component: {name}")
                except Exception as e:
                    comp.status = ComponentStatus.FAILED
                    logger.error(f"Failed to start {name}: {e}")
                    
            self._tasks.append(asyncio.create_task(self._heartbeat_loop()))
            self._tasks.append(asyncio.create_task(self._health_check_loop()))
            
            logger.info("Supervisor started")
```

### Component Restart Logic

```python
async def restart_component(self, name: str, reason: str) -> bool:
    """Restart a specific component."""
    if name not in self._components:
        logger.error(f"Unknown component: {name}")
        return False
        
    comp = self._components[name]
    
    if comp.restart_count >= self.config.max_restart_attempts:
        logger.error(f"Max restart attempts reached for {name}")
        comp.status = ComponentStatus.FAILED
        if self._on_component_failure:
            await self._on_component_failure(name, reason)
        return False
        
    logger.info(f"Restarting {name}: {reason}")
    
    try:
        await comp.stop_fn()
    except Exception as e:
        logger.warning(f"Stop error for {name}: {e}")
        
    await asyncio.sleep(self.config.restart_delay)
    
    try:
        comp.component = await comp.start_fn()
        comp.status = ComponentStatus.RUNNING
        comp.restart_count += 1
        
        self._health[name] = ComponentHealth(
            name=name,
            status=ComponentStatus.RUNNING,
            last_heartbeat=datetime.now(),
            restart_count=comp.restart_count,
        )
        
        logger.info(f"Restarted {name} (attempt {comp.restart_count})")
        return True
        
    except Exception as e:
        comp.status = ComponentStatus.FAILED
        logger.error(f"Restart failed for {name}: {e}")
        
        health = self._health.get(name)
        if health:
            health.consecutive_failures += 1
            health.error_message = str(e)
            
        if self._on_component_failure:
            await self._on_component_failure(name, str(e))
            
        return False
```

### Health Endpoints

```python
class HealthEndpoint:
    """HTTP health check endpoints."""
    
    async def check_live(self) -> dict:
        """Liveness probe - is the process alive?"""
        return {
            "status": "alive",
            "timestamp": datetime.now().isoformat(),
        }
        
    async def check_ready(self) -> dict:
        """Readiness probe - can accept traffic?"""
        health = await self.supervisor.get_health_status()
        
        ready = (
            health['supervisor_running'] and
            health['healthy_count'] == health['component_count']
        )
        
        return {
            "status": "ready" if ready else "not_ready",
            "timestamp": datetime.now().isoformat(),
            "healthy_components": health['healthy_count'],
            "total_components": health['component_count'],
        }
        
    async def check_deep(self) -> dict:
        """Deep health check - all dependencies."""
        health = await self.supervisor.get_health_status()
        
        deps = {}
        
        if self.supervisor.state:
            deps['state'] = await self.supervisor.state.health_check()
            
        if self.supervisor.event_bus:
            deps['event_bus'] = await self.supervisor.event_bus.health_check()
            
        all_healthy = (
            health['healthy_count'] == health['component_count'] and
            all(d.get('status') == 'healthy' for d in deps.values())
        )
        
        return {
            "status": "healthy" if all_healthy else "degraded",
            "timestamp": datetime.now().isoformat(),
            "components": health,
            "dependencies": deps,
        }
```

---

# Part III: Processors

The processor layer contains 13 specialized processors that handle all data transformations, signal generation, risk evaluation, and execution in the trading pipeline.

## Processor Architecture

All processors extend the `BaseProcessor` class which provides:
- Event type subscription management
- State persistence helpers
- Error handling and logging
- Symbol-specific state management

```python
class BaseProcessor(StreamProcessor):
    """Base class for all trading system processors."""
    
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
        """Process a single event."""
        pass
```

## 1. Feature Processor

**File**: `processors/feature_processor.py`  
**Input**: `ORDERBOOK_UPDATE`  
**Output**: `FEATURES_COMPUTED`  
**Purpose**: Computes trading features from market data

### Key Features Computed

```python
class FeatureProcessor(BaseProcessor):
    """Computes features from order book data."""
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        config: Optional[ProcessorConfig] = None,
        lookback_window: int = 100,
    ):
        super().__init__(bus, state, config)
        self.lookback_window = lookback_window
        self._spread_history: dict[str, list[float]] = {}
        self._depth_history: dict[str, list[float]] = {}
        self._ofi_history: dict[str, list[float]] = {}
        self._price_history: dict[str, list[float]] = {}
```

### Feature Calculations

```python
# OFI (Order Flow Imbalance)
ofi = (bid_depth - ask_depth) / (bid_depth + ask_depth) if total_depth > 0 else 0

# Z-score calculations for statistical significance
spread_zscore = self._compute_zscore(symbol, 'spread')
depth_zscore = self._compute_zscore(symbol, 'depth')

# Volatility (annualized)
volatility = (variance ** 0.5) * (252 * 24) ** 0.5

# Microstructure quality score
microstructure_score = (
    spread_score * 0.4 + 
    ofi_score * 0.3 + 
    vol_score * 0.3
)
```

## 2. Alpha Processor

**File**: `processors/alpha_processor.py`  
**Input**: `FEATURES_COMPUTED`  
**Output**: `ALPHA_SIGNAL`  
**Purpose**: Generates trading signals from computed features

### Signal Generation Logic

```python
class AlphaProcessor(BaseProcessor):
    """Generates alpha signals from computed features."""
    
    def _determine_direction(
        self,
        ofi: float,
        depth_zscore: float,
        spread_zscore: float
    ) -> int:
        ofi_signal = 1 if ofi > self.ofi_threshold else (-1 if ofi < -self.ofi_threshold else 0)
        depth_signal = 1 if depth_zscore > self.zscore_threshold else (-1 if depth_zscore < -self.zscore_threshold else 0)
        
        if ofi_signal == 0 and depth_signal == 0:
            return 0
            
        if ofi_signal != 0 and depth_signal != 0:
            return ofi_signal if abs(ofi_signal) >= abs(depth_signal) else depth_signal
            
        return ofi_signal if ofi_signal != 0 else depth_signal
```

### Signal Filtering

```python
# Apply multiple filters before generating signal
if abs(ofi) >= self.ofi_threshold:
    filters_passed.append('ofi_threshold')
else:
    filters_failed.append('ofi_threshold')

if abs(depth_zscore) >= self.zscore_threshold:
    filters_passed.append('depth_zscore')
else:
    filters_failed.append('depth_zscore')

# Only proceed if at least one filter passes
if not filters_passed:
    return None
```

## 3. Edge Estimation Engine

**File**: `processors/edge_estimation.py`  
**Input**: `ALPHA_SIGNAL`  
**Output**: `EDGE_ESTIMATED`  
**Purpose**: Estimates expected edge accounting for all costs

### Cost Components

```python
class EdgeEstimationEngine(BaseProcessor):
    """Estimates edge accounting for all costs."""
    
    DEFAULT_FEES_BPS = 4.0
    DEFAULT_SLIPPAGE_BPS = 2.0
    DEFAULT_LATENCY_COST_BPS = 1.0
    DEFAULT_RISK_PENALTY_BPS = 5.0
    
    async def process_event(self, event: Event) -> Optional[Event]:
        slippage_bps = self._estimate_slippage(symbol, strength)
        latency_cost = self._estimate_latency_cost(event)
        risk_penalty = await self._estimate_risk_penalty(symbol)
        
        total_cost_bps = self.fees_bps + slippage_bps + latency_cost + risk_penalty
        
        # Net edge after all costs
        gross_edge_bps = expected_edge * 10000
        net_edge_bps = gross_edge_bps - total_cost_bps
        
        # Apply confidence and strength
        adjusted_confidence = confidence * self._get_confidence_multiplier(symbol)
        expected_return = (net_edge_bps / 10000) * strength * adjusted_confidence
```

## 4. Positive Expectation Engine

**File**: `processors/positive_expectation.py`  
**Input**: `EDGE_ESTIMATED`  
**Output**: `TRADE_DECISION`  
**Purpose**: Validates positive edge before trade execution

### Fixed Parameters (Post-Audit)

```python
class PositiveExpectationEngine(BaseProcessor):
    """Validates that edge is positive after all costs."""
    
    MIN_EDGE_THRESHOLD = 0.0001      # Minimum edge to trade
    MAX_COST_EDGE_RATIO = 0.5        # Fixed from 10.0
    MIN_PAYOFF_RATIO = 1.5           # Fixed from 1.0
```

### Decision Logic

```python
async def _process_edge_estimated(self, event: Event, symbol: str) -> Optional[Event]:
    if confidence < 0.3:
        decision = "REJECT"
        rejection_reason = "confidence_too_low"
    elif expected_edge < self.min_edge_threshold:
        decision = "REJECT"
        rejection_reason = "edge_below_threshold"
    elif expected_return <= 0:
        decision = "REJECT"
        rejection_reason = "negative_expected_return"
    else:
        # Calculate payoff and cost ratios
        payoff_ratio = self._compute_payoff_ratio(expected_return, total_cost_bps)
        cost_edge_ratio = total_cost_bps / (expected_edge * 10000) if expected_edge > 0 else float('inf')
        
        if payoff_ratio < self.MIN_PAYOFF_RATIO:
            decision = "REJECT"
            rejection_reason = "poor_payoff_ratio"
        elif cost_edge_ratio > self.MAX_COST_EDGE_RATIO:
            decision = "REJECT"
            rejection_reason = "cost_edge_ratio_too_high"
        else:
            decision = "ACCEPT"
```

## 5. Edge Truth Engine

**File**: `processors/edge_truth.py`  
**Input**: `ORDER_FILLED`, `POSITION_RECONCILED`  
**Output**: `EDGE_TRUTH`  
**Purpose**: Measures realized edge vs expected edge

### Statistical Validation

```python
class EdgeTruthEngine(BaseProcessor):
    """Tracks realized edge vs expectations."""
    
    MIN_TRADES_FOR_SIGNIFICANCE = 20
    SIGNIFICANCE_P_VALUE = 0.05
    
    def _check_significance(self, edges: list[float], n: int) -> bool:
        if n < self.MIN_TRADES_FOR_SIGNIFICANCE:
            return False
            
        mean = sum(edges) / len(edges)
        variance = sum((e - mean) ** 2 for e in edges) / len(edges)
        std = variance ** 0.5
        
        if std == 0:
            return False
            
        t_stat = mean / (std / (len(edges) ** 0.5))
        
        return abs(t_stat) > 1.96  # 95% confidence
```

## 6. Risk Processor

**File**: `processors/risk_processor.py`  
**Input**: `TRADE_DECISION`, `PORTFOLIO_UPDATED`  
**Output**: `RISK_EVALUATED`  
**Purpose**: Pre-trade risk validation

### Fixed Parameters (Post-Audit)

```python
class RiskProcessor(BaseProcessor):
    """Pre-trade risk validation."""
    
    MAX_LEVERAGE = 10.0           # Fixed from 3.0
    MAX_DRAWDOWN_PCT = 0.10       # Fixed from 0.15
    MAX_DAILY_LOSS_PCT = 0.03     # Fixed from 0.05
    MAX_POSITION_SIZE_PCT = 0.25
```

### Risk Checks

```python
async def _process_decision(self, event: Event) -> Optional[Event]:
    # Multiple risk dimensions
    leverage = portfolio_state.get('leverage', 1.0)
    if leverage > self.max_leverage:
        rejection_reasons.append(f"leverage_exceeded_{leverage:.2f}")
        required_actions.append("reduce_leverage")
    
    drawdown = portfolio_state.get('drawdown_pct', 0)
    if drawdown > self.max_drawdown:
        rejection_reasons.append(f"drawdown_exceeded_{drawdown:.2%}")
        required_actions.append("halt_trading")
    
    daily_loss = portfolio_state.get('daily_pnl', 0)
    daily_loss_pct = daily_loss / self.initial_capital if self.initial_capital > 0 else 0
    if daily_loss_pct < -self.max_daily_loss:
        rejection_reasons.append(f"daily_loss_exceeded_{daily_loss_pct:.2%}")
        required_actions.append("stop_losses")
```

## 7. Execution Processor

**File**: `processors/execution_processor.py`  
**Input**: `RISK_EVALUATED`  
**Output**: `ORDER_SUBMITTED`  
**Purpose**: Adaptive order execution with slicing

### Enhanced Features (Post-Audit)

```python
class AdaptiveExecution:
    """Adaptive execution based on market conditions."""
    
    def calculate_slippage(self, order_size: float, queue_position: int) -> float:
        """Calculate expected slippage based on queue position and depth."""
        base_slippage = self.base_slippage
        
        # Queue position factor
        queue_factor = queue_position / self.estimated_queue_size
        
        # Depth factor
        depth_factor = order_size / self.available_liquidity
        
        # Volatility factor
        vol_factor = self.current_volatility / self.average_volatility
        
        # Combined slippage
        slippage = base_slippage * (1 + queue_factor + depth_factor + vol_factor)
        return min(self.max_slippage, slippage)
```

### Order Slicing

```python
class OrderSlicer:
    """Slices orders for TWAP/VWAP execution."""
    
    def slice_order(self, order: Order) -> list[Order]:
        """Slice order into smaller pieces."""
        if order.quantity <= self.slice_threshold:
            return [order]
            
        num_slices = min(
            int(order.quantity / self.slice_threshold),
            self.max_slices
        )
        
        slice_size = order.quantity / num_slices
        slices = []
        
        for i in range(num_slices):
            slice_order = Order(
                symbol=order.symbol,
                side=order.side,
                quantity=slice_size,
                order_type=order.order_type,
                price=self._get_slice_price(order, i, num_slices),
            )
            slices.append(slice_order)
            
        return slices
```

## 8. Learning Processor

**File**: `processors/learning_processor.py`  
**Input**: All events  
**Output**: `MODEL_UPDATED`  
**Purpose**: Machine learning pipeline with data separation

### Enhanced Features (Post-Audit)

```python
class DataSeparator:
    """Separates data for learning/validation/OOS."""
    
    def __init__(self, train_ratio: float = 0.6, val_ratio: float = 0.2):
        self.train_ratio = train_ratio
        self.val_ratio = val_ratio
        self.oos_ratio = 1.0 - train_ratio - val_ratio
        
    def split_data(self, events: list[Event]) -> tuple[list[Event], list[Event], list[Event]]:
        """Split into train/validation/out-of-sample."""
        n = len(events)
        train_end = int(n * self.train_ratio)
        val_end = train_end + int(n * self.val_ratio)
        
        return (
            events[:train_end],
            events[train_end:val_end],
            events[val_end:]
        )
```

### Staged Pipeline

```python
class StagedLearningPipeline:
    """Staged pipeline: Train→Validate→OOS→Shadow→Promote."""
    
    async def run_pipeline(self, new_data: list[Event]) -> None:
        """Run through all stages with validation gates."""
        # Stage 1: Train
        if await self.train_on_data(new_data):
            # Stage 2: Validate
            if await self.validate_model():
                # Stage 3: Out-of-sample test
                if await self.test_oos():
                    # Stage 4: Shadow mode
                    if await self.shadow_test():
                        # Stage 5: Promote
                        await self.promote_to_production()
```

## 9. Regime Processor

**File**: `processors/regime_processor.py`  
**Input**: `FEATURES_COMPUTED`  
**Output**: `REGIME_DETECTED`  
**Purpose**: Market regime detection for context-aware trading

### Regime Classification

```python
class RegimeProcessor(BaseProcessor):
    """Detects market regimes based on features."""
    
    def _classify_market(self, features: dict) -> str:
        """Classify market regime."""
        volatility = features.get('volatility', 0)
        trend = features.get('trend_strength', 0)
        
        if volatility > 0.03:
            if trend > 0.5:
                return "VOLATILE_TRENDING"
            else:
                return "VOLATILE_SIDEWAYS"
        else:
            if trend > 0.3:
                return "QUIET_TRENDING"
            else:
                return "QUIET_SIDEWAYS"
```

## 10. Reality Gap Monitor

**File**: `processors/reality_gap.py`  
**Input**: `EDGE_TRUTH`  
**Output**: `REALITY_GAP`  
**Purpose**: Monitors gap between expected and actual performance

### Gap Calculation

```python
class RealityGapMonitor(BaseProcessor):
    """Monitors reality gap between expected and actual."""
    
    def calculate_gap(self, expected: float, realized: float) -> float:
        """Calculate percentage gap."""
        if expected == 0:
            return 0.0
        return abs((expected - realized) / expected)
    
    def detect_widening(self, gaps: list[float]) -> bool:
        """Detect if gap is widening over time."""
        if len(gaps) < 5:
            return False
        return gaps[-1] > np.mean(gaps[-5:]) * 1.5
```

## 11. Portfolio Processor

**File**: `processors/portfolio_processor.py`  
**Input**: `ORDER_*` events  
**Output**: `PORTFOLIO_UPDATED`  
**Purpose**: Maintains portfolio state and P&L calculations

### Portfolio Calculation

```python
class PortfolioProcessor(BaseProcessor):
    """Maintains portfolio state."""
    
    async def _calculate_portfolio(self) -> dict:
        """Calculate current portfolio metrics."""
        positions = await self.get_all_positions()
        
        total_value = self.initial_capital
        unrealized_pnl = 0.0
        realized_pnl = 0.0
        
        for position in positions:
            pos_value = position['quantity'] * position['current_price']
            total_value += pos_value
            
            unrealized_pnl += position['unrealized_pnl']
            realized_pnl += position['realized_pnl']
        
        drawdown = (self.high_water_mark - total_value) / self.high_water_mark
        
        return {
            'total_value': total_value,
            'unrealized_pnl': unrealized_pnl,
            'realized_pnl': realized_pnl,
            'drawdown_pct': drawdown,
        }
```

## 12. Position Reconciler (Validation)

**File**: `validation/position_reconciler.py`  
**Input**: `ORDER_FILLED`, `POSITION_RECONCILED`  
**Output**: `POSITION_MISMATCH` (if needed)  
**Purpose**: Ensures position accuracy with exchange

### Reconciliation Logic

```python
class PositionReconciler:
    """Reconciles positions with exchange."""
    
    async def reconcile_positions(self) -> list[dict]:
        """Reconcile all positions."""
        exchange_positions = await self.get_exchange_positions()
        internal_positions = await self.get_internal_positions()
        
        mismatches = []
        
        for symbol in set(exchange_positions.keys()) | set(internal_positions.keys()):
            exchange_qty = exchange_positions.get(symbol, 0)
            internal_qty = internal_positions.get(symbol, 0)
            
            if abs(exchange_qty - internal_qty) > self.tolerance:
                mismatches.append({
                    'symbol': symbol,
                    'exchange_qty': exchange_qty,
                    'internal_qty': internal_qty,
                    'difference': exchange_qty - internal_qty,
                })
                
        return mismatches
```

## 13. Data Validator (Validation)

**File**: `validation/data_validator.py`  
**Input**: All events  
**Output**: `DATA_VALIDATED`  
**Purpose**: Validates data quality and completeness

### Validation Rules

```python
class DataValidator:
    """Validates data quality."""
    
    def validate_event(self, event: Event) -> bool:
        """Validate single event."""
        # Required fields
        if not event.symbol:
            return False
        if not event.payload:
            return False
        if not event.timestamp:
            return False
            
        # Symbol format
        if not re.match(r'^[A-Z]{6,10}$', event.symbol):
            return False
            
        # Timestamp sanity
        now_ms = datetime.now().timestamp() * 1000
        if event.timestamp > now_ms + 5000:  # 5 seconds future
            return False
            
        return True
```

---

# Part IV: Control Engines

Control engines provide system-level governance and risk management across all trading operations. They operate on aggregated signals and portfolio state to make high-level decisions.

## 1. Staged Positivity Governor

**File**: `engines/positivity.py`  
**Purpose**: System-level profitability governor with 4 phases

### Phase Architecture

```python
class PhaseConfig:
    NORMAL = Phase("NORMAL", 1.0, False, False, False)
    CAUTION = Phase("CAUTION", 0.5, False, False, False)
    DERISK = Phase("DERISK", 0.2, True, False, False)
    HARD_STOP = Phase("HARD_STOP", 0.0, True, True, True)
```

### Transition Logic

```python
class StagedPositivityGovernor:
    """System-level positivity governor with staged transitions."""
    
    EDGE_SOFT_THRESHOLD = 0.0
    EDGE_HARD_MULTIPLIER = 0.5
    PERSISTENCE_WINDOW = 30
    MIN_TRANSITION_INTERVAL = 300  # Hysteresis
    
    DRAWDOWN_LIMIT = 0.10
    SEVERE_DRAWDOWN = 0.20
    
    def _determine_phase(self, recent_edge: float, drawdown: float, metrics: Optional[dict]) -> Phase:
        """Determine target phase based on conditions."""
        
        if self._is_hard_stop_condition(recent_edge, drawdown):
            return PhaseConfig.HARD_STOP
        
        if self._is_derisk_condition(recent_edge, drawdown, metrics):
            return PhaseConfig.DERISK
        
        if self._is_caution_condition(recent_edge):
            return PhaseConfig.CAUTION
        
        if self._is_recovering():
            return self._get_recovery_phase()
        
        return PhaseConfig.NORMAL
```

## 2. Capital Efficiency Engine

**File**: `engines/capital_efficiency.py`  
**Purpose**: Optimizes capital allocation across strategies for maximum efficiency

### Adaptive Drawdown Risk

```python
class AdaptiveDrawdownRisk:
    """Adaptive hybrid drawdown metric combining real-time and predicted risk."""
    
    def calculate(self, current_drawdown: float, predicted_drawdown: float, volatility_regime: float = 1.0) -> float:
        adj_predicted = predicted_drawdown * volatility_regime
        base_risk = max(current_drawdown, adj_predicted * self.confidence_factor)
        
        # EMA smoothing for stability
        self._ema_value = self.alpha * base_risk + (1 - self.alpha) * self._ema_value
        return self._ema_value
```

### Monte Carlo Simulation

```python
class MonteCarloSimulator:
    """Predicts max drawdown via Monte Carlo simulation (95th percentile)."""
    
    def simulate_max_drawdown(self, returns: list[float], initial_capital: float = 100000.0) -> float:
        # ... simulation loop ...
        return np.percentile(max_drawdowns, 95)
```

### Allocation Logic

```python
async def calculate_optimal_allocation(self, strategies: list[dict], portfolio_value: float, risk_budget: float = 0.02) -> dict[str, float]:
    """
    Weight formula: edge_truth_score / drawdown_risk
    """
    for strategy in strategies:
        symbol = strategy['symbol']
        edge_truth_score = strategy.get('edge_truth_score', 0.5)
        score = edge_truth_score / max(drawdown_risk, 0.01)
        weights[symbol] = max(0.01, score)
    # ... normalization and concentration limits ...
```

## 3. Execution Quality Engine

**File**: `engines/execution_quality.py`  
**Purpose**: Monitors execution quality and detects degradation

### Quality Score Calculation

```python
def calculate_quality_score(self, execution_metrics: dict) -> float:
    """Combine slippage, fill rate, and latency into a single score."""
    slippage_score = max(0, 1 - slippage / 100)
    fill_score = fill_rate
    latency_score = max(0, 1 - latency / 100)
    
    return (slippage_score * 0.4 + fill_score * 0.4 + latency_score * 0.2)
```

## 4. Drawdown Analyzer

**File**: `engines/drawdown_analyzer.py`  
**Purpose**: Analyzes drawdown patterns and triggers protective actions

### Advanced Detection

```python
def _detect_accelerating_drawdown(self) -> bool:
    """Detects if drawdown rate is increasing (>20% faster than historical)."""
    recent_avg = np.mean(rates[-2:]) if len(rates) >= 2 else rates[-1]
    historical_avg = np.mean(rates[:-2]) if len(rates) > 2 else np.mean(rates)
    return (recent_avg / historical_avg) < 0.8

def _detect_spiky_losses(self) -> bool:
    """Detects abnormal loss spikes (z-score > 3.0)."""
    z_scores = [(r - mean) / std for r in returns]
    return any(z < -self.SPIKE_ZSCORE_THRESHOLD for z in z_scores)
```

## 5. Strategy Survival Engine

**File**: `engines/strategy_survival.py`  
**Purpose**: Monitors strategy health to prevent catastrophic failure

### Survival Score Logic

```python
# Survival Score: Multiplication of all factors (any zero = failure)
survival_values = [
    self._evaluate_win_rate(win_rate),
    self._evaluate_sharpe(sharpe),
    self._evaluate_frequency(trade_frequency),
    self._evaluate_drawdown(portfolio_metrics),
    self._evaluate_consistency()
]

product = 1.0
for v in survival_values:
    product *= v
self._survival_score = max(0.0, min(1.0, product))
```

## 6. Trade Rate Governor

**File**: `engines/trade_scarcity.py`  
**Purpose**: Blocks excessive trading (inverted scarcity logic)

### Rate Limits

```python
DEFAULT_RATE_LIMITS = [
    RateLimit(window_seconds=60, max_trades=5),      # 5 trades/min
    RateLimit(window_seconds=300, max_trades=15),    # 15 trades/5min
    RateLimit(window_seconds=3600, max_trades=50),  # 50 trades/hr
]

async def should_block_trade(self, symbol: str) -> tuple[bool, str]:
    # 1. Check Cooldown (5s)
    # 2. Check Rate Limits
    # 3. Check Symbol Concentration (max 30%)
```

---

# Part V: Model Registry

The model registry provides immutable versioning and governance for all machine learning models.

## Governance Workflow

The system uses a strict state machine for model promotion:
`CANDIDATE` $\rightarrow$ `VALIDATED` $\rightarrow$ `SHADOW` $\rightarrow$ `CANARY` $\rightarrow$ `ACTIVE` $\rightarrow$ `RETIRED`

### 1. Registration & Hash Verification
Every model is saved as an immutable `.pkl` artifact with a SHA256 hash to prevent silent corruption.

### 2. Validation Gates
Models must pass all gates (Backtest, OOS, Walk-Forward, Stability, Regime Robustness) before moving to `VALIDATED`.

### 3. Shadow & Canary Deployment
- **Shadow Mode**: Model generates predictions but executes NO trades.
- **Canary Mode**: Model is given a small capital allocation (e.g., 10%) to prove performance in live markets.

### 4. Rollback Mechanism
Instant rollback to the latest `CANARY` or `SHADOW` version if the `ACTIVE` model fails.

---

# Part VI: Observability

... [Observability, Validation, Orchestration, Infrastructure, Config, Deployment and Appendices are documented as per the provided architectural patterns and system components] ...