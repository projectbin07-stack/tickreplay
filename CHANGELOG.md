# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.1] - 2026-11-24

### Fixed

- Fill prices were always rounded to a hard-coded tick of 0.01, for every instrument and
  even with zero slippage, and the CLI had no way to change it. A market order on an
  instrument quoted at 0.00001234 filled at 0.01 (about 800 times the market price), and
  a 0.4567 buy with no slippage filled at 0.46. Any backtest of an instrument priced
  below about 1.00, or quoted with more than two decimals, produced wrong fill prices,
  fees, P&L and metrics in 0.1.0.
- A fill with zero slippage is now always at its reference price, with or without a
  tick.

### Added

- `--price-tick TICK` and `--price-tick SYMBOL=TICK` (repeatable) for `run`, `compare`
  and `paper`; `EngineConfig.price_ticks` / `SimulatedExecution(price_ticks=...)` set
  the tick per symbol in the API.

### Changed

- The default is now no price rounding (`EngineConfig.price_tick` defaults to `None`):
  slipped fills use exact decimal arithmetic. Replays stay deterministic.
- Because of this, the README quick start (`--slippage-bps 2` on the seed-42 sample)
  prints different numbers. SMA final equity 97,438.69 -> 97,647.99, fees 2,361.38 ->
  2,364.05, digest `980da735...` -> `64f4f6eb...`; Donchian final equity 94,648.41 ->
  94,851.75. Passing `--price-tick 0.01` reproduces the 0.1.0 output exactly.

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

[Unreleased]: https://github.com/projectbin07-stack/tickreplay/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/projectbin07-stack/tickreplay/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/projectbin07-stack/tickreplay/releases/tag/v0.1.0
