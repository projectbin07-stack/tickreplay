"""Strict CSV readers and writers for bars and trade ticks.

Bar files need the columns ``timestamp,open,high,low,close,volume`` and may carry a
``symbol`` column. Trade files need ``timestamp,price,size`` and may carry ``symbol``.
Lines starting with ``#`` are comments; they are preserved in ``comments`` so provenance
notes (for example the "synthetic data" banner written by ``tickreplay gen``) survive.

Timestamps are ISO 8601 (a trailing ``Z`` is accepted; naive values are taken as UTC)
or integer Unix epoch seconds.

Validation failures raise :class:`DataValidationError` with the offending line number.
Gaps are *reported*, not rejected: real feeds have holes, and the caller should decide.
"""

from __future__ import annotations

import csv
import itertools
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tickreplay.events import Bar, Trade

BAR_COLUMNS = ("timestamp", "symbol", "open", "high", "low", "close", "volume")
TRADE_COLUMNS = ("timestamp", "symbol", "price", "size")


class DataValidationError(ValueError):
    """A data file broke a validation rule."""

    def __init__(self, message: str, line: int | None = None) -> None:
        self.line = line
        super().__init__(f"line {line}: {message}" if line is not None else message)


@dataclass(frozen=True, slots=True)
class Gap:
    """A hole between two consecutive rows of one symbol."""

    symbol: str
    after: datetime
    before: datetime
    missing: int


@dataclass(frozen=True, slots=True)
class BarSeries:
    """Validated bars sorted by ``(timestamp, symbol)``, plus what was learned reading them."""

    bars: tuple[Bar, ...]
    gaps: tuple[Gap, ...]
    intervals: dict[str, timedelta]
    comments: tuple[str, ...]

    @property
    def symbols(self) -> tuple[str, ...]:
        """Distinct symbols, sorted."""
        return tuple(sorted({b.symbol for b in self.bars}))


@dataclass(frozen=True, slots=True)
class TradeSeries:
    """Validated trade ticks in file order (non-decreasing time per symbol)."""

    trades: tuple[Trade, ...]
    comments: tuple[str, ...]


def parse_timestamp(text: str) -> datetime:
    """Parse ISO 8601 or integer epoch seconds into an aware UTC datetime."""
    raw = text.strip()
    if not raw:
        raise ValueError("empty timestamp")
    if raw.lstrip("-").isdigit():
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    if raw[-1] in "zZ":
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_timestamp(ts: datetime) -> str:
    """Canonical text form used by the writers (UTC, ``Z`` suffix)."""
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _decimal(row: dict[str, str], column: str, line: int) -> Decimal:
    text = (row.get(column) or "").strip()
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise DataValidationError(f"{column} is not a number: {text!r}", line) from None
    if not value.is_finite():
        raise DataValidationError(f"{column} is not finite: {text!r}", line)
    return value


def _rows(
    path: Path, required: Sequence[str]
) -> tuple[list[tuple[int, dict[str, str]]], list[str], bool]:
    comments: list[str] = []
    data_lines: list[tuple[int, str]] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                comments.append(stripped[1:].strip())
                continue
            data_lines.append((number, line))
    if not data_lines:
        raise DataValidationError(f"{path.name}: no header row found")
    header_line, header_text = data_lines[0]
    header = [h.strip().lower() for h in next(csv.reader([header_text]))]
    missing = [c for c in required if c not in header]
    if missing:
        raise DataValidationError(f"missing column(s): {', '.join(missing)}", header_line)
    if len(set(header)) != len(header):
        raise DataValidationError("duplicate column names in header", header_line)
    rows: list[tuple[int, dict[str, str]]] = []
    for number, text in data_lines[1:]:
        values = next(csv.reader([text]))
        if len(values) != len(header):
            raise DataValidationError(f"expected {len(header)} fields, found {len(values)}", number)
        rows.append((number, dict(zip(header, values, strict=True))))
    return rows, comments, "symbol" in header


def _timestamp(row: dict[str, str], line: int) -> datetime:
    try:
        return parse_timestamp(row["timestamp"])
    except (ValueError, OverflowError, OSError):
        raise DataValidationError(f"bad timestamp: {row['timestamp']!r}", line) from None


def _symbol(row: dict[str, str], has_symbol: bool, default: str, line: int) -> str:
    if not has_symbol:
        return default
    symbol = row["symbol"].strip()
    if not symbol:
        raise DataValidationError("empty symbol", line)
    return symbol


def find_gaps(bars: Iterable[Bar]) -> tuple[tuple[Gap, ...], dict[str, timedelta]]:
    """Infer each symbol's bar interval (its most common spacing) and list larger gaps.

    ``missing`` counts the whole intervals absent between the two neighbours. A symbol
    with fewer than two bars has no inferred interval.
    """
    by_symbol: dict[str, list[datetime]] = {}
    for bar in bars:
        by_symbol.setdefault(bar.symbol, []).append(bar.ts)
    gaps: list[Gap] = []
    intervals: dict[str, timedelta] = {}
    for symbol in sorted(by_symbol):
        stamps = by_symbol[symbol]
        deltas = [b - a for a, b in itertools.pairwise(stamps)]
        if not deltas:
            continue
        counts = Counter(deltas)
        top = max(counts.values())
        interval = min(d for d, n in counts.items() if n == top)
        intervals[symbol] = interval
        for a, b in itertools.pairwise(stamps):
            if b - a > interval:
                gaps.append(Gap(symbol, a, b, max(1, round((b - a) / interval) - 1)))
    gaps.sort(key=lambda g: (g.after, g.symbol))
    return tuple(gaps), intervals


def read_bars(path: str | Path, *, default_symbol: str = "ASSET") -> BarSeries:
    """Read and validate a bar CSV.

    Rules: positive prices, ``low <= open, close <= high``, non-negative volume, and
    strictly increasing timestamps within each symbol.
    """
    rows, comments, has_symbol = _rows(Path(path), [c for c in BAR_COLUMNS if c != "symbol"])
    last_ts: dict[str, datetime] = {}
    bars: list[Bar] = []
    for line, row in rows:
        ts = _timestamp(row, line)
        symbol = _symbol(row, has_symbol, default_symbol, line)
        previous = last_ts.get(symbol)
        if previous is not None and ts <= previous:
            kind = "duplicate" if ts == previous else "out-of-order"
            raise DataValidationError(
                f"{kind} timestamp {format_timestamp(ts)} for {symbol} "
                "(timestamps must be strictly increasing)",
                line,
            )
        last_ts[symbol] = ts
        values = {c: _decimal(row, c, line) for c in ("open", "high", "low", "close", "volume")}
        try:
            bars.append(Bar(ts=ts, symbol=symbol, **values))
        except ValueError as exc:
            raise DataValidationError(str(exc), line) from None
    if not bars:
        raise DataValidationError("file contains a header but no data rows")
    bars.sort(key=lambda b: (b.ts, b.symbol))
    gaps, intervals = find_gaps(bars)
    return BarSeries(tuple(bars), gaps, intervals, tuple(comments))


def read_trades(path: str | Path, *, default_symbol: str = "ASSET") -> TradeSeries:
    """Read and validate a trade-tick CSV.

    Rules: positive price, non-negative size, non-decreasing timestamps per symbol
    (several prints may share a timestamp), and non-decreasing timestamps overall.
    """
    rows, comments, has_symbol = _rows(Path(path), ["timestamp", "price", "size"])
    last: datetime | None = None
    trades: list[Trade] = []
    for line, row in rows:
        ts = _timestamp(row, line)
        if last is not None and ts < last:
            raise DataValidationError(
                f"out-of-order timestamp {format_timestamp(ts)} "
                "(timestamps must be non-decreasing)",
                line,
            )
        last = ts
        try:
            trades.append(
                Trade(
                    ts=ts,
                    symbol=_symbol(row, has_symbol, default_symbol, line),
                    price=_decimal(row, "price", line),
                    size=_decimal(row, "size", line),
                )
            )
        except ValueError as exc:
            if isinstance(exc, DataValidationError):
                raise
            raise DataValidationError(str(exc), line) from None
    if not trades:
        raise DataValidationError("file contains a header but no data rows")
    return TradeSeries(tuple(trades), tuple(comments))


def write_bars(path: str | Path, bars: Iterable[Bar], *, comments: Sequence[str] = ()) -> None:
    """Write bars as CSV with LF line endings, preceded by ``# comment`` lines."""
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        for comment in comments:
            handle.write(f"# {comment}\n")
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(BAR_COLUMNS)
        for b in bars:
            writer.writerow(
                [format_timestamp(b.ts), b.symbol, b.open, b.high, b.low, b.close, b.volume]
            )


def write_trades(
    path: str | Path, trades: Iterable[Trade], *, comments: Sequence[str] = ()
) -> None:
    """Write trade ticks as CSV with LF line endings."""
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        for comment in comments:
            handle.write(f"# {comment}\n")
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(TRADE_COLUMNS)
        for t in trades:
            writer.writerow([format_timestamp(t.ts), t.symbol, t.price, t.size])
