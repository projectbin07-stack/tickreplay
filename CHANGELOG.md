# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-18

First release.

### Added

- Immutable event types (`Bar`, `Trade`, `Signal`, `Order`, `Fill`) and an event queue
  ordered by timestamp, then insertion sequence.
- Strict CSV readers and writers for bars and trade ticks, with line-numbered validation
  errors and gap reporting.
- Seeded synthetic bar generators: geometric Brownian motion and a mean-reverting
  Ornstein-Uhlenbeck process. Two generated sample files under `examples/data/`.
- Execution simulator: market, limit and stop orders, next-bar-open fills, fixed and
  proportional fees, fixed-bps and volume-participation slippage, partial fills capped
  by bar volume.
- Decimal portfolio accounting: positions, average cost, realised and unrealised P&L,
  cash, equity curve, round trips.
- Risk layer: max position, max gross exposure, per-trade risk sizing, daily loss limit
  with optional flattening.
- Strategy API (`on_start`, `on_bar`, `on_trade`, `on_fill`) with two example
  strategies: SMA crossover and Donchian breakout.
- Metrics: total return, CAGR, volatility, Sharpe, Sortino, max drawdown and duration,
  win rate, profit factor, exposure, turnover.
- Paper-trading mode driven by a `MarketFeed`; `ReplayFeed` replays recorded events in
  real or accelerated time.
- CLI: `tickreplay gen`, `run`, `compare`, `paper`; self-contained HTML report with
  inline SVG equity and drawdown charts.

### Not included

- Live trading, exchange connectivity and any key or wallet handling are out of scope.

[Unreleased]: https://github.com/projectbin07-stack/tickreplay/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/projectbin07-stack/tickreplay/releases/tag/v0.1.0
