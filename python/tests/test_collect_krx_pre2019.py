"""`collect-krx-pre2019.sh` must not start a second writer, run during the KRX
session, or proceed without credentials.

**Why this script needs tests rather than a careful reading.** It is invoked
hourly by cron against a nine-day pass, so "two passes overlap" is the normal
case rather than an unlucky one — and both write `scan_progress`, which keys on
`code`, so the second would record outcomes for codes the first is still
fetching. A comment saying "flock prevents this" is exactly the kind of guard
this project has shipped inert three times.

**Everything here runs the real script with `python` stubbed**, so each
assertion is about what the file does rather than what it says. The stub is also
why nothing reaches KIS.

One property is deliberately not tested here: the scan's own refusal to start
during the session, and its pausing of a pass that reaches one, belong to
`data.krx_scan` and are tested there. This script only declines to start a *new*
pass, which is what the session test below checks.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "collect-krx-pre2019.sh"

pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="script not present")


def _repo(tmp_path: Path, *, env_file: bool = True) -> Path:
    """A throwaway checkout holding the real script and a stub `python`.

    The stub answers the session probe from `SESSION_RC` and records every `-m`
    module it is asked for, so a test can tell "the scan ran" from "the scan was
    skipped" without inferring either from an exit code.
    """
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "python" / ".venv" / "bin").mkdir(parents=True)
    (repo / "var").mkdir(parents=True)
    (repo / "scripts" / SCRIPT.name).write_text(
        SCRIPT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    if env_file:
        # CRLF on purpose: this repo's real .env carries it, and stripping it is
        # the fix for an incident rather than a nicety.
        (repo / ".env").write_text(
            "KIS_APP_KEY=fake-key\r\nKIS_APP_SECRET=fake-secret\r\n", encoding="utf-8"
        )

    fake = repo / "python" / ".venv" / "bin" / "python"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "# `-c` is the session probe; `-m` is the scan. Both are recorded.\n"
        'for arg in "$@"; do\n'
        '  case "$prev" in\n'
        '    -m) echo "module:$arg" >> "$CALL_LOG";;\n'
        '    -c) echo "probe" >> "$CALL_LOG"; exit "$SESSION_RC";;\n'
        "  esac\n"
        '  prev="$arg"\n'
        "done\n"
        'exit "${SCAN_RC:-0}"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return repo


def _run(
    repo: Path, *, in_session: bool = False, scan_rc: int = 0, hold_lock: bool = False
) -> tuple[int, list[str], str]:
    calls = repo / "calls.txt"
    env = {
        **os.environ,
        "CALL_LOG": str(calls),
        "SESSION_RC": "0" if in_session else "1",
        "SCAN_RC": str(scan_rc),
        # Blanked so the script's own .env fallback is the thing under test.
        "KIS_APP_KEY": "",
        "KIS_APP_SECRET": "",
    }
    script = repo / "scripts" / SCRIPT.name
    holder = None
    if hold_lock:
        # A real competing holder, not a simulated one: another process takes
        # the same lock file the script will reach for.
        lock = repo / "var" / "krx-pre2019.lock"
        lock.touch()
        holder = subprocess.Popen(["flock", "-x", str(lock), "sleep", "30"])
        time.sleep(0.5)
    try:
        proc = subprocess.run(
            ["bash", str(script)], env=env, capture_output=True, text=True, timeout=60
        )
    finally:
        if holder is not None:
            holder.kill()
            holder.wait()
    recorded = calls.read_text().split() if calls.exists() else []
    log_file = repo / "var" / "krx-pre2019.log"
    return proc.returncode, recorded, (log_file.read_text() if log_file.exists() else "")


def test_the_happy_path_runs_the_scan(tmp_path):
    """The baseline, so every skip below is a real skip and not a broken stub."""
    code, calls, log = _run(_repo(tmp_path))
    assert code == 0, log
    assert "module:data.krx_scan" in calls, calls
    assert "starting a pass" in log


def test_a_pass_already_holding_the_lock_is_a_no_op_not_a_failure(tmp_path):
    """Hourly cron makes an overlap normal, so this must not mail the operator —
    and must not run, because two writers against one `scan_progress` keyed on
    `code` is the corruption the lock exists for."""
    code, calls, log = _run(_repo(tmp_path), hold_lock=True)
    assert code == 0, f"an already-running pass is not an error: {log}"
    assert "module:data.krx_scan" not in calls, (
        f"a second pass started while the lock was held: {calls}"
    )
    assert "already holds the lock" in log


def test_the_session_is_asked_about_directly_and_stops_a_new_pass(tmp_path):
    """The KRX collectors share this app key, so a burst during the session is
    contention on series that cannot be backfilled.

    Asked with its own call rather than read off the scan's exit status:
    `krx_scan` exits 2 on a session refusal and argparse exits 2 on a bad
    argument, so inferring would make a typo in the flags read as a clean skip
    for ever.
    """
    code, calls, log = _run(_repo(tmp_path), in_session=True)
    assert code == 0, log
    assert "probe" in calls, "the session was never asked about"
    assert "module:data.krx_scan" not in calls, f"a pass started in session: {calls}"
    assert "continuous session" in log


def test_missing_credentials_refuse_to_run_and_never_log_a_value(tmp_path):
    """A silent exit on a rotated key is the failure the other collectors
    already recorded, so the refusal has to be explicit and logged."""
    code, calls, log = _run(_repo(tmp_path, env_file=False))
    assert code == 1, f"expected a refusal, got {code}: {log}"
    assert "module:data.krx_scan" not in calls
    assert "refusing to run" in log
    assert "fake" not in log, "a credential value reached the log"


def test_a_failing_scan_propagates_rather_than_reading_as_done(tmp_path):
    """The next cron tick resumes, so a failure must be visible rather than
    swallowed into a clean exit."""
    code, _calls, log = _run(_repo(tmp_path), scan_rc=7)
    assert code == 7, log
    assert "exited 7" in log


def test_the_credentials_reach_the_scan_with_the_CRLF_stripped(tmp_path):
    """A trailing `\\r` on a key once reached a JDK exception message with the
    real value inside it. Asserted on the value the scan actually receives, not
    on the presence of a `tr` in the source."""
    repo = _repo(tmp_path)
    fake = repo / "python" / ".venv" / "bin" / "python"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'for arg in "$@"; do\n'
        '  case "$prev" in -c) exit 1;; esac\n'
        '  prev="$arg"\n'
        "done\n"
        'printf "[%s][%s]" "$KIS_APP_KEY" "$KIS_APP_SECRET" > "$CALL_LOG"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    _run(repo)
    seen = (repo / "calls.txt").read_text()
    assert seen == "[fake-key][fake-secret]", repr(seen)


def _command_lines() -> str:
    """The script with comments stripped, for any assertion about what it RUNS.

    **A comment must never vouch for a command.** `set -euo pipefail` appears
    twice in this script — once as the line that takes effect and once inside a
    comment explaining why a `|| true` is load-bearing — so a text search found
    it even after the real line had been mutated away, and the guard passed on
    prose. Same shape as the sibling-vouches-for-sibling defect the runbook
    guard hit three times.
    """
    return "\n".join(
        line.split("#", 1)[0] for line in SCRIPT.read_text(encoding="utf-8").splitlines()
    )


def test_the_panel_is_hardcoded_and_no_page_width_is_passed():
    """Two properties of the invocation, both load-bearing.

    `scan_progress` keys on `code`, so a second panel in this database would
    skip every finished code; `claim_panel` refuses it, and an argument here
    would only make that refusal reachable by typo. And the page width is a
    property of the panel's era — 90 days before 2000, because a 120-day page
    lands on the 100-row cap there and `failed:capped` is not retryable — so
    `default_page_days` has to be the one deciding it.
    """
    text = _command_lines()
    assert "PANEL_START=19910828" in text
    assert "PANEL_END=20181231" in text
    assert '--panel-start "$PANEL_START"' in text
    invocation = text.split("set +e", 1)[1]
    assert "--page-days" not in invocation, (
        "the script passes a page width, overriding the era default"
    )


def test_errexit_is_lifted_in_exactly_one_place():
    """`set -euo pipefail`, checked because a Gate A verification script once
    shipped without `set -e` and its evidence could not fail.

    The single `set +e` is around the scan, whose status is then read
    explicitly; anywhere else would be a hole rather than a deliberate window.
    """
    text = _command_lines()
    assert re.search(r"^set -euo pipefail\s*$", text, re.M), (
        "errexit is not set on any command line -- a mention in a comment is "
        "not the same as the line taking effect"
    )
    assert text.count("set +e") == 1
    assert "rc=$?" in text and 'if [ "$rc" -ne 0 ]' in text


def test_the_runtime_artifacts_are_all_gitignored():
    """A file this script leaves in the checkout blocks the next deploy.

    `scripts/vps-deploy.sh` refuses to run on ANY `git status --porcelain`
    output, untracked files included, and `live.health_check` raises
    `uncommitted_changes` on the same signal. That has now bitten three times
    in one shape: a lock in `python/var/` (2026-09-22), this backfill's log
    (2026-09-28), and this script's own `flock` file, caught on review
    (2026-09-30).

    **The lock file is deliberately not deleted** — `flock` releases on fd
    close, and unlinking a file another process holds open is how the lock
    stops meaning anything. So the artifact stays and the ignore rule is what
    has to cover it, which is why this asserts the rule rather than a cleanup.

    Checked through `git check-ignore` rather than by reading `.gitignore`,
    because the anchoring rule is easy to state wrongly -- this file's own first
    draft did. A pattern is anchored at the repository root only when it holds a
    slash somewhere other than the end, so `var/` alone matches at every level
    (measured: `python/var/x.lock` is ignored by it) while `var/live/` is
    anchored and never matched `python/var/live/`. That second form is what the
    2026-09-22 incident was.
    """
    repo = Path(__file__).resolve().parents[2]
    artifacts = [
        "var/krx-pre2019.lock",
        "var/krx-pre2019.log",
        # The `cd python` form, which is where the anchored rule failed.
        "python/var/krx-pre2019.lock",
        "python/var/live/latest.json",
    ]
    not_ignored = [
        a
        for a in artifacts
        if subprocess.run(
            ["git", "check-ignore", "-q", a], cwd=repo, capture_output=True
        ).returncode
        != 0
    ]
    assert not not_ignored, (
        f"these would be left in the checkout and block the next deploy: {not_ignored}"
    )


def test_the_script_leaves_its_lock_file_rather_than_racing_to_remove_it():
    """Stated as a test because "why not just delete it" is the obvious
    question and the answer is not obvious.

    Deleting the lock would let a second invocation create a fresh file and
    take a lock on it while the first still holds the old inode — two writers
    against one `scan_progress`, which is what the lock exists to prevent.
    """
    text = _command_lines()
    assert 'exec 9>"$LOCK_FILE"' in text
    assert "rm" not in text.replace("$REPO_ROOT", ""), (
        "the script removes something; a lock file removal reopens the race"
    )
