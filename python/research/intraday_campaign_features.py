"""Pure EC common-observed-peer feature preparation, with no IO or runner.

This NEW limited peer proxy does not amend EA/EB's complete-peer definition.
The caller authenticates the full original day before candidate retention,
including empty sessions and source receipts. Retrieval after observation is
not historical availability certification. No detector, outcome or volume is
consumed here; this source preparation authorizes no actual research execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
import re
from types import MappingProxyType

from research import intraday_context_experiment as eb


ea, cc = eb.ea, eb.cc
PROXY_ID = "EC-common-observed-peer-min5"
MINIMUM_COMMON_PEERS = 5
WINDOWS = ("first30", "open_to_signal", "last15")
FEATURE_NAMES = (
    "setup_is_A", "fraction_of_session_elapsed", "target_open_to_signal_return",
    "target_last15_return", "structural_stop_gap_from_signal_close",
    "trigger_close_location", "peer_open_to_signal_return", "peer_last15_return",
    "peer_first30_return", "common_peer_count_divided_by_original_peer_count",
)


class FeaturesRefusal(ValueError):
    """Broken source/type/clock contracts never become feature missingness."""


@dataclass(frozen=True)
class WindowObservation:
    symbol: str
    name: str
    start_at: datetime
    end_at: datetime
    provenance: ea.Provenance
    value: Fraction | None
    reasons: tuple[str, ...]
    minute_issues: tuple[tuple[datetime, str], ...]


@dataclass(frozen=True)
class PreparedFeatures:
    proxy_id: str
    setup_id: str
    target: ea.Target
    observed_end: datetime
    decision_at: datetime
    features: Mapping[str, ea.Feature]
    fixed_peers: tuple[str, ...]
    common_peers: tuple[str, ...]
    observed_peers_by_window: Mapping[str, tuple[str, ...]]
    windows: tuple[WindowObservation, ...]
    source_evidence: Mapping[str, Mapping]
    universe_provenance: ea.Provenance
    authentication_reference: str
    historical_timing_certified: bool = False
    official_index: bool = False
    actual_execution_authorized: bool = False


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise FeaturesRefusal(reason)


def _clock(value: object, *, kst: bool = True) -> None:
    _require(type(value) is datetime and value.utcoffset() is not None,
             "aware_clock_required")
    if kst:
        _require(value.utcoffset() == timedelta(hours=9), "kst_session_clock_required")


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _source(source: object, symbol: str, target: ea.Target) -> Mapping:
    _require(isinstance(source, Mapping), "source_receipt_required")
    fields = {"session", "symbol", "data_vintage", "raw_chunk_metadata", "input_rows",
              "row_linkage_sha256", "source_row_metadata_location"}
    _require(set(source) == fields and source["symbol"] == symbol
             and source["session"] == target.session_date.isoformat(),
             "source_identity_or_schema_changed")
    _require(all(type(source[key]) is str and re.fullmatch(r"[0-9a-f]{64}", source[key])
                 for key in ("data_vintage", "row_linkage_sha256")), "invalid_source_digest")
    _require(type(source["input_rows"]) is int and 0 <= source["input_rows"] <= 600
             and source["source_row_metadata_location"] == "original_pinned_archive_chunk",
             "invalid_source_receipt")
    meta = source["raw_chunk_metadata"]
    _require(isinstance(meta, Mapping) and set(meta) == set(eb.SOURCE_FIELDS)
             and meta["observation_date"] == source["session"]
             and meta["available_at"] is None and meta["source"] == "collector klines"
             and meta["is_final"] is None and meta["evidence_level"] == "assumed",
             "source_metadata_changed")
    _require(type(meta["retrieved_at"]) is str, "invalid_source_retrieval")
    try:
        retrieved = datetime.fromisoformat(meta["retrieved_at"])
    except ValueError:
        raise FeaturesRefusal("invalid_source_retrieval") from None
    _clock(retrieved, kst=False)
    return source


def _session(session: object, target: ea.Target) -> None:
    if session is None:
        return
    _require(type(session) is cc.Session and isinstance(session.minutes, Mapping)
             and type(session.gaps) is tuple, "invalid_session_type")
    _clock(session.opening)
    _require(session.opening == target.opening, "session_opening_changed")
    # Authenticate only clock geometry in the suffix, never its OHLC or prefix.
    for at in session.minutes:
        _clock(at)
        _require(target.opening <= at < target.closing
                 and (at - target.opening) % cc.MINUTE == timedelta(0),
                 "invalid_session_minute_clock")


def _window(session: cc.Session | None, source: Mapping, symbol: str, name: str,
            start: datetime, end: datetime, target: ea.Target,
            decision: datetime) -> tuple[WindowObservation, ea.PriceWindow | None]:
    _require(target.opening <= start < end <= target.closing
             and (end - start) % cc.MINUTE == timedelta(0), "invalid_feature_window")
    _require(end + 2 * cc.MINUTE <= decision, "future_or_late_available_window")
    meta = eb._provenance(source, end)
    _require(meta.retrieved_at >= end, "retrieval_precedes_observation")
    issues = []
    if session is None:
        issues.append((start, "session_missing"))
    else:
        _require(type(session) is cc.Session and isinstance(session.minutes, Mapping),
                 "invalid_session_type")
        _clock(session.opening)
        _require(session.opening == target.opening, "session_opening_changed")
        for offset in range(int((end - start) / cc.MINUTE)):
            at = start + offset * cc.MINUTE
            values = session.minutes.get(at)
            if values is None:
                # Preserve the parser's missing/conflict/invalid-price distinction
                # only for required minutes; future suffix diagnostics are unused.
                reason = "missing"
                for gap in session.gaps:
                    _require(isinstance(gap, Mapping) and {"start", "end", "state"} <= set(gap),
                             "invalid_gap_diagnostic")
                    _clock(gap["start"])
                    _clock(gap["end"])
                    if gap["start"] <= at < gap["end"]:
                        reason = gap["state"]
                        _require(reason in {"missing", "conflict", "invalid_price"},
                                 "invalid_gap_reason")
                        break
                issues.append((at, reason))
                continue
            _require(type(values) is tuple and len(values) == 4
                     and all(type(price) is Decimal for price in values),
                     "exact_decimal_ohlc_required")
            if not all(price.is_finite() and price > 0 for price in values):
                issues.append((at, "invalid_price"))
            elif not values[2] <= min(values[0], values[3]) <= max(values[0], values[3]) <= values[1]:
                issues.append((at, "invalid_price"))
    reasons = tuple(sorted({reason for _, reason in issues}))
    window = None if issues else eb._window(session, source, symbol, start, end)
    value = (Fraction(window.closing_price) / Fraction(window.opening_price) - 1
             if window is not None else None)
    return WindowObservation(symbol, name, start, end, meta, value, reasons, tuple(issues)), window


def prepare_features(target: ea.Target, universe: ea.MonthlyUniverse,
                     sessions: Mapping[str, cc.Session | None], sources: Mapping[str, Mapping],
                     *, setup_id: str, observed_end: datetime, decision_at: datetime,
                     structural_stop: Fraction, inputs_authenticated: bool,
                     authentication_reference: str, price_unit: str = "KRW") -> PreparedFeatures:
    """Prepare ten separate causal values from one authenticated original day.

    Every original universe member must have a session slot (None represents
    genuinely absent prices) and its original EB-shaped source receipt. Extra
    original day symbols are ignored. No outcome-completeness mask is accepted.
    The same common observed peer intersection supplies all three equal means.
    """
    _require(inputs_authenticated is True, "caller_authenticated_full_original_day_required")
    _require(type(authentication_reference) is str and bool(authentication_reference.strip()),
             "authentication_reference_required")
    _require(type(setup_id) is str and setup_id in ea.SETUPS, "unregistered_setup")
    _require(type(target) is ea.Target and type(universe) is ea.MonthlyUniverse,
             "target_and_fixed_universe_required")
    try:
        ea._target(target)
        peers = ea._peers(universe, target)
    except ea.PreparationRefusal as error:
        raise FeaturesRefusal(str(error)) from None
    _clock(target.opening)
    _clock(target.closing)
    _clock(observed_end)
    _clock(decision_at)
    _require(target.opening == cc.session_open(target.session_date.isoformat())
             and target.closing == cc.session_close(target.session_date.isoformat()),
             "original_session_clock_changed")
    _require(target.opening < observed_end < decision_at < target.closing
             and (observed_end - target.opening) % (5 * cc.MINUTE) == timedelta(0)
             and decision_at == observed_end + 2 * cc.MINUTE,
             "original_signal_or_decision_clock_changed")
    _require(observed_end >= target.opening + 30 * cc.MINUTE,
             "future_first30_window")
    _require(price_unit == "KRW" and type(price_unit) is str
             and target.price_basis == eb.PRICE_BASIS, "price_unit_or_basis_changed")
    _require(type(structural_stop) is Fraction and structural_stop > 0,
             "positive_exact_fraction_stop_required")
    _require(isinstance(sessions, Mapping) and isinstance(sources, Mapping)
             and set(universe.members) <= set(sessions)
             and set(universe.members) <= set(sources), "full_original_day_slots_required")
    evidence = {symbol: _source(sources[symbol], symbol, target) for symbol in universe.members}
    for symbol in universe.members:
        _session(sessions[symbol], target)
    _require(eb._price_source(evidence[target.symbol]) == target.price_source,
             "target_source_binding_changed")
    ranges = {
        "first30": (target.opening, target.opening + 30 * cc.MINUTE),
        "open_to_signal": (target.opening, observed_end),
        "last15": (observed_end - 15 * cc.MINUTE, observed_end),
    }
    diagnostics, peer_windows = [], {}
    observed = {name: [] for name in WINDOWS}
    for symbol in peers:
        peer_windows[symbol] = {}
        for name, (start, end) in ranges.items():
            diagnostic, window = _window(sessions[symbol], evidence[symbol], symbol,
                                         name, start, end, target, decision_at)
            diagnostics.append(diagnostic)
            peer_windows[symbol][name] = diagnostic
            if window is not None:
                observed[name].append(symbol)
    common = tuple(symbol for symbol in peers if all(symbol in observed[name] for name in WINDOWS))
    features = {
        "setup_is_A": ea.Feature(Fraction(int(setup_id == "EA-A"))),
        "fraction_of_session_elapsed": ea.Feature(Fraction(
            (decision_at - target.opening) // timedelta(microseconds=1),
            (target.closing - target.opening) // timedelta(microseconds=1))),
        "common_peer_count_divided_by_original_peer_count": ea.Feature(
            Fraction(len(common), len(peers)) if peers else None,
            () if peers else ("no_original_peers",), (universe.provenance,)),
    }
    for name in WINDOWS:
        provenance = tuple(peer_windows[symbol][name].provenance for symbol in common)
        features[f"peer_{name}_return"] = ea.Feature(
            sum((peer_windows[symbol][name].value for symbol in common), Fraction()) / len(common)
            if len(common) >= MINIMUM_COMMON_PEERS else None,
            () if len(common) >= MINIMUM_COMMON_PEERS else ("insufficient_common_observed_peers",),
            provenance)
    target_windows = {}
    for name, (start, end) in {**{key: ranges[key] for key in ("open_to_signal", "last15")},
            "trigger5": (observed_end - 5 * cc.MINUTE, observed_end),
            "signal_close": (observed_end - cc.MINUTE, observed_end)}.items():
        diagnostic, window = _window(sessions[target.symbol], evidence[target.symbol],
                                     target.symbol, name, start, end, target, decision_at)
        diagnostics.append(diagnostic)
        target_windows[name] = (diagnostic, window)
    for name in ("open_to_signal", "last15"):
        diagnostic, _ = target_windows[name]
        features[f"target_{name}_return"] = ea.Feature(diagnostic.value, diagnostic.reasons,
                                                       (diagnostic.provenance,))
    diagnostic, window = target_windows["signal_close"]
    features["structural_stop_gap_from_signal_close"] = ea.Feature(
        (Fraction(window.closing_price) - structural_stop) / Fraction(window.closing_price)
        if window is not None else None, diagnostic.reasons, (diagnostic.provenance,))
    diagnostic, window = target_windows["trigger5"]
    location, reasons = None, diagnostic.reasons
    if window is not None:
        prices = [sessions[target.symbol].minutes[observed_end - offset * cc.MINUTE]
                  for offset in range(1, 6)]
        high, low = max(row[1] for row in prices), min(row[2] for row in prices)
        if high == low:
            reasons = ("zero_trigger_range",)
        else:
            location = (Fraction(window.closing_price) - Fraction(low)) / (Fraction(high) - Fraction(low))
    features["trigger_close_location"] = ea.Feature(location, reasons, (diagnostic.provenance,))
    return PreparedFeatures(PROXY_ID, setup_id, target, observed_end, decision_at,
        MappingProxyType({name: features[name] for name in FEATURE_NAMES}), peers, common,
        MappingProxyType({name: tuple(observed[name]) for name in WINDOWS}), tuple(diagnostics),
        _freeze(evidence), universe.provenance, authentication_reference)
