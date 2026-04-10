"""
Decision Resolver - Autonomy engine for trade execution decisions.

Implements a 5-tier decision hierarchy:
1. Safety First - Kill switch and hard limits
2. Data Quality - Valid market data check
3. Edge Validation - Positive expectation check
4. Risk Compliance - Risk limits check  
5. Execution Decision - Auto or manual approval

The system operates autonomously by default but can be configured
to require human approval for certain decisions.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable, Any

logger = logging.getLogger(__name__)


class DecisionLevel(Enum):
    """Decision hierarchy levels."""
    KILL_SWITCH = "kill_switch"
    DATA_QUALITY = "data_quality"
    EDGE_VALIDATION = "edge_validation"
    RISK_COMPLIANCE = "risk_compliance"
    EXECUTION = "execution"


class DecisionOutcome(Enum):
    """Possible decision outcomes."""
    APPROVED = "approved"
    REJECTED = "rejected"
    REQUIRES_APPROVAL = "requires_approval"
    PAUSED = "paused"
    KILL_SWITCH = "kill_switch"


class AutonomyMode(Enum):
    """Autonomy operating modes."""
    FULL_AUTO = "full_auto"       # Execute all approved trades
    SEMI_AUTO = "semi_auto"       # Auto-execute, human for size changes
    MANUAL = "manual"             # All trades require approval


@dataclass
class DecisionContext:
    """Context for decision making."""
    symbol: str
    side: str
    quantity: float
    price: Optional[float]
    signal_confidence: float
    signal_strength: float
    expected_edge: float
    current_drawdown: float
    daily_pnl: float
    current_leverage: float
    data_fresh: bool
    data_age_ms: int
    protection_level: int
    consecutive_failures: int


@dataclass
class Decision:
    """Decision result."""
    outcome: DecisionOutcome
    level: DecisionLevel
    reason: str
    details: dict
    approved_quantity: Optional[float] = None
    trace_id: Optional[str] = None


class DecisionResolver:
    """
    Autonomy decision engine with 5-tier hierarchy.
    
    Features:
    - Kill switch integration
    - Data quality gates
    - Edge validation
    - Risk compliance checks
    - Configurable autonomy modes
    
    Example:
        >>> resolver = DecisionResolver(autonomy_mode=AutonomyMode.FULL_AUTO)
        >>> decision = await resolver.resolve(context)
        >>> if decision.outcome == DecisionOutcome.APPROVED:
        ...     await executor.submit_order(order)
    """

    def __init__(
        self,
        autonomy_mode: AutonomyMode = AutonomyMode.FULL_AUTO,
        min_confidence: float = 0.3,
        min_edge: float = 0.0001,
        max_drawdown_pct: float = 0.15,
        max_daily_loss_pct: float = 0.05,
        max_leverage: float = 10.0,
        data_freshness_ms: int = 5000,
        pause_on_failure_count: int = 5,
        human_approval_callback: Optional[Callable] = None
    ):
        self.autonomy_mode = autonomy_mode
        self.min_confidence = min_confidence
        self.min_edge = min_edge
        self.max_drawdown_pct = max_drawdown_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_leverage = max_leverage
        self.data_freshness_ms = data_freshness_ms
        self.pause_on_failure_count = pause_on_failure_count
        self.human_approval_callback = human_approval_callback
        
        self._kill_switch_active = False
        self._paused = False
        self._pause_reason: Optional[str] = None
        self._decision_history: list[Decision] = []
        self._max_history = 1000
        
        self._stats = {
            "decisions_made": 0,
            "approved": 0,
            "rejected": 0,
            "requires_approval": 0,
            "paused": 0,
            "kill_switch_triggered": 0
        }

    async def resolve(self, context: DecisionContext) -> Decision:
        """
        Resolve a trade decision through the hierarchy.
        
        Args:
            context: Decision context with all required information
            
        Returns:
            Decision with outcome and details
        """
        if self._kill_switch_active:
            decision = Decision(
                outcome=DecisionOutcome.KILL_SWITCH,
                level=DecisionLevel.KILL_SWITCH,
                reason="Kill switch active",
                details={"kill_switch": True}
            )
            self._record_decision(decision)
            return decision
        
        if self._paused:
            decision = Decision(
                outcome=DecisionOutcome.PAUSED,
                level=DecisionLevel.EXECUTION,
                reason=self._pause_reason or "System paused",
                details={"paused": True}
            )
            self._record_decision(decision)
            self._stats["paused"] += 1
            return decision
        
        decision = await self._evaluate_hierarchy(context)
        self._record_decision(decision)
        
        self._stats["decisions_made"] += 1
        if decision.outcome == DecisionOutcome.APPROVED:
            self._stats["approved"] += 1
        elif decision.outcome == DecisionOutcome.REJECTED:
            self._stats["rejected"] += 1
        elif decision.outcome == DecisionOutcome.REQUIRES_APPROVAL:
            self._stats["requires_approval"] += 1
        
        return decision

    async def _evaluate_hierarchy(self, context: DecisionContext) -> Decision:
        """Evaluate through decision hierarchy."""
        
        decision = self._check_kill_switch(context)
        if decision:
            return decision
        
        decision = self._check_data_quality(context)
        if decision:
            return decision
        
        decision = self._check_edge_validation(context)
        if decision:
            return decision
        
        decision = self._check_risk_compliance(context)
        if decision:
            return decision
        
        return self._make_execution_decision(context)

    def _check_kill_switch(self, context: DecisionContext) -> Optional[Decision]:
        """Level 1: Kill switch check."""
        if self._kill_switch_active:
            return Decision(
                outcome=DecisionOutcome.KILL_SWITCH,
                level=DecisionLevel.KILL_SWITCH,
                reason="Kill switch triggered",
                details={"kill_switch": True}
            )
        return None

    def _check_data_quality(self, context: DecisionContext) -> Optional[Decision]:
        """Level 2: Data quality check."""
        if not context.data_fresh:
            return Decision(
                outcome=DecisionOutcome.REJECTED,
                level=DecisionLevel.DATA_QUALITY,
                reason="Data not fresh",
                details={"data_fresh": False, "data_age_ms": context.data_age_ms}
            )
        
        if context.data_age_ms > self.data_freshness_ms:
            return Decision(
                outcome=DecisionOutcome.REJECTED,
                level=DecisionLevel.DATA_QUALITY,
                reason=f"Data stale: {context.data_age_ms}ms > {self.data_freshness_ms}ms",
                details={"data_age_ms": context.data_age_ms}
            )
        
        return None

    def _check_edge_validation(self, context: DecisionContext) -> Optional[Decision]:
        """Level 3: Edge validation check."""
        if context.signal_confidence < self.min_confidence:
            return Decision(
                outcome=DecisionOutcome.REJECTED,
                level=DecisionLevel.EDGE_VALIDATION,
                reason=f"Confidence {context.signal_confidence} < {self.min_confidence}",
                details={"confidence": context.signal_confidence}
            )
        
        if context.expected_edge < self.min_edge:
            return Decision(
                outcome=DecisionOutcome.REJECTED,
                level=DecisionLevel.EDGE_VALIDATION,
                reason=f"Edge {context.expected_edge} < {self.min_edge}",
                details={"expected_edge": context.expected_edge}
            )
        
        if context.consecutive_failures >= self.pause_on_failure_count:
            return Decision(
                outcome=DecisionOutcome.PAUSED,
                level=DecisionLevel.EDGE_VALIDATION,
                reason=f"Too many failures: {context.consecutive_failures}",
                details={"consecutive_failures": context.consecutive_failures}
            )
        
        return None

    def _check_risk_compliance(self, context: DecisionContext) -> Optional[Decision]:
        """Level 4: Risk compliance check."""
        if context.current_drawdown > self.max_drawdown_pct:
            return Decision(
                outcome=DecisionOutcome.REJECTED,
                level=DecisionLevel.RISK_COMPLIANCE,
                reason=f"Drawdown {context.current_drawdown:.2%} > {self.max_drawdown_pct:.2%}",
                details={"drawdown": context.current_drawdown}
            )
        
        if context.daily_pnl < -self.max_daily_loss_pct:
            return Decision(
                outcome=DecisionOutcome.REJECTED,
                level=DecisionLevel.RISK_COMPLIANCE,
                reason=f"Daily loss {context.daily_pnl:.2%} > {self.max_daily_loss_pct:.2%}",
                details={"daily_pnl": context.daily_pnl}
            )
        
        if context.current_leverage > self.max_leverage:
            return Decision(
                outcome=DecisionOutcome.REJECTED,
                level=DecisionLevel.RISK_COMPLIANCE,
                reason=f"Leverage {context.current_leverage}x > {self.max_leverage}x",
                details={"leverage": context.current_leverage}
            )
        
        return None

    def _make_execution_decision(self, context: DecisionContext) -> Decision:
        """Level 5: Execution decision based on autonomy mode."""
        
        if self.autonomy_mode == AutonomyMode.MANUAL:
            return Decision(
                outcome=DecisionOutcome.REQUIRES_APPROVAL,
                level=DecisionLevel.EXECUTION,
                reason="Manual mode - human approval required",
                details={"autonomy_mode": self.autonomy_mode.value}
            )
        
        if self.autonomy_mode == AutonomyMode.SEMI_AUTO:
            return Decision(
                outcome=DecisionOutcome.REQUIRES_APPROVAL,
                level=DecisionLevel.EXECUTION,
                reason="Semi-auto mode - human approval for size",
                details={"autonomy_mode": self.autonomy_mode.value}
            )
        
        return Decision(
            outcome=DecisionOutcome.APPROVED,
            level=DecisionLevel.EXECUTION,
            reason="Approved for auto-execution",
            details={
                "autonomy_mode": self.autonomy_mode.value,
                "approved_quantity": context.quantity
            },
            approved_quantity=context.quantity
        )

    def trigger_kill_switch(self, reason: str) -> None:
        """Trigger kill switch."""
        logger.critical(f"KILL SWITCH TRIGGERED: {reason}")
        self._kill_switch_active = True
        self._stats["kill_switch_triggered"] += 1

    def reset_kill_switch(self) -> None:
        """Reset kill switch."""
        self._kill_switch_active = False
        logger.info("Kill switch reset")

    def pause(self, reason: str) -> None:
        """Pause decision engine."""
        self._paused = True
        self._pause_reason = reason
        logger.warning(f"Decision engine paused: {reason}")

    def resume(self) -> None:
        """Resume decision engine."""
        self._paused = False
        self._pause_reason = None
        logger.info("Decision engine resumed")

    def set_autonomy_mode(self, mode: AutonomyMode) -> None:
        """Change autonomy mode."""
        old_mode = self.autonomy_mode
        self.autonomy_mode = mode
        logger.info(f"Autonomy mode changed: {old_mode.value} -> {mode.value}")

    def _record_decision(self, decision: Decision) -> None:
        """Record decision in history."""
        self._decision_history.append(decision)
        if len(self._decision_history) > self._max_history:
            self._decision_history = self._decision_history[-self._max_history:]

    def get_recent_decisions(self, count: int = 10) -> list[Decision]:
        """Get recent decisions."""
        return self._decision_history[-count:]

    def get_stats(self) -> dict:
        """Get decision statistics."""
        return self._stats.copy()

    def is_healthy(self) -> bool:
        """Check if decision engine is healthy."""
        return not self._kill_switch_active and not self._paused