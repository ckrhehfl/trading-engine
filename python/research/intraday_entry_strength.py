"""Causal prior-return selection and selected own-label power calibration only.

Reuse independently verified CF events, original peers and fixed complete-pair
labels. No detector, future-return comparison, fill model or strategy verdict.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from datetime import date
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import shutil
from statistics import NormalDist
import sys

from research import experiment_log
from research import intraday_pattern_horizons as horizons


cd, cc = horizons.cd, horizons.cc
ROOT = horizons.ROOT
SPECIFICATION_ID = "intraday-entry-strength-calibration-v1"
MODE = "causal-entry-feature-own-label-calibration"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-entry-strength-calibration-v1.json"
HORIZONS = horizons.HORIZONS
WINDOW = dict(horizons.WINDOW)
PARAMETERS = {**horizons.PARAMETERS,
    "upstream_reuse": "pinned_verified_cf_events_controls_and_labels_no_detector_or_label_rerun",
    "feature_minutes": 30,
    "feature_end": "original_signal_bar_end",
    "feature_return": "first_minute_open_to_last_minute_close_all_30_valid_contiguous_buckets",
    "feature_peer_weighting": "equal_all_original_causal_selected_controls_no_replacement",
    "feature_selection": "event_prior_return_minus_peer_mean_strictly_greater_than_zero",
    "feature_availability": "feature_end_plus_two_minutes_no_later_than_original_decision",
    "output_scope": "selection_frequency_and_selected_event_own_label_dispersion_only"}
REGISTERED_PATHS = {
    "upstream_path": "/home/minjun4897/research-evidence/intraday-cf-horizons-recovery-20261021-v1/report.json",
    "input_archive_path": horizons.REGISTERED_PATHS["input_archive_path"],
    "output_directory": "/home/minjun4897/research-evidence/intraday-cg-entry-strength-20261021-v1",
    "canonical_runs_path": horizons.REGISTERED_PATHS["canonical_runs_path"]}
UPSTREAM_SHA256 = "ec9c110b7556eeb8fff2557bc069719cdc1b167b9c7c21da908d84e30180667e"
INPUT_COMPRESSED_SHA256 = horizons.INPUT_COMPRESSED_SHA256
INPUT_STREAM_SHA256 = horizons.INPUT_STREAM_SHA256
RESOURCES = dict(horizons.RESOURCES)
FAMILIES = horizons.FAMILIES
EXPECTED_REFERENCE_SESSIONS = horizons.EXPECTED_REFERENCE_SESSIONS
EXPECTED_COMPLETE_PAIRS = horizons.EXPECTED_COMPLETE_PAIRS
EXPECTED_INPUT_COUNTS = {"unique_target_days": 5158, "queried_rows": 1002211}
SELECTION_STATES = ("pass", "reject", "unknown", "non_signal")
FLAGS = {**cc.FLAGS, "exploratory_only": True, "actual_portfolio_pnl": False,
    "family_adopted": False, "comparison_performed": False, "underpower_exception_applied": False}
ASSUMPTIONS = {
    "minute_label": "source timestamp floored to open minute",
    "bar_available_at": "feature end plus two minutes; no later than original event decision",
    "session_schedule": cd.ASSUMPTIONS["session_schedule"],
    "feature": "prior 30-minute first open to last close, ending at original signal_bar_end; all original causal selected peers equally weighted",
    "cohort": "only original CD 120-minute complete pairs inherited through CF can supply future labels; conditional on future completeness, not executable inclusion",
    "upstream": "pinned independently verified CF events, causal peers and all five own labels reused without detector or future-label replay",
    "frequency": "selection opportunities by original event, month and ISO week; not fills or independent samples",
    "power": "selected event own-label dispersion over all reference sessions including zero-event dates; no underpower exception transfers",
    "cost": "33bp planning assumption only; no fee, fill, historical availability or tradability certification",
    "source": "collector klines retain assumed observation/publication and unresolved source, calendar, revision and price-basis qualifications",
    "runtime": "upstream CF completed separately; original CD runtime conformance remains unresolved; this report requires its own shared-budget receipt"}
_canonical, _sha = cc._canonical, cc._sha


class EntryStrengthRefusal(RuntimeError):
    """Stable reason codes omit private evidence values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        "parameters": PARAMETERS, "resources": RESOURCES, **REGISTERED_PATHS,
        "upstream_sha256": UPSTREAM_SHA256, "input_compressed_sha256": INPUT_COMPRESSED_SHA256,
        "input_stream_sha256": INPUT_STREAM_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise EntryStrengthRefusal("specification_scope_mismatch")


def _matrix(report: Mapping) -> tuple[dict, list[str], dict]:
    if (report.get("study_id") != horizons.SPECIFICATION_ID or report.get("mode") != horizons.MODE
            or report.get("parameters") != horizons.PARAMETERS or report.get("window") != WINDOW
            or any(report.get(key) != value for key, value in horizons.FLAGS.items())):
        raise EntryStrengthRefusal("wrong_upstream_study_or_scope")
    projection = {"study_id": cc.SPECIFICATION_ID, "parameters": cc.PARAMETERS,
        **cc.FLAGS, "variants": report["variants"]}
    matrix, reference, union = cd._matrix(projection)
    if report.get("reference_sessions") != reference:
        raise EntryStrengthRefusal("upstream_reference_sessions_mismatch")
    for grouped in matrix.values():
        for rows in grouped.values():
            names = {row["symbol"] for row in rows}
            for row in rows:
                if set(row.get("families", {})) != set(FAMILIES):
                    raise EntryStrengthRefusal("upstream_family_population_mismatch")
                for family, finding in row["families"].items():
                    if finding.get("status") not in cd.STATES:
                        raise EntryStrengthRefusal("invalid_upstream_state")
                    if finding["status"] != "signal":
                        if finding.get("event") is not None or finding.get("original_pair_status") is not None or finding.get("responses"):
                            raise EntryStrengthRefusal("upstream_nonsignal_has_event_or_labels")
                        continue
                    if (not isinstance(finding.get("event"), Mapping)
                            or finding["event"].get("family") != family):
                        raise EntryStrengthRefusal("upstream_event_family_mismatch")
                    peers = finding.get("selected_controls")
                    if not isinstance(peers, list):
                        raise EntryStrengthRefusal("invalid_upstream_control_population")
                    selected = [item["symbol"] for item in peers]
                    if selected != sorted(set(selected)) or row["symbol"] in selected or not set(selected) <= names:
                        raise EntryStrengthRefusal("invalid_upstream_control_population")
                    complete = finding.get("original_pair_status") == "complete"
                    if (finding.get("original_pair_status") not in {"complete", "unknown"}
                            or complete != (bool(peers) and not finding.get("original_pair_unknown_reasons"))):
                        raise EntryStrengthRefusal("invalid_upstream_pair_state")
                    if set(finding.get("responses", {})) != {str(h) for h in HORIZONS}:
                        raise EntryStrengthRefusal("upstream_horizon_population_mismatch")
                    for response in finding["responses"].values():
                        if response.get("status") != ("measured" if complete else "not_measured_outside_fixed_cohort"):
                            raise EntryStrengthRefusal("upstream_label_cohort_mismatch")
                        if complete:
                            horizons._ratio(response["event_price_change"])
                        elif any(response.get(key) is not None for key in
                                ("event_price_change", "control_mean_price_change", "excess_price_change")):
                            raise EntryStrengthRefusal("upstream_unknown_pair_has_labels")
    return matrix, reference, union


def _prior_return(minutes: Mapping, start: int, symbol: str) -> dict:
    values = [minutes.get(start + offset) for offset in range(30)]
    missing = sum(value is None for value in values)
    return {"symbol": symbol, "status": "unknown" if missing else "known",
        "missing_minute_buckets": missing,
        "unknown_reason": "incomplete_30minute_feature_path" if missing else None,
        "first_open": None if missing else values[0][0],
        "last_close": None if missing else values[-1][3],
        "price_change": None if missing else Fraction(values[-1][3]) / Fraction(values[0][0]) - 1}


def _feature(saved: Mapping, sessions: Mapping, day: str, symbol: str) -> tuple[dict, str, list[str]]:
    end = horizons._instant(saved["event"]["signal_bar_end"])
    decision = horizons._instant(saved["event"]["decision_at"])
    start, available = end - 30 * cc.MINUTE, end + 2 * cc.MINUTE
    opening = cc.session_open(day)
    elapsed = (start - opening) / cc.MINUTE
    if (not elapsed.is_integer() or end <= opening or end > cc.session_close(day)
            or decision < end or decision > cc.session_close(day)):
        raise EntryStrengthRefusal("invalid_upstream_feature_boundary")
    event = _prior_return(sessions[symbol], int(elapsed), symbol)
    peers = [_prior_return(sessions[item["symbol"]], int(elapsed), item["symbol"])
        for item in saved["selected_controls"]]
    reasons = []
    if available > decision:
        reasons.append("feature_unavailable_at_original_decision")
    if event["status"] != "known":
        reasons.append("event_feature_unknown")
    if not peers:
        reasons.append("no_original_selected_controls")
    if any(item["status"] != "known" for item in peers):
        reasons.append("original_selected_control_feature_unknown")
    peer_mean = (sum((item["price_change"] for item in peers), Fraction()) / len(peers)
        if peers and all(item["status"] == "known" for item in peers) else None)
    excess = event["price_change"] - peer_mean if event["status"] == "known" and peer_mean is not None else None
    feature = {"start": start, "end": end, "available_at": available, "decision_at": decision,
        "event": event, "peers": peers, "peer_mean_price_change": peer_mean, "excess_price_change": excess}
    return feature, ("unknown" if reasons else "pass" if excess > 0 else "reject"), reasons


def _counts(rows: list[dict]) -> dict:
    original = Counter(row["status"] for row in rows)
    selection = Counter(row["selection_state"] for row in rows)
    pairs = Counter(row["original_pair_status"] for row in rows if row["status"] == "signal")
    return {"counts": {state: original[state] for state in cd.STATES},
        "selection_counts": {state: selection[state] for state in SELECTION_STATES},
        "original_pair_counts": {state: pairs[state] for state in ("complete", "unknown")},
        "selected_complete": sum(row["label_cohort_state"] == "selected_complete" for row in rows)}


def _own_power(records: list[dict], reference: list[str], checkpoint: Callable[[], None]) -> dict:
    own = horizons._statistics(records, reference, checkpoint)
    design = own["design_se"]
    mde = (NormalDist().inv_cdf(.975) + NormalDist().inv_cdf(.8)) * design * 10000 if design is not None else None
    constraint = ("insufficient_known_labels_or_reference_sessions" if len(records) < 2 or len(reference) < 2
        else "insufficient_known_label_sessions" if own["known_label_sessions"] < 2
        else "insufficient_valid_bootstrap_draws" if design is None
        else "own_label_mde_exceeds_assumed_cost_floor" if mde > 33 else "cost_floor_power_prerequisite_met")
    return {**own, "measured_labels": len(records), "mde_bps": mde,
        "cost_floor_bps_assumed": "33", "requires_operator_choice": mde is None or mde > 33,
        "planning_constraint": constraint, "underpower_exception_applied": False}


def build_calibration(cf_report: Mapping, raw_chunks: Mapping | Iterable[Mapping], *,
        checkpoint: Callable[[], None] | None = None) -> dict:
    """Pure supplied-input calibration, preserving the complete original matrix."""
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    matrix, reference, union = _matrix(cf_report)
    expected = [(day, symbol) for day in reference for symbol in sorted(union[day])]
    if isinstance(raw_chunks, Mapping):
        if set(raw_chunks) != set(expected):
            raise EntryStrengthRefusal("input_chunk_population_mismatch")
        chunks = iter(raw_chunks[key] for key in expected)
    else:
        chunks = iter(raw_chunks)
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "reference_sessions": reference, "assumptions": ASSUMPTIONS, **FLAGS,
        "variants": {key: {"days": [], "families": {}} for key in matrix}}
    chunk_count, row_count = 0, 0
    for day in reference:
        checkpoint()
        sessions, vintages = {}, {}
        for symbol in sorted(union[day]):
            checkpoint()
            try:
                chunk = next(chunks)
            except StopIteration as exc:
                raise EntryStrengthRefusal("missing_input_chunk") from exc
            if not isinstance(chunk, Mapping) or chunk.get("symbol") != symbol or chunk.get("session") != day:
                raise EntryStrengthRefusal("input_chunk_order_or_identity_mismatch")
            if (chunk.get("observation_date") != day or chunk.get("available_at") is not None
                    or chunk.get("source") != "collector klines" or chunk.get("is_final") is not None
                    or chunk.get("evidence_level") != "assumed"):
                raise EntryStrengthRefusal("input_chunk_metadata_mismatch")
            raw = chunk.get("rows")
            if not isinstance(raw, list) or len(raw) > RESOURCES["maximum_session_rows"]:
                raise EntryStrengthRefusal("input_session_row_budget_exceeded")
            chunk_count += 1
            row_count += len(raw)
            if row_count > RESOURCES["maximum_rows"]:
                raise EntryStrengthRefusal("input_row_budget_exceeded")
            vintages[symbol] = hashlib.sha256(_canonical(chunk) + b"\n").hexdigest()
            sessions[symbol] = horizons._session_minutes(raw, day, symbol, checkpoint)
        for key, grouped in matrix.items():
            for row in grouped[day]:
                if row["data_vintage"] != vintages[row["symbol"]]:
                    raise EntryStrengthRefusal("upstream_chunk_vintage_mismatch")
                output = {name: row[name] for name in ("symbol", "session", "data_vintage", "observation_date",
                    "membership_observation_date", "membership_available_at", "membership_source")}
                output["families"] = {}
                for family, saved in row["families"].items():
                    finding = {name: saved[name] for name in ("status", "original_pair_status",
                        "original_pair_unknown_reasons", "original_unknown_control_symbols",
                        "original_event_label_state", "excluded_controls")}
                    finding["event"] = ({name: saved["event"][name] for name in ("decision_at", "signal_bar_end")}
                        if saved["status"] == "signal" else None)
                    finding["selected_controls"] = [{"symbol": peer["symbol"]} for peer in saved["selected_controls"]]
                    finding.update(feature=None, selection_state="non_signal", selection_unknown_reasons=[],
                        label_cohort_state="non_signal")
                    if saved["status"] == "signal":
                        feature, state, reasons = _feature(saved, sessions, day, row["symbol"])
                        finding.update(feature=feature, selection_state=state, selection_unknown_reasons=reasons,
                            label_cohort_state=("outside_original_complete_cohort" if saved["original_pair_status"] != "complete"
                                else "selected_complete" if state == "pass" else "not_selected"))
                    output["families"][family] = finding
                result["variants"][key]["days"].append(output)
    try:
        next(chunks)
    except StopIteration:
        pass
    else:
        raise EntryStrengthRefusal("extra_input_chunk")
    evidence = cf_report["input_evidence"]
    if (evidence["unique_target_days"] != chunk_count or evidence["queried_rows"] != row_count
            or evidence["compressed_sha256"] != INPUT_COMPRESSED_SHA256
            or evidence["stream_sha256"] != INPUT_STREAM_SHA256):
        raise EntryStrengthRefusal("upstream_input_count_or_pin_mismatch")
    result["input_evidence"] = {"upstream_report_sha256": UPSTREAM_SHA256,
        "archive_path": REGISTERED_PATHS["input_archive_path"], "compressed_sha256": INPUT_COMPRESSED_SHA256,
        "stream_sha256": INPUT_STREAM_SHA256, "unique_target_days": chunk_count, "queried_rows": row_count}
    for key, variant in result["variants"].items():
        for family in FAMILIES:
            rows = [{"session": row["session"], **row["families"][family]} for row in variant["days"]]
            selected = {(row["session"], row["symbol"]) for row in variant["days"]
                if row["families"][family]["label_cohort_state"] == "selected_complete"}
            own = {}
            for horizon in HORIZONS:
                checkpoint()
                records = [{"session": row["session"], "label": horizons._ratio(
                    row["families"][family]["responses"][str(horizon)]["event_price_change"])}
                    for row in cf_report["variants"][key]["days"] if (row["session"], row["symbol"]) in selected]
                own[str(horizon)] = _own_power(records, reference, checkpoint)
            def week(day):
                iso = date.fromisoformat(day).isocalendar()
                return f"{iso.year:04d}-W{iso.week:02d}"
            variant["families"][family] = {**_counts(rows),
                "by_month": {month: _counts([row for row in rows if row["session"][:7] == month])
                    for month in sorted({day[:7] for day in reference})},
                "by_week": {period: _counts([row for row in rows if week(row["session"]) == period])
                    for period in sorted({week(day) for day in reference})},
                "by_horizon": own, "requires_operator_choice": any(item["requires_operator_choice"] for item in own.values())}
    result["requires_operator_choice"] = any(summary["requires_operator_choice"]
        for variant in result["variants"].values() for summary in variant["families"].values())
    checkpoint()
    return result


def run_calibration(*, specification_path: str | Path, upstream_path: str | Path,
        input_archive_path: str | Path, output_directory: str | Path, runs_path: str | Path,
        budget=None, before_completion: Callable[[], None] | None = None) -> dict:
    """Log durable start before opening saved CF or minute evidence."""
    if budget is None:
        from research.research_budget import ResearchBudget
        budget = ResearchBudget()
    checkpoint = lambda: budget.checkpoint("entry_strength_pipeline")
    checkpoint()
    spec_path, upstream, archive, output, log = map(Path,
        (specification_path, upstream_path, input_archive_path, output_directory, runs_path))
    if spec_path != SPECIFICATION_PATH:
        raise EntryStrengthRefusal("unregistered_specification_path")
    spec_bytes = spec_path.read_bytes()
    spec, spec_sha = json.loads(spec_bytes), hashlib.sha256(spec_bytes).hexdigest()
    validate_spec(spec)
    paths = (spec_path, upstream, archive, output, log)
    for path, key in zip((upstream, archive, output, log), REGISTERED_PATHS, strict=True):
        if not path.is_absolute() or path != Path(spec[key]):
            raise EntryStrengthRefusal("unregistered_path")
    for index, path in enumerate(paths):
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise EntryStrengthRefusal("symlinked_path")
        if any(path.resolve() == other.resolve() or (path.exists() and other.exists() and path.samefile(other)) for other in paths[:index]):
            raise EntryStrengthRefusal("aliased_paths")
    sources = (Path(__file__), Path(horizons.__file__), Path(cd.__file__), Path(cc.__file__), Path(cc.core.__file__),
        Path(experiment_log.__file__), Path(cd.conclusion_check.__file__),
        ROOT / "python/research/research_budget.py", ROOT / "python/research/__init__.py",
        ROOT / "python/pyproject.toml", ROOT / "python/uv.lock", spec_path)
    pins = {str(path): _sha(path) for path in sources}
    executable = Path(sys.executable).resolve(strict=True)
    runtime = {"python": sys.version, "implementation": sys.implementation.name,
        "executable": str(executable), "executable_sha256": _sha(executable)}
    if pins[str(spec_path)] != spec_sha:
        raise EntryStrengthRefusal("specification_changed")

    def evaluate() -> dict:
        checkpoint()
        if not output.parent.is_dir() or os.path.lexists(output):
            raise EntryStrengthRefusal("unsafe_or_existing_output")
        if shutil.disk_usage(output.parent).free < RESOURCES["minimum_free_bytes"]:
            raise EntryStrengthRefusal("insufficient_free_disk")
        output.mkdir(mode=0o700)
        cc._sync_directory(output.parent)
        if output.stat().st_mode & 0o777 != 0o700:
            raise EntryStrengthRefusal("unsafe_output_permissions")
        with upstream.open("rb") as stream:
            upstream_bytes = stream.read(RESOURCES["maximum_upstream_bytes"] + 1)
        checkpoint()
        if len(upstream_bytes) > RESOURCES["maximum_upstream_bytes"] or hashlib.sha256(upstream_bytes).hexdigest() != UPSTREAM_SHA256:
            raise EntryStrengthRefusal("upstream_size_or_hash_mismatch")
        if archive.stat().st_size > RESOURCES["maximum_compressed_bytes"] or _sha(archive) != INPUT_COMPRESSED_SHA256:
            raise EntryStrengthRefusal("compressed_input_size_or_hash_mismatch")
        checkpoint()
        upstream_report = json.loads(upstream_bytes)
        del upstream_bytes
        report = build_calibration(upstream_report, horizons._archive_chunks(archive, checkpoint), checkpoint=checkpoint)
        del upstream_report
        if len(report["reference_sessions"]) != EXPECTED_REFERENCE_SESSIONS:
            raise EntryStrengthRefusal("registered_reference_population_changed")
        for key, variant in report["variants"].items():
            for family in FAMILIES:
                if variant["families"][family]["original_pair_counts"]["complete"] != EXPECTED_COMPLETE_PAIRS[key][family]:
                    raise EntryStrengthRefusal("registered_complete_pair_population_changed")
        if any(report["input_evidence"][key] != value for key, value in EXPECTED_INPUT_COUNTS.items()):
            raise EntryStrengthRefusal("registered_raw_population_changed")
        report.update(specification_sha256=spec_sha, source_sha256=pins, runtime=runtime)
        checkpoint()
        report_path = output / "report.json"
        with cc._private_file(report_path) as stream:
            encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                allow_nan=False, default=lambda value: json.loads(_canonical(value)))
            for index, chunk in enumerate(encoder.iterencode(report)):
                if index % 1024 == 0:
                    checkpoint()
                stream.write(chunk.encode())
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        cc._sync_directory(output)
        report_sha = _sha(report_path)
        if before_completion is not None:
            before_completion()
        checkpoint()
        if (any(_sha(path) != pins[str(path)] for path in sources) or _sha(upstream) != UPSTREAM_SHA256
                or _sha(archive) != INPUT_COMPRESSED_SHA256):
            raise EntryStrengthRefusal("source_specification_or_input_changed")
        if _sha(executable) != runtime["executable_sha256"] or _sha(report_path) != report_sha:
            raise EntryStrengthRefusal("runtime_or_output_changed")
        checkpoint()
        return {"output_path": str(report_path), "output_sha256": report_sha,
            "input_evidence": report["input_evidence"], "requires_operator_choice": report["requires_operator_choice"], **FLAGS}

    return experiment_log.run_discovery_trial(study_id=SPECIFICATION_ID, specification_sha256=spec_sha,
        parameters={"mode": MODE, "parameters": PARAMETERS, "source_sha256": pins, "runtime": runtime,
            "upstream_sha256": UPSTREAM_SHA256, "input_compressed_sha256": INPUT_COMPRESSED_SHA256,
            "input_stream_sha256": INPUT_STREAM_SHA256, "resources": RESOURCES},
        window=dict(WINDOW), evaluate=evaluate, runs_path=log)
