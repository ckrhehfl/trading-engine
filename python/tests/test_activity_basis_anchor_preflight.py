"""Synthetic retained artifacts and logged boundaries; no actual evidence IO."""
from collections import Counter
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path
import socket
import sqlite3

import pytest

from research import activity_basis_anchor_preflight as module
from research import activity_preflight as saved
from research.activity_holding_inputs import _FINGERPRINT_SCOPE
from research.activity_quote_extension import extend_holding_quotes
import test_activity_quote_extension as synthetic_quotes

SOURCE = Path(module.__file__).resolve()
CALENDAR = tuple(date.fromisoformat(value) for value in ("2021-04-28", "2021-04-29", "2021-05-27", "2021-05-28"))
RECEIVED = "2026-10-10T00:00:00+00:00"
RECEIPT_FALSE = ("database_access", "api_access", "returns_computed", "historical_vintage_certified", "price_basis_certified", "event_coverage_certified")


def audit(component, scope):
    rows = []
    for item in component.observations:
        series, index = component.panel[item.code], CALENDAR.index(item.observation_date)
        state = "absent_requested" if item.typed_values is None else "unresolved_observed" if item.issues else "frozen" if series.frozen[index] else "locked" if series.locked[index] else "observed"
        rows.append(dict(code=item.code, observation_date=item.observation_date.isoformat(), state=state, issues=list(item.issues),
            typed_values=None if item.typed_values is None else [list(pair) for pair in item.typed_values], provenance=saved.strict_json(item.provenance_json)))
    return dict(schema="activity-holding-price-preflight-v1:holding-input-audit-v1", read_scope_sha256=component.read_scope_sha256,
        activity_snapshot_sha256=component.activity_snapshot_sha256, quote_snapshot_sha256=component.quote_snapshot_sha256,
        snapshot_received_at=component.snapshot_received_at, fingerprint=saved.strict_json(component.fingerprint_json),
        scan_metadata=dict(typed_snapshot_sha256=component.quote_snapshot_sha256, snapshot_received_at=component.snapshot_received_at,
            fingerprint_scope=_FINGERPRINT_SCOPE, whole_database_hash=False, BI_snapshot_equality_checked=False, progress_counts_recomputed_from_prices=False),
        requested_code_dates=deepcopy(scope["requested_code_dates"]), rows=rows,
        absent_coordinates=[[code, day.isoformat()] for code, day in component.absent_coordinates],
        unrequested_coordinates=[[code, day.isoformat()] for code, day in component.unrequested_coordinates],
        summary=dict(requested_codes=len(component.panel), requested_code_dates=len(component.requested_coordinates),
            absent_requested_code_dates=len(component.absent_coordinates), unrequested_code_dates=len(component.unrequested_coordinates),
            state_counts=dict(sorted(Counter(row["state"] for row in rows).items()))))


def fixture(tmp_path, monkeypatch, *, changes=None, missing=False, overlap=False):
    monkeypatch.setattr(synthetic_quotes, "DAYS", CALENDAR)
    activity = synthetic_quotes.ACTIVITY
    old_scope = dict(schema="activity-holding-price-preflight-v1:read-scope-v1", calendar_dates=[day.strftime("%Y%m%d") for day in CALENDAR],
        activity_snapshot_sha256=activity, requested_code_dates={"003550": ["2021-05-27"]})
    original = synthetic_quotes.component({"003550": (CALENDAR[2],)}, saved.digest(saved.encoded(old_scope)), changes=changes)
    requested = {"003550": ["2021-04-28"], "383800": ["2021-05-27"]}
    if missing: del requested["003550"]
    if overlap: requested["003550"].append("2021-05-27")
    scope = dict(old_scope, requested_code_dates=requested, original_quote_snapshot_sha256=original.quote_snapshot_sha256,
        original_read_scope_sha256=original.read_scope_sha256)
    extension = synthetic_quotes.component({code: tuple(date.fromisoformat(day) for day in days) for code, days in requested.items()},
        saved.digest(saved.encoded(scope)), changes=changes, receipt="2026-10-10T01:00:00+00:00")
    envelope = dict(schema="activity-quote-extension-envelope-v1", original_quote_snapshot_sha256=original.quote_snapshot_sha256,
        read_scope=scope, holding_input_audit=audit(extension, scope))
    deps = {name + ".raw.json": dict(sha256=saved.digest(name.encode()), bytes=100) for name in ("old", "new", "identity")}
    targets = []
    for (_, code, day, _), close in zip(module.TARGETS, ("200.000", "300", "125")):
        response = deps[("old" if day == "2021-04-28" else "new") + ".raw.json"]["sha256"]
        targets.append(dict(code=code, kind="raw_close", status="observed", reason=None, row_position=len(targets), observation_date=day,
            requested_bas_dd=day.replace("-", ""), returned_BAS_DD=day.replace("-", ""), available_at=None, retrieved_at=RECEIVED,
            source="KRX OpenAPI stk_bydd_trd", is_final=None, data_vintage="current_retrieval_sha256:" + response,
            evidence_level="observed_current_api_not_historical", response_sha256=response, raw_close=close, original_raw_close=close))
    identity_sha = deps["identity.raw.json"]["sha256"]
    targets.append(dict(code="383800", kind="issue_identity", status="observed", reason=None, row_position=1,
        observation_date=None, requested_bas_dd="20210527", available_at=None, retrieved_at=RECEIVED,
        source="KRX OpenAPI stk_isu_base_info", is_final=None, data_vintage="current_retrieval_sha256:" + identity_sha,
        evidence_level="observed_current_api_not_historical", response_sha256=identity_sha, isin="KR7000002000", returned_LIST_DD="20210527"))
    common = dict(verified=True, D1_completed_studies=0, D2_completed_studies=0, **dict.fromkeys(RECEIPT_FALSE, False))
    documents = dict(bm_read_scope=old_scope, bm_holding_input_audit=audit(original, old_scope), quote_extension=envelope,
        raw_anchor_result=dict(schema="activity-raw-anchor-preflight-v1", status="completed", targets=targets),
        bm_verification=dict(common, scope="BM_new_output_typed_rows_scope_and_internal_lineage_only", file_sha256={},
            activity_snapshot_sha256=activity, quote_snapshot_sha256=original.quote_snapshot_sha256),
        quote_verification=dict(common, scope="BP_new_output_typed_rows_exact_extension_scope_composite_and_publication_links_only", file_sha256={},
            reference_sha256={}, activity_snapshot_sha256=activity, original_quote_snapshot_sha256=original.quote_snapshot_sha256,
            extension_quote_snapshot_sha256=extension.quote_snapshot_sha256,
            composite_quote_snapshot_sha256="f" * 64 if overlap else extend_holding_quotes(original, extension).quote_snapshot_sha256),
        raw_verification=dict(common, schema="bp-raw-independent-verification-v1", file_sha256={}, response_dependency_pins=deps,
            current_retrieval_only=True, raw_response_row_extract_verified=True, targets_verified=4, observed_targets=4, unresolved_targets=0))
    parameters = dict(lookback=60, threshold=3.0, holding_sessions=126, end="2026-09-18", slippage_bps_per_side=5.0)
    declarations = {role: dict(path=str(tmp_path / "evidence" / ("bm" if role in ("bm_read_scope", "bm_holding_input_audit") else role) / name), sha256="f" * 64)
        for role, name in module.INPUT_FILES.items()}
    spec = dict(schema=module.SCHEMA, study_id=module.SCHEMA, mode="discovery_event_basis_anchor_diagnostic", promotion_allowed=False,
        window=dict(saved.WINDOW), reference_specification=saved.REFERENCE, parameters=parameters, inputs=declarations)
    return spec, documents, refresh(spec, documents), original, extension


def refresh(spec, documents):
    pin = lambda role: saved.digest(saved.encoded(documents[role]))
    documents["bm_verification"]["file_sha256"] = {"read-scope.json": pin("bm_read_scope"), "holding-input-audit.json": pin("bm_holding_input_audit")}
    documents["quote_verification"]["file_sha256"] = {"quote-extension.json": pin("quote_extension")}
    documents["quote_verification"]["reference_sha256"] = {"bm-read-scope.json": pin("bm_read_scope"), "bm-verification.json": pin("bm_verification")}
    documents["raw_verification"]["file_sha256"] = {"result.json": pin("raw_anchor_result")}
    store = {}
    for role, document in documents.items():
        raw = saved.encoded(document)
        spec["inputs"][role]["sha256"] = saved.digest(raw)
        store[Path(spec["inputs"][role]["path"])] = raw
    return store


def test_committed_spec_keeps_exact_reference_parameters_and_registered_seven_pins():
    root = SOURCE.parents[2]
    spec = saved.strict_json((root / "configs/research/discovery/activity-basis-anchor-preflight-v1.json").read_bytes())
    reference = saved.strict_json((root / saved.REFERENCE).read_bytes())
    module.validate_spec(spec, reference)
    assert [spec["inputs"][role]["sha256"] for role in module.INPUT_FILES] == [
        "905c52d3696006a1938df7c4cf06fd216e40b4e5fec584b4511b09d567d0734d", "567c800406e365a01b3bc6e86467d0d7a547062e09e64934872b81ecb08c594b",
        "c6895ace52cfb4beab4045aa4860bf009eb0b600ace4a5cf1e14d4d282b49529", "78e55336891168d4def5abb985a920763bb97e16988e6f52abff98de0e021716",
        "31620821e85d7b96116f32205879002e5c80ba5402c0fe3652336da93009c89e", "0db5101d5ca4392d090e3d2685b78b52a223ea6ed681d71de92107267da5ecf0",
        "fbeabc92ae02da39b821653a1bf2d4388870d25d32cd8b37409fbd8862a2caca"]
    assert [row[1:3] for row in module.TARGETS] == [("003550", "2021-04-28"), ("003550", "2021-05-27"), ("383800", "2021-05-27")]


def test_full_restore_and_exact_three_anchor_projection_read_each_artifact_once(tmp_path, monkeypatch):
    spec, documents, store, _, _ = fixture(tmp_path, monkeypatch)
    before, reads = deepcopy(documents), []
    for owner, name in ((sqlite3, "connect"), (socket, "create_connection")):
        monkeypatch.setattr(owner, name, lambda *a, **kw: pytest.fail("unexpected database/API access"))
    def reader(path): reads.append(path); return store[path]
    with localcontext() as context:
        context.prec = 2
        result = module.evaluate_inputs(spec, saved.PinnedInputs(reader), tmp_path)
    projection = saved.strict_json((tmp_path / "basis-anchor-projection.json").read_bytes())
    assert len(reads) == len(set(reads)) == 7 and documents == before
    old, retained, new = projection["anchors"]
    assert [row["component"] for row in projection["anchors"]] == ["extension", "original", "extension"]
    assert old["adjusted_quote_row"] == documents["quote_extension"]["holding_input_audit"]["rows"][0]
    assert old["raw_price_per_adjusted_price"] == dict(numerator="2", denominator="1", decimal_if_terminating="2")
    assert retained["raw_price_per_adjusted_price"]["decimal_if_terminating"] == "3"
    assert new["raw_price_per_adjusted_price"]["decimal_if_terminating"] == "1.25"
    assert retained["raw_shares_per_adjusted_share_candidate"] == dict(numerator="1", denominator="3", decimal_if_terminating=None)
    assert all(row["isin"] is None for row in projection["anchors"])
    assert projection["raw_identity_observation"]["observation_date"] is None
    assert result["projection_sha256"] == saved.digest((tmp_path / "basis-anchor-projection.json").read_bytes())
    assert all(result[name] is projection[name] is False for name in module.FALSE_FLAGS)


@pytest.mark.parametrize("mutation", ["receipt_pin", "receipt_cert", "raw_unverified", "composite", "original_id", "state", "issues", "typed", "raw_date", "raw_duplicate", "raw_price", "raw_vintage", "identity", "response"])
def test_repinned_linkage_and_row_corruptions_fail_closed(tmp_path, monkeypatch, mutation):
    spec, docs, _, _, _ = fixture(tmp_path, monkeypatch)
    if mutation == "receipt_cert": docs["bm_verification"]["price_basis_certified"] = True
    elif mutation == "raw_unverified": docs["raw_verification"]["raw_response_row_extract_verified"] = False
    elif mutation == "composite": docs["quote_verification"]["composite_quote_snapshot_sha256"] = "e" * 64
    elif mutation == "original_id": docs["quote_verification"]["original_quote_snapshot_sha256"] = "e" * 64
    elif mutation in ("state", "issues", "typed"):
        row = docs["quote_extension"]["holding_input_audit"]["rows"][0]
        if mutation == "state": row["state"] = "locked"
        elif mutation == "issues": row["issues"] = ["invented"]
        else: row["typed_values"][4] = ["integer", "100"]
    elif mutation == "raw_date": docs["raw_anchor_result"]["targets"][0]["returned_BAS_DD"] = "20210429"
    elif mutation == "raw_duplicate": docs["raw_anchor_result"]["targets"][1] = deepcopy(docs["raw_anchor_result"]["targets"][0])
    elif mutation == "raw_price": docs["raw_anchor_result"]["targets"][0]["original_raw_close"] = "201"
    elif mutation == "raw_vintage": docs["raw_anchor_result"]["targets"][0]["available_at"] = RECEIVED
    elif mutation == "identity": docs["raw_anchor_result"]["targets"][-1]["observation_date"] = "2021-05-27"
    elif mutation == "response": docs["raw_anchor_result"]["targets"][0]["response_sha256"] = "e" * 64
    store = refresh(spec, docs)
    if mutation == "receipt_pin":
        docs["bm_verification"]["file_sha256"]["holding-input-audit.json"] = "e" * 64
        store[Path(spec["inputs"]["bm_verification"]["path"])] = saved.encoded(docs["bm_verification"])
        spec["inputs"]["bm_verification"]["sha256"] = saved.digest(store[Path(spec["inputs"]["bm_verification"]["path"])])
    with pytest.raises(ValueError): module.evaluate_inputs(spec, saved.PinnedInputs(store.__getitem__), tmp_path)
    assert not (tmp_path / "basis-anchor-projection.json").exists()


@pytest.mark.parametrize("condition", ["absent", "frozen", "integer", "missing", "overlap"])
def test_real_adapter_states_and_request_boundaries_are_refused(tmp_path, monkeypatch, condition):
    day = CALENDAR[0]
    changes = None
    if condition == "absent": changes = {("003550", day): None}
    elif condition == "frozen": changes = {("003550", day): ("20210428", "100", "100", "100", "100", "0", "0")}
    elif condition == "integer": changes = {("003550", day): ("20210428", "100", "101", "99", 100, "100", "10")}
    spec, _, store, _, _ = fixture(tmp_path, monkeypatch, changes=changes, missing=condition == "missing", overlap=condition == "overlap")
    with pytest.raises(ValueError): module.evaluate_inputs(spec, saved.PinnedInputs(store.__getitem__), tmp_path)


@pytest.mark.parametrize("failure", [None, "pin", "reader", "drift", "uncommitted", "source_pin"])
def test_logged_start_precedes_seven_reads_and_failure_retains_private_evidence(tmp_path, monkeypatch, failure):
    spec, _, store, _, _ = fixture(tmp_path, monkeypatch)
    root, output, runs = SOURCE.parents[2], tmp_path / "output", tmp_path / "runs.jsonl"
    monkeypatch.chdir(root)
    sources = dict(files=[dict(path=module.SOURCE_PATH, sha256=saved.digest(SOURCE.read_bytes()))], code_version="a" * 40)
    if failure == "uncommitted": sources["files"] = []
    if failure == "source_pin": sources["files"][0]["sha256"] = "e" * 64
    if failure == "pin": spec["inputs"]["raw_anchor_result"]["sha256"] = "e" * 64
    freezes, reads = [], []
    def freeze(*a, **kw):
        module.validate_spec(spec, {"parameters": spec["parameters"]})
        freezes.append(1)
        return {**sources, "code_version": "b" * 40 if failure == "drift" and len(freezes) > 1 else "a" * 40}, saved.encoded(spec), spec
    def reader(path):
        assert [saved.strict_json(line)["status"] for line in runs.read_bytes().splitlines()] == ["started"]
        assert (output / "specification.json").exists() and (output / "source-manifest.json").exists()
        reads.append(path)
        if failure == "reader": raise OSError("private failure details")
        return store[path]
    monkeypatch.setattr(saved, "freeze_sources", freeze)
    if failure:
        with pytest.raises((ValueError, OSError)): module.run_preflight(root, root / "synthetic-spec.json", output, runs, reader=reader)
        if failure in ("uncommitted", "source_pin"):
            assert not runs.exists() and not output.exists() and not reads
            return
        failure_raw = (output / "failure.json").read_bytes()
        assert b"private failure details" not in failure_raw
        assert saved.strict_json(failure_raw)["partial_package_preserved"] is True
        assert (output / "resource-manifest.json").exists() and (output / "input-manifest.json").exists()
        assert not (output / "result.json").exists()
        if failure == "drift": assert (output / "basis-anchor-projection.json").exists()
    else:
        completed = module.run_preflight(root, root / "synthetic-spec.json", output, runs, reader=reader)
        assert len(reads) == 7 and completed["result"]["D1_completed_studies"] == completed["result"]["D2_completed_studies"] == 0
    assert [saved.strict_json(line)["status"] for line in runs.read_bytes().splitlines()] == ["started", "failed" if failure else "completed"]


@pytest.mark.parametrize("mutation", ["parameter_type", "role", "alias", "parent", "relative", "filename", "promotion"])
def test_spec_rejects_scope_or_parameter_changes(tmp_path, monkeypatch, mutation):
    spec, _, _, _, _ = fixture(tmp_path, monkeypatch)
    reference = {"parameters": deepcopy(spec["parameters"])}
    if mutation == "parameter_type": spec["parameters"]["threshold"] = 3
    elif mutation == "role": spec["inputs"]["scan_database"] = dict(path="/synthetic/db", sha256="a" * 64)
    elif mutation == "alias": spec["inputs"]["quote_verification"]["path"] = spec["inputs"]["bm_verification"]["path"]
    elif mutation == "parent": spec["inputs"]["bm_read_scope"]["path"] = "/different/read-scope.json"
    elif mutation == "relative": spec["inputs"]["raw_anchor_result"]["path"] = "result.json"
    elif mutation == "filename": spec["inputs"]["raw_anchor_result"]["path"] += ".wrong"
    elif mutation == "promotion": spec["promotion_allowed"] = True
    with pytest.raises(ValueError): module.validate_spec(spec, reference)


@pytest.mark.parametrize("failure", [False, True])
def test_cli_prints_completion_or_sanitized_failure_only(monkeypatch, capsys, failure):
    def run(*a, **kw):
        if failure: raise ValueError("private details")
        return {"result": dict(status="basis_anchor_inputs_audited", D1_completed_studies=0)}
    monkeypatch.setattr(module, "run_preflight", run)
    assert module.main(["--spec", "/synthetic/spec", "--output-dir", "/synthetic/output", "--runs-path", "/synthetic/runs"]) == int(failure)
    assert b"private details" not in capsys.readouterr().out.encode()
