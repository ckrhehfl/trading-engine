"""Bounded ED listing acquisition and saved-raw independent verification.

Importing this module performs no IO. The caller supplies exclusive native
storage, resource/source checkpoints, clocks and its existing discovery journal.
No resume, retry, credentials, viewer request, price read or strategy calculation
is provided. Complete means only the current three-type HTML query returned a
supported complete listing; it does not certify historical ticker identity,
withdrawn vintages, all earnings news, release times or S1/S2 event support.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import re
import ssl
from typing import Callable, Sequence
from urllib import error, parse, request as http

from research import intraday_ed_disclosures as listing


MAX_REQUESTS = 54
MIN_INTERVAL = 1.1
SCHEMA = "ed-disclosure-acquisition-v1"


class AcquisitionRefusal(ValueError):
    """Invalid bounded acquisition or inconsistent saved evidence."""


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise AcquisitionRefusal(message)


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _projection(value: object) -> object:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _projection(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_projection(item) for item in value]
    return value


def _codes(codes: Sequence[str]) -> tuple[str, ...]:
    _need(isinstance(codes, (list, tuple)) and 1 <= len(codes) <= 18, "one to eighteen fixed codes required")
    _need(all(isinstance(code, str) and re.fullmatch(r"[0-9]{6}", code) for code in codes),
          "codes must be six ASCII digits")
    _need(len(set(codes)) == len(codes), "duplicate query code")
    return tuple(codes)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    url: str
    body: bytes


class _NoRedirect(http.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def https_listing_transport(query: listing.ListingRequest) -> HttpResponse:
    """One exact public POST, default verified TLS, no proxy/redirect/retry.

    The extra byte detects overflow. HTTP error bodies are returned as evidence
    for the engine to retain before terminating; no error status is retried.
    """
    form = parse.urlencode(listing.listing_form(query)).encode("ascii")
    opener = http.build_opener(http.ProxyHandler({}), _NoRedirect(),
                               http.HTTPSHandler(context=ssl.create_default_context()))
    req = http.Request(listing.SOURCE, data=form, method="POST",
                       headers={"Content-Type": "application/x-www-form-urlencoded", "Accept-Encoding": "identity",
                                "User-Agent": "trading-engine-ED-public-metadata-audit/1.0",
                                "Referer": "https://dart.fss.or.kr/dsab001/main.do"})
    try:
        response = opener.open(req, timeout=20)
    except error.HTTPError as failure:
        response = failure
    with response:
        return HttpResponse(response.status, response.geturl(), response.read(listing.MAX_BYTES + 1))


def run_listing_acquisition(
    codes: Sequence[str], *, write_new: Callable[[str, bytes], None],
    transport: Callable[[listing.ListingRequest], HttpResponse],
    clock: Callable[[], datetime], sleep: Callable[[float], None], checkpoint: Callable[[], None],
) -> dict:
    """Execute a fixed serial scope with injected storage and execution guards.

    Storage must exclusively create and durably retain each named artifact.
    A transport/guard error terminates the pass with partial evidence; parser
    refusal leaves that code unknown and proceeds to the next fixed code.
    Sink failure propagates and never permits a subsequent HTTP request.
    Exception text is not persisted because injected failures may contain secrets.
    """
    fixed = _codes(codes)
    previous_clock: datetime | None = None

    def now() -> datetime:
        nonlocal previous_clock
        value = clock()
        _need(type(value) is datetime and value.tzinfo is not None and value.utcoffset().total_seconds() == 0,
              "clock must be aware UTC")
        _need(previous_clock is None or value >= previous_clock, "UTC clock moved backwards")
        previous_clock = value
        return value

    scope = {"schema": SCHEMA, "codes": list(fixed), "source": listing.SOURCE,
             "start_date": listing.WINDOW_START.isoformat(), "end_date": listing.WINDOW_END.isoformat()}
    write_new("scope.json", _json(scope))
    report = {**scope, "state": "complete", "attempts": [], "listings": [], "failure": None}
    ended: datetime | None = None
    fatal = False
    for code in fixed:
        result = {"query_code": code, "state": "unknown", "rows": [], "reason": "not_attempted"}
        report["listings"].append(result)
        if fatal:
            continue
        pages = []
        for page_number in range(1, 4):
            query = listing.ListingRequest(code, listing.WINDOW_START, listing.WINDOW_END, page_number)
            try:
                checkpoint()
                started = now()
                if ended is not None and (started - ended).total_seconds() < MIN_INTERVAL:
                    sleep(MIN_INTERVAL - (started - ended).total_seconds())
                    checkpoint()
                    started = now()
                    _need((started - ended).total_seconds() >= MIN_INTERVAL, "response-to-request spacing violated")
                _need(len(report["attempts"]) < MAX_REQUESTS, "wire request ceiling")
            except Exception as failure:
                report["failure"] = {"stage": "guard", "error_type": type(failure).__name__}
                fatal = True
                break
            prefix = f"attempt-{len(report['attempts']) + 1:03d}"
            identity = _projection(asdict(query))
            attempt = {"request": identity, "source": listing.SOURCE, "method": "POST",
                       "form": _projection(listing.listing_form(query)), "started_at": started.isoformat()}
            write_new(prefix + "-attempt.json", _json(attempt))
            report["attempts"].append(prefix)
            try:
                response = transport(query)
            except Exception as failure:
                write_new(prefix + "-failure.json", _json({"stage": "transport", "error_type": type(failure).__name__}))
                report["failure"] = {"stage": "transport", "error_type": type(failure).__name__}
                fatal = True
                break
            try:
                _need(isinstance(response, HttpResponse) and type(response.body) is bytes, "invalid transport response")
            except Exception as failure:
                write_new(prefix + "-failure.json", _json({"stage": "guard", "error_type": type(failure).__name__}))
                report["failure"] = {"stage": "guard", "error_type": type(failure).__name__}
                fatal = True
                break
            # Preserve even a refused body before checking HTTP or parsing it.
            raw = response.body[:listing.MAX_BYTES + 1]
            try:
                ended = now()
            except Exception as failure:
                write_new(prefix + "-raw.html", raw)
                write_new(prefix + "-failure.json", _json({"stage": "guard", "error_type": type(failure).__name__}))
                report["failure"] = {"stage": "guard", "error_type": type(failure).__name__}
                fatal = True
                break
            write_new(prefix + "-raw.html", raw)
            metadata = {"received_at": ended.isoformat(), "status": response.status, "url": response.url,
                        "bytes": len(raw), "received_bytes": len(response.body),
                        "sha256": hashlib.sha256(raw).hexdigest()}
            write_new(prefix + "-response.json", _json(metadata))
            try:
                checkpoint()
                _need(response.url == listing.SOURCE and type(response.status) is int and response.status == 200,
                      "HTTP status, redirect or off-host response refused")
                _need(0 < len(response.body) <= listing.MAX_BYTES, "empty/oversized response")
            except Exception as failure:
                report["failure"] = {"stage": "guard", "error_type": type(failure).__name__}
                fatal = True
                break
            try:
                parsed = listing.parse_listing_page(raw, query)
                write_new(prefix + "-page.json", _json({"state": "parsed", "page": _projection(asdict(parsed))}))
                pages.append(parsed)
                if page_number == parsed.pages:
                    complete = listing.combine_listing_pages(pages)
                    result.update(state="complete", reason=None, rows=_projection([asdict(row) for row in complete.rows]))
                    break
            except listing.Refusal as failure:
                # The page file is already present if only whole-list validation refused.
                if not pages or pages[-1].request.page != page_number:
                    write_new(prefix + "-page.json", _json({"state": "unknown", "error_type": type(failure).__name__}))
                result["reason"] = "parser_or_pagination_refusal"
                break
        if fatal:
            result["reason"] = "terminated_partial_evidence"
    report["state"] = "terminated" if fatal else "complete"
    report["summary"] = {
        "wire_attempts": len(report["attempts"]),
        "complete_codes": sum(item["state"] == "complete" for item in report["listings"]),
        "unknown_codes": sum(item["state"] == "unknown" for item in report["listings"]),
        "complete_listing_rows": sum(len(item["rows"]) for item in report["listings"]),
    }
    write_new("report.json", _json(report))
    return report


class _AuditNesting(HTMLParser):
    """Validate structure and locate exact raw spans; no producer tree is used."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.line_offsets = [0, *(match.end() for match in re.finditer("\n", source))]
        self.open_tags = []
        self.containers = {"rows": [], "pagination": []}
        self.nodes = 0
        self.feed(source)
        self.close()
        _need(not self.open_tags, "audit truncated document")

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        _need(self.nodes <= 20_000 and len(self.open_tags) < 128, "audit structure bound")
        _need(len(dict(attrs)) == len(attrs), "audit duplicate attribute")
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
                       "meta", "param", "source", "track", "wbr"}:
            attributes = dict(attrs)
            target = ("rows" if tag == "tbody" and attributes.get("id") == "tbody" else
                      "pagination" if tag == "div" and attributes.get("class") == "pageInfo" else None)
            line, column = self.getpos()
            content_start = self.line_offsets[line - 1] + column + len(self.get_starttag_text())
            self.open_tags.append((tag, content_start, target))

    def handle_endtag(self, tag):
        _need(bool(self.open_tags) and self.open_tags[-1][0] == tag, "audit malformed nesting")
        _, content_start, target = self.open_tags.pop()
        if target is not None:
            line, column = self.getpos()
            self.containers[target].append(self.source[content_start:self.line_offsets[line - 1] + column])

    def handle_startendtag(self, tag, attrs):
        previous = len(self.open_tags)
        self.handle_starttag(tag, attrs)
        if len(self.open_tags) > previous:
            self.handle_endtag(tag)


def _audit_raw(data: bytes, query: dict) -> dict:
    """A separate bounded structural/regex reconstruction, never the producer parser.

    This verifier accepts only the explicitly supported six-column row grammar.
    It deliberately refuses markup changes instead of repairing or guessing.
    """
    _need(type(data) is bytes and 0 < len(data) <= listing.MAX_BYTES, "audit response bounds")
    try:
        source = data.decode("utf-8-sig", "strict")
    except UnicodeError as failure:
        raise AcquisitionRefusal("audit UTF-8") from failure
    _need(not any(ord(char) < 32 and char not in "\t\r\n" for char in source), "audit control character")
    _need(len(re.findall(r"<", source)) <= 40_000, "audit markup bound")
    containers = _AuditNesting(source).containers

    def clean(text: str) -> str:
        _need(re.search(r"<(?:script|style)\b", text, re.I) is None, "audit executable row markup")
        return " ".join(unescape(re.sub(r"<[^>]*>", " ", text)).split())

    def attribute(tag: str, name: str) -> str:
        matches = re.findall(r"\b" + name + r"\s*=\s*([\"'])(.*?)\1", tag, re.S | re.I)
        _need(len(matches) == 1, "audit missing/duplicate attribute")
        return unescape(matches[0][1])

    # Whole-document non-overlapping div regexes swallow a nested pageInfo in
    # an outer layout div. Balanced raw spans preserve nested wrappers without
    # accepting ambiguous/malformed containers or sharing the producer's tree.
    target_bodies, target_infos = containers["rows"], containers["pagination"]
    _need(len(target_bodies) == len(target_infos) == 1, "audit listing containers")
    info = re.fullmatch(r"\[([1-3])/([1-3])\]\s*\[총\s*(0|[1-9][0-9]{0,2})건\]", clean(target_infos[0]))
    _need(info is not None, "audit pagination grammar")
    current, pages, total = map(int, info.groups())
    _need(current == query["page"] and 1 <= current <= pages <= 3 and 0 < total <= 300
          and pages == (total + 99) // 100, "audit pagination values")
    body = target_bodies[0]
    row_markup = re.findall(r"<tr\b[^>]*>(.*?)</tr\s*>", body, re.S | re.I)
    _need(not re.sub(r"<tr\b[^>]*>.*?</tr\s*>", "", body, flags=re.S | re.I).strip(), "audit extra tbody content")
    _need(len(row_markup) == min(100, total - 100 * (current - 1)), "audit truncated rows")
    rows = []
    for offset, row in enumerate(row_markup, 1):
        cells = re.findall(r"<td\b[^>]*>(.*?)</td\s*>", row, re.S | re.I)
        _need(len(cells) == 6 and not re.sub(r"<td\b[^>]*>.*?</td\s*>", "", row, flags=re.S | re.I).strip(),
              "audit columns")
        ordinal = (current - 1) * 100 + offset
        _need(clean(cells[0]) == str(ordinal), "audit ordinal")
        corporate = re.findall(r"(<a\b[^>]*>)(.*?)</a\s*>", cells[1], re.S | re.I)
        report = re.findall(r"(<a\b[^>]*>)(.*?)</a\s*>", cells[2], re.S | re.I)
        _need(len(corporate) == len(report) == 1, "audit anchors")
        corp = re.fullmatch(r"openCorpInfoNew\('([0-9]{8})',\s*'winCorpInfo',\s*'/dsae001/selectPopup.ax'\);",
                            attribute(corporate[0][0], "onclick"))
        href = attribute(report[0][0], "href")
        receipt_match = re.fullmatch(r"/dsaf001/main.do\?rcpNo=([0-9]{14})", href)
        _need(corp is not None and receipt_match is not None, "audit corp/receipt")
        receipt = receipt_match[1]
        _need(attribute(report[0][0], "id") == "r_" + receipt, "audit anchor receipt ID")
        _need(re.fullmatch(r"openReportViewer\('" + receipt + r"',''\);\s*return false;",
                          attribute(report[0][0], "onclick")) is not None, "audit viewer receipt ID")
        title = clean(report[0][1])
        title_match = re.fullmatch(r"((?:\[[^\[\]]+\]\s*)*)(사업보고서|반기보고서|분기보고서)\s*\(([0-9]{4})\.([0-9]{2})\)", title)
        _need(title_match is not None, "audit title")
        prefixes = re.findall(r"\[([^\[\]]+)\]", title_match[1])
        _need(len(prefixes) == len(set(prefixes)) and set(prefixes) <= {"기재정정", "첨부정정", "첨부추가", "철회"},
              "audit version prefixes")
        observed_text = clean(cells[4])
        _need(re.fullmatch(r"[0-9]{4}\.[0-9]{2}\.[0-9]{2}", observed_text) is not None, "audit source date")
        try:
            observed = date.fromisoformat(observed_text.replace(".", "-"))
            period = date(int(title_match[3]), int(title_match[4]), 1)
        except ValueError as failure:
            raise AcquisitionRefusal("audit calendar") from failure
        _need(observed.strftime("%Y%m%d") == receipt[:8] and period <= observed
              and query["start_date"] <= observed.isoformat() <= query["end_date"], "audit chronology")
        company = clean(corporate[0][1])
        _need(bool(company), "audit empty company")
        rows.append({"query_code": query["query_code"], "corp_cik": corp[1], "source_company_name": company,
                     "row_number": ordinal, "title": title, "report_type": title_match[2],
                     "public_type": {"사업보고서": "A001", "반기보고서": "A002", "분기보고서": "A003"}[title_match[2]],
                     "version_prefixes": prefixes, "period": title_match[3] + "." + title_match[4],
                     "receipt": receipt, "observation_date": observed.isoformat(), "submitter": clean(cells[3]),
                     "remarks": clean(cells[5]), "source_page": current, "wrapper_url": "https://dart.fss.or.kr" + href,
                     "source": listing.SOURCE, "source_event_at": None, "available_at": None,
                     "first_seen_at": None, "is_final": None})
    _audit_population(rows)
    return {"request": query, "pages": pages, "total": total, "rows": rows,
            "raw_sha256": hashlib.sha256(data).hexdigest()}


def _audit_population(rows: list[dict]) -> None:
    _need(len({row["corp_cik"] for row in rows}) == 1, "audit mixed corporate identities")
    _need(len({row["receipt"] for row in rows}) == len(rows), "audit duplicate receipts")
    dates = [row["observation_date"] for row in rows]
    _need(dates == sorted(dates, reverse=True), "audit source date ordering")


def verify_saved_acquisition(read_saved: Callable[[str], bytes]) -> dict:
    """Reconstruct saved raw listings independently and compare saved projections.

    This verifies content consistency, including hashes and bounded request
    chronology. The caller separately pins source/payload identities and native
    storage integrity. A hash is not an external historical-content certificate.
    Missing interrupted evidence is reported as incomplete, never as no-event.
    """
    def load(name: str) -> dict:
        value = json.loads(read_saved(name))
        _need(isinstance(value, dict), "saved object required")
        return value

    scope, report = load("scope.json"), load("report.json")
    codes = _codes(scope["codes"])
    expected_scope = {"schema": SCHEMA, "codes": list(codes), "source": listing.SOURCE,
                      "start_date": listing.WINDOW_START.isoformat(), "end_date": listing.WINDOW_END.isoformat()}
    _need(scope == expected_scope and all(report.get(key) == value for key, value in scope.items()), "saved scope changed")
    _need(isinstance(report["attempts"], list) and len(report["attempts"]) <= MAX_REQUESTS, "audit attempt bound")
    _need(report["attempts"] == [f"attempt-{index:03d}" for index in range(1, len(report["attempts"]) + 1)],
          "audit attempt identities")
    reconstructed = {code: [] for code in codes}
    unknown = set()
    previous_received = None
    order = []
    terminal = False
    for prefix in report["attempts"]:
        _need(not terminal, "request after terminal failure")
        attempt = load(prefix + "-attempt.json")
        query = attempt["request"]
        _need(set(query) == {"query_code", "start_date", "end_date", "page"}, "audit request fields")
        _need(query["query_code"] in codes and query["start_date"] == scope["start_date"]
              and query["end_date"] == scope["end_date"], "audit request scope")
        expected_page = 1 + len(reconstructed[query["query_code"]])
        _need(type(query["page"]) is int and query["page"] == expected_page <= 3
              and query["query_code"] not in unknown, "audit request page progression")
        code_index = codes.index(query["query_code"])
        _need(not order or code_index >= order[-1], "audit fixed code ordering")
        order.append(code_index)
        contract = listing.ListingRequest(query["query_code"], listing.WINDOW_START, listing.WINDOW_END, query["page"])
        _need(attempt["source"] == listing.SOURCE and attempt["method"] == "POST"
              and attempt["form"] == _projection(listing.listing_form(contract)), "audit exact form")
        started = datetime.fromisoformat(attempt["started_at"])
        _need(started.tzinfo is not None and started.utcoffset().total_seconds() == 0, "audit UTC request")
        _need(previous_received is None or (started - previous_received).total_seconds() >= MIN_INTERVAL,
              "audit request spacing")
        try:
            metadata = load(prefix + "-response.json")
        except FileNotFoundError:
            failure = load(prefix + "-failure.json")
            _need(failure["stage"] in {"transport", "guard"} and report["failure"] == failure,
                  "audit missing response without terminal failure")
            terminal = True
            continue
        raw = read_saved(prefix + "-raw.html")
        _need(len(raw) == metadata["bytes"] and len(raw) <= listing.MAX_BYTES + 1
              and metadata["received_bytes"] >= len(raw)
              and metadata["sha256"] == hashlib.sha256(raw).hexdigest(), "saved raw hash/length mismatch")
        received = datetime.fromisoformat(metadata["received_at"])
        _need(received.tzinfo is not None and received.utcoffset().total_seconds() == 0 and received >= started,
              "audit response chronology")
        previous_received = received
        if metadata["url"] != listing.SOURCE or type(metadata["status"]) is not int or metadata["status"] != 200 or not 0 < metadata["received_bytes"] <= listing.MAX_BYTES:
            _need(report["failure"] is not None and report["failure"]["stage"] == "guard", "audit unreported response guard failure")
            terminal = True
            continue
        try:
            producer_page = load(prefix + "-page.json")
        except FileNotFoundError:
            _need(report["failure"] is not None and report["failure"]["stage"] == "guard", "audit missing producer page")
            terminal = True
            continue
        try:
            independent = _audit_raw(raw, query)
        except AcquisitionRefusal:
            _need(producer_page.get("state") == "unknown", "producer accepted independently refused raw")
            unknown.add(query["query_code"])
            continue
        _need(producer_page == {"state": "parsed", "page": independent}, "independent raw/page projection mismatch")
        reconstructed[query["query_code"]].append(independent)
    _need(len(report["listings"]) == len(codes), "audit listing count")
    for code, actual in zip(codes, report["listings"], strict=True):
        _need(actual["query_code"] == code, "audit listing ordering")
        pages = reconstructed[code]
        rows = [row for page in pages for row in page["rows"]]
        complete = bool(pages) and len(pages) == pages[0]["pages"] and code not in unknown
        if complete:
            try:
                _need(len({(page["pages"], page["total"]) for page in pages}) == 1
                      and len(rows) == pages[0]["total"], "audit whole-list pagination")
                _audit_population(rows)
            except AcquisitionRefusal:
                complete = False
        _need(actual["state"] == ("complete" if complete else "unknown")
              and actual["rows"] == (rows if complete else []), "independent whole-list projection mismatch")
    summary = {"wire_attempts": len(report["attempts"]),
               "complete_codes": sum(row["state"] == "complete" for row in report["listings"]),
               "unknown_codes": sum(row["state"] == "unknown" for row in report["listings"]),
               "complete_listing_rows": sum(len(row["rows"]) for row in report["listings"])}
    _need(summary == report["summary"], "audit summary mismatch")
    _need(report["state"] in {"complete", "terminated"}
          and (report["failure"] is not None) == (report["state"] == "terminated"), "audit terminal status")
    if report["state"] == "complete":
        _need(not terminal and set(order) == set(range(len(codes))), "audit skipped code")
    return {"schema": SCHEMA, "verified": True, "state": report["state"], **summary}
