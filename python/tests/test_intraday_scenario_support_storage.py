"""Invented EH saved rows, complete seals and original population counts."""

from dataclasses import replace
import hashlib
import json
import os

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_scenario_support_storage as storage
from research.intraday_ed_sources import SourceRefusal

from test_intraday_scenario_support import A, B, C, DAYS, InventedReader, chunk, universe


def test_deterministic_artifact_and_independent_population_receipt(tmp_path, monkeypatch):
    original = universe()
    chunks = (chunk(DAYS[0], A), chunk(DAYS[1], A, count=15), chunk(DAYS[2], B, count=20))
    left, right = tmp_path / "left.jsonl", tmp_path / "right.jsonl"
    seal = storage.write_support(left, original, InventedReader(*chunks), lambda _phase: None)
    assert storage.write_support(right, original, InventedReader(*chunks), lambda _phase: None) == seal
    saved = left.read_bytes()
    assert right.read_bytes() == saved
    assert seal == storage.SupportSeal(hashlib.sha256(saved).hexdigest(), len(saved), 6)
    assert b"invented metadata" not in saved and b"100.0000000000000000001" not in saved
    assert json.loads(saved.splitlines()[1])["targets"][0]["metadata_sha256"] == hashlib.sha256(
        b"invented metadata 2099-02-02 S1",
    ).hexdigest()
    with pytest.raises(FileExistsError):
        storage.write_support(left, original, InventedReader(), lambda _phase: None)
    assert left.read_bytes() == saved

    def forbidden(*_args, **_kwargs):
        pytest.fail("Artifact verifier consulted producer math")

    monkeypatch.setattr(storage.producer, "iter_support_records", forbidden)
    monkeypatch.setattr(storage.producer, "measure_price_support", forbidden)
    result = storage.verify_support(left, seal, original, InventedReader(*chunks), lambda _phase: None)
    assert result.verification.calendar_sessions == 3
    assert result.verification.original_targets == 11
    assert result.verification.union_records == 6
    union, s1, s2, overlap = result.populations
    assert [group.population for group in result.populations] == ["union", "S1", "S2", "overlap"]
    assert [group.counts.coordinates for group in result.populations] == [6, 6, 5, 5]
    assert [group.counts.price_chunk_absent_records for group in result.populations] == [3, 3, 3, 3]
    assert [dict(group.counts.complete_coverage_counts) for group in result.populations] == [
        {"first15": 3, "first20": 2, "first60": 1, "full": 1},
        {"first15": 3, "first20": 2, "first60": 1, "full": 1},
        {"first15": 2, "first20": 2, "first60": 1, "full": 1},
        {"first15": 2, "first20": 2, "first60": 1, "full": 1},
    ]
    assert dict(union.counts.prior_status_counts) == {
        "outside_calendar": 2, "outside_union": 2, "complete": 1, "chunk_absent": 1,
    }
    assert dict(s2.counts.prior_status_counts) == {"outside_calendar": 2, "outside_union": 2, "chunk_absent": 1}
    assert dict(s2.counts.cross_session_basis_counts) == {"unknown": 5}
    assert dict(union.prior_full_counts) == {"observed": 390, "missing": 390}
    assert union.coverage_counts == result.verification.coverage_counts
    assert dict(s1.by_session)[DAYS[1]].coordinates == 2
    assert dict(overlap.by_session)[DAYS[1]].coordinates == 1
    assert dict(union.by_symbol)[A].coordinates == dict(union.by_symbol)[B].coordinates == 2
    assert dict(union.by_symbol)[C].price_chunk_absent_records == 2


def _replace_record(lines, update):
    value = json.loads(lines[1])
    update(value)
    lines[1] = wire.encode(value)
    return b"".join(lines)


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda lines: b"".join(lines) + b"\n", "support_artifact_trailing_bytes"),
        (lambda lines: b"".join(lines[:-1]), "support_artifact_framing_or_bound"),
        (lambda lines: b"".join(lines)[:-1], "support_artifact_framing_or_bound"),
        (lambda lines: b"".join(lines[:-1]) + wire.encode({"kind": "footer", "records": 5}),
         "support_artifact_footer_differs"),
        (lambda lines: b"".join((lines[0], b" " + lines[1], *lines[2:])), "noncanonical_saved_support_record"),
        (lambda lines: b"".join((lines[0], lines[1].replace(b'"kind":', b'"kind":"record","kind":', 1), *lines[2:])),
         "noncanonical_saved_support_record"),
        (lambda lines: b"".join((lines[0], b" " * storage.MAX_LINE + lines[1], *lines[2:])),
         "support_artifact_framing_or_bound"),
        (lambda lines: b"".join((lines[0], lines[2], lines[1], *lines[3:])), "stored_support_record_differs"),
        (lambda lines: b"".join((*lines[:-2], lines[-1])), "support_artifact_footer_differs"),
        (lambda lines: _replace_record(lines, lambda row: row.update(price_chunk_present=0)),
         "stored_support_record_differs"),
        (lambda lines: _replace_record(lines, lambda row: row["targets"][0].update(metadata_sha256="0" * 64)),
         "noncanonical_saved_support_record"),
        (lambda lines: _replace_record(lines, lambda row: row["targets"][0].update(rank=2)),
         "stored_support_record_differs"),
        (lambda lines: _replace_record(lines, lambda row: row.update(coverage="invalid")),
         "invalid_saved_support_record"),
    ],
)
def test_resealed_tampering_refuses(tmp_path, mutate, reason):
    original = universe()
    path = tmp_path / storage.FILENAME
    seal = storage.write_support(path, original, InventedReader(), lambda _phase: None)
    changed = mutate(path.read_bytes().splitlines(keepends=True))
    path.write_bytes(changed)
    resealed = replace(seal, sha256=hashlib.sha256(changed).hexdigest(), size=len(changed))
    with pytest.raises(SourceRefusal, match=reason):
        storage.verify_support(path, resealed, original, InventedReader(), lambda _phase: None)


def test_external_seal_and_original_calendar_identity_are_required(tmp_path):
    original = universe()
    path = tmp_path / storage.FILENAME
    seal = storage.write_support(path, original, InventedReader(), lambda _phase: None)
    for new_seal, reason in (
        (replace(seal, sha256="0" * 64), "support_artifact_seal_differs"),
        (replace(seal, size=seal.size - 1), "support_artifact_size_differs"),
        (replace(seal, records=seal.records - 1), "invalid_support_artifact_seal"),
    ):
        with pytest.raises(SourceRefusal, match=reason):
            storage.verify_support(path, new_seal, original, InventedReader(), lambda _phase: None)
    with pytest.raises(SourceRefusal, match="support_artifact_header_differs"):
        storage.verify_support(path, seal, replace(original, raw_sha256="0" * 64),
                               InventedReader(), lambda _phase: None)


def test_late_source_change_refuses_even_with_valid_saved_eof(tmp_path):
    original = universe()
    path = tmp_path / storage.FILENAME
    seal = storage.write_support(path, original, InventedReader(), lambda _phase: None)
    reader = InventedReader(fail_check=2 * seal.records + 1)
    with pytest.raises(SourceRefusal, match="invented_reader_changed"):
        storage.verify_support(path, seal, original, reader, reader.events.append)
    assert reader.events[-2:] == ["eh_support_read_record", "unchanged"]


def test_artifact_identity_checked_at_end_of_verified_read(tmp_path):
    original = universe()
    path = tmp_path / storage.FILENAME
    seal = storage.write_support(path, original, InventedReader(), lambda _phase: None)
    read_count = 0

    def touch_at_footer(phase):
        nonlocal read_count
        if phase == "eh_support_read_record":
            read_count += 1
            if read_count == seal.records + 2:
                before = path.stat()
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))

    with pytest.raises(SourceRefusal, match="support_artifact_changed_during_read"):
        storage.verify_support(path, seal, original, InventedReader(), touch_at_footer)


def test_overlap_receipt_retains_original_date_with_zero_coordinates(tmp_path):
    original = universe()
    original = replace(original, targets=tuple(
        replace(target, symbol=B) if (target.session, target.scenario) == (DAYS[1], "S2") else target
        for target in original.targets
    ))
    path = tmp_path / storage.FILENAME
    seal = storage.write_support(path, original, InventedReader(), lambda _phase: None)
    result = storage.verify_support(path, seal, original, InventedReader(), lambda _phase: None)
    assert [group.counts.coordinates for group in result.populations] == [7, 6, 5, 4]
    overlap_dates = dict(result.populations[-1].by_session)
    assert tuple(overlap_dates) == DAYS
    assert overlap_dates[DAYS[1]].coordinates == 0
    assert dict(overlap_dates[DAYS[1]].complete_coverage_counts) == {
        "first15": 0, "first20": 0, "first60": 0, "full": 0,
    }
