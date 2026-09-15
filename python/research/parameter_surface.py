"""Is the winner a plateau or a spike — and which cells were never run?

**Adopted 2026-09-15 from an outside practitioner write-up** on building
automated strategies with an AI coding agent
(`.planning/rd-i-what-was-adopted-from-outside.md`). Its one genuinely
new idea for this project: MetaTrader5's optimizer draws the parameter
surface, so *"the peak is a spike, not a hill"* is something you **see**.
This project asserts the same rule in prose — CLAUDE.md's *"never
conclude about a domain from one parameter setting — sweep it first"* —
and had no way to look at it.

**Two things make this more than a copy of that idea, and both come from
this project's own incidents:**

1. **The verdict is computed, not eyeballed.** A surface you squint at is
   a judgement call; `plateau_ratio` is a number a test can pin.
2. **Coverage is reported.** S16's most expensive error was concluding
   *"the signal is not there"* from `entry_z=5.0` while `|z|>=6` had
   never been run at all — and reversing the sign when it finally was.
   A hole in the grid is invisible in a heatmap and obvious in a
   coverage count, so the count is the headline, not the picture.

Reads `runs/experiments.jsonl` only. **It scores nothing, runs no
backtest, and touches no window** — every number here was already logged,
so this spends no `N`.

Run:

    python -m research.parameter_surface --strategy single-lookback-momentum
    python -m research.parameter_surface --list
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

#: Resolved from this file rather than the working directory, for the
#: reason `data/_paths.py` records: two relative conventions coexisted in
#: this repo and silently created a second database.
DEFAULT_RUNS_PATH = str(Path(__file__).resolve().parents[2] / "runs" / "experiments.jsonl")

#: Below this, the best cell's neighbours are so much worse than the best
#: cell that the result is a spike. 0.5 means "the neighbourhood holds at
#: least half the peak"; the choice is a convention, so it is an argument
#: rather than a constant everywhere it matters.
DEFAULT_PLATEAU_RATIO = 0.5

_SHADES = " .:-=+*#%@"


@dataclass(frozen=True)
class Cell:
    values: tuple
    n: int
    mean: float
    best: float
    worst: float


@dataclass(frozen=True)
class Surface:
    strategy_id: str
    metric: str
    axes: tuple[str, ...]
    ticks: tuple[tuple, ...]
    cells: dict[tuple, Cell]
    plateau_ratio_threshold: float = DEFAULT_PLATEAU_RATIO
    notes: list[str] = field(default_factory=list)

    @property
    def grid_size(self) -> int:
        n = 1
        for t in self.ticks:
            n *= len(t)
        return n

    @property
    def coverage(self) -> float:
        """Fraction of the implied grid that was actually evaluated.

        **The headline number.** A sweep that looks dense in a heatmap can
        have left whole rows unrun, and the conclusion drawn from it is
        then a conclusion about the cells someone happened to choose.
        """
        return len(self.cells) / self.grid_size if self.grid_size else 0.0

    @property
    def peak(self) -> Cell | None:
        return max(self.cells.values(), key=lambda c: c.mean) if self.cells else None

    def neighbours(self, values: tuple) -> list[Cell]:
        """Cells one tick away along exactly one axis."""
        out = []
        for axis, ticks in enumerate(self.ticks):
            i = ticks.index(values[axis])
            for j in (i - 1, i + 1):
                if 0 <= j < len(ticks):
                    key = values[:axis] + (ticks[j],) + values[axis + 1 :]
                    if key in self.cells:
                        out.append(self.cells[key])
        return out

    @property
    def plateau_ratio(self) -> float | None:
        """`mean(neighbours) / peak`, or `None` if the peak is isolated.

        Deliberately signed-agnostic in only one direction: a **negative**
        peak makes the ratio meaningless, so it returns `None` rather than
        a number that would read as a verdict.
        """
        p = self.peak
        if p is None or p.mean <= 0:
            return None
        nb = self.neighbours(p.values)
        if not nb:
            return None
        return statistics.fmean(c.mean for c in nb) / p.mean

    @property
    def verdict(self) -> str:
        p = self.peak
        if p is None:
            return "EMPTY"
        if p.mean <= 0:
            return "NO POSITIVE CELL"
        nb = self.neighbours(p.values)
        if not nb:
            return "PEAK ISOLATED — every neighbour is an unrun cell"
        r = self.plateau_ratio
        assert r is not None
        return "PLATEAU" if r >= self.plateau_ratio_threshold else "SPIKE"


def _params(record: dict) -> dict:
    """Logged parameters. The key is `params` on scored records and
    `parameters` on some older ones; both spellings appear in the real
    log, so both are read rather than one being assumed."""
    p = record.get("params") or record.get("parameters") or {}
    return p if isinstance(p, dict) else {}


def _metric(record: dict, name: str) -> float | None:
    agg = record.get("aggregate_metrics") or {}
    v = agg.get(name)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _hashable(v):
    return json.dumps(v, sort_keys=True) if isinstance(v, (list, dict)) else v


def load_records(runs_path: str | Path = DEFAULT_RUNS_PATH) -> list[dict]:
    path = Path(runs_path)
    if not path.exists():
        return []
    lines = [(i, l) for i, l in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
             if l.strip()]
    out = []
    for pos, (lineno, line) in enumerate(lines):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            # **Only the final line may be a partial write.** A corrupt line
            # anywhere else silently drops a real run, which manufactures a
            # false unrun cell and can move both `coverage` and `verdict` --
            # the two numbers this module exists to report.
            if pos == len(lines) - 1:
                continue
            raise ValueError(
                f"{path}: line {lineno} is not valid JSON and is not the last "
                f"line, so it is corruption rather than a partial append"
            ) from None
    return out


def varying_parameters(records: list[dict]) -> dict[str, int]:
    """`{parameter: distinct value count}` for parameters that move."""
    seen: dict[str, set] = defaultdict(set)
    for r in records:
        for k, v in _params(r).items():
            seen[k].add(_hashable(v))
    return {k: len(v) for k, v in seen.items() if len(v) > 1}


def build_surface(
    records: list[dict],
    strategy_id: str,
    axes: tuple[str, ...] | None = None,
    metric: str = "sharpe_ratio",
    plateau_ratio_threshold: float = DEFAULT_PLATEAU_RATIO,
) -> Surface:
    scored = [
        r
        for r in records
        if r.get("strategy_id") == strategy_id
        and _params(r)
        and _metric(r, metric) is not None
    ]
    notes: list[str] = []
    if not scored:
        return Surface(strategy_id, metric, (), (), {}, plateau_ratio_threshold,
                       [f"no scored records for {strategy_id!r} with metric {metric!r}"])

    varying = varying_parameters(scored)
    if axes is not None and len(axes) > 2:
        # `render` walks two axes and looks cells up by a 2-tuple, so a
        # third would leave every real cell unmatched and the whole grid
        # would print as `?` -- a surface that looks entirely unrun.
        raise ValueError(
            f"a surface is two-dimensional; got {len(axes)} axes {axes}. "
            f"Pick two, or facet the rest by filtering the records first."
        )
    if axes is None:
        axes = tuple(sorted(varying, key=lambda k: -varying[k])[:2])
    if not axes:
        return Surface(strategy_id, metric, (), (), {}, plateau_ratio_threshold,
                       ["no parameter varies across these records"])

    missing = [a for a in axes if a not in varying]
    if missing:
        notes.append(f"axes that do not vary and so cannot form a surface: {missing}")
    # A varying parameter that is not an axis is averaged over, silently,
    # inside every cell. Naming it is the difference between a projection
    # and a sweep.
    projected = {k: n for k, n in varying.items() if k not in axes}
    if projected:
        notes.append(
            f"PROJECTED, not held fixed -- each cell averages over these varying "
            f"parameters: {projected}. Filter the records to hold them constant "
            f"before reading the verdict as a statement about the axes alone."
        )

    ticks = tuple(
        tuple(sorted({_hashable(_params(r).get(a)) for r in scored if a in _params(r)},
                     key=lambda x: (x is None, x)))
        for a in axes
    )

    grouped: dict[tuple, list[float]] = defaultdict(list)
    for r in scored:
        p = _params(r)
        if not all(a in p for a in axes):
            continue
        grouped[tuple(_hashable(p[a]) for a in axes)].append(_metric(r, metric))

    cells = {
        k: Cell(k, len(v), statistics.fmean(v), max(v), min(v))
        for k, v in grouped.items()
    }
    return Surface(strategy_id, metric, axes, ticks, cells, plateau_ratio_threshold, notes)


def render(surface: Surface) -> str:
    """A text heatmap. Terminal-readable on purpose — a PNG nobody opens
    is worse than a grid in the run output."""
    if not surface.cells:
        return "\n".join(surface.notes) or "(empty surface)"
    lo = min(c.mean for c in surface.cells.values())
    hi = max(c.mean for c in surface.cells.values())
    span = hi - lo or 1.0

    def shade(key):
        c = surface.cells.get(key)
        if c is None:
            return "?"  # never run -- deliberately loud
        return _SHADES[min(len(_SHADES) - 1, int((c.mean - lo) / span * (len(_SHADES) - 1)))]

    lines = []
    if len(surface.axes) == 1:
        (ticks,) = surface.ticks
        lines.append(f"{surface.axes[0]}: " + "".join(shade((t,)) for t in ticks))
        lines.append("  " + " ".join(str(t) for t in ticks))
        return "\n".join(lines)

    rows, cols = surface.ticks[0], surface.ticks[1]
    w = max(len(str(surface.axes[0])), max(len(str(r)) for r in rows))
    lines.append(f"{'':<{w}}  {surface.axes[1]} ->")
    lines.append(f"{'':<{w}}  " + "".join(str(c)[-1] for c in cols))
    for r in rows:
        lines.append(f"{str(r):<{w}}  " + "".join(shade((r, c)) for c in cols))
    lines.append(f"{'':<{w}}  legend: '{_SHADES.strip()}' low->high, '?' = never run")
    return "\n".join(lines)


def report(surface: Surface) -> str:
    out = [
        f"strategy   : {surface.strategy_id}",
        f"metric     : {surface.metric}",
        f"axes       : {' x '.join(surface.axes) if surface.axes else '(none)'}",
    ]
    for n in surface.notes:
        out.append(f"note       : {n}")
    if not surface.cells:
        return "\n".join(out)
    out += [
        f"grid       : {' x '.join(str(len(t)) for t in surface.ticks)} = {surface.grid_size} cells",
        f"COVERAGE   : {len(surface.cells)}/{surface.grid_size} = {surface.coverage:.0%}"
        + ("  <-- unrun cells cannot support a conclusion about them" if surface.coverage < 1 else ""),
        "",
        render(surface),
        "",
    ]
    p = surface.peak
    assert p is not None
    out.append(f"peak       : {dict(zip(surface.axes, p.values))} mean={p.mean:.4f} (n={p.n})")
    nb = surface.neighbours(p.values)
    out.append(f"neighbours : {len(nb)} evaluated"
               + (f", mean={statistics.fmean(c.mean for c in nb):.4f}" if nb else ""))
    r = surface.plateau_ratio
    out.append(f"plateau    : {'n/a' if r is None else f'{r:.2f}'} "
               f"(threshold {surface.plateau_ratio_threshold})")
    out.append(f"VERDICT    : {surface.verdict}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs-path", default=DEFAULT_RUNS_PATH)
    ap.add_argument("--strategy")
    ap.add_argument("--axes", help="comma-separated; default is the two most-varied")
    ap.add_argument("--metric", default="sharpe_ratio")
    ap.add_argument("--plateau-ratio", type=float, default=DEFAULT_PLATEAU_RATIO)
    ap.add_argument("--list", action="store_true", help="strategies with a usable sweep")
    args = ap.parse_args(argv)

    records = load_records(args.runs_path)
    if not records:
        print(f"no records at {args.runs_path}")
        return 1

    if args.list or not args.strategy:
        by: dict[str, list[dict]] = defaultdict(list)
        for r in records:
            if _params(r) and _metric(r, args.metric) is not None:
                by[r.get("strategy_id", "?")].append(r)
        print(f"{'strategy_id':44} {'scored':>7}  varying parameters")
        for sid, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
            v = varying_parameters(rs)
            if v:
                print(f"{sid:44} {len(rs):>7}  {v}")
        return 0

    axes = tuple(a.strip() for a in args.axes.split(",")) if args.axes else None
    print(report(build_surface(records, args.strategy, axes, args.metric, args.plateau_ratio)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
