"""Invented public-list HTML only; no acquisition, saved market bodies or SQL."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime
import hashlib
from html import escape

import pytest

from research import intraday_ed_disclosures as dart


REQUEST = dart.ListingRequest("000001", date(2025, 9, 4), date(2026, 9, 23), 1)


def row(number=1, *, receipt="20260303001000", corp="00000001", title="사업보고서 (2025.12)",
        observed=None):
    observed = observed or f"{receipt[:4]}.{receipt[4:6]}.{receipt[6:8]}"
    return (
        f"<tr><td>{number}</td><td><a onclick=\"openCorpInfoNew('{corp}', 'winCorpInfo', "
        "'/dsae001/selectPopup.ax');\">발명회사</a></td><td>"
        f"<a href=\"/dsaf001/main.do?rcpNo={receipt}\" id=\"r_{receipt}\" "
        f"onclick=\"openReportViewer('{receipt}',''); return false;\">{escape(title)}</a>"
        f"</td><td>발명제출인</td><td>{observed}</td><td>유</td></tr>"
    )


def document(rows, *, page=1, pages=1, total=None):
    if total is None:
        total = len(rows)
    return (
        "<!doctype html><html><head><meta charset='utf-8'></head><body><table>"
        f"<tbody id='tbody'>{''.join(rows)}</tbody></table>"
        f"<div class='pageInfo'>[{page}/{pages}] [총 {total:,}건]</div></body></html>"
    ).encode()


def multi_page(*, total=101, corp="00000001", query_code="000001", page=1):
    start = (page - 1) * 100 + 1
    end = min(page * 100, total)
    raw = document([
        row(number, receipt=f"20260303{10000 - number:06d}", corp=corp)
        for number in range(start, end + 1)
    ], page=page, pages=(total + 99) // 100, total=total)
    return dart.parse_listing_page(raw, replace(REQUEST, page=page, query_code=query_code))


def test_form_preserves_repeated_types_and_literal_inclusive_bounds():
    form = dart.listing_form(REQUEST)
    assert [value for key, value in form if key == "publicType"] == ["A001", "A002", "A003"]
    assert dict(form) == {
        "currentPage": "1", "maxResults": "100", "maxLinks": "10", "sort": "date", "series": "desc",
        "textCrpCik": "", "pageGubun": "corp", "textCrpNm": "000001", "autoSearchCorp": "Y",
        "startDate": "20250904", "endDate": "20260923", "publicType": "A003",
    }
    assert dart.SOURCE == "https://dart.fss.or.kr/dsab001/search.ax"


@pytest.mark.parametrize("changes", [
    {"query_code": "A00001"}, {"query_code": "１２３４５６"}, {"query_code": "00001"},
    {"query_code": 1}, {"page": 0}, {"page": 4}, {"page": True}, {"page": "1"},
    {"start_date": date(2025, 9, 3)}, {"end_date": date(2026, 9, 24)},
    {"start_date": date(2026, 9, 23), "end_date": date(2025, 9, 4)},
    {"start_date": "2025-09-04"}, {"end_date": datetime(2026, 9, 23)},
])
def test_request_refuses_unbounded_or_ambiguous_inputs(changes):
    with pytest.raises(dart.Refusal):
        replace(REQUEST, **changes)


def test_all_original_corrected_and_withdrawn_rows_remain_date_only():
    raw = document([
        row(1, receipt="20260303001000"),
        row(2, receipt="20260303001001", title="[기재정정][첨부추가]사업보고서 (2025.12)"),
        row(3, receipt="20260303001002", title="[철회]분기보고서 (2025.09)"),
        row(4, receipt="20260303001003", title="[첨부정정]반기보고서 (2025.06)"),
    ])
    page = dart.parse_listing_page(raw, REQUEST)
    listing = dart.combine_listing_pages([page])
    assert len(listing.rows) == 4
    assert [item.public_type for item in listing.rows] == ["A001", "A001", "A003", "A002"]
    assert [item.is_original for item in listing.rows] == [True, False, False, False]
    amended = listing.rows[1]
    assert amended.version_prefixes == ("기재정정", "첨부추가")
    assert amended.title == "[기재정정][첨부추가]사업보고서 (2025.12)"
    assert amended.receipt == "20260303001001"
    assert amended.query_code == "000001" and amended.corp_cik == "00000001"
    assert amended.observation_date == date(2026, 3, 3)
    assert amended.source_event_at is amended.available_at is amended.first_seen_at is amended.is_final is None
    assert amended.source_page == 1 and amended.source == dart.SOURCE
    assert amended.wrapper_url == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260303001001"
    assert page.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert dart.parse_listing_page(raw, REQUEST) == page
    with pytest.raises(FrozenInstanceError):
        amended.title = "changed"


@pytest.mark.parametrize("receipt", ["20250904000001", "20260923000001"])
def test_request_window_is_inclusive(receipt):
    raw = document([row(receipt=receipt, title="분기보고서 (2025.06)")])
    assert dart.parse_listing_page(raw, REQUEST).rows[0].receipt == receipt


def test_complete_three_pages_preserve_the_entire_population():
    pages = [multi_page(total=201, page=number) for number in (1, 2, 3)]
    listing = dart.combine_listing_pages(pages)
    assert len(listing.rows) == 201
    assert [item.row_number for item in listing.rows] == list(range(1, 202))
    assert [item.source_page for item in listing.rows].count(1) == 100
    assert [item.source_page for item in listing.rows].count(2) == 100
    assert listing.rows[-1].source_page == 3
    assert listing.pages == tuple(pages)


@pytest.mark.parametrize("raw", [
    b"", b"<html><body>Unavailable</body></html>", b"\xff",
    document([], page=1, pages=0, total=0),
    document(["<tr><td colspan='6'>조회된 데이터가 없습니다.</td></tr>"], total=0),
    document([], total=1),
    document([row()], total=2),
    document([row()], page=1, pages=2, total=1),
    document([row()], page=1, pages=4, total=301),
    document([row()]).replace(b"</tbody>", b""),
    document([row()]).replace(b"</td>", b"", 1),
    document([row()]).replace(b"id='tbody'", b"id='tbody' id='other'"),
    document([row()]).replace(b"<tbody id='tbody'>", b"<tbody id='tbody'><tbody>"),
    document([row()]).replace(b"</tbody>", b"<tr><td>unexpected</td></tr></tbody>"),
    document([row()]).replace(b"<tbody id='tbody'>", b"<tbody id='tbody'>unexpected"),
    document([row()]).replace(b"<tr>", b"<tr><th>unexpected</th>"),
    document([row()]).replace(b"pageInfo", b"pageInfo changed"),
    document([row()]).replace(b"[1/1]", b"[" + b"9" * 5000 + b"/1]"),
    document([row()]).replace(b"[1/1]", b"[01/1]"),
    document([row()]).replace(b"[1/1]", b"[01/1]") + b"\x00",
    document([row()]).replace(b"</body>", b"<div class='pageInfo'>[1/1] [total 1]</div></body>"),
    b"a" * (dart.MAX_BYTES + 1),
])
def test_empty_partial_changed_and_malformed_pages_are_unknown(raw):
    with pytest.raises(dart.Refusal):
        dart.parse_listing_page(raw, REQUEST)


@pytest.mark.parametrize("kwargs", [
    {"receipt": "2026030300100"}, {"receipt": "20261303001000"},
    {"receipt": "20260230001000"}, {"receipt": "20250903001000", "title": "분기보고서 (2025.06)"},
    {"receipt": "20260924001000"}, {"observed": "2026.03.04"}, {"observed": "20260303"},
    {"corp": "0000001"}, {"title": "사업보고서 (2025.13)"},
    {"title": "사업보고서 (2027.01)"}, {"title": "사업보고서 (0000.01)"},
    {"title": "영업(잠정)실적(공정공시)"}, {"title": "사업보고서 (2025.12) 추가문구"},
    {"title": "[발명접두어]사업보고서 (2025.12)"},
    {"title": "[기재정정][기재정정]사업보고서 (2025.12)"},
])
def test_unsupported_identity_title_and_date_refuse(kwargs):
    with pytest.raises(dart.Refusal):
        dart.parse_listing_page(document([row(**kwargs)]), REQUEST)


@pytest.mark.parametrize("change", [
    lambda raw: raw.replace(b"id=\"r_20260303001000\"", b"id=\"r_20260303001001\""),
    lambda raw: raw.replace(b"openReportViewer('20260303001000'", b"openReportViewer('20260303001001'"),
    lambda raw: raw.replace(b"/dsaf001/main.do?", b"https://foreign.invalid/dsaf001/main.do?"),
    lambda raw: raw.replace(b"</a></td><td>", b"</a><a>duplicate</a></td><td>", 1),
    lambda raw: raw.replace(b"return false;\">", b"return false;\"><a>duplicate</a>"),
    lambda raw: raw.replace(b"<td>1</td>", b"<td>2</td>"),
])
def test_conflicting_links_and_changed_row_structure_refuse(change):
    with pytest.raises(dart.Refusal):
        dart.parse_listing_page(change(document([row()])), REQUEST)


def test_wrong_current_page_refuses():
    with pytest.raises(dart.Refusal, match="wrong page"):
        dart.parse_listing_page(document([row()]), replace(REQUEST, page=2))


def test_mixed_corporations_and_duplicate_receipts_within_a_page_refuse():
    with pytest.raises(dart.Refusal, match="corporate IDs"):
        dart.parse_listing_page(document([row(1), row(2, receipt="20260303001001", corp="00000002")]), REQUEST)
    with pytest.raises(dart.Refusal, match="duplicated report receipt"):
        dart.parse_listing_page(document([row(1), row(2)]), REQUEST)


def test_unsorted_dates_refuse_but_same_date_receipt_order_is_unrestricted():
    with pytest.raises(dart.Refusal, match="date sort"):
        dart.parse_listing_page(document([row(1), row(2, receipt="20260304001001")]), REQUEST)
    assert len(dart.parse_listing_page(document([row(1), row(2, receipt="20260303001001")]), REQUEST).rows) == 2


def test_complete_population_requires_all_unique_pages_of_the_same_query():
    first = multi_page(page=1)
    second = multi_page(page=2)
    for pages in ([], [first], [first, first], [second, first]):
        with pytest.raises(dart.Refusal):
            dart.combine_listing_pages(pages)
    with pytest.raises(dart.Refusal, match="query changed"):
        dart.combine_listing_pages([first, multi_page(page=2, query_code="000002")])
    with pytest.raises(dart.Refusal, match="corporate ID changed"):
        dart.combine_listing_pages([first, multi_page(page=2, corp="00000002")])
    with pytest.raises(dart.Refusal, match="source changed"):
        dart.combine_listing_pages([first, multi_page(page=2, total=102)])
    assert len(dart.combine_listing_pages([first, second]).rows) == 101


def test_duplicate_receipt_across_pages_cannot_be_deduped_into_complete_coverage():
    first = multi_page(page=1)
    raw = document([row(101, receipt=first.rows[0].receipt)], page=2, pages=2, total=101)
    second = dart.parse_listing_page(raw, replace(REQUEST, page=2))
    with pytest.raises(dart.Refusal, match="across pages"):
        dart.combine_listing_pages([first, second])


def test_different_date_window_between_pages_refuses():
    first = multi_page(page=1)
    second = multi_page(page=2)
    second = replace(second, request=replace(second.request, start_date=date(2025, 9, 5)))
    with pytest.raises(dart.Refusal, match="query changed"):
        dart.combine_listing_pages([first, second])
