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
#:
#: **The quote after `-lc` is part of the form, not formatting.** `bash -lc`
#: takes exactly one argument as the command string, so
#: `... -H bash -lc cat ~/trading-engine/x` runs `cat` with `~/trading-engine/x`
#: as `$0` — and the caller's shell has already expanded that tilde, which is
#: the very defect this checks for. An earlier version of this pattern accepted
#: it; CodeRabbit caught that on review.
_CORRECT = re.compile(
    rf"sudo\s+(?:-n\s+)?-u\s+{OWNER}\s+-H\s+bash\s+-lc\s+['\"]"
)

#: Any invocation as that user at all.
_ANY_SUDO = re.compile(rf"sudo\s+(?:-n\s+)?-u\s+{OWNER}\b")

#: A tilde path into the checkout, which is what the wrong form mis-resolves.
_TILDE_PATH = re.compile(r"~/trading-engine\b")

#: A `tmux` subcommand that has to reach the owner's own server.
#:
#: **Its own rule, because neither check above sees it.** `tmux`'s default
#: socket is per-UID: measured 2026-09-29, the sessions live on
#: `/tmp/tmux-1001` (`minjun4897`) and the same `tmux ls` as the login user
#: answers `error connecting to /tmp/tmux-1002/default (No such file or
#: directory)`. A bare `tmux capture-pane -t =paper-trading -p` therefore names
#: no path and no user, passes both other predicates, and cannot see the
#: session at all — which is how it survived this file's first mutation run.
_TMUX = re.compile(r"\btmux\s+(?!-)[a-z]")


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


#: A line that *opens* the owner's shell for the lines that follow: the correct
#: prefix with nothing after the quote, i.e. the multi-line form.
_OPENS_SHELL = re.compile(
    rf"sudo\s+(?:-n\s+)?-u\s+{OWNER}\s+-H\s+bash\s+-lc\s+['\"]\s*$"
)


def test_every_tmux_command_reaches_the_owners_socket():
    """Line by line, not block by block, and that distinction is the whole test.

    A `tmux` line names neither the user nor a path, so it is invisible to both
    other predicates — which is how a bare `capture-pane` survived this file's
    first mutation run. Blocks were the obvious granularity and **also wrong**:
    the two `capture-pane` lines are separate complete commands, so checking the
    block let a correct sibling vouch for a bare one, and the same mutation
    survived a second time. A line is accepted only if it carries the prefix
    itself, or if an earlier line in that block opened the owner's shell and left
    it open.
    """
    text = RUNBOOK.read_text(encoding="utf-8")
    offenders: list[str] = []
    for block in re.findall(r"```bash\n(.*?)```", text, re.S):
        inside = False
        for line in block.splitlines():
            if _OPENS_SHELL.search(line):
                inside = True
                continue
            if inside and line.strip() == "'":
                inside = False
                continue
            if _TMUX.search(line) and not inside and not _CORRECT.search(line):
                offenders.append(line.strip())
    assert not offenders, (
        f"these tmux commands do not reach {OWNER}'s socket, which is per-UID:\n"
        + "\n".join(f"  {b}" for b in offenders)
    )


def test_every_bash_block_parses():
    """`bash -n` on each fenced block, which the other checks cannot substitute
    for.

    Added because wrapping commands in `sudo ... bash -lc '...'` makes an
    apostrophe inside the block a syntax error, and two comments in the raw-detail
    block had one — *"simulated loop's daily reports"* closed the quoted shell
    eight lines early. Every predicate above passed on it: the user is right, the
    paths are right, the tmux calls are wrapped, and the command still would not
    run.

    This is the check that steps outside the change's own assumptions, which is
    the category `change_check.py` records as the one that actually catches
    things.
    """
    import subprocess
    import tempfile

    text = RUNBOOK.read_text(encoding="utf-8")
    blocks = re.findall(r"```bash\n(.*?)```", text, re.S)
    assert len(blocks) > 5, f"only {len(blocks)} bash blocks found; the fence parse is wrong"
    broken: list[str] = []
    for block in blocks:
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
            fh.write(block)
            path = fh.name
        try:
            result = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
        finally:
            pathlib.Path(path).unlink(missing_ok=True)
        if result.returncode != 0:
            broken.append(
                f"{block.strip().splitlines()[0][:70]} -> "
                f"{result.stderr.strip().splitlines()[-1][:90]}"
            )
    assert not broken, "these blocks are not valid shell:\n" + "\n".join(
        f"  {b}" for b in broken
    )


def test_the_runbook_states_the_rule_once_rather_than_per_command():
    """The commands being right is not enough — the next one added has to be.

    Six commands were wrong in the same way, which is what a missing stated
    convention looks like. This pins that the explanation is present, including
    the measured login user, so a reader who wonders why the form is so long
    finds the answer instead of shortening it.
    """
    text = RUNBOOK.read_text(encoding="utf-8")
    for needed in ("-H bash -lc", "minju", OWNER, "does not exist", "per-UID"):
        assert needed in text, f"the runbook no longer explains the rule: {needed!r} is gone"


@pytest.mark.parametrize(
    "command,ok",
    [
        ("sudo -u minjun4897 -H bash -lc 'cat ~/trading-engine/x'", True),
        ("sudo -n -u minjun4897 -H bash -lc \"wc -l < ~/trading-engine/x\"", True),
        ("sudo -u minjun4897 cat ~/trading-engine/x", False),
        ("sudo -u minjun4897 -H cat ~/trading-engine/x", False),
        ("sudo -u minjun4897 bash -lc 'cat ~/x'", False),
        # `-lc` with an unquoted command: `cat` is the whole command string and
        # the path becomes $0, its tilde already expanded by the caller.
        ("sudo -u minjun4897 -H bash -lc cat ~/trading-engine/x", False),
        ("sudo -u minjun4897 -H bash -lc  ~/trading-engine/x", False),
    ],
)
def test_what_the_form_check_accepts(command, ok):
    """The predicate itself, on both sides.

    `-H` without `bash -lc` is the case worth pinning: it sets `HOME` for the
    new process but the caller's shell has already expanded `~` by then, so it
    reads as a fix and is not one.
    """
    assert bool(_CORRECT.search(command)) is ok
