"""Tests for `data.krx_delisted`.

The properties worth guarding are the ones whose failure would corrupt the
survivorship record *quietly*:

- **the two finders must stay disjoint**, or a listed name gets recorded
  as delisted and the bias flips direction
- **a 200 carrying HTML is not success** -- the portal answers a bad `bld`
  that way, so a status check alone takes it for data
- **no session means `HTTP 400 LOGOUT`**, which is a missing cookie rather
  than an auth failure and must not be reported as one
- **`plain_codes` is a floor, not a filter**, and a test that pretended
  otherwise would licence treating its output as common stock
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.parse

import pytest

from data.krx_instrument import is_common_stock
from data.krx_delisted import (
    DELISTED_BLD,
    LISTED_BLD,
    Delisting,
    KrxDelistedError,
    fetch_delistings,
    common_stock,
    plain_codes,
    snapshot,
)
from data.store import (
    connect,
    fetch_krx_delisted,
    krx_delisted_snapshots,
    upsert_krx_delisted,
)

# Real rows, abbreviated: the shapes the portal actually returned on
# 2026-09-20, including the non-plain codes that make `plain_codes` a real
# question rather than a formality.
DELISTED_ROWS = [
    {"short_code": "117930", "codeName": "한진해운", "marketName": "유가증권",
     "full_code": "KR7117930008"},
    {"short_code": "103130", "codeName": "웅진에너지", "marketName": "코스닥",
     "full_code": "KR7103130004"},
    {"short_code": "085370", "codeName": "루트로닉", "marketName": "코스닥",
     "full_code": "KR7085370000"},
    {"short_code": "3686001G", "codeName": "아이씨에이치 5R", "marketName": "코스닥",
     "full_code": "KR43686001G6"},
    {"short_code": "702071KB", "codeName": "윈윈하이일드(A5)", "marketName": "유가증권",
     "full_code": "KR5702071KB8"},
    {"short_code": "007121", "codeName": "국제전자공업1신", "marketName": "코스닥",
     "full_code": "KR7007121008"},
]
LISTED_ROWS = [
    {"short_code": "005930", "codeName": "삼성전자", "marketName": "유가증권",
     "full_code": "KR7005930003"},
    {"short_code": "000660", "codeName": "SK하이닉스", "marketName": "유가증권",
     "full_code": "KR7000660001"},
]


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body.encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _FakeOpener:
    """Stands in for the cookie-carrying opener, keyed on the `bld` in the
    POST body so the two finders can answer differently."""

    def __init__(self, by_bld: dict[str, str]) -> None:
        self.by_bld = by_bld
        self.calls: list[str] = []

    def open(self, request, timeout=None):  # noqa: ARG002
        data = getattr(request, "data", None)
        if data is None:  # the loader page
            return _FakeResponse("<html/>")
        # The body is form-encoded, so `dbms/comm/finder/...` arrives as
        # `dbms%2Fcomm%2Ffinder%2F...`. A substring match on the raw body
        # silently matches nothing, which is how the first version of this
        # fixture "passed" by never reaching the code under test.
        body = urllib.parse.unquote_plus(data.decode())
        for bld, payload in self.by_bld.items():
            if bld in body:
                self.calls.append(bld)
                return _FakeResponse(payload)
        raise AssertionError(f"unexpected request body {body!r}")


def _install(monkeypatch, delisted, listed):
    opener = _FakeOpener(
        {
            DELISTED_BLD: json.dumps({"block1": delisted}),
            LISTED_BLD: json.dumps({"block1": listed}),
        }
    )
    monkeypatch.setattr("data.krx_delisted._opener", lambda: opener)
    return opener


# ================================ the disjointness check, which is the point


def test_the_two_finders_are_checked_for_overlap_rather_than_trusted(monkeypatch):
    """**The failure this guards is silent and one-directional.** If the
    delisted finder ever starts returning listed names too, every one of
    them is recorded as delisted on a date it was still trading — which
    biases a scan in the *opposite* direction to survivorship and is just
    as wrong. The endpoint's name cannot establish this; the data can."""
    overlapping = DELISTED_ROWS + [
        {"short_code": "005930", "codeName": "삼성전자", "marketName": "유가증권",
         "full_code": "KR7005930003"}
    ]
    _install(monkeypatch, overlapping, LISTED_ROWS)
    with pytest.raises(KrxDelistedError, match="BOTH the delisted and listed"):
        fetch_delistings()


def test_the_listed_finder_is_actually_fetched(monkeypatch):
    """A check that never loads the thing it compares against is inert —
    this project has shipped three such fixtures already."""
    opener = _install(monkeypatch, DELISTED_ROWS, LISTED_ROWS)
    fetch_delistings()
    assert LISTED_BLD in opener.calls, "nothing was compared against"
    assert DELISTED_BLD in opener.calls


def test_a_clean_fetch_keeps_every_row_including_the_odd_codes(monkeypatch):
    """Filtering belongs at read time. The record keeps what KRX published,
    so a later reader can confirm a filter was applied rather than assume."""
    _install(monkeypatch, DELISTED_ROWS, LISTED_ROWS)
    out = fetch_delistings()
    assert len(out) == len(DELISTED_ROWS)
    assert Delisting("117930", "유가증권", "한진해운", "KR7117930008") in out
    assert any(d.code == "3686001G" for d in out), "rights entitlement dropped"


# ============================== a 200 is not success on this portal


def test_a_200_carrying_html_is_rejected(monkeypatch):
    """The portal answers an unknown `bld` with HTTP 200 and an HTML body.
    Measured: `finder_dellistisu`, `finder_delisu` and
    `finder_deallistisu` all did exactly that while probing for the real
    id. A status check alone would have taken any of them for data."""
    opener = _FakeOpener({DELISTED_BLD: "<html>not json</html>"})
    monkeypatch.setattr("data.krx_delisted._opener", lambda: opener)
    with pytest.raises(KrxDelistedError, match="non-JSON"):
        fetch_delistings()


def test_an_empty_block1_is_rejected(monkeypatch):
    _install(monkeypatch, [], LISTED_ROWS)
    with pytest.raises(KrxDelistedError, match="no block1 rows"):
        fetch_delistings()


def test_a_transport_failure_does_not_leak_the_url(monkeypatch):
    class _Boom:
        def open(self, request, timeout=None):  # noqa: ARG002
            raise urllib.error.URLError("nope")

    monkeypatch.setattr("data.krx_delisted._opener", lambda: _Boom())
    with pytest.raises(KrxDelistedError, match="request failed: URLError"):
        fetch_delistings()


def test_no_session_cookie_is_its_own_error(monkeypatch):
    """`HTTP 400 LOGOUT` is what every data request answers without a
    `JSESSIONID`. It reads like an auth failure and is not one — there is
    no account involved anywhere here — so the diagnosis belongs in the
    message rather than in a future session's afternoon."""
    import data.krx_delisted as mod

    class _NoCookieOpener:
        addheaders: list = []

        def open(self, url, timeout=None):  # noqa: ARG002
            return _FakeResponse("<html/>")

    monkeypatch.setattr(
        mod.urllib.request, "build_opener", lambda *_a, **_k: _NoCookieOpener()
    )
    with pytest.raises(KrxDelistedError, match="JSESSIONID"):
        mod._opener()


# ================================= plain_codes is a FLOOR, not a filter


def test_plain_codes_drops_rights_and_fund_classes():
    out = plain_codes([Delisting(r["short_code"], "", "", r["full_code"])
                       for r in DELISTED_ROWS])
    assert {d.code for d in out} == {"117930", "103130", "085370", "007121"}


def test_common_stock_is_narrower_than_plain_codes():
    """**312 of the 2,350 plain 6-digit delisted codes are not common
    stock.** `plain_codes` is documented as a floor; this is the filter,
    and it reads the ISIN rather than the code or the name."""
    rows = [
        Delisting("117930", "유가증권", "한진해운", "KR7117930008"),
        Delisting("002365", "유가증권", "SH에너지화학우", "KR7002361001"),
        Delisting("900010", "코스닥", "3노드디지탈", "KYG887121070"),
        Delisting("3686001G", "코스닥", "아이씨에이치 5R", "KR43686001G6"),
    ]
    assert [d.code for d in common_stock(rows)] == ["117930"]


def test_common_stock_now_drops_SPACs_and_REITs(monkeypatch):
    """**This test asserted the opposite on 2026-09-20**, and its own name
    said so: `test_common_stock_CANNOT_drop_a_delisted_etf_or_spac`. The
    gap was real then — a SPAC is legally a 주식회사, so its group code is
    `ST` and its ISIN is `KR7...0`, and it passed every structural test.

    Two **name** rules close it, validated against live ground truth:
    `스팩`/`기업인수목적` matched 70 live names, all 70 carrying `ST`; an
    anchored 리츠 form matched 25 live names covering all 23 live REITs.
    Measured over the real delisted list 2026-09-22, they remove **178
    SPACs and 14 REITs** from the 2,033 the two ISIN rules leave.

    아스팩오일 is in here because it is the name that made the SPAC rule
    anchored: a 코넥스 oil company that a bare `스팩` substring removed.
    """
    rows = [
        Delisting("223040", "코스닥", "교보5호스팩", "KR7223040007"),
        Delisting("088260", "유가증권", "이리츠코크렙", "KR7088260005"),
        Delisting("232360", "코넥스", "아스팩오일", "KR7232360008"),
        Delisting("117930", "유가증권", "한진해운", "KR7117930008"),
    ]
    assert [d.code for d in common_stock(rows)] == ["232360", "117930"]


def test_a_securities_firm_named_메리츠_is_NOT_dropped_as_a_REIT():
    """The anchored form exists because a bare `리츠` match takes 메리츠종금
    with it — the same substring failure 다우기술 showed for 우선주."""
    rows = [Delisting("008560", "유가증권", "메리츠종금", "KR7008560006")]
    assert [d.code for d in common_stock(rows)] == ["008560"]


def test_an_ETN_or_fund_with_a_plain_code_is_dropped_by_its_ISIN_CLASS():
    """`plain_codes` keeps them and the issue type says nothing about
    them; the ISIN's third character is what puts them out."""
    # **The code must be plain 6-digit or `plain_codes` drops it first
    # and this proves nothing** — the first fixture used `Q50006`, which
    # is six characters and not six digits, so the class filter was never
    # reached. Real ETN ISIN: `KRG...`, and note it passes
    # `is_common_stock` (position 8 is `0`), so only the class catches it.
    etn = Delisting("500006", "코스닥", "an ETN", "KRG500000671")
    assert is_common_stock(etn.standard_code), "the issue type does not object"
    rows = [etn, Delisting("117930", "유가증권", "한진해운", "KR7117930008")]
    assert [d.code for d in common_stock(rows)] == ["117930"]


def test_an_unbranded_delisted_ETF_would_STILL_pass_and_that_is_disclosed():
    """**What is not closed.** An ETF shares `KR7` with common stock and
    carries no group code on the delisted side, so only its name betrays
    it. The evidence that this is empty rather than merely unhandled is
    quantified and indirect: **zero** of the 2,335 KR7 plain delisted
    names carry any ETF-shaped word, where **569 of 1,172 live ETFs do**.
    If delisted ETFs were present at that rate, P(seeing none) is under a
    percent for even ten of them — so the count is small, not provably
    zero."""
    # position 8 must be `0` or the issue-type filter drops it for the
    # wrong reason and the test proves nothing — the first fixture here
    # had a `1` there.
    fake_etf = Delisting("999000", "유가증권", "어떤성장액티브", "KR7999000005")
    assert common_stock([fake_etf]) == [fake_etf], (
        "an unbranded ETF is not detectable here, and pretending otherwise "
        "would be worse than saying so"
    )


def test_plain_codes_KEEPS_preferred_shares_and_spacs():
    """**Stated as a test because the opposite is the tempting reading.**
    A preferred share and a SPAC have plain 6-digit codes and are not
    common stock; the finder publishes no instrument-type field, so
    nothing here can tell them apart. A caller that treats `plain_codes`
    output as a common-stock universe is wrong, and this pins that."""
    out = plain_codes(
        [
            Delisting("002365", "유가증권", "SH에너지화학우", "KR7002361001"),
            Delisting("223040", "코스닥", "교보5호스팩", "KR7223040007"),
            Delisting("117930", "유가증권", "한진해운", "KR7117930008"),
        ]
    )
    assert len(out) == 3


# ========================================================= the store half


def test_a_snapshot_round_trips(monkeypatch):
    _install(monkeypatch, DELISTED_ROWS, LISTED_ROWS)
    conn = connect(":memory:")
    date, written, plain = snapshot(conn, "2026-09-20")
    assert (date, written, plain) == ("2026-09-20", len(DELISTED_ROWS), 4)
    rows = fetch_krx_delisted(conn, "2026-09-20")
    assert ("117930", "유가증권", "한진해운", "KR7117930008") in rows


def test_re_running_a_snapshot_is_a_no_op(monkeypatch):
    """A cron firing twice must not corrupt the record it exists to be."""
    _install(monkeypatch, DELISTED_ROWS, LISTED_ROWS)
    conn = connect(":memory:")
    snapshot(conn, "2026-09-20")
    _, written, _ = snapshot(conn, "2026-09-20")
    assert written == 0
    assert len(fetch_krx_delisted(conn, "2026-09-20")) == len(DELISTED_ROWS)


@pytest.mark.parametrize("bad", ["2026-02-31", "2026-99-99", "20260920", "", None])
def test_an_unreal_snapshot_date_is_rejected(bad):
    conn = connect(":memory:")
    with pytest.raises(ValueError, match="real YYYY-MM-DD"):
        upsert_krx_delisted(conn, bad, [("117930", "유가증권", "한진해운", "KR7117930008")])


def test_the_coverage_report_counts_plain_codes():
    conn = connect(":memory:")
    upsert_krx_delisted(
        conn,
        "2026-09-20",
        [("117930", "유가증권", "한진해운", "KR7117930008"),
         ("3686001G", "코스닥", "아이씨에이치 5R", "KR43686001G6")],
    )
    assert krx_delisted_snapshots(conn) == [("2026-09-20", 2, 1)]


def test_the_table_is_separate_from_krx_universe():
    """**Not a `delisted` flag on `krx_universe`.** The two answer
    different questions from different sources, and absence from a
    `krx_universe` snapshot means "not listed then", which is not the same
    claim as "delisted"."""
    conn = connect(":memory:")
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"krx_universe", "krx_delisted"} <= names
    cols = {r[1] for r in conn.execute("PRAGMA table_info(krx_universe)")}
    assert "delisted" not in cols


def test_an_empty_row_list_writes_nothing():
    conn = connect(":memory:")
    assert upsert_krx_delisted(conn, "2026-09-20", []) == 0


def test_a_failed_write_rolls_back():
    """A half-written snapshot is worse than none: the coverage report
    would show the date as captured."""

    class _Failing:
        def __init__(self, real):
            self._real = real
            self.rolled_back = False

        def executemany(self, *_a, **_k):
            raise sqlite3.OperationalError("disk gone")

        def rollback(self):
            self.rolled_back = True

        def __getattr__(self, name):
            return getattr(self._real, name)

    conn = _Failing(connect(":memory:"))
    with pytest.raises(sqlite3.OperationalError):
        upsert_krx_delisted(conn, "2026-09-20",
                            [("117930", "유가증권", "한진해운", "KR7117930008")])
    assert conn.rolled_back
