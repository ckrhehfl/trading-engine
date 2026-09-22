"""`--install-cron` must not be able to restart a stopped trading loop.

**The defect this exists for, found 2026-09-22 on CodeRabbit review.**
`scripts/vps-bootstrap.sh`'s `CRON_LINES` still held the four BTC-era
jobs and none of the three KRX collectors. So `--install-cron`, run on
today's instance, would have:

- re-added `paper-trading-watchdog.sh` at `*/5`, whose stated job is to
  restart whichever of the two BTC loops is missing -- both are missing
  **deliberately** (operator decision, 2026-09-17), so it would have
  resumed order submission to the VST demo venue inside five minutes
  without a human choosing that;
- re-added `collect-positioning.sh`, a Binance collector that answers
  HTTP 451 from this instance and fails every series by design;

and would still not have scheduled a single thing that is supposed to be
running. The doc mismatch was the symptom; a provisioning script that
silently restarts a stopped trading loop was the defect.

**These tests execute the real arrays out of the real file** rather than
restating them. A test that listed the expected lines would pass against
a copy of my own understanding, which is the failure mode this repo has
already paid for three times (`test_conftest_isolation.py`).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "vps-bootstrap.sh"

#: The job that restarts a stopped loop. Named as a constant because it is
#: the whole point of the split, not one example among several.
RESTARTS_A_LOOP = "paper-trading-watchdog.sh"


def _cron_lines(install_btc: int) -> list[str]:
    """Run the script's own array construction, at the given flag value.

    Extracted between two stable markers and executed by bash, so what is
    under test is the file's real text. If the markers ever move the
    extraction raises rather than silently testing nothing.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    start = src.find("CRON_LINES=(")
    end = src.find("if ((INSTALL_CRON)); then")
    assert 0 <= start < end, (
        "the CRON_LINES block markers moved; this test is not reading the "
        "real arrays any more and must be repaired rather than deleted"
    )
    block = src[start:end]
    done = subprocess.run(
        ["bash", "-c",
         f'REPO_ROOT=/R; INSTALL_BTC_CRON={install_btc}\n{block}\n'
         'printf "%s\\n" "${CRON_LINES[@]}"'],
        capture_output=True, text=True, check=True,
    )
    return [ln for ln in done.stdout.splitlines() if ln and not ln.startswith("#")]


def test_the_default_install_cannot_restart_a_loop():
    """The one that matters. `--install-cron` is provisioning; resuming a
    deliberately stopped loop must never be a side effect of it."""
    lines = _cron_lines(install_btc=0)
    assert not any(RESTARTS_A_LOOP in ln for ln in lines), (
        f"{RESTARTS_A_LOOP} restarts whichever BTC loop is missing, and both "
        f"are missing by operator decision. It may only be scheduled behind "
        f"--install-btc-cron.\ngot: {lines}"
    )


def test_the_default_install_schedules_what_is_actually_running():
    """The other half of the same defect: the set was not merely wrong, it
    was missing every job this instance exists to run."""
    lines = _cron_lines(install_btc=0)
    joined = "\n".join(lines)
    for script in (
        "collect-krx-quotes.sh",
        "collect-krx-flow.sh",
        "collect-krx-intraday.sh",
        "paper-trading-health-check.sh",
    ):
        assert script in joined, f"{script} is not scheduled by --install-cron"


def test_the_BTC_jobs_are_kept_and_reachable_behind_their_own_flag():
    """**Kept, not deleted** -- BTC is set aside, not abandoned, and the
    same treatment CLAUDE.md gives the struck-through local row."""
    lines = _cron_lines(install_btc=1)
    joined = "\n".join(lines)
    for script in (
        "paper-trading-daily-signal.sh",
        RESTARTS_A_LOOP,
        "collect-positioning.sh",
        "generate-mock-signal.sh",
    ):
        assert script in joined, f"{script} was lost rather than gated"


def test_the_flag_is_purely_additive():
    """Turning BTC back on must not change what the current scope
    schedules -- otherwise resuming one arc silently edits the other."""
    assert _cron_lines(0) == _cron_lines(1)[: len(_cron_lines(0))]


def test_the_KRX_hours_are_UTC_and_say_so_in_the_crontab():
    """Getting these wrong FAILS SILENTLY: the sampler gates on KST
    internally, so a wrong-hour entry logs "outside the continuous
    session" forever and collects nothing. The comment has to reach the
    crontab, because that is where the next person edits it."""
    src = SCRIPT.read_text(encoding="utf-8")
    start = src.find("CRON_LINES=(")
    end = src.find("if ((INSTALL_CRON)); then")
    block = src[start:end]
    assert "# KRX trades 00:00-06:20 UTC" in block, (
        "the UTC warning is not among the installed lines, so it never "
        "reaches the crontab a future operator reads"
    )
    quotes = [ln for ln in _cron_lines(0) if "collect-krx-quotes.sh" in ln]
    assert quotes and quotes[0].startswith("*/30 0-6 "), (
        f"the quote sampler must run over KRX's UTC hours; got {quotes}"
    )


@pytest.mark.parametrize("flag", ["--install-cron", "--install-btc-cron"])
def test_both_flags_are_documented_in_usage(flag):
    """An undocumented flag that resumes trading is worse than no flag."""
    src = SCRIPT.read_text(encoding="utf-8")
    usage = src[src.find("usage() {"):src.find("USAGE\n}")]
    assert flag in usage, f"{flag} is not in the usage text"
