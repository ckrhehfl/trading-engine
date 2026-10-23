"""Fixed OR15 first-contact retest frequency, missingness and own-label sizing.

Only original CB targets and saved CC raw chunks are consumed. New thirty-minute
labels use the retest entry, with no old complete-cohort mask or performance
comparison. The logged caller owns actual IO authentication and shared budgets.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import asdict
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path

from research import intraday_opening_range_calibration as ct


cc = ct.cc
ROOT = ct.ROOT
SPECIFICATION_ID = "intraday-opening-retest-calibration-v1"
MODE = "fixed-opening-retest-own-label-frequency-calibration"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-opening-retest-calibration-v1.json"
WINDOW = dict(ct.WINDOW)
ARMS = ("15_retest",)
PARAMETERS = {
    "hypothesis_id": "opening-range15-first-retest-bounce-v1",
    "range_minutes": 15,
    "range_high": "high_of_original_initial15minute_range",
    "breakout": "original_first_subsequent_completed5minute_close_strictly_above_range_high",
    "signal": "first_completed5minute_contact_strictly_after_breakout_low_at_or_below_range_high_and_close_strictly_above_range_high",
    "invalidation": "any_earlier_post_breakout_close_at_or_below_range_high_or_first_contact_close_at_or_below_range_high_no_retry_or_replacement",
    "same_bar_breakout_retest_allowed": False,
    "filters": "none_no_CG_no_peer_no_opening_impulse_no_volume_condition",
    "availability_lag_minutes": 2,
    "decision_minutes": 240,
    "decision_cutoff_clock": "retest_confirmation_bar_end_plus2minutes_at_or_before_session_open_plus240minutes",
    "causal_path": "contact_may_precede_breakout_availability_decision_only_after_completed_contact_availability_no_touch_limit_fill",
    "entry": "first_five_minute_open_strictly_after_signal_available_at",
    "label_minutes": 30,
    "exit": "entry_plus30minute_cap_minute_open",
    "label_required_minutes": "all31_buckets_entry_through_cap_inclusive",
    "label_availability": "cap_minute_end_plus_two_minutes_assumed",
    "maximum_signals_per_name_session": 1,
    "cohort": "new_retest_entry30minute_labels_all_original_targets_no_CG_or120minute_complete_mask",
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
    "output_directory": "/home/minjun4897/research-evidence/intraday-dc-opening-retest-20261023-v1",
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
    "timing": "R initial15minute high; original first breakout B; first later completed contact confirms at S; decision D=S+2m; strict next five-minute open E=D+3m; new cap X=E+30m; outcome available X+3m",
    "signal_missingness": "only contiguous valid causal prefix; same B bar excluded; first later low<=R contact is sole trial; close<=R invalidates without retry; later gaps cannot erase known signal or invalidation;47bars establish untriggered absence; no rows has unresolved cause",
    "measurement": "new retest entry/cap minute opens are price proxies not fills; no CT original-entry labels or old120minute-complete mask; no volume or impulse filter",
    "unknown_bounds": "at most one opportunity per original name/session; unknowns only define possible upper bounds, never imputed signals or known labels",
}
_canonical = ct._canonical


class OpeningRetestRefusal(RuntimeError):
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
        raise OpeningRetestRefusal("specification_scope_mismatch")


def archive_chunks(path: str | Path, checkpoint: Callable[[], None]) -> Iterable[dict]:
    """Bound and authenticate original canonical decompressed archive framing.

    The caller authenticates compressed bytes before starting this iterator.
    Exhaustion is mandatory: the final decompressed hash is checked at EOF.
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
                raise OpeningRetestRefusal("input_stream_budget_or_framing_mismatch")
            digest.update(line)
            chunk = json.loads(line)
            if not isinstance(chunk, dict) or line != _canonical(chunk) + b"\n":
                raise OpeningRetestRefusal("input_chunk_not_original_canonical_framing")
            yield chunk
    if digest.hexdigest() != INPUT_STREAM_SHA256:
        raise OpeningRetestRefusal("input_stream_hash_mismatch")


def _session_findings(session: cc.Session, metadata: Mapping, vintage: str, raw_count: int) -> dict:
    cutoff = session.opening + 240 * cc.MINUTE
    bars = tuple(bar for bar in session.prefix if bar.available_at <= cutoff)
    required = int((cutoff - session.opening - 2 * cc.MINUTE) // cc.FIVE)
    gaps = ct._gap_counts(session, session.opening, session.opening + required * cc.FIVE)
    unknown_reasons = (["no_saved_rows_unknown_cause"] if not raw_count else
        [f"decision_prefix_{state}_minute_buckets" for state, count in gaps.items() if count])
    level = max((bar.high for bar in bars[:3]), default=None) if len(bars) >= 3 else None
    breakout = contact = invalidation = None
    invalidation_reason = None
    for bar in bars[3:]:
        if breakout is None:
            if bar.close > level:
                breakout = bar
            continue  # No contact from the breakout bar, even if its low<=R.
        if bar.low <= level:
            contact = bar
            if bar.close <= level:
                invalidation = bar
                invalidation_reason = "first_contact_close_at_or_below_range_high"
            break  # First contact is the sole trial; no retry or replacement B.
    event = None
    if contact is not None and invalidation is None:
        event = {
            "family": "opening_range15_first_retest_bounce",
            "decision_at": contact.available_at,
            "signal_bar_start": contact.start_at, "signal_bar_end": contact.end_at,
            "context": tuple(asdict(bar) for bar in bars[:3]), "range_high": level,
            "breakout_bar": asdict(breakout), "contact_bar": asdict(contact),
            "observation_date": metadata["observation_date"], "available_at": contact.available_at,
            "retrieved_at": metadata["retrieved_at"],
            "source": "collector klines; assumed completed five-minute bars",
            "is_final": None, "data_vintage": vintage, "evidence_level": "assumed",
            "range_minutes": 15, "parameters_sha256": hashlib.sha256(_canonical(PARAMETERS)).hexdigest(),
        }
    status = ("signal" if event else "known_no_signal" if invalidation is not None
        or len(bars) == required else "unknown")
    no_signal_reason = (invalidation_reason or
        ("no_breakout_before_cutoff" if breakout is None else "no_contact_before_cutoff")) if status == "known_no_signal" else None
    return {ARMS[0]: {
        "status": status, "event": event,
        "label": ct._label(session, contact.available_at) if event else None,
        "required_bars": required, "valid_prefix_bars": len(bars),
        "unknown_reasons": unknown_reasons if status == "unknown" else [],
        "decision_prefix_unknown_bucket_counts": gaps,
        "known_no_signal_reason": no_signal_reason,
        "path": {
            "range_high": level, "breakout_bar": asdict(breakout) if breakout else None,
            "first_contact_bar": asdict(contact) if contact else None,
            "invalidation_bar": asdict(invalidation) if invalidation else None,
            "invalidation_reason": invalidation_reason,
        },
    }}


def _counts(rows: list[dict], arm: str) -> dict:
    return {
        **ct._counts(rows, arm),
        "known_no_signal_reason_counts": dict(sorted(Counter(row["arms"][arm]["known_no_signal_reason"]
            for row in rows if row["arms"][arm]["status"] == "known_no_signal").items())),
    }


def _weekly_range(values: list[int]) -> dict:
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = Fraction(ordered[middle]) if len(ordered) % 2 else Fraction(ordered[middle - 1] + ordered[middle], 2)
    return {"minimum": ordered[0], "median": median, "maximum": ordered[-1]}


def _frequency(counts: Mapping, by_week: Mapping, reference: list[str]) -> dict:
    weeks = len(by_week)
    signals, unknown = (counts["signal_counts"][key] for key in ("signal", "unknown"))
    known, missing = (counts["label_counts"][key] for key in ("known", "unknown"))
    weekly_signals = [item["signal_counts"]["signal"] for item in by_week.values()]
    weekly_known = [item["label_counts"]["known"] for item in by_week.values()]
    return {
        "reference_sessions": len(reference), "reference_weeks": weeks,
        "signals_per_reference_week": Fraction(signals, weeks),
        "known_labels_per_reference_week": Fraction(known, weeks),
        "unknown_signal_symbol_sessions": unknown,
        "maximum_signals_including_unknown": signals + unknown,
        "signals_per_reference_week_upper_bound": Fraction(signals + unknown, weeks),
        "maximum_known_labels_including_unknown": known + missing + unknown,
        "known_labels_per_reference_week_upper_bound": Fraction(known + missing + unknown, weeks),
        "weekly_operating_objective": 15,
        "weeks_with_at_least15_signals": sum(value >= 15 for value in weekly_signals),
        "weeks_with_at_least15_signals_upper_bound": sum(item["signal_counts"]["signal"] + item["signal_counts"]["unknown"] >= 15 for item in by_week.values()),
        "weeks_with_at_least15_known_labels": sum(value >= 15 for value in weekly_known),
        "weeks_with_at_least15_known_labels_upper_bound": sum(item["signal_counts"]["signal"] + item["signal_counts"]["unknown"] >= 15 for item in by_week.values()),
        "zero_signal_weeks": weekly_signals.count(0), "zero_known_label_weeks": weekly_known.count(0),
        "weekly_signal_counts_min_median_max": _weekly_range(weekly_signals),
        "weekly_known_label_counts_min_median_max": _weekly_range(weekly_known),
        "bound_assumption": "at_most_one_signal_and_label_per_original_name_session_unknowns_are_possibilities_not_imputed_events",
        "forced_trades": False, "executable_frequency_certified": False,
    }


def build_calibration(upstream: Mapping, chunks: Mapping | Iterable[Mapping],
        specification: Mapping | None = None, checkpoint: Callable[[], None] | None = None) -> dict:
    """Retain all original targets; mappings and original ordered streams work.

    Small invented source grids are supported. Actual hashes and registered
    date/week/chunk/row counts are enforced by the caller before publication.
    """
    if specification is not None:
        validate_spec(specification)
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    matrix, union = cc._target_matrix(upstream)
    reference = list(union)
    if upstream.get("mode") != "discovery-universe-source-and-coverage-only":
        raise OpeningRetestRefusal("wrong_upstream_mode")
    expected = [(day, symbol) for day in reference for symbol in sorted(union[day])]
    if isinstance(chunks, Mapping):
        if set(chunks) != set(expected):
            raise OpeningRetestRefusal("input_chunk_population_mismatch")
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
                raise OpeningRetestRefusal("missing_input_chunk") from exc
            if not isinstance(chunk, Mapping) or chunk.get("symbol") != symbol or chunk.get("session") != day:
                raise OpeningRetestRefusal("input_chunk_order_or_identity_mismatch")
            if (set(chunk) != {"symbol", "session", "observation_date", "available_at", "retrieved_at",
                    "source", "is_final", "evidence_level", "rows"}
                    or chunk["observation_date"] != day or chunk["available_at"] is not None
                    or chunk["source"] != "collector klines" or chunk["is_final"] is not None
                    or chunk["evidence_level"] != "assumed"):
                raise OpeningRetestRefusal("input_chunk_metadata_mismatch")
            ct._instant(chunk["retrieved_at"])
            raw = chunk["rows"]
            if not isinstance(raw, list) or len(raw) > RESOURCES["maximum_session_rows"]:
                raise OpeningRetestRefusal("input_session_row_budget_exceeded")
            for row in raw:
                if (not isinstance(row, Mapping) or set(row) != set(cc.COLUMNS)
                        or row["symbol"] != symbol
                        or row["fetched_at"] is not None and not isinstance(row["fetched_at"], str)):
                    raise OpeningRetestRefusal("input_raw_columns_identity_or_retrieval_mismatch")
            row_count += len(raw)
            chunk_count += 1
            if row_count > RESOURCES["maximum_rows"] or chunk_count > RESOURCES["maximum_chunks"]:
                raise OpeningRetestRefusal("input_population_budget_exceeded")
            vintage = hashlib.sha256(_canonical(chunk) + b"\n").hexdigest()
            metadata = {name: deepcopy(chunk[name]) for name in
                ("observation_date", "available_at", "retrieved_at", "source", "is_final", "evidence_level")}
            session = cc.aggregate_session(raw, cc.session_open(day))
            sessions[symbol] = {
                "symbol": symbol, "session": day, "data_vintage": vintage,
                "raw_chunk_metadata": metadata, "input_rows": len(raw),
                "source_fetched_at": sorted({row["fetched_at"] for row in raw if row["fetched_at"] is not None}),
                "source_offset_counts_ms": dict(Counter(session.source_offsets_ms)),
                "gaps": session.gaps, "arms": _session_findings(session, metadata, vintage, len(raw)),
            }
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
        raise OpeningRetestRefusal("extra_input_chunk")
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
            counts = _counts(rows, arm)
            by_week = {week: _counts([row for row in rows if ct._week(row["session"]) == week], arm) for week in weeks}
            variant["by_arm"][arm] = {
                **counts, "own_label_dispersion": own, "by_week": by_week,
                "by_month": {month: _counts([row for row in rows if row["session"][:7] == month], arm) for month in months},
                "frequency": _frequency(counts, by_week, reference),
                "requires_operator_choice_before_comparison": bool(reasons),
                "comparison_choice_reasons": reasons,
            }
    result["requires_operator_choice_before_comparison"] = any(
        summary["requires_operator_choice_before_comparison"]
        for variant in result["variants"].values() for summary in variant["by_arm"].values())
    checkpoint()
    return result
