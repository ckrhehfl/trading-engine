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
    # **The COUNT, not the fixed phrase.** `"step 2 must keep these"` is
    # printed unconditionally, so asserting only that would pass with a rule
    # count of zero. Reported on review.
    text = report([rule])
    assert "1 survive step 1 and read as a rule" in text, text
    assert "0 survive step 1 and read as narrative" in text, text


def test_an_ORPHAN_is_not_counted_as_a_trim_candidate():
    """**Reported on review**, and the two figures contradicted each other on
    the same screen: a two-way rule/narrative split counted orphan narrative
    lines as trim candidates while the report also said they cannot be
    trimmed. Orphans are now counted only as orphans."""
    verdicts = [
        LineVerdict(1, "measured 0.716 once", ["0.716"], missing=["0.716"]),
        LineVerdict(2, "measured 0.9705 once", ["0.9705"]),
        LineVerdict(3, "FEE_BPS = 5 and must not be tuned", ["5"],
                    rule_markers=["= "]),
    ]
    text = report(verdicts)
    assert "1 carry a figure found nowhere else" in text, text
    assert "1 survive step 1 and read as a rule" in text, text
    assert "1 survive step 1 and read as narrative" in text, text
    assert "Only that last figure -- 1 --" in text, text


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


# ------------------------- what counts as a heading, and as a fence


def test_a_line_starting_with_HASH_is_not_automatically_a_heading():
    """**Reported on review, with the consequence measured.**
    `CLAUDE.md:188` reads `#103-#106). Full design record, ...` — prose, and
    the only such line in the file. Read as a depth-1 heading it cut the
    `## Architecture` section off at 187 instead of 318, silently dropping
    both `₩250,000` lines (255 and 300). Markdown requires whitespace or end
    of line after the `#` run.
    """
    from research.figure_survival import CLAUDE_MD, section_span

    text = CLAUDE_MD.read_text(encoding="utf-8")
    lines = text.splitlines()
    lo, hi = section_span(text, "Architecture")

    # **Asserted as properties, not as line numbers.** An earlier version
    # pinned `(116, 318)` — the literal span at the time — in the one test
    # whose own docstring says a line number rots on the next edit. It rotted
    # on the next edit: the 2026-09-25 documentation split moved the section's
    # body to `docs/architecture.md` and every number shifted.
    assert lines[lo - 1].strip() == "## Architecture"
    assert hi == len(lines) or lines[hi].startswith("## "), (
        f"the span does not end immediately before a top-level heading: "
        f"line {hi + 1} is {lines[hi][:60]!r}"
    )

    # The section must still reach its own last paragraph rather than being
    # truncated by something that merely looks like a heading.
    body = "\n".join(lines[lo - 1 : hi])
    assert "Still out of scope" in body, (
        "the Architecture section was cut short of its final block"
    )

    # **The `#`-prefixed prose line this test was written for is no longer in
    # `CLAUDE.md`.** `#103-#106).` began a line until the documentation split
    # rewrote that paragraph, so the real-file half can no longer exercise the
    # defect. Recorded rather than quietly dropped: the parser guard now lives
    # entirely in `test_heading_depth_follows_markdown`, which carries the
    # exact string as a synthetic case and does not depend on the file keeping
    # it.
    #
    # **Fenced blocks are excluded, and the first version was not** — it would
    # have fired on a `#!/bin/sh` shebang or a `#` comment inside a ```bash
    # block, neither of which is a heading. That made the assertion contradict
    # the very parser it guards, since `section_span` correctly ignores fenced
    # lines. `CLAUDE.md` carries no such block today, so this was a trap laid
    # for whoever adds the first one. Reported on review of PR #207.
    import re as _re

    from research.figure_survival import prose_lines

    prose = prose_lines(text)
    assert not [
        line
        for line in prose
        if line.startswith("#") and not _re.match(r"^#{1,6}(\s|$)", line)
    ], "a #-prefixed prose line is back -- re-point the real-file assertion at it"


@pytest.mark.parametrize(
    "line, depth",
    [
        ("# Title", 1),
        ("## Two", 2),
        ("###### Six", 6),
        ("#", 1),
        ("#103-#106). prose", None),
        ("#hashtag", None),
        ("####### seven is too many", None),
        # CommonMark allows up to THREE spaces of indentation on an ATX
        # heading; four makes it an indented code block. Requiring column zero
        # made a legal heading read as prose and lost a section boundary, and
        # was inconsistent with the fence rule beside it, which accepted any
        # indentation. Reported on review.
        (" # one space", 1),
        ("   ### three spaces", 3),
        ("    #### four spaces is a code block", None),
        ("\t## a tab is not 0-3 spaces", None),
        ("text # not at the start", None),
    ],
)
def test_heading_depth_follows_markdown(line, depth):
    from research.figure_survival import _heading_depth

    assert _heading_depth(line) == depth


def test_a_LONGER_fence_is_not_closed_by_a_shorter_run_inside_it():
    """**Reported on review.** A block opened with four backticks legitimately
    contains three-backtick lines; toggling on every ``` would close it early
    and then read the following `##` code line as a section boundary. Narrow
    today — `CLAUDE.md` uses three everywhere — and pinned so it stays fixed.

    Tilde fences are deliberately out of scope: the contract here does not
    define them and adding them would be a guess rather than a fix.
    """
    from research.figure_survival import section_span

    doc = "\n".join([
        "## Wanted",                 # 1
        "````markdown",              # 2  opens with FOUR
        "```",                       # 3  three -- must NOT close it
        "## this is sample text, not a heading",  # 4
        "```",                       # 5  three -- still must not close it
        "````",                      # 6  four -- closes
        "PSR 0.9705",                # 7
        "## Next",                   # 8
    ])
    assert section_span(doc, "Wanted") == (1, 7)


def test_an_EXACT_heading_match_wins_over_a_substring_one():
    """Matching is by substring, which is genuinely ambiguous in this file:
    `"Architecture"` occurs in `## Architecture` and in `## Long-term Design
    Targets (shape the architecture now, not built now)`. Before this,
    `audit("Architecture")` silently returned the second one. Disclosed rather
    than hidden — where no exact match exists the first substring hit wins,
    and a caller wanting the other names it more precisely.
    """
    from research.figure_survival import section_span

    doc = "\n".join([
        "## Shaping the architecture later",  # 1
        "0.9705",                             # 2
        "## Architecture",                    # 3
        "0.716",                              # 4
        "## Next",                            # 5
    ])
    assert section_span(doc, "Architecture") == (3, 4)
    assert section_span(doc, "Shaping") == (1, 2)


@pytest.mark.parametrize(
    "opener, opens",
    [
        ("```", True),
        ("```python", True),
        ("   ```bash", True),
        ("    ```four spaces is not a fence", False),
        # **An info string may not contain a backtick**, so this is an inline
        # code span, not a fence. Opening one here skips every heading until
        # the next backtick line, which extends a section SILENTLY — the exact
        # failure this module exists to catch. Reported on review.
        ("```foo``` and some text", False),
        ("`inline`", False),
        ("``two``", False),
    ],
)
def test_what_opens_a_fence_follows_commonmark(opener, opens):
    from research.figure_survival import section_span

    doc = "\n".join(["## Wanted", "0.9705", opener, "## Inside or a heading?",
                     "```", "0.716", "## Next", "4,374"])
    lo, hi = section_span(doc, "Wanted")
    if opens:
        # The `##` on line 4 is inside the block, so the section runs past it
        # and ends at the real `## Next` on line 7.
        assert (lo, hi) == (1, 6), f"{opener!r} should have opened a fence"
    else:
        # Line 4 is a real heading, so the section ends there.
        assert (lo, hi) == (1, 3), f"{opener!r} should NOT have opened a fence"


def test_a_closing_fence_carries_only_whitespace_after_its_run():
    """A line whose backtick run is long enough but which carries trailing
    text is not a closer. Closing early puts the rest of the block back in
    play as headings. Reported on review."""
    from research.figure_survival import section_span

    doc = "\n".join([
        "## Wanted",                 # 1
        "```",                       # 2  opens
        "``` trailing text",         # 3  NOT a closer
        "## still inside the block", # 4
        "```   ",                    # 5  closes (whitespace only)
        "0.9705",                    # 6
        "## Next",                   # 7
    ])
    assert section_span(doc, "Wanted") == (1, 6)


# ------------------- survival is matched on number tokens, not substrings


@pytest.mark.parametrize(
    "figure, corpus, why",
    [
        ("4374", "row id 4963594374 against", "inside a longer integer"),
        ("0.999999", "PSR : 0.99999900692081", "inside a longer decimal"),
        ("1.250", "floor : 1.250042", "a rounded form of a longer decimal"),
        ("20", "measured 2026-09-24 and 2026-08", "inside a date"),
        ("0.62", "the floor 10.625 here", "inside another decimal"),
        ("250", "a 2500 row table", "inside a longer integer"),
        ("95", "PSR 0.95 cleared", "after a decimal point"),
    ],
)
def test_a_substring_is_NOT_a_survival(figure, corpus, why):
    """**Reported on review, and it is the unsafe direction for this tool.**
    A bare `needle in corpus` test found `20` inside `2026-09-24`, `0.62`
    inside `10.625` and `250` inside `2500` — every one a **false survival**,
    which shrinks the orphan list and makes a line read as carrying nothing to
    lose.

    The first three cases are real occurrences from `.planning/`: the row id
    `4963594374`, a PSR printed at full precision, and a floor stored as
    `1.250042` where `CLAUDE.md` rounds it to `1.250`. That last one is a
    **rounded figure reported as an orphan**, which is conservative: it blocks
    a trim rather than permitting one.
    """
    from research.figure_survival import _normalise, _survives

    assert not _survives(figure, _normalise(corpus)), why


@pytest.mark.parametrize(
    "figure, corpus",
    [
        # `_normalise` strips a needle's `+`, so the sign in the corpus has to
        # be CONSUMED rather than forbidden. Forbidding it reported `+79.2`,
        # `+0.0184` and `+61.8` as existing nowhere when all three are in
        # `.planning/` — a false ORPHAN, found by checking the count rather
        # than by reading the regex.
        ("+79.2", "+73.5R of +79.2R and 4"),
        ("+0.0184", "member Sharpe +0.0184** six"),
        ("+61.8", "| 464 | +61.8% | **13"),
        ("-14.4", "raw mean -14.4 bp against"),
        # A trailing `.` is sentence punctuation unless a digit follows it.
        ("4,374", "Combined pool 4,374."),
        ("0.95", "clears 0.95."),
        # And the ordinary cases still work.
        ("20", "exactly 20 rows"),
        ("1,901", "1,901 bars, zero gaps"),
    ],
)
def test_a_real_occurrence_IS_a_survival(figure, corpus):
    from research.figure_survival import _normalise, _survives

    assert _survives(figure, _normalise(corpus)), f"{figure!r} vs {corpus!r}"


def test_the_sign_conflation_limitation_is_real_and_disclosed():
    """An unsigned needle matches a signed occurrence, so `20` survives on a
    corpus of `-20`. A false survival, the unsafe direction, accepted rather
    than fixed because distinguishing them needs the sign to be part of the
    figure and this file's own prose writes the same quantity both ways.

    Pinned so it is a known limitation rather than a surprise, and so closing
    it has to update this test on purpose.
    """
    from research.figure_survival import _normalise, _survives

    assert _survives("20", _normalise("a -20 reading"))


# ------------------------- a year is provenance, and a date is not a figure


@pytest.mark.parametrize(
    "text, why",
    [
        ("them are 2026 listings with zero bars", "a year in prose"),
        ("`2001` KOSPI200. Same tr_id", "the KOSPI200 INDEX CODE, not a year"),
        ("Bailey & López de Prado 2014,", "a citation year"),
        ("(Alexander & Fabozzi 2026) over the mean", "a citation year"),
        ("spanning 2000 to 2026; all four", "a year range"),
        ("the 2018 ordering could not see 2019–2026", "three years in one line"),
    ],
)
def test_a_standalone_four_digit_year_is_not_a_figure(text, why):
    """**Reported on review.** `2026` in "2026 listings" was extracted as a
    figure and then counted as *surviving* on the strength of a **date**
    elsewhere (`2026-09-24`) — so a figure existing nowhere read as one that
    does, the unsafe direction.

    All 24 such tokens in `CLAUDE.md` were read before excluding them: years in
    prose, citation years, or the KOSPI200 index code `2001`. Not one is a
    measurement.
    """
    assert figures_in(text) == [], why


def test_a_COMMA_GROUPED_count_in_the_year_range_is_still_a_figure():
    """The exclusion is narrower than it looks, and this file's own convention
    is what makes it safe: a count is written `1,901`, a year `1901`. So
    `1,901 bars` survives as a figure while `1901` alone would not.

    **Disclosed limitation**: an uncomma'd count in 1900-2100 is invisible.
    Accepted for the same reason the two-digit exclusion is — the alternative
    is 24 false survivals on every run.
    """
    assert figures_in("1,901 bars, zero gaps") == ["1,901"]
    assert figures_in("1901 bars") == []


def test_a_years_DATE_cannot_satisfy_a_bare_year_needle():
    """The second line of defence, kept even though the provenance rule now
    strips bare years before they become figures: the two guard different
    steps, and this one also stops a match landing on the left half of a
    range."""
    from research.figure_survival import _normalise, _survives

    assert not _survives("2026", _normalise("measured 2026-09-24"))
    assert not _survives("13", _normalise("the 13-15 bp band"))
    # And an ordinary figure is unaffected.
    assert _survives("0.95", _normalise("PSR 0.95 cleared"))


@pytest.mark.parametrize(
    "figure, corpus, why",
    [
        # **The gap the first hyphen guard left**, reported on review: `(?!-\d)`
        # blocks a date's YEAR but not its DAY. `_normalise("20%")` is `"20"`,
        # and `.planning/` puts a date in nearly every paragraph while
        # `CLAUDE.md` is full of `20%` ceilings — so integer percentages from
        # 10% to 31% could all survive on a day number. The earlier test corpus
        # happened to carry no date ending in 20, which is why it passed.
        ("20%", "a run on 2026-08-20 here", "a date's DAY fragment"),
        ("24%", "measured 2026-09-24 against", "a date's DAY fragment"),
        ("9%", "measured 2026-09-24 against", "a date's MONTH fragment"),
        ("2026", "measured 2026-09-24", "a date's YEAR fragment"),
        ("15", "the 13-15 bp band", "the RIGHT half of a range"),
        ("13", "the 13-15 bp band", "the LEFT half of a range"),
    ],
)
def test_neither_side_of_a_hyphen_can_vouch_for_a_figure(figure, corpus, why):
    from research.figure_survival import _normalise, _survives

    assert not _survives(figure, _normalise(corpus)), why


@pytest.mark.parametrize(
    "figure, corpus",
    [
        # The symmetric guard must not break sign consumption, which is
        # preceded by whitespace rather than by a digit-hyphen.
        ("-14.4", "raw mean -14.4 bp against"),
        ("+79.2", "of +79.2R and 4"),
        ("20%", "exactly 20% drawdown"),
        ("0.95", "PSR 0.95 cleared"),
    ],
)
def test_the_symmetric_guard_leaves_real_occurrences_alone(figure, corpus):
    from research.figure_survival import _normalise, _survives

    assert _survives(figure, _normalise(corpus)), f"{figure!r} vs {corpus!r}"


def test_prose_lines_excludes_every_fenced_block():
    """**The fence exclusion, proved synthetically.** It was inline in the
    assertion above and therefore inert: `CLAUDE.md` carries no `#` line
    inside a fence today, so deleting the exclusion changed nothing. Tested
    here on a document that does, so the guard can fail."""
    from research.figure_survival import prose_lines

    doc = "\n".join([
        "## Heading",           # kept
        "```bash",
        "#!/bin/sh",            # excluded -- a shebang, not a heading
        "# a comment",          # excluded
        "```",
        "prose",                # kept
        "````markdown",
        "```",                  # excluded -- shorter run cannot close a longer fence
        "## sample text",       # excluded
        "````",
        "   ```cron",
        "# */30 0-6 * * 1-5",   # excluded -- indented fence still a fence
        "   ```",
        "tail",                 # kept
    ])
    assert prose_lines(doc) == ["## Heading", "prose", "tail"]
