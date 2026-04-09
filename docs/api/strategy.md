# Trading Strategy

This document explains the trading strategy components of the LVR Trading System.

## Overview

The strategy layer transforms features into actionable trading signals through:

1. **Signal Generation** - Multi-factor scoring from features
2. **Regime Detection** - Market condition filtering
3. **Quality Filters** - Signal quality control

---

## Signal Generation

### Core Formula

```python
score = I* × (-L*) × S* × (1 - |OFI|)
direction = opposite(I*)
```

**Components:**

| Component | Formula | Role |
|-----------|---------|------|
| Impulse | I* | Price momentum direction |
| Liquidity | -L* | Favor liquid markets |
| Spread | S* | Favor tight spreads |
| Flow | (1 - \|OFI\|) | Avoid extreme imbalance |

### Signal Strength

```python
strength = min(|score|, 1.0)
```

### Signal Confidence

```python
confidence = (
    impulse_score × 0.4 +
    liquidity_score × 0.2 +
    spread_score × 0.2 +
    flow_score × 0.2
)
```

**Component Scores:**

| Score | Calculation | Range |
|-------|-------------|-------|
| impulse_score | abs(I*) | [0, 1] |
| liquidity_score | 1 - min(abs(L*)/3, 1) | [0, 1] |
| spread_score | 1 - min(abs(S*)/3, 1) | [0, 1] |
| flow_score | 1 - abs(OFI) | [0, 1] |

### Direction Logic

```python
if I* > 0:
    direction = SELL  # Price up → expect reversal
else:
    direction = BUY   # Price down → expect reversal
```

**Rationale:** Mean reversion strategy - fade extreme moves.

---

## Filter Pipeline

### 1. OFI Filter

**Rule:** Reject if |OFI| > threshold

**Default:** threshold = 0.7

**Rationale:** Extreme order flow imbalance indicates directional pressure that may continue rather than reverse.

```
|    Extreme    |    Normal     |   Extreme   |
|   Buy Bias    |   Balanced    |  Sell Bias  |
|     Reject    |    Accept     |    Reject   |
|  -1    -0.7  |  -0.7  0.7   |   0.7  +1   |
```

### 2. Microstructure Reversal Filter

**Rule:** Require score > 0 (mean reversion setup)

**Rationale:** Only trade when momentum suggests potential reversal.

### 3. Regime Filter

**Rule:** Block if T = |returns|/volatility > threshold

**Default:** threshold = 2.0

**Rationale:** In high-volatility regimes, mean reversion fails more often.

### 4. Confidence Filter

**Rule:** Reject if confidence < min_confidence

**Default:** min_confidence = 0.3

### 5. Edge Filter

**Rule:** Reject if expected_edge <= 0

**Rationale:** No point trading if expected value is negative.

---

## Signal Validation

A signal is valid if:

```python
is_valid = (
    in_trading_regime and
    len(filters_failed) == 0 and
    confidence >= min_confidence and
    expected_edge > 0
)
```

---

## Regime Detection

### Regime T Metric

```python
T = |returns| / volatility
```

**Interpretation:**

| T Range | Regime | Action |
|---------|--------|--------|
| T <= 1 | Low volatility | Normal trading |
| 1 < T <= 2 | Elevated | Reduce size |
| T > 2 | High volatility | BLOCK ALL |

### Regime Statistics

```python
@dataclass
class RegimeStats:
    blocked_pct: float      # % of ticks blocked
    total_checks: int      # Total regime checks
    avg_T: float           # Average T value
```

---

## Strategy Parameters

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| ofi_threshold | 0.7 | [0.5, 0.9] | OFI filter threshold |
| regime_threshold | 2.0 | [1.5, 3.0] | Regime T threshold |
| min_confidence | 0.3 | [0.2, 0.5] | Minimum signal confidence |
| signal_decay | 0.95 | [0.9, 0.99] | Decay for consecutive signals |

### Parameter Tuning Guidelines

**ofi_threshold:**
- Lower (0.5): Fewer trades, higher quality
- Higher (0.8): More trades, lower quality

**regime_threshold:**
- Lower (1.5): Block more, safer
- Higher (2.5): Allow more, riskier

**signal_decay:**
- Lower (0.9): Faster decay, avoid overtrading
- Higher (0.99): Slower decay, more signals

---

## Signal Decay Mechanism

When consecutive signals have the same direction:

```python
if previous_direction == current_direction:
    confidence *= signal_decay
```

**Purpose:** Prevent overtrading in trending conditions.

---

## Expected Edge Estimation

```python
expected_edge = strength × confidence
```

**Bayesian Update:**
```python
# After trade
actual_edge = realized_return - expected_return

# Update estimate
new_edge = 0.9 × old_edge + 0.1 × actual_edge
```

**Constraints:**
- Minimum 30 trades before full weight
- Maximum 5% change per update
- 10-tick cooldown between updates

---

## Signal Lifecycle

```
┌─────────────────────────────────────────────────────────┐
│                    SIGNAL LIFECYCLE                      │
├─────────────────────────────────────────────────────────┤
│                                                          │
│   FeatureVector                                          │
│        │                                                 │
│        ▼                                                 │
│   ┌─────────────┐                                        │
│   │ OFI Filter │ ─── Reject ────► filters_failed        │
│   └─────────────┘                                        │
│        │                                                 │
│        ▼                                                 │
│   ┌─────────────┐                                        │
│   │ Reversal?   │ ─── Reject ────► filters_failed        │
│   └─────────────┘                                        │
│        │                                                 │
│        ▼                                                 │
│   ┌─────────────┐                                        │
│   │ Regime OK?  │ ─── Block ────► in_trading_regime=False│
│   └─────────────┘                                        │
│        │                                                 │
│        ▼                                                 │
│   ┌─────────────┐                                        │
│   │ Confidence  │ ─── Reject ────► filters_failed        │
│   │   >= 0.3?   │                                        │
│   └─────────────┘                                        │
│        │                                                 │
│        ▼                                                 │
│   ┌─────────────┐                                        │
│   │  Edge > 0?  │ ─── Reject ────► filters_failed        │
│   └─────────────┘                                        │
│        │                                                 │
│        ▼                                                 │
│     Signal                                               │
│        │                                                 │
│        ▼                                                 │
│   ┌─────────────┐                                        │
│   │ Valid?      │                                        │
│   └─────────────┘                                        │
│        │                                                 │
│   Yes ─┴── No                                            │
│    │      │                                              │
│    ▼      ▼                                              │
│  Trade  Discard                                          │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

---

## Usage Example

```python
from app.schemas import FeatureVector, Side
from strategy import SignalGenerator, RegimeDetector

# Initialize
signal_gen = SignalGenerator(
    ofi_threshold=0.7,
    min_confidence=0.3
)
regime_det = RegimeDetector(threshold=2.0)

# Generate signal
features = FeatureVector(
    timestamp=1609459200000,
    symbol="BTCUSDT",
    I_star=0.5,
    L_star=-0.2,
    S_star=0.3,
    OFI=0.1,
    depth_imbalance=0.0
)

# Generate
signal = signal_gen.generate(features)

if signal:
    # Check regime
    signal = regime_det.apply_to_signal(signal)
    
    if signal.is_valid:
        print(f"Signal: {signal.direction} {signal.symbol}")
        print(f"Strength: {signal.strength:.2f}")
        print(f"Confidence: {signal.confidence:.2f}")
        print(f"Expected Edge: {signal.expected_edge:.5f}")
```

---

## Backtesting Considerations

### Overfitting Prevention

1. **Out-of-sample testing** - Never tune on test data
2. **Walk-forward analysis** - Validate on rolling windows
3. **Parameter bounds** - Constrain to reasonable ranges

### Signal Quality Metrics

| Metric | Good | Bad |
|--------|------|-----|
| Signal rate | 5-20% of ticks | <1% or >50% |
| Win rate | >50% | <45% |
| Avg win/loss | >1.5 | <1.0 |
| Sharpe ratio | >1.2 | <0.5 |

---

## Regime-Specific Behavior

### Normal Regime (T <= 1.5)
- Full signal generation
- Standard position sizing

### Elevated Regime (1.5 < T <= 2.0)
- Continue trading
- Reduce position size by 25%

### High Volatility Regime (T > 2.0)
- Block all new signals
- Hold existing positions
- Increase stop loss proximity
