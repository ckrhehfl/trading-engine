"""Matched-count controls use fabricated saved declarations and real book math."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, localcontext
from statistics import NormalDist

import pytest

from research import price_trend_matched_activity as module
from research.activity_pair_calibration import PARAMETERS
from research.activity_paired_replay import _json_value
import test_price_trend_activity_comparison as fixture


def case(ks=(2, 0), *, cash=False):
    universe, originals, _, audits, quotes = fixture.case(ks)
    # Reject the highest rank: the new control must choose it, whereas COMBO
    # keeps the lower-ranked original target. This distinguishes the policies.
    rejected = tuple(code for row in originals for code in (row.chosen_codes if cash else row.chosen_codes[:1]))
    audits = fixture.audits_for(universe, originals, rejected=rejected)
    combos = fixture.module.build_combo_declarations(originals, audits, universe=universe, parameters=PARAMETERS)
    declarations = module.build_matched_declarations(combos, universe=universe, parameters=PARAMETERS)
    return universe, combos, declarations, quotes


def run(declarations, universe, quotes, **kwargs):
    return module.replay_matched_trend_cohorts(declarations, quotes, (), universe=universe,
        parameters=deepcopy(PARAMETERS), **kwargs)


def compact(rows):
    return tuple(_json_value({key: value for key, value in row.items() if key != "replay"}) for row in rows)


def pair(ks=(2, 0), *, cash=False):
    universe, combos, declarations, quotes = case(ks, cash=cash)
    matched = run(declarations, universe, quotes)
    original = fixture.replay(combos, universe, quotes)
    return universe, declarations, matched, compact(original["cohorts"])


def test_original_combo_proof_and_trend_prefix_are_immutable_and_distinct_groups():
    universe, combos, declarations, _ = case()
    for original, matched in zip(combos, declarations, strict=True):
        assert matched.combo_declaration == original
        assert matched.group_id == "TREND_MATCHED"
        assert matched.chosen_codes == original.trend_declaration.chosen_codes[:len(original.chosen_codes)]
        assert matched.target_ceiling == original.target_ceiling
        if original.chosen_codes:
            assert matched.chosen_codes != original.chosen_codes
    assert module.restore_matched_declarations(_json_value(declarations)) == declarations
    saved = _json_value(declarations)
    saved[0]["combo_declaration"]["group_id"] = "TREND_MATCHED"
    with pytest.raises(ValueError, match="noncanonical"):
        module.restore_matched_declarations(saved)
    with pytest.raises(ValueError, match="explicit TREND_MATCHED"):
        module._declarations(combos, universe, PARAMETERS)


@pytest.mark.parametrize("change", ("prefix", "combo", "rank", "budget", "parameters"))
def test_forged_declarations_fail_before_the_book(change, monkeypatch):
    universe, combos, declarations, quotes = case()
    first = declarations[0]
    if change == "prefix": first = replace(first, chosen_codes=first.combo_declaration.chosen_codes)
    elif change == "combo": first = replace(first, combo_declaration=replace(combos[0], chosen_codes=()))
    elif change in ("rank", "budget"):
        old = combos[0].trend_declaration
        old = replace(old, selection=replace(old.selection, chosen_codes=tuple(reversed(old.chosen_codes)))) if change == "rank" else replace(old, original_activity_targets=())
        first = replace(first, combo_declaration=replace(combos[0], trend_declaration=old))
    parameters = deepcopy(PARAMETERS)
    if change == "parameters": parameters["slots"] = 19
    monkeypatch.setattr(module.cohort, "_execute_one_cohort", lambda *args, **kwargs: pytest.fail("must not replay"))
    with pytest.raises(ValueError):
        module.replay_matched_trend_cohorts((first, *declarations[1:]), quotes, (), universe=universe, parameters=parameters)


def test_original_cash_budget_and_per_name_slot_math_survive_the_same_engine(monkeypatch):
    universe, _, declarations, quotes = case()
    real, calls = module.cohort._execute_one_cohort, []
    def execute(row, **kwargs):
        calls.append(row)
        return real(row, **kwargs)
    monkeypatch.setattr(module.cohort, "_execute_one_cohort", execute)
    result = run(declarations, universe, quotes)
    assert calls == list(declarations)
    for row in result["cohorts"]:
        assert row["assigned_capital"] == Decimal(row["target_ceiling"]) / 20
        assert row["matched_target_budget"] == Decimal(len(row["chosen_codes"])) / 20
        assert row["filled_capital"] == Decimal(row["filled_entries"]) / 20
        assert row["key"][1] == module.GROUP
    for arm in module.cohort.ARMS:
        assert result["aggregates"][arm][module.GROUP]["assigned_capital"] == Decimal(".05")
        assert result["original_budget_aggregates"][arm][module.GROUP]["assigned_capital"] == Decimal(".1")
    assert result["old_combo_books_replayed"] == result["old_trend_books_replayed"] == 0


@pytest.mark.parametrize("zero_budget", (True, False))
def test_fourteen_cash_formations_keep_all_twenty_eight_books_and_undefined_normalization(zero_budget):
    universe, combos, declarations, quotes = case((0 if zero_budget else 1,) * 14, cash=True)
    result = run(declarations, universe, quotes)
    assert result["cohort_evaluation_counts"] == {"D1": 14, "D2": 14}
    assert len(result["cohorts"]) == 28
    assert all(row["filled_entries"] == 0 and set(row["replay"].navs) == {Decimal(1)} for row in result["cohorts"])
    for arm in module.cohort.ARMS:
        assert result["aggregates"][arm][module.GROUP]["metrics"]["total_return"] is None
        assert result["original_budget_aggregates"][arm][module.GROUP]["assigned_capital"] == (0 if zero_budget else Decimal(".7"))
        calibration = module.blind_matched_calibration(result, PARAMETERS)[arm]
        assert calibration["detectable_daily_bps"] is None and not calibration["power_design_passed"]


def test_callback_mutation_is_rejected_before_book_and_failure_propagates(monkeypatch):
    universe, _, declarations, quotes = case()
    monkeypatch.setattr(module.cohort, "_execute_one_cohort", lambda *args, **kwargs: pytest.fail("must not replay"))
    def mutate(row): object.__setattr__(row.combo_declaration.trend_declaration.selection, "chosen_codes", ())
    with pytest.raises(ValueError, match="prepared inputs"):
        run(declarations, universe, quotes, before_cohort=mutate)
    universe, _, declarations, quotes = case()
    def fail(row): raise RuntimeError("synthetic durable intent failure")
    with pytest.raises(RuntimeError, match="durable intent"):
        run(declarations, universe, quotes, before_cohort=fail)


def test_raw_cash_context_is_issued_once_and_full_original_grid_is_reused(monkeypatch):
    declarations, base, raw = fixture.fixture.fixture.raw_cash_case()
    frontier = fixture.fixture.saved_frontier(declarations, raw.calendar)
    universe = module.trend.derive_certified_trend_universe(frontier, declarations, raw.calendar, parameters=PARAMETERS)
    originals = module.trend.build_trend_declarations(universe, (), parameters=PARAMETERS)
    combos = fixture.module.build_combo_declarations(originals, [], universe=universe, parameters=PARAMETERS)
    matched = module.build_matched_declarations(combos, universe=universe, parameters=PARAMETERS)
    real, calls = fixture.fixture.fixture.raw_view.validate_raw_quote_view, []
    def validate(value):
        calls.append(value)
        return real(value)
    monkeypatch.setattr(fixture.fixture.fixture.raw_view, "validate_raw_quote_view", validate)
    result = run(matched, universe, base, raw_quote_view=raw)
    assert len(calls) == 1 and len(result["cohorts"]) == 28


def test_v3_policy_inventory_and_five_issuer_attribution_survive_both_denominators(monkeypatch):
    connection = fixture.connection_fixture
    args = connection.rights_package(v3=True, component=True)
    binding = connection.module.prepare_cohort_scope(**args)
    universe = module.trend.derive_certified_trend_universe(fixture.fixture.saved_frontier(args["declarations"], args["quotes"].calendar),
        args["declarations"], args["quotes"].calendar, parameters=PARAMETERS)
    originals = module.trend.build_trend_declarations(universe, fixture.fixture.trend_observations(universe, prefer_activity=True), parameters=PARAMETERS)
    combos = fixture.module.build_combo_declarations(originals, fixture.audits_for(universe, originals), universe=universe, parameters=PARAMETERS)
    declarations = module.build_matched_declarations(combos, universe=universe, parameters=PARAMETERS)
    real, checked = module.verify_cohort_inventory, []
    def verify(binding, arm, result):
        checked.append(arm)
        real(binding, arm, result)
    monkeypatch.setattr(module, "verify_cohort_inventory", verify)
    matched = module.replay_matched_trend_cohorts(declarations, args["quotes"], binding.events,
        universe=universe, parameters=PARAMETERS, scope_binding=binding)
    original = fixture.module.replay_price_trend_activity_cohorts(combos, args["quotes"], binding.events,
        universe=universe, parameters=PARAMETERS, scope_binding=binding)
    report = module.comparison_report(matched, compact(original["cohorts"]), declarations=declarations,
        universe=universe, parameters=PARAMETERS, return_policy_json=binding.return_policy_json)
    assert len(checked) == len(declarations) and matched["conditional_exit_bounds_consumed"]
    for arm in module.cohort.ARMS:
        for group in ("groups", "supplemental_original_budget_groups"):
            for summary in report["arms"][arm][group].values():
                assert summary["return_policy_schema"] == module.SCHEMA_V3
                assert summary["automatic_bonus_share_assets"] == "not_modeled"
                assert not summary[module.cohort.ATTRIBUTION_KEY]["actual_investment_return"]
    with pytest.raises(ValueError, match="policy"):
        module.comparison_report(matched, compact(original["cohorts"]), declarations=declarations, universe=universe, parameters=PARAMETERS)


def test_saved_report_matches_primary_and_supplemental_paths_without_any_replay(monkeypatch):
    universe, declarations, matched, combos = pair()
    monkeypatch.setattr(module.cohort, "_execute_one_cohort", lambda *args, **kwargs: pytest.fail("report cannot replay"))
    report = module.comparison_report({**matched, "cohorts": compact(matched["cohorts"])}, combos,
        declarations=declarations, universe=universe, parameters=PARAMETERS)
    no_universe = module.comparison_report(_json_value({key: value for key, value in matched.items() if key not in ("aggregates", "original_budget_aggregates", "cohorts")}) | {"cohorts": compact(matched["cohorts"])},
        combos, declarations=module.restore_matched_declarations(_json_value(declarations)), parameters=PARAMETERS)
    assert no_universe == report
    for arm in module.cohort.ARMS:
        result = report["arms"][arm]
        assert result["matched_target_budget"] == Decimal(".05") and result["original_assigned_capital"] == Decimal(".1")
        assert len(result["by_formation"]) == 2
        assert result["by_formation"][0]["selection_overlap_codes"] == ()
        assert result["by_formation"][1]["matched_target_budget"] == 0
        for group in (module.GROUP, module.combo.GROUP):
            primary, supplemental = result["groups"][group], result["supplemental_original_budget_groups"][group]
            assert primary["metrics"]["total_return"] == supplemental["metrics"]["total_return"] * 2
            assert primary["net_pnl"] == supplemental["net_pnl"]
        assert sum(row["combo_minus_matched_pnl"] for row in result["by_formation_year"]) == result["combo_minus_matched"]["combo_minus_matched_pnl"]
    assert not report["realized_exposure_matched"] and not report["statistical_test_performed"]


@pytest.mark.parametrize("change", ("missing_cash", "wrong_group", "identity", "budget", "prefix", "path", "inventory_dates"))
def test_saved_report_rejects_missing_zero_groups_and_forged_input_contracts(change):
    universe, declarations, matched, combos = pair()
    saved = deepcopy(compact(matched["cohorts"]))
    if change == "missing_cash": saved = saved[:1] + saved[2:]
    elif change == "wrong_group": saved[0]["group_id"] = "COMBO"
    elif change == "identity": saved[0]["matched_selection_identity"] = "0" * 64
    elif change == "budget": saved[0]["assigned_capital"] = ".05"
    elif change == "prefix": saved[0]["chosen_codes"] = list(saved[0]["original_combo_chosen_codes"])
    elif change == "path": saved[0]["raw_metrics"]["path"].pop()
    elif change == "inventory_dates": saved[0]["exposure"]["path"][0]["date"] = "2000-01-01"
    with pytest.raises(ValueError):
        module.comparison_report({**matched, "cohorts": saved}, combos, declarations=declarations, universe=universe, parameters=PARAMETERS)


def test_saved_calibration_uses_each_group_own_matched_denominator_and_all_registered_ses(monkeypatch):
    universe, originals, declarations, quotes = case((2,) * 4)
    day = quotes.calendar[62]
    quotes = fixture.fixture.fixture.quotes(quotes.calendar, changes={
        (declarations[0].chosen_codes[0], day): (day.strftime("%Y%m%d"), "200", "201", "199", "200", "100", "10")})
    matched = run(declarations, universe, quotes)
    combos = compact(fixture.replay(originals, universe, quotes)["cohorts"])
    calls = []
    def calibrate(book, parameters):
        calls.append(book["returns"])
        return {"iid_session_se": 1e-9, "block_se": {"63": 2e-9, "126": 0.5e-9, "252": 1e-9}, "cost_floor_daily_bps": 43.54 / 126}
    monkeypatch.setattr(module, "calibration", calibrate)
    result = module.calibrate_saved_groups(combos, compact(matched["cohorts"]), declarations=declarations,
        universe=universe, parameters=PARAMETERS)
    assert len(calls) == 4 and calls[0] != calls[1]
    expected = (NormalDist().inv_cdf(.95) + NormalDist().inv_cdf(.8)) * 2e-9 * 10000
    for groups in result.values():
        assert set(groups) == {module.GROUP, "COMBO"}
        for group, row in groups.items():
            assert row["detectable_daily_bps"] == expected
            assert f"own {group} dispersion only" in row["interpretation"]
            assert not row["power_design_passed"] and not row["cost_floor_exposure_matched"]
    with localcontext() as context:
        context.prec = 9
        assert module.calibrate_saved_groups(combos, compact(matched["cohorts"]), declarations=declarations,
            universe=universe, parameters=PARAMETERS) == result


def test_blind_calibration_requires_its_genuine_group():
    _, _, matched, _ = pair((0,))
    matched["cohorts"][0]["group_id"] = "COMBO"
    with pytest.raises(ValueError, match="authentic TREND_MATCHED"):
        module.blind_matched_calibration(matched, PARAMETERS)
