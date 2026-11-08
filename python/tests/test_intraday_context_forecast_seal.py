"""Invented sealed forecasts; no actual inputs or evaluation acquisition."""

from dataclasses import replace
from hashlib import sha256
import json

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_forecast_seal as store
from research.intraday_context_forecast_fold import forecast_fold
from research.intraday_context_forecast_report import score_fold
from research.intraday_context_model_inputs import encode
from research.intraday_context_snapshot import FEATURE_SCHEMA, LABEL_SCHEMA, project_coordinate
from research.intraday_scenario_snapshot import SnapshotSeal
from test_intraday_context_forecast_fold import adapters, fixture, label_reader


FEATURES = SnapshotSeal(FEATURE_SCHEMA, "a" * 64, 1024, 3)
LABELS = SnapshotSeal(LABEL_SCHEMA, "b" * 64, 2048, 3)


@pytest.fixture(scope="module")
def invented():
    targets, coordinates, sessions, fold = fixture()
    forecast = forecast_fold(coordinates, fold, "S1", label_reader(sessions, coordinates, []),
                             expected_targets=targets, adapters=adapters([]))
    return targets, coordinates, sessions, fold, forecast


def saved(tmp_path, invented, forecast=None):
    path = tmp_path / "forecasts.jsonl"
    forecast = forecast or invented[-1]
    seal = store.seal_forecasts(path, forecast, FEATURES, LABELS)
    return path, seal


def read(path, seal, invented, **changes):
    values = {"feature_seal": FEATURES, "label_seal": LABELS,
              "expected_coordinates": invented[-1].original_test_coordinates,
              "expected_fold": invented[3], "expected_scenario": "S1",
              "expected_adapter_mode": "injected_diagnostic"}
    return store.read_forecasts(path, seal, **(values | changes))


def rewrite(path, seal, mutate):
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    mutate(rows)
    raw = b"".join(wire.encode(row) for row in rows)
    path.chmod(0o600)
    path.write_bytes(raw)
    path.chmod(0o400)
    return replace(seal, sha256=wire.digest(raw), size=len(raw))


def test_roundtrip_keeps_typed_original_questions_models_and_full_denominator(tmp_path, invented):
    path, seal = saved(tmp_path, invented)
    received = read(path, seal, invented)
    assert received.forecast == invented[-1]
    assert received.feature_seal == FEATURES and received.label_seal == LABELS and received.seal == seal
    assert seal.records == 1 + 4 + len(received.forecast.original_test_coordinates)
    assert path.stat().st_mode & 0o777 == 0o400
    assert store.MAX_BYTES == 128 * 1024**2 and store.MAX_LINE == 8 * 1024**2
    raw = path.read_text()
    assert '"prices"' not in raw and '"volumes"' not in raw and '"minute_available_at"' not in raw
    for left, right in zip(received.forecast.predictions, invented[-1].predictions, strict=True):
        assert left.question is right.question
    assert any(frame.status != "questions" for coordinate in received.forecast.original_test_coordinates
               for frame in coordinate.frames)


def test_original_feature_iterator_is_exhausted_before_any_usable_result(tmp_path, invented):
    path, seal = saved(tmp_path, invented)
    exhausted = False

    def originals():
        nonlocal exhausted
        yield from invented[-1].original_test_coordinates
        exhausted = True

    received = read(path, seal, invented, expected_coordinates=originals())
    assert exhausted and received.forecast.predictions


def test_missing_training_roundtrip_retains_all_six_unsupported(tmp_path, invented):
    targets, coordinates, _, fold, _ = invented
    forecast = forecast_fold(coordinates, fold, "S1", lambda *_: None,
                             expected_targets=targets, adapters=adapters([]))
    path, seal = saved(tmp_path, invented, forecast)
    restored = read(path, seal, invented).forecast
    assert restored == forecast and restored.empirical is None
    assert restored.learned_fit_count == restored.empirical_fit_count == 0
    assert all(arm.status == "unsupported" for row in restored.predictions for arm in row.predictions)


def test_absent_original_coordinate_without_questions_is_preserved(tmp_path, invented):
    targets, coordinates, sessions, fold, _ = invented
    absent = project_coordinate(coordinates[1].targets, None)
    source = (coordinates[0], absent, coordinates[2])
    forecast = forecast_fold(source, fold, "S1", label_reader(sessions, coordinates, []),
                             expected_targets=targets, adapters=adapters([]))
    path, seal = saved(tmp_path, invented, forecast)
    restored = read(path, seal, invented, expected_coordinates=(absent,)).forecast
    assert restored == forecast and restored.original_test_coordinates == (absent,)
    assert restored.predictions == () and restored.learned_fit_count == 0


def test_stage_support_gap_roundtrip_does_not_invent_predictions(tmp_path, invented):
    targets, coordinates, sessions, fold, _ = invented
    reader = label_reader(sessions, coordinates, [])

    def labels(*args):
        result = reader(*args)
        return result if result.question.stage == "approach" else None

    forecast = forecast_fold(coordinates, fold, "S1", labels, expected_targets=targets, adapters=adapters([]))
    path, seal = saved(tmp_path, invented, forecast)
    restored = read(path, seal, invented).forecast
    assert restored == forecast
    assert all(arm.status == "unsupported" for row in restored.predictions
               if row.question.question.stage == "breakout" for arm in row.predictions)


@pytest.mark.parametrize("change", ["feature_sha", "label_sha", "source_schema", "fold", "scenario", "mode"])
def test_external_original_pins_and_expected_modes_cannot_be_substituted(tmp_path, invented, change):
    path, seal = saved(tmp_path, invented)
    updates = {
        "feature_sha": {"feature_seal": replace(FEATURES, sha256="c" * 64)},
        "label_sha": {"label_seal": replace(LABELS, sha256="c" * 64)},
        "source_schema": {"feature_seal": replace(FEATURES, schema=LABEL_SCHEMA)},
        "fold": {"expected_fold": replace(invented[3], index=2)},
        "scenario": {"expected_scenario": "S2"},
        "mode": {"expected_adapter_mode": "fixed_native"},
    }
    with pytest.raises(ValueError):
        read(path, seal, invented, **updates[change])


@pytest.mark.parametrize("change", ["question", "question_missing", "question_extra", "arm_order", "mass",
    "frame", "feature_hash", "artifact", "pair", "query", "model_mode", "proof", "fit_count", "extra_field"])
def test_resealed_semantic_corruption_is_rejected(tmp_path, invented, change):
    path, seal = saved(tmp_path, invented)

    def mutate(rows):
        metadata, model, coordinate = rows[1], rows[2], rows[6]
        if change == "question":
            coordinate["predictions"][0][0] = "0" * 64
        elif change == "question_missing":
            coordinate["predictions"].pop()
        elif change == "question_extra":
            coordinate["predictions"].append(coordinate["predictions"][0])
        elif change == "arm_order":
            coordinate["predictions"][0][1].reverse()
        elif change == "mass":
            coordinate["predictions"][0][1][2][2] = [0.2] * 10
        elif change == "frame":
            coordinate["original"]["frames"][0][3] = "unknown"
        elif change == "feature_hash":
            coordinate["original"]["questions"][0][1] = "0" * 64
        elif change == "artifact":
            model["artifact"]["kind"] = "substituted"
        elif change == "pair":
            model["pair_sha256"] = "0" * 64
        elif change == "query":
            model["query_input_sha256"] = "0" * 64
        elif change == "model_mode":
            model["training"]["mode"] = "chart_context"
        elif change == "proof":
            rows[4]["independent_verification"]["query_ids"].pop()
        elif change == "fit_count":
            metadata["learned_fit_count"] = 3
        elif change == "extra_field":
            metadata["unexpected"] = True

    changed = rewrite(path, seal, mutate)
    with pytest.raises(ValueError):
        read(path, changed, invented)


@pytest.mark.parametrize("change", ["omit_coordinate", "extra_coordinate", "footer", "extra_bytes", "mode", "hash"])
def test_complete_eof_record_count_mode_and_hash_are_checked_before_return(tmp_path, invented, change):
    path, seal = saved(tmp_path, invented)
    if change in ("omit_coordinate", "extra_coordinate", "footer"):
        def mutate(rows):
            if change == "omit_coordinate":
                rows.pop(-2)
                rows[-1]["records"] -= 1
            elif change == "extra_coordinate":
                rows.insert(-1, rows[-2])
                rows[-1]["records"] += 1
            else:
                rows[-1]["records"] += 1
        seal = rewrite(path, seal, mutate)
        if change != "footer":
            seal = replace(seal, records=seal.records + (-1 if change == "omit_coordinate" else 1))
    elif change == "mode":
        path.chmod(0o600)
    elif change == "hash":
        seal = replace(seal, sha256="0" * 64)
    else:
        path.chmod(0o600)
        raw = path.read_bytes() + b"extra\n"
        path.write_bytes(raw)
        path.chmod(0o400)
        seal = replace(seal, sha256=wire.digest(raw), size=len(raw))
    with pytest.raises(ValueError):
        read(path, seal, invented)


def test_incorrect_original_stream_cannot_hide_or_duplicate_coordinates(tmp_path, invented):
    path, seal = saved(tmp_path, invented)
    coordinates = invented[-1].original_test_coordinates
    for supplied in ((), coordinates * 2):
        with pytest.raises(ValueError):
            read(path, seal, invented, expected_coordinates=iter(supplied))


def test_sequence_cannot_substitute_another_training_input_for_the_same_view(tmp_path, invented):
    path, seal = saved(tmp_path, invented)

    def substitute(rows):
        model = rows[4]
        model["training"]["input_sha256"] = "0" * 64
        model["artifact"]["training"] = model["training"]
        model["artifact_bytes_sha256"] = sha256(encode(model["artifact"])).hexdigest()
        pair = {"model": model["key"], "training_input_sha256": model["training"]["input_sha256"],
                "query_input_sha256": model["query_input_sha256"],
                "artifact_bytes_sha256": model["artifact_bytes_sha256"], "unsupported_reason": None}
        model["pair_sha256"] = sha256(encode(pair)).hexdigest()

    seal = rewrite(path, seal, substitute)
    with pytest.raises(ValueError, match="same_mode_training_inputs_differ"):
        read(path, seal, invented)


def test_reader_size_bounds_are_explicit_and_writer_does_not_overwrite(tmp_path, invented, monkeypatch):
    path, seal = saved(tmp_path, invented)
    with pytest.raises(FileExistsError):
        store.seal_forecasts(path, invented[-1], FEATURES, LABELS)
    with pytest.raises(ValueError):
        read(path, replace(seal, size=store.MAX_BYTES + 1), invented)
    monkeypatch.setattr(store, "MAX_COORDINATES", 0)
    with pytest.raises(ValueError, match="bound"):
        read(path, seal, invented)


def test_corrupt_footer_cannot_reach_evaluation_callback(tmp_path, invented):
    path, seal = saved(tmp_path, invented)
    seal = rewrite(path, seal, lambda rows: rows[-1].update(records=999))
    calls = []
    with pytest.raises(ValueError):
        stored = read(path, seal, invented)
        score_fold(stored.forecast, lambda *args: calls.append(args),
                   allowed_dates=invented[3].test_sessions, expected_targets=invented[0])
    assert calls == []


def test_saved_roundtrip_can_feed_existing_score_layer_without_refitting(tmp_path, invented):
    path, seal = saved(tmp_path, invented)
    stored = read(path, seal, invented)
    calls = []
    report = score_fold(stored.forecast, lambda *args: calls.append(args),
                        allowed_dates=invented[3].test_sessions, expected_targets=invented[0])
    assert report.original_questions == len(stored.forecast.predictions)
    assert calls and {row[0] for row in calls} == set(invented[3].test_sessions)


def test_tiny_native_artifacts_and_independent_proofs_roundtrip(tmp_path, invented):
    pytest.importorskip("numpy")
    pytest.importorskip("lightgbm")
    targets, coordinates, sessions, fold, _ = invented
    forecast = forecast_fold(coordinates, fold, "S1", label_reader(sessions, coordinates, []), expected_targets=targets)
    path, seal = saved(tmp_path, invented, forecast)
    restored = read(path, seal, invented, expected_adapter_mode="fixed_native").forecast
    assert restored == forecast
    assert all(sha256(encode(model.artifact)).hexdigest() == model.artifact_bytes_sha256 for model in restored.models)
