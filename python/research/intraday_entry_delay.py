"""Own-label sizing for one-minute versus three-minute entry waits.

The original exit clock and selected-complete population stay fixed. This
calibration reports no future-return means or entry-policy comparison.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
from statistics import NormalDist
import sys

from research import experiment_log
from research import intraday_signal_paths as ci


ch, cg, horizons, cd, cc = ci.ch, ci.cg, ci.horizons, ci.cd, ci.cc
ROOT = ci.ROOT
SPECIFICATION_ID = "intraday-entry-delay-calibration-v1"
MODE = "fixed-exit-clock-earlier-entry-own-label-calibration"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-entry-delay-calibration-v1.json"
WINDOW = dict(ci.WINDOW)
FAMILIES = ci.FAMILIES
QUALIFIED_HORIZONS = ci.QUALIFIED_HORIZONS
EXCLUDED_HORIZONS = ci.EXCLUDED_HORIZONS
PARAMETERS = {
    "response_horizons_minutes": {family: list(values) for family, values in QUALIFIED_HORIZONS.items()},
    "cost_floor_bps": "33",
    "cohort": "exact_ch_selected_complete_mask_no_reselection_or_short_horizon_rescue",
    "decision": "original_signal_bar_end_plus_two_minutes_assumed",
    "current_entry": "strict_next_five_minute_boundary_after_decision_exact_three_minute_wait",
    "earlier_entry": "decision_plus_one_minute_exact_two_minutes_before_current_entry",
    "exit": "original_current_entry_plus_original_horizon_unchanged_for_both_labels",
    "price_label": "terminal_minute_close_divided_by_entry_minute_open_minus_one_all_intervening_minutes_valid",
    "label_availability": "unchanged_exit_plus_two_minutes_assumed_future_label_never_selection_feature",
    "missingness": "earlier_extra_two_minutes_unknown_keeps_full_selected_population_and_requires_choice_current_contradiction_refuses",
    "power": "retain_pinned_ch_current_own_dispersion_calculate_earlier_own_dispersion_all_reference_sessions",
    "resampling": "maximum_naive_session_cluster_circular_five_session_bootstrap_2000_draws_seed_zero",
    "output_scope": "own_label_dispersion_calibration_only_no_new_future_means_or_final_comparison_family"}
UPSTREAM_SHA256 = ci.UPSTREAM_SHA256
INPUT_COMPRESSED_SHA256 = ci.INPUT_COMPRESSED_SHA256
INPUT_STREAM_SHA256 = ci.INPUT_STREAM_SHA256
REGISTERED_PATHS = {**ci.REGISTERED_PATHS,
    "output_directory": "/home/minjun4897/research-evidence/intraday-ck-entry-delay-20261021-v1"}
RESOURCES = dict(ci.RESOURCES)
EXPECTED_REFERENCE_SESSIONS = ci.EXPECTED_REFERENCE_SESSIONS
EXPECTED_SELECTED_COMPLETE = ci.EXPECTED_SELECTED_COMPLETE
EXPECTED_INPUT_COUNTS = ci.EXPECTED_INPUT_COUNTS
ORIGINAL_FIELDS = ci.ORIGINAL_FIELDS
FLAGS = {**cc.FLAGS, "exploratory_only": True, "calibration_only": True,
    "actual_portfolio_pnl": False, "family_adopted": False, "horizon_selected": False,
    "comparison_performed": False, "earlier_entry_policy_compared": False,
    "underpower_exception_applied": False, "full_family_certified": False,
    "historical_costs_certified": False}
ASSUMPTIONS = {
    "measurement": "fixed-exit-clock current and earlier exact event price labels for own-dispersion sizing only; no new future-return means, costs comparison, paired future contrast or final comparison family",
    "cohort": "all original CH targets, unknowns, causal peers and selected-complete masks retained; original120minute future-completeness selection remains conditional",
    "timing": "D=S+2m assumed; current E is strict next5m boundary with E-D=3m; earlier A=D+1m=E-2m; unchanged X=E+h gives earlier holding h+2m",
    "availability": "label_available_at=X+2m is an assumed future-label boundary, never an entry feature or historical publication claim; retrieval timestamp is not historical availability",
    "prices": "same original minute-open and terminal-minute-close basis; all minutes in each label interval valid; no inferred intraminute fill or executable investment",
    "missingness": "invalid current path or mismatch with exact CH current return refuses; missing earlier extra buckets remain unknown without dropping selected events or zero filling",
    "power": "current own-label sizing is reused from pinned CH; earlier known-label dispersion uses all reference dates including zero-event dates; unknown new labels or failed either sizing arm require operator choice",
    "exceptions": "no CD, CE or other family's exploratory underpower exception transfers; no automatic final comparison registration or execution",
    "cost": "33bp is an unchanged assumed planning floor, not historical trading costs or a deduction from reported future labels",
    "source": "original source, calendar, eligibility, liquidity, revision, historical availability and price-basis qualifications remain uncertified",
    "interpretation": "no winning delay, horizon or parameter, p-value, take-profit/stop policy, edge, strategy pass, direction closure, reserved access or promotion"}
_canonical, _sha = cc._canonical, cc._sha


class EntryDelayRefusal(RuntimeError):
    """Stable reason codes deliberately omit saved evidence values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "resources": RESOURCES, **REGISTERED_PATHS, "upstream_sha256": UPSTREAM_SHA256,
        "input_compressed_sha256": INPUT_COMPRESSED_SHA256, "input_stream_sha256": INPUT_STREAM_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise EntryDelayRefusal("specification_scope_mismatch")


def _label_pair(minutes: Mapping, day: str, event: Mapping, horizon: int) -> dict:
    opening = cc.session_open(day)
    signal, decision = (horizons._instant(event[name]) for name in ("signal_bar_end", "decision_at"))
    entry = opening + ((decision - opening) // cc.FIVE + 1) * cc.FIVE
    earlier, end = decision + cc.MINUTE, entry + horizon * cc.MINUTE
    if (not ((signal - opening) / cc.MINUTE).is_integer() or signal <= opening
            or decision != signal + 2 * cc.MINUTE or entry - decision != 3 * cc.MINUTE
            or entry - earlier != 2 * cc.MINUTE or end > cc.session_close(day)):
        raise EntryDelayRefusal("invalid_upstream_signal_decision_or_entry_boundary")
    current = horizons._label(minutes, opening, entry, horizon)
    if current["status"] != "known":
        raise EntryDelayRefusal("upstream_complete_current_path_invalid")
    early = horizons._label(minutes, opening, earlier, horizon + 2)
    # Keep available anchors even when a newly included bucket is unknown.
    start = int((earlier - opening) / cc.MINUTE)
    early.update(entry_open=minutes[start][0] if minutes.get(start) is not None else None,
        terminal_close=current["terminal_close"])
    early["unknown_reason"] = "incomplete_earlier_entry_path" if early["status"] == "unknown" else None
    fields = ("status", "entry_open", "terminal_close", "price_change", "missing_minute_buckets", "unknown_reason")
    return {"signal_bar_end": signal, "decision_at": decision, "current_entry_at": entry,
        "earlier_entry_at": earlier, "exit_at": end, "original_holding_minutes": horizon,
        "earlier_holding_minutes": horizon + 2, "label_available_at": end + 2 * cc.MINUTE,
        "current": {name: current[name] for name in fields}, "earlier": {name: early[name] for name in fields}}


def _current_power_valid(own: object, selected: int, reference: list[str]) -> bool:
    """Validate the retained prerequisite, without recalculating old sizing."""
    if not isinstance(own, Mapping):
        return False
    numbers = ("event_sd", "naive_se", "session_cluster_se", "bootstrap_se", "design_se", "mde_bps")
    if any(type(own.get(name)) not in {int, float} or not math.isfinite(own[name]) or own[name] < 0 for name in numbers):
        return False
    expected_mde = (NormalDist().inv_cdf(.975) + NormalDist().inv_cdf(.8)) * own["design_se"] * 10000
    return (own.get("measured_labels") == selected and selected >= 2
        and own.get("reference_sessions") == len(reference)
        and type(own.get("known_label_sessions")) is int and 2 <= own["known_label_sessions"] <= min(selected, len(reference))
        and math.isclose(own["naive_se"], own["event_sd"] / math.sqrt(selected), rel_tol=1e-12, abs_tol=1e-15)
        and own["design_se"] == max(own[name] for name in ("naive_se", "session_cluster_se", "bootstrap_se"))
        and math.isclose(own["mde_bps"], expected_mde, rel_tol=1e-12, abs_tol=1e-12)
        and own["mde_bps"] <= 33 and own.get("cost_floor_bps_assumed") == "33"
        and own.get("requires_operator_choice") is False and own.get("underpower_exception_applied") is False
        and own.get("planning_constraint") == "cost_floor_power_prerequisite_met")


def build_calibration(ch_report: Mapping, raw_chunks: Mapping | Iterable[Mapping], *,
        checkpoint: Callable[[], None] | None = None) -> dict:
    """Pure supplied-input sizing, retaining every original target and unknown."""
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    matrix, reference, union = ci._matrix(ch_report)
    expected = [(day, symbol) for day in reference for symbol in sorted(union[day])]
    if isinstance(raw_chunks, Mapping):
        if set(raw_chunks) != set(expected):
            raise EntryDelayRefusal("input_chunk_population_mismatch")
        chunks = iter(raw_chunks[key] for key in expected)
    else:
        chunks = iter(raw_chunks)
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "reference_sessions": reference, "assumptions": ASSUMPTIONS, **FLAGS,
        "excluded_horizons_minutes": {family: list(values) for family, values in EXCLUDED_HORIZONS.items()},
        "upstream_calibration_requires_operator_choice": ch_report["upstream_calibration_requires_operator_choice"],
        "variants": {key: {"days": [], "families": {}} for key in matrix}}
    chunk_count, row_count = 0, 0
    for day in reference:
        checkpoint()
        sessions, vintages, metadata, cache = {}, {}, {}, {}
        for symbol in sorted(union[day]):
            checkpoint()
            try:
                chunk = next(chunks)
            except StopIteration as exc:
                raise EntryDelayRefusal("missing_input_chunk") from exc
            if not isinstance(chunk, Mapping) or chunk.get("symbol") != symbol or chunk.get("session") != day:
                raise EntryDelayRefusal("input_chunk_order_or_identity_mismatch")
            if (chunk.get("observation_date") != day or chunk.get("available_at") is not None
                    or chunk.get("source") != "collector klines" or chunk.get("is_final") is not None
                    or chunk.get("evidence_level") != "assumed"):
                raise EntryDelayRefusal("input_chunk_metadata_mismatch")
            horizons._instant(chunk.get("retrieved_at"))
            raw = chunk.get("rows")
            if not isinstance(raw, list) or len(raw) > RESOURCES["maximum_session_rows"]:
                raise EntryDelayRefusal("input_session_row_budget_exceeded")
            chunk_count += 1
            row_count += len(raw)
            if row_count > RESOURCES["maximum_rows"]:
                raise EntryDelayRefusal("input_row_budget_exceeded")
            vintages[symbol] = hashlib.sha256(_canonical(chunk) + b"\n").hexdigest()
            metadata[symbol] = {name: chunk[name] for name in ("observation_date", "available_at", "retrieved_at", "source", "is_final", "evidence_level")}
            sessions[symbol] = horizons._session_minutes(raw, day, symbol, checkpoint)
        for key, grouped in matrix.items():
            for row in grouped[day]:
                if row["data_vintage"] != vintages[row["symbol"]]:
                    raise EntryDelayRefusal("upstream_chunk_vintage_mismatch")
                output = {name: row[name] for name in ("symbol", "session", "data_vintage")}
                output.update(raw_chunk_metadata=metadata[row["symbol"]], families={})
                for family, saved in row["families"].items():
                    finding = {name: saved[name] for name in ORIGINAL_FIELDS}
                    finding["labels"] = None
                    if saved["label_cohort_state"] == "selected_complete":
                        finding["labels"] = {}
                        event = saved["event"]
                        for horizon in QUALIFIED_HORIZONS[family]:
                            checkpoint()
                            cache_key = (row["symbol"], event["signal_bar_end"], event["decision_at"], horizon)
                            if cache_key not in cache:
                                cache[cache_key] = _label_pair(sessions[row["symbol"]], day, event, horizon)
                            pair = cache[cache_key]
                            if pair["current"]["price_change"] != horizons._ratio(saved["responses"][str(horizon)]["event_price_change"]):
                                raise EntryDelayRefusal("upstream_current_label_mismatch")
                            finding["labels"][str(horizon)] = pair
                    output["families"][family] = finding
                result["variants"][key]["days"].append(output)
    try:
        next(chunks)
    except StopIteration:
        pass
    else:
        raise EntryDelayRefusal("extra_input_chunk")
    result["input_evidence"] = {"upstream_report_sha256": UPSTREAM_SHA256,
        "upstream_report_path": REGISTERED_PATHS["upstream_path"], "archive_path": REGISTERED_PATHS["input_archive_path"],
        "compressed_sha256": INPUT_COMPRESSED_SHA256, "stream_sha256": INPUT_STREAM_SHA256,
        "unique_target_days": chunk_count, "queried_rows": row_count}
    for key, variant in result["variants"].items():
        for family in FAMILIES:
            counts = cg._counts([row["families"][family] for row in variant["days"]])
            summaries = {}
            for horizon in QUALIFIED_HORIZONS[family]:
                checkpoint()
                records = [{"session": row["session"], "label": row["families"][family]["labels"][str(horizon)]["earlier"]["price_change"]}
                    for row in variant["days"] if row["families"][family]["labels"] is not None
                    and row["families"][family]["labels"][str(horizon)]["earlier"]["status"] == "known"]
                own = cg._own_power(records, reference, checkpoint)
                current = ch_report["variants"][key]["families"][family]["by_horizon"][str(horizon)]["own_label_dispersion"]
                valid = _current_power_valid(current, counts["selected_complete"], reference)
                unknown = counts["selected_complete"] - len(records)
                summaries[str(horizon)] = {"selected_complete": counts["selected_complete"],
                    "earlier_known_labels": len(records), "earlier_unknown_labels": unknown,
                    "current_own_power": current, "current_power_prerequisite_met": valid,
                    "earlier_own_power": own,
                    "requires_operator_choice": bool(unknown or not valid or own["requires_operator_choice"])}
            variant["families"][family] = {**counts, "by_horizon": summaries,
                "requires_operator_choice": any(cell["requires_operator_choice"] for cell in summaries.values())}
    result["requires_operator_choice"] = any(summary["requires_operator_choice"]
        for variant in result["variants"].values() for summary in variant["families"].values())
    checkpoint()
    return result


def run_calibration(*, specification_path: str | Path, upstream_path: str | Path,
        input_archive_path: str | Path, output_directory: str | Path, runs_path: str | Path,
        budget=None, before_completion: Callable[[], None] | None = None) -> dict:
    """A durable start before either input, sharing the caller's single budget."""
    if budget is None:
        from research.research_budget import ResearchBudget
        budget = ResearchBudget()
    checkpoint = lambda: budget.checkpoint("entry_delay_calibration_pipeline")
    checkpoint()
    spec_path, upstream, archive, output, log = map(Path,
        (specification_path, upstream_path, input_archive_path, output_directory, runs_path))
    if spec_path != SPECIFICATION_PATH:
        raise EntryDelayRefusal("unregistered_specification_path")
    spec_bytes = spec_path.read_bytes()
    spec, spec_sha = json.loads(spec_bytes), hashlib.sha256(spec_bytes).hexdigest()
    validate_spec(spec)
    paths = (spec_path, upstream, archive, output, log)
    for path, key in zip((upstream, archive, output, log), REGISTERED_PATHS, strict=True):
        if not path.is_absolute() or path != Path(REGISTERED_PATHS[key]):
            raise EntryDelayRefusal("unregistered_path")
    for index, path in enumerate(paths):
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise EntryDelayRefusal("symlinked_path")
        if any(path.resolve() == other.resolve() or (path.exists() and other.exists() and path.samefile(other)) for other in paths[:index]):
            raise EntryDelayRefusal("aliased_paths")
    sources = (spec_path, Path(__file__), Path(ci.__file__), Path(ch.__file__), Path(cg.__file__),
        ROOT / "python/research/research_budget.py", Path(horizons.__file__), Path(cd.__file__),
        Path(cc.__file__), Path(cc.core.__file__), Path(experiment_log.__file__), Path(cd.conclusion_check.__file__),
        ROOT / "python/research/__init__.py", ROOT / "python/pyproject.toml", ROOT / "python/uv.lock")
    pins = {str(path): _sha(path) for path in sources}
    executable = Path(sys.executable).resolve(strict=True)
    runtime = {"python": sys.version, "implementation": sys.implementation.name,
        "executable": str(executable), "executable_sha256": _sha(executable)}
    if pins[str(spec_path)] != spec_sha:
        raise EntryDelayRefusal("specification_changed")

    def evaluate() -> dict:
        checkpoint()
        if not output.parent.is_dir() or os.path.lexists(output):
            raise EntryDelayRefusal("unsafe_or_existing_output")
        if shutil.disk_usage(output.parent).free < RESOURCES["minimum_free_bytes"]:
            raise EntryDelayRefusal("insufficient_free_disk")
        output.mkdir(mode=0o700)
        cc._sync_directory(output.parent)
        if output.stat().st_mode & 0o777 != 0o700:
            raise EntryDelayRefusal("unsafe_output_permissions")
        with upstream.open("rb") as stream:
            upstream_bytes = stream.read(RESOURCES["maximum_upstream_bytes"] + 1)
        checkpoint()
        if len(upstream_bytes) > RESOURCES["maximum_upstream_bytes"] or hashlib.sha256(upstream_bytes).hexdigest() != UPSTREAM_SHA256:
            raise EntryDelayRefusal("upstream_size_or_hash_mismatch")
        if archive.stat().st_size > RESOURCES["maximum_compressed_bytes"] or _sha(archive) != INPUT_COMPRESSED_SHA256:
            raise EntryDelayRefusal("compressed_input_size_or_hash_mismatch")
        upstream_report = json.loads(upstream_bytes)
        del upstream_bytes
        report = build_calibration(upstream_report, horizons._archive_chunks(archive, checkpoint), checkpoint=checkpoint)
        del upstream_report
        if len(report["reference_sessions"]) != EXPECTED_REFERENCE_SESSIONS:
            raise EntryDelayRefusal("registered_reference_population_changed")
        for key, variant in report["variants"].items():
            for family in FAMILIES:
                if variant["families"][family]["selected_complete"] != EXPECTED_SELECTED_COMPLETE[key][family]:
                    raise EntryDelayRefusal("registered_selected_population_changed")
        if any(report["input_evidence"][key] != value for key, value in EXPECTED_INPUT_COUNTS.items()):
            raise EntryDelayRefusal("registered_raw_population_changed")
        report.update(specification_sha256=spec_sha, source_sha256=pins, runtime=runtime)
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
        if (any(_sha(path) != pins[str(path)] for path in sources)
                or _sha(upstream) != UPSTREAM_SHA256 or _sha(archive) != INPUT_COMPRESSED_SHA256):
            raise EntryDelayRefusal("source_specification_or_input_changed")
        if _sha(executable) != runtime["executable_sha256"] or _sha(report_path) != report_sha:
            raise EntryDelayRefusal("runtime_or_output_changed")
        checkpoint()
        return {"output_path": str(report_path), "output_sha256": report_sha,
            "input_evidence": report["input_evidence"], "requires_operator_choice": report["requires_operator_choice"], **FLAGS}

    return experiment_log.run_discovery_trial(study_id=SPECIFICATION_ID, specification_sha256=spec_sha,
        parameters={"mode": MODE, "parameters": PARAMETERS, "source_sha256": pins, "runtime": runtime,
            "upstream_sha256": UPSTREAM_SHA256, "input_compressed_sha256": INPUT_COMPRESSED_SHA256,
            "input_stream_sha256": INPUT_STREAM_SHA256, "resources": RESOURCES},
        window=dict(WINDOW), evaluate=evaluate, runs_path=log)
