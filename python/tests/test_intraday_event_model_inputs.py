"""Invented files only: whole pins and target parsing boundaries."""
from hashlib import sha256
import json
from pathlib import Path

import pytest

from research import intraday_event_model_inputs as source
from research import intraday_campaign_wsl_transport as wire


def save(slot, name, rows):
    raw = b"".join(wire.encode(row) for row in rows)
    (slot / name).write_bytes(raw)
    return {"filename": name, "bytes": len(raw), "rows": len(rows), "sha256": sha256(raw).hexdigest()}


def fixture(slot):
    manifest = {"calendar_sha256": "a" * 64, "before_sha256": "b" * 64, "session_indices": [0, 32]}
    headers = {"kind": "header", **manifest}
    queries = [{"kind": "query", "key": f"key{i}", "session": day, "symbol": "KRX:000001"}
               for i, day in enumerate(("2026-01-01", "2026-01-02"))]
    labels = [{**row, "kind": "labels", "test_payload": "FIRST" if i == 0 else "EVALUATION_SECRET"}
              for i, row in enumerate(queries)]
    footer = {"kind": "footer", "data_rows": 2}
    manifest["files"] = {"queries.jsonl": save(slot, "queries.jsonl", [headers, *queries, footer]),
                         "labels.jsonl": save(slot, "labels.jsonl", [headers, *labels, footer])}
    return manifest


def test_test_targets_are_hashed_but_never_decoded_at_training_boundary(tmp_path, monkeypatch):
    manifest = fixture(tmp_path)
    original = source.json.loads
    def no_future(raw):
        assert b"EVALUATION_SECRET" not in raw
        return original(raw)
    monkeypatch.setattr(source.json, "loads", no_future)
    rows = list(source.training_labels(tmp_path, manifest, "2026-01-02"))
    assert len(rows) == 1 and rows[0]["test_payload"] == "FIRST"


def test_all_training_reads_footer_and_exact_order(tmp_path):
    manifest = fixture(tmp_path)
    assert len(list(source.training_labels(tmp_path, manifest, "2026-01-03"))) == 2
    assert len(list(source.query_rows(tmp_path, manifest))) == 2
    labels = [json.loads(raw) for raw in (tmp_path / "labels.jsonl").read_bytes().splitlines()]
    labels[1], labels[2] = labels[2], labels[1]
    manifest["files"]["labels.jsonl"] = save(tmp_path, "labels.jsonl", labels)
    with pytest.raises(ValueError, match="target prefix"):
        list(source.training_labels(tmp_path, manifest, "2026-01-02"))


def test_tampered_future_bytes_fail_even_without_decoding_their_targets(tmp_path):
    manifest = fixture(tmp_path)
    path = tmp_path / "labels.jsonl"
    path.write_bytes(path.read_bytes().replace(b"EVALUATION_SECRET", b"EVALUATION_CHANGE"))
    with pytest.raises(ValueError, match="bytes differ"):
        list(source.training_labels(tmp_path, manifest, "2026-01-02"))


def test_unknown_extra_rows_or_lost_footer_are_rejected(tmp_path):
    manifest = fixture(tmp_path)
    rows = [json.loads(raw) for raw in (tmp_path / "queries.jsonl").read_bytes().splitlines()]
    rows[-1]["data_rows"] = 3
    manifest["files"]["queries.jsonl"] = save(tmp_path, "queries.jsonl", rows)
    with pytest.raises(ValueError, match="count/EOF"):
        list(source.query_rows(tmp_path, manifest))


def test_no_training_date_never_decodes_first_target(tmp_path, monkeypatch):
    manifest = fixture(tmp_path)
    original = source.json.loads
    def no_targets(raw):
        assert b'"test_payload"' not in raw
        return original(raw)
    monkeypatch.setattr(source.json, "loads", no_targets)
    assert list(source.training_labels(tmp_path, manifest, "2026-01-01")) == []


def test_dataset_admission_requires_every_audited_shard_and_binds_their_order(monkeypatch):
    qualification = "f" * 64
    pins = tuple(f"{shard:064x}" for shard in range(9))
    seen = []

    def audit_shard(prior, shard):
        assert prior["qualification_sha256"] == qualification and not prior["invented"]
        seen.append(shard)
        return {"identity": {"attempt": f"actual1-s{shard:02d}"}, "dataset": {}}, pins[shard]

    monkeypatch.setattr(source.dataset_run, "audited", lambda prior: ({}, qualification))
    monkeypatch.setattr(source.dataset_run, "audited_shard", audit_shard)
    monkeypatch.setattr(source.dataset_run, "_slot", Path)
    with pytest.raises(ValueError, match="all nine"):
        source.admit_dataset({}, qualification, pins[:-1])
    assert not seen
    shards, binding = source.admit_dataset({}, qualification, pins)
    assert seen == list(range(9)) and tuple(s[2] for s in shards) == pins
    assert wire.valid_sha(binding)
    with pytest.raises(ValueError, match="audit pin differs"):
        source.admit_dataset({}, qualification, pins[::-1])
