"""One logged, blind TREND dispersion pilot over the registered saved subset.

Signal targets are frozen before any saved holding/opening quote is restored.
Only the new signal baseline/formation union is read from the existing spent
scan. Full books and attributed paths stay private; public output contains
dispersion/MDE, counts and hashes, never mean returns, ranks or chosen codes.
This is no comparison family, source certificate or promotion decision.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import math
import os
from pathlib import Path
import resource
import signal
from statistics import NormalDist
import time
import traceback

from research import activity_cohort_preflight as base
from research import activity_preflight as saved
from research import activity_price_parity as prices
from research import experiment_log
from research import price_trend_comparison as trend
from research.activity_holding_inputs import adapt_holding_scan
from research.activity_pair_calibration import PARAMETERS
from research.activity_paired_replay import _json_value
from research.activity_portfolio import calibration
from research.activity_quote_extension import _same
from research.activity_raw_quote_view import RawEpisodeQuoteInputs
from research.activity_timing import AvailabilityMetadata, SessionLagPolicy
from research.price_trend_signal import PriceTrendObservation


SCHEMA = "price-trend-pilot-v1"
SOURCE_PATH = "python/research/price_trend_pilot.py"
MODE = "blind_price_trend_dispersion_pilot"
CPU_LIMIT_SECONDS = 2400
WALL_LIMIT_SECONDS = 3600
ADDRESS_SPACE_BYTES = 1536 * 1024**2
OLD_ROLES = (*base.BASE_ROLES, *base.RAW_ROLES, "return_policy")
CACHED_ROLES = ("cohort_declarations", "holding_read_scope")
PRICE_BASIS = "KIS_adjusted_daily_declared"
BASIS_REFERENCE = "KIS adjusted close relative-price exploration; vendor vintage and comparable economic units unverified"
RESTRICTED_POPULATION = "eligible needed_normal_windows with exactly 60 preceding normal dates; other source rows retained outside this experiment"
OPERATOR_DECISION = "2026-10-19 certified179 price-return exploration; retain five-issuer unmodeled assets and underpower limitation"


def source_contract(spec: dict) -> dict:
    """Restore the original BS input contract without adding TREND to its types."""
    return {**spec["source_cohort_contract"], "inputs": {role: spec["inputs"][role] for role in OLD_ROLES}}


def validate_spec(spec: dict, reference: dict) -> None:
    saved.require(type(spec) is dict and set(spec) == {"schema", "study_id", "mode", "promotion_allowed",
        "reference_specification", "window", "parameters", "source_cohort_contract", "restricted_population",
        "expected_candidate_windows", "expected_formations", "expected_active_formations", "operator_decision", "inputs"},
        "exact registered blind trend pilot contract required")
    saved.require(spec["schema"] == spec["study_id"] == SCHEMA and spec["mode"] == MODE
        and spec["promotion_allowed"] is False and spec["reference_specification"] == saved.REFERENCE
        and saved.encoded(spec["parameters"]) == saved.encoded(PARAMETERS)
        and saved.encoded(spec["parameters"]) == saved.encoded(reference["parameters"])
        and saved.encoded(spec["window"]) == saved.encoded(saved.WINDOW), "unchanged pilot parameters/window required")
    saved.require(spec["restricted_population"] == RESTRICTED_POPULATION
        and spec["operator_decision"] == OPERATOR_DECISION
        and all(type(spec[key]) is int and spec[key] == value for key, value in (
            ("expected_candidate_windows", 179), ("expected_formations", 14), ("expected_active_formations", 12))),
        "fixed operator-approved restricted candidate/formation scope required")
    declared = spec["inputs"]
    saved.require(type(declared) is dict and set(declared) == {*OLD_ROLES, "frontier", "scan_database"}
        and type(spec["source_cohort_contract"]) is dict and "inputs" not in spec["source_cohort_contract"],
        "exact original saved inputs plus frontier and signal scan required")
    original = source_contract(spec)
    base.validate_contract(original, reference)
    saved.require(original["mode"] == base.RETURN_MODE and original["return_policy_schema"] == base.RETURN_POLICY_SCHEMA_V3,
        "unchanged original five-issuer price-return contract required")
    frontier, scan = declared["frontier"], declared["scan_database"]
    saved.require(type(frontier) is dict and set(frontier) == {"path", "sha256"}
        and frontier["sha256"] == original["holding_verification_identity"]["input_sha256"]["bs_normal_frontier"],
        "original verified normal frontier pin required")
    saved._pin(frontier["sha256"])
    saved.require(type(scan) is dict and set(scan) == {"path", "mode", "columns"}
        and scan["mode"] == "ro" and scan["columns"] == list(prices.READ_COLUMNS),
        "exact read-only spent signal scan columns required")
    paths = []
    for row in declared.values():
        saved.require(type(row["path"]) is str and Path(row["path"]).is_absolute()
            and ".." not in Path(row["path"]).parts, "absolute non-aliased pilot input paths required")
        paths.append(str(saved._safe_path(Path(row["path"]))))
    saved.require(len(paths) == len(set(paths)), "pilot input path aliases refused")


class PilotInputs(saved.PinnedInputs):
    """Cache only the two metadata bodies needed again by the strict BS loader."""

    def __init__(self, reader):
        super().__init__(reader)
        self.reused_roles: list[dict] = []
        self._metadata: dict[str, tuple[Path, str, bytes]] = {}

    def read(self, path: Path, expected: str, role: str) -> bytes:
        if role in self._metadata:
            original_path, pin, body = self._metadata[role]
            saved.require(path == original_path and expected == pin, "cached metadata declaration changed")
            self.reused_roles.append({"role": role, "path": str(path), "sha256": pin,
                "read_status": "verified_memory_reuse_no_filesystem_read"})
            return body
        saved.require(not any(row["role"] == role for row in self.manifest), "unregistered repeated input role read")
        body = super().read(path, expected, role)
        if role in CACHED_ROLES:
            self._metadata[role] = path, expected, body
        return body


def _write(output: Path, name: str, value) -> str:
    body = saved.encoded(_json_value(value))
    saved.write_exclusive(output / name, body)
    return saved.digest(body)


def _read(spec, inputs, role):
    declaration = spec["inputs"][role]
    return saved.strict_json(inputs.read(Path(declaration["path"]), declaration["sha256"], role))


def _metadata_universe(spec, inputs):
    frontier = _read(spec, inputs, "frontier")
    declarations = base._declarations(_read(spec, inputs, "cohort_declarations"))
    scope = _read(spec, inputs, "holding_read_scope")
    saved.require(type(scope) is dict and scope.get("producer_schema") == base.holding.SCHEMA
        and scope.get("scope_kind") == "complete_BS_family_proof_only_original_issue_union_not_successor_closure"
        and scope.get("returns_computed") is False
        and scope.get("cohort_declarations_sha256") == spec["inputs"]["cohort_declarations"]["sha256"],
        "original price-free holding scope/declaration links required")
    dates = scope.get("calendar_dates")
    saved.require(type(dates) is list and all(type(day) is str for day in dates), "saved complete calendar required")
    calendar = tuple(date.fromisoformat(day) for day in dates)
    SessionLagPolicy._calendar(calendar)
    saved.require(calendar[0] >= date.fromisoformat(spec["window"]["start"]), "reserved calendar is outside pilot scope")
    saved._pin(scope.get("activity_snapshot_sha256"))
    universe = trend.derive_certified_trend_universe(frontier, declarations, calendar, parameters=spec["parameters"])
    activities = tuple(row for row in declarations if row.arm == "D1" and row.group_id == "ACTIVITY")
    saved.require(universe.candidate_window_count == spec["expected_candidate_windows"]
        and len(activities) == spec["expected_formations"]
        and sum(row.target_ceiling > 0 for row in activities) == spec["expected_active_formations"],
        "registered restricted candidate/formation counts differ")
    return universe, scope["activity_snapshot_sha256"]


def _signal_observations(holding, universe, scope_pin):
    identities = {}
    for formation, code, isin, normal in universe.windows:
        for day in (normal[0], formation):
            key = code, day
            saved.require(key not in identities or identities[key] == isin, "signal coordinate has conflicting exact ISINs")
            identities[key] = isin
    positions, panel = {day: index for index, day in enumerate(holding.calendar)}, holding.panel
    saved.require(set(identities) == set(holding.requested_coordinates), "signal quote union changed during projection")
    result = []
    for row in holding.observations:
        saved.require(row.typed_values is not None and not row.issues, "unresolved or absent signal price row")
        provenance = saved.strict_json(row.provenance_json)
        meta = AvailabilityMetadata(row.observation_date, datetime.fromisoformat(provenance["available_at"]),
            "unknown" if provenance["retrieved_at"] == "unknown" else datetime.fromisoformat(provenance["retrieved_at"]),
            provenance["source"], provenance["is_final"], provenance["data_vintage"], provenance["evidence_level"],
            source_public_available_at=None if provenance["source_public_available_at"] is None else
                datetime.fromisoformat(provenance["source_public_available_at"]),
            availability_policy=provenance["availability_policy"],
            evidence_reference=f"modeled availability declaration in signal read scope {scope_pin}; historical publication unknown")
        key = row.code, row.observation_date
        result.append(PriceTrendObservation(row.code, identities[key], panel[row.code].closes[positions[key[1]]],
            PRICE_BASIS, holding.quote_snapshot_sha256, BASIS_REFERENCE, meta))
    return tuple(result)


def blind_calibration(replay: dict, parameters: dict) -> dict:
    """Use only each arm's own session dispersion, conservatively including IID SE."""
    result = {}
    for arm in trend.cohort.ARMS:
        summary = replay["aggregates"][arm][trend.GROUP]
        returns = [float(row["session_return"]) for row in summary["metrics"]["path"]]
        saved.require(all(math.isfinite(value) for value in returns), "finite blind pilot session returns required")
        book = {"returns": returns, "diagnostics": {"filled_entries": summary["filled_entries"]},
            "closed_trades": summary["ordinary_closed_trades"], "unresolved_positions": summary["terminal_open_lots"]}
        value = calibration(book, parameters)
        se = max(value["iid_session_se"], *value["block_se"].values())
        normal = NormalDist()
        detectable = (normal.inv_cdf(1 - parameters["alpha"]) + normal.inv_cdf(parameters["power"])) * se * 10000
        value.update(corrected_to_naive_se=se / value["iid_session_se"], detectable_daily_bps=detectable,
            cost_floor_resolvable=detectable <= value["cost_floor_daily_bps"],
            standard_error_policy="maximum of own IID and all registered own circular-block SEs",
            interpretation="own TREND dispersion planning only; no pairwise comparison, mean return or edge claim")
        result[arm] = value
    return result


def evaluate_inputs(spec: dict, inputs: PilotInputs, output: Path, *, progress: dict) -> dict:
    universe, activity_pin = _metadata_universe(spec, inputs)
    requested = {}
    for code, day in universe.required_price_coordinates:
        requested.setdefault(code, []).append(day.isoformat())
    scope = {"schema": SCHEMA + ":signal-read-scope-v1", "reuse_identity": universe.reuse_identity,
        "calendar_dates": [day.strftime("%Y%m%d") for day in universe.calendar],
        "intersection_code_dates": requested, "scan_database": spec["inputs"]["scan_database"],
        "candidate_window_count": universe.candidate_window_count, "price_basis": PRICE_BASIS,
        "basis_reference": BASIS_REFERENCE, "historical_vintage_certified": False,
        "input_manifest_before_database": inputs.manifest.copy()}
    scope_pin = _write(output, "signal-read-scope.json", scope)
    scan = prices.load_scan(Path(spec["inputs"]["scan_database"]["path"]), scope, inputs, scope_pin)
    holding = adapt_holding_scan(scan, universe.calendar, requested, quote_snapshot_sha256=scan["typed_snapshot_sha256"],
        read_scope_sha256=scope_pin, activity_snapshot_sha256=activity_pin)
    signal_pin = holding.quote_snapshot_sha256
    audit_pin = _write(output, "signal-input-audit.json", {"schema": SCHEMA + ":signal-input-audit-v1",
        "typed_snapshot_sha256": signal_pin, "read_scope_sha256": scope_pin, "fingerprint": scan["fingerprint"],
        "observations": [{"code": row.code, "observation_date": row.observation_date,
            "typed_values": row.typed_values, "provenance": saved.strict_json(row.provenance_json), "issues": row.issues}
            for row in holding.observations], "whole_database_hash": False, "historical_vintage_certified": False})
    observations = _signal_observations(holding, universe, scope_pin)
    declarations = trend.build_trend_declarations(universe, observations, parameters=spec["parameters"])
    declaration_pin = _write(output, "fixed-trend-declarations.json", declarations)
    # No original holding/opening body is accessed until the durable target file.
    del scan, holding, observations
    prepared = base.prepare_inputs(source_contract(spec), inputs)
    saved.require(_same(prepared.declarations, universe.original_declarations), "restored original declarations changed")
    _write(output, "input-manifest-before-cohorts.json", {"actual_reads": inputs.manifest, "reused_roles": inputs.reused_roles})
    cohorts = output / "cohorts"
    cohorts.mkdir(mode=0o700)
    saved._sync_directory(output)
    expected = tuple(trend.cohort._key(row) for row in declarations)

    def before(row):
        key = trend.cohort._key(row)
        ordinal = len(progress["started"])
        saved.require(ordinal < len(expected) and key == expected[ordinal]
            and len(progress["completed"]) == ordinal, "one ordered pilot cohort at a time required")
        name = f"cohort-{ordinal + 1:04d}-{row.arm}-{row.formation_on.isoformat()}"
        _write(cohorts, name + ".started.json", {"schema": SCHEMA + ":cohort-start-v1", "key": key,
            "chosen_codes": row.chosen_codes, "target_ceiling": row.target_ceiling,
            "fixed_declarations_sha256": declaration_pin, "automatic_retries": 0})
        progress["started"].append({"key": key, "name": name})

    def completed(result):
        saved.require(progress["started"] and result["key"] == progress["started"][-1]["key"],
            "completed pilot book must match its durable start")
        name = progress["started"][-1]["name"]
        body = saved.encoded(_json_value(result))
        saved.write_exclusive(cohorts / (name + ".book.json"), body)
        receipt = {"key": result["key"], "file": name + ".book.json", "sha256": saved.digest(body), "bytes": len(body)}
        _write(cohorts, name + ".inventory.json", receipt)
        progress["completed"].append(receipt)

    raw = prepared.quotes if type(prepared.quotes) is RawEpisodeQuoteInputs else None
    replay = trend.replay_price_trend_cohorts(declarations, raw.base if raw is not None else prepared.quotes,
        prepared.binding.events, universe=universe, parameters=spec["parameters"], raw_quote_view=raw,
        scope_binding=prepared.binding, before_cohort=before, on_cohort=completed)
    saved.require(tuple(row["key"] for row in progress["completed"]) == expected
        and replay["cohort_evaluation_counts"] == dict.fromkeys(trend.cohort.ARMS, spec["expected_formations"]),
        "complete registered pilot cohort family required")
    inventory_pin = _write(output, "cohort-inventory.json", progress["completed"])
    aggregate_pin = _write(output, "private-trend-aggregates.json", {"schema": SCHEMA + ":private-aggregates-v1",
        "aggregates": replay["aggregates"], "calendar": replay["calendar"],
        "fixed_declarations_sha256": declaration_pin, "quote_snapshot_sha256": replay["quote_snapshot_sha256"],
        "interpretation": "private saved TREND paths; no comparison report registered or executed"})
    arms = blind_calibration(replay, spec["parameters"])
    return {"schema": SCHEMA, "status": "blind_trend_dispersion_calibrated", "arms": arms,
        "candidate_window_count": universe.candidate_window_count, "formation_count": spec["expected_formations"],
        "active_formation_count": spec["expected_active_formations"], "reuse_identity": universe.reuse_identity,
        "pilot_cohorts_by_arm": replay["cohort_evaluation_counts"], "pilot_cohort_looks_completed": len(expected),
        "D1_completed_comparison_studies": 0, "D2_completed_comparison_studies": 0, "old_control_books_replayed": 0,
        "signal_scope_sha256": scope_pin, "signal_input_audit_sha256": audit_pin,
        "signal_typed_snapshot_sha256": signal_pin, "fixed_declarations_sha256": declaration_pin,
        "cohort_inventory_sha256": inventory_pin, "private_aggregates_sha256": aggregate_pin,
        "activity_snapshot_sha256": replay["activity_snapshot_sha256"], "quote_snapshot_sha256": replay["quote_snapshot_sha256"],
        "conditional_scope_binding_sha256": prepared.binding.binding_sha256,
        "own_dispersion_cost_floor_resolvable": all(row["cost_floor_resolvable"] for row in arms.values()),
        "sampling_unit": "one TREND attributed portfolio session per arm; overlapping cohorts are not independent observations",
        "comparison_family_registered": False, "comparison_power_prerequisite_met": False, "power_design_passed": False,
        "books_replayed": True, "returns_computed_privately": True, "public_mean_returns_reported": False,
        "database_access": True, "api_access": False, "new_source_requests": 0, "automatic_retries": 0,
        "source_truth_certified": False, "historical_vintage_certified": False, "price_basis_certified": False,
        "whole_population_complete": False, "actual_investment_return": False, "promotion_allowed": False,
        "independent_output_verification": "pending", **base.policy_report(prepared.binding.return_policy_json)}


def _self_oom_score_adj() -> int:
    """Inspect only this research process's Linux OOM adjustment."""
    return int(Path("/proc/self/oom_score_adj").read_text(encoding="ascii").strip())


def _protect_self_from_oom() -> None:
    """Prefer this process for OOM termination without touching another PID."""
    Path("/proc/self/oom_score_adj").write_text("1000\n", encoding="ascii")
    saved.require(_self_oom_score_adj() == 1000, "research process must retain OOM score adjustment 1000")


def _resources(started):
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {"elapsed_seconds": time.monotonic() - started, "process_peak_rss_kib": usage.ru_maxrss,
        "process_user_cpu_seconds": usage.ru_utime, "process_system_cpu_seconds": usage.ru_stime,
        "address_space_limit_bytes": list(resource.getrlimit(resource.RLIMIT_AS)),
        "cpu_limit_seconds": list(resource.getrlimit(resource.RLIMIT_CPU)), "wall_limit_seconds": WALL_LIMIT_SECONDS,
        "nice": os.getpriority(os.PRIO_PROCESS, 0), "oom_score_adj": _self_oom_score_adj()}


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path, *, reader=None, freeze=None):
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root and Path(__file__).resolve() == root / SOURCE_PATH,
        "run committed blind pilot source from repository root")
    freezer = saved.freeze_sources if freeze is None else freeze
    sources, raw, spec = freezer(root, spec_path, spec_validator=validate_spec)
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    saved.require(not output.exists(), "exclusive pilot output already exists")
    large = {Path(spec["inputs"][role]["path"]) for role in ("cohort_declarations", "holding_input_audit")}
    def bounded_reader(path):
        return base._read_large(path) if path in large else saved.read_private(path)
    inputs = PilotInputs(bounded_reader if reader is None else reader)
    progress = {"started": [], "completed": []}
    source_pin, spec_pin = saved.digest(saved.encoded(sources)), saved.digest(raw)

    def evaluate():
        created, started = False, time.monotonic()
        try:
            saved.private_directory(output.parent)
            output.mkdir(mode=0o700)
            created = True
            saved._sync_directory(output.parent)
            saved.write_exclusive(output / "specification.json", raw)
            saved.write_exclusive(output / "source-manifest.json", saved.encoded(sources))
            result = evaluate_inputs(spec, inputs, output, progress=progress)
            after, after_raw, _ = freezer(root, spec_path, spec_validator=validate_spec)
            identity = lambda value: {key: item for key, item in value.items() if key != "installed_locked_distributions"}
            saved.require(identity(after) == identity(sources) and after_raw == raw, "source/runtime changed during pilot")
            resources = _resources(started)
            saved.require(resources["elapsed_seconds"] <= WALL_LIMIT_SECONDS
                and resources["process_user_cpu_seconds"] + resources["process_system_cpu_seconds"] <= CPU_LIMIT_SECONDS,
                "observed pilot resources exceed registered limits")
            result.update(source_manifest_sha256=source_pin, specification_sha256=spec_pin,
                resource_manifest_sha256=_write(output, "resource-manifest.json", resources),
                input_manifest_sha256=_write(output, "input-manifest.json", inputs.manifest),
                reused_roles_sha256=_write(output, "reused-roles.json", inputs.reused_roles))
            body = saved.encoded(result)
            saved.write_exclusive(output / "result.json", body)
            return {**result, "result_sha256": saved.digest(body)}
        except BaseException as error:
            if created:
                for name, value in (("resource-manifest.json", _resources(started)),
                        ("input-manifest.json", inputs.manifest), ("reused-roles.json", inputs.reused_roles)):
                    if not (output / name).exists():
                        _write(output, name, value)
                _write(output, "failure.json", {"schema": SCHEMA + ":failure-v1", "status": "failed",
                    "error_type": type(error).__name__, "error_frames": [{"file": Path(frame.filename).name,
                        "line": frame.lineno, "function": frame.name} for frame in traceback.extract_tb(error.__traceback__)],
                    "source_manifest_sha256": source_pin, "specification_sha256": spec_pin,
                    "pilot_cohorts_started_by_arm": {arm: sum(row["key"][0] == arm for row in progress["started"]) for arm in trend.cohort.ARMS},
                    "pilot_cohorts_completed_by_arm": {arm: sum(row["key"][0] == arm for row in progress["completed"]) for arm in trend.cohort.ARMS},
                    "D1_completed_comparison_studies": 0, "D2_completed_comparison_studies": 0,
                    "partial_package_preserved": True, "automatic_retries": 0, "promotion_allowed": False})
            raise

    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"], specification_sha256=spec_pin,
            window=spec["window"], runs_path=runs, parameters={**spec["parameters"], "mode": MODE,
                "evaluation_unit": "one blind pilot family with individually recorded TREND cohort looks",
                "expected_pilot_cohorts_by_arm": dict.fromkeys(trend.cohort.ARMS, spec["expected_formations"]),
                "source_manifest_sha256": source_pin, "declared_inputs": spec["inputs"], "output_dir": str(output),
                "comparison_family_registered": False, "automatic_retries": 0}, evaluate=evaluate)
    finally:
        os.umask(previous)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runs-path", type=Path, required=True)
    args = parser.parse_args(argv)
    def expired(signum, frame):
        raise TimeoutError("registered blind pilot wall limit exceeded")
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT_SECONDS,) * 2)
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(WALL_LIMIT_SECONDS)
    try:
        priority = os.getpriority(os.PRIO_PROCESS, 0)
        saved.require(priority <= 10 and os.nice(10 - priority) == 10, "exact nice10 required")
        _protect_self_from_oom()
        record = run_preflight(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
    except Exception as error:
        print(saved.encoded({"status": "failed", "error_type": type(error).__name__, "automatic_retries": 0}).decode())
        return 1
    finally:
        signal.alarm(0)
    print(saved.encoded(record["result"]).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
