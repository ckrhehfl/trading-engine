"""Tiny invented EI assembly; patched calendar is never a runtime option."""

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, timedelta
import json
from types import SimpleNamespace

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_forecast_unit as unit
from research import intraday_context_snapshot as snapshot
from research import intraday_context_snapshot_unit as snapshot_unit
from research.intraday_campaign_ml_labels import ChronologicalFold, fixed_calendar
from research.intraday_context_snapshot_reference import _Counts
from research.intraday_ed_sources import TargetCoordinate, UniverseCalendar
from research.intraday_scenario_snapshot_unit import _sealed_input
from test_intraday_context_forecast_fold import adapters, inputs, opening


DAYS = tuple((date(2031, 2, 10) + timedelta(days=i)).isoformat() for i in range(6))


def tiny_calendar(sessions):
    assert sessions == DAYS
    return SimpleNamespace(folds=tuple(ChronologicalFold(i, (0, i), (i, i + 1), DAYS[:i], DAYS[i:i + 1],
                                                       opening(DAYS[i])) for i in range(1, 6)),
                           evaluation_reference_sessions=DAYS[1:])


@pytest.fixture
def calendar_patch(monkeypatch):
    monkeypatch.setattr(unit, "fixed_calendar", tiny_calendar)


def _build(folder, *, absent=False):
    folder.mkdir(mode=0o700)
    targets, coordinates, sessions = [], [], {}
    for day in DAYS:
        for rank in range(1, 21):
            symbol = f"invented{rank:02d}"
            membership = tuple(TargetCoordinate(scenario, symbol, day, rank, None, "invented", b"{}")
                               for scenario in ("S1", "S2"))
            targets.extend(membership)
            value = None
            if not absent and rank <= 3:
                value = inputs(day, symbol)
                if rank == 2:
                    value = inputs(day, symbol, bars=((100, 101, 99, 100),) * 20)
                elif rank == 3:
                    value = replace(value, minutes={})
            if value is not None:
                sessions[day, symbol] = value
            coordinates.append(snapshot.project_coordinate(membership, value))
    universe = UniverseCalendar(DAYS, tuple(targets), "a" * 64)
    feature_path, label_path = folder / "features", folder / "labels"
    feature_seal = snapshot.write_features(feature_path, iter(coordinates), expected_targets=universe.targets)
    label_seal = snapshot.write_labels(label_path, feature_path, feature_seal,
        lambda day, symbol: sessions.get((day, symbol)), expected_targets=universe.targets)
    labels = tuple(snapshot.read_labels(label_path, label_seal, feature_path, feature_seal,
                                        expected_targets=universe.targets))
    union, scenarios = _Counts(), {name: _Counts() for name in ("S1", "S2")}
    for coordinate, label in zip(coordinates, labels, strict=True):
        union.add(coordinate, label)
        for target in coordinate.targets:
            scenarios[target.scenario].add(coordinate, label)
    counts = {"union": union.payload(), "scenarios": {name: value.payload() for name, value in scenarios.items()}}
    members = {"features": asdict(feature_seal), "labels": asdict(label_seal)}
    summary = {"counts": counts, "counts_sha256": wire.digest(wire.encode(counts)), "members": members,
               "universe_sha256": universe.raw_sha256, "calendar_sha256": wire.digest(wire.encode(DAYS)),
               "original_dates": len(DAYS), "original_coordinates": len(coordinates),
               "original_memberships": len(targets)}
    bundle = folder / "snapshot.bundle"
    snapshot_unit._pack_bundle(bundle, feature_path, feature_seal, label_path, label_seal, lambda _: None,
                               expected_targets=universe.targets)
    return SimpleNamespace(folder=folder, universe=universe, members=members, summary=summary, bundle=bundle,
                           feature_path=feature_path, feature_seal=feature_seal, label_path=label_path,
                           label_seal=label_seal, coordinates=tuple(coordinates))


@pytest.fixture(scope="module")
def invented(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("ei-unit-input") / "snapshot")


def compare(invented, output, events, **changes):
    arguments = {"scenario": "S1", "expected_members": invented.members, "expected_summary": invented.summary,
                 "universe": invented.universe, "checkpoint": events.append, "adapters": adapters([])}
    return unit.compare_snapshot(invented.bundle, output, **(arguments | changes))


def header(path):
    with path.open("rb") as stream:
        return json.loads(stream.readline())


def test_real_runtime_calendar_has_exactly_five_fixed_257_date_folds(invented):
    with pytest.raises(ValueError, match="257"):
        unit.compare_snapshot(invented.bundle, invented.folder / "must-not-exist", scenario="S1",
            expected_members=invented.members, expected_summary=invented.summary, universe=invented.universe,
            checkpoint=lambda _: None, adapters=adapters([]))
    assert not (invented.folder / "must-not-exist").exists()
    days = tuple((date(2030, 1, 1) + timedelta(days=i)).isoformat() for i in range(257))
    calendar = fixed_calendar(days)
    assert len(calendar.folds) == unit.FOLD_COUNT == 5
    assert unit.MAX_LEARNED_FITS == 20 and unit.MAX_EMPIRICAL_FITS == 5


def test_five_fold_assembly_seals_before_evaluation_and_retains_all_originals(tmp_path, invented, calendar_patch):
    output, events, fits = tmp_path / "result.bundle", [], []
    result = compare(invented, output, events, adapters=adapters(fits))
    assert result.summary["folds"] == 5 and result.summary["learned_fit_count"] == 20
    assert result.summary["empirical_fit_count"] == 5 and result.summary["evaluation_sessions"] == 5
    assert result.summary["original_evaluation_coordinates"] == 100
    assert result.summary["adapter_mode"] == "injected_diagnostic"
    assert result.summary["fold_prediction_readback_verified"] and result.summary["report_reference_verified"]
    assert not result.summary["independent_saved_bundle_audit"]
    assert not result.summary["contains_economic_returns"] and not result.summary["timing_scenarios_are_independent"]
    assert len([row for row in fits if row[0] == "fit"]) == 20
    started = [index for index, event in enumerate(events) if event == "ei_forecast_start_fold"]
    assert len(started) == 5
    for left, right in zip(started, (*started[1:], len(events)), strict=True):
        part = events[left:right]
        sealed = part.index("ei_forecast_predictions_verified")
        assert all(event not in part[:sealed] for event in
                   ("ei_forecast_evaluation_label_coordinate", "ei_forecast_reference_label_coordinate"))
        assert "ei_forecast_training_label_coordinate" not in part[sealed:]
        assert part.index("ei_forecast_report_verified") > sealed
    assert events.count("ei_forecast_index_label_bytes") == 120
    assert result.summary["io"]["label_index_coordinates"] == 120
    assert result.summary["io"]["maximum_cached_label_questions"] <= snapshot.MAX_QUESTIONS_PER_COORDINATE
    assert result.summary["io"]["label_coordinate_decodes"]["training"] == 15
    raw_header = header(output)
    assert raw_header["model_keys"] == list(unit.MODEL_KEYS)
    assert raw_header["contrasts"] == [list(row) for row in unit.CONTRASTS]
    assert len(raw_header["folds"]) == 5 and len(raw_header["date_weighted_comparisons"]) == 48
    for record in raw_header["folds"]:
        assert record["score"]["original_coordinates"] == 20 and record["report_reference"]["verified"]
        assert dict(record["score"]["coordinate_status"])["session_missing"] == 17
        assert dict(record["score"]["original_frames_by_status"])["unknown"] > 0
        assert all(len(model["pair_sha256"]) == 64 for model in record["models"])
    assert output.stat().st_mode & 0o777 == 0o400
    assert wire.file_digest(output, unit.MAXIMUM_BYTES, lambda _: None) == (result.seal.sha256, result.seal.size)
    assert not list(tmp_path.glob("ei-forecast-folds-*"))


def test_zero_support_uses_no_models_and_preserves_all_five_empty_denominators(tmp_path, calendar_patch):
    invented = _build(tmp_path / "empty", absent=True)
    fits = []
    output = tmp_path / "empty.bundle"
    result = compare(invented, output, [], adapters=adapters(fits))
    assert not fits and result.summary["learned_fit_count"] == result.summary["empirical_fit_count"] == 0
    assert result.summary["original_evaluation_coordinates"] == 100
    assert result.summary["original_evaluation_questions"] == 0
    assert result.summary["io"]["label_coordinate_decodes"] == {}
    assert all(model["status"] == "unsupported" for fold in header(output)["folds"] for model in fold["models"])


def test_raw_label_index_does_not_decode_any_outcome_json(tmp_path, invented, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("outcome decoded while indexing bytes")
    monkeypatch.setattr(snapshot, "_read_label_coordinate", forbidden)
    with _sealed_input(invented.label_path) as (stream, _):
        index = unit._LabelIndex(stream, invented.label_path, invented.label_seal, invented.feature_seal,
                                snapshot._roster(invented.universe.targets), lambda _: None)
        assert len(index.offsets) == 120 and index.decoded == {}
        read = index.reader(fold=1, phase="training", allowed_dates=(DAYS[0],))
        with pytest.raises(ValueError, match="phase or date"):
            read(DAYS[0], "invented01", "0" * 64)


def test_index_checks_phase_dates_original_question_and_one_coordinate_cache(invented):
    with _sealed_input(invented.label_path) as (stream, _):
        index = unit._LabelIndex(stream, invented.label_path, invented.label_seal, invented.feature_seal,
                                snapshot._roster(invented.universe.targets), lambda _: None)
        for coordinate in invented.coordinates:
            index.register(coordinate)
        index.finish_features()
        coordinate = invented.coordinates[0]
        training = index.reader(fold=1, phase="training", allowed_dates=(DAYS[0],))
        for question in coordinate.questions:
            assert training(DAYS[0], coordinate.symbol, snapshot.question_digest(question.question)).question == question.question
        assert index.decoded["training"] == 1
        with pytest.raises(ValueError, match="phase or date"):
            training(DAYS[1], coordinate.symbol, snapshot.question_digest(coordinate.questions[0].question))
        evaluation = index.reader(fold=1, phase="evaluation", allowed_dates=(DAYS[1],))
        with pytest.raises(ValueError, match="phase or date"):
            evaluation(DAYS[1], coordinate.symbol, "0" * 64)
        index.allow_evaluation(1)
        with pytest.raises(ValueError, match="phase or date"):
            training(DAYS[0], coordinate.symbol, snapshot.question_digest(coordinate.questions[0].question))
        with pytest.raises(ValueError, match="question label omitted"):
            evaluation(DAYS[1], coordinate.symbol, "0" * 64)


@pytest.mark.parametrize("change", ["footer", "trailing", "hash"])
def test_index_verifies_whole_label_framing_and_hash_before_any_decode(tmp_path, invented, change, monkeypatch):
    raw = invented.label_path.read_bytes()
    if change == "footer":
        rows = raw.splitlines(keepends=True)
        rows[-1] = wire.encode({"records": 121})
        raw = b"".join(rows)
    elif change == "trailing":
        raw += b"extra\n"
    else:
        raw = raw.replace(b'invented01', b'invented09', 1)
    path = tmp_path / "bad-labels"
    path.write_bytes(raw)
    path.chmod(0o400)
    monkeypatch.setattr(snapshot, "_read_label_coordinate", lambda *_: pytest.fail("decoded before index verification"))
    with _sealed_input(path) as (stream, _), pytest.raises(ValueError):
        unit._LabelIndex(stream, path, invented.label_seal, invented.feature_seal,
                         snapshot._roster(invented.universe.targets), lambda _: None)


def test_index_does_not_confuse_a_resealed_row_with_another_original_coordinate(tmp_path, invented):
    rows = [json.loads(line) for line in invented.label_path.read_bytes().splitlines()]
    rows[1]["symbol"] = "invented02"
    raw = b"".join(wire.encode(row) for row in rows)
    path = tmp_path / "swapped-label"
    path.write_bytes(raw)
    path.chmod(0o400)
    seal = replace(invented.label_seal, size=len(raw), sha256=wire.digest(raw))
    with _sealed_input(path) as (stream, _):
        index = unit._LabelIndex(stream, path, seal, invented.feature_seal,
                                snapshot._roster(invented.universe.targets), lambda _: None)
        for coordinate in invented.coordinates:
            index.register(coordinate)
        index.finish_features()
        read = index.reader(fold=1, phase="training", allowed_dates=(DAYS[0],))
        question = invented.coordinates[0].questions[0]
        with pytest.raises(ValueError, match="original coordinate or question"):
            read(DAYS[0], "invented01", snapshot.question_digest(question.question))


@pytest.mark.parametrize("change", ["members", "universe", "calendar", "counts_hash", "population", "features"])
def test_audited_pins_roster_and_causal_support_are_required_before_any_fit(tmp_path, invented, calendar_patch, change):
    summary = deepcopy(invented.summary)
    members = deepcopy(invented.members)
    if change == "members":
        members["features"]["sha256"] = "0" * 64
        summary["members"] = members
    elif change == "universe":
        summary["universe_sha256"] = "0" * 64
    elif change == "calendar":
        summary["calendar_sha256"] = "0" * 64
    elif change == "counts_hash":
        summary["counts_sha256"] = "0" * 64
    elif change == "population":
        summary["original_coordinates"] -= 1
    else:
        summary["counts"]["union"]["questions"] += 1
        summary["counts_sha256"] = wire.digest(wire.encode(summary["counts"]))
    fits = []
    with pytest.raises(ValueError):
        compare(invented, tmp_path / "rejected", [], expected_summary=summary,
                expected_members=members, adapters=adapters(fits))
    assert fits == [] and not (tmp_path / "rejected").exists()


def test_failed_prediction_readback_never_enables_evaluation(tmp_path, invented, calendar_patch, monkeypatch):
    events = []
    def broken(*args, **kwargs):
        raise ValueError("invented failed seal readback")
    monkeypatch.setattr(unit, "read_forecasts", broken)
    with pytest.raises(ValueError, match="failed seal"):
        compare(invented, tmp_path / "rejected", events)
    assert "ei_forecast_predictions_verified" not in events
    assert not any(event in events for event in
                   ("ei_forecast_evaluation_label_coordinate", "ei_forecast_reference_label_coordinate"))


def test_failed_independent_report_prevents_final_output(tmp_path, invented, calendar_patch, monkeypatch):
    monkeypatch.setattr(unit, "verify_forecast_report", lambda *args, **kwargs: {"verified": False})
    with pytest.raises(ValueError, match="independent report"):
        compare(invented, tmp_path / "rejected", [])
    assert not (tmp_path / "rejected").exists()


def test_total_output_bound_includes_header_before_output_creation(tmp_path, invented, calendar_patch, monkeypatch):
    monkeypatch.setattr(unit, "MAXIMUM_BYTES", 1)
    with pytest.raises(ValueError, match="bundle bounds"):
        compare(invented, tmp_path / "rejected", [])
    assert not (tmp_path / "rejected").exists()


def test_s2_reuses_original_coordinates_without_independence_claim(tmp_path, invented, calendar_patch):
    result = compare(invented, tmp_path / "s2.bundle", [], scenario="S2")
    assert result.summary["scenario"] == "S2" and result.summary["original_evaluation_coordinates"] == 100
    assert not result.summary["timing_scenarios_are_independent"]


def test_native_tiny_five_fold_boundary(tmp_path, invented, calendar_patch):
    pytest.importorskip("numpy")
    pytest.importorskip("lightgbm")
    result = compare(invented, tmp_path / "native.bundle", [], adapters=None)
    assert result.summary["adapter_mode"] == "fixed_native" and result.summary["learned_fit_count"] == 20
    assert result.summary["fold_prediction_readback_verified"] and result.summary["report_reference_verified"]
