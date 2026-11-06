"""Invented model-adapter fixtures. No native fit or saved market input."""

from dataclasses import replace
from fractions import Fraction
from struct import pack, unpack
import sys
from types import SimpleNamespace

import pytest

from research import intraday_highvol_models as models


def _input(*, holding=False, peer=False):
    padded = (0.0,) * 11 + (1.0,)
    observed = (0.0, 1.0, -1.0, 0.5, 0.5, 2.0, 0.75, 0.0, 0.0, 0.0, 1.0, 0.0)
    if peer:
        padded += (0.0, 0.0, 0.0, 0.0, 0.0, 1.0)
        observed += (0.5, 1.0, 0.75, 0.5, 1.0, 0.0)
    return models.ModelInput((padded,) * 45 + (observed,) * 15,
                             models.decision_state(holding=holding, remaining_minutes=25 if holding else 30,
                                                   close=Fraction(100), stop=Fraction(99), target=Fraction(102)))


def test_exact_labels_masks_and_masked_chart_summary():
    entry, hold = _input(), _input(holding=True)
    inputs, labels, manifest = models._training("LGB_CHART", (
        models.TrainingRow(entry, gross30=Fraction(1, 300), target_first=False),
        models.TrainingRow(entry, target_first=True),
        models.TrainingRow(hold, hold_advantage=Fraction(0)),
    ), 1)
    assert labels == ((1 / 3, 0.0, None), (None, 1.0, None), (None, None, 0.0))
    assert manifest["task_rows"] == [1, 2, 1]
    assert manifest["target_first_false_true_rows"] == [1, 1]
    matrix, targets = models._lgb_training(inputs, labels)
    assert targets == (1 / 3, 0.0, 1.0, 0.0)
    gross, target, holding = models.TASK_TAGS
    assert tuple(row[-3:] for row in matrix) == (gross, target, target, holding)
    values = dict(zip(models.LGB_NAMES, matrix[0], strict=True))
    assert values["minute60__price_fraction"] == 0.25
    assert values["minute60__preopening_fraction"] == 0.75
    assert values["minute60__return_pct"] == 0.5
    assert values["minute60__span_pct"] == 2.0
    assert values["minute60__volume_fraction"] == 0.0


def test_one_class_target_is_recorded_and_removed_from_fit():
    entry = _input()
    training = (models.TrainingRow(entry, gross30=Fraction(1, 100), target_first=False),
                models.TrainingRow(entry))
    inputs, labels, manifest = models._training("LGB_CHART", training, 1)
    assert manifest["target_first_false_true_rows"] == [1, 0]
    assert manifest["target_first_support"] == "one_class"
    assert manifest["task_rows"] == [1, 0, 0]
    assert labels == ((1.0, None, None), (None, None, None))
    rows, targets = models._lgb_training(inputs, labels)
    assert targets == (1.0,)
    assert rows[0][-3:] == models.TASK_TAGS[0]


@pytest.mark.parametrize("train,arm,reason", [
    (models.TrainingRow(_input(), target_first=0), "LGB_CHART", "adjudicated boolean"),
    (models.TrainingRow(_input(), gross30=0.1), "LGB_CHART", "exact returns"),
    (models.TrainingRow(_input(), hold_advantage=Fraction(1)), "LGB_CHART", "task disagrees"),
    (models.TrainingRow(_input(peer=True), gross30=Fraction(1)), "TCN_CHART", "minute values"),
    (models.TrainingRow(_input(), gross30=Fraction(1)), "TCN_CHART_PEER", "minute values"),
    (models.TrainingRow(_input()), "TCN_CHART", "no supported task"),
    (models.TrainingRow(_input(), target_first=False), "TCN_CHART", "no supported task"),
])
def test_scope_and_target_validation_precede_any_native_fit(train, arm, reason):
    with pytest.raises(ValueError, match=reason):
        models.fit(arm, (train,), binding={"fixture": "validation"}, fold_index=1)


def test_single_lightgbm_fit_clipping_unsupported_and_independent_replay(monkeypatch):
    calls = []
    np = SimpleNamespace(float32=object(),
                         asarray=lambda values, dtype: tuple(unpack(">f", pack(">f", x))[0] for x in values))
    native = SimpleNamespace()
    booster = SimpleNamespace(dump_model=lambda: {"fixture": "tree"})
    monkeypatch.setattr(models.numeric, "_libraries", lambda learner: (np, native, {"fixture": learner}))

    def fit_once(rows, labels, names, library, backend):
        calls.append("fit")
        assert library is np and backend is native and names == models.LGB_NAMES
        assert len(rows) == len(labels) == 3
        return {"native_training_labels_sha256": models.numeric._vector_hash(np.asarray(labels, np.float32))}

    monkeypatch.setattr(models.numeric, "_fit_lightgbm_matrix", fit_once)
    monkeypatch.setattr(models.numeric, "_reload_lightgbm_matrix", lambda *_: booster)

    def scores(rows):
        assert tuple(row[-3:] for row in rows) == models.TASK_TAGS[:2]
        return 0.43, 1.25

    def replay(dump, rows, width):
        calls.append("independent")
        assert dump == booster.dump_model() and width == len(models.LGB_NAMES)
        return scores(rows)

    monkeypatch.setattr(models.numeric, "_predict_lightgbm_matrix", lambda _, rows, np: scores(rows))
    monkeypatch.setattr(models.numeric, "_scalar_lightgbm_replay", replay)
    arm, binding = "LGB_CHART", {"fold": "fixture"}
    entry, hold = _input(), _input(holding=True)
    training = (models.TrainingRow(entry, gross30=Fraction(1, 300), target_first=False),
                models.TrainingRow(entry, target_first=True))
    artifact = models.fit(arm, training, binding=binding, fold_index=2)
    output = models.predict(arm, (entry, hold), artifact, binding=binding)
    assert output == (models.ModelPrediction(0.43, 1.0, None, 1.25, True),
                      models.ModelPrediction(None, None, None, None, False))
    receipt = models.verify(arm, training, (entry, hold), artifact, output, binding=binding, fold_index=2)
    assert receipt["target_first_clipped_rows"] == 1
    assert receipt["threshold_ambiguous_rows"] == {"gross33": [], "gross43": [0], "hold_zero": []}
    assert calls == ["fit", "independent"]
    altered = (replace(training[0], gross30=Fraction(1, 300) + Fraction(1, 10**40)), training[1])
    with pytest.raises(ValueError, match="training manifest"):
        models.verify(arm, altered, (entry, hold), artifact, output, binding=binding, fold_index=2)


def test_tcn_one_fit_deterministic_seed_masks_and_tolerance_receipt(monkeypatch):
    calls = []
    torch = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setattr(models, "_torch_runtime", lambda native: {"fixture": "cpu"})

    def fit_once(native, inputs, labels, width, seed):
        calls.append("fit")
        assert native is torch and width == 18 and seed == models.SEED + 3
        assert labels == ((1.0, 1.0, None), (None, 0.0, None), (None, None, 0.0))
        return {"fixture": "coefficients"}

    def raw(native, inputs, artifact, width, replay):
        calls.append("independent" if replay else "native")
        assert width == 18 and artifact["model"] == {"fixture": "coefficients"}
        return ((0.3300001 if replay else 0.3299999, 0.0, 999.0), (999.0, 999.0, 0.0))

    monkeypatch.setattr(models, "_tcn_fit", fit_once)
    monkeypatch.setattr(models, "_tcn_raw", raw)
    arm, binding = "TCN_CHART_PEER", {"fold": "fixture"}
    entry, hold = _input(peer=True), _input(peer=True, holding=True)
    training = (models.TrainingRow(entry, gross30=Fraction(1, 100), target_first=True),
                models.TrainingRow(entry, target_first=False),
                models.TrainingRow(hold, hold_advantage=Fraction(0)))
    artifact = models.fit(arm, training, binding=binding, fold_index=3)
    output = models.predict(arm, (entry, hold), artifact, binding=binding)
    assert output == (models.ModelPrediction(0.3299999, 0.5, None, 0.0, False),
                      models.ModelPrediction(None, None, 0.0, None, False))
    receipt = models.verify(arm, training, (entry, hold), artifact, output, binding=binding, fold_index=3)
    assert receipt["threshold_ambiguous_rows"] == {"gross33": [0], "gross43": [], "hold_zero": [1]}
    assert calls == ["fit", "native", "independent"]
    with pytest.raises(ValueError, match="artifact contract or checksum"):
        models.predict(arm, (entry,), {**artifact, "channels": []}, binding=binding)
    with pytest.raises(ValueError, match="independent replay score"):
        models.verify(arm, training, (entry, hold), artifact, (replace(output[0], gross30_pct=1.0), output[1]),
                      binding=binding, fold_index=3)


def test_tcn_cpu_runtime_refuses_visible_accelerator_and_warn_only(monkeypatch):
    float32 = object()
    torch = SimpleNamespace(
        __version__="2.7.0+cu128", float32=float32, get_num_threads=lambda: 4,
        get_num_interop_threads=lambda: 1, are_deterministic_algorithms_enabled=lambda: True,
        is_deterministic_algorithms_warn_only_enabled=lambda: False,
        is_autocast_enabled=lambda device: False, get_default_dtype=lambda: float32,
        get_float32_matmul_precision=lambda: "highest",
        backends=SimpleNamespace(mkldnn=SimpleNamespace(enabled=True, deterministic=False)),
    )
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    with pytest.raises(ValueError, match="pinned deterministic"):
        models._torch_runtime(torch)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    assert models._torch_runtime(torch)["device"] == "cpu"
    torch.is_deterministic_algorithms_warn_only_enabled = lambda: True
    with pytest.raises(ValueError, match="pinned deterministic"):
        models._torch_runtime(torch)
