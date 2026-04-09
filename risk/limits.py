"""
Risk limits and breach detection.

This module implements the risk management layer with comprehensive
limit checking and multi-level protection mechanisms. The RiskEngine
provides defense-in-depth by checking multiple risk metrics and
implementing graduated protection responses.

Protection Levels (escalating):
    NONE: Normal operation, no restrictions.
    REDUCE_SIZE: Reduce position sizes by 50%.
    RESTRICT_TRADING: Block new orders, allow existing positions.
    CLOSE_ALL_HALT: Close all positions and halt trading (manual restart required).

Hard Limits:
    - Maximum leverage: 10x default
    - Maximum drawdown: 10% default
    - Maximum daily loss: 3% default
    - Maximum position size: 20% of capital

Soft Limits (warnings + adjustments):
    - Position size warning: 15%
    - Daily loss warning: 2%
    - Consecutive loss warning: 3 trades

Example:
    >>> from risk.limits import RiskEngine, RiskLimits
    >>> limits = RiskLimits(max_leverage=5.0, max_drawdown_pct=0.05)
    >>> engine = RiskEngine(limits)
    >>> 
    >>> result = engine.check_order(order, signal, portfolio, risk_state)
    >>> if not result.approved:
    ...     print(f"Rejected: {result.rejection_reason}")
    >>> for action in result.required_actions:
    ...     print(f"Action: {action}")
"""

import logging
from dataclasses import dataclass
from typing import Optional

from app.schemas import (
    OrderRequest, Portfolio, ProtectionLevel, RiskCheckResult, RiskState, Side, Signal
)

logger = logging.getLogger(__name__)

EPS = 1e-10


@dataclass
class RiskLimits:
    """
    Risk limit configuration.
    
    Defines all configurable risk parameters for the trading system.
    Hard limits trigger immediate rejections or actions; soft limits
    trigger warnings and size adjustments.
    
    Attributes:
        max_leverage: Maximum allowed leverage (e.g., 10.0 = 10x).
        max_drawdown_pct: Maximum drawdown before halt (e.g., 0.10 = 10%).
        max_daily_loss_pct: Maximum daily loss before halt (e.g., 0.03 = 3%).
        max_position_size_pct: Maximum position as % of capital (e.g., 0.20 = 20%).
        max_consecutive_losses: Max losing trades before restrictions.
        position_warning_pct: Position size warning threshold.
        daily_loss_warning_pct: Daily loss warning threshold.
        consecutive_loss_warning: Consecutive loss warning threshold.
    """
    max_leverage: float = 10.0
    max_drawdown_pct: float = 0.10
    max_daily_loss_pct: float = 0.03
    max_position_size_pct: float = 0.20
    max_consecutive_losses: int = 5

    position_warning_pct: float = 0.15
    daily_loss_warning_pct: float = 0.02
    consecutive_loss_warning: int = 3


class RiskEngine:
    """
    Risk management engine with limit checking.
    
    Implements defense-in-depth risk management with graduated
    protection levels. Hard limits trigger immediate action;
    soft limits trigger warnings and position size reduction.
    
    Protection Level Escalation:
        1. Normal operation (ProtectionLevel.NONE)
        2. Reduce sizes by 50% (ProtectionLevel.REDUCE_SIZE)
        3. Restrict new trading (ProtectionLevel.RESTRICT_TRADING)
        4. Close all, halt (ProtectionLevel.CLOSE_ALL_HALT)
    
    Attributes:
        limits: RiskLimits configuration.
        protection_level: Current protection level.
        is_halted: True if system is halted.
    
    Example:
        >>> engine = RiskEngine(RiskLimits(max_drawdown_pct=0.05))
        >>> result = engine.check_order(order, signal, portfolio, risk_state)
        >>> 
        >>> engine.record_trade_result(pnl=-100)
        >>> engine.record_trade_result(pnl=-50)
        >>> engine.record_trade_result(pnl=-75)
        >>> level = engine.evaluate_protection_level(portfolio)
    """

    def __init__(self, limits: RiskLimits = None):
        """
        Initialize risk engine.
        
        Args:
            limits: RiskLimits configuration. Uses defaults if None.
        """
        self.limits = limits or RiskLimits()

        self._consecutive_losses = 0
        self._protection_level = ProtectionLevel.NONE
        self._halted = False

    @property
    def protection_level(self) -> ProtectionLevel:
        """
        Get current protection level.
        
        Returns:
            Current ProtectionLevel enum value.
        """
        return self._protection_level

    @property
    def is_halted(self) -> bool:
        """
        Check if system is halted.
        
        Halted systems reject all new orders until manually restarted.
        
        Returns:
            True if system is halted, False otherwise.
        """
        return self._halted

    def check_order(
        self,
        order: OrderRequest,
        signal: Signal,
        portfolio: Portfolio,
        risk_state: RiskState
    ) -> RiskCheckResult:
        """
        Check if order passes risk limits.
        
        Performs comprehensive risk checks including leverage, drawdown,
        daily loss, position size, and protection level restrictions.
        
        Args:
            order: OrderRequest to check.
            signal: Signal generating the order.
            portfolio: Current portfolio state.
            risk_state: Current risk state (modified in place).
        
        Returns:
            RiskCheckResult containing:
            - approved: Whether order is allowed
            - risk_state: Updated risk state
            - adjusted_quantity: Modified quantity if size reduced
            - rejection_reason: Reason if not approved
            - required_actions: List of actions taken/adjustments
        
        Checks Performed:
            1. Halt check: Reject if system is halted
            2. Leverage check: Reject if exceeds max_leverage
            3. Drawdown check: Reject if exceeds max_drawdown_pct
            4. Daily loss check: Reject if exceeds max_daily_loss_pct
            5. Position size check: Adjust if exceeds max_position_size_pct
            6. Protection level check: Apply size reduction if active
        """
        if self._halted:
            return RiskCheckResult(
                approved=False,
                risk_state=risk_state,
                rejection_reason="System halted"
            )

        risk_state = self._update_risk_state(portfolio, risk_state)

        approved = True
        adjusted_qty = None
        required_actions = []
        rejection_reason = None

        if not risk_state.leverage_ok:
            approved = False
            rejection_reason = f"Leverage exceeded: {risk_state.current_leverage:.1f}x"
        elif not risk_state.drawdown_ok:
            approved = False
            rejection_reason = f"Drawdown exceeded: {risk_state.current_drawdown:.2%}"
        elif not risk_state.daily_loss_ok:
            if self._protection_level >= ProtectionLevel.RESTRICT_TRADING:
                approved = False
                rejection_reason = f"Daily loss limit exceeded: {risk_state.daily_loss:.2%}"

        position_value = order.quantity * (order.price or 50000)
        position_pct = position_value / portfolio.current_capital

        if position_pct > self.limits.max_position_size_pct:
            adjusted_qty = (portfolio.current_capital * self.limits.max_position_size_pct) / (order.price or 50000)
            required_actions.append(f"Position reduced from {order.quantity:.4f} to {adjusted_qty:.4f}")
            order.quantity = adjusted_qty

        if self._protection_level >= ProtectionLevel.REDUCE_SIZE:
            order.quantity *= 0.5
            required_actions.append("Position halved due to protection level")

        return RiskCheckResult(
            approved=approved,
            risk_state=risk_state,
            adjusted_quantity=adjusted_qty,
            rejection_reason=rejection_reason,
            required_actions=required_actions
        )

    def _update_risk_state(self, portfolio: Portfolio, risk_state: RiskState) -> RiskState:
        """
        Update risk state from portfolio.
        
        Computes current risk metrics from portfolio state and
        updates the risk_state object with pass/fail flags.
        
        Args:
            portfolio: Portfolio to extract metrics from.
            risk_state: RiskState to update (modified in place).
        
        Returns:
            Updated risk_state object.
        
        Computed Metrics:
            - current_leverage: From portfolio.portfolio_leverage
            - current_drawdown: From portfolio.current_drawdown
            - daily_loss: portfolio.daily_pnl / portfolio.initial_capital
            - leverage_ok: current_leverage <= max_leverage
            - drawdown_ok: current_drawdown <= max_drawdown_pct
            - daily_loss_ok: daily_loss >= -max_daily_loss_pct
        """
        risk_state.current_leverage = portfolio.portfolio_leverage
        risk_state.current_drawdown = portfolio.current_drawdown
        risk_state.daily_loss = portfolio.daily_pnl / portfolio.initial_capital

        risk_state.leverage_ok = risk_state.current_leverage <= self.limits.max_leverage
        risk_state.drawdown_ok = risk_state.current_drawdown <= self.limits.max_drawdown_pct
        risk_state.daily_loss_ok = risk_state.daily_loss >= -self.limits.max_daily_loss_pct

        risk_state.consecutive_losses = self._consecutive_losses
        risk_state.protection_level = self._protection_level

        return risk_state

    def record_trade_result(self, pnl: float) -> None:
        """
        Record trade result for loss tracking.
        
        Tracks consecutive wins/losses for protection level evaluation.
        
        Args:
            pnl: Trade profit/loss amount. Positive = win, negative = loss.
        
        Side Effects:
            Increments _consecutive_losses on loss, resets on win.
        """
        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        logger.info(f"Trade result: PnL={pnl:.2f}, consecutive losses={self._consecutive_losses}")

    def evaluate_protection_level(self, portfolio: Portfolio) -> ProtectionLevel:
        """
        Evaluate required protection level based on conditions.
        
        Checks all risk metrics and determines the appropriate
        protection level. Protection level only increases, never
        decreases (requires manual intervention).
        
        Args:
            portfolio: Current portfolio state for evaluation.
        
        Returns:
            New protection level (may be same as current if conditions
            haven't worsened).
        
        Escalation Triggers:
            - Drawdown > 80% of max: RESTRICT_TRADING
            - Drawdown > 100% of max: CLOSE_ALL_HALT
            - Daily loss > 100% of max: CLOSE_ALL_HALT
            - Consecutive losses >= warning threshold: REDUCE_SIZE
            - Consecutive losses >= max: RESTRICT_TRADING
        """
        new_level = ProtectionLevel.NONE

        if portfolio.current_drawdown > self.limits.max_drawdown_pct:
            new_level = ProtectionLevel.CLOSE_ALL_HALT
            self._halted = True
        elif portfolio.current_drawdown > self.limits.max_drawdown_pct * 0.8:
            new_level = ProtectionLevel.RESTRICT_TRADING
        elif abs(portfolio.daily_pnl) > portfolio.initial_capital * self.limits.max_daily_loss_pct:
            new_level = ProtectionLevel.CLOSE_ALL_HALT
            self._halted = True
        elif self._consecutive_losses >= self.limits.max_consecutive_losses:
            new_level = ProtectionLevel.RESTRICT_TRADING
        elif self._consecutive_losses >= self.limits.consecutive_loss_warning:
            new_level = ProtectionLevel.REDUCE_SIZE

        if new_level > self._protection_level:
            logger.warning(f"Protection level increased: {self._protection_level} -> {new_level}")
            self._protection_level = new_level

        return self._protection_level

    def apply_protection_action(self, action: ProtectionLevel) -> list[str]:
        """
        Apply protection action and return actions taken.
        
        Translates protection level to concrete actions. Called
        internally when protection level changes.
        
        Args:
            action: ProtectionLevel to apply.
        
        Returns:
            List of action descriptions taken.
        
        Action Mapping:
            REDUCE_SIZE: Reduce position sizes by 50%
            RESTRICT_TRADING: Restrict orders, block volatility
            CLOSE_ALL_HALT: Close positions, halt system
        """
        actions = []

        if action == ProtectionLevel.REDUCE_SIZE:
            actions.append("Reduce position sizes by 50%")

        elif action == ProtectionLevel.RESTRICT_TRADING:
            actions.append("Restrict new orders to 1 per minute")
            actions.append("Block signals during high volatility")

        elif action == ProtectionLevel.CLOSE_ALL_HALT:
            actions.append("CLOSE ALL POSITIONS IMMEDIATELY")
            actions.append("HALT TRADING")
            actions.append("Require manual restart")
            self._halted = True

        self._protection_level = action
        return actions

    def reset(self) -> None:
        """
        Reset risk engine state.
        
        Resets all counters and protection levels to initial state.
        Does NOT unhalt the system (use unhalt() for that).
        """
        self._consecutive_losses = 0
        self._protection_level = ProtectionLevel.NONE
        self._halted = False
        logger.info("Risk engine reset")

    def unhalt(self) -> bool:
        """
        Unhalt system (manual restart required).
        
        Called after manual review to restart trading after a halt.
        Resets all protection state and loss counters.
        
        Returns:
            True (system is now unhalted).
        
        Warning:
            This should only be called after manual review of why
            the halt was triggered and appropriate corrective action.
        """
        if not self._halted:
            return True

        logger.warning("Manual unhalt requested")
        self._halted = False
        self._protection_level = ProtectionLevel.NONE
        self._consecutive_losses = 0
        return True
