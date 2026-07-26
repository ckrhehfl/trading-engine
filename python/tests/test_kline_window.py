"""Bounds-enforcement tests for `KlineWindow`.

`test_engine.py::test_strategy_only_ever_sees_bars_up_to_and_including_the_current_one`
already proves the end-to-end lookahead guarantee through `run_backtest`.
This file strengthens that with direct, exhaustive tests of `KlineWindow`
itself — every access pattern (positive index, negative index, slice,
iteration) is exercised against its own bounds-violation case, since this
class is what makes lookahead bias structurally impossible rather than
merely avoided by convention.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backtest.kline import Kline
from backtest.kline_window import KlineWindow

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _klines(count: int) -> list[Kline]:
    return [
        Kline(
            open_time=BASE_TIME + timedelta(minutes=15 * i),
            open=Decimal(100 + i),
            high=Decimal(101 + i),
            low=Decimal(99 + i),
            close=Decimal(100 + i) + Decimal("0.5"),
            volume=Decimal("100"),
        )
        for i in range(count)
    ]


def test_len_reflects_configured_length_not_underlying_list_length():
    klines = _klines(10)
    window = KlineWindow(klines, 4)

    assert len(window) == 4


def test_positive_indexing_matches_underlying_list_within_bounds():
    klines = _klines(5)
    window = KlineWindow(klines, 3)

    assert window[0] == klines[0]
    assert window[1] == klines[1]
    assert window[2] == klines[2]


def test_positive_indexing_out_of_range_raises_index_error():
    klines = _klines(5)
    window = KlineWindow(klines, 3)

    with pytest.raises(IndexError):
        window[3]  # klines[3] exists in the underlying list, but is not visible

    with pytest.raises(IndexError):
        window[100]


def test_negative_indexing_is_relative_to_configured_length():
    klines = _klines(5)
    window = KlineWindow(klines, 3)

    assert window[-1] == klines[2]  # last *visible* bar, not klines[-1] (index 4)
    assert window[-2] == klines[1]
    assert window[-3] == klines[0]


def test_negative_indexing_out_of_range_raises_index_error():
    klines = _klines(5)
    window = KlineWindow(klines, 3)

    with pytest.raises(IndexError):
        window[-4]


def test_slice_never_returns_elements_beyond_configured_length():
    klines = _klines(10)
    window = KlineWindow(klines, 4)

    assert list(window[:]) == klines[:4]
    assert list(window[:100]) == klines[:4]
    assert list(window[2:100]) == klines[2:4]
    assert list(window[:-1]) == klines[:3]


def test_slice_with_step_never_returns_elements_beyond_configured_length():
    klines = _klines(10)
    window = KlineWindow(klines, 4)

    assert list(window[::2]) == klines[:4][::2]
    assert list(window[::-1]) == klines[:4][::-1]


def test_iteration_yields_exactly_the_visible_prefix_and_nothing_more():
    klines = _klines(10)
    window = KlineWindow(klines, 4)

    assert list(window) == klines[:4]


def test_iteration_can_be_repeated_and_is_not_exhausted_after_one_pass():
    klines = _klines(5)
    window = KlineWindow(klines, 3)

    assert list(window) == list(window)


def test_zero_length_window_is_empty_and_all_access_raises_or_yields_nothing():
    klines = _klines(5)
    window = KlineWindow(klines, 0)

    assert len(window) == 0
    assert list(window) == []
    assert list(window[:]) == []
    with pytest.raises(IndexError):
        window[0]
    with pytest.raises(IndexError):
        window[-1]


def test_full_length_window_matches_entire_underlying_list():
    klines = _klines(5)
    window = KlineWindow(klines, 5)

    assert list(window) == klines
    assert window[-1] == klines[-1]


def test_mutating_underlying_list_after_construction_is_still_bounds_checked():
    # KlineWindow holds a reference, not a copy — appending to the
    # underlying list after construction must not make the appended bar
    # visible through an already-constructed window.
    klines = _klines(3)
    window = KlineWindow(klines, 3)

    klines.append(
        Kline(
            open_time=BASE_TIME + timedelta(minutes=15 * 3),
            open=Decimal("200"),
            high=Decimal("201"),
            low=Decimal("199"),
            close=Decimal("200.5"),
            volume=Decimal("100"),
        )
    )

    assert len(window) == 3
    with pytest.raises(IndexError):
        window[3]
    assert list(window) == klines[:3]


def test_construction_rejects_length_greater_than_underlying_list():
    klines = _klines(3)

    with pytest.raises(ValueError):
        KlineWindow(klines, 4)


def test_construction_rejects_negative_length():
    klines = _klines(3)

    with pytest.raises(ValueError):
        KlineWindow(klines, -1)


def test_the_sequence_protocol_is_the_only_bounds_checked_access_path_not_the_backing_reference():
    """Documents a known, accepted limitation — not a passing "the bypass
    is prevented" claim, because it isn't prevented, and pretending
    otherwise would be worse than not testing this at all.

    `KlineWindow` is O(1) to construct specifically because it holds a
    live reference to the full underlying list rather than copying the
    visible prefix (see the class docstring's "Scope of the guarantee"
    section). Every access path through the `Sequence[Kline]` protocol
    (`__getitem__`/`__iter__`/`len()` — exercised by every other test in
    this file) is bounds-checked against `length`, but the reference
    itself remains reachable via the `_klines` attribute to anyone who
    reaches around the public interface deliberately. Raised and declined
    as a CodeRabbit review finding on the PR that added this class — see
    `.planning/sr-b-engine-metrics.md` for the full reasoning (a real
    fix requires either an O(n) copy per bar, which reintroduces the
    exact cost this class exists to eliminate, or process-isolating
    strategy execution, disproportionate for this project's trusted,
    first-party research code).
    """
    klines = _klines(10)
    window = KlineWindow(klines, 3)

    assert window._klines is klines
    assert window._klines[5] == klines[5]  # bar 5 is not visible through
    # the Sequence protocol at length=3 (see test_positive_indexing_out_of_range_raises_index_error),
    # yet is reachable this way — the documented, accepted gap.
