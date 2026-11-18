"""Performance statistics computed from an equity curve and completed round trips.

Accounting stays in :class:`~decimal.Decimal`; the statistics here are descriptive and
use floats. Definitions (``e`` is the equity series, ``r[i] = e[i] / e[i-1] - 1``, and
``P`` is the number of periods per year):

* total return   ``e[-1] / e[0] - 1``
* CAGR           ``(e[-1] / e[0]) ** (1 / years) - 1`` with ``years`` = elapsed / 365.25 d
* volatility     ``stdev(r, ddof=1) * sqrt(P)``
* Sharpe         ``(mean(r) - rf / P) / stdev(r, ddof=1) * sqrt(P)``
* Sortino        ``(mean(r) - rf / P) / sqrt(mean(min(r - rf / P, 0) ** 2)) * sqrt(P)``
* max drawdown   largest ``1 - e[i] / max(e[:i+1])``
* drawdown duration  longest span from a peak until equity is back at or above it
  (or until the series ends, if it never recovers)
* win rate       share of round trips with positive net P&L
* profit factor  sum of winning net P&L / absolute sum of losing net P&L
* exposure       share of equity samples at which any position was held
* turnover       total traded notional / mean equity

A ratio whose denominator is zero is reported as ``None`` (or ``inf`` for a profit
factor with wins and no losses) rather than as a made-up number.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from tickreplay.portfolio import EquityPoint, RoundTrip

SECONDS_PER_YEAR = 365.25 * 24 * 3600


@dataclass(frozen=True, slots=True)
class Metrics:
    """Summary statistics for one run. ``None`` means "undefined for this series"."""

    periods: int
    periods_per_year: float | None
    total_return: float
    cagr: float | None
    volatility: float | None
    sharpe: float | None
    sortino: float | None
    max_drawdown: float
    max_drawdown_duration: timedelta
    max_drawdown_periods: int
    trades: int
    win_rate: float | None
    profit_factor: float | None
    exposure: float
    turnover: float | None


def infer_periods_per_year(points: Sequence[EquityPoint]) -> float | None:
    """Periods per year implied by the median spacing of the samples (365.25-day year)."""
    if len(points) < 2:
        return None
    gaps = [(b.ts - a.ts).total_seconds() for a, b in itertools.pairwise(points)]
    median = statistics.median(gaps)
    if median <= 0:
        return None
    return SECONDS_PER_YEAR / median


def drawdown_series(equity: Sequence[float]) -> list[float]:
    """Drawdown at each sample as a non-negative fraction of the running peak."""
    out: list[float] = []
    peak = -math.inf
    for value in equity:
        peak = max(peak, value)
        out.append(1.0 - value / peak if peak > 0 else 0.0)
    return out


def _max_drawdown_duration(points: Sequence[EquityPoint]) -> tuple[timedelta, int]:
    longest = timedelta(0)
    longest_periods = 0
    if not points:
        return longest, longest_periods
    peak = points[0].equity
    peak_index = 0
    for i, point in enumerate(points):
        ended = point.equity >= peak
        if (ended or i == len(points) - 1) and i - peak_index > 0:
            # Only spans that actually dipped count: consecutive new highs have length 1
            # but contain no drawdown.
            dipped = any(p.equity < peak for p in points[peak_index + 1 : i + 1])
            span = point.ts - points[peak_index].ts
            if dipped and span > longest:
                longest = span
                longest_periods = i - peak_index
        if ended:
            peak = point.equity
            peak_index = i
    return longest, longest_periods


def compute_metrics(
    equity_curve: Sequence[EquityPoint],
    round_trips: Sequence[RoundTrip] = (),
    *,
    traded_notional: Decimal = Decimal(0),
    periods_per_year: float | None = None,
    risk_free_rate: float = 0.0,
) -> Metrics:
    """Compute :class:`Metrics`. ``periods_per_year`` is inferred when not given."""
    if not equity_curve:
        raise ValueError("equity curve is empty")
    equity = [float(p.equity) for p in equity_curve]
    ppy = periods_per_year if periods_per_year is not None else infer_periods_per_year(equity_curve)
    first, last = equity[0], equity[-1]
    total_return = last / first - 1.0

    years = (equity_curve[-1].ts - equity_curve[0].ts).total_seconds() / SECONDS_PER_YEAR
    cagr: float | None
    if years <= 0:
        cagr = None
    elif last <= 0:
        cagr = -1.0
    else:
        cagr = (last / first) ** (1.0 / years) - 1.0

    returns = [b / a - 1.0 for a, b in itertools.pairwise(equity)]
    volatility = sharpe = sortino = None
    if len(returns) >= 2 and ppy is not None:
        per_period_rf = risk_free_rate / ppy
        excess = statistics.fmean(returns) - per_period_rf
        stdev = statistics.stdev(returns)
        volatility = stdev * math.sqrt(ppy)
        if stdev > 0:
            sharpe = excess / stdev * math.sqrt(ppy)
        downside = math.sqrt(statistics.fmean([min(r - per_period_rf, 0.0) ** 2 for r in returns]))
        if downside > 0:
            sortino = excess / downside * math.sqrt(ppy)

    duration, duration_periods = _max_drawdown_duration(equity_curve)

    wins = [float(t.net_pnl) for t in round_trips if t.net_pnl > 0]
    losses = [float(t.net_pnl) for t in round_trips if t.net_pnl < 0]
    win_rate = len(wins) / len(round_trips) if round_trips else None
    profit_factor: float | None
    if losses:
        profit_factor = sum(wins) / abs(sum(losses))
    elif wins:
        profit_factor = math.inf
    else:
        profit_factor = None

    mean_equity = statistics.fmean(equity)
    return Metrics(
        periods=len(equity_curve),
        periods_per_year=ppy,
        total_return=total_return,
        cagr=cagr,
        volatility=volatility,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max(drawdown_series(equity)),
        max_drawdown_duration=duration,
        max_drawdown_periods=duration_periods,
        trades=len(round_trips),
        win_rate=win_rate,
        profit_factor=profit_factor,
        exposure=sum(1 for p in equity_curve if p.gross_exposure > 0) / len(equity_curve),
        turnover=float(traded_notional) / mean_equity if mean_equity > 0 else None,
    )
