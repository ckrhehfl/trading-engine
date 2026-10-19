"""Pure TREND cohorts on the explicitly approved saved normal-window subset.

The original six-group BS declarations remain strict and independently checked.
Reduced-pool hash reselection must reproduce every original control target in
its original order. This establishes a structural reuse condition, not source
truth, a whole-universe result, a power pass or new independent market samples.
No input loading, logging, database access or actual-output certification occurs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import localcontext

from research import activity_cohort_comparison as cohort
from research.activity_cohort_connection import (
    CohortScopeBinding, _validate_cohort_scope, verify_cohort_inventory,
)
from research.activity_cohort_return_policy import SCHEMA_V3, decode_return_policy, policy_report
from research.activity_cohort_selection import CohortSelection, CONTROL_IDS, rank_control_codes
from research.activity_pair_calibration import PARAMETERS
from research.activity_partition_selection import PartitionSelection
from research.activity_preflight import digest, encoded, require, strict_json
from research.activity_quote_extension import _same
from research.activity_raw_quote_view import _prepare_raw_quote_context, _immutable_fields, _unchanged_fields
from research.activity_replay import replay_synthetic, _replay_synthetic_from_context
from research.activity_timing import SessionLagPolicy
from research.price_trend_signal import PriceTrendObservation, PriceTrendSelection, select_price_trend


GROUP = "TREND"


def _day(value: object) -> date:
    require(type(value) is str, "explicit saved date text required")
    return date.fromisoformat(value)


def _parameters(parameters: dict) -> None:
    require(type(parameters) is dict and encoded(parameters) == encoded(PARAMETERS),
        "unchanged canonical trend parameters required")


@dataclass(frozen=True)
class CertifiedTrendUniverse:
    """Retain the full source metadata and only the approved dated candidate rows.

    Each ``windows`` row is formation, code, ISIN and exactly sixty preceding
    normal dates. Zero-budget formations deliberately have no candidate rows.
    ``reuse_identity`` binds this consistency proof, not any saved control book.
    """

    frontier_json: bytes
    original_declarations: tuple[CohortSelection, ...]
    calendar: tuple[date, ...]
    windows: tuple[tuple[date, str, str, tuple[date, ...]], ...]
    reuse_identity: str
    old_control_targets_unchanged: bool = field(default=True, init=False)
    whole_source_population_complete: bool = field(default=False, init=False)
    source_truth_certified: bool = field(default=False, init=False)

    @property
    def candidate_window_count(self) -> int:
        return len(self.windows)

    @property
    def required_price_coordinates(self) -> tuple[tuple[str, date], ...]:
        return tuple(sorted({(code, day) for formation, code, _, normal in self.windows
            for day in (normal[0], formation)}))


def derive_certified_trend_universe(frontier: dict, original_declarations: tuple[CohortSelection, ...],
                                   calendar: tuple[date, ...], *, parameters: dict) -> CertifiedTrendUniverse:
    """Fix the saved eligible/exact-lookback subset and prove old target reuse.

    Unresolved and baseline-less original rows remain in ``frontier_json``;
    they are outside this explicitly restricted study, never relabeled excluded.
    Original target membership and exact reduced-pool hash order are mandatory.
    """
    _parameters(parameters)
    require(type(calendar) is tuple, "immutable complete trend calendar required")
    SessionLagPolicy._calendar(calendar)
    keyed = cohort._declarations(original_declarations, calendar, parameters)
    require(type(frontier) is dict and frontier.get("schema") == "activity-cohort-scope-v1"
        and frontier.get("complete_target_prefix") is True, "completed saved original frontier required")
    require(all(type(frontier.get(name)) is list for name in (
        "formations", "population_partition", "needed_normal_windows")), "explicit complete frontier lists required")
    formations = sorted({row.formation_on for row in original_declarations})
    declared = {}
    for row in frontier["formations"]:
        require(type(row) is dict, "typed frontier formation required")
        formation = _day(row.get("formation"))
        require(formation in formations and formation not in declared, "duplicate or unknown frontier formation")
        require(type(row.get("ACTIVITY")) is dict and set(row["ACTIVITY"]) == set(cohort.ARMS),
            "paired original activity frontier declarations required")
        for arm in cohort.ARMS:
            old = keyed[arm, "ACTIVITY", formation]
            value = row["ACTIVITY"][arm]
            require(type(value) is dict and type(value.get("target_ceiling")) is int
                and value["target_ceiling"] == old.target_ceiling
                and type(value.get("source_population_count")) is int
                and value["source_population_count"] == old.source_population_count,
                "frontier budget or complete source count changed")
        declared[formation] = row
    require(set(declared) == set(formations), "complete original formation frontier required")
    expected_populations = {formation: dict(keyed["D1", "ACTIVITY", formation].source_population)
        for formation in formations}
    population = {}
    for row in frontier["population_partition"]:
        require(type(row) is dict, "typed frontier population row required")
        key = _day(row.get("formation")), row.get("code")
        require(type(key[1]) is str and key[0] in declared and key not in population,
            "unique dated source candidate required")
        expected = expected_populations[key[0]]
        require(key[1] in expected and row.get("isin") == expected[key[1]]
            and row.get("normal_input_disposition") in ("eligible", "excluded", "unresolved"),
            "exact source candidate identity and explicit disposition required")
        population[key] = row
    require(set(population) == {(formation, code) for formation in formations
        for code, _ in keyed["D1", "ACTIVITY", formation].source_population},
        "complete original source population must remain present")
    normal_windows = {}
    calendar_days = set(calendar)
    for row in frontier["needed_normal_windows"]:
        require(type(row) is dict, "typed retained normal window required")
        key = _day(row.get("formation")), row.get("code")
        require(type(key[1]) is str and key in population and key not in normal_windows,
            "unique normal window joined to original population required")
        require(row.get("isin") == population[key]["isin"]
            and row.get("normal_input_disposition") == population[key]["normal_input_disposition"]
            and type(row.get("normal_dates")) is list, "normal window identity/disposition mismatch")
        normal = tuple(_day(day) for day in row["normal_dates"])
        require(len(normal) <= parameters["lookback"] and normal == tuple(sorted(set(normal)))
            and all(day < key[0] and day in calendar_days for day in normal),
            "ordered preceding normal dates within the original calendar required")
        normal_windows[key] = normal
    windows = tuple((formation, code, population[formation, code]["isin"], normal)
        for (formation, code), normal in sorted(normal_windows.items())
        if keyed["D1", "ACTIVITY", formation].target_ceiling
        and population[formation, code]["normal_input_disposition"] == "eligible"
        and len(normal) == parameters["lookback"])
    for formation in formations:
        pool = tuple(code for day, code, _, _ in windows if day == formation)
        for arm in cohort.ARMS:
            for group in cohort.GROUPS:
                old = keyed[arm, group, formation]
                require(set(old.chosen_codes) <= set(pool),
                    "every old group target must belong to the restricted certified pool")
                if group in CONTROL_IDS:
                    require(rank_control_codes(pool, control_id=group, formation_on=formation)[:old.target_ceiling]
                        == old.chosen_codes, "reduced-pool hash reselection changed an original control target or order")
    frontier_json = encoded(frontier)
    identity = digest(encoded({"schema": "price-trend-certified-universe-v1",
        "frontier_sha256": digest(frontier_json), "windows": [
            [formation.isoformat(), code, isin, [day.isoformat() for day in normal]]
            for formation, code, isin, normal in windows],
        "original_targets": [[row.arm, row.group_id, row.formation_on.isoformat(),
            row.activity_targets_sha256, list(row.chosen_codes), row.source_partition_sha256,
            row.source_population_sha256] for row in sorted(original_declarations, key=cohort._key)]}))
    return CertifiedTrendUniverse(frontier_json, original_declarations, calendar, windows, identity)


def _universe(universe: CertifiedTrendUniverse, parameters: dict) -> CertifiedTrendUniverse:
    require(type(universe) is CertifiedTrendUniverse, "explicit certified-subset declaration required")
    fresh = derive_certified_trend_universe(strict_json(universe.frontier_json), universe.original_declarations,
        universe.calendar, parameters=parameters)
    require(_same(fresh, universe), "certified-subset declaration changed")
    return fresh


@dataclass(frozen=True)
class TrendCohortDeclaration:
    """An explicit TREND contract, never an ACTIVITY or BS control declaration."""

    arm: str
    formation_on: date
    decision_on: date
    selection_at: datetime
    original_activity_targets: tuple[str, ...]
    source_population: tuple[tuple[str, str], ...]
    source_population_count: int
    source_population_sha256: str
    source_partition_sha256: str
    selection: PriceTrendSelection
    observations: tuple[PriceTrendObservation, ...]
    group_id: str = field(default=GROUP, init=False)

    @property
    def activity_targets(self) -> tuple[str, ...]:
        """Original acquisition opportunity reference used by shared reporting."""
        return self.original_activity_targets

    @property
    def target_ceiling(self) -> int:
        return len(self.original_activity_targets)

    @property
    def chosen_codes(self) -> tuple[str, ...]:
        return self.selection.chosen_codes

    @property
    def eligible_shortfall(self) -> int:
        return self.selection.eligible_shortfall


def build_trend_declarations(universe: CertifiedTrendUniverse, observations: tuple[PriceTrendObservation, ...], *,
                             parameters: dict) -> tuple[TrendCohortDeclaration, ...]:
    """Recompute each arm's selection using only its formation's exact inputs."""
    universe = _universe(universe, parameters)
    require(type(observations) is tuple and all(type(row) is PriceTrendObservation for row in observations),
        "immutable typed complete trend observations required")
    quotes = {}
    for row in observations:
        row.__post_init__()
        key = row.code, row.metadata.observation_date
        require(key not in quotes, "duplicate global trend price coordinate")
        quotes[key] = row
    require(set(quotes) == set(universe.required_price_coordinates),
        "exact complete restricted baseline/formation quote union required")
    results = []
    for old in sorted((row for row in universe.original_declarations if row.group_id == "ACTIVITY"),
            key=cohort._key):
        windows = tuple(row for row in universe.windows if row[0] == old.formation_on)
        expected = tuple((code, isin) for _, code, isin, _ in windows)
        baselines = tuple((code, normal[0]) for _, code, _, normal in windows)
        coordinates = {(code, day) for code, baseline in baselines for day in (baseline, old.formation_on)}
        consumed = tuple(quotes[key] for key in sorted(coordinates))
        selection = select_price_trend(expected_candidates=expected, expected_baseline_on=baselines,
            formation_on=old.formation_on, selection_at=old.selection_at,
            target_count=old.target_ceiling, observations=consumed)
        results.append(TrendCohortDeclaration(old.arm, old.formation_on, old.decision_on, old.selection_at,
            old.activity_targets, old.source_population, old.source_population_count, old.source_population_sha256,
            old.source_partition_sha256, selection, consumed))
    return tuple(results)


def _declarations(rows: tuple[TrendCohortDeclaration, ...], universe: CertifiedTrendUniverse, parameters: dict) -> tuple:
    require(type(rows) is tuple and all(type(row) is TrendCohortDeclaration for row in rows),
        "explicit immutable TREND declarations required")
    quotes = {}
    for row in rows:
        require(type(row.observations) is tuple and all(type(item) is PriceTrendObservation for item in row.observations),
            "immutable retained trend observations required")
        for quote in row.observations:
            key = quote.code, quote.metadata.observation_date
            require(key not in quotes or _same(quotes[key], quote), "inconsistent repeated trend observation")
            quotes[key] = quote
    expected = build_trend_declarations(universe, tuple(quotes[key] for key in sorted(quotes)), parameters=parameters)
    require(_same(tuple(sorted(rows, key=cohort._key)), expected), "TREND declaration or fixed selection changed")
    return expected


def _attribution(replay, row, quotes, parameters, *, events, return_policy_json):
    from research.activity_cohort_return_attribution import attribute_selected_issuer_returns
    selected = PartitionSelection(row.formation_on, row.decision_on, row.selection_at,
        row.chosen_codes, row.source_population_count, row.source_partition_sha256)
    return attribute_selected_issuer_returns(replay, selected, row.target_ceiling, quotes, parameters,
        events=events, return_policy_json=return_policy_json)


def replay_price_trend_cohorts(declarations: tuple[TrendCohortDeclaration, ...], holding_inputs, events: tuple, *,
                              universe: CertifiedTrendUniverse, parameters: dict,
                              raw_quote_view=None, scope_binding=None,
                              before_cohort=None, on_cohort=None) -> dict:
    """Validate the restricted family, then reuse the unchanged cohort/book loop.

    A supplied BS binding is revalidated against its complete original family.
    Every TREND origin must already be covered by that exact conditional proof.
    Its versioned five-issuer price-return policy is retained verbatim. No old
    control books are rerun and all actual evidence/readiness flags stay false.
    Optional caller callbacks may perform IO: ``before_cohort(row)`` precedes
    the book and ``on_cohort(result)`` follows it. Their failures propagate;
    callbacks may not mutate the sealed inputs or canonical parameters.
    """
    _parameters(parameters)
    require(all(callback is None or callable(callback) for callback in (before_cohort, on_cohort)),
        "cohort callbacks must be callable or absent")
    universe = _universe(universe, parameters)
    rows = _declarations(declarations, universe, parameters)
    raw_context = None
    if raw_quote_view is None:
        quotes = cohort._quotes(holding_inputs, None)
    else:
        raw_context = _prepare_raw_quote_context(raw_quote_view)
        quotes, _ = raw_context[1](raw_context[0])
        require(_same(quotes.base, holding_inputs), "trend raw view must preserve the complete supplied holding base")
    require(quotes.calendar == universe.calendar, "trend and original holding calendars differ")
    ordered_events = cohort._events(events, quotes, parameters, raw_quote_view)
    binding, policy_json = None, None
    if scope_binding is not None:
        require(type(scope_binding) is CohortScopeBinding
            and _same(scope_binding.declarations, universe.original_declarations),
            "trend proof must retain the exact original BS family")
        binding = _validate_cohort_scope(scope_binding, universe.original_declarations, quotes, ordered_events,
            parameters=parameters, _raw_context=raw_context)
        policy_json = binding.return_policy_json
        policy = decode_return_policy(policy_json)
        require(policy is not None and policy["schema"] == SCHEMA_V3,
            "unchanged approved five-issuer price-return policy required")
        bounds = {row[:4]: row[4] for row in binding.origin_bounds}
        required = {(row.arm, row.formation_on, code, row.decision_on) for row in rows for code in row.chosen_codes}
        require(required <= set(bounds), "TREND origin is outside the original conditional proof union")
        cohort._coverage(rows, quotes, ordered_events, parameters,
            origin_bounds=tuple(sorted((*key, bounds[key]) for key in required)))
        cohort.require_supported_attribution_actions(ordered_events, return_policy_json=policy_json)
    else:
        cohort._coverage(rows, quotes, ordered_events, parameters)
    end = universe.calendar.index(date.fromisoformat(parameters["end"]))
    days = universe.calendar[parameters["lookback"] + 1:end + 1]
    initial = cohort.ActivityBook(cohort.ONE, (), universe.calendar[parameters["lookback"]])
    additional = (universe, rows, quotes, binding, holding_inputs, raw_quote_view)
    sealed = (_immutable_fields(additional) if raw_context is None else
        raw_context[1](raw_context[0], additional_seal=additional))
    parameter_bytes = encoded(parameters)
    engine = replay_synthetic if raw_context is None else lambda *args, **kwargs: _replay_synthetic_from_context(
        *raw_context, *args, **kwargs)

    def require_unchanged():
        if raw_context is not None:
            raw_context[1](raw_context[0])
        require(encoded(parameters) == parameter_bytes and _unchanged_fields(sealed),
            "trend prepared inputs or parameters changed before cohort replay")

    results = []
    with localcontext() as context:
        context.prec = 28
        for row in rows:
            require_unchanged()
            if before_cohort is not None:
                before_cohort(row)
                require_unchanged()
            verify = None if binding is None else lambda replay, arm=row.arm: verify_cohort_inventory(binding, arm, replay)
            result = cohort._execute_one_cohort(row, declarations=rows, quotes=quotes,
                ordered_events=ordered_events, parameters=parameters, days=days, initial=initial,
                engine=engine, raw_quote_view=quotes if raw_context is not None else None,
                return_policy_json=policy_json, verify_inventory=verify, attribution=_attribution)
            if on_cohort is not None:
                on_cohort(result)
                require_unchanged()
            results.append(result)
        aggregates = {arm: {GROUP: cohort._summary(tuple(row for row in results if row["arm"] == arm), days,
            return_policy_json=policy_json)} for arm in cohort.ARMS}
    return {"schema": "price-trend-certified-cohorts-v1", "status": "pure_restricted_price_return_diagnostic",
        "parameters": strict_json(parameter_bytes), "calendar": universe.calendar,
        "initial_nav_per_cohort": cohort.ONE, "cohorts": tuple(results), "aggregates": aggregates,
        "cohort_evaluation_counts": {arm: sum(row["arm"] == arm for row in results) for arm in cohort.ARMS},
        "candidate_window_count": universe.candidate_window_count, "reuse_identity": universe.reuse_identity,
        "old_control_targets_unchanged": True, "old_control_books_replayed": 0,
        "activity_snapshot_sha256": quotes.activity_snapshot_sha256,
        "quote_snapshot_sha256": quotes.quote_snapshot_sha256,
        "aggregation": "ex-post sum(cohort_NAV-1)/sum(original_activity_target_count/20); not a deployable continuous strategy",
        "future_counts_used_only_for_ex_post_attribution": True,
        "conditional_exit_bounds_consumed": binding is not None,
        "conditional_scope_binding_sha256": None if binding is None else binding.binding_sha256,
        "whole_source_population_complete": False, "actual_input_reuse_readiness_established": False,
        "comparison_power_prerequisite_met": False, "power_design_passed": False,
        "source_truth_certified": False, "event_coverage_certified": False, "price_basis_certified": False,
        "historical_vintage_certified": False, "actual_study_completed": False,
        "promotion_allowed": False, **policy_report(policy_json)}
