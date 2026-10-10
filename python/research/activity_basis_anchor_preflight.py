"""One logged three-anchor diagnostic over seven pinned retained artifacts.

Restore existing quote components; never reread a DB, API or raw response body.
Same-code/session price joins and exact ratios do not grant an executable basis,
ISIN identity, vendor unit convention, historical vintage or event certificate.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import os
from pathlib import Path
import re
import time
import traceback

from research import activity_exit_preflight as logged
from research import activity_preflight as saved
from research import experiment_log
from research.activity_holding_restore import restore_holding_inputs
from research.activity_quote_extension import extend_holding_quotes

SCHEMA = "activity-basis-anchor-preflight-v1"
SOURCE_PATH = "python/research/activity_basis_anchor_preflight.py"
INPUT_FILES = {"bm_read_scope": "read-scope.json", "bm_holding_input_audit": "holding-input-audit.json",
    "bm_verification": "verification.json", "quote_extension": "quote-extension.json",
    "quote_verification": "verification.json", "raw_anchor_result": "result.json", "raw_verification": "verification.json"}
TARGETS = (("lg_old_candidate", "003550", "2021-04-28", "last_observable_pre_event_candidate"),
    ("lg_retained_candidate", "003550", "2021-05-27", "retained_availability_candidate"),
    ("lx_new_candidate", "383800", "2021-05-27", "successor_availability_candidate"))
FALSE_FLAGS = ("source_truth_certified", "event_coverage_certified", "price_basis_certified", "unit_bridge_certified",
    "historical_vintage_certified", "historical_publication_certified", "custody_delivery_certified", "isin_join_certified",
    "atomic_source_vintage_certified", "executable_basis_created", "actions_applied", "returns_computed", "books_computed",
    "actual_study_completed", "promotion_allowed", "database_access", "api_access", "raw_response_bodies_read")


def validate_spec(spec: dict, reference: dict) -> None:
    """Keep seven saved artifacts and the exact reference parameter encoding."""
    saved.require(type(spec) is dict and set(spec) == {"schema", "study_id", "mode", "promotion_allowed", "window",
        "reference_specification", "parameters", "inputs"} and spec["schema"] == spec["study_id"] == SCHEMA
        and spec["mode"] == "discovery_event_basis_anchor_diagnostic" and spec["promotion_allowed"] is False
        and spec["window"] == saved.WINDOW and spec["reference_specification"] == saved.REFERENCE
        and type(spec["parameters"]) is dict and saved.encoded(spec["parameters"]) == saved.encoded(reference["parameters"]),
        "fixed basis-anchor specification mismatch")
    saved.require(type(spec["inputs"]) is dict and set(spec["inputs"]) == set(INPUT_FILES), "exact seven saved artifacts required")
    paths = []
    for role, filename in INPUT_FILES.items():
        row = spec["inputs"][role]
        saved.require(type(row) is dict and set(row) == {"path", "sha256"} and type(row["path"]) is str
            and Path(row["path"]).is_absolute() and ".." not in Path(row["path"]).parts
            and Path(row["path"]).name == filename, "exact absolute saved filename/pin required")
        saved._pin(row["sha256"])
        paths.append(str(saved._safe_path(Path(row["path"]))))
    saved.require(len(set(paths)) == 7 and Path(paths[0]).parent == Path(paths[1]).parent,
        "saved inputs must be distinct and retain the original BM directory")


def _number(value: object) -> Decimal:
    saved.require(type(value) is str and len(value) <= 256 and value == value.strip(), "bounded Decimal TEXT required")
    try: parsed = Decimal(value)
    except InvalidOperation as exc: raise ValueError("invalid Decimal TEXT") from exc
    saved.require(parsed.is_finite() and parsed > 0 and abs(parsed.as_tuple().exponent) <= 256, "finite positive bounded amount required")
    return parsed


def _ratio(numerator: Decimal, denominator: Decimal) -> dict:
    """Retain exact rational directions without ambient Decimal rounding."""
    value = Fraction(numerator) / Fraction(denominator)
    n, d, twos, fives = value.numerator, value.denominator, 0, 0
    rest = d
    while rest % 2 == 0: rest //= 2; twos += 1
    while rest % 5 == 0: rest //= 5; fives += 1
    decimal = None
    if rest == 1:
        places = max(twos, fives)
        digits = str(n * 2 ** (places - twos) * 5 ** (places - fives)).zfill(places + 1)
        decimal = digits if not places else (digits[:-places] + "." + digits[-places:]).rstrip("0").rstrip(".")
    return dict(numerator=str(n), denominator=str(d), decimal_if_terminating=decimal)


def _raw_anchors(result: dict, verification: dict) -> tuple[dict, dict]:
    saved.require(type(result) is dict and result.get("schema") == "activity-raw-anchor-preflight-v1"
        and result.get("status") == "completed" and type(result.get("targets")) is list and len(result["targets"]) == 4,
        "completed four-target raw diagnostic required")
    dependencies = verification.get("response_dependency_pins")
    saved.require(type(dependencies) is dict and len(dependencies) == 3, "three independently verified response dependencies required")
    response_pins = set()
    for row in dependencies.values():
        saved.require(type(row) is dict and set(row) == {"sha256", "bytes"} and type(row["bytes"]) is int and row["bytes"] > 0,
            "bounded response dependency receipt required")
        saved._pin(row["sha256"]); response_pins.add(row["sha256"])
    saved.require(len(response_pins) == 3, "distinct raw response receipts required")
    raw, identity = {}, None
    wanted = {(code, day) for _, code, day, _ in TARGETS}
    for row in result["targets"]:
        saved.require(type(row) is dict and row.get("status") == "observed" and row.get("reason") is None
            and type(row.get("row_position")) is int and row["row_position"] >= 0, "unresolved/ambiguous raw target")
        receipt = row.get("retrieved_at")
        saved.require(type(receipt) is str, "raw current receipt required")
        timestamp = datetime.fromisoformat(receipt)
        saved.require(timestamp.utcoffset() is not None and timestamp.isoformat() == receipt
            and row.get("response_sha256") in response_pins
            and row.get("data_vintage") == "current_retrieval_sha256:" + row["response_sha256"]
            and row.get("evidence_level") == "observed_current_api_not_historical"
            and row.get("available_at") is None and row.get("is_final") is None, "raw current-vintage receipt mismatch")
        if row.get("kind") == "issue_identity":
            isin, listed = row.get("isin"), row.get("returned_LIST_DD")
            saved.require(identity is None and row.get("code") == "383800" and row.get("requested_bas_dd") == "20210527"
                and row.get("observation_date") is None and row.get("source") == "KRX OpenAPI stk_isu_base_info"
                and type(isin) is str and re.fullmatch(r"[A-Z]{2}[0-9A-Z]{9}[0-9]", isin)
                and type(listed) is str and re.fullmatch(r"[0-9]{8}", listed)
                and date.fromisoformat(listed).strftime("%Y%m%d") == listed <= "20210527", "raw identity context mismatch")
            identity = deepcopy(row)
            continue
        key = row.get("code"), row.get("observation_date")
        saved.require(row.get("kind") == "raw_close" and key in wanted and key not in raw
            and row.get("requested_bas_dd") == row.get("returned_BAS_DD") == key[1].replace("-", "")
            and row.get("source") == "KRX OpenAPI stk_bydd_trd", "raw code/session/source mismatch")
        original = row.get("original_raw_close")
        saved.require(type(original) is str and len(original) <= 256
            and re.fullmatch(r"[+]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", original), "raw source Decimal text required")
        close = _number(row.get("raw_close"))
        saved.require(format(_number(original.replace(",", "")), "f") == row["raw_close"], "raw source/normalized price mismatch")
        raw[key] = (close, deepcopy(row))
    saved.require(set(raw) == wanted and identity is not None
        and raw["003550", "2021-05-27"][1]["response_sha256"] == raw["383800", "2021-05-27"][1]["response_sha256"]
        and {row[1]["response_sha256"] for row in raw.values()} | {identity["response_sha256"]} == response_pins,
        "exact three prices and separate identity response required")
    return raw, identity


def project_anchors(original, extension, raw_result: dict, raw_verification: dict, *, expected_composite_sha256: str) -> dict:
    """Reuse validated components; preserve candidate roles and unidentified ISINs."""
    composite = extend_holding_quotes(original, extension)
    saved._pin(expected_composite_sha256)
    saved.require(composite.quote_snapshot_sha256 == expected_composite_sha256, "independently pinned composite identity mismatch")
    raw, identity = _raw_anchors(raw_result, raw_verification)
    anchors = []
    for anchor_id, code, text, role in TARGETS:
        day, key = date.fromisoformat(text), (code, date.fromisoformat(text))
        candidates = [(name, source) for name, source in (("original", original), ("extension", extension)) if key in source.requested_coordinates]
        saved.require(len(candidates) == 1, "missing/unrequested/ambiguous adjusted anchor")
        name, source = candidates[0]
        observation = next(row for row in source.observations if (row.code, row.observation_date) == key)
        index, series = source.calendar.index(day), source.panel[code]
        saved.require(observation.typed_values is not None and not observation.issues and series.observed[index] is True
            and series.frozen[index] is False, "absent/unresolved/frozen adjusted anchor")
        adjusted, (unadjusted, raw_row) = series.closes[index], raw[code, text]
        saved.require(isinstance(adjusted, Decimal) and _number(str(adjusted)).as_tuple() == adjusted.as_tuple(), "exact adjusted Decimal required")
        quote_row = dict(code=code, observation_date=text, state="locked" if series.locked[index] else "observed",
            issues=list(observation.issues), typed_values=observation.typed_values, provenance=saved.strict_json(observation.provenance_json))
        anchors.append(dict(anchor_id=anchor_id, code=code, session=text, candidate_role=role, isin=None, component=name,
            component_quote_snapshot_sha256=source.quote_snapshot_sha256, component_read_scope_sha256=source.read_scope_sha256,
            composite_quote_snapshot_sha256=composite.quote_snapshot_sha256, adjusted_quote_row=quote_row, raw_observation=raw_row,
            raw_close=format(unadjusted, "f"), adjusted_close=format(adjusted, "f"),
            raw_price_per_adjusted_price=_ratio(unadjusted, adjusted), raw_shares_per_adjusted_share_candidate=_ratio(adjusted, unadjusted)))
    return dict(schema=SCHEMA + ":projection-v1", status="diagnostic_projection_only", anchors=anchors,
        raw_identity_observation=identity, raw_source_schema=raw_result["schema"],
        activity_snapshot_sha256=composite.activity_snapshot_sha256, composite_quote_snapshot_sha256=composite.quote_snapshot_sha256,
        component_lineage=saved.strict_json(composite.fingerprint_json), prior_independent_receipts_relied_on=True,
        whole_package_independent_verification_performed=False, new_source_requests=0, automatic_retries=0,
        D1_completed_studies=0, D2_completed_studies=0, **dict.fromkeys(FALSE_FLAGS, False))


def _verify_receipts(documents: dict, pins: dict) -> None:
    """Bind retained artifacts to prior independent receipts without rereading their dependencies."""
    for role, required in (("bm_verification", {"read-scope.json": pins["bm_read_scope"], "holding-input-audit.json": pins["bm_holding_input_audit"]}),
        ("quote_verification", {"quote-extension.json": pins["quote_extension"]}), ("raw_verification", {"result.json": pins["raw_anchor_result"]})):
        receipt = documents[role]
        saved.require(type(receipt) is dict and receipt.get("verified") is True and type(receipt.get("file_sha256")) is dict
            and all(receipt["file_sha256"].get(name) == value for name, value in required.items())
            and all(receipt.get(name) is False for name in ("database_access", "api_access", "returns_computed",
                "historical_vintage_certified", "price_basis_certified", "event_coverage_certified"))
            and all(type(receipt.get(name)) is int and receipt[name] == 0 for name in ("D1_completed_studies", "D2_completed_studies")),
            "prior independent receipt links/boundaries mismatch")
    bm, bp, raw = (documents[name] for name in ("bm_verification", "quote_verification", "raw_verification"))
    audit, envelope = documents["bm_holding_input_audit"], documents["quote_extension"]
    saved.require(bm.get("scope") == "BM_new_output_typed_rows_scope_and_internal_lineage_only"
        and bp.get("scope") == "BP_new_output_typed_rows_exact_extension_scope_composite_and_publication_links_only"
        and type(envelope) is dict and set(envelope) == {"schema", "original_quote_snapshot_sha256", "read_scope", "holding_input_audit"}
        and envelope["schema"] == "activity-quote-extension-envelope-v1"
        and bm.get("activity_snapshot_sha256") == bp.get("activity_snapshot_sha256") == audit.get("activity_snapshot_sha256")
        and bm.get("quote_snapshot_sha256") == bp.get("original_quote_snapshot_sha256") == envelope["original_quote_snapshot_sha256"] == audit.get("quote_snapshot_sha256")
        and bp.get("extension_quote_snapshot_sha256") == envelope["holding_input_audit"].get("quote_snapshot_sha256")
        and bp.get("reference_sha256", {}).get("bm-read-scope.json") == pins["bm_read_scope"]
        and bp.get("reference_sha256", {}).get("bm-verification.json") == pins["bm_verification"], "BM/BP independent component lineage mismatch")
    saved.require(raw.get("schema") == "bp-raw-independent-verification-v1" and raw.get("current_retrieval_only") is True
        and raw.get("raw_response_row_extract_verified") is True and raw.get("targets_verified") == raw.get("observed_targets") == 4
        and type(raw.get("unresolved_targets")) is int and raw["unresolved_targets"] == 0, "raw independent extraction boundary mismatch")


def evaluate_inputs(spec: dict, inputs: saved.PinnedInputs, output: Path) -> dict:
    """Load each of seven saved artifacts once after durable discovery start."""
    pins = {role: spec["inputs"][role]["sha256"] for role in INPUT_FILES}
    documents = {role: saved.strict_json(inputs.read(Path(spec["inputs"][role]["path"]), pins[role], role)) for role in INPUT_FILES}
    _verify_receipts(documents, pins)
    audit, envelope, bm, bp = (documents[name] for name in ("bm_holding_input_audit", "quote_extension", "bm_verification", "quote_verification"))
    original = restore_holding_inputs(audit, documents["bm_read_scope"], quote_snapshot_sha256=bm["quote_snapshot_sha256"],
        activity_snapshot_sha256=bm["activity_snapshot_sha256"], read_scope_sha256=pins["bm_read_scope"])
    scope = envelope["read_scope"]
    saved.require(scope.get("original_quote_snapshot_sha256") == original.quote_snapshot_sha256
        and scope.get("original_read_scope_sha256") == original.read_scope_sha256, "extension original scope mismatch")
    extension = restore_holding_inputs(envelope["holding_input_audit"], scope, quote_snapshot_sha256=bp["extension_quote_snapshot_sha256"],
        activity_snapshot_sha256=original.activity_snapshot_sha256, read_scope_sha256=saved.digest(saved.encoded(scope)))
    projection = project_anchors(original, extension, documents["raw_anchor_result"], documents["raw_verification"],
        expected_composite_sha256=bp["composite_quote_snapshot_sha256"])
    projection["input_sha256"] = pins
    raw = saved.encoded(projection)
    saved.write_exclusive(output / "basis-anchor-projection.json", raw)
    return dict(schema=SCHEMA, status="basis_anchor_inputs_audited", summary=dict(anchors=3, raw_identity_contexts=1),
        projection_sha256=saved.digest(raw), input_sha256=pins, input_manifest=inputs.manifest,
        activity_snapshot_sha256=original.activity_snapshot_sha256, original_quote_snapshot_sha256=original.quote_snapshot_sha256,
        extension_quote_snapshot_sha256=extension.quote_snapshot_sha256, composite_quote_snapshot_sha256=bp["composite_quote_snapshot_sha256"],
        independent_output_verification="pending", new_source_requests=0, automatic_retries=0,
        D1_completed_studies=0, D2_completed_studies=0, **dict.fromkeys(FALSE_FLAGS, False))


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path, *, reader=None) -> dict:
    """Freeze committed source, log once, retain completion or private failure."""
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root and Path(__file__).resolve() == root / SOURCE_PATH, "use committed caller from repository root")
    sources, raw, spec = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
    saved.require(any(row["path"] == SOURCE_PATH and row["sha256"] == saved.digest(Path(__file__).read_bytes())
        for row in sources["files"]), "caller must belong to committed frozen source")
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    inputs = saved.PinnedInputs(saved.read_private if reader is None else reader)

    def evaluate():
        created, started = False, time.monotonic()
        try:
            saved.private_directory(output.parent); output.mkdir(mode=0o700); created = True; saved._sync_directory(output.parent)
            saved.write_exclusive(output / "specification.json", raw)
            saved.write_exclusive(output / "source-manifest.json", saved.encoded(sources))
            result = evaluate_inputs(spec, inputs, output)
            after, after_raw, _ = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
            identity = lambda value: {key: item for key, item in value.items() if key != "installed_locked_distributions"}
            saved.require(identity(after) == identity(sources) and after_raw == raw, "source/runtime changed")
            resources = saved.encoded(logged._resources(started))
            saved.write_exclusive(output / "resource-manifest.json", resources)
            saved.write_exclusive(output / "input-manifest.json", saved.encoded(inputs.manifest))
            result.update(source_manifest_sha256=saved.digest(saved.encoded(sources)), specification_sha256=saved.digest(raw),
                resource_manifest_sha256=saved.digest(resources), input_manifest_sha256=saved.digest(saved.encoded(inputs.manifest)))
            saved.write_exclusive(output / "result.json", saved.encoded(result))
            return dict(status=result["status"], output_dir=str(output), summary=result["summary"], result_sha256=saved.digest(saved.encoded(result)),
                projection_sha256=result["projection_sha256"], D1_completed_studies=0, D2_completed_studies=0)
        except BaseException as exc:
            if created:
                for name, value in (("resource-manifest.json", logged._resources(started)), ("input-manifest.json", inputs.manifest)):
                    if not (output / name).exists(): saved.write_exclusive(output / name, saved.encoded(value))
                saved.write_exclusive(output / "failure.json", saved.encoded(dict(status="failed", error_type=type(exc).__name__,
                    actual_inputs_attempted=inputs.manifest, error_frames=[dict(file=Path(frame.filename).name, line=frame.lineno,
                        function=frame.name) for frame in traceback.extract_tb(exc.__traceback__)], source_manifest_sha256=saved.digest(saved.encoded(sources)),
                    specification_sha256=saved.digest(raw), partial_package_preserved=True, automatic_retries=0,
                    D1_completed_studies=0, D2_completed_studies=0)))
            raise

    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"], specification_sha256=saved.digest(raw), window=spec["window"],
            runs_path=runs, parameters={**spec["parameters"], "source_manifest": sources, "declared_inputs": spec["inputs"],
                "output_dir": str(output), "automatic_retries": 0}, evaluate=evaluate)
    finally: os.umask(previous)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ("spec", "output-dir", "runs-path"): parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    try: record = run_preflight(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
    except Exception as exc:
        print(saved.encoded(dict(status="failed", error_type=type(exc).__name__, automatic_retries=0)).decode()); return 1
    print(saved.encoded(record["result"]).decode()); return 0


if __name__ == "__main__":
    raise SystemExit(main())
