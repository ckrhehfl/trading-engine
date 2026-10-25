"""Fixed OR15 two-close acceptance counts, missingness and own-label sizing.

The only market inputs are original CB targets and saved CC minute chunks.
Shared pure calendar, intake and sizing helpers do not replay an old detector or
reuse its labels. The logged caller owns authentication and process budgets.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path

from research import intraday_opening_retest_calibration as dc


ct, cc = dc.ct, dc.cc
ROOT = ct.ROOT
SPECIFICATION_ID = "intraday-opening-acceptance-calibration-v1"
MODE = "fixed-opening-acceptance-own-label-frequency-calibration"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-opening-acceptance-calibration-v1.json"
WINDOW = dict(ct.WINDOW)
ARMS = ("15_acceptance",)
PARAMETERS = {
    "hypothesis_id": "opening-range15-two-close-acceptance-v1",
    "universe": "same_saved_point_in_time_monthly_around_top20_targets_both_D+1_and_D+2",
    "side": "long_only",
    "range_minutes": 15,
    "range_high": "R_is_original_initial15minute_range_high",
    "breakout": "B_is_original_first_later_completed5minute_close_strictly_above_R",
    "confirmation": "W_is_exactly_next_scheduled5minute_bar_after_B_no_skip;W_close_strictly_above_R",
    "invalidation": "W_close_at_or_below_R_cancels_name_session_no_retry",
    "touch_required": False,
    "additional_filters": "none_no_volume_peer_CLV_impulse_volatility_or_retest_predicate",
    "availability_lag_minutes": 2,
    "decision_minutes": 240,
    "decision": "W_end_plus2minutes;at_or_before_session_open_plus240minutes",
    "entry": "first5minute_open_strictly_after_decision;original3minute_wait",
    "label_minutes": 30,
    "exit": "entry_plus30minute_cap_minute_OPEN",
    "label_required_minutes": "all31_buckets_entry_through_cap_inclusive",
    "label_availability": "cap_minute_end_plus_two_minutes_assumed",
    "maximum_signals_per_name_session": 1,
    "unknown_prefix": "missing_or_invalid_initial_range_B_prefix_or_exact_W_remains_unknown_no_skip;later_gaps_do_not_erase_known_signal",
    "future_labels": "new_entry_and_new_entry_plus30minute_OPEN_labels;never_reuse_old_B_or_retest_labels",
    "cohort": "all_original_targets_no_CT_DK_CG_or120minute_outcome_mask",
    "block_sessions": 5,
    "bootstrap_draws": 2000,
    "bootstrap_seed": 0,
    "cost_floor_bps": "33",
    "weekly_operating_objective": 15,
    "output_scope": "frequency_missingness_own_label_dispersion_only_no_performance_comparison",
}
UPSTREAM_SHA256 = ct.UPSTREAM_SHA256
INPUT_COMPRESSED_SHA256 = ct.INPUT_COMPRESSED_SHA256
INPUT_STREAM_SHA256 = ct.INPUT_STREAM_SHA256
REGISTERED_PATHS = {
    **ct.REGISTERED_PATHS,
    "output_directory": "/home/minjun4897/research-evidence/intraday-du-opening-acceptance-20261025-v1",
}
RESOURCES = dict(ct.RESOURCES)
EXPECTED_REFERENCE_SESSIONS = ct.EXPECTED_REFERENCE_SESSIONS
EXPECTED_REFERENCE_WEEKS = ct.EXPECTED_REFERENCE_WEEKS
EXPECTED_TARGETS_PER_VARIANT = ct.EXPECTED_TARGETS_PER_VARIANT
EXPECTED_INPUT_COUNTS = dict(ct.EXPECTED_INPUT_COUNTS)
STATES = ct.STATES
FLAGS = dict(ct.FLAGS)
ASSUMPTIONS = {
    **ct.ASSUMPTIONS,
    "timing": "R initial15minute high; original first breakout B; exactly next scheduled5minute bar W close>R; decision D=W.end+2m at_or_before_open+240m; strict next5minute OPEN E=D+3m; new cap X=E+30m; outcome available X+3m",
    "signal_missingness": "contiguous valid causal prefix only; exactly next W cannot be skipped; W.close<=R cancels without retry; missing initial range/B-prefix/W stays unknown; later gaps cannot erase known signal or cancellation;47bars establish untriggered absence; no rows has unresolved cause",
    "measurement": "new acceptance-entry/cap minute OPEN price proxies not fills; no original B/retest labels or CT/DK/CG/120minute outcome mask; no touch or additional filters",
    "unknown_bounds": "at most one opportunity per original name/session; unknowns only define possible upper bounds, never imputed signals or known labels",
}
_canonical = ct._canonical


class OpeningAcceptanceRefusal(RuntimeError):
    """Stable reason codes exclude private source values."""


def validate_spec(spec: Mapping) -> None:
    expected = {
        "id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        "parameters": PARAMETERS, "resources": RESOURCES, **REGISTERED_PATHS,
        "upstream_sha256": UPSTREAM_SHA256,
        "input_compressed_sha256": INPUT_COMPRESSED_SHA256,
        "input_stream_sha256": INPUT_STREAM_SHA256,
    }
    if _canonical(spec) != _canonical(expected):
        raise OpeningAcceptanceRefusal("specification_scope_mismatch")


def archive_chunks(path: str | Path, checkpoint: Callable[[], None]) -> Iterable[dict]:
    """Authenticate original canonical stream at EOF after bounded intake.

    The caller authenticates compressed bytes first and exhausts this iterator.
    """
    digest, size, count = hashlib.sha256(), 0, 0
    with gzip.open(path, "rb") as stream:
        while True:
            checkpoint()
            line = stream.readline(RESOURCES["maximum_chunk_bytes"] + 1)
            if not line:
                break
            size += len(line)
            count += 1
            if (len(line) > RESOURCES["maximum_chunk_bytes"] or not line.endswith(b"\n")
                    or size > RESOURCES["maximum_uncompressed_bytes"]
                    or count > RESOURCES["maximum_chunks"]):
                raise OpeningAcceptanceRefusal("input_stream_budget_or_framing_mismatch")
            digest.update(line)
            chunk = json.loads(line)
            if not isinstance(chunk, dict) or line != _canonical(chunk) + b"\n":
                raise OpeningAcceptanceRefusal("input_chunk_not_original_canonical_framing")
            yield chunk
    if digest.hexdigest() != INPUT_STREAM_SHA256:
        raise OpeningAcceptanceRefusal("input_stream_hash_mismatch")


def _session_findings(session: cc.Session, metadata: Mapping, vintage: str, raw_count: int) -> dict:
    cutoff = session.opening + 240 * cc.MINUTE
    bars = tuple(bar for bar in session.prefix if bar.available_at <= cutoff)
    required = int((cutoff - session.opening - 2 * cc.MINUTE) // cc.FIVE)
    gaps = ct._gap_counts(session, session.opening, session.opening + required * cc.FIVE)
    unknown_reasons = (["no_saved_rows_unknown_cause"] if not raw_count else
        [f"decision_prefix_{state}_minute_buckets" for state, count in gaps.items() if count])
    level = max(bar.high for bar in bars[:3]) if len(bars) >= 3 else None
    breakout = confirmation = invalidation = None
    invalidation_reason = None
    no_signal_reason = None
    for index, bar in enumerate(bars[3:], 3):
        if bar.close <= level:
            continue
        breakout = bar  # Original first B is the sole trial.
        if bar.end_at + cc.FIVE + 2 * cc.MINUTE > cutoff:
            no_signal_reason = "confirmation_after_cutoff"
        elif index + 1 < len(bars):
            confirmation = bars[index + 1]
            if confirmation.start_at != breakout.end_at:
                raise OpeningAcceptanceRefusal("noncontiguous_confirmation_bar")
            if confirmation.close <= level:
                invalidation = confirmation
                invalidation_reason = "next_scheduled_close_at_or_below_range_high"
                no_signal_reason = invalidation_reason
        break  # No replacement B, skipped W or retry after cancellation.
    event = None
    if confirmation is not None and invalidation is None:
        event = {
            "family": "opening_range15_two_close_acceptance",
            "decision_at": confirmation.available_at,
            "signal_bar_start": confirmation.start_at, "signal_bar_end": confirmation.end_at,
            "context": tuple(asdict(bar) for bar in bars[:3]), "range_high": level,
            "breakout_bar": asdict(breakout), "confirmation_bar": asdict(confirmation),
            "observation_date": metadata["observation_date"], "available_at": confirmation.available_at,
            "retrieved_at": metadata["retrieved_at"],
            "source": "collector klines; assumed completed five-minute bars",
            "is_final": None, "data_vintage": vintage, "evidence_level": "assumed",
            "range_minutes": 15, "parameters_sha256": hashlib.sha256(_canonical(PARAMETERS)).hexdigest(),
        }
    if breakout is None and len(bars) == required:
        no_signal_reason = "no_breakout_before_cutoff"
    status = "signal" if event else "known_no_signal" if no_signal_reason else "unknown"
    return {ARMS[0]: {
        "status": status, "event": event,
        "label": ct._label(session, confirmation.available_at) if event else None,
        "required_bars": required, "valid_prefix_bars": len(bars),
        "unknown_reasons": unknown_reasons if status == "unknown" else [],
        "decision_prefix_unknown_bucket_counts": gaps,
        "known_no_signal_reason": no_signal_reason,
        "path": {
            "range_high": level, "breakout_bar": asdict(breakout) if breakout else None,
            "confirmation_bar": asdict(confirmation) if confirmation else None,
            "invalidation_bar": asdict(invalidation) if invalidation else None,
            "invalidation_reason": invalidation_reason,
        },
    }}


def _read_chunk(chunk: Mapping, day: str, symbol: str) -> tuple[dict, int]:
    """Validate only a supplied saved chunk; perform no data acquisition."""
    if not isinstance(chunk, Mapping) or chunk.get("symbol") != symbol or chunk.get("session") != day:
        raise OpeningAcceptanceRefusal("input_chunk_order_or_identity_mismatch")
    if (set(chunk) != {"symbol", "session", "observation_date", "available_at", "retrieved_at",
            "source", "is_final", "evidence_level", "rows"}
            or chunk["observation_date"] != day or chunk["available_at"] is not None
            or chunk["source"] != "collector klines" or chunk["is_final"] is not None
            or chunk["evidence_level"] != "assumed"):
        raise OpeningAcceptanceRefusal("input_chunk_metadata_mismatch")
    ct._instant(chunk["retrieved_at"])
    raw = chunk["rows"]
    if not isinstance(raw, list) or len(raw) > RESOURCES["maximum_session_rows"]:
        raise OpeningAcceptanceRefusal("input_session_row_budget_exceeded")
    for row in raw:
        if (not isinstance(row, Mapping) or set(row) != set(cc.COLUMNS)
                or row["symbol"] != symbol
                or row["fetched_at"] is not None and not isinstance(row["fetched_at"], str)):
            raise OpeningAcceptanceRefusal("input_raw_columns_identity_or_retrieval_mismatch")
    vintage = hashlib.sha256(_canonical(chunk) + b"\n").hexdigest()
    metadata = {name: deepcopy(chunk[name]) for name in
        ("observation_date", "available_at", "retrieved_at", "source", "is_final", "evidence_level")}
    session = cc.aggregate_session(raw, cc.session_open(day))
    return {
        "symbol": symbol, "session": day, "data_vintage": vintage,
        "raw_chunk_metadata": metadata, "input_rows": len(raw),
        "source_fetched_at": sorted({row["fetched_at"] for row in raw if row["fetched_at"] is not None}),
        "source_offset_counts_ms": dict(Counter(session.source_offsets_ms)),
        "gaps": session.gaps, "arms": _session_findings(session, metadata, vintage, len(raw)),
    }, len(raw)


def build_calibration(upstream: Mapping, chunks: Mapping | Iterable[Mapping],
        specification: Mapping | None = None, checkpoint: Callable[[], None] | None = None) -> dict:
    """Retain every original target; accept mappings or original CC order.

    Invented small grids work. The caller must enforce registered actual hashes,
    date/week/chunk/row counts and process budgets before publication.
    """
    if specification is not None:
        validate_spec(specification)
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    matrix, union = cc._target_matrix(upstream)
    reference = list(union)
    if upstream.get("mode") != "discovery-universe-source-and-coverage-only":
        raise OpeningAcceptanceRefusal("wrong_upstream_mode")
    expected = [(day, symbol) for day in reference for symbol in sorted(union[day])]
    if isinstance(chunks, Mapping):
        if set(chunks) != set(expected):
            raise OpeningAcceptanceRefusal("input_chunk_population_mismatch")
        iterator = iter(chunks[key] for key in expected)
    else:
        iterator = iter(chunks)
    result = {
        "study_id": SPECIFICATION_ID, "mode": MODE, "window": dict(WINDOW),
        "parameters": deepcopy(PARAMETERS), "reference_sessions": reference,
        "reference_weeks": sorted({ct._week(day) for day in reference}),
        "assumptions": dict(ASSUMPTIONS), **FLAGS,
        "upstream_calendar": deepcopy(upstream["calendar"]),
        "source_target_matrix": deepcopy(upstream["variants"]),
        "variants": {key: {"days": [], "by_arm": {}} for key in matrix},
    }
    chunk_count, row_count = 0, 0
    for index, day in enumerate(reference):
        checkpoint()
        sessions = {}
        for symbol in sorted(union[day]):
            checkpoint()
            try:
                chunk = next(iterator)
            except StopIteration as exc:
                raise OpeningAcceptanceRefusal("missing_input_chunk") from exc
            findings, count = _read_chunk(chunk, day, symbol)
            row_count += count
            chunk_count += 1
            if row_count > RESOURCES["maximum_rows"] or chunk_count > RESOURCES["maximum_chunks"]:
                raise OpeningAcceptanceRefusal("input_population_budget_exceeded")
            sessions[symbol] = findings
        for key, variant in matrix.items():
            membership = variant["days"][index]
            for target in membership["targets"]:
                output = deepcopy(sessions[target["symbol"]])
                output["membership_target"] = deepcopy(target)
                output["membership_source"] = {name: deepcopy(value) for name, value in membership.items() if name != "targets"}
                result["variants"][key]["days"].append(output)
    try:
        next(iterator)
    except StopIteration:
        pass
    else:
        raise OpeningAcceptanceRefusal("extra_input_chunk")
    result["input_evidence"] = {
        "upstream_report_sha256": UPSTREAM_SHA256,
        "upstream_report_path": REGISTERED_PATHS["upstream_path"],
        "archive_path": REGISTERED_PATHS["input_archive_path"],
        "compressed_sha256": INPUT_COMPRESSED_SHA256, "stream_sha256": INPUT_STREAM_SHA256,
        "unique_target_days": chunk_count, "queried_rows": row_count,
        "pin_authentication": "caller_obligation_not_proven_by_pure_builder",
    }
    weeks, months = result["reference_weeks"], sorted({day[:7] for day in reference})
    for variant in result["variants"].values():
        rows = variant["days"]
        for arm in ARMS:
            checkpoint()
            records = [{"session": row["session"], "label": row["arms"][arm]["label"]["price_change"]}
                for row in rows if row["arms"][arm]["status"] == "signal"]
            own = cc.dispersion(records, reference)
            checkpoint()
            reasons = []
            if own["unknown_labels"]:
                reasons.append("unknown_own_labels")
            if own["mde_bps"] is None:
                reasons.append("undefined_own_label_sizing")
            elif own["mde_bps"] > 33:
                reasons.append("own_label_mde_exceeds_assumed_cost_floor")
            counts = dc._counts(rows, arm)
            by_week = {week: dc._counts([row for row in rows if ct._week(row["session"]) == week], arm) for week in weeks}
            variant["by_arm"][arm] = {
                **counts, "own_label_dispersion": own, "by_week": by_week,
                "by_month": {month: dc._counts([row for row in rows if row["session"][:7] == month], arm) for month in months},
                "frequency": dc._frequency(counts, by_week, reference),
                "requires_operator_choice_before_comparison": bool(reasons),
                "comparison_choice_reasons": reasons,
            }
    result["requires_operator_choice_before_comparison"] = any(
        summary["requires_operator_choice_before_comparison"]
        for variant in result["variants"].values() for summary in variant["by_arm"].values())
    checkpoint()
    return result
