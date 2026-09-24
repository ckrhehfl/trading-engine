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
    # A **standalone four-digit year or index code**. Reported on review:
    # `2026` in "2026 listings" was extracted as a figure and then counted as
    # surviving on the strength of a *date* elsewhere (`2026-09-24`), so a
    # figure existing nowhere read as one that does.
    #
    # Measured before excluding them rather than assumed: all 24 such tokens in
    # `CLAUDE.md` are years in prose ("excluding 2021", "2017-2021", "spanning
    # 2000 to 2026"), citation years (Bailey & Lopez de Prado 2014, Alexander &
    # Fabozzi 2026), or the KOSPI200 **index code** `2001`. Not one is a
    # measurement.
    #
    # **Disclosed limitation**: a genuine count that happens to land in
    # 1900-2100 is now invisible, which is the unsafe direction, and it is
    # accepted for the same reason the two-digit exclusion is -- the
    # alternative is 24 false survivals on every run.
    r"|(?<![\d.\-/])(?:19|20)\d{2}(?![\d.\-/])"
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
    """Is this figure present anywhere in `.planning/`, as a whole number?

    **Matched on number-token boundaries, not as a substring**, and that is a
    correctness fix in the unsafe direction rather than a nicety. A bare
    `bare in corpus_norm` test found:

    | looking for | matched inside |
    |---|---|
    | `20` | `2026-09-24` |
    | `0.62` | `10.625` |
    | `250` | `2500` |

    Every one of those is a **false survival**, which shrinks the orphan list
    and makes a line read as carrying nothing to lose. For a tool whose whole
    job is to say which figures exist only in `CLAUDE.md`, that is the
    direction that matters. Reported on review.

    The comma-grouping branch this replaces was **dead code**: the corpus is
    passed through `_normalise`, which strips commas, so a comma-bearing needle
    could never match it. It was written to handle `4374` vs `4,374` and the
    normalisation already handles that.
    """
    bare = _normalise(figure)
    if not bare:
        return False
    # **Two corrections the first boundary attempt needed, both measured.**
    #
    # A leading sign must be CONSUMED, not forbidden: `_normalise` strips a
    # needle's `+`, so forbidding a preceding `+` made `79.2` fail against the
    # corpus's own `+79.2R`. That reported `+79.2`, `+0.0184` and `+61.8` as
    # figures existing nowhere when all three are in `.planning/`.
    #
    # A trailing `.` is sentence punctuation unless a digit follows it, so the
    # lookahead rejects `\.?\d` rather than `[\d.]` — otherwise a figure
    # ending a sentence (`4,374.`) reads as absent.
    #
    # What it correctly still rejects, verified against the real corpus:
    # `4374` inside the row id `4963594374`, `0.999999` inside
    # `0.99999900692081`, and `1.250` inside `1.250042` — different numbers,
    # every one of which a substring test called a survival.
    #
    # **Disclosed limitation**: an unsigned needle matches a signed occurrence,
    # so `20` would survive on a corpus `-20`. That is a false survival, the
    # unsafe direction, and it is accepted rather than fixed because
    # distinguishing them needs the sign to be part of the figure and this
    # file's own prose writes the same quantity both ways.
    # `(?!-\d)` as well, so a needle can never be satisfied by the year
    # fragment of a date: `2026` must not match inside `2026-09-24`. Reported
    # on review, and kept as a second line of defence even though the
    # provenance rule above now strips bare years before they become figures --
    # the two guard different steps, and this one also stops a match landing on
    # the left half of a range like `13-15`.
    pattern = re.compile(
        r"(?<![\d.])[-+−]?" + re.escape(bare) + r"(?!\.?\d)(?!-\d)"
    )
    return pattern.search(corpus_norm) is not None


#: A real ATX heading, to CommonMark: **up to three** spaces of indentation,
#: one to six `#`, then whitespace or end of line. Four spaces makes it an
#: indented code block instead.
#:
#: Two findings live in this one pattern, both reported on review:
#:
#: - **`#103-#106).` is not a heading**, and it is the only such line in
#:   `CLAUDE.md` (measured). A `startswith("#")` test read it as depth 1 and
#:   truncated `## Architecture` at line 187 instead of 318, silently dropping
#:   both `₩250,000` lines.
#: - **an indented heading IS one.** Requiring column zero made a legal
#:   heading read as prose, losing a section boundary — and it was inconsistent
#:   with the fence rule beside it, which accepted any indentation at all.
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:\s|$)")

#: A fenced code block, to CommonMark, with the same 0-3 space indent rule as
#: `_ATX` so the two agree:
#:
#: - the opening run's **length** is recorded, because a block opened with
#:   ```` ```` ```` legitimately contains ``` ``` ``` lines;
#: - a backtick fence's **info string may not contain a backtick**, so
#:   ``` ```foo``` text ``` is an inline code span rather than a fence. Opening
#:   one there skips every heading until the next backtick line, which extends
#:   a section **silently** — the exact failure this module exists to catch;
#: - a **closing** fence carries only whitespace after its run.
#:
#: Tilde fences are deliberately out of scope: the contract here does not
#: define them, and adding them would be a guess rather than a fix.
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,})([^`]*)$")
_FENCE_CLOSE = re.compile(r"^ {0,3}(`{3,})\s*$")


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
        if fence is None:
            opened = _FENCE_OPEN.match(line)
            if opened:
                fence = opened.group(1)
                continue
        else:
            closed = _FENCE_CLOSE.match(line)
            if closed and len(closed.group(1)) >= len(fence):
                fence = None
            continue
        m = _ATX.match(line)
        if m:
            headings.append((i, len(m.group(1)), line[m.end():].strip()))

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

    # **Three-way, and the third number is the only candidate pool.** A
    # two-way rule/narrative split counted orphan narrative lines as trim
    # candidates while the same report said they cannot be trimmed -- the two
    # figures contradicted each other on the same screen. Reported on review.
    # Orphans are counted only as orphans, so nothing is double-counted.
    counts = Counter()
    for v in verdicts:
        if not v.survives:
            counts["orphan"] += 1
        elif v.looks_like_a_rule:
            counts["rule"] += 1
        else:
            counts["narrative"] += 1
    out.append(
        "Of every line carrying a figure: "
        f"{counts['orphan']:,} carry a figure found nowhere else (step 1 "
        f"forbids removing these), "
        f"{counts['rule']:,} survive step 1 and read as a "
        f"rule/constant/constraint (step 2 must keep these), "
        f"{counts['narrative']:,} survive step 1 and read as narrative."
    )
    out.append("")
    out.append(
        f"Only that last figure -- {counts['narrative']:,} -- is the trim "
        "candidate POOL, and it is a pool rather than a trim: step 2 is a "
        "judgement call this script does not make. Run with --orphans to see "
        "what may never be removed."
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
