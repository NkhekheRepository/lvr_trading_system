# Execution API

The Execution Abstraction Layer (EAL) provides a unified interface for order execution across all trading modes.

## Architecture

```
Strategy → OrderRequest → ExecutionEngine.submit_order()
                               ↓
              ┌─────────────┴─────────────┐
              ↓             ↓             ↓
            SIM        PAPER           LIVE
              └─────────────┬─────────────┘
                            ↓
                  ExecutionResult
                            ↓
              ┌─────────────┴─────────────┐
              ↓             ↓             ↓
          FillEvent    RejectEvent    Order Update
```

## Execution Modes

### SIM (Simulation)

Used for backtesting with simulated fills based on order book state.

```python
from execution.simulator import SimulatedExecutionEngine

engine = SimulatedExecutionEngine(
    slippage_alpha=0.5,    # Slippage magnitude
    latency_ms=100,       # Simulated latency
    maker_fee=0.0002,      # 0.02% maker fee
    taker_fee=0.0004,      # 0.04% taker fee
    zero_slippage=False    # Disable slippage for comparison
)
```

### PAPER (Paper Trading)

Paper trading against live market data with simulated fills.

```python
from execution.paper_engine import PaperExecutionEngine

engine = PaperExecutionEngine(
    slippage_alpha=0.5,
    slippage_multiplier=1.0,
    latency_base_ms=50,
    latency_jitter_ms=50,
    maker_fee=0.0002,
    taker_fee=0.0004
)
```

### LIVE (Live Trading)

Real execution against exchange with actual fills.

```python
from execution.vnpy_adapter import VnpyAdapter

adapter = VnpyAdapter(
    api_key="your_api_key",
    api_secret="your_api_secret",
    testnet=True  # Use testnet for testing
)
```

## Fill Model

Estimates fill probability based on queue position and market flow.

```python
from execution.fill_model import FillModel

fill_model = FillModel(base_flow_rate=0.5)

# Compute fill probability
prob = fill_model.compute_fill_probability(
    queue_ahead=10,        # Orders ahead in queue
    order_size=0.1,        # Your order size
    market_depth=100,      # Available liquidity
    flow_rate=None         # Use default
)
# Returns: 0.045 (4.5% probability)
```

### Fill Probability Formula

```
fill_probability = flow_rate / (queue_ahead + 1)
```

Where:
- `flow_rate`: Estimated order flow rate (trades/second)
- `queue_ahead`: Number of orders ahead in queue

## Cost Model

Calculates all execution costs including fees, slippage, and latency impact.

```python
from execution.cost_model import CostModel

cost_model = CostModel(
    maker_fee=0.0002,
    taker_fee=0.0004,
    slippage_alpha=0.5,
    latency_coefficient=0.000001
)

costs = cost_model.calculate_total_cost(
    quantity=0.1,
    price=50000.0,
    side="buy",
    spread=5.0,
    market_depth=100.0,
    latency_ms=100
)
```

### Cost Breakdown

| Cost Component | Description |
|---------------|--------------|
| `spread_cost` | Half of spread |
| `slippage_cost` | Volume-proportional slippage |
| `fee_cost` | Maker or taker fee |
| `latency_cost` | Price movement during latency |
| `total_cost` | Sum of all costs |
| `total_cost_bps` | Total cost in basis points |

## Slippage Model

The slippage model estimates price impact based on order size relative to available liquidity:

```
slippage = slippage_alpha × (quantity / depth) × (spread / 2 + 1)
```

Parameters:
- `slippage_alpha`: Controls slippage magnitude (0 = none, higher = more)
- `quantity`: Order quantity
- `depth`: Sum of top 5 order book levels
- `spread`: Bid-ask spread

## Callbacks

Register callbacks for order lifecycle events:

```python
engine = SimulatedExecutionEngine()

def on_fill(fill: FillEvent):
    print(f"Filled: {fill.quantity} @ {fill.price}")

def on_reject(reject: RejectEvent):
    print(f"Rejected: {reject.reason}")

def on_update(order: Order):
    print(f"Order {order.order_id}: {order.status}")

engine.on_fill(on_fill)
engine.on_reject(on_reject)
engine.on_order_update(on_update)
```

## Order Lifecycle

```
PENDING → SUBMITTED → FILLED/PARTIAL → CANCELLED/REJECTED
              ↓
         CANCELLED
```

### Status Transitions

| Status | Description |
|--------|-------------|
| `PENDING` | Order created, not yet submitted |
| `SUBMITTED` | Sent to exchange |
| `PARTIAL` | Partially filled |
| `FILLED` | Fully filled |
| `CANCELLED` | User cancelled |
| `REJECTED` | Rejected by exchange or risk |
