"""
Strategy Survival Engine - Ensures strategy can survive adverse conditions.

Monitors strategy health and initiates protective shutdowns.
"""

import logging
from typing import Optional
from dataclasses import asdict
from datetime import datetime

from core.event import Event, EventType
from core.bus import EventBus
from core.state import DistributedState

logger = logging.getLogger(__name__)


class StrategySurvivalEngine:
    """
    Ensures strategy survival through adverse conditions.
    
    Survival checks:
    - Win rate thresholds
    - Sharpe ratio minimums
    - Trade frequency sanity
    - Correlation with market
    """
    
    MIN_WIN_RATE = 0.40
    MIN_SHARPE = 0.5
    MIN_TRADE_FREQUENCY = 0.1
    SURVIVAL_WINDOW_TRADES = 50
    
    def __init__(
        self,
        bus: EventBus,
        state: Optional[DistributedState] = None,
        survival_score_threshold: float = 0.3,
    ):
        self.bus = bus
        self.state = state
        self.survival_score_threshold = survival_score_threshold
        
        self._trade_pnls: list[float] = []
        self._trade_times: list[int] = []
        self._survival_score: float = 1.0
        self._health_status: str = "HEALTHY"
        self._protective_actions: list[str] = []
        
    async def assess_survival(
        self,
        recent_trades: list[dict],
        portfolio_metrics: dict
    ) -> tuple[bool, str, list[str]]:
        """
        Assess if strategy can survive.
        
        Returns:
            (can_survive, status, protective_actions)
        """
        if not recent_trades:
            return True, "NO_DATA", []
        
        self._update_trade_history(recent_trades)
        
        win_rate = self._calculate_win_rate()
        sharpe = self._calculate_sharpe_ratio()
        trade_frequency = self._calculate_trade_frequency()
        
        survival_factors = {
            'win_rate': self._evaluate_win_rate(win_rate),
            'sharpe': self._evaluate_sharpe(sharpe),
            'frequency': self._evaluate_frequency(trade_frequency),
            'drawdown': self._evaluate_drawdown(portfolio_metrics),
            'consistency': self._evaluate_consistency(),
        }
        
        survival_values = list(survival_factors.values())
        if any(v == 0 for v in survival_values):
            self._survival_score = 0.0
        else:
            product = 1.0
            for v in survival_values:
                product *= v
            self._survival_score = max(0.0, min(1.0, product))
        
        protective_actions = []
        status = "HEALTHY"
        
        if self._survival_score < self.survival_score_threshold:
            status = "CRITICAL"
            protective_actions = [
                "halt_new_trades",
                "tighten_stops",
                "reduce_exposure",
            ]
        elif survival_factors['win_rate'] < 0.3:
            status = "WARNING"
            protective_actions.append("review_entry_criteria")
        elif survival_factors['sharpe'] < 0.3:
            status = "WARNING"
            protective_actions.append("reduce_risk_per_trade")
        
        self._health_status = status
        self._protective_actions = protective_actions
        
        await self._update_survival_state(survival_factors, status)
        
        if protective_actions:
            await self._emit_survival_alert(status, protective_actions)
        
        return len(protective_actions) == 0 or status == "WARNING", status, protective_actions
    
    def _update_trade_history(self, recent_trades: list[dict]) -> None:
        """Update internal trade history."""
        for trade in recent_trades[-self.SURVIVAL_WINDOW_TRADES:]:
            self._trade_pnls.append(trade.get('pnl', 0))
            self._trade_times.append(trade.get('timestamp', 0))
        
        if len(self._trade_pnls) > self.SURVIVAL_WINDOW_TRADES:
            self._trade_pnls = self._trade_pnls[-self.SURVIVAL_WINDOW_TRADES:]
            self._trade_times = self._trade_times[-self.SURVIVAL_WINDOW_TRADES:]
    
    def _calculate_win_rate(self) -> float:
        """Calculate win rate from recent trades."""
        if not self._trade_pnls:
            return 0.5
        
        wins = sum(1 for pnl in self._trade_pnls if pnl > 0)
        return wins / len(self._trade_pnls)
    
    def _calculate_sharpe_ratio(self) -> float:
        """Calculate Sharpe ratio from trade PnLs."""
        if len(self._trade_pnls) < 10:
            return 1.0
        
        mean_pnl = sum(self._trade_pnls) / len(self._trade_pnls)
        variance = sum((p - mean_pnl) ** 2 for p in self._trade_pnls) / len(self._trade_pnls)
        std = variance ** 0.5
        
        if std == 0:
            return 0
        
        return mean_pnl / std * (252 ** 0.5)
    
    def _calculate_trade_frequency(self) -> float:
        """Calculate trades per day."""
        if len(self._trade_times) < 2:
            return 1.0
        
        time_span = self._trade_times[-1] - self._trade_times[0]
        if time_span == 0:
            return 1.0
        
        days = time_span / (24 * 60 * 60 * 1000)
        return len(self._trade_pnls) / days if days > 0 else 1.0
    
    def _evaluate_win_rate(self, win_rate: float) -> float:
        """Evaluate win rate factor."""
        if win_rate >= self.MIN_WIN_RATE * 1.5:
            return 1.0
        elif win_rate >= self.MIN_WIN_RATE:
            return 0.7
        elif win_rate >= self.MIN_WIN_RATE * 0.75:
            return 0.4
        else:
            return 0.1
    
    def _evaluate_sharpe(self, sharpe: float) -> float:
        """Evaluate Sharpe ratio factor."""
        if sharpe >= self.MIN_SHARPE * 2:
            return 1.0
        elif sharpe >= self.MIN_SHARPE:
            return 0.7
        elif sharpe >= self.MIN_SHARPE * 0.5:
            return 0.4
        else:
            return 0.2
    
    def _evaluate_frequency(self, frequency: float) -> float:
        """Evaluate trade frequency factor."""
        if frequency >= self.MIN_TRADE_FREQUENCY * 5:
            return 1.0
        elif frequency >= self.MIN_TRADE_FREQUENCY:
            return 0.7
        elif frequency >= self.MIN_TRADE_FREQUENCY * 0.5:
            return 0.5
        else:
            return 0.3
    
    def _evaluate_drawdown(self, portfolio_metrics: dict) -> float:
        """Evaluate drawdown factor."""
        drawdown = portfolio_metrics.get('drawdown_pct', 0)
        
        if drawdown < 0.05:
            return 1.0
        elif drawdown < 0.10:
            return 0.7
        elif drawdown < 0.15:
            return 0.4
        else:
            return 0.1
    
    def _evaluate_consistency(self) -> float:
        """Evaluate trade PnL consistency."""
        if len(self._trade_pnls) < 10:
            return 0.5
        
        positive = sum(1 for p in self._trade_pnls if p > 0)
        negative = sum(1 for p in self._trade_pnls if p < 0)
        
        ratio = positive / negative if negative > 0 else positive
        
        if ratio >= 1.5:
            return 1.0
        elif ratio >= 1.0:
            return 0.7
        elif ratio >= 0.5:
            return 0.4
        else:
            return 0.2
    
    async def _update_survival_state(
        self,
        factors: dict,
        status: str
    ) -> None:
        if not self.state:
            return
            
        await self.state.set(
            key="survival:global",
            value={
                'survival_score': self._survival_score,
                'health_status': status,
                'factors': factors,
                'protective_actions': self._protective_actions,
                'trade_count': len(self._trade_pnls),
                'updated_at': int(datetime.now().timestamp() * 1000),
            },
            trace_id="strategy_survival_engine",
        )
    
    async def _emit_survival_alert(
        self,
        status: str,
        actions: list[str]
    ) -> None:
        """Emit survival alert."""
        logger.warning(
            f"Strategy survival {status}: {actions}",
            extra={
                'status': status,
                'survival_score': self._survival_score,
                'actions': actions,
            }
        )
        
        if self.bus:
            alert_event = Event.create(
                event_type=EventType.STRATEGY_TERMINATION,
                payload={
                    'reason': f"survival_score_low_{self._survival_score:.2f}",
                    'status': status,
                    'actions': actions,
                },
                source="strategy_survival_engine",
            )
            await self.bus.publish(alert_event)
    
    async def get_survival_report(self) -> dict:
        """Get survival assessment report."""
        return {
            'survival_score': self._survival_score,
            'health_status': self._health_status,
            'protective_actions': self._protective_actions,
            'trade_count': len(self._trade_pnls),
            'can_continue': self._survival_score >= self.survival_score_threshold,
        }
