"""
Stream Processors - Event-driven processors for the trading pipeline.

Each processor:
- Subscribes to specific event types
- Processes events and produces new events
- Is stateless and idempotent
- Has priority for scheduling
"""

from processors.base_processor import BaseProcessor
from processors.feature_processor import FeatureProcessor
from processors.alpha_processor import AlphaProcessor
from processors.edge_estimation import EdgeEstimationEngine
from processors.edge_truth import EdgeTruthEngine
from processors.positive_expectation import PositiveExpectationEngine
from processors.regime_processor import RegimeProcessor
from processors.reality_gap import RealityGapMonitor
from processors.portfolio_processor import PortfolioProcessor
from processors.risk_processor import RiskProcessor
from processors.execution_processor import ExecutionProcessor
from processors.learning_processor import LearningProcessor

__all__ = [
    'BaseProcessor',
    'FeatureProcessor',
    'AlphaProcessor',
    'EdgeEstimationEngine',
    'EdgeTruthEngine',
    'PositiveExpectationEngine',
    'RegimeProcessor',
    'RealityGapMonitor',
    'PortfolioProcessor',
    'RiskProcessor',
    'ExecutionProcessor',
    'LearningProcessor',
]
