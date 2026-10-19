"""Synthetic-only price-trend selection regressions; no data or replay imports."""
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, Inexact, MAX_EMAX, MIN_EMIN, localcontext
import hashlib

import pytest

from research.activity_timing import AvailabilityMetadata, KST
from research.price_trend_signal import PriceTrendObservation, select_price_trend


BASELINE = date(2020, 1, 2)
FORMATION = date(2020, 4, 1)
CUTOFF = datetime(2020, 4, 2, 8, 30, tzinfo=KST)


def observation(code, close, day):
    return PriceTrendObservation(code, f"ISIN-{code}", close,
        "synthetic-adjusted-close", "synthetic-snapshot", "synthetic-unit-declaration",
        AvailabilityMetadata(day, datetime.combine(day, datetime.min.time(), KST)
            + timedelta(hours=16), "unknown", "synthetic-provider", None, None,
            "assumed", availability_policy="synthetic-after-close",
            evidence_reference="synthetic-assumption"))


def case(prices=None, *, target_count=1):
    if prices is None:
        prices = (("A", Decimal("100"), Decimal("120")),
            ("B", Decimal("100"), Decimal("90")),
            ("C", Decimal("100"), Decimal("80")))
    return dict(expected_candidates=tuple((code, f"ISIN-{code}") for code, _, _ in prices),
        expected_baseline_on=tuple((code, BASELINE) for code, _, _ in prices),
        formation_on=FORMATION, selection_at=CUTOFF, target_count=target_count,
        observations=tuple(row for code, baseline, formation in prices for row in (
            observation(code, baseline, BASELINE), observation(code, formation, FORMATION))))


def test_top_k_relative_rank_and_conservative_result():
    result = select_price_trend(**case(target_count=2))
    assert result.ranked_codes == ("A", "B", "C")
    assert result.chosen_codes == ("A", "B")  # A negative trend is still chosen.
    assert result.eligible_shortfall == 0
    assert result.policy == "BT-trend-v1"
    assert result.seed == 20261005
    assert result.shortfall_policy == "retain_assigned_budget_in_cash"
    assert result.expected_baseline_on == (("A", BASELINE), ("B", BASELINE), ("C", BASELINE))
    assert result.formation_on == FORMATION and result.selection_at == CUTOFF
    assert not any((result.source_facts_certified, result.normal_history_certified,
        result.basis_comparability_certified, result.performance_computed,
        result.strategy_edge_established, result.promotion_permitted))
    with pytest.raises(FrozenInstanceError):
        result.chosen_codes = ()


@pytest.mark.parametrize("target_count,chosen,shortfall", [
    (0, (), 0), (3, ("A", "B", "C"), 0), (5, ("A", "B", "C"), 2)])
def test_zero_and_shortfall_preserve_target_ceiling(target_count, chosen, shortfall):
    result = select_price_trend(**case(target_count=target_count))
    assert result.chosen_codes == chosen
    assert result.target_count == target_count and result.eligible_shortfall == shortfall


def test_empty_explicit_population_retains_all_budget():
    result = select_price_trend(**case((), target_count=3))
    assert result.ranked_codes == result.chosen_codes == ()
    assert result.eligible_shortfall == 3


def test_exact_ties_and_identity_ignore_input_order():
    values = case((("A", Decimal("10.0"), Decimal("12")),
        ("B", Decimal("100"), Decimal("120.00")),
        ("C", Decimal("25"), Decimal("30"))), target_count=3)
    expected = tuple(sorted(("A", "B", "C"), key=lambda code: (
        hashlib.sha256(f"20261005:BT-trend-v1:{FORMATION.isoformat()}:{code}".encode()).digest(), code)))
    first = select_price_trend(**values)
    reversed_values = dict(values, expected_candidates=tuple(reversed(values["expected_candidates"])),
        expected_baseline_on=tuple(reversed(values["expected_baseline_on"])),
        observations=tuple(reversed(values["observations"])))
    assert first.ranked_codes == first.chosen_codes == expected
    assert select_price_trend(**reversed_values) == first
    assert select_price_trend(**dict(values, target_count=2)).ranking_identity != first.ranking_identity


@pytest.mark.parametrize("precision", [1, 7, 28, 200])
def test_close_ratios_and_huge_integer_prices_ignore_ambient_precision(precision):
    values = case((("A", Decimal(str(10 ** 2000)), Decimal(str(10 ** 2000 + 1))),
        ("B", Decimal(str(10 ** 1999)), Decimal(str(10 ** 1999 + 1))),
        ("C", Decimal("1"), Decimal("1"))), target_count=3)
    with localcontext() as context:
        context.prec = precision
        context.Emax = 1
        context.Emin = -1
        context.traps[Inexact] = True
        context.clear_flags()
        result = select_price_trend(**values)
        assert not any(context.flags.values())
    assert result.ranked_codes == ("B", "A", "C")


@pytest.mark.parametrize("exponent", [MAX_EMAX, MIN_EMIN])
def test_finite_extreme_exponents_do_not_overflow_cross_products(exponent):
    values = case((("A", Decimal((0, (1,), exponent)), Decimal((0, (2,), exponent))),
        ("B", Decimal((0, (1,), exponent)), Decimal((0, (3,), exponent)))))
    assert select_price_trend(**values).ranked_codes == ("B", "A")


@pytest.mark.parametrize("target_count", [0, 1])
@pytest.mark.parametrize("index", [4, 5])
def test_unselected_last_candidate_future_metadata_rejects_every_selection(target_count, index):
    values = case(target_count=target_count)
    rows = list(values["observations"])
    rows[index] = replace(rows[index], metadata=replace(rows[index].metadata,
        available_at=CUTOFF + timedelta(microseconds=1)))
    with pytest.raises(ValueError, match="available_at is later"):
        select_price_trend(**dict(values, observations=tuple(rows)))


def test_exact_cutoff_is_available_and_unknown_metadata_stays_explicit():
    values = case()
    rows = tuple(replace(row, metadata=replace(row.metadata, available_at=CUTOFF))
        for row in values["observations"])
    result = select_price_trend(**dict(values, observations=rows))
    assert result.chosen_codes == ("A",)
    assert all(row.metadata.retrieved_at == "unknown" and row.metadata.is_final is None
        and row.metadata.data_vintage is None for row in rows)


@pytest.mark.parametrize("field,new_value", [
    ("price_basis", "other-basis"), ("source_identity", "other-snapshot"),
    ("basis_reference", "other-unit-declaration"), ("metadata", "other-provider")])
def test_basis_snapshot_reference_and_supplier_must_agree_even_for_last_candidate(field, new_value):
    values = case()
    rows = list(values["observations"])
    if field == "metadata":
        new_value = replace(rows[-1].metadata, source=new_value)
    rows[-1] = replace(rows[-1], **{field: new_value})
    with pytest.raises(ValueError, match="common price basis"):
        select_price_trend(**dict(values, observations=tuple(rows)))


@pytest.mark.parametrize("problem", ["missing", "duplicate", "extra-code", "isin", "date"])
def test_no_missing_duplicate_extra_or_mismatched_quote_is_silently_removed(problem):
    values = case()
    rows = list(values["observations"])
    if problem == "missing":
        rows.pop()
    elif problem == "duplicate":
        rows.append(rows[-1])
    elif problem == "extra-code":
        rows[-1] = replace(rows[-1], code="UNKNOWN")
    elif problem == "isin":
        rows[-1] = replace(rows[-1], isin="OTHER-ISIN")
    else:
        rows[-1] = replace(rows[-1], metadata=replace(rows[-1].metadata,
            observation_date=FORMATION - timedelta(days=1)))
    with pytest.raises(ValueError):
        select_price_trend(**dict(values, observations=tuple(rows)))


@pytest.mark.parametrize("field,new_value", [
    ("expected_candidates", [("A", "ISIN-A")]),
    ("expected_candidates", (("A", "ISIN-A"), ("A", "ISIN-B"))),
    ("expected_candidates", (("A", "ISIN-A"), ("B", "ISIN-A"))),
    ("expected_candidates", ((" ", "ISIN-A"),)),
    ("expected_baseline_on", (("A", BASELINE),)),
    ("expected_baseline_on", (("A", BASELINE), ("A", BASELINE))),
    ("expected_baseline_on", (("A", BASELINE), ("B", BASELINE), ("C", FORMATION))),
    ("expected_baseline_on", (("A", BASELINE), ("B", BASELINE), ("C", CUTOFF))),
    ("observations", []), ("observations", ({},)),
    ("formation_on", CUTOFF), ("formation_on", CUTOFF.date() + timedelta(days=1)),
    ("selection_at", CUTOFF.replace(tzinfo=None)), ("selection_at", "2020-04-02"),
    ("target_count", -1), ("target_count", True), ("target_count", 1.0)])
def test_explicit_complete_structures_dates_and_integer_target_count_required(field, new_value):
    with pytest.raises(ValueError):
        select_price_trend(**dict(case(), **{field: new_value}))


@pytest.mark.parametrize("close", [0, 1, 1.5, "100", None, Decimal("0"), Decimal("-1"),
    Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_only_positive_finite_decimal_closes_are_accepted(close):
    with pytest.raises(ValueError, match="positive finite Decimal"):
        observation("A", close, BASELINE)


@pytest.mark.parametrize("field,new_value", [
    ("available_at", CUTOFF.replace(tzinfo=None)),
    ("retrieved_at", CUTOFF.replace(tzinfo=None)),
    ("source_public_available_at", CUTOFF + timedelta(days=1)),
    ("evidence_level", "verified"), ("source", ""), ("is_final", 1)])
def test_existing_availability_contract_rejects_invalid_metadata(field, new_value):
    row = observation("A", Decimal("100"), BASELINE)
    with pytest.raises(ValueError):
        replace(row.metadata, **{field: new_value})


def test_code_specific_baseline_date_is_checked_independently_of_observations():
    values = case()
    later = BASELINE + timedelta(days=1)
    with pytest.raises(ValueError, match="observation date"):
        select_price_trend(**dict(values,
            expected_baseline_on=(("A", later), ("B", BASELINE), ("C", BASELINE))))
    rows = tuple(replace(row, metadata=replace(row.metadata, observation_date=later,
        available_at=datetime(2020, 1, 3, 16, tzinfo=KST)))
        if row.code == "A" and row.metadata.observation_date == BASELINE else row
        for row in values["observations"])
    assert select_price_trend(**dict(values, observations=rows,
        expected_baseline_on=(("A", later), ("B", BASELINE), ("C", BASELINE)))).chosen_codes == ("A",)
