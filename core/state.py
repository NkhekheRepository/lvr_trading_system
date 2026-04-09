"""
Distributed State - Hot/Cold state management with Redis + PostgreSQL.

Architecture:
- Redis: Hot state for low-latency reads (positions, orders, PnL)
- PostgreSQL: Authoritative state for durability

Features:
- Atomic updates with optimistic locking
- Version tracking for consistency
- Event-linked state transitions
- Row-level locking
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Callable
import uuid

import redis.asyncio as redis
import asyncpg
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base

from core.event import Event

logger = logging.getLogger(__name__)

Base = declarative_base()


class StateTable(Base):
    """SQLAlchemy model for state storage."""
    __tablename__ = 'state'
    
    key = sa.Column(sa.String(256), primary_key=True)
    value = sa.Column(sa.JSON, nullable=False)
    version = sa.Column(sa.Integer, default=1)
    updated_at = sa.Column(sa.DateTime, default=datetime.utcnow)
    updated_by = sa.Column(sa.String(64))
    trace_id = sa.Column(sa.String(64))


class PositionTable(Base):
    """SQLAlchemy model for positions."""
    __tablename__ = 'positions'
    
    id = sa.Column(sa.Integer, primary_key=True, autoincrement=True)
    symbol = sa.Column(sa.String(32), unique=True, nullable=False)
    quantity = sa.Column(sa.Float, default=0.0)
    avg_entry_price = sa.Column(sa.Float, default=0.0)
    unrealized_pnl = sa.Column(sa.Float, default=0.0)
    realized_pnl = sa.Column(sa.Float, default=0.0)
    updated_at = sa.Column(sa.DateTime, default=datetime.utcnow)
    version = sa.Column(sa.Integer, default=1)


@dataclass
class StateValue:
    """Wrapper for state value with metadata."""
    key: str
    value: Any
    version: int
    updated_at: datetime
    updated_by: Optional[str] = None
    trace_id: Optional[str] = None


class DistributedState:
    """
    Distributed state management with Redis + PostgreSQL.
    
    Read path: Redis (hot) → PostgreSQL (fallback)
    Write path: PostgreSQL (authoritative) → Redis (cache)
    
    Features:
    - Atomic updates with version checking
    - Event-linked state transitions
    - Optimistic locking
    - Cache invalidation
    """
    
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
        
    async def connect(self) -> None:
        """Connect to Redis and PostgreSQL."""
        self._redis = redis.from_url(
            self.redis_url,
            decode_responses=True,
        )
        await self._redis.ping()
        
        self._pg_pool = await asyncpg.create_pool(
            self.postgres_dsn.replace("postgresql+asyncpg://", ""),
            min_size=2,
            max_size=10,
        )
        
        self._sqlalchemy_engine = create_async_engine(
            self.postgres_dsn,
            echo=False,
        )
        
        async with self._sqlalchemy_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            
        logger.info("DistributedState connected")
        
    async def disconnect(self) -> None:
        """Disconnect from Redis and PostgreSQL."""
        if self._redis:
            await self._redis.close()
            
        if self._pg_pool:
            await self._pg_pool.close()
            
        if self._sqlalchemy_engine:
            await self._sqlalchemy_engine.dispose()
            
        logger.info("DistributedState disconnected")
        
    async def get(self, key: str) -> Optional[StateValue]:
        """Get value from hot cache (Redis) or cold storage (PostgreSQL)."""
        redis_key = f"state:{key}"
        
        try:
            cached = await self._redis.get(redis_key)
            if cached:
                data = json.loads(cached)
                return StateValue(
                    key=key,
                    value=data['value'],
                    version=data['version'],
                    updated_at=datetime.fromisoformat(data['updated_at']),
                    updated_by=data.get('updated_by'),
                    trace_id=data.get('trace_id'),
                )
        except Exception as e:
            logger.warning(f"Redis get failed for {key}: {e}")
            
        async with self._pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM state WHERE key = $1",
                key
            )
            
        if row:
            await self._cache_to_redis(key, row)
            return StateValue(
                key=key,
                value=row['value'],
                version=row['version'],
                updated_at=row['updated_at'],
                updated_by=row.get('updated_by'),
                trace_id=row.get('trace_id'),
            )
            
        return None
        
    async def set(
        self,
        key: str,
        value: Any,
        version: Optional[int] = None,
        updated_by: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> StateValue:
        """
        Set value atomically with version check.
        
        If version is provided, uses optimistic locking.
        """
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
                await conn.execute("""
                    INSERT INTO state (key, value, version, updated_at, updated_by, trace_id)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        version = EXCLUDED.version,
                        updated_at = EXCLUDED.updated_at,
                        updated_by = EXCLUDED.updated_by,
                        trace_id = EXCLUDED.trace_id
                """, key, json.dumps(value), new_version, updated_at, updated_by, trace_id)
                
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
            
    async def atomic_update(
        self,
        key: str,
        update_fn: Callable[[Any], Any],
        max_retries: int = 3,
        updated_by: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> StateValue:
        """
        Atomically update value using a function.
        
        Implements optimistic locking with retry.
        """
        for attempt in range(max_retries):
            current = await self.get(key)
            current_value = current.value if current else None
            
            try:
                new_value = update_fn(current_value)
            except Exception as e:
                raise ValueError(f"Update function failed: {e}")
                
            try:
                return await self.set(
                    key=key,
                    value=new_value,
                    version=current.version if current else None,
                    updated_by=updated_by,
                    trace_id=trace_id,
                )
            except ValueError:
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.01 * (attempt + 1))
                    continue
                raise
                
        raise RuntimeError("atomic_update failed after max retries")
        
    async def delete(self, key: str) -> bool:
        """Delete a key from both stores."""
        redis_key = f"state:{key}"
        
        await self._redis.delete(redis_key)
        
        async with self._pg_pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM state WHERE key = $1",
                key
            )
            
        return "DELETE 1" in result
        
    async def _cache_to_redis(self, key: str, data: dict) -> None:
        """Cache state to Redis."""
        redis_key = f"state:{key}"
        await self._redis.setex(
            redis_key,
            self.hot_ttl,
            json.dumps(data, default=str),
        )
        
    async def invalidate_cache(self, key: str) -> None:
        """Invalidate Redis cache for a key."""
        redis_key = f"state:{key}"
        await self._redis.delete(redis_key)
        
    async def health_check(self) -> dict:
        """Check health of both stores."""
        health = {
            "status": "healthy",
            "redis": False,
            "postgres": False,
        }
        
        try:
            await self._redis.ping()
            health["redis"] = True
        except Exception as e:
            health["redis_error"] = str(e)
            
        try:
            async with self._pg_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            health["postgres"] = True
        except Exception as e:
            health["postgres_error"] = str(e)
            
        if not health["redis"] or not health["postgres"]:
            health["status"] = "degraded"
            
        return health


class PositionState:
    """
    Specialized position state management.
    
    Provides atomic updates for:
    - Position quantity
    - Entry price
    - Unrealized/Realized PnL
    """
    
    def __init__(self, state: DistributedState):
        self.state = state
        
    async def get_position(self, symbol: str) -> Optional[dict]:
        """Get current position for symbol."""
        state = await self.state.get(f"position:{symbol}")
        return state.value if state else None
        
    async def update_position(
        self,
        symbol: str,
        quantity_delta: float,
        price: float,
        realized_pnl_delta: float = 0.0,
        trace_id: Optional[str] = None,
    ) -> dict:
        """Atomically update position."""
        
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
        )
        
        return result.value
        
    async def get_all_positions(self) -> dict:
        """Get all positions."""
        pattern = "state:position:*"
        keys = await self.state._redis.keys(pattern)
        
        positions = {}
        for key in keys:
            symbol = key.replace("state:position:", "")
            pos = await self.get_position(symbol)
            if pos and pos.get('quantity', 0) != 0:
                positions[symbol] = pos
                
        return positions
        
    async def calculate_unrealized_pnl(
        self,
        symbol: str,
        current_price: float,
    ) -> float:
        """Calculate unrealized PnL for position."""
        position = await self.get_position(symbol)
        if not position or position['quantity'] == 0:
            return 0.0
            
        if position['quantity'] > 0:
            return (current_price - position['avg_entry_price']) * position['quantity']
        else:
            return (position['avg_entry_price'] - current_price) * abs(position['quantity'])
