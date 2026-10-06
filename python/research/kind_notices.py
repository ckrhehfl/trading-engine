"""Pure parsers for inspected KIND disclosure pages, not an eligibility ledger.

Only the observed search, viewer and three exchange-notice forms are supported.
Unknown structures fail closed. A disclosure issuer ID is opaque; stock codes
come only from the common-share row under the explicit short-code heading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
import re


class KindParseError(ValueError):
    """The response cannot establish the requested public metadata."""


@dataclass
class _Element:
    tag: str
    attrs: dict[str, str | None] = field(default_factory=dict)
    children: list[_Element | str] = field(default_factory=list)

    def find(self, tag: str) -> list[_Element]:
        found = []
        for child in self.children:
            if isinstance(child, _Element):
                if child.tag == tag:
                    found.append(child)
                found.extend(child.find(tag))
        return found

    def text(self) -> str:
        return " ".join(" ".join(
            child if isinstance(child, str) else child.text()
            for child in self.children
            if isinstance(child, str) or child.tag not in {"script", "style"}
        ).split())


class _HTML(HTMLParser):
    # A small tree retains cell boundaries and select ownership. No HTML repair:
    # silently repairing a truncated cell could turn unrelated text into a code.
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
             "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Element("root")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if len(dict(attrs)) != len(attrs):
            raise KindParseError("duplicate HTML attribute")
        element = _Element(tag, dict(attrs))
        self.stack[-1].children.append(element)
        if tag not in self._VOID:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if len(self.stack) == 1 or self.stack[-1].tag != tag:
            raise KindParseError("unexpected or unclosed HTML element")
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _parse(payload: bytes) -> _Element:
    if not isinstance(payload, bytes) or not payload or len(payload) > 2_000_000:
        raise KindParseError("empty, oversized or non-byte KIND response")
    try:
        source = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        if not re.search(br"charset\s*=\s*[\"']?euc-kr", payload[:2000], re.I):
            raise KindParseError("unknown KIND response encoding") from error
        try:
            source = payload.decode("cp949")
        except UnicodeDecodeError as error:
            raise KindParseError("invalid KIND response encoding") from error
    if "\x00" in source or "\ufffd" in source:
        raise KindParseError("corrupt KIND response text")
    parser = _HTML()
    parser.feed(source)
    parser.close()
    if len(parser.stack) != 1:
        raise KindParseError("truncated KIND response")
    return parser.root


def _one(items: list, label: str):
    if len(items) != 1:
        raise KindParseError(f"missing or ambiguous {label}")
    return items[0]


def _date(value: str) -> str:
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value)
    if match is None:
        match = re.fullmatch(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", value)
    if match is None:
        raise KindParseError("unrecognized disclosure date")
    try:
        return date(*(int(part) for part in match.groups())).isoformat()
    except ValueError as error:
        raise KindParseError("invalid disclosure date") from error


def _number(value: str, label: str) -> str:
    if re.fullmatch(r"[0-9]{14}", value) is None:
        raise KindParseError(f"invalid {label}")
    _date(f"{value[:4]}-{value[4:6]}-{value[6:8]}")
    return value


def _children(element: _Element, tags: set[str]) -> list[_Element]:
    return [child for child in element.children
            if isinstance(child, _Element) and child.tag in tags]


def _cells(row: _Element) -> list[_Element]:
    return _children(row, {"td", "th"})


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def parse_search(payload: bytes, start: str, end: str, page: int = 1) -> dict:
    """Read a complete declared page; publication timestamps are KIND's KST."""
    if _date(start) != start or _date(end) != end or start > end:
        raise KindParseError("invalid requested search interval")
    if type(page) is not int or page < 1:
        raise KindParseError("invalid requested search page")
    root = _parse(payload)
    table = _one(root.find("table"), "search table")
    if _compact(table.attrs.get("summary") or "") != "번호,시간,회사명,공시제목,제출인,차트/주가":
        raise KindParseError("unrecognized search columns")
    if len(table.find("col")) != 6:
        raise KindParseError("unexpected search column count")
    body = _one(table.find("tbody"), "search table body")
    counts = re.findall(r"전체\s*([0-9]+(?:,[0-9]{3})*)\s*건\s*:\s*([0-9]+)\s*/\s*([0-9]+)", root.text())
    count = _one(counts, "search pagination")
    total, actual_page, pages = (int(value.replace(",", "")) for value in count)
    select = _one([node for node in root.find("select")
                   if node.attrs.get("id") == "currentPageSize"], "search page size")
    option = _one([node for node in select.find("option")
                   if "selected" in node.attrs], "selected search page size")
    size_value = option.attrs.get("value")
    if size_value not in {"15", "30", "50", "100"}:
        raise KindParseError("unrecognized search page size")
    size = int(size_value)
    if total <= 0 or pages != (total + size - 1) // size or actual_page != page or page > pages:
        raise KindParseError("empty or inconsistent search pagination")
    rows = _children(body, {"tr"})
    expected = min(size, total - (page - 1) * size)
    if len(rows) != expected:
        raise KindParseError("incomplete search page")
    output = []
    for position, row in enumerate(rows):
        cells = _cells(row)
        if len(cells) != 6 or any(
            cell.attrs.get("colspan", "1") != "1" or cell.attrs.get("rowspan", "1") != "1"
            for cell in cells
        ):
            raise KindParseError("unexpected search row columns")
        if cells[0].text() != str(total - (page - 1) * size - position):
            raise KindParseError("inconsistent search row ordinal")
        stamp = cells[1].text()
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}", stamp) is None:
            raise KindParseError("missing search publication date/time")
        try:
            published = datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(
                tzinfo=timezone(timedelta(hours=9)))
        except ValueError as error:
            raise KindParseError("invalid search publication timestamp") from error
        if not start <= published.date().isoformat() <= end:
            raise KindParseError("search publication outside requested interval")
        issuer = _one(cells[2].find("a"), "search issuer link")
        issuer_match = re.fullmatch(r"companysummary_open\('([A-Za-z0-9]+)'\);\s*return false;",
                                    issuer.attrs.get("onclick") or "")
        if issuer_match is None or not issuer.text() or issuer.text() != issuer.attrs.get("title"):
            raise KindParseError("unrecognized search issuer")
        link = _one(cells[3].find("a"), "search disclosure link")
        receipt = re.fullmatch(r"openDisclsViewer\('([0-9]{14})',''\)", link.attrs.get("onclick") or "")
        if receipt is None:
            raise KindParseError("unrecognized search receipt link")
        receipt_no = _number(receipt[1], "receipt number")
        title = link.attrs.get("title") or ""
        if not title or not link.text() or not cells[4].text():
            raise KindParseError("missing disclosure title or submitter")
        images = cells[3].find("img")
        correction_image = {"src": "/images/common/icn_t_jung.gif", "class": "vmiddle legend",
                            "alt": "해당보고서 이후에 정정된 보고서 있음"}
        if (cells[3].text() != link.text() or len(images) > 1
                or images != _children(link, {"img"})
                or any(node.attrs != correction_image for node in images)):
            raise KindParseError("unrecognized disclosure title marker structure")
        # The attribute is the complete official title, including correction
        # markers; the visible text can be shortened by the search page. The
        # image says a later correction exists, not that this row is corrected.
        output.append(dict(receipt_no=receipt_no, published_at=published.isoformat(),
                           title=title, issuer_name=issuer.text(), issuer_id=issuer_match[1],
                           submitter=cells[4].text(), later_correction_reported=bool(images)))
    if len({row["receipt_no"] for row in output}) != len(output):
        raise KindParseError("duplicate disclosure receipt")
    return dict(total=total, page=page, pages=pages, rows=output)


def parse_versions(payload: bytes, receipt_no: str | None = None) -> list[dict]:
    """Return main-document versions, optionally bound to the requested receipt.

    Acquisition must supply its search-row receipt. The optional form supports
    inspecting a standalone saved selector without claiming a receipt linkage.
    """
    root = _parse(payload)
    if receipt_no is not None:
        _number(receipt_no, "requested receipt number")
        receipts = [node for node in root.find("input")
                    if node.attrs.get("id") == "acptNo" or node.attrs.get("name") == "acptNo"]
        if len(receipts) != 2 or any(
            node.attrs.get("type") != "hidden" or node.attrs.get("id") != "acptNo"
            or node.attrs.get("name") != "acptNo" or node.attrs.get("value") != receipt_no
            for node in receipts
        ):
            raise KindParseError("viewer hidden receipt fields disagree with request")
        scripts = "\n".join(node.text() for node in root.find("script"))
        assignments = re.findall(r"\b_TRK_PN\s*=", scripts)
        tracking = re.findall(r'\bvar\s+_TRK_PN\s*=\s*([\"\'])([0-9]{14})\1\s*;', scripts)
        if len(assignments) != 1 or len(tracking) != 1 or tracking[0][1] != receipt_no:
            raise KindParseError("viewer tracking receipt disagrees with request")
    select = _one([node for node in root.find("select")
                   if node.attrs.get("id") == "mainDoc"], "main-document selector")
    versions = []
    for option in select.find("option"):
        value = option.attrs.get("value")
        if value == "" and option.text() == "본문선택":
            continue
        match = re.fullmatch(r"([0-9]{14})\|[YN]", value or "")
        label = option.text()
        published = re.search(r"\(([0-9]{4})\.([0-9]{2})\.([0-9]{2})\)$", label)
        if match is None or published is None:
            raise KindParseError("unrecognized main-document option")
        publication_on = _date("-".join(published.groups()))
        versions.append(dict(doc_no=_number(match[1], "document number"),
                             publication_on=publication_on, label=label))
    if not versions or len({item["doc_no"] for item in versions}) != len(versions):
        raise KindParseError("empty or duplicate main-document versions")
    return versions


def parse_body_url(payload: bytes, doc_no: str) -> str:
    """Accept only the exact official body URL in the observed setPath call."""
    _number(doc_no, "requested document number")
    root = _parse(payload)
    scripts = "\n".join(node.text() for node in root.find("script"))
    if len(re.findall(r"parent\s*\.\s*setPath\s*\(", scripts)) != 1:
        raise KindParseError("missing or ambiguous body path")
    calls = re.findall(
        r"parent\.setPath\(\s*'([^'\\]*)'\s*,\s*'([^'\\]*)'\s*,\s*'([^'\\]*)'"
        r"\s*,\s*'([^'\\]*)'\s*,\s*'([^'\\]*)'\s*\)\s*;", scripts)
    _, url, server_path, _, _ = _one(calls, "body path call")
    match = re.fullmatch(
        r"https://kind\.krx\.co\.kr(/external/([0-9]{4})/([0-9]{2})/([0-9]{2})/"
        r"[0-9]{6}/([0-9]{14})/[0-9]{5})\.htm", url)
    if match is None or match[5] != doc_no or server_path != match[1]:
        raise KindParseError("unexpected body host, path or document number")
    path_date = _date("-".join(match.group(index) for index in (2, 3, 4)))
    if path_date.replace("-", "") != doc_no[:8]:
        raise KindParseError("body path date disagrees with document number")
    return url


def _row_texts(table: _Element) -> list[list[str]]:
    rows = table.find("tr")
    if len(rows) != len(_children(table, {"tr"})):
        raise KindParseError("unexpected nested notice table")
    return [[cell.text() for cell in _cells(row)] for row in rows]


def _field(rows: list[list[str]], label: str) -> str:
    row = _one([row for row in rows if row and _compact(row[0]) == label], label)
    if len(row) != 2 or not row[1]:
        raise KindParseError("unexpected notice field structure")
    return row[1]


def _company(rows: list[list[str]], label: str) -> str:
    row = _one([row for row in rows if row and _compact(row[0]) == label], label)
    if len(row) != 4 or row[1:3] != ["정식명", "한글"] or not row[3]:
        raise KindParseError("unexpected official company-name structure")
    return row[3]


def _security(rows: list[list[str]], heading: list[str]) -> dict:
    index = _one([index for index, row in enumerate(rows)
                  if [_compact(cell) for cell in row] == heading], "common-share code header")
    if index + 1 >= len(rows):
        raise KindParseError("missing common-share code row")
    row = rows[index + 1]
    columns = heading[1:]
    if len(row) != len(columns) or row[0] != "보통주":
        raise KindParseError("unexpected common-share code row")
    # Both inspected forms have exactly one security row, followed by field 3.
    # Do not silently ignore a second common/preferred row in an unseen form.
    if (index + 2 >= len(rows) or not rows[index + 2]
            or not _compact(rows[index + 2][0]).startswith("3.")):
        raise KindParseError("unrecognized multiple-security notice structure")
    short_code = row[columns.index("단축코드")]
    match = re.fullmatch(r"A([A-Z0-9]{6})", short_code)
    if match is None:
        raise KindParseError("invalid explicit short code")
    isin = row[columns.index("표준코드")] if "표준코드" in columns else None
    if isin is not None and re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin) is None:
        raise KindParseError("invalid explicit ISIN")
    return dict(code=match[1], short_code=short_code, security_kind="보통주", isin=isin)


def parse_notice(payload: bytes, kind: str) -> dict:
    """Extract only labelled corporate metadata from an inspected notice form.

    A rename has no code in the observed form. Its before/after names and date
    must be linked externally to separate code evidence; no issuer ID is used.
    """
    titles = {"additional": "추가상장", "rename": "변경상장(상호변경)",
              "spac_listing": "SPAC소멸합병상장"}
    if kind not in titles:
        raise KindParseError("unsupported notice kind")
    root = _parse(payload)
    title = _one([node for node in root.find("p")
                  if node.attrs.get("class") == "SECTION-1"], "notice title")
    if title.text() != titles[kind]:
        raise KindParseError("notice title disagrees with requested kind")
    tables = root.find("table")
    if len(tables) != (1 if kind == "spac_listing" else 3):
        raise KindParseError("unrecognized notice table count")
    rows = _row_texts(tables[0])
    if kind == "rename":
        if any("단축코드" in cell for table in tables for row in _row_texts(table) for cell in row):
            raise KindParseError("unrecognized rename code-bearing form")
        before = _field(rows, "1.변경전상호")
        after = _company(rows, "3.변경후상호")
        if _field(rows, "2.변경상장사유") != "상호변경":
            raise KindParseError("unexpected rename reason")
        return dict(kind=kind, company_name=after, before_company_name=before,
                    after_company_name=after, listing_on=_date(_field(rows, "4.상장일")),
                    code=None, short_code=None, security_kind=None, isin=None)
    if kind == "additional":
        security = _security(rows, ["2.추가주식의종류와수", "주권종류", "단축코드", "추가주식수(주)"])
        issuance = _row_texts(tables[2])
        if (len(issuance) != 2 or [_compact(cell) for cell in issuance[0]] !=
                ["주권종류", "추가사유", "추가주식수(주)", "발행/전환/행사가액(원)", "발행회수(발행일)"]
                or len(issuance[1]) != 6 or issuance[1][0] != "보통주"
                or issuance[1][1] != "타법인흡수합병"):
            raise KindParseError("unrecognized merger additional-listing reason")
        return dict(kind=kind, company_name=_field(rows, "1.회사명"),
                    listing_on=_date(_field(rows, "5.상장일")),
                    merger_reason=issuance[1][1], **security)
    security = _security(rows, ["2.주식의종류와수", "주권종류", "표준코드", "단축코드", "주식수(주)"])
    return dict(kind=kind, company_name=_company(rows, "1.회사명"),
                listing_on=_date(_field(rows, "10.상장일(매매개시일)")),
                absorbed_spac_name=_field(rows, "11.피합병법인(SPAC)"), **security)
