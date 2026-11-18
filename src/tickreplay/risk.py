"""Pre-trade risk checks, position sizing and the daily loss halt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_FLOOR, Decimal

from tickreplay.events import ZERO, Order


@dataclass(frozen=True, slots=True)
class RiskConfig:
    """Risk limits. ``None`` disables a limit.

    * ``max_position`` - largest absolute position per symbol, in units.
    * ``max_gross_exposure`` - cap on total absolute position value, as a multiple of
      equity (``1`` means no leverage).
    * ``risk_per_trade`` - fraction of equity a signal with a stop price may lose
      between its reference price and that stop; used to size the order.
    * ``daily_loss_limit`` - fraction of the day's starting equity; once equity falls by
      this much, exposure-increasing orders are refused for the rest of that UTC day.
    * ``flatten_on_halt`` - also close every position when the halt triggers.
    """

    max_position: Decimal | None = None
    max_gross_exposure: Decimal | None = None
    risk_per_trade: Decimal | None = None
    daily_loss_limit: Decimal | None = None
    flatten_on_halt: bool = False

    def __post_init__(self) -> None:
        for name in ("max_position", "max_gross_exposure", "risk_per_trade", "daily_loss_limit"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when set")
        for name in ("risk_per_trade", "daily_loss_limit"):
            value = getattr(self, name)
            if value is not None and value >= 1:
                raise ValueError(f"{name} is a fraction and must be below 1")


def floor_to_step(quantity: Decimal, step: Decimal) -> Decimal:
    """Round ``quantity`` down to a multiple of ``step`` (never below zero)."""
    if quantity <= 0:
        return ZERO
    return (quantity / step).to_integral_value(rounding=ROUND_FLOOR) * step


def size_for_risk(
    equity: Decimal, entry: Decimal, stop: Decimal, risk_fraction: Decimal, qty_step: Decimal
) -> Decimal:
    """Quantity such that a move from ``entry`` to ``stop`` loses ``risk_fraction`` of equity.

    Returns zero when entry and stop coincide or equity is not positive.
    """
    distance = abs(entry - stop)
    if distance == 0 or equity <= 0:
        return ZERO
    return floor_to_step(equity * risk_fraction / distance, qty_step)


class RiskManager:
    """Stateful pre-trade checks. The engine calls :meth:`update` on every valuation."""

    def __init__(self, config: RiskConfig) -> None:
        self.config = config
        self._day: date | None = None
        self._day_start_equity: Decimal | None = None
        self._last_equity: Decimal | None = None
        self._halted = False

    @property
    def halted(self) -> bool:
        """True while the daily loss limit is in force."""
        return self._halted

    @property
    def day_start_equity(self) -> Decimal | None:
        """Equity the current day's loss is measured from."""
        return self._day_start_equity

    def update(self, ts: datetime, equity: Decimal) -> bool:
        """Record a valuation; return ``True`` if this one newly triggers the halt.

        A new UTC calendar day resets the halt and starts measuring from the last
        equity seen on the previous day (or from this value on the very first call).
        """
        day = ts.astimezone(timezone.utc).date()
        if day != self._day:
            self._day = day
            self._day_start_equity = self._last_equity if self._last_equity is not None else equity
            self._halted = False
        self._last_equity = equity
        limit = self.config.daily_loss_limit
        start = self._day_start_equity
        if limit is None or self._halted or start is None or start <= 0:
            return False
        if equity <= start * (1 - limit):
            self._halted = True
            return True
        return False

    def review(
        self,
        order: Order,
        *,
        position: Decimal,
        working: Decimal,
        reference_price: Decimal,
        equity: Decimal,
        other_gross_exposure: Decimal,
    ) -> str | None:
        """Return a rejection reason, or ``None`` if the order may proceed.

        ``position`` is the signed holding, ``working`` the signed unfilled quantity of
        orders already resting for the symbol. Orders that do not increase the projected
        absolute position are always allowed, so exits work during a halt.
        """
        current = position + working
        projected = current + order.signed_quantity
        if abs(projected) <= abs(current):
            return None
        if self._halted:
            return "daily loss limit reached: trading halted for the day"
        limit = self.config.max_position
        if limit is not None and abs(projected) > limit:
            return f"max position {limit} exceeded (projected {abs(projected)})"
        cap = self.config.max_gross_exposure
        if cap is not None:
            gross = other_gross_exposure + abs(projected) * reference_price
            if gross > cap * equity:
                return f"max gross exposure {cap}x equity exceeded (projected {gross})"
        return None

    def cap_quantity(
        self,
        quantity: Decimal,
        *,
        reference_price: Decimal,
        equity: Decimal,
        other_gross_exposure: Decimal,
        qty_step: Decimal,
    ) -> Decimal:
        """Shrink a fresh entry of ``quantity`` units until it satisfies the limits."""
        if self.config.max_position is not None:
            quantity = min(quantity, floor_to_step(self.config.max_position, qty_step))
        cap = self.config.max_gross_exposure
        if cap is not None:
            room = cap * equity - other_gross_exposure
            quantity = min(quantity, floor_to_step(room / reference_price, qty_step))
        return max(quantity, ZERO)
