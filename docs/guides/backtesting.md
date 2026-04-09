# Backtesting Guide

How to run and interpret backtests with the LVR Trading System.

## Overview

The backtesting engine provides tick-by-tick simulation with realistic execution modeling.

---

## Quick Start

```bash
# Run backtest
python app/main.py config/config.yaml

# Run with custom data
python app/main.py --data /path/to/ticks.csv config/config.yaml
```

---

## Data Format

### CSV Format

```csv
timestamp,price,size,side
1609459200000,50000.0,0.1,buy
1609459200100,50001.0,0.15,sell
```

| Column | Type | Description |
|--------|------|-------------|
| timestamp | int | Milliseconds since epoch |
| price | float | Trade price |
| size | float | Trade size |
| side | string | "buy" or "sell" |

### Parquet Format

```python
import pandas as pd

df = pd.DataFrame({
    'timestamp': [1609459200000, ...],
    'price': [50000.0, ...],
    'size': [0.1, ...],
    'side': ['buy', ...]
})

df.to_parquet('ticks.parquet', engine='pyarrow')
```

---

## Running Backtests

### Basic Backtest

```python
from data import DataLoader, SyncReplayEngine, generate_test_dataset
from features import FeatureEngine
from strategy import SignalGenerator
from execution import SimulatedExecutionEngine
from portfolio import PortfolioManager
from risk import RiskEngine, PositionSizer

# Load data
loader = DataLoader()
ticks = list(loader.load_csv('data/BTCUSDT.csv', 'BTCUSDT'))

# Initialize components
executor = SimulatedExecutionEngine()
portfolio = PortfolioManager(initial_capital=100000)
risk_engine = RiskEngine()
sizer = PositionSizer()
feature_engine = FeatureEngine()
signal_gen = SignalGenerator()

# Run replay
replay = SyncReplayEngine(ticks)

def on_tick(index, tick):
    # Compute features
    features = feature_engine.update(tick)
    
    # Generate signal
    signal = signal_gen.generate(features)
    
    if signal and signal.is_valid:
        # Size and execute
        size = sizer.calculate_size(signal, portfolio.portfolio, ...)
        order = OrderRequest(..., quantity=size)
        result = await executor.submit_order(order)
        
        # Update portfolio
        for fill in result.fill_events:
            portfolio.update_from_fill(fill)

replay.on_tick(on_tick)
replay.run()
```

---

## Interpreting Results

### Key Metrics

| Metric | Good | Bad |
|--------|-------|-----|
| Total Return | > 20% | < 0% |
| Sharpe Ratio | > 1.5 | < 0.5 |
| Max Drawdown | < 10% | > 20% |
| Win Rate | > 55% | < 45% |
| Profit Factor | > 1.5 | < 1.0 |

### Sample Output

```
=== Backtest Results ===
Initial Capital:    $100,000.00
Final Capital:       $112,450.00
Total Return:        +12.45%

=== Risk Metrics ===
Max Drawdown:        -5.23%
Sharpe Ratio:        1.82
Win Rate:            58.3%
Profit Factor:       1.67

=== Trading Stats ===
Total Trades:        127
Winning Trades:      74
Losing Trades:       53
Avg Win:             $185.42
Avg Loss:            -$98.73

=== Execution Metrics ===
Avg Slippage:        $2.15
Fill Rate:           94.2%
Total Costs:         $342.18
```

---

## Common Pitfalls

### Overfitting

**Problem:** Strategy too tuned to historical data

**Signs:**
- Very high Sharpe in-sample
- Poor out-of-sample performance
- Many parameters with fine-tuned values

**Prevention:**
- Use walk-forward validation
- Keep parameter count low
- Test on multiple data periods

### Look-Ahead Bias

**Problem:** Using future information

**Signs:**
- Unrealistic returns
- Strategy doesn't work in live trading

**Prevention:**
- Never use future prices in signal calculation
- Use only data available at each tick
- Proper timestamp handling

### Survivorship Bias

**Problem:** Only testing existing symbols

**Prevention:**
- Include delisted/crashed assets
- Test on diverse universe
- Use point-in-time data

---

## Optimization

### Parameter Scanning

```python
# Grid search
for ofi_threshold in [0.5, 0.6, 0.7, 0.8]:
    for regime_threshold in [1.5, 2.0, 2.5]:
        signal_gen = SignalGenerator(
            ofi_threshold=ofi_threshold,
            regime_threshold=regime_threshold
        )
        results = run_backtest(signal_gen)
        print(f"{ofi_threshold}, {regime_threshold}: {results['sharpe']}")
```

### Walk-Forward Analysis

```python
# Split data
train_size = int(len(ticks) * 0.7)
train_ticks = ticks[:train_size]
test_ticks = ticks[train_size:]

# Optimize on train
optimized_params = optimize(train_ticks)

# Validate on test
test_results = run_backtest(test_ticks, **optimized_params)
```

---

## Performance Tips

### Speed Optimization

| Technique | Speedup |
|-----------|---------|
| Use SyncReplayEngine | 2-5x |
| Reduce rolling windows | 1.5-2x |
| Disable logging | 1.2-1.5x |
| Batch processing | 2-3x |

### Memory Optimization

```python
# Clear state periodically
if index % 10000 == 0:
    feature_engine.reset()  # Clear rolling windows
```

---

## Validation Checklist

Before trusting backtest results:

- [ ] No look-ahead bias
- [ ] Realistic execution model
- [ ] Transaction costs included
- [ ] Slippage modeled
- [ ] Out-of-sample tested
- [ ] Walk-forward validated
- [ ] Results reproducible
