"""Tests for `research.krx_dayone_universe`.

The properties worth guarding are the ones whose failure reproduces the
bias this module exists to remove, silently:

- **the pool must contain delisted names**, or it is
  currently-listed-only and biased upward by construction
- **a zero-row answer must not become `ABSENT` on trust**, because a
  broken request shape returns exactly that for everything
- **the ranking window must stay under the row cap**, or the declared
  window is not the measured one
- **the selection must be frozen**, since re-ranking on a later window is
  precisely what produced `rd-v`'s +54%/yr
"""

from __future__ import annotations

import json

import pytest

from data.store import connect, upsert_krx_delisted, upsert_krx_universe
from research.krx_dayone_universe import (
    MIN_RANKING_BARS,
    NEGATIVE_CONTROLS,
    RANKING_END,
    RANKING_START,
    UNIVERSE_SIZE,
    Candidate,
    DayOneUniverseError,
    Outcome,
    candidates,
    rank,
    select,
    verify_negative_controls,
)


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "u.sqlite3")
    upsert_krx_universe(
        conn,
        "2026-09-20",
        [
            ("005930", "KOSPI", "삼성전자", "ST", "KR7005930003"),
            ("005935", "KOSPI", "삼성전자우", "ST", "KR7005931001"),
            ("069500", "KOSPI", "KODEX 200", "EF", "KR7069500007"),
        ],
    )
    upsert_krx_delisted(
        conn,
        "2026-09-20",
        [
            ("117930", "유가증권", "한진해운", "KR7117930008"),
            ("002365", "유가증권", "SH에너지화학우", "KR7002361001"),
            ("3686001G", "코스닥", "아이씨에이치 5R", "KR43686001G6"),
        ],
    )
    return conn


class _FakeSession:
    """Answers per code from a dict of row-lists."""

    def __init__(self, by_code, *, fail=()):
        self.by_code = by_code
        self.fail = set(fail)
        self.asked: list[str] = []

    def headers(self, tr_id):  # noqa: ARG002
        return {}


def _install(monkeypatch, session):
    def fake_get(url, headers):  # noqa: ARG001
        from urllib.parse import parse_qs, urlparse

        code = parse_qs(urlparse(url).query)["FID_INPUT_ISCD"][0]
        session.asked.append(code)
        if code in session.fail:
            raise RuntimeError("simulated transport failure")
        return {"rt_cd": "0", "output2": session.by_code.get(code, [])}

    monkeypatch.setattr("research.krx_dayone_universe._get_with_retry", fake_get)
    monkeypatch.setattr("research.krx_dayone_universe.time.sleep", lambda *_: None)


def _bars(n, turnover):
    return [
        {"stck_bsop_date": f"201901{d:02d}", "acml_tr_pbmn": str(turnover)}
        for d in range(2, 2 + n)
    ]


# ============================== the pool must include the dead names


def test_the_pool_contains_delisted_common_stock(db):
    """**The whole construction.** A pool of currently-listed names is
    biased upward by construction, and `ms-e`'s own draft made exactly
    that mistake before review caught it."""
    pool = candidates(db)
    codes = {c[0] for c in pool}
    assert "117930" in codes, "한진해운 (delisted 2017) must be a candidate"
    assert {c[0] for c in pool if not c[3]} == {"117930"}


def test_the_pool_excludes_preferred_and_non_stock(db):
    """Both filters, live and dead: 삼성전자우 and SH에너지화학우 are
    preferred lines, KODEX 200 is an ETF, and the rights entitlement has
    no plain code."""
    codes = {c[0] for c in candidates(db)}
    assert codes == {"005930", "117930"}


def test_no_delisted_snapshot_is_refused_rather_than_silently_survivorship(tmp_path):
    """**Fail closed, and this is the one that matters most.** Falling
    back to live names alone would produce a perfectly clean-looking
    universe carrying the exact bias this module was written to remove."""
    conn = connect(tmp_path / "u.sqlite3")
    upsert_krx_universe(
        conn, "2026-09-20",
        [("005930", "KOSPI", "삼성전자", "ST", "KR7005930003")],
    )
    with pytest.raises(DayOneUniverseError, match="survivorship-contaminated"):
        candidates(conn)


def test_no_universe_snapshot_is_refused(tmp_path):
    conn = connect(tmp_path / "u.sqlite3")
    with pytest.raises(DayOneUniverseError, match="no krx_universe snapshot"):
        candidates(conn)


# ============================ a zero-row answer is not evidence on its own


def test_the_negative_controls_are_actually_asked(monkeypatch):
    session = _FakeSession({})
    _install(monkeypatch, session)
    verify_negative_controls(session)
    assert set(session.asked) == set(NEGATIVE_CONTROLS)


def test_a_negative_control_returning_bars_refuses_the_run(monkeypatch):
    """If a code that cannot exist answers with data, the request is
    reaching something other than what this module thinks it is."""
    session = _FakeSession({NEGATIVE_CONTROLS[0]: _bars(20, 1e9)})
    _install(monkeypatch, session)
    with pytest.raises(DayOneUniverseError, match="cannot be a real listing"):
        verify_negative_controls(session)


def test_a_negative_control_that_errors_refuses_the_run(monkeypatch):
    """**Not the same as it answering empty.** A control that cannot be
    asked leaves every `ABSENT` unreadable."""
    session = _FakeSession({}, fail={NEGATIVE_CONTROLS[0]})
    _install(monkeypatch, session)
    with pytest.raises(DayOneUniverseError, match="failed to answer"):
        verify_negative_controls(session)


def test_a_failed_request_is_ERROR_and_never_ABSENT(monkeypatch):
    """**The direction that reintroduces the bias.** Merging a transport
    failure into "not listed" drops a real name from the pool, and the
    run would look clean."""
    session = _FakeSession({"005930": _bars(20, 1e12)}, fail={"117930"})
    _install(monkeypatch, session)
    out = {c.code: c.outcome for c in rank(session, [
        ("005930", "삼성전자", "KOSPI", True),
        ("117930", "한진해운", "유가증권", False),
    ])}
    assert out["005930"] == Outcome.RANKED.value
    assert out["117930"] == Outcome.ERROR.value
    assert out["117930"] != Outcome.ABSENT.value


def test_a_genuinely_empty_answer_is_ABSENT(monkeypatch):
    session = _FakeSession({})
    _install(monkeypatch, session)
    out = rank(session, [("117930", "한진해운", "유가증권", False)])
    assert out[0].outcome == Outcome.ABSENT.value


# ============================================ the window and the cap


def test_the_ranking_window_is_one_month_and_under_the_cap():
    """One month is ~21 trading days. Declaring a longer window would put
    it over KIS's 100-row equity cap, which truncates silently and keeps
    the NEWEST rows — so the measured window would not be the declared
    one."""
    assert RANKING_START[:6] == RANKING_END[:6] == "201901"
    assert MIN_RANKING_BARS < 100


def test_a_capped_response_is_refused(monkeypatch):
    session = _FakeSession({"005930": _bars(100, 1e12)})
    _install(monkeypatch, session)
    out = rank(session, [("005930", "삼성전자", "KOSPI", True)])
    assert out[0].outcome == Outcome.ERROR.value, "a capped page must not rank"


def test_a_barely_traded_name_is_THIN_not_ranked(monkeypatch):
    """One or two prints in a month is a halt, and its median turnover is
    not a liquidity measure."""
    session = _FakeSession({"005930": _bars(MIN_RANKING_BARS - 1, 1e12)})
    _install(monkeypatch, session)
    assert rank(session, [("005930", "삼성전자", "KOSPI", True)])[0].outcome == (
        Outcome.THIN.value
    )


# ====================================================== the selection


def test_selection_is_by_median_turnover_descending():
    ranked = [
        Candidate("A00001", "small", "KOSPI", True, Outcome.RANKED.value, 20, 1e8),
        Candidate("B00002", "big", "KOSPI", True, Outcome.RANKED.value, 20, 9e11),
        Candidate("C00003", "mid", "KOSPI", True, Outcome.RANKED.value, 20, 5e10),
    ]
    assert [c.code for c in select(ranked, 2)] == ["B00002", "C00003"]


def test_only_RANKED_candidates_can_be_selected():
    ranked = [
        Candidate("A00001", "absent", "KOSPI", True, Outcome.ABSENT.value),
        Candidate("B00002", "error", "KOSPI", True, Outcome.ERROR.value),
        Candidate("C00003", "thin", "KOSPI", True, Outcome.THIN.value, 3),
        Candidate("D00004", "real", "KOSPI", True, Outcome.RANKED.value, 20, 1e9),
    ]
    assert [c.code for c in select(ranked)] == ["D00004"]


def test_a_tie_breaks_deterministically():
    """Two runs of the same ranking must give the same universe, or the
    selection is not frozen in any meaningful sense."""
    ranked = [
        Candidate("B00002", "b", "KOSPI", True, Outcome.RANKED.value, 20, 1e9),
        Candidate("A00001", "a", "KOSPI", True, Outcome.RANKED.value, 20, 1e9),
    ]
    assert [c.code for c in select(ranked, 1)] == ["A00001"]
    assert select(ranked, 1) == select(list(reversed(ranked)), 1)


def test_a_delisted_name_can_win_a_place():
    """**The point of the whole exercise.** If the selection could only
    ever return names that are still listed, nothing would have changed."""
    ranked = [
        Candidate("005930", "삼성전자", "KOSPI", True, Outcome.RANKED.value, 20, 1e11),
        Candidate("117930", "한진해운", "유가증권", False, Outcome.RANKED.value, 20, 9e11),
    ]
    chosen = select(ranked, 1)
    assert chosen[0].code == "117930" and chosen[0].listed_now is False


def test_the_universe_size_is_declared_not_derived():
    assert UNIVERSE_SIZE == 30


# ============================ review findings, PR #190


def test_a_candidate_needs_MIN_RANKING_BARS_of_valid_turnover(monkeypatch):
    """**`len(rows)` is not the same check.** Twenty bars carrying three
    usable 거래대금 values would rank a candidate on the median of three
    numbers, which is not a liquidity measure — and `bars` in the artifact
    would say 20."""
    rows = _bars(20, 1e12)
    for r in rows[3:]:
        r["acml_tr_pbmn"] = ""          # only 3 usable values remain
    session = _FakeSession({"005930": rows})
    _install(monkeypatch, session)
    assert rank(session, [("005930", "삼성전자", "KOSPI", True)])[0].outcome == (
        Outcome.THIN.value
    )


@pytest.mark.parametrize("bad", ["nan", "-1", "abc", "inf"])
def test_an_unusable_turnover_value_is_excluded_not_coerced(monkeypatch, bad):
    """NaN, a negative, a non-numeric string and an infinity are data
    errors, not small numbers. Coercing any of them to 0.0 would rank the
    name lower rather than decline to rank it."""
    rows = _bars(MIN_RANKING_BARS + 2, 1e12)
    for r in rows:
        r["acml_tr_pbmn"] = bad
    session = _FakeSession({"005930": rows})
    _install(monkeypatch, session)
    assert rank(session, [("005930", "삼성전자", "KOSPI", True)])[0].outcome == (
        Outcome.THIN.value
    )


def test_a_probe_or_second_arm_cannot_overwrite_the_canonical_artifact(
    monkeypatch, capsys
):
    """**`--limit` and `--window` both produce a different universe.**
    Either landing in `runs/krx_dayone_universe.json` hands the drift
    measurement a corrupted universe that looks canonical.

    **The first version of this test was inert**: it asserted only on the
    exit code, and `main` returns 2 for missing credentials as well — so
    deleting the guard changed nothing it could see. The credentials are
    now set, which makes the guard the only remaining source of a 2, and
    the message is asserted on.
    """
    from research.krx_dayone_universe import DEFAULT_OUT, main

    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")

    assert main(["--rank", "--limit", "5"]) == 2
    assert "non-canonical" in capsys.readouterr().err

    assert main(["--rank", "--window", "20260102..20260131"]) == 2
    assert "non-canonical" in capsys.readouterr().err

    assert str(DEFAULT_OUT).endswith("krx_dayone_universe.json")


def test_an_ERROR_refuses_to_write_an_artifact(monkeypatch, tmp_path, db):
    """A candidate whose turnover was never read might have belonged in the
    thirty, and nothing here can say. Writing anyway makes a partial pass
    indistinguishable from a complete one."""
    from research.krx_dayone_universe import DayOneUniverseError, main

    session = _FakeSession({"005930": _bars(20, 1e12)}, fail={"117930"})
    _install(monkeypatch, session)
    monkeypatch.setattr("research.krx_dayone_universe.KisSession",
                        lambda *a, **k: session)
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setattr("research.krx_dayone_universe.connect", lambda *_a: db)
    out = tmp_path / "u.json"
    with pytest.raises(DayOneUniverseError, match="failed to rank"):
        main(["--rank", "--out", str(out), "--db-path", str(tmp_path / "x.db")])
    assert not out.exists(), "a partial universe must leave no artifact"


def test_a_short_universe_refuses_to_write(monkeypatch, tmp_path, db):
    """Fewer than `UNIVERSE_SIZE` places filled means the pool or the
    window is wrong; a short universe written as canonical reads as
    complete."""
    from research.krx_dayone_universe import DayOneUniverseError, main

    session = _FakeSession({"005930": _bars(20, 1e12)})
    _install(monkeypatch, session)
    monkeypatch.setattr("research.krx_dayone_universe.KisSession",
                        lambda *a, **k: session)
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setattr("research.krx_dayone_universe.connect", lambda *_a: db)
    out = tmp_path / "u.json"
    with pytest.raises(DayOneUniverseError, match="places filled"):
        main(["--rank", "--out", str(out), "--db-path", str(tmp_path / "x.db")])
    assert not out.exists()


def test_the_window_is_threaded_into_the_request(monkeypatch):
    """A second arm holding the turnover SOURCE fixed and moving only the
    date is the only way to isolate the selection-date effect — so the
    window has to reach the request rather than being a constant."""
    from research.krx_dayone_universe import _fetch_window

    seen = {}

    def fake_get(url, headers):  # noqa: ARG001
        from urllib.parse import parse_qs, urlparse

        q = parse_qs(urlparse(url).query)
        seen["start"] = q["FID_INPUT_DATE_1"][0]
        seen["end"] = q["FID_INPUT_DATE_2"][0]
        return {"rt_cd": "0", "output2": []}

    monkeypatch.setattr("research.krx_dayone_universe._get_with_retry", fake_get)
    _fetch_window(_FakeSession({}), "005930", "20260102", "20260331")
    assert seen == {"start": "20260102", "end": "20260331"}


def test_a_pool_matched_arm_is_what_isolates_the_date(monkeypatch, tmp_path, capsys):
    """**Two arms that differ in their membership as well as their ranking
    date are not a control.** A later window otherwise admits every name
    that listed in between — `402340` SK스퀘어 first traded 2021-11-29 —
    so `--pool-from` restricts the second arm to the codes the first one
    ranked."""
    import json

    from research.krx_dayone_universe import main

    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    # it is a non-canonical arm, so it must still demand its own --out
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps({"all_ranked": [{"code": "005930"}]}), encoding="utf-8")
    assert main(["--rank", "--pool-from", str(prior)]) == 2
    assert "non-canonical" in capsys.readouterr().err


def test_a_cached_candidate_is_not_asked_again(monkeypatch, tmp_path):
    """**A full pass is hours of real API calls.** Without a cache, one
    transient failure two thirds of the way through discards all of them —
    which is what happened on the first corrected re-run, at candidate
    2,844 of 4,643."""
    from research.krx_dayone_universe import rank as rank_fn

    cache = tmp_path / "c.jsonl"
    session = _FakeSession({"005930": _bars(20, 1e12), "000660": _bars(20, 5e11)})
    _install(monkeypatch, session)
    pool = [("005930", "삼성전자", "KOSPI", True), ("000660", "SK하이닉스", "KOSPI", True)]
    first = rank_fn(session, pool, cache_path=cache)
    assert len(session.asked) == 2

    session.asked.clear()
    second = rank_fn(session, pool, cache_path=cache)
    assert session.asked == [], "a cached candidate must not be re-asked"
    assert [c.code for c in second] == [c.code for c in first]
    assert [c.median_turnover for c in second] == [c.median_turnover for c in first]


def test_an_ERROR_is_never_WRITTEN_to_the_cache(monkeypatch, tmp_path):
    """Caching a failure would make it permanent, which is the opposite of
    what the cache is for.

    **This asserts on the FILE, not on `load_cache`.** The two guards —
    not writing an error, and ignoring one on read — mask each other, so a
    test going through `load_cache` passes with either deleted. That is
    how the first version of this test was inert.
    """
    import json as _json

    from research.krx_dayone_universe import rank as rank_fn

    cache = tmp_path / "c.jsonl"
    session = _FakeSession({"005930": _bars(20, 1e12)}, fail={"117930"})
    _install(monkeypatch, session)
    pool = [("005930", "삼성전자", "KOSPI", True), ("117930", "한진해운", "유가증권", False)]
    out = rank_fn(session, pool, cache_path=cache)
    assert {c.code: c.outcome for c in out}["117930"] == Outcome.ERROR.value

    written = [
        _json.loads(line)
        for line in cache.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["code"] for row in written] == ["005930"]
    assert all(row["outcome"] != Outcome.ERROR.value for row in written)


def test_load_cache_ignores_an_ERROR_row_it_finds(tmp_path):
    """The read side is defensive, for a cache file written by an earlier
    version or edited by hand. Tested separately from the write side
    precisely because either one alone would hide the other."""
    import json as _json

    from research.krx_dayone_universe import load_cache

    cache = tmp_path / "c.jsonl"
    cache.write_text(
        _json.dumps({"code": "005930", "name": "a", "market": "KOSPI",
                     "listed_now": True, "outcome": Outcome.RANKED.value,
                     "bars": 20, "median_turnover": 1e12}) + "\n"
        + _json.dumps({"code": "117930", "name": "b", "market": "KOSPI",
                       "listed_now": False, "outcome": Outcome.ERROR.value,
                       "bars": 0, "median_turnover": 0.0}) + "\n",
        encoding="utf-8",
    )
    assert set(load_cache(cache)) == {"005930"}


def test_the_negative_control_checks_the_WINDOW_IT_WAS_GIVEN(monkeypatch):
    """**It took `start`/`end` and called `_fetch_window` without them**,
    so a `--window` run validated the 2019 request shape and never its
    own. A control that checks a different question is not a control."""
    from research.krx_dayone_universe import verify_negative_controls

    seen = []

    def fake_get(url, headers):  # noqa: ARG001
        from urllib.parse import parse_qs, urlparse

        q = parse_qs(urlparse(url).query)
        seen.append((q["FID_INPUT_DATE_1"][0], q["FID_INPUT_DATE_2"][0]))
        return {"rt_cd": "0", "output2": []}

    monkeypatch.setattr("research.krx_dayone_universe._get_with_retry", fake_get)
    monkeypatch.setattr("research.krx_dayone_universe.time.sleep", lambda *_: None)
    verify_negative_controls(_FakeSession({}), "20260102", "20260331")
    assert set(seen) == {("20260102", "20260331")}, seen


def test_a_cache_row_from_another_WINDOW_is_ignored(monkeypatch, tmp_path):
    """**Two arms sharing a cache file would make the second one a copy of
    the first**, with no API call and nothing in the output saying so —
    the exact confound the second arm exists to remove."""
    from research.krx_dayone_universe import rank as rank_fn

    cache = tmp_path / "c.jsonl"
    session = _FakeSession({"005930": _bars(20, 1e12)})
    _install(monkeypatch, session)
    pool = [("005930", "삼성전자", "KOSPI", True)]

    first = rank_fn(session, pool, cache_path=cache, start="20190102", end="20190131")
    assert len(session.asked) == 1
    assert first[0].median_turnover == pytest.approx(1e12)

    # the same cache, a different window: it must ask again, not reuse
    session.by_bld = None  # unused; the fake answers from by_code
    session.by_code = {"005930": _bars(20, 7e11)}
    session.asked.clear()
    second = rank_fn(session, pool, cache_path=cache, start="20260102", end="20260331")
    assert session.asked == ["005930"], "a row from another window was reused"
    assert second[0].median_turnover == pytest.approx(7e11)


def test_the_same_window_still_reuses_its_cache(monkeypatch, tmp_path):
    """The control above must not disable caching altogether."""
    from research.krx_dayone_universe import rank as rank_fn

    cache = tmp_path / "c.jsonl"
    session = _FakeSession({"005930": _bars(20, 1e12)})
    _install(monkeypatch, session)
    pool = [("005930", "삼성전자", "KOSPI", True)]
    rank_fn(session, pool, cache_path=cache, start="20190102", end="20190131")
    session.asked.clear()
    rank_fn(session, pool, cache_path=cache, start="20190102", end="20190131")
    assert session.asked == []
