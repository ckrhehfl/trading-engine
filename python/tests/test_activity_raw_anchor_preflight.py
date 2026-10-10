"""Synthetic requests only; no real credential, exchange, DB or GCP access."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import stat
from urllib.error import HTTPError, URLError

import pytest

from research import activity_preflight as saved
from research import activity_raw_anchor_preflight as module


SECRET = "synthetic-credential-not-for-an-exchange"


def payloads():
    return [
        {"OutBlock_1": [{"ISU_CD": "003550", "BAS_DD": "20210428", "TDD_CLSPRC": "1,234.5000"}]},
        {"OutBlock_1": [
            {"ISU_CD": "003550", "BAS_DD": "20210527", "TDD_CLSPRC": "9007199254740993.0000000000000001"},
            {"ISU_CD": "383800", "BAS_DD": "20210527", "TDD_CLSPRC": "123.45"}]},
        {"OutBlock_1": [{"ISU_SRT_CD": "383800", "ISU_CD": "KR7383800000", "LIST_DD": "20210527"}]},
    ]


def statuses(path):
    return [saved.strict_json(row)["status"] for row in path.read_bytes().splitlines()]


def fixture(tmp_path, monkeypatch, bodies=None):
    root, private = tmp_path / "repo", tmp_path / "private"
    root.mkdir()
    private.mkdir(mode=0o700)
    output, runs = private / "run", tmp_path / "runs.jsonl"
    actual_root = Path(__file__).resolve().parents[2]
    spec = saved.strict_json((actual_root / "configs/research/discovery/activity-raw-anchor-preflight-v1.json").read_bytes())
    reference = saved.strict_json((actual_root / saved.REFERENCE).read_bytes())
    source_path = "python/research/activity_raw_anchor_preflight.py"
    producer = root / source_path
    producer.parent.mkdir(parents=True)
    producer.write_bytes(b"synthetic frozen producer source")
    sources = {"files": [{"path": "python/synthetic.py", "sha256": "a" * 64},
                          {"path": source_path, "sha256": saved.digest(producer.read_bytes())}],
               "code_version": "a" * 40}

    def freeze(*args, **kwargs):
        assert kwargs["spec_validator"] is module.validate_spec
        module.validate_spec(spec, reference)
        return deepcopy(sources), saved.encoded(spec), deepcopy(spec)

    calls, sleeps = [], []
    values = [saved.encoded(body) for body in payloads()] if bodies is None else bodies

    class Response:
        status = 200

        def __init__(self, url, body):
            self.url, self.body = url, body

        def __enter__(self): return self
        def __exit__(self, *args): return False
        def geturl(self): return self.url

        def read(self, limit):
            assert limit == 8388609
            return self.body

    class Opener:
        def open(self, request, *, timeout):
            assert statuses(runs) == ["started"]
            assert (output / "source-manifest.json").exists()
            assert (output / "specification.json").exists()
            assert timeout == 20 and request.get_method() == "GET"
            assert request.get_header("Auth_key") == SECRET
            calls.append(request.full_url)
            value = values[len(calls) - 1]
            if isinstance(value, Exception):
                raise value
            return Response(request.full_url, value)

    def credential():
        assert statuses(runs) == ["started"]
        assert (output / "source-manifest.json").exists()
        return SECRET

    def opener(**kwargs):
        assert kwargs == {"header_case_check": True}
        assert statuses(runs) == ["started"]
        return Opener()

    monkeypatch.chdir(root)
    monkeypatch.setattr(module, "__file__", str(producer))
    monkeypatch.setattr(saved, "freeze_sources", freeze)
    monkeypatch.setattr(module.probe, "credential", credential)
    monkeypatch.setattr(module.probe, "make_opener", opener)
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    monkeypatch.setattr(module.probe, "acquire", lambda *a, **kw: pytest.fail("unbounded original acquire"))
    return root, output, runs, spec, sources, calls, sleeps


def run(f):
    root, output, runs, *_ = f
    return module.run_preflight(root, root / "spec.json", output, runs)


def test_committed_spec_retains_exact_parameters_and_three_literal_requests():
    root = Path(__file__).resolve().parents[2]
    spec = saved.strict_json((root / "configs/research/discovery/activity-raw-anchor-preflight-v1.json").read_bytes())
    reference = saved.strict_json((root / saved.REFERENCE).read_bytes())
    module.validate_spec(spec, reference)
    assert spec["request_contract"]["matrix"] == [
        {"service": "stk_bydd_trd", "bas_dd": "20210428", "targets": ["003550"]},
        {"service": "stk_bydd_trd", "bas_dd": "20210527", "targets": ["003550", "383800"]},
        {"service": "stk_isu_base_info", "bas_dd": "20210527", "targets": ["383800"]}]
    assert saved.encoded(spec["parameters"]) == saved.encoded(reference["parameters"])


def test_three_requests_follow_durable_start_and_keep_exact_decimal_and_metadata(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    root, output, runs, _, _, calls, sleeps = f
    record = run(f)
    result = saved.strict_json((output / "result.json").read_bytes())
    assert calls == ["https://data-dbg.krx.co.kr/svc/apis/sto/" + path for path in (
        "stk_bydd_trd?basDd=20210428", "stk_bydd_trd?basDd=20210527", "stk_isu_base_info?basDd=20210527")]
    assert sleeps == [1.1, 1.1] and statuses(runs) == ["started", "completed"]
    assert result["status"] == "completed" and len(result["targets"]) == 4
    assert [row["raw_close"] for row in result["targets"][:3]] == [
        "1234.5000", "9007199254740993.0000000000000001", "123.45"]
    assert result["targets"][0]["original_raw_close"] == "1,234.5000"
    identity = result["targets"][3]
    assert identity["isin"] == "KR7383800000" and identity["returned_LIST_DD"] == "20210527"
    assert identity["observation_date"] is None
    assert [row["observation_date"] for row in result["targets"][:3]] == [
        "2021-04-28", "2021-05-27", "2021-05-27"]
    assert [row["returned_BAS_DD"] for row in result["targets"][:3]] == [
        "20210428", "20210527", "20210527"]
    assert all(row["available_at"] is row["is_final"] is None for row in result["targets"])
    assert all(row["evidence_level"] == "observed_current_api_not_historical" for row in result["targets"])
    manifest = saved.strict_json((output / "input-manifest.json").read_bytes())
    assert len(manifest) == 3 and all(row["read_status"] == "retained" for row in manifest)
    for row in manifest:
        assert row["sha256"] == saved.digest((output / row["file"]).read_bytes())
    assert result["request_ledger_sha256"] == saved.digest((output / "requests.jsonl").read_bytes())
    assert record["result"]["result_sha256"] == saved.digest((output / "result.json").read_bytes())
    assert all(result[field] is False for field in module.FALSE_FLAGS)
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())
    assert SECRET.encode() not in b"".join(path.read_bytes() for path in output.iterdir()) + runs.read_bytes()


@pytest.mark.parametrize("change", ["host", "date", "target", "service", "count", "retry", "timeout",
    "bytes", "header", "interval", "parameter", "parameter_type", "inputs", "promotion", "window", "extra"])
def test_spec_refuses_any_request_surface_or_reference_drift(tmp_path, monkeypatch, change):
    f = fixture(tmp_path, monkeypatch)
    spec = f[3]
    reference = {"parameters": deepcopy(spec["parameters"])}
    contract = spec["request_contract"]
    if change == "date": contract["matrix"][0]["bas_dd"] = "20180102"
    elif change == "target": contract["matrix"][0]["targets"].append("000001")
    elif change == "service": contract["matrix"][0]["service"] = "ksq_bydd_trd"
    elif change == "parameter": spec["parameters"]["slots"] = 21
    elif change == "parameter_type": spec["parameters"]["threshold"] = 3
    elif change == "inputs": spec["inputs"]["database"] = {"path": "/tmp/db"}
    elif change == "promotion": spec["promotion_allowed"] = True
    elif change == "window": spec["window"]["start"] = "2018-01-01"
    elif change == "extra": spec["fallback"] = True
    else:
        field = {"host": "host", "count": "max_requests", "retry": "automatic_retries", "timeout": "timeout_seconds",
                 "bytes": "max_response_bytes", "header": "authentication_header", "interval": "interval_seconds"}[change]
        contract[field] = "bad"
    with pytest.raises(ValueError): module.validate_spec(spec, reference)


@pytest.mark.parametrize("change,reason", [
    ("absent", "target_absent_cause_unknown"), ("duplicate", "duplicate_target"),
    ("date", "observation_date_mismatch"), ("zero", "invalid_raw_close"),
    ("nan", "invalid_raw_close"), ("float", "invalid_raw_close"), ("negative", "invalid_raw_close"),
    ("isin", "invalid_or_ambiguous_isin_join"), ("short_join", "target_absent_cause_unknown"),
    ("duplicate_isin", "invalid_or_ambiguous_isin_join"), ("listed", "invalid_listing_date"),
    ("future_listed", "invalid_listing_date")])
def test_bad_or_missing_target_is_explicit_unresolved_not_zero(tmp_path, monkeypatch, change, reason):
    values = payloads()
    target_index = 3 if change in {"isin", "short_join", "duplicate_isin", "listed", "future_listed"} else 0
    response_index = 2 if target_index == 3 else 0
    rows = values[response_index]["OutBlock_1"]
    if change == "absent": rows.clear()
    elif change == "duplicate": rows.append(deepcopy(rows[0]))
    elif change == "date": rows[0]["BAS_DD"] = "20210427"
    elif change in {"zero", "nan", "float", "negative"}:
        rows[0]["TDD_CLSPRC"] = {"zero": "0", "nan": "NaN", "float": 123.45, "negative": "-1"}[change]
    elif change == "isin": rows[0]["ISU_CD"] = "383800"
    elif change == "short_join": rows[0]["ISU_SRT_CD"] = "KR7383800000"
    elif change == "duplicate_isin": rows.append({**rows[0], "ISU_SRT_CD": "999999"})
    else: rows[0]["LIST_DD"] = "20210230" if change == "listed" else "20210528"
    f = fixture(tmp_path, monkeypatch, [saved.encoded(value) for value in values])
    run(f)
    result = saved.strict_json((f[1] / "result.json").read_bytes())
    row = result["targets"][target_index]
    assert result["status"] == "completed_with_unresolved" and len(f[5]) == 3
    assert row["status"] == "unresolved" and row["reason"] == reason
    assert "raw_close" not in row and "isin" not in row
    assert row["observation_date"] is None
    if target_index == 0:
        assert row["requested_bas_dd"] == "20210428"
        assert row["returned_BAS_DD"] == (
            None if change in {"absent", "duplicate"} else "20210427" if change == "date" else "20210428")


def test_empty_success_responses_keep_four_unknown_targets(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch, [b'{"OutBlock_1":[]}' for _ in range(3)])
    run(f)
    result = saved.strict_json((f[1] / "result.json").read_bytes())
    assert result["status"] == "completed_with_unresolved"
    assert len(result["targets"]) == 4 and all(row["status"] == "unresolved" for row in result["targets"])
    assert all(row["observation_date"] is None for row in result["targets"])


@pytest.mark.parametrize("failure", ["http", "redirect", "transport", "echo", "escaped_echo", "json", "size", "drift", "credential"])
def test_failed_partial_attempt_has_no_retry_secret_or_false_completion(tmp_path, monkeypatch, failure):
    values = [saved.encoded(value) for value in payloads()]
    secret_exception = "private exception " + SECRET
    if failure == "http": values[1] = HTTPError("https://example.invalid", 401, secret_exception, {"AUTH_KEY": SECRET}, BytesIO(SECRET.encode()))
    elif failure == "redirect": values[1] = HTTPError("https://example.invalid", 302, secret_exception, {"Location": SECRET}, BytesIO(SECRET.encode()))
    elif failure == "transport": values[1] = URLError(secret_exception)
    elif failure == "echo": values[1] = saved.encoded({"OutBlock_1": [{"name": SECRET}]})
    elif failure == "escaped_echo":
        escaped = "".join("\\u%04x" % ord(char) for char in SECRET)
        values[1] = ('{"OutBlock_1":[{"name":"' + escaped + '"}]}').encode()
    elif failure == "json": values[1] = b"not a json response"
    elif failure == "size": values[1] = b"x" * 8388609
    f = fixture(tmp_path, monkeypatch, values)
    if failure == "drift":
        original = saved.freeze_sources
        count = 0

        def freeze(*args, **kwargs):
            nonlocal count
            source, raw, spec = original(*args, **kwargs)
            count += 1
            if count > 1: source["code_version"] = "b" * 40
            return source, raw, spec

        monkeypatch.setattr(saved, "freeze_sources", freeze)
    elif failure == "credential":
        monkeypatch.setattr(module.probe, "credential", lambda: (_ for _ in ()).throw(module.probe.ProbeRefusal(secret_exception)))
    with pytest.raises(module.probe.ProbeRefusal, match="raw_anchor_attempt_failed"):
        run(f)
    output, runs, calls = f[1], f[2], f[5]
    assert statuses(runs) == ["started", "failed"] and not (output / "result.json").exists()
    expected = 3 if failure == "drift" else 0 if failure == "credential" else 2
    assert len(calls) == expected
    receipt = saved.strict_json((output / "failure.json").read_bytes())
    assert receipt["requests_attempted"] == expected and receipt["requests_unattempted"] == 3 - expected
    assert receipt["automatic_retries"] == 0 and receipt["partial_package_preserved"] is True
    if failure == "http": assert receipt["failure"] == "access_failure"
    elif failure == "redirect": assert receipt["failure"] == "redirect_rejected"
    elif failure in {"echo", "escaped_echo"}: assert receipt["failure"] == "credential_echo"
    assert receipt["D1_completed_studies"] == receipt["D2_completed_studies"] == 0
    assert all(receipt[field] is False for field in module.FALSE_FLAGS)
    assert (output / "resource-manifest.json").exists() and (output / "input-manifest.json").exists()
    if expected: assert (output / "response-01-stk_bydd_trd-20210428.json").exists()
    if failure not in {"drift", "credential"}: assert not (output / "response-02-stk_bydd_trd-20210527.json").exists()
    retained = b"".join(path.read_bytes() for path in output.iterdir()) + runs.read_bytes()
    assert SECRET.encode() not in retained and b"private exception" not in retained


def test_existing_private_output_cannot_trigger_credential_or_requests(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    f[1].mkdir(mode=0o700)
    (f[1] / "sentinel").write_bytes(b"keep")
    monkeypatch.setattr(module.probe, "credential", lambda: pytest.fail("credential before exclusive output"))
    with pytest.raises(module.probe.ProbeRefusal): run(f)
    assert list(f[1].iterdir()) == [f[1] / "sentinel"] and not f[5]


@pytest.mark.parametrize("alias", ["source_log", "checkout_output", "output_log"])
def test_write_aliases_fail_before_durable_start_or_credential(tmp_path, monkeypatch, alias):
    f = fixture(tmp_path, monkeypatch)
    root, output, runs = f[:3]
    if alias == "source_log": runs = root / "python/synthetic.py"
    elif alias == "checkout_output": output = root / "run"
    else: runs = output / "runs.jsonl"
    monkeypatch.setattr(module.probe, "credential", lambda: pytest.fail("credential before boundary checks"))
    with pytest.raises(ValueError): module.run_preflight(root, root / "spec.json", output, runs)
    assert not output.exists() and not runs.exists() and not f[5]


@pytest.mark.parametrize("change", ["missing", "pin", "bytes", "root"])
def test_unfrozen_executed_producer_fails_before_log_credential_or_network(tmp_path, monkeypatch, change):
    f = fixture(tmp_path, monkeypatch)
    root, output, runs, _, sources = f[:5]
    if change == "missing": sources["files"].pop()
    elif change == "pin": sources["files"][-1]["sha256"] = "f" * 64
    elif change == "bytes": Path(module.__file__).write_bytes(b"different runtime producer")
    else: monkeypatch.setattr(module, "__file__", str(tmp_path / "outside.py"))
    monkeypatch.setattr(module.probe, "credential", lambda: pytest.fail("credential before producer validation"))
    with pytest.raises(ValueError): module.run_preflight(root, root / "spec.json", output, runs)
    assert not output.exists() and not runs.exists() and not f[5]


def test_cli_does_not_expose_arbitrary_exception_message(monkeypatch, capsys):
    monkeypatch.setattr(module, "run_preflight", lambda *args: (_ for _ in ()).throw(ValueError(SECRET)))
    assert module.main(["--spec", "/unused", "--output-dir", "/unused", "--runs-path", "/unused"]) == 1
    assert SECRET not in capsys.readouterr().out
