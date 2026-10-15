"""Pure conditional evidence binding for the complete BS cohort family.

The proof-only paired union is never a portfolio selection. Full original
quotes and requested absences survive unchanged; only reviewed action bounds
may shorten a potential origin's consequential lifetime. Actual input pins,
source judgments, logging and independent output verification remain caller
obligations. No input is opened and no book is executed in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from research.activity_cohort_selection import CohortSelection
from research.activity_component_bounds import validate_component_exit_bounds, verify_component_inventory
from research.activity_exit_bounds import mixed_action_scope, validate_exit_bounds, verify_ordinary_inventory
from research.activity_holding_inputs import HoldingInputs
from research.activity_pair_calibration import PARAMETERS
from research.activity_paired_replay import _json_value, _require_raw_potential_scope
from research.activity_partition_selection import PartitionSelection
from research.activity_preflight import digest, encoded, require, strict_json, _pin
from research.activity_quote_extension import CompositeHoldingInputs, _same, _validated
from research.activity_raw_episode import TARGET_CODES
from research.activity_raw_quote_view import RawEpisodeQuoteInputs, raw_price_basis
from research.activity_reviewed_actions import reviewed_action_terms, reviewed_actions


def cohort_proof_selections(declarations: tuple[CohortSelection, ...], calendar: tuple[date, ...],
                            parameters: dict) -> dict[str, tuple[PartitionSelection, ...]]:
    """Use the same full-family possible-code union in both proof arms only."""
    # Local imports avoid a module cycle; comparison never imports this module
    # until a caller explicitly supplies a conditional binding.
    from research.activity_cohort_comparison import ARMS, GROUPS, _declarations

    require(type(parameters) is dict and encoded(parameters) == encoded(PARAMETERS),
        "unchanged canonical cohort parameters required")
    require(type(calendar) is tuple, "immutable full cohort calendar required")
    keyed = _declarations(declarations, calendar, parameters)
    formations = sorted({row.formation_on for row in declarations})
    result = {arm: [] for arm in ARMS}
    for formation in formations:
        codes = tuple(sorted({code for arm in ARMS for group in GROUPS
            for code in keyed[arm, group, formation].chosen_codes}))
        for arm in ARMS:
            reference = keyed[arm, "ACTIVITY", formation]
            result[arm].append(PartitionSelection(formation, reference.decision_on, reference.selection_at,
                codes, reference.source_population_count, reference.source_partition_sha256))
    return {arm: tuple(rows) for arm, rows in result.items()}


@dataclass(frozen=True)
class CohortScopeBinding:
    """Retained immutable inputs and a recomputable conditional-scope projection."""

    declarations: tuple[CohortSelection, ...]
    original: HoldingInputs
    quotes: HoldingInputs | CompositeHoldingInputs | RawEpisodeQuoteInputs
    parameters_json: bytes
    original_requirements_json: bytes
    issue_isins_json: bytes
    input_sha256_json: bytes
    expected_pins_json: bytes
    ordinary_proof_json: bytes
    component_proof_json: bytes | None
    coverage_json: bytes
    basis_evidence_json: bytes
    scope_json: bytes
    origin_bounds: tuple[tuple[str, date, str, date, date], ...]
    events: tuple
    fingerprint_json: bytes
    binding_sha256: str


def _payload(payload: bytes, pin: str, label: str) -> dict:
    _pin(pin)
    require(type(payload) is bytes and digest(payload) == pin, f"registered {label} byte pin mismatch")
    value = strict_json(payload)
    require(type(value) is dict, f"typed {label} required")
    return value


def prepare_cohort_scope(
    declarations: tuple[CohortSelection, ...], original: HoldingInputs,
    quotes: HoldingInputs | CompositeHoldingInputs | RawEpisodeQuoteInputs,
    ordinary_exit_scope: bytes, reviewed_coverage: bytes, basis_evidence: bytes, *,
    parameters: dict, original_required_code_dates: dict, issue_isins: dict,
    input_sha256: dict, expected_pins: dict, component_exit_scope: bytes | None = None,
) -> CohortScopeBinding:
    """Recompute every family origin, snapshot, proof and final coverage before books.

    expected_pins contains exactly ordinary_exit_scope, reviewed_action_coverage,
    basis_evidence and, when supplied, component_exit_scope. The latter is the
    component proof itself, not the legacy wrapper. Original input byte checks
    precede this pure typed boundary in the caller.
    """
    from research.activity_cohort_comparison import _coverage, _events, _quotes

    require(type(original) is HoldingInputs, "full original cohort HoldingInputs required")
    _validated(original)
    base = quotes.base if type(quotes) is RawEpisodeQuoteInputs else quotes
    raw = quotes if type(quotes) is RawEpisodeQuoteInputs else None
    fresh_quotes = _quotes(base, raw)
    require(type(parameters) is dict and encoded(parameters) == encoded(PARAMETERS),
        "unchanged canonical cohort parameters required")
    require(type(original_required_code_dates) is dict and type(issue_isins) is dict
        and type(input_sha256) is dict, "explicit original scope, identities and producer pins required")
    selections = cohort_proof_selections(declarations, original.calendar, parameters)
    identities = {}
    for row in declarations:
        for code in row.chosen_codes:
            isin = dict(row.source_population)[code]
            require(code not in identities or identities[code] == isin, "cohort issue-code reuse unsupported")
            identities[code] = isin
    require(issue_isins == identities, "proof identities must match every chosen cohort issue")
    roles = {"ordinary_exit_scope", "reviewed_action_coverage", "basis_evidence"}
    if component_exit_scope is not None:
        roles.add("component_exit_scope")
    require(type(expected_pins) is dict and set(expected_pins) == roles, "exact conditional cohort byte pins required")
    supplied = _payload(ordinary_exit_scope, expected_pins["ordinary_exit_scope"], "ordinary exit scope")
    coverage = _payload(reviewed_coverage, expected_pins["reviewed_action_coverage"], "action coverage")
    evidence = _payload(basis_evidence, expected_pins["basis_evidence"], "basis evidence")
    plan = validate_exit_bounds(supplied, selections["D1"], selections["D2"], original,
        parameters, original_required_code_dates, issue_isins, input_sha256=input_sha256)
    windows = tuple(dict(code=row["code"], isin=row["isin"], start=row["entry_on"], end=row["original_end_on"])
        for row in plan["entries"])
    terms, proof = None, None
    if component_exit_scope is not None:
        supplied_component = _payload(component_exit_scope, expected_pins["component_exit_scope"], "component exit scope")
        require(type(supplied_component.get("unresolved_priority_intervals")) is list,
            "explicit component priority intervals required")
        terms = reviewed_action_terms(coverage, evidence, expected_windows=windows, calendar=original.calendar,
            activity_snapshot_sha256=original.activity_snapshot_sha256,
            quote_snapshot_sha256=fresh_quotes.quote_snapshot_sha256)
        proof = validate_component_exit_bounds(supplied_component, plan, selections["D1"], selections["D2"],
            original, parameters, original_required_code_dates, issue_isins, input_sha256=input_sha256,
            quotes=fresh_quotes, terms=terms,
            unresolved_priority_intervals=tuple(supplied_component["unresolved_priority_intervals"]))
        if raw is not None:
            _require_raw_potential_scope(fresh_quotes, proof, terms.events)
        scope = proof
        action_windows, scope_sha = tuple(proof["windows"]), proof["component_scope_sha256"]
    else:
        require(raw is None, "raw cohort projection requires a recomputed component proof")
        require(_same(fresh_quotes, original), "ordinary-only cohort scope retains its original quote projection")
        scope = mixed_action_scope(plan, coverage)
        action_windows, scope_sha = tuple(scope["windows"]), scope["action_scope_sha256"]
    events, successors = reviewed_actions(coverage, evidence, expected_windows=action_windows,
        calendar=original.calendar, activity_snapshot_sha256=original.activity_snapshot_sha256,
        quote_snapshot_sha256=fresh_quotes.quote_snapshot_sha256, read_scope_sha256=scope_sha,
        spin_off_windows=None if proof is None else tuple(proof["spin_off_windows"]))
    if terms is not None:
        require(events == terms.events, "final cohort events differ from recomputed full-window terms")
        successors = dict(terms.successor_required)
    extra = {(code, date.fromisoformat(day)) for code, days in successors.items() for day in days}
    extra.update((row["code"], date.fromisoformat(row["session"])) for row in evidence["bases"].values())
    extra.difference_update(original.requested_coordinates)
    extension = base.extension if type(base) is CompositeHoldingInputs else None
    require((not extra and extension is None) or (extension is not None
        and set(extension.requested_coordinates) == extra), "exact full successor and basis quote extension required")
    positions = {day: index for index, day in enumerate(original.calendar)}
    observations = {(row.code, row.observation_date): row for row in base.observations}
    for row in evidence["bases"].values():
        key = row["code"], date.fromisoformat(row["session"])
        if raw is not None and key[0] in TARGET_CODES:
            basis = raw_price_basis(fresh_quotes, *key)
            require(Decimal(row["raw_close"]) == Decimal(row["adjusted_close"]) == basis.raw_close,
                "cohort raw event bases require requested resolved raw/raw units")
        else:
            observation = observations.get(key)
            require(key[1] in positions and observation is not None and observation.typed_values is not None
                and not observation.issues and fresh_quotes.panel[key[0]].closes[positions[key[1]]] == Decimal(row["adjusted_close"]),
                "cohort event adjusted basis must match a requested resolved quote")
    ordered_events = _events(events, fresh_quotes, parameters, raw)
    bounds = {}
    for row in scope["entries"]:
        origin = row["origin"] if proof is not None else row
        key = origin["arm"], date.fromisoformat(origin["formation_on"]), origin["code"], date.fromisoformat(origin["entry_on"])
        require(key not in bounds, "duplicate conditional cohort origin")
        bounds[key] = date.fromisoformat(row["end_on"] if proof is not None else row["action_end_on"])
    actual_bounds = tuple(sorted((row.arm, row.formation_on, code, row.decision_on,
        bounds[row.arm, row.formation_on, code, row.decision_on]) for row in declarations for code in row.chosen_codes))
    # Different groups can share one proof origin; the immutable projection is
    # deduplicated because an origin's prices, due date and events are identical.
    actual_bounds = tuple(sorted(set(actual_bounds)))
    _coverage(declarations, fresh_quotes, ordered_events, parameters, origin_bounds=actual_bounds)
    fingerprint = encoded(dict(schema="activity-cohort-conditional-binding-v1",
        declarations_sha256=digest(encoded(_json_value(declarations))), parameters=parameters,
        original_requirements_sha256=digest(encoded(original_required_code_dates)), issue_isins=issue_isins,
        original_input_sha256=input_sha256, expected_pins=expected_pins,
        original_quote_snapshot_sha256=original.quote_snapshot_sha256,
        original_read_scope_sha256=original.read_scope_sha256,
        quote_snapshot_sha256=fresh_quotes.quote_snapshot_sha256,
        conditional_scope_sha256=scope_sha, origin_bounds=_json_value(actual_bounds)))
    return CohortScopeBinding(declarations, original, fresh_quotes, encoded(parameters),
        encoded(original_required_code_dates), encoded(issue_isins), encoded(input_sha256), encoded(expected_pins),
        ordinary_exit_scope, component_exit_scope, reviewed_coverage, basis_evidence,
        encoded(scope), actual_bounds, ordered_events, fingerprint, digest(fingerprint))


def validate_cohort_scope(binding: CohortScopeBinding, declarations: tuple[CohortSelection, ...], quotes,
                          events: tuple, *, parameters: dict) -> CohortScopeBinding:
    """Refreeze the retained inputs; a fabricated or stale binding is not trusted."""
    require(type(binding) is CohortScopeBinding, "explicit validated cohort scope binding required")
    fresh = prepare_cohort_scope(declarations, binding.original, quotes, binding.ordinary_proof_json,
        binding.coverage_json, binding.basis_evidence_json, parameters=parameters,
        original_required_code_dates=strict_json(binding.original_requirements_json),
        issue_isins=strict_json(binding.issue_isins_json), input_sha256=strict_json(binding.input_sha256_json),
        expected_pins=strict_json(binding.expected_pins_json), component_exit_scope=binding.component_proof_json)
    require(_same(binding, fresh) and events == fresh.events, "cohort scope binding differs from replay inputs")
    return fresh


def verify_cohort_inventory(binding: CohortScopeBinding, arm: str, replay) -> None:
    """Check one actual cohort against the shared potential-origin proof."""
    scope = strict_json(binding.scope_json)
    if binding.component_proof_json is None:
        verify_ordinary_inventory({arm: replay}, scope)
    else:
        verify_component_inventory({arm: replay}, scope, binding.events)
