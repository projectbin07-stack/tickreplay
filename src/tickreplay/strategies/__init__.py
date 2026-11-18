"""Reference strategies, shipped as examples of the API only.

They are deliberately textbook and untuned. Nothing about their inclusion suggests they
are profitable on real markets.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from tickreplay.strategies.donchian import DonchianBreakout
from tickreplay.strategies.sma import SmaCrossover
from tickreplay.strategy import Strategy

_REGISTRY: dict[str, tuple[Callable[..., Strategy], tuple[str, ...]]] = {
    "sma": (SmaCrossover, ("fast", "slow")),
    "donchian": (DonchianBreakout, ("entry", "exit")),
}


def available() -> tuple[str, ...]:
    """Names accepted by :func:`build`."""
    return tuple(_REGISTRY)


def build(name: str, params: Mapping[str, str] | None = None) -> Strategy:
    """Create a reference strategy from its name and string parameters (all integers)."""
    if name not in _REGISTRY:
        raise ValueError(f"unknown strategy {name!r}; choose from {', '.join(_REGISTRY)}")
    factory, allowed = _REGISTRY[name]
    kwargs: dict[str, int] = {}
    for key, value in (params or {}).items():
        if key not in allowed:
            raise ValueError(
                f"strategy {name!r} has no parameter {key!r} (allowed: {', '.join(allowed)})"
            )
        try:
            kwargs[key] = int(value)
        except ValueError:
            raise ValueError(f"parameter {key}={value!r} must be an integer") from None
    return factory(**kwargs)


__all__ = ["DonchianBreakout", "SmaCrossover", "available", "build"]
