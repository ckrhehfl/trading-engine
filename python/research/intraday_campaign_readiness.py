"""Pure EC metadata projection of already parsed, caller-pinned EB reports.

No files, prices, return arithmetic, authentication or account replay occur here.
The caller owns pins, the actual 257-session/5,140-target execution contract and
trial logging. Small invented calendars retain the original twenty-member shape.
Known intervals describe saved timing only, including an unknown exit OPEN;
they do not establish observable holding marks, feasible cash or actual fills.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
import re


MODE = "ec-readiness-metadata-projection"
COMBINED_MODE = "ec-combined-readiness-metadata"
POLICIES = ("D+1", "D+2")
KST = timezone(timedelta(hours=9))
FEATURES = {
    "EA-A": ("relative_volume", "stock_minus_peer_return", "peer_open_to_signal_return", "peer_last15_return"),
    "EA-B": ("peer_first30_return", "peer_last15_return"),
}
ARMS = {"EA-A": ("basic", "volume_only", "market_relative_only", "both"),
        "EA-B": ("basic", "market_context")}
HYPOTHESES = {"EA-A": "H1-morning-trend-pullback-reclaim", "EA-B": "H3-failed-downside-range-break"}
FEATURE_REASONS = {"incomplete_volume_window", "volume_inputs_missing", "missing_volume_reference",
                   "missing_current_volume", "nonpositive_reference_median", "fewer_than_ten_fixed_peers",
                   "incomplete_fixed_peer_basket", "same_day_cap_infeasible"}
OUTCOME_REASONS = {
    "known": {"time_cap", "delayed_structural_stop", "stop_at_time_cap"},
    "unknown": {"same_day_cap_infeasible", "structural_stop_missing", "entry_open_missing",
                "required_path_missing", "exit_open_missing"},
    "declined": {"invalidated_entry"},
}


class ReadinessRefusal(ValueError):
    """Malformed metadata is refused, never recoded as missing observations."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ReadinessRefusal(reason)


def _mapping(value, reason: str) -> Mapping:
    _require(isinstance(value, Mapping), reason)
    return value


def _day(value) -> date:
    try:
        _require(type(value) is str, "invalid_session")
        result = date.fromisoformat(value)
        _require(result.isoformat() == value, "invalid_session")
        return result
    except (TypeError, ValueError):
        raise ReadinessRefusal("invalid_session") from None


def _clock(value, session: str | None = None, *, optional: bool = False) -> datetime | None:
    if optional and value is None:
        return None
    try:
        _require(type(value) is str, "invalid_timestamp")
        parsed = datetime.fromisoformat(value)
        _require(parsed.utcoffset() is not None, "aware_timestamp_required")
        parsed = parsed.astimezone(KST)
        _require(session is None or parsed.date() == _day(session), "timestamp_outside_session")
        return parsed
    except (TypeError, ValueError):
        raise ReadinessRefusal("invalid_timestamp") from None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _opening(day: str) -> datetime:
    # The two exceptional assumed opens are the existing EB producer's clocks.
    return datetime.combine(_day(day), datetime.min.time(), KST).replace(
        hour=10 if day in {"2025-11-13", "2026-01-02"} else 9)


def _week(day: str) -> str:
    parsed = _day(day)
    return (parsed - timedelta(days=parsed.weekday())).isoformat()


def _sources(values, expected: set[tuple[str, str]]) -> list:
    _require(type(values) is list, "invalid_sources")
    identities = []
    for source in values:
        _mapping(source, "invalid_source")
        identity = (source.get("session"), source.get("symbol"))
        _require(all(type(value) is str for value in identity), "invalid_source_identity")
        identities.append(identity)
        _require(type(source.get("input_rows")) is int and 0 <= source["input_rows"] <= 600,
                 "invalid_source_row_count")
        for field in ("data_vintage", "row_linkage_sha256"):
            _require(type(source.get(field)) is str and re.fullmatch(r"[0-9a-f]{64}", source[field]) is not None,
                     "invalid_source_digest")
    _require(len(identities) == len(set(identities)) and set(identities) == expected,
             "source_population_changed")
    return values


def _source_at(sources: list, index, identity: tuple[str, str]) -> Mapping:
    _require(type(index) is int and 0 <= index < len(sources), "invalid_source_index")
    source = sources[index]
    _require((source["session"], source["symbol"]) == identity, "source_index_identity_changed")
    return source


def _row(row: Mapping, setup: str, sources: list, volumes: list) -> dict:
    _require({"entry_at", "cap_at", "outcome", "volume_source_index"} <= row.keys(), "missing_row_metadata")
    day, symbol = row["session"], row["symbol"]
    signal = _mapping(row.get("signal"), "invalid_signal")
    _require({"observed_through", "signal_bar_start", "signal_bar_end", "setup_bar_end"} <= signal.keys(),
             "missing_signal_metadata")
    state, reason = signal.get("state"), signal.get("reason")
    reasons = {"signal": {"reclaim" if setup == "EA-A" else "downside_reclaimed"},
               "known_no_signal": {"initial_condition_false", "midpoint_invalidated", "first_setup_timeout", "decision_cutoff"},
               "unknown": {"clock_infeasible", "missing_prefix", "noncontiguous_prefix"}}
    _require(type(state) is str and state in reasons and type(reason) is str and reason in reasons[state],
             "invalid_signal_state_or_reason")
    _require(signal.get("hypothesis_id") == HYPOTHESES[setup], "signal_hypothesis_changed")
    decision = _clock(signal.get("decision_at"), day)
    observed = _clock(signal.get("observed_through"), day, optional=True)
    start = _clock(signal.get("signal_bar_start"), day, optional=True)
    end = _clock(signal.get("signal_bar_end"), day, optional=True)
    setup_end = _clock(signal.get("setup_bar_end"), day, optional=True)
    _require(decision > _opening(day) and (observed is None or observed + timedelta(minutes=2) <= decision)
             and (setup_end is None or observed is not None and setup_end <= observed), "noncausal_signal_clock")
    entry = _clock(row.get("entry_at"), day, optional=True)
    cap = _clock(row.get("cap_at"), day, optional=True)
    outcome = row.get("outcome")
    if state == "signal":
        _require(start is not None and end is not None and setup_end is not None
                 and start + timedelta(minutes=5) == end == observed
                 and end + timedelta(minutes=2) == decision, "invalid_signal_bar_clock")
        opening = _opening(day)
        _require(entry == opening + ((decision - opening) // timedelta(minutes=5) + 1) * timedelta(minutes=5)
                 and cap == entry + timedelta(minutes=60), "candidate_clock_changed")
        outcome = _mapping(outcome, "signal_outcome_missing")
        status, outcome_reason = outcome.get("status"), outcome.get("reason")
        _require(type(status) is str and status in OUTCOME_REASONS and type(outcome_reason) is str
                 and outcome_reason in OUTCOME_REASONS[status], "invalid_outcome_state_or_reason")
        _require(_clock(outcome.get("entry_at"), day) == entry, "outcome_entry_changed")
        exit_at = _clock(outcome.get("exit_at"), day, optional=True)
        has_exit = status == "known" or outcome_reason == "exit_open_missing"
        _require((exit_at is not None) == has_exit
                 and (exit_at is None or entry < exit_at <= cap), "invalid_outcome_exit")
        if outcome_reason in {"time_cap", "stop_at_time_cap"}:
            _require(exit_at == cap, "outcome_cap_changed")
        if outcome_reason == "delayed_structural_stop":
            _require(exit_at < cap, "invalid_delayed_stop_clock")
    else:
        _require(start is None and end is None and entry is None and cap is None and outcome is None,
                 "nonsignal_has_trade_metadata")
        status, outcome_reason, exit_at = "absent", None, None
    features = _mapping(row.get("features"), "invalid_features")
    expected_features = set(FEATURES[setup]) if state == "signal" else set()
    _require(set(features) == expected_features or state == "signal" and set(features) == {"clock"},
             "feature_schema_changed")
    projected_features = {}
    for name, feature in features.items():
        _mapping(feature, "invalid_feature")
        _require("value" in feature, "feature_value_presence_missing")
        observed_feature = feature["value"] is not None
        feature_reasons = feature.get("reasons")
        _require(type(feature_reasons) in (list, tuple) and all(type(item) is str and item in FEATURE_REASONS
                 for item in feature_reasons), "invalid_feature_reasons")
        _require(observed_feature == (not feature_reasons), "feature_presence_reason_conflict")
        projected_features[name] = {"observed": observed_feature, "reasons": list(feature_reasons)}
    arms = _mapping(row.get("arms"), "invalid_arms")
    _require(set(arms) == set(ARMS[setup]) and all(type(v) is str and v in {"selected", "rejected", "unknown"}
             for v in arms.values()), "invalid_arm_states")
    if state != "signal":
        _require(set(arms.values()) == {"unknown" if state == "unknown" else "rejected"}, "nonsignal_arm_conflict")
    elif "clock" in features:
        _require(set(arms.values()) == {"unknown"} and outcome_reason == "same_day_cap_infeasible",
                 "infeasible_clock_metadata_conflict")
    else:
        _require(arms["basic"] == "selected", "basic_arm_changed")
    common = state == "signal" and bool(projected_features) and all(f["observed"] for f in projected_features.values())
    _require(row.get("common_context_observable") is common
             and row.get("common_price_observable") is (common and status == "known"), "observable_flags_conflict")
    source = _source_at(sources, row.get("source_index"), (day, symbol))
    volume_index = row.get("volume_source_index")
    if setup == "EA-A":
        _source_at(volumes, volume_index, (day, symbol))
    else:
        _require(volume_index is None, "unexpected_volume_source")
    _require(source["input_rows"] != 0 or state == "unknown", "empty_source_has_observed_signal")
    return {"session": day, "symbol": symbol, "membership_day_index": row["membership_day_index"],
            "source_index": row["source_index"], "volume_source_index": volume_index,
            "data_vintage": source["data_vintage"], "row_linkage_sha256": source["row_linkage_sha256"],
            "signal_state": state, "signal_reason": reason, "decision_at": _iso(decision),
            "observed_through": _iso(observed), "entry_at": _iso(entry), "cap_at": _iso(cap),
            "outcome_status": status, "outcome_reason": outcome_reason, "exit_at": _iso(exit_at),
            "source_state": "empty" if source["input_rows"] == 0 else "present",
            "features": projected_features, "arms": dict(arms), "common_context_observable": common,
            "common_price_observable": common and status == "known"}


def _interval(row: Mapping) -> str:
    if row["signal_state"] != "signal" or row["outcome_status"] == "declined":
        return "not_applicable"
    return "known" if row["exit_at"] is not None else "unknown"


def _counts(rows: list[dict]) -> dict:
    signals = Counter(row["signal_state"] for row in rows)
    outcomes = Counter(row["outcome_status"] for row in rows)
    intervals = Counter(_interval(row) for row in rows)
    return {"targets": len(rows), "signals": signals["signal"], "known_no_signal": signals["known_no_signal"],
            "unknown_signals": signals["unknown"], "empty_sources": sum(r["source_state"] == "empty" for r in rows),
            "outcome_absent": outcomes["absent"], "outcome_known": outcomes["known"],
            "outcome_unknown": outcomes["unknown"], "entry_declined": outcomes["declined"],
            "common_context_observable": sum(r["common_context_observable"] for r in rows),
            "common_price_observable": sum(r["common_price_observable"] for r in rows),
            **{f"interval_{name}": intervals[name] for name in ("known", "unknown", "not_applicable")}}


def project_report(report: Mapping, setup_id: str) -> dict:
    """Validate complete pinned-report metadata, projecting no market values.

    No caller assertion here authenticates a pin or authorizes an actual read.
    Malformed signal/outcome objects fail; absence is valid only for nonsignals.
    """
    _require(type(setup_id) is str and setup_id in FEATURES, "unexpected_setup")
    _mapping(report, "invalid_report")
    _require(report.get("mode") == "fixed-ea-context-exploratory-comparison"
             and report.get("setup_id") == setup_id, "unexpected_report_identity")
    reference = report.get("reference_sessions")
    _require(type(reference) is list and bool(reference), "invalid_reference_sessions")
    for session in reference:
        _day(session)
    _require(reference == sorted(set(reference)), "invalid_reference_sessions")
    weeks = sorted({_week(day) for day in reference})
    _require(report.get("reference_weeks") == weeks, "reference_weeks_changed")
    calendar_rows = _mapping(report.get("upstream_calendar"), "invalid_upstream_calendar").get("rows")
    _require(type(calendar_rows) is list, "invalid_upstream_calendar")
    calendar = [_mapping(row, "invalid_calendar_row").get("date") for row in calendar_rows]
    for session in calendar:
        _day(session)
    _require(calendar == sorted(set(calendar)) and
             [day for day in calendar if reference[0] <= day <= reference[-1]] == reference,
             "reference_calendar_changed")
    variants = _mapping(report.get("variants"), "invalid_variants")
    matrix = _mapping(report.get("source_target_matrix"), "invalid_target_matrix")
    _require(set(variants) == set(matrix) == set(POLICIES), "timing_policies_changed")
    expected = {}
    for policy in POLICIES:
        days = _mapping(matrix[policy], "invalid_target_policy").get("days")
        _require(type(days) is list and len(days) == len(reference), "target_sessions_changed")
        expected[policy] = {}
        for index, day in enumerate(days):
            _mapping(day, "invalid_target_day")
            _require(day.get("session") == reference[index], "target_sessions_changed")
            _require(_clock(day.get("available_at")) <= _clock(day.get("selected_at")) < _opening(reference[index]),
                     "noncausal_membership")
            targets = day.get("targets")
            _require(type(targets) is list and len(targets) == 20, "target_population_changed")
            ranks, symbols = set(), set()
            for target in targets:
                _mapping(target, "invalid_target")
                symbol, rank = target.get("symbol"), target.get("rank")
                _require(type(symbol) is str and re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol) is not None
                         and type(rank) is int, "invalid_target_identity")
                ranks.add(rank)
                symbols.add(symbol)
                expected[policy][(reference[index], symbol)] = (index, target, day)
            _require(len(symbols) == 20 and ranks == set(range(1, 21)), "target_population_changed")
    union = set(expected[POLICIES[0]]) | set(expected[POLICIES[1]])
    sources = _sources(report.get("sources"), union)
    volumes = _sources(report.get("volume_sources"), union if setup_id == "EA-A" else set())
    result = {"mode": MODE, "setup_id": setup_id, "reference_sessions": list(reference), "reference_weeks": weeks,
              "variants": {}, "performance_computed": False, "account_path_proven": False}
    for policy in POLICIES:
        rows = _mapping(variants[policy], "invalid_variant").get("days")
        _require(type(rows) is list and len(rows) == len(expected[policy]), "report_target_population_changed")
        projected, seen = [], set()
        for row in rows:
            _mapping(row, "invalid_row")
            identity = (row.get("session"), row.get("symbol"))
            _require(all(type(v) is str for v in identity) and identity in expected[policy] and identity not in seen,
                     "duplicate_or_unregistered_target")
            seen.add(identity)
            index, target, membership = expected[policy][identity]
            _require(type(row.get("membership_day_index")) is int and row["membership_day_index"] == index
                     and row.get("membership_target") == target, "membership_linkage_changed")
            prior = [day for day in calendar if day < identity[0]][-20:]
            _require(row.get("expected_volume_reference_dates") == (prior if setup_id == "EA-A" else [])
                     and "volume_reference_calendar_complete" in row
                     and row["volume_reference_calendar_complete"] is (len(prior) == 20 if setup_id == "EA-A" else None),
                     "volume_reference_calendar_changed")
            peers = {t["symbol"] for t in membership["targets"]} - {identity[1]}
            peer_indices = _mapping(row.get("peer_source_indices"), "invalid_peer_indices")
            _require(set(peer_indices) == peers and type(row.get("fixed_peers")) in (list, tuple)
                     and len(row["fixed_peers"]) == len(peers) and set(row["fixed_peers"]) == peers,
                     "fixed_peer_basket_changed")
            for peer, source_index in peer_indices.items():
                _source_at(sources, source_index, (identity[0], peer))
            projected.append(_row(row, setup_id, sources, volumes))
        projected.sort(key=lambda row: (row["session"], row["symbol"]))
        feature_names = sorted(set(FEATURES[setup_id]) | {name for row in projected for name in row["features"]})
        result["variants"][policy] = {"days": projected, "counts": _counts(projected),
            "arm_counts": {arm: {state: sum(row["arms"][arm] == state for row in projected)
                for state in ("selected", "rejected", "unknown")} for arm in ARMS[setup_id]},
            "feature_counts": {name: {"observed": sum(name in row["features"] and row["features"][name]["observed"] for row in projected),
                "unknown": sum(name in row["features"] and not row["features"][name]["observed"] for row in projected),
                "not_applicable": sum(name not in row["features"] for row in projected)} for name in feature_names},
            "by_week": {week: _counts([row for row in projected if _week(row["session"]) == week]) for week in weeks}}
    return result


def combine_readiness(a_projection: Mapping, b_projection: Mapping) -> dict:
    """Join validated projections; all counts are descriptive candidate timings.

    Equal planned entries include declined candidates. Intervals are half-open:
    an exit at another entry is not overlap. Unknown exits never use the cap as
    a replacement. No risk limits, mark path or shared-account feasibility is
    evaluated; the two availability policies are never pooled.
    """
    for projection, setup in ((a_projection, "EA-A"), (b_projection, "EA-B")):
        _mapping(projection, "invalid_projection")
        _require(projection.get("mode") == MODE and projection.get("setup_id") == setup
                 and projection.get("performance_computed") is False and projection.get("account_path_proven") is False,
                 "invalid_projection_identity")
        _require(set(_mapping(projection.get("variants"), "invalid_projected_variants")) == set(POLICIES),
                 "timing_policies_changed")
    _require(a_projection["reference_sessions"] == b_projection["reference_sessions"]
             and a_projection["reference_weeks"] == b_projection["reference_weeks"], "setup_calendars_differ")
    diagnostics = {}
    for policy in POLICIES:
        a_rows, b_rows = (p["variants"][policy]["days"] for p in (a_projection, b_projection))
        a_by = {(r["session"], r["symbol"]): r for r in a_rows}
        b_by = {(r["session"], r["symbol"]): r for r in b_rows}
        _require(len(a_by) == len(a_rows) and len(b_by) == len(b_rows) and a_by.keys() == b_by.keys(),
                 "setup_target_populations_differ")
        counts = dict.fromkeys(("same_symbol_session_signals", "same_symbol_session_simultaneous_entries",
                               "same_symbol_session_known_time_overlap", "same_symbol_session_unknown_interval",
                               "known_time_overlap_pairs", "simultaneous_entry_pairs"), 0)
        counts["same_symbol_session_targets"] = len(a_by)
        a_signals = [r for r in a_rows if r["signal_state"] == "signal"]
        b_per_day = {}
        for row in b_rows:
            if row["signal_state"] == "signal":
                b_per_day.setdefault(row["session"], []).append(row)
        for a in a_signals:
            for b in b_per_day.get(a["session"], ()):
                same = a["symbol"] == b["symbol"]
                simultaneous = a["entry_at"] == b["entry_at"]
                known = _interval(a) == _interval(b) == "known"
                overlap = known and max(a["entry_at"], b["entry_at"]) < min(a["exit_at"], b["exit_at"])
                counts["same_symbol_session_signals"] += same
                counts["same_symbol_session_simultaneous_entries"] += same and simultaneous
                counts["same_symbol_session_known_time_overlap"] += same and overlap
                counts["same_symbol_session_unknown_interval"] += same and "unknown" in {_interval(a), _interval(b)}
                counts["known_time_overlap_pairs"] += overlap
                counts["simultaneous_entry_pairs"] += simultaneous
        for label in ("known", "unknown"):
            counts[f"{label}_interval_candidates"] = {setup: sum(_interval(r) == label for r in rows)
                for setup, rows in (("EA-A", a_rows), ("EA-B", b_rows))}
        counts["entry_declined_candidates"] = {setup: sum(r["outcome_status"] == "declined" for r in rows)
            for setup, rows in (("EA-A", a_rows), ("EA-B", b_rows))}
        diagnostics[policy] = counts
    return {"mode": COMBINED_MODE, "setups": {"EA-A": a_projection, "EA-B": b_projection},
            "variants": diagnostics, "performance_computed": False, "account_path_proven": False,
            "policy_results_not_pooled": True}
