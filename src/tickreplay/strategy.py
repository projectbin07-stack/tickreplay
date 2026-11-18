"""Strategy API.

A strategy subclasses :class:`Strategy` and overrides the callbacks it needs. It talks to
the engine only through the :class:`StrategyContext` it is handed, which exposes current
account state and order entry - and nothing about future market data.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from tickreplay.events import Bar, Direction, Fill, OrderType, Side, Trade
from tickreplay.execution import WorkingOrder


class StrategyContext(Protocol):
    """The engine-side handle a strategy uses to observe state and act."""

    @property
    def now(self) -> datetime | None:
        """Timestamp of the event being handled (``None`` before the first one)."""
        ...

    @property
    def cash(self) -> Decimal:
        """Current cash balance."""
        ...

    @property
    def equity(self) -> Decimal:
        """Cash plus position value at the latest marks."""
        ...

    def position(self, symbol: str) -> Decimal:
        """Signed position in ``symbol``."""
        ...

    def open_orders(self, symbol: str | None = None) -> tuple[WorkingOrder, ...]:
        """Orders still working, optionally filtered by symbol."""
        ...

    def signal(self, symbol: str, direction: Direction, stop_price: Decimal | None = None) -> None:
        """Emit a :class:`~tickreplay.events.Signal`; the engine sizes and routes it."""
        ...

    def submit(
        self,
        symbol: str,
        side: Side,
        quantity: Decimal,
        order_type: OrderType = OrderType.MARKET,
        *,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
    ) -> int:
        """Submit an explicit order and return its id. It is still risk-checked."""
        ...

    def cancel(self, order_id: int) -> bool:
        """Cancel a working order; ``False`` if it was not working."""
        ...


class Strategy:
    """Base class for strategies. All callbacks default to doing nothing."""

    #: Short name used in reports.
    name: str = "strategy"

    def on_start(self, ctx: StrategyContext) -> None:
        """Called once before any market data. Orders cannot be placed here."""

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        """Called with each completed bar."""

    def on_trade(self, ctx: StrategyContext, trade: Trade) -> None:
        """Called with each trade tick (only for tick feeds)."""

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None:
        """Called after a fill has been applied to the portfolio."""

    def describe(self) -> dict[str, str]:
        """Parameters to show in reports."""
        return {}
