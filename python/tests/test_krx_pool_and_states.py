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


# ------------- the classifier against the message its callers really raise


def test_the_cap_refusal_this_MODULE_raises_classifies_as_CAPPED(monkeypatch):
    """**Obtained by triggering the refusal, not by quoting it.**

    Every other case in this file hands `classify_failure` a string I wrote,
    which confirms my own wording rather than the caller's -- and that let a
    real mismatch through: `krx_scan._page` raised "... rows at the 100-row
    cap" while the classifier matched only `kis_klines`'s "at or over this
    endpoint", so `Failure.CAPPED` was never recorded from the actual scan
    path. Reported on review of this PR.

    The direction was safe (it filed as `OTHER`, which is not retried) and
    the defect was still real: the PR's whole point is telling a cap breach
    apart from a transient, and the scan path could not.
    """
    from data import krx_scan as S

    class _S:
        def headers(self, tr):  # noqa: ARG002
            return {}

    monkeypatch.setattr(
        S,
        "_get_with_retry",
        lambda url, headers: {
            "rt_cd": "0",
            "output2": [{"stck_bsop_date": f"2019{i:04d}"} for i in range(1, 101)],
        },
    )
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)

    with pytest.raises(Exception) as exc:
        S._page(_S(), "005930", "20190102", "20190430")
    assert classify_failure(exc.value) is Failure.CAPPED, (
        f"the refusal this module actually raises classifies as "
        f"{classify_failure(exc.value).value}: {exc.value}"
    )


def test_the_rejection_refusal_this_module_raises_classifies_as_REJECTED(monkeypatch):
    from data import krx_scan as S

    class _S:
        def headers(self, tr):  # noqa: ARG002
            return {}

    monkeypatch.setattr(
        S, "_get_with_retry",
        lambda url, headers: {"rt_cd": "1", "msg_cd": "OPSQ0003"},
    )
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)
    with pytest.raises(Exception) as exc:
        S._page(_S(), "005930", "20190102", "20190430")
    assert classify_failure(exc.value) is Failure.REJECTED


# ------------------------------------------- the wide probe and its verdicts


#: Not the positive control's code, deliberately. Using `005930` made the
#: symbol under test *be* the control, so a fake keyed on the code answered
#: the control's payload for both and the branch under test never ran.
SUBJECT = "000660"


def _probe_env(monkeypatch, payload, *, control=None):
    """Answer `_wide_probe` with `payload`, and the positive control
    separately.

    The control answers with a real bar by default, so a test can exercise the
    verdict branches without the control's own refusal firing first. A test
    that wants the control to fail passes `control=`.
    """
    from data import krx_scan as S

    if control is None:
        control = {"rt_cd": "0", "output2": [{"stck_bsop_date": "19910828"}]}

    class _S:
        def headers(self, tr):  # noqa: ARG002
            return {}

    def answer(url, headers):  # noqa: ARG001
        from urllib.parse import parse_qs, urlparse

        code = parse_qs(urlparse(url).query)["FID_INPUT_ISCD"][0]
        return control if code == S.WIDE_PROBE_POSITIVE_CONTROL else payload

    monkeypatch.setattr(S, "_get_with_retry", answer)
    monkeypatch.setattr(S, "verify_negative_controls", lambda s: None)
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)
    return _S()


def _seeded(tmp_path, code=SUBJECT):
    tmp_path = __import__("pathlib").Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(tmp_path / "s.sqlite3")
    conn.executescript(SCAN_SCHEMA)
    conn.execute(
        "INSERT INTO scan_progress (code, bars, frozen, status, fetched_at) "
        "VALUES (?, 0, 0, ?, '2026-09-24T00:00:00+00:00')",
        (code, Absence.UNKNOWN.value),
    )
    conn.commit()
    return conn


def test_a_dateless_placeholder_does_not_make_a_code_look_served(
    monkeypatch, tmp_path
):
    """**Reported on review.** The probe checked only `isinstance(row, dict)`,
    so a placeholder would resolve a genuinely never-served code to
    `OUTSIDE_WINDOW` -- which asserts the name existed. Every other reader
    here requires the date; this one skipped it."""
    from data.krx_scan import resolve_absences

    session = _probe_env(monkeypatch, {"rt_cd": "0", "output2": [{}, {"a": 1}]})
    conn = _seeded(tmp_path)
    counts = resolve_absences(session, conn)
    assert counts["failed"] == 1, "a dateless row was accepted as a bar"
    assert counts[Absence.OUTSIDE_WINDOW.value] == 0


def test_no_bars_at_all_resolves_to_NEVER_SERVED(monkeypatch, tmp_path):
    from data.krx_scan import resolve_absences

    session = _probe_env(monkeypatch, {"rt_cd": "0", "output2": []})
    conn = _seeded(tmp_path)
    counts = resolve_absences(session, conn)
    assert counts[Absence.NEVER_SERVED.value] == 1
    status = conn.execute("SELECT status FROM scan_progress").fetchone()[0]
    assert status == Absence.NEVER_SERVED.value


def test_bars_entirely_before_the_panel_resolve_to_OUTSIDE_WINDOW(
    monkeypatch, tmp_path
):
    from data.krx_scan import resolve_absences

    session = _probe_env(
        monkeypatch,
        {"rt_cd": "0", "output2": [{"stck_bsop_date": d} for d in
                                   ("19991201", "19991202")]},
    )
    conn = _seeded(tmp_path)
    counts = resolve_absences(session, conn)
    assert counts[Absence.OUTSIDE_WINDOW.value] == 1


def test_bars_INSIDE_the_panel_are_a_contradiction_and_stay_UNKNOWN(
    monkeypatch, tmp_path
):
    """**Reported on review, and the more important half.** KIS pricing this
    code inside the very panel the pass found empty is not "outside the
    window" -- a session that should have been fetched was not. Filing that
    as an expected absence is the survivorship direction, so it stays
    UNKNOWN and is counted separately to stay visible."""
    from data.krx_scan import PANEL_START, resolve_absences

    session = _probe_env(
        monkeypatch,
        {"rt_cd": "0", "output2": [{"stck_bsop_date": "20000103"},
                                   {"stck_bsop_date": PANEL_START}]},
    )
    conn = _seeded(tmp_path)
    counts = resolve_absences(session, conn)
    assert counts["contradicted"] == 1
    assert counts[Absence.OUTSIDE_WINDOW.value] == 0
    status = conn.execute("SELECT status FROM scan_progress").fetchone()[0]
    assert status == Absence.UNKNOWN.value, (
        "a contradiction was resolved into an expected absence"
    )


def test_an_unresolvable_negative_control_stops_the_whole_pass(monkeypatch, tmp_path):
    """Without the controls a zero-row answer cannot be read at all, so the
    pass must refuse rather than resolve every symbol on a guess."""
    from data import krx_scan as S

    class _S:
        def headers(self, tr):  # noqa: ARG002
            return {}

    def boom(session):
        raise S.KrxScanError("THE NEGATIVE CONTROL could not be asked")

    def must_not_be_called(url, headers):  # noqa: ARG001
        raise AssertionError(
            "the resolver made a request before the negative control had run"
        )

    monkeypatch.setattr(S, "verify_negative_controls", boom)
    monkeypatch.setattr(S, "_get_with_retry", must_not_be_called)
    conn = _seeded(tmp_path)
    # **The match string is deliberately specific**, and the loose `"control"`
    # it replaces is why this test was INERT: deleting the negative-control
    # call let the POSITIVE control run instead, whose own refusal message also
    # contains "control", so the test passed for the wrong reason. The stubbed
    # transport is the second half -- it makes any request at all a hard
    # failure rather than something a message can imitate.
    with pytest.raises(S.KrxScanError, match="THE NEGATIVE CONTROL"):
        S.resolve_absences(_S(), conn)
    status = conn.execute("SELECT status FROM scan_progress").fetchone()[0]
    assert status == Absence.UNKNOWN.value, "a symbol was resolved anyway"


def test_a_live_only_code_with_an_EMPTY_name_does_not_kill_the_pool(tmp_path):
    """**Reported on review.** `live.get(code) or dead[code]` branched on
    truthiness rather than membership, so a live-only code whose name is the
    empty string fell through to `dead[code]` and raised `KeyError` -- which
    takes the whole pool build down and stops the scan from starting at all.
    `parse_master` strips names, so it does not rule an empty one out."""
    conn = _universe(
        tmp_path,
        live=[("005930", "", "KR7005930003"), ("000660", "SK하이닉스", "KR7000660001")],
        dead=[("117930", "한진해운", "KR7117930004")],
    )
    pool = candidates(conn)
    assert {c for c, _, _ in pool} == {"005930", "000660", "117930"}
    assert dict((c, n) for c, n, _ in pool)["005930"] == ""


def test_a_LEAVING_code_falls_back_to_the_delisted_name_when_live_is_blank(tmp_path):
    """The one place a fallback is legitimate: the code really is on both
    lists, so the other side's name is a better answer than nothing."""
    conn = _universe(
        tmp_path,
        live=[("006380", "", "KR7006380000")],
        dead=[("006380", "카프로", "KR7006380000")],
    )
    pool = candidates(conn)
    assert pool == [("006380", "카프로", Listing.LEAVING)]


def test_the_coverage_report_groups_failure_kinds_without_truncating_them(tmp_path):
    """**Reported on review.** `substr(status, 1, 14)` printed
    `failed:rejecte`, cut `failed:transport` and `failed:malformed` short, and
    split `failed:other:<Type>` into one group per exception type's first
    letter. Deciding whether to run a second pass means reading this report.
    """
    import io
    from contextlib import redirect_stdout

    from data.krx_scan import coverage

    conn = sqlite3.connect(tmp_path / "s.sqlite3")
    conn.executescript(SCAN_SCHEMA)
    conn.executemany(
        "INSERT INTO scan_progress (code, bars, frozen, status, fetched_at) "
        "VALUES (?, 0, 0, ?, '2026-09-24T00:00:00+00:00')",
        [
            ("A", Failure.REJECTED.value),
            ("B", Failure.TRANSPORT.value),
            ("C", Failure.MALFORMED.value),
            ("D", Failure.CAPPED.value),
            ("E", f"{Failure.OTHER.value}:ZeroDivisionError"),
            ("F", f"{Failure.OTHER.value}:KeyError"),
            ("G", Absence.UNKNOWN.value),
            ("H", Absence.NEVER_SERVED.value),
        ],
    )
    conn.commit()

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        coverage(conn)
    printed = buffer.getvalue()

    for kind in (
        "failed:rejected", "failed:transport", "failed:malformed",
        "failed:capped", "absent:unknown", "absent:never_served",
    ):
        assert kind in printed, f"{kind} is not reported under its own name"
    # The two `other` rows collapse into one group rather than splitting by
    # exception type, and neither exception name reaches the report.
    assert "failed:other" in printed
    assert "ZeroDivisionError" not in printed and "KeyError" not in printed


def test_bars_only_AFTER_the_panel_resolve_to_OUTSIDE_WINDOW(monkeypatch, tmp_path):
    """**Reported on review, and the unsafe direction.** `_wide_probe` used to
    end at `PANEL_END`, so a name listed after it returned zero rows exactly as
    it had in the first pass and was recorded `NEVER_SERVED` -- asserting KIS
    does not price a name it prices perfectly well. That is the pool losing a
    real name, which is the survivorship bias this universe exists to remove.
    """
    import datetime as dt

    from data.krx_scan import KST, PANEL_END, resolve_absences

    # **The day after `PANEL_END`, not a year after it.** An earlier version
    # used 2027, which `_wide_probe` -- ending at today -- would never actually
    # request, so the test proved `OUTSIDE_WINDOW` from a response the real
    # code could not receive. `_probe_env` answers regardless of the window it
    # was asked for, which is exactly the "right verdict for the wrong reason"
    # this file's own probe-range test was added to guard against, and it
    # caught me here one commit later. Reported on review.
    later = (
        dt.datetime.strptime(PANEL_END, "%Y%m%d").date() + dt.timedelta(days=1)
    ).strftime("%Y%m%d")
    assert later <= dt.datetime.now(KST).strftime("%Y%m%d"), (
        f"{later} is past today, so _wide_probe would not request it and this "
        f"test would be validating an unreachable response again"
    )
    session = _probe_env(
        monkeypatch,
        {"rt_cd": "0", "output2": [{"stck_bsop_date": later}]},
    )
    conn = _seeded(tmp_path)
    counts = resolve_absences(session, conn)
    assert counts[Absence.OUTSIDE_WINDOW.value] == 1
    assert counts[Absence.NEVER_SERVED.value] == 0
    assert counts["contradicted"] == 0


def test_the_wide_probe_asks_past_the_panel_end(monkeypatch):
    """Asserted on the request itself, not only on the verdict: the verdict
    can be reached for the wrong reason if a fake answers regardless of the
    window it was asked for."""
    import datetime as dt

    from data import krx_scan as S

    seen = {}

    def capture(url, headers):  # noqa: ARG001
        from urllib.parse import parse_qs, urlparse

        seen.update({k: v[0] for k, v in parse_qs(urlparse(url).query).items()})
        return {"rt_cd": "0", "output2": []}

    monkeypatch.setattr(S, "_get_with_retry", capture)

    class _S:
        def headers(self, tr):  # noqa: ARG002
            return {}

    S._wide_probe(_S(), "005930")
    assert seen["FID_INPUT_DATE_2"] > S.PANEL_END, (
        f"the probe asked only to {seen['FID_INPUT_DATE_2']}, so a name listed "
        f"after {S.PANEL_END} cannot be distinguished from one KIS never prices"
    )
    assert seen["FID_INPUT_DATE_2"] == dt.datetime.now(S.KST).strftime("%Y%m%d")


def test_BOTH_passes_record_an_unclassifiable_failure_the_same_way(
    monkeypatch, tmp_path
):
    """**Reported on review.** `scan()` appended the exception type and
    `resolve_absences()` did not, so a probe-path `OTHER` lost the only clue it
    had -- and `OTHER` is exactly the kind that is never retried, so manual
    diagnosis is all it gets."""
    from data import krx_scan as S

    class _S:
        def headers(self, tr):  # noqa: ARG002
            return {}

    def odd(url, headers):  # noqa: ARG001
        from urllib.parse import parse_qs, urlparse

        code = parse_qs(urlparse(url).query)["FID_INPUT_ISCD"][0]
        # The control has to answer, or it refuses the pass before the
        # failure under test can be recorded -- which is the control working,
        # not a problem with it.
        if code == S.WIDE_PROBE_POSITIVE_CONTROL:
            return {"rt_cd": "0", "output2": [{"stck_bsop_date": "19910828"}]}
        raise ZeroDivisionError("nothing anticipated this")

    monkeypatch.setattr(S, "_get_with_retry", odd)
    monkeypatch.setattr(S, "verify_negative_controls", lambda s: None)
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)

    scan_conn = sqlite3.connect(tmp_path / "a.sqlite3")
    scan_conn.executescript(SCAN_SCHEMA)
    scan_conn.commit()
    S.scan(_S(), scan_conn, [(SUBJECT, "SK하이닉스", Listing.LIVE)])

    probe_conn = _seeded(tmp_path / "b")
    S.resolve_absences(_S(), probe_conn)

    expected = f"{Failure.OTHER.value}:ZeroDivisionError"
    for label, conn in (("scan", scan_conn), ("resolve_absences", probe_conn)):
        got = conn.execute("SELECT status FROM scan_progress").fetchone()[0]
        assert got == expected, f"{label} recorded {got!r}, not {expected!r}"


# ------------------- the wide probe needs a POSITIVE control, not only a negative


def test_the_resolver_refuses_when_the_wide_probe_returns_nothing_for_005930(
    monkeypatch, tmp_path
):
    """**Reported on review, and it is the survivorship direction.**

    `verify_negative_controls` proves a nonsense code answers empty on a
    NARROW `_page` request. That is a different request from `_wide_probe`'s
    `19900101`..today, and it cannot prove that an empty wide answer means
    "not served" -- an endpoint that answered empty for *everything* passes a
    negative control by construction, because empty is what it expects. Every
    `absent:unknown` would then be recorded `NEVER_SERVED`, removing real
    names from the pool as "KIS does not price this".
    """
    from data import krx_scan as S

    # An endpoint answering empty for EVERY code, the control included --
    # which is precisely the state a negative control cannot detect.
    empty = {"rt_cd": "0", "output2": []}
    session = _probe_env(monkeypatch, empty, control=empty)
    # `_probe_env` stubs the negative controls out, which is the whole point:
    # they pass here and must not be enough on their own.
    conn = _seeded(tmp_path)
    with pytest.raises(S.KrxScanError, match="no bars for 005930"):
        S.resolve_absences(session, conn)
    status = conn.execute("SELECT status FROM scan_progress").fetchone()[0]
    assert status == Absence.UNKNOWN.value, (
        "a symbol was resolved to NEVER_SERVED on an answer the probe gives "
        "for everything"
    )


def test_a_positive_control_that_cannot_be_asked_also_refuses(monkeypatch, tmp_path):
    from data import krx_scan as S

    class _S:
        def headers(self, tr):  # noqa: ARG002
            return {}

    def boom(url, headers):  # noqa: ARG001
        raise TimeoutError("timed out")

    monkeypatch.setattr(S, "_get_with_retry", boom)
    monkeypatch.setattr(S, "verify_negative_controls", lambda s: None)
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)
    with pytest.raises(S.KrxScanError, match="could not be asked"):
        S.resolve_absences(_S(), _seeded(tmp_path))


def test_the_positive_control_passing_lets_the_pass_proceed(monkeypatch, tmp_path):
    """The control must not be a refusal that always fires -- a guard that
    always blocks teaches nothing either."""
    from data import krx_scan as S

    calls = {"n": 0}

    def answer(url, headers):  # noqa: ARG001
        from urllib.parse import parse_qs, urlparse

        code = parse_qs(urlparse(url).query)["FID_INPUT_ISCD"][0]
        calls["n"] += 1
        if code == S.WIDE_PROBE_POSITIVE_CONTROL:
            return {"rt_cd": "0", "output2": [{"stck_bsop_date": "19910828"}]}
        return {"rt_cd": "0", "output2": []}

    monkeypatch.setattr(S, "_get_with_retry", answer)
    monkeypatch.setattr(S, "verify_negative_controls", lambda s: None)
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)

    class _S:
        def headers(self, tr):  # noqa: ARG002
            return {}

    counts = S.resolve_absences(_S(), _seeded(tmp_path))
    assert counts[Absence.NEVER_SERVED.value] == 1
    assert calls["n"] == 2, "the control and the symbol, one call each"
    assert S.WIDE_PROBE_POSITIVE_CONTROL != SUBJECT, (
        "the subject must not be the control, or the fake answers both the same"
    )


# ------------------------------------------- a truncated probe is undecidable


def test_a_capped_probe_entirely_after_the_panel_stays_UNKNOWN(monkeypatch, tmp_path):
    """**Reported on review; latent, not live.** `_wide_probe` keeps only the
    newest rows. Coming back FULL with every date after the panel means older
    rows were dropped, and some of them may be in-panel bars -- so this is not
    "outside the window", it is "we cannot see". Recording `OUTSIDE_WINDOW`
    would file a real hole as an expected absence and `already_done` would
    call the code complete.

    Unreachable today, since `PANEL_END` is a handful of sessions back.
    Reachable once a second pass runs ~100 sessions after it.
    """
    import datetime as dt

    from data import krx_scan as S
    from data.kis_klines import EQUITY_ROWS_PER_CALL_CAP

    start = dt.datetime.strptime(S.PANEL_END, "%Y%m%d").date() + dt.timedelta(days=1)
    rows = [
        {"stck_bsop_date": (start + dt.timedelta(days=i)).strftime("%Y%m%d")}
        for i in range(EQUITY_ROWS_PER_CALL_CAP)
    ]
    assert len(rows) == EQUITY_ROWS_PER_CALL_CAP

    def answer(url, headers):  # noqa: ARG001
        from urllib.parse import parse_qs, urlparse

        code = parse_qs(urlparse(url).query)["FID_INPUT_ISCD"][0]
        if code == S.WIDE_PROBE_POSITIVE_CONTROL:
            return {"rt_cd": "0", "output2": [{"stck_bsop_date": "19910828"}]}
        return {"rt_cd": "0", "output2": rows}

    monkeypatch.setattr(S, "_get_with_retry", answer)
    monkeypatch.setattr(S, "verify_negative_controls", lambda s: None)
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)

    class _S:
        def headers(self, tr):  # noqa: ARG002
            return {}

    conn = _seeded(tmp_path)
    counts = S.resolve_absences(_S(), conn)
    assert counts["truncated"] == 1
    assert counts[Absence.OUTSIDE_WINDOW.value] == 0
    assert conn.execute(
        "SELECT status FROM scan_progress"
    ).fetchone()[0] == Absence.UNKNOWN.value


def test_an_UNDER_cap_page_after_the_panel_is_still_OUTSIDE_WINDOW(
    monkeypatch, tmp_path
):
    """The boundary in the other direction, so the truncation branch cannot
    swallow the ordinary later-listing case it sits next to."""
    import datetime as dt

    from data import krx_scan as S
    from data.kis_klines import EQUITY_ROWS_PER_CALL_CAP

    start = dt.datetime.strptime(S.PANEL_END, "%Y%m%d").date() + dt.timedelta(days=1)
    rows = [
        {"stck_bsop_date": (start + dt.timedelta(days=i)).strftime("%Y%m%d")}
        for i in range(EQUITY_ROWS_PER_CALL_CAP - 1)
    ]

    def answer(url, headers):  # noqa: ARG001
        from urllib.parse import parse_qs, urlparse

        code = parse_qs(urlparse(url).query)["FID_INPUT_ISCD"][0]
        if code == S.WIDE_PROBE_POSITIVE_CONTROL:
            return {"rt_cd": "0", "output2": [{"stck_bsop_date": "19910828"}]}
        return {"rt_cd": "0", "output2": rows}

    monkeypatch.setattr(S, "_get_with_retry", answer)
    monkeypatch.setattr(S, "verify_negative_controls", lambda s: None)
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)

    class _S:
        def headers(self, tr):  # noqa: ARG002
            return {}

    counts = S.resolve_absences(_S(), _seeded(tmp_path))
    assert counts[Absence.OUTSIDE_WINDOW.value] == 1
    assert counts["truncated"] == 0


# ------------- a control that cannot be ASKED is not a control that FAILED


def _control_env(monkeypatch, answers):
    """Answer `_page` from a per-call script: an exception is raised, a list
    is returned."""
    from data import krx_scan as S

    calls = {"n": 0}

    def fake_page(session, code, start, end):  # noqa: ARG001
        i = calls["n"]
        calls["n"] += 1
        outcome = answers[min(i, len(answers) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(S, "_page", fake_page)
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)
    return calls


def test_a_control_that_fails_then_ANSWERS_lets_the_pass_proceed(monkeypatch):
    """**The fix, and it cost two aborted multi-hour launches.**

    Measured 2026-09-24, 25 consecutive calls to `999999`: 18 returned
    `rt_cd=0` with zero rows, 5 raised `HTTPError 500`, 2 timed out, and
    **none returned rows**. So the control's premise was never contradicted;
    what failed was reaching the endpoint. Refusing a scan on that is refusing
    on evidence the run does not have.
    """
    from data import krx_scan as S

    calls = _control_env(
        monkeypatch,
        [TimeoutError("timed out"), OSError("500"), []],
    )
    S.verify_negative_controls(object())
    assert calls["n"] >= 3, "the failures were not retried"


def test_a_control_that_RETURNS_BARS_is_never_retried(monkeypatch):
    """The other half of the asymmetry, and the half that keeps this a fix
    rather than a weakening. A control carrying bars is the real signal the
    check exists to catch; retrying it would be sampling until the answer is
    convenient."""
    from data import krx_scan as S

    calls = _control_env(
        monkeypatch,
        [[{"stck_bsop_date": "20190102"}], []],
    )
    with pytest.raises(S.KrxScanError, match="returned 1 bars"):
        S.verify_negative_controls(object())
    assert calls["n"] == 1, "an ANSWER was retried"


def test_a_rt_cd_REJECTION_of_a_control_is_not_retried(monkeypatch):
    """**Reported on review, and it corrects this fix's own framing.** A
    `rt_cd` rejection is a *completed answer* from the venue, not a failure to
    reach it, so the asymmetry this function is built on forbids retrying it.
    The first version retried any exception at all.

    The consequence is stated rather than papered over: **the `rt_cd=1` abort
    that prompted the fix is therefore not covered by it.** Covering it would
    mean allowlisting the `msg_cd` behind it the way `fetch_daily_page` does
    for the measured-transient `OPSQ0003`, and 25 probe calls never reproduced
    a `rt_cd=1` — so there is no code to allowlist, and adding one on a guess
    is the mistake already recorded for Binance's HTTP 418.
    """
    from data import krx_scan as S

    calls = _control_env(
        monkeypatch,
        [KisKlinesError("999999 20190102..20190430: rt_cd=1"), []],
    )
    with pytest.raises(S.KrxScanError, match="not retried after 1"):
        S.verify_negative_controls(object())
    assert calls["n"] == 1, "a venue answer was retried"


def test_a_MALFORMED_control_response_is_not_retried(monkeypatch):
    """Same reasoning: a response whose shape is wrong is an answer."""
    from data import krx_scan as S

    calls = _control_env(
        monkeypatch,
        [KisKlinesError("output2 row 0 carries no stck_bsop_date for X"), []],
    )
    with pytest.raises(S.KrxScanError, match="not retried after 1"):
        S.verify_negative_controls(object())
    assert calls["n"] == 1


def test_a_control_that_can_NEVER_be_asked_still_refuses(monkeypatch):
    """The bound is real. A consistently unreachable control leaves every
    `absent` unreadable exactly as before, so the pass must still stop."""
    from data import krx_scan as S

    calls = _control_env(monkeypatch, [TimeoutError("always")])
    with pytest.raises(S.KrxScanError, match="retried 4 times"):
        S.verify_negative_controls(object())
    assert calls["n"] == S._CONTROL_ATTEMPTS, (
        f"asked {calls['n']} times, not {S._CONTROL_ATTEMPTS}"
    )


def test_the_positive_control_retries_a_failure_to_ask_too(monkeypatch):
    from data import krx_scan as S

    seq = [OSError("500"), OSError("500"), ["19910828"]]
    calls = {"n": 0}

    def fake_probe(session, code):  # noqa: ARG001
        i = calls["n"]
        calls["n"] += 1
        out = seq[min(i, len(seq) - 1)]
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(S, "_wide_probe", fake_probe)
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)
    S.verify_wide_probe_positive_control(object())
    assert calls["n"] == 3


def test_the_positive_control_gives_up_after_exactly_FOUR_transport_failures(
    monkeypatch,
):
    """**Reported on review.** Only the fail-then-succeed path was covered, so
    neither an infinite retry nor an early give-up would have been caught."""
    from data import krx_scan as S

    calls = {"n": 0}

    def always_fails(session, code):  # noqa: ARG001
        calls["n"] += 1
        raise TimeoutError("timed out")

    monkeypatch.setattr(S, "_wide_probe", always_fails)
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)
    with pytest.raises(S.KrxScanError, match="could not be asked"):
        S.verify_wide_probe_positive_control(object())
    assert calls["n"] == S._CONTROL_ATTEMPTS == 4, (
        f"asked {calls['n']} times, not {S._CONTROL_ATTEMPTS}"
    )


def test_a_rt_cd_rejection_of_the_POSITIVE_control_is_not_retried(monkeypatch):
    from data import krx_scan as S

    calls = {"n": 0}

    def rejected(session, code):  # noqa: ARG001
        calls["n"] += 1
        raise KisKlinesError("KIS rejected the request for 005930: rt_cd=1")

    monkeypatch.setattr(S, "_wide_probe", rejected)
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)
    with pytest.raises(S.KrxScanError, match="could not be asked"):
        S.verify_wide_probe_positive_control(object())
    assert calls["n"] == 1, "a venue answer was retried"


def test_an_EMPTY_positive_control_answer_is_not_retried(monkeypatch):
    """An empty answer from 삼성전자 is the signal, not a transport problem."""
    from data import krx_scan as S

    calls = {"n": 0}

    def fake_probe(session, code):  # noqa: ARG001
        calls["n"] += 1
        return []

    monkeypatch.setattr(S, "_wide_probe", fake_probe)
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)
    with pytest.raises(S.KrxScanError, match="no bars for 005930"):
        S.verify_wide_probe_positive_control(object())
    assert calls["n"] == 1, "an empty answer was retried"


# ----------------------------------- the count, against the pool not a total


def test_the_todo_count_is_computed_against_the_POOL(monkeypatch, tmp_path, capsys):
    """**It printed "-9 to fetch" on a run with 257 failures to retry.**
    `len(pool) - len(done)` is the wrong arithmetic once `done` can hold codes
    that are no longer candidates — the progress table still carries rows from
    the pre-deduplication pool of 4,643. A negative count is obvious; a wrong
    positive one would not have been."""
    from data import krx_scan as S

    conn = sqlite3.connect(tmp_path / "s.sqlite3")
    conn.executescript(SCAN_SCHEMA)
    conn.executemany(
        "INSERT INTO scan_progress (code, bars, frozen, status, fetched_at) "
        "VALUES (?, 0, 0, ?, '2026-09-24T00:00:00+00:00')",
        [("A", "done"), ("B", "done"), ("GONE1", "done"), ("GONE2", "absent:unknown"),
         ("C", Failure.REJECTED.value)],
    )
    conn.commit()

    pool = [("A", "a", Listing.LIVE), ("B", "b", Listing.LIVE), ("C", "c", Listing.LIVE)]
    monkeypatch.setattr(S, "candidates", lambda c: pool)
    monkeypatch.setattr(S, "connect", lambda p: conn)
    monkeypatch.setattr(S, "KisSession", lambda *a, **k: object())
    monkeypatch.setattr(S, "verify_negative_controls", lambda s: None)
    monkeypatch.setattr(S, "scan", lambda *a, **k: {"failed": 0})
    monkeypatch.setattr(S, "coverage", lambda c: None)
    monkeypatch.setattr(S, "in_continuous_session", lambda *a: False)
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")

    S.main(["--scan", "--db-path", str(tmp_path / "s.sqlite3")])
    printed = capsys.readouterr().out
    assert "3 candidates, 2 already complete, 1 to fetch" in printed, printed
    assert "2 recorded codes are not in this candidate selection" in printed, printed


def test_the_outside_count_does_not_call_a_LIMITED_code_delisted(
    monkeypatch, tmp_path, capsys
):
    """**Reported on review.** `--limit` truncates the pool, so a completed
    code can be a perfectly current candidate that simply falls outside this
    run's slice. Calling it "no longer a candidate" is a wrong statement rather
    than a vague one."""
    from data import krx_scan as S

    conn = sqlite3.connect(tmp_path / "s.sqlite3")
    conn.executescript(SCAN_SCHEMA)
    conn.executemany(
        "INSERT INTO scan_progress (code, bars, frozen, status, fetched_at) "
        "VALUES (?, 0, 0, 'done', '2026-09-24T00:00:00+00:00')",
        [("A",), ("B",)],
    )
    conn.commit()

    pool = [("A", "a", Listing.LIVE), ("B", "b", Listing.LIVE), ("C", "c", Listing.LIVE)]
    monkeypatch.setattr(S, "candidates", lambda c: pool)
    monkeypatch.setattr(S, "connect", lambda p: conn)
    monkeypatch.setattr(S, "KisSession", lambda *a, **k: object())
    monkeypatch.setattr(S, "verify_negative_controls", lambda s: None)
    monkeypatch.setattr(S, "scan", lambda *a, **k: {"failed": 0})
    monkeypatch.setattr(S, "coverage", lambda c: None)
    monkeypatch.setattr(S, "in_continuous_session", lambda *a: False)
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")

    # --limit 1 slices the pool to ["A"], so B is complete and outside the
    # selection while remaining a live candidate.
    S.main(["--scan", "--limit", "1", "--db-path", str(tmp_path / "s.sqlite3")])
    printed = capsys.readouterr().out
    assert "no longer" not in printed, printed
    assert "not in this candidate selection" in printed, printed


def test_a_control_refusal_carries_the_REAL_attempt_count_and_the_cause(monkeypatch):
    """**Reported on review, and it was working against the plan beside it.**
    The positive control's refusal said "in 4 attempts" even when a `rt_cd`
    rejection stopped it after one, and dropped the underlying message —
    losing the `rt_cd`/`msg_cd` that is the exact diagnostic `_ask_control`'s
    docstring says to capture if the abort recurs.

    Both controls now share one message, so they cannot drift apart.
    """
    from data import krx_scan as S

    calls = {"n": 0}

    def rejected(session, code):  # noqa: ARG001
        calls["n"] += 1
        raise KisKlinesError("KIS rejected the request for 005930: rt_cd=1 msg_cd=OPSQ9999")

    monkeypatch.setattr(S, "_wide_probe", rejected)
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)
    with pytest.raises(S.KrxScanError) as exc:
        S.verify_wide_probe_positive_control(object())

    message = str(exc.value)
    assert "not retried after 1" in message, message
    assert "4 attempts" not in message, "the message still claims four tries"
    assert "OPSQ9999" in message, "the msg_cd needed to decide an allowlist was dropped"
    assert calls["n"] == 1


def test_a_TRANSPORT_refusal_reports_the_full_attempt_count(monkeypatch):
    from data import krx_scan as S

    def timeout(session, code):  # noqa: ARG001
        raise TimeoutError("timed out")

    monkeypatch.setattr(S, "_wide_probe", timeout)
    monkeypatch.setattr(S.time, "sleep", lambda *_: None)
    with pytest.raises(S.KrxScanError, match="retried 4 times"):
        S.verify_wide_probe_positive_control(object())
