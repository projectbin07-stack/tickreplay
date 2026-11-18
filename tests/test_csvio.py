from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from helpers import D, bar, tick
from tickreplay.data import (
    DataValidationError,
    find_gaps,
    format_timestamp,
    parse_timestamp,
    read_bars,
    read_trades,
    write_bars,
    write_trades,
)

HEADER = "timestamp,symbol,open,high,low,close,volume\n"


def write(tmp_path: Path, text: str, name: str = "bars.csv") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_reads_valid_bars_with_comments(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "# SYNTHETIC DATA - test fixture\n"
        + HEADER
        + "2024-01-01T00:00:00Z,ABC,100,101.5,99.5,101,1200\n"
        + "\n"
        + "2024-01-01T01:00:00Z,ABC,101,102,100,100.25,800\n",
    )
    series = read_bars(path)
    assert series.comments == ("SYNTHETIC DATA - test fixture",)
    assert series.symbols == ("ABC",)
    assert len(series.bars) == 2
    first = series.bars[0]
    assert first.ts == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert (first.open, first.high, first.low, first.close, first.volume) == (
        D(100),
        D("101.5"),
        D("99.5"),
        D(101),
        D(1200),
    )
    assert series.gaps == ()
    assert series.intervals == {"ABC": timedelta(hours=1)}


def test_timestamp_formats() -> None:
    expected = datetime(2024, 3, 5, 12, 30, tzinfo=timezone.utc)
    assert parse_timestamp("2024-03-05T12:30:00Z") == expected
    assert parse_timestamp("2024-03-05T12:30:00") == expected  # naive means UTC
    assert parse_timestamp("2024-03-05 18:00:00+05:30") == expected
    assert parse_timestamp("1709641800") == expected  # epoch seconds
    assert format_timestamp(expected) == "2024-03-05T12:30:00Z"
    with pytest.raises(ValueError):
        parse_timestamp("")
    with pytest.raises(ValueError):
        parse_timestamp("yesterday")


def test_symbol_column_is_optional_and_header_is_case_insensitive(tmp_path: Path) -> None:
    path = write(tmp_path, "Timestamp,Open,High,Low,Close,Volume\n2024-01-01,1,2,1,2,10\n")
    series = read_bars(path, default_symbol="ZZZ")
    assert series.bars[0].symbol == "ZZZ"
    assert series.intervals == {}  # one bar: nothing to infer


@pytest.mark.parametrize(
    ("body", "message", "line"),
    [
        ("2024-01-01T01:00:00Z,A,1,2,1,2,5\n2024-01-01T00:00:00Z,A,1,2,1,2,5\n", "out-of-order", 3),
        ("2024-01-01T00:00:00Z,A,1,2,1,2,5\n2024-01-01T00:00:00Z,A,1,2,1,2,5\n", "duplicate", 3),
        ("2024-01-01T00:00:00Z,A,-1,2,-1,2,5\n", "positive", 2),
        ("2024-01-01T00:00:00Z,A,0,2,0,2,5\n", "positive", 2),
        ("2024-01-01T00:00:00Z,A,5,4,3,4,5\n", "high is below", 2),
        ("2024-01-01T00:00:00Z,A,5,6,5.5,5.8,5\n", "low is above", 2),
        ("2024-01-01T00:00:00Z,A,1,2,1,2,-5\n", "volume", 2),
        ("2024-01-01T00:00:00Z,A,abc,2,1,2,5\n", "open is not a number", 2),
        ("2024-01-01T00:00:00Z,A,NaN,2,1,2,5\n", "not finite", 2),
        ("not-a-date,A,1,2,1,2,5\n", "bad timestamp", 2),
        ("2024-01-01T00:00:00Z,A,1,2,1,2\n", "expected 7 fields, found 6", 2),
        ("2024-01-01T00:00:00Z,,1,2,1,2,5\n", "empty symbol", 2),
    ],
)
def test_bar_validation_errors_carry_line_numbers(
    tmp_path: Path, body: str, message: str, line: int
) -> None:
    with pytest.raises(DataValidationError, match=message) as info:
        read_bars(write(tmp_path, HEADER + body))
    assert info.value.line == line
    assert str(info.value).startswith(f"line {line}:")


def test_structural_errors(tmp_path: Path) -> None:
    with pytest.raises(DataValidationError, match="no header"):
        read_bars(write(tmp_path, "# only a comment\n"))
    with pytest.raises(DataValidationError, match=r"missing column\(s\): low, volume"):
        read_bars(write(tmp_path, "timestamp,open,high,close\n"))
    with pytest.raises(DataValidationError, match="duplicate column"):
        read_bars(write(tmp_path, "timestamp,open,high,low,close,volume,open\n"))
    with pytest.raises(DataValidationError, match="no data rows"):
        read_bars(write(tmp_path, HEADER))


def test_gaps_are_reported_not_rejected(tmp_path: Path) -> None:
    rows = [0, 1, 2, 5, 6, 8]  # hours; holes of 2 bars and of 1 bar
    body = "".join(f"2024-01-01T{h:02d}:00:00Z,A,1,2,1,2,5\n" for h in rows)
    series = read_bars(write(tmp_path, HEADER + body))
    assert series.intervals["A"] == timedelta(hours=1)
    assert [(g.after.hour, g.before.hour, g.missing) for g in series.gaps] == [
        (2, 5, 2),
        (6, 8, 1),
    ]
    assert all(g.symbol == "A" for g in series.gaps)


def test_multi_symbol_is_validated_per_symbol_and_sorted(tmp_path: Path) -> None:
    body = (
        "2024-01-01T00:00:00Z,B,1,2,1,2,5\n"
        "2024-01-01T01:00:00Z,B,1,2,1,2,5\n"
        "2024-01-01T00:00:00Z,A,3,4,3,4,5\n"
        "2024-01-01T01:00:00Z,A,3,4,3,4,5\n"
    )
    series = read_bars(write(tmp_path, HEADER + body))
    assert [(b.ts.hour, b.symbol) for b in series.bars] == [(0, "A"), (0, "B"), (1, "A"), (1, "B")]
    assert series.symbols == ("A", "B")


def test_find_gaps_interval_tie_prefers_the_smaller_spacing() -> None:
    bars = [bar(i, 1, 1, 1, 1) for i in (0, 1, 3)]  # spacings 1h and 2h, once each
    gaps, intervals = find_gaps(bars)
    assert intervals == {"X": timedelta(hours=1)}
    assert len(gaps) == 1 and gaps[0].missing == 1


def test_bar_round_trip_is_lossless_and_uses_lf(tmp_path: Path) -> None:
    bars = [bar(0, "10.10", "10.50", "9.90", "10.20", 123), bar(1, "10.20", "11", "10", "10.5", 0)]
    path = tmp_path / "out.csv"
    write_bars(path, bars, comments=["hello"])
    raw = path.read_bytes()
    assert b"\r" not in raw
    assert raw.startswith(b"# hello\ntimestamp,symbol,open,high,low,close,volume\n")
    series = read_bars(path)
    assert list(series.bars) == bars
    assert series.comments == ("hello",)


def test_trades_round_trip_and_validation(tmp_path: Path) -> None:
    trades = [tick(0, "100.5", 3), tick(0, "100.6", 1), tick(2, "100.4", 7)]
    path = tmp_path / "trades.csv"
    write_trades(path, trades, comments=["ticks"])
    series = read_trades(path)
    assert list(series.trades) == trades  # equal timestamps are allowed for ticks
    assert series.comments == ("ticks",)

    head = "timestamp,price,size\n"
    with pytest.raises(DataValidationError, match="out-of-order") as info:
        read_trades(
            write(tmp_path, head + "2024-01-01T00:00:05Z,1,1\n2024-01-01T00:00:04Z,1,1\n", "t.csv")
        )
    assert info.value.line == 3
    with pytest.raises(DataValidationError, match="positive"):
        read_trades(write(tmp_path, head + "2024-01-01T00:00:05Z,0,1\n", "t.csv"))
    with pytest.raises(DataValidationError, match="non-negative"):
        read_trades(write(tmp_path, head + "2024-01-01T00:00:05Z,1,-1\n", "t.csv"))
    with pytest.raises(DataValidationError, match="price is not a number"):
        read_trades(write(tmp_path, head + "2024-01-01T00:00:05Z,x,1\n", "t.csv"))
    with pytest.raises(DataValidationError, match="no data rows"):
        read_trades(write(tmp_path, head, "t.csv"))
    assert (
        read_trades(
            write(tmp_path, head + "2024-01-01T00:00:05Z,1,1\n", "t.csv"), default_symbol="Q"
        )
        .trades[0]
        .symbol
        == "Q"
    )
