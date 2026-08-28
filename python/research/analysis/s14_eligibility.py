"""Task S14: score the logged `selective-reversion` walk-forward against
the Eligibility Bar.

    PYTHONPATH=python python/.venv/bin/python -m research.analysis.s14_eligibility

Reads the record `s14_walkforward_run.py` already wrote to
`runs/experiments.jsonl` rather than re-running the walk-forward.
Deliberate: every `run_walk_forward` call logs a trial and raises the
project-level `N` that this same evaluation is deflated against, so a
scorer that re-ran the backtest would make the number worse each time it
was invoked.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from decimal import Decimal
from pathlib import Path

from research import eligibility, lineage, overfitting_check

STRATEGY_ID = "selective-reversion"
FAMILY = "btc-scalping"
RUNS_PATH = "runs/experiments.jsonl"

# CLAUDE.md leaves the fold-consistency threshold to a human decision
# within its approved 80-90% band; 0.80 is the permissive end, chosen so a
# failure here cannot be blamed on picking the strict end.
MIN_FOLD_CONSISTENCY = Decimal("0.80")
BARS_PER_DAY = 1440
DSR_THRESHOLD = 0.95
DRAWDOWN_CEILING = 0.25
PROFIT_FACTOR_FLOOR = 1.3


# The `aggregate_metrics` keys this scorer reads. Named, so a record is
# selected by what it actually provides rather than by what type it calls
# itself -- the same reason `fold_results` is checked for content and not
# merely for presence.
REQUIRED_AGGREGATE_KEYS = (
    "total_trades",
    "mean_sharpe",
    "worst_fold_max_drawdown",
    "mean_profit_factor",
)


def _as_number(value, *, non_negative_integer: bool = False) -> float | None:
    """`value` as a float, or `None` if it does not denote one.

    `None` here is a real, expected value rather than corruption: this
    codebase's `Metrics` contract returns `None` -- deliberately not
    `0.0`, `inf`, or an exception -- for a degenerate input, so a fold set
    with no closed trades genuinely produces a null `mean_sharpe` or
    `mean_profit_factor`. `worst_fold_max_drawdown` is logged as a
    `Decimal`-derived string, so strings are accepted too. `bool` is
    excluded because it is an `int` subclass and `True` is never a metric.

    NaN and +/-Infinity are rejected too. Those are not "no value" but a
    value that silently poisons every comparison downstream: NaN fails
    every `>=` test, so a NaN profit factor would read as a clean FAIL
    rather than as unevaluable -- exactly the misclassification this
    function exists to prevent. `json.loads` really does produce them,
    since Python's JSON accepts the non-standard `NaN`/`Infinity`
    literals its own `json.dumps` emits.

    `non_negative_integer` additionally requires a whole, non-negative
    count -- for `total_trades`, where `12.5` or `-3` is corruption
    rather than a degenerate measurement.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, str)):
        try:
            number = float(value)
        except (ValueError, OverflowError):
            # `OverflowError` is not hypothetical: JSON integers are
            # unbounded, so `json.loads` happily produces a Python int
            # too large for a float, and `float()` raises on it. A
            # too-large number is unevaluable in exactly the same way a
            # null is, and must reach `_require_scoreable` as `None`
            # rather than escape as a traceback.
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    if non_negative_integer and (number < 0 or number != int(number)):
        return None
    return number


def _load(runs_path: str, strategy_id: str, run_id: str | None = None) -> dict:
    # Parse every line and match `strategy_id` as a field. A substring test
    # on the raw line would also match a record that merely mentions this id
    # somewhere else (a `params` value, a family name, a future field), and
    # scoring the wrong record is worse than scoring none.
    records = []
    for line in Path(runs_path).read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if isinstance(record, dict) and record.get("strategy_id") == strategy_id:
            records.append(record)

    # The log holds several shapes for one strategy_id (the walk-forward run,
    # plus any per-candidate `backtest_run` children it logged), so select
    # positively on the fields read below rather than excluding a type.
    walk_forward = [
        r for r in records
        if isinstance(r.get("fold_results"), list)
        and r["fold_results"]
        and isinstance(r.get("aggregate_metrics"), dict)
        and all(k in r["aggregate_metrics"] for k in REQUIRED_AGGREGATE_KEYS)
    ]
    if run_id is not None:
        walk_forward = [r for r in walk_forward if r.get("run_id") == run_id]
        if not walk_forward:
            print(f"no walk-forward record with run_id={run_id} for {strategy_id} "
                  f"in {runs_path}.", file=sys.stderr)
            raise SystemExit(1)
    if not walk_forward:
        print(
            f"no logged walk-forward record for {strategy_id} in {runs_path}: "
            f"{len(records)} record(s) carry that strategy_id, none with a "
            f"non-empty fold_results and an aggregate_metrics providing all of "
            f"{', '.join(REQUIRED_AGGREGATE_KEYS)}.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if len(walk_forward) > 1 and run_id is None:
        # Fail closed rather than silently take the newest. One
        # `strategy_id` can legitimately cover several logged cells --
        # S15 ran two sizing variants under one id -- and quietly
        # scoring whichever happened to be logged last would attach a
        # verdict to a configuration the caller never named.
        print(
            f"{len(walk_forward)} walk-forward records match strategy_id="
            f"{strategy_id!r}; refusing to guess which one to score.\n"
            f"Re-run with --run-id, choosing from:",
            file=sys.stderr,
        )
        for r in walk_forward:
            params = r.get("params") or {}
            hint = ", ".join(
                f"{k}={params[k]}" for k in sorted(params) if k in ("sizing_mode", "use_stop")
            )
            print(f"  {r.get('run_id')}  {r.get('logged_at', '')}  {hint}", file=sys.stderr)
        raise SystemExit(1)
    return walk_forward[-1]


def _require_scoreable(agg: dict) -> dict[str, float]:
    """The four aggregate metrics as real numbers, or exit saying which
    are undefined.

    Presence of a key is not the same as a value that can be scored. A
    `null` `mean_sharpe` or `mean_profit_factor` is a legitimate logged
    outcome (see `_as_number`), and it means the run produced no
    evaluable risk-adjusted return -- which is an INCONCLUSIVE-DATA-LIMITED
    condition under CLAUDE.md's trade-count clause, never a FAIL. Formatting
    it would raise `TypeError` and formatting a substituted zero would
    silently report a verdict the data does not support.
    """
    resolved = {
        k: _as_number(agg.get(k), non_negative_integer=(k == "total_trades"))
        for k in REQUIRED_AGGREGATE_KEYS
    }
    undefined = sorted(k for k, v in resolved.items() if v is None)
    if undefined:
        print(
            f"VERDICT: INCONCLUSIVE-DATA-LIMITED -- undefined aggregate "
            f"metric(s): {', '.join(undefined)}. A null metric means the run "
            f"produced nothing evaluable there, which is not evidence against "
            f"the strategy and must not be reported as a FAIL."
        )
        raise SystemExit(0)
    return resolved


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs-path", default=RUNS_PATH)
    ap.add_argument("--strategy-id", default=STRATEGY_ID,
                    help="which logged strategy_id to score (default: the S14 candidate)")
    ap.add_argument("--run-id", default=None,
                    help="disambiguate when one strategy_id covers several logged runs")
    args = ap.parse_args(argv)

    record = _load(args.runs_path, args.strategy_id, args.run_id)
    folds = record["fold_results"]
    agg = record["aggregate_metrics"]
    scoreable = _require_scoreable(agg)
    sharpes = [f["metrics"]["sharpe_ratio"] for f in folds]
    trades = int(scoreable["total_trades"])
    mean_sharpe = scoreable["mean_sharpe"]
    worst_drawdown = scoreable["worst_fold_max_drawdown"]
    mean_profit_factor = scoreable["mean_profit_factor"]

    print(f"run_id            : {record['run_id']}")
    print(f"folds             : {len(folds)}")
    print(f"total trades      : {trades:,}")
    print(f"mean fold Sharpe  : {mean_sharpe:+.4f}")
    print(f"worst drawdown    : {worst_drawdown*100:.2f}%")
    print(f"mean profit factor: {mean_profit_factor:.4f}")

    resolution = lineage.resolve_family(args.strategy_id, record)
    counts = overfitting_check.check_project_combination_count(runs_path=args.runs_path)
    n = counts.research_selection_trials
    print(f"\nfamily            : {resolution.family} (source={resolution.source})")
    print(f"project N         : {n}")
    if resolution.source == lineage.UNMAPPED_SOURCE:
        # Fail closed, do not merely warn. An unmapped resolution makes the
        # strategy its own single-member family, which UNDERSTATES N -- and
        # a smaller N inflates DSR, making this gate weaker than 0.95
        # intends. CLAUDE.md's Eligibility Bar clause 2 is explicit that
        # such a DSR "is not admissible as an Eligibility Bar pass", so no
        # verdict of any kind may be reported from here.
        print("\nVERDICT: INADMISSIBLE -- family resolution is 'unmapped', which "
              "understates N and inflates DSR.\nAdd a curated lineage entry (with "
              "its .planning/ citation) or pass strategy_family= at run time, "
              "then re-score.")
        return 1

    # DSR wants per-observation Sharpe on one consistent sampling
    # frequency; every logged `sharpe_ratio` here is annualized, and the
    # bar computes significance on DAILY-resampled returns.
    daily = [
        None if s is None else eligibility.deannualize_sharpe(s, bars_per_day=BARS_PER_DAY)
        for s in sharpes
    ]
    mean_daily = eligibility.deannualize_sharpe(mean_sharpe, bars_per_day=BARS_PER_DAY)
    evaluated_days = sum(
        f["validate_end_index"] - f["validate_start_index"] for f in folds
    ) // BARS_PER_DAY
    dsr = eligibility.evaluate_deflated_sharpe(
        sharpe_ratio=mean_daily,
        num_observations=evaluated_days,
        num_trials=n,
        trial_sharpe_variance=eligibility.sharpe_variance_across_trials(daily),
    )
    result = eligibility.evaluate_eligibility(
        sharpes, min_fold_consistency=MIN_FOLD_CONSISTENCY, deflated_sharpe=dsr
    )

    print("\n--- Eligibility Bar ---")
    fc = result.fold_consistency
    print(f"fold consistency  : {fc.num_positive}/{fc.num_folds} "
          f"({fc.fraction_positive*100:.1f}%) vs {float(MIN_FOLD_CONSISTENCY)*100:.0f}%  "
          f"-> {'PASS' if fc.passed else 'FAIL'}")
    st = result.sign_test
    print(f"sign test         : p={st.p_value:.6g}  -> {'PASS' if st.passed else 'FAIL'}")
    ss = result.sharpe_significance
    print(f"mean-Sharpe t-test: t={ss.t_statistic:+.4f} p={ss.p_value:.6g}  "
          f"-> {'PASS' if ss.passed else 'FAIL'}")
    print(f"DSR               : {dsr.dsr:.6g} vs {DSR_THRESHOLD}  "
          f"-> {'PASS' if dsr.dsr >= DSR_THRESHOLD else 'FAIL'}")

    # `evaluate_eligibility` deliberately covers only fold consistency, the
    # sign test and the mean-Sharpe t-test -- its own docstring says the
    # remaining criteria stay the caller's responsibility. So the verdict
    # must combine both halves; reporting `result.passed` alone would call
    # a run with a sub-floor profit factor a PASS.
    dd = worst_drawdown
    floor = max(30, min(100, int(sum(
        f["validate_end_index"] - f["validate_start_index"] for f in folds) / BARS_PER_DAY / 20)))
    drawdown_ok = dd <= DRAWDOWN_CEILING
    trades_ok = trades >= floor
    pf_ok = mean_profit_factor >= PROFIT_FACTOR_FLOOR
    dsr_ok = dsr.dsr is not None and dsr.dsr >= DSR_THRESHOLD

    print(f"max drawdown      : {dd*100:.2f}% vs {DRAWDOWN_CEILING*100:.0f}%  "
          f"-> {'PASS' if drawdown_ok else 'FAIL'}")
    print(f"trade count       : {trades:,} vs floor {floor}  "
          f"-> {'PASS' if trades_ok else 'INCONCLUSIVE-DATA-LIMITED'}")
    # Report the median beside the mean. A profit factor is a ratio of two
    # non-negative magnitudes, so a fold with almost no losing trades can
    # produce an enormous value and drag the MEAN across the floor on its
    # own. CLAUDE.md sets the floor without naming which statistic it
    # applies to; the mean is what `walkforward` aggregates, so the mean is
    # what is scored -- but a mean that passes while the median does not is
    # a fragile pass and saying so is not optional.
    fold_pf = sorted(
        v for v in (_as_number(f["metrics"].get("profit_factor")) for f in folds)
        if v is not None
    )
    median_pf = statistics.median(fold_pf) if fold_pf else None
    print(f"profit factor     : {mean_profit_factor:.4f} vs {PROFIT_FACTOR_FLOOR}  "
          f"-> {'PASS' if pf_ok else 'FAIL'}")
    if median_pf is not None:
        note = ""
        if pf_ok and median_pf < PROFIT_FACTOR_FLOOR:
            note = ("  ! FRAGILE -- the mean clears the floor but the median fold "
                    "does not, so the pass rests on a few outlier folds")
        print(f"  median fold PF  : {median_pf:.4f}{note}")

    # An under-floor trade count is neither a pass nor a fail: CLAUDE.md
    # requires it be reported as INCONCLUSIVE-DATA-LIMITED and explicitly
    # says such a run "is not evidence against the strategy and must not be
    # written up as such". So it is resolved before the pass/fail verdict,
    # not folded into it.
    if not trades_ok:
        print(f"\nVERDICT: INCONCLUSIVE-DATA-LIMITED "
              f"({trades:,} trades against a floor of {floor})")
        return 0
    overall = result.passed and drawdown_ok and pf_ok and dsr_ok
    print(f"\nVERDICT: {'PASS' if overall else 'REJECTED'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
