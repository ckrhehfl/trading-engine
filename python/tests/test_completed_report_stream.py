"""Invented-only adversarial coverage of the bounded completed-report reader."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tracemalloc

import pytest

from research import completed_report_stream as report_stream


def _wire(events=None) -> bytes:
    events = [{"id": 1}] if events is None else events
    report = {
        "metadata": {"status": "completed", "unicode": "한글"},
        "variants": {
            "D+1": {"before": [1, None, False], "events": events, "after": "kept"},
            "D+2": {"events": [{"id": 2}], "event_count": 99},
        },
        "tail": {"ok": True},
    }
    return (json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def _read(tmp_path: Path, raw: bytes, callback=None, **options):
    path = tmp_path / "invented.json"
    path.write_bytes(raw)
    return report_stream.read_report(
        path,
        expected_sha256=options.pop("expected_sha256", hashlib.sha256(raw).hexdigest()),
        maximum_bytes=options.pop("maximum_bytes", len(raw)),
        on_event=callback if callback is not None else lambda policy, event: None,
        **options,
    )


@pytest.mark.parametrize("chunk_bytes", [1, 2, 3, 7, 31, 65536])
def test_chunk_boundaries_escapes_unicode_and_metadata(tmp_path, monkeypatch, chunk_bytes):
    monkeypatch.setattr(report_stream, "_CHUNK_BYTES", chunk_bytes)
    event = {"nested": {"text": '한글 😀 \\" } [ { \\ end', "array": [True, {}, []]}}
    raw = _wire([event])
    calls = []
    result = _read(tmp_path, raw, lambda policy, row: calls.append((policy, row)))
    expected = json.loads(raw)
    del expected["variants"]["D+1"]["events"]
    del expected["variants"]["D+2"]["events"]
    assert result == expected
    assert calls == [("D+1", event), ("D+2", {"id": 2})]
    assert result["variants"]["D+2"]["event_count"] == 99


def test_member_order_and_internal_whitespace_are_not_canonicalized(tmp_path):
    raw = b' { "tail": 1, "variants": {"D+2": {"events": []}, "D+1": {"events": []}}, "a": 2}\n'
    assert _read(tmp_path, raw) == {
        "tail": 1,
        "variants": {"D+2": {}, "D+1": {}},
        "a": 2,
    }


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2,"variants":{"D+1":{"events":[]},"D+2":{"events":[]}}}\n',
        b'{"variants":{"D+1":{"events":[]},"D+1":{"events":[]},"D+2":{"events":[]}}}\n',
        b'{"variants":{"D+1":{"events":[],"events":[]},"D+2":{"events":[]}}}\n',
        b'{"metadata":{"a":1,"a":2},"variants":{"D+1":{"events":[]},"D+2":{"events":[]}}}\n',
        b'{"variants":{"D+1":{"events":[{"a":1,"\\u0061":2}]},"D+2":{"events":[]}}}\n',
        b'{"variants":{"D+1":{"events":[{"nested":{"a":1,"a":2}}]},"D+2":{"events":[]}}}\n',
    ],
)
def test_duplicate_keys_at_every_level_are_rejected(tmp_path, raw):
    with pytest.raises(report_stream.ReportReadError):
        _read(tmp_path, raw)


@pytest.mark.parametrize("number", [b"NaN", b"Infinity", b"-Infinity", b"1e999", b"-1e999"])
def test_nonfinite_numbers_are_rejected(tmp_path, number):
    raw = _wire().replace(b'"id":1', b'"id":' + number)
    with pytest.raises(report_stream.ReportReadError):
        _read(tmp_path, raw)


@pytest.mark.parametrize(
    "transform",
    [
        lambda raw: raw[:-1],
        lambda raw: raw[:-3] + b"\n",
        lambda raw: raw + b"{}\n",
        lambda raw: raw + b"\n",
        lambda raw: raw[:-1] + b"\r\n",
        lambda raw: raw[:-1] + b" \n",
        lambda raw: raw.replace(b'"id":1', b'"id":01'),
        lambda raw: raw.replace(b'"id":1', b'"id":1.'),
        lambda raw: raw.replace(b'"id":1', b'"id":truefalse'),
        lambda raw: raw.replace(b'"id":1', b'"id":"\\q"'),
        lambda raw: raw.replace(b'"id":1', b'"id":"line\nbreak"'),
        lambda raw: raw.replace(b'"id":1', b'"id":"\xff"'),
        lambda raw: raw.replace(b'"id":1', b'"id":"unfinished'),
        lambda raw: raw.replace(b'"id":1', b'"id":[1}'),
        lambda raw: raw.replace(b'"id":1', b'"id":1,'),
        lambda raw: raw.replace(b'"events":[{"id":1}]', b'"events":[{"id":1},]'),
    ],
)
def test_malformed_truncated_utf8_and_extra_data_are_rejected(tmp_path, monkeypatch, transform):
    monkeypatch.setattr(report_stream, "_CHUNK_BYTES", 3)
    with pytest.raises(report_stream.ReportReadError):
        _read(tmp_path, transform(_wire()))


@pytest.mark.parametrize(
    "raw",
    [
        b'[]\n',
        b'{}\n',
        b'{"variants":{}}\n',
        b'{"variants":{"D+1":{"events":[]}}}\n',
        b'{"variants":{"D+1":{"events":[]},"D+2":{"events":[]},"D+3":{"events":[]}}}\n',
        b'{"variants":{"D+1":{},"D+2":{"events":[]}}}\n',
        b'{"variants":{"D+1":[],"D+2":{"events":[]}}}\n',
        b'{"variants":{"D+1":{"events":{}},"D+2":{"events":[]}}}\n',
        b'{"variants":{"D+1":{"events":[1]},"D+2":{"events":[]}}}\n',
    ],
)
def test_required_policy_event_shape(tmp_path, raw):
    with pytest.raises(report_stream.ReportReadError):
        _read(tmp_path, raw)


@pytest.mark.parametrize("failure", ["hash", "size", "missing_lf"])
def test_entire_file_is_authenticated_before_any_callback(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(report_stream, "_CHUNK_BYTES", 5)
    raw = _wire([{"id": index} for index in range(20)])
    options = {}
    if failure == "hash":
        options["expected_sha256"] = "0" * 64
    elif failure == "size":
        options["maximum_bytes"] = len(raw) - 1
    else:
        raw = raw[:-1]
    calls = []
    with pytest.raises(report_stream.ReportReadError):
        _read(tmp_path, raw, lambda *args: calls.append(args), **options)
    assert calls == []


def test_checkpoints_cover_all_bounded_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(report_stream, "_CHUNK_BYTES", 17)
    raw = _wire()
    checkpoints = []
    calls = []

    def callback(policy, row):
        assert len(checkpoints) >= (len(raw) + 16) // 17 + 1
        calls.append(policy)

    _read(tmp_path, raw, callback, checkpoint=lambda: checkpoints.append(1))
    assert calls == ["D+1", "D+2"]
    assert len(checkpoints) >= 3 * ((len(raw) + 16) // 17 + 1)


def test_checkpoint_failure_propagates_before_callback(tmp_path):
    def checkpoint():
        raise TimeoutError("invented budget")

    calls = []
    with pytest.raises(TimeoutError, match="invented budget"):
        _read(tmp_path, _wire(), lambda *args: calls.append(args), checkpoint=checkpoint)
    assert calls == []


@pytest.mark.parametrize("location", ["event", "metadata", "key"])
def test_individual_value_byte_limit(tmp_path, location):
    raw = _wire([{"id": "x" * 100}])
    if location == "metadata":
        raw = _wire().replace(b'"status":"completed"', b'"status":"' + b"x" * 100 + b'"')
    elif location == "key":
        raw = _wire().replace(b'"metadata":', b'"' + b"x" * 100 + b'":')
    with pytest.raises(report_stream.ReportReadError, match="maximum_value_bytes"):
        _read(tmp_path, raw, maximum_value_bytes=80)


def test_exact_value_bound_is_in_bytes_and_inclusive(tmp_path):
    raw = b'{"variants":{"D+1":{"events":[{"x":"' + "한".encode() * 10 + b'"}]},"D+2":{"events":[]}}}\n'
    value_bytes = len(b'{"x":"' + "한".encode() * 10 + b'"}')
    _read(tmp_path, raw, maximum_value_bytes=value_bytes)
    with pytest.raises(report_stream.ReportReadError, match="maximum_value_bytes"):
        _read(tmp_path, raw, maximum_value_bytes=value_bytes - 1)


def test_container_depth_includes_streamed_envelope(tmp_path):
    raw = _wire().replace(b'"id":1', b'"id":' + b"[" * 59 + b"0" + b"]" * 59)
    _read(tmp_path, raw)
    raw = _wire().replace(b'"id":1', b'"id":' + b"[" * 60 + b"0" + b"]" * 60)
    with pytest.raises(report_stream.ReportReadError, match="nesting"):
        _read(tmp_path, raw)


def test_mutation_in_callback_is_rejected_even_when_parse_bytes_were_prefetched(tmp_path):
    path = tmp_path / "invented.json"
    raw = _wire()
    path.write_bytes(raw)
    mutated = False

    def callback(policy, row):
        nonlocal mutated
        if not mutated:
            path.write_bytes(raw.replace(b"completed", b"corrupted"))
            mutated = True

    with pytest.raises(report_stream.ReportReadError, match="changed|SHA256"):
        report_stream.read_report(
            path,
            expected_sha256=hashlib.sha256(raw).hexdigest(),
            maximum_bytes=len(raw),
            on_event=callback,
        )
    assert mutated


def test_replacement_with_identical_bytes_is_rejected(tmp_path):
    path = tmp_path / "invented.json"
    replacement = tmp_path / "replacement.json"
    raw = _wire()
    path.write_bytes(raw)
    replacement.write_bytes(raw)
    replaced = False

    def callback(policy, row):
        nonlocal replaced
        if not replaced:
            replacement.replace(path)
            replaced = True

    with pytest.raises(report_stream.ReportReadError, match="changed"):
        report_stream.read_report(
            path,
            expected_sha256=hashlib.sha256(raw).hexdigest(),
            maximum_bytes=len(raw),
            on_event=callback,
        )


def test_final_authentication_rejects_a_late_byte_change_after_metadata_check(tmp_path, monkeypatch):
    # Change bytes at final-authentication's first checkpoint, after parse and
    # its identity comparison. A fresh byte hash is required to refuse this.
    path = tmp_path / "invented.json"
    raw = _wire()
    monkeypatch.setattr(report_stream, "_CHUNK_BYTES", len(raw) + 1)
    path.write_bytes(raw)
    checkpoints = 0
    changed = False

    def checkpoint():
        nonlocal changed, checkpoints
        checkpoints += 1
        if checkpoints == 5:
            # Each full pass has one data read and one EOF read. This is the
            # final pass's first read, after both parse identity comparisons.
            path.write_bytes(raw.replace(b"completed", b"corrupted"))
            changed = True

    with pytest.raises(report_stream.ReportReadError, match="SHA256 mismatch"):
        report_stream.read_report(
            path,
            expected_sha256=hashlib.sha256(raw).hexdigest(),
            maximum_bytes=len(raw),
            on_event=lambda policy, row: None,
            checkpoint=checkpoint,
        )
    assert changed


def test_callback_exceptions_propagate(tmp_path):
    def callback(policy, row):
        raise RuntimeError("invented callback failure")

    with pytest.raises(RuntimeError, match="invented callback failure"):
        _read(tmp_path, _wire(), callback)


@pytest.mark.parametrize(
    "options,exception",
    [
        ({"expected_sha256": "wrong"}, ValueError),
        ({"maximum_bytes": 0}, ValueError),
        ({"maximum_bytes": True}, ValueError),
        ({"maximum_value_bytes": -1}, ValueError),
        ({"maximum_value_bytes": 1.0}, ValueError),
        ({"checkpoint": 1}, TypeError),
    ],
)
def test_invalid_reader_configuration(tmp_path, options, exception):
    with pytest.raises(exception):
        _read(tmp_path, _wire(), **options)


def test_reader_memory_does_not_grow_with_event_count(tmp_path, monkeypatch):
    monkeypatch.setattr(report_stream, "_CHUNK_BYTES", 4096)

    def measure(count):
        raw = _wire([{"id": index, "text": "x" * 30} for index in range(count)])
        path = tmp_path / f"invented-{count}.json"
        path.write_bytes(raw)
        expected_sha256 = hashlib.sha256(raw).hexdigest()
        seen = 0

        def callback(policy, row):
            nonlocal seen
            seen += 1

        tracemalloc.start()
        try:
            result = report_stream.read_report(
                path,
                expected_sha256=expected_sha256,
                maximum_bytes=len(raw),
                on_event=callback,
            )
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        assert seen == count + 1
        assert "events" not in result["variants"]["D+1"]
        return peak

    small = measure(20)
    large = measure(20000)
    assert large < small + 256 * 1024
