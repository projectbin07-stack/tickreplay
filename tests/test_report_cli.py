from __future__ import annotations

import math
import os
import re
import subprocess
import sys
from datetime import timedelta
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

import pytest

from helpers import D, bars_from_closes
from tickreplay import EngineConfig, FeeModel, __version__, compute_metrics, run_backtest
from tickreplay.cli import main
from tickreplay.data import gbm_bars
from tickreplay.report import (
    DISCLAIMER,
    _decimate,
    _nice_ticks,
    fmt_duration,
    fmt_money,
    fmt_pct,
    fmt_ratio,
    render_report,
)
from tickreplay.strategies import build

VOID = {"meta", "line", "polyline", "polygon", "br"}


class Checker(HTMLParser):
    """Counts tags and verifies that every opened element is closed in order."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.tags: list[str] = []
        self.attrs: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attrs.extend(attrs)
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attrs.extend(attrs)

    def handle_endtag(self, tag: str) -> None:
        assert self.stack and self.stack.pop() == tag, f"unbalanced </{tag}>"


def make_report(n: int = 900, title: str = "t") -> str:
    result = run_backtest(
        gbm_bars(seed=42, n=n), build("sma"), EngineConfig(fees=FeeModel(rate_bps=D(5)))
    )
    metrics = compute_metrics(
        result.equity_curve, result.round_trips, traded_notional=result.traded_notional
    )
    return render_report(
        result, metrics, title=title, data_source="mem.csv", data_notes=["SYNTHETIC <data>"]
    )


def test_report_is_well_formed_and_self_contained() -> None:
    doc = make_report()
    checker = Checker()
    checker.feed(doc)
    assert checker.stack == []
    assert checker.tags.count("svg") == 2  # equity + drawdown
    assert checker.tags.count("polyline") == 2 and checker.tags.count("polygon") == 1
    # Nothing is fetched from anywhere: no URLs and no src/href attributes at all.
    assert "http://" not in doc and "https://" not in doc and "//cdn" not in doc
    assert not [a for a in checker.attrs if a[0] in ("src", "href")]
    assert DISCLAIMER in doc
    assert "SYNTHETIC &lt;data&gt;" in doc and "<data>" not in doc
    assert f"tickreplay {__version__}" in doc
    for label in ("Total return", "Sharpe", "Max drawdown", "Result digest", "Fee model"):
        assert label in doc


def test_report_escapes_titles_and_draws_every_decimated_point() -> None:
    doc = make_report(n=2500, title='<script>alert("x")</script>')
    assert "<script>alert" not in doc
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in doc
    for points in re.findall(r'<polyline class="line\d" points="([^"]+)"', doc):
        coords = points.split()
        assert len(coords) == 600  # 2,500 samples decimated to the 600-point cap
        xs = [float(c.split(",")[0]) for c in coords]
        assert xs == sorted(xs) and xs[0] == 70.0 and xs[-1] == 864.0


def test_report_handles_a_flat_equity_curve() -> None:
    result = run_backtest(bars_from_closes([10, 10, 10]), build("sma"))
    doc = render_report(result, compute_metrics(result.equity_curve))
    assert doc.count("<svg") == 2 and not re.search(r"nan", doc.lower())
    assert "Data note" not in doc


def test_formatters() -> None:
    assert fmt_pct(0.12345) == "12.35%" and fmt_pct(None) == "n/a"
    assert fmt_ratio(1.005) == "1.00" and fmt_ratio(None) == "n/a"
    assert fmt_ratio(math.inf) == "inf"
    assert fmt_money(Decimal("1234567.891")) == "1,234,567.89"
    assert fmt_duration(timedelta(days=2, hours=5, minutes=59)) == "2d 5h"
    assert _decimate(5, 10) == [0, 1, 2, 3, 4]
    picked = _decimate(1000, 10)
    assert len(picked) == 10 and picked[0] == 0 and picked[-1] == 999
    assert _nice_ticks(0, 100) == [0, 20, 40, 60, 80, 100]
    assert _nice_ticks(-10.88, 0) == [-10, -5, 0]
    assert _nice_ticks(5, 5)  # degenerate range still yields ticks


# -- CLI --------------------------------------------------------------------------------


def gen(tmp_path: Path, *extra: str, name: str = "data.csv") -> Path:
    out = tmp_path / name
    assert main(["gen", "--seed", "42", "--bars", "600", "--out", str(out), *extra]) == 0
    return out


def test_cli_gen_is_reproducible_and_labelled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = gen(tmp_path, name="a.csv")
    b = gen(tmp_path, name="b.csv")
    assert "wrote 600 SYNTHETIC bars" in capsys.readouterr().out
    assert a.read_bytes() == b.read_bytes()
    lines = a.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# SYNTHETIC DATA")
    assert "seed=42" in lines[1] and "model=gbm" in lines[1]
    assert lines[2] == "timestamp,symbol,open,high,low,close,volume"
    assert len(lines) == 603
    ou = gen(tmp_path, "--model", "ou", "--symbol", "MR", "--interval-seconds", "60", name="ou.csv")
    rows = ou.read_text(encoding="utf-8").splitlines()
    assert "model=ou" in rows[1]
    assert rows[3].startswith("2024-01-01T00:00:00Z,MR,") and rows[4].startswith(
        "2024-01-01T00:01:00Z"
    )


def test_cli_run_writes_report_and_prints_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = gen(tmp_path)
    report = tmp_path / "out.html"
    args = [
        "run",
        "--strategy",
        "sma",
        "--data",
        str(data),
        "--report",
        str(report),
        "--fee-bps",
        "5",
        "--slippage-bps",
        "2",
        "--param",
        "fast=5",
        "--param",
        "slow=20",
    ]
    capsys.readouterr()
    assert main(args) == 0
    first = capsys.readouterr().out
    assert "data note: SYNTHETIC DATA" in first
    assert "strategy: sma (fast=5, slow=20)  mode: backtest" in first
    assert "Total return" in first and "digest: " in first
    assert first.rstrip().endswith(DISCLAIMER)
    html = report.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>") and "SYNTHETIC DATA" in html
    assert "https://" not in html
    # Same command, same output - byte for byte, including the report.
    assert main(args) == 0
    assert capsys.readouterr().out == first
    assert report.read_text(encoding="utf-8") == html


def test_cli_run_options_change_the_configuration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = gen(tmp_path)
    capsys.readouterr()
    base = ["run", "--strategy", "donchian", "--data", str(data)]
    assert main(base) == 0
    plain = capsys.readouterr().out
    assert (
        main(
            [
                *base,
                "--impact-bps",
                "50",
                "--fee-fixed",
                "1",
                "--risk-per-trade",
                "0.01",
                "--max-position",
                "100",
                "--max-gross-exposure",
                "1",
                "--daily-loss-limit",
                "0.02",
                "--flatten-on-halt",
                "--participation",
                "0",
                "--periods-per-year",
                "8760",
            ]
        )
        == 0
    )
    tuned = capsys.readouterr().out
    assert re.search(r"Fees paid\s+0\.00", plain)
    assert not re.search(r"Fees paid\s+0\.00", tuned)
    assert plain.split("digest: ")[1] != tuned.split("digest: ")[1]


def test_cli_price_tick_restores_cent_rounding_and_is_deterministic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The README quick start: v0.1.0 always rounded to 0.01 and printed this digest.
    data = tmp_path / "demo.csv"
    assert main(["gen", "--seed", "42", "--bars", "1500", "--out", str(data)]) == 0
    base = ["run", "--strategy", "sma", "--data", str(data), "--fee-bps", "5"]
    base += ["--slippage-bps", "2"]
    v010 = "980da7354c1da3d104df73d4ca77b29f90a5157b07a47b5c1eb3899509444625"
    digests = []
    for extra in ([], [], ["--price-tick", "0.01"], ["--price-tick", "SYN-GBM=0.01"]):
        capsys.readouterr()
        assert main([*base, *extra]) == 0
        digests.append(capsys.readouterr().out.split("digest: ")[1].split()[0])
    unrounded, again, cent, per_symbol = digests
    assert unrounded == again  # no rounding is still a deterministic replay
    assert cent == per_symbol == v010
    assert unrounded != v010


def test_cli_rejects_bad_price_ticks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = gen(tmp_path)
    base = ["run", "--strategy", "sma", "--data", str(data)]
    for bad in ("0", "X=-1", "=0.01", "X=abc"):
        with pytest.raises(SystemExit):
            main([*base, "--price-tick", bad])
    assert main([*base, "--price-tick", "0.01", "--price-tick", "0.1"]) == 2
    assert "more than once" in capsys.readouterr().err


def test_cli_compare_lists_each_strategy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = gen(tmp_path)
    capsys.readouterr()
    assert main(["compare", "--data", str(data)]) == 0
    out = capsys.readouterr().out
    rows = [line for line in out.splitlines() if line.startswith(("sma ", "donchian "))]
    assert [r.split()[0] for r in rows] == ["sma", "donchian"]
    assert "final equity" in out and DISCLAIMER in out
    assert main(["compare", "--data", str(data), "--strategies", "donchian"]) == 0
    assert "sma " not in capsys.readouterr().out


def test_cli_paper_prints_fills_and_matches_backtest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = gen(tmp_path)
    capsys.readouterr()
    assert main(["run", "--strategy", "sma", "--data", str(data)]) == 0
    backtest = capsys.readouterr().out
    assert main(["paper", "--strategy", "sma", "--data", str(data), "--speed", "0"]) == 0
    paper = capsys.readouterr().out
    assert "paper mode: simulated fills only" in paper
    assert "[paper] 2024-" in paper and " FILL BUY " in paper
    assert "mode: paper" in paper
    assert paper.split("digest: ")[1].split()[0] == backtest.split("digest: ")[1].split()[0]


def test_cli_reports_gaps(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = gen(tmp_path)
    lines = data.read_text(encoding="utf-8").splitlines(keepends=True)
    del lines[100:103]
    data.write_text("".join(lines), encoding="utf-8", newline="")
    capsys.readouterr()
    assert main(["run", "--strategy", "sma", "--data", str(data)]) == 0
    out = capsys.readouterr().out
    assert "data warning: 1 gap(s), about 3 missing bar(s)" in out
    assert "(3 missing)" in out


def test_cli_errors_exit_with_code_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--strategy", "sma", "--data", str(tmp_path / "missing.csv")]) == 2
    assert "error:" in capsys.readouterr().err
    bad = tmp_path / "bad.csv"
    bad.write_text("timestamp,open,high,low,close,volume\n2024-01-01,1,2,3,1,5\n", encoding="utf-8")
    assert main(["run", "--strategy", "sma", "--data", str(bad)]) == 2
    assert "error: line 2:" in capsys.readouterr().err
    data = gen(tmp_path)
    assert main(["run", "--strategy", "sma", "--data", str(data), "--param", "fast=x"]) == 2
    assert "must be an integer" in capsys.readouterr().err
    for argv in (
        ["run", "--strategy", "nope", "--data", "x"],
        ["gen", "--out", "x.csv"],
        ["run", "--strategy", "sma", "--data", "x", "--param", "oops"],
        ["run", "--strategy", "sma", "--data", "x", "--cash", "lots"],
        [],
    ):
        with pytest.raises(SystemExit) as info:
            main(argv)
        assert info.value.code == 2


def test_cli_version_and_module_entry_point() -> None:
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    src = Path(__file__).resolve().parent.parent / "src"
    done = subprocess.run(
        [sys.executable, "-m", "tickreplay", "--version"],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONPATH": str(src)},
    )
    assert done.stdout.strip() == f"tickreplay {__version__}"
