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


def _load(runs_path: str) -> dict:
    # Parse every line and match `strategy_id` as a field. A substring test
    # on the raw line would also match a record that merely mentions this id
    # somewhere else (a `params` value, a family name, a future field), and
    # scoring the wrong record is worse than scoring none.
    records = []
    for line in Path(runs_path).read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if isinstance(record, dict) and record.get("strategy_id") == STRATEGY_ID:
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
    if not walk_forward:
        print(
            f"no logged walk-forward record for {STRATEGY_ID} in {runs_path}: "
            f"{len(records)} record(s) carry that strategy_id, none with a "
            f"non-empty fold_results and an aggregate_metrics providing all of "
            f"{', '.join(REQUIRED_AGGREGATE_KEYS)}.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return walk_forward[-1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs-path", default=RUNS_PATH)
    args = ap.parse_args(argv)

    record = _load(args.runs_path)
    folds = record["fold_results"]
    agg = record["aggregate_metrics"]
    sharpes = [f["metrics"]["sharpe_ratio"] for f in folds]
    trades = agg["total_trades"]

    print(f"run_id            : {record['run_id']}")
    print(f"folds             : {len(folds)}")
    print(f"total trades      : {trades:,}")
    print(f"mean fold Sharpe  : {agg['mean_sharpe']:+.4f}")
    print(f"worst drawdown    : {float(agg['worst_fold_max_drawdown'])*100:.2f}%")
    print(f"mean profit factor: {agg['mean_profit_factor']:.4f}")

    resolution = lineage.resolve_family(STRATEGY_ID, record)
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
    mean_daily = eligibility.deannualize_sharpe(agg["mean_sharpe"], bars_per_day=BARS_PER_DAY)
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
    dd = float(agg["worst_fold_max_drawdown"])
    floor = max(30, min(100, int(sum(
        f["validate_end_index"] - f["validate_start_index"] for f in folds) / BARS_PER_DAY / 20)))
    drawdown_ok = dd <= DRAWDOWN_CEILING
    trades_ok = trades >= floor
    pf_ok = agg["mean_profit_factor"] >= PROFIT_FACTOR_FLOOR
    dsr_ok = dsr.dsr is not None and dsr.dsr >= DSR_THRESHOLD

    print(f"max drawdown      : {dd*100:.2f}% vs {DRAWDOWN_CEILING*100:.0f}%  "
          f"-> {'PASS' if drawdown_ok else 'FAIL'}")
    print(f"trade count       : {trades:,} vs floor {floor}  "
          f"-> {'PASS' if trades_ok else 'INCONCLUSIVE-DATA-LIMITED'}")
    print(f"profit factor     : {agg['mean_profit_factor']:.4f} vs {PROFIT_FACTOR_FLOOR}  "
          f"-> {'PASS' if pf_ok else 'FAIL'}")

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
