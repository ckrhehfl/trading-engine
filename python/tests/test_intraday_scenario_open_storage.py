"""Invented census round trip, exact population accounting and resealed mutations."""

from dataclasses import replace
from hashlib import sha256
import json
import os

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_scenario_open_storage as storage
from research.intraday_ed_sources import SourceRefusal

from test_intraday_scenario_support import A, B, C, DAYS, InventedReader, chunk, universe


def _event_chunk(day, symbol):
    raw = chunk(day, symbol, count=75)
    for index, row in enumerate(raw["rows"]):
        if 15 <= index < 20:
            row.update(open="100", high="101", low="99", close="101")
        elif 20 <= index < 25:
            row.update(open="102", high="103", low="102", close="102")
        elif index >= 25:
            row.update(open="103", high="106", low="102", close="105")
    return raw


def test_opening_census_keeps_every_coordinate_and_reconstructs_from_raw(tmp_path, monkeypatch):
    original = universe()
    raw = (_event_chunk(DAYS[0], A), chunk(DAYS[1], A, count=20), chunk(DAYS[2], C))
    path, duplicate = tmp_path / storage.FILENAME, tmp_path / "second.jsonl"
    seal = storage.write_opening_support(path, original, InventedReader(*raw), lambda _: None)
    assert storage.write_opening_support(duplicate, original, InventedReader(*raw), lambda _: None) == seal
    saved = path.read_bytes()
    assert duplicate.read_bytes() == saved and seal.sha256 == sha256(saved).hexdigest()
    with pytest.raises(FileExistsError):
        storage.write_opening_support(path, original, InventedReader(*raw), lambda _: None)
    assert path.read_bytes() == saved and b"invented metadata" not in saved
    header, *records, footer = [json.loads(line) for line in saved.splitlines()]
    assert header["schema"] == storage.SCHEMA and footer == {"kind": "footer", "records": 6}
    first = records[0]
    assert first["event"]["kind"] == "prepared"
    assert first["event"]["level"] == "101" and first["event"]["band"] == "1/5"
    approach, breakout = first["initial_paths"]
    assert approach["stage"] == "approach" and breakout["stage"] == "breakout"
    assert approach["question"]["stop"] == "503/5" and breakout["question"]["target"] == "522/5"
    assert [(item["horizon_minutes"], item["status"], item["path"]) for item in breakout["labels"]] == [
        (5, "resolved", "P1"), (15, "resolved", "P1"), (30, "resolved", "P1"),
    ]

    def forbidden(*_args, **_kwargs):
        pytest.fail("Census verifier consulted production scenario math")

    monkeypatch.setattr(storage.producer, "opening_support_observation", forbidden)
    receipt = storage.verify_opening_support(path, seal, original, InventedReader(*raw), lambda _: None)
    totals = receipt.support_totals
    assert [totals[name]["coordinates"] for name in ("union", "S1", "S2", "overlap")] == [6, 6, 5, 5]
    assert totals["union"]["scan_status_counts"] == {
        "not_yet_eligible": 0, "known_no_event": 1, "support_unknown": 4, "event": 1,
    }
    assert totals["union"]["price_chunk_absent_records"] == 3
    for name in totals:
        for stage in ("approach", "breakout"):
            counts = totals[name]["stages"][stage]
            assert counts["initial"] == counts["first_hour"] == counts["question_supported"] == 1
            assert counts["horizons"]["30"]["path_counts"] == {"P1": 1, "P2": 0, "P3": 0, "P4": 0}
    assert receipt.by_session["S2"][DAYS[1]] == {"coordinates": 1}
    assert receipt.by_symbol["union"][A]["approach_resolved_30m"] == 1
    assert receipt.by_symbol["union"][B] == {"coordinates": 2}


def _mutate(lines, change):
    value = json.loads(lines[1])
    change(value)
    return b"".join((lines[0], wire.encode(value), *lines[2:]))


def test_unformable_question_retains_all_horizon_denominators(tmp_path):
    original = universe()
    raw = chunk(DAYS[0], A, count=90)
    for row in raw["rows"][15:20]:
        row["high"] = "1000"
    path = tmp_path / storage.FILENAME
    seal = storage.write_opening_support(path, original, InventedReader(raw), lambda _: None)
    first = json.loads(path.read_bytes().splitlines()[1])
    approach, = first["initial_paths"]
    assert approach["question"] is None
    assert approach["issues"][0]["reason"] == "invalid_prepared_risk_boundary"
    assert [(label["horizon_minutes"], label["status"], label["path"]) for label in approach["labels"]] == [
        (5, "unsupported", None), (15, "unsupported", None), (30, "unsupported", None),
    ]
    verified = storage.verify_opening_support(path, seal, original, InventedReader(raw), lambda _: None)
    counts = verified.support_totals["union"]["stages"]["approach"]
    assert counts["initial"] == 1 and counts["question_supported"] == 0
    assert all(horizon["status_counts"]["unsupported"] == 1 for horizon in counts["horizons"].values())


@pytest.mark.parametrize(("mutate", "reason"), [
    (lambda lines: b"".join(lines) + b"\n", "opening_artifact_trailing_bytes"),
    (lambda lines: b"".join(lines[:-1]), "opening_artifact_framing_or_bound"),
    (lambda lines: b"".join(lines[:-1]) + wire.encode({"kind": "footer", "records": 5}),
     "opening_artifact_footer_differs"),
    (lambda lines: b"".join((lines[0], lines[2], lines[1], *lines[3:])), "stored_opening_record_differs"),
    (lambda lines: _mutate(lines, lambda row: row["initial_paths"][0]["labels"][0].update(path="P2")),
     "stored_opening_record_differs"),
    (lambda lines: _mutate(lines, lambda row: row["targets"][0].update(rank=9)), "stored_opening_record_differs"),
    (lambda lines: b"".join((lines[0], b" " * storage.MAX_LINE + lines[1], *lines[2:])),
     "opening_artifact_framing_or_bound"),
])
def test_resealed_mutations_do_not_verify(tmp_path, mutate, reason):
    original = universe()
    raw = _event_chunk(DAYS[0], A)
    path = tmp_path / storage.FILENAME
    seal = storage.write_opening_support(path, original, InventedReader(raw), lambda _: None)
    changed = mutate(path.read_bytes().splitlines(keepends=True))
    path.write_bytes(changed)
    resealed = replace(seal, sha256=sha256(changed).hexdigest(), size=len(changed))
    with pytest.raises(SourceRefusal, match=reason):
        storage.verify_opening_support(path, resealed, original, InventedReader(raw), lambda _: None)


def test_original_seal_source_identity_and_complete_eof_are_required(tmp_path):
    original = universe()
    path = tmp_path / storage.FILENAME
    seal = storage.write_opening_support(path, original, InventedReader(), lambda _: None)
    with pytest.raises(SourceRefusal, match="opening_artifact_seal_differs"):
        storage.verify_opening_support(path, replace(seal, sha256="0" * 64), original,
                                       InventedReader(), lambda _: None)
    with pytest.raises(SourceRefusal, match="opening_artifact_header_differs"):
        storage.verify_opening_support(path, seal, replace(original, raw_sha256="0" * 64),
                                       InventedReader(), lambda _: None)
    with pytest.raises(SourceRefusal, match="invented_reader_changed"):
        storage.verify_opening_support(path, seal, original, InventedReader(fail_check=2 * seal.records + 1),
                                       lambda _: None)

    lines = 0

    def touch_at_footer(phase):
        nonlocal lines
        if phase == "eh_open_read_record":
            lines += 1
            if lines == seal.records + 2:
                info = path.stat()
                os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))

    with pytest.raises(SourceRefusal, match="opening_artifact_changed_during_read"):
        storage.verify_opening_support(path, seal, original, InventedReader(), touch_at_footer)
