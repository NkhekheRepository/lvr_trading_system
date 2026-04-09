"""
Cost-Aware Edge Calculation

This module computes the NET edge after accounting for all trading costs:
- Fees: Exchange fees (maker/taker)
- Slippage: Market impact from order size
- Impact: Permanent price impact from trades

Formula:
    net_edge = raw_edge - (fees + slippage + impact)
    
    where:
    - raw_edge: Expected return from signal
    - fees: transaction costs (fixed per trade)
    - slippage: temporary price impact (size-dependent)
    - impact: permanent price impact (size-dependent)

RULE: If net_edge <= 0 → DO NOT TRADE

Author: LVR Trading System
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class CostComponents:
    """Breakdown of trading costs."""
    fee: float = 0.0
    slippage: float = 0.0
    impact: float = 0.0
    total_cost: float = 0.0
    
    @property
    def cost_bps(self) -> float:
        """Total cost in basis points."""
        return self.total_cost * 10000
    
    def is_positive_edge(self, raw_edge: float) -> bool:
        """Check if net edge is positive."""
        return raw_edge > self.total_cost


class CostAwareEdge:
    """
    Computes cost-aware net edge for trading signals.
    
    ACCURATE COST MODELING IS CRITICAL:
    - Underestimate costs → False signals with negative edge
    - Overestimate costs → Miss valid signals
    
    Cost Components:
    1. Fees: Fixed per trade (e.g., 0.04% taker, -0.02% maker rebate)
    2. Slippage: Size × liquidity factor × volatility
    3. Impact: Size² × market_depth factor
    
    Formula:
        fees = order_value × fee_rate
        
        slippage = order_value × (size / market_depth) × vol_factor
        
        impact = order_value × (size / market_depth)² × permanent_factor
        
        total_cost = fees + slippage + impact
        
        net_edge = raw_edge - total_cost
    
    Usage:
        >>> cost_calc = CostAwareEdge(
        ...     fee_rate=0.0004,      # 4 bps taker fee
        ...     maker_rebate=0.0002,  # 2 bps maker rebate
        ...     vol_factor=0.5,
        ...     permanent_impact_factor=0.1
        ... )
        >>> 
        >>> costs = cost_calc.calculate_costs(
        ...     size=1.0,
        ...     price=50000,
        ...     market_depth=100,
        ...     volatility=0.001
        ... )
        >>> 
        >>> net = cost_calc.compute_net_edge(raw_edge=0.001, costs=costs)
        >>> print(f"Net edge: {net*100:.3f}%")
    """
    
    def __init__(
        self,
        fee_rate: float = 0.0004,
        maker_rebate: float = 0.0002,
        vol_factor: float = 0.5,
        permanent_impact_factor: float = 0.1,
        liquidity_decay: float = 0.001,
    ):
        """
        Initialize cost calculator.
        
        Args:
            fee_rate: Taker fee rate (4 bps = 0.0004)
            maker_rebate: Maker rebate rate (2 bps = 0.0002)
            vol_factor: Volatility multiplier for slippage
            permanent_impact_factor: Permanent impact coefficient
            liquidity_decay: Depth decay per unit of trade
        """
        self.fee_rate = fee_rate
        self.maker_rebate = maker_rebate
        self.vol_factor = vol_factor
        self.permanent_impact_factor = permanent_impact_factor
        self.liquidity_decay = liquidity_decay
    
    def calculate_costs(
        self,
        size: float,
        price: float,
        market_depth: float,
        volatility: float,
        is_maker: bool = False,
    ) -> CostComponents:
        """
        Calculate all cost components for a potential trade.
        
        Args:
            size: Order size (quantity)
            price: Current price
            market_depth: Available liquidity at best levels
            volatility: Current volatility (std of returns)
            is_maker: Whether order will be a maker (vs taker)
            
        Returns:
            CostComponents with fee, slippage, impact breakdown
        """
        order_value = size * price
        
        if order_value <= 0 or market_depth <= 0:
            return CostComponents()
        
        fee = self._calculate_fee(order_value, is_maker)
        slippage = self._calculate_slippage(
            size=size,
            price=price,
            market_depth=market_depth,
            volatility=volatility
        )
        impact = self._calculate_impact(
            size=size,
            price=price,
            market_depth=market_depth
        )
        
        total_cost = fee + slippage + impact
        
        return CostComponents(
            fee=fee,
            slippage=slippage,
            impact=impact,
            total_cost=total_cost
        )
    
    def _calculate_fee(self, order_value: float, is_maker: bool) -> float:
        """Calculate fee cost (positive) or rebate (negative)."""
        if is_maker:
            return -order_value * self.maker_rebate
        return order_value * self.fee_rate
    
    def _calculate_slippage(
        self,
        size: float,
        price: float,
        market_depth: float,
        volatility: float
    ) -> float:
        """
        Calculate temporary price impact (slippage).
        
        Slippage = price × participation_rate × vol_factor × sqrt(participation)
        
        where participation = size / market_depth
        """
        order_value = size * price
        participation = size / market_depth if market_depth > 0 else 1.0
        
        slippage_pct = (
            participation * self.vol_factor * math.sqrt(participation + 1e-10)
        )
        
        return order_value * slippage_pct
    
    def _calculate_impact(
        self,
        size: float,
        price: float,
        market_depth: float
    ) -> float:
        """
        Calculate permanent price impact.
        
        Impact = price × (size/market_depth)² × permanent_factor
        
        This is the permanent price movement caused by information leakage.
        """
        order_value = size * price
        participation = size / market_depth if market_depth > 0 else 1.0
        
        impact_pct = participation ** 2 * self.permanent_impact_factor
        
        return order_value * impact_pct
    
    def compute_net_edge(
        self,
        raw_edge: float,
        costs: CostComponents
    ) -> float:
        """
        Compute net edge after costs.
        
        Args:
            raw_edge: Expected return from signal (as decimal)
            costs: Pre-calculated cost components
            
        Returns:
            Net edge = raw_edge - costs
            
        RULE: Return 0 if net edge <= 0 (no trade)
        """
        net_edge = raw_edge - costs.total_cost
        
        if net_edge <= 0:
            return 0.0
        
        return net_edge
    
    def should_trade(
        self,
        raw_edge: float,
        size: float,
        price: float,
        market_depth: float,
        volatility: float,
        is_maker: bool = False,
        min_edge: float = 0.0001,
    ) -> tuple[bool, CostComponents, float]:
        """
        Determine if trade should be executed.
        
        Args:
            raw_edge: Expected edge from signal
            size: Order size
            price: Current price
            market_depth: Available liquidity
            volatility: Current volatility
            is_maker: Whether order will be maker
            min_edge: Minimum edge threshold (default: 1 bps)
            
        Returns:
            Tuple of (should_trade, costs, net_edge)
        """
        costs = self.calculate_costs(
            size=size,
            price=price,
            market_depth=market_depth,
            volatility=volatility,
            is_maker=is_maker
        )
        
        net_edge = self.compute_net_edge(raw_edge, costs)
        
        should_trade = net_edge >= min_edge
        
        return should_trade, costs, net_edge
    
    def estimate_breakeven_edge(
        self,
        size: float,
        price: float,
        market_depth: float,
        volatility: float,
        is_maker: bool = False,
    ) -> float:
        """
        Estimate the edge required to break even.
        
        Useful for filtering signals that cannot possibly be profitable.
        """
        costs = self.calculate_costs(
            size=size,
            price=price,
            market_depth=market_depth,
            volatility=volatility,
            is_maker=is_maker
        )
        return costs.total_cost
    
    def get_cost_estimate_bps(
        self,
        size: float,
        price: float,
        market_depth: float,
        volatility: float = 0.001,
    ) -> float:
        """
        Get cost estimate in basis points for quick filtering.
        
        Args:
            size: Order size
            price: Price
            market_depth: Depth
            volatility: Volatility (default 0.1%)
            
        Returns:
            Total cost in basis points
        """
        costs = self.calculate_costs(
            size=size,
            price=price,
            market_depth=market_depth,
            volatility=volatility
        )
        return costs.cost_bps
