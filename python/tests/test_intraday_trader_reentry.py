"""Only invented raw/typed minutes; no existing reports, databases or network."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from research import intraday_trader_reentry as producer
from research import intraday_trader_reentry_reference as reference

from research import intraday_ed_sources as sources
from research import intraday_trader_reference as old_reference
from research import intraday_trader_followups as followups
from research import intraday_trader_session as primary


DAY, SYMBOL = "2026-06-03", "KRX:000001"
OPEN = sources.calendar_source.session_open(DAY)
M = timedelta(minutes=1)


def sessions(blocks, *, overrides=None, missing=(), filler=(126, 127, 125, 126)):
    rows = []
    for index in range(390):
        if index in missing:
            continue
        ohlc = list(blocks[index // 5] if index // 5 < len(blocks) else filler)
        if overrides and index in overrides:
            ohlc = overrides[index]
        rows.append(dict(symbol=SYMBOL, interval="1m", open_time_ms=int((OPEN + index * M).timestamp()) * 1000,
            fetched_at=None, **dict(zip(("open", "high", "low", "close"), map(str, ohlc), strict=True))))
    raw = dict(symbol=SYMBOL, session=DAY, observation_date=DAY, available_at=None,
        retrieved_at="2026-10-30T00:00:00Z", source="collector klines", is_final=None,
        evidence_level="assumed", rows=rows)
    view = sources.session_inputs(SYMBOL, DAY, deepcopy(raw), None)
    independent = sources.full_reference_session_inputs(SYMBOL, DAY, deepcopy(raw), None)
    return view.session, independent


def a_blocks():
    return [(100, 105, 95, close) for close in (100, 100, 101, 101, 102, 103)] + [
        (108, 120, 107, 112), (112, 122, 111, 115), (115, 117, 108, 111),
        (111, 114, 109, 113), (113, 116, 110, 115), (115, 116, 114, 115),
        (123, 131, 114, 123), (123, 126, 121, 125), (125, 126, 120, 122),
        (122, 124, 121, 123), (123, 127, 122, 126)]


def a_inputs(**changes):
    return sessions(a_blocks(), overrides={60: (115, 131, 114, 130), 64: (130, 131, 121, 123),
        90: (126, 127, 118, 119), 94: (118, 119, 117, 118)}, **changes)


def b_blocks():
    return [(105, 110, 100, 105)] * 6 + [(105, 112, 90, 95), (95, 98, 92, 97),
        (97, 99, 93, 98), (98, 101, 94, 100), (100, 103, 95, 102), (102, 106, 96, 105),
        (105, 106, 104, 105), (105, 113, 104, 112), (105, 106, 95, 98),
        (98, 99, 96, 98), (98, 100, 97, 99), (99, 102, 98, 101),
        (101, 104, 99, 103), (103, 107, 100, 106)]


def b_inputs(**changes):
    return sessions(b_blocks(), overrides={65: (105, 112, 104, 111), 69: (112, 113, 111, 112),
        105: (106, 107, 93, 94), 109: (94, 95, 93, 94)}, filler=(106, 107, 105, 106), **changes)


def evaluate(pair):
    current, independent = pair
    return producer.evaluate_session(current, reference_session=independent)


def family(result, name):
    return next(item for item in result.families if item.family == name)


def test_a_reset_uses_original_impulse_high_not_risk_profit_target():
    result = family(evaluate(a_inputs()), "EE-A")
    assert result.first.detection.candidate.target == Decimal(129)
    assert result.reset.previous.frozen_target == Decimal(122)
    assert result.reset.previous.terminal_at == OPEN + 64 * M
    assert result.second.detection.candidate.parent.end_at == OPEN + 65 * M
    assert result.second.detection.candidate.attempt_number == 2
    assert result.second.path.net33 < 0


def test_missing_exit_is_not_closed_even_when_later_crossing_is_complete():
    result = family(evaluate(b_inputs(missing=(69,))), "EE-B")
    assert result.first.path.status == "unknown"
    assert result.first.path.entry_open is not None and result.first.path.exit_at == OPEN + 69 * M
    assert result.reset.state == "unknown" and result.reset.previous is None
    assert result.second is None


def test_first_a2r_and_b_base_are_unchanged_in_every_declared_field():
    current, independent = a_inputs()
    actual = producer.evaluate_session(current, reference_session=independent)
    expected, _ = followups.evaluate_session(deepcopy(current), peers=())
    assert family(actual, "EE-A").first == expected.results[0]
    current, independent = b_inputs()
    actual = producer.evaluate_session(current, reference_session=independent)
    expected = primary.evaluate_session(deepcopy(current), scenarios=("EE-B",))
    assert family(actual, "EE-B").first == expected.results[0]


def test_b_base_closed_then_fresh_downside_has_one_additional_losing_path():
    result = family(evaluate(b_inputs()), "EE-B")
    assert result.reset.state == "supported" and result.reset.previous.kind == "closed"
    assert result.reset.previous.terminal_at == OPEN + 69 * M
    assert result.second.detection.candidate.parent.end_at == OPEN + 75 * M
    assert result.second.detection.candidate.original_parent_end_at == OPEN + 35 * M
    assert result.second.detection.candidate.attempt_number == 2
    assert result.second.path.status == "traded" and result.second.path.net33 < 0
    assert result.second.detection.candidate.previous_terminal == result.reset.previous


def test_a_structural_cancel_with_frozen_impulse_can_reset_without_a_first_trade():
    blocks = a_blocks()
    blocks[10] = (113, 116, 107, 115)
    result = family(evaluate(sessions(blocks)), "EE-A")
    assert result.first.detection.reason == "pullback_low_lost" and result.first.path is None
    assert result.reset.previous.kind == "cancelled"
    assert result.reset.previous.terminal_at == OPEN + 57 * M
    assert result.second.detection.parent.end_at == OPEN + 65 * M


def test_a_pre_pullback_cancel_has_no_invented_reset_price():
    blocks = a_blocks()
    blocks[7] = (112, 122, 103, 104)
    result = family(evaluate(sessions(blocks)), "EE-A")
    assert result.first.detection.reason == "key_level_lost"
    assert result.reset.state == "unsupported" and result.reset.reason == "reset_level_unavailable"
    assert result.reset.impulse_high is None and result.reset.previous is None and result.second is None


def test_b_structural_cancel_requires_new_break_and_new_four_bar_base():
    blocks = b_blocks()[:12] + [(105, 106, 94, 98), (98, 99, 95, 98),
        (98, 100, 96, 99), (99, 102, 97, 101), (101, 104, 98, 103), (103, 107, 99, 106)]
    blocks[8] = (97, 99, 89, 98)
    result = family(evaluate(sessions(blocks, filler=(106, 107, 105, 106))), "EE-B")
    assert result.first.detection.reason == "parent_low_lost"
    assert result.reset.previous.kind == "cancelled" and result.reset.previous.terminal_at == OPEN + 47 * M
    assert result.second.detection.candidate.parent.end_at == OPEN + 65 * M
    base = [item for item in result.second.detection.trace if item.kind == "base_bar"]
    assert len(base) == 4 and base[0].bar_end_at == OPEN + 70 * M


@pytest.mark.parametrize("which", ("entry", "held", "exit"))
def test_missing_first_price_stage_remains_unknown_and_has_no_second(which):
    minute = {"entry": 63, "held": 64, "exit": 69}[which]
    result = family(evaluate(b_inputs(missing=(minute,))), "EE-B")
    assert result.first.path.status == "unknown" and result.reset.state == "unknown"
    assert result.reset.previous is None and result.second is None


def test_held_clock_refusal_does_not_become_cancelled_or_zero():
    current, independent = b_inputs()
    for session in (current, independent):
        session.minutes[OPEN + 64 * M] = replace(session.minutes[OPEN + 64 * M], available_at=OPEN + 68 * M)
    result = family(evaluate((current, independent)), "EE-B")
    assert result.first.path is None and result.first.path_refusal == "held_minute_unavailable_at_decision"
    assert result.reset.state == "refusal" and result.second is None


@pytest.mark.parametrize("cause", ("outside", "cost_space", "cap"))
def test_known_no_entry_is_model_eligibility_cancellation_at_first_known_clock(cause):
    current, independent = b_inputs()
    if cause == "cap":
        current, independent = (replace(item, closing=OPEN + 120 * M) for item in (current, independent))
    else:
        op = Decimal("111") if cause == "outside" else Decimal("109.9")
        for session in (current, independent):
            session.minutes[OPEN + 63 * M] = replace(session.minutes[OPEN + 63 * M],
                ohlc=(op, Decimal(112), Decimal(109), Decimal(110)))
    result = family(evaluate((current, independent)), "EE-B")
    assert result.first.path.status == "declined"
    assert result.reset.state == "supported" and result.reset.previous.kind == "cancelled"
    expected_at = OPEN + (62 if cause == "cap" else 63) * M
    assert result.reset.previous.terminal_at == result.reset.determined_at == expected_at
    assert result.reset.terminal_reason == result.first.path.reason
    assert result.reset.reason == "model_eligibility_cancelled"
    assert result.first.path.exit_at is None and result.first.path.gross is None
    assert result.second is not None


@pytest.mark.parametrize("which", ("EE-A", "EE-B"))
def test_expiration_is_model_eligibility_cancellation_only_when_expired_is_known(which):
    blocks = a_blocks()[:10] if which == "EE-A" else b_blocks()[:11]
    filler = (113, 114, 109, 113) if which == "EE-A" else (102, 103, 95, 102)
    result = family(evaluate(sessions(blocks, filler=filler)), which)
    assert result.first.detection.reason == "confirmation_timeout"
    expired = result.first.detection.trace[-1]
    assert expired.kind == "expired"
    assert result.reset.state == "supported" and result.reset.previous.kind == "cancelled"
    assert result.reset.determined_at == expired.available_at == result.first.detection.decision_at
    assert result.reset.previous.terminal_at > expired.bar_end_at
    assert result.reset.terminal_reason == "confirmation_timeout" and result.second is not None


@pytest.mark.parametrize("terminal,accepted", [(64, True), (65, False), (66, False)])
def test_crossing_completion_and_availability_must_both_follow_actual_modeled_exit(terminal, accepted):
    blocks = a_blocks()[:13] + [(123, 124, 121, 123)] * 40
    overrides = {terminal - 4: (115, 131, 114, 130), terminal: (130, 131, 121, 123),
                 64: (123, 131, 114, 123)}
    if terminal == 64:
        overrides[64] = (130, 131, 121, 123)
    result = family(evaluate(sessions(blocks, overrides=overrides, filler=(123, 124, 121, 123))), "EE-A")
    assert result.reset.previous.terminal_at == OPEN + terminal * M
    # F65 has D67. A bar starting60 is allowed after exit64, while completion
    # equal65 and completion65<exit66 must both be rejected despite D67>exit.
    if accepted:
        assert result.second.detection.parent.end_at == OPEN + 65 * M
    else:
        assert result.second.detection.parent is None


def test_gap_after_known_first_closure_keeps_first_and_unknown_second():
    result = family(evaluate(a_inputs(missing=(72,))), "EE-A")
    assert result.first.path.status == "traded" and result.reset.state == "supported"
    assert result.second.detection.state == "unknown" and result.second.path is None


def test_complete_no_first_opportunity_is_a_retained_null_second():
    result = evaluate(sessions([(100, 105, 95, 103)] * 78, filler=(100, 105, 95, 103)))
    for item in result.families:
        assert item.first.detection.state == "known_no_candidate"
        assert item.reset.state == "unsupported" and item.reset.reason == "no_registered_terminal"
        assert item.second is None


def test_independent_reference_reconstructs_both_legs_without_producer_helpers(monkeypatch):
    current, independent = a_inputs()
    result = producer.evaluate_session(current, reference_session=independent)
    def forbidden(*args, **kwargs):
        raise AssertionError("producer used as reference oracle")
    monkeypatch.setattr(producer, "_attempt", forbidden)
    monkeypatch.setattr(producer, "_derive_reset", forbidden)
    monkeypatch.setattr(producer.scenarios, "detect_bars", forbidden)
    monkeypatch.setattr(producer.paths, "evaluate_path", forbidden)
    assert reference.verify_session_result(result, independent).verified
    first = result.families[0]
    forged = replace(first, reset=replace(first.reset,
        previous=replace(first.reset.previous, frozen_target=first.first.detection.candidate.target)))
    with pytest.raises(old_reference.ReferenceRefusal, match="mathematical_mismatch"):
        reference.verify_session_result(replace(result, families=(forged, result.families[1])), independent)


def test_first_must_verify_before_any_terminal_is_derived(monkeypatch):
    real = producer._attempt
    def corrupt(session, which, previous=None):
        result = real(session, which, previous)
        if which == "EE-A" and previous is None:
            return replace(result, detection=replace(result.detection,
                candidate=replace(result.detection.candidate, target=Decimal(999))))
        return result
    monkeypatch.setattr(producer, "_attempt", corrupt)
    def forbidden(*args, **kwargs):
        raise AssertionError("terminal derived before first verification")
    monkeypatch.setattr(producer, "_derive_reset", forbidden)
    with pytest.raises(old_reference.ReferenceRefusal, match="mathematical_mismatch"):
        evaluate(a_inputs())


def test_second_positive_path_is_a_separate_label_not_an_account_return():
    pair = a_inputs()
    for session in pair:
        session.minutes[OPEN + 90 * M] = replace(session.minutes[OPEN + 90 * M],
            ohlc=tuple(map(Decimal, (126, 140, 125, 139))))
        session.minutes[OPEN + 94 * M] = replace(session.minutes[OPEN + 94 * M],
            ohlc=tuple(map(Decimal, (140, 141, 139, 140))))
    result = family(evaluate(pair), "EE-A")
    assert result.first.path.status == result.second.path.status == "traded"
    assert result.second.path.reason == "known_target_close_reached" and result.second.path.net33 > 0
    assert result.second.path.plan.target == Decimal(138)


def test_third_fresh_setup_does_not_create_a_third_call(monkeypatch):
    blocks = b_blocks() + [(106, 107, 105, 106)] * 4 + [(106, 107, 95, 98),
        (98, 99, 96, 98), (98, 100, 97, 99), (99, 102, 98, 101),
        (101, 104, 99, 103), (103, 107, 100, 106)]
    pair = sessions(blocks, overrides={65: (105, 112, 104, 111), 69: (112, 113, 111, 112),
        105: (106, 107, 93, 94), 109: (94, 95, 93, 94)}, filler=(106, 107, 105, 106))
    calls, real = [], producer._attempt
    def counted(session, which, previous=None):
        calls.append((which, previous))
        return real(session, which, previous)
    monkeypatch.setattr(producer, "_attempt", counted)
    result = family(evaluate(pair), "EE-B")
    assert result.second.path.exit_at == OPEN + 109 * M
    assert [which for which, previous in calls].count("EE-B") == 2
    assert sum(which == "EE-B" and previous is not None for which, previous in calls) == 1


def test_late_first_exit_does_not_shift_the_original_decision_cutoff():
    blocks = [(105, 110, 100, 105)] * 41 + [(105, 112, 90, 95),
        (95, 98, 92, 97), (97, 99, 93, 98), (98, 101, 94, 100),
        (100, 103, 95, 102), (102, 106, 96, 105), (105, 106, 104, 105),
        (105, 106, 104, 105), (105, 106, 95, 98)]
    pair = sessions(blocks, overrides={239: (105, 112, 104, 111), 243: (112, 113, 111, 112)},
                    filler=(106, 107, 105, 106))
    result = family(evaluate(pair), "EE-B")
    assert result.first.detection.candidate.signal.end_at == OPEN + 235 * M
    assert result.reset.previous.terminal_at == OPEN + 243 * M
    assert result.second.detection.reason == "decision_cutoff"
    assert result.second.detection.decision_at == OPEN + 240 * M and result.second.detection.parent is None


@pytest.mark.parametrize("gate", ("admission", "cap"))
def test_decision_known_cancellation_does_not_wait_for_an_irrelevant_missing_open(gate):
    if gate == "admission":
        blocks = a_blocks()
        blocks[5] = (100, 105, 95, 100)
        pair = sessions(blocks, missing=(58,))
        which, at, reason = "EE-A", 57, "admission_decline"
    else:
        pair = tuple(replace(item, closing=OPEN + 120 * M) for item in b_inputs(missing=(63,)))
        which, at, reason = "EE-B", 62, "same_day_hold_cap_does_not_fit"
    result = family(evaluate(pair), which)
    assert result.first.path.status == "declined" and result.first.path.entry_at is None
    assert result.reset.previous.kind == "cancelled" and result.reset.terminal_reason == reason
    assert result.reset.determined_at == OPEN + at * M
    assert result.second.detection.state == "unknown"  # later support is still missing


def test_expiry_bar_is_not_itself_a_new_cross_but_later_fresh_cross_is_supported():
    base = b_blocks()[:11] + [(102, 103, 95, 102)] * 3
    before = list(base)
    before[-1] = (102, 103, 95, 98)  # F70 precedes expiry availability T72
    blocked = family(evaluate(sessions(before, filler=(98, 99, 96, 98))), "EE-B")
    assert blocked.reset.determined_at == OPEN + 72 * M
    assert blocked.second.detection.parent is None
    after = base + [(102, 103, 94, 98), *b_blocks()[15:20]]
    admitted = family(evaluate(sessions(after, filler=(106, 107, 105, 106))), "EE-B")
    assert admitted.reset.previous.kind == "cancelled" and admitted.reset.previous.terminal_at == OPEN + 72 * M
    assert admitted.second.detection.candidate.parent.end_at == OPEN + 75 * M


def test_new_cancellation_cause_clock_and_first_identity_are_independently_reconstructed():
    current, independent = b_inputs()
    for session in (current, independent):
        session.minutes[OPEN + 63 * M] = replace(session.minutes[OPEN + 63 * M],
            ohlc=tuple(map(Decimal, (111, 112, 109, 110))))
    result = evaluate((current, independent))
    b = family(result, "EE-B")
    altered = [replace(b.reset, terminal_reason="same_day_hold_cap_does_not_fit"),
        replace(b.reset, determined_at=OPEN + 62 * M),
        replace(b.reset, previous=replace(b.reset.previous, terminal_at=OPEN + 62 * M)),
        replace(b.reset, first_sha256="0" * 64)]
    for reset in altered:
        forged = replace(result, families=(result.families[0], replace(b, reset=reset)))
        with pytest.raises(old_reference.ReferenceRefusal, match="mathematical_mismatch"):
            reference.verify_session_result(forged, independent)


def test_invalid_first_observation_remains_refusal_without_any_terminal():
    pair = b_inputs()
    for session in pair:
        session.minutes[OPEN] = replace(session.minutes[OPEN], ohlc=tuple(map(Decimal, (0, 110, 100, 105))))
    result = evaluate(pair)
    for item in result.families:
        assert item.first.detection.state == "invalid"
        assert item.reset.state == "refusal" and item.reset.previous is None
        assert item.reset.terminal_reason is item.reset.determined_at is item.second is None


def test_detection_only_helpers_reuse_fixed_math_without_any_path_call(monkeypatch):
    current, independent = a_inputs()
    complete = producer.evaluate_session(current, reference_session=independent)
    expected = family(complete, "EE-A")
    def forbidden(*args, **kwargs):
        raise AssertionError("candidate-only helper evaluated a path")
    monkeypatch.setattr(producer.paths, "evaluate_path", forbidden)
    monkeypatch.setattr(reference.math, "_path", forbidden)
    for previous, baseline in ((None, expected.first), (expected.reset.previous, expected.second)):
        detection, issues = producer._detection(current, "EE-A", previous)
        other, other_issues = reference._detection(independent, "EE-A", previous)
        assert detection == baseline.detection and issues == baseline.issues
        reference.math._record_shape(detection, other)
        assert reference.math._canonical(detection) == reference.math._canonical(other)
        assert reference.math._canonical(issues) == reference.math._canonical(other_issues)
