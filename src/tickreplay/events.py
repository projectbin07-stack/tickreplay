"""Typed, immutable events that flow through the engine.

All monetary values and quantities are :class:`decimal.Decimal`. All timestamps are
timezone-aware :class:`datetime.datetime` objects; naive timestamps are rejected so that
ordering never depends on the machine's local timezone.

Convention: a :class:`Bar` timestamp is the bar's *open* time. A strategy receives the
bar only once it is complete, so any order it submits can fill no earlier than the open
of the following bar.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TypeAlias

ZERO = Decimal(0)


class Side(enum.Enum):
    """Order / fill side."""

    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        """``+1`` for buys, ``-1`` for sells."""
        return 1 if self is Side.BUY else -1


class OrderType(enum.Enum):
    """Supported order types."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class Direction(enum.Enum):
    """Target stance carried by a :class:`Signal`."""

    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


def _require_aware(ts: datetime) -> None:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"timestamp must be timezone-aware, got {ts!r}")


def _require_decimal(name: str, value: object) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite, got {value}")


@dataclass(frozen=True, slots=True)
class Bar:
    """One completed OHLCV bar. ``ts`` is the bar's open time."""

    ts: datetime
    symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        _require_aware(self.ts)
        for name in ("open", "high", "low", "close", "volume"):
            _require_decimal(name, getattr(self, name))
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("bar prices must be positive")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("bar high is below open/close/low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("bar low is above open/close/high")
        if self.volume < 0:
            raise ValueError("bar volume must be non-negative")


@dataclass(frozen=True, slots=True)
class Trade:
    """One trade tick (a single print)."""

    ts: datetime
    symbol: str
    price: Decimal
    size: Decimal

    def __post_init__(self) -> None:
        _require_aware(self.ts)
        _require_decimal("price", self.price)
        _require_decimal("size", self.size)
        if self.price <= 0:
            raise ValueError("trade price must be positive")
        if self.size < 0:
            raise ValueError("trade size must be non-negative")


@dataclass(frozen=True, slots=True)
class Signal:
    """A strategy's desired stance; the engine sizes it into an order.

    ``stop_price`` is optional. When present and per-trade risk sizing is configured,
    the position is sized so that a move from the reference price to the stop loses
    the configured fraction of equity.
    """

    ts: datetime
    symbol: str
    direction: Direction
    stop_price: Decimal | None = None

    def __post_init__(self) -> None:
        _require_aware(self.ts)
        if self.stop_price is not None:
            _require_decimal("stop_price", self.stop_price)
            if self.stop_price <= 0:
                raise ValueError("stop_price must be positive")


@dataclass(frozen=True, slots=True)
class Order:
    """An order request. ``ts`` is the decision time (the event being handled)."""

    ts: datetime
    order_id: int
    symbol: str
    side: Side
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None

    def __post_init__(self) -> None:
        _require_aware(self.ts)
        _require_decimal("quantity", self.quantity)
        if self.quantity <= 0:
            raise ValueError("order quantity must be positive")
        if self.order_type is OrderType.LIMIT:
            if self.limit_price is None or self.limit_price <= 0:
                raise ValueError("limit orders need a positive limit_price")
        elif self.limit_price is not None:
            raise ValueError("limit_price is only valid on limit orders")
        if self.order_type is OrderType.STOP:
            if self.stop_price is None or self.stop_price <= 0:
                raise ValueError("stop orders need a positive stop_price")
        elif self.stop_price is not None:
            raise ValueError("stop_price is only valid on stop orders")

    @property
    def signed_quantity(self) -> Decimal:
        """Quantity signed by side (positive for buys)."""
        return self.quantity * self.side.sign


@dataclass(frozen=True, slots=True)
class Fill:
    """An execution against an order.

    ``reference_price`` is the pre-slippage price the simulator matched at (bar open,
    limit price or stop price); ``price`` is what the portfolio actually pays/receives.
    """

    ts: datetime
    fill_id: int
    order_id: int
    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal
    fee: Decimal
    reference_price: Decimal

    def __post_init__(self) -> None:
        _require_aware(self.ts)
        if self.quantity <= 0:
            raise ValueError("fill quantity must be positive")
        if self.price <= 0:
            raise ValueError("fill price must be positive")
        if self.fee < 0:
            raise ValueError("fill fee must be non-negative")

    @property
    def notional(self) -> Decimal:
        """Unsigned traded value (quantity x price)."""
        return self.quantity * self.price

    @property
    def signed_quantity(self) -> Decimal:
        """Quantity signed by side (positive for buys)."""
        return self.quantity * self.side.sign


MarketEvent: TypeAlias = Bar | Trade
Event: TypeAlias = Bar | Trade | Signal | Order | Fill
