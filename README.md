# LVR Trading System

Production-grade autonomous trading system for Binance Futures with tick-level execution simulation.

## Features

- **Tick-level Data Processing**: No OHLCV-only systems
- **Feature Engineering**: I*, L*, S*, OFI, depth imbalance with rolling statistics
- **Signal Generation**: Multi-factor scoring with filters and regime detection
- **Execution Abstraction Layer**: SIM, PAPER, LIVE modes
- **Risk Management**: Position sizing, limits, protection levels
- **Bayesian Learning**: Adaptive edge estimation with bounded updates
- **State Management**: PostgreSQL + Redis + Event Log architecture
- **Monitoring**: Real-time metrics, alerts, anomaly detection
- **Protection System**: Multi-level response (reduce size → restrict → close all)

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    TRADING SYSTEM CORE                           │
├─────────────────────────────────────────────────────────────────┤
│  DATA LAYER          │  FEATURE ENGINE   │  SIGNAL ENGINE       │
│  - Tick loader       │  - Rolling stats  │  - Scoring           │
│  - Replay engine     │  - Z-score norm   │  - Filters           │
├──────────────────────┴───────────────────┴─────────────────────┤
│  EXECUTION ABSTRACTION LAYER                                     │
│  ┌──────────┐  ┌──────────┐  ┌────────────────┐                  │
│  │   SIM    │  │  PAPER   │  │  LIVE (vn.py)  │                  │
│  └──────────┘  └──────────┘  └────────────────┘                  │
├─────────────────────────────────────────────────────────────────┤
│  RISK + PORTFOLIO │  LEARNING + ATTRIBUTION │  MONITORING        │
└─────────────────────────────────────────────────────────────────┘
```

## Quick Start

### Installation

```bash
# Clone repository
git clone https://github.com/YOUR_USERNAME/lvr_trading_system.git
cd lvr_trading_system

# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env
# Edit .env with your configuration
```

### Running

```bash
# Backtest (SIM mode)
python app/main.py

# Paper trading (PAPER mode)
export LVR_EXECUTION_MODE=PAPER
python app/main.py

# Live trading (LIVE mode - requires confirmation)
export LVR_LIVE_CONFIRMED=true
python app/main.py
```

### Testing

```bash
pytest tests/ -v
```

## Configuration

Edit `config/config.yaml` for system settings:

```yaml
system:
  mode: SIM  # SIM | PAPER | LIVE

exchange:
  symbols:
    - BTCUSDT
    - ETHUSDT

risk:
  max_leverage: 10
  limits:
    max_drawdown_pct: 0.10
    max_daily_loss_pct: 0.03
```

## Execution Modes

| Mode | Engine | Description |
|------|--------|-------------|
| SIM | Simulated | Deterministic backtesting |
| PAPER | Paper | Real market data, simulated execution |
| LIVE | Vnpy | Real trading (requires authorization) |

## Risk Protection Levels

| Level | Trigger | Action |
|-------|---------|--------|
| 1 | Metrics degraded | Reduce size 50% |
| 2 | Multiple breaches | Restrict to 1 order/min |
| 3 | Critical | Close ALL + HALT |

## Project Structure

```
lvr_trading_system/
├── app/              # Main application
├── config/           # Configuration files
├── data/             # Data loading and replay
├── features/         # Feature engineering
├── strategy/         # Signal generation
├── execution/        # Execution engines
├── portfolio/        # Portfolio management
├── risk/             # Risk management
├── learning/        # Bayesian learning
├── monitoring/       # Monitoring and alerts
├── state/           # State persistence
├── infrastructure/  # Deployment files
└── tests/           # Test suite
```

## Safety Features

- LIVE mode requires explicit `LVR_LIVE_CONFIRMED=true`
- All execution through abstract interface
- Edge protection: `real_edge > execution_cost` required
- Fail-safe main loop: any exception → skip cycle
- Automatic checkpointing

## License

MIT License
