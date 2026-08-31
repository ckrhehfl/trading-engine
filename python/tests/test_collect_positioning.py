"""`collect()` must not report a total failure as a quiet no-op.

CodeRabbit, PR #135. Every request failing and nothing new arriving both
returned 0 and exited cleanly. On endpoints that retain ~30 days and
cannot be backfilled, a month of that silence is permanent loss — which
is the only reason this collector was written before any strategy needed
it.
"""

from __future__ import annotations

import pytest

from data.binance_positioning import BinancePositioningError
from data.collect_positioning import collect, main


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "k.sqlite3")


def test_total_failure_raises(monkeypatch, db):
    def always_fails(*a, **k):
        raise BinancePositioningError("simulated outage")

    monkeypatch.setattr("data.collect_positioning.collect_gap", always_fails)
    with pytest.raises(BinancePositioningError, match="every one of the"):
        collect(db_path=db, symbols=("BTCUSDT",), periods=("1h",))


def test_cli_exits_non_zero_on_total_failure(monkeypatch, db):
    def always_fails(*a, **k):
        raise BinancePositioningError("simulated outage")

    monkeypatch.setattr("data.collect_positioning.collect_gap", always_fails)
    assert main(["--db-path", db, "--symbols", "BTCUSDT", "--periods", "1h"]) == 1


def test_partial_failure_still_succeeds(monkeypatch, db):
    """One bad endpoint must never cost the others their collection
    window — that is the point of continuing past a failure."""
    calls = {"n": 0}

    def sometimes(*a, **k):
        calls["n"] += 1
        if calls["n"] % 2:
            raise BinancePositioningError("simulated flake")
        return []

    monkeypatch.setattr("data.collect_positioning.collect_gap", sometimes)
    assert collect(db_path=db, symbols=("BTCUSDT",), periods=("1h",)) == 0
    assert calls["n"] > 1, "must attempt every series, not stop at the first failure"


def test_a_clean_empty_run_is_not_a_failure(monkeypatch, db):
    """Nothing new is a normal outcome and must stay exit 0, or the
    scheduler learns to ignore this job."""
    monkeypatch.setattr("data.collect_positioning.collect_gap", lambda *a, **k: [])
    assert collect(db_path=db, symbols=("BTCUSDT",), periods=("1h",)) == 0
    assert main(["--db-path", db, "--symbols", "BTCUSDT", "--periods", "1h"]) == 0
