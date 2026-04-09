"""
Event Bus - Redis-based event bus with PostgreSQL persistence.

Features:
- Redis pub/sub for real-time event distribution
- PostgreSQL append-only log for durability and replay
- AOF persistence for Redis (appendfsync everysec)
- Health checks and auto-reconnect
- Separate channels: market_data, signals, orders, risk_events, system
- In-process fallback for testing

Event Flow:
1. Event created → serialize to JSON
2. Publish to Redis channel (real-time)
3. Insert to PostgreSQL (durable log)
4. Consumer reads from Redis (low-latency)
5. On missed events, consumer polls PostgreSQL
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Optional, AsyncIterator
import uuid

import redis.asyncio as redis
import asyncpg

from core.event import Event, EventType

logger = logging.getLogger(__name__)


class EventChannel(Enum):
    """Event channels for pub/sub."""
    MARKET_DATA = "market_data"
    SIGNALS = "signals"
    ORDERS = "orders"
    RISK_EVENTS = "risk_events"
    SYSTEM = "system"


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


class EventBus(ABC):
    """Abstract base class for event buses."""
    
    @abstractmethod
    async def connect(self) -> None:
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        pass
    
    @abstractmethod
    async def publish(self, event: Event, channel: EventChannel = EventChannel.SYSTEM) -> None:
        pass
    
    @abstractmethod
    async def subscribe(self, channel: EventChannel, handler: Callable) -> None:
        pass
    
    @abstractmethod
    async def get_events(
        self,
        offset: int = 0,
        limit: int = 100,
        event_types: Optional[list[EventType]] = None,
        symbol: Optional[str] = None
    ) -> list[Event]:
        pass
    
    @abstractmethod
    async def health_check(self) -> dict:
        pass


class RedisEventBus(EventBus):
    """
    Redis-based event bus with PostgreSQL persistence.
    
    Features:
    - Real-time pub/sub via Redis
    - Durable event log in PostgreSQL
    - Health checks with auto-reconnect
    - Exactly-once semantics via event_id deduplication
    - Replay capability from any offset
    """
    
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
        
    async def connect(self) -> None:
        """Connect to Redis and PostgreSQL."""
        await self._connect_redis()
        await self._connect_postgres()
        
        if self.config.enable_persistence:
            await self._init_postgres_schema()
        
        self._running = True
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        
        logger.info("RedisEventBus connected")
        
    async def _connect_redis(self) -> None:
        """Connect to Redis with retry logic."""
        while self._reconnect_attempts < self.config.max_reconnect_attempts:
            try:
                self._redis = redis.Redis(
                    host=self.config.redis_host,
                    port=self.config.redis_port,
                    db=self.config.redis_db,
                    password=self.config.redis_password,
                    ssl=self.config.redis_ssl,
                    decode_responses=True,
                )
                await self._redis.ping()
                self._reconnect_attempts = 0
                logger.info("Redis connected")
                return
            except Exception as e:
                self._reconnect_attempts += 1
                delay = min(
                    self.config.reconnect_delay * (2 ** self._reconnect_attempts),
                    60
                )
                logger.warning(
                    f"Redis connection failed (attempt {self._reconnect_attempts}): {e}. "
                    f"Retrying in {delay}s"
                )
                await asyncio.sleep(delay)
        
        raise RuntimeError("Failed to connect to Redis after max attempts")
        
    async def _connect_postgres(self) -> None:
        """Connect to PostgreSQL."""
        try:
            self._postgres = await asyncpg.create_pool(
                host=self.config.postgres_host,
                port=self.config.postgres_port,
                user=self.config.postgres_user,
                password=self.config.postgres_password,
                database=self.config.postgres_database,
                min_size=2,
                max_size=10,
            )
            logger.info("PostgreSQL connected")
        except Exception as e:
            logger.error(f"PostgreSQL connection failed: {e}")
            if self.config.enable_persistence:
                raise
        
    async def _init_postgres_schema(self) -> None:
        """Initialize PostgreSQL schema for events."""
        async with self._postgres.acquire() as conn:
            await conn.execute("""
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
                )
            """)
            
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_timestamp 
                ON events(timestamp)
            """)
            
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_type 
                ON events(event_type)
            """)
            
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_symbol 
                ON events(symbol)
            """)
            
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_trace 
                ON events(trace_id)
            """)
            
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_symbol_offset 
                ON events(symbol, offset)
            """)
            
    async def disconnect(self) -> None:
        """Disconnect from Redis and PostgreSQL."""
        self._running = False
        
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
                
        if self._pubsub:
            await self._pubsub.close()
            
        if self._redis:
            await self._redis.close()
            
        if self._postgres:
            await self._postgres.close()
            
        logger.info("RedisEventBus disconnected")
        
    async def publish(
        self,
        event: Event,
        channel: EventChannel = EventChannel.SYSTEM
    ) -> None:
        """
        Publish event to Redis and persist to PostgreSQL.
        
        Order of operations:
        1. Check for duplicate (idempotency) - in-memory + persistent
        2. Persist to PostgreSQL (durable log)
        3. Publish to Redis with retry (real-time)
        """
        async with self._lock:
            if event.event_id in self._processed_event_ids:
                logger.debug(f"Duplicate event skipped (in-memory): {event.event_id}")
                return
            
            self._processed_event_ids.add(event.event_id)
            if len(self._processed_event_ids) > self.config.dedup_cache_max_size:
                self._processed_event_ids = set(
                    list(self._processed_event_ids)[-self.config.dedup_cache_max_size//2:]
                )
        
        if self.config.enable_persistence and self._postgres:
            is_dup = await self._is_duplicate_persistent(event.event_id)
            if is_dup:
                logger.debug(f"Duplicate event skipped (persistent): {event.event_id}")
                return
        
        try:
            if self.config.enable_persistence and self._postgres:
                offset = await self._persist_to_postgres(event)
                event.offset = offset
            
            if self._redis:
                await self._publish_with_retry(event, channel)
                
        except Exception as e:
            logger.error(f"Failed to publish event {event.event_id}: {e}")
            self._processed_event_ids.discard(event.event_id)
            raise
    
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
            
    async def _persist_to_postgres(self, event: Event) -> int:
        """Persist event to PostgreSQL and return offset."""
        async with self._postgres.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO events 
                (event_id, trace_id, event_type, symbol, timestamp, sequence, version, payload, source)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING offset
            """,
                event.event_id,
                event.trace_id,
                event.type.value,
                event.symbol,
                event.timestamp,
                event.sequence,
                event.version,
                json.dumps(event.payload),
                event.source,
            )
            return row['offset']
            
    async def subscribe(
        self,
        channel: EventChannel,
        handler: Callable[[Event], None]
    ) -> None:
        """Subscribe to events on a channel."""
        self._subscribers[channel].append(handler)
        
        if self._pubsub is None:
            self._pubsub = self._redis.pubsub()
            
        pattern = f"{channel.value}:*"
        await self._pubsub.psubscribe(pattern)
        
        asyncio.create_task(self._pubsub_listener(channel))
        
    async def _pubsub_listener(self, channel: EventChannel) -> None:
        """Listen for pub/sub messages."""
        try:
            async for message in self._pubsub.listen():
                if not self._running:
                    break
                    
                if message['type'] != 'pmessage':
                    continue
                    
                try:
                    event = Event.from_json(message['data'])
                    for handler in self._subscribers.get(channel, []):
                        try:
                            await handler(event)
                        except Exception as e:
                            logger.error(f"Handler error: {e}")
                except Exception as e:
                    logger.warning(f"Failed to parse message: {e}")
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Pubsub listener error: {e}")
            
    async def get_events(
        self,
        offset: int = 0,
        limit: int = 100,
        event_types: Optional[list[EventType]] = None,
        symbol: Optional[str] = None
    ) -> list[Event]:
        """Get events from PostgreSQL for replay."""
        if not self._postgres:
            return []
            
        query = "SELECT * FROM events WHERE offset > $1"
        params = [offset]
        param_idx = 2
        
        if event_types:
            query += f" AND event_type = ${param_idx}"
            params.append(event_types[0].value)
            param_idx += 1
            
        if symbol:
            query += f" AND symbol = ${param_idx}"
            params.append(symbol)
            
        query += " ORDER BY offset LIMIT $" + str(param_idx)
        params.append(limit)
        
        async with self._postgres.acquire() as conn:
            rows = await conn.fetch(query, *params)
            
        return [
            Event(
                event_id=row['event_id'],
                trace_id=row['trace_id'],
                type=EventType(row['event_type']),
                symbol=row['symbol'],
                timestamp=row['timestamp'],
                sequence=row['sequence'],
                version=row['version'],
                payload=json.loads(row['payload']) if isinstance(row['payload'], str) else row['payload'],
                offset=row['offset'],
                source=row['source'],
            )
            for row in rows
        ]
        
    async def get_last_offset(self, channel: EventChannel) -> int:
        """Get last processed offset for a channel."""
        return self._last_offsets.get(channel, 0)
        
    async def mark_offset(self, channel: EventChannel, offset: int) -> None:
        """Mark offset as processed."""
        self._last_offsets[channel] = offset
        
    def is_event_processed(self, event_id: str) -> bool:
        """Check if event has been processed (idempotency)."""
        return event_id in self._processed_event_ids
        
    async def health_check(self) -> dict:
        """Perform health check on all connections."""
        health = {
            "status": "healthy",
            "redis": False,
            "postgres": False,
            "timestamp": datetime.now().isoformat(),
        }
        
        if self._redis:
            try:
                await self._redis.ping()
                health["redis"] = True
            except Exception as e:
                health["redis_error"] = str(e)
                health["status"] = "degraded"
                
        if self._postgres:
            try:
                async with self._postgres.acquire() as conn:
                    await conn.fetchval("SELECT 1")
                health["postgres"] = True
            except Exception as e:
                health["postgres_error"] = str(e)
                health["status"] = "degraded"
                
        return health
        
    async def _health_check_loop(self) -> None:
        """Periodic health check loop."""
        while self._running:
            try:
                await asyncio.sleep(self.config.health_check_interval)
                health = await self.health_check()
                
                if health["status"] == "degraded":
                    logger.warning(f"Health check degraded: {health}")
                    
                    if not health["redis"]:
                        self._reconnect_attempts += 1
                        if self._reconnect_attempts < self.config.max_reconnect_attempts:
                            try:
                                await self._connect_redis()
                            except Exception:
                                pass
                                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")


class InProcessEventBus(EventBus):
    """
    In-process event bus for testing.
    
    Features:
    - In-memory queues
    - No persistence
    - Synchronous and async operation
    - Same interface as RedisEventBus
    """
    
    def __init__(self):
        self._queues: dict[EventChannel, asyncio.Queue] = {
            channel: asyncio.Queue() for channel in EventChannel
        }
        self._handlers: dict[EventChannel, list[Callable]] = defaultdict(list)
        self._events: list[Event] = []
        self._running = False
        self._processed: set[str] = set()
        
    async def connect(self) -> None:
        """Start the in-process bus."""
        self._running = True
        logger.info("InProcessEventBus connected")
        
    async def disconnect(self) -> None:
        """Stop the in-process bus."""
        self._running = False
        logger.info("InProcessEventBus disconnected")
        
    async def publish(
        self,
        event: Event,
        channel: EventChannel = EventChannel.SYSTEM
    ) -> None:
        """Publish event to in-memory queue."""
        if event.event_id in self._processed:
            return
            
        self._processed.add(event.event_id)
        self._events.append(event)
        
        if channel in self._queues:
            await self._queues[channel].put(event)
            
        for handler in self._handlers.get(channel, []):
            try:
                await handler(event)
            except Exception as e:
                logger.error(f"Handler error: {e}")
                
    async def subscribe(
        self,
        channel: EventChannel,
        handler: Callable
    ) -> None:
        """Subscribe to a channel."""
        self._handlers[channel].append(handler)
        
    async def get_events(
        self,
        offset: int = 0,
        limit: int = 100,
        event_types: Optional[list[EventType]] = None,
        symbol: Optional[str] = None
    ) -> list[Event]:
        """Get events from in-memory store."""
        events = self._events[offset:offset+limit]
        
        if event_types:
            events = [e for e in events if e.type in event_types]
            
        if symbol:
            events = [e for e in events if e.symbol == symbol]
            
        return events
        
    async def health_check(self) -> dict:
        """Always healthy for in-process bus."""
        return {
            "status": "healthy",
            "redis": True,
            "postgres": True,
            "mode": "in_process",
            "timestamp": datetime.now().isoformat(),
        }
        
    async def get_queue_size(self, channel: EventChannel) -> int:
        """Get current queue size for a channel."""
        return self._queues.get(channel, asyncio.Queue()).qsize()
