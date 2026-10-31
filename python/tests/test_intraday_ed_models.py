"""Invented ED rows only: no price archive, actual label, runner or IO."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from fractions import Fraction
import builtins
import importlib
import importlib.util
import math

import pytest

from research import intraday_campaign_ml_models as old
from research import intraday_ed_models as models


def calendar():
    days, day = [], date(2025, 1, 2)
    while len(days) < 257:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return models.fixed_calendar(days)


def features(value=1, kinds=("NO_EVENT",)):
    return ((float(value),) + (0.25,) * 190
            + tuple(float(kind in kinds) for kind in models.EVENT_TYPE_NAMES)
            + ((1.0,) + (0.0,) * 10 if kinds == ("NO_EVENT",) else (0.0, 1.0) + (0.0,) * 9))


def example(index, *, scenario="S1", session=None, kind=("NO_EVENT",), status="known_traded"):
    day = calendar().source_reference_sessions[index // 3 if session is None else session]
    decision = datetime.fromisoformat(day + "T10:00:00+09:00")
    return models.EDTrainingExample(f"invented-{index}-{scenario}", f"KRX:{index % 3:06d}", scenario,
        day, decision, features(index, kind), True, kind, status,
        Fraction(index - 60, 10000) if status == "known_traded" else Fraction(0),
        decision + timedelta(minutes=61), decision + timedelta(minutes=64))


def examples():
    return tuple(example(index) for index in range(120)) + tuple(
        example(index, session=100 + index - 120) for index in range(120, 123))


def view(rows=None, *, without_filing=False):
    return models.prepare_ed_fold(calendar(), 1, "S1", examples() if rows is None else rows,
                                  without_filing=without_filing)


def binding(*, without_filing=False):
    return models.EDModelBinding("a" * 64, "b" * 64, "c" * 64, "d" * 64, 1, "S1",
                                  "without_filing" if without_filing else "full")


@pytest.fixture(autouse=True)
def threads(monkeypatch):
    for key in old.THREAD_VARIABLES:
        monkeypatch.setenv(key, "1")


def test_exact_calendar_training_order_and_full_test_date_denominator():
    result = view(tuple(reversed(examples())))
    assert result.status == "ready" and result.reason is None
    assert len(result.X) == len(result.y) == 120
    assert result.train_ids == tuple(f"invented-{index}-S1" for index in range(120))
    assert result.counts["known_traded"] == 120
    assert result.counts["known_traded_sessions"] == 40
    assert result.training_group_counts["NO_EVENT"] == 120
    assert result.training_group_counts["NO_EVENT__sessions"] == 40
    assert result.training_group_counts["ORIGINAL_ANNUAL"] == 0
    assert len(result.test_ids) == 3 and len(result.test_sessions) == 32
    assert tuple(len(fold.test_sessions) for fold in calendar().folds) == (32, 32, 31, 31, 31)
    assert len(calendar().evaluation_reference_sessions) == 157


def test_test_label_status_maturity_and_future_labels_are_never_read():
    original = examples()
    changed = original[:120] + tuple(replace(row, label_status=object(), primary_label=object(),
        label_end_at="not a clock", mature_at=object(), reason="future") for row in original[120:])
    assert view(changed) == view(original)
    later = replace(example(600, session=200), primary_label=object(), label_status=object())
    assert view(changed + (later,)) == view(original)
    assert not hasattr(view().testing[0], "y")


def test_purge_uses_both_strict_cutoffs_and_unknowns_retain_reasons():
    cutoff = calendar().folds[0].first_test_opening
    rows = list(examples())
    rows[0] = replace(rows[0], label_end_at=cutoff, mature_at=cutoff + timedelta(minutes=3))
    rows[1] = replace(rows[1], mature_at=cutoff)
    rows[2] = replace(rows[2], features=None, label_status=object(), primary_label=object())
    rows[3] = replace(rows[3], label_status="unknown", primary_label=None,
                      label_end_at=None, mature_at=None, reason="invented_missing_exit")
    result = view(rows)
    excluded = dict(result.exclusions)
    assert result.counts["purged"] == 2
    assert excluded[rows[0].parent_id] == ("label_end_reaches_test_opening", "label_maturity_reaches_test_opening")
    assert excluded[rows[1].parent_id] == ("label_maturity_reaches_test_opening",)
    assert excluded[rows[2].parent_id] == ("training_common_input_unknown",)
    assert excluded[rows[3].parent_id] == ("training_label_unknown", "invented_missing_exit")
    assert len(result.training) == 116


def test_declined_entry_zero_labels_can_train_but_never_satisfy_filled_minimum():
    rows = tuple(example(index, status="known_declined_entry" if index < 21 else "known_traded")
                 for index in range(120))
    result = view(rows)
    assert result.status == "unavailable"
    assert len(result.y) == 120 and result.counts["known_declined_entry"] == 21
    assert result.counts["known_traded"] == 99
    assert "100_known_traded" in result.reason
    ready = view(tuple(replace(row, label_status="known_traded") if index == 20 else row
                       for index, row in enumerate(rows)))
    assert ready.status == "ready"
    assert ready.counts["known_declined_entry"] == 20
    bad = replace(rows[0], primary_label=Fraction(1, 10))
    with pytest.raises(ValueError, match="policy_cash_zero"):
        view((bad,) + rows[1:])


def test_minimum_30_real_filled_sessions_is_distinct_from_count():
    rows = []
    for index in range(120):
        item = example(index, session=index % 29)
        rows.append(replace(item, symbol=f"invented-{index:06d}"))
    result = view(rows)
    assert result.counts["known_traded"] == 120
    assert result.counts["known_traded_sessions"] == 29
    assert "30_known_traded_training_sessions" in result.reason


def test_without_filing_removes_exact_last15_bits_on_same_rows_labels_schedule():
    rows = list(examples())
    rows[4] = replace(rows[4], features=None)
    full, removed = view(rows), view(rows, without_filing=True)
    assert full.train_ids == removed.train_ids and full.y == removed.y
    assert full.test_ids == removed.test_ids and full.fold == removed.fold
    assert full.counts == removed.counts and full.exclusions == removed.exclusions
    assert full.training_group_counts == removed.training_group_counts
    assert full.feature_names == models.FEATURE_NAMES and len(full.feature_names) == 206
    assert removed.feature_names == models.FEATURE_NAMES[:191]
    assert all(left[:191] == right for left, right in zip(full.X, removed.X, strict=True))
    assert all(left[:191] == right for left, right in zip(full.test_X, removed.test_X, strict=True))


def test_missing_context_remains_outside_both_modes_and_scenarios_stay_separate():
    rows = list(examples())
    rows[120] = replace(rows[120], features=None, event_types=())
    other = tuple(example(index, scenario="S2") for index in range(120))
    for removed in (False, True):
        result = view(tuple(rows) + other, without_filing=removed)
        assert result.counts["known_traded"] == 120
        assert result.testing[0].features is None and result.test_ids[0] == rows[120].parent_id
        assert result.counts["test_input_unknown"] == 1
    with pytest.raises(ValueError, match="duplicate_ED_parent"):
        view(tuple(rows) + (rows[0],))
    with pytest.raises(ValueError, match="one_ED_parent"):
        view(tuple(rows) + (replace(rows[0], parent_id="other-id"),))


def test_unavailable_fold_covers_zero_parent_dates_without_numerical_import(monkeypatch):
    def forbidden(*args):
        raise AssertionError("unavailable fold attempted numerical work")
    monkeypatch.setattr(old, "_libraries", forbidden)
    fold = view(())
    result = models.fit_ed_lightgbm(fold, binding())
    batch = models.predict_ed_lightgbm(result, fold)
    assert result.status == "unavailable" and result.artifact is None
    assert batch.rows == () and batch.unavailable_sessions == fold.test_sessions
    assert len(batch.unavailable_sessions) == 32
    receipt = models.verify_ed_model(result, fold, batch, binding=binding())
    assert receipt["verified"] and not receipt["model_fitted"]
    assert receipt["unavailable_sessions"] == fold.test_sessions


@pytest.mark.parametrize("bad", [(1.0,) * 10, (1.0,) * 191, (1.0,) * 207,
                                      features(float("nan")), features(float("inf"))])
def test_full_206_schema_and_finite_boundary_are_not_loosened_for_ablation(bad):
    for removed in (False, True):
        with pytest.raises(ValueError):
            view((replace(example(0), features=bad),), without_filing=removed)


def test_fixed_filing_vocabulary_and_categorical_payload_must_agree():
    for bad in (replace(example(0), event_types=("other",)),
                replace(example(0), event_types=("ORIGINAL_ANNUAL",)),
                replace(example(0), features=features(1, ("NO_EVENT", "ORIGINAL_ANNUAL")))):
        with pytest.raises(ValueError):
            view((bad,))
    observed = example(120, session=100, kind=("ORIGINAL_ANNUAL", "ORIGINAL_QUARTER"))
    assert view(examples()[:120] + (observed,)).testing[0].features is not None


def test_old_ten_feature_and_artifact_guards_still_reject_ed_schema_before_import(monkeypatch):
    def forbidden(*args):
        raise AssertionError("old guard accepted an ED matrix")
    monkeypatch.setattr(old, "_libraries", forbidden)
    with pytest.raises(ValueError, match="ten-feature"):
        old.fit_model("lightgbm", [features()], [0], feature_names=models.FEATURE_NAMES, binding={"x": "y"})
    with pytest.raises(ValueError, match="ten columns"):
        old.fit_model("lightgbm", [features()], [0], feature_names=old.FEATURE_NAMES, binding={"x": "y"})
    with pytest.raises(ValueError, match="scientific_runtime_binding"):
        models.fit_ed_lightgbm(view(), replace(binding(), context_mode="without_filing"))
    with pytest.raises(ValueError, match="fold_geometry"):
        models.fit_ed_lightgbm(replace(view(), fold=replace(view().fold, training_sessions=())), binding())


def test_numeric_module_import_remains_lean(monkeypatch):
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in ("numpy", "scipy", "lightgbm", "torch"):
            raise AssertionError("numerical backend imported on load")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    importlib.reload(models)


native = pytest.mark.skipif(importlib.util.find_spec("lightgbm") is None, reason="native LightGBM unavailable")


@native
def test_native_full_schema_serialized_reload_scalar_replay_and_unseen_type(monkeypatch):
    rows = examples()[:120] + (example(120, session=100, kind=("ORIGINAL_ANNUAL",)),
                              replace(example(121, session=101), features=None))
    fold = view(rows)
    result = models.fit_ed_lightgbm(fold, binding())
    batch = models.predict_ed_lightgbm(result, fold)
    assert batch.rows[0].status == "known" and batch.rows[0].unseen_types == ("ORIGINAL_ANNUAL",)
    assert batch.rows[0].extrapolation and math.isfinite(float.fromhex(batch.rows[0].prediction_hex))
    assert batch.rows[0].accepted == (float.fromhex(batch.rows[0].prediction_hex) > 0)
    assert batch.rows[1].status == "unavailable" and batch.rows[1].accepted is None
    payload = old._copy(result.artifact.payload)
    assert payload["parameters"] == old.lightgbm_parameter_manifest()
    assert payload["model"]["current_iteration"] == 100
    assert payload["model"]["native_training_labels_sha256"] != payload["train_labels_sha256"]
    with pytest.raises(TypeError):
        result.artifact.payload["model"]["current_iteration"] = 2
    restored = replace(result, artifact=models.EDModelArtifact(payload))
    assert models.predict_ed_lightgbm(restored, fold) == batch
    import lightgbm
    def forbidden(*args, **kwargs):
        raise AssertionError("verification refitted or called producer prediction")
    monkeypatch.setattr(lightgbm, "train", forbidden)
    monkeypatch.setattr(models, "fit_ed_lightgbm", forbidden)
    monkeypatch.setattr(models, "predict_ed_lightgbm", forbidden)
    receipt = models.verify_ed_model(restored, fold, batch, binding=binding())
    assert receipt["verified"] and receipt["prediction_count"] == 1
    assert "scalar" in receipt["trusted_external_boundary"]
    assert receipt["native_replay_predictions_sha256"] == receipt["predictions_sha256"]
    wrong = replace(batch, rows=(replace(batch.rows[0], accepted=not batch.rows[0].accepted), batch.rows[1]))
    with pytest.raises(ValueError, match="strict_positive"):
        models.verify_ed_model(restored, fold, wrong, binding=binding())
    with pytest.raises(ValueError, match="external_scientific"):
        models.verify_ed_model(restored, fold, batch, binding=replace(binding(), snapshot_sha256="e" * 64))


@native
@pytest.mark.parametrize("without_filing", [False, True])
def test_genuine_native_constant_model_is_one_fit_and_zero_does_not_accept(monkeypatch, without_filing):
    import lightgbm
    original, calls = lightgbm.train, []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(lightgbm, "train", counted)
    rows = tuple(replace(row, features=features(), primary_label=Fraction(0)) for row in examples())
    fold = view(rows, without_filing=without_filing)
    result = models.fit_ed_lightgbm(fold, binding(without_filing=without_filing))
    assert calls == [1] and result.artifact.payload["model"]["current_iteration"] == 1
    assert len(result.artifact.payload["feature_names"]) == (191 if without_filing else 206)
    batch = models.predict_ed_lightgbm(result, fold)
    assert all(row.prediction_hex == (0.0).hex() and row.accepted is False for row in batch.rows)
    assert models.verify_ed_model(result, fold, batch, binding=binding(without_filing=without_filing))["verified"]
    assert calls == [1]


@native
@pytest.mark.parametrize("field", ["native_training_labels_sha256", "model_string_sha256", "current_iteration"])
def test_rehashed_model_tampering_is_rejected(field):
    fold = view()
    result = models.fit_ed_lightgbm(fold, binding())
    payload = old._copy(result.artifact.payload)
    payload["model"][field] = 99 if field == "current_iteration" else "0" * 64
    payload["artifact_sha256"] = old._digest({key: value for key, value in payload.items() if key != "artifact_sha256"})
    with pytest.raises(ValueError):
        models.predict_ed_lightgbm(replace(result, artifact=models.EDModelArtifact(payload)), fold)


@native
def test_scalar_tree_traversal_is_independent_of_native_prediction(monkeypatch):
    fold = view()
    result = models.fit_ed_lightgbm(fold, binding())
    batch = models.predict_ed_lightgbm(result, fold)
    original = old._predict_lightgbm_matrix
    def changed(booster, rows, np):
        return tuple(value + 0.01 for value in original(booster, rows, np))
    monkeypatch.setattr(old, "_predict_lightgbm_matrix", changed)
    corrupted = replace(batch, rows=tuple(replace(row,
        prediction_hex=(float.fromhex(row.prediction_hex) + 0.01).hex(),
        accepted=float.fromhex(row.prediction_hex) + 0.01 > 0) for row in batch.rows))
    with pytest.raises(ValueError, match="scalar_prediction_replay|replay_sign"):
        models.verify_ed_model(result, fold, corrupted, binding=binding())
