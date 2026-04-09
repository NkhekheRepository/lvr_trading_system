# API Reference

Complete API documentation for the LVR Trading System.

## Table of Contents

1. [Data Models](#data-models)
2. [Feature Engine](#feature-engine)
3. [Strategy](#strategy)
4. [Execution](#execution)
5. [Risk Management](#risk-management)
6. [Learning](#learning)
7. [Portfolio](#portfolio)
8. [Monitoring](#monitoring)
9. [State Management](#state-management)

---

## Data Models

### Enumerations

| Enum | Description |
|------|-------------|
| `Side` | Trade direction (BUY, SELL) |
| `OrderType` | Order types (MARKET, LIMIT, POST_ONLY, IOC, FOK) |
| `OrderStatus` | Order lifecycle states |
| `TimeInForce` | Order time restrictions (GTC, IOC, FOK) |
| `ExecutionMode` | Execution mode (SIM, PAPER, LIVE) |
| `AlertSeverity` | Alert levels (INFO, WARNING, CRITICAL) |
| `ProtectionLevel` | Protection levels (0-3) |
| `EventType` | Event log types |

### TradeTick

```python
class TradeTick(BaseModel):
    timestamp: int      # Milliseconds since epoch
    symbol: str         # Trading symbol (e.g., "BTCUSDT")
    price: float        # Trade price (> 0)
    size: float         # Trade size (>= 0)
    side: Side          # BUY or SELL
```

**Methods:**

| Method | Returns | Description |
|--------|---------|-------------|
| `from_dict(data)` | `TradeTick` | Create from dictionary |
| `to_dict()` | `dict` | Convert to dictionary |

### OrderBookSnapshot

```python
class OrderBookSnapshot(BaseModel):
    timestamp: int                         # Milliseconds since epoch
    symbol: str                           # Trading symbol
    bids: list[tuple[float, float]]      # [(price, size), ...]
    asks: list[tuple[float, float]]      # [(price, size), ...]
```

**Properties:**

| Property | Returns | Description |
|----------|---------|-------------|
| `best_bid` | `float` | Highest bid price |
| `best_ask` | `float` | Lowest ask price |
| `spread` | `float` | Ask - Bid |
| `mid_price` | `float` | (Bid + Ask) / 2 |
| `total_bid_depth` | `float` | Sum of bid sizes |
| `total_ask_depth` | `float` | Sum of ask sizes |
| `depth_imbalance` | `float` | (bid - ask) / (bid + ask) |

### FeatureVector

```python
class FeatureVector(BaseModel):
    timestamp: int
    symbol: str
    I_star: float         # Normalized returns
    L_star: float         # Depth z-score
    S_star: float         # Spread z-score
    OFI: float            # Order flow imbalance
    depth_imbalance: float
    returns: float        # Raw returns
    volatility: float     # Raw volatility
    spread: float         # Raw spread
    bid_depth: float      # Raw bid depth
    ask_depth: float      # Raw ask depth
```

**Methods:**

| Method | Returns | Description |
|--------|---------|-------------|
| `has_nans()` | `bool` | Check for NaN values |
| `to_array()` | `list[float]` | Convert to feature array |

### Signal

```python
class Signal(BaseModel):
    trace_id: str
    timestamp: int
    symbol: str
    direction: Side
    strength: float         # 0-1
    confidence: float       # 0-1
    impulse_score: float
    liquidity_score: float
    spread_score: float
    flow_score: float
    filters_passed: list[str]
    filters_failed: list[str]
    regime_T: float
    in_trading_regime: bool
    expected_edge: float
    features: Optional[FeatureVector]
```

**Properties:**

| Property | Returns | Description |
|----------|---------|-------------|
| `is_valid` | `bool` | Signal passes all filters |

### OrderRequest

```python
class OrderRequest(BaseModel):
    order_id: str
    trace_id: str
    timestamp: int
    symbol: str
    side: Side
    order_type: OrderType = OrderType.MARKET
    quantity: float          # > 0
    price: Optional[float]
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    close_position: bool = False
```

### Order

```python
class Order(BaseModel):
    order_id: str
    trace_id: str
    timestamp: int
    symbol: str
    side: Side
    order_type: OrderType
    quantity: float
    filled_quantity: float = 0.0
    price: Optional[float]
    avg_fill_price: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    time_in_force: TimeInForce
    created_at: int
    updated_at: int
```

**Properties:**

| Property | Returns | Description |
|----------|---------|-------------|
| `remaining_quantity` | `float` | Quantity - filled_quantity |
| `fill_ratio` | `float` | filled_quantity / quantity |

### FillEvent

```python
class FillEvent(BaseModel):
    event_id: str       # Unique, idempotent
    trace_id: str       # For correlation
    order_id: str
    timestamp: int
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
```

### Position

```python
class Position(BaseModel):
    symbol: str
    quantity: float = 0.0
    entry_price: float = 0.0
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    total_pnl: float = 0.0
    entry_timestamp: Optional[int]
    last_update: int
```

**Properties:**

| Property | Returns | Description |
|----------|---------|-------------|
| `notional_value` | `float` | abs(quantity * current_price) |
| `leverage` | `float` | notional_value / entry_price |
| `is_long` | `bool` | quantity > 0 |
| `is_short` | `bool` | quantity < 0 |
| `is_flat` | `bool` | quantity == 0 |

### Portfolio

```python
class Portfolio(BaseModel):
    timestamp: int
    initial_capital: float
    current_capital: float
    available_capital: float
    positions: dict[str, Position]
    total_realized_pnl: float
    total_unrealized_pnl: float
    peak_capital: float
    current_drawdown: float
    max_drawdown: float
    daily_pnl: float
    daily_trades: int
    trading_day_start: int
```

**Properties:**

| Property | Returns | Description |
|----------|---------|-------------|
| `total_exposure` | `float` | Sum of position notional values |
| `portfolio_leverage` | `float` | exposure / current_capital |

**Methods:**

| Method | Returns | Description |
|--------|---------|-------------|
| `get_position(symbol)` | `Position` | Get or create position |

---

## Feature Engine

### FeatureEngine

Computes normalized features from tick data and order books.

```python
class FeatureEngine:
    def __init__(
        self,
        return_window: int = 50,
        volatility_window: int = 100,
        depth_window: int = 100,
        spread_window: int = 100
    )
```

**Parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `return_window` | 50 | Rolling window for returns (ticks) |
| `volatility_window` | 100 | Window for volatility calculation |
| `depth_window` | 100 | Window for depth statistics |
| `spread_window` | 100 | Window for spread statistics |

**Methods:**

```python
def update(
    self,
    tick: TradeTick,
    order_book: Optional[OrderBookSnapshot] = None
) -> FeatureVector
```

Update with new tick and compute features.

**Parameters:**
- `tick`: Current trade tick
- `order_book`: Optional order book snapshot

**Returns:** `FeatureVector` with computed features

---

```python
def get_state(self, symbol: str) -> Optional[WindowState]
```

Get internal rolling window state for debugging.

**Returns:** `WindowState` or None

---

```python
def reset(self, symbol: Optional[str] = None) -> None
```

Reset internal state.

**Parameters:**
- `symbol`: Reset specific symbol, or all if None

---

## Strategy

### SignalGenerator

Generates trading signals from feature vectors.

```python
class SignalGenerator:
    def __init__(
        self,
        ofi_threshold: float = 0.7,
        min_confidence: float = 0.3,
        signal_decay: float = 0.95
    )
```

**Parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ofi_threshold` | 0.7 | Block if \|OFI\| > threshold |
| `min_confidence` | 0.3 | Minimum confidence to trade |
| `signal_decay` | 0.95 | Decay for consecutive same-direction signals |

**Methods:**

```python
def generate(self, features: FeatureVector) -> Optional[Signal]
```

Generate signal from features.

**Returns:** `Signal` if valid, `None` if rejected by filters

**Filters Applied:**
- OFI filter: |OFI| <= ofi_threshold
- Reversal filter: Microstructure reversal confirmation
- Confidence filter: confidence >= min_confidence
- Edge filter: expected_edge > 0

---

```python
def get_last_signal(self, symbol: Optional[str] = None) -> Optional[Signal]
```

Get most recent signal for symbol.

---

```python
def reset(self) -> None
```

Reset signal history.

---

### RegimeDetector

Detects market regimes and blocks trading in adverse conditions.

```python
class RegimeDetector:
    def __init__(self, threshold: float = 2.0)
```

**Parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `threshold` | 2.0 | Block if T > threshold |

**Methods:**

```python
def check_regime(self, features: FeatureVector) -> tuple[bool, float]
```

Check if market is in trading regime.

**Returns:** `(in_trading_regime, regime_T)`

Where:
- `in_trading_regime`: True if T <= threshold
- `regime_T`: |returns| / volatility

---

```python
def apply_to_signal(self, signal: Signal) -> Signal
```

Apply regime check to signal, adding filter result.

---

```python
def get_regime_stats(self) -> dict
```

Get regime statistics.

**Returns:** `{"blocked_pct": float, "total_checks": int, "avg_T": float}`

---

## Execution

### ExecutionEngine (Abstract)

Abstract base class for execution engines.

```python
class ExecutionEngine(ABC):
    @property
    def mode(self) -> ExecutionMode
```

**Async Methods:**

| Method | Returns | Description |
|--------|---------|-------------|
| `submit_order(order)` | `ExecutionResult` | Submit order |
| `cancel_order(order_id)` | `bool` | Cancel order |
| `get_position(symbol)` | `Position` | Get position |
| `get_open_orders(symbol)` | `list[Order]` | Get open orders |
| `connect()` | `None` | Connect to backend |
| `disconnect()` | `None` | Disconnect |
| `health_check()` | `bool` | Health status |

**Callback Registration:**

| Method | Description |
|--------|-------------|
| `on_fill(callback)` | Register fill callback |
| `on_reject(callback)` | Register reject callback |
| `on_order_update(callback)` | Register order update callback |

---

### SimulatedExecutionEngine

Backtesting execution with realistic fills.

```python
class SimulatedExecutionEngine:
    def __init__(
        self,
        slippage_alpha: float = 0.5,
        latency_ms: int = 100,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0004,
        zero_slippage: bool = False
    )
```

**Slippage Model:**
```
slippage = slippage_alpha * (quantity / depth) * (spread / 2 + 1)
```

**Methods:**

```python
def set_order_book(self, book: OrderBookSnapshot) -> None
```

Set current order book for fill simulation.

---

### PaperExecutionEngine

Paper trading with real market data.

```python
class PaperExecutionEngine:
    def __init__(
        self,
        slippage_alpha: float = 0.5,
        slippage_multiplier: float = 1.0,
        latency_base_ms: int = 50,
        latency_jitter_ms: int = 50,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0004
    )
```

**Methods:**

```python
def update_order_book(self, book: OrderBookSnapshot) -> None
```

Update current order book snapshot.

---

### FillModel

Fill probability estimation.

```python
class FillModel:
    def __init__(self, base_flow_rate: float = 0.5)
```

**Methods:**

```python
def compute_fill_probability(
    self,
    queue_ahead: int,
    order_size: float,
    market_depth: float,
    flow_rate: float = None
) -> float
```

Compute probability of fill (0-1).

**Formula:**
```
fill_prob = flow_rate / (queue_ahead + 1)
```

---

### CostModel

Full execution cost calculation.

```python
class CostModel:
    def __init__(
        self,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0004,
        slippage_alpha: float = 0.5,
        latency_coefficient: float = 0.000001
    )
```

**Methods:**

```python
def calculate_total_cost(
    self,
    quantity: float,
    price: float,
    side: str,
    spread: float,
    market_depth: float,
    latency_ms: float = 0
) -> dict
```

Calculate all costs.

**Returns:**
```python
{
    "spread_cost": float,
    "slippage_cost": float,
    "fee_cost": float,
    "latency_cost": float,
    "total_cost": float,
    "total_cost_bps": float,
    "notional": float
}
```

---

## Risk Management

### RiskEngine

Risk checking and protection management.

```python
class RiskEngine:
    def __init__(self, limits: RiskLimits = None)
```

**Properties:**

| Property | Returns | Description |
|----------|---------|-------------|
| `protection_level` | `ProtectionLevel` | Current level |
| `is_halted` | `bool` | System halted |

**Methods:**

```python
def check_order(
    self,
    order: OrderRequest,
    signal: Signal,
    portfolio: Portfolio,
    risk_state: RiskState
) -> RiskCheckResult
```

Check if order passes risk limits.

**Returns:** `RiskCheckResult` with approval status

---

```python
def record_trade_result(self, pnl: float) -> None
```

Record trade result for loss tracking.

---

```python
def evaluate_protection_level(self, portfolio: Portfolio) -> ProtectionLevel
```

Evaluate required protection level based on conditions.

---

```python
def apply_protection_action(self, action: ProtectionLevel) -> list[str]
```

Apply protection actions.

**Returns:** List of actions taken

---

```python
def reset(self) -> None
```

Reset risk engine state.

---

```python
def unhalt(self) -> bool
```

Manual unhalt (requires manual restart).

---

### PositionSizer

Position sizing calculations.

```python
class PositionSizer:
    def __init__(
        self,
        base_risk_per_trade: float = 0.01,
        max_leverage: float = 10.0,
        min_position: float = 0.001
    )
```

**Position Sizing Formula:**
```
size = (base_risk * portfolio_value * confidence) / (leverage * volatility * price)
```

**Methods:**

```python
def calculate_size(
    self,
    signal: Signal,
    portfolio: Portfolio,
    risk_state: RiskState,
    current_price: float,
    volatility: float = None
) -> float
```

Calculate position size for signal.

---

```python
def calculate_stop_loss(
    self,
    entry_price: float,
    signal: Signal,
    volatility: float = None,
    atr_multiplier: float = 2.0
) -> float
```

Calculate stop loss price.

---

```python
def calculate_take_profit(
    self,
    entry_price: float,
    signal: Signal,
    risk_reward_ratio: float = 2.0,
    stop_loss: float = None
) -> float
```

Calculate take profit price.

---

### RiskLimits

Risk limit configuration.

```python
@dataclass
class RiskLimits:
    max_leverage: float = 10.0
    max_drawdown_pct: float = 0.10
    max_daily_loss_pct: float = 0.03
    max_position_size_pct: float = 0.20
    max_consecutive_losses: int = 5
    
    position_warning_pct: float = 0.15
    daily_loss_warning_pct: float = 0.02
    consecutive_loss_warning: int = 3
```

---

## Learning

### BayesianLearner

Bayesian edge estimation with bounded updates.

```python
class BayesianLearner:
    def __init__(
        self,
        min_samples: int = 30,
        update_rate: float = 0.1,
        max_change_per_update: float = 0.05,
        cooldown_ticks: int = 10
    )
```

**Update Formula:**
```
new_estimate = 0.9 * old + 0.1 * observation
```

**Constraints:**
- Updates bounded (max_change_per_update)
- Minimum samples before full weight
- Cooldown between updates

**Methods:**

```python
def get_state(self, symbol: str) -> BayesianState
```

Get or create state for symbol.

---

```python
def update(
    self,
    fill: FillEvent,
    expected_edge: float = 0
) -> BayesianState
```

Update from fill event.

---

```python
def get_edge_estimate(self, symbol: str) -> float
```

Get estimated edge for symbol.

---

```python
def is_reliable(self, symbol: str) -> bool
```

Check if estimate is reliable (min_samples reached).

---

```python
def reset(self, symbol: Optional[str] = None) -> None
```

Reset learner state.

---

## Portfolio

### PortfolioManager

Portfolio and position management.

```python
class PortfolioManager:
    def __init__(self, initial_capital: float = 100000.0)
```

**Methods:**

```python
def update_from_fill(self, fill: FillEvent) -> None
```

Update portfolio from fill event.

---

```python
def update_market_prices(self, prices: dict[str, float]) -> None
```

Update positions with current market prices.

---

```python
def close_position(self, symbol: str, current_price: float) -> float
```

Close entire position.

**Returns:** Realized PnL

---

```python
def close_all_positions(self, prices: dict[str, float]) -> dict[str, float]
```

Close all positions.

**Returns:** PnL by symbol

---

```python
def get_position(self, symbol: str) -> Position
```

Get position for symbol.

---

```python
def get_summary(self) -> dict
```

Get portfolio summary.

**Returns:**
```python
{
    "capital": float,
    "available": float,
    "exposure": float,
    "leverage": float,
    "realized_pnl": float,
    "unrealized_pnl": float,
    "drawdown": float,
    "max_drawdown": float,
    "daily_pnl": float,
    "positions": int
}
```

---

## Monitoring

### MetricsCollector

System metrics collection.

```python
class MetricsCollector:
    def __init__(self, window_size: int = 100)
```

**Methods:**

```python
def record_fill(self, filled_qty: float, requested_qty: float) -> None
```

Record fill event.

---

```python
def record_rejection(self) -> None
```

Record order rejection.

---

```python
def record_slippage(
    self,
    actual_slippage: float,
    expected_slippage: float
) -> None
```

Record slippage error.

---

```python
def record_edge(
    self,
    expected_edge: float,
    realized_edge: float
) -> None
```

Record edge error.

---

```python
def record_latency(self, latency_ms: float) -> None
```

Record order latency.

---

```python
def collect(self) -> MetricsSnapshot
```

Collect current metrics snapshot.

---

```python
def get_summary(self) -> dict
```

Get metrics summary.

---

### AlertManager

Alert dispatch with rate limiting.

```python
class AlertManager:
    def __init__(
        self,
        rate_limit_per_minute: int = 10,
        slack_webhook: str = None,
        email_recipients: list = None
    )
```

**Methods:**

```python
def send_alert(
    self,
    severity: AlertSeverity,
    category: str,
    message: str,
    source_module: str,
    details: dict = None,
    trace_id: str = None
) -> Optional[Alert]
```

Send alert if within rate limit.

---

```python
def get_recent_alerts(
    self,
    severity: AlertSeverity = None,
    limit: int = 10
) -> list[Alert]
```

Get recent alerts.

---

### ProtectionSystem

Multi-level protection system.

```python
class ProtectionSystem:
    def __init__(self, alert_manager=None)
```

**Methods:**

```python
def evaluate(
    self,
    metrics: MetricsSnapshot,
    portfolio_drawdown: float,
    daily_loss_pct: float
) -> ProtectionLevel
```

Evaluate required protection level.

---

```python
def apply_protection(self, level: ProtectionLevel) -> dict
```

Apply protection actions.

**Returns:**
```python
{
    "should_reduce_size": bool,
    "should_restrict_trading": bool,
    "should_close_all": bool,
    "should_halt": bool,
    "max_order_per_minute": Optional[int],
    "size_multiplier": float
}
```

---

```python
def check_anomalies(self, metrics: MetricsSnapshot) -> list[str]
```

Detect anomalies in metrics.

---

## State Management

### StateStore

Multi-layer state management.

```python
class StateStore:
    def __init__(
        self,
        pg_config: dict = None,
        redis_config: dict = None,
        checkpoint_interval: int = 60
    )
```

**Async Methods:**

| Method | Returns | Description |
|--------|---------|-------------|
| `connect()` | `None` | Connect to stores |
| `disconnect()` | `None` | Disconnect |
| `save_position(position)` | `None` | Save position |
| `load_positions()` | `dict` | Load all positions |
| `save_portfolio(portfolio)` | `None` | Save portfolio |
| `load_portfolio()` | `Optional[Portfolio]` | Load portfolio |
| `checkpoint(force=False)` | `None` | Create checkpoint |
| `recover()` | `dict` | Recover state |

---

## Data Loading

### DataLoader

Tick data loading and validation.

```python
class DataLoader:
    def __init__(self, max_gap_ticks: int = 5)
```

**Methods:**

```python
def load_parquet(self, path: str, symbol: str) -> Iterator[TradeTick]
```

Load from Parquet file.

---

```python
def load_csv(self, path: str, symbol: str) -> Iterator[TradeTick]
```

Load from CSV file.

---

```python
def validate_sequence(self, ticks: list[TradeTick]) -> bool
```

Validate ticks are time-sorted.

---

### ReplayEngine

Tick-by-tick backtest replay.

```python
class ReplayEngine:
    def __init__(
        self,
        ticks: list[TradeTick],
        order_books: list[OrderBookSnapshot] = None,
        speed_multiplier: float = 1.0
    )
```

**Methods:**

| Method | Description |
|--------|-------------|
| `on_tick(callback)` | Register tick callback |
| `on_order_book(callback)` | Register order book callback |
| `on_cycle(callback)` | Register cycle callback |
| `run()` | Start replay (async) |
| `pause()` | Pause replay |
| `resume()` | Resume replay |
| `seek(index)` | Seek to tick index |
| `get_progress()` | Get replay progress |

---
