"""Every check must fire on the real defect that motivates it.

The whole premise of `change_check` is that a checklist with a scar
attached gets used. That only holds if each check would actually have
caught its own scar — so each one is fed the real historical case here,
not a synthetic one.

The `test_..._catches_the_real_case` tests are the load-bearing ones.
A check that passes on the defect it was written for is decoration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from research.change_check import (
    BLOCKER,
    WARNING,
    check_error_direction_declared,
    check_guard_fails_when_removed,
    check_guard_is_an_allowlist,
    check_java_change_deployed,
    check_no_shared_mutable_state,
    check_readonly_path_is_pure,
    check_reported_from_actual,
    check_script_fails_closed,
    format_findings,
    require_no_blockers,
)


class TestGuardFailsWhenRemoved:
    def test_catches_the_real_case(self):
        """conftest.py's log isolation was inert three versions running,
        each passing its own new tests."""
        finding = check_guard_fails_when_removed(
            guard="conftest experiment-log isolation",
            removed_and_observed_failing=False,
        )
        assert finding is not None and finding.severity == BLOCKER
        assert "cannot fail" in finding.message

    def test_passes_once_the_removal_was_actually_observed(self):
        assert check_guard_fails_when_removed(
            guard="flock on the mock signal generator",
            removed_and_observed_failing=True,
            how_verified="disabled flock, concurrency test went red, restored, green",
        ) is None


class TestGuardIsAnAllowlist:
    def test_catches_the_real_case(self):
        """The mock signal path guard blocked the real strategy tree and
        allowed the entire rest of the filesystem."""
        finding = check_guard_is_an_allowlist(
            guard="_reject_real_strategy_path", rejects_by_default=False
        )
        assert finding is not None and finding.severity == BLOCKER

    def test_passes_for_an_allowlist(self):
        assert check_guard_is_an_allowlist(
            guard="_reject_real_strategy_path", rejects_by_default=True
        ) is None


class TestReadonlyPathIsPure:
    def test_catches_the_real_case(self):
        """`--dry-run` advanced the persisted side state, changing the
        next real signal."""
        finding = check_readonly_path_is_pure(
            operation="--dry-run", mutations=[".last-side"]
        )
        assert finding is not None and finding.severity == BLOCKER
        assert ".last-side" in finding.message

    def test_passes_when_nothing_is_written(self):
        assert check_readonly_path_is_pure(operation="--dry-run") is None


class TestScriptFailsClosed:
    def test_catches_a_script_without_set_e(self, tmp_path):
        """The shape verify-gate-a-kill-switch.sh originally shipped in."""
        script = tmp_path / "verify.sh"
        script.write_text(
            "#!/usr/bin/env bash\n"
            "set -uo pipefail\n"
            "cd /somewhere\n"
            "run | grep -iE 'kill switch' | head -3\n",
            encoding="utf-8",
        )
        finding = check_script_fails_closed(script)
        assert finding is not None and finding.severity == BLOCKER
        assert "set -e" in finding.message

    def test_catches_an_unchecked_grep_when_there_is_no_pipefail_net(self, tmp_path):
        script = tmp_path / "verify.sh"
        script.write_text(
            "#!/usr/bin/env bash\nset -e\nrun | grep something\n",
            encoding="utf-8",
        )
        finding = check_script_fails_closed(script)
        assert finding is not None
        assert "unchecked grep" in finding.message

    def test_pipefail_makes_a_bare_grep_pipeline_fail_closed(self, tmp_path):
        """Not a false positive: under `set -Eeuo pipefail` an unmatched
        grep or a failed producer already aborts the script. Verified
        directly -- `echo hello | grep nomatch` exits 1 and the next line
        never runs. An earlier version of this check flagged it, and a
        checklist that cries wolf is one people stop running."""
        script = tmp_path / "verify.sh"
        script.write_text(
            "#!/usr/bin/env bash\nset -Eeuo pipefail\nrun | grep something\n",
            encoding="utf-8",
        )
        assert check_script_fails_closed(script) is None

    def test_set_plus_e_that_is_never_restored_is_caught(self, tmp_path):
        """The check's own defect, found on review. Searching the whole
        file for `set -e` reports this script fail-closed while `false`
        silently continues -- verified directly: under
        `set -Eeuo pipefail; set +e; false; echo` the echo runs."""
        script = tmp_path / "verify.sh"
        script.write_text(
            "#!/usr/bin/env bash\nset -Eeuo pipefail\nset +e\nfalse\necho survived\n",
            encoding="utf-8",
        )
        finding = check_script_fails_closed(script)
        assert finding is not None and finding.severity == BLOCKER
        assert "errexit is not in force at the end" in finding.message

    def test_set_plus_o_pipefail_removes_the_net_for_a_bare_grep(self, tmp_path):
        """The same hole through the other option: `+o pipefail` matched
        the old `.*pipefail` regex and so counted as *enabling* it."""
        script = tmp_path / "verify.sh"
        script.write_text(
            "#!/usr/bin/env bash\nset -Eeuo pipefail\nset +o pipefail\nrun | grep x\n",
            encoding="utf-8",
        )
        finding = check_script_fails_closed(script)
        assert finding is not None
        assert "unchecked grep" in finding.message

    def test_disabling_errexit_to_capture_an_exit_code_is_not_flagged(self, tmp_path):
        """Not a false positive. `scripts/paper-trading-daily-signal.sh`
        does exactly this on purpose -- drop errexit, run the command,
        read `$?`, put errexit back. Flagging a real and correct pattern
        is how a checklist stops being run."""
        script = tmp_path / "verify.sh"
        script.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "set +e\n"
            "run_the_thing\n"
            "RC=$?\n"
            "set -e\n"
            'grep -q ok "$log" || exit 1\n',
            encoding="utf-8",
        )
        assert check_script_fails_closed(script) is None

    def test_the_repositorys_own_verification_script_passes(self):
        """The check is pointed at the real script it was written for.
        A checker nobody runs against real input drifts."""
        repo_root = Path(__file__).resolve().parents[2]
        script = repo_root / "scripts" / "verify-gate-a-kill-switch.sh"
        if not script.exists():  # pragma: no cover - guard against a move
            pytest.skip(f"{script} not present")
        assert check_script_fails_closed(script) is None

    def test_passes_the_corrected_shape(self, tmp_path):
        script = tmp_path / "verify.sh"
        script.write_text(
            "#!/usr/bin/env bash\n"
            "set -Eeuo pipefail\n"
            'grep -qiE "clean start" "$log" || fail "no clean start"\n',
            encoding="utf-8",
        )
        assert check_script_fails_closed(script) is None

    def test_an_unreadable_script_is_a_blocker_not_a_pass(self, tmp_path):
        finding = check_script_fails_closed(tmp_path / "absent.sh")
        assert finding is not None and finding.severity == BLOCKER


class TestNoSharedMutableState:
    def test_catches_the_real_case(self):
        """Both paper loops read the same signal file, so a mock feed
        would have driven the venue-connected one too."""
        finding = check_no_shared_mutable_state(
            resource="var/live/signals/BTC-USDT/daily-tsmom-ensemble/latest.json",
            writers=["simulated loop", "bingx-vst loop"],
        )
        assert finding is not None and finding.severity == BLOCKER
        assert "venue" in finding.message

    def test_catches_the_classpath_cache_race(self):
        finding = check_no_shared_mutable_state(
            resource="var/live/runtime-classpath.txt.tmp",
            writers=["vps-bootstrap.sh", "paper-trading-watchdog.sh"],
        )
        assert finding is not None

    def test_passes_once_serialised(self):
        assert check_no_shared_mutable_state(
            resource="var/live/runtime-classpath.txt",
            writers=["vps-bootstrap.sh", "paper-trading-watchdog.sh"],
            serialised_by="flock on runtime-classpath.txt.lock",
        ) is None

    def test_a_single_writer_is_fine(self):
        assert check_no_shared_mutable_state(
            resource="var/live/signals/_mock/latest.json",
            writers=["generate_mock_signal"],
        ) is None


class TestReportedFromActual:
    def test_catches_the_real_case(self):
        """Task C published +45 from the signal book; from real fills it
        was -97, a gap of 3x the effect that reversed its sign."""
        finding = check_reported_from_actual(
            figure="tactical gross edge",
            source="signal-time Book",
            is_execution_record=False,
        )
        assert finding is not None and finding.severity == BLOCKER
        assert "intent" in finding.message

    def test_passes_for_an_execution_book(self):
        assert check_reported_from_actual(
            figure="tactical gross edge",
            source="replay_fills execution Book",
            is_execution_record=True,
        ) is None


class TestErrorDirectionDeclared:
    def test_warns_when_undeclared(self):
        finding = check_error_direction_declared(
            defect="tests appending to runs/experiments.jsonl", direction=None
        )
        assert finding is not None and finding.severity == WARNING

    @pytest.mark.parametrize("direction", ["safe", "unsafe"])
    def test_passes_when_declared_either_way(self, direction):
        assert check_error_direction_declared(
            defect="whatever", direction=direction
        ) is None

    def test_a_wrong_word_is_not_a_declaration(self):
        assert check_error_direction_declared(
            defect="whatever", direction="probably fine"
        ) is not None


class TestJavaChangeDeployed:
    def test_catches_the_real_case(self):
        """2026-09-08: checkout current, classes rebuilt that morning,
        both loops still running code from two days earlier."""
        finding = check_java_change_deployed(touched_java=True)
        assert finding is not None and finding.severity == BLOCKER
        assert "Merging is not deploying" in finding.message

    def test_passes_once_the_restart_was_verified(self):
        assert check_java_change_deployed(
            touched_java=True,
            loops_restarted_and_verified=True,
            how_verified="vps-deploy.sh; both sessions newer than the build",
        ) is None

    def test_a_change_that_touches_no_java_is_not_flagged(self):
        """Python and shell are live on the next cron tick, so demanding
        a restart for them would be a false positive — and a checklist
        that cries wolf is one people stop running."""
        assert check_java_change_deployed(touched_java=False) is None


class TestHarness:
    def test_require_no_blockers_raises_rather_than_warning(self):
        """Same reasoning as conclusion_check: a warning printed above a
        conclusion gets read as decoration."""
        from research.conclusion_check import ConclusionCheckError

        with pytest.raises(ConclusionCheckError):
            require_no_blockers([
                check_guard_is_an_allowlist(guard="g", rejects_by_default=False)
            ])

    def test_warnings_survive_and_are_returned(self):
        warnings = require_no_blockers([
            check_error_direction_declared(defect="d", direction=None)
        ])
        assert len(warnings) == 1 and warnings[0].severity == WARNING

    def test_a_clean_run_says_so(self):
        assert format_findings([None, None]) == "all conclusion checks passed"

    def test_every_check_carries_a_scar(self):
        """The design constraint: a checklist item without a real
        incident behind it becomes theatre nobody runs."""
        import research.change_check as cc

        checks = [getattr(cc, n) for n in cc.__all__ if n.startswith("check_")]
        assert len(checks) == 8
        for fn in checks:
            doc = fn.__doc__ or ""
            assert "Scar" in doc or "scar" in doc, (
                f"{fn.__name__} has no incident attached -- add one or remove "
                f"the check"
            )
