"""tickreplay - deterministic event-driven backtesting and paper-trading research engine.

Research and education software. It simulates; it does not trade. See the README for
scope and limitations.
"""

from tickreplay.backtest import run_backtest
from tickreplay.costs import FeeModel, FixedBpsSlippage, NoSlippage, VolumeParticipationSlippage
from tickreplay.engine import Engine, EngineConfig, EngineError, Mode, Rejection, RunResult
from tickreplay.eventqueue import EventQueue
from tickreplay.events import Bar, Direction, Fill, Order, OrderType, Side, Signal, Trade
from tickreplay.execution import ExecutionHandler, SimulatedExecution, WorkingOrder
from tickreplay.feeds import HistoricalFeed, MarketFeed, ReplayFeed
from tickreplay.metrics import Metrics, compute_metrics
from tickreplay.paper import run_paper
from tickreplay.portfolio import EquityPoint, Portfolio, Position, RoundTrip
from tickreplay.risk import RiskConfig, RiskManager, size_for_risk
from tickreplay.strategy import Strategy, StrategyContext

__version__ = "0.1.0"

__all__ = [
    "Bar",
    "Direction",
    "Engine",
    "EngineConfig",
    "EngineError",
    "EquityPoint",
    "EventQueue",
    "ExecutionHandler",
    "FeeModel",
    "Fill",
    "FixedBpsSlippage",
    "HistoricalFeed",
    "MarketFeed",
    "Metrics",
    "Mode",
    "NoSlippage",
    "Order",
    "OrderType",
    "Portfolio",
    "Position",
    "Rejection",
    "ReplayFeed",
    "RiskConfig",
    "RiskManager",
    "RoundTrip",
    "RunResult",
    "Side",
    "Signal",
    "SimulatedExecution",
    "Strategy",
    "StrategyContext",
    "Trade",
    "VolumeParticipationSlippage",
    "WorkingOrder",
    "__version__",
    "compute_metrics",
    "run_backtest",
    "run_paper",
    "size_for_risk",
]
