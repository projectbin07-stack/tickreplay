from __future__ import annotations

import dataclasses
from datetime import datetime

import pytest

from helpers import D, bar, flat_bar, order, tick, ts
from tickreplay import Bar, Direction, EventQueue, Fill, Order, OrderType, Side, Signal, Trade


def test_events_are_immutable() -> None:
    b = flat_bar(0, 10)
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.close = D(11)  # type: ignore[misc]
    o = order(0, Side.BUY, 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        o.quantity = D(2)  # type: ignore[misc]


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Bar(datetime(2024, 1, 1), "X", D(1), D(1), D(1), D(1), D(1))
    with pytest.raises(ValueError, match="timezone-aware"):
        Trade(datetime(2024, 1, 1), "X", D(1), D(1))


@pytest.mark.parametrize(
    ("o", "h", "low", "c", "v", "message"),
    [
        ("10", "9", "8", "9", "1", "high is below"),
        ("10", "11", "10.5", "10.8", "1", "low is above"),
        ("0", "1", "0", "1", "1", "positive"),
        ("-1", "1", "-1", "1", "1", "positive"),
        ("10", "11", "9", "10", "-5", "volume"),
    ],
)
def test_bar_validation(o: str, h: str, low: str, c: str, v: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        bar(0, o, h, low, c, v)


def test_bar_requires_decimals() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        Bar(ts(0), "X", 1.0, D(1), D(1), D(1), D(1))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite"):
        Bar(ts(0), "X", D("NaN"), D(1), D(1), D(1), D(1))


def test_trade_validation() -> None:
    with pytest.raises(ValueError, match="positive"):
        tick(0, 0)
    with pytest.raises(ValueError, match="non-negative"):
        tick(0, 1, -1)


def test_order_validation() -> None:
    with pytest.raises(ValueError, match="quantity"):
        order(0, Side.BUY, 0)
    with pytest.raises(ValueError, match="limit_price"):
        order(0, Side.BUY, 1, OrderType.LIMIT)
    with pytest.raises(ValueError, match="stop_price"):
        order(0, Side.BUY, 1, OrderType.STOP)
    with pytest.raises(ValueError, match="only valid on limit"):
        order(0, Side.BUY, 1, OrderType.MARKET, limit="5")
    with pytest.raises(ValueError, match="only valid on stop"):
        order(0, Side.BUY, 1, OrderType.LIMIT, limit="5", stop="4")
    assert order(0, Side.SELL, 3).signed_quantity == D(-3)
    assert Side.BUY.sign == 1 and Side.SELL.sign == -1


def test_fill_and_signal_validation() -> None:
    with pytest.raises(ValueError, match="quantity"):
        Fill(ts(0), 1, 1, "X", Side.BUY, D(0), D(1), D(0), D(1))
    with pytest.raises(ValueError, match="price"):
        Fill(ts(0), 1, 1, "X", Side.BUY, D(1), D(0), D(0), D(1))
    with pytest.raises(ValueError, match="fee"):
        Fill(ts(0), 1, 1, "X", Side.BUY, D(1), D(1), D(-1), D(1))
    f = Fill(ts(0), 1, 1, "X", Side.SELL, D(4), D("2.5"), D(0), D("2.5"))
    assert f.notional == D(10) and f.signed_quantity == D(-4)
    with pytest.raises(ValueError, match="stop_price"):
        Signal(ts(0), "X", Direction.LONG, D(0))


def test_queue_orders_by_timestamp_then_sequence() -> None:
    q = EventQueue()
    late = flat_bar(5, 10)
    early = flat_bar(1, 10)
    same_ts_first = order(3, Side.BUY, 1, order_id=1)
    same_ts_second = Signal(ts(3), "X", Direction.FLAT)
    same_ts_third: Order = order(3, Side.SELL, 1, order_id=2)
    assert q.push(late) == 0
    q.push(same_ts_first)
    q.push(early)
    q.push(same_ts_second)
    q.push(same_ts_third)
    assert len(q) == 5 and q.pushed == 5 and q
    assert q.peek_ts() == ts(1)
    assert [q.pop() for _ in range(5)] == [
        early,
        same_ts_first,
        same_ts_second,
        same_ts_third,
        late,
    ]
    assert not q and q.peek_ts() is None
    with pytest.raises(IndexError):
        q.pop()


def test_queue_never_compares_event_payloads() -> None:
    # Equal timestamps with different event types would raise TypeError if the heap
    # ever fell through to comparing the events themselves.
    q = EventQueue()
    events = [flat_bar(0, 10), tick(0, 10), Signal(ts(0), "X", Direction.LONG)]
    for e in events:
        q.push(e)
    assert [q.pop() for _ in events] == events
