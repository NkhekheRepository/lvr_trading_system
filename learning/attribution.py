"""
PnL attribution for decomposing returns into components.
"""

import logging
from typing import Optional

from app.schemas import AttributionResult, FillEvent, Side

logger = logging.getLogger(__name__)

EPS = 1e-10


class AttributionEngine:
    """
    Attributes PnL to signal, execution, and cost components.
    """

    def __init__(self):
        self._trade_history: list[dict] = []

    def record_trade(
        self,
        fill: FillEvent,
        signal_edge: float,
        expected_price: float,
        execution_price: float,
        fee: float,
        spread_cost: float
    ) -> None:
        """Record trade for attribution."""
        self._trade_history.append({
            "timestamp": fill.timestamp,
            "symbol": fill.symbol,
            "side": fill.side,
            "quantity": fill.quantity,
            "signal_edge": signal_edge,
            "expected_price": expected_price,
            "execution_price": execution_price,
            "fee": fee,
            "spread_cost": spread_cost,
            "slippage": fill.slippage
        })

        if len(self._trade_history) > 1000:
            self._trade_history = self._trade_history[-500:]

    def attribute(
        self,
        symbol: str,
        entry_fill: FillEvent,
        exit_fill: Optional[FillEvent],
        expected_return: float = 0
    ) -> AttributionResult:
        """
        Attribute PnL to components.
        
        Returns:
            AttributionResult with breakdown
        """
        if exit_fill is None:
            exit_fill = entry_fill

        if entry_fill.side == Side.BUY:
            realized_return = (exit_fill.price - entry_fill.price) / entry_fill.price
        else:
            realized_return = (entry_fill.price - exit_fill.price) / entry_fill.price

        total_pnl = realized_return * entry_fill.quantity

        signal_edge = expected_return * entry_fill.quantity
        execution_edge = realized_return - expected_return

        slippage_cost = entry_fill.slippage
        fee_cost = entry_fill.fee
        spread_cost = slippage_cost * 0.5

        cost_impact = -(slippage_cost + fee_cost + spread_cost)

        result = AttributionResult(
            symbol=symbol,
            total_pnl=total_pnl,
            signal_edge=signal_edge,
            execution_edge=execution_edge,
            cost_impact=cost_impact,
            expected_return=expected_return,
            realized_return=realized_return,
            slippage_cost=slippage_cost,
            fee_cost=fee_cost,
            spread_cost=spread_cost
        )

        return result

    def get_summary(self) -> dict:
        """Get attribution summary."""
        if not self._trade_history:
            return {
                "total_trades": 0,
                "avg_signal_edge": 0,
                "avg_execution_edge": 0,
                "avg_cost": 0
            }

        total_signal = sum(t["signal_edge"] for t in self._trade_history)
        total_execution = sum(
            t["expected_price"] - t["execution_price"]
            for t in self._trade_history
        )
        total_cost = sum(
            t["slippage"] + t["fee"] + t["spread_cost"]
            for t in self._trade_history
        )

        return {
            "total_trades": len(self._trade_history),
            "avg_signal_edge": total_signal / len(self._trade_history),
            "avg_execution_edge": total_execution / len(self._trade_history),
            "avg_cost": total_cost / len(self._trade_history),
            "total_pnl": sum(
                t["quantity"] * (t["expected_price"] - t["execution_price"])
                for t in self._trade_history
            )
        }

    def reset(self) -> None:
        """Reset attribution history."""
        self._trade_history.clear()


class CostAttributor:
    """Detailed cost attribution."""

    def __init__(self):
        self._cost_history = []

    def record_cost(
        self,
        timestamp: int,
        symbol: str,
        spread: float,
        slippage: float,
        fee: float,
        latency_cost: float
    ) -> None:
        """Record cost breakdown."""
        self._cost_history.append({
            "timestamp": timestamp,
            "symbol": symbol,
            "spread": spread,
            "slippage": slippage,
            "fee": fee,
            "latency_cost": latency_cost,
            "total": spread + slippage + fee + latency_cost
        })

        if len(self._cost_history) > 1000:
            self._cost_history = self._cost_history[-500:]

    def get_average_costs(self) -> dict:
        """Get average costs."""
        if not self._cost_history:
            return {
                "avg_spread": 0,
                "avg_slippage": 0,
                "avg_fee": 0,
                "avg_latency": 0,
                "avg_total": 0
            }

        n = len(self._cost_history)
        return {
            "avg_spread": sum(c["spread"] for c in self._cost_history) / n,
            "avg_slippage": sum(c["slippage"] for c in self._cost_history) / n,
            "avg_fee": sum(c["fee"] for c in self._cost_history) / n,
            "avg_latency": sum(c["latency_cost"] for c in self._cost_history) / n,
            "avg_total": sum(c["total"] for c in self._cost_history) / n
        }
