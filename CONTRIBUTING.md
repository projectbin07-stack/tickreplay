# Contributing

Thanks for taking a look. tickreplay is deliberately small; changes that keep it easy to
read and easy to verify are the most welcome.

## Setup

```bash
git clone https://github.com/projectbin07-stack/tickreplay.git
cd tickreplay
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

## Checks

All four must pass before a pull request is merged; CI runs them on Ubuntu and Windows
with Python 3.10 and 3.13.

```bash
ruff check .
ruff format --check .
mypy
pytest --cov=tickreplay
```

## Ground rules

- **Standard library only at runtime.** Development tools go in the `dev` extra.
- **Money is `Decimal`.** Floats are fine for descriptive statistics in `metrics.py`
  and for drawing charts, never for cash, prices, quantities or P&L.
- **No look-ahead.** Anything that changes when an order may fill needs a test that
  would fail if a strategy could act on data it has not been shown.
- **Determinism.** No wall-clock time, unseeded randomness, or iteration over unordered
  collections in the engine. If results change, the output quoted in `README.md` goes
  stale and `tests/test_docs.py` will tell you.
- **Hand-derived expectations.** A test for a numerical result should show the
  arithmetic in a comment. Do not paste the program's output back in as the expectation.
- **Synthetic data only.** Do not commit real market data. Sample files must come from
  `tickreplay gen` and carry the synthetic-data header.
- **No performance claims.** Example output illustrates the tool, is labelled as
  synthetic, and must be what the tool actually printed.
- **Out of scope:** exchange or broker connectivity, order routing, API keys, wallets.

## Commits and pull requests

Small, focused commits with imperative messages (`Add stop-limit order type`). Describe
what changed and why in the pull request, and update `CHANGELOG.md` under "Unreleased".
