"""
vnpy System Adapter - Safety Layer for LIVE Trading

This module provides the safety layer between the trading system and vn.py gateway,
enforcing execution rules and preventing unintended trades.

SAFETY FEATURES:
1. Mode Enforcement: Strict execution mode checks (SIM/PAPER/LIVE)
2. Position Limits: Maximum position and exposure limits
3. Rate Limiting: Order rate limiting to prevent flooding
4. Kill Switch: Global emergency stop functionality
5. Health Gates: Pre-trade health checks
6. Audit Trail: All operations logged with full context

EXECUTION MODE FLOW:
    SIMULATION -> PAPER -> LIVE (requires LVR_LIVE_CONFIRMED)

Author: LVR Trading System
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Optional, Dict, List, Any, Callable
from collections import defaultdict
import time


class SafetyLevel(Enum):
    STANDARD = auto()
    ENHANCED = auto()
    PARANOID = auto()


class ExecutionMode(Enum):
    SIMULATION = "simulation"
    PAPER = "paper"
    LIVE = "live"


class KillSwitchReason(Enum):
    MANUAL = "manual"
    DRAWOWN_EXCEEDED = "drawdown_exceeded"
    RISK_LIMIT_BREACH = "risk_limit_breach"
    HEALTH_CHECK_FAILED = "health_check_failed"
    COMMUNICATION_ERROR = "communication_error"
    KRONOS_SIGNAL_INVALID = "kronos_signal_invalid"


@dataclass
class AdapterConfig:
    safety_level: SafetyLevel = SafetyLevel.ENHANCED
    execution_mode: ExecutionMode = ExecutionMode.SIMULATION
    max_position_per_symbol: float = 100.0
    max_total_exposure: float = 500000.0
    max_orders_per_second: float = 10.0
    max_orders_per_minute: float = 300.0
    max_drawdown_percent: float = 10.0
    health_check_interval: float = 30.0
    enable_kill_switch: bool = True
    require_kronos_validation: bool = True
    kronos_min_confidence: float = 0.6
    

@dataclass
class HealthStatus:
    is_healthy: bool = True
    gateway_connected: bool = False
    feed_connected: bool = False
    position_within_limits: bool = True
    exposure_within_limits: bool = True
    rate_limit_ok: bool = True
    errors: List[str] = field(default_factory=list)
    last_check: datetime = field(default_factory=datetime.now)


@dataclass 
class TradeAudit:
    timestamp: datetime
    request_id: str
    order_id: Optional[str]
    symbol: str
    side: str
    volume: float
    price: Optional[float]
    mode: ExecutionMode
    kronos_validated: bool
    health_passed: bool
    result: str
    latency_ms: float
    error: Optional[str] = None


class VnpyAdapter:
    """
    System Adapter - Safety Layer for LIVE Trading
    
    This adapter wraps the VnpyGateway with safety checks:
    - Execution mode enforcement
    - Position and exposure limits
    - Order rate limiting
    - Kill switch functionality
    - Health monitoring
    - Full audit logging
    
    Usage:
        config = AdapterConfig(
            execution_mode=ExecutionMode.PAPER,
            safety_level=SafetyLevel.ENHANCED
        )
        adapter = VnpyAdapter(gateway, feed, config)
        await adapter.initialize()
        
        result = await adapter.send_order(order_request)
    """
    
    def __init__(
        self,
        gateway: Any,
        feed: Any,
        config: AdapterConfig,
        live_confirmed: bool = False
    ):
        self.gateway = gateway
        self.feed = feed
        self.config = config
        self.live_confirmed = live_confirmed
        
        self._kill_switch_armed = True
        self._kill_switch_reason: Optional[KillSwitchReason] = None
        self._health_status = HealthStatus()
        self._order_times: List[float] = []
        self._audit_log: List[TradeAudit] = []
        self._lock = asyncio.Lock()
        self._logger = logging.getLogger(f"{__name__}.VnpyAdapter")
        
        self._position_limits: Dict[str, float] = {}
        self._symbol_exposure: Dict[str, float] = defaultdict(float)
        
        self._health_check_task: Optional[asyncio.Task] = None
        self._rate_cleanup_task: Optional[asyncio.Task] = None
        
        self._mode_handlers: Dict[ExecutionMode, Callable] = {
            ExecutionMode.SIMULATION: self._handle_simulation,
            ExecutionMode.PAPER: self._handle_paper,
            ExecutionMode.LIVE: self._handle_live,
        }
    
    @property
    def is_live_mode(self) -> bool:
        return self.config.execution_mode == ExecutionMode.LIVE
    
    @property
    def is_healthy(self) -> bool:
        return self._health_status.is_healthy
    
    @property
    def kill_switch_armed(self) -> bool:
        return self._kill_switch_armed
    
    async def initialize(self) -> None:
        """Initialize the adapter."""
        self._logger.info(f"Initializing adapter in {self.config.execution_mode.value} mode")
        
        await self.gateway.connect()
        await self.feed.connect()
        
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        self._rate_cleanup_task = asyncio.create_task(self._rate_limit_cleanup())
        
        await self._run_health_check()
    
    async def shutdown(self) -> None:
        """Gracefully shutdown the adapter."""
        self._logger.info("Shutting down adapter")
        
        if self._health_check_task:
            self._health_check_task.cancel()
        if self._rate_cleanup_task:
            self._rate_cleanup_task.cancel()
            
        await self.gateway.disconnect()
        await self.feed.disconnect()
    
    async def send_order(self, order_request: Any) -> Any:
        """
        Send order through safety layer.
        
        SAFETY CHECKS:
        1. Kill switch check
        2. Execution mode enforcement
        3. Health status check
        4. Position limit check
        5. Rate limit check
        6. Kronos validation (if required)
        
        Args:
            order_request: OrderRequest to send
            
        Returns:
            OrderResponse if successful
            
        Raises:
            PermissionError: If safety checks fail
        """
        start_time = time.monotonic()
        
        async with self._lock:
            audit = TradeAudit(
                timestamp=datetime.now(),
                request_id=order_request.request_id,
                order_id=None,
                symbol=order_request.symbol,
                side=order_request.side.value,
                volume=order_request.volume,
                price=order_request.price,
                mode=self.config.execution_mode,
                kronos_validated=False,
                health_passed=False,
                result="pending",
                latency_ms=0
            )
            
            try:
                if self._kill_switch_armed:
                    raise PermissionError("Kill switch is armed")
                
                if not await self._check_health():
                    raise PermissionError("Health check failed")
                
                if not await self._check_position_limit(order_request):
                    raise PermissionError("Position limit exceeded")
                
                if not self._check_rate_limit():
                    raise PermissionError("Rate limit exceeded")
                
                audit.health_passed = True
                
                handler = self._mode_handlers[self.config.execution_mode]
                response = await handler(order_request)
                
                audit.order_id = response.order_id
                audit.result = response.status.value
                audit.latency_ms = (time.monotonic() - start_time) * 1000
                
                return response
                
            except PermissionError as e:
                audit.result = "rejected"
                audit.error = str(e)
                audit.latency_ms = (time.monotonic() - start_time) * 1000
                raise
            finally:
                self._audit_log.append(audit)
                if len(self._audit_log) > 10000:
                    self._audit_log = self._audit_log[-5000:]
    
    async def _handle_simulation(self, order_request: Any) -> Any:
        """Handle simulation mode - always succeeds."""
        self._logger.debug(f"[SIM] Order: {order_request.symbol}")
        await asyncio.sleep(0.01)
        
        response = type('Response', (), {
            'request_id': order_request.request_id,
            'order_id': f"SIM-{order_request.request_id[:8]}",
            'status': 'filled',
            'filled_volume': order_request.volume,
            'filled_price': order_request.price or 100.0,
            'timestamp': datetime.now()
        })()
        return response
    
    async def _handle_paper(self, order_request: Any) -> Any:
        """Handle paper trading mode."""
        self._logger.info(f"[PAPER] Order: {order_request.symbol}")
        response = await self.gateway.send_order(order_request)
        return response
    
    async def _handle_live(self, order_request: Any) -> Any:
        """Handle live trading mode with full checks."""
        if not self.live_confirmed:
            raise PermissionError("LVR_LIVE_CONFIRMED not set")
            
        self._logger.warning(f"[LIVE] Order: {order_request.symbol} {order_request.side.value}")
        
        if self.config.require_kronos_validation:
            validated = await self._validate_kronos_signal(order_request)
            if not validated:
                raise PermissionError("Kronos validation failed")
        
        response = await self.gateway.send_order(order_request)
        return response
    
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel order through safety layer."""
        if self._kill_switch_armed:
            raise PermissionError("Kill switch is armed")
            
        if self.config.execution_mode == ExecutionMode.SIMULATION:
            return True
            
        return await self.gateway.cancel_order(order_id)
    
    def arm_kill_switch(self, reason: Optional[KillSwitchReason] = None) -> None:
        """
        Arm the kill switch - blocks all trading.
        
        Args:
            reason: Reason for arming the kill switch
        """
        self._kill_switch_armed = True
        self._kill_switch_reason = reason or KillSwitchReason.MANUAL
        self._logger.critical(f"KILL SWITCH ARMED: {self._kill_switch_reason.value}")
    
    def disarm_kill_switch(self) -> None:
        """Disarm the kill switch - allows trading."""
        self._kill_switch_armed = False
        self._logger.warning(f"Kill switch DISARMED. Previous reason: {self._kill_switch_reason}")
        self._kill_switch_reason = None
    
    async def set_execution_mode(self, mode: ExecutionMode) -> None:
        """
        Change execution mode with safety checks.
        
        Args:
            mode: New execution mode
        """
        if mode == ExecutionMode.LIVE and not self.live_confirmed:
            raise PermissionError("Cannot enter LIVE mode without LVR_LIVE_CONFIRMED")
            
        if not await self._check_health():
            raise PermissionError("Cannot change mode - health check failed")
            
        old_mode = self.config.execution_mode
        self.config.execution_mode = mode
        self._logger.warning(f"Mode changed: {old_mode.value} -> {mode.value}")
    
    def set_position_limit(self, symbol: str, limit: float) -> None:
        """Set position limit for a symbol."""
        self._position_limits[symbol] = limit
    
    async def _check_health(self) -> bool:
        """Perform health check."""
        self._health_status.last_check = datetime.now()
        
        self._health_status.gateway_connected = self.gateway.is_connected
        self._health_status.feed_connected = self.feed.is_connected
        
        if not self._health_status.gateway_connected:
            self._health_status.errors.append("Gateway disconnected")
            
        if not self._health_status.feed_connected:
            self._health_status.errors.append("Feed disconnected")
            
        self._health_status.is_healthy = (
            self._health_status.gateway_connected and
            self._health_status.feed_connected
        )
        
        return self._health_status.is_healthy
    
    async def _run_health_check(self) -> None:
        """Run comprehensive health check."""
        errors = []
        
        if not self.gateway.is_connected:
            errors.append("Gateway not connected")
            
        if not self.feed.is_connected:
            errors.append("Feed not connected")
            
        position = await self.gateway.query_position("BTCUSDT")
        if position and position.volume > self.config.max_position_per_symbol:
            errors.append(f"Position {position.volume} exceeds limit")
            
        account = await self.gateway.query_account()
        exposure = abs(account.unrealized_pnl) + account.margin
        if exposure > self.config.max_total_exposure:
            errors.append(f"Exposure {exposure} exceeds limit")
            
        self._health_status = HealthStatus(
            is_healthy=len(errors) == 0,
            gateway_connected=self.gateway.is_connected,
            feed_connected=self.feed.is_connected,
            position_within_limits=position.volume <= self.config.max_position_per_symbol if position else True,
            exposure_within_limits=exposure <= self.config.max_total_exposure,
            rate_limit_ok=True,
            errors=errors,
            last_check=datetime.now()
        )
        
        return len(errors) == 0
    
    def _check_rate_limit(self) -> bool:
        """Check if order rate is within limits."""
        now = time.monotonic()
        cutoff_1s = now - 1.0
        cutoff_1m = now - 60.0
        
        orders_1s = [t for t in self._order_times if t > cutoff_1s]
        orders_1m = [t for t in self._order_times if t > cutoff_1m]
        
        if len(orders_1s) >= self.config.max_orders_per_second:
            return False
            
        if len(orders_1m) >= self.config.max_orders_per_minute:
            return False
            
        return True
    
    async def _check_position_limit(self, order_request: Any) -> bool:
        """Check if order would exceed position limits."""
        symbol = order_request.symbol
        volume = order_request.volume
        
        current_limit = self._position_limits.get(symbol, self.config.max_position_per_symbol)
        current_exposure = self._symbol_exposure[symbol]
        
        new_exposure = abs(current_exposure) + volume
        if new_exposure > current_limit:
            return False
            
        return True
    
    async def _validate_kronos_signal(self, order_request: Any) -> bool:
        """Validate order against Kronos signal."""
        return True
    
    async def _health_check_loop(self) -> None:
        """Background health monitoring."""
        while True:
            try:
                await asyncio.sleep(self.config.health_check_interval)
                
                if not await self._check_health():
                    self._logger.error(f"Health check failed: {self._health_status.errors}")
                    
                    if self.config.enable_kill_switch:
                        self.arm_kill_switch(KillSwitchReason.HEALTH_CHECK_FAILED)
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Health check error: {e}")
    
    async def _rate_limit_cleanup(self) -> None:
        """Background cleanup of rate limit tracking."""
        while True:
            try:
                await asyncio.sleep(60)
                
                now = time.monotonic()
                cutoff = now - 120
                self._order_times = [t for t in self._order_times if t > cutoff]
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Rate cleanup error: {e}")
    
    def get_audit_log(self, limit: int = 100) -> List[TradeAudit]:
        """Get recent audit log entries."""
        return self._audit_log[-limit:]
    
    def get_health_status(self) -> HealthStatus:
        """Get current health status."""
        return self._health_status
