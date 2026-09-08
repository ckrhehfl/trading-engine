"""Every shell script in `scripts/` must be executable **in git**.

WSL mounts `/mnt/c` with `core.fileMode=false`, so a local `chmod +x`
is invisible to git and a script commits as `100644` while looking
executable on the developer's own machine. Cron then runs it and gets
"Permission denied" — silently, because a cron job that cannot start
produces no log.

This has now happened three times in this repository:

- 2026-09-06, PR #138: every deployment script committed non-executable
- 2026-09-07: `paper-trading-health-check.sh`, caught by hand
- 2026-09-08: `vps-deploy.sh`, caught only because a `chmod` on the
  server made the file show as modified

The pattern is the point. Two of the three were caught by luck, and the
remedy for "remember to check the mode" is not remembering harder — it
is a check that runs whether or not anyone remembers.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

EXECUTABLE = "100755"


def _git_modes() -> dict[str, str]:
    done = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-s", "scripts/"],
        capture_output=True, text=True, check=True,
    )
    modes: dict[str, str] = {}
    for line in done.stdout.splitlines():
        mode = line.split(" ", 1)[0]
        path = line.split("\t", 1)[1]
        modes[path] = mode
    return modes


def non_executable_shell_scripts(modes: dict[str, str]) -> list[str]:
    """The detection rule itself, separated from where it reads its input.

    Split out so it can be exercised against a known-bad fixture. The
    first version of this file asserted only that the *real* index was
    clean, which passes for two different reasons — the repository is
    fine, or the rule stopped detecting anything — and could not tell
    them apart. That is the same inert-guard shape this repository has
    now hit five times, and it appeared here in the test written about
    it. Caught by CodeRabbit on PR #150, not by the author.
    """
    return sorted(
        path for path, mode in modes.items()
        if path.endswith(".sh") and mode != EXECUTABLE
    )


def test_the_rule_detects_a_non_executable_script():
    """Fed a known-bad fixture, so weakening the rule turns this red.

    This runs first because it is what gives the next test meaning.
    """
    assert non_executable_shell_scripts({"scripts/broken.sh": "100644"}) == [
        "scripts/broken.sh"
    ]
    assert non_executable_shell_scripts({"scripts/fine.sh": EXECUTABLE}) == []
    # A non-script is not the target: `.py` helpers under scripts/ are
    # imported, not exec'd, and flagging them would be the false positive
    # that gets a check switched off.
    assert non_executable_shell_scripts({"scripts/notes.md": "100644"}) == []
    # Mixed input: the rule must report the bad one and only the bad one.
    assert non_executable_shell_scripts({
        "scripts/a.sh": EXECUTABLE,
        "scripts/b.sh": "100644",
        "scripts/c.md": "100644",
    }) == ["scripts/b.sh"]


def test_every_script_is_executable_in_git():
    """The mode recorded in git, not the mode on this filesystem.

    Checking the working tree would pass on WSL for exactly the files
    that are broken in the repository, which is the whole defect.
    """
    modes = _git_modes()
    assert modes, "no files tracked under scripts/ -- has the directory moved?"
    assert any(p.endswith(".sh") for p in modes), (
        f"no .sh files tracked under scripts/, so this test would pass "
        f"vacuously. Tracked: {sorted(modes)}"
    )

    non_executable = non_executable_shell_scripts(modes)
    assert not non_executable, (
        "these scripts are committed non-executable, so cron will fail to run "
        "them with no log line to say why:\n"
        + "\n".join(f"  {p}   fix: git update-index --chmod=+x {p}" for p in non_executable)
    )


def test_git_reports_a_mode_this_check_understands():
    """A mode outside this set means `ls-files -s` changed shape and the
    comparison above may be silently matching nothing."""
    unexpected = {
        path: mode for path, mode in _git_modes().items()
        if mode not in ("100644", EXECUTABLE, "120000")
    }
    assert not unexpected, f"unrecognised git modes: {unexpected}"
