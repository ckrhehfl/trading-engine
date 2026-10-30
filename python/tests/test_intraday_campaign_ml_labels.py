"""Invented fixed-calendar/train-view checks; no data reads or numerical fits."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from fractions import Fraction
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from research import intraday_campaign_ml_labels as labels
from research import intraday_campaign_ml_label_verifier as verifier


def source_dates():
    result, current = [], date(2025, 9, 4)
    while len(result) < 257:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += timedelta(days=1)
    return tuple(result)


def feature_label_rows(count=120, distinct=40):
    reference = source_dates()
    features, outcomes = {}, {}
    for index in range(count):
        day = reference[index % distinct]
        key = ("D+1", "EA-A" if index % 2 == 0 else "EA-B", index, day, f"KRX:{index + 1:06d}")
        end = labels.readiness._opening(day) + timedelta(minutes=140)
        features[key] = (Fraction(index, 100),) * 10
        outcomes[key] = labels.ValidatedLabel(key, "known", "time_cap", Fraction(-33, 10000), end,
                                               end + timedelta(minutes=3), None, {})
    key = ("D+1", "EA-A", count, reference[100], "KRX:999999")
    features[key] = (Fraction(1),) * 10
    return features, outcomes


def test_exact_257_100_157_geometry_and_evaluation_week_denominators():
    calendar = labels.fixed_calendar(source_dates())
    assert len(calendar.source_reference_sessions) == 257
    assert len(calendar.initial_training_sessions) == 100
    assert len(calendar.evaluation_reference_sessions) == 157
    assert [len(fold.test_sessions) for fold in calendar.folds] == [32, 32, 31, 31, 31]
    assert [len(fold.training_sessions) for fold in calendar.folds] == [100, 132, 164, 195, 226]
    assert tuple(day for fold in calendar.folds for day in fold.test_sessions) == calendar.evaluation_reference_sessions
    assert sum(calendar.evaluation_week_session_counts.values()) == 157
    assert len(calendar.evaluation_reference_weeks) < len(calendar.source_reference_weeks)
    first = calendar.evaluation_reference_weeks[0]
    assert calendar.evaluation_boundary_week_flags[first]["source_sessions_omitted_by_initial_training"] == 3
    assert calendar.evaluation_boundary_week_flags[first]["partial_calendar_week"] is True
    features, outcomes = {}, {}
    for index in range(1, 6):
        view = labels.prepare_fold(calendar, index, "D+1", features, outcomes)
        assert verifier.verify_fold_training_view(view, calendar, features, outcomes)["evaluation_labels_consumed"] is False


@pytest.mark.parametrize("reference", [source_dates()[:256], (*source_dates(), "2027-01-01"),
    (*source_dates()[:-1], source_dates()[0]), tuple(reversed(source_dates()))])
def test_dynamic_calendar_reduction_and_reordering_are_refused(reference):
    with pytest.raises(labels.MLLabelsRefusal, match="257"):
        labels.fixed_calendar(reference)


def test_exact_original_order_and_post_purge_minimum_are_not_feature_readiness_counts():
    calendar = labels.fixed_calendar(source_dates())
    features, outcomes = feature_label_rows(120, 40)
    view = labels.prepare_fold(calendar, 1, "D+1", features, outcomes)
    assert view.status == "ready"
    assert view.train_row_keys == tuple(outcomes)
    assert len(view.X) == len(view.y) == 120
    assert view.counts["distinct_original_sessions"] == 40
    verifier.verify_fold_training_view(view, calendar, features, outcomes)
    for key in list(outcomes)[:21]:
        outcomes[key] = replace(outcomes[key], status="unknown", reason="required_path_missing", y=None,
                                label_end_at=None, available_at=None)
    unavailable = labels.prepare_fold(calendar, 1, "D+1", features, outcomes)
    assert unavailable.counts["training_candidates"] == 120
    assert unavailable.counts["observed_labeled_candidates"] == 99
    assert unavailable.status == "unavailable"
    assert "fewer_than_100" in unavailable.reason
    assert unavailable.test_row_keys == view.test_row_keys
    verifier.verify_fold_training_view(unavailable, calendar, features, outcomes)


def test_thirty_dates_guard_is_applied_after_features_known_labels_and_purge():
    calendar = labels.fixed_calendar(source_dates())
    features, outcomes = feature_label_rows(100, 29)
    view = labels.prepare_fold(calendar, 1, "D+1", features, outcomes)
    assert view.counts["observed_labeled_candidates"] == 100
    assert view.counts["distinct_original_sessions"] == 29
    assert view.status == "unavailable" and "fewer_than_30" in view.reason
    assert "fewer_than_100" not in view.reason


@pytest.mark.parametrize("offset,expected", [(-4, ()), (-3, ("label_availability_reaches_test_opening",)),
    (0, ("label_end_reaches_test_opening", "label_availability_reaches_test_opening"))])
def test_purge_end_or_availability_equality(offset, expected):
    calendar = labels.fixed_calendar(source_dates())
    features, outcomes = feature_label_rows()
    key = next(iter(outcomes))
    end = calendar.folds[0].first_test_opening + timedelta(minutes=offset)
    outcomes[key] = replace(outcomes[key], label_end_at=end, available_at=end + timedelta(minutes=3))
    view = labels.prepare_fold(calendar, 1, "D+1", features, outcomes)
    assert dict(view.purge_reasons).get(key, ()) == expected
    assert (key in view.train_row_keys) is (not expected)
    verifier.verify_fold_training_view(view, calendar, features, outcomes)


def frozen_ml_json_writer():
    """Load only authenticated fixture source; do not generate any input data."""
    path = Path(__file__).resolve().parents[2] / "var/ec-ml-fixtures.py"
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "422ee12a12748bef3b19b677c34472dbc1804065e2ee1d04290bf058d37d6b3c"
    spec = importlib.util.spec_from_file_location("invented_ml_purge_json_writer", path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return module.store_json


def restore_fold_view(stored):
    """Decode the writer's exact public JSON shape, without numerical adapters."""
    fields = dict(stored)
    fields["cutoff"] = datetime.fromisoformat(fields["cutoff"])
    for name in ("feature_order", "train_sessions"):
        fields[name] = tuple(fields[name])
    for name in ("train_row_keys", "test_row_keys"):
        fields[name] = tuple(tuple(key) for key in fields[name])
    for name in ("X", "test_X"):
        fields[name] = tuple(tuple(labels._fraction(value) for value in row) for row in fields[name])
    fields["y"] = tuple(labels._fraction(value) for value in fields["y"])
    fields["counts"] = labels._freeze(fields["counts"])
    fields["purge_reasons"] = tuple((tuple(key), tuple(reasons)) for key, reasons in fields["purge_reasons"])
    return labels.FoldTrainingView(**fields)


@pytest.mark.parametrize("candidates", [101, 102])
def test_nonempty_ordered_purge_coordinates_roundtrip_actual_ml_writer(tmp_path, candidates):
    from research.intraday_campaign_ml_verifier import _plain, typed_sha256
    calendar = labels.fixed_calendar(source_dates())
    features, outcomes = feature_label_rows(candidates, 34)
    first, second, *_ = outcomes
    cutoff = calendar.folds[0].first_test_opening
    outcomes[first] = replace(outcomes[first], label_end_at=cutoff, available_at=cutoff + timedelta(minutes=3))
    outcomes[second] = replace(outcomes[second], label_end_at=cutoff - timedelta(minutes=3), available_at=cutoff)
    # Deliberately preserve a source order that is different from sorting keys.
    features = {second: features[second], first: features[first],
                **{key: value for key, value in features.items() if key not in (first, second)}}
    view = labels.prepare_fold(calendar, 1, "D+1", features, outcomes)
    expected = ((second, ("label_availability_reaches_test_opening",)),
                (first, ("label_end_reaches_test_opening", "label_availability_reaches_test_opening")))
    assert view.purge_reasons == expected
    assert type(view.purge_reasons) is tuple
    assert view.counts["purged"] == 2
    assert view.counts["observed_labeled_candidates"] == candidates - 2
    assert view.counts["distinct_original_sessions"] == 34
    assert view.status == ("ready" if candidates == 102 else "unavailable")
    baseline_features = {key: value for key, value in features.items() if key not in (first, second)}
    baseline = labels.prepare_fold(calendar, 1, "D+1", baseline_features, outcomes)
    assert (view.train_row_keys, view.X, view.y, view.test_row_keys, view.test_X,
            view.status, view.reason) == (baseline.train_row_keys, baseline.X, baseline.y,
                baseline.test_row_keys, baseline.test_X, baseline.status, baseline.reason)
    assert baseline.purge_reasons == ()
    verifier.verify_fold_training_view(view, calendar, features, outcomes)
    destination = tmp_path / "invented-nonempty-purged-view.json"
    pin, size = frozen_ml_json_writer()(destination, view, 1024 * 1024, lambda stage: None)
    raw = destination.read_bytes()
    assert (pin, size) == (hashlib.sha256(raw).hexdigest(), len(raw))
    stored = json.loads(raw)
    assert stored["purge_reasons"] == [[list(key), list(reasons)] for key, reasons in expected]
    restored = restore_fold_view(stored)
    assert restored == view
    assert typed_sha256(_plain(restored)) == typed_sha256(_plain(view))
    verifier.verify_fold_training_view(restored, calendar, features, outcomes)
    with pytest.raises(verifier.MLLabelVerificationRefusal, match="training_view_mismatch"):
        verifier.verify_fold_training_view(replace(view, purge_reasons=tuple(reversed(view.purge_reasons))),
                                           calendar, features, outcomes)
    with pytest.raises(verifier.MLLabelVerificationRefusal, match="ordered_purge"):
        verifier.verify_fold_training_view(replace(view, purge_reasons=dict(view.purge_reasons)),
                                           calendar, features, outcomes)


def test_empty_purge_coordinates_are_an_empty_json_sequence(tmp_path):
    calendar = labels.fixed_calendar(source_dates())
    features, outcomes = feature_label_rows()
    view = labels.prepare_fold(calendar, 1, "D+1", features, outcomes)
    assert view.purge_reasons == ()
    destination = tmp_path / "invented-empty-purged-view.json"
    frozen_ml_json_writer()(destination, view, 1024 * 1024, lambda stage: None)
    stored = json.loads(destination.read_bytes())
    assert stored["purge_reasons"] == []
    assert restore_fold_view(stored) == view


def test_evaluation_label_missingness_is_not_queried_or_used_for_test_population():
    calendar = labels.fixed_calendar(source_dates())
    features, outcomes = feature_label_rows()
    test_key = next(reversed(features))
    class NoTestLabelAccess(dict):
        def get(self, key, *args):
            if key == test_key:
                raise AssertionError("evaluation_label_access")
            return super().get(key, *args)
    view = labels.prepare_fold(calendar, 1, "D+1", features, NoTestLabelAccess(outcomes))
    verifier.verify_fold_training_view(view, calendar, features, NoTestLabelAccess(outcomes))
    outcomes[test_key] = labels.ValidatedLabel(test_key, "unknown", "exit_open_missing", None, None, None, None, {})
    assert labels.prepare_fold(calendar, 1, "D+1", features, outcomes) == view
    assert view.test_row_keys == (test_key,)
    assert view.status == "ready"


def test_only_strictly_past_dates_expand_and_timing_policies_are_not_pooled():
    reference = source_dates()
    calendar = labels.fixed_calendar(reference)
    features, outcomes = feature_label_rows()
    key = next(reversed(features))
    end = labels.readiness._opening(reference[100]) + timedelta(minutes=140)
    outcomes[key] = labels.ValidatedLabel(key, "known", "time_cap", Fraction(1, 100), end, end + timedelta(minutes=3), None, {})
    other = ("D+2", "EA-A", 999, reference[0], "KRX:888888")
    features[other] = (Fraction(1),) * 10
    outcomes[other] = replace(outcomes[key], row_key=other)
    first, second, *_ = labels.build_fold_views(reference, features, outcomes, "D+1")
    assert key not in first.train_row_keys and key in first.test_row_keys
    assert key in second.train_row_keys and key not in second.test_row_keys
    assert other not in second.train_row_keys
    verifier.verify_fold_training_view(second, calendar, features, outcomes)


def test_missing_feature_is_not_zero_and_independent_membership_tampering_fails():
    calendar = labels.fixed_calendar(source_dates())
    features, outcomes = feature_label_rows()
    key = next(iter(features))
    features[key] = (*features[key][:9], None)
    view = labels.prepare_fold(calendar, 1, "D+1", features, outcomes)
    assert key not in view.train_row_keys and view.counts["feature_unknown"] == 1
    assert features[key][-1] is None
    verifier.verify_fold_training_view(view, calendar, features, outcomes)
    with pytest.raises(verifier.MLLabelVerificationRefusal, match="training_view"):
        verifier.verify_fold_training_view(replace(view, train_row_keys=tuple(reversed(view.train_row_keys))),
                                          calendar, features, outcomes)


@pytest.mark.parametrize("raw", [{"numerator": "-2", "denominator": "4"},
    {"numerator": "-0", "denominator": "1"}, {"numerator": "+1", "denominator": "1"},
    {"numerator": "01", "denominator": "1"}, {"numerator": "1", "denominator": "-1"},
    {"numerator": 1, "denominator": "1"}, "-1/2"])
def test_signed_fraction_decoder_rejects_noncanonical_forms(raw):
    with pytest.raises(labels.MLLabelsRefusal, match="fraction"):
        labels._fraction(raw)


def test_signed_fraction_decoder_preserves_exact_zero_and_negative_targets():
    assert labels._fraction({"numerator": "-33", "denominator": "10000"}) == Fraction(-33, 10000)
    assert labels._fraction({"numerator": "0", "denominator": "1"}) == 0
