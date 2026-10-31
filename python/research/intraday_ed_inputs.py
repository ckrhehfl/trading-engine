"""Pure ED-H1 inputs over caller-supplied, pre-adjudicated observations.

There is no reader, calendar adjudicator, transport, model, logger or account.
The caller supplies the original peers, twenty expected prior openings and the
opening-frozen filing judgment. Those declarations are not certified by this
adapter. Only requested causal coordinates are consumed, never a day suffix.
Decimal sources and Fraction arithmetic survive the explicit float64 boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from math import isfinite, sqrt
from typing import Literal

from research.intraday_pattern_events import Bar


MINUTE = timedelta(minutes=1)
FIVE = 5 * MINUTE
SCHEMA_ID = "ED-H1-common-30minute-observed-peer-v1"
NUMERIC_BOUNDARY = "float64/IEEE-754-binary64"
State = Literal["observed", "missing", "no_trade", "halt", "transport_error", "conflict", "unit_unknown"]
STATES = frozenset({"observed", "missing", "no_trade", "halt", "transport_error", "conflict", "unit_unknown"})
OHLC = tuple[Decimal, Decimal, Decimal, Decimal]
EventState = Literal["ELIGIBLE_ORIGINAL", "KNOWN_NO_WHITELISTED_LIST_EVENT", "UNKNOWN"]
EventType = Literal["ORIGINAL_ANNUAL", "ORIGINAL_HALF_YEAR", "ORIGINAL_QUARTER"]
DYNAMIC_CHANNELS = (
    "target_minute_oc_return", "target_range_over_open", "target_close_location_payload",
    "target_zero_range_flag", "target_same_clock_share_volume_ratio", "observed_peer_minute_oc_return",
)
STATIC_NUMERIC_NAMES = (
    "target_30m_return", "peer_30m_return", "peer_last15_return", "peer_positive_30m_fraction",
    "target_minus_peer_30m_return", "last5_share_volume_ratio", "past20_sameclock_30m_return_volatility",
    "fraction_of_session_elapsed", "structural_stop_gap_from_trigger_close",
    "common_peer_count_over_original_peer_count", "past20_sameclock5_share_volume_median",
)
EVENT_TYPE_NAMES = ("NO_EVENT", "ORIGINAL_ANNUAL", "ORIGINAL_HALF_YEAR", "ORIGINAL_QUARTER")
EVENT_AGE_NAMES = ("NO_EVENT_AGE",) + tuple(f"M{start}_{start + 29}" for start in range(30, 330, 30))
FEATURE_NAMES = (
    tuple(f"t{minute:02d}__{name}" for minute in range(30) for name in DYNAMIC_CHANNELS)
    + STATIC_NUMERIC_NAMES
    + tuple(f"event_type__{name}" for name in EVENT_TYPE_NAMES)
    + tuple(f"event_age__{name}" for name in EVENT_AGE_NAMES)
)
_EXACT_STATIC_NAMES = STATIC_NUMERIC_NAMES[:6] + STATIC_NUMERIC_NAMES[7:]


class InputRefusal(ValueError):
    """Malformed typed inputs are integrity failures, not observed missingness."""


@dataclass(frozen=True)
class Minute:
    symbol: str
    start_at: datetime
    available_at: datetime
    ohlc: OHLC | None
    shares: Decimal | None
    price_state: State = "observed"
    volume_state: State = "observed"


@dataclass(frozen=True)
class SessionInputs:
    symbol: str
    opening: datetime
    closing: datetime
    minutes: Mapping[datetime, Minute]


@dataclass(frozen=True)
class OpeningContext:
    """Caller judgment for one opening, not raw filing/title interpretation.

    available_at is the declared causal availability of this opening judgment,
    not today's retrieval timestamp. event_opening is the caller-adjudicated
    FIRST eligible opening, which must equal opening for ELIGIBLE_ORIGINAL.
    Unknown provenance must not be relabelled as a known no-event judgment.
    """

    symbol: str
    opening: datetime
    state: EventState
    available_at: datetime | None
    original_types: tuple[EventType, ...] = ()
    receipt_ids: tuple[str, ...] = ()
    event_opening: datetime | None = None


@dataclass(frozen=True)
class Issue:
    symbol: str
    minute: datetime
    reason: str


@dataclass(frozen=True)
class Window:
    minutes: tuple[Minute, ...]
    issues: tuple[Issue, ...]


@dataclass(frozen=True)
class H1Parent:
    symbol: str
    opening: datetime
    trigger: Bar
    preceding: tuple[Bar, ...]

    @property
    def decision_at(self) -> datetime:
        return self.trigger.available_at

    @property
    def structural_stop(self) -> Decimal:
        return min(bar.low for bar in self.preceding)


EDParent = H1Parent


@dataclass(frozen=True)
class ParentScan:
    status: Literal["parent", "known_no_parent", "parent_identity_unknown"]
    parent: H1Parent | None = None
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class VolumeBaseline:
    expected_openings: tuple[datetime, ...]
    totals: tuple[Fraction | None, ...]
    median: Fraction | None
    issues: tuple[Issue, ...]


@dataclass(frozen=True)
class PeerContext:
    common_peers: tuple[str, ...]
    minute_returns: tuple[Fraction, ...]
    return30: Fraction | None
    last15_return: Fraction | None
    positive_fraction: Fraction | None
    issues: tuple[Issue, ...]


@dataclass(frozen=True)
class H1WindowInputs:
    """Exact common record used by both rules and the 206-column boundary.

    static_exact holds ten rational scalars in STATIC_NUMERIC_NAMES order with
    volatility omitted. The eleventh scalar is sqrt(past20_return_variance),
    computed only by flatten_h1_features; exact variance remains in this record.
    Undefined no-event age is None with its own bit, not numerical age zero.
    Known price inputs may survive unknown filing context; no full vector does.
    """

    parent: H1Parent
    common_peers: tuple[str, ...]
    dynamic: tuple[tuple[Fraction, ...], ...] | None
    static_exact: tuple[tuple[str, Fraction], ...]
    past20_return_variance: Fraction | None
    opening_context: OpeningContext
    event_type_bits: tuple[Fraction, ...] | None
    event_age_bits: tuple[Fraction, ...] | None
    event_age_minutes: int | None
    issues: tuple[Issue, ...]


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise InputRefusal(reason)


def _clock(value: datetime) -> None:
    _require(isinstance(value, datetime) and value.utcoffset() is not None, "aware_clock_required")


def _session(value: SessionInputs) -> None:
    _require(isinstance(value, SessionInputs), "typed_session_required")
    _clock(value.opening)
    _clock(value.closing)
    _require(type(value.symbol) is str and bool(value.symbol) and isinstance(value.minutes, Mapping),
             "invalid_session_identity")
    _require(value.closing > value.opening
             and (value.closing - value.opening) % MINUTE == timedelta(0), "invalid_session_geometry")


def window(session: SessionInputs, offset: int, count: int, decision_at: datetime,
           *, price: bool = True, volume: bool = False) -> Window:
    """Read exactly this session-relative window, with no suffix iteration."""
    _session(session)
    _clock(decision_at)
    _require(type(offset) is int and offset >= 0 and type(count) is int and count > 0
             and type(price) is bool and type(volume) is bool and (price or volume),
             "invalid_window_geometry")
    end = session.opening + (offset + count) * MINUTE
    _require(end <= session.closing and end <= decision_at, "future_window")
    rows, issues = [], []
    for index in range(offset, offset + count):
        at = session.opening + index * MINUTE
        item = session.minutes.get(at)
        if item is None:
            issues.append(Issue(session.symbol, at, "missing_minute"))
            continue
        _require(isinstance(item, Minute) and item.symbol == session.symbol and item.start_at == at,
                 "minute_identity_changed")
        _clock(item.start_at)
        _clock(item.available_at)
        _require(at + MINUTE <= item.available_at <= decision_at, "minute_unavailable_at_decision")
        for required, state, payload, name in (
            (price, item.price_state, item.ohlc, "price"),
            (volume, item.volume_state, item.shares, "volume"),
        ):
            if not required:
                continue
            _require(type(state) is str and state in STATES, "invalid_observation_state")
            if state != "observed":
                _require(payload is None, "unknown_observation_has_payload")
                issues.append(Issue(session.symbol, at, f"{name}_{state}"))
                continue
            if name == "price":
                _require(type(payload) is tuple and len(payload) == 4, "observed_price_requires_ohlc")
                _require(all(isinstance(value, Decimal) and value.is_finite() and value > 0
                             for value in payload), "invalid_exact_price")
                op, hi, lo, cl = payload
                _require(lo <= min(op, cl) <= max(op, cl) <= hi, "invalid_ohlc_order")
            else:
                _require(isinstance(payload, Decimal) and payload.is_finite() and payload >= 0,
                         "invalid_exact_shares")
        rows.append(item)
    return Window(tuple(rows), tuple(issues))


def first_h1_parent(session: SessionInputs, *, through: datetime) -> ParentScan:
    """First completed five-bar upside break on the E>=30 five-minute grid.

    Decision is E+2; entry E+5 plus the fixed 60-minute cap must fit before
    close-15. An unknown prefix stops identification permanently for this scan.
    Context, volume and the holding/label suffix cannot choose another parent.
    """
    _session(session)
    _clock(through)
    _require(through >= session.opening, "cutoff_before_opening")
    last_end = session.closing - 80 * MINUTE
    if through < session.opening + 32 * MINUTE or last_end < session.opening + 30 * MINUTE:
        return ParentScan("known_no_parent")
    preceding: list[Bar] = []
    offset = 0
    while session.opening + (offset + 5) * MINUTE <= last_end:
        start = session.opening + offset * MINUTE
        end = start + FIVE
        decision = max(end + 2 * MINUTE, session.opening + 32 * MINUTE)
        if decision > through:
            break
        observed = window(session, offset, 5, decision)
        if observed.issues:
            return ParentScan("parent_identity_unknown", issues=observed.issues)
        prices = tuple(item.ohlc for item in observed.minutes)
        available = max(end + 2 * MINUTE, *(item.available_at for item in observed.minutes))
        bar = Bar(start, end, available, prices[0][0], max(row[1] for row in prices),
                  min(row[2] for row in prices), prices[-1][3])
        if len(preceding) == 5 and bar.close > max(item.high for item in preceding):
            return ParentScan("parent", H1Parent(session.symbol, session.opening, bar, tuple(preceding)))
        preceding.append(bar)
        preceding = preceding[-5:]
        offset += 5
    return ParentScan("known_no_parent")


def _expected(current: SessionInputs, expected: tuple[datetime, ...],
              references: Mapping[datetime, SessionInputs | None]) -> None:
    _session(current)
    _require(type(expected) is tuple and len(expected) == 20, "twenty_expected_sessions_required")
    for opening in expected:
        _clock(opening)
    _require(tuple(sorted(set(expected))) == expected
             and len({opening.date() for opening in expected}) == 20
             and all(opening.date() < current.opening.date() for opening in expected),
             "invalid_expected_prior_calendar")
    _require(isinstance(references, Mapping) and set(references) <= set(expected),
             "reference_outside_expected_calendar")
    for opening in expected:
        prior = references.get(opening)
        if prior is not None:
            _session(prior)
            _require(prior.opening == opening and prior.symbol == current.symbol,
                     "reference_session_identity_changed")


def _median(values: tuple[Fraction, ...]) -> Fraction:
    _require(len(values) == 20, "twenty_observations_required")
    ordered = sorted(values)
    return (ordered[9] + ordered[10]) / 2


def volume_baseline(current: SessionInputs, expected: tuple[datetime, ...],
                    references: Mapping[datetime, SessionInputs | None], *,
                    offset: int, count: int, decision_at: datetime) -> VolumeBaseline:
    """Twenty EXPECTED sessions, never twenty successful observations."""
    _expected(current, expected, references)
    _clock(decision_at)
    _require(decision_at >= current.opening, "decision_before_current_session")
    _require(type(offset) is int and offset >= 0 and type(count) is int and count > 0
             and current.opening + (offset + count) * MINUTE <= current.closing,
             "invalid_window_geometry")
    totals: list[Fraction | None] = []
    issues = []
    for opening in expected:
        prior = references.get(opening)
        if prior is None:
            totals.append(None)
            issues.append(Issue(current.symbol, opening, "missing_expected_session"))
            continue
        observed = window(prior, offset, count, decision_at, price=False, volume=True)
        issues.extend(observed.issues)
        totals.append(None if observed.issues else sum(
            (Fraction(item.shares) for item in observed.minutes), Fraction(0)))
    median = None if issues else _median(tuple(totals))
    if median is not None and median <= 0:
        issues.append(Issue(current.symbol, current.opening + offset * MINUTE,
                            "nonpositive_volume_denominator"))
        median = None
    return VolumeBaseline(expected, tuple(totals), median, tuple(issues))


def _return(rows: tuple[Minute, ...]) -> Fraction:
    return Fraction(rows[-1].ohlc[3]) / Fraction(rows[0].ohlc[0]) - 1


def peer_context(current: SessionInputs, parent: H1Parent, original_peers: tuple[str, ...],
                 peers: Mapping[str, SessionInputs | None]) -> PeerContext:
    """The same fixed common30 subset supplies every minute and both windows."""
    _session(current)
    _require(isinstance(parent, H1Parent), "typed_parent_required")
    _require(type(original_peers) is tuple and all(type(name) is str and name for name in original_peers)
             and tuple(sorted(set(original_peers))) == original_peers
             and current.symbol not in original_peers and bool(original_peers), "invalid_original_peers")
    _require(isinstance(peers, Mapping) and set(peers) <= set(original_peers), "peer_outside_original_set")
    offset = int((parent.trigger.end_at - current.opening) // MINUTE) - 30
    _require(parent.symbol == current.symbol and parent.opening == current.opening
             and offset >= 0, "parent_session_changed")
    common, observed, issues = [], [], []
    for name in original_peers:
        peer = peers.get(name)
        if peer is None:
            issues.append(Issue(name, current.opening, "missing_peer_session"))
            continue
        _session(peer)
        _require(peer.symbol == name and peer.opening == current.opening
                 and peer.closing == current.closing, "peer_session_changed")
        result = window(peer, offset, 30, parent.decision_at)
        issues.extend(result.issues)
        if not result.issues:
            common.append(name)
            observed.append(result.minutes)
    names = tuple(common)
    if len(common) < 5:
        return PeerContext(names, (), None, None, None, tuple(issues) + (
            Issue(current.symbol, parent.trigger.end_at, "fewer_than_five_common_peers"),))
    n = len(common)
    returns = tuple(_return(rows) for rows in observed)
    minute_returns = tuple(sum((Fraction(rows[index].ohlc[3]) / Fraction(rows[index].ohlc[0]) - 1
                                for rows in observed), Fraction(0)) / n for index in range(30))
    return PeerContext(names, minute_returns, sum(returns, Fraction(0)) / n,
                       sum((_return(rows[15:]) for rows in observed), Fraction(0)) / n,
                       Fraction(sum(value > 0 for value in returns), n), tuple(issues))


def _categories(context: OpeningContext, parent: H1Parent) -> tuple[
    tuple[Fraction, ...] | None, tuple[Fraction, ...] | None, int | None,
]:
    _require(isinstance(context, OpeningContext), "typed_opening_context_required")
    _clock(context.opening)
    _require(context.symbol == parent.symbol and context.opening == parent.opening,
             "opening_context_identity_changed")
    _require(type(context.state) is str
             and context.state in {"ELIGIBLE_ORIGINAL", "KNOWN_NO_WHITELISTED_LIST_EVENT", "UNKNOWN"},
             "invalid_event_state")
    _require(type(context.original_types) is tuple and type(context.receipt_ids) is tuple,
             "invalid_event_identity")
    _require(all(type(name) is str and name in EVENT_TYPE_NAMES[1:] for name in context.original_types)
             and len(set(context.original_types)) == len(context.original_types)
             and all(type(value) is str and value for value in context.receipt_ids)
             and len(set(context.receipt_ids)) == len(context.receipt_ids), "invalid_event_identity")
    if context.available_at is not None:
        _clock(context.available_at)
        _require(context.available_at <= context.opening, "context_not_available_at_opening")
    if context.state == "UNKNOWN":
        _require(not context.original_types and context.event_opening is None, "unknown_context_has_event")
        return None, None, None
    _require(context.available_at is not None, "known_context_availability_required")
    if context.state == "KNOWN_NO_WHITELISTED_LIST_EVENT":
        _require(not context.original_types and not context.receipt_ids and context.event_opening is None,
                 "no_event_has_filing")
        return (Fraction(1),) + (Fraction(0),) * 3, (Fraction(1),) + (Fraction(0),) * 10, None
    _clock(context.event_opening)
    _require(context.event_opening == context.opening and bool(context.original_types)
             and bool(context.receipt_ids), "event_not_first_eligible_opening")
    elapsed = parent.decision_at - context.event_opening
    _require(elapsed % MINUTE == timedelta(0), "event_age_not_whole_minutes")
    age = int(elapsed // MINUTE)
    bucket = age // 30
    _require(1 <= bucket <= 10, "event_age_outside_frozen_vocabulary")
    types = tuple(Fraction(int(name in context.original_types)) for name in EVENT_TYPE_NAMES)
    ages = tuple(Fraction(int(index == bucket)) for index in range(11))
    return types, ages, age


def build_h1_window_inputs(current: SessionInputs, parent: H1Parent, *,
                           original_peers: tuple[str, ...], peers: Mapping[str, SessionInputs | None],
                           expected: tuple[datetime, ...], references: Mapping[datetime, SessionInputs | None],
                           opening_context: OpeningContext) -> H1WindowInputs:
    """Build the shared predecision record; no training vocabulary admission."""
    _require(isinstance(parent, H1Parent), "typed_parent_required")
    authenticated = first_h1_parent(current, through=parent.decision_at)
    _require(authenticated.status == "parent" and authenticated.parent == parent,
             "not_the_first_causal_parent")
    _expected(current, expected, references)
    types, ages, age = _categories(opening_context, parent)
    end_offset = int((parent.trigger.end_at - current.opening) // MINUTE)
    offset = end_offset - 30
    target = window(current, offset, 30, parent.decision_at, volume=True)
    market = peer_context(current, parent, original_peers, peers)
    issues = list(target.issues)
    # Excluded peers remain diagnostics; >=5 complete peers establish the proxy.
    if market.return30 is None:
        issues.extend(market.issues)
    prior_rows = []
    for opening in expected:
        prior = references.get(opening)
        if prior is None:
            issues.append(Issue(current.symbol, opening, "missing_expected_session"))
            continue
        observed = window(prior, offset, 30, parent.decision_at, volume=True)
        issues.extend(observed.issues)
        prior_rows.append(observed.minutes)
    if issues:
        return H1WindowInputs(parent, market.common_peers, None, (), None, opening_context,
                              types, ages, age, tuple(issues))
    denominators = tuple(_median(tuple(Fraction(rows[index].shares) for rows in prior_rows))
                         for index in range(30))
    last5_median = _median(tuple(sum((Fraction(item.shares) for item in rows[-5:]), Fraction(0))
                                for rows in prior_rows))
    if any(value <= 0 for value in denominators) or last5_median <= 0:
        return H1WindowInputs(parent, market.common_peers, None, (), None, opening_context, types, ages, age, (
            Issue(current.symbol, parent.trigger.end_at, "nonpositive_volume_denominator"),))
    dynamic = []
    for index, item in enumerate(target.minutes):
        op, hi, lo, cl = map(Fraction, item.ohlc)
        zero = hi == lo
        dynamic.append((cl / op - 1, (hi - lo) / op,
                        Fraction(0) if zero else (cl - lo) / (hi - lo), Fraction(int(zero)),
                        Fraction(item.shares) / denominators[index], market.minute_returns[index]))
    prior_returns = tuple(_return(rows) for rows in prior_rows)
    mean = sum(prior_returns, Fraction(0)) / 20
    variance = sum(((value - mean) ** 2 for value in prior_returns), Fraction(0)) / 20
    target_return = _return(target.minutes)
    static = (
        ("target_30m_return", target_return),
        ("peer_30m_return", market.return30),
        ("peer_last15_return", market.last15_return),
        ("peer_positive_30m_fraction", market.positive_fraction),
        ("target_minus_peer_30m_return", target_return - market.return30),
        ("last5_share_volume_ratio", sum((Fraction(item.shares) for item in target.minutes[-5:]), Fraction(0)) / last5_median),
        ("fraction_of_session_elapsed", Fraction(
            (parent.decision_at - current.opening) // timedelta(microseconds=1),
            (current.closing - current.opening) // timedelta(microseconds=1))),
        ("structural_stop_gap_from_trigger_close", (Fraction(parent.trigger.close) - Fraction(parent.structural_stop)) / Fraction(parent.trigger.close)),
        ("common_peer_count_over_original_peer_count", Fraction(len(market.common_peers), len(original_peers))),
        ("past20_sameclock5_share_volume_median", last5_median),
    )
    context_issues = () if types is not None else (Issue(current.symbol, current.opening, "opening_context_unknown"),)
    return H1WindowInputs(parent, market.common_peers, tuple(dynamic), static, variance,
                          opening_context, types, ages, age, context_issues)


def _binary64(value: Fraction) -> float:
    _require(isinstance(value, Fraction), "exact_fraction_required_at_numeric_boundary")
    try:
        number = float(value)
    except (OverflowError, ValueError) as error:
        raise InputRefusal("nonfinite_float64") from error
    _require(isfinite(number), "nonfinite_float64")
    return number


def flatten_h1_features(value: H1WindowInputs) -> tuple[float, ...] | None:
    """206 ordered float64 inputs; retain the caller's exact record separately.

    No event-type training-coverage filter belongs here: a known fixed-vocabulary
    type remains a fully observed input even when unseen by a particular fold.
    """
    _require(isinstance(value, H1WindowInputs), "typed_h1_inputs_required")
    types, ages, age = _categories(value.opening_context, value.parent)
    _require((types, ages, age) == (value.event_type_bits, value.event_age_bits, value.event_age_minutes),
             "event_encoding_changed")
    if value.issues or value.dynamic is None or types is None:
        return None
    _require(type(value.dynamic) is tuple and len(value.dynamic) == 30
             and all(type(row) is tuple and len(row) == 6 for row in value.dynamic), "invalid_tensor_shape")
    _require(type(value.static_exact) is tuple
             and tuple(name for name, _ in value.static_exact) == _EXACT_STATIC_NAMES,
             "static_numeric_order_changed")
    _require(isinstance(value.past20_return_variance, Fraction) and value.past20_return_variance >= 0,
             "invalid_exact_variance")
    # Check the exact variance's float64 representation before sqrt. Zero
    # variance is an observed zero; missing variance never reaches this boundary.
    volatility = sqrt(_binary64(value.past20_return_variance))
    _require(isfinite(volatility), "nonfinite_float64_volatility")
    static = tuple(_binary64(number) for _, number in value.static_exact)
    result = tuple(_binary64(number) for row in value.dynamic for number in row)
    result += static[:6] + (volatility,) + static[6:]
    result += tuple(_binary64(number) for number in types + ages)
    _require(len(result) == len(FEATURE_NAMES) == 206, "feature_schema_changed")
    return result


def h1_rule_decision(value: H1WindowInputs, *, without_filing: bool = False) -> Literal["accepted", "declined", "unknown"]:
    """Both controls require the identical full support, with exact thresholds."""
    _require(type(without_filing) is bool, "invalid_rule_variant")
    if flatten_h1_features(value) is None:
        return "unknown"
    static = dict(value.static_exact)
    accepted = (without_filing or value.opening_context.state == "ELIGIBLE_ORIGINAL")
    accepted = accepted and static["target_minus_peer_30m_return"] > 0 and static["last5_share_volume_ratio"] > 1
    return "accepted" if accepted else "declined"
