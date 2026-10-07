"""Offline tests for the bounded access probe; no exchange data is requested."""

import io
import json
import ssl
import stat
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from data import krx_openapi_probe as probe


def trade(day="20190102", code="005930"):
    row = {field: "fixture" for field in probe.TRADE_FIELDS}
    row.update(BAS_DD=day, ISU_CD=code, MKTCAP="1,000", LIST_SHRS="10",
               ACC_TRDVOL="0", ACC_TRDVAL="0")
    return row


def basic():
    row = {field: "fixture" for field in probe.BASIC_FIELDS}
    row.update(ISU_CD="KR7005930003", ISU_SRT_CD="005930", LIST_DD="19750611",
               LIST_SHRS="10")
    return row


class Response(io.BytesIO):
    status = 200

    def __init__(self, body, url):
        super().__init__(body)
        self.url = url

    def geturl(self):
        return self.url


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("KRX_API_KEY", "test-only-credential")
    monkeypatch.setattr(probe, "source_version", lambda: "a" * 40)
    sleeps = []
    monkeypatch.setattr(probe.time, "sleep", sleeps.append)
    calls = []
    output = tmp_path / "evidence"

    class Opener:
        mutate = staticmethod(lambda request, payload: payload)

        def open(self, request, timeout):
            ledger = (output / "requests.jsonl").read_text().splitlines()
            assert json.loads(ledger[-1])["event"] == "attempt"
            assert json.loads(ledger[-1])["number"] == len(calls) + 1
            assert timeout == 20
            assert request.get_method() == "GET"
            assert request.get_header("Auth_key") == "test-only-credential"
            calls.append(request)
            day = request.full_url.split("basDd=")[1]
            is_trade = "bydd_trd" in request.full_url
            rows = [] if is_trade and day == "20260919" else [trade(day) if is_trade else basic()]
            payload = self.mutate(request, {"OutBlock_1": rows})
            if isinstance(payload, BaseException):
                raise payload
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            return Response(body, request.full_url)

    opener = Opener()
    opener.header_case_checks = []
    def make_opener(*, header_case_check=False):
        opener.header_case_checks.append(header_case_check)
        return opener
    monkeypatch.setattr(probe, "make_opener", make_opener)
    return output, opener, calls, sleeps


@pytest.fixture
def wire(monkeypatch):
    """Run urllib/http.client serialization and parsing without a real socket."""
    state = {"response": b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}", "sockets": []}

    class Socket:
        def __init__(self):
            self.sent = bytearray()

        def sendall(self, data):
            self.sent.extend(data)

        def makefile(self, mode):
            return io.BytesIO(state["response"])

        def close(self):
            pass

    def connect(connection):
        assert connection.host == "data-dbg.krx.co.kr"
        assert connection.port == 443 and connection.timeout == 20
        assert connection._context.check_hostname is True
        assert connection._context.verify_mode == ssl.CERT_REQUIRED
        connection.sock = Socket()
        state["sockets"].append(connection.sock)

    monkeypatch.setattr(probe.HTTPSConnection, "connect", connect)
    return state


def test_wire_changes_only_auth_header_spelling(wire):
    for diagnostic in (False, True):
        request = Request(
            "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=20190102",
            headers={"AUTH_KEY": "test-only-credential", "X-Test-Header": "unchanged"},
        )
        with probe.make_opener(header_case_check=diagnostic).open(request, timeout=20) as response:
            assert response.read() == b"{}"
    default, diagnostic = (bytes(sock.sent) for sock in wire["sockets"])
    assert b"\r\nAuth_Key: test-only-credential\r\n" in default
    assert b"\r\nAUTH_KEY: test-only-credential\r\n" in diagnostic
    assert diagnostic == default.replace(b"\r\nAuth_Key:", b"\r\nAUTH_KEY:")


def test_header_case_cli_sends_only_fixed_first_request(harness, capsys):
    output, opener, calls, sleeps = harness
    assert probe.main(["--output-dir", str(output), "--header-case-check"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "single_request_checked"
    assert report["specification"] == "krx-openapi-auth-header-case-v1"
    assert report["max_requests"] == report["requests_attempted"] == len(calls) == 1
    assert report["http_200_responses"] == report["schema_valid_responses"] == 1
    assert report["requests_unattempted"] == 0 and not sleeps
    assert opener.header_case_checks == [True]
    assert calls[0].full_url == "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=20190102"
    assert report["joins"] == report["basic_date_comparisons"] == []
    assert report["universe_ready"] is report["historical_coverage_certified"] is False
    started = json.loads((output / "started.json").read_text())
    assert started["max_requests"] == 1
    assert started["request_matrix"] == [{"service": "stk_bydd_trd", "bas_dd": "20190102"}]
    assert json.loads((output / "report.json").read_text()) == report
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    for path in output.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert b"test-only-credential" not in path.read_bytes()


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 401])
def test_header_case_http_failure_never_retries_or_follows_redirect(wire, tmp_path, monkeypatch,
                                                                  status):
    monkeypatch.setenv("KRX_API_KEY", "test-only-credential")
    monkeypatch.setenv("https_proxy", "http://untrusted.invalid:1234")
    monkeypatch.setattr(probe, "source_version", lambda: "a" * 40)
    body = b"test-only-credential"
    wire["response"] = (
        f"HTTP/1.1 {status} Refused\r\nLocation: https://untrusted.invalid/\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    ).encode() + body
    def refuse_body_read(*args, **kwargs):
        pytest.fail("an error response body must not be read")
    monkeypatch.setattr(HTTPError, "read", refuse_body_read, raising=False)
    output = tmp_path / "evidence"
    report = probe.acquire(output, header_case_check=True)
    assert report["status"] == "failed"
    assert report["failure"] == ("access_failure" if status == 401 else "redirect_rejected")
    assert report["max_requests"] == report["requests_attempted"] == len(wire["sockets"]) == 1
    assert report["requests_unattempted"] == report["http_200_responses"] == 0
    assert report["schema_valid_responses"] == 0
    assert report["joins"] == report["basic_date_comparisons"] == []
    assert not list(output.glob("response-*.json"))
    assert all(body not in path.read_bytes() for path in output.iterdir())
    ledger = [json.loads(line) for line in (output / "requests.jsonl").read_text().splitlines()]
    assert [record["event"] for record in ledger] == ["attempt", "response"]
    assert ledger[-1]["http_status"] == status


def test_fixed_matrix_private_evidence_and_aggregate_only(harness):
    output, opener, calls, sleeps = harness
    report = probe.acquire(output)
    assert report["status"] == "access_checked"
    assert report["specification"] == probe.SPECIFICATION and report["max_requests"] == 16
    assert opener.header_case_checks == [False]
    assert report["requests_attempted"] == 16 == len(calls)
    assert report["http_200_responses"] == report["schema_valid_responses"] == 16
    assert report["requests_unattempted"] == 0
    assert report["report_persisted"] is True
    started = json.loads((output / "started.json").read_text())
    assert len(started["request_matrix"]) == 16
    assert len(sleeps) == 15 and all(delay >= 1 for delay in sleeps)
    assert {r.full_url for r in calls} == {
        f"https://data-dbg.krx.co.kr/svc/apis/sto/{service}?basDd={day}"
        for service in probe.SERVICES for day in probe.DATES
    }
    assert report["source_sha"] == "a" * 40
    assert report["universe_ready"] is False
    assert report["historical_coverage_certified"] is False
    assert all(r["schema_valid"] for r in report["observations"])
    assert report["joins"][0]["matched_rows"] == 1
    assert all(r["all_observed_payloads_equal"] for r in report["basic_date_comparisons"])
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    for path in output.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert b"test-only-credential" not in path.read_bytes()
    public = json.dumps(report)
    assert "005930" not in public and "fixture" not in public and "1,000" not in public


@pytest.mark.parametrize("payload,reason", [
    (b"<html>denied</html>", "invalid_json"),
    ({"error": "unauthorized"}, "invalid_envelope"),
    ({"OutBlock_1": [], "error": "denied"}, "invalid_envelope"),
    ({"OutBlock_1": {}}, "invalid_envelope"),
    ({"OutBlock_1": ["not a row"]}, "invalid_envelope"),
    ({"OutBlock_1": []}, "schema_failure"),
])
def test_first_bad_response_stops_without_success(harness, payload, reason):
    output, opener, calls, _ = harness
    opener.mutate = lambda request, body: payload
    report = probe.acquire(output)
    assert len(calls) == 1
    assert report["status"] == "failed" and report["failure"] == reason
    assert report["schema_valid_responses"] == 0 and report["requests_unattempted"] == 15
    assert report["universe_ready"] is False
    assert json.loads((output / "report.json").read_text()) == report


@pytest.mark.parametrize("field,value", [
    ("MKTCAP", ""), ("MKTCAP", "0"), ("MKTCAP", "NaN"),
    ("MKTCAP", "1,00"), ("LIST_SHRS", "-1"), ("ACC_TRDVOL", "-1"),
    ("ACC_TRDVAL", "Infinity"), ("LIST_SHRS", 12), ("BAS_DD", "20260918"),
])
def test_numeric_and_date_errors_are_not_passed(harness, field, value):
    output, opener, calls, _ = harness
    row = trade()
    row[field] = value
    opener.mutate = lambda request, body: {"OutBlock_1": [row]}
    report = probe.acquire(output)
    assert len(calls) == 1
    assert report["failure"] == "schema_failure"
    assert report["observations"][0]["schema_valid"] is False


def test_missing_fields_and_duplicate_ids_are_counted(harness):
    output, opener, calls, _ = harness
    row = trade()
    del row["MKT_NM"]
    opener.mutate = lambda request, body: {"OutBlock_1": [row, row]}
    result = probe.acquire(output)
    observation = result["observations"][0]
    assert observation["required_missing_fields"]["MKT_NM"] == 2
    assert observation["identifiers"]["ISU_CD"]["duplicate_rows"] == 1
    assert result["failure"] == "schema_failure" and len(calls) == 1


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 401, 403, 429, 500])
def test_http_errors_do_not_read_or_persist_body(harness, status):
    output, opener, calls, _ = harness
    body = io.BytesIO(b"test-only-credential")
    opener.mutate = lambda request, payload: HTTPError(
        request.full_url, status, "test-only-credential", {}, body)
    result = probe.acquire(output)
    assert result["status"] == "failed" and len(calls) == 1
    assert not list(output.glob("response-*.json"))
    assert all(b"test-only-credential" not in path.read_bytes() for path in output.iterdir())


def test_transport_error_is_sanitized(harness, capsys):
    output, opener, calls, _ = harness
    opener.mutate = lambda request, payload: URLError("test-only-credential")
    assert probe.main(["--output-dir", str(output)]) == 1
    assert len(calls) == 1
    assert "test-only-credential" not in str(capsys.readouterr())
    assert all(b"test-only-credential" not in path.read_bytes() for path in output.iterdir())


@pytest.mark.parametrize("header_case_check", [False, True])
@pytest.mark.parametrize("escaped", [False, True])
def test_echoed_credential_never_saved(harness, escaped, header_case_check):
    output, opener, calls, _ = harness
    body = json.dumps({"OutBlock_1": [trade()]}).replace("fixture", "test-only-credential")
    if escaped:
        body = body.replace("test-only-credential", "\\u0074est-only-credential")
    opener.mutate = lambda request, payload: body.encode()
    report = probe.acquire(output, header_case_check=header_case_check)
    assert report["failure"] == "credential_echo" and len(calls) == 1
    assert not list(output.glob("response-*.json"))


def test_oversized_response_stops(harness):
    output, opener, calls, _ = harness
    opener.mutate = lambda request, body: b" " * (probe.MAX_RESPONSE_BYTES + 1)
    result = probe.acquire(output)
    assert result["failure"] == "response_too_large" and len(calls) == 1


def test_saturday_trade_nonempty_stops_but_basic_nonempty_is_observed(harness):
    output, opener, calls, _ = harness
    opener.mutate = lambda request, payload: {"OutBlock_1": [trade("20260919")]} if (
        "bydd_trd" in request.full_url and "20260919" in request.full_url) else payload
    result = probe.acquire(output)
    assert result["failure"] == "schema_failure" and len(calls) == 13
    assert result["observations"][-1]["negative_control"] == "unexpected_nonempty"


def test_isin_trade_joins_standard_id_not_short_id(harness):
    output, opener, _, _ = harness
    def use_isin(request, payload):
        if "bydd_trd" in request.full_url:
            for row in payload["OutBlock_1"]:
                row["ISU_CD"] = "KR7005930003"
        return payload
    opener.mutate = use_isin
    result = probe.acquire(output)
    assert result["joins"][0]["matched_rows"] == 1
    assert result["joins"][0]["trade_id_kind"] == "isin12"


def test_future_listing_is_flagged_without_certifying_history(harness):
    output, opener, _, _ = harness
    def future_listing(request, payload):
        if "base_info" in request.full_url:
            payload["OutBlock_1"][0]["LIST_DD"] = "20260102"
        return payload
    opener.mutate = future_listing
    result = probe.acquire(output)
    assert result["status"] == "access_checked"
    assert result["observations"][2]["listing_dates_after_requested_date"] == 1
    assert result["historical_coverage_certified"] is False


@pytest.mark.parametrize("value", [None, "", "bad\rkey", "bad\nkey", "bad key", "한글"])
def test_invalid_credentials_fail_before_requests(harness, monkeypatch, value):
    output, _, calls, _ = harness
    if value is None:
        monkeypatch.delenv("KRX_API_KEY")
    else:
        monkeypatch.setenv("KRX_API_KEY", value)
    with pytest.raises(probe.ProbeRefusal):
        probe.acquire(output)
    assert not calls and not output.exists()


@pytest.mark.parametrize("header_case_check", [False, True])
def test_dirty_checkout_fails_before_requests(harness, monkeypatch, header_case_check):
    output, _, calls, _ = harness
    def dirty():
        raise probe.ProbeRefusal("uncommitted_sources")
    monkeypatch.setattr(probe, "source_version", dirty)
    with pytest.raises(probe.ProbeRefusal, match="uncommitted_sources"):
        probe.acquire(output, header_case_check=header_case_check)
    assert not calls and not output.exists()


def test_existing_or_symlink_output_fails_before_requests(harness, tmp_path):
    output, _, calls, _ = harness
    output.mkdir()
    with pytest.raises(probe.ProbeRefusal):
        probe.acquire(output)
    output.rmdir()
    output.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(probe.ProbeRefusal):
        probe.acquire(output)
    assert not calls


@pytest.mark.parametrize("header_case_check", [False, True])
def test_ledger_failure_prevents_next_http_request(harness, monkeypatch, header_case_check):
    output, _, calls, _ = harness
    original = probe.persist
    def fail_attempt(path, value, **kwargs):
        if path.name == "requests.jsonl":
            raise OSError("test-only-credential")
        original(path, value, **kwargs)
    monkeypatch.setattr(probe, "persist", fail_attempt)
    result = probe.acquire(output, header_case_check=header_case_check)
    assert result["failure"] == "local_io_failure" and not calls
    assert result["requests_unattempted"] == (1 if header_case_check else 16)


def test_no_proxy_and_no_redirect_forwarding(monkeypatch):
    monkeypatch.setenv("https_proxy", "http://untrusted.invalid:1234")
    opener = probe.make_opener()
    assert not any(type(handler).__name__ == "ProxyHandler" and handler.proxies
                   for handler in opener.handlers)
    redirect = next(h for h in opener.handlers if isinstance(h, probe.NoRedirect))
    request = Request("https://data-dbg.krx.co.kr/original", headers={"AUTH_KEY": "sentinel"})
    for code in (301, 302, 303, 307, 308):
        assert redirect.redirect_request(request, None, code, "redirect", {},
                                         "https://untrusted.invalid/") is None


def test_cli_cannot_override_fixed_scope(harness, capsys):
    output, _, calls, _ = harness
    assert probe.main(["--output-dir", str(output), "--date", "20180101"]) == 1
    assert not calls
    assert "20180101" not in str(capsys.readouterr())


def test_duplicate_json_keys_are_not_an_error_envelope_bypass(harness):
    output, opener, calls, _ = harness
    opener.mutate = lambda request, payload: b'{"OutBlock_1":{"error":"denied"},"OutBlock_1":[]}'
    result = probe.acquire(output)
    assert result["failure"] == "invalid_json" and len(calls) == 1
    assert not list(output.glob("response-*.json"))


def test_symlinked_parent_fails_before_requests(harness, tmp_path):
    _, _, calls, _ = harness
    parent = tmp_path / "symlink"
    parent.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(probe.ProbeRefusal, match="symlinked_output_parent"):
        probe.acquire(parent / "new-output")
    assert not calls


def test_source_provenance_uses_actual_checkout_and_rejects_dirty(monkeypatch):
    calls = []
    monkeypatch.setenv("GIT_DIR", "/wrong/repository")
    def git_output(command, **kwargs):
        calls.append((command, kwargs))
        return " M python/data/krx_openapi_probe.py\n"
    monkeypatch.setattr(probe.subprocess, "check_output", git_output)
    with pytest.raises(probe.ProbeRefusal, match="uncommitted_sources"):
        probe.source_version()
    assert len(calls) == 1
    assert calls[0][0][:3] == ["git", "-C", str(probe.ROOT)]
    assert "GIT_DIR" not in calls[0][1]["env"]


def test_source_provenance_requires_full_commit_hash(monkeypatch):
    monkeypatch.setattr(probe.subprocess, "check_output", lambda command, **kwargs: (
        "" if "status" in command else "unverified\n"))
    with pytest.raises(probe.ProbeRefusal, match="source_version_unavailable"):
        probe.source_version()


def test_unmatched_join_is_reported_without_smaller_universe(harness):
    output, opener, _, _ = harness
    def no_match(request, payload):
        if "base_info" in request.full_url:
            payload["OutBlock_1"][0]["ISU_SRT_CD"] = "000000"
        return payload
    opener.mutate = no_match
    report = probe.acquire(output)
    assert report["joins"][0]["matched_rows"] == 0
    assert report["joins"][0]["unmatched_rows"] == 1
    assert report["universe_ready"] is False


@pytest.mark.parametrize("transport_failure", [False, True])
def test_final_evidence_failure_keeps_counts_and_original_failure(harness, monkeypatch, capsys,
                                                                transport_failure):
    output, opener, calls, _ = harness
    if transport_failure:
        opener.mutate = lambda request, payload: URLError("test-only-credential")
    original = probe.persist
    def fail_final(path, value, **kwargs):
        if path.name in ("report.json", "report.pending.json"):
            raise OSError("test-only-credential")
        original(path, value, **kwargs)
    monkeypatch.setattr(probe, "persist", fail_final)
    assert probe.main(["--output-dir", str(output)]) == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["status"] == "failed" and result["evidence_write_failure"] is True
    assert result["report_persisted"] is False
    assert result["requests_attempted"] == len(calls) == (1 if transport_failure else 16)
    assert result["requests_unattempted"] == (15 if transport_failure else 0)
    assert result["failure"] == ("transport_failure" if transport_failure else "evidence_write_failure")
    assert "test-only-credential" not in str(captured)
    assert not (output / "report.json").exists()


def test_report_rename_failure_leaves_no_published_success(harness, monkeypatch):
    output, _, calls, _ = harness
    def fail_rename(*args):
        raise OSError("disk full")
    monkeypatch.setattr(probe.os, "replace", fail_rename)
    result = probe.acquire(output)
    assert result["status"] == "failed" and result["requests_attempted"] == len(calls) == 16
    assert not (output / "report.json").exists()


def test_directory_sync_failure_withdraws_report_after_rename(harness, monkeypatch):
    output, _, calls, _ = harness
    original = probe.sync_directory
    def fail_published_sync(path):
        if (output / "report.json").exists():
            raise OSError("test-only-credential")
        original(path)
    monkeypatch.setattr(probe, "sync_directory", fail_published_sync)
    result = probe.acquire(output)
    assert result["status"] == "failed" and result["report_persisted"] is False
    assert result["requests_attempted"] == len(calls) == 16
    assert not (output / "report.json").exists()
