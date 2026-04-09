"""
Bayesian learning engine for edge estimation.
"""

import logging
import time
from typing import Optional

import numpy as np

from app.schemas import BayesianState, FillEvent, Side

logger = logging.getLogger(__name__)

EPS = 1e-10


class BayesianLearner:
    """
    Bayesian update system for edge estimation.
    
    Updates bounded and gradual to prevent instability.
    """

    def __init__(
        self,
        min_samples: int = 30,
        update_rate: float = 0.1,
        max_change_per_update: float = 0.05,
        cooldown_ticks: int = 10
    ):
        self.min_samples = min_samples
        self.update_rate = update_rate
        self.max_change_per_update = max_change_per_update
        self.cooldown_ticks = cooldown_ticks

        self._states: dict[str, BayesianState] = {}
        self._last_update_tick: dict[str, int] = {}
        self._current_tick = 0

    def get_state(self, symbol: str) -> BayesianState:
        """Get or create state for symbol."""
        if symbol not in self._states:
            self._states[symbol] = BayesianState(symbol=symbol)
        return self._states[symbol]

    def update(self, fill: FillEvent, expected_edge: float = 0) -> BayesianState:
        """Update Bayesian state from fill event."""
        self._current_tick += 1
        state = self.get_state(fill.symbol)

        if not self._check_cooldown(fill.symbol):
            return state

        state.trade_count += 1
        state.last_update = fill.timestamp

        realized_return = self._calculate_return(fill)
        is_win = realized_return > 0

        if is_win:
            state.win_count += 1

        self._update_beta(state, is_win)
        self._update_normal(state, realized_return)
        self._update_confidence(state)

        logger.debug(
            f"Bayesian update for {fill.symbol}: "
            f"win_rate={state.win_rate:.3f}, edge={state.expected_edge:.5f}"
        )

        return state

    def _check_cooldown(self, symbol: str) -> bool:
        """Check if cooldown period has passed."""
        if symbol not in self._last_update_tick:
            return True

        ticks_since = self._current_tick - self._last_update_tick[symbol]
        if ticks_since < self.cooldown_ticks:
            return False

        return True

    def _update_beta(self, state: BayesianState, is_win: bool) -> None:
        """Update Beta distribution parameters for win rate."""
        alpha_increment = 1.0 if is_win else 0.0
        beta_increment = 0.0 if is_win else 1.0

        weight = self.update_rate if state.trade_count >= self.min_samples else self.update_rate * 0.1

        new_alpha = state.alpha + alpha_increment * weight
        new_beta = state.beta + beta_increment * weight

        state.alpha = max(1.0, new_alpha)
        state.beta = max(1.0, new_beta)

    def _update_normal(self, state: BayesianState, realized_return: float) -> None:
        """Update Normal distribution parameters for P&L magnitude."""
        if state.trade_count <= 1:
            state.mean_pnl = realized_return
            return

        weight = self.update_rate if state.trade_count >= self.min_samples else self.update_rate * 0.1

        old_mean = state.mean_pnl
        old_std = state.std_pnl

        new_mean = old_mean + weight * (realized_return - old_mean)

        squared_diff = (realized_return - old_mean) ** 2
        new_var = old_std ** 2 + weight * (squared_diff - old_std ** 2)
        new_std = max(np.sqrt(new_var), EPS)

        change = abs(new_mean - old_mean) / (abs(old_mean) + EPS)
        if change > self.max_change_per_update:
            new_mean = old_mean + np.sign(new_mean - old_mean) * old_mean * self.max_change_per_update

        state.mean_pnl = new_mean
        state.std_pnl = new_std

    def _update_confidence(self, state: BayesianState) -> None:
        """Update confidence based on sample size."""
        if state.trade_count < self.min_samples:
            state.confidence = state.trade_count / self.min_samples
        else:
            consistency = 1.0 - min(state.std_pnl, 1.0)
            state.confidence = np.clip(0.5 + 0.5 * consistency, 0, 1)

    def _calculate_return(self, fill: FillEvent) -> float:
        """Calculate return from fill."""
        return 1.0 if fill.side == Side.BUY else -1.0

    def get_edge_estimate(self, symbol: str) -> float:
        """Get estimated edge for symbol."""
        state = self.get_state(symbol)
        return state.expected_edge

    def is_reliable(self, symbol: str) -> bool:
        """Check if estimate is reliable."""
        state = self.get_state(symbol)
        return state.is_reliable

    def reset(self, symbol: Optional[str] = None) -> None:
        """Reset learner state."""
        if symbol:
            if symbol in self._states:
                self._states[symbol] = BayesianState(symbol=symbol)
            if symbol in self._last_update_tick:
                del self._last_update_tick[symbol]
        else:
            self._states.clear()
            self._last_update_tick.clear()
        self._current_tick = 0


class AdaptiveLearner(BayesianLearner):
    """
    Adaptive learner with regime awareness.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._regime_multipliers: dict[str, float] = {}

    def update(
        self,
        fill: FillEvent,
        expected_edge: float = 0,
        regime: str = "normal"
    ) -> BayesianState:
        """Update with regime awareness."""
        self._current_tick += 1
        state = self.get_state(fill.symbol)

        if not self._check_cooldown(fill.symbol):
            return state

        state.trade_count += 1
        state.last_update = fill.timestamp

        realized_return = self._calculate_return(fill)
        is_win = realized_return > 0

        if is_win:
            state.win_count += 1

        regime_mult = self._regime_multipliers.get(regime, 1.0)
        adaptive_rate = self.update_rate * regime_mult

        alpha_increment = 1.0 if is_win else 0.0
        beta_increment = 0.0 if is_win else 1.0

        new_alpha = state.alpha + alpha_increment * adaptive_rate
        new_beta = state.beta + beta_increment * adaptive_rate

        state.alpha = max(1.0, new_alpha)
        state.beta = max(1.0, new_beta)

        self._update_normal(state, realized_return)
        self._update_confidence(state)

        return state

    def update_regime(self, regime: str, performance: float) -> None:
        """Update regime performance multiplier."""
        if regime not in self._regime_multipliers:
            self._regime_multipliers[regime] = 1.0

        current = self._regime_multipliers[regime]
        adjustment = 0.1 * (performance - 1.0)
        self._regime_multipliers[regime] = np.clip(current + adjustment, 0.5, 2.0)
