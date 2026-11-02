"""Invented-only EE reference qualification; no cross-test-module fixtures."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from fractions import Fraction
import ast
from pathlib import Path

import pytest

from research.intraday_ed_inputs import Minute, SessionInputs
from research.intraday_trader_scenarios import PreviousTerminal, ProfitFact
from research.intraday_trader_session import evaluate_session
from research.intraday_trader_reference import (
    CLAIM, SCENARIOS, ReferenceRefusal, expected_session, verify_session_result,
)


M = timedelta(minutes=1)
OPEN = datetime(2026, 6, 3, 9, tzinfo=timezone(timedelta(hours=9)))
SYMBOL = "KRX:000001"
D = Decimal
META = b'{"fixture":"invented", "archive_authenticated":false}'


def row(index, values, **changes):
    at = OPEN + index * M
    return replace(Minute(SYMBOL, at, at + 3 * M,
                          tuple(map(D, map(str, values))), None, volume_state="missing"), **changes)


def session(blocks=(), *, filler=110):
    minutes = {OPEN + i * M: row(i, (filler, filler + 1, filler - 1, filler)) for i in range(300)}
    for b, values in enumerate(blocks):
        for offset in range(5):
            index = b * 5 + offset
            minutes[OPEN + index * M] = row(index, values)
    return SessionInputs(SYMBOL, OPEN, OPEN + 300 * M, minutes)


def a_blocks():
    return [(100, 105, 95, close) for close in (100, 100, 101, 101, 102, 103)] + [
        (108, 120, 107, 112), (112, 122, 111, 115), (115, 117, 108, 111),
        (111, 114, 109, 113), (113, 116, 110, 115),
    ]


def b_blocks():
    return [(105, 110, 100, 105)] * 6 + [
        (105, 112, 90, 95), (95, 98, 92, 97), (97, 99, 93, 98),
        (98, 101, 94, 100), (100, 103, 95, 102), (102, 106, 96, 105),
    ]


def c_blocks():
    return [(100, 105, 95, 100)] * 6 + [(106, 110, 104, 108)]


def facts(current="20", prior="-3"):
    return (
        ProfitFact(SYMBOL, "original-current", "invented-current", 2026, 1, D(current),
                   "KRW", "million", "consolidated", date(2026, 3, 31), OPEN - M, True),
        ProfitFact(SYMBOL, "original-prior", "invented-prior", 2025, 1, D(prior),
                   "KRW", "million", "consolidated", date(2025, 3, 31), OPEN - 2 * M, True),
    )


def check(inputs, scenarios=SCENARIOS, **kwargs):
    options = dict(scenarios=scenarios, source_metadata=META, **kwargs)
    actual = evaluate_session(inputs, **options)
    receipt = verify_session_result(actual, inputs, **options)
    assert receipt.scenarios == scenarios
    assert receipt.compared_policy_count == len(scenarios)
    assert receipt.claim == CLAIM
    return actual, expected_session(inputs, **options)


def mutate(actual, path, value):
    """Update an actual immutable result, never use it to calculate expected."""
    if not path:
        return value
    key, *rest = path
    if type(key) is int:
        values = list(actual)
        values[key] = mutate(values[key], rest, value)
        return tuple(values)
    return replace(actual, **{key: mutate(getattr(actual, key), rest, value)})


@pytest.mark.parametrize("blocks,filler,scenario,signal,stop,target", [
    (a_blocks, 110, "EE-A", 55, "108", "122"),
    (a_blocks, 110, "EE-A-WITHOUT-15M-CONTEXT", 55, "108", "122"),
    (b_blocks, 105, "EE-B", 60, "90", "110"),
    (b_blocks, 105, "EE-B-EARLY-RECLAIM", 55, "90", "110"),
    (c_blocks, 110, "EE-C", 35, "95", "134"),
    (c_blocks, 110, "EE-C-WITHOUT-PROFIT-CONTENT", 35, "95", "134"),
])
def test_all_six_independently_reconstructed_literal_levels(blocks, filler, scenario, signal, stop, target):
    _, expected = check(session(blocks(), filler=filler), (scenario,),
                        profit_facts=facts(), event_session_admitted=True)
    detection = expected["results"][0]["detection"]
    candidate = detection["candidate"]
    assert detection["state"] == "candidate"
    assert candidate["signal"]["end_at"] == OPEN + signal * M
    assert candidate["stop"] == D(stop)
    assert candidate["target"] == D(target)
    assert expected["results"][0]["path"]["entry_at"] == OPEN + (signal + 3) * M


def test_all_six_have_original_opaque_metadata_and_policy_order():
    actual, expected = check(session(a_blocks()), profit_facts=facts(), event_session_admitted=True)
    assert expected["source_metadata"] == META
    assert [result.scenario_id for result in actual.results] == list(SCENARIOS)


@pytest.mark.parametrize("value", [True, 1, 1.0, Fraction(1), D(0), D(-1),
    D("NaN"), D("sNaN"), D("Infinity"), D("-Infinity")])
def test_reference_bar_validation_retains_exact_decimal_price_boundary(value):
    inputs = session(a_blocks())
    inputs.minutes[OPEN] = replace(inputs.minutes[OPEN], ohlc=(value, D(105), D(95), D(100)))
    _, expected = check(inputs)
    for policy in expected["results"]:
        assert policy["detection"]["state"] == "invalid"
        assert policy["detection"]["reason"] == "positive_finite_decimal_price_required"
        assert policy["issues"] == (dict(minute=OPEN, reason="positive_finite_decimal_price_required"),)
        assert policy["path"] is None and policy["path_refusal"] is None


def test_reference_bar_validation_preserves_fractional_decimal_prices_in_small_context():
    inputs = session(a_blocks())
    inputs = replace(inputs, minutes={at: replace(minute,
        ohlc=tuple(value.scaleb(-3) for value in minute.ohlc)) for at, minute in inputs.minutes.items()})
    with localcontext() as context:
        context.prec = 2
        _, expected = check(inputs, ("EE-A",))
    candidate = expected["results"][0]["detection"]["candidate"]
    assert candidate["stop"] == D("0.108") and candidate["target"] == D("0.122")


@pytest.mark.parametrize("admitted", [True, False, None])
@pytest.mark.parametrize("pair", [None, (None, None)])
def test_missing_profit_is_unknown_on_common_support_including_control(admitted, pair):
    _, expected = check(session(c_blocks()), ("EE-C", "EE-C-WITHOUT-PROFIT-CONTENT"),
                        profit_facts=pair, event_session_admitted=admitted)
    for policy in expected["results"]:
        assert policy["detection"]["candidate"]["information"]["state"] == "unknown"
        assert policy["path"]["status"] == ("declined" if admitted is False else "unknown")
        assert policy["path"]["entry_at"] is None
        assert policy["path"]["gross"] is None


def test_c_sign_removal_retains_price_parent_and_changes_admission_only():
    _, expected = check(session(c_blocks()), ("EE-C", "EE-C-WITHOUT-PROFIT-CONTENT"),
                        profit_facts=facts(current="-1"), event_session_admitted=True)
    selected, control = expected["results"]
    assert selected["detection"]["candidate"]["parent"] == control["detection"]["candidate"]["parent"]
    assert selected["path"]["reason"] == "admission_decline"
    assert control["path"]["status"] == "traded"


@pytest.mark.parametrize("change,reason", [
    (dict(available_at=OPEN + 38 * M), "profit_future_availability"),
    (dict(available_at=OPEN + M), "profit_unavailable_at_opening"),
    (dict(unit="won"), "incomparable_profit_basis"),
    (dict(is_original=False), "invalid_profit_identity_or_provenance"),
    (dict(fiscal_year=2020), "implausible_profit_fiscal_year"),
    (dict(receipt_id="original-prior"), "profit_originals_not_independently_identified"),
])
def test_invalid_or_future_profit_is_explicit_refusal_for_both_arms(change, reason):
    pair = (replace(facts()[0], **change), facts()[1])
    _, expected = check(session(c_blocks()), ("EE-C", "EE-C-WITHOUT-PROFIT-CONTENT"),
                        profit_facts=pair, event_session_admitted=True)
    for policy in expected["results"]:
        assert policy["detection"]["state"] == "invalid"
        assert policy["detection"]["reason"] == reason
        assert policy["path"] is None


@pytest.mark.parametrize("state", ["missing", "no_trade", "halt", "transport_error", "conflict", "unit_unknown"])
def test_nonobserved_price_preserves_precise_unknown_issue(state):
    inputs = session(a_blocks())
    at = OPEN + 18 * M
    inputs.minutes[at] = replace(inputs.minutes[at], price_state=state, ohlc=None)
    _, expected = check(inputs)
    for policy in expected["results"]:
        assert policy["detection"]["state"] == "unknown"
        assert policy["issues"] == (dict(minute=at, reason=f"price_{state}"),)


@pytest.mark.parametrize("change,reason", [
    (dict(symbol="KRX:999999"), "minute_identity_changed"),
    (dict(start_at=OPEN + 19 * M), "minute_identity_changed"),
    (dict(available_at=OPEN + 17 * M), "minute_availability_precedes_completion"),
    (dict(price_state="invented"), "invalid_price_state"),
    (dict(price_state="halt"), "unknown_price_has_payload"),
    (dict(ohlc=None), "observed_price_requires_ohlc"),
    (dict(ohlc=(D(1), D(2), D(0), D(1))), "positive_finite_decimal_price_required"),
    (dict(ohlc=(D(2), D(1), D(1), D(2))), "invalid_minute_ohlc_order"),
])
def test_invalid_prices_cannot_become_zero_result_or_gap(change, reason):
    inputs = session(a_blocks())
    at = OPEN + 18 * M
    inputs.minutes[at] = replace(inputs.minutes[at], **change)
    _, expected = check(inputs)
    for policy in expected["results"]:
        assert policy["detection"]["state"] == "invalid"
        assert policy["detection"]["reason"] == reason
        assert policy["issues"] == (dict(minute=at, reason=reason),)
        assert policy["path"] is None


def test_future_publication_is_unknown_without_inspecting_future_bad_payload():
    inputs = session(a_blocks())
    at = OPEN + 18 * M
    inputs.minutes[at] = replace(inputs.minutes[at], available_at=OPEN + 23 * M,
                                 price_state="future_invalid", ohlc=None)
    _, expected = check(inputs)
    assert expected["results"][0]["issues"] == (
        dict(minute=at, reason="price_unavailable_by_bar_deadline"),)
    assert expected["results"][0]["detection"]["state"] == "unknown"


def test_missing_minute_and_later_gap_cannot_erase_completed_event():
    inputs = session(c_blocks())
    del inputs.minutes[OPEN + 80 * M]
    _, expected = check(inputs, ("EE-C",), profit_facts=facts(), event_session_admitted=True)
    assert expected["results"][0]["detection"]["state"] == "candidate"
    assert expected["results"][0]["issues"] == ()
    assert expected["results"][0]["path"]["status"] == "unknown"
    del inputs.minutes[OPEN + 18 * M]
    _, expected = check(inputs, ("EE-C",))
    assert expected["results"][0]["issues"] == (dict(minute=OPEN + 18 * M, reason="missing_minute"),)


def test_known_absence_requires_complete_prefix_to_cutoff():
    inputs = session([(100, 105, 95, 100)] * 48, filler=100)
    _, expected = check(inputs)
    assert all(p["detection"]["reason"] == "decision_cutoff" for p in expected["results"])
    assert all(p["detection"]["decision_at"] == OPEN + 240 * M for p in expected["results"])
    del inputs.minutes[OPEN + 230 * M]
    _, expected = check(inputs)
    assert all(p["detection"]["state"] == "unknown" for p in expected["results"])


def test_a_parent_frozen_context_decline_is_ignored_only_by_control():
    blocks = a_blocks()
    blocks[2] = (100, 105, 95, 104)
    blocks[5] = (100, 105, 95, 103)
    _, expected = check(session(blocks), ("EE-A", "EE-A-WITHOUT-15M-CONTEXT"))
    selected, control = expected["results"]
    assert selected["detection"]["candidate"]["context_closes"] == (D(104), D(103))
    assert selected["path"]["reason"] == "admission_decline"
    assert control["path"]["status"] == "traded"


@pytest.mark.parametrize("family", ["EE-A", "EE-B"])
def test_one_explicit_reentry_retains_original_parent_and_new_attempt(family):
    blocks = a_blocks() if family == "EE-A" else b_blocks()
    # The earlier attempt is supplied evidence. The comparison close can be
    # before terminal; a fresh new crossing completion must be strictly later.
    prior = PreviousTerminal(family, SYMBOL, OPEN, 1, OPEN + 35 * M,
                             OPEN + 37 * M, "closed", D(111) if family == "EE-A" else None)
    if family == "EE-A":
        blocks[6] = (108, 111, 107, 110)
    inputs = session(blocks, filler=110 if family == "EE-A" else 105)
    # B parent at 35 predates terminal, so make a second downward crossing.
    if family == "EE-B":
        blocks = b_blocks()[:6] + [(105, 112, 90, 95), (100, 104, 95, 101)] + b_blocks()[6:]
        inputs = session(blocks, filler=105)
    _, expected = check(inputs, (family,), previous={family: prior})
    detection = expected["results"][0]["detection"]
    assert detection["attempt_number"] == 2
    assert detection["original_parent_end_at"] == OPEN + 35 * M
    assert detection["parent"]["end_at"] > prior.terminal_at
    assert detection["candidate"] is not None


def test_path_exact_target_delayed_open_cost_labels_and_ambiguity():
    inputs = session(c_blocks())
    # C signal end35, available37, entry38; q40 matures43, exits44.
    inputs.minutes[OPEN + 40 * M] = row(40, (110, 140, 90, 135))
    inputs.minutes[OPEN + 44 * M] = row(44, (137, 138, 136, 137))
    _, expected = check(inputs, ("EE-C",), profit_facts=facts(), event_session_admitted=True)
    path = expected["results"][0]["path"]
    assert path["entry_at"] == OPEN + 38 * M
    assert path["exit_at"] == OPEN + 44 * M
    assert path["trigger_at"] == OPEN + 43 * M
    assert path["reason"] == "known_target_close_reached"
    assert path["simultaneous_touch_minutes"] == (OPEN + 40 * M,)
    assert path["gross"] == Fraction(27, 110)
    assert path["net33"] == Fraction(27, 110) - Fraction(33, 10000)
    assert path["net43"] == Fraction(27, 110) - Fraction(43, 10000)


@pytest.mark.parametrize("kind", ["stop", "response", "cap", "missing", "refusal"])
def test_independent_held_path_stop_response_cap_and_refusal(kind):
    inputs = session(c_blocks())
    if kind == "stop":
        inputs.minutes[OPEN + 40 * M] = row(40, (110, 111, 90, 94))
    elif kind == "response":
        for index in range(38, 48):
            inputs.minutes[OPEN + index * M] = row(index, (110, 111, 108, 109))
    elif kind == "missing":
        del inputs.minutes[OPEN + 40 * M]
    elif kind == "refusal":
        inputs.minutes[OPEN + 40 * M] = row(40, (110, 111, 108, 109), available_at=OPEN + 44 * M)
    else:
        # Closes whose first exit could only equal cap are not consumed.
        for index in range(94, 98):
            del inputs.minutes[OPEN + index * M]
    _, expected = check(inputs, ("EE-C",), profit_facts=facts(), event_session_admitted=True)
    policy = expected["results"][0]
    if kind == "refusal":
        assert policy["path"] is None
        assert policy["path_refusal"] == "held_minute_unavailable_at_decision"
    else:
        assert policy["path"]["reason"] == dict(stop="structural_close_invalidation",
                                                  response="response_failure", cap="time_cap",
                                                  missing="missing_held_minute")[kind]


@pytest.mark.parametrize("path,value", [
    (("symbol",), "KRX:999999"),
    (("source_metadata",), b'{"fixture":"changed"}'),
    (("results", 0, "scenario_id"), "EE-A"),
    (("results", 0, "detection", "state"), "known_no_candidate"),
    (("results", 0, "detection", "reason"), "changed"),
    (("results", 0, "detection", "decision_at"), OPEN + 36 * M),
    (("results", 0, "detection", "observed_through"), OPEN + 30 * M),
    (("results", 0, "detection", "attempt_number"), True),
    (("results", 0, "detection", "original_parent_end_at"), OPEN + 40 * M),
    (("results", 0, "detection", "trace"), ()),
    (("results", 0, "detection", "candidate", "target"), D(135)),
    (("results", 0, "detection", "candidate", "stop"), D(96)),
    (("results", 0, "detection", "candidate", "key_level"), D(106)),
    (("results", 0, "detection", "candidate", "event_session_admitted"), 1),
    (("results", 0, "detection", "candidate", "information", "applied"), False),
    (("results", 0, "detection", "candidate", "signal", "available_at"), OPEN + 36 * M),
    (("results", 0, "path", "plan", "family"), "EE-A"),
    (("results", 0, "path", "plan", "admission"), "unknown"),
    (("results", 0, "path", "plan", "target"), D(135)),
    (("results", 0, "path", "status"), "declined"),
    (("results", 0, "path", "entry_at"), OPEN + 37 * M),
    (("results", 0, "path", "entry_open"), 110.0),
    (("results", 0, "path", "exit_open"), D(111)),
    (("results", 0, "path", "exit_at"), OPEN + 97 * M),
    (("results", 0, "path", "gross"), Fraction(1, 100)),
    (("results", 0, "path", "net33"), Fraction(-32, 10000)),
    (("results", 0, "path", "net43"), Fraction(-42, 10000)),
    (("results", 0, "path", "trigger_at"), OPEN + 50 * M),
    (("results", 0, "path", "simultaneous_touch_minutes"), (OPEN + 45 * M,)),
])
def test_mutated_actual_scientific_fields_are_rejected(path, value):
    inputs = session(c_blocks())
    options = dict(scenarios=("EE-C",), profit_facts=facts(), event_session_admitted=True, source_metadata=META)
    actual = evaluate_session(inputs, **options)
    changed = mutate(actual, path, value)
    with pytest.raises(ReferenceRefusal):
        verify_session_result(changed, inputs, **options)


@pytest.mark.parametrize("change", ["omit", "duplicate", "extra", "reorder", "list"])
def test_policy_cardinality_and_shapes_cannot_evade_verification(change):
    inputs = session(c_blocks())
    actual = evaluate_session(inputs)
    results = actual.results
    changed = dict(omit=results[:-1], duplicate=results + (results[0],), extra=results + (results[-1],),
                   reorder=results[::-1], list=list(results))[change]
    with pytest.raises(ReferenceRefusal):
        verify_session_result(replace(actual, results=changed), inputs)


def test_unknown_support_and_issue_mutation_are_rejected():
    inputs = session(c_blocks())
    del inputs.minutes[OPEN + 18 * M]
    actual = evaluate_session(inputs, scenarios=("EE-C",))
    changed = mutate(actual, ("results", 0, "issues", 0, "reason"), "price_halt")
    with pytest.raises(ReferenceRefusal):
        verify_session_result(changed, inputs, scenarios=("EE-C",))


def test_reference_ignores_producer_algorithms_and_uses_original_minutes(monkeypatch):
    inputs = session(c_blocks())
    options = dict(scenarios=("EE-C",), profit_facts=facts(), event_session_admitted=True)
    actual = evaluate_session(inputs, **options)
    def forbidden(*args, **kwargs):
        raise AssertionError("production algorithm invoked by reference")
    monkeypatch.setattr("research.intraday_trader_scenarios.detect_bars", forbidden)
    monkeypatch.setattr("research.intraday_trader_paths.evaluate_path", forbidden)
    monkeypatch.setattr("research.intraday_trader_session._project_block", forbidden)
    monkeypatch.setattr("research.intraday_trader_session._bars", forbidden)
    assert verify_session_result(actual, inputs, **options).compared_policy_count == 1
    changed_inputs = replace(inputs, minutes=dict(inputs.minutes))
    changed_inputs.minutes[OPEN + 30 * M] = row(30, (106, 120, 104, 119))
    with pytest.raises(ReferenceRefusal):
        verify_session_result(actual, changed_inputs, **options)


def test_reference_imports_only_trusted_input_types_from_producer_modules():
    import research.intraday_trader_reference as reference
    source = ast.parse(Path(reference.__file__).read_text())
    imports = [(node.module, tuple(alias.name for alias in node.names))
               for node in ast.walk(source) if isinstance(node, ast.ImportFrom)]
    assert [(module, names) for module, names in imports if module and "intraday_trader" in module] == [
        ("research.intraday_trader_scenarios", ("PreviousTerminal", "ProfitFact"))]


def test_decimal_context_and_quantum_do_not_change_exact_math_claim():
    inputs = session(c_blocks())
    with localcontext() as context:
        context.prec = 2
        actual, expected = check(inputs, ("EE-C",), profit_facts=facts(), event_session_admitted=True)
    assert expected["results"][0]["detection"]["candidate"]["target"] == D(134)
    altered = mutate(actual, ("results", 0, "detection", "candidate", "target"), D("134.00"))
    verify_session_result(altered, inputs, scenarios=("EE-C",), profit_facts=facts(),
                          event_session_admitted=True, source_metadata=META)


@pytest.mark.parametrize("kind,reason", [
    ("key", "key_level_lost"), ("low", "pullback_low_lost"),
    ("timeout", "confirmation_timeout"),
])
def test_a_irreversible_cancellation_and_deadline_are_reconstructed(kind, reason):
    blocks = a_blocks()
    if kind == "key":
        blocks[9] = (106, 114, 104, 105)
    elif kind == "low":
        blocks[9] = (111, 114, 107, 113)
    else:
        blocks = blocks[:10] + [(113, 114, 109, 113)] * 5
    _, expected = check(session(blocks), ("EE-A", "EE-A-WITHOUT-15M-CONTEXT"))
    for policy in expected["results"]:
        assert policy["detection"]["state"] == "known_no_candidate"
        assert policy["detection"]["reason"] == reason
        assert policy["path"] is None


@pytest.mark.parametrize("kind,reason", [("low", "parent_low_lost"), ("timeout", "confirmation_timeout")])
def test_b_fixed_base_cancellation_and_expiry(kind, reason):
    blocks = b_blocks()
    if kind == "low":
        blocks[7] = (95, 98, 89, 97)
    else:
        blocks = blocks[:11] + [(100, 104, 95, 102)] * 3
    _, expected = check(session(blocks, filler=105), ("EE-B",))
    assert expected["results"][0]["detection"]["reason"] == reason


def test_a_parent_ending_on_15minute_boundary_includes_its_close():
    blocks = a_blocks()
    # Delay the parent from end35 to end45. The completed [30,45] block
    # contributes its close, while no later confirmation close may enter.
    blocks[6:9] = [(103, 105, 100, 104), (104, 105, 100, 104), (108, 122, 107, 115)]
    blocks[9:] = [(115, 117, 108, 111), (111, 114, 109, 113), (113, 116, 110, 115)]
    _, expected = check(session(blocks), ("EE-A",))
    assert expected["results"][0]["detection"]["candidate"]["context_closes"] == (D(103), D(115))


@pytest.mark.parametrize("kind,reason", [("outside", "entry_outside_frozen_levels"),
                                         ("cost", "entry_cost_space_not_strict"),
                                         ("missing", "missing_open_minute")])
def test_entry_open_gates_are_reconstructed_without_signal_close_fill(kind, reason):
    inputs = session(c_blocks())
    if kind == "missing":
        del inputs.minutes[OPEN + 38 * M]
    else:
        value = D(134) if kind == "outside" else D("133.6")
        inputs.minutes[OPEN + 38 * M] = row(38, (value, value, value, value))
    _, expected = check(inputs, ("EE-C",), profit_facts=facts(), event_session_admitted=True)
    path = expected["results"][0]["path"]
    assert path["reason"] == reason
    assert path["gross"] is None


def test_unknown_information_never_requests_outcome_suffix():
    class PrefixOnly(dict):
        def get(self, at, default=None):
            assert at < OPEN + 35 * M, "outcome price consumed despite unknown admission"
            return super().get(at, default)
    inputs = session(c_blocks())
    inputs = replace(inputs, minutes=PrefixOnly({at: item for at, item in inputs.minutes.items()
                                               if at < OPEN + 35 * M}))
    _, expected = check(inputs, ("EE-C", "EE-C-WITHOUT-PROFIT-CONTENT"), event_session_admitted=True)
    assert all(policy["path"]["reason"] == "admission_unknown" for policy in expected["results"])


@pytest.mark.parametrize("key", ["parent", "signal", "information"])
def test_equivalent_loose_nested_dict_cannot_replace_a_typed_record(key):
    inputs = session(c_blocks())
    options = dict(scenarios=("EE-C",), profit_facts=facts(), event_session_admitted=True)
    actual = evaluate_session(inputs, **options)
    equivalent = expected_session(inputs, **options)["results"][0]["detection"]["candidate"][key]
    changed = mutate(actual, ("results", 0, "detection", "candidate", key), equivalent)
    with pytest.raises(ReferenceRefusal, match="typed_nested_result_required"):
        verify_session_result(changed, inputs, **options)


def test_reentry_identity_mutation_cannot_relabel_first_attempt():
    prior = PreviousTerminal("EE-A", SYMBOL, OPEN, 1, OPEN + 35 * M,
                             OPEN + 37 * M, "cancelled", D(111))
    blocks = a_blocks()
    blocks[6] = (108, 111, 107, 110)
    inputs = session(blocks)
    options = dict(scenarios=("EE-A",), previous={"EE-A": prior})
    actual = evaluate_session(inputs, **options)
    for path, value in [(("attempt_number",), 1), (("original_parent_end_at",), OPEN + 40 * M),
                        (("candidate", "previous_terminal", "kind"), "closed")]:
        changed = mutate(actual, ("results", 0, "detection", *path), value)
        with pytest.raises(ReferenceRefusal):
            verify_session_result(changed, inputs, **options)


@pytest.mark.parametrize("path,value", [
    (("detection", "candidate", "information", "state"), "pass"),
    (("detection", "candidate", "information", "reason"), "profit_turnaround"),
    (("detection", "candidate", "event_session_admitted"), False),
    (("path", "status"), "traded"),
    (("path", "gross"), Fraction(0)),
    (("path", "plan", "admission"), "pass"),
    (("path", "entry_at"), OPEN + 38 * M),
])
def test_unknown_information_cannot_be_relabelled_as_no_event_or_financial_zero(path, value):
    inputs = session(c_blocks())
    options = dict(scenarios=("EE-C-WITHOUT-PROFIT-CONTENT",), event_session_admitted=True)
    actual = evaluate_session(inputs, **options)
    with pytest.raises(ReferenceRefusal):
        verify_session_result(mutate(actual, ("results", 0, *path), value), inputs, **options)


def test_unknown_malformed_and_no_candidate_have_no_interchangeable_financial_result():
    inputs = session(a_blocks())
    at = OPEN + 18 * M
    del inputs.minutes[at]
    options = dict(scenarios=("EE-A",))
    actual = evaluate_session(inputs, **options)
    for path, value in [(("detection", "state"), "invalid"),
                        (("detection", "state"), "known_no_candidate"),
                        (("path_refusal",), "invented")]:
        with pytest.raises(ReferenceRefusal):
            verify_session_result(mutate(actual, ("results", 0, *path), value), inputs, **options)


def test_registered_previous_evidence_outside_subset_remains_unconsumed():
    inputs = session(c_blocks())
    options = dict(scenarios=("EE-C",), previous={"EE-A": object()})
    check(inputs, **options)
    with pytest.raises(ReferenceRefusal, match="invalid_previous_scenario"):
        expected_session(inputs, scenarios=("EE-C",), previous={"typo": None})
