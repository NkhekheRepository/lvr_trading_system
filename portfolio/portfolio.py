"""
Portfolio management with position and PnL tracking.
"""

import logging
import time
from typing import Optional

from app.schemas import FillEvent, Portfolio, Position, Side

logger = logging.getLogger(__name__)

EPS = 1e-10


class PortfolioManager:
    """
    Manages portfolio positions and PnL tracking.
    
    Tracks:
    - Positions per symbol
    - Realized and unrealized PnL
    - Drawdown
    - Daily metrics
    """

    def __init__(self, initial_capital: float = 100000.0):
        self.initial_capital = initial_capital
        self._portfolio = Portfolio(
            initial_capital=initial_capital,
            current_capital=initial_capital,
            available_capital=initial_capital
        )
        self._last_day_reset = self._get_trading_day()

    @property
    def portfolio(self) -> Portfolio:
        return self._portfolio

    def update_from_fill(self, fill: FillEvent) -> None:
        """Update portfolio from fill event."""
        pos = self._portfolio.get_position(fill.symbol)

        if pos.quantity == 0:
            pos.entry_price = fill.price
            pos.entry_timestamp = fill.timestamp

        if fill.side == Side.BUY:
            self._handle_buy_fill(pos, fill)
        else:
            self._handle_sell_fill(pos, fill)

        pos.current_price = fill.price
        pos.last_update = fill.timestamp

        self._update_pnl()
        self._update_drawdown()
        self._check_daily_reset()

    def _handle_buy_fill(self, pos: Position, fill: FillEvent) -> None:
        """Handle buy fill."""
        if pos.quantity >= 0:
            total_cost = pos.entry_price * pos.quantity + fill.price * fill.quantity
            new_qty = pos.quantity + fill.quantity
            pos.entry_price = total_cost / new_qty if new_qty > 0 else 0
            pos.quantity = new_qty
        else:
            close_qty = min(abs(pos.quantity), fill.quantity)
            pos.realized_pnl += close_qty * (pos.entry_price - fill.price)
            pos.quantity += fill.quantity
            if pos.quantity < 0:
                pos.entry_price = fill.price

    def _handle_sell_fill(self, pos: Position, fill: FillEvent) -> None:
        """Handle sell fill."""
        if pos.quantity <= 0:
            total_cost = abs(pos.entry_price * pos.quantity) + fill.price * fill.quantity
            new_qty = pos.quantity - fill.quantity
            pos.entry_price = total_cost / abs(new_qty) if new_qty != 0 else 0
            pos.quantity = new_qty
        else:
            close_qty = min(pos.quantity, fill.quantity)
            pos.realized_pnl += close_qty * (fill.price - pos.entry_price)
            pos.quantity -= fill.quantity
            if pos.quantity > 0:
                pass
            else:
                pos.entry_price = fill.price

        if abs(pos.quantity) < EPS:
            pos.quantity = 0
            pos.entry_price = 0
            pos.entry_timestamp = None

    def _update_pnl(self) -> None:
        """Update total PnL."""
        total_realized = 0.0
        total_unrealized = 0.0

        for symbol, pos in self._portfolio.positions.items():
            pos.total_pnl = pos.realized_pnl + pos.unrealized_pnl
            total_realized += pos.realized_pnl
            total_unrealized += pos.unrealized_pnl

        self._portfolio.total_realized_pnl = total_realized
        self._portfolio.total_unrealized_pnl = total_unrealized
        self._portfolio.daily_pnl = total_realized

        self._portfolio.current_capital = (
            self.initial_capital + total_realized + total_unrealized
        )
        self._portfolio.available_capital = (
            self._portfolio.current_capital - 
            sum(p.notional_value for p in self._portfolio.positions.values()) +
            sum(abs(p.quantity * p.entry_price) for p in self._portfolio.positions.values())
        )

    def _update_drawdown(self) -> None:
        """Update drawdown metrics."""
        if self._portfolio.current_capital > self._portfolio.peak_capital:
            self._portfolio.peak_capital = self._portfolio.current_capital

        if self._portfolio.peak_capital > EPS:
            self._portfolio.current_drawdown = (
                (self._portfolio.peak_capital - self._portfolio.current_capital) /
                self._portfolio.peak_capital
            )

        if self._portfolio.current_drawdown > self._portfolio.max_drawdown:
            self._portfolio.max_drawdown = self._portfolio.current_drawdown

    def _check_daily_reset(self) -> None:
        """Reset daily metrics if new trading day."""
        current_day = self._get_trading_day()
        if current_day != self._last_day_reset:
            self._portfolio.daily_pnl = 0.0
            self._portfolio.daily_trades = 0
            self._portfolio.trading_day_start = int(time.time() * 1000)
            self._last_day_reset = current_day

    def _get_trading_day(self) -> int:
        """Get trading day timestamp (midnight)."""
        ts = int(time.time())
        return (ts // 86400) * 86400

    def update_market_prices(self, prices: dict[str, float]) -> None:
        """Update positions with current market prices."""
        for symbol, price in prices.items():
            if symbol in self._portfolio.positions:
                pos = self._portfolio.positions[symbol]
                pos.current_price = price

                if pos.quantity != 0:
                    if pos.quantity > 0:
                        pos.unrealized_pnl = pos.quantity * (price - pos.entry_price)
                    else:
                        pos.unrealized_pnl = abs(pos.quantity) * (pos.entry_price - price)

        self._update_pnl()
        self._update_drawdown()

    def get_position(self, symbol: str) -> Position:
        """Get position for symbol."""
        return self._portfolio.get_position(symbol)

    def close_position(self, symbol: str, current_price: float) -> float:
        """Close entire position and return realized PnL."""
        pos = self._portfolio.get_position(symbol)

        if pos.quantity == 0:
            return 0.0

        if pos.quantity > 0:
            pnl = pos.quantity * (current_price - pos.entry_price)
        else:
            pnl = abs(pos.quantity) * (pos.entry_price - current_price)

        pos.realized_pnl += pnl
        pos.quantity = 0
        pos.entry_price = 0
        pos.unrealized_pnl = 0
        pos.entry_timestamp = None

        self._update_pnl()
        return pnl

    def close_all_positions(self, prices: dict[str, float]) -> dict[str, float]:
        """Close all positions and return PnL by symbol."""
        results = {}
        for symbol in list(self._portfolio.positions.keys()):
            price = prices.get(symbol, 0)
            results[symbol] = self.close_position(symbol, price)
        return results

    def get_summary(self) -> dict:
        """Get portfolio summary."""
        return {
            "capital": self._portfolio.current_capital,
            "available": self._portfolio.available_capital,
            "exposure": self._portfolio.total_exposure,
            "leverage": self._portfolio.portfolio_leverage,
            "realized_pnl": self._portfolio.total_realized_pnl,
            "unrealized_pnl": self._portfolio.total_unrealized_pnl,
            "drawdown": self._portfolio.current_drawdown,
            "max_drawdown": self._portfolio.max_drawdown,
            "daily_pnl": self._portfolio.daily_pnl,
            "positions": len([p for p in self._portfolio.positions.values() if not p.is_flat])
        }
