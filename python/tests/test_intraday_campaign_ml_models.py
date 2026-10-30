"""Small invented numerical rows only; no archive, actual labels or runner."""

from copy import deepcopy
import builtins
import hashlib
import importlib.util
import json
import math

import pytest

from research import intraday_campaign_ml_models as models


NAMES = models.FEATURE_NAMES
BINDING = {"slot": "invented-D+1-fold1", "runtime_pin": "invented-runtime-sha256",
           "train_row_keys": ["invented-a", "invented-b", "invented-c", "invented-d"]}


@pytest.fixture(autouse=True)
def one_thread(monkeypatch):
    for name in models.THREAD_VARIABLES:
        monkeypatch.setenv(name, "1")


def rows(values):
    return tuple((float(value),) + (0.1,) * 9 for value in values)


def fit(X=None, y=None, learner="ridge"):
    return models.fit_model(learner, rows([0, 2, 4, 6]) if X is None else X,
                            [1, 3, 5, 7] if y is None else y,
                            feature_names=NAMES, binding=BINDING)


def rehash(artifact):
    payload = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    artifact["artifact_sha256"] = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode("ascii")).hexdigest()
    return artifact


def verify(artifact, X=None, y=None, test=None, predictions=None, binding=None):
    test = rows([1, 5]) if test is None else test
    predictions = (models.predict_model(artifact, test, feature_names=NAMES)
                   if predictions is None else predictions)
    return models.verify_model(artifact, rows([0, 2, 4, 6]) if X is None else X,
                               [1, 3, 5, 7] if y is None else y, test, predictions,
                               feature_names=NAMES, binding=BINDING if binding is None else binding)


def test_fixed_ridge_sse_alpha_population_scaling_and_unpenalized_intercept():
    artifact = fit()
    model = artifact["model"]
    assert float.fromhex(model["means"][0]) == 3
    assert float.fromhex(model["scales"][0]) == math.sqrt(5)
    assert model["constant_mask"] == [False] + [True] * 9
    assert float.fromhex(model["intercept"]) == 4
    assert float.fromhex(model["coefficients"][0]) == pytest.approx(4 * math.sqrt(5) / 5)
    assert models.predict_model(artifact, rows([1, 5]), feature_names=NAMES) == pytest.approx((2.4, 5.6))
    receipt = verify(artifact)
    assert receipt["verified"] and receipt["strict_positive_signs_agree"]
    assert receipt["prediction_count"] == 2
    assert len(receipt["ridge_normal_equation_residual_hex"]) == 1
    assert json.loads(json.dumps(artifact)) == artifact


def test_intercept_only_model_drops_exact_training_constants_and_ignores_test_variation():
    X = rows([0.1] * 6)
    y = [3, 3, 3, 3, 3, 3]
    artifact = fit(X, y)
    assert artifact["model"]["constant_mask"] == [True] * 10
    assert artifact["model"]["coefficients"] == []
    test = rows([-500, 500])
    assert models.predict_model(artifact, test, feature_names=NAMES) == (3, 3)
    assert verify(artifact, X=X, y=y, test=test)["verified"]
    assert models.predict_model(artifact, [], feature_names=NAMES) == ()


def test_small_but_nonconstant_feature_is_not_dropped():
    artifact = fit(rows([0, 1e-15, 2e-15, 3e-15]), [1, 3, 5, 7])
    assert artifact["model"]["constant_mask"][0] is False
    assert verify(artifact, X=rows([0, 1e-15, 2e-15, 3e-15]))["verified"]


def test_transform_tolerance_cannot_hide_a_different_prediction_sign():
    X, y = rows([0, 1e-15, 2e-15, 3e-15]), [-1, -1, 1, 1]
    artifact = fit(X, y)
    # This mean shift is smaller than the declared absolute transform tolerance.
    # Independent-transform prediction replay still rejects its changed admission.
    artifact["model"]["means"][0] = (1.8e-15).hex()
    rehash(artifact)
    test = rows([1.6e-15])
    supplied = models.predict_model(artifact, test, feature_names=NAMES)
    assert supplied[0] < 0
    with pytest.raises(ValueError, match="admission sign"):
        verify(artifact, X=X, y=y, test=test, predictions=supplied)


def test_no_mutation_and_binding_snapshot():
    X, y, binding = [list(row) for row in rows([0, 2, 4, 6])], [1, 3, 5, 7], deepcopy(BINDING)
    before = deepcopy((X, y, binding))
    artifact = models.fit_model("ridge", X, y, feature_names=list(NAMES), binding=binding)
    assert (X, y, binding) == before
    binding["slot"] = "changed-after-fit"
    assert artifact["binding"]["slot"] == BINDING["slot"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), "1", True])
def test_rejects_nonfinite_and_nonnumeric_inputs(value):
    X = [list(row) for row in rows([0, 2, 4, 6])]
    X[0][0] = value
    with pytest.raises(ValueError):
        fit(X)
    with pytest.raises(ValueError):
        fit(y=[value, 3, 5, 7])


def test_rejects_shape_unknown_learner_feature_order_and_empty_binding():
    for X in ([], [(1,) * 9], [(1,) * 11]):
        with pytest.raises(ValueError):
            fit(X, [])
    with pytest.raises(ValueError):
        fit(y=[1])
    with pytest.raises(ValueError, match="only fixed"):
        fit(learner="alternative")
    with pytest.raises(ValueError, match="feature order"):
        models.fit_model("ridge", rows([0, 2, 4, 6]), [1, 3, 5, 7],
                         feature_names=NAMES[::-1], binding=BINDING)
    with pytest.raises(ValueError, match="nonempty caller"):
        models.fit_model("ridge", rows([0, 2, 4, 6]), [1, 3, 5, 7],
                         feature_names=NAMES, binding={})


def test_environment_is_checked_without_rewriting_it(monkeypatch):
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "2")
    with pytest.raises(ValueError, match="single-thread"):
        fit()
    assert models.os.environ["OPENBLAS_NUM_THREADS"] == "2"


def test_import_is_lean_without_numerical_imports(monkeypatch):
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in ("numpy", "scipy", "lightgbm"):
            raise AssertionError("numerical import during module load")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    importlib.reload(models)


@pytest.mark.parametrize("change,reason", [
    (lambda a: a["parameters"].update(alpha=2), "parameter"),
    (lambda a: a["runtime"].update(numpy="other"), "runtime"),
    (lambda a: a["feature_names"].reverse(), "scientific"),
    (lambda a: a["numerical_contract"].update(strict_positive_sign_tolerance=1), "scientific"),
    (lambda a: a["model"]["means"].__setitem__(0, (3.2).hex()), "training mean"),
    (lambda a: a["model"]["scales"].__setitem__(0, (5.0).hex()), "population scale"),
    (lambda a: a["model"]["coefficients"].__setitem__(0, (0.1).hex()), "normal-equation"),
    (lambda a: a["model"].update(intercept=(4.1).hex()), "intercept"),
])
def test_independent_verification_rejects_rehashed_tampering(change, reason):
    artifact = fit()
    change(artifact)
    rehash(artifact)
    with pytest.raises(ValueError, match=reason):
        verify(artifact)


def test_hash_and_canonical_float_encoding_tampering():
    artifact = fit()
    artifact["model"]["intercept"] = (4.1).hex()
    with pytest.raises(ValueError, match="hash"):
        verify(artifact)
    for invalid in ("nan", "inf", "0x1p+2"):
        artifact = fit()
        artifact["model"]["intercept"] = invalid
        rehash(artifact)
        with pytest.raises(ValueError, match="canonical finite"):
            verify(artifact)


def test_exact_training_row_label_binding_and_test_order_receipts():
    artifact = fit()
    for changes in ({"X": rows([6, 4, 2, 0])}, {"y": [7, 5, 3, 1]},
                    {"binding": {"slot": "different"}}):
        with pytest.raises(ValueError, match="binding mismatch"):
            verify(artifact, **changes)
    with pytest.raises(ValueError, match="replay mismatch"):
        verify(artifact, predictions=(5.6, 2.4))
    forward = verify(artifact)
    reverse = verify(artifact, test=rows([5, 1]))
    assert forward["test_features_sha256"] != reverse["test_features_sha256"]


def test_ieee754_row_hash_distinguishes_signed_zero():
    positive = fit(rows([0, 2, 4, 6]))
    negative = fit(rows([-0.0, 2, 4, 6]))
    assert positive["train_features_sha256"] != negative["train_features_sha256"]


def test_admission_sign_has_no_numerical_tolerance():
    X, y, test = rows([0, 0]), [0, 0], rows([0])
    artifact = fit(X, y)
    assert models.predict_model(artifact, test, feature_names=NAMES) == (0,)
    with pytest.raises(ValueError, match="admission sign"):
        verify(artifact, X=X, y=y, test=test, predictions=(1e-100,))
    assert verify(artifact, X=X, y=y, test=test, predictions=(-0.0,))["verified"]


def test_verifier_never_refits_or_calls_numpy_solver(monkeypatch):
    artifact = fit()
    predictions = models.predict_model(artifact, rows([1, 5]), feature_names=NAMES)
    import numpy
    def forbidden(*args, **kwargs):
        raise AssertionError("verification attempted a fresh fit")
    monkeypatch.setattr(models, "fit_model", forbidden)
    monkeypatch.setattr(models, "predict_model", forbidden)
    monkeypatch.setattr(numpy.linalg, "solve", forbidden)
    assert verify(artifact, predictions=predictions)["verified"]


def test_lightgbm_predeclared_manifest_has_complete_fixed_native_closure():
    manifest = models.lightgbm_parameter_manifest()
    assert len(manifest["native_effective"]) == 120
    assert manifest["requested"]["num_iterations"] == 100
    assert manifest["native_effective"]["learning_rate"] == "0.05"
    assert manifest["native_effective"]["early_stopping_round"] == "0"
    assert manifest["native_effective"]["force_row_wise"] == "0"
    assert manifest["python_api"]["valid_sets"] is None
    manifest["requested"]["max_depth"] = 100
    assert models.lightgbm_parameter_manifest()["requested"]["max_depth"] == 3


def test_fixed_parameter_and_tolerance_constants_are_immutable():
    for params, key in ((models.RIDGE_PARAMETERS, "alpha"),
                        (models.LIGHTGBM_PARAMETERS, "max_depth"),
                        (models.NUMERICAL_CONTRACT, "absolute_tolerance")):
        with pytest.raises(TypeError):
            params[key] = 2
    artifact = fit()
    artifact["parameters"]["alpha"] = True
    rehash(artifact)
    with pytest.raises(ValueError, match="parameter"):
        verify(artifact)


def test_nonfinite_prediction_and_feature_fail_closed():
    artifact = fit()
    with pytest.raises(ValueError, match="nonfinite"):
        verify(artifact, predictions=[float("nan"), 1])
    with pytest.raises(ValueError, match="nonfinite"):
        models.predict_model(artifact, rows([float("inf")]), feature_names=NAMES)


@pytest.mark.skipif(importlib.util.find_spec("lightgbm") is None, reason="native LightGBM unavailable")
def test_native_lightgbm_serialization_parameters_replay_and_no_refit(monkeypatch):
    X = rows(range(120))
    y = [value / 1000 - 0.06 for value in range(120)]
    artifact = fit(X, y, "lightgbm")
    test = rows([10, 80, 110])
    predictions = models.predict_model(artifact, test, feature_names=NAMES)
    import lightgbm
    def forbidden(*args, **kwargs):
        raise AssertionError("verification attempted native retraining")
    monkeypatch.setattr(lightgbm, "train", forbidden)
    receipt = verify(artifact, X=X, y=y, test=test, predictions=predictions)
    assert receipt["verified"]
    assert "pinned LightGBM" in receipt["trusted_external_boundary"]
    assert artifact["model"]["num_trees"] == artifact["model"]["current_iteration"] == 100
    assert all(math.isfinite(value) for value in predictions)
    for change in ("string", "native_parameter", "feature_order", "iteration", "native_labels"):
        bad = deepcopy(artifact)
        if change in ("string", "native_parameter", "feature_order"):
            if change == "string":
                bad["model"]["model_string"] += "corruption"
            elif change == "native_parameter":
                bad["model"]["model_string"] = bad["model"]["model_string"].replace(
                    "[max_depth: 3]", "[max_depth: 4]")
            else:
                bad["model"]["model_string"] = bad["model"]["model_string"].replace(
                    "feature_names=" + " ".join(NAMES), "feature_names=" + " ".join(NAMES[::-1]))
            if change != "string":
                bad["model"]["model_string_sha256"] = hashlib.sha256(
                    bad["model"]["model_string"].encode()).hexdigest()
        elif change == "iteration":
            bad["model"]["current_iteration"] = 99
        else:
            bad["model"]["native_training_labels_sha256"] = "0" * 64
        rehash(bad)
        with pytest.raises(ValueError):
            verify(bad, X=X, y=y, test=test, predictions=predictions)


@pytest.mark.skipif(importlib.util.find_spec("lightgbm") is None, reason="native LightGBM unavailable")
def test_native_lightgbm_no_gain_stop_is_preserved_without_retry(monkeypatch):
    import lightgbm
    original, calls = lightgbm.train, []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(lightgbm, "train", counted)
    X, y, test = rows([0] * 40), [0.125] * 40, rows([20, -20])
    artifact = fit(X, y, "lightgbm")
    assert calls == [1]
    assert artifact["model"]["current_iteration"] == 1
    assert models.predict_model(artifact, test, feature_names=NAMES) == (0.125, 0.125)
    assert verify(artifact, X=X, y=y, test=test)["verified"]
    assert calls == [1]


@pytest.mark.skipif(importlib.util.find_spec("lightgbm") is None, reason="native LightGBM unavailable")
def test_native_lightgbm_rejects_label_storage_overflow_before_fit(monkeypatch):
    import lightgbm
    def forbidden(*args, **kwargs):
        raise AssertionError("fit began despite nonfinite float32 label")
    monkeypatch.setattr(lightgbm, "train", forbidden)
    with pytest.raises(ValueError, match="float32 training label"):
        fit(rows([0] * 40), [1e100] * 40, "lightgbm")
