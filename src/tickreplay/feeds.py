"""Market-data feeds.

A feed is anything iterable that yields :class:`~tickreplay.events.Bar` or
:class:`~tickreplay.events.Trade` objects in non-decreasing time order. The engine does
not care where they come from, which is the whole of the "pluggable live feed" interface.

Shipped implementations:

* :class:`HistoricalFeed` - in-memory events, as fast as possible (backtests).
* :class:`ReplayFeed` - the same events paced against a clock (paper trading).

There is no exchange or broker feed in this project.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator
from typing import Protocol, runtime_checkable

from tickreplay.events import MarketEvent


@runtime_checkable
class MarketFeed(Protocol):
    """Source of market events in non-decreasing timestamp order."""

    def __iter__(self) -> Iterator[MarketEvent]: ...


def _sorted(events: Iterable[MarketEvent]) -> list[MarketEvent]:
    # ``sorted`` is stable, so events with equal (ts, symbol) keep their input order.
    return sorted(events, key=lambda e: (e.ts, e.symbol))


class HistoricalFeed:
    """Yields the given events sorted by ``(timestamp, symbol)`` with no delay."""

    def __init__(self, events: Iterable[MarketEvent]) -> None:
        self._events = _sorted(events)

    def __iter__(self) -> Iterator[MarketEvent]:
        return iter(self._events)

    def __len__(self) -> int:
        return len(self._events)


class ReplayFeed:
    """Replays recorded events with the gaps between them scaled by ``speed``.

    ``speed=1`` replays in real time, ``speed=60`` makes one recorded minute take one
    second, and ``speed=None`` disables pacing entirely. ``sleep`` is injectable so
    tests (and callers with their own scheduler) do not have to wait on a real clock.
    """

    def __init__(
        self,
        events: Iterable[MarketEvent],
        *,
        speed: float | None = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if speed is not None and speed <= 0:
            raise ValueError("speed must be positive, or None for no pacing")
        self._events = _sorted(events)
        self._speed = speed
        self._sleep = sleep

    def __iter__(self) -> Iterator[MarketEvent]:
        previous = None
        for event in self._events:
            if self._speed is not None and previous is not None:
                delay = (event.ts - previous).total_seconds() / self._speed
                if delay > 0:
                    self._sleep(delay)
            previous = event.ts
            yield event

    def __len__(self) -> int:
        return len(self._events)
