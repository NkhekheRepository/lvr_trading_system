"""
Alpha Factory Module - Cost-Aware Signal Generation and Lifecycle Management

This module implements the ALPHA FACTORY specification:
- Generate: Create signals from features
- Validate: Cost-aware edge, turnover/stability filters
- Deploy: Only validated signals proceed
- Monitor: Track signal performance
- Kill: Disable underperforming signals

KEY CONCEPTS:
1. Cost-Aware Edge: net_edge = raw_edge - (fees + slippage + impact)
2. Turnover Filter: Reject high turnover signals (prevents overtrading)
3. Stability Filter: Reject high variance signals (reduces noise)
4. Signal Lifecycle: Track signals from creation to death

Author: LVR Trading System
"""

from .factory import AlphaFactory, AlphaSignal
from .cost_aware import CostAwareEdge, CostComponents
from .filters import TurnoverFilter, StabilityFilter, SignalFilters
from .signal_pool import SignalPool, PoolConfig

__all__ = [
    "AlphaFactory",
    "AlphaSignal",
    "CostAwareEdge",
    "CostComponents",
    "TurnoverFilter",
    "StabilityFilter",
    "SignalFilters",
    "SignalPool",
    "PoolConfig",
]

__version__ = "1.0.0"
