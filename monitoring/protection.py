"""
Protection system with multi-level response.
"""

import logging
import time
from typing import Optional

from app.schemas import AlertSeverity, MetricsSnapshot, ProtectionLevel

logger = logging.getLogger(__name__)


class ProtectionSystem:
    """
    Multi-level protection system.
    
    Level 1: Reduce size
    Level 2: Restrict trading
    Level 3: Close all + halt
    """

    def __init__(self, alert_manager=None):
        self.alert_manager = alert_manager
        self._current_level = ProtectionLevel.NONE
        self._last_escalation = 0
        self._escalation_cooldown = 60

        self._active_protections: dict[str, bool] = {}

    @property
    def protection_level(self) -> ProtectionLevel:
        return self._current_level

    def evaluate(
        self,
        metrics: MetricsSnapshot,
        portfolio_drawdown: float,
        daily_loss_pct: float
    ) -> ProtectionLevel:
        """Evaluate required protection level."""
        new_level = ProtectionLevel.NONE

        if metrics.consecutive_failures >= 5:
            new_level = ProtectionLevel.RESTRICT_TRADING

        if metrics.slippage_error > metrics.avg_slippage * 2 and metrics.avg_slippage > 0:
            new_level = max(new_level, ProtectionLevel.REDUCE_SIZE)

        if metrics.fill_rate < 0.5:
            new_level = max(new_level, ProtectionLevel.RESTRICT_TRADING)

        if portfolio_drawdown > 0.10:
            new_level = ProtectionLevel.CLOSE_ALL_HALT
        elif portfolio_drawdown > 0.08:
            new_level = max(new_level, ProtectionLevel.RESTRICT_TRADING)
        elif portfolio_drawdown > 0.05:
            new_level = max(new_level, ProtectionLevel.REDUCE_SIZE)

        if abs(daily_loss_pct) > 0.03:
            new_level = ProtectionLevel.CLOSE_ALL_HALT
        elif abs(daily_loss_pct) > 0.02:
            new_level = max(new_level, ProtectionLevel.RESTRICT_TRADING)

        if not metrics.data_fresh:
            new_level = max(new_level, ProtectionLevel.REDUCE_SIZE)

        if new_level > self._current_level:
            if time.time() - self._last_escalation > self._escalation_cooldown:
                self._escalate(new_level)
            else:
                logger.warning(f"Escalation blocked by cooldown")

        return self._current_level

    def _escalate(self, new_level: ProtectionLevel) -> None:
        """Escalate protection level."""
        old_level = self._current_level
        self._current_level = new_level
        self._last_escalation = time.time()

        logger.critical(
            f"PROTECTION ESCALATION: {old_level} -> {new_level}"
        )

        if self.alert_manager:
            self.alert_manager.send_alert(
                severity=AlertSeverity.CRITICAL if new_level == ProtectionLevel.CLOSE_ALL_HALT else AlertSeverity.WARNING,
                category="PROTECTION",
                message=f"Protection level escalated: {old_level.name} -> {new_level.name}",
                source_module="protection_system",
                details={"old_level": old_level.name, "new_level": new_level.name}
            )

    def apply_protection(
        self,
        level: ProtectionLevel
    ) -> dict:
        """Apply protection actions and return instructions."""
        actions = {
            "should_reduce_size": False,
            "should_restrict_trading": False,
            "should_close_all": False,
            "should_halt": False,
            "max_order_per_minute": None,
            "size_multiplier": 1.0
        }

        if level >= ProtectionLevel.REDUCE_SIZE:
            actions["should_reduce_size"] = True
            actions["size_multiplier"] = 0.5

        if level >= ProtectionLevel.RESTRICT_TRADING:
            actions["should_restrict_trading"] = True
            actions["max_order_per_minute"] = 1

        if level >= ProtectionLevel.CLOSE_ALL_HALT:
            actions["should_close_all"] = True
            actions["should_halt"] = True
            actions["size_multiplier"] = 0

        return actions

    def check_anomalies(self, metrics: MetricsSnapshot) -> list[str]:
        """Detect anomalies in metrics."""
        anomalies = []

        if metrics.fill_rate < 0.6:
            anomalies.append(f"Low fill rate: {metrics.fill_rate:.2%}")

        if metrics.avg_slippage > 0.001:
            anomalies.append(f"High slippage: {metrics.avg_slippage:.5f}")

        if metrics.order_latency_ms > 500:
            anomalies.append(f"High latency: {metrics.order_latency_ms:.0f}ms")

        if metrics.rejection_rate > 0.2:
            anomalies.append(f"High rejection rate: {metrics.rejection_rate:.2%}")

        if not metrics.data_fresh:
            anomalies.append(f"Stale data: {metrics.last_tick_age_sec:.1f}s old")

        if metrics.consecutive_failures > 3:
            anomalies.append(f"Consecutive failures: {metrics.consecutive_failures}")

        return anomalies

    def reset(self) -> None:
        """Reset protection system."""
        self._current_level = ProtectionLevel.NONE
        self._active_protections.clear()
        logger.info("Protection system reset")

    def deescalate(self) -> bool:
        """Attempt to deescalate protection level."""
        if self._current_level > ProtectionLevel.NONE:
            old_level = self._current_level
            self._current_level = ProtectionLevel(self._current_level.value - 1)
            logger.info(f"Protection deescalated: {old_level} -> {self._current_level}")
            return True
        return False
