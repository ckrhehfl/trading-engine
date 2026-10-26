"""Authenticate the fixed five-hypothesis Discovery registration, without running it.

``validate_spec`` is pure. ``load_spec`` reads only the supplied registration
JSON; neither function opens a registered input path, computes an outcome,
logs a trial or authorizes execution. Each later runner must still satisfy the
registration's independent verification, review, profiling and logging gates.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any


SPECIFICATION_ID = "intraday-autonomous-round1-v1"
SPECIFICATION_PATH = (Path(__file__).resolve().parents[2]
    / "configs/research/discovery/intraday-autonomous-round1-v1.json")
# SHA-256 of UTF-8, sorted keys, compact separators, no ASCII escaping or NaN.
# Fixed independently of the file read at runtime; prose is part of the contract.
REGISTERED_CANONICAL_SHA256 = "0502a97c3bb9d1d86221154104dabdae275c3868556362c31f784f0403c9f1ff"
HYPOTHESIS_IDS = (
    "H1-morning-trend-pullback-reclaim",
    "H2-morning-decline-midpoint-recovery",
    "H3-failed-downside-range-break",
    "H4-compression-expansion-breakout",
    "H5-late-session-morning-strength",
)


class RegistrationRefusal(ValueError):
    """Stable refusal codes never include supplied paths or source values."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise RegistrationRefusal(reason)


def _json_value(value: object) -> None:
    if type(value) is dict:
        _require(all(type(key) is str for key in value), "invalid_json_key")
        for item in value.values():
            _json_value(item)
    elif type(value) is list:
        for item in value:
            _json_value(item)
    else:
        _require(value is None or type(value) in (str, int, float, bool), "invalid_json_value")
        if type(value) is float:
            _require(math.isfinite(value), "nonfinite_json_value")


def _canonical(value: object) -> bytes:
    try:
        _json_value(value)
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (RecursionError, UnicodeError):
        raise RegistrationRefusal("invalid_json_value") from None


def _fields(actual: dict, expected: dict, reason: str) -> None:
    """Compare JSON types too: True, 1 and 1.0 are not interchangeable."""
    _require(all(key in actual and _canonical(actual[key]) == _canonical(value)
        for key, value in expected.items()), reason)


def validate_spec(spec: object) -> None:
    """Reject any change to the frozen registration, including prose and extras.

    Boundary checks provide useful refusal codes; the independent whole-contract
    pin also covers every hypothesis parameter, source path/hash and definition.
    Success authenticates a design only, never a trial result or strategy pass.
    """
    encoded = _canonical(spec)
    _require(type(spec) is dict, "registration_not_object")
    _fields(spec, {
        "id": SPECIFICATION_ID,
        "mode": "bounded_non_promotable_discovery_campaign",
        "maximum_hypotheses": 5,
        "window": {"start": "2025-09-04", "end": "2026-09-23", "status": "spent_discovery"},
        "universe": "original_CB_point_in_time_monthly_top20_no_replacement",
        "policies": ["D+1", "D+2"],
        "side": "long_only_cash_equity",
        "status": "registered_design_no_new_hypothesis_outcomes_read",
        "stop": "first_followup_marker_or_all5_hypotheses_reported;no_sixth_or_same_sample_tuning",
    }, "campaign_scope_mismatch")
    for section in ("inputs", "cost", "common", "exploration_policy", "statistics", "execution"):
        _require(type(spec.get(section)) is dict, "registration_section_not_object")
    _fields(spec["inputs"], {
        "new_acquisition": False, "forward_or_reserved_access": False,
        "fields": ["OHLC", "original_timestamps", "original_provenance"],
        "reference_sessions": 257, "reference_weeks": 56, "targets_per_policy": 5140,
    }, "input_scope_mismatch")
    _fields(spec["cost"], {
        "primary_bps": "33", "commission_each_side_bps": "1.5",
        "slippage_each_side_bps": "5", "primary_sell_tax_bps": "20",
        "secondary_can_qualify": False,
    }, "cost_scope_mismatch")
    _fields(spec["common"], {
        "bar_minutes": 5, "availability_lag_minutes": 2,
        "entry_rule": "first_five_minute_OPEN_strictly_after_decision",
        "holding_minutes": 60, "exit_rule": "entry_plus60minute_OPEN_no_intrabar_barriers",
        "required_label_minutes": 61, "maximum_signals_per_name_session": 1,
        "ordinary_decision_cutoff_minutes": 240, "same_day_only": True,
        "frequency_denominator_weeks": 56, "weekly_opportunity_objective": 15,
        "portfolio_or_actual_fills": False,
    }, "timing_or_measurement_scope_mismatch")
    _fields(spec["exploration_policy"], {
        "parameter_search": False, "adaptive_hypothesis_replacement": False,
        "automatic_retry": False, "direction_closure": False, "promotion": False,
    }, "exploration_boundary_mismatch")
    _fields(spec["statistics"], {
        "own_sizing_before_means": True, "own_sizing_cost_bps": "33",
        "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
        "primary_hypothesis_tests": 10, "marker_is_strategy_pass": False,
    }, "statistical_boundary_mismatch")
    _fields(spec["execution"], {
        "unit": "one_fixed_hypothesis_including_both_policies_producer_and_independent_verifier",
        "maximum_actual_units": 5, "cpu_seconds": 300, "address_space_mib": 512,
        "wall_seconds": 1200, "nice": 10, "preregistration_and_durable_start_required": True,
        "exact_gcp_invented_profile_required": True,
        "separate_source_and_integrity_reviews_required": True,
        "required_CI_and_normal_merge_required": True, "collector_changes": False,
    }, "execution_boundary_mismatch")
    hypotheses = spec.get("hypotheses")
    _require(type(hypotheses) is list and len(hypotheses) == 5, "hypothesis_count_mismatch")
    for order, (hypothesis, identity) in enumerate(zip(hypotheses, HYPOTHESIS_IDS), 1):
        _require(type(hypothesis) is dict, "hypothesis_not_object")
        _fields(hypothesis, {"id": identity, "order": order, "holding_minutes": 60},
            "hypothesis_identity_or_horizon_mismatch")
    _require(hashlib.sha256(encoded).hexdigest() == REGISTERED_CANONICAL_SHA256,
        "immutable_registration_mismatch")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def load_spec(path: str | Path = SPECIFICATION_PATH) -> dict[str, Any]:
    """Read and authenticate a registration JSON only; do not dereference inputs."""
    try:
        spec = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise RegistrationRefusal("registration_unreadable") from None
    validate_spec(spec)
    return spec
