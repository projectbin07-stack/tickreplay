"""Every statistic is checked against values worked out by hand on tiny series."""

from __future__ import annotations

import math
from datetime import timedelta

import pytest

from helpers import D, ts
from tickreplay import Direction, EquityPoint, RoundTrip, compute_metrics
from tickreplay.metrics import drawdown_series, infer_periods_per_year

DAY = timedelta(days=1)


def curve(values: list[str | int], exposure: list[int] | None = None) -> list[EquityPoint]:
    exposure = exposure or [0] * len(values)
    return [
        EquityPoint(ts(i, DAY), D(v), D(v), D(e))
        for i, (v, e) in enumerate(zip(values, exposure, strict=False))
    ]


def trip(gross: str | int, fees: str | int = 0) -> RoundTrip:
    return RoundTrip("X", Direction.LONG, ts(0), ts(1), D(1), D(gross), D(fees))


def test_return_volatility_sharpe_sortino() -> None:
    # Equity 100 -> 110 -> 99 -> 108.9, i.e. returns +10%, -10%, +10%.
    #   mean            = 0.1 / 3
    #   sample variance = (2 * (1/15)^2 + (2/15)^2) / 2 = 1/75     -> stdev = 1 / sqrt(75)
    #   downside dev    = sqrt(0.1^2 / 3)                           = 0.1 / sqrt(3)
    # With 4 periods per year (sqrt = 2):
    #   volatility = 2 / sqrt(75)             = 0.230940
    #   Sharpe     = (0.1/3) * sqrt(75) * 2   = 1 / sqrt(3) = 0.577350
    #   Sortino    = (0.1/3) / (0.1/sqrt(3)) * 2 = 2 / sqrt(3) = 1.154701
    m = compute_metrics(curve([100, 110, 99, "108.9"]), periods_per_year=4)
    assert m.periods == 4 and m.periods_per_year == 4
    assert m.total_return == pytest.approx(0.089)
    assert m.volatility == pytest.approx(0.230940, abs=1e-6)
    assert m.sharpe == pytest.approx(0.577350, abs=1e-6)
    assert m.sortino == pytest.approx(1.154701, abs=1e-6)
    assert m.volatility == pytest.approx(2 / math.sqrt(75))
    assert m.sharpe == pytest.approx(1 / math.sqrt(3))
    assert m.sortino == pytest.approx(2 / math.sqrt(3))


def test_risk_free_rate_is_subtracted_per_period() -> None:
    # rf 4%/yr over 4 periods = 1% per period. Excess mean = 0.1/3 - 0.01 = 0.023333.
    #   Sharpe  = 0.023333 * sqrt(75) * 2 = 0.404145
    #   Downside (vs 1%): only the -10% period, shortfall 0.11 -> sqrt(0.0121 / 3) = 0.063509
    #   Sortino = 0.023333 / 0.063509 * 2 = 0.734809
    m = compute_metrics(curve([100, 110, 99, "108.9"]), periods_per_year=4, risk_free_rate=0.04)
    assert m.sharpe == pytest.approx(0.404145, abs=1e-6)
    assert m.sortino == pytest.approx(0.734809, abs=1e-6)


def test_cagr_over_exactly_two_years() -> None:
    points = [
        EquityPoint(ts(0), D(100), D(100), D(0)),
        EquityPoint(ts(0) + timedelta(days=730.5), D(121), D(121), D(0)),
    ]
    m = compute_metrics(points)
    assert m.total_return == pytest.approx(0.21)
    assert m.cagr == pytest.approx(0.10)  # 1.21 ** (1/2) - 1
    # Two samples give one return: dispersion statistics are undefined, not zero.
    assert m.volatility is None and m.sharpe is None and m.sortino is None


def test_cagr_edge_cases() -> None:
    assert compute_metrics(curve([100])).cagr is None  # no elapsed time
    wiped = [
        EquityPoint(ts(0), D(100), D(100), D(0)),
        EquityPoint(ts(0) + timedelta(days=365.25), D(0), D(0), D(0)),
    ]
    assert compute_metrics(wiped).cagr == -1.0
    with pytest.raises(ValueError):
        compute_metrics([])


def test_max_drawdown_and_duration_with_recovery() -> None:
    # Peak 120 (day 1) -> trough 90 (day 2) = 25% -> back at 120 on day 4: 3 days under water.
    # Then peak 130 (day 5) -> 125 on the last day: 3.85%, 1 day and unrecovered.
    m = compute_metrics(curve([100, 120, 90, 100, 120, 130, 125]))
    assert m.max_drawdown == pytest.approx(0.25)
    assert m.max_drawdown_duration == timedelta(days=3)
    assert m.max_drawdown_periods == 3
    assert drawdown_series([100, 120, 90, 100, 120, 130, 125]) == pytest.approx(
        [0, 0, 0.25, 1 / 6, 0, 0, 5 / 130]
    )


def test_unrecovered_drawdown_runs_to_the_end_of_the_series() -> None:
    m = compute_metrics(curve([100, 110, 99, "108.9"]))
    assert m.max_drawdown == pytest.approx(0.10)  # 110 -> 99
    assert m.max_drawdown_duration == timedelta(days=2)  # day 1 to the final day 3
    assert m.max_drawdown_periods == 2


def test_monotonic_equity_has_no_drawdown() -> None:
    m = compute_metrics(curve([100, 101, 101, 105]))
    assert m.max_drawdown == 0.0
    assert m.max_drawdown_duration == timedelta(0) and m.max_drawdown_periods == 0
    assert m.sortino is None  # no downside periods: undefined rather than infinite


def test_flat_equity_has_undefined_ratios() -> None:
    m = compute_metrics(curve([100, 100, 100, 100]), periods_per_year=252)
    assert m.total_return == 0.0 and m.volatility == 0.0
    assert m.sharpe is None and m.sortino is None


def test_win_rate_and_profit_factor() -> None:
    # Net P&L: +30, -10, +20, -20 (gross -15 less 5 of fees).
    trips = [trip(30), trip(-10), trip(25, 5), trip(-15, 5)]
    m = compute_metrics(curve([100, 100]), trips)
    assert m.trades == 4
    assert m.win_rate == pytest.approx(0.5)
    assert m.profit_factor == pytest.approx(50 / 30)

    only_wins = compute_metrics(curve([100, 100]), [trip(5), trip(7)])
    assert only_wins.win_rate == 1.0 and only_wins.profit_factor == math.inf
    # A trade that nets exactly zero is neither a win nor a loss.
    scratch = compute_metrics(curve([100, 100]), [trip(5, 5)])
    assert scratch.win_rate == 0.0 and scratch.profit_factor is None
    none = compute_metrics(curve([100, 100]))
    assert none.trades == 0 and none.win_rate is None and none.profit_factor is None


def test_exposure_and_turnover() -> None:
    # In the market on 2 of 4 samples. Mean equity = (100 + 110 + 99 + 108.9) / 4 = 104.475;
    # traded notional 208.95 -> turnover exactly 2.
    points = curve([100, 110, 99, "108.9"], exposure=[0, 50, 50, 0])
    m = compute_metrics(points, traded_notional=D("208.95"))
    assert m.exposure == pytest.approx(0.5)
    assert m.turnover == pytest.approx(2.0)
    assert compute_metrics(points).turnover == 0.0


def test_periods_per_year_inference() -> None:
    assert infer_periods_per_year(curve([1, 1, 1])) == pytest.approx(365.25)
    hourly = [EquityPoint(ts(i), D(1), D(1), D(0)) for i in range(5)]
    assert infer_periods_per_year(hourly) == pytest.approx(8766.0)
    # The median spacing is used, so one long gap does not change the answer.
    gappy = [EquityPoint(ts(i), D(1), D(1), D(0)) for i in (0, 1, 2, 50, 51)]
    assert infer_periods_per_year(gappy) == pytest.approx(8766.0)
    assert infer_periods_per_year(curve([1])) is None
    same = [EquityPoint(ts(0), D(1), D(1), D(0))] * 3
    assert infer_periods_per_year(same) is None
    assert compute_metrics(curve([100, 110, 99])).periods_per_year == pytest.approx(365.25)
