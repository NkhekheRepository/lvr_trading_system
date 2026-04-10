"""
Control Engines - Trading system control logic.

Engines provide higher-level control over trading decisions:
- Capital Efficiency: Optimizes capital allocation
- Execution Quality: Monitors execution metrics
- Drawdown Analyzer: Tracks and responds to drawdowns
- Strategy Survival: Ensures strategy viability
- Trade Scarcity: Handles low trade frequency
"""

from engines.capital_efficiency import CapitalEfficiencyEngine
from engines.execution_quality import ExecutionQualityEngine
from engines.drawdown_analyzer import DrawdownAnalyzer
from engines.strategy_survival import StrategySurvivalEngine
from engines.trade_scarcity import TradeRateGovernor

__all__ = [
    'CapitalEfficiencyEngine',
    'ExecutionQualityEngine',
    'DrawdownAnalyzer',
    'StrategySurvivalEngine',
    'TradeRateGovernor',
]
