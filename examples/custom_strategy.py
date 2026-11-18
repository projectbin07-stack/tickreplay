"""Write a strategy against the tickreplay API and backtest it on SYNTHETIC data.

The strategy is a toy: buy a fixed quantity with a limit order one percent below the
last close, and take it off again with a limit one percent above the fill. It exists to
show the three callbacks and explicit order entry - it is not a trading idea.

Run it from the repository root:

    python examples/custom_strategy.py
"""

from __future__ import annotations

from decimal import Decimal

from tickreplay import (
    Bar,
    EngineConfig,
    FeeModel,
    Fill,
    OrderType,
    Side,
    Strategy,
    StrategyContext,
    compute_metrics,
    run_backtest,
)
from tickreplay.data import gbm_bars

CENT = Decimal("0.01")


class DipBuyer(Strategy):
    name = "dip-buyer"

    def __init__(self, quantity: int = 100) -> None:
        self.quantity = Decimal(quantity)

    def on_start(self, ctx: StrategyContext) -> None:
        # No market data yet, so nothing can be ordered here; set up state instead.
        self.fills_seen = 0

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        # ``bar`` is complete. Anything submitted now can fill from the next bar on.
        if ctx.position(bar.symbol) == 0 and not ctx.open_orders(bar.symbol):
            limit = (bar.close * Decimal("0.99")).quantize(CENT)
            ctx.submit(bar.symbol, Side.BUY, self.quantity, OrderType.LIMIT, limit_price=limit)

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> None:
        self.fills_seen += 1
        if fill.side is Side.BUY:
            target = (fill.price * Decimal("1.01")).quantize(CENT)
            ctx.submit(fill.symbol, Side.SELL, fill.quantity, OrderType.LIMIT, limit_price=target)


def main() -> None:
    bars = gbm_bars(seed=42, n=1500)  # synthetic: geometric Brownian motion, fixed seed
    config = EngineConfig(fees=FeeModel(rate_bps=Decimal(5)))
    result = run_backtest(bars, DipBuyer(), config)
    metrics = compute_metrics(
        result.equity_curve, result.round_trips, traded_notional=result.traded_notional
    )
    print("SYNTHETIC data (gbm, seed=42) - simulated output, not evidence of profitability")
    print(f"fills={len(result.fills)} round_trips={metrics.trades} fees={result.fees_paid}")
    print(f"final_equity={result.final_equity} max_drawdown={metrics.max_drawdown:.4%}")
    print(f"digest={result.digest()}")


if __name__ == "__main__":
    main()
