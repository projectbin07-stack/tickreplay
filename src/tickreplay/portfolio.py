"""Portfolio accounting in exact decimal arithmetic.

Positions are tracked by signed quantity and total cost basis (``|quantity| x average
cost``). Tracking the basis rather than the average keeps the core identities exact:

* ``equity == cash + position_value``
* ``equity == initial_cash + realised_pnl + unrealised_pnl - fees_paid``

A partial close removes a pro-rata share of the cost basis. That share involves a
division and is rounded to 12 decimal places, but the rounding moves value between
"realised" and "remaining basis" only, so both identities still hold exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from tickreplay.events import ZERO, Direction, Fill

_Q = Decimal("0.000000000001")


@dataclass(slots=True)
class Position:
    """Holding in one symbol. ``quantity`` is signed; ``cost_basis`` is never negative."""

    symbol: str
    quantity: Decimal = ZERO
    cost_basis: Decimal = ZERO

    @property
    def average_cost(self) -> Decimal:
        """Average entry price of the open position (zero when flat)."""
        if self.quantity == 0:
            return ZERO
        return self.cost_basis / abs(self.quantity)


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """One sample of the equity curve."""

    ts: datetime
    equity: Decimal
    cash: Decimal
    gross_exposure: Decimal


@dataclass(frozen=True, slots=True)
class RoundTrip:
    """A completed flat-to-flat trade in one symbol.

    ``quantity`` is the total quantity entered. ``gross_pnl`` excludes fees.
    """

    symbol: str
    direction: Direction
    opened: datetime
    closed: datetime
    quantity: Decimal
    gross_pnl: Decimal
    fees: Decimal

    @property
    def net_pnl(self) -> Decimal:
        """Realised P&L after fees."""
        return self.gross_pnl - self.fees


@dataclass(slots=True)
class _OpenTrip:
    direction: Direction
    opened: datetime
    quantity: Decimal = ZERO
    gross_pnl: Decimal = ZERO
    fees: Decimal = ZERO


class Portfolio:
    """Cash, positions, P&L and the equity curve."""

    def __init__(self, initial_cash: Decimal) -> None:
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.realised_pnl = ZERO
        self.fees_paid = ZERO
        self.traded_notional = ZERO
        self.positions: dict[str, Position] = {}
        self.equity_curve: list[EquityPoint] = []
        self.round_trips: list[RoundTrip] = []
        self._marks: dict[str, Decimal] = {}
        self._trips: dict[str, _OpenTrip] = {}

    # -- queries ----------------------------------------------------------------------

    def quantity(self, symbol: str) -> Decimal:
        """Signed position in ``symbol`` (zero if never traded)."""
        position = self.positions.get(symbol)
        return position.quantity if position is not None else ZERO

    def mark_price(self, symbol: str) -> Decimal | None:
        """Last price the symbol was marked at, if any."""
        return self._marks.get(symbol)

    @property
    def position_value(self) -> Decimal:
        """Signed market value of all positions at the last marks."""
        return sum(
            (p.quantity * self._marks[s] for s, p in self.positions.items() if p.quantity != 0),
            ZERO,
        )

    @property
    def gross_exposure(self) -> Decimal:
        """Sum of absolute position values at the last marks."""
        return sum(
            (abs(p.quantity) * self._marks[s] for s, p in self.positions.items()),
            ZERO,
        )

    def gross_exposure_excluding(self, symbol: str) -> Decimal:
        """Gross exposure of every symbol except ``symbol``."""
        return sum(
            (abs(p.quantity) * self._marks[s] for s, p in self.positions.items() if s != symbol),
            ZERO,
        )

    @property
    def equity(self) -> Decimal:
        """Cash plus the market value of positions."""
        return self.cash + self.position_value

    @property
    def unrealised_pnl(self) -> Decimal:
        """Mark-to-market P&L of open positions, before any exit fees."""
        total = ZERO
        for symbol, position in self.positions.items():
            if position.quantity > 0:
                total += position.quantity * self._marks[symbol] - position.cost_basis
            elif position.quantity < 0:
                total += position.cost_basis + position.quantity * self._marks[symbol]
        return total

    # -- mutation ---------------------------------------------------------------------

    def mark(self, symbol: str, price: Decimal) -> None:
        """Update the valuation price for ``symbol``."""
        if price <= 0:
            raise ValueError("mark price must be positive")
        self._marks[symbol] = price

    def record(self, ts: datetime) -> EquityPoint:
        """Append (or, for a repeated timestamp, replace) an equity-curve sample."""
        point = EquityPoint(ts, self.equity, self.cash, self.gross_exposure)
        if self.equity_curve and self.equity_curve[-1].ts == ts:
            self.equity_curve[-1] = point
        else:
            self.equity_curve.append(point)
        return point

    def apply_fill(self, fill: Fill) -> Decimal:
        """Apply an execution and return the gross P&L it realised."""
        position = self.positions.setdefault(fill.symbol, Position(fill.symbol))
        self._marks.setdefault(fill.symbol, fill.price)
        sign = fill.side.sign
        self.cash -= sign * fill.notional + fill.fee
        self.fees_paid += fill.fee
        self.traded_notional += fill.notional

        if position.quantity == 0 or (position.quantity > 0) == (sign > 0):
            self._open(position, fill, fill.quantity, fill.fee)
            return ZERO

        held = abs(position.quantity)
        closing = min(fill.quantity, held)
        opening = fill.quantity - closing
        close_fee = fill.fee if opening == 0 else (fill.fee * closing / fill.quantity).quantize(_Q)
        if closing == held:
            cost_removed = position.cost_basis
        else:
            cost_removed = (position.cost_basis * closing / held).quantize(_Q)
        proceeds = closing * fill.price
        realised = proceeds - cost_removed if position.quantity > 0 else cost_removed - proceeds

        position.cost_basis -= cost_removed
        position.quantity += sign * closing
        self.realised_pnl += realised

        trip = self._trips[fill.symbol]
        trip.gross_pnl += realised
        trip.fees += close_fee
        if position.quantity == 0:
            del self._trips[fill.symbol]
            self.round_trips.append(
                RoundTrip(
                    symbol=fill.symbol,
                    direction=trip.direction,
                    opened=trip.opened,
                    closed=fill.ts,
                    quantity=trip.quantity,
                    gross_pnl=trip.gross_pnl,
                    fees=trip.fees,
                )
            )
        if opening > 0:
            self._open(position, fill, opening, fill.fee - close_fee)
        return realised

    def _open(self, position: Position, fill: Fill, quantity: Decimal, fee: Decimal) -> None:
        trip = self._trips.get(fill.symbol)
        if trip is None:
            direction = Direction.LONG if fill.side.sign > 0 else Direction.SHORT
            trip = self._trips[fill.symbol] = _OpenTrip(direction, fill.ts)
        trip.quantity += quantity
        trip.fees += fee
        position.quantity += fill.side.sign * quantity
        position.cost_basis += quantity * fill.price
