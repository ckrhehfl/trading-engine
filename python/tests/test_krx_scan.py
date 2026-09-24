"""Tests for `data.krx_scan`.

A pass this long fails differently from a short one, so the properties
that matter are about surviving and reporting rather than about refusing:

- **it must resume**, because a two-to-three-day pass will be interrupted
- **one failure must not abort it**, which is the opposite of
  `krx_dayone_drift`'s rule and deliberately so — there a missing name
  made the measurement wrong, here partial progress is the point
- **the coverage report is what stops a partial scan being read as a
  complete one**, so it has to distinguish absent from failed
- **it must not run during the KRX session**, because the instance's
  collectors share this app key and collect series that cannot be
  backfilled
"""

from __future__ import annotations

import datetime as dt

import pytest

from data.krx_scan import (
    KST,
    NEGATIVE_CONTROLS,
    PANEL_END,
    PANEL_START,
    SCAN_SCHEMA,
    KrxScanError,
    _pages,
    already_done,
    candidates,
    in_continuous_session,
    scan,
    store,
    verify_negative_controls,
)
from data.store import connect, upsert_krx_delisted, upsert_krx_universe


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "scan.sqlite3")
    conn.executescript(SCAN_SCHEMA)
    conn.commit()
    return conn


def _bars(n, price=100, turnover=1000):
    return [
        {
            "stck_bsop_date": f"2019{m:02d}{d:02d}",
            "stck_oprc": str(price), "stck_hgpr": str(price),
            "stck_lwpr": str(price), "stck_clpr": str(price),
            "acml_vol": "10", "acml_tr_pbmn": str(turnover),
        }
        for m in (1,) for d in range(2, 2 + n)
    ]


class _FakeSession:
    def __init__(self, by_code, *, fail=()):
        self.by_code = by_code
        self.fail = set(fail)
        self.asked: list[str] = []

    def headers(self, tr):  # noqa: ARG002
        return {}


def _install(monkeypatch, session):
    def fake_get(url, headers):  # noqa: ARG001
        from urllib.parse import parse_qs, urlparse

        code = parse_qs(urlparse(url).query)["FID_INPUT_ISCD"][0]
        session.asked.append(code)
        if code in session.fail:
            raise RuntimeError("simulated outage")
        return {"rt_cd": "0", "output2": session.by_code.get(code, [])}

    monkeypatch.setattr("data.krx_scan._get_with_retry", fake_get)
    monkeypatch.setattr("data.krx_scan.time.sleep", lambda *_: None)


# ==================================== the session guard


@pytest.mark.parametrize(
    "when,expected",
    [
        ("2026-09-21 10:00", True),    # Monday, mid-session
        ("2026-09-21 08:59", False),   # before the open
        ("2026-09-21 15:31", False),   # after the close
        ("2026-09-19 10:00", False),   # Saturday
        ("2026-09-20 10:00", False),   # Sunday
    ],
)
def test_the_session_window_is_KST_and_weekdays_only(when, expected):
    """**The instance's collectors share this app key.** Throughput was
    measured at 0.3/s in-session against 0.5-0.7/s after the close, which
    is contention costing the thing that cannot be backfilled."""
    moment = dt.datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
    assert in_continuous_session(moment) is expected


def test_scanning_in_session_is_refused_without_the_override(monkeypatch, tmp_path):
    from data.krx_scan import main

    monkeypatch.setattr("data.krx_scan.in_continuous_session", lambda *_a: True)
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    assert main(["--scan", "--db-path", str(tmp_path / "s.sqlite3")]) == 2


# ==================================== the pool


def test_the_pool_is_live_PLUS_delisted_common_stock(db):
    upsert_krx_universe(db, "2026-09-21", [
        ("005930", "KOSPI", "삼성전자", "ST", "KR7005930003"),
        ("005935", "KOSPI", "삼성전자우", "ST", "KR7005931001"),
        ("069500", "KOSPI", "KODEX 200", "EF", "KR7069500007"),
    ])
    upsert_krx_delisted(db, "2026-09-21", [
        ("117930", "유가증권", "한진해운", "KR7117930008"),
        ("002365", "유가증권", "SH에너지화학우", "KR7002361001"),
    ])
    assert {c[0] for c in candidates(db)} == {"005930", "117930"}


def test_BOTH_sides_of_the_pool_apply_the_SAME_four_filters(db):
    """**Half a filter is worse than none, because the pool looks
    filtered.** When the SPAC rule landed, the live side picked it up
    through `fetch_krx_universe(common_stock_only=True)` while the delisted
    side still carried its 178 SPACs — and the pool's own docstring called
    itself survivorship-safe common stock throughout."""
    upsert_krx_universe(db, "2026-09-21", [
        ("005930", "KOSPI", "삼성전자", "ST", "KR7005930003"),
        ("223040", "KOSDAQ", "교보5호스팩", "ST", "KR7223040007"),
    ])
    upsert_krx_delisted(db, "2026-09-21", [
        ("117930", "유가증권", "한진해운", "KR7117930008"),
        ("232040", "코스닥", "하나머스트7호스팩", "KR7232040008"),
        ("088260", "유가증권", "이리츠코크렙", "KR7088260005"),
        ("500006", "코스닥", "an ETN", "KRG500000671"),
    ])
    assert {c[0] for c in candidates(db)} == {"005930", "117930"}


def test_no_delisted_snapshot_is_refused(db):
    upsert_krx_universe(db, "2026-09-21",
                        [("005930", "KOSPI", "삼성전자", "ST", "KR7005930003")])
    with pytest.raises(KrxScanError, match="survivorship-contaminated"):
        candidates(db)


# ==================================== negative controls


def test_the_controls_run_before_anything_else(monkeypatch):
    session = _FakeSession({})
    _install(monkeypatch, session)
    verify_negative_controls(session)
    assert set(session.asked) == set(NEGATIVE_CONTROLS)


def test_a_control_returning_bars_refuses_the_scan(monkeypatch):
    session = _FakeSession({NEGATIVE_CONTROLS[0]: _bars(5)})
    _install(monkeypatch, session)
    with pytest.raises(KrxScanError, match="reaching something other"):
        verify_negative_controls(session)


def test_a_control_that_cannot_be_asked_refuses_the_scan(monkeypatch):
    session = _FakeSession({}, fail={NEGATIVE_CONTROLS[0]})
    _install(monkeypatch, session)
    with pytest.raises(KrxScanError, match="could not be asked"):
        verify_negative_controls(session)


# ==================================== resume, survive, report


def test_a_completed_symbol_is_not_fetched_again(monkeypatch, db):
    """**A two-to-three-day pass will be interrupted.** Resumption reads
    the database rather than a sidecar, so a half-written sidecar cannot
    disagree with what is actually stored."""
    session = _FakeSession({"005930": _bars(5)})
    _install(monkeypatch, session)
    pool = [("005930", "삼성전자", True)]
    scan(session, db, pool)
    assert already_done(db) == {"005930"}

    session.asked.clear()
    counts = scan(session, db, pool)
    assert session.asked == [], "a completed symbol was re-fetched"
    assert counts["skipped"] == 1


def test_one_failure_does_not_abort_the_pass(monkeypatch, db):
    """The opposite of `krx_dayone_drift`'s rule, deliberately: there a
    missing name made the measurement wrong; here partial progress is the
    point and the coverage report is what protects the reader."""
    session = _FakeSession({"005930": _bars(5), "000660": _bars(5)},
                           fail={"117930"})
    _install(monkeypatch, session)
    pool = [("005930", "a", True), ("117930", "b", False), ("000660", "c", True)]
    counts = scan(session, db, pool)
    assert counts == {"done": 2, "absent": 0, "failed": 1, "skipped": 0}
    assert already_done(db) == {"005930", "000660"}, "a failure must not count as done"


def test_a_failed_symbol_is_retried_on_the_next_pass(monkeypatch, db):
    session = _FakeSession({"005930": _bars(5)}, fail={"005930"})
    _install(monkeypatch, session)
    pool = [("005930", "a", True)]
    assert scan(session, db, pool)["failed"] == 1
    session.fail.clear()
    session.asked.clear()
    assert scan(session, db, pool)["done"] == 1
    assert session.asked, "the failed symbol was never retried"


def test_absent_is_recorded_distinctly_from_failed(monkeypatch, db):
    """**They are different facts** and merging them is how a broken
    request becomes 'this name never traded'."""
    session = _FakeSession({}, fail={"117930"})
    _install(monkeypatch, session)
    counts = scan(session, db, [("000020", "absent", True), ("117930", "fail", False)])
    assert counts["absent"] == 1 and counts["failed"] == 1
    statuses = dict(db.execute("SELECT code, status FROM scan_progress"))
    # `absent:unknown`, not a bare `absent`: the bucket was split because
    # `rt_cd=0` with zero rows is the same answer for a dead name, an
    # out-of-range window and a code that never existed, and a first pass
    # cannot tell which. What this test was written for is unchanged -- an
    # absence is still a different fact from a failure.
    assert statuses["000020"] == "absent:unknown"
    assert statuses["117930"].startswith("failed:")


def test_a_capped_page_is_a_failure_not_data(monkeypatch, db):
    session = _FakeSession({"005930": _bars(99) + _bars(1)})
    _install(monkeypatch, session)
    counts = scan(session, db, [("005930", "a", True)])
    assert counts["failed"] == 1
    assert db.execute("SELECT COUNT(*) FROM scan_bars").fetchone()[0] == 0


def test_frozen_sessions_are_stored_and_counted(monkeypatch, db):
    """A halted name's bars are what the tape said, so they are kept —
    and **counted**, because a bar is not evidence the name was
    tradeable."""
    session = _FakeSession({"215600": _bars(5, price=7757, turnover=0)})
    _install(monkeypatch, session)
    scan(session, db, [("215600", "신라젠", True)])
    row = db.execute(
        "SELECT bars, frozen FROM scan_progress WHERE code = '215600'"
    ).fetchone()
    assert row[0] == 5 and row[1] == 5


# ==================================== paging


def test_pages_are_contiguous_and_under_the_cap():
    pages = list(_pages(PANEL_START, PANEL_END, 120))
    assert pages[0][0] == PANEL_START and pages[-1][1] == PANEL_END
    for (_, a_end), (b_start, _) in zip(pages, pages[1:]):
        gap = dt.datetime.strptime(b_start, "%Y%m%d") - dt.datetime.strptime(
            a_end, "%Y%m%d"
        )
        assert gap.days == 1, "a gap here silently drops trading days"


def test_storing_is_idempotent(db):
    rows = _bars(5)
    store(db, "X", rows)
    store(db, "X", rows)
    assert db.execute("SELECT COUNT(*) FROM scan_bars").fetchone()[0] == 5


def test_the_scan_PAUSES_for_the_session_not_just_refuses_to_start(monkeypatch, db):
    """**A 64-hour pass begun after one close runs through the next day's
    session.** The start-up guard alone would have protected only the
    first eight hours of three days — so the loop re-checks, and waits."""
    session = _FakeSession({"005930": _bars(5), "000660": _bars(5)})
    _install(monkeypatch, session)

    # in session for the first check, closed afterwards
    states = iter([True, False, False, False, False, False, False, False])
    monkeypatch.setattr("data.krx_scan.in_continuous_session",
                        lambda *_a: next(states, False))
    slept: list[float] = []
    monkeypatch.setattr("data.krx_scan.time.sleep", lambda s: slept.append(s))

    counts = scan(session, db, [("005930", "a", True), ("000660", "b", True)],
                  allow_in_session=False)
    assert counts["done"] == 2, "it must resume once the session closes"
    from data.krx_scan import _SESSION_POLL_S

    assert _SESSION_POLL_S in slept, "it never waited for the session to close"


def test_a_pause_is_reported_ONCE_and_carries_no_rate(monkeypatch, db):
    """The real run logged `PAUSED … 0.00 sym/s eta 20786666666.7h`, once
    every poll. Two defects in one line: a state change printed as a
    measurement, and an ETA invented from a rate of zero. A reader of that
    log cannot tell a pause from a hang."""
    session = _FakeSession({"005930": _bars(5)})
    _install(monkeypatch, session)
    states = iter([True, True, True, False])
    monkeypatch.setattr("data.krx_scan.in_continuous_session",
                        lambda *_a: next(states, False))
    monkeypatch.setattr("data.krx_scan.time.sleep", lambda _s: None)

    seen: list[tuple[str, float | None]] = []
    scan(session, db, [("005930", "a", True)], allow_in_session=False,
         progress=lambda i, n, code, name, rate: seen.append((name, rate)))

    paused = [r for name, r in seen if name.startswith("PAUSED")]
    assert len(paused) == 1, f"one line per pause, not per poll: {seen}"
    assert paused == [None], "a pause has no throughput to report"
    assert any(name.startswith("resumed") for name, _ in seen), (
        "the resume must be logged too, or the pause looks unbounded"
    )


def test_paused_time_is_excluded_from_the_reported_rate(monkeypatch, db):
    """**A reported figure taken from the wrong denominator** — the same
    shape as Task C's `+45` that was really `−97`. Six hours of waiting in
    the divisor is what produced the billion-hour ETA."""
    session = _FakeSession({"005930": _bars(5)})
    _install(monkeypatch, session)
    # **After `_install`, which patches `time.sleep` itself.** Setting the
    # clock first left it frozen, `worked` at 0 either way, and this test
    # passing with the fix deleted -- the sixth inert guard this arc.
    clock = [0.0]
    monkeypatch.setattr("data.krx_scan.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("data.krx_scan.time.sleep",
                        lambda s: clock.__setitem__(0, clock[0] + s))
    states = iter([True, True, False])
    monkeypatch.setattr("data.krx_scan.in_continuous_session",
                        lambda *_a: next(states, False))

    seen: list[tuple[str, float | None]] = []
    scan(session, db, [("005930", "a", True)], allow_in_session=False,
         progress=lambda i, n, code, name, rate: seen.append((name, rate)))

    from data.krx_scan import _SESSION_POLL_S

    rates = [r for name, r in seen if r is not None]
    assert rates, "the per-symbol progress line never fired"
    assert rates[0] > 1.0 / _SESSION_POLL_S, (
        f"the {2 * _SESSION_POLL_S:.0f}s pause is still in the divisor: {rates}"
    )


def test_the_override_skips_the_pause(monkeypatch, db):
    session = _FakeSession({"005930": _bars(5)})
    _install(monkeypatch, session)
    monkeypatch.setattr("data.krx_scan.in_continuous_session", lambda *_a: True)
    slept: list[float] = []
    monkeypatch.setattr("data.krx_scan.time.sleep", lambda s: slept.append(s))
    from data.krx_scan import _SESSION_POLL_S

    assert scan(session, db, [("005930", "a", True)],
                allow_in_session=True)["done"] == 1
    assert _SESSION_POLL_S not in slept
