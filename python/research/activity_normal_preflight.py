"""Logged normal-screen assembly from immutable BI and saved official KRX inputs.

No new API/DB/credentials, holding-price reads or return calculation. The
complete formation population remains an explicit source/proof partition.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
import os
from pathlib import Path
import traceback

from research import activity_preflight as saved
from research import activity_scope_preflight as prior
from research import experiment_log
from research.activity_failure import activity_failure
from research.activity_failure_preflight import plan_windows, day
from research.activity_failure_inputs import restore_activity_failure
from research.activity_normal_assembly import assemble_window
from research.activity_normal_sources import load_sources

SCHEMA = "activity-normal-input-assembly-v1"
INPUTS = {
    "calendar_manifest": {
        "path": "/home/minjun4897/research-evidence/large-liquid-calendar-20261007-v1/manifest.json",
        "sha256": "ccdcee1681ad9fa16aef837e4aceaf4d2f40847974046a1714d1352c3df01882"
    },
    "bb_result": {
        "path": "/home/minjun4897/research-evidence/large-liquid-bb-replay-readiness-20261009-v1/result.json",
        "sha256": "2ac0b2c81d19921d19cfea95a850574d9bef8dc7b5d1ebc900aa1f039ea0d9b4"
    },
    "bh_result": {
        "path": "/home/minjun4897/research-evidence/large-liquid-bh-timing-preflight-20261009-recovery-v1/run/result.json",
        "sha256": "dc9234b69339a5c4cce9295d00436c0a1d198402936a798c8eac636811e70ef6"
    },
    "au_result": {
        "path": "/home/minjun4897/research-evidence/large-liquid-normal-lookbacks-20261008-v1/result.json",
        "sha256": "eb95349f4a5555a45363b98ae907c6a7791e12297a3fee17fa7729a492ce1fa4"
    },
    "bi_result": {
        "path": "/home/minjun4897/research-evidence/large-liquid-bi-activity-failures-20261009-v1/run/result.json",
        "sha256": "b011e43ff4c34c26e8f6e66b216b202b1f1b06c340abc9e4d1340b6393729dc3"
    },
    "bk_adjudication": {
        "path": "/home/minjun4897/research-evidence/large-liquid-bk-evidence-adjudication-20261010-v1/verification/bk-period-adjudication.json",
        "sha256": "2c6f1fab960bf5214fe0a176b20ffee2207e4d10844c23b632a9cfebe1bef893"
    },
    "aq": {
        "root": "/home/minjun4897/research-evidence/krx-formations-20261007-v2/run",
        "report_sha256": "17ae2a87663ed592c2968b9ce051f4fa248574a6d70561e704e7b5a27c8c593b",
        "started_sha256": "2bce0c4b916a1f67415e91dd1b4372ae8425e518d98624661725c58407ee48e9",
        "ledger_sha256": "f85c8a4403eaacde5d6fd0195054568262cbbb4cdab98ffdc846ab45933f2487"
    },
    "ar_original": {
        "root": "/home/minjun4897/research-evidence/krx-liquidity-20261007-v1/run",
        "report_sha256": "53effb672a849084148511be55cda1e92e90d181d0d6ee47291545ecdf12b612",
        "started_sha256": "34b0abbd41bd360a9309114b6cdb34dd30516bff747586d7190cc6d8acb0a79f",
        "ledger_sha256": "8b2c2367f1ac6e0d3ca5d8f07dd2cf9380bb99a593b0fd769d5e8495fb8da622"
    },
    "ar_recovery": {
        "root": "/home/minjun4897/research-evidence/krx-liquidity-recovery-20261007-v1/run",
        "report_sha256": "f23e37b0b6613a4720c21949b63f8c3ba1ba0a6c97546478b9abae522ca983ad",
        "started_sha256": "e7a3f7290abc6e7541a1ff005c39e0ec07a1e4d99bfcd64b182b7cb404141e27",
        "ledger_sha256": "2f86c7af7db76f2fcf6410bcef477eef7b5e25973db87b4e4a8ff13f39e34518",
        "candidates_sha256": "09a942994bf2d4eec746a5cfb2337cf0bc47fb3d4f271d21339fb97f7a1a95d6"
    }
}
COUNTS = {"cap_below": 34327, "negative_type": 27, "bh_failure": 41,
          "bi_failure": 840, "normal_window": 36}
ADJUDICATION_SHA256 = INPUTS["bk_adjudication"]["sha256"]


def validate_spec(spec: dict, reference: dict) -> None:
    saved.require(spec.get("schema") == SCHEMA and spec.get("study_id") == SCHEMA
        and spec.get("mode") == "discovery_normal_input_assembly"
        and spec.get("promotion_allowed") is False
        and spec.get("reference_specification") == saved.REFERENCE
        and spec.get("window") == saved.WINDOW
        and spec.get("parameters") == reference["parameters"]
        and type(spec["parameters"]["threshold"]) is type(reference["parameters"]["threshold"])
        and spec.get("inputs") == INPUTS, "fixed BL specification mismatch")


def adjudication_records(ledger: dict, potential: set[tuple[str, str]]) -> dict[str, dict]:
    records = ledger.get("records")
    saved.require(isinstance(records, list), "invalid BK adjudication records")
    by_code = {}
    actual = set()
    for record in records:
        code = record["code"]
        saved.require(code not in by_code and record.get("decision") == "accepted",
                      "duplicate or unaccepted BK code")
        by_code[code] = record
        for window in record["required_windows"]:
            key = date.fromisoformat(window["formation"]).strftime("%Y%m%d"), code
            saved.require(key not in actual
                          and window["window_id"] == "/".join(key),
                          "duplicate or mismatched BK window identity")
            actual.add(key)
    saved.require(actual == potential and set(by_code) == {code for _, code in potential},
                  "BK decisions do not cover exactly the potential windows")
    return by_code


def replay_proofs(windows: list[dict], loaded, bi: dict, calendar: tuple[date, ...],
                  params: dict) -> list[dict]:
    """Recompute every BI result from the restored pinned consumed observations."""
    expected = {(row["formation"], row["code"]): row for row in bi["windows"]}
    saved.require(len(expected) == len(bi["windows"]) == len(windows),
                  "duplicate/missing BI result")
    actual = []
    for row in windows:
        key = row["formation"], row["code"]
        result = activity_failure(calendar, day(key[0]), loaded.observations[key[1]],
            bb_required_observed_dates=row["required_dates"],
            bb_frozen_dates=row["frozen_dates"], bb_formation_state=row["formation_state"],
            threshold=Decimal(str(params["threshold"])), lookback=params["lookback"])
        value = {"formation": key[0], "code": key[1], **result.to_dict()}
        saved.require(expected.get(key) == value, "restored BI proof differs from pinned result")
        actual.append(value)
    saved.require(set(expected) == {(row["formation"], row["code"]) for row in actual},
                  "restored BI key partition mismatch")
    return actual


def partition_population(population: list[dict], bh_rows: list[dict], bi_rows: list[dict],
                         potential: set[tuple[str, str]], *, counts: dict = COUNTS) -> list[dict]:
    """Every source row has exactly one justified disposition; no narrowed panel."""
    bh = {(row["formation"], row["code"]): row for row in bh_rows}
    bi = {(row["formation"], row["code"]): row for row in bi_rows}
    saved.require(len(bh) == len(bh_rows) and len(bi) == len(bi_rows),
                  "duplicate proof population")
    result, seen = [], set()
    for row in population:
        key = row["formation"], row["code"]
        saved.require(key not in seen and type(row["cap_pass"]) is bool,
                      "duplicate or invalid formation source identity")
        seen.add(key)
        if not row["cap_pass"]:
            disposition, proof = "cap_below", {"capitalization": row["capitalization"]}
            saved.require(key not in bh and key not in bi and key not in potential,
                          "cap failure unexpectedly in subsequent proof population")
        elif row["negative_type"] is not None:
            disposition, proof = "negative_type", {"literal_type": row["negative_type"]}
            saved.require(key not in bh and key not in bi and key not in potential,
                          "negative-type control unexpectedly in activity population")
        else:
            saved.require(key in bh, "cap-pass source lacks prior disposition")
            prior_row = bh[key]
            if prior_row["proven_numeric_failures"]:
                disposition, proof = "bh_failure", prior_row
                saved.require(key not in bi and key not in potential,
                              "BH failure unexpectedly in BI population")
            else:
                saved.require(key in bi, "unresolved BH key missing BI proof")
                current = bi[key]
                if current["proven_failure"]:
                    disposition, proof = "bi_failure", current
                    saved.require(key not in potential, "BI failure became potential")
                else:
                    saved.require(key in potential, "numeric pass missing normal assembly")
                    disposition, proof = "normal_window", {"reason": current["reason"]}
        result.append({**row, "disposition": disposition, "proof": proof})
    actual_counts = dict(Counter(row["disposition"] for row in result))
    saved.require(actual_counts == counts and len(result) == sum(counts.values()),
                  "complete formation disposition counts mismatch")
    saved.require(set(bh) | set(bi) | potential <= seen, "proof outside source population")
    saved.require({(row["formation"], row["code"]) for row in result
                   if row["disposition"] == "normal_window"} == potential,
                  "normal assembly source keys mismatch")
    return result


def population_audit(rows: list[dict]) -> dict:
    """Preserve resolvable row provenance without repeating service metadata.

    Large formation populations otherwise serialize the same date/service
    declaration three times per row. References retain the exact field-specific
    metadata rather than dropping it or inventing per-row publication times.
    """
    metadata, identifiers, compact = {}, {}, []
    for row in rows:
        value = dict(row)
        for field in ("capitalization_availability", "classification_availability",
                      "trade_section_availability"):
            if field not in row:
                continue  # Small synthetic partition fixtures have no source reader.
            key = saved.encoded(row[field])
            if key not in identifiers:
                reference = f"m{len(metadata):04d}"
                identifiers[key] = reference
                metadata[reference] = row[field]
            value[field] = identifiers[key]
        compact.append(value)
    return {"schema": "activity-normal-population-audit-v1",
            "availability_metadata": metadata, "rows": compact}


def evaluate_inputs(spec: dict, inputs: saved.PinnedInputs, output: Path) -> dict:
    declared = spec["inputs"]
    values = {role: inputs.json(Path(declared[role]["path"]), declared[role]["sha256"], role)
              for role in ("calendar_manifest", "bb_result", "bh_result", "au_result", "bi_result",
                           "bk_adjudication")}
    calendar_strings, _, schedule = saved.calendar_schedule(values["calendar_manifest"], spec["parameters"])
    calendar = tuple(day(value) for value in calendar_strings)
    population_receipt = prior.population_snapshot(values["bh_result"], schedule)
    scope = prior.plan_scope(population_receipt, values["bi_result"], calendar_strings,
                             schedule, spec["parameters"])
    potential = {(row["formation"], row["code"]) for row in scope["possible_windows"]}
    records = adjudication_records(values["bk_adjudication"], potential)
    bb_windows, _ = saved._bb_windows(values["bb_result"], calendar_strings)
    windows, requested = plan_windows(bb_windows, values["bh_result"]["coverage"]["windows"],
                                      values["au_result"]["rows"], calendar_strings)
    read_scope = {"schema": SCHEMA, "restored_activity_source": declared["bi_result"],
        "calendar_dates": [d.isoformat() for d in calendar],
        "upstream_dataset_sha256": values["bi_result"]["scan_snapshot"]["dataset_sha256"],
        "typed_OHLC_fingerprint_recomputed": False,
        "requested_code_dates": {code: [d.isoformat() for d in dates] for code, dates in requested.items()},
        "planned_normal_windows": [list(key) for key in sorted(potential)],
        "database_access": False, "new_source_requests": 0, "returns_computed": False}
    saved.write_exclusive(output / "read-scope.json", saved.encoded(read_scope))
    loaded = restore_activity_failure(values["bi_result"]["scan_snapshot"], calendar, requested)
    bi_proofs = replay_proofs(windows, loaded, values["bi_result"], calendar, spec["parameters"])
    bh_proofs = population_receipt["prior_dispositions"]
    # Drop large upstream raw provenance before reading the cached raw envelopes.
    del values, scope, population_receipt, bb_windows
    sources = load_sources(declared, inputs, schedule, calendar, potential, output)
    partition = partition_population(sources["population"], bh_proofs, bi_proofs, potential)
    planned = {(row["formation"], row["code"]): row for row in windows}
    saved.require(set(sources["potential_sources"]) == potential,
                  "source join omitted or added potential windows")
    normal = []
    screen_params = {**spec["parameters"],
                     "threshold": Decimal(str(spec["parameters"]["threshold"]))}
    for key in sorted(potential):
        source = sources["potential_sources"][key]
        record = records[key[1]]
        accepted = next(row for row in record["required_windows"]
                        if row["formation"] == day(key[0]).isoformat())
        saved.require(accepted["isin"] == source["isin"]
                      and accepted["krx_metadata_provenance"] == source["krx_metadata_provenance"],
                      "accepted issue source provenance mismatch")
        observations = {d: loaded.observations[key[1]][d] for d in planned[key]["required_dates"]}
        assembled = assemble_window(calendar=calendar, formation=day(key[0]), code=key[1],
            isin=source["isin"], listing_date=source["listing_date"], observations=observations,
            capitalization=source["capitalization"], liquidity=source["liquidity"],
            accepted_record=record, params=screen_params)
        assembled["formation_source_provenance"] = source["source_provenance"]
        normal.append(assembled)
    arms = {}
    for arm in ("D1", "D2"):
        selected = [{"formation": row["formation"], "code": row["code"], "isin": row["isin"]}
                    for row in normal if row["arms"][arm]["selected"]]
        arms[arm] = {"screen_windows": len(normal), "signal_windows": len(selected),
                     "signals": selected, "performance_completed": False}
    a = {(r["formation"], r["code"]) for r in arms["D1"]["signals"]}
    b = {(r["formation"], r["code"]) for r in arms["D2"]["signals"]}
    summary = {"source_rows": len(partition),
        "disposition_counts": dict(sorted(Counter(r["disposition"] for r in partition).items())),
        "replayed_BI_windows": len(bi_proofs), "normal_windows": len(normal),
        "normal_codes": len({r["code"] for r in normal}),
        "signal_windows": {arm: arms[arm]["signal_windows"] for arm in arms},
        "paired_signal_symmetric_difference": len(a ^ b), "paired_signal_union": len(a | b),
        "unresolved_input_windows": 0}
    saved.write_exclusive(output / "population-audit.json", saved.encoded(population_audit(partition)))
    saved.write_exclusive(output / "normal-input-audit.json", saved.encoded(normal))
    restored_observations = {
        code: {d.isoformat(): {"state": row.state,
            "turnover": str(row.turnover) if row.turnover is not None else None,
            "public_available_at": None,
            "availability": {name: value.isoformat() if isinstance(value, (date, datetime)) else value
                             for name, value in asdict(row.availability).items()}}
               for d, row in rows.items()}
        for code, rows in loaded.observations.items()}
    saved.write_exclusive(output / "restored-activity-audit.json", saved.encoded({
        "upstream_result_sha256": declared["bi_result"]["sha256"],
        "upstream_dataset_sha256": loaded.snapshot.dataset_sha256,
        "typed_OHLC_fingerprint_recomputed": False, "replayed_proofs": bi_proofs,
        "restored_observations_by_code": restored_observations}))
    return {"schema": SCHEMA, "status": "normal_inputs_assembled", "summary": summary,
        "paired_schedule": schedule, "arms": arms, "formation_totals": sources["formation_totals"],
        "negative_type_controls": sources["negative_type_controls"],
        "activity_input": {"source_sha256": declared["bi_result"]["sha256"],
            "upstream_dataset_sha256": loaded.snapshot.dataset_sha256,
            "typed_OHLC_fingerprint_recomputed": False,
            "failure_reuse_condition": "same consumed KIS turnover/state snapshot and unchanged AN predicate"},
        "accepted_classification_evidence_level": "inferred",
        "historical_publication_evidence_level": "assumed",
        "historical_publication_certified": False, "original_source_vintage_certified": False,
        "price_basis_certified": False, "holding_selection_executed": False,
        "holding_lot_denominator": None, "action_lot_denominator": None,
        "returns_computed": False, "D1_completed_studies": 0, "D2_completed_studies": 0,
        "new_source_requests": 0, "database_access": False, "promotion_allowed": False,
        "remaining_obligations": ["consequential prices/actions", "actual portfolio replay integration",
                                  "frozen logged paired performance study"],
        "input_manifest": inputs.manifest}


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path,
                  *, reader=saved.read_private) -> dict:
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root, "run from repository root")
    sources, raw, spec = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    inputs = saved.PinnedInputs(reader)

    def evaluate():
        created = False
        try:
            saved.private_directory(output.parent)
            output.mkdir(mode=0o700)
            created = True
            saved._sync_directory(output.parent)
            saved.write_exclusive(output / "specification.json", raw)
            saved.write_exclusive(output / "source-manifest.json", saved.encoded(sources))
            result = evaluate_inputs(spec, inputs, output)
            after, after_raw, _ = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
            identity = lambda value: {k: v for k, v in value.items()
                                      if k != "installed_locked_distributions"}
            saved.require(identity(after) == identity(sources) and after_raw == raw, "source/runtime changed")
            result.update(source_manifest_sha256=saved.digest(saved.encoded(sources)),
                          specification_sha256=saved.digest(raw))
            files = ("read-scope.json", "raw-source-read-scope.json", "population-audit.json",
                     "normal-input-audit.json", "restored-activity-audit.json")
            audits = {name: saved.digest((output / name).read_bytes()) for name in files}
            result["audit_sha256"] = audits
            saved.write_exclusive(output / "result.json", saved.encoded(result))
            saved.write_exclusive(output / "input-manifest.json", saved.encoded(inputs.manifest))
            return {"status": result["status"], "output_dir": str(output), "summary": result["summary"],
                    "result_sha256": saved.digest(saved.encoded(result)),
                    "source_manifest_sha256": result["source_manifest_sha256"],
                    "input_manifest_sha256": saved.digest(saved.encoded(inputs.manifest)),
                    "audit_sha256": audits, "D1_completed_studies": 0, "D2_completed_studies": 0}
        except BaseException as exc:
            if created:
                if not (output / "input-manifest.json").exists():
                    saved.write_exclusive(output / "input-manifest.json", saved.encoded(inputs.manifest))
                saved.write_exclusive(output / "failure.json", saved.encoded({"status": "failed",
                    "error_type": type(exc).__name__, "actual_inputs_attempted": inputs.manifest,
                    "error_frames": [{"file": Path(frame.filename).name, "line": frame.lineno,
                                      "function": frame.name}
                                     for frame in traceback.extract_tb(exc.__traceback__)],
                    "source_manifest_sha256": saved.digest(saved.encoded(sources)),
                    "specification_sha256": saved.digest(raw),
                    "partial_package_preserved": True, "automatic_retries": 0}))
            raise

    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"],
            specification_sha256=saved.digest(raw), window=spec["window"], runs_path=runs,
            parameters={**spec["parameters"], "source_manifest": sources, "declared_inputs": spec["inputs"],
                        "output_dir": str(output), "automatic_retries": 0}, evaluate=evaluate)
    finally:
        os.umask(previous)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runs-path", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = run_preflight(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
    except Exception as exc:
        print(saved.encoded({"status": "failed", "error_type": type(exc).__name__,
                             "automatic_retries": 0}).decode())
        return 1
    print(saved.encoded(record["result"]).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
