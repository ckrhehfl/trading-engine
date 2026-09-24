"""The trim rule's step 1, and the things it must refuse to do.

`CLAUDE.md` says of its own trim rule that step 1 is mechanical and *"a
script can check this, and one is the obvious next tool if trimming becomes
routine."* Trimming has never run for the Korean-equities arc — the file grew
from 1,696 lines at the 2026-08-26 reorganisation to 3,506 — so this is that
tool.

**The tests that matter here are the ones asserting what it does NOT do.**
The same section is emphatic that step 1 is necessary and not sufficient:
applied literally on 2026-09-15 it would have licensed deleting almost the
whole scalping section, since 95 of its 98 figures survive elsewhere —
including the thirteen S8 rules and the `FEE_BPS`/`SLIPPAGE_BPS` constants,
which that section labels *"rules, not history"*. A tool that printed a
delete list would be worse than no tool.
"""

from __future__ import annotations

import pytest

from research.figure_survival import (
    CLAUDE_MD,
    LineVerdict,
    audit,
    figures_in,
    report,
)


# ------------------------------------------------ what counts as a figure


@pytest.mark.parametrize(
    "text, expected",
    [
        ("mean annualized Sharpe +0.039", ["+0.039"]),
        ("DSR = 6.5e-11 against", ["6.5e-11"]),
        ("Combined pool 4,374.", ["4,374"]),
        ("max drawdown 20.135% vs. a 20% ceiling", ["20.135%", "20%"]),
        ("raw mean of −14.4 bp", ["−14.4"]),
        ("1,901 bars, zero gaps", ["1,901"]),
        ("~0.62 -- the best this project has", ["0.62"]),
    ],
)
def test_an_actionable_number_is_a_figure(text, expected):
    assert figures_in(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "two independent reasons to stop",
        "Three filters, and none subsumes the others",
        "measured 2026-09-24 against the 2026-09-23 snapshots",
        "PRs #103-#106",
        "an over-wide window 19900101..20261231",
        "the 2026-08 reorganisation",
    ],
)
def test_prose_and_provenance_are_not_figures(text):
    """**`19900101` is in this list because it was a real false positive.**
    Eight digits also match the "integer of 3+ digits" rule, so the first run
    reported a KIS date range as two figures surviving nowhere. A date is
    provenance; every paragraph carries one, and counting them buries the
    real figures."""
    assert figures_in(text) == [], figures_in(text)


def test_the_two_disclosed_LIMITATIONS_are_real_and_pinned():
    """**Asserted rather than described**, because both miss a real figure and
    a missed figure reads as "this line carries nothing to lose" — the unsafe
    direction.

    1. A bare one- or two-digit integer is prose. `"15 consecutive days"` and
       `"50+ trades"` are genuine criteria this does not see. Admitting them
       makes almost every line a figure line and the report stops
       distinguishing anything, so the narrowness is deliberate.
    2. A **range** written `80-90%` yields nothing: the leading `80` is too
       short, and the `-90%` is blocked by the lookbehind that stops a hyphen
       being read as a minus sign. So the Eligibility Bar's own
       fold-consistency figure is invisible here.

    Pinned so a future reader meets the limits as tests rather than trusting a
    docstring, and so widening the definition has to update them on purpose.
    """
    assert figures_in("at least 80-90% of folds") == []
    assert figures_in("15 consecutive days of operation") == []
    assert figures_in("50+ trades") == []
    assert figures_in("exactly two times jointly") == []
    # And the ones it does catch, so the limits are bounded rather than open.
    assert figures_in("+54%/yr equal-weight") == ["+54%"]
    assert figures_in("PSR 0.9705") == ["0.9705"]


# --------------------------------------- normalisation, so a hit is a hit


def test_comma_grouping_and_unicode_minus_do_not_hide_a_survival():
    """A `.planning/` document may spell 4,374 either way and uses `−` as
    often as `-`. Treating those as distinct figures is how a real survival
    gets reported as a figure that would be lost — which would block a trim
    that the rule permits."""
    from research.figure_survival import _normalise

    assert _normalise("4,374") == "4374"
    assert _normalise("−14.4") == "-14.4"
    assert _normalise("+0.039") == "0.039"
    assert _normalise("20.135%") == "20.135"


# ------------------------------------------- the refusals, which are the point


def test_the_report_states_that_step_1_is_NOT_sufficient():
    """Without this sentence the output reads as permission. The file's own
    warning is that a rule citing a figure is not made redundant by that
    figure living elsewhere, and the check cannot see the difference."""
    text = report([])
    assert "NOT sufficient" in text
    assert "does not mean the line may be removed" in text.lower()
    assert "judgement call" in text.lower()


def test_the_report_never_prints_a_delete_list():
    """The tool's whole hazard. A machine-generated list of lines to delete
    would be acted on, and step 2 is a judgement call this script cannot
    make."""
    verdicts = [
        LineVerdict(1, "mean Sharpe +0.039 was measured", ["+0.039"]),
        LineVerdict(2, "FEE_BPS = 5, and it must not be tuned", ["5"], rule_markers=["= "]),
    ]
    text = report(verdicts).lower()
    for forbidden in ("delete", "remove these", "safe to remove", "trim these",
                      "can be deleted", "may be deleted"):
        assert forbidden not in text, f"the report suggested a deletion: {forbidden!r}"


def test_a_rule_line_is_flagged_even_when_its_figures_survive():
    """The scalping section is the case: 95 of 98 figures survive elsewhere,
    and the lines carrying them are rules. Surviving figures plus a rule
    marker is exactly the combination step 1 would wave through and step 2
    must keep."""
    rule = LineVerdict(
        1, "**`SLIPPAGE_BPS = 1`** for scalping preregistrations", ["1"],
        rule_markers=["= "],
    )
    assert rule.survives and rule.looks_like_a_rule
    assert "step 2 must keep these" in report([rule])


# ------------------------------------------------ against the real file


def test_the_real_audit_finds_orphans_and_they_may_never_be_trimmed():
    """Run against the committed `CLAUDE.md` and `.planning/`. The figures
    that exist only here are the check's most useful output: whatever else is
    true of them, the rule forbids removing the lines that carry them."""
    verdicts = audit()
    assert verdicts, "the audit found no figure lines at all -- it is inert"
    orphans = [v for v in verdicts if not v.survives]
    # Not asserting a count -- it moves every time a document is added. What
    # must hold is that the orphan list is reported rather than swallowed.
    text = report(verdicts, orphans_only=True)
    assert "cannot be trimmed" in text
    for v in orphans:
        assert f"L{v.number:>5}" in text, f"orphan at L{v.number} was not reported"


def test_the_audit_can_be_scoped_to_one_section():
    """**The boundaries are asserted, not merely the fact that filtering
    happened.** The first version checked only that the scoped result was a
    non-empty proper subset — which any heading would satisfy, so the test
    passed without establishing that the *right* section was selected.
    Reported on review, and it is the same shape as a verification that
    shares an assumption with its implementation.

    The span is derived from the committed file rather than hardcoded, because
    a line number rots on the next edit; what is pinned is that every scoped
    line falls inside the section's own heading boundaries and that a line
    from a different section does not.
    """
    from research.figure_survival import CLAUDE_MD, section_span

    text = CLAUDE_MD.read_text(encoding="utf-8")
    lo, hi = section_span(text, "Scalping")
    assert "scalping" in text.splitlines()[lo - 1].lower()
    assert text.splitlines()[hi].startswith("#"), "the span does not end at a heading"

    whole = audit()
    scoped = audit("Scalping")
    assert scoped, "the section filter matched nothing"
    assert all(lo <= v.number <= hi for v in scoped), (
        f"a scoped line fell outside {lo}..{hi}: "
        f"{[v.number for v in scoped if not lo <= v.number <= hi]}"
    )
    assert {v.number for v in scoped} <= {v.number for v in whole}

    # And a figure line from a DIFFERENT section is excluded, so the filter is
    # selecting rather than merely truncating.
    risk_lo, risk_hi = section_span(text, "Risk Parameters")
    assert risk_hi < lo or risk_lo > hi, "the two sections overlap; pick another"
    assert not any(risk_lo <= v.number <= risk_hi for v in scoped)


def test_a_section_that_matches_nothing_RAISES():
    """A filter that matches nothing and reports nothing is the inert-guard
    shape this repo has paid for three times."""
    from research.figure_survival import KisSectionError

    with pytest.raises(KisSectionError, match="no heading"):
        audit("a heading that does not exist anywhere")


def test_a_hash_inside_a_FENCED_BLOCK_does_not_end_a_section():
    """**A real defect in the first version**, which toggled on any line
    starting with `#`: a `#` comment inside a fenced code block ended the
    section silently, and `CLAUDE.md` contains both fences and `#` comments.
    Subsection headings did the same, which is why the span is depth-aware."""
    from research.figure_survival import section_span

    doc = "\n".join([
        "## Wanted",            # 1
        "PSR 0.9705",           # 2
        "```bash",              # 3
        "# a comment, not a heading",  # 4
        "```",                  # 5
        "### A subsection",     # 6  -- deeper, so still inside
        "Sharpe 0.716",         # 7
        "## Next",              # 8  -- same depth, so the end
        "4,374",                # 9
    ])
    assert section_span(doc, "Wanted") == (1, 7)


def test_the_tool_reads_the_committed_file_not_a_copy():
    """A figure-survival check run against a stale copy would pass vacuously,
    which is the shape of every inert guard this repo has paid for."""
    assert CLAUDE_MD.exists()
    assert CLAUDE_MD.name == "CLAUDE.md"
    assert (CLAUDE_MD.parent / ".planning").is_dir()
