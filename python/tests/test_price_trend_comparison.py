"""Restricted TREND adapters use only fabricated calendars, quotes and proofs."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest

from research import activity_cohort_comparison as old
from research import price_trend_comparison as module
from research.activity_cohort_return_policy import SCHEMA_V3
from research.activity_cohort_selection import CONTROL_IDS, rank_control_codes
from research.activity_pair_calibration import PARAMETERS
from research.activity_timing import AvailabilityMetadata, KST
from research.price_trend_signal import PriceTrendObservation
import test_activity_cohort_comparison as fixture
import test_activity_cohort_connection as connection_fixture


def saved_frontier(declarations, calendar):
    activities = sorted((row for row in declarations if row.arm == "D1" and row.group_id == "ACTIVITY"),
        key=lambda row: row.formation_on)
    population, windows = [], []
    for row in activities:
        index = calendar.index(row.formation_on)
        normal = [day.isoformat() for day in calendar[index - 60:index]]
        for code, isin in row.source_population:
            item = {"formation": row.formation_on.isoformat(), "code": code, "isin": isin,
                "normal_input_disposition": "eligible"}
            population.append(item)
            windows.append({**item, "normal_dates": normal})
    return {"schema": "activity-cohort-scope-v1", "complete_target_prefix": True,
        "formations": [{"formation": row.formation_on.isoformat(), "ACTIVITY": {arm: {
            "target_ceiling": row.target_ceiling, "source_population_count": row.source_population_count}
            for arm in old.ARMS}} for row in activities],
        "population_partition": population, "needed_normal_windows": windows}


def inputs(ks=(1, 0)):
    declarations, quotes = fixture.case(ks)
    frontier = saved_frontier(declarations, quotes.calendar)
    universe = module.derive_certified_trend_universe(frontier, declarations, quotes.calendar,
        parameters=deepcopy(PARAMETERS))
    observations = trend_observations(universe)
    rows = module.build_trend_declarations(universe, observations, parameters=deepcopy(PARAMETERS))
    return frontier, universe, rows, quotes


def trend_observations(universe, *, prefer_activity=False):
    identities = {}
    formation_preferences = {}
    for old_row in universe.original_declarations:
        if old_row.arm == "D1" and old_row.group_id == "ACTIVITY":
            formation_preferences[old_row.formation_on] = set(old_row.chosen_codes)
    formations = {formation for formation, _, _, _ in universe.windows}
    for _, code, isin, _ in universe.windows:
        identities[code] = isin
    rows = []
    for code, day in universe.required_price_coordinates:
        price = Decimal("100")
        if day in formations:
            price = (Decimal("200") if code in formation_preferences[day] else Decimal("50")) if prefer_activity else Decimal(100 + int(code))
        rows.append(PriceTrendObservation(code, identities[code], price, "synthetic-adjusted",
            "synthetic-snapshot", "synthetic-comparable-unit-declaration",
            AvailabilityMetadata(day, datetime.combine(day, time(16), KST), "unknown", "synthetic-provider",
                None, None, "assumed", availability_policy="synthetic-close",
                evidence_reference="synthetic-only")))
    return tuple(rows)


def run(rows, universe, quotes, *, events=(), binding=None, **callbacks):
    return module.replay_price_trend_cohorts(rows, quotes, events, universe=universe,
        parameters=deepcopy(PARAMETERS), scope_binding=binding, **callbacks)


def test_reduced_scope_retains_unknown_and_baseline_gaps_without_excluding_them():
    declarations, quotes = fixture.case((1, 0))
    frontier = saved_frontier(declarations, quotes.calendar)
    first = min(row.formation_on for row in declarations)
    chosen = {code for row in declarations if row.formation_on == first for code in row.chosen_codes}
    unused = sorted(set(fixture.CODES) - chosen)
    assert len(unused) >= 2
    for row in frontier["population_partition"]:
        if row["formation"] == first.isoformat() and row["code"] == unused[0]:
            row["normal_input_disposition"] = "unresolved"
    frontier["needed_normal_windows"] = [row for row in frontier["needed_normal_windows"]
        if not (row["formation"] == first.isoformat() and row["code"] in unused[:2])]
    universe = module.derive_certified_trend_universe(frontier, declarations, quotes.calendar, parameters=PARAMETERS)
    assert universe.candidate_window_count == len(fixture.CODES) - 2
    assert universe.old_control_targets_unchanged
    assert not universe.whole_source_population_complete and not universe.source_truth_certified
    retained = module.strict_json(universe.frontier_json)
    assert len(retained["population_partition"]) == 2 * len(fixture.CODES)
    assert next(row for row in retained["population_partition"] if row["formation"] == first.isoformat()
        and row["code"] == unused[0])["normal_input_disposition"] == "unresolved"
    assert all(window[0] == first for window in universe.windows)


def test_exact_old_hash_control_targets_are_recomputed_in_original_order():
    _, universe, _, _ = inputs((1, 0))
    for row in universe.original_declarations:
        if row.group_id not in CONTROL_IDS:
            continue
        pool = tuple(code for formation, code, _, _ in universe.windows if formation == row.formation_on)
        assert rank_control_codes(pool, control_id=row.group_id, formation_on=row.formation_on)[:row.target_ceiling] == row.chosen_codes


def test_old_target_outside_restricted_pool_refuses_before_prices():
    frontier, universe, _, _ = inputs()
    activity = next(row for row in universe.original_declarations if row.target_ceiling and row.group_id == "ACTIVITY")
    frontier["needed_normal_windows"] = [row for row in frontier["needed_normal_windows"]
        if not (row["formation"] == activity.formation_on.isoformat() and row["code"] == activity.chosen_codes[0])]
    with pytest.raises(ValueError, match="every old group target"):
        module.derive_certified_trend_universe(frontier, universe.original_declarations,
            universe.calendar, parameters=PARAMETERS)


def test_valid_original_bs_exclusion_cannot_hide_reduced_pool_hash_mismatch():
    frontier, universe, _, _ = inputs()
    declarations = list(universe.original_declarations)
    activity = next(row for row in declarations if row.target_ceiling and row.group_id == "ACTIVITY")
    for index, row in enumerate(declarations):
        if row.arm != "D1" or row.group_id not in CONTROL_IDS or not row.target_ceiling:
            continue
        order = rank_control_codes(tuple(code for code, _ in row.source_population),
            control_id=row.group_id, formation_on=row.formation_on)
        if order[0] not in activity.chosen_codes:
            declarations[index] = replace(row, resolved_prefix=((order[0], "excluded", "d" * 64),
                (order[1], "eligible", "d" * 64)))
            break
    else:
        pytest.fail("synthetic fixture needs a non-activity first control rank")
    with pytest.raises(ValueError, match="hash reselection"):
        module.derive_certified_trend_universe(frontier, tuple(declarations), universe.calendar, parameters=PARAMETERS)


@pytest.mark.parametrize("change", ["population_missing", "population_duplicate", "isin", "window_duplicate",
    "date_duplicate", "date_future", "date_outside_calendar", "formation_missing", "paired_budget", "unproved"])
def test_frontier_corruption_is_not_a_new_eligible_subset(change):
    frontier, universe, _, _ = inputs()
    if change == "population_missing":
        frontier["population_partition"].pop()
    elif change == "population_duplicate":
        frontier["population_partition"].append(frontier["population_partition"][0])
    elif change == "isin":
        frontier["needed_normal_windows"][0]["isin"] = "OTHER"
    elif change == "window_duplicate":
        frontier["needed_normal_windows"].append(frontier["needed_normal_windows"][0])
    elif change == "date_duplicate":
        frontier["needed_normal_windows"][0]["normal_dates"][0] = frontier["needed_normal_windows"][0]["normal_dates"][1]
    elif change == "date_future":
        frontier["needed_normal_windows"][0]["normal_dates"][-1] = frontier["formations"][0]["formation"]
    elif change == "date_outside_calendar":
        frontier["needed_normal_windows"][0]["normal_dates"][0] = "1900-01-01"
    elif change == "formation_missing":
        frontier["formations"].pop()
    elif change == "paired_budget":
        frontier["formations"][0]["ACTIVITY"]["D2"]["target_ceiling"] = 2
    else:
        frontier["complete_target_prefix"] = False
    with pytest.raises(ValueError):
        module.derive_certified_trend_universe(frontier, universe.original_declarations,
            universe.calendar, parameters=PARAMETERS)


def test_basic_and_iso_saved_dates_identify_the_same_candidate_coordinates():
    frontier, universe, _, _ = inputs()
    for name in ("formations", "population_partition", "needed_normal_windows"):
        for row in frontier[name]:
            row["formation"] = row["formation"].replace("-", "")
            if "normal_dates" in row:
                row["normal_dates"] = [day.replace("-", "") for day in row["normal_dates"]]
    basic = module.derive_certified_trend_universe(frontier, universe.original_declarations,
        universe.calendar, parameters=PARAMETERS)
    assert basic.windows == universe.windows
    assert basic.required_price_coordinates == universe.required_price_coordinates


def test_trend_contract_and_shared_book_loop_preserve_equal_original_budget(monkeypatch):
    _, universe, _, quotes = inputs()
    rows = module.build_trend_declarations(universe, trend_observations(universe, prefer_activity=True), parameters=PARAMETERS)
    assert all(type(row) is module.TrendCohortDeclaration and row.group_id == "TREND" for row in rows)
    calls = []
    real = old._execute_one_cohort
    def capture(row, **kwargs):
        calls.append(row.group_id)
        return real(row, **kwargs)
    monkeypatch.setattr(old, "_execute_one_cohort", capture)
    result = run(rows, universe, quotes)
    assert calls == ["TREND"] * 4
    assert result["cohort_evaluation_counts"] == {"D1": 2, "D2": 2}
    assert result["old_control_books_replayed"] == 0
    assert all(set(groups) == {"TREND"} for groups in result["aggregates"].values())
    original = old.replay_equal_capital_cohorts(universe.original_declarations, quotes, (), parameters=PARAMETERS,
        cohort_keys=tuple((row.arm, "ACTIVITY", row.formation_on) for row in rows))
    for new, previous in zip(result["cohorts"], original["cohorts"], strict=True):
        assert new["replay"] == previous["replay"]
        assert new["assigned_capital"] == Decimal(new["target_ceiling"]) / 20
        assert new["metrics"] == previous["metrics"]
    assert not result["whole_source_population_complete"] and not result["power_design_passed"]
    assert not result["promotion_allowed"] and not result["actual_study_completed"]


def test_fourteen_formations_produce_only_twenty_eight_empty_trend_books(monkeypatch):
    _, universe, rows, quotes = inputs((0,) * 14)
    assert universe.windows == universe.required_price_coordinates == ()
    assert all(row.selection.expected_candidates == row.observations == () for row in rows)
    result = run(rows, universe, quotes)
    assert len(result["cohorts"]) == 28
    assert result["cohort_evaluation_counts"] == {"D1": 14, "D2": 14}
    assert all(row["target_ceiling"] == row["assigned_capital"] == 0 for row in result["cohorts"])
    assert all(row["metrics"]["total_return"] is None for row in result["cohorts"])


def test_raw_view_uses_the_existing_issued_context_and_same_twenty_eight_book_loop(monkeypatch):
    declarations, base, raw = fixture.raw_cash_case()
    universe = module.derive_certified_trend_universe(saved_frontier(declarations, raw.calendar),
        declarations, raw.calendar, parameters=PARAMETERS)
    rows = module.build_trend_declarations(universe, (), parameters=PARAMETERS)
    checks = []
    original = fixture.raw_view.validate_raw_quote_view
    def counted(value):
        checks.append(True)
        return original(value)
    monkeypatch.setattr(fixture.raw_view, "validate_raw_quote_view", counted)
    result = module.replay_price_trend_cohorts(rows, base, (), universe=universe,
        parameters=deepcopy(PARAMETERS), raw_quote_view=raw)
    assert checks == [True]
    assert len(result["cohorts"]) == 28
    assert all(set(row["replay"].navs) == {Decimal(1)} for row in result["cohorts"])


def test_nested_mutation_between_cohorts_is_rejected_before_the_next_book(monkeypatch):
    _, universe, rows, quotes = inputs()
    calls = []
    original = old._execute_one_cohort
    def mutate(row, **kwargs):
        result = original(row, **kwargs)
        calls.append(row)
        object.__setattr__(row.selection, "chosen_codes", ())
        return result
    monkeypatch.setattr(old, "_execute_one_cohort", mutate)
    with pytest.raises(ValueError, match="prepared inputs"):
        run(rows, universe, quotes)
    assert len(calls) == 1


def test_callbacks_wrap_each_book_in_order_and_preserve_default_result(monkeypatch):
    _, universe, rows, quotes = inputs((0,))
    baseline = run(rows, universe, quotes)
    calls = []
    original = old._execute_one_cohort
    def execute(row, **kwargs):
        calls.append(("book", old._key(row)))
        return original(row, **kwargs)
    monkeypatch.setattr(old, "_execute_one_cohort", execute)
    result = run(rows, universe, quotes,
        before_cohort=lambda row: calls.append(("before", old._key(row))),
        on_cohort=lambda result: calls.append(("after", result["key"])))
    assert result == baseline
    assert calls == [(phase, old._key(row)) for row in rows for phase in ("before", "book", "after")]


@pytest.mark.parametrize("phase", ["before", "after"])
def test_callback_failure_propagates_without_attempting_another_book(phase, monkeypatch):
    _, universe, rows, quotes = inputs((0,))
    calls = []
    marker = RuntimeError("synthetic durable writer failure")
    original = old._execute_one_cohort
    def execute(row, **kwargs):
        calls.append(row)
        return original(row, **kwargs)
    def fail(value):
        raise marker
    monkeypatch.setattr(old, "_execute_one_cohort", execute)
    callbacks = {"before_cohort" if phase == "before" else "on_cohort": fail}
    with pytest.raises(RuntimeError) as failure:
        run(rows, universe, quotes, **callbacks)
    assert failure.value is marker
    assert len(calls) == (0 if phase == "before" else 1)


@pytest.mark.parametrize("phase", ["before", "last_after"])
def test_callback_nested_input_mutation_is_rejected_before_book_or_final_return(phase, monkeypatch):
    _, universe, rows, quotes = inputs((0,))
    calls = []
    original = old._execute_one_cohort
    def execute(row, **kwargs):
        calls.append(row)
        return original(row, **kwargs)
    def mutate(value):
        if phase == "before" or len(calls) == len(rows):
            object.__setattr__(universe.original_declarations[0], "source_partition_sha256", "0" * 64)
    monkeypatch.setattr(old, "_execute_one_cohort", execute)
    callbacks = {"before_cohort" if phase == "before" else "on_cohort": mutate}
    with pytest.raises(ValueError, match="prepared inputs"):
        run(rows, universe, quotes, **callbacks)
    assert len(calls) == (0 if phase == "before" else len(rows))


@pytest.mark.parametrize("change", ["missing", "duplicate", "extra", "future", "isin"])
def test_price_coordinate_and_every_candidate_availability_reject_before_books(change, monkeypatch):
    _, universe, _, _ = inputs()
    quotes = list(trend_observations(universe))
    if change == "missing":
        quotes.pop()
    elif change == "duplicate":
        quotes.append(quotes[-1])
    elif change == "extra":
        quotes[-1] = replace(quotes[-1], code="UNKNOWN")
    elif change == "future":
        quotes[-1] = replace(quotes[-1], metadata=replace(quotes[-1].metadata,
            available_at=max(row.selection_at for row in universe.original_declarations) + timedelta(days=1)))
    else:
        quotes[-1] = replace(quotes[-1], isin="OTHER")
    monkeypatch.setattr(old, "_execute_one_cohort", lambda *args, **kwargs: pytest.fail("must not replay"))
    with pytest.raises(ValueError):
        module.build_trend_declarations(universe, tuple(quotes), parameters=PARAMETERS)


@pytest.mark.parametrize("change", ["missing_declaration", "bs_type", "chosen", "budget", "cutoff", "universe"])
def test_forged_or_incomplete_trend_declarations_reject_before_engine(change, monkeypatch):
    _, universe, rows, quotes = inputs()
    if change == "missing_declaration":
        rows = rows[:-1]
    elif change == "bs_type":
        rows = universe.original_declarations
    elif change == "chosen":
        rows = (replace(rows[0], selection=replace(rows[0].selection, chosen_codes=())), *rows[1:])
    elif change == "budget":
        rows = (replace(rows[0], original_activity_targets=()), *rows[1:])
    elif change == "cutoff":
        rows = (replace(rows[0], selection_at=rows[0].selection_at + timedelta(minutes=1)), *rows[1:])
    else:
        universe = replace(universe, windows=())
    monkeypatch.setattr(old, "_execute_one_cohort", lambda *args, **kwargs: pytest.fail("must not replay"))
    with pytest.raises(ValueError):
        run(rows, universe, quotes)


def test_v3_existing_conditional_binding_and_same_book_five_issuer_policy_are_reused(monkeypatch):
    args = connection_fixture.rights_package(v3=True, component=True)
    declarations, quotes = args["declarations"], args["quotes"]
    binding = connection_fixture.module.prepare_cohort_scope(**args)
    frontier = saved_frontier(declarations, quotes.calendar)
    universe = module.derive_certified_trend_universe(frontier, declarations, quotes.calendar, parameters=PARAMETERS)
    rows = module.build_trend_declarations(universe, trend_observations(universe, prefer_activity=True), parameters=PARAMETERS)
    checked = []
    real = module.verify_cohort_inventory
    def verify(binding, arm, replay):
        checked.append(arm)
        real(binding, arm, replay)
    monkeypatch.setattr(module, "verify_cohort_inventory", verify)
    result = run(rows, universe, quotes, events=binding.events, binding=binding)
    assert len(checked) == len(rows)
    assert result["conditional_exit_bounds_consumed"]
    assert result["return_policy_schema"] == SCHEMA_V3
    assert result["automatic_subscription_rights_cash"] == "not_modeled"
    assert result["automatic_stock_dividend_assets"] == "not_modeled"
    assert result["automatic_bonus_share_assets"] == "not_modeled"
    assert not result["actual_investment_return"]
    assert all(module.cohort.ATTRIBUTION_KEY in row for row in result["cohorts"])
