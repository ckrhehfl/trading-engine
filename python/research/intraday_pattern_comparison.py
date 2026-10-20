"""One frozen exploratory comparison of saved CC intraday price labels.

Controls are selected using only the event's information boundary. Every
selected control must subsequently have a complete label for a paired result.
No database, exchange, parameter search, fills or portfolio accounting occurs.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from fractions import Fraction
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
from statistics import median, stdev
import sys
import time

from research import experiment_log
from research import conclusion_check
from research import intraday_pattern_calibration as cc


SPECIFICATION_ID = "intraday-pattern-comparison-v1"
MODE = "limited-exploratory-price-label-comparison"
ROOT = Path(__file__).resolve().parents[2]
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-pattern-comparison-v1.json"
WINDOW = dict(cc.WINDOW)
PARAMETERS = {**cc.PARAMETERS,
    "control_selection": "other_variant_day_members_with_valid_prefix_through_event_decision",
    "control_weighting": "equal_all_preselected",
    "pair_requirement": "event_and_all_preselected_controls_complete_same_boundary_label"}
REGISTERED_PATHS = {
    "upstream_path": "/home/minjun4897/research-evidence/intraday-cc-calibration-20261020-v1/report.json",
    "input_archive_path": "/home/minjun4897/research-evidence/intraday-cc-calibration-20261020-v1/inputs.jsonl.gz",
    "output_directory": "/home/minjun4897/research-evidence/intraday-cd-comparison-20261020-v1",
    "canonical_runs_path": "/home/minjun4897/trading-engine/runs/experiments.jsonl"}
UPSTREAM_SHA256 = "33c642ebbcda1ccca4c68de111629bdff19bc2b58897e71be737b7350522c8a7"
INPUT_COMPRESSED_SHA256 = "4a5380b5477476a55dccb402c1227b23e0e6a08bc9c92f2e742eef2d9a7b62de"
INPUT_STREAM_SHA256 = "d48593ce0da9d6cdca17ad00002e65fbcfda1f7b99f6b83c272ebb16943b36da"
RESOURCES = {"maximum_upstream_bytes": 67_108_864, "maximum_compressed_bytes": 536_870_912,
    "maximum_uncompressed_bytes": 1_073_741_824, "maximum_chunk_bytes": 1_048_576,
    "maximum_chunks": 6000, "maximum_session_rows": 600, "maximum_rows": 3_600_000,
    "minimum_free_bytes": 1_073_741_824, "address_space_mib": 512,
    "cpu_seconds": 300, "wall_seconds": 1200, "nice": 10, "automatic_retries": 0}
FLAGS = {**cc.FLAGS, "exploratory_only": True, "actual_portfolio_pnl": False,
    "recovery_underpower_exception": "limited_to_this_registered_comparison"}
ASSUMPTIONS = {
    "minute_label": "source timestamp floored to open minute",
    "bar_available_at": "bar end plus two minutes",
    "session_schedule": "09:00-15:30; 2025-11-13 10:00-16:30; 2026-01-02 10:00-15:30; assumed, not historical calendar certification",
    "cost": "33bp planning only; not verified historical fees, executable fills or portfolio PnL",
    "selection": "all other variant/day members with contiguous valid five-minute prefix through event decision; future labels never select controls",
    "measurement": "120-minute same-boundary price labels; complete event and every preselected control required",
    "dependence": "raw event counts are not independent samples; uncertainty clusters and resamples whole reference sessions, including zero-event dates"}
FAMILIES = cc.FAMILIES
EXPECTED_REFERENCE_SESSIONS = 257
PAIR_REASONS = ("no_selected_controls", "event_label_unknown", "selected_control_label_unknown")
STATES = ("signal", "known_no_signal", "unknown")
_canonical = cc._canonical
_sha = cc._sha


class ComparisonRefusal(RuntimeError):
    """Fixed reason codes avoid exposing saved source values in failures."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        "parameters": PARAMETERS, "resources": RESOURCES, **REGISTERED_PATHS,
        "upstream_sha256": UPSTREAM_SHA256, "input_compressed_sha256": INPUT_COMPRESSED_SHA256,
        "input_stream_sha256": INPUT_STREAM_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise ComparisonRefusal("specification_scope_mismatch")


def _matrix(report: Mapping) -> tuple[dict, list[str], dict[str, set[str]]]:
    if report.get("study_id") != cc.SPECIFICATION_ID or report.get("parameters") != cc.PARAMETERS:
        raise ComparisonRefusal("wrong_upstream_study_or_parameters")
    if any(report.get(key) is not False for key in cc.FLAGS):
        raise ComparisonRefusal("upstream_certification_changed")
    if set(report.get("variants", {})) != {"D+1", "D+2"}:
        raise ComparisonRefusal("invalid_timing_variants")
    matrix, union, reference = {}, {}, None
    for variant, content in report["variants"].items():
        grouped = {}
        for row in content["days"]:
            day, symbol = row["session"], row["symbol"]
            if not WINDOW["start"] <= day <= WINDOW["end"] or not re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol):
                raise ComparisonRefusal("target_outside_registered_population")
            grouped.setdefault(day, []).append(row)
        dates = list(grouped)
        if not dates or dates != sorted(set(dates)) or (reference is not None and dates != reference):
            raise ComparisonRefusal("target_matrix_lost_sessions")
        reference = dates
        for day, rows in grouped.items():
            if len(rows) != 20 or len({row["symbol"] for row in rows}) != 20:
                raise ComparisonRefusal("target_population_changed")
            membership = None
            for row in rows:
                source = row["membership_source"]
                if (datetime.fromisoformat(source["available_at"]) > datetime.fromisoformat(source["selected_at"])
                        or datetime.fromisoformat(source["selected_at"]) >= cc.session_open(day)):
                    raise ComparisonRefusal("noncausal_membership")
                if row["membership_available_at"] != source["available_at"] or row["membership_observation_date"] != source["observation_date"]:
                    raise ComparisonRefusal("inconsistent_membership_metadata")
                if membership is not None and _canonical(source) != membership:
                    raise ComparisonRefusal("inconsistent_day_membership")
                membership = _canonical(source)
            union.setdefault(day, set()).update(row["symbol"] for row in rows)
        matrix[variant] = grouped
    if sum(map(len, union.values())) > RESOURCES["maximum_chunks"] or len(set().union(*union.values())) > 260:
        raise ComparisonRefusal("target_budget_exceeded")
    return matrix, reference, union


def _label(session: cc.Session, decision: datetime) -> dict:
    label = cc.price_label(session, decision)
    entry = label["entry_label_start"]
    missing = sum(session.minutes.get(entry + index * cc.MINUTE) is None for index in range(120))
    return {**label, "missing_minute_buckets": missing,
        "unknown_reason": "incomplete_120minute_path" if missing else None}


def _distribution(values: list[Fraction]) -> dict:
    def describe(items: list[Fraction]) -> dict:
        return {"mean": math.fsum(float(value) for value in items) / len(items) if items else None,
            "median": median(items) if items else None,
            "positive_fraction": Fraction(sum(value > 0 for value in items), len(items)) if items else None}
    return {"gross": describe(values),
        "minus_33bp_assumption": describe([value - Fraction(33, 10000) for value in values])}


def _uncertainty(pairs: list[dict], reference: list[str]) -> dict:
    totals = {day: [0.0, 0] for day in reference}
    values = []
    for row in pairs:
        value = float(row["comparison"]["excess_price_change"])
        values.append(value)
        totals[row["session"]][0] += value
        totals[row["session"]][1] += 1
    n, groups = len(values), len(totals)
    known_groups = sum(count > 0 for _, count in totals.values())
    result = {"reference_sessions": groups, "known_pair_sessions": known_groups,
        "naive_se": None, "session_cluster_se": None, "bootstrap_se": None,
        "design_se": None, "corrected_to_naive_se_ratio": None, "bootstrap_zero_pair_draws": 0}
    if n < 2 or groups < 2:
        return result
    naive = stdev(values) / math.sqrt(n)
    result["naive_se"] = naive
    if known_groups < 2:
        return result
    mean = math.fsum(values) / n
    cluster = math.sqrt(groups / (groups - 1) * math.fsum(
        (total - count * mean) ** 2 for total, count in totals.values())) / n
    daily = list(totals.values())
    blocks = {size: [(math.fsum(daily[(index + offset) % groups][0] for offset in range(size)),
        sum(daily[(index + offset) % groups][1] for offset in range(size))) for index in range(groups)]
        for size in {5, groups % 5} if size}
    rng, draws = random.Random(0), []
    for _ in range(2000):
        sampled = [blocks[5][rng.randrange(groups)] for _ in range(groups // 5)]
        if groups % 5:
            sampled.append(blocks[groups % 5][rng.randrange(groups)])
        count = sum(item[1] for item in sampled)
        if count:
            draws.append(math.fsum(item[0] for item in sampled) / count)
    bootstrap = stdev(draws) if len(draws) >= 2 else None
    design = max(naive, cluster, bootstrap) if bootstrap is not None else None
    result.update(session_cluster_se=cluster, bootstrap_se=bootstrap, design_se=design,
        corrected_to_naive_se_ratio=design / naive if naive and design is not None else None,
        bootstrap_zero_pair_draws=2000 - len(draws))
    return result


def _summary(rows: list[dict], reference: list[str], *, uncertainty: bool) -> dict:
    counts = Counter(row["status"] for row in rows)
    events = [row for row in rows if row["status"] == "signal"]
    pairs = [row for row in events if row["comparison"]["pair_status"] == "complete"]
    comparisons = [row["comparison"] for row in events]
    controls = [control for item in comparisons for control in item["selected_controls"]]
    reasons = Counter(reason for item in comparisons for reason in item["pair_unknown_reasons"])
    values = [row["comparison"]["event_label"]["price_change"] for row in pairs]
    control_values = [row["comparison"]["control_mean_price_change"] for row in pairs]
    event_ids = [(row["session"], row["symbol"]) for row in pairs
        if row["comparison"]["event_label"]["price_change"] is not None]
    control_ids = [(row["session"], row["symbol"]) for row in pairs
        if row["comparison"]["control_mean_price_change"] is not None]
    conclusion_check.require_no_blockers([
        conclusion_check.check_same_population({"event": event_ids, "control": control_ids}),
        conclusion_check.check_clustered_observations([row["session"] for row in pairs],
            reported_n=len({row["session"] for row in pairs}))])
    return {"counts": {state: counts[state] for state in STATES},
        "pair_counts": {"complete": len(pairs), "unknown": len(events) - len(pairs)},
        "control_counts": {"selected": len(controls),
            "excluded_incomplete_prefix": sum(len(item["excluded_controls"]) for item in comparisons),
            "selected_label_known": sum(control["label"]["status"] == "known" for control in controls),
            "selected_label_unknown": sum(control["label"]["status"] == "unknown" for control in controls),
            "events_without_selected_controls": sum(not item["selected_controls"] for item in comparisons)},
        "pair_unknown_reason_counts": {reason: reasons[reason] for reason in PAIR_REASONS},
        "paired_complete_conditional": {"events": len(pairs), "event": _distribution(values),
            "control": _distribution(control_values),
            "excess_mean": math.fsum(float(row["comparison"]["excess_price_change"]) for row in pairs) / len(pairs) if pairs else None,
            "paired_mean_uncertainty": _uncertainty(pairs, reference) if uncertainty else None}}


def _check_finding(actual: dict, saved: Mapping) -> None:
    for key in ("status", "required_bars", "valid_prefix_bars", "label"):
        if _canonical(actual[key]) != _canonical(saved.get(key)):
            raise ComparisonRefusal("upstream_finding_replay_mismatch")
    actual_event, saved_event = actual["event"], saved.get("event")
    if actual_event is None:
        if saved_event is not None:
            raise ComparisonRefusal("upstream_event_replay_mismatch")
    elif not isinstance(saved_event, Mapping) or any(
            _canonical(value) != _canonical(saved_event.get(key)) for key, value in actual_event.items()):
        raise ComparisonRefusal("upstream_event_replay_mismatch")


def build_comparison(report: Mapping, raw_chunks: Mapping | Iterable[Mapping]) -> dict:
    """Replay supplied saved inputs in session order, retaining one day's prices.

    A mapping uses (session, symbol) keys and full archived chunk values. An
    iterable must have exactly CC's session/sorted-symbol archive order.
    Pure synthetic callers may supply a smaller date grid, always twenty names.
    """
    matrix, reference, union = _matrix(report)
    expected = [(day, symbol) for day in reference for symbol in sorted(union[day])]
    if isinstance(raw_chunks, Mapping):
        if set(raw_chunks) != set(expected):
            raise ComparisonRefusal("input_chunk_population_mismatch")
        chunks = iter(raw_chunks[key] for key in expected)
    else:
        chunks = iter(raw_chunks)
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        "parameters": PARAMETERS, "reference_sessions": reference, "assumptions": ASSUMPTIONS,
        **FLAGS, "variants": {key: {"days": [], "families": {}} for key in matrix}}
    chunk_count, row_count = 0, 0
    for day in reference:
        sessions, findings, vintages = {}, {}, {}
        for symbol in sorted(union[day]):
            try:
                chunk = next(chunks)
            except StopIteration as exc:
                raise ComparisonRefusal("missing_input_chunk") from exc
            if not isinstance(chunk, Mapping) or chunk.get("symbol") != symbol or chunk.get("session") != day:
                raise ComparisonRefusal("input_chunk_order_or_identity_mismatch")
            if (chunk.get("observation_date") != day or chunk.get("available_at") is not None
                    or chunk.get("source") != "collector klines" or chunk.get("is_final") is not None
                    or chunk.get("evidence_level") != "assumed"):
                raise ComparisonRefusal("input_chunk_metadata_mismatch")
            raw = chunk["rows"]
            if not isinstance(raw, list) or len(raw) > RESOURCES["maximum_session_rows"]:
                raise ComparisonRefusal("input_session_row_budget_exceeded")
            if any(row.get("symbol") != symbol for row in raw):
                raise ComparisonRefusal("input_raw_symbol_mismatch")
            row_count += len(raw)
            chunk_count += 1
            if row_count > RESOURCES["maximum_rows"]:
                raise ComparisonRefusal("input_row_budget_exceeded")
            vintages[symbol] = hashlib.sha256(_canonical(chunk) + b"\n").hexdigest()
            sessions[symbol] = cc.aggregate_session(raw, cc.session_open(day))
            findings[symbol] = cc.calibrate_session(sessions[symbol])
        for key, grouped in matrix.items():
            members = grouped[day]
            for row in members:
                symbol = row["symbol"]
                if row["data_vintage"] != vintages[symbol]:
                    raise ComparisonRefusal("upstream_chunk_vintage_mismatch")
                output = {name: row[name] for name in ("symbol", "session", "data_vintage",
                    "membership_observation_date", "membership_available_at", "membership_source")}
                output.update(observation_date=day, families={})
                for family in FAMILIES:
                    actual, saved = findings[symbol][family], row["families"][family]
                    _check_finding(actual, saved)
                    entry = {"status": actual["status"], "event": saved["event"], "comparison": None}
                    if actual["status"] == "signal":
                        event = actual["event"]
                        decision = event["decision_at"]
                        needed = (event["signal_bar_end"] - sessions[symbol].opening) // cc.FIVE
                        selected, excluded = [], []
                        # Freeze selection entirely before inspecting any control label.
                        for control in sorted(item["symbol"] for item in members if item["symbol"] != symbol):
                            count = sum(bar.available_at <= decision for bar in sessions[control].prefix)
                            item = {"symbol": control, "valid_prefix_bars_at_decision": count}
                            if count >= needed:
                                selected.append(item)
                            else:
                                excluded.append({**item, "reason": "incomplete_prefix_at_event_decision"})
                        label = _label(sessions[symbol], decision)
                        for control in selected:
                            control["label"] = _label(sessions[control["symbol"]], decision)
                        unknown_controls = [item["symbol"] for item in selected if item["label"]["status"] != "known"]
                        reasons = []
                        if not selected:
                            reasons.append("no_selected_controls")
                        if label["status"] != "known":
                            reasons.append("event_label_unknown")
                        if unknown_controls:
                            reasons.append("selected_control_label_unknown")
                        complete = not reasons
                        control_mean = sum((item["label"]["price_change"] for item in selected), Fraction()) / len(selected) if complete else None
                        entry["comparison"] = {"event_label": label, "selected_controls": selected,
                            "excluded_controls": excluded, "pair_status": "complete" if complete else "unknown",
                            "pair_unknown_reasons": sorted(reasons), "unknown_control_symbols": unknown_controls,
                            "control_mean_price_change": control_mean,
                            "excess_price_change": label["price_change"] - control_mean if complete else None}
                    output["families"][family] = entry
                result["variants"][key]["days"].append(output)
    try:
        next(chunks)
    except StopIteration:
        pass
    else:
        raise ComparisonRefusal("extra_input_chunk")
    evidence = report["input_evidence"]
    if evidence["unique_target_days"] != chunk_count or evidence["queried_rows"] != row_count:
        raise ComparisonRefusal("upstream_input_count_mismatch")
    result["input_evidence"] = {"upstream_report_sha256": UPSTREAM_SHA256,
        "archive_path": REGISTERED_PATHS["input_archive_path"],
        "compressed_sha256": evidence["compressed_sha256"], "stream_sha256": evidence["stream_sha256"],
        "unique_target_days": chunk_count, "queried_rows": row_count}
    for key, variant in result["variants"].items():
        for family in FAMILIES:
            rows = [{"session": row["session"], "symbol": row["symbol"], **row["families"][family]}
                for row in variant["days"]]
            summary = _summary(rows, reference, uncertainty=True)
            sizing = report["variants"][key]["families"][family]["dispersion"]
            summary["upstream_calibration_sizing"] = {name: sizing[name] for name in (
                "mde_bps", "cost_floor_bps_assumed", "would_require_operator_underpower_choice",
                "known_labels", "known_label_sessions")}
            summary["upstream_calibration_sizing"]["underpower_exception_applied"] = family == "post_drop_recovery"
            summary["by_month"] = {month: _summary([row for row in rows if row["session"][:7] == month],
                [day for day in reference if day[:7] == month], uncertainty=False) for month in sorted({day[:7] for day in reference})}
            summary["by_symbol"] = {symbol: _summary([row for row in rows if row["symbol"] == symbol],
                reference, uncertainty=False) for symbol in sorted({row["symbol"] for row in rows})}
            variant["families"][family] = summary
    return result


def _archive_chunks(path: Path) -> Iterable[dict]:
    digest, size, chunks = hashlib.sha256(), 0, 0
    with gzip.open(path, "rb") as stream:
        while True:
            line = stream.readline(RESOURCES["maximum_chunk_bytes"] + 1)
            if not line:
                break
            size += len(line)
            chunks += 1
            if (len(line) > RESOURCES["maximum_chunk_bytes"] or not line.endswith(b"\n")
                    or size > RESOURCES["maximum_uncompressed_bytes"] or chunks > RESOURCES["maximum_chunks"]):
                raise ComparisonRefusal("input_stream_budget_or_framing_mismatch")
            digest.update(line)
            yield json.loads(line)
    if digest.hexdigest() != INPUT_STREAM_SHA256:
        raise ComparisonRefusal("input_stream_hash_mismatch")


def run_comparison(*, specification_path: str | Path, upstream_path: str | Path,
        input_archive_path: str | Path, output_directory: str | Path, runs_path: str | Path,
        before_completion: Callable[[], None] | None = None) -> dict:
    """Only the exact registered saved artifacts; started is durable before reads."""
    spec_path, upstream, archive, output, log = map(Path,
        (specification_path, upstream_path, input_archive_path, output_directory, runs_path))
    if spec_path.resolve() != SPECIFICATION_PATH.resolve():
        raise ComparisonRefusal("unregistered_specification_path")
    spec_bytes = spec_path.read_bytes()
    spec, spec_sha = json.loads(spec_bytes), hashlib.sha256(spec_bytes).hexdigest()
    validate_spec(spec)
    paths = (spec_path, upstream, archive, output, log)
    for path, key in zip((upstream, archive, output, log), REGISTERED_PATHS, strict=True):
        if not path.is_absolute() or path.resolve() != Path(spec[key]).resolve():
            raise ComparisonRefusal("unregistered_path")
    for index, path in enumerate(paths):
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise ComparisonRefusal("symlinked_path")
        if any(path.resolve() == other.resolve() or (path.exists() and other.exists() and path.samefile(other)) for other in paths[:index]):
            raise ComparisonRefusal("aliased_paths")
    sources = (Path(__file__), Path(cc.__file__), Path(cc.core.__file__), Path(experiment_log.__file__),
        Path(conclusion_check.__file__),
        ROOT / "python/research/__init__.py", ROOT / "python/pyproject.toml", ROOT / "python/uv.lock", spec_path)
    pins = {str(path): _sha(path) for path in sources}
    executable = Path(sys.executable).resolve(strict=True)
    runtime = {"python": sys.version, "implementation": sys.implementation.name,
        "executable": str(executable), "executable_sha256": _sha(executable)}
    if pins[str(spec_path)] != spec_sha:
        raise ComparisonRefusal("specification_changed")

    def evaluate() -> dict:
        if not output.parent.is_dir() or os.path.lexists(output):
            raise ComparisonRefusal("unsafe_or_existing_output")
        if shutil.disk_usage(output.parent).free < RESOURCES["minimum_free_bytes"]:
            raise ComparisonRefusal("insufficient_free_disk")
        output.mkdir(mode=0o700)
        cc._sync_directory(output.parent)
        if output.stat().st_mode & 0o777 != 0o700:
            raise ComparisonRefusal("unsafe_output_permissions")
        started_wall, started_cpu = time.monotonic(), time.process_time()
        with upstream.open("rb") as stream:
            upstream_bytes = stream.read(RESOURCES["maximum_upstream_bytes"] + 1)
        if len(upstream_bytes) > RESOURCES["maximum_upstream_bytes"] or hashlib.sha256(upstream_bytes).hexdigest() != UPSTREAM_SHA256:
            raise ComparisonRefusal("upstream_size_or_hash_mismatch")
        if archive.stat().st_size > RESOURCES["maximum_compressed_bytes"] or _sha(archive) != INPUT_COMPRESSED_SHA256:
            raise ComparisonRefusal("compressed_input_size_or_hash_mismatch")
        upstream_report = json.loads(upstream_bytes)
        if (upstream_report["input_evidence"]["compressed_sha256"] != INPUT_COMPRESSED_SHA256
                or upstream_report["input_evidence"]["stream_sha256"] != INPUT_STREAM_SHA256):
            raise ComparisonRefusal("upstream_input_pin_mismatch")
        def bounded_chunks():
            for chunk in _archive_chunks(archive):
                if time.monotonic() - started_wall >= RESOURCES["wall_seconds"] or time.process_time() - started_cpu >= RESOURCES["cpu_seconds"]:
                    raise ComparisonRefusal("execution_budget_exceeded")
                yield chunk
        report = build_comparison(upstream_report, bounded_chunks())
        del upstream_report, upstream_bytes
        if len(report["reference_sessions"]) != EXPECTED_REFERENCE_SESSIONS:
            raise ComparisonRefusal("registered_reference_population_changed")
        report.update(specification_sha256=spec_sha, source_sha256=pins, runtime=runtime)
        report_path = output / "report.json"
        with cc._private_file(report_path) as stream:
            stream.write(_canonical(report) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        cc._sync_directory(output)
        report_sha = _sha(report_path)
        if before_completion is not None:
            before_completion()
        if (any(_sha(path) != pins[str(path)] for path in sources) or _sha(upstream) != UPSTREAM_SHA256
                or _sha(archive) != INPUT_COMPRESSED_SHA256):
            raise ComparisonRefusal("source_specification_or_input_changed")
        if _sha(executable) != runtime["executable_sha256"] or _sha(report_path) != report_sha:
            raise ComparisonRefusal("runtime_or_output_changed")
        return {"output_path": str(report_path), "output_sha256": report_sha,
            "input_evidence": report["input_evidence"], **FLAGS}

    return experiment_log.run_discovery_trial(study_id=SPECIFICATION_ID, specification_sha256=spec_sha,
        parameters={"mode": MODE, "parameters": PARAMETERS, "source_sha256": pins, "runtime": runtime,
            "upstream_sha256": UPSTREAM_SHA256, "input_compressed_sha256": INPUT_COMPRESSED_SHA256,
            "input_stream_sha256": INPUT_STREAM_SHA256, "resources": RESOURCES},
        window=dict(WINDOW), evaluate=evaluate, runs_path=log)
