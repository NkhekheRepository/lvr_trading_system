"""
Capital Efficiency Engine - Optimizes capital allocation across strategies.

Maximizes return on capital while respecting risk constraints.
"""

import logging
from typing import Optional
from dataclasses import asdict
from datetime import datetime

from core.bus import EventBus
from core.state import DistributedState

logger = logging.getLogger(__name__)


class CapitalEfficiencyEngine:
    """
    Optimizes capital allocation for maximum efficiency.
    
    Metrics:
    - Capital utilization rate
    - Risk-adjusted returns
    - Position concentration
    - Rebalancing triggers
    """
    
    MIN_CAPITAL_UTILIZATION = 0.3
    MAX_CONCENTRATION = 0.3
    REBALANCE_THRESHOLD = 0.15
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        initial_capital: float = 100000.0,
    ):
        self.bus = bus
        self.state = state
        self.initial_capital = initial_capital
        self._allocations: dict[str, dict] = {}
        self._target_allocations: dict[str, float] = {}
        
    async def calculate_optimal_allocation(
        self,
        strategies: list[dict],
        portfolio_value: float,
        risk_budget: float = 0.02
    ) -> dict[str, float]:
        """
        Calculate optimal capital allocation across strategies.
        
        Args:
            strategies: List of strategy info with expected_return, risk, correlation
            portfolio_value: Total portfolio value
            risk_budget: Maximum risk as fraction of portfolio
            
        Returns:
            Dict of symbol -> allocation fraction
        """
        if not strategies:
            return {}
        
        total_expected_return = 0
        total_risk = 0
        weights = {}
        
        for strategy in strategies:
            symbol = strategy['symbol']
            expected_return = strategy.get('expected_return', 0.01)
            risk = strategy.get('risk', 0.1)
            edge_confidence = strategy.get('edge_confidence', 0.5)
            
            score = (expected_return / risk) * edge_confidence
            
            weights[symbol] = max(0.01, score)
        
        total_weight = sum(weights.values())
        if total_weight == 0:
            return {}
        
        allocations = {
            symbol: min(self.MAX_CONCENTRATION, weight / total_weight * 1.5)
            for symbol, weight in weights.items()
        }
        
        total_allocated = sum(allocations.values())
        if total_allocated > 1.0:
            scale = 1.0 / total_allocated
            allocations = {k: v * scale for k, v in allocations.items()}
        
        self._target_allocations = allocations
        
        for symbol, allocation in allocations.items():
            await self._update_allocation_state(symbol, allocation)
        
        return allocations
    
    async def calculate_position_size(
        self,
        symbol: str,
        portfolio_value: float,
        edge_estimate: dict,
        regime: dict
    ) -> float:
        """
        Calculate optimal position size for a symbol.
        
        Considers:
        - Available capital
        - Edge confidence
        - Risk constraints
        - Regime adjustments
        """
        target_allocation = self._target_allocations.get(symbol, 0.1)
        
        base_value = portfolio_value * target_allocation
        
        confidence = edge_estimate.get('confidence', 0.5)
        confidence_factor = 0.5 + confidence * 0.5
        
        regime_scale = regime.get('max_position_scale', 1.0)
        
        risk_score = regime.get('risk_score', 0)
        risk_factor = 1.0 - risk_score * 0.5
        
        position_value = base_value * confidence_factor * regime_scale * risk_factor
        
        price = edge_estimate.get('price', 1.0)
        if price <= 0:
            price = 1.0
        
        position_size = position_value / price
        
        return position_size
    
    async def check_allocation_valid(
        self,
        symbol: str,
        current_position_value: float,
        portfolio_value: float
    ) -> tuple[bool, str]:
        """
        Check if allocation is within valid bounds.
        """
        if portfolio_value <= 0:
            return False, "invalid_portfolio_value"
        
        concentration = current_position_value / portfolio_value
        
        if concentration > self.MAX_CONCENTRATION:
            return False, f"concentration_exceeded_{concentration:.2%}"
        
        utilization = self._calculate_utilization(portfolio_value)
        if utilization < self.MIN_CAPITAL_UTILIZATION:
            return False, f"utilization_too_low_{utilization:.2%}"
        
        return True, "valid"
    
    def _calculate_utilization(self, portfolio_value: float) -> float:
        """Calculate capital utilization rate."""
        total_allocated = sum(a.get('current', 0) for a in self._allocations.values())
        return total_allocated / portfolio_value if portfolio_value > 0 else 0
    
    async def rebalance_if_needed(
        self,
        symbol: str,
        current_allocation: float,
        target_allocation: float
    ) -> tuple[bool, float]:
        """
        Determine if rebalancing is needed.
        
        Returns:
            (should_rebalance, delta)
        """
        delta = target_allocation - current_allocation
        
        if abs(delta) > self.REBALANCE_THRESHOLD:
            return True, delta
        
        return False, 0.0
    
    async def _update_allocation_state(
        self,
        symbol: str,
        allocation: float
    ) -> None:
        if not self.state:
            return
            
        state_key = f"allocation:{symbol}"
        await self.state.set(
            key=state_key,
            value={
                'target': allocation,
                'current': self._allocations.get(symbol, {}).get('current', 0),
                'updated_at': int(datetime.now().timestamp() * 1000),
            },
            trace_id="capital_efficiency_engine",
        )
        
        self._allocations[symbol] = {
            'target': allocation,
            'current': self._allocations.get(symbol, {}).get('current', allocation),
        }
    
    async def get_allocation_report(self) -> dict:
        """Get current allocation report."""
        return {
            'allocations': self._allocations.copy(),
            'target_allocations': self._target_allocations.copy(),
            'total_allocated': sum(a.get('current', 0) for a in self._allocations.values()),
        }
