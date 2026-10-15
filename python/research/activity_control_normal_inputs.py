"""Pure BR connection of reviewed normal windows to the complete source pool.

The caller verifies scope/source bytes, restored BI lineage and unchanged study
parameters before this seam. The numeric-possible pool and the reviewed subset
are distinct: an unreviewed row remains unresolved, including an earlier hash
candidate. Nothing here proves a complete selection prefix or successor closure,
opens a file, selects a target, reads a price, runs a book or certifies sources.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import date
from decimal import Decimal

from research.activity_control_assembly import assemble_control_window
from research.activity_normal_assembly import _day
from research.activity_partition_selection import _amount, _availability
from research.activity_screen import PublishedValue, ScreenObservation
from research.activity_timing import SessionLagPolicy


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _rows(rows: object, formations: set[str], label: str) -> dict:
    _require(type(rows) is list, f"explicit {label} records required")
    keyed = {}
    for row in rows:
        _require(type(row) is dict and row.get("formation") in formations
                 and type(row.get("code")) is str and bool(row["code"])
                 and type(row.get("isin")) is str and bool(row["isin"]),
                 f"{label} formation/code/ISIN mismatch")
        key = row["formation"], row["code"]
        _require(key not in keyed, f"duplicate {label} window")
        keyed[key] = row
    return keyed


def assemble_control_normal_inputs(
    calendar: tuple[date, ...], scope: dict, population_audit: dict,
    accepted_records: list[dict], observations: Mapping[str, Mapping[date, ScreenObservation]],
    potential_sources: Mapping[tuple[str, str], dict], params: dict,
) -> dict:
    """Assemble every accepted exact window without dropping unknown pool rows.

    ``scope`` is the verified BR rank-metadata result; ``population_audit`` is
    the complete dated BL source audit, not its 36 surviving activity windows.
    ``potential_sources`` is the exact reviewed subset returned by the saved
    KRX source loader. Only the caller defines and pins its actual read scope.
    This result reports normal-input dispositions, never a complete comparator.
    """
    _require(type(calendar) is tuple, "immutable complete calendar required")
    SessionLagPolicy._calendar(calendar)
    threshold = params.get("threshold") if type(params) is dict else None
    _require(type(params) is dict and type(params.get("lookback")) is int and params["lookback"] == 60
             and not isinstance(threshold, bool) and isinstance(threshold, (int, Decimal))
             and (not isinstance(threshold, Decimal) or threshold.is_finite()) and threshold == 3,
             "unchanged exact normal-screen parameters required")
    end = _day(params.get("end"), "evaluation end")
    _require(end in calendar, "evaluation end outside supplied calendar")
    indices = SessionLagPolicy(1, paired=True).formation_indices(calendar,
        lookback=params["lookback"], holding_sessions=params["holding_sessions"], end_index=calendar.index(end))
    expected = [calendar[index].strftime("%Y%m%d") for index in indices]
    _require(type(scope) is dict and scope.get("schema") == "activity-control-scope-v1"
             and scope.get("status") == "complete_control_rank_metadata_planned"
             and type(scope.get("formations")) is list and type(scope.get("paired_schedule")) is list,
             "verified BR numeric rank-metadata scope required")
    formations = scope["formations"]
    _require([row["formation"] for row in formations] == expected
             == [row["formation"] for row in scope["paired_schedule"]], "complete ordered formation grid required")
    cutoffs = {}
    for index, row, scheduled in zip(indices, formations, scope["paired_schedule"], strict=True):
        _require(row["arms"] == scheduled["arms"], "scope paired schedule mismatch")
        arms = row["arms"]
        _require(type(arms) is list and len(arms) == 2, "both paired timing arms required")
        values = []
        for arm, lag in zip(arms, (1, 2), strict=True):
            policy = SessionLagPolicy(lag, paired=True)
            cutoff = policy.selection_at(calendar, index)
            _require(arm.get("session_lag") == lag and arm.get("execution_on") == policy.execution_on(calendar, index).isoformat()
                     and arm.get("selection_at") == cutoff.isoformat(), "formation timing mismatch")
            values.append(cutoff)
        cutoffs[row["formation"]] = tuple(values)
    _require(type(population_audit) is dict and population_audit.get("schema") == "activity-normal-population-audit-v1"
             and type(population_audit.get("availability_metadata")) is dict, "complete source population audit required")
    population = _rows(population_audit.get("rows"), set(expected), "source population")
    partition = _rows(scope.get("population_partition"), set(expected), "scope partition")
    _require(population.keys() == partition.keys() and scope["summary"]["source_rows"] == len(population),
             "complete source population/partition mismatch")
    metadata = population_audit["availability_metadata"]
    allowed = {"cap_below", "negative_type", "bh_failure", "formation_frozen", "numeric_possible"}
    for key, source in population.items():
        prior = partition[key]
        _require(prior["isin"] == source["isin"] and prior["prior_disposition"] == source["disposition"]
                 and prior["control_disposition"] in allowed, "source/partition identity or disposition mismatch")
        formation = date.fromisoformat(key[0])
        for field in ("capitalization_availability", "classification_availability", "trade_section_availability"):
            reference = source[field]
            _require(reference in metadata, "unresolved full-population availability reference")
            _availability(metadata[reference], formation, cutoffs[key[0]])
    possible = {}
    for formation in formations:
        rows = formation["ranked_numeric_possible"]
        _require(type(rows) is list and [row["rank"] for row in rows] == list(range(1, len(rows) + 1)),
                 "complete ordered numeric rank metadata required")
        for key, row in _rows(rows, {formation["formation"]}, "numeric possible").items():
            source = population.get(key)
            _require(source is not None and row["isin"] == source["isin"]
                     and row["krx_metadata_provenance"] == source["krx_metadata_provenance"]
                     and row["listing_date"] == source["listing_date"], "numeric/source identity provenance mismatch")
            possible[key] = row
    _require(set(possible) == {key for key, row in partition.items() if row["control_disposition"] == "numeric_possible"}
             and scope["summary"]["numeric_possible_windows"] == len(possible), "numeric possible pool mismatch")
    _require(type(accepted_records) is list and isinstance(observations, Mapping)
             and isinstance(potential_sources, Mapping), "explicit accepted records and restored source mappings required")
    records, accepted = {}, {}
    for record in accepted_records:
        _require(type(record) is dict and record.get("decision") == "accepted" and record.get("code") not in records
                 and type(record.get("required_windows")) is list and bool(record["required_windows"]),
                 "unique positive bounded accepted records required")
        records[record["code"]] = record
        for window in record["required_windows"]:
            formation = _day(window.get("formation"), "accepted formation")
            key = formation.strftime("%Y%m%d"), record["code"]
            _require(key in possible and key not in accepted, "accepted window outside numeric pool or duplicated")
            accepted[key] = window
    _require(set(potential_sources) == set(accepted), "exact accepted/source window subset required")
    audits, evaluated = [], {}
    for key, window in sorted(accepted.items()):
        candidate, population_row, source = possible[key], population[key], potential_sources[key]
        _require(type(source) is dict and source.get("isin") == window.get("isin") == candidate["isin"]
                 and source.get("krx_metadata_provenance") == window.get("krx_metadata_provenance")
                 == population_row["krx_metadata_provenance"], "accepted/source exact issue provenance mismatch")
        _require(window.get("required_dates") == candidate["required_dates"]
                 and type(source.get("listing_date")) is date
                 and source["listing_date"].isoformat() == candidate["listing_date"]
                 and isinstance(source.get("capitalization"), PublishedValue)
                 and source["capitalization"].value == _amount(population_row["capitalization"]),
                 "accepted/source dates, listing or capitalization mismatch")
        dates = tuple(_day(value, "required observation") for value in candidate["required_dates"])
        rows = observations.get(key[1])
        _require(isinstance(rows, Mapping) and all(day in rows for day in dates), "missing restored accepted activity observation")
        result = assemble_control_window(calendar, date.fromisoformat(key[0]), key[1], candidate["isin"],
            source["listing_date"], {day: rows[day] for day in dates}, source["capitalization"], source["liquidity"],
            records[key[1]], params)
        for arm in result["activity_screen"]["arms"].values():
            _require(arm["normal_dates"] == candidate["normal_dates"]
                     and arm["skipped_frozen_dates"] == candidate["frozen_dates"], "restored normal/frozen dates differ from scope metadata")
        result.update(formation=date.fromisoformat(key[0]).isoformat(), code=key[1], isin=candidate["isin"],
                      formation_source_provenance=deepcopy(source["source_provenance"]))
        evaluated[key] = result
        audits.append(result)
    dispositions = []
    for key, row in sorted(partition.items()):
        if key in evaluated:
            arm = evaluated[key]["control_arms"]["D1"]
            state, reasons = ("eligible" if arm["eligible"] else "excluded"), arm["exclusion_reasons"]
        elif key in possible:
            state, reasons = "unresolved", None
        else:
            state, reasons = "excluded", [row["control_disposition"]]
        dispositions.append({**deepcopy(row), "normal_input_disposition": state, "normal_input_exclusion_reasons": deepcopy(reasons)})
    return {"status": "reviewed_normal_subset_assembled", "population_audit": deepcopy(population_audit),
        "normal_input_audit": audits, "dispositions": dispositions,
        "summary": {"source_rows": len(population), "numeric_possible_windows": len(possible),
                    "reviewed_normal_windows": len(audits), "unreviewed_numeric_possible_windows": len(possible) - len(audits)},
        "classification_evidence_level": "inferred", "historical_publication_evidence_level": "assumed",
        "complete_control_prefix_proved": False, "compulsory_successor_closure_complete": False,
        "holding_selection_executed": False, "returns_computed": False, "promotion_allowed": False}
