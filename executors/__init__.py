"""
Executors Module - Smart Order Routing and Execution Planning

This module implements the execution abstraction for the trading system:

1. EXECUTION PLANNER:
   - Converts allocation into execution intent
   - Models queue position and splitting
   - Calculates urgency and slippage limits

2. SMART ORDER ROUTER (SOR):
   - Scores routes by fill_probability / total_cost
   - Multi-exchange routing
   - Order splitting
   - Predictive slippage
   - Dynamic rerouting
   - Learning from fills

Author: LVR Trading System
"""

from .planner import ExecutionPlanner, ExecutionIntent, SplitConfig
from .router import SmartOrderRouter, Route, RouteScore
from .fill_model import FillPredictor, FillPrediction

__all__ = [
    "ExecutionPlanner",
    "ExecutionIntent",
    "SplitConfig",
    "SmartOrderRouter",
    "Route",
    "RouteScore",
    "FillPredictor",
    "FillPrediction",
]

__version__ = "1.0.0"
