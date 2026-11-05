"""Invented EF snapshot geometry, byte binding and label separation."""

from dataclasses import replace
from datetime import datetime, timedelta
from fractions import Fraction
import hashlib
from itertools import product
import os
import sqlite3
import stat

import pytest

from research import intraday_chart_snapshot as snapshot
from research.intraday_chart_tensors import ChartTensor, PeerTensor
from research.intraday_ed_sources import TargetCoordinate


pytestmark = pytest.mark.skipif(os.name != "posix", reason="private native SQLite boundary")
DAY = "2025-01-02"
OPENING = datetime.fromisoformat(f"{DAY}T10:00+09:00")
CLOSING = OPENING + timedelta(minutes=330)
EVAL_OPEN = OPENING + timedelta(days=1)
A, B = "KRX:000001", "KRX:000002"
MINUTE = (0.0, 1.0, -1.0, 0.125, 0.125, 2.0, 0.5, 0.0, 0.0, 0.0)
CHART = ChartTensor((MINUTE,) * 60, ((0.0, 0.5, -0.5, 0.0, 1.0, 0.0),) * 24,
                    (60.0 / 390.0, 0.0, 0.0, 0.0, 0.0))
PEER = PeerTensor(((0.125, -0.5, 0.625),) * 60, 0.625)
PROVENANCE = ((A, b'{"CC":"A","CX":"A"}'), (B, b'{"CC":"missing","CX":"missing"}'))


def checkpoint(_phase):
    pass


def inputs():
    metadata = snapshot.SnapshotMetadata(
        (DAY,),
        (TargetCoordinate("S2", B, DAY, 2, None, "no_stored_rows_unknown_reason", b'{"scenario":"S2"}'),
         TargetCoordinate("S2", A, DAY, 1, 170, "stored_rows_completeness_unverified", b'{"scenario":"S2"}'),
         TargetCoordinate("S1", A, DAY, 1, 170, "stored_rows_completeness_unverified", b'{"scenario":"S1"}')),
        b'{"synthetic":"test"}',
    )
    rows = []
    for offset, symbol in product(snapshot.QUOTE_OFFSETS, (A, B)):
        peers = ()
        if offset in snapshot.FEATURE_OFFSETS:
            peers = ((snapshot.PeerRow("S1", PEER, b'{"complete":5}'),
                      snapshot.PeerRow("S2", None, b'{"complete":0}')) if symbol == A else
                     (snapshot.PeerRow("S2", None, b'{"complete":0}'),))
        chart = CHART if offset == 60 and symbol == A else None
        quote = Fraction(101_001, 29) if symbol == A and offset < 315 else None
        state = "outside_clock" if offset >= 315 else "unavailable" if quote is None else "available"
        row = snapshot.SnapshotRow(DAY, symbol, offset, chart, peers, quote, state, b'{"clock":2}')
        label = None
        if offset in snapshot.ENTRY_OFFSETS:
            primary = snapshot.LabelHead(Fraction(1, 333), OPENING + timedelta(minutes=96))
            late = snapshot.LabelHead(Fraction(0), EVAL_OPEN)
            heads = (primary, late, None, None, None, None) if chart is not None else (None,) * 6
            label = snapshot.LabelRow(DAY, symbol, offset, heads, b'{"why":"invented"}')
        rows.append((row, label))
    return metadata, snapshot.SnapshotDay(DAY, OPENING, CLOSING, PROVENANCE, tuple(rows))


@pytest.fixture
def paths(tmp_path):
    tmp_path.chmod(0o700)
    return tmp_path / "features.sqlite", tmp_path / "labels.sqlite"


def test_independent_feature_and_label_files_keep_original_unknown_targets(paths):
    feature_path, label_path = paths
    metadata, day = inputs()
    seal = snapshot.write_snapshot(*paths, metadata, (day,), checkpoint)
    for path, digest in zip(paths, (seal.features_sha256, seal.labels_sha256), strict=True):
        assert stat.S_IMODE(path.stat().st_mode) == 0o400
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    moved_labels = label_path.rename(label_path.with_name("retained-labels.sqlite"))
    with snapshot.open_feature_snapshot(feature_path, seal, checkpoint) as reader:
        assert reader.metadata.sessions == (DAY,)
        assert set(reader.metadata.targets) == set(metadata.targets)
        assert reader.metadata.provenance == metadata.provenance
        assert tuple(reader.days()) == (snapshot.DayMetadata(DAY, OPENING, CLOSING, PROVENANCE),)
        assert tuple(reader.rows()) == tuple(row for row, _ in day.rows)
        assert list(reader.feature_support("S1"))[0] == (DAY, A, 60, True, True)
        support = list(reader.feature_support("S2", (DAY,)))
        assert len(support) == 2 * len(snapshot.ENTRY_OFFSETS)
        assert support[:2] == [(DAY, A, 60, True, False), (DAY, B, 60, False, False)]
        assert list(reader.feature_support("S2", ())) == []
    with sqlite3.connect(feature_path.as_uri() + "?mode=ro", uri=True) as reader:
        names = {row[0] for row in reader.execute("SELECT name FROM sqlite_schema")}
        assert "labels" not in names
        assert reader.execute("SELECT length(chart) FROM features WHERE chart IS NOT NULL").fetchall() == [(749 * 8,)]
        assert reader.execute("SELECT DISTINCT length(tensor) FROM peers WHERE tensor IS NOT NULL").fetchall() == [(181 * 8,)]
    with snapshot.open_label_snapshot(moved_labels, seal, checkpoint) as reader:
        assert tuple(reader.audit_rows()) == tuple(label for _, label in day.rows if label is not None)
        first = next(reader.training_rows((DAY,), EVAL_OPEN))
        assert first.heads == (day.rows[0][1].heads[0], None, None, None, None, None)
        assert first.evidence == b""
        later = next(reader.training_rows((DAY,), EVAL_OPEN + timedelta(minutes=1)))
        assert later.heads[1].value == 0
        with pytest.raises(snapshot.SnapshotRefusal, match="invalid_training_label_sessions"):
            list(reader.training_rows((DAY,), OPENING))
        with pytest.raises(snapshot.SnapshotRefusal, match="invalid_snapshot_session_selection"):
            list(reader.training_rows(("2025-01-01",), EVAL_OPEN))


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "absent_label", "bad_label_clock", "extra"])
def test_geometry_refusals_leave_unsealed_files(paths, mutation):
    metadata, day = inputs()
    rows = list(day.rows)
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows[1] = rows[0]
    elif mutation == "absent_label":
        rows[0] = (rows[0][0], None)
    elif mutation == "bad_label_clock":
        row, label = rows[0]
        rows[0] = row, replace(label, heads=(snapshot.LabelHead(Fraction(1), OPENING), *label.heads[1:]))
    else:
        rows.append(None)
    with pytest.raises(snapshot.SnapshotRefusal, match="snapshot_"):
        snapshot.write_snapshot(*paths, metadata, (replace(day, rows=rows),), checkpoint)
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in paths)


@pytest.mark.parametrize("minute,match", [
    ((float("nan"), *MINUTE[1:]), "shape_or_value"),
    (MINUTE[:-1], "shape_or_value"),
    ((*MINUTE[:9], 0.25), "minute_mask"),
    ((*MINUTE[:8], 1.0, 0.0), "minute_mask"),
])
def test_refuses_nonfinite_malformed_or_inconsistent_binary64_inputs(paths, minute, match):
    metadata, day = inputs()
    first, label = day.rows[0]
    chart = replace(CHART, minute=(minute, *CHART.minute[1:]))
    with pytest.raises(snapshot.SnapshotRefusal, match=match):
        snapshot.write_snapshot(*paths, metadata,
                                (replace(day, rows=((replace(first, chart=chart), label), *day.rows[1:])),),
                                checkpoint)


def test_file_identity_pin_and_no_overwrite(paths):
    metadata, day = inputs()
    seal = snapshot.write_snapshot(*paths, metadata, (day,), checkpoint)
    with pytest.raises(FileExistsError):
        snapshot.write_snapshot(*paths, metadata, (day,), checkpoint)
    assert hashlib.sha256(paths[0].read_bytes()).hexdigest() == seal.features_sha256
    with pytest.raises(snapshot.SnapshotRefusal, match="snapshot_fingerprint_differs"):
        with snapshot.open_feature_snapshot(paths[0], replace(seal, fingerprint="0" * 64), checkpoint):
            pytest.fail("accepted the wrong fingerprint")
    paths[0].chmod(0o600)
    with pytest.raises(snapshot.SnapshotRefusal, match="snapshot_file_identity_differs"):
        with snapshot.open_feature_snapshot(paths[0], seal, checkpoint):
            pytest.fail("accepted a writable file")
    with paths[0].open("ab") as stream:
        stream.write(b"unregistered bytes after the final SQLite page")
    paths[0].chmod(0o400)
    with pytest.raises(snapshot.SnapshotRefusal, match="snapshot_file_pin_differs"):
        with snapshot.open_feature_snapshot(paths[0], seal, checkpoint):
            pytest.fail("accepted a file with a changed full digest")


def test_label_file_binds_exact_feature_bytes_even_for_same_population(paths):
    metadata, day = inputs()
    seal = snapshot.write_snapshot(*paths, metadata, (day,), checkpoint)
    other = tuple(path.with_name("other-" + path.name) for path in paths)
    row, label = day.rows[0]
    changed = replace(day, rows=((replace(row, evidence=b"changed"), label), *day.rows[1:]))
    other_seal = snapshot.write_snapshot(*other, metadata, (changed,), checkpoint)
    assert seal.fingerprint == other_seal.fingerprint
    assert seal.features_sha256 != other_seal.features_sha256
    mixed = replace(seal, labels_sha256=other_seal.labels_sha256)
    with pytest.raises(snapshot.SnapshotRefusal, match="snapshot_label_binding_differs"):
        with snapshot.open_label_snapshot(other[1], mixed, checkpoint):
            pytest.fail("accepted labels bound to a different feature file")


def test_sqlite_sidecar_cannot_be_consumed_or_replaced(paths):
    metadata, day = inputs()
    feature_path = paths[0]
    sidecar = feature_path.with_name(feature_path.name + "-journal")
    sidecar.write_bytes(b"existing evidence")
    with pytest.raises(snapshot.SnapshotRefusal, match="snapshot_sidecar_exists"):
        snapshot.write_snapshot(*paths, metadata, (day,), checkpoint)
    assert sidecar.read_bytes() == b"existing evidence"
    assert not feature_path.exists()
