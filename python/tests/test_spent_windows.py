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


def test_the_real_ledger_loads_and_names_the_krx_window():
    """The committed artifact itself, not a fixture — the row whose
    absence from CLAUDE.md was the finding this module was built for."""
    rows = load()
    assert any(r["symbol"].startswith("KRX:") and r["interval"] == "1d" for r in rows)
