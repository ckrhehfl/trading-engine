"""Pure EC causal feature support report; no IO, detector, fit or policy replay.

The caller authenticates the whole original JSON reports and ordered archive.
Parsing those reports exposes their old labels; only the existing integrity
validator inspects label metadata. No outcome or old common mask admits a row.
Every original row survives, and completion requires consuming the full union.
Historical availability, finality and corporate-action units remain assumed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from fractions import Fraction
from types import MappingProxyType

from research import intraday_campaign_account_inputs as account_inputs
from research import intraday_campaign_features as features
from research import intraday_campaign_feature_verifier as verifier
from research import intraday_campaign_volume as volume


eb, ea, cc = features.eb, features.ea, features.cc
POLICIES = ("D+1", "D+2")
SETUPS = ("EA-A", "EA-B")
MODE = "ec-causal-context-input-readiness"
SCHEMA_VERSION = 1
SIGNAL_CLOCKS = ("decision_at", "observed_through", "signal_bar_start",
                 "signal_bar_end", "setup_bar_end")
SUPPORT_NAMES = ("P04_volume_inputs", "P05_market_inputs", "P06_volume_and_market_inputs",
                 "P07_market_inputs", "P08_router_inputs", "P09_P10_all_ten_inputs")
LIMITATIONS = (
    "caller_authenticates_whole_original_json_including_old_labels",
    "old_label_metadata_inspected_only_by_existing_report_integrity_validation",
    "no_outcome_price_return_or_old_common_mask_for_feature_admission",
    "new_minimum_five_common_observed_peer_proxy_not_old_ea_eb_or_official_index",
    "end_plus_two_minute_availability_assumption_not_historical_certification",
    "retrieval_is_not_historical_publication_or_finality_certification",
    "saved_krw_prices_corporate_action_comparability_uncertified",
    "stored_a_volume_availability_cannot_be_recertified_from_report",
    "feature_support_only_no_labels_models_policy_selection_or_performance",
    "all_original_targets_unknowns_zero_signal_dates_and_weeks_retained",
)


class ContextReadinessRefusal(ValueError):
    """Broken identities or incomplete streams fail; they never become unknown."""


@dataclass(frozen=True)
class ReadinessInputs:
    setup_id: str
    authentication_reference: str
    request_metadata: Mapping
    account_projection: Mapping
    target_rows: Mapping
    volume_sidecar: Mapping | None
    volume_verification: Mapping | None


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ContextReadinessRefusal(reason)


def _authentication(authenticated: bool, reference: str) -> None:
    _require(authenticated is True, "caller_authenticated_original_inputs_required")
    _require(type(reference) is str and bool(reference.strip()), "authentication_reference_required")


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) in (tuple, list):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value: object) -> object:
    """JSON-compatible scalar/ref copy; never a raw Session or price copy."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [_plain(item) for item in value]
    if type(value) is Fraction:
        return {"numerator": str(value.numerator), "denominator": str(value.denominator)}
    return value


def _account_plain(value: object) -> object:
    # The existing day adapter needs ordinary JSON sequences, with typed stop
    # and clocks retained in its separate Candidate kwargs.
    if isinstance(value, Mapping):
        return {key: _account_plain(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [_account_plain(item) for item in value]
    return value


def project_readiness_inputs(report: Mapping, setup_id: str, *, inputs_authenticated: bool,
                             authentication_reference: str) -> ReadinessInputs:
    """Project one original report while it is alive, then release its labels.

    Original report row order and membership/source indices are retained. The
    account projection is a separate integrity/input seam, never feature kwargs.
    A volume projection is independently checked against the still-live report.
    No training-label availability analysis is performed in this unit.
    """
    _authentication(inputs_authenticated, authentication_reference)
    _require(type(setup_id) is str and setup_id in SETUPS, "unregistered_setup")
    validated_account = account_inputs.project_account_inputs(report, setup_id)
    # Reuse the original schema validator, then discard all old outcome/arm
    # evidence. This is a context-stream geometry, not the full core account
    # projection. Its candidate symbols preserve every original stored signal.
    account = {key: validated_account[key] for key in ("mode", "setup_id", "reference_sessions",
        "reference_weeks", "source_target_matrix", "upstream_calendar", "sources", "volume_sources")}
    account["variants"] = {policy: {"dates": {day: {"candidates": [
        {"symbol": candidate["symbol"]} for candidate in content["candidates"]]}
        for day, content in validated_account["variants"][policy]["dates"].items()}}
        for policy in POLICIES}
    target_rows = {}
    for policy in POLICIES:
        rows = []
        for index, original in enumerate(report["variants"][policy]["days"]):
            signal = original["signal"]
            rows.append({
                "session": original["session"], "symbol": original["symbol"],
                "original_row_index": index,
                **{key: original[key] for key in ("membership_day_index", "membership_target",
                                                 "source_index", "volume_source_index")},
                "signal_state": signal["state"], "signal_reason": signal["reason"],
                **{key: signal[key] for key in SIGNAL_CLOCKS},
                "structural_stop": account_inputs._stop(original["structural_stop"])
                    if signal["state"] == "signal" else None,
            })
        target_rows[policy] = rows
    sidecar = (volume.project_volume_inputs(report, inputs_authenticated=True)
               if setup_id == "EA-A" else None)
    receipt = (verifier.verify_volume_sidecar(sidecar, report, inputs_authenticated=True)
               if sidecar is not None else None)
    projected = ReadinessInputs(setup_id, authentication_reference,
        _freeze({"mode": "ec-context-readiness-original-request", "setup_id": setup_id,
                 "authentication_reference": authentication_reference,
                 "target_counts_by_policy": {policy: len(target_rows[policy]) for policy in POLICIES}}),
        _freeze(account), _freeze(target_rows), _freeze(sidecar), _freeze(receipt))
    # This independent original-to-projection check does not call this producer.
    verifier.verify_readiness_projection(projected, report, inputs_authenticated=True,
                                         authentication_reference=authentication_reference)
    return projected


def coalesce_minute_issues(issues: tuple[tuple[datetime, str], ...]) -> list[dict]:
    """Losslessly run-length encode sorted individual minute diagnostics."""
    result = []
    previous = None
    for at, state in issues:
        _require(type(at) is datetime and at.utcoffset() is not None
                 and type(state) is str and bool(state), "invalid_minute_issue")
        _require(previous is None or at > previous, "unordered_minute_issues")
        if result and result[-1]["state"] == state and result[-1]["end_at"] == at.isoformat():
            result[-1]["end_at"] = (at + cc.MINUTE).isoformat()
        else:
            result.append({"start_at": at.isoformat(), "end_at": (at + cc.MINUTE).isoformat(),
                           "state": state})
        previous = at
    return result


def expand_minute_issue_intervals(intervals: Iterable[Mapping]) -> tuple[tuple[datetime, str], ...]:
    """Decode intervals; reject overlap and noncanonical adjacent equal runs."""
    result, previous_end, previous_state = [], None, None
    for interval in intervals:
        _require(isinstance(interval, Mapping) and set(interval) == {"start_at", "end_at", "state"},
                 "invalid_minute_issue_interval")
        try:
            start, end = (datetime.fromisoformat(interval[key]) for key in ("start_at", "end_at"))
        except (TypeError, ValueError):
            raise ContextReadinessRefusal("invalid_minute_issue_interval_clock") from None
        state = interval["state"]
        _require(start.utcoffset() is not None and end.utcoffset() is not None
                 and start < end and (end - start) % cc.MINUTE == timedelta(0)
                 and type(state) is str and bool(state), "invalid_minute_issue_interval")
        _require(previous_end is None or start >= previous_end, "overlapping_minute_issue_intervals")
        _require(start != previous_end or state != previous_state, "uncoalesced_minute_issue_intervals")
        result.extend((start + minute * cc.MINUTE, state) for minute in range((end - start) // cc.MINUTE))
        previous_end, previous_state = end, state
    return tuple(result)


def serialize_context(prepared: features.PreparedFeatures, source_index_by_symbol: Mapping[str, int],
                      membership_day_index: int) -> dict:
    """Compact a verified record without repeated provenance or raw prices.

    Each window's full provenance is reconstructed from its original source
    index and range end (assumed end+2 availability). Universe provenance points
    to the original matrix day. Feature provenance references its scientific
    window name and symbol, even when different windows share identical source
    metadata or endpoints. Feature availability conservatively uses the original
    decision, including first30's common-peer intersection.
    """
    windows, ranges, window_indices = [], {}, {}
    for index, window in enumerate(prepared.windows):
        source_index = source_index_by_symbol[window.symbol]
        span = {"start_at": window.start_at.isoformat(), "end_at": window.end_at.isoformat()}
        _require(window.name not in ranges or ranges[window.name] == span, "window_ranges_changed")
        ranges[window.name] = span
        key = (window.symbol, window.name)
        _require(key not in window_indices, "duplicate_feature_window")
        window_indices[key] = index
        windows.append({"source_index": source_index, "name": window.name,
                        "value": _plain(window.value), "reasons": list(window.reasons),
                        "minute_issue_intervals": coalesce_minute_issues(window.minute_issues)})
    target_symbol = prepared.target.symbol
    feature_window_indices = {
        "setup_is_A": [], "fraction_of_session_elapsed": [],
        "common_peer_count_divided_by_original_peer_count": [],
        "target_open_to_signal_return": [window_indices[(target_symbol, "open_to_signal")]],
        "target_last15_return": [window_indices[(target_symbol, "last15")]],
        "structural_stop_gap_from_signal_close": [window_indices[(target_symbol, "signal_close")]],
        "trigger_close_location": [window_indices[(target_symbol, "trigger5")]],
        **{f"peer_{name}_return": [window_indices[(symbol, name)] for symbol in prepared.common_peers]
           for name in features.WINDOWS},
    }
    encoded_features = {}
    for name, feature in prepared.features.items():
        encoded_features[name] = {
            "value": _plain(feature.value), "reasons": list(feature.reasons),
            "provenance_window_indices": feature_window_indices[name],
            "universe_provenance": name == "common_peer_count_divided_by_original_peer_count",
            "available_at": prepared.decision_at.isoformat(),
        }
    indices = lambda symbols: [source_index_by_symbol[symbol] for symbol in symbols]
    return {"proxy_id": prepared.proxy_id, "observed_end": prepared.observed_end.isoformat(),
            "decision_at": prepared.decision_at.isoformat(),
            "membership_provenance_ref": {"policy": prepared.target.policy, "day_index": membership_day_index},
            "source_indices": indices(prepared.source_evidence),
            "fixed_peer_source_indices": indices(prepared.fixed_peers),
            "common_peer_source_indices": indices(prepared.common_peers),
            "observed_peer_source_indices_by_window": {name: indices(symbols)
                for name, symbols in prepared.observed_peers_by_window.items()},
            "window_ranges": ranges, "windows": windows, "features": encoded_features}


def _support(row: Mapping, setup: str, volume_row: Mapping | None) -> dict:
    result = dict.fromkeys(SUPPORT_NAMES)
    if row["context"] is None:
        return result
    known = {name: feature["value"] is not None for name, feature in row["context"]["features"].items()}
    market_a = all(known[name] for name in ("target_open_to_signal_return",
                     "peer_open_to_signal_return", "peer_last15_return"))
    if setup == "EA-A":
        observed_volume = volume_row["volume_state"] == "observed"
        result.update(P04_volume_inputs=observed_volume, P05_market_inputs=market_a,
                      P06_volume_and_market_inputs=observed_volume and market_a)
    else:
        result["P07_market_inputs"] = all(known[name] for name in ("peer_first30_return", "peer_last15_return"))
    result["P08_router_inputs"] = all(known[name] for name in
        ("peer_first30_return", "peer_open_to_signal_return", "peer_last15_return"))
    result["P09_P10_all_ten_inputs"] = all(known.values())
    return result


def summarize_rows(rows: Iterable[Mapping]) -> dict:
    """Scalar availability counts only; an empty date/week has explicit zeros."""
    rows = list(rows)
    state_counts = Counter(row["signal_state"] for row in rows)
    contexts = [row["context"] for row in rows if row["context"] is not None]
    feature_counts = {name: {"observed": sum(context["features"][name]["value"] is not None for context in contexts),
        "unknown": sum(context["features"][name]["value"] is None for context in contexts),
        "not_applicable": len(rows) - len(contexts)} for name in features.FEATURE_NAMES}
    return {"counts": {"targets": len(rows), "signals": state_counts["signal"],
                       "known_no_signal": state_counts["known_no_signal"], "unknown_signals": state_counts["unknown"]},
            "feature_counts": feature_counts,
            "peer_quorum": {"met": sum(len(context["common_peer_source_indices"]) >= features.MINIMUM_COMMON_PEERS for context in contexts),
                            "not_met": sum(len(context["common_peer_source_indices"]) < features.MINIMUM_COMMON_PEERS for context in contexts),
                            "not_applicable": len(rows) - len(contexts)},
            "common_peer_count_distribution": dict(sorted(Counter(str(len(context["common_peer_source_indices"])) for context in contexts).items(), key=lambda item: int(item[0]))),
            "original_peer_count_distribution": dict(sorted(Counter(str(len(context["fixed_peer_source_indices"])) for context in contexts).items(), key=lambda item: int(item[0]))),
            "support_counts": {name: {"observed": sum(row["input_support"][name] is True for row in rows),
                "unknown": sum(row["input_support"][name] is False for row in rows),
                "not_applicable": sum(row["input_support"][name] is None for row in rows)} for name in SUPPORT_NAMES}}


def training_feature_support(setups: Mapping, reference: list[str], weeks: list[str]) -> dict:
    """Past-only feature counts, with no labels, model fit or minimum-size pass."""
    daily = {day: {setup: 0 for setup in SETUPS} for day in reference}
    for setup in SETUPS:
        for row in setups[setup]["days"]:
            daily[row["session"]][setup] += row["input_support"]["P09_P10_all_ten_inputs"] is True

    def counts(days):
        return {setup: {"feature_complete_candidates": sum(daily[day][setup] for day in days),
                        "distinct_feature_complete_sessions": sum(daily[day][setup] > 0 for day in days)} for setup in SETUPS} | {
            "combined": {"feature_complete_candidates": sum(sum(daily[day].values()) for day in days),
                         "distinct_feature_complete_sessions": sum(any(daily[day].values()) for day in days)}}

    initial = min(100, len(reference))
    remainder = len(reference) - initial
    size, extra = divmod(remainder, 5)
    blocks, cursor = [], initial
    for index in range(5):
        length = size + (index < extra)
        test = reference[cursor:cursor + length]
        blocks.append({"block_index": index + 1, "test_reference_sessions": test,
                       "past_training_reference_session_count": cursor,
                       "past_training_feature_support": counts(reference[:cursor]),
                       "test_feature_support": counts(test)})
        cursor += length
    return {"scope": "feature_counts_only_not_observed_labels_or_training_readiness",
            "initial_reference_session_limit": 100, "initial_reference_sessions": reference[:initial],
            "initial_feature_support": counts(reference[:initial]), "expanding_blocks": blocks,
            "by_session": {day: {"feature_complete_candidates": {**daily[day], "combined": sum(daily[day].values())},
                                 "past_training_feature_support": counts(reference[:index])}
                           for index, day in enumerate(reference)},
            "by_week": {week: counts([day for day in reference if eb._week(day) == week]) for week in weeks}}


def build_readiness(a_projected: ReadinessInputs, b_projected: ReadinessInputs,
                    ordered_original_price_chunks: Iterable[Mapping], *, inputs_authenticated: bool,
                    authentication_reference: str, checkpoint: Callable[[], None] | None = None) -> dict:
    """Build complete readiness from one authenticated original-day stream.

    Each signal runs the causal producer and independent reconstruction once.
    Compact serialization is checked against that already verified record.
    Every original source is checked, even if it supplies no detected target.
    The caller owns registration, budgets, stream decoding and output storage.
    """
    _authentication(inputs_authenticated, authentication_reference)
    _require(not isinstance(ordered_original_price_chunks, Mapping), "ordered_price_chunks_required")
    _require(checkpoint is None or callable(checkpoint), "invalid_checkpoint")
    check = checkpoint or (lambda: None)
    for value, setup in ((a_projected, "EA-A"), (b_projected, "EA-B")):
        _require(type(value) is ReadinessInputs and value.setup_id == setup, "invalid_readiness_projection")
    projected = {"EA-A": a_projected, "EA-B": b_projected}
    accounts = {setup: _account_plain(value.account_projection) for setup, value in projected.items()}
    # Validate both complete populations before starting price iteration.
    account_inputs._linked_inputs(accounts["EA-A"], accounts["EA-B"])
    reference = list(accounts["EA-A"]["reference_sessions"])
    weeks = list(accounts["EA-A"]["reference_weeks"])
    sources = accounts["EA-A"]["sources"]
    source_indices = {(source["session"], source["symbol"]): index for index, source in enumerate(sources)}
    rows_by_day = {setup: {policy: {day: [] for day in reference} for policy in POLICIES} for setup in SETUPS}
    output = {policy: {"setups": {setup: {"days": [None] * len(projected[setup].target_rows[policy])}
                                  for setup in SETUPS}} for policy in POLICIES}
    for setup, value in projected.items():
        for policy in POLICIES:
            for row in value.target_rows[policy]:
                rows_by_day[setup][policy][row["session"]].append(row)
    volume_rows = {policy: {(row["session"], row["symbol"]): row
                  for row in a_projected.volume_sidecar["variants"][policy]["days"]} for policy in POLICIES}
    consumed_days = consumed_chunks = consumed_rows = verified_signals = 0
    complete = False
    accumulator = verifier.ReadinessAccumulator(a_projected, b_projected,
                                               authentication_reference=authentication_reference)
    for day in account_inputs.iter_context_price_days(accounts["EA-A"], accounts["EA-B"], ordered_original_price_chunks):
        check()
        accumulator.add_day(day)
        session = day["session"]
        _require(session == reference[consumed_days], "original_day_order_changed")
        day_source_indices = {symbol: source_indices[(session, symbol)] for symbol in day["original_sources"]}
        for policy in POLICIES:
            membership = accounts["EA-A"]["source_target_matrix"][policy]["days"][consumed_days]
            universe = eb._universe(membership, session)
            for setup in SETUPS:
                for original in rows_by_day[setup][policy][session]:
                    check()
                    row = _plain(original)
                    prepared = arguments = None
                    row["context"] = None
                    if original["signal_state"] == "signal":
                        target = ea.Target(original["symbol"], date.fromisoformat(session), policy,
                            day["session_open"], day["session_close"], eb.PRICE_BASIS,
                            eb._price_source(day["original_sources"][original["symbol"]]))
                        arguments = {"target": target, "universe": universe,
                            "sessions": day["original_sessions"], "sources": day["original_sources"],
                            "setup_id": setup, "observed_end": datetime.fromisoformat(original["observed_through"]),
                            "decision_at": datetime.fromisoformat(original["decision_at"]),
                            "structural_stop": original["structural_stop"], "inputs_authenticated": True,
                            "authentication_reference": authentication_reference, "price_unit": "KRW"}
                        prepared = features.prepare_features(**arguments)
                        check()
                        row["context"] = serialize_context(prepared, day_source_indices, original["membership_day_index"])
                        verified_signals += 1
                    row["input_support"] = _support(row, setup, volume_rows[policy].get((session, row["symbol"])))
                    output[policy]["setups"][setup]["days"][row["original_row_index"]] = row
                    accumulator.add_row(policy, setup, row, prepared=prepared, original_inputs=arguments)
                    check()
                    del prepared, arguments
        consumed_days += 1
        consumed_chunks += day["consumed_target_chunks"]
        consumed_rows += day["consumed_price_rows"]
        complete = day["original_union_complete"]
        accumulator.finish_day()
        del day
    _require(complete and consumed_days == len(reference), "original_union_not_exhausted")
    for policy in POLICIES:
        for setup in SETUPS:
            variant = output[policy]["setups"][setup]
            rows = variant["days"]
            _require(all(row is not None for row in rows), "original_target_row_missing")
            variant.update(summarize_rows(rows))
            variant["by_session"] = {day: summarize_rows([row for row in rows if row["session"] == day]) for day in reference}
            variant["by_week"] = {week: summarize_rows([row for row in rows if eb._week(row["session"]) == week]) for week in weeks}
        output[policy]["training_feature_support"] = training_feature_support(output[policy]["setups"], reference, weeks)
    report = {"mode": MODE, "schema_version": SCHEMA_VERSION, "proxy_id": features.PROXY_ID,
        "authentication_reference": authentication_reference,
        "original_requests": {setup: _plain(value.request_metadata) for setup, value in projected.items()},
        "reference_sessions": reference, "reference_weeks": weeks,
        **{key: accounts["EA-A"][key] for key in ("source_target_matrix", "upstream_calendar", "sources")},
        "volume_sources": accounts["EA-A"]["volume_sources"],
        "volume_sidecar": _plain(a_projected.volume_sidecar),
        "volume_verification": _plain(a_projected.volume_verification),
        "price_basis": eb.PRICE_BASIS, "price_unit": "KRW", "minimum_common_peers": features.MINIMUM_COMMON_PEERS,
        "variants": output, "input_evidence": {"consumed_reference_sessions": consumed_days,
            "consumed_original_target_chunks": consumed_chunks, "consumed_price_rows": consumed_rows,
            "original_union_complete": complete, "independently_verified_signals": verified_signals},
        "policy_results_not_pooled": True, "feature_support_only": True,
        "historical_timing_certified": False, "corporate_action_units_certified": False,
        "volume_availability_reauthenticated": False, "performance_computed": False,
        "training_labels_analyzed": False, "model_fits": 0, "policy_evaluations": 0,
        "strategy_pass": False, "promotion_allowed": False, "actual_execution_authorized": False,
        "limitations": list(LIMITATIONS)}
    check()
    report["independent_verification"] = accumulator.verify_report(report)
    return report
