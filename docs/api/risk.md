# Risk Management API

Comprehensive risk management for trading operations.

## Risk Engine

The `RiskEngine` class provides defense-in-depth risk management with graduated protection levels.

### Initialization

```python
from risk.limits import RiskEngine, RiskLimits

# Default limits
engine = RiskEngine()

# Custom limits
limits = RiskLimits(
    max_leverage=5.0,           # Maximum 5x leverage
    max_drawdown_pct=0.05,      # 5% max drawdown
    max_daily_loss_pct=0.02,    # 2% daily loss limit
    max_position_size_pct=0.15, # 15% max position
    max_consecutive_losses=3    # 3 loss streak limit
)
engine = RiskEngine(limits)
```

### Order Checking

```python
result = engine.check_order(
    order=order_request,
    signal=signal,
    portfolio=portfolio,
    risk_state=risk_state
)

if not result.approved:
    print(f"Rejected: {result.rejection_reason}")

for action in result.required_actions:
    print(f"Action: {action}")
```

### Protection Levels

| Level | Name | Description |
|-------|------|-------------|
| 0 | NONE | Normal operation |
| 1 | REDUCE_SIZE | Reduce positions by 50% |
| 2 | RESTRICT_TRADING | Block new orders |
| 3 | CLOSE_ALL_HALT | Close all, halt system |

### Protection Level Evaluation

```python
# After each trade
engine.record_trade_result(pnl=-100)

# Evaluate protection level
level = engine.evaluate_protection_level(portfolio)
if level >= ProtectionLevel.CLOSE_ALL_HALT:
    print("HALTING TRADING")
    engine.apply_protection_action(level)
```

### Manual Operations

```python
# Reset engine state
engine.reset()

# Unhalt after manual review
engine.unhalt()
```

## Position Sizing

The `PositionSizer` class calculates optimal position sizes based on risk parameters.

### Initialization

```python
from risk.sizing import PositionSizer, AdaptivePositionSizer

sizer = PositionSizer(
    base_risk_per_trade=0.01,  # 1% risk per trade
    max_leverage=10.0,         # Max 10x leverage
    min_position=0.001         # Minimum 0.001 contracts
)

# Adaptive sizing based on recent performance
adaptive_sizer = AdaptivePositionSizer(
    base_risk_per_trade=0.01,
    max_leverage=10.0,
    adaptation_rate=0.1        # 10% adaptation rate
)
```

### Size Calculation

```python
size = sizer.calculate_size(
    signal=signal,
    portfolio=portfolio,
    risk_state=risk_state,
    current_price=50000.0,
    volatility=0.0015
)
print(f"Position size: {size:.4f}")
```

### Size Formula

```
size = (base_risk × capital × confidence × multipliers) / (leverage × volatility × price)

multipliers:
- loss_streak_mult = max(0.5, 1 - consecutive_losses × 0.1)
- drawdown_mult = max(0.5, 1 - drawdown × 2)
```

### Stop Loss and Take Profit

```python
# Calculate stop loss
stop = sizer.calculate_stop_loss(
    entry_price=50000.0,
    signal=signal,
    volatility=0.0015,
    atr_multiplier=2.0  # 2x ATR
)

# Calculate take profit
profit = sizer.calculate_take_profit(
    entry_price=50000.0,
    signal=signal,
    risk_reward_ratio=2.0,
    stop_loss=stop
)

print(f"Entry: 50000, Stop: {stop}, Target: {profit}")
```

### Adaptive Sizing

```python
# Record trade returns
adaptive_sizer.record_return(0.02)   # 2% win
adaptive_sizer.record_return(-0.01)   # 1% loss

# Get adaptation info
info = adaptive_sizer.get_adaptation_info()
print(f"Multiplier: {info['current_multiplier']:.2f}")
```

## Risk Limits Configuration

### Default Limits

```python
@dataclass
class RiskLimits:
    # Hard limits
    max_leverage: float = 10.0           # 10x max leverage
    max_drawdown_pct: float = 0.10        # 10% max drawdown
    max_daily_loss_pct: float = 0.03     # 3% daily loss
    max_position_size_pct: float = 0.20  # 20% max position
    max_consecutive_losses: int = 5      # 5 consecutive losses
    
    # Soft limits (warnings)
    position_warning_pct: float = 0.15    # 15% position warning
    daily_loss_warning_pct: float = 0.02 # 2% daily loss warning
    consecutive_loss_warning: int = 3     # 3 consecutive loss warning
```

### Conservative Settings

```python
conservative_limits = RiskLimits(
    max_leverage=3.0,
    max_drawdown_pct=0.05,
    max_daily_loss_pct=0.015,
    max_position_size_pct=0.10,
    max_consecutive_losses=2
)
```

### Aggressive Settings

```python
aggressive_limits = RiskLimits(
    max_leverage=20.0,
    max_drawdown_pct=0.20,
    max_daily_loss_pct=0.05,
    max_position_size_pct=0.30,
    max_consecutive_losses=7
)
```

## Risk State

The `RiskState` tracks current risk metrics:

```python
@dataclass
class RiskState:
    current_leverage: float = 0.0
    current_drawdown: float = 0.0
    daily_loss: float = 0.0
    leverage_ok: bool = True
    drawdown_ok: bool = True
    daily_loss_ok: bool = True
    consecutive_losses: int = 0
    protection_level: ProtectionLevel = ProtectionLevel.NONE
```

## Risk Check Result

```python
@dataclass
class RiskCheckResult:
    approved: bool
    risk_state: RiskState
    adjusted_quantity: Optional[float] = None
    rejection_reason: Optional[str] = None
    required_actions: list[str] = field(default_factory=list)
```

### Checking Result

```python
result = engine.check_order(order, signal, portfolio, risk_state)

# Check approval
if not result.approved:
    print(f"Order rejected: {result.rejection_reason}")

# Check for adjustments
if result.adjusted_quantity:
    print(f"Position adjusted to {result.adjusted_quantity}")

# Check required actions
for action in result.required_actions:
    print(f"Action required: {action}")
```
