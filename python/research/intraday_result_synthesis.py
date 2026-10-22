"""Documentary re-expression of four previously verified publications.

The caller authenticates publication bytes against its frozen specification
before decoding them. This module has no input/output or project imports and
does not reconstruct labels, events, uncertainty, or a research trial.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from fractions import Fraction
import hashlib
import json
import math
import re


SOURCES = ("CL", "CN", "CP", "CR")
VARIANTS = ("D+1", "D+2")
FAMILIES = ("opening_range_breakout", "post_drop_recovery")
HORIZONS = {"opening_range_breakout": ("5", "15", "30", "60", "120"),
            "post_drop_recovery": ("5", "15", "30")}
REFERENCE_SESSIONS = 257
REFERENCE_WEEKS = 56
COST = Fraction(33, 10000)
WINDOW = {"symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23"}
IDENTITIES = {
    "CL": ("intraday-entry-delay-comparison-v1", "entry_delay_compared_within_budget",
           "fixed-exit-clock-one-vs-three-minute-entry-comparison"),
    "CN": ("intraday-exit-policy-comparison-v1", "exit_policy_compared_within_budget",
           "fixed-three-minute-entry-time-only-vs-close-trigger-managed-exit-comparison"),
    "CP": ("intraday-peer-context-comparison-v1", "peer_context_compared_within_budget",
           "fixed-opening-breakout-positive-original-peer-context-conditional-per-entry-comparison"),
    "CR": ("intraday-recovery-context-comparison-v1", "recovery_context_compared_within_budget",
           "fixed-post-drop-recovery-negative-original-event-context-exploratory-conditional-per-entry-comparison"),
}
CAP_OPEN = "same_original_three_minute_wait_entry_open_and_time_only_entry_plus30minute_exit_open"
PAIRED_COST = "subtract_exact_33_over_10000_from_each_event_both_arms_same_assumption"
FILTER_COST = "subtract_exact33_over10000_per_event_same_assumed_cost_cancels_in_difference"


class SynthesisRefusal(ValueError):
    """Stable errors avoid disclosing source values."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise SynthesisRefusal(reason)


def _finite_json(value: object) -> None:
    if type(value) is dict:
        _require(all(type(key) is str for key in value), "invalid_json_key")
        for item in value.values():
            _finite_json(item)
    elif type(value) is list:
        for item in value:
            _finite_json(item)
    else:
        _require(value is None or type(value) in (str, bool, int, float), "invalid_json_value")
        if type(value) is float:
            _require(math.isfinite(value), "nonfinite_source")


def _integer(value: object, minimum: int = 0) -> int:
    _require(type(value) is int and value >= minimum, "invalid_count")
    return value


def _number(value: object) -> float:
    _require(type(value) in (int, float) and math.isfinite(value), "invalid_number")
    return value


def _ratio(value: object) -> Fraction:
    _require(type(value) is dict and set(value) == {"numerator", "denominator"}, "invalid_exact_ratio")
    numerator, denominator = value["numerator"], value["denominator"]
    _require(type(numerator) is str and re.fullmatch(r"-?(0|[1-9][0-9]*)", numerator) is not None,
             "invalid_exact_ratio")
    _require(type(denominator) is str and re.fullmatch(r"[1-9][0-9]*", denominator) is not None,
             "invalid_exact_ratio")
    return Fraction(int(numerator), int(denominator))


def _exact(value: Fraction) -> dict:
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _matches(value: object, exact: Fraction) -> None:
    _require(_number(value) == float(exact), "source_arithmetic_mismatch")


def _arm(arm: dict) -> tuple[int, Fraction]:
    n = _integer(arm["events"], 1)
    total = _ratio(arm["sum_price_change"])
    _matches(arm["mean_price_change"], total / n)
    _matches(arm["mean_after_assumed_cost"], total / n - COST)
    for name in ("gross_positive_events", "cost_exceeding_events"):
        _require(_integer(arm[name]) <= n, "invalid_count")
    for name, count in (("gross_positive_fraction", "gross_positive_events"),
                        ("cost_exceeding_fraction", "cost_exceeding_events")):
        if name in arm:
            _matches(arm[name], Fraction(arm[count], n))
    return n, total


def _row(arm: dict) -> dict:
    n, total = _arm(arm)
    gross, net = total / n * 10000, (total / n - COST) * 10000
    return {"opportunities": n, "gross_mean_bps": float(gross), "net_mean_bps": float(net),
            "gross_mean_bps_exact": _exact(gross), "net_mean_bps_exact": _exact(net),
            "gross_sum_bps_exact": _exact(total * 10000),
            "net_sum_bps_exact": _exact((total - n * COST) * 10000),
            "reference_sessions": REFERENCE_SESSIONS, "reference_weeks": REFERENCE_WEEKS,
            "opportunities_per_week": n / REFERENCE_WEEKS,
            "opportunities_per_week_exact": _exact(Fraction(n, REFERENCE_WEEKS))}


def _contrast(original: dict, changed: dict, saved: dict, *, paired: bool) -> dict:
    n, original_total = _arm(original)
    m, changed_total = _arm(changed)
    difference = changed_total / m - original_total / n
    _matches(saved["mean_change"], difference)
    interval = saved["descriptive_normal_95_interval"]
    _require(type(interval) is list and len(interval) == 2, "invalid_source_interval")
    low, high = map(_number, interval)
    _require(low <= float(difference) <= high, "invalid_source_interval")
    uncertainty = saved["uncertainty"]
    _require(_integer(uncertainty["reference_sessions"]) == REFERENCE_SESSIONS,
             "reference_session_mismatch")
    if paired:
        _require(n == m == _integer(saved["events"]), "paired_cohort_mismatch")
        _require(_ratio(saved["sum_change"]) == changed_total - original_total,
                 "source_arithmetic_mismatch")
        _require(sum(_integer(saved[name]) for name in ("improved_events", "equal_events", "worse_events")) == n,
                 "paired_cohort_mismatch")
    else:
        _require(_integer(uncertainty["original_events"]) == n
                 and _integer(uncertainty["filtered_events"]) == m, "filtered_cohort_mismatch")
    return {"comparison": "changed_minus_original", "gross_mean_change_bps": float(difference * 10000),
            "net_mean_change_bps": float(difference * 10000),
            "mean_change_bps_exact": _exact(difference * 10000),
            "opportunities_change": m - n, "opportunities_per_week_change": (m - n) / REFERENCE_WEEKS,
            "opportunities_per_week_change_exact": _exact(Fraction(m - n, REFERENCE_WEEKS)),
            "source_contrast": deepcopy(saved), "source_interval_unit": "return_fraction",
            "descriptive_normal_95_interval_bps": [low * 10000, high * 10000],
            "uncertainty_recomputed": False}


def _weeks() -> tuple[str, ...]:
    start = date.fromisoformat(WINDOW["start"])
    end = date.fromisoformat(WINDOW["end"])
    monday = start - timedelta(days=start.weekday())
    weeks = []
    while monday <= end:
        year, week, _ = monday.isocalendar()
        weeks.append(f"{year}-W{week:02d}")
        monday += timedelta(days=7)
    _require(len(weeks) == REFERENCE_WEEKS, "reference_week_mismatch")
    return tuple(weeks)


def _selection(selection: dict, n: int, m: int) -> dict:
    _require(m <= n, "filtered_cohort_mismatch")
    expected = {"original_events": n, "filtered_events": m, "removed_events": n - m}
    for name, value in expected.items():
        _require(_integer(selection[name]) == value, "filtered_cohort_mismatch")
    _matches(selection["retention_fraction"], Fraction(m, n))
    weekly = selection["by_week"]
    _require(type(weekly) is dict and set(weekly) == set(_weeks()), "zero_inclusive_week_population_mismatch")
    totals = dict.fromkeys(expected, 0)
    for counts in weekly.values():
        original, filtered, removed = (_integer(counts[name]) for name in expected)
        _require(filtered + removed == original, "weekly_count_mismatch")
        if original:
            _matches(counts["retention_fraction"], Fraction(filtered, original))
        else:
            _require(counts["retention_fraction"] is None, "weekly_zero_mismatch")
        for name in expected:
            totals[name] += counts[name]
    _require(totals == expected, "weekly_count_mismatch")
    if "reference_weeks" in selection:
        _require(_integer(selection["reference_weeks"]) == REFERENCE_WEEKS, "reference_week_mismatch")
    for name, value in (("original_opportunities_per_week", n), ("filtered_opportunities_per_week", m),
                        ("removed_opportunities_per_week", n - m)):
        if name in selection:
            _matches(selection[name], Fraction(value, REFERENCE_WEEKS))
    return deepcopy(selection)


def _identity(source: str, publication: dict, pins: dict) -> None:
    study, status, mode = IDENTITIES[source]
    _require(type(pins) is dict and set(pins) == {"run_id", "report_sha256", "specification_sha256"},
             "invalid_expected_pins")
    _require(type(pins["run_id"]) is str and bool(pins["run_id"]), "invalid_expected_pins")
    for name in ("report_sha256", "specification_sha256"):
        _require(type(pins[name]) is str and re.fullmatch("[0-9a-f]{64}", pins[name]) is not None,
                 "invalid_expected_pins")
    record, verified = publication["record"], publication["verification"]
    _require(publication["status"] == status and record["status"] == "completed"
             and record["study_id"] == verified["study_id"] == study, "source_identity_mismatch")
    _require(publication["run_id"] == record["run_id"] == pins["run_id"], "source_identity_mismatch")
    _require(publication["output_sha256"] == record["result"]["output_sha256"]
             == verified["report_sha256"] == pins["report_sha256"], "source_identity_mismatch")
    _require(record["specification_sha256"] == verified["specification_sha256"]
             == pins["specification_sha256"], "source_identity_mismatch")
    _require(verified["verified"] is True, "unverified_source")
    _require(publication["calibration_only"] is False and publication["requires_operator_choice"] is False
             and verified["requires_operator_choice"] is False, "incomplete_source_comparison")
    _require(record["window"] == WINDOW and record["parameters"]["mode"] == mode,
             "source_convention_mismatch")
    _require(record["promotion_allowed"] is False and _integer(record["promotion_trial_increment"]) == 0,
             "source_claim_mismatch")
    _require(record["result"]["input_evidence"] == verified["input_evidence"], "source_evidence_mismatch")
    parameters = record["parameters"]["parameters"]
    if source in ("CL", "CN"):
        _require(parameters["cost_floor_bps"] == "33" and parameters["cost"] == PAIRED_COST,
                 "source_cost_mismatch")
    else:
        _require(parameters["cost"] == FILTER_COST and parameters["entry_exit"] == CAP_OPEN,
                 "source_convention_mismatch")
    if source == "CL":
        _require(parameters["price_label"] == "terminal_minute_close_divided_by_entry_minute_open_minus_one_all_intervening_minutes_valid"
                 and parameters["exit"] == "original_current_entry_plus_original_horizon_unchanged_for_both_labels",
                 "source_convention_mismatch")
    if source == "CN":
        _require(parameters["time_cap_minutes"] == 30
                 and parameters["time_only"] == "preplanned_at_original_decision_exit_open_at_entry_plus_30minutes",
                 "source_convention_mismatch")
    summaries = publication["summaries"]
    _require(type(summaries) is dict and set(summaries) == set(VARIANTS), "variant_population_mismatch")
    if source in ("CL", "CN"):
        for variant in VARIANTS:
            _require(type(summaries[variant]) is dict and set(summaries[variant]) == set(FAMILIES),
                     "family_population_mismatch")
            if source == "CL":
                for family in FAMILIES:
                    _require(set(summaries[variant][family]["by_horizon"]) == set(HORIZONS[family]),
                             "horizon_population_mismatch")
    raw = (json.dumps(summaries, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode()
    _require(hashlib.sha256(raw).hexdigest() == publication["summaries_sha256"], "source_summaries_hash_mismatch")


def _known_sizing(cell: dict, n: int, *, earlier: bool = False) -> None:
    _require(_integer(cell["selected_complete"]) == n and cell["requires_operator_choice"] is False,
             "source_sizing_mismatch")
    known, unknown, own = (("earlier_known_labels", "earlier_unknown_labels", "earlier_own_power")
                           if earlier else ("known_labels", "unknown_labels", "own_label_dispersion"))
    _require(_integer(cell[known]) == n and _integer(cell[unknown]) == 0
             and _integer(cell[own]["measured_labels"]) == n
             and _integer(cell[own]["reference_sessions"]) == REFERENCE_SESSIONS
             and cell[own]["requires_operator_choice"] is False
             and cell[own]["underpower_exception_applied"] is False, "source_sizing_mismatch")


def _synthesize(publications: dict, expected_pins: dict) -> dict:
    _require(type(publications) is dict and set(publications) == set(SOURCES)
             and type(expected_pins) is dict and set(expected_pins) == set(SOURCES), "source_population_mismatch")
    _finite_json(publications)
    for source in SOURCES:
        _identity(source, publications[source], expected_pins[source])
    cn_evidence = publications["CN"]["verification"]["input_evidence"]
    for source in ("CP", "CR"):
        evidence = publications[source]["verification"]["input_evidence"]["calibration_input_evidence"]
        _require(evidence["upstream_report_sha256"] == cn_evidence["upstream_report_sha256"]
                 and evidence["calibration_input_evidence"] == cn_evidence["calibration_input_evidence"],
                 "common_cm_provenance_mismatch")
    cr = publications["CR"]
    _require(cr["underpower_exception_applied"] is True and cr["qualified_cells_power_prerequisite_met"] is False
             and cr["record"]["result"]["underpower_exception_applied"] is True
             and cr["record"]["result"]["qualified_cells_power_prerequisite_met"] is False,
             "recovery_failed_power_not_retained")
    opened, closed = {}, {}
    for variant in VARIANTS:
        opened[variant], closed[variant] = {}, {}
        for family, selection_source in zip(FAMILIES, ("CP", "CR"), strict=True):
            cn = publications["CN"]["summaries"][variant][family]
            cl = publications["CL"]["summaries"][variant][family]
            selection = publications[selection_source]["summaries"][variant]
            baseline, managed = cn["time_only"], cn["managed"]
            n, total = _arm(baseline)
            _require(_arm(managed)[0] == n == _integer(cn["selected_complete"]), "paired_cohort_mismatch")
            for arm in ("time_only", "managed"):
                _known_sizing(cn["by_arm"][arm], n)
            _require(_integer(publications["CN"]["verification"]["selected_complete"][variant][family]) == n,
                     "source_count_mismatch")
            _require(_arm(selection["original"]) == (n, total)
                     and selection["original"]["mean_price_change"] == baseline["mean_price_change"]
                     and selection["original"]["mean_after_assumed_cost"] == baseline["mean_after_assumed_cost"],
                     "common_cm_baseline_mismatch")
            filtered_n, _ = _arm(selection["filtered"])
            verification = publications[selection_source]["verification"]
            _require(_integer(verification["original_complete"][variant]) == n
                     and _integer(verification["selected_complete"][variant]) == filtered_n
                     and _integer(verification["reference_sessions"]) == REFERENCE_SESSIONS, "source_count_mismatch")
            frequency = _selection(selection["selection"], n, filtered_n)
            opened[variant][family] = {
                "baseline": _row(baseline), "managed_exit": _row(managed), "context_filter": _row(selection["filtered"]),
                "managed_minus_baseline": _contrast(baseline, managed, cn["paired"], paired=True),
                "filtered_minus_original": _contrast(baseline, selection["filtered"], selection["difference"], paired=False),
                "filter_source": selection_source, "source_selection": frequency,
                "underpower_exception_applied": selection_source == "CR",
                "own_power_prerequisite_met": selection_source != "CR"}
            _require(_integer(cl["selected_complete"]) == n
                     == _integer(publications["CL"]["verification"]["selected_complete"][variant][family]),
                     "original_cohort_count_mismatch")
            cell = cl["by_horizon"]["30"]
            _known_sizing(cell, n, earlier=True)
            _require(cell["current_power_prerequisite_met"] is True
                     and cell["current_own_power"]["requires_operator_choice"] is False
                     and cell["current_own_power"]["underpower_exception_applied"] is False
                     and _integer(cell["current_own_power"]["measured_labels"]) == n
                     and _integer(cell["current_own_power"]["reference_sessions"]) == REFERENCE_SESSIONS,
                     "source_sizing_mismatch")
            _require(_arm(cell["current"])[0] == _arm(cell["earlier"])[0] == n, "paired_cohort_mismatch")
            closed[variant][family] = {"three_minute_wait": _row(cell["current"]),
                "one_minute_wait": _row(cell["earlier"]),
                "one_minus_three_minute_wait": _contrast(cell["current"], cell["earlier"], cell["paired"], paired=True)}
    return {"kind": "documentary_reexpression_of_verified_publications", "window": dict(WINDOW),
        "reference_sessions": REFERENCE_SESSIONS, "reference_weeks": REFERENCE_WEEKS, "assumed_cost_bps": 33,
        "frequency_basis": "all_56_reference_iso_weeks_including_zero_opportunity_weeks; opportunities_are_not_realized_portfolio_trades",
        "cap_minute_open_30m": {"endpoint_convention": "entry_open_to_cap_minute_open; managed_exit_uses_its_original_triggered_open",
            "variants": opened},
        "close_endpoint_30m": {"endpoint_convention": "original_three_minute_entry_plus30m_terminal_minute_close_fixed_for_both_waits",
            "variants": closed},
        "source_publications": {source: {"pins": deepcopy(expected_pins[source]),
            "record": deepcopy(publications[source]["record"]), "verification": deepcopy(publications[source]["verification"]),
            "summaries": deepcopy(publications[source]["summaries"])} for source in SOURCES},
        "limitations": ["source_cohort_provenance_retained_not_event_identity_recertification",
            "original_full_targets_unknowns_future_complete_conditioning_and_repeated_search_limitations_remain",
            "D+1_and_D+2_are_membership_information_delays_not_independent_replications",
            "CL_and_CN_endpoint_conventions_are_separate_no_cross_convention_ranking",
            "conditional_equal_hypothetical_cash_price_labels_not_portfolio_returns_or_fills",
            "source_intervals_retained_no_pooling_new_test_or_uncertainty_recalculation",
            "CQ_failed_own_sizing_remains_failed_CR_exception_applies_only_to_its_original_comparison",
            "no_exception_transfers_to_another_family_or_future_run"],
        "new_performance_family": False, "uncertainty_recomputed": False, "strategy_qualified": False,
        "direction_closed": False, "promotion_allowed": False, "actual_portfolio_pnl": False}


def synthesize(publications: dict[str, dict], *, expected_pins: dict[str, dict]) -> dict:
    """Re-express authenticated CL/CN/CP/CR JSON publications without I/O.

    Each expected pin has exactly ``run_id``, ``report_sha256`` and
    ``specification_sha256``. Pinning/authenticating the raw publication file
    remains the caller's responsibility; a self-declared verified flag alone
    does not authenticate bytes. Missing or inconsistent supplied fields fail
    closed with a stable ``SynthesisRefusal`` reason.
    """
    try:
        return _synthesize(publications, expected_pins)
    except SynthesisRefusal:
        raise
    except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError, RecursionError) as error:
        raise SynthesisRefusal("malformed_source_publication") from error
