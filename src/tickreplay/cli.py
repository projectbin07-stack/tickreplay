"""Command-line interface: ``tickreplay gen | run | compare | paper``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tickreplay import __version__, strategies
from tickreplay.backtest import run_backtest
from tickreplay.costs import (
    FeeModel,
    FixedBpsSlippage,
    NoSlippage,
    SlippageModel,
    VolumeParticipationSlippage,
)
from tickreplay.data.csvio import (
    BarSeries,
    DataValidationError,
    format_timestamp,
    parse_timestamp,
    read_bars,
    write_bars,
)
from tickreplay.data.synthetic import SYNTHETIC_NOTICE, gbm_bars, ou_bars
from tickreplay.engine import EngineConfig, RunResult
from tickreplay.events import Event, Fill
from tickreplay.feeds import ReplayFeed
from tickreplay.metrics import Metrics, compute_metrics
from tickreplay.paper import run_paper
from tickreplay.report import (
    DISCLAIMER,
    fmt_money,
    fmt_pct,
    fmt_ratio,
    metric_rows,
    render_report,
)
from tickreplay.risk import RiskConfig


def _decimal(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not value.is_finite():
        raise argparse.ArgumentTypeError(f"not a finite number: {text!r}")
    return value


def _param(text: str) -> tuple[str, str]:
    key, sep, value = text.partition("=")
    if not sep or not key or not value:
        raise argparse.ArgumentTypeError(f"expected KEY=VALUE, got {text!r}")
    return key, value


def _add_engine_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--data", required=True, type=Path, help="bar CSV file")
    p.add_argument("--cash", type=_decimal, default=Decimal(100000), help="initial cash")
    p.add_argument("--fee-bps", type=_decimal, default=Decimal(0), help="proportional fee, bps")
    p.add_argument("--fee-fixed", type=_decimal, default=Decimal(0), help="fixed fee per fill")
    p.add_argument("--slippage-bps", type=_decimal, default=Decimal(0), help="fixed slippage, bps")
    p.add_argument(
        "--impact-bps",
        type=_decimal,
        default=None,
        help="extra slippage in bps at 100%% of bar volume (volume-participation model)",
    )
    p.add_argument(
        "--participation",
        type=_decimal,
        default=Decimal("0.1"),
        help="max fraction of a bar's volume fills may take; 0 disables the cap",
    )
    p.add_argument("--allocation", type=_decimal, default=Decimal("0.95"))
    p.add_argument("--qty-step", type=_decimal, default=Decimal(1), help="quantity increment")
    p.add_argument("--max-position", type=_decimal, default=None, help="max units per symbol")
    p.add_argument("--max-gross-exposure", type=_decimal, default=None, help="multiple of equity")
    p.add_argument("--risk-per-trade", type=_decimal, default=None, help="fraction of equity")
    p.add_argument("--daily-loss-limit", type=_decimal, default=None, help="fraction of equity")
    p.add_argument("--flatten-on-halt", action="store_true")
    p.add_argument("--periods-per-year", type=float, default=None, help="override inference")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tickreplay",
        description=(
            "Deterministic event-driven backtesting and paper-trading research engine. "
            "Simulation only: no exchange connectivity, not financial advice."
        ),
    )
    parser.add_argument("--version", action="version", version=f"tickreplay {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("gen", help="generate seeded SYNTHETIC bar data")
    gen.add_argument("--model", choices=("gbm", "ou"), default="gbm")
    gen.add_argument("--seed", type=int, required=True)
    gen.add_argument("--bars", type=int, default=1000)
    gen.add_argument("--out", type=Path, required=True)
    gen.add_argument("--symbol", default=None)
    gen.add_argument("--start", default="2024-01-01T00:00:00Z", help="ISO 8601 start time")
    gen.add_argument("--interval-seconds", type=int, default=3600)
    gen.add_argument("--start-price", type=float, default=100.0)
    gen.add_argument("--mu", type=float, default=0.05, help="annualised drift (gbm)")
    gen.add_argument("--sigma", type=float, default=0.6, help="annualised volatility")
    gen.add_argument("--theta", type=float, default=200.0, help="reversion speed (ou)")
    gen.add_argument("--mean-price", type=float, default=100.0, help="reversion level (ou)")

    run = sub.add_parser("run", help="backtest one strategy over a bar CSV")
    run.add_argument("--strategy", required=True, choices=strategies.available())
    run.add_argument("--param", action="append", type=_param, default=[], metavar="KEY=VALUE")
    run.add_argument("--report", type=Path, default=None, help="write an HTML report here")
    _add_engine_options(run)

    compare = sub.add_parser("compare", help="backtest several strategies on the same data")
    compare.add_argument(
        "--strategies",
        nargs="+",
        default=list(strategies.available()),
        choices=strategies.available(),
    )
    _add_engine_options(compare)

    paper = sub.add_parser("paper", help="paper-trade a strategy against a replayed CSV")
    paper.add_argument("--strategy", required=True, choices=strategies.available())
    paper.add_argument("--param", action="append", type=_param, default=[], metavar="KEY=VALUE")
    paper.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="replay speed multiplier (1 = real time); 0 = no pacing",
    )
    _add_engine_options(paper)
    return parser


def _config(args: argparse.Namespace) -> EngineConfig:
    slippage: SlippageModel
    if args.impact_bps is not None:
        slippage = VolumeParticipationSlippage(args.slippage_bps, args.impact_bps)
    elif args.slippage_bps > 0:
        slippage = FixedBpsSlippage(args.slippage_bps)
    else:
        slippage = NoSlippage()
    return EngineConfig(
        initial_cash=args.cash,
        fees=FeeModel(fixed=args.fee_fixed, rate_bps=args.fee_bps),
        slippage=slippage,
        max_participation=args.participation if args.participation > 0 else None,
        qty_step=args.qty_step,
        signal_allocation=args.allocation,
        risk=RiskConfig(
            max_position=args.max_position,
            max_gross_exposure=args.max_gross_exposure,
            risk_per_trade=args.risk_per_trade,
            daily_loss_limit=args.daily_loss_limit,
            flatten_on_halt=args.flatten_on_halt,
        ),
    )


def _load(path: Path) -> BarSeries:
    series = read_bars(path)
    print(
        f"data: {path.name}: {len(series.bars)} bars, symbols {', '.join(series.symbols)}, "
        f"{format_timestamp(series.bars[0].ts)} to {format_timestamp(series.bars[-1].ts)}"
    )
    for note in series.comments:
        print(f"data note: {note}")
    if series.gaps:
        missing = sum(g.missing for g in series.gaps)
        print(f"data warning: {len(series.gaps)} gap(s), about {missing} missing bar(s)")
        for gap in series.gaps[:5]:
            print(
                f"  {gap.symbol}: {format_timestamp(gap.after)} -> "
                f"{format_timestamp(gap.before)} ({gap.missing} missing)"
            )
    return series


def _metrics(result: RunResult, args: argparse.Namespace) -> Metrics:
    return compute_metrics(
        result.equity_curve,
        result.round_trips,
        traded_notional=result.traded_notional,
        periods_per_year=args.periods_per_year,
    )


def _print_summary(result: RunResult, metrics: Metrics) -> None:
    params = ", ".join(f"{k}={v}" for k, v in result.strategy_params.items())
    print(f"strategy: {result.strategy_name} ({params})  mode: {result.mode.value}")
    rows = metric_rows(result, metrics)
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"  {label:<{width}}  {value:>14}")
    print(f"  digest: {result.digest()}")


def _cmd_gen(args: argparse.Namespace) -> int:
    common = {
        "seed": args.seed,
        "n": args.bars,
        "start": parse_timestamp(args.start),
        "interval": timedelta(seconds=args.interval_seconds),
        "start_price": args.start_price,
        "sigma": args.sigma,
    }
    if args.model == "gbm":
        symbol = args.symbol or "SYN-GBM"
        bars = gbm_bars(symbol=symbol, mu=args.mu, **common)
        detail = f"model=gbm mu={args.mu} sigma={args.sigma}"
    else:
        symbol = args.symbol or "SYN-OU"
        bars = ou_bars(symbol=symbol, theta=args.theta, mean_price=args.mean_price, **common)
        detail = f"model=ou theta={args.theta} mean_price={args.mean_price} sigma={args.sigma}"
    comments = [
        SYNTHETIC_NOTICE,
        f"{detail} seed={args.seed} bars={args.bars} start_price={args.start_price} "
        f"interval_seconds={args.interval_seconds}",
    ]
    write_bars(args.out, bars, comments=comments)
    print(f"wrote {len(bars)} SYNTHETIC bars ({detail}, seed={args.seed}) to {args.out}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    series = _load(args.data)
    strategy = strategies.build(args.strategy, dict(args.param))
    result = run_backtest(series.bars, strategy, _config(args))
    metrics = _metrics(result, args)
    _print_summary(result, metrics)
    if args.report is not None:
        document = render_report(
            result,
            metrics,
            title=f"tickreplay: {result.strategy_name} on {args.data.name}",
            data_source=args.data.name,
            data_notes=series.comments,
        )
        args.report.write_text(document, encoding="utf-8", newline="\n")
        print(f"report: {args.report}")
    print(f"note: {DISCLAIMER}")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    series = _load(args.data)
    config = _config(args)
    header = (
        f"{'strategy':<10} {'final equity':>14} {'return':>9} {'max dd':>8} {'sharpe':>7} "
        f"{'trips':>6} {'win rate':>9} {'fees':>10}"
    )
    print(header)
    print("-" * len(header))
    for name in args.strategies:
        result = run_backtest(series.bars, strategies.build(name), config)
        m = _metrics(result, args)
        print(
            f"{name:<10} {fmt_money(result.final_equity):>14} {fmt_pct(m.total_return):>9} "
            f"{fmt_pct(m.max_drawdown):>8} {fmt_ratio(m.sharpe):>7} {m.trades:>6} "
            f"{fmt_pct(m.win_rate):>9} {fmt_money(result.fees_paid):>10}"
        )
    print(f"note: {DISCLAIMER}")
    return 0


def _cmd_paper(args: argparse.Namespace) -> int:
    series = _load(args.data)
    strategy = strategies.build(args.strategy, dict(args.param))

    def listener(event: Event) -> None:
        if isinstance(event, Fill):
            print(
                f"[paper] {format_timestamp(event.ts)} FILL {event.side.value} "
                f"{event.quantity} {event.symbol} @ {event.price} fee {event.fee}",
                flush=True,
            )

    feed = ReplayFeed(series.bars, speed=args.speed if args.speed > 0 else None)
    print("paper mode: simulated fills only; no orders leave this process")
    result = run_paper(feed, strategy, _config(args), listener=listener)
    _print_summary(result, _metrics(result, args))
    print(f"note: {DISCLAIMER}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = _build_parser().parse_args(argv)
    handlers = {"gen": _cmd_gen, "run": _cmd_run, "compare": _cmd_compare, "paper": _cmd_paper}
    try:
        return handlers[args.command](args)
    except (DataValidationError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
