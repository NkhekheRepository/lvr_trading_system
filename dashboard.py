"""
Web Dashboard - Real-time visualization via FastAPI + WebSocket.

Provides:
- Real-time position tracking
- Trade history
- Metrics visualization
- Decision log
- System health status

Usage:
    uvicorn dashboard:app --reload --port 8000
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from collections import deque
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
import secrets

logger = logging.getLogger(__name__)

security = HTTPBasic()

USERS_DB = {
    "nkhekhe": "nwa45690"
}

app = FastAPI(title="LVR Trading Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PositionUpdate(BaseModel):
    """Position data update."""
    symbol: str
    quantity: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    timestamp: int


class TradeUpdate(BaseModel):
    """Trade execution update."""
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    pnl: float
    fee: float
    timestamp: int


class MetricsUpdate(BaseModel):
    """System metrics update."""
    fill_rate: float
    avg_slippage: float
    rejection_rate: float
    daily_pnl: float
    drawdown: float
    leverage: float
    protection_level: int
    consecutive_failures: int
    timestamp: int


class DecisionUpdate(BaseModel):
    """Decision log update."""
    trace_id: str
    symbol: str
    outcome: str
    level: str
    reason: str
    quantity: float
    timestamp: int


class HealthUpdate(BaseModel):
    """System health update."""
    component: str
    status: str
    details: dict
    timestamp: int


class DashboardState:
    """Manages dashboard state and WebSocket connections."""
    
    def __init__(self, max_history: int = 1000):
        self.max_history = max_history
        
        self._connections: list[WebSocket] = []
        
        self._positions: dict[str, dict] = {}
        self._trades: deque = deque(maxlen=max_history)
        self._metrics_history: deque = deque(maxlen=max_history)
        self._decisions: deque = deque(maxlen=max_history)
        self._health: dict[str, dict] = {}
        
        self._stats = {
            "total_connections": 0,
            "messages_sent": 0,
            "positions_updated": 0,
            "trades_recorded": 0,
            "decisions_recorded": 0
        }

    async def connect(self, websocket: WebSocket) -> None:
        """Accept WebSocket connection."""
        await websocket.accept()
        self._connections.append(websocket)
        self._stats["total_connections"] += 1
        
        await websocket.send_json({
            "type": "connected",
            "timestamp": int(time.time() * 1000),
            "message": "Connected to LVR Trading Dashboard"
        })
        
        await self._send_snapshot(websocket)
        
        logger.info(f"WebSocket connected. Total: {len(self._connections)}")

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove WebSocket connection."""
        if websocket in self._connections:
            self._connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Total: {len(self._connections)}")

    async def _send_snapshot(self, websocket: WebSocket) -> None:
        """Send current state snapshot to new connection."""
        await websocket.send_json({
            "type": "snapshot",
            "positions": list(self._positions.values()),
            "trades": list(self._trades),
            "metrics": list(self._metrics_history)[-10:] if self._metrics_history else [],
            "health": self._health
        })

    async def broadcast(self, message: dict) -> None:
        """Broadcast message to all connections."""
        if not self._connections:
            return
        
        dead_connections = []
        
        for ws in self._connections:
            try:
                await ws.send_json(message)
                self._stats["messages_sent"] += 1
            except Exception as e:
                logger.warning(f"Failed to send: {e}")
                dead_connections.append(ws)
        
        for ws in dead_connections:
            self.disconnect(ws)

    async def broadcast_position(self, position: dict) -> None:
        """Broadcast position update."""
        self._positions[position["symbol"]] = position
        self._stats["positions_updated"] += 1
        
        await self.broadcast({
            "type": "position",
            "data": position
        })

    async def broadcast_trade(self, trade: dict) -> None:
        """Broadcast trade execution."""
        self._trades.append(trade)
        self._stats["trades_recorded"] += 1
        
        await self.broadcast({
            "type": "trade",
            "data": trade
        })

    async def broadcast_metrics(self, metrics: dict) -> None:
        """Broadcast metrics update."""
        self._metrics_history.append(metrics)
        
        await self.broadcast({
            "type": "metrics",
            "data": metrics
        })

    async def broadcast_decision(self, decision: dict) -> None:
        """Broadcast decision update."""
        self._decisions.append(decision)
        self._stats["decisions_recorded"] += 1
        
        await self.broadcast({
            "type": "decision",
            "data": decision
        })

    async def broadcast_health(self, health: dict) -> None:
        """Broadcast health update."""
        self._health[health["component"]] = health
        
        await self.broadcast({
            "type": "health",
            "data": health
        })

    def get_stats(self) -> dict:
        """Get dashboard statistics."""
        return {
            **self._stats,
            "active_connections": len(self._connections),
            "positions_tracked": len(self._positions),
            "trades_in_history": len(self._trades),
            "metrics_in_history": len(self._metrics_history)
        }


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)) -> HTTPBasicCredentials:
    """Verify user credentials for Basic Auth."""
    if credentials.username not in USERS_DB or USERS_DB[credentials.username] != credentials.password:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return credentials


dashboard_state = DashboardState()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates with optional auth via query params."""
    query_params = websocket.query_params
    
    auth_user = query_params.get("user")
    auth_pass = query_params.get("pass")
    
    if auth_user and auth_pass:
        if auth_user not in USERS_DB or USERS_DB[auth_user] != auth_pass:
            await websocket.close(code=4003, reason="Unauthorized")
            return
    
    await dashboard_state.connect(websocket)
    
    try:
        while True:
            data = await websocket.receive_text()
            
            try:
                import json
                message = json.loads(data)
                
                if message.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": int(time.time() * 1000)})
                    
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON received: {data}")
                
    except WebSocketDisconnect:
        dashboard_state.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        dashboard_state.disconnect(websocket)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "LVR Trading Dashboard",
        "version": "1.0.0",
        "status": "running",
        "endpoints": {
            "websocket": "/ws",
            "api": "/api"
        }
    }


@app.get("/api/state")
async def get_state(credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    """Get current system state."""
    return {
        "positions": list(dashboard_state._positions.values()),
        "recent_trades": list(dashboard_state._trades)[-20:],
        "recent_metrics": list(dashboard_state._metrics_history)[-10:],
        "recent_decisions": list(dashboard_state._decisions)[-20:],
        "health": dashboard_state._health
    }


@app.get("/api/stats")
async def get_stats(credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    """Get dashboard statistics."""
    return dashboard_state.get_stats()


@app.post("/api/broadcast")
async def broadcast_message(message: dict, credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    """Broadcast message to all clients (internal use)."""
    await dashboard_state.broadcast(message)
    return {"status": "broadcast"}


@app.post("/api/position")
async def update_position(position: PositionUpdate, credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    """Update position (internal use)."""
    await dashboard_state.broadcast_position(position.model_dump())
    return {"status": "updated"}


@app.post("/api/trade")
async def record_trade(trade: TradeUpdate, credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    """Record trade (internal use)."""
    await dashboard_state.broadcast_trade(trade.model_dump())
    return {"status": "recorded"}


@app.post("/api/metrics")
async def update_metrics(metrics: MetricsUpdate, credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    """Update metrics (internal use)."""
    await dashboard_state.broadcast_metrics(metrics.model_dump())
    return {"status": "updated"}


@app.post("/api/decision")
async def record_decision(decision: DecisionUpdate, credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    """Record decision (internal use)."""
    await dashboard_state.broadcast_decision(decision.model_dump())
    return {"status": "recorded"}


@app.post("/api/health")
async def update_health(health: HealthUpdate, credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    """Update health (internal use)."""
    await dashboard_state.broadcast_health(health.model_dump())
    return {"status": "updated"}


class DashboardClient:
    """Client for sending updates to dashboard from trading system."""
    
    def __init__(self, dashboard_url: str = "http://localhost:8000"):
        self.dashboard_url = dashboard_url
        self._session = None
    
    async def connect(self) -> None:
        """Initialize HTTP session."""
        import aiohttp
        self._session = aiohttp.ClientSession()
    
    async def close(self) -> None:
        """Close HTTP session."""
        if self._session:
            await self._session.close()
    
    async def send_position(self, position: dict) -> None:
        """Send position update."""
        if self._session:
            try:
                async with self._session.post(
                    f"{self.dashboard_url}/api/position",
                    json=position
                ) as resp:
                    pass
            except Exception as e:
                logger.warning(f"Dashboard position update failed: {e}")
    
    async def send_trade(self, trade: dict) -> None:
        """Send trade update."""
        if self._session:
            try:
                async with self._session.post(
                    f"{self.dashboard_url}/api/trade",
                    json=trade
                ) as resp:
                    pass
            except Exception as e:
                logger.warning(f"Dashboard trade update failed: {e}")
    
    async def send_metrics(self, metrics: dict) -> None:
        """Send metrics update."""
        if self._session:
            try:
                async with self._session.post(
                    f"{self.dashboard_url}/api/metrics",
                    json=metrics
                ) as resp:
                    pass
            except Exception as e:
                logger.warning(f"Dashboard metrics update failed: {e}")
    
    async def send_decision(self, decision: dict) -> None:
        """Send decision update."""
        if self._session:
            try:
                async with self._session.post(
                    f"{self.dashboard_url}/api/decision",
                    json=decision
                ) as resp:
                    pass
            except Exception as e:
                logger.warning(f"Dashboard decision update failed: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)