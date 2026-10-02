"""The external-review fact package must not leak this project's conclusions.

Run automatically rather than by hand, because the boundary was crossed once
already: §5's statistical rules carried *"this moved one p-value from 0.016 to
0.182"* and *"took the count surviving Benjamini-Hochberg from 1 to 0"* into a
document whose whole purpose is to withhold past results. A hand-run scan found
neither — it looked for strategy names and verdict words, and those sentences
contain neither. CodeRabbit caught them on PR #215, after the external reviewer
had already read them.

So the check lives in the suite now, and the predicate is tested on synthetic
input as well as on the real document. A document-only assertion passes whether
or not it still consults its predicate, which is a gap this repo has now hit
three times.
"""

from __future__ import annotations

import pytest

from research.external_review_leakcheck import (
    FACT_PACKAGE,
    REQUIRED_FACTS,
    find_leaks,
    missing_required_facts,
)

def test_the_fact_package_exists():
    """**Not a skip condition.** A module-level `skipif` on the document's
    existence makes every check below pass silently the moment the file is
    deleted or renamed — which is the exact scenario in which a leak check
    matters least and a missing *record* matters most. The document is the
    evidence for conclusions now written into `CLAUDE.md`, so its absence is a
    failure rather than a reason to stop looking. Flagged on review of PR #215,
    while the operator was in fact deleting the generated paste bundles."""
    assert FACT_PACKAGE.exists(), (
        f"{FACT_PACKAGE} is missing -- it is the record of what the external "
        "reviewer was given, and the basis of the rules derived from the reply"
    )


def test_the_fact_package_leaks_nothing():
    """Every hit is read by hand before being silenced, so the clean state is
    the asserted one — a known false positive would be fixed in the document or
    the pattern, never tolerated here."""
    hits = find_leaks(FACT_PACKAGE.read_text(encoding="utf-8"))
    assert not hits, "withheld conclusions appear in the fact package:\n" + "\n".join(
        f"  [{h.bucket}] {h.term!r} at L{h.line_number}: {h.line[:96]}" for h in hits
    )


def test_every_fact_the_design_needs_is_present():
    """The opposite failure from a leak, and just as disqualifying: a reviewer
    without these produces generalities rather than a runnable design."""
    missing = missing_required_facts(FACT_PACKAGE.read_text(encoding="utf-8"))
    assert not missing, f"the fact package is missing: {', '.join(missing)}"


# The scan skips the document's own preamble, which legitimately describes what
# is withheld. Synthetic text therefore needs a section header to be scanned.
HEADER = "## 1. What the system is\n"


@pytest.mark.parametrize(
    "bucket,line",
    [
        # The shape that slipped past the first version -- a result with no name.
        ("MEASURED-OUTCOME", "this moved one p-value from 0.016 to 0.182"),
        ("MEASURED-OUTCOME", "took the count surviving Benjamini-Hochberg from 1 to 0"),
        ("MEASURED-OUTCOME", "one matched null ran 0.42-0.88 times the event arm's error"),
        ("MEASURED-OUTCOME", "correcting it cut t roughly threefold"),
        ("MEASURED-OUTCOME", "posted t = +2.388 on that window"),
        ("MEASURED-OUTCOME", "reaches DSR = 2.0e-05 against the project count"),
        ("MEASURED-OUTCOME", "mean annualized Sharpe +0.039 was the best"),
        # The shapes the first version did catch.
        ("IDENTITY", "daily-tsmom-ensemble was run against that window"),
        ("ARC", "see rd-u for the derivation"),
        ("VERDICT", "the stage produced no candidate produced at all"),
        ("DIAGNOSIS", "the real finding is about the windows"),
        ("WITHHELD-ARITH", "the floor there is 0.315 over the full span"),
    ],
)
def test_what_counts_as_a_leak(bucket, line):
    """The predicate on synthetic input, so removing its use in the real check
    fails here even while the document itself is clean."""
    hits = find_leaks(HEADER + line)
    assert hits, f"not flagged: {line!r}"
    assert any(h.bucket == bucket for h in hits), (
        f"expected bucket {bucket}, got {[h.bucket for h in hits]}"
    )


@pytest.mark.parametrize(
    "line",
    [
        # A verdict CATEGORY is a rule the reviewer needs, not an outcome.
        "A run below the floor is reported `INCONCLUSIVE-DATA-LIMITED`.",
        # Available tooling is a resource, not a finding.
        "PSR, Deflated Sharpe, block bootstrap, Benjamini-Hochberg, Stouffer combination.",
        # A measurement the package is supposed to give.
        "Round trip is 30-33 bp, of which 20 bp is 거래세.",
        # A rule with a threshold in it.
        "Profit factor floor 1.3-1.5; the mean is what is scored.",
        # The formula itself, which is an input rather than a result.
        "floor = 1.6449 / sqrt(years) on daily-resampled returns",
    ],
)
def test_what_does_not_count_as_a_leak(line):
    """False positives matter here: this check gates a document that must stay
    useful, and a scan that flags its own required content gets switched off."""
    assert not find_leaks(HEADER + line), f"wrongly flagged: {line!r}"


@pytest.mark.parametrize("label,needle", sorted(REQUIRED_FACTS.items()))
def test_a_missing_required_fact_is_reported(label, needle):
    """On synthetic input, because the real document is complete.

    `assert not missing` passes on an empty list however that list was
    produced — so a predicate stubbed to return nothing makes this check
    *greener*, not redder. A mutation doing exactly that survived until this
    existed, which is the third time that shape has appeared in this repo.
    """
    text = FACT_PACKAGE.read_text(encoding="utf-8")
    without = text.replace(needle, "[removed]")
    assert needle not in without, f"{needle!r} not actually removable from the document"
    assert label in missing_required_facts(without), (
        f"removing {needle!r} was not reported as missing {label!r}"
    )


def test_the_preamble_is_exempt_but_the_body_is_not():
    """The document explains its own design above §1, naming what it withholds.
    Scanning that would flag the explanation; not scanning the body would make
    the check vacuous."""
    leaky = "the real finding is about the windows"
    assert not find_leaks(f"# Title\n\n{leaky}\n\n## 1. Section\n")
    assert find_leaks(f"# Title\n\n## 1. Section\n\n{leaky}\n")
