"""
Pydantic schemas for the LVR Trading System.

Note: Core event types are imported from core.event to ensure consistency.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

try:
    from core.event import EventType as CoreEventType
    EventType = CoreEventType
except ImportError:
    class EventType(str, Enum):
        MARKET = "market"
        SIGNAL = "signal"
        ORDER_SUBMIT = "order_submit"
        ORDER_UPDATE = "order_update"
        FILL = "fill"
        REJECT = "reject"
        CANCEL = "cancel"
        RISK_CHECK = "risk_check"
        PROTECTION = "protection"
        STATE_UPDATE = "state_update"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    POST_ONLY = "post_only"
    IOC = "ioc"
    FOK = "fok"


class OrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class TimeInForce(str, Enum):
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class ExecutionMode(str, Enum):
    SIM = "SIM"
    PAPER = "PAPER"
    LIVE = "LIVE"


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ProtectionLevel(int, Enum):
    NONE = 0
    REDUCE_SIZE = 1
    RESTRICT_TRADING = 2
    CLOSE_ALL_HALT = 3


class TradeTick(BaseModel):
    timestamp: int = Field(..., description="Timestamp in milliseconds")
    symbol: str = Field(..., description="Trading symbol")
    price: float = Field(..., gt=0)
    size: float = Field(..., ge=0)
    side: Side = Field(...)

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: int) -> int:
        if v < 1000000000000:
            v *= 1000
        return v

    @classmethod
    def from_dict(cls, data: dict) -> TradeTick:
        return cls(
            timestamp=int(data["timestamp"]),
            symbol=data.get("symbol", "UNKNOWN"),
            price=float(data["price"]),
            size=float(data["size"]),
            side=Side(data["side"]) if isinstance(data["side"], str) else data["side"]
        )

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "price": self.price,
            "size": self.size,
            "side": self.side.value
        }


class OrderBookSnapshot(BaseModel):
    timestamp: int = Field(...)
    symbol: str = Field(...)
    bids: list[tuple[float, float]] = Field(...)
    asks: list[tuple[float, float]] = Field(...)

    @field_validator("bids", "asks", mode="before")
    @classmethod
    def validate_levels(cls, v):
        if isinstance(v, list) and len(v) > 0:
            if isinstance(v[0], dict):
                return [(float(x["price"]), float(x["size"])) for x in v]
            return [(float(x[0]), float(x[1])) for x in v]
        return v

    @property
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else 0.0

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid if self.bids and self.asks else 0.0

    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2 if self.bids and self.asks else 0.0

    @property
    def total_bid_depth(self) -> float:
        return sum(size for _, size in self.bids)

    @property
    def total_ask_depth(self) -> float:
        return sum(size for _, size in self.asks)

    @property
    def depth_imbalance(self) -> float:
        total = self.total_bid_depth + self.total_ask_depth
        if total == 0:
            return 0.0
        return (self.total_bid_depth - self.total_ask_depth) / total


class FeatureVector(BaseModel):
    timestamp: int
    symbol: str
    I_star: float = Field(..., description="Normalized returns")
    L_star: float = Field(..., description="Depth z-score")
    S_star: float = Field(..., description="Spread z-score")
    OFI: float = Field(..., description="Order flow imbalance")
    depth_imbalance: float = 0.0
    returns: float = 0.0
    volatility: float = 0.0
    spread: float = 0.0
    bid_depth: float = 0.0
    ask_depth: float = 0.0

    def has_nans(self) -> bool:
        for v in self.model_dump().values():
            if isinstance(v, float) and v != v:
                return True
        return False

    def to_array(self) -> list[float]:
        return [self.I_star, self.L_star, self.S_star, self.OFI, self.depth_imbalance]


class Signal(BaseModel):
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))
    symbol: str
    direction: Side = Field(...)
    strength: float = Field(..., ge=0, le=1)
    confidence: float = Field(..., ge=0, le=1)
    impulse_score: float = 0.0
    liquidity_score: float = 0.0
    spread_score: float = 0.0
    flow_score: float = 0.0
    filters_passed: list[str] = Field(default_factory=list)
    filters_failed: list[str] = Field(default_factory=list)
    regime_T: float = 0.0
    in_trading_regime: bool = True
    expected_edge: float = 0.0
    features: Optional[FeatureVector] = None

    @property
    def is_valid(self) -> bool:
        return (
            self.in_trading_regime and
            len(self.filters_failed) == 0 and
            self.confidence >= 0.3 and
            self.expected_edge > 0
        )


class OrderRequest(BaseModel):
    order_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))
    symbol: str
    side: Side
    order_type: OrderType = OrderType.MARKET
    quantity: float = Field(..., gt=0)
    price: Optional[float] = Field(None, gt=0)
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    close_position: bool = False


class Order(BaseModel):
    order_id: str
    trace_id: str
    timestamp: int
    symbol: str
    side: Side
    order_type: OrderType
    quantity: float
    filled_quantity: float = 0.0
    price: Optional[float] = None
    avg_fill_price: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    time_in_force: TimeInForce = TimeInForce.GTC
    created_at: int = Field(default_factory=lambda: int(time.time() * 1000))
    updated_at: int = Field(default_factory=lambda: int(time.time() * 1000))

    @property
    def remaining_quantity(self) -> float:
        return self.quantity - self.filled_quantity

    @property
    def fill_ratio(self) -> float:
        if self.quantity == 0:
            return 0.0
        return self.filled_quantity / self.quantity


class FillEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str
    order_id: str
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))
    symbol: str
    side: Side
    quantity: float
    price: float
    fee: float = 0.0
    fee_currency: str = "USDT"
    slippage: float = 0.0
    latency_ms: float = 0.0
    fill_probability: float = 1.0
    queue_ahead: int = 0


class RejectEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str
    order_id: str
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))
    symbol: str
    reason: str
    error_code: Optional[str] = None


class ExecutionResult(BaseModel):
    success: bool
    order_id: str
    trace_id: str
    symbol: str
    status: OrderStatus
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    total_cost: float = 0.0
    slippage: float = 0.0
    fee: float = 0.0
    fill_events: list[FillEvent] = Field(default_factory=list)
    reject_event: Optional[RejectEvent] = None
    latency_ms: float = 0.0
    error_message: Optional[str] = None


class Position(BaseModel):
    symbol: str
    quantity: float = 0.0
    entry_price: float = 0.0
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    total_pnl: float = 0.0
    entry_timestamp: Optional[int] = None
    last_update: int = Field(default_factory=lambda: int(time.time() * 1000))

    @property
    def notional_value(self) -> float:
        return abs(self.quantity * self.current_price)

    @property
    def leverage(self) -> float:
        return self.notional_value / self.entry_price if self.entry_price > 0 else 0.0

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0


class Portfolio(BaseModel):
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))
    initial_capital: float
    current_capital: float
    available_capital: float
    positions: dict[str, Position] = Field(default_factory=dict)
    total_realized_pnl: float = 0.0
    total_unrealized_pnl: float = 0.0
    peak_capital: float = 0.0
    current_drawdown: float = 0.0
    max_drawdown: float = 0.0
    daily_pnl: float = 0.0
    daily_trades: int = 0
    trading_day_start: int = Field(default_factory=lambda: int(time.time() * 1000))

    @property
    def total_exposure(self) -> float:
        return sum(p.notional_value for p in self.positions.values())

    @property
    def portfolio_leverage(self) -> float:
        if self.current_capital == 0:
            return 0.0
        return self.total_exposure / self.current_capital

    def get_position(self, symbol: str) -> Position:
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol)
        return self.positions[symbol]


class RiskState(BaseModel):
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))
    current_leverage: float = 0.0
    current_drawdown: float = 0.0
    daily_loss: float = 0.0
    consecutive_losses: int = 0
    protection_level: ProtectionLevel = ProtectionLevel.NONE
    risk_per_trade: float = 0.01
    max_position_size: float = 0.0
    leverage_ok: bool = True
    drawdown_ok: bool = True
    daily_loss_ok: bool = True


class RiskCheckResult(BaseModel):
    approved: bool
    risk_state: RiskState
    adjusted_quantity: Optional[float] = None
    rejection_reason: Optional[str] = None
    required_actions: list[str] = Field(default_factory=list)


class BayesianState(BaseModel):
    symbol: str
    alpha: float = 1.0
    beta: float = 1.0
    mean_pnl: float = 0.0
    std_pnl: float = 1.0
    trade_count: int = 0
    win_count: int = 0
    last_update: int = 0
    confidence: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def expected_edge(self) -> float:
        return self.win_rate * self.mean_pnl - (1 - self.win_rate) * abs(self.mean_pnl)

    @property
    def is_reliable(self) -> bool:
        return self.trade_count >= 30


class AttributionResult(BaseModel):
    symbol: str
    total_pnl: float
    signal_edge: float = 0.0
    execution_edge: float = 0.0
    cost_impact: float = 0.0
    expected_return: float = 0.0
    realized_return: float = 0.0
    slippage_cost: float = 0.0
    fee_cost: float = 0.0
    spread_cost: float = 0.0


class MetricsSnapshot(BaseModel):
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))
    fill_rate: float = 1.0
    avg_slippage: float = 0.0
    slippage_error: float = 0.0
    rejection_rate: float = 0.0
    edge_error: float = 0.0
    signal_accuracy: float = 0.0
    drawdown: float = 0.0
    daily_pnl: float = 0.0
    order_latency_ms: float = 0.0
    data_latency_ms: float = 0.0
    last_tick_age_sec: float = 0.0
    data_fresh: bool = True
    protection_level: ProtectionLevel = ProtectionLevel.NONE
    consecutive_failures: int = 0


class Alert(BaseModel):
    alert_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))
    severity: AlertSeverity
    category: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    source_module: str
    trace_id: Optional[str] = None
    acknowledged: bool = False


class SystemEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))
    payload: dict[str, Any] = Field(default_factory=dict)
    symbol: Optional[str] = None
    order_id: Optional[str] = None
