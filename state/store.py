"""
State management with PostgreSQL and Redis.
"""

import asyncio
import json
import logging
import time
from typing import Optional

import redis.asyncio as redis

from app.schemas import Portfolio, Position, RiskState, SystemEvent, EventType

logger = logging.getLogger(__name__)


class StateStore:
    """
    Multi-layer state management.
    
    PostgreSQL = Authoritative (ACID)
    Event Log = Append-only history
    Redis = Fast cache
    """

    def __init__(
        self,
        pg_config: dict = None,
        redis_config: dict = None,
        checkpoint_interval: int = 60
    ):
        self.pg_config = pg_config or {}
        self.redis_config = redis_config or {}
        self.checkpoint_interval = checkpoint_interval

        self._pg_pool = None
        self._redis_client = None
        self._connected = False

        self._last_checkpoint = 0
        self._pending_events: list[SystemEvent] = []

    async def connect(self) -> None:
        """Connect to state stores."""
        logger.info("Connecting to state stores")

        if self.redis_config:
            try:
                self._redis_client = redis.Redis(
                    host=self.redis_config.get("host", "localhost"),
                    port=self.redis_config.get("port", 6379),
                    db=self.redis_config.get("db", 0),
                    password=self.redis_config.get("password") or None,
                    decode_responses=True
                )
                await self._redis_client.ping()
                logger.info("Redis connected")
            except Exception as e:
                logger.warning(f"Redis connection failed: {e}")
                self._redis_client = None

        self._connected = True

    async def disconnect(self) -> None:
        """Disconnect from state stores."""
        if self._redis_client:
            await self._redis_client.close()
        self._connected = False

    async def save_position(self, position: Position) -> None:
        """Save position to PostgreSQL."""
        if not self._pg_pool:
            await self._save_position_redis(position)
            return

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
        except Exception as e:
            logger.error(f"Failed to save position: {e}")
            await self._save_position_redis(position)

    async def _save_position_redis(self, position: Position) -> None:
        """Save position to Redis."""
        if not self._redis_client:
            return

        try:
            key = f"position:{position.symbol}"
            data = {
                "symbol": position.symbol,
                "quantity": position.quantity,
                "entry_price": position.entry_price,
                "updated_at": position.last_update
            }
            await self._redis_client.set(key, json.dumps(data))
        except Exception as e:
            logger.error(f"Redis save failed: {e}")

    async def load_positions(self) -> dict[str, Position]:
        """Load all positions."""
        positions = {}

        if self._redis_client:
            try:
                keys = await self._redis_client.keys("position:*")
                for key in keys:
                    data = await self._redis_client.get(key)
                    if data:
                        pos_data = json.loads(data)
                        positions[pos_data["symbol"]] = Position(**pos_data)
            except Exception as e:
                logger.error(f"Failed to load from Redis: {e}")

        return positions

    async def save_portfolio(self, portfolio: Portfolio) -> None:
        """Save portfolio snapshot."""
        if not self._redis_client:
            return

        try:
            key = "portfolio:current"
            data = portfolio.model_dump()
            await self._redis_client.set(key, json.dumps(data))
        except Exception as e:
            logger.error(f"Failed to save portfolio: {e}")

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

    def append_event(self, event: SystemEvent) -> None:
        """Append event to log."""
        self._pending_events.append(event)

        if len(self._pending_events) >= 100:
            asyncio.create_task(self._flush_events())

    async def _flush_events(self) -> None:
        """Flush pending events to storage."""
        if not self._pending_events:
            return

        events = self._pending_events.copy()
        self._pending_events.clear()

        logger.debug(f"Flushing {len(events)} events")

    async def checkpoint(self, force: bool = False) -> None:
        """Create checkpoint if interval passed."""
        now = time.time()
        if not force and now - self._last_checkpoint < self.checkpoint_interval:
            return

        logger.debug("Creating checkpoint")
        self._last_checkpoint = now

    async def recover(self) -> dict:
        """Recover state from storage."""
        logger.info("Starting state recovery")

        positions = await self.load_positions()
        portfolio = await self.load_portfolio()

        return {
            "positions": positions,
            "portfolio": portfolio,
            "recovered": bool(positions or portfolio)
        }
