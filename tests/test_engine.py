from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from helpers import D, bar, bars_from_closes, flat_bar, tick, ts
from tickreplay import (
    Bar,
    Direction,
    Engine,
    EngineConfig,
    EngineError,
    FeeModel,
    Fill,
    FixedBpsSlippage,
    Mode,
    Order,
    OrderType,
    RiskConfig,
    Side,
    Signal,
    Strategy,
    StrategyContext,
    Trade,
    run_backtest,
)
from tickreplay.data import gbm_bars
from tickreplay.events import Event
from tickreplay.strategies import build

Action = Callable[[StrategyContext, Bar], None]


class Scripted(Strategy):
    """Runs a callable on chosen bar indices and records everything it is shown."""

    name = "scripted"

    def __init__(self, actions: dict[int, Action]) -> None:
        self.actions = actions
        self.seen: list[Bar] = []
        self.fills: list[Fill] = []
        self.seen_at_fill: list[list[datetime]] = []

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        index = len(self.seen)
        self.seen.append(bar)
        if index in self.actions:
            self.actions[index](ctx, bar)

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None:
        self.fills.append(fill)
        self.seen_at_fill.append([b.ts for b in self.seen])


def buy(qty: int) -> Action:
    def act(ctx: StrategyContext, b: Bar) -> None:
        ctx.submit(b.symbol, Side.BUY, D(qty))

    return act


def sell(qty: int) -> Action:
    def act(ctx: StrategyContext, b: Bar) -> None:
        ctx.submit(b.symbol, Side.SELL, D(qty))

    return act


def signal(direction: Direction, stop: str | None = None) -> Action:
    return lambda ctx, b: ctx.signal(b.symbol, direction, D(stop) if stop else None)


# -- look-ahead -------------------------------------------------------------------------


def test_strategy_cannot_see_the_bar_it_is_filled_on() -> None:
    # Bar 3 opens far away from bar 2's close. If the engine leaked bar 3 to the
    # strategy, or filled at bar 2's close, the assertions below would fail.
    bars = [
        bar(0, 100, 101, 99, 100),
        bar(1, 100, 101, 99, 100),
        bar(2, 100, 101, 99, 100),
        bar(3, 150, 160, 140, 155),
        bar(4, 155, 156, 154, 155),
    ]
    strategy = Scripted({2: buy(10)})
    result = run_backtest(bars, strategy)

    (fill,) = result.fills
    assert fill.ts == bars[3].ts
    assert fill.price == bars[3].open == D(150)
    assert fill.price != bars[2].close

    # When the fill was delivered the strategy had been shown bars 0..2 only.
    (seen,) = strategy.seen_at_fill
    assert seen == [b.ts for b in bars[:3]]
    assert max(seen) < fill.ts
    # And the order itself is stamped with the decision bar, strictly before the fill.
    (order,) = result.orders
    assert order.ts == bars[2].ts < fill.ts


def test_limit_order_cannot_fill_on_the_bar_that_prompted_it() -> None:
    # Bar 1 trades down to 90. A limit buy at 95 placed after seeing bar 1 must not be
    # filled by bar 1's own range; later bars never get near 95.
    bars = [flat_bar(0, 100), bar(1, 100, 101, 90, 100), flat_bar(2, 100), flat_bar(3, 100)]

    def place(ctx: StrategyContext, b: Bar) -> None:
        ctx.submit(b.symbol, Side.BUY, D(1), OrderType.LIMIT, limit_price=D(95))

    result = run_backtest(bars, Scripted({1: place}))
    assert result.fills == ()
    assert len(result.orders) == 1


def test_no_same_timestamp_fill_across_symbols() -> None:
    # Deciding on A's bar at T must not produce a fill on B's bar at the same T.
    bars = [
        flat_bar(0, 10, symbol="A"),
        flat_bar(0, 20, symbol="B"),
        flat_bar(1, 11, symbol="A"),
        flat_bar(1, 22, symbol="B"),
    ]

    class CrossSymbol(Strategy):
        def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
            if bar.symbol == "A" and bar.ts == ts(0):
                ctx.submit("B", Side.BUY, D(1))

    result = run_backtest(bars, CrossSymbol())
    # B had no price when the order was decided, so it is rejected rather than guessed.
    assert result.fills == ()
    assert [r.reason for r in result.rejections] == ["no market data for symbol yet"]

    class CrossSymbolLater(Strategy):
        def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
            if bar.symbol == "A" and bar.ts == ts(1):
                ctx.submit("B", Side.BUY, D(1))

    extended = [*bars, flat_bar(2, 12, symbol="A"), flat_bar(2, 25, symbol="B")]
    result = run_backtest(extended, CrossSymbolLater())
    (fill,) = result.fills
    assert (fill.ts, fill.price) == (ts(2), D(25))  # not B's bar at ts(1), priced 22


# -- determinism and event order --------------------------------------------------------


def config_with_costs() -> EngineConfig:
    return EngineConfig(
        fees=FeeModel(fixed=D(1), rate_bps=D(5)),
        slippage=FixedBpsSlippage(D(3)),
        risk=RiskConfig(risk_per_trade=D("0.01"), daily_loss_limit=D("0.03")),
    )


@pytest.mark.parametrize("name", ["sma", "donchian"])
def test_runs_are_reproducible(name: str) -> None:
    bars = gbm_bars(seed=11, n=800)
    first = run_backtest(bars, build(name), config_with_costs())
    second = run_backtest(list(reversed(bars)), build(name), config_with_costs())
    assert len(first.fills) > 4
    assert first.fills == second.fills
    assert first.equity_curve == second.equity_curve
    assert first.digest() == second.digest()
    other = run_backtest(gbm_bars(seed=12, n=800), build(name), config_with_costs())
    assert other.digest() != first.digest()


def test_event_order_within_a_timestamp() -> None:
    log: list[tuple[int, str]] = []

    def listener(event: Event) -> None:
        log.append((int((event.ts - ts(0)) / timedelta(hours=1)), type(event).__name__))

    bars = bars_from_closes([10, 10, 10, 10])
    engine = Engine(
        Scripted({0: signal(Direction.LONG), 2: signal(Direction.FLAT)}), listener=listener
    )
    result = engine.run(bars)
    assert log == [
        (0, "Bar"),
        (0, "Signal"),
        (0, "Order"),
        (1, "Fill"),  # fill at bar 1's open comes before bar 1 is shown
        (1, "Bar"),
        (2, "Bar"),
        (2, "Signal"),
        (2, "Order"),
        (3, "Fill"),
        (3, "Bar"),
    ]
    assert result.events_processed == len(log)
    assert result.mode is Mode.BACKTEST


def test_engine_misuse_is_rejected() -> None:
    with pytest.raises(EngineError, match="backwards"):
        Engine(Strategy()).run([flat_bar(1, 10), flat_bar(0, 10)])

    engine = Engine(Strategy())
    engine.run([flat_bar(0, 10)])
    with pytest.raises(EngineError, match="only be run once"):
        engine.run([flat_bar(1, 10)])

    class Eager(Strategy):
        def on_start(self, ctx: StrategyContext) -> None:
            assert ctx.now is None
            ctx.submit("X", Side.BUY, D(1))

    with pytest.raises(EngineError, match="before the first market event"):
        Engine(Eager()).run([flat_bar(0, 10)])

    with pytest.raises(ValueError):
        EngineConfig(initial_cash=D(0))
    with pytest.raises(ValueError):
        EngineConfig(signal_allocation=D("1.5"))


# -- accounting through the engine ------------------------------------------------------


def test_fees_reduce_final_equity_exactly() -> None:
    bars = gbm_bars(seed=5, n=300)
    actions: dict[int, Action] = {
        i: (buy(40) if (i // 10) % 2 == 0 else sell(40)) for i in range(5, 290, 10)
    }
    free = run_backtest(bars, Scripted(actions), EngineConfig())
    costly = run_backtest(
        bars, Scripted(actions), EngineConfig(fees=FeeModel(fixed=D("1.5"), rate_bps=D(7)))
    )
    assert len(free.fills) == len(costly.fills) == 29
    assert costly.fees_paid == sum((f.fee for f in costly.fills), Decimal(0)) > 0
    assert free.final_equity - costly.final_equity == costly.fees_paid
    for a, b in zip(free.equity_curve, costly.equity_curve, strict=True):
        assert a.gross_exposure == b.gross_exposure


@pytest.mark.parametrize("name", ["sma", "donchian"])
def test_accounting_invariants_hold_at_every_step(name: str) -> None:
    cfg = config_with_costs()
    engine = Engine(build(name), cfg)
    checked = 0

    def check(event: Event) -> None:
        nonlocal checked
        p = engine.portfolio
        assert p.equity == p.cash + p.position_value
        assert p.equity == cfg.initial_cash + p.realised_pnl + p.unrealised_pnl - p.fees_paid
        checked += 1

    engine._listener = check
    result = engine.run(gbm_bars(seed=3, n=600))
    assert checked == result.events_processed > 600
    assert result.final_equity == result.final_cash + sum(
        (q * engine.portfolio.mark_price(s) for s, q in result.final_positions.items()),  # type: ignore[operator]
        Decimal(0),
    )
    assert result.fees_paid == sum((f.fee for f in result.fills), Decimal(0))
    assert result.traded_notional == sum((f.notional for f in result.fills), Decimal(0))


# -- signals and sizing -----------------------------------------------------------------


def test_signal_sizing_by_allocation() -> None:
    bars = bars_from_closes([100, 100, 100, 100, 100])
    result = run_backtest(bars, Scripted({0: signal(Direction.LONG), 2: signal(Direction.FLAT)}))
    # 95% of 100,000 at a price of 100 -> 950 units.
    assert [(f.side, f.quantity, f.price) for f in result.fills] == [
        (Side.BUY, D(950), D(100)),
        (Side.SELL, D(950), D(100)),
    ]
    assert result.final_positions == {"X": D(0)}
    assert len(result.round_trips) == 1


def test_signal_sizing_by_per_trade_risk() -> None:
    bars = bars_from_closes([100, 100, 100])
    cfg = EngineConfig(risk=RiskConfig(risk_per_trade=D("0.01")))
    result = run_backtest(bars, Scripted({0: signal(Direction.LONG, "95")}), cfg)
    assert [f.quantity for f in result.fills] == [D(200)]  # 1,000 at risk / 5 per unit
    # Without a stop the same config falls back to the allocation.
    result = run_backtest(bars, Scripted({0: signal(Direction.LONG)}), cfg)
    assert [f.quantity for f in result.fills] == [D(950)]
    # A very tight stop is capped by the allocation instead of levering up.
    result = run_backtest(bars, Scripted({0: signal(Direction.LONG, "99.99")}), cfg)
    assert [f.quantity for f in result.fills] == [D(950)]


def test_signal_sizing_respects_position_and_exposure_limits() -> None:
    bars = bars_from_closes([100, 100, 100])
    cfg = EngineConfig(risk=RiskConfig(max_position=D(300)))
    result = run_backtest(bars, Scripted({0: signal(Direction.LONG)}), cfg)
    assert [f.quantity for f in result.fills] == [D(300)]
    cfg = EngineConfig(risk=RiskConfig(max_gross_exposure=D("0.5")))
    result = run_backtest(bars, Scripted({0: signal(Direction.LONG)}), cfg)
    assert [f.quantity for f in result.fills] == [D(500)]


def test_short_signal_and_reversal() -> None:
    bars = bars_from_closes([100, 100, 100, 100, 100])
    result = run_backtest(
        bars,
        Scripted(
            {0: signal(Direction.SHORT), 1: signal(Direction.SHORT), 2: signal(Direction.LONG)}
        ),
    )
    # Short 950, a repeated SHORT is ignored, then LONG buys back 950 and 950 more.
    assert [(f.side, f.quantity) for f in result.fills] == [
        (Side.SELL, D(950)),
        (Side.BUY, D(1900)),
    ]
    assert result.final_positions == {"X": D(950)}
    assert [t.direction for t in result.round_trips] == [Direction.SHORT]


def test_flat_signal_cancels_working_orders() -> None:
    bars = bars_from_closes([100, 100, 100, 100])

    def rest_a_limit(ctx: StrategyContext, b: Bar) -> None:
        ctx.submit(b.symbol, Side.BUY, D(5), OrderType.LIMIT, limit_price=D(50))
        assert ctx.open_orders() == ()  # not accepted until the order event is handled

    def check_then_flat(ctx: StrategyContext, b: Bar) -> None:
        (working,) = ctx.open_orders(b.symbol)
        assert working.order.limit_price == D(50) and working.remaining == D(5)
        ctx.signal(b.symbol, Direction.FLAT)

    engine = Engine(Scripted({0: rest_a_limit, 1: check_then_flat}))
    result = engine.run(bars)
    assert result.fills == () and engine.execution.open_orders() == ()


def test_zero_sized_signal_is_recorded_as_rejection() -> None:
    bars = bars_from_closes([100, 100, 100])
    result = run_backtest(
        bars, Scripted({0: signal(Direction.LONG)}), EngineConfig(initial_cash=D(50))
    )
    assert result.fills == ()
    assert [r.reason for r in result.rejections] == ["signal sized to zero quantity"]
    assert result.rejections[0].order is None


def test_explicit_orders_are_risk_checked() -> None:
    bars = bars_from_closes([100, 100, 100, 100])
    cfg = EngineConfig(risk=RiskConfig(max_position=D(10)))
    result = run_backtest(bars, Scripted({0: buy(11), 1: buy(10), 2: buy(1)}), cfg)
    assert [f.quantity for f in result.fills] == [D(10)]
    assert [(r.order.quantity if r.order else None) for r in result.rejections] == [D(11), D(1)]
    assert all("max position" in r.reason for r in result.rejections)
    assert [o.quantity for o in result.orders] == [D(10)]


def test_cancel_through_context() -> None:
    bars = bars_from_closes([100, 100, 100, 100])
    ids: list[int] = []

    def place(ctx: StrategyContext, b: Bar) -> None:
        ids.append(ctx.submit(b.symbol, Side.BUY, D(1), OrderType.LIMIT, limit_price=D(50)))

    def cancel(ctx: StrategyContext, b: Bar) -> None:
        assert ctx.cancel(ids[0]) is True
        assert ctx.cancel(ids[0]) is False
        assert ctx.cash == ctx.equity == D(100000) and ctx.position("X") == 0
        assert ctx.now == b.ts

    engine = Engine(Scripted({0: place, 1: cancel}))
    engine.run(bars)
    assert ids == [1] and engine.execution.open_orders() == ()


# -- daily loss limit -------------------------------------------------------------------


def crash_bars() -> list[Bar]:
    # Day 1: enter at 100, then the price falls to 90 (equity -9.5% with 950 units).
    # Day 2 starts at index 24.
    closes = [100, 100, 90, 90, 90, 90] + [90] * 18 + [90, 90, 90]
    return bars_from_closes(closes)


def test_daily_loss_limit_halts_new_entries_until_next_day() -> None:
    actions = {
        0: signal(Direction.LONG),  # fills 950 @ 100 on bar 1
        3: signal(Direction.FLAT),  # exits are still allowed during the halt
        5: signal(Direction.LONG),  # refused: halted
        6: buy(1),  # refused: halted
        24: signal(Direction.LONG),  # next day: allowed again
    }
    cfg = EngineConfig(risk=RiskConfig(daily_loss_limit=D("0.05")))
    result = run_backtest(crash_bars(), Scripted(actions), cfg)

    assert result.halts == (ts(2),)  # equity 90,500 <= 95,000 on bar 2
    assert [(f.ts, f.side, f.quantity, f.price) for f in result.fills] == [
        (ts(1), Side.BUY, D(950), D(100)),
        (ts(4), Side.SELL, D(950), D(90)),
        (ts(25), Side.BUY, D(955), D(90)),  # floor(0.95 * 90,500 / 90)
    ]
    assert [(r.ts, r.reason) for r in result.rejections] == [
        (ts(5), "daily loss limit reached: trading halted for the day"),
        (ts(6), "daily loss limit reached: trading halted for the day"),
    ]


def test_flatten_on_halt_closes_positions_at_the_next_open() -> None:
    cfg = EngineConfig(risk=RiskConfig(daily_loss_limit=D("0.05"), flatten_on_halt=True))
    result = run_backtest(crash_bars(), Scripted({0: signal(Direction.LONG)}), cfg)
    assert result.halts == (ts(2),)
    assert [(f.ts, f.side, f.quantity, f.price) for f in result.fills] == [
        (ts(1), Side.BUY, D(950), D(100)),
        (ts(3), Side.SELL, D(950), D(90)),
    ]
    assert result.final_positions == {"X": D(0)}
    assert result.final_equity == D(90500)


def test_halt_cancels_resting_entries_but_keeps_protective_exits() -> None:
    def setup(ctx: StrategyContext, b: Bar) -> None:
        ctx.submit(b.symbol, Side.BUY, D(950))

    def rest_orders(ctx: StrategyContext, b: Bar) -> None:
        ctx.submit(b.symbol, Side.BUY, D(10), OrderType.LIMIT, limit_price=D(50))
        ctx.submit(b.symbol, Side.SELL, D(950), OrderType.STOP, stop_price=D(60))

    engine = Engine(
        Scripted({0: setup, 1: rest_orders}),
        EngineConfig(risk=RiskConfig(daily_loss_limit=D("0.05"))),
    )
    result = engine.run(crash_bars())
    assert result.halts == (ts(2),)
    (survivor,) = engine.execution.open_orders()
    assert survivor.order.order_type is OrderType.STOP and survivor.order.side is Side.SELL


# -- ticks ------------------------------------------------------------------------------


def test_trade_tick_feed_drives_on_trade_and_fills_on_a_later_tick() -> None:
    class TickBuyer(Strategy):
        def __init__(self) -> None:
            self.prices: list[Decimal] = []

        def on_trade(self, ctx: StrategyContext, trade: Trade) -> None:
            self.prices.append(trade.price)
            if len(self.prices) == 2:
                ctx.submit(trade.symbol, Side.BUY, D(3))

    ticks = [tick(0, "10.00"), tick(1, "10.10"), tick(2, "10.25"), tick(3, "10.20")]
    strategy = TickBuyer()
    result = run_backtest(ticks, strategy, EngineConfig(max_participation=None))
    (fill,) = result.fills
    assert (fill.ts, fill.price, fill.quantity) == (ticks[2].ts, D("10.25"), D(3))
    assert strategy.prices == [t.price for t in ticks]
    assert len(result.equity_curve) == 4
    assert result.final_equity == D(100000) + 3 * (D("10.20") - D("10.25"))


def test_event_types_reach_the_listener() -> None:
    kinds: set[type] = set()
    Engine(Scripted({0: signal(Direction.LONG)}), listener=lambda e: kinds.add(type(e))).run(
        bars_from_closes([10, 10, 10])
    )
    assert kinds == {Bar, Signal, Order, Fill}
