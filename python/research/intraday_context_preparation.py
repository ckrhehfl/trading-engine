"""EA preparation: fixed context on existing H1/H3, with no IO or runner.

All inputs are caller-authenticated snapshots, never historical-availability
certification. The only outcome helper explicitly accepts invented fixtures.
Nothing here authorizes actual access, an experiment, trading or promotion.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import re

from research import intraday_autonomous_signals as signals
from research import intraday_pattern_events as core


MINUTE = timedelta(minutes=1)
FIVE = 5 * MINUTE
KST = timezone(timedelta(hours=9))
SETUPS = ("EA-A", "EA-B")
# Canonical sorted-key JSON, UTF-8, compact separators. Includes all prose.
REGISTERED_CANONICAL_SHA256 = "bf3d240cf5d9778df0fec8246e023119241b51f3fa52ff133f791a236c88860c"


class PreparationRefusal(ValueError):
    """Invalid contracts raise; absent observations remain explicit unknowns."""


def validate_spec(spec: object) -> None:
    """Authenticate the preparation contract only; never read an input path."""
    try:
        encoded = json.dumps(spec, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise PreparationRefusal("invalid_registration_json") from None
    if hashlib.sha256(encoded).hexdigest() != REGISTERED_CANONICAL_SHA256:
        raise PreparationRefusal("preparation_registration_changed")


@dataclass(frozen=True)
class Provenance:
    observation_end: datetime
    available_at: datetime
    retrieved_at: datetime
    source: str
    is_final: bool | None
    data_vintage: str
    evidence_level: str


@dataclass(frozen=True)
class PriceSource:
    """Prefix source identity; each core.Bar still supplies end+2 availability."""

    retrieved_at: datetime
    source: str
    is_final: bool | None
    data_vintage: str
    evidence_level: str


@dataclass(frozen=True)
class Target:
    symbol: str
    session_date: date
    policy: str
    opening: datetime
    closing: datetime
    price_basis: str
    price_source: PriceSource


@dataclass(frozen=True)
class MonthlyUniverse:
    month: date
    members: tuple[str, ...]
    fixed_at: datetime
    provenance: Provenance


@dataclass(frozen=True)
class PriceWindow:
    """Authenticated exact window OPEN/CLOSE aggregate, not an endpoint search."""

    symbol: str
    session_date: date
    start_at: datetime
    end_at: datetime
    opening_price: Decimal
    closing_price: Decimal
    provenance: Provenance
    price_basis: str
    unit: str = "KRW"


@dataclass(frozen=True)
class VolumeMinute:
    start_at: datetime
    shares: Decimal
    provenance: Provenance


@dataclass(frozen=True)
class VolumeWindow:
    symbol: str
    session_date: date
    opening: datetime
    minutes: tuple[VolumeMinute, ...]
    comparable_share_basis: str
    unit: str = "shares"


@dataclass(frozen=True)
class VolumeInputs:
    """The expected calendar dates are fixed by the caller, not observed rows."""

    expected_reference_dates: tuple[date, ...]
    expected_reference_openings: Mapping[date, datetime]
    current: VolumeWindow | None
    references: Mapping[date, VolumeWindow | None]


@dataclass(frozen=True)
class Feature:
    value: Fraction | None
    reasons: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()


@dataclass(frozen=True)
class PreparedCandidate:
    setup_id: str
    target: Target
    basic_candidate: signals.Detection
    status: str
    arms: Mapping[str, str]
    features: Mapping[str, Feature]
    fixed_peers: tuple[str, ...]
    structural_stop: Fraction | None
    entry_at: datetime | None
    cap_at: datetime | None
    common_context_observable: bool
    universe_provenance: Provenance
    actual_execution_authorized: bool = False
    historical_timing_certified: bool = False
    promotion_allowed: bool = False


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise PreparationRefusal(reason)


def _aware(value: object) -> None:
    _require(isinstance(value, datetime) and value.utcoffset() is not None,
             "aware_clock_required")


def _symbol(value: object) -> None:
    _require(isinstance(value, str) and re.fullmatch(r"KRX:[0-9A-Z]{6}", value) is not None,
             "invalid_symbol")


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _decimal(value: object, *, zero: bool = False) -> Fraction:
    _require(isinstance(value, Decimal) and value.is_finite()
             and (value >= 0 if zero else value > 0), "invalid_exact_value")
    return Fraction(value)


def _metadata(meta: Provenance, end: datetime, decision: datetime) -> None:
    _require(isinstance(meta, Provenance), "provenance_required")
    for clock in (meta.observation_end, meta.available_at, meta.retrieved_at):
        _aware(clock)
    _require(meta.observation_end == end, "observation_window_changed")
    _require(end <= meta.available_at <= decision, "unavailable_or_invalid_source_clock")
    _require(meta.retrieved_at >= end, "retrieval_precedes_observation")
    _require(_text(meta.source) and _text(meta.data_vintage), "source_and_vintage_required")
    _require(meta.is_final is None or type(meta.is_final) is bool, "invalid_finality")
    _require(isinstance(meta.evidence_level, str)
             and meta.evidence_level in {"synthetic", "observed", "assumed", "unknown"},
             "invalid_evidence_level")


def _target(target: Target) -> None:
    _require(isinstance(target, Target), "target_required")
    _symbol(target.symbol)
    _require(type(target.session_date) is date and isinstance(target.policy, str)
             and target.policy in {"D+1", "D+2"},
             "invalid_target_date_or_policy")
    _aware(target.opening)
    _aware(target.closing)
    _require(target.opening.astimezone(KST).date() == target.session_date
             and target.closing.astimezone(KST).date() == target.session_date
             and target.opening < target.closing
             and (target.closing - target.opening) % FIVE == timedelta(0)
             and target.opening.second == target.opening.microsecond == 0,
             "invalid_target_session_clock")
    _require(_text(target.price_basis), "price_basis_required")
    source = target.price_source
    _require(isinstance(source, PriceSource), "price_source_required")
    _aware(source.retrieved_at)
    _require(_text(source.source) and _text(source.data_vintage)
             and (source.is_final is None or type(source.is_final) is bool)
             and isinstance(source.evidence_level, str)
             and source.evidence_level in {"synthetic", "observed", "assumed", "unknown"},
             "invalid_price_source")


def _peers(universe: MonthlyUniverse, target: Target) -> tuple[str, ...]:
    _require(isinstance(universe, MonthlyUniverse), "fixed_universe_required")
    _require(type(universe.month) is date
             and universe.month == target.session_date.replace(day=1), "universe_month_changed")
    _require(type(universe.members) is tuple, "fixed_members_must_be_tuple")
    for member in universe.members:
        _symbol(member)
    _require(len(set(universe.members)) == len(universe.members), "duplicate_member")
    _require(target.symbol in universe.members, "target_outside_fixed_universe")
    _aware(universe.fixed_at)
    _require(universe.fixed_at <= target.opening, "universe_fixed_after_open")
    _require(isinstance(universe.provenance, Provenance), "provenance_required")
    _aware(universe.provenance.observation_end)
    _require(universe.provenance.observation_end.astimezone(KST).date() < universe.month,
             "monthly_formation_observation_must_precede_month")
    _metadata(universe.provenance, universe.provenance.observation_end, universe.fixed_at)
    return tuple(sorted(member for member in universe.members if member != target.symbol))


def _volume_total(window: VolumeWindow, symbol: str, day: date,
                  expected_opening: datetime, decision: datetime) -> Feature:
    _require(isinstance(window, VolumeWindow), "invalid_volume_window")
    _require(window.symbol == symbol and type(window.session_date) is date
             and window.session_date == day, "volume_identity_changed")
    _aware(window.opening)
    _require(window.opening == expected_opening and window.opening.astimezone(KST).date() == day
             and window.opening.second == window.opening.microsecond == 0,
             "volume_session_clock_changed")
    _require(window.unit == "shares" and _text(window.comparable_share_basis),
             "invalid_volume_unit_or_basis")
    _require(type(window.minutes) is tuple and len(window.minutes) <= 60,
             "invalid_volume_minute_count")
    total, provenance = Fraction(0), []
    for index, minute in enumerate(window.minutes):
        _require(isinstance(minute, VolumeMinute), "invalid_volume_minute")
        expected = window.opening + index * MINUTE
        _require(minute.start_at == expected, "volume_minute_gap_or_duplicate")
        _metadata(minute.provenance, expected + MINUTE, decision)
        total += _decimal(minute.shares, zero=True)
        provenance.append(minute.provenance)
    if len(window.minutes) != 60:
        return Feature(None, ("incomplete_volume_window",), tuple(provenance))
    return Feature(total, provenance=tuple(provenance))


def _immutable_volume(window: VolumeWindow) -> bool:
    """Only the declared frozen tree is eligible for identity memoization.

    Frozen dataclasses may still be given mutable subclasses or mutable tzinfo
    objects. Those valid legacy inputs use the uncached validator instead.
    """
    def clock(value: object) -> bool:
        return type(value) is datetime and type(value.tzinfo) is timezone

    if not (type(window) is VolumeWindow and type(window.minutes) is tuple
            and type(window.session_date) is date and clock(window.opening)
            and all(type(value) is str for value in
                    (window.symbol, window.comparable_share_basis, window.unit))):
        return False
    for minute in window.minutes:
        if not (type(minute) is VolumeMinute and clock(minute.start_at)
                and type(minute.shares) is Decimal and type(minute.provenance) is Provenance):
            return False
        meta = minute.provenance
        if not (all(clock(value) for value in
                    (meta.observation_end, meta.available_at, meta.retrieved_at))
                and all(type(value) is str for value in
                        (meta.source, meta.data_vintage, meta.evidence_level))
                and (meta.is_final is None or type(meta.is_final) is bool)):
            return False
    return True


class VolumeValidationCache:
    """Bounded, per-build reuse of successful immutable window validation.

    An entry retains the exact window object, so id reuse cannot hit. A later
    decision can reuse its availability checks; an earlier decision, changed
    symbol/date/opening or a different window always uses the full validator.
    Values cannot be supplied by the caller. Never share across experiment runs.
    """

    def __init__(self) -> None:
        self._entries: OrderedDict[tuple, tuple[VolumeWindow, datetime, Feature]] = OrderedDict()

    def total(self, window: VolumeWindow, symbol: str, day: date,
              expected_opening: datetime, decision: datetime) -> Feature:
        if not (type(window) is VolumeWindow and type(symbol) is str and type(day) is date
                and type(expected_opening) is datetime and type(expected_opening.tzinfo) is timezone
                and type(decision) is datetime and type(decision.tzinfo) is timezone):
            return _volume_total(window, symbol, day, expected_opening, decision)
        key = (id(window), symbol, day, expected_opening)
        saved = self._entries.get(key)
        if saved is not None and saved[0] is window and decision >= saved[1]:
            self._entries.move_to_end(key)
            return saved[2]
        result = _volume_total(window, symbol, day, expected_opening, decision)
        if _immutable_volume(window):
            self._entries[key] = (window, decision, result)
            self._entries.move_to_end(key)
            if len(self._entries) > 512:
                self._entries.popitem(last=False)
        return result


def relative_volume(inputs: VolumeInputs | None, target: Target, decision: datetime,
                    *, validation_cache: VolumeValidationCache | None = None) -> Feature:
    """Current first60 / exact median of the twenty supplied expected sessions."""
    _target(target)
    _aware(decision)
    _require(validation_cache is None or type(validation_cache) is VolumeValidationCache,
             "invalid_volume_validation_cache")
    if inputs is None:
        return Feature(None, ("volume_inputs_missing",))
    _require(isinstance(inputs, VolumeInputs), "invalid_volume_inputs")
    dates = inputs.expected_reference_dates
    _require(type(dates) is tuple and len(dates) == 20
             and all(type(day) is date and day < target.session_date for day in dates)
             and len(set(dates)) == 20 and tuple(sorted(dates)) == dates,
             "exact_twenty_prior_reference_dates_required")
    _require(isinstance(inputs.references, Mapping)
             and all(type(day) is date for day in inputs.references)
             and set(inputs.references) <= set(dates), "unexpected_reference_date")
    _require(isinstance(inputs.expected_reference_openings, Mapping)
             and set(inputs.expected_reference_openings) == set(dates),
             "exact_reference_calendar_required")
    for day, opening in inputs.expected_reference_openings.items():
        _aware(opening)
        _require(opening.astimezone(KST).date() == day
                 and opening.second == opening.microsecond == 0, "invalid_reference_opening")
    windows = [inputs.current] + [inputs.references.get(day) for day in dates]
    totals, provenance, reasons, bases = [], [], [], set()
    for day, window in zip((target.session_date,) + dates, windows):
        if window is None:
            reasons.append("missing_volume_reference" if day != target.session_date else "missing_current_volume")
            continue
        expected_opening = target.opening if day == target.session_date else inputs.expected_reference_openings[day]
        value = (_volume_total(window, target.symbol, day, expected_opening, decision)
                 if validation_cache is None else validation_cache.total(
                     window, target.symbol, day, expected_opening, decision))
        if day == target.session_date:
            _require(window.opening == target.opening, "current_volume_open_changed")
        bases.add(window.comparable_share_basis)
        provenance.extend(value.provenance)
        reasons.extend(value.reasons)
        totals.append(value.value)
    _require(len(bases) <= 1, "incomparable_share_units")
    if reasons:
        return Feature(None, tuple(sorted(set(reasons))), tuple(provenance))
    ordered = sorted(totals[1:])
    median = (ordered[9] + ordered[10]) / 2
    if median <= 0:
        return Feature(None, ("nonpositive_reference_median",), tuple(provenance))
    return Feature(totals[0] / median, provenance=tuple(provenance))


def _peer_mean(windows: Mapping[str, PriceWindow | None], peers: tuple[str, ...],
               target: Target, start: datetime, end: datetime, decision: datetime) -> Feature:
    _require(isinstance(windows, Mapping) and set(windows) <= set(peers),
             "peer_inputs_outside_fixed_basket")
    values, reasons, provenance = [], [], []
    if len(peers) < 10:
        reasons.append("fewer_than_ten_fixed_peers")
    for symbol in peers:
        window = windows.get(symbol)
        if window is None:
            reasons.append("incomplete_fixed_peer_basket")
            continue
        _require(isinstance(window, PriceWindow), "invalid_peer_price_window")
        _require(window.symbol == symbol and type(window.session_date) is date
                 and window.session_date == target.session_date, "peer_identity_changed")
        _require(window.start_at == start and window.end_at == end,
                 "peer_price_window_changed")
        _require(window.unit == "KRW" and _text(window.price_basis), "invalid_peer_price_unit")
        _metadata(window.provenance, end, decision)
        values.append(_decimal(window.closing_price) / _decimal(window.opening_price) - 1)
        provenance.append(window.provenance)
    if reasons:
        return Feature(None, tuple(sorted(set(reasons))), tuple(provenance))
    return Feature(sum(values, Fraction(0)) / len(peers), provenance=tuple(provenance))


def _arm(*conditions: tuple[Feature, bool]) -> str:
    if any(feature.value is None for feature, _ in conditions):
        return "unknown"
    return "selected" if all(passed for _, passed in conditions) else "rejected"


def prepare_candidate(setup_id: str, bars: Iterable[core.Bar], *, target: Target,
                      universe: MonthlyUniverse,
                      first_window_peers: Mapping[str, PriceWindow | None],
                      last15_peers: Mapping[str, PriceWindow | None],
                      volume: VolumeInputs | None = None,
                      volume_validation_cache: VolumeValidationCache | None = None) -> PreparedCandidate:
    """First H1/H3 candidate only; never retry after unknown/rejected context.

    EA-A first_window_peers is open-to-signal-end; EA-B is opening thirty
    minutes. Both require exact last15 ending at the original signal end.
    The fixed basket is every supplied original member excluding the target.
    Caller authenticates monthly eligibility and comparable price/share units.
    """
    _require(setup_id in SETUPS, "unregistered_setup")
    _target(target)
    peers = _peers(universe, target)
    consumed = []

    def remember():
        for bar in bars:
            consumed.append(bar)
            yield bar

    detection = signals.detect_bars(signals.HYPOTHESES[0 if setup_id == "EA-A" else 2],
                                   remember(), opening=target.opening, closing=target.closing)
    _require(detection.observed_through is None
             or target.price_source.retrieved_at >= detection.observed_through,
             "price_retrieval_precedes_observation")
    arm_names = ("basic", "volume_only", "market_relative_only", "both") if setup_id == "EA-A" else (
        "basic", "market_context")
    if detection.state != "signal":
        status = "unknown" if detection.state == "unknown" else "rejected"
        return PreparedCandidate(setup_id, target, detection, status,
                                 dict.fromkeys(arm_names, status), {}, peers, None, None, None,
                                 False, universe.provenance)
    end, decision = detection.signal_bar_end, detection.decision_at
    stop_start = detection.setup_bar_end - (2 * FIVE if setup_id == "EA-A" else FIVE)
    stop = min(Fraction(bar.low) for bar in consumed if bar.start_at >= stop_start)
    entry = target.opening + ((decision - target.opening) // FIVE + 1) * FIVE
    cap = entry + 60 * MINUTE
    if cap >= target.closing:
        return PreparedCandidate(setup_id, target, detection, "unknown",
                                 dict.fromkeys(arm_names, "unknown"),
                                 {"clock": Feature(None, ("same_day_cap_infeasible",))},
                                 peers, stop, entry, cap, False, universe.provenance)
    first_end = end if setup_id == "EA-A" else target.opening + 30 * MINUTE
    first = _peer_mean(first_window_peers, peers, target, target.opening, first_end, decision)
    last = _peer_mean(last15_peers, peers, target, end - 15 * MINUTE, end, decision)
    last_condition = (last, last.value is not None and last.value >= 0)
    if setup_id == "EA-A":
        stock_return = Fraction(consumed[-1].close) / Fraction(consumed[0].open) - 1
        relative = Feature(stock_return - first.value if first.value is not None else None,
                           first.reasons, first.provenance)
        activity = relative_volume(volume, target, decision, validation_cache=volume_validation_cache)
        relative_condition = (relative, relative.value is not None and relative.value > 0)
        volume_condition = (activity, activity.value is not None and activity.value >= 2)
        features = {"relative_volume": activity, "stock_minus_peer_return": relative,
                    "peer_open_to_signal_return": first, "peer_last15_return": last}
        arms = {"basic": "selected", "volume_only": _arm(volume_condition),
                "market_relative_only": _arm(relative_condition, last_condition),
                "both": _arm(volume_condition, relative_condition, last_condition)}
    else:
        features = {"peer_first30_return": first, "peer_last15_return": last}
        arms = {"basic": "selected", "market_context": _arm(
            (first, first.value is not None and first.value <= Fraction(-5, 1000)), last_condition)}
    return PreparedCandidate(setup_id, target, detection, arms[arm_names[-1]], arms, features,
                             peers, stop, entry, cap,
                             all(feature.value is not None for feature in features.values()),
                             universe.provenance)


def synthetic_outcome(candidate: PreparedCandidate,
                      minutes: Mapping[datetime, tuple[Decimal, Decimal, Decimal, Decimal]],
                      *, synthetic: bool) -> dict:
    """Invented-fixture label, shared by all arms, without actual fill claims.

    A CLOSED minute at/below the structural stop sends an assumed order three
    minutes after its end (two observation plus one order). Fill is that minute's
    OPEN, or the predecided +60 cap. Missing required path/fill is unknown; a low
    touch alone is not a stop. Entry eligibility and the chosen exit consume
    only OPEN. Completed held minutes validate OHLC; exit-minute HLC is unread.
    """
    _require(synthetic is True, "invented_fixtures_only")
    return _price_outcome(candidate, minutes, evidence_level="synthetic", error_context="synthetic")


def _price_outcome(candidate: PreparedCandidate,
                   minutes: Mapping[datetime, tuple[Decimal, Decimal, Decimal, Decimal] | None],
                   *, evidence_level: str, error_context: str,
                   retrieved_at: datetime | None = None,
                   opening_prices: Mapping[datetime, Decimal | None] | None = None) -> dict:
    """Common exact label arithmetic; public wrappers own evidence boundaries."""
    _require(isinstance(candidate, PreparedCandidate) and isinstance(minutes, Mapping),
             f"invalid_{error_context}_inputs")
    _require(opening_prices is None or isinstance(opening_prices, Mapping),
             "invalid_opening_price_inputs")
    _target(candidate.target)
    _require(candidate.setup_id in SETUPS and candidate.basic_candidate.state == "signal",
             "price_candidate_required")
    entry, cap, stop = candidate.entry_at, candidate.cap_at, candidate.structural_stop
    decision = candidate.basic_candidate.decision_at
    expected_entry = candidate.target.opening + ((decision - candidate.target.opening) // FIVE + 1) * FIVE
    _require(entry == expected_entry and cap == entry + 60 * MINUTE,
             f"{error_context}_candidate_clock_changed")
    result = {"status": "unknown", "reason": None, "entry_at": entry, "exit_at": None,
              "structural_stop": stop, "entry_open": None, "exit_open": None,
              "stop_observation_end": None, "gross": None, "primary_net": None,
              "stress_net": None, "primary_cost_bps": 33, "stress_cost_bps": 43,
              "evidence_level": evidence_level, "actual_fill_certified": False,
              "actual_execution_authorized": False, "promotion_allowed": False}
    if not candidate.target.opening < decision < entry < cap < candidate.target.closing:
        return {**result, "reason": "same_day_cap_infeasible"}
    if not isinstance(stop, Fraction) or stop <= 0:
        return {**result, "reason": "structural_stop_missing"}

    def bucket(at):
        value = minutes.get(at)
        if value is None:
            return None
        _require(type(value) is tuple and len(value) == 4, "invalid_minute_prices")
        return value

    def opening_price(at):
        value = bucket(at)
        if opening_prices is not None:
            raw_open = opening_prices.get(at)
            if raw_open is None:
                return None
            op = _decimal(raw_open)
            if value is not None and op != _decimal(value[0]):
                return None
            if retrieved_at is not None:
                _require(retrieved_at >= at, "outcome_retrieval_precedes_observation")
            return op
        if value is not None and retrieved_at is not None:
            _require(retrieved_at >= at, "outcome_retrieval_precedes_observation")
        return None if value is None else _decimal(value[0])

    def closed_prices(at):
        value = bucket(at)
        if value is None:
            return None
        if retrieved_at is not None:
            _require(retrieved_at >= at + MINUTE, "outcome_retrieval_precedes_observation")
        op, hi, lo, cl = (_decimal(item) for item in value)
        _require(lo <= min(op, cl) <= max(op, cl) <= hi, "invalid_minute_ohlc")
        if opening_prices is not None:
            raw_open = opening_prices.get(at)
            if raw_open is None or _decimal(raw_open) != op:
                return None
        return op, hi, lo, cl

    first = opening_price(entry)
    if first is None:
        return {**result, "reason": "entry_open_missing"}
    result["entry_open"] = first
    if first <= stop:
        return {**result, "status": "declined", "reason": "invalidated_entry"}
    exit_at, reason = cap, "time_cap"
    minute = entry
    while minute < exit_at:
        value = closed_prices(minute)
        if value is None:
            return {**result, "reason": "required_path_missing"}
        if result["stop_observation_end"] is None and value[3] <= stop:
            observed_end = minute + MINUTE
            stop_fill = observed_end + 3 * MINUTE
            if stop_fill <= cap:
                result["stop_observation_end"] = observed_end
                exit_at = stop_fill
                reason = "delayed_structural_stop" if stop_fill < cap else "stop_at_time_cap"
        minute += MINUTE
    final = opening_price(exit_at)
    if final is None:
        return {**result, "exit_at": exit_at, "reason": "exit_open_missing"}
    gross = final / first - 1
    return {**result, "status": "known", "reason": reason, "exit_at": exit_at,
            "exit_open": final, "gross": gross,
            "primary_net": gross - Fraction(33, 10000),
            "stress_net": gross - Fraction(43, 10000)}
