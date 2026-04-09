# Quick Start Guide

Get the LVR Trading System running in 5 minutes.

## Prerequisites

- Python 3.10+
- Git

---

## Step 1: Clone & Setup (2 min)

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
```

---

## Step 2: Run Backtest (2 min)

```bash
# Run simulation mode (backtest)
python app/main.py config/config.yaml
```

You should see output like:
```
INFO: Starting trading system mode=SIM
INFO: Running backtest
INFO: Processing 10000 ticks for BTCUSDT
INFO: Backtest complete
INFO: Final PnL: $X,XXX.XX
```

---

## Step 3: Verify Tests (1 min)

```bash
# Run test suite
pytest tests/ -v
```

Expected output:
```
tests/test_features.py::test_no_nans_on_basic_tick PASSED
tests/test_signal.py::test_signal_generation PASSED
tests/test_execution.py::test_basic_order PASSED
tests/test_risk.py::test_normal_order_approved PASSED
====== X passed in X.XXs ======
```

---

## What's Next?

### Run with Custom Data

```bash
# Edit config.yaml
nano config/config.yaml

# Point to your data
# data_source:
#   path: /path/to/your/ticks.parquet
```

### Paper Trading

```bash
export LVR_EXECUTION_MODE=PAPER
python app/main.py config/config.yaml
```

### Explore the Code

Key files to examine:

| File | Purpose |
|------|---------|
| `app/main.py` | Main trading loop |
| `features/engine.py` | Feature calculation |
| `strategy/signal.py` | Signal generation |
| `execution/simulator.py` | Execution simulation |

---

## Common Issues

### Import Errors

```bash
pip install -r requirements.txt --force-reinstall
```

### Permission Denied

```bash
chmod +x infrastructure/*.sh
```

### PostgreSQL Not Found

```bash
# Install PostgreSQL
sudo apt-get install postgresql postgresql-contrib
```

---

## Documentation

- [Full API Reference](../api/README.md)
- [Feature Engineering](../api/features.md)
- [Trading Strategy](../api/strategy.md)
- [Deployment Guide](./deployment.md)
