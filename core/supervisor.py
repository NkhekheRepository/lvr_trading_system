"""
Supervisor - Process monitoring and lifecycle management.

Features:
- Monitor all processors and components
- Automatic restart on failure
- State reloading from snapshots
- Resume from event offset
- Health check endpoints
- Graceful shutdown
"""

import asyncio
import logging
import signal
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Optional, Any
from collections import defaultdict

from core.bus import EventBus
from core.state import DistributedState

logger = logging.getLogger(__name__)


class ComponentStatus(Enum):
    """Component status."""
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass
class ComponentHealth:
    """Health information for a component."""
    name: str
    status: ComponentStatus
    last_heartbeat: Optional[datetime] = None
    restart_count: int = 0
    consecutive_failures: int = 0
    error_message: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    
    def is_healthy(self, heartbeat_timeout: int = 30) -> bool:
        """Check if component is healthy."""
        if self.status in [ComponentStatus.FAILED, ComponentStatus.STOPPED]:
            return False
        if self.last_heartbeat:
            age = (datetime.now() - self.last_heartbeat).total_seconds()
            return age < heartbeat_timeout
        return self.status == ComponentStatus.RUNNING


@dataclass
class SupervisorConfig:
    """Configuration for supervisor."""
    heartbeat_interval: int = 10
    heartbeat_timeout: int = 30
    max_restart_attempts: int = 5
    restart_delay: float = 1.0
    graceful_shutdown_timeout: int = 30
    health_check_interval: int = 60


class Supervisor:
    """
    Supervisor for monitoring and managing system components.
    
    Responsibilities:
    - Monitor component health via heartbeats
    - Restart failed components
    - Reload state from snapshots
    - Resume from event offsets
    - Coordinate graceful shutdown
    - Expose health endpoints
    """
    
    def __init__(
        self,
        config: Optional[SupervisorConfig] = None,
        state: Optional[DistributedState] = None,
        event_bus: Optional[EventBus] = None,
    ):
        self.config = config or SupervisorConfig()
        self.state = state
        self.event_bus = event_bus
        
        self._components: dict[str, 'ManagedComponent'] = {}
        self._running = False
        self._tasks: list[asyncio.Task] = []
        
        self._lock = asyncio.Lock()
        self._shutdown_event = asyncio.Event()
        
        self._health: dict[str, ComponentHealth] = {}
        self._last_global_health_check: Optional[datetime] = None
        
        self._on_component_failure: Optional[Callable] = None
        self._on_system_halt: Optional[Callable] = None
        
    @dataclass
    class ManagedComponent:
        """A managed component with lifecycle control."""
        name: str
        start_fn: Callable[[], Any]
        stop_fn: Callable[[], Any]
        health_check_fn: Optional[Callable[[], bool]] = None
        restart_fn: Optional[Callable[[], Any]] = None
        
        component: Any = None
        status: ComponentStatus = ComponentStatus.UNKNOWN
        restart_count: int = 0
        
    def register(
        self,
        name: str,
        start_fn: Callable,
        stop_fn: Callable,
        health_check_fn: Optional[Callable[[], bool]] = None,
        restart_fn: Optional[Callable[[], Any]] = None,
    ) -> None:
        """Register a component for supervision."""
        self._components[name] = self.ManagedComponent(
            name=name,
            start_fn=start_fn,
            stop_fn=stop_fn,
            health_check_fn=health_check_fn,
            restart_fn=restart_fn,
        )
        logger.info(f"Registered component: {name}")
        
    def unregister(self, name: str) -> None:
        """Unregister a component."""
        if name in self._components:
            del self._components[name]
            logger.info(f"Unregistered component: {name}")
            
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
            
    async def stop(self, graceful: bool = True) -> None:
        """Stop the supervisor and all components."""
        async with self._lock:
            logger.info(f"Supervisor stopping (graceful={graceful})")
            self._running = False
            
            for name, comp in self._components.items():
                comp.status = ComponentStatus.STOPPING
                try:
                    if graceful:
                        stop_task = asyncio.create_task(comp.stop_fn())
                        await asyncio.wait_for(
                            stop_task,
                            timeout=self.config.graceful_shutdown_timeout
                        )
                    else:
                        await comp.stop_fn()
                        
                    comp.status = ComponentStatus.STOPPED
                    logger.info(f"Stopped component: {name}")
                except asyncio.TimeoutError:
                    logger.warning(f"Component {name} stop timeout")
                except Exception as e:
                    logger.error(f"Error stopping {name}: {e}")
                    
            for task in self._tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                    
            self._shutdown_event.set()
            logger.info("Supervisor stopped")
            
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
            
    def record_heartbeat(self, name: str) -> None:
        """Record a heartbeat from a component."""
        if name in self._health:
            self._health[name].last_heartbeat = datetime.now()
            self._health[name].status = ComponentStatus.RUNNING
            
    async def _heartbeat_loop(self) -> None:
        """Monitor component heartbeats."""
        while self._running:
            try:
                await asyncio.sleep(self.config.heartbeat_interval)
                
                now = datetime.now()
                
                for name, comp in self._components.items():
                    health = self._health.get(name)
                    
                    if not health:
                        continue
                        
                    if not health.last_heartbeat:
                        continue
                        
                    age = (now - health.last_heartbeat).total_seconds()
                    
                    if age > self.config.heartbeat_timeout:
                        logger.warning(f"Heartbeat timeout for {name}: {age:.0f}s")
                        
                        if comp.status == ComponentStatus.RUNNING:
                            comp.status = ComponentStatus.DEGRADED
                            health.status = ComponentStatus.DEGRADED
                            
                        if age > self.config.heartbeat_timeout * 2:
                            await self.restart_component(name, "Heartbeat timeout")
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat loop error: {e}")
                
    async def _health_check_loop(self) -> None:
        """Periodic comprehensive health check."""
        while self._running:
            try:
                await asyncio.sleep(self.config.health_check_interval)
                
                await self._perform_health_check()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")
                
    async def _perform_health_check(self) -> dict:
        """Perform comprehensive health check."""
        self._last_global_health_check = datetime.now()
        
        overall_healthy = True
        degraded_components = []
        failed_components = []
        
        for name, health in self._health.items():
            if not health.is_healthy(self.config.heartbeat_timeout):
                overall_healthy = False
                
                if health.status == ComponentStatus.FAILED:
                    failed_components.append(name)
                elif health.status == ComponentStatus.DEGRADED:
                    degraded_components.append(name)
                    
        comp = self._components.get('_system')
        if comp and not overall_healthy:
            if self._on_system_halt:
                await self._on_system_halt(
                    degraded=degraded_components,
                    failed=failed_components,
                )
                
        return {
            'healthy': overall_healthy,
            'timestamp': self._last_global_health_check.isoformat(),
            'degraded': degraded_components,
            'failed': failed_components,
            'components': {
                name: {
                    'status': h.status.value,
                    'last_heartbeat': h.last_heartbeat.isoformat() if h.last_heartbeat else None,
                    'restart_count': h.restart_count,
                    'consecutive_failures': h.consecutive_failures,
                }
                for name, h in self._health.items()
            }
        }
        
    async def get_health_status(self) -> dict:
        """Get current health status."""
        if self._last_global_health_check:
            check_age = (datetime.now() - self._last_global_health_check).total_seconds()
        else:
            check_age = None
            
        return {
            'supervisor_running': self._running,
            'last_check': self._last_global_health_check.isoformat() if self._last_global_health_check else None,
            'check_age_seconds': check_age,
            'components': {
                name: health.__dict__
                for name, health in self._health.items()
            },
            'component_count': len(self._components),
            'healthy_count': sum(
                1 for h in self._health.values() if h.is_healthy(self.config.heartbeat_timeout)
            ),
        }
        
    async def reload_state(self, snapshot: dict) -> None:
        """Reload state from a snapshot."""
        logger.info("Reloading state from snapshot")
        
        if self.state:
            for key, value in snapshot.get('state', {}).items():
                await self.state.set(key, value)
                
        logger.info("State reloaded")
        
    def set_on_failure_callback(self, callback: Callable) -> None:
        """Set callback for component failures."""
        self._on_component_failure = callback
        
    def set_on_halt_callback(self, callback: Callable) -> None:
        """Set callback for system halt."""
        self._on_system_halt = callback


class HealthEndpoint:
    """
    HTTP health check endpoints.
    
    Exposes:
    - /health/live - Is process alive?
    - /health/ready - Can accept traffic?
    - /health/deep - Deep health check
    """
    
    def __init__(self, supervisor: Supervisor):
        self.supervisor = supervisor
        
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
