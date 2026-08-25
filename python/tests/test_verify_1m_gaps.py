"""Tests for `python/research/verify_1m_gaps.py` -- Scalping Strategy
Research Task S4's fail-closed gap-detection preflight.

Hermetic: every test builds its own temporary SQLite database with a
hand-controlled row set. No test here touches the real
`python/data/var/klines.sqlite3` -- see that module's own docstring for
why (never spends the holdout-access claim, but still deserves a fast,
isolated, deterministic unit-test suite rather than depending on the
real production cache's current contents).
"""

from decimal import Decimal

import pytest

from data.bingx_klines import KlineRow
from data.store import connect, upsert_klines
from research.verify_1m_gaps import KNOWN_GAPS, UnexpectedGapError, verify_1m_gaps

STEP_MS = 60_000  # 1m grid


def _conn(tmp_path):
    path = tmp_path / "klines.sqlite3"
    conn = connect(path)
    return conn, path


def _row(open_time_ms: int) -> KlineRow:
    price = Decimal("100")
    return KlineRow(
        open_time_ms=open_time_ms,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("1"),
    )


def _insert_range(conn, start_ms: int, end_ms: int, *, skip: set[int] = frozenset()) -> None:
    rows = [_row(t) for t in range(start_ms, end_ms, STEP_MS) if t not in skip]
    upsert_klines(conn, "BTC-USDT", "1m", rows)


class TestExactlyTheKnownGaps:
    def test_passes_when_the_real_gap_set_matches_exactly(self, tmp_path):
        conn, path = _conn(tmp_path)
        start_ms = KNOWN_GAPS[0][0] - 10 * STEP_MS
        end_ms = KNOWN_GAPS[1][1] + 10 * STEP_MS
        skip = set()
        for gap_start, gap_end in KNOWN_GAPS:
            skip.update(range(gap_start, gap_end, STEP_MS))
        _insert_range(conn, start_ms, end_ms, skip=skip)
        conn.close()

        gaps = verify_1m_gaps(start_ms, end_ms, db_path=path)
        assert tuple(gaps) == KNOWN_GAPS


class TestUnexpectedGap:
    def test_raises_on_an_extra_gap_beyond_the_known_two(self, tmp_path):
        conn, path = _conn(tmp_path)
        start_ms = KNOWN_GAPS[0][0] - 10 * STEP_MS
        end_ms = KNOWN_GAPS[1][1] + 10 * STEP_MS
        skip = set()
        for gap_start, gap_end in KNOWN_GAPS:
            skip.update(range(gap_start, gap_end, STEP_MS))
        # A third, unexpected gap.
        extra_gap_start = KNOWN_GAPS[0][0] - 5 * STEP_MS
        skip.add(extra_gap_start)
        _insert_range(conn, start_ms, end_ms, skip=skip)
        conn.close()

        with pytest.raises(UnexpectedGapError, match="does not exactly match"):
            verify_1m_gaps(start_ms, end_ms, db_path=path)

    def test_raises_when_a_known_gap_is_missing_ie_fewer_gaps_than_expected(self, tmp_path):
        conn, path = _conn(tmp_path)
        start_ms = KNOWN_GAPS[0][0] - 10 * STEP_MS
        end_ms = KNOWN_GAPS[1][1] + 10 * STEP_MS
        # Only skip the first known gap, not the second -- a fully
        # contiguous range at the second gap's location is itself a real
        # deviation from what was expected (e.g. the gap got backfilled)
        # and must not be silently accepted either.
        skip = set(range(KNOWN_GAPS[0][0], KNOWN_GAPS[0][1], STEP_MS))
        _insert_range(conn, start_ms, end_ms, skip=skip)
        conn.close()

        with pytest.raises(UnexpectedGapError, match="does not exactly match"):
            verify_1m_gaps(start_ms, end_ms, db_path=path)

    def test_raises_when_there_are_no_gaps_at_all(self, tmp_path):
        conn, path = _conn(tmp_path)
        start_ms = KNOWN_GAPS[0][0] - 10 * STEP_MS
        end_ms = KNOWN_GAPS[0][0] + 10 * STEP_MS
        _insert_range(conn, start_ms, end_ms)
        conn.close()

        with pytest.raises(UnexpectedGapError, match="does not exactly match"):
            verify_1m_gaps(start_ms, end_ms, db_path=path)
