"""Causal observed-peer input inventory on the unchanged completed DK book.

Only supplied mappings and decompressed original canonical line bytes are read.
No detector, peer return, candidate selection, future label or statistic is run.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json

from research import intraday_breakout_loss_concentration as dl


ROOT = dl.ROOT
SPECIFICATION_ID = "intraday-market-context-readiness-v1"
MODE = "fixed-original-basic-breakout-causal-observed-peer-input-readiness"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-market-context-readiness-v1.json"
OUTPUT_NAME = "intraday-do-market-context-readiness-20261025-v1"
WINDOW, POLICIES = dict(dl.WINDOW), dl.POLICIES
UPSTREAM_SHA256 = dl.UPSTREAM_SHA256
UPSTREAM_SPECIFICATION_SHA256 = dl.UPSTREAM_SPECIFICATION_SHA256
INPUT_COMPRESSED_SHA256 = "4a5380b5477476a55dccb402c1227b23e0e6a08bc9c92f2e742eef2d9a7b62de"
INPUT_STREAM_SHA256 = "d48593ce0da9d6cdca17ad00002e65fbcfda1f7b99f6b83c272ebb16943b36da"
SOURCE_ROLES = {"parent": "pinned_completed_full_DK_basic_breakout_exit_policy_report",
    "archive": "pinned_original_CC_saved_raw_minute_archive"}
RESOURCES = {"maximum_source_bytes": 67108864, "maximum_compressed_bytes": 536870912,
    "maximum_uncompressed_bytes": 1073741824, "maximum_chunk_bytes": 1048576,
    "maximum_chunks": 5158, "maximum_session_rows": 600, "maximum_rows": 1002211,
    "cpu_seconds": 300, "address_space_mib": 512, "wall_seconds": 1200, "nice": 10,
    "minimum_free_bytes": 1073741824, "maximum_market_gets": 0,
    "maximum_token_posts": 0, "automatic_retries": 0}
NEXT_HYPOTHESIS = {
    "id": "basic_OR15_positive_observed_peer_rolling15m_min5_v1",
    "population": "unchanged_original_common_complete_basic_OR15_first_breakout_book_all_targets_and_unknowns_retained",
    "predicate": "basic_OR15_first_breakout_AND_equal_weight_mean_of_all_available_other_original_members_15minute_first_OPEN_to_last_CLOSE_price_changes_strictly_positive",
    "window": "original_signal_bar_end_F_minus15minutes_inclusive_to_F_exclusive",
    "minimum_complete_peer_windows": 5,
    "peer_rule": "all_complete_other_members_of_original_policy_day20_no_replacement_return_rank_or_outcome_selection",
    "zero_rule": "exact_zero_rejects_subquorum_unknown_never_zero_or_negative",
    "availability": "every_observation_end_plus2minutes_at_most_original_D_Fplus2_entry_E_Dplus3",
    "entry": "unchanged_original_D_plus3minute_OPEN",
    "exit": "unchanged_original_E_plus30minute_OPEN",
    "assumed_cost": {"numerator": "33", "denominator": "10000"},
    "other_conjuncts": [],
    "interpretation": "observed_large_stock_basket_not_official_KOSPI_KOSDAQ_index_or_complete_market",
    "future_gate": "new_registered_own_feature_label_missingness_signal_counts_weekly15fit_and_own_label_sizing_before_any_comparison_no_old_waiver",
}
PARAMETERS = {
    "cohort": "all_original_DK_targets_selected_iff_outcome_complete_and_basic_status_signal",
    "window_minutes": 15, "availability_lag_minutes": 2,
    "minimum_complete_peer_windows": 5, "original_members": 20, "other_members": 19,
    "entry_delay_minutes": 3, "time_cap_minutes": 30,
    "assumed_cost": {"numerator": "33", "denominator": "10000"},
    "window": "F_minus15_inclusive_F_exclusive_F_original_signal_bar_end_D_Fplus2_E_Dplus3",
    "validity": "one_unambiguous_positive_valid_OHLC_row_per_minute_duplicate_even_equal_conflicts",
    "timestamp": "original_integer_milliseconds_floor_to_KST_open_minute_preserve_offsets",
    "missingness": "missing_conflict_invalid_price_distinct_unknowns_all19_and_gaps_retained_no_new_event_exclusion",
    "atoms": "complete_peer_first_OPEN_last_CLOSE_original_coordinates_metadata_offsets_and_source_fingerprints_only",
    "output_scope": "readiness_only_no_peer_returns_mean_sign_candidate_counts_labels_statistics_or_performance",
    "next_hypothesis": NEXT_HYPOTHESIS,
}
ASSUMPTIONS = {
    "source": "caller_authenticates_completed_DK_and_original_CC_gzip_pins_no_new_source_or_parent_replay",
    "availability": "minute_end_plus2minutes_modeled_not_historical_publication_retrieval_is_not_availability",
    "membership": "original_available_at_at_most_selected_at_strictly_before_session_open",
    "unknowns": "all_original_targets_masks_book_metadata_retained_by_accounting_and_canonical_hashes_new_gaps_never_drop_events",
    "basket": "other_original19_members_only_observed_stock_basket_not_official_market_index",
    "quorum": "fixed_five_complete_peer_windows_operational_hypothesis_not_representativeness_or_statistical_theorem",
    "policies": "Dplus1_Dplus2_overlap_input_timing_assumptions_not_holding_days_or_independent_samples",
    "future": "no_signal_count_weekly_operating_fit_sizing_return_comparison_or_gate_waiver_in_readiness",
}
FLAGS = {name: False for name in (
    "historical_eligibility_certified", "liquidity_certified", "historical_revision_status_certified",
    "calendar_completeness_certified", "historical_api_availability_proven", "price_basis_certified",
    "performance_certified", "promotion_allowed", "full_family_certified", "family_adopted",
    "underpower_exception_applied", "historical_costs_certified", "actual_fills_certified",
    "actual_portfolio_pnl", "account_maximum_drawdown_calculated", "orders_simulated",
    "new_signals_calculated", "new_labels_calculated", "upstream_statistics_recalculated",
    "comparison_performed", "candidate_measured", "peer_returns_calculated",
    "peer_return_mean_or_sign_calculated", "own_label_sizing_calculated")}
FLAGS.update(exploratory_only=True, readiness_only=True, raw_replay_performed=False,
    new_performance_families=0, source_rows_overwritten=0)
GAP_STATES = ("missing", "conflict", "invalid_price")
METADATA = ("observation_date", "available_at", "retrieved_at", "source", "is_final", "evidence_level")
COLUMNS = ("symbol", "interval", "open_time_ms", "open", "high", "low", "close", "fetched_at")
MINUTE = timedelta(minutes=1)
KST = timezone(timedelta(hours=9))
_canonical, _digest = dl._canonical, dl._digest


class MarketContextReadinessRefusal(RuntimeError):
    """Stable refusal codes never include supplied private data."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise MarketContextReadinessRefusal(reason)


def _same(left: object, right: object, reason: str) -> None:
    _require(_canonical(left) == _canonical(right), reason)


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "output_name": OUTPUT_NAME,
        "window": WINDOW, "source_roles": SOURCE_ROLES, "upstream_sha256": UPSTREAM_SHA256,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
        "input_compressed_sha256": INPUT_COMPRESSED_SHA256, "input_stream_sha256": INPUT_STREAM_SHA256,
        "parameters": PARAMETERS, "resources": RESOURCES}
    try:
        same = _canonical(spec) == _canonical(expected)
    except (TypeError, ValueError):
        same = False
    _require(same, "specification_scope_mismatch")


def _opening(day: str) -> datetime:
    return datetime.fromisoformat(day + ("T10:00:00+09:00" if day in {"2025-11-13", "2026-01-02"}
        else "T09:00:00+09:00"))


def _closing(day: str) -> datetime:
    return datetime.fromisoformat(day + ("T16:30:00+09:00" if day == "2025-11-13" else "T15:30:00+09:00"))


def _membership(membership: Mapping, day: str) -> None:
    _require(membership["session"] == day and dl._instant(membership["available_at"])
        <= dl._instant(membership["selected_at"]) < _opening(day), "source_membership_availability_changed")
    _require(len(membership["targets"]) == 20
        and all(type(target["rank"]) is int for target in membership["targets"])
        and {target["rank"] for target in membership["targets"]} == set(range(1, 21))
        and len({target["symbol"] for target in membership["targets"]}) == 20,
        "source_original20_members_changed")


def _event(row: Mapping, member: Mapping, membership: Mapping, checkpoint: Callable[[], None]) -> dict:
    event, label = row["arms"]["basic"]["event"], row["arms"]["basic"]["label"]
    end, decision, entry = (dl._instant(value) for value in
        (event["signal_bar_end"], event["decision_at"], label["entry_at"]))
    cap = dl._instant(label["exit_at"])
    _require(dl._instant(event["signal_bar_start"]) == end - 5 * MINUTE
        and decision == end + 2 * MINUTE and dl._instant(event["available_at"]) == decision
        and entry == decision + 3 * MINUTE and dl._instant(label["exit_at"]) == entry + 30 * MINUTE,
        "source_original_signal_entry_or_cap_clock_changed")
    pair = row["breakout_exit_policy"]
    _require(isinstance(pair, Mapping) and set(pair) == {"common", "time_only", "managed", "touch_diagnostic"},
        "source_original_exit_pair_schema_changed")
    common, timed = pair["common"], pair["time_only"]
    _require(isinstance(common, Mapping) and isinstance(timed, Mapping) and isinstance(pair["managed"], Mapping)
        and isinstance(pair["touch_diagnostic"], Mapping)
        and dl._instant(label["time_cap_decided_at"]) == decision
        and dl._instant(label["entry_price_available_at"]) == entry + 3 * MINUTE
        and dl._instant(label["label_available_at"]) == cap + 3 * MINUTE
        and dl._instant(common["decision_at"]) == decision and dl._instant(common["entry_at"]) == entry
        and dl._instant(common["time_cap_at"]) == cap and dl._instant(common["time_cap_decided_at"]) == decision
        and dl._instant(common["entry_available_at"]) == entry + 3 * MINUTE
        and timed["status"] == "known" and dl._instant(timed["exit_at"]) == cap
        and dl._instant(timed["label_available_at"]) == cap + 3 * MINUTE,
        "source_original_label_clock_changed")
    _same(common["raw_chunk_metadata"], row["raw_chunk_metadata"], "source_original_pair_metadata_changed")
    _same(common["data_vintage"], row["data_vintage"], "source_original_pair_vintage_changed")
    _require(_opening(row["session"]) <= end - 15 * MINUTE < end <= _closing(row["session"]),
        "source_context_window_outside_session")
    result = {"session": row["session"], "symbol": row["symbol"],
        "decision_at": event["decision_at"], "signal_bar_end": event["signal_bar_end"],
        "entry_at": label["entry_at"], "window_start": (end - 15 * MINUTE).isoformat(),
        "window_end": end.isoformat(), "feature_available_at": decision.isoformat(),
        "decision_slack_seconds": 0, "entry_slack_seconds": 180,
        "source_row_sha256": _digest(row, checkpoint), "peers": []}
    for key in ("data_vintage", "raw_chunk_metadata", "source_fetched_at"):
        result[key] = deepcopy(row[key])
    result["membership_target"], result["membership_source"] = member, membership
    return result


def _source(source: Mapping, checkpoint: Callable[[], None]) -> tuple[list, list, dict, dict]:
    reference, weeks = dl._source_header(source)
    by_key, variants = {}, {}
    for policy in POLICIES:
        variant = source["variants"][policy]
        _require(set(variant) == {"days", "source_variant_provenance", "by_arm", "comparison"},
            "source_variant_schema_changed")
        _require(isinstance(variant["source_variant_provenance"], Mapping)
            and set(variant["by_arm"]) == {"time_only", "managed"}
            and all(isinstance(value, Mapping) for value in variant["by_arm"].values())
            and isinstance(variant["comparison"], Mapping), "source_prior_receipt_schema_changed")
        matrix, rows = source["source_target_matrix"][policy]["days"], variant["days"]
        _require([membership["session"] for membership in matrix] == reference,
            "source_target_calendar_changed")
        expected = []
        for membership in matrix:
            _membership(membership, membership["session"])
            # These output-owned snapshots are never mutated. All events and
            # peers for this policy/day may retain them without copying the
            # same nested source provenance once per event/peer.
            members = deepcopy(membership["targets"])
            provenance = deepcopy({key: value for key, value in membership.items() if key != "targets"})
            expected.extend((membership, target, member, members, provenance)
                for target, member in zip(membership["targets"], members, strict=True))
        _require(len(rows) == len(expected), "source_target_population_changed")
        population, events = dl._empty_population(), []
        for row, (membership, target, member, members, provenance) in zip(rows, expected, strict=True):
            checkpoint()
            day, symbol = membership["session"], target["symbol"]
            _require(row["session"] == day and row["symbol"] == symbol
                and dl._SYMBOL.fullmatch(symbol) is not None, "source_target_identity_changed")
            _same(row["membership_target"], target, "source_membership_target_changed")
            _same(row["membership_source"], {key: value for key, value in membership.items() if key != "targets"},
                "source_membership_metadata_changed")
            _require(isinstance(row["data_vintage"], str) and dl._SHA.fullmatch(row["data_vintage"]) is not None,
                "source_vintage_changed")
            selected = dl._row_state(row)
            _require((row["breakout_exit_policy"] is not None) == selected,
                "source_original_book_changed")
            dl._account(population, row, selected)
            by_key.setdefault((day, symbol), []).append(row)
            if selected:
                audit = _event(row, member, provenance, checkpoint)
                # Only original member coordinates define requests; no source outcome is evaluated.
                audit["_members"], audit["_membership"] = members, provenance
                events.append(audit)
        variants[policy] = {"population": population, "events": events,
            "source_rows_sha256": _digest(rows, checkpoint),
            "source_variant_sha256": _digest({key: value for key, value in variant.items() if key != "days"}, checkpoint)}
    return list(reference), weeks, by_key, variants


def _minutes(raw: list, day: str, symbol: str, checkpoint: Callable[[], None]) -> tuple[dict, dict, list]:
    buckets, offsets, fetched = {}, Counter(), set()
    for index, row in enumerate(raw):
        if index % 128 == 0:
            checkpoint()
        _require(isinstance(row, Mapping) and set(row) == set(COLUMNS), "invalid_raw_row_schema")
        stamp = row["open_time_ms"]
        _require(type(stamp) is int, "invalid_raw_timestamp")
        minute = datetime.fromtimestamp(stamp // 1000, KST).replace(second=0, microsecond=0)
        _require(row["symbol"] == symbol and row["interval"] == "1m"
            and _opening(day) <= minute < _closing(day), "raw_row_outside_registered_scope")
        _require(row["fetched_at"] is None or isinstance(row["fetched_at"], str), "invalid_raw_retrieval_clock")
        if row["fetched_at"] is not None:
            dl._instant(row["fetched_at"])
            fetched.add(row["fetched_at"])
        buckets.setdefault(minute, []).append(row)
        offsets[str(stamp % 60000)] += 1
    return buckets, dict(sorted(offsets.items())), sorted(fetched)


def _valid(row: Mapping) -> bool:
    try:
        text = [row[key] for key in ("open", "high", "low", "close")]
        if not all(isinstance(value, str) for value in text):
            return False
        op, high, low, close = map(Decimal, text)
        return all(value.is_finite() and value > 0 for value in (op, high, low, close)) and low <= min(op, close) <= max(op, close) <= high
    except (KeyError, ValueError, InvalidOperation):
        return False


def _window(event: Mapping, buckets: Mapping) -> dict:
    start, end, decision, entry = (dl._instant(event[key]) for key in
        ("window_start", "window_end", "decision_at", "entry_at"))
    gaps, minute_offsets, counts, values = [], [], Counter(), []
    for index in range(15):
        minute = start + index * MINUTE
        _require(minute + 3 * MINUTE <= decision, "noncausal_observation_availability")
        rows = buckets.get(minute, [])
        original_offsets = [row["open_time_ms"] % 60000 for row in rows]
        minute_offsets.append(original_offsets)
        reason = "missing" if not rows else "conflict" if len(rows) != 1 else "invalid_price" if not _valid(rows[0]) else None
        if reason is not None:
            counts[reason] += 1
            gaps.append({"minute": minute.isoformat(), "state": reason,
                "source_offsets_ms": original_offsets,
                "source_fetched_at": [row["fetched_at"] for row in rows]})
        values.append(rows[0] if reason is None else None)
    complete = not gaps
    return {"window_start": start.isoformat(), "window_end": end.isoformat(),
        "first_open": values[0]["open"] if complete else None,
        "last_close": values[-1]["close"] if complete else None,
        "state": "complete" if complete else "unknown", "valid_minute_buckets": 15 - len(gaps),
        "unknown_minute_buckets": len(gaps), "gap_counts": {key: counts[key] for key in GAP_STATES},
        "gaps": gaps, "observation_offsets_ms": minute_offsets,
        "available_at": decision.isoformat(), "decision_slack_seconds": 0,
        "entry_slack_seconds": int((entry - decision).total_seconds())}


def _peer(event: Mapping, member: Mapping, metadata: Mapping, vintage: str,
        offsets: dict, fetched: list, atoms: Mapping) -> dict:
    return {"symbol": member["symbol"], "member": member,
        "membership_source": event["_membership"], "data_vintage": vintage,
        "raw_chunk_metadata": metadata, "source_fetched_at": fetched,
        "source_offset_counts_ms": offsets, **atoms}


def _empty_summary() -> dict:
    return {"events": 0, "quorum_usable_events": 0, "feature_unknown_events": 0,
        "full_coverage_events": 0, "complete_peer_windows_distribution": {str(index): 0 for index in range(20)},
        "gap_counts": {key: 0 for key in GAP_STATES}, "unknown_reason_counts": {}}


def _add_summary(summary: dict, event: Mapping) -> None:
    summary["events"] += 1
    summary["quorum_usable_events"] += event["quorum_usable"]
    summary["feature_unknown_events"] += not event["quorum_usable"]
    summary["full_coverage_events"] += event["full_coverage"]
    summary["complete_peer_windows_distribution"][str(event["complete_peer_windows"])] += 1
    for peer in event["peers"]:
        for key in GAP_STATES:
            summary["gap_counts"][key] += peer["gap_counts"][key]
    for reason in event["unknown_reasons"]:
        summary["unknown_reason_counts"][reason] = summary["unknown_reason_counts"].get(reason, 0) + 1


def build_readiness(dk_report: Mapping, raw_lines: Iterable[bytes], *, specification: Mapping | None = None,
        checkpoint: Callable[[], None] | None = None) -> dict:
    """Consume one bounded original stream; retain every original-book event.

    Smaller invented calendars are supported. Actual pins and fixed complete
    geometry are the separately bounded authenticated caller's obligations.
    """
    if specification is not None:
        validate_spec(specification)
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        reference, weeks, by_key, variants = _source(dk_report, checkpoint)
        expected = sorted(by_key)
        _require(len(expected) <= RESOURCES["maximum_chunks"], "input_chunk_budget_exceeded")
        requests = {}
        for variant in variants.values():
            for event in variant["events"]:
                for member in event["_members"]:
                    if member["symbol"] != event["symbol"]:
                        requests.setdefault((event["session"], member["symbol"]), []).append((event, member))
        stream, fingerprints, total_bytes, total_rows = hashlib.sha256(), [], 0, 0
        lines = iter(raw_lines)
        for day, symbol in expected:
            checkpoint()
            try:
                line = next(lines)
            except StopIteration as exc:
                raise MarketContextReadinessRefusal("missing_input_chunk") from exc
            _require(type(line) is bytes and len(line) <= RESOURCES["maximum_chunk_bytes"]
                and line.endswith(b"\n"), "input_chunk_not_original_canonical_framing")
            total_bytes += len(line)
            _require(total_bytes <= RESOURCES["maximum_uncompressed_bytes"], "input_stream_byte_budget_exceeded")
            chunk = json.loads(line)
            _require(isinstance(chunk, Mapping) and line == _canonical(chunk) + b"\n",
                "input_chunk_not_original_canonical_framing")
            _require(chunk["session"] == day and chunk["symbol"] == symbol, "input_chunk_order_or_identity_mismatch")
            _require(chunk["observation_date"] == day and chunk["available_at"] is None
                and chunk["source"] == "collector klines" and chunk["is_final"] is None
                and chunk["evidence_level"] == "assumed", "input_chunk_metadata_mismatch")
            dl._instant(chunk["retrieved_at"])
            raw = chunk["rows"]
            _require(isinstance(raw, list) and len(raw) <= RESOURCES["maximum_session_rows"], "input_session_row_budget_exceeded")
            total_rows += len(raw)
            _require(total_rows <= RESOURCES["maximum_rows"], "input_row_budget_exceeded")
            vintage = hashlib.sha256(line).hexdigest()
            stream.update(line)
            buckets, offsets, fetched = _minutes(raw, day, symbol, checkpoint)
            metadata = deepcopy({key: chunk[key] for key in METADATA})
            for row in by_key[day, symbol]:
                _require(row["data_vintage"] == vintage and row["input_rows"] == len(raw), "source_raw_vintage_or_count_mismatch")
                _same(row["raw_chunk_metadata"], metadata, "source_raw_metadata_mismatch")
                _same(row["source_offset_counts_ms"], offsets, "source_raw_offsets_mismatch")
                _same(row["source_fetched_at"], fetched, "source_raw_retrieval_mismatch")
            fingerprints.append({"session": day, "symbol": symbol, "data_vintage": vintage, "input_rows": len(raw)})
            window_atoms = {}
            for event, member in requests.get((day, symbol), []):
                window_key = event["window_start"], event["window_end"]
                if window_key not in window_atoms:
                    window_atoms[window_key] = _window(event, buckets)
                event["peers"].append(_peer(event, member, metadata, vintage,
                    offsets, fetched, window_atoms[window_key]))
        sentinel = object()
        _require(next(lines, sentinel) is sentinel, "extra_input_chunk")
        for variant in variants.values():
            summary = {"overall": _empty_summary(), "by_week": {week: _empty_summary() for week in weeks},
                "by_session": {day: _empty_summary() for day in reference}}
            for event in variant["events"]:
                event["peers"].sort(key=lambda peer: peer["member"]["rank"])
                _require(len(event["peers"]) == 19, "event_original_peer_population_changed")
                complete = sum(peer["state"] == "complete" for peer in event["peers"])
                event.update(complete_peer_windows=complete, quorum_usable=complete >= 5, full_coverage=complete == 19,
                    unknown_reasons=[] if complete >= 5 else ["fewer_than_five_complete_peer_windows"])
                del event["_members"], event["_membership"]
                for cell in (summary["overall"], summary["by_week"][dl._week(event["session"])], summary["by_session"][event["session"]]):
                    _add_summary(cell, event)
            variant["summary"] = summary
        identity = {"completed_DK_sha256": UPSTREAM_SHA256, "DK_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
            "canonical_source_sha256": _digest(dk_report, checkpoint),
            "source_target_matrix_sha256": _digest(dk_report["source_target_matrix"], checkpoint),
            "upstream_calendar_sha256": _digest(dk_report["upstream_calendar"], checkpoint),
            "source_metadata_sha256": _digest({key: value for key, value in dk_report.items()
                if key not in {"variants", "source_target_matrix", "upstream_calendar"}}, checkpoint)}
    except (KeyError, TypeError, ValueError, OverflowError, InvalidOperation, dl.BreakoutLossConcentrationRefusal) as exc:
        raise MarketContextReadinessRefusal("malformed_or_mismatched_source") from exc
    computable = {policy: variants[policy]["summary"]["overall"]["quorum_usable_events"] for policy in POLICIES}
    checkpoint()
    return {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "parameters": deepcopy(PARAMETERS), "assumptions": dict(ASSUMPTIONS), **FLAGS,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256, "source_identity": identity,
        "reference_sessions": reference, "reference_weeks": weeks, "variants": variants,
        "input_evidence": {"unique_target_days": len(expected), "queried_rows": total_rows,
            "stream_sha256": stream.hexdigest(), "stream_bytes": total_bytes,
            "chunk_fingerprints_sha256": _digest(fingerprints, checkpoint)},
        "next_hypothesis": {**deepcopy(NEXT_HYPOTHESIS),
            "status": "fixed_for_future_calibration" if all(computable.values()) else "not_data_ready",
            "basis": {"computable_events_by_policy": computable}},
        "pin_authentication": "caller_obligation_not_proven_by_pure_builder"}
