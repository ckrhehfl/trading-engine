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
#: risk: a decimal, a comma-grouped integer, **or any integer carrying a
#: percent sign**. A bare small integer ("two planes", "section 3") is prose
#: and is deliberately not matched.
#:
#: **The percent case was missing, and it is the one this repo's limits are
#: written in.** The first version required a decimal or a comma group, so
#: `docs/` could have copied `CLAUDE.md`'s `20%` drawdown ceiling, `2%` max
#: order notional or `99%` uptime floor and this check would have passed.
#: Reported on review; every one of those is a Risk-Parameter-class figure
#: that must exist in exactly one file.
#: **The `x`-suffixed leverage multiplier was the second missing form.** The
#: percent case was added on the first review round of this file; `2x` and `3x`
#: were still invisible, and they are how every leverage limit in `CLAUDE.md` is
#: written — canary max `2x`, stable max `3x`, and
#: `RiskLimits.ABSOLUTE_MAX_LEVERAGE`'s own `2x` entry gate. A `docs/` file could
#: have copied any of them. Reported on review of PR #207.
#:
#: The `x` branch carries a trailing non-word guard the `%` branch does not need,
#: so a hex-looking `0x1F` is not read as the figure `0x`.
_FIGURE = re.compile(
    r"(?<![\w.])[-−+]?\d+(?:[.,]\d+)*(?:%|[xX](?!\w))"
    r"|(?<![\w.])[-−+]?\d+(?:[.,]\d+)+"
)

#: Documents that are allowed to carry figures because their whole job is
#: operational numbers a human types at a prompt. The runbook's ports, sleep
#: intervals and cron minutes are not claims about the system's behaviour.
#:
#: **Scoped to the figure check only, which it was not.** One exemption set
#: filtered `_docs_files()` itself, so exempting the runbook for its cron
#: minutes also quietly exempted it from both *rule* checks below — and it was
#: restating a fail-closed property when that was noticed. A file excused from
#: one check silently leaving the others is the inert-guard shape this repo has
#: paid for repeatedly, so the two lists are now separate and the rule checks
#: see every document.
_FIGURE_EXEMPT = {"paper-trading-runbook.md"}


def _docs_files() -> list[pathlib.Path]:
    return sorted(DOCS.glob("*.md"))


def _figure_checked_files() -> list[pathlib.Path]:
    return [p for p in _docs_files() if p.name not in _FIGURE_EXEMPT]


def test_docs_exists_and_is_not_empty():
    """A guard over an empty directory passes vacuously, which is the inert
    shape this repo has paid for three times."""
    assert DOCS.is_dir(), "docs/ is missing"
    assert _docs_files(), "no docs/*.md at all -- every check here is inert"
    assert _figure_checked_files(), (
        "every docs/*.md is figure-exempt -- the figure check is inert"
    )


@pytest.mark.parametrize("path", _figure_checked_files(), ids=lambda p: p.name)
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


@pytest.mark.parametrize(
    "text, expected",
    [
        # The percent forms, which is what this repo's limits are written in.
        ("a 20% drawdown ceiling", ["20%"]),
        ("max order notional 2%", ["2%"]),
        ("uptime >= 99%", ["99%"]),
        ("max drawdown 20.135%", ["20.135%"]),
        ("-0.5% daily loss limit", ["-0.5%"]),
        # The leverage forms, which were the second missing case.
        ("leverage hard max 2x", ["2x"]),
        ("documented max 3x", ["3x"]),
        ("account-wide leverage at 20X", ["20X"]),
        ("1.5x the stop distance", ["1.5x"]),
        # ...but not an identifier or a hex-looking token that merely ends in x.
        ("0x1F is a byte", []),
        ("the 2xl breakpoint", []),
        # Decimals and comma groups, unchanged.
        ("PSR 0.9705", ["0.9705"]),
        ("Combined pool 4,374", ["4,374"]),
        # Prose integers stay out, or every line becomes a figure line.
        ("two planes and three seams", []),
        ("section 3, item 7", []),
        ("Java 21 + Gradle", []),
    ],
)
def test_what_counts_as_a_duplicable_figure(text, expected):
    """Pinned because the percent case was **missing** and it is the form this
    repo's limits use: `20%` drawdown ceiling, `2%` max order notional, `99%`
    uptime floor. `docs/` could have copied any of them and this check would
    have passed. Reported on review of PR #207."""
    assert _FIGURE.findall(text) == expected


#: Phrases that are the *signature* of a safety property `CLAUDE.md` owns.
#:
#: **This is a blocklist and cannot be complete**, which this project normally
#: treats as a reason to reject a guard. It is kept anyway, narrowly, because
#: the failure it catches was live: `docs/architecture.md`'s first execution-
#: modes table carried a "kill switch at construction" column restating the
#: `kis-paper` unconditional trip — three lines below the same document's own
#: statement that it does not duplicate safety properties. A human reviewer
#: caught it; nothing mechanical could, because a duplicated *figure* is
#: detectable and a restated *rule* is not.
#:
#: So it is labelled for what it is: it catches these known phrases, not the
#: category. The general case stays a judgement call at review time, exactly
#: as this module's docstring says.
#: **Matched against `_flat()`ed text, and the phrases are written for that.**
#: This test read the raw source while the layering-invariant one beside it read
#: the flattened form — so the weaker normalisation was the one guarding the
#: older list. `CLAUDE.md`'s own wording is ``trips `KillSwitch`
#: unconditionally``, whose backticks defeat a raw search for
#: `trips unconditionally`, and a line wrap between any two words of any phrase
#: here would have done the same. Reported on review of PR #207: not a live
#: safety failure, a check that could be walked past by copying the sentence
#: verbatim.
_SAFETY_PHRASES = (
    "trips killswitch unconditionally",
    "trips unconditionally",
    "trips the kill switch",
    "fails closed",
    "fail closed",
    "refuses to start",
    "must not be weakened",
    "do not weaken",
)


#: Labels from `CLAUDE.md`'s Risk Parameters. **No `docs/` file may name one,
#: including a figure-exempt one**, which is the hole this closes: the runbook is
#: exempt from the figure check for its ports and cron minutes, so
#: `max order notional 2%` written there would have carried a Risk Parameter past
#: every check in this module. It has no reason to name one — an operator reads
#: limits from `CLAUDE.md` or from `RiskLimits`, and a second copy in a runbook is
#: the drift this whole module exists to prevent. Reported on review of PR #207.
#:
#: Labels rather than numbers, deliberately: a bare `2%` in a runbook could be
#: anything, while `max order notional` can only be the risk parameter. Generic
#: words (`weekly`, `monthly`) are left out for the opposite reason — a cron
#: schedule is allowed to say them.
_RISK_PARAMETER_LABELS = (
    "base leverage",
    "max leverage",
    "max order notional",
    "daily loss limit",
    "weekly loss limit",
    "monthly loss limit",
    "hard stop",
    "emergency stop",
    "absolute_max_leverage",
)


@pytest.mark.parametrize("path", _docs_files(), ids=lambda p: p.name)
def test_no_docs_file_names_a_risk_parameter(path: pathlib.Path):
    """Risk Parameters live in `CLAUDE.md` and in `RiskLimits`, nowhere else.

    Applies to **every** file, the figure-exempt runbook included — that
    exemption is for operational numbers a human types at a prompt, and a risk
    limit is not one.
    """
    text = _flat(path.read_text(encoding="utf-8")).lower()
    found = [label for label in _RISK_PARAMETER_LABELS if label in text]
    assert not found, (
        f"{path.name} names risk parameter(s) {found}. Changing one of these "
        f"needs explicit human approval, which a second copy in docs/ quietly "
        f"routes around -- point at CLAUDE.md's Risk Parameters instead."
    )


def test_the_risk_parameter_check_covers_the_figure_exempt_file():
    """The hole, asserted rather than assumed.

    A file excused from one check silently leaving the others is the inert-guard
    shape this repo has paid for repeatedly, so the coverage is pinned: the
    exempt file is in the risk-parameter check's own parameter list.
    """
    assert _FIGURE_EXEMPT, "nothing is exempt, so this test proves nothing"
    checked = {p.name for p in _docs_files()}
    assert _FIGURE_EXEMPT <= checked, (
        f"{_FIGURE_EXEMPT - checked} is figure-exempt and reaches no other check"
    )
    assert not _FIGURE_EXEMPT <= {p.name for p in _figure_checked_files()}, (
        "the figure exemption does nothing, so it is not the exemption under test"
    )


#: The same idea for the **layering invariant**, which is an invariant rather
#: than a safety property and so gets its own list rather than being filed
#: under one.
#:
#: **Both phrases are here because both got through.** `docs/architecture.md`
#: §3 restated the implementation count as a verbatim blockquote — *"must only
#: ever be, exactly two"* — and restated *"it never means writing a new
#: `OrderExecutor`"* nine lines later, in a document whose own header says it
#: does not repeat invariants and while `CLAUDE.md` says in as many words that
#: none of what it holds *"is repeated in `docs/`"*. A human reviewer caught
#: it on PR #207; this module's docstring had already disclosed why nothing
#: mechanical would.
#:
#: **So this narrows the disclosed gap by exactly the two sentences that got
#: through, and claims nothing more.** A blocklist grown one incident at a
#: time is still a blocklist: the category — a rule restated in prose no
#: regex anticipated — stays a judgement call at review time.
_INVARIANT_PHRASES = (
    "must only ever",
    "never means writing",
)


@pytest.mark.parametrize("path", _docs_files(), ids=lambda p: p.name)
def test_a_docs_file_does_not_restate_the_layering_invariant(path: pathlib.Path):
    """A binding count is stated once, where it binds.

    `docs/` may say *which* implementations exist — that is the structure, and
    keeping it current is why the file was split out. What it may not say is
    how many are **permitted**, because two copies of a limit drift and the
    copy a reader believes is whichever they opened.
    """
    text = _flat(path.read_text(encoding="utf-8")).lower()
    found = [phrase for phrase in _INVARIANT_PHRASES if phrase in text]
    assert not found, (
        f"{path.name} restates invariant language {found}. Describe what the "
        f"code is and point at CLAUDE.md's layering invariant for what is "
        f"permitted -- a count written in two files becomes two counts."
    )


def test_the_safety_blocklist_survives_CLAUDE_MDs_own_formatting():
    """The verbatim sentence must be caught, backticks and line wrap included.

    Pinned because copying `CLAUDE.md`'s wording into `docs/` is the exact move
    this check is for, and its own markup was enough to get past the raw
    search that used to run here.
    """
    verbatim = "**`forKisPaper()` trips `KillSwitch` unconditionally** at construction"
    wrapped = "`forKisPaper()` trips `KillSwitch`\nunconditionally at construction"
    for text in (verbatim, wrapped):
        assert [p for p in _SAFETY_PHRASES if p in _flat(text).lower()] == [
            "trips killswitch unconditionally"
        ], f"CLAUDE.md's own phrasing got past the blocklist: {text!r}"


def test_what_the_invariant_blocklist_catches():
    """Pinned so the narrowness is visible rather than asserted.

    The first phrase alone would have passed the second sentence, which is the
    whole reason a blocklist cannot be trusted as a category check.
    """
    got_through = (
        "There are, and must only ever be, exactly two `OrderExecutor` "
        "implementations -- full stop."
    )
    second = "A new venue means writing a new `ExchangeAdapter`. It never means writing a new `OrderExecutor`."
    describing = "Two exist, both in `:execution`: `PaperBroker` and `ExchangeOrderExecutor`."

    assert [p for p in _INVARIANT_PHRASES if p in _flat(got_through).lower()] == [
        "must only ever"
    ]
    assert [p for p in _INVARIANT_PHRASES if p in _flat(second).lower()] == [
        "never means writing"
    ]
    assert not [p for p in _INVARIANT_PHRASES if p in _flat(describing).lower()], (
        "describing which implementations exist must stay allowed -- that is "
        "the living-structure content the docs/ split exists to hold"
    )


@pytest.mark.parametrize("path", _docs_files(), ids=lambda p: p.name)
def test_a_docs_file_does_not_restate_a_safety_property(path: pathlib.Path):
    """A safety property is stated once, in the file every AI session reads."""
    text = _flat(path.read_text(encoding="utf-8")).lower()
    found = [phrase for phrase in _SAFETY_PHRASES if phrase in text]
    assert not found, (
        f"{path.name} restates safety-property language {found}. State it in "
        f"CLAUDE.md and point here — an AI session is guaranteed to have read "
        f"that file and is not guaranteed to have opened this one."
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
