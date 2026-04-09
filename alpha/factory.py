"""
Alpha Factory - Main Entry Point for Signal Generation

This module provides the main AlphaFactory class that orchestrates
the complete signal generation pipeline:

    Features → Generate → Validate → Deploy → Monitor → Kill

KEY FEATURES:
1. Cost-aware edge calculation
2. Turnover and stability filtering
3. Signal lifecycle management
4. Automatic kill on degradation

Author: LVR Trading System
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Callable

import numpy as np

from .cost_aware import CostAwareEdge, CostComponents
from .filters import SignalFilters, FilterResult, ValidationResult
from .signal_pool import AlphaSignal, SignalPool, PoolConfig, SignalState, KillReason

logger = logging.getLogger(__name__)


@dataclass
class AlphaConfig:
    """Configuration for alpha generation."""
    min_confidence: float = 0.5
    min_edge_bps: float = 1.0
    min_stable_trades: int = 10
    max_turnover: float = 0.5
    max_variance: float = 0.5
    
    fee_rate: float = 0.0004
    maker_rebate: float = 0.0002
    vol_factor: float = 0.5
    permanent_impact_factor: float = 0.1
    
    min_sharpe: float = 0.5
    max_drawdown_contribution: float = 0.3


class AlphaFactory:
    """
    Main alpha factory for signal generation and lifecycle management.
    
    PIPELINE:
    1. Generate: Create signal from features
    2. Validate: Check costs, turnover, stability
    3. Deploy: Activate validated signals
    4. Monitor: Track performance
    5. Kill: Disable underperforming signals
    
    USAGE:
        >>> factory = AlphaFactory(config=AlphaConfig())
        >>> 
        >>> # Generate and validate signal
        >>> signal = factory.generate(
        ...     symbol="BTCUSDT",
        ...     features={"ofi": 0.8, "depth_imbalance": 0.3},
        ...     raw_edge=0.015,
        ...     confidence=0.7,
        ...     market_depth=100,
        ...     volatility=0.001
        ... )
        >>> 
        >>> if signal and signal.state == SignalState.VALIDATED:
        ...     factory.deploy(signal)
    
    RULES:
    - If net_edge <= 0 → REJECT
    - If filters fail → REJECT
    - If confidence < threshold → REJECT
    - If Sharpe < threshold for N periods → KILL
    """
    
    def __init__(
        self,
        config: Optional[AlphaConfig] = None,
        pool_config: Optional[PoolConfig] = None,
    ):
        """
        Initialize alpha factory.
        
        Args:
            config: Alpha generation configuration
            pool_config: Signal pool configuration
        """
        self.config = config or AlphaConfig()
        self.pool_config = pool_config or PoolConfig(
            min_sharpe=self.config.min_sharpe,
            max_drawdown_contribution=self.config.max_drawdown_contribution,
        )
        
        self.cost_calculator = CostAwareEdge(
            fee_rate=self.config.fee_rate,
            maker_rebate=self.config.maker_rebate,
            vol_factor=self.config.vol_factor,
            permanent_impact_factor=self.config.permanent_impact_factor,
        )
        
        self.filters = SignalFilters(
            turnover_config={"max_turnover": self.config.max_turnover},
            stability_config={
                "min_stable_trades": self.config.min_stable_trades,
                "max_variance": self.config.max_variance,
            },
            min_confidence=self.config.min_confidence,
        )
        
        self.pool = SignalPool(config=self.pool_config)
        
        self._portfolio_value = 100000.0
        self._on_signal_deployed: Optional[Callable] = None
        self._on_signal_killed: Optional[Callable] = None
    
    def set_portfolio_value(self, value: float) -> None:
        """Set portfolio value for turnover calculation."""
        self._portfolio_value = value
        self.filters.turnover_filter.set_portfolio_value(value)
    
    def set_deployment_callback(self, callback: Callable[[AlphaSignal], None]) -> None:
        """Set callback for signal deployment."""
        self._on_signal_deployed = callback
    
    def set_kill_callback(self, callback: Callable[[AlphaSignal, KillReason], None]) -> None:
        """Set callback for signal kills."""
        self._on_signal_killed = callback
    
    def generate(
        self,
        symbol: str,
        features: dict,
        raw_edge: float,
        confidence: float,
        market_depth: float,
        volatility: float,
        size: float = 1.0,
        price: float = 0.0,
        is_maker: bool = False,
    ) -> Optional[AlphaSignal]:
        """
        Generate and validate signal from features.
        
        This is the main entry point for signal generation.
        
        Args:
            symbol: Trading symbol
            features: Feature dictionary
            raw_edge: Predicted edge (before costs)
            confidence: Signal confidence (0-1)
            market_depth: Available liquidity
            volatility: Current volatility
            size: Proposed trade size
            price: Current price
            is_maker: Whether order will be maker
            
        Returns:
            AlphaSignal if validated, None if rejected
        """
        if confidence < self.config.min_confidence:
            logger.debug(f"Signal rejected: confidence {confidence:.2%} < {self.config.min_confidence:.2%}")
            return None
        
        costs = self.cost_calculator.calculate_costs(
            size=size,
            price=price,
            market_depth=market_depth,
            volatility=volatility,
            is_maker=is_maker,
        )
        
        net_edge = self.cost_calculator.compute_net_edge(raw_edge, costs)
        
        min_edge = self.config.min_edge_bps / 10000
        if net_edge < min_edge:
            logger.debug(f"Signal rejected: net_edge {net_edge:.4%} < min {min_edge:.4%}")
            return None
        
        validation = self.filters.validate(
            symbol=symbol,
            size=size,
            price=price,
            confidence=confidence,
        )
        
        if not validation.all_passed:
            logger.debug(f"Signal rejected: {'; '.join(validation.failed_reasons)}")
            return None
        
        signal = self.pool.create_signal(
            symbol=symbol,
            features=features,
            raw_edge=raw_edge,
            confidence=confidence,
            cost_aware_edge=net_edge,
        )
        
        if self.pool.validate_signal(signal, net_edge, validation):
            logger.info(
                f"Signal {signal.signal_id} validated for {symbol}: "
                f"edge={net_edge:.4%}, confidence={confidence:.2%}"
            )
            return signal
        
        return None
    
    def deploy(self, signal: AlphaSignal) -> bool:
        """
        Deploy validated signal for trading.
        
        Args:
            signal: Validated signal to deploy
            
        Returns:
            True if deployed successfully
        """
        try:
            self.pool.deploy_signal(signal)
            
            if self._on_signal_deployed:
                self._on_signal_deployed(signal)
            
            return True
        except ValueError as e:
            logger.error(f"Failed to deploy signal: {e}")
            return False
    
    def record_trade(
        self,
        signal: AlphaSignal,
        pnl: float,
        prediction_error: float,
    ) -> None:
        """
        Record trade outcome for signal.
        
        Args:
            signal: Signal that generated trade
            pnl: Realized PnL
            prediction_error: Prediction error
        """
        signal.record_trade(pnl, prediction_error)
        
        self.pool.evaluate_kill_conditions()
        
        for s, reason in self.pool.evaluate_kill_conditions():
            if self._on_signal_killed:
                self._on_signal_killed(s, reason)
    
    def should_trade(
        self,
        symbol: str,
        raw_edge: float,
        confidence: float,
        market_depth: float,
        volatility: float,
        size: float = 1.0,
        price: float = 0.0,
    ) -> tuple[bool, Optional[AlphaSignal], CostComponents]:
        """
        Quick check if trade should be executed.
        
        Convenience method that combines generate and deploy checks.
        
        Args:
            symbol: Trading symbol
            raw_edge: Predicted edge
            confidence: Signal confidence
            market_depth: Available liquidity
            volatility: Current volatility
            size: Proposed trade size
            price: Current price
            
        Returns:
            Tuple of (should_trade, signal_or_none, costs)
        """
        signal = self.generate(
            symbol=symbol,
            features={},
            raw_edge=raw_edge,
            confidence=confidence,
            market_depth=market_depth,
            volatility=volatility,
            size=size,
            price=price,
        )
        
        if signal is None:
            costs = self.cost_calculator.calculate_costs(
                size=size, price=price, market_depth=market_depth, volatility=volatility
            )
            return False, None, costs
        
        return True, signal, self.cost_calculator.calculate_costs(
            size=size, price=price, market_depth=market_depth, volatility=volatility
        )
    
    def get_active_signals(self, symbol: Optional[str] = None) -> list[AlphaSignal]:
        """Get all active signals."""
        return self.pool.get_active_signals(symbol)
    
    def get_pool_stats(self) -> dict:
        """Get pool statistics."""
        return self.pool.get_pool_stats()
    
    def evaluate_all_kills(self) -> list[tuple[AlphaSignal, KillReason]]:
        """Evaluate and apply kill conditions for all signals."""
        kills = self.pool.evaluate_kill_conditions()
        
        for signal, reason in kills:
            if self._on_signal_killed:
                self._on_signal_killed(signal, reason)
        
        return kills
