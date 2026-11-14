"""Invented sparse queries on all nine fixed calendar shards."""

from hashlib import sha256

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_event_model_inputs as source
from research.intraday_event_dataset_run import SHARDS
from research.intraday_event_model_workflow import FOLDS, SCOPES
from test_intraday_event_model_inputs import save
from test_intraday_event_model_workflow import CALENDAR, row, target


# Witness both ends of each shard and both sides of every fold cut. Other
# sessions remain in the original calendar even though they have no queries.
QUERY_INDICES = sorted(
    {index for first, end in SHARDS for index in (first, end - 1)}
    | {index for _, start, end in FOLDS for index in (start - 1, start, start + 1, end - 1)}
)


def _save_rows(slot, filename, header, rows):
    return save(slot, filename, [header, *rows, {"kind": "footer", "data_rows": len(rows)}])


@pytest.fixture
def invented_shards(tmp_path):
    calendar_pin = sha256(wire.encode(CALENDAR)).hexdigest()
    queries = {index: row(index, peer=(0, 0, 1) if index % 2 == 0 else None) for index in QUERY_INDICES}
    labels = {index: {**target(query), "symbol": query["symbol"]} for index, query in queries.items()}
    shards = []
    for number, (first, end) in enumerate(SHARDS):
        slot = tmp_path / f"shard-{number}"
        slot.mkdir()
        manifest = {"calendar_sha256": calendar_pin, "session_indices": [first, end], "before_sha256": "a" * 64}
        header = {"kind": "header", **manifest}
        coordinates = [{"kind": "coordinate", "session": day} for day in CALENDAR[first:end]]
        selected = [index for index in QUERY_INDICES if first <= index < end]
        manifest["files"] = {
            "coordinate-support.jsonl": _save_rows(slot, "coordinate-support.jsonl", header, coordinates),
            "queries.jsonl": _save_rows(slot, "queries.jsonl", header, [queries[index] for index in selected]),
            "labels.jsonl": _save_rows(slot, "labels.jsonl", header, [labels[index] for index in selected]),
        }
        shards.append((slot, manifest))
    return tuple(shards), queries, labels


@pytest.mark.parametrize("fold", range(len(FOLDS)))
def test_load_fold_preserves_calendar_and_fences_labels_on_shared_shard(invented_shards, monkeypatch, fold):
    shards, queries, labels = invented_shards
    _, start, end = FOLDS[fold]
    assert any(first <= start - 1 < start < last for first, last in SHARDS)
    training_indices = [index for index in QUERY_INDICES if index < start]
    evaluation_indices = [index for index in QUERY_INDICES if start <= index < end]
    expected_label_bytes = [wire.encode(labels[index]) for index in training_indices]
    permitted = set(expected_label_bytes)
    parsed = []
    original = source.json.loads
    original_query_rows = source.query_rows
    query_scans = []

    def count_query_rows(slot, manifest):
        query_scans.append(tuple(manifest["session_indices"]))
        yield from original_query_rows(slot, manifest)

    def guarded_loads(raw):
        if isinstance(raw, bytes) and b'"kind":"labels"' in raw:
            assert raw in permitted, "A label on or after the fold cutoff reached the loader"
            parsed.append(raw)
        return original(raw)

    loaded = {}
    for scope in SCOPES:
        parsed.clear()
        query_scans.clear()
        with monkeypatch.context() as guard:
            guard.setattr(source.json, "loads", guarded_loads)
            guard.setattr(source, "query_rows", count_query_rows)
            data = source.load_fold(shards, fold, scope)
        assert parsed == expected_label_bytes
        assert query_scans == [bounds for bounds in SHARDS if bounds[0] < end]
        assert data.calendar == CALENDAR and data.fold == fold
        assert not data.support["evaluation_labels_read"]
        for indices, actual in ((training_indices, data.training), (evaluation_indices, data.evaluation)):
            supported = {queries[index]["key"] for index in indices if scope == "full_chart" or index % 2 == 0}
            assert {query.key for query in actual} == supported
        assert {label.key for label in data.targets} == {query.key for query in data.training}
        loaded[scope] = data

    chart, peer = loaded["common_chart"], loaded["common_chart_peer"]
    assert (chart.training, chart.evaluation, chart.targets, chart.support) == (
        peer.training, peer.evaluation, peer.targets, peer.support)
    # Reporting is separate and includes the first evaluation date, which
    # shares a shard with the last training date.
    full = loaded["full_chart"]
    reported = list(source.evaluation_targets(shards, full))
    assert {label.key for label in reported} == {query.key for query in full.evaluation}
    assert reported[0].key == queries[start]["key"]


@pytest.mark.parametrize("repin", [False, True], ids=["tampered-bytes", "pinned-missing-date"])
def test_load_fold_rejects_incomplete_calendar_in_last_shard(invented_shards, repin):
    shards, _, _ = invented_shards
    slot, manifest = shards[-1]
    name = "coordinate-support.jsonl"
    header = {"kind": "header", **{key: value for key, value in manifest.items() if key != "files"}}
    pin = _save_rows(slot, name, header, [])
    if repin:
        manifest["files"][name] = pin
    with pytest.raises(ValueError, match="original calendar dates were lost" if repin else "model input bytes differ"):
        source.load_fold(shards, 0, "full_chart")
