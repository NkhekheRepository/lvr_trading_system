# Monitoring API

Real-time monitoring, alerting, and protection systems.

## Metrics Collector

The `MetricsCollector` tracks execution quality and strategy performance.

### Initialization

```python
from monitoring.monitor import MetricsCollector

collector = MetricsCollector(window_size=100)
```

### Recording Metrics

```python
# Record fill
collector.record_fill(
    filled_qty=0.1,
    requested_qty=0.1
)

# Record partial fill
collector.record_fill(
    filled_qty=0.05,
    requested_qty=0.1
)

# Record rejection
collector.record_rejection()

# Record slippage
collector.record_slippage(
    actual_slippage=2.5,      # Actual slippage in quote currency
    expected_slippage=1.0     # Expected slippage
)

# Record edge
collector.record_edge(
    expected_edge=0.001,
    realized_edge=0.0008
)

# Record latency
collector.record_latency(150.0)  # 150ms
```

### Collecting Metrics

```python
snapshot = collector.collect()

print(f"Fill rate: {snapshot.fill_rate:.1%}")
print(f"Rejection rate: {snapshot.rejection_rate:.1%}")
print(f"Avg slippage error: {snapshot.slippage_error:.2f}")
print(f"Edge error: {snapshot.edge_error:.5f}")
print(f"P99 latency: {snapshot.latency_p99:.0f}ms")
```

### Metrics Snapshot

```python
@dataclass
class MetricsSnapshot:
    timestamp: int
    
    # Execution metrics
    total_orders: int
    fill_rate: float
    partial_fill_rate: float
    rejection_rate: float
    
    # Slippage metrics
    avg_slippage: float
    slippage_error: float  # actual - expected
    
    # Edge metrics
    avg_edge: float
    edge_error: float  # realized - expected
    
    # Latency metrics
    avg_latency: float
    latency_p50: float
    latency_p99: float
    
    # Rolling windows
    slippage_history: list
    latency_history: list
```

### Summary

```python
summary = collector.get_summary()

print(f"Total fills: {summary['total_fills']}")
print(f"Fill rate: {summary['fill_rate']:.2%}")
print(f"Avg latency: {summary['avg_latency']:.1f}ms")
```

## Alert Manager

The `AlertManager` dispatches alerts with rate limiting.

### Initialization

```python
from monitoring.alerts import AlertManager
from app.schemas import AlertSeverity

manager = AlertManager(
    rate_limit_per_minute=10,
    slack_webhook="https://hooks.slack.com/...",
    email_recipients=["trader@example.com"]
)
```

### Sending Alerts

```python
# Critical alert
manager.send_alert(
    severity=AlertSeverity.CRITICAL,
    category="risk",
    message="Drawdown exceeded 10%",
    source_module="risk_engine",
    details={"drawdown": 0.105, "threshold": 0.10},
    trace_id="trace_001"
)

# Warning alert
manager.send_alert(
    severity=AlertSeverity.WARNING,
    category="execution",
    message="High rejection rate detected",
    source_module="execution",
    details={"rejection_rate": 0.15}
)

# Info alert
manager.send_alert(
    severity=AlertSeverity.INFO,
    category="strategy",
    message="Strategy parameter updated",
    source_module="strategy"
)
```

### Alert Severity

```python
class AlertSeverity(Enum):
    INFO = 1      # Informational
    WARNING = 2    # Requires attention
    CRITICAL = 3  # Immediate action required
```

### Retrieving Alerts

```python
# Get recent critical alerts
critical = manager.get_recent_alerts(
    severity=AlertSeverity.CRITICAL,
    limit=10
)

# Get all recent alerts
recent = manager.get_recent_alerts(limit=50)

for alert in recent:
    print(f"[{alert.severity.name}] {alert.message}")
```

## Protection System

The `ProtectionSystem` implements multi-level protection based on metrics.

### Initialization

```python
from monitoring.protection import ProtectionSystem

protection = ProtectionSystem(alert_manager=manager)
```

### Evaluation

```python
from app.schemas import ProtectionLevel

# Get current metrics
metrics = collector.collect()

# Evaluate protection level
level = protection.evaluate(
    metrics=metrics,
    portfolio_drawdown=0.08,
    daily_loss_pct=0.025
)

print(f"Protection level: {level.name}")
```

### Applying Protection

```python
if level > ProtectionLevel.NONE:
    actions = protection.apply_protection(level)
    
    print(f"Should reduce size: {actions['should_reduce_size']}")
    print(f"Should restrict trading: {actions['should_restrict_trading']}")
    print(f"Should close all: {actions['should_close_all']}")
    print(f"Should halt: {actions['should_halt']}")
    
    if actions['should_halt']:
        # Emergency shutdown
        pass
```

### Protection Actions

| Level | Actions |
|-------|---------|
| NONE | None |
| REDUCE_SIZE | Reduce positions by 50% |
| RESTRICT_TRADING | Block new orders for 1 minute |
| CLOSE_ALL_HALT | Close all positions, halt system |

### Anomaly Detection

```python
anomalies = protection.check_anomalies(metrics)

for anomaly in anomalies:
    print(f"Anomaly detected: {anomaly}")
    manager.send_alert(
        severity=AlertSeverity.WARNING,
        category="anomaly",
        message=anomaly,
        source_module="protection"
    )
```

## Alert Schema

### Alert

```python
@dataclass
class Alert:
    alert_id: str
    timestamp: int
    severity: AlertSeverity
    category: str
    message: str
    source_module: str
    details: dict
    trace_id: Optional[str]
```

### Alert Categories

| Category | Description |
|----------|-------------|
| risk | Risk limit breaches |
| execution | Order execution issues |
| strategy | Strategy signal issues |
| system | System health issues |
| anomaly | Detected anomalies |
| protection | Protection level changes |

## Rate Limiting

The alert manager implements rate limiting per severity:

```python
manager = AlertManager(
    rate_limit_per_minute=10,
    severity_limits={
        AlertSeverity.CRITICAL: 100,  # More critical alerts allowed
        AlertSeverity.WARNING: 10,
        AlertSeverity.INFO: 5
    }
)
```

### Rate Limit Buckets

Alerts are grouped by severity and limited per minute:
- INFO: 5/minute
- WARNING: 10/minute  
- CRITICAL: 100/minute

## Dashboard Integration

Export metrics for dashboard consumption:

```python
# Get dashboard data
dashboard_data = collector.get_summary()

# Add alert summary
dashboard_data["alerts"] = {
    "critical": len(manager.get_recent_alerts(AlertSeverity.CRITICAL, 100)),
    "warning": len(manager.get_recent_alerts(AlertSeverity.WARNING, 100)),
    "info": len(manager.get_recent_alerts(AlertSeverity.INFO, 100))
}

# Add protection status
dashboard_data["protection"] = {
    "level": protection.current_level.name,
    "actions_today": protection.actions_today
}

print(dashboard_data)
```

## Integration Example

```python
async def trading_loop():
    collector = MetricsCollector()
    manager = AlertManager(rate_limit_per_minute=10)
    protection = ProtectionSystem(manager)
    
    while True:
        # Process orders
        result = await engine.submit_order(order)
        
        # Record metrics
        if result.success:
            collector.record_fill(result.filled_quantity, order.quantity)
        else:
            collector.record_rejection()
        
        # Check protection
        metrics = collector.collect()
        level = protection.evaluate(
            metrics,
            portfolio_drawdown=portfolio.current_drawdown,
            daily_loss_pct=portfolio.daily_pnl / portfolio.initial_capital
        )
        
        if level >= ProtectionLevel.CLOSE_ALL_HALT:
            await close_all_positions()
            manager.send_alert(
                AlertSeverity.CRITICAL,
                "protection",
                "System halted due to risk limits",
                "protection"
            )
            break
        
        await asyncio.sleep(1)
```
