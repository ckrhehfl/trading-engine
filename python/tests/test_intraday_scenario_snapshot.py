"""Invented EH feature sealing and supervised linkage."""

from dataclasses import replace
from hashlib import sha256
import stat

import pytest

from research.intraday_ed_inputs import MINUTE
from research.intraday_ed_sources import TargetCoordinate
from research.intraday_scenario_snapshot import (
    project_opening_day, read_features, read_labels, write_features, write_labels,
)
from test_intraday_scenario_state import BELOW, OPENING, OPENING_EVIDENCE, session


DAY = OPENING.date().isoformat()


def _target(symbol, scenario, rank):
    return TargetCoordinate(scenario, symbol, DAY, rank, None, "no_stored_rows_unknown_reason", b"invented-roster")


def _sessions():
    primary = session(BELOW, BELOW, (94, 100, 89, 99), (99, 101, 98, 100), *((100, 101, 99, 100),) * 2,
                      (100, 104, 99, 103), *((103, 104, 102, 103),) * 7, closing=70)
    quiet = session(*((94, 100, 89, 94),) * 14, closing=70)
    peers = tuple(f"peer-{index}" for index in range(5))
    sessions = {primary.symbol: primary}
    for symbol in peers:
        sessions[symbol] = replace(quiet, symbol=symbol, minutes={at: replace(row, symbol=symbol)
                                                                 for at, row in quiet.minutes.items()})
    targets = (_target(primary.symbol, "S1", 1), _target("missing", "S1", 2),
               *(_target(symbol, "S2", rank) for rank, symbol in enumerate((*peers, primary.symbol), 1)))
    evidence = dict.fromkeys(sessions, OPENING_EVIDENCE)
    return primary, targets, sessions, evidence


def _project(*, known_at=OPENING):
    primary, targets, sessions, evidence = _sessions()
    projected = project_opening_day(DAY, targets, sessions, evidence, members_known_at=known_at)
    return primary, projected


def test_day_projection_preserves_union_and_observable_peer_support():
    primary, projected = _project()
    coordinates = {item.symbol: item for item in projected.coordinates}
    assert coordinates[primary.symbol].scenarios == ("S1", "S2")
    assert coordinates["missing"].scenarios == ("S1",)
    assert coordinates["missing"].status == "session_missing"
    assert all(coordinates[f"peer-{index}"].status == "known_no_event" for index in range(5))
    assert sum(len(item.targets) for item in projected.coordinates) == 8
    first = projected.rows[0]
    assert first.scenarios == ("S1", "S2") and first.frame.chart.status == "ready"
    assert first.frame.observed_at == OPENING + 22 * MINUTE
    assert len(first.peer.complete) == 5 and primary.symbol not in first.peer.expected
    assert first.peer.sequence is not None
    _, late = _project(known_at=OPENING + 23 * MINUTE)
    assert late.rows[0].frame.chart == first.frame.chart
    assert late.rows[0].peer.sequence is None
    assert late.rows[0].peer.issues[0].reason == "peer_roster_unavailable_at_decision"


def test_later_gap_preserves_earlier_approach_and_does_not_expose_raw_day_count(tmp_path):
    opening_bar = (94, 100, 89, 94)
    approach = (94, 100, 93, 100)
    touches_target_without_breakout_close = (100, 103, 99, 100)
    primary = session(*((opening_bar,) * 3), approach, touches_target_without_breakout_close,
                      *((opening_bar,) * 9), closing=70)
    missing = OPENING + 38 * MINUTE
    later_gap = replace(primary, minutes={at: row for at, row in primary.minutes.items() if at != missing})
    target = replace(_target(primary.symbol, "S1", 1), stored_row_count=len(primary.minutes),
                     state="stored_rows_completeness_unverified")

    def project(candidate):
        return project_opening_day(DAY, (target,), {primary.symbol: candidate},
                                   {primary.symbol: OPENING_EVIDENCE}, members_known_at=OPENING)

    complete, gapped = project(primary), project(later_gap)
    assert complete.rows == gapped.rows and complete.rows
    assert complete.coordinates[0].breakout_gap == ()
    assert tuple(issue.minute for issue in gapped.coordinates[0].breakout_gap) == (missing,)
    path = tmp_path / "features.jsonl"
    seal = write_features(path, (gapped,))
    assert tuple(read_features(path, seal)) == (gapped,)
    assert b"stored_rows_completeness_unverified" not in path.read_bytes()


def test_separate_seals_link_one_label_per_fixed_question(tmp_path):
    primary, projected = _project()
    features, labels = tmp_path / "features.jsonl", tmp_path / "labels.jsonl"
    feature_seal = write_features(features, (projected,))
    assert tuple(read_features(features, feature_seal)) == (projected,)
    assert stat.S_IMODE(features.stat().st_mode) == 0o400
    feature_bytes = features.read_bytes()
    assert b'"results"' not in feature_bytes and b'"mature_at"' not in feature_bytes
    calls = []

    def outcome(day, symbol):
        assert features.read_bytes() == feature_bytes
        calls.append((day, symbol))
        return primary

    seal = write_labels(labels, features, feature_seal, outcome)
    restored = tuple(read_labels(labels, seal, feature_seal))
    assert stat.S_IMODE(labels.stat().st_mode) == 0o400
    assert calls == [(DAY, primary.symbol)]
    assert seal.records == 2 < len(projected.rows)
    by_stage = {item.key[1]: item for item in restored}
    assert {item.key for item in restored} == {row.question_key for row in projected.rows}
    assert by_stage["approach"].results[0].path == "P4"
    assert by_stage["approach"].results[1].path == "P1"
    assert by_stage["approach"].training is not None
    assert by_stage["approach"].training.mature_at > projected.rows[0].frame.observed_at
    assert features.read_bytes() == feature_bytes
    with pytest.raises(FileExistsError):
        write_features(features, (projected,))
    with pytest.raises(ValueError, match="header_differs"):
        tuple(read_labels(labels, seal, replace(feature_seal, sha256="a" * 64)))


def test_tampered_feature_eof_refuses_before_outcome_provider(tmp_path):
    _, projected = _project()
    features, labels = tmp_path / "features.jsonl", tmp_path / "labels.jsonl"
    seal = write_features(features, (projected,))
    features.chmod(0o600)
    original = features.read_bytes()
    tampered = original + b"{}\n"
    features.write_bytes(tampered)
    features.chmod(0o400)

    def forbidden(*_):
        pytest.fail("provider opened before full feature validation")

    with pytest.raises(ValueError, match="hash_differs"):
        write_labels(labels, features, replace(seal, size=len(tampered)), forbidden)
    with pytest.raises(ValueError, match="footer_or_EOF_differs"):
        write_labels(labels, features, replace(seal, size=len(tampered), sha256=sha256(tampered).hexdigest()), forbidden)
    assert not labels.exists()


def test_missing_or_mismatched_outcome_does_not_create_a_label(tmp_path):
    primary, projected = _project()
    features, labels = tmp_path / "features.jsonl", tmp_path / "labels.jsonl"
    feature_seal = write_features(features, (projected,))
    seal = write_labels(labels, features, feature_seal, lambda *_: None)
    missing = tuple(read_labels(labels, seal, feature_seal))
    assert len(missing) == 2
    assert all(item.status == "session_missing" and not item.results and item.training is None for item in missing)
    with pytest.raises(ValueError, match="outcome_session_differs"):
        write_labels(tmp_path / "mismatch.jsonl", features, feature_seal, lambda *_: replace(primary, symbol="other"))


def test_original_missing_coordinates_survive_without_outcome_requests(tmp_path):
    missing = project_opening_day(DAY, (_target("missing", "S2", 1),), {}, {}, members_known_at=OPENING)
    features, labels = tmp_path / "features.jsonl", tmp_path / "labels.jsonl"
    feature_seal = write_features(features, (missing,))
    assert tuple(read_features(features, feature_seal)) == (missing,)

    def forbidden(*_):
        pytest.fail("no fixed question to label")

    seal = write_labels(labels, features, feature_seal, forbidden)
    assert seal.records == 0 and tuple(read_labels(labels, seal, feature_seal)) == ()


def test_changed_question_basis_refuses_before_any_label_session(tmp_path):
    _, projected = _project()
    second = projected.rows[1]
    changed = replace(second, frame=replace(second.frame, initial=replace(second.frame.initial, start_close=99)))
    day = replace(projected, rows=(projected.rows[0], changed, *projected.rows[2:]))
    features, labels = tmp_path / "features.jsonl", tmp_path / "labels.jsonl"
    seal = write_features(features, (day,))

    def forbidden(*_):
        pytest.fail("conflicting question reached outcome provider")

    with pytest.raises(ValueError, match="fixed_question_changed"):
        write_labels(labels, features, seal, forbidden)
    assert not labels.exists()
