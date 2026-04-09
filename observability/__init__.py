"""
Observability - System observability components.

Components:
- Logger: Structured JSON logging
- Metrics: Prometheus metrics collection
- Tracer: Distributed tracing
- Credentials: Secure credentials management
"""

from observability.logger import TradingLogger, get_logger, set_trace_context, clear_trace_context
from observability.metrics import TradingMetrics, LatencyTracker, MetricConfig
from observability.tracer import Tracer, SpanContext, get_tracer, get_current_trace_id
from observability.credentials import CredentialsManager, get_credentials_manager

__all__ = [
    'TradingLogger',
    'get_logger',
    'set_trace_context',
    'clear_trace_context',
    'TradingMetrics',
    'LatencyTracker',
    'MetricConfig',
    'Tracer',
    'SpanContext',
    'get_tracer',
    'get_current_trace_id',
    'CredentialsManager',
    'get_credentials_manager',
]
