"""Fixed-exit comparison using only the verified earlier-entry calibration.

Both own-label sizing prerequisites remain prior evidence. The new summaries
describe paired hypothetical cash-unit price labels, never portfolio returns.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from statistics import NormalDist
import sys

from research import experiment_log
from research import intraday_entry_delay as ck


ci, ch, cg, horizons, cd, cc = ck.ci, ck.ch, ck.cg, ck.horizons, ck.cd, ck.cc
ROOT = ck.ROOT
SPECIFICATION_ID = "intraday-entry-delay-comparison-v1"
MODE = "fixed-exit-clock-one-vs-three-minute-entry-comparison"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-entry-delay-comparison-v1.json"
WINDOW = dict(ck.WINDOW)
FAMILIES = ck.FAMILIES
QUALIFIED_HORIZONS = ck.QUALIFIED_HORIZONS
EXCLUDED_HORIZONS = ck.EXCLUDED_HORIZONS
PARAMETERS = {**ck.PARAMETERS,
    "power": "retain_pinned_ck_current_and_earlier_own_dispersion_require_both_prerequisites_without_remeasurement",
    "upstream_reuse": "pinned_verified_ck_report_only_no_raw_reconstruction_or_reselection",
    "unit": "same_hypothetical_cash_budget_per_original_event_each_arm_uses_its_own_entry_open",
    "cost": "subtract_exact_33_over_10000_from_each_event_both_arms_same_assumption",
    "comparison": "paired_earlier_minus_current_at_unchanged_original_exit_costs_cancel",
    "uncertainty": "paired_difference_maximum_naive_session_cluster_circular_five_session_bootstrap_2000_draws_seed_zero_descriptive_normal_95_interval",
    "output_scope": "all_sixteen_fixed_cells_conditional_price_label_comparison_no_winner_or_executable_portfolio"}
UPSTREAM_SHA256 = "3edf951d517dab8f6849f0c2b8d7ed206a89e99ebf138a33052f0a0450611cb8"
REGISTERED_PATHS = {
    "upstream_path": "/home/minjun4897/research-evidence/intraday-ck-entry-delay-20261021-v1/report.json",
    "output_directory": "/home/minjun4897/research-evidence/intraday-cl-entry-delay-comparison-20261021-v1",
    "canonical_runs_path": ck.REGISTERED_PATHS["canonical_runs_path"]}
RESOURCES = dict(ch.RESOURCES)
EXPECTED_REFERENCE_SESSIONS = ck.EXPECTED_REFERENCE_SESSIONS
EXPECTED_SELECTED_COMPLETE = ck.EXPECTED_SELECTED_COMPLETE
EXPECTED_INPUT_COUNTS = ck.EXPECTED_INPUT_COUNTS
FLAGS = {**ck.FLAGS, "calibration_only": False, "comparison_performed": True,
    "earlier_entry_policy_compared": True}
ASSUMPTIONS = {**ck.ASSUMPTIONS,
    "measurement": "all sixteen preregistered fixed-exit current and earlier price-label cells; descriptive paired comparison only",
    "unit": "same hypothetical cash unit per original event; terminal close divided by each arm's own entry open minus one; no equal-share or portfolio accounting",
    "power": "retain exact pinned CK own-label sizing and current prerequisite status for both arms; both must pass with zero earlier unknowns before any comparison; no own-power remeasurement",
    "exceptions": "no underpower exception; the upstream legacy choice flag for excluded recovery60/120 is distinct from CK's passing sixteen-cell prerequisites",
    "cost": "subtract exact33/10000 from each event in each arm; same assumed33bp costs cancel in earlier-minus-current; historical costs and fills remain uncertified",
    "dependence": "paired difference uncertainty includes all reference sessions including zero-event dates; maximum naive/session-cluster/circular five-session bootstrap error,2000draws seed0",
    "interval": "mean paired difference plus/minus normal0.975 quantile times design error is descriptive only; no p-value, inferential decision, family winner, pass or promotion",
    "upstream": "only the pinned separately verified CK report is read; validate its saved contract without recertifying original raw source truth"}
COST = Fraction(33, 10000)
_canonical, _sha = ck._canonical, ck._sha


class ComparisonRefusal(RuntimeError):
    """Stable reason codes omit private saved evidence values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "resources": RESOURCES, **REGISTERED_PATHS, "upstream_sha256": UPSTREAM_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise ComparisonRefusal("specification_scope_mismatch")


def _positive_price(value: object) -> Fraction:
    if not isinstance(value, (str, Decimal)):
        raise ComparisonRefusal("upstream_price_not_exact")
    price = Decimal(value)
    if not price.is_finite() or price <= 0:
        raise ComparisonRefusal("invalid_upstream_price")
    return Fraction(price)


def _validate_pair(pair: Mapping, event: Mapping, day: str, horizon: int) -> None:
    opening = cc.session_open(day)
    signal, decision = (horizons._instant(event[name]) for name in ("signal_bar_end", "decision_at"))
    entry = opening + ((decision - opening) // cc.FIVE + 1) * cc.FIVE
    earlier, end = decision + cc.MINUTE, entry + horizon * cc.MINUTE
    if (not ((signal - opening) / cc.MINUTE).is_integer() or signal <= opening
            or decision != signal + 2 * cc.MINUTE or entry - decision != 3 * cc.MINUTE
            or entry - earlier != 2 * cc.MINUTE or end > cc.session_close(day)):
        raise ComparisonRefusal("invalid_upstream_signal_decision_or_entry_boundary")
    clocks = {"signal_bar_end": signal, "decision_at": decision, "current_entry_at": entry,
        "earlier_entry_at": earlier, "exit_at": end, "label_available_at": end + 2 * cc.MINUTE}
    if (set(pair) != {*clocks, "original_holding_minutes", "earlier_holding_minutes", "current", "earlier"}
            or any(horizons._instant(pair[name]) != value for name, value in clocks.items())
            or type(pair["original_holding_minutes"]) is not int or pair["original_holding_minutes"] != horizon
            or type(pair["earlier_holding_minutes"]) is not int or pair["earlier_holding_minutes"] != horizon + 2):
        raise ComparisonRefusal("upstream_fixed_exit_clock_mismatch")
    terminals = []
    for arm in ("current", "earlier"):
        label = pair[arm]
        if (set(label) != {"status", "entry_open", "terminal_close", "price_change", "missing_minute_buckets", "unknown_reason"}
                or label["status"] != "known" or type(label["missing_minute_buckets"]) is not int
                or label["missing_minute_buckets"] != 0 or label["unknown_reason"] is not None):
            raise ComparisonRefusal("upstream_unknown_or_invalid_label")
        start, terminal = (_positive_price(label[name]) for name in ("entry_open", "terminal_close"))
        if horizons._ratio(label["price_change"]) != terminal / start - 1:
            raise ComparisonRefusal("upstream_exact_label_ratio_mismatch")
        terminals.append(terminal)
    if terminals[0] != terminals[1]:
        raise ComparisonRefusal("upstream_shared_terminal_mismatch")


def _validate_upstream(report: Mapping, checkpoint: Callable[[], None]) -> list[str]:
    expected = {"study_id": ck.SPECIFICATION_ID, "mode": ck.MODE, "parameters": ck.PARAMETERS,
        "window": WINDOW, **ck.FLAGS, "assumptions": ck.ASSUMPTIONS, "requires_operator_choice": False,
        "excluded_horizons_minutes": {family: list(values) for family, values in EXCLUDED_HORIZONS.items()}}
    if (any(_canonical(report.get(name)) != _canonical(value) for name, value in expected.items())
            or type(report.get("upstream_calibration_requires_operator_choice")) is not bool
            or set(report.get("variants", {})) != {"D+1", "D+2"}):
        raise ComparisonRefusal("wrong_upstream_study_or_prerequisites")
    reference = report.get("reference_sessions")
    if (not isinstance(reference, list) or not reference or reference != sorted(set(reference))
            or any(not isinstance(day, str) or date.fromisoformat(day).isoformat() != day
                or not WINDOW["start"] <= day <= WINDOW["end"] for day in reference)):
        raise ComparisonRefusal("upstream_reference_population_mismatch")
    union = {day: set() for day in reference}
    for variant in report["variants"].values():
        grouped = {}
        for row in variant["days"]:
            day, symbol = row["session"], row["symbol"]
            if (day not in union or not re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol)
                    or not re.fullmatch(r"[0-9a-f]{64}", row["data_vintage"])):
                raise ComparisonRefusal("target_outside_registered_population")
            grouped.setdefault(day, []).append(row)
        if list(grouped) != reference:
            raise ComparisonRefusal("target_matrix_lost_sessions")
        if set(variant["families"]) != set(FAMILIES):
            raise ComparisonRefusal("upstream_family_population_mismatch")
        for day, rows in grouped.items():
            checkpoint()
            names = {row["symbol"] for row in rows}
            if len(rows) != 20 or len(names) != 20:
                raise ComparisonRefusal("target_population_changed")
            union[day].update(names)
            for row in rows:
                metadata = row["raw_chunk_metadata"]
                if (set(metadata) != {"observation_date", "available_at", "retrieved_at", "source", "is_final", "evidence_level"}
                        or metadata["observation_date"] != day or metadata["available_at"] is not None
                        or metadata["source"] != "collector klines" or metadata["is_final"] is not None
                        or metadata["evidence_level"] != "assumed"):
                    raise ComparisonRefusal("upstream_raw_metadata_mismatch")
                horizons._instant(metadata["retrieved_at"])
                if set(row["families"]) != set(FAMILIES):
                    raise ComparisonRefusal("upstream_family_population_mismatch")
                for family, finding in row["families"].items():
                    if set(finding) != {*ck.ORIGINAL_FIELDS, "labels"}:
                        raise ComparisonRefusal("upstream_projection_incomplete")
                    status, state = finding["status"], finding["selection_state"]
                    reasons = finding["selection_unknown_reasons"]
                    if (status not in cd.STATES or state not in cg.SELECTION_STATES
                            or not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons)
                            or (state == "unknown") != bool(reasons) or (status != "signal") != (state == "non_signal")):
                        raise ComparisonRefusal("invalid_upstream_selection_state")
                    cohort = ("non_signal" if status != "signal" else "outside_original_complete_cohort"
                        if finding["original_pair_status"] != "complete" else "selected_complete" if state == "pass" else "not_selected")
                    if finding["label_cohort_state"] != cohort:
                        raise ComparisonRefusal("upstream_selection_cohort_mismatch")
                    peers = finding["selected_controls"]
                    peer_names = [item["symbol"] for item in peers]
                    if (peer_names != sorted(set(peer_names)) or row["symbol"] in peer_names
                            or not set(peer_names) <= names or any(set(item) != {"symbol"} for item in peers)):
                        raise ComparisonRefusal("invalid_upstream_peer_population")
                    original_reasons = finding["original_pair_unknown_reasons"]
                    unknown_peers = finding["original_unknown_control_symbols"]
                    if (not isinstance(original_reasons, list) or any(not isinstance(item, str) for item in original_reasons)
                            or not isinstance(unknown_peers, list) or unknown_peers != sorted(set(unknown_peers))
                            or not set(unknown_peers) <= set(peer_names)):
                        raise ComparisonRefusal("invalid_upstream_original_unknowns")
                    if status != "signal":
                        if (finding["event"] is not None or finding["original_pair_status"] is not None
                                or finding["original_event_label_state"] is not None or peers
                                or finding["excluded_controls"] or original_reasons or unknown_peers):
                            raise ComparisonRefusal("upstream_nonsignal_has_event")
                    elif (finding["original_pair_status"] not in {"complete", "unknown"}
                            or (finding["original_pair_status"] == "complete") != (bool(peers) and not finding["original_pair_unknown_reasons"])
                            or set(finding["event"]) != {"signal_bar_end", "decision_at"}):
                        raise ComparisonRefusal("invalid_upstream_event_or_cohort")
                    else:
                        original_label = finding["original_event_label_state"]
                        if (not isinstance(original_label, Mapping)
                                or set(original_label) != {"status", "missing_minute_buckets", "unknown_reason"}
                                or original_label["status"] not in {"known", "unknown"}
                                or type(original_label["missing_minute_buckets"]) is not int
                                or not 0 <= original_label["missing_minute_buckets"] <= 120
                                or (original_label["status"] == "known") != (original_label["missing_minute_buckets"] == 0)
                                or original_label["unknown_reason"] != (None if original_label["status"] == "known" else "incomplete_120minute_path")):
                            raise ComparisonRefusal("invalid_upstream_original_label_state")
                        expected_reasons = (["no_selected_controls"] if not peers else [])
                        if original_label["status"] == "unknown":
                            expected_reasons.append("event_label_unknown")
                        if unknown_peers:
                            expected_reasons.append("selected_control_label_unknown")
                        if original_reasons != sorted(expected_reasons):
                            raise ComparisonRefusal("upstream_original_unknown_state_mismatch")
                    labels = finding["labels"]
                    if cohort == "selected_complete":
                        if not isinstance(labels, Mapping) or set(labels) != {str(h) for h in QUALIFIED_HORIZONS[family]}:
                            raise ComparisonRefusal("upstream_label_population_mismatch")
                        for h in QUALIFIED_HORIZONS[family]:
                            _validate_pair(labels[str(h)], finding["event"], day, h)
                    elif labels is not None:
                        raise ComparisonRefusal("upstream_unselected_has_labels")
        for family in FAMILIES:
            counts = cg._counts([row["families"][family] for row in variant["days"]])
            summary = variant["families"][family]
            if (any(_canonical(summary.get(name)) != _canonical(value) for name, value in counts.items())
                    or summary.get("requires_operator_choice") is not False
                    or set(summary["by_horizon"]) != {str(h) for h in QUALIFIED_HORIZONS[family]}):
                raise ComparisonRefusal("upstream_counts_or_family_prerequisite_mismatch")
            for cell in summary["by_horizon"].values():
                selected_sessions = len({row["session"] for row in variant["days"]
                    if row["families"][family]["label_cohort_state"] == "selected_complete"})
                if (any(type(cell.get(name)) is not int or cell[name] != expected for name, expected in (
                        ("selected_complete", counts["selected_complete"]), ("earlier_known_labels", counts["selected_complete"]), ("earlier_unknown_labels", 0)))
                        or cell.get("current_power_prerequisite_met") is not True
                        or cell.get("requires_operator_choice") is not False
                        or any(not ck._current_power_valid(cell.get(arm), counts["selected_complete"], reference)
                            or cell[arm]["known_label_sessions"] != selected_sessions
                            for arm in ("current_own_power", "earlier_own_power"))):
                    raise ComparisonRefusal("qualified_cell_power_prerequisite_failed")
    evidence = report["input_evidence"]
    expected_evidence = {"upstream_report_sha256": ck.UPSTREAM_SHA256,
        "upstream_report_path": ck.REGISTERED_PATHS["upstream_path"], "archive_path": ck.REGISTERED_PATHS["input_archive_path"],
        "compressed_sha256": ck.INPUT_COMPRESSED_SHA256, "stream_sha256": ck.INPUT_STREAM_SHA256,
        "unique_target_days": sum(map(len, union.values()))}
    if (any(_canonical(evidence.get(name)) != _canonical(value) for name, value in expected_evidence.items())
            or type(evidence.get("queried_rows")) is not int or not 0 <= evidence["queried_rows"] <= ck.RESOURCES["maximum_rows"]):
        raise ComparisonRefusal("upstream_input_evidence_mismatch")
    return reference


def _describe(values: list[Fraction]) -> dict:
    total, n = sum(values, Fraction()), len(values)
    return {"events": n, "sum_price_change": total, "mean_price_change": float(total / n),
        "mean_after_assumed_cost": float(total / n - COST),
        "gross_positive_events": sum(value > 0 for value in values),
        "cost_exceeding_events": sum(value > COST for value in values)}


def build_comparison(ck_report: Mapping, checkpoint: Callable[[], None] | None = None) -> dict:
    """Pure comparison, with all upstream prerequisites checked before means."""
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    reference = _validate_upstream(ck_report, checkpoint)
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "reference_sessions": reference, "assumptions": ASSUMPTIONS, **FLAGS,
        "qualified_cells_power_prerequisite_met": True, "requires_operator_choice": False,
        "upstream_calibration_requires_operator_choice": ck_report["requires_operator_choice"],
        "upstream_legacy_calibration_requires_operator_choice": ck_report["upstream_calibration_requires_operator_choice"],
        "excluded_horizons_minutes": {family: list(values) for family, values in EXCLUDED_HORIZONS.items()},
        "input_evidence": {"upstream_report_sha256": UPSTREAM_SHA256,
            "upstream_report_path": REGISTERED_PATHS["upstream_path"], "calibration_input_evidence": ck_report["input_evidence"]},
        "variants": {}}
    for key, variant in ck_report["variants"].items():
        families = {}
        for family in FAMILIES:
            checkpoint()
            summary = variant["families"][family]
            cells = {}
            for h in QUALIFIED_HORIZONS[family]:
                selected = [row for row in variant["days"] if row["families"][family]["labels"] is not None]
                pairs = [row["families"][family]["labels"][str(h)] for row in selected]
                current, earlier = ([horizons._ratio(pair[arm]["price_change"]) for pair in pairs] for arm in ("current", "earlier"))
                delta = [early - old for early, old in zip(earlier, current, strict=True)]
                uncertainty = horizons._statistics([{"session": row["session"], "label": change}
                    for row, change in zip(selected, delta, strict=True)], reference, checkpoint)
                mean = float(sum(delta, Fraction()) / len(delta))
                error = uncertainty["design_se"]
                if error is None:
                    raise ComparisonRefusal("paired_uncertainty_unavailable")
                margin = NormalDist().inv_cdf(.975) * error
                cells[str(h)] = {**summary["by_horizon"][str(h)], "current": _describe(current), "earlier": _describe(earlier),
                    "paired": {"events": len(delta), "sum_change": sum(delta, Fraction()), "mean_change": mean,
                        "improved_events": sum(value > 0 for value in delta), "equal_events": sum(value == 0 for value in delta),
                        "worse_events": sum(value < 0 for value in delta), "uncertainty": uncertainty,
                        "descriptive_normal_95_interval": [mean - margin, mean + margin]}}
            families[family] = {**summary, "by_horizon": cells}
        result["variants"][key] = {"days": variant["days"], "families": families}
    checkpoint()
    return result


def run_comparison(*, specification_path: str | Path, upstream_path: str | Path,
        output_directory: str | Path, runs_path: str | Path, budget=None,
        before_completion: Callable[[], None] | None = None) -> dict:
    """Durably start before the sole CK input read; caller owns budget finish."""
    if budget is None:
        from research.research_budget import ResearchBudget
        budget = ResearchBudget()
    checkpoint = lambda: budget.checkpoint("entry_delay_comparison_pipeline")
    checkpoint()
    spec_path, upstream, output, log = map(Path, (specification_path, upstream_path, output_directory, runs_path))
    if spec_path != SPECIFICATION_PATH:
        raise ComparisonRefusal("unregistered_specification_path")
    spec_bytes = spec_path.read_bytes()
    spec, spec_sha = json.loads(spec_bytes), hashlib.sha256(spec_bytes).hexdigest()
    validate_spec(spec)
    paths = (spec_path, upstream, output, log)
    for path, key in zip((upstream, output, log), REGISTERED_PATHS, strict=True):
        if not path.is_absolute() or path != Path(REGISTERED_PATHS[key]):
            raise ComparisonRefusal("unregistered_path")
    for index, path in enumerate(paths):
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise ComparisonRefusal("symlinked_path")
        if any(path.resolve() == other.resolve() or (path.exists() and other.exists() and path.samefile(other)) for other in paths[:index]):
            raise ComparisonRefusal("aliased_paths")
    sources = (spec_path, Path(__file__), Path(ck.__file__), Path(ci.__file__), Path(ch.__file__), Path(cg.__file__),
        ROOT / "python/research/research_budget.py", Path(horizons.__file__), Path(cd.__file__), Path(cc.__file__),
        Path(cc.core.__file__), Path(experiment_log.__file__), Path(cd.conclusion_check.__file__),
        ROOT / "python/research/__init__.py", ROOT / "python/pyproject.toml", ROOT / "python/uv.lock")
    executable = Path(sys.executable).resolve(strict=True)
    for path in (upstream, output, log):
        if any(path.resolve() == source.resolve()
                or (path.exists() and source.exists() and path.samefile(source)) for source in (*sources, executable)):
            raise ComparisonRefusal("aliased_source_or_runtime_path")
    pins = {str(path): _sha(path) for path in sources}
    runtime = {"python": sys.version, "implementation": sys.implementation.name,
        "executable": str(executable), "executable_sha256": _sha(executable)}
    if pins[str(spec_path)] != spec_sha:
        raise ComparisonRefusal("specification_changed")

    def evaluate() -> dict:
        checkpoint()
        if not output.parent.is_dir() or os.path.lexists(output):
            raise ComparisonRefusal("unsafe_or_existing_output")
        if shutil.disk_usage(output.parent).free < RESOURCES["minimum_free_bytes"]:
            raise ComparisonRefusal("insufficient_free_disk")
        output.mkdir(mode=0o700)
        cc._sync_directory(output.parent)
        if output.stat().st_mode & 0o777 != 0o700:
            raise ComparisonRefusal("unsafe_output_permissions")
        with upstream.open("rb") as stream:
            upstream_bytes = stream.read(RESOURCES["maximum_upstream_bytes"] + 1)
        checkpoint()
        if len(upstream_bytes) > RESOURCES["maximum_upstream_bytes"] or hashlib.sha256(upstream_bytes).hexdigest() != UPSTREAM_SHA256:
            raise ComparisonRefusal("upstream_size_or_hash_mismatch")
        upstream_report = json.loads(upstream_bytes)
        del upstream_bytes
        report = build_comparison(upstream_report, checkpoint)
        del upstream_report
        if len(report["reference_sessions"]) != EXPECTED_REFERENCE_SESSIONS:
            raise ComparisonRefusal("registered_reference_population_changed")
        for key, variant in report["variants"].items():
            for family in FAMILIES:
                if variant["families"][family]["selected_complete"] != EXPECTED_SELECTED_COMPLETE[key][family]:
                    raise ComparisonRefusal("registered_selected_population_changed")
        if any(report["input_evidence"]["calibration_input_evidence"][name] != value for name, value in EXPECTED_INPUT_COUNTS.items()):
            raise ComparisonRefusal("registered_raw_population_changed")
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
        if any(_sha(path) != pins[str(path)] for path in sources) or _sha(upstream) != UPSTREAM_SHA256:
            raise ComparisonRefusal("source_specification_or_input_changed")
        if _sha(executable) != runtime["executable_sha256"] or _sha(report_path) != report_sha:
            raise ComparisonRefusal("runtime_or_output_changed")
        checkpoint()
        return {"output_path": str(report_path), "output_sha256": report_sha, "input_evidence": report["input_evidence"],
            "requires_operator_choice": False, **FLAGS}

    return experiment_log.run_discovery_trial(study_id=SPECIFICATION_ID, specification_sha256=spec_sha,
        parameters={"mode": MODE, "parameters": PARAMETERS, "source_sha256": pins, "runtime": runtime,
            "upstream_sha256": UPSTREAM_SHA256, "resources": RESOURCES},
        window=dict(WINDOW), evaluate=evaluate, runs_path=log)
