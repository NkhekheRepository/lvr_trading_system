"""
Smart Order Router (SOR) - Multi-Exchange Order Routing

This module implements intelligent order routing:
1. Route scoring: score = fill_probability / total_cost
2. Multi-exchange routing
3. Order splitting
4. Predictive slippage
5. Dynamic rerouting
6. Learning from fills

ROUTE SELECTION:
    Best route = argmax(score = fill_prob / cost)

Author: LVR Trading System
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4

import numpy as np

logger = logging.getLogger(__name__)


class Exchange(Enum):
    """Supported exchanges."""
    BINANCE = "binance"
    BYBIT = "bybit"
    OKX = "okx"
    BINANCE_TESTNET = "binance_testnet"


@dataclass
class RouteScore:
    """Score components for a route."""
    total_score: float
    fill_probability: float
    expected_cost: float
    expected_slippage: float
    expected_latency: float
    
    fee: float = 0.0
    rebate: float = 0.0
    
    @property
    def cost_per_unit(self) -> float:
        """Cost per unit of score."""
        if self.total_score <= 0:
            return float('inf')
        return self.expected_cost / self.total_score
    
    @property
    def net_cost(self) -> float:
        """Net cost after rebates."""
        return self.expected_cost - self.rebate


@dataclass
class Route:
    """
    Order route specification.
    
    Represents a single execution path for an order.
    """
    route_id: str
    exchange: Exchange
    order_type: str  # "market", "limit", "post_only"
    
    symbol: str
    side: str
    size: float
    price: Optional[float] = None
    
    priority: int = 0
    is_maker: bool = False
    
    estimated_fill_prob: float = 0.95
    estimated_slippage: float = 0.0
    estimated_latency: float = 100.0  # ms
    
    fee_rate: float = 0.0004
    maker_rebate: float = 0.0002
    
    child_routes: list[Route] = field(default_factory=list)
    
    def __post_init__(self):
        if not self.route_id:
            self.route_id = str(uuid4())[:8]
    
    def score(self) -> RouteScore:
        """Calculate route score."""
        cost = self.estimated_slippage + (self.size * self.price * self.fee_rate if self.price else 0)
        rebate = self.size * self.price * self.maker_rebate if self.is_maker else 0
        
        score_value = self.estimated_fill_prob / (cost + 1e-10)
        
        return RouteScore(
            total_score=score_value,
            fill_probability=self.estimated_fill_prob,
            expected_cost=cost,
            expected_slippage=self.estimated_slippage,
            expected_latency=self.estimated_latency,
            fee=cost,
            rebate=rebate,
        )
    
    def split(self, num_parts: int) -> list[Route]:
        """Split route into multiple child routes."""
        if num_parts <= 1:
            return [self]
        
        split_size = self.size / num_parts
        splits = []
        
        for i in range(num_parts):
            child = Route(
                route_id=f"{self.route_id}-{i}",
                exchange=self.exchange,
                order_type=self.order_type,
                symbol=self.symbol,
                side=self.side,
                size=split_size,
                price=self.price,
                priority=self.priority + i,
                is_maker=self.is_maker,
                estimated_fill_prob=self.estimated_fill_prob,
                estimated_slippage=self.estimated_slippage * 0.5,
                estimated_latency=self.estimated_latency,
                fee_rate=self.fee_rate,
                maker_rebate=self.maker_rebate,
            )
            splits.append(child)
        
        self.child_routes = splits
        return splits


@dataclass
class ExchangeInfo:
    """Exchange information for routing."""
    exchange: Exchange
    available: bool = True
    latency_ms: float = 100.0
    fee_rate: float = 0.0004
    maker_rebate: float = 0.0002
    min_order_size: float = 0.001
    max_order_size: float = 100.0


class SmartOrderRouter:
    """
    Smart Order Router for multi-exchange execution.
    
    RESPONSIBILITIES:
    1. Generate candidate routes across exchanges
    2. Score routes by fill_probability / total_cost
    3. Select best route(s)
    4. Handle order splitting
    5. Learn from fill outcomes
    
    SCORING FORMULA:
        score = fill_probability / (slippage_cost + fee_cost)
        
        Higher score = better route
    
    USAGE:
        >>> router = SmartOrderRouter()
        >>> router.add_exchange(Exchange.BINANCE, latency_ms=50, fee_rate=0.0004)
        >>> router.add_exchange(Exchange.BYBIT, latency_ms=80, fee_rate=0.0005)
        >>> 
        >>> routes = router.generate_routes(
        ...     symbol="BTCUSDT",
        ...     side="buy",
        ...     size=1.0,
        ...     price=50000,
        ...     market_depth=100,
        ... )
        >>> 
        >>> best = router.select_best_route(routes)
        >>> print(f"Best route: {best.exchange.value}")
    """
    
    def __init__(
        self,
        enable_splitting: bool = True,
        max_routes: int = 5,
    ):
        """
        Initialize Smart Order Router.
        
        Args:
            enable_splitting: Enable order splitting
            max_routes: Maximum routes to consider
        """
        self.enable_splitting = enable_splitting
        self.max_routes = max_routes
        
        self._exchanges: dict[Exchange, ExchangeInfo] = {}
        self._fill_history: list[dict] = []
        
        self._add_default_exchanges()
    
    def _add_default_exchanges(self) -> None:
        """Add default exchange configurations."""
        self._exchanges[Exchange.BINANCE] = ExchangeInfo(
            exchange=Exchange.BINANCE,
            latency_ms=50,
            fee_rate=0.0004,
            maker_rebate=0.0002,
        )
        self._exchanges[Exchange.BYBIT] = ExchangeInfo(
            exchange=Exchange.BYBIT,
            latency_ms=80,
            fee_rate=0.0005,
            maker_rebate=0.0001,
        )
        self._exchanges[Exchange.OKX] = ExchangeInfo(
            exchange=Exchange.OKX,
            latency_ms=100,
            fee_rate=0.0005,
            maker_rebate=0.0001,
        )
    
    def add_exchange(
        self,
        exchange: Exchange,
        latency_ms: float = 100,
        fee_rate: float = 0.0004,
        maker_rebate: float = 0.0002,
    ) -> None:
        """Add or update exchange configuration."""
        self._exchanges[exchange] = ExchangeInfo(
            exchange=exchange,
            latency_ms=latency_ms,
            fee_rate=fee_rate,
            maker_rebate=maker_rebate,
        )
    
    def remove_exchange(self, exchange: Exchange) -> None:
        """Remove exchange from routing."""
        if exchange in self._exchanges:
            del self._exchanges[exchange]
    
    def generate_routes(
        self,
        symbol: str,
        side: str,
        size: float,
        price: float,
        market_depth: float,
        volatility: float = 0.001,
        order_type: str = "market",
        preferred_exchange: Optional[Exchange] = None,
    ) -> list[Route]:
        """
        Generate candidate routes for an order.
        
        Args:
            symbol: Trading symbol
            side: "buy" or "sell"
            size: Order size
            price: Current price
            market_depth: Available liquidity
            volatility: Current volatility
            order_type: Order type ("market", "limit", "post_only")
            preferred_exchange: Preferred exchange (if any)
            
        Returns:
            List of candidate routes sorted by score
        """
        routes = []
        
        for exchange, info in self._exchanges.items():
            if not info.available:
                continue
            
            if not self._validate_order_size(size, info):
                continue
            
            route = self._create_route(
                exchange=exchange,
                info=info,
                symbol=symbol,
                side=side,
                size=size,
                price=price,
                market_depth=market_depth,
                volatility=volatility,
                order_type=order_type,
            )
            routes.append(route)
            
            if preferred_exchange and exchange == preferred_exchange:
                route.priority = -1
            
            if order_type in ("post_only", "limit"):
                maker_route = self._create_maker_route(
                    exchange=exchange,
                    info=info,
                    symbol=symbol,
                    side=side,
                    size=size,
                    price=price,
                    market_depth=market_depth,
                )
                routes.append(maker_route)
        
        routes.sort(key=lambda r: (r.priority, -r.score().total_score))
        
        return routes[:self.max_routes]
    
    def _validate_order_size(self, size: float, info: ExchangeInfo) -> bool:
        """Validate order size against exchange limits."""
        return info.min_order_size <= size <= info.max_order_size
    
    def _create_route(
        self,
        exchange: Exchange,
        info: ExchangeInfo,
        symbol: str,
        side: str,
        size: float,
        price: float,
        market_depth: float,
        volatility: float,
        order_type: str,
    ) -> Route:
        """Create a taker route."""
        slippage = self._estimate_slippage(
            size=size,
            price=price,
            market_depth=market_depth,
            volatility=volatility,
            latency_ms=info.latency_ms,
        )
        
        participation = size / market_depth if market_depth > 0 else 1.0
        fill_prob = max(0.5, 1.0 - participation * 0.5)
        
        return Route(
            route_id="",
            exchange=exchange,
            order_type=order_type,
            symbol=symbol,
            side=side,
            size=size,
            price=price,
            is_maker=False,
            estimated_fill_prob=fill_prob,
            estimated_slippage=slippage,
            estimated_latency=info.latency_ms,
            fee_rate=info.fee_rate,
            maker_rebate=info.maker_rebate,
        )
    
    def _create_maker_route(
        self,
        exchange: Exchange,
        info: ExchangeInfo,
        symbol: str,
        side: str,
        size: float,
        price: float,
        market_depth: float,
    ) -> Route:
        """Create a maker route with limit order."""
        participation = size / market_depth if market_depth > 0 else 0.1
        
        limit_price = price * (1.0001 if side == "buy" else 0.9999)
        
        return Route(
            route_id="",
            exchange=exchange,
            order_type="post_only",
            symbol=symbol,
            side=side,
            size=size,
            price=limit_price,
            priority=1,
            is_maker=True,
            estimated_fill_prob=max(0.3, 0.8 - participation),
            estimated_slippage=0.0,
            estimated_latency=info.latency_ms * 2,
            fee_rate=info.fee_rate,
            maker_rebate=info.maker_rebate,
        )
    
    def _estimate_slippage(
        self,
        size: float,
        price: float,
        market_depth: float,
        volatility: float,
        latency_ms: float,
    ) -> float:
        """
        Estimate slippage for order.
        
        Formula:
            slippage = price × participation × vol_factor × sqrt(participation)
        """
        participation = size / market_depth if market_depth > 0 else 1.0
        
        vol_factor = 0.5
        latency_factor = 1 + latency_ms / 1000
        
        slippage = (
            price * 
            participation * 
            vol_factor * 
            np.sqrt(participation + 1e-10) *
            (1 + volatility * 100) *
            latency_factor
        )
        
        return slippage
    
    def select_best_route(self, routes: list[Route]) -> Optional[Route]:
        """
        Select best route from candidates.
        
        Args:
            routes: List of candidate routes
            
        Returns:
            Best route or None if no valid routes
        """
        if not routes:
            return None
        
        scored_routes = [(r, r.score()) for r in routes]
        scored_routes.sort(key=lambda x: -x[1].total_score)
        
        best_route, best_score = scored_routes[0]
        
        logger.debug(
            f"Selected route: {best_route.exchange.value} "
            f"(score={best_score.total_score:.2f}, "
            f"fill_prob={best_score.fill_probability:.2%})"
        )
        
        return best_route
    
    def record_fill(
        self,
        route: Route,
        actual_fill_price: float,
        actual_fill_time: float,
    ) -> None:
        """
        Record fill outcome for learning.
        
        Args:
            route: Route that was executed
            actual_fill_price: Actual fill price
            actual_fill_time: Actual fill time (ms)
        """
        self._fill_history.append({
            "route_id": route.route_id,
            "exchange": route.exchange.value,
            "symbol": route.symbol,
            "expected_price": route.price,
            "actual_price": actual_fill_price,
            "expected_time": route.estimated_latency,
            "actual_time": actual_fill_time,
            "timestamp": time.time(),
        })
        
        if len(self._fill_history) > 1000:
            self._fill_history = self._fill_history[-500:]
    
    def get_exchange_stats(self) -> dict:
        """Get exchange performance statistics."""
        stats = {}
        
        for exchange in self._exchanges:
            fills = [f for f in self._fill_history if f["exchange"] == exchange.value]
            
            if not fills:
                stats[exchange.value] = {"count": 0}
                continue
            
            slippage_errors = [
                (f["actual_price"] - f["expected_price"]) / f["expected_price"]
                for f in fills
            ]
            
            stats[exchange.value] = {
                "count": len(fills),
                "avg_slippage_bps": np.mean(slippage_errors) * 10000,
                "avg_latency_ms": np.mean([f["actual_time"] for f in fills]),
            }
        
        return stats
