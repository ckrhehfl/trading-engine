"""`python -m research.run_preregistered <path>` -- run a walk-forward
validation whose grid comes from a committed pre-registration file rather
than from a call site. Strategy Research Task S.

## The one property that matters

Everything in `research/preregistration.py` is bookkeeping around a single
mechanism, and it is this module that provides it: **the grid is read from
the registration file, so there is no second place to type a parameter.**
Expanding a search therefore requires editing a git-tracked file, which
shows up in `git log` and in the recorded `preregistration_sha256` on every
affected run. There is no code path here that accepts a grid, a candidate
count, a fee, or a fold geometry from anywhere else.

A consequence worth stating plainly rather than leaving as a puzzle: on
*this* path `check_run_matches_preregistration`'s hard block can never fire,
because the candidate count it compares is derived from the same file it is
compared against. That is not the block being useless -- it is the stronger
guarantee. The block exists for the other path: a hand-written script that
calls `run_walk_forward` directly while claiming a `preregistration_id`. One
route makes expansion impossible by construction; the other makes it loud.

## Deliberately thin

This module resolves a strategy, loads the registered window through the
existing research loader, checks the run against the registration, and calls
`research.walkforward.run_walk_forward`. It must not grow into a second
strategy-configuration surface: anything a strategy needs to know belongs in
the registration file (data) or in the strategy module (code).

**Research split only.** A `data.split == "holdout"` registration is refused
here, by design. A holdout confirmation is a single deliberate act with a
single-access claim on record (`holdout.load_holdout_klines`), it is judged
against a different criterion (PSR, not DSR -- CLAUDE.md's single-window
variant), and CLAUDE.md reserves the `1d` early window until a specification
is committed for it. Wiring that path from a general-purpose runner would
make spending a holdout a one-command accident. The registration *schema*
can express a holdout confirmation, so its criteria can be pinned before
access as the Bar requires; driving it is a separate, later task.

## Calling convention for a pre-registered strategy

`strategy_entry_point` names `module:attribute` inside the `research`
package. The attribute is called with exactly these keyword arguments and
must return a `research.walkforward.TrainableStrategy`:

    target(strategy_id=..., strategy_version=..., fee_bps=..., slippage_bps=...,
           runs_path=...)

That is the signature this project's existing `Trainable` classes already
have (`research/strategies/ma_crossover.py`'s `MACrossoverTrainable`, and the
same shape in every strategy module since). A strategy needing anything else
exposes a small module-level factory with this signature rather than
widening this contract.

The registered grid reaches the strategy through
`Preregistration.run_params()` as `params["candidates"]` (value lists in the
declared parameter order -- the shape this project's `fit()` implementations
already consume) plus `params["parameter_names"]`,
`params["total_candidates"]` and `params["preregistration_id"]`.
"""

import argparse
import importlib
import logging
from pathlib import Path
from typing import Any, Sequence

from backtest.kline import Kline
from data.backfill import DEFAULT_DB_PATH
from metrics.funding import FundingRate
from research import experiment_log
from research.holdout import load_research_funding, load_research_klines
from research.preregistration import (
    ELIGIBILITY_BAR_MIN_FOLD_COUNT,
    ENTRY_POINT_MODULE_PREFIX,
    Preregistration,
    PreregistrationError,
    check_run_matches_preregistration,
    load_preregistration,
    warn_if_uncommitted,
)
from research.walkforward import TrainableStrategy, WalkForwardResult, run_walk_forward

logger = logging.getLogger(__name__)


def resolve_entry_point(prereg: Preregistration) -> Any:
    """Import the registered `module:attribute`.

    Re-checks the `research.` module prefix even though
    `validate_preregistration` already enforced it: this is the function that
    actually executes an import named by a config file, and a guard is worth
    having at the point of use rather than only at the point of validation.
    An unimportable module or a missing attribute propagates
    (`ModuleNotFoundError`/`AttributeError`) -- a registration naming code
    that does not exist is a loud failure, not something to paper over.
    """
    module_path, attribute = prereg.strategy_entry_point.split(":")
    if not module_path.startswith(ENTRY_POINT_MODULE_PREFIX):
        raise PreregistrationError(
            f"strategy_entry_point must name a module inside {ENTRY_POINT_MODULE_PREFIX!r}, "
            f"got {module_path!r}"
        )
    return getattr(importlib.import_module(module_path), attribute)


def build_strategy(prereg: Preregistration, *, runs_path: str | Path) -> TrainableStrategy:
    """Instantiate the registered strategy via this module's documented
    calling convention (see the module docstring).
    """
    target = resolve_entry_point(prereg)
    return target(
        strategy_id=prereg.strategy_id,
        strategy_version=prereg.strategy_version,
        fee_bps=prereg.fee_bps,
        slippage_bps=prereg.slippage_bps,
        runs_path=str(runs_path),
    )


def run_preregistered(
    prereg: Preregistration,
    *,
    strategy: TrainableStrategy | None = None,
    klines: list[Kline] | None = None,
    funding_rates: Sequence[FundingRate] | None = None,
    runs_path: str | Path = experiment_log.DEFAULT_RUNS_PATH,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> WalkForwardResult:
    """Run the registered walk-forward validation and log it with its
    pre-registration provenance.

    `strategy` and `klines` exist so a test can inject both without a
    database or an importable strategy; in real use both are `None` and come
    from the registration (`strategy_entry_point`, and the registered window
    loaded through `holdout.load_research_klines` -- the loader that
    structurally cannot return holdout data).

    Raises `ValueError` for a holdout-split registration (see the module
    docstring) and `preregistration.GridExpansionError` if the run somehow
    reports more candidates than were registered. Everything else that
    deviates from the registration is warned about, loudly, and still runs.
    """
    if prereg.is_holdout_confirmation:
        raise ValueError(
            f"pre-registration {prereg.preregistration_id!r} declares "
            f"data.split='holdout': this runner drives research-split runs only. A holdout "
            "confirmation is a single deliberate act with a single-access claim on record "
            "(research.holdout.load_holdout_klines), judged against PSR rather than DSR per "
            "CLAUDE.md's single-window variant -- it is not something a general-purpose runner "
            "should be able to spend by accident."
        )

    warn_if_uncommitted(prereg.path)

    fold_count = prereg.expected_fold_count
    if fold_count is not None and fold_count < ELIGIBILITY_BAR_MIN_FOLD_COUNT:
        logger.warning(
            "run_preregistered: pre-registration %r declares a geometry producing %d fold(s), "
            "below CLAUDE.md's 8-10 fold credibility floor. Reported, not blocked -- a "
            "data-limited window may not support 8 folds at any sensible sizing -- but the "
            "result must be written up carrying this fact.",
            prereg.preregistration_id,
            fold_count,
        )

    procedure = prereg.procedure
    data = prereg.data
    run_kwargs = {
        "strategy_id": prereg.strategy_id,
        "strategy_version": prereg.strategy_version,
        "strategy_family": prereg.strategy_family,
        "symbol": data["symbol"],
        "interval": data["interval"],
        "train_bars": procedure["train_bars"],
        "validate_bars": procedure["validate_bars"],
        "step_bars": procedure["step_bars"],
        "bars_per_day": procedure["bars_per_day"],
        "fee_bps": prereg.fee_bps,
        "slippage_bps": prereg.slippage_bps,
        "funding_included": procedure["funding_included"],
        "is_holdout_run": False,
        "total_candidates": prereg.total_candidates,
    }
    # Cannot fire on this path -- the count compared is derived from the file
    # it is compared against. Called anyway, deliberately: it is also what
    # emits the mismatch warnings, and skipping it here would leave the one
    # real caller of the check untested in practice.
    check_run_matches_preregistration(prereg, run_kwargs)

    if klines is None:
        klines = load_research_klines(
            data["start_ms"],
            data["end_ms"],
            db_path=db_path,
            holdout_config_path=data["holdout_config_path"],
        )
    if len(klines) != data["expected_bars"]:
        logger.warning(
            "run_preregistered: pre-registration %r declares expected_bars=%d but %d bar(s) "
            "loaded. BingX retention is rolling, so a drift here can be legitimate -- but the "
            "registered window is what the declared detection floor and trade-count floor were "
            "computed from, so a large gap invalidates both.",
            prereg.preregistration_id,
            data["expected_bars"],
            len(klines),
        )

    if funding_rates is None and procedure["funding_included"]:
        # Registered as funding-inclusive, so it must actually be inclusive:
        # a claim in the file with no series threaded through would be a
        # silent mismatch of exactly the kind this task exists to prevent.
        funding_rates = load_research_funding(
            data["start_ms"],
            data["end_ms"],
            symbol=data["symbol"],
            db_path=db_path,
            holdout_config_path=data["holdout_config_path"],
        )

    if strategy is None:
        strategy = build_strategy(prereg, runs_path=runs_path)

    return run_walk_forward(
        klines,
        strategy,
        prereg.strategy_id,
        prereg.strategy_version,
        prereg.run_params(),
        train_bars=procedure["train_bars"],
        validate_bars=procedure["validate_bars"],
        step_bars=procedure["step_bars"],
        fee_bps=prereg.fee_bps,
        slippage_bps=prereg.slippage_bps,
        bars_per_day=procedure["bars_per_day"],
        funding_rates=funding_rates,
        strategy_family=prereg.strategy_family,
        total_candidates=prereg.total_candidates,
        preregistration_id=prereg.preregistration_id,
        preregistration_sha256=prereg.sha256,
        runs_path=str(runs_path),
    )


def _format_summary(prereg: Preregistration, result: WalkForwardResult) -> str:
    """The run's own summary, printed alongside the **pre-committed**
    outcome interpretation.

    Printing the three interpretation regions verbatim next to the numbers is
    the point of pre-registering them: a disappointing result cannot be
    re-narrated afterwards if the narrative for each outcome region was
    committed, hashed and printed before anyone saw which region the result
    landed in.
    """
    aggregate = result.aggregate
    lines = [
        f"pre-registration : {prereg.preregistration_id}",
        f"  file           : {prereg.path}",
        f"  sha256         : {prereg.sha256}",
        f"  strategy       : {prereg.strategy_id} {prereg.strategy_version} "
        f"(family {prereg.strategy_family})",
        f"  candidates     : {prereg.total_candidates} registered",
        f"  criterion      : {prereg.criterion_kind} "
        f">= {prereg.primary_criterion['threshold']}",
        f"  detection floor: {prereg.config['declared_detection_floor_sharpe']} annualized Sharpe "
        "(declared before the run)",
        "",
        f"run_id           : {result.run_id}",
        f"  folds          : {aggregate.get('fold_count')}",
        f"  mean Sharpe    : {aggregate.get('mean_sharpe')}",
        f"  min Sharpe     : {aggregate.get('min_sharpe')}",
        f"  total trades   : {aggregate.get('total_trades')}",
        f"  worst drawdown : {aggregate.get('worst_fold_max_drawdown')}",
        f"  mean profit f. : {aggregate.get('mean_profit_factor')}",
        "",
        "pre-committed outcome interpretation (written before this run):",
    ]
    for region, text in prereg.config["outcome_interpretation"].items():
        lines.append(f"  {region}: {text}")
    lines.append("")
    lines.append(
        "This runner does NOT assign a verdict. Evaluate the criterion with "
        "research/eligibility.py and research/overfitting_check.py, then report the region "
        "above that the result actually lands in."
    )
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a walk-forward validation whose parameter grid comes from a committed "
            "pre-registration file (configs/research/preregistrations/<id>.json). Research "
            "split only -- a holdout confirmation registration is refused."
        )
    )
    parser.add_argument("preregistration_path", help="path to configs/research/preregistrations/<id>.json")
    parser.add_argument("--runs-path", default=experiment_log.DEFAULT_RUNS_PATH)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    prereg = load_preregistration(args.preregistration_path)
    result = run_preregistered(prereg, runs_path=args.runs_path, db_path=args.db_path)
    print(_format_summary(prereg, result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
