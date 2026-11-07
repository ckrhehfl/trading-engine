"""Invented-only EH worker and exact pair-envelope regressions."""

from dataclasses import asdict, replace
import json

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_ed_sources as sources
from research import intraday_scenario_snapshot as snapshot
from research import intraday_scenario_snapshot_unit as unit


def _checkpoint(_):
    pass


def _sealed(path, raw):
    path.write_bytes(raw)
    path.chmod(0o400)


def _pair(tmp_path):
    feature_path = tmp_path / "features"
    label_path = tmp_path / "labels"
    features = b"".join(map(wire.encode, ({"schema": snapshot.FEATURE_SCHEMA}, {"invented": True}, {"records": 1})))
    feature_seal = snapshot.SnapshotSeal(snapshot.FEATURE_SCHEMA, wire.digest(features), len(features), 1)
    labels = wire.encode(snapshot._label_header(feature_seal)) + wire.encode({"records": 0})
    label_seal = snapshot.SnapshotSeal(snapshot.LABEL_SCHEMA, wire.digest(labels), len(labels), 0)
    _sealed(feature_path, features)
    _sealed(label_path, labels)
    path = tmp_path / unit.OUTPUT
    seal = unit._pack_bundle(path, feature_path, feature_seal, label_path, label_seal, _checkpoint)
    return path, seal, features, feature_seal, labels, label_seal


def test_bundle_round_trip_checks_pair_and_refuses_to_clobber(tmp_path):
    path, seal, features, feature_seal, labels, label_seal = _pair(tmp_path)
    assert (seal.sha256, seal.size) == wire.file_digest(path, unit.MAX_OUTPUT, _checkpoint)
    assert unit.verify_bundle(path, _checkpoint) == (feature_seal, label_seal)
    directory = tmp_path / "unpacked"
    directory.mkdir(mode=0o700)
    feature_copy, feature_copy_seal, label_copy, label_copy_seal = unit.open_bundle(path, directory, _checkpoint)
    assert (feature_copy_seal, label_copy_seal) == (feature_seal, label_seal)
    assert (feature_copy.read_bytes(), label_copy.read_bytes()) == (features, labels)
    assert feature_copy.stat().st_mode & 0o777 == label_copy.stat().st_mode & 0o777 == 0o400
    with pytest.raises(ValueError, match="fresh private directory"):
        unit.open_bundle(path, directory, _checkpoint)


@pytest.mark.parametrize(("change", "message"), (
    ("truncated", "member length"),
    ("trailing", "member length"),
    ("hash", "member hash"),
    ("noncanonical", "noncanonical"),
    ("bound", "header exceeded"),
))
def test_bundle_rejects_changed_framing_or_payload(tmp_path, change, message):
    path, _, _, _, _, _ = _pair(tmp_path)
    raw = path.read_bytes()
    if change == "truncated":
        raw = raw[:-1]
    elif change == "trailing":
        raw += b"\n"
    elif change == "hash":
        raw = raw.replace(b'"invented":true', b'"invented":null')
    elif change == "noncanonical":
        raw = b" " + raw
    else:
        raw = b" " * (unit.MAX_HEADER + 1) + raw
    corrupt = tmp_path / "corrupt"
    _sealed(corrupt, raw)
    with pytest.raises(ValueError, match=message):
        unit.verify_bundle(corrupt, _checkpoint)


def test_bundle_refuses_pair_from_different_feature_snapshot(tmp_path):
    _, _, _, feature_seal, labels, label_seal = _pair(tmp_path)
    wrong = labels.replace(feature_seal.sha256.encode(), b"0" * 64)
    wrong_path = tmp_path / "wrong-labels"
    _sealed(wrong_path, wrong)
    with pytest.raises(ValueError, match="member header differs"):
        unit._pack_bundle(tmp_path / "mixed", tmp_path / "features", feature_seal, wrong_path,
                          replace(label_seal, sha256=wire.digest(wrong)), _checkpoint)


def test_worker_preserves_populations_and_seals_features_before_labels(tmp_path):
    received = tmp_path / "received"
    received.mkdir(mode=0o700)
    envelope = unit.make_invented(received, 2, _checkpoint)
    events = []

    def checkpoint(event):
        if event in {"eh_snapshot_after_feature_day", "eh_snapshot_label_coordinate"}:
            events.append(event)

    receipt = unit.compute(tmp_path, envelope, None, invented_only=True, checkpoint=checkpoint).payload()
    assert events[:3] == ["eh_snapshot_after_feature_day"] * 2 + ["eh_snapshot_label_coordinate"]
    proof = receipt["independent_verification"]
    assert proof["schema"] == unit.SCHEMA and proof["invented_only"]
    assert proof["verified"] and proof["full_eof_verified"] and proof["all_original_coordinates_verified"]
    assert not proof["historical_availability_certified"] and not proof["full_geometry"]
    assert receipt["candidate_counts"] == {"S1": {"original": 40}, "S2": {"original": 40}}
    assert proof["raw_byte_audit"]["raw_keys"] == 42
    summary = proof["snapshot_summary"]
    assert summary["sessions"] == 2 and summary["union_coordinates"] == 42
    assert summary["questions"] > 0 and summary["peer_ready"] > 0
    assert proof["support_totals"] == summary["scenarios"]
    assert proof["summary_sha256"] == wire.digest(wire.encode(summary))
    assert summary["universe_sha256"] == envelope.manifest[0].sha256
    assert proof["input_manifest_sha256"] == wire.digest(wire.encode(proof["input_manifest"]))
    feature_seal, label_seal = unit.verify_bundle(tmp_path / unit.OUTPUT, _checkpoint)
    assert summary["members"] == {"features": asdict(feature_seal), "labels": asdict(label_seal)}
    assert (feature_seal.records, label_seal.records) == (2, summary["questions"])
    assert not list(tmp_path.glob("eh-snapshot-members-*"))


def test_noncausal_roster_is_refused_before_price_lookup(tmp_path):
    envelope = unit.make_invented(tmp_path, 1, _checkpoint)
    universe = sources.load_universe((tmp_path / "CB").read_bytes(), envelope.manifest[0].sha256, expected_sessions=1)
    day = universe.sessions[0]
    targets = tuple(replace(item, metadata=sources._canonical(
        dict(json.loads(item.metadata), selected_at=f"{day}T15:00:00+09:00")))
        if item.scenario == "S2" else item for item in universe.targets)

    class NoPriceReader:
        def assert_unchanged(self):
            pytest.fail("price reader touched before complete causal roster validation")

    with pytest.raises(ValueError, match="roster is noncausal"):
        list(unit._projected_days(replace(universe, targets=targets), NoPriceReader(), _checkpoint))
