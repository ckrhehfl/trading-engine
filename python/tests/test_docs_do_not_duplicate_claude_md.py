"""`docs/` describes structure; `CLAUDE.md` owns every rule and figure.

**The split this enforces**, decided 2026-09-25:

| question | kind | home |
|---|---|---|
| what may never be violated? | invariant, loaded every session | `CLAUDE.md` |
| what does the structure look like **now**? | living, replaced | `docs/` |
| what was decided when, and why? | append-only log | `.planning/` |

**Why it needs a test rather than a convention.** The split's one real
hazard is that `docs/` starts repeating things `CLAUDE.md` owns, and the two
then drift apart — at which point a reader believes whichever they opened.
This project has already shipped exactly that failure: `CLAUDE.md` called a
research window "unspent" while `runs/spent_windows.json` and **four other
paragraphs of the same file** said it was spent. The claim survived a
day-after PR review and was only caught weeks later.

A duplicated *figure* is the detectable form of it, so that is what this
asserts. `docs/architecture.md` was written with zero — the ₩250,000 index
multiplier was in its first draft and removed on this rule.

**What this cannot do**, stated so it is not over-trusted: it catches a
number appearing in both places. It cannot catch a *rule* restated in prose,
which is the same hazard in a form no regex sees. That half stays a
judgement call at review time.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
CLAUDE_MD = REPO / "CLAUDE.md"
DOCS = REPO / "docs"

#: A figure precise enough that having two copies is a real contradiction
#: risk: a decimal, or a comma-grouped integer. A bare small integer ("two
#: planes", "section 3") is prose and is deliberately not matched.
_FIGURE = re.compile(r"(?<![\w.])[-−+]?\d+(?:[.,]\d+)+%?")

#: Documents that are allowed to carry figures because their whole job is
#: operational numbers a human types at a prompt. The runbook's ports, sleep
#: intervals and cron minutes are not claims about the system's behaviour.
_EXEMPT = {"paper-trading-runbook.md"}


def _docs_files() -> list[pathlib.Path]:
    return sorted(p for p in DOCS.glob("*.md") if p.name not in _EXEMPT)


def test_docs_exists_and_is_not_empty():
    """A guard over an empty directory passes vacuously, which is the inert
    shape this repo has paid for three times."""
    assert DOCS.is_dir(), "docs/ is missing"
    assert _docs_files(), "no non-exempt docs/*.md to check -- this test is inert"


@pytest.mark.parametrize("path", _docs_files(), ids=lambda p: p.name)
def test_a_docs_file_carries_no_figure_claude_md_owns(path: pathlib.Path):
    """A figure belongs to exactly one file.

    `docs/` says *what the structure is*; the numbers that bound it —
    multipliers, limits, thresholds, measured API behaviour — are
    `CLAUDE.md`'s, and `docs/` points instead of repeating.
    """
    text = path.read_text(encoding="utf-8")
    found = sorted(set(_FIGURE.findall(text)))
    assert not found, (
        f"{path.name} carries {found}. A figure lives in exactly one file: "
        f"state it in CLAUDE.md and point here, or the two will drift and a "
        f"reader will believe whichever they opened first."
    )


def test_docs_points_at_claude_md_rather_than_restating_it():
    """The positive half: a structure document that never mentions where the
    rules are has quietly become the whole documentation."""
    arch = _flat((DOCS / "architecture.md").read_text(encoding="utf-8"))
    assert "CLAUDE.md" in arch, "architecture.md does not point at CLAUDE.md at all"

    # **Two structural commitments, not the mere presence of a word.** An
    # earlier version asserted that "safety propert" appeared somewhere, and a
    # mutation removing the real pointer still passed — the phrase also occurs
    # in the §7 signpost table, so the test was satisfied for a reason
    # unrelated to what it meant to check.
    assert "What is deliberately NOT here" in arch, (
        "architecture.md no longer states what it deliberately does NOT hold, "
        "so nothing stops it absorbing the invariants it was split away from"
    )
    assert "Where the rest is" in arch, (
        "architecture.md lost its signpost section, so a reader who starts "
        "here has no route to the rules, the record, or the runbook"
    )


def test_claude_md_points_at_the_living_architecture():
    """And the reverse, so the pointer is not one-way. ~70 `.planning`
    back-references name `CLAUDE.md`'s Architecture section by heading; the
    heading therefore stays, and has to say where the body went."""
    raw = CLAUDE_MD.read_text(encoding="utf-8")
    assert "## Architecture" in raw, "the referenced heading was removed"
    section = _flat(raw.split("## Architecture", 1)[1].split("\n## ", 1)[0])
    assert "docs/architecture.md" in section, (
        "CLAUDE.md's Architecture section no longer points at the living "
        "document, so a reader following a .planning reference lands nowhere"
    )


def _flat(text: str) -> str:
    """Whitespace collapsed and markdown emphasis stripped.

    **Matching a rule on its literal line is what failed here.** The first
    version of the test below asserted the raw string and reported
    `"no environment variable, argument, or other configuration surface"` as
    having left `CLAUDE.md` when it was plainly there — split across a line
    break with `**` in the middle. A guard that cries wolf on a rewrap gets
    deleted the third time someone reflows a paragraph, so it has to survive
    one.
    """
    return re.sub(r"\s+", " ", text.replace("*", "").replace("`", ""))


def test_the_invariants_did_not_move_out_of_claude_md():
    """The move's whole risk in one test. These are what an AI session must
    have read; if they are only in `docs/`, they are read only when opened."""
    text = _flat(CLAUDE_MD.read_text(encoding="utf-8"))
    for invariant in (
        "Safety properties. Do not weaken",
        "trips `KillSwitch` unconditionally",
        "Three open gaps, none closed",
        "must only ever be, exactly two implementations",
        "Never bypass the Java Risk Gateway",
        "Never let Python place live orders directly",
        "no environment variable, argument, or other configuration surface",
    ):
        assert _flat(invariant) in text, f"{invariant!r} left CLAUDE.md"
