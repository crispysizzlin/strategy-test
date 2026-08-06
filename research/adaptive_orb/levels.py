"""Round-number ("even") price levels for index futures.

Equity-index futures cluster stops, targets, and resting liquidity at prices whose
last two digits are 00, 20, 40, 50, 60, or 80. These levels act as short-term
magnets and reaction points. The strategy never *trades* a round number by itself;
it only adjusts already-decided exits:

- Targets that land near a level are shaved a few ticks in front of it, in the
  direction of travel, so the exit rests before the crowd's orders at the level.
- Stops that sit within a few ticks of a level are padded to the far side of it,
  so an ordinary sweep of the level does not take the stop, subject to the
  configured maximum stop distance.

Both adjustments are bounded and deterministic; neither creates or vetoes trades.
"""

from __future__ import annotations

from typing import Iterator

# Offsets within each 100-point century that behave as round levels.
LEVEL_OFFSETS = (0.0, 20.0, 40.0, 50.0, 60.0, 80.0)


def _candidate_levels(price: float) -> Iterator[float]:
    century = (price // 100.0) * 100.0
    for base in (century - 100.0, century, century + 100.0):
        for offset in LEVEL_OFFSETS:
            yield base + offset


def nearest_round_level(price: float) -> float:
    """The round level closest to ``price`` (ties resolve to the lower level)."""

    return min(_candidate_levels(price), key=lambda level: (abs(level - price), level))


def shave_target(
    entry: float,
    target: float,
    direction: int,
    tick_size: float,
    front_ticks: int,
    window_ticks: int,
) -> float:
    """Pull a target that lands near a round level to ``front_ticks`` in front of it.

    Only ever moves the target closer to the entry (never extends reward), and never
    below one tick of profit. ``direction`` is +1 for long, -1 for short.
    """

    if tick_size <= 0:
        raise ValueError("tick_size must be positive")
    level = nearest_round_level(target)
    if abs(target - level) > window_ticks * tick_size:
        return target
    shaved = level - direction * front_ticks * tick_size
    if direction > 0:
        shaved = min(target, shaved)
    else:
        shaved = max(target, shaved)
    if direction * (shaved - entry) < tick_size:
        return target
    return shaved


def pad_stop(
    entry: float,
    stop: float,
    direction: int,
    tick_size: float,
    trigger_ticks: int,
    pad_ticks: int,
    max_stop_ticks: int,
) -> float:
    """Move a stop resting near a round level to ``pad_ticks`` beyond that level.

    A stop within ``trigger_ticks`` of a level is likely to be swept when the level
    is tested. The padded stop is only used when it stays inside ``max_stop_ticks``
    of the entry; otherwise the original stop is kept unchanged. ``direction`` is
    +1 for long, -1 for short.
    """

    if tick_size <= 0:
        raise ValueError("tick_size must be positive")
    level = nearest_round_level(stop)
    if abs(stop - level) > trigger_ticks * tick_size:
        return stop
    padded = level - direction * pad_ticks * tick_size
    # Only move the stop away from the entry; a pad that tightens the stop would
    # silently change the trade's risk basis.
    if direction * (padded - stop) >= 0:
        return stop
    if direction * (entry - padded) / tick_size > max_stop_ticks:
        return stop
    return padded
