"""Fixed EI native fit/reload/replay tests use only invented feature rows."""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import importlib.util
import json

import pytest

from research import intraday_context_lightgbm as model
from research.intraday_context_model_inputs import encode, prepare_queries, prepare_training
from research.intraday_ed_inputs import InputRefusal
from test_intraday_context_model_inputs import CUTOFF, sample


NATIVE = pytest.mark.skipif(importlib.util.find_spec("lightgbm") is None or importlib.util.find_spec("numpy") is None,
                            reason="pinned native LightGBM/NumPy unavailable; invented native parity not run")


def test_fixed_parameters_do_not_create_tuning_or_calibration_surface():
    assert model.ROUNDS == 40
    assert {key: model.PARAMETERS[key] for key in ("objective", "num_class", "num_leaves", "max_depth",
        "min_data_in_leaf", "learning_rate", "num_threads")} == {
        "objective": "multiclass", "num_class": 10, "num_leaves": 7, "max_depth": 3,
        "min_data_in_leaf": 20, "learning_rate": 0.05, "num_threads": 1}
    assert model.PARAMETERS["deterministic"] is model.PARAMETERS["force_col_wise"] is True
    assert model.PARAMETERS["bagging_fraction"] == model.PARAMETERS["feature_fraction"] == 1
    assert model.PARAMETERS["bagging_freq"] == 0
    assert not any("early_stopping" in key for key in model.PARAMETERS)


def test_empty_support_and_malformed_training_never_load_native_libraries(monkeypatch):
    monkeypatch.setattr(model, "_libraries", lambda: pytest.fail("native import before metadata admission"))
    empty = prepare_training([], with_context=False, cutoff_at=CUTOFF, fold=1)
    assert model.fit_lightgbm(empty) is None
    training = prepare_training([sample()], with_context=False, cutoff_at=CUTOFF, fold=1)
    with pytest.raises(InputRefusal):
        model.fit_lightgbm(replace(training, weights=(True,)))
    with pytest.raises(InputRefusal):
        model.load_lightgbm(b"{}", expected_sha256="0" * 64)


@pytest.fixture(scope="module")
def native_models():
    np = pytest.importorskip("numpy")
    scipy = pytest.importorskip("scipy")
    lgb = pytest.importorskip("lightgbm")
    assert {"numpy": np.__version__, "scipy": scipy.__version__, "lightgbm": lgb.__version__} == model.VERSIONS
    result = {}
    for mode in ("chart", "chart_context"):
        rows = [sample(index, symbol=f"invented{index // 3}", episode=index % 3 + 1,
                       outcome=index % 10, mode=mode) for index in range(180)]
        prepared = prepare_training(rows, with_context=mode == "chart_context", cutoff_at=CUTOFF, fold=1)
        artifact = model.fit_lightgbm(prepared)
        queries = prepare_queries([sample(index, day=2, mode=mode).features for index in range(10)],
                                   with_context=mode == "chart_context", cutoff_at=CUTOFF, fold=1)
        result[mode] = artifact, queries, prepared
    return result


@NATIVE
@pytest.mark.parametrize("mode", ["chart", "chart_context"])
def test_native_fit_saved_reload_and_independent_probability_replay(native_models, mode):
    artifact, queries, training = native_models[mode]
    assert 1 <= artifact["effective_iterations"] <= 40
    assert artifact["training"]["input_sha256"] == training.input_sha256
    assert artifact["training"]["weights_sha256"] == training.weights_sha256
    assert artifact["training"]["outcomes_sha256"] == training.outcomes_sha256
    values = model.predict_lightgbm(artifact, queries)
    assert len(values) == len(queries.rows) and all(len(row) == 10 for row in values)
    assert all(sum(row) == pytest.approx(1.0, abs=1e-12) for row in values)
    raw = model.dump_lightgbm(artifact)
    restored = model.load_lightgbm(raw, expected_sha256=sha256(raw).hexdigest())
    assert model.predict_lightgbm(restored, queries) == values
    assert json.loads(raw)["training"]["weighted_stage_groups"] == 60


@NATIVE
def test_native_no_split_completion_records_actual_iteration_count():
    training = prepare_training([sample()], with_context=False, cutoff_at=CUTOFF, fold=1)
    artifact = model.fit_lightgbm(training)
    assert artifact["effective_iterations"] == 1
    assert len(artifact["dump"]["tree_info"]) == 10
    assert artifact["round_limit"] == 40


@NATIVE
@pytest.mark.parametrize("mutation", ["iterations_bool", "parameter", "feature_order", "objective", "class_count",
    "dump_leaf", "text_threads", "training_mode", "training_fold", "training_cutoff"])
def test_saved_model_mutations_cannot_pass_native_or_reference_binding(native_models, mutation):
    original, queries, _ = native_models["chart"]
    changed = deepcopy(original)
    if mutation == "iterations_bool":
        changed["effective_iterations"] = True
    elif mutation == "parameter":
        changed["parameters"]["num_leaves"] = 8
    elif mutation == "feature_order":
        changed["dump"]["feature_names"] = list(reversed(changed["dump"]["feature_names"]))
    elif mutation == "objective":
        changed["dump"]["objective"] = "multiclassova num_class:10"
    elif mutation == "class_count":
        changed["dump"]["num_class"] = 9
    elif mutation == "dump_leaf":
        node = changed["dump"]["tree_info"][0]["tree_structure"]
        while "left_child" in node:
            node = node["left_child"]
        node["leaf_value"] += 0.25
    elif mutation == "text_threads":
        assert "[num_threads: 1]" in changed["model_text"]
        changed["model_text"] = changed["model_text"].replace("[num_threads: 1]", "[num_threads: 2]")
        changed["model_sha256"] = sha256(changed["model_text"].encode()).hexdigest()
    elif mutation == "training_mode":
        changed["training"]["mode"] = "chart_context"
    elif mutation == "training_fold":
        changed["training"]["fold"] = 2
    else:
        changed["training"]["cutoff_at"] = "2031-02-15T00:00:00+00:00"
    changed["dump_sha256"] = sha256(encode(changed["dump"])).hexdigest()
    with pytest.raises(ValueError):
        model.predict_lightgbm(changed, queries)


@NATIVE
def test_native_prediction_error_is_detected_for_every_query(native_models, monkeypatch):
    import lightgbm as lgb

    artifact, queries, _ = native_models["chart"]
    original = lgb.Booster.predict

    def wrong(self, *args, **kwargs):
        answer = original(self, *args, **kwargs)
        answer[-1, 0] += 0.01
        return answer

    monkeypatch.setattr(lgb.Booster, "predict", wrong)
    with pytest.raises(InputRefusal, match="probability_replay"):
        model.predict_lightgbm(artifact, queries)


@NATIVE
def test_saved_bytes_require_external_pin_canonical_form_and_exact_package_versions(native_models, monkeypatch):
    artifact, queries, _ = native_models["chart"]
    raw = model.dump_lightgbm(artifact)
    with pytest.raises(InputRefusal, match="pin"):
        model.load_lightgbm(raw, expected_sha256="f" * 64)
    noncanonical = raw + b"\n"
    with pytest.raises(InputRefusal, match="canonical"):
        model.load_lightgbm(noncanonical, expected_sha256=sha256(noncanonical).hexdigest())
    import lightgbm as lgb

    monkeypatch.setattr(lgb, "__version__", "4.6.1")
    with pytest.raises(InputRefusal, match="versions"):
        model.predict_lightgbm(artifact, queries)


@NATIVE
def test_empty_query_is_target_free_and_keeps_artifact_validation(native_models):
    artifact, _, _ = native_models["chart"]
    queries = prepare_queries([], with_context=False, cutoff_at=CUTOFF, fold=1)
    assert model.predict_lightgbm(artifact, queries) == ()
    with pytest.raises(InputRefusal):
        model.predict_lightgbm(artifact, replace(queries, input_sha256="a" * 64))
