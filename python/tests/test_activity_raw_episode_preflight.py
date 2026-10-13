"""Only fabricated receipts, cached envelopes, credentials and HTTP responses."""
from copy import deepcopy
from datetime import date
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import stat
from urllib.error import HTTPError, URLError

import pytest

from research import activity_raw_episode_preflight as module
from research import activity_preflight as saved
from test_activity_price_parity import receipt_fixture, pinned_receipts
from test_krx_formation_audit import rows_for_day

SECRET = "synthetic-environment-credential-only"
RETRIEVED = "2026-10-10T00:00:00+00:00"


def trade(day, code="003550", **changes):
    row = rows_for_day(day)["stk_bydd_trd"][0]
    row.update(ISU_CD=code, TDD_OPNPRC="100.0000", TDD_HGPRC="102", TDD_LWPRC="99",
        TDD_CLSPRC="101.2500000000000000001", ACC_TRDVOL="3", ACC_TRDVAL="300")
    row.update(changes)
    return row


def statuses(path):
    return [saved.strict_json(row)["status"] for row in path.read_bytes().splitlines()]


def fixture(tmp_path, monkeypatch):
    root, private = tmp_path / "repo", tmp_path / "private"
    root.mkdir(); private.mkdir(mode=0o700)
    output, runs = private / "run", tmp_path / "runs.jsonl"
    values, history = receipt_fixture()
    excluded = {"20210428", "20210527", "20211021", "20211022"}
    days = sorted((set(history["history_windows"]["synthetic"]) - excluded) | {
        "20260102", "20260103", "20260104", "20260105"})
    matrix = [dict(service=service, bas_dd=day) for day in days for service in saved.probe.SERVICES[:2]]
    for role, offset, selected in (("ar_original", 0, matrix[:549]), ("ar_recovery", 549, matrix[549:])):
        report, started, ledger = values[role]
        report["observations"] = [dict(service=item["service"], requested_bas_dd=item["bas_dd"], schema_valid=True,
            response_sha256="a" * 64, at=RETRIEVED,
            **(dict(logical_number=offset + index + 1, number=index + 1) if offset else {})) for index, item in enumerate(selected)]
        started["request_matrix"] = matrix
        if offset: started["new_request_matrix"] = selected
        ledger[:] = [dict(**item, number=index + 1, event=event,
            **(dict(logical_number=offset + index + 1) if offset else {}),
            **(dict(http_status=200) if event == "response" else {}))
            for index, item in enumerate(selected) for event in ("attempt", "response")]
        if not offset:
            ledger.extend([dict(**matrix[549], number=550, event="attempt"),
                dict(**matrix[549], number=550, event="response", http_status=None)])
    geometry = dict(history_windows={"synthetic": days}, aq_matrix=values["aq"][1]["request_matrix"], ar_matrix=matrix)
    request_matrix = [dict(service="stk_bydd_trd", bas_dd=day, targets=["003550"] if day < "20210527" else ["003550", "383800"])
        for day in ("20210416", "20210428", "20210527", "20211020", "20211021", "20211022")]
    bp_bodies = {day: saved.encoded({"OutBlock_1": [trade(day, code) for code in item["targets"]]})
        for item in request_matrix if (day := item["bas_dd"]) in {"20210428", "20210527"}}
    bp_pins = {day: (index, saved.digest(bp_bodies[day])) for index, day in enumerate(sorted(bp_bodies), 1)}
    monkeypatch.setattr(module.planning, "BP_RESPONSES", bp_pins)
    cached = [{**module.planning.cached_request(item["bas_dd"], geometry), "targets": item["targets"]}
        for item in request_matrix[:4]]
    bodies = {}
    for item in cached:
        if item["role"] == "bp_raw": continue
        day, role, logical = item["bas_dd"], item["role"], item["logical_number"]
        rows = [trade(day, code) for code in item["targets"]]
        body = saved.encoded({"OutBlock_1": rows})
        summarize = saved.formation._summarize if role == "aq" else saved.formation.summarize_trading_day
        observation = dict(**summarize(rows, "stk_bydd_trd", day), response_sha256=saved.digest(body), at=RETRIEVED)
        if role == "ar_recovery": observation.update(number=logical - 549, logical_number=logical)
        values[role][0]["observations"][logical - 1 if role != "ar_recovery" else logical - 550] = observation
        bodies[role, item["filename"]] = body
    store, declared = pinned_receipts(tmp_path, values)
    for (role, filename), body in bodies.items(): store[Path(declared[role]["root"]) / filename] = body
    bp_root = tmp_path / "bp"
    previous = []
    for item in request_matrix[1:3]:
        day = item["bas_dd"]
        previous.extend(module.anchors.extract_targets(saved.parse_envelope(bp_bodies[day]), item, RETRIEVED, bp_pins[day][1]))
        store[bp_root / f"response-{bp_pins[day][0]:02d}-stk_bydd_trd-{day}.json"] = bp_bodies[day]
    bp = dict(schema=module.anchors.SCHEMA, status="completed", targets=previous + [{"kind": "issue_identity"}],
        requests_attempted=3, automatic_retries=0, database_access=False, api_access=True,
        D1_completed_studies=0, D2_completed_studies=0, **dict.fromkeys(module.anchors.FALSE_FLAGS, False))
    store[bp_root / "result.json"] = saved.encoded(bp)
    review_path = tmp_path / "review" / "content.json"
    store[review_path] = saved.encoded({"synthetic_price_free_metadata": True})
    declared.update(bp_raw=dict(root=str(bp_root), result_sha256=saved.digest(store[bp_root / "result.json"])),
        scope_review=dict(path=str(review_path), sha256=saved.digest(store[review_path])))
    monkeypatch.setattr(module, "INPUTS", deepcopy(declared))
    requested = {code: [date.fromisoformat(item["bas_dd"]).isoformat() for item in request_matrix if code in item["targets"]]
        for code in ("003550", "383800")}
    calendar = sorted({day for values_ in requested.values() for day in values_})
    unit = dict(schema=module.view.PLAN_SCHEMA, activity_snapshot_sha256="a" * 64, base_quote_snapshot_sha256="b" * 64,
        calendar_sha256=module.view.calendar_sha256(tuple(map(date.fromisoformat, calendar))),
        episodes=[dict(code=code, start=start, end=end) for code, start, end in module.planning.EPISODES], **module.view.UNITS)
    counts = dict.fromkeys(module.COUNT_FIELDS, 0)
    counts.update(calendar_sessions=6, potential_origins=2, raw_unique_days=6, raw_code_dates=10, cached_responses=4,
        new_requests=2, cached_code_dates=6, new_code_dates=4, cache_geometry_unique_days=854,
        aq_responses=1, ar_recovery_responses=1, bp_raw_responses=2)
    plan = dict(schema=module.planning.SCHEMA, counts=counts, cache_geometry=geometry, cached_requests=cached,
        requested_code_dates=requested, new_request_matrix=request_matrix[4:])
    plans = dict(unit_plan=unit, acquisition_plan=plan)
    def builder(metadata, *, scope_review_sha256):
        assert metadata == {"synthetic_price_free_metadata": True} and scope_review_sha256 == declared["scope_review"]["sha256"]
        return deepcopy(plans)
    monkeypatch.setattr(module.planning, "build_raw_episode_plan", builder)
    spec = dict(schema=module.SCHEMA, study_id=module.SCHEMA, mode="discovery_raw_episode_acquisition", promotion_allowed=False,
        window=deepcopy(saved.WINDOW), reference_specification=saved.REFERENCE, parameters={"lookback": 60}, inputs=deepcopy(declared),
        request_contract=deepcopy(module.HTTP_CONTRACT), registration=dict(acquisition_plan_sha256=saved.digest(saved.encoded(plan)),
            unit_plan_sha256=saved.digest(saved.encoded(unit)), counts=deepcopy(counts), new_request_matrix=deepcopy(request_matrix[4:])))
    monkeypatch.setattr(module, "PLAN_COUNTS", deepcopy(counts))
    monkeypatch.setattr(module, "PLAN_PINS", {key: spec["registration"][key] for key in ("acquisition_plan_sha256", "unit_plan_sha256")})
    producer = root / "python/research/activity_raw_episode_preflight.py"
    producer.parent.mkdir(parents=True); producer.write_bytes(b"synthetic frozen producer")
    sources = dict(code_version="a" * 40, files=[dict(path=producer.relative_to(root).as_posix(), sha256=saved.digest(producer.read_bytes()))])
    freeze_calls = []
    def freeze(*args, **kwargs):
        assert kwargs["spec_validator"] is module.validate_spec
        module.validate_spec(spec, {"parameters": {"lookback": 60}})
        freeze_calls.append(True)
        return deepcopy(sources), saved.encoded(spec), deepcopy(spec)
    reads, calls, sleeps = [], [], []
    responses = [saved.encoded({"OutBlock_1": [trade(item["bas_dd"], code) for code in item["targets"]]}) for item in request_matrix[4:]]
    def reader(path):
        assert statuses(runs) == ["started"] and (output / "source-manifest.json").exists()
        if path.name.startswith("response-"):
            assert len(reads) >= 11 and (output / "cache-manifest.json").exists() and (output / "unit-plan.json").exists()
            assert "ksq_" not in str(path) and "base_info" not in str(path)
        reads.append(path)
        return store[path]
    class Response(BytesIO):
        status = 200
        def __init__(self, body, url): super().__init__(body); self.url = url
        def geturl(self): return self.url
        def read(self, limit): assert limit == 8388609; return super().read(limit)
    class Opener:
        def open(self, request, *, timeout):
            assert statuses(runs) == ["started"] and len(reads) == 15
            assert timeout == 20 and request.get_method() == "GET" and request.get_header("Auth_key") == SECRET
            ledger = [saved.strict_json(line) for line in (output / "requests.jsonl").read_bytes().splitlines()]
            assert ledger[-1]["event"] == "attempt" and ledger[-1]["number"] == len(calls) + 1
            calls.append(request.full_url)
            body = responses[len(calls) - 1]
            if isinstance(body, Exception): raise body
            return Response(body, request.full_url)
    def credential():
        assert statuses(runs) == ["started"] and len(reads) == 15 and (output / "request-manifest.json").exists()
        return SECRET
    def opener(**kwargs): assert kwargs == {"header_case_check": True}; return Opener()
    monkeypatch.chdir(root)
    monkeypatch.setattr(module, "__file__", str(producer))
    monkeypatch.setattr(saved, "freeze_sources", freeze)
    monkeypatch.setattr(probe := module.probe, "credential", credential)
    monkeypatch.setattr(probe, "make_opener", opener)
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    return SimpleNamespace(root=root, output=output, runs=runs, spec=spec, sources=sources, store=store, plans=plans,
        reader=reader, reads=reads, calls=calls, sleeps=sleeps, responses=responses, freeze_calls=freeze_calls)


def run(f):
    return module.run_preflight(f.root, f.root / "spec.json", f.output, f.runs, reader=f.reader)


def test_production_registration_is_fixed_metadata_only_without_database_role():
    assert set(module.INPUTS) == {"scope_review", "aq", "ar_original", "ar_recovery", "bp_raw"}
    for role in ("aq", "ar_original", "ar_recovery"):
        assert module.INPUTS[role] == module.parity.INPUTS[role]
    assert module.PLAN_COUNTS == dict(calendar_sessions=1898, potential_origins=2, raw_unique_days=128,
        raw_code_dates=229, cached_responses=63, new_requests=65, cached_code_dates=125, new_code_dates=104,
        cache_geometry_unique_days=854, aq_responses=1, ar_original_responses=0, ar_recovery_responses=60, bp_raw_responses=2)
    assert module.PLAN_PINS == dict(acquisition_plan_sha256="cfe9c20a31539947eb8a396932f3c7ffb6d44ad848c7fbc60245da918e0792f0",
        unit_plan_sha256="4acf0dbb395bcda7163effa9fc6ffacc0fe8c94223e3c1d3767a4f67a72669e8")


def test_logged_cache_and_two_new_requests_exact_artifact_no_secret_or_certificate(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch); receipt = run(f)
    result = saved.strict_json((f.output / "result.json").read_bytes())
    artifact = saved.strict_json((f.output / "raw-episode-quotes.json").read_bytes())
    assert statuses(f.runs) == ["started", "completed"] and len(f.freeze_calls) == 2
    assert f.calls == [module.probe.HOST + "/svc/apis/sto/stk_bydd_trd?basDd=" + day for day in ("20211021", "20211022")]
    assert f.sleeps == [1.1] and len(f.reads) == 15
    assert len([path for path in f.output.iterdir() if path.name.startswith("response-")]) == 2
    assert result["summary"]["observed_rows"] == len(artifact["rows"]) == 10
    assert result["summary"]["unresolved_rows"] == 0 and result["summary"]["cached_responses"] == 4
    assert all(result[key] is False for key in module.FALSE_FLAGS)
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 0
    assert artifact["rows"][0]["close"] == "101.2500000000000000001"
    assert artifact["rows"][0]["original_texts"][0] == ["TDD_OPNPRC", "100.0000"]
    assert {row["requested_bas_dd"] for row in artifact["rows"]} == {"20210416", "20210428", "20210527", "20211020", "20211021", "20211022"}
    assert artifact["activity_snapshot_sha256"] == "a" * 64 and artifact["base_quote_snapshot_sha256"] == "b" * 64
    assert artifact["unit_plan_sha256"] == saved.digest((f.output / "unit-plan.json").read_bytes())
    for key, name in (("raw_episode_quotes_sha256", "raw-episode-quotes.json"), ("request_ledger_sha256", "requests.jsonl"),
            ("cache_manifest_sha256", "cache-manifest.json"), ("input_manifest_sha256", "input-manifest.json")):
        assert result[key] == saved.digest((f.output / name).read_bytes())
    assert receipt["result"]["result_sha256"] == saved.digest((f.output / "result.json").read_bytes())
    assert stat.S_IMODE(f.output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in f.output.iterdir())
    assert SECRET.encode() not in b"".join(path.read_bytes() for path in f.output.iterdir()) + f.runs.read_bytes()


@pytest.mark.parametrize("state", ["absent", "frozen", "locked"])
def test_cached_raw_states_are_not_filled_or_refetched(tmp_path, monkeypatch, state):
    f = fixture(tmp_path, monkeypatch)
    entry = next(item for item in f.plans["acquisition_plan"]["cached_requests"] if item["role"] == "ar_recovery")
    root = Path(f.spec["inputs"]["ar_recovery"]["root"])
    rows = [trade(entry["bas_dd"], "111111" if state == "absent" else "003550")]
    if state != "absent":
        rows[0].update(TDD_OPNPRC="100", TDD_HGPRC="100", TDD_LWPRC="100", TDD_CLSPRC="100",
            ACC_TRDVOL="0" if state == "frozen" else "3", ACC_TRDVAL="0" if state == "frozen" else "300")
    body = saved.encoded({"OutBlock_1": rows}); f.store[root / entry["filename"]] = body
    report = saved.strict_json(f.store[root / "report.json"])
    observation = dict(**saved.formation.summarize_trading_day(rows, entry["service"], entry["bas_dd"]),
        response_sha256=saved.digest(body), at=RETRIEVED, number=entry["number"], logical_number=entry["logical_number"])
    assert observation["schema_valid"] is True
    report["observations"][entry["number"] - 1] = observation
    f.store[root / "report.json"] = saved.encoded(report)
    pin = saved.digest(f.store[root / "report.json"])
    f.spec["inputs"]["ar_recovery"]["report_sha256"] = pin
    monkeypatch.setitem(module.INPUTS["ar_recovery"], "report_sha256", pin)
    run(f)
    artifact = saved.strict_json((f.output / "raw-episode-quotes.json").read_bytes())
    row = next(row for row in artifact["rows"] if row["code"] == "003550" and row["requested_bas_dd"] == entry["bas_dd"])
    assert len(f.calls) == 2 and all(entry["bas_dd"] not in url for url in f.calls)
    if state == "absent":
        assert row["status"] == "unresolved" and row["issues"] == ["target_absent_cause_unknown"] and row["close"] is None
    else:
        assert row["state"] == state and row["status"] == "observed" and row["close"] == "100"


@pytest.mark.parametrize("foreign", ["KOSDAQ", "KONEX", "", None, 1])
def test_explicit_foreign_target_market_refused_without_positive_price_requirement(foreign):
    item = dict(service="stk_bydd_trd", bas_dd="20211021", targets=["003550"])
    row = trade(item["bas_dd"])
    for field in module.parity.RAW_FIELDS: row[field] = "0"
    module._check_target_market([row], item)
    row.pop("MKT_NM")
    module._check_target_market([row], item)  # Missing market is compatible, never certified.
    module._check_target_market([row, trade(item["bas_dd"], "999999", MKT_NM="KOSDAQ")], item)
    with pytest.raises(ValueError, match="target market"):
        module._check_target_market([row, {**row, "MKT_NM": foreign}], item)


@pytest.mark.parametrize("source", ["new", "BP", "cached"])
def test_logged_market_guard_applies_to_each_acquisition_path(tmp_path, monkeypatch, source):
    f = fixture(tmp_path, monkeypatch)
    if source == "new":
        rows = saved.parse_envelope(f.responses[0]); rows[0]["MKT_NM"] = "KOSDAQ"
        f.responses[0] = saved.encoded({"OutBlock_1": rows})
    elif source == "cached":
        read_rows = saved._rows
        def foreign_target(*args, **kwargs):
            rows = read_rows(*args, **kwargs)
            rows[0]["MKT_NM"] = "KOSDAQ"
            return rows
        # Exercise the producer's target guard even if a cached reader returned
        # contradictory rows after its own receipt/whole-envelope checks.
        monkeypatch.setattr(saved, "_rows", foreign_target)
    else:
        entry = next(row for row in f.plans["acquisition_plan"]["cached_requests"] if row["role"] == "bp_raw")
        root = Path(f.spec["inputs"]["bp_raw"]["root"])
        body_path = root / entry["filename"]
        rows = saved.parse_envelope(f.store[body_path]); rows[0]["MKT_NM"] = "KOSDAQ"
        f.store[body_path] = saved.encoded({"OutBlock_1": rows})
        pin = saved.digest(f.store[body_path]); entry["response_sha256"] = pin
        monkeypatch.setitem(module.planning.BP_RESPONSES, entry["bas_dd"], (entry["number"], pin))
        prior = saved.strict_json(f.store[root / "result.json"])
        for row in prior["targets"]:
            if row.get("kind") == "raw_close" and row["requested_bas_dd"] == entry["bas_dd"]:
                row.update(response_sha256=pin, data_vintage="current_retrieval_sha256:" + pin)
        f.store[root / "result.json"] = saved.encoded(prior)
        result_pin = saved.digest(f.store[root / "result.json"])
        f.spec["inputs"]["bp_raw"]["result_sha256"] = result_pin
        monkeypatch.setitem(module.INPUTS["bp_raw"], "result_sha256", result_pin)
        plan_pin = saved.digest(saved.encoded(f.plans["acquisition_plan"]))
        f.spec["registration"]["acquisition_plan_sha256"] = plan_pin
        monkeypatch.setitem(module.PLAN_PINS, "acquisition_plan_sha256", plan_pin)
    with pytest.raises(module.probe.ProbeRefusal, match="raw_episode_attempt_failed"): run(f)
    assert statuses(f.runs) == ["started", "failed"] and not (f.output / "result.json").exists()
    assert len(f.calls) == (1 if source == "new" else 0)
    failure = saved.strict_json((f.output / "failure.json").read_bytes())
    assert failure["error_type"] == "ValueError" and failure["automatic_retries"] == 0


def test_missing_new_target_market_retains_decoder_compatibility_without_certificate(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    rows = saved.parse_envelope(f.responses[0])
    for row in rows: row.pop("MKT_NM")
    f.responses[0] = saved.encoded({"OutBlock_1": rows}); run(f)
    result = saved.strict_json((f.output / "result.json").read_bytes())
    assert result["summary"]["observed_rows"] == 10 and len(f.calls) == 2
    assert all(result[key] is False for key in module.FALSE_FLAGS)


@pytest.mark.parametrize("change", ["receipt_pin", "receipt_geometry", "body_pin", "BP_body", "registration", "unit", "metadata", "BP_result"])
def test_bad_linkage_refuses_before_credential_or_new_request(tmp_path, monkeypatch, change):
    f = fixture(tmp_path, monkeypatch)
    if change == "receipt_pin": f.store[next(path for path in f.store if path.name == "requests.jsonl")] = b"{}\n"
    elif change == "receipt_geometry":
        f.plans["acquisition_plan"]["cache_geometry"]["aq_matrix"] = []
        f.spec["registration"]["acquisition_plan_sha256"] = saved.digest(saved.encoded(f.plans["acquisition_plan"]))
        monkeypatch.setitem(module.PLAN_PINS, "acquisition_plan_sha256", f.spec["registration"]["acquisition_plan_sha256"])
    elif change in {"body_pin", "BP_body"}:
        path = next(path for path in f.store if path.name.startswith("response-") and (path.parent.name == "bp") == (change == "BP_body"))
        f.store[path] += b" "
    elif change == "registration":
        f.spec["registration"]["acquisition_plan_sha256"] = "f" * 64
        monkeypatch.setitem(module.PLAN_PINS, "acquisition_plan_sha256", "f" * 64)
    elif change == "unit": f.plans["unit_plan"]["base_quote_snapshot_sha256"] = "f" * 64
    elif change == "metadata": f.store[Path(f.spec["inputs"]["scope_review"]["path"])] = b"{}"
    else:
        path = Path(f.spec["inputs"]["bp_raw"]["root"]) / "result.json"
        f.store[path] = b"{}"
    monkeypatch.setattr(module.probe, "credential", lambda: pytest.fail("credential before validated cache"))
    with pytest.raises(module.probe.ProbeRefusal, match="raw_episode_attempt_failed"): run(f)
    assert statuses(f.runs) == ["started", "failed"] and not f.calls and not (f.output / "result.json").exists()
    if change in {"receipt_pin", "receipt_geometry", "registration", "unit", "metadata", "BP_result"}:
        assert not any(path.name.startswith("response-") for path in f.reads)
    assert (f.output / "resource-manifest.json").exists() and (f.output / "failure.json").exists()


@pytest.mark.parametrize("change", ["absent", "duplicate", "zero", "bad_date"])
def test_new_rows_preserve_unknown_zero_and_ambiguous_states_without_refetch(tmp_path, monkeypatch, change):
    f = fixture(tmp_path, monkeypatch)
    rows = saved.parse_envelope(f.responses[0])
    if change == "absent": rows.pop(0)
    elif change == "duplicate": rows.append(deepcopy(rows[0]))
    elif change == "bad_date": rows[0]["BAS_DD"] = "20211020"
    else:
        for field in module.parity.RAW_FIELDS: rows[0][field] = "0"
    f.responses[0] = saved.encoded({"OutBlock_1": rows}); run(f)
    result = saved.strict_json((f.output / "result.json").read_bytes())
    artifact = saved.strict_json((f.output / "raw-episode-quotes.json").read_bytes())
    row = next(row for row in artifact["rows"] if row["code"] == "003550" and row["requested_bas_dd"] == "20211021")
    assert len(f.calls) == 2 and result["new_source_requests"] == 2 and row["close"] is None
    if change == "zero":
        assert row["status"] == "observed" and row["state"] == "observed_no_trade_zero_prices"
        assert row["volume"] == row["turnover"] == "0" and result["summary"]["no_trade_zero_price_rows"] == 1
    else:
        assert row["status"] == row["state"] == "unresolved" and result["status"] == "completed_with_unresolved"
        assert row["issues"] and row["observation_date"] is None


@pytest.mark.parametrize("failure", ["http", "redirect", "transport", "echo", "escaped_echo", "json", "size", "drift", "credential"])
def test_partial_failure_keeps_private_progress_no_retry_or_exception_text(tmp_path, monkeypatch, failure):
    f = fixture(tmp_path, monkeypatch)
    if failure in {"http", "redirect"}:
        f.responses[1] = HTTPError("https://secret.invalid", 403 if failure == "http" else 302, SECRET, {"AUTH_KEY": SECRET}, BytesIO(SECRET.encode()))
    elif failure == "transport": f.responses[1] = URLError("sensitive exception " + SECRET)
    elif failure == "echo": f.responses[1] = saved.encoded({"OutBlock_1": [{"name": SECRET}]})
    elif failure == "escaped_echo": f.responses[1] = ('{"OutBlock_1":[{"name":"' + "".join("\\u%04x" % ord(c) for c in SECRET) + '"}]}').encode()
    elif failure == "json": f.responses[1] = b"not JSON"
    elif failure == "size": f.responses[1] = b"x" * 8388609
    elif failure == "credential": monkeypatch.setattr(module.probe, "credential", lambda: (_ for _ in ()).throw(ValueError(SECRET)))
    else:
        freeze = saved.freeze_sources
        def drift(*args, **kwargs):
            sources, raw, spec = freeze(*args, **kwargs)
            if len(f.freeze_calls) > 1: sources["code_version"] = "b" * 40
            return sources, raw, spec
        monkeypatch.setattr(saved, "freeze_sources", drift)
    with pytest.raises(module.probe.ProbeRefusal, match="raw_episode_attempt_failed"): run(f)
    assert statuses(f.runs) == ["started", "failed"] and not (f.output / "result.json").exists()
    failure_doc = saved.strict_json((f.output / "failure.json").read_bytes())
    expected = 0 if failure == "credential" else 2
    assert len(f.calls) == failure_doc["requests_attempted"] == expected and failure_doc["automatic_retries"] == 0
    assert len(failure_doc["partial_rows"]) == (10 if failure == "drift" else 6 if failure == "credential" else 8)
    assert SECRET.encode() not in b"".join(path.read_bytes() for path in f.output.iterdir()) + f.runs.read_bytes()
    assert b"sensitive exception" not in (f.output / "failure.json").read_bytes()


@pytest.mark.parametrize("change", ["host", "service", "timeout", "proxy", "retry", "input", "parameters", "count", "count_type", "date", "target", "extra"])
def test_spec_has_no_arbitrary_transport_range_input_or_parameter_surface(tmp_path, monkeypatch, change):
    f = fixture(tmp_path, monkeypatch); spec = f.spec
    if change in {"host", "service", "timeout", "proxy", "retry"}:
        key = dict(timeout="timeout_seconds", proxy="proxies_allowed", retry="automatic_retries").get(change, change)
        spec["request_contract"][key] = "invalid"
    elif change == "input": spec["inputs"]["database"] = dict(path="/unused", sha256="a" * 64)
    elif change == "parameters": spec["parameters"]["lookback"] = 61
    elif change == "count": spec["registration"]["counts"]["new_requests"] = 0
    elif change == "count_type": spec["registration"]["counts"]["potential_origins"] = True
    elif change == "date": spec["registration"]["new_request_matrix"][0]["bas_dd"] = "20180102"
    elif change == "target": spec["registration"]["new_request_matrix"][0]["targets"] = ["999999"]
    else: spec["unregistered"] = True
    with pytest.raises(ValueError): module.validate_spec(spec, {"parameters": {"lookback": 60}})
    assert not f.output.exists() and not f.runs.exists()


@pytest.mark.parametrize("change", ["missing_source", "changed_source", "source_log", "input_output", "checkout_output"])
def test_source_and_write_boundary_guards_precede_log_and_financial_reads(tmp_path, monkeypatch, change):
    f = fixture(tmp_path, monkeypatch)
    if change == "missing_source": f.sources["files"] = []
    elif change == "changed_source": Path(module.__file__).write_bytes(b"not frozen")
    elif change == "source_log": f.runs = Path(module.__file__)
    elif change == "input_output": f.output = Path(f.spec["inputs"]["aq"]["root"]) / "output"
    else: f.output = f.root / "output"
    monkeypatch.setattr(module.probe, "credential", lambda: pytest.fail("early credential"))
    with pytest.raises(ValueError): run(f)
    assert not f.reads and not f.calls and not f.output.exists()


def test_existing_output_is_preserved_and_cli_never_echoes_errors(tmp_path, monkeypatch, capsys):
    f = fixture(tmp_path, monkeypatch); f.output.mkdir(mode=0o700)
    (f.output / "sentinel").write_bytes(b"keep")
    with pytest.raises(module.probe.ProbeRefusal): run(f)
    assert not f.reads and not f.calls and list(f.output.iterdir()) == [f.output / "sentinel"]
    monkeypatch.setattr(module, "run_preflight", lambda *a, **kw: (_ for _ in ()).throw(ValueError(SECRET)))
    assert module.main(["--spec", "/unused", "--output-dir", "/unused", "--runs-path", "/unused"]) == 1
    assert SECRET not in capsys.readouterr().out
