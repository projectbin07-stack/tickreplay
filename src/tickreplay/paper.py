"""Paper-trading mode: the same engine and the same simulated execution, fed by a
:class:`~tickreplay.feeds.MarketFeed` that delivers events over time.

Paper mode never sends an order anywhere. Fills come from
:class:`~tickreplay.execution.SimulatedExecution`, exactly as in a backtest. The only
feed shipped is :class:`~tickreplay.feeds.ReplayFeed`; connecting a real venue is out of
scope for this project.
"""

from __future__ import annotations

from collections.abc import Callable

from tickreplay.engine import Engine, EngineConfig, Mode, RunResult
from tickreplay.events import Event
from tickreplay.feeds import MarketFeed
from tickreplay.strategy import Strategy


def run_paper(
    feed: MarketFeed,
    strategy: Strategy,
    config: EngineConfig | None = None,
    *,
    listener: Callable[[Event], None] | None = None,
) -> RunResult:
    """Run ``strategy`` against ``feed`` until the feed ends.

    ``listener`` is called with every event after it has been handled, which is how a
    caller watches fills as they happen.
    """
    return Engine(strategy, config, mode=Mode.PAPER, listener=listener).run(feed)
