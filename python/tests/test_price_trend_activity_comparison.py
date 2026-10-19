"""COMBO uses fabricated saved audits, calendars, quotes and existing book engine."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal, localcontext
from statistics import NormalDist

import pytest

from research import price_trend_activity_comparison as module
from research import price_trend_comparison as trend
from research.activity_pair_calibration import PARAMETERS
from research.activity_paired_replay import _json_value
from research.activity_preflight import encoded, strict_json
from research.activity_timing import AvailabilityMetadata, KST
import test_activity_cohort_connection as connection_fixture
import test_price_trend_comparison as fixture


def audits_for(universe, declarations, *, rejected=(), history=None):
    result = []
    for formation, code, isin, normal in universe.windows:
        previous = history or [Decimal(1)] * 60
        baseline = module._median(previous)
        current = baseline * (2 if code in rejected else 3)
        sources = {}
        required = universe.calendar[universe.calendar.index(normal[0]):universe.calendar.index(formation) + 1]
        normal_values = dict(zip(normal, previous, strict=True))
        frozen = tuple(day for day in required[:-1] if day not in normal)
        for day in required:
            turnover = current if day == formation else normal_values.get(day, Decimal(0))
            metadata = AvailabilityMetadata(day, datetime.combine(day, time(16), KST), "unknown", "synthetic",
                None, None, "assumed", availability_policy="synthetic-close", evidence_reference="synthetic-only")
            sources[day.isoformat()] = _json_value({"state": "frozen" if day in frozen else "observed",
                "turnover": turnover, "public_available_at": None, "availability": metadata})
        with localcontext() as context:
            context.prec = 28
            ratio = None if baseline == 0 else current / baseline
        arms = {}
        for old in declarations:
            if old.formation_on == formation:
                arms[old.arm] = {"candidate": {"code": code, "activity_pass": baseline > 0 and current >= 3 * baseline},
                    "normal_dates": [day.isoformat() for day in normal], "skipped_frozen_dates": [day.isoformat() for day in frozen],
                    "decision_on": old.decision_on.isoformat(), "selection_at": old.selection_at.isoformat(),
                    "activity_median": str(baseline), "activity_ratio": None if ratio is None else str(ratio)}
        screen = {"formation": formation.isoformat(), "code": code, "isin": isin, "inputs": {"activity": sources}, "arms": arms}
        result.append({"formation": formation.isoformat(), "code": code, "isin": isin, "activity_screen": screen})
    return result


def case(ks=(2, 0), *, rejected=(), history=None):
    _, universe, originals, quotes = fixture.inputs(ks)
    audits = audits_for(universe, originals, rejected=rejected, history=history)
    declarations = module.build_combo_declarations(originals, audits, universe=universe, parameters=PARAMETERS)
    return universe, originals, declarations, audits, quotes


def replay(declarations, universe, quotes, **kwargs):
    return module.replay_price_trend_activity_cohorts(declarations, quotes, (), universe=universe,
        parameters=deepcopy(PARAMETERS), **kwargs)


def test_saved_trend_decoder_roundtrips_and_rejects_invented_static_flags():
    _, originals, _, _, _ = case()
    saved = _json_value(originals)
    assert module.restore_trend_declarations(saved) == originals
    saved[0]["selection"]["promotion_permitted"] = True
    with pytest.raises(ValueError, match="noncanonical"):
        module.restore_trend_declarations(saved)


def test_decoder_does_not_make_a_forged_rank_valid():
    universe, originals, _, audits, _ = case()
    saved = _json_value(originals)
    saved[0]["selection"]["chosen_codes"].reverse()
    decoded = module.restore_trend_declarations(saved)
    with pytest.raises(ValueError, match="fixed selection changed"):
        module.build_combo_declarations(decoded, audits, universe=universe, parameters=PARAMETERS)


def test_exact_even_median_and_inclusive_threshold_preserve_order_and_original_budget():
    previous = [Decimal("1.0000000000000000000000000000000001")] * 30 + [Decimal("2.0000000000000000000000000000000002")] * 30
    # Use enough precision only in the fabricated input construction, as a source would.
    with localcontext() as context:
        context.prec = 90
        universe, originals, declarations, _, _ = case(history=previous)
    for old, combo in zip(originals, declarations, strict=True):
        assert combo.trend_declaration == old
        assert combo.chosen_codes == old.chosen_codes
        assert combo.target_ceiling == old.target_ceiling
        for check in combo.activity_checks:
            assert check.activity_median == Decimal("1.50000000000000000000000000000000015")
            assert check.formation_turnover == Decimal("4.50000000000000000000000000000000045")
            assert check.activity_pass


def test_screening_leaves_cash_and_never_fills_from_the_next_rank(monkeypatch):
    _, universe, originals, quotes = fixture.inputs((2, 0))
    rejected = originals[0].chosen_codes[:1]
    audit = audits_for(universe, originals, rejected=rejected)
    declarations = module.build_combo_declarations(originals, audit, universe=universe, parameters=PARAMETERS)
    real = module.cohort._execute_one_cohort
    calls = []
    def execute(row, **kwargs):
        calls.append(row)
        return real(row, **kwargs)
    monkeypatch.setattr(module.cohort, "_execute_one_cohort", execute)
    result = replay(declarations, universe, quotes)
    assert len(calls) == len(declarations)
    for row, declaration in zip(result["cohorts"], declarations, strict=True):
        assert row["assigned_capital"] == Decimal(declaration.target_ceiling) / 20
        assert row["chosen_codes"] == tuple(code for code in declaration.trend_declaration.chosen_codes if code not in rejected)
        assert row["filled_capital"] == Decimal(len(row["chosen_codes"])) / 20
        assert row["retained_target_budget"] == row["filled_capital"]
        assert row["eligible_shortfall"] == declaration.target_ceiling - len(row["chosen_codes"])
    assert result["old_trend_books_replayed"] == result["replacement_targets"] == 0


def test_fourteen_cash_formations_replay_exactly_twenty_eight_books():
    universe, _, declarations, _, quotes = case((0,) * 14)
    result = replay(declarations, universe, quotes)
    assert result["cohort_evaluation_counts"] == {"D1": 14, "D2": 14}
    assert all(row["filled_entries"] == row["assigned_capital"] == 0 for row in result["cohorts"])


def test_raw_view_reuses_the_issued_context_and_the_same_book_loop(monkeypatch):
    declarations, base, raw = fixture.fixture.raw_cash_case()
    universe = trend.derive_certified_trend_universe(fixture.saved_frontier(declarations, raw.calendar),
        declarations, raw.calendar, parameters=PARAMETERS)
    originals = trend.build_trend_declarations(universe, (), parameters=PARAMETERS)
    combos = module.build_combo_declarations(originals, [], universe=universe, parameters=PARAMETERS)
    checked = []
    validate = fixture.fixture.raw_view.validate_raw_quote_view
    def counted(value):
        checked.append(True)
        return validate(value)
    monkeypatch.setattr(fixture.fixture.raw_view, "validate_raw_quote_view", counted)
    result = replay(combos, universe, base, raw_quote_view=raw)
    assert checked == [True]
    assert result["cohort_evaluation_counts"] == {"D1": 14, "D2": 14}
    assert all(set(row["replay"].navs) == {Decimal(1)} for row in result["cohorts"])


def test_complete_frozen_interval_is_retained_but_never_becomes_a_normal_value():
    declarations, quotes = fixture.fixture.case((0, 2))
    frontier = fixture.saved_frontier(declarations, quotes.calendar)
    second = sorted({row.formation_on for row in declarations})[1]
    index = quotes.calendar.index(second)
    frozen_day = quotes.calendar[index - 20]
    normal = [day.isoformat() for day in quotes.calendar[index - 61:index] if day != frozen_day]
    for row in frontier["needed_normal_windows"]:
        if row["formation"] == second.isoformat():
            row["normal_dates"] = normal
    universe = trend.derive_certified_trend_universe(frontier, declarations, quotes.calendar, parameters=PARAMETERS)
    originals = trend.build_trend_declarations(universe, fixture.trend_observations(universe), parameters=PARAMETERS)
    audits = audits_for(universe, originals)
    combos = module.build_combo_declarations(originals, audits, universe=universe, parameters=PARAMETERS)
    assert all(check.skipped_frozen_dates == (frozen_day,) for row in combos for check in row.activity_checks)
    selected = next(row for row in originals if row.chosen_codes)
    raw = next(row for row in audits if row["code"] == selected.chosen_codes[0])["activity_screen"]["inputs"]["activity"]
    raw[frozen_day.isoformat()]["state"] = "observed"
    with pytest.raises(ValueError, match="sixty normal"):
        module.build_combo_declarations(originals, audits, universe=universe, parameters=PARAMETERS)


@pytest.mark.parametrize("change", ("missing", "missing_turnover", "float", "negative", "nan", "malformed", "late", "provenance", "normal", "candidate", "median", "ratio", "isin"))
def test_invalid_inputs_cannot_turn_into_a_false_predicate(change):
    universe, originals, _, audits, _ = case()
    code = originals[0].chosen_codes[0]
    row = next(row for row in audits if row["code"] == code)
    screen = row["activity_screen"]
    sources = screen["inputs"]["activity"]
    formation = row["formation"]
    if change == "missing": sources.pop(formation)
    elif change == "missing_turnover": sources[formation]["turnover"] = None
    elif change == "float": sources[formation]["turnover"] = 3.0
    elif change == "negative": sources[formation]["turnover"] = "-1"
    elif change == "nan": sources[formation]["turnover"] = "NaN"
    elif change == "malformed": sources[formation]["turnover"] = "not-a-number"
    elif change == "late": sources[formation]["availability"]["available_at"] = (originals[0].selection_at + timedelta(seconds=1)).isoformat()
    elif change == "provenance": sources[formation]["public_available_at"] = sources[formation]["availability"]["available_at"]
    elif change == "normal": screen["arms"]["D1"]["normal_dates"].pop()
    elif change == "candidate": screen["arms"]["D1"]["candidate"]["activity_pass"] = False
    elif change == "median": screen["arms"]["D1"]["activity_median"] = "2"
    elif change == "ratio": screen["arms"]["D1"]["activity_ratio"] = "2"
    elif change == "isin": row["isin"] = "other"
    with pytest.raises(ValueError):
        module.build_combo_declarations(originals, audits, universe=universe, parameters=PARAMETERS)


def test_zero_median_is_explicit_nonpass_with_undefined_ratio():
    _, _, declarations, _, _ = case(history=[Decimal(0)] * 60)
    assert all(not row.chosen_codes for row in declarations)
    assert all(check.activity_median == 0 and check.activity_ratio is None and not check.activity_pass
        for row in declarations for check in row.activity_checks)


@pytest.mark.parametrize("change", ("subset", "budget", "parameters"))
def test_forged_subset_budget_and_mutated_parameters_fail_before_engine(change, monkeypatch):
    universe, _, declarations, _, quotes = case()
    if change == "subset": declarations = (replace(declarations[0], chosen_codes=()), *declarations[1:])
    if change == "budget":
        old = replace(declarations[0].trend_declaration, original_activity_targets=())
        declarations = (replace(declarations[0], trend_declaration=old), *declarations[1:])
    parameters = deepcopy(PARAMETERS)
    if change == "parameters": parameters["slots"] = 19
    monkeypatch.setattr(module.cohort, "_execute_one_cohort", lambda *args, **kwargs: pytest.fail("must not replay"))
    with pytest.raises(ValueError):
        module.replay_price_trend_activity_cohorts(declarations, quotes, (), universe=universe, parameters=parameters)


def test_callbacks_preserve_order_and_reject_input_mutation_before_book(monkeypatch):
    universe, _, declarations, _, quotes = case()
    called = []
    def before(row):
        called.append(row.arm)
        object.__setattr__(row.trend_declaration.selection, "chosen_codes", ())
    monkeypatch.setattr(module.cohort, "_execute_one_cohort", lambda *args, **kwargs: pytest.fail("must not replay"))
    with pytest.raises(ValueError, match="prepared inputs"):
        replay(declarations, universe, quotes, before_cohort=before)
    assert called == ["D1"]


@pytest.mark.parametrize("phase", ("before", "after"))
def test_callback_failure_propagates_and_never_attempts_a_second_book(phase, monkeypatch):
    universe, _, declarations, _, quotes = case((0,))
    calls = []
    real = module.cohort._execute_one_cohort
    def execute(row, **kwargs):
        calls.append(row)
        return real(row, **kwargs)
    monkeypatch.setattr(module.cohort, "_execute_one_cohort", execute)
    def fail(value):
        raise RuntimeError("synthetic durable callback failure")
    with pytest.raises(RuntimeError, match="durable callback"):
        replay(declarations, universe, quotes, **{"before_cohort" if phase == "before" else "on_cohort": fail})
    assert len(calls) == (0 if phase == "before" else 1)


def test_valid_v3_scope_and_same_book_policy_inventory_checks_are_consumed(monkeypatch):
    args = connection_fixture.rights_package(v3=True, component=True)
    binding = connection_fixture.module.prepare_cohort_scope(**args)
    frontier = fixture.saved_frontier(args["declarations"], args["quotes"].calendar)
    universe = trend.derive_certified_trend_universe(frontier, args["declarations"], args["quotes"].calendar, parameters=PARAMETERS)
    originals = trend.build_trend_declarations(universe, fixture.trend_observations(universe, prefer_activity=True), parameters=PARAMETERS)
    declarations = module.build_combo_declarations(originals, audits_for(universe, originals), universe=universe, parameters=PARAMETERS)
    checked = []
    real = module.verify_cohort_inventory
    def verify(binding, arm, result):
        checked.append(arm)
        real(binding, arm, result)
    monkeypatch.setattr(module, "verify_cohort_inventory", verify)
    result = module.replay_price_trend_activity_cohorts(declarations, args["quotes"], binding.events,
        universe=universe, parameters=PARAMETERS, scope_binding=binding)
    assert len(checked) == len(declarations)
    assert result["conditional_exit_bounds_consumed"]
    assert result["return_policy_schema"] == module.SCHEMA_V3
    assert result["automatic_bonus_share_assets"] == "not_modeled"
    old = trend.replay_price_trend_cohorts(originals, args["quotes"], binding.events,
        universe=universe, parameters=PARAMETERS, scope_binding=binding)
    saved_trend = tuple(_json_value({key: value for key, value in row.items() if key != "replay"}) for row in old["cohorts"])
    report = module.comparison_report(result, saved_trend, parameters=PARAMETERS, return_policy_json=binding.return_policy_json)
    for arm in module.cohort.ARMS:
        for summary in report["arms"][arm]["groups"].values():
            assert summary["automatic_bonus_share_assets"] == "not_modeled"
            assert summary["exposure"]["automatic_stock_dividend_assets_included"] is False
            assert summary[module.cohort.ATTRIBUTION_KEY]["actual_investment_return"] is False
    with pytest.raises(ValueError, match="policy bytes required"):
        module.comparison_report(result, saved_trend, parameters=PARAMETERS)


def test_even_activity_rejected_original_trend_origin_must_be_in_old_scope(monkeypatch):
    args = connection_fixture.rights_package(v3=True, component=True)
    binding = connection_fixture.module.prepare_cohort_scope(**args)
    frontier = fixture.saved_frontier(args["declarations"], args["quotes"].calendar)
    universe = trend.derive_certified_trend_universe(frontier, args["declarations"], args["quotes"].calendar, parameters=PARAMETERS)
    originals = trend.build_trend_declarations(universe, fixture.trend_observations(universe), parameters=PARAMETERS)
    declarations = module.build_combo_declarations(originals, audits_for(universe, originals,
        rejected=tuple(code for row in originals for code in row.chosen_codes)), universe=universe, parameters=PARAMETERS)
    needed = {(row.arm, row.formation_on, code, row.decision_on) for row in declarations for code in row.trend_declaration.chosen_codes}
    absent = min(needed)
    # Isolate the subset guard after the existing scope validator has run.
    real_validate = module._validate_cohort_scope
    def validate(*args, **kwargs):
        valid = real_validate(*args, **kwargs)
        return replace(valid, origin_bounds=tuple(row for row in valid.origin_bounds if row[:4] != absent))
    monkeypatch.setattr(module, "_validate_cohort_scope", validate)
    monkeypatch.setattr(module.cohort, "_execute_one_cohort", lambda *args, **kwargs: pytest.fail("must not replay"))
    with pytest.raises(ValueError, match="outside original conditional proof"):
        module.replay_price_trend_activity_cohorts(declarations, args["quotes"], binding.events,
            universe=universe, parameters=PARAMETERS, scope_binding=binding)


def test_blind_cash_only_and_constant_invested_paths_have_undefined_power():
    universe, _, declarations, _, quotes = case((0,) * 4)
    result = replay(declarations, universe, quotes)
    calibration = module.blind_combo_calibration(result, PARAMETERS)
    for row in calibration.values():
        assert row["status"] == "undefined_no_finite_nonzero_invested_dispersion"
        assert row["detectable_daily_bps"] is row["cost_floor_resolvable_diagnostic"] is None
        assert row["cost_floor_resolvable"] is row["power_design_passed"] is False
    for arm in module.cohort.ARMS:
        result["aggregates"][arm][module.GROUP]["filled_entries"] = 1
        result["aggregates"][arm][module.GROUP]["assigned_capital"] = Decimal(".05")
        for point in result["aggregates"][arm][module.GROUP]["metrics"]["path"]:
            point["session_return"] = Decimal(0)
    assert all(row["detectable_daily_bps"] is None for row in module.blind_combo_calibration(result, PARAMETERS).values())


def test_blind_calibration_keeps_old_costfloor_diagnostic_without_power_claim(monkeypatch):
    universe, _, declarations, _, quotes = case((1,) * 4)
    result = replay(declarations, universe, quotes)
    monkeypatch.setattr(module, "calibration", lambda book, parameters: {"iid_session_se": 1e-9,
        "block_se": {"63": 2e-9, "126": 0.5e-9, "252": 1e-9}, "cost_floor_daily_bps": 43.54 / 126})
    arms = module.blind_combo_calibration(result, PARAMETERS)
    expected = (NormalDist().inv_cdf(.95) + NormalDist().inv_cdf(.8)) * 2e-9 * 10000
    for row in arms.values():
        assert row["detectable_daily_bps"] == expected
        assert row["cost_floor_resolvable_diagnostic"]
        assert not row["cost_floor_resolvable"] and not row["power_design_passed"]
        assert not row["cost_floor_exposure_matched"]


def test_blind_nonfinite_dispersion_is_undefined_and_never_bootstrapped(monkeypatch):
    universe, _, declarations, _, quotes = case((0,))
    result = replay(declarations, universe, quotes)
    for arm in module.cohort.ARMS:
        summary = result["aggregates"][arm][module.GROUP]
        summary["assigned_capital"], summary["filled_entries"] = Decimal(".05"), 1
        for index, point in enumerate(summary["metrics"]["path"]):
            point["session_return"] = Decimal(index % 2)
    monkeypatch.setattr(module, "stdev", lambda values: float("inf"))
    monkeypatch.setattr(module, "calibration", lambda *args: pytest.fail("undefined dispersion must not bootstrap"))
    for row in module.blind_combo_calibration(result, PARAMETERS).values():
        assert row["session_return_sd"] is row["detectable_daily_bps"] is None
        assert not row["cost_floor_resolvable"]


def test_report_reuses_original_books_and_keeps_full_and_retained_denominators():
    universe, originals, _, _, quotes = case((2, 0))
    rejected = originals[0].chosen_codes[:1]
    declarations = module.build_combo_declarations(originals, audits_for(universe, originals, rejected=rejected), universe=universe, parameters=PARAMETERS)
    combos = replay(declarations, universe, quotes)
    old = trend.replay_price_trend_cohorts(originals, quotes, (), universe=universe, parameters=PARAMETERS)
    # The report accepts compact saved dictionaries; no ReplayResult is needed.
    compact = tuple({key: value for key, value in row.items() if key != "replay"} for row in combos["cohorts"])
    saved_trend = tuple(strict_json(encoded(_json_value({key: value for key, value in row.items() if key != "replay"}))) for row in old["cohorts"])
    report = module.comparison_report({**combos, "cohorts": compact}, saved_trend, parameters=PARAMETERS)
    for arm in module.cohort.ARMS:
        row = report["arms"][arm]
        assert row["original_assigned_capital"] == Decimal(".1")
        assert row["retained_target_budget"] == Decimal(".05")
        assert row["groups"]["TREND"]["filled_entries"] == 2
        assert row["groups"]["COMBO"]["filled_entries"] == 1
        assert row["groups"]["COMBO"]["metrics"]["total_return"] == row["secondary_combo_retained_budget_metrics"]["total_return"] / 2
        assert sum(item["combo_minus_trend_pnl"] for item in row["by_formation"]) == row["combo_minus_trend"]["combo_minus_trend_pnl"]
    assert report["new_books_replayed"] == 0 and not report["realized_exposure_matched"]
