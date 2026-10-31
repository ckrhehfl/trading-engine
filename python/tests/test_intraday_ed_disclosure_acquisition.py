"""Invented-only transport/storage evidence; no external or actual-data access."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from urllib import error, parse

import pytest

from research import intraday_ed_disclosure_acquisition as acquisition
from research import intraday_ed_disclosures as listing


def invented_page(page=1, total=2, *, corporate="00123456", amendment=True):
    rows = []
    for number in range(100 * (page - 1) + 1, min(100 * page, total) + 1):
        receipt = "20260315" + f"{number:06d}"
        title = ("[기재정정] " if amendment and number == 2 else "") + "사업보고서 (2025.12)"
        rows.append(
            f'<tr><td>{number}</td><td><a onclick="openCorpInfoNew(\'{corporate}\', '
            "'winCorpInfo', '/dsae001/selectPopup.ax');\">발명회사</a></td>"
            f'<td><a href="/dsaf001/main.do?rcpNo={receipt}" id="r_{receipt}" '
            f'onclick="openReportViewer(\'{receipt}\',\'\');return false;">{title}</a></td>'
            '<td>발명제출인</td><td>2026.03.15</td><td>유</td></tr>'
        )
    return (f'<html><body><div class="pageInfo">[{page}/{(total + 99) // 100}] [총 {total}건]</div>'
            '<table><tbody id="tbody">' + "".join(rows) + '</tbody></table></body></html>').encode()


class Harness:
    def __init__(self, transport=None):
        self.saved = {}
        self.events = []
        self.time = datetime(2026, 10, 31, tzinfo=timezone.utc)
        self.requests = []
        self.transport = transport
        self.guards = 0

    def write(self, name, body):
        assert name not in self.saved
        self.events.append(("save", name))
        self.saved[name] = body

    def fetch(self, query):
        self.events.append(("fetch", query.query_code, query.page))
        self.requests.append(query)
        return self.transport(query) if self.transport else acquisition.HttpResponse(200, listing.SOURCE, invented_page())

    def sleep(self, seconds):
        self.events.append(("sleep", seconds))
        self.time += timedelta(seconds=seconds)

    def checkpoint(self):
        self.guards += 1

    def run(self, codes=("000001",), **kwargs):
        args = dict(write_new=self.write, transport=self.fetch, clock=lambda: self.time,
                    sleep=self.sleep, checkpoint=self.checkpoint)
        args.update(kwargs)
        return acquisition.run_listing_acquisition(codes, **args)

    def verify(self):
        def read(name):
            if name not in self.saved:
                raise FileNotFoundError(name)
            return self.saved[name]
        return acquisition.verify_saved_acquisition(read)


def test_attempt_before_fetch_and_raw_before_parse(monkeypatch):
    harness = Harness()
    original = listing.parse_listing_page

    def parse_saved(raw, query):
        assert harness.saved["attempt-001-raw.html"] == raw
        assert "attempt-001-response.json" in harness.saved
        harness.events.append(("parse",))
        return original(raw, query)

    monkeypatch.setattr(listing, "parse_listing_page", parse_saved)
    report = harness.run()
    assert harness.events.index(("save", "attempt-001-attempt.json")) < harness.events.index(("fetch", "000001", 1))
    assert harness.events.index(("save", "attempt-001-raw.html")) < harness.events.index(("parse",))
    assert report["summary"] == {"wire_attempts": 1, "complete_codes": 1, "unknown_codes": 0, "complete_listing_rows": 2}
    assert harness.verify()["verified"] is True


def test_corrections_preserved_with_no_synthetic_availability():
    harness = Harness()
    report = harness.run()
    rows = report["listings"][0]["rows"]
    assert [row["version_prefixes"] for row in rows] == [[], ["기재정정"]]
    assert rows[0]["receipt"] != rows[1]["receipt"]
    assert all(row["available_at"] is None and row["source_event_at"] is None for row in rows)
    assert harness.verify()["complete_listing_rows"] == 2


@pytest.mark.parametrize("wrapper", [b'<div class="outer"><div class="inner">',
                                     '<div class="outer">\n발명\n<div class="inner">\n'.encode()])
def test_independent_verifier_finds_listing_inside_nested_div_wrappers(wrapper):
    raw = invented_page().replace(b"<body>", b"<body>" + wrapper).replace(
        b"</body>", b"</div></div></body>")
    harness = Harness(lambda _: acquisition.HttpResponse(200, listing.SOURCE, raw))
    assert harness.run()["summary"]["complete_codes"] == 1
    assert harness.verify()["complete_listing_rows"] == 2


def test_independent_verifier_still_refuses_duplicate_nested_containers():
    raw = invented_page().replace(b"</body>", b'<div class="outer"><div class="pageInfo">'
                                  b'[1/1] [fake]</div></div></body>')
    query = {"query_code": "000001", "start_date": "2025-09-04", "end_date": "2026-09-23", "page": 1}
    with pytest.raises(acquisition.AcquisitionRefusal, match="listing containers"):
        acquisition._audit_raw(raw, query)


def test_full_fixed_bound_and_response_spacing():
    harness = Harness(lambda query: acquisition.HttpResponse(200, listing.SOURCE, invented_page(query.page, 300)))
    report = harness.run(tuple(f"{number:06d}" for number in range(18)))
    assert report["summary"] == {"wire_attempts": 54, "complete_codes": 18, "unknown_codes": 0, "complete_listing_rows": 5400}
    assert len(harness.requests) == 54
    assert len([event for event in harness.events if event[0] == "sleep"]) == 53
    assert harness.verify()["verified"]


@pytest.mark.parametrize("codes", [[], ["000001"] * 2, ["1"], ["１２３４５６"], [f"{n:06d}" for n in range(19)]])
def test_scope_refusal_before_storage_or_http(codes):
    harness = Harness()
    with pytest.raises(acquisition.AcquisitionRefusal):
        harness.run(codes)
    assert not harness.saved and not harness.requests


def test_transport_failure_terminates_without_retry_and_preserves_partial():
    def fetch(query):
        if query.query_code == "000002":
            raise TimeoutError("private text must not be persisted")
        return acquisition.HttpResponse(200, listing.SOURCE, invented_page())

    harness = Harness(fetch)
    report = harness.run(("000001", "000002", "000003"))
    assert report["state"] == "terminated" and len(harness.requests) == 2
    assert report["summary"]["complete_codes"] == 1 and report["summary"]["unknown_codes"] == 2
    assert "attempt-002-attempt.json" in harness.saved and "attempt-002-raw.html" not in harness.saved
    assert b"private text" not in harness.saved["report.json"]
    assert harness.verify()["state"] == "terminated"


@pytest.mark.parametrize("status,url,body", [
    (302, listing.SOURCE, b"redirect"), (500, listing.SOURCE, b"error"),
    (200, "https://example.invalid/", invented_page()),
    (200, listing.SOURCE, b"x" * (listing.MAX_BYTES + 1)), (200, listing.SOURCE, b""),
])
def test_response_guard_saves_raw_then_terminates(status, url, body):
    harness = Harness(lambda query: acquisition.HttpResponse(status, url, body))
    report = harness.run(("000001", "000002"))
    assert report["state"] == "terminated" and len(harness.requests) == 1
    assert harness.saved["attempt-001-raw.html"] == body
    assert "attempt-001-page.json" not in harness.saved
    assert harness.verify()["state"] == "terminated"


def test_parser_refusal_unknown_then_remaining_fixed_code():
    harness = Harness(lambda query: acquisition.HttpResponse(200, listing.SOURCE,
                      b"<html><body>empty unverified</body></html>" if query.query_code == "000001" else invented_page()))
    report = harness.run(("000001", "000002"))
    assert report["state"] == "complete"
    assert [item["state"] for item in report["listings"]] == ["unknown", "complete"]
    assert report["listings"][0]["rows"] == []
    assert harness.verify()["unknown_codes"] == 1


@pytest.mark.parametrize("change", [
    lambda raw: raw.replace(b"</html>", b""),
    lambda raw: raw.replace(b'id="tbody"', b'id="tbody" id="tbody"'),
    lambda raw: raw.replace(b"[1/1]", b"[1/3]"),
    lambda raw: raw.replace(b"20260315000002", b"20260315000001"),
])
def test_independent_unknown_reconstruction_for_refused_source(change):
    harness = Harness(lambda query: acquisition.HttpResponse(200, listing.SOURCE, change(invented_page())))
    report = harness.run()
    assert report["listings"][0]["state"] == "unknown"
    assert harness.verify()["unknown_codes"] == 1


def test_invalid_transport_response_is_terminal_guard_evidence():
    harness = Harness(lambda query: None)
    report = harness.run(("000001", "000002"))
    assert report["state"] == "terminated" and len(harness.requests) == 1
    assert harness.verify()["unknown_codes"] == 2


def test_cross_page_corporate_change_stays_unknown():
    harness = Harness(lambda query: acquisition.HttpResponse(200, listing.SOURCE,
                      invented_page(query.page, 101, corporate="00123456" if query.page == 1 else "00876543")))
    report = harness.run()
    assert report["listings"][0]["state"] == "unknown"
    assert len(harness.requests) == 2
    assert harness.verify()["unknown_codes"] == 1


def test_guard_failure_before_second_request_is_terminal():
    harness = Harness()

    def guard():
        if harness.requests:
            raise RuntimeError("source changed")

    report = harness.run(("000001", "000002"), checkpoint=guard)
    assert report["state"] == "terminated" and len(harness.requests) == 1
    assert "attempt-001-raw.html" in harness.saved
    assert harness.verify()["unknown_codes"] == 2


def test_sink_failure_before_attempt_never_fetches():
    harness = Harness()

    def write(name, raw):
        if name.endswith("-attempt.json"):
            raise OSError("storage unavailable")
        harness.write(name, raw)

    with pytest.raises(OSError):
        harness.run(write_new=write)
    assert not harness.requests


def test_unhonored_spacing_never_sends_second_request():
    harness = Harness()
    report = harness.run(("000001", "000002"), sleep=lambda seconds: None)
    assert report["state"] == "terminated" and len(harness.requests) == 1


def test_independent_verifier_does_not_call_primary_parser(monkeypatch):
    harness = Harness()
    harness.run()
    monkeypatch.setattr(listing, "parse_listing_page", lambda *args: pytest.fail("primary oracle called"))
    monkeypatch.setattr(listing, "combine_listing_pages", lambda *args: pytest.fail("primary combiner called"))
    assert harness.verify()["verified"]


@pytest.mark.parametrize("field,value", [("receipt", "20260315009999"), ("title", "반기보고서 (2025.06)"),
                                       ("corp_cik", "00888888"), ("row_number", 99),
                                       ("observation_date", "2026-03-16"), ("version_prefixes", ["철회"])])
def test_independent_raw_check_rejects_self_consistent_producer_edits(field, value):
    harness = Harness()
    harness.run()
    page = json.loads(harness.saved["attempt-001-page.json"])
    report = json.loads(harness.saved["report.json"])
    page["page"]["rows"][0][field] = value
    report["listings"][0]["rows"][0][field] = value
    harness.saved["attempt-001-page.json"] = json.dumps(page).encode()
    harness.saved["report.json"] = json.dumps(report).encode()
    with pytest.raises(acquisition.AcquisitionRefusal, match="projection mismatch"):
        harness.verify()


def test_saved_raw_cache_edit_rejected_even_if_projection_unchanged():
    harness = Harness()
    harness.run()
    harness.saved["attempt-001-raw.html"] = harness.saved["attempt-001-raw.html"].replace(b"2025.12", b"2025.11")
    with pytest.raises(acquisition.AcquisitionRefusal, match="hash/length"):
        harness.verify()
    metadata = json.loads(harness.saved["attempt-001-response.json"])
    metadata["sha256"] = hashlib.sha256(harness.saved["attempt-001-raw.html"]).hexdigest()
    harness.saved["attempt-001-response.json"] = json.dumps(metadata).encode()
    with pytest.raises(acquisition.AcquisitionRefusal, match="projection mismatch"):
        harness.verify()


def test_saved_pagination_edit_rejected():
    harness = Harness()
    harness.run()
    page = json.loads(harness.saved["attempt-001-page.json"])
    page["page"]["total"] = 3
    harness.saved["attempt-001-page.json"] = json.dumps(page).encode()
    with pytest.raises(acquisition.AcquisitionRefusal, match="projection mismatch"):
        harness.verify()


def test_https_transport_exact_post_repeated_keys_and_bounded_read(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def geturl(self):
            return listing.SOURCE

        def read(self, amount):
            captured["read"] = amount
            return b"invented"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Opener:
        def open(self, req, timeout):
            captured.update(url=req.full_url, method=req.method, data=req.data, timeout=timeout)
            return Response()

    def build(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(acquisition.http, "build_opener", build)
    query = listing.ListingRequest("000001", listing.WINDOW_START, listing.WINDOW_END, 1)
    assert acquisition.https_listing_transport(query).body == b"invented"
    assert captured["url"] == listing.SOURCE and captured["method"] == "POST" and captured["timeout"] == 20
    assert captured["read"] == listing.MAX_BYTES + 1
    assert [value for key, value in parse.parse_qsl(captured["data"].decode(), keep_blank_values=True) if key == "publicType"] == ["A001", "A002", "A003"]
    assert captured["handlers"][0].proxies == {}
    assert captured["handlers"][1].redirect_request(None, None, 302, "", {}, "https://example.invalid") is None
    context = captured["handlers"][2]._context
    assert context.check_hostname


def test_https_transport_network_failure_is_not_retried(monkeypatch):
    calls = []

    class Opener:
        def open(self, req, timeout):
            calls.append(req)
            raise error.URLError("invented failure")

    monkeypatch.setattr(acquisition.http, "build_opener", lambda *args: Opener())
    with pytest.raises(error.URLError):
        acquisition.https_listing_transport(listing.ListingRequest("000001", listing.WINDOW_START, listing.WINDOW_END, 1))
    assert len(calls) == 1


def test_https_redirect_error_body_retained_without_following(monkeypatch):
    from io import BytesIO

    calls = []

    class Opener:
        def open(self, req, timeout):
            calls.append(req)
            raise error.HTTPError(listing.SOURCE, 302, "redirect", {}, BytesIO(b"invented redirect body"))

    monkeypatch.setattr(acquisition.http, "build_opener", lambda *args: Opener())
    response = acquisition.https_listing_transport(listing.ListingRequest("000001", listing.WINDOW_START, listing.WINDOW_END, 1))
    assert response.status == 302 and response.body == b"invented redirect body" and len(calls) == 1
