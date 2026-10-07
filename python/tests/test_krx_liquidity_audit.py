"""Fixed-window liquidity audit contracts using synthetic private evidence only."""

from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import io
import json
import stat
from urllib.error import HTTPError, URLError

import pytest

from data import krx_formation_audit as formation
from data import krx_liquidity_audit as audit
from data import krx_openapi_probe as probe
from test_krx_formation_audit import rows_for_day


def synthetic_calendar():
    anchors = {0: "20190102", 1894: "20260918", 1895: "20260921",
               1896: "20260922", 1897: "20260923"}
    anchors.update({60 + 126 * i: day for i, day in enumerate(formation.DATES)})
    calendar = [None] * 1898
    ordered = sorted(anchors.items())
    for (left, start), (right, end) in zip(ordered, ordered[1:]):
        date = datetime.strptime(start, "%Y%m%d")
        span = (datetime.strptime(end, "%Y%m%d") - date).days
        for index in range(left, right):
            calendar[index] = (date + timedelta(days=(index-left)*span//(right-left))).strftime("%Y%m%d")
    calendar[-1] = ordered[-1][1]
    return calendar


def pin(monkeypatch, name, path):
    monkeypatch.setattr(audit, name, hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    calendar = synthetic_calendar()
    windows = {day: calendar[calendar.index(day)-60:calendar.index(day)] for day in formation.DATES}
    union = sorted(set(formation.DATES).union(*(set(window) for window in windows.values())))
    matrix = [{"bas_dd": day, "service": service} for day in union for service in probe.SERVICES[:2]]
    matrix += [{"bas_dd": day, "service": service} for day in formation.DATES for service in probe.SERVICES[2:]]
    manifest = tmp_path / "calendar.json"
    probe.persist(manifest, {"calendar_dates": calendar, "formation_dates": list(formation.DATES),
                            "not_execution_authorization": True, "type_history_coverage_complete": False,
                            "proposed_minimum_request_matrix": sorted(matrix, key=lambda item: (item["bas_dd"], item["service"]))})
    pin(monkeypatch, "CALENDAR_MANIFEST_SHA256", manifest)
    monkeypatch.setattr(audit, "CALENDAR_SHA256", hashlib.sha256(probe.canonical(calendar)).hexdigest())
    cache = tmp_path / "formation"
    cache.mkdir(mode=0o700)
    report = formation._initial_report()
    report.update(status="formations_audited", requests_attempted=56, requests_unattempted=0,
                  http_200_responses=56, schema_valid_responses=56, days_audited=14,
                  market_joins_valid=28, report_persisted=True)
    formation_matrix = [{"service": service, "bas_dd": day} for day in formation.DATES for service in probe.SERVICES]
    probe.persist(cache / "started.json", {**formation._initial_report(), "request_matrix": formation_matrix})
    for number, item in enumerate(formation_matrix, 1):
        day, service = item["bas_dd"], item["service"]
        rows = rows_for_day(day)[service]
        if service in probe.SERVICES[:2]:
            rows[0].update(TDD_OPNPRC="50000", TDD_HGPRC="50000", TDD_LWPRC="50000", ACC_TRDVAL="10000000000")
        if service == "ksq_isu_base_info":
            rows[0]["KIND_STKCERT_TP_NM"] = "우선주"
        body = probe.canonical({"OutBlock_1": rows})
        probe.persist(cache / f"response-{number:02d}-{service}-{day}.json", body, raw=True)
        observation = formation._summarize(rows, service, day)
        observation["response_sha256"] = hashlib.sha256(body).hexdigest()
        report["observations"].append(observation)
        for event in ("attempt", "response"):
            probe.persist(cache / "requests.jsonl", {**item, "number": number, "event": event,
                                                   "http_status": 200 if event == "response" else None}, append=True)
    for day in formation.DATES:
        rows = rows_for_day(day)
        rows["ksq_isu_base_info"][0]["KIND_STKCERT_TP_NM"] = "우선주"
        result = formation.audit_day(day, rows)
        report["days"].append(result)
        for field in ("cap_pass_rows", "cap_below_rows", "cap_pass_common_label_rows"):
            report[field] += result[field]
    probe.persist(cache / "report.json", report)
    for name, filename in (("FORMATION_REPORT_SHA256", "report.json"),
                           ("FORMATION_STARTED_SHA256", "started.json"),
                           ("FORMATION_LEDGER_SHA256", "requests.jsonl")):
        pin(monkeypatch, name, cache / filename)
    return manifest, cache, tmp_path / "output", windows


@pytest.fixture
def harness(inputs, monkeypatch):
    manifest, cache, output, windows = inputs
    monkeypatch.setenv("KRX_API_KEY", "test-only-credential")
    monkeypatch.setattr(probe, "source_version", lambda: "a" * 40)
    monkeypatch.setattr(audit.shutil, "disk_usage", lambda path: (4 << 30, 0, 4 << 30))
    sleeps, calls = [], []
    monkeypatch.setattr(audit.time, "sleep", sleeps.append)

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
            assert len(started["request_matrix"]) == 1680
            assert started["source_sha"] == "a" * 40
            last = json.loads((output / "requests.jsonl").read_text().splitlines()[-1])
            assert last["event"] == "attempt" and last["number"] == len(calls) + 1
            assert timeout == 20 and request.get_method() == "GET"
            calls.append(request)
            service, day = request.full_url.split("/sto/")[1].split("?basDd=")
            rows = rows_for_day(day)[service]
            rows[0].update(TDD_OPNPRC="50000", TDD_HGPRC="50000", TDD_LWPRC="50000", ACC_TRDVAL="10000000000")
            payload = self.mutate(request, {"OutBlock_1": rows})
            if isinstance(payload, BaseException):
                raise payload
            return Response(payload if isinstance(payload, bytes) else probe.canonical(payload), request.full_url)

    opener = Opener()
    monkeypatch.setattr(probe, "make_opener", lambda: opener)
    return inputs, opener, calls, sleeps


def test_calendar_exact_windows_exclude_current_and_future(inputs):
    manifest, _, _, expected = inputs
    windows, matrix = audit.load_calendar(manifest)
    assert windows == expected
    assert all(len(window) == 60 and max(window) < day for day, window in windows.items())
    assert windows[formation.DATES[0]][0] == "20190102"
    assert len(matrix) == len({(row["service"], row["bas_dd"]) for row in matrix}) == 1680
    assert len({row["bas_dd"] for row in matrix}) == 840
    assert not set(formation.DATES) & {row["bas_dd"] for row in matrix}
    assert [row["service"] for row in matrix[:2]] == list(probe.SERVICES[:2])


@pytest.mark.parametrize("mutation", ["hash", "duplicate", "invalid_date", "formation", "matrix", "flag", "count"])
def test_calendar_refuses_changed_or_inconsistent_evidence(inputs, monkeypatch, mutation):
    manifest, _, _, _ = inputs
    payload = json.loads(manifest.read_bytes())
    if mutation in ("hash", "duplicate"):
        payload["calendar_dates"][1] = payload["calendar_dates"][0]
    elif mutation == "invalid_date":
        payload["calendar_dates"][1] = "20190230"
    elif mutation == "formation":
        payload["formation_dates"][0] = "20190403"
    elif mutation == "matrix":
        payload["proposed_minimum_request_matrix"].pop()
    elif mutation == "flag":
        payload["not_execution_authorization"] = "true"
    else:
        payload["calendar_dates"].pop()
    manifest.write_bytes(probe.canonical(payload))
    if mutation != "hash":
        pin(monkeypatch, "CALENDAR_MANIFEST_SHA256", manifest)
        monkeypatch.setattr(audit, "CALENDAR_SHA256", hashlib.sha256(probe.canonical(payload["calendar_dates"])).hexdigest())
    with pytest.raises(probe.ProbeRefusal):
        audit.load_calendar(manifest)


def target():
    return {"formation": formation.DATES[0], "code": "111111", "source_common_label": True,
            "reported_listing_date": "20190115", "formation_tradability_proxy": True}


def history(value="10000000000"):
    dates = synthetic_calendar()[:60]
    return dates, {day: {"turnover": Decimal(value), "equal_ohlc_zero_turnover": False, "zero_ohl": False} for day in dates}


def test_exact_even_median_keeps_precision_beyond_default_context():
    dates, observations = history("9999999999.999999999999999999999999999999")
    for day in dates[30:]:
        observations[day]["turnover"] = Decimal("10000000000.000000000000000000000000000001")
    result = audit.summarize_history(target(), dates, observations, set(dates))
    assert Decimal(result["median_traded_value_krw"]) == Decimal("10000000000")
    assert result["liquidity_pass"] is True and result["observed_count"] == 60


def test_verified_zero_differs_from_missing_and_listing_date_does_not_resolve_it():
    dates, observations = history()
    observations[dates[0]].update(turnover=Decimal(0), equal_ohlc_zero_turnover=True, zero_ohl=True)
    complete = audit.summarize_history(target(), dates, observations, set(dates))
    assert complete["zero_turnover_count"] == complete["equal_ohlc_zero_turnover_count"] == complete["zero_ohl_count"] == 1
    assert complete["liquidity_pass"] is True
    del observations[dates[0]]
    missing = audit.summarize_history(target(), dates, observations, set(dates))
    assert missing["observed_count"] == 59 and missing["missing_count"] == 1
    assert missing["missing_before_reported_listing_count"] == 1
    assert missing["median_traded_value_krw"] is None and missing["liquidity_pass"] is None
    assert "unresolved_history" in missing["reasons"]
    assert missing["zero_turnover_count"] == 0


def test_full_audit_is_fixed_private_and_uncertified(harness, capsys):
    inputs, _, calls, sleeps = harness
    manifest, cache, output, windows = inputs
    assert audit.main(["--calendar-manifest", str(manifest), "--formation-dir", str(cache), "--output-dir", str(output)]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["status"] == "liquidity_audited"
    assert result["requests_attempted"] == result["schema_valid_responses"] == len(calls) == 1680
    assert result["days_audited"] == 840 and result["coverage_complete"] is True
    assert result["totals"]["cap_pass_rows"] == result["totals"]["liquidity_pass_rows"] == 28
    assert result["common_label_totals"]["cap_pass_rows"] == 14
    assert len(sleeps) == 1679 and set(sleeps) == {1}
    dates = sorted(set().union(*(set(window) for window in windows.values())))
    assert [r.full_url for r in calls] == [f"{probe.HOST}/svc/apis/sto/{service}?basDd={day}" for day in dates for service in probe.SERVICES[:2]]
    for flag in formation._uncertified_flags():
        assert result[flag] is False
    candidate_bytes = (output / "candidates.json").read_bytes()
    assert hashlib.sha256(candidate_bytes).hexdigest() == result["candidates_sha256"]
    assert len(json.loads(candidate_bytes)["candidates"]) == 28
    assert json.loads((output / "report.json").read_bytes()) == result
    for path in output.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for private in ("fixture-name", "111111", "222222", "test-only-credential", "median_traded_value_krw"):
        assert private not in captured.out + captured.err


@pytest.mark.parametrize("problem", ["report", "raw", "missing", "symlink", "permissions", "oversized", "started", "ledger"])
def test_cache_preflight_refuses_before_network(harness, problem):
    inputs, _, calls, _ = harness
    manifest, cache, output, _ = inputs
    path = cache / "response-01-stk_bydd_trd-20190402.json"
    if problem in ("report", "started", "ledger"):
        path = cache / {"report": "report.json", "started": "started.json", "ledger": "requests.jsonl"}[problem]
        path.write_bytes(path.read_bytes() + b" ")
    elif problem == "raw":
        path.write_bytes(path.read_bytes() + b" ")
    elif problem == "missing":
        path.unlink()
    elif problem == "symlink":
        other = cache / "other.json"
        path.rename(other)
        path.symlink_to(other)
    elif problem == "permissions":
        path.chmod(0o644)
    else:
        path.write_bytes(b" " * (8 * 1024 * 1024 + 1))
    with pytest.raises(probe.ProbeRefusal):
        audit.acquire(manifest, cache, output)
    assert not calls and not output.exists()


@pytest.mark.parametrize("problem,reason", [("transport", "transport_failure"), ("schema", "schema_failure"),
                                          ("cap", "capitalization_mismatch"), ("duplicate", "cross_market_duplicate"),
                                          ("oversized", "response_too_large"), ("secret", "credential_echo")])
def test_partial_failure_no_retry_and_raw_before_field_validation(harness, problem, reason):
    inputs, opener, calls, _ = harness
    manifest, cache, output, _ = inputs
    def mutate(request, payload):
        if len(calls) != 4:
            return payload
        if problem == "transport":
            return URLError("test-only-credential")
        if problem == "oversized":
            return b" " * (8 * 1024 * 1024 + 1)
        row = payload["OutBlock_1"][0]
        row[{"schema": "ACC_TRDVAL", "cap": "MKTCAP", "duplicate": "ISU_CD", "secret": "ISU_NM"}[problem]] = {
            "schema": "-", "cap": "1", "duplicate": "111111", "secret": "test-only-credential"}[problem]
        return payload
    opener.mutate = mutate
    result = audit.acquire(manifest, cache, output)
    assert result["status"] == "failed" and result["failure"] == reason
    assert result["requests_attempted"] == len(calls) == 4 and result["requests_unattempted"] == 1676
    assert result["days_audited"] == 1
    assert len(list(output.glob("response-*.json"))) == (4 if problem in ("schema", "cap", "duplicate") else 3)


def test_low_disk_stops_before_attempt(harness, monkeypatch):
    inputs, _, calls, _ = harness
    manifest, cache, output, _ = inputs
    monkeypatch.setattr(audit.shutil, "disk_usage", lambda path: (4 << 30, 3 << 30, (2 << 30) - 1))
    result = audit.acquire(manifest, cache, output)
    assert result["failure"] == "low_disk_space" and not calls
    assert not (output / "requests.jsonl").exists()


@pytest.mark.parametrize("stage", ["started", "attempt", "response", "raw", "candidates", "candidate_rename", "final", "report_rename", "sync"])
def test_evidence_failures_cannot_leave_success(harness, monkeypatch, stage):
    inputs, opener, calls, _ = harness
    manifest, cache, output, _ = inputs
    original = probe.persist
    def persist(path, value, **kwargs):
        if ((stage == "started" and path.name == "started.json")
                or (stage in ("attempt", "response") and path.name == "requests.jsonl" and value["event"] == stage)
                or (stage == "raw" and path.name.startswith("response-"))
                or (stage == "candidates" and path.name == "candidates.pending.json")
                or (stage == "final" and path.name == "report.pending.json")):
            raise OSError("test-only-credential")
        original(path, value, **kwargs)
    monkeypatch.setattr(probe, "persist", persist)
    replace = audit.os.replace
    def fail_replace(src, dest):
        if (stage == "candidate_rename" and dest.name == "candidates.json") or (stage == "report_rename" and dest.name == "report.json"):
            raise OSError("test-only-credential")
        replace(src, dest)
    monkeypatch.setattr(audit.os, "replace", fail_replace)
    sync = probe.sync_directory
    def fail_sync(path):
        if stage == "sync" and (output / "report.json").exists():
            raise OSError("test-only-credential")
        sync(path)
    monkeypatch.setattr(probe, "sync_directory", fail_sync)
    result = audit.acquire(manifest, cache, output)
    assert result["status"] == "failed" and "test-only-credential" not in json.dumps(result)
    if stage in ("final", "report_rename", "sync"):
        assert result["report_persisted"] is False
        assert not (output / "report.json").exists() and not (output / "candidates.json").exists()
    if stage in ("candidates", "candidate_rename"):
        assert result["candidates_persisted"] is False


def test_missing_target_and_market_transfer_finish_fixed_matrix(harness):
    inputs, opener, calls, _ = harness
    manifest, cache, output, windows = inputs
    window = windows[formation.DATES[0]]
    def mutate(request, payload):
        service, day = request.full_url.split("/sto/")[1].split("?basDd=")
        row = payload["OutBlock_1"][0]
        if day not in window:
            return payload
        if window.index(day) >= 30:
            row["ISU_CD"] = "222222" if service == "stk_bydd_trd" else "111111"
        if day == window[0] and row["ISU_CD"] == "111111":
            row["ISU_CD"] = "333333"  # Another code cannot fill the missing observation.
        if row["ISU_CD"] == "222222" and window.index(day) < 30:
            row["ACC_TRDVAL"] = "0"
            if day == window[1]:
                row["TDD_OPNPRC"] = "0"
        return payload
    opener.mutate = mutate
    result = audit.acquire(manifest, cache, output)
    assert result["status"] == "liquidity_audited" and len(calls) == 1680
    assert result["coverage_complete"] is False
    assert result["totals"]["incomplete_history_rows"] == 1
    assert result["totals"]["liquidity_pass_rows"] == 26 and result["totals"]["liquidity_below_rows"] == 1
    assert result["common_label_totals"]["incomplete_history_rows"] == 1
    first, second = json.loads((output / "candidates.json").read_bytes())["candidates"][:2]
    assert first["code"] == "111111" and first["observed_count"] == 59
    assert first["missing_dates"] == [window[0]] and not first["uncollected_dates"]
    assert first["liquidity_pass"] is first["median_traded_value_krw"] is None
    assert second["code"] == "222222" and second["observed_count"] == 60
    assert Decimal(second["median_traded_value_krw"]) == 5_000_000_000
    assert second["zero_turnover_count"] == 30 and second["equal_ohlc_zero_turnover_count"] == 29
    assert second["zero_ohl_count"] == 1 and second["liquidity_pass"] is False


def change_cached_row(cache, monkeypatch, updates):
    path = cache / "response-01-stk_bydd_trd-20190402.json"
    payload = json.loads(path.read_bytes())
    payload["OutBlock_1"][0].update(updates)
    path.write_bytes(probe.canonical(payload))
    report_path = cache / "report.json"
    report = json.loads(report_path.read_bytes())
    report["observations"][0]["response_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report_path.write_bytes(probe.canonical(report))
    pin(monkeypatch, "FORMATION_REPORT_SHA256", report_path)


@pytest.mark.parametrize("updates,expected", [
    ({"ACC_TRDVAL": "0"}, False),
    ({"ACC_TRDVAL": "1", "ACC_TRDVOL": "0"}, True),
    ({"ACC_TRDVAL": "0", "TDD_HGPRC": "50001"}, True),
    ({"TDD_OPNPRC": "0"}, False), ({"TDD_HGPRC": "0"}, False), ({"TDD_LWPRC": "0"}, False),
])
def test_formation_proxy_distinguishes_frozen_locked_and_zero_ohl(inputs, monkeypatch, updates, expected):
    _, cache, _, _ = inputs
    change_cached_row(cache, monkeypatch, updates)
    targets = audit.load_formation_cache(cache, "test-only-credential")
    assert len(targets) == 28 and sum(item["source_common_label"] for item in targets) == 14
    assert targets[0]["formation_tradability_proxy"] is expected


@pytest.mark.parametrize("updates", [{"MKTCAP": "1"}, {"LIST_SHRS": "100000001"},
                                    {"BAS_DD": "20190403"}, {"ISU_CD": "333333"}, {"ACC_TRDVOL": "0.5"}])
def test_cache_rows_are_reaudited_even_with_matching_raw_hash(inputs, monkeypatch, updates):
    _, cache, _, _ = inputs
    change_cached_row(cache, monkeypatch, updates)
    with pytest.raises(probe.ProbeRefusal):
        audit.load_formation_cache(cache, "test-only-credential")


@pytest.mark.parametrize("field,value", [("status", "started"), ("days_audited", 13), ("cap_pass_rows", 27),
                                      ("universe_ready", True), ("historical_coverage_certified", True),
                                      ("historical_eligibility_certified", True)])
def test_formation_report_requires_success_and_false_flags(inputs, monkeypatch, field, value):
    _, cache, _, _ = inputs
    path = cache / "report.json"
    report = json.loads(path.read_bytes())
    report[field] = value
    path.write_bytes(probe.canonical(report))
    pin(monkeypatch, "FORMATION_REPORT_SHA256", path)
    with pytest.raises(probe.ProbeRefusal):
        audit.load_formation_cache(cache, "test-only-credential")


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_http_errors_never_read_body_or_retry(harness, status, capsys):
    inputs, opener, calls, _ = harness
    manifest, cache, output, _ = inputs
    class ErrorBody(io.BytesIO):
        def read(self, *args):
            pytest.fail("HTTP error body must not be read")
    opener.mutate = lambda request, payload: HTTPError(request.full_url, status, "test-only-credential", {}, ErrorBody(b"test-only-credential"))
    assert audit.main(["--calendar-manifest", str(manifest), "--formation-dir", str(cache), "--output-dir", str(output)]) == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["requests_attempted"] == len(calls) == 1 and result["http_200_responses"] == 0
    assert not list(output.glob("response-*.json")) and not (output / "candidates.json").exists()
    assert "test-only-credential" not in captured.out + captured.err
    ledger = [json.loads(line) for line in (output / "requests.jsonl").read_text().splitlines()]
    assert [row["event"] for row in ledger] == ["attempt", "response"]
    assert ledger[-1]["http_status"] == status


@pytest.mark.parametrize("option", ["--date", "--url", "--key", "--resume", "--calendar", "--output"])
def test_cli_only_accepts_three_evidence_paths(harness, capsys, option):
    inputs, _, calls, _ = harness
    manifest, cache, output, _ = inputs
    assert audit.main(["--calendar-manifest", str(manifest), "--formation-dir", str(cache), "--output-dir", str(output), option, "test-only-credential"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["failure"] == "invalid_arguments"
    assert not calls and not output.exists() and "test-only-credential" not in captured.out + captured.err


@pytest.mark.parametrize("payload,reason", [(b"<html>failure</html>", "invalid_json"),
                                        (b'{"OutBlock_1":[],"OutBlock_1":[]}', "invalid_json"),
                                        ({"OutBlock_1": []}, "schema_failure"),
                                        ({"error": "test-only-credential"}, "credential_echo"),
                                        ({"wrong": []}, "invalid_envelope")])
def test_invalid_envelopes_are_bounded_and_secret_safe(harness, payload, reason):
    inputs, opener, calls, _ = harness
    manifest, cache, output, _ = inputs
    opener.mutate = lambda request, original: payload
    result = audit.acquire(manifest, cache, output)
    assert result["failure"] == reason and len(calls) == 1
    assert "test-only-credential" not in json.dumps(result)


def test_disk_is_rechecked_at_each_attempt_and_equality_is_allowed(harness, monkeypatch):
    inputs, _, calls, _ = harness
    manifest, cache, output, _ = inputs
    monkeypatch.setattr(audit.shutil, "disk_usage", lambda path: (4 << 30, 2 << 30, (2 << 30) - (len(calls) == 2)))
    result = audit.acquire(manifest, cache, output)
    assert result["failure"] == "low_disk_space" and len(calls) == 2 and result["days_audited"] == 1
    assert len((output / "requests.jsonl").read_text().splitlines()) == 4
    assert not (output / "candidates.json").exists()


def test_report_write_failure_preserves_transport_failure_and_partial_counts(harness, monkeypatch):
    inputs, opener, calls, _ = harness
    manifest, cache, output, _ = inputs
    opener.mutate = lambda request, payload: URLError("test-only-credential") if len(calls) == 3 else payload
    original = probe.persist
    def persist(path, value, **kwargs):
        if path.name == "report.pending.json":
            raise OSError("test-only-credential")
        original(path, value, **kwargs)
    monkeypatch.setattr(probe, "persist", persist)
    result = audit.acquire(manifest, cache, output)
    assert result["failure"] == "transport_failure" and result["evidence_write_failure"] is True
    assert result["requests_attempted"] == 3 and result["http_200_responses"] == 2 and result["days_audited"] == 1
    assert result["report_persisted"] is False and not (output / "candidates.json").exists()


@pytest.mark.parametrize("problem", ["59", "61", "formation", "future", "duplicate", "outside_observation"])
def test_history_never_shortens_extends_or_includes_formation(problem):
    dates, observations = history()
    if problem == "59":
        dates.pop()
    elif problem == "61":
        dates.append("20190401")
    elif problem in ("formation", "future"):
        dates[-1] = "20190402" if problem == "formation" else "20190403"
    elif problem == "duplicate":
        dates[-1] = dates[0]
    else:
        observations["20190402"] = observations[dates[0]]
    with pytest.raises(probe.ProbeRefusal, match="invalid_history_window"):
        audit.summarize_history(target(), dates, observations, set(dates))


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1"])
def test_invalid_turnover_cannot_become_zero(value):
    dates, observations = history()
    observations[dates[0]]["turnover"] = Decimal(value)
    with pytest.raises(probe.ProbeRefusal, match="invalid_history_observation"):
        audit.summarize_history(target(), dates, observations, set(dates))


def test_all_real_zeros_are_complete_below_threshold():
    dates, observations = history("0")
    result = audit.summarize_history(target(), dates, observations, set(dates))
    assert result["missing_count"] == 0 and result["zero_turnover_count"] == 60
    assert result["median_traded_value_krw"] == "0" and result["liquidity_pass"] is False


def test_listing_boundary_and_uncollected_dates_remain_explicit():
    dates, observations = history()
    subject = target()
    subject["reported_listing_date"] = dates[1]
    for day in dates[:3]:
        observations.pop(day)
    result = audit.summarize_history(subject, dates, observations, set(dates[1:]))
    assert result["missing_before_reported_listing_count"] == 1
    assert result["missing_on_or_after_reported_listing_count"] == 2
    assert result["uncollected_dates"] == [dates[0]]
    assert result["liquidity_pass"] is None


def test_malformed_non_target_row_also_stops(harness):
    inputs, opener, calls, _ = harness
    manifest, cache, output, _ = inputs
    def mutate(request, payload):
        extra = dict(payload["OutBlock_1"][0], ISU_CD="333333", ACC_TRDVAL="-")
        payload["OutBlock_1"].append(extra)
        return payload
    opener.mutate = mutate
    result = audit.acquire(manifest, cache, output)
    assert result["failure"] == "schema_failure" and len(calls) == 1
    assert len(list(output.glob("response-*.json"))) == 1


@pytest.mark.parametrize("problem", ["calendar_symlink", "parent_symlink", "hardlink", "nonprivate_directory", "checkout"])
def test_private_input_paths_are_required(inputs, tmp_path, problem):
    manifest, cache, _, _ = inputs
    if problem == "calendar_symlink":
        link = tmp_path / "linked.json"
        link.symlink_to(manifest)
        manifest = link
    elif problem == "parent_symlink":
        link = tmp_path / "linked-directory"
        link.symlink_to(cache, target_is_directory=True)
        with pytest.raises(probe.ProbeRefusal):
            audit.load_formation_cache(link, "test-only-credential")
        return
    elif problem == "hardlink":
        (tmp_path / "calendar-hardlink.json").hardlink_to(manifest)
    elif problem == "nonprivate_directory":
        tmp_path.chmod(0o755)
    else:
        manifest = probe.ROOT / "never-read-calendar.json"
    with pytest.raises(probe.ProbeRefusal):
        audit.load_calendar(manifest)

