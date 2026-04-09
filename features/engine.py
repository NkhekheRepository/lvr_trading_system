"""
Feature engine with rolling window statistics and normalized features.
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
    """Rolling window state for a single symbol."""
    returns: deque = None
    prices: deque = None
    bid_depths: deque = None
    ask_depths: deque = None
    spreads: deque = None

    bid_deltas: deque = None
    ask_deltas: deque = None

    def __post_init__(self):
        self.returns = deque(maxlen=100)
        self.prices = deque(maxlen=100)
        self.bid_depths = deque(maxlen=100)
        self.ask_depths = deque(maxlen=100)
        self.spreads = deque(maxlen=100)
        self.bid_deltas = deque(maxlen=50)
        self.ask_deltas = deque(maxlen=50)


class FeatureEngine:
    """
    Computes features from tick data and order book.
    
    Features:
        I_star = normalized returns
        L_star = depth z-score
        S_star = spread z-score
        OFI = order flow imbalance
        depth_imbalance = bid-ask depth ratio
    """

    def __init__(
        self,
        return_window: int = 50,
        volatility_window: int = 100,
        depth_window: int = 100,
        spread_window: int = 100
    ):
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
        """Update with new tick and compute features."""
        symbol = tick.symbol

        if symbol not in self._states:
            self._states[symbol] = WindowState()
            self._last_book[symbol] = None
            self._last_tick[symbol] = None

        state = self._states[symbol]

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

        book = order_book or self._last_book.get(symbol)
        if book:
            bid_depth = book.total_bid_depth
            ask_depth = book.total_ask_depth
            spread = book.spread

            state.bid_depths.append(bid_depth)
            state.ask_depths.append(ask_depth)
            state.spreads.append(spread)

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
        """Compute normalized feature vector."""
        state = self._states[symbol]

        returns = np.array(state.returns) if state.returns else np.array([0.0])
        volatility = self._safe_std(returns[-self.volatility_window:])
        I_star = returns[-1] / (volatility + EPS) if len(returns) > 0 else 0.0

        bid_depths = np.array(state.bid_depths) if state.bid_depths else np.array([0.0])
        ask_depths = np.array(state.ask_depths) if state.ask_depths else np.array([0.0])

        current_depth = (bid_depths[-1] + ask_depths[-1]) / 2 if len(bid_depths) > 0 else 0.0
        depth_mean = np.mean(bid_depths[-self.depth_window:] + ask_depths[-self.depth_window:]) / 2
        depth_std = self._safe_std(np.concatenate([
            bid_depths[-self.depth_window:],
            ask_depths[-self.depth_window:]
        ]))
        L_star = (current_depth - depth_mean) / (depth_std + EPS)

        spreads = np.array(state.spreads) if state.spreads else np.array([0.0])
        current_spread = spreads[-1] if len(spreads) > 0 else 0.0
        spread_mean = np.mean(spreads[-self.spread_window:]) if len(spreads) > 0 else 0.0
        spread_std = self._safe_std(spreads[-self.spread_window:]) if len(spreads) > 0 else EPS
        S_star = (current_spread - spread_mean) / (spread_std + EPS)

        bid_deltas = np.array(state.bid_deltas) if state.bid_deltas else np.array([0.0])
        ask_deltas = np.array(state.ask_deltas) if state.ask_deltas else np.array([0.0])

        ofi = 0.0
        if len(bid_deltas) > 0 and len(ask_deltas) > 0:
            ofi_bid = np.sum([d if d > 0 else 0 for d in bid_deltas[-10:]])
            ofi_ask = np.sum([d if d > 0 else 0 for d in ask_deltas[-10:]])
            ofi = (ofi_bid - ofi_ask) / (np.sum(np.abs(bid_deltas[-10:])) + np.sum(np.abs(ask_deltas[-10:])) + EPS)

        depth_imbalance = 0.0
        if len(bid_depths) > 0 and len(ask_depths) > 0:
            total = bid_depths[-1] + ask_depths[-1]
            if total > 0:
                depth_imbalance = (bid_depths[-1] - ask_depths[-1]) / total

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
        """Safe standard deviation."""
        if len(arr) < 2:
            return 1.0
        std = np.std(arr)
        return max(std, EPS)

    def _zero_features(self, timestamp: int, symbol: str) -> FeatureVector:
        """Return zero feature vector."""
        return FeatureVector(
            timestamp=timestamp,
            symbol=symbol,
            I_star=0.0, L_star=0.0, S_star=0.0, OFI=0.0, depth_imbalance=0.0
        )

    def get_state(self, symbol: str) -> Optional[WindowState]:
        """Get internal state for debugging."""
        return self._states.get(symbol)

    def reset(self, symbol: Optional[str] = None) -> None:
        """Reset state."""
        if symbol:
            if symbol in self._states:
                self._states[symbol] = WindowState()
                self._last_book[symbol] = None
                self._last_tick[symbol] = None
        else:
            self._states.clear()
            self._last_book.clear()
            self._last_tick.clear()
