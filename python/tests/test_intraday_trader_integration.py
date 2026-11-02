"""Invented hand checks of detector -> delayed price-path boundaries.

These fixtures do not verify the referenced traders' source IDs, original
theories, market returns, fill liquidity or account admission. The only file
read is the registered scenario metadata; every bar and minute is invented.
No production wrapper/reader/account or dependency on another test module is
introduced. Fixed observed transitions and prices bind config to behavior.
"""

from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path

import pytest

from research.intraday_ed_inputs import Minute
from research.intraday_pattern_events import Bar
from research import intraday_trader_paths as paths
from research import intraday_trader_scenarios as scenarios


D = Decimal
M = timedelta(minutes=1)
FIVE = timedelta(minutes=5)
OPEN = datetime(2026, 1, 5, 9, tzinfo=timezone(timedelta(hours=9)))
CLOSE = OPEN + 390 * M
SYMBOL = "KRX:ABC123"


@pytest.fixture(scope="module")
def spec():
    path = Path(__file__).resolve().parents[2] / "configs/research/discovery/intraday-trader-scenarios-v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


def bar(n, *, close="100", high="110", low="90"):
    end = OPEN + n * FIVE
    return Bar(end - FIVE, end, end + 2 * M, D(close), D(high), D(low), D(close))


def opening():
    return [bar(n, close="106" if n == 3 else "108" if n == 6 else "100")
            for n in range(1, 7)]


def a_bars():
    return opening() + [bar(7, close="116", high="120", low="111"),
                        bar(8, close="114", high="118", low="112"),
                        bar(9, close="115", high="117", low="113"),
                        bar(10, close="118", high="119", low="114")]


def b_bars():
    return opening() + [bar(7, close="88", high="150", low="80")] + [
        bar(n, close="91", high="95", low="82") for n in range(8, 12)
    ] + [bar(12, close="96", high="97", low="83")]


def c_bars():
    result = opening() + [bar(7, close="111", high="112", low="99")]
    # This old low is outside the five bars preceding the C parent.
    result[0] = bar(1, low="80")
    return result


def facts(*, current="100"):
    available = OPEN - timedelta(days=3)
    return (
        scenarios.ProfitFact(SYMBOL, "invented-current", "invented-content", 2025, 3,
                             D(current), "KRW", "million", "consolidated",
                             date(2025, 9, 30), available, True),
        scenarios.ProfitFact(SYMBOL, "invented-prior", "invented-content", 2024, 3,
                             D("-10"), "KRW", "million", "consolidated",
                             date(2024, 9, 30), available, True),
    )


def detect(scenario_id, bars, **kwargs):
    return scenarios.detect_bars(scenario_id, bars, symbol=SYMBOL, opening=OPEN,
                                 closing=CLOSE, **kwargs)


def path_plan(candidate):
    """Caller-side test mapping preserves controls and common unknown support."""
    admission = {True: "pass", False: "decline", None: "unknown"}[candidate.eligible]
    return paths.PathPlan(candidate.scenario_id, candidate.symbol, candidate.available_at,
                          candidate.stop, candidate.target, candidate.opening, CLOSE, admission)


def minute(at, *, op="100", high="105", low="95", close="101"):
    return Minute(SYMBOL, at, at + 3 * M, tuple(D(v) for v in (op, high, low, close)),
                  None, volume_state="missing")


def open_only(at, value):
    # The exit OPEN cannot depend on this minute's archived future H/L/C.
    return Minute(SYMBOL, at, at + 3 * M, (D(value), object(), object(), object()),
                  None, volume_state="missing")


class CoordinatesOnly(Mapping):
    def __init__(self, rows):
        self.rows, self.reads = rows, []

    def __getitem__(self, key):
        self.reads.append(key)
        assert key in self.rows, f"unregistered invented coordinate read: {key}"
        return self.rows[key]

    def __len__(self):
        raise AssertionError("whole-path length")

    def __iter__(self):
        raise AssertionError("whole-path scan")


def target_rows(candidate, entry, exit_open):
    at = candidate.available_at + M
    return CoordinatesOnly({
        at: minute(at, op=entry, high=str(candidate.target), low=entry,
                   close=str(candidate.target)),
        at + 4 * M: open_only(at + 4 * M, exit_open),
    })


def same_price_candidate(left, right):
    assert (left.parent, left.signal, left.stop, left.target, left.available_at) == (
        right.parent, right.signal, right.stop, right.target, right.available_at)


@pytest.mark.parametrize("family, make_bars, signal_minutes, stop, target, entry, exit_open, gross", [
    ("EE-A", a_bars, 50, "112", "120", "117", "121", Fraction(4, 117)),
    ("EE-B", b_bars, 60, "80", "110", "97", "109", Fraction(12, 97)),
    ("EE-C", c_bars, 35, "90", "153", "112", "154", Fraction(3, 8)),
])
def test_three_candidates_keep_frozen_levels_and_use_delayed_open_labels(
        spec, family, make_bars, signal_minutes, stop, target, entry, exit_open, gross):
    kwargs = dict(profit_facts=facts(), event_session_admitted=True) if family == "EE-C" else {}
    detection = detect(family, make_bars(), **kwargs)
    assert detection.state == "candidate"
    candidate = detection.candidate
    assert candidate.signal.end_at == OPEN + signal_minutes * M
    assert candidate.stop == D(stop) and candidate.target == D(target)
    assert candidate.eligible is True
    assert all(t.available_at - t.bar_end_at ==
               spec["common"]["availability_lag_minutes"] * M for t in detection.trace)
    assert candidate.signal.end_at - candidate.signal.start_at == spec["common"]["bar_minutes"] * M
    source = target_rows(candidate, entry, exit_open)
    outcome = paths.evaluate_path(path_plan(candidate), source)
    assert outcome.status == "traded" and outcome.reason == "known_target_close_reached"
    assert outcome.entry_at == OPEN + (signal_minutes + 3) * M
    assert outcome.entry_at > candidate.available_at > candidate.signal.end_at
    assert outcome.entry_open == D(entry) != candidate.signal.close
    assert outcome.trigger_at == outcome.entry_at + (1 + spec["common"]["availability_lag_minutes"]) * M
    assert outcome.exit_at == OPEN + (signal_minutes + 7) * M
    assert outcome.exit_open == D(exit_open)
    assert outcome.gross == gross
    assert outcome.net33 == gross - Fraction(D(spec["cost_scenarios"]["primary_bps"])) / 10000
    assert outcome.net43 == gross - Fraction(D(spec["cost_scenarios"]["stress_bps"])) / 10000
    assert source.reads == [outcome.entry_at, outcome.entry_at, outcome.exit_at]


def test_a_context_decline_and_control_map_same_price_candidate_to_different_admission():
    bars = a_bars()
    bars[2] = bar(3, close="108")  # Both completed 15-minute closes are108.
    selected = detect("EE-A", bars).candidate
    control = detect("EE-A-WITHOUT-15M-CONTEXT", bars).candidate
    same_price_candidate(selected, control)
    assert selected.information.state == control.information.state == "decline"
    assert selected.information.applied is True and control.information.applied is False
    no_source = CoordinatesOnly({})
    rejected = paths.evaluate_path(path_plan(selected), no_source)
    assert rejected.status == "declined" and no_source.reads == []
    assert rejected.gross is rejected.net33 is rejected.net43 is None
    result = paths.evaluate_path(path_plan(control), target_rows(control, "117", "121"))
    assert result.status == "traded" and result.gross == Fraction(4, 117)


@pytest.mark.parametrize("scenario_id", ["EE-C", "EE-C-WITHOUT-PROFIT-CONTENT"])
def test_c_missing_facts_remain_unknown_in_both_arms_without_path_reads(scenario_id):
    candidate = detect(scenario_id, c_bars(), event_session_admitted=True).candidate
    assert candidate is not None and candidate.information.state == "unknown"
    no_source = CoordinatesOnly({})
    result = paths.evaluate_path(path_plan(candidate), no_source)
    assert result.status == "unknown" and result.reason == "admission_unknown"
    assert result.gross is result.net33 is result.net43 is None
    assert no_source.reads == []


def test_c_known_false_profit_predicate_declines_while_control_keeps_same_path():
    kwargs = dict(profit_facts=facts(current="0"), event_session_admitted=True)
    selected = detect("EE-C", c_bars(), **kwargs).candidate
    control = detect("EE-C-WITHOUT-PROFIT-CONTENT", c_bars(), **kwargs).candidate
    same_price_candidate(selected, control)
    assert selected.information.reason == control.information.reason == "profit_predicate_false"
    no_source = CoordinatesOnly({})
    result = paths.evaluate_path(path_plan(selected), no_source)
    assert result.status == "declined" and no_source.reads == []
    assert result.gross is result.net33 is result.net43 is None
    result = paths.evaluate_path(path_plan(control), target_rows(control, "112", "154"))
    assert result.status == "traded" and result.gross == Fraction(3, 8)


def test_registered_a_deadline_is_observed_sixth_bar_then_irreversible_expiry(spec):
    definition = next(f for f in spec["families"] if f["id"] == "EE-A")
    bars = a_bars()[:9] + [bar(n, close="115", high="117", low="113") for n in range(10, 14)]
    bars += [bar(14, close="118", high="119", low="114")]
    result = detect("EE-A", bars)
    assert result.state == "candidate" and result.candidate.signal == bars[-1]
    frozen = next(t for t in result.trace if t.kind == "opening_range_frozen")
    pullback = next(t for t in result.trace if t.kind == "pullback")
    confirmed = result.trace[-1]
    assert frozen.bar_end_at == OPEN + definition["initial_range_minutes"] * M == OPEN + 30 * M
    assert confirmed.bar_end_at == OPEN + 70 * M
    assert (confirmed.bar_end_at - pullback.bar_end_at) / FIVE == definition["confirm_deadline_bars"]
    bars[-1] = bar(14, close="117", high="119", low="114")
    bars.append(bar(15, close="118", high="119", low="114"))
    expired = detect("EE-A", bars)
    assert expired.state == "known_no_candidate" and expired.reason == "confirmation_timeout"
    assert expired.trace[-1].kind == "expired"
    assert expired.observed_through == confirmed.bar_end_at  # The seventh cannot rescue it.


def test_registered_b_base_excludes_parent_and_confirms_only_in_next_three_bars(spec):
    definition = next(f for f in spec["families"] if f["id"] == "EE-B")
    bars = b_bars()[:-1] + [bar(n, close="95", high="96", low="83") for n in (12, 13)]
    bars.append(bar(14, close="96", high="97", low="83"))
    result = detect("EE-B", bars)
    assert result.state == "candidate" and result.candidate.signal == bars[-1]
    base = [t for t in result.trace if t.kind == "base_bar"]
    assert [t.bar_end_at for t in base] == [OPEN + n * FIVE for n in (8, 9, 10, 11)]
    assert len(base) == definition["base_bars"]
    assert next(t.level for t in result.trace if t.kind == "base_high_frozen") == D("95")
    assert result.candidate.parent.high == D("150")  # Parent is not one of the base bars.
    assert (base[-1].bar_end_at - result.parent.end_at) / FIVE == definition["base_bars"]
    assert (result.candidate.signal.end_at - base[-1].bar_end_at) / FIVE == definition["confirm_deadline_bars"]
    bars[-1] = bar(14, close="95", high="96", low="83")
    bars.append(bar(15, close="96", high="97", low="83"))
    expired = detect("EE-B", bars)
    assert expired.state == "known_no_candidate" and expired.reason == "confirmation_timeout"
    assert expired.observed_through == OPEN + 70 * M


def test_registered_response_tenth_available_close_and_stop_priority(spec):
    candidate = detect("EE-B", b_bars()).candidate
    at = candidate.available_at + M
    rows = {at + n * M: minute(at + n * M, high="101", low="98", close="99") for n in range(10)}
    rows[at + 13 * M] = open_only(at + 13 * M, "98")
    outcome = paths.evaluate_path(path_plan(candidate), CoordinatesOnly(rows))
    assert outcome.reason == "response_failure" and outcome.gross == Fraction(-1, 50)
    assert outcome.trigger_minute == at + 9 * M
    assert (outcome.trigger_minute - at) / M + 1 == spec["exits"]["response_minutes"]
    assert outcome.trigger_at == at + 12 * M
    assert outcome.trigger_at - outcome.trigger_minute == (1 + spec["common"]["availability_lag_minutes"]) * M
    assert outcome.exit_at == at + 13 * M
    rows[at + 9 * M] = minute(at + 9 * M, high="101", low="79", close="80")
    stopped = paths.evaluate_path(path_plan(candidate), CoordinatesOnly(rows))
    assert stopped.reason == "structural_close_invalidation"
    assert stopped.trigger_minute == outcome.trigger_minute
    assert spec["exits"]["priority"].index(stopped.reason) < spec["exits"]["priority"].index(outcome.reason)


def test_registered_cap_sixty_uses_cap_open_and_does_not_require_irrelevant_closes(spec):
    candidate = detect("EE-B", b_bars()).candidate
    at = candidate.available_at + M
    rows = {at + n * M: minute(at + n * M, op="97", high="100", low="95", close="98")
            for n in range(56)}
    rows[at + 60 * M] = open_only(at + 60 * M, "99")
    source = CoordinatesOnly(rows)
    result = paths.evaluate_path(path_plan(candidate), source)
    assert result.status == "traded" and result.reason == "time_cap"
    assert result.exit_at == at + 60 * M
    assert (result.exit_at - result.entry_at) / M == spec["common"]["hold_cap_minutes"]
    assert result.gross == Fraction(2, 97)
    assert max(t for t in source.reads if t != result.exit_at) == result.exit_at - 5 * M
    assert result.trigger_at is None


def test_registered_costs_use_same_primary_entry_gate_for_both_labels(spec):
    costs = spec["cost_scenarios"]
    assert costs["entry_space_uses_primary_for_both_scenarios"] is True
    assert costs["date_specific_actual_cost_certified"] is costs["net_labels_are_account_pnl"] is False
    # Hand calculations: 3*100.03-2*99.88=100.33; 3*100.04-2*99.89=100.34.
    for parent_close, stop, target, expected in [
            ("100.03", "99.88", "100.33", "declined"),
            ("100.04", "99.89", "100.34", "traded")]:
        bars = [bar(n, high="100.02", low=stop) for n in range(1, 7)]
        bars.append(bar(7, close=parent_close, high="100.10", low="99.98"))
        candidate = detect("EE-C", bars, profit_facts=facts(), event_session_admitted=True).candidate
        assert candidate.target == D(target) and candidate.stop == D(stop)
        at = candidate.available_at + M
        source = CoordinatesOnly({at: open_only(at, "100")}) if expected == "declined" else target_rows(
            candidate, "100", "100.40")
        result = paths.evaluate_path(path_plan(candidate), source)
        assert result.status == expected
        if expected == "declined":
            assert result.reason == "entry_cost_space_not_strict"
            assert Fraction(candidate.target) - 100 == Fraction(D(costs["primary_bps"])) / 100
            assert result.gross is result.net33 is result.net43 is None
        else:
            assert result.gross == Fraction(4, 1000)
            assert result.net33 == Fraction(7, 10000) > 0
            assert result.net43 == Fraction(-3, 10000) < 0
            assert result.net33 == result.gross - Fraction(D(costs["primary_bps"])) / 10000
            assert result.net43 == result.gross - Fraction(D(costs["stress_bps"])) / 10000


def test_registered_detector_cutoff_is_causal_and_does_not_adopt_later_parent(spec):
    bars = opening() + [bar(n) for n in range(7, 48)]
    bars.append(bar(48, close="116", high="120", low="111"))
    result = detect("EE-A", bars)
    assert result.state == "known_no_candidate" and result.reason == "decision_cutoff"
    assert result.decision_at == OPEN + 240 * M
    assert (result.decision_at - OPEN) / M == spec["common"]["decision_cutoff_after_open_minutes"]
    assert result.observed_through == OPEN + 235 * M
