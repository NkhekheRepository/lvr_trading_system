"""
Control Engines - Trading system control logic.

Engines provide higher-level control over trading decisions:
- Positivity: Ensures positive edge trades
- Capital Efficiency: Optimizes capital allocation
- Execution Quality: Monitors execution metrics
- Drawdown Analyzer: Tracks and responds to drawdowns
- Strategy Survival: Ensures strategy viability
- Trade Scarcity: Handles low trade frequency
"""

from engines.positivity import PositivityEngine
from engines.capital_efficiency import CapitalEfficiencyEngine
from engines.execution_quality import ExecutionQualityEngine
from engines.drawdown_analyzer import DrawdownAnalyzer
from engines.strategy_survival import StrategySurvivalEngine
from engines.trade_scarcity import TradeScarcityEngine

__all__ = [
    'PositivityEngine',
    'CapitalEfficiencyEngine',
    'ExecutionQualityEngine',
    'DrawdownAnalyzer',
    'StrategySurvivalEngine',
    'TradeScarcityEngine',
]
