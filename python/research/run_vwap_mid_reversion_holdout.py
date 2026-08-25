"""`python -m research.run_vwap_mid_reversion_holdout` -- the real
execution path for the `vwap-mid-reversion` 1m holdout confirmation
(Scalping Strategy Research Task S4).

## Why this exists, and why it is not just "run `verify_1m_gaps.py`,
## then run `research.run_preregistered_holdout` yourself"

`research/verify_1m_gaps.py` was originally built as a standalone
module precisely so it would never need to touch
`research/run_preregistered_holdout.py`'s own shared, already-proven
infrastructure. That is still true here -- this module does not modify
`run_preregistered_holdout.py` at all. But a standalone check that
nothing calls automatically is only a *convention* ("remember to run
this first"), not an enforced gate -- a real CodeRabbit review finding
on this task's own PR caught exactly this: the actual holdout-loading
path (`research.holdout.load_holdout_klines`, invoked from
`run_preregistered_holdout`) does not call `verify_1m_gaps` itself, so
an operator who runs `python -m research.run_preregistered_holdout
<path>` directly -- the same command every prior holdout confirmation
(`sr-v`, `sr-ab`) has used -- would consume the single-access holdout
claim even if an unexpected gap existed, with no structural check in
the way.

This module is the fix, scoped narrowly: a thin wrapper, specific to
the one registration (`vwap-mid-reversion-1m-holdout.json`) that
currently has any known gaps at all, which calls `verify_1m_gaps`
**unconditionally, before** calling `run_preregistered_holdout` --
never the other way around, and with no path that reaches the real
holdout loader without the gap check having already passed. If
`verify_1m_gaps` raises `UnexpectedGapError`, Python's own control flow
guarantees `run_preregistered_holdout` (and therefore
`load_holdout_klines` and the single-access claim it writes) is never
reached at all -- not "should not run," structurally cannot run.

`verify_gaps`/`execute_holdout` are injectable (defaulting to the real
functions) purely so this ordering property itself can be unit-tested
without touching the real database or the real holdout-access claim
tracking -- mirrors `run_preregistered_holdout`'s own `strategy`/
`klines` injection parameters, same rationale.
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Callable

from data.backfill import DEFAULT_DB_PATH
from research import experiment_log
from research.preregistration import load_preregistration
from research.run_preregistered_holdout import HoldoutConfirmationResult, run_preregistered_holdout
from research.verify_1m_gaps import verify_1m_gaps

logger = logging.getLogger(__name__)

DEFAULT_PREREGISTRATION_PATH = "configs/research/preregistrations/vwap-mid-reversion-1m-holdout.json"


def run_vwap_mid_reversion_holdout(
    *,
    preregistration_path: str | Path = DEFAULT_PREREGISTRATION_PATH,
    db_path: str | Path = DEFAULT_DB_PATH,
    runs_path: str | Path = experiment_log.DEFAULT_RUNS_PATH,
    force_reclaim_reason: str | None = None,
    verify_gaps: Callable[..., object] = verify_1m_gaps,
    execute_holdout: Callable[..., HoldoutConfirmationResult] = run_preregistered_holdout,
) -> HoldoutConfirmationResult:
    """Load the registered `vwap-mid-reversion` pre-registration, run the
    gap preflight against its own declared `start_ms`/`end_ms`, and only
    then execute the real holdout confirmation.

    Raises `research.verify_1m_gaps.UnexpectedGapError` (propagated,
    unmodified) if the real gap set does not match the 2 known,
    disclosed gaps exactly -- and, by construction, never calls
    `execute_holdout` in that case, so no holdout data is loaded and no
    single-access claim is written.
    """
    prereg = load_preregistration(preregistration_path)

    verify_gaps(prereg.data["start_ms"], prereg.data["end_ms"], db_path=db_path)

    return execute_holdout(
        prereg,
        runs_path=runs_path,
        db_path=db_path,
        force_reclaim_reason=force_reclaim_reason,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Execute the real vwap-mid-reversion 1m holdout confirmation (Scalping Strategy "
            "Research Task S4), gated by a mandatory gap preflight that runs BEFORE any holdout "
            "data is loaded. This is the real execution path for this specific registration -- "
            "use this, not research.run_preregistered_holdout directly, so the gap check can "
            "never be skipped by accident."
        )
    )
    parser.add_argument("--preregistration-path", default=DEFAULT_PREREGISTRATION_PATH)
    parser.add_argument("--runs-path", default=experiment_log.DEFAULT_RUNS_PATH)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument(
        "--force-reclaim-reason",
        default=None,
        help=(
            "Only pass this for a deliberate, justified re-run of an ALREADY-claimed holdout "
            "access. Omit it for the normal, single, real execution this script exists for."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = run_vwap_mid_reversion_holdout(
        preregistration_path=args.preregistration_path,
        db_path=args.db_path,
        runs_path=args.runs_path,
        force_reclaim_reason=args.force_reclaim_reason,
    )
    summary = result.to_dict()
    summary["observed_sharpe_ratio"] = result.metrics.sharpe_ratio
    summary["observed_max_drawdown"] = result.metrics.max_drawdown
    summary["observed_num_trades"] = result.metrics.num_trades
    summary["observed_profit_factor"] = result.metrics.profit_factor
    summary["observed_psr"] = result.psr_result.psr
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
