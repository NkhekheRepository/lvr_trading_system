# Feature Engineering

This document explains the feature engineering system in the LVR Trading System.

## Overview

The feature engine transforms raw tick data and order book snapshots into normalized features used for signal generation. All features are designed to be:

- **Numerically stable**: No NaN values, division-by-zero protection
- **Deterministic**: Same input always produces same output
- **Normalized**: Z-scores centered around zero
- **Bounded**: Output values constrained to reasonable ranges

## Feature Definitions

### I* - Normalized Returns

**Purpose:** Capture price momentum normalized by volatility.

**Formula:**
```
I* = returns_t / (volatility_t + ε)
```

**Where:**
- `returns_t = log(price_t / price_{t-1})`
- `volatility_t = std(returns_{t-window:t})`
- `ε = 1e-10` (prevent division by zero)

**Interpretation:**
| I* Range | Interpretation |
|----------|----------------|
| I* > 2 | Strong upward impulse |
| 0 < I* < 2 | Mild upward impulse |
| -2 < I* < 0 | Mild downward impulse |
| I* < -2 | Strong downward impulse |

**Constraints:**
- Clipped to [-10, 10]
- Returns 0 if volatility is extremely low

---

### L* - Depth Z-Score

**Purpose:** Detect abnormal liquidity conditions.

**Formula:**
```
L* = (depth_t - μ_depth) / (σ_depth + ε)
```

**Where:**
- `depth_t = (bid_depth_t + ask_depth_t) / 2`
- `μ_depth = mean(depth_{t-window:t})`
- `σ_depth = std(depth_{t-window:t})`

**Interpretation:**
| L* Range | Interpretation |
|----------|----------------|
| L* > 2 | Abnormally high liquidity |
| -2 < L* < 2 | Normal liquidity |
| L* < -2 | Abnormally low liquidity |

**Constraints:**
- Clipped to [-10, 10]

---

### S* - Spread Z-Score

**Purpose:** Detect abnormal bid-ask spread conditions.

**Formula:**
```
S* = (spread_t - μ_spread) / (σ_spread + ε)
```

**Where:**
- `spread_t = best_ask - best_bid`
- `μ_spread = mean(spread_{t-window:t})`
- `σ_spread = std(spread_{t-window:t})`

**Interpretation:**
| S* Range | Interpretation |
|----------|----------------|
| S* > 2 | Abnormally wide spread |
| -2 < S* < 2 | Normal spread |
| S* < -2 | Abnormally tight spread |

**Constraints:**
- Clipped to [-10, 10]

---

### OFI - Order Flow Imbalance

**Purpose:** Capture net order flow direction and intensity.

**Formula:**
```
OFI = (Σ Δbid_positive - Σ Δask_positive) / (Σ |Δbid| + Σ |Δask| + ε)
```

**Where:**
- `Δbid_positive` = positive bid depth changes
- `Δask_positive` = positive ask depth changes
- Computed over last 10 ticks

**Interpretation:**
| OFI Range | Interpretation |
|-----------|----------------|
| OFI > 0.3 | Strong buy pressure |
| 0 < OFI < 0.3 | Mild buy pressure |
| -0.3 < OFI < 0 | Mild sell pressure |
| OFI < -0.3 | Strong sell pressure |

**Constraints:**
- Clipped to [-1, 1]

---

### Depth Imbalance

**Purpose:** Snapshot-based liquidity imbalance.

**Formula:**
```
depth_imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth + ε)
```

**Interpretation:**
| Value | Interpretation |
|-------|----------------|
| > 0.2 | Buy-side dominant |
| -0.2 to 0.2 | Balanced |
| < -0.2 | Sell-side dominant |

**Constraints:**
- Clipped to [-1, 1]

---

## Rolling Windows

| Window | Size | Purpose |
|--------|------|---------|
| Returns | 50 ticks | Impulse calculation |
| Volatility | 100 ticks | I* normalization |
| Depth | 100 ticks | L* z-score |
| Spread | 100 ticks | S* z-score |
| OFI | 10 ticks | Order flow |

### Window Size Rationale

**Returns Window (50):**
- Balances responsiveness vs noise
- Short enough for intraday signals
- Long enough to filter random fluctuations

**Volatility Window (100):**
- Captures full volatility cycle
- Stable estimate of "normal" volatility
- 2x returns window for statistical stability

**Depth/Spread Windows (100):**
- Similar rationale to volatility
- Captures regime changes in liquidity

---

## Feature Engineering Pipeline

```
Raw Data
    │
    ▼
┌─────────────────────────────────────┐
│         Data Validation             │
│  - Timestamp normalization (ms)     │
│  - Price > 0                        │
│  - Size >= 0                        │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│       Rolling Window Updates         │
│  - Append to rolling deques          │
│  - Maintain maxlen for memory        │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│       Feature Computation            │
│  - Calculate raw features            │
│  - Apply z-score normalization       │
│  - Clip to bounds                    │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│       NaN Prevention                 │
│  - ε for division                   │
│  - Zero-fill on insufficient data    │
│  - Validation check                  │
└─────────────────────────────────────┘
    │
    ▼
   Output
```

---

## Usage Example

```python
from app.schemas import TradeTick, OrderBookSnapshot, Side
from features import FeatureEngine

# Initialize engine
engine = FeatureEngine(
    return_window=50,
    volatility_window=100,
    depth_window=100,
    spread_window=100
)

# Process tick
tick = TradeTick(
    timestamp=1609459200000,
    symbol="BTCUSDT",
    price=50000.0,
    size=0.1,
    side=Side.BUY
)

# Optional order book
book = OrderBookSnapshot(
    timestamp=1609459200000,
    symbol="BTCUSDT",
    bids=[(49999.0, 1.0), (49998.0, 2.0)],
    asks=[(50001.0, 1.0), (50002.0, 2.0)]
)

# Compute features
features = engine.update(tick, book)

print(f"I*: {features.I_star:.4f}")
print(f"L*: {features.L_star:.4f}")
print(f"S*: {features.S_star:.4f}")
print(f"OFI: {features.OFI:.4f}")
```

---

## Signal Components

Features combine into signal components:

| Component | Formula | Weight |
|-----------|---------|--------|
| Impulse | abs(I*) | 0.4 |
| Liquidity | 1 - min(abs(L*)/3, 1) | 0.2 |
| Spread | 1 - min(abs(S*)/3, 1) | 0.2 |
| Flow | 1 - abs(OFI) | 0.2 |

**Signal Score:**
```
score = I* × (-L*) × S* × (1 - |OFI|)
```

---

## Stability Guarantees

### Division by Zero Prevention
```python
EPS = 1e-10
volatility = max(calculated_volatility, EPS)
```

### NaN Handling
```python
if features.has_nans():
    logger.warning("NaN detected, returning zeros")
    return zero_features()
```

### Clipping
```python
I_star = float(np.clip(I_star, -10, 10))
```

### Insufficient Data
When rolling windows don't have enough data:
- Returns: Use 0
- Z-scores: Use 0 (center of distribution)
- OFI: Use 0 (neutral)

---

## Performance Considerations

| Operation | Complexity |
|-----------|------------|
| Rolling append | O(1) |
| Std calculation | O(window) per update |
| Full feature | O(window) worst case |

**Optimization:** Using `deque` with `maxlen` for automatic eviction.

---

## Testing

Run feature tests:
```bash
pytest tests/test_features.py -v
```

**Test Coverage:**
- No NaN on varied input
- No NaN on extreme values
- Deterministic output
- Z-score bounds
- OFI bounds [-1, 1]
- Depth imbalance bounds [-1, 1]
