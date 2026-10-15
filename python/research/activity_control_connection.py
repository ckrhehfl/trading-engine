"""Pure BR counted declarations and binding to the original nominee diagnostic.

The caller pins and independently verifies the BR outputs, original ACTIVITY
targets and reviewed successor declarations before this seam. No IO, source
judgment, successor inference, action application, replay or returns occur here.
Unknown prefix rows and origins outside the saved nominee union require another
registered scope; an unknown tail remains part of the complete population.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date

from research.activity_control_inputs_preflight import nominee_selections
from research.activity_control_selection import (
    CountedControlSelection, _rank, activity_targets_fingerprint, population_fingerprint,
)
from research.activity_exit_bounds import validate_exit_bounds
from research.activity_holding_inputs import HoldingInputs
from research.activity_holding_restore import restore_holding_inputs
from research.activity_pair_calibration import PARAMETERS
from research.activity_paired_replay import _json_value
from research.activity_partition_selection import PartitionSelection
from research.activity_preflight import digest, encoded, require, _pin
from research.activity_timing import SessionLagPolicy


ARMS = ("D1", "D2")


@dataclass(frozen=True)
class ControlBoundDeclaration:
    """Caller-reviewed additions become the NEXT formation's retained bound.

    An empty successor_codes still needs an evidence pin. The pin identifies
    the caller's reviewed declaration, not a machine certificate of closure.
    Every possible retained/pending descendant must already be included there.
    """

    formation_on: date
    prior_potential_codes: frozenset[str]
    successor_codes: frozenset[str]
    evidence_sha256: str


@dataclass(frozen=True)
class BoundControlInputs:
    """Final counted declarations with the unchanged conservative evidence scope."""

    selections_d1: tuple[CountedControlSelection, ...]
    selections_d2: tuple[CountedControlSelection, ...]
    nominees_d1: tuple[PartitionSelection, ...]
    nominees_d2: tuple[PartitionSelection, ...]
    holding_inputs: HoldingInputs
    original_required_code_dates: dict[str, list[str]]
    original_windows: tuple[dict, ...]
    provisional_exit_scope: dict


def _keyed(rows: object, *, iso: bool = False) -> dict:
    require(type(rows) is list, "explicit saved rows required")
    result = {}
    for row in rows:
        require(type(row) is dict and type(row.get("formation")) is str
                and type(row.get("code")) is str and bool(row["code"])
                and type(row.get("isin")) is str and bool(row["isin"]),
                "exact saved formation/code/ISIN required")
        formation = date.fromisoformat(row["formation"])
        expected = formation.isoformat() if iso else formation.strftime("%Y%m%d")
        require(row["formation"] == expected, "canonical saved formation required")
        key = formation, row["code"]
        require(key not in result, "duplicate saved formation/code")
        result[key] = row
    return result


def _indices(calendar: tuple[date, ...], parameters: dict) -> tuple[int, ...]:
    require(type(calendar) is tuple and type(parameters) is dict,
            "immutable calendar and explicit unchanged parameters required")
    SessionLagPolicy._calendar(calendar)
    for key in ("lookback", "holding_sessions", "slots", "seed"):
        require(type(parameters.get(key)) is int and parameters[key] == PARAMETERS[key],
                "unchanged control geometry required")
    end = date.fromisoformat(parameters["end"])
    require(end.isoformat() == parameters["end"] and end in calendar,
            "canonical evaluation end in calendar required")
    return tuple(SessionLagPolicy(1, paired=True).formation_indices(calendar,
        lookback=parameters["lookback"], holding_sessions=parameters["holding_sessions"],
        end_index=calendar.index(end)))


def counted_selections_from_br(
    calendar: tuple[date, ...], parameters: dict, scope: dict, population_audit: dict,
    dispositions: list[dict], normal_audits: list[dict],
    bounds: Mapping[str, tuple[ControlBoundDeclaration, ...]],
) -> dict[str, tuple[CountedControlSelection, ...]]:
    """Resolve the whole-source hash prefix using only supplied reviewed evidence.

    Scope/BR package bytes and original target links are caller obligations.
    Prior exclusions retain their proof rows. Reviewed numeric possibilities
    require their matching normal audit; all others remain unresolved. U_prior
    is fixed for each formation, and its exact successor-aware U_post must be
    the next supplied U_prior. Initial normalized books are cash-only.
    """
    indices = _indices(calendar, parameters)
    formations = tuple(calendar[index] for index in indices)
    positions = dict(zip(formations, indices, strict=True))
    require(type(scope) is dict and type(scope.get("formations")) is list
            and tuple(date.fromisoformat(row["formation"]) for row in scope["formations"]) == formations,
            "complete ordered BR formation scope required")
    require(type(population_audit) is dict
            and population_audit.get("schema") == "activity-normal-population-audit-v1",
            "complete BR population audit required")
    population = _keyed(population_audit.get("rows"))
    partition, audited = _keyed(dispositions), _keyed(normal_audits, iso=True)
    original = _keyed(scope.get("population_partition"))
    require(population.keys() == partition.keys() == original.keys()
            and all(key[0] in formations for key in population)
            and scope["summary"]["source_rows"] == len(population),
            "complete source population/partition required")
    require(list(partition) == sorted(partition) and audited.keys() <= partition.keys(),
            "ordered complete dispositions and bounded normal audits required")
    allowed_exclusions = {"cap_below", "negative_type", "bh_failure", "formation_frozen"}
    for key, row in partition.items():
        prior, source = original[key], population[key]
        require(row["isin"] == prior["isin"] == source["isin"]
                and row.get("prior_disposition") == prior["prior_disposition"] == source["disposition"]
                and row.get("control_disposition") == prior["control_disposition"],
                "BR disposition/source identity mismatch")
        state, reasons = row.get("normal_input_disposition"), row.get("normal_input_exclusion_reasons")
        if key in audited:
            audit = audited[key]
            arms = audit.get("control_arms")
            require(row["control_disposition"] == "numeric_possible" and audit["isin"] == row["isin"]
                    and type(arms) is dict and set(arms) == set(ARMS)
                    and all(type(arms[arm].get("eligible")) is bool
                            and type(arms[arm].get("exclusion_reasons")) is list for arm in ARMS),
                    "reviewed control normal audit required")
            left, right = arms["D1"], arms["D2"]
            require((left["eligible"], left["exclusion_reasons"]) == (right["eligible"], right["exclusion_reasons"])
                    and left["eligible"] == (not left["exclusion_reasons"])
                    and state == ("eligible" if left["eligible"] else "excluded")
                    and reasons == left["exclusion_reasons"], "normal audit/disposition mismatch")
            for arm, lag in (("D1", 1), ("D2", 2)):
                policy = SessionLagPolicy(lag, paired=True)
                require(arms[arm].get("selection_at") == policy.selection_at(calendar, positions[key[0]]).isoformat()
                        and arms[arm].get("decision_on") == policy.execution_on(calendar, positions[key[0]]).isoformat(),
                        "normal audit paired timing mismatch")
        elif row["control_disposition"] == "numeric_possible":
            require(state == "unresolved" and reasons is None,
                    "unreviewed numeric candidate must remain unresolved")
        else:
            require(row["control_disposition"] in allowed_exclusions and state == "excluded"
                    and reasons == [row["control_disposition"]], "retained non-activity proof required")
    require(isinstance(bounds, Mapping) and set(bounds) == set(ARMS), "both explicit successor-aware bounds required")
    result = {}
    for arm, lag in (("D1", 1), ("D2", 2)):
        declarations = bounds[arm]
        require(type(declarations) is tuple and len(declarations) == len(formations),
                "one immutable bound per formation/arm required")
        previous_post, selections = frozenset(), []
        for index, formation, scoped, bound in zip(indices, formations, scope["formations"], declarations, strict=True):
            require(type(bound) is ControlBoundDeclaration and bound.formation_on == formation,
                    "bound formation mismatch")
            _pin(bound.evidence_sha256)
            for codes in (bound.prior_potential_codes, bound.successor_codes):
                require(type(codes) is frozenset and all(type(code) is str and code and code == code.strip() for code in codes),
                        "immutable explicit retained/successor codes required")
            require(bound.prior_potential_codes == previous_post,
                    "fixed U_prior must equal prior successor-aware U_post")
            rows = [row for key, row in partition.items() if key[0] == formation]
            identities = tuple(sorted((code, row["isin"]) for (day, code), row in population.items() if day == formation))
            by_code = {row["code"]: row for row in rows}
            targets = scoped["activity_preopening_targets"][arm]
            require(type(targets) is list and scoped["activity_preopening_k"][arm] == len(targets),
                    "original pre-opening target ceiling mismatch")
            prefix, outside = [], 0
            if targets:
                for code in _rank(by_code, formation, parameters["seed"]):
                    row, key = by_code[code], (formation, code)
                    state = row["normal_input_disposition"]
                    require(state != "unresolved", f"new scope required: unresolved control prefix {arm}/{formation}/{code}")
                    evidence = digest(encoded({"disposition": row, "normal_input_audit": audited.get(key)}))
                    prefix.append((code, state, evidence))
                    outside += state == "eligible" and code not in bound.prior_potential_codes
                    if outside == len(targets):
                        break
            policy = SessionLagPolicy(lag, paired=True)
            decision, cutoff = policy.execution_on(calendar, index), policy.selection_at(calendar, index)
            selected = CountedControlSelection(arm, formation, decision, cutoff, parameters["seed"], tuple(targets),
                activity_targets_fingerprint(arm=arm, formation_on=formation, decision_on=decision,
                    selection_at=cutoff, targets=tuple(targets), seed=parameters["seed"]),
                identities, len(identities), population_fingerprint(identities), digest(encoded(rows)),
                bound.prior_potential_codes, tuple(prefix))
            selections.append(selected)
            previous_post = bound.prior_potential_codes | selected.possible_origin_codes | bound.successor_codes
        result[arm] = tuple(selections)
    return result


def bind_control_holding_scope(
    selections: Mapping[str, tuple[CountedControlSelection, ...]], *,
    calendar: tuple[date, ...], parameters: dict, scope: dict, dispositions: list[dict],
    normal_audits: list[dict], holding_audit: dict, read_scope: dict,
    quote_snapshot_sha256: str, activity_snapshot_sha256: str, read_scope_sha256: str,
    provisional_exit_scope: dict, producer_input_sha256: dict[str, str],
) -> BoundControlInputs:
    """Bind final possible origins to the unchanged paired nominee quote/proof union.

    Holding/exit helpers require identical paired evidence pools; final counted
    pools can differ with k and U. Preserve the original nominee projections for
    those helpers and require each counted origin to fit its exact formation/arm.
    Final action coverage, successor quotes/units and replay remain separate.
    """
    indices = _indices(calendar, parameters)
    require(isinstance(selections, Mapping) and set(selections) == set(ARMS), "both final counted arms required")
    nominees, requested, identities = nominee_selections(normal_audits, scope, calendar, parameters, dispositions)
    require(read_scope.get("producer_schema") == "activity-control-inputs-v1"
            and read_scope.get("scope_kind") == "reviewed_nominee_original_issue_union_not_final_control_or_successor_closure"
            and read_scope.get("requested_code_dates") == requested
            and read_scope.get("input_sha256") == producer_input_sha256
            and read_scope.get("normal_input_audit_sha256") == producer_input_sha256.get("normal_input_audit")
            == digest(encoded(normal_audits)), "unchanged BR nominee read scope required")
    require(all(all(row["control_arms"][arm]["eligible"] is True for arm in ARMS) for row in normal_audits),
            "new scope required: reviewed nominee failed normal predicates")
    for arm, lag in (("D1", 1), ("D2", 2)):
        final = selections[arm]
        require(type(final) is tuple and len(final) == len(indices), "whole final counted formation schedule required")
        for index, selected, nominee in zip(indices, final, nominees[arm], strict=True):
            require(isinstance(selected, CountedControlSelection) and selected.arm == arm
                    and selected.formation_on == nominee.formation_on
                    and selected.decision_on == nominee.decision_on and selected.selection_at == nominee.selection_at
                    and selected.source_population_count == nominee.source_population_count
                    and selected.source_partition_sha256 == nominee.source_partition_sha256,
                    "counted/nominee population or timing mismatch")
            require(set(selected.eligible_codes) <= set(nominee.eligible_codes),
                    f"new scope required: final control origin outside nominees {arm}/{calendar[index]}")
            require(all(dict(selected.source_population)[code] == identities[code] for code in selected.eligible_codes),
                    "counted/nominee issue identity mismatch")
    quotes = restore_holding_inputs(holding_audit, read_scope, quote_snapshot_sha256=quote_snapshot_sha256,
        activity_snapshot_sha256=activity_snapshot_sha256, read_scope_sha256=read_scope_sha256)
    plan = validate_exit_bounds(provisional_exit_scope, nominees["D1"], nominees["D2"], quotes,
        parameters, requested, identities, input_sha256=producer_input_sha256)
    windows = tuple({"code": row["code"], "isin": row["isin"], "start": row["entry_on"],
                     "end": row["original_end_on"]} for row in plan["entries"])
    return BoundControlInputs(selections["D1"], selections["D2"], nominees["D1"], nominees["D2"],
        quotes, requested, windows, plan)


def serialize_counted_selections(selections: Mapping[str, tuple[CountedControlSelection, ...]]) -> dict:
    """Retain exact declarations, deterministically spelling frozen code sets."""
    require(isinstance(selections, Mapping) and set(selections) == set(ARMS), "both counted arms required")
    result = {}
    for arm in ARMS:
        require(type(selections[arm]) is tuple and all(isinstance(row, CountedControlSelection)
                and row.arm == arm for row in selections[arm]), "immutable counted declarations required")
        result[arm] = []
        for row in selections[arm]:
            value = asdict(row)
            value["prior_potential_codes"] = sorted(row.prior_potential_codes)
            result[arm].append(_json_value(value))
    return result
