"""`collect-krx-flow.sh` must not let a re-fetchable step kill a
non-backfillable one.

The script runs under `set -euo pipefail`, where **any** failing command
aborts the rest of the file. It collects two very different things:

- **투자자별 매매동향** — a 30-row rolling horizon with no date parameter.
  A session not collected is gone permanently.
- **the delisted universe** — the whole history, republished by KRX every
  day. A day missed costs a dated row and nothing else.

So the delisted snapshot runs **last** and is allowed to fail. Put it
anywhere earlier, or let it abort, and a KRX portal outage silently costs
that day's flow data.

**This executes the script with the real commands stubbed**, rather than
asserting on its text. A comment saying "runs last" is exactly the kind of
guard this project has shipped inert three times.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "collect-krx-flow.sh"


def _run(tmp_path, *, delisted_fails: bool) -> tuple[int, list[str]]:
    """Run the real script with `python` and the outside world stubbed.

    A fake `python/.venv/bin/python` records each `-m` module it is asked
    for, in order, and fails for `data.krx_delisted` when asked to.
    """
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "python" / ".venv" / "bin").mkdir(parents=True)
    (repo / "var" / "live").mkdir(parents=True)
    (repo / "scripts" / "collect-krx-flow.sh").write_text(
        SCRIPT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (repo / ".env").write_text(
        "KIS_APP_KEY=fake-key\r\nKIS_APP_SECRET=fake-secret\r\n", encoding="utf-8"
    )

    calls = repo / "calls.txt"
    fake = repo / "python" / ".venv" / "bin" / "python"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "# records the -m module it was asked for, in order\n"
        'for arg in "$@"; do\n'
        '  case "$prev" in -m) echo "$arg" >> "$CALL_LOG";; esac\n'
        '  prev="$arg"\n'
        "done\n"
        'if [ "$FAIL_DELISTED" = "1" ]; then\n'
        '  case " $* " in *" data.krx_delisted "*) exit 3;; esac\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    env = {
        **os.environ,
        "CALL_LOG": str(calls),
        "FAIL_DELISTED": "1" if delisted_fails else "0",
        # the script's own .env fallback supplies the credentials; nothing
        # real is ever contacted because `python` itself is the stub
        "KIS_APP_KEY": "",
        "KIS_APP_SECRET": "",
    }
    proc = subprocess.run(
        ["bash", str(repo / "scripts" / "collect-krx-flow.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    recorded = calls.read_text().split() if calls.exists() else []
    return proc.returncode, recorded


@pytest.mark.skipif(not SCRIPT.exists(), reason="collector script not present")
def test_the_non_backfillable_collection_runs_before_the_delisted_snapshot(tmp_path):
    """Ordering is the guard, and `set -e` is why it matters."""
    code, calls = _run(tmp_path, delisted_fails=False)
    assert code == 0, "the happy path must exit clean"
    assert "data.kis_investor_flow" in calls, "the flow collector never ran"
    assert "data.krx_delisted" in calls, "the delisted snapshot never ran"
    assert calls.index("data.kis_investor_flow") < calls.index("data.krx_delisted"), (
        f"the delisted snapshot must run AFTER the non-backfillable flow "
        f"collection; order was {calls}"
    )


@pytest.mark.skipif(not SCRIPT.exists(), reason="collector script not present")
def test_a_failing_delisted_snapshot_does_not_fail_the_run(tmp_path):
    """**The property, executed rather than read.** KRX's portal being
    down must cost a dated row, never that day's 투자자별 매매동향."""
    code, calls = _run(tmp_path, delisted_fails=True)
    assert "data.kis_investor_flow" in calls, "the flow collector never ran"
    assert code == 0, (
        f"a failed delisted snapshot aborted the collector (exit {code}); "
        f"under `set -e` that is how a re-fetchable step costs a "
        f"non-backfillable one"
    )


@pytest.mark.skipif(not SCRIPT.exists(), reason="collector script not present")
def test_missing_credentials_still_refuse_to_run(tmp_path):
    """The existing fail-closed behaviour is not weakened by any of this:
    no key means no run, and the values are never logged."""
    repo = tmp_path / "repo"
    code, calls = _run(tmp_path, delisted_fails=False)
    assert code == 0  # sanity: the fixture above supplies credentials
    (repo / ".env").unlink()
    proc = subprocess.run(
        ["bash", str(repo / "scripts" / "collect-krx-flow.sh")],
        env={**os.environ, "CALL_LOG": str(repo / "calls2.txt"),
             "FAIL_DELISTED": "0", "KIS_APP_KEY": "", "KIS_APP_SECRET": ""},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 1, "no credentials must refuse to run"
    log = (repo / "var" / "live" / "krx-flow.log").read_text(encoding="utf-8")
    assert "fake-key" not in log and "fake-secret" not in log
