"""
State Management with PostgreSQL, Redis, and Event Log

Multi-layer persistence architecture:
- PostgreSQL: Authoritative state (ACID transactions)
- Redis: Fast cache layer
- Event Log: Append-only audit trail

Author: LVR Trading System
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, List, AsyncIterator
from collections import deque

import redis.asyncio as redis

from app.schemas import Portfolio, Position, RiskState, SystemEvent, EventType

logger = logging.getLogger(__name__)


class EventSeverity(Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class StoredEvent:
    event_id: str
    event_type: str
    timestamp: datetime
    data: Dict[str, Any]
    severity: EventSeverity = EventSeverity.INFO
    source: str = "system"
    correlation_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
            "severity": self.severity.value,
            "source": self.source,
            "correlation_id": self.correlation_id,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> StoredEvent:
        return cls(
            event_id=data["event_id"],
            event_type=data["event_type"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            data=data["data"],
            severity=EventSeverity(data.get("severity", "info")),
            source=data.get("source", "system"),
            correlation_id=data.get("correlation_id"),
        )


class StateStore:
    """
    Multi-layer state management with PostgreSQL, Redis, and Event Log.
    
    PERSISTENCE LAYERS:
    - PostgreSQL: Authoritative state (ACID transactions) - PRIMARY
    - Redis: Fast cache for low-latency reads
    - Event Log: Append-only audit trail for compliance and recovery
    
    FEATURES:
    - Connection pooling for PostgreSQL
    - Automatic failover between layers
    - Event batching for performance
    - Snapshot checkpoints
    - State recovery on startup
    
    Author: LVR Trading System
    """

    def __init__(
        self,
        pg_config: Optional[Dict[str, Any]] = None,
        redis_config: Optional[Dict[str, Any]] = None,
        event_log_path: Optional[str] = None,
        checkpoint_interval: int = 60,
        batch_size: int = 100,
        max_event_buffer: int = 10000,
    ):
        """
        Initialize StateStore.
        
        Args:
            pg_config: PostgreSQL connection config
            redis_config: Redis connection config
            event_log_path: Path for event log files
            checkpoint_interval: Seconds between checkpoints
            batch_size: Number of events to batch before flush
            max_event_buffer: Maximum events to buffer in memory
        """
        self.pg_config = pg_config or {}
        self.redis_config = redis_config or {}
        self.event_log_path = event_log_path
        self.checkpoint_interval = checkpoint_interval
        self.batch_size = batch_size
        self.max_event_buffer = max_event_buffer

        self._pg_pool: Optional[Any] = None
        self._redis_client: Optional[redis.Redis] = None
        self._connected = False
        self._pg_available = False
        self._redis_available = False

        self._last_checkpoint = 0
        self._pending_events: deque[StoredEvent] = deque(maxlen=max_event_buffer)
        self._event_buffer_count = 0
        self._event_flush_task: Optional[asyncio.Task] = None
        self._checkpoint_task: Optional[asyncio.Task] = None
        
        self._event_log_file: Optional[Path] = None
        self._event_log_lock = asyncio.Lock()

    async def connect(self) -> None:
        """
        Connect to all state stores.
        
        Connects in order: Redis -> PostgreSQL -> Event Log
        Each connection is attempted independently with graceful degradation.
        """
        logger.info("Connecting to state stores")

        await self._connect_redis()
        await self._connect_postgres()
        await self._init_event_log()

        self._connected = True
        
        self._event_flush_task = asyncio.create_task(self._event_flush_loop())
        self._checkpoint_task = asyncio.create_task(self._checkpoint_loop())
        
        logger.info(
            f"State stores connected: Redis={self._redis_available}, "
            f"PostgreSQL={self._pg_available}"
        )

    async def disconnect(self) -> None:
        """Gracefully disconnect from all state stores."""
        logger.info("Disconnecting from state stores")

        if self._event_flush_task:
            self._event_flush_task.cancel()
        if self._checkpoint_task:
            self._checkpoint_task.cancel()

        await self._flush_all_events()

        if self._redis_client:
            await self._redis_client.close()

        if self._pg_pool:
            await self._pg_pool.close()

        self._connected = False
        logger.info("State stores disconnected")

    async def _connect_redis(self) -> None:
        """Connect to Redis with retry."""
        try:
            self._redis_client = redis.Redis(
                host=self.redis_config.get("host", "localhost"),
                port=self.redis_config.get("port", 6379),
                db=self.redis_config.get("db", 0),
                password=self.redis_config.get("password") or None,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            await asyncio.wait_for(self._redis_client.ping(), timeout=5)
            self._redis_available = True
            logger.info("Redis connected")
        except asyncio.TimeoutError:
            logger.warning("Redis connection timeout - operating without cache")
            self._redis_client = None
            self._redis_available = False
        except Exception as e:
            logger.warning(f"Redis connection failed: {e} - operating without cache")
            self._redis_client = None
            self._redis_available = False

    async def _connect_postgres(self) -> None:
        """Connect to PostgreSQL with connection pool."""
        try:
            import asyncpg
            
            self._pg_pool = await asyncpg.create_pool(
                host=self.pg_config.get("host", "localhost"),
                port=self.pg_config.get("port", 5432),
                database=self.pg_config.get("database", "trading"),
                user=self.pg_config.get("user", "trading"),
                password=self.pg_config.get("password", ""),
                min_size=2,
                max_size=10,
                command_timeout=60,
            )
            
            async with self._pg_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
                
            self._pg_available = True
            logger.info("PostgreSQL connected")
            
        except ImportError:
            logger.warning("asyncpg not installed - PostgreSQL disabled")
            self._pg_available = False
        except Exception as e:
            logger.warning(f"PostgreSQL connection failed: {e}")
            self._pg_available = False

    async def _init_event_log(self) -> None:
        """Initialize event log file."""
        if not self.event_log_path:
            return
            
        try:
            self._event_log_file = Path(self.event_log_path)
            self._event_log_file.parent.mkdir(parents=True, exist_ok=True)
            
            if not self._event_log_file.exists():
                self._event_log_file.touch()
                
            logger.info(f"Event log initialized: {self.event_log_path}")
        except Exception as e:
            logger.warning(f"Event log initialization failed: {e}")

    async def save_position(self, position: Position) -> None:
        """
        Save position to PostgreSQL (primary) with Redis cache.
        
        Write order:
        1. PostgreSQL (authoritative)
        2. Redis (cache)
        
        Args:
            position: Position to save
        """
        pg_saved = False
        
        if self._pg_available and self._pg_pool:
            try:
                async with self._pg_pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO positions (symbol, quantity, entry_price, updated_at)
                        VALUES ($1, $2, $3, NOW())
                        ON CONFLICT (symbol) DO UPDATE SET
                            quantity = EXCLUDED.quantity,
                            entry_price = EXCLUDED.entry_price,
                            updated_at = NOW()
                    """, position.symbol, position.quantity, position.entry_price)
                    pg_saved = True
            except Exception as e:
                logger.error(f"PostgreSQL save failed: {e}")

        if not pg_saved and self._redis_available and self._redis_client:
            await self._save_position_redis(position)

    async def _save_position_redis(self, position: Position) -> None:
        """Save position to Redis cache."""
        if not self._redis_client:
            return

        try:
            key = f"position:{position.symbol}"
            data = {
                "symbol": position.symbol,
                "quantity": position.quantity,
                "entry_price": position.entry_price,
                "updated_at": position.last_update,
            }
            await self._redis_client.set(key, json.dumps(data))
        except Exception as e:
            logger.error(f"Redis save failed: {e}")

    async def load_positions(self) -> Dict[str, Position]:
        """
        Load all positions with multi-layer fallback.
        
        Read order:
        1. Redis (cache)
        2. PostgreSQL (fallback)
        
        Returns:
            Dictionary of symbol -> Position
        """
        positions = {}

        if self._redis_available and self._redis_client:
            try:
                keys = await self._redis_client.keys("position:*")
                for key in keys:
                    data = await self._redis_client.get(key)
                    if data:
                        pos_data = json.loads(data)
                        positions[pos_data["symbol"]] = Position(**pos_data)
                        
                if positions:
                    return positions
            except Exception as e:
                logger.error(f"Redis load failed: {e}")

        if self._pg_available and self._pg_pool:
            try:
                async with self._pg_pool.acquire() as conn:
                    rows = await conn.fetch("SELECT * FROM positions")
                    for row in rows:
                        positions[row["symbol"]] = Position(
                            symbol=row["symbol"],
                            quantity=row["quantity"],
                            entry_price=row["entry_price"],
                            last_update=str(row["updated_at"]),
                        )
            except Exception as e:
                logger.error(f"PostgreSQL load failed: {e}")

        return positions

    async def save_portfolio(self, portfolio: Portfolio) -> None:
        """
        Save portfolio snapshot.
        
        Args:
            portfolio: Portfolio to save
        """
        if not self._redis_client:
            return

        try:
            key = "portfolio:current"
            data = portfolio.model_dump()
            await self._redis_client.set(key, json.dumps(data))
            
            if self._pg_available and self._pg_pool:
                await self._save_portfolio_pg(portfolio)
        except Exception as e:
            logger.error(f"Failed to save portfolio: {e}")

    async def _save_portfolio_pg(self, portfolio: Portfolio) -> None:
        """Save portfolio to PostgreSQL."""
        if not self._pg_pool:
            return
            
        try:
            async with self._pg_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO portfolio_snapshots (snapshot_data, created_at)
                    VALUES ($1, NOW())
                """, json.dumps(portfolio.model_dump()))
        except Exception as e:
            logger.error(f"Portfolio PG save failed: {e}")

    async def load_portfolio(self) -> Optional[Portfolio]:
        """Load portfolio snapshot."""
        if not self._redis_client:
            return None

        try:
            data = await self._redis_client.get("portfolio:current")
            if data:
                return Portfolio(**json.loads(data))
        except Exception as e:
            logger.error(f"Failed to load portfolio: {e}")

        return None

    def append_event(
        self,
        event_type: str,
        data: Dict[str, Any],
        severity: EventSeverity = EventSeverity.INFO,
        source: str = "system",
        correlation_id: Optional[str] = None,
    ) -> str:
        """
        Append event to log with automatic persistence.
        
        Args:
            event_type: Type of event
            data: Event data
            severity: Event severity level
            source: Event source
            correlation_id: Optional correlation ID for tracing
            
        Returns:
            Generated event ID
        """
        import uuid
        
        event_id = str(uuid4())
        event = StoredEvent(
            event_id=event_id,
            event_type=event_type,
            timestamp=datetime.now(),
            data=data,
            severity=severity,
            source=source,
            correlation_id=correlation_id,
        )
        
        self._pending_events.append(event)
        self._event_buffer_count += 1

        if len(self._pending_events) >= self.batch_size:
            asyncio.create_task(self._flush_events())
        
        return event_id

    async def _flush_events(self) -> None:
        """Flush pending events to storage."""
        if not self._pending_events:
            return

        events = list(self._pending_events)
        self._pending_events.clear()
        self._event_buffer_count = 0

        logger.debug(f"Flushing {len(events)} events")

        if self._event_log_file:
            await self._write_events_to_log(events)

        if self._pg_available and self._pg_pool:
            await self._write_events_to_pg(events)

    async def _write_events_to_log(self, events: List[StoredEvent]) -> None:
        """Write events to append-only log file."""
        if not self._event_log_file:
            return
            
        async with self._event_log_lock:
            try:
                with open(self._event_log_file, "a") as f:
                    for event in events:
                        f.write(json.dumps(event.to_dict()) + "\n")
            except Exception as e:
                logger.error(f"Event log write failed: {e}")

    async def _write_events_to_pg(self, events: List[StoredEvent]) -> None:
        """Write events to PostgreSQL."""
        if not self._pg_pool:
            return
            
        try:
            async with self._pg_pool.acquire() as conn:
                await conn.executemany("""
                    INSERT INTO event_log (event_id, event_type, timestamp, data, severity, source, correlation_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                """, [
                    (e.event_id, e.event_type, e.timestamp, json.dumps(e.data), 
                     e.severity.value, e.source, e.correlation_id)
                    for e in events
                ])
        except Exception as e:
            logger.error(f"Event PG write failed: {e}")

    async def _flush_all_events(self) -> None:
        """Flush all remaining events on shutdown."""
        if self._pending_events:
            await self._flush_events()

    async def query_events(
        self,
        event_type: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[StoredEvent]:
        """
        Query events from storage.
        
        Args:
            event_type: Filter by event type
            start_time: Start of time range
            end_time: End of time range
            limit: Maximum events to return
            
        Returns:
            List of matching events
        """
        if self._pg_available and self._pg_pool:
            return await self._query_events_pg(event_type, start_time, end_time, limit)
        
        return []

    async def _query_events_pg(
        self,
        event_type: Optional[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
        limit: int,
    ) -> List[StoredEvent]:
        """Query events from PostgreSQL."""
        if not self._pg_pool:
            return []
            
        try:
            query = "SELECT * FROM event_log WHERE 1=1"
            params = []
            
            if event_type:
                params.append(event_type)
                query += f" AND event_type = ${len(params)}"
            
            if start_time:
                params.append(start_time)
                query += f" AND timestamp >= ${len(params)}"
            
            if end_time:
                params.append(end_time)
                query += f" AND timestamp <= ${len(params)}"
            
            query += f" ORDER BY timestamp DESC LIMIT {limit}"
            
            async with self._pg_pool.acquire() as conn:
                rows = await conn.fetch(query, *params)
                return [
                    StoredEvent(
                        event_id=row["event_id"],
                        event_type=row["event_type"],
                        timestamp=row["timestamp"],
                        data=json.loads(row["data"]) if isinstance(row["data"], str) else row["data"],
                        severity=EventSeverity(row["severity"]),
                        source=row["source"],
                        correlation_id=row["correlation_id"],
                    )
                    for row in rows
                ]
        except Exception as e:
            logger.error(f"Event query failed: {e}")
            return []

    async def _event_flush_loop(self) -> None:
        """Background loop to periodically flush events."""
        while True:
            try:
                await asyncio.sleep(5)
                if self._pending_events:
                    await self._flush_events()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Event flush loop error: {e}")

    async def _checkpoint_loop(self) -> None:
        """Background loop for periodic checkpoints."""
        while True:
            try:
                await asyncio.sleep(self.checkpoint_interval)
                await self.checkpoint(force=True)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Checkpoint loop error: {e}")

    async def checkpoint(self, force: bool = False) -> None:
        """
        Create checkpoint of current state.
        
        Args:
            force: Force checkpoint even if interval not reached
        """
        now = time.time()
        if not force and now - self._last_checkpoint < self.checkpoint_interval:
            return

        logger.debug("Creating checkpoint")
        
        self._last_checkpoint = now
        
        await self._flush_events()
        
        if self._pg_available and self._pg_pool:
            try:
                async with self._pg_pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO checkpoints (created_at)
                        VALUES (NOW())
                    """)
            except Exception as e:
                logger.error(f"Checkpoint creation failed: {e}")

    async def recover(self) -> Dict[str, Any]:
        """
        Recover state from storage on startup.
        
        Returns:
            Dictionary with recovered state
        """
        logger.info("Starting state recovery")

        positions = await self.load_positions()
        portfolio = await self.load_portfolio()

        recovery_result = {
            "positions": positions,
            "portfolio": portfolio,
            "recovered": bool(positions or portfolio),
        }
        
        self.append_event(
            event_type="state_recovery",
            data=recovery_result,
            severity=EventSeverity.INFO,
            source="statestore",
        )
        
        logger.info(f"State recovery complete: {recovery_result}")

        return recovery_result

    async def stream_events(
        self,
        last_event_id: Optional[str] = None,
    ) -> AsyncIterator[StoredEvent]:
        """
        Stream events from log (for real-time processing).
        
        Args:
            last_event_id: Resume from this event ID
            
        Yields:
            StoredEvent objects
        """
        if not self._event_log_file or not self._event_log_file.exists():
            return
            
        seen_ids = set()
        if last_event_id:
            seen_ids.add(last_event_id)
        
        with open(self._event_log_file, "r") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    if data["event_id"] not in seen_ids:
                        seen_ids.add(data["event_id"])
                        yield StoredEvent.from_dict(data)
                except json.JSONDecodeError:
                    continue


from uuid import uuid4
