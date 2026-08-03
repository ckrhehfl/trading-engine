"""Tests for `python/research/macro_alignment.py` -- Strategy Research Task X
(the macro-real-yield-trend strategy; see `.planning/sr-x-macro-real-yield-
strategy.md`). Written first (TDD): this file existed and failed on
`ModuleNotFoundError` before `research/macro_alignment.py` did.

**Synthetic fixtures only.** No test in this file loads, queries, or
touches `python/data/var/klines.sqlite3` or any real BTC/FRED data --
every `Kline`/`ObservationRow` here is hand-built. The alignment logic is
generic and has nothing to do with which BTC data split (research vs.
holdout) a caller eventually feeds it, so there is no need for -- and no
excuse for -- exercising it against real data in a unit test.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backtest.kline import Kline
from data.fred_client import ObservationRow
from research.macro_alignment import MacroSeriesCursor, forward_fill_macro_series

BASE_DATE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _kline(day_offset: int, base: datetime = BASE_DATE) -> Kline:
    price = Decimal("100")
    return Kline(
        open_time=base + timedelta(days=day_offset),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("1"),
    )


def _klines(day_offsets: list[int], base: datetime = BASE_DATE) -> list[Kline]:
    return [_kline(offset, base) for offset in day_offsets]


def _obs(date: str, value: str | None) -> ObservationRow:
    return ObservationRow(observation_date=date, value=None if value is None else Decimal(value))


class TestForwardFillBasic:
    def test_exact_date_match_uses_that_days_value(self):
        klines = _klines([0])  # 2024-01-01
        observations = [_obs("2024-01-01", "4.00")]
        result = forward_fill_macro_series(klines, observations)
        assert result == [Decimal("4.00")]

    def test_forward_fills_the_last_known_value_across_a_gap(self):
        # Observations only on day 0 and day 3 -- days 1 and 2 (e.g. a
        # weekend) have no row at all in the underlying series.
        klines = _klines([0, 1, 2, 3])
        observations = [_obs("2024-01-01", "4.00"), _obs("2024-01-04", "4.50")]
        result = forward_fill_macro_series(klines, observations)
        assert result == [Decimal("4.00"), Decimal("4.00"), Decimal("4.00"), Decimal("4.50")]

    def test_returns_none_before_any_real_observation_is_available(self):
        # The kline sequence starts before the macro series has any data
        # at all -- there is nothing to forward-fill from yet.
        klines = _klines([0, 1, 2])
        observations = [_obs("2024-01-03", "4.00")]
        result = forward_fill_macro_series(klines, observations)
        assert result == [None, None, Decimal("4.00")]

    def test_empty_observations_yields_all_none(self):
        klines = _klines([0, 1, 2])
        result = forward_fill_macro_series(klines, [])
        assert result == [None, None, None]

    def test_empty_klines_yields_empty_result(self):
        observations = [_obs("2024-01-01", "4.00")]
        result = forward_fill_macro_series([], observations)
        assert result == []


class TestNullValuesAreForwardFilledThrough:
    def test_a_holiday_null_row_does_not_overwrite_the_last_real_value(self):
        # 2024-01-02 is a real FRED row (a market holiday) with value=None
        # -- fred_client.py's own "." marker -- not an omitted row. The
        # forward-filled reading for that day, and every day until the
        # next real value, must still be the prior real value.
        klines = _klines([0, 1, 2])
        observations = [_obs("2024-01-01", "4.00"), _obs("2024-01-02", None), _obs("2024-01-03", "4.25")]
        result = forward_fill_macro_series(klines, observations)
        assert result == [Decimal("4.00"), Decimal("4.00"), Decimal("4.25")]

    def test_leading_null_rows_before_any_real_value_still_yield_none(self):
        klines = _klines([0, 1])
        observations = [_obs("2024-01-01", None), _obs("2024-01-02", "4.00")]
        result = forward_fill_macro_series(klines, observations)
        assert result == [None, Decimal("4.00")]


class TestLookAheadSafety:
    def test_a_kline_never_sees_an_observation_dated_after_itself(self):
        # The cursor is constructed with the FULL observation history,
        # including dates far in the "future" relative to the klines it
        # will actually be fed -- exactly the real-world shape (a strategy
        # loading the whole DFII10 series once, then walking BTC bars that
        # only cover a small slice of it). Every returned value must come
        # from a date <= the kline's own date, never later.
        klines = _klines([0, 1])  # 2024-01-01, 2024-01-02
        observations = [
            _obs("2024-01-01", "4.00"),
            _obs("2024-01-02", "4.10"),
            _obs("2024-01-03", "99.00"),  # "the future" relative to both klines
            _obs("2024-06-01", "-50.00"),  # far future -- must never leak in
        ]
        result = forward_fill_macro_series(klines, observations)
        assert result == [Decimal("4.00"), Decimal("4.10")]

    def test_incremental_cursor_never_reveals_a_later_dated_row_early(self):
        cursor = MacroSeriesCursor(
            [
                _obs("2024-01-01", "4.00"),
                _obs("2024-01-05", "9.99"),
            ]
        )
        # Bars 2024-01-01 through 2024-01-04 must all read 4.00 (the last
        # real value at/before their own date), never the 2024-01-05 row.
        for offset in range(4):
            value = cursor.update(_kline(offset))
            assert value == Decimal("4.00"), f"offset={offset} leaked a future-dated value"
        # Only once the cursor is actually fed the 2024-01-05 bar itself
        # does the later value become visible.
        assert cursor.update(_kline(4)) == Decimal("9.99")


class TestOutOfOrderObservationsAreSortedDefensively:
    def test_observations_supplied_out_of_chronological_order_still_align_correctly(self):
        klines = _klines([0, 1, 2])
        observations = [
            _obs("2024-01-03", "4.30"),
            _obs("2024-01-01", "4.00"),  # deliberately out of order
        ]
        result = forward_fill_macro_series(klines, observations)
        assert result == [Decimal("4.00"), Decimal("4.00"), Decimal("4.30")]


class TestCursorMirrorsBatchHelper:
    def test_feeding_the_cursor_one_kline_at_a_time_matches_the_batch_function(self):
        klines = _klines([0, 1, 2, 3, 5])  # deliberately skips day 4
        observations = [
            _obs("2024-01-01", "4.00"),
            _obs("2024-01-02", None),
            _obs("2024-01-03", "4.10"),
            _obs("2024-01-06", "4.50"),
        ]
        batch_result = forward_fill_macro_series(klines, observations)

        cursor = MacroSeriesCursor(observations)
        incremental_result = [cursor.update(k) for k in klines]

        assert incremental_result == batch_result
