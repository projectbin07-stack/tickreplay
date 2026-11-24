from __future__ import annotations

from decimal import Decimal

import pytest

from helpers import D, bar, flat_bar, order, tick
from tickreplay import (
    FeeModel,
    FixedBpsSlippage,
    NoSlippage,
    OrderType,
    Side,
    SimulatedExecution,
    VolumeParticipationSlippage,
)
from tickreplay.costs import apply_slippage

TICK = D("0.01")


# -- cost models ------------------------------------------------------------------------


def test_fee_model_fixed_plus_proportional() -> None:
    fees = FeeModel(fixed=D(1), rate_bps=D(10))
    assert fees.fee(D(10000)) == D("11.00")  # 1 + 0.10% of 10,000
    assert FeeModel().fee(D(10000)) == D("0.00")
    # 2.5 bps of 1,234.56 = 0.30864 -> 0.31 (half-up to the cent)
    assert FeeModel(rate_bps=D("2.5")).fee(D("1234.56")) == D("0.31")
    with pytest.raises(ValueError):
        FeeModel(fixed=D(-1))
    with pytest.raises(ValueError):
        FeeModel(quantum=D(0))


def test_slippage_moves_price_against_the_trader_and_rounds_adversely() -> None:
    assert apply_slippage(D(100), Side.BUY, D(10), TICK) == D("100.10")
    assert apply_slippage(D(100), Side.SELL, D(10), TICK) == D("99.90")
    # 33.33 * 1.0001 = 33.333333 -> buy rounds UP; 33.33 * 0.9999 = 33.326667 -> sell DOWN
    assert apply_slippage(D("33.33"), Side.BUY, D(1), TICK) == D("33.34")
    assert apply_slippage(D("33.33"), Side.SELL, D(1), TICK) == D("33.32")
    assert apply_slippage(D("12.34"), Side.BUY, D(0), TICK) == D("12.34")
    # a sell can never be pushed to zero
    assert apply_slippage(D("0.01"), Side.SELL, D(9999), TICK) == D("0.01")


def test_slippage_models() -> None:
    assert NoSlippage().bps(D(1), D(1)) == 0
    assert FixedBpsSlippage(D(7)).bps(D(500), D(1000)) == D(7)
    model = VolumeParticipationSlippage(base_bps=D(1), impact_bps=D(100))
    assert model.bps(D(500), D(1000)) == D(51)  # 1 + 100 * 0.5
    assert model.bps(D(5000), D(1000)) == D(101)  # participation capped at 1
    assert model.bps(D(5), D(0)) == D(101)  # no volume: worst case
    with pytest.raises(ValueError):
        FixedBpsSlippage(D(-1))
    with pytest.raises(ValueError):
        VolumeParticipationSlippage(D(1), D(-1))


# -- matching ---------------------------------------------------------------------------


def test_market_order_fills_at_next_bar_open_never_on_its_own_bar() -> None:
    ex = SimulatedExecution()
    ex.submit(order(3, Side.BUY, 10))
    assert ex.on_bar(bar(2, 50, 60, 40, 55)) == []  # earlier bar
    assert ex.on_bar(bar(3, 50, 60, 40, 55)) == []  # the decision bar itself
    fills = ex.on_bar(bar(4, 70, 80, 60, 75))
    assert len(fills) == 1
    f = fills[0]
    assert (f.price, f.quantity, f.side, f.order_id, f.fill_id) == (D(70), D(10), Side.BUY, 1, 1)
    assert f.reference_price == D(70) and f.ts == bar(4, 70, 80, 60, 75).ts
    assert ex.open_orders() == ()


def test_orders_only_match_their_own_symbol() -> None:
    ex = SimulatedExecution()
    ex.submit(order(0, Side.BUY, 1, symbol="A"))
    assert ex.on_bar(flat_bar(1, 10, symbol="B")) == []
    assert len(ex.open_orders("A")) == 1 and ex.open_orders("B") == ()
    assert len(ex.on_bar(flat_bar(1, 10, symbol="A"))) == 1


@pytest.mark.parametrize(
    ("side", "limit", "ohlc", "expected"),
    [
        (Side.BUY, "100", (105, 106, 99, 104), "100"),  # traded down through the limit
        (Side.BUY, "100", (105, 106, 100, 104), "100"),  # low exactly touches
        (Side.BUY, "100", (97, 99, 96, 98), "97"),  # gapped below: fill at the open
        (Side.BUY, "100", (105, 106, 101, 104), None),  # never reached
        (Side.SELL, "100", (95, 101, 94, 96), "100"),
        (Side.SELL, "100", (103, 104, 102, 103), "103"),  # gapped above: fill at the open
        (Side.SELL, "100", (95, 99, 94, 96), None),
    ],
)
def test_limit_orders(
    side: Side, limit: str, ohlc: tuple[int, int, int, int], expected: str | None
) -> None:
    # Heavy slippage is configured to show that limit fills ignore it.
    ex = SimulatedExecution(slippage=FixedBpsSlippage(D(500)))
    ex.submit(order(0, side, 5, OrderType.LIMIT, limit=limit))
    fills = ex.on_bar(bar(1, *ohlc))
    if expected is None:
        assert fills == []
        assert len(ex.open_orders()) == 1
    else:
        assert [f.price for f in fills] == [D(expected)]


@pytest.mark.parametrize(
    ("side", "stop", "ohlc", "expected"),
    [
        (Side.BUY, "100", (95, 101, 94, 99), "100"),  # traded up through the stop
        (Side.BUY, "100", (104, 105, 103, 104), "104"),  # gapped over: worse fill at the open
        (Side.BUY, "100", (95, 99, 94, 98), None),
        (Side.SELL, "100", (105, 106, 99, 104), "100"),
        (Side.SELL, "100", (96, 97, 95, 96), "96"),  # gapped under: worse fill at the open
        (Side.SELL, "100", (105, 106, 101, 104), None),
    ],
)
def test_stop_orders(
    side: Side, stop: str, ohlc: tuple[int, int, int, int], expected: str | None
) -> None:
    ex = SimulatedExecution()
    ex.submit(order(0, side, 5, OrderType.STOP, stop=stop))
    fills = ex.on_bar(bar(1, *ohlc))
    assert [f.price for f in fills] == ([] if expected is None else [D(expected)])


def test_slippage_and_fees_apply_to_market_and_stop_fills() -> None:
    ex = SimulatedExecution(
        fees=FeeModel(fixed=D(1), rate_bps=D(10)), slippage=FixedBpsSlippage(D(10))
    )
    ex.submit(order(0, Side.BUY, 100, order_id=1))
    ex.submit(order(0, Side.SELL, 100, OrderType.STOP, order_id=2, stop="190"))
    buy, sell = ex.on_bar(bar(1, 200, 201, 180, 185))
    # buy: 200 * 1.001 = 200.20; fee = 1 + 0.1% * 20,020 = 21.02
    assert (buy.reference_price, buy.price, buy.fee) == (D(200), D("200.20"), D("21.02"))
    # stop sell: 190 * 0.999 = 189.81; fee = 1 + 0.1% * 18,981 = 19.981 -> 19.98
    assert (sell.reference_price, sell.price, sell.fee) == (D(190), D("189.81"), D("19.98"))


def test_partial_fills_against_bar_volume() -> None:
    ex = SimulatedExecution(max_participation=D("0.1"))
    ex.submit(order(0, Side.BUY, 250))
    quantities = []
    for i, open_ in enumerate((10, 11, 12, 13), start=1):
        fills = ex.on_bar(bar(i, open_, open_ + 1, open_ - 1, open_, 1000))
        quantities.append([(f.quantity, f.price) for f in fills])
    # 10% of 1,000 = 100 per bar: 100 @ 10, 100 @ 11, the last 50 @ 12, then nothing left.
    assert quantities == [[(D(100), D(10))], [(D(100), D(11))], [(D(50), D(12))], []]


def test_bar_volume_is_shared_first_come_first_served() -> None:
    ex = SimulatedExecution(max_participation=D("0.5"))
    ex.submit(order(0, Side.BUY, 30, order_id=1))
    ex.submit(order(0, Side.BUY, 30, order_id=2))
    fills = ex.on_bar(flat_bar(1, 10, 100))  # 50 available
    assert [(f.order_id, f.quantity) for f in fills] == [(1, D(30)), (2, D(20))]
    (remaining,) = ex.open_orders()
    assert remaining.order.order_id == 2 and remaining.remaining == D(10)
    assert remaining.signed_remaining == D(10)
    assert ex.on_bar(flat_bar(2, 10, 0)) == []  # no volume, no fill
    assert [f.quantity for f in ex.on_bar(flat_bar(3, 10, 100))] == [D(10)]


def test_participation_cap_respects_quantity_step() -> None:
    ex = SimulatedExecution(max_participation=D("0.1"), qty_step=D("0.5"))
    ex.submit(order(0, Side.BUY, 10))
    # 10% of 37 = 3.7 -> floored to 3.5
    assert [f.quantity for f in ex.on_bar(flat_bar(1, 10, 37))] == [D("3.5")]


def test_triggered_stop_keeps_filling_at_the_open() -> None:
    ex = SimulatedExecution(max_participation=D("0.1"))
    ex.submit(order(0, Side.SELL, 150, OrderType.STOP, stop="100"))
    first = ex.on_bar(bar(1, 105, 106, 99, 104, 1000))
    assert [(f.quantity, f.price) for f in first] == [(D(100), D(100))]
    # Next bar never trades at 100 again, but the stop already triggered: market at open.
    second = ex.on_bar(bar(2, 108, 109, 107, 108, 1000))
    assert [(f.quantity, f.price) for f in second] == [(D(50), D(108))]


def test_volume_participation_slippage_uses_fill_size() -> None:
    ex = SimulatedExecution(slippage=VolumeParticipationSlippage(D(0), D(100)))
    ex.submit(order(0, Side.BUY, 500))
    (f,) = ex.on_bar(flat_bar(1, 100, 1000))
    assert f.price == D("100.50")  # 500 / 1000 * 100 bps = 50 bps


def test_cancel_and_duplicate_ids() -> None:
    ex = SimulatedExecution()
    ex.submit(order(0, Side.BUY, 1, order_id=7))
    with pytest.raises(ValueError, match="duplicate"):
        ex.submit(order(0, Side.BUY, 1, order_id=7))
    assert ex.cancel(7) is True
    assert ex.cancel(7) is False
    assert ex.on_bar(flat_bar(1, 10)) == []


def test_tick_matching_never_improves_a_resting_limit() -> None:
    ex = SimulatedExecution()
    ex.submit(order(0, Side.BUY, 5, OrderType.LIMIT, order_id=1, limit="100"))
    ex.submit(order(0, Side.BUY, 5, OrderType.STOP, order_id=2, stop="101"))
    ex.submit(order(0, Side.SELL, 5, order_id=3))
    assert ex.on_trade(tick(0, 99)) == []  # same timestamp as the orders
    fills = ex.on_trade(tick(1, "100.50"))
    assert [(f.order_id, f.price) for f in fills] == [(3, D("100.50"))]
    fills = ex.on_trade(tick(2, "99.00"))  # trades through the bid: we get our limit, not 99
    assert [(f.order_id, f.price) for f in fills] == [(1, D(100))]
    fills = ex.on_trade(tick(3, "102.00"))  # stop triggered at the print
    assert [(f.order_id, f.price) for f in fills] == [(2, D(102))]


def test_constructor_validation() -> None:
    with pytest.raises(ValueError):
        SimulatedExecution(max_participation=Decimal(0))
    with pytest.raises(ValueError):
        SimulatedExecution(max_participation=Decimal("1.5"))
    with pytest.raises(ValueError):
        SimulatedExecution(price_tick=Decimal(0))
    with pytest.raises(ValueError, match="price tick for A"):
        SimulatedExecution(price_ticks={"A": Decimal("-0.01")})
    with pytest.raises(ValueError):
        SimulatedExecution(qty_step=Decimal(0))


# -- price precision ---------------------------------------------------------------------


def test_sub_cent_instrument_fills_at_its_own_price_by_default() -> None:
    # Regression: v0.1.0 rounded every market fill to 0.01, so this filled at 0.01.
    ex = SimulatedExecution()
    ex.submit(order(0, Side.SELL, 1000, order_id=1))
    ex.submit(order(0, Side.BUY, 1000, order_id=2))
    fills = ex.on_bar(bar(1, "0.00001234", "0.00001300", "0.00001200", "0.00001250", 10**9))
    assert [f.price for f in fills] == [D("0.00001234"), D("0.00001234")]
    assert all(f.reference_price == f.price for f in fills)


def test_zero_slippage_never_moves_a_fill_even_with_a_tick() -> None:
    # Regression: v0.1.0 filled a 0.4567 buy at 0.46 with no slippage configured.
    for ex in (SimulatedExecution(), SimulatedExecution(price_tick=TICK)):
        ex.submit(order(0, Side.BUY, 1, order_id=1))
        ex.submit(order(0, Side.SELL, 1, OrderType.STOP, order_id=2, stop="0.4555"))
        fills = ex.on_bar(bar(1, "0.4567", "0.46", "0.45", "0.455", 10**6))
        assert [f.price for f in fills] == [D("0.4567"), D("0.4555")]


def test_slippage_without_a_tick_is_exact() -> None:
    assert apply_slippage(D("0.4567"), Side.BUY, D(2)) == D("0.45679134")
    assert apply_slippage(D("0.4567"), Side.SELL, D(2)) == D("0.45660866")
    assert apply_slippage(D("0.00001234"), Side.BUY, D(10)) == D("0.00001235234")
    assert apply_slippage(D("12.34"), Side.SELL, D(0)) == D("12.34")
    with pytest.raises(ValueError, match="or below zero"):
        apply_slippage(D(1), Side.SELL, D(10000))


def test_price_tick_per_symbol_overrides_the_default() -> None:
    ex = SimulatedExecution(
        slippage=FixedBpsSlippage(D(1)),
        price_tick=TICK,
        price_ticks={"FINE": D("0.0001"), "COARSE": D("0.5")},
    )
    assert ex.price_tick_for("FINE") == D("0.0001")
    assert ex.price_tick_for("OTHER") == TICK
    for i, symbol in enumerate(("FINE", "COARSE", "OTHER"), start=1):
        ex.submit(order(0, Side.BUY, 1, order_id=i, symbol=symbol))
    prices = {}
    for symbol in ("FINE", "COARSE", "OTHER"):
        (f,) = ex.on_bar(flat_bar(1, "33.33", symbol=symbol))
        prices[symbol] = f.price
    # 33.33 * 1.0001 = 33.333333, rounded up to each symbol's tick
    assert prices == {"FINE": D("33.3334"), "COARSE": D("33.5"), "OTHER": D("33.34")}
    assert SimulatedExecution(price_ticks={"FINE": D("0.0001")}).price_tick_for("X") is None
