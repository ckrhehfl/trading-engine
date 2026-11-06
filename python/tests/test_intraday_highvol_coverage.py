"""Invented coverage artifact framing, original denominators and weekly counts."""
from dataclasses import replace
from datetime import datetime
from fractions import Fraction
import gzip
import hashlib
import json

import pytest

from research import intraday_highvol_coverage as storage
from research.intraday_highvol_population import iter_coverage_records
from test_intraday_highvol_population import Reader, SYMBOL, chunk, days, records, universe


def noop(_):
    return None


def write(tmp_path, original=None, reader=None):
    original = universe() if original is None else original
    reader = Reader() if reader is None else reader
    path = tmp_path / storage.FILENAME
    seal, report = storage.write_coverage(path, original, {"test": "invented"},
        iter_coverage_records(original, reader, noop), noop)
    return path, seal, report


def reseal(path, data, records_count=1):
    compressed = gzip.compress(data, mtime=0)
    path.write_bytes(compressed)
    return storage.CoverageSeal(hashlib.sha256(compressed).hexdigest(), len(compressed),
        hashlib.sha256(data).hexdigest(), len(data), records_count)


def test_lossless_artifact_keeps_opaque_metadata_and_unknowns(tmp_path):
    original = universe()
    expected = records(original, Reader())
    path, seal, report = write(tmp_path, original)
    with storage.open_coverage(path, seal, noop) as saved:
        assert saved.header == {"kind": "header", "schema": storage.SCHEMA,
            "universe": {"sessions": list(original.sessions), "raw_sha256": original.raw_sha256,
                         "target_count": 2}, "binding": {"test": "invented"}}
        observed = tuple(saved.records())
        assert observed == tuple(storage.project(row) for row in expected)
        assert observed[0]["targets"][0]["metadata"] == {"bytes_hex": original.targets[0].metadata.hex()}
        assert saved.footer == {"kind": "footer", "records": 1, "report": report}
    assert report["union_records"] == 1 and seal.records == 1
    for scenario in ("S1", "S2"):
        arm = report["scenarios"][scenario]
        assert arm["target_days"] == 1 and arm["source_absent_price_days"] == 1
        assert arm["minute_segments"]["first_hour"]["expected_minutes"] == 60
        assert arm["window_segments"]["first_hour"]["expected_windows"] == 10
        assert arm["window_segments"]["later"]["expected_windows"] == 48
        assert arm["episodes"]["unavailable_windows"] == 58
        assert arm["episodes"]["days_exact_onset_unknown"] == 1
        assert arm["weekly"][0]["observed_clusters"] == 0
        assert arm["confirmed_onsets_weeks_ge15"] == 0


def test_artifacts_are_deterministic_and_exclusive(tmp_path):
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    path1, seal1, _ = write(one)
    path2, seal2, _ = write(two)
    assert seal1 == seal2 and path1.read_bytes() == path2.read_bytes()
    with pytest.raises(FileExistsError):
        write(one)


def test_zero_volume_is_not_missing_and_zero_trade_weeks_are_preserved(tmp_path):
    calendar = days(6)
    original = universe(calendar)
    values = {(kind, SYMBOL, day): chunk(kind, day, count=60, volume="0")
              for day in calendar for kind in ("CC", "CX")}
    _, _, report = write(tmp_path, original, Reader(values))
    arm = report["scenarios"]["S1"]
    assert len(arm["weekly"]) == 2 and [week["calendar_sessions"] for week in arm["weekly"]] == [5, 1]
    assert all(week["observed_clusters"] == 0 for week in arm["weekly"])
    assert arm["minute_segments"]["first_hour"]["zero_volume_minutes"] == 360
    assert arm["window_segments"]["first_hour"]["zero_volume_windows"] == 60
    assert arm["window_segments"]["first_hour"]["decision_reasons"] == {"insufficient_original_history": 60}


def test_high_clusters_are_not_repeated_windows_or_confirmed_onsets(tmp_path):
    calendar = days(21)
    values = {("CC", SYMBOL, day): chunk("CC", day, count=60, slope=10 if index == 20 else 1)
              for index, day in enumerate(calendar)}
    _, _, report = write(tmp_path, universe(calendar), Reader(values))
    arm = report["scenarios"]["S1"]
    assert arm["window_segments"]["first_hour"]["decision_statuses"]["high"] == 10
    assert arm["episodes"]["observed_clusters"] == 1
    assert arm["episodes"]["left_censored_clusters"] == 1
    assert arm["episodes"]["confirmed_onsets"] == 0
    assert arm["first_hour_episodes"]["observed_clusters"] == 1
    assert arm["episodes"]["right_censored_clusters"] == 1


@pytest.mark.parametrize("mutation", ["extra_record", "missing_record", "changed_target"])
def test_original_record_population_is_not_silently_changed(tmp_path, mutation):
    original = universe()
    row, = records(original, Reader())
    rows = {"extra_record": (row, row), "missing_record": (),
            "changed_target": (replace(row, targets=(replace(row.targets[0], rank=2), row.targets[1])),)}[mutation]
    with pytest.raises(ValueError, match="EG original|EG coverage population"):
        storage.write_coverage(tmp_path / "partial.gz", original, {}, rows, noop)
    assert (tmp_path / "partial.gz").exists()


def test_early_reader_exit_is_not_full_verification(tmp_path):
    path, seal, _ = write(tmp_path)
    with pytest.raises(ValueError, match="not consumed through EOF"):
        with storage.open_coverage(path, seal, noop) as saved:
            next(saved.records())


def test_changed_compressed_bytes_fail_before_records(tmp_path):
    path, seal, _ = write(tmp_path)
    path.write_bytes(path.read_bytes() + b"unexpected")
    with pytest.raises(ValueError, match="compressed artifact seal"):
        with storage.open_coverage(path, seal, noop):
            pass


def test_resealed_truncated_stream_still_refuses_missing_footer(tmp_path):
    path, _, _ = write(tmp_path)
    lines = gzip.decompress(path.read_bytes()).splitlines(keepends=True)
    seal = reseal(path, b"".join(lines[:-1]))
    with pytest.raises(ValueError, match="truncated"):
        with storage.open_coverage(path, seal, noop) as saved:
            tuple(saved.records())


def test_resealed_footer_count_and_trailing_content_are_rejected(tmp_path):
    path, _, _ = write(tmp_path)
    data = gzip.decompress(path.read_bytes())
    lines = data.splitlines(keepends=True)
    footer = json.loads(lines[-1])
    footer["records"] = 2
    lines[-1] = storage.wire.encode(footer)
    seal = reseal(path, b"".join(lines))
    with pytest.raises(ValueError, match="footer count"):
        with storage.open_coverage(path, seal, noop) as saved:
            tuple(saved.records())
    seal = reseal(path, data + b"\n")
    with pytest.raises(ValueError, match="EOF or decoded seal"):
        with storage.open_coverage(path, seal, noop) as saved:
            tuple(saved.records())


def test_duplicate_keys_rejected_even_with_a_matching_digest(tmp_path):
    path, _, _ = write(tmp_path)
    data = gzip.decompress(path.read_bytes())
    corrupted = data.replace(b'"kind":"record"', b'"kind":"record","kind":"record"', 1)
    seal = reseal(path, corrupted)
    with pytest.raises(ValueError, match="duplicate EG artifact key"):
        with storage.open_coverage(path, seal, noop) as saved:
            tuple(saved.records())


def test_exact_numbers_never_use_floating_point_and_naive_clocks_refuse():
    value = Fraction(2**70 + 1, 3)
    assert storage.project(value) == {"fraction": [value.numerator, value.denominator]}
    with pytest.raises(ValueError, match="scalar"):
        storage.project(0.1)
    with pytest.raises(ValueError, match="aware clock"):
        storage.project(datetime(2026, 6, 1))
