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


#: A block typed inside a shell that already belongs to `OWNER` -- the `tmux`
#: session §8b opens. Marked explicitly rather than inferred: the commands there
#: carry no `sudo`, correctly, because the session is already that user's, and no
#: scan of the block itself can tell that from an unwrapped command.
_ALREADY_OWNER = re.compile(rf"^#\s*already\s+{OWNER}\b", re.M)

#: Sections whose commands run on the LOCAL machine, not the deployment: the
#: prerequisites, the fresh-clone setup, and the sync that pulls the audit trail
#: down over `gcloud`. An allowlist rather than a blocklist, so a section added
#: later counts as the deployment's and has to say otherwise -- the noisy
#: direction, which is what `CLAUDE.md` asks for when a declaration is absent.
_LOCAL_SECTIONS = frozenset({"1", "2", "7b"})

#: A line that *opens* the owner's shell for the lines that follow: the correct
#: prefix with nothing after the quote, i.e. the multi-line form.
_OPENS_SHELL = re.compile(
    rf"sudo\s+(?:-n\s+)?-u\s+{OWNER}\s+-H\s+bash\s+-lc\s+['\"]\s*$"
)


def split_commands(line: str) -> list[str]:
    """Split a line into the commands a shell would run, ignoring separators
    inside quotes.

    **A line is not a command, and treating it as one exempts the wrong half.**
    `./scripts/x.sh; sudo -u OWNER -H bash -lc '...'` has the correct form
    somewhere on the line, so any per-line rule clears the unprotected command
    in front of it. Separators inside a quoted `-lc` string are part of that
    command and must not split it, which is why this tracks quote state rather
    than calling `re.split`.
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


def strip_comment(line: str) -> str:
    """Drop a shell comment, respecting quotes and word boundaries.

    **Splitting on the first `#` fails OPEN**, which is why this exists.
    `grep '#' log; ./scripts/vps-deploy.sh --check` becomes `grep '` that way,
    so the unwrapped command after it is never examined and the check passes on
    a line that would run as the wrong user. Found on review.

    Two rules, both from the shell: a `#` inside quotes is data, and a `#` that
    is not at the start of a word is part of that word (`echo a#b` has no
    comment).
    """
    quote: str | None = None
    for i, ch in enumerate(line):
        if quote is not None:
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            continue
        if ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _path_escapes_the_quoted_command(line: str) -> bool:
    """True when a path sits OUTSIDE `-lc`'s command string.

    `sudo -u OWNER -H bash -lc 'cat' ~/trading-engine/x` carries the correct
    prefix and still fails the contract: `bash -lc` takes exactly one argument
    as the command string, so `~/trading-engine/x` becomes `$0` -- and the
    caller's shell expanded that tilde before `sudo` ran.

    A line whose quote never closes is the multi-line form, where the shell
    stays open for the lines below and a path there is inside it, so an
    unclosed quote returns False rather than guessing.
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


#: Runs as `OWNER` at all, in any form.
_RUNS_AS_OWNER = re.compile(rf"sudo\s+(?:-n\s+)?-u\s+{OWNER}\b")

#: Invoking something by a path relative to the checkout, or its virtualenv.
_CHECKOUT_RELATIVE = re.compile(r"(?:^|[\s;&|(])(?:\./)?scripts/\S+\.sh|\.venv/bin/")


def _in_owners_shell(command: str) -> bool:
    """Whether `command` reaches the deployment as `OWNER`, correctly.

    **Two conditions, and conflating them was too strict.** Running as the owner
    is `sudo -u OWNER`; `-H bash -lc` is what additionally makes `~` and `$HOME`
    resolve as that user. A command that names no path needs only the first —
    `sudo -u OWNER -H crontab -e` is right, and demanding `bash -lc` around it
    rejected a correct command, which is the failure direction that gets a guard
    switched off.

    So the login shell is required exactly when the command depends on
    expansion: a `~` path, or an invocation relative to the checkout.
    """
    if not _RUNS_AS_OWNER.search(command):
        return False
    if _path_escapes_the_quoted_command(command):
        return False
    depends_on_expansion = "~/" in command or _CHECKOUT_RELATIVE.search(command)
    if depends_on_expansion and not _CORRECT.search(command):
        return False
    return True


def offenders_in_block(section: str, block: str) -> list[str]:
    """Every command in `block` that the deployment would run as the wrong user.

    **One general rule, replacing four narrow ones**, and the reason is that
    each narrow rule was written after something slipped past the others:

    | rule | what slipped past the ones before it |
    |---|---|
    | `sudo` form | — (the original) |
    | `~/trading-engine` path | a `sudo` that was correct but pointed at a path the caller expanded |
    | `tmux` subcommand | a bare `capture-pane`, which names no user and no path |
    | checkout-relative | `./scripts/vps-deploy.sh` and `cd python && .venv/bin/...` |
    | *and then* | `crontab -e`, which is none of the four |

    Five holes in four rules is a pattern about the approach rather than a run
    of bad luck: any enumeration of *shapes that need the owner* is a blocklist,
    and the real invariant is the complement -- **on the deployment, every
    command runs as `OWNER`.** So that is what this asserts, and the exemptions
    are the enumerated part instead: the local sections, and a block already
    inside the owner's session.

    `crontab -e` is the case that makes this concrete. A crontab belongs to an
    account, so running it as the login user schedules the job for `minju`, who
    has no checkout -- and it carries no `sudo`, no `~`, no `tmux` and no
    relative path.
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
        if inside:
            continue
        bare = strip_comment(line).strip()
        if not bare:
            continue
        for command in split_commands(bare):
            if not _in_owners_shell(command):
                offenders.append(command)
    return offenders


#: The scan must be reached through the launcher, which takes the `flock`. The
#: CLI takes no lock, so a hand-run pass and an hourly cron tick would both
#: write `scan_progress` -- keyed on `code`, so each records outcomes for codes
#: the other is still fetching. Exempting a block as "already the owner" says
#: nothing about the lock, which is why this is its own rule.
_UNLOCKED_SCAN = re.compile(r"(?:python3?|\bpython\b)\s+-m\s+data\.krx_scan\b")


def unlocked_scan_offenders(
    blocks: list[tuple[str, str]] | None = None,
) -> list[str]:
    """Any fenced command invoking the scan CLI instead of the launcher.

    Takes the blocks so a test can hand it synthetic ones. The document is
    currently clean, so a version that only read the file passed whether or not
    it still consulted `_UNLOCKED_SCAN` — a mutation removing the check survived
    until this parameter existed. Same gap, third time.
    """
    if blocks is None:
        blocks = _fenced_blocks_by_section()
    return [
        f"§{section}: {command.strip()}"
        for section, block in blocks
        for command in block.splitlines()
        if _UNLOCKED_SCAN.search(strip_comment(command))
    ]


def _fenced_blocks_by_section() -> list[tuple[str, str]]:
    """Every ```bash block paired with the `## N.` section it sits in.

    Only `bash` fences: a `text` fence is a fragment or an example, and §8b
    carries one deliberately (`... 2>&1 | tee ...` is an instruction to append
    to the command above, not a command).
    """
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


def test_the_runbook_has_fenced_commands_to_check():
    """Otherwise every assertion below passes over an empty list.

    The fence parser is the part most likely to break silently — a change to
    how the document marks code would empty this and leave the real checks
    green while checking nothing.
    """
    blocks = _fenced_blocks_by_section()
    lines = [(s, l) for s, b in blocks for l in b.splitlines()]
    assert len(blocks) > 5, f"only {len(blocks)} bash blocks found; the fence parse is wrong"
    assert len(lines) > 50, f"only {len(lines)} fenced lines found; the fence parse is wrong"
    assert any(OWNER in line for _, line in lines), (
        f"no fenced command mentions {OWNER}; either the runbook stopped "
        f"addressing the deployment or the parse is wrong"
    )


def test_every_deployment_command_runs_as_the_owner():
    """The one rule. Four narrower ones preceded it and each was written after
    something slipped past the others -- see `offenders_in_block`."""
    offenders = [
        f"§{section}: {command}"
        for section, block in _fenced_blocks_by_section()
        for command in offenders_in_block(section, block)
    ]
    assert not offenders, (
        f"these would run as the SSH login user rather than {OWNER}, which has "
        f"no checkout and its own tmux socket and crontab:\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


@pytest.mark.parametrize(
    "section,block,expected",
    [
        # The five shapes, each of which slipped past the rules before it.
        ("6b", "sudo -u minjun4897 cat ~/trading-engine/x", 1),
        ("7", "sudo -u minjun4897 -H bash -lc 'cat' ~/trading-engine/x", 1),
        ("7", "tmux capture-pane -t =paper-trading -p", 1),
        ("6b", "./scripts/vps-deploy.sh --check", 1),
        ("4", "crontab -e", 1),
        # Correct without `bash -lc`: it names no path, so nothing needs
        # expanding. Requiring the login shell here rejected a right answer.
        ("8b", "sudo -u minjun4897 -H crontab -e", 0),
        ("7", "sudo -u minjun4897 -H tmux ls", 0),
        # Correct, in both the single-command and multi-line forms.
        ("6b", "sudo -u minjun4897 -H bash -lc '~/trading-engine/scripts/x.sh'", 0),
        ("7", "sudo -u minjun4897 -H bash -lc '\ncd ~/trading-engine\nscripts/x.sh\ntmux ls\n'", 0),
        # One correct command must not vouch for a bare sibling, on a line or
        # across lines.
        ("7", "cat ~/trading-engine/x\nsudo -u minjun4897 -H bash -lc 'true'", 1),
        ("7", "cat ~/trading-engine/x; sudo -u minjun4897 -H bash -lc 'true'", 1),
        # A separator inside the quoted command string does not split it.
        ("7", "sudo -u minjun4897 -H bash -lc 'cd ~/x && ls; pwd'", 0),
        # Exemptions: a local section, and a block already in the owner's shell.
        ("2", "cd python && uv sync && cd ..", 0),
        ("7b", "PYTHONPATH=python python/.venv/bin/python -m live.sync_live_signals", 0),
        ("8b", "# already minjun4897\ncd ~/trading-engine/python\npython3 -m data.krx_scan", 0),
        # Comments are not commands.
        ("4", "# see scripts/paper-trading-daily-signal.sh for why", 0),
    ],
)
def test_what_counts_as_an_offender(section, block, expected):
    """The predicate on synthetic input, so deleting its use in the real check
    fails here even while the document itself is clean.

    A negative case alone would only prove the predicate rejects the bad form,
    never that anything still consults it -- the gap CodeRabbit named, confirmed
    by a mutation that survived until this existed.
    """
    assert len(offenders_in_block(section, block)) == expected


def test_the_scan_is_always_reached_through_the_launcher():
    """The CLI takes no `flock`; the launcher does.

    Being inside the owner's own `tmux` session — which `# already minjun4897`
    exempts a block for — says nothing about the lock, so a typed pass calling
    the CLI directly would contend with an hourly cron tick over one
    `scan_progress` keyed on `code`. Verified as its own rule for that reason: a
    mutation swapping the launcher for the CLI survived every other check here.
    """
    offenders = unlocked_scan_offenders()
    assert not offenders, (
        "these invoke the scan CLI directly, taking no lock:\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


@pytest.mark.parametrize(
    "block,expected",
    [
        ("python3 -m data.krx_scan --scan", 1),
        ("  python -m data.krx_scan --second-pass", 1),
        ("~/trading-engine/scripts/collect-krx-pre2019.sh", 0),
        # A mention in a comment is not an invocation.
        ("# data.krx_scan keys scan_progress on code", 0),
    ],
)
def test_what_counts_as_an_unlocked_scan(block, expected):
    assert len(unlocked_scan_offenders([("8b", block)])) == expected


@pytest.mark.parametrize(
    "line,stripped",
    [
        ("./scripts/x.sh  # why", "./scripts/x.sh  "),
        # A `#` inside quotes is data, not a comment. The first two are also
        # rejected by the word-boundary rule (the char before `#` is a quote),
        # so they do NOT exercise the quote tracking -- the third does, and it
        # is the one a mutation removing that tracking fails.
        ("grep '#' log; ./scripts/x.sh", "grep '#' log; ./scripts/x.sh"),
        ('grep "#" log; ./scripts/x.sh', 'grep "#" log; ./scripts/x.sh'),
        ("grep 'a #b' log; ./scripts/x.sh", "grep 'a #b' log; ./scripts/x.sh"),
        ('sed "s/ #x/y/" f; tmux ls', 'sed "s/ #x/y/" f; tmux ls'),
        # Mid-word `#` is part of the word, as in the shell.
        ("echo a#b", "echo a#b"),
        ("# whole line", ""),
    ],
)
def test_how_a_comment_is_stripped(line, stripped):
    assert strip_comment(line) == stripped


@pytest.mark.parametrize(
    "block,must_be_named",
    [
        ("grep 'a #b' log; ./scripts/vps-deploy.sh --check", "vps-deploy.sh"),
        ("grep 'a #b' log; tmux ls", "tmux ls"),
    ],
)
def test_a_quoted_hash_does_not_hide_the_rest_of_the_line(block, must_be_named):
    """The guard failed open here, which is the direction that matters: a
    deployment command running as the login user, and a green test.

    **Asserted on which command is named, not on the count.** Under the naive
    split the surviving fragment (`grep '`) is itself an offender, so a
    non-empty list stayed green while the command after the quoted `#` went
    unexamined -- the mutation survived until this named it.
    """
    found = offenders_in_block("7", block)
    assert any(must_be_named in o for o in found), (
        f"{must_be_named!r} was not examined; got {found}"
    )


def test_a_quoted_hash_does_not_hide_an_unlocked_scan():
    found = unlocked_scan_offenders([("8b", "echo 'a #b'; python3 -m data.krx_scan")])
    assert any("data.krx_scan" in o for o in found), found


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
