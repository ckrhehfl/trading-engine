"""Information coefficient: does a feature actually predict forward returns?

Scalping Strategy Research Task S8 step 3 (see
`.planning/scalp-s8-research-methodology.md` §3.4). **Measures features,
not strategies.** No entry rule, no exit, no sizing, no P&L. The question
is only whether a number known at bar close carries information about
what happens next, which is far cheaper to answer and far harder to
overfit than a full backtest -- a strategy adds entry, exit and sizing
choices that each multiply the search space.

**This is a TIME-SERIES IC, not the cross-sectional one.** The
conventional quant-equity IC correlates a feature across many assets at
one instant. This project trades one symbol, so the correlation runs
across many *instants* for one asset instead. The distinction is not
cosmetic and it matters for Grinold's `IR ~= IC * sqrt(breadth)`: breadth
here comes from independent decisions *in time*, so it is bounded by how
long a bet is held, not by how many symbols are traded. Overlapping
holding periods do not add breadth.

**Rank IC by default.** Spearman rank correlation is the standard choice
and the right one here: crypto returns are fat-tailed, and a single
outlier bar can dominate a Pearson correlation. Pearson is reported
alongside so a large gap between the two -- which means outliers are
doing the work -- is visible rather than hidden.

**Non-overlapping samples.** Sampling every bar while measuring an
`h`-bar forward return makes consecutive observations share `h-1` bars,
which inflates apparent significance badly. `sample_indices` steps by the
horizon instead. Even then observations are *non-overlapping*, not
independent -- volatility clustering guarantees residual serial
dependence -- so the reported t-statistic is an upper bound on
confidence, and `.planning/scalp-s10-regime-classifier.md` records what
happened the last time that distinction was blurred.

**Multiple testing is the point of `benjamini_hochberg`.** Measuring
twenty features against two horizons is forty tests; at alpha=0.05 two
false positives are expected from noise alone. A raw p-value from such a
sweep is not evidence. This mirrors what DSR already does for Sharpe
elsewhere in this project.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Callable, Sequence


@dataclass(frozen=True)
class IcResult:
    """One feature measured at one horizon.

    `rank_ic` is the headline. `pearson_ic` exists to expose outlier
    dependence: when the two disagree substantially, the linear
    correlation is being driven by a handful of extreme bars.

    `t_stat`/`p_value` treat the observations as independent, which they
    are not (see module docstring), so both are optimistic. Use them to
    rank and screen features, never as a final significance claim.
    """

    name: str
    horizon: int
    n: int
    rank_ic: float | None
    pearson_ic: float | None
    t_stat: float | None
    p_value: float | None

    @property
    def is_usable(self) -> bool:
        """S8's own calibration: |IC| of 0.02-0.05 is a genuinely useful
        signal, so anything below 0.02 is not worth carrying regardless of
        its p-value."""
        return self.rank_ic is not None and abs(self.rank_ic) >= 0.02


def _ranks(values: Sequence[float]) -> list[float]:
    """Ranks with ties averaged -- required for Spearman to stay correct
    on features that take few distinct values (a session label, a boolean
    flag), which would otherwise get arbitrary tie-breaking."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """`None` -- never 0.0 -- when there are fewer than three pairs or
    either side has zero variance. Zero would claim "measured, no
    relationship"; `None` says "not measurable", the same convention
    `metrics.metrics` uses for its own degenerate inputs."""
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sxx = syy = 0.0
    for x, y in zip(xs, ys):
        dx, dy = x - mx, y - my
        sxy += dx * dy
        sxx += dx * dx
        syy += dy * dy
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Rank correlation, ties averaged. Same `None` contract as `pearson`."""
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    return pearson(_ranks(xs), _ranks(ys))


def sample_indices(total_bars: int, horizon: int, warmup: int = 0) -> list[int]:
    """Indices stepping by `horizon` so forward windows never overlap.

    Starts at `warmup` (the first bar whose feature is defined) and stops
    early enough that `i + horizon` is always a real bar.
    """
    if horizon <= 0:
        raise ValueError(f"horizon must be positive, got {horizon}")
    if warmup < 0:
        raise ValueError(f"warmup must not be negative, got {warmup}")
    return list(range(warmup, max(warmup, total_bars - horizon), horizon))


def measure_ic(
    name: str,
    feature: Sequence[float | None],
    closes: Sequence[float],
    horizon: int,
    warmup: int = 0,
) -> IcResult:
    """Correlate `feature[i]` against the forward return from `i` to
    `i + horizon`, over non-overlapping samples.

    Bars whose feature is `None` (warmup, or a degenerate input the
    feature itself refused to score) are skipped rather than imputed --
    substituting a zero or a mean would invent data and bias the
    correlation toward whatever was substituted.
    """
    xs: list[float] = []
    ys: list[float] = []
    for i in sample_indices(len(closes), horizon, warmup):
        f = feature[i]
        if f is None:
            continue
        base = closes[i]
        if base <= 0:
            continue
        xs.append(float(f))
        ys.append((closes[i + horizon] - base) / base)

    rank_ic = spearman(xs, ys)
    lin_ic = pearson(xs, ys)
    t_stat = p_value = None
    if rank_ic is not None and len(xs) > 2 and abs(rank_ic) < 1.0:
        t_stat = rank_ic * math.sqrt((len(xs) - 2) / (1 - rank_ic**2))
        # Two-sided, normal approximation to the t distribution. Fine at
        # the sample sizes here (thousands); the dependence caveat in the
        # module docstring dominates any error from the approximation.
        p_value = 2 * (1 - NormalDist().cdf(abs(t_stat)))
    return IcResult(
        name=name,
        horizon=horizon,
        n=len(xs),
        rank_ic=rank_ic,
        pearson_ic=lin_ic,
        t_stat=t_stat,
        p_value=p_value,
    )


def benjamini_hochberg(p_values: Sequence[float | None], alpha: float = 0.05) -> list[bool]:
    """Which hypotheses survive at false-discovery-rate `alpha`.

    Benjamini-Hochberg rather than Bonferroni: with dozens of correlated
    features Bonferroni is so conservative it would reject everything
    including real effects, and controlling the *proportion* of false
    discoveries is the honest target when screening a feature library.

    A `None` p-value (unmeasurable feature) is never a discovery.
    """
    if not 0 < alpha < 1:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    indexed = [(p, i) for i, p in enumerate(p_values) if p is not None]
    out = [False] * len(p_values)
    if not indexed:
        return out
    indexed.sort()
    m = len(indexed)
    cutoff_rank = 0
    for rank, (p, _) in enumerate(indexed, start=1):
        if p <= alpha * rank / m:
            cutoff_rank = rank
    for rank, (_, original) in enumerate(indexed, start=1):
        if rank <= cutoff_rank:
            out[original] = True
    return out


def measure_all(
    features: dict[str, Sequence[float | None]],
    closes: Sequence[float],
    horizons: Sequence[int],
    warmup: int = 0,
    alpha: float = 0.05,
) -> list[IcResult]:
    """Every feature against every horizon, with one Benjamini-Hochberg
    correction applied across the **whole sweep** -- not per horizon.

    Correcting per horizon would understate the number of tests actually
    run and is exactly the loophole the correction exists to close.
    Results come back sorted by |rank IC| descending, with a
    `survives_fdr` flag attached via `IcSweep`.
    """
    results = [
        measure_ic(name, values, closes, horizon, warmup)
        for name, values in features.items()
        for horizon in horizons
    ]
    flags = benjamini_hochberg([r.p_value for r in results], alpha=alpha)
    return [
        IcSweep(result=r, survives_fdr=flag)
        for r, flag in sorted(
            zip(results, flags),
            key=lambda pair: abs(pair[0].rank_ic) if pair[0].rank_ic is not None else -1.0,
            reverse=True,
        )
    ]


@dataclass(frozen=True)
class IcSweep:
    """An `IcResult` plus whether it survived multiple-testing correction
    across the sweep it was measured in. A feature is only interesting
    when it clears **both** bars: `is_usable` (large enough to matter) and
    `survives_fdr` (unlikely to be noise from the sweep)."""

    result: IcResult
    survives_fdr: bool

    @property
    def is_interesting(self) -> bool:
        return self.result.is_usable and self.survives_fdr


def conditional_ic(
    name: str,
    feature: Sequence[float | None],
    closes: Sequence[float],
    horizon: int,
    conditioner: Sequence[float | None],
    quantile: float,
    warmup: int = 0,
) -> IcResult:
    """`measure_ic` restricted to bars in the top `quantile` of
    `conditioner`.

    Exists because Task S9/S10 established that forward movement only
    clears trading costs in elevated-activity moments, so a feature's IC
    over all bars can hide a real effect that only lives where trading is
    viable -- or flatter itself with bars nobody would trade.

    **The quantile threshold is computed over the whole series**, which is
    look-ahead. That is acceptable for measuring *whether* a feature
    separates, matching how the S8/S9 activity buckets were computed, and
    is not acceptable for a deployable rule -- which needs a trailing
    reference, as `regime_classifier.AbsoluteAtr` implements.
    """
    if not 0 <= quantile < 1:
        raise ValueError(f"quantile must be in [0, 1), got {quantile}")
    scored = sorted(c for c in conditioner if c is not None)
    if not scored:
        return IcResult(name, horizon, 0, None, None, None, None)
    threshold = scored[min(int(len(scored) * quantile), len(scored) - 1)]
    masked: list[float | None] = [
        f if (c is not None and c >= threshold) else None
        for f, c in zip(feature, conditioner)
    ]
    return measure_ic(name, masked, closes, horizon, warmup)


def format_sweep(sweeps: Sequence[IcSweep], min_abs_ic: float = 0.0) -> str:
    """Human-readable table. `min_abs_ic` hides the long tail of nothing."""
    lines = [
        f"{'feature':>34} {'h':>5} {'n':>9} {'rank IC':>9} {'pearson':>9} {'p':>10}  flags",
        "-" * 92,
    ]
    for s in sweeps:
        r = s.result
        if r.rank_ic is None or abs(r.rank_ic) < min_abs_ic:
            continue
        flags = []
        if r.is_usable:
            flags.append("USABLE")
        if s.survives_fdr:
            flags.append("FDR-OK")
        lines.append(
            f"{r.name:>34} {r.horizon:>5} {r.n:>9,} {r.rank_ic:>9.4f} "
            f"{(r.pearson_ic if r.pearson_ic is not None else float('nan')):>9.4f} "
            f"{(r.p_value if r.p_value is not None else float('nan')):>10.2e}  {' '.join(flags)}"
        )
    return "\n".join(lines)
