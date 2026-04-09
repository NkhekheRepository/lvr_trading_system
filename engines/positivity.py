"""
Staged Positivity Governor - System-level profitability governor.

Monitors system edge and transitions through phases:
- NORMAL: Full operation
- CAUTION: Reduced size, no new signals
- DERISK: Block new signals, reduce exposure
- HARD_STOP: Block all trades, force close

Features:
- Threshold-based transitions with persistence
- Hysteresis to prevent oscillation
- Drawdown integration
- Gradual recovery
"""

import logging
import numpy as np
from typing import Optional, NamedTuple
from dataclasses import asdict
from datetime import datetime
from collections import deque

from core.event import Event, EventType
from core.bus import EventBus
from core.state import DistributedState

logger = logging.getLogger(__name__)


class Phase(NamedTuple):
    name: str
    exposure_mult: float
    block_signals: bool
    block_all: bool
    force_close: bool


class PhaseConfig:
    NORMAL = Phase("NORMAL", 1.0, False, False, False)
    CAUTION = Phase("CAUTION", 0.5, False, False, False)
    DERISK = Phase("DERISK", 0.2, True, False, False)
    HARD_STOP = Phase("HARD_STOP", 0.0, True, True, True)
    
    _RANKING = {"NORMAL": 0, "CAUTION": 1, "DERISK": 2, "HARD_STOP": 3}


class StagedPositivityGovernor:
    """
    System-level positivity governor with staged transitions.
    
    Edge = rolling(expected_return − fees − slippage − risk_penalty)
    
    Transitions based on:
    - System edge vs soft/hard thresholds
    - Drawdown vs limits
    - Edge persistence over time
    """
    
    EDGE_SOFT_THRESHOLD = 0.0
    EDGE_HARD_MULTIPLIER = 0.5
    PERSISTENCE_WINDOW = 30
    RECOVERY_WINDOW = 50
    MIN_TRANSITION_INTERVAL = 300
    
    DRAWDOWN_LIMIT = 0.10
    SEVERE_DRAWDOWN = 0.20
    DRAWDOWN_RECOVERY = 0.05
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        edge_std: float = 0.001,
    ):
        self.bus = bus
        self.state = state
        self.edge_std = edge_std
        
        self._phase = PhaseConfig.NORMAL
        self._edge_hard_threshold = -edge_std * self.EDGE_HARD_MULTIPLIER
        
        self._edge_history = deque(maxlen=100)
        self._phase_history: list[dict] = []
        self._last_transition_time: float = 0
        self._transition_count = 0
        
        self._current_exposure_mult = 1.0
        self._recovery_start_time: Optional[float] = None
    
    @property
    def phase(self) -> str:
        return self._phase.name
    
    def evaluate(
        self,
        system_edge: float,
        current_drawdown: float,
        portfolio_metrics: Optional[dict] = None
    ) -> 'PhaseDecision':
        """
        Evaluate system state and determine phase.
        
        Args:
            system_edge: Rolling system edge (expected_return - costs)
            current_drawdown: Current portfolio drawdown (0-1)
            portfolio_metrics: Optional additional metrics
            
        Returns:
            PhaseDecision with action details
        """
        self._edge_history.append(system_edge)
        
        persistence_window = min(self.PERSISTENCE_WINDOW, len(self._edge_history))
        recent_edge = np.mean(list(self._edge_history)[-persistence_window:])
        
        new_phase = self._determine_phase(recent_edge, current_drawdown, portfolio_metrics)
        
        if new_phase.name != self._phase.name:
            if self._can_transition(new_phase.name):
                self._transition(new_phase, system_edge, current_drawdown)
            else:
                pass
        
        return PhaseDecision(
            phase=self._phase.name,
            exposure_mult=self._phase.exposure_mult,
            block_signals=self._phase.block_signals,
            block_all=self._phase.block_all,
            force_close=self._phase.force_close,
            system_edge=system_edge,
            recent_edge=recent_edge,
            current_drawdown=current_drawdown,
            transition_reason=self._phase_history[-1]['reason'] if self._phase_history else None,
        )
    
    def _determine_phase(
        self,
        recent_edge: float,
        drawdown: float,
        metrics: Optional[dict]
    ) -> Phase:
        """Determine target phase based on conditions."""
        
        if self._is_hard_stop_condition(recent_edge, drawdown):
            return PhaseConfig.HARD_STOP
        
        if self._is_derisk_condition(recent_edge, drawdown, metrics):
            return PhaseConfig.DERISK
        
        if self._is_caution_condition(recent_edge):
            return PhaseConfig.CAUTION
        
        if self._is_recovering():
            return self._get_recovery_phase()
        
        return PhaseConfig.NORMAL
    
    def _is_hard_stop_condition(self, edge: float, drawdown: float) -> bool:
        """Check if hard stop conditions are met."""
        if drawdown >= self.SEVERE_DRAWDOWN:
            return True
        if edge < self._edge_hard_threshold * 2:
            return True
        return False
    
    def _is_derisk_condition(
        self,
        edge: float,
        drawdown: float,
        metrics: Optional[dict]
    ) -> bool:
        """Check if de-risk conditions are met."""
        if edge < self._edge_hard_threshold:
            return True
        if drawdown > self.DRAWDOWN_LIMIT * 0.8 and self._is_drawdown_increasing():
            return True
        return False
    
    def _is_caution_condition(self, edge: float) -> bool:
        """Check if caution conditions are met."""
        return edge < self.EDGE_SOFT_THRESHOLD
    
    def _is_drawdown_increasing(self) -> bool:
        """Check if drawdown is increasing over time."""
        if len(self._edge_history) < 5:
            return False
        
        recent = list(self._edge_history)[-3:]
        historical = list(self._edge_history)[:-3]
        
        if not historical:
            return False
        
        recent_dd = np.mean([abs(e) for e in recent])
        historical_dd = np.mean([abs(e) for e in historical])
        
        return recent_dd > historical_dd * 1.1
    
    def _is_recovering(self) -> bool:
        """Check if system is in recovery mode."""
        return self._recovery_start_time is not None
    
    def _get_recovery_phase(self) -> Phase:
        """Get appropriate phase during recovery."""
        if not self._phase_history:
            return PhaseConfig.NORMAL
        
        recent_transitions = [
            t for t in self._phase_history
            if t.get('timestamp', 0) > self._recovery_start_time - 1000
        ]
        
        if any(t['to'] in ('DERISK', 'HARD_STOP') for t in recent_transitions):
            return PhaseConfig.CAUTION
        
        return PhaseConfig.NORMAL
    
    def _can_transition(self, new_phase: str) -> bool:
        """Check if transition is allowed (hysteresis)."""
        now = datetime.now().timestamp()
        
        if PhaseConfig._RANKING[new_phase] > PhaseConfig._RANKING[self._phase.name]:
            if now - self._last_transition_time < self.MIN_TRANSITION_INTERVAL:
                return False
        
        return True
    
    def _transition(self, new_phase: Phase, edge: float, drawdown: float) -> None:
        """Execute phase transition."""
        old_phase = self._phase.name
        self._phase = new_phase
        self._transition_count += 1
        self._last_transition_time = datetime.now().timestamp()
        
        transition = {
            'from': old_phase,
            'to': new_phase.name,
            'edge': edge,
            'drawdown': drawdown,
            'timestamp': self._last_transition_time,
            'reason': self._get_transition_reason(old_phase, new_phase.name, edge, drawdown),
        }
        self._phase_history.append(transition)
        
        if len(self._phase_history) > 100:
            self._phase_history = self._phase_history[-100:]
        
        if PhaseConfig._RANKING[new_phase.name] > PhaseConfig._RANKING[old_phase]:
            self._recovery_start_time = None
        else:
            self._recovery_start_time = self._last_transition_time
        
        self._emit_state_change(transition)
        
        logger.warning(
            f"Positivity governor transition: {old_phase} -> {new_phase.name}",
            extra=transition
        )
    
    def _get_transition_reason(
        self,
        from_phase: str,
        to_phase: str,
        edge: float,
        drawdown: float
    ) -> str:
        """Generate human-readable transition reason."""
        reasons = []
        
        if drawdown >= self.SEVERE_DRAWDOWN:
            reasons.append(f"severe_drawdown_{drawdown:.2%}")
        elif drawdown >= self.DRAWDOWN_LIMIT:
            reasons.append(f"drawdown_limit_{drawdown:.2%}")
        
        if edge < self._edge_hard_threshold * 2:
            reasons.append(f"critical_edge_{edge:.6f}")
        elif edge < self._edge_hard_threshold:
            reasons.append(f"negative_edge_{edge:.6f}")
        elif edge < self.EDGE_SOFT_THRESHOLD:
            reasons.append(f"soft_edge_{edge:.6f}")
        
        return "; ".join(reasons) if reasons else "unknown"
    
    def _emit_state_change(self, transition: dict) -> None:
        """Emit state change event."""
        if not self.bus:
            return
        
        event = Event.create(
            event_type=EventType.SYSTEM_STATE_CHANGED,
            payload={
                'governor': 'positivity',
                'from_phase': transition['from'],
                'to_phase': transition['to'],
                'reason': transition['reason'],
                'edge': transition['edge'],
                'drawdown': transition['drawdown'],
                'exposure_mult': self._phase.exposure_mult,
            },
            source="positivity_governor",
        )
        
        asyncio.create_task(self.bus.publish(event))
    
    def should_block_signals(self) -> bool:
        """Check if new signals should be blocked."""
        return self._phase.block_signals
    
    def should_force_close(self) -> bool:
        """Check if positions should be force closed."""
        return self._phase.force_close
    
    def get_exposure_multiplier(self) -> float:
        """Get current exposure multiplier."""
        return self._phase.exposure_mult
    
    def record_outcome(self, edge: float, pnl: float) -> None:
        """Record trade outcome for tracking."""
        self._edge_history.append(edge)
    
    async def get_governor_report(self) -> dict:
        """Get comprehensive governor report."""
        recent_edge = np.mean(list(self._edge_history)[-self.PERSISTENCE_WINDOW:]) if self._edge_history else 0
        
        return {
            'phase': self._phase.name,
            'exposure_mult': self._phase.exposure_mult,
            'block_signals': self._phase.block_signals,
            'block_all': self._phase.block_all,
            'force_close': self._phase.force_close,
            'system_edge': self._edge_history[-1] if self._edge_history else 0,
            'recent_edge': recent_edge,
            'transition_count': self._transition_count,
            'time_in_phase': (
                datetime.now().timestamp() - self._last_transition_time
                if self._last_transition_time else 0
            ),
            'phase_history': self._phase_history[-10:],
        }
    
    async def update_state(self) -> None:
        """Persist governor state."""
        if not self.state:
            return
        
        await self.state.set(
            key="governor:positivity",
            value={
                'phase': self._phase.name,
                'exposure_mult': self._phase.exposure_mult,
                'transition_count': self._transition_count,
                'last_transition': self._last_transition_time,
                'edge_history': list(self._edge_history)[-50:],
                'phase_history': self._phase_history[-20:],
            },
            trace_id="positivity_governor",
        )


class PhaseDecision(NamedTuple):
    phase: str
    exposure_mult: float
    block_signals: bool
    block_all: bool
    force_close: bool
    system_edge: float
    recent_edge: float
    current_drawdown: float
    transition_reason: Optional[str]


import asyncio
