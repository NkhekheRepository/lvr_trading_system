# Configuration Reference

Complete reference for all configuration options.

## config.yaml Structure

```yaml
system:              # System settings
exchange:           # Exchange configuration
execution:          # Execution parameters
risk:               # Risk management
features:           # Feature windows
strategy:           # Strategy parameters
portfolio:          # Portfolio settings
learning:           # Learning parameters
monitoring:         # Monitoring settings
state:              # State management
database:           # PostgreSQL config
redis:              # Redis config
```

---

## system

```yaml
system:
  name: lvr_trading_system    # System name
  version: 1.0.0              # Version
  mode: SIM                   # SIM | PAPER | LIVE
  environment: development    # development | staging | production
  log_level: INFO            # DEBUG | INFO | WARNING | ERROR
  log_dir: logs               # Log directory
  live_confirm_required: true # Require explicit LIVE confirmation
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | string | lvr_trading_system | System identifier |
| `version` | string | 1.0.0 | Version number |
| `mode` | enum | SIM | Execution mode |
| `environment` | enum | development | Runtime environment |
| `log_level` | enum | INFO | Logging level |
| `log_dir` | string | logs | Log output directory |
| `live_confirm_required` | bool | true | Safety lock for LIVE |

---

## exchange

```yaml
exchange:
  name: binance_futures
  testnet: true
  rate_limits:
    orders_per_second: 10
    requests_per_second: 120
    max_open_orders: 50
  symbols:
    - BTCUSDT
    - ETHUSDT
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | string | binance_futures | Exchange name |
| `testnet` | bool | true | Use testnet |
| `rate_limits.orders_per_second` | int | 10 | Max order rate |
| `rate_limits.requests_per_second` | int | 120 | Max API requests |
| `rate_limits.max_open_orders` | int | 50 | Max open orders |
| `symbols` | list | [BTCUSDT] | Trading symbols |

---

## execution

```yaml
execution:
  slippage_alpha: 0.5          # Slippage coefficient
  simulated_latency_ms: 100     # Backtest latency
  
  modes:
    SIM:
      deterministic_fills: true
      zero_slippage: false
    PAPER:
      use_real_market_data: true
      slippage_multiplier: 1.0
    LIVE:
      use_vnpy: true
      slippage_multiplier: 1.5
  
  fees:
    maker: 0.00020            # 0.02%
    taker: 0.00040            # 0.04%
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `slippage_alpha` | float | 0.5 | Slippage model alpha |
| `simulated_latency_ms` | int | 100 | Backtest latency |
| `fees.maker` | float | 0.00020 | Maker fee rate |
| `fees.taker` | float | 0.00040 | Taker fee rate |

---

## risk

```yaml
risk:
  base_risk_per_trade: 0.01   # 1% of portfolio
  max_leverage: 10            # Max leverage
  
  limits:                     # Hard limits
    max_drawdown_pct: 0.10    # 10%
    max_daily_loss_pct: 0.03  # 3%
    max_position_size_pct: 0.20
  
  soft_limits:                # Soft limits
    position_warning_pct: 0.15
    daily_loss_warning_pct: 0.02
    consecutive_losses: 3
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `base_risk_per_trade` | float | 0.01 | Base risk per trade |
| `max_leverage` | float | 10 | Maximum leverage |
| `limits.max_drawdown_pct` | float | 0.10 | Hard drawdown limit |
| `limits.max_daily_loss_pct` | float | 0.03 | Hard daily loss limit |
| `limits.max_position_size_pct` | float | 0.20 | Max position as % of portfolio |

---

## features

```yaml
features:
  return_window: 50            # Returns window (ticks)
  volatility_window: 100       # Volatility window (ticks)
  depth_window: 100           # Depth window (ticks)
  spread_window: 100           # Spread window (ticks)
  zscore_threshold: 3.0       # Z-score threshold
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `return_window` | int | 50 | Rolling window for returns |
| `volatility_window` | int | 100 | Rolling window for volatility |
| `depth_window` | int | 100 | Rolling window for depth |
| `spread_window` | int | 100 | Rolling window for spread |
| `zscore_threshold` | float | 3.0 | Z-score outlier threshold |

---

## strategy

```yaml
strategy:
  ofi_threshold: 0.7           # Block if |OFI| > threshold
  regime_threshold: 2.0         # Block if T > threshold
  signal_decay: 0.95           # Decay for consecutive signals
  min_confidence: 0.3           # Minimum confidence
  reversal_window: 5            # Reversal confirmation window
  reversal_required: true        # Require reversal confirmation
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ofi_threshold` | float | 0.7 | OFI filter threshold |
| `regime_threshold` | float | 2.0 | Regime T threshold |
| `signal_decay` | float | 0.95 | Signal decay factor |
| `min_confidence` | float | 0.3 | Minimum signal confidence |
| `reversal_window` | int | 5 | Reversal lookback window |
| `reversal_required` | bool | true | Require reversal |

---

## portfolio

```yaml
portfolio:
  initial_capital: 100000.0   # Starting capital
  currency: USDT              # Quote currency
  rebalance_threshold: 0.05   # Rebalance threshold
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `initial_capital` | float | 100000.0 | Starting capital |
| `currency` | string | USDT | Quote currency |
| `rebalance_threshold` | float | 0.05 | Rebalance trigger |

---

## learning

```yaml
learning:
  min_samples: 30             # Min trades before full weight
  update_rate: 0.1            # Learning rate
  max_change_per_update: 0.05 # Max parameter change
  cooldown_ticks: 10          # Update cooldown
  edge_threshold: 0.001      # Minimum edge to trade
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `min_samples` | int | 30 | Min trades for full weight |
| `update_rate` | float | 0.1 | Bayesian update rate |
| `max_change_per_update` | float | 0.05 | Max parameter drift |
| `cooldown_ticks` | int | 10 | Ticks between updates |
| `edge_threshold` | float | 0.001 | Minimum edge threshold |

---

## monitoring

```yaml
monitoring:
  collection_interval: 1.0      # Metrics interval (seconds)
  data_freshness_threshold_sec: 10
  
  alerts:
    slack_webhook: ""
    email_recipients: []
    rate_limit_per_minute: 10
  
  health_check_interval: 5.0
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `collection_interval` | float | 1.0 | Metrics collection interval |
| `data_freshness_threshold_sec` | int | 10 | Data freshness threshold |
| `alerts.slack_webhook` | string | "" | Slack webhook URL |
| `alerts.rate_limit_per_minute` | int | 10 | Alert rate limit |

---

## state

```yaml
state:
  checkpoint_interval_sec: 60  # Checkpoint interval
  event_log_enabled: true     # Enable event log
  event_retention_days: 90    # Event retention
  auto_recovery: true         # Auto recovery on restart
  validate_on_startup: true   # Validate state on startup
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `checkpoint_interval_sec` | int | 60 | State checkpoint interval |
| `event_log_enabled` | bool | true | Enable append-only log |
| `event_retention_days` | int | 90 | Days to retain events |
| `auto_recovery` | bool | true | Recover from storage |
| `validate_on_startup` | bool | true | Validate on startup |

---

## database

```yaml
database:
  host: localhost
  port: 5432
  name: trading_system
  user: trading_user
  pool_size: 10
  max_overflow: 20
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `host` | string | localhost | PostgreSQL host |
| `port` | int | 5432 | PostgreSQL port |
| `name` | string | trading_system | Database name |
| `user` | string | trading_user | Database user |
| `pool_size` | int | 10 | Connection pool size |
| `max_overflow` | int | 20 | Max pool overflow |

---

## redis

```yaml
redis:
  host: localhost
  port: 6379
  db: 0
  password: ""
  pool_size: 10
  socket_timeout: 5
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `host` | string | localhost | Redis host |
| `port` | int | 6379 | Redis port |
| `db` | int | 0 | Redis database |
| `password` | string | "" | Redis password |
| `pool_size` | int | 10 | Connection pool size |
| `socket_timeout` | int | 5 | Socket timeout (seconds) |

---

## Environment Variables

Override config.yaml with environment variables:

```bash
# System
export LVR_ENV=production
export LVR_LOG_LEVEL=DEBUG

# Execution
export LVR_EXECUTION_MODE=PAPER
export LVR_LIVE_CONFIRMED=false

# Database
export PG_HOST=production-db.example.com
export PG_PORT=5432
export PG_PASSWORD=secure_password

# Redis
export REDIS_HOST=production-redis.example.com
```

---

## Configuration Precedence

1. Environment variables (highest)
2. Command-line arguments
3. config.yaml
4. Default values (lowest)
