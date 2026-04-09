"""
LVR Trading System - Production-grade autonomous trading system.
"""

__version__ = "1.0.0"
__author__ = "LVR Trading Team"

from app.schemas import (
    Alert, AlertSeverity, AttributionResult, BayesianState,
    ExecutionMode, ExecutionResult, FeatureVector, FillEvent,
    MetricsSnapshot, Order, OrderBookSnapshot, OrderRequest, OrderStatus,
    OrderType, Portfolio, Position, ProtectionLevel, RejectEvent,
    RiskCheckResult, RiskState, Side, Signal, SystemEvent, TimeInForce,
    TradeTick, EventType
)

__all__ = [
    "TradeTick", "OrderBookSnapshot", "FeatureVector", "Signal",
    "OrderRequest", "Order", "ExecutionResult", "FillEvent", "RejectEvent",
    "Position", "Portfolio", "RiskState", "RiskCheckResult", "BayesianState",
    "AttributionResult", "MetricsSnapshot", "Alert", "AlertSeverity",
    "SystemEvent", "EventType", "Side", "OrderType", "OrderStatus",
    "TimeInForce", "ExecutionMode", "ProtectionLevel",
]
