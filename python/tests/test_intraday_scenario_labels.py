from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research.intraday_ed_inputs import MINUTE, InputRefusal, Minute, SessionInputs
from research.intraday_scenario_labels import PathQuestion, label_horizons, label_path


OPEN = datetime(2099, 1, 5, 9, tzinfo=timezone(timedelta(hours=9)))


def at(offset):
    return OPEN + offset * MINUTE


def question():
    return PathQuestion(at(0), at(30), 100, Fraction(1), Decimal("105"), 95)


def row(offset, prices=(103, 104, 102, 103), *, available=None):
    return Minute("INVENTED", at(offset), at(offset + 3 if available is None else available),
                  tuple(map(Decimal, prices)), None, volume_state="missing")


def session(*changes):
    minutes = {at(offset): row(offset) for offset in range(30)}
    minutes.update((item.start_at, item) for item in changes)
    return SessionInputs("INVENTED", OPEN, at(60), minutes)


@pytest.mark.parametrize(("prices", "path", "reason"), [
    ((103, 106, 94, 103), None, "same_minute_target_stop_order_unknown"),
    ((105, 106, 94, 103), "P1", None),
    ((95, 106, 94, 103), "P3", None),
    ((103, 106, 100, 103), None, "same_minute_retest_target_order_unknown"),
    ((101, 106, 100, 103), "P2", None),
    ((105, 106, 100, 103), "P1", None),
])
def test_only_opening_print_can_disambiguate_competing_same_minute_events(prices, path, reason):
    result = label_path(question(), 103, session(row(0, prices)), cutoff_at=at(4))
    assert result.status == ("resolved" if path else "unknown")
    assert (result.path, result.reason, result.observed_through, result.mature_at) == (
        path, reason, at(1), at(3))


def test_starting_inside_band_and_visits_before_departure_do_not_create_retest():
    value = session(row(0, (100, 102, 99, 101)), row(1, (101, 104, 100, 103)),
                    row(2, (103, 105, 102, 104)))
    result = label_path(question(), 100, value, cutoff_at=at(7))
    assert (result.path, result.departed, result.retested) == ("P1", True, False)


def test_departure_requires_a_close_strictly_above_band_and_retest_follows_it():
    value = session(row(0, (100, 104, 99, 101)), row(1, (101, 104, 100, 103)),
                    row(2, (103, 104, 101, 102)), row(3, (103, 105, 102, 104)))
    before_retest = label_path(question(), 100, value, cutoff_at=at(4))
    after_retest = label_path(question(), 100, value, cutoff_at=at(5))
    resolved = label_path(question(), 100, value, cutoff_at=at(6))
    assert (before_retest.status, before_retest.departed, before_retest.retested) == ("pending", True, False)
    assert (after_retest.status, after_retest.departed, after_retest.retested) == ("pending", True, True)
    assert (resolved.status, resolved.path) == ("resolved", "P2")
    assert before_retest.question.question_id == after_retest.question.question_id == resolved.question.question_id


def test_missing_prefix_is_unknown_and_cannot_use_later_target():
    value = session(row(3, (103, 106, 102, 103)))
    del value.minutes[at(2)]
    result = label_path(question(), 103, value, cutoff_at=at(32))
    assert (result.status, result.path, result.reason, result.observed_through) == (
        "unknown", None, "minute_not_observed_at_cutoff", at(2))


def test_explicit_price_unknown_differs_from_a_bar_not_yet_available():
    unavailable = replace(row(0), ohlc=None, price_state="halt")
    value = session(unavailable)
    pending = label_path(question(), 103, value, cutoff_at=at(2))
    unknown = label_path(question(), 103, value, cutoff_at=at(3))
    assert (pending.status, pending.reason) == ("pending", "minute_not_due_at_cutoff")
    assert (unknown.status, unknown.path, unknown.reason) == ("unknown", None, "price_halt")


def test_unobserved_late_and_missing_rows_have_same_prefix_before_and_after_due_time():
    missing = session()
    del missing.minutes[at(0)]
    late = session(row(0, (103, 106, 102, 105), available=8))
    for cutoff in (0, 1, 2, 3, 7):
        assert label_path(question(), 103, missing, cutoff_at=at(cutoff)) == label_path(
            question(), 103, late, cutoff_at=at(cutoff))
    assert label_path(question(), 103, late, cutoff_at=at(8)).path == "P1"


def test_pending_minute_is_never_looked_up_and_schedule_floor_sets_maturity():
    class NoLookup(dict):
        def get(self, key, default=None):
            raise AssertionError("unpublished suffix was inspected")

    value = replace(session(), minutes=NoLookup())
    assert label_path(question(), 103, value, cutoff_at=at(2)).status == "pending"
    early = session(row(0, (103, 106, 102, 105), available=1))
    assert label_path(question(), 103, early, cutoff_at=at(3)).mature_at == at(3)
    assert label_path(question(), 103, early, cutoff_at=at(1), availability_lag=timedelta(0)).mature_at == at(1)


def test_completed_short_horizon_and_later_stop_use_one_path_without_erasing_first_stop():
    value = session(row(8, (103, 104, 95, 102), available=13),
                    row(9, (102, 107, 102, 106)))
    del value.minutes[at(10)]
    results = label_horizons(question(), 103, value, cutoff_at=at(32))
    assert [result.path for result in results] == ["P4", "P3", "P3"]
    assert [result.mature_at for result in results] == [at(7), at(13), at(13)]
    assert [result.observed_through for result in results] == [at(5), at(9), at(9)]
    assert results[-1] == label_path(question(), 103, value, cutoff_at=at(14))


def test_complete_no_touch_and_immature_suffix_differ_at_absolute_deadline():
    value = session()
    partial = label_horizons(question(), 103, value, cutoff_at=at(31))
    complete = label_horizons(question(), 103, value, cutoff_at=at(32))
    assert [result.path for result in partial] == ["P4", "P4", None]
    assert [result.path for result in complete] == ["P4", "P4", "P4"]
    assert partial[-1].status == "pending"


def test_opening_target_ignores_any_suffix_after_resolution():
    class NoSuffix(dict):
        def get(self, key, default=None):
            assert key == at(0)
            return super().get(key, default)

    value = session(row(0, (105, 106, 94, 103)))
    result = label_horizons(question(), 103, replace(value, minutes=NoSuffix(value.minutes)), cutoff_at=at(32))
    assert [item.path for item in result] == ["P1", "P1", "P1"]


def test_question_identity_uses_absolute_clocks_and_exact_boundaries():
    base = question()
    equivalent = PathQuestion(at(0).astimezone(UTC), at(30).astimezone(UTC), Decimal("100.00"),
                              Decimal("1.00"), Fraction(105), Decimal("95.0"))
    assert equivalent.question_id == base.question_id
    assert base.forecast_id(at(0)) != base.forecast_id(at(5))
    assert base.question_id != replace(base, start_at=at(5), end_at=at(35)).question_id
    assert base.question_id != replace(base, target=Fraction(105) + Fraction(1, 10**30)).question_id
    assert base.question_id != replace(base, end_at=at(29)).question_id
    with pytest.raises(InputRefusal, match="exact_finite_price_required"):
        replace(base, target=105.0)


@pytest.mark.parametrize(("start_close", "reason"), [
    (None, "missing_start_close"), (105, "unsupported_price_geometry"), (95, "unsupported_price_geometry"),
])
def test_unsupported_initial_price_has_no_path(start_close, reason):
    result = label_path(question(), start_close, session(), cutoff_at=at(32))
    assert (result.status, result.path, result.reason) == ("unsupported", None, reason)
