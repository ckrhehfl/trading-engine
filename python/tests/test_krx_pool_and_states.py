"""One pool definition, and three statuses that used to be one.

**Why the pool definition is consolidated** (audit D1). The four-filter
common-stock rule was implemented three times -- in
`store.fetch_krx_universe(common_stock_only=True)`, in
`krx_delisted.common_stock`, and inline in `krx_scan.candidates`. When the
SPAC rule landed the live side picked it up immediately and the delisted
side kept its 178 SPACs, so the pool looked filtered and was not. Half a
filter is worse than none.

**Why `is_live` had to become three-valued.** Measured 2026-09-24 against
the 2026-09-23 snapshots: `live + dead` produced 4,375 rows over 4,371
distinct codes, and all four duplicates carried `True` on one row and
`False` on the other. That flag decides whether a missing bar is an unknown
or an exit, so a contradictory value is not a cosmetic duplicate. All four
(카프로 `006380`, 원풍물산 `008290`, 코다코 `046070`, 코스나인 `082660`)
appear on both lists with the **same name, market and ISIN**.

This does not contradict `rd-w`'s "zero overlap": that compared KRX's
delisted finder against KRX's own `finder_stkisu`, while `krx_universe`
reads the **KIS master files**. The two listed sources disagree about four
names, and both measurements stand.

**Why `absent` and `failed` had to be split.** A first pass recorded 1,524
`absent` and 193 `failed:KisKlinesError`. Neither bucket can be acted on:
`rt_cd=0` with zero rows is the same answer for a dead name, an
out-of-range window and a code that never existed; and one exception type
covers a transient rejection (retry), a row-cap breach (never retry) and a
parse refusal (a venue change).
"""

from __future__ import annotations

import http.client
import sqlite3

import pytest

from data.kis_klines import KisKlinesError
from data.krx_instrument import has_plain_equity_code, is_common_stock_issue
from data.krx_scan import (
    RETRYABLE_FAILURES,
    SCAN_SCHEMA,
    Absence,
    Failure,
    Listing,
    candidates,
    classify_failure,
    retryable_failures,
)


# --------------------------------------------------- the one predicate


@pytest.mark.parametrize(
    "name, isin, expected, why",
    [
        ("삼성전자", "KR7005930003", True, "보통주"),
        ("삼성전자우", "KR7005931001", False, "우선주 -- issue type at position 8"),
        # **An ETF passes BOTH structural filters, and that is by design,
        # not a hole this predicate closes.** Issue type at position 8 is
        # `0` and instrument class at position 3 is `7`, which covers
        # 주식+ETF+리츠. What removes ETFs on the LIVE side is the `ST`
        # group code; on the delisted side nothing does, and that residue is
        # disclosed rather than closed -- zero of the 2,335 KR7 plain
        # delisted names carry an ETF-shaped word, against 569 of 1,172 live
        # ETFs that do. Asserted as True so a future reader does not "fix"
        # the predicate into claiming a separation it cannot make.
        ("KODEX 200", "KR7069500007", True, "an ETF is NOT separated by ISIN alone"),
        ("한화수성스팩", "KR7265920001", False, "a SPAC passes BOTH structural tests"),
        ("신한제9호스팩", "KR7405640005", False, "the numbered SPAC form"),
        ("맥쿼리인프라", "KR7088980008", True, "not matched by the anchored REIT rule"),
        ("이리츠코크렙", "KR7088260005", False, "코크렙 is an anchored REIT term"),
        ("다우기술", "KR7023590007", True, "우 as an ordinary syllable, not 우선주"),
        ("아스팩오일", "KR7109080007", True, "스팩 as an ordinary syllable"),
        ("미래에셋대우스팩 5호", "KR7439250003", False, "호수 after a SPACE"),
    ],
)
def test_the_four_filters_agree_with_every_measured_edge_case(name, isin, expected, why):
    assert is_common_stock_issue(name, isin) is expected, why


def test_an_unreadable_isin_fails_closed():
    """Dropping a name silently is itself a survivorship hazard, so where
    the *count* matters `krx_instrument.classify` is the tool. As a
    predicate, an ISIN this cannot read is not common stock."""
    for isin in (None, "", "XX", "US0378331005", "HK0000069689"):
        assert is_common_stock_issue("아무이름", isin) is False


def test_no_module_keeps_its_own_copy_of_the_rule():
    """**The consolidation, asserted rather than trusted.**

    Written expecting three copies. It found a **fourth** on its first run
    -- `krx_universe.py`'s own snapshot count -- which is the whole argument
    for having it: counting the copies by reading is exactly what missed one.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "data"
    for path in root.glob("*.py"):
        if path.name == "krx_instrument.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "not is_spac(" not in text or "is_common_stock_issue" in text, (
            f"{path.name} spells the SPAC clause out itself instead of calling "
            f"is_common_stock_issue -- that is how the fix reached one copy"
        )


def test_the_plain_code_floor_names_its_own_expiry():
    assert has_plain_equity_code("005930") is True
    # KOSDAQ's 2026 alphanumeric format. Correct to reject today (nothing
    # carrying one has delisted) and documented as the first thing this
    # will silently drop.
    assert has_plain_equity_code("0001A0") is False
    for bad in (None, "", "00593", "0059301", "00593O"):
        assert has_plain_equity_code(bad) is False


# ------------------------------------------------- the pool, deduplicated


def _universe(tmp_path, live, dead):
    """A store carrying one snapshot of each side."""
    conn = sqlite3.connect(tmp_path / "u.sqlite3")
    conn.executescript(
        "CREATE TABLE krx_universe (snapshot_date TEXT, code TEXT, market TEXT, "
        "name TEXT, group_code TEXT, standard_code TEXT);"
        "CREATE TABLE krx_delisted (snapshot_date TEXT, code TEXT, market TEXT, "
        "name TEXT, standard_code TEXT);"
    )
    conn.executemany(
        "INSERT INTO krx_universe VALUES ('2026-09-23', ?, 'KOSPI', ?, 'ST', ?)", live
    )
    conn.executemany(
        "INSERT INTO krx_delisted VALUES ('2026-09-23', ?, '유가증권', ?, ?)", dead
    )
    conn.commit()
    return conn


def test_a_code_on_both_sides_becomes_one_row_flagged_LEAVING(tmp_path):
    """**The measured defect.** Concatenation gave that code two rows with
    contradictory `is_live`, and `is_live` is what decides whether a missing
    bar is an unknown or an exit."""
    conn = _universe(
        tmp_path,
        live=[("006380", "카프로", "KR7006380000"), ("005930", "삼성전자", "KR7005930003")],
        dead=[("006380", "카프로", "KR7006380000"), ("117930", "한진해운", "KR7117930004")],
    )
    pool = candidates(conn)
    assert len(pool) == len({c for c, _, _ in pool}), "the pool still carries a duplicate"
    by_code = {c: st for c, _n, st in pool}
    assert by_code["006380"] is Listing.LEAVING
    assert by_code["005930"] is Listing.LIVE
    assert by_code["117930"] is Listing.DELISTED


def test_the_delisted_side_gets_the_same_filters_as_the_live_one(tmp_path):
    """The original defect: a SPAC on the delisted side survived while the
    identical rule removed it from the live side."""
    conn = _universe(
        tmp_path,
        live=[("005930", "삼성전자", "KR7005930003")],
        dead=[
            ("265920", "한화수성스팩", "KR7265920001"),
            ("005935", "삼성전자우", "KR7005931001"),
            ("117930", "한진해운", "KR7117930004"),
        ],
    )
    assert [c for c, _, _ in candidates(conn)] == ["005930", "117930"]


def test_a_pool_with_no_delisted_snapshot_is_refused(tmp_path):
    conn = _universe(tmp_path, live=[("005930", "삼성전자", "KR7005930003")], dead=[])
    with pytest.raises(Exception, match="survivorship-contaminated"):
        candidates(conn)


# ------------------------------------------------- absent, split three ways


def test_the_three_absences_are_distinct_and_unknown_is_the_default():
    assert len({a.value for a in Absence}) == 3
    assert Absence.UNKNOWN.value == "absent:unknown"
    # The first pass may only ever record UNKNOWN: distinguishing the other
    # two needs a separate over-wide probe plus a negative control.
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "data" / "krx_scan.py").read_text(
        encoding="utf-8"
    )
    body = src[src.index("def scan("):src.index("def resolve_absences(")]
    assert "Absence.UNKNOWN.value" in body
    for other in (Absence.NEVER_SERVED, Absence.OUTSIDE_WINDOW):
        assert other.name not in body, (
            f"the first pass records {other.name}, which it cannot know"
        )


# ------------------------------------- failures, at retry-decision grain


@pytest.mark.parametrize(
    "exc, expected",
    [
        (KisKlinesError("X returned 100 rows, at or over this endpoint's 100-row cap"),
         Failure.CAPPED),
        (KisKlinesError("KIS rejected the request for X: rt_cd=1 msg_cd=OPSQ0003"),
         Failure.REJECTED),
        (KisKlinesError("request failed after 4 attempts: URLError"), Failure.TRANSPORT),
        (KisKlinesError("output2 row 3 carries no stck_bsop_date for X"),
         Failure.MALFORMED),
        (KisKlinesError("output2 is str, not a list, for X"), Failure.MALFORMED),
        (TimeoutError("timed out"), Failure.TRANSPORT),
        (OSError("connection reset"), Failure.TRANSPORT),
        (http.client.IncompleteRead(b"partial"), Failure.TRANSPORT),
        (ValueError("something nobody anticipated"), Failure.OTHER),
    ],
)
def test_a_failure_is_classified_at_the_grain_a_retry_needs(exc, expected):
    assert classify_failure(exc) is expected


def test_the_retry_set_is_an_allowlist_and_excludes_a_capped_page():
    """A blocklist bets on having thought of every kind. `failed:capped`
    in particular must never be retried: it means the paging is wrong, so a
    retry reproduces it at the same cost."""
    assert Failure.CAPPED not in RETRYABLE_FAILURES
    assert Failure.MALFORMED not in RETRYABLE_FAILURES
    assert Failure.OTHER not in RETRYABLE_FAILURES
    assert RETRYABLE_FAILURES == {Failure.REJECTED, Failure.TRANSPORT}


def test_only_retryable_kinds_are_offered_to_a_second_pass(tmp_path):
    conn = sqlite3.connect(tmp_path / "s.sqlite3")
    conn.executescript(SCAN_SCHEMA)
    rows = [
        ("A", Failure.REJECTED.value),
        ("B", Failure.TRANSPORT.value),
        ("C", Failure.CAPPED.value),
        ("D", Failure.MALFORMED.value),
        ("E", f"{Failure.OTHER.value}:ValueError"),
        ("F", "done"),
        ("G", Absence.UNKNOWN.value),
        # A pass recorded before the split.
        ("H", "failed:KisKlinesError"),
    ]
    conn.executemany(
        "INSERT INTO scan_progress (code, bars, frozen, status, fetched_at) "
        "VALUES (?, 0, 0, ?, '2026-09-24T00:00:00+00:00')",
        rows,
    )
    conn.commit()
    assert retryable_failures(conn) == ["A", "B"], (
        "a non-retryable or legacy status leaked into the retry set"
    )


def test_a_legacy_bare_absent_still_counts_as_complete(tmp_path):
    """Resumption must not re-fetch 1,524 symbols recorded under the old
    single bucket -- that is a day of API calls to learn nothing."""
    from data.krx_scan import already_done

    conn = sqlite3.connect(tmp_path / "s.sqlite3")
    conn.executescript(SCAN_SCHEMA)
    conn.executemany(
        "INSERT INTO scan_progress (code, bars, frozen, status, fetched_at) "
        "VALUES (?, 0, 0, ?, '2026-09-24T00:00:00+00:00')",
        [
            ("A", "done"),
            ("B", "absent"),
            ("C", Absence.UNKNOWN.value),
            ("D", Absence.NEVER_SERVED.value),
            ("E", Failure.REJECTED.value),
        ],
    )
    conn.commit()
    assert already_done(conn) == {"A", "B", "C", "D"}, (
        "a failure must NOT count as done -- retrying it is what a second "
        "pass is for"
    )


def test_the_scan_RECORDS_the_classified_kind_not_the_exception_type(monkeypatch, tmp_path):
    """**Found by a mutation, not by reading.** Replacing
    `classify_failure(exc)` with a constant `Failure.OTHER` left the whole
    suite green: the existing scan tests raise a bare `RuntimeError`, which
    classifies as `OTHER` anyway, and the retry-set test seeds the database
    directly instead of going through `scan`. So nothing connected the
    classifier to the thing that writes its answer down -- which is the only
    reason the classifier exists.
    """
    from data.krx_scan import SCAN_SCHEMA as SCHEMA, scan

    conn = sqlite3.connect(tmp_path / "s.sqlite3")
    conn.executescript(SCHEMA)
    conn.commit()

    class _S:
        def headers(self, tr):  # noqa: ARG002
            return {}

    def rejected(url, headers):  # noqa: ARG001
        raise KisKlinesError(
            "KIS rejected the request for 005930 20190101..20190430: "
            "rt_cd=1 msg_cd=OPSQ0003"
        )

    monkeypatch.setattr("data.krx_scan._get_with_retry", rejected)
    monkeypatch.setattr("data.krx_scan.time.sleep", lambda *_: None)

    scan(_S(), conn, [("005930", "삼성전자", Listing.LIVE)])
    status = conn.execute(
        "SELECT status FROM scan_progress WHERE code = '005930'"
    ).fetchone()[0]
    assert status == Failure.REJECTED.value, (
        f"the scan recorded {status!r} instead of the classified kind, so a "
        f"second pass cannot tell a transient rejection from a cap breach"
    )


def test_an_unclassifiable_failure_still_records_its_exception_type(monkeypatch, tmp_path):
    """`OTHER` is not retried, so it has to stay diagnosable by hand."""
    from data.krx_scan import SCAN_SCHEMA as SCHEMA, scan

    conn = sqlite3.connect(tmp_path / "s.sqlite3")
    conn.executescript(SCHEMA)
    conn.commit()

    class _S:
        def headers(self, tr):  # noqa: ARG002
            return {}

    def odd(url, headers):  # noqa: ARG001
        raise ZeroDivisionError("nothing anticipated this")

    monkeypatch.setattr("data.krx_scan._get_with_retry", odd)
    monkeypatch.setattr("data.krx_scan.time.sleep", lambda *_: None)

    scan(_S(), conn, [("005930", "삼성전자", Listing.LIVE)])
    status = conn.execute(
        "SELECT status FROM scan_progress WHERE code = '005930'"
    ).fetchone()[0]
    assert status == f"{Failure.OTHER.value}:ZeroDivisionError"
