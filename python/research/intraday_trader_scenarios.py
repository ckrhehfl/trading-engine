"""Pure EE research proxies over caller-supplied, contiguous five-minute bars.

No readers, outcomes, fills, account checks or execution entrypoint. The caller
authenticates symbol/session/source identity and, for C, adjudicates the first
eligible event session. A price candidate is retained when information declines
or is unknown. Only bars needed before the first terminal result are consumed.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from re import fullmatch
from typing import Literal

from research import intraday_pattern_events as core


FIVE = timedelta(minutes=5)
LAG = timedelta(minutes=2)
CONTROLS = {
    "EE-A-WITHOUT-15M-CONTEXT": "EE-A",
    "EE-B-EARLY-RECLAIM": "EE-B",
    "EE-C-WITHOUT-PROFIT-CONTENT": "EE-C",
}
SCENARIOS = ("EE-A", "EE-B", "EE-C", *CONTROLS)


@dataclass(frozen=True)
class ProfitFact:
    symbol: str
    receipt_id: str
    source_id: str
    fiscal_year: int
    fiscal_quarter: int
    value: Decimal
    currency: str
    unit: str
    consolidation_basis: Literal["consolidated", "separate"]
    observation_end: date
    available_at: datetime
    is_original: bool


@dataclass(frozen=True)
class InformationAdmission:
    state: Literal["pass", "decline", "unknown"]
    reason: str
    applied: bool = True


@dataclass(frozen=True)
class Transition:
    kind: str
    bar_end_at: datetime
    available_at: datetime
    level: Decimal | None = None


@dataclass(frozen=True)
class PreviousTerminal:
    """Caller evidence of an earlier eligibility's cancellation or closure.

    This is not proof of a fill, cash, flatness or permission to place an order.
    A requires the previous attempt's frozen target; a missing target refuses
    its reset rather than inventing a new threshold.
    """

    family: Literal["EE-A", "EE-B"]
    symbol: str
    opening: datetime
    attempt_number: int
    parent_end_at: datetime
    terminal_at: datetime
    kind: Literal["cancelled", "closed"]
    frozen_target: Decimal | None = None


@dataclass(frozen=True)
class Candidate:
    scenario_id: str
    symbol: str
    opening: datetime
    parent: core.Bar
    original_parent_end_at: datetime
    signal: core.Bar
    key_level: Decimal
    stop: Decimal
    target: Decimal
    information: InformationAdmission
    event_session_admitted: bool | None
    attempt_number: int
    previous_terminal: PreviousTerminal | None
    context_closes: tuple[Decimal, ...] = ()
    profit_facts: tuple[ProfitFact | None, ProfitFact | None] | None = None

    @property
    def available_at(self) -> datetime:
        return self.signal.available_at

    @property
    def eligible(self) -> bool | None:
        if self.event_session_admitted is not True:
            return self.event_session_admitted
        if self.information.state == "unknown":
            return None
        if not self.information.applied:
            return True
        return self.information.state == "pass"


@dataclass(frozen=True)
class Detection:
    scenario_id: str
    state: Literal["candidate", "known_no_candidate", "unknown", "invalid"]
    reason: str
    decision_at: datetime
    observed_through: datetime | None
    parent: core.Bar | None
    original_parent_end_at: datetime | None
    attempt_number: int
    trace: tuple[Transition, ...]
    candidate: Candidate | None = None


def _decimal(value: Fraction) -> Decimal:
    """Construct a terminating Decimal without consulting decimal context."""
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise core.PatternValidationError("nonterminating_price")
    places = max(twos, fives)
    integer = value.numerator * 2 ** (places - twos) * 5 ** (places - fives)
    return Decimal((int(integer < 0), tuple(map(int, str(abs(integer)))), -places))


def _registered_bars(bars: Iterable[core.Bar], opening: datetime) -> Iterator[core.Bar]:
    expected = opening
    for bar in bars:
        if not isinstance(bar, core.Bar):
            raise core.PatternValidationError("stream_must_contain_Bar")
        core._aware(bar.end_at, "end_at")
        core._aware(bar.available_at, "available_at")
        core._aware(bar.start_at, "start_at")
        if bar.available_at != bar.end_at + LAG:
            raise core.PatternValidationError("invalid_source_clock")
        if bar.start_at < expected:
            raise core.PatternValidationError("invalid_source_sequence")
        expected = bar.end_at
        yield bar


def _profit_admission(
    facts: tuple[ProfitFact | None, ProfitFact | None] | None,
    symbol: str, decision_at: datetime, applied: bool, *,
    opening: datetime, event_session_admitted: bool | None,
) -> InformationAdmission:
    if facts is None:
        return InformationAdmission("unknown", "missing_profit_facts", applied)
    if not isinstance(facts, tuple) or len(facts) != 2:
        raise core.PatternValidationError("invalid_profit_pair")
    for fact in facts:
        if fact is None:
            continue
        if not isinstance(fact, ProfitFact):
            raise core.PatternValidationError("invalid_profit_fact")
        if (fact.symbol != symbol
                or any(not isinstance(value, str) or not value.strip()
                       for value in (fact.receipt_id, fact.source_id))
                or fact.is_original is not True):
            raise core.PatternValidationError("invalid_profit_identity_or_provenance")
        if (type(fact.fiscal_year) is not int or fact.fiscal_year < 1
                or type(fact.fiscal_quarter) is not int or fact.fiscal_quarter not in (1, 2, 3, 4)):
            raise core.PatternValidationError("invalid_profit_period")
        if not isinstance(fact.value, Decimal) or not fact.value.is_finite():
            raise core.PatternValidationError("invalid_profit_value")
        if (any(not isinstance(value, str) or not value.strip()
                for value in (fact.currency, fact.unit))
                or fact.consolidation_basis not in ("consolidated", "separate")):
            raise core.PatternValidationError("invalid_profit_basis")
        core._aware(fact.available_at, "profit_available_at")
        if fact.available_at > decision_at:
            raise core.PatternValidationError("profit_future_availability")
        if event_session_admitted is True and fact.available_at > opening:
            raise core.PatternValidationError("profit_unavailable_at_opening")
        if (type(fact.observation_end) is not date
                or fact.observation_end > fact.available_at.date()):
            raise core.PatternValidationError("invalid_profit_observation_clock")
        if abs(fact.fiscal_year - fact.observation_end.year) > 1:
            raise core.PatternValidationError("implausible_profit_fiscal_year")
    current, prior = facts
    if current is None or prior is None:
        return InformationAdmission("unknown", "missing_profit_facts", applied)
    if (current.fiscal_year != prior.fiscal_year + 1
            or current.fiscal_quarter != prior.fiscal_quarter
            or current.observation_end <= prior.observation_end):
        raise core.PatternValidationError("incomparable_profit_period")
    if (current.currency, current.unit, current.consolidation_basis) != (
            prior.currency, prior.unit, prior.consolidation_basis):
        raise core.PatternValidationError("incomparable_profit_basis")
    if current.receipt_id == prior.receipt_id:
        raise core.PatternValidationError("profit_originals_not_independently_identified")
    passes = Fraction(current.value) > 0 and Fraction(prior.value) <= 0
    return InformationAdmission("pass" if passes else "decline",
                                "profit_turnaround" if passes else "profit_predicate_false", applied)


def _validate_previous(
    previous: PreviousTerminal | None, family: str, symbol: str,
    opening: datetime, closing: datetime,
) -> None:
    if previous is None:
        return
    if (not isinstance(previous, PreviousTerminal) or family == "EE-C"
            or previous.family != family or previous.symbol != symbol
            or previous.opening != opening or previous.attempt_number != 1
            or type(previous.attempt_number) is not int
            or previous.kind not in ("cancelled", "closed")):
        raise core.PatternValidationError("invalid_previous_terminal_identity_or_attempt")
    core._aware(previous.parent_end_at, "previous_parent_end_at")
    core._aware(previous.terminal_at, "previous_terminal_at")
    if (previous.parent_end_at <= opening + 6 * FIVE
            or (previous.parent_end_at - opening) % FIVE
            or previous.terminal_at < previous.parent_end_at + LAG
            or previous.terminal_at > closing):
        raise core.PatternValidationError("invalid_previous_terminal_clock")
    if family == "EE-A":
        core._positive_decimal(previous.frozen_target, "previous_frozen_target")


def detect_bars(
    scenario_id: str, bars: Iterable[core.Bar], *, symbol: str,
    opening: datetime, closing: datetime,
    profit_facts: tuple[ProfitFact | None, ProfitFact | None] | None = None,
    event_session_admitted: bool | None = None,
    previous: PreviousTerminal | None = None,
) -> Detection:
    """Detect the first registered eligibility, or one explicitly evidenced reset.

    Invalid invocation parameters raise PatternValidationError before consuming
    bars; malformed consumed bars/profit facts return state invalid. A gap or
    exhausted causal prefix is unknown; only a fully observed cutoff or
    irreversible structural cancellation proves no candidate. No terminal result
    consumes a later bar. Selected/control calls must receive the same prefix.
    C's event-session assertion is caller evidence, not historical certification;
    admitted facts must nevertheless both be available by the session opening.
    """
    core._aware(opening, "opening")
    core._aware(closing, "closing")
    if (not isinstance(scenario_id, str) or scenario_id not in SCENARIOS
            or not isinstance(symbol, str) or fullmatch(r"KRX:[0-9A-Z]{6}", symbol) is None):
        raise core.PatternValidationError("invalid_scenario_or_symbol")
    family = CONTROLS.get(scenario_id, scenario_id)
    if (closing <= opening or (closing - opening) % FIVE
            or closing.astimezone(opening.tzinfo).date() != opening.date()):
        raise core.PatternValidationError("invalid_session_clock")
    if event_session_admitted is not None and type(event_session_admitted) is not bool:
        raise core.PatternValidationError("invalid_event_session_admission")
    _validate_previous(previous, family, symbol, opening, closing)
    cutoff = min(opening + timedelta(minutes=240), closing)
    observed = None
    parent = pullback = higher_low = None
    original_parent = previous.parent_end_at if isinstance(previous, PreviousTerminal) else None
    attempt = 2 if previous is not None else 1
    trace: list[Transition] = []
    history: list[core.Bar] = []
    key = target = stop = None
    base_high = None
    context_closes: tuple[Decimal, ...] = ()
    information = InformationAdmission("pass", "price_structure")
    expected_available = opening

    def result(state: str, reason: str, decision: datetime, candidate=None) -> Detection:
        return Detection(scenario_id, state, reason, decision, observed, parent,
                         original_parent, attempt, tuple(trace), candidate)

    def mark(kind: str, bar: core.Bar, level=None) -> None:
        trace.append(Transition(kind, bar.end_at, bar.available_at, level))

    def finish(bar: core.Bar) -> Detection:
        candidate = Candidate(
            scenario_id, symbol, opening, parent, original_parent, bar, key, stop, target,
            information, event_session_admitted if family == "EE-C" else True,
            attempt, previous, context_closes, profit_facts if family == "EE-C" else None,
        )
        mark("confirmation", bar)
        return result("candidate", "price_candidate", bar.available_at, candidate)

    try:
        prefix = iter(core._prefix(_registered_bars(bars, opening), opening, cutoff))
        count = (cutoff - opening - LAG) // FIVE
        for index in range(count):
            expected_available = opening + (index + 1) * FIVE + LAG
            try:
                bar = next(prefix)
            except StopIteration:
                return result("unknown", "missing_prefix", expected_available)
            except core.PatternGapError:
                return result("unknown", "noncontiguous_prefix", expected_available)
            observed = bar.end_at
            if index < 6:
                history.append(bar)
                if index == 5:
                    key = (min(item.low for item in history) if family == "EE-B"
                           else max(item.high for item in history))
                    target = max(item.high for item in history)
                    mark("opening_range_frozen", bar, key)
                continue
            close = Fraction(bar.close)
            if parent is None:
                eligible_clock = previous is None or (
                    bar.end_at > previous.terminal_at and bar.available_at > previous.terminal_at)
                if family == "EE-A":
                    threshold = max(key, previous.frozen_target) if previous else key
                    broke = close > Fraction(threshold)
                    if previous:
                        broke = broke and Fraction(history[-1].close) <= Fraction(threshold)
                elif family == "EE-B":
                    broke = close < Fraction(key)
                    if previous:
                        broke = broke and Fraction(history[-1].close) >= Fraction(key)
                else:
                    key = max(item.high for item in history[-5:])
                    broke = close > Fraction(key)
                if not eligible_clock or not broke:
                    history.append(bar)
                    continue
                parent = bar
                original_parent = original_parent or parent.end_at
                mark("parent", bar, key)
                if family == "EE-A":
                    completed = (history + [bar])[:((index + 1) // 3) * 3]
                    context_closes = tuple(item.close for item in completed[2::3][-2:])
                    passes = (Fraction(context_closes[-1]) > Fraction(context_closes[-2])
                              and Fraction(context_closes[-1]) > Fraction(history[0].open))
                    information = InformationAdmission("pass" if passes else "decline",
                        "positive_15m_context" if passes else "15m_context_false",
                        scenario_id not in CONTROLS)
                    target = bar.high
                elif family == "EE-B":
                    stop = bar.low
                else:
                    stop = min(item.low for item in history[-5:])
                    target = _decimal(3 * close - 2 * Fraction(stop))
                    information = _profit_admission(profit_facts, symbol, bar.available_at,
                        scenario_id not in CONTROLS, opening=opening,
                        event_session_admitted=event_session_admitted)
                    return finish(bar)
            elif family == "EE-A":
                if close <= Fraction(key):
                    mark("cancelled_key", bar, key)
                    return result("known_no_candidate", "key_level_lost", bar.available_at)
                if pullback is None:
                    target = max(target, bar.high)
                    if close < Fraction(history[-1].close):
                        pullback, stop = bar, bar.low
                        mark("pullback", bar, stop)
                        mark("impulse_target_frozen", bar, target)
                else:
                    age = (bar.end_at - pullback.end_at) // FIVE
                    if Fraction(bar.low) < Fraction(stop):
                        mark("cancelled_new_low", bar, stop)
                        return result("known_no_candidate", "pullback_low_lost", bar.available_at)
                    if higher_low is None:
                        if (Fraction(bar.low) > Fraction(stop)
                                and close > Fraction(pullback.close)):
                            higher_low = bar
                            mark("higher_low", bar, bar.high)
                    elif close > Fraction(higher_low.high):
                        return finish(bar)
                    if age == 6:
                        mark("expired", bar)
                        return result("known_no_candidate", "confirmation_timeout", bar.available_at)
            elif family == "EE-B":
                age = (bar.end_at - parent.end_at) // FIVE
                if Fraction(bar.low) < Fraction(stop):
                    mark("cancelled_new_low", bar, stop)
                    return result("known_no_candidate", "parent_low_lost", bar.available_at)
                if scenario_id == "EE-B-EARLY-RECLAIM":
                    if close > Fraction(key):
                        return finish(bar)
                elif age <= 4:
                    mark("base_bar", bar)
                    if age == 4:
                        base_high = max(item.high for item in history[-3:] + [bar])
                        mark("base_high_frozen", bar, base_high)
                elif close > Fraction(key) and close > Fraction(base_high):
                    return finish(bar)
                if age == 7:
                    mark("expired", bar)
                    return result("known_no_candidate", "confirmation_timeout", bar.available_at)
            history.append(bar)
        return result("known_no_candidate", "decision_cutoff", cutoff)
    except core.PatternValidationError as error:
        return result("invalid", str(error), expected_available)
