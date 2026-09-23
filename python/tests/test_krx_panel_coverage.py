"""The panel's completeness check, and the three classes it must not merge.

Every test here exists for a rule CLAUDE.md already carries, not for a
shape that merely seems tidy:

- an **empty reference** makes every symbol look complete, which is the
  most dangerous way this check could fail -- it would report a verified
  panel having verified nothing;
- a symbol printing on a day the reference lacks is evidence about the
  **reference**, so it aborts rather than being filed against the symbol;
- an **interior gap is UNKNOWN, never "not in the pool"** -- dropping the
  name is the exact survivorship bias the rule exists to prevent, and a
  halted name still prints a bar, so this is not how a halt shows up.
"""

from __future__ import annotations

import sqlite3

import pytest

from data.kis_klines import ReferenceCalendarError, trading_date_to_ms
from research.krx_panel_coverage import (
    PanelCoverageError,
    coverage_for,
    panel_coverage,
    reference_days,
)

# 2024-05-02, -03, -07 -- a real KRX shape: the 4th/5th/6th are a weekend
# plus 어린이날 substitute, so the calendar is not an arithmetic step.
DAYS = ("20240502", "20240503", "20240507", "20240508")


def _reference_db(days=DAYS, symbol="KRX-INDEX:0001"):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE klines (symbol TEXT, interval TEXT, open_time_ms INTEGER)"
    )
    conn.executemany(
        "INSERT INTO klines VALUES (?, '1d', ?)",
        [(symbol, trading_date_to_ms(d)) for d in days],
    )
    return conn


def _scan_db(rows: dict[str, list[str]], frozen: dict[str, int] | None = None):
    """`{code: [bsop_date, ...]}` -> a scan database with matching progress."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE scan_bars (code TEXT, bsop_date TEXT, PRIMARY KEY (code, bsop_date))"
    )
    conn.execute(
        "CREATE TABLE scan_progress (code TEXT PRIMARY KEY, first_date TEXT, "
        "last_date TEXT, bars INTEGER, frozen INTEGER, status TEXT, fetched_at TEXT)"
    )
    for code, dates in rows.items():
        conn.executemany(
            "INSERT INTO scan_bars VALUES (?, ?)", [(code, d) for d in dates]
        )
        conn.execute(
            "INSERT INTO scan_progress VALUES (?,?,?,?,?,'done','t')",
            (code, min(dates), max(dates), len(dates), (frozen or {}).get(code, 0)),
        )
    return conn


# ------------------------------------------------------ the reference itself


def test_an_empty_reference_is_refused():
    """**The most dangerous failure this check has.** With no calendar
    every symbol is trivially complete, and the run would report a
    verified panel having verified nothing."""
    with pytest.raises(PanelCoverageError, match="no stored bars"):
        reference_days(_reference_db(days=()))


def test_a_bar_the_reference_LACKS_aborts_rather_than_counting_against_the_symbol():
    """An index prints whenever the market is open, so a symbol trading on
    a day it lacks proves the reference is truncated -- most likely at its
    50-row cap. That is a fact about the calendar, not the symbol."""
    ref = reference_days(_reference_db(days=("20240502", "20240503")))
    scan = _scan_db({"005930": ["20240502", "20240503", "20240507"]})
    with pytest.raises(ReferenceCalendarError, match="reference"):
        coverage_for(scan, "005930", ref)


# ------------------------------------------------- the three kinds of absence


def test_a_complete_symbol_has_no_interior_gaps():
    ref = reference_days(_reference_db())
    cov = coverage_for(_scan_db({"005930": list(DAYS)}), "005930", ref)
    assert cov.is_complete
    assert cov.interior_gaps == ()
    assert (cov.before_first, cov.after_last) == (0, 0)


def test_days_before_the_first_bar_are_NOT_a_gap():
    """A later listing is not a hole in the series, and counting it as one
    would make every 2026 listing look catastrophically incomplete -- 80
    live names carry KOSDAQ's new alphanumeric codes and all of them are
    2026 listings."""
    ref = reference_days(_reference_db())
    cov = coverage_for(_scan_db({"A": ["20240507", "20240508"]}), "A", ref)
    assert cov.is_complete, "a late listing must not read as a gap"
    assert cov.before_first == 2


def test_days_after_the_last_bar_are_NOT_a_gap():
    """Delisting is not failure and not absence: CLAUDE.md's rule 2 takes
    the exit price from that last bar rather than booking −100%."""
    ref = reference_days(_reference_db())
    cov = coverage_for(_scan_db({"A": ["20240502", "20240503"]}), "A", ref)
    assert cov.is_complete
    assert cov.after_last == 2


def test_an_INTERIOR_gap_is_reported_and_is_the_only_real_one():
    """**UNKNOWN, never "not in the pool".** A halted name still prints a
    bar (`O==H==L==C`, zero turnover -- 신라젠 carries 604 consecutive
    such sessions), so an interior gap is not how a halt shows up. It is
    something rarer or an incomplete fetch, and dropping the name is the
    survivorship bias the rule exists to prevent."""
    ref = reference_days(_reference_db())
    cov = coverage_for(_scan_db({"A": ["20240502", "20240508"]}), "A", ref)
    assert not cov.is_complete
    assert cov.interior_gaps == ("20240503", "20240507")
    assert (cov.before_first, cov.after_last) == (0, 0)
    assert cov.span_days == 4, "the span covers the gap days too"


def test_the_three_classes_are_counted_separately_on_one_symbol():
    """The point of keeping them apart: a name listed late, gapped in the
    middle and delisted early must not collapse into one number."""
    ref = reference_days(_reference_db(days=("20240502", "20240503", "20240507",
                                             "20240508", "20240509")))
    cov = coverage_for(_scan_db({"A": ["20240503", "20240508"]}), "A", ref)
    assert cov.before_first == 1
    assert cov.interior_gaps == ("20240507",)
    assert cov.after_last == 1


# ---------------------------------------------------------------- the panel


def test_the_panel_judges_every_completed_symbol():
    ref = reference_days(_reference_db())
    scan = _scan_db({"A": list(DAYS), "B": ["20240502", "20240508"]})
    rows = panel_coverage(scan, ref)
    assert [r.code for r in rows] == ["A", "B"]
    assert [r.is_complete for r in rows] == [True, False]


def test_a_symbol_that_never_completed_is_not_silently_judged():
    """A `failed:` row has no trustworthy first/last date, so judging it
    would manufacture a coverage verdict out of a failed fetch."""
    ref = reference_days(_reference_db())
    scan = _scan_db({"A": list(DAYS)})
    scan.execute("UPDATE scan_progress SET status = 'failed:X' WHERE code = 'A'")
    assert panel_coverage(scan, ref) == []
    with pytest.raises(PanelCoverageError, match="no completed"):
        coverage_for(scan, "A", ref)


def test_a_run_that_judges_NOTHING_is_refused(tmp_path, capsys):
    """**The symmetric case to an empty reference**, and it was missed on
    the first version. `reference_days` already refuses an empty calendar
    because it makes every symbol look complete; an empty symbol list
    makes the whole panel look complete the same way — zero gaps out of
    zero judged, printed as a clean report and exit 0.

    A verification that cannot fail is not evidence, which is the rule
    `change_check.check_script_fails_closed` exists for.
    """
    import sqlite3

    from research.krx_panel_coverage import main

    ref, scan = tmp_path / "ref.sqlite3", tmp_path / "scan.sqlite3"
    rc = sqlite3.connect(ref)
    rc.execute("CREATE TABLE klines (symbol TEXT, interval TEXT, open_time_ms INTEGER)")
    rc.executemany("INSERT INTO klines VALUES ('KRX-INDEX:0001','1d',?)",
                   [(trading_date_to_ms(d),) for d in DAYS])
    rc.commit(); rc.close()

    sc = sqlite3.connect(scan)
    sc.execute("CREATE TABLE scan_bars (code TEXT, bsop_date TEXT)")
    sc.execute(
        "CREATE TABLE scan_progress (code TEXT PRIMARY KEY, first_date TEXT, "
        "last_date TEXT, bars INTEGER, frozen INTEGER, status TEXT, fetched_at TEXT)"
    )
    # a scan that has only failures -- exactly what a broken pass looks like
    sc.execute("INSERT INTO scan_progress VALUES ('005930',NULL,NULL,0,0,'failed:X','t')")
    sc.commit(); sc.close()

    assert main(["--scan-db", str(scan), "--reference-db", str(ref)]) == 1
    assert "REFUSED" in capsys.readouterr().err


def test_frozen_bars_are_carried_through_rather_than_rediscovered():
    """They are the opposite failure to a gap -- present, and not evidence
    the name was tradeable -- so the count travels with the coverage."""
    ref = reference_days(_reference_db())
    cov = coverage_for(_scan_db({"A": list(DAYS)}, frozen={"A": 3}), "A", ref)
    assert cov.frozen == 3
    assert cov.is_complete, "a frozen bar is present; it is not a gap"
