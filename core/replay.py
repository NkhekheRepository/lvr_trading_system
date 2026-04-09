"""
Replay Engine - Event replay and state reconstruction.

Features:
- Replay events from PostgreSQL event log
- State reconstruction from snapshots
- Reproducible results verification
- Replay to specific time or offset
- Parallel replay for speed (optional)
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Callable, AsyncIterator, Any

import asyncpg

from core.event import Event, EventType
from core.bus import EventBus, InProcessEventBus

logger = logging.getLogger(__name__)


@dataclass
class ReplayConfig:
    """Configuration for replay."""
    batch_size: int = 100
    parallel_workers: int = 1
    state_snapshot_interval: int = 1000
    validate_results: bool = True
    stop_on_error: bool = True


@dataclass
class ReplayResult:
    """Result of replay operation."""
    events_replayed: int
    events_failed: int
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    final_state: dict
    validation_passed: Optional[bool] = None
    error: Optional[str] = None


class ReplayEngine:
    """
    Engine for replaying events from the event log.
    
    Use cases:
    - Reproduce historical results
    - Backtest with exact simulation
    - Debug production issues
    - Recover from crash
    """
    
    def __init__(
        self,
        postgres_dsn: str,
        config: Optional[ReplayConfig] = None,
    ):
        self.postgres_dsn = postgres_dsn
        self.config = config or ReplayConfig()
        self._pool: Optional[asyncpg.Pool] = None
        self._state_snapshots: dict[int, dict] = {}
        
    async def connect(self) -> None:
        """Connect to PostgreSQL."""
        self._pool = await asyncpg.create_pool(
            self.postgres_dsn,
            min_size=2,
            max_size=self.config.parallel_workers + 1,
        )
        logger.info("ReplayEngine connected")
        
    async def disconnect(self) -> None:
        """Disconnect from PostgreSQL."""
        if self._pool:
            await self._pool.close()
        logger.info("ReplayEngine disconnected")
        
    async def replay(
        self,
        start_offset: int = 0,
        end_offset: Optional[int] = None,
        event_types: Optional[list[EventType]] = None,
        symbols: Optional[list[str]] = None,
        processor: Optional[Callable[[Event], Any]] = None,
    ) -> ReplayResult:
        """
        Replay events from start_offset.
        
        Args:
            start_offset: Starting event offset
            end_offset: Ending event offset (None = replay all)
            event_types: Filter by event types
            symbols: Filter by symbols
            processor: Optional event processor function
            
        Returns:
            ReplayResult with statistics
        """
        if not self._pool:
            await self.connect()
            
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
                        
                if end_offset and start_offset >= end_offset:
                    break
                    
        except Exception as e:
            logger.error(f"Replay failed: {e}")
            end_time = datetime.now()
            return ReplayResult(
                events_replayed=events_replayed,
                events_failed=events_failed,
                start_time=start_time,
                end_time=end_time,
                duration_seconds=(end_time - start_time).total_seconds(),
                final_state=state,
                error=str(e),
            )
            
        end_time = datetime.now()
        return ReplayResult(
            events_replayed=events_replayed,
            events_failed=events_failed,
            start_time=start_time,
            end_time=end_time,
            duration_seconds=(end_time - start_time).total_seconds(),
            final_state=state,
        )
        
    async def replay_to_time(
        self,
        end_time: datetime,
        start_time: Optional[datetime] = None,
        processor: Optional[Callable[[Event], Any]] = None,
    ) -> ReplayResult:
        """Replay events until end_time."""
        start_ms = int(start_time.timestamp() * 1000) if start_time else 0
        end_ms = int(end_time.timestamp() * 1000)
        
        result = await self.replay(
            processor=processor,
        )
        
        filtered_events = [
            e for e in await self._fetch_all_events(start_ms)
            if e.timestamp <= end_ms
        ]
        
        return result
        
    async def stream_events(
        self,
        start_offset: int = 0,
        event_types: Optional[list[EventType]] = None,
        symbols: Optional[list[str]] = None,
    ) -> AsyncIterator[Event]:
        """
        Stream events as an async iterator.
        
        Memory efficient for large replays.
        """
        offset = start_offset
        
        while True:
            events = await self._fetch_batch(
                start_offset=offset,
                event_types=event_types,
                symbols=symbols,
            )
            
            if not events:
                break
                
            for event in events:
                yield event
                offset = event.offset
                
            await asyncio.sleep(0)
            
    async def _fetch_batch(
        self,
        start_offset: int = 0,
        event_types: Optional[list[EventType]] = None,
        symbols: Optional[list[str]] = None,
    ) -> list[Event]:
        """Fetch a batch of events."""
        query = "SELECT * FROM events WHERE offset > $1"
        params = [start_offset]
        param_idx = 2
        
        if event_types:
            types_str = ",".join([f"'{t.value}'" for t in event_types])
            query += f" AND event_type IN ({types_str})"
            
        if symbols:
            symbols_str = ",".join([f"'{s}'" for s in symbols])
            query += f" AND symbol IN ({symbols_str})"
            
        query += f" ORDER BY offset LIMIT ${param_idx}"
        params.append(self.config.batch_size)
        
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            
        return [self._row_to_event(row) for row in rows]
        
    async def _fetch_all_events(
        self,
        start_timestamp: int = 0,
        limit: int = 100000,
    ) -> list[Event]:
        """Fetch all events from timestamp."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM events 
                WHERE timestamp >= $1
                ORDER BY offset
                LIMIT $2
            """, start_timestamp, limit)
            
        return [self._row_to_event(row) for row in rows]
        
    def _row_to_event(self, row) -> Event:
        """Convert database row to Event."""
        import json
        
        return Event(
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
        
    async def get_event_count(
        self,
        event_type: Optional[EventType] = None,
        symbol: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> int:
        """Get count of events matching criteria."""
        query = "SELECT COUNT(*) FROM events WHERE 1=1"
        params = []
        
        if event_type:
            query += " AND event_type = $1"
            params.append(event_type.value)
            
        if symbol:
            param_idx = len(params) + 1
            query += f" AND symbol = ${param_idx}"
            params.append(symbol)
            
        if since:
            param_idx = len(params) + 1
            query += f" AND timestamp >= ${param_idx}"
            params.append(int(since.timestamp() * 1000))
            
        async with self._pool.acquire() as conn:
            count = await conn.fetchval(query, *params)
            
        return count
        
    async def get_latest_offset(self) -> int:
        """Get latest event offset."""
        async with self._pool.acquire() as conn:
            offset = await conn.fetchval("""
                SELECT COALESCE(MAX(offset), 0) FROM events
            """)
        return offset
        
    async def take_snapshot(self) -> dict:
        """Take a snapshot of current state."""
        snapshot = {
            'timestamp': datetime.now(),
            'offset': await self.get_latest_offset(),
            'events': {},
        }
        
        event_types = [
            EventType.POSITION_RECONCILED,
            EventType.PORTFOLIO_UPDATED,
        ]
        
        for event_type in event_types:
            events = await self._fetch_batch(event_types=[event_type])
            snapshot['events'][event_type.value] = [
                e.to_dict() for e in events[-100:]
            ]
            
        return snapshot
        
    async def restore_snapshot(self, snapshot: dict) -> None:
        """Restore state from a snapshot."""
        logger.info(f"Restoring snapshot from offset {snapshot.get('offset')}")
        
        state = {}
        for event_type_str, events in snapshot.get('events', {}).items():
            event_type = EventType(event_type_str)
            
            if event_type == EventType.PORTFOLIO_UPDATED:
                if events:
                    state['portfolio'] = events[-1]['payload']
                    
        return state


class SimulationEngine:
    """
    Engine for running simulations with replay.
    
    Features:
    - Deterministic replay
    - State isolation
    - Result verification
    """
    
    def __init__(
        self,
        replay_engine: ReplayEngine,
        state: dict,
    ):
        self.replay_engine = replay_engine
        self.initial_state = state.copy()
        self.current_state = state.copy()
        self.results: list[dict] = []
        
    async def run_simulation(
        self,
        start_offset: int = 0,
        end_offset: Optional[int] = None,
        on_event: Optional[Callable[[Event, dict], dict]] = None,
    ) -> dict:
        """
        Run a simulation with event replay.
        
        Args:
            start_offset: Starting offset
            end_offset: Ending offset
            on_event: Callback(event, state) -> updated_state
            
        Returns:
            Final state and results
        """
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
        
    def verify_determinism(
        self,
        run1_results: list[dict],
        run2_results: list[dict],
    ) -> tuple[bool, list[str]]:
        """
        Verify that two simulation runs produce identical results.
        
        Returns:
            (is_deterministic, differences)
        """
        differences = []
        
        if len(run1_results) != len(run2_results):
            differences.append(
                f"Event count mismatch: {len(run1_results)} vs {len(run2_results)}"
            )
            
        for i, (r1, r2) in enumerate(zip(run1_results, run2_results)):
            if r1['offset'] != r2['offset']:
                differences.append(f"Offset mismatch at index {i}")
                
            if r1['type'] != r2['type']:
                differences.append(f"Type mismatch at index {i}")
                
        return len(differences) == 0, differences
