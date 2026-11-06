"""Bind a registered EG fit to the audited, split scenario predecessor."""

from pathlib import Path
import re

from research import intraday_campaign_wsl_transport as wire
from research import intraday_highvol_fit_run as fit_run
from research import intraday_highvol_runtime as runtime
from research import intraday_highvol_snapshot_unit as snapshot
from research.intraday_campaign_wsl_bridge import bounded_read
from research.intraday_highvol_coverage import MAX_COMPRESSED
from research.intraday_highvol_model_dataset import MINIMUM_DAYS, MINIMUM_ROWS
from research.intraday_highvol_models import BATCH_SIZE, DILATIONS, EPOCHS, SEED, STATES, TASKS
from research.intraday_highvol_snapshot_io import FILENAMES, ROLES
from research.intraday_highvol_unit import SCIENCE, _json


SPEC = "configs/research/discovery/intraday-highvol-fit-v1.json"
INPUT_CONTRACT = "eg-highvol-model-fit-v1"
FIT_IDS = (*(f"EG-TCN-{branch}-{timing}-1" for branch in ("A", "B") for timing in ("S1", "S2")),
           "EG-LGB-A-1", "EG-LGB-B-1")
PROOF_LIMIT = 2 * wire.MIB  # Leave space for the runtime journal envelope, summary and artifact manifest.


def fit_contract(specification: bytes, science: bytes, unit_id: str, *, actual: bool) -> None:
    """Validate the registered scientific and resource envelope without data I/O."""
    spec, design = map(_json, (specification, science))
    roster = {}
    for name in FIT_IDS:
        cells = fit_run.fit_roster(name)
        roster[name] = {"branch": cells[0].branch, "timings": list(dict.fromkeys(cell.timing for cell in cells)),
                        "arms": list(dict.fromkeys(cell.arm for cell in cells))}
    wire.require(type(actual) is bool and unit_id in FIT_IDS and spec["id"] == "intraday-highvol-fit-v1"
                 and spec["campaign"] == design["campaign"] == "EG" and design["id"] == "intraday-highvol-v1"
                 and spec["units"] == roster and spec["execution_host"] == "local_wsl_only"
                 and spec["resource_profile"] == design["resource_approval"]["profile"] == runtime.PROFILE
                 and spec["uses_cuda"] is False and spec["automatic_actual_retry"] is False
                 and spec["live_or_paper_order_capable"] is False
                 and type(spec["actual_runner_ready"]) is bool
                 and spec["actual_execution_authorized_by_this_file"] is spec["actual_runner_ready"],
                 "EG fit registration scope differs")
    if actual:
        wire.require(spec["actual_runner_ready"] is True, "EG fit actual source not registered ready")
    bounds, resources = design["campaign_bounds"], design["resource_approval"]
    wire.require(spec["fit_limits"] == {"folds": [1, 2, 3, 4, 5], "max_fits_per_unit": 10,
                   "max_primary_fits": 60, "max_followup_units": 4, "max_followup_fits": 20,
                   "refit_in_verifier": False}
                 and spec["fit_limits"]["refit_in_verifier"] is False
                 and set(FIT_IDS) <= set(bounds["primary_units"]) and bounds["primary_market_fits_max"] == 60
                 and bounds["conditional_followup_units_max"] == 4 and bounds["conditional_followup_market_fits_max"] == 20
                 and resources["cpu_cores"] == 4 and resources["shared_cpu_seconds"] == 7200
                 and resources["wall_seconds"] == 3600 and resources["aggregate_actual_host_memory_mib"] == 3072
                 and resources["swap_mib"] == 0 and resources["concurrent_units"] == 1
                 and resources["nice"] == 10 and resources["automatic_actual_retry"] is False,
                 "EG fit count or resource limits differ")
    tcn, lgb, support = spec["tcn"], spec["lgb"], spec["training_support"]
    wire.require(spec["tasks"] == list(TASKS) and spec["state_features"] == list(STATES)
                 and support["minimum_mature_primary_rows"] == MINIMUM_ROWS
                 and support["minimum_original_days"] == MINIMUM_DAYS
                 and tcn["backend"] == "CPU FP32 deterministic Torch 2.7.0+cu128 with hidden CUDA devices"
                 and tuple(tcn[key] for key in ("intraop_threads", "interop_threads", "minute_slots", "chart_width",
                                               "peer_width", "tower_width", "heads")) == (4, 1, 60, 12, 18, 16, 3)
                 and tcn["dilations"] == list(DILATIONS) and tcn["seed_base"] == SEED and tcn["seed_add_fold"] is True
                 and (tcn["epochs"], tcn["batch_size"]) == (EPOCHS, BATCH_SIZE)
                 and lgb["backend"] == "single-thread LightGBM 4.6.0, NumPy 2.5.2, SciPy 1.18.1"
                 and spec["entry"]["costs_bps"] == [33, 43], "EG fit model or scientific limits differ")


def _document(path, maximum=runtime.MAX_METADATA):
    raw = bounded_read(path, maximum)
    return raw, runtime._json(raw)


def _snapshot_scope(invented_only, attempt, audit_sha256, merge_commit):
    wire.require(type(invented_only) is bool and type(attempt) is str
                 and (re.fullmatch(r"qualification[1-9][0-9]*", attempt) is not None if invented_only
                      else attempt == "actual1")
                 and wire.valid_sha(audit_sha256) and type(merge_commit) is str
                 and re.fullmatch(r"[0-9a-f]{40}", merge_commit) is not None, "EG fit snapshot pin or attempt differs")
    return runtime.NATIVE_ROOT / snapshot.UNIT / attempt


def _predecessor(directory, invented_only, attempt, audit_sha256, merge_commit, checkpoint):
    audit_raw, audit = _document(directory / "original-audit.json", wire.MIB)
    wire.require(wire.digest(audit_raw) == audit_sha256 and audit["schema"] == "EG-original-audit-v1"
                 and audit["status"] == "PASS" and audit["science_reexecuted"] is False
                 and audit["full_geometry"] == runtime.FULL, "EG fit snapshot audit differs")
    publication_raw, publication = _document(directory / "publication.json")
    binding, result = publication["binding"], publication["result"]
    wire.require(publication["schema"] == "EG-unit-publication-v1" and audit["binding"] == binding
                 and audit["publication_sha256"] == wire.digest(publication_raw)
                 and binding["unit_id"] == snapshot.UNIT and binding["resource_profile"] == runtime.PROFILE
                 and binding["input_contract"] == snapshot.INPUT_CONTRACT and binding["attempt"] == attempt
                 and binding["invented_only"] is invented_only and binding["merge_commit"] == merge_commit
                 and binding["geometry"] == result["geometry"] == runtime.FULL
                 and all(wire.valid_sha(binding[field]) for field in
                         ("source_sha256", "runtime_sha256", "registration_sha256"))
                 and not (directory / "unresolved.json").exists()
                 and not (directory / "unresolved.json").is_symlink(), "EG fit snapshot publication differs")
    for name, expected in (("service-final.json", audit["service_final_sha256"]),
                           ("service-exit.json", audit["service_exit_sha256"]),
                           ("discovery.jsonl", publication["journal_sha256"])):
        wire.require(wire.valid_sha(expected) and wire.file_digest(directory / name, runtime.MAX_METADATA, checkpoint)[0]
                     == expected, "EG fit audited predecessor lifecycle bytes differ")

    receipt_raw, receipt = _document(directory / snapshot.OUTPUT, wire.MIB)
    independent, bound = result["independent_verification"], receipt["binding"]
    wire.require(set(bound) == set(binding) | {"input_manifest_sha256", "coverage_seal_sha256", "coverage_audit_sha256"}
                 and all(bound[key] == value for key, value in binding.items())
                 and all(wire.valid_sha(bound[field]) for field in ("input_manifest_sha256", "coverage_seal_sha256"))
                 and (bound["coverage_audit_sha256"] is None if invented_only else
                      wire.valid_sha(bound["coverage_audit_sha256"]))
                 and receipt["geometry"] == runtime.FULL and receipt["report"] == result["summary"]
                 and independent["complete"] is True
                 and receipt["independent_verification"] == independent["raw_reconstruction"]
                 and set(receipt["seals"]) == set(ROLES), "EG fit snapshot receipt scope differs")
    expected = [runtime.Artifact(FILENAMES[role], receipt["seals"][role]["sha256"],
                                receipt["seals"][role]["compressed_bytes"], MAX_COMPRESSED).payload() for role in ROLES]
    expected.append(runtime.Artifact(snapshot.OUTPUT, wire.digest(receipt_raw), len(receipt_raw), wire.MIB).payload())
    wire.require(expected == result["artifacts"], "EG fit audited snapshot artifact pins differ")
    persisted = snapshot.verify_persisted(directory, receipt, checkpoint)
    wire.require(independent["persisted"] == persisted, "EG fit snapshot persisted independent evidence differs")
    return receipt


def _require_started(directory, binding):
    raw = bounded_read(directory / "discovery.jsonl", wire.MIB)
    records = raw.splitlines(keepends=True)
    wire.require(len(records) == 1 and records[0].endswith(b"\n"), "EG fit needs a durable started-only journal")
    started = _json(records[0])
    wire.require(started["record_type"] == "discovery_trial" and started["status"] == "started"
                 and started["study_id"] == binding["unit_id"] and started["parameters"] == binding
                 and started["specification_sha256"] == binding["registration_sha256"]
                 and started["code_version"] == binding["merge_commit"] and started["window"] == runtime.WINDOW
                 and started["promotion_allowed"] is False and started["promotion_trial_increment"] == 0,
                 "EG fit start binding differs")


def _output(directory, receipt, checkpoint):
    artifacts = tuple(runtime.Artifact(row["name"], row["seal"]["sha256"], row["seal"]["compressed_bytes"],
                                       MAX_COMPRESSED) for row in receipt["coordinates"])
    receipt_sha, size = wire.file_digest(directory / fit_run.OUTPUT, wire.MIB, checkpoint)
    wire.require(receipt_sha == wire.digest(wire.encode(receipt)), "EG fit stored and produced receipt differ")
    artifacts += (runtime.Artifact(fit_run.OUTPUT, receipt_sha, size, wire.MIB),)
    summary = {"fit_count": receipt["fit_count"], "maximum_fits": receipt["maximum_fits"],
               "snapshot": receipt["snapshot"], "coordinates": [
                   {key: row[key] for key in ("coordinate", "fit_status", "candidate_counts")}
                   for row in receipt["coordinates"]]}
    return runtime.UnitOutput(artifacts, wire.encode(runtime.FULL), wire.encode(summary))


def closed_fit_unit(run: runtime.RunContract, specification: bytes, science: bytes, *,
                    snapshot_attempt: str, snapshot_audit_sha256: str, snapshot_merge_commit: str) -> runtime.ClosedUnit:
    """Compose registration, STARTED-gated fitting and persisted replay callbacks."""
    predecessor = _snapshot_scope(run.invented_only, snapshot_attempt, snapshot_audit_sha256, snapshot_merge_commit)
    saved = {}

    def preflight():
        pins = dict(run.source_hashes)
        wire.require(pins.get(SPEC) == wire.digest(specification) and pins.get(SCIENCE) == wire.digest(science)
                     and run.binding["input_contract"] == INPUT_CONTRACT, "EG fit registration pins differ")
        fit_contract(specification, science, run.unit_id, actual=not run.invented_only)
        saved["prepared"] = True

    def compute(directory: Path, binding: dict, checkpoint):
        wire.require(saved.get("prepared") is True and "computed" not in saved
                     and directory == run.directory and binding == run.binding,
                     "EG fit compute binding or preflight differs")
        _require_started(directory, binding)
        prior = _predecessor(predecessor, run.invented_only, snapshot_attempt, snapshot_audit_sha256,
                             snapshot_merge_commit, checkpoint)
        receipt = fit_run.produce_fit(directory, predecessor, prior, binding, run.unit_id, checkpoint)
        output = _output(directory, receipt, checkpoint)
        saved["computed"] = prior, receipt, output
        return output

    def independent_verify(directory: Path, output: runtime.UnitOutput, binding: dict, checkpoint):
        wire.require("computed" in saved and directory == run.directory and binding == run.binding,
                     "EG fit persisted replay binding differs")
        prior, receipt, expected = saved["computed"]
        wire.require(output == expected, "EG fit verification output handoff differs")
        proof = fit_run.verify_fit(directory, predecessor, prior, binding, run.unit_id, checkpoint)
        fitted = [row for row in proof["coordinates"] if row["status"] == "verified"]
        wire.require(proof["complete"] is True and proof["refit_count"] == 0
                     and proof["fit_count"] == receipt["fit_count"] == len(fitted)
                     and proof["receipt_sha256"] == output.artifacts[-1].sha256
                     and [row["coordinate"] for row in proof["coordinates"]] ==
                     [row["coordinate"] for row in receipt["coordinates"]]
                     and all(type(row["replayed_rows"]) is int and row["replayed_rows"] > 0 for row in fitted)
                     and len(wire.encode(proof)) <= PROOF_LIMIT, "EG fit replay proof differs or exceeds journal bound")
        wire.require(not run.invented_only or bool(fitted), "EG invented qualification fitted zero models")
        return proof

    return runtime.ClosedUnit(preflight, compute, independent_verify)
