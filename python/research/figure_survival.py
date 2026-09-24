"""Step 1 of CLAUDE.md's own trim rule, mechanised.

**The rule, quoted from the file it checks**:

> So the operation is two steps, and only the first is mechanical:
>
> 1. **Mechanical**: no figure may be removed unless it survives in the
>    `.planning/` document. A script can check this, and one is the obvious
>    next tool if trimming becomes routine.
> 2. **A judgement call, which must not be skipped**: of what passes step 1,
>    remove only **narrative and evidence** — what was run, in what order,
>    and what it measured. Keep every **rule, constant, safety property and
>    standing constraint**, however well its evidence is preserved
>    elsewhere.

This module is **step 1 only**, and says so in its own output, because the
file is emphatic that step 1 is *necessary and not sufficient*: applied
literally on 2026-09-15 it would have licensed deleting almost the whole
scalping section, since 95 of its 98 figures survive in `.planning/scalp-*.md`
— including the thirteen S8 rules and the `FEE_BPS`/`SLIPPAGE_BPS`
constants, which that section itself labels *"rules, not history"*.

> **A rule that cites a figure is not made redundant by that figure living
> elsewhere: deleting it loses the rule, and the check cannot see the
> difference.**

So a `SURVIVES` verdict here means *"removing this line would not lose a
number"*. It does **not** mean the line may be removed. Nothing in this
module decides that, and it refuses to print anything that reads like a
recommendation to delete.

Trimming has never run for the Korean-equities arc, which is why `CLAUDE.md`
grew from 1,696 lines at the 2026-08-26 reorganisation to 3,506 — larger than
before that cleanup.

Usage::

    python -m research.figure_survival                    # whole file
    python -m research.figure_survival --section Scalping # one section
    python -m research.figure_survival --orphans          # figures found nowhere
"""

from __future__ import annotations

import argparse
import pathlib
import re
from collections import Counter
from dataclasses import dataclass, field

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
PLANNING = REPO_ROOT / ".planning"

#: A figure is a number a reader could act on. Deliberately narrow: bare
#: small integers ("two reasons", "three rules") are prose, not figures, and
#: counting them would bury the real ones. What qualifies:
#:
#: - a decimal, with or without sign, comma grouping or a unit suffix
#: - a percentage
#: - an integer of 3+ digits, or any comma-grouped integer
#: - a scientific-notation value (``6.5e-11``)
#:
#: Dates are excluded separately — a date is provenance, not a measurement,
#: and every paragraph carries one.
#:
#: **Two disclosed limitations, measured rather than assumed.** A bare one- or
#: two-digit integer is treated as prose, so `"15 consecutive days"` and
#: `"50+ trades"` are real criteria this does not see — admitting them makes
#: almost every line a figure line and the report stops distinguishing
#: anything. And a **range** written `80-90%` yields nothing, because the
#: leading `80` is too short and the `-90%` is blocked by the lookbehind: so
#: the Eligibility Bar's fold-consistency figure is invisible here. Both mean
#: the check is **conservative in the unsafe direction** — it can miss a
#: figure, and a missed figure reads as "this line carries nothing to lose",
#: which is why step 2 exists and why no output of this module may be acted on
#: as a delete list.
_FIGURE = re.compile(
    r"""
    (?<![\w.\-])
    (?:
        [-−+]?\d+(?:\.\d+)?%           # 54%, 20.135% -- FIRST, so a decimal
                                       # percentage keeps its sign: with the
                                       # decimal alternative ahead of it,
                                       # 20.135% matched as bare 20.135 and
                                       # the unit was silently dropped.
      | [-−+]?\d+\.\d+(?:e[-+]?\d+)?   # 0.95, 6.5e-11, -14.4
      | [-−+]?\d{1,3}(?:,\d{3})+       # 4,374
      | [-−+]?\d{3,}                   # 1901
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

#: `2026-09-17`, `2026-08`, `#203`, `PR #103-#106`, a version like `3.12`,
#: and a bare `YYYYMMDD` — this project's own KIS date format, which appears
#: constantly as `19900101`..`20261231` and is provenance, not measurement.
#: The last one was a real false positive on the first run: eight digits also
#: match the "integer of 3+ digits" rule, so a date range reported as two
#: figures that survive nowhere.
_PROVENANCE = re.compile(
    r"\b(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\b"  # YYYYMMDD
    r"|\b\d{4}-\d{2}(?:-\d{2})?\b"
    r"|#\d+|\bPR\s*#|\bv?\d+\.\d+\.\d+\b"
)

#: Lines that state a rule, a constant, a constraint or a safety property.
#: A line matching any of these is **flagged as step-2 material** even when
#: its figures survive, because those are exactly what the file says to keep.
_RULE_MARKERS = (
    "must ", "must not", "never ", "always ", "may not", "may only",
    "required", "requires", "refuses", "fails closed", "fail closed",
    "do not ", "don't ", "is a rule", "binding", "standing", "non-negotiable",
    "needs explicit human approval", "hard gate", "floor is", "ceiling",
    "= ", "deliberately", "forbid",
)


@dataclass
class LineVerdict:
    number: int
    text: str
    figures: list[str]
    missing: list[str] = field(default_factory=list)
    rule_markers: list[str] = field(default_factory=list)

    @property
    def survives(self) -> bool:
        return not self.missing

    @property
    def looks_like_a_rule(self) -> bool:
        return bool(self.rule_markers)


def _normalise(figure: str) -> str:
    """One figure to the forms a `.planning/` document might spell it in.

    Comma grouping, the Unicode minus this repo's own documents use, and a
    trailing percent are all cosmetic, and treating them as distinct
    figures is how a real survival gets reported as a loss.
    """
    return figure.replace(",", "").replace("−", "-").rstrip("%").lstrip("+")


def figures_in(text: str) -> list[str]:
    """Every actionable figure on one line, provenance removed first."""
    stripped = _PROVENANCE.sub(" ", text)
    return [m.group(0) for m in _FIGURE.finditer(stripped)]


def _planning_corpus() -> str:
    parts = []
    for path in sorted(PLANNING.glob("*.md")):
        parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _survives(figure: str, corpus_norm: str) -> bool:
    """Is this figure present anywhere in `.planning/`?

    Compared on the normalised form **and** with comma grouping restored,
    because a document may spell 4,374 either way and a miss here reads as a
    figure that would be lost.
    """
    bare = _normalise(figure)
    if bare in corpus_norm:
        return True
    # 4374 -> 4,374
    if bare.lstrip("-").isdigit() and len(bare.lstrip("-")) > 3:
        sign = "-" if bare.startswith("-") else ""
        digits = bare.lstrip("-")
        grouped = f"{int(digits):,}"
        if sign + grouped in corpus_norm or grouped in corpus_norm:
            return True
    return False


#: A real ATX heading: one to six `#`, then whitespace or end of line.
#: **`#103-#106).` is not one**, and it is the only such line in `CLAUDE.md`
#: (measured) — a `startswith("#")` test read it as a depth-1 heading and
#: truncated the `## Architecture` section at line 187 instead of 318,
#: silently dropping both `₩250,000` lines. Reported on review.
_ATX = re.compile(r"^(#{1,6})(?:\s|$)")

#: A fence's opening run length, so a shorter run inside it does not close it:
#: a block opened with ```` ```` ```` contains ``` ``` ``` lines legitimately.
#: Tilde fences are deliberately out of scope — the contract here does not
#: define them, and adding them would be a guess rather than a fix.
_FENCE = re.compile(r"^(`{3,})")


def _heading_depth(line: str) -> int | None:
    """The heading depth of a line, or `None` if it is not a heading."""
    m = _ATX.match(line)
    return len(m.group(1)) if m else None


def section_span(text: str, section: str) -> tuple[int, int]:
    """1-indexed `(first, last)` lines of the section whose heading matches.

    A section runs until the next heading at the **same or shallower** depth,
    which is what a reader means by a section. Three properties, each a fix
    for a real miss rather than polish:

    - **Depth-aware.** The first version ended a section at any heading, so a
      subsection inside it silently truncated it.
    - **Fence-aware, by fence length.** A `#` comment inside a fenced code
      block is not a heading. The fence's own opening run length is recorded,
      so a three-backtick line inside a four-backtick block does not close it.
    - **A heading is ATX-validated.** `#103-#106).` starts with `#` and is
      prose; reading it as a heading cut `## Architecture` short at 187.

    **Matching is by substring and takes the LAST-OPENED match at the
    shallowest depth, which is a choice and is disclosed**: `"Architecture"`
    matches both `## Architecture` and `## Long-term Design Targets (shape the
    architecture now, not built now)`. Preferring an exact heading-text match
    resolves the common case; where none exists the first substring match
    wins, and a caller wanting the other one names it more precisely.

    Raises rather than returning an empty span: a filter that matches nothing
    and reports nothing is the inert-guard shape this repo has paid for.
    """
    lines = text.splitlines()
    fence: str | None = None
    headings: list[tuple[int, int, str]] = []  # (line, depth, text)
    for i, line in enumerate(lines, start=1):
        m = _FENCE.match(line.lstrip())
        if m:
            if fence is None:
                fence = m.group(1)
            elif len(m.group(1)) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        depth = _heading_depth(line)
        if depth is not None:
            headings.append((i, depth, line[depth:].strip()))

    wanted = section.lower()
    exact = [h for h in headings if h[2].lower() == wanted]
    matches = exact or [h for h in headings if wanted in h[2].lower()]
    if not matches:
        raise KisSectionError(f"no heading in CLAUDE.md matches {section!r}")

    start, depth, _ = matches[0]
    for line_no, other_depth, _ in headings:
        if line_no > start and other_depth <= depth:
            return start, line_no - 1
    return start, len(lines)


class KisSectionError(ValueError):
    """No heading matched — never silently an empty audit."""


def audit(section: str | None = None) -> list[LineVerdict]:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    corpus_norm = _normalise(_planning_corpus())

    lo, hi = (1, len(text.splitlines())) if section is None else section_span(text, section)
    lines = text.splitlines()
    verdicts: list[LineVerdict] = []
    for i, line in enumerate(lines, start=1):
        if not (lo <= i <= hi):
            continue
        figs = figures_in(line)
        if not figs:
            continue
        lowered = line.lower()
        verdicts.append(
            LineVerdict(
                number=i,
                text=line.strip(),
                figures=figs,
                missing=[f for f in figs if not _survives(f, corpus_norm)],
                rule_markers=[m for m in _RULE_MARKERS if m in lowered],
            )
        )
    return verdicts


def report(verdicts: list[LineVerdict], *, orphans_only: bool = False) -> str:
    out: list[str] = []
    total_lines = len(verdicts)
    total_figs = sum(len(v.figures) for v in verdicts)
    lost = [v for v in verdicts if not v.survives]
    rules = [v for v in verdicts if v.looks_like_a_rule]

    out.append("STEP 1 ONLY -- necessary, NOT sufficient.")
    out.append(
        "A SURVIVES verdict means removing the line would not lose a NUMBER. "
        "It does not mean the line may be removed: a rule that cites a figure "
        "is not made redundant by that figure living elsewhere. Step 2 is a "
        "judgement call and this script does not make it."
    )
    out.append("")
    out.append(f"lines carrying a figure          {total_lines:>6,}")
    out.append(f"figures                          {total_figs:>6,}")
    out.append(f"lines whose figures all survive  {total_lines - len(lost):>6,}")
    out.append(f"lines with a figure found NOWHERE{len(lost):>6,}  <-- cannot be trimmed")
    out.append(
        f"...of the survivors, lines that look like a RULE "
        f"{len([v for v in rules if v.survives]):,} "
        f"-- step 2 must keep these"
    )
    out.append("")

    if orphans_only:
        out.append("Figures that appear in CLAUDE.md and in no .planning/ document:")
        for v in lost:
            out.append(f"  L{v.number:>5}  missing {v.missing}")
            out.append(f"         {v.text[:110]}")
        return "\n".join(out)

    counts = Counter()
    for v in verdicts:
        counts["rule" if v.looks_like_a_rule else "narrative"] += 1
    out.append(
        "Of every line carrying a figure: "
        f"{counts['rule']:,} read as a rule/constant/constraint, "
        f"{counts['narrative']:,} read as narrative."
    )
    out.append("")
    out.append(
        "The second number is the trim candidate POOL, not the trim. Run with "
        "--orphans to see what may never be removed."
    )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--section", default=None, help="only headings matching this")
    ap.add_argument("--orphans", action="store_true",
                    help="list figures present in CLAUDE.md and nowhere else")
    args = ap.parse_args(argv)
    print(report(audit(args.section), orphans_only=args.orphans))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
