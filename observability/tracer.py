"""
Distributed Tracer - Distributed tracing for the trading system.

Provides trace context propagation across async boundaries.
"""

import uuid
import time
from typing import Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from contextvars import ContextVar
from collections import deque

trace_context: ContextVar[dict] = ContextVar('trace_context', default=None)


@dataclass
class Span:
    """Represents a trace span."""
    span_id: str
    trace_id: str
    name: str
    start_time: float
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None
    tags: dict = field(default_factory=dict)
    logs: list = field(default_factory=list)
    parent_span_id: Optional[str] = None
    
    def finish(self, end_time: Optional[float] = None) -> None:
        """Finish the span."""
        if end_time is None:
            end_time = time.perf_counter()
        self.end_time = end_time
        self.duration_ms = (end_time - self.start_time) * 1000
    
    def add_tag(self, key: str, value: Any) -> None:
        """Add a tag to the span."""
        self.tags[key] = value
    
    def add_log(self, message: str, **kwargs: Any) -> None:
        """Add a log event to the span."""
        self.logs.append({
            'timestamp': datetime.utcnow().isoformat(),
            'message': message,
            **kwargs,
        })


class Tracer:
    """
    Distributed tracer for the trading system.
    
    Features:
    - Trace context propagation
    - Span creation and management
    - Automatic parent-child relationships
    - Sampling support
    """
    
    MAX_SPANS = 1000
    
    def __init__(
        self,
        service_name: str,
        sample_rate: float = 1.0,
    ):
        self.service_name = service_name
        self.sample_rate = sample_rate
        self._spans: deque = deque(maxlen=self.MAX_SPANS)
        self._active_spans: dict[str, Span] = {}
    
    def start_trace(self, trace_id: Optional[str] = None) -> str:
        """Start a new trace."""
        if trace_id is None:
            trace_id = self._generate_trace_id()
        
        ctx = {
            'trace_id': trace_id,
            'service_name': self.service_name,
            'start_time': time.perf_counter(),
        }
        trace_context.set(ctx)
        
        return trace_id
    
    def end_trace(self) -> dict:
        """End current trace and return summary."""
        ctx = trace_context.get()
        if ctx is None:
            return {}
        
        duration = (time.perf_counter() - ctx['start_time']) * 1000
        
        summary = {
            'trace_id': ctx['trace_id'],
            'service_name': self.service_name,
            'duration_ms': duration,
            'span_count': len(self._spans),
        }
        
        trace_context.set(None)
        
        return summary
    
    def start_span(
        self,
        name: str,
        parent_span_id: Optional[str] = None,
        tags: Optional[dict] = None,
    ) -> Span:
        """Start a new span."""
        ctx = trace_context.get()
        
        trace_id = ctx['trace_id'] if ctx else self._generate_trace_id()
        
        if parent_span_id is None and ctx:
            parent_span = self._get_current_span()
            if parent_span:
                parent_span_id = parent_span.span_id
        
        span_id = self._generate_span_id()
        
        span = Span(
            span_id=span_id,
            trace_id=trace_id,
            name=name,
            start_time=time.perf_counter(),
            parent_span_id=parent_span_id,
        )
        
        if tags:
            for key, value in tags.items():
                span.add_tag(key, value)
        
        self._spans.append(span)
        self._active_spans[span_id] = span
        
        return span
    
    def end_span(self, span: Span) -> None:
        """End a span."""
        if not span.end_time:
            span.finish()
        
        if span.span_id in self._active_spans:
            del self._active_spans[span.span_id]
    
    def _get_current_span(self) -> Optional[Span]:
        """Get the current active span."""
        if self._active_spans:
            return list(self._active_spans.values())[-1]
        return None
    
    def _generate_trace_id(self) -> str:
        """Generate a unique trace ID."""
        return uuid.uuid4().hex[:16]
    
    def _generate_span_id(self) -> str:
        """Generate a unique span ID."""
        return uuid.uuid4().hex[:8]
    
    def get_active_spans(self) -> list[Span]:
        """Get all active spans."""
        return list(self._active_spans.values())
    
    def get_trace_summary(self) -> dict:
        """Get summary of current trace."""
        ctx = trace_context.get()
        if ctx is None:
            return {}
        
        active_spans = self.get_active_spans()
        
        return {
            'trace_id': ctx['trace_id'],
            'service_name': self.service_name,
            'active_span_count': len(active_spans),
            'total_span_count': len(self._spans),
            'spans': [
                {
                    'span_id': s.span_id,
                    'name': s.name,
                    'duration_ms': s.duration_ms,
                    'tags': s.tags,
                }
                for s in self._spans
            ],
        }


class SpanContext:
    """Context manager for span lifecycle."""
    
    def __init__(
        self,
        tracer: Tracer,
        name: str,
        tags: Optional[dict] = None,
    ):
        self.tracer = tracer
        self.name = name
        self.tags = tags
        self.span: Optional[Span] = None
    
    def __enter__(self) -> 'SpanContext':
        self.span = self.tracer.start_span(self.name, tags=self.tags)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.span:
            if exc_type is not None:
                self.span.add_tag('error', True)
                self.span.add_tag('error_type', exc_type.__name__)
                self.span.add_tag('error_message', str(exc_val))
            
            self.tracer.end_span(self.span)
    
    def add_tag(self, key: str, value: Any) -> None:
        """Add a tag to the current span."""
        if self.span:
            self.span.add_tag(key, value)
    
    def add_log(self, message: str, **kwargs: Any) -> None:
        """Add a log to the current span."""
        if self.span:
            self.span.add_log(message, **kwargs)


def get_tracer(service_name: str = 'trading_system') -> Tracer:
    """Get or create a tracer instance."""
    return Tracer(service_name)


def get_current_trace_id() -> Optional[str]:
    """Get current trace ID from context."""
    ctx = trace_context.get()
    return ctx['trace_id'] if ctx else None
