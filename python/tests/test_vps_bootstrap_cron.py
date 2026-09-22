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
import tempfile
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


# ------------------------------------------- the install block, really run


def _run_install(seed: str, install_btc: int) -> str:
    """Execute the script's real crontab-install block against a seeded
    crontab, with `crontab` itself stubbed.

    Appending alone protects a fresh box and leaves every existing one
    exactly as dangerous, so what has to be tested is the **migration**:
    a crontab that already carries the watchdog. Building the expected
    output by hand would test my reading of the block, not the block.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    arrays = src[src.index("CRON_ENV=("):src.index("if ((INSTALL_CRON)); then")]
    block = src[src.index("if ((INSTALL_CRON)); then"):src.index('say "ready"')]
    with tempfile.TemporaryDirectory() as tmp:
        seed_f, out_f = Path(tmp) / "seed", Path(tmp) / "out"
        seed_f.write_text(seed, encoding="utf-8")
        prog = (
            "REPO_ROOT=/R\nINSTALL_CRON=1\n"
            f"INSTALL_BTC_CRON={install_btc}\nMOCK_SIGNALS=0\n"
            "ok(){ :; }; warn(){ :; }; systemctl(){ return 1; }\n"
            'crontab() { if [ "$1" = "-l" ]; then cat ' f'"{seed_f}"'
            '; else cat > ' f'"{out_f}"' '; fi; }\n'
            f"{arrays}\n{block}\n"
        )
        subprocess.run(["bash", "-c", prog], check=True, capture_output=True)
        return out_f.read_text(encoding="utf-8") if out_f.exists() else seed


def test_a_plain_install_REMOVES_an_existing_watchdog_line():
    """**The migration hazard.** A box provisioned before 2026-09-22 —
    or by `--install-btc-cron` — already carries the watchdog. Being told
    to install the current scope has to mean the BTC scope is not
    installed, not merely that it is not added again."""
    seed = "*/5 * * * * /R/scripts/paper-trading-watchdog.sh"
    out = _run_install(seed, install_btc=0)
    assert RESTARTS_A_LOOP not in out, (
        f"the watchdog survived a plain --install-cron:\n{out}"
    )
    assert "collect-krx-quotes.sh" in out, "the current scope was not installed"


def test_removal_matches_a_HAND_EDITED_schedule_too():
    """Matched on the filename, not the whole line: `*/10` instead of
    `*/5` is exactly as capable of restarting the loop."""
    seed = "*/10 * * * * /R/scripts/paper-trading-watchdog.sh"
    assert RESTARTS_A_LOOP not in _run_install(seed, install_btc=0)


def test_the_BTC_flag_keeps_an_existing_watchdog_line():
    """Removal must be the consequence of *not* asking for BTC, never an
    unconditional purge — otherwise resuming the arc fights the script."""
    seed = "*/5 * * * * /R/scripts/paper-trading-watchdog.sh"
    assert RESTARTS_A_LOOP in _run_install(seed, install_btc=1)


def test_an_unrelated_cron_line_is_never_touched():
    """The operator's own jobs are not this script's to manage."""
    seed = "0 3 * * * /home/me/backup.sh"
    assert "/home/me/backup.sh" in _run_install(seed, install_btc=0)


def test_the_btc_flag_implies_install_rather_than_silently_doing_nothing():
    """It set `INSTALL_BTC_CRON` without `INSTALL_CRON`, so passing it
    alone built the array and then printed "cron not touched" — a flag
    whose whole purpose is to schedule something, scheduling nothing."""
    src = SCRIPT.read_text(encoding="utf-8")
    parser = src[src.index("while [[ $# -gt 0 ]]; do"):src.index('case "$MODE" in')]
    done = subprocess.run(
        ["bash", "-c",
         "INSTALL_CRON=0; INSTALL_BTC_CRON=0; MOCK_SIGNALS=0; MODE=simulated\n"
         "usage(){ :; }\n"
         f"set -- --install-btc-cron\n{parser}\n"
         'echo "cron=$INSTALL_CRON btc=$INSTALL_BTC_CRON"'],
        capture_output=True, text=True, check=True,
    )
    assert done.stdout.strip() == "cron=1 btc=1", done.stdout
