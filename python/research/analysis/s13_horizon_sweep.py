"""Task S13: does the gross mean outcome grow with holding period fast
enough to clear the fee?

    PYTHONPATH=python python/.venv/bin/python -m research.analysis.s13_horizon_sweep

The last remaining question in the S8 work order.

Two remedies are already ruled out -- maker execution (S12: +0.95bps
gross is under half a 2bps maker round trip) and signal combination
(Grinold's sqrt(3)/sqrt(2) gives ~1.16bps against a 10bps fee). Holding
period is the third, and it needs a >10x improvement.

Reuses the committed S12 runner's entry construction unchanged, varying
only the holding period -- over a sample held FIXED across horizons, so
the table varies one thing rather than two. Measurement only;
already-spent window; no holdout.

Reports no significance statistic, deliberately: these positions
overlap, and an i.i.d. standard error over overlapping windows is not a
significance test (Task S14).
"""
import statistics

from research.analysis import s12_excursion_run as s12  # noqa: E402
from research.excursion import measure_excursion  # noqa: E402

HORIZONS = [15, 30, 60, 120, 240, 480]
FEE_ONLY = 10.0
FULL_COST = 12.0
MAKER_COST = 2.0

rows = s12.load(s12.DEFAULT_DB)
entries, (opens, highs, lows, closes), atr = s12.build_entries(rows)

# Fix the sample ONCE, to entries that can complete the LONGEST horizon.
# Otherwise each horizon scores a different (and progressively smaller)
# set of entries, and the table compares horizons against each other
# while silently also varying which trades are in it -- so a "longer
# holds do worse" reading could be entirely a change of sample.
LONGEST = max(HORIZONS)
last_usable = len(closes) - LONGEST - 2
eligible = [(i, side) for i, side in entries if i <= last_usable]
print(f"{s12.SYMBOL}: {len(rows):,} bars, {len(entries):,} entries, "
      f"{len(eligible):,} usable at every horizon up to {LONGEST}m\n"
      f"(entry rule identical to S12; only the holding period varies, "
      f"and the sample is held fixed across horizons)\n")

hdr = (f"{'hold':>8} {'positions':>10} {'win%':>7} {'gross':>10} "
       f"{'net@12':>10} {'vs fee':>8} {'vs maker':>9}")
print(hdr)
print("-" * len(hdr))
for h in HORIZONS:
    ex = [e for e in (measure_excursion(i, s, opens, highs, lows, closes, h, FULL_COST, atr[i])
                      for i, s in eligible) if e is not None and not e.censored]
    if not ex:
        continue
    gross = statistics.mean(e.outcome_gross_bps for e in ex)
    win = sum(1 for e in ex if e.is_winner) / len(ex) * 100
    label = f"{h}m" if h < 60 else f"{h//60}h"
    print(f"{label:>8} {len(ex):>10,} {win:>6.1f}% {gross:>7.2f}bps "
          f"{gross-FULL_COST:>7.2f}bps {gross/FEE_ONLY:>7.2f}x {gross/MAKER_COST:>8.2f}x")

print(f"\nfee alone {FEE_ONLY:.0f}bps | full round trip {FULL_COST:.0f}bps | "
      f"maker round trip {MAKER_COST:.0f}bps")
print("'vs fee' and 'vs maker' are gross / cost -- above 1.00 means the")
print("holding period alone would cover that cost.")
print("\nThese are OVERLAPPING positions, so no standard error or t is")
print("reported here: an i.i.d. formula over overlapping windows is not a")
print("significance test. See s13_selectivity_sweep.py's `non_overlapping`.")
