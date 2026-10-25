"""Fresh own-label calibration of the fixed causal observed-peer hypothesis.

The caller authenticates the completed DK/DQ bytes.  DK is projected once and
may then be released; DQ events are consumed one at a time without retaining
their large peer ledgers.  This module performs no source acquisition or replay.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from fractions import Fraction
import math
from statistics import median

from research import intraday_breakout_close_calibration as dm
from research import intraday_breakout_loss_concentration as dl
from research import intraday_market_context_readiness as market


ROOT = dl.ROOT
SPECIFICATION_ID = "intraday-market-context-calibration-v1"
MODE = "fixed-original-basic-breakout-positive-observed-peer-own-label-calibration"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-market-context-calibration-v1.json"
OUTPUT_NAME = "intraday-dr-market-context-calibration-20261025-v1"
WINDOW, POLICIES = dict(dl.WINDOW), dl.POLICIES
UPSTREAM_SHA256 = "23b25931f160aa91644389caf6aa9aa0095e034e705e67e9f6680c6ee08647ba"
UPSTREAM_SPECIFICATION_SHA256 = "9346cad4655e96cb5d172049f561d1b695b1a802aa9c0cd37d077b707846b5b3"
ORIGINAL_DK_SHA256 = dl.UPSTREAM_SHA256
ORIGINAL_DK_SPECIFICATION_SHA256 = dl.UPSTREAM_SPECIFICATION_SHA256
SOURCE_ROLES = {"parent": "pinned_completed_full_DQ_causal_observed_peer_readiness_report",
    "original_book": "pinned_completed_full_DK_basic_breakout_exit_policy_report"}
NEXT_HYPOTHESIS = deepcopy(market.NEXT_HYPOTHESIS)
RESOURCES = {"maximum_source_bytes": 268435456, "maximum_original_source_bytes": 67108864,
    "maximum_report_bytes": 67108864, "cpu_seconds": 300, "address_space_mib": 512,
    "wall_seconds": 1200, "nice": 10, "minimum_free_bytes": 1073741824,
    "maximum_market_gets": 0, "maximum_token_posts": 0, "automatic_retries": 0}
EXPECTED_GEOMETRY = {"reference_sessions": 257, "reference_weeks": 56,
    "targets_per_policy": 5140, "original_book_events": {"D+1": 1169, "D+2": 1167}}
PARAMETERS = {
    "next_hypothesis": NEXT_HYPOTHESIS,
    "cohort": "same_original_DK_common_outcome_complete_basic_time_only_book_no_new_outcome_exclusion",
    "all_targets": "retain_original_targets_masks_labels_unknowns_and_outside_book_without_feature_evaluation",
    "peer_rule": "all_complete_other_original_policy_day20_members_no_replacement_or_return_rank_selection",
    "window_minutes": 15, "minimum_complete_peer_windows": 5,
    "predicate": "exact_equal_weight_mean_last_CLOSE_over_first_OPEN_minus1_strictly_positive_zero_rejected_subquorum_unknown",
    "entry_delay_minutes": 3, "time_cap_minutes": 30,
    "assumed_cost": {"numerator": "33", "denominator": "10000"},
    "block_sessions": 5, "bootstrap_draws": 2000, "bootstrap_seed": 0,
    "sizing": "CC_exact_eventSD_naiveSE_all_reference_session_clusterSE_circular5session_bootstrap_maxSE_normal_two_sided5percent_power80percent",
    "weekly_operating_objective": 15,
    "frequency": "all_reference_weeks_including_zero_selected_unknown_outside_book_loose_bound_not_portfolio_entries",
    "future_comparison_gate": "finish_calibration_regardless_unknown_book_features_selected_labels_undefined_nonfinite_or_above33bp_MDE_requires_new_operator_choice_no_old_waiver",
    "output_scope": "frequency_missingness_fresh_selected_own_label_dispersion_only_no_selected_outcome_mean_net_winrate_contrast",
    "expected_actual_geometry": EXPECTED_GEOMETRY,
}
ASSUMPTIONS = {
    "source": "caller_authenticated_completed_DQ_and_original_DK_only_no_parent_replay_archive_API_DB_or_new_source",
    "identity": "original_DK_full_row_metadata_matrix_calendar_hashes_joined_to_each_DQ_event_and_all19_peer_provenance",
    "population": "unchanged_original_common_complete_basic_time_only_book_all_targets_and_unknowns_retained",
    "availability": "original_Fplus2_decision_all_minute_end_plus2_at_most_D_entry_Dplus3_OPEN_cap_entryplus30_OPEN",
    "basket": "observed_other_original_members_not_official_KOSPI_KOSDAQ_index_or_complete_market",
    "quorum": "fixed_five_complete_peer_windows_operational_definition_not_representativeness_certificate",
    "measurement": "original_selected_own_price_labels_conditional_not_certified_fills_account_entries_or_portfolio_returns",
    "cost": "33bp_assumed_planning_floor_not_verified_historical_cost",
    "policies": "overlapping_Dplus1_Dplus2_input_timing_assumptions_not_independent_replications",
    "claims": "calibration_only_no_outcome_mean_comparison_edge_direction_closure_reserved_access_or_promotion",
}
FLAGS = {**market.FLAGS, "readiness_only": False, "calibration_only": True,
    "candidate_measured": True, "peer_returns_calculated": True,
    "peer_return_mean_or_sign_calculated": True, "own_label_sizing_calculated": True,
    "new_selection_calculated": True, "resampling_performed": True,
    "original_signal_detector_replayed": False, "subgroup_return_means_calculated": False}
STATES = ("selected", "rejected", "unknown", "not_applicable", "outside_original_book")
_MINUTE = market.MINUTE
_canonical, _digest = market._canonical, market._digest


class MarketContextCalibrationRefusal(RuntimeError):
    """Stable refusals never expose supplied private evidence."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise MarketContextCalibrationRefusal(reason)


def _same(left: object, right: object, reason: str) -> None:
    _require(_canonical(left) == _canonical(right), reason)


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "output_name": OUTPUT_NAME,
        "window": WINDOW, "source_roles": SOURCE_ROLES, "upstream_sha256": UPSTREAM_SHA256,
        "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
        "original_DK_sha256": ORIGINAL_DK_SHA256,
        "original_DK_specification_sha256": ORIGINAL_DK_SPECIFICATION_SHA256,
        "parameters": PARAMETERS, "resources": RESOURCES}
    try:
        valid = _canonical(spec) == _canonical(expected)
    except (TypeError, ValueError):
        valid = False
    _require(valid, "specification_scope_mismatch")


def _metadata(row: Mapping) -> None:
    metadata = row["raw_chunk_metadata"]
    _require(set(metadata) == set(market.METADATA)
        and metadata["observation_date"] == row["session"] and metadata["available_at"] is None
        and metadata["source"] == "collector klines" and metadata["is_final"] is None
        and metadata["evidence_level"] == "assumed", "source_raw_metadata_changed")
    dl._instant(metadata["retrieved_at"])
    _require(isinstance(row["data_vintage"], str) and dl._SHA.fullmatch(row["data_vintage"]) is not None,
        "source_vintage_changed")
    offsets, fetched = row["source_offset_counts_ms"], row["source_fetched_at"]
    _require(type(row["input_rows"]) is int and 0 <= row["input_rows"] <= 600
        and isinstance(offsets, Mapping) and all(isinstance(key, str) and key.isdecimal()
            and str(int(key)) == key and 0 <= int(key) < 60000
            and type(value) is int and value > 0 for key, value in offsets.items())
        and sum(offsets.values()) == row["input_rows"], "source_raw_offsets_changed")
    _require(isinstance(fetched, list) and fetched == sorted(set(fetched))
        and all(isinstance(value, str) for value in fetched), "source_raw_retrieval_changed")
    for value in fetched:
        dl._instant(value)


def project_dk(dk_report: Mapping, checkpoint: Callable[[], None] | None = None) -> dict:
    """Validate original masks/labels, retaining no old feature or statistic body."""
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        reference, weeks = dl._source_header(dk_report)
        matrix = deepcopy(dk_report["source_target_matrix"])
        variants = {}
        for policy in POLICIES:
            variant = dk_report["variants"][policy]
            _require(set(variant) == {"days", "source_variant_provenance", "by_arm", "comparison"}
                and isinstance(variant["source_variant_provenance"], Mapping)
                and set(variant["by_arm"]) == {"time_only", "managed"}
                and all(isinstance(value, Mapping) for value in variant["by_arm"].values())
                and isinstance(variant["comparison"], Mapping), "source_variant_schema_changed")
            memberships = matrix[policy]["days"]
            _require([item["session"] for item in memberships] == reference, "source_target_calendar_changed")
            expected = []
            for membership in memberships:
                day, targets = membership["session"], membership["targets"]
                _require(dl._instant(membership["available_at"]) <= dl._instant(membership["selected_at"])
                    < dm._opening(day) and len(targets) == 20
                    and all(type(item["rank"]) is int for item in targets)
                    and {item["rank"] for item in targets} == set(range(1, 21))
                    and len({item["symbol"] for item in targets}) == 20, "source_original20_members_changed")
                provenance = {key: value for key, value in membership.items() if key != "targets"}
                expected.extend((day, target, provenance) for target in targets)
            rows = variant["days"]
            _require(isinstance(rows, list) and len(rows) == len(expected), "source_target_population_changed")
            population, targets = dl._empty_population(), []
            for row, (day, member, provenance) in zip(rows, expected, strict=True):
                checkpoint()
                _require(row["session"] == day and row["symbol"] == member["symbol"]
                    and dl._SYMBOL.fullmatch(row["symbol"]) is not None, "source_target_identity_changed")
                _same(row["membership_target"], member, "source_membership_target_changed")
                _same(row["membership_source"], provenance, "source_membership_metadata_changed")
                _metadata(row)
                in_book = dl._row_state(row)
                _require((row["breakout_exit_policy"] is not None) == in_book, "source_original_book_changed")
                dl._account(population, row, in_book)
                label = dm._original_label(row, in_book)
                basic = row["arms"]["basic"]
                original_event = None if basic["event"] is None else {
                    key: deepcopy(basic["event"][key]) for key in
                    ("signal_bar_start", "signal_bar_end", "decision_at", "available_at")}
                item = {"session": day, "symbol": row["symbol"], "in_original_book": in_book,
                    "source_row_sha256": _digest(row, checkpoint), "basic_status": basic["status"],
                    "retest_status": row["arms"]["retest"]["status"],
                    "basic_label_status": basic["label"]["status"] if basic["label"] is not None else "not_applicable",
                    "signal_observable": row["signal_observable"], "outcome_complete": row["outcome_complete"],
                    "exclusion_reasons": list(row["exclusion_reasons"]), "original_event": original_event,
                    "original_label": deepcopy(basic["label"]), "label": label,
                    "membership_target": member, "membership_source": provenance}
                for key in ("data_vintage", "raw_chunk_metadata", "source_fetched_at", "source_offset_counts_ms", "input_rows"):
                    item[key] = deepcopy(row[key])
                targets.append(item)
            variants[policy] = {"population": population, "targets": targets,
                "source_rows_sha256": _digest(rows, checkpoint),
                "source_variant_sha256": _digest({key: value for key, value in variant.items() if key != "days"}, checkpoint)}
        identity = {"completed_DK_sha256": ORIGINAL_DK_SHA256,
            "DK_specification_sha256": ORIGINAL_DK_SPECIFICATION_SHA256,
            "canonical_source_sha256": _digest(dk_report, checkpoint),
            "source_target_matrix_sha256": _digest(dk_report["source_target_matrix"], checkpoint),
            "upstream_calendar_sha256": _digest(dk_report["upstream_calendar"], checkpoint),
            "source_metadata_sha256": _digest({key: value for key, value in dk_report.items()
                if key not in {"variants", "source_target_matrix", "upstream_calendar"}}, checkpoint)}
        return {"source_identity": identity, "reference_sessions": list(reference), "reference_weeks": weeks,
            "source_target_matrix": matrix, "upstream_calendar": deepcopy(dk_report["upstream_calendar"]),
            "variants": variants}
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError,
            dl.BreakoutLossConcentrationRefusal, dm.BreakoutCloseCalibrationRefusal) as exc:
        raise MarketContextCalibrationRefusal("malformed_or_mismatched_completed_DK_source") from exc


def _cell() -> dict:
    return {"targets": 0, "original_book_events": 0, "state_counts": dict.fromkeys(STATES, 0),
        "selected_known_labels": 0, "selected_unknown_labels": 0}


def _summary() -> dict:
    return {"events": 0, "quorum_usable_events": 0, "feature_unknown_events": 0,
        "full_coverage_events": 0, "complete_peer_windows_distribution": {str(index): 0 for index in range(20)},
        "gap_counts": dict.fromkeys(market.GAP_STATES, 0), "unknown_reason_counts": {}}


def _add_summary(cell: dict, event: Mapping) -> None:
    cell["events"] += 1
    cell["quorum_usable_events"] += event["quorum_usable"]
    cell["feature_unknown_events"] += not event["quorum_usable"]
    cell["full_coverage_events"] += event["full_coverage"]
    cell["complete_peer_windows_distribution"][str(event["complete_peer_windows"])] += 1
    for peer in event["peers"]:
        for reason in market.GAP_STATES:
            cell["gap_counts"][reason] += peer["gap_counts"][reason]
    for reason in event["unknown_reasons"]:
        cell["unknown_reason_counts"][reason] = cell["unknown_reason_counts"].get(reason, 0) + 1


def _window(peer: Mapping, event: Mapping) -> Fraction | None:
    """Validate stored causal atoms before computing a complete peer return."""
    start, end, decision, entry = (dl._instant(event[key]) for key in
        ("window_start", "window_end", "decision_at", "entry_at"))
    _require(dl._instant(peer["window_start"]) == start and dl._instant(peer["window_end"]) == end
        and end - start == 15 * _MINUTE and end + 2 * _MINUTE == decision
        and entry == decision + 3 * _MINUTE and dl._instant(peer["available_at"]) == decision
        and type(peer["decision_slack_seconds"]) is int and peer["decision_slack_seconds"] == 0
        and type(peer["entry_slack_seconds"]) is int and peer["entry_slack_seconds"] == 180,
        "peer_window_or_availability_changed")
    offsets, gaps, counts = peer["observation_offsets_ms"], peer["gaps"], peer["gap_counts"]
    _require(isinstance(offsets, list) and len(offsets) == 15
        and all(isinstance(items, list) and all(type(offset) is int and 0 <= offset < 60000
            for offset in items) for items in offsets), "peer_minute_offsets_changed")
    _require(isinstance(gaps, list) and len(gaps) <= 15 and set(counts) == set(market.GAP_STATES)
        and all(type(value) is int and 0 <= value <= 15 for value in counts.values())
        and type(peer["valid_minute_buckets"]) is int and type(peer["unknown_minute_buckets"]) is int
        and peer["unknown_minute_buckets"] == len(gaps) == sum(counts.values())
        and peer["valid_minute_buckets"] == 15 - len(gaps), "peer_gap_accounting_changed")
    observed, gap_indices, actual_counts = Counter(), [], Counter()
    for index, items in enumerate(offsets):
        _require(start + (index + 3) * _MINUTE <= decision, "peer_uses_future_observation")
        observed.update(str(offset) for offset in items)
    _require(all(count <= peer["source_offset_counts_ms"].get(offset, 0)
        for offset, count in observed.items()), "peer_window_offsets_exceed_original_source")
    for gap in gaps:
        _require(set(gap) == {"minute", "state", "source_offsets_ms", "source_fetched_at"}, "peer_gap_schema_changed")
        elapsed = dl._instant(gap["minute"]) - start
        _require(elapsed % _MINUTE == _MINUTE * 0 and _MINUTE * 0 <= elapsed < 15 * _MINUTE,
            "peer_gap_minute_changed")
        index = elapsed // _MINUTE
        reason = gap["state"]
        _require(reason in market.GAP_STATES and gap["source_offsets_ms"] == offsets[index]
            and (len(offsets[index]) == 0 if reason == "missing" else
                len(offsets[index]) >= 2 if reason == "conflict" else len(offsets[index]) == 1),
            "peer_gap_cause_or_offsets_changed")
        retrievals = gap["source_fetched_at"]
        _require(isinstance(retrievals, list) and len(retrievals) == len(offsets[index])
            and all(value is None or value in peer["source_fetched_at"] for value in retrievals),
            "peer_gap_retrieval_changed")
        gap_indices.append(index)
        actual_counts[reason] += 1
    _require(gap_indices == sorted(set(gap_indices))
        and all(counts[reason] == actual_counts[reason] for reason in market.GAP_STATES)
        and all(len(items) == 1 for index, items in enumerate(offsets) if index not in gap_indices),
        "peer_minute_state_changed")
    if gaps:
        _require(peer["state"] == "unknown" and peer["first_open"] is None and peer["last_close"] is None,
            "unknown_peer_has_price_atoms")
        return None
    _require(peer["state"] == "complete" and isinstance(peer["first_open"], str)
        and isinstance(peer["last_close"], str), "complete_peer_atoms_missing")
    return dl._price(peer["last_close"]) / dl._price(peer["first_open"]) - 1


_EVENT_KEYS = {"session", "symbol", "decision_at", "signal_bar_end", "entry_at", "window_start", "window_end",
    "feature_available_at", "decision_slack_seconds", "entry_slack_seconds", "source_row_sha256", "peers",
    "data_vintage", "raw_chunk_metadata", "source_fetched_at", "membership_target", "membership_source",
    "complete_peer_windows", "quorum_usable", "full_coverage", "unknown_reasons"}
_PEER_KEYS = {"symbol", "member", "membership_source", "data_vintage", "raw_chunk_metadata", "source_fetched_at",
    "source_offset_counts_ms", "window_start", "window_end", "first_open", "last_close", "state",
    "valid_minute_buckets", "unknown_minute_buckets", "gap_counts", "gaps", "observation_offsets_ms",
    "available_at", "decision_slack_seconds", "entry_slack_seconds"}


def _feature(event: Mapping, original: Mapping, members: list, peers_by_symbol: Mapping) -> tuple[str, str | None, Fraction | None]:
    """The predicate accepts causal peer atoms, original clocks and provenance only."""
    _require(set(event) == _EVENT_KEYS and event["session"] == original["session"]
        and event["symbol"] == original["symbol"], "context_event_schema_or_identity_changed")
    for key in ("source_row_sha256", "data_vintage", "raw_chunk_metadata", "source_fetched_at",
            "membership_target", "membership_source"):
        _same(event[key], original[key], "context_event_original_lineage_changed")
    clock = original["original_event"]
    end, decision = dl._instant(clock["signal_bar_end"]), dl._instant(clock["decision_at"])
    _require(dl._instant(event["signal_bar_end"]) == end and dl._instant(event["decision_at"]) == decision
        and dl._instant(event["entry_at"]) == decision + 3 * _MINUTE
        and dl._instant(event["window_start"]) == end - 15 * _MINUTE
        and dl._instant(event["window_end"]) == end and dl._instant(event["feature_available_at"]) == decision
        and end + 2 * _MINUTE == decision
        and type(event["decision_slack_seconds"]) is int and event["decision_slack_seconds"] == 0
        and type(event["entry_slack_seconds"]) is int and event["entry_slack_seconds"] == 180
        and dm._opening(original["session"]) <= end - 15 * _MINUTE < end <= market._closing(original["session"]),
        "context_event_original_clock_changed")
    expected = sorted((member for member in members if member["symbol"] != original["symbol"]), key=lambda member: member["rank"])
    supplied = event["peers"]
    _require(isinstance(supplied, list) and len(supplied) == len(expected) == 19,
        "context_original_peer_population_changed")
    returns = []
    for peer, member in zip(supplied, expected, strict=True):
        _require(isinstance(peer, Mapping) and set(peer) == _PEER_KEYS and peer["symbol"] == member["symbol"],
            "context_original_peer_order_or_identity_changed")
        _same(peer["member"], member, "context_peer_member_changed")
        _same(peer["membership_source"], original["membership_source"], "context_peer_membership_changed")
        source_peer = peers_by_symbol[member["symbol"]]
        for key in ("data_vintage", "raw_chunk_metadata", "source_fetched_at", "source_offset_counts_ms"):
            _same(peer[key], source_peer[key], "context_peer_source_provenance_changed")
        value = _window(peer, event)
        if value is not None:
            returns.append(value)
    complete = len(returns)
    _require(type(event["complete_peer_windows"]) is int and event["complete_peer_windows"] == complete
        and type(event["quorum_usable"]) is bool and event["quorum_usable"] == (complete >= 5)
        and type(event["full_coverage"]) is bool and event["full_coverage"] == (complete == 19)
        and event["unknown_reasons"] == ([] if complete >= 5 else ["fewer_than_five_complete_peer_windows"]),
        "context_quorum_or_missingness_changed")
    if complete < 5:
        return "unknown", "fewer_than_five_complete_peer_windows", None
    mean = sum(returns, Fraction()) / complete
    return ("selected", None, mean) if mean > 0 else ("rejected", None, mean)


class Calibrator:
    """Bounded callback sink: no supplied DQ event is retained after its call."""

    def __init__(self, projection: Mapping, *, specification: Mapping | None = None,
            checkpoint: Callable[[], None] | None = None):
        if specification is not None:
            validate_spec(specification)
        self.checkpoint = checkpoint or (lambda: None)
        self.checkpoint()
        self.projection = projection
        self.reference, self.weeks = projection["reference_sessions"], projection["reference_weeks"]
        self.finished = False
        self.offsets = dict.fromkeys(POLICIES, 0)
        self.events, self.targets, self.members, self.source_rows, self.summaries = {}, {}, {}, {}, {}
        for policy in POLICIES:
            rows = projection["variants"][policy]["targets"]
            self.events[policy] = [row for row in rows if row["in_original_book"]]
            self.targets[policy] = []
            self.members[policy] = {item["session"]: item["targets"] for item in
                projection["source_target_matrix"][policy]["days"]}
            self.source_rows[policy] = {}
            for row in rows:
                self.source_rows[policy].setdefault(row["session"], {})[row["symbol"]] = row
                state, reason = ("unknown", "original_signal_unknown") if row["basic_status"] == "unknown" else (
                    ("not_applicable", None) if row["basic_status"] == "known_no_signal" else (
                    ("unknown", "context_event_not_received") if row["in_original_book"] else
                    ("outside_original_book", "original_common_complete_exclusion")))
                item = {key: deepcopy(value) for key, value in row.items() if key != "label"}
                item.update(state=state, reason=reason, feature_evaluated=False, feature_available_at=None,
                    feature_availability_evidence_level=None, complete_peer_windows=None,
                    observed_peer_mean_price_change=None, source_context_event_sha256=None)
                self.targets[policy].append(item)
            self.summaries[policy] = {"overall": _summary(), "by_week": {week: _summary() for week in self.weeks},
                "by_session": {day: _summary() for day in self.reference}}
        self.target_by_key = {policy: {(item["session"], item["symbol"]): item for item in self.targets[policy]}
            for policy in POLICIES}

    def accept_event(self, policy: str, event: Mapping) -> None:
        self.checkpoint()
        _require(not self.finished, "calibrator_already_finished")
        _require(policy in POLICIES, "context_policy_changed")
        offset = self.offsets[policy]
        _require(offset < len(self.events[policy]), "extra_or_duplicate_context_event")
        original = self.events[policy][offset]
        try:
            causal = {key: original[key] for key in ("session", "symbol", "source_row_sha256", "data_vintage",
                "raw_chunk_metadata", "source_fetched_at", "membership_target", "membership_source", "original_event")}
            state, reason, mean = _feature(event, causal, self.members[policy][original["session"]],
                self.source_rows[policy][original["session"]])
            item = self.target_by_key[policy][original["session"], original["symbol"]]
            item.update(state=state, reason=reason, feature_evaluated=True,
                feature_available_at=event["feature_available_at"], feature_availability_evidence_level="assumed",
                complete_peer_windows=event["complete_peer_windows"], observed_peer_mean_price_change=mean,
                source_context_event_sha256=_digest(event, self.checkpoint))
            summary = self.summaries[policy]
            for cell in (summary["overall"], summary["by_week"][dl._week(original["session"])],
                    summary["by_session"][original["session"]]):
                _add_summary(cell, event)
            self.offsets[policy] += 1
        except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError,
                dl.BreakoutLossConcentrationRefusal) as exc:
            raise MarketContextCalibrationRefusal("malformed_or_mismatched_DQ_event") from exc

    def _header(self, header: Mapping) -> None:
        _require(header["study_id"] == market.SPECIFICATION_ID and header["mode"] == market.MODE
            and header["specification_sha256"] == UPSTREAM_SPECIFICATION_SHA256
            and header["upstream_specification_sha256"] == ORIGINAL_DK_SPECIFICATION_SHA256,
            "context_source_identity_or_specification_changed")
        for key, expected in (("window", WINDOW), ("parameters", market.PARAMETERS),
                ("assumptions", market.ASSUMPTIONS), ("source_identity", self.projection["source_identity"]),
                ("reference_sessions", self.reference), ("reference_weeks", self.weeks)):
            _same(header[key], expected, "context_source_scope_or_lineage_changed")
        for key, expected in market.FLAGS.items():
            _require(type(header.get(key)) is type(expected) and header[key] == expected, "context_source_claim_changed")
        _require(header["pin_authentication"] == "caller_obligation_not_proven_by_pure_builder"
            and set(header["variants"]) == set(POLICIES), "context_source_not_completed")
        computable = {}
        for policy in POLICIES:
            _require(self.offsets[policy] == len(self.events[policy]), "missing_context_event")
            variant = header["variants"][policy]
            _require(set(variant) == {"population", "source_rows_sha256", "source_variant_sha256", "summary"},
                "context_header_variant_schema_changed")
            for key in ("population", "source_rows_sha256", "source_variant_sha256"):
                _same(variant[key], self.projection["variants"][policy][key], "context_original_source_variant_changed")
            _same(variant["summary"], self.summaries[policy], "context_readiness_summary_changed")
            computable[policy] = self.summaries[policy]["overall"]["quorum_usable_events"]
        expected_hypothesis = {**deepcopy(NEXT_HYPOTHESIS),
            "status": "fixed_for_future_calibration" if all(computable.values()) else "not_data_ready",
            "basis": {"computable_events_by_policy": computable}}
        _same(header["next_hypothesis"], expected_hypothesis, "context_fixed_hypothesis_changed")
        unique = {}
        for policy in POLICIES:
            for row in self.projection["variants"][policy]["targets"]:
                key = row["session"], row["symbol"]
                fingerprint = {name: row[name] for name in ("session", "symbol", "data_vintage", "input_rows")}
                if key in unique:
                    _same(unique[key], fingerprint, "original_policy_raw_vintage_disagrees")
                unique[key] = fingerprint
        evidence = header["input_evidence"]
        _require(set(evidence) == {"unique_target_days", "queried_rows", "stream_sha256", "stream_bytes", "chunk_fingerprints_sha256"}
            and type(evidence["unique_target_days"]) is int and evidence["unique_target_days"] == len(unique)
            and type(evidence["queried_rows"]) is int
            and evidence["queried_rows"] == sum(row["input_rows"] for row in unique.values())
            and isinstance(evidence["stream_sha256"], str) and dl._SHA.fullmatch(evidence["stream_sha256"]) is not None
            and type(evidence["stream_bytes"]) is int and 0 < evidence["stream_bytes"] <= market.RESOURCES["maximum_uncompressed_bytes"],
            "context_input_evidence_changed")
        _require(evidence["chunk_fingerprints_sha256"] == _digest([unique[key] for key in sorted(unique)], self.checkpoint),
            "context_input_fingerprints_changed")

    def _variant(self, policy: str) -> dict:
        targets = self.targets[policy]
        states, reasons, records, selected = _cell(), Counter(), [], []
        by_session = {day: _cell() for day in self.reference}
        by_week = {week: _cell() for week in self.weeks}
        by_month = {month: _cell() for month in sorted({day[:7] for day in self.reference})}
        for item, original in zip(targets, self.projection["variants"][policy]["targets"], strict=True):
            self.checkpoint()
            state, label = item["state"], original["label"]
            if state == "unknown":
                reasons[item["reason"]] += 1
            for cell in (states, by_session[item["session"]], by_week[dl._week(item["session"])], by_month[item["session"][:7]]):
                cell["targets"] += 1
                cell["original_book_events"] += item["in_original_book"]
                cell["state_counts"][state] += 1
                if state == "selected":
                    cell["selected_known_labels"] += label is not None
                    cell["selected_unknown_labels"] += label is None
            if state == "selected":
                selected.append([item["session"], item["symbol"]])
                records.append({"session": item["session"], "label": label})
        own = dm._dispersion(records, self.reference, self.checkpoint)
        choice = []
        if own["unknown_labels"]:
            choice.append("selected_unknown_original_labels")
        if own["mde_bps"] is None or not math.isfinite(own["mde_bps"]):
            choice.append("own_label_mde_undefined_or_nonfinite")
        elif own["mde_bps"] > 33:
            choice.append("own_label_mde_exceeds_assumed_cost_floor")
        sizing_passed = not choice
        if any(item["in_original_book"] and item["state"] == "unknown" for item in targets):
            choice.append("unknown_original_book_features")
        weekly = [cell["state_counts"]["selected"] for cell in by_week.values()]
        upper = [cell["state_counts"]["selected"] + cell["state_counts"]["unknown"]
            + cell["state_counts"]["outside_original_book"] for cell in by_week.values()]
        frequency = {"reference_weeks": len(self.weeks), "selected_events": len(records),
            "selected_distinct_dates": len({row["session"] for row in records}),
            "selected_known_labels": own["known_labels"], "selected_unknown_labels": own["unknown_labels"],
            "selected_mean_per_all_reference_weeks": Fraction(len(records), len(self.weeks)),
            "selected_weekly_min": min(weekly), "selected_weekly_median": median(weekly), "selected_weekly_max": max(weekly),
            "weeks_at_least15_selected": sum(value >= 15 for value in weekly),
            "zero_selected_weeks": sum(value == 0 for value in weekly),
            "loose_selected_unknown_outside_book_upper_events": sum(upper),
            "loose_upper_mean_per_all_reference_weeks": Fraction(sum(upper), len(self.weeks)),
            "loose_upper_weeks_at_least15": sum(value >= 15 for value in upper),
            "upper_bound_is_estimate": False, "opportunities_are_fills_or_account_entries": False}
        source = self.projection["variants"][policy]
        population = deepcopy(source["population"])
        population.update(original_book_events=states["original_book_events"], state_counts=states["state_counts"],
            selected_known_labels=own["known_labels"], selected_unknown_labels=own["unknown_labels"])
        return {"population": population, "unknown_reason_counts": dict(sorted(reasons.items())),
            "target_states": targets, "selected_coordinates": selected, "by_session": by_session,
            "by_week": by_week, "by_month": by_month, "frequency": frequency, "own_label_dispersion": own,
            "sizing_passed": sizing_passed, "requires_operator_choice_before_comparison": bool(choice),
            "comparison_choice_reasons": choice, "source_variant_sha256": source["source_variant_sha256"],
            "source_rows_sha256": source["source_rows_sha256"],
            "reconciliation": {"all_source_rows_accounted": True, "original_book_preserved": True,
                "state_counts_match_targets": sum(states["state_counts"].values()) == len(targets),
                "weekly_monthly_match_selected": all(sum(cell["state_counts"]["selected"] for cell in panel.values())
                    == len(records) for panel in (by_session, by_week, by_month))}}

    def finish(self, dq_header: Mapping) -> dict:
        self.checkpoint()
        _require(not self.finished, "calibrator_already_finished")
        try:
            self._header(dq_header)
            variants = {policy: self._variant(policy) for policy in POLICIES}
            identity = {**deepcopy(self.projection["source_identity"]), "completed_DQ_sha256": UPSTREAM_SHA256,
                "DQ_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
                "DQ_header_sha256": _digest(dq_header, self.checkpoint)}
        except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError,
                dl.BreakoutLossConcentrationRefusal) as exc:
            raise MarketContextCalibrationRefusal("malformed_or_mismatched_completed_DQ_source") from exc
        reasons = [policy + ":" + reason for policy, variant in variants.items()
            for reason in variant["comparison_choice_reasons"]]
        self.finished = True
        self.checkpoint()
        return {"study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
            "parameters": deepcopy(PARAMETERS), "assumptions": dict(ASSUMPTIONS), **FLAGS,
            "next_hypothesis": deepcopy(NEXT_HYPOTHESIS), "upstream_specification_sha256": UPSTREAM_SPECIFICATION_SHA256,
            "source_identity": identity, "reference_sessions": list(self.reference), "reference_weeks": list(self.weeks),
            "source_target_matrix": deepcopy(self.projection["source_target_matrix"]),
            "upstream_calendar": deepcopy(self.projection["upstream_calendar"]),
            "source_readiness_input_evidence": deepcopy(dq_header["input_evidence"]),
            "source_readiness_summary": deepcopy(self.summaries), "variants": variants,
            "requires_operator_choice": bool(reasons), "requires_operator_choice_before_comparison": bool(reasons),
            "comparison_not_run_reasons": reasons, "pin_authentication": "caller_obligation_not_proven_by_pure_builder"}


def build_calibration(projection: Mapping, dq_header: Mapping, dq_events: Iterable[tuple[str, Mapping]], *,
        specification: Mapping | None = None, checkpoint: Callable[[], None] | None = None) -> dict:
    """Convenience wrapper around the same one-event callback contract."""
    calibrator = Calibrator(projection, specification=specification, checkpoint=checkpoint)
    for policy, event in dq_events:
        calibrator.accept_event(policy, event)
    return calibrator.finish(dq_header)
