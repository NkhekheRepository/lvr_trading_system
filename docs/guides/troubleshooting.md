# Troubleshooting Guide

Solutions for common issues with the LVR Trading System.

---

## Installation Issues

### Import Errors

**Error:**
```
ModuleNotFoundError: No module named 'xxx'
```

**Solution:**
```bash
pip install -r requirements.txt --force-reinstall
```

---

### PostgreSQL Connection Failed

**Error:**
```
psycopg2.OperationalError: could not connect to server
```

**Diagnosis:**
```bash
# Check if PostgreSQL is running
sudo systemctl status postgresql

# Check listening ports
sudo ss -tlnp | grep 5432
```

**Solution:**
```bash
# Start PostgreSQL
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Check pg_hba.conf
sudo nano /etc/postgresql/*/main/pg_hba.conf
# Ensure: host all all 127.0.0.1/32 md5
```

---

### Redis Connection Failed

**Error:**
```
redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379
```

**Solution:**
```bash
# Start Redis
sudo systemctl start redis-server
sudo systemctl enable redis-server

# Test connection
redis-cli ping
# Should return: PONG
```

---

## Runtime Issues

### Permission Denied on Logs

**Error:**
```
PermissionError: [Errno 13] Permission denied: 'logs/trading.log'
```

**Solution:**
```bash
# Create logs directory
mkdir -p logs
chmod 755 logs
chown $USER:$USER logs
```

---

### Config File Not Found

**Error:**
```
FileNotFoundError: [Errno 2] No such file: 'config/config.yaml'
```

**Solution:**
```bash
# Run from project root
cd /path/to/lvr_trading_system
python app/main.py config/config.yaml

# Or use absolute path
python app/main.py /full/path/to/config/config.yaml
```

---

### Mode Not Authorized

**Error:**
```
PermissionError: LIVE mode not authorized
```

**Solution:**
```bash
# Set environment variable
export LVR_LIVE_CONFIRMED=true
python app/main.py config/config.yaml
```

**Warning:** Only do this when ready for live trading!

---

## Data Issues

### No Data Loaded

**Symptom:** Features all zeros, no signals generated

**Diagnosis:**
```python
# Check data loader
loader = DataLoader()
ticks = list(loader.load_csv('data.csv', 'BTCUSDT'))
print(f"Loaded {len(ticks)} ticks")
```

**Solution:**
- Verify data file exists and is readable
- Check CSV format matches expected schema
- Ensure timestamps are in milliseconds

---

### Missing Timestamps

**Symptom:** Data gaps, inconsistent replay

**Solution:**
```python
# Use DataLoader with gap handling
loader = DataLoader(max_gap_ticks=10)

# Validate sequence
ticks = list(loader.load_csv('data.csv', 'BTCUSDT'))
assert loader.validate_sequence(ticks), "Data not sorted!"
```

---

## Execution Issues

### Orders Not Filling

**Symptom:** Orders submitted but never filled

**Diagnosis:**
```python
# Check order book
book = OrderBookSnapshot(...)
print(f"Best bid: {book.best_bid}")
print(f"Best ask: {book.best_ask}")

# Check simulator state
executor = SimulatedExecutionEngine()
print(executor._connected)
```

**Solution:**
- Ensure order book is set: `executor.set_order_book(book)`
- Check quantity is within available depth
- Verify latency settings aren't too high

---

### Slippage Too High

**Symptom:** Execution costs much higher than expected

**Diagnosis:**
```python
# Check slippage alpha
executor = SimulatedExecutionEngine(slippage_alpha=0.5)

# Calculate expected slippage
slippage = 0.5 * (order_size / market_depth)
```

**Solution:**
- Reduce `slippage_alpha` in config
- Increase `market_depth` by using deeper levels
- Switch to `zero_slippage=True` for backtest baseline

---

## Risk Issues

### Position Rejected

**Symptom:** Orders blocked by risk engine

**Diagnosis:**
```python
# Check risk state
risk_state = RiskState(
    current_leverage=portfolio.portfolio_leverage,
    current_drawdown=portfolio.current_drawdown
)
result = risk_engine.check_order(order, signal, portfolio, risk_state)
print(f"Approved: {result.approved}")
print(f"Reason: {result.rejection_reason}")
```

**Common Reasons:**
| Reason | Cause | Solution |
|--------|-------|----------|
| Leverage exceeded | Too many positions | Reduce positions |
| Drawdown exceeded | Losses too large | Wait for recovery |
| Position too large | Single position > 20% | Reduce size |

---

### System Halted

**Symptom:** No new orders after breach

**Diagnosis:**
```python
# Check protection level
print(f"Protection level: {risk_engine.protection_level}")
print(f"Is halted: {risk_engine.is_halted}")
```

**Solution:**
```python
# Manual unhalt (requires restart)
risk_engine.unhalt()

# Or restart the service
sudo systemctl restart lvr-trading
```

---

## Performance Issues

### High Memory Usage

**Symptom:** System running out of memory

**Diagnosis:**
```python
# Check rolling window sizes
engine = FeatureEngine(
    return_window=50,
    volatility_window=100,
    depth_window=100,
    spread_window=100
)
```

**Solution:**
- Reduce rolling window sizes
- Process data in batches
- Clear state periodically: `engine.reset()`

---

### Slow Backtests

**Symptom:** Backtest taking too long

**Solution:**
```python
# Increase speed multiplier
replay = ReplayEngine(ticks, speed_multiplier=10.0)

# Or use sync version
sync_replay = SyncReplayEngine(ticks)
sync_replay.run()
```

---

## State Issues

### State Not Persisting

**Symptom:** Positions lost on restart

**Diagnosis:**
```python
# Check state store
store = StateStore(checkpoint_interval=60)
await store.connect()

# Check connection
print(f"Redis connected: {store._redis_client is not None}")
```

**Solution:**
```bash
# Ensure Redis is running
sudo systemctl status redis-server

# Force checkpoint
await store.checkpoint(force=True)
```

---

### Recovery Failed

**Symptom:** System starting from scratch

**Diagnosis:**
```python
# Check recovery
recovery = await store.recover()
print(f"Recovered: {recovery.get('recovered')}")
print(f"Positions: {recovery.get('positions')}")
```

**Solution:**
```bash
# Check PostgreSQL
psql -U trading_user -d trading_system -c "SELECT * FROM positions;"

# Check Redis
redis-cli KEYS "position:*"
```

---

## Monitoring Issues

### No Alerts Received

**Symptom:** Expected alerts not appearing

**Diagnosis:**
```python
# Check alert manager
manager = AlertManager(rate_limit_per_minute=10)
manager.send_alert(AlertSeverity.WARNING, "test", "message", "source")
```

**Solution:**
- Check rate limit hasn't been exceeded
- Verify webhook URL is correct
- Check network connectivity

---

### Metrics Stale

**Symptom:** Metrics not updating

**Diagnosis:**
```python
# Check collection
metrics = MetricsCollector()
metrics.record_fill(1.0, 1.0)
snapshot = metrics.collect()
print(f"Timestamp: {snapshot.timestamp}")
```

**Solution:**
- Check tick data is flowing
- Verify timestamps are updating
- Increase collection interval

---

## Database Issues

### Lock Timeout

**Error:**
```
psycopg2.errors.LockNotAvailable: could not obtain lock
```

**Solution:**
```sql
-- Check blocking queries
SELECT * FROM pg_locks WHERE NOT granted;

-- Kill blocking query
SELECT pg_terminate_backend(pid);
```

---

### Connection Pool Exhausted

**Error:**
```
psycopg2.pool.ThreadedConnectionPool: exhausted
```

**Solution:**
```yaml
# Increase pool size in config.yaml
database:
  pool_size: 20
  max_overflow: 40
```

---

## Getting Help

### Enable Debug Logging

```bash
export LVR_LOG_LEVEL=DEBUG
python app/main.py config/config.yaml
```

### Check System Status

```bash
# PostgreSQL
psql -U trading_user -d trading_system -c "SELECT count(*) FROM positions;"

# Redis
redis-cli INFO stats | grep connected

# System service
sudo systemctl status lvr-trading
```

### Collect Diagnostics

```bash
# System info
uname -a
python3 --version
pip list | grep -E "numpy|pandas|psycopg2|redis"

# Logs
sudo journalctl -u lvr-trading --since "1 hour ago" > diagnostics.log
```
