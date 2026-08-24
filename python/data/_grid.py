"""Shared time-grid helpers for `python/data/`.

Interval-to-milliseconds mapping and alignment validation, used by
`bingx_klines.py`, `store.py`, and `backfill.py` so the "fail loud on
misaligned input" rule (see `schemas/_types.py`'s `PositiveDecimalString`
and its `allow_inf_nan=False`) is enforced identically in all three
places rather than reimplemented three times with a chance of drifting
apart.

Only `15m` was wired up through Task A (the original scope); `1h` was
added in Strategy Research Task F (see `.planning/sr-f-risk-management-
and-1h-variant.md`) to support a native-1h strategy variant needing real
1h BingX history -- exactly the one-line addition Task A's own docstring
anticipated, not a refactor. `1d` was added the same way in Strategy
Research Task T (see `.planning/sr-t-daily-data-path.md`), to reach the
~2.9 years of daily history that predates every backtest this project
has ever logged -- BingX retains materially more history at coarser
granularity (verified, see CLAUDE.md's "Exchange API Facts"). `5m` (also
already named in CLAUDE.md's Current Scope) remains unwired until
something actually needs it.

`1d`'s step is a plain 86,400,000ms, which makes "grid-aligned" mean
"UTC midnight" -- verified against the live endpoint 2026-07-28, every
returned daily bar's `time` is exactly a multiple of 86,400,000 (BingX
does *not* open its daily candle at a local/exchange-timezone offset,
which would have made this constant wrong while still looking right).

`1m` was added in Scalping Strategy Research Task S1 (see CLAUDE.md's
"Scalping Strategy Research" section) -- the one-line addition this
docstring has anticipated since Task A, not a refactor. Its real BingX
retention depth is a separate, open empirical question (Task S1's own
go/no-go probe) -- wiring the interval into the grid does not itself
say anything about how much real history is actually fetchable at it.
"""

INTERVAL_MS = {
    "1m": 60_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "1d": 86_400_000,
}


def interval_ms(interval: str) -> int:
    try:
        return INTERVAL_MS[interval]
    except KeyError:
        raise ValueError(
            f"unsupported interval: {interval!r}; supported: {sorted(INTERVAL_MS)}"
        ) from None


def require_aligned(name: str, value_ms: int, step_ms: int) -> None:
    """Raise ValueError if `value_ms` doesn't fall exactly on the
    `step_ms` grid. Deliberately raises rather than silently rounding —
    see CLAUDE.md's "Strategy Research Operational Design" data-pipeline
    section and `schemas/_types.py`'s established "fail loud on bad
    input" precedent.
    """
    if value_ms % step_ms != 0:
        raise ValueError(
            f"{name}={value_ms} is not aligned to the {step_ms}ms grid "
            f"(remainder {value_ms % step_ms}, expected 0)"
        )


def require_valid_range(start_ms: int, end_ms: int, step_ms: int) -> None:
    """Validate a half-open `[start_ms, end_ms)` range: both endpoints
    grid-aligned and `start_ms < end_ms`.
    """
    require_aligned("start_ms", start_ms, step_ms)
    require_aligned("end_ms", end_ms, step_ms)
    if start_ms >= end_ms:
        raise ValueError(f"start_ms ({start_ms}) must be < end_ms ({end_ms})")
