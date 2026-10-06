"""Counterexamples to trusting a partial list or a contemporary name join."""

import hashlib
import json
import sqlite3

import pytest

from research import activity_mergers as am
from test_activity_readiness import databases


def row(name="율촌", *, issuer="14606", process="20220704000031", kind="소멸", day="2023-09-08"):
    return f"""<tr onclick="fnDetailView('{issuer}','{process}')">
      <td title="{name}"><img alt='코스닥'>{name}<img alt='투자경고종목'></td>
      <td>{day}</td><td>SPAC {kind}합병</td><td>주권</td>
      <td>제조업</td><td>대한민국</td><td>증권사</td></tr>"""


def page(rows=None, *, total=1, pages=1):
    return (f"<table><thead><tr></tr></thead><tbody>{row() if rows is None else rows}</tbody></table>"
            f"전체 <em>{total}</em>건 : <strong>1</strong>/{pages}&nbsp;").encode()


def test_observed_seven_column_shape_keeps_opaque_identifiers_and_no_price_fields():
    result = am.parse_listings(page(row(issuer="0004V", day="2026-01-14")))[0]
    assert result["issuer_id"] == "0004V"
    assert result["process_id"] == "20220704000031"
    assert result["market"] == "코스닥"
    assert result["merger_type"] == "spac_disappears"
    assert not {"code", "known_on", "effective_on", "price", "eligible"} & result.keys()


@pytest.mark.parametrize("payload,match", [
    (b"<html>temporarily unavailable</html>", "pagination"),
    (page(total=2), "incomplete"),
    (page(pages=2), "incomplete"),
    (page("", total=0), "empty"),
    (page(row() + row(), total=2), "duplicate"),
    (page(row() + row(day="2023-09-09"), total=2), "duplicate"),
    (page().replace(b"fnDetailView", b"changedDetail"), "identifiers"),
    (page().replace(b"</tbody>", b""), "truncated"),
    (page().replace(b"</tr></tbody>", b"</tbody>"), "unclosed"),
    (page(row(day="2018-12-31")), "outside"),
    (page(row(day="2026-09-19")), "outside"),
    (page(row(kind="unknown")), "type"),
    (page().replace("주권".encode(), "수익증권".encode()), "security"),
    (page().replace("제조업</td>".encode(), "제조업</td><td>100</td>".encode()), "columns"),
    (page().replace('title="율촌"'.encode(), 'title="다른 회사"'.encode()), "name"),
])
def test_source_drift_and_truncation_fail_closed(payload, match):
    with pytest.raises(ValueError, match=match):
        am.parse_listings(payload)


def test_two_merger_types_and_source_order_are_preserved_deterministically():
    older = row("네오셈", issuer="25359", kind="존속", day="2019-01-31")
    listings = am.parse_listings(page(row() + older, total=2))
    assert [r["merger_type"] for r in listings] == ["spac_survives", "spac_disappears"]
    assert listings == am.parse_listings(page(older + row(), total=2))


def test_reconciliation_keeps_delisted_ambiguous_missing_and_unscanned_names(tmp_path):
    scan, master = databases(tmp_path)
    with sqlite3.connect(master) as con:
        con.execute("INSERT INTO krx_universe VALUES('2026-10-05','0004V0','미수집','KR70004V0001','ST','KOSDAQ')")
        con.execute("INSERT INTO krx_universe VALUES('2026-09-14','999999','삼성전자','KR7999999000','ST','KOSPI')")
    before = [p.read_bytes() for p in (scan, master)]
    names = ["삼성전자", "유안타제8호스팩", "수성웹툰", "미수집", "이름변경미확인"]
    listings = am.parse_listings(page("".join(row(n, issuer=str(i)) for i, n in enumerate(names)), total=5))
    metadata = am.read_metadata(scan, master)
    result = am.reconcile(listings, metadata)
    assert result["match_counts"] == {"ambiguous": 1, "unique_name_candidate": 3, "unmatched": 1}
    matched = {r["name"]: r for r in result["records"]}
    assert matched["유안타제8호스팩"]["candidates"][0]["kind"] == "delisted"
    assert matched["삼성전자"]["candidates"][0]["code"] == "005930"
    assert len(matched["수성웹툰"]["candidates"]) == 2
    assert matched["미수집"]["candidates"][0]["scan_status"] == "absent"
    assert matched["이름변경미확인"]["candidates"] == []
    assert [p.read_bytes() for p in (scan, master)] == before
    assert result == am.reconcile(listings, am.read_metadata(scan, master))
    metadata["progress"]["005930"] = "failed:transport"
    assert am.reconcile(listings, metadata)["metadata_sha256"] != result["metadata_sha256"]


def test_reserved_panel_refused_before_any_identity_query(tmp_path, monkeypatch):
    scan, master = databases(tmp_path, start="19910828")
    statements = []
    original = am.readonly

    def traced(path):
        con = original(path)
        con.set_trace_callback(statements.append)
        return con

    monkeypatch.setattr(am, "readonly", traced)
    with pytest.raises(ValueError, match="spent"):
        am.read_metadata(scan, master)
    assert [s for s in statements if s.startswith("SELECT")] == [
        "SELECT start,end FROM scan_panel WHERE id=1"]


def test_absent_snapshot_refuses(tmp_path):
    scan, master = databases(tmp_path)
    with sqlite3.connect(master) as con:
        con.execute("DELETE FROM krx_delisted")
    with pytest.raises(ValueError, match="missing identity snapshot"):
        am.read_metadata(scan, master)


def test_cli_retains_source_hash_and_wont_overwrite_evidence(tmp_path, monkeypatch):
    scan, master = databases(tmp_path)
    monkeypatch.setattr(am.subprocess, "check_output", lambda cmd, **kw: "a" * 40 if kw else b"")
    calls = []
    payload = page()
    monkeypatch.setattr(am, "fetch_listings", lambda: calls.append(True) or payload)
    output = tmp_path / "audit"
    args = ["--scan-db", str(scan), "--identity-db", str(master), "--output-dir", str(output)]
    assert am.main(args) == 0
    result = json.loads((output / "result.json").read_text())
    assert result["source_sha256"] == hashlib.sha256(payload).hexdigest()
    assert (output / "kind-mergers.html").read_bytes() == payload
    with pytest.raises(FileExistsError):
        am.main(args)
    assert calls == [True]


def test_dirty_source_refused_before_read_or_network(tmp_path, monkeypatch):
    monkeypatch.setattr(am.subprocess, "check_output", lambda *a, **kw: b" M activity_mergers.py")
    monkeypatch.setattr(am, "read_metadata", lambda *a: pytest.fail("must not open database"))
    monkeypatch.setattr(am, "fetch_listings", lambda: pytest.fail("must not fetch"))
    with pytest.raises(ValueError, match="committed"):
        am.main(["--scan-db", "absent", "--identity-db", "absent", "--output-dir", str(tmp_path)])


def test_fetch_is_bounded_and_requests_only_observed_metadata_fields(monkeypatch):
    class Oversized:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, count):
            assert count == am.MAX_BYTES + 1
            return b"x" * count

    def open_response(request, timeout):
        assert timeout == 30
        assert "choicType" not in request.full_url
        assert "fromDate=2019-01-02" in request.full_url
        assert "listTypeArrStr=06%7C07%7C" in request.full_url
        return Oversized()

    monkeypatch.setattr(am, "urlopen", open_response)
    with pytest.raises(ValueError, match="oversized"):
        am.fetch_listings()
