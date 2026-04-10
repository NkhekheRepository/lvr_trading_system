"""
Multi-Exchange WebSocket Client - Streams data from multiple exchanges with failover.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable
from datetime import datetime
from collections import deque
from enum import Enum
import time

import websockets
import aiohttp

logger = logging.getLogger(__name__)


class Exchange(Enum):
    BINANCE = "binance"
    BINANCE_US = "binance_us"
    BYBIT = "bybit"
    OKX = "okx"
    UNISWAP = "uniswap"


@dataclass
class ExchangeConfig:
    exchange: Exchange
    ws_url: str
    rest_url: str
    enabled: bool = True
    priority: int = 1
    timeout: float = 10.0
    max_reconnect_attempts: int = 5
    heartbeat_interval: float = 30.0
    is_futures: bool = False


@dataclass
class TickerData:
    symbol: str
    exchange: Exchange
    bid: float
    ask: float
    last: float
    volume_24h: float
    timestamp: datetime
    latency_ms: float = 0.0


@dataclass 
class OrderBookData:
    symbol: str
    exchange: Exchange
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]
    timestamp: datetime
    depth: int = 20


@dataclass
class TradeData:
    symbol: str
    exchange: Exchange
    price: float
    quantity: float
    side: str
    trade_id: str
    timestamp: datetime


@dataclass
class WebSocketState:
    connected: bool = False
    last_message: Optional[datetime] = None
    reconnect_count: int = 0
    error_count: int = 0
    messages_per_second: float = 0.0


class MultiExchangeWebSocket:
    """
    WebSocket client for streaming data from multiple exchanges.
    
    Features:
    - Automatic reconnection with exponential backoff
    - Exchange failover based on priority
    - Heartbeat monitoring
    - Data quality tracking
    - Graceful shutdown
    """
    
    def __init__(
        self,
        on_ticker: Optional[Callable[[TickerData], Awaitable[None]]] = None,
        on_orderbook: Optional[Callable[[OrderBookData], Awaitable[None]]] = None,
        on_trade: Optional[Callable[[TradeData], Awaitable[None]]] = None,
        on_heartbeat: Optional[Callable[[Exchange, datetime], Awaitable[None]]] = None,
    ):
        self.on_ticker = on_ticker
        self.on_orderbook = on_orderbook
        self.on_trade = on_trade
        self.on_heartbeat = on_heartbeat
        
        self.exchanges: dict[Exchange, ExchangeConfig] = {}
        self.states: dict[Exchange, WebSocketState] = {}
        self.connections: dict[Exchange, websockets.WebSocketClientProtocol] = {}
        
        self.subscriptions: dict[Exchange, set[str]] = {e: set() for e in Exchange}
        
        self.message_buffers: dict[Exchange, deque] = {
            e: deque(maxlen=1000) for e in Exchange
        }
        
        self.running = False
        self._tasks: list[asyncio.Task] = []
        self._heartbeat_task: Optional[asyncio.Task] = None
        
        self._last_mps_update = time.time()
        self._message_counts: dict[Exchange, int] = {e: 0 for e in Exchange}
        
    def add_exchange(self, config: ExchangeConfig) -> None:
        """Add exchange configuration."""
        self.exchanges[config.exchange] = config
        self.states[config.exchange] = WebSocketState()
        
    async def connect(self, exchange: Exchange, symbols: list[str]) -> bool:
        """Connect to exchange WebSocket and subscribe to symbols."""
        
        if exchange not in self.exchanges:
            return False
            
        config = self.exchanges[exchange]
        
        if not config.enabled:
            return False
            
        config = self.exchanges[exchange]
        
        if not config.enabled:
            logger.warning(f"Exchange {exchange} is disabled")
            return False
            
        try:
            ws_url = self._build_ws_url(exchange, symbols)
            
            async with websockets.connect(
                ws_url,
                ping_interval=config.heartbeat_interval,
                ping_timeout=config.timeout,
                close_timeout=5.0,
            ) as ws:
                self.connections[exchange] = ws
                self.states[exchange].connected = True
                self.states[exchange].reconnect_count = 0
                
                self.subscriptions[exchange] = set(symbols)
                
                logger.info(f"Connected to {exchange.value} WebSocket")
                
                await self._send_subscribe(exchange, ws, symbols)
                
                async for message in ws:
                    if not self.running:
                        break
                        
                    await self._handle_message(exchange, message)
                    
        except Exception as e:
            logger.error(f"WebSocket error for {exchange.value}: {e}")
            self.states[exchange].connected = False
            self.states[exchange].error_count += 1
            
            return await self._attempt_reconnect(exchange, symbols)
            
        return False
        
    async def _attempt_reconnect(
        self, 
        exchange: Exchange, 
        symbols: list[str]
    ) -> bool:
        """Attempt to reconnect with exponential backoff."""
        config = self.exchanges[exchange]
        state = self.states[exchange]
        
        if state.reconnect_count >= config.max_reconnect_attempts:
            logger.error(f"Max reconnect attempts reached for {exchange.value}")
            return False
            
        state.reconnect_count += 1
        delay = min(2 ** state.reconnect_count, 60)
        
        logger.info(f"Reconnecting to {exchange.value} in {delay}s (attempt {state.reconnect_count})")
        
        await asyncio.sleep(delay)
        
        return await self.connect(exchange, symbols)
        
    def _build_ws_url(self, exchange: Exchange, symbols: list[str]) -> str:
        """Build WebSocket URL for exchange."""
        config = self.exchanges[exchange]
        
        if exchange == Exchange.BINANCE:
            if config.is_futures:
                streams = [f"{s.lower()}@ticker" for s in symbols]
            else:
                streams = [f"{s.lower()}@ticker" for s in symbols]
            return f"{config.ws_url}/stream?streams={'/'.join(streams)}"
            
        elif exchange == Exchange.BINANCE_US:
            symbols_param = '/'.join([s.lower().replace('/', '') for s in symbols])
            return f"wss://stream.binance.us:9443/stream?streams={'/'.join([f'{s.lower()}@ticker/{s.lower()}@depth/{s.lower()}@trade' for s in symbols])}"
            
        elif exchange == Exchange.BYBIT:
            return "wss://stream.bybit.com/v5/public/linear"
            
        elif exchange == Exchange.OKX:
            return "wss://ws.okx.com:8443/ws/v5/public"
            
        else:
            return config.ws_url
            
    async def _send_subscribe(
        self, 
        exchange: Exchange, 
        ws: websockets.WebSocketClientProtocol,
        symbols: list[str]
    ) -> None:
        """Send subscription message to exchange."""
        if exchange == Exchange.BINANCE or exchange == Exchange.BINANCE_US:
            pass
            
        elif exchange == Exchange.BYBIT:
            for symbol in symbols:
                await ws.send(json.dumps({
                    "op": "subscribe",
                    "args": [
                        f"tickers.{symbol}",
                        f"orderbook.50.{symbol}",
                        f"publicTrade.{symbol}"
                    ]
                }))
                
        elif exchange == Exchange.OKX:
            await ws.send(json.dumps({
                "op": "subscribe",
                "args": [
                    {"channel": "tickers", "instId": symbol} for symbol in symbols
                ] + [
                    {"channel": "books5", "instId": symbol} for symbol in symbols
                ]
            }))
            
    async def _handle_message(self, exchange: Exchange, message: str) -> None:
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            self.states[exchange].last_message = datetime.now()
            self._message_counts[exchange] += 1
            
            if 'data' in data:
                data = data['data']
                
            if isinstance(data, list):
                for item in data:
                    await self._process_message_item(exchange, item)
            else:
                await self._process_message_item(exchange, data)
                
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON from {exchange.value}")
        except Exception as e:
            logger.error(f"Error handling message from {exchange.value}: {e}")
            
    async def _process_message_item(self, exchange: Exchange, data: dict) -> None:
        """Process a single message item."""
        event_type = data.get('e', '')
        
        if 'data' in data:
            data = data['data']
            event_type = data.get('e', '')
        
        if event_type == '24hrTicker' or data.get('s'):
            ticker = self._parse_ticker(exchange, data)
            if ticker and self.on_ticker:
                await self.on_ticker(ticker)
                
        elif event_type == 'depthUpdate' or 'bids' in data:
            orderbook = self._parse_orderbook(exchange, data)
            if orderbook and self.on_orderbook:
                await self.on_orderbook(orderbook)
                
        elif event_type == 'trade' or event_type == 'aggTrade':
            trade = self._parse_trade(exchange, data)
            if trade and self.on_trade:
                await self.on_trade(trade)
                
    def _parse_ticker(self, exchange: Exchange, data: dict) -> Optional[TickerData]:
        """Parse ticker data from message."""
        try:
            symbol = data.get('s', '')
            last_price = data.get('c', 0) or data.get('last', 0) or data.get('L', 0)
            bid_price = data.get('b', 0)
            ask_price = data.get('a', 0)
            volume = data.get('v', 0) or data.get('q', 0)
            
            return TickerData(
                symbol=symbol,
                exchange=exchange,
                bid=float(bid_price) if bid_price else 0.0,
                ask=float(ask_price) if ask_price else 0.0,
                last=float(last_price) if last_price else 0.0,
                volume_24h=float(volume) if volume else 0.0,
                timestamp=datetime.fromtimestamp(
                    int(data.get('E', data.get('ts', 0))) / 1000
                ) if data.get('E') or data.get('ts') else datetime.now(),
            )
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse ticker: {e}")
            return None
            
    def _parse_orderbook(self, exchange: Exchange, data: dict) -> Optional[OrderBookData]:
        """Parse orderbook data from message."""
        try:
            bids_raw = data.get('b', data.get('bids', []))
            asks_raw = data.get('a', data.get('asks', []))
            
            if isinstance(bids_raw, list) and len(bids_raw) > 0:
                if isinstance(bids_raw[0], list):
                    bids = [(float(b[0]), float(b[1])) for b in bids_raw]
                else:
                    bids = [(float(b), 0.0) for b in bids_raw]
            else:
                bids = []
                
            if isinstance(asks_raw, list) and len(asks_raw) > 0:
                if isinstance(asks_raw[0], list):
                    asks = [(float(a[0]), float(a[1])) for a in asks_raw]
                else:
                    asks = [(float(a), 0.0) for a in asks_raw]
            else:
                asks = []
                
            return OrderBookData(
                symbol=data.get('s', ''),
                exchange=exchange,
                bids=bids,
                asks=asks,
                timestamp=datetime.now(),
            )
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse orderbook: {e}")
            return None
            
    def _parse_trade(self, exchange: Exchange, data: dict) -> Optional[TradeData]:
        """Parse trade data from message."""
        try:
            return TradeData(
                symbol=data.get('s', ''),
                exchange=exchange,
                price=float(data.get('p', data.get('price', 0))),
                quantity=float(data.get('q', data.get('qty', 0))),
                side='buy' if data.get('m', False) else 'sell',
                trade_id=str(data.get('t', data.get('a', ''))),
                timestamp=datetime.fromtimestamp(
                    int(data.get('T', data.get('ts', 0))) / 1000
                ),
            )
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse trade: {e}")
            return None
            
    async def start(self, symbols: list[str]) -> None:
        """Start WebSocket connections for all enabled exchanges."""
        self.running = True
        
        for exchange, config in self.exchanges.items():
            if config.enabled:
                task = asyncio.create_task(self.connect(exchange, symbols))
                self._tasks.append(task)
                
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        
        logger.info(f"Started {len(self._tasks)} WebSocket connections")
        
    async def stop(self) -> None:
        """Stop all WebSocket connections gracefully."""
        self.running = False
        
        for task in self._tasks:
            task.cancel()
            
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            
        for exchange, ws in self.connections.items():
            try:
                await ws.close()
                logger.info(f"Closed {exchange.value} connection")
            except Exception as e:
                logger.warning(f"Error closing {exchange.value}: {e}")
                
        self._tasks.clear()
        self.connections.clear()
        
        for state in self.states.values():
            state.connected = False
            
    async def _heartbeat_loop(self) -> None:
        """Monitor connection health and report statistics."""
        while self.running:
            try:
                await asyncio.sleep(30)
                
                now = time.time()
                if now - self._last_mps_update >= 30:
                    for exchange in Exchange:
                        state = self.states.get(exchange)
                        if state:
                            state.messages_per_second = self._message_counts[exchange] / 30
                            
                            if self.on_heartbeat and state.last_message:
                                await self.on_heartbeat(exchange, state.last_message)
                                
                    self._message_counts = {e: 0 for e in Exchange}
                    self._last_mps_update = now
                    
                for exchange, state in self.states.items():
                    if state.connected and state.last_message:
                        elapsed = (datetime.now() - state.last_message).total_seconds()
                        if elapsed > 60:
                            logger.warning(
                                f"{exchange.value} no messages for {elapsed:.1f}s"
                            )
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                
    def get_state(self, exchange: Exchange) -> Optional[WebSocketState]:
        """Get connection state for exchange."""
        return self.states.get(exchange)
        
    def is_healthy(self, exchange: Exchange) -> bool:
        """Check if exchange connection is healthy."""
        state = self.states.get(exchange)
        if not state or not state.connected:
            return False
            
        if state.last_message:
            elapsed = (datetime.now() - state.last_message).total_seconds()
            return elapsed < 60
            
        return True
