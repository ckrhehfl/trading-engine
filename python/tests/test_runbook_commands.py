"""The runbook's instance commands must resolve `~` as the user that owns the repo.

**The defect this exists for, measured on 2026-09-29 rather than reasoned
about.** `gcloud compute ssh paper-trading` logs in as `minju`; the deployment
lives under `minjun4897`; and `/home/minju/trading-engine` does not exist. Six
of the runbook's commands combined `sudo -u minjun4897` with a `~/trading-engine`
path, and `~` expands in the *caller's* shell (or, inside a single-quoted
`--command=`, in the remote *login* shell) — as `minju` either way. Run for real,
the old form answered:

    bash: line 1: /home/minju/trading-engine/var/live/live_signals.jsonl:
    No such file or directory

while `sudo -u minjun4897 -H bash -lc "wc -l < ~/trading-engine/..."` answered
`23`. `CLAUDE.md` already carried the underlying fact — *"the repo on the
instance lives under `minjun4897`, not the SSH login user"* — so the runbook
contradicted a fact the project had already written down.

**Why a test and not a convention.** A runbook is followed literally, often
while something is broken, and this failure mode produces `No such file or
directory` with nothing pointing at the cause. The rule is mechanical, so it
can be checked; the reason it needs checking is that the correct form is
longer than the wrong one and the wrong one looks right.

**What this cannot do**: it checks the *shape* of a command, not that the
command does the right thing. A well-formed command against the wrong path
passes.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
RUNBOOK = REPO / "docs" / "paper-trading-runbook.md"

#: The user the deployment's files belong to. Named here rather than matched
#: loosely, because the whole point is that it is *not* the login user.
OWNER = "minjun4897"

#: The only form that makes `~` and `$HOME` resolve as `OWNER`. `-H` sets
#: `HOME`; `bash -lc` is what re-expands the tilde inside that user's own
#: login shell rather than leaving the caller's expansion in place.
_CORRECT = re.compile(rf"sudo\s+(?:-n\s+)?-u\s+{OWNER}\s+-H\s+bash\s+-lc\b")

#: Any invocation as that user at all.
_ANY_SUDO = re.compile(rf"sudo\s+(?:-n\s+)?-u\s+{OWNER}\b")

#: A tilde path into the checkout, which is what the wrong form mis-resolves.
_TILDE_PATH = re.compile(r"~/trading-engine\b")


def _code_lines() -> list[tuple[int, str]]:
    """Every line inside a fenced block, with its 1-based file line number.

    Prose is excluded deliberately: the runbook's own explanation of this rule
    *quotes* the wrong form in a table, and a check that cannot tell an example
    from an instruction would forbid documenting the defect it guards.
    """
    lines = RUNBOOK.read_text(encoding="utf-8").splitlines()
    out: list[tuple[int, str]] = []
    inside = False
    for n, line in enumerate(lines, start=1):
        if line.startswith("```"):
            inside = not inside
            continue
        if inside:
            out.append((n, line))
    return out


def test_the_runbook_has_fenced_commands_to_check():
    """Otherwise every assertion below passes over an empty list.

    The fence parser is the part most likely to break silently — a change to
    how the document marks code would empty this and leave the real checks
    green while checking nothing.
    """
    code = _code_lines()
    assert len(code) > 50, f"only {len(code)} fenced lines found; the fence parse is wrong"
    assert any(OWNER in line for _, line in code), (
        f"no fenced command mentions {OWNER}; either the runbook stopped "
        f"addressing the deployment or the parse is wrong"
    )


def test_every_command_run_as_the_owner_uses_the_login_shell_form():
    offenders = [
        (n, line.strip())
        for n, line in _code_lines()
        if _ANY_SUDO.search(line) and not _CORRECT.search(line)
    ]
    assert not offenders, (
        f"these commands run as {OWNER} without `-H bash -lc`, so `~` and "
        f"`$HOME` resolve as the SSH login user instead:\n"
        + "\n".join(f"  line {n}: {t}" for n, t in offenders)
    )


def test_no_tilde_path_into_the_checkout_is_left_to_the_login_shell():
    """A `~/trading-engine` path is only correct inside the owner's own shell.

    Checked per fenced *block* rather than per line, because the correct form
    opens a quoted shell on one line and the paths follow on later ones — which
    is exactly what the two loop-start commands look like.
    """
    text = RUNBOOK.read_text(encoding="utf-8")
    offenders: list[str] = []
    for block in re.findall(r"```bash\n(.*?)```", text, re.S):
        if not _TILDE_PATH.search(block):
            continue
        if not _CORRECT.search(block):
            offenders.append(block.strip().splitlines()[0])
    assert not offenders, (
        "these blocks use a ~/trading-engine path without first becoming "
        f"{OWNER} via `-H bash -lc`:\n" + "\n".join(f"  {b}" for b in offenders)
    )


def test_the_runbook_states_the_rule_once_rather_than_per_command():
    """The commands being right is not enough — the next one added has to be.

    Six commands were wrong in the same way, which is what a missing stated
    convention looks like. This pins that the explanation is present, including
    the measured login user, so a reader who wonders why the form is so long
    finds the answer instead of shortening it.
    """
    text = RUNBOOK.read_text(encoding="utf-8")
    for needed in ("-H bash -lc", "minju", OWNER, "does not exist"):
        assert needed in text, f"the runbook no longer explains the rule: {needed!r} is gone"


@pytest.mark.parametrize(
    "command,ok",
    [
        ("sudo -u minjun4897 -H bash -lc 'cat ~/trading-engine/x'", True),
        ("sudo -n -u minjun4897 -H bash -lc \"wc -l < ~/trading-engine/x\"", True),
        ("sudo -u minjun4897 cat ~/trading-engine/x", False),
        ("sudo -u minjun4897 -H cat ~/trading-engine/x", False),
        ("sudo -u minjun4897 bash -lc 'cat ~/x'", False),
    ],
)
def test_what_the_form_check_accepts(command, ok):
    """The predicate itself, on both sides.

    `-H` without `bash -lc` is the case worth pinning: it sets `HOME` for the
    new process but the caller's shell has already expanded `~` by then, so it
    reads as a fix and is not one.
    """
    assert bool(_CORRECT.search(command)) is ok
