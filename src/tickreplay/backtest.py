"""Backtest mode: run the engine over historical events as fast as possible."""

from __future__ import annotations

from collections.abc import Iterable

from tickreplay.engine import Engine, EngineConfig, Mode, RunResult
from tickreplay.events import MarketEvent
from tickreplay.feeds import HistoricalFeed
from tickreplay.strategy import Strategy


def run_backtest(
    events: Iterable[MarketEvent], strategy: Strategy, config: EngineConfig | None = None
) -> RunResult:
    """Sort ``events`` by ``(timestamp, symbol)`` and run ``strategy`` over them."""
    return Engine(strategy, config, mode=Mode.BACKTEST).run(HistoricalFeed(events))
