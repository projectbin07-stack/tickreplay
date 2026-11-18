"""Small builders shared by the tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tickreplay import Bar, Fill, Order, OrderType, Side, Trade

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


def D(value: str | int) -> Decimal:
    return Decimal(str(value))


def ts(i: int, step: timedelta = HOUR) -> datetime:
    return T0 + i * step


def bar(
    i: int,
    o: str | int,
    h: str | int,
    low: str | int,
    c: str | int,
    v: str | int = 1_000_000,
    *,
    symbol: str = "X",
    step: timedelta = HOUR,
) -> Bar:
    return Bar(ts(i, step), symbol, D(o), D(h), D(low), D(c), D(v))


def flat_bar(i: int, price: str | int, v: str | int = 1_000_000, *, symbol: str = "X") -> Bar:
    return bar(i, price, price, price, price, v, symbol=symbol)


def bars_from_closes(closes: Sequence[str | int], *, symbol: str = "X") -> list[Bar]:
    """Bars whose open is the previous close, so next-bar-open fills are easy to predict."""
    out: list[Bar] = []
    previous = D(closes[0])
    for i, close in enumerate(closes):
        c = D(close)
        out.append(
            Bar(ts(i), symbol, previous, max(previous, c), min(previous, c), c, D(1_000_000))
        )
        previous = c
    return out


def order(
    i: int,
    side: Side,
    qty: str | int,
    kind: OrderType = OrderType.MARKET,
    *,
    order_id: int = 1,
    limit: str | None = None,
    stop: str | None = None,
    symbol: str = "X",
) -> Order:
    return Order(
        ts(i),
        order_id,
        symbol,
        side,
        kind,
        D(qty),
        limit_price=D(limit) if limit is not None else None,
        stop_price=D(stop) if stop is not None else None,
    )


def fill(
    i: int, side: Side, qty: str | int, price: str | int, fee: str | int = 0, *, symbol: str = "X"
) -> Fill:
    return Fill(ts(i), i + 1, i + 1, symbol, side, D(qty), D(price), D(fee), D(price))


def tick(i: int, price: str | int, size: str | int = 100, *, symbol: str = "X") -> Trade:
    return Trade(ts(i, timedelta(seconds=1)), symbol, D(price), D(size))
