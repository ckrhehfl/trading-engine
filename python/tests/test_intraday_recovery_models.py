"""Invented-only EK model contracts, numerical gradients and independent replay."""

from copy import deepcopy
from dataclasses import fields, replace
from hashlib import sha256
import importlib.util
from math import fsum
from pathlib import Path
import subprocess
import sys

import pytest

from research import intraday_recovery_models as model
from research import intraday_recovery_models_reference as reference


PINS = {name: str(index) * 64 for index, name in enumerate((
    "source_sha256", "runtime_sha256", "registration_sha256", "features_sha256", "labels_sha256"), 1)}
CUTOFF = "2031-02-10"
STATIC_NAMES = ("path_efficiency", "contraction")
NATIVE = pytest.mark.skipif(importlib.util.find_spec("numpy") is None, reason="pinned invented NumPy runtime absent")
LGB = pytest.mark.skipif(importlib.util.find_spec("lightgbm") is None, reason="pinned invented LightGBM runtime absent")


@pytest.fixture(autouse=True)
def native_thread_environment(monkeypatch):
    # The external qualification owns actual process limits and import authentication.
    for name in model.THREAD_VARIABLES:
        monkeypatch.setenv(name, "1")


def row(index=0, *, peer=False, unknown=False):
    sign = float(1 if index % 2 else -1)
    minute = tuple(tuple(float(sign * (at / 60 + channel / 100)) for channel in range(10)) for at in range(60))
    peers = tuple((float(at / 60), sign, 0.5) for at in range(60)) if peer else None
    return model.ModelRow(f"invented-{index}", f"2031-02-{index // 24 + 1:02d}", minute, (sign, 1.0),
                          None if unknown else sign / 2, None if unknown else index % 3,
                          f"2031-02-{index // 24 + 1:02d}", peers)


def training(rows=None, *, mode="chart", fold=1):
    rows = tuple(row(index, peer=mode == "chart_peer") for index in range(12)) if rows is None else tuple(rows)
    return model.prepare_training(rows, stock_by_key={item.key: f"stock-{index % 4}" for index, item in enumerate(rows)},
        fold=fold, cutoff_session=CUTOFF, mode=mode, static_names=STATIC_NAMES, provenance=PINS)


def queries(prepared, count=2):
    rows = []
    for index in range(count):
        sample = row(index, peer=prepared.contract["mode"] == "chart_peer")
        rows.append(model.ModelQuery(f"query-{index}", CUTOFF, sample.minute, sample.static, sample.peer))
    return model.prepare_queries(rows, model.training_contract(prepared))


def reseal(artifact):
    artifact["artifact_sha256"] = sha256(model.encode({key: value for key, value in artifact.items()
                                                     if key != "artifact_sha256"})).hexdigest()


def test_import_has_no_native_library_or_file_reader_side_effect():
    root = Path(__file__).resolve().parents[1]
    code = f"import sys; sys.path.insert(0, {str(root)!r}); from research import intraday_recovery_models; "
    code += "assert 'numpy' not in sys.modules; assert 'lightgbm' not in sys.modules"
    subprocess.run([sys.executable, "-I", "-S", "-c", code], check=True, capture_output=True, timeout=15)


def test_query_type_cannot_carry_outcomes_and_training_is_past_only():
    names = {field.name for field in fields(model.ModelQuery)}
    assert not names & {"target_gross_pct", "path_class", "mature_session"}
    prepared = training()
    with pytest.raises(ValueError, match="target_separated"):
        model.prepare_queries([replace(row(), session=CUTOFF)], model.training_contract(prepared))
    with pytest.raises(ValueError, match="mature"):
        training([replace(row(), mature_session=CUTOFF)])
    with pytest.raises(ValueError, match="before_split"):
        model.prepare_queries([model.ModelQuery("q", "2031-02-09", row().minute, row().static)],
                              model.training_contract(prepared))


def test_date_stock_weights_do_not_count_repeated_questions_as_new_stock_days():
    rows = (row(0), row(1), row(2), replace(row(3), session="2031-02-02", mature_session="2031-02-02"))
    prepared = model.prepare_training(rows, stock_by_key={"invented-0": "A", "invented-1": "A",
        "invented-2": "B", "invented-3": "A"}, fold=1, cutoff_session=CUTOFF, mode="chart",
        static_names=STATIC_NAMES, provenance=PINS)
    assert prepared.gross_weights == (0.125, 0.125, 0.25, 0.5)
    assert prepared.path_weights == prepared.gross_weights
    assert prepared.contract["date_count"] == 2 and prepared.contract["stock_dates"] == 3


def test_independent_target_masks_preserve_unknown_and_observed_zero():
    rows = [replace(row(0), target_gross_pct=0.0, path_class=None),
            replace(row(1), target_gross_pct=None, path_class=0), row(2, unknown=True)]
    prepared = training(rows)
    assert prepared.gross_weights == (1.0, 0.0, 0.0)
    assert prepared.path_weights == (0.0, 1.0, 0.0)
    artifact = model.fit_model("empirical", prepared)
    prediction, = model.predict_model(artifact, queries(prepared, 1))
    assert prediction.gross_pct == 0.0 and prediction.probabilities == pytest.approx((0.6, 0.2, 0.2))


def test_common_binary32_gross_boundary_is_shared_and_disclosed():
    prepared = training([replace(row(), target_gross_pct=0.1)])
    artifact = model.fit_model("empirical", prepared)
    prediction, = model.predict_model(artifact, queries(prepared, 1))
    assert prediction.gross_pct == model._gross(prepared.rows[0]) != 0.1
    assert artifact["training"]["gross_numeric_boundary"] == "common_ieee754_binary32_targets_before_any_learner"
    with pytest.raises(ValueError, match="binary32"):
        training([replace(row(), target_gross_pct=1e300)])


def test_holding_target_is_a_separate_contract_without_an_initial_path_head():
    sample = replace(row(), path_class=None)
    kwargs = dict(stock_by_key={sample.key: "A"}, fold=1, cutoff_session=CUTOFF, mode="chart",
                  static_names=STATIC_NAMES, provenance=PINS)
    prepared = model.prepare_training([sample], **kwargs, target_kind="holding_remaining")
    assert prepared.contract["target_kind"] == "holding_remaining"
    prediction, = model.predict_model(model.fit_model("empirical", prepared), queries(prepared, 1))
    assert prediction.gross_pct is not None and prediction.probabilities is None
    with pytest.raises(ValueError, match="initial_path"):
        model.prepare_training([row()], **kwargs, target_kind="holding_remaining")
    with pytest.raises(ValueError, match="identity"):
        model.predict_model(model.fit_model("empirical", prepared), queries(training([sample]), 1))


def test_empty_and_all_unknown_training_never_loads_numpy(monkeypatch):
    monkeypatch.setattr(model, "_numpy", lambda: pytest.fail("unsupported fit loaded native runtime"))
    for prepared in (training([]), training([row(unknown=True)])):
        for kind in model.KINDS:
            artifact = model.fit_model(kind, prepared)
            assert model.predict_model(artifact, queries(prepared)) == (
                model.ModelPrediction("query-0", None, None), model.ModelPrediction("query-1", None, None))


@pytest.mark.parametrize("change", [
    lambda value: replace(value, path_class=True),
    lambda value: replace(value, target_gross_pct=float("nan")),
    lambda value: replace(value, minute=value.minute[:-1]),
    lambda value: replace(value, static=(True, 1.0)),
    lambda value: replace(value, peer=((0.0, 0.0, 0.0),) * 60),
])
def test_invalid_features_targets_and_chart_peer_leakage_refuse(change):
    with pytest.raises(ValueError):
        training([change(row())])


def test_training_only_scaling_constant_scale_and_mutation_detection():
    prepared = training()
    original = deepcopy(prepared.contract)
    query = queries(prepared)
    huge = replace(query.rows[0], static=(1e6, 9.0))
    changed = model.prepare_queries([huge], query.contract)
    assert changed.contract == original | {"training_input_sha256": prepared.input_sha256}
    assert prepared.contract["scaling"]["scale"][-1] == 1.0
    with pytest.raises(ValueError, match="changed"):
        model.validate_training(replace(prepared, gross_weights=(1.0,) * len(prepared.rows)))
    with pytest.raises(ValueError, match="changed"):
        model.validate_queries(replace(query, rows=(huge,)))


def test_order_ablation_preserves_latest_static_multiset_and_training_scale():
    ordinary, ablated = training(), training(mode="order_removed")
    sample = ordinary.rows[0]
    result = model._minute(sample, "order_removed")
    assert result[-1] == sample.minute[-1] and sorted(result[:-1]) == sorted(sample.minute[:-1])
    assert result[:-1] != sample.minute[:-1] and sample.static == ablated.rows[0].static
    assert ordinary.contract["scaling"] == ablated.contract["scaling"]


def test_artifact_roundtrip_external_pin_and_complete_provenance():
    prepared = training()
    artifact = model.fit_model("empirical", prepared)
    raw = model.dump_model(artifact)
    assert model.load_model(raw, expected_sha256=sha256(raw).hexdigest()) == artifact
    assert artifact["training"]["provenance"] == PINS
    with pytest.raises(ValueError, match="external_pin"):
        model.load_model(raw, expected_sha256="0" * 64)
    changed = deepcopy(artifact)
    changed["payload"]["probabilities"][0] = 2.0
    reseal(changed)
    with pytest.raises(ValueError, match="probability"):
        model.dump_model(changed)


def test_replay_detects_changed_predictions_even_with_valid_saved_model():
    prepared = training()
    artifact, query = model.fit_model("empirical", prepared), queries(prepared)
    predictions = model.predict_model(artifact, query)
    with pytest.raises(ValueError, match="gross_replay"):
        reference.verify_predictions(artifact, query, (replace(predictions[0], gross_pct=4.0), predictions[1]))
    with pytest.raises(ValueError, match="identity"):
        reference.verify_predictions(artifact, query, tuple(reversed(predictions)))


def test_reference_rejects_resealed_invalid_target_contract_and_zero_scale():
    artifact = model.fit_model("empirical", training())
    for change in (lambda value: value["training"].update(target_kind="unregistered"),
                   lambda value: value["training"]["scaling"]["scale"].__setitem__(0, 0.0)):
        altered = deepcopy(artifact)
        change(altered)
        reseal(altered)
        with pytest.raises(ValueError):
            reference.validate_artifact(altered)


def test_source_and_query_contract_cannot_be_rebound_to_another_fold_or_feature():
    prepared = training()
    artifact = model.fit_model("empirical", prepared)
    with pytest.raises(ValueError, match="identity"):
        model.predict_model(artifact, queries(training(fold=2)))
    altered = deepcopy(artifact)
    altered["training"]["provenance"]["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        model.dump_model(altered)


@NATIVE
def test_sequence_causality_shared_initialization_and_weighted_gradients():
    np = model._numpy()
    prepared = training()
    contract = model.training_contract(prepared)
    parameters = model._initialize(np, contract)
    peer = model._initialize(np, model.training_contract(training(mode="chart_peer")))
    assert np.array_equal(parameters["projection"], peer["projection"][:10])
    assert not peer["projection"][10:].any()
    assert all(np.array_equal(value, peer[name]) for name, value in parameters.items() if name != "projection")
    batch = model._batch(np, prepared.rows[:2], contract)
    changed = (batch[0].copy(), batch[1])
    changed[0][:, 40:] += 100
    from research.intraday_scenario_sequence import _tower
    before = _tower(np, batch[0], parameters, model._NAMES, model.DILATIONS)[-1]
    after = _tower(np, changed[0], parameters, model._NAMES, model.DILATIONS)[-1]
    assert np.array_equal(before[:, :40], after[:, :40])
    gross, paths, weights = np.array([-0.5, 0.5]), np.array([0, 1]), np.array([0.25, 0.75])
    gradients = model._gradients(np, batch, parameters, gross, paths, weights, weights)

    def loss():
        estimates, probabilities, _, _ = model._forward(np, batch, parameters)
        return float(np.sum(weights * (estimates - gross) ** 2) - np.sum(weights * np.log(probabilities[np.arange(2), paths])))

    for name, coordinate in (("head", (2, 1)), ("projection", (1, 2)), ("kernel_2", (1, 1, 2))):
        original, step = parameters[name][coordinate], 1e-6
        parameters[name][coordinate] = original + step
        upper = loss()
        parameters[name][coordinate] = original - step
        lower = loss()
        parameters[name][coordinate] = original
        assert gradients[name][coordinate] == pytest.approx((upper - lower) / (2 * step), rel=1e-5, abs=1e-6)


@NATIVE
@pytest.mark.parametrize("mode", model.MODES)
def test_fixed_sequence_training_saved_replay_and_deterministic_seed(mode):
    prepared = training(mode=mode)
    artifact = model.fit_model("sequence", prepared)
    assert model.fit_model("sequence", prepared) == artifact
    predictions = model.predict_model(artifact, queries(prepared))
    assert all(value.gross_pct is not None and fsum(value.probabilities) == pytest.approx(1.0) for value in predictions)
    changed = deepcopy(artifact)
    changed["payload"]["parameters"]["head"][0].append(0.0)
    reseal(changed)
    with pytest.raises(ValueError, match="geometry"):
        model.dump_model(changed)


@LGB
def test_native_tree_fit_reload_and_independent_scalar_replay():
    prepared = training([row(index) for index in range(144)])
    artifact = model.fit_model("lightgbm", prepared)
    predictions = model.predict_model(artifact, queries(prepared))
    assert all(value.gross_pct is not None and fsum(value.probabilities) == pytest.approx(1.0) for value in predictions)
    assert all(1 <= value["iterations"] <= 40 for value in artifact["payload"].values())
    changed = deepcopy(artifact)
    changed["payload"]["gross"]["parameters"]["num_threads"] = 2
    reseal(changed)
    with pytest.raises(ValueError, match="parameters"):
        model.dump_model(changed)


@NATIVE
@pytest.mark.parametrize("head", ["gross", "path"])
def test_sequence_masked_head_remains_unsupported(head):
    rows = [replace(row(index), **({"path_class": None} if head == "gross" else {"target_gross_pct": None}))
            for index in range(4)]
    prepared = training(rows)
    predictions = model.predict_model(model.fit_model("sequence", prepared), queries(prepared))
    assert all((value.gross_pct is not None) == (head == "gross")
               and (value.probabilities is not None) == (head == "path") for value in predictions)
