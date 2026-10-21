"""Calibrate one preregistered positive-peer context on saved opening events.

Join pinned CM labels and original CG causal features. Preserve the entire
upstream matrix and calibrate only the new opening subset's own-label power.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys

from research import experiment_log
from research import intraday_exit_policy_comparison as cn


cm, ci, ch, cg, horizons, cd, cc = cn.cm, cn.ci, cn.ch, cn.cg, cn.horizons, cn.cd, cn.cc
ROOT = cn.ROOT
SPECIFICATION_ID = "intraday-peer-context-v1"
MODE = "fixed-opening-breakout-positive-original-peer-context-own-label-calibration"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-peer-context-v1.json"
WINDOW, FAMILIES = dict(cm.WINDOW), cm.FAMILIES
OPENING = "opening_range_breakout"
PARAMETERS = {
    "scope": "opening_range_breakout_only_both_original_membership_variants_recovery_retained_as_upstream_evidence",
    "condition": "original_cg_event_prior30m_return_gt_original_equal_weight_peer_mean_and_peer_mean_strictly_gt_zero",
    "boundary": "exact_zero_peer_mean_reject_no_threshold_sweep",
    "feature": "pinned_cg_original_30minute_first_open_to_last_close_same_original_peers_and_clocks",
    "availability": "feature_end_plus_two_minutes_no_later_than_original_decision",
    "cohort": "original_full_top20_target_matrix_unknowns_and_original120minute_complete_cohort_no_rescue_or_reselection",
    "label": "pinned_cm_time_only_30minute_cap_open_price_change_original_three_minute_entry_wait",
    "power": "new_selected_complete_opening_subset_own_label_dispersion_all_reference_dates_including_zero_event_dates",
    "resampling": "maximum_naive_session_cluster_circular_five_session_bootstrap_2000_draws_seed_zero",
    "cost_floor_bps": "33",
    "unknowns": "unknown_new_features_or_labels_or_failed_unknown_own_power_require_operator_choice_no_prior_waiver",
    "output_scope": "selection_frequency_week_month_and_own_label_dispersion_only_no_future_means_cost_returns_contrast_or_winner"}
UPSTREAM_SHA256 = cn.UPSTREAM_SHA256
FEATURE_SHA256 = "b507b29998aa797d79f12e6da64ac6e39fd233a542fbedfb1f0ed98cee57d3f0"
REGISTERED_PATHS = {
    "upstream_path": cn.REGISTERED_PATHS["upstream_path"],
    "feature_path": "/home/minjun4897/research-evidence/intraday-cg-entry-strength-20261021-v1/report.json",
    "output_directory": "/home/minjun4897/research-evidence/intraday-co-peer-context-20261022-v1",
    "canonical_runs_path": cn.REGISTERED_PATHS["canonical_runs_path"]}
RESOURCES = dict(cn.RESOURCES)
EXPECTED_REFERENCE_SESSIONS = cm.EXPECTED_REFERENCE_SESSIONS
EXPECTED_SELECTED_COMPLETE = cm.EXPECTED_SELECTED_COMPLETE
EXPECTED_INPUT_COUNTS = cm.EXPECTED_INPUT_COUNTS
FLAGS = {**cm.FLAGS, "peer_context_calibrated": True}
ASSUMPTIONS = {
    "hypothesis": "fixed before new subgroup outcomes: original CG opening pass plus strictly positive original peer mean; exact zero rejects",
    "measurement": "new opening selection frequency and own-label dispersion only; no future-return mean assumed-cost return contrast winner or final comparison family",
    "cohort": "retain every CM target unknown label peer and original120minute future-completeness qualification; passing context cannot rescue original unknown pairs",
    "timing": "reuse CG original signal-end30minute feature end+2minute assumed availability no later than original decision; original three-minute entry and CM30minute cap-open label",
    "peers": "all original causal selected peers equally weighted with no outcome-complete replacement; no raw replay or new input window",
    "power": "new selected-complete opening time-only labels use their own dispersion all reference dates circular five-session2000draws seed0 maximum error against assumed33bp; old8CM cells retained separately",
    "unknowns": "new feature or label unknown or unknown/failed own sizing requires operator choice; no legacy underpower exception transfers",
    "recovery": "post-drop recovery retained unchanged as upstream evidence and excluded from the new hypothesis and sizing family",
    "source": "source calendar eligibility liquidity revisions historical availability price basis costs and actual fills remain uncertified",
    "interpretation": "no performance qualification strategy pass direction closure reserved access risk change or paper/live promotion"}
_canonical, _sha = cm._canonical, cm._sha


class PeerContextRefusal(RuntimeError):
    """Stable reason codes omit private supplied evidence values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "resources": RESOURCES, **REGISTERED_PATHS, "upstream_sha256": UPSTREAM_SHA256,
        "feature_sha256": FEATURE_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise PeerContextRefusal("specification_scope_mismatch")


def _ratio(value: object) -> Fraction:
    if isinstance(value, Fraction):
        return value
    if (not isinstance(value, Mapping) or set(value) != {"numerator", "denominator"}
            or not isinstance(value["numerator"], str) or not re.fullmatch(r"-?[0-9]+", value["numerator"])
            or not isinstance(value["denominator"], str) or not re.fullmatch(r"[0-9]+", value["denominator"])
            or int(value["denominator"]) <= 0):
        raise PeerContextRefusal("feature_ratio_not_exact")
    return Fraction(int(value["numerator"]), int(value["denominator"]))


def _member(item: Mapping, symbol: str) -> Fraction | None:
    if (set(item) != {"symbol", "status", "missing_minute_buckets", "unknown_reason", "first_open", "last_close", "price_change"}
            or item["symbol"] != symbol or type(item["missing_minute_buckets"]) is not int
            or not 0 <= item["missing_minute_buckets"] <= 30):
        raise PeerContextRefusal("feature_member_contract_mismatch")
    missing = item["missing_minute_buckets"]
    if (item["status"] != ("unknown" if missing else "known")
            or item["unknown_reason"] != ("incomplete_30minute_feature_path" if missing else None)):
        raise PeerContextRefusal("feature_member_unknown_mismatch")
    if missing:
        if any(item[name] is not None for name in ("first_open", "last_close", "price_change")):
            raise PeerContextRefusal("unknown_feature_has_price")
        return None
    value = cn._positive_price(item["last_close"]) / cn._positive_price(item["first_open"]) - 1
    if _ratio(item["price_change"]) != value:
        raise PeerContextRefusal("feature_price_ratio_mismatch")
    return value


def _feature(saved: Mapping, day: str, symbol: str) -> tuple[str, list[str]]:
    """Authenticate the original CG derivation before adding any condition."""
    feature = saved["feature"]
    if saved["status"] != "signal":
        if feature is not None:
            raise PeerContextRefusal("nonsignal_has_feature")
        return "non_signal", []
    if not isinstance(feature, Mapping) or set(feature) != {"start", "end", "available_at", "decision_at",
            "event", "peers", "peer_mean_price_change", "excess_price_change"}:
        raise PeerContextRefusal("feature_contract_mismatch")
    start, end, available, decision = (horizons._instant(feature[name])
        for name in ("start", "end", "available_at", "decision_at"))
    opening = cc.session_open(day)
    if (end != horizons._instant(saved["event"]["signal_bar_end"])
            or decision != horizons._instant(saved["event"]["decision_at"])
            or start != end - 30 * cc.MINUTE or available != end + 2 * cc.MINUTE
            or not ((start - opening) / cc.MINUTE).is_integer()
            or not opening < end <= cc.session_close(day) or not end <= decision <= cc.session_close(day)):
        raise PeerContextRefusal("feature_clock_mismatch")
    peers = saved["selected_controls"]
    if (not isinstance(feature["peers"], list)
            or [item["symbol"] for item in feature["peers"]] != [item["symbol"] for item in peers]):
        raise PeerContextRefusal("feature_original_peers_mismatch")
    outside = max(0, int((opening - start) / cc.MINUTE))
    if any(item["missing_minute_buckets"] < outside for item in (feature["event"], *feature["peers"])):
        raise PeerContextRefusal("preopening_feature_missingness_mismatch")
    event = _member(feature["event"], symbol)
    values = [_member(item, peer["symbol"]) for item, peer in zip(feature["peers"], peers, strict=True)]
    mean = sum(values, Fraction()) / len(values) if values and all(value is not None for value in values) else None
    excess = event - mean if event is not None and mean is not None else None
    for name, expected in (("peer_mean_price_change", mean), ("excess_price_change", excess)):
        actual = feature[name]
        if (actual is None) != (expected is None) or (actual is not None and _ratio(actual) != expected):
            raise PeerContextRefusal("feature_peer_mean_or_excess_mismatch")
    reasons = []
    if available > decision: reasons.append("feature_unavailable_at_original_decision")
    if event is None: reasons.append("event_feature_unknown")
    if not values: reasons.append("no_original_selected_controls")
    if any(value is None for value in values): reasons.append("original_selected_control_feature_unknown")
    state = "unknown" if reasons else "pass" if excess > 0 else "reject"
    if saved["selection_state"] != state or saved["selection_unknown_reasons"] != reasons:
        raise PeerContextRefusal("original_feature_selection_derivation_mismatch")
    return ("pass" if state == "pass" and mean > 0 else "reject" if state == "pass" else state), reasons


def _week(day: str) -> str:
    iso = date.fromisoformat(day).isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def _counts(rows: list[dict]) -> dict:
    return {**cg._counts(rows), "feature_unknown": sum(row["selection_state"] == "unknown" for row in rows)}


def _validate_inputs(cm_report: Mapping, cg_report: Mapping, checkpoint: Callable[[], None]) -> list[str]:
    reference = cn._validate_upstream(cm_report, checkpoint)
    expected = {"study_id": cg.SPECIFICATION_ID, "mode": cg.MODE, "window": WINDOW,
        "parameters": cg.PARAMETERS, "assumptions": cg.ASSUMPTIONS, **cg.FLAGS, "reference_sessions": reference}
    if (any(_canonical(cg_report.get(name)) != _canonical(value) for name, value in expected.items())
            or type(cg_report.get("requires_operator_choice")) is not bool
            or set(cg_report.get("variants", {})) != {"D+1", "D+2"}):
        raise PeerContextRefusal("wrong_feature_study_or_scope")
    projection = {"study_id": cc.SPECIFICATION_ID, "parameters": cc.PARAMETERS, **cc.FLAGS,
        "variants": cg_report["variants"]}
    _, feature_reference, _ = cd._matrix(projection)
    if feature_reference != reference:
        raise PeerContextRefusal("cross_input_reference_mismatch")
    feature_sources, feature_graphs, member_values = {}, {}, {}
    for key, variant in cm_report["variants"].items():
        calibrated = cg_report["variants"][key]
        if set(calibrated) != {"days", "families"} or set(calibrated["families"]) != set(FAMILIES):
            raise PeerContextRefusal("feature_family_population_mismatch")
        index = {(row["session"], row["symbol"]): row for row in calibrated["days"]}
        if set(index) != {(row["session"], row["symbol"]) for row in variant["days"]}:
            raise PeerContextRefusal("cross_input_target_matrix_mismatch")
        for row in variant["days"]:
            checkpoint()
            day, symbol = row["session"], row["symbol"]
            actual = index[day, symbol]
            if (set(actual) != {"symbol", "session", "data_vintage", "observation_date", "membership_observation_date",
                    "membership_available_at", "membership_source", "families"}
                    or actual["observation_date"] != day or actual["data_vintage"] != row["data_vintage"]
                    or set(actual["families"]) != set(FAMILIES)):
                raise PeerContextRefusal("cross_input_target_metadata_mismatch")
            source = _canonical([actual["data_vintage"], actual["observation_date"]])
            if (day, symbol) in feature_sources and feature_sources[day, symbol] != source:
                raise PeerContextRefusal("feature_cross_variant_source_mismatch")
            feature_sources[day, symbol] = source
            for family, saved in row["families"].items():
                feature = actual["families"][family]
                if (set(feature) != {*cm.ORIGINAL_FIELDS, "feature"}
                        or any(_canonical(feature.get(name)) != _canonical(saved[name]) for name in cm.ORIGINAL_FIELDS)):
                    raise PeerContextRefusal("cross_input_original_finding_mismatch")
                _feature(feature, day, symbol)
                if feature["status"] == "signal":
                    graph_feature = feature["feature"]
                    for item in (graph_feature["event"], *graph_feature["peers"]):
                        member_key = (day, item["symbol"], _canonical([graph_feature["start"], graph_feature["end"]]))
                        value = _canonical(item)
                        if member_key in member_values and member_values[member_key] != value:
                            raise PeerContextRefusal("feature_cross_event_member_mismatch")
                        member_values[member_key] = value
                    graph_key = (day, symbol, _canonical(feature["event"]), _canonical(feature["selected_controls"]))
                    graph = _canonical(feature["feature"])
                    if graph_key in feature_graphs and feature_graphs[graph_key] != graph:
                        raise PeerContextRefusal("feature_cross_event_graph_mismatch")
                    feature_graphs[graph_key] = graph
        for family in FAMILIES:
            rows = [{"session": row["session"], **row["families"][family]} for row in calibrated["days"]]
            summary = calibrated["families"][family]
            expected_counts = cg._counts(rows)
            if (set(summary) != {*expected_counts, "by_month", "by_week", "by_horizon", "requires_operator_choice"}
                    or any(_canonical(summary[name]) != _canonical(value) for name, value in expected_counts.items())
                    or set(summary["by_horizon"]) != {str(h) for h in cg.HORIZONS}
                    or type(summary["requires_operator_choice"]) is not bool
                    or summary["requires_operator_choice"] != any(cell["requires_operator_choice"]
                        for cell in summary["by_horizon"].values())):
                raise PeerContextRefusal("feature_summary_mismatch")
            for field, period in (("by_month", lambda day: day[:7]), ("by_week", _week)):
                expected_periods = {value: cg._counts([row for row in rows if period(row["session"]) == value])
                    for value in sorted({period(day) for day in reference})}
                if _canonical(summary[field]) != _canonical(expected_periods):
                    raise PeerContextRefusal("feature_calendar_counts_mismatch")
    if cg_report["requires_operator_choice"] != any(summary["requires_operator_choice"]
            for variant in cg_report["variants"].values() for summary in variant["families"].values()):
        raise PeerContextRefusal("feature_choice_summary_mismatch")
    if cg_report["requires_operator_choice"] != cm_report["upstream_calibration_requires_operator_choice"]:
        raise PeerContextRefusal("cross_input_legacy_choice_mismatch")
    evidence = cg_report["input_evidence"]
    expected_evidence = {"upstream_report_sha256": cg.UPSTREAM_SHA256,
        "archive_path": cg.REGISTERED_PATHS["input_archive_path"], "compressed_sha256": cg.INPUT_COMPRESSED_SHA256,
        "stream_sha256": cg.INPUT_STREAM_SHA256,
        **{name: cm_report["input_evidence"][name] for name in EXPECTED_INPUT_COUNTS}}
    if _canonical(evidence) != _canonical(expected_evidence):
        raise PeerContextRefusal("cross_input_lineage_mismatch")
    return reference


def build_calibration(cm_report: Mapping, cg_report: Mapping, checkpoint: Callable[[], None] | None = None) -> dict:
    """Pure supplied-input calibration, with no actual-input reads or means."""
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    try:
        reference = _validate_inputs(cm_report, cg_report, checkpoint)
    except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError, cn.ComparisonRefusal,
            cd.ComparisonRefusal, horizons.HorizonRefusal) as exc:
        raise PeerContextRefusal("invalid_upstream_contract") from exc
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "reference_sessions": reference, "assumptions": ASSUMPTIONS, **FLAGS,
        "upstream_calibration_requires_operator_choice": cm_report["requires_operator_choice"],
        "upstream_legacy_calibration_requires_operator_choice": cm_report["upstream_calibration_requires_operator_choice"],
        "input_evidence": {"upstream_report_sha256": UPSTREAM_SHA256, "upstream_report_path": REGISTERED_PATHS["upstream_path"],
            "feature_report_sha256": FEATURE_SHA256, "feature_report_path": REGISTERED_PATHS["feature_path"],
            "calibration_input_evidence": cm_report["input_evidence"], "feature_input_evidence": cg_report["input_evidence"]},
        "variants": {}}
    for key, variant in cm_report["variants"].items():
        index = {(row["session"], row["symbol"]): row for row in cg_report["variants"][key]["days"]}
        days, records, projected = [], [], []
        known, unknown = 0, 0
        for row in variant["days"]:
            checkpoint()
            saved = row["families"][OPENING]
            feature = index[row["session"], row["symbol"]]["families"][OPENING]
            state, reasons = _feature(feature, row["session"], row["symbol"])
            cohort = ("non_signal" if saved["status"] != "signal" else "outside_original_complete_cohort"
                if saved["original_pair_status"] != "complete" else "selected_complete" if state == "pass" else "not_selected")
            context = {"feature": feature["feature"], "selection_state": state,
                "selection_unknown_reasons": reasons, "label_cohort_state": cohort}
            days.append({**row, "families": {**row["families"], OPENING: {**saved, "peer_context": context}}})
            projected.append({"session": row["session"], **saved, **context})
            if cohort == "selected_complete":
                outcome = saved["labels"]["time_only"]
                if outcome["status"] == "known":
                    known += 1
                    records.append({"session": row["session"], "label": horizons._ratio(outcome["price_change"])})
                else:
                    unknown += 1
        own = cg._own_power(records, reference, checkpoint)
        counts = _counts(projected)
        summary = {**counts, "known_labels": known, "unknown_labels": unknown, "own_label_dispersion": own,
            "requires_operator_choice": bool(counts["feature_unknown"] or unknown or own["requires_operator_choice"])}
        for field, period in (("by_month", lambda day: day[:7]), ("by_week", _week)):
            summary[field] = {value: _counts([row for row in projected if period(row["session"]) == value])
                for value in sorted({period(day) for day in reference})}
        result["variants"][key] = {"days": days, "families": variant["families"], "peer_context": summary}
    result["requires_operator_choice"] = any(variant["peer_context"]["requires_operator_choice"] for variant in result["variants"].values())
    checkpoint()
    return result


def run_calibration(*, specification_path: str | Path, upstream_path: str | Path, feature_path: str | Path,
        output_directory: str | Path, runs_path: str | Path, budget=None,
        before_completion: Callable[[], None] | None = None) -> dict:
    """Durably start before either pinned report read; caller finishes budget."""
    if budget is None:
        from research.research_budget import ResearchBudget
        budget = ResearchBudget()
    checkpoint = lambda: budget.checkpoint("peer_context_pipeline")
    checkpoint()
    spec_path, upstream, feature, output, log = map(Path,
        (specification_path, upstream_path, feature_path, output_directory, runs_path))
    if spec_path != SPECIFICATION_PATH:
        raise PeerContextRefusal("unregistered_specification_path")
    spec_bytes = spec_path.read_bytes()
    spec, spec_sha = json.loads(spec_bytes), hashlib.sha256(spec_bytes).hexdigest()
    validate_spec(spec)
    paths = (spec_path, upstream, feature, output, log)
    for path, key in zip((upstream, feature, output, log), REGISTERED_PATHS, strict=True):
        if not path.is_absolute() or path != Path(REGISTERED_PATHS[key]):
            raise PeerContextRefusal("unregistered_path")
    for index, path in enumerate(paths):
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise PeerContextRefusal("symlinked_path")
        if any(path.resolve() == other.resolve() or (path.exists() and other.exists() and path.samefile(other)) for other in paths[:index]):
            raise PeerContextRefusal("aliased_paths")
    sources = (spec_path, Path(__file__), Path(cn.__file__), Path(cm.__file__), Path(ci.__file__), Path(ch.__file__), Path(cg.__file__),
        ROOT / "python/research/research_budget.py", Path(horizons.__file__), Path(cd.__file__), Path(cc.__file__),
        Path(cc.core.__file__), Path(experiment_log.__file__), Path(cd.conclusion_check.__file__),
        ROOT / "python/research/__init__.py", ROOT / "python/pyproject.toml", ROOT / "python/uv.lock")
    executable = Path(sys.executable).resolve(strict=True)
    for path in (upstream, feature, output, log):
        if any(path.resolve() == source.resolve()
                or (path.exists() and source.exists() and path.samefile(source)) for source in (*sources, executable)):
            raise PeerContextRefusal("aliased_source_or_runtime_path")
    pins = {str(path): _sha(path) for path in sources}
    runtime = {"python": sys.version, "implementation": sys.implementation.name,
        "executable": str(executable), "executable_sha256": _sha(executable)}
    if pins[str(spec_path)] != spec_sha:
        raise PeerContextRefusal("specification_changed")

    def evaluate() -> dict:
        checkpoint()
        if not output.parent.is_dir() or os.path.lexists(output):
            raise PeerContextRefusal("unsafe_or_existing_output")
        if shutil.disk_usage(output.parent).free < RESOURCES["minimum_free_bytes"]:
            raise PeerContextRefusal("insufficient_free_disk")
        output.mkdir(mode=0o700)
        cc._sync_directory(output.parent)
        if output.stat().st_mode & 0o777 != 0o700:
            raise PeerContextRefusal("unsafe_output_permissions")
        reports = []
        for path, digest in ((upstream, UPSTREAM_SHA256), (feature, FEATURE_SHA256)):
            with path.open("rb") as stream:
                data = stream.read(RESOURCES["maximum_upstream_bytes"] + 1)
            checkpoint()
            if len(data) > RESOURCES["maximum_upstream_bytes"] or hashlib.sha256(data).hexdigest() != digest:
                raise PeerContextRefusal("upstream_size_or_hash_mismatch")
            reports.append(json.loads(data))
            del data
        report = build_calibration(*reports, checkpoint=checkpoint)
        del reports
        if len(report["reference_sessions"]) != EXPECTED_REFERENCE_SESSIONS:
            raise PeerContextRefusal("registered_reference_population_changed")
        for key, variant in report["variants"].items():
            for family in FAMILIES:
                if variant["families"][family]["selected_complete"] != EXPECTED_SELECTED_COMPLETE[key][family]:
                    raise PeerContextRefusal("registered_selected_population_changed")
        if any(report["input_evidence"]["calibration_input_evidence"][name] != value for name, value in EXPECTED_INPUT_COUNTS.items()):
            raise PeerContextRefusal("registered_raw_population_changed")
        report.update(specification_sha256=spec_sha, source_sha256=pins, runtime=runtime)
        report_path = output / "report.json"
        with cc._private_file(report_path) as stream:
            encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                allow_nan=False, default=lambda value: json.loads(_canonical(value)))
            for index, chunk in enumerate(encoder.iterencode(report)):
                if index % 1024 == 0: checkpoint()
                stream.write(chunk.encode())
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        cc._sync_directory(output)
        report_sha = _sha(report_path)
        if before_completion is not None: before_completion()
        checkpoint()
        if (any(_sha(path) != pins[str(path)] for path in sources) or _sha(upstream) != UPSTREAM_SHA256
                or _sha(feature) != FEATURE_SHA256):
            raise PeerContextRefusal("source_specification_or_input_changed")
        if _sha(executable) != runtime["executable_sha256"] or _sha(report_path) != report_sha:
            raise PeerContextRefusal("runtime_or_output_changed")
        checkpoint()
        return {"output_path": str(report_path), "output_sha256": report_sha, "input_evidence": report["input_evidence"],
            "requires_operator_choice": report["requires_operator_choice"], **FLAGS}

    return experiment_log.run_discovery_trial(study_id=SPECIFICATION_ID, specification_sha256=spec_sha,
        parameters={"mode": MODE, "parameters": PARAMETERS, "source_sha256": pins, "runtime": runtime,
            "upstream_sha256": UPSTREAM_SHA256, "feature_sha256": FEATURE_SHA256, "resources": RESOURCES},
        window=dict(WINDOW), evaluate=evaluate, runs_path=log)
