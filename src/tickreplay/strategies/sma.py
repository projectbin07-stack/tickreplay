"""Simple moving-average crossover - an EXAMPLE strategy, not a recommendation."""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from tickreplay.events import Bar, Direction
from tickreplay.strategy import Strategy, StrategyContext


class SmaCrossover(Strategy):
    """Long while the fast average of closes is above the slow one, otherwise flat.

    It trades the symbol of the bars it is given and holds no state per symbol beyond
    the price window, so feed it a single-symbol series.
    """

    name = "sma"

    def __init__(self, fast: int = 10, slow: int = 30) -> None:
        if fast < 1 or slow <= fast:
            raise ValueError("need 1 <= fast < slow")
        self.fast = fast
        self.slow = slow
        self._closes: deque[Decimal] = deque(maxlen=slow)
        self._long = False

    def describe(self) -> dict[str, str]:
        return {"fast": str(self.fast), "slow": str(self.slow)}

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        self._closes.append(bar.close)
        if len(self._closes) < self.slow:
            return
        closes = list(self._closes)
        slow_avg = sum(closes, Decimal(0)) / self.slow
        fast_avg = sum(closes[-self.fast :], Decimal(0)) / self.fast
        want_long = fast_avg > slow_avg
        if want_long and not self._long:
            ctx.signal(bar.symbol, Direction.LONG)
        elif not want_long and self._long:
            ctx.signal(bar.symbol, Direction.FLAT)
        self._long = want_long
