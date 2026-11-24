"""Transaction cost models: fees and slippage."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Protocol

from tickreplay.events import ZERO, Side

_BPS = Decimal(10000)
_BPS_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class FeeModel:
    """Fixed fee per fill plus a proportional fee on traded notional.

    ``rate_bps`` is in basis points of notional (``10`` means 0.10 %). The total is
    rounded half-up to ``quantum`` (one cent by default).
    """

    fixed: Decimal = ZERO
    rate_bps: Decimal = ZERO
    quantum: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        if self.fixed < 0 or self.rate_bps < 0:
            raise ValueError("fees must be non-negative")
        if self.quantum <= 0:
            raise ValueError("fee quantum must be positive")

    def fee(self, notional: Decimal) -> Decimal:
        """Fee charged on a fill of the given unsigned notional."""
        raw = self.fixed + notional * self.rate_bps / _BPS
        return raw.quantize(self.quantum, rounding=ROUND_HALF_UP)


class SlippageModel(Protocol):
    """Returns adverse slippage, in basis points, for one fill."""

    def bps(self, quantity: Decimal, market_volume: Decimal) -> Decimal:
        """Slippage in bps for filling ``quantity`` against ``market_volume``."""
        ...


@dataclass(frozen=True, slots=True)
class NoSlippage:
    """Fills at the reference price."""

    def bps(self, quantity: Decimal, market_volume: Decimal) -> Decimal:
        return ZERO


@dataclass(frozen=True, slots=True)
class FixedBpsSlippage:
    """A constant number of basis points against the trader on every fill."""

    value: Decimal

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("slippage must be non-negative")

    def bps(self, quantity: Decimal, market_volume: Decimal) -> Decimal:
        return self.value


@dataclass(frozen=True, slots=True)
class VolumeParticipationSlippage:
    """Linear impact: ``base_bps + impact_bps * (quantity / market_volume)``.

    The participation ratio is capped at 1. A bar with zero volume is treated as full
    participation. This is a simple illustrative model, not a calibrated one.
    """

    base_bps: Decimal
    impact_bps: Decimal

    def __post_init__(self) -> None:
        if self.base_bps < 0 or self.impact_bps < 0:
            raise ValueError("slippage must be non-negative")

    def bps(self, quantity: Decimal, market_volume: Decimal) -> Decimal:
        ratio = Decimal(1) if market_volume <= 0 else min(Decimal(1), quantity / market_volume)
        return (self.base_bps + self.impact_bps * ratio).quantize(
            _BPS_QUANTUM, rounding=ROUND_HALF_UP
        )


def apply_slippage(
    price: Decimal, side: Side, bps: Decimal, tick: Decimal | None = None
) -> Decimal:
    """Move ``price`` against the trader by ``bps``, rounding to ``tick`` if one is given.

    * Zero slippage returns ``price`` unchanged, with or without a tick: a fill with no
      slippage happens at the reference price taken from the data.
    * Without a tick the result is exact decimal arithmetic (no rounding at all), so the
      fill stays correct for instruments of any price scale.
    * With a tick, buys are rounded up and sells rounded down, so rounding never favours
      the trader, and a sell is floored at one tick so it can never fill at or below zero.
    """
    if bps == 0:
        return price
    raw = price * (_BPS + bps if side is Side.BUY else _BPS - bps) / _BPS
    if tick is None:
        if raw <= 0:
            raise ValueError(f"{bps} bps of slippage would move a sell fill to or below zero")
        return raw
    if side is Side.BUY:
        return (raw / tick).to_integral_value(rounding=ROUND_CEILING) * tick
    rounded = (raw / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    return max(rounded, tick)
