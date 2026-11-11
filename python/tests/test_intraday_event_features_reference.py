"""Exact invented calibration and causal mutation checks for EL inputs."""

from copy import deepcopy
from dataclasses import replace
from fractions import Fraction as F
from itertools import permutations
import random

import pytest

from research import intraday_event_features as producer
from research import intraday_event_features_reference as reference
from research.intraday_event_features import VolumeObservation
from research.intraday_event_scenarios import EventFrame, MinuteBar


def event(**changes):
    base = EventFrame("A_DOWNFAIL", "invented:a", "invented:session-a", 25, 30, 33, 22, 31,
                      "scheduled", "WAITING", False, F(2), F(99), F(98), ())
    return replace(base, **changes)


def prices(end=100):
    return {i: MinuteBar(i, 100 + i, 102 + i, 99 + i, 101 + i, i + 3) for i in range(end)}


def volumes(end=100):
    return {i: VolumeObservation(i, F(10 if i % 2 == 0 else 30), i + 3) for i in range(end)}


def chart(frame=None, bars=None, reported=None):
    frame = event() if frame is None else frame
    bars = prices() if bars is None else bars
    result = producer.chart_features(frame, bars, session_length=390, volumes=reported)
    reference.verify_chart_features(result, frame, bars, session_length=390, volumes=reported)
    return result


def test_direct_exact_chart_calibration_includes_every_static_column():
    reported = volumes()
    for i in range(26, 31):
        reported[i] = VolumeObservation(i, F(40), i + 3)
    result = chart(reported=reported)
    assert len(result["sequence"]) == 64 and all(len(row) == 13 for row in result["sequence"])
    assert result["sequence"][-1] == ["-1/2", "1/2", "-1", "0", "1/2", "3/2", "1/2",
                                         "1", "1", "0", "4/3", "1", "1"]
    assert result["static"] == ["11/130", "5", "2/131", "16", "33/2", "3", "31/64",
                               "5/2", "15/2", "7/2", "17/2", "1", "3/2", "16/17", "1", "2"]
    assert result["sequence"][0] == [None] * 7 + ["0", "0", "1", None, "0", "0"]
    assert result["reference_close"] == "131" and "unverified" in result["volume_semantics"]


def test_flat_prices_zero_motion_and_zero_volume_keep_distinct_undefined_ratios():
    bars = {i: MinuteBar(i, 100, 100, 100, 100, i + 3) for i in range(40)}
    reported = {i: VolumeObservation(i, F(0), i + 3) for i in range(40)}
    result = chart(bars=bars, reported=reported)
    stats = dict(zip(result["static_names"], result["static"]))
    assert stats["net15"] == "0" and stats["range15"] == "0"
    assert stats["efficiency15"] is None and stats["position15"] is None
    assert stats["compression5"] == "0" and stats["above_level_fraction15"] == "1"
    assert result["sequence"][-1][-3:] == [None, "1", "0"]
    assert stats["volume5_vs_prior20"] is None


@pytest.mark.parametrize("channel", ["price", "volume"])
def test_missing_and_late_rows_preserve_observed_zero_and_history_masks(channel):
    bars, reported = prices(), volumes()
    reported[30] = VolumeObservation(30, F(0), 33)
    original = chart(bars=bars, reported=reported)
    assert original["sequence"][-1][-3:] == ["0", "1", "1"]
    mapping = bars if channel == "price" else reported
    mapping[29] = replace(mapping[29], available_offset=34)
    late = chart(bars=bars, reported=reported)
    mapping.pop(29)
    missing = chart(bars=bars, reported=reported)
    assert late == missing
    if channel == "price":
        assert late["sequence"][-2][:7] == [None] * 7
        assert late["sequence"][-1][6] is None
        assert late["static"][7] is None
    else:
        assert late["sequence"][-2][-3:] == [None, "0", "1"]
        assert late["sequence"][-1][-3:] == [None, "1", "0"]
        assert late["static"][-1] is None


def test_only_complete_history_counts_and_missing_anchor_is_not_dropped():
    bars = prices()
    bars.pop(15)
    result = chart(bars=bars)
    stats = dict(zip(result["static_names"], result["static"]))
    assert stats["net5"] == "5/2" and stats["compression5"] == "3/2"
    assert all(stats[name] is None for name in ("net15", "range15", "efficiency15", "position15", "above_level_fraction15"))
    assert stats["known_fraction"] == "15/32"
    reported = volumes()
    reported.pop(6)  # Earliest minute of the separate prior20 volume baseline.
    assert chart(reported=reported)["static"][-1] is None


def test_features_with_no_known_price_are_none_without_fabricated_flat_sequence():
    assert chart(bars={}) is None
    bars = {0: MinuteBar(0, 100, 101, 99, 100, 99)}
    assert chart(bars=bars) is None
    with pytest.raises(ValueError, match="independent chart"):
        reference.verify_chart_features({}, event(), bars, session_length=390)


@pytest.mark.parametrize("decision", [33, 50, 80])
def test_future_prices_volumes_and_longer_source_suffix_leave_receipt_prefix_unchanged(decision):
    frame, bars, reported = event(decision_offset=decision), prices(), volumes()
    expected = chart(frame, bars, reported)
    for i in range(decision - 2, 100):
        bars[i] = MinuteBar(i, 999, 1000, 998, 999, i + 3)
        reported[i] = VolumeObservation(i, F(999999), i + 3)
    assert chart(frame, bars, reported) == expected
    assert chart(frame, {i: b for i, b in bars.items() if i <= decision - 3},
                 {i: v for i, v in reported.items() if i <= decision - 3}) == expected
    assert expected["reference_offset"] == decision - 3
    assert expected["static"][1] == "5"  # Receipt delay does not alter event age.


def test_early_completed_minute_is_admitted_but_unreceived_volume_remains_unknown():
    bars, reported = prices(), volumes()
    bars[32] = replace(bars[32], available_offset=33)
    result = chart(bars=bars, reported=reported)
    assert result["reference_offset"] == 32 and result["static"][5] == "1"
    assert result["sequence"][-1][-2] == "0"


def constant_peer(last_close, *, length=40):
    bars = {i: MinuteBar(i, 100, 100, 100, 100, i + 3) for i in range(length)}
    bars[30] = MinuteBar(30, last_close, last_close, last_close, last_close, 33)
    return bars


def test_direct_even_peer_median_and_positive_fraction_exclude_self():
    peers = {"self": {"not-an-offset": "never inspected"},
             "a": constant_peer(99), "b": constant_peer(100),
             "c": constant_peer(102), "d": constant_peer(103)}
    result = producer.peer_features("self", peers, decision_offset=33, quorum=4)
    reference.verify_peer_features(result, "self", peers, decision_offset=33, quorum=4)
    assert result["values"] == {"median5": "1/100", "median15": "1/100", "positive15_fraction": "1/2"}
    assert result["members"] == ["a", "b", "c", "d"]
    peers["c"][20] = replace(peers["c"][20], available_offset=34)
    result = producer.peer_features("self", peers, decision_offset=33, quorum=4)
    reference.verify_peer_features(result, "self", peers, decision_offset=33, quorum=4)
    assert result["observed"] == 3 and result["values"] is None
    assert result["members"] == ["a", "b", "d"]


@pytest.mark.parametrize("decision", [-2, 5, 17, 18, 33])
def test_peer_warmup_and_future_source_suffix_do_not_change_membership(decision):
    peers = {name: prices() for name in ("self", "b", "a", "c", "e", "d")}
    expected = producer.peer_features("self", peers, decision_offset=decision)
    reference.verify_peer_features(expected, "self", peers, decision_offset=decision)
    for bars in peers.values():
        for offset in range(max(0, decision - 2), 100):
            bars[offset] = {"future": "not decoded"}
    assert reference.peer_features("self", peers, decision_offset=decision) == expected


def test_coalescing_is_order_invariant_across_series_families_and_terminals():
    old = event(offset=25, decision_offset=50, kind="at_onset")
    terminal = event(decision_offset=50, terminal=True, state="INVALIDATED", kind="terminal")
    latest = event(event_id="new", onset_offset=35, offset=35, decision_offset=50, kind="at_onset")
    other = replace(terminal, overlap_group_id="invented:session-b", event_id="other")
    family = replace(terminal, family="B_ORL30", event_id="other-family")
    expected = (latest, family, other)
    for rows in permutations((old, terminal, latest, other, family)):
        assert reference.coalesce_frames(rows) == expected
        reference.verify_coalesced_frames(producer.coalesce_frames(rows), rows)
    assert reference.coalesce_frames((old, old, terminal, terminal)) == (terminal,)
    same_offset_onset = replace(terminal, terminal=False, kind="at_onset", state="WAITING")
    assert reference.coalesce_frames((terminal, same_offset_onset)) == (terminal,)


def test_conflicts_in_old_history_cannot_be_hidden_by_a_later_frame():
    old = event(offset=25, decision_offset=50, kind="at_onset")
    conflict = replace(old, state="other")
    later = event(decision_offset=50, terminal=True, kind="terminal")
    for rows in permutations((old, conflict, later)):
        with pytest.raises(ValueError, match="conflicting|Conflicting"):
            reference.coalesce_frames(rows)
        with pytest.raises(ValueError, match="conflicting|Conflicting"):
            producer.coalesce_frames(rows)


def test_references_do_not_call_any_producer_feature_helpers(monkeypatch):
    bars, reported = prices(), volumes()
    frame = event()
    chart_row = chart(frame, bars, reported)
    peers = {str(i): prices() for i in range(6)}
    peer_row = producer.peer_features("0", peers, decision_offset=33)
    grouped = producer.coalesce_frames((frame,))
    def forbidden(*args, **kwargs):
        pytest.fail("Producer feature helper used as independent oracle")
    for name in ("chart_features", "peer_features", "coalesce_frames", "_known", "_complete", "_ratio",
                 "_volume", "_volume_baseline"):
        monkeypatch.setattr(producer, name, forbidden)
    reference.verify_chart_features(chart_row, frame, bars, session_length=390, volumes=reported)
    reference.verify_peer_features(peer_row, "0", peers, decision_offset=33)
    reference.verify_coalesced_frames(grouped, (frame,))


@pytest.mark.parametrize("column", range(16))
def test_every_static_value_is_independently_checked(column):
    result = chart(reported=volumes())
    result["static"][column] = "999"
    with pytest.raises(ValueError, match="independent chart"):
        reference.verify_chart_features(result, event(), prices(), session_length=390, volumes=volumes())


@pytest.mark.parametrize("column", range(13))
def test_every_sequence_value_and_mask_is_independently_checked(column):
    result = chart(reported=volumes())
    result["sequence"][-1][column] = "999"
    with pytest.raises(ValueError, match="independent chart"):
        reference.verify_chart_features(result, event(), prices(), session_length=390, volumes=volumes())


def test_metadata_ordering_membership_and_coalesced_state_are_verified():
    result = chart()
    for key, value in (("reference_close", "1000"), ("reference_offset", 31),
                       ("decision_offset", 32), ("state", "CONFIRMED"), ("event_id", "other"),
                       ("sequence_names", list(reversed(result["sequence_names"]))),
                       ("sequence", result["sequence"][:-1])):
        altered = deepcopy(result)
        altered[key] = value
        with pytest.raises(ValueError, match="independent chart"):
            reference.verify_chart_features(altered, event(), prices(), session_length=390)
    peers = {str(i): prices() for i in range(6)}
    peer = reference.peer_features("0", peers, decision_offset=33)
    for key, value in (("members", ["0", *peer["members"]]), ("values", None), ("observed", 0)):
        with pytest.raises(ValueError, match="independent peer"):
            reference.verify_peer_features({**peer, key: value}, "0", peers, decision_offset=33)
    old, latest = event(offset=25), event()
    with pytest.raises(ValueError, match="independent coalesced"):
        reference.verify_coalesced_frames((old,), (old, latest))


def test_producer_volume_normalization_defect_is_detected(monkeypatch):
    old = producer._volume_baseline
    def wrong_baseline(*args):
        value = old(*args)
        return None if value is None else value * 2
    monkeypatch.setattr(producer, "_volume_baseline", wrong_baseline)
    row = producer.chart_features(event(), prices(), session_length=390, volumes=volumes())
    with pytest.raises(ValueError, match="independent chart"):
        reference.verify_chart_features(row, event(), prices(), session_length=390, volumes=volumes())


@pytest.mark.parametrize("seed", range(8))
def test_mixed_fractional_prices_late_observations_and_volume_support(seed):
    rng, bars, reported = random.Random(seed), prices(), volumes()
    for index in range(100):
        if rng.randrange(12) == 0:
            bars.pop(index)
        else:
            close = F(1000 + rng.randrange(100), 7)
            bars[index] = MinuteBar(index, close, close + F(1, 3), close - F(1, 2), close,
                                    index + rng.choice((1, 3, 5, 20)))
        if rng.randrange(9) == 0:
            reported.pop(index)
        else:
            reported[index] = VolumeObservation(index, F(rng.randrange(8), 3), index + rng.choice((1, 3, 20)))
    for decision in (33, 50, 80):
        chart(event(decision_offset=decision), bars, reported)


@pytest.mark.parametrize("changes", [{"decision_offset": 390}, {"decision_offset": 30},
                                      {"onset_offset": 31}, {"scale": F(0)}, {"level": F(0)}])
def test_invalid_feature_geometry_is_refused(changes):
    with pytest.raises(ValueError, match="geometry"):
        reference.chart_features(event(**changes), prices(), session_length=390)


def test_relevant_coordinate_conflicts_are_refused_and_unread_future_is_ignored():
    bars = prices()
    bars[30] = bars[31]
    with pytest.raises(ValueError, match="bar coordinate"):
        reference.chart_features(event(), bars, session_length=390)
    reported = volumes()
    reported[30] = reported[31]
    with pytest.raises(ValueError, match="volume coordinate"):
        reference.chart_features(event(), prices(), session_length=390, volumes=reported)
    bars = prices()
    bars[90] = "unread"
    assert reference.chart_features(event(), bars, session_length=390) == chart()
    with pytest.raises(TypeError, match="frame"):
        reference.coalesce_frames(({},))
