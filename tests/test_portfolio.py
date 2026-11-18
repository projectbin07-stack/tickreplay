from __future__ import annotations

import random
from decimal import Decimal

import pytest

from helpers import D, fill, ts
from tickreplay import Direction, Fill, Portfolio, Side


def check_identities(p: Portfolio) -> None:
    assert p.equity == p.cash + p.position_value
    assert p.equity == p.initial_cash + p.realised_pnl + p.unrealised_pnl - p.fees_paid


def test_long_position_walkthrough_matches_hand_computation() -> None:
    p = Portfolio(D(100000))
    assert p.equity == D(100000) and p.quantity("X") == 0 and p.mark_price("X") is None

    assert p.apply_fill(fill(0, Side.BUY, 10, 100, 1)) == 0
    assert p.cash == D(98999)  # 100000 - 1000 - 1
    p.mark("X", D(110))
    assert p.equity == D(100099)  # 98999 + 10 * 110
    assert p.unrealised_pnl == D(100)
    check_identities(p)

    p.apply_fill(fill(1, Side.BUY, 10, 120, 1))
    pos = p.positions["X"]
    assert (pos.quantity, pos.cost_basis, pos.average_cost) == (D(20), D(2200), D(110))
    assert p.cash == D(97798)

    realised = p.apply_fill(fill(2, Side.SELL, 5, 130, 2))
    assert realised == D(100)  # 5 * (130 - 110)
    assert (pos.quantity, pos.cost_basis) == (D(15), D(1650))
    assert p.cash == D(98446)  # 97798 + 650 - 2
    p.mark("X", D(130))
    assert p.position_value == D(1950) and p.gross_exposure == D(1950)
    assert p.unrealised_pnl == D(300)
    assert p.realised_pnl == D(100) and p.fees_paid == D(4)
    assert p.equity == D(100396)  # 100000 + 100 + 300 - 4
    assert p.traded_notional == D(2850)
    check_identities(p)
    assert p.round_trips == []  # still open

    p.apply_fill(fill(3, Side.SELL, 15, 130, 0))
    assert pos.quantity == 0 and pos.cost_basis == 0 and pos.average_cost == 0
    (trip,) = p.round_trips
    assert trip.direction is Direction.LONG
    assert (trip.quantity, trip.gross_pnl, trip.fees, trip.net_pnl) == (D(20), D(400), D(4), D(396))
    assert (trip.opened, trip.closed) == (ts(0), ts(3))
    assert p.equity == p.cash == D(100396)
    check_identities(p)


def test_short_position_and_flip_through_zero() -> None:
    p = Portfolio(D(10000))
    p.apply_fill(fill(0, Side.SELL, 10, 50))
    assert p.cash == D(10500) and p.quantity("X") == D(-10)
    p.mark("X", D(45))
    assert p.position_value == D(-450) and p.gross_exposure == D(450)
    assert p.unrealised_pnl == D(50) and p.equity == D(10050)

    assert p.apply_fill(fill(1, Side.BUY, 4, 45)) == D(20)  # 4 * (50 - 45)
    # Buy 10 @ 40 with a fee of 5: 6 close the short (+60), 4 open a long at 40.
    assert p.apply_fill(fill(2, Side.BUY, 10, 40, 5)) == D(60)
    pos = p.positions["X"]
    assert (pos.quantity, pos.cost_basis) == (D(4), D(160))
    (trip,) = p.round_trips
    assert trip.direction is Direction.SHORT
    assert (trip.gross_pnl, trip.fees) == (D(80), D(3))  # 6/10 of the fee belongs to the close
    p.mark("X", D(40))
    check_identities(p)

    p.apply_fill(fill(3, Side.SELL, 4, 42, 1))
    second = p.round_trips[1]
    assert second.direction is Direction.LONG
    assert (second.gross_pnl, second.fees, second.net_pnl) == (D(8), D(3), D(5))
    assert p.fees_paid == D(6) and sum((t.fees for t in p.round_trips), D(0)) == D(6)


def test_fees_reduce_equity_exactly() -> None:
    plain, charged = Portfolio(D(50000)), Portfolio(D(50000))
    script = [(Side.BUY, 30, "101.37", "1.25"), (Side.SELL, 12, "99.10", "0.75"),
              (Side.BUY, 7, "104.44", "2.00"), (Side.SELL, 25, "108.02", "3.10")]  # fmt: skip
    for i, (side, qty, price, fee) in enumerate(script):
        plain.apply_fill(fill(i, side, qty, price, 0))
        charged.apply_fill(fill(i, side, qty, price, fee))
        for p in (plain, charged):
            p.mark("X", D(price))
            check_identities(p)
        assert plain.equity - charged.equity == charged.fees_paid
    assert charged.fees_paid == D("7.10")
    assert plain.realised_pnl == charged.realised_pnl  # realised P&L is gross of fees


def test_identities_hold_exactly_under_random_trading() -> None:
    rng = random.Random(1234)
    p = Portfolio(D(1_000_000))
    prices = {"A": Decimal("50.00"), "B": Decimal("7.31")}
    for i in range(2000):
        symbol = rng.choice(["A", "B"])
        prices[symbol] = max(
            Decimal("0.01"), prices[symbol] + Decimal(rng.randint(-150, 150)) / 100
        )
        side = rng.choice([Side.BUY, Side.SELL])
        qty = Decimal(rng.randint(1, 4000)) / 1000  # fractional sizes force inexact averages
        fee = Decimal(rng.randint(0, 500)) / 100
        px = prices[symbol]
        p.apply_fill(Fill(ts(i), i + 1, i + 1, symbol, side, qty, px, fee, px))
        p.mark(symbol, prices[symbol])
        check_identities(p)
        assert all(pos.cost_basis >= 0 for pos in p.positions.values())
    assert len(p.round_trips) > 10
    closed_gross = sum((t.gross_pnl for t in p.round_trips), Decimal(0))
    open_gross = sum((t.gross_pnl for t in p._trips.values()), Decimal(0))
    assert closed_gross + open_gross == p.realised_pnl


def test_equity_curve_records_and_replaces_same_timestamp() -> None:
    p = Portfolio(D(1000))
    p.apply_fill(fill(0, Side.BUY, 1, 100))
    first = p.record(ts(0))
    assert (first.equity, first.cash, first.gross_exposure) == (D(1000), D(900), D(100))
    p.mark("X", D(150))
    p.record(ts(0))
    p.record(ts(1))
    assert [(pt.ts, pt.equity) for pt in p.equity_curve] == [(ts(0), D(1050)), (ts(1), D(1050))]


def test_gross_exposure_excluding_and_validation() -> None:
    p = Portfolio(D(1000))
    p.apply_fill(fill(0, Side.BUY, 2, 10, symbol="A"))
    p.apply_fill(fill(1, Side.SELL, 3, 20, symbol="B"))
    assert p.gross_exposure == D(80)
    assert p.gross_exposure_excluding("A") == D(60)
    assert p.gross_exposure_excluding("B") == D(20)
    with pytest.raises(ValueError):
        p.mark("A", D(0))
    with pytest.raises(ValueError):
        Portfolio(D(0))
