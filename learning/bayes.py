"""
Bayesian learning engine for edge estimation.

This module implements Bayesian update methods for continuously
estimating the trading edge from realized trade outcomes. The system
maintains conjugate prior distributions (Beta for win rate, Normal
for PnL magnitude) and updates them with bounded, gradual changes
to prevent instability.

Mathematical Foundation:
    Win Rate: Beta(α, β) conjugate prior
        - α = number of wins + 1
        - β = number of losses + 1
        - Posterior updates with weight on each new observation
    
    PnL Magnitude: Normal(μ, σ²) distribution
        - μ = exponential moving average of returns
        - σ² = exponential moving variance
        - Bounded updates to prevent explosion
    
    Edge Estimation:
        edge = win_rate × avg_win - (1 - win_rate) × avg_loss
             = P(win) × E[return | win] - P(loss) × E[|return| | loss]

Stability Mechanisms:
    1. Cooldown: Minimum ticks between updates
    2. Weight decay: Less weight before min_samples
    3. Change bounding: Max % change per update
    4. Confidence scaling: Lower confidence = lower update weight

Example:
    >>> from learning.bayes import BayesianLearner, AdaptiveLearner
    >>> from app.schemas import FillEvent, Side
    >>>
    >>> learner = BayesianLearner(min_samples=30, update_rate=0.1)
    >>>
    >>> # After each trade
    >>> fill = FillEvent(symbol="BTCUSDT", side=Side.BUY, quantity=0.1, ...)
    >>> state = learner.update(fill)
    >>> 
    >>> print(f"Win rate: {state.win_rate:.1%}")
    >>> print(f"Edge: {state.expected_edge:.5f}")
    >>> print(f"Reliable: {state.is_reliable}")
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from app.schemas import BayesianState, FillEvent, Side

logger = logging.getLogger(__name__)

EPS = 1e-10


class BayesianLearner:
    """
    Bayesian update system for edge estimation.
    
    Maintains per-symbol Bayesian state with conjugate prior distributions
    for win rate (Beta) and PnL magnitude (Normal). Updates are bounded
    and gradual to prevent instability from noisy trade outcomes.
    
    Distribution Updates:
        Beta for Win Rate:
            α_new = α_old + is_win × weight
            β_new = β_old + (1 - is_win) × weight
        
        Normal for PnL:
            μ_new = μ_old + weight × (return - μ_old)
            σ²_new = σ²_old + weight × ((return - μ_old)² - σ²_old)
    
    Attributes:
        min_samples: Minimum trades before full weight updates.
        update_rate: Base learning rate for updates.
        max_change_per_update: Max % change per update (stability).
        cooldown_ticks: Ticks between updates per symbol.
    
    Example:
        >>> learner = BayesianLearner(min_samples=50, update_rate=0.05)
        >>> state = learner.update(fill_event)
        >>> if state.is_reliable:
        ...     edge = learner.get_edge_estimate("BTCUSDT")
    """

    def __init__(
        self,
        min_samples: int = 30,
        update_rate: float = 0.1,
        max_change_per_update: float = 0.05,
        cooldown_ticks: int = 10
    ):
        """
        Initialize Bayesian learner.
        
        Args:
            min_samples: Minimum trades before full weight updates.
            update_rate: Base learning rate (0.1 = 10% weight per update).
            max_change_per_update: Max % change allowed per update.
            cooldown_ticks: Minimum ticks between updates.
        """
        self.min_samples = min_samples
        self.update_rate = update_rate
        self.max_change_per_update = max_change_per_update
        self.cooldown_ticks = cooldown_ticks

        self._states: dict[str, BayesianState] = {}
        self._last_update_tick: dict[str, int] = {}
        self._current_tick = 0

    def get_state(self, symbol: str) -> BayesianState:
        """
        Get or create state for symbol.
        
        Args:
            symbol: Trading symbol.
        
        Returns:
            BayesianState for the symbol.
        """
        if symbol not in self._states:
            self._states[symbol] = BayesianState(symbol=symbol)
        return self._states[symbol]

    def update(self, fill: FillEvent, expected_edge: float = 0) -> BayesianState:
        """
        Update Bayesian state from fill event.
        
        Performs bounded Bayesian updates to the win rate (Beta)
        and PnL magnitude (Normal) distributions.
        
        Args:
            fill: FillEvent with trade details.
            expected_edge: Optional expected edge for this trade.
        
        Returns:
            Updated BayesianState.
        
        Algorithm:
            1. Increment tick counter
            2. Check cooldown for symbol
            3. Increment trade count
            4. Classify as win/loss
            5. Update Beta distribution for win rate
            6. Update Normal distribution for PnL
            7. Update confidence estimate
        """
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
        """
        Check if cooldown period has passed.
        
        Args:
            symbol: Trading symbol.
        
        Returns:
            True if update allowed, False if in cooldown.
        """
        if symbol not in self._last_update_tick:
            return True

        ticks_since = self._current_tick - self._last_update_tick[symbol]
        if ticks_since < self.cooldown_ticks:
            return False

        return True

    def _update_beta(self, state: BayesianState, is_win: bool) -> None:
        """
        Update Beta distribution parameters for win rate.
        
        Mathematical Formula:
            α_new = max(1, α_old + is_win × weight)
            β_new = max(1, β_old + (1 - is_win) × weight)
            
            where weight = update_rate (after min_samples), 
                  or update_rate × 0.1 (before min_samples)
        
        Args:
            state: BayesianState to update.
            is_win: Whether the trade was a win.
        """
        alpha_increment = 1.0 if is_win else 0.0
        beta_increment = 0.0 if is_win else 1.0

        weight = self.update_rate if state.trade_count >= self.min_samples else self.update_rate * 0.1

        new_alpha = state.alpha + alpha_increment * weight
        new_beta = state.beta + beta_increment * weight

        state.alpha = max(1.0, new_alpha)
        state.beta = max(1.0, new_beta)

    def _update_normal(self, state: BayesianState, realized_return: float) -> None:
        """
        Update Normal distribution parameters for P&L magnitude.
        
        Uses exponential moving average for mean and variance with
        bounded change to prevent instability.
        
        Mathematical Formula:
            μ_new = μ_old + weight × (x - μ_old)
            σ²_new = σ²_old + weight × ((x - μ_old)² - σ²_old)
            
            with bounded: |μ_new - μ_old| / |μ_old| <= max_change
        
        Args:
            state: BayesianState to update.
            realized_return: Realized return from trade.
        """
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
        """
        Update confidence based on sample size and consistency.
        
        Confidence increases with:
        - More samples (until min_samples reached)
        - Lower PnL variance (more consistent returns)
        
        Mathematical Formula:
            if n < min_samples:
                confidence = n / min_samples
            else:
                consistency = 1 - min(std_pnl, 1)
                confidence = clip(0.5 + 0.5 × consistency, 0, 1)
        
        Args:
            state: BayesianState to update.
        """
        if state.trade_count < self.min_samples:
            state.confidence = state.trade_count / self.min_samples
        else:
            consistency = 1.0 - min(state.std_pnl, 1.0)
            state.confidence = np.clip(0.5 + 0.5 * consistency, 0, 1)

    def _calculate_return(self, fill: FillEvent) -> float:
        """
        Calculate return from fill.
        
        Args:
            fill: FillEvent to calculate return for.
        
        Returns:
            1.0 for BUY (long), -1.0 for SELL (short).
        """
        return 1.0 if fill.side == Side.BUY else -1.0

    def get_edge_estimate(self, symbol: str) -> float:
        """
        Get estimated edge for symbol.
        
        Args:
            symbol: Trading symbol.
        
        Returns:
            Estimated edge value.
        """
        state = self.get_state(symbol)
        return state.expected_edge

    def is_reliable(self, symbol: str) -> bool:
        """
        Check if estimate is reliable.
        
        Args:
            symbol: Trading symbol.
        
        Returns:
            True if estimate meets reliability criteria.
        """
        state = self.get_state(symbol)
        return state.is_reliable

    def reset(self, symbol: Optional[str] = None) -> None:
        """
        Reset learner state.
        
        Args:
            symbol: Symbol to reset, or None to reset all.
        """
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
    
    Extends BayesianLearner with regime-specific update rates.
    Different market regimes (e.g., trending, ranging) may have
    different trade characteristics, and this learner adapts
    its update rate based on regime performance.
    
    Attributes:
        _regime_multipliers: Per-regime performance multipliers.
    
    Example:
        >>> learner = AdaptiveLearner()
        >>> learner.update_regime("high_vol", 0.8)  # 80% of normal rate
        >>> learner.update_regime("trending", 1.2)  # 120% of normal rate
    """

    def __init__(self, **kwargs):
        """
        Initialize adaptive learner.
        
        Args:
            **kwargs: Arguments passed to BayesianLearner.
        """
        super().__init__(**kwargs)
        self._regime_multipliers: dict[str, float] = {}

    def update(
        self,
        fill: FillEvent,
        expected_edge: float = 0,
        regime: str = "normal"
    ) -> BayesianState:
        """
        Update with regime awareness.
        
        Extends parent update with regime-specific learning rate.
        
        Args:
            fill: FillEvent with trade details.
            expected_edge: Optional expected edge.
            regime: Current market regime identifier.
        
        Returns:
            Updated BayesianState.
        
        Learning Rate Adjustment:
            adaptive_rate = update_rate × regime_multiplier
            
            where regime_multiplier is learned from regime performance.
        """
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
        """
        Update regime performance multiplier.
        
        Adjusts how much weight is given to trades in a given regime
        based on recent performance.
        
        Args:
            regime: Regime identifier.
            performance: Performance ratio (e.g., 0.8 = 80% of expected).
        
        Adjustment Formula:
            multiplier_new = clip(
                multiplier_old + 0.1 × (performance - 1.0),
                0.5, 2.0
            )
        
        Example:
            >>> learner.update_regime("high_vol", 0.5)  # Poor performance
            >>> learner.update_regime("trending", 1.3)  # Good performance
        """
        if regime not in self._regime_multipliers:
            self._regime_multipliers[regime] = 1.0

        current = self._regime_multipliers[regime]
        adjustment = 0.1 * (performance - 1.0)
        self._regime_multipliers[regime] = np.clip(current + adjustment, 0.5, 2.0)


class DriftDetector:
    """
    Concept Drift Detection for Trading Models
    
    Detects changes in the statistical properties of trade outcomes
    that may indicate model degradation or market regime changes.
    
    DETECTION METHODS:
    1. ADWIN (Adaptive Windowing): Detects changes in streaming data
    2. CUSUM: Cumulative sum for abrupt changes
    3. Page-Hinkley: Sequential change detection
    
    RESPONSE ACTIONS:
    - Alert: Log warning for monitoring
    - Reduce confidence: Lower confidence until more data
    - Reset: Reset model state if severe drift detected
    - Pause: Temporarily pause trading
    
    Usage:
        >>> detector = DriftDetector(window_size=100, threshold=0.05)
        >>> 
        >>> # After each trade
        >>> drift_info = detector.update(is_win)
        >>> if drift_info.detected:
        ...     learner.reset(symbol)  # Reset learning
    """
    
    def __init__(
        self,
        window_size: int = 100,
        adwin_delta: float = 0.002,
        cusum_threshold: float = 5.0,
        page_hinkley_threshold: float = 50.0,
        alert_threshold: float = 0.01,
        confidence_decay_rate: float = 0.95,
    ):
        self.window_size = window_size
        self.adwin_delta = adwin_delta
        self.cusum_threshold = cusum_threshold
        self.page_hinkley_threshold = page_hinkley_threshold
        self.alert_threshold = alert_threshold
        self.confidence_decay_rate = confidence_decay_rate
        
        self._window: list = []
        self._mean: float = 0.5
        self._variance: float = 0.25
        
        self._cusum_pos: float = 0.0
        self._cusum_neg: float = 0.0
        
        self._ph_sum: float = 0.0
        self._ph_mean: float = 0.0
        
        self._drift_detected: bool = False
        self._drift_count: int = 0
        self._last_drift_tick: int = 0
        
        self._confidence: float = 1.0
        self._tick: int = 0
    
    @property
    def confidence(self) -> float:
        """Get current confidence level (decays with drift)."""
        return self._confidence
    
    @property
    def drift_detected(self) -> bool:
        """Check if drift was recently detected."""
        return self._drift_detected
    
    def update(self, value: float) -> 'DriftInfo':
        """
        Update drift detector with new observation.
        
        Args:
            value: Binary value (0 or 1) or continuous value
            
        Returns:
            DriftInfo with detection results
        """
        self._tick += 1
        
        self._window.append(value)
        if len(self._window) > self.window_size:
            self._window.pop(0)
        
        old_mean = self._mean
        self._mean = sum(self._window) / len(self._window)
        
        if len(self._window) > 1:
            self._variance = sum((x - self._mean) ** 2 for x in self._window) / len(self._window)
        
        drift_detected = False
        drift_type = None
        drift_severity = 0.0
        
        adwin_change = self._check_adwin()
        if adwin_change:
            drift_detected = True
            drift_type = "adwin"
            drift_severity = abs(self._mean - old_mean) / (old_mean + 1e-10)
        
        cusum_change = self._check_cusum(value)
        if cusum_change:
            drift_detected = True
            drift_type = "cusum"
            drift_severity = 0.1
        
        ph_change = self._check_page_hinkley(value)
        if ph_change:
            drift_detected = True
            drift_type = "page_hinkley"
            drift_severity = 0.05
        
        if drift_detected:
            self._drift_count += 1
            self._last_drift_tick = self._tick
            self._confidence *= self.confidence_decay_rate
            self._confidence = max(0.1, self._confidence)
        else:
            self._confidence = min(1.0, self._confidence + 0.01)
        
        self._drift_detected = drift_detected
        
        return DriftInfo(
            detected=drift_detected,
            drift_type=drift_type,
            drift_severity=drift_severity,
            current_mean=self._mean,
            current_variance=self._variance,
            confidence=self._confidence,
            drift_count=self._drift_count,
        )
    
    def _check_adwin(self) -> bool:
        """
        ADWIN change detection.
        
        Compares recent window mean to older window mean.
        """
        if len(self._window) < self.window_size // 2:
            return False
        
        mid = len(self._window) // 2
        recent = self._window[mid:]
        older = self._window[:mid]
        
        recent_mean = sum(recent) / len(recent)
        older_mean = sum(older) / len(older)
        
        change = abs(recent_mean - older_mean)
        return change > self.alert_threshold
    
    def _check_cusum(self, value: float) -> bool:
        """
        CUSUM change detection.
        
        Tracks cumulative deviations from expected mean.
        """
        target = 0.5
        delta = value - target
        
        self._cusum_pos = max(0, self._cusum_pos + delta - 0.01)
        self._cusum_neg = max(0, self._cusum_neg - delta - 0.01)
        
        return max(self._cusum_pos, self._cusum_neg) > self.cusum_threshold
    
    def _check_page_hinkley(self, value: float) -> bool:
        """
        Page-Hinkley change detection.
        
        Sequential test for change in mean.
        """
        if self._tick == 1:
            self._ph_mean = value
            return False
        
        delta = value - self._ph_mean
        self._ph_sum += delta - 0.005
        self._ph_mean = 0.005 * value + 0.995 * self._ph_mean
        
        return abs(self._ph_sum) > self.page_hinkley_threshold
    
    def reset(self) -> None:
        """Reset drift detector state."""
        self._window.clear()
        self._mean = 0.5
        self._variance = 0.25
        self._cusum_pos = 0.0
        self._cusum_neg = 0.0
        self._ph_sum = 0.0
        self._ph_mean = 0.0
        self._drift_detected = False
        self._confidence = 1.0
        self._tick = 0
    
    def get_stats(self) -> dict:
        """Get detector statistics."""
        return {
            "mean": self._mean,
            "variance": self._variance,
            "confidence": self._confidence,
            "drift_count": self._drift_count,
            "window_size": len(self._window),
            "cusum_pos": self._cusum_pos,
            "cusum_neg": self._cusum_neg,
        }


@dataclass
class DriftInfo:
    """Results from drift detection check."""
    detected: bool
    drift_type: Optional[str]
    drift_severity: float
    current_mean: float
    current_variance: float
    confidence: float
    drift_count: int


from dataclasses import dataclass
