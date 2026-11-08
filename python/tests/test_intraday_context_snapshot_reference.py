"""Independent EI snapshot audit against invented original session callbacks."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import json

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_snapshot as producer
from research import intraday_context_snapshot_reference as reference
from research.intraday_ed_inputs import InputRefusal, MINUTE, Minute, SessionInputs
from research.intraday_ed_sources import TargetCoordinate


OPEN = datetime(2031, 2, 12, 9, tzinfo=timezone(timedelta(hours=9)))
DAY = OPEN.date().isoformat()
BASE = ((100, 102, 98, 100), (100, 103, 99, 102), (102, 104, 100, 103),
        (103, 116, 102, 114), (114, 114, 111, 112), (112, 116, 111, 114))


def at(minutes):
    return OPEN + minutes * MINUTE


def target(symbol="invented", rank=1, scenario="S1"):
    return TargetCoordinate(scenario, symbol, DAY, rank, 60, "observed", wire.encode({"synthetic": True}))


def session(symbol="invented", bars=None, closing=60):
    bars = bars or (*BASE, (114, 118, 113, 117), (118, 125, 117, 124), *((124, 125, 123, 124),) * 4)
    rows = {at(index): Minute(symbol, at(index), at(index + 3), tuple(map(Decimal, bar)), Decimal(10))
            for number, bar in enumerate(bars) if bar is not None for index in range(number * 5, number * 5 + 5)}
    return SessionInputs(symbol, OPEN, at(closing), rows)


def bundle(tmp_path, *, targets=None, sessions=None, label_sessions=None):
    targets = targets or (target(), target(scenario="S2"))
    sessions = sessions or {(DAY, "invented"): session()}
    label_sessions = sessions if label_sessions is None else label_sessions
    groups = {}
    for row in targets:
        groups.setdefault((row.session, row.symbol), []).append(row)
    features = tmp_path / "features.jsonl"
    labels = tmp_path / "labels.jsonl"
    coordinates = (producer.project_coordinate(tuple(groups[key]), sessions.get(key)) for key in sorted(groups))
    feature_seal = producer.write_features(features, coordinates, expected_targets=targets)
    label_seal = producer.write_labels(labels, features, feature_seal, lambda day, symbol: label_sessions.get((day, symbol)),
                                       expected_targets=targets)
    return features, feature_seal, labels, label_seal, targets, sessions


def audit(values, *, callbacks=None, checkpoint=None):
    features, feature_seal, labels, label_seal, targets, sessions = values

    def original(day, symbol):
        if callbacks is not None:
            callbacks.append((day, symbol))
        return sessions.get((day, symbol))

    return reference.verify_snapshot(features, feature_seal, labels, label_seal, expected_targets=targets,
        reference_session=original, checkpoint=checkpoint or (lambda _: None))


def rewrite(path, seal, change):
    records = [json.loads(raw) for raw in path.read_bytes().splitlines()]
    change(records)
    raw = b"".join(wire.encode(row) for row in records)
    path.chmod(0o600)
    path.write_bytes(raw)
    path.chmod(0o400)
    return replace(seal, sha256=wire.digest(raw), size=len(raw))


def rebind_labels(values, feature_seal):
    features, _, labels, label_seal, targets, sessions = values
    rebound = rewrite(labels, label_seal, lambda rows: rows[0].update(feature_sha256=feature_seal.sha256,
        feature_size=feature_seal.size, feature_records=feature_seal.records))
    return features, feature_seal, labels, rebound, targets, sessions


def test_entire_original_roster_features_labels_and_counts_are_verified(tmp_path):
    values = bundle(tmp_path)
    calls, checks = [], []
    proof = audit(values, callbacks=calls, checkpoint=checks.append)
    assert proof["verified"] and proof["full_eof_verified"] and proof["all_original_coordinates_verified"]
    assert proof["original_coordinates"] == proof["reference_session_calls"] == 1
    assert proof["original_memberships"] == 2 and calls == [(DAY, "invented")]
    union = proof["counts"]["union"]
    assert union["coordinates"] == 1 and union["frames"] == 6 and union["questions"] == 2
    assert union["feature_states"] == {"approach": {"ready": 1}, "breakout": {"ready": 1}}
    assert proof["counts"]["scenarios"]["S1"] == proof["counts"]["scenarios"]["S2"] == union
    assert proof["counts_sha256"] == wire.digest(wire.encode(proof["counts"]))
    assert checks.count("ei_snapshot_reference_coordinate") == 1
    assert checks.count("ei_snapshot_reference_frame") == 6
    assert checks.count("ei_snapshot_reference_question_label") == 2


def test_absent_unknown_and_non_event_coordinates_are_loaded_and_counted(tmp_path):
    targets = (target("absent", 1), target("flat", 2), target("unknown", 3), target("valid", 4),
               target("valid", 4, "S2"))
    unknown = session("unknown")
    unknown = replace(unknown, minutes={key: row for key, row in unknown.minutes.items() if key != OPEN})
    sessions = {(DAY, "flat"): session("flat", ((100, 101, 99, 100),) * 12),
                (DAY, "unknown"): unknown, (DAY, "valid"): session("valid")}
    values = bundle(tmp_path, targets=targets, sessions=sessions)
    calls = []
    proof = audit(values, callbacks=calls)
    assert calls == [(DAY, symbol) for symbol in ("absent", "flat", "unknown", "valid")]
    assert proof["original_coordinates"] == 4 and proof["original_memberships"] == 5
    counts = proof["counts"]["union"]
    assert counts["coordinate_states"] == {"observed_session": 3, "session_missing": 1}
    assert counts["frame_states"]["unknown"] == counts["frame_states"]["no_anchor"] == 6
    assert proof["counts"]["scenarios"]["S1"]["coordinates"] == 4
    assert proof["counts"]["scenarios"]["S2"]["coordinates"] == 1


@pytest.mark.parametrize("condition", ["target_stop", "retest_target", "missing", "late", "late_recovered", "outside30"])
def test_independent_paths_preserve_ambiguity_missingness_maturity_and_shorter_horizons(tmp_path, condition):
    value = session(closing=50 if condition == "outside30" else 70)
    rows = dict(value.minutes)
    if condition == "target_stop":
        rows[at(32)] = replace(rows[at(32)], ohlc=tuple(map(Decimal, (114, 125, 110, 114))))
    elif condition == "retest_target":
        rows[at(32)] = replace(rows[at(32)], ohlc=tuple(map(Decimal, (114, 120, 113, 119))))
        rows[at(33)] = replace(rows[at(33)], ohlc=tuple(map(Decimal, (119, 125, 116, 124))))
    elif condition == "missing":
        del rows[at(32)]
    elif condition in {"late", "late_recovered"}:
        rows[at(32)] = replace(rows[at(32)], available_at=at(80 if condition == "late" else 60))
    values = bundle(tmp_path, sessions={(DAY, "invented"): replace(value, minutes=rows)})
    proof = audit(values)
    states = proof["counts"]["union"]["horizon_states"]["approach"]
    if condition in {"target_stop", "retest_target", "missing", "late"}:
        assert all(counts == {"unknown": 1} for counts in states.values())
    elif condition == "outside30":
        assert states == {"5": {"resolved": 1}, "15": {"resolved": 1}, "30": {"unsupported": 1}}
    else:
        assert states["5"] == {"resolved": 1}


@pytest.mark.parametrize("kind", ["early_price", "context_depth", "volume", "no_event_summary"])
def test_resealed_internally_consistent_feature_changes_fail_original_reconstruction(tmp_path, kind):
    values = bundle(tmp_path)
    path, seal = values[:2]

    def mutate(rows):
        coordinate = rows[1]
        feature = coordinate["questions"][0]
        if kind == "early_price":
            feature["prices"][0] = [str(Fraction(value) + 1) for value in feature["prices"][0]]
        elif kind == "context_depth":
            feature["context"][7] = "99"
        elif kind == "volume":
            feature["volumes"] = ["20"] * 30
            feature["volume_scale"] = "20"
        else:
            coordinate["frames"][-1][1] = "B"

    changed = rewrite(path, seal, mutate)
    # These changes are structurally valid; only the original reconstruction rejects them.
    assert tuple(producer.read_features(path, changed, expected_targets=values[4]))
    with pytest.raises(InputRefusal, match="original_feature_or_frame_differs"):
        audit(rebind_labels(values, changed))


@pytest.mark.parametrize("kind", ["path", "maturity", "reason", "state", "label_only_missing"])
def test_resealed_label_changes_fail_independent_first_touch_paths(tmp_path, kind):
    values = bundle(tmp_path)
    labels, seal = values[2:4]

    def mutate(rows):
        coordinate = rows[1]
        if kind == "label_only_missing":
            coordinate["status"] = "session_missing"
            for label in coordinate["labels"]:
                label["status"], label["results"] = "session_missing", []
            return
        result = coordinate["labels"][0]["results"][0]
        if kind == "path":
            result[3] = "P1" if result[3] != "P1" else "P2"
        elif kind == "maturity":
            result[4] = producer._time(at(50))
        elif kind == "reason":
            result[5] = "changed_reason"
        else:
            result[6] = not result[6]

    changed = rewrite(labels, seal, mutate)
    with pytest.raises(InputRefusal, match="original_labels_differ"):
        audit((*values[:3], changed, *values[4:]))


def test_corrupted_label_question_identity_and_types_are_not_only_numeric_equal(tmp_path):
    values = bundle(tmp_path)
    changed = rewrite(values[2], values[3], lambda rows: rows[1]["labels"][0]["results"][0].__setitem__(6, 0))
    with pytest.raises(InputRefusal, match="original_labels_differ"):
        audit((*values[:3], changed, *values[4:]))


@pytest.mark.parametrize("file", ["features", "labels"])
def test_full_eof_and_footer_are_verified_after_last_coordinate(tmp_path, file):
    values = list(bundle(tmp_path))
    index = 0 if file == "features" else 2
    values[index + 1] = rewrite(values[index], values[index + 1], lambda rows: rows[-1].update(records=999))
    if file == "features":
        values = rebind_labels(tuple(values), values[1])
    with pytest.raises(ValueError, match="footer_or_EOF"):
        audit(tuple(values))


def test_original_metadata_and_expected_population_cannot_be_substituted(tmp_path):
    values = bundle(tmp_path)
    changed_targets = tuple(replace(row, metadata=b"changed") for row in values[4])
    with pytest.raises(ValueError):
        audit((*values[:4], changed_targets, values[5]))
    with pytest.raises(ValueError):
        audit((*values[:4], values[4][:1], values[5]))


def test_same_date_original_calendar_cannot_change_between_names(tmp_path):
    targets = target("a", 1), target("b", 2)
    sessions = {(DAY, "a"): session("a"), (DAY, "b"): session("b", closing=65)}
    with pytest.raises(InputRefusal, match="same_date_calendar_changed"):
        audit(bundle(tmp_path, targets=targets, sessions=sessions))


def test_reference_does_not_call_producer_science_and_reads_streams_only_once(tmp_path, monkeypatch):
    values = bundle(tmp_path)
    feature_reads, label_reads = [], []
    original_features, original_read = reference.read_features, reference._read

    def record_features(*args, **kwargs):
        feature_reads.append(args[0])
        return original_features(*args, **kwargs)

    def record_labels(*args, **kwargs):
        label_reads.append(args[0])
        return original_read(*args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("producer science called")

    monkeypatch.setattr(reference, "read_features", record_features)
    monkeypatch.setattr(reference, "_read", record_labels)
    for name in ("project_coordinate", "project_context_features", "scan_context_questions", "scan_context_frames", "label_horizons"):
        monkeypatch.setattr(producer, name, forbidden)
    assert audit(values)["verified"]
    assert feature_reads == [values[0]] and label_reads == [values[2]]


def test_independent_counts_do_not_use_producer_summary_properties(tmp_path, monkeypatch):
    values = bundle(tmp_path)
    monkeypatch.setattr(producer.ProjectedCoordinate, "feature_support", property(lambda _: {"wrong": 999}))
    monkeypatch.setattr(producer.LabeledCoordinate, "horizon_support", property(lambda _: {"wrong": 999}))
    proof = audit(values)
    assert proof["counts"]["union"]["questions"] == 2
    assert proof["counts"]["union"]["feature_states"]["approach"] == {"ready": 1}


def test_callback_failures_and_checkpoint_cancellation_are_not_support_unknowns(tmp_path):
    values = bundle(tmp_path)

    def stop(_):
        raise RuntimeError("invented cancellation")

    with pytest.raises(RuntimeError, match="invented cancellation"):
        audit(values, checkpoint=stop)
