"""Registration and invented mutations only; no actual research input is read."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from research import intraday_autonomous_round as round1


@pytest.fixture
def spec():
    return round1.load_spec()


def replace(spec, path, value):
    target = spec
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def test_registered_design_loads_and_pure_validation_does_not_mutate(spec, monkeypatch):
    original = deepcopy(spec)
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("unexpected file access"))
    assert round1.validate_spec(spec) is None
    assert spec == original


def test_loader_reads_only_requested_registration_and_returns_independent_objects(spec, monkeypatch):
    reads = []
    raw = json.dumps(spec)
    def read_text(path, **kwargs):
        reads.append(path)
        return raw
    monkeypatch.setattr(Path, "read_text", read_text)
    first = round1.load_spec(Path("invented-registration.json"))
    second = round1.load_spec(Path("invented-registration.json"))
    first["hypotheses"][0]["order"] = 99
    assert second["hypotheses"][0]["order"] == 1
    assert reads == [Path("invented-registration.json")] * 2


@pytest.mark.parametrize("path,value", [
    (("window", "start"), "2018-12-31"),
    (("window", "end"), "2026-09-24"),
    (("universe",), "current_survivors_top20"),
    (("inputs", "new_acquisition"), True),
    (("inputs", "forward_or_reserved_access"), True),
    (("inputs", "universe_sha256"), "0" * 64),
    (("inputs", "archive_path"), "/invented/replacement.jsonl.gz"),
    (("inputs", "fields"), ["OHLC", "volume"]),
    (("inputs", "reference_weeks"), 55),
    (("cost", "primary_bps"), "28"),
    (("cost", "secondary_can_qualify"), True),
    (("cost", "secondary_scenarios", 0, "before_trade_date"), "2026-01-01"),
    (("cost", "secondary_scenarios", 1, "fixed_bps"), "33"),
    (("common", "availability_lag_minutes"), 0),
    (("common", "required_label_minutes"), 60),
    (("common", "maximum_signals_per_name_session"), 2),
    (("common", "peer_control"), "best_future_peer"),
    (("exploration_policy", "missing_signal"), "fill_missing_with_zero"),
    (("exploration_policy", "missing_label"), "delete_unknown_targets"),
    (("exploration_policy", "underpowered"), "mark_power_pass_and_continue"),
    (("exploration_policy", "zero_or_undefined"), "invent_mean_zero_and_continue"),
    (("exploration_policy", "parameter_search"), True),
    (("exploration_policy", "adaptive_hypothesis_replacement"), True),
    (("exploration_policy", "automatic_retry"), True),
    (("exploration_policy", "direction_closure"), True),
    (("exploration_policy", "promotion"), True),
    (("statistics", "own_sizing_before_means"), False),
    (("statistics", "bootstrap_seed"), 1),
    (("execution", "maximum_actual_units"), 6),
    (("execution", "cpu_seconds"), 600),
    (("execution", "address_space_mib"), 1024),
    (("execution", "wall_seconds"), 2400),
    (("execution", "collector_changes"), True),
    (("execution", "exact_gcp_invented_profile_required"), False),
    (("execution", "separate_source_and_integrity_reviews_required"), False),
    (("execution", "required_CI_and_normal_merge_required"), False),
])
def test_rejects_changed_research_or_execution_boundary(spec, path, value):
    replace(spec, path, value)
    with pytest.raises(round1.RegistrationRefusal):
        round1.validate_spec(spec)


@pytest.mark.parametrize("index,key,value", [
    (0, "confirmation_bars", 4), (1, "initial_minutes", 30),
    (2, "invalidation", "retry_after_breach"), (3, "compression_ratio", "0.75"),
    (4, "entry_minutes_before_close", 60),
])
def test_each_hypothesis_parameters_and_semantics_are_frozen(spec, index, key, value):
    spec["hypotheses"][index][key] = value
    with pytest.raises(round1.RegistrationRefusal, match="immutable_registration_mismatch"):
        round1.validate_spec(spec)


@pytest.mark.parametrize("change", ["sixth", "missing", "duplicate", "reordered", "renamed"])
def test_exact_five_hypotheses_have_fixed_identity_and_order(spec, change):
    hypotheses = spec["hypotheses"]
    if change == "sixth":
        hypotheses.append(deepcopy(hypotheses[-1]))
    elif change == "missing":
        hypotheses.pop()
    elif change == "duplicate":
        hypotheses[1] = deepcopy(hypotheses[0])
    elif change == "reordered":
        hypotheses.reverse()
    else:
        hypotheses[0]["id"] = "adaptive-replacement"
    with pytest.raises(round1.RegistrationRefusal):
        round1.validate_spec(spec)


@pytest.mark.parametrize("path,value", [
    (("maximum_hypotheses",), 5.0), (("hypotheses", 0, "order"), True),
    (("statistics", "bootstrap_seed"), False), (("exploration_policy", "promotion"), 0),
])
def test_bool_integer_and_float_are_not_interchangeable(spec, path, value):
    replace(spec, path, value)
    with pytest.raises(round1.RegistrationRefusal):
        round1.validate_spec(spec)


@pytest.mark.parametrize("path,value", [
    (("statistics", "marker_is_strategy_pass"), True),
    (("statistics", "followup_marker"), "positive_observed_mean_only"),
    (("statistics", "primary_hypothesis_tests"), 5),
    (("stop",), "promote_first_positive_hypothesis"),
])
def test_early_followup_marker_is_conditional_and_never_a_strategy_pass(spec, path, value):
    replace(spec, path, value)
    with pytest.raises(round1.RegistrationRefusal):
        round1.validate_spec(spec)


def test_whole_pin_rejects_unexpected_fields_and_omitted_semantics(spec):
    changed = deepcopy(spec)
    changed["unregistered_extension"] = "new_trial"
    with pytest.raises(round1.RegistrationRefusal, match="immutable_registration_mismatch"):
        round1.validate_spec(changed)
    del spec["hypotheses"][0]["setup"]
    with pytest.raises(round1.RegistrationRefusal, match="immutable_registration_mismatch"):
        round1.validate_spec(spec)


def test_normalized_pin_accepts_whitespace_and_object_key_order(spec, tmp_path):
    path = tmp_path / "registration.json"
    path.write_text(json.dumps(dict(reversed(list(spec.items()))), indent=3), encoding="utf-8")
    assert round1.load_spec(path) == spec


@pytest.mark.parametrize("raw,reason", [
    ('{"id":"first","id":"second"}', "duplicate_json_key"),
    ('{"nested":{"cost":33,"cost":0}}', "duplicate_json_key"),
    ('{"value":NaN}', "nonfinite_json_value"),
    ('{"value":Infinity}', "nonfinite_json_value"),
    ('{"value":-Infinity}', "nonfinite_json_value"),
    ('{"broken":', "registration_unreadable"),
    ('[]', "registration_not_object"),
])
def test_json_loader_refuses_ambiguous_or_invalid_contracts(tmp_path, raw, reason):
    path = tmp_path / "invalid.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(round1.RegistrationRefusal, match=reason):
        round1.load_spec(path)


def test_invalid_python_json_values_and_missing_file_are_refused(spec, tmp_path):
    for invalid in (object(), (1, 2), {1: "non_string_key"}, float("nan")):
        with pytest.raises(round1.RegistrationRefusal):
            round1.validate_spec(invalid)
    with pytest.raises(round1.RegistrationRefusal, match="registration_unreadable"):
        round1.load_spec(tmp_path / "absent.json")
