# LVR Trading System

**Production-Grade Autonomous Trading System for Binance Futures**

A complete tick-level trading system with execution simulation, risk management, Bayesian learning, and multi-layer protection. Designed for deployment on Linux EC2 with PostgreSQL and Redis infrastructure.

---

## Table of Contents

1. [Overview](#overview)
2. [Features](#features)
3. [Architecture](#architecture)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [System Design](#system-design)
7. [Module Reference](#module-reference)
8. [Interface Contracts](#interface-contracts)
9. [Data Schemas](#data-schemas)
10. [Deployment](#deployment)
11. [Testing](#testing)
12. [Safety & Protection](#safety--protection)
13. [Performance Criteria](#performance-criteria)

---

## Overview

The LVR (Liquidity, Volatility, Regime) Trading System is a modular, production-grade autonomous trading framework designed for high-frequency futures trading on Binance. It implements a complete lifecycle: **data → features → signal → execution → portfolio → learning → monitoring → protection**.

### Design Principles

- **Execution-Aware**: Every component models real execution constraints
- **Modular & Testable**: Loose coupling enables comprehensive testing
- **Numerically Stable**: No NaNs, division-by-zero protection, deterministic outputs
- **Robust to Failure**: Fail-safe loops, automatic recovery, protection levels
- **Autonomous Operation**: Self-monitoring, self-protection, self-learning

---

## Features

| Feature | Description |
|---------|-------------|
| **Tick-Level Processing** | No OHLCV dependency; processes individual trades |
| **Feature Engineering** | I*, L*, S*, OFI, depth imbalance with rolling statistics |
| **Signal Generation** | Multi-factor scoring with OFI filter and regime detection |
| **Execution Abstraction** | SIM → PAPER → LIVE progression |
| **Risk Management** | Position sizing, leverage limits, drawdown protection |
| **Bayesian Learning** | Adaptive edge estimation with bounded updates |
| **State Management** | PostgreSQL (authoritative) + Redis (cache) + Event Log |
| **Real-Time Monitoring** | Metrics collection, alerts, anomaly detection |
| **Protection System** | 3-level response (reduce → restrict → halt) |

---

## Architecture

### System Flow

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         TRADING SYSTEM CORE                               │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────┐    ┌──────────┐    ┌────────┐    ┌──────────┐              │
│  │  DATA  │───▶│ FEATURES │───▶│ SIGNAL │───▶│EXECUTION│              │
│  └─────────┘    └──────────┘    └────────┘    └──────────┘              │
│      │              │               │              │                      │
│      │              │               │              ▼                      │
│      │              │               │         ┌──────────┐                 │
│      │              │               │         │ PORTFOLIO│                 │
│      │              │               │         └──────────┘                 │
│      │              │               │              │                      │
│      ▼              ▼               ▼              ▼                      │
│  ┌─────────┐    ┌──────────┐    ┌────────┐    ┌──────────┐              │
│  │ MONITOR │◀───│ LEARNING │◀───│  RISK  │◀───│PROTECTION│              │
│  └─────────┘    └──────────┘    └────────┘    └──────────┘              │
│                                                                          │
├──────────────────────────────────────────────────────────────────────────┤
│                         STATE MANAGEMENT                                 │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐                │
│  │ PostgreSQL    │  │ Redis Cache  │  │ Event Log     │                │
│  │ (Authoritative)│  │ (Real-time) │  │ (Append-only) │                │
│  └───────────────┘  └───────────────┘  └───────────────┘                │
└──────────────────────────────────────────────────────────────────────────┘
```

### Component Architecture

```
lvr_trading_system/
├── app/                    # Main application & schemas
│   ├── main.py            # Trading loop orchestrator
│   └── schemas.py         # Pydantic data models
│
├── config/                  # Configuration
│   └── config.yaml        # Master configuration
│
├── data/                    # Data ingestion & replay
│   ├── loader.py          # Tick data loading
│   ├── replay_engine.py   # Backtest replay
│   └── sample_data.py     # Test data generation
│
├── features/                # Feature engineering
│   └── engine.py          # Rolling statistics, Z-scores
│
├── strategy/                # Signal generation
│   ├── signal.py          # Multi-factor scoring
│   ├── regime.py          # Market regime detection
│   └── filters.py         # Signal quality filters
│
├── execution/               # Execution abstraction
│   ├── base.py            # Abstract interface
│   ├── simulator.py        # SIM mode
│   ├── paper_engine.py     # PAPER mode
│   ├── vnpy_adapter.py     # LIVE mode
│   ├── fill_model.py       # Fill probability
│   └── cost_model.py       # Cost calculations
│
├── portfolio/               # Portfolio management
│   └── portfolio.py        # Positions & PnL
│
├── risk/                    # Risk management
│   ├── sizing.py           # Position sizing
│   └── limits.py          # Risk limits
│
├── learning/                # Adaptive learning
│   ├── bayes.py           # Bayesian updates
│   └── attribution.py      # PnL attribution
│
├── monitoring/              # Observability
│   ├── monitor.py         # Metrics collection
│   ├── alerts.py          # Alert dispatch
│   └── protection.py      # Protection levels
│
├── state/                   # State persistence
│   └── store.py           # PostgreSQL + Redis
│
├── infrastructure/           # Deployment
│   ├── setup_db.sql       # PostgreSQL schema
│   ├── setup_redis.sh     # Redis setup
│   ├── deploy.sh          # Deployment script
│   └── systemd/           # Service files
│
└── tests/                    # Test suite
    ├── test_features.py
    ├── test_signal.py
    ├── test_execution.py
    ├── test_risk.py
    └── test_recovery.py
```

---

## Installation

### Prerequisites

- Python 3.10+
- PostgreSQL 14+
- Redis 7+
- Linux EC2 (recommended)

### Steps

```bash
# Clone repository
git clone https://github.com/NkhekheRepository/lvr_trading_system.git
cd lvr_trading_system

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Copy environment file
cp .env.example .env

# Edit .env with your configuration
nano .env
```

### Infrastructure Setup

```bash
# PostgreSQL (run as postgres user)
psql -U postgres -c "CREATE DATABASE trading_system;"
psql -U postgres -d trading_system -f infrastructure/setup_db.sql

# Redis
./infrastructure/setup_redis.sh
```

---

## Configuration

### config.yaml Reference

```yaml
system:
  name: lvr_trading_system
  version: 1.0.0
  mode: SIM                    # SIM | PAPER | LIVE
  environment: development     # development | staging | production
  log_level: INFO
  live_confirm_required: true   # Require explicit LIVE confirmation

exchange:
  name: binance_futures
  testnet: true
  symbols:
    - BTCUSDT
    - ETHUSDT
  rate_limits:
    orders_per_second: 10
    requests_per_second: 120

execution:
  slippage_alpha: 0.5          # Slippage model coefficient
  simulated_latency_ms: 100     # Backtest latency
  fees:
    maker: 0.00020             # 0.02%
    taker: 0.00040             # 0.04%

risk:
  base_risk_per_trade: 0.01    # 1% of portfolio
  max_leverage: 10
  limits:
    max_drawdown_pct: 0.10     # 10%
    max_daily_loss_pct: 0.03   # 3%
    max_position_size_pct: 0.20

features:
  return_window: 50            # Ticks
  volatility_window: 100        # Ticks
  depth_window: 100             # Ticks
  spread_window: 100            # Ticks

strategy:
  ofi_threshold: 0.7           # Block if |OFI| > threshold
  regime_threshold: 2.0         # Block if T > threshold
  min_confidence: 0.3           # Minimum signal confidence
  reversal_window: 5           # Microstructure reversal

learning:
  min_samples: 30              # Min trades before full weight
  update_rate: 0.1             # Learning rate
  max_change_per_update: 0.05  # Parameter drift limit
  cooldown_ticks: 10           # Oscillation prevention

monitoring:
  collection_interval: 1.0      # Seconds
  data_freshness_threshold_sec: 10

state:
  checkpoint_interval_sec: 60
  event_log_enabled: true
  auto_recovery: true

database:
  host: localhost
  port: 5432
  name: trading_system
  user: trading_user

redis:
  host: localhost
  port: 6379
  db: 0
```

### Environment Variables

```bash
# Execution Mode
LVR_EXECUTION_MODE=SIM          # SIM | PAPER | LIVE

# Live Trading Safety
LVR_LIVE_CONFIRMED=false        # Set true only when ready for LIVE

# Database
PG_HOST=localhost
PG_PORT=5432
PG_DATABASE=trading_system
PG_USER=trading_user
PG_PASSWORD=your_password

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
```

---

## System Design

### Data Layer

**Responsibilities:**
- Load tick data from CSV/Parquet
- Validate schema compliance
- Handle missing data (max 5-tick gap)
- Normalize timestamps to milliseconds
- Emit market events for downstream

**Data Rules:**
- Tick-level only (NO OHLCV)
- Time-sorted strictly
- Missing data → forward-fill with warning
- All timestamps normalized to ms

### Feature Engine

**Rolling Windows (Mandatory):**

| Window | Size | Purpose |
|--------|------|---------|
| Returns | 50 ticks | Impulse signal |
| Volatility | 100 ticks | Normalization |
| Depth | 100 ticks | Z-score |
| Spread | 100 ticks | Z-score |

**Features:**
```
I* = returns / volatility                    # Normalized returns
L* = (depth - depth_mean) / depth_std        # Depth z-score
S* = (spread - spread_mean) / spread_std     # Spread z-score
OFI = Σ(bid_delta+) - Σ(ask_delta+) / total  # Order flow imbalance
depth_imbalance = (bid_depth - ask_depth) / total
```

**Safety:**
- No NaNs allowed → zero-filled with warning
- Division-by-zero handled (ε = 1e-10)
- Deterministic outputs

### Signal Engine

**Core Formula:**
```
score = I* × (-L*) × S* × (1 - |OFI|)
direction = opposite(I*)  # Buy if I* < 0, Sell if I* > 0
```

**Filters:**

| Filter | Condition | Action |
|--------|-----------|--------|
| OFI | \|OFI\| > 0.7 | Reject |
| Regime | T = \|I*\| / volatility > 2.0 | Block |
| Confidence | confidence < 0.3 | Reject |
| Edge | edge <= 0 | Reject |

**Regime Detection:**
```
T = |returns| / volatility
if T > threshold: block trading (high volatility regime)
```

### Execution Layer

**Execution Modes:**

| Mode | Engine | Data | Execution | Safety |
|------|--------|------|-----------|--------|
| SIM | Simulated | Historical | Deterministic | Full |
| PAPER | Paper | Real-time | Simulated | Full |
| LIVE | Vnpy | Exchange | Real | Requires auth |

**Fill Model:**
```
fill_probability = flow_rate / (queue_ahead + 1)
```

**Slippage Model:**
```
slippage = α × (size / depth)
```

**Total Cost Model:**
```
total_cost = spread_cost + slippage_cost + fee_cost + latency_cost
```

**Latency:**
- Backtest: ~100ms simulated delay
- Paper: 50-100ms with jitter
- Live: Real exchange latency

**Partial Fills:**
- Orders MUST fill incrementally
- Track filled_quantity vs quantity
- Update position on each partial fill

### Risk Engine

**Position Sizing:**
```
size = (base_risk × portfolio_value) / (leverage × volatility)
```

**Hard Limits:**

| Limit | Value | Action on Breach |
|-------|-------|------------------|
| Max Leverage | 10x | Reject order |
| Max Drawdown | 10% | CLOSE ALL + HALT |
| Max Daily Loss | 3% | CLOSE ALL + HALT |
| Max Position | 20% | Reduce size |

### Bayesian Learning

**Update Formula:**
```
new_estimate = 0.9 × old + 0.1 × observation  # Bounded
```

**Constraints:**
- Updates bounded (max 5% change)
- Minimum 30 trades before full weight
- Cooldown: 10 ticks between updates
- No rapid oscillation

**Attribution Tracking:**
- Signal edge: expected vs actual return
- Execution efficiency: realized vs expected fill
- Cost breakdown: slippage, fees, spread

### Protection System

| Level | Trigger | Action |
|-------|---------|--------|
| 1 | Metrics degraded | Reduce size 50% |
| 2 | Multiple breaches | Restrict to 1 order/min |
| 3 | Critical (DD > 10%) | CLOSE ALL + HALT |

---

## Module Reference

### data/loader.py

```python
class DataLoader:
    def load_parquet(path: str, symbol: str) -> Iterator[TradeTick]
    def load_csv(path: str, symbol: str) -> Iterator[TradeTick]
    def validate_sequence(ticks: list[TradeTick]) -> bool
```

### data/replay_engine.py

```python
class ReplayEngine:
    def on_tick(callback: Callable[[TradeTick], None]) -> None
    def on_order_book(callback: Callable[[OrderBookSnapshot], None]) -> None
    def on_cycle(callback: Callable[[int, TradeTick], None]) -> None
    async def run() -> None
    def pause() -> None
    def resume() -> None
    def get_progress() -> dict
```

### features/engine.py

```python
class FeatureEngine:
    def update(tick: TradeTick, order_book: OrderBookSnapshot) -> FeatureVector
    def get_state(symbol: str) -> WindowState
    def reset(symbol: Optional[str] = None) -> None
```

### strategy/signal.py

```python
class SignalGenerator:
    def generate(features: FeatureVector) -> Signal | None
    def get_last_signal(symbol: Optional[str] = None) -> Signal | None
    def reset() -> None
```

### strategy/regime.py

```python
class RegimeDetector:
    def check_regime(features: FeatureVector) -> tuple[bool, float]
    def apply_to_signal(signal: Signal) -> Signal
    def get_regime_stats() -> dict
```

### execution/base.py

```python
class ExecutionEngine(ABC):
    @property mode: ExecutionMode
    
    async def submit_order(order: OrderRequest) -> ExecutionResult
    async def cancel_order(order_id: str) -> bool
    async def get_position(symbol: str) -> Position
    async def get_open_orders(symbol: Optional[str] = None) -> list[Order]
    async def connect() -> None
    async def disconnect() -> None
    async def health_check() -> bool
    
    def on_fill(callback: Callable[[FillEvent], None]) -> None
    def on_reject(callback: Callable[[RejectEvent], None]) -> None
```

### portfolio/portfolio.py

```python
class PortfolioManager:
    def update_from_fill(fill: FillEvent) -> None
    def update_market_prices(prices: dict[str, float]) -> None
    def close_position(symbol: str, current_price: float) -> float
    def close_all_positions(prices: dict[str, float]) -> dict[str, float]
    def get_summary() -> dict
```

### risk/limits.py

```python
class RiskEngine:
    @property protection_level: ProtectionLevel
    @property is_halted: bool
    
    def check_order(order: OrderRequest, signal: Signal, 
                   portfolio: Portfolio, risk_state: RiskState) -> RiskCheckResult
    def record_trade_result(pnl: float) -> None
    def evaluate_protection_level(portfolio: Portfolio) -> ProtectionLevel
    def apply_protection_action(level: ProtectionLevel) -> list[str]
    def reset() -> None
```

### learning/bayes.py

```python
class BayesianLearner:
    def get_state(symbol: str) -> BayesianState
    def update(fill: FillEvent, expected_edge: float) -> BayesianState
    def get_edge_estimate(symbol: str) -> float
    def is_reliable(symbol: str) -> bool
    def reset(symbol: Optional[str] = None) -> None
```

### monitoring/monitor.py

```python
class MetricsCollector:
    def record_fill(filled_qty: float, requested_qty: float) -> None
    def record_rejection() -> None
    def record_slippage(actual: float, expected: float) -> None
    def record_edge(expected: float, realized: float) -> None
    def record_latency(latency_ms: float) -> None
    def collect() -> MetricsSnapshot
    def get_summary() -> dict
```

---

## Interface Contracts

### compute_features(data) -> dict

```python
def compute_features(
    tick: TradeTick,
    order_book: OrderBookSnapshot
) -> FeatureVector:
    """
    Compute normalized feature vector from market data.
    
    Args:
        tick: Current trade tick
        order_book: Current order book snapshot
    
    Returns:
        FeatureVector with I*, L*, S*, OFI, depth_imbalance
    
    Requirements:
        - No NaN values in output
        - Deterministic (same input → same output)
        - Division-by-zero safe
    """
```

### generate_signal(features) -> dict | None

```python
def generate_signal(
    features: FeatureVector
) -> Signal | None:
    """
    Generate trading signal from features.
    
    Args:
        features: Feature vector from compute_features()
    
    Returns:
        Signal object if valid, None if rejected by filters
    
    Filters Applied:
        - OFI filter (|OFI| <= 0.7)
        - Regime filter (T <= 2.0)
        - Confidence filter (confidence >= 0.3)
        - Edge filter (edge > 0)
    """
```

### execute_order(signal) -> result

```python
async def execute_order(
    signal: Signal,
    executor: ExecutionEngine
) -> ExecutionResult:
    """
    Execute order based on signal.
    
    Args:
        signal: Validated trading signal
        executor: Execution engine (SIM/PAPER/LIVE)
    
    Returns:
        ExecutionResult with fill events or rejection
    
    Edge Protection:
        real_edge = expected_edge - execution_cost
        if real_edge <= 0: abort execution
    """
```

### update_portfolio(fill) -> None

```python
def update_portfolio(
    fill: FillEvent,
    portfolio: PortfolioManager
) -> None:
    """
    Update portfolio state from fill event.
    
    Updates:
        - Position quantity and entry price
        - Realized and unrealized PnL
        - Drawdown tracking
    """
```

### monitor_metrics() -> dict

```python
def monitor_metrics() -> MetricsSnapshot:
    """
    Collect current system metrics.
    
    Returns:
        MetricsSnapshot with:
        - fill_rate
        - avg_slippage
        - edge_error
        - drawdown
        - latency
        - data_freshness
    """
```

### detect_anomalies(metrics) -> list

```python
def detect_anomalies(
    metrics: MetricsSnapshot
) -> list[str]:
    """
    Detect anomalies in system metrics.
    
    Checks:
        - fill_rate < 0.6
        - avg_slippage > 0.001
        - latency > 500ms
        - rejection_rate > 0.2
        - stale data
        - consecutive failures > 3
    """
```

### apply_protection(alerts) -> None

```python
def apply_protection(
    alerts: list[Alert],
    protection: ProtectionSystem,
    portfolio: PortfolioManager,
    executor: ExecutionEngine
) -> None:
    """
    Apply protection actions based on alerts.
    
    Actions by Level:
        Level 1: Reduce size by 50%
        Level 2: Restrict to 1 order/min
        Level 3: CLOSE ALL + HALT
    """
```

---

## Data Schemas

### TradeTick

```python
class TradeTick(BaseModel):
    timestamp: int      # ms since epoch
    symbol: str          # e.g., "BTCUSDT"
    price: float         # > 0
    size: float          # >= 0
    side: Side           # BUY | SELL
```

### OrderBookSnapshot

```python
class OrderBookSnapshot(BaseModel):
    timestamp: int
    symbol: str
    bids: list[tuple[float, float]]  # [(price, size), ...]
    asks: list[tuple[float, float]]  # [(price, size), ...]
    
    @property best_bid: float
    @property best_ask: float
    @property spread: float
    @property mid_price: float
    @property depth_imbalance: float
```

### FeatureVector

```python
class FeatureVector(BaseModel):
    timestamp: int
    symbol: str
    I_star: float       # Normalized returns
    L_star: float       # Depth z-score
    S_star: float       # Spread z-score
    OFI: float          # Order flow imbalance
    depth_imbalance: float
    
    returns: float       # Raw
    volatility: float   # Raw
    spread: float       # Raw
    bid_depth: float     # Raw
    ask_depth: float     # Raw
```

### Signal

```python
class Signal(BaseModel):
    trace_id: str       # Unique signal ID
    timestamp: int
    symbol: str
    direction: Side      # BUY | SELL
    strength: float      # 0-1
    confidence: float    # 0-1
    
    filters_passed: list[str]
    filters_failed: list[str]
    
    regime_T: float
    in_trading_regime: bool
    expected_edge: float
```

### OrderRequest

```python
class OrderRequest(BaseModel):
    order_id: str
    trace_id: str
    symbol: str
    side: Side
    order_type: OrderType   # MARKET | LIMIT | POST_ONLY | IOC | FOK
    quantity: float          # > 0
    price: Optional[float]
    time_in_force: TimeInForce  # GTC | IOC | FOK
```

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
    fee: float
    slippage: float
    latency_ms: float
```

---

## Deployment

### EC2 Setup

```bash
# Run deployment script
./infrastructure/deploy.sh

# Or manual setup:
# 1. Install dependencies
apt-get update && apt-get install -y python3 python3-pip postgresql redis-server

# 2. Setup Python
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Setup PostgreSQL
sudo -u postgres psql -c "CREATE USER trading_user WITH PASSWORD 'password';"
sudo -u postgres psql -c "CREATE DATABASE trading_system OWNER trading_user;"
psql -U postgres -d trading_system -f infrastructure/setup_db.sql

# 4. Setup Redis
./infrastructure/setup_redis.sh

# 5. Configure and run
cp .env.example .env
# Edit .env with your settings
```

### Systemd Service

```bash
# Install service
sudo cp infrastructure/systemd/lvr-trading.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable lvr-trading

# Start/Stop/Status
sudo systemctl start lvr-trading
sudo systemctl stop lvr-trading
sudo systemctl status lvr-trading
```

### PostgreSQL Schema

The database includes:
- `positions` - Current positions (authoritative)
- `orders` - Order lifecycle
- `fills` - Fill events
- `events` - Append-only event log
- `portfolio_snapshots` - Historical portfolio states
- `risk_state` - Risk state history
- `alerts` - Alert log
- `metrics_snapshots` - Metrics history

---

## Testing

### Run Tests

```bash
# All tests
pytest tests/ -v

# Specific test file
pytest tests/test_features.py -v

# With coverage
pytest tests/ -v --cov=. --cov-report=html
```

### Test Categories

| Test File | Coverage |
|-----------|----------|
| test_features.py | NaN detection, Z-score bounds, determinism |
| test_signal.py | Filter enforcement, direction consistency |
| test_execution.py | Slippage bounds, partial fills, cost model |
| test_risk.py | Position sizing, limit enforcement |
| test_recovery.py | State reconstruction, consistency |

### Performance Criteria

| Metric | Target | Description |
|--------|--------|-------------|
| Fill Rate | > 60% | Fills / submitted orders |
| Slippage | < expected | Actual vs model slippage |
| Edge Error | ≈ 0 | Signal vs realized edge |
| Sharpe Ratio | > 1.2 | Risk-adjusted returns |

---

## Safety & Protection

### Live Trading Safety Lock

```python
# LIVE mode requires explicit confirmation
if mode == "LIVE" and not os.getenv("LVR_LIVE_CONFIRMED"):
    raise PermissionError("LIVE mode not authorized")
```

### Edge Protection

```python
# Never execute if edge destroyed by costs
real_edge = expected_edge - execution_cost
if real_edge <= 0:
    abort execution
```

### Fail-Safe Loop

```python
# Any layer failure → skip cycle, never crash
try:
    tick = await data_source.next_tick()
    features = feature_engine.update(tick)
    signal = signal_engine.generate(features)
    if signal and check_edge(signal):
        result = await executor.submit(signal)
        portfolio.update(result.fill)
except Exception as e:
    logger.error(f"Cycle failed: {e}")
    protection.handle_failure(e)  # Skip, don't crash
```

### Protection Levels

| Level | Condition | Action |
|-------|-----------|--------|
| 0 | Normal | Continue trading |
| 1 | Metrics degraded | Reduce size 50% |
| 2 | Multiple breaches | 1 order/min, block signals |
| 3 | Critical | CLOSE ALL + HALT |

### Recovery

- Auto-recovery from PostgreSQL event log
- State validation on startup
- Resume from last checkpoint

---

## Performance Criteria

The system targets the following benchmarks:

| Metric | Target | Critical |
|--------|--------|----------|
| Fill Rate | > 60% | < 50% |
| Slippage Error | ≈ 0 | > 2x expected |
| Edge Error | ≈ 0 | Signal < Realized |
| Sharpe Ratio | > 1.2 | < 0.5 |
| Max Drawdown | < 10% | > 15% |
| Daily Loss | < 3% | > 5% |

---

## License

MIT License

---

## Support

For issues and questions, please open an issue on GitHub.
