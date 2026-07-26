"""Tests for `python/research/holdout.py`.

See CLAUDE.md's "Strategy Research Operational Design" section,
"Holdout-split mechanics" subsection, for the full design this module
implements.
"""

import json
from decimal import Decimal

import pytest

from data.bingx_klines import KlineRow
from data.store import connect, upsert_klines
from research.experiment_log import DEFAULT_RUNS_PATH as _UNUSED  # sanity import
from research.holdout import (
    HoldoutAlreadyClaimedError,
    load_holdout_klines,
    load_research_klines,
)

STEP = 900_000
BASE = (1_700_000_000_000 // STEP) * STEP
CUTOFF = BASE + 10 * STEP  # bars 0..9 are "research", 10+ are "holdout"


def _row(offset: int, price: str = "100") -> KlineRow:
    return KlineRow(
        open_time_ms=BASE + offset * STEP,
        open=Decimal(price),
        high=Decimal(price),
        low=Decimal(price),
        close=Decimal(price),
        volume=Decimal("1"),
    )


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "klines.sqlite3"
    conn = connect(path)
    upsert_klines(conn, "BTC-USDT", "15m", [_row(i) for i in range(20)])  # bars 0..19
    conn.close()
    return path


@pytest.fixture
def holdout_config_path(tmp_path):
    path = tmp_path / "holdout.json"
    path.write_text(
        json.dumps(
            {
                "symbol": "BTC-USDT",
                "interval": "15m",
                "holdout_cutoff_ms": CUTOFF,
                "set_on": "2026-07-26",
                "rationale": "test fixture cutoff",
            }
        )
    )
    return path


@pytest.fixture
def runs_path(tmp_path):
    return tmp_path / "experiments.jsonl"


# ---------------------------------------------------------------------------
# load_research_klines
# ---------------------------------------------------------------------------


def test_load_research_klines_returns_klines_within_the_requested_range(db_path, holdout_config_path):
    klines = load_research_klines(BASE, BASE + 5 * STEP, db_path=db_path, holdout_config_path=holdout_config_path)

    assert len(klines) == 5


def test_load_research_klines_clamps_end_ms_to_the_holdout_cutoff(db_path, holdout_config_path):
    # Requesting through bar 19, well past the cutoff at bar 10.
    klines = load_research_klines(
        BASE, BASE + 20 * STEP, db_path=db_path, holdout_config_path=holdout_config_path
    )

    # Only bars 0..9 (i.e. up to but not including CUTOFF) are research data.
    assert len(klines) == 10
    assert klines[-1].open_time.timestamp() * 1000 == CUTOFF - STEP


def test_load_research_klines_logs_a_warning_when_it_actually_clamps(db_path, holdout_config_path, caplog):
    with caplog.at_level("WARNING"):
        load_research_klines(BASE, BASE + 20 * STEP, db_path=db_path, holdout_config_path=holdout_config_path)

    assert any("clamp" in record.message.lower() for record in caplog.records)


def test_load_research_klines_does_not_warn_when_end_ms_is_already_before_cutoff(
    db_path, holdout_config_path, caplog
):
    with caplog.at_level("WARNING"):
        load_research_klines(BASE, BASE + 5 * STEP, db_path=db_path, holdout_config_path=holdout_config_path)

    assert not any("clamp" in record.message.lower() for record in caplog.records)


def test_load_research_klines_never_returns_data_at_or_after_the_cutoff(db_path, holdout_config_path):
    klines = load_research_klines(
        BASE, BASE + 20 * STEP, db_path=db_path, holdout_config_path=holdout_config_path
    )

    assert all(k.open_time.timestamp() * 1000 < CUTOFF for k in klines)


# ---------------------------------------------------------------------------
# load_holdout_klines
# ---------------------------------------------------------------------------


def test_load_holdout_klines_raises_without_the_explicit_keyword(db_path, holdout_config_path, runs_path):
    with pytest.raises(TypeError):
        load_holdout_klines(  # noqa
            CUTOFF,
            BASE + 20 * STEP,
            strategy_id="s1",
            db_path=db_path,
            holdout_config_path=holdout_config_path,
            runs_path=runs_path,
        )


def test_load_holdout_klines_raises_when_keyword_is_explicitly_false(db_path, holdout_config_path, runs_path):
    with pytest.raises(ValueError):
        load_holdout_klines(
            CUTOFF,
            BASE + 20 * STEP,
            strategy_id="s1",
            i_understand_this_is_holdout_data=False,
            db_path=db_path,
            holdout_config_path=holdout_config_path,
            runs_path=runs_path,
        )


def test_load_holdout_klines_returns_only_data_at_or_after_the_cutoff(db_path, holdout_config_path, runs_path):
    klines = load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )

    assert len(klines) == 10  # bars 10..19
    assert all(k.open_time.timestamp() * 1000 >= CUTOFF for k in klines)


def test_load_holdout_klines_rejects_a_start_ms_before_the_cutoff(db_path, holdout_config_path, runs_path):
    with pytest.raises(ValueError):
        load_holdout_klines(
            BASE,  # before CUTOFF
            BASE + 20 * STEP,
            strategy_id="s1",
            i_understand_this_is_holdout_data=True,
            db_path=db_path,
            holdout_config_path=holdout_config_path,
            runs_path=runs_path,
        )


def test_load_holdout_klines_logs_a_holdout_access_record(db_path, holdout_config_path, runs_path):
    load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )

    lines = runs_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["record_type"] == "holdout_access"
    assert record["strategy_id"] == "s1"
    assert record["force_reclaim_reason"] is None


def test_load_holdout_klines_second_call_for_same_strategy_id_raises(db_path, holdout_config_path, runs_path):
    load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )

    with pytest.raises(HoldoutAlreadyClaimedError):
        load_holdout_klines(
            CUTOFF,
            BASE + 20 * STEP,
            strategy_id="s1",
            i_understand_this_is_holdout_data=True,
            db_path=db_path,
            holdout_config_path=holdout_config_path,
            runs_path=runs_path,
        )

    # The rejected second call must not have appended a second record.
    lines = runs_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_load_holdout_klines_different_strategy_ids_do_not_collide(db_path, holdout_config_path, runs_path):
    load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )

    # A different strategy_id must succeed -- the claim is per-strategy_id.
    klines = load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s2",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )
    assert len(klines) == 10


def test_load_holdout_klines_second_call_with_blank_force_reclaim_reason_still_raises(
    db_path, holdout_config_path, runs_path
):
    load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )

    for blank in ("", "   "):
        with pytest.raises(HoldoutAlreadyClaimedError):
            load_holdout_klines(
                CUTOFF,
                BASE + 20 * STEP,
                strategy_id="s1",
                i_understand_this_is_holdout_data=True,
                force_reclaim_reason=blank,
                db_path=db_path,
                holdout_config_path=holdout_config_path,
                runs_path=runs_path,
            )


def test_load_holdout_klines_force_reclaim_reason_allows_a_second_access(
    db_path, holdout_config_path, runs_path
):
    load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )

    klines = load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        force_reclaim_reason="fixed a metrics bug found after the first holdout run, re-confirming",
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )

    assert len(klines) == 10
    lines = runs_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    second_record = json.loads(lines[1])
    assert second_record["force_reclaim_reason"] == (
        "fixed a metrics bug found after the first holdout run, re-confirming"
    )


def test_load_holdout_klines_a_third_access_without_a_new_reason_still_raises(
    db_path, holdout_config_path, runs_path
):
    load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )
    load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        force_reclaim_reason="legitimate re-run after a metrics bugfix",
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )

    with pytest.raises(HoldoutAlreadyClaimedError):
        load_holdout_klines(
            CUTOFF,
            BASE + 20 * STEP,
            strategy_id="s1",
            i_understand_this_is_holdout_data=True,
            db_path=db_path,
            holdout_config_path=holdout_config_path,
            runs_path=runs_path,
        )
