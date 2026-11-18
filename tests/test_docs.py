"""The documentation's code and claims are executed, not trusted."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def run(*args: str) -> str:
    done = subprocess.run(
        [sys.executable, *args], capture_output=True, text=True, check=True, env=ENV, cwd=ROOT
    )
    return done.stdout


def test_readme_python_snippet_runs() -> None:
    (snippet,) = re.findall(r"```python\n(.*?)```", README, flags=re.DOTALL)
    fills, equity, digest = run("-c", snippet).split()
    assert int(fills) > 0 and float(equity) > 0 and len(digest) == 64


def test_custom_strategy_example_runs() -> None:
    out = run("examples/custom_strategy.py")
    assert out.startswith("SYNTHETIC data")
    assert re.search(r"digest=[0-9a-f]{64}", out)


def test_readme_quick_start_output_is_what_the_tool_prints(tmp_path: Path) -> None:
    data = tmp_path / "demo.csv"
    report = tmp_path / "report.html"
    run("-m", "tickreplay", "gen", "--model", "gbm", "--seed", "42", "--bars", "1500",
        "--out", str(data))  # fmt: skip
    costs = ["--fee-bps", "5", "--slippage-bps", "2"]
    out = run("-m", "tickreplay", "run", "--strategy", "sma", "--data", str(data),
              "--report", str(report), *costs)  # fmt: skip
    out += run("-m", "tickreplay", "compare", "--data", str(data), *costs)
    printed = [line for line in out.splitlines() if not line.startswith("report: ")]
    assert len(printed) > 30
    for line in printed:
        assert line in README, f"README quick start is stale; missing line: {line!r}"


def test_readme_help_block_matches_cli() -> None:
    for line in run("-m", "tickreplay", "--help").splitlines():
        assert line in README, f"README --help block is stale; missing line: {line!r}"


def test_readme_carries_the_disclaimer_and_scope() -> None:
    for phrase in (
        "research and education software",
        "synthetic data",
        "Nothing here is financial advice",
        "Live trading is out of scope",
    ):
        assert phrase in README
