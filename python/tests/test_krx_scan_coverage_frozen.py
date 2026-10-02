"""The coverage report's frozen accounting must not understate the pool.

`coverage()` printed *"N symbols carry >20 frozen sessions … M in total"*, where
M summed only those same `>20` symbols. On the completed 2019+ panel that read
186,939 against a pool total of **192,292** — understating by 5,353 sessions
across the 554 symbols holding 1-20 frozen bars each. The `>20` filter is
deliberate and useful; the phrase "in total" beside its sum is what misled, and
`rd-y-the-full-universe-scan-result.md` quoted the wrong figure before this was
found.

**Why it is worth a test rather than a careful sentence.** A frozen bar is
`O == H == L == C` with zero turnover, and `CLAUDE.md`'s standing rule is that
such a bar may never make a name eligible. The report is what a reader consults
before deciding whether a pool is usable, so a frozen count that reads low in
the safe-sounding direction is exactly the figure that gets trusted.

The 100%-frozen line exists for the same reason: the rule reads as cautionary
until you see that a symbol can be listed for 578 consecutive sessions and
traded on none of them.
"""

from __future__ import annotations

import sqlite3

import pytest

from data.krx_scan import SCAN_SCHEMA, coverage


def _db(rows: list[tuple[str, int, int]]) -> sqlite3.Connection:
    """An in-memory scan database carrying `(code, bars, frozen)` progress rows.

    Bars are inserted to match each row's count so the percentage-of-all-bars
    figure has a real denominator rather than a stubbed one.
    """
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCAN_SCHEMA)
    for code, bars, frozen in rows:
        conn.execute(
            "INSERT INTO scan_progress (code, bars, frozen, status, fetched_at) "
            "VALUES (?,?,?,?,?)",
            (code, bars, frozen, "done", "2026-10-02T00:00:00+00:00"),
        )
        # Bars are stored as TEXT by the real schema, and a frozen bar is
        # O==H==L==C with zero turnover -- written that way so the fixture
        # cannot drift from what `coverage` is describing.
        for i in range(bars):
            conn.execute(
                "INSERT INTO scan_bars "
                "(code, bsop_date, open, high, low, close, volume, turnover) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (code, f"2019{i:04d}", "1", "1", "1", "1", "0", "0"),
            )
    conn.commit()
    return conn


def test_the_pool_total_is_reported_not_just_the_sustained_subset(capsys):
    """The defect: one symbol over the threshold and several under it, where
    summing only the former loses the rest."""
    conn = _db([("AAA", 100, 50), ("BBB", 100, 5), ("CCC", 100, 7), ("DDD", 100, 8)])
    coverage(conn)
    out = capsys.readouterr().out

    assert "70 across 4 symbols" in out, (
        f"the pool total (50+5+7+8=70 over 4 symbols) must be the headline; got:\n{out}"
    )
    assert "of which 50 are in the 1 symbols carrying >20 each" in out, (
        "the sustained subset stays, labelled as a subset"
    )


def test_the_percentage_uses_all_bars_as_its_denominator(capsys):
    """70 frozen against 400 bars is 17.50%. A percentage computed against the
    frozen symbols' own bars instead would read differently and mean nothing."""
    conn = _db([("AAA", 100, 50), ("BBB", 100, 5), ("CCC", 100, 7), ("DDD", 100, 8)])
    coverage(conn)
    assert "17.50% of all bars" in capsys.readouterr().out


def test_a_fully_frozen_symbol_is_named(capsys):
    """The measurement that makes the eligibility rule concrete: listed
    throughout, traded never."""
    conn = _db([("120780", 578, 578), ("AAA", 100, 3)])
    coverage(conn)
    out = capsys.readouterr().out
    assert "1 symbols are 100% frozen" in out
    assert "120780 (578 bars)" in out
    assert "never traded" in out


def test_a_partially_frozen_symbol_is_not_counted_as_fully_frozen(capsys):
    """The count and the examples share a predicate, and writing it twice let
    one copy drift: loosening the example query to `frozen > 0` changed which
    symbols were shown while the count stayed right, and nothing caught it. One
    query now, and this asserts both halves of its output together."""
    conn = _db([("FULL", 500, 500), ("PARTIAL", 400, 399), ("LIGHT", 300, 2)])
    coverage(conn)
    out = capsys.readouterr().out
    assert "1 symbols are 100% frozen" in out, out
    assert "FULL (500 bars)" in out
    assert "PARTIAL" not in out, "399/400 is not 100% frozen"
    assert "LIGHT" not in out


def test_a_pool_with_no_halts_says_nothing_about_frozen_symbols(capsys):
    """No invented line when there is nothing to report — the counterpart to the
    rule above, so the 100%-frozen sentence means something when it appears."""
    conn = _db([("AAA", 100, 0), ("BBB", 50, 0)])
    coverage(conn)
    out = capsys.readouterr().out
    assert "0 across 0 symbols" in out
    assert "100% frozen" not in out


def test_an_empty_database_does_not_divide_by_zero(capsys):
    """`coverage` runs on a fresh file by design — it is the read-only mode that
    claims no panel — so zero bars must not raise."""
    conn = _db([])
    coverage(conn)
    out = capsys.readouterr().out
    assert "bars 0" in out
    assert "% of all bars" not in out, "no denominator, so no percentage"


@pytest.mark.parametrize("frozen,bars", [(20, 100), (21, 100)])
def test_the_threshold_is_strictly_greater_than_twenty(capsys, frozen, bars):
    """Pinned because the subset line names the number: 20 is not sustained, 21
    is. An off-by-one here would silently reclassify 554 symbols on the real
    pool."""
    conn = _db([("AAA", bars, frozen)])
    coverage(conn)
    out = capsys.readouterr().out
    expected_sustained = frozen if frozen > 20 else 0
    expected_symbols = 1 if frozen > 20 else 0
    assert f"of which {expected_sustained} are in the {expected_symbols} symbols" in out
