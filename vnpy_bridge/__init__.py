"""
vnpy Bridge Module - Production-Ready vn.py Gateway Integration

This module provides the Execution Abstraction Layer for LIVE trading via vn.py.
It wraps the vn.py gateway with safety features required for production use.

KEY SAFETY FEATURES:
- Execution mode gating (LVR_LIVE_CONFIRMED flag)
- Idempotency via request_id tracking
- Order state machine with strict transitions
- Failover and health monitoring
- Comprehensive audit logging

ARCHITECTURE:
    vnpy_bridge/
    ├── __init__.py      - Module exports
    ├── gateway.py       - vn.py gateway wrapper with safety
    ├── feed.py          - Real-time data feed adapter
    ├── adapter.py       - System adapter (safety layer)
    └── config.py        - Configuration

NOTE: vn.py must be installed separately. See requirements.txt.
"""

from .gateway import VnpyGateway, GatewayConfig, OrderRequest, OrderResponse, OrderStatus
from .feed import VnpyFeed, FeedConfig, TickData, BarData
from .adapter import VnpyAdapter, AdapterConfig, SafetyLevel
from .config import VnpyBridgeConfig, load_config, validate_config

__all__ = [
    "VnpyGateway",
    "GatewayConfig", 
    "OrderRequest",
    "OrderResponse",
    "OrderStatus",
    "VnpyFeed",
    "FeedConfig",
    "TickData",
    "BarData",
    "VnpyAdapter",
    "AdapterConfig",
    "SafetyLevel",
    "VnpyBridgeConfig",
    "load_config",
    "validate_config",
]

__version__ = "1.0.0"
