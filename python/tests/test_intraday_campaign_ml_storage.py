"""Invented storage boundary checks; no private fixture or research input reads."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from fractions import Fraction
import hashlib
import json
from types import MappingProxyType

import pytest

from research.intraday_campaign_ml_storage import store_json


def test_exact_nested_encoding_and_exclusive_read_only_store(tmp_path):
    @dataclass(frozen=True)
    class Record:
        amount: Fraction
        when: date

    stamp = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    value = MappingProxyType({"한국": (Record(Fraction(-7, 3), date(2026, 1, 2)), stamp)})
    expected = {"한국": [{"amount": {"numerator": "-7", "denominator": "3"},
                           "when": "2026-01-02"}, stamp.isoformat()]}
    raw = (json.dumps(expected, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False) + "\n").encode()
    path, stages = tmp_path / "invented.json", []
    assert store_json(path, value, len(raw), stages.append) == (hashlib.sha256(raw).hexdigest(), len(raw))
    assert path.read_bytes() == raw
    assert path.stat().st_mode & 0o777 == 0o400
    assert "exact_output_store" in stages
    with pytest.raises(FileExistsError):
        store_json(path, {"overwrite": True}, 1000, stages.append)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object()])
def test_nonfinite_and_unsupported_values_are_refused(tmp_path, value):
    path = tmp_path / "partial.json"
    with pytest.raises((TypeError, ValueError)):
        store_json(path, value, 1000, lambda stage: None)
    assert path.exists()  # Failure evidence is preserved, never silently retried.


def test_size_and_checkpoint_failures_preserve_exclusive_partial_artifacts(tmp_path):
    path = tmp_path / "too-large.json"
    with pytest.raises(ValueError, match="size exceeded"):
        store_json(path, {"value": "abc"}, 1, lambda stage: None)
    assert path.exists()

    def refuse(stage):
        raise RuntimeError("invented-budget-refusal")

    other = tmp_path / "interrupted.json"
    with pytest.raises(RuntimeError, match="invented-budget-refusal"):
        store_json(other, {"value": "abc"}, 1000, refuse)
    assert other.exists()
