from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from helpers import D, order, ts
from tickreplay import Order, RiskConfig, RiskManager, Side, size_for_risk
from tickreplay.risk import floor_to_step


def review(
    rm: RiskManager,
    o: Order,
    *,
    position: str | int = 0,
    working: str | int = 0,
    price: str | int = 100,
    equity: str | int = 100000,
    other: str | int = 0,
) -> str | None:
    return rm.review(
        o,
        position=D(position),
        working=D(working),
        reference_price=D(price),
        equity=D(equity),
        other_gross_exposure=D(other),
    )


def test_size_for_risk_hand_values() -> None:
    # Risk 1% of 100,000 = 1,000; entry 100, stop 95 -> 5 per unit -> 200 units.
    assert size_for_risk(D(100000), D(100), D(95), D("0.01"), D(1)) == D(200)
    # Short side: the distance is what matters.
    assert size_for_risk(D(100000), D(95), D(100), D("0.01"), D(1)) == D(200)
    # 1,000 / 3 = 333.33 -> floored to the step.
    assert size_for_risk(D(100000), D(100), D(97), D("0.01"), D(1)) == D(333)
    assert size_for_risk(D(100000), D(100), D(97), D("0.01"), D("0.1")) == D("333.3")
    assert size_for_risk(D(100000), D(100), D(100), D("0.01"), D(1)) == 0
    assert size_for_risk(D(0), D(100), D(95), D("0.01"), D(1)) == 0
    assert floor_to_step(D("-3"), D(1)) == 0


def test_config_validation() -> None:
    with pytest.raises(ValueError, match="max_position"):
        RiskConfig(max_position=D(0))
    with pytest.raises(ValueError, match="fraction"):
        RiskConfig(risk_per_trade=D(1))
    with pytest.raises(ValueError, match="fraction"):
        RiskConfig(daily_loss_limit=D(2))


def test_no_limits_means_everything_passes() -> None:
    rm = RiskManager(RiskConfig())
    assert review(rm, order(0, Side.BUY, 10**9)) is None
    assert rm.update(ts(0), D(1)) is False and not rm.halted


def test_max_position_counts_holdings_and_working_orders() -> None:
    rm = RiskManager(RiskConfig(max_position=D(100)))
    assert review(rm, order(0, Side.BUY, 100)) is None
    assert "max position" in (review(rm, order(0, Side.BUY, 101)) or "")
    assert review(rm, order(0, Side.BUY, 40), position=60) is None
    assert review(rm, order(0, Side.BUY, 41), position=60) is not None
    assert review(rm, order(0, Side.BUY, 20), position=60, working=30) is not None
    assert review(rm, order(0, Side.SELL, 100)) is None  # short of 100 is within the limit
    assert review(rm, order(0, Side.SELL, 101)) is not None
    # Reducing an over-limit position is always allowed.
    assert review(rm, order(0, Side.SELL, 50), position=500) is None
    # Flipping from +60 to -100 is fine; to -101 is not.
    assert review(rm, order(0, Side.SELL, 160), position=60) is None
    assert review(rm, order(0, Side.SELL, 161), position=60) is not None


def test_max_gross_exposure_uses_equity_and_other_positions() -> None:
    rm = RiskManager(RiskConfig(max_gross_exposure=D(1)))
    # 1,000 units at 100 = 100,000 = exactly 1x equity -> allowed; one more is not.
    assert review(rm, order(0, Side.BUY, 1000)) is None
    assert "gross exposure" in (review(rm, order(0, Side.BUY, 1001)) or "")
    assert review(rm, order(0, Side.BUY, 600), other=40000) is None
    assert review(rm, order(0, Side.BUY, 601), other=40000) is not None
    assert review(rm, order(0, Side.SELL, 1001)) is not None  # shorts count as exposure too
    half = RiskManager(RiskConfig(max_gross_exposure=D("0.5")))
    assert review(half, order(0, Side.BUY, 500)) is None
    assert review(half, order(0, Side.BUY, 501)) is not None


def test_cap_quantity() -> None:
    rm = RiskManager(RiskConfig(max_position=D(300), max_gross_exposure=D(1)))
    kwargs = {"reference_price": D(100), "equity": D(100000), "qty_step": D(1)}
    assert rm.cap_quantity(D(950), other_gross_exposure=D(0), **kwargs) == D(300)
    assert rm.cap_quantity(D(250), other_gross_exposure=D(0), **kwargs) == D(250)
    assert rm.cap_quantity(D(250), other_gross_exposure=D(90000), **kwargs) == D(100)
    assert rm.cap_quantity(D(250), other_gross_exposure=D(200000), **kwargs) == 0
    free = RiskManager(RiskConfig())
    assert free.cap_quantity(D(950), other_gross_exposure=D(0), **kwargs) == D(950)


def test_daily_loss_limit_halts_then_resets_next_day() -> None:
    rm = RiskManager(RiskConfig(daily_loss_limit=D("0.05")))
    buy, sell = order(0, Side.BUY, 10), order(0, Side.SELL, 10)

    assert rm.update(ts(0), D(100000)) is False
    assert rm.day_start_equity == D(100000)
    assert rm.update(ts(1), D("95000.01")) is False  # 4.99999% down: still trading
    assert not rm.halted and review(rm, buy) is None

    assert rm.update(ts(2), D(95000)) is True  # exactly 5% down trips the limit
    assert rm.halted
    assert rm.update(ts(3), D(90000)) is False  # already halted: not "newly" halted
    assert "halted" in (review(rm, buy) or "")
    assert "halted" in (review(rm, sell) or "")  # opening a short is new exposure too
    assert review(rm, sell, position=10) is None  # exits stay possible
    assert review(rm, order(0, Side.BUY, 10), position=-10) is None

    # A recovery later the same day does not lift the halt.
    assert rm.update(ts(4), D(120000)) is False and rm.halted

    # Next UTC day: reset, measured from the last equity of the previous day.
    next_day = ts(0) + timedelta(days=1)
    assert rm.update(next_day, D(118000)) is False
    assert not rm.halted and rm.day_start_equity == D(120000)
    assert review(rm, buy) is None
    assert rm.update(next_day + timedelta(hours=1), D(114000)) is True  # 5% of 120,000


def test_daily_loss_limit_disabled_never_halts() -> None:
    rm = RiskManager(RiskConfig())
    rm.update(ts(0), Decimal(100))
    assert rm.update(ts(1), Decimal(1)) is False and not rm.halted
