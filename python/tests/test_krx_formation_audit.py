"""Offline formation-source audit contracts; all responses are synthetic."""

import io
import json
import stat
from copy import deepcopy
from decimal import Decimal
from urllib.error import HTTPError, URLError

import pytest

from data import krx_formation_audit as audit
from data import krx_openapi_probe as probe


DATES = (
    "20190402", "20191004", "20200407", "20201013", "20210415", "20211020",
    "20220422", "20221027", "20230427", "20231103", "20240510", "20241115",
    "20250527", "20251201",
)


def rows_for_day(day=DATES[0]):
    result = {}
    for index, market in enumerate(("KOSPI", "KOSDAQ")):
        code = ("111111", "222222")[index]
        trade = {field: "fixture-name" for field in probe.TRADE_FIELDS}
        trade.update(BAS_DD=day, ISU_CD=code, MKT_NM=market, SECT_TP_NM="" if index == 0 else "중견기업부",
                     TDD_CLSPRC="50000", MKTCAP="5000000000000", LIST_SHRS="100000000",
                     TDD_OPNPRC="0", TDD_HGPRC="0", TDD_LWPRC="0", ACC_TRDVOL="0", ACC_TRDVAL="0")
        basic = {field: "fixture-name" for field in probe.BASIC_FIELDS}
        basic.update(ISU_CD=f"KR7{code}003", ISU_SRT_CD=code, MKT_TP_NM=market,
                     LIST_DD="20000104", LIST_SHRS="100000000", SECUGRP_NM="주권",
                     KIND_STKCERT_TP_NM="보통주", SECT_TP_NM=trade["SECT_TP_NM"])
        result[probe.SERVICES[index]] = [trade]
        result[probe.SERVICES[index + 2]] = [basic]
    return result


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("KRX_API_KEY", "test-only-credential")
    monkeypatch.setattr(probe, "source_version", lambda: "a" * 40)
    sleeps = []
    monkeypatch.setattr(audit.time, "sleep", sleeps.append)
    output = tmp_path / "evidence"
    calls = []

    class Response(io.BytesIO):
        status = 200

        def __init__(self, body, url):
            super().__init__(body)
            self.url = url

        def geturl(self):
            return self.url

        def read(self, size=-1):
            assert size == 8 * 1024 * 1024 + 1
            return super().read(size)

    class Opener:
        mutate = staticmethod(lambda request, payload: payload)

        def open(self, request, timeout):
            started = json.loads((output / "started.json").read_text())
            assert started["status"] == "started" and started["source_sha"] == "a" * 40
            assert len(started["request_matrix"]) == 56
            ledger = [json.loads(line) for line in (output / "requests.jsonl").read_text().splitlines()]
            assert ledger[-1]["event"] == "attempt" and ledger[-1]["number"] == len(calls) + 1
            assert timeout == 20 and request.get_method() == "GET"
            assert request.get_header("Auth_key") == "test-only-credential"
            calls.append(request)
            service, day = request.full_url.split("/sto/")[1].split("?basDd=")
            payload = self.mutate(request, {"OutBlock_1": rows_for_day(day)[service]})
            if isinstance(payload, BaseException):
                raise payload
            return Response(payload if isinstance(payload, bytes) else json.dumps(payload).encode(), request.full_url)

    opener = Opener()
    def make_opener(**kwargs):
        assert not kwargs  # The existing normal-mode transport is used unchanged.
        return opener
    monkeypatch.setattr(probe, "make_opener", make_opener)
    return output, opener, calls, sleeps


def test_cap_boundary_is_inclusive_and_counts_whole_market_without_eligibility():
    rows = rows_for_day()
    below = rows["ksq_bydd_trd"][0]
    below.update(TDD_CLSPRC="49999.99999999", MKTCAP="4999999999999")
    result = audit.audit_day(DATES[0], rows)
    assert result["trade_rows"] == result["basic_rows"] == 2
    assert result["cap_pass_rows"] == result["cap_below_rows"] == 1
    assert result["cap_pass_common_label_rows"] == 1
    assert result["markets"][0]["trade_rows"] == result["markets"][1]["trade_rows"] == 1
    assert result["universe_ready"] is result["historical_coverage_certified"] is False
    assert result["historical_eligibility_certified"] is False
    assert "111111" not in json.dumps(result)


def test_class_label_count_is_not_an_operating_company_rule():
    rows = rows_for_day()
    rows["stk_isu_base_info"][0]["SECUGRP_NM"] = "투자회사"
    result = audit.audit_day(DATES[0], rows)
    assert result["cap_pass_rows"] == result["cap_pass_common_label_rows"] == 2
    assert result["historical_eligibility_certified"] is False


def test_short_codes_allow_uppercase_alphanumeric_without_loss_of_join():
    rows = rows_for_day()
    rows["ksq_bydd_trd"][0]["ISU_CD"] = "0001A0"
    rows["ksq_isu_base_info"][0]["ISU_SRT_CD"] = "0001A0"
    assert audit.audit_day(DATES[0], rows)["joined_rows"] == 2


@pytest.mark.parametrize("day,missing_service", [("20181228", None), (DATES[0], "ksq_isu_base_info")])
def test_pure_audit_refuses_unregistered_or_incomplete_day(day, missing_service):
    rows = rows_for_day(day)
    if missing_service:
        rows.pop(missing_service)
    with pytest.raises(probe.ProbeRefusal, match="incomplete_day"):
        audit.audit_day(day, rows)


@pytest.mark.parametrize("service,field,value", [
    ("stk_bydd_trd", "ISU_CD", "KR7111111003"),
    ("stk_bydd_trd", "MKTCAP", ""), ("stk_bydd_trd", "MKTCAP", "NaN"),
    ("stk_bydd_trd", "MKTCAP", "0"), ("stk_bydd_trd", "MKTCAP", "1,00"),
    ("stk_bydd_trd", "TDD_CLSPRC", "0"), ("stk_bydd_trd", "TDD_OPNPRC", "-1"),
    ("stk_bydd_trd", "TDD_HGPRC", "Infinity"), ("stk_bydd_trd", "TDD_LWPRC", None),
    ("stk_bydd_trd", "LIST_SHRS", "0.5"), ("stk_bydd_trd", "ACC_TRDVOL", "1.5"),
    ("stk_bydd_trd", "ACC_TRDVOL", "-1"), ("stk_bydd_trd", "ACC_TRDVAL", "-1"),
    ("stk_bydd_trd", "BAS_DD", "20190403"), ("stk_bydd_trd", "MKT_NM", "KOSDAQ"),
    ("ksq_bydd_trd", "SECT_TP_NM", ""),
    ("stk_isu_base_info", "ISU_CD", "111111"), ("stk_isu_base_info", "ISU_SRT_CD", "KR7111111003"),
    ("stk_isu_base_info", "LIST_DD", "20190403"), ("stk_isu_base_info", "LIST_DD", "20190230"),
    ("stk_isu_base_info", "MKT_TP_NM", "KOSDAQ"), ("stk_isu_base_info", "SECUGRP_NM", " "),
    ("stk_isu_base_info", "KIND_STKCERT_TP_NM", ""), ("stk_isu_base_info", "LIST_SHRS", "1.1"),
])
def test_invalid_whole_market_fields_are_fatal(service, field, value):
    rows = rows_for_day()
    rows[service][0][field] = value
    with pytest.raises(probe.ProbeRefusal, match="schema_failure"):
        audit.audit_day(DATES[0], rows)


@pytest.mark.parametrize("service", probe.SERVICES)
def test_empty_missing_field_and_duplicate_rows_fail(service):
    for mutation in (lambda rows: rows.clear(), lambda rows: rows[0].pop("ISU_NM"),
                     lambda rows: rows.append(deepcopy(rows[0]))):
        rows = rows_for_day()
        mutation(rows[service])
        with pytest.raises(probe.ProbeRefusal, match="schema_failure"):
            audit.audit_day(DATES[0], rows)


@pytest.mark.parametrize("kind", ["trade", "basic_short", "basic_isin"])
def test_cross_market_duplicate_ids_fail(kind):
    rows = rows_for_day()
    if kind == "trade":
        rows["ksq_bydd_trd"][0]["ISU_CD"] = rows["stk_bydd_trd"][0]["ISU_CD"]
    else:
        field = "ISU_SRT_CD" if kind == "basic_short" else "ISU_CD"
        rows["ksq_isu_base_info"][0][field] = rows["stk_isu_base_info"][0][field]
    with pytest.raises(probe.ProbeRefusal, match="cross_market_duplicate"):
        audit.audit_day(DATES[0], rows)


@pytest.mark.parametrize("extra_service", ["stk_bydd_trd", "stk_isu_base_info"])
def test_both_directions_of_membership_are_required(extra_service):
    rows = rows_for_day()
    extra = deepcopy(rows[extra_service][0])
    if "bydd" in extra_service:
        extra["ISU_CD"] = "333333"
    else:
        extra.update(ISU_CD="KR7333333003", ISU_SRT_CD="333333")
    rows[extra_service].append(extra)
    with pytest.raises(probe.ProbeRefusal, match="membership_mismatch"):
        audit.audit_day(DATES[0], rows)


@pytest.mark.parametrize("field,value,reason", [
    ("MKTCAP", "4999999999999", "capitalization_mismatch"),
    ("LIST_SHRS", "99999999", "listed_shares_mismatch"),
])
def test_arithmetic_and_matching_share_counts_are_exact(field, value, reason):
    rows = rows_for_day()
    service = "stk_bydd_trd" if field == "MKTCAP" else "stk_isu_base_info"
    rows[service][0][field] = value
    with pytest.raises(probe.ProbeRefusal, match=reason):
        audit.audit_day(DATES[0], rows)


def test_market_cap_product_does_not_round_at_decimal_context_precision():
    rows = rows_for_day()
    rows["stk_bydd_trd"][0].update(TDD_CLSPRC="12345678901234567890123456789", LIST_SHRS="10",
                                   MKTCAP="123456789012345678901234567890")
    rows["stk_isu_base_info"][0]["LIST_SHRS"] = "10"
    assert audit.audit_day(DATES[0], rows)["cap_pass_rows"] == 2


def test_regular_ohlc_is_not_a_bound_for_all_session_vwap():
    rows = rows_for_day()
    rows["stk_bydd_trd"][0].update(TDD_OPNPRC="50000", TDD_HGPRC="50000", TDD_LWPRC="50000",
                                   ACC_TRDVOL="1", ACC_TRDVAL="70000")
    assert audit.audit_day(DATES[0], rows)["trade_rows"] == 2


def test_success_is_fixed_56_requests_with_private_evidence_and_aggregate_stdout(harness, capsys):
    output, _, calls, sleeps = harness
    assert audit.main(["--output-dir", str(output)]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["status"] == "formations_audited"
    assert report["requests_attempted"] == report["http_200_responses"] == report["schema_valid_responses"] == 56
    assert report["days_audited"] == 14 and report["market_joins_valid"] == 28
    assert report["cap_pass_rows"] == report["cap_pass_common_label_rows"] == 28
    assert report["cap_below_rows"] == report["requests_unattempted"] == 0
    assert [(r.full_url.split("/sto/")[1]) for r in calls] == [
        f"{service}?basDd={day}" for day in DATES for service in probe.SERVICES]
    assert len(sleeps) == 55 and all(delay == 1 for delay in sleeps)
    assert report["universe_ready"] is report["historical_coverage_certified"] is False
    assert report["historical_eligibility_certified"] is False
    assert json.loads((output / "report.json").read_text()) == report
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    for path in output.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert b"test-only-credential" not in path.read_bytes()
    for private in ("fixture-name", "111111", "222222", "5000000000000", "test-only-credential"):
        assert private not in captured.out + captured.err
    started = json.loads((output / "started.json").read_text())
    assert started["specification"] == "krx-large-liquid-formations-v1"
    assert started["calendar_sha256"] == "fceaf0d8748f203001afbf11a66c44aa756f7f55b21e22aaee04152186506ce4"
    assert started["calendar_manifest_sha256"] == "ccdcee1681ad9fa16aef837e4aceaf4d2f40847974046a1714d1352c3df01882"
    assert Decimal(started["market_cap_min_krw"]) == Decimal("5000000000000")


@pytest.mark.parametrize("problem,reason,attempts", [
    ("schema", "schema_failure", 5), ("cap", "capitalization_mismatch", 5),
    ("shares", "listed_shares_mismatch", 7), ("join", "membership_mismatch", 7),
    ("duplicate", "cross_market_duplicate", 6), ("transport", "transport_failure", 5),
])
def test_partial_failure_stops_immediately_and_retains_valid_raw(harness, problem, reason, attempts):
    output, opener, calls, _ = harness
    def mutate(request, payload):
        if DATES[1] not in request.full_url:
            return payload
        row = payload["OutBlock_1"][0]
        if problem == "transport":
            return URLError("test-only-credential")
        if problem == "schema":
            row["MKTCAP"] = "-"
        if problem == "cap":
            row["MKTCAP"] = "1"
        if problem == "duplicate" and "ksq_bydd" in request.full_url:
            row["ISU_CD"] = "111111"
        if "stk_isu_base" in request.full_url:
            if problem == "shares":
                row["LIST_SHRS"] = "1"
            if problem == "join":
                row["ISU_SRT_CD"] = "333333"
        return payload
    opener.mutate = mutate
    result = audit.acquire(output)
    assert result["failure"] == reason and result["status"] == "failed"
    assert result["requests_attempted"] == len(calls) == attempts
    assert result["requests_unattempted"] == 56 - attempts
    assert result["days_audited"] == 1 and result["cap_pass_rows"] == 2
    assert len(list(output.glob("response-*.json"))) == attempts - (problem == "transport")


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 401, 403, 429, 500])
def test_http_failure_never_reads_or_logs_error_body(harness, status, capsys):
    output, opener, calls, _ = harness
    class ErrorBody(io.BytesIO):
        def read(self, *args):
            pytest.fail("HTTP error body must never be read")
    def mutate(request, payload):
        if len(calls) == 5:
            return HTTPError(request.full_url, status, "test-only-credential", {}, ErrorBody(b"test-only-credential"))
        return payload
    opener.mutate = mutate
    assert audit.main(["--output-dir", str(output)]) == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["requests_attempted"] == len(calls) == 5
    assert result["http_200_responses"] == 4 and result["days_audited"] == 1
    assert "test-only-credential" not in captured.out + captured.err
    ledger = [json.loads(line) for line in (output / "requests.jsonl").read_text().splitlines()]
    assert ledger[-1]["http_status"] == status
    assert len(list(output.glob("response-*.json"))) == 4


@pytest.mark.parametrize("payload,reason", [
    (b"<html>bad</html>", "invalid_json"), (b'{"OutBlock_1":[],"OutBlock_1":[]}', "invalid_json"),
    ({"error": "failed"}, "invalid_envelope"), ({"OutBlock_1": []}, "schema_failure"),
    (b'{"OutBlock_1":[{"name":"test-only-credential"}]}', "credential_echo"),
    (b'{"OutBlock_1":[{"name":"\\u0074est-only-credential"}]}', "credential_echo"),
    (b" " * (8 * 1024 * 1024 + 1), "response_too_large"),
    (RuntimeError("test-only-credential"), "unexpected_failure"),
])
def test_bad_envelope_size_or_secret_stops_without_leaking(harness, payload, reason, capsys):
    output, opener, calls, _ = harness
    opener.mutate = lambda request, body: payload
    assert audit.main(["--output-dir", str(output)]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["failure"] == reason and len(calls) == 1
    assert "test-only-credential" not in captured.out + captured.err
    assert len(list(output.glob("response-*.json"))) == (reason == "schema_failure")


@pytest.mark.parametrize("option", ["--key", "--date", "--url", "--header-case-check", "--output"])
def test_only_fixed_output_option_is_allowed(harness, option, capsys):
    output, _, calls, _ = harness
    assert audit.main(["--output-dir", str(output), option, "test-only-credential"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["failure"] == "invalid_arguments"
    assert not calls and not output.exists()
    assert "test-only-credential" not in captured.out + captured.err


@pytest.mark.parametrize("problem", ["credential", "dirty", "existing", "inside_checkout"])
def test_preflight_fails_before_any_network(harness, monkeypatch, problem):
    output, _, calls, _ = harness
    if problem == "credential":
        monkeypatch.delenv("KRX_API_KEY")
    elif problem == "dirty":
        def dirty():
            raise probe.ProbeRefusal("uncommitted_sources")
        monkeypatch.setattr(probe, "source_version", dirty)
    elif problem == "existing":
        output.mkdir()
    else:
        output = probe.ROOT / "forbidden-evidence"
    with pytest.raises(probe.ProbeRefusal):
        audit.acquire(output)
    assert not calls


@pytest.mark.parametrize("stage", ["started", "attempt", "response", "raw", "final", "rename", "sync"])
def test_evidence_failure_stops_and_cannot_publish_success(harness, monkeypatch, stage):
    output, _, calls, _ = harness
    original_persist = probe.persist
    def persist(path, value, **kwargs):
        if ((stage == "started" and path.name == "started.json")
                or (stage in ("attempt", "response") and path.name == "requests.jsonl" and value["event"] == stage)
                or (stage == "raw" and path.name.startswith("response-"))
                or (stage == "final" and path.name == "report.pending.json")):
            raise OSError("test-only-credential")
        original_persist(path, value, **kwargs)
    monkeypatch.setattr(probe, "persist", persist)
    if stage == "rename":
        monkeypatch.setattr(audit.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("test-only-credential")))
    if stage == "sync":
        original_sync = probe.sync_directory
        def sync(path):
            if (output / "report.json").exists():
                raise OSError("test-only-credential")
            original_sync(path)
        monkeypatch.setattr(probe, "sync_directory", sync)
    result = audit.acquire(output)
    expected = 0 if stage in ("started", "attempt") else 1 if stage in ("response", "raw") else 56
    assert result["status"] == "failed" and result["requests_attempted"] == len(calls) == expected
    assert result["http_200_responses"] == expected
    assert "test-only-credential" not in json.dumps(result)
    if stage in ("final", "rename", "sync"):
        assert result["report_persisted"] is False
        assert not (output / "report.json").exists()


def test_failed_final_write_preserves_original_failure_and_counts(harness, monkeypatch):
    output, opener, calls, _ = harness
    opener.mutate = lambda request, payload: URLError("test-only-credential")
    original = probe.persist
    def fail_final(path, value, **kwargs):
        if path.name == "report.pending.json":
            raise OSError("test-only-credential")
        original(path, value, **kwargs)
    monkeypatch.setattr(probe, "persist", fail_final)
    result = audit.acquire(output)
    assert result["failure"] == "transport_failure" and result["evidence_write_failure"] is True
    assert result["requests_attempted"] == len(calls) == 1 and result["requests_unattempted"] == 55
