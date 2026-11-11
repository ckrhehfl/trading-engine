from dataclasses import replace
from fractions import Fraction as F

import pytest

from research.intraday_event_features import VolumeObservation, chart_features, coalesce_frames, peer_features
from research.intraday_event_scenarios import EventFrame, MinuteBar


def frame(**changes):
    base = EventFrame("A_DOWNFAIL", "a", "group", 25, 30, 33, 22, 31,
                      "interval", "WAITING", False, F(2), F(99), F(98), ())
    return replace(base, **changes)


def tape(end=80):
    return {i: MinuteBar(i, 100 + i, 102 + i, 99 + i, 101 + i, i + 3) for i in range(end)}


def test_future_changes_cannot_change_features():
    bars = tape()
    expected = chart_features(frame(), bars, session_length=390)
    modified = {i: b if i <= 30 else MinuteBar(i, 1000, 1002, 999, 1001, i + 3) for i, b in bars.items()}
    assert chart_features(frame(), modified, session_length=390) == expected
    assert chart_features(frame(), {i: b for i, b in bars.items() if i <= 30}, session_length=390) == expected


def test_late_bar_is_not_backdated_and_missing_is_not_flat():
    bars = tape()
    bars[29] = replace(bars[29], available_offset=50)
    out = chart_features(frame(), bars, session_length=390)
    assert out["reference_offset"] == 30
    assert out["sequence"][-2][:7] == [None] * 7
    assert out["sequence"][-2][7] == "0"
    assert out["sequence"][-1][6] is None
    assert out["static"][out["static_names"].index("net5")] is None


def test_pre_session_padding_has_a_separate_flag():
    out = chart_features(frame(), tape(), session_length=390)
    assert out["sequence"][0] == [None] * 7 + ["0", "0", "1", None, "0", "0"]
    assert out["sequence"][-1][7:10] == ["1", "1", "0"]
    assert len(out["sequence"]) == 64


def test_receipt_time_features_include_already_known_later_bars():
    out = chart_features(frame(decision_offset=50), tape(), session_length=390)
    assert out["reference_offset"] == 47


def test_terminal_update_dominates_stale_onset_at_same_receipt_time():
    onset = frame(offset=25, decision_offset=50, kind="onset")
    terminal = frame(decision_offset=50, state="INVALIDATED", terminal=True, kind="terminal")
    assert coalesce_frames((onset, terminal)) == (terminal,)
    assert coalesce_frames((terminal, onset)) == (terminal,)
    assert coalesce_frames((onset, onset)) == (onset,)
    with pytest.raises(ValueError, match="Conflicting"):
        coalesce_frames((onset, replace(onset, state="other")))


def test_later_episode_replaces_earlier_episode_at_same_receipt():
    old = frame(offset=26, decision_offset=50, state="CONFIRMED", terminal=True)
    new = frame(event_id="b", onset_offset=35, offset=35, decision_offset=50, kind="onset")
    assert coalesce_frames((old, new)) == (new,)


def test_different_symbol_or_session_groups_do_not_coalesce():
    one = frame()
    two = replace(one, overlap_group_id="other", event_id="other-a")
    assert set(coalesce_frames((one, two))) == {one, two}


def test_later_state_cannot_hide_conflicting_earlier_updates():
    low = frame(offset=25, decision_offset=50)
    conflict = replace(low, state="CONFLICT")
    high = frame(decision_offset=50)
    for order in ((low, conflict, high), (high, low, conflict)):
        with pytest.raises(ValueError, match="Conflicting"):
            coalesce_frames(order)


def test_peer_unknown_is_not_neutral_and_self_is_excluded():
    peers = {str(i): tape() for i in range(6)}
    out = peer_features("0", peers, decision_offset=33)
    assert out["observed"] == 5 and out["values"] is not None
    assert "0" not in out["members"]
    peers["5"].pop(20)
    out = peer_features("0", peers, decision_offset=33)
    assert out["observed"] == 4 and out["values"] is None


def test_peer_future_change_has_no_effect():
    peers = {str(i): tape() for i in range(6)}
    expected = peer_features("0", peers, decision_offset=33)
    for bars in peers.values():
        bars[70] = MinuteBar(70, 1000, 1002, 999, 1001, 73)
    assert peer_features("0", peers, decision_offset=33) == expected


def test_invalid_clock_or_bar_identity_is_refused():
    with pytest.raises(ValueError, match="geometry"):
        chart_features(frame(decision_offset=390), tape(), session_length=390)
    bars = tape()
    bars[30] = bars[31]
    with pytest.raises(ValueError, match="coordinate"):
        chart_features(frame(), bars, session_length=390)


def test_zero_volume_is_distinct_from_missing_and_late():
    volumes = {i: VolumeObservation(i, F(100), i + 3) for i in range(80)}
    volumes[30] = VolumeObservation(30, F(0), 33)
    out = chart_features(frame(), tape(), session_length=390, volumes=volumes)
    assert out["sequence"][-1][-3:] == ["0", "1", "1"]
    volumes[30] = replace(volumes[30], available_offset=34)
    out = chart_features(frame(), tape(), session_length=390, volumes=volumes)
    assert out["sequence"][-1][-3:] == [None, "0", "1"]


def test_volume_uses_only_the_same_session_past_without_unit_claim():
    volumes = {i: VolumeObservation(i, F(100 if i <= 25 else 200), i + 3) for i in range(80)}
    out = chart_features(frame(), tape(), session_length=390, volumes=volumes)
    assert out["static"][-1] == "2"
    volumes[70] = VolumeObservation(70, F(99999), 73)
    assert chart_features(frame(), tape(), session_length=390, volumes=volumes) == out
    assert "unverified" in out["volume_semantics"]


def test_zero_volume_baseline_is_unknown_ratio_not_division_by_zero():
    volumes = {i: VolumeObservation(i, F(0), i + 3) for i in range(80)}
    out = chart_features(frame(), tape(), session_length=390, volumes=volumes)
    assert out["sequence"][-1][-3:] == [None, "1", "0"]
    assert out["static"][-1] is None


def test_future_event_state_clock_cannot_enter_features():
    with pytest.raises(ValueError, match="geometry"):
        chart_features(frame(offset=35, decision_offset=33), tape(), session_length=390)
