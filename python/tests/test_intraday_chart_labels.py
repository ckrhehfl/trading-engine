"""Invented-only EF supervised labels and cutoff boundaries."""

from collections.abc import Mapping
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research.intraday_chart_labels import MinuteEvidence, build_chart_labels
from research.intraday_ed_inputs import MINUTE, InputRefusal, Minute, SessionInputs


OPEN = datetime(2026, 1, 30, 9, tzinfo=timezone(timedelta(hours=9)))
D = Decimal


def at(offset):
    return OPEN + offset * MINUTE


def session(*, closing=390):
    prices = tuple(map(D, ("100.25", "101.25", "99.25", "100.50")))
    rows = {at(offset): Minute("TARGET", at(offset), at(offset + 3), prices, None,
                               volume_state="missing") for offset in range(63, 94)}
    rows[at(70)] = replace(rows[at(70)], ohlc=(D("100.25"), D(103), D("95.125"), D(102)))
    rows[at(78)] = replace(rows[at(78)], ohlc=(D("106.125"), D(130), D(70), D(100)))
    rows[at(93)] = replace(rows[at(93)], ohlc=(D("97.25"), D(999), D(1), D(100)))
    return SessionInputs("TARGET", OPEN, at(closing), rows)


def labels(value, *, cutoff=at(96), bar_end=at(60), decision=at(62)):
    return build_chart_labels(value, bar_end, decision, label_cutoff=cutoff)


def change(value, offset, **updates):
    rows = dict(value.minutes)
    rows[at(offset)] = replace(rows[at(offset)], **updates)
    return replace(value, minutes=rows)


def remove(value, offset):
    rows = dict(value.minutes)
    del rows[at(offset)]
    return replace(value, minutes=rows)


class ExactLookups(Mapping):
    def __init__(self, rows, expected):
        self.rows = rows
        self.expected = iter(expected)

    def __getitem__(self, key):
        assert key == next(self.expected, None), f"unexpected label lookup {key}"
        return self.rows[key]

    def __iter__(self):
        raise AssertionError("whole-session iteration")

    def __len__(self):
        raise AssertionError("whole-session length")

    def assert_finished(self):
        assert next(self.expected, None) is None


def test_exact_endpoint_and_path_results_share_one_entry_and_read_each_minute_once():
    value = session()
    lookups = ExactLookups(value.minutes, (at(i) for i in range(63, 94)))
    result = labels(replace(value, minutes=lookups))
    lookups.assert_finished()
    short, long = result.horizons
    assert (result.symbol, result.opening, result.closing) == ("TARGET", OPEN, at(390))
    assert (result.bar_end, result.decision_at, result.label_cutoff) == (at(60), at(62), at(96))
    assert (short.horizon_minutes, long.horizon_minutes) == (15, 30)
    assert short.entry_at == long.entry_at == at(63)
    assert short.entry_price == long.entry_price == Fraction(401, 4)
    assert (short.exit_at, short.exit_price, short.gross_return) == (at(78), Fraction(849, 8), Fraction(47, 802))
    assert (long.exit_at, long.exit_price, long.gross_return) == (at(93), Fraction(389, 4), Fraction(-12, 401))
    assert (short.path.low_return, short.path.high_return) == (Fraction(-41, 802), Fraction(47, 802))
    assert (long.path.low_return, long.path.high_return) == (Fraction(-121, 401), Fraction(119, 401))
    for label in (short, long):
        assert label.status == label.path.status == "available"
        assert label.issues == label.path.issues == ()
        assert label.mature_at == label.path.mature_at == label.exit_at + 3 * MINUTE
        assert label.evidence == (MinuteEvidence(at(63), at(66)),
                                  MinuteEvidence(label.exit_at, label.mature_at))
        assert label.path.evidence == tuple(MinuteEvidence(at(i), at(i + 3))
                                            for i in range(63, 64 + label.horizon_minutes))
    with pytest.raises(FrozenInstanceError):
        short.exit_price = Fraction(1)


def test_mature_short_and_unmatured_long_only_read_at_or_before_cutoff():
    value = session()
    lookups = ExactLookups(value.minutes, (at(i) for i in range(63, 79)))
    short, long = labels(replace(value, minutes=lookups), cutoff=at(81)).horizons
    lookups.assert_finished()
    assert short.status == short.path.status == "available"
    assert long.status == long.path.status == "unmatured"
    assert long.entry_price == short.entry_price
    assert long.exit_price is long.gross_return is long.mature_at is None
    assert [(issue.minute, issue.reason) for issue in long.issues] == [(at(93), "minute_not_mature_at_cutoff")]
    assert long.path.evidence == short.path.evidence


@pytest.mark.parametrize("cutoff", [at(62), at(66) - timedelta(microseconds=1)])
def test_pre_entry_evidence_cutoff_does_not_lookup_any_minute(cutoff):
    value = session()
    lookups = ExactLookups(value.minutes, ())
    for label in labels(replace(value, minutes=lookups), cutoff=cutoff).horizons:
        assert label.status == label.path.status == "unmatured"
        assert label.entry_price is label.exit_price is label.gross_return is None
        assert label.evidence == label.path.evidence == ()


def test_late_typed_availability_controls_maturity_without_using_late_payload():
    value = change(session(), 78, available_at=at(86))
    value = change(value, 70, available_at=at(88))
    late = change(value, 78, ohlc=(D(-1),) * 4)
    short, _ = labels(late, cutoff=at(85)).horizons
    assert short.status == "unmatured" and short.exit_price is None
    assert [issue.reason for issue in short.issues] == ["minute_available_after_cutoff"]
    assert [point.start_at for point in short.evidence] == [at(63)]
    short, _ = labels(value, cutoff=at(86)).horizons
    assert short.status == "available" and short.mature_at == at(86)
    assert short.path.status == "unmatured" and short.path.mature_at is None
    assert [(issue.minute, issue.reason) for issue in short.path.issues] == [(at(70), "minute_available_after_cutoff")]
    short, _ = labels(value, cutoff=at(88)).horizons
    assert short.path.status == "available" and short.path.mature_at == at(88)


def test_missing_interior_preserves_both_endpoint_returns():
    short, long = labels(remove(session(), 70)).horizons
    for label in (short, long):
        assert label.status == "available" and label.gross_return is not None
        assert label.issues == ()
        assert label.path.status == "unavailable"
        assert label.path.low_return is label.path.high_return is label.path.mature_at is None
        assert [(issue.minute, issue.reason) for issue in label.path.issues] == [(at(70), "missing_minute")]


def test_absent_mature_long_exit_is_missing_while_short_is_available():
    short, long = labels(remove(session(), 93)).horizons
    assert short.status == short.path.status == "available"
    assert long.status == long.path.status == "unavailable"
    assert long.entry_price == short.entry_price
    assert long.exit_price is long.gross_return is long.mature_at is None
    assert [(issue.minute, issue.reason) for issue in long.issues] == [(at(93), "missing_minute")]


def test_nonobserved_entry_retains_independently_known_exits():
    value = change(session(), 63, price_state="halt", ohlc=None)
    for label in labels(value).horizons:
        assert label.status == label.path.status == "unavailable"
        assert label.entry_price is label.gross_return is None and label.exit_price is not None
        assert [issue.reason for issue in label.issues] == ["price_halt"]


@pytest.mark.parametrize("closing, allowed", [(93, 0), (108, 15), (109, 30)])
def test_continuous_cash_boundary_is_strict_with_no_fallback(closing, allowed):
    value = session(closing=closing)
    expected = (at(i) for i in range(63, 64 + allowed)) if allowed else ()
    lookups = ExactLookups(value.minutes, expected)
    short, long = labels(replace(value, minutes=lookups)).horizons
    lookups.assert_finished()
    assert short.entry_at == long.entry_at == at(63)
    assert (short.exit_at, long.exit_at) == (at(78), at(93))
    for label in (short, long):
        if label.horizon_minutes <= allowed:
            assert label.status == "available"
        else:
            assert label.status == label.path.status == "outside_clock"
            assert label.entry_price is label.exit_price is label.gross_return is label.mature_at is None
            assert label.evidence == label.path.evidence == ()
            assert [(issue.minute, issue.reason) for issue in label.issues] == [
                (label.exit_at, "exit_outside_continuous_cash_clock")]


def test_holding_grid_at_close_preserves_scheduled_clocks_without_accessing_prices():
    value = replace(session(), minutes=ExactLookups({}, ()))
    short, long = labels(value, bar_end=at(390), decision=at(392)).horizons
    assert short.status == long.status == "outside_clock"
    assert short.entry_at == long.entry_at == at(393)
    assert (short.exit_at, long.exit_at) == (at(408), at(423))


@pytest.mark.parametrize("bar_end, decision, cutoff", [
    (at(55), at(57), at(96)),
    (at(61), at(63), at(96)),
    (at(395), at(397), at(420)),
    (at(60), at(61), at(96)),
    (at(60), at(62).replace(second=1), at(96)),
    (at(60).replace(tzinfo=None), at(62), at(96)),
    (at(60), at(62), at(96).replace(tzinfo=None)),
])
def test_invalid_decision_geometry_or_cutoff_refuses_before_any_lookup(bar_end, decision, cutoff):
    value = replace(session(), minutes=ExactLookups({}, ()))
    with pytest.raises(InputRefusal):
        labels(value, bar_end=bar_end, decision=decision, cutoff=cutoff)


@pytest.mark.parametrize("updates", [
    {"opening": OPEN.replace(second=1)},
    {"closing": at(390).replace(tzinfo=None)},
    {"closing": at(390) + timedelta(days=1)},
    {"closing": OPEN},
    {"symbol": ""},
    {"minutes": []},
])
def test_invalid_cash_session_geometry_or_identity(updates):
    value = replace(session(), minutes=ExactLookups({}, ()))
    with pytest.raises(InputRefusal):
        labels(replace(value, **updates))


@pytest.mark.parametrize("updates, reason", [
    ({"start_at": at(64)}, "minute_identity_changed"),
    ({"start_at": at(63).replace(tzinfo=None)}, "aware_clock_required"),
    ({"symbol": "OTHER"}, "minute_identity_changed"),
    ({"available_at": at(66).replace(tzinfo=None)}, "aware_clock_required"),
    ({"available_at": at(65)}, "minute_available_before_label_floor"),
    ({"price_state": "unknown"}, "invalid_observation_state"),
    ({"price_state": "missing"}, "unknown_observation_has_payload"),
    ({"ohlc": None}, "observed_price_requires_ohlc"),
    ({"ohlc": (100.25, D(102), D(99), D(100))}, "invalid_exact_price"),
    ({"ohlc": (D(100), D("NaN"), D(99), D(100))}, "invalid_exact_price"),
    ({"ohlc": (D(100), D(102), D(0), D(100))}, "invalid_exact_price"),
    ({"ohlc": (D(100), D(99), D(90), D(101))}, "invalid_ohlc_order"),
])
def test_mature_malformed_observations_are_refused(updates, reason):
    with pytest.raises(InputRefusal, match=reason):
        labels(change(session(), 63, **updates))
