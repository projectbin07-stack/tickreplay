from __future__ import annotations

import pytest

from helpers import D, bar, bars_from_closes, ts
from tickreplay import Engine, EngineConfig, OrderType, RiskConfig, Side, run_backtest
from tickreplay.strategies import DonchianBreakout, SmaCrossover, available, build


def test_registry() -> None:
    assert available() == ("sma", "donchian")
    sma = build("sma", {"fast": "3", "slow": "8"})
    assert isinstance(sma, SmaCrossover) and sma.describe() == {"fast": "3", "slow": "8"}
    don = build("donchian")
    assert isinstance(don, DonchianBreakout) and don.describe() == {"entry": "20", "exit": "10"}
    with pytest.raises(ValueError, match="unknown strategy"):
        build("martingale")
    with pytest.raises(ValueError, match="no parameter"):
        build("sma", {"window": "3"})
    with pytest.raises(ValueError, match="must be an integer"):
        build("sma", {"fast": "3.5"})
    with pytest.raises(ValueError, match="fast < slow"):
        build("sma", {"fast": "30", "slow": "10"})
    with pytest.raises(ValueError):
        build("donchian", {"entry": "0"})


def test_sma_crossover_trades_where_the_averages_cross() -> None:
    # fast=2, slow=3 on closes:
    #   i  close  fast   slow    stance
    #   2   10    10     10      flat (not above)
    #   3   11    10.5   10.33   LONG  -> buy at bar 4's open (= 11)
    #   4   12    11.5   11      long
    #   5   11    11.5   11.33   long
    #   6    9    10     10.67   FLAT  -> sell at bar 7's open (= 9)
    #   7    8    8.5    9.33    flat
    bars = bars_from_closes([10, 10, 10, 11, 12, 11, 9, 8])
    result = run_backtest(bars, SmaCrossover(fast=2, slow=3))
    # 95% of 100,000 / 11 = 8636.36 -> 8636 units
    assert [(f.ts, f.side, f.quantity, f.price) for f in result.fills] == [
        (ts(4), Side.BUY, D(8636), D(11)),
        (ts(7), Side.SELL, D(8636), D(9)),
    ]
    (trip,) = result.round_trips
    assert trip.gross_pnl == D(8636) * (D(9) - D(11))
    assert result.final_equity == D(100000) - 2 * D(8636)


def test_sma_needs_a_full_slow_window() -> None:
    bars = bars_from_closes([1, 2, 3, 4])  # rising, but shorter than slow=5
    assert run_backtest(bars, SmaCrossover(fast=2, slow=5)).fills == ()


def donchian_bars(tail: list[tuple[str, str, str, str]]) -> list[object]:
    head = [("10", "10.5", "9.5", "10")] * 3 + [
        ("10", "11.2", "9.9", "11"),  # close 11 > highest high 10.5 of bars 0-2: entry signal
        ("11", "11.5", "10.8", "11.2"),  # buy fills at this open; stop at 9.5 goes in
    ]
    return [bar(i, *row) for i, row in enumerate(head + tail)]


def test_donchian_enters_on_breakout_with_risk_sized_position_and_stop() -> None:
    bars = donchian_bars(
        [
            ("11.2", "11.3", "10.9", "11"),
            ("11", "11", "9.0", "9.2"),  # trades down through the 9.5 stop
            ("9.2", "9.3", "9.1", "9.2"),
        ]
    )
    cfg = EngineConfig(risk=RiskConfig(risk_per_trade=D("0.01")))
    engine = Engine(DonchianBreakout(entry=3, exit=2), cfg)
    result = engine.run(bars)  # type: ignore[arg-type]

    # Stop = lowest low of the two bars before the breakout bar = 9.5.
    # Risk 1% of 100,000 = 1,000 over (11 - 9.5) = 1.5 per unit -> 666 units.
    assert [(f.ts, f.side, f.quantity, f.price) for f in result.fills] == [
        (ts(4), Side.BUY, D(666), D(11)),
        (ts(6), Side.SELL, D(666), D("9.5")),
    ]
    entry_order, stop_order = result.orders
    assert entry_order.order_type is OrderType.MARKET
    assert (stop_order.order_type, stop_order.stop_price, stop_order.ts) == (
        OrderType.STOP,
        D("9.5"),
        ts(4),
    )
    (trip,) = result.round_trips
    assert trip.gross_pnl == D(666) * (D("9.5") - D(11)) == D(-999)  # about the 1% risked
    assert engine.execution.open_orders() == ()


def test_donchian_channel_exit_cancels_the_resting_stop() -> None:
    bars = donchian_bars(
        [
            ("11.2", "11.3", "11.0", "11.1"),
            ("11.1", "11.1", "10.5", "10.6"),  # close 10.6 < lowest low 10.8 of bars 4-5: exit
            ("10.6", "10.7", "10.5", "10.6"),  # market sell fills at this open
            ("10.6", "10.7", "10.5", "10.6"),
        ]
    )
    engine = Engine(DonchianBreakout(entry=3, exit=2))
    result = engine.run(bars)  # type: ignore[arg-type]
    assert [(f.ts, f.side, f.price) for f in result.fills] == [
        (ts(4), Side.BUY, D(11)),
        (ts(7), Side.SELL, D("10.6")),
    ]
    assert result.final_positions == {"X": D(0)}
    assert engine.execution.open_orders() == ()  # the 9.5 stop did not outlive the position


def test_donchian_moves_the_stop_as_partial_fills_arrive() -> None:
    # Thin volume: with 10% participation the 666-unit entry fills over several bars.
    rows = [("10", "10.5", "9.5", "10")] * 3 + [("10", "11.2", "9.9", "11")]
    rows += [("11", "11.5", "10.8", "11.2")] * 4
    bars = [bar(i, *row, 3000) for i, row in enumerate(rows)]
    cfg = EngineConfig(risk=RiskConfig(risk_per_trade=D("0.01")))
    engine = Engine(DonchianBreakout(entry=3, exit=2), cfg)
    result = engine.run(bars)
    assert [f.quantity for f in result.fills] == [D(300), D(300), D(66)]
    (stop,) = engine.execution.open_orders()
    assert stop.order.order_type is OrderType.STOP and stop.remaining == D(666)
    stops = [o for o in result.orders if o.order_type is OrderType.STOP]
    assert [o.quantity for o in stops] == [D(300), D(600), D(666)]
