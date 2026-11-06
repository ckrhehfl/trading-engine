"""Invented EG comparison lifecycle and prospective access checks."""

from contextlib import contextmanager
from datetime import date, timedelta
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_highvol_evaluation_unit as unit
from research import intraday_highvol_runtime as runtime
from research.intraday_highvol_policy import EntryForecast


ROOT = Path(__file__).resolve().parents[2]
DAYS = tuple((date(2025, 7, 1) + timedelta(days=index)).isoformat() for index in range(257))
FIRST, SECOND, THIRD = ((day, "KRX:000001", 15) for day in DAYS[100:103])


class InventedInputs:
    source_dates = DAYS

    def __init__(self, keys=(), forecasts=None):
        self.evaluation_keys = keys
        self._entry = {} if forecasts is None else forecasts
        self.forecasts = {key: SimpleNamespace(comparison_scope=scope) for key, (scope, _) in self._entry.items()}
        self.events = []

    def entry_forecasts(self):
        return {key: value for key, (_, value) in self._entry.items()}

    def holding_forecasts(self):
        return {}

    def features(self):
        for key in self.evaluation_keys:
            yield {"key": list(key)}
        self.events.append("features_complete")

    @contextmanager
    def open_labels(self, keys):
        assert self.events == ["features_complete"]
        self.events.append(("labels_open", keys))

        def label(key):
            self.events.append(("label", key))
            return {"key": list(key)}

        yield label
        self.events.append("labels_sealed")


def test_policy_requests_all_evaluation_keys_only_after_feature_eof(monkeypatch):
    inputs = InventedInputs((FIRST, SECOND))

    def compare(dates, features, *, labels_at, forecasts, include_chart_only):
        assert dates == DAYS and forecasts == {} and include_chart_only is True
        keys = [tuple(item["key"]) for item in features]
        return {"rows": [labels_at(key) for key in keys]}

    monkeypatch.setattr(unit, "compare_entry_policies", compare)
    report, keys = unit._evaluate(unit.POLICY, inputs, lambda _: None)
    assert keys == (FIRST, SECOND)
    assert report == {"rows": [{"key": list(key)} for key in keys]}
    assert inputs.events == ["features_complete", ("labels_open", keys), ("label", FIRST), ("label", SECOND), "labels_sealed"]


def test_management_requests_only_selected_common_event_union(monkeypatch):
    inputs = InventedInputs((FIRST, SECOND, THIRD), {
        ("A", "S1", "LGB_CHART", (*FIRST, 0)): ("common", EntryForecast(0.50, replay_ambiguous_costs=frozenset({33}))),
        ("A", "S2", "TCN_CHART", (*FIRST, 0)): ("common", EntryForecast(0.48)),
        ("A", "S1", "LGB_CHART", (*SECOND, 0)): ("chart_only", EntryForecast(0.70)),
        ("A", "S1", "TCN_CHART", (*THIRD, 0)): ("common", EntryForecast(0.33)),
    })

    def compare(dates, features, *, labels_at, entry_forecasts, holding_forecasts):
        assert dates == DAYS and len(list(features)) == 3 and len(entry_forecasts) == 4 and holding_forecasts == {}
        return {"selected": labels_at(FIRST)}

    monkeypatch.setattr(unit, "compare_saved_management", compare)
    assert unit._evaluate(unit.MANAGEMENT, inputs, lambda _: None) == ({"selected": {"key": list(FIRST)}}, (FIRST,))
    assert inputs.events == ["features_complete", ("labels_open", (FIRST,)), ("label", FIRST), "labels_sealed"]


def test_outcome_order_and_complete_consumption_are_required(monkeypatch):
    def compare(dates, features, *, labels_at, **kwargs):
        tuple(features)
        labels_at(SECOND)
        return {}

    monkeypatch.setattr(unit, "compare_entry_policies", compare)
    inputs = InventedInputs((FIRST, SECOND))
    with pytest.raises(ValueError, match="prospective outcome request"):
        unit._evaluate(unit.POLICY, inputs, lambda _: None)
    assert inputs.events == ["features_complete"]

    def omit(dates, features, *, labels_at, **kwargs):
        tuple(features)
        labels_at(FIRST)
        return {}

    monkeypatch.setattr(unit, "compare_entry_policies", omit)
    with pytest.raises(ValueError, match="every prospective outcome"):
        unit._evaluate(unit.POLICY, InventedInputs((FIRST, SECOND)), lambda _: None)


def test_empty_prospective_roster_still_checks_the_label_seal(monkeypatch):
    def compare(dates, features, *, labels_at, **kwargs):
        assert len(tuple(features)) == 1
        return {}

    monkeypatch.setattr(unit, "compare_saved_management", compare)
    inputs = InventedInputs((FIRST,))
    assert unit._evaluate(unit.MANAGEMENT, inputs, lambda _: None) == ({}, ())
    assert inputs.events == ["features_complete", ("labels_open", ()), "labels_sealed"]


@pytest.mark.parametrize("unit_id", [unit.POLICY, unit.MANAGEMENT])
def test_registration_gates_actual_readiness_and_exact_policy(unit_id):
    spec = json.loads((ROOT / unit.SPEC).read_bytes())
    science = (ROOT / unit.SCIENCE).read_bytes()
    spec.update(actual_runner_ready=False, actual_execution_authorized_by_this_file=False)
    unit.evaluation_contract(wire.encode(spec), science, unit_id, actual=False)
    with pytest.raises(ValueError, match="not registered ready"):
        unit.evaluation_contract(wire.encode(spec), science, unit_id, actual=True)
    spec.update(actual_runner_ready=True, actual_execution_authorized_by_this_file=True)
    unit.evaluation_contract(wire.encode(spec), science, unit_id, actual=True)
    spec["policy"]["fixed_selection_stress"]["outcome_bps"] = 33
    with pytest.raises(ValueError, match="timing or cost"):
        unit.evaluation_contract(wire.encode(spec), science, unit_id, actual=True)


def _policy_report(*, known):
    point = {"mean": 0.2 if known else None, "normal95_exploratory": [0.1, 0.3] if known else None,
             "contributing_sessions": 12 if known else 0}
    arm = {"decision_counts": {}, "outcome_counts": {}, "gross_pp": point, "net_pp": point, "weeks": {}}
    return {"schema": unit.REPORT_SCHEMAS[unit.POLICY], "reference_sessions": list(DAYS[100:]),
            "candidate_screen": [],
            "groups": {branch: {timing: {"support_states": {}, "common": {
                "opportunities": int(known), "first_hour_observed_clusters": int(known),
                "first_hour_confirmed_onsets": int(known), "first_hour_left_censored_clusters": 0, "costs": {
                    str(cost): {"arms": {name: arm for name in (unit.RULE, *unit.ARMS)}} for cost in unit.COST_BPS}}}
                for timing in unit.TIMINGS} for branch in unit.BRANCHES},
            "immediate": {timing: {"observed_clusters": int(known)} for timing in unit.TIMINGS}}


def test_whole_invented_qualification_refuses_empty_science():
    policy = unit._policy_summary(_policy_report(known=False))
    management = {"groups": {"A": {"S1": {"33": {"TCN_CHART": {"known_matched": 0}}}}}}
    for unit_id, report in ((unit.POLICY, policy), (unit.MANAGEMENT, management)):
        with pytest.raises(ValueError, match="known outcome"):
            unit._require_qualification_cases(unit_id, report)
    unit._require_qualification_cases(unit.POLICY, unit._policy_summary(_policy_report(known=True)))
    management["groups"]["A"]["S1"]["33"]["TCN_CHART"]["known_matched"] = 1
    unit._require_qualification_cases(unit.MANAGEMENT, management)


def _closed(tmp_path, monkeypatch):
    attempt, merge, checkpoint = "qualification1", "c" * 40, lambda _: None
    spec, science = ((ROOT / name).read_bytes() for name in (unit.SPEC, unit.SCIENCE))
    registration = wire.digest(spec)
    binding = {"unit_id": unit.POLICY, "input_contract": unit.INPUT_CONTRACT, "geometry": runtime.FULL,
               "registration_sha256": registration, "merge_commit": merge}
    run = SimpleNamespace(unit_id=unit.POLICY, invented_only=True, binding=binding, directory=tmp_path,
                          source_hashes=((unit.SPEC, registration), (unit.SCIENCE, wire.digest(science))))
    def pin(unit_id):
        return unit.PredecessorPin(unit_id, attempt, "a" * 64, merge)

    opened = []

    def open_inputs(snapshot, fits, *, invented_only, checkpoint):
        assert snapshot == pin(unit.SNAPSHOT_UNIT) and fits == tuple(map(pin, unit.FIT_IDS)) and invented_only
        result = InventedInputs((FIRST,))
        opened.append(result)
        return result

    def compare(dates, features, *, labels_at, **kwargs):
        assert dates == DAYS and list(features) == [{"key": list(FIRST)}] and labels_at(FIRST)["key"] == list(FIRST)
        return _policy_report(known=True)

    monkeypatch.setattr(unit, "open_evaluation_inputs", open_inputs)
    monkeypatch.setattr(unit, "compare_entry_policies", compare)
    closed = unit.closed_evaluation_unit(run, spec, science, snapshot_pin=pin(unit.SNAPSHOT_UNIT),
                                         fit_pins=tuple(map(pin, reversed(unit.FIT_IDS))))
    return run, closed, opened, checkpoint


def _start(run):
    binding = run.binding
    wire.exclusive_bytes(run.directory / "discovery.jsonl", wire.encode({
        "record_type": "discovery_trial", "status": "started", "study_id": run.unit_id,
        "parameters": binding, "specification_sha256": binding["registration_sha256"],
        "code_version": binding["merge_commit"], "window": runtime.WINDOW,
        "promotion_allowed": False, "promotion_trial_increment": 0,
    }))


def test_lifecycle_checks_start_and_reconstructs_exact_persisted_binding(tmp_path, monkeypatch):
    run, closed, opened, checkpoint = _closed(tmp_path, monkeypatch)
    closed.preflight()
    assert not opened
    with pytest.raises((FileNotFoundError, ValueError)):
        closed.compute(tmp_path, run.binding, checkpoint)
    assert not opened
    _start(run)
    output = closed.compute(tmp_path, run.binding, checkpoint)
    assert len(opened) == 1
    pin, = output.artifacts
    saved = runtime._json((tmp_path / pin.name).read_bytes())
    assert saved["binding"] == run.binding and saved["prospective_outcomes"] == {
        "keys": 1, "sha256": wire.digest(wire.encode((FIRST,))),
    }
    assert runtime._json(output.summary)["evaluation_sessions"] == 157
    assert "reference_sessions" not in runtime._json(output.summary) and len(output.summary) <= unit.SUMMARY_LIMIT
    assert runtime._json(output.geometry) == runtime.FULL

    def verify(unit_id, inputs, report, checkpoint):
        assert unit_id == unit.POLICY and inputs is opened[-1] and inputs is not opened[0] and report == saved["report"]
        return {"complete": True, "prospective_outcomes": saved["prospective_outcomes"]}

    module_name = "research.intraday_highvol_evaluation_reference"
    reference = ModuleType(module_name)
    reference.verify_evaluation = verify
    monkeypatch.setitem(sys.modules, module_name, reference)
    proof = closed.independent_verify(tmp_path, output, run.binding, checkpoint)
    assert proof["complete"] and proof["refit_count"] == 0 and proof["report_sha256"] == pin.sha256

    reference.verify_evaluation = lambda *_: {"complete": True, "prospective_outcomes": {"keys": 0}}
    with pytest.raises(ValueError, match="independent reconstruction incomplete"):
        closed.independent_verify(tmp_path, output, run.binding, checkpoint)
    (tmp_path / pin.name).write_bytes(wire.encode({**saved, "binding": {}}))
    with pytest.raises(ValueError, match="stored evaluation artifact differs"):
        closed.independent_verify(tmp_path, output, run.binding, checkpoint)
