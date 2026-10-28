"""Invented fixtures exercising the actual-input API; no source files or IO."""

from dataclasses import asdict, replace
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from research import intraday_context_outcomes as outcomes
from research import intraday_context_preparation as preparation
from test_intraday_context_preparation import MINUTE, path, prepare


class LegacyFractionDecimal(Decimal):
    """Exercise the retained all-Fraction validation path with identical values."""


def legacy_prices(values):
    return {at: tuple(LegacyFractionDecimal(value) if isinstance(value, Decimal) else value
                      for value in prices) if prices is not None else None
            for at, prices in values.items()}


def source_for(candidate):
    # Invented metadata pretending to be a caller-authenticated saved source.
    return outcomes.OutcomeSource(
        candidate.target.symbol, candidate.target.session_date,
        candidate.target.policy, candidate.target.price_basis,
        replace(candidate.target.price_source, source="invented_saved_source",
                data_vintage="invented-source-v2", evidence_level="observed"),
        "a" * 64, "invented-EB-exploration-authorization")


def evaluate(candidate, values, **overrides):
    kwargs = {"source": source_for(candidate), "inputs_authenticated": True}
    kwargs.update(overrides)
    return outcomes.exploratory_outcome(candidate, values, **kwargs)


@pytest.mark.parametrize("setup", preparation.SETUPS)
@pytest.mark.parametrize("case", ["cap", "stop", "invalid_entry", "missing_entry", "gap", "missing_exit"])
def test_exact_same_label_arithmetic_and_distinct_evidence(setup, case):
    candidate = prepare(setup)
    values = path(candidate)
    if case == "stop":
        stop = Decimal(candidate.structural_stop.numerator) / candidate.structural_stop.denominator
        values[candidate.entry_at] = (Decimal("110"), Decimal("112"), stop, stop)
        values[candidate.entry_at + 4 * MINUTE] = (Decimal("80"), None, object(), "future")
    elif case == "invalid_entry":
        values = {candidate.entry_at: (Decimal("80"), None, object(), "future")}
    elif case in {"missing_entry", "gap", "missing_exit"}:
        offset = {"missing_entry": 0, "gap": 1, "missing_exit": 60}[case]
        del values[candidate.entry_at + offset * MINUTE]
    expected = preparation.synthetic_outcome(candidate, values, synthetic=True)
    result = evaluate(candidate, values)
    assert {key: result[key] for key in expected if key != "evidence_level"} == {
        key: value for key, value in expected.items() if key != "evidence_level"}
    assert expected["evidence_level"] == "synthetic"
    assert result["evidence_level"] == "assumed"
    assert result["source"] == asdict(source_for(candidate))
    assert result["source"]["price_source"]["evidence_level"] == "observed"
    assert result["exploratory_only"] and result["assumed_fill"]
    assert not result["historical_timing_certified"]
    assert not candidate.actual_execution_authorized
    if case == "stop":
        assert result["exit_at"] == candidate.entry_at + 4 * MINUTE
        assert result["gross"] == Fraction(-3, 11)
        assert result["primary_net"] == Fraction(-3, 11) - Fraction(33, 10000)
        assert result["stress_net"] == Fraction(-3, 11) - Fraction(43, 10000)


@pytest.mark.parametrize("stop_exit", [False, True])
def test_exit_open_only_and_no_later_observation_requirement(stop_exit):
    candidate = prepare()
    values = path(candidate)
    exit_at = candidate.cap_at
    if stop_exit:
        values[candidate.entry_at] = tuple(map(Decimal, ("110", "112", "106", "106")))
        exit_at = candidate.entry_at + 4 * MINUTE
    values[exit_at] = (Decimal("100"), None, object(), "future close")
    source = source_for(candidate)
    source = replace(source, price_source=replace(source.price_source, retrieved_at=exit_at))
    result = evaluate(candidate, values, source=source)
    assert result["exit_open"] == 100
    assert result["gross"] == Fraction(-1, 11)
    assert result["exit_at"] == exit_at


def test_held_closed_minute_requires_retrieval_after_its_end_even_before_gap():
    candidate = prepare()
    source = source_for(candidate)
    source = replace(source, price_source=replace(source.price_source, retrieved_at=candidate.entry_at))
    values = {candidate.entry_at: path(candidate)[candidate.entry_at]}
    with pytest.raises(preparation.PreparationRefusal, match="retrieval_precedes"):
        evaluate(candidate, values, source=source)
    values[candidate.entry_at] = (Decimal("100"), None, object(), "future")
    assert evaluate(candidate, values, source=source)["reason"] == "invalidated_entry"


@pytest.mark.parametrize("close", [
    "107.0000000000000000000000000000000000000001",
    "107", "106.9999999999999999999999999999999999999999",
])
@pytest.mark.parametrize("with_raw_open", [False, True])
def test_decimal_held_validation_matches_all_fraction_reference_at_stop_boundary(close, with_raw_open):
    candidate = prepare()
    values = path(candidate)
    values[candidate.entry_at + MINUTE] = tuple(map(Decimal, ("110", "112", "106", close)))
    values[candidate.entry_at + 5 * MINUTE] = tuple(map(Decimal, ("109.12345678901234567890123456789", "112", "109", "110")))
    values[candidate.cap_at] = (Decimal("111.12345678901234567890123456789"), None, object(), "unread")
    reference = legacy_prices(values)
    results = []
    for precision in (2, 7, 28, 80):
        with localcontext() as context:
            context.prec = precision
            opening = {at: prices[0] for at, prices in values.items()} if with_raw_open else None
            legacy_opening = {at: prices[0] for at, prices in reference.items()} if with_raw_open else None
            actual = evaluate(candidate, values, opening_prices=opening)
            expected = evaluate(candidate, reference, opening_prices=legacy_opening)
            assert actual == expected
            results.append(actual)
    assert all(result == results[0] for result in results)


@pytest.mark.parametrize("index", range(4))
@pytest.mark.parametrize("invalid", [Decimal("0"), Decimal("-1"), Decimal("NaN"),
                                     Decimal("sNaN"), Decimal("Infinity"), None, "110"])
def test_decimal_held_invalid_value_errors_match_all_fraction_reference(index, invalid):
    candidate = prepare()
    values = path(candidate)
    modified = list(values[candidate.entry_at + MINUTE])
    modified[index] = invalid
    values[candidate.entry_at + MINUTE] = tuple(modified)
    with pytest.raises(preparation.PreparationRefusal) as actual:
        evaluate(candidate, values)
    with pytest.raises(preparation.PreparationRefusal) as expected:
        evaluate(candidate, legacy_prices(values))
    assert str(actual.value) == str(expected.value) == "invalid_exact_value"


@pytest.mark.parametrize("prices", [("110", "108", "109", "110"), ("110", "112", "111", "110")])
def test_decimal_held_ohlc_order_error_matches_all_fraction_reference(prices):
    candidate = prepare()
    values = path(candidate)
    values[candidate.entry_at + MINUTE] = tuple(map(Decimal, prices))
    for sample in (values, legacy_prices(values)):
        with pytest.raises(preparation.PreparationRefusal, match="invalid_minute_ohlc"):
            evaluate(candidate, sample)


def test_decimal_subclass_comparisons_are_normalized_before_ohlc_ordering():
    class CustomDecimal(Decimal):
        def __le__(self, other):
            raise AssertionError("custom ordering must not replace exact Fraction ordering")
        def __lt__(self, other):
            raise AssertionError("custom ordering must not replace exact Fraction ordering")
    candidate = prepare()
    values = path(candidate)
    expected = evaluate(candidate, values)
    values[candidate.entry_at + MINUTE] = tuple(CustomDecimal(value) for value in values[candidate.entry_at + MINUTE])
    assert evaluate(candidate, values) == expected


def test_delay_path_gap_stays_unknown_and_does_not_skip_to_available_fill():
    candidate = prepare()
    values = path(candidate)
    values[candidate.entry_at] = tuple(map(Decimal, ("110", "112", "106", "106")))
    del values[candidate.entry_at + 2 * MINUTE]
    assert evaluate(candidate, values)["reason"] == "required_path_missing"


@pytest.mark.parametrize("acknowledgement", [False, None, 1, "true"])
def test_explicit_authentication_required_before_reading_prices(acknowledgement):
    class Poison(dict):
        def get(self, key, default=None):
            raise AssertionError("unauthenticated prices were read")

    candidate = prepare()
    with pytest.raises(preparation.PreparationRefusal, match="authenticated_outcome_inputs_required"):
        evaluate(candidate, Poison(), inputs_authenticated=acknowledgement)


@pytest.mark.parametrize("field,value", [
    ("symbol", "KRX:000002"), ("policy", "D+2"), ("price_basis", "other"),
    ("input_sha256", "not-a-digest"), ("authorization_reference", " "),
])
def test_source_identity_and_boundary_cannot_be_omitted(field, value):
    candidate = prepare()
    with pytest.raises(preparation.PreparationRefusal):
        evaluate(candidate, path(candidate), source=replace(source_for(candidate), **{field: value}))


def test_synthetic_source_refused_without_relabelling_and_old_wrapper_remains_guarded():
    candidate = prepare()
    source = replace(source_for(candidate), price_source=candidate.target.price_source)
    with pytest.raises(preparation.PreparationRefusal, match="must_not_be_synthetic"):
        evaluate(candidate, path(candidate), source=source)
    with pytest.raises(preparation.PreparationRefusal, match="invented_fixtures_only"):
        preparation.synthetic_outcome(candidate, path(candidate), synthetic=False)


def test_unknown_source_finality_and_evidence_remain_explicit():
    candidate = prepare()
    source = source_for(candidate)
    source = replace(source, price_source=replace(source.price_source, evidence_level="unknown", is_final=None))
    result = evaluate(candidate, path(candidate), source=source)
    assert result["source"]["price_source"]["evidence_level"] == "unknown"
    assert result["source"]["price_source"]["is_final"] is None
    assert result["evidence_level"] == "assumed"


def test_changed_clock_refuses_with_exploratory_error_context():
    candidate = prepare()
    changed = replace(candidate, cap_at=candidate.cap_at + MINUTE)
    with pytest.raises(preparation.PreparationRefusal, match="exploratory_candidate_clock_changed"):
        evaluate(changed, path(candidate))


@pytest.mark.parametrize("stop_exit", [False, True])
def test_unique_raw_exit_open_survives_invalid_hlc_bucket(stop_exit):
    candidate = prepare()
    values = path(candidate)
    exit_at = candidate.cap_at
    if stop_exit:
        values[candidate.entry_at] = tuple(map(Decimal, ("110", "112", "106", "106")))
        exit_at = candidate.entry_at + 4 * MINUTE
    opens = {at: value[0] for at, value in values.items()}
    values[exit_at] = None  # CC rejected later HLC, but unique raw OPEN is valid.
    opens[exit_at] = Decimal("100")
    result = evaluate(candidate, values, opening_prices=opens)
    assert result["status"] == "known"
    assert result["exit_at"] == exit_at
    assert result["gross"] == Fraction(-1, 11)


def test_unique_raw_invalidated_entry_open_survives_invalid_hlc_bucket():
    candidate = prepare()
    result = evaluate(candidate, {candidate.entry_at: None},
                      opening_prices={candidate.entry_at: Decimal("100")})
    assert result["status"] == "declined"
    assert result["reason"] == "invalidated_entry"
    assert result["entry_open"] == 100


@pytest.mark.parametrize("offset", [0, 1, 59])
def test_raw_open_does_not_rescue_invalid_held_ohlc(offset):
    candidate = prepare()
    values = path(candidate)
    opens = {at: value[0] for at, value in values.items()}
    values[candidate.entry_at + offset * MINUTE] = None
    result = evaluate(candidate, values, opening_prices=opens)
    assert result["status"] == "unknown"
    assert result["reason"] == "required_path_missing"


@pytest.mark.parametrize("offset,reason", [(0, "entry_open_missing"), (1, "required_path_missing"), (60, "exit_open_missing")])
@pytest.mark.parametrize("missing", [False, True])
def test_duplicate_or_missing_raw_open_never_falls_back_to_ohlc(offset, reason, missing):
    candidate = prepare()
    values = path(candidate)
    opens = {at: value[0] for at, value in values.items()}
    at = candidate.entry_at + offset * MINUTE
    if missing:
        del opens[at]
    else:
        opens[at] = None  # Caller found duplicate/conflicting raw buckets.
    result = evaluate(candidate, values, opening_prices=opens)
    assert result["status"] == "unknown"
    assert result["reason"] == reason


@pytest.mark.parametrize("offset,reason", [(0, "entry_open_missing"), (1, "required_path_missing"), (60, "exit_open_missing")])
def test_disagreeing_open_maps_remain_unknown(offset, reason):
    candidate = prepare()
    values = path(candidate)
    opens = {at: value[0] for at, value in values.items()}
    opens[candidate.entry_at + offset * MINUTE] = Decimal("111")
    assert evaluate(candidate, values, opening_prices=opens)["reason"] == reason


def test_raw_open_retrieval_bounds_apply_even_when_ohlc_missing():
    candidate = prepare()
    source = source_for(candidate)
    source = replace(source, price_source=replace(source.price_source, retrieved_at=candidate.entry_at - MINUTE))
    with pytest.raises(preparation.PreparationRefusal, match="retrieval_precedes"):
        evaluate(candidate, {candidate.entry_at: None}, source=source,
                 opening_prices={candidate.entry_at: Decimal("100")})


def test_unchanged_explicit_open_map_matches_default_labels():
    candidate = prepare()
    values = path(candidate)
    opens = {at: value[0] for at, value in values.items()}
    assert evaluate(candidate, values, opening_prices=opens) == evaluate(candidate, values)
