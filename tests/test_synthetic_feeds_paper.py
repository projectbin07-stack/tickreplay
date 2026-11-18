from __future__ import annotations

import itertools
from datetime import timedelta
from pathlib import Path

import pytest

from helpers import D, flat_bar, tick, ts
from tickreplay import (
    EngineConfig,
    FeeModel,
    Fill,
    HistoricalFeed,
    MarketFeed,
    Mode,
    ReplayFeed,
    run_backtest,
    run_paper,
)
from tickreplay.data import SYNTHETIC_NOTICE, gbm_bars, ou_bars, read_bars, write_bars
from tickreplay.events import Event
from tickreplay.strategies import build

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "data"
YEAR = timedelta(days=365.25)


# -- synthetic generators ---------------------------------------------------------------


def test_generators_are_deterministic_per_seed() -> None:
    assert gbm_bars(seed=9, n=200) == gbm_bars(seed=9, n=200)
    assert gbm_bars(seed=9, n=200) != gbm_bars(seed=10, n=200)
    assert ou_bars(seed=9, n=200) == ou_bars(seed=9, n=200)
    assert ou_bars(seed=9, n=200) != ou_bars(seed=10, n=200)


@pytest.mark.parametrize("make", [gbm_bars, ou_bars])
def test_generated_bars_are_well_formed_and_contiguous(make: object) -> None:
    bars = make(seed=3, n=500, interval=timedelta(minutes=5))  # type: ignore[operator]
    assert len(bars) == 500
    assert bars[0].open == D("100.00")
    for previous, current in itertools.pairwise(bars):
        assert current.ts - previous.ts == timedelta(minutes=5)
        assert current.open == previous.close
    for b in bars:
        assert b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high
        assert b.volume >= 0 and b.volume == b.volume.to_integral_value()
        assert b.close.as_tuple().exponent == -2  # quantised to cents


def test_gbm_without_noise_is_pure_drift() -> None:
    # sigma = 0 leaves S(t) = S0 * exp(mu * t): 100 * e^0.1, e^0.2, e^0.3.
    bars = gbm_bars(seed=1, n=3, interval=YEAR, mu=0.1, sigma=0.0, substeps=2)
    assert [b.close for b in bars] == [D("110.52"), D("122.14"), D("134.99")]
    assert bars[0].high == D("110.52") and bars[0].low == D("100.00")


def test_ou_without_noise_decays_toward_the_mean() -> None:
    # One Euler step with theta * dt = 0.5 halves the log distance to the mean:
    # 200 -> 100 * sqrt(2) = 141.42 -> 100 * 2 ** 0.25 = 118.92.
    bars = ou_bars(
        seed=1,
        n=2,
        interval=YEAR,
        theta=0.5,
        sigma=0.0,
        substeps=1,
        start_price=200.0,
        mean_price=100.0,
    )
    assert [(b.open, b.close) for b in bars] == [
        (D("200.00"), D("141.42")),
        (D("141.42"), D("118.92")),
    ]


def test_ou_stays_near_its_mean_while_gbm_wanders() -> None:
    ou = ou_bars(seed=7, n=1500)
    gbm = gbm_bars(seed=42, n=1500)
    assert D(90) < min(b.low for b in ou) and max(b.high for b in ou) < D(110)
    assert min(b.low for b in gbm) < D(80)


def test_generator_argument_validation() -> None:
    for bad in (
        {"n": 0},
        {"n": 5, "sigma": -1.0},
        {"n": 5, "substeps": 0},
        {"n": 5, "interval": timedelta(0)},
        {"n": 5, "start_price": 0.0},
        {"n": 5, "base_volume": -1},
    ):
        with pytest.raises(ValueError):
            gbm_bars(seed=1, **bad)
    with pytest.raises(ValueError):
        ou_bars(seed=1, n=5, theta=-1.0)
    with pytest.raises(ValueError):
        ou_bars(seed=1, n=5, mean_price=0.0)


@pytest.mark.parametrize(
    ("name", "bars"),
    [
        ("synthetic_gbm_seed42.csv", gbm_bars(seed=42, n=1500)),
        ("synthetic_ou_seed7.csv", ou_bars(seed=7, n=1500)),
    ],
)
def test_shipped_sample_data_is_synthetic_and_reproducible(name: str, bars: list[object]) -> None:
    series = read_bars(EXAMPLES / name)
    assert series.comments[0] == SYNTHETIC_NOTICE
    assert list(series.bars) == bars  # regenerating from the seed gives the committed file
    assert series.gaps == ()


def test_only_synthetic_csv_files_are_shipped() -> None:
    files = sorted(EXAMPLES.glob("*"))
    assert [f.name for f in files] == ["synthetic_gbm_seed42.csv", "synthetic_ou_seed7.csv"]
    for f in files:
        assert f.read_text(encoding="utf-8").startswith(f"# {SYNTHETIC_NOTICE}\n")


# -- feeds ------------------------------------------------------------------------------


def test_historical_feed_sorts_by_time_then_symbol() -> None:
    events = [flat_bar(1, 1, symbol="B"), flat_bar(0, 1, symbol="B"), flat_bar(1, 1, symbol="A")]
    feed = HistoricalFeed(events)
    assert isinstance(feed, MarketFeed) and len(feed) == 3
    assert [(e.ts, e.symbol) for e in feed] == [(ts(0), "B"), (ts(1), "A"), (ts(1), "B")]
    # Ticks sharing a timestamp keep their input order.
    ticks = [tick(0, 3), tick(0, 1), tick(0, 2)]
    assert list(HistoricalFeed(ticks)) == ticks


def test_replay_feed_paces_events_by_speed() -> None:
    bars = [flat_bar(i, 10) for i in (0, 1, 3)]  # gaps of 1h and 2h
    slept: list[float] = []
    feed = ReplayFeed(bars, speed=3600, sleep=slept.append)
    assert isinstance(feed, MarketFeed) and len(feed) == 3
    assert list(feed) == bars
    assert slept == [1.0, 2.0]  # one recorded hour per second

    slept.clear()
    assert list(ReplayFeed(bars, speed=1, sleep=slept.append)) == bars
    assert slept == [3600.0, 7200.0]  # real time

    slept.clear()
    assert list(ReplayFeed(bars, speed=None, sleep=slept.append)) == bars
    assert slept == []

    # Events sharing a timestamp are delivered without sleeping in between.
    slept.clear()
    list(ReplayFeed([tick(0, 1), tick(0, 2), tick(5, 3)], speed=1, sleep=slept.append))
    assert slept == [5.0]

    with pytest.raises(ValueError):
        ReplayFeed(bars, speed=0)


# -- paper mode -------------------------------------------------------------------------


def test_paper_mode_matches_backtest_and_reports_fills_as_they_happen() -> None:
    bars = gbm_bars(seed=21, n=400)
    cfg = EngineConfig(fees=FeeModel(rate_bps=D(5)))
    backtest = run_backtest(bars, build("sma"), cfg)

    slept: list[float] = []
    seen: list[Event] = []
    paper = run_paper(
        ReplayFeed(bars, speed=7200, sleep=slept.append),
        build("sma"),
        cfg,
        listener=seen.append,
    )
    assert paper.mode is Mode.PAPER and backtest.mode is Mode.BACKTEST
    assert len(paper.fills) > 2
    assert paper.digest() == backtest.digest()
    assert slept == [0.5] * 399
    assert tuple(e for e in seen if isinstance(e, Fill)) == paper.fills
    assert len(seen) == paper.events_processed


def test_paper_mode_from_csv_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "bars.csv"
    write_bars(path, ou_bars(seed=2, n=300), comments=[SYNTHETIC_NOTICE])
    series = read_bars(path)
    paper = run_paper(ReplayFeed(series.bars, speed=None), build("donchian"))
    assert paper.digest() == run_backtest(ou_bars(seed=2, n=300), build("donchian")).digest()
