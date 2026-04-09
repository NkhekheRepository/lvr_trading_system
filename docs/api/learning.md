# Learning API

Bayesian learning and PnL attribution for continuous strategy improvement.

## Bayesian Learning

The `BayesianLearner` maintains conjugate prior distributions for win rate and PnL magnitude.

### Initialization

```python
from learning.bayes import BayesianLearner, AdaptiveLearner

learner = BayesianLearner(
    min_samples=30,              # Min trades before full weight
    update_rate=0.1,             # 10% learning rate
    max_change_per_update=0.05,  # Max 5% change per update
    cooldown_ticks=10           # 10 tick cooldown
)
```

### Updating from Trades

```python
from app.schemas import FillEvent, Side

# After each trade fill
fill = FillEvent(
    event_id="evt_001",
    trace_id="trace_001",
    order_id="ord_001",
    timestamp=1700000000000,
    symbol="BTCUSDT",
    side=Side.BUY,
    quantity=0.1,
    price=50000.0,
    fee=2.0,
    slippage=1.0
)

state = learner.update(fill)
print(f"Win rate: {state.win_rate:.1%}")
print(f"Edge: {state.expected_edge:.5f}")
print(f"Confidence: {state.confidence:.2f}")
```

### Bayesian State

```python
@dataclass
class BayesianState:
    symbol: str
    trade_count: int = 0
    win_count: int = 0
    
    # Beta distribution parameters (win rate)
    alpha: float = 1.0  # Wins + 1
    beta: float = 1.0   # Losses + 1
    
    # Normal distribution parameters (PnL)
    mean_pnl: float = 0.0
    std_pnl: float = 0.0
    
    confidence: float = 0.0
    expected_edge: float = 0.0
    last_update: int = 0
```

### Win Rate Estimation

The Beta distribution parameters encode the win rate belief:

```python
state = learner.get_state("BTCUSDT")

# Win rate estimate
win_rate = state.win_rate  # alpha / (alpha + beta)

# Credible interval (approximate)
# For large samples: mean ± 2*std
std_win_rate = np.sqrt(
    (state.alpha * state.beta) / 
    ((state.alpha + state.beta)**2 * (state.alpha + state.beta + 1)
)
```

### Checking Reliability

```python
if learner.is_reliable("BTCUSDT"):
    edge = learner.get_edge_estimate("BTCUSDT")
    print(f"Estimated edge: {edge:.5f}")
else:
    print("Not enough samples yet")
```

### Resetting State

```python
# Reset single symbol
learner.reset("BTCUSDT")

# Reset all symbols
learner.reset()
```

## Adaptive Learning

The `AdaptiveLearner` extends Bayesian learning with regime awareness.

```python
adaptive = AdaptiveLearner(
    min_samples=30,
    update_rate=0.1
)

# Update with regime context
state = adaptive.update(
    fill=fill,
    expected_edge=0.001,
    regime="high_volatility"  # Current market regime
)

# Update regime performance
adaptive.update_regime("high_volatility", performance=0.8)
adaptive.update_regime("trending", performance=1.2)
```

### Regime Multipliers

Regime multipliers adjust the learning rate based on regime performance:

| Regime | Performance | Multiplier |
|--------|-------------|------------|
| trending | 1.3 | 1.3x faster learning |
| ranging | 0.9 | 0.9x slower learning |
| high_vol | 0.7 | 0.7x slower learning |

## PnL Attribution

The attribution module provides detailed analysis of trading performance.

### Initialization

```python
from learning.attribution import PnLAttributor

attributor = PnLAttributor()
```

### Recording Trades

```python
from app.schemas import FillEvent, Side

# Entry trade
entry = FillEvent(
    symbol="BTCUSDT",
    side=Side.BUY,
    quantity=0.1,
    price=50000.0,
    timestamp=1700000000000
)
attributor.record_entry(entry)

# Exit trade
exit_fill = FillEvent(
    symbol="BTCUSDT",
    side=Side.SELL,
    quantity=0.1,
    price=51000.0,
    timestamp=1700001000000
)
attributor.record_exit(exit_fill)

# Get attribution
attribution = attributor.get_attribution("BTCUSDT")
print(f"Total PnL: {attribution.total_pnl}")
print(f"Entry price: {attribution.entry_price}")
print(f"Exit price: {attribution.exit_price}")
print(f"Holding time: {attribution.holding_time}s")
```

### Attribution Breakdown

```python
@dataclass
class TradeAttribution:
    symbol: str
    entry_price: float
    exit_price: float
    quantity: float
    gross_pnl: float
    fees: float
    slippage_cost: float
    net_pnl: float
    holding_time: float
    entry_timestamp: int
    exit_timestamp: int
    
    # Component attribution
    direction_pnl: float      # PnL from directional bet
    spread_pnl: float         # PnL from spread changes
    timing_pnl: float         # PnL from entry timing
```

### Strategy Attribution

```python
# Record trade with strategy metadata
attributor.record_trade(
    fill=exit_fill,
    strategy="mean_reversion",
    regime="normal",
    signal_strength=0.8,
    confidence=0.9
)

# Get attribution by strategy
strategy_attribution = attributor.get_by_strategy("mean_reversion")
print(f"Strategy PnL: {strategy_attribution.total_pnl}")
print(f"Win rate: {strategy_attribution.win_rate}")
print(f"Avg hold time: {strategy_attribution.avg_holding_time}")
```

### Performance Metrics

```python
metrics = attributor.get_metrics()

print(f"Total trades: {metrics.total_trades}")
print(f"Win rate: {metrics.win_rate:.1%}")
print(f"Profit factor: {metrics.profit_factor:.2f}")
print(f"Sharpe ratio: {metrics.sharpe_ratio:.2f}")
print(f"Max drawdown: {metrics.max_drawdown:.2%}")
```

## Edge Estimation

The edge estimate combines win rate and average win/loss:

```
edge = win_rate × avg_win - (1 - win_rate) × |avg_loss|
```

### Manual Edge Calculation

```python
from app.schemas import BayesianState

def calculate_edge(state: BayesianState) -> float:
    """Calculate edge from Bayesian state."""
    win_rate = state.win_rate
    
    # Estimate avg win/loss from mean_pnl and std_pnl
    # This is simplified - real implementation would track separately
    avg_return = state.mean_pnl
    
    # Edge approximation
    win_contribution = win_rate * max(avg_return, 0)
    loss_contribution = (1 - win_rate) * abs(min(avg_return, 0))
    
    return win_contribution - loss_contribution

edge = calculate_edge(learner.get_state("BTCUSDT"))
```

## Stability Mechanisms

The learning system includes several mechanisms to prevent instability:

### 1. Minimum Samples

```python
if state.trade_count < min_samples:
    # Use reduced learning rate
    weight = update_rate * 0.1  # 10% of normal weight
else:
    weight = update_rate
```

### 2. Cooldown

```python
if not learner._check_cooldown(symbol):
    return  # Skip update, in cooldown period
```

### 3. Bounded Changes

```python
change = abs(new_mean - old_mean) / abs(old_mean)
if change > max_change_per_update:
    # Limit the change
    new_mean = old_mean + np.sign(change) * old_mean * max_change_per_update
```

### 4. Clamped Parameters

```python
state.alpha = max(1.0, new_alpha)  # Never go below 1
state.beta = max(1.0, new_beta)    # Never go below 1
```
