"""Task S15(b): what risk control survives a mean-reversion edge that
lives inside its own adverse excursion?

    PYTHONPATH=python python/.venv/bin/python -m research.analysis.s15_risk_control

S14 applied a **2.65 ATR** stop, taken from S12's winners' MAE 80th
percentile -- which S12 measured at `|z|>=2` in the top 10% of activity
(57 trades/day), while S14 traded `|z|>=5..6` in the top 1%
(0.1-0.2/day), a population ~29x more selective. The obvious hypothesis
was that the stop simply came from the wrong population.

**That hypothesis was tested here and is wrong, and the fact is left in
this file rather than deleted.** Re-running S12's own `recommend_stop` on
the correct population gives **2.71 ATR** (`|z|>=5`) and **3.36 ATR**
(`|z|>=6`) -- essentially S14's 2.65. The winners' MAE p80 barely moves
between the two populations. What does move is the *mean* MAE over all
positions (4.65 ATR), and that is dominated by losers, whose median MAE
is 5.0-5.4 ATR. Confusing the two is what made the hypothesis look
plausible.

So the real question is not the stop's width. It is whether a stop of
**any** width helps this signal, which needs a different measurement: not
"how many winners does it destroy", but **what does the stop realise on
the positions it catches, versus what those positions actually did
without it.** A stop that catches a position heading for -20bps and
realises -120bps has manufactured a loss, not avoided one.

Every figure is over **non-overlapping** positions.

Measurement only. `run_backtest` is not called, nothing is logged, and no
configuration is selected. Adopting a row would be a selection and needs
its own logged walk-forward run.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys

from research.analysis.s12_excursion_run import COST_BPS, DEFAULT_DB, MAX_HOLD, SYMBOL
from research.analysis.s13_selectivity_sweep import build_series, load, non_overlapping, run_cell
from research.excursion import recommend_stop

CELLS = [(5.0, 0.99), (6.0, 0.99)]
CANDIDATE_STOPS = [1.5, 2.65, 4.0, 6.0, 8.0, 12.0]


def _quantile(sorted_values, q):
    if not sorted_values:
        return None
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def stop_effect(indep, stop_atr):
    """What a stop at `stop_atr` does to this sample.

    Approximate by construction and labelled as such: a real stop changes
    the exit, and this cannot know what the stopped position would have
    done afterwards. It reports what IS knowable -- how many winners and
    losers the stop would have caught, and what the outcome is over the
    positions it leaves untouched -- which is enough to see whether a stop
    is affordable at all. It is not a substitute for a backtest.
    """
    survived, cut_winners, cut_losers = [], 0, 0
    for _, e in indep:
        if e.mae_atr is not None and e.mae_atr >= stop_atr:
            if e.is_winner:
                cut_winners += 1
            else:
                cut_losers += 1
        else:
            survived.append(e)
    winners = sum(1 for _, e in indep if e.is_winner)
    losers = len(indep) - winners
    caught = [e for _, e in indep if e.mae_atr is not None and e.mae_atr >= stop_atr]
    return {
        "survived": survived,
        "caught": caught,
        "winners_cut": cut_winners / winners if winners else None,
        "losers_cut": cut_losers / losers if losers else None,
        "stopped": cut_winners + cut_losers,
    }


def stop_loss_bps(caught, stop_atr):
    """What the stop actually realises on the positions it catches, versus
    what those same positions did without it.

    This is the number that decides whether a stop helps. A position the
    stop catches is not automatically a position the stop saved: if it
    would have finished at -20bps and the stop realises -120bps, the stop
    manufactured a loss rather than avoiding one.

    The realised loss is `stop_atr` expressed in bps, recoverable per
    position from `mae_bps / mae_atr` -- the ATR at that entry, in bps.
    """
    realised, actual = [], []
    for e in caught:
        if e.mae_atr in (None, 0) or e.mae_bps is None:
            continue
        atr_bps = e.mae_bps / e.mae_atr
        realised.append(-(stop_atr * atr_bps))
        actual.append(e.outcome_gross_bps)
    if not realised:
        return None, None
    return statistics.mean(realised), statistics.mean(actual)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db-path", default=DEFAULT_DB)
    args = ap.parse_args(argv)

    rows = load(args.db_path)
    if not rows:
        print(f"no {SYMBOL} 1m bars in {args.db_path}", file=sys.stderr)
        return 1
    times, ohlc, atr, rank, score = build_series(rows)
    print(f"{SYMBOL}: {len(rows):,} bars, {MAX_HOLD}m hold, {COST_BPS:.0f}bps round trip")
    print("All statistics over NON-OVERLAPPING positions.\n")

    for z, q in CELLS:
        cell = run_cell(z, q, times, ohlc, atr, rank, score, MAX_HOLD)
        indep = non_overlapping(cell, MAX_HOLD)
        winner_mae = sorted(e.mae_atr for _, e in indep if e.is_winner and e.mae_atr is not None)
        loser_mae = sorted(e.mae_atr for _, e in indep if e.is_loser and e.mae_atr is not None)
        gross = [e.outcome_gross_bps for _, e in indep]

        print(f"=== |z|>={z:g}, top {(1-q)*100:g}%  --  {len(indep):,} independent positions ===")
        print(f"  gross mean {statistics.mean(gross):+.2f}bps   "
              f"winners {len(winner_mae):,}  losers {len(loser_mae):,}")
        # `recommend_stop` is S12's own machinery, re-run on THIS population
        # rather than reusing S12's number -- which is the entire point.
        rec = recommend_stop([e for _, e in indep], unit="atr")
        print(f"  winners' MAE  p50 {_quantile(winner_mae, 0.50):.2f}  "
              f"p80 {_quantile(winner_mae, 0.80):.2f}  p90 {_quantile(winner_mae, 0.90):.2f} ATR")
        print(f"  losers'  MAE  p50 {_quantile(loser_mae, 0.50):.2f} ATR")
        print(f"  recommend_stop on this population: p80 = "
              f"{'n/a' if rec.winner_mae_p80 is None else f'{rec.winner_mae_p80:.2f}'} ATR "
              f"(S14 used 2.65, measured at |z|>=2 top 10%)")

        hdr = (f"  {'stop':>7} {'win cut':>8} {'lose cut':>9} {'stopped':>8} "
               f"{'stop takes':>11} {'they did':>10} {'verdict':>9} {'ALL-IN mean':>12}")
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for stop in CANDIDATE_STOPS:
            eff = stop_effect(indep, stop)
            surv, caught = eff["survived"], eff["caught"]
            realised, actual = stop_loss_bps(caught, stop)
            # Portfolio outcome WITH the stop: survivors keep their real
            # outcome, everything the stop caught realises the stop loss.
            with_stop = None
            if realised is not None and indep:
                total = sum(e.outcome_gross_bps for e in surv) + realised * len(caught)
                with_stop = total / len(indep)
            wc = "n/a" if eff["winners_cut"] is None else f"{eff['winners_cut']*100:>5.1f}%"
            lc = "n/a" if eff["losers_cut"] is None else f"{eff['losers_cut']*100:>5.1f}%"
            r_txt = "n/a" if realised is None else f"{realised:>8.1f}"
            a_txt = "n/a" if actual is None else f"{actual:>7.1f}"
            verdict = "n/a" if realised is None else ("HELPS" if realised > actual else "HURTS")
            w_txt = "n/a" if with_stop is None else f"{with_stop:>9.2f}bps"
            mark = "  <- S14" if abs(stop - 2.65) < 1e-9 else ""
            print(f"  {stop:>7.2f} {wc:>8} {lc:>9} {eff['stopped']:>8,} "
                  f"{r_txt:>11} {a_txt:>10} {verdict:>9} {w_txt:>12}{mark}")
        no_stop = statistics.mean(gross)
        print(f"  {'none':>7} {'0.0%':>8} {'0.0%':>9} {0:>8,} "
              f"{'--':>11} {'--':>10} {'--':>9} {no_stop:>9.2f}bps")
        print()

    print("'stop takes' is what the stop realises on a caught position;")
    print("'they did' is what those same positions actually returned without")
    print("it. HURTS means the stop realises a BIGGER loss than the position")
    print("would have taken on its own -- it manufactured the loss rather")
    print("than avoiding it. 'ALL-IN mean' is the portfolio outcome with the")
    print("stop applied (survivors at their real outcome, caught positions at")
    print("the stop loss), directly comparable to the 'none' row.")
    print()
    print("Still approximate in one direction, stated rather than hidden: a")
    print("stopped position is assumed not to re-enter, and MAE order within")
    print("a bar is unknown. It cannot replace a backtest -- but the sign of")
    print("the comparison is not close enough for that to change it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
