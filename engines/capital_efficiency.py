"""
Capital Efficiency Engine - Optimizes capital allocation across strategies.

Maximizes return on capital while respecting risk constraints.
"""

import logging
import numpy as np
from typing import Optional
from dataclasses import asdict
from datetime import datetime
from collections import deque

from core.bus import EventBus
from core.state import DistributedState

logger = logging.getLogger(__name__)


class AdaptiveDrawdownRisk:
    """
    Adaptive hybrid drawdown metric.
    
    Combines:
    - Current drawdown (real-time protection)
    - Predicted drawdown via Monte Carlo / historical simulation
    - EMA smoothing for stability
    - Regime adjustment for volatility
    """
    
    def __init__(
        self,
        alpha: float = 0.2,
        min_confidence: float = 0.7,
        max_confidence: float = 1.3,
    ):
        self.alpha = alpha
        self.min_confidence = min_confidence
        self.max_confidence = max_confidence
        self.confidence_factor = 1.0
        self._ema_value: Optional[float] = None
        self._drawdown_history = deque(maxlen=100)
        self._prediction_errors = deque(maxlen=50)
    
    def calculate(
        self,
        current_drawdown: float,
        predicted_drawdown: float,
        volatility_regime: float = 1.0,
    ) -> float:
        """
        Calculate adaptive drawdown risk metric.
        
        Args:
            current_drawdown: Real-time equity drawdown (0-1)
            predicted_drawdown: Simulated max drawdown (0-1)
            volatility_regime: >1 for high vol regimes (1.0 normal)
        
        Returns:
            Adaptive drawdown risk value
        """
        adj_predicted = predicted_drawdown * volatility_regime
        
        base_risk = max(current_drawdown, adj_predicted * self.confidence_factor)
        
        if self._ema_value is None:
            self._ema_value = base_risk
        self._ema_value = self.alpha * base_risk + (1 - self.alpha) * self._ema_value
        
        self._drawdown_history.append(current_drawdown)
        self._update_confidence(current_drawdown, predicted_drawdown)
        
        return self._ema_value
    
    def _update_confidence(self, actual: float, predicted: float) -> None:
        """Update confidence factor based on prediction accuracy."""
        if predicted < 1e-10:
            return
        error = abs(actual - predicted) / (predicted + 1e-10)
        self._prediction_errors.append(error)
        
        avg_error = sum(self._prediction_errors) / len(self._prediction_errors)
        self.confidence_factor = max(
            self.min_confidence,
            min(self.max_confidence, 1.0 - avg_error * 0.5)
        )
    
    def get_confidence(self) -> float:
        """Get current prediction confidence factor."""
        return self.confidence_factor


class MonteCarloSimulator:
    """Monte Carlo simulation for drawdown prediction."""
    
    def __init__(self, n_simulations: int = 1000, horizon: int = 252):
        self.n_simulations = n_simulations
        self.horizon = horizon
    
    def simulate_max_drawdown(
        self,
        returns: list[float],
        initial_capital: float = 100000.0,
    ) -> float:
        """
        Run Monte Carlo simulation to predict max drawdown.
        
        Returns:
            Predicted maximum drawdown as fraction (0-1)
        """
        if len(returns) < 10:
            return 0.0
        
        mean_return = np.mean(returns)
        std_return = np.std(returns)
        
        max_drawdowns = []
        for _ in range(self.n_simulations):
            portfolio_values = [initial_capital]
            peak = initial_capital
            
            for _ in range(self.horizon):
                daily_return = np.random.normal(mean_return, std_return)
                new_value = portfolio_values[-1] * (1 + daily_return)
                portfolio_values.append(new_value)
                peak = max(peak, new_value)
            
            sim_max_dd = max(
                (peak - v) / peak
                for v in portfolio_values
            )
            max_drawdowns.append(sim_max_dd)
        
        return np.percentile(max_drawdowns, 95)
    
    def estimate_max_drawdown_historical(
        self,
        returns: list[float],
        window: int = 252,
    ) -> float:
        """
        Estimate max drawdown from historical returns.
        
        Returns:
            Historical max drawdown as fraction (0-1)
        """
        if len(returns) < 2:
            return 0.0
        
        window_returns = returns[-window:] if len(returns) > window else returns
        cumulative = [1.0]
        
        for r in window_returns:
            cumulative.append(cumulative[-1] * (1 + r))
        
        peak = cumulative[0]
        max_dd = 0.0
        
        for value in cumulative:
            peak = max(peak, value)
            dd = (peak - value) / peak
            max_dd = max(max_dd, dd)
        
        return max_dd


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
        self._drawdown_risk = AdaptiveDrawdownRisk()
        self._mc_simulator = MonteCarloSimulator()
        self._strategy_returns: dict[str, list[float]] = {}
        self._volatility_regime = 1.0
        
    async def calculate_optimal_allocation(
        self,
        strategies: list[dict],
        portfolio_value: float,
        risk_budget: float = 0.02
    ) -> dict[str, float]:
        """
        Calculate optimal capital allocation across strategies.
        
        Uses adaptive hybrid drawdown metric:
        - Current drawdown (real-time)
        - Predicted drawdown (Monte Carlo)
        - EMA smoothing
        
        Weight formula: edge_truth_score / drawdown_risk
        
        Args:
            strategies: List of strategy info with expected_return, risk, edge_truth_score
            portfolio_value: Total portfolio value
            risk_budget: Maximum risk as fraction of portfolio
            
        Returns:
            Dict of symbol -> allocation fraction
        """
        if not strategies:
            return {}
        
        current_drawdown = self._get_portfolio_drawdown()
        predicted_drawdown = self._get_predicted_drawdown()
        
        drawdown_risk = self._drawdown_risk.calculate(
            current_drawdown=current_drawdown,
            predicted_drawdown=predicted_drawdown,
            volatility_regime=self._volatility_regime,
        )
        
        weights = {}
        
        for strategy in strategies:
            symbol = strategy['symbol']
            edge_truth_score = strategy.get('edge_truth_score', 0.5)
            
            score = edge_truth_score / max(drawdown_risk, 0.01)
            
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
    
    def _get_portfolio_drawdown(self) -> float:
        """Get current portfolio drawdown from state."""
        if not self.state:
            return 0.0
        try:
            portfolio = self.state.get("portfolio:global")
            if portfolio and portfolio.value:
                return portfolio.value.get('drawdown_pct', 0.0)
        except Exception:
            pass
        return 0.0
    
    def _get_predicted_drawdown(self) -> float:
        """Get predicted max drawdown via Monte Carlo."""
        all_returns = []
        for returns in self._strategy_returns.values():
            all_returns.extend(returns)
        
        if len(all_returns) < 10:
            return 0.1
        
        return self._mc_simulator.simulate_max_drawdown(all_returns, self.initial_capital)
    
    def update_strategy_returns(self, symbol: str, returns: list[float]) -> None:
        """Update returns history for Monte Carlo simulation."""
        if symbol not in self._strategy_returns:
            self._strategy_returns[symbol] = []
        self._strategy_returns[symbol].extend(returns)
        if len(self._strategy_returns[symbol]) > 252:
            self._strategy_returns[symbol] = self._strategy_returns[symbol][-252:]
    
    def set_volatility_regime(self, regime: float) -> None:
        """Set volatility regime multiplier (>1 for high volatility)."""
        self._volatility_regime = max(0.5, min(2.0, regime))
    
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
