"""Tests for `python/research/run_preregistered_holdout.py` -- the
dedicated, single-purpose runner for a `data.split == "holdout"`
pre-registration. Strategy Research Task V.

**Synthetic fixtures only, per this task's own inviolable rule.** No test
here reads `runs/experiments.jsonl`, the real kline cache
(`python/data/var/klines.sqlite3`), or the real committed
`daily-tsmom-ensemble-1d-holdout.json` registration against real data --
every kline/holdout-config/experiment-log path is a `tmp_path` fixture. The
one, real, once-only execution against real 1d holdout data happens
separately, driven by hand (see `.planning/sr-v-preregistered-attempt-
result.md`), not by this test suite.
"""

import json
import math
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from backtest.kline import Kline
from data.bingx_klines import KlineRow
from data.store import connect, upsert_klines
from metrics.metrics import Metrics
from research.eligibility import PsrResult
from research.experiment_log import log_holdout_access, read_records
from research.holdout import HoldoutAlreadyClaimedError
from research.preregistration import SPLIT_RESEARCH, frequency_scaled_min_trades, load_preregistration
from research.run_preregistered_holdout import (
    OUTCOME_FAIL,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_PASS,
    evaluate_gating,
    main,
    recompute_detection_floor_sharpe,
    run_preregistered_holdout,
    verify_detection_floor,
    verify_trade_floor,
)
from schemas.order_intent import OrderIntent, OrderType, Side

STEP_MS = 86_400_000  # 1d
START_MS = (1_700_000_000_000 // STEP_MS) * STEP_MS
NUM_BARS = 60
END_MS = START_MS + NUM_BARS * STEP_MS

PREREG_ID = "sr-v-holdout-runner-demo"
STRATEGY_ID = "sr-v-holdout-runner-demo"


def _config(**overrides) -> dict:
    config = {
        "preregistration_id": PREREG_ID,
        "registered_at": "2026-07-30T00:00:00Z",
        "strategy_family": "sr-v-holdout-runner-demo",
        "strategy_id": STRATEGY_ID,
        "strategy_version": "v1",
        "strategy_entry_point": "research.strategies.ma_crossover:MACrossoverTrainable",
        "hypothesis": "Synthetic fixture: the dedicated holdout runner executes a registered strategy "
        "against a real (fixture) holdout window exactly once.",
        "prior_art": ["None -- synthetic runner fixture."],
        "data": {
            "symbol": "BTC-USDT",
            "interval": "1d",
            "source": "synthetic tmp_path sqlite cache",
            "split": "holdout",
            "holdout_config_path": "PLACEHOLDER",
            "start_ms": START_MS,
            "end_ms": END_MS,
            "expected_bars": NUM_BARS,
        },
        "parameter_grid": [{"fast": 2, "slow": 5}],
        "total_candidates": 1,
        "free_parameter_count": 0,
        "procedure": {
            "fee_bps": "5",
            "slippage_bps": "2",
            "bars_per_day": 1,
            "funding_included": False,
        },
        "primary_criterion": {
            "kind": "holdout_psr",
            "threshold": 0.95,
            "max_drawdown_ceiling": "0.20",
            "min_total_trades": frequency_scaled_min_trades(total_evaluated_bars=NUM_BARS, bars_per_day=1),
            "profit_factor_floor": "1.3",
            "require_sharpe_above_detection_floor": True,
            "criteria_pinned_at_claude_md_revision": "2026-07-29",
        },
        "secondary_reported_not_gating": ["synthetic fixture -- secondary figures not exercised here"],
        "declared_detection_floor_sharpe": recompute_detection_floor_sharpe(
            total_evaluated_bars=NUM_BARS, bars_per_day=1
        ),
        "declared_power": {
            "assumed_true_sharpe": 1.0,
            "probability": 0.5,
            "derivation": "Synthetic fixture, not a real power calculation.",
        },
        "outcome_interpretation": {
            "PASS": "Synthetic PASS text.",
            "INCONCLUSIVE": "Synthetic INCONCLUSIVE text.",
            "FAIL": "Synthetic FAIL text.",
        },
        "stopping_rule": "Exactly one execution against the fixture holdout window.",
    }
    config.update(overrides)
    return config


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "klines.sqlite3"
    conn = connect(path)
    upsert_klines(
        conn,
        "BTC-USDT",
        "1d",
        [
            KlineRow(
                open_time_ms=START_MS + i * STEP_MS,
                open=Decimal(100 + i),
                high=Decimal(102 + i),
                low=Decimal(99 + i),
                # A zig-zag close so a moving-average crossover actually
                # fires (real trades) rather than trending monotonically.
                close=Decimal(100 + i) + (Decimal("5") if i % 6 < 3 else Decimal("-5")),
                volume=Decimal("1"),
            )
            for i in range(NUM_BARS)
        ],
    )
    conn.close()
    return path


@pytest.fixture
def holdout_config_path(tmp_path):
    path = tmp_path / "holdout_synthetic_1d.json"
    path.write_text(
        json.dumps(
            {
                "symbol": "BTC-USDT",
                "interval": "1d",
                "holdout_side": "before",
                # Everything in [START_MS, END_MS) is the holdout side.
                "holdout_cutoff_ms": END_MS,
                "set_on": "2026-07-30",
                "rationale": "synthetic test fixture -- before-side holdout",
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def runs_path(tmp_path):
    return tmp_path / "experiments.jsonl"


def _write_prereg(tmp_path: Path, holdout_config_path: Path, **overrides) -> Path:
    config = _config(**overrides)
    config["data"] = {**config["data"], "holdout_config_path": str(holdout_config_path)}
    path = tmp_path / f"{config['preregistration_id']}.json"
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


def _klines(count: int) -> list[Kline]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        Kline(
            open_time=base + timedelta(days=i),
            open=Decimal(100 + i),
            high=Decimal(102 + i),
            low=Decimal(99 + i),
            close=Decimal(100 + i),
            volume=Decimal("1"),
        )
        for i in range(count)
    ]


class _RecordingTrainable:
    """A `TrainableStrategy` double that trades once per bar (after the
    first), so fold metrics are always well-defined -- same pattern
    `test_run_preregistered.py` uses for the research-split runner.
    """

    def __init__(self, *, side=Side.LONG):
        self.params_seen: list[dict] = []
        self._side = side

    def fit(self, train_klines, params, *, parent_run_id):
        self.params_seen.append(dict(params))

        state = {"opened": False}

        def _strategy(visible_klines):
            if state["opened"]:
                return None
            if len(visible_klines) < 2:
                return None
            state["opened"] = True
            return OrderIntent(
                intent_id=uuid4(),
                symbol="BTC-USDT",
                side=self._side,
                order_type=OrderType.GUARDED_MARKET,
                quantity=Decimal("1"),
                limit_price=None,
                signal_timeframe="1d",
                created_at=visible_klines[-1].open_time,
            )

        return _strategy


class _NeverTradesTrainable:
    def fit(self, train_klines, params, *, parent_run_id):
        return lambda visible_klines: None


def _backtest_records(runs_path: Path) -> list[dict]:
    return [r for r in read_records(runs_path) if r.get("record_type") == "backtest_run"]


def _holdout_access_records(runs_path: Path) -> list[dict]:
    return [r for r in read_records(runs_path) if r.get("record_type") == "holdout_access"]


# ---------------------------------------------------------------------------
# Refuses a research-split registration
# ---------------------------------------------------------------------------


def test_refuses_a_research_split_registration(tmp_path, holdout_config_path, runs_path):
    path = _write_prereg(
        tmp_path,
        holdout_config_path,
        data={
            **_config()["data"],
            "split": SPLIT_RESEARCH,
            "holdout_config_path": str(holdout_config_path),
        },
        procedure={
            "fee_bps": "5",
            "slippage_bps": "2",
            "bars_per_day": 1,
            "funding_included": False,
            "train_bars": 30,
            "validate_bars": 10,
            "step_bars": 10,
        },
        primary_criterion={
            "kind": "walk_forward_dsr",
            "threshold": 0.95,
            "min_fold_consistency": "0.80",
            "sign_test_alpha": 0.05,
            "max_drawdown_ceiling": "0.20",
            "min_total_trades": 30,
            "profit_factor_floor": "1.3",
            "criteria_pinned_at_claude_md_revision": "2026-07-29",
        },
    )
    prereg = load_preregistration(path)

    with pytest.raises(ValueError, match=re.escape("research.run_preregistered")):
        run_preregistered_holdout(
            prereg, strategy=_RecordingTrainable(), klines=_klines(NUM_BARS), runs_path=runs_path
        )

    assert _backtest_records(runs_path) == []


# ---------------------------------------------------------------------------
# The single-access holdout claim -- refuses a second attempt for the same
# strategy_id, without ever passing force_reclaim_reason itself.
# ---------------------------------------------------------------------------


def test_refuses_when_the_strategy_id_already_claimed_the_holdout(
    tmp_path, holdout_config_path, db_path, runs_path
):
    # Pre-seed an existing holdout_access record for this exact strategy_id
    # -- simulating "this holdout was already spent by a prior real run" --
    # via the real log_holdout_access mechanism (a mocked/fixture claim, per
    # the task's own TDD requirement), not by hand-writing JSON.
    log_holdout_access(
        strategy_id=STRATEGY_ID,
        symbol="BTC-USDT",
        interval="1d",
        start_ms=START_MS,
        end_ms=END_MS,
        runs_path=runs_path,
    )
    prereg = load_preregistration(_write_prereg(tmp_path, holdout_config_path))

    with pytest.raises(HoldoutAlreadyClaimedError):
        run_preregistered_holdout(prereg, strategy=_RecordingTrainable(), db_path=db_path, runs_path=runs_path)

    # No outer confirmation record was logged -- a refused claim must not
    # leave a run behind.
    assert _backtest_records(runs_path) == []
    # And the claim was never bypassed: still exactly the one pre-seeded
    # holdout_access record, not two.
    assert len(_holdout_access_records(runs_path)) == 1


def test_a_second_real_call_for_the_same_strategy_id_is_refused_by_the_first_calls_own_claim(
    tmp_path, holdout_config_path, db_path, runs_path
):
    """End-to-end version of the claim check: the FIRST real call through
    `run_preregistered_holdout` (not a hand-seeded record) succeeds and logs
    its own `holdout_access`; a second call for the same `strategy_id`
    against the same `runs_path` then raises, because `load_holdout_klines`
    scans the very log the first call just wrote to.
    """
    prereg = load_preregistration(_write_prereg(tmp_path, holdout_config_path))

    first = run_preregistered_holdout(
        prereg, strategy=_RecordingTrainable(), db_path=db_path, runs_path=runs_path
    )
    assert first.klines_count == NUM_BARS
    assert len(_holdout_access_records(runs_path)) == 1

    with pytest.raises(HoldoutAlreadyClaimedError):
        run_preregistered_holdout(prereg, strategy=_RecordingTrainable(), db_path=db_path, runs_path=runs_path)

    # Still exactly one outer confirmation record -- the refused second call
    # left nothing behind.
    assert len(_backtest_records(runs_path)) >= 1
    parents = [r for r in _backtest_records(runs_path) if r["is_holdout_run"] is True]
    assert len(parents) == 1
    assert len(_holdout_access_records(runs_path)) == 1


# ---------------------------------------------------------------------------
# The round trip: a real (fixture) execution logs everything it should
# ---------------------------------------------------------------------------


def test_the_runner_drives_a_holdout_confirmation_end_to_end_and_logs_its_provenance(
    tmp_path, holdout_config_path, db_path, runs_path
):
    prereg = load_preregistration(_write_prereg(tmp_path, holdout_config_path))

    result = run_preregistered_holdout(
        prereg, strategy=_RecordingTrainable(), db_path=db_path, runs_path=runs_path
    )

    assert result.strategy_id == STRATEGY_ID
    assert result.klines_count == NUM_BARS
    assert result.outcome in ("PASS", "INCONCLUSIVE", "FAIL")

    record = next(r for r in _backtest_records(runs_path) if r["run_id"] == result.run_id)
    assert record["is_holdout_run"] is True
    assert record["preregistration_id"] == PREREG_ID
    assert record["preregistration_sha256"] == prereg.sha256
    assert record["strategy_family"] == "sr-v-holdout-runner-demo"
    assert record["total_candidates"] == 1
    assert record["aggregate_metrics"]["outcome_region"] == result.outcome
    assert record["aggregate_metrics"]["psr"]["psr"] == result.psr_result.psr
    assert record["walk_forward_config"]["bars_per_day"] == 1

    # The claim itself: exactly one holdout_access record, for this
    # strategy_id, spanning exactly the requested range.
    accesses = _holdout_access_records(runs_path)
    assert len(accesses) == 1
    assert accesses[0]["strategy_id"] == STRATEGY_ID
    assert accesses[0]["start_ms"] == START_MS
    assert accesses[0]["end_ms"] == END_MS
    assert accesses[0]["force_reclaim_reason"] is None


def test_the_strategys_own_fit_diagnostic_record_is_not_the_holdout_confirmation_record(
    tmp_path, holdout_config_path, db_path, runs_path
):
    """`MACrossoverTrainable.fit()` logs its own in-sample-scoring child
    record with `is_holdout_run=False` and no preregistration provenance
    (matching every sibling Trainable) -- this test proves that sub-record
    is distinguishable from, and does not replace, this module's own outer
    `is_holdout_run=True` record.
    """
    prereg = load_preregistration(_write_prereg(tmp_path, holdout_config_path))

    result = run_preregistered_holdout(prereg, db_path=db_path, runs_path=runs_path)

    records = _backtest_records(runs_path)
    outer = [r for r in records if r["run_id"] == result.run_id]
    assert len(outer) == 1
    assert outer[0]["is_holdout_run"] is True

    children = [r for r in records if r.get("parent_run_id") == result.run_id]
    assert len(children) >= 1
    assert all(child["is_holdout_run"] is False for child in children)
    assert all("preregistration_id" not in child for child in children)


def test_a_bar_count_other_than_the_registered_one_fails_closed(tmp_path, holdout_config_path, db_path, runs_path):
    # Unlike research.run_preregistered's identical-looking research-split
    # check (which only warns), the dedicated holdout runner fails closed:
    # this is an exactly-once, never-re-run confirmation, and continuing to
    # gate against a detection floor/trade-count floor computed from a bar
    # count that no longer matches the actually-loaded window would be
    # silently comparing against thresholds that no longer describe the
    # evaluated data. (CodeRabbit review finding on this PR.)
    prereg = load_preregistration(
        _write_prereg(tmp_path, holdout_config_path, data={**_config()["data"], "expected_bars": NUM_BARS + 5})
    )
    with pytest.raises(ValueError, match="expected_bars"):
        run_preregistered_holdout(prereg, strategy=_RecordingTrainable(), db_path=db_path, runs_path=runs_path)

    # The load itself succeeded (real data was legitimately read before the
    # bar-count check fires), so the single-access claim is correctly spent
    # even though the overall run raised afterward -- a re-run needs a
    # deliberate force_reclaim_reason, same as any other legitimate holdout
    # re-access.
    assert len(_holdout_access_records(runs_path)) == 1
    # But no outer confirmation record was logged: the run never reached
    # the point where it would compute or log a result.
    assert _backtest_records(runs_path) == []


def test_injecting_klines_directly_warns_that_the_holdout_claim_was_skipped(
    tmp_path, holdout_config_path, runs_path, caplog
):
    # The klines= injection path exists for tests only (mirrors research
    # .run_preregistered's identical parameter) -- it never calls
    # load_holdout_klines, so no holdout_access claim is recorded. Loud by
    # design, in case this is ever reached on a real path by mistake.
    # (CodeRabbit review finding on this PR.)
    prereg = load_preregistration(_write_prereg(tmp_path, holdout_config_path))

    with caplog.at_level("WARNING"):
        run_preregistered_holdout(
            prereg, strategy=_RecordingTrainable(), klines=_klines(NUM_BARS), runs_path=runs_path
        )

    assert any("were injected by the caller" in record.message for record in caplog.records)
    assert _holdout_access_records(runs_path) == []


def test_a_never_trading_strategy_lands_in_the_fail_region_end_to_end(
    tmp_path, holdout_config_path, db_path, runs_path
):
    # Drives the REAL backtest -> compute_metrics -> psr_from_equity_curve ->
    # evaluate_gating pipeline through a genuine zero-trade result (not a
    # hand-constructed Metrics/PsrResult fixture, which the evaluate_gating
    # unit tests below already cover), confirming the degenerate case lands
    # in FAIL via the real pipeline too. (CodeRabbit review suggestion.)
    prereg = load_preregistration(_write_prereg(tmp_path, holdout_config_path))

    result = run_preregistered_holdout(
        prereg, strategy=_NeverTradesTrainable(), db_path=db_path, runs_path=runs_path
    )

    assert result.metrics.num_trades == 0
    assert result.outcome == OUTCOME_FAIL
    assert result.gating_checks["min_total_trades"].passed is False
    assert result.gating_checks["profit_factor"].passed is False


# ---------------------------------------------------------------------------
# verify_trade_floor / verify_detection_floor -- the independent
# recomputations the task brief specifically asks to be unit tested.
# ---------------------------------------------------------------------------


def test_verify_trade_floor_matches_for_a_correctly_registered_floor(tmp_path, holdout_config_path):
    prereg = load_preregistration(_write_prereg(tmp_path, holdout_config_path))
    assert verify_trade_floor(prereg) is True


def test_verify_trade_floor_flags_a_registration_stricter_than_the_floor(tmp_path, holdout_config_path, caplog):
    # A registered value strictly above the recomputed floor is VALID per
    # research.preregistration (stricter is always accepted), but this
    # function's own job is to flag any disagreement, loudly, never raise.
    stricter_criterion = {**_config()["primary_criterion"], "min_total_trades": NUM_BARS}  # far above the real floor
    prereg = load_preregistration(
        _write_prereg(tmp_path, holdout_config_path, primary_criterion=stricter_criterion)
    )
    with caplog.at_level("WARNING"):
        matched = verify_trade_floor(prereg)
    assert matched is False
    assert any("min_total_trades" in record.message for record in caplog.records)


def test_verify_trade_floor_reproduces_the_registered_1d_holdout_floor_of_53():
    # Cross-check against CLAUDE.md's own published number for the real
    # registered 1d holdout geometry (1,079 bars, bars_per_day=1) -- this
    # does not load any file, just exercises the same arithmetic the real
    # registration was built from.
    assert frequency_scaled_min_trades(total_evaluated_bars=1079, bars_per_day=1) == 53


def test_verify_detection_floor_matches_for_a_correctly_registered_floor(tmp_path, holdout_config_path):
    prereg = load_preregistration(_write_prereg(tmp_path, holdout_config_path))
    assert verify_detection_floor(prereg) is True


def test_verify_detection_floor_flags_a_mismatched_value(tmp_path, holdout_config_path, caplog):
    prereg = load_preregistration(
        _write_prereg(tmp_path, holdout_config_path, declared_detection_floor_sharpe=99.0)
    )
    with caplog.at_level("WARNING"):
        matched = verify_detection_floor(prereg)
    assert matched is False
    assert any("declared_detection_floor_sharpe" in record.message for record in caplog.records)


def test_recompute_detection_floor_sharpe_reproduces_the_registered_1d_holdout_value():
    # Cross-check against CLAUDE.md's/the real registration's own published
    # figure: 0.9567 for 1,079 bars at bars_per_day=1.
    value = recompute_detection_floor_sharpe(total_evaluated_bars=1079, bars_per_day=1)
    assert math.isclose(value, 0.9567, abs_tol=1e-3)


# ---------------------------------------------------------------------------
# evaluate_gating -- the PASS/INCONCLUSIVE/FAIL determination logic
# ---------------------------------------------------------------------------


def _metrics(
    *, sharpe=2.0, max_drawdown=Decimal("0.05"), num_trades=60, profit_factor=1.5
) -> Metrics:
    return Metrics(
        starting_equity=Decimal("10000"),
        final_equity=Decimal("11000"),
        equity_curve=[Decimal("10000"), Decimal("11000")],
        closed_trades=[],
        total_return=Decimal("0.1"),
        sharpe_ratio=sharpe,
        max_drawdown=max_drawdown,
        win_rate=0.6,
        num_trades=num_trades,
        profit_factor=profit_factor,
        return_skewness=0.0,
        return_kurtosis=3.0,
        num_returns=100,
    )


def _psr(value) -> PsrResult:
    return PsrResult(
        psr=value,
        sharpe_ratio=0.05,
        benchmark_sharpe=0.0,
        num_observations=100,
        skewness=0.0,
        kurtosis=3.0,
        moments_source="observed",
        z_score=1.0 if value is not None else None,
        sampling="daily",
    )


def _gating_prereg(tmp_path, holdout_config_path):
    return load_preregistration(_write_prereg(tmp_path, holdout_config_path))


def test_evaluate_gating_passes_when_every_check_clears(tmp_path, holdout_config_path):
    prereg = _gating_prereg(tmp_path, holdout_config_path)
    outcome, checks = evaluate_gating(
        prereg, psr_result=_psr(0.99), metrics=_metrics(), declared_detection_floor=1.0
    )
    assert outcome == OUTCOME_PASS
    assert all(check.passed for check in checks.values())


def test_evaluate_gating_is_inconclusive_when_psr_is_positive_but_below_threshold(
    tmp_path, holdout_config_path
):
    prereg = _gating_prereg(tmp_path, holdout_config_path)
    outcome, checks = evaluate_gating(
        prereg, psr_result=_psr(0.5), metrics=_metrics(), declared_detection_floor=1.0
    )
    assert outcome == OUTCOME_INCONCLUSIVE
    assert checks["psr"].passed is False


def test_evaluate_gating_is_inconclusive_when_sharpe_does_not_exceed_the_detection_floor(
    tmp_path, holdout_config_path
):
    prereg = _gating_prereg(tmp_path, holdout_config_path)
    outcome, checks = evaluate_gating(
        prereg, psr_result=_psr(0.99), metrics=_metrics(sharpe=0.5), declared_detection_floor=1.0
    )
    assert outcome == OUTCOME_INCONCLUSIVE
    assert checks["sharpe_above_detection_floor"].passed is False


def test_evaluate_gating_is_inconclusive_when_trades_fall_below_the_floor_even_with_a_high_psr(
    tmp_path, holdout_config_path
):
    prereg = _gating_prereg(tmp_path, holdout_config_path)
    outcome, checks = evaluate_gating(
        prereg, psr_result=_psr(0.99), metrics=_metrics(num_trades=1), declared_detection_floor=1.0
    )
    assert outcome == OUTCOME_INCONCLUSIVE
    assert checks["min_total_trades"].passed is False


def test_evaluate_gating_fails_when_psr_is_degenerate_none(tmp_path, holdout_config_path):
    prereg = _gating_prereg(tmp_path, holdout_config_path)
    outcome, checks = evaluate_gating(
        prereg, psr_result=_psr(None), metrics=_metrics(num_trades=0, profit_factor=None), declared_detection_floor=1.0
    )
    assert outcome == OUTCOME_FAIL
    assert checks["psr"].passed is False


def test_evaluate_gating_fails_when_psr_is_non_positive(tmp_path, holdout_config_path):
    prereg = _gating_prereg(tmp_path, holdout_config_path)
    outcome, _checks = evaluate_gating(
        prereg, psr_result=_psr(-0.1), metrics=_metrics(), declared_detection_floor=1.0
    )
    assert outcome == OUTCOME_FAIL


def test_evaluate_gating_treats_zero_losing_trades_as_trivially_clearing_the_profit_factor_floor(
    tmp_path, holdout_config_path
):
    prereg = _gating_prereg(tmp_path, holdout_config_path)
    outcome, checks = evaluate_gating(
        prereg,
        psr_result=_psr(0.99),
        metrics=_metrics(profit_factor=None, num_trades=60),
        declared_detection_floor=1.0,
    )
    assert checks["profit_factor"].passed is True
    assert outcome == OUTCOME_PASS


def test_evaluate_gating_treats_a_zero_trade_none_profit_factor_as_not_passing(
    tmp_path, holdout_config_path
):
    prereg = _gating_prereg(tmp_path, holdout_config_path)
    outcome, checks = evaluate_gating(
        prereg,
        psr_result=_psr(None),
        metrics=_metrics(profit_factor=None, num_trades=0, sharpe=None),
        declared_detection_floor=1.0,
    )
    assert checks["profit_factor"].passed is False
    assert outcome == OUTCOME_FAIL


# ---------------------------------------------------------------------------
# The strategy never receives a force_reclaim_reason on its own initiative
# ---------------------------------------------------------------------------


def test_run_preregistered_holdout_never_supplies_a_force_reclaim_reason_by_default(
    tmp_path, holdout_config_path, db_path, runs_path
):
    log_holdout_access(
        strategy_id=STRATEGY_ID, symbol="BTC-USDT", interval="1d", start_ms=START_MS, end_ms=END_MS, runs_path=runs_path
    )
    prereg = load_preregistration(_write_prereg(tmp_path, holdout_config_path))

    # Confirms the exception's own message names the strategy_id -- i.e.
    # this really is HoldoutAlreadyClaimedError's real message, not a
    # bypass silently succeeding.
    with pytest.raises(HoldoutAlreadyClaimedError, match=STRATEGY_ID):
        run_preregistered_holdout(prereg, db_path=db_path, runs_path=runs_path)


def test_an_explicit_force_reclaim_reason_is_honored_when_the_caller_supplies_one(
    tmp_path, holdout_config_path, db_path, runs_path
):
    log_holdout_access(
        strategy_id=STRATEGY_ID, symbol="BTC-USDT", interval="1d", start_ms=START_MS, end_ms=END_MS, runs_path=runs_path
    )
    prereg = load_preregistration(_write_prereg(tmp_path, holdout_config_path))

    result = run_preregistered_holdout(
        prereg,
        strategy=_RecordingTrainable(),
        db_path=db_path,
        runs_path=runs_path,
        force_reclaim_reason="deliberate test of the override path itself",
    )
    assert result.klines_count == NUM_BARS
    accesses = _holdout_access_records(runs_path)
    assert len(accesses) == 2
    assert accesses[-1]["force_reclaim_reason"] == "deliberate test of the override path itself"


# ---------------------------------------------------------------------------
# The CLI -- argument handling
# ---------------------------------------------------------------------------


def test_the_cli_runs_a_registration_against_the_injected_paths(
    tmp_path, holdout_config_path, db_path, runs_path, capsys
):
    path = _write_prereg(tmp_path, holdout_config_path)

    exit_code = main([str(path), "--runs-path", str(runs_path), "--db-path", str(db_path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert PREREG_ID in out
    assert "OUTCOME:" in out
    parents = [r for r in _backtest_records(runs_path) if r["is_holdout_run"] is True]
    assert len(parents) == 1


def test_the_cli_forwards_an_explicit_force_reclaim_reason(
    tmp_path, holdout_config_path, db_path, runs_path
):
    log_holdout_access(
        strategy_id=STRATEGY_ID, symbol="BTC-USDT", interval="1d", start_ms=START_MS, end_ms=END_MS, runs_path=runs_path
    )
    path = _write_prereg(tmp_path, holdout_config_path)

    exit_code = main(
        [
            str(path),
            "--runs-path",
            str(runs_path),
            "--db-path",
            str(db_path),
            "--force-reclaim-reason",
            "cli-driven deliberate reclaim",
        ]
    )
    assert exit_code == 0
    accesses = _holdout_access_records(runs_path)
    assert accesses[-1]["force_reclaim_reason"] == "cli-driven deliberate reclaim"


def test_the_cli_without_force_reclaim_reason_propagates_the_already_claimed_error(
    tmp_path, holdout_config_path, db_path, runs_path
):
    log_holdout_access(
        strategy_id=STRATEGY_ID, symbol="BTC-USDT", interval="1d", start_ms=START_MS, end_ms=END_MS, runs_path=runs_path
    )
    path = _write_prereg(tmp_path, holdout_config_path)

    with pytest.raises(HoldoutAlreadyClaimedError):
        main([str(path), "--runs-path", str(runs_path), "--db-path", str(db_path)])


def test_the_cli_requires_a_preregistration_path_argument():
    with pytest.raises(SystemExit):
        main([])
