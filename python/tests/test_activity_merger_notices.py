"""Counterexamples to certifying a listing from incomplete or conflicting evidence."""

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode

import pytest

from research import activity_merger_notices as am
from research import kind_notices as kn
from test_kind_notices import (
    CORRECTION_MARKER, additional, rename, search_page, search_row, spac_listing, versions,
)


def candidate(**changes):
    result = dict(issuer_id="25359", process_id="20180124000001", name="네오셈",
                  listed_on="2019-01-31", merger_type="spac_survives",
                  candidate_code="253590", candidate_isin="KR7253590004")
    result.update(changes)
    return result


def notice(kind="additional", **changes):
    payloads = {"additional": additional(), "rename": rename(), "spac_listing": spac_listing()}
    numbers = {"additional": "20190130002090", "rename": "20190128001025",
               "spac_listing": "20250707000001"}
    doc_no = numbers[kind]
    result = dict(receipt_no=doc_no, doc_no=doc_no,
                  publication_on=f"{doc_no[:4]}-{doc_no[4:6]}-{doc_no[6:8]}",
                  fields=kn.parse_notice(payloads[kind], kind))
    result.update(changes)
    return result


def disappearing(**changes):
    return candidate(merger_type="spac_disappears", name="뉴키즈온", issuer_id="46231",
                     candidate_code="462310", candidate_isin="KR7462310004",
                     listed_on="2025-07-09", **changes)


def assert_unresolved(result, reason):
    assert result["status"] == "unresolved"
    assert result["reason"] == reason
    assert result["historical_intervals_ready"] is False
    assert result["known_on"] is None


def test_surviving_spac_requires_linked_rename_and_does_not_certify_an_identity_interval():
    listing, renaming = notice(), notice("rename")
    assert_unresolved(am.verify_candidate(candidate(), [listing]), "missing_linked_rename")
    result = am.verify_candidate(candidate(), [renaming, listing])
    assert result["status"] == "listing_identity_verified"
    assert result["operating_name_at_listing"] == "(주)네오셈"
    assert result["notice_evidence_available_on"] == "2019-01-30"
    assert result["historical_intervals_ready"] is False
    assert result["known_on"] is None
    assert [n["fields"]["kind"] for n in result["evidence"]] == ["additional", "rename"]


@pytest.mark.parametrize("field,value", [
    ("before_company_name", "다른 기업인수목적 주식회사"),
    ("listing_on", "2019-02-01"),
])
def test_unrelated_or_different_day_rename_cannot_link_an_additional_listing(field, value):
    renaming = notice("rename")
    renaming["fields"][field] = value
    assert_unresolved(am.verify_candidate(candidate(), [notice(), renaming]), "missing_linked_rename")


@pytest.mark.parametrize("field,value", [("code", "123456"), ("listing_on", "2019-02-01")])
def test_source_code_and_event_day_must_agree_with_candidate(field, value):
    listing = notice()
    listing["fields"][field] = value
    assert_unresolved(am.verify_candidate(candidate(), [listing, notice("rename")]),
                      "no_matching_listing_notice")


@pytest.mark.parametrize("kind,field,value,reason", [
    ("additional", "code", "123456", "conflicting_listing_versions"),
    ("additional", "listing_on", "2019-02-01", "conflicting_listing_versions"),
    ("additional", "company_name", "다른스팩", "conflicting_listing_versions"),
    ("rename", "before_company_name", "다른스팩", "conflicting_rename_versions"),
    ("rename", "after_company_name", "다른회사", "conflicting_rename_versions"),
    ("rename", "listing_on", "2019-02-01", "conflicting_rename_versions"),
])
def test_later_correction_cannot_be_dropped_merely_because_original_matches(kind, field, value, reason):
    notices = [notice(), notice("rename")]
    correction = deepcopy(next(n for n in notices if n["fields"]["kind"] == kind))
    correction.update(doc_no="20190201000001", publication_on="2019-02-01")
    correction["fields"][field] = value
    result = am.verify_candidate(candidate(), [*notices, correction])
    assert_unresolved(result, reason)
    assert correction in result["evidence"]


def test_identical_correction_retains_earliest_actual_publication_without_backdating_listing():
    listing = notice()
    correction = deepcopy(listing)
    correction.update(doc_no="20190201000001", publication_on="2019-02-01")
    result = am.verify_candidate(candidate(), [correction, notice("rename"), listing])
    assert result["status"] == "listing_identity_verified"
    assert result["evidence"][0]["doc_no"] == listing["doc_no"]
    assert result["notice_evidence_available_on"] == "2019-01-30"
    assert result["known_on"] is None


def test_disappearing_spac_checks_isin_and_never_infers_absorbed_spac_code():
    listing = notice("spac_listing")
    result = am.verify_candidate(disappearing(), [listing])
    assert result["status"] == "listing_identity_verified"
    assert result["evidence"][0]["fields"]["absorbed_spac_name"] == "KB제28호스팩"
    assert not {"old_code", "absorbed_spac_code", "exchange_ratio", "effective_on"} & result.keys()
    assert not {"old_code", "absorbed_spac_code", "exchange_ratio", "effective_on"} & listing["fields"].keys()
    mismatch = disappearing()
    mismatch["candidate_isin"] = "KR7123456001"
    assert_unresolved(am.verify_candidate(mismatch, [listing]), "current_isin_conflict")
    correction = deepcopy(listing)
    correction.update(doc_no="20250708000001", publication_on="2025-07-08")
    correction["fields"]["isin"] = "KR7123456001"
    assert_unresolved(am.verify_candidate(disappearing(), [listing, correction]),
                      "conflicting_listing_versions")


def test_alphanumeric_code_stays_explicit_while_opaque_issuer_is_not_a_code_source():
    listing = notice()
    listing["fields"] = kn.parse_notice(additional(code="A0004V0"), "additional")
    result = am.verify_candidate(candidate(candidate_code="0004V0", issuer_id="other"),
                                 [listing, notice("rename")])
    assert result["status"] == "listing_identity_verified"
    assert result["candidate_code"] == "0004V0"
    assert result["issuer_id"] == "other"


class SavedResponses:
    """An exact request map: unexpected requests fail rather than use the network."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, form=None):
        key = (url, am.encoded(form))
        self.calls.append(key)
        assert key in self.responses, f"unexpected request: {url}"
        payload = self.responses[key]
        return payload, {"sha256": am.sha(payload)}


def viewer_url(method, **params):
    return am.VIEWER + "?" + urlencode({"method": method, **params})


def acquisition_responses(*, correction=False, issuer="25359", title="추가상장(타법인흡수합병)"):
    row = search_row(receipt="20190130000781", issuer=issuer).replace("추가상장(타법인흡수합병)", title)
    rename_row = search_row(receipt="20190128001025", ordinal=1).replace(
        "추가상장(타법인흡수합병)", "변경상장(상호변경)")
    listing_row = row.replace("<td>1</td>", "<td>2</td>", 1)
    responses = {(am.SEARCH, am.encoded(am.search_form("253590", "2019-01-31", 1))):
                 search_page(listing_row + rename_row, total=2)}
    for receipt, doc_no, body, label in [
        ("20190130000781", "20190130002090", additional(), "추가상장"),
        ("20190128001025", "20190128001025", rename(), "변경상장(상호변경)"),
    ]:
        day = f"{doc_no[:4]}.{doc_no[4:6]}.{doc_no[6:8]}"
        options = f"<option value='{doc_no}|Y'>{label} ({day})</option>"
        documents = [(doc_no, body)]
        if correction and label == "추가상장":
            options += "<option value='20190201000001|Y'>[정정]추가상장 (2019.02.01)</option>"
            documents.append(("20190201000001", b"<html>unrecognized correction body</html>"))
        identity = (f"<input type='hidden' id='acptNo' name='acptNo' value='{receipt}'>"
                    f"<input type='hidden' id='acptNo' name='acptNo' value='{receipt}'>"
                    f'<script>var _TRK_PN = "{receipt}";</script>').encode()
        responses[(viewer_url("search", acptno=receipt), am.encoded(None))] = versions(options).replace(
            b"</body>", identity + b"</body>")
        for number, document in documents:
            path = f"/external/{number[:4]}/{number[4:6]}/{number[6:8]}/000001/{number}/70791"
            url = "https://kind.krx.co.kr" + path + ".htm"
            content = f"<script>parent.setPath('','{url}','{path}','03','11');</script>".encode()
            responses[(viewer_url("searchContents", docNo=number), am.encoded(None))] = content
            responses[(url, am.encoded(None))] = document
    return responses


def test_acquisition_connects_real_parsers_and_retains_each_body_hash():
    evidence = SavedResponses(acquisition_responses())
    result = am.acquire_candidate(candidate(), evidence)
    assert result["status"] == "listing_identity_verified"
    assert result["search_rows"] == 2
    assert len(result["notices"]) == 2
    for item in result["notices"]:
        body = evidence.responses[(item["body_url"], am.encoded(None))]
        assert item["body_sha256"] == am.sha(body)
    assert all("fnPopStockPrices" not in call[0] for call in evidence.calls)


def test_unread_correction_keeps_previously_matching_original_unresolved():
    result = am.acquire_candidate(candidate(), SavedResponses(acquisition_responses(correction=True)))
    assert_unresolved(result, "notice_parse_or_identity_failure")
    assert len(result["notices"]) == 2
    assert result["parse_failures"][0]["doc_no"] == "20190201000001"
    assert "operating_name_at_listing" not in result
    assert "notice_evidence_available_on" not in result


def parsed_correction(monkeypatch, *, publication_on="2019-02-01", changes=None):
    """Isolate acquisition's metadata contract from correction HTML parsing."""
    original_parser = am.parse_notice
    correction = {"publication_on": publication_on, "original_submission_on": "2019-01-30",
                  "items": [{"field": "6.기타", "before": "-", "after": "보호예수 내역 보완"}]}

    def parse(body, kind):
        if body != b"<html>unrecognized correction body</html>":
            return original_parser(body, kind)
        fields = original_parser(additional(), kind)
        fields.update(changes or {})
        fields["correction"] = deepcopy(correction)
        return fields

    monkeypatch.setattr(am, "parse_notice", parse)
    return correction


def test_correction_metadata_is_preserved_outside_identity_without_false_conflict(monkeypatch):
    correction = parsed_correction(monkeypatch)
    result = am.acquire_candidate(candidate(), SavedResponses(acquisition_responses(correction=True)))
    assert result["status"] == "listing_identity_verified"
    assert result["parse_failures"] == []
    assert len(result["notices"]) == 3
    documents = {item["doc_no"]: item for item in result["notices"]}
    corrected = documents["20190201000001"]
    original = documents["20190130002090"]
    assert corrected["correction"] == correction
    assert "correction" not in corrected["fields"]
    assert corrected["fields"] == original["fields"]
    assert original["correction"] is None
    assert corrected["publication_on"] == "2019-02-01"
    assert result["notice_evidence_available_on"] == "2019-01-30"


@pytest.mark.parametrize("field,value", [
    ("company_name", "다른 기업인수목적 주식회사"),
    ("listing_on", "2019-02-04"),
    ("code", "123456"),
])
def test_separating_correction_metadata_does_not_hide_changed_listing_facts(monkeypatch, field, value):
    correction = parsed_correction(monkeypatch, changes={field: value})
    result = am.acquire_candidate(candidate(), SavedResponses(acquisition_responses(correction=True)))
    assert_unresolved(result, "conflicting_listing_versions")
    corrected = next(item for item in result["evidence"] if item["doc_no"] == "20190201000001")
    assert corrected["fields"][field] == value
    assert corrected["correction"] == correction
    assert "operating_name_at_listing" not in result
    assert "notice_evidence_available_on" not in result


def test_correction_publication_mismatch_removes_stale_verified_fields(monkeypatch):
    parsed_correction(monkeypatch, publication_on="2019-02-02")
    result = am.acquire_candidate(candidate(), SavedResponses(acquisition_responses(correction=True)))
    assert_unresolved(result, "notice_parse_or_identity_failure")
    assert len(result["notices"]) == 2
    assert result["parse_failures"][0]["doc_no"] == "20190201000001"
    assert result["parse_failures"][0]["reason"] == "correction publication date disagrees with viewer"
    assert "operating_name_at_listing" not in result
    assert "notice_evidence_available_on" not in result


@pytest.mark.parametrize("mismatch", ["issuer", "submitter"])
def test_wrong_issuer_cannot_be_rescued_by_a_matching_body(mismatch):
    responses = acquisition_responses(issuer="other" if mismatch == "issuer" else "25359")
    if mismatch == "submitter":
        key = (am.SEARCH, am.encoded(am.search_form("253590", "2019-01-31", 1)))
        responses[key] = responses[key].replace("코스닥시장본부".encode(), "다른제출법인".encode(), 1)
    evidence = SavedResponses(responses)
    result = am.acquire_candidate(candidate(), evidence)
    assert_unresolved(result, "notice_parse_or_identity_failure")
    assert result["parse_failures"][0]["reason"] == "unexpected_notice_issuer"
    assert (viewer_url("search", acptno="20190130000781"), am.encoded(None)) not in evidence.calls


def test_unrecognized_notice_title_is_a_reference_and_not_a_verified_listing():
    result = am.acquire_candidate(candidate(), SavedResponses(acquisition_responses(title="추가상장(무상증자)")))
    assert_unresolved(result, "no_matching_listing_notice")
    assert [n["fields"]["kind"] for n in result["notices"]] == ["rename"]


@pytest.mark.parametrize("title", ["SPAC소멸합병상장(2023.2.17)", "[정정]SPAC소멸합병상장(2023.02.17)"])
def test_dated_spac_listing_title_is_recognized(title):
    assert am.notice_kind(title, "spac_disappears") == "spac_listing"


@pytest.mark.parametrize("title", [
    "SPAC소멸합병상장(2023.2.17) 기준가격 안내", "SPAC소멸합병상장 기준가격 안내",
    "SPAC소멸합병상장(2023.2.17)(기준가격)",
])
def test_price_announcements_are_not_fetched_as_spac_listing_notices(title):
    assert am.notice_kind(title, "spac_disappears") is None


def test_reported_later_correction_missing_from_viewer_stops_before_body_fetch():
    responses = acquisition_responses()
    key = (am.SEARCH, am.encoded(am.search_form("253590", "2019-01-31", 1)))
    responses[key] = responses[key].replace(
        "추가상장(타법인흡수합병)</a>".encode(),
        ("추가상장(타법인흡수합병) " + CORRECTION_MARKER + "</a>").encode(), 1)
    evidence = SavedResponses(responses)
    with pytest.raises(kn.KindParseError, match="later correction absent"):
        am.acquire_candidate(candidate(), evidence)
    assert evidence.calls == [key, (viewer_url("search", acptno="20190130000781"), am.encoded(None))]


@pytest.mark.parametrize("changed_total,duplicate", [(True, False), (False, True)])
def test_pagination_changes_and_repeated_receipts_fail_before_body_acquisition(changed_total, duplicate):
    first_rows = "".join(search_row(receipt=f"20190130{i:06d}", ordinal=101 - i) for i in range(100))
    last_rows = search_row(receipt="20190130000000" if duplicate else "20190130000100")
    if changed_total:
        last_rows = search_row(receipt="20190130000100", ordinal=2) + search_row(receipt="20190130000101")
    responses = {
        (am.SEARCH, am.encoded(am.search_form("253590", "2019-01-31", 1))):
        search_page(first_rows, total=101, pages=2),
        (am.SEARCH, am.encoded(am.search_form("253590", "2019-01-31", 2))):
        search_page(last_rows, total=102 if changed_total else 101, page=2, pages=2),
    }
    with pytest.raises(kn.KindParseError, match="pagination changed|duplicate search"):
        am.acquire_candidate(candidate(), SavedResponses(responses))


def saved_evidence(tmp_path, monkeypatch, *, entries=None, payload=b"<html>corporate metadata</html>"):
    class NoNetwork:
        def open(self, *args, **kwargs):
            pytest.fail("offline evidence opened the network")

    monkeypatch.setattr(am, "build_opener", lambda *args: NoNetwork())
    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    output.mkdir()
    request = {"url": am.SEARCH, "form": {"method": "searchDetailsSub"}, "method": "POST"}
    entry = {"request": request, "file": am.sha(am.encoded(request)) + ".html",
             "sha256": am.sha(payload), "retrieved_at": "2026-10-06T07:00:00+00:00"}
    (source / entry["file"]).write_bytes(payload)
    (source / "sources.jsonl").write_text("".join(json.dumps(item) + "\n" for item in
                                                 ([entry] if entries is None else entries)), encoding="utf-8")
    return source, output, entry


def test_offline_replay_checks_hash_preserves_timestamp_and_never_opens_network(tmp_path, monkeypatch):
    source, output, original = saved_evidence(tmp_path, monkeypatch)
    evidence = am.Evidence(output, source)
    payload, entry = evidence.get(am.SEARCH, original["request"]["form"])
    assert payload == (source / original["file"]).read_bytes()
    assert entry["sha256"] == original["sha256"]
    assert entry["retrieved_at"] == original["retrieved_at"]
    assert entry["transport"] == "saved_response"
    assert evidence.network_requests == 0
    assert evidence.get(am.SEARCH, original["request"]["form"])[0] == payload
    assert len((output / "sources.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize("failure", ["missing", "tamper", "duplicate", "path", "timezone"])
def test_offline_evidence_failures_never_fall_back_to_network(tmp_path, monkeypatch, failure):
    source, output, entry = saved_evidence(tmp_path, monkeypatch)
    if failure == "tamper":
        (source / entry["file"]).write_bytes(b"different metadata")
    elif failure == "path":
        entry["file"] = "../outside.html"
    elif failure == "timezone":
        entry["retrieved_at"] = "2026-10-06T07:00:00"
    lines = [entry, entry] if failure == "duplicate" else [entry]
    (source / "sources.jsonl").write_text("".join(json.dumps(item) + "\n" for item in lines), encoding="utf-8")
    with pytest.raises((ValueError, kn.KindParseError)):
        evidence = am.Evidence(output, source)
        evidence.get(am.SEARCH, {"missing": "request"} if failure == "missing" else entry["request"]["form"])
    assert list(output.iterdir()) == []


def test_explicit_resume_reuses_verified_bytes_and_fetches_only_missing_requests(tmp_path, monkeypatch):
    source, output, original = saved_evidence(tmp_path, monkeypatch)
    source_before = {path.name: path.read_bytes() for path in source.iterdir()}
    calls = []
    new_form = {"method": "searchDetailsSub", "pageIndex": "2"}
    new_payload = b"<html>new public page</html>"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def geturl(self):
            return am.SEARCH

        def read(self, count):
            assert count == am.MAX_BYTES + 1
            return new_payload

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            assert request.full_url == am.SEARCH
            assert request.data == urlencode(new_form).encode()
            assert timeout == 30
            return Response()

    monkeypatch.setattr(am, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(am.time, "sleep", lambda duration: None)
    evidence = am.Evidence(output, source, fetch_missing=True)
    stored_payload, stored = evidence.get(am.SEARCH, original["request"]["form"])
    assert calls == []
    assert stored_payload == source_before[original["file"]]
    assert stored["retrieved_at"] == original["retrieved_at"]
    assert stored["transport"] == "saved_response"
    fetched_payload, fetched = evidence.get(am.SEARCH, new_form)
    assert fetched_payload == new_payload
    assert fetched["sha256"] == am.sha(new_payload)
    assert fetched["transport"] == "https"
    assert datetime.fromisoformat(fetched["retrieved_at"]).utcoffset() is not None
    assert evidence.get(am.SEARCH, new_form) == (fetched_payload, fetched)
    assert len(calls) == evidence.network_requests == 1
    assert [json.loads(line)["transport"] for line in
            (output / "sources.jsonl").read_text().splitlines()] == ["saved_response", "https"]
    assert {path.name: path.read_bytes() for path in source.iterdir()} == source_before


@pytest.mark.parametrize("failure", ["tamper", "missing_file"])
def test_resume_never_refetches_a_corrupt_or_missing_recorded_response(tmp_path, monkeypatch, failure):
    source, output, original = saved_evidence(tmp_path, monkeypatch)
    path = source / original["file"]
    if failure == "tamper":
        path.write_bytes(b"tampered response")
    else:
        path.unlink()
    evidence = am.Evidence(output, source, fetch_missing=True)
    with pytest.raises((ValueError, FileNotFoundError)):
        evidence.get(am.SEARCH, original["request"]["form"])
    assert evidence.network_requests == 0
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("url", [
    "http://kind.krx.co.kr/disclosure/details.do", "https://kind.krx.co.kr.attacker.example/disclosure/details.do",
    "https://kind.krx.co.kr/quotation/prices.do", "https://kind.krx.co.kr/external/../../credentials",
])
def test_transport_allowlist_rejects_other_hosts_or_price_paths_before_open(tmp_path, monkeypatch, url):
    source, output, _ = saved_evidence(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="unexpected public document"):
        am.Evidence(output, source).get(url)


def fake_main_input(tmp_path, monkeypatch):
    monkeypatch.setattr(am, "ROOT", tmp_path)
    path = tmp_path / "candidates.json"
    records = [candidate(issuer_id=f"issuer{i}", process_id=f"20190101{i:06d}",
                         merger_type="spac_survives" if i < 51 else "spac_disappears") for i in range(104)]
    path.write_bytes(am.encoded({"records": records}))
    for relative in ("python/research/kind_notices.py", "python/research/activity_merger_notices.py",
                     ".planning/rd-al-merger-notices.md"):
        fixture = tmp_path / relative
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_bytes(b"synthetic committed source for provenance test\n")
    monkeypatch.setattr(am, "INPUT", path)
    monkeypatch.setattr(am.subprocess, "check_output", lambda *args, **kwargs:
                        "a" * 40 if kwargs.get("text") else b"")
    monkeypatch.setattr(am, "Evidence", lambda *args, **kwargs:
                        type("Offline", (), {"network_requests": 0, "cache": {}})())
    return tmp_path / "run"


def unresolved(c, evidence):
    return {**c, "status": "unresolved", "reason": "unavailable",
            "known_on": None, "historical_intervals_ready": False}


def test_main_retains_every_unresolved_candidate_in_completed_metadata_result(tmp_path, monkeypatch, capsys):
    output = fake_main_input(tmp_path, monkeypatch)
    monkeypatch.setattr(am, "acquire_candidate", unresolved)
    assert am.main(["--output-dir", str(output)]) == 0
    summary = json.loads((output / "result.json").read_text())
    assert summary["candidate_count"] == 104
    assert summary["counts"] == {"unresolved": 104}
    assert summary["historical_intervals_ready"] is False
    assert len(list(output.glob("candidate-*.json"))) == 104
    assert len(capsys.readouterr().out.splitlines()) == 104
    assert (output / "input.json").read_bytes() == am.INPUT.read_bytes()
    started = json.loads((output / "started.json").read_text())
    assert len(started["source_sha256"]) == 4
    for relative, digest in started["source_sha256"].items():
        assert digest == am.sha((tmp_path / relative).read_bytes())


def test_missing_offline_response_aborts_main_instead_of_completing_unresolved_rows(tmp_path, monkeypatch):
    source, _, _ = saved_evidence(tmp_path, monkeypatch)
    evidence_type = am.Evidence
    output = fake_main_input(tmp_path, monkeypatch)
    monkeypatch.setattr(am, "Evidence", evidence_type)
    with pytest.raises(ValueError, match="absent from offline evidence"):
        am.main(["--output-dir", str(output), "--source-dir", str(source)])
    assert not (output / "result.json").exists()
    assert not list(output.glob("candidate-*.json"))
    failure = json.loads((output / "failure.json").read_text())
    assert failure["status"] == "incomplete"
    assert failure["network_requests"] == 0
    assert failure["completed_candidates"] == 0


@pytest.mark.parametrize("option,fetch_missing", [("--source-dir", False), ("--resume-dir", True)])
def test_main_only_enables_missing_fetch_for_explicit_resume(tmp_path, monkeypatch, option, fetch_missing):
    output = fake_main_input(tmp_path, monkeypatch)
    source = tmp_path / "prior-evidence"
    calls = []

    def evidence_factory(destination, saved_source, **kwargs):
        calls.append((destination, saved_source, kwargs))
        return type("Offline", (), {"network_requests": 0, "cache": {}})()

    monkeypatch.setattr(am, "Evidence", evidence_factory)
    monkeypatch.setattr(am, "acquire_candidate", unresolved)
    assert am.main(["--output-dir", str(output), option, str(source)]) == 0
    assert calls == [(output, source, {"fetch_missing": fetch_missing})]
    started = json.loads((output / "started.json").read_text())
    assert started["source_dir"] == str(source)
    assert started["fetch_missing"] is fetch_missing


def test_source_and_resume_modes_are_mutually_exclusive_before_any_work(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(am.subprocess, "check_output", lambda *args, **kwargs:
                        pytest.fail("incompatible modes reached source inspection"))
    output = tmp_path / "run"
    with pytest.raises(SystemExit) as error:
        am.main(["--output-dir", str(output), "--source-dir", "saved", "--resume-dir", "saved"])
    assert error.value.code == 2
    assert not output.exists()
    assert "not allowed with argument" in capsys.readouterr().err


def test_transport_exception_aborts_without_publishing_completed_result(tmp_path, monkeypatch):
    output = fake_main_input(tmp_path, monkeypatch)
    calls = []

    def acquire(c, evidence):
        calls.append(c)
        if len(calls) == 2:
            raise URLError("public endpoint unavailable")
        return unresolved(c, evidence)

    monkeypatch.setattr(am, "acquire_candidate", acquire)
    with pytest.raises(URLError):
        am.main(["--output-dir", str(output)])
    assert len(calls) == 2
    assert (output / "candidate-001.json").exists()
    assert not (output / "candidate-002.json").exists()
    assert not (output / "result.json").exists()
    failure = json.loads((output / "failure.json").read_text())
    assert failure["status"] == "incomplete"
    assert failure["completed_candidates"] == 1


def test_result_publish_failure_leaves_only_pending_bytes_and_an_incomplete_marker(tmp_path, monkeypatch):
    output = fake_main_input(tmp_path, monkeypatch)
    monkeypatch.setattr(am, "acquire_candidate", unresolved)
    original_link = am.os.link

    def fail_result_publish(source, destination):
        if Path(destination).name == "result.json":
            raise OSError("publish interrupted")
        original_link(source, destination)

    monkeypatch.setattr(am.os, "link", fail_result_publish)
    with pytest.raises(OSError, match="publish interrupted"):
        am.main(["--output-dir", str(output)])
    assert not (output / "result.json").exists()
    assert (output / "result.json.pending").exists()
    assert json.loads((output / "failure.json").read_text())["status"] == "incomplete"


def test_write_new_cannot_overwrite_existing_evidence_or_publish_failed_flush(tmp_path, monkeypatch):
    existing = tmp_path / "evidence.html"
    existing.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        am.write_new(existing, b"replacement")
    assert existing.read_bytes() == b"original"
    monkeypatch.setattr(am.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("flush failed")))
    result = tmp_path / "result.json"
    with pytest.raises(OSError, match="flush failed"):
        am.write_new(result, b"complete bytes")
    assert not result.exists()
