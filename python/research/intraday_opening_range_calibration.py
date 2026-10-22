"""Fixed fifteen/thirty-minute opening-range frequency and own-label sizing.

Only supplied CB targets and saved CC minute chunks are consumed. Both arms
are unfiltered and declare new thirty-minute cap-open labels over all original
targets. No historical complete-cohort mask or performance summary is reused.
The caller owns registration, durable logging, input pins and the shared budget.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path

from research import intraday_pattern_calibration as cc


core = cc.core
ROOT = cc.ROOT
SPECIFICATION_ID = "intraday-opening-range-calibration-v1"
MODE = "fixed-unfiltered-opening-range-own-label-frequency-calibration"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-opening-range-calibration-v1.json"
WINDOW = dict(cc.WINDOW)
ARMS = ("15", "30")
PARAMETERS = {
    "range_minutes": {"15": 15, "30": 30},
    "signal": "first_subsequent_completed_five_minute_close_strictly_above_own_initial_range_high",
    "filters_both_arms": "none_no_relative_strength_or_peer_context",
    "availability_lag_minutes": 2,
    "decision_minutes": 240,
    "entry": "first_five_minute_open_strictly_after_signal_available_at",
    "label_minutes": 30,
    "exit": "entry_plus30minute_cap_minute_open",
    "label_required_minutes": "all31_buckets_entry_through_cap_inclusive",
    "label_availability": "cap_minute_end_plus_two_minutes_assumed",
    "maximum_signals_per_name_session": 1,
    "cohort": "new30minute_labels_all_original_targets_no_CG_or120minute_complete_mask",
    "block_sessions": 5,
    "bootstrap_draws": 2000,
    "bootstrap_seed": 0,
    "cost_floor_bps": "33",
    "weekly_operating_objective": 15,
    "output_scope": "frequency_missingness_own_label_dispersion_only_no_performance_comparison",
}
UPSTREAM_SHA256 = "f1ac24fed9ee3a54dfcac0d25ef1ff339be0a872d8915d849e4369e4d5c5eb91"
INPUT_COMPRESSED_SHA256 = "4a5380b5477476a55dccb402c1227b23e0e6a08bc9c92f2e742eef2d9a7b62de"
INPUT_STREAM_SHA256 = "d48593ce0da9d6cdca17ad00002e65fbcfda1f7b99f6b83c272ebb16943b36da"
REGISTERED_PATHS = {
    "upstream_path": "/home/minjun4897/research-evidence/intraday-cb-universe-20261020-v1/report.json",
    "input_archive_path": "/home/minjun4897/research-evidence/intraday-cc-calibration-20261020-v1/inputs.jsonl.gz",
    "output_directory": "/home/minjun4897/research-evidence/intraday-ct-opening-range-20261023-v1",
    "canonical_runs_path": "/home/minjun4897/trading-engine/runs/experiments.jsonl",
}
RESOURCES = {
    "maximum_upstream_bytes": 33_554_432,
    "maximum_compressed_bytes": 536_870_912,
    "maximum_uncompressed_bytes": 1_073_741_824,
    "maximum_chunk_bytes": 1_048_576,
    "maximum_chunks": 6000,
    "maximum_session_rows": 600,
    "maximum_rows": 3_600_000,
    "minimum_free_bytes": 1_073_741_824,
    "address_space_mib": 512,
    "cpu_seconds": 300,
    "wall_seconds": 1200,
    "nice": 10,
    "automatic_retries": 0,
}
EXPECTED_REFERENCE_SESSIONS = 257
EXPECTED_REFERENCE_WEEKS = 56
EXPECTED_TARGETS_PER_VARIANT = 5140
EXPECTED_INPUT_COUNTS = {"unique_target_days": 5158, "queried_rows": 1002211}
STATES = ("signal", "known_no_signal", "unknown")
GAP_STATES = ("missing", "conflict", "invalid_price")
TIMING_STATES = (
    "shared_same_timing", "shared_changed_timing_15_earlier",
    "15_only_30_known_no_signal", "15_only_30_unknown",
    "both_known_no_signal", "both_unknown",
)
FLAGS = {
    **cc.FLAGS,
    "exploratory_only": True,
    "calibration_only": True,
    "actual_portfolio_pnl": False,
    "comparison_performed": False,
    "family_adopted": False,
    "underpower_exception_applied": False,
    "historical_costs_certified": False,
    "actual_fills_certified": False,
}
ASSUMPTIONS = {
    "population": "all CB original targets both D+1/D+2 with original no-stored-row unknowns; no substitution or inherited CG/120minute complete selection",
    "timing": "completed five-minute signal end S; decision D=S+2m; strict next five-minute open E=D+3m; preplanned cap X=E+30m; outcome available X+3m",
    "source": "source stamps floored to assumed minute opens; original milliseconds and fetched_at remain in pinned archive; retrieval is not historical availability",
    "session_schedule": "09:00-15:30; 2025-11-13 10:00-16:30; 2026-01-02 10:00-15:30; assumed not certified",
    "signal_missingness": "only contiguous valid causal prefix is used; later gap cannot erase signal; known no-signal needs all47 decision-prefix bars; no rows has unresolved cause",
    "label_missingness": "each event keeps a label even when any of31 entry-through-cap minute buckets is missing conflicting or invalid; no zeros or later replacement",
    "measurement": "entry/cap minute opens are price proxies not fills; new thirty-minute label population is not a rescue of the historical120minute cohort",
    "power": "cc.dispersion own-label SD naive/session-cluster/circular-five-session bootstrap2000seed0 maxSE over every reference date including zero-event dates",
    "cost": "unchanged33bp assumed planning floor only; no cost deduction return mean or cost-exceedance count is produced",
    "frequency": "signals and known labels are opportunities not executable portfolio entries or independent observations;15perweek is operating fit never forced trades",
    "checkpoint": "complete calibration first; unknown labels/sizing or failed own sizing require the ordinary choice before any later comparison; no old waiver transfers",
    "claims": "no final comparison family parameter winner edge direction closure reserved access or paper/live promotion",
}
_canonical = cc._canonical


class OpeningRangeRefusal(RuntimeError):
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
        raise OpeningRangeRefusal("specification_scope_mismatch")


def _instant(value: object) -> datetime:
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError as exc:
        raise OpeningRangeRefusal("invalid_metadata_clock") from exc
    if not isinstance(result, datetime) or result.utcoffset() is None:
        raise OpeningRangeRefusal("invalid_metadata_clock")
    return result


def archive_chunks(path: str | Path, checkpoint: Callable[[], None]) -> Iterable[dict]:
    """Read the registered saved gzip stream after the caller's durable start.

    The caller authenticates compressed bytes first. Canonical CC framing makes
    each recomputed chunk vintage exactly the original archived line hash.
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
                raise OpeningRangeRefusal("input_stream_budget_or_framing_mismatch")
            digest.update(line)
            chunk = json.loads(line)
            if not isinstance(chunk, dict) or line != _canonical(chunk) + b"\n":
                raise OpeningRangeRefusal("input_chunk_not_original_canonical_framing")
            yield chunk
    if digest.hexdigest() != INPUT_STREAM_SHA256:
        raise OpeningRangeRefusal("input_stream_hash_mismatch")


def _gap_counts(session: cc.Session, start: datetime, end: datetime) -> dict:
    return {state: sum(int((min(gap["end"], end) - max(gap["start"], start)) / cc.MINUTE)
        for gap in session.gaps if gap["state"] == state
        and max(gap["start"], start) < min(gap["end"], end)) for state in GAP_STATES}


def _label(session: cc.Session, decision: datetime) -> dict:
    opening = session.opening
    entry = opening + ((decision - opening) // cc.FIVE + 1) * cc.FIVE
    cap = entry + 30 * cc.MINUTE
    closing = cc.session_close(opening.date().isoformat())
    if not opening < decision < entry <= cap < closing or entry - decision != 3 * cc.MINUTE:
        raise OpeningRangeRefusal("invalid_signal_entry_or_cap_boundary")
    prices = [session.minutes.get(entry + offset * cc.MINUTE) for offset in range(31)]
    gaps = _gap_counts(session, entry, cap + cc.MINUTE)
    missing = sum(value is None for value in prices)
    reasons = [f"label_{state}_minute_buckets" for state, count in gaps.items() if count]
    label = {
        "status": "unknown" if missing else "known",
        "entry_at": entry, "exit_at": cap, "time_cap_decided_at": decision,
        "entry_open": prices[0][0] if prices[0] is not None else None,
        "exit_open": prices[-1][0] if prices[-1] is not None else None,
        "price_change": None,
        "required_minute_buckets": 31, "unknown_minute_buckets": missing,
        "unknown_bucket_reason_counts": gaps, "unknown_reasons": reasons,
        "entry_price_available_at": entry + 3 * cc.MINUTE,
        "label_available_at": cap + 3 * cc.MINUTE,
        "evidence_level": "assumed", "actual_fill_certified": False,
    }
    if not missing:
        label["price_change"] = Fraction(prices[-1][0]) / Fraction(prices[0][0]) - 1
    return label


def _session_findings(session: cc.Session, metadata: Mapping, vintage: str, raw_count: int) -> dict:
    cutoff = session.opening + 240 * cc.MINUTE
    bars = tuple(bar for bar in session.prefix if bar.available_at <= cutoff)
    required = int((cutoff - session.opening - 2 * cc.MINUTE) // cc.FIVE)
    gaps = _gap_counts(session, session.opening, session.opening + required * cc.FIVE)
    unknown_reasons = (["no_saved_rows_unknown_cause"] if not raw_count else
        [f"decision_prefix_{state}_minute_buckets" for state, count in gaps.items() if count])
    findings = {}
    for arm in ARMS:
        event = core.detect_opening_range_breakout(bars, session_open=session.opening,
            entry_cutoff=cutoff, range_minutes=PARAMETERS["range_minutes"][arm])
        status = "signal" if event else "known_no_signal" if len(bars) == required else "unknown"
        saved_event = None
        if event:
            saved_event = asdict(event)
            saved_event.update(
                observation_date=metadata["observation_date"], available_at=event.decision_at,
                retrieved_at=metadata["retrieved_at"], source="collector klines; assumed completed five-minute bars",
                is_final=None, data_vintage=vintage, evidence_level="assumed",
                range_minutes=PARAMETERS["range_minutes"][arm],
                parameters_sha256=hashlib.sha256(_canonical(PARAMETERS)).hexdigest(),
            )
        findings[arm] = {
            "status": status, "event": saved_event,
            "label": _label(session, event.decision_at) if event else None,
            "required_bars": required, "valid_prefix_bars": len(bars),
            "unknown_reasons": unknown_reasons if status == "unknown" else [],
            "decision_prefix_unknown_bucket_counts": gaps,
        }
    return findings


def _timing(findings: Mapping) -> str:
    early, control = (findings[arm] for arm in ARMS)
    if control["status"] == "signal":
        if early["status"] != "signal":
            raise OpeningRangeRefusal("thirty_minute_signal_without_fifteen_minute_signal")
        first, second = (finding["event"]["decision_at"] for finding in (early, control))
        if first > second:
            raise OpeningRangeRefusal("fifteen_minute_signal_later_than_thirty")
        return "shared_same_timing" if first == second else "shared_changed_timing_15_earlier"
    if early["status"] == "signal":
        return "15_only_30_known_no_signal" if control["status"] == "known_no_signal" else "15_only_30_unknown"
    if early["status"] != control["status"]:
        raise OpeningRangeRefusal("inconsistent_shared_decision_prefix_state")
    return "both_known_no_signal" if early["status"] == "known_no_signal" else "both_unknown"


def _week(day: str) -> str:
    iso = date.fromisoformat(day).isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def _counts(rows: list[dict], arm: str) -> dict:
    findings = [row["arms"][arm] for row in rows]
    counts = Counter(item["status"] for item in findings)
    events = [item for item in findings if item["status"] == "signal"]
    labels = Counter(item["label"]["status"] for item in events)
    return {
        "target_symbol_sessions": len(rows),
        "signal_counts": {state: counts[state] for state in STATES},
        "label_counts": {state: labels[state] for state in ("known", "unknown")},
        "signal_unknown_reason_counts": dict(sorted(Counter(reason for item in findings
            for reason in item["unknown_reasons"]).items())),
        "label_unknown_reason_counts": dict(sorted(Counter(reason for item in events
            for reason in item["label"]["unknown_reasons"]).items())),
    }


def _timing_counts(rows: list[dict]) -> dict:
    counts = Counter(row["event_timing_state"] for row in rows)
    return {
        "states": {state: counts[state] for state in TIMING_STATES},
        "early_only": counts["15_only_30_known_no_signal"] + counts["15_only_30_unknown"],
        "shared": counts["shared_same_timing"] + counts["shared_changed_timing_15_earlier"],
        "changed_event_timing": counts["shared_changed_timing_15_earlier"],
        "thirty_only": 0, "fifteen_later": 0,
    }


def build_calibration(cb_report: Mapping, raw_chunks: Mapping | Iterable[Mapping], *,
        checkpoint: Callable[[], None] | None = None) -> dict:
    """Preserve complete supplied target populations; small invented grids work.

    Actual date/week/chunk/row counts and input hashes are enforced by the logged
    caller. A mapping uses (session, symbol) keys; iterables follow the original
    CC session/sorted-symbol order and must be exhausted including final hashes.
    """
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    matrix, union = cc._target_matrix(cb_report)
    reference = list(union)
    if cb_report.get("mode") != "discovery-universe-source-and-coverage-only":
        raise OpeningRangeRefusal("wrong_upstream_mode")
    expected = [(day, symbol) for day in reference for symbol in sorted(union[day])]
    if isinstance(raw_chunks, Mapping):
        if set(raw_chunks) != set(expected):
            raise OpeningRangeRefusal("input_chunk_population_mismatch")
        chunks = iter(raw_chunks[key] for key in expected)
    else:
        chunks = iter(raw_chunks)
    result = {
        "study_id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        "parameters": deepcopy(PARAMETERS), "reference_sessions": reference,
        "reference_weeks": sorted({_week(day) for day in reference}),
        "assumptions": dict(ASSUMPTIONS), **FLAGS,
        "upstream_calendar": deepcopy(cb_report["calendar"]),
        "source_target_matrix": deepcopy(cb_report["variants"]),
        "variants": {key: {"days": [], "by_arm": {}} for key in matrix},
    }
    chunk_count, row_count = 0, 0
    for index, day in enumerate(reference):
        checkpoint()
        sessions = {}
        for symbol in sorted(union[day]):
            checkpoint()
            try:
                chunk = next(chunks)
            except StopIteration as exc:
                raise OpeningRangeRefusal("missing_input_chunk") from exc
            if not isinstance(chunk, Mapping) or chunk.get("symbol") != symbol or chunk.get("session") != day:
                raise OpeningRangeRefusal("input_chunk_order_or_identity_mismatch")
            if (set(chunk) != {"symbol", "session", "observation_date", "available_at", "retrieved_at",
                    "source", "is_final", "evidence_level", "rows"}
                    or chunk["observation_date"] != day or chunk["available_at"] is not None
                    or chunk["source"] != "collector klines" or chunk["is_final"] is not None
                    or chunk["evidence_level"] != "assumed"):
                raise OpeningRangeRefusal("input_chunk_metadata_mismatch")
            _instant(chunk["retrieved_at"])
            raw = chunk["rows"]
            if not isinstance(raw, list) or len(raw) > RESOURCES["maximum_session_rows"]:
                raise OpeningRangeRefusal("input_session_row_budget_exceeded")
            for row in raw:
                if (not isinstance(row, Mapping) or set(row) != set(cc.COLUMNS)
                        or row["symbol"] != symbol
                        or row["fetched_at"] is not None and not isinstance(row["fetched_at"], str)):
                    raise OpeningRangeRefusal("input_raw_columns_identity_or_retrieval_mismatch")
            row_count += len(raw)
            chunk_count += 1
            if row_count > RESOURCES["maximum_rows"] or chunk_count > RESOURCES["maximum_chunks"]:
                raise OpeningRangeRefusal("input_population_budget_exceeded")
            vintage = hashlib.sha256(_canonical(chunk) + b"\n").hexdigest()
            metadata = {name: deepcopy(chunk[name]) for name in
                ("observation_date", "available_at", "retrieved_at", "source", "is_final", "evidence_level")}
            session = cc.aggregate_session(raw, cc.session_open(day))
            findings = _session_findings(session, metadata, vintage, len(raw))
            sessions[symbol] = {
                "symbol": symbol, "session": day, "data_vintage": vintage,
                "raw_chunk_metadata": metadata, "input_rows": len(raw),
                "source_fetched_at": sorted({row["fetched_at"] for row in raw if row["fetched_at"] is not None}),
                "source_offset_counts_ms": dict(Counter(session.source_offsets_ms)),
                "gaps": session.gaps, "arms": findings, "event_timing_state": _timing(findings),
            }
        for key, variant in matrix.items():
            membership = variant["days"][index]
            for target in membership["targets"]:
                output = deepcopy(sessions[target["symbol"]])
                output["membership_target"] = deepcopy(target)
                output["membership_source"] = {name: deepcopy(value) for name, value in membership.items() if name != "targets"}
                result["variants"][key]["days"].append(output)
    try:
        next(chunks)
    except StopIteration:
        pass
    else:
        raise OpeningRangeRefusal("extra_input_chunk")
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
            choice_reasons = []
            if own["unknown_labels"]:
                choice_reasons.append("unknown_own_labels")
            if own["mde_bps"] is None:
                choice_reasons.append("undefined_own_label_sizing")
            elif own["mde_bps"] > 33:
                choice_reasons.append("own_label_mde_exceeds_assumed_cost_floor")
            by_week = {week: _counts([row for row in rows if _week(row["session"]) == week], arm) for week in weeks}
            variant["by_arm"][arm] = {
                **_counts(rows, arm), "own_label_dispersion": own,
                "by_week": by_week,
                "by_month": {month: _counts([row for row in rows if row["session"][:7] == month], arm) for month in months},
                "frequency": {
                    "reference_sessions": len(reference), "reference_weeks": len(weeks),
                    "signals_per_reference_week": Fraction(len(records), len(weeks)),
                    "known_labels_per_reference_week": Fraction(own["known_labels"], len(weeks)),
                    "weekly_operating_objective": 15,
                    "weeks_with_at_least15_signals": sum(item["signal_counts"]["signal"] >= 15 for item in by_week.values()),
                    "forced_trades": False, "executable_frequency_certified": False,
                },
                "requires_operator_choice_before_comparison": bool(choice_reasons),
                "comparison_choice_reasons": choice_reasons,
            }
        variant["event_timing"] = _timing_counts(rows)
        variant["event_timing_by_week"] = {week: _timing_counts([row for row in rows if _week(row["session"]) == week]) for week in weeks}
    result["requires_operator_choice_before_comparison"] = any(
        summary["requires_operator_choice_before_comparison"]
        for variant in result["variants"].values() for summary in variant["by_arm"].values())
    checkpoint()
    return result
