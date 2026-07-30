"""Tests for `python/research/run_preregistered.py` -- the runner that
makes pre-registration real by reading the grid from the committed
registration file rather than from a call site.

Everything here is **synthetic**: a tmp-path registration, a tmp-path
holdout config, a tmp-path sqlite kline cache, a tmp-path experiment log.
No test reads `runs/experiments.jsonl`, the real kline cache, or any
committed registration -- and nothing here touches the 1d holdout window.
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from backtest.kline import Kline
from data.bingx_klines import KlineRow
from data.store import connect, upsert_klines
from metrics.funding import FundingRate
from research.experiment_log import read_records
from research.preregistration import SPLIT_HOLDOUT, load_preregistration
from research.run_preregistered import main, run_preregistered
from schemas.order_intent import OrderIntent, OrderType, Side

STEP_MS = 3_600_000  # 1h
START_MS = (1_700_000_000_000 // STEP_MS) * STEP_MS
NUM_BARS = 100
END_MS = START_MS + NUM_BARS * STEP_MS

PREREG_ID = "sr-s-runner-demo"


def _config(**overrides) -> dict:
    config = {
        "preregistration_id": PREREG_ID,
        "registered_at": "2026-07-30T00:00:00Z",
        "strategy_family": "sr-s-runner-demo",
        "strategy_id": "sr-s-runner-demo",
        "strategy_version": "v1",
        "strategy_entry_point": "research.strategies.ma_crossover:MACrossoverTrainable",
        "hypothesis": "Synthetic fixture: the runner threads a committed grid into a real walk-forward run.",
        "prior_art": ["None -- synthetic runner fixture."],
        "data": {
            "symbol": "BTC-USDT",
            "interval": "1h",
            "source": "synthetic tmp_path sqlite cache",
            "split": "research",
            "holdout_config_path": "PLACEHOLDER",
            "start_ms": START_MS,
            "end_ms": END_MS,
            "expected_bars": NUM_BARS,
        },
        "parameter_grid": {"fast": [2, 3], "slow": [5, 8]},
        "total_candidates": 4,
        "free_parameter_count": 2,
        "procedure": {
            "train_bars": 40,
            "validate_bars": 20,
            "step_bars": 20,
            "fee_bps": "5",
            "slippage_bps": "2",
            "bars_per_day": 24,
            "funding_included": False,
        },
        "primary_criterion": {
            "kind": "walk_forward_dsr",
            "threshold": 0.95,
            "min_fold_consistency": "0.80",
            "sign_test_alpha": 0.05,
            "max_drawdown_ceiling": "0.20",
            "min_total_trades": 30,
            "profit_factor_floor": "1.3",
            "criteria_pinned_at_claude_md_revision": "2026-07-29",
        },
        "secondary_reported_not_gating": ["t-test p (continuity only)"],
        "declared_detection_floor_sharpe": 1.21,
        "declared_power": {
            "assumed_true_sharpe": 0.8,
            "probability": 0.35,
            "derivation": "Synthetic fixture, not a real power calculation.",
        },
        "outcome_interpretation": {
            "PASS": "Synthetic: would be reported as a candidate.",
            "INCONCLUSIVE": "Synthetic: below the trade floor or unpowered.",
            "FAIL": "Synthetic: criterion not cleared.",
        },
        "stopping_rule": "One run of the four enumerated candidates. No expansion.",
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
        "1h",
        [
            KlineRow(
                open_time_ms=START_MS + i * STEP_MS,
                open=Decimal(100 + i),
                high=Decimal(102 + i),
                low=Decimal(99 + i),
                # A zig-zag close so a moving-average crossover actually
                # fires rather than trending monotonically forever.
                close=Decimal(100 + i) + (Decimal("3") if i % 7 < 3 else Decimal("-3")),
                volume=Decimal("1"),
            )
            for i in range(NUM_BARS)
        ],
    )
    conn.close()
    return path


@pytest.fixture
def holdout_config_path(tmp_path):
    path = tmp_path / "holdout_synthetic.json"
    path.write_text(
        json.dumps(
            {
                "symbol": "BTC-USDT",
                "interval": "1h",
                # Everything in [START_MS, END_MS) is research data.
                "holdout_cutoff_ms": END_MS,
                "set_on": "2026-07-30",
                "rationale": "synthetic test fixture",
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
            open_time=base + timedelta(hours=i),
            open=Decimal(100 + i),
            high=Decimal(102 + i),
            low=Decimal(99 + i),
            close=Decimal(100 + i),
            volume=Decimal("1"),
        )
        for i in range(count)
    ]


class _RecordingTrainable:
    """A `TrainableStrategy` double that records the `params` it was given
    and always trades once per fold, so the fold metrics are defined.
    """

    def __init__(self):
        self.params_seen: list[dict] = []

    def fit(self, train_klines, params, *, parent_run_id):
        self.params_seen.append(dict(params))

        def _strategy(visible_klines):
            if len(visible_klines) != 1:
                return None
            return OrderIntent(
                intent_id=uuid4(),
                symbol="BTC-USDT",
                side=Side.LONG,
                order_type=OrderType.GUARDED_MARKET,
                quantity=Decimal("1"),
                limit_price=None,
                signal_timeframe="1h",
                created_at=visible_klines[-1].open_time,
            )

        return _strategy


def _backtest_records(runs_path: Path) -> list[dict]:
    return [r for r in read_records(runs_path) if r.get("record_type") == "backtest_run"]


# ---------------------------------------------------------------------------
# The round trip
# ---------------------------------------------------------------------------


def test_the_runner_drives_a_registration_end_to_end_and_logs_its_provenance(
    tmp_path, holdout_config_path, runs_path
):
    prereg = load_preregistration(_write_prereg(tmp_path, holdout_config_path))
    strategy = _RecordingTrainable()

    result = run_preregistered(prereg, strategy=strategy, klines=_klines(NUM_BARS), runs_path=runs_path)

    assert result.strategy_id == "sr-s-runner-demo"
    assert len(result.folds) == 3  # 100 bars at train=40/validate=20/step=20

    record = next(r for r in _backtest_records(runs_path) if r["run_id"] == result.run_id)
    assert record["preregistration_id"] == PREREG_ID
    assert record["preregistration_sha256"] == prereg.sha256
    assert record["total_candidates"] == 4
    assert record["strategy_family"] == "sr-s-runner-demo"
    assert record["walk_forward_config"]["train_bars"] == 40
    assert record["walk_forward_config"]["bars_per_day"] == 24
    assert record["fee_bps"] == "5"


def test_the_grid_reaching_the_strategy_comes_from_the_registration_file(
    tmp_path, holdout_config_path, runs_path
):
    prereg = load_preregistration(_write_prereg(tmp_path, holdout_config_path))
    strategy = _RecordingTrainable()

    run_preregistered(prereg, strategy=strategy, klines=_klines(NUM_BARS), runs_path=runs_path)

    params = strategy.params_seen[0]
    assert params["parameter_names"] == ["fast", "slow"]
    assert params["candidates"] == [[2, 5], [2, 8], [3, 5], [3, 8]]
    assert params["total_candidates"] == 4
    assert params["preregistration_id"] == PREREG_ID
    # Every fold sees the same registered grid -- no per-fold divergence.
    assert all(seen == params for seen in strategy.params_seen)


def test_editing_the_grid_in_the_file_is_the_only_way_to_change_it(
    tmp_path, holdout_config_path, runs_path
):
    path = _write_prereg(
        tmp_path, holdout_config_path, parameter_grid={"fast": [2], "slow": [5]}, total_candidates=1
    )
    before = load_preregistration(path)
    strategy = _RecordingTrainable()

    run_preregistered(before, strategy=strategy, klines=_klines(NUM_BARS), runs_path=runs_path)
    assert strategy.params_seen[0]["candidates"] == [[2, 5]]

    # The grid grows only by editing the committed file -- which is the
    # whole mechanism: it shows up in `git log`.
    config = json.loads(path.read_text(encoding="utf-8"))
    config["parameter_grid"] = {"fast": [2, 3], "slow": [5]}
    config["total_candidates"] = 2
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    after = load_preregistration(path)
    strategy2 = _RecordingTrainable()
    run_preregistered(after, strategy=strategy2, klines=_klines(NUM_BARS), runs_path=runs_path)

    assert strategy2.params_seen[0]["candidates"] == [[2, 5], [3, 5]]
    # ...and the recorded sha moves with the edit, so the two logged runs
    # can be told apart by which version of the registration they ran under.
    assert after.sha256 != before.sha256
    logged_shas = [r["preregistration_sha256"] for r in _backtest_records(runs_path) if r["parent_run_id"] is None]
    assert logged_shas == [before.sha256, after.sha256]


def test_a_bar_count_other_than_the_registered_one_warns(tmp_path, holdout_config_path, runs_path, caplog):
    prereg = load_preregistration(_write_prereg(tmp_path, holdout_config_path))

    with caplog.at_level("WARNING"):
        run_preregistered(
            prereg, strategy=_RecordingTrainable(), klines=_klines(NUM_BARS - 5), runs_path=runs_path
        )

    assert any("expected_bars" in record.message for record in caplog.records)


def test_injecting_funding_under_a_registration_that_denies_it_warns(
    tmp_path, holdout_config_path, runs_path, caplog
):
    # `funding_included` in run_kwargs is derived from the series that will
    # ACTUALLY be applied, not copied from the registration -- otherwise this
    # comparison would be trivially self-satisfying and could never catch
    # anything. (CodeRabbit review finding on this task's PR.)
    prereg = load_preregistration(_write_prereg(tmp_path, holdout_config_path))
    assert prereg.procedure["funding_included"] is False

    with caplog.at_level("WARNING"):
        run_preregistered(
            prereg,
            strategy=_RecordingTrainable(),
            klines=_klines(NUM_BARS),
            funding_rates=[
                FundingRate(
                    funding_time=datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
                    funding_rate=Decimal("0.0001"),
                    mark_price=Decimal("50000"),
                )
            ],
            runs_path=runs_path,
        )

    assert any("funding_included" in record.message for record in caplog.records)


def test_a_run_with_no_funding_under_a_registration_that_denies_it_does_not_warn(
    tmp_path, holdout_config_path, runs_path, caplog
):
    prereg = load_preregistration(_write_prereg(tmp_path, holdout_config_path))

    with caplog.at_level("WARNING"):
        run_preregistered(
            prereg, strategy=_RecordingTrainable(), klines=_klines(NUM_BARS), runs_path=runs_path
        )

    assert not any("funding_included" in record.message for record in caplog.records)


def test_the_runner_refuses_to_drive_a_holdout_confirmation_registration(
    tmp_path, holdout_config_path, runs_path
):
    path = _write_prereg(
        tmp_path,
        holdout_config_path,
        data={**_config()["data"], "split": SPLIT_HOLDOUT, "holdout_config_path": str(holdout_config_path)},
        procedure={"fee_bps": "5", "slippage_bps": "2", "bars_per_day": 24, "funding_included": False},
        primary_criterion={
            "kind": "holdout_psr",
            "threshold": 0.95,
            "max_drawdown_ceiling": "0.20",
            "min_total_trades": 41,
            "profit_factor_floor": "1.3",
            "require_sharpe_above_detection_floor": True,
            "criteria_pinned_at_claude_md_revision": "2026-07-29",
        },
    )
    prereg = load_preregistration(path)

    with pytest.raises(ValueError, match="holdout"):
        run_preregistered(prereg, strategy=_RecordingTrainable(), klines=_klines(NUM_BARS), runs_path=runs_path)

    # Nothing was logged: a refused run must not leave a record behind.
    assert _backtest_records(runs_path) == []


def test_a_misaligned_window_still_fails_loudly_from_the_data_layer(
    tmp_path, holdout_config_path, db_path, runs_path
):
    # `research/preregistration.py` deliberately does not re-implement
    # `data/_grid.py`'s alignment rule (that module is documented as
    # internal to `python/data/`). This proves the check is relocated, not
    # lost: a misaligned registered window fails when the window is loaded.
    path = _write_prereg(
        tmp_path,
        holdout_config_path,
        data={
            **_config()["data"],
            "holdout_config_path": str(holdout_config_path),
            "start_ms": START_MS + 1,
        },
    )

    with pytest.raises(ValueError, match="start_ms"):
        run_preregistered(load_preregistration(path), runs_path=runs_path, db_path=db_path)


# ---------------------------------------------------------------------------
# Entry-point resolution
# ---------------------------------------------------------------------------


def test_the_strategy_is_built_from_the_registered_entry_point_when_not_injected(
    tmp_path, holdout_config_path, db_path, runs_path
):
    prereg = load_preregistration(_write_prereg(tmp_path, holdout_config_path))

    result = run_preregistered(prereg, runs_path=runs_path, db_path=db_path)

    assert len(result.folds) == 3
    # `MACrossoverTrainable` logs one child record per (fold, candidate),
    # which is how a grid search's own trial count reaches the log.
    children = [r for r in _backtest_records(runs_path) if r["parent_run_id"] == result.run_id]
    assert len(children) == 3 * 4
    assert {r["total_candidates"] for r in children} == {4}


def test_an_entry_point_outside_the_research_package_is_rejected(tmp_path, holdout_config_path):
    from research.preregistration import PreregistrationError

    path = _write_prereg(tmp_path, holdout_config_path, strategy_entry_point="os:getcwd")

    with pytest.raises(PreregistrationError, match="strategy_entry_point"):
        load_preregistration(path)


def test_an_unresolvable_entry_point_fails_loudly(tmp_path, holdout_config_path, runs_path, db_path):
    prereg = load_preregistration(
        _write_prereg(tmp_path, holdout_config_path, strategy_entry_point="research.strategies.ma_crossover:Nope")
    )

    with pytest.raises(AttributeError):
        run_preregistered(prereg, runs_path=runs_path, db_path=db_path)


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------


def test_the_cli_runs_a_registration_against_the_real_loader_path(
    tmp_path, holdout_config_path, db_path, runs_path, capsys
):
    path = _write_prereg(tmp_path, holdout_config_path)

    exit_code = main([str(path), "--runs-path", str(runs_path), "--db-path", str(db_path)])

    assert exit_code == 0
    parents = [r for r in _backtest_records(runs_path) if r["parent_run_id"] is None]
    assert len(parents) == 1
    assert parents[0]["preregistration_id"] == PREREG_ID
    assert parents[0]["data_range"]["num_bars"] == NUM_BARS
    assert "sr-s-runner-demo" in capsys.readouterr().out


def test_the_cli_reports_the_registration_sha_it_ran_under(
    tmp_path, holdout_config_path, db_path, runs_path, capsys
):
    path = _write_prereg(tmp_path, holdout_config_path)
    prereg = load_preregistration(path)

    main([str(path), "--runs-path", str(runs_path), "--db-path", str(db_path)])

    assert prereg.sha256 in capsys.readouterr().out


def test_the_cli_surfaces_a_malformed_registration_rather_than_running_it(
    tmp_path, holdout_config_path, db_path, runs_path
):
    from research.preregistration import PreregistrationError

    config = _config()
    config["data"] = {**config["data"], "holdout_config_path": str(holdout_config_path)}
    del config["stopping_rule"]
    path = tmp_path / f"{PREREG_ID}.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(PreregistrationError, match="stopping_rule"):
        main([str(path), "--runs-path", str(runs_path), "--db-path", str(db_path)])

    assert _backtest_records(runs_path) == []
