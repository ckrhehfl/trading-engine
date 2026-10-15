"""Equal-capital diagnostics use fabricated declarations and in-memory quotes."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D, localcontext
from pathlib import Path
import hashlib
import socket
import sqlite3

import pytest

from research import activity_cohort_comparison as module
from research.activity_accounting import FinalCashDistribution, PriceBasis, sum_decimal_values
from research.activity_book import CompulsorySpinOff, CompulsoryStockExchange, FinalCashPayment
from research.activity_cohort_selection import (
    CohortSelection, CONTROL_IDS, activity_targets_fingerprint, population_fingerprint, rank_control_codes,
)
from research.activity_failure_inputs import _typed
from research.activity_holding_inputs import _FINGERPRINT_SCOPE, _provenance, adapt_holding_scan
from research.activity_pair_calibration import PARAMETERS
from research.activity_preflight import digest, encoded
from research.activity_quote_extension import extend_holding_quotes
from research import activity_raw_quote_view as raw_view
from research.activity_raw_episode import extract_raw_episode_targets
from research.activity_timing import SessionLagPolicy
from research.krx_tax_schedule import KOSPI, total_bp


ACTIVITY = "a" * 64
SCOPE = "b" * 64
PARTITION = "c" * 64
CODES = tuple(f"{index:06d}" for index in range(1, 9))


def quotes(calendar, *, changes=None, scope=None, scope_pin=SCOPE):
    """Reproduce the existing typed boundary without a database or real input."""
    scope = {code: calendar[61:-2] for code in CODES} if scope is None else scope
    if not scope:
        return adapt_holding_scan(None, calendar, {}, quote_snapshot_sha256=None,
            read_scope_sha256=scope_pin, activity_snapshot_sha256=ACTIVITY)
    receipt = "2026-10-16T00:00:00+00:00"
    typed_rows, rows = [], {}
    for code, days in sorted(scope.items()):
        for day in days:
            raw = (day.strftime("%Y%m%d"), "100", "101", "99", "100", "100", "10")
            raw = (changes or {}).get((code, day), raw)
            typed_rows.append([code, day.isoformat(), "absent" if raw is None else [_typed(value) for value in raw]])
    fingerprint = {"schema": "activity-cached-price-parity-v1:typed-scan-v1",
        "calendar_dates": [day.strftime("%Y%m%d") for day in calendar], "read_scope_sha256": scope_pin,
        "intersection_code_dates": {code: [day.isoformat() for day in days] for code, days in sorted(scope.items())},
        "panel": [[["integer", "1"], ["text", "20190102"], ["text", "20260918"]]],
        "progress": [[code, [[_typed(value) for value in (
            "20190102", "20260918", len(calendar), 0, "done", receipt)]]] for code in sorted(scope)],
        "rows": typed_rows}
    sha = digest(encoded(fingerprint))
    for code, text, typed in typed_rows:
        day = date.fromisoformat(text)
        values = None if typed == "absent" else {field: value if kind in ("null", "text") else
            {"sqlite_storage": [kind, value]} for field, (kind, value) in zip(
                ("open", "high", "low", "close", "volume", "turnover"), typed[1:])}
        rows[code, day.strftime("%Y%m%d")] = {"values": values,
            "typed_values": None if typed == "absent" else typed,
            "provenance": _provenance(day, calendar, receipt, sha)}
    scan = {"rows": rows, "fingerprint": fingerprint, "typed_snapshot_sha256": sha,
        "snapshot_received_at": receipt, "fingerprint_scope": _FINGERPRINT_SCOPE,
        "whole_database_hash": False, "BI_snapshot_equality_checked": False,
        "progress_counts_recomputed_from_prices": False}
    return adapt_holding_scan(scan, calendar, fingerprint["intersection_code_dates"], quote_snapshot_sha256=sha,
        read_scope_sha256=scope_pin, activity_snapshot_sha256=ACTIVITY)


def case(ks=(2, 1), *, changes=None, scope=None):
    calendar = tuple(date(2026, 9, 18) - timedelta(days=314 - index) for index in range(317))
    population = tuple((code, f"synthetic_ISIN_{code}") for code in CODES)
    result = []
    for lag in (1, 2):
        arm, policy = f"D{lag}", SessionLagPolicy(lag, paired=True)
        for index, k in zip((60, 186), ks, strict=True):
            formation, decision, cutoff = calendar[index], policy.execution_on(calendar, index), policy.selection_at(calendar, index)
            targets = tuple(sorted(CODES, key=lambda code: hashlib.sha256(
                f"{PARAMETERS['seed']}:{formation}:{code}".encode()).digest()))[:k]
            for group in ("ACTIVITY", *CONTROL_IDS):
                prefix = () if group == "ACTIVITY" or k == 0 else tuple((code, "eligible", "d" * 64)
                    for code in rank_control_codes(CODES, control_id=group, formation_on=formation)[:k])
                result.append(CohortSelection(group, arm, formation, decision, cutoff, PARAMETERS["seed"], targets,
                    activity_targets_fingerprint(arm=arm, formation_on=formation, decision_on=decision,
                        selection_at=cutoff, targets=targets, seed=PARAMETERS["seed"]),
                    population, len(population), population_fingerprint(population), PARTITION, prefix))
    return tuple(result), quotes(calendar, changes=changes, scope=scope)


def run(values, *, events=(), keys=None, parameters=None):
    declarations, holding = values
    return module.replay_equal_capital_cohorts(declarations, holding, events,
        parameters=deepcopy(PARAMETERS) if parameters is None else parameters, cohort_keys=keys)


def cohort(result, arm="D1", group="ACTIVITY", formation=None):
    return next(row for row in result["cohorts"] if row["arm"] == arm and row["group_id"] == group
        and (formation is None or row["formation_on"] == formation))


def test_complete_cohorts_keep_fixed_twenty_slot_budget_costs_and_common_paths(monkeypatch):
    values = case()
    for owner, name in ((Path, "open"), (socket, "create_connection"), (sqlite3, "connect")):
        monkeypatch.setattr(owner, name, lambda *args, **kwargs: pytest.fail("unexpected IO"))
    result = run(values)
    assert len(result["cohorts"]) == 24
    assert result["cohort_evaluation_counts"] == {"D1": 12, "D2": 12}
    assert result["comparison_power_prerequisite_met"] is result["power_design_passed"] is False
    cost = D("6.77") / 10000
    expected_dates = values[1].calendar[61:315]
    for row in result["cohorts"]:
        replay = row["replay"]
        assert tuple(book.as_of for book in replay.books) == expected_dates
        assert row["assigned_capital"] == row["filled_capital"] == D(row["target_ceiling"]) / 20
        entry = next(book for book in replay.books if book.as_of == row["decision_on"])
        assert entry.cash == 1 - row["assigned_capital"]
        assert all(lot.shares == D("0.05") / (100 * (1 + cost)) for lot in entry.lots)
        principal = sum_decimal_values(tuple(lot.shares * 100 for lot in entry.lots))
        assert row["filled_principal"] == principal < row["filled_capital"]
        assert sum_decimal_values((principal, row["entry_cost_amount"])) == row["filled_capital"]
        exposure = next(point for point in row["exposure"]["path"] if point["date"] == row["decision_on"])
        assert exposure["capital_attribution_nav"] == sum_decimal_values((row["assigned_capital"], module._difference(entry.nav, D(1))))
        assert exposure["marked_inventory_value"] == principal
        assert exposure["marked_inventory_nav_weight"] == principal / exposure["capital_attribution_nav"]
        assert exposure["auxiliary_raw_books_nav_weight"] == principal / entry.nav
        assert all(lot.due_on == values[1].calendar[values[1].calendar.index(row["decision_on"]) + 126] for lot in entry.lots)
        assert sum(bool(codes) for _, codes in replay.selections) == 1
        assert row["terminal_open_lots"] == 0 and row["ordinary_closed_trades"] == row["target_ceiling"]
    for arm in module.ARMS:
        aggregate = result["aggregates"][arm]["ACTIVITY"]
        assert aggregate["assigned_capital"] == D("0.15")
        assert result["comparisons"][arm]["known_control_count"] == 5
        assert result["comparisons"][arm]["median"] == D(0)
        assert result["comparisons"][arm]["ties"] == 5
    assert result["promotion_allowed"] is result["actual_study_completed"] is False
    assert result["actual_input_reuse_readiness_established"] is result["conditional_exit_bounds_consumed"] is False


def test_group_return_normalizes_total_assigned_capital_instead_of_number_of_cohorts():
    values = case((1, 3))
    calendar = values[1].calendar
    changes = {}
    for code in CODES:
        for index in range(187, 315):
            changes[code, calendar[index]] = (calendar[index].strftime("%Y%m%d"), "120", "121", "119", "120", "100", "10")
    values = case((1, 3), changes=changes)
    result = run(values)
    for arm in module.ARMS:
        rows = tuple(row for row in result["cohorts"] if row["arm"] == arm and row["group_id"] == "ACTIVITY")
        aggregate = result["aggregates"][arm]["ACTIVITY"]
        expected = sum_decimal_values(tuple(module._difference(row["replay"].navs[-1], D(1)) for row in rows)) / D("0.20")
        assert aggregate["metrics"]["total_return"] == expected
        for index, point in enumerate(aggregate["metrics"]["path"]):
            expected_pnl = sum_decimal_values(tuple(module._difference(row["replay"].navs[index], D(1)) for row in rows))
            assert point["nav"] == sum_decimal_values((D(1), expected_pnl / D("0.20")))
        assert aggregate["metrics"]["total_return"] != sum(row["metrics"]["total_return"] for row in rows) / 2
    # A one-cohort invocation gives that cohort's own assigned-capital result.
    row = cohort(result)
    one = run(values, keys=(row["key"],))
    assert len(one["cohorts"]) == 1 and one["comparisons"]["D1"] is None
    assert one["aggregates"]["D1"]["ACTIVITY"]["metrics"] == row["metrics"]
    # A future cohort's k changes only retrospective attribution, never this
    # earlier cohort's target choice, allocation or book path.
    earlier_only = run(case((1, 0), changes=changes), keys=(row["key"],))
    assert cohort(earlier_only)["replay"] == row["replay"]


@pytest.mark.parametrize("state", ["absent", "frozen"])
def test_failed_entries_stay_cash_without_replacement_or_denominator_change(state):
    declarations, holding = case((2, 0))
    calendar = holding.calendar
    changes = {}
    for day in (calendar[61], calendar[62]):
        for code in CODES:
            changes[code, day] = None if state == "absent" else (day.strftime("%Y%m%d"), *("100",) * 4, "0", "0")
    result = run(case((2, 0), changes=changes))
    assert all(row["filled_capital"] == 0 and row["filled_entries"] == 0 for row in result["cohorts"])
    for arm in module.ARMS:
        for aggregate in result["aggregates"][arm].values():
            assert aggregate["assigned_capital"] == D("0.10")
            assert aggregate["metrics"]["total_return"] == 0 and aggregate["metrics"]["maximum_drawdown"] == 0
    assert all(book.cash == book.nav == 1 and not book.lots for row in result["cohorts"] for book in row["replay"].books)


def test_all_zero_targets_preserve_declarations_and_unknown_normalized_returns():
    result = run(case((0, 0), scope={}))
    assert len(result["cohorts"]) == 24 and result["quote_snapshot_sha256"] is None
    for row in result["cohorts"]:
        assert row["assigned_capital"] == 0 and row["metrics"]["total_return"] is None
        assert row["raw_metrics"]["total_return"] == 0
    for arm in module.ARMS:
        assert result["comparisons"][arm]["median"] is None
        assert result["comparisons"][arm]["known_control_count"] == 0
        for aggregate in result["aggregates"][arm].values():
            assert aggregate["positive_assigned_cohort_count"] == 0
            assert aggregate["metrics"]["total_return"] is aggregate["metrics"]["session_return_sd"] is None
            assert aggregate["exposure"]["mean_marked_inventory_nav_weight"] is None
            assert aggregate["exposure"]["nav_weight_session_counts"] == {
                "defined_session_count": 0, "undefined_session_count": 254}


def test_delayed_exit_retains_full_holding_until_available_open_and_conserves_cash():
    declarations, holding = case((1, 0))
    chosen = next(row for row in declarations if row.arm == "D1" and row.group_id == "ACTIVITY")
    code, calendar = chosen.chosen_codes[0], holding.calendar
    due = calendar[187]
    changes = {(code, due): (due.strftime("%Y%m%d"), *("100",) * 4, "0", "0")}
    result = run(case((1, 0), changes=changes), keys=(("D1", "ACTIVITY", calendar[60]),))
    row = cohort(result)
    on_due = next(book for book in row["replay"].books if book.as_of == due)
    sold = next(book for book in row["replay"].books if book.as_of == calendar[188])
    assert on_due.slot_count == 1 and sold.slot_count == 0
    assert dict(row["replay"].diagnostics)["delayed_exits"] == 1
    tax = D(str(total_bp(KOSPI, calendar[188], calendar)))
    expected = on_due.cash + on_due.lots[0].shares * 100 * (1 - (D("6.77") + tax) / 10000)
    assert sold.cash == sold.nav == expected
    assert row["assigned_capital"] == row["filled_capital"] == D("0.05")


def test_pending_final_payment_retains_rights_and_is_not_fabricated_as_an_exit():
    declarations, holding = case((1, 0))
    original = next(row for row in declarations if row.arm == "D1" and row.group_id == "ACTIVITY")
    code, calendar = original.chosen_codes[0], holding.calendar
    changes = {(code, day): None for day in calendar[100:315]}
    values = case((1, 0), changes=changes)
    basis = PriceBasis(code, calendar[99], D(100), D(100), values[1].quote_snapshot_sha256, "synthetic raw/raw")
    event = FinalCashPayment("pending", FinalCashDistribution(code, calendar[99], calendar[101],
        calendar[-1] + timedelta(days=3), D(110), basis, "synthetic final payout"))
    result = run(values, events=(event,), keys=(("D1", "ACTIVITY", calendar[60]),))
    row = cohort(result)
    assert row["terminal_open_lots"] == row["terminal_open_investments"] == 1
    assert row["ordinary_closed_trades"] == 0 and row["replay"].books[-1].cash == D("0.95")
    assert dict(row["replay"].diagnostics)["pending_exit_sessions"] > 0
    assert row["replay"].navs[-1] == row["replay"].navs[38]


def test_spin_off_pending_components_keep_one_assigned_slot_and_exact_value_conservation():
    declarations, holding = case((1, 0))
    original = next(row for row in declarations if row.arm == "D1" and row.group_id == "ACTIVITY")
    old, new, calendar = original.chosen_codes[0], "999999", holding.calendar
    scope = {code: calendar[61:315] for code in CODES}
    scope[new] = calendar[100:315]
    changes = {(new, day): None for day in calendar[100:315]}
    changes[old, calendar[100]] = None
    values = case((1, 0), scope=scope, changes=changes)
    pin = values[1].quote_snapshot_sha256
    def basis(code, index):
        return PriceBasis(code, calendar[index], D(100), D(100), pin, "synthetic raw/raw")
    event = CompulsorySpinOff("spin", D("0.5"), D("0.5"), basis(old, 99), basis(old, 101),
        basis(new, 315), calendar[98], calendar[99], calendar[100], calendar[101], calendar[315],
        D("0.5"), "synthetic carry", "assumed", "synthetic final terms")
    result = run(values, events=(event,), keys=(("D1", "ACTIVITY", calendar[60]),))
    row = cohort(result)
    before = next(book for book in row["replay"].books if book.as_of == calendar[99])
    after = next(book for book in row["replay"].books if book.as_of == calendar[100])
    assert after.slot_count == 1 and len(after.lots) == 2
    assert sum_decimal_values(tuple(lot.marked_value for lot in after.lots)) == sum_decimal_values(tuple(lot.marked_value for lot in before.lots))
    assert row["terminal_open_lots"] == row["terminal_open_investments"] == 1
    assert row["ordinary_closed_trades"] == 1
    assert row["filled_capital"] == D("0.05")


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "late_cutoff", "wrong_decision", "targets", "population",
    "partition", "parameters", "numeric_type", "cost", "quote_pin", "quote_series", "coverage", "unknown_quote", "calendar"])
def test_all_family_metadata_and_quotes_fail_before_the_first_replay(corruption, monkeypatch):
    declarations, holding = case()
    parameters = deepcopy(PARAMETERS)
    if corruption == "missing": declarations = declarations[:-1]
    elif corruption == "duplicate": declarations = (*declarations, declarations[-1])
    elif corruption in ("late_cutoff", "wrong_decision", "targets", "population", "partition"):
        row = deepcopy(declarations[-1])
        field, value = {
            "late_cutoff": ("selection_at", row.selection_at + timedelta(minutes=1)),
            "wrong_decision": ("decision_on", row.decision_on + timedelta(days=1)),
            "targets": ("activity_targets", ()),
            "population": ("source_population_sha256", "e" * 64),
            "partition": ("source_partition_sha256", "e" * 64),
        }[corruption]
        object.__setattr__(row, field, value)
        declarations = (*declarations[:-1], row)
    elif corruption == "parameters": parameters["holding_sessions"] = 125
    elif corruption == "numeric_type": parameters["threshold"] = 3
    elif corruption == "cost": parameters["slippage_bps_per_side"] = 0.0
    elif corruption == "quote_pin": holding = replace(holding, quote_snapshot_sha256="e" * 64)
    elif corruption == "quote_series": holding = replace(holding, series=())
    elif corruption == "coverage":
        scope = {code: holding.calendar[62:315] for code in CODES}
        holding = quotes(holding.calendar, scope=scope)
    elif corruption == "unknown_quote":
        final = declarations[-1]
        day, code = final.decision_on, final.chosen_codes[0]
        raw = (day.strftime("%Y%m%d"), "100", "101", "99", None, "100", "10")
        holding = quotes(holding.calendar, changes={(code, day): raw})
    else: holding = replace(holding, calendar=holding.calendar[:-1])
    monkeypatch.setattr(module, "replay_synthetic", lambda *args, **kwargs: pytest.fail("replay before complete validation"))
    with pytest.raises(ValueError): run((declarations, holding), parameters=parameters)


@pytest.mark.parametrize("corruption", ["duplicate", "snapshot", "quote_conflict", "unknown_type"])
def test_event_contracts_are_validated_before_any_replay(corruption, monkeypatch):
    declarations, holding = case((1, 0))
    code, calendar = declarations[0].chosen_codes[0], holding.calendar
    values = case((1, 0), changes={(code, day): None for day in calendar[100:315]})
    pin = "e" * 64 if corruption == "snapshot" else values[1].quote_snapshot_sha256
    basis = PriceBasis(code, calendar[99], D(100), D(100), pin, "synthetic basis")
    event = FinalCashPayment("payout", FinalCashDistribution(code, calendar[99], calendar[101], calendar[200], D(100), basis, "synthetic payment"))
    events = (event, event) if corruption == "duplicate" else (object(),) if corruption == "unknown_type" else (event,)
    if corruption == "quote_conflict":
        values = declarations, holding
        basis = replace(basis, dataset_sha256=holding.quote_snapshot_sha256)
        events = (replace(event, distribution=replace(event.distribution, basis=basis)),)
    monkeypatch.setattr(module, "replay_synthetic", lambda *args, **kwargs: pytest.fail("replay before event validation"))
    with pytest.raises(ValueError): run(values, events=events)


def test_exact_saved_navs_survive_without_float_conversion_or_context_mutation():
    values = case((1, 0))
    key = ("D1", "ACTIVITY", values[1].calendar[60])
    with localcontext() as context:
        context.prec = 7
        result = run(values, keys=(key,))
        assert context.prec == 7
    row = cohort(result)
    assert all(type(point["nav"]) is D and point["nav"] == nav for point, nav in
        zip(row["raw_metrics"]["path"], row["replay"].navs, strict=True))


def test_overlapping_original_and_successor_scopes_cannot_erase_an_original_entry_coordinate(monkeypatch):
    declarations, holding = case((8, 0))
    calendar = holding.calendar
    old, new = CODES[:2]
    scope = {code: calendar[62:315] if code == new else calendar[61:315] for code in CODES}
    changes = {(old, day): None for day in calendar[100:315]}
    values = case((8, 0), scope=scope, changes=changes)
    pin = values[1].quote_snapshot_sha256
    event = CompulsoryStockExchange("merge", D(1),
        PriceBasis(old, calendar[99], D(100), D(100), pin, "synthetic old basis"),
        PriceBasis(new, calendar[100], D(100), D(100), pin, "synthetic new basis"),
        calendar[100], calendar[100], "synthetic compulsory event")
    monkeypatch.setattr(module, "replay_synthetic", lambda *args, **kwargs: pytest.fail("erased original scope reached replay"))
    with pytest.raises(ValueError, match="complete chosen and possible descendant"):
        run(values, events=(event,))


def test_control_delta_distribution_matches_all_five_fixed_controls():
    values = case((2, 0))
    calendar = values[1].calendar
    changes = {}
    for code in CODES:
        price = 100 + int(code)
        for day in calendar[63:315]:
            changes[code, day] = (day.strftime("%Y%m%d"), str(price), str(price + 1), str(price - 1), str(price), "100", "10")
    values = case((2, 0), changes=changes)
    keys = tuple(("D1", group, calendar[60]) for group in module.GROUPS)
    result = run(values, keys=keys)
    comparison = result["comparisons"]["D1"]
    original = result["aggregates"]["D1"]["ACTIVITY"]["metrics"]["total_return"]
    deltas = tuple(module._difference(original, result["aggregates"]["D1"][group]["metrics"]["total_return"])
        for group in CONTROL_IDS)
    assert comparison["activity_minus_control"] == dict(zip(CONTROL_IDS, deltas, strict=True))
    assert comparison["median"] == sorted(deltas)[2]
    assert comparison["minimum"] == min(deltas) and comparison["maximum"] == max(deltas)
    assert comparison["positive_count"] == sum(value > 0 for value in deltas)
    assert comparison["ties"] == sum(value == 0 for value in deltas)
    assert any(value != 0 for value in deltas)


def test_zero_funded_nav_exposure_keeps_undefined_ratios_separate_from_zero():
    days = (date(2026, 1, 1), date(2026, 1, 2))
    result = module._exposure(days, (D("0.05"), D(0)), (D(1), D("0.95")), D("0.05"), (D(0), D("-0.05")))
    assert result["path"][0]["marked_inventory_nav_weight"] == 1
    assert result["path"][1]["marked_inventory_nav_weight"] is None
    assert result["mean_marked_inventory_nav_weight"] == 1
    assert result["nav_weight_session_counts"] == {"defined_session_count": 1, "undefined_session_count": 1}


@pytest.mark.parametrize("keys", [(), (("D3", "ACTIVITY", date(2026, 1, 1)),),
    (("D1", "ACTIVITY", date(2026, 1, 1)),) * 2])
def test_invalid_subset_never_reaches_replay(keys, monkeypatch):
    monkeypatch.setattr(module, "replay_synthetic", lambda *args, **kwargs: pytest.fail("invalid cohort key reached replay"))
    with pytest.raises(ValueError, match="cohort keys"):
        run(case(), keys=keys)


def test_raw_stock_successor_needs_explicit_rows_even_before_delivery(monkeypatch):
    declarations, holding = case((1, 0))
    calendar, old = holding.calendar, declarations[0].chosen_codes[0]
    original = quotes(calendar, changes={(old, day): None for day in calendar[100:315]})
    extension = quotes(calendar, scope={"383800": calendar[101:315]}, scope_pin="f" * 64)
    base = extend_holding_quotes(original, extension)
    plan = dict(schema=raw_view.PLAN_SCHEMA, activity_snapshot_sha256=base.activity_snapshot_sha256,
        base_quote_snapshot_sha256=base.quote_snapshot_sha256, calendar_sha256=raw_view.calendar_sha256(calendar),
        episodes=[dict(code="003550", start=str(calendar[100]), end=str(calendar[100])),
                  dict(code="383800", start=str(calendar[101]), end=str(calendar[314]))], **raw_view.UNITS)
    observations = []
    for episode in plan["episodes"]:
        code = episode["code"]
        for day in calendar:
            if episode["start"] <= str(day) <= episode["end"]:
                source = dict(ISU_CD=code, BAS_DD=day.strftime("%Y%m%d"), TDD_OPNPRC="100", TDD_HGPRC="101",
                    TDD_LWPRC="99", TDD_CLSPRC="100", ACC_TRDVOL="100", ACC_TRDVAL="10")
                observations.extend(extract_raw_episode_targets([source], dict(service="stk_bydd_trd",
                    bas_dd=day.strftime("%Y%m%d"), targets=[code]), "2026-10-16T00:00:00+00:00", "d" * 64))
    plan_bytes = encoded(plan)
    artifact = raw_view.encode_raw_artifact(base, tuple(observations), digest(plan_bytes))
    view = raw_view.build_raw_quote_view(base, tuple(observations), source_artifact=artifact,
        source_artifact_sha256=digest(artifact), unit_plan=plan_bytes, unit_plan_sha256=digest(plan_bytes))
    event = CompulsoryStockExchange("raw-successor", D(1),
        PriceBasis(old, calendar[99], D(100), D(100), view.quote_snapshot_sha256, "synthetic old basis"),
        PriceBasis("383800", calendar[101], D(100), D(100), view.quote_snapshot_sha256, "synthetic raw basis"),
        calendar[100], calendar[101], "synthetic compulsory event")
    monkeypatch.setattr(module, "replay_synthetic", lambda *args, **kwargs: pytest.fail("missing pending raw row reached replay"))
    with pytest.raises(ValueError, match="complete chosen and possible descendant"):
        module.replay_equal_capital_cohorts(declarations, base, (event,), parameters=deepcopy(PARAMETERS), raw_quote_view=view)


@pytest.mark.parametrize("reverse", [False, True])
def test_same_day_exchange_chain_refuses_before_any_book(monkeypatch, reverse):
    declarations, holding = case((1, 0))
    calendar, old, middle, new = holding.calendar, declarations[0].chosen_codes[0], "999998", "999999"
    scope = {code: calendar[61:315] for code in (*CODES, middle, new)}
    changes = {(code, day): None for code in (old, middle) for day in calendar[100:315]}
    values = case((1, 0), scope=scope, changes=changes)
    pin = values[1].quote_snapshot_sha256
    def basis(code, index):
        return PriceBasis(code, calendar[index], D(100), D(100), pin, "synthetic basis")
    events = (CompulsoryStockExchange("first", D(1), basis(old, 99), basis(middle, 100),
        calendar[100], calendar[100], "synthetic first event"),
        CompulsoryStockExchange("second", D(1), basis(middle, 99), basis(new, 100),
        calendar[100], calendar[100], "synthetic second event"))
    monkeypatch.setattr(module, "replay_synthetic", lambda *args, **kwargs: pytest.fail("unsupported chain reached replay"))
    with pytest.raises(ValueError, match="unsupported re-exchange of a pending entitlement"):
        run(values, events=tuple(reversed(events)) if reverse else events)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("payment_delay", [0, 20])
def test_final_payout_record_before_successor_delivery_refuses_in_both_orders(monkeypatch, reverse, payment_delay):
    declarations, holding = case((1, 0))
    calendar, old, new = holding.calendar, declarations[0].chosen_codes[0], "999998"
    scope = {code: calendar[61:315] for code in (*CODES, new)}
    changes = {(old, day): None for day in calendar[100:315]}
    changes.update({(new, day): None for day in calendar[99:315]})
    values = case((1, 0), scope=scope, changes=changes)
    pin = values[1].quote_snapshot_sha256
    def basis(code, index):
        return PriceBasis(code, calendar[index], D(100), D(100), pin, "synthetic basis")
    exchange = CompulsoryStockExchange("exchange", D(1), basis(old, 99), basis(new, 100),
        calendar[100], calendar[100], "synthetic exchange")
    payment = FinalCashPayment("payment", FinalCashDistribution(new, calendar[98], calendar[99],
        calendar[100 + payment_delay], D(100), basis(new, 98), "synthetic payment"))
    events = (payment, exchange) if reverse else (exchange, payment)
    monkeypatch.setattr(module, "replay_synthetic", lambda *args, **kwargs: pytest.fail("invalid payout graph reached replay"))
    with pytest.raises(ValueError, match="unsupported final payout eligibility before parent delivery"):
        run(values, events=events)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("child_kind", ["stock", "spin_cutoff", "spin_effect"])
def test_pending_parent_delivery_cannot_cross_child_boundaries(monkeypatch, reverse, child_kind):
    declarations, holding = case((1, 0))
    calendar, old, middle, new = holding.calendar, declarations[0].chosen_codes[0], "999998", "999999"
    scope = {code: calendar[61:315] for code in (*CODES, middle, new)}
    changes = {(old, day): None for day in calendar[100:315]}
    if child_kind == "stock":
        changes.update({(middle, day): None for day in calendar[110:315]})
    values = case((1, 0), scope=scope, changes=changes)
    pin = values[1].quote_snapshot_sha256
    def basis(code, index):
        return PriceBasis(code, calendar[index], D(100), D(100), pin, "synthetic basis")
    delivery = {"stock": 111, "spin_cutoff": 100, "spin_effect": 110}[child_kind]
    parent = CompulsoryStockExchange("parent", D(1), basis(old, 99), basis(middle, delivery),
        calendar[100], calendar[delivery], "synthetic parent")
    if child_kind == "stock":
        child = CompulsoryStockExchange("child", D(1), basis(middle, 109), basis(new, 110),
            calendar[110], calendar[110], "synthetic child")
        message = "unsupported re-exchange of a pending entitlement"
    else:
        cutoff = 99 if child_kind == "spin_cutoff" else 110
        child = CompulsorySpinOff("child", D("0.5"), D("0.5"), basis(middle, 109), basis(middle, 110),
            basis(new, 112), calendar[cutoff], calendar[cutoff], calendar[110], calendar[110], calendar[112],
            D("0.5"), "synthetic carry", "assumed", "synthetic child")
        message = "unsupported spin-off eligibility before parent delivery"
    events = (child, parent) if reverse else (parent, child)
    monkeypatch.setattr(module, "replay_synthetic", lambda *args, **kwargs: pytest.fail("invalid delivery graph reached replay"))
    with pytest.raises(ValueError, match=message):
        run(values, events=events)


def final_successor_case(payment_delay):
    declarations, holding = case((1, 0))
    calendar, old, new = holding.calendar, declarations[0].chosen_codes[0], "999998"
    scope = {code: calendar[61:315] for code in (*CODES, new)}
    changes = {(code, day): None for code in (old, new) for day in calendar[100:315]}
    values = case((1, 0), scope=scope, changes=changes)
    pin = values[1].quote_snapshot_sha256
    def basis(code, index):
        return PriceBasis(code, calendar[index], D(100), D(100), pin, "synthetic basis")
    exchange = CompulsoryStockExchange("exchange", D(1), basis(old, 99), basis(new, 100),
        calendar[100], calendar[100], "synthetic exchange")
    payment = FinalCashPayment("payment", FinalCashDistribution(new, calendar[99], calendar[100],
        calendar[100 + payment_delay], D(100), basis(new, 99), "synthetic payment"))
    return values, (exchange, payment)


@pytest.mark.parametrize("reverse", [False, True])
def test_same_day_final_payment_and_successor_creation_refuse_when_record_equals_delivery(monkeypatch, reverse):
    values, events = final_successor_case(0)
    monkeypatch.setattr(module, "replay_synthetic", lambda *args, **kwargs: pytest.fail("same-day payout tie reached replay"))
    with pytest.raises(ValueError, match="unsupported same-day final payout after compulsory successor creation"):
        run(values, events=tuple(reversed(events)) if reverse else events)


@pytest.mark.parametrize("reverse", [False, True])
def test_later_final_payment_allows_delivery_on_record_date(reverse):
    values, events = final_successor_case(20)
    key = ("D1", "ACTIVITY", values[1].calendar[60])
    result = run(values, events=tuple(reversed(events)) if reverse else events, keys=(key,))
    row = cohort(result)
    assert row["ordinary_closed_trades"] == row["terminal_open_lots"] == row["terminal_open_investments"] == 0
    assert row["replay"].books[-1].applied_event_ids == ("exchange", "payment")
    with localcontext() as context:
        context.prec = 28
        assert row["replay"].books[-1].cash == D("0.95") + row["filled_principal"]


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("kinds", [("stock", "stock"), ("stock", "spin"), ("stock", "final"),
    ("spin", "spin"), ("spin", "final"), ("final", "final")])
def test_conflicting_full_issue_actions_refuse_in_both_orders(monkeypatch, reverse, kinds):
    declarations, holding = case((1, 0))
    calendar, old = holding.calendar, declarations[0].chosen_codes[0]
    scope = {code: calendar[61:315] for code in (*CODES, "999998", "999999")}
    values = case((1, 0), scope=scope, changes={(old, day): None for day in calendar[100:315]})
    pin = values[1].quote_snapshot_sha256
    def basis(code, index):
        return PriceBasis(code, calendar[index], D(100), D(100), pin, "synthetic basis")
    def action(kind, ordinal):
        new = ("999998", "999999")[ordinal]
        identifier = f"event-{ordinal}"
        if kind == "stock":
            return CompulsoryStockExchange(identifier, D(1), basis(old, 99), basis(new, 100),
                calendar[100], calendar[100], "synthetic exchange")
        if kind == "spin":
            return CompulsorySpinOff(identifier, D("0.5"), D("0.5"), basis(old, 99), basis(old, 100),
                basis(new, 101), calendar[98], calendar[99], calendar[100], calendar[100], calendar[101],
                D("0.5"), "synthetic carry", "assumed", "synthetic spin")
        return FinalCashPayment(identifier, FinalCashDistribution(old, calendar[99], calendar[101],
            calendar[120 + ordinal], D(100), basis(old, 99), "synthetic payment"))
    events = tuple(action(kind, ordinal) for ordinal, kind in enumerate(kinds))
    monkeypatch.setattr(module, "replay_synthetic", lambda *args, **kwargs: pytest.fail("conflicting old-issue actions reached replay"))
    with pytest.raises(ValueError, match="conflicting full-issue events"):
        run(values, events=tuple(reversed(events)) if reverse else events)
