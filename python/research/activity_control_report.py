"""Pure descriptive ACTIVITY/control reports; callers verify saved input pins.

Saved books are restored into the existing accounting types, never replayed.
Common dates and declarations establish internal consistency, not source truth
or equal realized exposure. A fixed hash control is not a permutation test.
"""
from __future__ import annotations

from dataclasses import fields
from datetime import date, datetime
from decimal import Decimal, localcontext
import json
import math

from research.activity_accounting import Lot, sum_decimal_values
from research.activity_book import ActivityBook
from research.activity_control_selection import CountedControlSelection
from research.activity_pair_calibration import PARAMETERS
from research.activity_partition_selection import PartitionSelection
from research.activity_replay import SyntheticReplay
from research.activity_timing import SessionLagPolicy
from research.activity_timing_report import _arm, paired_timing_report


ARMS = ("D1", "D2")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _fields(value: dict, expected, message: str) -> None:
    _require(type(value) is dict and set(value) == set(expected), message)


def _encoded(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _day(value: str) -> date:
    _require(type(value) is str, "saved canonical date text required")
    result = date.fromisoformat(value)
    _require(result.isoformat() == value, "saved canonical date text required")
    return result


def _decimal(value: str) -> Decimal:
    _require(type(value) is str and 0 < len(value) <= 8200, "bounded saved Decimal text required")
    try:
        number = Decimal(value)
    except ArithmeticError as error:
        raise ValueError("saved Decimal text required") from error
    _require(number.is_finite() and str(number) == value
        and len(number.as_tuple().digits) <= 4096 and abs(number.as_tuple().exponent) <= 4096,
        "finite canonical saved Decimal required")
    return number


def _restore_book(row: dict) -> ActivityBook:
    _fields(row, (item.name for item in fields(ActivityBook)), "exact saved book fields required")
    _require(type(row["lots"]) is list and type(row["applied_event_ids"]) is list,
        "saved lot/event arrays required")
    lots = []
    for value in row["lots"]:
        _fields(value, (item.name for item in fields(Lot)), "exact saved lot fields required")
        values = dict(value)
        for name in ("shares", "mark"):
            values[name] = _decimal(values[name])
        if values["carried_value"] is not None:
            values["carried_value"] = _decimal(values["carried_value"])
        for name in ("mark_date", "entered_on", "due_on", "available_on"):
            values[name] = _day(values[name])
        lots.append(Lot(**values))
    return ActivityBook(_decimal(row["cash"]), tuple(lots), _day(row["as_of"]),
        tuple(row["applied_event_ids"]))


def _restore_replay(row: dict) -> SyntheticReplay:
    _fields(row, (item.name for item in fields(SyntheticReplay)), "exact saved replay fields required")
    _require(all(type(row[name]) is list for name in ("books", "selections", "diagnostics")),
        "saved replay arrays required")
    targets = []
    for value in row["selections"]:
        _require(type(value) is list and len(value) == 2 and type(value[1]) is list,
            "saved pre-opening targets required")
        targets.append((_day(value[0]), tuple(value[1])))
    _require(all(type(value) is list and len(value) == 2 for value in row["diagnostics"]),
        "saved diagnostic pairs required")
    return SyntheticReplay(tuple(_restore_book(value) for value in row["books"]), tuple(targets),
        tuple(tuple(value) for value in row["diagnostics"]), row["closed_trades"])


def _restore_selection(row: dict) -> PartitionSelection:
    _fields(row, (item.name for item in fields(PartitionSelection)), "exact saved selection fields required")
    _require(type(row["eligible_codes"]) is list and type(row["selection_at"]) is str,
        "saved selection codes and cutoff required")
    return PartitionSelection(_day(row["formation_on"]), _day(row["decision_on"]),
        datetime.fromisoformat(row["selection_at"]), tuple(row["eligible_codes"]),
        row["source_population_count"], row["source_partition_sha256"])


def _compare_report(saved: dict, computed: dict) -> None:
    _fields(saved, computed, "saved report fields differ from reconstructed books")
    for key in saved:
        if key != "arms":
            _require(_encoded(saved[key]) == _encoded(computed[key]), "saved report/declaration mismatch")
    _fields(saved["arms"], ARMS, "both saved report arms required")
    for arm in ARMS:
        _fields(saved["arms"][arm], computed["arms"][arm], "exact saved arm metrics required")
        for key, value in computed["arms"][arm].items():
            actual = saved["arms"][arm][key]
            if key == "session_return_sd" and value is not None:
                _require(type(actual) is float and math.isfinite(actual)
                    and math.isclose(actual, value, rel_tol=1e-12, abs_tol=1e-15),
                    "saved session-return dispersion differs from books")
            else:
                _require(_encoded(actual) == _encoded(value), "saved arm path/counter/metric mismatch")


def _exposure(books: tuple[ActivityBook, ...]) -> dict:
    rows, values = [], []
    for book in books:
        nav = book.nav
        invested = sum_decimal_values(tuple(lot.marked_value for lot in book.lots))
        fraction = None if nav == 0 else invested / nav
        _require(fraction is None or 0 <= fraction <= 1, "noncash NAV fraction outside unit interval")
        rows.append({"date": book.as_of.isoformat(), "invested_fraction": None if fraction is None else str(fraction)})
        if fraction is not None:
            values.append(fraction)
    return {"path": rows, "mean_invested_fraction": str(sum_decimal_values(tuple(values)) / len(values)) if values else None,
        "maximum_invested_fraction": str(max(values)) if values else None,
        "active_sessions": sum(value > 0 for value in values), "defined_sessions": len(values),
        "undefined_zero_nav_sessions": len(books) - len(values),
        "interpretation": "noncash inventory includes carried pending claims; it is not immediately liquid exposure"}


def _metrics(metrics: dict, replay: SyntheticReplay) -> dict:
    return {"cumulative_return": metrics["total_return"],
        **{name: metrics[name] for name in ("maximum_drawdown", "session_return_sd", "session_return_count",
            "filled_entries", "ordinary_closed_trades", "terminal_open_lots")},
        "unfilled_entries": dict(replay.diagnostics).get("unfilled_entries", 0), "exposure": _exposure(replay.books)}


def descriptive_control_report(original_report: dict, original_replay: dict,
    control_replays: dict[str, SyntheticReplay], control_selections: dict[str, tuple[CountedControlSelection, ...]],
    *, calendar: tuple[date, ...], original_parameters: dict, control_parameters: dict,
    initial_nav: Decimal = Decimal(1)) -> dict:
    """Describe one fixed control for each lag without IO, replay or inference.

    The caller pins and links the original report/replay and the complete source
    calendar, and verifies the control's evidence. Exact parameter equality is
    necessary but cannot establish source, event, dividend or fill truth.
    """
    _require(type(initial_nav) is Decimal and str(initial_nav) == "1", "unchanged normalized initial NAV required")
    _require(_encoded(original_parameters) == _encoded(control_parameters) == _encoded(PARAMETERS),
        "unchanged canonical original/control parameters and costs required")
    _require(type(calendar) is tuple and calendar and all(type(day) is date for day in calendar)
        and calendar == tuple(sorted(set(calendar))), "complete unique ordered calendar required")
    end = date.fromisoformat(original_parameters["end"])
    _require(end in calendar, "calendar must include evaluation end")
    end_index = calendar.index(end)
    expected_days = calendar[original_parameters["lookback"] + 1:end_index + 1]
    _require(bool(expected_days), "complete evaluation calendar required")
    _require(type(original_replay) is dict and original_replay.get("schema") == "activity-paired-replay-v1"
        and original_replay.get("status") == "pure_paired_replay" and original_replay.get("initial_nav") == "1"
        and _encoded(original_replay.get("report")) == _encoded(original_report), "linked original saved pair/report required")
    for mapping in (original_replay["replays"], original_replay["selections"], control_replays, control_selections):
        _fields(mapping, ARMS, "both separate timing arms required")
    activities, partitions, controls, overlaps = {}, {}, {}, {}
    with localcontext() as context:
        context.prec = 28
        for arm, lag in (("D1", 1), ("D2", 2)):
            activities[arm] = _restore_replay(original_replay["replays"][arm])
            _require(type(original_replay["selections"][arm]) is list, "saved formation declarations required")
            partitions[arm] = tuple(_restore_selection(row) for row in original_replay["selections"][arm])
            selections = control_selections[arm]
            _require(type(selections) is tuple and all(isinstance(row, CountedControlSelection) for row in selections),
                "immutable counted control declarations required")
            policy = SessionLagPolicy(lag, paired=True)
            indices = policy.formation_indices(calendar, lookback=original_parameters["lookback"],
                holding_sessions=original_parameters["holding_sessions"], end_index=end_index)
            formations = tuple(calendar[index] for index in indices)
            _require(tuple(row.formation_on for row in partitions[arm]) == formations
                == tuple(row.formation_on for row in selections), "whole original/control formation grid required")
            _require(tuple(book.as_of for book in activities[arm].books) == expected_days
                and isinstance(control_replays[arm], SyntheticReplay)
                and tuple(book.as_of for book in control_replays[arm].books) == expected_days,
                "whole common original/control session calendar required")
            projections, overlap_rows, difference, union = [], [], 0, 0
            targets = dict(activities[arm].selections)
            for index, original, selected in zip(indices, partitions[arm], selections, strict=True):
                decision, cutoff = policy.execution_on(calendar, index), policy.selection_at(calendar, index)
                _require(original.decision_on == selected.decision_on == decision
                    and original.selection_at == selected.selection_at == cutoff and selected.arm == arm
                    and selected.activity_targets == targets.get(original.formation_on)
                    and original.source_population_count == selected.source_population_count
                    and set(original.eligible_codes) <= {code for code, _ in selected.source_population},
                    "original pre-opening targets, source population or paired schedule mismatch")
                projections.append(PartitionSelection(selected.formation_on, selected.decision_on, selected.selection_at,
                    selected.eligible_codes, selected.source_population_count, selected.source_partition_sha256))
            controls[arm] = _arm(control_replays[arm], tuple(projections), initial_nav)
            for selected, (formation, chosen) in zip(selections, control_replays[arm].selections, strict=True):
                _require(len(chosen) <= len(selected.activity_targets), "control targets exceed original pre-opening ceiling")
                first, second = set(selected.activity_targets), set(chosen)
                changed, combined = len(first ^ second), len(first | second)
                difference += changed
                union += combined
                overlap_rows.append({"formation": formation.isoformat(), "ACTIVITY_codes": list(selected.activity_targets),
                    "CONTROL_codes": list(chosen), "activity_target_ceiling": len(first), "control_targets": len(second),
                    "symmetric_difference_count": changed, "union_count": combined,
                    "rate": None if not combined else str(Decimal(changed) / combined)})
            overlaps[arm] = {"formations": overlap_rows, "symmetric_difference_count": difference,
                "union_count": union, "rate": None if not union else str(Decimal(difference) / union)}
        for left, right in zip(control_selections["D1"], control_selections["D2"], strict=True):
            _require(left.source_population == right.source_population
                and left.source_population_sha256 == right.source_population_sha256,
                "paired controls must retain the same formation source population")
        recomputed = paired_timing_report(activities["D1"], activities["D2"], partitions["D1"], partitions["D2"], initial_nav=initial_nav)
        _compare_report(original_report, recomputed)
        arms = {}
        for arm in ARMS:
            activity = _metrics(original_report["arms"][arm], activities[arm])
            control = _metrics(controls[arm], control_replays[arm])
            arms[arm] = {"ACTIVITY": activity, "CONTROL": control, "target_overlap": overlaps[arm],
                "CONTROL_minus_ACTIVITY": {"cumulative_return": str(Decimal(control["cumulative_return"]) - Decimal(activity["cumulative_return"])),
                    "maximum_drawdown": str(Decimal(control["maximum_drawdown"]) - Decimal(activity["maximum_drawdown"])),
                    "session_return_sd": None if activity["session_return_sd"] is None or control["session_return_sd"] is None
                        else control["session_return_sd"] - activity["session_return_sd"]}}
    return {"schema": "activity-control-descriptive-report-v1", "status": "pure_descriptive_comparison",
        "arms": arms, "parameters": json.loads(_encoded(original_parameters)), "initial_nav": "1",
        "units": {"cumulative_return": "fraction; Decimal text", "maximum_drawdown": "nonnegative fraction; Decimal text",
            "session_return_sd": "unannualized sample session-return fraction; float or null", "invested_fraction": "noncash marked inventory / NAV; Decimal text or null"},
        "interpretation": "one fixed hash control per lag; not a permutation distribution; no p-value, edge, strategy pass or winning-lag claim",
        "limitations": "count ceilings do not match realized cash exposure, sectors, volatility or holding duration; price returns exclude dividends",
        "comparison_power_prerequisite_met": False, "power_design_passed": False,
        "promotion_allowed": False, "source_truth_certified": False, "full_economic_accounting_verified": False,
        "books_replayed": False, "D1_completed_studies": 0, "D2_completed_studies": 0}
