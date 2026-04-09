"""
Event Schema - Core event definitions for the event-driven architecture.

This module defines the base Event class and all event types used in the
trading system. Events are the primary communication mechanism between
all system components.

Event Rules:
- ALL communication via event log
- Events must be idempotent
- Replayable from any offset
- Duplicate-safe processing
- Strict ordering per symbol partition
"""

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional, Any
import hashlib


class EventType(Enum):
    """All event types in the system."""
    # Input Events
    MARKET_TICK = "market_tick"
    ORDERBOOK_UPDATE = "orderbook_update"
    
    # Processing Events
    FEATURES_COMPUTED = "features_computed"
    ALPHA_SIGNAL = "alpha_signal"
    EDGE_ESTIMATED = "edge_estimated"
    EDGE_TRUTH = "edge_truth"
    POSITIVE_EXPECTATION = "positive_expectation"
    TRADE_DECISION = "trade_decision"
    REGIME_DETECTED = "regime_detected"
    REALITY_GAP = "reality_gap"
    
    # Output Events
    ORDER_SUBMITTED = "order_submitted"
    ORDER_PARTIAL = "order_partial"
    ORDER_FILLED = "order_filled"
    ORDER_CANCELED = "order_canceled"
    ORDER_REJECTED = "order_rejected"
    
    # State Events
    PORTFOLIO_UPDATED = "portfolio_updated"
    RISK_EVALUATED = "risk_evaluated"
    POSITION_RECONCILED = "position_reconciled"
    
    # Control Events
    ALLOCATION_UPDATE = "allocation_update"
    CAPITAL_REALLOCATION = "capital_reallocation"
    HALT_REQUEST = "halt_request"
    RESUME_REQUEST = "resume_request"
    KILL_SWITCH_TRIGGERED = "kill_switch_triggered"
    
    # Quality Events
    EXECUTION_QUALITY = "execution_quality"
    DRAWDOWN_ALERT = "drawdown_alert"
    REALITY_GAP_ALERT = "reality_gap_alert"
    STRATEGY_TERMINATION = "strategy_termination"
    RATE_LIMIT_APPLIED = "rate_limit_applied"
    
    # System Events
    SYSTEM_EVENT = "system_event"
    HEALTH_CHECK = "health_check"
    METRICS_SNAPSHOT = "metrics_snapshot"
    MODEL_UPDATED = "model_updated"
    
    # Validation Events
    DATA_VALIDATED = "data_validated"
    POSITION_MISMATCH = "position_mismatch"
    TIME_DRIFT_DETECTED = "time_drift_detected"


class EventPriority(Enum):
    """Event processing priority."""
    HIGH = 1   # Execution + Risk
    MEDIUM = 2 # Signals
    LOW = 3    # Logging


@dataclass
class Event:
    """
    Base event class for all system events.
    
    Attributes:
        event_id: Unique identifier (UUID). Used for idempotency.
        trace_id: Request tracing identifier (propagated through pipeline).
        type: Event type enum.
        symbol: Symbol this event relates to (optional).
        timestamp: Unix timestamp in milliseconds.
        sequence: Strict ordering sequence per symbol partition.
        version: Schema version for evolution.
        payload: Type-specific data.
        offset: Persistent offset in event log (set on storage).
        source: Source module/service that created this event.
    """
    event_id: str
    trace_id: str
    type: EventType
    symbol: Optional[str] = None
    timestamp: int = field(default_factory=lambda: int(datetime.now().timestamp() * 1000))
    sequence: int = 0
    version: int = 1
    payload: dict = field(default_factory=dict)
    offset: int = 0
    source: str = "unknown"
    
    def __post_init__(self):
        if not self.event_id:
            self.event_id = str(uuid.uuid4())
        if not self.trace_id:
            self.trace_id = self.event_id
            
    @property
    def event_id_hash(self) -> str:
        """Generate deterministic hash for idempotency checking."""
        content = f"{self.type.value}:{self.symbol}:{self.timestamp}:{json.dumps(self.payload, sort_keys=True)}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        data = asdict(self)
        data['type'] = self.type.value
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Event':
        """Deserialize from dictionary."""
        data = data.copy()
        data['type'] = EventType(data['type'])
        return cls(**data)
    
    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict())
    
    @classmethod
    def from_json(cls, json_str: str) -> 'Event':
        """Deserialize from JSON string."""
        return cls.from_dict(json.loads(json_str))
    
    @classmethod
    def create(
        cls,
        event_type: EventType,
        symbol: Optional[str] = None,
        payload: Optional[dict] = None,
        trace_id: Optional[str] = None,
        source: str = "unknown"
    ) -> 'Event':
        """Factory method to create a new event."""
        return cls(
            event_id=str(uuid.uuid4()),
            trace_id=trace_id or str(uuid.uuid4()),
            type=event_type,
            symbol=symbol,
            payload=payload or {},
            source=source,
        )


@dataclass
class MarketTickPayload:
    """Payload for MARKET_TICK events."""
    price: float
    bid: float
    ask: float
    volume: float
    timestamp: int
    exchange: str
    latency_ms: float = 0.0
    quality_score: float = 1.0


@dataclass
class OrderBookPayload:
    """Payload for ORDERBOOK_UPDATE events."""
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]
    timestamp: int
    exchange: str
    depth: int = 20


@dataclass
class FeaturesPayload:
    """Payload for FEATURES_COMPUTED events."""
    returns: float
    depth_zscore: float
    spread_zscore: float
    ofi: float
    volatility: float
    microstructure_score: float
    quality_metrics: dict


@dataclass
class AlphaSignalPayload:
    """Payload for ALPHA_SIGNAL events."""
    direction: int  # 1 = long, -1 = short, 0 = neutral
    strength: float
    confidence: float
    expected_edge: float
    filters_passed: list[str]
    filters_failed: list[str]
    regime: str = "unknown"


@dataclass
class EdgeEstimationPayload:
    """Payload for EDGE_ESTIMATED events."""
    expected_edge: float
    expected_return: float
    fees_bps: float
    slippage_bps: float
    latency_cost_bps: float
    risk_penalty_bps: float
    total_cost_bps: float
    confidence: float
    confidence_interval: tuple[float, float]


@dataclass
class EdgeTruthPayload:
    """Payload for EDGE_TRUTH events."""
    edge_truth_score: float  # realized / expected
    expected_edge: float
    realized_edge: float
    trade_count: int
    win_rate: float
    avg_win: float
    avg_loss: float
    is_significant: bool
    confidence: float


@dataclass
class TradeDecisionPayload:
    """Payload for TRADE_DECISION events."""
    decision: str  # ACCEPT or REJECT
    expected_edge: float
    total_cost: float
    payoff_ratio: float
    cost_edge_ratio: float
    is_significant: bool
    rejection_reason: Optional[str] = None


@dataclass
class RegimePayload:
    """Payload for REGIME_DETECTED events."""
    market_regime: str
    volatility_regime: str
    liquidity_regime: str
    risk_score: float
    use_caution: bool
    max_position_scale: float
    confidence: float
    trend_strength: float = 0.0
    momentum_score: float = 0.0


@dataclass
class RealityGapPayload:
    """Payload for REALITY_GAP events."""
    gap_pct: float  # (expected - actual) / expected
    expected_pnl: float
    actual_pnl: float
    is_widening: bool
    confidence: float
    adjustment_factor: float = 1.0


@dataclass
class OrderPayload:
    """Payload for ORDER_* events."""
    order_id: str
    symbol: str
    side: str  # BUY or SELL
    quantity: float
    price: float
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    status: str = "NEW"
    slippage_bps: float = 0.0
    latency_ms: float = 0.0
    rejection_reason: Optional[str] = None


@dataclass
class PortfolioPayload:
    """Payload for PORTFOLIO_UPDATED events."""
    total_value: float
    cash: float
    unrealized_pnl: float
    realized_pnl: float
    drawdown_pct: float
    daily_pnl: float
    positions: dict  # symbol -> position details
    leverage: float = 1.0


@dataclass
class RiskPayload:
    """Payload for RISK_EVALUATED events."""
    approved: bool
    leverage: float
    drawdown_pct: float
    daily_loss_pct: float
    position_size_pct: float
    rejection_reason: Optional[str] = None
    required_actions: list[str] = field(default_factory=list)


@dataclass
class ExecutionQualityPayload:
    """Payload for EXECUTION_QUALITY events."""
    quality_score: float
    avg_slippage_bps: float
    fill_rate: float
    latency_p50_ms: float
    latency_p99_ms: float
    is_degrading: bool


@dataclass
class HaltPayload:
    """Payload for HALT_REQUEST events."""
    reason: str
    halt_type: str  # SOFT or HARD
    source: str
    requires_manual_resume: bool = False


@dataclass
class SystemPayload:
    """Payload for SYSTEM_EVENT events."""
    event_subtype: str
    message: str
    details: dict = field(default_factory=dict)


def create_market_tick_event(
    symbol: str,
    price: float,
    bid: float,
    ask: float,
    volume: float,
    exchange: str,
    trace_id: Optional[str] = None,
    latency_ms: float = 0.0,
    quality_score: float = 1.0
) -> Event:
    """Create a MARKET_TICK event."""
    payload = MarketTickPayload(
        price=price,
        bid=bid,
        ask=ask,
        volume=volume,
        timestamp=int(datetime.now().timestamp() * 1000),
        exchange=exchange,
        latency_ms=latency_ms,
        quality_score=quality_score,
    )
    return Event.create(
        event_type=EventType.MARKET_TICK,
        symbol=symbol,
        payload=asdict(payload),
        trace_id=trace_id,
        source="data_layer",
    )


def create_order_event(
    event_type: EventType,
    order_id: str,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    trace_id: Optional[str] = None,
    **kwargs
) -> Event:
    """Create an order-related event."""
    payload = OrderPayload(
        order_id=order_id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=price,
        **kwargs
    )
    return Event.create(
        event_type=event_type,
        symbol=symbol,
        payload=asdict(payload),
        trace_id=trace_id,
        source="execution_layer",
    )


def create_halt_event(
    reason: str,
    halt_type: str = "SOFT",
    source: str = "system",
    requires_manual_resume: bool = False
) -> Event:
    """Create a HALT_REQUEST event."""
    payload = HaltPayload(
        reason=reason,
        halt_type=halt_type,
        source=source,
        requires_manual_resume=requires_manual_resume,
    )
    return Event.create(
        event_type=EventType.HALT_REQUEST,
        payload=asdict(payload),
        source=source,
    )
