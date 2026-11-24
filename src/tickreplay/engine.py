"""The event loop shared by backtest and paper mode.

For every market event the engine does three things, in this order:

1. asks the execution handler for fills of orders placed on *earlier* events and queues
   them as :class:`~tickreplay.events.Fill` events;
2. queues the market event itself;
3. drains the queue. Handlers may push more events (a bar handler calls the strategy,
   which emits signals and orders); those carry the same timestamp and are processed in
   push order.

Because fills for a bar are produced before the strategy is shown that bar, and because
the simulator refuses to match an order against an event that is not strictly later than
the order, a strategy can never be filled on information it has not yet been given.
"""

from __future__ import annotations

import enum
import hashlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from tickreplay.costs import FeeModel, NoSlippage, SlippageModel
from tickreplay.eventqueue import EventQueue
from tickreplay.events import (
    ZERO,
    Bar,
    Direction,
    Event,
    Fill,
    MarketEvent,
    Order,
    OrderType,
    Side,
    Signal,
    Trade,
)
from tickreplay.execution import (
    ExecutionHandler,
    SimulatedExecution,
    WorkingOrder,
    check_price_ticks,
)
from tickreplay.portfolio import EquityPoint, Portfolio, RoundTrip
from tickreplay.risk import RiskConfig, RiskManager, floor_to_step, size_for_risk
from tickreplay.strategy import Strategy


class EngineError(RuntimeError):
    """Raised when the engine is driven incorrectly (e.g. an out-of-order feed)."""


class Mode(enum.Enum):
    """How the engine is being driven. There is deliberately no ``LIVE`` member."""

    BACKTEST = "backtest"
    PAPER = "paper"


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """Everything that parameterises a run apart from the strategy and the data.

    ``signal_allocation`` is the fraction of equity committed when a signal is sized
    without a stop price; it also caps risk-sized signals.

    ``price_tick`` rounds slipped MARKET and STOP fills of every symbol to that tick;
    ``price_ticks`` sets it per symbol and takes precedence. With neither, fill prices
    are not rounded.
    """

    initial_cash: Decimal = Decimal(100000)
    fees: FeeModel = field(default_factory=FeeModel)
    slippage: SlippageModel = field(default_factory=NoSlippage)
    max_participation: Decimal | None = Decimal("0.1")
    price_tick: Decimal | None = None
    price_ticks: Mapping[str, Decimal] = field(default_factory=dict)
    qty_step: Decimal = Decimal(1)
    signal_allocation: Decimal = Decimal("0.95")
    risk: RiskConfig = field(default_factory=RiskConfig)

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if not (0 < self.signal_allocation <= 1):
            raise ValueError("signal_allocation must be in (0, 1]")
        check_price_ticks(self.price_tick, self.price_ticks)


@dataclass(frozen=True, slots=True)
class Rejection:
    """An order or signal the engine refused, with the reason."""

    ts: datetime
    symbol: str
    reason: str
    order: Order | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    """Immutable record of a finished run."""

    mode: Mode
    strategy_name: str
    strategy_params: dict[str, str]
    config: EngineConfig
    equity_curve: tuple[EquityPoint, ...]
    fills: tuple[Fill, ...]
    orders: tuple[Order, ...]
    rejections: tuple[Rejection, ...]
    round_trips: tuple[RoundTrip, ...]
    halts: tuple[datetime, ...]
    final_cash: Decimal
    final_equity: Decimal
    final_positions: dict[str, Decimal]
    realised_pnl: Decimal
    unrealised_pnl: Decimal
    fees_paid: Decimal
    traded_notional: Decimal
    events_processed: int

    def digest(self) -> str:
        """SHA-256 over every fill and equity sample.

        Two runs with the same inputs produce the same digest; it is the cheap way to
        assert reproducibility.
        """
        h = hashlib.sha256()
        for f in self.fills:
            line = (
                f"F|{f.ts.isoformat()}|{f.fill_id}|{f.order_id}|{f.symbol}|{f.side.value}"
                f"|{f.quantity}|{f.price}|{f.fee}\n"
            )
            h.update(line.encode())
        for p in self.equity_curve:
            h.update(f"E|{p.ts.isoformat()}|{p.equity}|{p.cash}\n".encode())
        return h.hexdigest()


class _Context:
    """Concrete :class:`~tickreplay.strategy.StrategyContext` bound to one engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @property
    def now(self) -> datetime | None:
        return self._engine._now

    @property
    def cash(self) -> Decimal:
        return self._engine.portfolio.cash

    @property
    def equity(self) -> Decimal:
        return self._engine.portfolio.equity

    def position(self, symbol: str) -> Decimal:
        return self._engine.portfolio.quantity(symbol)

    def open_orders(self, symbol: str | None = None) -> tuple[WorkingOrder, ...]:
        return self._engine.execution.open_orders(symbol)

    def signal(self, symbol: str, direction: Direction, stop_price: Decimal | None = None) -> None:
        self._engine._queue.push(Signal(self._engine._require_now(), symbol, direction, stop_price))

    def submit(
        self,
        symbol: str,
        side: Side,
        quantity: Decimal,
        order_type: OrderType = OrderType.MARKET,
        *,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
    ) -> int:
        return self._engine._new_order(symbol, side, quantity, order_type, limit_price, stop_price)

    def cancel(self, order_id: int) -> bool:
        return self._engine.execution.cancel(order_id)


class Engine:
    """Drives one strategy over one stream of market events."""

    def __init__(
        self,
        strategy: Strategy,
        config: EngineConfig | None = None,
        *,
        mode: Mode = Mode.BACKTEST,
        execution: ExecutionHandler | None = None,
        listener: Callable[[Event], None] | None = None,
    ) -> None:
        self.strategy = strategy
        self.config = config if config is not None else EngineConfig()
        self.mode = mode
        self.execution: ExecutionHandler = (
            execution
            if execution is not None
            else SimulatedExecution(
                fees=self.config.fees,
                slippage=self.config.slippage,
                max_participation=self.config.max_participation,
                price_tick=self.config.price_tick,
                price_ticks=self.config.price_ticks,
                qty_step=self.config.qty_step,
            )
        )
        self.portfolio = Portfolio(self.config.initial_cash)
        self.risk = RiskManager(self.config.risk)
        self._listener = listener
        self._queue = EventQueue()
        self._ctx = _Context(self)
        self._now: datetime | None = None
        self._next_order_id = 1
        self._last_price: dict[str, Decimal] = {}
        self._fills: list[Fill] = []
        self._orders: list[Order] = []
        self._rejections: list[Rejection] = []
        self._halts: list[datetime] = []
        self._events = 0
        self._ran = False

    # -- public -----------------------------------------------------------------------

    def run(self, feed: Iterable[MarketEvent]) -> RunResult:
        """Consume ``feed`` to exhaustion and return the result. One-shot."""
        if self._ran:
            raise EngineError("an Engine instance can only be run once")
        self._ran = True
        self.strategy.on_start(self._ctx)
        for market_event in feed:
            if self._now is not None and market_event.ts < self._now:
                raise EngineError(
                    f"feed went backwards in time: {market_event.ts.isoformat()} "
                    f"after {self._now.isoformat()}"
                )
            self._now = market_event.ts
            if isinstance(market_event, Bar):
                fills = self.execution.on_bar(market_event)
            else:
                fills = self.execution.on_trade(market_event)
            for fill in fills:
                self._queue.push(fill)
            self._queue.push(market_event)
            self._drain(market_event.ts)
        return self._result()

    # -- event loop -------------------------------------------------------------------

    def _drain(self, until: datetime) -> None:
        while True:
            next_ts = self._queue.peek_ts()
            if next_ts is None or next_ts > until:
                return
            event = self._queue.pop()
            self._events += 1
            if isinstance(event, Fill):
                self._on_fill(event)
            elif isinstance(event, Bar):
                self._on_market(event, event.close)
                self.strategy.on_bar(self._ctx, event)
            elif isinstance(event, Trade):
                self._on_market(event, event.price)
                self.strategy.on_trade(self._ctx, event)
            elif isinstance(event, Signal):
                self._on_signal(event)
            else:
                self._on_order(event)
            if self._listener is not None:
                self._listener(event)

    def _on_fill(self, fill: Fill) -> None:
        self.portfolio.apply_fill(fill)
        self._fills.append(fill)
        self.strategy.on_fill(self._ctx, fill)

    def _on_market(self, event: MarketEvent, price: Decimal) -> None:
        self._last_price[event.symbol] = price
        self.portfolio.mark(event.symbol, price)
        point = self.portfolio.record(event.ts)
        if self.risk.update(event.ts, point.equity):
            self._halts.append(event.ts)
            self._on_halt()

    def _on_halt(self) -> None:
        """Pull exposure-increasing orders; optionally flatten every position."""
        for working in self.execution.open_orders():
            position = self.portfolio.quantity(working.order.symbol)
            if position == 0 or (position > 0) == (working.order.side is Side.BUY):
                self.execution.cancel(working.order.order_id)
        if self.config.risk.flatten_on_halt:
            for symbol in sorted(self.portfolio.positions):
                self._flatten(symbol)

    def _flatten(self, symbol: str) -> None:
        for working in self.execution.open_orders(symbol):
            self.execution.cancel(working.order.order_id)
        position = self.portfolio.quantity(symbol)
        if position != 0:
            side = Side.SELL if position > 0 else Side.BUY
            self._new_order(symbol, side, abs(position), OrderType.MARKET, None, None)

    def _on_signal(self, signal: Signal) -> None:
        symbol = signal.symbol
        if signal.direction is Direction.FLAT:
            self._flatten(symbol)
            return
        price = self._last_price.get(symbol)
        if price is None:
            self._rejections.append(Rejection(signal.ts, symbol, "no market data for symbol yet"))
            return
        position = self.portfolio.quantity(symbol)
        want_long = signal.direction is Direction.LONG
        if position != 0 and (position > 0) == want_long:
            return  # already positioned that way
        if self.risk.halted:
            self._rejections.append(
                Rejection(signal.ts, symbol, "daily loss limit reached: trading halted for the day")
            )
            return
        for working in self.execution.open_orders(symbol):
            self.execution.cancel(working.order.order_id)
        quantity = self._size(signal, price)
        if quantity <= 0:
            self._rejections.append(Rejection(signal.ts, symbol, "signal sized to zero quantity"))
        total = quantity + abs(position)
        if total > 0:
            side = Side.BUY if want_long else Side.SELL
            self._new_order(symbol, side, total, OrderType.MARKET, None, None)

    def _size(self, signal: Signal, price: Decimal) -> Decimal:
        equity = self.portfolio.equity
        step = self.config.qty_step
        quantity = floor_to_step(equity * self.config.signal_allocation / price, step)
        risk_fraction = self.config.risk.risk_per_trade
        if signal.stop_price is not None and risk_fraction is not None:
            quantity = min(
                quantity, size_for_risk(equity, price, signal.stop_price, risk_fraction, step)
            )
        return self.risk.cap_quantity(
            quantity,
            reference_price=price,
            equity=equity,
            other_gross_exposure=self.portfolio.gross_exposure_excluding(signal.symbol),
            qty_step=step,
        )

    def _on_order(self, order: Order) -> None:
        price = self._last_price.get(order.symbol)
        if price is None:
            self._rejections.append(
                Rejection(order.ts, order.symbol, "no market data for symbol yet", order)
            )
            return
        working = sum((w.signed_remaining for w in self.execution.open_orders(order.symbol)), ZERO)
        reason = self.risk.review(
            order,
            position=self.portfolio.quantity(order.symbol),
            working=working,
            reference_price=price,
            equity=self.portfolio.equity,
            other_gross_exposure=self.portfolio.gross_exposure_excluding(order.symbol),
        )
        if reason is not None:
            self._rejections.append(Rejection(order.ts, order.symbol, reason, order))
            return
        self.execution.submit(order)
        self._orders.append(order)

    # -- helpers ----------------------------------------------------------------------

    def _require_now(self) -> datetime:
        if self._now is None:
            raise EngineError("orders and signals cannot be issued before the first market event")
        return self._now

    def _new_order(
        self,
        symbol: str,
        side: Side,
        quantity: Decimal,
        order_type: OrderType,
        limit_price: Decimal | None,
        stop_price: Decimal | None,
    ) -> int:
        order = Order(
            ts=self._require_now(),
            order_id=self._next_order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            limit_price=limit_price,
            stop_price=stop_price,
        )
        self._next_order_id += 1
        self._queue.push(order)
        return order.order_id

    def _result(self) -> RunResult:
        p = self.portfolio
        return RunResult(
            mode=self.mode,
            strategy_name=self.strategy.name,
            strategy_params=dict(self.strategy.describe()),
            config=self.config,
            equity_curve=tuple(p.equity_curve),
            fills=tuple(self._fills),
            orders=tuple(self._orders),
            rejections=tuple(self._rejections),
            round_trips=tuple(p.round_trips),
            halts=tuple(self._halts),
            final_cash=p.cash,
            final_equity=p.equity,
            final_positions={s: pos.quantity for s, pos in sorted(p.positions.items())},
            realised_pnl=p.realised_pnl,
            unrealised_pnl=p.unrealised_pnl,
            fees_paid=p.fees_paid,
            traded_notional=p.traded_notional,
            events_processed=self._events,
        )
