"""
Feature engine with rolling window statistics and normalized features.

Feature Engineering Pipeline
===========================

The feature engine transforms raw tick data and order book snapshots into
normalized features for signal generation.

Feature Definitions
------------------

1. I* (Normalized Returns)
   Formula: I* = returns_t / (volatility_t + ε)
   
   - returns_t = log(price_t / price_{t-1})
   - volatility_t = std(returns_{t-window:t})
   - ε = 1e-10 (prevents division by zero)
   
   Interpretation:
   - I* > 0: Price increasing
   - I* < 0: Price decreasing
   - |I*| > 2: Strong impulse

2. L* (Depth Z-Score)
   Formula: L* = (depth_t - μ_depth) / (σ_depth + ε)
   
   - depth_t = (bid_depth + ask_depth) / 2
   - μ_depth = mean(depth over window)
   - σ_depth = std(depth over window)
   
   Interpretation:
   - L* > 0: Higher than average liquidity
   - L* < 0: Lower than average liquidity

3. S* (Spread Z-Score)
   Formula: S* = (spread_t - μ_spread) / (σ_spread + ε)
   
   - spread_t = best_ask - best_bid
   - Higher S* indicates wider than average spread

4. OFI (Order Flow Imbalance)
   Formula: OFI = (Σ Δbid_pos - Σ Δask_pos) / (Σ|Δbid| + Σ|Δask| + ε)
   
   - Measures net directional pressure
   - OFI > 0: Buy pressure dominates
   - OFI < 0: Sell pressure dominates

5. Depth Imbalance
   Formula: DI = (bid_depth - ask_depth) / (bid_depth + ask_depth + ε)
   
   - Snapshot-based liquidity imbalance
   - DI > 0: Buy-side depth dominates
   - DI < 0: Sell-side depth dominates

Rolling Windows
---------------
| Feature      | Window Size | Purpose                    |
|--------------|-------------|----------------------------|
| Returns      | 50 ticks   | Impulse calculation        |
| Volatility   | 100 ticks  | I* normalization           |
| Depth        | 100 ticks  | L* z-score                 |
| Spread       | 100 ticks  | S* z-score                 |
| OFI          | 10 ticks   | Order flow                 |

Stability Guarantees
--------------------
1. Division by Zero: ε = 1e-10 prevents NaN
2. NaN Handling: Returns zeros with warning log
3. Clipping: All z-scores clipped to [-10, 10]
4. Determinism: Same input produces same output

Usage Example
------------
    from features import FeatureEngine
    from app.schemas import TradeTick, Side
    
    engine = FeatureEngine(
        return_window=50,
        volatility_window=100,
        depth_window=100,
        spread_window=100
    )
    
    tick = TradeTick(
        timestamp=1609459200000,
        symbol="BTCUSDT",
        price=50000.0,
        size=0.1,
        side=Side.BUY
    )
    
    features = engine.update(tick, order_book)
    print(f"I*: {features.I_star:.4f}")
    print(f"L*: {features.L_star:.4f}")
    print(f"S*: {features.S_star:.4f}")
    print(f"OFI: {features.OFI:.4f}")
"""

import logging
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

from app.schemas import FeatureVector, OrderBookSnapshot, Side, TradeTick

logger = logging.getLogger(__name__)

EPS = 1e-10


@dataclass
class WindowState:
    """
    Rolling window state for a single symbol.
    
    Maintains deques for all rolling statistics needed for feature computation.
    Using deques with maxlen automatically evicts old values for memory efficiency.
    
    Attributes:
        returns: Log returns over price changes
        prices: Raw prices for reference
        bid_depths: Total bid depth over time
        ask_depths: Total ask depth over time
        spreads: Bid-ask spreads over time
        bid_deltas: Changes in bid depth
        ask_deltas: Changes in ask depth
    
    Note:
        All deques are initialized with maxlen=100 except bid/ask deltas
        which use maxlen=50 to save memory.
    """
    returns: deque = None
    prices: deque = None
    bid_depths: deque = None
    ask_depths: deque = None
    spreads: deque = None
    bid_deltas: deque = None
    ask_deltas: deque = None

    def __post_init__(self):
        """Initialize deques with maxlen for automatic eviction."""
        self.returns = deque(maxlen=100)
        self.prices = deque(maxlen=100)
        self.bid_depths = deque(maxlen=100)
        self.ask_depths = deque(maxlen=100)
        self.spreads = deque(maxlen=100)
        self.bid_deltas = deque(maxlen=50)
        self.ask_deltas = deque(maxlen=50)


class FeatureEngine:
    """
    Computes normalized features from tick data and order book.
    
    This engine is the foundation of the signal generation system. It transforms
    raw market data into normalized features that capture:
    
    1. Price momentum (I*)
    2. Liquidity conditions (L*)
    3. Spread conditions (S*)
    4. Order flow pressure (OFI)
    5. Depth imbalance
    
    The engine maintains rolling windows of data to compute z-scores and
    statistics, ensuring features are comparable across different market
    conditions.
    
    Attributes:
        return_window: Number of ticks for return calculation
        volatility_window: Number of ticks for volatility calculation
        depth_window: Number of ticks for depth statistics
        spread_window: Number of ticks for spread statistics
    
    Example:
        >>> engine = FeatureEngine()
        >>> features = engine.update(tick, order_book)
        >>> print(features.I_star, features.L_star, features.OFI)
        
    Note:
        - All features are deterministic (same input = same output)
        - NaN values are replaced with zeros and logged
        - Division by zero is prevented with EPS = 1e-10
    """

    def __init__(
        self,
        return_window: int = 50,
        volatility_window: int = 100,
        depth_window: int = 100,
        spread_window: int = 100
    ):
        """
        Initialize FeatureEngine with rolling window sizes.
        
        Args:
            return_window: Ticks for return calculation (default: 50)
            volatility_window: Ticks for volatility calculation (default: 100)
            depth_window: Ticks for depth statistics (default: 100)
            spread_window: Ticks for spread statistics (default: 100)
        
        Note:
            Larger windows provide more stable statistics but require
            more warm-up ticks. Windows of 50-100 are generally optimal
            for tick-level data.
        """
        self.return_window = return_window
        self.volatility_window = volatility_window
        self.depth_window = depth_window
        self.spread_window = spread_window

        self._states: dict[str, WindowState] = {}
        self._last_book: dict[str, Optional[OrderBookSnapshot]] = {}
        self._last_tick: dict[str, Optional[TradeTick]] = {}

    def update(
        self,
        tick: TradeTick,
        order_book: Optional[OrderBookSnapshot] = None
    ) -> FeatureVector:
        """
        Update with new tick and compute features.
        
        This is the main entry point for the feature engine. Call this
        method with each new tick to compute the feature vector.
        
        Args:
            tick: Current trade tick containing price, size, side
            order_book: Optional order book snapshot for depth/spread features
        
        Returns:
            FeatureVector containing:
            - I*: Normalized returns (impulse)
            - L*: Depth z-score (liquidity)
            - S*: Spread z-score
            - OFI: Order flow imbalance
            - depth_imbalance: Bid-ask depth ratio
            - Plus raw values for debugging
        
        Example:
            >>> tick = TradeTick(timestamp=..., price=50000, size=0.1, side=Side.BUY)
            >>> book = OrderBookSnapshot(...)
            >>> features = engine.update(tick, book)
            >>> assert not features.has_nans()
        """
        symbol = tick.symbol

        if symbol not in self._states:
            self._states[symbol] = WindowState()
            self._last_book[symbol] = None
            self._last_tick[symbol] = None

        state = self._states[symbol]

        # Compute log return
        if self._last_tick[symbol] is not None:
            last_price = self._last_tick[symbol].price
            if last_price > 0:
                ret = np.log(tick.price / last_price)
            else:
                ret = 0.0
        else:
            ret = 0.0

        state.returns.append(ret)
        state.prices.append(tick.price)

        # Update order book features
        book = order_book or self._last_book.get(symbol)
        if book:
            bid_depth = book.total_bid_depth
            ask_depth = book.total_ask_depth
            spread = book.spread

            state.bid_depths.append(bid_depth)
            state.ask_depths.append(ask_depth)
            state.spreads.append(spread)

            # Compute depth deltas for OFI
            if self._last_book.get(symbol) is not None:
                last_book = self._last_book[symbol]
                bid_delta = bid_depth - last_book.total_bid_depth
                ask_delta = ask_depth - last_book.total_ask_depth

                state.bid_deltas.append(bid_delta)
                state.ask_deltas.append(ask_delta)

            self._last_book[symbol] = book

        self._last_tick[symbol] = tick

        return self._compute_features(symbol, tick.timestamp)

    def _compute_features(self, symbol: str, timestamp: int) -> FeatureVector:
        """
        Compute normalized feature vector.
        
        Internal method that computes all features from rolling window state.
        
        Formula Reference:
            I* = returns[-1] / (std(returns[-window:]) + ε)
            L* = (depth - mean(depth[-window:])) / (std(depth[-window:]) + ε)
            S* = (spread - mean(spread[-window:])) / (std(spread[-window:]) + ε)
            OFI = (ΣΔbid_pos - ΣΔask_pos) / (Σ|Δbid| + Σ|Δask| + ε)
        
        Returns:
            FeatureVector with all computed features, clipped to bounds
        """
        state = self._states[symbol]

        # I*: Normalized Returns
        returns = np.array(state.returns) if state.returns else np.array([0.0])
        volatility = self._safe_std(returns[-self.volatility_window:])
        I_star = returns[-1] / (volatility + EPS) if len(returns) > 0 else 0.0

        # L*: Depth Z-Score
        bid_depths = np.array(state.bid_depths) if state.bid_depths else np.array([0.0])
        ask_depths = np.array(state.ask_depths) if state.ask_depths else np.array([0.0])

        current_depth = (bid_depths[-1] + ask_depths[-1]) / 2 if len(bid_depths) > 0 else 0.0
        depth_mean = np.mean(bid_depths[-self.depth_window:] + ask_depths[-self.depth_window:]) / 2
        depth_std = self._safe_std(np.concatenate([
            bid_depths[-self.depth_window:],
            ask_depths[-self.depth_window:]
        ]))
        L_star = (current_depth - depth_mean) / (depth_std + EPS)

        # S*: Spread Z-Score
        spreads = np.array(state.spreads) if state.spreads else np.array([0.0])
        current_spread = spreads[-1] if len(spreads) > 0 else 0.0
        spread_mean = np.mean(spreads[-self.spread_window:]) if len(spreads) > 0 else 0.0
        spread_std = self._safe_std(spreads[-self.spread_window:]) if len(spreads) > 0 else EPS
        S_star = (current_spread - spread_mean) / (spread_std + EPS)

        # OFI: Order Flow Imbalance
        bid_deltas = np.array(state.bid_deltas) if state.bid_deltas else np.array([0.0])
        ask_deltas = np.array(state.ask_deltas) if state.ask_deltas else np.array([0.0])

        ofi = 0.0
        if len(bid_deltas) > 0 and len(ask_deltas) > 0:
            ofi_bid = np.sum([d if d > 0 else 0 for d in bid_deltas[-10:]])
            ofi_ask = np.sum([d if d > 0 else 0 for d in ask_deltas[-10:]])
            ofi = (ofi_bid - ofi_ask) / (np.sum(np.abs(bid_deltas[-10:])) + np.sum(np.abs(ask_deltas[-10:])) + EPS)

        # Depth Imbalance
        depth_imbalance = 0.0
        if len(bid_depths) > 0 and len(ask_depths) > 0:
            total = bid_depths[-1] + ask_depths[-1]
            if total > 0:
                depth_imbalance = (bid_depths[-1] - ask_depths[-1]) / total

        # Build FeatureVector with clipping
        features = FeatureVector(
            timestamp=timestamp,
            symbol=symbol,
            I_star=float(np.clip(I_star, -10, 10)),
            L_star=float(np.clip(L_star, -10, 10)),
            S_star=float(np.clip(S_star, -10, 10)),
            OFI=float(np.clip(ofi, -1, 1)),
            depth_imbalance=float(np.clip(depth_imbalance, -1, 1)),
            returns=float(returns[-1]) if len(returns) > 0 else 0.0,
            volatility=float(volatility),
            spread=float(current_spread),
            bid_depth=float(bid_depths[-1]) if len(bid_depths) > 0 else 0.0,
            ask_depth=float(ask_depths[-1]) if len(ask_depths) > 0 else 0.0
        )

        if features.has_nans():
            logger.warning(f"NaN detected in features for {symbol}, returning zeros")
            return self._zero_features(timestamp, symbol)

        return features

    def _safe_std(self, arr: np.ndarray) -> float:
        """
        Compute safe standard deviation.
        
        Args:
            arr: Input array
        
        Returns:
            Standard deviation, minimum EPS to prevent division by zero
        
        Note:
            Returns 1.0 for arrays with fewer than 2 elements
            to avoid undefined variance.
        """
        if len(arr) < 2:
            return 1.0
        std = np.std(arr)
        return max(std, EPS)

    def _zero_features(self, timestamp: int, symbol: str) -> FeatureVector:
        """
        Return zero feature vector for error recovery.
        
        Called when NaN is detected to prevent downstream errors.
        All features set to 0, which is neutral for signal generation.
        """
        return FeatureVector(
            timestamp=timestamp,
            symbol=symbol,
            I_star=0.0, L_star=0.0, S_star=0.0, OFI=0.0, depth_imbalance=0.0
        )

    def get_state(self, symbol: str) -> Optional[WindowState]:
        """
        Get internal state for debugging.
        
        Args:
            symbol: Trading symbol
        
        Returns:
            WindowState for the symbol, or None if not initialized
        
        Example:
            >>> state = engine.get_state("BTCUSDT")
            >>> print(f"Warm-up: {len(state.returns)} ticks")
        """
        return self._states.get(symbol)

    def reset(self, symbol: Optional[str] = None) -> None:
        """
        Reset internal state.
        
        Args:
            symbol: Reset specific symbol, or all if None
        
        Use this to:
        - Free memory after processing
        - Start fresh for new symbol
        - Clear accumulated state
        
        Example:
            >>> # Reset specific symbol
            >>> engine.reset("BTCUSDT")
            >>> 
            >>> # Reset all
            >>> engine.reset()
        """
        if symbol:
            if symbol in self._states:
                self._states[symbol] = WindowState()
                self._last_book[symbol] = None
                self._last_tick[symbol] = None
        else:
            self._states.clear()
            self._last_book.clear()
            self._last_tick.clear()
