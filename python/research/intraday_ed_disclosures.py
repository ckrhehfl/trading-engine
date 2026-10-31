"""Pure parser for ED's bounded public DART periodic-report listings.

The caller owns transport, raw receipt storage, actual retrieval timestamps and
request/response binding. This module opens nothing, chooses no latest filing,
and infers neither sentiment nor intraday availability. A complete supported
list covers only the three requested periodic-report types and receipt dates;
it is not all-news coverage or an S1/S2 strategy no-event classification.
Completeness means the rows returned by this current HTML query, not a proved
historical-vintage archive: removed/withdrawn past rows may be unavailable.

The listing's eight-digit corporate ID is preserved and must be consistent. It
does not independently establish the queried stock code's corporate identity.
Unrecognized/empty source markup refuses rather than certifying no filings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import hashlib
from html.parser import HTMLParser
import re
from typing import Sequence


SOURCE = "https://dart.fss.or.kr/dsab001/search.ax"
WINDOW_START = date(2025, 9, 4)
WINDOW_END = date(2026, 9, 23)
MAX_BYTES = 1024 * 1024
REPORT_TYPES = (("사업보고서", "A001"), ("반기보고서", "A002"), ("분기보고서", "A003"))
VERSION_PREFIXES = frozenset({"기재정정", "첨부정정", "첨부추가", "철회"})


class Refusal(ValueError):
    """Unknown/incomplete source; callers must not convert this into no-event."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise Refusal(message)


def _compact(text: str) -> str:
    return " ".join(text.split())


@dataclass(frozen=True)
class ListingRequest:
    query_code: str
    start_date: date
    end_date: date
    page: int

    def __post_init__(self) -> None:
        _require(isinstance(self.query_code, str) and re.fullmatch(r"[0-9]{6}", self.query_code) is not None,
                 "query code must be six ASCII digits")
        _require(type(self.start_date) is date and type(self.end_date) is date,
                 "request bounds must be dates without a time")
        _require(WINDOW_START <= self.start_date <= self.end_date <= WINDOW_END,
                 "request is outside the spent discovery window")
        _require(type(self.page) is int and 1 <= self.page <= 3, "listing page limit")


def listing_form(request: ListingRequest) -> tuple[tuple[str, str], ...]:
    """Exact form pairs; repeated publicType keys must survive encoding."""
    _require(isinstance(request, ListingRequest), "invalid listing request")
    return (
        ("currentPage", str(request.page)), ("maxResults", "100"), ("maxLinks", "10"),
        ("sort", "date"), ("series", "desc"), ("textCrpCik", ""),
        ("pageGubun", "corp"), ("textCrpNm", request.query_code), ("autoSearchCorp", "Y"),
        ("startDate", request.start_date.strftime("%Y%m%d")),
        ("endDate", request.end_date.strftime("%Y%m%d")),
        *(("publicType", code) for _, code in REPORT_TYPES),
    )


@dataclass(frozen=True)
class DisclosureRow:
    query_code: str
    corp_cik: str
    source_company_name: str
    row_number: int
    title: str
    report_type: str
    public_type: str
    version_prefixes: tuple[str, ...]
    period: str
    receipt: str
    observation_date: date
    submitter: str
    remarks: str
    source_page: int
    wrapper_url: str
    source: str = field(default=SOURCE, init=False)
    source_event_at: None = field(default=None, init=False)
    available_at: None = field(default=None, init=False)
    first_seen_at: None = field(default=None, init=False)
    is_final: None = field(default=None, init=False)

    @property
    def is_original(self) -> bool:
        return not self.version_prefixes


@dataclass(frozen=True)
class ListingPage:
    request: ListingRequest
    pages: int
    total: int
    rows: tuple[DisclosureRow, ...]
    raw_sha256: str


@dataclass(frozen=True)
class CompleteListing:
    request: ListingRequest
    corp_cik: str
    rows: tuple[DisclosureRow, ...]
    pages: tuple[ListingPage, ...]


class _Element:
    def __init__(self, tag: str, attrs: Sequence[tuple[str, str | None]] = ()) -> None:
        _require(len(dict(attrs)) == len(attrs), "duplicate HTML attribute")
        self.tag = tag
        self.attrs = dict(attrs)
        self.children: list[_Element | str] = []

    def find(self, tag: str, **attrs: str) -> list[_Element]:
        result = []
        for child in self.children:
            if isinstance(child, _Element):
                if child.tag == tag and all(child.attrs.get(key) == value for key, value in attrs.items()):
                    result.append(child)
                result.extend(child.find(tag, **attrs))
        return result

    def text(self) -> str:
        if self.tag in {"script", "style", "head"}:
            return ""
        return " ".join(child.text() if isinstance(child, _Element) else child for child in self.children)


class _Document(HTMLParser):
    _VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
                       "meta", "param", "source", "track", "wbr"})

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Element("root")
        self.stack = [self.root]
        self.nodes = 0
        self.feed(source)
        self.close()
        _require(len(self.stack) == 1, "unclosed/truncated HTML")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.nodes += 1
        _require(self.nodes <= 20_000 and len(self.stack) <= 128, "HTML structure limit")
        child = _Element(tag, attrs)
        self.stack[-1].children.append(child)
        if tag not in self._VOID:
            self.stack.append(child)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        _require(len(self.stack) > 1 and self.stack[-1].tag == tag, "malformed HTML nesting")
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _source_date(text: str) -> date:
    _require(re.fullmatch(r"[0-9]{4}\.[0-9]{2}\.[0-9]{2}", text) is not None,
             "receipt date format changed")
    try:
        return date.fromisoformat(text.replace(".", "-"))
    except ValueError as error:
        raise Refusal("invalid receipt calendar date") from error


def _parse_row(tr: _Element, request: ListingRequest, expected_number: int) -> DisclosureRow:
    cells = [child for child in tr.children if isinstance(child, _Element) and child.tag == "td"]
    _require(len(cells) == 6 and len(tr.find("td")) == 6, "listing column contract changed")
    _require([child for child in tr.children if isinstance(child, _Element)] == cells
             and all(not isinstance(child, str) or not child.strip() for child in tr.children),
             "unexpected listing row content")
    _require(_compact(cells[0].text()) == str(expected_number), "listing row numbering mismatch")
    corps = cells[1].find("a")
    _require(len(corps) == 1, "missing/ambiguous corporate anchor")
    corp = re.fullmatch(
        r"openCorpInfoNew\('([0-9]{8})',\s*'winCorpInfo',\s*'/dsae001/selectPopup.ax'\);",
        corps[0].attrs.get("onclick") or "",
    )
    _require(corp is not None, "corporate ID contract changed")
    company_name = _compact(corps[0].text())
    _require(bool(company_name), "empty company name")
    anchors = cells[2].find("a")
    _require(len(anchors) == 1, "missing/ambiguous report anchor")
    anchor = anchors[0]
    receipt_match = re.fullmatch(r"/dsaf001/main.do\?rcpNo=([0-9]{14})", anchor.attrs.get("href") or "")
    _require(receipt_match is not None, "report link contract changed")
    receipt = receipt_match[1]
    _require(anchor.attrs.get("id") == "r_" + receipt, "receipt ID mismatch")
    _require(re.fullmatch(r"openReportViewer\('" + receipt + r"',''\);\s*return false;",
                         anchor.attrs.get("onclick") or "") is not None, "receipt onclick mismatch")
    title = _compact(anchor.text())
    title_match = re.fullmatch(
        r"((?:\[[^\[\]]+\]\s*)*)(사업|반기|분기)보고서\s*\(([0-9]{4})\.([0-9]{2})\)", title
    )
    _require(title_match is not None, "non-periodic or ambiguous report title")
    prefixes = tuple(re.findall(r"\[([^\[\]]+)\]", title_match[1]))
    _require(all(prefix in VERSION_PREFIXES for prefix in prefixes), "unknown report version prefix")
    _require(len(set(prefixes)) == len(prefixes), "duplicate report version prefix")
    observed = _source_date(_compact(cells[4].text()))
    _require(observed.strftime("%Y%m%d") == receipt[:8], "receipt/date mismatch")
    _require(request.start_date <= observed <= request.end_date, "receipt outside request window")
    try:
        period_start = date(int(title_match[3]), int(title_match[4]), 1)
    except ValueError as error:
        raise Refusal("invalid report period") from error
    _require(period_start <= observed, "future report period")
    report_type = title_match[2] + "보고서"
    return DisclosureRow(
        request.query_code, corp[1], company_name, expected_number, title, report_type,
        dict(REPORT_TYPES)[report_type], prefixes, title_match[3] + "." + title_match[4],
        receipt, observed, _compact(cells[3].text()), _compact(cells[5].text()), request.page,
        "https://dart.fss.or.kr" + receipt_match[0],
    )


def parse_listing_page(data: bytes, request: ListingRequest) -> ListingPage:
    """Parse one already-received UTF-8 page; refusal means unknown coverage."""
    _require(isinstance(request, ListingRequest), "invalid listing request")
    _require(type(data) is bytes and 0 < len(data) <= MAX_BYTES, "empty/oversized/non-bytes response")
    try:
        source = data.decode("utf-8-sig", errors="strict")
    except UnicodeError as error:
        raise Refusal("response is not strict UTF-8") from error
    _require(not any(ord(char) < 32 and char not in "\t\r\n" for char in source), "HTML control character")
    document = _Document(source).root
    bodies = document.find("tbody", id="tbody")
    infos = document.find("div", **{"class": "pageInfo"})
    _require(len(bodies) == len(infos) == 1, "missing/ambiguous listing container")
    pagination = re.fullmatch(r"\[([1-3])/([1-3])\]\s*\[총\s*(0|[1-9][0-9]{0,2})건\]",
                              _compact(infos[0].text()))
    _require(pagination is not None, "listing pagination contract changed")
    current, pages, total = map(int, pagination.groups())
    _require(current == request.page and 1 <= current <= pages <= 3 and 0 < total <= 300,
             "wrong page, unsupported empty source, or pagination bound")
    _require(pages == (total + 99) // 100, "pagination/total mismatch")
    trs = bodies[0].find("tr")
    direct = [child for child in bodies[0].children if isinstance(child, _Element)]
    _require(direct == trs, "nested/non-row listing structure")
    _require(all(not isinstance(child, str) or not child.strip() for child in bodies[0].children),
             "unexpected listing text")
    _require(len(trs) == min(100, total - (current - 1) * 100), "listing population truncated")
    rows = tuple(_parse_row(tr, request, (current - 1) * 100 + offset)
                 for offset, tr in enumerate(trs, 1))
    _require(len({row.corp_cik for row in rows}) == 1, "query maps to multiple corporate IDs")
    _require(len({row.receipt for row in rows}) == len(rows), "duplicated report receipt")
    _require([row.observation_date for row in rows] == sorted(
        (row.observation_date for row in rows), reverse=True), "listing date sort changed")
    return ListingPage(request, pages, total, rows, hashlib.sha256(data).hexdigest())


def combine_listing_pages(pages: Sequence[ListingPage]) -> CompleteListing:
    """Require the full bounded population; preserve every receipt/version."""
    _require(isinstance(pages, (list, tuple)) and 1 <= len(pages) <= 3, "missing/overflow listing pages")
    _require(all(isinstance(page, ListingPage) for page in pages), "invalid parsed page")
    first = pages[0]
    _require([page.request.page for page in pages] == list(range(1, first.pages + 1)),
             "missing/duplicated/out-of-order listing pages")
    identity = (first.request.query_code, first.request.start_date, first.request.end_date)
    _require(all((page.request.query_code, page.request.start_date, page.request.end_date) == identity
                 for page in pages), "query changed between pages")
    _require(len({(page.pages, page.total) for page in pages}) == 1, "source changed between pages")
    rows = tuple(row for page in pages for row in page.rows)
    _require(len(rows) == first.total, "listing total population mismatch")
    _require(len({row.corp_cik for row in rows}) == 1, "corporate ID changed between pages")
    _require(len({row.receipt for row in rows}) == len(rows), "duplicated report receipt across pages")
    _require([row.observation_date for row in rows] == sorted(
        (row.observation_date for row in rows), reverse=True), "listing date sort changed between pages")
    return CompleteListing(first.request, rows[0].corp_cik, rows, tuple(pages))
