"""Own-label calibration of one fixed close-triggered intraday exit rule.

Only supplied frozen CH events are labelled. Neither new performance means
nor a final comparison family is produced by this calibration.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

from research import experiment_log
from research import intraday_signal_paths as ci


ch, cg, horizons, cd, cc = ci.ch, ci.cg, ci.horizons, ci.cd, ci.cc
ROOT = ci.ROOT
SPECIFICATION_ID = "intraday-exit-policy-calibration-v1"
MODE = "fixed-entry-close-trigger-take-profit-stop-loss-time-own-label-calibration"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-exit-policy-calibration-v1.json"
WINDOW = dict(ci.WINDOW)
FAMILIES = ci.FAMILIES
ARMS = ("time_only", "managed")
TIME_CAP_MINUTES = 30
TAKE_PROFIT = Fraction(1, 100)
STOP_LOSS = Fraction(-1, 200)
PARAMETERS = {
    "time_cap_minutes": 30, "take_profit_gross_return": "0.01", "stop_loss_gross_return": "-0.005",
    "cost_floor_bps": "33",
    "cohort": "exact_ch_selected_complete_mask_original_120minute_path_no_reselection",
    "scope": "both_patterns_both_membership_variants_only_common_previously_qualified_30minute_cap",
    "decision": "original_signal_bar_end_plus_two_minutes_assumed",
    "entry": "strict_next_five_minute_boundary_after_decision_exact_three_minute_wait_entry_minute_open",
    "entry_price_availability": "entry_plus_three_minutes_assumed_not_used_to_select_entry",
    "time_only": "preplanned_at_original_decision_exit_open_at_entry_plus_30minutes",
    "trigger": "first_complete_minute_close_at_T_strictly_after_entry_return_ge_0.01_or_le_minus_0.005",
    "trigger_scan": "T_from_entry_plus_one_to_time_cap_minus_four_minutes_inclusive",
    "managed_fill": "trigger_T_plus_two_minute_availability_lag_plus_one_minute_execution_allowance_minute_open_strictly_before_cap",
    "pending": "first_eligible_trigger_wins_later_reversal_ignored_no_threshold_price_fill",
    "tie": "scheduled_fill_equal_to_cap_uses_preplanned_time_exit",
    "fallback": "no_eligible_trigger_both_arms_identical_time_cap_minute_open",
    "bridge": "original_CH_30minute_terminal_close_and_entry_open_exactly_reproduced_row_only_no_new_bridge_performance",
    "label_availability": "exit_minute_end_plus_two_minutes_assumed_future_label_never_selection",
    "missingness": "full_original_120minute_path_contradiction_refuses_no_new_label_zero_fill_or_selected_event_drop",
    "power": "both_new_open_exit_arms_own_dispersion_all_reference_sessions_including_zero_event_dates",
    "resampling": "maximum_naive_session_cluster_circular_five_session_bootstrap_2000_draws_seed_zero",
    "output_scope": "own_label_dispersion_calibration_only_no_new_future_means_or_final_comparison_family"}
UPSTREAM_SHA256 = ci.UPSTREAM_SHA256
INPUT_COMPRESSED_SHA256 = ci.INPUT_COMPRESSED_SHA256
INPUT_STREAM_SHA256 = ci.INPUT_STREAM_SHA256
REGISTERED_PATHS = {**ci.REGISTERED_PATHS,
    "output_directory": "/home/minjun4897/research-evidence/intraday-cm-exit-policy-20261021-v1"}
RESOURCES = dict(ci.RESOURCES)
EXPECTED_REFERENCE_SESSIONS = ci.EXPECTED_REFERENCE_SESSIONS
EXPECTED_SELECTED_COMPLETE = ci.EXPECTED_SELECTED_COMPLETE
EXPECTED_INPUT_COUNTS = ci.EXPECTED_INPUT_COUNTS
ORIGINAL_FIELDS = ci.ORIGINAL_FIELDS
FLAGS = {**cc.FLAGS, "exploratory_only": True, "calibration_only": True,
    "actual_portfolio_pnl": False, "family_adopted": False, "horizon_selected": False,
    "comparison_performed": False, "exit_policy_compared": False,
    "underpower_exception_applied": False, "full_family_certified": False,
    "historical_costs_certified": False, "actual_fills_certified": False}
ASSUMPTIONS = {
    "measurement": "one fixed grossTP1percent grossSLminus0.5percent 30minute cap rule; two new own-label sizing arms only; no means or aggregate trigger/profit/win counts",
    "cohort": "all original CH targets unknowns causal peers selected masks and original120minute future-completeness conditioning retained; 30minutes is common previously qualified scope not a selected winner",
    "timing": "D=S+2m assumed; entryE strict next5m boundary E-D=3m; capX=E+30m preplanned atD; complete closeT>E observed atT+2m and fillF=T+3m strictly<X",
    "availability": "entry open modeled availableE+3m; first eligible trigger decisionT+2m is at or after that; outcome label availableexit+3m assumed never selection; retrieval is not historical publication",
    "execution": "entry and exit minute opens are price proxies; first eligible close trigger wins while pending despite later reversal; no high/low touches threshold fills or actual-fill certification",
    "bridge": "originalCH30 close of[X-1m,X) and old entry open exactly reproduced only per row; reference arm exits openX which is a new label requiring its own sizing",
    "missingness": "all selected original120minute paths validated; policy prices lie within that path; contradiction refuses rather than zero filling dropping or rescuing selected events",
    "power": "both new arms use their own known-label dispersion all reference dates including zero-event dates; unknown labels or failed/unknown either prerequisite require operator choice; paired error cannot substitute",
    "exceptions": "no prior exploratory underpower exception transfers; no automatic comparison registration before ordinary prerequisites resolve",
    "cost": "33bp unchanged assumed planning floor only; not deducted or reported as performance and not historical cost certification",
    "source": "original source calendar eligibility liquidity revision historical publication and price-basis qualifications remain uncertified",
    "interpretation": "no threshold sweep winning policy horizon parameter edge strategy pass direction closure reserved access or paper/live promotion"}
_canonical, _sha = cc._canonical, cc._sha


class ExitPolicyRefusal(RuntimeError):
    """Stable reason codes deliberately omit saved evidence values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "resources": RESOURCES, **REGISTERED_PATHS, "upstream_sha256": UPSTREAM_SHA256,
        "input_compressed_sha256": INPUT_COMPRESSED_SHA256, "input_stream_sha256": INPUT_STREAM_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise ExitPolicyRefusal("specification_scope_mismatch")


def _label_pair(minutes: Mapping, day: str, event: Mapping) -> dict:
    opening = cc.session_open(day)
    signal, decision = (horizons._instant(event[name]) for name in ("signal_bar_end", "decision_at"))
    entry = opening + ((decision - opening) // cc.FIVE + 1) * cc.FIVE
    cap = entry + TIME_CAP_MINUTES * cc.MINUTE
    if (not ((signal - opening) / cc.MINUTE).is_integer() or signal <= opening
            or decision != signal + 2 * cc.MINUTE or entry - decision != 3 * cc.MINUTE
            or entry + 120 * cc.MINUTE > cc.session_close(day)):
        raise ExitPolicyRefusal("invalid_upstream_signal_decision_or_entry_boundary")
    original = horizons._label(minutes, opening, entry, 120)
    if original["status"] != "known":
        raise ExitPolicyRefusal("upstream_complete_120minute_path_invalid")
    start = int((entry - opening) / cc.MINUTE)
    entry_open = minutes[start][0]
    anchor_close = minutes[start + TIME_CAP_MINUTES - 1][3]
    anchor_change = Fraction(anchor_close) / Fraction(entry_open) - 1
    entry_available = entry + 3 * cc.MINUTE

    def outcome(exit_at):
        price = minutes[int((exit_at - opening) / cc.MINUTE)][0]
        available = exit_at + 3 * cc.MINUTE
        return {"status": "known", "entry_open": entry_open, "exit_open": price,
            "exit_at": exit_at, "price_change": Fraction(price) / Fraction(entry_open) - 1,
            "label_available_at": available, "unknown_reason": None,
            "model_metadata": {"evidence_level": "assumed", "execution_price": "minute_open",
                "entry_price_available_at": entry_available, "outcome_label_available_at": available,
                "actual_fill_certified": False}}

    managed = outcome(cap)
    managed.update(reason="time_cap", trigger_trace=None)
    # T is the complete bar's END, so E+1 reads [E,E+1), not a pre-entry bar.
    # T=X-3 would schedule F=X and is deliberately outside this eligible scan.
    for offset in range(1, TIME_CAP_MINUTES - 3):
        close_at = entry + offset * cc.MINUTE
        close = minutes[start + offset - 1][3]
        change = Fraction(close) / Fraction(entry_open) - 1
        reason = "take_profit" if change >= TAKE_PROFIT else "stop_loss" if change <= STOP_LOSS else None
        if reason is None:
            continue
        available = close_at + 2 * cc.MINUTE
        fill = close_at + 3 * cc.MINUTE
        if not entry_available <= available < fill < cap:
            raise ExitPolicyRefusal("invalid_policy_information_or_execution_boundary")
        managed = outcome(fill)
        managed.update(reason=reason, trigger_trace={"bar_start": close_at - cc.MINUTE,
            "close_at": close_at, "close": close, "price_change": change,
            "available_at": available, "decision_at": available, "scheduled_fill_at": fill})
        break
    return {"common": {"signal_bar_end": signal, "decision_at": decision, "entry_at": entry,
        "entry_available_at": entry_available, "time_cap_at": cap, "time_cap_decided_at": decision,
        "original_holding_minutes": TIME_CAP_MINUTES, "anchor_at": cap, "anchor_label_available_at": cap + 2 * cc.MINUTE,
        "anchor_close": anchor_close, "anchor_price_change": anchor_change},
        "time_only": outcome(cap), "managed": managed}


def build_calibration(ch_report: Mapping, raw_chunks: Mapping | Iterable[Mapping], *,
        checkpoint: Callable[[], None] | None = None) -> dict:
    """Pure supplied-input sizing, retaining every original target and unknown."""
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    matrix, reference, union = ci._matrix(ch_report)
    expected = [(day, symbol) for day in reference for symbol in sorted(union[day])]
    if isinstance(raw_chunks, Mapping):
        if set(raw_chunks) != set(expected):
            raise ExitPolicyRefusal("input_chunk_population_mismatch")
        chunks = iter(raw_chunks[key] for key in expected)
    else:
        chunks = iter(raw_chunks)
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "reference_sessions": reference, "assumptions": ASSUMPTIONS, **FLAGS,
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
                raise ExitPolicyRefusal("missing_input_chunk") from exc
            if not isinstance(chunk, Mapping) or chunk.get("symbol") != symbol or chunk.get("session") != day:
                raise ExitPolicyRefusal("input_chunk_order_or_identity_mismatch")
            if (chunk.get("observation_date") != day or chunk.get("available_at") is not None
                    or chunk.get("source") != "collector klines" or chunk.get("is_final") is not None
                    or chunk.get("evidence_level") != "assumed"):
                raise ExitPolicyRefusal("input_chunk_metadata_mismatch")
            horizons._instant(chunk.get("retrieved_at"))
            raw = chunk.get("rows")
            if not isinstance(raw, list) or len(raw) > RESOURCES["maximum_session_rows"]:
                raise ExitPolicyRefusal("input_session_row_budget_exceeded")
            chunk_count += 1
            row_count += len(raw)
            if row_count > RESOURCES["maximum_rows"]:
                raise ExitPolicyRefusal("input_row_budget_exceeded")
            vintages[symbol] = hashlib.sha256(_canonical(chunk) + b"\n").hexdigest()
            metadata[symbol] = {name: chunk[name] for name in ("observation_date", "available_at", "retrieved_at", "source", "is_final", "evidence_level")}
            sessions[symbol] = horizons._session_minutes(raw, day, symbol, checkpoint)
        for key, grouped in matrix.items():
            for row in grouped[day]:
                if row["data_vintage"] != vintages[row["symbol"]]:
                    raise ExitPolicyRefusal("upstream_chunk_vintage_mismatch")
                output = {name: row[name] for name in ("symbol", "session", "data_vintage")}
                output.update(raw_chunk_metadata=metadata[row["symbol"]], families={})
                for family, saved in row["families"].items():
                    finding = {name: saved[name] for name in ORIGINAL_FIELDS}
                    finding["labels"] = None
                    if saved["label_cohort_state"] == "selected_complete":
                        event = saved["event"]
                        cache_key = (row["symbol"], event["signal_bar_end"], event["decision_at"])
                        if cache_key not in cache:
                            cache[cache_key] = _label_pair(sessions[row["symbol"]], day, event)
                        pair = cache[cache_key]
                        if pair["common"]["anchor_price_change"] != horizons._ratio(saved["responses"]["30"]["event_price_change"]):
                            raise ExitPolicyRefusal("upstream_30minute_close_bridge_mismatch")
                        finding["labels"] = pair
                    output["families"][family] = finding
                result["variants"][key]["days"].append(output)
    try:
        next(chunks)
    except StopIteration:
        pass
    else:
        raise ExitPolicyRefusal("extra_input_chunk")
    result["input_evidence"] = {"upstream_report_sha256": UPSTREAM_SHA256,
        "upstream_report_path": REGISTERED_PATHS["upstream_path"], "archive_path": REGISTERED_PATHS["input_archive_path"],
        "compressed_sha256": INPUT_COMPRESSED_SHA256, "stream_sha256": INPUT_STREAM_SHA256,
        "unique_target_days": chunk_count, "queried_rows": row_count}
    for variant in result["variants"].values():
        for family in FAMILIES:
            counts = cg._counts([row["families"][family] for row in variant["days"]])
            by_arm = {}
            for arm in ARMS:
                checkpoint()
                records = [{"session": row["session"], "label": row["families"][family]["labels"][arm]["price_change"]}
                    for row in variant["days"] if row["families"][family]["labels"] is not None
                    and row["families"][family]["labels"][arm]["status"] == "known"]
                own = cg._own_power(records, reference, checkpoint)
                unknown = counts["selected_complete"] - len(records)
                by_arm[arm] = {"selected_complete": counts["selected_complete"], "known_labels": len(records),
                    "unknown_labels": unknown, "own_label_dispersion": own,
                    "requires_operator_choice": bool(unknown or own["requires_operator_choice"])}
            variant["families"][family] = {**counts, "by_arm": by_arm,
                "requires_operator_choice": any(cell["requires_operator_choice"] for cell in by_arm.values())}
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
    checkpoint = lambda: budget.checkpoint("exit_policy_calibration_pipeline")
    checkpoint()
    spec_path, upstream, archive, output, log = map(Path,
        (specification_path, upstream_path, input_archive_path, output_directory, runs_path))
    if spec_path != SPECIFICATION_PATH:
        raise ExitPolicyRefusal("unregistered_specification_path")
    spec_bytes = spec_path.read_bytes()
    spec, spec_sha = json.loads(spec_bytes), hashlib.sha256(spec_bytes).hexdigest()
    validate_spec(spec)
    paths = (spec_path, upstream, archive, output, log)
    for path, key in zip((upstream, archive, output, log), REGISTERED_PATHS, strict=True):
        if not path.is_absolute() or path != Path(REGISTERED_PATHS[key]):
            raise ExitPolicyRefusal("unregistered_path")
    for index, path in enumerate(paths):
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise ExitPolicyRefusal("symlinked_path")
        if any(path.resolve() == other.resolve() or (path.exists() and other.exists() and path.samefile(other)) for other in paths[:index]):
            raise ExitPolicyRefusal("aliased_paths")
    sources = (spec_path, Path(__file__), Path(ci.__file__), Path(ch.__file__), Path(cg.__file__),
        ROOT / "python/research/research_budget.py", Path(horizons.__file__), Path(cd.__file__),
        Path(cc.__file__), Path(cc.core.__file__), Path(experiment_log.__file__), Path(cd.conclusion_check.__file__),
        ROOT / "python/research/__init__.py", ROOT / "python/pyproject.toml", ROOT / "python/uv.lock")
    executable = Path(sys.executable).resolve(strict=True)
    for path in (upstream, archive, output, log):
        if any(path.resolve() == source.resolve()
                or (path.exists() and source.exists() and path.samefile(source)) for source in (*sources, executable)):
            raise ExitPolicyRefusal("aliased_source_or_runtime_path")
    pins = {str(path): _sha(path) for path in sources}
    runtime = {"python": sys.version, "implementation": sys.implementation.name,
        "executable": str(executable), "executable_sha256": _sha(executable)}
    if pins[str(spec_path)] != spec_sha:
        raise ExitPolicyRefusal("specification_changed")

    def evaluate() -> dict:
        checkpoint()
        if not output.parent.is_dir() or os.path.lexists(output):
            raise ExitPolicyRefusal("unsafe_or_existing_output")
        if shutil.disk_usage(output.parent).free < RESOURCES["minimum_free_bytes"]:
            raise ExitPolicyRefusal("insufficient_free_disk")
        output.mkdir(mode=0o700)
        cc._sync_directory(output.parent)
        if output.stat().st_mode & 0o777 != 0o700:
            raise ExitPolicyRefusal("unsafe_output_permissions")
        with upstream.open("rb") as stream:
            upstream_bytes = stream.read(RESOURCES["maximum_upstream_bytes"] + 1)
        checkpoint()
        if len(upstream_bytes) > RESOURCES["maximum_upstream_bytes"] or hashlib.sha256(upstream_bytes).hexdigest() != UPSTREAM_SHA256:
            raise ExitPolicyRefusal("upstream_size_or_hash_mismatch")
        if archive.stat().st_size > RESOURCES["maximum_compressed_bytes"] or _sha(archive) != INPUT_COMPRESSED_SHA256:
            raise ExitPolicyRefusal("compressed_input_size_or_hash_mismatch")
        upstream_report = json.loads(upstream_bytes)
        del upstream_bytes
        report = build_calibration(upstream_report, horizons._archive_chunks(archive, checkpoint), checkpoint=checkpoint)
        del upstream_report
        if len(report["reference_sessions"]) != EXPECTED_REFERENCE_SESSIONS:
            raise ExitPolicyRefusal("registered_reference_population_changed")
        for key, variant in report["variants"].items():
            for family in FAMILIES:
                if variant["families"][family]["selected_complete"] != EXPECTED_SELECTED_COMPLETE[key][family]:
                    raise ExitPolicyRefusal("registered_selected_population_changed")
        if any(report["input_evidence"][key] != value for key, value in EXPECTED_INPUT_COUNTS.items()):
            raise ExitPolicyRefusal("registered_raw_population_changed")
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
            raise ExitPolicyRefusal("source_specification_or_input_changed")
        if _sha(executable) != runtime["executable_sha256"] or _sha(report_path) != report_sha:
            raise ExitPolicyRefusal("runtime_or_output_changed")
        checkpoint()
        return {"output_path": str(report_path), "output_sha256": report_sha,
            "input_evidence": report["input_evidence"], "requires_operator_choice": report["requires_operator_choice"], **FLAGS}

    return experiment_log.run_discovery_trial(study_id=SPECIFICATION_ID, specification_sha256=spec_sha,
        parameters={"mode": MODE, "parameters": PARAMETERS, "source_sha256": pins, "runtime": runtime,
            "upstream_sha256": UPSTREAM_SHA256, "input_compressed_sha256": INPUT_COMPRESSED_SHA256,
            "input_stream_sha256": INPUT_STREAM_SHA256, "resources": RESOURCES},
        window=dict(WINDOW), evaluate=evaluate, runs_path=log)
