"""
Structured Logger - Production logging for the trading system.

Provides structured logging with context propagation.
"""

import logging
import json
import sys
from datetime import datetime
from typing import Any, Optional
from contextvars import ContextVar

trace_id_var: ContextVar[str] = ContextVar('trace_id', default='')
span_id_var: ContextVar[str] = ContextVar('span_id', default='')


class StructuredFormatter(logging.Formatter):
    """JSON structured logging formatter."""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'trace_id': trace_id_var.get(),
            'span_id': span_id_var.get(),
        }
        
        if hasattr(record, 'trace_id'):
            log_data['trace_id'] = record.trace_id
        if hasattr(record, 'span_id'):
            log_data['span_id'] = record.span_id
        
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        for key, value in record.__dict__.items():
            if key not in ('name', 'msg', 'args', 'created', 'filename', 'funcName',
                          'levelname', 'lineno', 'module', 'msecs', 'message',
                          'pathname', 'process', 'processName', 'relativeCreated',
                          'stack_info', 'exc_info', 'exc_text', 'thread', 'threadName',
                          'trace_id', 'span_id'):
                if not key.startswith('_'):
                    log_data[key] = value
        
        return json.dumps(log_data, default=str)


class TradingLogger:
    """
    Structured logger for trading system.
    
    Features:
    - JSON structured output
    - Trace ID propagation
    - Context enrichment
    - Level-based filtering
    """
    
    def __init__(
        self,
        name: str,
        level: int = logging.INFO,
    ):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        
        if not self.logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(StructuredFormatter())
            self.logger.addHandler(handler)
    
    def _log(
        self,
        level: int,
        message: str,
        **kwargs: Any
    ) -> None:
        extra = {}
        for key, value in kwargs.items():
            extra[key] = value
        
        if trace_id_var.get():
            extra['trace_id'] = trace_id_var.get()
        if span_id_var.get():
            extra['span_id'] = span_id_var.get()
        
        self.logger.log(level, message, extra=extra)
    
    def debug(self, message: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, message, **kwargs)
    
    def info(self, message: str, **kwargs: Any) -> None:
        self._log(logging.INFO, message, **kwargs)
    
    def warning(self, message: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, message, **kwargs)
    
    def error(self, message: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, message, **kwargs)
    
    def critical(self, message: str, **kwargs: Any) -> None:
        self._log(logging.CRITICAL, message, **kwargs)
    
    def with_context(
        self,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        **kwargs: Any
    ) -> 'ContextLogger':
        """Create a context-aware logger."""
        return ContextLogger(self, trace_id=trace_id, span_id=span_id, **kwargs)
    
    def log_event(
        self,
        event_type: str,
        symbol: Optional[str] = None,
        **kwargs: Any
    ) -> None:
        """Log an event with standard structure."""
        self.info(
            f"Event: {event_type}",
            event_type=event_type,
            symbol=symbol,
            **kwargs
        )
    
    def log_trade(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        **kwargs: Any
    ) -> None:
        """Log a trade execution."""
        self.info(
            f"Trade: {side} {quantity} {symbol} @ {price}",
            event_type='trade',
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            **kwargs
        )
    
    def log_signal(
        self,
        symbol: str,
        direction: int,
        strength: float,
        **kwargs: Any
    ) -> None:
        """Log an alpha signal."""
        self.info(
            f"Signal: {symbol} direction={direction} strength={strength:.3f}",
            event_type='signal',
            symbol=symbol,
            direction=direction,
            strength=strength,
            **kwargs
        )
    
    def log_risk_event(
        self,
        event_type: str,
        symbol: Optional[str] = None,
        reason: str = '',
        **kwargs: Any
    ) -> None:
        """Log a risk event."""
        self.warning(
            f"Risk: {event_type} reason={reason}",
            event_type=f'risk_{event_type}',
            symbol=symbol,
            reason=reason,
            **kwargs
        )


class ContextLogger:
    """Logger with persistent context."""
    
    def __init__(
        self,
        logger: TradingLogger,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        **context: Any
    ):
        self._logger = logger
        self._context = context
        self._trace_id = trace_id
        self._span_id = span_id
    
    def _apply_context(self, **kwargs: Any) -> dict:
        context = self._context.copy()
        context.update(kwargs)
        return context
    
    def debug(self, message: str, **kwargs: Any) -> None:
        self._logger.debug(message, **self._apply_context(**kwargs))
    
    def info(self, message: str, **kwargs: Any) -> None:
        self._logger.info(message, **self._apply_context(**kwargs))
    
    def warning(self, message: str, **kwargs: Any) -> None:
        self._logger.warning(message, **self._apply_context(**kwargs))
    
    def error(self, message: str, **kwargs: Any) -> None:
        self._logger.error(message, **self._apply_context(**kwargs))
    
    def critical(self, message: str, **kwargs: Any) -> None:
        self._logger.critical(message, **self._apply_context(**kwargs))
    
    def child(self, **kwargs: Any) -> 'ContextLogger':
        """Create a child context logger."""
        return ContextLogger(
            self._logger,
            trace_id=self._trace_id,
            span_id=self._span_id,
            **self._apply_context(**kwargs)
        )


def set_trace_context(trace_id: str, span_id: Optional[str] = None) -> None:
    """Set trace context for current coroutine."""
    trace_id_var.set(trace_id)
    if span_id:
        span_id_var.set(span_id)


def clear_trace_context() -> None:
    """Clear trace context."""
    trace_id_var.set('')
    span_id_var.set('')


def get_logger(name: str, level: int = logging.INFO) -> TradingLogger:
    """Get a structured logger instance."""
    return TradingLogger(name, level)
