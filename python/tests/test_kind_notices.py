"""Corporate-only counterexamples derived from observed KIND table layouts.

The reduced markup preserves the source's headers and spans. It deliberately
omits shareholders, officers, addresses, scripts, and unrelated public filings.
"""

import pytest

from research import kind_notices as kn


START = "2019-01-02"
END = "2019-02-07"
DOC_NO = "20190130002090"
BODY_URL = f"https://kind.krx.co.kr/external/2019/01/30/000781/{DOC_NO}/70791.htm"


def search_row(*, receipt="20190130000781", at="2019-01-30 17:01", issuer="25359", ordinal=1):
    return f"""<tr><td>{ordinal}</td><td>{at}</td>
      <td><img alt="코스닥"><a onclick="companysummary_open('{issuer}'); return false;"
          title="네오셈">네오셈</a></td>
      <td><a onclick="openDisclsViewer('{receipt}','')"
          title="추가상장(타법인흡수합병)">추가상장(타법인흡수합병)</a></td>
      <td>코스닥시장본부</td><td><a onclick="fnPopStockPrices('{issuer}')">차트</a></td>
      </tr>"""


def search_page(rows=None, *, total=1, page=1, pages=1):
    return ("<section><table class='list type-00 mt10' summary='번호, 시간, 회사명, 공시제목, 제출인, 차트/주가'>"
            "<colgroup><col><col><col><col><col><col></colgroup><thead><tr></tr></thead><tbody>"
            + (search_row() if rows is None else rows)
            + f"</tbody></table>전체 <em>{total}</em>건 : <strong>{page}</strong>/{pages}&nbsp;"
            + "<select id='currentPageSize'><option value='100' selected='selected'>100건</option></select></section>"
            ).encode()


def versions(options=None):
    if options is None:
        options = f"<option value='{DOC_NO}|Y'>추가상장 (2019.01.30)</option>"
    return ("<html><body><select id='mainDoc' name='mainDoc'><option value=''>본문선택</option>"
            + options + "</select></body></html>").encode()


def contents(url=BODY_URL, *, first="", method="setPath"):
    return (f"<html><head><script>parent.{method}('{first}','{url}',"
            f"'/external/2019/01/30/000781/{DOC_NO}/70791','03','11');"
            "</script></head><body></body></html>").encode()


def additional(*, code="A253590", security="보통주", extra_security="", reason="타법인흡수합병"):
    row_count = 3 if extra_security else 2
    return f"""<html><body><p class="SECTION-1"><a name="#2">추가상장</a></p>
    <table>
      <tr><td>1.회사명</td><td colspan="3">대신밸런스제3호기업인수목적 주식회사</td></tr>
      <tr><td rowspan="{row_count}">2.추가주식의 종류와 수</td>
          <td>주권종류</td><td>단축코드</td><td>추가주식수(주)</td></tr>
      {extra_security}<tr><td>{security}</td><td>{code}</td><td>27,631,514</td></tr>
      <tr><td>3.1주당 액면가/자본금(원)</td><td colspan="3">100</td></tr>
      <tr><td rowspan="2">4.추가상장후 총발행주식수</td>
          <td>주권종류</td><td>주식수(주)</td><td>배당기산일</td></tr>
      <tr><td>보통주</td><td>33,141,514</td><td>2019-01-01</td></tr>
      <tr><td>5.상장일</td><td colspan="3">2019-01-31</td></tr>
      <tr><td>6.기타</td><td colspan="3">참고번호 A999999</td></tr>
    </table>
    <table><tr><td>【발행내역】</td></tr></table>
    <table>
      <tr><th>주권종류</th><th colspan="2">추가사유</th><th>추가주식수(주)</th>
          <th>발행/전환/행사가액(원)</th><th>발행회수(발행일)</th></tr>
      <tr><td>보통주</td><td>{reason}</td><td>-</td><td>27,631,514</td>
          <td>2,100</td><td>제3회('19.01.24)</td></tr>
    </table></body></html>""".encode()


def rename():
    return """<html><body><p class="SECTION-1"><a name="#2">변경상장(상호변경)</a></p><table>
      <tr><td>1.변경전 상호</td><td colspan="3">대신밸런스제3호기업인수목적 주식회사</td></tr>
      <tr><td>2.변경상장사유</td><td colspan="3">상호변경</td></tr>
      <tr><td rowspan="4">3.변경후 상호</td><td rowspan="2">정식명</td>
          <td>한글</td><td>(주)네오셈</td></tr>
      <tr><td>영문</td><td>Neosem Inc.</td></tr>
      <tr><td rowspan="2">약명</td><td>한글</td><td>네오셈</td></tr>
      <tr><td>영문</td><td>Neosem</td></tr>
      <tr><td>4.상장일</td><td colspan="3">2019-01-31</td></tr>
      <tr><td>5.비고</td><td colspan="3">참고번호 A253590</td></tr>
    </table><table><tr><td>【발행내역】</td></tr></table><table>
      <tr><th>발행회수</th><th>발행일</th><th>비고</th></tr>
      <tr><td>제4회</td><td>2019-01-24</td><td>-</td></tr>
    </table></body></html>""".encode()


def spac_listing(*, code="A462310", isin="KR7462310004", security="보통주"):
    return f"""<html><body><p class="SECTION-1"><a name="#2">SPAC소멸합병상장</a></p><table>
      <tr><td rowspan="4">1.회사명</td><td rowspan="2">정식명</td>
          <td>한글</td><td colspan="4">주식회사 뉴키즈온</td></tr>
      <tr><td>영문</td><td colspan="4">New Kids On Co.,Ltd.</td></tr>
      <tr><td rowspan="2">약명</td><td>한글</td><td colspan="4">뉴키즈온</td></tr>
      <tr><td>영문</td><td colspan="4">New Kids On</td></tr>
      <tr><td colspan="3" rowspan="2">2.주식의 종류와 수</td>
          <td>주권 종류</td><td>표준코드</td><td>단축코드</td><td>주식수(주)</td></tr>
      <tr><td>{security}</td><td>{isin}</td><td>{code}</td><td>7,902,000</td></tr>
      <tr><td colspan="3">3.액면가(원)</td><td colspan="4">100</td></tr>
      <tr><td colspan="3">9.승인일</td><td colspan="4">2025년 07월 07일</td></tr>
      <tr><td colspan="3">10.상장일(매매개시일)</td><td colspan="4">2025년 07월 09일</td></tr>
      <tr><td colspan="3">11.피합병법인(SPAC)</td><td colspan="4">KB제28호스팩</td></tr>
    </table></body></html>""".encode()


def test_search_preserves_receipt_time_and_opaque_issuer_without_inventing_code():
    result = kn.parse_search(search_page(search_row(issuer="0004V")), START, END)
    assert (result["total"], result["page"], result["pages"]) == (1, 1, 1)
    assert result["rows"] == [{
        "receipt_no": "20190130000781", "published_at": "2019-01-30T17:01:00+09:00",
        "title": "추가상장(타법인흡수합병)", "issuer_name": "네오셈",
        "issuer_id": "0004V", "submitter": "코스닥시장본부",
    }]


def test_search_accepts_full_page_and_partial_final_page_with_explicit_counts():
    rows = "".join(search_row(receipt=f"20190130{i:06d}", ordinal=101 - i) for i in range(100))
    first = kn.parse_search(search_page(rows, total=101, pages=2), START, END)
    last = kn.parse_search(search_page(total=101, page=2, pages=2), START, END, page=2)
    assert len(first["rows"]) == 100
    assert (last["page"], len(last["rows"])) == (2, 1)


@pytest.mark.parametrize("payload,page", [
    (b"<html>temporarily unavailable</html>", 1),
    (search_page(total=2), 1),
    (search_page(total=101, pages=2), 1),
    (search_page(total=101, page=2, pages=2), 1),
    (search_page(total=101, page=1, pages=2), 2),
    (search_page(total=1, pages=2), 1),
    (search_page().replace(b"</tbody>", b""), 1),
    (search_page().replace(b"</tr></tbody>", b"</tbody>"), 1),
    (search_page().replace(b"openDisclsViewer", b"unknownViewer"), 1),
    (search_page().replace(b"companysummary_open", b"unknownCompany"), 1),
    (search_page(search_row(at="2019-01-01 23:59")), 1),
    (search_page(search_row(at="2019-02-08 00:00")), 1),
    (search_page(search_row(at="2019-02-30 15:00")), 1),
])
def test_search_refuses_partial_wrong_page_malformed_and_out_of_window(payload, page):
    with pytest.raises(kn.KindParseError):
        kn.parse_search(payload, START, END, page=page)


@pytest.mark.parametrize("second", [search_row(), search_row(at="2019-01-31 10:00")])
def test_duplicate_receipt_cannot_be_counted_twice_or_silently_replaced(second):
    with pytest.raises(kn.KindParseError):
        kn.parse_search(search_page(search_row(ordinal=2) + second, total=2), START, END)


def test_version_dates_remain_separate_and_attachments_are_not_versions():
    original = f"<option value='{DOC_NO}|Y'>추가상장 (2019.01.30)</option>"
    correction = "<option value='20190201000001|Y' selected='selected'>[정정]추가상장 (2019.02.01)</option>"
    attachment = ("<select id='attachDoc'><option value='20190202000001|Y'>"
                  "첨부서류 (2019.02.02)</option></select>")
    payload = versions(correction + original).replace(b"</body>", attachment.encode() + b"</body>")
    actual = {v["doc_no"]: v for v in kn.parse_versions(payload)}
    assert actual == {
        DOC_NO: {"doc_no": DOC_NO, "publication_on": "2019-01-30", "label": "추가상장 (2019.01.30)"},
        "20190201000001": {"doc_no": "20190201000001", "publication_on": "2019-02-01",
                           "label": "[정정]추가상장 (2019.02.01)"},
    }


@pytest.mark.parametrize("payload", [
    b"<html><select id='attachDoc'><option value='20190130002090|Y'>attachment</option></select></html>",
    versions(""),
    versions().replace(b"</select>", b""),
    versions().replace(b"2019.01.30", b"2019.02.30"),
    versions().replace(b"20190130002090|Y", b"not-a-document|Y"),
    versions(f"<option value='{DOC_NO}|Y'>추가상장 (2019.01.30)</option>" * 2),
    versions(f"<option value='{DOC_NO}|Y'>추가상장 (2019.01.30)</option>"
             f"<option value='{DOC_NO}|Y'>[정정]추가상장 (2019.02.01)</option>"),
])
def test_missing_malformed_or_duplicate_versions_fail_closed(payload):
    with pytest.raises(kn.KindParseError):
        kn.parse_versions(payload)


def receipt_viewer(receipt="20190130000781"):
    identity = (f'<script>var _TRK_PN = "{receipt}";</script>'
                f'<form id="docdownloadform"><input type="hidden" id="acptNo" '
                f'name="acptNo" value="{receipt}" /></form>'
                f'<form id="frm"><input type="hidden" id="acptNo" '
                f'name="acptNo" value="{receipt}" /></form>')
    return versions().replace(b"<body>", b"<body>" + identity.encode())


def test_viewer_versions_bind_both_hidden_fields_and_tracking_to_requested_receipt():
    actual = kn.parse_versions(receipt_viewer(), "20190130000781")
    assert actual == [{"doc_no": DOC_NO, "publication_on": "2019-01-30",
                       "label": "추가상장 (2019.01.30)"}]


@pytest.mark.parametrize("payload", [
    versions(),
    receipt_viewer("20190130000782"),
    receipt_viewer().replace(b'var _TRK_PN = "20190130000781";', b''),
    receipt_viewer().replace(b'var _TRK_PN = "20190130000781";',
                            b'var _TRK_PN = "20190130000782";'),
    receipt_viewer().replace(b'var _TRK_PN = "20190130000781";',
                            b'var _TRK_PN = "20190130000781"; _TRK_PN = "20190130000782";'),
    receipt_viewer().replace(b'name="acptNo" value="20190130000781"',
                            b'name="acptNo" value="20190130000782"', 1),
    receipt_viewer().replace(b'<input type="hidden" id="acptNo" name="acptNo" '
                            b'value="20190130000781" />', b'', 1),
    receipt_viewer().replace(b'id="acptNo"', b'id="other"', 1),
    receipt_viewer().replace(b'name="acptNo"', b'name="other"', 1),
    receipt_viewer().replace(b'type="hidden"', b'type="text"', 1),
    receipt_viewer().replace(b'</body>', b'<input type="hidden" id="acptNo" '
                            b'name="acptNo" value="20190130000781" /></body>'),
])
def test_viewer_missing_or_conflicting_receipt_identity_fails_closed(payload):
    with pytest.raises(kn.KindParseError):
        kn.parse_versions(payload, "20190130000781")


def test_viewer_second_hidden_receipt_cannot_contradict_first_and_tracking():
    payload = receipt_viewer()
    offset = payload.rindex(b'value="20190130000781"')
    payload = payload[:offset] + payload[offset:].replace(b"20190130000781", b"20190130000782", 1)
    with pytest.raises(kn.KindParseError):
        kn.parse_versions(payload, "20190130000781")


@pytest.mark.parametrize("receipt", ["20190130002090", "20190230000781", "253590"])
def test_viewer_request_must_be_a_valid_matching_receipt_not_document_or_stock_code(receipt):
    with pytest.raises(kn.KindParseError):
        kn.parse_versions(receipt_viewer(), receipt)


def test_body_resolution_uses_exact_second_setpath_argument():
    assert kn.parse_body_url(contents(first="https://unrelated.example/toc.htm"), DOC_NO) == BODY_URL


@pytest.mark.parametrize("payload", [
    contents(BODY_URL.replace(DOC_NO, "20190130000781")),
    contents(BODY_URL.replace("kind.krx.co.kr", "kind.krx.co.kr.attacker.example")),
    contents(BODY_URL.replace("https://", "http://")),
    contents(BODY_URL.replace("/external/", "/other/")),
    contents(BODY_URL.replace("/70791.htm", "/../70791.htm")),
    contents(BODY_URL + "?redirect=https://attacker.example"),
    contents(BODY_URL + "#different"),
    contents("https://attacker.example/body.htm", first=BODY_URL),
    contents(method="setPath2"),
    contents() + contents(),
    b"<html>temporarily unavailable</html>",
])
def test_body_resolution_refuses_wrong_document_or_unobserved_paths(payload):
    with pytest.raises(kn.KindParseError):
        kn.parse_body_url(payload, DOC_NO)


def test_additional_listing_extracts_code_and_merger_reason_from_separate_tables():
    notice = kn.parse_notice(additional(), "additional")
    assert notice["kind"] == "additional"
    assert notice["company_name"] == "대신밸런스제3호기업인수목적 주식회사"
    assert notice["listing_on"] == "2019-01-31"
    assert notice["code"] == "253590"
    assert notice["short_code"] == "A253590"
    assert notice["security_kind"] == "보통주"
    assert notice["isin"] is None
    assert "타법인흡수합병" in notice["merger_reason"]


@pytest.mark.parametrize("security,code", [("우선주", "A253595"), ("보통주", "A123456")])
@pytest.mark.parametrize("first", [True, False])
def test_unobserved_multiple_share_rows_cannot_silently_select_a_stock(security, code, first):
    extra = f"<tr><td>{security}</td><td>{code}</td><td>10</td></tr>"
    payload = additional(extra_security=extra)
    if not first:
        payload = payload.replace(extra.encode(), b"").replace(
            b"<tr><td>3.", extra.encode() + b"<tr><td>3.", 1)
    with pytest.raises(kn.KindParseError):
        kn.parse_notice(payload, "additional")


@pytest.mark.parametrize("code,expected", [("A0004V0", "0004V0"), ("A253590", "253590")])
def test_valid_alphanumeric_short_code_is_not_rebuilt_from_issuer_id(code, expected):
    notice = kn.parse_notice(additional(code=code), "additional")
    assert (notice["code"], notice["short_code"]) == (expected, code)


@pytest.mark.parametrize("payload", [
    additional(code="-"),
    additional(code="25359"),
    additional(code="B253590"),
    additional(code="A25359!"),
    additional(security="우선주"),
    additional(extra_security="<tr><td>보통주</td><td>A123456</td><td>10</td></tr>"),
    additional().replace("단축코드".encode(), "참고번호".encode()),
    additional().replace(b"2019-01-31", b"2019-02-30"),
    additional().replace(b"</table>", b"", 1),
    additional().replace("5.상장일".encode(), "5.기타일".encode()),
    additional(reason="-"),
])
def test_unrelated_code_preferred_only_ambiguity_and_missing_required_evidence_refused(payload):
    with pytest.raises(kn.KindParseError):
        kn.parse_notice(payload, "additional")


def test_rename_links_formal_names_without_claiming_a_code_from_incidental_text():
    notice = kn.parse_notice(rename(), "rename")
    assert notice["before_company_name"] == "대신밸런스제3호기업인수목적 주식회사"
    assert notice["after_company_name"] == "(주)네오셈"
    assert notice["listing_on"] == "2019-01-31"
    assert notice["code"] is None
    assert notice["security_kind"] is None
    assert notice["isin"] is None


def test_spac_disappearance_keeps_operating_company_code_isin_and_absorbed_name():
    notice = kn.parse_notice(spac_listing(), "spac_listing")
    assert notice["kind"] == "spac_listing"
    assert notice["company_name"] == "주식회사 뉴키즈온"
    assert notice["listing_on"] == "2025-07-09"
    assert (notice["code"], notice["short_code"], notice["isin"]) == ("462310", "A462310", "KR7462310004")
    assert notice["security_kind"] == "보통주"
    assert notice["absorbed_spac_name"] == "KB제28호스팩"
    assert not {"absorbed_spac_code", "effective_on", "exchange_ratio", "known_on"} & notice.keys()


@pytest.mark.parametrize("payload,kind", [
    (spac_listing(isin="-"), "spac_listing"),
    (spac_listing(security="우선주"), "spac_listing"),
    (spac_listing().replace("11.피합병법인(SPAC)".encode(), "11.기타".encode()), "spac_listing"),
    (rename().replace("1.변경전 상호".encode(), "1.기타".encode()), "rename"),
    (rename(), "additional"),
    (additional(), "spac_listing"),
    (b"<html>temporarily unavailable</html>", "rename"),
])
def test_notice_shape_changes_cannot_yield_apparently_verified_identity(payload, kind):
    with pytest.raises(kn.KindParseError):
        kn.parse_notice(payload, kind)
