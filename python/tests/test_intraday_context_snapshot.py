"""Invented EI roster, seal, future-access and per-horizon snapshot contracts."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import json

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_snapshot as snapshot
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs
from research.intraday_ed_sources import TargetCoordinate


OPEN = datetime(2031, 2, 12, 9, tzinfo=timezone(timedelta(hours=9)))
DAY = OPEN.date().isoformat()
BASE = ((100, 102, 98, 100), (100, 103, 99, 102), (102, 104, 100, 103),
        (103, 116, 102, 114), (114, 114, 111, 112), (112, 116, 111, 114))


def at(offset):
    return OPEN + offset * MINUTE


def target(scenario="S1", symbol="invented", rank=1):
    return TargetCoordinate(scenario, symbol, DAY, rank, 60, "observed", wire.encode({"synthetic": True}))


def session(bars=None, symbol="invented", closing=60):
    bars = bars or (*BASE, (114, 118, 113, 117), (118, 125, 117, 124), *((124, 125, 123, 124),) * 4)
    values = {at(index): Minute(symbol, at(index), at(index + 3), tuple(map(Decimal, bar)), Decimal(10))
              for number, bar in enumerate(bars) for index in range(number * 5, number * 5 + 5)}
    return SessionInputs(symbol, OPEN, at(closing), values)


def write_one(tmp_path, value=None, targets=None):
    value, targets = value or session(), targets or (target(), target("S2"))
    coordinate = snapshot.project_coordinate(targets, value)
    path = tmp_path / "features.jsonl"
    seal = snapshot.write_features(path, iter((coordinate,)), expected_targets=targets)
    return path, seal, coordinate, targets, value


def rewrite(path, seal, change):
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    change(rows)
    raw = b"".join(wire.encode(row) for row in rows)
    path.chmod(0o600)
    path.write_bytes(raw)
    path.chmod(0o400)
    return replace(seal, sha256=wire.digest(raw), size=len(raw))


def test_roundtrip_preserves_original_memberships_frames_and_exact_question_metadata(tmp_path):
    path, seal, coordinate, targets, value = write_one(tmp_path)
    assert seal.schema == snapshot.FEATURE_SCHEMA and seal.records == 1
    assert snapshot.MAX_BYTES == 128 * 1024**2 and snapshot.MAX_LINE == 8 * 1024**2
    assert tuple(snapshot.read_features(path, seal, expected_targets=targets)) == (coordinate,)
    assert coordinate.targets == targets and len(coordinate.frames) == 6
    assert [row.available_at for row in coordinate.frames] == list(map(at, (32, 37, 42, 47, 52, 57)))
    assert coordinate.feature_support["approach"] == {"questions": 1, "ready_features": 1}
    assert coordinate.feature_support["breakout"] == {"questions": 1, "ready_features": 1}
    assert coordinate.questions[0].question.episode_origin_context == "A"
    assert all(len(snapshot.question_digest(row.question)) == 64 for row in coordinate.questions)
    calls = []

    def outcomes(day, symbol):
        calls.append((day, symbol))
        return value

    label_path = tmp_path / "labels.jsonl"
    label_seal = snapshot.write_labels(label_path, path, seal, outcomes, expected_targets=targets)
    result, = snapshot.read_labels(label_path, label_seal, path, seal, expected_targets=targets)
    assert calls == [(DAY, "invented")]
    assert result.status == "observed" and len(result.labels) == len(coordinate.questions)
    assert [row.question for row in result.labels] == [row.question for row in coordinate.questions]
    assert all(len(row.results) == 3 for row in result.labels)
    assert result.horizon_support["approach"]["5"] == {"resolved": 1}


def test_absent_session_unknown_frames_and_no_event_are_preserved(tmp_path):
    targets = (target(symbol="absent", rank=1), target(symbol="flat", rank=2), target(symbol="unknown", rank=3))
    flat = session(((100, 101, 99, 100),) * 12, symbol="flat")
    unknown = session(symbol="unknown")
    unknown = replace(unknown, minutes={key: value for key, value in unknown.minutes.items() if key != OPEN})
    coordinates = (snapshot.project_coordinate((targets[0],), None), snapshot.project_coordinate((targets[1],), flat),
                   snapshot.project_coordinate((targets[2],), unknown))
    path = tmp_path / "features.jsonl"
    seal = snapshot.write_features(path, iter(coordinates), expected_targets=targets)
    assert tuple(snapshot.read_features(path, seal, expected_targets=targets)) == coordinates
    assert coordinates[0].status == "session_missing" and not coordinates[0].frames
    assert {row.status for row in coordinates[1].frames} == {"no_anchor"}
    assert {row.status for row in coordinates[2].frames} == {"unknown"}
    label_path = tmp_path / "labels.jsonl"
    label_seal = snapshot.write_labels(label_path, path, seal, lambda *_: pytest.fail("no question needs future data"),
                                       expected_targets=targets)
    labels = tuple(snapshot.read_labels(label_path, label_seal, path, seal, expected_targets=targets))
    assert [row.status for row in labels] == ["feature_session_missing", "no_questions", "no_questions"]
    assert all(not row.labels for row in labels)


def test_all_feature_eof_validation_precedes_any_outcome_callback(tmp_path):
    path, seal, _, targets, _ = write_one(tmp_path)
    seal = rewrite(path, seal, lambda rows: rows[-1].update(records=999))
    calls = []
    with pytest.raises(ValueError, match="footer_or_EOF"):
        snapshot.write_labels(tmp_path / "labels.jsonl", path, seal, lambda *args: calls.append(args),
                              expected_targets=targets)
    assert calls == [] and not (tmp_path / "labels.jsonl").exists()


def test_last_coordinate_schema_error_precedes_any_outcome_callback(tmp_path):
    value = session()
    first, last = target(), target(symbol="last", rank=2)
    targets = (first, last)
    coordinates = (snapshot.project_coordinate((first,), value), snapshot.project_coordinate((last,), None))
    path = tmp_path / "features.jsonl"
    seal = snapshot.write_features(path, coordinates, expected_targets=targets)
    seal = rewrite(path, seal, lambda rows: rows[-2].update(unexpected=1))
    with pytest.raises(ValueError, match="schema_fields"):
        snapshot.write_labels(tmp_path / "labels.jsonl", path, seal,
                              lambda *_: pytest.fail("future callback before complete feature validation"),
                              expected_targets=targets)


@pytest.mark.parametrize("kind", ["omit", "extra", "duplicate", "substitute", "reorder"])
def test_writer_requires_exact_original_roster_once_in_order(tmp_path, kind):
    targets = (target(symbol="a"), target(symbol="b", rank=2))
    a, b = (snapshot.project_coordinate((row,), None) for row in targets)
    values = {"omit": (a,), "extra": (a, b, b), "duplicate": (a, a), "substitute": (a, replace(b, symbol="c")),
              "reorder": (b, a)}[kind]
    with pytest.raises(ValueError):
        snapshot.write_features(tmp_path / "features.jsonl", values, expected_targets=targets)


@pytest.mark.parametrize("kind", ["target_metadata", "symbol", "frame_omission", "question_duplicate", "question_id",
                                  "feature_available", "volume_scale", "feature_static", "feature_extra_field",
                                  "frame_context", "overlap", "episode_origin"])
def test_resealed_bad_feature_stream_is_rejected(tmp_path, kind):
    path, seal, _, targets, _ = write_one(tmp_path)

    def mutate(rows):
        row = rows[1]
        if kind == "target_metadata":
            row["targets"][0][-1] = "00"
        elif kind == "symbol":
            row["symbol"] = "replacement"
        elif kind == "frame_omission":
            row["frames"].pop()
        elif kind == "question_duplicate":
            row["questions"].append(row["questions"][0])
        elif kind == "question_id":
            row["questions"][0]["id"] = "0" * 64
        elif kind == "feature_available":
            row["questions"][0]["available"][-1] = snapshot._time(at(33))
        elif kind == "volume_scale":
            row["questions"][0]["volume_scale"] = "99"
        elif kind == "feature_static":
            row["questions"][0]["common"][0] = "1"
        elif kind == "frame_context":
            row["frames"][0][1] = "B"
        elif kind == "overlap":
            row["questions"][1]["question"]["overlap"] = []
        elif kind == "episode_origin":
            row["questions"][1]["question"]["origin_context"] = "B"
            row["questions"][1]["context"][:3] = ["0", "1", "0"]
        else:
            row["questions"][0]["unused"] = 1

    changed = rewrite(path, seal, mutate)
    with pytest.raises(ValueError):
        tuple(snapshot.read_features(path, changed, expected_targets=targets))


def test_feature_seal_and_exclusive_writer_cannot_be_reused_for_changed_bytes(tmp_path):
    path, seal, coordinate, targets, _ = write_one(tmp_path)
    with pytest.raises(FileExistsError):
        snapshot.write_features(path, (coordinate,), expected_targets=targets)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="hash_differs"):
        tuple(snapshot.read_features(path, replace(seal, sha256="0" * 64), expected_targets=targets))
    assert path.read_bytes() == before


@pytest.mark.parametrize("reader", ["features", "labels", "label_writer"])
@pytest.mark.parametrize("kind", ["volume_scale", "late_minute", "ohlc", "nonfinite"])
def test_semantic_feature_checks_remain_at_each_complete_stream_boundary(tmp_path, reader, kind):
    path, seal, _, targets, value = write_one(tmp_path)
    labels = tmp_path / "labels.jsonl"
    label_seal = snapshot.write_labels(labels, path, seal, lambda *_: value, expected_targets=targets)

    def mutate(rows):
        feature = rows[1]["questions"][0]
        if kind == "volume_scale":
            feature["volume_scale"] = "99"
        elif kind == "late_minute":
            feature["available"][-1] = snapshot._time(at(33))
        elif kind == "ohlc":
            feature["prices"][0][2] = "1000"
        else:
            # Keep exact OHLC ordering and positive restored prices; the lossy
            # numeric representation must still reject this finite Fraction.
            feature["prices"][0] = [str(10**400)] * 4

    changed = rewrite(path, seal, mutate)
    if reader == "features":
        with pytest.raises(ValueError):
            tuple(snapshot.read_features(path, changed, expected_targets=targets))
    elif reader == "labels":
        rebound = rewrite(labels, label_seal, lambda rows: rows[0].update(snapshot._label_header(changed)))
        with pytest.raises(ValueError):
            tuple(snapshot.read_labels(labels, rebound, path, changed, expected_targets=targets))
    else:
        output = tmp_path / "guarded-labels.jsonl"
        with pytest.raises(ValueError):
            snapshot.write_labels(output, path, changed,
                                  lambda *_: pytest.fail("future callback before semantic feature validation"),
                                  expected_targets=targets)
        assert not output.exists()


def test_missing_future_session_is_not_an_observed_zero_outcome(tmp_path):
    path, seal, coordinate, targets, _ = write_one(tmp_path)
    labels = tmp_path / "labels.jsonl"
    label_seal = snapshot.write_labels(labels, path, seal, lambda *_: None, expected_targets=targets)
    result, = snapshot.read_labels(labels, label_seal, path, seal, expected_targets=targets)
    assert len(result.labels) == len(coordinate.questions) and result.status == "session_missing"
    assert all(not row.results for row in result.labels)
    assert result.horizon_support["approach"]["5"] == {"session_missing": 1}


@pytest.mark.parametrize("reason", ["same_minute_target_stop_order_unknown", "same_minute_retest_target_order_unknown"])
def test_observed_intrabar_order_ambiguity_retains_maturity_without_inventing_outcome(tmp_path, reason):
    value = session(closing=70)
    rows = dict(value.minutes)
    bars = ((32, (114, 125, 110, 114)),) if reason == "same_minute_target_stop_order_unknown" else (
        (32, (114, 120, 113, 119)), (33, (119, 125, 116, 124)))
    for index, prices in bars:
        rows[at(index)] = replace(rows[at(index)], ohlc=tuple(map(Decimal, prices)))
    value = replace(value, minutes=rows)
    path, seal, _, targets, _ = write_one(tmp_path, value)
    labels = tmp_path / "labels.jsonl"
    label_seal = snapshot.write_labels(labels, path, seal, lambda *_: value, expected_targets=targets)
    result, = snapshot.read_labels(labels, label_seal, path, seal, expected_targets=targets)
    first = next(row for row in result.labels if row.question.stage == "approach")
    assert all(row.status == "unknown" and row.reason == reason and row.path is None for row in first.results)
    assert all(row.mature_at == at(bars[-1][0] + 3) for row in first.results)
    assert result.horizon_support["approach"]["5"] == {"unknown": 1}
    bad_seal = rewrite(labels, label_seal, lambda records: records[1]["labels"][0]["results"][0].__setitem__(
        4, snapshot._time(at(73))))
    with pytest.raises(ValueError, match="unresolved_label"):
        tuple(snapshot.read_labels(labels, bad_seal, path, seal, expected_targets=targets))


@pytest.mark.parametrize("kind", ["symbol", "opening", "closing"])
def test_outcome_callback_cannot_substitute_name_or_session_clock(tmp_path, kind):
    path, seal, _, targets, value = write_one(tmp_path)
    changed = {"symbol": replace(value, symbol="other"), "opening": replace(value, opening=at(1)),
               "closing": replace(value, closing=at(61))}[kind]
    with pytest.raises(ValueError, match="outcome_session_substitution"):
        snapshot.write_labels(tmp_path / "labels.jsonl", path, seal, lambda *_: changed, expected_targets=targets)


def test_late_parent_horizon_does_not_remove_resolved_shorter_children(tmp_path):
    value = session(closing=50)
    path, seal, _, targets, _ = write_one(tmp_path, value)
    labels = tmp_path / "labels.jsonl"
    label_seal = snapshot.write_labels(labels, path, seal, lambda *_: value, expected_targets=targets)
    result, = snapshot.read_labels(labels, label_seal, path, seal, expected_targets=targets)
    approach = next(row for row in result.labels if row.question.stage == "approach")
    assert approach.question.unsupported_reasons == ("outside_session",)
    assert [row.status for row in approach.results] == ["resolved", "resolved", "unsupported"]
    assert approach.results[2].reason == "outside_session"


@pytest.mark.parametrize("kind", ["drop_label", "duplicate_label", "horizon_order", "unknown_with_outcome", "changed_metadata",
                                  "changed_coordinate", "label_extra_field"])
def test_resealed_labels_still_require_exact_feature_bijection_and_status(tmp_path, kind):
    path, seal, _, targets, value = write_one(tmp_path)
    labels = tmp_path / "labels.jsonl"
    label_seal = snapshot.write_labels(labels, path, seal, lambda *_: value, expected_targets=targets)

    def mutate(rows):
        row = rows[1]
        if kind == "drop_label":
            row["labels"].pop()
        elif kind == "duplicate_label":
            row["labels"].append(row["labels"][0])
        elif kind == "horizon_order":
            row["labels"][0]["results"][0][0] = 15
        elif kind == "unknown_with_outcome":
            row["labels"][0]["results"][0][1] = "unknown"
        elif kind == "changed_metadata":
            row["labels"][0]["question"]["origin_context"] = "B"
        elif kind == "changed_coordinate":
            row["symbol"] = "other"
        else:
            row["labels"][0]["extra"] = 1

    changed = rewrite(labels, label_seal, mutate)
    with pytest.raises(ValueError):
        tuple(snapshot.read_labels(labels, changed, path, seal, expected_targets=targets))


def test_label_stream_is_bound_to_full_exact_feature_seal(tmp_path):
    path, seal, _, targets, value = write_one(tmp_path)
    labels = tmp_path / "labels.jsonl"
    label_seal = snapshot.write_labels(labels, path, seal, lambda *_: value, expected_targets=targets)
    with pytest.raises(ValueError):
        tuple(snapshot.read_labels(labels, label_seal, path, replace(seal, sha256="0" * 64), expected_targets=targets))


def test_question_hash_binds_absolute_target_time_and_episode_not_only_symbol():
    coordinate = snapshot.project_coordinate((target(),), session())
    question = coordinate.questions[0].question
    assert snapshot.question_digest(question) != snapshot.question_digest(
        replace(question, path=replace(question.path, target=question.path.target + Fraction(1))))
    assert snapshot.question_digest(question) != snapshot.question_digest(
        replace(question, episode_id=(question.episode_id[0], question.episode_id[1] + 1)))


def test_original_target_duplicates_and_bounds_are_explicit(tmp_path):
    original = target()
    with pytest.raises(ValueError, match="duplicate_original_target"):
        snapshot.write_features(tmp_path / "one", (), expected_targets=(original, original))
    with pytest.raises(ValueError, match="original_roster"):
        snapshot.write_features(tmp_path / "two", (), expected_targets=())
    with pytest.raises(ValueError, match="invalid_original_target"):
        snapshot.write_features(tmp_path / "three", (), expected_targets=(replace(original, metadata=b"x" * 4097),))
