"""Invented independent EL reconstruction, mutation and prefix tests."""

from dataclasses import replace
from fractions import Fraction
import random

import pytest

from research import intraday_event_scenarios as producer
from research import intraday_event_scenarios_reference as reference
from research.intraday_event_scenarios import MinuteBar


SERIES = "invented:one-session:version1"


def bar(index, close=1000, *, low=None, high=None, arrival=None):
    return MinuteBar(index, close, close + 5 if high is None else high,
                     close - 5 if low is None else low, close,
                     index + 3 if arrival is None else arrival)


def path(family, length=65):
    rows = [bar(i) for i in range(length)]
    if family == "A_DOWNFAIL":
        changes = [(21, 990, 988, 1000), (22, 980, 978, 990), (23, 965, 963, 980),
                   (24, 966, Fraction(1927, 2), 970), (25, 967, 963, 971), (26, 974, 964, 976)]
    elif family == "B_ORL30":
        changes = [(33, 990, 988, 1000), (34, 991, 987, 994), (35, 998, 990, 1000)]
    else:
        changes = [(21, 1005, 1000, 1010), (22, 1010, 1005, 1015), (23, 1015, 1010, 1020),
                   (24, 1020, 1015, 1025), (25, 1035, 1030, 1040), (26, 1040, 1038, 1042),
                   (27, 1036, 1035, 1041), (28, 1034, 1030, 1039), (29, 1043, 1036, 1044)]
    for i, close, low, high in changes:
        if i < length:
            rows[i] = bar(i, close, low=low, high=high)
    return rows


def scan(rows, end=None, length=90):
    end = max((row.offset for row in rows), default=-1) + 1 if end is None else end
    produced = producer.scan_scenarios(rows, end_offset=end, session_length=length, series_id=SERIES)
    reference.verify(produced, iter(rows), end_offset=end, session_length=length, series_id=SERIES)
    return produced


@pytest.mark.parametrize("family,onset,terminal", [
    ("A_DOWNFAIL", 23, 26), ("B_ORL30", 33, 35), ("C_FIRSTPULLBACK", 25, 29),
])
def test_independent_positive_scene_and_broad_census(family, onset, terminal):
    projection = scan(path(family, terminal + 1))
    frames = [frame for frame in projection.frames if frame.family == family]
    assert frames[0].offset == onset and frames[-1].offset == terminal
    assert frames[-1].state == "CONFIRMED"
    assert frames[0].scale == 10
    if family == "B_ORL30":
        assert dict(frames[0].conditions)["opening_range_bps"] == 100
    else:
        assert dict(frames[0].conditions)["meets_minimum_past_move"] is True


@pytest.mark.parametrize("family,end", [("A_DOWNFAIL", 36), ("B_ORL30", 54), ("C_FIRSTPULLBACK", 38)])
def test_independent_episode_expiry_and_scheduled_frames(family, end):
    rows = path(family, end)
    start, close, low, high = {
        "A_DOWNFAIL": (24, 966, 963, 971),
        "B_ORL30": (34, 990, 988, 992),
        "C_FIRSTPULLBACK": (28, 1034, 1030, 1039),
    }[family]
    for index in range(start, end):
        rows[index] = bar(index, close, low=low, high=high)
    frames = [frame for frame in scan(rows).frames if frame.family == family]
    assert frames[-1].state == "EXPIRED_NO_CONFIRMATION"
    assert any(frame.kind == "scheduled" for frame in frames)


def test_no_pullback_expiry_and_failure_priority_are_separate():
    rows = path("C_FIRSTPULLBACK", 36)
    for index in range(26, 36):
        value = 1035 + (index - 25) * 6
        rows[index] = bar(index, value, high=value + 2)
    assert [f for f in scan(rows).frames if f.family == "C_FIRSTPULLBACK"][-1].state == "EXPIRED_NO_PULLBACK"
    for family, last, invalid_low in (("A_DOWNFAIL", 26, 959), ("C_FIRSTPULLBACK", 29, 1019)):
        rows = path(family, last + 1)
        rows[last] = replace(rows[last], low=Fraction(invalid_low))
        terminal = [f for f in scan(rows).frames if f.family == family][-1]
        assert terminal.state == "INVALIDATED"
        assert dict(terminal.conditions)["reclaimed_prior_highs"] is True


@pytest.mark.parametrize("gap", [0, 1, 22, 24, 25, 26, 29, 34, 35, 64])
def test_missing_history_active_gap_and_trailing_watermark_are_distinct(gap):
    for family in reference.FAMILIES:
        rows = [row for row in path(family) if row.offset != gap]
        projection = scan(rows, end=65)
        missing = [row for row in projection.support if row.offset == gap]
        assert len(missing) == 3 and {row.status for row in missing} == {"MISSING_BAR"}
        assert len(projection.support) == 195


def test_zero_scale_support_and_explicit_session_end():
    rows = [bar(i, low=1000, high=1000) for i in range(40)]
    projection = scan(rows)
    assert not projection.frames and not projection.broad_observations
    assert "ZERO_SCALE" in {row.status for row in projection.support}
    rows = path("A_DOWNFAIL", 24)
    prefix = scan(rows, length=90)
    ended = scan(rows, length=24)
    assert not prefix.frames[-1].terminal
    assert [(row.kind, row.state) for row in ended.frames[-2:]] == [
        ("at_onset", "WAITING"), ("terminal", "SESSION_END")]


def test_late_minutes_delay_decisions_and_opening_range_remains_preknown():
    rows = path("B_ORL30", 44)
    rows[29] = replace(rows[29], available_offset=40)
    rows[41] = bar(41, 990, low=988, high=1000)
    rows[42] = bar(42, 998)
    result = scan(rows)
    b = [f for f in result.frames if f.family == "B_ORL30"]
    assert b[0].offset == 41 and dict(b[0].conditions)["level_known_at"] == 40
    assert all(row.decision_offset >= 40 for row in result.support if row.offset >= 29)


@pytest.mark.parametrize("family,end,direction", [("A_DOWNFAIL", 24, -1), ("C_FIRSTPULLBACK", 26, 1)])
def test_broad_observation_denominator_survives_past_move_floor(family, end, direction):
    rows = [bar(i, low=Fraction(19999, 20), high=Fraction(20001, 20)) for i in range(end)]
    for index in range(21, end):
        close = 1000 + Fraction(direction * (index - 20), 5)
        rows[index] = bar(index, close, low=close - Fraction(1, 20), high=close + Fraction(1, 20))
    projection = scan(rows)
    broad = [row for row in projection.broad_observations if row.family == family]
    assert broad and not any(row.meets_minimum_past_move for row in broad)
    assert not [frame for frame in projection.frames if frame.family == family]
    assert any(row.status == "below_past_move_floor" for row in projection.support)
    for move, expected in ((Fraction(33, 5), True), (Fraction(6599, 1000), False)):
        close = 1000 + direction * move
        rows[-1] = bar(end - 1, close, low=close - Fraction(1, 20), high=close + Fraction(1, 20))
        result = scan(rows)
        assert bool([f for f in result.frames if f.family == family]) is expected


@pytest.mark.parametrize("family", reference.FAMILIES)
def test_reference_prefix_invariance_with_late_bars_missingness_and_new_episodes(family):
    rows = path(family, 65)
    rows[20] = replace(rows[20], available_offset=48)
    rows = [row for row in rows if row.offset not in (28, 50)]
    complete = reference.reference_scan(rows, end_offset=65, session_length=90, series_id=SERIES)
    for end in range(66):
        prior = [row for row in rows if row.offset < end]
        rebuilt = reference.reference_scan(prior, end_offset=end, session_length=90, series_id=SERIES)
        for name in ("frames", "support", "broad_observations"):
            assert getattr(rebuilt, name) == tuple(row for row in getattr(complete, name) if row.offset < end)
        scan(prior, end=end)


def test_reference_does_not_call_producer_science_helpers(monkeypatch):
    rows = path("C_FIRSTPULLBACK", 30)
    projection = scan(rows)
    def forbidden(*args, **kwargs):
        pytest.fail("producer science was used as the independent oracle")
    for name in ("scan_scenarios", "_scale", "_broad_measure", "_onset", "_advance", "_frame"):
        monkeypatch.setattr(producer, name, forbidden)
    reference.verify(projection, rows, end_offset=30, session_length=90, series_id=SERIES)


@pytest.mark.parametrize("field,value", [
    ("decision_offset", 999), ("structure", Fraction(1)), ("level", Fraction(1)),
    ("scale", Fraction(2)), ("interval_start", 0), ("interval_end", 88),
    ("event_id", "invented:wrong"), ("overlap_group_id", "invented:independent"),
    ("state", "CONFIRMED"), ("terminal", True), ("conditions", ()),
])
def test_changed_frame_fields_cannot_pass_independent_verification(field, value):
    rows = path("A_DOWNFAIL", 27)
    projection = scan(rows)
    bad = replace(projection, frames=(replace(projection.frames[0], **{field: value}), *projection.frames[1:]))
    with pytest.raises(ValueError, match="independent frames"):
        reference.verify(bad, rows, end_offset=27, session_length=90, series_id=SERIES)


@pytest.mark.parametrize("stream", ["frames", "support", "broad_observations"])
def test_omitted_or_reordered_streams_cannot_pass_as_an_equivalent_summary(stream):
    rows = path("A_DOWNFAIL", 27)
    rows[24] = bar(24, 960, low=958, high=965)
    projection = scan(rows)
    original = getattr(projection, stream)
    assert len(original) >= 2
    for changed in (original[:-1], tuple(reversed(original)), (*original, original[-1])):
        with pytest.raises(ValueError, match="independent " + stream):
            reference.verify(replace(projection, **{stream: changed}), rows,
                             end_offset=27, session_length=90, series_id=SERIES)


def test_missing_support_and_broad_floor_labels_are_checked():
    rows = [row for row in path("A_DOWNFAIL", 27) if row.offset != 25]
    projection = scan(rows, end=27)
    i = next(i for i, row in enumerate(projection.support) if row.status == "MISSING_BAR")
    changed = list(projection.support)
    changed[i] = replace(changed[i], status="SUPPORTED_NO_ONSET")
    with pytest.raises(ValueError, match="independent support"):
        reference.verify(replace(projection, support=tuple(changed)), rows,
                         end_offset=27, session_length=90, series_id=SERIES)
    changed_broad = replace(projection.broad_observations[0], meets_minimum_past_move=False)
    with pytest.raises(ValueError, match="independent broad_observations"):
        reference.verify(replace(projection, broad_observations=(changed_broad, *projection.broad_observations[1:])),
                         rows, end_offset=27, session_length=90, series_id=SERIES)


def test_production_scale_rule_mutation_is_detected(monkeypatch):
    rows = path("A_DOWNFAIL", 27)
    original = producer._scale
    def wrong_scale(*args):
        state, value = original(*args)
        return state, value * 2 if value is not None else None
    monkeypatch.setattr(producer, "_scale", wrong_scale)
    bad = producer.scan_scenarios(rows, end_offset=27, session_length=90, series_id=SERIES)
    with pytest.raises(ValueError, match="independent frames"):
        reference.verify(bad, rows, end_offset=27, session_length=90, series_id=SERIES)


@pytest.mark.parametrize("seed", range(12))
def test_deterministic_mixed_raw_paths_agree_without_a_producer_oracle(seed):
    rng, rows, close = random.Random(seed), [], Fraction(1000)
    for index in range(95):
        if rng.randrange(24) == 0:
            continue
        close += rng.choice((-30, -15, -2, 0, 2, 15, 30))
        spread = rng.choice((0, 2, 5, 15))
        rows.append(bar(index, close, low=close - spread, high=close + spread,
                        arrival=index + rng.choice((1, 3, 3, 3, 20))))
    scan(rows, end=95, length=95)


@pytest.mark.parametrize("kwargs", [
    {"end_offset": True, "session_length": 90}, {"end_offset": 1, "session_length": 0},
    {"end_offset": 91, "session_length": 90}, {"end_offset": -1, "session_length": 90},
    {"end_offset": 1, "session_length": 90, "series_id": " "},
])
def test_reference_refuses_invalid_calendar_and_series_contract(kwargs):
    with pytest.raises(ValueError):
        reference.reference_scan([], **kwargs)


def test_reference_refuses_ambiguous_or_future_input_rows():
    for rows, end in (([bar(0), bar(0)], 1), ([bar(1), bar(0)], 2), ([bar(0), bar(1)], 1)):
        with pytest.raises(ValueError):
            reference.reference_scan(rows, end_offset=end, session_length=90)
    with pytest.raises(TypeError):
        reference.reference_scan([{"offset": 0}], end_offset=1, session_length=90)
