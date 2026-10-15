"""Pure CONTROL books over a bound BR scope with reviewed ordinary intervals.

Reuse existing action validation, accounting, exits and descriptive reporting.
An event needing successor/unit preparation refuses before either book runs;
the caller must prepare that existing event path under a new registered scope.
The original ACTIVITY books are restored from saved values and never replayed.
This module performs no IO, registration, source judgment or actual trial.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from research.activity_book import ActivityBook
from research.activity_control_connection import BoundControlInputs, serialize_counted_selections
from research.activity_control_report import descriptive_control_report
from research.activity_exit_bounds import mixed_action_scope, verify_ordinary_inventory
from research.activity_pair_calibration import PARAMETERS
from research.activity_paired_replay import _json_value
from research.activity_preflight import encoded, require
from research.activity_replay import replay_synthetic
from research.activity_reviewed_actions import reviewed_actions
from research.activity_timing import SessionLagPolicy


def replay_bound_control(
    bound: BoundControlInputs, reviewed_coverage: dict, basis_evidence: dict,
    original_report: dict, original_replay: dict, *, parameters: dict,
) -> dict:
    """Validate both arms and all ordinary intervals before computing CONTROL.

    The caller independently verifies every saved input's byte pin and the
    successor-aware declarations used to construct ``bound``. No event or
    unknown interval can be silently treated as ordinary. The conservative
    nominee evidence scope remains distinct from final counted declarations.
    """
    require(isinstance(bound, BoundControlInputs)
        and encoded(parameters) == encoded(PARAMETERS), "bound inputs and unchanged canonical parameters required")
    quotes = bound.holding_inputs
    calendar = quotes.calendar
    selections = {"D1": bound.selections_d1, "D2": bound.selections_d2}
    require(len(selections["D1"]) == len(selections["D2"]), "complete paired control schedule required")
    for left, right in zip(selections["D1"], selections["D2"], strict=True):
        require(left.formation_on == right.formation_on
            and left.source_population == right.source_population
            and left.source_population_sha256 == right.source_population_sha256,
            "paired controls must retain the same complete source population before replay")
    require(type(original_replay) is dict and original_replay.get("schema") == "activity-paired-replay-v1"
        and original_replay.get("status") == "pure_paired_replay" and original_replay.get("initial_nav") == "1"
        and encoded(original_replay.get("report")) == encoded(original_report)
        and type(original_replay.get("replays")) is dict and set(original_replay["replays"]) == set(selections)
        and type(original_replay.get("selections")) is dict and set(original_replay["selections"]) == set(selections),
        "unchanged linked original ACTIVITY saved pair/report required")
    # Check every target and source count before either arm can expose returns.
    for arm in selections:
        original = original_replay["replays"][arm]
        require(type(original) is dict and type(original.get("selections")) is list,
            "original saved pre-opening selections required")
        targets = {date.fromisoformat(day): tuple(codes) for day, codes in original["selections"]}
        original_partitions = original_replay["selections"][arm]
        require(len(targets) == len(original["selections"]) == len(selections[arm])
            and type(original_partitions) is list and len(original_partitions) == len(selections[arm]),
            "complete unique original formation schedule required")
        for selected, partition in zip(selections[arm], original_partitions, strict=True):
            require(selected.activity_targets == targets.get(selected.formation_on)
                and partition["formation_on"] == selected.formation_on.isoformat()
                and partition["decision_on"] == selected.decision_on.isoformat()
                and partition["selection_at"] == selected.selection_at.isoformat()
                and partition["source_population_count"] == selected.source_population_count
                and set(partition["eligible_codes"]) <= {code for code, _ in selected.source_population},
                "original target ceiling/source/timing changed")
    mixed = mixed_action_scope(bound.provisional_exit_scope, reviewed_coverage)
    require(set(mixed["ordinary_codes"]) == set(bound.original_required_code_dates),
        "new event preparation required: every nominee needs complete ordinary interval coverage")
    events, successors = reviewed_actions(reviewed_coverage, basis_evidence,
        expected_windows=tuple(mixed["windows"]), calendar=calendar,
        activity_snapshot_sha256=quotes.activity_snapshot_sha256,
        quote_snapshot_sha256=quotes.quote_snapshot_sha256,
        read_scope_sha256=mixed["action_scope_sha256"])
    require(not events and not successors and not basis_evidence["bases"],
        "new event preparation required before CONTROL replay; events cannot be dropped")
    initial = ActivityBook(Decimal(1), (), calendar[parameters["lookback"]])
    replays = {arm: replay_synthetic(calendar, quotes.panel, parameters, selections[arm],
        dataset_sha256=quotes.quote_snapshot_sha256, events=events, initial_book=initial,
        timing_policy=SessionLagPolicy(lag, paired=True)) for arm, lag in (("D1", 1), ("D2", 2))}
    verify_ordinary_inventory(replays, mixed)
    report = descriptive_control_report(original_report, original_replay, replays, selections,
        calendar=calendar, original_parameters=parameters, control_parameters=parameters)
    return {
        "schema": "activity-control-replay-v1", "status": "pure_descriptive_control_replay",
        "parameters": parameters, "initial_nav": "1", "calendar_dates": [day.isoformat() for day in calendar],
        "activity_snapshot_sha256": quotes.activity_snapshot_sha256,
        "quote_snapshot_sha256": quotes.quote_snapshot_sha256, "read_scope_sha256": quotes.read_scope_sha256,
        "action_scope_sha256": mixed["action_scope_sha256"], "conditional_action_scope": mixed,
        "selections": serialize_counted_selections(selections), "replays": _json_value(replays), "report": report,
        "original_ACTIVITY_replayed": False, "comparison_power_prerequisite_met": False,
        "power_design_passed": False, "actual_study_completed": False, "promotion_allowed": False,
    }
