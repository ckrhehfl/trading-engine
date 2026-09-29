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


#: A block typed inside a shell that already belongs to `OWNER` -- the `tmux`
#: session §8b opens. Marked explicitly rather than inferred: the commands there
#: carry no `sudo`, correctly, because the session is already that user's, and no
#: scan of the block itself can tell that from an unwrapped command. The marker
#: is useful to a reader for the same reason it is needed here.
_ALREADY_OWNER = re.compile(rf"^#\s*already\s+{OWNER}\b", re.M)

#: Sections whose commands run on the LOCAL machine, not the deployment: the
#: prerequisites, the fresh-clone setup, and the sync that pulls the audit trail
#: down over `gcloud`. An allowlist rather than a blocklist, so a section added
#: later is treated as the deployment's and has to say otherwise — the noisy
#: direction, which is the one `CLAUDE.md` asks for when a declaration is absent.
_LOCAL_SECTIONS = frozenset({"1", "2", "7b"})

#: Invoking something by a path relative to the checkout, or its virtualenv.
#: This is the shape that slips past every other rule here: `scripts/x.sh` and
#: `cd python && .venv/bin/python -m live.dashboard` name no user, no `~` and no
#: `tmux`, so nothing else in this file sees them. Three such commands survived
#: the first mutation run for exactly that reason.
_CHECKOUT_RELATIVE = re.compile(r"(?:^|[\s;&|(])(?:\./)?scripts/\S+\.sh|\.venv/bin/")


def split_commands(line: str) -> list[str]:
    """Split a line into the commands a shell would run, ignoring separators
    inside quotes.

    **A line is not a command, and treating it as one exempts the wrong half.**
    `./scripts/x.sh; sudo -u OWNER -H bash -lc '...'` has the correct form
    somewhere on the line, so any per-line rule clears the unprotected command
    in front of it. The separators inside a quoted `-lc` string are part of that
    command and must not split it, which is why this tracks quote state instead
    of calling `re.split`.
    """
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if line.startswith("&&", i) or line.startswith("||", i):
            parts.append("".join(buf))
            buf = []
            i += 2
            continue
        if ch in ";|&":
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _path_escapes_the_quoted_command(line: str) -> bool:
    """True when a checkout path sits OUTSIDE `-lc`'s command string.

    `sudo -u minjun4897 -H bash -lc 'cat' ~/trading-engine/x` carries the correct
    prefix and still fails the contract: `bash -lc` takes exactly one argument as
    the command string, so `~/trading-engine/x` becomes `$0` — and the caller's
    shell expanded that tilde before `sudo` ran. The prefix check alone accepts
    it, because the prefix really is there; what is wrong is where the path is.

    A line whose quote never closes is the multi-line form, where the shell stays
    open for the lines below and a path there is inside it. That is not an
    offender, so an unclosed quote returns False rather than guessing.
    """
    opening = re.search(r"-lc\s+(['\"])", line)
    if opening is None:
        return False
    quote = opening.group(1)
    rest = line[opening.end():]
    close = rest.find(quote)
    if close == -1:
        return False
    return "~/" in rest[close + 1:]


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


def _fenced_blocks_by_section() -> list[tuple[str, str]]:
    """Every ```bash block paired with the `## N.` section it sits in."""
    out: list[tuple[str, str]] = []
    section = "(preamble)"
    buf: list[str] | None = None
    for line in RUNBOOK.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line[3:].split(".")[0].strip()
            continue
        if line.startswith("```bash"):
            buf = []
            continue
        if line.startswith("```") and buf is not None:
            out.append((section, "\n".join(buf)))
            buf = None
            continue
        if buf is not None:
            buf.append(line)
    return out


def checkout_relative_offenders(section: str, block: str) -> list[str]:
    """Lines in `block` that invoke the checkout from outside the owner's shell.

    Exposed, and used by both the real check and its own synthetic test, because
    a guard whose only evidence is the document it was written against proves
    nothing: removing it would still pass while the document happens to be
    clean. `CLAUDE.md` records that shape — three inert fixtures in a row, each
    passing its own tests.
    """
    if section in _LOCAL_SECTIONS or _ALREADY_OWNER.search(block):
        return []
    offenders: list[str] = []
    inside = False
    for line in block.splitlines():
        if _OPENS_SHELL.search(line):
            inside = True
            continue
        if inside and line.strip() == "'":
            inside = False
            continue
        for command in split_commands(line.split("#", 1)[0]):
            if not _CHECKOUT_RELATIVE.search(command):
                continue
            if inside or (
                _CORRECT.search(command)
                and not _path_escapes_the_quoted_command(command)
            ):
                continue
            offenders.append(command)
    return offenders


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


def sudo_form_offenders(lines: list[str]) -> list[str]:
    """Lines invoking `sudo -u OWNER` in a form that mis-resolves a path.

    Extracted for the same reason `checkout_relative_offenders` is: the document
    currently contains none of these, so a check that only reads the document
    passes whether or not it still calls
    `_path_escapes_the_quoted_command`. Removing that call survived a mutation
    run until this function existed to be given synthetic input — which is the
    gap CodeRabbit named: a negative case proves the predicate rejects the bad
    form, never that anything still consults it.
    """
    return [
        command
        for line in lines
        for command in split_commands(line)
        if _ANY_SUDO.search(command)
        and (
            not _CORRECT.search(command)
            or _path_escapes_the_quoted_command(command)
        )
    ]


@pytest.mark.parametrize(
    "line,offending",
    [
        ("sudo -u minjun4897 -H bash -lc 'cat ~/trading-engine/x'", False),
        ("sudo -u minjun4897 cat ~/trading-engine/x", True),
        ("sudo -u minjun4897 -H cat ~/trading-engine/x", True),
        # The form the escape guard exists for: prefix right, path outside.
        ("sudo -u minjun4897 -H bash -lc 'bash' ~/trading-engine/x", True),
        ('sudo -u minjun4897 -H bash -lc "bash" ~/trading-engine/x', True),
        # Not addressed to the owner at all, so not this rule's business.
        ("tmux capture-pane -t =paper-trading -p", False),
        # Two commands on one line: a correct second one used to clear the
        # first, because the check read the whole line.
        (
            "sudo -u minjun4897 cat ~/trading-engine/x"
            " && sudo -u minjun4897 -H bash -lc 'true'",
            True,
        ),
        # The same shape where BOTH are correct stays clean.
        (
            "sudo -u minjun4897 -H bash -lc 'cat ~/x'"
            " && sudo -u minjun4897 -H bash -lc 'true'",
            False,
        ),
    ],
)
def test_what_the_sudo_form_check_rejects(line, offending):
    assert bool(sudo_form_offenders([line])) is offending


def test_every_command_run_as_the_owner_uses_the_login_shell_form():
    offenders = [
        (n, line.strip())
        for n, line in _code_lines()
        if sudo_form_offenders([line])
    ]
    assert not offenders, (
        f"these commands run as {OWNER} but let the caller's shell expand a "
        f"checkout path -- either no `-H bash -lc`, or a path left outside its "
        f"quoted command string:\n"
        + "\n".join(f"  line {n}: {t}" for n, t in offenders)
    )


def tilde_path_offenders(block: str) -> list[str]:
    """Commands using a `~/trading-engine` path from outside the owner's shell.

    **Per command, not per block**, which it was until CodeRabbit pointed out
    that a correct sibling then vouches for a bare one — the identical defect
    the tmux rule already had and had already been narrowed for. A block-scoped
    check passes `cat ~/trading-engine/x` on its own line as long as some other
    line in the block carries the right prefix.
    """
    if _ALREADY_OWNER.search(block):
        return []
    offenders: list[str] = []
    inside = False
    for line in block.splitlines():
        if _OPENS_SHELL.search(line):
            inside = True
            continue
        if inside and line.strip() == "'":
            inside = False
            continue
        if inside:
            continue
        for command in split_commands(line.split("#", 1)[0]):
            if not _TILDE_PATH.search(command):
                continue
            if _CORRECT.search(command) and not _path_escapes_the_quoted_command(command):
                continue
            offenders.append(command)
    return offenders


def test_no_tilde_path_into_the_checkout_is_left_to_the_login_shell():
    offenders = [
        o
        for block in re.findall(
            r"```bash\n(.*?)```", RUNBOOK.read_text(encoding="utf-8"), re.S
        )
        for o in tilde_path_offenders(block)
    ]
    assert not offenders, (
        f"these use a ~/trading-engine path without becoming {OWNER} via "
        f"`-H bash -lc`:\n" + "\n".join(f"  {b}" for b in offenders)
    )


@pytest.mark.parametrize(
    "block,expected",
    [
        # The hole: a correct command later in the block used to clear this one.
        ("cat ~/trading-engine/x\nsudo -u minjun4897 -H bash -lc 'true'", 1),
        # And the same on one line, separated by `;`.
        ("cat ~/trading-engine/x; sudo -u minjun4897 -H bash -lc 'true'", 1),
        ("sudo -u minjun4897 -H bash -lc 'cat ~/trading-engine/x'", 0),
        # A separator inside the quoted command string must not split it.
        ("sudo -u minjun4897 -H bash -lc 'cd ~/trading-engine && ls'", 0),
        # The multi-line form keeps the shell open for the lines below.
        ("sudo -u minjun4897 -H bash -lc '\ncat ~/trading-engine/x\n'", 0),
        ("# already minjun4897\ncat ~/trading-engine/x", 0),
    ],
)
def test_what_counts_as_a_tilde_offender(block, expected):
    assert len(tilde_path_offenders(block)) == expected


@pytest.mark.parametrize(
    "line,parts",
    [
        ("a; b", ["a", "b"]),
        ("a && b || c", ["a", "b", "c"]),
        ("cat x | tail -3", ["cat x", "tail -3"]),
        # Separators inside quotes belong to the quoted command.
        ("sudo -u x -H bash -lc 'cd y && ls; pwd'", ["sudo -u x -H bash -lc 'cd y && ls; pwd'"]),
        ('sh -c "a; b" && c', ['sh -c "a; b"', "c"]),
    ],
)
def test_how_a_line_splits_into_commands(line, parts):
    assert split_commands(line) == parts


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
        if _ALREADY_OWNER.search(block):
            continue
        inside = False
        for line in block.splitlines():
            if _OPENS_SHELL.search(line):
                inside = True
                continue
            if inside and line.strip() == "'":
                inside = False
                continue
            # Comments only, never commands: "inside the tmux session opened
            # above" is prose about tmux, not a call to it, and the first version
            # of this check flagged it.
            bare = line.split("#", 1)[0]
            if _TMUX.search(bare) and not inside and not _CORRECT.search(line):
                offenders.append(line.strip())
    assert not offenders, (
        f"these tmux commands do not reach {OWNER}'s socket, which is per-UID:\n"
        + "\n".join(f"  {b}" for b in offenders)
    )


def test_no_checkout_relative_command_runs_outside_the_owners_shell():
    """The rule the other three cannot see, and the one that mattered most.

    `./scripts/vps-deploy.sh --check` and
    `cd python && .venv/bin/python -m live.dashboard` name no user, no `~` and no
    `tmux`. All three predicates above pass them, and all three commands fail on
    the deployment because `minju` has no checkout. They survived this file's
    first mutation run untouched.
    """
    offenders = [
        f"§{section}: {line}"
        for section, block in _fenced_blocks_by_section()
        for line in checkout_relative_offenders(section, block)
    ]
    assert not offenders, (
        "these invoke the checkout without becoming "
        f"{OWNER} first, so they run against /home/minju:\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


@pytest.mark.parametrize(
    "section,block,expected",
    [
        # The three shapes that survived, each now an offender.
        ("6b", "./scripts/vps-deploy.sh --check", 1),
        ("7", "cd python && .venv/bin/python -m live.dashboard", 1),
        ("7", "scripts/paper-trading-monitor.sh", 1),
        # Correct: wrapped, and the path inside the command string.
        ("6b", "sudo -u minjun4897 -H bash -lc '~/trading-engine/scripts/x.sh'", 0),
        # Correct: inside a shell an earlier line opened.
        ("7", "sudo -u minjun4897 -H bash -lc '\ncd ~/trading-engine\nscripts/x.sh\n'", 0),
        # A path that escapes the quotes is the OTHER rule's business -- it is
        # not checkout-relative, it is an absolute path the caller expanded.
        # `test_every_command_run_as_the_owner_uses_the_login_shell_form`
        # rejects it, verified by mutation.
        # A local section is exempt, which is what keeps the fresh-clone setup
        # and the gcloud-side sync from being rewritten into nonsense.
        ("2", "cd python && uv sync && cd ..", 0),
        # The marker exempts a block typed inside the owner's own session.
        ("8b", "# already minjun4897\ncd ~/trading-engine/python\npython3 -m data.krx_scan", 0),
        ("7b", "PYTHONPATH=python python/.venv/bin/python -m live.sync_live_signals", 0),
        # A mention inside a comment is not an invocation.
        ("4", "# see scripts/paper-trading-daily-signal.sh for why", 0),
    ],
)
def test_what_counts_as_a_checkout_relative_offender(section, block, expected):
    """The predicate on synthetic input, so deleting its use in the real check
    fails here even while the document itself is clean.

    That is the whole point: a negative case alone would only prove the current
    implementation rejects the bad form, not that anything still calls it.
    """
    assert len(checkout_relative_offenders(section, block)) == expected


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


@pytest.mark.parametrize(
    "command,escapes",
    [
        # The prefix is right and the path is still outside the command string,
        # so the caller's shell expands it. CodeRabbit caught this one.
        ("sudo -u minjun4897 -H bash -lc 'cat' ~/trading-engine/x", True),
        ('sudo -u minjun4897 -H bash -lc "cat" ~/trading-engine/x', True),
        ("sudo -u minjun4897 -H bash -lc 'cat ~/trading-engine/x'", False),
        # The multi-line form: the quote stays open, so the lines below are
        # inside the owner's shell.
        ("sudo -u minjun4897 -H bash -lc '", False),
        ("sudo -u minjun4897 -H bash -lc 'cd ~/trading-engine/python && ls'", False),
    ],
)
def test_a_path_outside_the_quoted_command_is_an_offender(command, escapes):
    assert _path_escapes_the_quoted_command(command) is escapes
