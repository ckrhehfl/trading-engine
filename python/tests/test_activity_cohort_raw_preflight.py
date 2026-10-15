"""Registered BS raw diagnostics use only fabricated metadata and responses."""
from collections import Counter
from copy import deepcopy
from datetime import date
from io import BytesIO
from pathlib import Path
import socket
import sqlite3
import stat
from urllib.error import URLError

import pytest

from research import activity_cohort_raw_preflight as module
from research import activity_preflight as saved
from research import activity_raw_quote_view as view
from research import experiment_log
from research.activity_paired_replay import _json_value
from research.activity_quote_extension import extend_holding_quotes
from research.activity_raw_episode import RAW_FIELDS, extract_raw_episode_targets
import test_activity_cohort_raw_plan as geometry
import test_activity_cohort_comparison as synthetic
from test_activity_raw_episode_preflight import trade


RETRIEVED = "2026-10-10T00:00:00+00:00"
SECRET = "synthetic-request-credential"


@pytest.fixture(autouse=True)
def deny_actual_io(monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail("actual database/network/credential access")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(module.acquisition.probe, "credential", forbidden)
    monkeypatch.setattr(module.acquisition.probe, "make_opener", forbidden)


def source_rows(item, *, state=None):
    rows = [trade(item["bas_dd"], code) for code in item["targets"]]
    if state == "zero": rows[0].update(dict.fromkeys(RAW_FIELDS, "0"))
    elif state == "impossible": rows[0]["TDD_HGPRC"] = "1"
    elif state == "absent": rows.pop(0)
    return rows


def fixture(tmp_path, *, uncached=(), row_state=None, receipt_kind="raw", cash=False):
    values = geometry.case(formations=1, lg_only=True, cash=cash)
    calendar = values["calendar"]
    original = synthetic.quotes(calendar, scope={geometry.LG: calendar[61:-2]}, changes={(geometry.LG, calendar[80]): None})
    extension = synthetic.quotes(calendar, scope={geometry.LX: calendar[102:-2]}, scope_pin="9" * 64)
    base = extend_holding_quotes(original, extension)
    values["input_sha256"].update(activity_snapshot_sha256=base.activity_snapshot_sha256,
        original_quote_snapshot_sha256=original.quote_snapshot_sha256,
        original_read_scope_sha256=original.read_scope_sha256, base_quote_snapshot_sha256=base.quote_snapshot_sha256)
    initial = module.planning.build_cohort_raw_plan(**values)
    store, inventory, receipt = {}, [], []
    receipt_path = str(tmp_path / "prior" / "original-receipts.json")
    for number, item in enumerate(initial["acquisition_plan"]["request_matrix"], 1):
        if number in uncached: continue
        rows = source_rows(item, state=row_state if number == 20 else None)
        body = saved.encoded(dict(OutBlock_1=rows))
        path = str(tmp_path / "prior" / f"response-{number:04d}-{item['service']}-{item['bas_dd']}.json")
        pin = saved.digest(body)
        leaf = dict(number=number, service=item["service"], bas_dd=item["bas_dd"], read_status="retained", http_status=200,
            file=Path(path).name, bytes=len(body), rows=len(rows), sha256=pin, retrieved_at=RETRIEVED)
        if receipt_kind == "aq_ar":
            leaf = dict(**saved.formation.summarize_trading_day(rows, item["service"], item["bas_dd"]),
                response_sha256=pin, at=RETRIEVED, number=number, logical_number=number)
        elif receipt_kind == "bp":
            leaf = module.anchors.extract_targets(rows, {**item, "targets": [item["targets"][0]]}, RETRIEVED, pin)[0]
        receipt.append(leaf)
        inventory.append(dict(service=item["service"], bas_dd=item["bas_dd"], response_path=path, response_sha256=pin,
            receipt_path=receipt_path, receipt_sha256=None, receipt_coordinate=f"/{len(receipt) - 1}", retrieved_at=RETRIEVED))
        store[path] = body
    store[receipt_path] = saved.encoded(receipt)
    for row in inventory: row["receipt_sha256"] = saved.digest(store[receipt_path])
    values.update(prior_responses=tuple(inventory), prior_inventory_sha256=saved.digest(saved.encoded(inventory)))
    plans = module.planning.build_cohort_raw_plan(**values)
    metadata = dict(schema=module.METADATA_SCHEMA, declarations=_json_value(values["declarations"]),
        calendar_dates=[str(day) for day in calendar], raw_event_plan=list(values["raw_event_plan"]),
        input_sha256=values["input_sha256"], prior_responses=inventory, prior_inventory_sha256=values["prior_inventory_sha256"])
    metadata_path, lineage_path = str(tmp_path / "metadata" / "planning.json"), str(tmp_path / "base" / "composite-lineage.json")
    store[metadata_path], store[lineage_path] = saved.encoded(metadata), base.fingerprint_json
    plan, unit = plans["acquisition_plan"], plans["unit_plan"]
    spec = dict(schema=module.SCHEMA, study_id=module.SCHEMA, mode=module.MODE, promotion_allowed=False,
        window=deepcopy(saved.WINDOW), reference_specification=saved.REFERENCE, parameters=deepcopy(module.PARAMETERS),
        inputs=dict(planning_metadata=dict(path=metadata_path, sha256=saved.digest(store[metadata_path])),
            composite_lineage=dict(path=lineage_path, sha256=saved.digest(store[lineage_path])),
            **module.cache_input_declarations(plan["cached_response_candidates"])),
        request_contract=dict(**module.acquisition.HTTP_CONTRACT, request_count_ceiling=len(uncached)),
        registration=dict(acquisition_plan_sha256=saved.digest(saved.encoded(plan)),
            unit_plan_sha256=None if unit is None else saved.digest(saved.encoded(unit)), counts=plan["counts"],
            new_request_matrix=plan["uncached_request_candidates"]),
        resources=dict(address_space_bytes=768 * 1024**2, cpu_seconds=300, wall_seconds=900, nice=10))
    return spec, store, plans, base


def pinned(store, reads):
    def reader(path):
        reads.append(str(path))
        return store[str(path)]
    return saved.PinnedInputs(reader)


def repin_metadata(spec, store, mutate):
    row = spec["inputs"]["planning_metadata"]
    metadata = saved.strict_json(store[row["path"]]); mutate(metadata)
    store[row["path"]] = saved.encoded(metadata); row["sha256"] = saved.digest(store[row["path"]])


@pytest.mark.parametrize("receipt_kind", ["raw", "aq_ar", "bp"])
def test_all_cached_original_receipts_redecode_into_new_artifact_with_no_credential_or_http(tmp_path, receipt_kind):
    spec, store, plans, base = fixture(tmp_path, receipt_kind=receipt_kind)
    output = tmp_path / "out"; output.mkdir(mode=0o700)
    reads, before = [], deepcopy(store)
    result = module.evaluate_inputs(spec, pinned(store, reads), output)
    assert store == before and Counter(reads) == Counter(dict.fromkeys(reads, 1))
    assert result["new_source_requests"] == result["requests_attempted"] == 0
    assert result["api_access"] is result["credential_access"] is False
    assert result["cached_response_bodies_redecoded"] == plans["acquisition_plan"]["counts"]["query_days"]
    assert all(result[name] is False for name in module.FALSE_FLAGS)
    raw, unit = (output / "raw-episode-quotes.json").read_bytes(), (output / "unit-plan.json").read_bytes()
    restored = view.restore_raw_quote_view(base, raw, saved.digest(raw), unit, saved.digest(unit))
    assert restored.base is base and (geometry.LG, base.calendar[80]) in restored.base_absent_coordinates
    assert restored.source_artifact_json == view.encode_raw_artifact(base, restored.raw_observations, saved.digest(unit))
    for row in restored.raw_observations:
        candidate = next(item for item in plans["acquisition_plan"]["cached_response_candidates"] if item["bas_dd"] == row.requested_bas_dd)
        expected = extract_raw_episode_targets(saved.parse_envelope(store[candidate["response_path"]]),
            {key: candidate[key] for key in ("service", "bas_dd", "targets")}, RETRIEVED, candidate["response_sha256"])
        assert row == next(item for item in expected if item.code == row.code)
        assert row.retrieved_at == RETRIEVED
    assert restored.quote_snapshot_sha256 not in {base.quote_snapshot_sha256, saved.digest(raw), saved.digest(unit)}
    assert (output / "requests.jsonl").read_bytes() == b""


@pytest.mark.parametrize("state", ["zero", "absent", "impossible"])
def test_cached_zero_absence_and_impossible_prices_are_not_refetched_or_zero_filled(tmp_path, state):
    spec, store, plans, base = fixture(tmp_path, row_state=state)
    output = tmp_path / "out"; output.mkdir(mode=0o700)
    result = module.evaluate_inputs(spec, pinned(store, []), output)
    raw, unit = (output / "raw-episode-quotes.json").read_bytes(), (output / "unit-plan.json").read_bytes()
    restored = view.restore_raw_quote_view(base, raw, saved.digest(raw), unit, saved.digest(unit))
    item = plans["acquisition_plan"]["request_matrix"][19]
    key = geometry.LG, date.fromisoformat(item["bas_dd"])
    assert result["new_source_requests"] == 0
    assert (key in restored.no_trade_coordinates) == (state == "zero")
    assert (key in restored.absent_coordinates) == (state == "absent")
    assert (key in restored.unresolved_coordinates) == (state == "impossible")
    assert result["status"] == ("completed_with_unresolved" if state in ("absent", "impossible") else "completed")


def test_only_registered_uncached_delta_fetches_after_all_receipts_and_cached_bodies(tmp_path, monkeypatch):
    spec, store, plans, base = fixture(tmp_path, uncached=(1, 128))
    output = tmp_path / "out"; output.mkdir(mode=0o700)
    reads, calls, credentials, sleeps = [], [], [], []
    matrix = plans["acquisition_plan"]["uncached_request_candidates"]
    class Response(BytesIO):
        status = 200
        def __init__(self, body, url): super().__init__(body); self.url = url
        def geturl(self): return self.url
    class Opener:
        def open(self, request, *, timeout):
            assert len(reads) == len(spec["inputs"]) and timeout == 20
            assert (output / "unit-plan.json").exists() and (output / "cache-manifest.json").exists()
            assert request.get_header("Auth_key") == SECRET and request.get_method() == "GET"
            ledger = [saved.strict_json(row) for row in (output / "requests.jsonl").read_bytes().splitlines()]
            assert ledger[-1]["event"] == "attempt" and ledger[-1]["number"] == len(calls) + 1
            item = matrix[len(calls)]; calls.append(request.full_url)
            assert request.full_url == f"{module.acquisition.probe.HOST}/svc/apis/sto/stk_bydd_trd?basDd={item['bas_dd']}"
            return Response(saved.encoded(dict(OutBlock_1=source_rows(item))), request.full_url)
    def credential():
        assert len(reads) == len(spec["inputs"]) and (output / "request-manifest.json").exists()
        credentials.append(True); return SECRET
    monkeypatch.setattr(module.acquisition.probe, "credential", credential)
    monkeypatch.setattr(module.acquisition.probe, "make_opener", lambda **kwargs: Opener())
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    result = module.evaluate_inputs(spec, pinned(store, reads), output)
    assert len(calls) == 2 and credentials == [True] and sleeps == [1.1]
    assert result["new_source_requests"] == 2 and result["requests_unattempted"] == 0
    raw, unit = (output / "raw-episode-quotes.json").read_bytes(), (output / "unit-plan.json").read_bytes()
    view.restore_raw_quote_view(base, raw, saved.digest(raw), unit, saved.digest(unit))
    assert len((output / "requests.jsonl").read_bytes().splitlines()) == 4
    assert all(SECRET.encode() not in path.read_bytes() for path in output.iterdir())


@pytest.mark.parametrize("change", ["metadata_pin", "calendar", "declarations", "base", "original", "lineage_calendar",
    "lineage_fingerprint", "matrix", "counts", "plan_pin", "unit_pin", "cache_extra", "cache_omitted",
    "ceiling", "host", "retry", "cost", "resources", "resource_bool", "alias", "inventory_pin"])
def test_changed_or_incomplete_scope_is_rejected_before_any_cached_body_or_http(tmp_path, change):
    spec, store, plans, base = fixture(tmp_path, uncached=(128,))
    if change == "metadata_pin": spec["inputs"]["planning_metadata"]["sha256"] = "0" * 64
    elif change in ("calendar", "declarations", "base", "original", "inventory_pin"):
        def mutate(value):
            if change == "calendar": value["calendar_dates"].pop(80)
            elif change == "declarations": value["declarations"].pop()
            else: value["input_sha256" if change != "inventory_pin" else "prior_responses"]
            if change in ("base", "original"):
                value["input_sha256"]["base_quote_snapshot_sha256" if change == "base" else "original_quote_snapshot_sha256"] = "0" * 64
            elif change == "inventory_pin": value["prior_inventory_sha256"] = "0" * 64
        repin_metadata(spec, store, mutate)
    elif change.startswith("lineage_"):
        row = spec["inputs"]["composite_lineage"]; value = saved.strict_json(store[row["path"]])
        if change == "lineage_calendar": value["calendar_dates"].pop()
        else: value["extension"]["fingerprint_sha256"] = "0" * 64
        store[row["path"]] = saved.encoded(value)
        # Keep the registered composite identity: changing its bytes must fail.
    elif change == "matrix": spec["registration"]["new_request_matrix"] = []
    elif change == "counts": spec["registration"]["counts"]["raw_code_dates"] -= 1
    elif change == "plan_pin": spec["registration"]["acquisition_plan_sha256"] = "0" * 64
    elif change == "unit_pin": spec["registration"]["unit_plan_sha256"] = "0" * 64
    elif change == "cache_extra": spec["inputs"]["cache_response_9999"] = dict(path=str(tmp_path / "extra.json"), sha256="0" * 64)
    elif change == "cache_omitted": del spec["inputs"]["cache_response_0001"]
    elif change == "ceiling": spec["request_contract"]["request_count_ceiling"] = 0
    elif change == "host": spec["request_contract"]["host"] = "https://example.invalid"
    elif change == "retry": spec["request_contract"]["automatic_retries"] = 1
    elif change == "cost": spec["parameters"]["slippage_bps_per_side"] = 6.0
    elif change == "resources": del spec["resources"]["wall_seconds"]
    elif change == "resource_bool": spec["resources"]["cpu_seconds"] = True
    elif change == "alias": spec["inputs"]["cache_response_0001"] = deepcopy(spec["inputs"]["planning_metadata"])
    reads = []
    with pytest.raises(ValueError): module.prepare_inputs(spec, pinned(store, reads))
    assert all(not Path(path).name.startswith("response-") for path in reads)


@pytest.mark.parametrize("change", ["body_hash", "receipt_hash", "date", "service", "timestamp", "body_length", "row_length",
    "pointer", "receipt_form", "market", "malformed_body"])
def test_original_receipt_and_body_mismatch_fail_without_http_fallback(tmp_path, change):
    spec, store, plans, base = fixture(tmp_path, uncached=(128,))
    output = tmp_path / "out"; output.mkdir(mode=0o700)
    entry = plans["acquisition_plan"]["cached_response_candidates"][0]
    if change == "body_hash": store[entry["response_path"]] += b" "
    elif change == "receipt_hash": store[entry["receipt_path"]] += b" "
    else:
        receipt = saved.strict_json(store[entry["receipt_path"]]); row = receipt[0]
        if change == "date": row["bas_dd"] = "20200101"
        elif change == "service": row["service"] = "ksq_bydd_trd"
        elif change == "timestamp": row["retrieved_at"] = "2026-10-11T00:00:00+00:00"
        elif change == "body_length": row["bytes"] += 1
        elif change == "row_length": row["rows"] += 1
        elif change == "receipt_form": receipt[0] = dict(old_artifact_sha256="1" * 64)
        elif change in ("market", "malformed_body"):
            body = saved.strict_json(store[entry["response_path"]])
            if change == "market": body["OutBlock_1"][0]["MKT_NM"] = "KOSDAQ"
            else: body = {"wrong": []}
            store[entry["response_path"]] = saved.encoded(body)
            row.update(sha256=saved.digest(store[entry["response_path"]]), bytes=len(store[entry["response_path"]]))
        store[entry["receipt_path"]] = saved.encoded(receipt)
        def mutate(metadata):
            for previous in metadata["prior_responses"]:
                previous["receipt_sha256"] = saved.digest(store[entry["receipt_path"]])
                if previous["response_path"] == entry["response_path"]:
                    previous["response_sha256"] = row.get("sha256", previous["response_sha256"])
                    if change == "pointer": previous["receipt_coordinate"] = "/9999"
            metadata["prior_inventory_sha256"] = saved.digest(saved.encoded(metadata["prior_responses"]))
        repin_metadata(spec, store, mutate)
        # Re-register the changed metadata, leaving semantic mismatch checks to
        # the original-receipt/body boundary rather than a stale outer hash.
        metadata = saved.strict_json(store[spec["inputs"]["planning_metadata"]["path"]])
        changed = module.planning.build_cohort_raw_plan(module.cohort._declarations(metadata["declarations"]),
            tuple(map(date.fromisoformat, metadata["calendar_dates"])), tuple(metadata["raw_event_plan"]),
            parameters=spec["parameters"], input_sha256=metadata["input_sha256"],
            prior_responses=tuple(metadata["prior_responses"]), prior_inventory_sha256=metadata["prior_inventory_sha256"])
        spec["registration"]["acquisition_plan_sha256"] = saved.digest(saved.encoded(changed["acquisition_plan"]))
        spec["inputs"] = {key: value for key, value in spec["inputs"].items() if not key.startswith("cache_")}
        spec["inputs"].update(module.cache_input_declarations(changed["acquisition_plan"]["cached_response_candidates"]))
    with pytest.raises(ValueError): module.evaluate_inputs(spec, pinned(store, []), output)
    assert (output / "requests.jsonl").read_bytes() == b""


def logging_fixture(tmp_path, monkeypatch, **kwargs):
    spec, store, plans, base = fixture(tmp_path, **kwargs)
    root, private = tmp_path / "repo", tmp_path / "private"
    root.mkdir(); private.mkdir(mode=0o700)
    producer = root / module.SOURCE_PATH; producer.parent.mkdir(parents=True); producer.write_bytes(b"synthetic producer source")
    monkeypatch.setattr(module, "__file__", str(producer)); monkeypatch.chdir(root)
    output, runs, spec_path = private / "run", tmp_path / "runs.jsonl", root / "spec.json"
    sources = dict(code_version="a" * 40, files=[dict(path=module.SOURCE_PATH, sha256=saved.digest(producer.read_bytes()))])
    freezes, reads = [], []
    def freeze(*args, **kwargs):
        module.validate_spec(spec, {"parameters": module.PARAMETERS}); freezes.append(True)
        return deepcopy(sources), saved.encoded(spec), deepcopy(spec)
    def reader(path):
        assert [saved.strict_json(row)["status"] for row in runs.read_bytes().splitlines()] == ["started"]
        assert (output / "source-manifest.json").exists()
        if Path(path).name.startswith("response-"):
            assert (output / "acquisition-plan.json").exists() and (output / "unit-plan.json").exists()
        reads.append(str(path)); return store[str(path)]
    args = dict(root=root, spec_path=spec_path, output_dir=output, runs_path=runs, reader=reader, freeze=freeze)
    return args, spec, store, plans, base, freezes, reads


def test_durable_start_precedes_reads_and_final_source_freeze_precedes_completion(tmp_path, monkeypatch):
    args, spec, store, plans, base, freezes, reads = logging_fixture(tmp_path, monkeypatch)
    record = module.run_preflight(**args)
    output, runs = args["output_dir"], args["runs_path"]
    assert len(freezes) == 2 and len(reads) == len(spec["inputs"])
    assert [saved.strict_json(row)["status"] for row in runs.read_bytes().splitlines()] == ["started", "completed"]
    result = saved.strict_json((output / "result.json").read_bytes())
    assert record["result"]["result_sha256"] == saved.digest((output / "result.json").read_bytes())
    assert result["independent_output_verified"] is False
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())


def test_existing_output_and_log_input_alias_refuse_before_start_or_reads(tmp_path, monkeypatch):
    args, spec, store, plans, base, freezes, reads = logging_fixture(tmp_path, monkeypatch)
    args["output_dir"].mkdir(mode=0o700)
    with pytest.raises(ValueError): module.run_preflight(**args)
    assert not reads and not args["runs_path"].exists()
    args["output_dir"].rmdir()
    args["runs_path"] = Path(spec["inputs"]["planning_metadata"]["path"])
    with pytest.raises(ValueError): module.run_preflight(**args)
    assert not reads


@pytest.mark.parametrize("failure", ["input", "final_source", "body", "http"])
def test_failed_attempt_keeps_partial_outputs_and_zero_studies_without_retry(tmp_path, monkeypatch, failure):
    args, spec, store, plans, base, freezes, reads = logging_fixture(tmp_path, monkeypatch, uncached=(128,) if failure == "http" else ())
    if failure == "input":
        args["reader"] = lambda path: (_ for _ in ()).throw(MemoryError("private input failure"))
    elif failure == "final_source":
        original = args["freeze"]
        def freeze(*a, **kw):
            sources, raw, value = original(*a, **kw)
            if len(freezes) == 2: sources["code_version"] = "b" * 40
            return sources, raw, value
        args["freeze"] = freeze
    elif failure == "body":
        path = plans["acquisition_plan"]["cached_response_candidates"][2]["response_path"]
        store[path] += b" "
    else:
        class Opener:
            def open(self, request, **kwargs): raise URLError("private HTTP failure")
        monkeypatch.setattr(module.acquisition.probe, "credential", lambda: SECRET)
        monkeypatch.setattr(module.acquisition.probe, "make_opener", lambda **kwargs: Opener())
    with pytest.raises((MemoryError, ValueError, module.acquisition.probe.ProbeRefusal)):
        module.run_preflight(**args)
    output = args["output_dir"]
    assert [saved.strict_json(row)["status"] for row in args["runs_path"].read_bytes().splitlines()] == ["started", "failed"]
    for name in ("resource-manifest.json", "input-manifest.json", "new-response-manifest.json", "partial-raw-observations.json", "failure.json"):
        assert (output / name).exists()
    failed = saved.strict_json((output / "failure.json").read_bytes())
    assert failed["D1_completed_studies"] == failed["D2_completed_studies"] == failed["automatic_retries"] == 0
    assert all(b"private HTTP failure" not in path.read_bytes() and b"private input failure" not in path.read_bytes() for path in output.iterdir())
    if failure == "http":
        assert len((output / "requests.jsonl").read_bytes().splitlines()) == 2
        assert len(saved.strict_json((output / "new-response-manifest.json").read_bytes())) == 1


@pytest.mark.parametrize("failed_names", [{"resource-manifest.json"}, {"resource-manifest.json", "input-manifest.json", "failure.json", "failure-cleanup.json"}])
def test_compound_cleanup_failure_keeps_primary_and_attempts_all_artifacts(tmp_path, monkeypatch, failed_names):
    args, *_ = logging_fixture(tmp_path, monkeypatch)
    primary, attempted, original = MemoryError("private primary text"), [], saved.write_exclusive
    def execute(*a, **kw): raise primary
    def write(path, body):
        attempted.append(path.name)
        if path.name in failed_names: raise OSError("private cleanup text")
        return original(path, body)
    args["evaluator"] = execute; monkeypatch.setattr(saved, "write_exclusive", write)
    with pytest.raises(MemoryError) as caught: module.run_preflight(**args)
    assert caught.value is primary
    assert attempted[-6:] == ["resource-manifest.json", "input-manifest.json", "new-response-manifest.json",
        "partial-raw-observations.json", "failure.json", "failure-cleanup.json"]
    assert saved.strict_json(args["runs_path"].read_bytes().splitlines()[-1])["error_type"] == "MemoryError"


def test_failed_failed_record_append_does_not_replace_original_error(tmp_path, monkeypatch):
    args, *_ = logging_fixture(tmp_path, monkeypatch)
    primary, append = MemoryError("private primary"), experiment_log._append_record
    args["evaluator"] = lambda *a, **kw: (_ for _ in ()).throw(primary)
    def failing(record, path):
        if record["status"] == "failed": raise OSError("private append failure")
        return append(record, path)
    monkeypatch.setattr(experiment_log, "_append_record", failing)
    with pytest.raises(MemoryError) as caught: module.run_preflight(**args)
    assert caught.value is primary and len(args["runs_path"].read_bytes().splitlines()) == 1
    assert saved.strict_json((args["output_dir"] / "logging-failure.json").read_bytes())["primary_error_type"] == "MemoryError"


def test_empty_raw_scope_writes_no_invented_unit_or_artifact(tmp_path):
    spec, store, plans, base = fixture(tmp_path, cash=True)
    output = tmp_path / "out"; output.mkdir(mode=0o700)
    result = module.evaluate_inputs(spec, pinned(store, []), output)
    assert plans["unit_plan"] is None and result["summary"]["raw_code_dates"] == 0
    assert not (output / "unit-plan.json").exists() and not (output / "raw-episode-quotes.json").exists()
    assert result["raw_response_bodies_read"] is False


def test_cli_uses_only_registered_resource_limits_and_preserves_alarm_cleanup_primary(tmp_path, monkeypatch, capsys):
    spec, store, plans, base = fixture(tmp_path)
    spec_path = tmp_path / "spec.json"; spec_path.write_bytes(saved.encoded(spec))
    output = tmp_path / "out"; output.mkdir(mode=0o700)
    limits, alarms, primary = [], [], MemoryError("private primary")
    monkeypatch.setattr(module.resource, "setrlimit", lambda kind, pair: limits.append((kind, pair)))
    monkeypatch.setattr(module.signal, "signal", lambda *args: None)
    def alarm(value):
        alarms.append(value)
        if value == 0: raise OSError("private alarm cleanup")
    monkeypatch.setattr(module.signal, "alarm", alarm)
    monkeypatch.setattr(module.os, "getpriority", lambda *args: 0)
    monkeypatch.setattr(module.os, "nice", lambda value: value)
    monkeypatch.setattr(module, "run_preflight", lambda *a, **kw: (_ for _ in ()).throw(primary))
    assert module.main(["--spec", str(spec_path), "--output-dir", str(output), "--runs-path", str(tmp_path / "runs.jsonl")]) == 1
    assert limits == [(module.resource.RLIMIT_AS, (spec["resources"]["address_space_bytes"],) * 2),
        (module.resource.RLIMIT_CPU, (spec["resources"]["cpu_seconds"],) * 2)]
    assert alarms == [900, 0]
    assert saved.strict_json(capsys.readouterr().out.encode())["error_type"] == "MemoryError"
    assert saved.strict_json((output / "alarm-cleanup-failure.json").read_bytes())["primary_error_type"] == "MemoryError"
