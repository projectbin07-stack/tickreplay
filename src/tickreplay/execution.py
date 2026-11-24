"""Order execution.

:class:`ExecutionHandler` is the seam between the engine and whatever turns orders into
fills. The only implementation shipped is :class:`SimulatedExecution`, which is used by
both backtest and paper mode. A live handler (routing to a real venue) would implement
the same protocol; none exists in this project.

Fill rules of the simulator (``ts`` is the market event being matched):

* An order is eligible only for market events with ``ts`` strictly greater than the
  order's decision timestamp. A strategy therefore never fills on the bar it acted on.
* MARKET: fills at the bar open.
* LIMIT buy: if the bar opens at or below the limit it fills at the open, else if the
  low reaches the limit it fills at the limit. (Mirrored for sells.)
* STOP buy: if the bar opens at or above the stop it fills at the open (gap), else if
  the high reaches the stop it fills at the stop. (Mirrored for sells.)
* Slippage is applied to MARKET and STOP fills, never to LIMIT fills.
* Prices are not rounded unless a price tick is configured, either one ``price_tick``
  for every symbol or per symbol through ``price_ticks``. With a tick, slipped MARKET and
  STOP fills are rounded against the trader; a fill with zero slippage is never moved
  off its reference price.
* With ``max_participation`` set, all fills on one bar together cannot exceed that
  fraction of the bar's volume; the remainder of an order keeps working.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Protocol

from tickreplay.costs import FeeModel, NoSlippage, SlippageModel, apply_slippage
from tickreplay.events import ZERO, Bar, Fill, Order, OrderType, Side, Trade


@dataclass(frozen=True, slots=True)
class WorkingOrder:
    """Snapshot of an order still resting in the book."""

    order: Order
    remaining: Decimal

    @property
    def signed_remaining(self) -> Decimal:
        """Unfilled quantity signed by side."""
        return self.remaining * self.order.side.sign


class ExecutionHandler(Protocol):
    """What the engine needs from an execution venue."""

    def submit(self, order: Order) -> None:
        """Accept an order for execution."""
        ...

    def cancel(self, order_id: int) -> bool:
        """Cancel a working order; ``False`` if it is not working."""
        ...

    def open_orders(self, symbol: str | None = None) -> tuple[WorkingOrder, ...]:
        """Working orders, optionally for one symbol, in submission order."""
        ...

    def on_bar(self, bar: Bar) -> list[Fill]:
        """Match working orders against a bar."""
        ...

    def on_trade(self, trade: Trade) -> list[Fill]:
        """Match working orders against a trade tick."""
        ...


def check_price_ticks(
    price_tick: Decimal | None, price_ticks: Mapping[str, Decimal] | None
) -> None:
    """Raise ``ValueError`` unless every configured price tick is positive."""
    if price_tick is not None and price_tick <= 0:
        raise ValueError("price_tick must be positive")
    for symbol, tick in (price_ticks or {}).items():
        if tick <= 0:
            raise ValueError(f"price tick for {symbol} must be positive")


class _Resting:
    __slots__ = ("order", "remaining", "triggered")

    def __init__(self, order: Order) -> None:
        self.order = order
        self.remaining = order.quantity
        self.triggered = False


class SimulatedExecution:
    """Deterministic fill simulator used for backtests and paper trading."""

    def __init__(
        self,
        *,
        fees: FeeModel | None = None,
        slippage: SlippageModel | None = None,
        max_participation: Decimal | None = None,
        price_tick: Decimal | None = None,
        price_ticks: Mapping[str, Decimal] | None = None,
        qty_step: Decimal = Decimal(1),
    ) -> None:
        if max_participation is not None and not (0 < max_participation <= 1):
            raise ValueError("max_participation must be in (0, 1]")
        check_price_ticks(price_tick, price_ticks)
        if qty_step <= 0:
            raise ValueError("qty_step must be positive")
        self._fees = fees if fees is not None else FeeModel()
        self._slippage: SlippageModel = slippage if slippage is not None else NoSlippage()
        self._max_participation = max_participation
        self._tick = price_tick
        self._ticks = dict(price_ticks) if price_ticks is not None else {}
        self._step = qty_step
        self._book: dict[int, _Resting] = {}
        self._next_fill_id = 1

    def price_tick_for(self, symbol: str) -> Decimal | None:
        """Tick used to round slipped fills of ``symbol``; ``None`` means no rounding."""
        return self._ticks.get(symbol, self._tick)

    # -- order management -------------------------------------------------------------

    def submit(self, order: Order) -> None:
        if order.order_id in self._book:
            raise ValueError(f"duplicate order id {order.order_id}")
        self._book[order.order_id] = _Resting(order)

    def cancel(self, order_id: int) -> bool:
        return self._book.pop(order_id, None) is not None

    def open_orders(self, symbol: str | None = None) -> tuple[WorkingOrder, ...]:
        return tuple(
            WorkingOrder(r.order, r.remaining)
            for r in self._book.values()
            if symbol is None or r.order.symbol == symbol
        )

    # -- matching ---------------------------------------------------------------------

    def on_bar(self, bar: Bar) -> list[Fill]:
        return self._match(
            bar.ts, bar.symbol, bar.open, bar.high, bar.low, bar.volume, gap_improvement=True
        )

    def on_trade(self, trade: Trade) -> list[Fill]:
        # A tick is a degenerate bar. Resting limits fill at their own price, never
        # better, because the tick's price is where *someone else* traded.
        return self._match(
            trade.ts,
            trade.symbol,
            trade.price,
            trade.price,
            trade.price,
            trade.size,
            gap_improvement=False,
        )

    def _reference_price(
        self, resting: _Resting, open_: Decimal, high: Decimal, low: Decimal, gap: bool
    ) -> Decimal | None:
        order = resting.order
        buy = order.side is Side.BUY
        if order.order_type is OrderType.MARKET or resting.triggered:
            return open_
        if order.order_type is OrderType.LIMIT:
            limit = order.limit_price
            assert limit is not None
            if (open_ <= limit) if buy else (open_ >= limit):
                return open_ if gap else limit
            if (low <= limit) if buy else (high >= limit):
                return limit
            return None
        stop = order.stop_price
        assert stop is not None
        if (open_ >= stop) if buy else (open_ <= stop):
            return open_
        if (high >= stop) if buy else (low <= stop):
            return stop
        return None

    def _match(
        self,
        ts: datetime,
        symbol: str,
        open_: Decimal,
        high: Decimal,
        low: Decimal,
        volume: Decimal,
        *,
        gap_improvement: bool,
    ) -> list[Fill]:
        available: Decimal | None = None
        if self._max_participation is not None:
            raw = volume * self._max_participation / self._step
            available = raw.to_integral_value(rounding=ROUND_FLOOR) * self._step

        fills: list[Fill] = []
        for order_id in list(self._book):
            resting = self._book[order_id]
            order = resting.order
            if order.symbol != symbol or not ts > order.ts:
                continue
            reference = self._reference_price(resting, open_, high, low, gap_improvement)
            if reference is None:
                continue
            if order.order_type is OrderType.STOP:
                resting.triggered = True
            quantity = resting.remaining
            if available is not None:
                quantity = min(quantity, available)
            if quantity <= 0:
                continue
            if order.order_type is OrderType.LIMIT:
                price = reference
            else:
                bps = self._slippage.bps(quantity, volume)
                price = apply_slippage(reference, order.side, bps, self.price_tick_for(symbol))
            fill = Fill(
                ts=ts,
                fill_id=self._next_fill_id,
                order_id=order.order_id,
                symbol=symbol,
                side=order.side,
                quantity=quantity,
                price=price,
                fee=self._fees.fee(quantity * price),
                reference_price=reference,
            )
            self._next_fill_id += 1
            fills.append(fill)
            resting.remaining -= quantity
            if available is not None:
                available -= quantity
            if resting.remaining <= ZERO:
                del self._book[order_id]
        return fills
