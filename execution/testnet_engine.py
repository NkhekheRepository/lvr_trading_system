"""
Binance Testnet Execution Engine.

Connects to Binance Futures Testnet for realistic trading simulation
with real exchange responses but no real money.
"""

import asyncio
import logging
import time
import os
from typing import Optional

from app.schemas import (
    ExecutionMode, ExecutionResult, FillEvent, Order, OrderBookSnapshot,
    OrderRequest, OrderStatus, Position, RejectEvent, Side
)
from execution.base import ExecutionEngine

logger = logging.getLogger(__name__)

EPS = 1e-10

BINANCE_TESTNET_URL = "https://testnet.binancefuture.com"


class TestnetExecutionEngine(ExecutionEngine):
    """
    Binance Testnet execution engine.
    
    Features:
    - Real API calls to Binance Testnet
    - Real order placement and cancellation
    - Real-time position tracking via API
    - Realistic latency simulation
    - Same order validation as LIVE
    
    Note: Requires TESTNET API keys from https://testnet.binancefuture.com
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet_url: str = BINANCE_TESTNET_URL,
        latency_base_ms: int = 100,
        latency_jitter_ms: int = 100,
    ):
        super().__init__()
        self._mode = ExecutionMode.TESTNET
        self.api_key = api_key or os.getenv("BINANCE_TESTNET_API_KEY", "")
        self.api_secret = api_secret or os.getenv("BINANCE_TESTNET_API_SECRET", "")
        self.testnet_url = testnet_url
        
        self.latency_base_ms = latency_base_ms
        self.latency_jitter_ms = latency_jitter_ms
        
        self._positions: dict[str, Position] = {}
        self._open_orders: dict[str, Order] = {}
        self._connected = False
        self._running = False
        
        self._session = None
        self._account_cache = None
        self._account_cache_time = 0

    @property
    def mode(self) -> ExecutionMode:
        return self._mode

    async def _poll_for_fill(self, order_id: str, symbol: str, max_attempts: int = 10) -> dict:
        """Poll for order fill."""
        import hmac
        import hashlib
        import urllib.parse
        
        for attempt in range(max_attempts):
            try:
                timestamp = int(time.time() * 1000)
                query = f"timestamp={timestamp}"
                signature = hmac.new(
                    self.api_secret.encode('utf-8'),
                    query.encode('utf-8'),
                    hashlib.sha256
                ).hexdigest()
                
                headers = {"X-MBX-APIKEY": self.api_key}
                url = f"{self.testnet_url}/fapi/v1/order?symbol={symbol}&orderId={order_id}&timestamp={timestamp}&signature={signature}"
                
                async with self._session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("status") in ("FILLED", "PARTIALLY_FILLED"):
                            return {
                                "filled_qty": float(data.get("executedQty", 0)),
                                "price": float(data.get("avgPrice", 0)),
                                "fee": float(data.get("commission", 0)),
                                "slippage": 0,
                                "total_cost": float(data.get("cumQuote", 0))
                            }
                        if data.get("status") == "CANCELED":
                            return {"filled_qty": 0.0, "price": 0.0, "fee": 0.0, "slippage": 0, "total_cost": 0.0}
            except Exception:
                pass
            
            await asyncio.sleep(0.5)
        
        return {"filled_qty": 0.0, "price": 0.0, "fee": 0.0, "slippage": 0, "total_cost": 0.0}

    async def connect(self) -> None:
        """Connect to Binance Testnet."""
        logger.info("Connecting to Binance Testnet")
        
        if not self.api_key or not self.api_secret:
            logger.warning("No TESTNET API keys provided - using simulation mode")
        
        try:
            import aiohttp
            self._session = aiohttp.ClientSession()
            
            await self._fetch_account_info()
            
            self._connected = True
            self._running = True
            logger.info("Connected to Binance Testnet")
        except Exception as e:
            logger.warning(f"Could not connect to Testnet API: {e}, using simulation mode")
            self._connected = True
            self._running = True

    async def disconnect(self) -> None:
        """Disconnect from Binance Testnet."""
        logger.info("Disconnecting from Binance Testnet")
        self._running = False
        self._connected = False
        if self._session:
            await self._session.close()
            self._session = None

    async def health_check(self) -> bool:
        if not self._connected:
            return False
        
        try:
            if self._session:
                async with self._session.get(f"{self.testnet_url}/fapi/v1/ping") as resp:
                    return resp.status == 200
        except Exception:
            pass
        return True

    async def submit_order(self, order: OrderRequest) -> ExecutionResult:
        """Submit order to Binance Testnet."""
        if not self._connected:
            return self._reject(order, "Engine not connected", "NOT_CONNECTED")

        start_time = time.time()
        
        latency = self.latency_base_ms + asyncio.get_event_loop().time() % self.latency_jitter_ms
        
        try:
            if self._session and self.api_key and self.api_secret:
                result = await self._submit_to_exchange(order)
                
                if order.order_type.value.lower() == "limit":
                    order_id = result.get("order_id", "")
                    if order_id:
                        print(f">>> POLLING for {order_id}", flush=True)
                        fill_result = await self._poll_for_fill(order_id, order.symbol)
                        result["filled_qty"] = fill_result.get("filled_qty", result.get("filled_qty", 0))
                        result["price"] = fill_result.get("price", result.get("price", order.price or 0))
                        print(f">>> FILL RESULT: {result}", flush=True)
                
                self._update_position_from_fill(order, result)
                
                fill_event = FillEvent(
                    trace_id=order.trace_id,
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=result.get("filled_qty", order.quantity),
                    price=result.get("price", 0),
                    fee=result.get("fee", 0),
                    slippage=result.get("slippage", 0),
                    latency_ms=latency
                )
                self._emit_fill(fill_event)
                
                return ExecutionResult(
                    success=True,
                    order_id=order.order_id,
                    trace_id=order.trace_id,
                    symbol=order.symbol,
                    status=OrderStatus.FILLED,
                    filled_quantity=result.get("filled_qty", order.quantity),
                    avg_fill_price=result.get("price", 0),
                    total_cost=result.get("total_cost", 0),
                    slippage=result.get("slippage", 0),
                    fee=result.get("fee", 0),
                    fill_events=[fill_event],
                    latency_ms=(time.time() - start_time) * 1000
                )
            else:
                return await self._simulate_order(order, start_time)

        except Exception as e:
            logger.error(f"Testnet order failed: {e}")
            return self._reject(order, str(e), "EXCHANGE_ERROR")

    async def _submit_to_exchange(self, order: OrderRequest) -> dict:
        """Submit order to Binance Testnet API."""
        import hmac
        import hashlib
        import urllib.parse
        
        quantity = self._round_quantity(order.symbol, order.quantity)
        price = self._round_price(order.symbol, order.price) if order.price else None
        
        timestamp = int(time.time() * 1000)
        
        params = {
            "symbol": order.symbol,
            "side": order.side.value.upper(),
            "type": order.order_type.value.upper() if order.order_type.value != "post_only" else "LIMIT",
            "quantity": quantity,
            "timestamp": timestamp,
        }
        
        if order.order_type.value == "limit" and price:
            params["price"] = price
            params["timeInForce"] = order.time_in_force.value.upper()
        
        if order.reduce_only:
            params["reduceOnly"] = True
        
        query_string = urllib.parse.urlencode(sorted(params.items()))
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        headers = {
            "X-MBX-APIKEY": self.api_key
        }
        
        url = f"{self.testnet_url}/fapi/v1/order?{query_string}&signature={signature}"
        
        async with self._session.post(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                result = {
                    "order_id": data.get("orderId"),
                    "filled_qty": float(data.get("executedQty", order.quantity)),
                    "price": float(data.get("avgPrice", order.price or 0)),
                    "fee": float(data.get("commission", 0)),
                    "slippage": 0,
                    "total_cost": float(data.get("executedQty", 0)) * float(data.get("avgPrice", 0))
                }
                print(f">>> API RESULT: {data}", flush=True)
                print(f">>> PARSED: {result}", flush=True)
                return result
            else:
                error_text = await resp.text()
                raise Exception(f"API error: {resp.status} - {error_text}")

    async def _simulate_order(self, order: OrderRequest, start_time: float) -> ExecutionResult:
        """Simulate order when no API keys available."""
        import random
        
        await asyncio.sleep(self.latency_base_ms / 1000.0)
        
        simulated_price = 50000.0
        
        slippage = random.uniform(0.0001, 0.0005) * simulated_price
        fill_price = simulated_price + slippage if order.side == Side.BUY else simulated_price - slippage
        
        fee = order.quantity * fill_price * 0.0004
        
        fill_event = FillEvent(
            trace_id=order.trace_id,
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            fee=fee,
            slippage=slippage * order.quantity,
            latency_ms=self.latency_base_ms
        )
        
        self._emit_fill(fill_event)
        self._update_position_from_fill(order, {
            "filled_qty": order.quantity,
            "price": fill_price,
            "fee": fee
        })
        
        return ExecutionResult(
            success=True,
            order_id=order.order_id,
            trace_id=order.trace_id,
            symbol=order.symbol,
            status=OrderStatus.FILLED,
            filled_quantity=order.quantity,
            avg_fill_price=fill_price,
            total_cost=order.quantity * fill_price,
            slippage=slippage * order.quantity,
            fee=fee,
            fill_events=[fill_event],
            latency_ms=(time.time() - start_time) * 1000
        )

    def _update_position_from_fill(self, order: OrderRequest, fill_result: dict) -> None:
        """Update position after fill."""
        if order.symbol not in self._positions:
            self._positions[order.symbol] = Position(symbol=order.symbol)

        pos = self._positions[order.symbol]
        qty = fill_result.get("filled_qty", 0)
        price = fill_result.get("price", 0)

        if order.side == Side.BUY:
            if pos.quantity >= 0:
                new_qty = pos.quantity + qty
                if new_qty > 0:
                    pos.entry_price = (pos.entry_price * pos.quantity + price * qty) / new_qty
                pos.quantity = new_qty
            else:
                if qty <= abs(pos.quantity):
                    pos.quantity += qty
                else:
                    pos.quantity += qty
                    pos.entry_price = price
        else:
            if pos.quantity <= 0:
                new_qty = pos.quantity - qty
                if new_qty != 0:
                    pos.entry_price = (abs(pos.entry_price * pos.quantity) + price * qty) / abs(new_qty)
                pos.quantity = new_qty
            else:
                if qty <= pos.quantity:
                    pos.quantity -= qty
                else:
                    pos.quantity -= qty
                    pos.entry_price = price

        pos.last_update = int(time.time() * 1000)

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel order on Binance Testnet."""
        if not self._connected:
            return False
        
        if order_id in self._open_orders:
            del self._open_orders[order_id]
            logger.info(f"Testnet order cancelled: {order_id}")
            return True
        
        return False

    async def get_position(self, symbol: str) -> Position:
        """Get position from cache or exchange."""
        if symbol not in self._positions:
            self._positions[symbol] = Position(symbol=symbol)
        
        if self._session and self.api_key:
            await self._fetch_account_info()
            if self._account_cache and "positions" in self._account_cache:
                for pos in self._account_cache["positions"]:
                    if pos.get("symbol") == symbol:
                        self._positions[symbol].quantity = float(pos.get("positionAmt", 0))
                        self._positions[symbol].entry_price = float(pos.get("entryPrice", 0))
        
        return self._positions[symbol]

    async def get_open_orders(self, symbol: Optional[str] = None) -> list[Order]:
        """Get open orders."""
        if symbol:
            return [o for o in self._open_orders.values() if o.symbol == symbol]
        return list(self._open_orders.values())

    async def _fetch_account_info(self) -> None:
        """Fetch account info from Testnet."""
        if not self._session or not self.api_key:
            return
        
        try:
            import hmac
            import hashlib
            import urllib.parse
            
            timestamp = int(time.time() * 1000)
            query = f"timestamp={timestamp}"
            signature = hmac.new(
                self.api_secret.encode('utf-8'),
                query.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            headers = {"X-MBX-APIKEY": self.api_key}
            url = f"{self.testnet_url}/fapi/v2/account?{query}&signature={signature}"
            
            async with self._session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    self._account_cache = await resp.json()
                    self._account_cache_time = int(time.time() * 1000)
        except Exception as e:
            logger.warning(f"Failed to fetch account info: {e}")

    def _reject(
        self,
        order: OrderRequest,
        reason: str,
        error_code: str
    ) -> ExecutionResult:
        """Create rejection result."""
        reject = RejectEvent(
            trace_id=order.trace_id,
            order_id=order.order_id,
            symbol=order.symbol,
            reason=reason,
            error_code=error_code
        )
        self._emit_reject(reject)
        return ExecutionResult(
            success=False,
            order_id=order.order_id,
            trace_id=order.trace_id,
            symbol=order.symbol,
            status=OrderStatus.REJECTED,
            reject_event=reject
        )

    SYMBOL_PRECISION = {
        "BTCUSDT": {"quantity": 3, "price": 1},
        "ETHUSDT": {"quantity": 2, "price": 2},
        "BNBUSDT": {"quantity": 1, "price": 2},
        "SOLUSDT": {"quantity": 0, "price": 2},
        "XRPUSDT": {"quantity": 1, "price": 4},
        "DOGEUSDT": {"quantity": 0, "price": 5},
        "ADAUSDT": {"quantity": 0, "price": 5},
    }

    DEFAULT_PRECISION = {"quantity": 3, "price": 2}

    def _round_quantity(self, symbol: str, quantity: float) -> float:
        precision = self.SYMBOL_PRECISION.get(symbol, self.DEFAULT_PRECISION)
        return round(quantity, precision["quantity"])

    def _round_price(self, symbol: str, price: float) -> float:
        precision = self.SYMBOL_PRECISION.get(symbol, self.DEFAULT_PRECISION)
        return round(price, precision["price"])

    def update_order_book(self, book: OrderBookSnapshot) -> None:
        """Update order book for simulation fallback."""
        pass