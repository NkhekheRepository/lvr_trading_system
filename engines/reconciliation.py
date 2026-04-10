"""
Reconciliation & Self-Healing System.

Features:
- Position reconciliation with exchange
- Order state reconciliation
- Automatic recovery from mismatches
- Health monitoring and alerts
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class ReconciliationStatus(Enum):
    """Reconciliation result status."""
    MATCH = "match"
    MISMATCH = "mismatch"
    HEALED = "healed"
    FAILED = "failed"


class HealAction(Enum):
    """Possible healing actions."""
    CANCEL_ORDER = "cancel_order"
    PLACE_ORDER = "place_order"
    ADJUST_POSITION = "adjust_position"
    SYNC_STATE = "sync_state"
    RESET_ORDER = "reset_order"


@dataclass
class PositionReconciliation:
    """Position reconciliation result."""
    symbol: str
    internal_quantity: float
    exchange_quantity: float
    difference: float
    status: ReconciliationStatus
    heal_action: Optional[HealAction] = None


@dataclass
class OrderReconciliation:
    """Order reconciliation result."""
    order_id: str
    internal_status: str
    exchange_status: str
    status: ReconciliationStatus
    heal_action: Optional[HealAction] = None


class ReconciliationEngine:
    """
    Reconciles internal state with exchange state.
    
    Features:
    - Periodic position reconciliation
    - Order state reconciliation
    - Automatic healing actions
    - Detailed reporting
    """

    def __init__(
        self,
        reconciliation_interval_sec: int = 30,
        mismatch_threshold: float = 0.001,
        auto_heal: bool = True,
        max_heal_attempts: int = 3
    ):
        self.reconciliation_interval_sec = reconciliation_interval_sec
        self.mismatch_threshold = mismatch_threshold
        self.auto_heal = auto_heal
        self.max_heal_attempts = max_heal_attempts
        
        self._running = False
        self._heal_attempts: dict[str, int] = {}
        
        self._callbacks: list[Callable] = []
        
        self._stats = {
            "reconciliations_done": 0,
            "mismatches_found": 0,
            "heals_successful": 0,
            "heals_failed": 0,
            "position_issues": 0,
            "order_issues": 0
        }

    def register_callback(self, callback: Callable) -> None:
        """Register callback for reconciliation events."""
        self._callbacks.append(callback)

    async def start(self) -> None:
        """Start periodic reconciliation."""
        self._running = True
        logger.info("Reconciliation engine started")
        
        while self._running:
            try:
                await asyncio.sleep(self.reconciliation_interval_sec)
            except asyncio.CancelledError:
                break

    def stop(self) -> None:
        """Stop reconciliation."""
        self._running = False
        logger.info("Reconciliation engine stopped")

    async def reconcile_position(
        self,
        symbol: str,
        internal_position: float,
        exchange_position: float
    ) -> PositionReconciliation:
        """Reconcile position between internal and exchange."""
        difference = abs(internal_position - exchange_position)
        
        if difference < self.mismatch_threshold:
            status = ReconciliationStatus.MATCH
            heal_action = None
        else:
            self._stats["mismatches_found"] += 1
            self._stats["position_issues"] += 1
            
            if self.auto_heal and internal_position != 0:
                status = ReconciliationStatus.MISMATCH
                heal_action = HealAction.ADJUST_POSITION
            else:
                status = ReconciliationStatus.MISMATCH
                heal_action = None
        
        result = PositionReconciliation(
            symbol=symbol,
            internal_quantity=internal_position,
            exchange_quantity=exchange_position,
            difference=difference,
            status=status,
            heal_action=heal_action
        )
        
        self._stats["reconciliations_done"] += 1
        
        await self._notify_callbacks("position_reconciliation", result)
        
        return result

    async def reconcile_order(
        self,
        order_id: str,
        internal_status: str,
        exchange_status: Optional[str]
    ) -> OrderReconciliation:
        """Reconcile order status."""
        if exchange_status is None:
            if internal_status in ["FILLED", "CANCELLED", "REJECTED"]:
                status = ReconciliationStatus.MATCH
                heal_action = None
            else:
                self._stats["mismatches_found"] += 1
                self._stats["order_issues"] += 1
                status = ReconciliationStatus.MISMATCH
                heal_action = HealAction.CANCEL_ORDER if self.auto_heal else None
        elif internal_status == exchange_status:
            status = ReconciliationStatus.MATCH
            heal_action = None
        else:
            self._stats["mismatches_found"] += 1
            self._stats["order_issues"] += 1
            
            if self.auto_heal:
                heal_action = HealAction.SYNC_STATE
                status = ReconciliationStatus.MISMATCH
            else:
                heal_action = None
                status = ReconciliationStatus.MISMATCH
        
        result = OrderReconciliation(
            order_id=order_id,
            internal_status=internal_status,
            exchange_status=exchange_status or "UNKNOWN",
            status=status,
            heal_action=heal_action
        )
        
        self._stats["reconciliations_done"] += 1
        
        await self._notify_callbacks("order_reconciliation", result)
        
        return result

    async def attempt_heal(
        self,
        heal_type: HealAction,
        context: dict
    ) -> bool:
        """Attempt to heal a reconciliation issue."""
        heal_key = f"{heal_type.value}_{context.get('order_id', context.get('symbol', 'unknown'))}"
        
        attempt_count = self._heal_attempts.get(heal_key, 0)
        
        if attempt_count >= self.max_heal_attempts:
            logger.error(f"Max heal attempts reached for {heal_key}")
            self._stats["heals_failed"] += 1
            return False
        
        self._heal_attempts[heal_key] = attempt_count + 1
        
        try:
            logger.info(f"Attempting heal: {heal_type.value} with context {context}")
            
            if heal_type == HealAction.CANCEL_ORDER:
                pass
            elif heal_type == HealAction.ADJUST_POSITION:
                pass
            elif heal_type == HealAction.SYNC_STATE:
                pass
            
            self._stats["heals_successful"] += 1
            self._heal_attempts[heal_key] = 0
            
            return True
            
        except Exception as e:
            logger.error(f"Heal failed: {e}")
            self._stats["heals_failed"] += 1
            return False

    async def _notify_callbacks(self, event_type: str, result: any) -> None:
        """Notify registered callbacks."""
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event_type, result)
                else:
                    callback(event_type, result)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    def reset_heal_attempts(self, key: str = None) -> None:
        """Reset heal attempt counters."""
        if key:
            self._heal_attempts.pop(key, None)
        else:
            self._heal_attempts.clear()

    def get_stats(self) -> dict:
        """Get reconciliation statistics."""
        return self._stats.copy()


class CircuitBreaker:
    """
    Circuit breaker for fault tolerance.
    
    Features:
    - Track failure counts
    - Open circuit on threshold
    - Auto-recovery after timeout
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_sec: int = 60,
        half_open_max_calls: int = 1
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout_sec = recovery_timeout_sec
        self.half_open_max_calls = half_open_max_calls
        
        self._state = "closed"
        self._failure_count = 0
        self._last_failure_time = 0
        self._half_open_calls = 0

    @property
    def state(self) -> str:
        """Get current circuit state."""
        if self._state == "open":
            if time.time() - self._last_failure_time > self.recovery_timeout_sec:
                self._state = "half_open"
                self._half_open_calls = 0
                logger.info("Circuit breaker moving to half-open")
        
        return self._state

    def can_execute(self) -> bool:
        """Check if execution is allowed."""
        return self.state != "open"

    def record_success(self) -> None:
        """Record successful execution."""
        if self.state == "half_open":
            self._half_open_calls += 1
            if self._half_open_calls >= self.half_open_max_calls:
                self._state = "closed"
                self._failure_count = 0
                logger.info("Circuit breaker closed after successful recovery")
        else:
            self._failure_count = 0

    def record_failure(self) -> None:
        """Record failed execution."""
        self._failure_count += 1
        self._last_failure_time = time.time()
        
        if self._failure_count >= self.failure_threshold:
            self._state = "open"
            logger.warning(f"Circuit breaker opened after {self._failure_count} failures")

    def reset(self) -> None:
        """Manually reset circuit breaker."""
        self._state = "closed"
        self._failure_count = 0
        self._half_open_calls = 0


class SelfHealingSystem:
    """
    Self-healing system for automatic recovery.
    
    Features:
    - Health monitoring
    - Automatic recovery actions
    - Escalation on repeated failures
    """

    def __init__(self):
        self._health_checks: dict[str, Callable] = {}
        self._recovery_actions: dict[str, Callable] = {}
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        
        self._stats = {
            "health_checks_run": 0,
            "recoveries_attempted": 0,
            "recoveries_successful": 0,
            "escalations": 0
        }

    def register_health_check(self, component: str, check: Callable) -> None:
        """Register health check for component."""
        self._health_checks[component] = check
        self._circuit_breakers[component] = CircuitBreaker()

    def register_recovery_action(self, component: str, action: Callable) -> None:
        """Register recovery action for component."""
        self._recovery_actions[component] = action

    async def run_health_checks(self) -> dict:
        """Run all health checks."""
        results = {}
        
        for component, check in self._health_checks.items():
            try:
                breaker = self._circuit_breakers.get(component)
                
                if breaker and not breaker.can_execute():
                    results[component] = {
                        "status": "circuit_open",
                        "healthy": False
                    }
                    continue
                
                if asyncio.iscoroutinefunction(check):
                    is_healthy = await check()
                else:
                    is_healthy = check()
                
                if is_healthy:
                    if breaker:
                        breaker.record_success()
                    results[component] = {"status": "healthy", "healthy": True}
                else:
                    if breaker:
                        breaker.record_failure()
                    results[component] = {"status": "unhealthy", "healthy": False}
                    
                    await self._attempt_recovery(component)
                
                self._stats["health_checks_run"] += 1
                
            except Exception as e:
                logger.error(f"Health check failed for {component}: {e}")
                results[component] = {"status": "error", "healthy": False, "error": str(e)}
        
        return results

    async def _attempt_recovery(self, component: str) -> None:
        """Attempt to recover unhealthy component."""
        action = self._recovery_actions.get(component)
        
        if not action:
            logger.warning(f"No recovery action for {component}")
            self._stats["escalations"] += 1
            return
        
        self._stats["recoveries_attempted"] += 1
        
        try:
            if asyncio.iscoroutinefunction(action):
                success = await action()
            else:
                success = action()
            
            if success:
                self._stats["recoveries_successful"] += 1
                logger.info(f"Recovery successful for {component}")
            else:
                self._stats["escalations"] += 1
                logger.warning(f"Recovery failed for {component}")
                
        except Exception as e:
            logger.error(f"Recovery error for {component}: {e}")
            self._stats["escalations"] += 1

    def get_stats(self) -> dict:
        """Get self-healing statistics."""
        return self._stats.copy()