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
SCRIPTS = REPO_ROOT / "scripts"


def _git_modes() -> dict[str, str]:
    done = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-s", "scripts/"],
        capture_output=True, text=True, check=True,
    )
    modes: dict[str, str] = {}
    for line in done.stdout.splitlines():
        mode, _rest = line.split(" ", 1)
        path = line.split("\t", 1)[1]
        modes[path] = mode
    return modes


def test_every_script_is_executable_in_git():
    """The mode recorded in git, not the mode on this filesystem.

    Checking the working tree would pass on WSL for exactly the files
    that are broken in the repository, which is the whole defect.
    """
    modes = _git_modes()
    assert modes, "no files tracked under scripts/ -- has the directory moved?"

    non_executable = sorted(
        path for path, mode in modes.items()
        if path.endswith(".sh") and mode != "100755"
    )
    assert not non_executable, (
        "these scripts are committed non-executable, so cron will fail to run "
        "them with no log line to say why:\n"
        + "\n".join(f"  {p}   fix: git update-index --chmod=+x {p}" for p in non_executable)
    )


def test_the_check_would_notice_a_non_executable_file():
    """Without this, a `ls-files` that returned nothing — a moved
    directory, a changed flag — would make the test above pass on an
    empty set."""
    modes = _git_modes()
    assert any(p.endswith(".sh") for p in modes), "no .sh files found to check"
    assert all(m in ("100644", "100755", "120000") for m in modes.values()), (
        f"unexpected git mode among {modes}"
    )
