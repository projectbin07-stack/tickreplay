"""Donchian channel breakout - an EXAMPLE strategy, not a recommendation."""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from tickreplay.events import Bar, Direction, Fill, OrderType, Side
from tickreplay.strategy import Strategy, StrategyContext


class DonchianBreakout(Strategy):
    """Long-only channel breakout with a protective stop order.

    * Entry: the close exceeds the highest high of the previous ``entry`` bars. The
      signal carries the lowest low of the previous ``exit`` bars as its stop, so the
      engine can apply per-trade risk sizing when that is configured.
    * After each entry fill a STOP sell for the whole position rests at that level.
    * Exit: the close falls below the lowest low of the previous ``exit`` bars.

    The channels never include the current bar. Single-symbol only.
    """

    name = "donchian"

    def __init__(self, entry: int = 20, exit: int = 10) -> None:
        if entry < 1 or exit < 1:
            raise ValueError("entry and exit windows must be at least 1")
        self.entry = entry
        self.exit = exit
        self._highs: deque[Decimal] = deque(maxlen=entry)
        self._lows: deque[Decimal] = deque(maxlen=exit)
        self._stop_price: Decimal | None = None
        self._stop_order: int | None = None

    def describe(self) -> dict[str, str]:
        return {"entry": str(self.entry), "exit": str(self.exit)}

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        ready = len(self._highs) == self.entry and len(self._lows) == self.exit
        if ready:
            position = ctx.position(bar.symbol)
            floor = min(self._lows)
            if position == 0 and not ctx.open_orders(bar.symbol):
                if bar.close > max(self._highs) and floor < bar.close:
                    self._stop_price = floor
                    ctx.signal(bar.symbol, Direction.LONG, stop_price=floor)
            elif position > 0 and bar.close < floor:
                ctx.signal(bar.symbol, Direction.FLAT)
        self._highs.append(bar.high)
        self._lows.append(bar.low)

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None:
        position = ctx.position(fill.symbol)
        if fill.side is Side.BUY and position > 0 and self._stop_price is not None:
            if self._stop_order is not None:
                ctx.cancel(self._stop_order)
            self._stop_order = ctx.submit(
                fill.symbol, Side.SELL, position, OrderType.STOP, stop_price=self._stop_price
            )
        elif position == 0:
            if self._stop_order is not None:
                ctx.cancel(self._stop_order)
            self._stop_order = None
            self._stop_price = None
