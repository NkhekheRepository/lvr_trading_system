"""
Portfolio management with position and PnL tracking.

This module implements portfolio-level tracking including position
management, realized/unrealized PnL calculation, drawdown tracking,
and daily performance metrics. The PortfolioManager maintains the
authoritative record of portfolio state.

Core Concepts:
    Realized PnL: Profit/loss from closed positions (settled).
    Unrealized PnL: Mark-to-market PnL on open positions.
    Drawdown: Peak-to-current decline in capital.
    Daily PnL: PnL reset at start of each trading day.

Position Management:
    - Long positions: quantity > 0
    - Short positions: quantity < 0
    - Flat: quantity = 0
    
    Entry Price: Volume-weighted average price of opens.
    Current Price: Latest market price for mark-to-market.

Example:
    >>> from portfolio.portfolio import PortfolioManager
    >>> from app.schemas import FillEvent, Side
    >>>
    >>> manager = PortfolioManager(initial_capital=100000)
    >>> 
    >>> # Update from fills
    >>> fill = FillEvent(
    ...     symbol="BTCUSDT",
    ...     side=Side.BUY,
    ...     quantity=0.1,
    ...     price=50000,
    ...     timestamp=1700000000000
    ... )
    >>> manager.update_from_fill(fill)
    >>> 
    >>> # Get summary
    >>> summary = manager.get_summary()
    >>> print(f"Capital: ${summary['capital']:,.2f}")
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
    
    Provides centralized portfolio management including:
    - Position tracking per symbol
    - Realized and unrealized PnL calculation
    - Drawdown tracking from peak capital
    - Daily metrics with automatic reset
    - Capital availability accounting
    
    PnL Calculations:
        Realized: Closed trade PnL = qty × (exit - entry)
        Unrealized: Mark-to-market = qty × (current - entry)
        
        For shorts, signs are reversed.
    
    Capital Accounting:
        current_capital = initial + realized_pnl + unrealized_pnl
        available_capital = current_capital - margin_used + collateral
    
    Example:
        >>> manager = PortfolioManager(initial_capital=100000)
        >>> 
        >>> # After trade fills
        >>> manager.update_from_fill(buy_fill)
        >>> manager.update_market_prices({"BTCUSDT": 51000})
        >>> 
        >>> # Check PnL
        >>> print(f"Unrealized: {manager.portfolio.total_unrealized_pnl}")
        >>> print(f"Drawdown: {manager.portfolio.current_drawdown:.2%}")
    """

    def __init__(self, initial_capital: float = 100000.0):
        """
        Initialize portfolio manager.
        
        Args:
            initial_capital: Starting capital amount.
        """
        self.initial_capital = initial_capital
        self._portfolio = Portfolio(
            initial_capital=initial_capital,
            current_capital=initial_capital,
            available_capital=initial_capital
        )
        self._last_day_reset = self._get_trading_day()

    @property
    def portfolio(self) -> Portfolio:
        """
        Get current portfolio state.
        
        Returns:
            Portfolio object with all positions and metrics.
        """
        return self._portfolio

    def update_from_fill(self, fill: FillEvent) -> None:
        """
        Update portfolio from fill event.
        
        Processes the fill to update position entries and
        triggers PnL and drawdown recalculation.
        
        Args:
            fill: FillEvent containing trade details:
                - symbol: Trading pair
                - side: BUY (open/reduce short) or SELL (open/reduce long)
                - quantity: Fill quantity
                - price: Fill price
                - timestamp: Fill timestamp
        
        Side Effects:
            - Updates position entry price and quantity
            - Calculates realized PnL for closing trades
            - Updates total PnL metrics
            - Updates drawdown metrics
            - Checks for daily reset
        """
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
        """
        Handle buy fill.
        
        Logic:
            - Long existing (qty >= 0): Average up position
            - Short existing (qty < 0): Reduce or reverse short
        
        Args:
            pos: Position to update.
            fill: Buy fill event.
        """
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
        """
        Handle sell fill.
        
        Logic:
            - Short existing (qty <= 0): Average down position
            - Long existing (qty > 0): Reduce or reverse long
        
        Args:
            pos: Position to update.
            fill: Sell fill event.
        """
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
        """
        Update total PnL from all positions.
        
        Computes:
            - Per-position unrealized PnL
            - Total realized PnL
            - Total unrealized PnL
            - Current capital
            - Available capital
        
        Formula:
            unrealized = qty × (current - entry) for longs
                        = qty × (entry - current) for shorts
            
            current_capital = initial + realized + unrealized
        """
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
        """
        Update drawdown metrics.
        
        Drawdown = (peak - current) / peak
        
        Tracks:
            - peak_capital: Maximum capital achieved
            - current_drawdown: Current drawdown %
            - max_drawdown: Maximum drawdown encountered
        """
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
        """
        Reset daily metrics if new trading day.
        
        Automatically resets daily_pnl and daily_trades at
        midnight (start of new trading day).
        """
        current_day = self._get_trading_day()
        if current_day != self._last_day_reset:
            self._portfolio.daily_pnl = 0.0
            self._portfolio.daily_trades = 0
            self._portfolio.trading_day_start = int(time.time() * 1000)
            self._last_day_reset = current_day

    def _get_trading_day(self) -> int:
        """
        Get trading day timestamp (midnight).
        
        Returns:
            Unix timestamp of current day midnight UTC.
        """
        ts = int(time.time())
        return (ts // 86400) * 86400

    def update_market_prices(self, prices: dict[str, float]) -> None:
        """
        Update positions with current market prices.
        
        Performs mark-to-market valuation for all positions.
        
        Args:
            prices: Dictionary mapping symbol to current price.
        
        Example:
            >>> manager.update_market_prices({
            ...     "BTCUSDT": 51000,
            ...     "ETHUSDT": 3200
            ... })
        """
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
        """
        Get position for symbol.
        
        Args:
            symbol: Trading symbol.
        
        Returns:
            Position object (creates empty if none exists).
        """
        return self._portfolio.get_position(symbol)

    def close_position(self, symbol: str, current_price: float) -> float:
        """
        Close entire position and return realized PnL.
        
        Calculates final PnL and resets position to zero.
        
        Args:
            symbol: Symbol to close.
            current_price: Price to close at.
        
        Returns:
            Realized PnL from closing the position.
        
        Formula:
            LONG: pnl = qty × (current - entry)
            SHORT: pnl = |qty| × (entry - current)
        """
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
        """
        Close all positions and return PnL by symbol.
        
        Args:
            prices: Dictionary of symbol to closing prices.
        
        Returns:
            Dictionary mapping symbol to realized PnL.
        """
        results = {}
        for symbol in list(self._portfolio.positions.keys()):
            price = prices.get(symbol, 0)
            results[symbol] = self.close_position(symbol, price)
        return results

    def get_summary(self) -> dict:
        """
        Get portfolio summary.
        
        Returns:
            Dictionary with key portfolio metrics:
            - capital: Current total capital
            - available: Available capital
            - exposure: Total position exposure
            - leverage: Portfolio leverage
            - realized_pnl: Total realized PnL
            - unrealized_pnl: Total unrealized PnL
            - drawdown: Current drawdown %
            - max_drawdown: Maximum drawdown encountered
            - daily_pnl: Today's PnL
            - positions: Number of open positions
        """
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
