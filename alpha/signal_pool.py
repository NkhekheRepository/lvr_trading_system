"""
Signal Pool - Lifecycle Management for Alpha Signals

This module implements signal lifecycle management:
1. Generate: Create new signals
2. Validate: Check cost, filters, confidence
3. Deploy: Activate signals for trading
4. Monitor: Track performance
5. Kill: Disable underperforming signals

LIFECYCLE STATES:
    Generated → Validated → Deployed → Monitored → Killed
                     ↓
                  Rejected (at any stage)

KILL CONDITIONS:
- Sharpe < threshold for N periods
- Drawdown contribution > limit
- Prediction error spikes
- Regime mismatch

Author: LVR Trading System
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional
from uuid import uuid4

import numpy as np

logger = logging.getLogger(__name__)


class SignalState(Enum):
    """Signal lifecycle states."""
    GENERATED = auto()
    VALIDATED = auto()
    DEPLOYED = auto()
    MONITORED = auto()
    REJECTED = auto()
    KILLED = auto()


class KillReason(Enum):
    """Reasons for signal termination."""
    POOR_SHARPE = "poor_sharpe"
    DRAWDOWN = "drawdown_contribution"
    PREDICTION_ERROR = "prediction_error"
    REGIME_MISMATCH = "regime_mismatch"
    MANUAL = "manual"
    EXPIRED = "expired"


@dataclass
class SignalMetrics:
    """Performance metrics for a signal."""
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    total_pnl: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    
    prediction_errors: deque = field(default_factory=lambda: deque(maxlen=100))
    returns: deque = field(default_factory=lambda: deque(maxlen=100))
    
    last_update: float = field(default_factory=time.time)
    
    @property
    def edge(self) -> float:
        """Calculate realized edge."""
        if self.trade_count == 0:
            return 0.0
        if self.win_rate == 0 or self.avg_loss == 0:
            return 0.0
        return self.win_rate * self.avg_win - (1 - self.win_rate) * abs(self.avg_loss)
    
    def update_trade(self, pnl: float, prediction_error: float) -> None:
        """Update metrics with new trade."""
        self.trade_count += 1
        self.last_update = time.time()
        
        if pnl > 0:
            self.win_count += 1
            self.avg_win = (self.avg_win * (self.win_count - 1) + pnl) / self.win_count
        else:
            self.loss_count += 1
            self.avg_loss = (self.avg_loss * (self.loss_count - 1) + pnl) / self.loss_count
        
        self.realized_pnl += max(0, pnl)
        self.unrealized_pnl = max(0, pnl)
        self.total_pnl += pnl
        
        self.win_rate = self.win_count / self.trade_count if self.trade_count > 0 else 0.0
        
        self.prediction_errors.append(prediction_error)
        if pnl != 0:
            self.returns.append(pnl)
        
        self._compute_sharpe()
        self._compute_max_drawdown()
    
    def _compute_sharpe(self) -> None:
        """Compute rolling Sharpe ratio."""
        if len(self.returns) < 5:
            self.sharpe = 0.0
            return
            
        returns_array = np.array(list(self.returns))
        mean_return = np.mean(returns_array)
        std_return = np.std(returns_array)
        
        if std_return == 0:
            self.sharpe = 0.0
            return
        
        self.sharpe = mean_return / std_return * np.sqrt(252)
    
    def _compute_max_drawdown(self) -> None:
        """Compute maximum drawdown."""
        if not self.returns:
            return
            
        cumulative = np.cumsum(list(self.returns))
        peak = np.maximum.accumulate(cumulative)
        drawdown = peak - cumulative
        
        self.max_drawdown = np.max(drawdown) if len(drawdown) > 0 else 0.0


@dataclass
class PoolConfig:
    """Configuration for signal pool."""
    min_sharpe: float = 0.5
    max_drawdown_contribution: float = 0.3
    max_prediction_error: float = 0.02
    kill_after_trades: int = 50
    kill_after_days: int = 30
    min_trades_before_kill: int = 10
    stale_threshold_seconds: float = 300.0


@dataclass
class AlphaSignal:
    """
    Alpha signal with full lifecycle tracking.
    
    Attributes:
        signal_id: Unique identifier
        symbol: Trading symbol
        features: Feature vector that generated signal
        raw_edge: Predicted edge before costs
        net_edge: Edge after costs
        confidence: Signal confidence (0-1)
        state: Current lifecycle state
        metrics: Performance metrics
        created_at: Creation timestamp
        deployed_at: When deployed for trading
    """
    signal_id: str
    symbol: str
    features: dict
    
    raw_edge: float
    net_edge: float
    confidence: float
    
    state: SignalState = SignalState.GENERATED
    metrics: SignalMetrics = field(default_factory=SignalMetrics)
    
    created_at: float = field(default_factory=time.time)
    deployed_at: Optional[float] = None
    killed_at: Optional[float] = None
    kill_reason: Optional[KillReason] = None
    
    def __post_init__(self):
        if not self.signal_id:
            self.signal_id = str(uuid4())[:8]
    
    @property
    def age_seconds(self) -> float:
        """Signal age in seconds."""
        return time.time() - self.created_at
    
    @property
    def is_active(self) -> bool:
        """Check if signal is active (deployed or monitored)."""
        return self.state in (SignalState.DEPLOYED, SignalState.MONITORED)
    
    @property
    def should_kill(self) -> bool:
        """Check if signal should be killed."""
        return self.state == SignalState.KILLED
    
    def validate(self, cost_aware_edge: float, validation_result) -> bool:
        """
        Validate signal for deployment.
        
        Args:
            cost_aware_edge: Net edge after costs
            validation_result: Result from filter validation
            
        Returns:
            True if validation passed
        """
        if not validation_result.all_passed:
            self.state = SignalState.REJECTED
            return False
        
        if self.net_edge <= 0:
            self.state = SignalState.REJECTED
            return False
        
        self.state = SignalState.VALIDATED
        return True
    
    def deploy(self) -> None:
        """Deploy signal for trading."""
        if self.state != SignalState.VALIDATED:
            raise ValueError(f"Cannot deploy signal in state {self.state}")
        
        self.state = SignalState.DEPLOYED
        self.deployed_at = time.time()
        logger.info(f"Signal {self.signal_id} deployed for {self.symbol}")
    
    def monitor(self) -> None:
        """Move to monitored state."""
        if self.state != SignalState.DEPLOYED:
            return
        self.state = SignalState.MONITORED
    
    def kill(self, reason: KillReason) -> None:
        """Kill the signal."""
        self.state = SignalState.KILLED
        self.killed_at = time.time()
        self.kill_reason = reason
        logger.warning(f"Signal {self.signal_id} killed: {reason.value}")
    
    def record_trade(self, pnl: float, prediction_error: float) -> None:
        """Record trade outcome."""
        self.metrics.update_trade(pnl, prediction_error)


class SignalPool:
    """
    Manages lifecycle of all alpha signals.
    
    RESPONSIBILITIES:
    1. Track all signals across lifecycle states
    2. Evaluate kill conditions
    3. Manage signal weights
    4. Provide aggregate statistics
    
    Example:
        >>> pool = SignalPool(config=PoolConfig())
        >>> 
        >>> # Create and validate signal
        >>> signal = pool.create_signal(symbol="BTC", raw_edge=0.01, ...)
        >>> if pool.validate_signal(signal, costs, filters):
        ...     pool.deploy_signal(signal)
        >>> 
        >>> # After trades
        >>> pool.evaluate_kill_conditions()
        >>> 
        >>> # Get active signals
        >>> active = pool.get_active_signals()
    """
    
    def __init__(self, config: Optional[PoolConfig] = None):
        """
        Initialize signal pool.
        
        Args:
            config: Pool configuration with kill thresholds
        """
        self.config = config or PoolConfig()
        
        self._signals: dict[str, AlphaSignal] = {}
        self._signals_by_symbol: dict[str, list[str]] = {}
        
        self._lock = None  # For thread safety if needed
    
    def create_signal(
        self,
        symbol: str,
        features: dict,
        raw_edge: float,
        confidence: float,
        cost_aware_edge: float,
    ) -> AlphaSignal:
        """
        Create new signal in GENERATED state.
        
        Args:
            symbol: Trading symbol
            features: Feature vector
            raw_edge: Raw predicted edge
            confidence: Signal confidence
            cost_aware_edge: Edge after costs
            
        Returns:
            New AlphaSignal
        """
        signal = AlphaSignal(
            signal_id=str(uuid4())[:8],
            symbol=symbol,
            features=features,
            raw_edge=raw_edge,
            net_edge=cost_aware_edge,
            confidence=confidence,
        )
        
        self._signals[signal.signal_id] = signal
        
        if symbol not in self._signals_by_symbol:
            self._signals_by_symbol[symbol] = []
        self._signals_by_symbol[symbol].append(signal.signal_id)
        
        logger.debug(f"Created signal {signal.signal_id} for {symbol}")
        return signal
    
    def validate_signal(
        self,
        signal: AlphaSignal,
        cost_aware_edge: float,
        validation_result,
    ) -> bool:
        """
        Validate signal for deployment.
        
        Args:
            signal: Signal to validate
            cost_aware_edge: Net edge after costs
            validation_result: Filter validation result
            
        Returns:
            True if validation passed
        """
        signal.net_edge = cost_aware_edge
        return signal.validate(cost_aware_edge, validation_result)
    
    def deploy_signal(self, signal: AlphaSignal) -> None:
        """Deploy validated signal."""
        signal.deploy()
    
    def get_active_signals(self, symbol: Optional[str] = None) -> list[AlphaSignal]:
        """
        Get active signals, optionally filtered by symbol.
        
        Args:
            symbol: Optional symbol filter
            
        Returns:
            List of active signals
        """
        if symbol:
            signal_ids = self._signals_by_symbol.get(symbol, [])
            return [s for sid, s in self._signals.items() 
                   if sid in signal_ids and s.is_active]
        
        return [s for s in self._signals.values() if s.is_active]
    
    def get_signals_by_state(self, state: SignalState) -> list[AlphaSignal]:
        """Get all signals in a specific state."""
        return [s for s in self._signals.values() if s.state == state]
    
    def evaluate_kill_conditions(self) -> list[tuple[AlphaSignal, KillReason]]:
        """
        Evaluate kill conditions for all active signals.
        
        Returns:
            List of (signal, reason) tuples for signals to kill
        """
        to_kill = []
        now = time.time()
        
        for signal in self.get_active_signals():
            kill_reason = self._check_kill_conditions(signal, now)
            
            if kill_reason:
                to_kill.append((signal, kill_reason))
        
        for signal, reason in to_kill:
            signal.kill(reason)
        
        return to_kill
    
    def _check_kill_conditions(
        self,
        signal: AlphaSignal,
        now: float
    ) -> Optional[KillReason]:
        """
        Check if signal should be killed.
        
        Returns:
            KillReason if should kill, None otherwise
        """
        metrics = signal.metrics
        
        if metrics.trade_count < self.config.min_trades_before_kill:
            return None
        
        if metrics.sharpe < self.config.min_sharpe and metrics.trade_count >= 10:
            return KillReason.POOR_SHARPE
        
        if metrics.max_drawdown > self.config.max_drawdown_contribution:
            return KillReason.DRAWDOWN
        
        mean_error = np.mean(list(metrics.prediction_errors)) if metrics.prediction_errors else 0
        if abs(mean_error) > self.config.max_prediction_error:
            return KillReason.PREDICTION_ERROR
        
        age_days = (now - signal.created_at) / 86400
        if age_days > self.config.kill_after_days:
            return KillReason.EXPIRED
        
        if metrics.trade_count > self.config.kill_after_trades:
            return KillReason.EXPIRED
        
        return None
    
    def get_pool_stats(self) -> dict:
        """Get aggregate pool statistics."""
        all_signals = list(self._signals.values())
        active = [s for s in all_signals if s.is_active]
        killed = [s for s in all_signals if s.state == SignalState.KILLED]
        
        total_pnl = sum(s.metrics.total_pnl for s in active)
        
        return {
            "total_signals": len(all_signals),
            "active": len(active),
            "killed": len(killed),
            "active_pnl": total_pnl,
            "avg_confidence": np.mean([s.confidence for s in active]) if active else 0,
            "avg_edge": np.mean([s.net_edge for s in active]) if active else 0,
        }
    
    def reset(self) -> None:
        """Reset all signals."""
        self._signals.clear()
        self._signals_by_symbol.clear()
