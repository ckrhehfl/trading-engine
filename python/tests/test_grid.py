"""Tests for `python/data/_grid.py`.

`_grid.py` had no dedicated test module before Strategy Research Task T
-- it was exercised only indirectly, through its three consumers
(`bingx_klines.py`, `store.py`, `backfill.py`) and their own test files.
Task T adds the `1d` interval, which is the first grid step whose
arithmetic differs from the others in a way worth pinning down directly
(a 86,400,000ms step means "aligned" is UTC midnight, and a
1000-candle page spans ~2.74 years rather than ~10 days), so the grid
gets its own tests here rather than being asserted only as a side effect
of a kline-fetch test.

The `15m`/`1h` assertions below are deliberately included even though
neither is new: this task's whole risk is that adding a third interval
changes behavior for the two that already exist, and CLAUDE.md's own
15m/1h holdout configs and every logged experiment depend on them.
"""

import pytest

from data._grid import INTERVAL_MS, interval_ms, require_aligned, require_valid_range

DAY_MS = 86_400_000


# ---------------------------------------------------------------------------
# INTERVAL_MS / interval_ms
# ---------------------------------------------------------------------------


def test_interval_ms_supports_1d():
    assert interval_ms("1d") == DAY_MS


def test_interval_ms_still_supports_the_pre_existing_intervals():
    # Byte-for-byte unchanged behavior for the two intervals every
    # existing config, cached row, and logged experiment already uses.
    assert interval_ms("15m") == 900_000
    assert interval_ms("1h") == 3_600_000


def test_interval_map_contains_exactly_the_wired_intervals():
    # `5m` is deliberately still unwired -- see `_grid.py`'s docstring.
    assert set(INTERVAL_MS) == {"15m", "1h", "1d"}


def test_interval_ms_rejects_an_unsupported_interval_and_names_the_supported_ones():
    with pytest.raises(ValueError) as excinfo:
        interval_ms("5m")

    message = str(excinfo.value)
    assert "5m" in message
    assert "1d" in message  # the error lists what *is* supported


def test_one_day_step_is_exactly_ninety_six_fifteen_minute_steps():
    # Not a tautology: it pins the relationship the 15m/1h/1d cache rows
    # share, so a typo'd constant (e.g. 86_400_00) fails loudly here.
    assert interval_ms("1d") == 96 * interval_ms("15m")
    assert interval_ms("1d") == 24 * interval_ms("1h")


# ---------------------------------------------------------------------------
# require_aligned / require_valid_range at daily granularity
# ---------------------------------------------------------------------------


def test_require_aligned_accepts_a_utc_midnight_timestamp_on_the_day_grid():
    # 2024-04-27T00:00:00Z -- the cutoff `configs/research/holdout_1d.json`
    # actually commits to.
    require_aligned("start_ms", 1_714_176_000_000, DAY_MS)


def test_require_aligned_rejects_a_mid_day_timestamp_on_the_day_grid():
    # 2024-04-27T10:00:00Z -- grid-aligned for 1h (and the earliest bar
    # BingX retains at that granularity), but NOT for 1d.
    with pytest.raises(ValueError):
        require_aligned("start_ms", 1_714_212_000_000, DAY_MS)


def test_require_valid_range_accepts_a_day_aligned_half_open_range():
    require_valid_range(1_714_176_000_000, 1_714_176_000_000 + 3 * DAY_MS, DAY_MS)


def test_require_valid_range_rejects_a_day_misaligned_end():
    with pytest.raises(ValueError):
        require_valid_range(1_714_176_000_000, 1_714_176_000_000 + DAY_MS + 3_600_000, DAY_MS)


def test_require_valid_range_rejects_an_inverted_day_range():
    with pytest.raises(ValueError):
        require_valid_range(1_714_176_000_000 + DAY_MS, 1_714_176_000_000, DAY_MS)


def test_every_wired_interval_divides_the_day_evenly():
    # The property that makes a 1d bar's boundaries coincide with a whole
    # number of 15m/1h bars -- relied on implicitly by any future task
    # comparing a daily-bar result against an intraday one.
    for interval, step in INTERVAL_MS.items():
        assert DAY_MS % step == 0, interval
