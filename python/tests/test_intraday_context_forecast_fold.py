"""Invented EI six-arm orchestration, with tiny native replay and no market IO."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256

import pytest

from research import intraday_context_forecast_fold as fold_module
from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_context_model_inputs import encode, training_contract
from research.intraday_context_snapshot import QuestionLabel, project_coordinate, question_digest
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs
from research.intraday_ed_sources import TargetCoordinate
from research.intraday_scenario_labels import label_horizons


KST = timezone(timedelta(hours=9))
DAYS = ("2031-02-12", "2031-02-13", "2031-02-14")
BASE = ((100, 102, 98, 100), (100, 103, 99, 102), (102, 104, 100, 103),
        (103, 116, 102, 114), (114, 114, 111, 112), (112, 116, 111, 114))
BELOW, TOUCH, BREAK = (114, 114, 111, 112), (112, 116, 111, 114), (114, 119, 113, 118)


def opening(day):
    return datetime.fromisoformat(day).replace(hour=9, tzinfo=KST)


def inputs(day, symbol="invented", bars=None):
    bars = bars or (*BASE, BELOW, TOUCH, BREAK, BELOW, TOUCH, BREAK, TOUCH, TOUCH,
                    BELOW, TOUCH, BELOW, TOUCH, TOUCH, TOUCH)
    start = opening(day)
    rows = {start + index * MINUTE: Minute(symbol, start + index * MINUTE, start + (index + 3) * MINUTE,
            tuple(map(Decimal, bar)), Decimal(10)) for number, bar in enumerate(bars)
            for index in range(5 * number, 5 * number + 5)}
    return SessionInputs(symbol, start, start + 100 * MINUTE, rows)


def fixture(extra=False):
    targets, coordinates, sessions = [], [], {}
    for day in DAYS:
        for rank, symbol in enumerate(("invented", "other") if extra else ("invented",), 1):
            membership = tuple(TargetCoordinate(scenario, symbol, day, rank, 100, "observed", b"{}")
                               for scenario in ("S1", "S2"))
            value = inputs(day, symbol)
            targets.extend(membership)
            coordinates.append(project_coordinate(membership, value))
            sessions[(day, symbol)] = value
    fold = ChronologicalFold(1, (0, 1), (1, 2), DAYS[:1], DAYS[1:2], opening(DAYS[1]))
    return tuple(targets), tuple(coordinates), sessions, fold


def label_reader(sessions, coordinates, calls):
    lookup = {(coordinate.session, coordinate.symbol, question_digest(row.question)): row.question
              for coordinate in coordinates for row in coordinate.questions}

    def read(day, symbol, key):
        calls.append((day, symbol, key))
        assert day == DAYS[0], "evaluation or unrelated source outcome was requested"
        session = sessions[(day, symbol)]
        question = lookup[(day, symbol, key)]
        cutoff = session.closing + 2 * MINUTE
        return QuestionLabel(question, cutoff, "observed",
            label_horizons(question.path, question.start_close, session, cutoff_at=cutoff))

    return read


def adapters(events, *, broken=None):
    def fit(kind, prepared):
        events.append(("fit", kind, prepared))
        artifact = {"kind": kind, "training": training_contract(prepared),
                    "artifact_sha256": sha256((kind + prepared.input_sha256).encode()).hexdigest()}
        if broken == "training":
            artifact["training"] = {**artifact["training"], "roster_sha256": "0" * 64}
        return artifact

    def predict(kind, artifact, query):
        events.append(("predict", kind, query))
        if broken == "mutated_artifact":
            artifact["changed"] = True
        result = tuple((0.1,) * 10 for _ in query.rows)
        if broken == "length":
            return result[:-1]
        if broken == "mass":
            return tuple((0.2,) * 10 for _ in query.rows)
        return result

    def verify(artifact, query, pairs):
        events.append(("verify", "sequence", query))
        proof = {"verified": True, "rows": len(query.rows), "artifact_sha256": artifact["artifact_sha256"],
                 "query_input_sha256": query.input_sha256, "query_ids": tuple(row.question_id for row in query.rows)}
        assert tuple(key for key, _ in pairs) == proof["query_ids"]
        if broken == "verify":
            proof["query_ids"] = ()
        return proof

    return fold_module.ForecastAdapters(lambda rows: fit("lightgbm", rows), lambda model, query: predict("lightgbm", model, query),
        lambda rows: fit("sequence", rows), lambda model, query: predict("sequence", model, query), verify)


def test_six_arms_share_training_query_rosters_weights_and_target_free_calls():
    targets, coordinates, sessions, fold = fixture()
    calls, events = [], []
    result = fold_module.forecast_fold(iter(coordinates), fold, "S1", label_reader(sessions, coordinates, calls),
                                      expected_targets=targets, adapters=adapters(events))
    assert result.schema == fold_module.SCHEMA and result.adapter_mode == "injected_diagnostic"
    assert result.original_test_coordinates == (coordinates[1],)
    assert not result.timing_scenarios_are_independent
    assert calls and {row[0] for row in calls} == {DAYS[0]}
    assert result.learned_fit_count == 4 and result.empirical_fit_count == 1
    fitted = [row[2] for row in events if row[0] == "fit"]
    assert [row.mode for row in fitted] == ["chart", "chart_context", "chart", "chart_context"]
    assert len({row.roster_sha256 for row in fitted}) == len({row.weights_sha256 for row in fitted}) == 1
    assert len({row.outcomes_sha256 for row in fitted}) == 1
    assert any(weight < 1 for weight in fitted[0].exact_weights)
    queried = [row[2] for row in events if row[0] == "predict"]
    assert all(tuple(row.question_id for row in query.rows) == tuple(row.question_id for row in queried[0].rows)
               for query in queried)
    assert all(not hasattr(query, "outcomes") for query in queried)
    assert len([row for row in events if row[0] == "verify"]) == 2
    assert all(tuple(item.key for item in row.predictions) == fold_module.MODEL_KEYS for row in result.predictions)
    assert all(item.status == "predicted" for row in result.predictions for item in row.predictions)
    assert result.training_support.original_questions > result.training_support.supported_joint_questions
    assert all(len(model.pair_sha256) == len(model.artifact_bytes_sha256) == 64 for model in result.models)
    assert len({model.pair_sha256 for model in result.models}) == 4
    assert any("joint_horizon_30_unsupported" in reason for reason, _ in result.training_support.unavailable_by_reason)


def test_full_original_stream_is_exhausted_before_any_training_label_callback():
    targets, coordinates, sessions, fold = fixture()
    exhausted = False

    def source():
        nonlocal exhausted
        yield from coordinates
        exhausted = True

    reader = label_reader(sessions, coordinates, [])

    def guarded(*args):
        assert exhausted
        return reader(*args)

    fold_module.forecast_fold(source(), fold, "S1", guarded, expected_targets=targets, adapters=adapters([]))


def test_all_missing_training_labels_leave_six_unsupported_and_no_fit_calls(monkeypatch):
    targets, coordinates, _, fold = fixture()
    events = []
    monkeypatch.setattr(fold_module, "fit_empirical", lambda *_args, **_kw: pytest.fail("zero support must not fit"))
    result = fold_module.forecast_fold(coordinates, fold, "S1", lambda *_: None,
                                      expected_targets=targets, adapters=adapters(events))
    assert result.learned_fit_count == result.empirical_fit_count == 0 and not events
    assert result.empirical is None and all(row.status == "unsupported" for row in result.models)
    assert all(row.probabilities is None and row.status == "unsupported"
               for question in result.predictions for row in question.predictions)
    assert result.training_support.supported_joint_questions == 0
    assert dict(result.training_support.unavailable_by_reason)["training_label_missing"] > 0


def test_missing_training_reason_and_each_horizon_support_survive():
    targets, coordinates, sessions, fold = fixture()
    reader = label_reader(sessions, coordinates, [])
    first = question_digest(coordinates[0].questions[0].question)

    def partial(day, symbol, key):
        return None if key == first else reader(day, symbol, key)

    result = fold_module.forecast_fold(coordinates, fold, "S1", partial,
                                      expected_targets=targets, adapters=adapters([]))
    assert dict(result.training_support.unavailable_by_reason)["training_label_missing"] == 1
    horizon = {(stage, minute, status): count for stage, minute, status, count
               in result.training_support.label_support_by_stage_horizon}
    assert horizon[("approach", 5, "label_missing")] == 1
    assert horizon[("approach", 15, "label_missing")] == 1
    assert horizon[("approach", 30, "label_missing")] == 1


def test_query_parent_outside_thirty_minutes_retains_short_horizon_prediction():
    targets, coordinates, sessions, fold = fixture()
    result = fold_module.forecast_fold(coordinates, fold, "S1", label_reader(sessions, coordinates, []),
                                      expected_targets=targets, adapters=adapters([]))
    outside = [row for row in result.predictions if "outside_session" in row.question.question.unsupported_reasons]
    assert outside and all(pred.status == "predicted" for row in outside for pred in row.predictions)


def test_unsupported_price_query_is_kept_without_numeric_prediction():
    targets, coordinates, sessions, fold = fixture()
    query = coordinates[1]
    bad = replace(query.questions[0], status="unsupported", reasons=("recent_price_window_unknown",),
                  prices_in_R=None, common=None, context=None)
    changed = (coordinates[0], replace(query, questions=(bad, *query.questions[1:])), coordinates[2])
    result = fold_module.forecast_fold(changed, fold, "S1", label_reader(sessions, coordinates, []),
                                      expected_targets=targets, adapters=adapters([]))
    first = result.predictions[0]
    assert first.question == bad
    assert all(row.status == "unsupported" and row.reason == "feature_unsupported:recent_price_window_unknown"
               for row in first.predictions)
    assert len(result.predictions) == len(query.questions)


def test_noevent_and_unknown_original_test_coordinates_are_not_dropped():
    targets, coordinates, sessions, fold = fixture(extra=True)
    query = coordinates[3]
    unknown_session = replace(sessions[(DAYS[1], "other")], minutes={})
    unknown = project_coordinate(query.targets, unknown_session)
    changed = (*coordinates[:3], unknown, *coordinates[4:])
    result = fold_module.forecast_fold(changed, fold, "S1", label_reader(sessions, coordinates, []),
                                      expected_targets=targets, adapters=adapters([]))
    assert len(result.original_test_coordinates) == 2
    assert result.original_test_coordinates[1] == unknown
    assert {row.status for row in unknown.frames} == {"unknown"}


@pytest.mark.parametrize("kind", ["duplicate", "omit", "reorder"])
def test_bad_original_coordinate_stream_refuses_before_training_labels(kind):
    targets, coordinates, _, fold = fixture()
    supplied = {"duplicate": (coordinates[0], coordinates[0], coordinates[2]),
                "omit": coordinates[:-1], "reorder": tuple(reversed(coordinates))}[kind]
    with pytest.raises(ValueError):
        fold_module.forecast_fold(supplied, fold, "S1", lambda *_: pytest.fail("callback before roster validation"),
                                 expected_targets=targets, adapters=adapters([]))


@pytest.mark.parametrize("kind", ["question", "cutoff", "maturity", "target"])
def test_training_callback_cannot_substitute_metadata_or_future_maturity(kind):
    targets, coordinates, sessions, fold = fixture()
    reader = label_reader(sessions, coordinates, [])

    def invalid(*args):
        label = reader(*args)
        if kind == "question":
            return replace(label, question=replace(label.question, episode_origin_context="B"))
        if kind == "cutoff":
            return replace(label, cutoff_at=fold.first_test_opening + MINUTE)
        if kind == "target":
            return replace(label, question=replace(label.question, path=replace(label.question.path,
                                                   target=label.question.path.target + 1)))
        return replace(label, results=(replace(label.results[0], mature_at=fold.first_test_opening), *label.results[1:]))

    with pytest.raises(ValueError):
        fold_module.forecast_fold(coordinates, fold, "S1", invalid, expected_targets=targets, adapters=adapters([]))


@pytest.mark.parametrize("broken", ["training", "length", "mass", "verify", "mutated_artifact"])
def test_artifact_prediction_and_independent_verification_binding_cannot_drift(broken):
    targets, coordinates, sessions, fold = fixture()
    with pytest.raises(ValueError):
        fold_module.forecast_fold(coordinates, fold, "S1", label_reader(sessions, coordinates, []),
                                 expected_targets=targets, adapters=adapters([], broken=broken))


def test_s1_s2_original_overlap_is_once_per_scenario_not_independent_evidence():
    targets, coordinates, sessions, fold = fixture()
    results = [fold_module.forecast_fold(coordinates, fold, scenario, label_reader(sessions, coordinates, []),
               expected_targets=targets, adapters=adapters([])) for scenario in ("S1", "S2")]
    assert results[0].original_test_coordinates == results[1].original_test_coordinates
    assert [row.question_id for row in results[0].predictions] == [row.question_id for row in results[1].predictions]
    assert all(not row.timing_scenarios_are_independent for row in results)


def test_source_dates_outside_registered_fold_cannot_be_training_or_query_rows():
    targets, coordinates, sessions, fold = fixture()
    result = fold_module.forecast_fold(coordinates, fold, "S1", label_reader(sessions, coordinates, []),
                                      expected_targets=targets, adapters=adapters([]))
    assert {row.session for row in result.original_test_coordinates} == {DAYS[1]}
    assert result.training_support.original_coordinates == 1
    with pytest.raises(ValueError, match="outside_original_dates"):
        fold_module.forecast_fold(coordinates, replace(fold, test_sessions=("2031-02-17",),
            first_test_opening=opening("2031-02-17")), "S1", lambda *_: None, expected_targets=targets, adapters=adapters([]))


def test_stage_without_training_support_has_the_same_abstention_in_all_six_arms():
    targets, coordinates, sessions, fold = fixture()
    reader = label_reader(sessions, coordinates, [])

    def one_stage(*args):
        result = reader(*args)
        return result if result.question.stage == "approach" else None

    result = fold_module.forecast_fold(coordinates, fold, "S1", one_stage,
                                      expected_targets=targets, adapters=adapters([]))
    assert result.learned_fit_count == 4
    for row in result.predictions:
        if row.question.question.stage == "breakout":
            assert all(item.status == "unsupported" and item.reason == "no_mature_supported_training_for_stage"
                       for item in row.predictions)
        else:
            assert all(item.status == "predicted" for item in row.predictions)


def test_no_supported_test_questions_does_not_fit_unused_learned_models():
    targets, coordinates, sessions, fold = fixture()
    missing_test = project_coordinate(coordinates[1].targets, None)
    changed = (coordinates[0], missing_test, coordinates[2])
    events = []
    result = fold_module.forecast_fold(changed, fold, "S1", label_reader(sessions, coordinates, []),
                                      expected_targets=targets, adapters=adapters(events))
    assert not events and result.learned_fit_count == 0
    assert result.original_test_coordinates == (missing_test,) and result.predictions == ()
    assert all(row.reason == "no_supported_test_questions" for row in result.models)


def test_tiny_native_fold_connects_all_models_and_independent_sequence_replay():
    pytest.importorskip("numpy")
    pytest.importorskip("lightgbm")
    targets, coordinates, sessions, fold = fixture()
    result = fold_module.forecast_fold(coordinates, fold, "S1", label_reader(sessions, coordinates, []),
                                      expected_targets=targets)
    assert result.adapter_mode == "fixed_native" and result.learned_fit_count == 4
    assert result.empirical_fit_count == 1
    assert all(row.status == "predicted" for question in result.predictions for row in question.predictions)
    assert all(sha256(encode(model.artifact)).hexdigest() == model.artifact_bytes_sha256
               for model in result.models)
    for model in result.models:
        if model.key.endswith("sequence"):
            assert model.independent_verification["verified"] is True
            assert model.independent_verification["rows"] == len(result.predictions)
            assert model.independent_verification["query_input_sha256"] == model.query_input_sha256
