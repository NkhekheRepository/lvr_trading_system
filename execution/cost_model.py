"""
Full cost model for execution quality estimation.
"""

import numpy as np

EPS = 1e-10


class CostModel:
    """
    Complete execution cost model.
    
    total_cost = spread + slippage + fees + latency_penalty
    """

    def __init__(
        self,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0004,
        slippage_alpha: float = 0.5,
        latency_coefficient: float = 0.000001
    ):
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.slippage_alpha = slippage_alpha
        self.latency_coefficient = latency_coefficient

    def calculate_total_cost(
        self,
        quantity: float,
        price: float,
        side: str,
        spread: float,
        market_depth: float,
        latency_ms: float = 0
    ) -> dict:
        """
        Calculate all execution costs.
        
        Returns breakdown of costs.
        """
        notional = quantity * price

        spread_cost = self._calculate_spread_cost(quantity, spread)
        slippage_cost = self._calculate_slippage(quantity, market_depth)
        fee_cost = self._calculate_fee(notional, side == "buy")
        latency_cost = self._calculate_latency_cost(latency_ms, notional)

        total = spread_cost + slippage_cost + fee_cost + latency_cost
        total_bps = (total / notional * 10000) if notional > 0 else 0

        return {
            "spread_cost": spread_cost,
            "slippage_cost": slippage_cost,
            "fee_cost": fee_cost,
            "latency_cost": latency_cost,
            "total_cost": total,
            "total_cost_bps": total_bps,
            "notional": notional
        }

    def _calculate_spread_cost(self, quantity: float, spread: float) -> float:
        """Half-spread cost."""
        return quantity * spread / 2

    def _calculate_slippage(
        self,
        quantity: float,
        market_depth: float
    ) -> float:
        """Slippage based on order size relative to depth."""
        if market_depth <= EPS:
            return 0.0
        depth_ratio = min(quantity / market_depth, 1.0)
        slippage_bps = self.slippage_alpha * depth_ratio * 10
        return quantity * price * slippage_bps / 10000

    def _calculate_fee(self, notional: float, is_taker: bool) -> float:
        """Exchange fee."""
        fee_rate = self.taker_fee if is_taker else self.maker_fee
        return notional * fee_rate

    def _calculate_latency_cost(
        self,
        latency_ms: float,
        notional: float
    ) -> float:
        """Latency impact cost."""
        return latency_ms * notional * self.latency_coefficient

    def estimate_net_cost(
        self,
        quantity: float,
        price: float,
        side: str,
        spread: float,
        market_depth: float,
        expected_move_bps: float = 0
    ) -> dict:
        """
        Estimate net cost including expected price movement.
        """
        costs = self.calculate_total_cost(
            quantity, price, side, spread, market_depth
        )

        expected_move = quantity * price * expected_move_bps / 10000
        costs["expected_move"] = expected_move
        costs["net_cost"] = costs["total_cost"] - expected_move

        return costs


def estimate_realistic_slippage(
    order_size: float,
    market_depth: float,
    base_spread: float,
    alpha: float = 0.5
) -> float:
    """
    Quick slippage estimation.
    
    slippage = alpha * (size / depth) * (spread / 2 + 1)
    """
    if market_depth <= EPS:
        return base_spread / 2

    depth_ratio = min(order_size / market_depth, 1.0)
    slippage = alpha * depth_ratio * (base_spread / 2 + 1)
    return slippage
