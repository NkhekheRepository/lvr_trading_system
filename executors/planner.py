"""
Execution Planner - Converts Allocation to Execution Intent

This module handles the execution planning phase:
1. Queue modeling: estimate position in queue
2. Order splitting: determine if order should be split
3. Urgency calculation: how quickly must order execute
4. Slippage limits: maximum acceptable slippage

KEY FORMULAS:
    queue_position = size / liquidity
    urgency = f(time_horizon, market_conditions)
    split_threshold = liquidity × participation_rate_max

Author: LVR Trading System
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class UrgencyLevel(Enum):
    """Execution urgency levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class SplitConfig:
    """Configuration for order splitting."""
    max_order_size: float = 10.0
    max_participation_rate: float = 0.1
    min_split_size: float = 0.1
    time_horizon_seconds: float = 60.0
    
    @property
    def participation_factor(self) -> float:
        """Max participation rate for queue modeling."""
        return self.max_participation_rate


@dataclass
class ExecutionIntent:
    """
    Execution intent for an order.
    
    Contains all parameters needed to execute an order:
    - symbol, side, size
    - urgency level and time constraint
    - slippage limits
    - split plan (if applicable)
    """
    symbol: str
    side: str  # "buy" or "sell"
    total_size: float
    
    urgency: UrgencyLevel
    time_horizon: float  # seconds
    
    slippage_limit: float  # max acceptable slippage
    slippage_limit_bps: float  # in basis points
    
    queue_position: float  # estimated position in queue
    estimated_fill_time: float  # seconds
    
    splits: list[ExecutionIntent]  # child intents if split
    
    max_price: Optional[float] = None  # for buy orders
    min_price: Optional[float] = None  # for sell orders
    
    is_split: bool = False
    
    def get_child_count(self) -> int:
        """Get number of child orders."""
        return len(self.splits) if self.is_split else 1
    
    def get_split_sizes(self) -> list[float]:
        """Get sizes of split orders."""
        if self.is_split:
            return [s.total_size for s in self.splits]
        return [self.total_size]


class ExecutionPlanner:
    """
    Plans order execution with queue modeling and splitting.
    
    RESPONSIBILITIES:
    1. Estimate queue position
    2. Determine if order should be split
    3. Calculate urgency based on conditions
    4. Set slippage limits
    
    USAGE:
        >>> planner = ExecutionPlanner(config=SplitConfig())
        >>> 
        >>> intent = planner.plan(
        ...     symbol="BTCUSDT",
        ...     side="buy",
        ...     size=5.0,
        ...     price=50000,
        ...     market_depth=100.0,
        ...     volatility=0.001,
        ...     available_liquidity=50.0,
        ... )
        >>> 
        >>> if intent.is_split:
        ...     for child in intent.splits:
        ...         router.route(child)
    
    QUEUE MODELING:
        queue_position = size / (market_depth × participation_rate)
        
        Higher queue_position = longer wait = lower urgency
    
    SPLITTING LOGIC:
        If size > max_order_size OR participation > max_rate:
            Split into multiple orders
    """
    
    def __init__(self, config: Optional[SplitConfig] = None):
        """
        Initialize execution planner.
        
        Args:
            config: Split configuration
        """
        self.config = config or SplitConfig()
    
    def plan(
        self,
        symbol: str,
        side: str,
        size: float,
        price: float,
        market_depth: float,
        volatility: float,
        available_liquidity: Optional[float] = None,
        time_constraint: float = 60.0,
        spread: float = 0.0,
    ) -> ExecutionIntent:
        """
        Create execution intent for an order.
        
        Args:
            symbol: Trading symbol
            side: "buy" or "sell"
            size: Order size
            price: Current price
            market_depth: Available depth at best levels
            volatility: Current volatility
            available_liquidity: Optional liquidity override
            time_constraint: Maximum execution time (seconds)
            spread: Current bid-ask spread
        
        Returns:
            ExecutionIntent with full execution plan
        """
        liquidity = available_liquidity or market_depth
        
        if liquidity <= 0:
            logger.warning(f"Zero liquidity for {symbol}, using default")
            liquidity = size * 2
        
        queue_position = self._calculate_queue_position(
            size=size,
            market_depth=liquidity,
            participation_rate=self.config.participation_factor
        )
        
        urgency = self._calculate_urgency(
            queue_position=queue_position,
            time_constraint=time_constraint,
            volatility=volatility,
        )
        
        slippage_limit, slippage_bps = self._calculate_slippage_limit(
            price=price,
            volatility=volatility,
            urgency=urgency,
            spread=spread,
        )
        
        estimated_fill_time = self._estimate_fill_time(
            queue_position=queue_position,
            urgency=urgency,
        )
        
        should_split = self._should_split_order(
            size=size,
            market_depth=liquidity,
            participation_rate=self._estimate_participation(size, liquidity),
        )
        
        if should_split:
            splits = self._create_splits(
                symbol=symbol,
                side=side,
                size=size,
                price=price,
                market_depth=liquidity,
                volatility=volatility,
                time_constraint=time_constraint,
                spread=spread,
            )
            
            return ExecutionIntent(
                symbol=symbol,
                side=side,
                total_size=size,
                urgency=urgency,
                time_horizon=time_constraint,
                slippage_limit=slippage_limit,
                slippage_limit_bps=slippage_bps,
                queue_position=queue_position,
                estimated_fill_time=estimated_fill_time,
                splits=splits,
                is_split=True,
            )
        
        return ExecutionIntent(
            symbol=symbol,
            side=side,
            total_size=size,
            urgency=urgency,
            time_horizon=time_constraint,
            slippage_limit=slippage_limit,
            slippage_limit_bps=slippage_bps,
            queue_position=queue_position,
            estimated_fill_time=estimated_fill_time,
            splits=[],
            is_split=False,
        )
    
    def _calculate_queue_position(
        self,
        size: float,
        market_depth: float,
        participation_rate: float,
    ) -> float:
        """
        Calculate estimated position in queue.
        
        Formula:
            queue_position = size / (market_depth × participation_rate)
            
        Example:
            size = 10, market_depth = 100, participation = 0.1
            queue_position = 10 / (100 × 0.1) = 1.0
        """
        effective_depth = market_depth * participation_rate
        
        if effective_depth <= 0:
            return 10.0
        
        return size / effective_depth
    
    def _estimate_participation(self, size: float, market_depth: float) -> float:
        """Estimate participation rate for order."""
        if market_depth <= 0:
            return 1.0
        
        participation = size / market_depth
        return min(participation, 1.0)
    
    def _calculate_urgency(
        self,
        queue_position: float,
        time_constraint: float,
        volatility: float,
    ) -> UrgencyLevel:
        """
        Calculate execution urgency.
        
        Urgency increases with:
        - Higher queue position
        - Shorter time constraint
        - Higher volatility
        """
        urgency_score = queue_position * (60.0 / max(time_constraint, 1.0))
        urgency_score *= (1 + volatility * 100)
        
        if urgency_score < 0.5:
            return UrgencyLevel.LOW
        elif urgency_score < 1.0:
            return UrgencyLevel.MEDIUM
        elif urgency_score < 2.0:
            return UrgencyLevel.HIGH
        else:
            return UrgencyLevel.CRITICAL
    
    def _calculate_slippage_limit(
        self,
        price: float,
        volatility: float,
        urgency: UrgencyLevel,
        spread: float,
    ) -> tuple[float, float]:
        """
        Calculate maximum acceptable slippage.
        
        Slippage increases with urgency and volatility.
        
        Returns:
            Tuple of (slippage_value, slippage_bps)
        """
        base_slippage = spread / 2
        
        urgency_multiplier = {
            UrgencyLevel.LOW: 1.0,
            UrgencyLevel.MEDIUM: 1.5,
            UrgencyLevel.HIGH: 2.0,
            UrgencyLevel.CRITICAL: 3.0,
        }[urgency]
        
        vol_slippage = price * volatility * urgency_multiplier
        
        slippage_value = base_slippage + vol_slippage
        slippage_bps = (slippage_value / price) * 10000
        
        return slippage_value, slippage_bps
    
    def _estimate_fill_time(
        self,
        queue_position: float,
        urgency: UrgencyLevel,
    ) -> float:
        """
        Estimate time to fill.
        
        More urgent orders get filled faster but with more slippage.
        """
        base_time = queue_position * 10.0
        
        urgency_multiplier = {
            UrgencyLevel.LOW: 2.0,
            UrgencyLevel.MEDIUM: 1.5,
            UrgencyLevel.HIGH: 1.0,
            UrgencyLevel.CRITICAL: 0.5,
        }[urgency]
        
        return base_time * urgency_multiplier
    
    def _should_split_order(
        self,
        size: float,
        market_depth: float,
        participation_rate: float,
    ) -> bool:
        """
        Determine if order should be split.
        
        Split if:
        - Size > max_order_size
        - Participation > max_participation_rate
        """
        if size > self.config.max_order_size:
            return True
        
        participation = self._estimate_participation(size, market_depth)
        if participation > self.config.max_participation_rate:
            return True
        
        return False
    
    def _create_splits(
        self,
        symbol: str,
        side: str,
        size: float,
        price: float,
        market_depth: float,
        volatility: float,
        time_constraint: float,
        spread: float,
    ) -> list[ExecutionIntent]:
        """
        Create split execution intents.
        
        Splits order into smaller pieces that stay within limits.
        """
        max_size = min(
            self.config.max_order_size,
            market_depth * self.config.max_participation_rate
        )
        
        max_size = max(max_size, self.config.min_split_size)
        
        num_splits = math.ceil(size / max_size)
        split_size = size / num_splits
        
        splits = []
        for i in range(num_splits):
            split_intent = self.plan(
                symbol=symbol,
                side=side,
                size=split_size,
                price=price,
                market_depth=market_depth,
                volatility=volatility,
                available_liquidity=market_depth / num_splits,
                time_constraint=time_constraint / num_splits,
                spread=spread,
            )
            splits.append(split_intent)
        
        logger.info(f"Split {symbol} order: {size} → {num_splits} × {split_size:.4f}")
        return splits
    
    def estimate_market_impact(
        self,
        size: float,
        price: float,
        market_depth: float,
    ) -> tuple[float, float]:
        """
        Estimate market impact from order.
        
        Returns:
            Tuple of (temporary_impact, permanent_impact)
        """
        participation = size / market_depth if market_depth > 0 else 1.0
        
        temporary = price * participation * 0.5
        permanent = price * participation ** 2 * 0.1
        
        return temporary, permanent
