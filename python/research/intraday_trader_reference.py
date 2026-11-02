"""Independent EE mathematics over the original caller-supplied minute mapping.

This module has no reader and imports no detector, path evaluator or producer
projection. Only the immutable Minute, SessionInputs, ProfitFact and
PreviousTerminal input definitions are trusted. The reference searches successive
finite setup intervals directly; expected values never come from produced
candidates. A successful comparison covers typed-session mathematics, including
unknowns/refusals, not archives, population, runtime, accounts or publication.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from re import fullmatch
from typing import Any

from research.intraday_ed_inputs import Minute, SessionInputs
from research.intraday_trader_scenarios import PreviousTerminal, ProfitFact


MINUTE = timedelta(minutes=1)
SCENARIOS = ("EE-A", "EE-B", "EE-C", "EE-A-WITHOUT-15M-CONTEXT",
             "EE-B-EARLY-RECLAIM", "EE-C-WITHOUT-PROFIT-CONTENT")
FAMILIES = dict(zip(SCENARIOS, ("EE-A", "EE-B", "EE-C", "EE-A", "EE-B", "EE-C")))
PRICE_STATES = frozenset({"observed", "missing", "no_trade", "halt", "transport_error",
                          "conflict", "unit_unknown"})
PATH_ASSUMPTIONS = (
    "OPEN prices are modeled prices, not certified executable fills or liquidity",
    "caller supplies common physical price units and price basis; neither is certified here",
    "completed minute decisions assume end+2 minutes; source availability must not be later",
    "net33/net43 subtract fixed assumed round-trip 33/43 bp from price labels, not account PnL",
    "no cash, flatness, risk, source entitlement or trade eligibility certification",
)
CLAIM = "typed-session mathematical comparison only; no source/runtime/archive/population/account/publication authentication"
Record = dict[str, Any]


class ReferenceRefusal(ValueError):
    """Invalid invocation, unsupported output shape or mathematical disagreement."""


@dataclass(frozen=True)
class VerificationReceipt:
    scenarios: tuple[str, ...]
    compared_policy_count: int
    claim: str = CLAIM


class _InputError(ValueError):
    pass


class _PrefixEnd(Exception):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise _InputError(reason)


def _clock(value: Any, reason: str = "aware_clock_required") -> None:
    _require(isinstance(value, datetime) and value.utcoffset() is not None, reason)


def _price(value: Any, reason: str = "positive_finite_decimal_price_required") -> Fraction:
    _require(isinstance(value, Decimal) and value.is_finite() and value > 0, reason)
    return Fraction(value)


def _decimal(value: Fraction) -> Decimal:
    # Decimal division is context dependent. Construct the finite base-ten value
    # from integer arithmetic, so a caller's precision cannot alter a price.
    n, d, places = value.numerator, value.denominator, 0
    while d != 1:
        divisor = 10 if d % 10 == 0 else 2 if d % 2 == 0 else 5 if d % 5 == 0 else None
        _require(divisor is not None, "nonterminating_price")
        n *= 10 // divisor
        d //= divisor
        places += 1
    return Decimal((int(n < 0), tuple(map(int, str(abs(n)))), -places))


def _bar(start: datetime, prices: list[tuple[Decimal, ...]]) -> Record:
    return dict(start_at=start, end_at=start + 5 * MINUTE,
                available_at=start + 7 * MINUTE, open=prices[0][0],
                high=max(p[1] for p in prices), low=min(p[2] for p in prices),
                close=prices[-1][3])


class _Tape:
    """Reference-local demand cursor, deliberately unrelated to producer cache."""

    def __init__(self, session: SessionInputs):
        self.session = session
        self.bars: list[Record] = []
        self.issues: list[Record] = []
        self.cutoff = min(session.opening + 240 * MINUTE, session.closing)
        self.limit = (self.cutoff - session.opening - 2 * MINUTE) // (5 * MINUTE)
        self.requested = session.opening

    def next(self) -> Record:
        if len(self.bars) >= self.limit:
            raise _PrefixEnd
        at = self.session.opening + len(self.bars) * 5 * MINUTE
        self.requested = at + 7 * MINUTE
        rows: list[tuple[Decimal, ...]] = []
        for offset in range(5):
            minute = at + offset * MINUTE
            item = self.session.minutes.get(minute)
            if item is None:
                self.issues.append(dict(minute=minute, reason="missing_minute"))
                raise _PrefixEnd
            try:
                _require(isinstance(item, Minute), "typed_minute_required")
                _clock(item.start_at)
                _require(item.symbol == self.session.symbol and item.start_at == minute,
                         "minute_identity_changed")
                _clock(item.available_at)
                _require(item.available_at >= minute + MINUTE,
                         "minute_availability_precedes_completion")
                if item.available_at > self.requested:
                    self.issues.append(dict(minute=minute, reason="price_unavailable_by_bar_deadline"))
                    raise _PrefixEnd
                _require(type(item.price_state) is str and item.price_state in PRICE_STATES,
                         "invalid_price_state")
                if item.price_state != "observed":
                    _require(item.ohlc is None, "unknown_price_has_payload")
                    self.issues.append(dict(minute=minute, reason=f"price_{item.price_state}"))
                    raise _PrefixEnd
                _require(type(item.ohlc) is tuple and len(item.ohlc) == 4,
                         "observed_price_requires_ohlc")
                for value in item.ohlc:
                    _require(isinstance(value, Decimal) and value.is_finite() and value > 0,
                             "positive_finite_decimal_price_required")
                o, h, l, c = item.ohlc
                _require(l <= min(o, c) <= max(o, c) <= h, "invalid_minute_ohlc_order")
            except _InputError as error:
                self.issues.append(dict(minute=minute, reason=str(error)))
                raise
            rows.append(item.ohlc)
        result = _bar(at, rows)
        self.bars.append(result)
        return result


def _validate_previous(previous: PreviousTerminal | None, family: str, session: SessionInputs) -> None:
    if previous is None:
        return
    _require(isinstance(previous, PreviousTerminal) and family in ("EE-A", "EE-B")
             and previous.family == family and previous.symbol == session.symbol
             and previous.opening == session.opening and type(previous.attempt_number) is int
             and previous.attempt_number == 1 and previous.kind in ("cancelled", "closed"),
             "invalid_previous_terminal_identity_or_attempt")
    _clock(previous.parent_end_at, "previous_parent_end_at must be a timezone-aware datetime")
    _clock(previous.terminal_at, "previous_terminal_at must be a timezone-aware datetime")
    _require(previous.parent_end_at > session.opening + 30 * MINUTE
             and (previous.parent_end_at - session.opening) % (5 * MINUTE) == timedelta(0)
             and previous.parent_end_at + 2 * MINUTE <= previous.terminal_at <= session.closing,
             "invalid_previous_terminal_clock")
    if family == "EE-A":
        _price(previous.frozen_target, "previous_frozen_target must be a positive finite Decimal")


def _profit(facts: Any, symbol: str, opening: datetime, decision: datetime,
            admitted: bool | None, applied: bool) -> Record:
    if facts is None:
        return dict(state="unknown", reason="missing_profit_facts", applied=applied)
    _require(type(facts) is tuple and len(facts) == 2, "invalid_profit_pair")
    for fact in facts:
        if fact is None:
            continue
        _require(isinstance(fact, ProfitFact), "invalid_profit_fact")
        _require(fact.symbol == symbol and fact.is_original is True
                 and all(isinstance(v, str) and v.strip() for v in (fact.receipt_id, fact.source_id)),
                 "invalid_profit_identity_or_provenance")
        _require(type(fact.fiscal_year) is int and fact.fiscal_year >= 1
                 and type(fact.fiscal_quarter) is int and fact.fiscal_quarter in (1, 2, 3, 4),
                 "invalid_profit_period")
        _require(isinstance(fact.value, Decimal) and fact.value.is_finite(), "invalid_profit_value")
        _require(all(isinstance(v, str) and v.strip() for v in (fact.currency, fact.unit))
                 and fact.consolidation_basis in ("consolidated", "separate"), "invalid_profit_basis")
        _clock(fact.available_at, "profit_available_at must be a timezone-aware datetime")
        _require(fact.available_at <= decision, "profit_future_availability")
        _require(admitted is not True or fact.available_at <= opening, "profit_unavailable_at_opening")
        _require(type(fact.observation_end) is date and fact.observation_end <= fact.available_at.date(),
                 "invalid_profit_observation_clock")
        _require(abs(fact.fiscal_year - fact.observation_end.year) <= 1, "implausible_profit_fiscal_year")
    current, prior = facts
    if current is None or prior is None:
        return dict(state="unknown", reason="missing_profit_facts", applied=applied)
    _require(current.fiscal_year == prior.fiscal_year + 1
             and current.fiscal_quarter == prior.fiscal_quarter
             and current.observation_end > prior.observation_end, "incomparable_profit_period")
    _require((current.currency, current.unit, current.consolidation_basis)
             == (prior.currency, prior.unit, prior.consolidation_basis), "incomparable_profit_basis")
    _require(current.receipt_id != prior.receipt_id, "profit_originals_not_independently_identified")
    passes = Fraction(current.value) > 0 >= Fraction(prior.value)
    return dict(state="pass" if passes else "decline",
                reason="profit_turnaround" if passes else "profit_predicate_false", applied=applied)


def _detect(session: SessionInputs, scenario: str, facts: Any, admitted: bool | None,
            previous: PreviousTerminal | None) -> tuple[Record, tuple[Record, ...]]:
    tape = _Tape(session)
    family, trace = FAMILIES[scenario], []
    parent = None
    original = previous.parent_end_at if previous else None
    attempt = 2 if previous else 1

    def mark(kind: str, b: Record, level: Any = None) -> None:
        trace.append(dict(kind=kind, bar_end_at=b["end_at"], available_at=b["available_at"], level=level))

    def terminal(state: str, reason: str, at: datetime,
                 candidate: Record | None = None) -> tuple[Record, tuple[Record, ...]]:
        return (dict(scenario_id=scenario, state=state, reason=reason, decision_at=at,
                     observed_through=tape.bars[-1]["end_at"] if tape.bars else None,
                     parent=parent, original_parent_end_at=original, attempt_number=attempt,
                     trace=tuple(trace), candidate=candidate), tuple(tape.issues))

    try:
        initial = [tape.next() for _ in range(6)]
        key = min(b["low"] for b in initial) if family == "EE-B" else max(b["high"] for b in initial)
        target = max(b["high"] for b in initial)
        mark("opening_range_frozen", initial[-1], key)
        while parent is None:
            preceding = tape.bars[-5:]
            previous_close = tape.bars[-1]["close"]
            b = tape.next()
            if previous and (b["end_at"] <= previous.terminal_at or b["available_at"] <= previous.terminal_at):
                continue
            if family == "EE-C":
                key = max(p["high"] for p in preceding)
                crossed = b["close"] > key
            elif family == "EE-A":
                threshold = max(key, previous.frozen_target) if previous else key
                crossed = b["close"] > threshold and (previous is None or previous_close <= threshold)
            else:
                crossed = b["close"] < key and (previous is None or previous_close >= key)
            if crossed:
                parent = b
        original = original or parent["end_at"]
        mark("parent", parent, key)
        context: tuple[Decimal, ...] = ()
        information = dict(state="pass", reason="price_structure", applied=True)
        if family == "EE-C":
            stop = min(b["low"] for b in preceding)
            target = _decimal(3 * Fraction(parent["close"]) - 2 * Fraction(stop))
            information = _profit(facts, session.symbol, session.opening,
                                  parent["available_at"], admitted, scenario == family)
            signal = parent
        elif family == "EE-A":
            # Each third five-minute close is exactly the aligned 15m close.
            # Slice at the parent, never at the later confirmation.
            context = tuple(b["close"] for b in tape.bars[2::3][-2:])
            passes = context[-1] > context[-2] and context[-1] > initial[0]["open"]
            information = dict(state="pass" if passes else "decline",
                               reason="positive_15m_context" if passes else "15m_context_false",
                               applied=scenario == family)
            target = parent["high"]
            # Phase 1: first lower close, retaining the running impulse high.
            while True:
                prior_close = tape.bars[-1]["close"]
                b = tape.next()
                if b["close"] <= key:
                    mark("cancelled_key", b, key)
                    return terminal("known_no_candidate", "key_level_lost", b["available_at"])
                target = max(target, b["high"])
                if b["close"] < prior_close:
                    pullback, stop = b, b["low"]
                    mark("pullback", b, stop)
                    mark("impulse_target_frozen", b, target)
                    break
            # Phase 2: at most six subsequent bars, with the first higher-low
            # defining a separate frozen confirmation threshold.
            higher = None
            for _ in range(6):
                b = tape.next()
                if b["close"] <= key:
                    mark("cancelled_key", b, key)
                    return terminal("known_no_candidate", "key_level_lost", b["available_at"])
                if b["low"] < stop:
                    mark("cancelled_new_low", b, stop)
                    return terminal("known_no_candidate", "pullback_low_lost", b["available_at"])
                if higher is not None and b["close"] > higher["high"]:
                    signal = b
                    break
                if higher is None and b["low"] > stop and b["close"] > pullback["close"]:
                    higher = b
                    mark("higher_low", b, b["high"])
            else:
                mark("expired", b)
                return terminal("known_no_candidate", "confirmation_timeout", b["available_at"])
        else:
            stop = parent["low"]
            base: list[Record] = []
            for age in range(1, 8):
                b = tape.next()
                if b["low"] < stop:
                    mark("cancelled_new_low", b, stop)
                    return terminal("known_no_candidate", "parent_low_lost", b["available_at"])
                if scenario == "EE-B-EARLY-RECLAIM":
                    confirms = b["close"] > key
                elif age <= 4:
                    base.append(b)
                    mark("base_bar", b)
                    if age == 4:
                        base_high = max(p["high"] for p in base)
                        mark("base_high_frozen", b, base_high)
                    confirms = False
                else:
                    confirms = b["close"] > max(key, base_high)
                if confirms:
                    signal = b
                    break
            else:
                mark("expired", b)
                return terminal("known_no_candidate", "confirmation_timeout", b["available_at"])
        event = admitted if family == "EE-C" else True
        eligible = event if event is not True else (
            None if information["state"] == "unknown" else
            information["state"] == "pass" or not information["applied"])
        candidate = dict(scenario_id=scenario, symbol=session.symbol, opening=session.opening,
                         parent=parent, original_parent_end_at=original, signal=signal,
                         key_level=key, stop=stop, target=target, information=information,
                         event_session_admitted=event, attempt_number=attempt,
                         previous_terminal=previous, context_closes=context,
                         profit_facts=facts if family == "EE-C" else None)
        mark("confirmation", signal)
        detection, issues = terminal("candidate", "price_candidate", signal["available_at"], candidate)
        # Derived properties are separately checked against actual properties.
        detection["_eligible"] = eligible
        return detection, issues
    except _PrefixEnd:
        complete = len(tape.bars) >= tape.limit
        return terminal("known_no_candidate" if complete else "unknown",
                        "decision_cutoff" if complete else "missing_prefix",
                        tape.cutoff if complete else tape.requested)
    except _InputError as error:
        return terminal("invalid", str(error), tape.requested)


def _path(session: SessionInputs, scenario: str, candidate: Record,
          eligible: bool | None) -> tuple[Record | None, str | None]:
    admission = "pass" if eligible is True else "decline" if eligible is False else "unknown"
    plan = dict(family=scenario, symbol=session.symbol, decision_at=candidate["signal"]["available_at"],
                stop=candidate["stop"], target=candidate["target"], opening=session.opening,
                closing=session.closing, admission=admission)
    outcome = dict(plan=plan, status="unknown", reason="", entry_at=None, entry_open=None,
                   exit_at=None, exit_open=None, trigger_minute=None, trigger_at=None,
                   gross=None, net33=None, net43=None, simultaneous_touch_minutes=(),
                   assumptions=PATH_ASSUMPTIONS)
    touches: list[datetime] = []

    def result(status: str, reason: str) -> tuple[Record, None]:
        outcome.update(status=status, reason=reason, simultaneous_touch_minutes=tuple(touches))
        return outcome, None

    def row(at: datetime) -> Minute | None:
        item = session.minutes.get(at)
        if item is not None:
            _require(isinstance(item, Minute), "typed_minute_required")
            _clock(item.start_at)
            _require(item.symbol == session.symbol and item.start_at == at, "minute_identity_changed")
            _require(type(item.price_state) is str and item.price_state in PRICE_STATES, "invalid_price_state")
            _require(item.ohlc is None if item.price_state != "observed" else
                     type(item.ohlc) is tuple and len(item.ohlc) == 4,
                     "unknown_price_has_payload" if item.price_state != "observed" else "observed_price_requires_ohlc")
        return item

    def open_price(at: datetime) -> tuple[Decimal | None, str | None]:
        item = row(at)
        if item is None:
            return None, "missing_open_minute"
        if item.price_state != "observed":
            return None, f"open_price_{item.price_state}"
        _price(item.ohlc[0])
        return item.ohlc[0], None

    try:
        if admission != "pass":
            return result("declined" if admission == "decline" else "unknown", f"admission_{admission}")
        entry_at = plan["decision_at"].replace(second=0, microsecond=0) + MINUTE
        cap = entry_at + 60 * MINUTE
        if cap > session.closing:
            return result("declined", "same_day_hold_cap_does_not_fit")
        outcome["entry_at"] = entry_at
        entry_price, issue = open_price(entry_at)
        if issue:
            return result("unknown", issue)
        outcome["entry_open"] = entry_price
        entry, stop, target = map(Fraction, (entry_price, plan["stop"], plan["target"]))
        if not stop < entry < target:
            return result("declined", "entry_outside_frozen_levels")
        if (target - entry) / entry <= Fraction(33, 10000):
            return result("declined", "entry_cost_space_not_strict")
        trigger = None
        closes: list[Fraction] = []
        # A close at q can first execute at q+4. At cap equality the
        # unconsumed close has no influence; the time-cap OPEN is sufficient.
        for offset in range(56):
            at = entry_at + offset * MINUTE
            item = row(at)
            if item is None:
                return result("unknown", "missing_held_minute")
            _clock(item.available_at)
            _require(at + MINUTE <= item.available_at <= at + 3 * MINUTE,
                     "held_minute_unavailable_at_decision")
            if item.price_state != "observed":
                return result("unknown", f"held_price_{item.price_state}")
            high, low, close = map(_price, item.ohlc[1:])
            _require(low <= close <= high, "invalid_held_hlc_order")
            if low <= stop and high >= target:
                touches.append(at)
            closes.append(close)
            reason = ("structural_close_invalidation" if close <= stop else
                      "known_target_close_reached" if close >= target else
                      "response_failure" if len(closes) >= 10 and max(closes) <= entry and close < entry else None)
            if reason:
                trigger = (at, at + 3 * MINUTE, reason)
                break
        exit_at = trigger[1] + MINUTE if trigger else cap
        outcome["exit_at"] = exit_at
        if trigger:
            outcome.update(trigger_minute=trigger[0], trigger_at=trigger[1])
        exit_price, issue = open_price(exit_at)
        if issue:
            return result("unknown", issue)
        gross = Fraction(exit_price) / entry - 1
        outcome.update(exit_open=exit_price, gross=gross, net33=gross - Fraction(33, 10000),
                       net43=gross - Fraction(43, 10000))
        return result("traded", trigger[2] if trigger else "time_cap")
    except _InputError as error:
        return None, str(error)


def expected_session(
    session: SessionInputs, *, scenarios: tuple[str, ...] = SCENARIOS,
    profit_facts: tuple[ProfitFact | None, ProfitFact | None] | None = None,
    event_session_admitted: bool | None = None,
    previous: Mapping[str, PreviousTerminal | None] | None = None,
    source_metadata: bytes = b"{}",
) -> Record:
    """Reconstruct every requested policy from the original supplied minutes.

    Invalid invocation raises ReferenceRefusal. Consumed invalid price/fact rows
    are represented by detection invalid or a separate path_refusal, with no
    manufactured return. No source suffix or unavailable outcome selects signals.
    """
    try:
        _require(isinstance(session, SessionInputs), "typed_session_required")
        _require(isinstance(session.symbol, str) and fullmatch(r"KRX:[0-9A-Z]{6}", session.symbol) is not None,
                 "invalid_scenario_or_symbol")
        for value in (session.opening, session.closing):
            _clock(value)
        _require(session.opening.second == session.opening.microsecond == 0
                 and session.closing.second == session.closing.microsecond == 0,
                 "whole_minute_session_required")
        _require(session.closing > session.opening and session.closing.date() == session.opening.date()
                 and session.closing.utcoffset() == session.opening.utcoffset()
                 and (session.closing - session.opening) % (5 * MINUTE) == timedelta(0), "invalid_session_clock")
        _require(isinstance(session.minutes, Mapping), "minute_mapping_required")
        _require(type(scenarios) is tuple and bool(scenarios)
                 and all(type(s) is str and s in SCENARIOS for s in scenarios)
                 and len(set(scenarios)) == len(scenarios), "invalid_scenarios")
        _require(event_session_admitted is None or type(event_session_admitted) is bool,
                 "invalid_event_session_admission")
        _require(type(source_metadata) is bytes, "opaque_source_metadata_bytes_required")
        _require(previous is None or isinstance(previous, Mapping), "previous_mapping_required")
        if previous is not None:
            _require(all(type(key) is str and key in SCENARIOS for key in previous), "invalid_previous_scenario")
        results = []
        for scenario in scenarios:
            prior = previous.get(scenario) if previous else None
            _validate_previous(prior, FAMILIES[scenario], session)
            detection, issues = _detect(session, scenario, profit_facts, event_session_admitted, prior)
            eligible = detection.pop("_eligible", None)
            path, refusal = (None, None) if detection["candidate"] is None else _path(
                session, scenario, detection["candidate"], eligible)
            results.append(dict(scenario_id=scenario, detection=detection, path=path,
                                path_refusal=refusal, issues=issues))
        return dict(symbol=session.symbol, opening=session.opening, closing=session.closing,
                    source_metadata=source_metadata, results=tuple(results))
    except _InputError as error:
        raise ReferenceRefusal(str(error)) from error


def _canonical(value: Any) -> Any:
    """Type-tag exact scalars; never let bool/int or list/tuple compare equal."""
    if is_dataclass(value) and not isinstance(value, type):
        return _canonical({field.name: getattr(value, field.name) for field in fields(value)})
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ReferenceRefusal("non_string_result_field")
        return ("record", tuple((key, _canonical(value[key])) for key in sorted(value)))
    if type(value) is tuple:
        return ("tuple", tuple(map(_canonical, value)))
    if value is None or type(value) in (str, bytes, bool, int):
        return (type(value).__name__, value)
    if isinstance(value, datetime):
        return ("datetime", value.isoformat(), value.fold)
    if type(value) is date:
        return ("date", value.isoformat())
    if type(value) is Decimal:
        if not value.is_finite():
            raise ReferenceRefusal("nonfinite_result_decimal")
        return ("Decimal", Fraction(value))
    if type(value) is Fraction:
        return ("Fraction", value.numerator, value.denominator)
    raise ReferenceRefusal(f"unsupported_result_scalar:{type(value).__name__}")


def _record_shape(actual: Any, expected: Any) -> None:
    """The public result is nested typed records, never equivalent loose dicts."""
    if type(expected) is dict:
        if not is_dataclass(actual) or isinstance(actual, type):
            raise ReferenceRefusal("typed_nested_result_required")
        names = {field.name for field in fields(actual)}
        if names != set(expected):
            raise ReferenceRefusal("result_record_fields_changed")
        for key, value in expected.items():
            _record_shape(getattr(actual, key), value)
    elif type(expected) is tuple:
        if type(actual) is not tuple or len(actual) != len(expected):
            raise ReferenceRefusal("result_tuple_shape_changed")
        for observed, reference in zip(actual, expected, strict=True):
            _record_shape(observed, reference)
    elif is_dataclass(expected):
        if type(actual) is not type(expected):
            raise ReferenceRefusal("trusted_input_record_type_changed")


def verify_session_result(actual: Any, session: SessionInputs, **kwargs: Any) -> VerificationReceipt:
    """Refuse any changed field, missing/extra/duplicate policy or type drift.

    Every declared Detection/Transition/Candidate/PathPlan/PathOutcome field is
    compared, including assumptions and opaque metadata bytes. Candidate derived
    eligibility/availability properties are also checked. A receipt makes no
    whole-run, archive or executable-fill certification.
    """
    expected = expected_session(session, **kwargs)
    if not is_dataclass(actual) or isinstance(actual, type):
        raise ReferenceRefusal("typed_session_result_required")
    _record_shape(actual, expected)
    if _canonical(actual) != _canonical(expected):
        raise ReferenceRefusal("session_scientific_signature_mismatch")
    for policy, reference in zip(actual.results, expected["results"], strict=True):
        candidate = policy.detection.candidate
        if candidate is None:
            continue
        reference_candidate = reference["detection"]["candidate"]
        event, info = reference_candidate["event_session_admitted"], reference_candidate["information"]
        eligible = event if event is not True else (
            None if info["state"] == "unknown" else info["state"] == "pass" or not info["applied"])
        try:
            same = (_canonical(candidate.eligible) == _canonical(eligible)
                    and _canonical(candidate.available_at) == _canonical(reference["detection"]["decision_at"]))
        except (AttributeError, TypeError, ValueError) as error:
            raise ReferenceRefusal("invalid_candidate_derived_properties") from error
        if not same:
            raise ReferenceRefusal("candidate_derived_property_mismatch")
    return VerificationReceipt(tuple(item["scenario_id"] for item in expected["results"]), len(expected["results"]))
