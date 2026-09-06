"""Mechanical checks an engineering change must pass before it is pushed.

The engineering counterpart to `research/conclusion_check.py`, and built
to the same constraint: **every check here exists because this project
made that exact mistake, and each one names it.** A checklist of things
that merely sound like good practice becomes theatre nobody runs. A
checklist where every item has a scar attached gets run, because the
cost of skipping it is already known.

## Why this file exists at all

An audit on 2026-09-06 counted what actually caught the fifteen
significant defects of the preceding session:

| caught by | count |
|---|---|
| CodeRabbit review | ~7 |
| the real deployment | 3 |
| an external observable (sha256, `ls`, HTTP status) | 3 |
| reading the existing system before extending it | 1 |
| **the tests written alongside the code** | **~0** |

That last row is the finding. Almost nothing significant was caught by
the tests written for it -- they passed in every broken version. The
pattern behind it is one sentence:

> **A verification that shares an assumption with its implementation
> confirms the misunderstanding rather than catching it.**

Which is why the checks below are all forms of "step outside the thing
you just wrote": remove the guard and watch the test fail, run it where
it will actually run, compare against something the code cannot fake.

## Scope, stated so it is not mistaken for more

These catch **structural mistakes in a change's own construction**. They
cannot tell you the change was worth making, or that the design is right.
Those need a human, exactly as `conclusion_check`'s own scope note says
about asking the wrong question.

## Usage

    from research.change_check import (
        check_guard_fails_when_removed, check_no_shared_mutable_state,
        require_no_blockers,
    )

    findings = [
        check_guard_fails_when_removed(...),
        check_no_shared_mutable_state(...),
    ]
    require_no_blockers(findings)   # raises on severity="blocker"
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

from research.conclusion_check import BLOCKER, WARNING, Finding, format_findings, require_no_blockers

__all__ = [
    "BLOCKER",
    "WARNING",
    "Finding",
    "format_findings",
    "require_no_blockers",
    "check_guard_fails_when_removed",
    "check_guard_is_an_allowlist",
    "check_readonly_path_is_pure",
    "check_script_fails_closed",
    "check_no_shared_mutable_state",
    "check_reported_from_actual",
    "check_error_direction_declared",
]


_SHARED_ASSUMPTION_SCAR = (
    "Of fifteen significant defects in one session, roughly zero were "
    "caught by the tests written alongside the code -- they passed in "
    "every broken version."
)


def check_guard_fails_when_removed(
    *,
    guard: str,
    removed_and_observed_failing: bool,
    how_verified: str = "",
    check: str = "guard_fails_when_removed",
) -> Finding | None:
    """A guard whose test still passes without it is not tested.

    Pass `removed_and_observed_failing=True` only after actually
    disabling the guard and watching the test go red. Not "I read it and
    it looks right" -- the three false starts below all read fine.

    Scars, all from 2026-08/09:

    - `conftest.py`'s experiment-log isolation silently did nothing
      **three times running**, each version passing its own new tests.
      What caught it every time was a sha256 of the real log before and
      after, never a test.
    - `generate_mock_signal`'s concurrency test compared only the two
      final files, so two runs both publishing LONG would have passed.
      Removing the `flock` and watching it fail is what made it real.
    """
    if removed_and_observed_failing:
        return None
    return Finding(
        check=check,
        severity=BLOCKER,
        message=(
            f"the guard {guard!r} has not been shown to fail when removed"
            + (f" ({how_verified})" if how_verified else "")
            + ". Disable it, watch the test go red, restore it, watch it go green. "
            "A test that cannot fail when the thing it guards is gone is not a "
            "test -- and a guard that silently does nothing is worse than no "
            "guard, because it converts 'we might be exposed' into 'we are "
            "certain we are not' while the exposure continues."
        ),
        scar=(
            "conftest.py's log isolation was inert three versions running, each "
            "passing its own tests; the mock generator's concurrency test would "
            "have passed with both runs publishing the same side."
        ),
    )


def check_guard_is_an_allowlist(
    *,
    guard: str,
    rejects_by_default: bool,
    check: str = "guard_is_an_allowlist",
) -> Finding | None:
    """Enumerate what is permitted, not what is forbidden.

    A blocklist is a bet that you thought of everything. You did not.

    Scar: `generate_mock_signal`'s path guard rejected anything inside
    `var/live/signals/` and let everything *outside* through -- so a
    mistyped path could create or overwrite an unrelated file anywhere on
    the box. There was exactly one directory it had business writing to.
    """
    if rejects_by_default:
        return None
    return Finding(
        check=check,
        severity=BLOCKER,
        message=(
            f"{guard!r} enumerates what is forbidden rather than what is "
            f"allowed. Invert it: permit the known-good set and refuse "
            f"everything else, including inputs nobody has thought of yet."
        ),
        scar=(
            "The mock signal generator's path guard blocked the real strategy "
            "tree and allowed the entire rest of the filesystem."
        ),
    )


def check_readonly_path_is_pure(
    *,
    operation: str,
    mutations: Sequence[str] = (),
    check: str = "readonly_path_is_pure",
) -> Finding | None:
    """Anything named `--dry-run`, `--check`, `peek` or `preview` must
    leave no trace.

    Scar: `generate_mock_signal --dry-run` advanced the persisted side
    state, so a dry run changed the **next real** signal -- the one thing
    a "does not write" flag must not do.
    """
    if not mutations:
        return None
    return Finding(
        check=check,
        severity=BLOCKER,
        message=(
            f"{operation!r} presents as read-only but mutates: "
            f"{', '.join(mutations)}. Split the read from the write, and test "
            f"that the read-only path predicts exactly what the real one then "
            f"does without having changed it."
        ),
        scar="`--dry-run` advanced the side state and so changed the next real signal.",
    )


def check_script_fails_closed(
    script_path: str | Path, *, check: str = "script_fails_closed"
) -> Finding | None:
    """A verification script that cannot fail is not evidence.

    Reads the file and reports the two ways this project has actually
    produced a green result from a broken run: no `set -e`, and command
    output consumed without checking it.

    Scar: `verify-gate-a-kill-switch.sh` shipped with `set -uo pipefail`
    and unchecked `grep`s, so a failed `cd`, an unreadable classpath or a
    JVM that never started would have printed nothing and exited 0 --
    while being the evidence for a Gate A criterion.
    """
    path = Path(script_path)
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        return Finding(
            check=check,
            severity=BLOCKER,
            message=f"could not read {path} to check it: {exc}",
            scar="A verification script that cannot be read cannot be trusted either.",
        )

    problems: list[str] = []
    if not re.search(r"^\s*set\s+-[A-Za-z]*e", source, re.MULTILINE):
        problems.append("no `set -e` (or `-Eeuo pipefail`), so a failing step is ignored")
    # A grep whose result is neither tested nor `-q`-asserted proves nothing.
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "grep" not in stripped:
            continue
        if any(tok in stripped for tok in ("grep -q", "|| ", "&& ", "if ", "!", "$(")):
            continue
        problems.append(f"unchecked grep: {stripped[:70]}")
        break

    if not problems:
        return None
    return Finding(
        check=check,
        severity=BLOCKER,
        message=(
            f"{path} can report success while doing nothing: "
            + "; ".join(problems)
            + ". Assert every expected observation explicitly and exit non-zero "
            "when one is missing."
        ),
        scar=(
            "verify-gate-a-kill-switch.sh shipped without `set -e` and with "
            "unchecked greps, as the evidence for a Gate A criterion."
        ),
    )


def check_no_shared_mutable_state(
    *,
    resource: str,
    writers: Sequence[str],
    serialised_by: str | None = None,
    check: str = "no_shared_mutable_state",
) -> Finding | None:
    """Two things writing one resource need a lock, or separate resources.

    Scars, all real and all found late:

    - Both paper loops read the **same signal file**, so a mock feed
      would have driven the venue-connected one too -- 288 manufactured
      orders a day onto a real demo account.
    - `vps-bootstrap.sh` and `paper-trading-watchdog.sh` shared a
      `$CLASSPATH_CACHE.tmp`; a rename landing between the other's write
      and its size check made it discard a good cache and refuse to start.
    - The submission-marker store was a hardcoded path, so any isolated
      instance shared the live loop's risk-control file.
    """
    if len(writers) < 2 or serialised_by:
        return None
    return Finding(
        check=check,
        severity=BLOCKER,
        message=(
            f"{resource!r} is written by {len(writers)} independent things "
            f"({', '.join(writers)}) with nothing serialising them. Give each "
            f"its own resource, or put every read-modify-write behind one lock. "
            f"Ask specifically: is one of these writers connected to a real "
            f"venue?"
        ),
        scar=(
            "Both paper loops read the same signal file; a mock feed there would "
            "have sent 288 manufactured orders a day to a real demo account."
        ),
    )


def check_reported_from_actual(
    *,
    figure: str,
    source: str,
    is_execution_record: bool,
    check: str = "reported_from_actual",
) -> Finding | None:
    """A published number must come from what happened, not what was meant.

    Scar: Task C reported the tactical overlay's edge as **+45** from the
    signal-time book, which records a leg at the price the strategy *saw
    when deciding*. Rebuilt from real fills it was **−97** -- the gap was
    over three times the effect being measured, and it flipped the sign.
    """
    if is_execution_record:
        return None
    return Finding(
        check=check,
        severity=BLOCKER,
        message=(
            f"{figure!r} is computed from {source!r}, which records intent "
            f"rather than execution. Rebuild it from the real fills before "
            f"publishing it, and print both if the gap is worth seeing."
        ),
        scar=(
            "Task C's reported +45 gross edge was really -97 once rebuilt from "
            "fills; the gap was 3x the effect and reversed its sign."
        ),
    )


def check_error_direction_declared(
    *,
    defect: str,
    direction: str | None,
    check: str = "error_direction_declared",
) -> Finding | None:
    """Say which way a defect errs before saying how bad it is.

    `direction` must be `"safe"` or `"unsafe"`. It decides urgency.

    Scar: the same-looking bug went both ways within one session.
    Tests polluting `runs/experiments.jsonl` **inflated** `N`, which only
    lowers a DSR -- safe, and nothing had been wrongly passed, so the
    append-only log was left alone. The Task C runner logging nothing
    **deflated** `N`, which inflates every DSR computed against it --
    unsafe, and fixed the same hour. Identical shape, opposite urgency,
    and the direction is what told them apart.
    """
    if direction in ("safe", "unsafe"):
        return None
    return Finding(
        check=check,
        severity=WARNING,
        message=(
            f"the error direction of {defect!r} is not stated. Say whether it "
            f"fails safe (the wrong answer is the conservative one) or unsafe "
            f"(the wrong answer flatters the result). That decides whether this "
            f"is a note or an incident."
        ),
        scar=(
            "Test pollution inflated N (safe); an unlogged research run deflated "
            "it (unsafe). Identical shape, opposite urgency."
        ),
    )
