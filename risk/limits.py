"""
Risk limits and breach detection.
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
    """Risk limit configuration."""
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
    
    Hard limits trigger immediate action.
    Soft limits trigger warnings and size reduction.
    """

    def __init__(self, limits: RiskLimits = None):
        self.limits = limits or RiskLimits()

        self._consecutive_losses = 0
        self._protection_level = ProtectionLevel.NONE
        self._halted = False

    @property
    def protection_level(self) -> ProtectionLevel:
        return self._protection_level

    @property
    def is_halted(self) -> bool:
        return self._halted

    def check_order(
        self,
        order: OrderRequest,
        signal: Signal,
        portfolio: Portfolio,
        risk_state: RiskState
    ) -> RiskCheckResult:
        """Check if order passes risk limits."""
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
        """Update risk state from portfolio."""
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
        """Record trade result for loss tracking."""
        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        logger.info(f"Trade result: PnL={pnl:.2f}, consecutive losses={self._consecutive_losses}")

    def evaluate_protection_level(self, portfolio: Portfolio) -> ProtectionLevel:
        """Evaluate required protection level based on conditions."""
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
        """Apply protection action and return actions taken."""
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
        """Reset risk engine state."""
        self._consecutive_losses = 0
        self._protection_level = ProtectionLevel.NONE
        self._halted = False
        logger.info("Risk engine reset")

    def unhalt(self) -> bool:
        """Unhalt system (manual restart required)."""
        if not self._halted:
            return True

        logger.warning("Manual unhalt requested")
        self._halted = False
        self._protection_level = ProtectionLevel.NONE
        self._consecutive_losses = 0
        return True
