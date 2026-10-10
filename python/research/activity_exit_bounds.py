"""Pure provisional ordinary-exit bounds over a restored full BM snapshot.

The caller separately verifies BL/BM package bytes and restores HoldingInputs.
Every potential D1/D2 entry remains independent; no targets, books or returns
are computed. Original through-end requirements and quote identifiers remain
unchanged. A shorter child interval is a source-priority proposal only. Final
use needs separately reviewed inclusive calendar-day no-action coverage through
the candidate itself, plus the existing pending-rights and price-basis checks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
import json
import re

from research.activity_holding_inputs import HoldingInputs, _iso, _require, _sha, holding_requirements
from research.activity_partition_selection import PartitionSelection
from research.activity_preflight import digest, encoded


SCHEMA = "activity-provisional-exit-bounds-v1"


def _json_value(value: object) -> object:
    """Copy reference parameters without changing exact Decimal text."""
    if isinstance(value, Decimal):
        _require(value.is_finite(), "exit parameters must be finite")
        return str(value)
    if isinstance(value, Mapping):
        _require(all(type(key) is str for key in value), "exit parameter keys must be text")
        return {key: _json_value(item) for key, item in value.items()}
    if type(value) in (list, tuple):
        return [_json_value(item) for item in value]
    _require(value is None or type(value) in (str, int, float, bool), "unsupported exit parameter value")
    return value


def _state(inputs: HoldingInputs, code: str, index: int, observations: dict) -> str:
    """Inspect only requested coordinates; unresolved rows cannot supply a fill."""
    observation = observations[code, inputs.calendar[index]]
    series = inputs.panel[code]
    if observation.typed_values is None:
        _require(not series.observed[index], "absent exit observation carries a present quote")
        return "absent_requested"
    _require(series.observed[index], "present exit observation lacks a quote")
    if observation.issues:
        return "unresolved_observed"
    if any(values[index] is None for values in
           (series.opens, series.closes, series.turnover, series.frozen, series.locked)):
        return "unresolved_observed"
    if series.frozen[index]:
        return "frozen"
    return "locked" if series.locked[index] else "observed"


def _windows(intervals: dict, issue_isins: dict) -> list[dict]:
    """Merge overlapping calendar-day intervals, preserving unrelated gaps."""
    windows = []
    for code, spans in sorted(intervals.items()):
        merged: list[tuple[date, date]] = []
        for start, end in sorted(spans):
            if merged and start <= merged[-1][1] + timedelta(days=1):
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        windows.extend({"code": code, "isin": issue_isins[code], "start": start.isoformat(),
                        "end": end.isoformat()} for start, end in merged)
    return windows


def provisional_exit_bounds(
    selections_d1: tuple[PartitionSelection, ...],
    selections_d2: tuple[PartitionSelection, ...],
    holding_inputs: HoldingInputs,
    parameters: Mapping,
    original_required_code_dates: Mapping[str, Sequence[str]],
    issue_isins: Mapping[str, str],
    *,
    input_sha256: Mapping[str, str],
) -> dict:
    """Propose each potential entry's first usable due-or-later opening.

    Holding length is the unchanged caller-pinned holding_sessions (126 in the
    registered study). Missing, unresolved or frozen rows are not candidates;
    locked bars retain the existing daily-open proxy. No candidate by evaluation
    end retains that end. The original package and all its input pins stay in
    the proof. Neither observed continuity nor this projection proves no events.

    child_scope_sha256 hashes encoded(result without that one field). A final
    caller must recompute this proof with validate_exit_bounds before passing
    child_windows and that scope identifier to reviewed_actions. All final
    content, pending-rights, original-end and successor obligations remain.
    """
    _require(isinstance(holding_inputs, HoldingInputs), "restored holding inputs required")
    _require(isinstance(parameters, Mapping) and {
        "lookback", "holding_sessions", "end", "threshold", "slots", "seed",
        "commission_bps_per_side", "slippage_bps_per_side"} <= parameters.keys(),
        "complete frozen replay parameters required")
    _require(all(type(parameters[key]) is int and parameters[key] > 0
                 for key in ("lookback", "holding_sessions", "slots"))
             and type(parameters["seed"]) is int, "exit geometry must retain integer parameters")
    params = _json_value(parameters)
    parameter_sha = digest(encoded(params))
    _require(isinstance(input_sha256, Mapping) and bool(input_sha256)
             and all(type(key) is str and key and key == key.strip() for key in input_sha256),
             "registered original input pins required")
    for pin in input_sha256.values():
        _sha(pin)
    pins = dict(sorted(input_sha256.items()))
    calendar = holding_inputs.calendar
    original = holding_requirements(selections_d1, selections_d2, calendar, parameters,
                                    original_required_code_dates)
    original_dates = {code: list(days) for code, days in original.items()}
    _require(original_dates == {code: list(days) for code, days in original_required_code_dates.items()},
             "exit proof must retain the exact full original requirements")
    _require(isinstance(issue_isins, Mapping) and set(issue_isins) == set(original)
             and all(type(isin) is str and re.fullmatch(r"[A-Z]{2}[0-9A-Z]{9}[0-9]", isin)
                     for isin in issue_isins.values()), "exact signal code/ISIN mapping required")
    identities = dict(sorted(issue_isins.items()))
    _sha(holding_inputs.activity_snapshot_sha256)
    _sha(holding_inputs.read_scope_sha256)
    coordinates = tuple((code, _iso(day)) for code, days in original.items() for day in days)
    _require(holding_inputs.requested_coordinates == coordinates
             and tuple((row.code, row.observation_date) for row in holding_inputs.observations) == coordinates,
             "restored exit observations must retain full original coordinates")
    panel = holding_inputs.panel
    _require(len(holding_inputs.series) == len(panel) and set(panel) == set(original)
             and all(len(series.observed) == len(calendar) for series in panel.values()),
             "restored exit panel must match original codes and calendar")
    if original:
        _sha(holding_inputs.quote_snapshot_sha256)
        _require(type(holding_inputs.fingerprint_json) is bytes
                 and digest(holding_inputs.fingerprint_json) == holding_inputs.quote_snapshot_sha256,
                 "exit snapshot must retain its original typed fingerprint")
    else:
        _require(holding_inputs.quote_snapshot_sha256 is holding_inputs.fingerprint_json
                 is holding_inputs.snapshot_received_at is None,
                 "empty exit union must not invent quote evidence")
    observations = {(row.code, row.observation_date): row for row in holding_inputs.observations}
    positions = {day: index for index, day in enumerate(calendar)}
    end_on = _iso(parameters["end"])
    end_index = positions[end_on]
    holding = parameters["holding_sessions"]
    entries, child, intervals = [], {}, {}
    for left, right in zip(selections_d1, selections_d2):
        for arm, selection in (("D1", left), ("D2", right)):
            entry_index = positions[selection.decision_on]
            due_index = entry_index + holding
            due_on = calendar[due_index] if due_index < len(calendar) else None
            due_state = ("outside_calendar" if due_on is None else "outside_evaluation"
                         if due_index > end_index else None)
            for code in sorted(selection.eligible_codes):
                candidate, candidate_state = None, None
                for index in range(due_index, end_index + 1):
                    state = _state(holding_inputs, code, index, observations)
                    opening = panel[code].opens[index]
                    if state in ("observed", "locked") and opening is not None and opening > 0:
                        candidate, candidate_state = calendar[index], state
                        break
                endpoint = candidate or end_on
                entries.append({
                    "formation_on": selection.formation_on.isoformat(), "arm": arm,
                    "code": code, "isin": identities[code], "entry_on": selection.decision_on.isoformat(),
                    "entry_state": _state(holding_inputs, code, entry_index, observations),
                    "due_on": due_on.isoformat() if due_on else None,
                    "due_state": due_state or _state(holding_inputs, code, due_index, observations),
                    "candidate_on": candidate.isoformat() if candidate else None,
                    "candidate_state": candidate_state, "end_on": endpoint.isoformat(),
                    "original_end_on": end_on.isoformat(),
                    "state": ("provisional_candidate" if candidate else "due_outside_calendar"
                              if due_on is None else "no_usable_opening"),
                })
                child.setdefault(code, set()).update(calendar[entry_index:positions[endpoint] + 1])
                intervals.setdefault(code, []).append((selection.decision_on, endpoint))
    result = {
        "schema": SCHEMA, "status": "provisional", "parameters": params,
        "parameters_sha256": parameter_sha,
        "calendar_dates": [day.isoformat() for day in calendar],
        "calendar_sha256": digest(json.dumps([day.isoformat() for day in calendar],
                                             separators=(",", ":")).encode()),
        "activity_snapshot_sha256": holding_inputs.activity_snapshot_sha256,
        "quote_snapshot_sha256": holding_inputs.quote_snapshot_sha256,
        "original_read_scope_sha256": holding_inputs.read_scope_sha256,
        "input_sha256": pins, "original_required_code_dates": original_dates,
        "original_requirements_sha256": digest(encoded(original_dates)),
        "issue_isins": identities, "entries": entries,
        "child_code_dates": {code: [day.isoformat() for day in sorted(days)]
                             for code, days in sorted(child.items())},
        "child_windows": _windows(intervals, identities),
        "returns_computed": False, "no_event_coverage_certified": False, "actual_exits_certified": False,
    }
    result["child_scope_sha256"] = digest(encoded(result))
    return result


def validate_exit_bounds(
    plan: dict,
    selections_d1: tuple[PartitionSelection, ...],
    selections_d2: tuple[PartitionSelection, ...],
    holding_inputs: HoldingInputs,
    parameters: Mapping,
    original_required_code_dates: Mapping[str, Sequence[str]],
    issue_isins: Mapping[str, str],
    *,
    input_sha256: Mapping[str, str],
) -> dict:
    """Recompute every entry, bound, union and pin; reject caller truncation.

    Returning the fresh projection does not upgrade provisional price endpoints
    to source coverage or actual exits. Evidence content review remains separate.
    """
    recomputed = provisional_exit_bounds(selections_d1, selections_d2, holding_inputs, parameters,
                                        original_required_code_dates, issue_isins, input_sha256=input_sha256)
    _require(type(plan) is dict and encoded(plan) == encoded(recomputed),
             "provisional exit proof disagrees with the full original package")
    return recomputed


def mixed_action_scope(plan: dict, coverage: dict) -> dict:
    """Project an already recomputed proof without shortening any event issue.

    A code with any declared event, supported-event interval or unresolved
    interval retains every original entry-through-end window. This intentionally
    conservative code-level fallback also covers independent re-entries. Other
    issues use their ordinary child windows; reviewed_actions must still verify
    exact complete inclusive source coverage before either book executes.
    No input quote row or original scope identifier is replaced.
    """
    _require(type(coverage) is dict and all(type(coverage.get(name)) is list
             for name in ("windows", "events", "unresolved")), "typed mixed action declarations required")
    full_codes = set()
    for row in coverage["events"] + coverage["unresolved"]:
        _require(type(row) is dict and type(row.get("code")) is str,
                 "mixed event or unresolved issue identity required")
        full_codes.add(row["code"])
        if type(row.get("successor_code")) is str:
            full_codes.add(row["successor_code"])
    for row in coverage["windows"]:
        _require(type(row) is dict and type(row.get("code")) is str,
                 "mixed coverage issue identity required")
        if row.get("review_state") != "reviewed_no_event":
            full_codes.add(row["code"])
    entries, intervals, codes = [], {}, set(plan["issue_isins"])
    for original in plan["entries"]:
        full = original["code"] in full_codes
        endpoint = original["original_end_on"] if full else original["end_on"]
        entries.append(dict(original, action_end_on=endpoint,
                            action_mode="original_event_or_unresolved" if full else "ordinary_child"))
        intervals.setdefault(original["code"], []).append((_iso(original["entry_on"]), _iso(endpoint)))
    windows = _windows(intervals, plan["issue_isins"])
    ordinary = codes - full_codes
    result = {"schema": "activity-mixed-action-scope-v1",
              "original_read_scope_sha256": plan["original_read_scope_sha256"],
              "child_scope_sha256": plan["child_scope_sha256"],
              "quote_snapshot_sha256": plan["quote_snapshot_sha256"],
              "entries": entries, "windows": windows,
              "ordinary_codes": sorted(ordinary), "original_end_codes": sorted(codes & full_codes)}
    # Preserve the two existing homogeneous contracts, including vacuous cash.
    result["action_scope_sha256"] = (plan["child_scope_sha256"] if not codes & full_codes else
        plan["original_read_scope_sha256"] if not ordinary else digest(encoded(result)))
    return result


def verify_ordinary_inventory(replays: dict, scope: dict) -> None:
    """Reject an actual ordinary lot outside its independently proved lifetime.

    This is a result invariant, never a forced sale or a replacement book engine.
    Event issues retain the original horizon and existing rights accounting.
    A future deferred-entry queue cannot silently reuse an earlier entry proof.
    """
    bounds = {(row["arm"], row["code"], _iso(row["entry_on"])): row
              for row in scope["entries"] if row["action_mode"] == "ordinary_child"}
    for arm, replay in replays.items():
        for book in replay.books:
            for lot in book.lots:
                if lot.code not in scope["ordinary_codes"]:
                    continue
                row = bounds.get((arm, lot.code, lot.entered_on))
                _require(row is not None and lot.due_on.isoformat() == row["due_on"],
                         "ordinary inventory lacks its exact entry/due proof")
                _require(row["candidate_on"] is None or book.as_of < _iso(row["candidate_on"]),
                         "ordinary inventory survives its validated opening exit")
