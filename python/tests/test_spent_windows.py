"""Tests for `research.spent_windows`.

The module exists so a check can run where the experiment log does not,
so the properties that matter are the ones that stop it from being a
comfortable lie:

- **An unidentifiable holdout access raises.** A window that cannot be
  named cannot be marked spent, and a silently dropped one reads as
  "available".
- **A corrupt middle line raises**, inheriting `read_records`' contract.
  Swallowing it would drop whatever access it held.
- **An empty or missing ledger raises rather than returning `[]`**, since
  every check built on this would then pass vacuously.
"""

from __future__ import annotations

import json

import pytest

from research.spent_windows import build, load


def _log(tmp_path, *records):
    p = tmp_path / "experiments.jsonl"
    p.write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    return p


def _access(symbol="BTC-USDT", interval="1d", at="2026-07-30T05:18:08+00:00"):
    return {
        "record_type": "holdout_access",
        "symbol": symbol,
        "interval": interval,
        "accessed_at": at,
    }


def _ranged(y1, m1, d1, y2, m2, d2, symbol="KRX:005930", interval="1d"):
    """A holdout access that carries the era it touched, which `_access` does
    not — the span is derived from `start_ms`/`end_ms`, and an access without
    them is deliberately read as an UNKNOWN era."""
    import datetime as _dt

    def _ms(y, m, d):
        return int(_dt.datetime(y, m, d, tzinfo=_dt.timezone.utc).timestamp() * 1000)

    row = _access(symbol=symbol, interval=interval)
    row["start_ms"] = _ms(y1, m1, d1)
    row["end_ms"] = _ms(y2, m2, d2)
    return row


def test_a_holdout_access_marks_its_window_spent(tmp_path):
    out = build(_log(tmp_path, _access()))["windows"]
    assert [(w["symbol"], w["interval"]) for w in out] == [("BTC-USDT", "1d")]
    assert out[0]["accesses"] == 1


def test_repeated_accesses_to_one_window_are_counted_not_duplicated(tmp_path):
    """KR-10 has three, each with its own recorded reclaim reason. The
    ledger is about which windows are gone, so they collapse to one row
    carrying the count."""
    out = build(
        _log(
            tmp_path,
            _access(at="2026-09-13T15:29:49+00:00"),
            _access(at="2026-09-13T15:30:35+00:00"),
            _access(at="2026-09-13T15:31:38+00:00"),
        )
    )["windows"]
    assert len(out) == 1
    assert out[0]["accesses"] == 3
    assert out[0]["first_access"] == "2026-09-13T15:29:49+00:00"


def test_two_intervals_of_one_symbol_are_two_windows(tmp_path):
    """BTC-USDT has been spent at 15m, 1d and 1m by three different
    strategies. Collapsing on symbol alone would mark all of them gone
    together — or, worse, none."""
    out = build(
        _log(tmp_path, _access(interval="1d"), _access(interval="1m"))
    )["windows"]
    assert {w["interval"] for w in out} == {"1d", "1m"}


def test_records_that_are_not_holdout_accesses_are_ignored(tmp_path):
    out = build(
        _log(
            tmp_path,
            {"record_type": "backtest_run", "strategy_id": "x"},
            _access(),
        )
    )["windows"]
    assert len(out) == 1


def test_an_access_without_a_window_raises(tmp_path):
    """A holdout access that cannot be attributed to a window would
    otherwise vanish, and a vanished access reads as an unspent window."""
    bad = _access()
    del bad["symbol"]
    with pytest.raises(ValueError, match="without symbol/interval"):
        build(_log(tmp_path, bad))


def test_a_corrupt_middle_line_raises(tmp_path):
    """`read_records`' contract: only a truncated FINAL line is
    tolerable. Anything earlier is real corruption, and the record it held
    might have been the access that marks a window spent."""
    p = tmp_path / "experiments.jsonl"
    p.write_text(
        json.dumps(_access()) + "\n{ not json\n" + json.dumps(_access()) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(json.JSONDecodeError):
        build(p)


def test_a_truncated_final_line_is_tolerated(tmp_path):
    """An append-only log can end mid-write, and that is not corruption."""
    p = tmp_path / "experiments.jsonl"
    p.write_text(json.dumps(_access()) + '\n{"record_type": "hold', encoding="utf-8")
    assert len(build(p)["windows"]) == 1


def test_a_missing_log_yields_no_windows_rather_than_raising(tmp_path):
    """Distinct from a missing *ledger*: before the first run ever happens
    an absent log is an unremarkable state, and `read_records` says so."""
    assert build(tmp_path / "nope.jsonl")["windows"] == []


def test_the_ledger_says_it_is_derived(tmp_path):
    """A hand-edited ledger silently decides which windows look available,
    so the file has to say what it is on its face."""
    note = build(_log(tmp_path, _access()))["note"]
    assert "Do not hand-edit" in note and "spent_windows" in note


# ----------------------------------------------------------- load()


def test_a_missing_ledger_raises_rather_than_returning_empty(tmp_path):
    """**The whole point of the module.** An empty result would make every
    check built on it pass vacuously — which is exactly how the first
    version of this guard behaved in CI, where the log does not exist."""
    with pytest.raises(FileNotFoundError, match="committed artifact"):
        load(tmp_path / "nope.json")


def test_an_empty_ledger_raises_too(tmp_path):
    p = tmp_path / "spent_windows.json"
    p.write_text(json.dumps({"windows": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="lists no spent windows"):
        load(p)


def test_load_returns_the_committed_rows(tmp_path):
    p = tmp_path / "spent_windows.json"
    p.write_text(
        json.dumps({"windows": [{"symbol": "BTC-USDT", "interval": "1d"}]}),
        encoding="utf-8",
    )
    assert load(p) == [{"symbol": "BTC-USDT", "interval": "1d"}]


def test_the_rows_are_sorted_so_list_equality_is_a_stable_contract(tmp_path):
    """`test_the_spent_window_ledger_matches_the_log` compares whole rows
    by list equality, which only means anything if the order is fixed."""
    out = build(
        _log(
            tmp_path,
            _access(symbol="ZZZ", interval="1d"),
            _access(symbol="AAA", interval="1m"),
            _access(symbol="AAA", interval="1d"),
        )
    )["windows"]
    assert [(w["symbol"], w["interval"]) for w in out] == [
        ("AAA", "1d"),
        ("AAA", "1m"),
        ("ZZZ", "1d"),
    ]


def test_an_extra_access_changes_the_row_not_just_the_window_set(tmp_path):
    """The reason the ledger check compares whole rows. A fourth access to
    an already-spent window leaves `(symbol, interval)` identical, so an
    identity-only comparison would call a stale ledger current — and
    CLAUDE.md cites that count as evidence the single-access discipline
    held."""
    three = build(_log(tmp_path, _access(), _access(), _access()))["windows"]
    four = build(_log(tmp_path, _access(), _access(), _access(), _access()))["windows"]
    assert {(w["symbol"], w["interval"]) for w in three} == {
        (w["symbol"], w["interval"]) for w in four
    }
    assert three != four
    assert (three[0]["accesses"], four[0]["accesses"]) == (3, 4)


def test_the_real_ledger_loads_and_names_the_krx_window():
    """The committed artifact itself, not a fixture — the row whose
    absence from CLAUDE.md was the finding this module was built for."""
    rows = load()
    assert any(r["symbol"].startswith("KRX:") and r["interval"] == "1d" for r in rows)


# ------------------------------------------- the write path cannot un-spend


def test_regenerating_from_an_absent_log_refuses_to_shrink_the_ledger(tmp_path):
    """**The defect this was written for, reproduced.** `DEFAULT_RUNS_PATH` is
    the relative `runs/experiments.jsonl` — deliberately, so `conftest.py` can
    redirect it and keep tests out of the real log — while the ledger path is
    absolute. Running the documented regeneration command from `python/` rather
    than the repository root therefore read nothing and **overwrote the
    committed ledger with an empty one**. `load` refuses an empty ledger; nothing
    refused writing one.
    """
    from research.spent_windows import main

    ledger = tmp_path / "spent_windows.json"
    ledger.write_text(
        json.dumps({"windows": [{"symbol": "KRX:005930", "interval": "1d"}]}),
        encoding="utf-8",
    )
    before = ledger.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="would erase spends"):
        main([
            "--write",
            "--runs-path", str(tmp_path / "absent.jsonl"),
            "--ledger-path", str(ledger),
        ])

    assert ledger.read_text(encoding="utf-8") == before, (
        "the ledger changed even though the write was refused"
    )


def test_a_ledger_that_does_not_shrink_is_written(tmp_path):
    """The guard must not block the ordinary case, or it gets removed."""
    from research.spent_windows import main

    ledger = tmp_path / "spent_windows.json"
    ledger.write_text(json.dumps({"windows": []}), encoding="utf-8")
    log = _log(tmp_path, _access())

    assert main(["--write", "--runs-path", str(log), "--ledger-path", str(ledger)]) == 0
    assert json.loads(ledger.read_text(encoding="utf-8"))["windows"], (
        "a legitimate regeneration wrote nothing"
    )


def test_an_unreadable_existing_ledger_refuses_rather_than_overwriting(tmp_path):
    """An unreadable ledger is not evidence that nothing is spent."""
    from research.spent_windows import main

    ledger = tmp_path / "spent_windows.json"
    ledger.write_text("{ not json", encoding="utf-8")
    log = _log(tmp_path, _access())

    with pytest.raises(ValueError, match="could not be read"):
        main(["--write", "--runs-path", str(log), "--ledger-path", str(ledger)])


def test_every_span_names_the_era_its_accesses_covered(tmp_path):
    """`span` is what lets one symbol hold a spent era and a reserved one.

    `sr-t` reserved the EARLY 1d window while the later one was spent, and KRX
    daily is now the same shape. A ledger keyed on (symbol, interval) alone
    reports the whole symbol spent and makes the reserved era unnameable.
    """
    from research.spent_windows import build

    log = _log(tmp_path, _access())
    row = build(log)["windows"][0]
    assert "span" in row, "the ledger no longer records which era was spent"
    if row["span"] is not None:
        a, _, b = row["span"].partition("..")
        assert a and b and a <= b, f"malformed span {row['span']!r}"


def test_a_regeneration_that_swaps_one_window_for_another_is_refused(tmp_path):
    """**Counting rows was not enough.** Dropping one window while adding another
    leaves the count equal, so the count-based guard passed it and the dropped
    spend was erased. Reported on review of PR #210."""
    from research.spent_windows import main

    ledger = tmp_path / "spent_windows.json"
    ledger.write_text(
        json.dumps({"windows": [
            {"symbol": "KRX:005930", "interval": "1d", "span": "2019-01-02..2026-09-02"},
        ]}),
        encoding="utf-8",
    )
    log = _log(tmp_path, _access(symbol="BTC-USDT", interval="1m"))

    with pytest.raises(ValueError, match="would erase spends"):
        main(["--write", "--runs-path", str(log), "--ledger-path", str(ledger)])


def test_a_regeneration_that_NARROWS_an_era_is_refused(tmp_path):
    """The other half the count could not see: the same window, the same row
    count, a smaller span. The era outside the new span would read as available."""
    from research.spent_windows import main

    ledger = tmp_path / "spent_windows.json"
    ledger.write_text(
        json.dumps({"windows": [
            {"symbol": "KRX:005930", "interval": "1d", "span": "2010-01-02..2026-09-02"},
        ]}),
        encoding="utf-8",
    )
    log = _log(tmp_path, _ranged(2019, 1, 2, 2026, 9, 2))

    with pytest.raises(ValueError, match="would erase spends"):
        main(["--write", "--runs-path", str(log), "--ledger-path", str(ledger)])


def test_replacing_an_UNKNOWN_era_with_a_finite_one_is_refused(tmp_path):
    """`span: null` is the widest claim there is — the era was unknown, so every
    era counts as spent. A finite replacement narrows it."""
    from research.spent_windows import main

    ledger = tmp_path / "spent_windows.json"
    ledger.write_text(
        json.dumps({"windows": [
            {"symbol": "KRX:005930", "interval": "1d", "span": None},
        ]}),
        encoding="utf-8",
    )
    log = _log(tmp_path, _ranged(2019, 1, 2, 2026, 9, 2))

    with pytest.raises(ValueError, match="would erase spends"):
        main(["--write", "--runs-path", str(log), "--ledger-path", str(ledger)])


def test_ONE_access_without_a_range_makes_the_whole_window_unknown(tmp_path):
    """**The conservative direction, and the first version got it backwards.**
    Taking the bounds of only the records that carry a range reported a finite
    span covering less than was actually accessed, so an era outside it read as
    unspent — a reserved window that is not. Reported on review of PR #210."""
    from research.spent_windows import build

    rows = build(
        _log(tmp_path, _ranged(2019, 1, 2, 2026, 9, 2),
             _access(symbol="KRX:005930", interval="1d"))
    )["windows"]
    assert len(rows) == 1, rows
    assert rows[0]["span"] is None, (
        f"span {rows[0]['span']!r} claims a finite era while one access had no "
        f"range at all, so some era reads as available that was accessed"
    )
