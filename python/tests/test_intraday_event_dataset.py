"""Invented raw chunks only; bounded dataset stages and independent readback."""

from dataclasses import replace
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path

import pytest

from research import intraday_ed_sources as source
from research import intraday_event_dataset as dataset
from research import intraday_event_features as features
from research import intraday_event_held as held
from research import intraday_event_inputs as inputs
from research import intraday_event_labels as labels
from research import intraday_event_scenarios as scenarios
from research.intraday_event_fixtures import raw_chunk
from research.intraday_event_models import Query, TrainingLabels, validate_training, support_seal


DAY = "2026-01-05"


class Reader:
    def __init__(self, values):
        self.values, self.loaded, self.checks = values, [], 0

    def load_raw_chunk(self, kind, symbol, day):
        self.loaded.append((kind, symbol, day))
        return self.values[kind, symbol, day]

    def assert_unchanged(self):
        self.checks += 1


def invented(cases=(0, 1, 2), *, days=(DAY,), minutes=100, only_s1=()):
    targets, values = [], {}
    for day in days:
        for rank, case in enumerate(cases, 1):
            symbol = f"KRX:{rank:06}"
            for kind in ("CC", "CX"):
                values[kind, symbol, day] = raw_chunk(kind, day, symbol, minutes, case)
            for scenario in ("S1",) if rank in only_s1 else ("S1", "S2"):
                targets.append(source.TargetCoordinate(scenario, symbol, day, rank, minutes,
                    "stored_rows_completeness_unverified", b'{"original":true}'))
    return source.UniverseCalendar(days, tuple(targets), "a" * 64), Reader(values)


def rows(slot, name, kind=None):
    values = [json.loads(line) for line in (slot / name).read_bytes().splitlines()]
    return values if kind is None else [row for row in values if row["kind"] == kind]


@pytest.fixture(scope="module")
def saved(tmp_path_factory):
    slot = tmp_path_factory.mktemp("el-dataset-invented")
    universe, reader = invented(tuple(range(12)), only_s1=(12,))
    result = dataset.build_dataset(slot, universe, reader)
    return slot, universe, reader, result


def test_registered_contract_and_file_manifest(saved):
    slot, _, _, result = saved
    spec = json.loads((Path(__file__).parents[2] / "configs/research/discovery/intraday-event-dataset-v1.json").read_text())
    assert dataset.CONTRACT == spec["scientific_contract"]
    assert set(result["files"]) == set(dataset.FILES)
    assert result["manifest_file"]["filename"] == "dataset-manifest.json"
    assert result["stage_order"] == ["causal", "case_roster", "before_cards", "outcomes", "after_cards"]
    for name, pin in result["files"].items():
        assert pin["bytes"] == (slot / name).stat().st_size <= dataset.MAX_FILE
        assert pin["rows"] == len((slot / name).read_bytes().splitlines())
        assert all(len(line) + 1 <= dataset.MAX_ROW for line in (slot / name).read_bytes().splitlines())


def test_original_coordinates_full_denominators_and_fixed_peer_population(saved):
    slot, _, reader, result = saved
    coordinates = rows(slot, "coordinate-support.jsonl", "coordinate")
    assert result["counts"]["coordinates"] == 12
    assert result["counts"]["scenario_coordinates"] == {"S1": 12, "S2": 11}
    assert result["counts"]["regular_minutes"] == 12 * 390
    assert result["counts"]["continuous_minutes"] == 12 * 380
    assert len(reader.loaded) == 12 * 2 * 2  # Two bounded passes, not a whole-window cache.
    assert all(len(row["minute_states"]) == 390 for row in coordinates)
    assert all(row["auction_interval"] == [380, 390] for row in coordinates)
    assert all(not row["auction_absence_is_continuous_gap"] for row in coordinates)
    assert all(len(row["day_peer_population"]) == 11 for row in coordinates)
    assert all(row["symbol"] not in row["peer_roster"] and "KRX:000012" not in row["peer_roster"] for row in coordinates)
    assert result["counts"]["series_clusters"] == 12
    assert result["counts"]["original_episodes"] == sum(result["counts"]["events_by_family"].values())
    support = rows(slot, "event-support.jsonl", "support_runs")
    assert len(support) == 3 * 12
    for row in support:
        assert row["scheduled_minutes"] == sum(row["counts"].values()) == 380
        assert row["runs"][0]["start"] == 0 and row["runs"][-1]["end"] == 380
        assert all(left["end"] == right["start"] for left, right in zip(row["runs"], row["runs"][1:]))
    queries = rows(slot, "queries.jsonl", "query")
    assert all(row["query"] is not None for row in queries)
    assert all(row["symbol"] not in row["peer_support"]["members"] for row in queries)
    assert any(row["symbol"] == "KRX:000012" for row in queries) is False  # Invalid impulse remains supported denominator.
    assert all(result["counts"]["events_by_family"][family] > 0 for family in scenarios.FAMILIES)


def test_future_free_held_queries_deduplicate_reference_books(saved):
    slot, _, _, result = saved
    queries = {row["key"]: row for row in rows(slot, "queries.jsonl", "query")}
    checkpoints = rows(slot, "held-checkpoints.jsonl", "held_checkpoint")
    free = [row for row in checkpoints if row["checkpoint"]["status"] == "free_alive"]
    assert free and result["counts"]["held_queries"] > 0
    assert len(queries) == result["counts"]["entry_queries"] + result["counts"]["held_queries"]
    paired = [row for row in free if row["checkpoint"]["entry_policy"] == "reference_limit_one_shot"]
    assert paired
    for row in free:
        query = queries[row["learning_query_key"]]
        assert query["query_kind"] == "held"
        assert query["origin"]["entry_policy"] == "next_open_capped"
        assert query["origin"]["original_event_id"] == row["checkpoint"]["original_event_id"]
        assert query["query"]["episode_id"] == query["frame"]["event_id"]
        assert query["query"]["episode_id"] != query["origin"]["original_event_id"]
        assert query["query"]["position"] == row["checkpoint"]["position"]
        assert query["frame"]["decision_offset"] == row["checkpoint"]["checkpoint_offset"]
        assert row["checkpoint"]["expiry_offset"] < query["continuous_length"]
    assert result["counts"]["counterfactual_books"] % 2 == 0
    assert len(checkpoints) == 2 * result["counts"]["counterfactual_books"]
    targets = {row["key"]: row for row in rows(slot, "labels.jsonl", "labels")}
    for key, query in queries.items():
        if query["query_kind"] == "held":
            assert all(targets[key]["training_labels"][head] is None for head in
                       ("path_class", "fill", "net_immediate", "net_wait3"))
    assert any(row["targets"]["status"] == "known" for row in targets.values() if row["query_kind"] == "held")


def test_no_entry_raw_zero_and_unknown_are_not_no_fill_training_zero(saved):
    slot, _, _, _ = saved
    entry = [row for row in rows(slot, "labels.jsonl", "labels") if row["query_kind"] == "entry"]
    refused = [row for row in entry if row["targets"]["entry_admissibility"] == "not_admissible"]
    assert refused
    assert any(row["targets"]["immediate"]["value"] == "0" for row in refused)
    assert all(row["training_labels"][head] is None for row in refused
               for head in ("fill", "net_immediate", "net_wait3"))
    no_fill = [row for row in entry if row["targets"]["immediate"]["status"] == "no_fill"]
    assert no_fill and all(row["training_labels"]["net_immediate"] == "0" for row in no_fill)
    unknown = [row for row in entry if row["targets"]["immediate"]["status"] == "unknown"]
    assert unknown and all(row["training_labels"]["net_immediate"] is None for row in unknown)
    assert any(row["targets"]["path"]["status"] == "unknown_intrabar" for row in entry)


def test_query_and_label_streams_admit_shared_model_contract_without_backend(saved):
    slot, _, _, _ = saved
    queries = [Query(**row["query"]) for row in rows(slot, "queries.jsonl", "query") if row["query"]]
    targets = [TrainingLabels(**row["training_labels"]) for row in rows(slot, "labels.jsonl", "labels")
               if row["training_labels"]]
    admitted, by_key = validate_training(queries, targets, "2026-02-01", mode="chart", support_binding=support_seal(queries))
    assert len(admitted) == len(queries) == len(by_key)


def test_audit_reconstructs_without_producer_oracles_and_writes_nothing(saved, monkeypatch):
    slot, universe, reader, expected = saved
    before = {path.name: path.stat().st_mtime_ns for path in slot.iterdir()}
    def forbidden(*args, **kwargs):
        raise AssertionError("Producer scientific oracle reached during independent audit")
    for module, names in (
        (source, ("session_inputs",)), (inputs, ("adapt_session", "prefix_card", "case_roster")),
        (scenarios, ("scan_scenarios",)), (features, ("chart_features", "peer_features", "coalesce_frames")),
        (labels, ("prepare_query", "build_targets")), (held, ("held_checkpoint", "remaining_gross_target")),
        (dataset, ("_select_case", "_entry_request")),
    ):
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    assert dataset.build_dataset(slot, universe, reader, audit=True) == expected
    assert {path.name: path.stat().st_mtime_ns for path in slot.iterdir()} == before


def test_seals_exist_before_first_target_evaluation(tmp_path, monkeypatch):
    universe, reader = invented((0,))
    original = labels.build_targets
    calls = []
    def checked(*args, **kwargs):
        assert (tmp_path / "case-roster.json").is_file()
        for name in (*dataset.CAUSAL_FILES, "before-cards.jsonl"):
            assert rows(tmp_path, name)[-1]["kind"] == "footer"
        assert not (tmp_path / "after-cards.jsonl").exists()
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(labels, "build_targets", checked)
    dataset.build_dataset(tmp_path, universe, reader)
    assert calls


def test_missing_closing_call_does_not_become_continuous_data_gap(tmp_path):
    universe, reader = invented((9,), minutes=380)
    dataset.build_dataset(tmp_path, universe, reader)
    coordinate = rows(tmp_path, "coordinate-support.jsonl", "coordinate")[0]
    assert coordinate["continuous_price_observed"] == 380
    assert all(row["price"] == "missing" for row in coordinate["minute_states"][380:])
    assert all("missing_minute" not in row["counts"] for row in rows(tmp_path, "event-support.jsonl", "support_runs"))


def test_shards_preserve_original_calendar_and_never_load_other_days(tmp_path):
    universe, reader = invented((9,), days=(DAY, "2026-01-06"))
    result = dataset.build_dataset(tmp_path, universe, reader, session_indices=(1, 2))
    assert result["session_indices"] == [1, 2] and result["counts"]["coordinates"] == 1
    assert all(day == "2026-01-06" for _, _, day in reader.loaded)
    assert result == dataset.build_dataset(tmp_path, universe, reader, audit=True, session_indices=(1, 2))


@pytest.mark.parametrize("mutation", ("append", "change", "truncate"))
def test_full_eof_audit_rejects_tampered_saved_stream(tmp_path, mutation):
    universe, reader = invented((9,))
    dataset.build_dataset(tmp_path, universe, reader)
    path = tmp_path / "event-support.jsonl"
    raw = path.read_bytes()
    changed = raw + b"{}\n" if mutation == "append" else raw[:-5] if mutation == "truncate" else raw.replace(b'"scheduled_minutes":380', b'"scheduled_minutes":379', 1)
    path.write_bytes(changed)
    with pytest.raises(ValueError, match="differs"):
        dataset.build_dataset(tmp_path, universe, reader, audit=True)


def test_exclusive_outputs_and_duplicate_original_coordinate_rejected(tmp_path):
    universe, reader = invented((9,))
    with pytest.raises(ValueError, match="duplicate"):
        dataset.build_dataset(tmp_path, replace(universe, targets=universe.targets * 2), reader)
    assert reader.loaded == []
    dataset.build_dataset(tmp_path, universe, reader)
    with pytest.raises(FileExistsError):
        dataset.build_dataset(tmp_path, universe, reader)


def test_exact_serialization_and_row_bound_fail_closed(tmp_path, monkeypatch):
    assert dataset._plain({"value": Fraction(1, 3)}) == {"value": "1/3"}
    with pytest.raises(ValueError, match="exact"):
        dataset._plain({"value": 0.1})
    monkeypatch.setattr(dataset, "MAX_ROW", 10)
    stream = dataset._Stream(tmp_path / "bounded", False)
    try:
        with pytest.raises(ValueError, match="bound"):
            stream.append({"too_long": "x" * 20})
    finally:
        stream.close()


def test_changed_future_does_not_change_past_queries_or_example_selection(tmp_path):
    universe, reader = invented((1,))
    alternative = Reader(deepcopy(reader.values))
    for row in alternative.values["CC", "KRX:000001", DAY]["rows"][60:]:
        for field in ("open", "high", "low", "close"):
            row[field] = str(int(row[field]) + 100)
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    dataset.build_dataset(first, universe, reader)
    dataset.build_dataset(second, universe, alternative)
    prefix = lambda path: [row for row in rows(path, "queries.jsonl", "query") if row["frame"]["decision_offset"] < 60]
    assert prefix(first) and prefix(first) == prefix(second)
    assert rows(first, "case-roster.json")[0]["examples"] == rows(second, "case-roster.json")[0]["examples"]
    assert rows(first, "labels.jsonl", "labels") != rows(second, "labels.jsonl", "labels")


def test_delayed_onset_card_uses_coalesced_terminal_state(tmp_path, monkeypatch):
    universe, reader = invented((10,))
    original = dataset._day
    def delayed(*args, **kwargs):
        decoded = original(*args, **kwargs)
        for regular, continuous, _ in decoded.values():
            regular.bars[20] = replace(regular.bars[20], available_offset=50)
            continuous.bars[20] = replace(continuous.bars[20], available_offset=50)
            regular.states[20]["available_offset"] = 50
            continuous.states[20]["available_offset"] = 50
        return decoded
    monkeypatch.setattr(dataset, "_day", delayed)
    dataset.build_dataset(tmp_path, universe, reader)
    cards = rows(tmp_path, "before-cards.jsonl", "before_card")
    chosen = next(row for row in cards if row["selected_onset"]["family"] == "A_DOWNFAIL")
    assert chosen["coalesced_onset_warning"]
    assert chosen["card"]["frame"]["state"] == "INVALIDATED"
    assert chosen["card"]["frame"]["decision_offset"] == 50
    query = next(row for row in rows(tmp_path, "queries.jsonl", "query") if row["key"] == chosen["decision_query_key"])
    assert query["query"]["state"] == "INVALIDATED"
    target = next(row for row in rows(tmp_path, "labels.jsonl", "labels") if row["key"] == query["key"])
    assert target["targets"]["entry_admissibility"] == "not_admissible"
    assert target["training_labels"]["net_immediate"] is None


def test_fixed_hash_minima_compose_across_shards():
    values = [(f"{i:064x}", ("A_DOWNFAIL", str(i))) for i in (99, 3, 60, 1, 20, 2, 17)]
    direct, left, right, composed = [], [], [], []
    for rank in values:
        dataset._select_case(direct, rank, lambda rank=rank: rank)
    for selected, chunk in ((left, values[:3]), (right, values[3:])):
        for rank in chunk:
            dataset._reference_select_case(selected, rank, lambda rank=rank: rank)
    for rank, value in left + right:
        dataset._select_case(composed, rank, lambda value=value: value)
    assert direct == composed and [int(rank[0], 16) for rank, _ in direct] == [1, 2, 3]
