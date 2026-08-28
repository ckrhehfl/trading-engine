"""Mechanical checks a research conclusion must pass before it is reported.

Every check here exists because this project made that exact mistake, and
each one names it. That is the design constraint: a checklist of things
that *sound* like good practice becomes theatre nobody runs. A checklist
where every item has a scar attached gets run, because the cost of
skipping it is already known.

Scope, stated so this is not mistaken for more than it is. These checks
catch **arithmetic and bookkeeping errors in a conclusion's own
evidence** -- overlapping samples reported as independent, a domain
judged from one parameter setting, a criterion that cannot be met at the
sample size in hand, two statistics computed over different populations
and compared, a prose claim that contradicts its own table, and a DSR
that disagrees with the reference implementation.

They cannot catch **asking the wrong question**. Every one of this
project's largest errors that was caught by a human -- comparing costs to
an *unconditional* move distribution, testing a *directional* hypothesis
by measuring *magnitude*, running a strategy at 57 trades/day and calling
it "scalping" -- would pass all six of these cleanly. Those need someone
asking whether the measurement matches the world, which no assertion in
this file does.

## Usage

    from research.conclusion_check import (
        check_non_overlapping, check_parameter_swept, require_no_blockers,
    )

    findings = [
        check_non_overlapping(entry_indices, hold_bars=60),
        check_parameter_swept(records, parameter="entry_z"),
    ]
    require_no_blockers(findings)   # raises on anything severity="blocker"

`require_no_blockers` raises rather than warns, deliberately. A warning
printed above a conclusion is read as decoration; this project has the
evidence.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Callable, Mapping

BLOCKER = "blocker"
WARNING = "warning"

# The plausible per-fold win probability of a genuinely good strategy.
# Not a tuning knob: it is the "is this criterion reachable at all"
# reference point, and 0.60 is deliberately generous -- a strategy whose
# folds are positive 60% of the time is a good one.
PLAUSIBLE_FOLD_WIN_RATE = 0.60

# Below this, a criterion is judged unreachable rather than merely strict:
# if a good strategy clears it less than 5% of the time, passing it is
# mostly a statement about luck.
MIN_CRITERION_POWER = 0.05


@dataclass(frozen=True)
class Finding:
    """One failed check. `scar` cites the real incident that motivates it,
    so a reader can decide whether the check still applies rather than
    obeying it on faith."""

    check: str
    severity: str
    message: str
    scar: str

    def __str__(self) -> str:
        mark = "BLOCKER" if self.severity == BLOCKER else "warning"
        return f"[{mark}] {self.check}: {self.message}\n    ({self.scar})"


class ConclusionCheckError(AssertionError):
    """Raised by `require_no_blockers`. An `AssertionError` subclass so it
    reads as a broken invariant rather than a runtime failure."""


def require_no_blockers(findings: Iterable[Finding | None]) -> list[Finding]:
    """Raise if any finding is a blocker; return the surviving warnings.

    Raises rather than prints. A warning above a conclusion gets read as
    decoration -- S13's inflated t-statistic was disclosed in prose and
    still ended up in CLAUDE.md as a headline number.
    """
    real = [f for f in findings if f is not None]
    blockers = [f for f in real if f.severity == BLOCKER]
    if blockers:
        raise ConclusionCheckError(
            "conclusion blocked by "
            f"{len(blockers)} check(s):\n\n" + "\n\n".join(str(b) for b in blockers)
        )
    return [f for f in real if f.severity == WARNING]


def format_findings(findings: Iterable[Finding | None]) -> str:
    real = [f for f in findings if f is not None]
    if not real:
        return "all conclusion checks passed"
    return "\n\n".join(str(f) for f in real)


# --------------------------------------------------------------------------
# 1. Overlapping samples reported as independent
# --------------------------------------------------------------------------

def check_non_overlapping(
    starts: Sequence[int], *, hold_bars: int, check: str = "non_overlapping"
) -> Finding | None:
    """Every position's holding window must be disjoint from the others'.

    A statistic over overlapping windows is not a statistic over
    independent observations, and the inflation is not small.
    """
    if hold_bars < 0:
        raise ValueError(f"hold_bars must be non-negative, got {hold_bars}")
    ordered = sorted(starts)
    overlaps = sum(
        1 for a, b in zip(ordered, ordered[1:]) if b - a <= hold_bars
    )
    if not overlaps:
        return None
    return Finding(
        check=check,
        severity=BLOCKER,
        message=(
            f"{overlaps:,} of {len(ordered):,} positions start within {hold_bars} "
            f"bars of the previous one, so their holding windows overlap. Any t, "
            f"p-value or standard error over this sample assumes independence it "
            f"does not have. Deduplicate to non-overlapping positions first "
            f"(see research.analysis.s13_selectivity_sweep.non_overlapping), or "
            f"state explicitly that the figures are uncorrected for overlap."
        ),
        scar=(
            "S13 reported t = 7.0-8.0 over overlapping 60-minute excursions. "
            "Deduplicated, t was 1.50-2.56 and the mean fell 25-60%. The tool "
            "to prevent this (research/ic.py) already existed and was not used."
        ),
    )


# --------------------------------------------------------------------------
# 2. A domain judged from one parameter setting
# --------------------------------------------------------------------------

def check_parameter_swept(
    records: Iterable[Mapping[str, Any]],
    *,
    parameter: str,
    strategy_id: str | None = None,
    family: str | None = None,
    minimum: int = 2,
    check: str = "parameter_swept",
) -> Finding | None:
    """A conclusion about a *domain* needs more than one setting tested.

    Counts the distinct values of `params[parameter]` across the logged
    records this conclusion rests on. One distinct value means the
    conclusion describes that setting, not the domain.
    """
    if minimum < 1:
        raise ValueError(f"minimum must be at least 1, got {minimum}")
    seen: set[str] = set()
    considered = 0
    for record in records:
        if not isinstance(record, Mapping):
            continue
        if strategy_id is not None and record.get("strategy_id") != strategy_id:
            continue
        if family is not None and record.get("strategy_family") != family:
            continue
        params = record.get("params")
        if not isinstance(params, Mapping) or parameter not in params:
            continue
        considered += 1
        seen.add(str(params[parameter]))
    if considered == 0:
        return Finding(
            check=check,
            severity=BLOCKER,
            message=(
                f"no logged record carries params[{parameter!r}], so it is not "
                f"possible to tell how many settings were tested. A conclusion "
                f"about {parameter!r} cannot be supported from this log."
            ),
            scar=(
                "S14 and S15 both concluded about the scalping signal without "
                "the record showing which entry_z values had been tried."
            ),
        )
    if len(seen) >= minimum:
        return None
    return Finding(
        check=check,
        severity=BLOCKER,
        message=(
            f"only {len(seen)} distinct value(s) of {parameter!r} were tested "
            f"({', '.join(sorted(seen))}) across {considered} run(s), against a "
            f"minimum of {minimum}. A result from one setting describes that "
            f"setting. Sweep the parameter before concluding about the domain, "
            f"or restate the conclusion as being about this setting only."
        ),
        scar=(
            "S14/S15 walk-forwarded only entry_z=5.0 and declared the signal "
            "absent. entry_z=6.0, never run, gave +0.899 mean fold Sharpe and "
            "+32.42% compounded against 5.0's -0.333 and -50.6%. This was the "
            "fifth instance of the same shape in one research arc."
        ),
    )


# --------------------------------------------------------------------------
# 3. A criterion that cannot be met at this sample size
# --------------------------------------------------------------------------

def check_criterion_attainable(
    *,
    num_folds: int,
    required_fraction: float,
    trades_per_fold: float | None = None,
    plausible_win_rate: float = PLAUSIBLE_FOLD_WIN_RATE,
    min_power: float = MIN_CRITERION_POWER,
    check: str = "criterion_attainable",
) -> Finding | None:
    """Would a genuinely good strategy ever clear this fold criterion?

    If a strategy whose folds are positive `plausible_win_rate` of the
    time clears the bar less than `min_power` of the time, the bar is
    measuring luck rather than edge, and reporting a failure against it is
    misleading in both directions.
    """
    if num_folds < 1:
        raise ValueError(f"num_folds must be at least 1, got {num_folds}")
    if not 0 < required_fraction <= 1:
        raise ValueError(f"required_fraction must be in (0, 1], got {required_fraction}")
    required = math.ceil(required_fraction * num_folds)
    power = sum(
        math.comb(num_folds, k) * plausible_win_rate**k * (1 - plausible_win_rate) ** (num_folds - k)
        for k in range(required, num_folds + 1)
    )
    if power >= min_power:
        return None
    thin = (
        f" At a median of {trades_per_fold:g} trades per fold a fold's sign is "
        f"close to a coin flip, which is why."
        if trades_per_fold is not None
        else ""
    )
    return Finding(
        check=check,
        severity=BLOCKER,
        message=(
            f"a strategy whose folds are positive {plausible_win_rate:.0%} of the "
            f"time -- a good edge -- clears {required}/{num_folds} "
            f"({required_fraction:.0%}) with probability {power:.2e}, below the "
            f"{min_power:.0%} floor.{thin} Do not report this criterion as "
            f"evidence in either direction; use the aggregate result instead."
        ),
        scar=(
            "S15 offered '45.8% of folds positive against an 80% floor' as "
            "evidence for a strategy holding a median of 2-6 trades per fold. "
            "sr-j had already made this argument for fold counts; it had not "
            "been made for trades per fold."
        ),
    )


# --------------------------------------------------------------------------
# 4. Two statistics compared across different populations
# --------------------------------------------------------------------------

def check_same_population(
    samples: Mapping[str, Sequence[Any]], *, check: str = "same_population"
) -> Finding | None:
    """Figures presented side by side must be computed over one population.

    Pass the actual samples each headline figure was computed from. A
    difference in size means the comparison is between two different
    things, whatever the labels say.
    """
    sizes = {name: len(values) for name, values in samples.items()}
    if len(set(sizes.values())) <= 1:
        return None
    listed = ", ".join(f"{name}={size:,}" for name, size in sorted(sizes.items()))
    return Finding(
        check=check,
        severity=BLOCKER,
        message=(
            f"figures presented for comparison were computed over samples of "
            f"different sizes ({listed}). Whatever the labels say, the "
            f"comparison is between two different populations. Restrict every "
            f"figure to the common subset, and report how many observations "
            f"that excludes."
        ),
        scar=(
            "S15's stop table averaged the 'no stop' row over every position "
            "while the stop rows excluded positions with no MAE reading -- in a "
            "table whose only purpose was comparing those two."
        ),
    )


# --------------------------------------------------------------------------
# 5. A prose claim that contradicts its own numbers
# --------------------------------------------------------------------------

def check_claim_monotonic(
    values: Sequence[float],
    *,
    direction: str,
    claim: str = "monotonic",
    check: str = "claim_monotonic",
) -> Finding | None:
    """Verify a "monotonically" claim against the numbers it describes.

    `direction` is `"increasing"` or `"decreasing"`. Call this whenever
    the word appears in a conclusion; if it is not worth calling, the word
    is not worth writing.
    """
    if direction not in ("increasing", "decreasing"):
        raise ValueError(f"direction must be 'increasing' or 'decreasing', got {direction!r}")
    pairs = list(zip(values, values[1:]))
    if direction == "increasing":
        breaks = [(i, a, b) for i, (a, b) in enumerate(pairs) if b < a]
    else:
        breaks = [(i, a, b) for i, (a, b) in enumerate(pairs) if b > a]
    if not breaks:
        return None
    shown = "; ".join(f"index {i}->{i + 1}: {a:g} -> {b:g}" for i, a, b in breaks[:3])
    more = f" (and {len(breaks) - 3} more)" if len(breaks) > 3 else ""
    return Finding(
        check=check,
        severity=BLOCKER,
        message=(
            f"the claim {claim!r} ({direction}) is contradicted by the series it "
            f"describes: {shown}{more}. State the weaker claim the numbers "
            f"actually support."
        ),
        scar=(
            "S15 wrote 'every delay tested is worse, monotonically' in both the "
            "planning doc and CLAUDE.md. Its own table showed +30 bars at "
            "-2.18bps and +60 bars at +3.93."
        ),
    )


def check_claim_universal(
    values: Sequence[Any],
    predicate: Callable[[Any], bool],
    *,
    claim: str,
    check: str = "claim_universal",
) -> Finding | None:
    """Verify an "every"/"all"/"none" claim against its own data."""
    failures = [v for v in values if not predicate(v)]
    if not failures:
        return None
    shown = ", ".join(repr(v) for v in failures[:3])
    more = f" (and {len(failures) - 3} more)" if len(failures) > 3 else ""
    return Finding(
        check=check,
        severity=BLOCKER,
        message=(
            f"the universal claim {claim!r} fails for {len(failures)} of "
            f"{len(values)} values: {shown}{more}."
        ),
        scar=(
            "Universal words are where this project's overclaims concentrate: "
            "'every delay tested is worse, monotonically' and 'minutes-scale "
            "scalping is arithmetically impossible' were both stated as "
            "universals over evidence that did not cover the range."
        ),
    )


# --------------------------------------------------------------------------
# 6. A DSR that disagrees with the reference implementation
# --------------------------------------------------------------------------

def check_dsr_agrees(
    *,
    computed: float | None,
    reference: float | None,
    relative_tolerance: float = 1e-6,
    check: str = "dsr_agrees",
) -> Finding | None:
    """A locally computed DSR must match `research/retrospective.py`.

    There is one correct way to feed this statistic and several plausible
    wrong ones, none visible at a glance. If a second implementation
    exists at all, it must be reconciled against the reference every time.
    """
    if computed is None or reference is None:
        if computed is reference:
            return None
        return Finding(
            check=check,
            severity=BLOCKER,
            message=(
                f"one DSR is undefined and the other is not (computed={computed}, "
                f"reference={reference}); they cannot describe the same run."
            ),
            scar="See below.",
        )
    if reference == 0:
        agrees = computed == 0
    else:
        agrees = abs(computed - reference) <= relative_tolerance * abs(reference)
    if agrees:
        return None
    return Finding(
        check=check,
        severity=BLOCKER,
        message=(
            f"locally computed DSR {computed:.6g} disagrees with "
            f"research/retrospective.py's {reference:.6g}. Reconcile the inputs "
            f"or delete the second implementation and read the reference's row."
        ),
        scar=(
            "s14_eligibility.py fed trial_sharpe_variance its own run's PER-FOLD "
            "Sharpes rather than the across-TRIAL variance, inflating the "
            "selection benchmark and pushing every DSR it reported toward zero. "
            "Correcting the input was not enough -- the second implementation "
            "still disagreed on which purposes to pool -- so it now delegates."
        ),
    )
