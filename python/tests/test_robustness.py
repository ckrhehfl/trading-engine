"""Tests for `python/research/robustness.py` -- the parameter-sensitivity
overfitting check (CLAUDE.md's Strategy Research Methodology /
`.planning/sr-g-overfitting-safeguards.md`, "Finding 2").

Covers: `perturb_candidate`'s neighbor-generation arithmetic in isolation
(rounding, dedup, clamping), `check_parameter_sensitivity` against a fully
synthetic `TrainableStrategy` test double with a controlled scoring
surface (both a "flat"/robust surface and a "spiky"/fragile one), error
handling for neighbor candidates that are invalid for the wrapped
strategy (e.g. `fast >= slow`), and one integration-style test against the
real `research.strategies.ma_crossover.MACrossoverTrainable` to prove the
utility works generically against a real strategy's grid shape, not only
the synthetic double.

`check_parameter_sensitivity` never logs anything to `runs/experiments.jsonl`
itself -- it relies entirely on the wrapped `TrainableStrategy`'s own
`fit()` to do so (the same single-candidate-grid `fit()` call it already
makes for every other real candidate, per `research/walkforward.py`'s
`TrainableStrategy` protocol), so it takes no `runs_path` parameter at all.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backtest.kline import Kline
from research.robustness import check_parameter_sensitivity, perturb_candidate
from research.strategies.ma_crossover import MACrossoverTrainable
from schemas.order_intent import OrderIntent, OrderType, Side

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _klines(count: int, start_price: int = 100) -> list[Kline]:
    return [
        Kline(
            open_time=BASE_TIME + timedelta(minutes=15 * i),
            open=Decimal(start_price + i),
            high=Decimal(start_price + i + 1),
            low=Decimal(start_price + i - 1),
            close=Decimal(start_price + i) + Decimal("0.5"),
            volume=Decimal("100"),
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# perturb_candidate
# ---------------------------------------------------------------------------


def test_perturb_candidate_generates_plus_and_minus_ten_and_twentyfive_percent_by_default():
    neighbors = perturb_candidate((10, 30))

    # Position 0 (value 10): +-10% -> 9/11, +-25% -> 8(7.5->8)/13(12.5->13, round-half-up)
    # Position 1 (value 30): +-10% -> 27/33, +-25% -> 23(22.5->23)/38(37.5->38)
    position_0_values = {n.candidate[0] for n in neighbors if n.perturbed_index == 0}
    position_1_values = {n.candidate[1] for n in neighbors if n.perturbed_index == 1}
    assert position_0_values == {9, 11, 8, 13}
    assert position_1_values == {27, 33, 23, 38}


def test_perturb_candidate_holds_other_positions_fixed():
    neighbors = perturb_candidate((10, 30))
    for n in neighbors:
        for i, value in enumerate(n.candidate):
            if i != n.perturbed_index:
                assert value == (10, 30)[i]


def test_perturb_candidate_rounds_half_up_not_bankers_rounding():
    # 10 +/- 25% = 12.5 / 7.5 -- exact ties. Python's builtin round() would
    # banker's-round these to 12/8; this function must round half up (13/8)
    # so the perturbation is predictable without needing to know Python's
    # float rounding mode.
    neighbors = perturb_candidate((10,), fractions=(Decimal("0.25"),))
    values = sorted(n.candidate[0] for n in neighbors)
    assert values == [8, 13]


def test_perturb_candidate_clamps_to_minimum_value():
    # A -90% perturbation of 2 computes to 0.2 -> rounds to 0, below
    # min_value -- must clamp up to min_value rather than emit a
    # non-positive or zero candidate.
    neighbors = perturb_candidate((2,), fractions=(Decimal("0.9"),), min_value=1)
    assert {n.candidate for n in neighbors} == {(4,), (1,)}
    for n in neighbors:
        assert n.candidate[0] >= 1


def test_perturb_candidate_deduplicates_neighbors_that_collide_after_rounding():
    # 6 -10% = 5.4 -> 5; 6 -25% = 4.5 -> 5 (round-half-up) -- two different
    # fractions collide on the same rounded neighbor value and must only
    # appear once in the output, not twice.
    neighbors = perturb_candidate((6,), fractions=(Decimal("0.10"), Decimal("0.25")))
    candidates = [n.candidate for n in neighbors]
    assert len(candidates) == len(set(candidates))
    assert set(candidates) == {(7,), (5,), (8,)}


def test_perturb_candidate_rejects_empty_fractions():
    with pytest.raises(ValueError):
        perturb_candidate((10, 30), fractions=())


# ---------------------------------------------------------------------------
# check_parameter_sensitivity -- synthetic controlled surface
# ---------------------------------------------------------------------------


class _FlatSurfaceStrategy:
    """A `Strategy` test double: always buys once on the first bar and
    force-closes at the end (so it always has exactly one trade), with a
    fixed positive return -- used as the "this candidate is profitable"
    stand-in returned by `_RecordingCandidateTrainable.fit()`.
    """

    def __call__(self, window):
        if len(window) != 1:
            return None
        return OrderIntent(
            intent_id=uuid.uuid4(),
            symbol="BTC-USDT",
            side=Side.LONG,
            order_type=OrderType.GUARDED_MARKET,
            quantity=Decimal("1"),
            limit_price=None,
            signal_timeframe="15m",
            created_at=window[-1].open_time,
        )


class _RecordingCandidateTrainable:
    """A `TrainableStrategy` test double that mimics the real strategies'
    `candidates`-list convention (`params["candidates"]` is a list of
    exactly one numeric tuple when driven by `check_parameter_sensitivity`;
    `fit()` returns a bound `Strategy` for it), with a fully controlled
    scoring surface instead of a real fill simulation -- lets tests
    construct an exact "robust" (every neighbor also profitable) or
    "fragile" (only the winner profitable) shape without needing to
    reverse-engineer real backtest mechanics.

    `check_parameter_sensitivity` re-derives each candidate's score via
    `run_backtest`+`compute_metrics` on whatever `Strategy` `fit()`
    returns, so this double controls the *shape* purely by choosing which
    `Strategy` to return per candidate: `_FlatSurfaceStrategy()` (trades
    once, profitably) for a candidate in `profitable_candidates`, or a
    strategy that never trades (zero return) otherwise.
    """

    def __init__(self, profitable_candidates: set[tuple[int, ...]]):
        self._profitable_candidates = profitable_candidates
        self.fit_calls: list[tuple[int, ...]] = []

    def fit(self, train_klines, params, *, parent_run_id=None):
        (candidate,) = params["candidates"]
        self.fit_calls.append(tuple(candidate))
        if tuple(candidate) in self._profitable_candidates:
            return _FlatSurfaceStrategy()

        def _never_trades(window):
            return None

        return _never_trades


def test_check_parameter_sensitivity_flags_a_flat_surface_as_robust():
    klines = _klines(4)
    # Every neighbor of (10, 30) is also profitable -> flat surface.
    all_candidates = {(10, 30)} | {n.candidate for n in perturb_candidate((10, 30))}
    strategy = _RecordingCandidateTrainable(profitable_candidates=all_candidates)

    result = check_parameter_sensitivity(
        strategy,
        {"quantity": "0.001", "symbol": "BTC-USDT"},
        (10, 30),
        klines,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result.winning_total_return > 0
    assert result.is_robust is True
    valid_neighbors = [n for n in result.neighbors if n.total_return is not None]
    assert len(valid_neighbors) == len(all_candidates) - 1
    assert all(n.total_return > 0 for n in valid_neighbors)


def test_check_parameter_sensitivity_flags_a_spiky_surface_as_not_robust():
    klines = _klines(4)
    # Only the exact winner is profitable -- every neighbor never trades.
    strategy = _RecordingCandidateTrainable(profitable_candidates={(10, 30)})

    result = check_parameter_sensitivity(
        strategy,
        {"quantity": "0.001", "symbol": "BTC-USDT"},
        (10, 30),
        klines,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result.winning_total_return > 0
    assert result.is_robust is False
    valid_neighbors = [n for n in result.neighbors if n.total_return is not None]
    assert len(valid_neighbors) > 0
    assert all(n.total_return == 0 for n in valid_neighbors)


def test_check_parameter_sensitivity_not_robust_when_winner_itself_is_unprofitable():
    klines = _klines(4)
    strategy = _RecordingCandidateTrainable(profitable_candidates=set())  # nothing trades, ever

    result = check_parameter_sensitivity(
        strategy,
        {},
        (10, 30),
        klines,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result.winning_total_return == 0
    assert result.is_robust is False
    assert "not profitable" in result.reason


def test_check_parameter_sensitivity_evaluates_every_generated_neighbor():
    klines = _klines(4)
    all_candidates = {(10, 30)} | {n.candidate for n in perturb_candidate((10, 30))}
    strategy = _RecordingCandidateTrainable(profitable_candidates=all_candidates)

    result = check_parameter_sensitivity(
        strategy,
        {},
        (10, 30),
        klines,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    expected_neighbor_candidates = {n.candidate for n in perturb_candidate((10, 30))}
    assert {n.candidate for n in result.neighbors} == expected_neighbor_candidates


def test_check_parameter_sensitivity_only_fits_the_winner_and_each_neighbor_once():
    klines = _klines(4)
    all_candidates = {(10, 30)} | {n.candidate for n in perturb_candidate((10, 30))}
    strategy = _RecordingCandidateTrainable(profitable_candidates=all_candidates)

    check_parameter_sensitivity(
        strategy,
        {},
        (10, 30),
        klines,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    # winner + every neighbor, each fit exactly once, on the same
    # train_klines this call was given -- no other dataset is ever
    # involved (fit() only ever receives what this test passed as klines).
    assert len(strategy.fit_calls) == 1 + len(perturb_candidate((10, 30)))
    assert len(set(strategy.fit_calls)) == len(strategy.fit_calls)


# ---------------------------------------------------------------------------
# check_parameter_sensitivity -- invalid neighbor handling
# ---------------------------------------------------------------------------


class _RaisesOnInvalidCandidateTrainable:
    """Mimics a real strategy's fit() raising ValueError when a candidate
    is structurally invalid (e.g. ma_crossover's `fast >= slow` guard).
    """

    def fit(self, train_klines, params, *, parent_run_id=None):
        (candidate,) = params["candidates"]
        fast, slow = candidate
        if fast >= slow:
            raise ValueError(f"fast window ({fast}) must be strictly less than slow window ({slow})")
        return _FlatSurfaceStrategy()


def test_check_parameter_sensitivity_skips_invalid_neighbors_without_crashing():
    klines = _klines(4)
    strategy = _RaisesOnInvalidCandidateTrainable()

    # (8, 10): a -25% perturbation of slow (10 -> 7 or 8) can collide with
    # or cross fast (8), producing an invalid fast>=slow combination.
    result = check_parameter_sensitivity(
        strategy,
        {},
        (8, 10),
        klines,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    invalid_neighbors = [n for n in result.neighbors if n.error is not None]
    assert len(invalid_neighbors) > 0
    for n in invalid_neighbors:
        assert n.total_return is None
    # The overall check must still complete and return a result, not raise.
    assert isinstance(result.is_robust, bool)


# ---------------------------------------------------------------------------
# check_parameter_sensitivity -- real strategy integration
# ---------------------------------------------------------------------------


def test_check_parameter_sensitivity_works_with_real_ma_crossover_trainable(tmp_path):
    # Demonstrates the utility works generically against a real
    # TrainableStrategy (not just the synthetic double above), using
    # ma_crossover's real DEFAULT_CANDIDATE_GRID shape, and that the real
    # strategy's own fit() logging (one backtest_run record per
    # single-candidate fit call check_parameter_sensitivity makes) still
    # happens exactly as it would for a normal grid-search candidate.
    klines = _klines(120)
    runs_path = tmp_path / "experiments.jsonl"
    strategy = MACrossoverTrainable(
        strategy_id="ma-crossover-sensitivity-test",
        strategy_version="v1",
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        runs_path=str(runs_path),
    )

    result = check_parameter_sensitivity(
        strategy,
        {"quantity": "0.001", "symbol": "BTC-USDT"},
        (5, 20),
        klines,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result.winning_candidate == (5, 20)
    assert len(result.neighbors) > 0
    assert isinstance(result.is_robust, bool)
    assert runs_path.exists()
    lines = runs_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 1
    for line in lines:
        record = json.loads(line)
        assert record["strategy_id"] == "ma-crossover-sensitivity-test"
        assert record["parent_run_id"] is None
        assert record["total_candidates"] == 1


# ---------------------------------------------------------------------------
# check_parameter_sensitivity -- serialization for the experiment log
# ---------------------------------------------------------------------------


def test_parameter_sensitivity_result_to_dict_has_expected_keys():
    klines = _klines(4)
    all_candidates = {(10, 30)} | {n.candidate for n in perturb_candidate((10, 30))}
    strategy = _RecordingCandidateTrainable(profitable_candidates=all_candidates)

    result = check_parameter_sensitivity(
        strategy,
        {},
        (10, 30),
        klines,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    as_dict = result.to_dict()
    assert as_dict["winning_candidate"] == [10, 30]
    assert as_dict["is_robust"] is True
    assert "reason" in as_dict
    assert "neighbors" in as_dict
    assert isinstance(as_dict["neighbors"], list)
    assert as_dict["neighbors"][0]["candidate"]
    # Must be JSON-serializable end to end (same convention as every other
    # object that flows into runs/experiments.jsonl).
    json.dumps(as_dict, default=str)
