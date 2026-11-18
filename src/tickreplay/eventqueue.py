"""Priority event queue ordered by timestamp, then by insertion sequence.

The sequence number is a monotonically increasing integer assigned at ``push`` time, so
two events with the same timestamp always come out in the order they went in. That tie
break is what makes a run reproducible: nothing depends on hash ordering, object identity
or wall-clock time.
"""

from __future__ import annotations

import heapq
from datetime import datetime

from tickreplay.events import Event


class EventQueue:
    """Min-heap of events keyed on ``(timestamp, sequence)``."""

    def __init__(self) -> None:
        self._heap: list[tuple[datetime, int, Event]] = []
        self._seq = 0

    def push(self, event: Event) -> int:
        """Add ``event`` and return the sequence number it was assigned."""
        seq = self._seq
        self._seq += 1
        heapq.heappush(self._heap, (event.ts, seq, event))
        return seq

    def pop(self) -> Event:
        """Remove and return the earliest event (FIFO among equal timestamps)."""
        if not self._heap:
            raise IndexError("pop from an empty EventQueue")
        return heapq.heappop(self._heap)[2]

    def peek_ts(self) -> datetime | None:
        """Timestamp of the next event, or ``None`` when the queue is empty."""
        return self._heap[0][0] if self._heap else None

    @property
    def pushed(self) -> int:
        """Total number of events ever pushed (the next sequence number)."""
        return self._seq

    def __len__(self) -> int:
        return len(self._heap)

    def __bool__(self) -> bool:
        return bool(self._heap)
