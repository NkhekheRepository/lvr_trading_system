"""
Exchange Validator - Validates orders against exchange rules and constraints.

Ensures orders comply with:
- Symbol whitelist
- Quantity limits (min/max)
- Price limits (tick size, precision)
- Rate limits
- Position limits
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ExchangeLimits:
    """Exchange-specific limits."""
    min_quantity: float = 0.001
    max_quantity: float = 1000000.0
    min_price: float = 0.01
    max_price: float = 1000000.0
    price_precision: int = 2
    quantity_precision: int = 3
    tick_size: float = 0.01


EXCHANGE_LIMITS = {
    "BTCUSDT": ExchangeLimits(
        min_quantity=0.001,
        max_quantity=1000.0,
        price_precision=2,
        quantity_precision=3
    ),
    "ETHUSDT": ExchangeLimits(
        min_quantity=0.001,
        max_quantity=10000.0,
        price_precision=2,
        quantity_precision=3
    ),
    "SOLUSDT": ExchangeLimits(
        min_quantity=0.1,
        max_quantity=100000.0,
        price_precision=3,
        quantity_precision=1
    ),
    "BNBUSDT": ExchangeLimits(
        min_quantity=0.01,
        max_quantity=100000.0,
        price_precision=2,
        quantity_precision=2
    ),
}


class ValidationResult:
    """Result of order validation."""
    
    def __init__(self, valid: bool, reason: Optional[str] = None, adjusted_value: Optional[float] = None):
        self.valid = valid
        self.reason = reason
        self.adjusted_value = adjusted_value
    
    def __bool__(self) -> bool:
        return self.valid


class ExchangeValidator:
    """
    Validates orders against exchange rules.
    
    Features:
    - Symbol whitelist checking
    - Quantity validation (min/max/precision)
    - Price validation (tick size/precision)
    - Rate limit tracking
    - Position limit enforcement
    """

    def __init__(
        self,
        allowed_symbols: Optional[list[str]] = None,
        max_orders_per_sec: int = 50,
        max_orders_per_min: int = 1200
    ):
        self.allowed_symbols = set(allowed_symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"])
        self.max_orders_per_sec = max_orders_per_sec
        self.max_orders_per_min = max_orders_per_min
        
        self._order_timestamps_sec: list[float] = []
        self._order_timestamps_min: list[float] = []
        self._position_limits: dict[str, float] = {}
        self._current_positions: dict[str, float] = {}
        
        self._stats = {
            "total_validated": 0,
            "rejected": 0,
            "adjusted": 0,
            "rate_limited": 0,
            "symbol_rejected": 0
        }

    def validate_symbol(self, symbol: str) -> ValidationResult:
        """Validate symbol is allowed."""
        if symbol not in self.allowed_symbols:
            self._stats["symbol_rejected"] += 1
            return ValidationResult(False, f"Symbol {symbol} not in whitelist")
        return ValidationResult(True)

    def validate_quantity(self, quantity: float, symbol: str = "BTCUSDT") -> ValidationResult:
        """Validate order quantity."""
        limits = EXCHANGE_LIMITS.get(symbol, ExchangeLimits())
        
        if quantity < limits.min_quantity:
            return ValidationResult(False, f"Quantity {quantity} below min {limits.min_quantity}")
        
        if quantity > limits.max_quantity:
            return ValidationResult(False, f"Quantity {quantity} exceeds max {limits.max_quantity}")
        
        precision = 10 ** (-limits.quantity_precision)
        adjusted = round(quantity / precision) * precision
        
        if adjusted != quantity:
            self._stats["adjusted"] += 1
            return ValidationResult(True, "Quantity adjusted to match precision", adjusted)
        
        return ValidationResult(True)

    def validate_price(self, price: float, symbol: str = "BTCUSDT") -> ValidationResult:
        """Validate order price."""
        if price is None:
            return ValidationResult(True)
        
        limits = EXCHANGE_LIMITS.get(symbol, ExchangeLimits())
        
        if price < limits.min_price:
            return ValidationResult(False, f"Price {price} below min {limits.min_price}")
        
        if price > limits.max_price:
            return ValidationResult(False, f"Price {price} exceeds max {limits.max_price}")
        
        tick_adj = round(price / limits.tick_size) * limits.tick_size
        
        if tick_adj != price:
            self._stats["adjusted"] += 1
            return ValidationResult(True, "Price adjusted to tick size", tick_adj)
        
        return ValidationResult(True)

    def validate_rate_limit(self) -> ValidationResult:
        """Check rate limits."""
        now = time.time()
        
        self._order_timestamps_sec = [t for t in self._order_timestamps_sec if now - t < 1]
        self._order_timestamps_min = [t for t in self._order_timestamps_min if now - t < 60]
        
        if len(self._order_timestamps_sec) >= self.max_orders_per_sec:
            self._stats["rate_limited"] += 1
            return ValidationResult(False, f"Rate limit: {self.max_orders_per_sec}/sec reached")
        
        if len(self._order_timestamps_min) >= self.max_orders_per_min:
            self._stats["rate_limited"] += 1
            return ValidationResult(False, f"Rate limit: {self.max_orders_per_min}/min reached")
        
        self._order_timestamps_sec.append(now)
        self._order_timestamps_min.append(now)
        
        return ValidationResult(True)

    def validate_position_limit(
        self,
        symbol: str,
        side: str,
        quantity: float
    ) -> ValidationResult:
        """Validate position limit would not be exceeded."""
        current = self._current_positions.get(symbol, 0.0)
        limit = self._position_limits.get(symbol, 100.0)
        
        new_position = current + quantity if side.upper() == "BUY" else current - quantity
        
        if abs(new_position) > limit:
            return ValidationResult(
                False,
                f"Position limit exceeded: {abs(new_position)} > {limit}"
            )
        
        return ValidationResult(True)

    def set_position_limit(self, symbol: str, limit: float) -> None:
        """Set position limit for symbol."""
        self._position_limits[symbol] = limit

    def update_position(self, symbol: str, quantity: float) -> None:
        """Update tracked position."""
        self._current_positions[symbol] = self._current_positions.get(symbol, 0.0) + quantity

    def validate_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: Optional[float] = None
    ) -> tuple[bool, Optional[str], Optional[dict]]:
        """
        Full order validation.
        
        Returns:
            (valid, reason, adjustments)
        """
        self._stats["total_validated"] += 1
        
        result = self.validate_symbol(symbol)
        if not result:
            self._stats["rejected"] += 1
            return False, result.reason, None
        
        result = self.validate_quantity(quantity, symbol)
        if not result:
            self._stats["rejected"] += 1
            return False, result.reason, None
        
        if price:
            result = self.validate_price(price, symbol)
            if not result:
                self._stats["rejected"] += 1
                return False, result.reason, None
        
        result = self.validate_rate_limit()
        if not result:
            return False, result.reason, None
        
        result = self.validate_position_limit(symbol, side, quantity)
        if not result:
            self._stats["rejected"] += 1
            return False, result.reason, None
        
        adjustments = {}
        result = self.validate_quantity(quantity, symbol)
        if result.adjusted_value:
            adjustments["quantity"] = result.adjusted_value
        
        if price:
            result = self.validate_price(price, symbol)
            if result.adjusted_value:
                adjustments["price"] = result.adjusted_value
        
        return True, None, adjustments if adjustments else None

    def get_stats(self) -> dict:
        """Get validator statistics."""
        return self._stats.copy()

    def reset_rate_limits(self) -> None:
        """Reset rate limit counters."""
        self._order_timestamps_sec.clear()
        self._order_timestamps_min.clear()